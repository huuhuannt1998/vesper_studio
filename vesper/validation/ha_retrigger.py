"""The synthetic re-triggering stratum (protocol Addendum 20): automation modes against unmodified Home Assistant.

Addendum 15's stratum never saw a trigger arrive while a `restart`, `queued` or `parallel` automation was running, so
those modes were untested. This module writes 8 automations for the purpose (`STRATUM`, one `single` control and
one or more of each other mode, each with an action long enough to be re-triggered) and a trace that re-triggers them
at known instants (`pulse_instants`: a motion sensor turning on), and runs both engines on it with Addendum 10's
harness: VESPER's replay (`vesper.edt.replay.replay_detailed`) and Home Assistant 2026.10.2 in-process on the virtual
clock (`ha_diff_driver.py`), given the same entity states at the same instants (`ha_diff.input_stream`).

Measures (A20.4), per automation over the judged days 1 and 2: firings (one call on one target), distinct instants,
the (service, target) call counts, the firing sequences (pairwise within 1 s), run starts (accepted runs: Home
Assistant's `automation_triggered`, VESPER's `Replay.runs`), cancellations (Home Assistant's "Restarting" line,
VESPER's "cancel" run events) and the oracle's outcome, `flaps` among it; dropped triggers descriptively.

Also (A20.6, A20.7): the reach of an engine change over the sweep's automations with a mode other than single
(`reach`: VESPER only, on the sweep's cached traces) and the frame flags with such a mode on their own environments
(`flags`: both engines, Addendum 10's `run_env`).

`python -m vesper.validation.ha_retrigger {stratum,flags,reach} ...` (see `main`).
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import gzip
import json
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from vesper.edt.types import Env, Firing
from vesper.validation import ha_diff

H = 3600.0
DAY_S = 86400.0
PULSE_S = 10.0  # each pulse turns the sensor off this long after it turned on
SEGMENT_A = (8 * H, 8 * H + 120, 8 * H + 240)  # a spaced re-trigger
SEGMENT_B = tuple(12 * H + 70.0 * k for k in range(15))  # a burst
SEGMENT_C = (18 * H,)  # an isolated trigger
WINDOWS = {"A": (8 * H, 12 * H), "B": (12 * H, 18 * H), "C": (18 * H, 24 * H)}  # time of day, by segment
DAYS = (0, 1, 2)
MOTION = ("office_motion", "occupancy")  # the trigger
WAIT = ("kitchen_motion", "occupancy")  # a sensor the trace never turns on (automation 4's wait)
LIGHTS = ("office_light", "office_light_2", "office_light_3", "kitchen_light", "kitchen_light_2", "kitchen_light_3",
          "living_light", "living_light_2")
PLUG = "office_plug"
SOURCE_ENV = ha_diff.ENVS[0]  # R7-C's first environment: the synthetic trace's header (site, date, start states)
RESTARTING = "Restarting"
DROPPED = ("Already running", "Maximum number of runs exceeded")
SAME_S = 1.0  # two firings are the same if their service and target agree and they are under a second apart
MAX_CHAIN = 15  # the most runs one queued chain can hold in a segment (the burst has 15 triggers)


def _on(entity):
    return {"action": "light.turn_on", "target": {"entity_id": entity}}


def _off(entity):
    return {"action": "light.turn_off", "target": {"entity_id": entity}}


def _auto(n: int, actions: list, **mode) -> dict:
    return {"id": f"retrigger_{n}", "alias": f"retrigger {n}", **mode,
            "triggers": [{"trigger": "state", "entity_id": "{motion}", "to": "on"}], "actions": actions}


def _hold(n: int, seconds: float, **mode) -> dict:
    light = f"{{light{n}}}"
    return _auto(n, [_on(light), {"delay": seconds}, _off(light)], **mode)


# A20.1: synthetic, written for this test (not from the corpus); "{...}" names an entity of the home (`stratum`)
STRATUM = {
    "r1": _hold(1, 300),
    "r2": _hold(2, 300, mode="restart"),
    "r3": _auto(3, [_on("{light3}"), {"delay": 100},
                    {"action": "switch.turn_on", "target": {"entity_id": "{plug}"}}, {"delay": 100},
                    _off("{light3}"), {"action": "switch.turn_off", "target": {"entity_id": "{plug}"}}],
                mode="restart"),
    "r4": _auto(4, [_on("{light4}"),
                    {"wait_for_trigger": [{"trigger": "state", "entity_id": "{wait}", "to": "on"}], "timeout": 200,
                     "continue_on_timeout": True},
                    _off("{light4}")], mode="restart"),
    "r5": _hold(5, 300, mode="queued", max=3),
    "r6": _hold(6, 95, mode="queued"),
    "r7": _hold(7, 300, mode="parallel", max=2, max_exceeded="silent"),
    "r8": _hold(8, 300, mode="parallel"),
}


def _fill(node, names: dict[str, str]):
    if isinstance(node, str):
        for k, v in names.items():
            node = node.replace("{" + k + "}", v)
        return node
    if isinstance(node, list):
        return [_fill(x, names) for x in node]
    if isinstance(node, dict):
        return {k: _fill(v, names) for k, v in node.items()}
    return node


def stratum(ids: dict[str, dict[str, str]]) -> dict[str, dict]:
    """The 8 automations with the home's entity ids (`vesper.lab.automations.entity_ids`) filled in."""
    names = {"motion": ids[MOTION[0]][MOTION[1]], "wait": ids[WAIT[0]][WAIT[1]], "plug": ids[PLUG]["state"]}
    names |= {f"light{i + 1}": ids[name]["state"] for i, name in enumerate(LIGHTS)}
    return {k: _fill(copy.deepcopy(a), names) for k, a in STRATUM.items()}


