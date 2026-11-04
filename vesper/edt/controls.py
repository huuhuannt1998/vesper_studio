"""Synthetic controls of the EDT oracle (docs/experiments/edt-protocol.md, addendum 4): accuracy, not only consistency.

Two corpora of automations we write ourselves, run through the unchanged sweep (rung 4, every environment, the
standard home, the shared trace cache) and scored against answers known without the replay:

- the **positive control**: automations whose firing times per environment follow analytically from the site (sun
  triggers with offsets, time triggers, sun and time conditions; `vesper.env.sun.sun_events`) or from the recorded
  trace itself (numeric_state triggers, Home Assistant's armed rule over the trace's reports). Each expectation is
  reduced to an outcome per environment and to the expected environmental divergences by section 4's rules, written
  out here independently of `vesper.edt.oracle`. A candidate whose answer sits near a boundary (a condition's edge
  within 10 min, a first-firing spread within 10 min of 3 h, a flapping call that depends on the window's exact
  length) is dropped before the run.
- the **sensor-path negative control**: automations that read a sensor (binding, the sensors' reports, the replay's
  delivery) but can never fire in any environment: thresholds outside every environment's range, states no sensor of
  that role reports.

Every synthetic automation acts only on a smart plug (`switch.turn_on`), so no lamp or cover envelope can arise.
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

DAY_S = 86400.0
HOUR_S = 3600.0
RUN_DAYS = 3                      # vesper.env.outdoor.RUN_DAYS: day 0 warm-up, days 1-2 measured
WINDOW = (DAY_S, RUN_DAYS * DAY_S)  # the measured window, [start, end)
REPORT_DELAY_S = 0.05             # vesper.edt.replay.REPORT_DELAY_S: a report reaches the engine this late
TIME_SPREAD_H = 3.0
FLAP_COUNT = 6
FLAP_WINDOW_S = HOUR_S
MARGIN_S = 600.0                  # protocol A4.1: no answer within 10 min of a boundary
SPREAD_MARGIN_H = 10 / 60
DIVERGENCE_KINDS = ("fires_somewhere_only", "time_spread", "flaps")
CRITERIA = {"detection_rate_min": 0.95, "false_positives_max": 0, "first_time_mae_min_max": 2.0,
            "negative_flagged_max": 0}
ACTION = {"service": "switch.turn_on", "entity_id": "switch.kitchen_plug"}
PROTOCOL = "docs/experiments/edt-protocol.md, addendum 4"

# The sensors the numeric_state controls read: (name in the automation, twin id in the trace, feature). The twin ids
# follow vesper.edt.home (`<room>_temp0`, `<room>_lux0` of PRODUCTS' one product per role).
SERIES = {
    "porch_temperature": ("sensor.porch_temperature", "zigbee:sonoff:snzb-02@porch_temp0", "temperature"),
    "living_illuminance": ("sensor.living_room_illuminance", "zigbee:aqara:rtcgq11lm@living_lux0", "illuminance"),
    "porch_illuminance": ("sensor.porch_illuminance", "zigbee:aqara:rtcgq11lm@porch_lux0", "illuminance"),
}


# ---- expected firing times ------------------------------------------------------------------------------------------

def _in_window(times) -> list[float]:
    return sorted(t for t in times if WINDOW[0] <= t < WINDOW[1])


def sun_firings(events: list[dict], event: str, offset_s: float) -> list[float]:
    """A sun trigger's firing times in the measured window: each run day's event (seconds after the first day's local
    midnight, `vesper.env.sun.sun_events`) plus the offset; a day without the event has none."""
    return _in_window(e[event] + offset_s for e in events if e.get(event) is not None)


def time_firings(at_s: float) -> list[float]:
    """A time trigger's firing times in the measured window (local time of day `at_s`, every day)."""
    return _in_window(d * DAY_S + at_s for d in range(RUN_DAYS))


