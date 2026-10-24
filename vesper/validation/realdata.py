"""E2: do VESPER's reporting rules reproduce real Zigbee devices? (docs/experiments/l3-timing-design.md, amendment 2)

Real reports come from ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0), captured under Home
Assistant's ZHA. Interval by interval, VESPER's own runtime is installed in the state a device last reported,
fed the device's next reported value at its real time, and configured with the rule ZHA set; the twin's next
report is its prediction of the device's next report.
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
from vesper.validation.zcl import ZclError, manufacturer_code, parse_report

# ZHA's default attribute-reporting configuration, (min_s, max_s, change) in raw ZCL units: zigpy/zha 0.0.70
# (2025-08-26, ebc3cbf), zha/zigbee/cluster_handlers/{const,general,measurement,homeautomation,smartenergy,
# lighting,hvac,closures}.py; the reporting configuration is identical in 0.0.50 (2025-02-26) and 1.0.0
# (2026-02-25). zha 2.0.0 (2026-06-24) postdates the dataset.
ZHA_SOURCE = ("zigpy/zha 0.0.70 (ebc3cbfd0d04, 2025-08-26) cluster_handlers; reporting configuration identical in "
              "0.0.50 (2025-02-26) and 1.0.0 (2026-02-25)")
_DEFAULT, _ASAP, _IMMEDIATE, _OP, _BATTERY = (30, 900, 1), (1, 900, 1), (0, 900, 1), (5, 900, 1), (3600, 10800, 1)
_CLIMATE, _CLIMATE_DEMAND, _CLIMATE_DISCRETE = (30, 900, 25), (30, 900, 5), (30, 900, 1)  # hvac.py
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
    **{(0x0300, a): _DEFAULT for a in (0x0003, 0x0004, 0x0007)},  # colour: current x, current y, temperature
    (0x0202, 0x0000): _OP,  # fan mode
    **{(0x0201, a): _CLIMATE for a in (0x0000, 0x0011, 0x0012, 0x0013, 0x0014, 0x001E, 0x001C)},  # thermostat
    **{(0x0201, a): _CLIMATE_DEMAND for a in (0x0029, 0x0007, 0x0008)},  # running state, PI demands
    (0x0201, 0x0002): _CLIMATE_DISCRETE,  # thermostat occupancy
    (0x0101, 0x0000): _IMMEDIATE,  # door lock state
    (0x0102, 0x0008): _IMMEDIATE, (0x0102, 0x0009): _IMMEDIATE,  # window covering lift, tilt percentage
}
ZIOTP_COMMIT = "c3481e8073f40bdbc1bee4a24e05a0a13a9545ba"  # github.com/antonio-boiano/ZIOTP2026, 2026-01-22
DEDUPE_WINDOW_S = 10.0  # a relayed copy or APS retry of a frame arrives within this
# Pre-registered (amendment 2): matching windows and pass criteria.
CHANGE_TOL_S = 1.0  # a change-driven twin report matches the real one within this
PERIODIC_TOL = 0.10  # a heartbeat matches within this fraction of max_s, early or late (device clocks drift)
MIN_INTERVALS = 20  # informative intervals a stream needs before it is judged
MATCH_MIN = 0.95  # fraction of informative intervals in which the twin's next report must match the device's
DECRYPT_MIN = 0.99  # fraction of a file's secured frames, on its network's PAN, the published key must decrypt
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


def loss_gaps(frames: list[Frame], key: Callable = lambda fr: fr.src) -> dict:
    """Per owner (`key`, by default the NWK source), the spans in which a frame may have been lost: a step in
    its APS counter other than +1, or a change of its short address (a rejoin), between two captured frames."""
    last: dict = {}
    gaps: dict = {}
    for fr in sorted(frames, key=lambda x: x.t):
        owner = key(fr)
        prev = last.get(owner)
        if prev is not None and ((fr.aps_counter - prev.aps_counter) % 256 != 1 or fr.src != prev.src):
            gaps.setdefault(owner, []).append((prev.t, fr.t))
        last[owner] = fr
    return gaps


@dataclass
class Extracted:
    reports: list[tuple]  # (owner, endpoint, cluster, attribute, time, value)
    undecodable: list[tuple]  # (owner, cluster, time) of report frames that cannot be decoded exactly
    manufacturer_specific: int  # reports in a vendor's own attribute namespace, kept out of every stream


def extract_reports(frames: list[Frame], key: Callable = lambda fr: fr.src) -> Extracted:
    """Every standard attribute report, located by owner (`key`) and source endpoint; an undecodable report
    frame is located so the intervals around it can be cut, and a manufacturer-specific one is only counted."""
    out = Extracted([], [], 0)
    for fr in frames:
        if fr.zcl is None:
            continue
        try:
            records = parse_report(fr.zcl)
            vendor = manufacturer_code(fr.zcl)
        except ZclError:
            out.undecodable.append((key(fr), fr.cluster, fr.t))
            continue
        if records and vendor is not None:
            out.manufacturer_specific += len(records)
            continue
        for attr, _dtype, value in records or ():
            out.reports.append((key(fr), fr.endpoint, fr.cluster, attr, fr.t, value))
    return out


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


def interval_class(a: Report, b: Report, rule: tuple[float, float, float]) -> str:
    """`change`: a reportable change between min_s and max_s after `a`; there the twin agrees by construction,
    because it is given b's value at b's time. Every other interval is informative: `steady` (no reportable
    change, so the heartbeat is due), `early_change` (before min_s) or `late_change` (after max_s)."""
    min_s, max_s, change = rule
    dt, dv = b.t - a.t, abs(b.value - a.value)
    if dv < change if change else dv == 0:
        return "steady"
    if dt < min_s:
        return "early_change"
    return "change" if dt <= max_s else "late_change"


def predict(a: Report, b: Report, rule: tuple[float, float, float]) -> tuple[float, str]:
    """The twin's first report after `a`, (time, cause): VESPER's runtime is installed in a's state as just
    reported at a's time, and b's value is applied as a measured stimulus at b's time."""
    min_s, max_s, change = rule
    clock = LogicalClock()
    rt = Runtime(clock=clock)
    vds = {"id": "replay", "features": [{"name": "value", "type": "numeric", "access": ["state"]}],
           "behavior": {"reporting": {"value": {"min_s": min_s, "max_s": max_s, "change": change}}}}
    out: list[tuple[float, str]] = []
    rt.subscribe(lambda e: out.append((a.t + e.observed_t, e.cause)))
    rt.add(vds, state={"value": a.value})
    clock.advance_to(b.t - a.t)  # a heartbeat falls due first when the device stays silent past max_s
    if not out:
        rt.stimulate("replay", {"type": "measured", "feature": "value", "value": b.value})
    if not out:
        clock.advance_to(max_s)  # a deferred report (at min_s) or the heartbeat (at max_s) is due by then
    if rt.faulted or rt.notes or not out:
        raise RuntimeError(f"replay failed: {rt.faulted or rt.notes or 'no report'}")
    return out[0]