def timer_offsets() -> set[float]:
    """Every instant after a run's start at which the stratum sets a timer to end (its delays' and timeout's ends; for
    a queued automation, the n-th run of a chain started at that instant ending, n up to MAX_CHAIN)."""
    out: set[float] = set()
    for a in STRATUM.values():
        t, ends = 0.0, []
        for step in a["actions"]:
            hold = step.get("delay", step.get("timeout"))
            if hold is not None:
                t += float(hold)
                ends.append(t)
        out |= set(ends)
        if a.get("mode") == "queued":
            out |= {n * t for n in range(1, MAX_CHAIN + 1)}
    return out


def pulse_instants() -> list[float]:
    """When the trigger sensor turns on, seconds after local midnight (the same each day)."""
    return [*SEGMENT_A, *SEGMENT_B, *SEGMENT_C]


def segments() -> dict[str, list[float]]:
    return {"A": list(SEGMENT_A), "B": list(SEGMENT_B), "C": list(SEGMENT_C)}


def reports(twin: str, feature: str = MOTION[1], days=DAYS) -> list[dict]:
    """The synthetic trace's reports: the sensor on at each pulse instant of each day and off PULSE_S later."""
    rows = []
    for day in days:
        for at in pulse_instants():
            t = day * DAY_S + at
            rows += [{"t": t, "twin_id": twin, "feature": feature, "value": True, "observed_t": t},
                     {"t": t + PULSE_S, "twin_id": twin, "feature": feature, "value": False,
                      "observed_t": t + PULSE_S}]
    return sorted(rows, key=lambda r: r["observed_t"])


def write_trace(source: Path, out: Path, twin: str) -> dict:
    """A20.2: `source`'s header (site, date, every device's start state) and the synthetic reports only."""
    with gzip.open(source, "rt") as f:
        header = json.loads(f.readline())
    header = {**header, "synthetic": "protocol Addendum 20: the re-triggering stratum's trace"}
    header.setdefault("initial_state", {}).setdefault(twin, {})[MOTION[1]] = False
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt") as f:
        f.write(json.dumps(header) + "\n")
        for r in reports(twin):
            f.write(json.dumps(r) + "\n")
    return header


