"""Spec schema, validation and loading for creation projects.

The Spec is the user's structured intent.  It is deliberately independent from
``pack.json``: a Pack is an output artifact, while the Spec also records
creative choices, acceptance criteria, unknowns and budget constraints.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from veyra_pack.contract import (
    ASSET_KINDS,
    PACK_ID_RE,
    PACK_TYPES,
    VERSION_RE,
    Issue,
    error,
)
from veyra_pack.paths import check_relative_path, is_creation_path, path_key

from .canonical import now_iso
from .constants import (
    CREATION_SCHEMA_VERSION,
    SPEC_STATES,
    SUPPORTED_SCHEMA_MAJOR,
)
from .errors import CreationError

SPEC_ID_RE = PACK_ID_RE
PROFILE_ID_RE = PACK_ID_RE


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_schema_version(value: object, location: str = "spec") -> list[Issue]:
    """Validate a creation-contract version string."""

    if not isinstance(value, str) or not value:
        return [error("E_SCHEMA_TYPE", location, "schema_version must be a string")]
    parts = value.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        return [error("E_SCHEMA_TYPE", location, f"invalid schema version: {value!r}")]
    if int(parts[0]) != SUPPORTED_SCHEMA_MAJOR:
        return [
            error(
                "E_SCHEMA_UNSUPPORTED",
                location,
                f"schema major {parts[0]} is not supported (need {SUPPORTED_SCHEMA_MAJOR}.x)",
            )
        ]
    return []


def validate_spec(spec: object) -> list[Issue]:
    """Return every structural problem found in ``spec``.

    Unknown fields are intentionally allowed and preserved: the creation
    contract follows the same additive-minor policy as the Pack contract.
    """

    issues: list[Issue] = []
    if not isinstance(spec, dict):
        return [error("E_SPEC_TYPE", "spec", "spec must be a JSON object")]

    issues.extend(check_schema_version(spec.get("schema_version"), "spec.schema_version"))

    spec_id = spec.get("spec_id")
    if not isinstance(spec_id, str) or not SPEC_ID_RE.match(spec_id):
        issues.append(
            error(
                "E_SPEC_ID",
                "spec.spec_id",
                "spec_id must match ^[a-z0-9][a-z0-9._-]{0,63}$",
            )
        )

    revision = spec.get("revision")
    if not _is_int(revision) or revision < 1:
        issues.append(error("E_SPEC_REVISION", "spec.revision", "revision must be an integer >= 1"))

    state = spec.get("state")
    if state not in SPEC_STATES:
        issues.append(
            error(
                "E_SPEC_STATE",
                "spec.state",
                f"state must be one of {', '.join(SPEC_STATES)}",
            )
        )

    project = spec.get("project")
    if not isinstance(project, dict):
        issues.append(error("E_SPEC_PROJECT", "spec.project", "project must be an object"))
    else:
        issues.extend(_validate_project(project))

    deliverables = spec.get("deliverables")
    if not isinstance(deliverables, list):
        issues.append(
            error("E_SPEC_DELIVERABLES", "spec.deliverables", "deliverables must be an array")
        )
    else:
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for index, item in enumerate(deliverables):
            location = f"spec.deliverables[{index}]"
            if not isinstance(item, dict):
                issues.append(error("E_DELIVERABLE_TYPE", location, "deliverable must be an object"))
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not SPEC_ID_RE.match(item_id):
                issues.append(
                    error(
                        "E_DELIVERABLE_ID",
                        f"{location}.id",
                        "id must match ^[a-z0-9][a-z0-9._-]{0,63}$",
                    )
                )
            elif item_id in seen_ids:
                issues.append(error("E_DUPLICATE_ID", f"{location}.id", f"duplicate id: {item_id}"))
            else:
                seen_ids.add(item_id)

            path_issue = check_relative_path(item.get("path"))
            if path_issue is not None:
                issues.append(replace(path_issue, location=f"{location}.path"))
            else:
                path = item.get("path")
                key = path_key(path)
                if is_creation_path(path):
                    issues.append(
                        error(
                            "E_DELIVERABLE_PATH",
                            f"{location}.path",
                            "deliverables must not target the internal creation/ directory",
                        )
                    )
                elif key in seen_paths:
                    issues.append(
                        error("E_DUPLICATE_PATH", f"{location}.path", f"duplicate path: {path}")
                    )
                else:
                    seen_paths.add(key)

            kind = item.get("kind")
            if kind not in ASSET_KINDS:
                issues.append(
                    error(
                        "E_DELIVERABLE_KIND",
                        f"{location}.kind",
                        f"kind must be one of {', '.join(ASSET_KINDS)}",
                    )
                )
            role = item.get("role")
            if role is not None and (not isinstance(role, str) or not role):
                issues.append(error("E_DELIVERABLE_ROLE", f"{location}.role", "role must be a non-empty string"))
            optional = item.get("optional")
            if optional is not None and not isinstance(optional, bool):
                issues.append(error("E_DELIVERABLE_OPTIONAL", f"{location}.optional", "optional must be a boolean"))

    constraints = spec.get("constraints")
    if constraints is not None:
        if not isinstance(constraints, dict):
            issues.append(error("E_SPEC_CONSTRAINTS", "spec.constraints", "constraints must be an object"))
        else:
            no_game_assets = constraints.get("no_game_assets")
            if no_game_assets is not None and not isinstance(no_game_assets, bool):
                issues.append(
                    error(
                        "E_SPEC_CONSTRAINT_TYPE",
                        "spec.constraints.no_game_assets",
                        "no_game_assets must be a boolean",
                    )
                )
            budget = constraints.get("budget")
            if budget is not None:
                if not isinstance(budget, dict):
                    issues.append(error("E_SPEC_BUDGET", "spec.constraints.budget", "budget must be an object"))
                else:
                    max_usd = budget.get("max_usd")
                    if max_usd is not None and (
                        not _is_number(max_usd) or not math.isfinite(float(max_usd)) or max_usd < 0
                    ):
                        issues.append(
                            error(
                                "E_SPEC_BUDGET",
                                "spec.constraints.budget.max_usd",
                                "max_usd must be a finite number >= 0",
                            )
                        )
                    max_images = budget.get("max_images")
                    if max_images is not None and (not _is_int(max_images) or max_images < 0):
                        issues.append(
                            error(
                                "E_SPEC_BUDGET",
                                "spec.constraints.budget.max_images",
                                "max_images must be an integer >= 0",
                            )
                        )
                    for key in ("max_input_tokens", "max_output_tokens"):
                        value = budget.get(key)
                        if value is not None and (not _is_int(value) or value < 0):
                            issues.append(
                                error(
                                    "E_SPEC_BUDGET",
                                    f"spec.constraints.budget.{key}",
                                    f"{key} must be an integer >= 0",
                                )
                            )

    for key in ("unknowns", "acceptance", "assumptions"):
        value = spec.get(key)
        if value is not None and not isinstance(value, list):
            issues.append(error("E_SPEC_FIELD_TYPE", f"spec.{key}", f"{key} must be an array"))

    return issues


def _validate_project(project: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    name = project.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append(error("E_PROJECT_NAME", "spec.project.name", "name must be a non-empty string"))

    pack_id = project.get("pack_id")
    if not isinstance(pack_id, str) or not PACK_ID_RE.match(pack_id):
        issues.append(
            error(
                "E_PROJECT_PACK_ID",
                "spec.project.pack_id",
                "pack_id must match ^[a-z0-9][a-z0-9._-]{0,63}$",
            )
        )

    pack_type = project.get("pack_type")
    if pack_type not in PACK_TYPES:
        issues.append(
            error(
                "E_PROJECT_PACK_TYPE",
                "spec.project.pack_type",
                f"pack_type must be one of {', '.join(PACK_TYPES)}",
            )
        )

    version = project.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        issues.append(
            error(
                "E_PROJECT_VERSION",
                "spec.project.version",
                "version must be a semantic version such as 0.1.0",
            )
        )

    target_game = project.get("target_game")
    if target_game is not None and (not isinstance(target_game, str) or not target_game.strip()):
        issues.append(
            error(
                "E_PROJECT_TARGET_GAME",
                "spec.project.target_game",
                "target_game must be a non-empty string or null",
            )
        )

    for key in ("profile_id", "adapter_id"):
        value = project.get(key)
        if value is not None and (not isinstance(value, str) or not PROFILE_ID_RE.match(value)):
            issues.append(
                error(
                    "E_PROJECT_PROFILE",
                    f"spec.project.{key}",
                    f"{key} must match ^[a-z0-9][a-z0-9._-]{{0,63}}$ or be null",
                )
            )
    return issues


def make_initial_spec(
    *,
    spec_id: str,
    project: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Build a valid draft Spec; deliverables are filled in later."""

    return {
        "schema_version": CREATION_SCHEMA_VERSION,
        "spec_id": spec_id,
        "revision": 1,
        "state": "draft",
        "created_at": created_at,
        "updated_at": created_at,
        "project": project,
        "deliverables": [],
        "constraints": {"no_game_assets": True, "budget": {"max_usd": 0.0, "max_images": 0}},
        "unknowns": [],
        "acceptance": [],
        "assumptions": [],
    }


def load_spec(path: Path, *, validate: bool = True) -> dict[str, Any]:
    """Load one Spec file, raising :class:`CreationError` on any problem."""

    if not path.exists():
        raise CreationError("E_SPEC_MISSING", f"spec file does not exist: {path}", "spec")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CreationError("E_SPEC_ENCODING", f"spec is not valid UTF-8: {exc}", "spec") from exc
    except OSError as exc:
        raise CreationError("E_SPEC_UNREADABLE", f"cannot read spec: {exc}", "spec") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CreationError(
            "E_SPEC_CORRUPT",
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            "spec",
        ) from exc
    if validate:
        issues = validate_spec(data)
        if issues:
            raise CreationError(
                "E_SPEC_INVALID",
                issues[0].format(),
                "spec",
                details=[issue.as_dict() for issue in issues],
            )
    if not isinstance(data, dict):
        raise CreationError("E_SPEC_TYPE", "spec must be a JSON object", "spec")
    return data


def spec_created_at(spec: dict[str, Any], fallback: str | None = None) -> str:
    value = spec.get("created_at")
    return value if isinstance(value, str) else (fallback or now_iso())
