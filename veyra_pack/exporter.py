"""Deterministic, atomic ZIP export of a validated pack.

The exporter never writes a "successful" archive for an invalid pack: it
validates first, builds into a temporary file next to the destination, re-opens
that temporary file to verify every member, and only then renames it into
place with :func:`os.replace`.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .contract import (
    PACK_FILENAME,
    Issue,
    Limits,
    PackError,
)
from .paths import is_within
from .validation import validate_pack

#: Fixed ZIP timestamp (the earliest representable DOS date) so identical
#: inputs produce byte-identical archives.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
ZIP_COMPRESSLEVEL = 9


class ExportError(Exception):
    """Raised when a pack cannot be exported; ``issues`` explains why."""

    def __init__(self, message: str, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


@dataclass(frozen=True)
class ExportResult:
    output: Path
    entries: tuple[str, ...]
    size_bytes: int


def export_pack(
    pack_root: str | Path,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
    limits: Limits | None = None,
) -> ExportResult:
    """Validate ``pack_root`` and atomically write a ZIP archive."""

    root = Path(pack_root)
    validation = validate_pack(root, limits)
    if not validation.ok or validation.data is None:
        errors = validation.errors or [
            Issue("E_FATAL", "pack.json", "pack could not be validated")
        ]
        raise ExportError("pack validation failed; nothing was exported", errors)

    data = validation.data
    pack_id = str(data.get("pack_id") or "pack")
    version = str(data.get("version") or "0.0.0")
    manifest_bytes = (root / PACK_FILENAME).read_bytes()

    names = ["pack.json"] + sorted(validation.resolved_files)
    if len(names) != len(set(names)):
        raise ExportError(
            "duplicate archive entries",
            [Issue("E_ZIP_DUPLICATE", "pack.json", "duplicate archive entry")],
        )

    destination = Path(output) if output is not None else Path(f"{pack_id}-{version}.zip")
    destination = destination.resolve()
    if is_within(destination, root):
        raise ExportError(
            "refusing to write the archive inside the pack project",
            [Issue("E_EXPORT_INSIDE_PROJECT", str(destination), "choose an output path outside the pack root")],
        )
    if destination.exists() and not overwrite:
        raise ExportError(
            f"output already exists: {destination}",
            [
                Issue(
                    "E_OUTPUT_EXISTS",
                    str(destination),
                    "pass --overwrite to replace it explicitly",
                )
            ],
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".veyra-export-", suffix=".zip.tmp", dir=str(destination.parent)
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSLEVEL
        ) as archive:
            _write_entry(archive, "pack.json", manifest_bytes)
            for rel in sorted(validation.resolved_files):
                _write_entry(archive, rel, validation.resolved_files[rel].read_bytes())

        _verify_archive(temp_path, validation.resolved_files, manifest_bytes, names)
        os.replace(temp_path, destination)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise

    return ExportResult(
        output=destination,
        entries=tuple(names),
        size_bytes=destination.stat().st_size,
    )


def _write_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    archive.writestr(info, payload, compresslevel=ZIP_COMPRESSLEVEL)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _verify_archive(
    path: Path,
    sources: dict[str, Path],
    manifest_bytes: bytes,
    expected_names: list[str],
) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            if broken is not None:
                raise ExportError(
                    "archive failed its CRC check",
                    [Issue("E_ZIP_CRC", broken, "corrupt ZIP member")],
                )
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ExportError(
                    "archive contains duplicate entries",
                    [Issue("E_ZIP_DUPLICATE", str(path), "duplicate entry names")],
                )
            if set(names) != set(expected_names):
                raise ExportError(
                    "archive entry set does not match the validated manifest",
                    [Issue("E_ZIP_CONTENT", str(path), "unexpected archive contents")],
                )
            if _sha256_bytes(archive.read("pack.json")) != _sha256_bytes(manifest_bytes):
                raise ExportError(
                    "pack.json inside the archive does not match the source",
                    [Issue("E_ZIP_CONTENT", "pack.json", "manifest bytes changed during export")],
                )
            for rel, source in sources.items():
                if _sha256_bytes(archive.read(rel)) != _sha256_bytes(source.read_bytes()):
                    raise ExportError(
                        f"archive member differs from its source: {rel}",
                        [Issue("E_ZIP_CONTENT", rel, "member bytes changed during export")],
                    )
    except zipfile.BadZipFile as exc:
        raise ExportError(
            "produced archive is not a valid ZIP",
            [Issue("E_ZIP_INVALID", str(path), str(exc))],
        ) from exc


def describe_output(path: Path) -> str:
    return str(path)


__all__ = ["ExportError", "ExportResult", "export_pack"]
