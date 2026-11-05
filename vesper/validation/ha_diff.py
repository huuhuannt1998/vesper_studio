"""Differential test of VESPER's automation engine against unmodified Home Assistant (protocol Addendum 10).

The sweeps' screening replays each bound public automation in VESPER's own engine (`vesper.edt.replay`, the engine of
`vesper.lab.automations`). This module replays a sample of the same bound automations, on the same inputs, in Home
Assistant 2026.10.2's own automation integration, and compares what the two did.

Same inputs. For an environment's cached trace, VESPER's engine is run twice: once with no automation, to write down
every entity state change it derives from the trace (the trace's reports through the home's MQTT discovery: the
open-loop input stream, the same whatever the automations do), and once with the sample (the firings, exactly as the
sweep replays them). Home Assistant then runs in its own venv (`ha_diff_driver.py`, imported by nothing here) on a
virtual clock from the same local midnight: every entity but `sun.sun` starts in the state VESPER's engine started it
in and changes when VESPER's did; `sun.sun` is Home Assistant's own `sun` integration, at the site. Its service calls
are recorded and executed by nothing (the replay is open loop on both sides).

What is compared, per (automation, environment), over the judged window (days 1 and 2, as the oracle judges):
whether it fires; the first firing's time (VESPER minus Home Assistant); the set of (service, target) calls and the
number of distinct firing instants; a light's requested brightness where both have one. A firing is one action call
on one target (VESPER's `Firing`); Home Assistant's `homeassistant.turn_on/off/toggle` is counted as the calls it makes
(`light.turn_on` ...), as VESPER's engine records them.

`python -m vesper.validation.ha_diff --sweep FILE --work DIR --out FILE [--catalog DIR] [--ha-python PATH]`.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import gzip
import json
import math
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from vesper.edt.types import Env, Firing

DAY_S = 86400.0
SEED = 20261105
# Addendum 10: the four environments (January and July, both hemispheres, a high latitude)
ENVS = (Env("Minneapolis, MN, USA", "2026-01-14", 0.0, 4), Env("Stockholm, Sweden", "2026-07-15", 180.0, 4),
        Env("Sydney, Australia", "2026-01-14", 0.0, 4), Env("Sydney, Australia", "2026-07-15", 180.0, 4))
DEV_ENV = Env("Phoenix, AZ, USA", "2026-04-15", 0.0, 4)  # the harness shakedown only (never reported)
# Strata in priority order: (name, size); an automation belongs to the first whose rule it meets
STRATA = (("time_spread_frame_flag", None), ("frame_flag", 40), ("sun_offset", 30), ("numeric_state", 30),
          ("time_pattern", 25), ("template", 30), ("for", 30), ("choose", 25), ("sun_other", 25), ("other", 20))
FIRING_SHARE = 0.75  # within strata 3-10: this share from automations VESPER fires in at least one of the four
SUBSECOND_S = 1.0
AGREE_FIRST_S = 60.0
HA_SERVICES = {  # the services Home Assistant's own integrations register for the home's entity domains
    "light": ("turn_on", "turn_off", "toggle"), "switch": ("turn_on", "turn_off", "toggle"),
    "lock": ("lock", "unlock", "open"),
    "climate": ("turn_on", "turn_off", "toggle", "set_hvac_mode", "set_temperature", "set_preset_mode",
                "set_fan_mode", "set_humidity", "set_swing_mode", "set_swing_horizontal_mode")}
HA_VERBS = ("turn_on", "turn_off", "toggle")
# Services that would end or reload the harness's Home Assistant (a restart, a reload of the YAML it has no file
# for): replaced by handlers that do nothing, so the call is recorded and the run goes on (Addendum 10, A10.1)
HA_NOOP = {"homeassistant": ["restart", "stop", "reload_all", "reload_core_config", "reload_custom_templates",
                             "reload_config_entry"], "automation": ["reload"]}
DRIVER = Path(__file__).with_name("ha_diff_driver.py")


# ---- what an automation uses ----------------------------------------------------------------------------------------

def _listed(x) -> list:
    return [] if x is None else x if isinstance(x, list) else [x]


def _first(d: dict, *keys):
    return next((d[k] for k in keys if d.get(k) is not None), None)


def _has_template(node) -> bool:
    if isinstance(node, str):
        return "{{" in node or "{%" in node
    if isinstance(node, dict):
        return any(_has_template(v) for k, v in node.items() if k not in ("id", "alias"))
    if isinstance(node, list):
        return any(_has_template(v) for v in node)
    return False


def _walk_actions(steps):
    for s in _listed(steps):
        if not isinstance(s, dict):
            continue
        yield s
        for key in ("sequence", "then", "else", "default"):
            if key in s:
                yield from _walk_actions(s[key])
        if isinstance(s.get("choose"), list):
            for o in s["choose"]:
                if isinstance(o, dict):
                    yield from _walk_actions(o.get("sequence"))
        if isinstance(s.get("repeat"), dict):
            yield from _walk_actions(s["repeat"].get("sequence"))


def _conditions_walk(c):
    if isinstance(c, list):
        for x in c:
            yield from _conditions_walk(x)
    elif isinstance(c, dict):
        yield c
        for key in ("conditions", "and", "or", "not"):
            if key in c:
                yield from _conditions_walk(c[key])


def features(bound: dict) -> set[str]:
    """The trigger kinds and constructs an automation uses (Addendum 10, A10.2): sun_offset (a sun trigger with a
    non-zero offset), sun_other (another sun trigger, a state or numeric_state trigger on sun.sun, a sun condition),
    numeric_state, time_pattern, template (a template trigger, a template condition, a value_template), for (a
    trigger `for:`), choose (choose or if in the actions), and the other trigger platforms by name."""
    out: set[str] = set()
    if not isinstance(bound, dict):
        return out
    triggers = [t for t in _listed(_first(bound, "trigger", "triggers")) if isinstance(t, dict)]
    conditions = _listed(_first(bound, "condition", "conditions"))
    for t in triggers:
        kind = str(t.get("platform") or t.get("trigger") or "")
        out.add(f"trigger:{kind}")
        if kind == "sun":
            off = t.get("offset")
            from vesper.edt.oracle import _offset_s
            out.add("sun_offset" if off is not None and (_offset_s(off) or 0) != 0 else "sun_other")
        elif kind in ("state", "numeric_state") and any(str(e).startswith("sun.") for e in _listed(t.get("entity_id"))):
            out.add("sun_other")
        if kind == "numeric_state":
            out.add("numeric_state")
        if kind == "time_pattern":
            out.add("time_pattern")
        if kind == "template" or "value_template" in t:
            out.add("template")
        if t.get("for") is not None:
            out.add("for")
    for c in _conditions_walk(conditions):
        if c.get("condition") == "sun":
            out.add("sun_other")
        if c.get("condition") == "template" or "value_template" in c:
            out.add("template")
    if any(isinstance(c, str) and _has_template(c) for c in conditions):
        out.add("template")
    for s in _walk_actions(_first(bound, "action", "actions")):
        if "choose" in s or "if" in s:
            out.add("choose")
    return out


def stratum_rule(name: str, a: dict, feats: set[str]) -> bool:
    frame = a.get("frame_divergences") or []
    if name == "time_spread_frame_flag":
        return "time_spread" in frame
    if name == "frame_flag":
        return bool(frame)
    if name == "other":
        return True
    return name in feats


def fires_in(a: dict, envs) -> bool:
    return any((a["outcomes"].get(e.key) or {}).get("fired") for e in envs)


def draw_sample(autos: dict, envs=ENVS, seed: int = SEED) -> list[tuple[str, str]]:
    """(automation id, stratum) for the sample of Addendum 10 (A10.2): the eligible automations (an outcome in every
    one of `envs`) assigned to their first stratum; every time_spread frame flag; then each stratum's size drawn with
    `seed`, frame flags at random, the others FIRING_SHARE of the draw from those VESPER fires in some environment of
    `envs` and the rest from those it fires in none (a short side is filled from the other)."""
    rng = random.Random(seed)
    eligible = sorted(k for k, a in autos.items() if a.get("bound") is not None
                      and all(e.key in a["outcomes"] for e in envs))
    pools: dict[str, list[str]] = defaultdict(list)
    for k in eligible:
        a = autos[k]
        feats = features(a["bound"])
        pools[next(n for n, _ in STRATA if stratum_rule(n, a, feats))].append(k)
    out: list[tuple[str, str]] = []
    for name, size in STRATA:
        pool = pools.get(name, [])
        if size is None or len(pool) <= size:
            out.extend((k, name) for k in pool)
            continue
        if name == "frame_flag":
            out.extend((k, name) for k in sorted(rng.sample(pool, size)))
            continue
        firing = [k for k in pool if fires_in(autos[k], envs)]
        dead = [k for k in pool if not fires_in(autos[k], envs)]
        n_fire = min(len(firing), round(size * FIRING_SHARE))
        n_dead = min(len(dead), size - n_fire)
        n_fire = min(len(firing), size - n_dead)
        picked = rng.sample(firing, n_fire) + rng.sample(dead, n_dead)
        out.extend((k, name) for k in sorted(picked))
    return out


# ---- VESPER's side --------------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _recording_engine(sink: dict):
    """Within the block, the replay's engine writes down its start states and every entity state change it applies."""
    from vesper.lab import automations as engine_module
    base = engine_module.Engine

    class Recording(base):
        def start(self):
            super().start()
            sink["initial"] = {e: [s, dict(self._attrs.get(e, {}))] for e, s in self._state.items()}
            sink["platform"] = {e.entity_id: e.platform for e in self.entities.values()}
            sink["entities"] = {e.entity_id: {"platform": e.platform, "scale": e.cfg.get("brightness_scale", 255)}
                                for e in self.entities.values()}

        def _apply(self, changes):
            now = self.session.clock.now()
            for c in changes:
                if c[1] != c[2] or c[3] != c[4]:
                    sink.setdefault("stream", []).append([now, c[0], c[2], dict(c[4] or {})])
            super()._apply(changes)

    engine_module.Engine = Recording
    try:
        yield
    finally:
        engine_module.Engine = base


