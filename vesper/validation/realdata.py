"""E2: do VESPER's reporting rules reproduce real Zigbee devices? (docs/experiments/l3-timing-design.md, amendment 2)

Real reports come from ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0), captured under Home
Assistant's ZHA. Each device's reported values are replayed through VESPER's own runtime, configured with
the rule ZHA set, and the twin's reports are matched against the real ones.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from vesper.validation.capture import Frame
from vesper.validation.zcl import ZclError, parse_report

# ZHA's default attribute-reporting configuration, (min_s, max_s, change) in raw ZCL units: zigpy/zha 0.0.70
# (2025-08-26, ebc3cbf), zha/zigbee/cluster_handlers/{const,general,measurement,homeautomation,smartenergy}.py;
# identical in 0.0.50 (2025-02-26) and 1.0.0 (2026-02-25). zha 2.0.0 (2026-06-24) postdates the dataset.
ZHA_SOURCE = ("zigpy/zha 0.0.70 (ebc3cbfd0d04, 2025-08-26) cluster_handlers; constants identical in "
              "0.0.50 (2025-02-26) and 1.0.0 (2026-02-25)")
_DEFAULT, _ASAP, _IMMEDIATE, _OP, _BATTERY = (30, 900, 1), (1, 900, 1), (0, 900, 1), (5, 900, 1), (3600, 10800, 1)
ZHA_RULES: dict[tuple[int, int], tuple[float, float, float]] = {
    (0x0001, 0x0020): _BATTERY, (0x0001, 0x0021): _BATTERY,  # battery voltage, percentage remaining
    (0x0006, 0x0000): _IMMEDIATE, (0x0008, 0x0000): _ASAP,  # on/off, current level
    **{(c, 0x0055): _DEFAULT for c in range(0x000C, 0x0015)},  # analog/binary/multistate present value
    (0x0400, 0x0000): _DEFAULT, (0x0401, 0x0000): _DEFAULT, (0x0402, 0x0000): (30, 900, 50),
    (0x0403, 0x0000): _DEFAULT, (0x0404, 0x0000): _DEFAULT, (0x0405, 0x0000): (30, 900, 100),
    (0x0406, 0x0000): _IMMEDIATE, (0x0407, 0x0000): (30, 900, 100), (0x0408, 0x0000): (30, 900, 100),
    (0x040A, 0x0000): _DEFAULT, (0x040B, 0x0000): (30, 900, 0.01), (0x040C, 0x0000): (30, 900, 0.000001),
    (0x040D, 0x0000): (30, 900, 0.000001), (0x042A, 0x0000): (30, 900, 0.1), (0x042B, 0x0000): (30, 900, 0.000001),
    **{(0x0B04, a): _IMMEDIATE for a in (0x0402, 0x0403, 0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605)},
    **{(0x0B04, a): _OP for a in (0x0300, 0x0304, 0x0505, 0x0508, 0x050B, 0x050F, 0x0905, 0x0908, 0x090B,
                                  0x0A05, 0x0A08, 0x0A0B)},
    **{(0x0702, a): _DEFAULT for a in (0x0000, 0x0001, 0x0100, 0x0102, 0x0104, 0x0106, 0x0108, 0x010A)},
    (0x0702, 0x0200): _ASAP, (0x0702, 0x0400): _OP,
}
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this


@dataclass(frozen=True)
class Device:
    topology: str
    name: str
    address: int  # NWK short address at the end of the capture campaign
    previous: tuple[int, ...]  # earlier addresses of the same device
    key: str  # the topology's network key


@dataclass(frozen=True)
class Report:
    t: float
    value: object
    session: str  # the capture file; intervals never span two files


_LINE = re.compile(r"^(0x[0-9a-fA-F]{4})\s+(.+?)\s+key\s+([0-9a-fA-F]{32})\s*(?:\(prev\s+(0x[0-9a-fA-F]{4})\))?\s*$")


def parse_device_names(text: str) -> dict[tuple[str, int], Device]:
    """ZIOTP's Data/Device_Name.txt: (topology, any address the device had) -> Device."""
    out, topology = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("Topology "):
            topology = line.split()[1]
            continue
        if line.startswith("---"):
            break  # the IP-address list of the other capture interfaces follows
        if not line:
            continue
        m = _LINE.match(line)
        if m is None or topology is None:
            raise ValueError(f"Device_Name.txt line {n} is not '<address> <name> key <key> [(prev <address>)]'")
        prev = (int(m.group(4), 16),) if m.group(4) else ()
        dev = Device(topology, m.group(2).strip(), int(m.group(1), 16), prev, m.group(3).lower())
        for addr in (dev.address, *prev):
            out[(topology, addr)] = dev
    return out


def dedupe(frames: list[Frame], window_s: float = DEDUPE_WINDOW_S) -> list[Frame]:
    """Drop relayed copies and APS retries: a frame whose (source, APS counter) was seen within window_s."""
    seen: dict[tuple[int, int], float] = {}
    out = []
    for fr in sorted(frames, key=lambda x: x.t):
        last = seen.get((fr.src, fr.aps_counter))
        if last is None or fr.t - last > window_s:
            out.append(fr)
            seen[(fr.src, fr.aps_counter)] = fr.t
    return out


def loss_gaps(frames: list[Frame]) -> dict[int, list[tuple[float, float]]]:
    """Per source, the spans in which the sniffer missed at least one of its APS frames (deduplicated input)."""
    last: dict[int, Frame] = {}
    gaps: dict[int, list[tuple[float, float]]] = {}
    for fr in sorted(frames, key=lambda x: x.t):
        prev = last.get(fr.src)
        if prev is not None and (fr.aps_counter - prev.aps_counter) % 256 != 1:
            gaps.setdefault(fr.src, []).append((prev.t, fr.t))
        last[fr.src] = fr
    return gaps


def extract_reports(frames: list[Frame]) -> tuple[list[tuple[int, int, int, float, object]], int]:
    """(source, cluster, attribute, time, value) for every attribute report, and the count of undecodable ones."""
    out, undecodable = [], 0
    for fr in frames:
        if fr.zcl is None:
            continue
        try:
            records = parse_report(fr.zcl)
        except ZclError:
            undecodable += 1
            continue
        for attr, _dtype, value in records or ():
            out.append((fr.src, fr.cluster, attr, fr.t, value))
    return out, undecodable


def segments(reports: list[Report], gaps: list[tuple[float, float]]) -> list[list[Report]]:
    """Split one attribute's reports into runs with no session change and no possible loss between neighbours."""
    out: list[list[Report]] = []
    for r in sorted(reports, key=lambda x: x.t):
        if out:
            p = out[-1][-1]
            lost = any(g1 < r.t and g2 > p.t for g1, g2 in gaps)
            if p.session == r.session and not lost:
                out[-1].append(r)
                continue
        out.append([r])
    return out
