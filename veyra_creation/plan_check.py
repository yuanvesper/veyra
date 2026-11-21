"""Independent semantic check for ``plan.json``.

``plan.py`` validates the JSON shape.  This module adds the checks that need the
Spec: every target must be one of the confirmed deliverables, acceptance
references must exist, and step/plan budgets must stay inside the Spec budget.

Both ``ProjectStore.save_plan`` and ``ProjectStore.approve_plan`` call
:func:`check_plan`, so no model-produced Plan can enter ``plan.json`` or become
approved without passing the same deterministic gate.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from veyra_pack.contract import Issue, error
from veyra_pack.paths import path_key

from .errors import CreationError
from .plan import PLAN_ACTIONS, load_plan, validate_plan


def _budget_numbers(budget: object) -> dict[str, float]:
    if not isinstance(budget, dict):
        return {}
    values: dict[str, float] = {}
    for key in ("max_usd", "max_images", "max_input_tokens", "max_output_tokens"):
        value = budget.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values[key] = float(value)
    return values


def check_plan(plan: object, spec: dict[str, Any]) -> list[Issue]:
    """Return every structural and Spec-consistency problem in ``plan``."""

    issues = validate_plan(plan, spec=spec)
    if not isinstance(plan, dict) or not isinstance(spec, dict):
        return issues

    deliverables: dict[str, str] = {}
    for item in spec.get("deliverables", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str):
            deliverables[path_key(path)] = path

    acceptance_ids = {
        item.get("id")
        for item in spec.get("acceptance", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    constraints = spec.get("constraints")
    spec_budget = _budget_numbers(
        constraints.get("budget") if isinstance(constraints, dict) else None
    )
    steps = plan.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            action = step.get("action")
            target = step.get("target")
            if action in ("write_text", "generate_text", "generate_image", "delete", "copy"):
                if isinstance(target, str) and path_key(target) not in deliverables:
                    issues.append(
                        error(
                            "E_STEP_TARGET_NOT_DELIVERABLE",
                            f"plan.steps[{index}].target",
                            f"target is not a confirmed Spec deliverable: {target}",
                        )
                    )
            acceptance = step.get("acceptance")
            if isinstance(acceptance, list):
                for ref in acceptance:
                    if isinstance(ref, str) and ref not in acceptance_ids:
                        issues.append(
                            error(
                                "E_STEP_ACCEPTANCE_REF",
                                f"plan.steps[{index}].acceptance",
                                f"acceptance check is not declared by the Spec: {ref}",
                            )
                        )
            step_budget = _budget_numbers(step.get("budget"))
            for key, value in step_budget.items():
                if key in spec_budget and value > spec_budget[key]:
                    issues.append(
                        error(
                            "E_STEP_BUDGET_EXCEEDS_SPEC",
                            f"plan.steps[{index}].budget.{key}",
                            f"step {key} exceeds the Spec limit {spec_budget[key]:g}",
                        )
                    )

    plan_budget = _budget_numbers(plan.get("budget"))
    for key, value in plan_budget.items():
        if key in spec_budget and value > spec_budget[key]:
            issues.append(
                error(
                    "E_PLAN_BUDGET_EXCEEDS_SPEC",
                    f"plan.budget.{key}",
                    f"plan {key} exceeds the Spec limit {spec_budget[key]:g}",
                )
            )

    return issues


def load_checked_plan(path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Read ``plan.json`` and require it to pass :func:`check_plan`."""

    plan = load_plan(path)
    issues = check_plan(plan, spec)
    if issues:
        raise CreationError(
            "E_PLAN_INVALID",
            "plan check failed: "
            + "; ".join(f"{issue.location}: {issue.message}" for issue in issues),
            "plan",
        )
    return plan


__all__ = ["PLAN_ACTIONS", "check_plan", "load_checked_plan"]