def home_entities(home_yaml: Path, catalog_dir: str) -> tuple[dict, dict[str, str]]:
    """(the home's entity ids as `entity_ids` names them, each device name -> its twin id)."""
    from vesper.edt.replay import _home
    from vesper.lab.automations import entity_ids
    home, twins = _home(str(Path(home_yaml).resolve()), Path(home_yaml).stat().st_mtime_ns,
                        str(Path(catalog_dir).resolve()))
    return entity_ids(home), {tid.split("@", 1)[1]: tid for tid in twins}


# ---- the two sides --------------------------------------------------------------------------------------------------

def _judged(t: float) -> bool:
    return DAY_S <= t < 3 * DAY_S


def vesper_sides(rep) -> dict[str, dict]:
    """Per automation: its firings, run starts, cancellations and dropped triggers (judged days) from a Replay."""
    out = {}
    for k, firings in rep.firings.items():
        runs = rep.runs.get(k, [])
        out[k] = {"firings": [f for f in firings if _judged(f.t)],
                  "starts": [t for t, ev in runs if ev == "start" and _judged(t)],
                  "cancels": [t for t, ev in runs if ev == "cancel" and _judged(t)],
                  "dropped": [[t, why] for t, why in rep.skipped.get(k, []) if _judged(t)]}
    return out


def ha_sides(out: dict, label_of: dict[str, str]) -> dict[str, dict]:
    """The same from the driver's output: calls as Firings (`ha_diff.ha_firings`), `automation_triggered` events as
    run starts, "Restarting" INFO lines as cancellations, "Already running" / "Maximum number of runs exceeded" as
    dropped triggers."""
    firings = ha_diff.ha_firings(out, label_of, DAY_S, 3 * DAY_S)
    sides = {k: {"firings": [f for f in firings.get(k, []) if _judged(f.t)], "starts": [], "cancels": [],
                 "dropped": []} for k in label_of.values()}
    for t, alias, _source in out.get("triggered", []):
        k = label_of.get(alias)
        if k is not None and _judged(t):
            sides[k]["starts"].append(t)
    for row in out.get("info", []):
        k = ha_diff._log_owner(row, label_of)
        if k is not None and _judged(row[0]) and RESTARTING in row[3]:
            sides[k]["cancels"].append(row[0])
    for row in out.get("log", []):
        k = ha_diff._log_owner(row, label_of)
        why = next((d for d in DROPPED if d in row[3]), None)
        if k is not None and why is not None and _judged(row[0]):
            sides[k]["dropped"].append([row[0], why])
    return sides


# ---- comparing ------------------------------------------------------------------------------------------------------

def _segment(t: float) -> str:
    tod = t % DAY_S
    return next((s for s, (lo, hi) in WINDOWS.items() if lo <= tod < hi), "other")


def summary(side: dict, site) -> dict:
    from vesper.edt.oracle import outcome
    fs = sorted(side["firings"], key=lambda f: (f.t, f.service, f.target))
    seg = {s: {"firings": 0, "starts": 0, "cancels": 0} for s in WINDOWS}
    for kind, times in (("firings", [f.t for f in fs]), ("starts", side["starts"]), ("cancels", side["cancels"])):
        for t in times:
            seg.setdefault(_segment(t), {"firings": 0, "starts": 0, "cancels": 0})[kind] += 1
    calls = Counter((f.service, f.target) for f in fs)
    return {"fired": bool(fs), "n_firings": len(fs), "instants": len({f.t for f in fs}),
            "calls": sorted([s, t, n] for (s, t), n in calls.items()), "starts": len(side["starts"]),
            "cancels": len(side["cancels"]), "dropped": len(side["dropped"]),
            "dropped_by": dict(Counter(why for _, why in side["dropped"])),
            "outcome": dataclasses.asdict(outcome(fs, site)), "by_segment": seg}


