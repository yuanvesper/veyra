"""Reconstruct Project state from the append-only decision log.

``state.json``, ``spec.json`` and ``plan.json`` are projections.  The decision
log is the authoritative history: replaying it must yield exactly the state the
store currently claims, otherwise an approval can be deleted or a confirmed
document edited without detection.

The replay deliberately never reads ``state.json``; it only uses the event log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from veyra_pack.contract import Issue, error
from veyra_pack.paths import check_relative_path, path_key

from .errors import CreationError

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LIFECYCLE_ACTIONS = {
    "project_created",
    "spec_revised",
    "confirm_spec",
    "plan_proposed",
    "approve_plan",
}


@dataclass
class ReplayResult:
    project_id: str | None = None
    pack_id: str | None = None
    spec_id: str | None = None
    spec_revision: int | None = None
    spec_state: str | None = None
    confirmed_spec_revision: int | None = None
    spec_sha256: str | None = None
    plan_id: str | None = None
    plan_spec_revision: int | None = None
    plan_content_sha256: str | None = None
    plan_state: str | None = None
    approved_plan_id: str | None = None
    locks: dict[str, str] = field(default_factory=dict)
    last_event_id: str | None = None
    event_count: int = 0
    issues: list[Issue] = field(default_factory=list)

    def expected_state(self) -> dict[str, Any]:
        active = None
        if self.plan_id is not None and self.plan_state != "superseded":
            active = self.plan_id
        return {
            "project_id": self.project_id,
            "pack_id": self.pack_id,
            "spec_revision": self.spec_revision,
            "spec_state": self.spec_state,
            "confirmed_spec_revision": self.confirmed_spec_revision,
            "active_plan_id": active,
            "approved_plan_id": self.approved_plan_id,
            "last_event_id": self.last_event_id,
            "history_format": 2,
        }


def _require_string(event: dict[str, Any], field_name: str, issues: list[Issue]) -> str | None:
    value = event.get(field_name)
    if not isinstance(value, str) or not value:
        issues.append(
            error(
                "E_DECISIONS_CORRUPT",
                f"decisions.{event.get('event_id')}",
                f"event is missing a string field: {field_name}",
            )
        )
        return None
    return value


def _require_sha(event: dict[str, Any], issues: list[Issue]) -> str | None:
    value = event.get("sha256")
    if not isinstance(value, str) or not SHA256_RE.match(value):
        issues.append(
            error(
                "E_DECISIONS_CORRUPT",
                f"decisions.{event.get('event_id')}",
                "event is missing a valid sha256",
            )
        )
        return None
    return value


def _require_revision(
    event: dict[str, Any], issues: list[Issue], field_name: str = "revision"
) -> int | None:
    value = event.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        issues.append(
            error(
                "E_DECISIONS_CORRUPT",
                f"decisions.{event.get('event_id')}",
                f"event is missing a positive integer {field_name}",
            )
        )
        return None
    return value


def replay_history(store: Any) -> ReplayResult:
    """Replay ``decisions.ndjson`` into the expected project state."""

    result = ReplayResult()
    try:
        events = store.load_decisions()
    except CreationError as exc:
        result.issues.append(error(exc.code, exc.location, exc.message))
        return result

    result.event_count = len(events)
    legacy_seen = False
    created = False
    for index, event in enumerate(events, start=1):
        if "event_format" not in event:
            legacy_seen = True
        action = event.get("action")
        event_id = event.get("event_id")
        if index == 1 and action != "project_created":
            result.issues.append(
                error(
                    "E_DECISIONS_CORRUPT",
                    "decisions[0]",
                    "the first event must be project_created",
                )
            )
        if action == "project_created":
            if created:
                result.issues.append(
                    error("E_DECISIONS_CORRUPT", f"decisions[{index}]", "duplicate project_created")
                )
                continue
            created = True
            revision = _require_revision(event, result.issues)
            sha = _require_sha(event, result.issues)
            result.project_id = _require_string(event, "project_id", result.issues)
            result.pack_id = _require_string(event, "pack_id", result.issues)
            result.spec_id = _require_string(event, "spec_id", result.issues)
            if revision != 1:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "project_created must describe Spec revision 1",
                    )
                )
            result.spec_revision = revision
            result.spec_state = "draft"
            result.spec_sha256 = sha
        elif action == "spec_revised":
            if not created:
                result.issues.append(
                    error("E_DECISIONS_CORRUPT", f"decisions[{index}]", "event before project_created")
                )
                continue
            revision = _require_revision(event, result.issues)
            sha = _require_sha(event, result.issues)
            spec_id = _require_string(event, "spec_id", result.issues)
            if spec_id is not None and result.spec_id is not None and spec_id != result.spec_id:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "spec_id changed between revisions",
                    )
                )
            if revision is not None and result.spec_revision is not None:
                if revision != result.spec_revision + 1:
                    result.issues.append(
                        error(
                            "E_DECISIONS_CORRUPT",
                            f"decisions[{index}]",
                            "Spec revisions must increase by exactly one",
                        )
                    )
            result.spec_revision = revision
            result.spec_state = "draft"
            result.confirmed_spec_revision = None
            result.spec_sha256 = sha
            if result.plan_id is not None and result.plan_state != "superseded":
                result.plan_state = "superseded"
            result.approved_plan_id = None
        elif action == "confirm_spec":
            if not created:
                result.issues.append(
                    error("E_DECISIONS_CORRUPT", f"decisions[{index}]", "event before project_created")
                )
                continue
            revision = _require_revision(event, result.issues)
            sha = _require_sha(event, result.issues)
            invalid = False
            if result.spec_state != "draft" or result.confirmed_spec_revision is not None:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "confirm_spec is only valid for a draft Spec with no prior confirmation",
                    )
                )
                invalid = True
            if revision != result.spec_revision:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "confirm_spec does not refer to the current Spec revision",
                    )
                )
                invalid = True
            if sha is None:
                invalid = True
            if invalid:
                continue
            result.spec_state = "specified"
            result.confirmed_spec_revision = revision
            result.spec_sha256 = sha
        elif action == "plan_proposed":
            if not created:
                result.issues.append(
                    error("E_DECISIONS_CORRUPT", f"decisions[{index}]", "event before project_created")
                )
                continue
            invalid = False
            if result.spec_state != "specified":
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "a Plan cannot be proposed before the Spec is confirmed",
                    )
                )
                invalid = True
            if result.plan_state == "approved" or result.approved_plan_id is not None:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "a new Plan cannot replace an approved Plan; revise the Spec first",
                    )
                )
                invalid = True
            revision = _require_revision(event, result.issues, "spec_revision")
            plan_id = _require_string(event, "plan_id", result.issues)
            sha = _require_sha(event, result.issues)
            if revision != result.spec_revision:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "plan_proposed does not target the current Spec revision",
                    )
                )
                invalid = True
            if plan_id is None or sha is None or revision is None:
                invalid = True
            if invalid:
                continue
            result.plan_id = plan_id
            result.plan_spec_revision = revision
            result.plan_content_sha256 = sha
            result.plan_state = "proposed"
            result.approved_plan_id = None
        elif action == "approve_plan":
            if not created:
                result.issues.append(
                    error("E_DECISIONS_CORRUPT", f"decisions[{index}]", "event before project_created")
                )
                continue
            plan_id = _require_string(event, "plan_id", result.issues)
            sha = _require_sha(event, result.issues)
            if plan_id != result.plan_id:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "approve_plan does not match the proposed Plan",
                    )
                )
            if sha != result.plan_content_sha256:
                result.issues.append(
                    error(
                        "E_DECISIONS_CORRUPT",
                        f"decisions[{index}]",
                        "approve_plan hash does not match the proposed Plan content",
                    )
                )
            if result.approved_plan_id is not None:
                result.issues.append(
                    error("E_DECISIONS_CORRUPT", f"decisions[{index}]", "Plan approved more than once")
                )
            result.plan_state = "approved"
            result.approved_plan_id = plan_id
        elif action == "lock":
            rel = _require_string(event, "path", result.issues)
            sha = _require_sha(event, result.issues)
            if rel is not None:
                issue = check_relative_path(rel)
                if issue is not None:
                    result.issues.append(
                        error("E_DECISIONS_CORRUPT", f"decisions[{index}]", issue.message)
                    )
                else:
                    key = path_key(rel)
                    if key in result.locks:
                        result.issues.append(
                            error(
                                "E_DECISIONS_CORRUPT",
                                f"decisions[{index}]",
                                f"file was locked twice without an unlock: {rel}",
                            )
                        )
                    if sha is not None:
                        result.locks[key] = sha
        elif action == "unlock":
            rel = _require_string(event, "path", result.issues)
            if rel is not None:
                issue = check_relative_path(rel)
                if issue is not None:
                    result.issues.append(
                        error("E_DECISIONS_CORRUPT", f"decisions[{index}]", issue.message)
                    )
                else:
                    key = path_key(rel)
                    if key not in result.locks:
                        result.issues.append(
                            error(
                                "E_DECISIONS_CORRUPT",
                                f"decisions[{index}]",
                                f"unlock without a matching lock: {rel}",
                            )
                        )
                    result.locks.pop(key, None)
        # Non-lifecycle notes are deliberately ignored; they do not alter state.
        result.last_event_id = str(event_id)

    if not created:
        result.issues.append(
            error("E_DECISIONS_CORRUPT", "decisions", "history has no project_created event")
        )
    if legacy_seen:
        result.issues.append(
            error(
                "E_DECISIONS_LEGACY",
                "decisions",
                "history_format 2 requires every event to carry event_format and prev_event_id",
            )
        )
    if result.spec_state == "specified" and result.plan_state is None:
        result.plan_state = None
    return result