def input_stream(home_yaml: Path, trace: Path, catalog_dir: str) -> dict:
    """VESPER's engine with no automation over `trace`: {"initial": {entity: [state, attrs]}, "stream": [[t, entity,
    state, attrs], ...], "entities": {entity: {platform, scale}}}; `sun.sun` is in both (Home Assistant computes its
    own; VESPER's is kept to explain a discrepancy)."""
    from vesper.edt.replay import replay_detailed
    sink: dict = {"stream": []}
    with _recording_engine(sink):
        replay_detailed(home_yaml, trace, {}, catalog_dir=catalog_dir)
    return sink


def vesper_firings(home_yaml: Path, trace: Path, bound: dict[str, dict], catalog_dir: str):
    from vesper.edt.replay import replay_detailed
    return replay_detailed(home_yaml, trace, bound, catalog_dir=catalog_dir)


def _site_header(trace: Path) -> dict:
    with gzip.open(trace, "rt") as f:
        return json.loads(f.readline())


def _iso_event(t0: float, t: float) -> str:
    return dt.datetime.fromtimestamp(t0 + t, dt.UTC).isoformat(timespec="milliseconds")


def ha_job(header: dict, inputs: dict, labelled: list[dict], end: float, seed: int = SEED) -> dict:
    """The driver's job: the site, virtual time 0 at the trace's local midnight, every non-sun entity's start state
    and changes (an event entity's state is the time of its event, as Home Assistant's event entities have it), the
    services Home Assistant's own integrations would register for the home's domains, and the automations."""
    from vesper.env.site import parse_site
    site = parse_site(header["site"])
    t0 = site.utc(0).timestamp()
    platform = {e: v["platform"] for e, v in inputs["entities"].items()}
    initial = {e: sa for e, sa in inputs["initial"].items() if e != "sun.sun"}
    stream = []
    for t, e, s, attrs in inputs["stream"]:
        if e == "sun.sun" or t > end:
            continue
        stream.append([t, e, _iso_event(t0, t) if platform.get(e) == "event" else s, attrs])
    domains = sorted({e.split(".", 1)[0] for e in initial})
    services = {d: list(HA_SERVICES[d]) for d in domains if d in HA_SERVICES}
    return {"t0": t0, "end": end, "seed": seed, "integrations": sorted(services), "services": services,
            "noop": HA_NOOP,
            "site": {"lat": site.lat, "lon": site.lon, "elevation_m": site.elevation_m, "tz": site.tz},
            "automations": labelled, "initial": initial, "stream": stream}