def _same_sequence(v: list[Firing], h: list[Firing]) -> bool:
    v = sorted(v, key=lambda f: (f.t, f.service, f.target))
    h = sorted(h, key=lambda f: (f.t, f.service, f.target))
    return len(v) == len(h) and all(a.service == b.service and a.target == b.target and abs(a.t - b.t) < SAME_S
                                    for a, b in zip(v, h, strict=True))


def _same_outcome(a: dict, b: dict) -> bool:
    if a["fired"] != b["fired"] or any(a[k] != b[k] for k in ("flaps", "night_light", "night_cover")):
        return False
    if a["first_tod_h"] is None or b["first_tod_h"] is None:
        return a["first_tod_h"] == b["first_tod_h"]
    return abs(a["first_tod_h"] - b["first_tod_h"]) * H < SAME_S


def compare(v_side: dict, h_side: dict, site) -> dict:
    """A20.4/A20.5: VESPER's and Home Assistant's measures and which are equal; `agrees` if measures 1-5 all are."""
    v, h = summary(v_side, site), summary(h_side, site)
    equal = {"fired": v["fired"] == h["fired"], "n_firings": v["n_firings"] == h["n_firings"],
             "instants": v["instants"] == h["instants"], "calls": v["calls"] == h["calls"],
             "sequence": _same_sequence(v_side["firings"], h_side["firings"]), "starts": v["starts"] == h["starts"],
             "cancels": v["cancels"] == h["cancels"], "outcome": _same_outcome(v["outcome"], h["outcome"])}
    return {"v": v, "h": h, "equal": equal, "agrees": all(equal.values())}


# ---- A20.1-A20.5: the stratum ---------------------------------------------------------------------------------------

def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_stratum(work: Path, catalog_dir: str, ha_python: Path, scratch: Path, env: Env = SOURCE_ENV,
                log=print) -> dict:
    """Both engines on the synthetic trace; the comparison per automation."""
    from vesper.edt.oracle import site_of
    from vesper.edt.replay import replay_detailed
    from vesper.env.outdoor import RUN_DAYS
    home_yaml = work / "edt" / "home.yaml"
    source = work / "cache" / f"trace-{env.key}-rho_device.jsonl.gz"
    ids, twin_of = home_entities(home_yaml, catalog_dir)
    trace = scratch / "synthetic-trace.jsonl.gz"
    header = write_trace(source, trace, twin_of[MOTION[0]])
    bound = stratum(ids)
    end = RUN_DAYS * DAY_S
    t = time.monotonic()
    inputs = ha_diff.input_stream(home_yaml, trace, catalog_dir)
    rep = replay_detailed(home_yaml, trace, bound, catalog_dir=catalog_dir)
    log(f"VESPER {time.monotonic() - t:.1f} s, {len(inputs['stream'])} state changes")
    labelled, label_of = ha_diff.ha_automations(list(bound), bound)
    job = ha_diff.ha_job(header, inputs, labelled, end)
    job["info_messages"] = [RESTARTING]
    t = time.monotonic()
    out = ha_diff.run_ha(job, ha_python, scratch / "ha")
    log(f"Home Assistant {time.monotonic() - t:.1f} s, {len(out['calls'])} calls, {len(out['unavailable'])} "
        "unavailable")
    vs, hs = vesper_sides(rep), ha_sides(out, label_of)
    site = site_of(env)
    per = {}
    for k in bound:
        rec = compare(vs[k], hs[k], site)
        rec.update(mode=bound[k].get("mode", "single"), max=bound[k].get("max"),
                   vesper_refused=rep.refused.get(k), approximations=rep.approximations.get(k, []),
                   ha_unavailable=next((a for a, kk in label_of.items() if kk == k), None) in out["unavailable"],
                   v_firings=[[f.t, f.service, f.target] for f in vs[k]["firings"]],
                   h_firings=[[f.t, f.service, f.target] for f in hs[k]["firings"]],
                   v_runs=[[t, "start"] for t in vs[k]["starts"]] + [[t, "cancel"] for t in vs[k]["cancels"]],
                   h_runs=[[t, "start"] for t in hs[k]["starts"]] + [[t, "cancel"] for t in hs[k]["cancels"]],
                   v_dropped=vs[k]["dropped"], h_dropped=hs[k]["dropped"])
        per[k] = rec
    stream = inputs["stream"]
    return {"addendum": 20, "engine_commit": _commit(), "ha_version": out.get("ha_version"), "env": env.key,
            "trace": {"pulses_per_day": len(pulse_instants()), "days": len(DAYS),
                      "state_changes": sum(1 for _, e, *_ in stream if e != "sun.sun"),
                      "entities_changing": sorted({e for _, e, *_ in stream if e != "sun.sun"})},
            "per": per, "agree": sum(r["agrees"] for r in per.values()), "n": len(per),
            "ha_log_total": len(out.get("log", [])), "ha_info_total": len(out.get("info", []))}