def verdict(b_t: float, predicted: tuple[float, str], max_s: float) -> str:
    """`matched`, `extra` (the twin would have reported before the device did) or `missed` (the device
    reported before the twin would have)."""
    t, cause = predicted
    tol = PERIODIC_TOL * max_s if cause == "periodic" else CHANGE_TOL_S
    if t < b_t - tol:
        return "extra"
    return "missed" if t > b_t + tol else "matched"


def _numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def evaluate_stream(segs: list[list[Report]], rule: tuple[float, float, float]) -> dict:
    """The pre-registered agreement over informative intervals, and descriptive conformance, of one stream."""
    if not all(_numeric(r.value) for s in segs for r in s):
        raise TypeError("only numeric attribute values can be replayed")
    min_s, max_s, change = rule
    classes: Counter = Counter()
    verdicts: Counter = Counter()
    trivial = trivial_ok = 0
    intervals, steady, early_ok = [], [], []
    for s in segs:
        for a, b in zip(s, s[1:]):
            dt, kind = b.t - a.t, interval_class(a, b, rule)
            classes[kind] += 1
            intervals.append(dt)
            if kind == "steady":
                steady.append(dt)
            if dt < max_s - CHANGE_TOL_S:  # before the heartbeat was due: only a reportable change may be sent
                early_ok.append(kind != "steady")
            v = verdict(b.t, predict(a, b, rule), max_s)
            if kind == "change":  # agreement by construction: a check of the runtime, not evidence
                trivial, trivial_ok = trivial + 1, trivial_ok + (v == "matched")
            else:
                verdicts[v] += 1
    n, informative = len(intervals), sum(verdicts.values())
    eligible = informative >= MIN_INTERVALS
    matched = verdicts["matched"]
    return {
        "segments": len(segs), "intervals": n, "classes": dict(classes),
        "change_agreement": trivial_ok / trivial if trivial else None,
        "informative": informative, "matched": matched, "extra": verdicts["extra"], "missed": verdicts["missed"],
        "matched_fraction": matched / informative if informative else None, "eligible": eligible,
        "passed": (matched / informative >= MATCH_MIN) if eligible else None,
        "min_interval_ok": sum(dt >= min_s - CHANGE_TOL_S for dt in intervals) / n if n else None,
        "max_silence_ok": sum(dt <= max_s * (1 + PERIODIC_TOL) for dt in intervals) / n if n else None,
        "heartbeat_ratio": median(steady) / max_s if steady else None,
        "early_change_ok": sum(early_ok) / len(early_ok) if early_ok else None,
    }


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _network(cap: Capture) -> tuple[int | None, int, int]:
    """(PAN, secured, decrypted) of the capture's own network: the PAN with the most decrypted frames (or, if
    none decrypted, the most secured ones). Secured frames of other PANs on the channel are not its traffic."""
    if not cap.pans:
        return None, cap.secured, cap.decrypted
    pan = max(cap.pans, key=lambda p: (cap.pans[p][1], cap.pans[p][0]))
    return pan, cap.pans[pan][0], cap.pans[pan][1]


