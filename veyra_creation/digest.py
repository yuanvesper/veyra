"""Deterministic Project Digest and integrity verification.

The digest is a *derived view*: it is always reconstructible from the project
tree, ``state.json``, the Spec, the Plan, locks and the append-only decision log.
It is never the source of truth, and it deliberately excludes ``creation/`` and
user-protected files from the scanned file list.

``digest_id`` is computed from content only.  ``generated_at`` is written for
humans and is excluded from the id so that rebuilding an unchanged project
yields the same identity.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from veyra_pack.contract import Issue, error, warning
from veyra_pack.paths import (
    is_creation_path,
    is_link_like,
    path_key,
    resolve_file,
)

from .canonical import canonical_json, now_iso, sha256_file, sha256_text
from .constants import (
    CREATION_SCHEMA_VERSION,
    DIGEST_EXCLUDED_DIRS,
    DIGEST_EXCLUDED_FILES,
    DIGEST_EXCLUDED_ROOT_DIRS,
)
from .errors import CreationError
from .replay import replay_history

DIGEST_ID_RE = re.compile(r"^dig_[0-9a-f]{24}$")


def _iter_project_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(relative POSIX path, absolute path)`` for real project files.

    Symlinks are skipped instead of followed: following them could read outside
    the project or traverse a loop.  ``creation/`` and VCS/cache directories are
    excluded entirely.
    """

    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            candidate = current / name
            if current == root and name in DIGEST_EXCLUDED_ROOT_DIRS:
                continue
            if name in DIGEST_EXCLUDED_DIRS:
                continue
            if is_link_like(candidate):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name in DIGEST_EXCLUDED_FILES:
                continue
            path = current / name
            if is_link_like(path) or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            yield relative, path


def _load_placeholders(store: Any) -> list[dict[str, Any]]:
    return store.load_placeholders()


def _digest_id(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key not in ("digest_id", "generated_at")}
    return "dig_" + sha256_text(canonical_json(body))[:24]