def run_ha(job: dict, ha_python: Path, workdir: Path, timeout: float = 3600.0) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    jp, op = workdir / "job.json", workdir / "out.json"
    jp.write_text(json.dumps(job))
    op.unlink(missing_ok=True)
    cmd = ["nice", "-n", "5", str(ha_python), "-I", str(DRIVER), str(jp), str(op)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not op.is_file():
        raise RuntimeError(f"the Home Assistant driver wrote nothing (exit {p.returncode}): {p.stderr[-2000:]}")
    out = json.loads(op.read_text())
    if out.get("error"):
        raise RuntimeError(f"the Home Assistant driver failed: {out['error']}")
    return out


# ---- comparing ------------------------------------------------------------------------------------------------------

def ha_firings(out: dict, label_of: dict[str, str], lo: float, hi: float) -> dict[str, list[Firing]]:
    """Home Assistant's calls as VESPER's Firings, per automation id: one per (call, target), a call with no target
    once with target ''; a top-level homeassistant.turn_on/off/toggle replaced by the calls it made (nested, same
    context and instant). `lo`/`hi`: none dropped here (the oracle filters the judged window)."""
    out_f: dict[str, list[Firing]] = defaultdict(list)
    for t, alias, domain, service, ids, data, nested, parent, refused in out["calls"]:
        key = label_of.get(alias) if alias is not None else None
        if key is None or refused:  # a call its service refused did not run (and stopped the run)
            continue
        top_ha = domain == "homeassistant" and service in HA_VERBS
        if (nested and not (parent or "").startswith("homeassistant.")) or (not nested and top_ha):
            continue
        name = f"{domain}.{service}"
        for target in ids or [""]:
            out_f[key].append(Firing(t, key, name, target, tuple(sorted((data or {}).items(), key=str))))
    return {k: sorted(v, key=lambda f: f.t) for k, v in out_f.items()}


def _in(firings, lo, hi):
    return [f for f in firings if lo <= f.t < hi]


def _brightness(f: Firing, scale: float | None, side: str) -> float | None:
    """The brightness (0..255) a light.turn_on asked for: VESPER's device value scaled back, or Home Assistant's
    service data."""
    if f.service != "light.turn_on":
        return None
    d = dict(f.data or ())
    if side == "vesper":
        v = d.get("brightness")
        return None if v is None or not scale else round(float(v) * 255 / scale)
    if d.get("brightness") is not None:
        try:
            return min(max(int(float(d["brightness"])), 0), 255)
        except (TypeError, ValueError):
            return None
    if d.get("brightness_pct") is not None:
        try:
            return round(float(d["brightness_pct"]) * 255 / 100)
        except (TypeError, ValueError):
            return None
    return None


def compare_pair(v: list[Firing], h: list[Firing], lo: float, hi: float, scales: dict) -> dict:
    """One (automation, environment): fired, first times, Δ = VESPER - HA, call sets, instant counts, brightness."""
    vm, hm = _in(v, lo, hi), _in(h, lo, hi)
    rec = {"v_fired": bool(vm), "h_fired": bool(hm), "v_first": vm[0].t if vm else None,
           "h_first": hm[0].t if hm else None, "v_count": len({f.t for f in vm}), "h_count": len({f.t for f in hm})}
    rec["delta_s"] = rec["v_first"] - rec["h_first"] if vm and hm else None
    vc, hc = {(f.service, f.target) for f in vm}, {(f.service, f.target) for f in hm}
    rec["calls_equal"] = vc == hc
    rec["calls_only_v"] = sorted(map(list, vc - hc))[:5]
    rec["calls_only_h"] = sorted(map(list, hc - vc))[:5]
    vb = sorted({b for f in vm if (b := _brightness(f, scales.get(f.target), "vesper")) is not None})
    hb = sorted({b for f in hm if (b := _brightness(f, None, "ha")) is not None})
    rec["brightness_comparable"] = bool(vb and hb)
    rec["brightness_equal"] = vb == hb if vb and hb else None
    return rec


def outcome_pair(v: list[Firing], h: list[Firing], site) -> tuple:
    from vesper.edt.oracle import outcome, rich_outcome
    return (dataclasses.asdict(outcome(v, site)), rich_outcome(v), dataclasses.asdict(outcome(h, site)),
            rich_outcome(h))


# ---- one environment ------------------------------------------------------------------------------------------------

def run_env(env: Env, sample: list[str], autos: dict, work: Path, scratch: Path, catalog_dir: str,
            ha_python: Path, log=print) -> dict:
    """Both engines over one environment's cached trace: per automation, the comparison record, VESPER's and Home
    Assistant's oracle outcomes, the sweep's cached outcome (the reproduction check) and HA's notes."""
    from vesper.edt.oracle import site_of
    from vesper.env.outdoor import RUN_DAYS
    home_yaml = work / "edt" / "home.yaml"
    trace = work / "cache" / f"trace-{env.key}-rho_device.jsonl.gz"
    if not trace.is_file():
        raise FileNotFoundError(f"no cached trace {trace}")
    end = RUN_DAYS * DAY_S
    lo, hi = DAY_S, end
    t = time.monotonic()
    inputs = input_stream(home_yaml, trace, catalog_dir)
    bound = {k: autos[k]["bound"] for k in sample}
    rep = vesper_firings(home_yaml, trace, bound, catalog_dir)
    log(f"{env.key}: VESPER {time.monotonic() - t:.1f} s, {len(inputs['stream'])} state changes")
    labelled, label_of = [], {}
    for i, k in enumerate(sample):
        label_of[f"edt:{i}"] = k
        labelled.append({**bound[k], "alias": f"edt:{i}"})
    job = ha_job(_site_header(trace), inputs, labelled, end)
    t = time.monotonic()
    out = run_ha(job, ha_python, scratch / env.key)
    log(f"{env.key}: Home Assistant {time.monotonic() - t:.1f} s ({out.get('wall_s')} s in the driver), "
        f"{len(out['calls'])} calls, {len(out['unavailable'])} unavailable")
    hf = ha_firings(out, label_of, lo, hi)
    unavailable = {label_of[a] for a in out["unavailable"] if a in label_of}
    scales = {e: v["scale"] for e, v in inputs["entities"].items() if v["platform"] == "light"}
    site = site_of(env)
    notes = defaultdict(list)
    unattributed = []
    for row in out["log"]:
        k = _log_owner(row, label_of)
        if k is None:
            unattributed.append(row)
        else:
            notes[k].append(row[:3] + [row[3][:300]])
    refused_calls = Counter(label_of.get(c[1]) for c in out["calls"] if c[8] and c[1] in label_of)
    per = {}
    for k in sample:
        v = rep.firings.get(k, [])
        h = hf.get(k, [])
        rec = compare_pair(v, h, lo, hi, scales)
        vo, vr, ho, hr = outcome_pair(v, h, site)
        cached = autos[k]
        rec.update(v_outcome=vo, h_outcome=ho, v_rich=vr, h_rich=hr, ha_unavailable=k in unavailable,
                   vesper_refused=rep.refused.get(k), notes=notes.get(k, [])[:10], n_notes=len(notes.get(k, [])),
                   ha_refused_calls=refused_calls.get(k, 0), approximations=rep.approximations.get(k, []),
                   reproduces=(cached["outcomes"].get(env.key) == vo
                               and (cached.get("rich", {}).get(env.key) or {}).get("first_t") == vr["first_t"]
                               and (cached.get("rich", {}).get(env.key) or {}).get("count") == vr["count"]
                               and (cached.get("rich", {}).get(env.key) or {}).get("payload") == vr["payload"]))
        rec["v_firings"] = [[f.t, f.service, f.target] for f in _in(v, lo, hi)][:20]
        rec["h_firings"] = [[f.t, f.service, f.target] for f in _in(h, lo, hi)][:20]
        per[k] = rec
    sun_v = [[t, s] for t, e, s, _ in inputs["stream"] if e == "sun.sun"]
    return {"env": env.key, "per": per, "ha_version": out.get("ha_version"), "ha_wall_s": out.get("wall_s"),
            "n_state_changes": len(job["stream"]), "sun_vesper": _sun_changes(sun_v),
            "sun_ha": _sun_changes(out.get("sun", [])), "log_total": len(out["log"]),
            "log_unattributed": unattributed[:50], "n_log_unattributed": len(unattributed)}


def _log_owner(row: list, label_of: dict[str, str]) -> str | None:
    """The automation a Home Assistant log line is about: its logger or message names `automation.edt_<i>` or the
    alias `edt:<i>`."""
    import re
    text = f"{row[2]} {row[3]}"
    m = re.search(r"automation\.edt_(\d+)\b", text) or re.search(r"\bedt:(\d+)\b", text)
    return label_of.get(f"edt:{m.group(1)}") if m else None


def _sun_changes(rows) -> list:
    """The sun.sun state transitions [t, state] (attribute-only updates dropped)."""
    out, last = [], None
    for t, s in rows:
        if s != last:
            out.append([t, s])
            last = s
    return out


# ---- the flags ------------------------------------------------------------------------------------------------------

def _divergences(entry: dict, outs: dict, rich: dict, envs_by_key: dict):
    """The sweep's clock and frame divergences of one automation from (possibly substituted) outcomes."""
    from vesper.edt.oracle import divergences, frame_divergences, frames_of
    from vesper.edt.sweep import ENVELOPE
    from vesper.edt.types import Outcome
    o = {k: Outcome(**v) for k, v in outs.items()}
    kinds = divergences(o)
    static = {k for k in ENVELOPE if k in kinds and all(getattr(x, k) for x in o.values())}
    by_env = {envs_by_key[k]: x for k, x in o.items()}
    first_t = {envs_by_key[k]: (rich.get(k) or {}).get("first_t") for k in o}
    frame = frame_divergences(by_env, first_t, frames_of(entry["bound"])) - static
    return sorted(kinds - static), sorted(frame)


def substituted_flags(entry: dict, ha_by_env: dict, envs_by_key: dict) -> dict:
    """A10.5(a): the automation's flags with Home Assistant's outcome in place of VESPER's in each compared
    environment (the other environments keep the sweep's); with the sweep's own outcomes it reproduces the sweep."""
    outs = {k: dict(v) for k, v in entry["outcomes"].items()}
    rich = {k: dict(v) for k, v in (entry.get("rich") or {}).items()}
    for k, (ho, hr) in ha_by_env.items():
        outs[k], rich[k] = ho, hr
    clock, frame = _divergences(entry, outs, rich, envs_by_key)
    return {"clock": clock, "frame": frame}


def _shift_outcome(o: dict, r: dict, delta_s: float) -> tuple[dict, dict]:
    t = r.get("first_t")
    if not o.get("fired") or t is None:
        return o, r
    nt = t + delta_s
    return {**o, "first_tod_h": (nt % DAY_S) / 3600.0}, {**r, "first_t": nt}


def propagate(autos: dict, flag_ids: list[str], pairs: list[dict], envs_by_key: dict, reps: int = 1000,
              seed: int = SEED) -> dict:
    """A10.5(b): Monte Carlo over the observed discrepancy distribution. For every flag and every one of its
    environments, independently, a draw from the observed (automation, environment) pairs of the same VESPER firing
    status: a VESPER-firing pair's partner either does not fire (its outcome becomes not fired) or fires Δ later
    (the first firing moves by -Δ; Δ = VESPER - HA); a VESPER-silent pair's partner either stays silent or fires
    (its outcome becomes HA's first-firing time of day, drawn from the observed ones). Envelope kinds are kept. The
    flag recomputed under both relations; per replicate, how many of the flags change their clock or frame kinds."""
    rng = random.Random(seed)
    fire_pairs = [p for p in pairs if p["v_fired"]]
    silent_pairs = [p for p in pairs if not p["v_fired"]]
    h_tods = [p["h_first"] for p in pairs if p["h_fired"] and not p["v_fired"]]
    changed_frame, changed_clock = [], []
    per_flag = Counter()
    for _ in range(reps):
        nf = nc = 0
        for k in flag_ids:
            e = autos[k]
            outs = {kk: dict(v) for kk, v in e["outcomes"].items()}
            rich = {kk: dict(v) for kk, v in (e.get("rich") or {}).items()}
            for kk, o in outs.items():
                r = rich.get(kk) or {}
                if o.get("fired"):
                    p = rng.choice(fire_pairs) if fire_pairs else None
                    if p is None:
                        continue
                    if not p["h_fired"]:
                        outs[kk] = {**o, "fired": False, "first_tod_h": None}
                        rich[kk] = {**r, "first_t": None}
                    elif p["delta_s"]:
                        outs[kk], rich[kk] = _shift_outcome(o, r, -p["delta_s"])
                else:
                    p = rng.choice(silent_pairs) if silent_pairs else None
                    if p is not None and p["h_fired"] and h_tods:
                        t = rng.choice(h_tods)
                        day = 1 * DAY_S
                        outs[kk] = {**o, "fired": True, "first_tod_h": (t % DAY_S) / 3600.0}
                        rich[kk] = {**r, "first_t": day + (t % DAY_S)}
            clock, frame = _divergences(e, outs, rich, envs_by_key)
            if frame != (e.get("frame_divergences") or []):
                nf += 1
                per_flag[k] += 1
            if clock != (e.get("divergences") or []):
                nc += 1
        changed_frame.append(nf)
        changed_clock.append(nc)
    return {"reps": reps, "frame_changed": _summary(changed_frame), "clock_changed": _summary(changed_clock),
            "flags_ever_changed": len(per_flag), "n": len(flag_ids)}


def _summary(xs: list[float]) -> dict:
    s = sorted(xs)
    if not s:
        return {}
    q = lambda p: s[min(len(s) - 1, max(0, math.ceil(p * len(s)) - 1))]  # noqa: E731
    return {"mean": statistics.fmean(s), "median": statistics.median(s), "p2.5": q(0.025), "p97.5": q(0.975),
            "min": s[0], "max": s[-1]}


def margin_at_risk(autos: dict, flag_ids: list[str], m_s: float, envs_by_key: dict) -> int:
    """A10.5(c): how many flags have a time_spread decision that a shift of every first firing by at most `m_s`
    could change (in some judged frame the spread is within 2m of the 3 h threshold, either side)."""
    from vesper.edt.oracle import TIME_SPREAD_H, _circular_gap, frame_values, frames_of
    from vesper.edt.types import Outcome
    n = 0
    for k in flag_ids:
        e = autos[k]
        o = {envs_by_key[kk]: Outcome(**v) for kk, v in e["outcomes"].items()}
        first_t = {envs_by_key[kk]: (e.get("rich", {}).get(kk) or {}).get("first_t") for kk in e["outcomes"]}
        frames = frames_of(e["bound"])
        vals = frame_values(o, first_t, frames)
        risky = False
        for f in frames.judged:
            if f == "clock":
                tods = vals["clock"]
                if len(tods) >= 2:
                    gaps = [_circular_gap(a, b) for i, a in enumerate(tods) for b in tods[i + 1:]]
                    risky |= any(abs(g - TIME_SPREAD_H) * 3600 <= 2 * m_s for g in gaps)
            elif f == "sun" and len(vals.get("sun", [])) >= 2:
                spread = (max(vals["sun"]) - min(vals["sun"])) * 60
                risky |= abs(spread - TIME_SPREAD_H * 3600) <= 2 * m_s
        n += risky
    return n


# ---- the run --------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vesper.validation.ha_diff", description=__doc__.split("\n")[0])
    ap.add_argument("--sweep", required=True, help="the main rung-4 sweep (8fcd672)")
    ap.add_argument("--work", required=True, help="its work directory (edt/home.yaml and cache/ traces)")
    ap.add_argument("--out", required=True, help="the per-pair records (JSON); the aggregates are made from it")
    ap.add_argument("--scratch", default=None, help="where the driver's jobs go (default: a temporary directory)")
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--ha-python", default=os.environ.get("VESPER_HA_PYTHON", ".vesper/ha/venv/bin/python"))
    ap.add_argument("--env", action="append", default=None, help="an environment key (default: Addendum 10's four)")
    ap.add_argument("--dev", action="store_true", help="the shakedown environment only (never reported)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    autos = json.loads(Path(args.sweep).read_text())["automations"]
    sample = draw_sample(autos)
    if args.limit:
        sample = sample[: args.limit]
    envs = [DEV_ENV] if args.dev else [e for e in ENVS if args.env is None or e.key in args.env]
    ids = [k for k, _ in sample]
    if args.dev:  # the shakedown needs an outcome there too
        ids = [k for k in ids if DEV_ENV.key in autos[k]["outcomes"]]
    scratch_ctx = tempfile.TemporaryDirectory(prefix="ha-diff-") if args.scratch is None else contextlib.nullcontext(
        args.scratch)
    results = {"sample": sample, "envs": [e.key for e in envs], "per_env": {}}
    with scratch_ctx as scratch:
        for env in envs:
            results["per_env"][env.key] = run_env(env, ids, autos, Path(args.work), Path(scratch), args.catalog,
                                                  Path(args.ha_python), log=lambda m: print(m, flush=True))
    Path(args.out).write_text(json.dumps(results))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
