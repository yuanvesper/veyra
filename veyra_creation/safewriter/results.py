"""JSON-friendly result objects returned by SafeWriter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

COMPLETED = "COMPLETED"
FAILED = "FAILED"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
PARTIAL = "PARTIAL"
REFUSED = "REFUSED"
UNCERTAIN = "UNCERTAIN"

STATUSES = frozenset({COMPLETED, FAILED, RECOVERY_REQUIRED, PARTIAL, REFUSED, UNCERTAIN})


@dataclass(frozen=True)
class WriteResult:
    status: str
    operation_id: str
    action: str
    code: str | None = None
    message: str = ""
    identity: dict[str, Any] | None = None
    sha256: str | None = None
    size: int | None = None
    temp_name: str | None = None
    quarantine_name: str | None = None
    journal_path: str | None = None
    durable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown WriteResult status: {self.status}")

    @property
    def ok(self) -> bool:
        return self.status == COMPLETED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operation_id": self.operation_id,
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "identity": self.identity,
            "sha256": self.sha256,
            "size": self.size,
            "temp_name": self.temp_name,
            "quarantine_name": self.quarantine_name,
            "journal_path": self.journal_path,
            "durable": self.durable,
            "details": self.details,
        }