# ---- A20.6: reach over the sweep (VESPER only) ----------------------------------------------------------------------

def non_single(autos: dict) -> list[str]:
    """The run automations (an outcome somewhere) whose mode is not single, in key order."""
    return sorted(k for k, a in autos.items() if a.get("bound") and a.get("outcomes")
                  and a["bound"].get("mode", "single") != "single")


def _reach_env(args: tuple) -> tuple[str, dict]:
    work, env_key, bound, catalog_dir = args
    from vesper.edt.oracle import outcome, rich_outcome, site_of
    from vesper.edt.replay import replay_detailed
    from vesper.edt.trace import ENVIRONMENTS
    env = next(e for e in ENVIRONMENTS if e.key == env_key)
    trace = Path(work) / "cache" / f"trace-{env.key}-rho_device.jsonl.gz"
    r = replay_detailed(Path(work) / "edt" / "home.yaml", trace, bound, catalog_dir=catalog_dir)
    site = site_of(env)
    res = {}
    for k in bound:
        if k in r.refused:
            res[k] = {"refused": r.refused[k]}
            continue
        f = r.firings.get(k, [])
        res[k] = {"outcome": dataclasses.asdict(outcome(f, site)), "rich": rich_outcome(f, r.branches.get(k)),
                  "starts": sum(ev == "start" for _, ev in r.runs.get(k, [])),
                  "cancels": sum(ev == "cancel" for _, ev in r.runs.get(k, [])),
                  "dropped": len(r.skipped.get(k, []))}
    return env_key, res


def run_reach(sweep: dict, work: Path, catalog_dir: str, workers: int = 2, log=print) -> dict:
    """Every run automation with a non-single mode, replayed on each of the sweep's environments' cached traces."""
    autos = sweep["automations"]
    keys = non_single(autos)
    bound = {k: autos[k]["bound"] for k in keys}
    envs = sorted({e for k in keys for e in autos[k]["outcomes"]})
    t = time.monotonic()
    per_env = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for env_key, res in ex.map(_reach_env, [(str(work), e, bound, catalog_dir) for e in envs]):
            per_env[env_key] = res
            log(f"{env_key}: {len(res)} automations ({time.monotonic() - t:.0f} s)")
    gate = Counter()
    for env_key, res in per_env.items():
        for k, r in res.items():
            if "outcome" not in r or env_key not in autos[k]["outcomes"]:
                gate["no outcome"] += 1
                continue
            cached = autos[k]["outcomes"][env_key]
            crich = (autos[k].get("rich") or {}).get(env_key) or {}
            same = cached == r["outcome"] and all(crich.get(x) == r["rich"].get(x)
                                                  for x in ("first_t", "count", "payload", "branches"))
            gate["reproduces" if same else "differs"] += 1
    return {"engine_commit": _commit(), "keys": keys, "envs": envs, "per_env": per_env, "gate_vs_sweep": dict(gate)}


