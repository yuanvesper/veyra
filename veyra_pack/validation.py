"""Validation of a Veyra pack against contract 1.0.

``validate_pack`` is deliberately read-only: it never rewrites ``pack.json``
and never creates files.  Every diagnostic carries a stable code, a JSON
pointer-like location (``pack.json#/assets/0/path``) and a human readable
message so that both a CLI user and a future agent can act on it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .contract import (
    ASSET_ID_RE,
    ASSET_KINDS,
    MAX_PATH_LENGTH,
    PACK_FILENAME,
    PACK_ID_RE,
    PACK_TYPES,
    REQUIRED_ASSET_FIELDS,
    REQUIRED_FILE_FIELDS,
    REQUIRED_TOP_LEVEL,
    SHA256_RE,
    SUPPORTED_SCHEMA_MAJOR,
    SUPPORTED_SCHEMA_MINOR,
    TOOL_FIELDS,
    VERSION_RE,
    Issue,
    Limits,
    PackError,
    ValidationResult,
    error,
    iter_unknown_keys,
    warning,
)
from .imaging import IMAGE_EXTENSIONS, read_image_size
from .paths import check_relative_path, resolve_file

ASSET_FIELDS = (
    "id",
    "path",
    "kind",
    "sha256",
    "size_bytes",
    "width",
    "height",
    "license",
    "description",
)
FILE_FIELDS = ("path", "sha256", "size_bytes")
LICENSE_FIELDS = ("spdx", "source", "note")


def load_manifest(
    root: Path, limits: Limits
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Read and parse ``pack.json``; syntactic problems become diagnostics."""

    issues: list[Issue] = []
    pack_path = root / PACK_FILENAME
    if not pack_path.is_file():
        raise PackError(f"missing {PACK_FILENAME} in {root}", "E_MANIFEST_MISSING")
    try:
        raw = pack_path.read_bytes()
    except OSError as exc:
        raise PackError(f"cannot read {PACK_FILENAME}: {exc}", "E_MANIFEST_UNREADABLE")
    if len(raw) > limits.max_pack_json_bytes:
        issues.append(
            error(
                "E_MANIFEST_TOO_LARGE",
                PACK_FILENAME,
                f"{PACK_FILENAME} exceeds {limits.max_pack_json_bytes} bytes",
            )
        )
        return None, issues
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(
            error(
                "E_JSON_ENCODING",
                PACK_FILENAME,
                f"{PACK_FILENAME} is not valid UTF-8 (byte {exc.start})",
            )
        )
        return None, issues
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        issues.append(
            error(
                "E_JSON_PARSE",
                f"{PACK_FILENAME}:{exc.lineno}:{exc.colno}",
                f"invalid JSON: {exc.msg}",
            )
        )
        return None, issues
    if not isinstance(data, dict):
        issues.append(
            error("E_JSON_ROOT", PACK_FILENAME, "pack.json must contain a JSON object")
        )
        return None, issues
    return data, issues


def validate_pack(root: str | Path, limits: Limits | None = None) -> ValidationResult:
    """Validate the pack rooted at ``root`` and return every diagnostic found."""

    limits = limits or Limits()
    root = Path(root)
    if not root.exists():
        raise PackError(f"pack root does not exist: {root}", "E_ROOT_MISSING")
    if not root.is_dir():
        raise PackError(f"pack root is not a directory: {root}", "E_ROOT_NOT_DIR")

    data, issues = load_manifest(root, limits)
    result = ValidationResult(root=root, data=data, issues=issues)
    if data is None:
        return result

    _check_top_level(data, issues)
    file_sizes, resolved = _check_files(data, root, limits, issues)
    assets = data.get("assets")
    if isinstance(assets, list):
        _check_assets(
            assets,
            root=root,
            resolved=resolved,
            files_sizes=file_sizes,
            issues=issues,
            limits=limits,
        )
    _check_limits(resolved, file_sizes, limits, issues)

    result.resolved_files = resolved
    # De-duplicate while keeping the first occurrence order stable.
    seen: set[tuple[str, str, str]] = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (issue.severity, issue.code, issue.location)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    result.issues = unique
    return result


# ---------------------------------------------------------------------------
# Top level


