"""Command line interface for creation projects (Phase 3.2.1).

Commands are intentionally small and deterministic.  They only read and write
project state; no model, image or build provider is contacted here.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from veyra_pack.contract import PACK_TYPES

from . import __version__
from .digest import build_digest, verify_project
from .errors import CreationError
from .store import ProjectStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m veyra_creation",
        description="Manage deterministic Veyra creation projects.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new project with creation/state.json")
    init.add_argument("project", help="project directory")
    init.add_argument("--name", help="display name (default: directory name)")
    init.add_argument("--pack-type", choices=PACK_TYPES, default="mod")
    init.add_argument("--target-game", default=None)
    init.add_argument("--pack-id", default=None)
    init.add_argument("--project-version", default="0.1.0")
    init.add_argument("--profile", default=None)
    init.add_argument("--adapter", default=None)
    init.add_argument("--json", action="store_true", help="print machine-readable JSON")

    status = sub.add_parser("status", help="show project status")
    status.add_argument("project", help="project directory")
    status.add_argument("--json", action="store_true")

    digest = sub.add_parser("digest", help="build the deterministic Project Digest")
    digest.add_argument("project", help="project directory")
    digest.add_argument(
        "--write", action="store_true", help="write creation/digest.json atomically"
    )

    verify = sub.add_parser("verify", help="check state, locks and digest consistency")
    verify.add_argument("project", help="project directory")
    verify.add_argument("--json", action="store_true")
    return parser


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _store(path: str) -> ProjectStore:
    # Check the raw CLI argument before ``Path()``: ``Path("")`` is
    # indistinguishable from ``Path(".")``, so an empty string must be
    # rejected at this boundary rather than silently targeting the cwd.
    if path == "":
        raise CreationError(
            "E_PROJECT_PATH_INVALID",
            "project path must not be empty",
            "project",
        )
    return ProjectStore(path)


def _cmd_init(args: argparse.Namespace) -> int:
    store = _store(args.project)
    name = args.name or store.root.name or "creation-project"
    store.init(
        name=name,
        pack_type=args.pack_type,
        target_game=args.target_game,
        pack_id=args.pack_id,
        version=args.project_version,
        profile_id=args.profile,
        adapter_id=args.adapter,
    )
    state = store.load_state()
    spec = store.load_spec()
    result = {
        "ok": True,
        "project": str(store.root),
        "project_id": state.get("project_id"),
        "spec_id": spec.get("spec_id"),
        "project_version": state.get("project_version"),
        "spec_revision": spec.get("revision"),
    }
    if args.json:
        _print_json(result)
    else:
        print(f"Initialized {name!r} at {store.root}")
        print(f"  project_id: {result['project_id']}")
        print(f"  spec_id:    {result['spec_id']}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = _store(args.project)
    status = store.status()
    if args.json:
        _print_json({"ok": True, **status})
        return 0
    spec = status["spec"]
    print(f"Project: {status['project_id']}  version {status['project_version']}")
    print(f"Spec:    {spec['spec_id']} revision {spec['revision']} ({spec['state']})")
    print(f"         deliverables: {spec['deliverables']}")
    plan = status["plan"]
    if plan is None:
        print("Plan:    none")
    else:
        print(f"Plan:    {plan['plan_id']} ({plan['state']}, {plan['steps']} steps)")
    print(f"Locks:   {status['locks']}")
    print(f"Runs:    {status['runs']['count']} (last: {status['runs']['last']})")
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    store = _store(args.project)
    digest = build_digest(store, write=args.write)
    if args.write:
        print(f"Wrote {store.digest_path}", file=sys.stderr)
    print(json.dumps(digest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    store = _store(args.project)
    issues = verify_project(store)
    errors = [issue for issue in issues if issue.is_error]
    warnings = [issue for issue in issues if not issue.is_error]
    ok = not errors
    if args.json:
        _print_json(
            {
                "ok": ok,
                "project": str(store.root),
                "errors": [issue.as_dict() for issue in errors],
                "warnings": [issue.as_dict() for issue in warnings],
            }
        )
    else:
        if ok:
            print(f"OK: no errors, {len(warnings)} warning(s).")
        else:
            print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s).")
        for issue in issues:
            print(issue.format())
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _cmd_init(args)
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "digest":
            return _cmd_digest(args)
        if args.command == "verify":
            return _cmd_verify(args)
    except CreationError as exc:
        if getattr(args, "json", False):
            _print_json({"ok": False, "error": exc.as_dict()})
        else:
            print(exc.format(), file=sys.stderr)
        return 2
    except OSError as exc:
        if getattr(args, "json", False):
            _print_json({"ok": False, "error": {"code": "E_OS", "message": str(exc)}})
        else:
            print(f"E_OS: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
