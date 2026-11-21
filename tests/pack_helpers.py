"""Shared helpers for the veyra_pack test modules.

The helpers only touch temporary directories supplied by the tests; they never
read or write the repository itself.
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def png_bytes(width: int = 2, height: int = 3) -> bytes:
    """A PNG header good enough for dimension parsing (no pixel data)."""

    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def base_manifest(**overrides) -> dict:
    data = {
        "schema_version": "1.0.0",
        "tool": {"name": "veyra-pack", "version": "0.1.0"},
        "pack_id": "demo-kit-1234abcd",
        "name": "Demo Kit",
        "description": "test pack",
        "pack_type": "character_kit",
        "target_game": None,
        "version": "0.1.0",
        "author": None,
        "assets": [],
        "files": [],
        "metadata": {},
    }
    data.update(overrides)
    return data


def write_pack(
    root: Path,
    data: dict | None = None,
    files: dict[str, bytes] | None = None,
    *,
    manifest_text: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, payload in (files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    if manifest_text is None:
        manifest_text = json.dumps(data if data is not None else base_manifest(), indent=2)
    (root / "pack.json").write_text(manifest_text, encoding="utf-8")
    return root


def file_entry(rel: str, payload: bytes | None = None, **extra) -> dict:
    entry = {"path": rel}
    if payload is not None:
        entry["sha256"] = sha256(payload)
        entry["size_bytes"] = len(payload)
    entry.update(extra)
    return entry


def asset_entry(
    asset_id: str, rel: str, kind: str = "image", license_data: dict | None = None, **extra
) -> dict:
    entry = {"id": asset_id, "path": rel, "kind": kind}
    if license_data is None:
        license_data = {"spdx": "CC0-1.0", "source": "original"}
    entry["license"] = license_data
    entry.update(extra)
    return entry


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    from veyra_pack.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def error_codes(result) -> set[str]:
    return {issue.code for issue in result.errors}
