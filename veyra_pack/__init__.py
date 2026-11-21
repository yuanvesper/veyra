"""Veyra creation Pack contract and offline lifecycle.

A *pack* is a versioned, game-agnostic creation project described by a
``pack.json`` manifest.  This package provides three offline-only commands:

``init``
    create a new pack project skeleton,
``validate``
    check the manifest and the files it references,
``export``
    atomically zip a valid pack.

The package has no third-party dependencies, performs no network access and
never calls a language model.  It does not import or modify
``triage_agent.py``; crash triage keeps its own contract (``schema_version``
``1.0.0`` of the report JSON), and the pack contract below is versioned
independently.
"""

from .contract import (
    PACK_FILENAME,
    PACK_TOOL_VERSION,
    PACK_TYPES,
    SCHEMA_VERSION,
)

__all__ = [
    "PACK_FILENAME",
    "PACK_TOOL_VERSION",
    "PACK_TYPES",
    "SCHEMA_VERSION",
]
