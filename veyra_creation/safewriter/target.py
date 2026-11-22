"""Target file handle: identity, link count, size and content hash."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import native
from .anchors import DirectoryAnchor, _close_quiet, _fail_status
from .errors import (
    E_HARDLINK_REFUSED,
    E_IDENTITY_UNAVAILABLE,
    E_LINK_COUNT_CHANGED,
    E_LINK_COUNT_UNAVAILABLE,
    E_REPARSE_REFUSED,
    E_TARGET_ABSENT,
    E_TARGET_CHANGED,
    E_TARGET_EXISTS,
    E_TARGET_UNSUPPORTED,
    SafeWriterError,
)
from .identities import ExpectedTarget, Identity, TargetFacts

_TARGET_READ = native.FILE_READ_DATA | native.FILE_READ_ATTRIBUTES | native.SYNCHRONIZE
_TARGET_REPLACE = _TARGET_READ | native.DELETE


@dataclass
class TargetAnchor:
    handle: int
    parent: DirectoryAnchor
    name: str
    facts: TargetFacts
    _closed: bool = False

    @classmethod
    def adopt_created(cls, parent: DirectoryAnchor, name: str, handle: int) -> "TargetAnchor":
        """Wrap a handle we just created with ``FILE_CREATE``."""
        try:
            facts = _read_facts(handle)
        except Exception:
            _close_quiet(handle)
            raise
        anchor = cls(handle=handle, parent=parent, name=name, facts=facts)
        try:
            anchor._check_facts_are_supported()
        except Exception:
            anchor.close()
            raise
        return anchor

    @classmethod
    def open_existing(
        cls,
        parent: DirectoryAnchor,
        name: str,
        *,
        expected: ExpectedTarget | None = None,
        for_replace: bool = False,
    ) -> "TargetAnchor":
        desired = _TARGET_REPLACE if for_replace else _TARGET_READ
        handle, status = native.nt_open_relative(
            parent.handle,
            name,
            desired_access=desired,
            share_access=native.FILE_SHARE_READ,
            disposition=native.FILE_OPEN,
            options=native.FILE_NON_DIRECTORY_FILE,
            attributes=0,
        )
        if status == native.STATUS_OBJECT_NAME_NOT_FOUND or status == native.STATUS_OBJECT_PATH_NOT_FOUND:
            raise SafeWriterError(E_TARGET_ABSENT, f"target {name!r} does not exist")
        if status == native.STATUS_FILE_IS_A_DIRECTORY:
            raise SafeWriterError(E_TARGET_UNSUPPORTED, f"target {name!r} is a directory")
        if status < 0 or handle is None:
            _fail_status(status, f"open target {name!r}")
        assert handle is not None
        try:
            facts = _read_facts(handle)
        except Exception:
            _close_quiet(handle)
            raise
        anchor = cls(handle=handle, parent=parent, name=name, facts=facts)
        try:
            anchor._check_facts_are_supported()
            if expected is not None:
                anchor.verify_expected(expected)
        except Exception:
            anchor.close()
            raise
        return anchor

    @classmethod
    def probe_absent(cls, parent: DirectoryAnchor, name: str) -> None:
        """Raise unless ``name`` is absent or a reparse target.

        A present ordinary file/directory is ``E_TARGET_EXISTS``.  A reparse
        target is ``E_REPARSE_REFUSED`` because create must never follow or
        overwrite one.
        """
        handle, status = native.nt_open_relative(
            parent.handle,
            name,
            desired_access=native.FILE_READ_ATTRIBUTES | native.SYNCHRONIZE,
            share_access=native.FILE_SHARE_READ,
            disposition=native.FILE_OPEN,
            options=0,
            attributes=0,
        )
        if status in (native.STATUS_OBJECT_NAME_NOT_FOUND, native.STATUS_OBJECT_PATH_NOT_FOUND):
            return
        if status < 0 or handle is None:
            _fail_status(status, f"probe target {name!r}")
        assert handle is not None
        try:
            try:
                attrs, tag = native.query_attributes(handle)
            except OSError as exc:
                raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot query target attributes: {exc}") from exc
            if native.is_reparse(attrs, tag):
                raise SafeWriterError(E_REPARSE_REFUSED, f"target {name!r} is a reparse point")
            raise SafeWriterError(E_TARGET_EXISTS, f"target {name!r} already exists")
        finally:
            _close_quiet(handle)

    def _check_facts_are_supported(self) -> None:
        if self.facts.is_reparse:
            raise SafeWriterError(E_REPARSE_REFUSED, f"target {self.name!r} is a reparse point")
        if self.facts.attributes & (native.FILE_ATTRIBUTE_DIRECTORY | native.FILE_ATTRIBUTE_DEVICE):
            raise SafeWriterError(E_TARGET_UNSUPPORTED, f"target {self.name!r} is not a regular file")
        if self.facts.nlink > 1:
            raise SafeWriterError(E_HARDLINK_REFUSED, f"target {self.name!r} has {self.facts.nlink} links")

    def verify_expected(self, expected: ExpectedTarget) -> TargetFacts:
        if self.facts.identity != expected.identity:
            raise SafeWriterError(E_TARGET_CHANGED, "target File ID or volume serial does not match expected")
        if self.facts.sha256 != expected.sha256:
            raise SafeWriterError(E_TARGET_CHANGED, "target SHA-256 does not match expected")
        return self.facts

    def read_facts(self) -> TargetFacts:
        return _read_facts(self.handle)

    def verify_unchanged(self, expected: ExpectedTarget) -> TargetFacts:
        current = _read_facts(self.handle)
        if current.identity != expected.identity:
            raise SafeWriterError(E_TARGET_CHANGED, "target identity changed while operation was in progress")
        if current.nlink != self.facts.nlink:
            raise SafeWriterError(E_LINK_COUNT_CHANGED, "target link count changed while operation was in progress")
        if current.size != self.facts.size:
            raise SafeWriterError(E_TARGET_CHANGED, "target size changed while operation was in progress")
        if current.sha256 != expected.sha256:
            raise SafeWriterError(E_TARGET_CHANGED, "target content changed while operation was in progress")
        self.facts = current
        return current

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_quiet(self.handle)


def _read_facts(handle: int) -> TargetFacts:
    try:
        attributes, reparse_tag = native.query_attributes(handle)
    except OSError as exc:
        raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot query target attributes: {exc}") from exc
    is_reparse = native.is_reparse(attributes, reparse_tag)
    if is_reparse:
        # Do not read content through a reparse handle.
        raise SafeWriterError(E_REPARSE_REFUSED, "target is a reparse point")
    try:
        nlink, size, _attrs = native.query_info(handle)
    except OSError as exc:
        raise SafeWriterError(E_LINK_COUNT_UNAVAILABLE, f"cannot query target link count: {exc}") from exc
    try:
        serial, file_id = native.query_identity(handle)
    except OSError as exc:
        raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot query target identity: {exc}") from exc
    identity = Identity(serial, file_id)
    try:
        sha256, total = native.read_hash(handle)
    except OSError as exc:
        raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot hash target: {exc}") from exc
    if total != size:
        raise SafeWriterError(E_TARGET_CHANGED, "target size changed while hashing")
    try:
        nlink_after, size_after, attrs_after = native.query_info(handle)
        serial_after, file_id_after = native.query_identity(handle)
        attrs_after2, tag_after = native.query_attributes(handle)
    except OSError as exc:
        raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot re-query target after hashing: {exc}") from exc
    if (
        nlink_after != nlink
        or size_after != size
        or attrs_after != attributes
        or attrs_after2 != attributes
        or tag_after != reparse_tag
        or serial_after != serial
        or file_id_after != file_id
    ):
        raise SafeWriterError(E_TARGET_CHANGED, "target changed while it was being inspected")
    if native.is_reparse(attrs_after2, tag_after):
        raise SafeWriterError(E_REPARSE_REFUSED, "target became a reparse point while it was being inspected")
    return TargetFacts(
        identity=identity,
        nlink=nlink,
        size=size,
        attributes=attributes,
        sha256=sha256,
        is_reparse=False,
    )
