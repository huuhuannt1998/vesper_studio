import shutil
import struct

import pytest

from tests.validation.zigbee_frames import ENCRYPTED_KEY, ENCRYPTED_PCAP, frame, report, write_pcap
from vesper.validation.capture import CaptureError, Frame, parse_fields, read_capture, tshark_command

needs_tshark = pytest.mark.skipif(shutil.which("tshark") is None, reason="tshark (Wireshark) is not installed")


def row(*cells: str) -> str:
    return "\t".join(cells)


def test_rows_become_frames_and_decryption_is_counted():
    lines = [
        row("1700000000.250000000", "0x1234", "7", "True", "vesper", "0x00", "40", "0x0402", "18:09:0a:00:00:29:66:08"),
        row("1700000001.000000000", "0x1234", "8", "True", "", "", "", "", ""),  # secured, not decrypted
        row("1700000002.000000000", "0x0000", "9", "True", "vesper", "", "", "", ""),  # a NWK command
        row("1700000003.000000000", "0x1234", "10", "True", "vesper", "0x02", "41", "0x0402", ""),  # APS ack
        row("1700000004.000000000", "", "", "", "", "", "", "", ""),  # a MAC frame
    ]
    cap = parse_fields(lines)
    assert (cap.secured, cap.decrypted) == (4, 3)
    assert cap.frames == [Frame(1700000000.25, 0x1234, 7, 40, 0x0402, bytes.fromhex("18090a0000296608"))]


def test_the_key_is_checked_and_passed_to_tshark_as_a_uat_entry():
    cmd = tshark_command("x.pcapng", "2fd6b1b929e883d274478f3a2bf8d4ef")
    assert 'uat:zigbee_pc_keys:"2FD6B1B929E883D274478F3A2BF8D4EF","Normal","vesper"' in cmd
    assert cmd[cmd.index("-e") + 1] == "frame.time_epoch" and cmd[-1] == "@zbee_zcl"
    with pytest.raises(CaptureError, match="32 hex"):
        tshark_command("x.pcapng", "not-a-key")


def test_a_failing_tshark_is_an_error_with_its_message():
    def run(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="tshark: The file \"x\" doesn't exist.")
    with pytest.raises(CaptureError, match="doesn't exist"):
        read_capture("x", ENCRYPTED_KEY, run=run)


@needs_tshark
def test_tshark_reads_plain_reports_and_relayed_copies(tmp_path):
    temp = report(0x0000, 0x29, struct.pack("<h", 2150))
    pcap = write_pcap(tmp_path / "plain.pcap", [
        (100.0, frame(0x1234, 7, 40, 0x0402, [temp])),
        (100.004, frame(0x1234, 7, 40, 0x0402, [temp], mac_src=0x5555)),  # the same frame, relayed
    ])
    cap = read_capture(pcap, ENCRYPTED_KEY)
    assert (cap.secured, cap.decrypted) == (0, 0)
    assert [(f.t, f.src, f.aps_counter, f.cluster) for f in cap.frames] == [(100.0, 0x1234, 40, 0x0402),
                                                                             (100.004, 0x1234, 40, 0x0402)]
    assert cap.frames[0].zcl == bytes.fromhex("18010a0000296608")


@needs_tshark
def test_tshark_decrypts_with_the_network_key_and_only_with_it(tmp_path):
    path = tmp_path / "enc.pcap"
    path.write_bytes(ENCRYPTED_PCAP)
    good = read_capture(path, ENCRYPTED_KEY)
    assert (good.secured, good.decrypted) == (1, 1)
    assert good.frames == [Frame(1700000100.0, 0x4321, 5, 9, 0x0402, bytes.fromhex("18030a00002983ff"))]
    bad = read_capture(path, "00112233445566778899AABBCCDDEEFF")
    assert (bad.secured, bad.decrypted, bad.frames) == (1, 0, [])
