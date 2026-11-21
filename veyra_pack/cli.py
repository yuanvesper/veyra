"""Command line interface: ``python -m veyra_pack <command>``.

The module is intentionally separate from ``triage_agent.py``: crash triage
and pack creation share no entry point, no schema and no options.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contract import PACK_TOOL_VERSION, PACK_TYPES, Issue, PackError
from .exporter import ExportError, export_pack
from .init_project import init_pack
from .validation import validate_pack

EXIT_OK = 0
EXIT_FAILURE = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veyra-pack",
        description=(
            "Create, validate and export Veyra creation packs. "
            "Offline only: no model calls and no network access."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"veyra-pack {PACK_TOOL_VERSION}",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    init = sub.add_parser("init", help="create a new pack project")
    init.add_argument("destination", help="directory for the new pack")
    init.add_argument("--name", required=True, help="human readable pack name")
    init.add_argument(
        "--type",
        required=True,
        dest="pack_type",
        choices=list(PACK_TYPES),
        help="creation kind",
    )
    init.add_argument("--target-game", default=None, help="target game, if any")
    init.add_argument("--author", default=None, help="author name, if any")
    init.add_argument("--description", default="", help="short description")
    init.add_argument("--pack-id", default=None, help="explicit stable pack id")
    init.add_argument("--quiet", action="store_true", help="print only errors")

    validate = sub.add_parser("validate", help="validate a pack project")
    validate.add_argument("pack", help="path to the pack directory")
    validate.add_argument(
        "--json", action="store_true", dest="json_output", help="machine readable result"
    )
    validate.add_argument("--quiet", action="store_true", help="print only errors")

    export = sub.add_parser("export", help="validate and write a deterministic ZIP")
    export.add_argument("pack", help="path to the pack directory")
    export.add_argument("-o", "--output", default=None, help="output .zip path")
    export.add_argument(
        "--overwrite", action="store_true", help="replace an existing output file"
    )
    export.add_argument("--quiet", action="store_true", help="print only errors")
    return parser


def _print_issues(issues: list[Issue], stream) -> None:
    for issue in issues:
        print(issue.format(), file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            pack_path = init_pack(
                args.destination,
                name=args.name,
                pack_type=args.pack_type,
                description=args.description,
                target_game=args.target_game,
                author=args.author,
                pack_id=args.pack_id,
            )
            if not args.quiet:
                print(f"created {pack_path}")
            return EXIT_OK

        if args.command == "validate":
            result = validate_pack(args.pack)
            if args.json_output:
                payload = {
                    "ok": result.ok,
                    "errors": [issue.as_dict() for issue in result.errors],
                    "warnings": [issue.as_dict() for issue in result.warnings],
                    "file_count": len(result.resolved_files),
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                if not args.quiet or result.errors:
                    _print_issues(result.issues, sys.stderr if result.errors else sys.stdout)
                if not args.quiet:
                    status = "valid" if result.ok else "invalid"
                    print(
                        f"pack {status}: {len(result.resolved_files)} files, "
                        f"{len(result.errors)} errors, {len(result.warnings)} warnings"
                    )
            return EXIT_OK if result.ok else EXIT_FAILURE

        if args.command == "export":
            exported = export_pack(args.pack, args.output, overwrite=args.overwrite)
            if not args.quiet:
                print(
                    f"exported {exported.output} "
                    f"({len(exported.entries)} entries, {exported.size_bytes} bytes)"
                )
            return EXIT_OK

        parser.error(f"unknown command: {args.command}")
    except PackError as exc:
        print(f"[{exc.code}] {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except ExportError as exc:
        for issue in exc.issues:
            print(issue.format(), file=sys.stderr)
        if not exc.issues:
            print(str(exc), file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_FAILURE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
