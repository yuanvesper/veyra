"""Structural validation for ``plan.json``.

Generation and execution belong to Phase 3.2.2.  Phase 3.2.1 still needs to
read and validate a persisted Plan, so the schema and the independent check live
here early; this module never produces or executes a Plan.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from veyra_pack.contract import Issue, error
from veyra_pack.paths import check_relative_path, is_creation_path, path_key

from .constants import PLAN_STATES
from .errors import CreationError
from .spec import SPEC_ID_RE, _is_int, check_schema_version

PLAN_ACTIONS = (
    "write_text",
    "generate_text",
    "generate_image",
    "delete",
    "copy",
    "check",
    "export",
)

_PLAN_ID_RE = SPEC_ID_RE

MAX_PLAN_STEPS = 200
MAX_DEPENDS_PER_STEP = 50
MAX_DEPENDENCY_DEPTH = 100
GENERATOR_RE = re.compile(r"^[a-z][a-z0-9:_-]{0,63}$")
STEP_FIELDS = frozenset(
    {
        "id",
        "action",
        "target",
        "generator",
        "depends_on",
        "budget",
        "acceptance",
        "prompt",
        "prompt_ref",
    }
)


def validate_plan(plan: object, spec: dict[str, Any] | None = None) -> list[Issue]:
    """Return every structural problem found in ``plan``.

    This is the independent validator every Plan must pass before it may be
    persisted or executed.  It intentionally has no model or filesystem access.
    """

    issues: list[Issue] = []
    if not isinstance(plan, dict):
        return [error("E_PLAN_TYPE", "plan", "plan must be a JSON object")]

    issues.extend(check_schema_version(plan.get("schema_version"), "plan.schema_version"))

    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not _PLAN_ID_RE.match(plan_id):
        issues.append(
            error(
                "E_PLAN_ID",
                "plan.plan_id",
                "plan_id must match ^[a-z0-9][a-z0-9._-]{0,63}$",
            )
        )

    spec_revision = plan.get("spec_revision")
    if not _is_int(spec_revision) or spec_revision < 1:
        issues.append(
            error("E_PLAN_SPEC_REVISION", "plan.spec_revision", "spec_revision must be an integer >= 1")
        )
    elif spec is not None and _is_int(spec.get("revision")) and spec_revision != spec.get("revision"):
        issues.append(
            error(
                "E_PLAN_SPEC_REVISION",
                "plan.spec_revision",
                f"plan targets spec revision {spec_revision}, current is {spec.get('revision')}",
            )
        )

    state = plan.get("state")
    if state not in PLAN_STATES:
        issues.append(
            error(
                "E_PLAN_STATE",
                "plan.state",
                f"state must be one of {', '.join(PLAN_STATES)}",
            )
        )

    steps = plan.get("steps")
    if not isinstance(steps, list):
        issues.append(error("E_PLAN_STEPS", "plan.steps", "steps must be an array"))
        return issues
    if not steps:
        issues.append(error("E_PLAN_EMPTY", "plan.steps", "plan must contain at least one step"))
    if len(steps) > MAX_PLAN_STEPS:
        issues.append(
            error(
                "E_PLAN_TOO_LARGE",
                "plan.steps",
                f"plan has {len(steps)} steps; the limit is {MAX_PLAN_STEPS}",
            )
        )
        return issues

    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    graph: dict[str, list[str]] = {}
    for index, step in enumerate(steps):
        location = f"plan.steps[{index}]"
        if not isinstance(step, dict):
            issues.append(error("E_STEP_TYPE", location, "step must be an object"))
            continue

        step_id = step.get("id")
        if not isinstance(step_id, str) or not _PLAN_ID_RE.match(step_id):
            issues.append(
                error(
                    "E_STEP_ID",
                    f"{location}.id",
                    "id must match ^[a-z0-9][a-z0-9._-]{0,63}$",
                )
            )
            continue
        if step_id in seen_ids:
            issues.append(error("E_DUPLICATE_STEP", f"{location}.id", f"duplicate step id: {step_id}"))
            continue
        seen_ids.add(step_id)
        graph[step_id] = []

        unknown_fields = sorted(set(step) - STEP_FIELDS)
        for field in unknown_fields:
            issues.append(
                error(
                    "E_STEP_FIELD",
                    f"{location}.{field}",
                    f"unknown step field: {field}",
                )
            )

        action = step.get("action")
        if action not in PLAN_ACTIONS:
            issues.append(
                error(
                    "E_STEP_ACTION",
                    f"{location}.action",
                    f"action must be one of {', '.join(PLAN_ACTIONS)}",
                )
            )

        generator = step.get("generator")
        if action in ("write_text", "generate_text", "generate_image"):
            if not isinstance(generator, str) or not GENERATOR_RE.match(generator):
                issues.append(
                    error(
                        "E_STEP_GENERATOR",
                        f"{location}.generator",
                        "generator must be a non-empty identifier for this action",
                    )
                )
        elif generator is not None and (not isinstance(generator, str) or not generator):
            issues.append(
                error(
                    "E_STEP_GENERATOR",
                    f"{location}.generator",
                    "generator must be a non-empty string when present",
                )
            )

        target = step.get("target")
        needs_target = action in ("write_text", "generate_text", "generate_image", "delete", "copy")
        if needs_target:
            target_issue = check_relative_path(target)
            if target_issue is not None:
                issues.append(replace(target_issue, location=f"{location}.target"))
            else:
                target_key = path_key(target)
                if is_creation_path(target):
                    issues.append(
                        error(
                            "E_STEP_TARGET",
                            f"{location}.target",
                            "steps must not target the internal creation/ directory",
                        )
                    )
                elif target_key in seen_targets:
                    issues.append(
                        error("E_DUPLICATE_TARGET", f"{location}.target", f"duplicate target: {target}")
                    )
                else:
                    seen_targets.add(target_key)
        elif target is not None:
            issues.append(
                error(
                    "E_STEP_TARGET",
                    f"{location}.target",
                    f"action {action!r} must not declare a target",
                )
            )

        depends_on = step.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
            issues.append(
                error(
                    "E_STEP_DEPENDS",
                    f"{location}.depends_on",
                    "depends_on must be an array of step ids",
                )
            )
        else:
            if len(depends_on) > MAX_DEPENDS_PER_STEP:
                issues.append(
                    error(
                        "E_STEP_DEPENDS",
                        f"{location}.depends_on",
                        f"at most {MAX_DEPENDS_PER_STEP} dependencies are allowed",
                    )
                )
            graph[step_id] = list(depends_on)
            for dependency in depends_on:
                if dependency == step_id:
                    issues.append(
                        error("E_STEP_CYCLE", f"{location}.depends_on", "a step cannot depend on itself")
                    )
                elif dependency not in seen_ids:
                    # A forward reference is checked again after all ids exist.
                    pass

        step_budget = step.get("budget")
        if action in ("write_text", "generate_text", "generate_image") and step_budget is None:
            issues.append(
                error(
                    "E_STEP_BUDGET",
                    f"{location}.budget",
                    "write and generate steps require an explicit budget object",
                )
            )
        elif step_budget is not None:
            issues.extend(_check_budget(step_budget, f"{location}.budget"))

        acceptance = step.get("acceptance")
        if acceptance is not None:
            if not isinstance(acceptance, list) or not all(
                isinstance(item, str) and item for item in acceptance
            ):
                issues.append(
                    error(
                        "E_STEP_ACCEPTANCE",
                        f"{location}.acceptance",
                        "acceptance must be an array of non-empty strings",
                    )
                )

    # All depends_on targets must exist (forward references are allowed).
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        step_id = step.get("id")
        depends_on = step.get("depends_on")
        if not isinstance(step_id, str) or not isinstance(depends_on, list):
            continue
        for dependency in depends_on:
            if isinstance(dependency, str) and dependency != step_id and dependency not in seen_ids:
                issues.append(
                    error(
                        "E_STEP_DEPENDS",
                        f"plan.steps[{index}].depends_on",
                        f"unknown dependency: {dependency}",
                    )
                )

    cycle = _find_cycle(graph)
    if cycle:
        issues.append(
            error(
                "E_STEP_CYCLE",
                "plan.steps",
                "dependency cycle: " + " -> ".join(cycle),
            )
        )

    budget = plan.get("budget")
    if budget is not None:
        issues.extend(_check_budget(budget, "plan.budget"))

    depth = _dependency_depth(graph)
    if depth > MAX_DEPENDENCY_DEPTH:
        issues.append(
            error(
                "E_STEP_DEPTH",
                "plan.steps",
                f"dependency depth {depth} exceeds the limit {MAX_DEPENDENCY_DEPTH}",
            )
        )

    return issues


BUDGET_FIELDS = frozenset(
    {"max_usd", "max_images", "max_input_tokens", "max_output_tokens"}
)


def _check_budget(budget: object, location: str) -> list[Issue]:
    if not isinstance(budget, dict):
        return [error("E_PLAN_BUDGET", location, "budget must be an object")]
    issues: list[Issue] = []
    for key in sorted(set(budget) - BUDGET_FIELDS):
        issues.append(error("E_PLAN_BUDGET", f"{location}.{key}", f"unknown budget field: {key}"))
    max_usd = budget.get("max_usd")
    if max_usd is not None and (
        not _is_int(max_usd) and not isinstance(max_usd, float)
        or isinstance(max_usd, bool)
        or not math.isfinite(float(max_usd))
        or float(max_usd) < 0
    ):
        issues.append(
            error("E_PLAN_BUDGET", f"{location}.max_usd", "max_usd must be a finite number >= 0")
        )
    for key in ("max_images", "max_input_tokens", "max_output_tokens"):
        value = budget.get(key)
        if value is not None and (not _is_int(value) or value < 0):
            issues.append(
                error("E_PLAN_BUDGET", f"{location}.{key}", f"{key} must be an integer >= 0")
            )
    return issues


def _dependency_depth(graph: dict[str, list[str]]) -> int:
    """Return the longest dependency chain, or 0 when a cycle exists."""

    indegree = {node: 0 for node in graph}
    for deps in graph.values():
        for dep in deps:
            if dep in indegree:
                indegree[dep] += 1
    ready = sorted(node for node, count in indegree.items() if count == 0)
    layers: dict[str, int] = {}
    processed = 0
    while ready:
        node = ready.pop(0)
        processed += 1
        depth = layers.get(node, 1)
        for dependency in graph.get(node, []):
            if dependency not in indegree:
                continue
            layers[dependency] = max(layers.get(dependency, 1), depth + 1)
            indegree[dependency] -= 1
            if indegree[dependency] == 0:
                ready.append(dependency)
                ready.sort()
    if processed != len(graph):
        return 0
    return max(layers.values(), default=0)


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle path, or None.  Iteration order is deterministic."""

    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in on_stack:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return None
        visited.add(node)
        stack.append(node)
        on_stack.add(node)
        for dependency in sorted(graph.get(node, [])):
            if dependency not in graph:
                continue
            found = visit(dependency)
            if found:
                return found
        stack.pop()
        on_stack.discard(node)
        return None

    for node in sorted(graph):
        found = visit(node)
        if found:
            return found
    return None


def load_plan(path: Path, *, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        raise CreationError("E_PLAN_MISSING", f"plan file does not exist: {path}", "plan")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise CreationError("E_PLAN_ENCODING", f"plan is not valid UTF-8: {exc}", "plan") from exc
    except json.JSONDecodeError as exc:
        raise CreationError(
            "E_PLAN_CORRUPT",
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            "plan",
        ) from exc
    except OSError as exc:
        raise CreationError("E_PLAN_UNREADABLE", f"cannot read plan: {exc}", "plan") from exc
    issues = validate_plan(data, spec=spec)
    if issues:
        raise CreationError(
            "E_PLAN_INVALID",
            issues[0].format(),
            "plan",
            details=[issue.as_dict() for issue in issues],
        )
    return data