def _sun_bounds(events: list[dict], t: float, cond: dict) -> list[tuple[str, float | None]]:
    day = events[int(t // DAY_S)]
    out = []
    for side in ("after", "before"):
        if cond.get(side):
            ev = day.get(cond[side])
            out.append((side, None if ev is None else ev + float(cond.get(f"{side}_offset", 0.0))))
    return out


def sun_condition_ok(events: list[dict], t: float, cond: dict) -> bool:
    """Home Assistant's sun condition at `t`: after the day's event plus `after_offset`, before the day's event plus
    `before_offset` (offsets in seconds). A day without the event fails the condition."""
    for side, bound in _sun_bounds(events, t, cond):
        if bound is None or (side == "after" and t < bound) or (side == "before" and t > bound):
            return False
    return True


def sun_condition_margin(events: list[dict], t: float, cond: dict) -> float:
    """Seconds from `t` to the nearest of the condition's boundaries (infinite without one)."""
    return min((abs(t - b) for _, b in _sun_bounds(events, t, cond) if b is not None), default=math.inf)


def time_condition_ok(t: float, after_s: float, before_s: float) -> bool:
    """Home Assistant's time condition: after <= time of day < before, or across midnight when after > before."""
    tod = t % DAY_S
    if after_s < before_s:
        return after_s <= tod < before_s
    return tod >= after_s or tod < before_s


def time_condition_margin(t: float, after_s: float, before_s: float) -> float:
    tod = t % DAY_S

    def gap(b):
        d = abs(tod - b) % DAY_S
        return min(d, DAY_S - d)
    return min(gap(after_s), gap(before_s))


def numeric_firings(initial, reports, above: float | None = None, below: float | None = None) -> list[float]:
    """Home Assistant's numeric_state trigger over a step function: armed at start iff the initial value is out of
    range (no value: armed); a value into range while armed fires and disarms; a value out of range re-arms.
    `reports`: (delivery time, value), in delivery order."""
    def inside(v):
        if v is None:
            return False
        return (above is None or v > above) and (below is None or v < below)
    armed, out = not inside(initial), []
    for t, v in reports:
        if inside(v):
            if armed:
                out.append(t)
            armed = False
        else:
            armed = True
    return out


# ---- outcome and divergences (section 4, written out independently of vesper.edt.oracle) ----------------------------

def _flaps(times: list[float], window: float = FLAP_WINDOW_S) -> bool:
    ts, n = sorted(set(times)), FLAP_COUNT + 1
    return any(ts[i + n - 1] - ts[i] <= window for i in range(len(ts) - n + 1))


def expected_outcome(times: list[float]) -> dict:
    """{fired, first_tod_h, flaps} over the measured window."""
    m = _in_window(times)
    if not m:
        return {"fired": False, "first_tod_h": None, "flaps": False}
    return {"fired": True, "first_tod_h": (m[0] % DAY_S) / HOUR_S, "flaps": _flaps(m)}


def _gap_h(a: float, b: float) -> float:
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def _max_gap_h(outcomes: dict) -> float | None:
    tods = [o["first_tod_h"] for o in outcomes.values() if o["fired"]]
    if len(tods) < 2:
        return None
    return max(_gap_h(a, b) for i, a in enumerate(tods) for b in tods[i + 1:])


def expected_divergences(outcomes: dict) -> tuple[set, set]:
    """(environmental divergences, static kinds) of per-environment outcomes: fires in some environments only; two
    first firings more than 3 h apart (the short way round the clock); flapping in some environments (in every one:
    static)."""
    divs, static = set(), set()
    fired = [o for o in outcomes.values() if o["fired"]]
    if fired and len(fired) < len(outcomes):
        divs.add("fires_somewhere_only")
    gap = _max_gap_h(outcomes)
    if gap is not None and gap > TIME_SPREAD_H:
        divs.add("time_spread")
    flapping = [o["flaps"] for o in outcomes.values()]
    if any(flapping):
        (static if all(flapping) else divs).add("flaps")
    return divs, static


def spread_margin_h(outcomes: dict) -> float | None:
    """Hours between the largest pairwise first-firing gap and the 3 h threshold (None: fewer than two fire)."""
    gap = _max_gap_h(outcomes)
    return None if gap is None else abs(gap - TIME_SPREAD_H)


# ---- scoring --------------------------------------------------------------------------------------------------------

def _stats(errors: list[float]) -> dict:
    return {"n": len(errors), "mae_min": sum(errors) / len(errors) if errors else None,
            "max_min": max(errors) if errors else None}


def score_positive(controls: list[dict], result: dict) -> dict:
    """The positive control against a sweep result (`result["automations"]` keyed by each control's `hash`)."""
    autos = result["automations"]
    expected = detected = 0
    by_div = {k: {"expected": 0, "detected": 0} for k in DIVERGENCE_KINDS}
    by_kind: dict[str, dict] = {}
    fps, missed, not_run, errors, disagreements, static_mismatch = [], [], [], [], [], []
    agree = n_env = 0
    kind_errors: dict[str, list[float]] = {}
    for ctl in controls:
        k = by_kind.setdefault(ctl["kind"], {"n": 0, "expected": 0, "detected": 0, "false_positives": 0})
        k["n"] += 1
        a = autos.get(ctl["hash"])
        ran = a is not None and a.get("bound") is not None and bool(a.get("outcomes"))
        obs = set(a["divergences"]) if ran else set()
        if not ran:
            not_run.append(ctl["name"])
        for d in ctl["divergences"]:
            expected += 1
            k["expected"] += 1
            by_div[d]["expected"] += 1
            if d in obs:
                detected += 1
                k["detected"] += 1
                by_div[d]["detected"] += 1
            else:
                missed.append({"name": ctl["name"], "kind": ctl["kind"], "divergence": d})
        for d in sorted(obs - set(ctl["divergences"])):
            fps.append({"name": ctl["name"], "kind": ctl["kind"], "divergence": d})
            k["false_positives"] += 1
        if not ran:
            continue
        if sorted(a.get("static") or []) != sorted(ctl.get("static") or []):
            static_mismatch.append({"name": ctl["name"], "expected": sorted(ctl.get("static") or []),
                                    "observed": sorted(a.get("static") or [])})
        for env, exp in ctl["expected"].items():
            o = a["outcomes"].get(env)
            n_env += 1
            got = bool(o and o["fired"])
            if got == exp["fired"]:
                agree += 1
            else:
                disagreements.append({"name": ctl["name"], "env": env, "expected": exp["fired"], "observed": got})
            if got and exp["fired"]:
                err = _gap_h(o["first_tod_h"], exp["first_tod_h"]) * 60
                errors.append(err)
                kind_errors.setdefault(ctl["kind"], []).append(err)
    for kind, k in by_kind.items():
        k["first_time"] = _stats(kind_errors.get(kind, []))
    rate = detected / expected if expected else None
    first = _stats(errors)
    ok = (rate is not None and rate >= CRITERIA["detection_rate_min"] and len(fps) <= CRITERIA["false_positives_max"]
          and first["mae_min"] is not None and first["mae_min"] <= CRITERIA["first_time_mae_min_max"])
    return {"n": len(controls), "expected_divergences": expected, "detected": detected, "detection_rate": rate,
            "by_divergence_kind": by_div, "by_automation_kind": dict(sorted(by_kind.items())),
            "false_positives": fps, "missed": missed, "not_run": not_run, "first_time": first,
            "fired_agreement": {"n": n_env, "agree": agree}, "fired_disagreements": disagreements,
            "static_mismatches": static_mismatch, "criteria": {k: v for k, v in CRITERIA.items()
                                                               if not k.startswith("negative")},
            "pass": ok}


def score_negative(controls: list[dict], result: dict) -> dict:
    """The sensor-path negative control against a sweep result: nothing may be flagged; anything that fires is
    listed with its environments."""
    autos = result["automations"]
    ran, not_run, flagged, fired = 0, [], [], []
    for ctl in controls:
        a = autos.get(ctl["hash"])
        if a is None or a.get("bound") is None or not a.get("outcomes"):
            not_run.append(ctl["name"])
            continue
        ran += 1
        if a["divergences"]:
            flagged.append({"name": ctl["name"], "kind": ctl["kind"], "divergences": sorted(a["divergences"])})
        envs = sorted(e for e, o in a["outcomes"].items() if o["fired"])
        if envs:
            fired.append({"name": ctl["name"], "kind": ctl["kind"], "envs": envs})
    return {"n": len(controls), "ran": ran, "not_run": not_run, "flagged": flagged, "fired": fired,
            "flag_rate": len(flagged) / ran if ran else None,
            "criteria": {"flagged_max": CRITERIA["negative_flagged_max"]},
            "pass": ran > 0 and len(flagged) <= CRITERIA["negative_flagged_max"]}


# ---- the corpora ----------------------------------------------------------------------------------------------------

def _hms(seconds: float) -> str:
    sign, s = ("-" if seconds < 0 else ""), int(round(abs(seconds)))
    return f"{sign}{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _auto(name: str, triggers: list, conditions: list | None = None) -> dict:
    a = {"alias": name, "trigger": triggers}
    if conditions:
        a["condition"] = conditions
    a["action"] = [dict(ACTION)]
    return a


def _hash(auto: dict) -> str:
    from vesper.edt.corpus import automation_hash
    return automation_hash(auto)


def negative_controls() -> list[dict]:
    """The sensor-path negative control (protocol A4.2): automations that read a sensor and can never fire."""
    def ns(entity, **bound):
        return {"platform": "numeric_state", "entity_id": entity, **bound}

    def st(entity, to):
        return {"platform": "state", "entity_id": entity, "to": to}
    spec = [
        ("numeric_state", [ns("sensor.porch_temperature", below=-25)], None),
        ("numeric_state", [ns("sensor.kitchen_temperature", below=-25)], None),
        ("numeric_state", [ns("sensor.living_room_temperature", below=-25)], None),
        ("numeric_state", [ns("sensor.bedroom_temperature", below=-25)], None),
        ("numeric_state", [ns("sensor.office_temperature", below=-25)], None),
        ("numeric_state", [ns("sensor.porch_humidity", below=2)], None),
        ("numeric_state", [ns("sensor.bathroom_humidity", below=2)], None),
        ("numeric_state", [ns("sensor.porch_humidity", above=100.5)], None),
        ("numeric_state", [ns("sensor.porch_illuminance", above=10_000_000)], None),
        ("numeric_state", [ns("sensor.living_room_illuminance", above=10_000_000)], None),
        ("numeric_state", [ns("sensor.kitchen_illuminance", below=-1)], None),
        ("state", [st("sensor.kitchen_temperature", "boiling")], None),
        ("state", [st("sensor.porch_illuminance", "dark")], None),
        ("state", [st("binary_sensor.hall_motion", "jammed")], None),
        ("time_numeric_condition", [{"platform": "time", "at": "12:00:00"}],
         [{"condition": "numeric_state", "entity_id": "sensor.porch_temperature", "below": -25}]),
        ("sun_numeric_condition", [{"platform": "sun", "event": "sunset"}],
         [{"condition": "numeric_state", "entity_id": "sensor.living_room_illuminance", "above": 10_000_000}]),
    ]
    out = []
    for i, (kind, triggers, conditions) in enumerate(spec):
        a = _auto(f"edt-control negative {i:02d} {kind}", triggers, conditions)
        out.append({"name": a["alias"], "kind": kind, "automation": a, "hash": _hash(a)})
    return out


def positive_candidates(pooled_porch_temperature: list[float]) -> list[dict]:
    """Every positive-control candidate before the boundary check: {name, kind, automation, spec}. `spec` is what
    the expectation is computed from. Porch-temperature thresholds sit at pooled percentiles, half-way between
    0.1 C steps (no report can equal one)."""
    out = []

    def add(kind, triggers, conditions, spec):
        a = _auto(f"edt-control positive {len(out):02d} {kind}", triggers, conditions)
        out.append({"name": a["alias"], "kind": kind, "automation": a, "spec": spec})

    for event in ("sunrise", "sunset"):
        for off_min in (-70, -25, 0, 20, 65):
            off = off_min * 60.0
            add("sun_trigger", [{"platform": "sun", "event": event, "offset": _hms(off)}], None,
                {"trigger": ("sun", event, off)})
    for at in ("06:15:00", "09:40:00", "13:05:00", "18:20:00", "22:50:00"):
        h, m, s = (int(p) for p in at.split(":"))
        add("time_trigger", [{"platform": "time", "at": at}], None, {"trigger": ("time", h * 3600 + m * 60 + s)})
    for at, side, event, off_min in (("05:10", "after", "sunrise", 0), ("06:20", "after", "sunrise", 0),
                                     ("07:25", "after", "sunrise", 0), ("08:35", "after", "sunrise", 0),
                                     ("12:00", "after", "sunrise", 0), ("07:05", "after", "sunrise", 30),
                                     ("06:45", "after", "sunrise", -40),
                                     ("16:25", "before", "sunset", 0), ("17:35", "before", "sunset", 0),
                                     ("18:40", "before", "sunset", 0), ("19:50", "before", "sunset", 0),
                                     ("21:15", "before", "sunset", 0), ("20:10", "before", "sunset", -30),
                                     ("17:00", "before", "sunset", 45)):
        h, m = (int(p) for p in at.split(":"))
        cond = {"condition": "sun", side: event}
        if off_min:
            cond[f"{side}_offset"] = _hms(off_min * 60)
        add("time_sun_condition", [{"platform": "time", "at": f"{at}:00"}], [cond],
            {"trigger": ("time", h * 3600 + m * 60),
             "sun_condition": {side: event, f"{side}_offset": off_min * 60.0}})
    for event, off_min, after, before in (("sunrise", 0, "06:00", "07:30"), ("sunrise", 0, "04:00", "06:30"),
                                          ("sunrise", -30, "06:30", "09:00"), ("sunset", 0, "17:00", "19:00"),
                                          ("sunset", 0, "19:30", "23:00"), ("sunset", 20, "15:00", "17:45"),
                                          ("sunset", 0, "20:30", "02:00")):
        off = off_min * 60.0
        a_s, b_s = (int(x.split(":")[0]) * 3600 + int(x.split(":")[1]) * 60 for x in (after, before))
        trig = {"platform": "sun", "event": event}
        if off_min:
            trig["offset"] = _hms(off)
        add("sun_time_condition", [trig], [{"condition": "time", "after": f"{after}:00", "before": f"{before}:00"}],
            {"trigger": ("sun", event, off), "time_condition": (a_s, b_s)})
    values = sorted(pooled_porch_temperature)
    for p in (5, 15, 30, 50, 70, 85, 95) if values else ():
        q = values[int(round(p / 100 * (len(values) - 1)))]
        threshold = round(math.floor(round(q * 10, 6)) / 10 + 0.05, 2)
        if not -30 <= threshold <= 35:  # the binder's ambient range (protocol A2.5)
            continue
        for side in ("below", "above"):
            add("numeric_state", [{"platform": "numeric_state", "entity_id": SERIES["porch_temperature"][0],
                                   side: threshold}], None,
                {"trigger": ("numeric", "porch_temperature", side, threshold), "percentile": p})
    for series, side, threshold in (("living_illuminance", "below", 20.5), ("living_illuminance", "below", 100.5),
                                    ("living_illuminance", "above", 300.5), ("living_illuminance", "above", 1500.5),
                                    ("porch_illuminance", "below", 500.5), ("porch_illuminance", "above", 20000.5),
                                    ("porch_illuminance", "above", 80000.5)):
        add("numeric_state", [{"platform": "numeric_state", "entity_id": SERIES[series][0], side: threshold}], None,
            {"trigger": ("numeric", series, side, threshold)})
    return out


def expected_times(spec: dict, env: dict) -> tuple[list[float], float]:
    """(firing times, smallest margin to a condition boundary in seconds) of one candidate in one environment.
    `env`: {"events": the run days' sun events, "series": {name: (initial, [(delivery t, value)])}}."""
    trig = spec["trigger"]
    if trig[0] == "sun":
        times = [t for e in env["events"] if e.get(trig[1]) is not None
                 for t in [e[trig[1]] + trig[2]]]
    elif trig[0] == "time":
        times = [d * DAY_S + trig[1] for d in range(RUN_DAYS)]
    else:
        _, series, side, threshold = trig
        initial, reports = env["series"][series]
        times = numeric_firings(initial, reports, **{side: threshold})
    margin = math.inf
    if "sun_condition" in spec:
        cond = spec["sun_condition"]
        candidates = [t for t in times if 0 <= t < RUN_DAYS * DAY_S]
        margin = min((sun_condition_margin(env["events"], t, cond) for t in candidates), default=math.inf)
        times = [t for t in candidates if sun_condition_ok(env["events"], t, cond)]
    if "time_condition" in spec:
        a_s, b_s = spec["time_condition"]
        margin = min([margin] + [time_condition_margin(t, a_s, b_s) for t in times])
        times = [t for t in times if time_condition_ok(t, a_s, b_s)]
    return times, margin


def positive_controls(env_data: dict) -> tuple[list[dict], list[dict]]:
    """(controls, dropped): every candidate's expected outcome per environment and expected divergences; a candidate
    near a boundary is dropped with its reason (protocol A4.1)."""
    pooled = [v for env in env_data.values() for t, v in env["series"]["porch_temperature"][1]
              if WINDOW[0] <= t < WINDOW[1]]
    keep, dropped = [], []
    for cand in positive_candidates(pooled):
        expected, margin, ambiguous_flaps = {}, math.inf, False
        for key, env in env_data.items():
            times, m = expected_times(cand["spec"], env)
            margin = min(margin, m)
            measured = _in_window(times)
            ambiguous_flaps |= _flaps(measured, FLAP_WINDOW_S - MARGIN_S) != _flaps(measured, FLAP_WINDOW_S + MARGIN_S)
            expected[key] = expected_outcome(times)
        divs, static = expected_divergences(expected)
        spread = spread_margin_h(expected)
        why = None
        if margin < MARGIN_S:
            why = f"a condition boundary {margin / 60:.1f} min from a firing"
        elif spread is not None and spread < SPREAD_MARGIN_H:
            why = f"first-firing spread {spread * 60:.1f} min from 3 h"
        elif ambiguous_flaps:
            why = "flapping depends on the window's exact length"
        spec = {k: list(v) if isinstance(v, tuple) else v for k, v in cand["spec"].items()}
        entry = {"name": cand["name"], "kind": cand["kind"], "automation": cand["automation"],
                 "hash": _hash(cand["automation"]), "spec": spec}
        if why:
            dropped.append({**entry, "reason": why})
            continue
        keep.append({**entry, "expected": expected, "divergences": sorted(divs), "static": sorted(static),
                     "margin_min": None if margin == math.inf else margin / 60,
                     "spread_margin_min": None if spread is None else spread * 60})
    return keep, dropped


# ---- reading traces, running ----------------------------------------------------------------------------------------

def trace_series(trace: Path) -> tuple[dict, dict]:
    """(header, {series name: (initial value, [(delivery time, value)])}) of a trace: the reports of SERIES' sensors
    up to the run's end, in `observed_t` order (stable), each delivered REPORT_DELAY_S after it."""
    want = {(twin, feature): name for name, (_, twin, feature) in SERIES.items()}
    with gzip.open(trace, "rt") as f:
        header = json.loads(next(f))
        reports = [json.loads(line) for line in f]
    reports.sort(key=lambda r: r["observed_t"])
    initial = header.get("initial_state") or {}
    out = {name: ((initial.get(twin) or {}).get(feature), []) for (twin, feature), name in want.items()}
    for r in reports:
        name = want.get((r["twin_id"], r["feature"]))
        if name is not None and r["observed_t"] <= WINDOW[1]:
            out[name][1].append((r["observed_t"] + REPORT_DELAY_S, r["value"]))
    return header, out


def _write_corpus(directory: Path, name: str, controls: list[dict]) -> Path:
    raw = directory / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for old in raw.glob("*.yaml"):
        old.unlink()
    (raw / f"vesper~{name}##automations.yaml").write_text(
        json.dumps([c["automation"] for c in controls], indent=1) + "\n")  # JSON is YAML
    return directory


def _check_binds(controls: list[dict], roles: dict) -> None:
    from vesper.edt.bind import Unbindable, bind
    bad = {c["name"]: b.reason for c in controls if isinstance(b := bind(c["automation"], roles, 0), Unbindable)}
    if bad:
        raise ValueError(f"synthetic controls that do not bind: {bad}")
    hashes = [c["hash"] for c in controls]
    if len(set(hashes)) != len(hashes):
        raise ValueError("two synthetic controls are the same automation")


def _observed(controls: list[dict], result: dict) -> dict:
    autos = result["automations"]
    return {c["name"]: {k: autos.get(c["hash"], {}).get(k) for k in
                        ("outcomes", "divergences", "static", "refused", "unbindable", "approximations")}
            for c in controls}


def run_controls(work, workers: int = 2, *, catalog_dir: str = "catalog/vds", fetch: bool = True, envs=None,
                 log=None) -> dict:
    """Both controls through the sweep (rung 4, `envs`: default every environment), scored. The negative control
    runs first and records (or reuses) the traces the positive control's expectations are computed from."""
    from vesper.edt.home import build_edt_home
    from vesper.edt.sweep import sweep
    from vesper.edt.trace import ENVIRONMENTS, env_site
    from vesper.env.sun import sun_events
    log = log or (lambda _msg: None)
    work = Path(work)
    envs = list(ENVIRONMENTS if envs is None else envs)
    _, roles = build_edt_home(work, catalog_dir)
    controls_dir = work / "controls"

    negative = negative_controls()
    _check_binds(negative, roles)
    log(f"negative control: {len(negative)} automations")
    neg_result = sweep(_write_corpus(controls_dir / "negative", "controls-negative", negative), work, envs, rung=4,
                       workers=workers, fetch=fetch, catalog_dir=catalog_dir, log=log)

    env_data = {}
    for env in envs:
        header, series = trace_series(work / "cache" / f"trace-{env.key}.jsonl.gz")
        if header["env"]["key"] != env.key:
            raise ValueError(f"trace for {env.key} names {header['env']['key']}")
        site = env_site(env)
        env_data[env.key] = {"events": [sun_events(site, d) for d in range(RUN_DAYS)], "series": series}
    positive, dropped = positive_controls(env_data)
    _check_binds(positive, roles)
    log(f"positive control: {len(positive)} automations ({len(dropped)} candidates dropped at a boundary)")
    pos_result = sweep(_write_corpus(controls_dir / "positive", "controls-positive", positive), work, envs, rung=4,
                       workers=workers, fetch=fetch, catalog_dir=catalog_dir, log=log)

    pos_score, neg_score = score_positive(positive, pos_result), score_negative(negative, neg_result)
    return {
        "protocol": PROTOCOL, "rung": 4, "envs": [e["key"] for e in pos_result["envs"]],
        "failed_envs": {"positive": pos_result.get("failed_envs") or {},
                        "negative": neg_result.get("failed_envs") or {}},
        "positive": {"score": pos_score, "controls": positive, "dropped": dropped,
                     "observed": _observed(positive, pos_result)},
        "negative": {"score": neg_score, "controls": negative, "observed": _observed(negative, neg_result)},
        "pass": {"positive": pos_score["pass"], "negative": neg_score["pass"]},
    }
