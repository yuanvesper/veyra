"""Creation of a new, empty pack project.

``init`` never overwrites an existing pack: the destination must be absent or
an empty directory, and the manifest is written with an exclusive create.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from .contract import (
    PACK_FILENAME,
    PACK_TOOL_NAME,
    PACK_TOOL_VERSION,
    PACK_TYPES,
    SCHEMA_VERSION,
    PackError,
    write_json_atomic,
)

DEFAULT_VERSION = "0.1.0"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "pack")[:48]


def generate_pack_id(name: str) -> str:
    """Return a readable, collision-resistant id such as ``my-kit-1a2b3c4d``."""

    return f"{slugify(name)}-{uuid.uuid4().hex[:8]}"


def default_manifest(
    *,
    pack_id: str,
    name: str,
    pack_type: str,
    description: str = "",
    target_game: str | None = None,
    author: str | None = None,
    version: str = DEFAULT_VERSION,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": PACK_TOOL_NAME, "version": PACK_TOOL_VERSION},
        "pack_id": pack_id,
        "name": name,
        "description": description,
        "pack_type": pack_type,
        "target_game": target_game,
        "version": version,
        "author": author,
        "assets": [],
        "files": [],
        "metadata": {"generated_by": f"veyra-pack {PACK_TOOL_VERSION}"},
    }


def init_pack(
    destination: str | Path,
    *,
    name: str,
    pack_type: str,
    description: str = "",
    target_game: str | None = None,
    author: str | None = None,
    pack_id: str | None = None,
    version: str = DEFAULT_VERSION,
) -> Path:
    """Create the project skeleton and return the manifest path."""

    if not isinstance(name, str) or not name.strip():
        raise PackError("--name must not be empty", "E_NAME")
    if pack_type not in PACK_TYPES:
        raise PackError(
            f"unknown --type '{pack_type}'; expected one of: {', '.join(PACK_TYPES)}",
            "E_PACK_TYPE",
        )

    destination = Path(destination)
    if destination.exists() and not destination.is_dir():
        raise PackError(f"destination is not a directory: {destination}", "E_DEST_NOT_DIR")
    if destination.exists() and any(destination.iterdir()):
        raise PackError(
            f"destination is not empty: {destination}", "E_DEST_NOT_EMPTY"
        )

    manifest = default_manifest(
        pack_id=pack_id or generate_pack_id(name),
        name=name.strip(),
        pack_type=pack_type,
        description=description,
        target_game=target_game,
        author=author,
        version=version,
    )

    destination.mkdir(parents=True, exist_ok=True)
    (destination / "assets").mkdir(exist_ok=True)
    (destination / "files").mkdir(exist_ok=True)
    pack_path = destination / PACK_FILENAME
    if pack_path.exists():
        raise PackError(f"{PACK_FILENAME} already exists: {pack_path}", "E_EXISTS")
    write_json_atomic(pack_path, manifest)
    return pack_path
