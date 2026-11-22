"""Windows Native API layer for SafeWriter.

This is the only module in the package that may contain ``NtCreateFile``,
``NtSetInformationFile``, ``LockFileEx`` and the related ctypes declarations.
All higher layers consume small, deterministic Python helpers.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

NTSTATUS = ctypes.c_long
ULONG = ctypes.c_ulong
ACCESS_MASK = ctypes.c_ulong
HANDLE = wintypes.HANDLE

FILE_READ_DATA = 0x0001
FILE_LIST_DIRECTORY = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_ADD_FILE = 0x0002
FILE_ADD_SUBDIRECTORY = 0x0004
FILE_TRAVERSE = 0x0020
FILE_DELETE_CHILD = 0x0040
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
DELETE = 0x00010000
SYNCHRONIZE = 0x00100000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
FILE_SHARE_ALL = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE

FILE_OPEN = 1
FILE_CREATE = 2
FILE_OPEN_IF = 3
OPEN_EXISTING = 3

FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_TEMPORARY = 0x00000100
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_DEVICE = 0x00000040

OBJ_CASE_INSENSITIVE = 0x00000040

LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
LOCKFILE_FAIL_IMMEDIATELY = 0x00000001

FILE_BEGIN = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DRIVE_FIXED = 3

STATUS_SUCCESS = 0
STATUS_OBJECT_NAME_INVALID = -1073741773
STATUS_OBJECT_NAME_NOT_FOUND = -1073741772
STATUS_OBJECT_NAME_COLLISION = -1073741771
STATUS_OBJECT_PATH_NOT_FOUND = -1073741766
STATUS_ACCESS_DENIED = -1073741790
STATUS_SHARING_VIOLATION = -1073741757
STATUS_FILE_IS_A_DIRECTORY = -1073741638
STATUS_NOT_A_DIRECTORY = -1073741565
STATUS_DELETE_PENDING = -1073741738
STATUS_DISK_FULL = -1073741697
STATUS_INVALID_INFO_CLASS = -1073741821

FILE_ID_INFO_CLASS = 18
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
FILE_RENAME_INFORMATION_EX_CLASS = 65
FILE_RENAME_INFORMATION_CLASS = 10
_RENAME_INFO_NAME_OFFSET = 20

ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ALREADY_EXISTS = 183


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", ULONG),
        ("RootDirectory", HANDLE),
        ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
        ("Attributes", ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    ]


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [
        ("Status", NTSTATUS),
        ("Information", ctypes.c_size_t),
    ]


class FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", ctypes.c_ubyte * 16),
    ]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class FILE_RENAME_INFO_EX_HEADER(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("RootDirectory", HANDLE),
        ("FileNameLength", wintypes.DWORD),
    ]


kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
kernel32.GetVolumeInformationW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
]
kernel32.GetVolumeInformationW.restype = wintypes.BOOL
kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
kernel32.GetDriveTypeW.restype = wintypes.UINT
kernel32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
kernel32.SetFilePointerEx.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
kernel32.SetEndOfFile.restype = wintypes.BOOL
kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
kernel32.FlushFileBuffers.restype = wintypes.BOOL
kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
kernel32.LockFileEx.restype = wintypes.BOOL
kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
kernel32.UnlockFileEx.restype = wintypes.BOOL

ntdll.NtCreateFile.argtypes = [
    ctypes.POINTER(HANDLE),
    ACCESS_MASK,
    ctypes.POINTER(OBJECT_ATTRIBUTES),
    ctypes.POINTER(IO_STATUS_BLOCK),
    ctypes.POINTER(ctypes.c_longlong),
    ULONG,
    ULONG,
    ULONG,
    ULONG,
    wintypes.LPVOID,
    ULONG,
]
ntdll.NtCreateFile.restype = NTSTATUS
ntdll.NtSetInformationFile.argtypes = [HANDLE, ctypes.POINTER(IO_STATUS_BLOCK), wintypes.LPVOID, ULONG, ULONG]
ntdll.NtSetInformationFile.restype = NTSTATUS
ntdll.RtlNtStatusToDosError.argtypes = [NTSTATUS]
ntdll.RtlNtStatusToDosError.restype = ULONG


def status_name(status: int) -> str:
    value = status & 0xFFFFFFFF
    names = {
        0xC0000033: "STATUS_OBJECT_NAME_INVALID",
        0xC0000034: "STATUS_OBJECT_NAME_NOT_FOUND",
        0xC0000035: "STATUS_OBJECT_NAME_COLLISION",
        0xC000003A: "STATUS_OBJECT_PATH_NOT_FOUND",
        0xC0000022: "STATUS_ACCESS_DENIED",
        0xC0000043: "STATUS_SHARING_VIOLATION",
        0xC00000BA: "STATUS_FILE_IS_A_DIRECTORY",
        0xC0000103: "STATUS_NOT_A_DIRECTORY",
        0xC0000056: "STATUS_DELETE_PENDING",
        0xC000007F: "STATUS_DISK_FULL",
        0xC0000003: "STATUS_INVALID_INFO_CLASS",
        0xC000000D: "STATUS_INVALID_PARAMETER",
    }
    return names.get(value, f"NTSTATUS_0x{value:08X}")


def is_not_found(status: int) -> bool:
    return status in (STATUS_OBJECT_NAME_NOT_FOUND, STATUS_OBJECT_PATH_NOT_FOUND, STATUS_DELETE_PENDING)


def _us(name: str) -> tuple[UNICODE_STRING, ctypes.Array]:
    # UNICODE_STRING lengths are UTF-16 code-unit counts, not Python code
    # points.  A non-BMP character is one Python character but two UTF-16 code
    # units (four bytes); using len(name) would make the kernel read a prefix.
    raw = name.encode("utf-16-le")
    code_units = len(raw) // 2
    buf = ctypes.create_unicode_buffer(code_units + 1)
    if raw:
        ctypes.memmove(buf, raw, len(raw))
    us = UNICODE_STRING()
    us.Length = len(raw)
    us.MaximumLength = len(raw) + 2
    us.Buffer = ctypes.cast(buf, wintypes.LPWSTR)
    return us, buf


def _status_or_raise(status: int, context: str) -> None:
    if status < 0:
        raise OSError(f"{context}: {status_name(status)} (0x{status & 0xFFFFFFFF:08X})")


def close_handle(handle: int | None) -> None:
    if handle:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def open_volume_root(volume_root: str) -> int:
    handle = kernel32.CreateFileW(
        volume_root,
        FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_OPEN_REPARSE_POINT,
        None,
    )
    if handle == INVALID_HANDLE_VALUE or not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def nt_open_relative(
    parent: int,
    name: str,
    *,
    desired_access: int,
    share_access: int,
    disposition: int,
    options: int,
    attributes: int = FILE_ATTRIBUTE_NORMAL,
) -> tuple[int | None, int]:
    handle = HANDLE()
    us, buf = _us(name)
    oa = OBJECT_ATTRIBUTES()
    oa.Length = ctypes.sizeof(OBJECT_ATTRIBUTES)
    oa.RootDirectory = HANDLE(parent)
    oa.ObjectName = ctypes.pointer(us)
    oa.Attributes = OBJ_CASE_INSENSITIVE
    iosb = IO_STATUS_BLOCK()
    status = ntdll.NtCreateFile(
        ctypes.byref(handle),
        desired_access,
        ctypes.byref(oa),
        ctypes.byref(iosb),
        None,
        attributes,
        share_access,
        disposition,
        options | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT,
        None,
        0,
    )
    # Keep buf and us alive until NtCreateFile returns.
    _ = buf
    if status < 0:
        return None, int(status)
    return int(handle.value), STATUS_SUCCESS


def query_identity(handle: int) -> tuple[int, str]:
    info = FILE_ID_INFO()
    if not kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle), FILE_ID_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    file_id = bytes(info.FileId).hex()
    return int(info.VolumeSerialNumber), file_id


def query_attributes(handle: int) -> tuple[int, int]:
    info = FILE_ATTRIBUTE_TAG_INFO()
    if not kernel32.GetFileInformationByHandleEx(
        wintypes.HANDLE(handle), FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.FileAttributes), int(info.ReparseTag)


def query_info(handle: int) -> tuple[int, int, int]:
    info = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
    return int(info.nNumberOfLinks), size, int(info.dwFileAttributes)


def is_reparse(attributes: int, reparse_tag: int) -> bool:
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT) or reparse_tag != 0


def set_pointer(handle: int, offset: int = 0) -> None:
    new_pos = ctypes.c_longlong(0)
    if not kernel32.SetFilePointerEx(wintypes.HANDLE(handle), ctypes.c_longlong(offset), ctypes.byref(new_pos), FILE_BEGIN):
        raise ctypes.WinError(ctypes.get_last_error())


def read_hash(handle: int, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    set_pointer(handle, 0)
    digest = hashlib.sha256()
    total = 0
    while True:
        buf = ctypes.create_string_buffer(chunk_size)
        read = wintypes.DWORD(0)
        if not kernel32.ReadFile(wintypes.HANDLE(handle), buf, chunk_size, ctypes.byref(read), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value == 0:
            break
        digest.update(buf.raw[: read.value])
        total += read.value
        if read.value < chunk_size:
            break
    return digest.hexdigest(), total


def read_all(handle: int, *, max_bytes: int = 16 * 1024 * 1024) -> bytes:
    _, size, _ = query_info(handle)
    if size > max_bytes:
        raise OSError(f"file exceeds read limit ({size} > {max_bytes})")
    set_pointer(handle, 0)
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        want = min(1024 * 1024, remaining)
        buf = ctypes.create_string_buffer(want)
        read = wintypes.DWORD(0)
        if not kernel32.ReadFile(wintypes.HANDLE(handle), buf, want, ctypes.byref(read), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value == 0:
            raise OSError("short read")
        chunks.append(buf.raw[: read.value])
        remaining -= read.value
    return b"".join(chunks)


def write_all(handle: int, data: bytes, *, truncate: bool = True) -> None:
    set_pointer(handle, 0)
    if truncate and not kernel32.SetEndOfFile(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())
    offset = 0
    view = memoryview(data)
    while offset < len(view):
        chunk = view[offset : offset + 1024 * 1024]
        buf = ctypes.create_string_buffer(bytes(chunk))
        written = wintypes.DWORD(0)
        if not kernel32.WriteFile(wintypes.HANDLE(handle), buf, len(chunk), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(chunk):
            raise OSError("short write")
        offset += written.value


def append_bytes(handle: int, data: bytes) -> None:
    offset = 0
    view = memoryview(data)
    while offset < len(view):
        chunk = view[offset : offset + 1024 * 1024]
        buf = ctypes.create_string_buffer(bytes(chunk))
        written = wintypes.DWORD(0)
        if not kernel32.WriteFile(wintypes.HANDLE(handle), buf, len(chunk), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(chunk):
            raise OSError("short append")
        offset += written.value


def flush(handle: int) -> None:
    if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def lock_exclusive(handle: int) -> None:
    ov = OVERLAPPED()
    ok = kernel32.LockFileEx(
        wintypes.HANDLE(handle),
        LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
        0,
        1,
        0,
        ctypes.byref(ov),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def unlock(handle: int) -> None:
    ov = OVERLAPPED()
    if not kernel32.UnlockFileEx(wintypes.HANDLE(handle), 0, 1, 0, ctypes.byref(ov)):
        raise ctypes.WinError(ctypes.get_last_error())


def rename(handle: int, target_parent: int, new_name: str, *, replace: bool = False) -> int:
    name_bytes = new_name.encode("utf-16-le")
    total = _RENAME_INFO_NAME_OFFSET + len(name_bytes)
    buf = ctypes.create_string_buffer(total)
    header = FILE_RENAME_INFO_EX_HEADER.from_buffer(buf)
    header.Flags = 0x1 if replace else 0
    header.RootDirectory = HANDLE(target_parent)
    header.FileNameLength = len(name_bytes)
    ctypes.memmove(ctypes.addressof(buf) + _RENAME_INFO_NAME_OFFSET, name_bytes, len(name_bytes))
    iosb = IO_STATUS_BLOCK()
    status = ntdll.NtSetInformationFile(
        HANDLE(handle), ctypes.byref(iosb), buf, total, FILE_RENAME_INFORMATION_EX_CLASS
    )
    if status == STATUS_INVALID_INFO_CLASS and not replace:
        # Older Windows builds lack FileRenameInformationEx.  The fallback is
        # still no-replace and still uses the already-open handle.
        buf2 = ctypes.create_string_buffer(total)
        ctypes.c_ubyte.from_buffer(buf2).value = 0
        header2 = FILE_RENAME_INFO_EX_HEADER.from_buffer(buf2)
        header2.RootDirectory = HANDLE(target_parent)
        header2.FileNameLength = len(name_bytes)
        ctypes.memmove(ctypes.addressof(buf2) + _RENAME_INFO_NAME_OFFSET, name_bytes, len(name_bytes))
        iosb2 = IO_STATUS_BLOCK()
        status = ntdll.NtSetInformationFile(
            HANDLE(handle), ctypes.byref(iosb2), buf2, total, FILE_RENAME_INFORMATION_CLASS
        )
    return int(status)


def set_end_of_file(handle: int) -> None:
    if not kernel32.SetEndOfFile(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_on_close(handle: int) -> None:
    """Mark the already-open file for deletion when its last handle closes.

    This is only used to clean up a temporary file whose identity we created.
    It never takes a path, so it cannot delete a replaced target by name.
    """

    class FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    info = FILE_DISPOSITION_INFO()
    info.DeleteFile = True
    if not kernel32.SetFileInformationByHandle(
        wintypes.HANDLE(handle), 4, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def file_size(handle: int) -> int:
    _, size, _ = query_info(handle)
    return size


def volume_facts(volume_root: str) -> tuple[int, str, str]:
    drive_type = int(kernel32.GetDriveTypeW(volume_root))
    if drive_type != DRIVE_FIXED:
        raise OSError(f"drive type {drive_type} is not DRIVE_FIXED")
    fs_name = ctypes.create_unicode_buffer(64)
    serial = wintypes.DWORD(0)
    max_component = wintypes.DWORD(0)
    fs_flags = wintypes.DWORD(0)
    if not kernel32.GetVolumeInformationW(
        volume_root,
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(max_component),
        ctypes.byref(fs_flags),
        fs_name,
        64,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    guid_buf = ctypes.create_unicode_buffer(64)
    if not kernel32.GetVolumeNameForVolumeMountPointW(volume_root, guid_buf, 64):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(serial.value), fs_name.value, guid_buf.value


def winerror_from_status(status: int) -> int:
    return int(ntdll.RtlNtStatusToDosError(NTSTATUS(status)))


def path_exists(path: str) -> bool:
    return os.path.exists(path)
