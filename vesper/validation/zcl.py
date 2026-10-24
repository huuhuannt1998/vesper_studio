"""ZCL Report Attributes (command 0x0a) payloads, decoded into (attribute id, data type, value).

Only the data types a reading needs are decoded (ZCL spec §2.6.2). A composite type (array, structure,
set, bag) or an unknown one stops the frame with ZclError, so a value is never guessed from the wrong
bytes. Strings stay bytes.
"""
from __future__ import annotations

import struct


class ZclError(ValueError):
    """A report payload that cannot be decoded exactly."""


REPORT_ATTRIBUTES = 0x0A
_STRINGS = {0x41: 1, 0x42: 1, 0x43: 2, 0x44: 2}  # type -> width of the length prefix
_FLOATS = {0x38: "<e", 0x39: "<f", 0x3A: "<d"}
_UNSIGNED = {0x30: 1, 0x31: 2, 0xE0: 4, 0xE1: 4, 0xE2: 4, 0xE8: 2, 0xE9: 2, 0xEA: 4, 0xF0: 8, 0xF1: 16}


def _width(dtype: int) -> tuple[int, bool] | None:
    """(bytes, signed) of a fixed-width integer-like type, or None."""
    for base in (0x08, 0x18, 0x20):  # data8..data64, map8..map64, uint8..uint64
        if base <= dtype <= base + 7:
            return dtype - base + 1, False
    if 0x28 <= dtype <= 0x2F:
        return dtype - 0x28 + 1, True
    if dtype == 0x10:
        return 1, False
    if dtype in _UNSIGNED:
        return _UNSIGNED[dtype], False
    return None


def _take(data: bytes, at: int, n: int) -> bytes:
    if at + n > len(data):
        raise ZclError(f"truncated at byte {at}: needs {n}, has {len(data) - at}")
    return data[at:at + n]


def parse_report(payload: bytes) -> list[tuple[int, int, object]] | None:
    """The attribute records of a global Report Attributes frame; None for any other ZCL frame."""
    fc = _take(payload, 0, 1)[0]
    at = 3 if fc & 0x04 else 1  # a manufacturer-specific frame carries a 2-byte manufacturer code
    _tsn, cmd = _take(payload, at, 2)
    at += 2
    if fc & 0x03 != 0 or cmd != REPORT_ATTRIBUTES:
        return None
    out = []
    while at < len(payload):
        attr, dtype = struct.unpack("<HB", _take(payload, at, 3))
        at += 3
        if dtype in _STRINGS:
            k = _STRINGS[dtype]
            n = int.from_bytes(_take(payload, at, k), "little")
            at += k
            n = 0 if n == (1 << 8 * k) - 1 else n  # all-ones length is the invalid string
            value: object = _take(payload, at, n)
            at += n
        elif dtype in _FLOATS:
            fmt = _FLOATS[dtype]
            value = struct.unpack(fmt, _take(payload, at, struct.calcsize(fmt)))[0]
            at += struct.calcsize(fmt)
        else:
            width = _width(dtype)
            if width is None:
                raise ZclError(f"attribute 0x{attr:04x} has data type 0x{dtype:02x}, which is not decoded")
            n, signed = width
            value = int.from_bytes(_take(payload, at, n), "little", signed=signed)
            at += n
        out.append((attr, dtype, value))
    return out