def _check_top_level(data: dict[str, Any], issues: list[Issue]) -> None:
    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            issues.append(
                error("E_FIELD_MISSING", f"pack.json#/{key}", f"required field '{key}' is missing")
            )
    for key in iter_unknown_keys(data, REQUIRED_TOP_LEVEL):
        issues.append(
            warning(
                "W_UNKNOWN_FIELD",
                f"pack.json#/{key}",
                f"unknown top-level field '{key}' is preserved but not interpreted",
            )
        )

    schema = data.get("schema_version")
    if isinstance(schema, str):
        match = VERSION_RE.match(schema)
        if not match:
            issues.append(
                error(
                    "E_SCHEMA_VERSION",
                    "pack.json#/schema_version",
                    f"'{schema}' is not a valid version (expected MAJOR.MINOR.PATCH)",
                )
            )
        else:
            major, minor = int(match.group(1)), int(match.group(2))
            if major != SUPPORTED_SCHEMA_MAJOR or minor > SUPPORTED_SCHEMA_MINOR:
                issues.append(
                    error(
                        "E_SCHEMA_UNSUPPORTED",
                        "pack.json#/schema_version",
                        f"schema {schema} is not supported; this tool implements "
                        f"{SUPPORTED_SCHEMA_MAJOR}.{SUPPORTED_SCHEMA_MINOR}.x",
                    )
                )
    elif "schema_version" in data:
        issues.append(
            error(
                "E_SCHEMA_TYPE",
                "pack.json#/schema_version",
                "schema_version must be a string",
            )
        )

    _check_required_string(data, "pack_id", "pack.json#/pack_id", issues, PACK_ID_RE)
    _check_required_string(data, "name", "pack.json#/name", issues, None, max_length=200)
    _check_required_string(data, "version", "pack.json#/version", issues, VERSION_RE)

    tool = data.get("tool")
    if isinstance(tool, dict):
        for key in TOOL_FIELDS:
            value = tool.get(key)
            if key not in tool:
                issues.append(
                    error(
                        "E_FIELD_MISSING",
                        f"pack.json#/tool/{key}",
                        f"required field 'tool.{key}' is missing",
                    )
                )
            elif not isinstance(value, str) or not value.strip():
                issues.append(
                    error(
                        "E_FIELD_TYPE",
                        f"pack.json#/tool/{key}",
                        f"tool.{key} must be a non-empty string",
                    )
                )
        for key in iter_unknown_keys(tool, TOOL_FIELDS):
            issues.append(
                warning(
                    "W_UNKNOWN_FIELD",
                    f"pack.json#/tool/{key}",
                    f"unknown tool field '{key}' is preserved but not interpreted",
                )
            )
    elif "tool" in data:
        issues.append(error("E_FIELD_TYPE", "pack.json#/tool", "tool must be an object"))

    pack_type = data.get("pack_type")
    if "pack_type" in data:
        if not isinstance(pack_type, str):
            issues.append(error("E_FIELD_TYPE", "pack.json#/pack_type", "pack_type must be a string"))
        elif pack_type not in PACK_TYPES:
            issues.append(
                error(
                    "E_PACK_TYPE",
                    "pack.json#/pack_type",
                    f"unknown pack_type '{pack_type}'; expected one of: {', '.join(PACK_TYPES)}",
                )
            )

    description = data.get("description")
    if "description" in data:
        if not isinstance(description, str):
            issues.append(
                error("E_FIELD_TYPE", "pack.json#/description", "description must be a string")
            )
        elif len(description) > 20000:
            issues.append(
                error(
                    "E_DESCRIPTION_TOO_LONG",
                    "pack.json#/description",
                    "description exceeds 20000 characters",
                )
            )

    for optional in ("target_game", "author"):
        value = data.get(optional)
        if value is not None:
            if not isinstance(value, str):
                issues.append(
                    error(
                        "E_FIELD_TYPE",
                        f"pack.json#/{optional}",
                        f"{optional} must be a string or null",
                    )
                )
            elif len(value) > 200:
                issues.append(
                    error(
                        "E_FIELD_TOO_LONG",
                        f"pack.json#/{optional}",
                        f"{optional} exceeds 200 characters",
                    )
                )

    for key in ("assets", "files"):
        if key in data and not isinstance(data[key], list):
            issues.append(
                error("E_FIELD_TYPE", f"pack.json#/{key}", f"{key} must be an array")
            )
    if "metadata" in data and not isinstance(data["metadata"], dict):
        issues.append(
            error("E_FIELD_TYPE", "pack.json#/metadata", "metadata must be an object")
        )