def _read_decrypting(reader: Callable, path: Path, key: str) -> tuple[Capture, str]:
    """The capture under the published key, in whichever byte order decrypts it (MIC-verified, so unambiguous)."""
    best, order = reader(path, key, byte_order="Normal"), "Normal"
    _, secured, decrypted = _network(best)
    if secured and decrypted / secured < DECRYPT_MIN:
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
    files, streams = [], defaultdict(list)
    gaps: defaultdict = defaultdict(list)  # device -> loss spans; (device, cluster) -> undecodable report frames
    unknown_sources: Counter = Counter()
    times: list[float] = []
    for path in paths:
        topology = path.parent.name.split("_", 1)[1]
        cap, order = _read_decrypting(reader, path, keys[topology])
        pan, secured, decrypted = _network(cap)

        def owner(fr, topology=topology):  # the device, across every address it had; else its source address
            return devices.get((topology, fr.src)) or (topology, fr.src)
        frames = dedupe(cap.frames)
        ex = extract_reports(frames, key=owner)
        session = path.relative_to(data).as_posix()
        for dev, spans in loss_gaps(frames, key=owner).items():
            gaps[dev].extend(spans)
        for dev, cluster, t in ex.undecodable:
            gaps[(dev, cluster)].append((t, t))  # a report there may be lost: cut the interval around it
        for dev, endpoint, cluster, attr, t, value in ex.reports:
            if not isinstance(dev, Device):
                unknown_sources[f"{dev[0]}:0x{dev[1]:04x}"] += 1
                continue
            streams[(dev, endpoint, cluster, attr)].append(Report(t, value, session))
        times += [fr.t for fr in frames]
        files.append({"file": session, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "byte_order": order, "pan": None if pan is None else f"0x{pan:04x}", "secured": secured,
                      "decrypted": decrypted, "decryption_rate": decrypted / secured if secured else None,
                      "foreign_secured": cap.secured - secured, "bad_fcs": cap.bad_fcs, "frames": len(cap.frames),
                      "deduplicated": len(frames), "reports": len(ex.reports),
                      "undecodable_reports": len(ex.undecodable),
                      "manufacturer_specific_reports": ex.manufacturer_specific})
    decryption_ok = all(f["decryption_rate"] is not None and f["decryption_rate"] >= DECRYPT_MIN for f in files)
    out_streams = []
    for (dev, endpoint, cluster, attr), reports in sorted(
            streams.items(), key=lambda kv: (kv[0][0].topology, kv[0][0].address, kv[0][1] or 0, kv[0][2], kv[0][3])):
        info = ZIOTP_DEVICES[dev.name]
        row = {"device": f"{dev.topology}:{dev.name}@0x{dev.address:04x}", "endpoint": endpoint,
               "cluster": f"0x{cluster:04x}", "attribute": f"0x{attr:04x}", "reports": len(reports),
               "firmware_governed": info["firmware_governed"]}
        rule = ZHA_RULES.get((cluster, attr))
        if rule is None or not all(_numeric(r.value) for r in reports):
            row.update(rule=None, judged=False, excluded="no ZHA reporting rule for this attribute" if rule is None
                       else "non-numeric values")
            out_streams.append(row)
            continue
        segs = segments(reports, gaps[dev] + gaps[(dev, cluster)])
        ev = evaluate_stream(segs, rule)
        row.update(rule=list(rule), **ev, judged=ev["eligible"] and not info["firmware_governed"])
        mine = _vesper_rule(catalog, dev, cluster, attr)
        if mine:
            v = evaluate_stream(segs, mine[1])
            row["vesper_rule"] = {"catalog_id": mine[0], "rule": list(mine[1]),
                                  **{k: v[k] for k in ("informative", "matched", "extra", "missed",
                                                       "matched_fraction")}}
        out_streams.append(row)
    judged = [s for s in out_streams if s["judged"]]
    window = [_utc(min(times)), _utc(max(times))] if times else None
    in_window = bool(window) and RULE_WINDOW_UTC[0] <= window[0][:10] and window[1][:10] <= RULE_WINDOW_UTC[1]
    return {
        "dataset": "ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0)", "zha_source": ZHA_SOURCE,
        "criteria": {"match_min": MATCH_MIN, "min_informative_intervals": MIN_INTERVALS,
                     "change_tol_s": CHANGE_TOL_S, "periodic_tol": PERIODIC_TOL, "decrypt_min": DECRYPT_MIN},
        "files": files, "decryption_ok": decryption_ok, "unknown_sources": dict(unknown_sources),
        "capture_window_utc": window,
        "rule_window_ok": in_window,
        "streams": out_streams, "eligible": sum(1 for s in out_streams if s.get("eligible")),
        "judged": len(judged), "judged_passed": sum(1 for s in judged if s["passed"]),
        "passed": decryption_ok and bool(judged) and all(s["passed"] for s in judged),
    }
