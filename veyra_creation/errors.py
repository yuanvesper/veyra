"""Error type used by the creation-project core.

The CLI turns these exceptions into a stable JSON envelope; library callers can
inspect ``code`` instead of matching message text.
"""

from __future__ import annotations

from typing import Any


class CreationError(Exception):
    """A deterministic, user-facing creation-project error."""

    def __init__(
        self,
        code: str,
        message: str,
        location: str = "project",
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.location = location
        self.details = details or []

    def format(self) -> str:
        if self.location:
            return f"{self.location}: [{self.code}] {self.message}"
        return f"[{self.code}] {self.message}"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result
