import shutil

import pytest

from tests.validation.ziotp_fixture import AQARA, GOOD, KEY_A, KEY_B, NAMES, reader_for, temp, tree
from tests.validation.zigbee_frames import ENCRYPTED_PCAP
from vesper.validation.realdata import ZIOTP_DEVICES, analyze

def test_conforming_streams_pass_and_firmware_governed_ones_are_reported_but_not_judged(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD + AQARA + [temp(1_760_000_050.0, 7, 1, src=0x9999)]}
    read = reader_for(files)
    res = analyze(tree(tmp_path, files), reader=read)
    assert read.calls == [("Idle/Topology_A/idle1.pcapng", KEY_A, "Normal")]
    assert res["passed"] and res["judged"] == 1 and res["judged_passed"] == 1
    by = {(s["device"], s["cluster"], s["attribute"]): s for s in res["streams"]}
    sonoff = by[("A:Sonoff Temperature@0x7150", "0x0402", "0x0000")]
    assert sonoff["rule"] == [30, 900, 50] and sonoff["passed"] and sonoff["firmware_governed"] is False
    aqara = by[("A:Aqara Door@0x772c", "0x0001", "0x0021")]
    assert aqara["firmware_governed"] and aqara["eligible"] and aqara["passed"] is False and not aqara["judged"]
    assert res["unknown_sources"] == {"A:0x9999": 1}
    assert res["files"][0]["decryption_rate"] == 1.0 and res["files"][0]["byte_order"] == "Normal"


def test_one_failing_judged_stream_fails_the_check(tmp_path):
    bad = [temp(1_760_000_000.0 + 10 * i, i + 1, 2000 + 100 * i) for i in range(25)]
    files = {"Idle/Topology_A/idle1.pcapng": bad}
    res = analyze(tree(tmp_path, files), reader=reader_for(files))
    assert not res["passed"] and res["judged"] == 1 and res["judged_passed"] == 0


def test_a_file_that_does_not_decrypt_fails_the_check_after_trying_the_other_byte_order(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD, "Idle/Topology_B/idle1.pcapng": GOOD}
    read = reader_for(files, decrypted={("Idle/Topology_B/idle1.pcapng", "Normal"): 0.5,
                                        ("Idle/Topology_B/idle1.pcapng", "Reverse"): 0.0})
    res = analyze(tree(tmp_path, files), reader=read)
    assert ("Idle/Topology_B/idle1.pcapng", KEY_B, "Reverse") in read.calls
    assert not res["passed"] and not res["decryption_ok"]
    assert [f["decryption_rate"] for f in res["files"]] == [1.0, 12 / 25]  # the better of the two orders


def test_the_reverse_byte_order_is_used_when_only_it_decrypts(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD}
    read = reader_for(files, decrypted={("Idle/Topology_A/idle1.pcapng", "Normal"): 0.0})
    res = analyze(tree(tmp_path, files), reader=read)
    assert res["decryption_ok"] and res["files"][0]["byte_order"] == "Reverse"


def test_streams_pool_across_sessions_and_a_capture_outside_the_rule_window_is_flagged(tmp_path):
    later = [temp(f.t + 86400.0 * 400, f.aps_counter, 2000) for f in GOOD]  # 2027: after zha 1.0.0
    files = {"Idle/Topology_A/idle1.pcapng": GOOD, "Power/Topology_A/p1.pcapng": later}
    res = analyze(tree(tmp_path, files), reader=reader_for(files))
    (s,) = [s for s in res["streams"] if s["cluster"] == "0x0402"]
    assert s["segments"] == 2 and s["intervals"] == 48
    assert res["capture_window_utc"][0].startswith("2025-10-09") and res["rule_window_ok"] is False


def test_vespers_own_catalog_rule_is_replayed_beside_zhas_as_a_description(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD}
    catalog = {"zigbee:sonoff:snzb-02": {"behavior": {"reporting": {"temperature": {"min_s": 10, "max_s": 3600,
                                                                                   "change": 1.0}}}}}
    res = analyze(tree(tmp_path, files), catalog=catalog, reader=reader_for(files))
    (s,) = [s for s in res["streams"] if s["cluster"] == "0x0402"]
    v = s["vesper_rule"]
    assert v["catalog_id"] == "zigbee:sonoff:snzb-02" and v["rule"] == [10, 3600, 100]
    assert v["matched"] == 6 and v["extra"] == 0  # 900-s heartbeats: the twin reproduces only every fourth


def test_a_device_name_outside_the_preregistered_table_is_refused(tmp_path):
    files = {"Idle/Topology_A/idle1.pcapng": GOOD}
    names = NAMES.replace("Aqara Door", "Mystery Gadget")
    with pytest.raises(ValueError, match="Mystery Gadget"):
        analyze(tree(tmp_path, files, names), reader=reader_for(files))


def test_a_tree_without_captures_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="pcapng"):
        analyze(tree(tmp_path, {}), reader=reader_for({}))


def test_every_preregistered_device_has_a_class_and_aqara_is_firmware_governed():
    assert {n for n, d in ZIOTP_DEVICES.items() if d["firmware_governed"]} == {
        "Aqara Door", "Aqara Vibration", "Aqara Mini Switch", "Aqara Motion"}


@pytest.mark.skipif(shutil.which("tshark") is None, reason="tshark (Wireshark) is not installed")
def test_the_default_reader_decrypts_a_real_encrypted_capture_with_the_topology_key(tmp_path):
    names = "Topology A\n0x4321\tSonoff Temperature\tkey 13CE8CB5C1FC6F208D63833D59D4FA2A\n"
    root = tree(tmp_path, {"Idle/Topology_A/enc.pcapng": []}, names)
    (root / "Data/Idle/Topology_A/enc.pcapng").write_bytes(ENCRYPTED_PCAP)
    res = analyze(root)
    assert res["decryption_ok"] and res["files"][0]["reports"] == 1
    (s,) = res["streams"]
    assert s["intervals"] == 0 and not s["eligible"]