def reach_analysis(sweep: dict, before: dict, after: dict) -> dict:
    """A20.6: pairs and automations whose outcome changes between the two engines; the flags recomputed with the
    fixed engine's outcomes in every environment (A10.5(a)'s recomputation), against the sweep's."""
    from vesper.edt.trace import ENVIRONMENTS
    autos = sweep["automations"]
    envs_by_key = {e.key: e for e in ENVIRONMENTS}
    keys = before["keys"]
    pairs = Counter()
    by_mode = {}
    components = Counter()
    changed_autos = []
    runs = Counter()
    flags = {"frame": Counter(), "clock": Counter()}
    changed_flags = []
    for k in keys:
        mode = autos[k]["bound"].get("mode", "single")
        bm = by_mode.setdefault(mode, {"automations": 0, "fire_somewhere": 0, "changed": 0, "pairs_changed": 0})
        bm["automations"] += 1
        bm["fire_somewhere"] += any((o or {}).get("fired") for o in autos[k]["outcomes"].values())
        changed = False
        subst = {}
        for env_key in before["envs"]:
            b, a = before["per_env"][env_key].get(k), after["per_env"][env_key].get(k)
            if b is None or a is None or "outcome" not in b or "outcome" not in a:
                pairs["unavailable"] += 1
                continue
            pairs["pairs"] += 1
            runs["starts_before"] += b["starts"]
            runs["starts_after"] += a["starts"]
            runs["cancels_after"] += a["cancels"]
            runs["dropped_before"] += b["dropped"]
            runs["dropped_after"] += a["dropped"]
            subst[env_key] = (a["outcome"], a["rich"])
            diff = [c for c in ("fired", "first_tod_h", "flaps", "night_light", "night_cover")
                    if b["outcome"][c] != a["outcome"][c]]
            diff += [f"rich_{c}" for c in ("first_t", "count", "payload", "branches") if b["rich"].get(c)
                     != a["rich"].get(c)]
            if diff:
                pairs["changed"] += 1
                bm["pairs_changed"] += 1
                components.update(diff)
                changed = True
        if changed:
            bm["changed"] += 1
            changed_autos.append(k)
        new = ha_diff.substituted_flags(autos[k], subst, envs_by_key)
        for rel, old in (("frame", autos[k].get("frame_divergences") or []), ("clock", autos[k].get("divergences")
                                                                               or [])):
            if old and new[rel] == old:
                flags[rel]["held_same_kinds"] += 1
            elif old and new[rel]:
                flags[rel]["held_other_kinds"] += 1
            elif old:
                flags[rel]["lost"] += 1
            elif new[rel]:
                flags[rel]["new"] += 1
        if (autos[k].get("frame_divergences") or new["frame"]):
            changed_flags.append({"index": keys.index(k), "mode": mode, "old": autos[k].get("frame_divergences"),
                                  "new": new["frame"], "old_clock": autos[k].get("divergences"),
                                  "new_clock": new["clock"]})
    return {"automations": len(keys), "pairs": dict(pairs), "by_mode": by_mode, "components": dict(components),
            "automations_changed": len(changed_autos), "runs": dict(runs),
            "flags": {r: dict(c) for r, c in flags.items()}, "frame_flags": changed_flags}


# ---- A20.7: the frame flags with a non-single mode, on their own environments ---------------------------------------

def flagged_non_single(autos: dict) -> list[str]:
    return [k for k in non_single(autos) if autos[k].get("frame_divergences")]


def run_flags(sweep: dict, work: Path, catalog_dir: str, ha_python: Path, scratch: Path, env_keys: list[str],
              log=print) -> dict:
    from vesper.edt.trace import ENVIRONMENTS
    autos = sweep["automations"]
    keys = flagged_non_single(autos)
    envs = [e for e in ENVIRONMENTS if e.key in env_keys]
    per_env = {}
    for env in envs:
        per_env[env.key] = ha_diff.run_env(env, keys, autos, work, scratch, catalog_dir, ha_python, log=log,
                                           runs=True)
    return {"engine_commit": _commit(), "keys": keys, "envs": [e.key for e in envs], "per_env": per_env}


