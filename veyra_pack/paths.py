"""Safe resolution of pack-relative paths.

All manifest paths are POSIX-style paths relative to the pack root.  Windows
drive letters, UNC prefixes, backslashes, absolute paths and ``..`` segments
are rejected before the filesystem is touched; after joining, every existing
component is checked so that a symlink (or NTFS junction) cannot redirect a
read outside the project.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import re
import stat
import unicodedata
from pathlib import Path

from .contract import (
    MAX_PATH_DEPTH,
    MAX_PATH_LENGTH,
    Issue,
    error,
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WINDOWS_LONG_PATH_CAPACITY = 32768
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_FILENAME_EXCED_RANGE = 206
_LONG_PATH_NAME_UNAVAILABLE = object()
_GET_LONG_PATH_NAME_W = None


def check_relative_path(rel: object) -> Issue | None:
    """Return an :class:`Issue` when ``rel`` is not a safe relative path."""

    if not isinstance(rel, str):
        return error("E_PATH_TYPE", "path", f"path must be a string, got {type(rel).__name__}")
    if not rel:
        return error("E_PATH_EMPTY", "path", "path must not be empty")
    if "\x00" in rel:
        return error("E_PATH_NUL", "path", "path must not contain NUL bytes")
    if _DRIVE_RE.match(rel):
        return error("E_PATH_DRIVE", "path", "Windows drive-letter paths are not allowed")
    if ":" in rel:
        return error(
            "E_PATH_ADS",
            "path",
            "NTFS alternate data streams (':' in a path) are not allowed",
        )
    if "\\" in rel:
        return error(
            "E_PATH_BACKSLASH",
            "path",
            "path must use '/' separators; backslashes are not allowed",
        )
    if rel.startswith("/") or rel.startswith("//"):
        return error("E_PATH_ABSOLUTE", "path", "path must be relative to the pack root")
    if len(rel) > MAX_PATH_LENGTH:
        return error("E_PATH_TOO_LONG", "path", f"path exceeds {MAX_PATH_LENGTH} characters")
    parts = rel.split("/")
    if len(parts) > MAX_PATH_DEPTH:
        return error("E_PATH_TOO_DEEP", "path", f"path exceeds {MAX_PATH_DEPTH} segments")
    for part in parts:
        if part in ("", "."):
            return error("E_PATH_SEGMENT", "path", "empty or '.' path segments are not allowed")
        if part == "..":
            return error(
                "E_PATH_TRAVERSAL", "path", "'..' segments must not escape the pack root"
            )
        if part.rstrip(" .") != part:
            return error(
                "E_PATH_TRAILING", "path", "segments must not end with a dot or a space"
            )
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_NAMES:
            return error(
                "E_PATH_RESERVED", "path", f"'{part}' is a reserved Windows device name"
            )
    return None


def _is_link_like(path: Path) -> bool:
    """Return True when ``path`` is a symlink or any Windows reparse point.

    ``lstat`` inspects the entry itself instead of following it.  Any metadata
    failure is treated as unsafe.  On Windows, ``FILE_ATTRIBUTE_REPARSE_POINT``
    covers junctions, symlinks, mount points and other reparse points, including
    on Python versions that do not provide ``os.path.isjunction``.
    """

    try:
        status = path.lstat()
    except OSError:
        return True
    if stat.S_ISLNK(status.st_mode):
        return True
    if os.name == "nt" and not hasattr(status, "st_file_attributes"):
        return True
    if getattr(status, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            if isjunction(path):
                return True
        except OSError:
            return True
    return False


def is_link_like(path: Path) -> bool:
    """Public spelling of the symlink/junction check used by callers."""

    return _is_link_like(path)


def path_key(rel: str) -> str:
    """Return the stable identity key for a project-relative path.

    Windows compares paths case-insensitively.  Using Unicode NFC plus
    ``casefold`` gives the same identity on every host while preserving the
    user's original spelling for display and on-disk writes.
    """

    return unicodedata.normalize("NFC", rel).casefold()


def is_creation_path(rel: object) -> bool:
    """Return True when ``rel`` targets the internal ``creation/`` directory."""

    if not isinstance(rel, str):
        return False
    key = path_key(rel)
    return key == CREATION_DIRNAME_CASEFOLD or key.startswith(CREATION_DIRNAME_CASEFOLD + "/")


CREATION_DIRNAME_CASEFOLD = "creation"


class PathPolicyError(ValueError):
    """A project-root path was rejected before any filesystem access.

    ``code`` is a stable Veyra error code; callers such as
    :class:`veyra_creation.store.ProjectStore` translate it into their public
    error type without leaking a platform exception.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_WINDOWS_DRIVE_ONLY_RE = re.compile(r"^[A-Za-z]:$")