def _check_required_string(
    data: dict[str, Any],
    key: str,
    location: str,
    issues: list[Issue],
    pattern: re.Pattern[str] | None,
    max_length: int = MAX_PATH_LENGTH,
) -> None:
    if key not in data:
        return
    value = data[key]
    if not isinstance(value, str):
        issues.append(error("E_FIELD_TYPE", location, f"{key} must be a string"))
        return
    if not value.strip():
        issues.append(error("E_FIELD_EMPTY", location, f"{key} must not be empty"))
        return
    if len(value) > max_length:
        issues.append(
            error("E_FIELD_TOO_LONG", location, f"{key} exceeds {max_length} characters")
        )
    if pattern is not None and not pattern.match(value):
        issues.append(
            error(
                "E_FIELD_FORMAT",
                location,
                f"{key} '{value}' does not match {pattern.pattern}",
            )
        )


# ---------------------------------------------------------------------------
# files[] and assets[]


def _check_files(
    data: dict[str, Any],
    root: Path,
    limits: Limits,
    issues: list[Issue],
) -> tuple[dict[str, int], dict[str, Path]]:
    files = data.get("files")
    if not isinstance(files, list):
        return {}, {}
    sizes: dict[str, int] = {}
    resolved: dict[str, Path] = {}
    seen_paths: dict[str, str] = {}
    for index, entry in enumerate(files):
        location = f"pack.json#/files/{index}"
        if not isinstance(entry, dict):
            issues.append(error("E_ENTRY_TYPE", location, "files[] entries must be objects"))
            continue
        for key in iter_unknown_keys(entry, FILE_FIELDS):
            issues.append(
                warning(
                    "W_UNKNOWN_FIELD",
                    f"{location}/{key}",
                    f"unknown file field '{key}' is preserved but not interpreted",
                )
            )
        for key in REQUIRED_FILE_FIELDS:
            if key not in entry:
                issues.append(
                    error("E_FIELD_MISSING", f"{location}/{key}", f"required field '{key}' is missing")
                )
        rel = entry.get("path")
        problem = check_relative_path(rel)
        if problem is not None:
            issues.append(_relocate(problem, f"{location}/path"))
            continue
        assert isinstance(rel, str)
        if rel == PACK_FILENAME:
            issues.append(
                error("E_RESERVED_PATH", f"{location}/path", f"'{PACK_FILENAME}' is added automatically")
            )
            continue
        lowered = rel.casefold()
        if lowered in seen_paths:
            issues.append(
                error(
                    "E_DUPLICATE_FILE_PATH",
                    f"{location}/path",
                    f"duplicate file path '{rel}' (also at {seen_paths[lowered]})",
                )
            )
            continue
        seen_paths[lowered] = f"{location}/path"

        path, path_issues = resolve_file(root, rel, f"{location}/path")
        issues.extend(path_issues)
        if path is None:
            continue
        resolved[rel] = path
        size = path.stat().st_size
        sizes[rel] = size
        _check_declared_digest(entry, path, size, f"{location}", issues)
    return sizes, resolved


