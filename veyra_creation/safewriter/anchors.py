"""Handle-anchored root and directory objects.

The path string is used only to locate the volume and component names.  Every
component after that is opened relative to its parent handle, and its File ID /
volume serial is checked against the live chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import native
from .errors import (
    E_ACCESS_DENIED,
    E_ANCESTOR_CHANGED,
    E_FS_UNSUPPORTED,
    E_IDENTITY_UNAVAILABLE,
    E_IO,
    E_PATH_INVALID,
    E_REPARSE_REFUSED,
    E_ROOT_CHANGED,
    E_SHARING_VIOLATION,
    E_TARGET_ABSENT,
    E_TARGET_EXISTS,
    E_TARGET_UNSUPPORTED,
    SafeWriterError,
)
from .identities import Identity, LevelIdentity, RootIdentity
from .policy import validate_root_path, validate_simple_name

_DIR_ACCESS = native.FILE_TRAVERSE | native.FILE_READ_ATTRIBUTES | native.SYNCHRONIZE
_DIR_BROAD_ACCESS = (
    native.FILE_TRAVERSE
    | native.FILE_READ_ATTRIBUTES
    | native.FILE_ADD_FILE
    | native.FILE_ADD_SUBDIRECTORY
    | native.SYNCHRONIZE
)


def _map_status(status: int, *, default: str = E_IO) -> str:
    if status in (
        native.STATUS_OBJECT_NAME_NOT_FOUND,
        native.STATUS_OBJECT_PATH_NOT_FOUND,
        native.STATUS_DELETE_PENDING,
    ):
        return E_TARGET_ABSENT
    if status == native.STATUS_OBJECT_NAME_COLLISION:
        return E_TARGET_EXISTS
    if status == native.STATUS_SHARING_VIOLATION:
        return E_SHARING_VIOLATION
    if status == native.STATUS_ACCESS_DENIED:
        return E_ACCESS_DENIED
    if status in (native.STATUS_FILE_IS_A_DIRECTORY, native.STATUS_NOT_A_DIRECTORY):
        return E_TARGET_UNSUPPORTED
    return default


def _fail_status(status: int, context: str, *, default: str = E_IO) -> None:
    raise SafeWriterError(
        _map_status(status, default=default),
        f"{context}: {native.status_name(status)} (0x{status & 0xFFFFFFFF:08X})",
    )


def _query_level(handle: int, name: str, volume_serial: int) -> LevelIdentity:
    try:
        attributes, reparse_tag = native.query_attributes(handle)
    except OSError as exc:
        raise SafeWriterError(E_IO, f"cannot query attributes for {name!r}: {exc}") from exc
    is_reparse = native.is_reparse(attributes, reparse_tag)
    try:
        serial, file_id = native.query_identity(handle)
    except OSError as exc:
        raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot query identity for {name!r}: {exc}") from exc
    if serial != volume_serial:
        raise SafeWriterError(E_ROOT_CHANGED, f"component {name!r} is on a different volume")
    return LevelIdentity(name=name, volume_serial=serial, file_id=file_id, is_reparse=is_reparse)


def _close_quiet(handle: int | None) -> None:
    try:
        native.close_handle(handle)
    except Exception:
        pass


@dataclass
class RootAnchor:
    root_path: str
    volume_root: str
    volume_serial: int
    volume_guid: str
    _chain: list[int] = field(default_factory=list, repr=False)
    _levels: tuple[LevelIdentity, ...] = ()
    _identity: RootIdentity | None = None
    _closed: bool = False

    @classmethod
    def open(
        cls,
        root_path: str,
        *,
        expected: RootIdentity | None = None,
    ) -> "RootAnchor":
        normalized, volume_root, components = validate_root_path(root_path)
        try:
            serial, fs_name, volume_guid = native.volume_facts(volume_root)
        except OSError as exc:
            raise SafeWriterError(E_FS_UNSUPPORTED, f"cannot inspect volume: {exc}") from exc
        if fs_name.casefold() != "ntfs":
            raise SafeWriterError(E_FS_UNSUPPORTED, f"only NTFS is supported, got {fs_name!r}")
        try:
            current = native.open_volume_root(volume_root)
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            code = E_ACCESS_DENIED if winerror == native.ERROR_ACCESS_DENIED else E_FS_UNSUPPORTED
            raise SafeWriterError(code, f"cannot open volume root {volume_root!r}: {exc}") from exc

        chain = [current]
        levels: list[LevelIdentity] = []
        try:
            # ``FileIdInfo.VolumeSerialNumber`` is the 64-bit NTFS volume
            # identity.  GetVolumeInformation's 32-bit serial is a different
            # value and must not be compared with FileIdInfo.
            try:
                serial, _root_file_id = native.query_identity(current)
            except OSError as exc:
                raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot query volume identity: {exc}") from exc
            for index, part in enumerate(components):
                validate_simple_name(part, what="project root component")
                is_final = index == len(components) - 1
                desired = _DIR_BROAD_ACCESS if is_final else _DIR_ACCESS
                child, status = native.nt_open_relative(
                    current,
                    part,
                    desired_access=desired,
                    share_access=native.FILE_SHARE_ALL,
                    disposition=native.FILE_OPEN,
                    options=native.FILE_DIRECTORY_FILE,
                    attributes=0,
                )
                if status < 0:
                    _fail_status(status, f"open project root component {part!r}")
                assert child is not None
                chain.append(child)
                level = _query_level(child, part, serial)
                if level.is_reparse:
                    raise SafeWriterError(E_REPARSE_REFUSED, f"project root component {part!r} is a reparse point")
                levels.append(level)
                current = child

            assert levels
            final_level = levels[-1]
            identity = RootIdentity(
                volume_serial=serial,
                file_id=final_level.file_id,
                volume_guid=volume_guid,
                levels=tuple(levels),
            )
            # This comparison must stay inside the cleanup boundary.  It is an
            # exceptional path too: a mismatched expected identity must close
            # every handle in the freshly opened chain.
            if expected is not None:
                _compare_root(identity, expected)
        except Exception:
            for handle in reversed(chain):
                _close_quiet(handle)
            raise

        return cls(
            root_path=normalized,
            volume_root=volume_root,
            volume_serial=serial,
            volume_guid=volume_guid,
            _chain=chain,
            _levels=tuple(levels),
            _identity=identity,
        )

    @property
    def handle(self) -> int:
        if not self._chain or self._closed:
            raise SafeWriterError(E_ROOT_CHANGED, "root anchor is closed")
        return self._chain[-1]

    @property
    def identity(self) -> RootIdentity:
        if self._identity is None:
            raise SafeWriterError(E_ROOT_CHANGED, "root identity is unavailable")
        return self._identity

    @property
    def levels(self) -> tuple[LevelIdentity, ...]:
        return self._levels

    def verify(self) -> RootIdentity:
        if self._closed:
            raise SafeWriterError(E_ROOT_CHANGED, "root anchor is closed")
        try:
            attributes, reparse_tag = native.query_attributes(self.handle)
            if native.is_reparse(attributes, reparse_tag):
                raise SafeWriterError(E_REPARSE_REFUSED, "project root handle is now a reparse point")
            serial, file_id = native.query_identity(self.handle)
        except SafeWriterError:
            raise
        except OSError as exc:
            raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot verify project root: {exc}") from exc
        if serial != self.volume_serial or file_id != self.identity.file_id:
            raise SafeWriterError(E_ROOT_CHANGED, "project root handle identity changed")
        return self.identity

    def final_anchor(self) -> "DirectoryAnchor":
        level = self._levels[-1]
        return DirectoryAnchor(
            handle=self.handle,
            name=level.name,
            identity=level,
            volume_serial=self.volume_serial,
            parent=None,
            owns_handle=False,
            created=False,
        )

    def open_child_dir(self, name: str, *, create: bool = False) -> "DirectoryAnchor":
        return self.final_anchor().open_child(name, create=create)

    def open_path(self, components: Iterable[str], *, create: bool = False) -> "DirectoryAnchor":
        anchor = self.final_anchor()
        opened: list[DirectoryAnchor] = []
        try:
            for part in components:
                anchor = anchor.open_child(part, create=create)
                opened.append(anchor)
        except Exception:
            # ``open_child`` closes the child it just opened on its own error
            # path.  Close only the ancestors that were successfully returned
            # before the failure; closing them deepest-first avoids relying on
            # garbage collection for handle release.
            for child in reversed(opened):
                child.close()
            raise
        return anchor

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in reversed(self._chain):
            _close_quiet(handle)
        self._chain.clear()

    def __enter__(self) -> "RootAnchor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _compare_root(actual: RootIdentity, expected: RootIdentity) -> None:
    if actual.volume_serial != expected.volume_serial or actual.file_id != expected.file_id:
        raise SafeWriterError(E_ROOT_CHANGED, "project root identity does not match expected")
    if actual.volume_guid != expected.volume_guid:
        raise SafeWriterError(E_ROOT_CHANGED, "project root volume GUID does not match expected")
    if len(actual.levels) != len(expected.levels):
        raise SafeWriterError(E_ROOT_CHANGED, "project root ancestor chain length changed")
    for actual_level, expected_level in zip(actual.levels, expected.levels):
        if (
            actual_level.name != expected_level.name
            or actual_level.volume_serial != expected_level.volume_serial
            or actual_level.file_id != expected_level.file_id
        ):
            raise SafeWriterError(E_ROOT_CHANGED, "project root ancestor chain changed")


@dataclass
class DirectoryAnchor:
    handle: int
    name: str
    identity: LevelIdentity
    volume_serial: int
    parent: "DirectoryAnchor | None" = None
    owns_handle: bool = True
    created: bool = False
    _closed: bool = False

    @property
    def path_components(self) -> tuple[str, ...]:
        parts: list[str] = []
        node: DirectoryAnchor | None = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return tuple(reversed(parts))

    def open_child(self, name: str, *, create: bool = False) -> "DirectoryAnchor":
        validate_simple_name(name)
        if self._closed:
            raise SafeWriterError(E_ANCESTOR_CHANGED, "parent directory anchor is closed")
        child, status = native.nt_open_relative(
            self.handle,
            name,
            desired_access=_DIR_BROAD_ACCESS,
            share_access=native.FILE_SHARE_ALL,
            disposition=native.FILE_OPEN,
            options=native.FILE_DIRECTORY_FILE,
            attributes=0,
        )
        created = False
        if status < 0 and create and status == native.STATUS_OBJECT_NAME_NOT_FOUND:
            child, status = native.nt_open_relative(
                self.handle,
                name,
                desired_access=_DIR_BROAD_ACCESS,
                share_access=native.FILE_SHARE_ALL,
                disposition=native.FILE_CREATE,
                options=native.FILE_DIRECTORY_FILE,
                attributes=native.FILE_ATTRIBUTE_DIRECTORY,
            )
            if status == native.STATUS_OBJECT_NAME_COLLISION:
                child, status = native.nt_open_relative(
                    self.handle,
                    name,
                    desired_access=_DIR_BROAD_ACCESS,
                    share_access=native.FILE_SHARE_ALL,
                    disposition=native.FILE_OPEN,
                    options=native.FILE_DIRECTORY_FILE,
                    attributes=0,
                )
            elif status == 0:
                created = True
        if status < 0 or child is None:
            _fail_status(status, f"open directory {name!r}")
        assert child is not None
        try:
            level = _query_level(child, name, self.volume_serial)
            if level.is_reparse:
                raise SafeWriterError(E_REPARSE_REFUSED, f"directory {name!r} is a reparse point")
        except Exception:
            _close_quiet(child)
            raise
        return DirectoryAnchor(
            handle=child,
            name=name,
            identity=level,
            volume_serial=self.volume_serial,
            parent=self,
            owns_handle=True,
            created=created,
        )

    def verify(self) -> LevelIdentity:
        if self._closed:
            raise SafeWriterError(E_ANCESTOR_CHANGED, "directory anchor is closed")
        try:
            current = _query_level(self.handle, self.name, self.volume_serial)
        except SafeWriterError:
            raise
        except OSError as exc:
            raise SafeWriterError(E_IDENTITY_UNAVAILABLE, f"cannot verify directory {self.name!r}: {exc}") from exc
        if current.is_reparse:
            raise SafeWriterError(E_REPARSE_REFUSED, f"directory {self.name!r} is now a reparse point")
        if current.file_id != self.identity.file_id or current.volume_serial != self.identity.volume_serial:
            raise SafeWriterError(E_ANCESTOR_CHANGED, f"directory {self.name!r} identity changed")
        return current

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.owns_handle:
            _close_quiet(self.handle)

    def __enter__(self) -> "DirectoryAnchor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