_WINDOWS_INVALID_CHARS = frozenset('<>"|?*')


def _windows_path_text(path: Path | str) -> str:
    try:
        raw = os.fspath(path)
    except TypeError as exc:
        raise PathPolicyError("E_PROJECT_PATH_TYPE", "path must be a string or Path") from exc
    if not isinstance(raw, str):
        raise PathPolicyError("E_PROJECT_PATH_TYPE", "path must be text, not bytes")
    return raw


def _reject_windows_namespace(raw: str) -> None:
    """Reject namespaces whose filesystem semantics cannot be checked safely."""

    probe = raw.replace("/", "\\")
    upper = probe.upper()
    if probe.startswith("\\\\?\\") or probe.startswith("\\\\.\\"):
        raise PathPolicyError(
            "E_PROJECT_PATH_DEVICE",
            "extended-length and device namespace paths are not supported",
        )
    if upper.startswith("\\??\\") or upper.startswith("\\DEVICE\\") or upper.startswith("\\GLOBAL??\\"):
        raise PathPolicyError(
            "E_PROJECT_PATH_DEVICE",
            "NT device namespace paths are not supported",
        )
    if probe.startswith("\\\\"):
        raise PathPolicyError(
            "E_PROJECT_PATH_UNC",
            "UNC project roots are not supported",
        )


def _validate_windows_root_components(raw: str) -> None:
    """Validate a Windows root before ``abspath`` can hide unsafe components."""

    _reject_windows_namespace(raw)
    drive, tail = ntpath.splitdrive(raw)
    if drive:
        if not _WINDOWS_DRIVE_ONLY_RE.match(drive):
            raise PathPolicyError(
                "E_PROJECT_PATH_DEVICE",
                f"unsupported Windows path prefix: {drive!r}",
            )
        if not tail.startswith(("\\", "/")):
            raise PathPolicyError(
                "E_PROJECT_PATH_DRIVE_RELATIVE",
                "drive-relative paths (for example 'C:foo') are not supported",
            )
        if not tail:
            raise PathPolicyError(
                "E_PROJECT_PATH_DRIVE_RELATIVE",
                "a drive letter without a root directory is not supported",
            )
    else:
        if raw.startswith(("\\", "/")):
            raise PathPolicyError(
                "E_PROJECT_PATH_INVALID",
                "root-relative Windows paths need an explicit drive",
            )

    parts = tail.replace("/", "\\").split("\\")
    if parts and parts[0] == "":
        parts = parts[1:]
    for part in parts:
        if part in ("", ".", ".."):
            continue
        if part.endswith((" ", ".")):
            raise PathPolicyError(
                "E_PROJECT_PATH_TRAILING",
                "path components must not end with a dot or a space",
            )
        if ":" in part:
            raise PathPolicyError(
                "E_PROJECT_PATH_ADS",
                "NTFS alternate data streams are not supported in a project root",
            )
        if any(char in _WINDOWS_INVALID_CHARS or ord(char) < 32 for char in part):
            raise PathPolicyError(
                "E_PROJECT_PATH_INVALID",
                f"path component contains a Windows-invalid character: {part!r}",
            )
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_NAMES:
            raise PathPolicyError(
                "E_PROJECT_PATH_RESERVED",
                f"'{part}' is a reserved Windows device name",
            )


