"""Durable NDJSON operation journal.

Each append is one canonical JSON line followed by ``\n`` and an explicit
``FlushFileBuffers``.  The previous raw line hash is carried in the next event,
so a deleted/reordered/edited middle line is detectable.  A trailing partial
line is corruption; the journal never guesses or repairs silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..canonical import Clock, canonical_json, now_iso
from . import native
from .anchors import DirectoryAnchor, _close_quiet, _fail_status
from .errors import (
    E_HARDLINK_REFUSED,
    E_IO,
    E_JOURNAL_CORRUPT,
    E_JOURNAL_WRITE_FAILED,
    E_PATH_INVALID,
    E_REPARSE_REFUSED,
    SafeWriterError,
)
from .identities import Identity

JOURNAL_SCHEMA_VERSION = 1
MAX_JOURNAL_LINE_BYTES = 64 * 1024
_REQUIRED = ("schema_version", "seq", "batch_id", "op_id", "state", "at", "action", "prev_event_sha256")


@dataclass(frozen=True)
class JournalView:
    events: tuple[dict[str, Any], ...]
    size: int
    identity: Identity
    last_line_sha256: str | None


@dataclass
class OperationJournal:
    handle: int
    parent: DirectoryAnchor
    batch_id: str
    identity: Identity
    clock: Clock | None
    events: list[dict[str, Any]] = field(default_factory=list)
    last_line_sha256: str | None = None
    size: int = 0
    _closed: bool = False

    @classmethod
    def open(cls, parent: DirectoryAnchor, batch_id: str, *, clock: Clock | None = None) -> "OperationJournal":
        name = f"{batch_id}.journal.ndjson"
        handle, status = native.nt_open_relative(
            parent.handle,
            name,
            desired_access=(
                native.GENERIC_READ
                | native.GENERIC_WRITE
                | native.FILE_READ_ATTRIBUTES
                | native.SYNCHRONIZE
            ),
            share_access=native.FILE_SHARE_READ,
            disposition=native.FILE_OPEN_IF,
            options=native.FILE_NON_DIRECTORY_FILE,
            attributes=native.FILE_ATTRIBUTE_NORMAL,
        )
        if status < 0 or handle is None:
            _fail_status(status, f"open journal {name!r}")
        assert handle is not None
        try:
            attrs, tag = native.query_attributes(handle)
            if native.is_reparse(attrs, tag):
                raise SafeWriterError(E_REPARSE_REFUSED, f"journal {name!r} is a reparse point")
            nlink, size, _attrs = native.query_info(handle)
            if nlink > 1:
                raise SafeWriterError(E_HARDLINK_REFUSED, f"journal {name!r} has multiple hard links")
            serial, file_id = native.query_identity(handle)
            data = native.read_all(handle)
            repaired_bytes = 0
            try:
                events, last_sha = _parse_journal(data, batch_id=batch_id)
            except SafeWriterError as exc:
                if exc.code != E_JOURNAL_CORRUPT or data.endswith(b"\n"):
                    raise
                cut = data.rfind(b"\n") + 1
                prefix = data[:cut]
                events, last_sha = _parse_journal(prefix, batch_id=batch_id)
                repaired_bytes = len(data) - cut
                try:
                    native.set_pointer(handle, cut)
                    native.set_end_of_file(handle)
                    native.flush(handle)
                except OSError as repair_exc:
                    raise SafeWriterError(
                        E_JOURNAL_WRITE_FAILED,
                        f"cannot truncate partial journal tail: {repair_exc}",
                    ) from repair_exc
                size = cut
            journal = cls(
                handle=handle,
                parent=parent,
                batch_id=batch_id,
                identity=Identity(serial, file_id),
                clock=clock,
                events=events,
                last_line_sha256=last_sha,
                size=size,
            )
            if repaired_bytes:
                journal.append(
                    op_id="journal-repair",
                    state="JOURNAL_TAIL_REPAIRED",
                    action="TRUNCATE_PARTIAL_TAIL",
                    repaired_bytes=repaired_bytes,
                )
            else:
                native.set_pointer(handle, size)
            return journal
        except SafeWriterError:
            _close_quiet(handle)
            raise
        except OSError as exc:
            _close_quiet(handle)
            raise SafeWriterError(E_IO, f"cannot open journal {name!r}: {exc}") from exc
        except Exception:
            _close_quiet(handle)
            raise

    @property
    def next_seq(self) -> int:
        return len(self.events) + 1

    def append(self, *, op_id: str, state: str, action: str, **fields: Any) -> dict[str, Any]:
        if self._closed:
            raise SafeWriterError(E_JOURNAL_CORRUPT, "journal is closed")
        event: dict[str, Any] = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "seq": self.next_seq,
            "batch_id": self.batch_id,
            "op_id": op_id,
            "state": state,
            "at": now_iso(self.clock),
            "action": action,
            "prev_event_sha256": self.last_line_sha256,
        }
        for key, value in fields.items():
            if key in event:
                raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal field {key!r} is reserved")
            event[key] = value
        try:
            line = canonical_json(event).encode("utf-8")
        except UnicodeEncodeError as exc:
            # Path/request policy rejects surrogate code points before a
            # session starts.  This is the defence-in-depth boundary for any
            # field added later: never let a raw encoder exception escape the
            # public API.
            raise SafeWriterError(
                E_PATH_INVALID, "journal event contains a Unicode surrogate code point"
            ) from exc
        if len(line) > MAX_JOURNAL_LINE_BYTES:
            raise SafeWriterError(E_JOURNAL_WRITE_FAILED, "journal event exceeds 64 KiB")
        old_size = self.size
        try:
            native.append_bytes(self.handle, line + b"\n")
            native.flush(self.handle)
        except OSError as exc:
            # Best-effort rollback to the previous complete line.  A failed
            # rollback is still reported as journal write failure; the next
            # open will classify the tail as corrupt rather than accept it.
            try:
                native.set_pointer(self.handle, old_size)
                native.set_end_of_file(self.handle)
                native.flush(self.handle)
            except OSError:
                pass
            raise SafeWriterError(E_JOURNAL_WRITE_FAILED, f"journal append/flush failed: {exc}") from exc
        self.events.append(event)
        self.last_line_sha256 = hashlib.sha256(line).hexdigest()
        self.size = old_size + len(line) + 1
        return event

    def flush(self) -> None:
        try:
            native.flush(self.handle)
        except OSError as exc:
            raise SafeWriterError(E_JOURNAL_WRITE_FAILED, f"journal flush failed: {exc}") from exc

    def view(self) -> JournalView:
        return JournalView(
            events=tuple(self.events),
            size=self.size,
            identity=self.identity,
            last_line_sha256=self.last_line_sha256,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_quiet(self.handle)


def _parse_journal(data: bytes, *, batch_id: str) -> tuple[list[dict[str, Any]], str | None]:
    if not data:
        return [], None
    if not data.endswith(b"\n"):
        raise SafeWriterError(E_JOURNAL_CORRUPT, "journal ends with a partial line")
    events: list[dict[str, Any]] = []
    previous: str | None = None
    for index, raw in enumerate(data.split(b"\n")[:-1], start=1):
        if not raw:
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal line {index} is empty")
        try:
            text = raw.decode("utf-8")
            event = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal line {index} is not valid JSON") from exc
        if not isinstance(event, dict):
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal line {index} is not an object")
        for key in _REQUIRED:
            if key not in event:
                raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal line {index} is missing {key}")
        if event["schema_version"] != JOURNAL_SCHEMA_VERSION:
            raise SafeWriterError(E_JOURNAL_CORRUPT, "journal schema_version is unsupported")
        if event["seq"] != index:
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal seq jumped at line {index}")
        if event["batch_id"] != batch_id:
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal batch_id mismatch at line {index}")
        if event["prev_event_sha256"] != previous:
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal chain mismatch at line {index}")
        if not isinstance(event["op_id"], str) or not event["op_id"]:
            raise SafeWriterError(E_JOURNAL_CORRUPT, f"journal line {index} has invalid op_id")
        events.append(event)
        previous = hashlib.sha256(raw).hexdigest()
    return events, previous