def _check_assets(
    assets: list[Any],
    root: Path,
    resolved: dict[str, Path],
    files_sizes: dict[str, int],
    issues: list[Issue],
    limits: Limits,
) -> None:
    seen_ids: dict[str, str] = {}
    seen_paths: dict[str, str] = {}
    for index, entry in enumerate(assets):
        location = f"pack.json#/assets/{index}"
        if not isinstance(entry, dict):
            issues.append(error("E_ENTRY_TYPE", location, "assets[] entries must be objects"))
            continue
        for key in iter_unknown_keys(entry, ASSET_FIELDS):
            issues.append(
                warning(
                    "W_UNKNOWN_FIELD",
                    f"{location}/{key}",
                    f"unknown asset field '{key}' is preserved but not interpreted",
                )
            )
        for key in REQUIRED_ASSET_FIELDS:
            if key not in entry:
                issues.append(
                    error("E_FIELD_MISSING", f"{location}/{key}", f"required field '{key}' is missing")
                )

        asset_id = entry.get("id")
        if isinstance(asset_id, str):
            if not ASSET_ID_RE.match(asset_id):
                issues.append(
                    error(
                        "E_ASSET_ID",
                        f"{location}/id",
                        "asset id must match ^[a-z0-9][a-z0-9._-]{0,63}$",
                    )
                )
            elif asset_id.casefold() in seen_ids:
                issues.append(
                    error(
                        "E_DUPLICATE_ASSET_ID",
                        f"{location}/id",
                        f"duplicate asset id '{asset_id}' (also at {seen_ids[asset_id.casefold()]})",
                    )
                )
            else:
                seen_ids[asset_id.casefold()] = f"{location}/id"
        elif "id" in entry:
            issues.append(error("E_FIELD_TYPE", f"{location}/id", "asset id must be a string"))

        kind = entry.get("kind")
        if "kind" in entry:
            if not isinstance(kind, str):
                issues.append(error("E_FIELD_TYPE", f"{location}/kind", "asset kind must be a string"))
            elif kind not in ASSET_KINDS:
                issues.append(
                    error(
                        "E_ASSET_KIND",
                        f"{location}/kind",
                        f"unknown asset kind '{kind}'; expected one of: {', '.join(ASSET_KINDS)}",
                    )
                )

        rel = entry.get("path")
        problem = check_relative_path(rel)
        if problem is not None:
            issues.append(_relocate(problem, f"{location}/path"))
            continue
        assert isinstance(rel, str)
        if rel == PACK_FILENAME:
            issues.append(
                error("E_RESERVED_PATH", f"{location}/path", f"'{PACK_FILENAME}' is added automatically")
            )
            continue
        lowered = rel.casefold()
        if lowered in seen_paths:
            issues.append(
                error(
                    "E_DUPLICATE_ASSET_PATH",
                    f"{location}/path",
                    f"duplicate asset path '{rel}' (also at {seen_paths[lowered]})",
                )
            )
            continue
        seen_paths[lowered] = f"{location}/path"

        path = resolved.get(rel)
        if path is None:
            path, path_issues = resolve_file(root, rel, f"{location}/path")
            issues.extend(path_issues)
            if path is None:
                continue
            resolved[rel] = path
            files_sizes[rel] = path.stat().st_size
        width = entry.get("width")
        height = entry.get("height")
        _check_declared_digest(entry, path, path.stat().st_size, location, issues)

        _check_dimensions(entry, path, width, height, kind, location, issues, limits)
        _check_license(entry, location, issues)


def _check_declared_digest(
    entry: dict[str, Any], path: Path, size: int, location: str, issues: list[Issue]
) -> None:
    declared_size = entry.get("size_bytes")
    if declared_size is not None:
        if isinstance(declared_size, bool) or not isinstance(declared_size, int):
            issues.append(
                error("E_FIELD_TYPE", f"{location}/size_bytes", "size_bytes must be an integer")
            )
        elif declared_size != size:
            issues.append(
                error(
                    "E_SIZE_MISMATCH",
                    f"{location}/size_bytes",
                    f"declared {declared_size} bytes but the file has {size} bytes",
                )
            )
    declared_hash = entry.get("sha256")
    if declared_hash is not None:
        if not isinstance(declared_hash, str) or not SHA256_RE.match(declared_hash.lower()):
            issues.append(
                error(
                    "E_FIELD_FORMAT",
                    f"{location}/sha256",
                    "sha256 must be 64 hexadecimal characters",
                )
            )
        else:
            actual = _sha256(path)
            if actual != declared_hash.lower():
                issues.append(
                    error(
                        "E_HASH_MISMATCH",
                        f"{location}/sha256",
                        f"declared {declared_hash.lower()} but the file has {actual}",
                    )
                )
    else:
        issues.append(
            warning(
                "W_HASH_MISSING",
                f"{location}/sha256",
                "no sha256 declared; integrity is checked only by size and path",
            )
        )