def normalize_lexical_path(path: Path | str) -> Path:
    """Normalize ``path`` without following symlinks or junctions.

    On Windows this first applies a strict root policy: normal absolute drive
    paths and relative paths are supported, while UNC roots, device/extended
    namespaces, drive-relative paths and components ending in a dot or space
    are rejected before the filesystem is touched.  ``abspath`` then collapses
    ``.`` and ``..`` lexically; it never follows a link.  Callers must still
    walk every resulting component with :func:`find_link_component` before
    resolving or writing.
    """

    raw = _windows_path_text(path)
    if not raw:
        raise PathPolicyError("E_PROJECT_PATH_INVALID", "path must not be empty")
    if "\x00" in raw:
        raise PathPolicyError("E_PROJECT_PATH_INVALID", "path contains an embedded NUL")
    if os.name != "nt":
        return Path(os.path.abspath(raw))

    _validate_windows_root_components(raw)
    normalized_text = ntpath.abspath(raw)
    # A relative path can inherit a UNC current directory; validate the
    # absolute result as well so that case cannot silently become a UNC root.
    _validate_windows_root_components(normalized_text)
    return Path(normalized_text)


def _windows_compare_key(path: Path | str) -> str:
    text = os.fspath(path).replace("/", "\\")
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return ntpath.normcase(ntpath.normpath(text))


def paths_equivalent(left: Path | str, right: Path | str) -> bool:
    """Return True when two paths name the same location without resolving.

    On Windows the comparison is case-insensitive and treats the
    ``\\\\?\\`` extended prefix as an implementation detail.  It deliberately
    does not use ``samefile``: the root may not exist yet and filesystem
    identity is exactly what the caller is trying to establish.
    """

    if os.name != "nt":
        return Path(left) == Path(right)
    return _windows_compare_key(left) == _windows_compare_key(right)


def _get_long_path_name_w():
    """Return the lazily bound ``GetLongPathNameW`` function, or ``None``."""

    global _GET_LONG_PATH_NAME_W
    if os.name != "nt":
        return None
    if _GET_LONG_PATH_NAME_W is _LONG_PATH_NAME_UNAVAILABLE:
        return None
    if _GET_LONG_PATH_NAME_W is None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            function = kernel32.GetLongPathNameW
            function.argtypes = [
                ctypes.c_wchar_p,  # LPCWSTR
                ctypes.c_wchar_p,  # LPWSTR
                ctypes.c_uint32,  # DWORD
            ]
            function.restype = ctypes.c_uint32
        except (AttributeError, OSError, TypeError):
            _GET_LONG_PATH_NAME_W = _LONG_PATH_NAME_UNAVAILABLE
            return None
        _GET_LONG_PATH_NAME_W = function
    return _GET_LONG_PATH_NAME_W


def _windows_long_name(path: Path) -> tuple[str | None, int]:
    """Return ``(long_name, 0)`` or ``(None, winerror)``.

    This is only a long-name conversion helper.  It is never used as a
    reparse-point or filesystem-identity check.
    """

    function = _get_long_path_name_w()
    if function is None:
        return None, 0
    buffer = ctypes.create_unicode_buffer(_WINDOWS_LONG_PATH_CAPACITY)
    ctypes.set_last_error(0)
    try:
        written = int(function(str(path), buffer, _WINDOWS_LONG_PATH_CAPACITY))
    except (OSError, ValueError, ctypes.ArgumentError):
        return None, 0
    if written == 0:
        return None, ctypes.get_last_error()
    if written >= _WINDOWS_LONG_PATH_CAPACITY:
        return None, _ERROR_FILENAME_EXCED_RANGE
    return buffer.value, 0


