"""E2: do VESPER's reporting rules reproduce real Zigbee devices? (docs/experiments/l3-timing-design.md, amendment 2)

Real reports come from ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0), captured under Home
Assistant's ZHA. Each device's reported values are replayed through VESPER's own runtime, configured with
the rule ZHA set, and the twin's reports are matched against the real ones.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.capture import Capture, Frame, read_capture
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
ZIOTP_COMMIT = "c3481e8073f40bdbc1bee4a24e05a0a13a9545ba"  # github.com/antonio-boiano/ZIOTP2026, 2026-01-22
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this
# Pre-registered (amendment 2): matching windows and pass criteria.
CHANGE_TOL_S = 1.0  # a change-driven twin report matches a real report within this
PERIODIC_LATE = 0.10  # a real heartbeat may come up to this fraction of max_s after the twin's
MIN_INTERVALS = 20  # clean intervals a stream needs before it is judged
MATCH_MIN = 0.95  # fraction of real reports the twin must reproduce
EXTRA_MAX = 0.05  # fraction of the twin's reports that may have no real counterpart
DECRYPT_MIN = 0.99  # fraction of a file's secured frames the published key must decrypt
# The capture must fall in the span over which ZHA_RULES was verified identical (zha 0.0.50 .. 1.0.0).
RULE_WINDOW_UTC = ("2025-02-26", "2026-02-25")

_LUMI = ("zigbee-herdsman-converters 26.123.0 configures no attribute reporting for this model and gives it a "
         "one-hour check-in quirk: its report timing is set by firmware, not by the hub")
# Every name in ZIOTP's Data/Device_Name.txt, classified before the data is read (amendment 2). The catalog id
# serves only the descriptive comparison with VESPER's own rule; None where the dataset names no model.
ZIOTP_DEVICES: dict[str, dict] = {
    "Sonoff Temperature": {"catalog_id": "zigbee:sonoff:snzb-02", "firmware_governed": False},
    "Sonoff Motion": {"catalog_id": "zigbee:sonoff:snzb-03", "firmware_governed": False},
    "Sonoff Door": {"catalog_id": "zigbee:sonoff:snzb-04", "firmware_governed": False},
    "Philips Motion": {"catalog_id": "zigbee:philips:9290012607", "firmware_governed": False},
    "Philips Lamp": {"catalog_id": "zigbee:philips:9290024691", "firmware_governed": False},
    "Ledvance Smart+ Plug": {"catalog_id": "zigbee:ledvance:4058075729261", "firmware_governed": False},
    "Ledvance RGBW Bulb": {"catalog_id": "zigbee:ledvance:4058075208391", "firmware_governed": False},
    "Ledvance Bulb": {"catalog_id": "zigbee:ledvance:4058075208391", "firmware_governed": False},
    "Ledvance Plug Z3": {"catalog_id": "zigbee:ledvance:ac10691", "firmware_governed": False},
    "Ledvance Z3 Plug": {"catalog_id": "zigbee:ledvance:ac10691", "firmware_governed": False},
    "Smart Socket": {"catalog_id": None, "firmware_governed": False},
    "Smart Power Plug": {"catalog_id": None, "firmware_governed": False},
    "Power Plug": {"catalog_id": None, "firmware_governed": False},
    "Moes Bulb": {"catalog_id": None, "firmware_governed": False},
    "Aqara Door": {"catalog_id": "zigbee:aqara:mccgq11lm", "firmware_governed": True, "basis": _LUMI},
    "Aqara Vibration": {"catalog_id": "zigbee:aqara:djt11lm", "firmware_governed": True, "basis": _LUMI},
    "Aqara Mini Switch": {"catalog_id": "zigbee:aqara:wb-r02d", "firmware_governed": True, "basis": _LUMI},
    "Aqara Motion": {"catalog_id": "zigbee:aqara:ms-s02", "firmware_governed": True, "basis": _LUMI},
}
# (cluster, attribute) -> (VDS reporting feature, raw ZCL units per VDS unit) for the VESPER-rule description;
# illuminance (logarithmic) and power (device-specific divisor) have no fixed conversion and are left out.
VESPER_FEATURES = {(0x0402, 0x0000): ("temperature", 100), (0x0405, 0x0000): ("humidity", 100),
                   (0x0406, 0x0000): ("occupancy", 1), (0x0006, 0x0000): ("state", 1),
                   (0x0008, 0x0000): ("brightness", 1), (0x0001, 0x0021): ("battery", 2)}


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


def replay(segment: list[Report], rule: tuple[float, float, float]) -> list[tuple[float, str]]:
    """The twin's reports, (time, cause), when VESPER's runtime is fed the segment's values at their real times.

    The twin starts in the first report's state, as if it had just sent it; the replay ends at the last report.
    """
    min_s, max_s, change = rule
    t0 = segment[0].t
    clock = LogicalClock()
    rt = Runtime(clock=clock)
    vds = {"id": "replay", "features": [{"name": "value", "type": "numeric", "access": ["state"]}],
           "behavior": {"reporting": {"value": {"min_s": min_s, "max_s": max_s, "change": change}}}}
    out: list[tuple[float, str]] = []
    rt.subscribe(lambda e: out.append((t0 + e.observed_t, e.cause)))
    rt.add(vds, state={"value": segment[0].value})
    for r in segment[1:]:
        clock.advance_to(r.t - t0)
        rt.stimulate("replay", {"type": "measured", "feature": "value", "value": r.value})
    if rt.faulted or rt.notes:
        raise RuntimeError(f"replay failed: {rt.faulted or rt.notes}")
    return out


def match(real: list[float], twin: list[tuple[float, str]], max_s: float) -> tuple[int, int]:
    """(real reports reproduced, twin reports with no real counterpart).

    Each twin report claims the nearest unclaimed real report in its window: within CHANGE_TOL_S of a
    change-driven report; for a heartbeat, from CHANGE_TOL_S before to PERIODIC_LATE * max_s after it.
    """
    free = sorted(real)
    matched = extra = 0
    for t, cause in sorted(twin):
        hi = t + (PERIODIC_LATE * max_s if cause == "periodic" else CHANGE_TOL_S)
        candidates = [x for x in free if t - CHANGE_TOL_S <= x <= hi]
        if candidates:
            free.remove(min(candidates, key=lambda x: abs(x - t)))
            matched += 1
        else:
            extra += 1
    return matched, extra


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def evaluate_stream(segs: list[list[Report]], rule: tuple[float, float, float]) -> dict:
    """Replay agreement (the pre-registered criterion) and descriptive conformance of one attribute stream."""
    if not all(_numeric(r.value) for s in segs for r in s):
        raise TypeError("only numeric attribute values can be replayed")
    min_s, max_s, change = rule
    intervals, steady, early_ok, matched, twin_n, extra = [], [], [], 0, 0, 0
    for s in segs:
        for a, b in zip(s, s[1:]):
            dt = b.t - a.t
            intervals.append(dt)
            if b.value == a.value:
                steady.append(dt)
            if dt < max_s - CHANGE_TOL_S:  # before the heartbeat was due: only a reportable change may be sent
                early_ok.append(abs(b.value - a.value) >= change if change else b.value != a.value)
        if len(s) > 1:
            twin = replay(s, rule)
            m, x = match([r.t for r in s[1:]], twin, max_s)
            matched, twin_n, extra = matched + m, twin_n + len(twin), extra + x
    n = len(intervals)
    eligible = n >= MIN_INTERVALS
    mf = matched / n if n else None
    xf = extra / twin_n if twin_n else 0.0
    return {
        "segments": len(segs), "intervals": n, "matched": matched, "twin_reports": twin_n, "extra": extra,
        "matched_fraction": mf, "extra_fraction": xf, "eligible": eligible,
        "passed": (mf >= MATCH_MIN and xf <= EXTRA_MAX) if eligible else None,
        "min_interval_ok": sum(dt >= min_s - CHANGE_TOL_S for dt in intervals) / n if n else None,
        "max_silence_ok": sum(dt <= max_s * (1 + PERIODIC_LATE) for dt in intervals) / n if n else None,
        "heartbeat_ratio": median(steady) / max_s if steady else None,
        "early_change_ok": sum(early_ok) / len(early_ok) if early_ok else None,
    }


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_decrypting(reader: Callable, path: Path, key: str) -> tuple[Capture, str]:
    """The capture under the published key, in whichever byte order decrypts it (MIC-verified, so unambiguous)."""
    best, order = reader(path, key, byte_order="Normal"), "Normal"
    if best.secured and best.decrypted / best.secured < DECRYPT_MIN:
        other = reader(path, key, byte_order="Reverse")
        if other.decrypted > best.decrypted:
            best, order = other, "Reverse"
    return best, order


def _vesper_rule(catalog: dict | None, dev: Device, cluster: int, attr: int) -> tuple[str, tuple] | None:
    cid = ZIOTP_DEVICES[dev.name]["catalog_id"]
    if not catalog or cid not in catalog or (cluster, attr) not in VESPER_FEATURES:
        return None
    feature, raw_per_unit = VESPER_FEATURES[(cluster, attr)]
    rule = catalog[cid].get("behavior", {}).get("reporting", {}).get(feature)
    if rule is None:
        return None
    return cid, (rule["min_s"], rule["max_s"], rule["change"] * raw_per_unit)


def analyze(root: str | Path, catalog: dict | None = None, *, reader: Callable = read_capture) -> dict:
    """E2 over a ZIOTP2025 checkout: decryption gate, streams, replay agreement under ZHA's rule (judged) and
    under VESPER's catalog rule (described)."""
    data = Path(root) / "Data"
    devices = parse_device_names((data / "Device_Name.txt").read_text())
    unknown = sorted({d.name for d in devices.values()} - set(ZIOTP_DEVICES))
    if unknown:
        raise ValueError(f"devices outside the pre-registered table: {', '.join(unknown)}")
    keys = {d.topology: d.key for d in devices.values()}
    if len(keys) != len({(d.topology, d.key) for d in devices.values()}):
        raise ValueError("a topology lists more than one network key")
    paths = sorted(data.glob("*/Topology_*/*.pcapng"))
    if not paths:
        raise FileNotFoundError(f"no Data/<scenario>/Topology_<X>/*.pcapng under {root}")
    files, streams, gaps = [], defaultdict(list), defaultdict(list)
    unknown_sources: Counter = Counter()
    times: list[float] = []
    for path in paths:
        topology = path.parent.name.split("_", 1)[1]
        cap, order = _read_decrypting(reader, path, keys[topology])
        frames = dedupe(cap.frames)
        reports, undecodable = extract_reports(frames)
        session = path.relative_to(data).as_posix()
        for src, spans in loss_gaps(frames).items():
            if (topology, src) in devices:
                gaps[devices[(topology, src)]].extend(spans)
        for src, cluster, attr, t, value in reports:
            dev = devices.get((topology, src))
            if dev is None:
                unknown_sources[f"{topology}:0x{src:04x}"] += 1
                continue
            streams[(dev, cluster, attr)].append(Report(t, value, session))
        times += [fr.t for fr in frames]
        files.append({"file": session, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "byte_order": order, "secured": cap.secured, "decrypted": cap.decrypted,
                      "decryption_rate": cap.decrypted / cap.secured if cap.secured else None,
                      "frames": len(cap.frames), "deduplicated": len(frames), "reports": len(reports),
                      "undecodable_reports": undecodable})
    decryption_ok = all(f["decryption_rate"] is not None and f["decryption_rate"] >= DECRYPT_MIN for f in files)
    out_streams = []
    for (dev, cluster, attr), reports in sorted(streams.items(), key=lambda kv: (kv[0][0].topology, kv[0][0].address,
                                                                                  kv[0][1], kv[0][2])):
        info = ZIOTP_DEVICES[dev.name]
        row = {"device": f"{dev.topology}:{dev.name}@0x{dev.address:04x}", "cluster": f"0x{cluster:04x}",
               "attribute": f"0x{attr:04x}", "reports": len(reports), "firmware_governed": info["firmware_governed"]}
        rule = ZHA_RULES.get((cluster, attr))
        if rule is None or not all(_numeric(r.value) for r in reports):
            row.update(rule=None, judged=False, excluded="no ZHA reporting rule for this attribute" if rule is None
                       else "non-numeric values")
            out_streams.append(row)
            continue
        segs = segments(reports, gaps[dev])
        ev = evaluate_stream(segs, rule)
        row.update(rule=list(rule), **ev, judged=ev["eligible"] and not info["firmware_governed"])
        mine = _vesper_rule(catalog, dev, cluster, attr)
        if mine:
            v = evaluate_stream(segs, mine[1])
            row["vesper_rule"] = {"catalog_id": mine[0], "rule": list(mine[1]),
                                  **{k: v[k] for k in ("matched", "twin_reports", "extra", "matched_fraction",
                                                       "extra_fraction")}}
        out_streams.append(row)
    judged = [s for s in out_streams if s["judged"]]
    window = [_utc(min(times)), _utc(max(times))] if times else None
    in_window = bool(window) and RULE_WINDOW_UTC[0] <= window[0][:10] and window[1][:10] <= RULE_WINDOW_UTC[1]
    return {
        "dataset": "ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0)", "zha_source": ZHA_SOURCE,
        "criteria": {"match_min": MATCH_MIN, "extra_max": EXTRA_MAX, "min_intervals": MIN_INTERVALS,
                     "change_tol_s": CHANGE_TOL_S, "periodic_late": PERIODIC_LATE, "decrypt_min": DECRYPT_MIN},
        "files": files, "decryption_ok": decryption_ok, "unknown_sources": dict(unknown_sources),
        "capture_window_utc": window,
        "rule_window_ok": in_window,
        "streams": out_streams, "eligible": sum(1 for s in out_streams if s.get("eligible")),
        "judged": len(judged), "judged_passed": sum(1 for s in judged if s["passed"]),
        "passed": decryption_ok and bool(judged) and all(s["passed"] for s in judged),
    }