def build_digest(store: Any, *, write: bool = False) -> dict[str, Any]:
    """Build the digest for ``store`` and optionally persist it atomically."""

    state = store.load_state()
    spec = store.load_spec()
    plan = store.load_plan()
    locks = store.load_locks()
    decisions = store.load_decisions()
    runs = store.list_runs()
    placeholders = _load_placeholders(store)

    locked_paths = {
        path_key(str(item.get("path"))): item.get("sha256") for item in locks
    }
    files: list[dict[str, Any]] = []
    for relative, absolute in _iter_project_files(store.root):
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(absolute),
                "size_bytes": absolute.stat().st_size,
            }
        )
    file_index = {path_key(item["path"]): item for item in files}

    deliverables: list[dict[str, Any]] = []
    for item in spec.get("deliverables") or []:
        path = item.get("path")
        if not isinstance(path, str):
            continue
        key = path_key(path)
        resolved, issues = resolve_file(store.root, path, "deliverables")
        actual = file_index.get(key)
        status = "present" if resolved is not None and actual is not None else "missing"
        deliverables.append(
            {
                "id": item.get("id"),
                "path": path,
                "kind": item.get("kind"),
                "role": item.get("role"),
                "optional": bool(item.get("optional", False)),
                "status": status,
                "sha256": actual["sha256"] if actual else None,
                "size_bytes": actual["size_bytes"] if actual else None,
                "locked": key in locked_paths,
            }
        )
    deliverables.sort(key=lambda value: path_key(value["path"]))

    warnings: list[Issue] = []
    for item in deliverables:
        if item["status"] == "missing" and not item["optional"]:
            warnings.append(
                warning(
                    "W_DELIVERABLE_MISSING",
                    f"deliverables.{item['id']}",
                    f"required deliverable is missing: {item['path']}",
                )
            )
    for path, expected in locked_paths.items():
        actual = file_index.get(path)
        if actual is None:
            warnings.append(
                warning("W_LOCK_FILE_MISSING", f"locks.{path}", f"locked file is missing: {path}")
            )
        elif expected is not None and actual["sha256"] != expected:
            warnings.append(
                warning(
                    "W_LOCK_HASH_MISMATCH",
                    f"locks.{path}",
                    f"locked file changed since it was locked: {path}",
                )
            )
    if spec.get("state") == "draft":
        warnings.append(
            warning(
                "W_SPEC_UNCONFIRMED",
                "spec.state",
                "the current Spec revision has not been confirmed by a user",
            )
        )
    if plan is None:
        warnings.append(warning("W_NO_PLAN", "plan", "no Plan has been proposed yet"))

    history_head = sha256_file(store.decisions_path) if store.decisions_path.exists() else None
    runs_failed = sum(1 for run in runs if run.get("status") == "failed")

    payload: dict[str, Any] = {
        "schema_version": CREATION_SCHEMA_VERSION,
        "project_id": state.get("project_id"),
        "project_version": state.get("project_version"),
        "spec": {
            "spec_id": spec.get("spec_id"),
            "revision": spec.get("revision"),
            "state": spec.get("state"),
            "confirmed_revision": state.get("confirmed_spec_revision"),
            "sha256": sha256_file(store.spec_path),
        },
        "project": {
            "name": (spec.get("project") or {}).get("name"),
            "pack_id": (spec.get("project") or {}).get("pack_id"),
            "pack_type": (spec.get("project") or {}).get("pack_type"),
            "target_game": (spec.get("project") or {}).get("target_game"),
            "version": (spec.get("project") or {}).get("version"),
            "profile_id": (spec.get("project") or {}).get("profile_id"),
            "adapter_id": (spec.get("project") or {}).get("adapter_id"),
        },
        "plan": None
        if plan is None
        else {
            "plan_id": plan.get("plan_id"),
            "state": plan.get("state"),
            "spec_revision": plan.get("spec_revision"),
            "steps": len(plan.get("steps") or []),
            "sha256": sha256_file(store.plan_path),
        },
        "deliverables": deliverables,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "locks": [
            {"path": item.get("path"), "sha256": item.get("sha256")} for item in locks
        ],
        "placeholders": placeholders,
        "history": {
            "decision_count": len(decisions),
            "last_event_id": decisions[-1].get("event_id") if decisions else None,
            "head_sha256": history_head,
            "confirmed_spec_revision": state.get("confirmed_spec_revision"),
            "approved_plan_id": state.get("approved_plan_id"),
        },
        "runs": {
            "count": len(runs),
            "failed": runs_failed,
            "last_run_id": runs[-1].get("run_id") if runs else None,
            "last_status": runs[-1].get("status") if runs else None,
            "items": [
                {
                    "run_id": run.get("run_id"),
                    "task": run.get("task"),
                    "status": run.get("status"),
                    "started_at": run.get("started_at"),
                    "finished_at": run.get("finished_at"),
                    "cost_usd": run.get("cost_usd"),
                }
                for run in runs
            ],
        },
        "warnings": [issue.as_dict() for issue in warnings],
    }
    payload["digest_id"] = _digest_id(payload)
    payload["generated_at"] = now_iso(store.clock)

    if write:
        store._assert_no_link(store.digest_path, "digest")
        from veyra_pack.contract import write_json_atomic

        write_json_atomic(store.digest_path, payload)
    return payload


def _plan_content_sha(plan: dict[str, Any]) -> str:
    content = {key: value for key, value in plan.items() if key != "state"}
    return sha256_text(canonical_json(content))