def _canonical_alias_path(path: Path) -> Path | None:
    """Expand the existing prefix of ``path`` to its Windows long-name form.

    Missing suffixes are preserved.  Ascent is permitted only for
    ``ERROR_FILE_NOT_FOUND`` and ``ERROR_PATH_NOT_FOUND``; every other Win32
    error fails closed.
    """

    current = Path(path)
    suffix: list[str] = []
    while True:
        long_name, error = _windows_long_name(current)
        if long_name is not None:
            result = Path(long_name)
            for part in reversed(suffix):
                result = result / part
            return result
        if error not in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            return None
        parent = current.parent
        if parent == current:
            return None
        suffix.append(current.name)
        current = parent


def _root_paths_equivalent(declared: Path, resolved: Path) -> bool:
    """Return True when a root spelling is safe to equate with ``resolved``.

    Preconditions: the caller must have checked ``declared`` with
    :func:`find_link_component` before calling this helper, and must check the
    returned ``resolved`` path again before using it.  This helper is not a
    standalone security validator.

    Lexical equality is handled first.  On Windows, an alternate spelling is
    accepted only when the OS long-name expansion equals ``resolved`` and, for
    an existing root, ``samefile`` proves the same filesystem object.  A missing
    root has no object identity to prove and is accepted after the alias and
    link checks.  Any metadata or identity failure fails closed.
    """

    if paths_equivalent(declared, resolved):
        return True
    if os.name != "nt":
        return False
    try:
        alias = _canonical_alias_path(declared)
    except (OSError, ValueError):
        return False
    if alias is None or not paths_equivalent(alias, resolved):
        return False
    try:
        declared.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if is_link_like(declared):
        return False
    try:
        return os.path.samefile(declared, resolved)
    except OSError:
        return False


def find_link_component(path: Path) -> Path | None:
    """Return the first existing symlink/junction component of ``path``.

    The check walks components without resolving them, so a junction cannot
    hide behind ``Path.resolve()``.  A missing component does not end the walk:
    later components are still inspected, and only a link that actually exists
    can be returned.
    """

    path = Path(path)
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return current
        if is_link_like(current):
            return current
    return None


def is_within(child: Path, parent: Path) -> bool:
    """Return True when ``child`` is inside (or equal to) ``parent``."""

    try:
        child_resolved = child.resolve()
        parent_resolved = parent.resolve()
    except OSError:
        return False
    return child_resolved == parent_resolved or parent_resolved in child_resolved.parents


def resolve_file(
    root: Path, rel: str, location: str
) -> tuple[Path | None, list[Issue]]:
    """Resolve ``rel`` under ``root`` and confirm it is a regular, contained file.

    Every component is inspected with ``lstat`` so a symlink is reported as
    such instead of silently following it outside the project.
    """

    issues: list[Issue] = []
    path_issue = check_relative_path(rel)
    if path_issue is not None:
        return None, [error(path_issue.code, location, path_issue.message)]
    current = root
    for part in rel.split("/"):
        current = current / part
        try:
            status = current.lstat()
        except FileNotFoundError:
            issues.append(error("E_FILE_MISSING", location, f"file does not exist: {rel}"))
            return None, issues
        except OSError as exc:
            issues.append(error("E_FILE_UNREADABLE", location, f"cannot stat {rel}: {exc}"))
            return None, issues
        if stat.S_ISLNK(status.st_mode) or _is_link_like(current):
            issues.append(
                error("E_SYMLINK", location, f"symlinks are not allowed in pack paths: {rel}")
            )
            return None, issues
    try:
        status = current.lstat()
    except OSError as exc:
        issues.append(error("E_FILE_UNREADABLE", location, f"cannot stat {rel}: {exc}"))
        return None, issues
    if not stat.S_ISREG(status.st_mode):
        issues.append(error("E_NOT_A_FILE", location, f"not a regular file: {rel}"))
        return None, issues
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        issues.append(error("E_FILE_UNREADABLE", location, f"cannot resolve {rel}: {exc}"))
        return None, issues
    if not is_within(resolved, root.resolve()):
        issues.append(
            error("E_PATH_ESCAPE", location, f"path escapes the pack root: {rel}")
        )
        return None, issues
    return current, issues
