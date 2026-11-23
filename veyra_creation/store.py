"""Versioned, deterministic project state for creation projects.

Only the standard library is used.  The store is single-writer by design: every
mutating method writes one file atomically and then advances
``project_version``.  It never deletes or rewrites user-authored history.
"""

from __future__ import annotations

import datetime as _datetime
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any

from veyra_pack.contract import (
    PACK_ID_RE,
    SHA256_RE,
    VERSION_RE,
    Issue,
    error,
    write_json_atomic,
)
from veyra_pack.paths import (
    PathPolicyError,
    _root_paths_equivalent,
    check_relative_path,
    find_link_component,
    is_creation_path,
    is_link_like,
    normalize_lexical_path,
    path_key,
    resolve_file,
)

from .canonical import canonical_json, now_iso, sha256_file, sha256_text
from .constants import (
    CREATION_DIRNAME,
    CREATION_SCHEMA_VERSION,
    CREATION_TOOL_NAME,
    CREATION_TOOL_VERSION,
    DECISIONS_FILENAME,
    DIGEST_FILENAME,
    LOCKS_FILENAME,
    PLACEHOLDERS_FILENAME,
    PLAN_FILENAME,
    PLAN_STATES,
    RUNS_DIRNAME,
    SPEC_FILENAME,
    SPEC_STATES,
    STATE_FILENAME,
)
from .errors import CreationError
from .plan import load_plan
from .plan_check import check_plan
from .spec import (
    SPEC_ID_RE,
    check_schema_version,
    load_spec,
    make_initial_spec,
    validate_spec,
)