def _check_dimensions(
    entry: dict[str, Any],
    path: Path,
    width: Any,
    height: Any,
    kind: Any,
    location: str,
    issues: list[Issue],
    limits: Limits,
) -> None:
    if width is not None and (isinstance(width, bool) or not isinstance(width, int) or width <= 0):
        issues.append(error("E_FIELD_TYPE", f"{location}/width", "width must be a positive integer"))
        width = None
    if height is not None and (
        isinstance(height, bool) or not isinstance(height, int) or height <= 0
    ):
        issues.append(
            error("E_FIELD_TYPE", f"{location}/height", "height must be a positive integer")
        )
        height = None
    if (width is None) != (height is None):
        issues.append(
            error(
                "E_IMAGE_DIMENSION_PARTIAL",
                location,
                "width and height must be declared together",
            )
        )
        return

    is_image = kind == "image" or path.suffix.lower() in IMAGE_EXTENSIONS
    if not is_image:
        return
    actual = read_image_size(path)
    if actual is None:
        if width is not None:
            issues.append(
                error(
                    "E_IMAGE_UNREADABLE",
                    location,
                    "width/height are declared but the image header cannot be read",
                )
            )
        else:
            issues.append(
                warning(
                    "W_IMAGE_DIMENSION_UNKNOWN",
                    location,
                    "image header is not recognised; dimensions are not verified",
                )
            )
        return
    actual_width, actual_height = actual
    if width is not None and (width, height) != actual:
        issues.append(
            error(
                "E_IMAGE_DIMENSION_MISMATCH",
                location,
                f"declared {width}x{height} but the image is {actual_width}x{actual_height}",
            )
        )
    if (
        actual_width > limits.max_image_dimension
        or actual_height > limits.max_image_dimension
        or actual_width * actual_height > limits.max_image_pixels
    ):
        issues.append(
            error(
                "E_IMAGE_TOO_LARGE",
                location,
                f"image is {actual_width}x{actual_height}; limits are "
                f"{limits.max_image_dimension}px per side and {limits.max_image_pixels} pixels",
            )
        )


def _check_license(entry: dict[str, Any], location: str, issues: list[Issue]) -> None:
    license_data = entry.get("license")
    if license_data is None:
        issues.append(
            warning(
                "W_LICENSE_MISSING",
                f"{location}/license",
                "no license/source declaration; add one before sharing the pack",
            )
        )
        return
    if not isinstance(license_data, dict):
        issues.append(
            error("E_FIELD_TYPE", f"{location}/license", "license must be an object")
        )
        return
    for key in iter_unknown_keys(license_data, LICENSE_FIELDS):
        issues.append(
            warning(
                "W_UNKNOWN_FIELD",
                f"{location}/license/{key}",
                f"unknown license field '{key}' is preserved but not interpreted",
            )
        )
    for key in LICENSE_FIELDS:
        if key in license_data and not isinstance(license_data[key], str):
            issues.append(
                error(
                    "E_FIELD_TYPE",
                    f"{location}/license/{key}",
                    f"license.{key} must be a string",
                )
            )


# ---------------------------------------------------------------------------
# Limits and helpers


def _check_limits(
    resolved: dict[str, Path],
    sizes: dict[str, int],
    limits: Limits,
    issues: list[Issue],
) -> None:
    if len(resolved) > limits.max_files:
        issues.append(
            error(
                "E_TOO_MANY_FILES",
                "pack.json#/files",
                f"{len(resolved)} files exceed the limit of {limits.max_files}",
            )
        )
    total = 0
    for rel, size in sizes.items():
        total += size
        if size > limits.max_file_bytes:
            issues.append(
                error(
                    "E_FILE_TOO_LARGE",
                    f"file:{rel}",
                    f"{size} bytes exceed the per-file limit of {limits.max_file_bytes}",
                )
            )
    if total > limits.max_total_bytes:
        issues.append(
            error(
                "E_TOTAL_TOO_LARGE",
                "pack.json#/files",
                f"{total} bytes exceed the total limit of {limits.max_total_bytes}",
            )
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relocate(issue: Issue, location: str) -> Issue:
    return Issue(code=issue.code, location=location, message=issue.message, severity=issue.severity)
