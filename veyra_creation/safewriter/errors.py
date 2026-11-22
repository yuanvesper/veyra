"""Stable error codes and exception type for the strict SafeWriter path.

The codes in this module are part of the public contract.  They are deliberately
short strings so they can be embedded in JSON results and CLI exit mappings
without a second translation table.
"""

from __future__ import annotations

from typing import Any


E_ROOT_CHANGED = "E_ROOT_CHANGED"
E_REPARSE_REFUSED = "E_REPARSE_REFUSED"
E_REPARSE_UNKNOWN = "E_REPARSE_UNKNOWN"
E_TARGET_CHANGED = "E_TARGET_CHANGED"
E_TARGET_EXISTS = "E_TARGET_EXISTS"
E_TARGET_ABSENT = "E_TARGET_ABSENT"
E_TARGET_UNSUPPORTED = "E_TARGET_UNSUPPORTED"
E_HARDLINK_REFUSED = "E_HARDLINK_REFUSED"
E_LINK_COUNT_CHANGED = "E_LINK_COUNT_CHANGED"
E_LINK_COUNT_UNAVAILABLE = "E_LINK_COUNT_UNAVAILABLE"
E_PATH_INVALID = "E_PATH_INVALID"
E_IDENTITY_UNAVAILABLE = "E_IDENTITY_UNAVAILABLE"
E_ANCESTOR_CHANGED = "E_ANCESTOR_CHANGED"
E_LOCK_CONFLICT = "E_LOCK_CONFLICT"
E_LOCK_OWNER = "E_LOCK_OWNER"
E_JOURNAL_CORRUPT = "E_JOURNAL_CORRUPT"
E_JOURNAL_WRITE_FAILED = "E_JOURNAL_WRITE_FAILED"
E_BACKUP_INVALID = "E_BACKUP_INVALID"
E_BACKUP_WRITE_FAILED = "E_BACKUP_WRITE_FAILED"
E_RECOVERY_REFUSED = "E_RECOVERY_REFUSED"
E_COMMIT_UNCERTAIN = "E_COMMIT_UNCERTAIN"
E_STRICT_BACKEND_UNAVAILABLE = "E_STRICT_BACKEND_UNAVAILABLE"
E_FS_UNSUPPORTED = "E_FS_UNSUPPORTED"
E_SHARING_VIOLATION = "E_SHARING_VIOLATION"
E_ACCESS_DENIED = "E_ACCESS_DENIED"
E_IO = "E_IO"
E_OPERATION_INVALID = "E_OPERATION_INVALID"
E_RESOURCE_LIMIT = "E_RESOURCE_LIMIT"
E_NOT_IMPLEMENTED = "E_NOT_IMPLEMENTED"

ERROR_CODES = frozenset(
    {
        E_ROOT_CHANGED,
        E_REPARSE_REFUSED,
        E_REPARSE_UNKNOWN,
        E_TARGET_CHANGED,
        E_TARGET_EXISTS,
        E_TARGET_ABSENT,
        E_TARGET_UNSUPPORTED,
        E_HARDLINK_REFUSED,
        E_LINK_COUNT_CHANGED,
        E_LINK_COUNT_UNAVAILABLE,
        E_PATH_INVALID,
        E_IDENTITY_UNAVAILABLE,
        E_ANCESTOR_CHANGED,
        E_LOCK_CONFLICT,
        E_LOCK_OWNER,
        E_JOURNAL_CORRUPT,
        E_JOURNAL_WRITE_FAILED,
        E_BACKUP_INVALID,
        E_BACKUP_WRITE_FAILED,
        E_RECOVERY_REFUSED,
        E_COMMIT_UNCERTAIN,
        E_STRICT_BACKEND_UNAVAILABLE,
        E_FS_UNSUPPORTED,
        E_SHARING_VIOLATION,
        E_ACCESS_DENIED,
        E_IO,
        E_OPERATION_INVALID,
        E_RESOURCE_LIMIT,
        E_NOT_IMPLEMENTED,
    }
)


class SafeWriterError(Exception):
    """A deterministic, non-retryable SafeWriter failure.

    ``details`` may contain identities/hashes, but never file contents.  The
    public result layer converts this exception into a ``WriteResult``.
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown SafeWriter error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
