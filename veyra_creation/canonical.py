"""Canonical JSON, hashing and clock helpers.

Digest identity must not depend on dictionary insertion order, whitespace or
the current time.  Everything that participates in ``digest_id`` is serialised
through :func:`canonical_json`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def canonical_json(value: Any) -> str:
    """Return the canonical UTF-8 JSON text used for hashing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def pretty_json(value: Any) -> str:
    """Return stable, human-readable JSON with a trailing newline."""

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


Clock = Callable[[], datetime | str]


def now_iso(clock: Clock | None = None) -> str:
    """Return an ISO-8601 UTC timestamp ending in ``Z``.

    Tests can inject a clock returning either a ``datetime`` or an already
    formatted string.
    """

    value = clock() if clock is not None else datetime.now(timezone.utc)
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")