def flags_analysis(sweep: dict, res: dict) -> dict:
    from vesper.edt.trace import ENVIRONMENTS
    autos = sweep["automations"]
    envs_by_key = {e.key: e for e in ENVIRONMENTS}
    out = []
    for i, k in enumerate(res["keys"]):
        recs = {e: res["per_env"][e]["per"][k] for e in res["envs"]}
        disc = [e for e, p in recs.items() if ha_diff.discrepant(p)]
        runs_differ = [e for e, p in recs.items() if (p.get("v_starts"), p.get("v_cancels"))
                       != (p.get("h_starts"), p.get("h_cancels"))]
        with_ha = ha_diff.substituted_flags(autos[k], {e: (p["h_outcome"], p["h_rich"]) for e, p in recs.items()},
                                            envs_by_key)
        with_v = ha_diff.substituted_flags(autos[k], {e: (p["v_outcome"], p["v_rich"]) for e, p in recs.items()},
                                           envs_by_key)
        out.append({"index": i, "mode": autos[k]["bound"].get("mode"), "envs": len(recs),
                    "sweep": {"frame": autos[k].get("frame_divergences"), "clock": autos[k].get("divergences")},
                    "reproduces": sum(p["reproduces"] for p in recs.values()),
                    "both_fire": sum(p["v_fired"] and p["h_fired"] for p in recs.values()),
                    "vesper_only": sum(p["v_fired"] and not p["h_fired"] for p in recs.values()),
                    "ha_only": sum(p["h_fired"] and not p["v_fired"] for p in recs.values()),
                    "discrepant": len(disc), "subsecond_only": sum(ha_diff.subsecond_only(p) for p in recs.values()),
                    "runs_differ": len(runs_differ),
                    "starts": [sum(p.get("v_starts", 0) for p in recs.values()),
                               sum(p.get("h_starts", 0) for p in recs.values())],
                    "cancels": [sum(p.get("v_cancels", 0) for p in recs.values()),
                                sum(p.get("h_cancels", 0) for p in recs.values())],
                    "max_abs_delta_s": max((abs(p["delta_s"]) for p in recs.values() if p["delta_s"] is not None),
                                           default=None),
                    "with_ha_outcomes": with_ha, "with_vesper_outcomes": with_v,
                    "survives_with_ha": bool(with_ha["frame"])})
    return {"flags": out}


# ---- CLI ------------------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vesper.validation.ha_retrigger", description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("stratum", "flags", "reach", "reach-analysis", "flags-analysis"))
    ap.add_argument("--work", help="the sweep's work directory (edt/home.yaml, cache/ traces)")
    ap.add_argument("--sweep", help="the sweep (its automations, outcomes and flags)")
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--ha-python", default=os.environ.get("VESPER_HA_PYTHON", ".vesper/ha/venv/bin/python"))
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--env", action="append", default=None, help="flags: an environment key (default: all)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--before", help="reach-analysis: the reach run with the engine before the change")
    ap.add_argument("--after", help="reach-analysis: the reach run with the changed engine")
    ap.add_argument("--runs", help="flags-analysis: a flags run")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    say = lambda m: print(m, flush=True)  # noqa: E731
    if args.what == "stratum":
        res = run_stratum(Path(args.work), str(Path(args.catalog).resolve()), Path(args.ha_python),
                          Path(args.scratch), log=say)
    else:
        sweep = json.loads(Path(args.sweep).read_text())
        if args.what == "reach":
            res = run_reach(sweep, Path(args.work), str(Path(args.catalog).resolve()), args.workers, log=say)
        elif args.what == "reach-analysis":
            res = reach_analysis(sweep, json.loads(Path(args.before).read_text()),
                                 json.loads(Path(args.after).read_text()))
        elif args.what == "flags-analysis":
            res = flags_analysis(sweep, json.loads(Path(args.runs).read_text()))
        else:
            envs = args.env or sorted({e for k in flagged_non_single(sweep["automations"])
                                       for e in sweep["automations"][k]["outcomes"]})
            res = run_flags(sweep, Path(args.work), str(Path(args.catalog).resolve()), Path(args.ha_python),
                            Path(args.scratch), envs, log=say)
    Path(args.out).write_text(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
