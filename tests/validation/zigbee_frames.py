"""Hand-built Zigbee captures for the capture and real-data tests (802.15.4 without FCS, pcap format)."""
from __future__ import annotations

import struct
from pathlib import Path

# One NWK-encrypted frame (CCM*, level 5, extended nonce) carrying a ZCL temperature report of -1.25 °C
# from 0x4321 (cluster 0x0402, APS counter 9, TSN 3), under the network key below. Made once with
# AES-CCM (tag 4, nonce = source IEEE 0x00158D0001A2B3C4 LE || frame counter 1000 LE || 0x2D) so the
# tests need no crypto library; tshark must decrypt and verify it with ENCRYPTED_KEY.
ENCRYPTED_KEY = "13CE8CB5C1FC6F208D63833D59D4FA2A"
ENCRYPTED_PCAP = bytes.fromhex(
    "d4c3b2a1020004000000000000000000ffff0000e600000064f15365000000003300000033000000618809621a000021434802"
    "000021431e0528e8030000c4b3a201008d150000beac84867d7ff58fda742da81eb03733283df10c")


def report(attr: int, dtype: int, value: bytes) -> bytes:
    return struct.pack("<HB", attr, dtype) + value


def frame(nwk_src: int, nwk_seq: int, aps_counter: int, cluster: int, records: list[bytes], *, tsn: int = 1,
          mac_src: int | None = None, zcl_cmd: int = 0x0A) -> bytes:
    """An unencrypted data frame from nwk_src to the coordinator; mac_src differs from nwk_src when relayed."""
    mac = struct.pack("<HBHHH", 0x8861, nwk_seq, 0x1A62, 0x0000, nwk_src if mac_src is None else mac_src)
    nwk = struct.pack("<HHHBB", 0x0048, 0x0000, nwk_src, 30, nwk_seq)
    aps = struct.pack("<BBHHBB", 0x00, 0x01, cluster, 0x0104, 0x01, aps_counter)
    return mac + nwk + aps + struct.pack("<BBB", 0x18, tsn, zcl_cmd) + b"".join(records)


def write_pcap(path: Path, frames: list[tuple[float, bytes]]) -> Path:
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 230))  # LINKTYPE_IEEE802_15_4_NOFCS
        for t, data in frames:
            whole = int(t)
            f.write(struct.pack("<IIII", whole, int(round((t - whole) * 1e6)), len(data), len(data)) + data)
    return path
