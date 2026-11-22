"""Pure path/name/request policy for SafeWriter.

No I/O happens in this module.  Path strings are only a locator: the anchors
still resolve every component through live handles and identities.
"""

from __future__ import annotations

import ntpath
import os
import re
from typing import Iterable

from .errors import E_PATH_INVALID, E_RESOURCE_LIMIT, SafeWriterError

MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_RELATIVE_PATH_CHARS = 1024
MAX_COMPONENT_CHARS = 200
MAX_BATCH_ID_CHARS = 64
MAX_OP_ID_CHARS = 64
MAX_REF_CHARS = 256
MAX_REASON_CHARS = 1000

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    "CONIN$",
    "CONOUT$",
}
for _n in range(1, 10):
    _DEVICE_NAMES.add(f"COM{_n}")
    _DEVICE_NAMES.add(f"LPT{_n}")
_DEVICE_NAMES.update({"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"})

_INVALID_NAME_CHARS = set('<>:"/\\|?*')


def _fail(code: str, message: str) -> None:
    raise SafeWriterError(code, message)


def _has_surrogate(value: str) -> bool:
    """Return True for any UTF-16 surrogate code point.

    Python strings can carry unpaired surrogates (for example through
    ``surrogateescape``).  Windows and UTF-8 encoders do not accept them, so
    rejecting them in policy keeps every later native/journal encode inside the
    structured-error boundary.  A valid non-BMP character such as an emoji is a
    single code point outside this range and remains allowed.
    """
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)


def validate_simple_name(name: str, *, what: str = "component") -> str:
    """Validate one Windows path component used with a directory handle."""
    if not isinstance(name, str) or name == "":
        _fail(E_PATH_INVALID, f"{what} must be a non-empty string")
    if name in {".", ".."}:
        _fail(E_PATH_INVALID, f"{what} may not be '.' or '..'")
    if len(name) > MAX_COMPONENT_CHARS:
        _fail(E_PATH_INVALID, f"{what} exceeds {MAX_COMPONENT_CHARS} characters")
    if name[-1] in {".", " "}:
        _fail(E_PATH_INVALID, f"{what} may not end with a dot or space")
    if any(ch in _INVALID_NAME_CHARS for ch in name):
        _fail(E_PATH_INVALID, f"{what} contains a Windows-invalid character")
    if any(ord(ch) < 32 for ch in name):
        _fail(E_PATH_INVALID, f"{what} contains a control character")
    if _has_surrogate(name):
        _fail(E_PATH_INVALID, f"{what} contains a Unicode surrogate code point")
    base = name.split(".", 1)[0].upper()
    if base in _DEVICE_NAMES:
        _fail(E_PATH_INVALID, f"{what} is a reserved Windows device name")
    return name


def validate_batch_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_BATCH_ID_CHARS:
        _fail(E_PATH_INVALID, "batch_id must be 1..64 characters")
    if not _SAFE_ID_RE.fullmatch(value):
        _fail(E_PATH_INVALID, "batch_id contains unsupported characters")
    return value


def validate_op_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_OP_ID_CHARS:
        _fail(E_PATH_INVALID, "op_id must be 1..64 characters")
    if not _SAFE_ID_RE.fullmatch(value):
        _fail(E_PATH_INVALID, "op_id contains unsupported characters")
    return value


