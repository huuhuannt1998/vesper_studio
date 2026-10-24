import struct

import pytest

from vesper.validation.capture import Frame
from vesper.validation.realdata import (ZHA_RULES, Device, Report, dedupe, extract_reports, loss_gaps,
                                        parse_device_names, segments)

NAMES = """Topology A
0x09ac\tLedvance Plug Z3\tkey 2fd6b1b929e883d274478f3a2bf8d4ef\t
0x7150\tSonoff Temperature\tkey 2fd6b1b929e883d274478f3a2bf8d4ef\t(prev 0xd8ee)
0x772c \tAqara Door\t\tkey 2fd6b1b929e883d274478f3a2bf8d4ef\t


Topology B
0x6a5e\tSonoff Temperature\tkey 60ca4507229ff82271c615339e526146\t
------------------------------------------------
192.168.8.116\tHomeAssistant
"""


def temp_zcl(raw: int, tsn: int = 1) -> bytes:
    return struct.pack("<BBBHBh", 0x18, tsn, 0x0A, 0x0000, 0x29, raw)


def f(t, counter, src=0x7150, zcl=None, cluster=0x0402, seq=None):
    return Frame(t, src, counter if seq is None else seq, counter, cluster, zcl)


def test_the_device_list_maps_each_topology_address_and_its_previous_one():
    devices = parse_device_names(NAMES)
    assert devices[("A", 0x7150)] == devices[("A", 0xD8EE)] == Device(
        "A", "Sonoff Temperature", 0x7150, (0xD8EE,), "2fd6b1b929e883d274478f3a2bf8d4ef")
    assert devices[("A", 0x772C)].name == "Aqara Door"
    assert devices[("B", 0x6A5E)].key == "60ca4507229ff82271c615339e526146"
    assert len({d for d in devices.values()}) == 4


def test_a_device_list_with_a_malformed_line_is_refused():
    with pytest.raises(ValueError, match="line 2"):
        parse_device_names("Topology A\n0x09ac\tLedvance Plug Z3\tno key here\n")


def test_relayed_copies_and_aps_retries_are_dropped_but_a_wrapped_counter_is_not():
    frames = [f(0.0, 40), f(0.004, 40, seq=99), f(3.0, 40), f(20.0, 40)]
    assert [fr.t for fr in dedupe(frames)] == [0.0, 20.0]


def test_a_missing_aps_counter_marks_a_loss_gap_and_the_counter_wraps():
    frames = [f(0.0, 254), f(1.0, 255), f(2.0, 0), f(5.0, 2), f(6.0, 3, src=0x1111), f(9.0, 5, src=0x1111)]
    assert loss_gaps(frames) == {0x7150: [(2.0, 5.0)], 0x1111: [(6.0, 9.0)]}


def test_reports_are_decoded_per_attribute_and_bad_payloads_are_counted():
    frames = [f(0.0, 1, zcl=temp_zcl(2150)), f(1.0, 2, zcl=b"\x18\x01\x01\x00\x00"),  # read response: not a report
              f(2.0, 3, zcl=b"\x18\x02\x0a\xf7\x00\x4c\x01"),  # a Lumi structure: undecodable
              f(3.0, 4, zcl=None)]
    reports, undecodable = extract_reports(frames)
    assert reports == [(0x7150, 0x0402, 0x0000, 0.0, 2150)] and undecodable == 1


def test_a_loss_gap_inside_an_interval_splits_the_stream_and_one_at_its_edge_does_not():
    rs = [Report(t, 2000, "s1") for t in (0.0, 900.0, 1800.0, 2700.0)]
    assert [len(s) for s in segments(rs, [(950.0, 1000.0)])] == [2, 2]
    assert [len(s) for s in segments(rs, [(2700.0, 2750.0)])] == [4]  # loss after the last report
    assert [len(s) for s in segments(rs, [(850.0, 900.0)])] == [1, 3]  # the lost frame came before 900.0


def test_sessions_never_share_an_interval():
    rs = [Report(0.0, 1, "a"), Report(10.0, 1, "a"), Report(20.0, 1, "b")]
    assert [[r.session for r in s] for s in segments(rs, [])] == [["a", "a"], ["b"]]


def test_the_zha_rule_table_holds_the_configured_values():
    assert ZHA_RULES[(0x0402, 0x0000)] == (30, 900, 50) and ZHA_RULES[(0x0405, 0x0000)] == (30, 900, 100)
    assert ZHA_RULES[(0x0406, 0x0000)] == (0, 900, 1) and ZHA_RULES[(0x0008, 0x0000)] == (1, 900, 1)
    assert ZHA_RULES[(0x0001, 0x0021)] == (3600, 10800, 1) and ZHA_RULES[(0x0B04, 0x050B)] == (5, 900, 1)
    assert len(ZHA_RULES) == 58
