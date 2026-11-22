"""Project-level single-writer lock (OS byte lock + owner token)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..canonical import canonical_json
from . import native
from .anchors import DirectoryAnchor, _close_quiet
from .errors import (
    E_ACCESS_DENIED,
    E_HARDLINK_REFUSED,
    E_IO,
    E_LOCK_CONFLICT,
    E_LOCK_OWNER,
    E_REPARSE_REFUSED,
    E_SHARING_VIOLATION,
    SafeWriterError,
)
from .identities import Identity

LOCK_NAME = "executor.lock"
_LOCK_ACCESS = native.GENERIC_READ | native.GENERIC_WRITE | native.FILE_READ_ATTRIBUTES | native.SYNCHRONIZE


@dataclass(frozen=True)
class OwnerInfo:
    owner_token: str
    pid: int
    batch_id: str
    root_identity: dict[str, Any]
    host: str
    tool_version: str
    acquired_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner_token": self.owner_token,
            "pid": self.pid,
            "batch_id": self.batch_id,
            "root_identity": self.root_identity,
            "host": self.host,
            "tool_version": self.tool_version,
            "acquired_at": self.acquired_at,
        }


@dataclass
class LockHandle:
    handle: int
    parent: DirectoryAnchor
    identity: Identity
    owner: OwnerInfo
    _closed: bool = False

    def release(self, *, suppress_errors: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        error: SafeWriterError | None = None
        current = None
        try:
            current, status = native.nt_open_relative(
                self.parent.handle,
                LOCK_NAME,
                desired_access=native.FILE_READ_ATTRIBUTES | native.SYNCHRONIZE,
                share_access=native.FILE_SHARE_READ | native.FILE_SHARE_WRITE,
                disposition=native.FILE_OPEN,
                options=native.FILE_NON_DIRECTORY_FILE,
                attributes=0,
            )
            if status < 0 or current is None:
                error = SafeWriterError(E_LOCK_OWNER, "lock file disappeared or cannot be reopened")
            else:
                serial, file_id = native.query_identity(current)
                if serial != self.identity.volume_serial or file_id != self.identity.file_id:
                    error = SafeWriterError(E_LOCK_CONFLICT, "lock file identity changed before release")
        except OSError as exc:
            error = SafeWriterError(E_LOCK_OWNER, f"cannot verify lock file before release: {exc}")
        finally:
            _close_quiet(current)
        try:
            native.unlock(self.handle)
        except OSError as exc:
            if error is None:
                error = SafeWriterError(E_LOCK_OWNER, f"UnlockFileEx failed: {exc}")
        finally:
            _close_quiet(self.handle)
        if error is not None and not suppress_errors:
            raise error


class LockManager:
    @staticmethod
    def acquire(parent: DirectoryAnchor, *, owner: OwnerInfo) -> LockHandle:
        handle, status = native.nt_open_relative(
            parent.handle,
            LOCK_NAME,
            desired_access=_LOCK_ACCESS,
            share_access=native.FILE_SHARE_READ,
            disposition=native.FILE_OPEN_IF,
            options=native.FILE_NON_DIRECTORY_FILE,
            attributes=native.FILE_ATTRIBUTE_NORMAL,
        )
        if status == native.STATUS_SHARING_VIOLATION:
            raise SafeWriterError(E_LOCK_CONFLICT, "lock file is held with an incompatible share mode")
        if status == native.STATUS_ACCESS_DENIED:
            raise SafeWriterError(E_ACCESS_DENIED, "access denied opening executor.lock")
        if status < 0 or handle is None:
            raise SafeWriterError(E_IO, f"cannot open executor.lock: {native.status_name(status)}")
        try:
            attrs, tag = native.query_attributes(handle)
            if native.is_reparse(attrs, tag):
                raise SafeWriterError(E_REPARSE_REFUSED, "executor.lock is a reparse point")
            nlink, _size, _attrs = native.query_info(handle)
            if nlink > 1:
                raise SafeWriterError(E_HARDLINK_REFUSED, "executor.lock has multiple hard links")
            serial, file_id = native.query_identity(handle)
            identity = Identity(serial, file_id)
            try:
                native.lock_exclusive(handle)
            except OSError as exc:
                if getattr(exc, "winerror", None) == native.ERROR_LOCK_VIOLATION:
                    raise SafeWriterError(E_LOCK_CONFLICT, "another process holds executor.lock") from exc
                raise SafeWriterError(E_LOCK_CONFLICT, f"LockFileEx failed: {exc}") from exc
            payload = canonical_json(owner.as_dict()).encode("utf-8")
            try:
                native.write_all(handle, payload, truncate=True)
                native.flush(handle)
            except OSError as exc:
                try:
                    native.unlock(handle)
                except OSError:
                    pass
                raise SafeWriterError(E_IO, f"cannot persist lock owner metadata: {exc}") from exc
            return LockHandle(handle=handle, parent=parent, identity=identity, owner=owner)
        except Exception:
            _close_quiet(handle)
            raise
