"""Constants, diagnostics and small helpers for the Veyra Pack contract.

The on-disk contract is ``pack.json``.  It is intentionally independent from
the crash-triage report contract: packs describe creative work (a character
kit, a desktop theme, a mod project, ...), while the report contract describes
one crash-log analysis.  Both are versioned separately.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PACK_FILENAME = "pack.json"

#: Version of the pack contract implemented by this package.
SCHEMA_VERSION = "1.0.0"

#: Version of the ``veyra-pack`` command line tool.
PACK_TOOL_VERSION = "0.1.0"

#: Name recorded in the manifest's ``tool`` block.
PACK_TOOL_NAME = "veyra-pack"

#: Only a schema with this major version can be consumed by this tool.
SUPPORTED_SCHEMA_MAJOR = 1

#: Highest schema minor version understood by this tool.
SUPPORTED_SCHEMA_MINOR = 0

#: Creation kinds known to contract 1.0.  New kinds require a contract bump.
PACK_TYPES = (
    "character_kit",
    "desktop_theme",
    "companion",
    "asset_pack",
    "mod",
    "plugin",
    "other",
)

#: Asset kinds known to contract 1.0.
ASSET_KINDS = (
    "image",
    "audio",
    "video",
    "model",
    "texture",
    "document",
    "data",
    "other",
)

REQUIRED_TOP_LEVEL = (
    "schema_version",
    "tool",
    "pack_id",
    "name",
    "description",
    "pack_type",
    "target_game",
    "version",
    "author",
    "assets",
    "files",
    "metadata",
)

TOOL_FIELDS = ("name", "version")

REQUIRED_FILE_FIELDS = ("path",)
REQUIRED_ASSET_FIELDS = ("id", "path", "kind")

PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

MAX_PATH_LENGTH = 512
MAX_PATH_DEPTH = 32


@dataclass(frozen=True)
class Limits:
    """Hard limits enforced by :func:`veyra_pack.validation.validate_pack`."""

    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_files: int = 512
    max_image_dimension: int = 16384
    max_image_pixels: int = 64 * 1024 * 1024
    max_pack_json_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class Issue:
    """One diagnostic produced while validating a pack."""

    code: str
    location: str
    message: str
    severity: str = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def format(self) -> str:
        return f"{self.location}: [{self.code}] {self.message}"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
            "severity": self.severity,
        }


def error(code: str, location: str, message: str) -> Issue:
    return Issue(code=code, location=location, message=message)


def warning(code: str, location: str, message: str) -> Issue:
    return Issue(code=code, location=location, message=message, severity="warning")


@dataclass
class ValidationResult:
    """Outcome of :func:`veyra_pack.validation.validate_pack`."""

    root: Path
    data: dict[str, Any] | None
    issues: list[Issue] = field(default_factory=list)
    #: relative POSIX path -> absolute path, only for files that exist.
    resolved_files: dict[str, Path] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.is_error]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if not issue.is_error]

    @property
    def ok(self) -> bool:
        return not self.errors


class PackError(Exception):
    """A fatal, non-validation error (unreadable root, missing manifest, ...)."""

    def __init__(self, message: str, code: str = "E_FATAL") -> None:
        super().__init__(message)
        self.code = code


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` as UTF-8 JSON to ``path`` atomically.

    The temporary file is created next to the destination so that
    :func:`os.replace` stays on one filesystem and never leaves a partially
    written manifest behind.
    """

    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def iter_unknown_keys(mapping: dict[str, Any], known: Iterable[str]) -> list[str]:
    known_set = set(known)
    return sorted(key for key in mapping if key not in known_set)
