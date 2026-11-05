"""The engine differential test's semantic stratum (protocol Addendum 15).

Addendum 10's sample (`ha_diff.draw_sample`, 263 automations) was stratified by trigger kind. This module adds a
stratum of the constructs it did not target: `mode` (`single`, `restart`, `queued`, `parallel`), `max`,
`max_exceeded`, `initial_state`, `delay` and `wait_for_trigger`. It reuses Addendum 10's harness (`ha_diff.run_env`,
Home Assistant 2026.10.2 on a virtual clock) and adds one diagnostic run: Home Assistant given *VESPER's reading* of
each automation, the bound automation without the keys VESPER's engine accepts and ignores (`mode`, `max`,
`max_exceeded`, `initial_state`; permissive replay runs every mode as `single`). A pair that differs in the main run
and agrees in the reading run differs because of those keys.

`python -m vesper.validation.ha_semantics --sweep FILE --r6c-sweep FILE --work DIR --out FILE [--reading]
[--env KEY ...]`, then `--analyse FILE ... --reading-pairs FILE ... --out FILE`.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import random
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from vesper.validation import ha_diff
from vesper.validation.ha_diff import (AGREE_FIRST_S, CLASSES, DEV_ENV, ENVS, FIRING_SHARE, SEED, _first,
                                       _quantiles, _walk_actions, discrepant, fires_in, margin_at_risk, propagate,
                                       subsecond_only, substituted_flags)

# The keys VESPER's engine accepts and does not act on (vesper.lab.automations: a mode other than single runs as
# single in permissive replay; max, max_exceeded and initial_state are accepted and ignored)
IGNORED_KEYS = ("mode", "max", "max_exceeded", "initial_state")
# Sub-strata in priority order (name, size; None: all): an automation belongs to the first whose rule it meets
SUBSTRATA = (("frame_flag", None), ("mode", 70), ("wait_for_trigger", 50), ("delay", 60),
             ("initial_state_off", 20), ("initial_state_on", 10))
_ON, _OFF = ("true", "on", "yes"), ("false", "off", "no")
FIRST_TOD_TOL_S = 1.0  # two first firings within a second are the same time of day (the sub-second offset)


def semantic_features(bound) -> set[str]:
    """`mode:<m>` (a mode other than single), `max`, `max_exceeded`, `initial_state:on|off`, `delay`,
    `wait_for_trigger` (anywhere in the actions, nested too)."""
    out: set[str] = set()
    if not isinstance(bound, dict):
        return out
    mode = bound.get("mode")
    if mode is not None and str(mode) != "single":
        out.add(f"mode:{mode}")
    for key in ("max", "max_exceeded"):
        if key in bound:
            out.add(key)
    if "initial_state" in bound:
        v = str(bound["initial_state"]).lower()
        out.add("initial_state:on" if v in _ON else "initial_state:off" if v in _OFF else f"initial_state:{v}")
    for s in _walk_actions(_first(bound, "action", "actions")):
        for key in ("delay", "wait_for_trigger"):
            if key in s:
                out.add(key)
    return out


def carries(feats: set[str]) -> bool:
    """Whether an automation belongs to the stratum: a delay, a wait_for_trigger, a mode other than single or an
    initial_state (max and max_exceeded alone do not count: under mode single they change nothing that fires)."""
    return bool(feats & {"delay", "wait_for_trigger"}) or any(f.startswith(("mode:", "initial_state:"))
                                                               for f in feats)


def substratum_rule(name: str, a: dict, feats: set[str]) -> bool:
    if name == "frame_flag":
        return bool(a.get("frame_divergences"))
    if name == "mode":
        return any(f.startswith("mode:") for f in feats)
    if name in ("wait_for_trigger", "delay"):
        return name in feats
    if name == "initial_state_off":
        return "initial_state:off" in feats
    return any(f.startswith("initial_state:") for f in feats)


def draw_semantic_sample(autos: dict, exclude: set[str], envs=ENVS, seed: int = SEED) -> list[tuple[str, str]]:
    """(automation id, sub-stratum) of Addendum 15's stratum (A15.2): the run automations with an outcome in every one
    of `envs` that carry a construct (`carries`); every frame flag among them (whether or not in `exclude`); then,
    outside `exclude` (Addendum 10's 263), each sub-stratum's size drawn with `seed`, FIRING_SHARE of it from those
    VESPER fires in some environment of `envs` and the rest from those it fires in none (a short side is filled from
    the other), as `ha_diff.draw_sample` draws."""
    rng = random.Random(seed)
    pools: dict[str, list[str]] = defaultdict(list)
    for k in sorted(autos):
        a = autos[k]
        if a.get("bound") is None or not all(e.key in (a.get("outcomes") or {}) for e in envs):
            continue
        feats = semantic_features(a["bound"])
        if not carries(feats):
            continue
        name = next(n for n, _ in SUBSTRATA if substratum_rule(n, a, feats))
        if name != "frame_flag" and k in exclude:
            continue
        pools[name].append(k)
    out: list[tuple[str, str]] = []
    for name, size in SUBSTRATA:
        pool = pools.get(name, [])
        if size is None or len(pool) <= size:
            out.extend((k, name) for k in pool)
            continue
        firing = [k for k in pool if fires_in(autos[k], envs)]
        dead = [k for k in pool if not fires_in(autos[k], envs)]
        n_fire = min(len(firing), round(size * FIRING_SHARE))
        n_dead = min(len(dead), size - n_fire)
        n_fire = min(len(firing), size - n_dead)
        out.extend((k, name) for k in sorted(rng.sample(firing, n_fire) + rng.sample(dead, n_dead)))
    return out


def vesper_reading(bound: dict) -> dict:
    """The automation as VESPER's engine runs it: without the keys it accepts and ignores (a copy)."""
    return {k: v for k, v in bound.items() if k not in IGNORED_KEYS}


def classify(p: dict, bound: dict, reading: dict | None, fixed: dict | None, fix: str = "") -> tuple[str, str]:
    """(class, cause) of one discrepant pair (A15.6), in order: the fixed engine agrees (an engine bug, `fix`);
    Home Assistant given VESPER's reading agrees (HA semantics VESPER lacks, named by the key); then Addendum 10's
    rules (`ha_diff.classify` without a fixed engine; its sub-second-offset rule only where the first firings differ,
    by under a second); else unexplained."""
    if fixed is not None and not discrepant(fixed):
        return "engine bug", fix or "resolved by the engine fix"
    if reading is not None and not discrepant(reading):
        feats = semantic_features(bound)
        mode = next((f.split(":", 1)[1] for f in sorted(feats) if f.startswith("mode:")), None)
        if mode is not None:
            return "HA semantics VESPER lacks", f"mode {mode} (VESPER runs it as single)"
        if "initial_state:off" in feats:
            return "HA semantics VESPER lacks", "initial_state off: Home Assistant never runs it"
        return "HA semantics VESPER lacks", "a key VESPER ignores (max, max_exceeded, initial_state on)"
    cls, cause = ha_diff.classify(p, bound, None)
    if cause.startswith("the 50-500 ms") and not p["delta_s"]:
        return "unexplained", ""  # the offset moves a first firing: with none moved, it is not the cause
    return cls, cause


def component_agreement(p: dict) -> dict:
    """Per oracle component, whether the two engines' outcomes agree (first_tod_h within FIRST_TOD_TOL_S)."""
    v, h = p["v_outcome"], p["h_outcome"]
    out = {k: v.get(k) == h.get(k) for k in ("fired", "flaps", "night_light", "night_cover")}
    a, b = v.get("first_tod_h"), h.get("first_tod_h")
    if a is None or b is None:
        out["first_tod_h"] = a is None and b is None
    else:
        d = abs(a - b) * 3600
        out["first_tod_h"] = min(d, 86400 - d) < FIRST_TOD_TOL_S
    return {k: out[k] for k in ("fired", "first_tod_h", "flaps", "night_light", "night_cover")}


def _merge(paths: list[str]) -> dict:
    out: dict = {"sample": None, "per_env": {}}
    for path in paths:
        d = json.loads(Path(path).read_text())
        if out["sample"] is not None and out["sample"] != d["sample"]:
            raise ValueError(f"{path}: another sample")
        out["sample"] = d["sample"]
        out["per_env"].update(d["per_env"])
    return out


def _block(rows: list, feats: dict) -> dict:
    """Addendum 10's measures (A10.4) over `rows` [(env, k, p)], plus firing counts and oracle components."""
    both = [p for _, _, p in rows if p["v_fired"] and p["h_fired"]]
    status = Counter(("both" if p["v_fired"] and p["h_fired"] else "vesper_only" if p["v_fired"] else
                      "ha_only" if p["h_fired"] else "neither") for _, _, p in rows)
    comps = [component_agreement(p) for _, _, p in rows]
    return {"pairs": len(rows), "automations": len({k for _, k, _ in rows}), "status": dict(status),
            "symmetric_automations": len({k for _, k, p in rows if p["v_fired"] != p["h_fired"]}),
            "delta": _quantiles([p["delta_s"] for p in both]),
            "payload": {"both_fire_pairs": len(both), "calls_equal": sum(p["calls_equal"] for p in both),
                        "instants_equal": sum(p["v_count"] == p["h_count"] for p in both),
                        "firings_equal": sum(p["v_n"] == p["h_n"] for p in both),
                        "vesper_fewer_instants": sum(p["v_count"] < p["h_count"] for p in both),
                        "vesper_more_instants": sum(p["v_count"] > p["h_count"] for p in both),
                        "brightness_comparable": sum(bool(p.get("brightness_comparable")) for p in both),
                        "brightness_equal": sum(p.get("brightness_equal") is True for p in both)},
            "oracle_components_agree": {c: sum(x[c] for x in comps) for c in comps[0]} if comps else {},
            "flaps": {"vesper": sum(bool(p["v_outcome"].get("flaps")) for _, _, p in rows),
                      "ha": sum(bool(p["h_outcome"].get("flaps")) for _, _, p in rows)},
            "ha_dropped_already_running": {"pairs": sum(p.get("n_already_running", 0) > 0 for _, _, p in rows),
                                           "lines": sum(p.get("n_already_running", 0) for _, _, p in rows)},
            "discrepant_pairs": sum(discrepant(p) for _, _, p in rows),
            "discrepant_automations": len({k for _, k, p in rows if discrepant(p)}),
            "subsecond_only_pairs": sum(subsecond_only(p) for _, _, p in rows)}


def analyse(pairs: dict, autos: dict, reading: dict | None = None, fixed: dict | None = None, fix: str = "",
            reps: int = 1000, r6c_ids: set[str] | None = None) -> dict:
    """The aggregates of A15.4-A15.8 from the per-pair records (`reading`: Home Assistant given VESPER's reading;
    `fixed`: VESPER's fixed engine, if a defect was fixed). Automations are named by sample index only."""
    sample = [tuple(x) for x in pairs["sample"]]
    index = {k: i for i, (k, _) in enumerate(sample)}
    sub = dict(sample)
    feats = {k: semantic_features(autos[k]["bound"]) for k, _ in sample}
    envs = list(pairs["per_env"])
    rows = [(e, k, pairs["per_env"][e]["per"][k]) for e in envs for k, _ in sample]

    def other(d, e, k):
        return None if d is None else d["per_env"][e]["per"][k]
    out: dict = {"protocol": "docs/experiments/edt-protocol.md, Addendum 15", "envs": envs,
                 "automations": len(sample), "pairs": len(rows), "substrata": dict(Counter(sub.values())),
                 "in_r6c_sample": sum(k in (r6c_ids or set()) for k, _ in sample),
                 "reproduces_sweep": sum(p["reproduces"] for _, _, p in rows),
                 "ha_unavailable": sum(p.get("ha_unavailable", False) for _, _, p in rows),
                 "ha_version": sorted({pairs["per_env"][e].get("ha_version") for e in envs}),
                 "feature_automations": dict(Counter(f for k, _ in sample for f in feats[k]))}
    out["all"] = _block(rows, feats)
    out["by_substratum"] = {n: _block([r for r in rows if sub[r[1]] == n], feats) for n, _ in SUBSTRATA
                            if n in sub.values()}
    keys = sorted({f for k, _ in sample for f in feats[k]})
    out["by_feature"] = {f: _block([r for r in rows if f in feats[r[1]]], feats) for f in keys}
    if r6c_ids:
        out["outside_r6c_sample"] = _block([r for r in rows if r[1] not in r6c_ids], feats)
    if reading is not None:
        rrows = [(e, k, other(reading, e, k)) for e, k, _ in rows]
        out["reading_run"] = _block(rrows, feats)
        out["reading_run"]["reproduces_sweep"] = sum(p["reproduces"] for _, _, p in rrows)
    classes = []
    for e, k, p in rows:
        if not discrepant(p):
            continue
        cls, cause = classify(p, autos[k]["bound"], other(reading, e, k), other(fixed, e, k), fix)
        classes.append({"index": index[k], "substratum": sub[k], "env": e, "class": cls, "cause": cause,
                        "features": sorted(feats[k]), "v_fired": p["v_fired"], "h_fired": p["h_fired"],
                        "delta_s": p["delta_s"], "v_count": p["v_count"], "h_count": p["h_count"],
                        "v_n": p["v_n"], "h_n": p["h_n"], "calls_equal": p["calls_equal"]})
    out["classes"] = {c: {"pairs": sum(x["class"] == c for x in classes),
                          "automations": len({x["index"] for x in classes if x["class"] == c}),
                          "causes": dict(Counter(x["cause"] for x in classes if x["class"] == c))} for c in CLASSES}
    out["discrepancies"] = classes
    artifact = {(x["env"], sample[x["index"]][0]) for x in classes if x["class"] == "harness artifact"}

    # A15.5: the flags (the sweep's frame flags that carry a construct; the clock flags beside them)
    from vesper.edt.trace import ENVIRONMENTS
    envs_by_key = {e.key: e for e in ENVIRONMENTS}
    run = {k: a for k, a in autos.items() if a.get("outcomes")}
    frame_flags = sorted(k for k, a in run.items() if a.get("frame_divergences"))
    carrying = [k for k in frame_flags if carries(semantic_features(run[k]["bound"]))]
    ts_carrying = [k for k in carrying if "time_spread" in run[k]["frame_divergences"]]
    clock_carrying = sorted(k for k, a in run.items() if a.get("divergences")
                            and carries(semantic_features(a["bound"])))
    direct = []
    for k, s in sample:
        if not autos[k].get("frame_divergences"):
            continue
        ha = {e: (pairs["per_env"][e]["per"][k]["h_outcome"], pairs["per_env"][e]["per"][k]["h_rich"]) for e in envs}
        new = substituted_flags(autos[k], ha, envs_by_key)
        direct.append({"index": index[k], "time_spread": "time_spread" in autos[k]["frame_divergences"],
                       "features": sorted(feats[k]), "frame_before": autos[k]["frame_divergences"],
                       "frame_after": new["frame"], "clock_before": autos[k]["divergences"],
                       "clock_after": new["clock"]})
    obs_all = [p for _, _, p in rows]
    obs_primary = [({**p, "h_fired": p["v_fired"], "delta_s": 0.0 if p["v_fired"] else None}
                    if (e, k) in artifact else p) for e, k, p in rows]
    groups = defaultdict(list)
    for (e, k, _), p in zip(rows, obs_primary):
        groups[k].append(p)
    groups = list(groups.values())
    out["flags"] = {
        "frame_flags": len(frame_flags), "frame_flags_carrying": len(carrying),
        "time_spread_frame_flags_carrying": len(ts_carrying), "clock_flags_carrying": len(clock_carrying),
        "direct": {"sampled_frame_flags": len(direct),
                   "frame_changed": sum(d["frame_before"] != d["frame_after"] for d in direct),
                   "time_spread_changed": sum(d["frame_before"] != d["frame_after"] for d in direct
                                              if d["time_spread"]),
                   "clock_changed": sum(d["clock_before"] != d["clock_after"] for d in direct),
                   "flag_lost": sum(bool(d["frame_before"]) and not d["frame_after"] for d in direct),
                   "changes": [d for d in direct if d["frame_before"] != d["frame_after"]
                               or d["clock_before"] != d["clock_after"]]},
        "propagation": {
            "primary: by automation (artifacts agree)": {
                "frame": propagate(autos, carrying, obs_primary, envs_by_key, reps, groups=groups),
                "time_spread": propagate(autos, ts_carrying, obs_primary, envs_by_key, reps, groups=groups),
                "clock": propagate(autos, clock_carrying, obs_primary, envs_by_key, max(100, reps // 10),
                                   groups=groups)},
            "secondary: per environment (artifacts agree)": {
                "frame": propagate(autos, carrying, obs_primary, envs_by_key, reps),
                "time_spread": propagate(autos, ts_carrying, obs_primary, envs_by_key, reps)},
            "secondary: by automation (as observed)": {
                "frame": propagate(autos, carrying, obs_all, envs_by_key, reps,
                                   groups=[[p for e, k, p in rows if k == kk] for kk, _ in sample])}}}
    deltas = sorted(abs(p["delta_s"]) for e, k, p in rows if p["v_fired"] and p["h_fired"] and (e, k) not in artifact)
    m99 = deltas[min(len(deltas) - 1, math.ceil(0.99 * len(deltas)) - 1)] if deltas else 0.0
    mmax = deltas[-1] if deltas else 0.0
    out["flags"]["margin"] = {"m_p99_s": m99, "m_max_s": mmax,
                              "frame_at_risk_max": margin_at_risk(autos, carrying, mmax, envs_by_key),
                              "time_spread_at_risk_max": margin_at_risk(autos, ts_carrying, mmax, envs_by_key)}

    # A15.7: the criterion (Addendum 10's four parts; harness artifacts count as agreeing)
    sym_na = [(e, k) for e, k, p in rows if p["v_fired"] != p["h_fired"] and (e, k) not in artifact]
    both_na = [p for e, k, p in rows if p["v_fired"] and p["h_fired"] and (e, k) not in artifact]
    within60 = sum(abs(p["delta_s"]) < AGREE_FIRST_S for p in both_na)
    prim = out["flags"]["propagation"]["primary: by automation (artifacts agree)"]["frame"]["frame_changed"]
    limit3b = math.floor(0.05 * len(frame_flags))
    out["criterion"] = {
        "symmetric_difference": {"value": len(sym_na), "of": len(rows), "limit": math.floor(0.02 * len(rows)),
                                 "pass": len(sym_na) <= math.floor(0.02 * len(rows))},
        "first_firing_within_60s": {"value": within60, "of": len(both_na),
                                    "share": within60 / len(both_na) if both_na else None,
                                    "pass": bool(both_na) and within60 >= 0.95 * len(both_na)},
        "time_spread_direct": {"value": out["flags"]["direct"]["time_spread_changed"],
                               "of": sum(d["time_spread"] for d in direct),
                               "pass": out["flags"]["direct"]["time_spread_changed"] == 0},
        "frame_propagated_p97_5": {"value": prim.get("p97.5", 0), "of": len(carrying), "limit": limit3b,
                                   "pass": prim.get("p97.5", 0) <= limit3b}}
    out["criterion"]["pass"] = all(v["pass"] for v in out["criterion"].values())
    if fixed is not None:
        frows = [(e, k, other(fixed, e, k)) for e, k, _ in rows]
        out["fixed_engine"] = _block(frows, feats)
        out["fixed_engine"]["vesper_outcome_differs_pairs"] = sum(
            p["v_outcome"] != pairs["per_env"][e]["per"][k]["v_outcome"] for e, k, p in frows)
    out["reach"] = reach(autos)
    return out


def reach(autos: dict) -> dict:
    """Of the sweep's run automations and its flags, how many carry each construct (a static count)."""
    run = [a for a in autos.values() if a.get("outcomes")]
    per = [(a, semantic_features(a["bound"])) for a in run]

    def count(pred):
        hit = [a for a, f in per if pred(f)]
        return {"run": len(hit), "fire_somewhere": sum(any(o.get("fired") for o in a["outcomes"].values())
                                                       for a in hit),
                "clock_flags": sum(bool(a.get("divergences")) for a in hit),
                "frame_flags": sum(bool(a.get("frame_divergences")) for a in hit),
                "time_spread_frame_flags": sum("time_spread" in (a.get("frame_divergences") or []) for a in hit),
                "flaps_frame_flags": sum("flaps" in (a.get("frame_divergences") or []) for a in hit)}
    names = sorted({f for _, fs in per for f in fs})
    out = {"of_run": len(run), "any (the stratum's rule)": count(carries),
           "a mode other than single": count(lambda f: any(x.startswith("mode:") for x in f))}
    out |= {n: count(lambda f, n=n: n in f) for n in names}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vesper.validation.ha_semantics", description=__doc__.split("\n")[0])
    ap.add_argument("--sweep", required=True, help="the sweep the replay must reproduce (r6d-5b54586)")
    ap.add_argument("--r6c-sweep", required=True, help="the sweep Addendum 10 drew its 263 from (8fcd672)")
    ap.add_argument("--work", help="the sweep's work directory (edt/home.yaml and cache/ traces)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--ha-python", default=os.environ.get("VESPER_HA_PYTHON", ".vesper/ha/venv/bin/python"))
    ap.add_argument("--env", action="append", default=None, help="an environment key (default: Addendum 10's four)")
    ap.add_argument("--dev", action="store_true", help="the shakedown environment only (never reported)")
    ap.add_argument("--reading", action="store_true", help="give Home Assistant VESPER's reading (A15.1)")
    ap.add_argument("--analyse", nargs="+", default=None, help="instead of running: the per-pair records")
    ap.add_argument("--reading-pairs", nargs="+", default=None)
    ap.add_argument("--fixed", nargs="+", default=None)
    ap.add_argument("--fix", default="")
    ap.add_argument("--reps", type=int, default=1000)
    args = ap.parse_args(argv)
    autos = json.loads(Path(args.sweep).read_text())["automations"]
    r6c = {k for k, _ in ha_diff.draw_sample(json.loads(Path(args.r6c_sweep).read_text())["automations"])}
    if args.analyse:
        res = analyse(_merge(args.analyse), autos, _merge(args.reading_pairs) if args.reading_pairs else None,
                      _merge(args.fixed) if args.fixed else None, args.fix, args.reps, r6c)
        Path(args.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        return 0
    envs = [DEV_ENV] if args.dev else [e for e in ENVS if args.env is None or e.key in args.env]
    sample = draw_semantic_sample(autos, r6c)
    ids = [k for k, _ in sample]
    if args.dev:
        ids = [k for k in ids if DEV_ENV.key in autos[k]["outcomes"]]
    scratch_ctx = tempfile.TemporaryDirectory(prefix="ha-sem-") if args.scratch is None else contextlib.nullcontext(
        args.scratch)
    results = {"sample": sample, "envs": [e.key for e in envs], "reading": args.reading, "per_env": {}}
    with scratch_ctx as scratch:
        for env in envs:
            results["per_env"][env.key] = ha_diff.run_env(
                env, ids, autos, Path(args.work), Path(scratch) / ("reading" if args.reading else "main"),
                args.catalog, Path(args.ha_python), log=lambda m: print(m, flush=True),
                ha_transform=vesper_reading if args.reading else None)
    Path(args.out).write_text(json.dumps(results))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