def validate_ref(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_REF_CHARS:
        _fail(E_PATH_INVALID, f"{field} must be 1..{MAX_REF_CHARS} characters")
    if "\x00" in value or "\n" in value or "\r" in value:
        _fail(E_PATH_INVALID, f"{field} contains a control character")
    if _has_surrogate(value):
        _fail(E_PATH_INVALID, f"{field} contains a Unicode surrogate code point")
    return value


def validate_reason(value: str) -> str:
    if not isinstance(value, str):
        _fail(E_PATH_INVALID, "reason must be a string")
    if len(value) > MAX_REASON_CHARS:
        _fail(E_PATH_INVALID, f"reason exceeds {MAX_REASON_CHARS} characters")
    if "\x00" in value:
        _fail(E_PATH_INVALID, "reason contains NUL")
    if _has_surrogate(value):
        _fail(E_PATH_INVALID, "reason contains a Unicode surrogate code point")
    return value


def validate_sha256(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        _fail(E_PATH_INVALID, "sha256 must be 64 lowercase hex characters")
    if any(c not in "0123456789abcdef" for c in value):
        _fail(E_PATH_INVALID, "sha256 must be 64 lowercase hex characters")
    return value


def validate_content(content: bytes) -> bytes:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        _fail(E_PATH_INVALID, "content must be bytes")
    data = bytes(content)
    if len(data) > MAX_CONTENT_BYTES:
        _fail(E_RESOURCE_LIMIT, f"content exceeds {MAX_CONTENT_BYTES} bytes")
    return data


def validate_root_path(root_path: str) -> tuple[str, str, tuple[str, ...]]:
    """Return ``(normalized_root, volume_root, components)``.

    Only a local absolute drive path is accepted.  UNC/device paths, relative
    paths, drive roots and any ``..`` component are rejected before a handle is
    opened.
    """
    if not isinstance(root_path, str) or not root_path:
        _fail(E_PATH_INVALID, "project root must be a non-empty string")
    if "\x00" in root_path:
        _fail(E_PATH_INVALID, "project root contains NUL")
    if _has_surrogate(root_path):
        _fail(E_PATH_INVALID, "project root contains a Unicode surrogate code point")
    if root_path.startswith(("\\\\", "//", "\\?\\", "\\\\.\\", "//?/", "//./")):
        _fail(E_PATH_INVALID, "UNC and device paths are not supported")
    if not ntpath.isabs(root_path):
        _fail(E_PATH_INVALID, "project root must be absolute")
    drive, rest = ntpath.splitdrive(root_path)
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
        _fail(E_PATH_INVALID, "project root must start with a local drive letter")
    normalized = ntpath.normpath(root_path)
    if normalized != ntpath.abspath(normalized):
        _fail(E_PATH_INVALID, "project root must be absolute without '..'")
    drive2, rest2 = ntpath.splitdrive(normalized)
    if drive2.upper() != drive.upper():
        _fail(E_PATH_INVALID, "project root drive changed during normalization")
    tail = rest2.lstrip("\\/")
    if not tail:
        _fail(E_PATH_INVALID, "project root may not be a volume root")
    components = tuple(part for part in re.split(r"[\\/]+", tail) if part)
    if not components:
        _fail(E_PATH_INVALID, "project root has no path components")
    if any(part in {".", ".."} for part in components):
        _fail(E_PATH_INVALID, "project root may not contain '.' or '..'")
    for part in components:
        validate_simple_name(part, what="project root component")
    volume_root = drive.upper() + "\\"
    return normalized, volume_root, components


def validate_relative_path(relative_path: str) -> tuple[str, ...]:
    """Validate a business-relative path and return its components."""
    if not isinstance(relative_path, str) or not relative_path:
        _fail(E_PATH_INVALID, "relative_path must be a non-empty string")
    if len(relative_path) > MAX_RELATIVE_PATH_CHARS:
        _fail(E_PATH_INVALID, f"relative_path exceeds {MAX_RELATIVE_PATH_CHARS} characters")
    if "\x00" in relative_path:
        _fail(E_PATH_INVALID, "relative_path contains NUL")
    if relative_path.startswith(("\\", "/")) or ntpath.splitdrive(relative_path)[0]:
        _fail(E_PATH_INVALID, "relative_path must not be absolute or drive-qualified")
    normalized = relative_path.replace("/", "\\")
    components = tuple(part for part in normalized.split("\\") if part != "")
    if not components:
        _fail(E_PATH_INVALID, "relative_path has no components")
    for part in components:
        validate_simple_name(part, what="relative_path component")
    if components[0].casefold() == "creation":
        _fail(E_PATH_INVALID, "business writes may not target the internal creation directory")
    if sum(len(part) + 1 for part in components) > MAX_RELATIVE_PATH_CHARS:
        _fail(E_PATH_INVALID, "relative_path is too long")
    return components


def validate_id_components(components: Iterable[str]) -> tuple[str, ...]:
    result = tuple(components)
    for part in result:
        validate_simple_name(part, what="internal path component")
    return result


def temp_name(op_id: str, token: str) -> str:
    name = f".veyra-{op_id}-{token}.tmp"
    return validate_simple_name(name, what="temporary name")


def quarantine_name(op_id: str) -> str:
    return validate_simple_name(f"{op_id}.quarantine", what="quarantine name")


def is_under(path: str, parent: str) -> bool:
    """Lexical helper used only for diagnostics, never for trust decisions."""
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(parent)]) == os.path.abspath(parent)
    except (ValueError, OSError):
        return False