PROJECT_ID_RE = re.compile(r"^prj_[a-z0-9][a-z0-9._-]{0,63}$")
RUN_ID_RE = re.compile(r"^run_[a-z0-9][a-z0-9._-]{0,63}$")
_EVENT_ID_RE = re.compile(r"^dec_[a-z0-9][a-z0-9._-]{0,63}$")
_DECISION_ACTORS = ("user", "system")
STATE_HISTORY_FORMAT = 2
MAX_RUNS = 1000
RESERVED_DECISION_ACTIONS = frozenset(
    {
        "project_created",
        "spec_revised",
        "confirm_spec",
        "plan_proposed",
        "approve_plan",
    }
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        _datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _plan_content_sha(plan: dict[str, Any]) -> str:
    """Hash Plan content, excluding the mutable lifecycle ``state`` field."""

    content = {key: value for key, value in plan.items() if key != "state"}
    return sha256_text(canonical_json(content))


def _validate_state(state: object) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(state, dict):
        return [error("E_STATE_TYPE", "state", "state must be a JSON object")]
    issues.extend(check_schema_version(state.get("schema_version"), "state.schema_version"))
    tool = state.get("tool")
    if not isinstance(tool, dict):
        issues.append(error("E_STATE_TOOL", "state.tool", "tool must be an object"))
    else:
        if not isinstance(tool.get("name"), str) or not tool.get("name"):
            issues.append(error("E_STATE_TOOL", "state.tool.name", "tool.name must be a non-empty string"))
        if not isinstance(tool.get("version"), str) or not tool.get("version"):
            issues.append(
                error("E_STATE_TOOL", "state.tool.version", "tool.version must be a non-empty string")
            )
    project_id = state.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
        issues.append(
            error("E_STATE_PROJECT_ID", "state.project_id", "project_id must look like prj_<slug>")
        )
    pack_id = state.get("pack_id")
    if not isinstance(pack_id, str) or not PACK_ID_RE.match(pack_id):
        issues.append(error("E_STATE_PACK_ID", "state.pack_id", "pack_id is invalid"))
    for key in ("created_at", "updated_at"):
        if not isinstance(state.get(key), str) or not state.get(key):
            issues.append(error("E_STATE_TIME", f"state.{key}", f"{key} must be a timestamp string"))
    if not _is_int(state.get("project_version")) or state.get("project_version") < 1:
        issues.append(
            error("E_STATE_VERSION", "state.project_version", "project_version must be an integer >= 1")
        )
    if not _is_int(state.get("spec_revision")) or state.get("spec_revision") < 1:
        issues.append(
            error("E_STATE_SPEC", "state.spec_revision", "spec_revision must be an integer >= 1")
        )
    if state.get("spec_state") not in SPEC_STATES:
        issues.append(
            error("E_STATE_SPEC", "state.spec_state", f"spec_state must be one of {', '.join(SPEC_STATES)}")
        )
    confirmed = state.get("confirmed_spec_revision")
    if confirmed is not None and (not _is_int(confirmed) or confirmed < 1):
        issues.append(
            error(
                "E_STATE_SPEC",
                "state.confirmed_spec_revision",
                "confirmed_spec_revision must be an integer >= 1 or null",
            )
        )
    for key in ("active_plan_id", "approved_plan_id", "last_run_id", "last_event_id"):
        value = state.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            issues.append(error("E_STATE_FIELD", f"state.{key}", f"{key} must be a string or null"))
    if state.get("history_format") != STATE_HISTORY_FORMAT:
        issues.append(
            error(
                "E_STATE_HISTORY",
                "state.history_format",
                f"history_format must be {STATE_HISTORY_FORMAT}; legacy projects need an explicit migration",
            )
        )
    return issues


def _default_state(project_id: str, pack_id: str, now: str) -> dict[str, Any]:
    return {
        "schema_version": CREATION_SCHEMA_VERSION,
        "tool": {"name": CREATION_TOOL_NAME, "version": CREATION_TOOL_VERSION},
        "project_id": project_id,
        "pack_id": pack_id,
        "created_at": now,
        "updated_at": now,
        "project_version": 1,
        "spec_revision": 1,
        "spec_state": "draft",
        "confirmed_spec_revision": None,
        "active_plan_id": None,
        "approved_plan_id": None,
        "last_run_id": None,
        "last_event_id": None,
        "history_format": STATE_HISTORY_FORMAT,
    }


class ProjectStore:
    """Filesystem-backed state for one creation project."""

    def __init__(self, root: Path | str, *, clock: Any = None) -> None:
        # ``Path("")`` is indistinguishable from ``Path(".")``, so the empty
        # string must be rejected here, before the conversion.  Callers that
        # already converted an empty string to ``Path("")`` have explicitly
        # selected the current directory and cannot be distinguished from a
        # deliberate ``Path(".")``; this boundary is intentional.
        if isinstance(root, str) and root == "":
            raise CreationError(
                "E_PROJECT_PATH_INVALID",
                "project root must not be an empty string",
                "project",
            )
        try:
            declared = normalize_lexical_path(Path(root).expanduser())
        except PathPolicyError as exc:
            raise CreationError(
                exc.code,
                f"invalid project root: {exc}",
                "project",
            ) from exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CreationError(
                "E_PROJECT_ROOT",
                f"invalid project root: {exc}",
                "project",
            ) from exc
        # Check the declared path before resolve(): a dangling junction has
        # ``exists() == False`` but still redirects ``resolve()`` outside the
        # project.  Lexical normalization happens first so that a missing
        # component followed by ``..`` cannot hide a later junction.
        link = find_link_component(declared)
        if link is not None:
            raise CreationError(
                "E_LINK_BOUNDARY",
                f"project root must not contain a symlink or junction: {link}",
                "project",
            )
        try:
            resolved = declared.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise CreationError(
                "E_PROJECT_ROOT",
                f"cannot resolve project root: {exc}",
                "project",
            ) from exc
        if not _root_paths_equivalent(declared, resolved):
            raise CreationError(
                "E_PROJECT_ROOT",
                f"project root resolves to a different path: declared {declared}, resolved {resolved}",
                "project",
            )
        # resolve() must not have followed a link that appeared after the
        # lexical walk; a second walk makes that invariant explicit.
        link = find_link_component(resolved)
        if link is not None:
            raise CreationError(
                "E_LINK_BOUNDARY",
                f"project root must not contain a symlink or junction: {link}",
                "project",
            )
        self.root = resolved
        self.clock = clock
        self.creation_dir = self.root / CREATION_DIRNAME
        self.state_path = self.creation_dir / STATE_FILENAME
        self.spec_path = self.creation_dir / SPEC_FILENAME
        self.plan_path = self.creation_dir / PLAN_FILENAME
        self.decisions_path = self.creation_dir / DECISIONS_FILENAME
        self.locks_path = self.creation_dir / LOCKS_FILENAME
        self.placeholders_path = self.creation_dir / PLACEHOLDERS_FILENAME
        self.digest_path = self.creation_dir / DIGEST_FILENAME
        self.runs_dir = self.creation_dir / RUNS_DIRNAME

    # ------------------------------------------------------------------ init
    def exists(self) -> bool:
        return self.state_path.is_file()

    def init(
        self,
        *,
        name: str,
        pack_type: str,
        target_game: str | None = None,
        pack_id: str | None = None,
        version: str = "0.1.0",
        project_id: str | None = None,
        spec_id: str | None = None,
        profile_id: str | None = None,
        adapter_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_internal()
        if self.root.exists() and not self.root.is_dir():
            raise CreationError("E_PROJECT_ROOT", f"project root is not a directory: {self.root}", "project")
        if self.state_path.exists():
            raise CreationError(
                "E_PROJECT_EXISTS",
                "project already has creation/state.json; refusing to overwrite it",
                "project",
            )
        if self.creation_dir.exists() and any(self.creation_dir.iterdir()):
            raise CreationError(
                "E_PROJECT_EXISTS",
                "creation/ already contains files but no state.json; refusing to initialize over it",
                "project",
            )

        if pack_id is None:
            pack_id = _slug(name)
        if not isinstance(pack_id, str) or not PACK_ID_RE.match(pack_id):
            raise CreationError("E_PROJECT_PACK_ID", f"invalid pack_id: {pack_id!r}", "project")
        if version and not VERSION_RE.match(version):
            raise CreationError("E_PROJECT_VERSION", f"invalid version: {version!r}", "project")
        if project_id is None:
            project_id = "prj_" + uuid.uuid4().hex[:12]
        if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
            raise CreationError("E_PROJECT_ID", f"invalid project_id: {project_id!r}", "project")
        if spec_id is None:
            spec_id = ("spec_" + pack_id)[:64]
        if not isinstance(spec_id, str) or not SPEC_ID_RE.match(spec_id):
            raise CreationError("E_SPEC_ID", f"invalid spec_id: {spec_id!r}", "spec")

        project = {
            "name": name,
            "pack_id": pack_id,
            "pack_type": pack_type,
            "target_game": target_game,
            "version": version,
            "profile_id": profile_id,
            "adapter_id": adapter_id,
        }
        now = self._now()
        spec = make_initial_spec(spec_id=spec_id, project=project, created_at=now)
        spec_issues = validate_spec(spec)
        if spec_issues:
            raise CreationError(
                "E_SPEC_INVALID",
                spec_issues[0].format(),
                "spec",
                details=[issue.as_dict() for issue in spec_issues],
            )
        state = _default_state(project_id, pack_id, now)

        creation_existed = self.creation_dir.exists()
        created_root = not self.root.exists()
        created: list[Path] = []
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._assert_no_link(self.root)
            self.creation_dir.mkdir(parents=True, exist_ok=True)
            self._assert_no_link(self.creation_dir)
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self._assert_no_link(self.runs_dir)
            self._write_json(self.spec_path, spec)
            created.append(self.spec_path)
            self._write_json(self.locks_path, {"schema_version": CREATION_SCHEMA_VERSION, "locks": []})
            created.append(self.locks_path)
            self._write_json(
                self.placeholders_path,
                {"schema_version": CREATION_SCHEMA_VERSION, "placeholders": []},
            )
            created.append(self.placeholders_path)
            self._assert_no_link(self.decisions_path)
            self.decisions_path.write_text("", encoding="utf-8")
            created.append(self.decisions_path)
            event = self._append_decision_raw(
                {
                    "actor": "system",
                    "action": "project_created",
                    "project_id": project_id,
                    "pack_id": pack_id,
                    "spec_id": spec_id,
                    "revision": 1,
                    "sha256": sha256_file(self.spec_path),
                }
            )
            state["last_event_id"] = event["event_id"]
            self._write_json(self.state_path, state)
        except BaseException:
            for path in reversed(created):
                try:
                    path.unlink()
                except OSError:
                    pass
            if not creation_existed:
                try:
                    self.runs_dir.rmdir()
                except OSError:
                    pass
                try:
                    self.creation_dir.rmdir()
                except OSError:
                    pass
            if created_root:
                try:
                    self.root.rmdir()
                except OSError:
                    pass
            raise
        return state

    # ----------------------------------------------------------------- state
    def load_state(self) -> dict[str, Any]:
        data = self._read_json(self.state_path, "E_STATE", "state")
        issues = _validate_state(data)
        if issues:
            raise CreationError(
                "E_STATE_CORRUPT",
                issues[0].format(),
                "state",
                details=[issue.as_dict() for issue in issues],
            )
        return data

    def _commit_state(self, changes: dict[str, Any]) -> dict[str, Any]:
        state = self.load_state()
        state.update(changes)
        state["project_version"] = int(state["project_version"]) + 1
        state["updated_at"] = self._now()
        self._write_json(self.state_path, state)
        return state

    def _assert_no_link(self, path: Path, location: str = "project") -> None:
        """Reject any symlink/junction component in a project path.

        ``ProjectStore.__init__`` rejects links in the declared root path.  A
        link can still be introduced after construction, so every read/write
        checks the whole path again instead of only components below ``root``.
        """

        link = find_link_component(path)
        if link is None:
            return
        raise CreationError(
            "E_LINK_BOUNDARY",
            f"project path must not contain a symlink or junction: {link}",
            location,
        )

    def _assert_internal(self) -> None:
        if self.root.exists() and not self.root.is_dir():
            raise CreationError("E_PROJECT_ROOT", f"project root is not a directory: {self.root}", "project")
        if self.creation_dir.exists() and not self.creation_dir.is_dir():
            raise CreationError(
                "E_PROJECT_ROOT", f"creation/ is not a directory: {self.creation_dir}", "project"
            )
        self._assert_no_link(self.creation_dir)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._assert_no_link(path)
        write_json_atomic(path, payload)

    def _read_json(self, path: Path, code_prefix: str, location: str) -> dict[str, Any]:
        self._assert_no_link(path, location)
        if not path.exists():
            raise CreationError(f"{code_prefix}_MISSING", f"{location} file does not exist: {path}", location)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CreationError(f"{code_prefix}_ENCODING", f"{location} is not valid UTF-8: {exc}", location) from exc
        except OSError as exc:
            raise CreationError(f"{code_prefix}_UNREADABLE", f"cannot read {location}: {exc}", location) from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CreationError(
                f"{code_prefix}_CORRUPT",
                f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                location,
            ) from exc
        if not isinstance(data, dict):
            raise CreationError(f"{code_prefix}_TYPE", f"{location} must be a JSON object", location)
        return data

    # ------------------------------------------------------------------ spec
    def load_spec(self) -> dict[str, Any]:
        self._assert_no_link(self.spec_path, "spec")
        return load_spec(self.spec_path)

    def save_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Persist a new draft revision; a confirmed revision is frozen."""

        self._assert_internal()
        state = self.load_state()
        current = self.load_spec()
        if state.get("spec_revision") != current.get("revision"):
            raise CreationError(
                "E_STATE_DESYNC",
                f"state.spec_revision {state.get('spec_revision')!r} does not match spec revision {current.get('revision')!r}",
                "spec",
            )
        issues = validate_spec(spec)
        if issues:
            raise CreationError(
                "E_SPEC_INVALID",
                issues[0].format(),
                "spec",
                details=[issue.as_dict() for issue in issues],
            )
        if spec.get("spec_id") != current.get("spec_id"):
            raise CreationError("E_SPEC_ID_MISMATCH", "spec_id cannot change between revisions", "spec")
        expected = int(current.get("revision", 0)) + 1
        if spec.get("revision") != expected:
            raise CreationError(
                "E_REVISION_CONFLICT",
                f"new revision must be {expected}, got {spec.get('revision')!r}",
                "spec",
            )
        if spec.get("state") != "draft":
            raise CreationError("E_SPEC_STATE", "a new revision must start in state 'draft'", "spec")
        now = self._now()
        spec = dict(spec)
        spec["updated_at"] = now
        if self.plan_path.exists():
            plan = self.load_plan()
            if plan is not None and plan.get("state") != "superseded":
                plan = dict(plan)
                plan["state"] = "superseded"
                self._write_json(self.plan_path, plan)
        self._write_json(self.spec_path, spec)
        event = self._append_decision_raw(
            {
                "actor": "system",
                "action": "spec_revised",
                "spec_id": spec.get("spec_id"),
                "revision": spec.get("revision"),
                "sha256": sha256_file(self.spec_path),
            }
        )
        self._commit_state(
            {
                "spec_revision": spec.get("revision"),
                "spec_state": "draft",
                "confirmed_spec_revision": None,
                "active_plan_id": None,
                "approved_plan_id": None,
                "last_event_id": event["event_id"],
            }
        )
        return spec

    def confirm_spec(
        self, revision: int, *, actor: str = "user", reason: str | None = None
    ) -> dict[str, Any]:
        self._assert_internal()
        if actor not in _DECISION_ACTORS:
            raise CreationError("E_DECISION_ACTOR", f"actor must be one of {_DECISION_ACTORS}", "spec")
        spec = self.load_spec()
        state = self.load_state()
        if state.get("spec_revision") != spec.get("revision"):
            raise CreationError(
                "E_STATE_DESYNC",
                "state and Spec disagree about the current revision; run verify before confirming",
                "spec",
            )
        if revision != spec.get("revision"):
            raise CreationError(
                "E_REVISION_CONFLICT",
                f"cannot confirm revision {revision}; current revision is {spec.get('revision')}",
                "spec",
            )
        if spec.get("state") == "specified" and state.get("confirmed_spec_revision") == revision:
            actual_sha = sha256_file(self.spec_path)
            for event in reversed(self.load_decisions()):
                if event.get("action") == "confirm_spec" and event.get("revision") == revision:
                    if event.get("sha256") != actual_sha:
                        raise CreationError(
                            "E_SPEC_TAMPERED",
                            "confirmed Spec content no longer matches its confirmation event",
                            "spec",
                        )
                    return spec
            raise CreationError(
                "E_SPEC_TAMPERED",
                "confirmed Spec has no matching confirmation event",
                "spec",
            )
        if spec.get("state") != "draft":
            raise CreationError("E_SPEC_STATE", "only a draft revision can be confirmed", "spec")
        existing_plan = self.load_plan()
        if existing_plan is not None and existing_plan.get("state") != "superseded":
            raise CreationError(
                "E_PLAN_STATE",
                "a Plan already refers to this Spec; revise the Spec before confirming it again",
                "spec",
            )
        spec = dict(spec)
        spec["state"] = "specified"
        spec["updated_at"] = self._now()
        self._write_json(self.spec_path, spec)
        event = self._append_decision_raw(
            {
                "actor": actor,
                "action": "confirm_spec",
                "spec_id": spec.get("spec_id"),
                "revision": revision,
                "reason": reason,
                "sha256": sha256_file(self.spec_path),
            }
        )
        self._commit_state(
            {
                "spec_state": "specified",
                "confirmed_spec_revision": revision,
                "last_event_id": event["event_id"],
            }
        )
        return spec

    # ------------------------------------------------------------------ plan
    def load_plan(self) -> dict[str, Any] | None:
        self._assert_no_link(self.plan_path, "plan")
        if not self.plan_path.exists():
            return None
        return load_plan(self.plan_path)

    def _confirmed_spec(self) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self.load_state()
        spec = self.load_spec()
        if state.get("spec_revision") != spec.get("revision"):
            raise CreationError(
                "E_STATE_DESYNC",
                "state.spec_revision does not match the current Spec revision",
                "plan",
            )
        if (
            spec.get("state") != "specified"
            or state.get("spec_state") != "specified"
            or state.get("confirmed_spec_revision") != spec.get("revision")
        ):
            raise CreationError(
                "E_SPEC_NOT_CONFIRMED",
                "the current Spec revision must be confirmed before a Plan can be used",
                "plan",
            )
        return state, spec

    def save_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        self._assert_internal()
        state, spec = self._confirmed_spec()
        if plan.get("spec_revision") != spec.get("revision"):
            raise CreationError(
                "E_PLAN_INVALID",
                f"plan.spec_revision {plan.get('spec_revision')!r} does not match confirmed revision {spec.get('revision')!r}",
                "plan",
            )
        issues = check_plan(plan, spec)
        if issues:
            raise CreationError(
                "E_PLAN_INVALID",
                issues[0].format(),
                "plan",
                details=[issue.as_dict() for issue in issues],
            )
        if plan.get("state") != "proposed":
            raise CreationError("E_PLAN_STATE", "a new plan must start in state 'proposed'", "plan")
        if self.plan_path.exists():
            existing = self.load_plan()
            if existing is not None:
                if existing.get("state") == "approved":
                    raise CreationError(
                        "E_PLAN_APPROVED",
                        "the active plan is approved; revise the spec to create a new plan",
                        "plan",
                    )
                if existing.get("plan_id") == plan.get("plan_id"):
                    if canonical_json(existing) == canonical_json(plan):
                        return existing
                    raise CreationError(
                        "E_PLAN_EXISTS", "a different plan with this plan_id already exists", "plan"
                    )
                if state.get("active_plan_id") not in (None, existing.get("plan_id")):
                    raise CreationError(
                        "E_STATE_DESYNC",
                        "state.active_plan_id does not match the stored Plan",
                        "plan",
                    )
        plan = dict(plan)
        self._write_json(self.plan_path, plan)
        event = self._append_decision_raw(
            {
                "actor": "system",
                "action": "plan_proposed",
                "plan_id": plan.get("plan_id"),
                "spec_revision": plan.get("spec_revision"),
                "sha256": _plan_content_sha(plan),
            }
        )
        self._commit_state(
            {
                "active_plan_id": plan.get("plan_id"),
                "approved_plan_id": None,
                "last_event_id": event["event_id"],
            }
        )
        return plan

    def approve_plan(self, plan_id: str, *, actor: str = "user", reason: str | None = None) -> dict[str, Any]:
        self._assert_internal()
        if actor not in _DECISION_ACTORS:
            raise CreationError("E_DECISION_ACTOR", f"actor must be one of {_DECISION_ACTORS}", "plan")
        plan = self.load_plan()
        if plan is None:
            raise CreationError("E_PLAN_MISSING", "no plan exists to approve", "plan")
        if plan.get("plan_id") != plan_id:
            raise CreationError(
                "E_PLAN_ID_MISMATCH",
                f"active plan is {plan.get('plan_id')!r}, not {plan_id!r}",
                "plan",
            )
        state, spec = self._confirmed_spec()
        if state.get("active_plan_id") != plan_id:
            raise CreationError(
                "E_STATE_DESYNC", "state.active_plan_id does not match the Plan being approved", "plan"
            )
        if plan.get("state") == "approved" and state.get("approved_plan_id") == plan_id:
            actual_sha = _plan_content_sha(plan)
            approvals = [
                event
                for event in self.load_decisions()
                if event.get("action") == "approve_plan" and event.get("plan_id") == plan_id
            ]
            if len(approvals) != 1:
                raise CreationError(
                    "E_PLAN_TAMPERED",
                    "approved Plan must have exactly one approval event",
                    "plan",
                )
            if approvals[0].get("sha256") != actual_sha:
                raise CreationError(
                    "E_PLAN_TAMPERED",
                    "approved Plan content changed since its approval event",
                    "plan",
                )
            return plan
        if plan.get("state") != "proposed":
            raise CreationError("E_PLAN_STATE", "only a proposed plan can be approved", "plan")
        if state.get("approved_plan_id") is not None:
            raise CreationError(
                "E_PLAN_APPROVED", "another Plan is already approved for this Spec", "plan"
            )
        issues = check_plan(plan, spec)
        if issues:
            raise CreationError(
                "E_PLAN_INVALID",
                issues[0].format(),
                "plan",
                details=[issue.as_dict() for issue in issues],
            )
        content_sha = _plan_content_sha(plan)
        plan = dict(plan)
        plan["state"] = "approved"
        self._write_json(self.plan_path, plan)
        event = self._append_decision_raw(
            {
                "actor": actor,
                "action": "approve_plan",
                "plan_id": plan_id,
                "reason": reason,
                "sha256": content_sha,
            }
        )
        self._commit_state({"approved_plan_id": plan_id, "last_event_id": event["event_id"]})
        return plan

    # -------------------------------------------------------------- decisions
    def load_decisions(self) -> list[dict[str, Any]]:
        self._assert_no_link(self.decisions_path, "decisions")
        if not self.decisions_path.exists():
            raise CreationError(
                "E_DECISIONS_MISSING",
                "decisions.ndjson is missing; append-only history cannot be reconstructed",
                "decisions",
            )
        try:
            text = self.decisions_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CreationError("E_DECISIONS_ENCODING", f"decisions are not valid UTF-8: {exc}", "decisions") from exc
        except OSError as exc:
            raise CreationError("E_DECISIONS_UNREADABLE", f"cannot read decisions: {exc}", "decisions") from exc
        if text and not text.endswith("\n"):
            raise CreationError(
                "E_DECISIONS_CORRUPT",
                "decisions.ndjson is missing its trailing newline; refusing to parse or append",
                "decisions",
            )
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        expected_prev: str | None = None
        saw_legacy = False
        lines = text.splitlines()
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                raise CreationError(
                    "E_DECISIONS_CORRUPT", f"blank decision line at line {index}", "decisions"
                )
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CreationError(
                    "E_DECISIONS_CORRUPT",
                    f"invalid JSON at line {index}: {exc.msg}",
                    "decisions",
                ) from exc
            if not isinstance(event, dict):
                raise CreationError(
                    "E_DECISIONS_CORRUPT", f"decision at line {index} is not an object", "decisions"
                )
            for field in ("event_id", "ts", "actor", "action"):
                if not isinstance(event.get(field), str) or not event.get(field):
                    raise CreationError(
                        "E_DECISIONS_CORRUPT",
                        f"decision at line {index} is missing a string field: {field}",
                        "decisions",
                    )
            if event["actor"] not in _DECISION_ACTORS:
                raise CreationError(
                    "E_DECISIONS_CORRUPT",
                    f"decision at line {index} has an invalid actor: {event['actor']!r}",
                    "decisions",
                )
            event_id = event["event_id"]
            if not _EVENT_ID_RE.match(event_id):
                raise CreationError(
                    "E_DECISIONS_CORRUPT",
                    f"decision at line {index} has an invalid event_id: {event_id!r}",
                    "decisions",
                )
            if event_id in seen:
                raise CreationError(
                    "E_DECISIONS_CORRUPT",
                    f"duplicate event_id at line {index}: {event_id}",
                    "decisions",
                )
            event_format = event.get("event_format")
            if event_format is None:
                saw_legacy = True
            else:
                if event_format != STATE_HISTORY_FORMAT:
                    raise CreationError(
                        "E_DECISIONS_CORRUPT",
                        f"decision at line {index} has unsupported event_format: {event_format!r}",
                        "decisions",
                    )
                if saw_legacy:
                    raise CreationError(
                        "E_DECISIONS_CORRUPT",
                        f"new-format decision at line {index} follows legacy events",
                        "decisions",
                    )
                if "prev_event_id" not in event:
                    raise CreationError(
                        "E_DECISIONS_CORRUPT",
                        f"decision at line {index} is missing prev_event_id",
                        "decisions",
                    )
                if event.get("prev_event_id") != expected_prev:
                    raise CreationError(
                        "E_DECISIONS_CORRUPT",
                        f"decision chain is broken at line {index}: "
                        f"expected prev_event_id {expected_prev!r}, got {event.get('prev_event_id')!r}",
                        "decisions",
                    )
                expected_prev = event_id
            seen.add(event_id)
            events.append(event)
        return events

    def append_decision(
        self, action: str, *, actor: str = "user", event_id: str | None = None, **fields: Any
    ) -> dict[str, Any]:
        if actor not in _DECISION_ACTORS:
            raise CreationError("E_DECISION_ACTOR", f"actor must be one of {_DECISION_ACTORS}", "decisions")
        if not isinstance(action, str) or not action:
            raise CreationError("E_DECISION_ACTION", "action must be a non-empty string", "decisions")
        if action in RESERVED_DECISION_ACTIONS:
            raise CreationError(
                "E_DECISION_ACTION",
                f"{action!r} is a lifecycle event and must be written by its store method",
                "decisions",
            )
        event = self._append_decision_raw(
            {"actor": actor, "action": action, **fields}, event_id=event_id
        )
        self._commit_state({"last_event_id": event["event_id"]})
        return event

    def _append_decision_raw(
        self, payload: dict[str, Any], *, event_id: str | None = None
    ) -> dict[str, Any]:
        existing = self.load_decisions()
        if any(event.get("event_format") != STATE_HISTORY_FORMAT for event in existing):
            raise CreationError(
                "E_DECISIONS_LEGACY",
                "decisions contain legacy events; migrate them before appending",
                "decisions",
            )
        known = {event.get("event_id") for event in existing}
        if event_id is None:
            event_id = self._new_event_id(known)
        elif not _EVENT_ID_RE.match(event_id):
            raise CreationError("E_DECISION_EVENT_ID", f"invalid event_id: {event_id!r}", "decisions")
        if event_id in known:
            raise CreationError("E_DUPLICATE_EVENT", f"duplicate event_id: {event_id}", "decisions")
        previous = existing[-1]["event_id"] if existing else None
        event: dict[str, Any] = {
            "event_id": event_id,
            "ts": self._now(),
            "actor": payload.pop("actor"),
            "action": payload.pop("action"),
            "event_format": STATE_HISTORY_FORMAT,
            "prev_event_id": previous,
        }
        for key, value in payload.items():
            if key not in event:
                event[key] = value
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._assert_no_link(self.decisions_path, "decisions")
        try:
            with self.decisions_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise CreationError("E_DECISIONS_WRITE", f"cannot append decision: {exc}", "decisions") from exc
        return event

    def _new_event_id(self, known: set[str]) -> str:
        for _ in range(100):
            candidate = "dec_" + uuid.uuid4().hex[:12]
            if candidate not in known:
                return candidate
        raise CreationError("E_EVENT_ID", "could not allocate a unique event id", "decisions")

    # ----------------------------------------------------------------- locks
    def load_locks(self) -> list[dict[str, Any]]:
        data = self._read_json(self.locks_path, "E_LOCKS", "locks")
        issues = check_schema_version(data.get("schema_version"), "locks.schema_version")
        if issues:
            raise CreationError("E_LOCKS_CORRUPT", issues[0].format(), "locks")
        locks = data.get("locks")
        if not isinstance(locks, list):
            raise CreationError("E_LOCKS_CORRUPT", "locks must be an array", "locks")
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for index, item in enumerate(locks):
            if not isinstance(item, dict):
                raise CreationError("E_LOCKS_CORRUPT", f"locks[{index}] must be an object", "locks")
            path = item.get("path")
            path_issue = check_relative_path(path)
            if path_issue is not None:
                raise CreationError("E_LOCKS_CORRUPT", f"locks[{index}] has an invalid path", "locks")
            if is_creation_path(path):
                raise CreationError(
                    "E_LOCKS_CORRUPT", f"locks[{index}] points into creation/", "locks"
                )
            sha = item.get("sha256")
            if not isinstance(sha, str) or not SHA256_RE.match(sha):
                raise CreationError("E_LOCKS_CORRUPT", f"locks[{index}] has an invalid sha256", "locks")
            key = path_key(path)
            if key in seen:
                raise CreationError("E_LOCKS_CORRUPT", f"duplicate lock for {path}", "locks")
            seen.add(key)
            result.append(item)
        result.sort(key=lambda item: path_key(item["path"]))
        return result

    def lock(self, path: str, *, reason: str | None = None, actor: str = "user") -> dict[str, Any]:
        self._assert_internal()
        if actor not in _DECISION_ACTORS:
            raise CreationError("E_DECISION_ACTOR", f"actor must be one of {_DECISION_ACTORS}", "locks")
        rel = self._safe_user_path(path)
        resolved, issues = resolve_file(self.root, rel, "locks")
        if resolved is None:
            raise CreationError(
                "E_LOCK_FILE_MISSING",
                issues[0].message if issues else f"file does not exist: {rel}",
                "locks",
            )
        sha = sha256_file(resolved)
        locks = self.load_locks()
        key = path_key(rel)
        existing = {path_key(item["path"]): item for item in locks}
        current = existing.get(key)
        if current is not None:
            if current.get("sha256") == sha:
                return current
            raise CreationError(
                "E_LOCK_CONFLICT",
                f"{rel} is locked at a different hash; unlock it explicitly before changing it",
                "locks",
            )
        item = {
            "path": rel,
            "sha256": sha,
            "locked_at": self._now(),
            "reason": reason,
        }
        locks.append(item)
        locks.sort(key=lambda value: path_key(value["path"]))
        self._write_json(self.locks_path, {"schema_version": CREATION_SCHEMA_VERSION, "locks": locks})
        event = self._append_decision_raw(
            {"actor": actor, "action": "lock", "path": rel, "sha256": sha, "reason": reason}
        )
        self._commit_state({"last_event_id": event["event_id"]})
        return item

    def unlock(self, path: str, *, actor: str = "user", reason: str | None = None) -> dict[str, Any]:
        self._assert_internal()
        if actor not in _DECISION_ACTORS:
            raise CreationError("E_DECISION_ACTOR", f"actor must be one of {_DECISION_ACTORS}", "locks")
        rel = self._safe_user_path(path)
        key = path_key(rel)
        locks = self.load_locks()
        target = next((item for item in locks if path_key(item["path"]) == key), None)
        if target is None:
            raise CreationError("E_LOCK_NOT_FOUND", f"{rel} is not locked", "locks")
        locks = [item for item in locks if path_key(item["path"]) != key]
        self._write_json(self.locks_path, {"schema_version": CREATION_SCHEMA_VERSION, "locks": locks})
        event = self._append_decision_raw(
            {
                "actor": actor,
                "action": "unlock",
                "path": target["path"],
                "sha256": target.get("sha256"),
                "reason": reason,
            }
        )
        self._commit_state({"last_event_id": event["event_id"]})
        return target

    def is_locked(self, path: str) -> bool:
        key = path_key(self._safe_user_path(path))
        return any(path_key(item["path"]) == key for item in self.load_locks())

    # ---------------------------------------------------------- placeholders
    def load_placeholders(self) -> list[dict[str, Any]]:
        data = self._read_json(self.placeholders_path, "E_PLACEHOLDERS", "placeholders")
        issues = check_schema_version(data.get("schema_version"), "placeholders.schema_version")
        if issues:
            raise CreationError("E_PLACEHOLDERS_CORRUPT", issues[0].format(), "placeholders")
        items = data.get("placeholders")
        if not isinstance(items, list):
            raise CreationError(
                "E_PLACEHOLDERS_CORRUPT", "placeholders must be an array", "placeholders"
            )
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise CreationError(
                    "E_PLACEHOLDERS_CORRUPT", f"placeholders[{index}] must be an object", "placeholders"
                )
            rel = item.get("path")
            if check_relative_path(rel) is not None or is_creation_path(rel):
                raise CreationError(
                    "E_PLACEHOLDERS_CORRUPT", f"placeholders[{index}] has an invalid path", "placeholders"
                )
            key = path_key(rel)
            if key in seen:
                raise CreationError(
                    "E_PLACEHOLDERS_CORRUPT", f"duplicate placeholder for {rel}", "placeholders"
                )
            seen.add(key)
            if "id" in item and (not isinstance(item["id"], str) or not item["id"]):
                raise CreationError(
                    "E_PLACEHOLDERS_CORRUPT", f"placeholders[{index}].id must be a string", "placeholders"
                )
            for label in ("reason", "created_at"):
                if label in item and item[label] is not None and not isinstance(item[label], str):
                    raise CreationError(
                        "E_PLACEHOLDERS_CORRUPT",
                        f"placeholders[{index}].{label} must be null or a string",
                        "placeholders",
                    )
            result.append(item)
        result.sort(key=lambda item: path_key(item["path"]))
        return result

    # ------------------------------------------------------------------ runs
    def start_run(
        self,
        task: str,
        *,
        run_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._assert_internal()
        if not isinstance(task, str) or not task:
            raise CreationError("E_RUN_TASK", "task must be a non-empty string", "runs")
        if run_id is None:
            run_id = self._new_run_id()
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise CreationError("E_RUN_ID", f"invalid run_id: {run_id!r}", "runs")
        if metadata is not None and not isinstance(metadata, dict):
            raise CreationError("E_RUN_METADATA", "metadata must be an object", "runs")
        for label, value in (("model", model), ("provider", provider)):
            if value is not None and (not isinstance(value, str) or not value):
                raise CreationError("E_RUN_FIELD", f"{label} must be a non-empty string", "runs")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._assert_no_link(self.runs_dir, "runs")
        path = self._run_path(run_id)
        if path.exists():
            raise CreationError("E_RUN_EXISTS", f"run already exists: {run_id}", "runs")
        run = {
            "schema_version": CREATION_SCHEMA_VERSION,
            "run_id": run_id,
            "task": task,
            "status": "running",
            "started_at": self._now(),
            "finished_at": None,
            "model": model,
            "provider": provider,
            "usage": None,
            "cost_usd": None,
            "cost_source": None,
            "outputs": [],
            "error": None,
            "metadata": metadata or {},
        }
        self._write_json(path, run)
        self._commit_state({"last_run_id": run_id})
        return run

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        usage: dict[str, Any] | None = None,
        cost_usd: float | None = None,
        cost_source: str | None = None,
        outputs: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in ("ok", "failed", "cancelled", "paused_budget"):
            raise CreationError("E_RUN_STATUS", f"invalid final run status: {status!r}", "runs")
        run = self.load_run(run_id)
        if run.get("status") != "running":
            raise CreationError("E_RUN_FINISHED", f"run is already finished: {run_id}", "runs")
        run = dict(run)
        run["status"] = status
        run["finished_at"] = self._now()
        if usage is not None:
            run["usage"] = usage
        if cost_usd is not None:
            run["cost_usd"] = cost_usd
        if cost_source is not None:
            run["cost_source"] = cost_source
        if outputs is not None:
            run["outputs"] = outputs
        if error is not None:
            run["error"] = error
        issues = self._validate_run(run, self._run_path(run_id).name)
        if issues:
            raise CreationError("E_RUN_INVALID", issues[0].format(), "runs")
        self._write_json(self._run_path(run_id), run)
        self._commit_state({"last_run_id": run_id})
        return run

    def load_run(self, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise CreationError("E_RUN_ID", f"invalid run_id: {run_id!r}", "runs")
        path = self._run_path(run_id)
        if not path.exists():
            raise CreationError("E_RUN_MISSING", f"run does not exist: {run_id}", "runs")
        data = self._read_json(path, "E_RUN", "runs")
        issues = self._validate_run(data, path.name)
        if issues:
            raise CreationError("E_RUN_CORRUPT", issues[0].format(), "runs")
        return data

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        self._assert_no_link(self.runs_dir, "runs")
        paths = sorted(self.runs_dir.glob("*.json"))
        if len(paths) > MAX_RUNS:
            raise CreationError(
                "E_RUNS_LIMIT", f"runs/ contains {len(paths)} records; limit is {MAX_RUNS}", "runs"
            )
        runs: list[dict[str, Any]] = []
        for path in paths:
            try:
                data = self._read_json(path, "E_RUN", "runs")
            except CreationError as exc:
                raise CreationError(
                    "E_RUN_CORRUPT", f"{path.name}: {exc.message}", "runs"
                ) from exc
            issues = self._validate_run(data, path.name)
            if issues:
                raise CreationError("E_RUN_CORRUPT", f"{path.name}: {issues[0].format()}", "runs")
            runs.append(data)
        runs.sort(key=lambda item: (item.get("started_at", ""), item.get("run_id", "")))
        return runs

    def _validate_run(self, run: dict[str, Any], filename: str) -> list[Issue]:
        issues: list[Issue] = []
        issues.extend(check_schema_version(run.get("schema_version"), "run.schema_version"))
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            issues.append(error("E_RUN_ID", "run.run_id", "run_id is invalid"))
        elif f"{run_id}.json" != filename:
            issues.append(error("E_RUN_ID", "run.run_id", "run_id does not match its filename"))
        if not isinstance(run.get("task"), str) or not run.get("task"):
            issues.append(error("E_RUN_TASK", "run.task", "task must be a non-empty string"))
        started = run.get("started_at")
        if not _is_timestamp(started):
            issues.append(error("E_RUN_TIME", "run.started_at", "started_at must be an ISO timestamp"))
        status = run.get("status")
        if status not in ("running", "ok", "failed", "cancelled", "paused_budget"):
            issues.append(error("E_RUN_STATUS", "run.status", "invalid run status"))
        finished = run.get("finished_at")
        if status == "running":
            if finished is not None:
                issues.append(
                    error("E_RUN_TIME", "run.finished_at", "a running run must not have finished_at")
                )
        elif not _is_timestamp(finished):
            issues.append(
                error("E_RUN_TIME", "run.finished_at", "a finished run needs an ISO finished_at")
            )
        elif _is_timestamp(started):
            try:
                start_dt = _datetime.datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                end_dt = _datetime.datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                if end_dt < start_dt:
                    issues.append(
                        error("E_RUN_TIME", "run.finished_at", "finished_at is before started_at")
                    )
        for label in ("model", "provider"):
            value = run.get(label)
            if value is not None and (not isinstance(value, str) or not value):
                issues.append(error("E_RUN_FIELD", f"run.{label}", f"{label} must be null or a string"))
        usage = run.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                issues.append(error("E_RUN_USAGE", "run.usage", "usage must be null or an object"))
            else:
                for key in ("input_tokens", "output_tokens", "images"):
                    if key in usage and (not _is_int(usage[key]) or usage[key] < 0):
                        issues.append(
                            error("E_RUN_USAGE", f"run.usage.{key}", f"{key} must be a non-negative integer")
                        )
        cost = run.get("cost_usd")
        if cost is not None and (not _is_number(cost) or float(cost) < 0):
            issues.append(
                error("E_RUN_COST", "run.cost_usd", "cost_usd must be null or a non-negative number")
            )
        source = run.get("cost_source")
        if source is not None and (not isinstance(source, str) or not source):
            issues.append(
                error("E_RUN_COST", "run.cost_source", "cost_source must be null or a string")
            )
        outputs = run.get("outputs")
        if not isinstance(outputs, list) or not all(isinstance(item, dict) for item in outputs):
            issues.append(error("E_RUN_OUTPUTS", "run.outputs", "outputs must be an array of objects"))
        error_text = run.get("error")
        if error_text is not None and not isinstance(error_text, str):
            issues.append(error("E_RUN_ERROR", "run.error", "error must be null or a string"))
        if "metadata" in run and not isinstance(run["metadata"], dict):
            issues.append(error("E_RUN_METADATA", "run.metadata", "metadata must be an object"))
        return issues

    def _run_path(self, run_id: str) -> Path:
        if not RUN_ID_RE.match(run_id):
            raise CreationError("E_RUN_ID", f"invalid run_id: {run_id!r}", "runs")
        return self.runs_dir / f"{run_id}.json"

    def _new_run_id(self) -> str:
        stamp = self._now().replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")
        return f"run_{stamp}_{uuid.uuid4().hex[:6]}"

    # ---------------------------------------------------------------- digest
    def build_digest(self, *, write: bool = False) -> dict[str, Any]:
        from .digest import build_digest

        return build_digest(self, write=write)

    def verify(self) -> list[Issue]:
        from .digest import verify_project

        return verify_project(self)

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        state = self.load_state()
        spec = self.load_spec()
        plan = self.load_plan()
        locks = self.load_locks()
        runs = self.list_runs()
        return {
            "project_id": state.get("project_id"),
            "project_version": state.get("project_version"),
            "spec": {
                "spec_id": spec.get("spec_id"),
                "revision": spec.get("revision"),
                "state": spec.get("state"),
                "deliverables": len(spec.get("deliverables") or []),
            },
            "plan": None
            if plan is None
            else {
                "plan_id": plan.get("plan_id"),
                "state": plan.get("state"),
                "spec_revision": plan.get("spec_revision"),
                "steps": len(plan.get("steps") or []),
            },
            "locks": len(locks),
            "runs": {
                "count": len(runs),
                "last": runs[-1].get("run_id") if runs else None,
            },
            "confirmed_spec_revision": state.get("confirmed_spec_revision"),
            "approved_plan_id": state.get("approved_plan_id"),
        }

    # ----------------------------------------------------------------- helpers
    def _now(self) -> str:
        return now_iso(self.clock)

    def _safe_user_path(self, rel: object) -> str:
        issue = check_relative_path(rel)
        if issue is not None:
            raise CreationError(issue.code, issue.message, "path")
        assert isinstance(rel, str)
        if is_creation_path(rel):
            raise CreationError(
                "E_CREATION_PATH",
                "internal creation/ paths cannot be locked or edited as deliverables",
                "path",
            )
        return rel


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", text.strip().lower()).strip("-._")
    if not value:
        value = "creation-project"
    return value[:64]
