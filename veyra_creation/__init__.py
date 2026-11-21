"""Deterministic creation-project state for Veyra (Phase 3.2.1).

This package owns project state, the Spec/Plan schemas, the append-only decision
log, locks, runs and the derived Project Digest.  It intentionally contains no
model calls and no game-specific rules; those arrive in later phases.
"""

from __future__ import annotations

from .constants import (
    CREATION_SCHEMA_VERSION,
    CREATION_TOOL_NAME,
    CREATION_TOOL_VERSION,
)
from .digest import build_digest, verify_project
from .errors import CreationError
from .store import ProjectStore

__version__ = CREATION_TOOL_VERSION

__all__ = [
    "CREATION_SCHEMA_VERSION",
    "CREATION_TOOL_NAME",
    "CREATION_TOOL_VERSION",
    "CreationError",
    "ProjectStore",
    "build_digest",
    "verify_project",
    "__version__",
]
