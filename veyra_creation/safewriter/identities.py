"""Immutable identity and result-adjacent value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Identity:
    """Volume serial + 128-bit NTFS File ID.

    The File ID is stored as a 32-character lowercase hex string so it is
    canonical in JSON and logs.  Volume serial is the 64-bit value returned by
    ``FileIdInfo``.
    """

    volume_serial: int
    file_id: str

    def __post_init__(self) -> None:
        if self.volume_serial < 0 or self.volume_serial > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("volume_serial out of range")
        if len(self.file_id) != 32 or any(c not in "0123456789abcdef" for c in self.file_id):
            raise ValueError("file_id must be 32 lowercase hex characters")

    def as_dict(self) -> dict[str, Any]:
        return {"volume_serial": self.volume_serial, "file_id": self.file_id}


@dataclass(frozen=True)
class LevelIdentity:
    name: str
    volume_serial: int
    file_id: str
    is_reparse: bool = False

    @property
    def identity(self) -> Identity:
        return Identity(self.volume_serial, self.file_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "volume_serial": self.volume_serial,
            "file_id": self.file_id,
            "is_reparse": self.is_reparse,
        }


@dataclass(frozen=True)
class RootIdentity:
    volume_serial: int
    file_id: str
    volume_guid: str
    levels: tuple[LevelIdentity, ...]

    @property
    def identity(self) -> Identity:
        return Identity(self.volume_serial, self.file_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume_serial": self.volume_serial,
            "file_id": self.file_id,
            "volume_guid": self.volume_guid,
            "levels": [level.as_dict() for level in self.levels],
        }


@dataclass(frozen=True)
class TargetFacts:
    identity: Identity
    nlink: int
    size: int
    attributes: int
    sha256: str
    is_reparse: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.as_dict(),
            "nlink": self.nlink,
            "size": self.size,
            "attributes": self.attributes,
            "sha256": self.sha256,
            "is_reparse": self.is_reparse,
        }


@dataclass(frozen=True)
class ExpectedTarget:
    identity: Identity
    sha256: str

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")


@dataclass(frozen=True)
class RequestRef:
    batch_id: str
    op_id: str
    approval_ref: str
    plan_ref: str
    plan_sha256: str
    reason: str = ""

    def __post_init__(self) -> None:
        if len(self.plan_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.plan_sha256):
            raise ValueError("plan_sha256 must be 64 lowercase hex characters")
