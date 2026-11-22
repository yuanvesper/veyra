"""Production-shaped SafeWriter prototype (Phase 3.2.2.1-2.4).

Only ``create`` and ``replace`` are implemented.  ``delete`` and ``recover``
return ``E_NOT_IMPLEMENTED``.  This package is not wired into ProjectStore or
the CLI yet.
"""

from .errors import SafeWriterError
from .identities import ExpectedTarget, Identity, LevelIdentity, RequestRef, RootIdentity, TargetFacts
from .results import (
    COMPLETED,
    FAILED,
    PARTIAL,
    RECOVERY_REQUIRED,
    REFUSED,
    UNCERTAIN,
    WriteResult,
)
from .writer import CreateRequest, ReplaceRequest, SafeWriter

__all__ = [
    "COMPLETED",
    "CreateRequest",
    "ExpectedTarget",
    "FAILED",
    "Identity",
    "LevelIdentity",
    "PARTIAL",
    "RECOVERY_REQUIRED",
    "REFUSED",
    "ReplaceRequest",
    "RequestRef",
    "RootIdentity",
    "SafeWriter",
    "SafeWriterError",
    "TargetFacts",
    "UNCERTAIN",
    "WriteResult",
]
