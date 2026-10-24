import struct

import pytest

from vesper.validation.zcl import ZclError, parse_report


def frame(records: bytes, fc: int = 0x18, manufacturer: int | None = None, cmd: int = 0x0A) -> bytes:
    header = struct.pack("<B", fc | (0x04 if manufacturer is not None else 0))
    if manufacturer is not None:
        header += struct.pack("<H", manufacturer)
    return header + struct.pack("<BB", 7, cmd) + records


def test_a_signed_temperature_report_is_decoded():
    assert parse_report(frame(struct.pack("<HBh", 0x0000, 0x29, -125))) == [(0x0000, 0x29, -125)]


def test_several_records_in_one_report_keep_their_order():
    payload = struct.pack("<HBB", 0x0021, 0x20, 0xC8) + struct.pack("<HBB", 0x0020, 0x20, 0x1E)
    assert parse_report(frame(payload)) == [(0x0021, 0x20, 200), (0x0020, 0x20, 30)]


def test_booleans_bitmaps_and_wide_integers_are_numbers():
    payload = (struct.pack("<HBB", 0, 0x10, 1) + struct.pack("<HBB", 1, 0x18, 0x01)
               + struct.pack("<HB", 2, 0x22) + (70000).to_bytes(3, "little")
               + struct.pack("<HB", 3, 0x2A) + (-2).to_bytes(3, "little", signed=True))
    assert parse_report(frame(payload)) == [(0, 0x10, 1), (1, 0x18, 1), (2, 0x22, 70000), (3, 0x2A, -2)]


def test_a_manufacturer_specific_header_is_skipped():
    payload = struct.pack("<HBh", 0x0000, 0x29, 2150)
    assert parse_report(frame(payload, manufacturer=0x115F)) == [(0x0000, 0x29, 2150)]


def test_a_string_attribute_is_kept_as_bytes_and_parsing_continues():
    payload = struct.pack("<HBB", 0xFF01, 0x42, 3) + b"abc" + struct.pack("<HBB", 0x0005, 0x20, 9)
    assert parse_report(frame(payload)) == [(0xFF01, 0x42, b"abc"), (0x0005, 0x20, 9)]


def test_other_commands_and_cluster_specific_frames_are_not_reports():
    assert parse_report(frame(b"", cmd=0x01)) is None  # Read Attributes Response
    assert parse_report(frame(b"", fc=0x19)) is None  # cluster-specific command 0x0a


def test_an_unparseable_data_type_stops_the_frame_with_an_error():
    with pytest.raises(ZclError, match="0x4c"):
        parse_report(frame(struct.pack("<HB", 0x00F7, 0x4C) + b"\x01\x00"))


def test_a_truncated_record_is_an_error_not_a_short_value():
    with pytest.raises(ZclError, match="truncated"):
        parse_report(frame(struct.pack("<HB", 0x0000, 0x29) + b"\x01"))
    with pytest.raises(ZclError, match="truncated"):
        parse_report(b"\x18\x07")
