"""PNG images for the visual checks: read a browser's screenshot, compare it with a reference, write the result.

Only what a browser writes is read: 8-bit RGB or RGBA, not interlaced. Pure Python, so the checks need neither
Pillow nor numpy.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

SIGNATURE = b"\x89PNG\r\n\x1a\n"
_KINDS = {0: "greyscale", 3: "palette", 4: "greyscale with alpha"}


class PngError(ValueError):
    """An image the checks cannot read or a region outside it."""


@dataclass(frozen=True)
class Image:
    width: int
    height: int
    rgba: bytes  # row-major, 4 bytes per pixel

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        k = 4 * (y * self.width + x)
        return tuple(self.rgba[k:k + 4])

    def crop(self, x: int, y: int, width: int, height: int) -> Image:
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise PngError(f"region {width}x{height} at ({x}, {y}) is outside the {self.width}x{self.height} image")
        rows = (self.rgba[4 * (j * self.width + x):4 * (j * self.width + x + width)] for j in range(y, y + height))
        return Image(width, height, b"".join(rows))


@dataclass(frozen=True)
class Diff:
    fraction: float  # of pixels that differ beyond the tolerance
    image: Image | None  # the reference, faded, with differing pixels in red
    note: str = ""


def decode(data: bytes) -> Image:
    if not data.startswith(SIGNATURE):
        raise PngError("not a PNG (no PNG signature)")
    pos, head, idat = len(SIGNATURE), None, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if head is None:
        raise PngError("not a PNG (no IHDR chunk)")
    width, height, depth, color_type, _, _, interlace = head
    if interlace:
        raise PngError("interlaced PNGs are not supported")
    if depth != 8:
        raise PngError(f"{depth}-bit PNGs are not supported (8-bit only)")
    if color_type not in (2, 6):
        raise PngError(f"colour type {color_type} ({_KINDS.get(color_type, 'unknown')}) is not supported")
    bpp = 4 if color_type == 6 else 3
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise PngError(f"corrupt image data: {e}") from e
    stride = width * bpp
    if len(raw) < height * (stride + 1):
        raise PngError("truncated image data")
    out, prev = bytearray(), bytearray(stride)
    for j in range(height):
        start = j * (stride + 1)
        row = _unfilter(raw[start], bytearray(raw[start + 1:start + 1 + stride]), prev, bpp)
        if bpp == 3:
            wide = bytearray(width * 4)
            wide[0::4], wide[1::4], wide[2::4] = row[0::3], row[1::3], row[2::3]
            wide[3::4] = b"\xff" * width
            out += wide
        else:
            out += row
        prev = row
    return Image(width, height, bytes(out))


def _unfilter(kind: int, row: bytearray, prev: bytearray, bpp: int) -> bytearray:
    n = len(row)
    if kind == 0:
        return row
    if kind == 1:
        for k in range(bpp, n):
            row[k] = (row[k] + row[k - bpp]) & 0xFF
    elif kind == 2:
        return bytearray((a + b) & 0xFF for a, b in zip(row, prev))
    elif kind == 3:
        for k in range(n):
            row[k] = (row[k] + ((row[k - bpp] if k >= bpp else 0) + prev[k]) // 2) & 0xFF
    elif kind == 4:
        for k in range(n):
            a, b = (row[k - bpp] if k >= bpp else 0), prev[k]
            c = prev[k - bpp] if k >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            row[k] = (row[k] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
    else:
        raise PngError(f"unknown row filter {kind}")
    return row


def encode(img: Image) -> bytes:
    stride = img.width * 4
    raw = b"".join(b"\x00" + img.rgba[j * stride:(j + 1) * stride] for j in range(img.height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    head = struct.pack(">IIBBBBB", img.width, img.height, 8, 6, 0, 0, 0)
    return SIGNATURE + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def diff(reference: Image, actual: Image, tolerance: int = 16) -> Diff:
    """The pixels whose largest channel difference exceeds `tolerance` (anti-aliasing stays below it)."""
    if (reference.width, reference.height) != (actual.width, actual.height):
        return Diff(1.0, None, f"sizes differ: {reference.width}x{reference.height} reference, "
                               f"{actual.width}x{actual.height} now")
    a, b, out, bad = reference.rgba, actual.rgba, bytearray(len(reference.rgba)), 0
    for k in range(0, len(a), 4):
        if max(abs(a[k + c] - b[k + c]) for c in range(4)) > tolerance:
            out[k:k + 4] = b"\xff\x00\x00\xff"
            bad += 1
        else:
            grey = 160 + (a[k] + a[k + 1] + a[k + 2]) // 12  # the reference, faded
            out[k:k + 4] = bytes((grey, grey, grey, 255))
    return Diff(bad / (reference.width * reference.height), Image(reference.width, reference.height, bytes(out)))


def distinct_colors(img: Image, limit: int | None = None) -> int:
    """How many different colours the image holds, counting up to `limit`: 1 is a blank screen."""
    seen: set[bytes] = set()
    for k in range(0, len(img.rgba), 4):
        seen.add(img.rgba[k:k + 4])
        if limit is not None and len(seen) >= limit:
            break
    return len(seen)