def verify_project(store: Any) -> list[Issue]:
    """Cross-check on-disk state against a replay of the decision log."""

    issues: list[Issue] = []
    try:
        state = store.load_state()
        spec = store.load_spec()
        locks = store.load_locks()
        plan = store.load_plan()
        runs = store.list_runs()
        placeholders = store.load_placeholders()
    except CreationError as exc:
        return [error(exc.code, exc.location, exc.message)]

    replay = replay_history(store)
    issues.extend(replay.issues)
    expected = replay.expected_state()
    for field_name, expected_value in expected.items():
        actual = state.get(field_name)
        if actual != expected_value:
            issues.append(
                error(
                    "E_STATE_DESYNC",
                    f"state.{field_name}",
                    f"replayed history expects {expected_value!r}, state says {actual!r}",
                )
            )

    if replay.spec_id is not None and spec.get("spec_id") != replay.spec_id:
        issues.append(
            error("E_SPEC_TAMPERED", "spec.spec_id", "spec_id does not match the decision log")
        )
    if replay.spec_revision is not None and spec.get("revision") != replay.spec_revision:
        issues.append(
            error("E_SPEC_TAMPERED", "spec.revision", "revision does not match the decision log")
        )
    if replay.spec_state is not None and spec.get("state") != replay.spec_state:
        issues.append(
            error("E_SPEC_TAMPERED", "spec.state", "Spec state does not match the decision log")
        )
    if replay.spec_sha256 is not None:
        actual_spec_sha = sha256_file(store.spec_path)
        if actual_spec_sha != replay.spec_sha256:
            issues.append(
                error(
                    "E_SPEC_TAMPERED",
                    "spec",
                    "spec.json was modified after it was recorded in the decision log",
                )
            )

    if replay.plan_id is None:
        if plan is not None:
            issues.append(
                error("E_PLAN_UNEXPECTED", "plan", "plan.json exists but the decision log never proposed it")
            )
    elif plan is None:
        issues.append(
            error("E_PLAN_MISSING", "plan", "the decision log references a Plan but plan.json is missing")
        )
    else:
        if plan.get("plan_id") != replay.plan_id:
            issues.append(
                error("E_PLAN_TAMPERED", "plan.plan_id", "plan_id does not match the decision log")
            )
        if plan.get("spec_revision") != replay.plan_spec_revision:
            issues.append(
                error("E_PLAN_TAMPERED", "plan.spec_revision", "Plan targets a different Spec revision")
            )
        if replay.plan_content_sha256 is not None:
            actual_plan_sha = _plan_content_sha(plan)
            if actual_plan_sha != replay.plan_content_sha256:
                issues.append(
                    error(
                        "E_PLAN_TAMPERED",
                        "plan",
                        "plan.json content was modified after it was recorded in the decision log",
                    )
                )
        if plan.get("state") != replay.plan_state:
            issues.append(
                error(
                    "E_PLAN_TAMPERED",
                    "plan.state",
                    f"expected Plan state {replay.plan_state!r}, found {plan.get('state')!r}",
                )
            )
        if plan.get("state") != "superseded":
            from .plan_check import check_plan

            issues.extend(check_plan(plan, spec))

    actual_locks = {path_key(item["path"]): item["sha256"] for item in locks}
    if actual_locks != replay.locks:
        missing = sorted(set(replay.locks) - set(actual_locks))
        unexpected = sorted(set(actual_locks) - set(replay.locks))
        changed = sorted(
            key
            for key in set(replay.locks) & set(actual_locks)
            if replay.locks[key] != actual_locks[key]
        )
        detail = ", ".join(
            part
            for part in (
                f"missing {missing}" if missing else "",
                f"unexpected {unexpected}" if unexpected else "",
                f"hash changed {changed}" if changed else "",
            )
            if part
        )
        issues.append(
            error("E_LOCKS_DESYNC", "locks", f"locks.json does not match the decision log: {detail}")
        )

    for lock in locks:
        path = lock.get("path")
        expected_sha = lock.get("sha256")
        resolved, _ = resolve_file(store.root, path, "locks")
        if resolved is None or not resolved.is_file():
            issues.append(
                error(
                    "E_LOCK_DRIFT",
                    f"locks.{path}",
                    f"locked file is missing: {path}",
                )
            )
        else:
            actual_sha = sha256_file(resolved)
            if expected_sha != actual_sha:
                issues.append(
                    error(
                        "E_LOCK_DRIFT",
                        f"locks.{path}",
                        "locked file hash changed; unlock and relock after reviewing the change",
                    )
                )

    run_ids = {run.get("run_id") for run in runs}
    last_run_id = state.get("last_run_id")
    if last_run_id is None:
        if run_ids:
            issues.append(
                error("E_STATE_DESYNC", "state.last_run_id", "runs exist but last_run_id is null")
            )
    elif last_run_id not in run_ids:
        issues.append(
            error("E_RUN_MISSING", "state.last_run_id", f"last_run_id {last_run_id!r} has no run record")
        )

    if placeholders:
        issues.append(
            Issue(
                code="W_PLACEHOLDER_ASSET",
                location="placeholders",
                message=f"{len(placeholders)} placeholder asset(s) still block a formal export",
                severity="warning",
            )
        )

    for item in spec.get("deliverables") or []:
        path = item.get("path")
        resolved, _ = resolve_file(store.root, path, "deliverables")
        if resolved is None or not resolved.is_file():
            if item.get("optional"):
                continue
            issues.append(
                Issue(
                    code="W_DELIVERABLE_MISSING",
                    location=f"deliverables.{item.get('id')}",
                    message=f"required deliverable is missing: {path}",
                    severity="warning",
                )
            )

    if store.digest_path.exists():
        try:
            saved = store._read_json(store.digest_path, "E_DIGEST", "digest")
        except CreationError as exc:
            issues.append(error(exc.code, "digest", exc.message))
        else:
            try:
                current = build_digest(store, write=False)
            except CreationError as exc:
                issues.append(error(exc.code, "digest", exc.message))
            else:
                if saved.get("digest_id") != current.get("digest_id"):
                    issues.append(
                        error(
                            "E_DIGEST_STALE",
                            "digest",
                            "digest.json does not match the current project; rebuild it with `digest --write`",
                        )
                    )
    return issues
