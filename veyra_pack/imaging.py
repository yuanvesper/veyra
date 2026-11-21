"""Zero-dependency image header parsing (PNG, JPEG, GIF, WebP).

Only the intrinsic pixel size is read; image pixels are never decoded.  This
keeps validation cheap and avoids a third-party imaging dependency.
"""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

_JPEG_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def read_image_size(path: Path) -> tuple[int, int] | None:
    """Return ``(width, height)`` for a supported image, else ``None``."""

    try:
        with path.open("rb") as handle:
            head = handle.read(1024 * 1024)
    except OSError:
        return None
    return read_image_size_bytes(head)


def read_image_size_bytes(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        if data[12:16] == b"IHDR":
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return width, height
        return None
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return width, height
    if data.startswith(b"\xff\xd8"):
        return _jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 20:
        return _webp_size(data)
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    length = len(data)
    while index + 4 <= length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            index += 2
            continue
        segment = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment < 2:
            return None
        if marker in _JPEG_SOF:
            if index + 9 > length:
                return None
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + segment
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 ":
        start = data.find(b"\x9d\x01\x2a", 20)
        if start == -1 or start + 7 > len(data):
            return None
        width = int.from_bytes(data[start + 3 : start + 5], "little") & 0x3FFF
        height = int.from_bytes(data[start + 5 : start + 7], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = 1 + (bits & 0x3FFF)
        height = 1 + ((bits >> 14) & 0x3FFF)
        return width, height
    return None
