import struct
import zlib

import pytest

from vesper.lab.png import Image, PngError, decode, diff, distinct_colors, encode


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _png(width, height, rows, *, color_type=6, filters=(0,), depth=8, interlace=0):
    """A PNG built the long way, with each row filtered by filters[row % len(filters)]."""
    bpp = len(rows[0]) // width
    out, prev = bytearray(), bytes(width * bpp)
    for j, row in enumerate(rows):
        f = filters[j % len(filters)]
        line = bytearray()
        for k, x in enumerate(row):
            a = row[k - bpp] if k >= bpp else 0
            b, c = prev[k], prev[k - bpp] if k >= bpp else 0
            pred = (0, a, b, (a + b) // 2, _paeth(a, b, c))[f]
            line.append((x - pred) & 0xFF)
        out += bytes([f]) + line
        prev = row

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    head = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(out))) + chunk(
        b"IEND", b"")


def _rows(width, height, bpp):
    return [bytes((7 * i + 13 * j + 31 * (i % bpp)) % 256 for i in range(width * bpp)) for j in range(height)]


def test_an_image_survives_encoding_and_decoding():
    img = Image(3, 2, bytes(range(24)))
    back = decode(encode(img))
    assert (back.width, back.height, back.rgba) == (3, 2, bytes(range(24)))
    assert back.pixel(2, 1) == (20, 21, 22, 23)


@pytest.mark.parametrize("color_type", [2, 6])
def test_every_row_filter_decodes(color_type):
    bpp = 4 if color_type == 6 else 3
    rows = _rows(5, 6, bpp)
    img = decode(_png(5, 6, rows, color_type=color_type, filters=(0, 1, 2, 3, 4)))
    for j, row in enumerate(rows):
        for i in range(5):
            px = tuple(row[i * bpp:(i + 1) * bpp]) + ((255,) if bpp == 3 else ())
            assert img.pixel(i, j) == px


@pytest.mark.parametrize("kwargs, words", [({"interlace": 1}, "interlaced"), ({"depth": 16}, "16-bit"),
                                           ({"color_type": 3}, "colour type 3")])
def test_unsupported_pngs_are_refused_by_name(kwargs, words):
    rows = _rows(2, 2, 4)
    with pytest.raises(PngError, match=words):
        decode(_png(2, 2, rows, **kwargs))


def test_bytes_that_are_not_a_png_are_refused():
    with pytest.raises(PngError, match="not a PNG"):
        decode(b"GIF89a....")


def test_diff_counts_pixels_beyond_the_tolerance_and_marks_them():
    base = Image(4, 4, bytes([100, 100, 100, 255]) * 16)
    near = bytearray(base.rgba)
    near[0] = 110  # within tolerance
    far = bytearray(base.rgba)
    far[4 * 5] = 200  # pixel (1, 1)
    assert diff(base, base).fraction == 0
    assert diff(base, Image(4, 4, bytes(near)), tolerance=16).fraction == 0
    d = diff(base, Image(4, 4, bytes(far)), tolerance=16)
    assert d.fraction == 1 / 16
    assert d.image.pixel(1, 1) == (255, 0, 0, 255)
    assert d.image.pixel(0, 0) != (255, 0, 0, 255)


def test_diff_of_different_sizes_is_total_and_says_why():
    d = diff(Image(2, 2, bytes(16)), Image(3, 2, bytes(24)))
    assert d.fraction == 1.0 and d.image is None and "2x2" in d.note and "3x2" in d.note


def test_distinct_colors_tells_a_blank_screen_from_a_drawn_one():
    blank = Image(8, 8, bytes([255, 255, 255, 255]) * 64)
    drawn = Image(8, 8, bytes(c for k in range(64) for c in (k * 4 % 256, 0, 255 - k, 255)))
    assert distinct_colors(blank) == 1
    assert distinct_colors(drawn, limit=10) == 10


def test_crop_keeps_the_region_it_names():
    img = Image(4, 3, bytes(c for k in range(12) for c in (k, k, k, 255)))
    part = img.crop(1, 1, 2, 2)
    assert (part.width, part.height) == (2, 2)
    assert [part.pixel(i, j)[0] for j in range(2) for i in range(2)] == [5, 6, 9, 10]
    with pytest.raises(PngError, match="outside"):
        img.crop(3, 0, 2, 1)
