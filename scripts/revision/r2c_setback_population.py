#!/usr/bin/env python3
"""r2c_setback_population.py -- VESPER revision R2-c (post hoc): the below-10 C finding over the whole population of
setback automations, not a sample.

The confirmation's below-10 C finding (`vesper edt confirm`) rests on a seeded sample of the flagged automations; a
different sweep moves the flagged set, the sample, and the count. Here the population is fixed statically instead:

1. **Population** (static, model-free): every automation that runs at rung 4 (bound, with an outcome in the sweep)
   and whose bound actions command a climate setpoint below 10 C (`vesper.edt.setback.low_setpoints`: a literal
   `temperature` or `target_temp_low` of a `climate.*` action). The corpus is re-bound here (`--corpus`, read-only,
   `vesper.edt.bind.bind_with_assumptions` with the sweep's roles, variant 0), and checked against the sweep's
   stored bound automations and run set. Context: the same count over every parsed automation, bound or not.
2. **Closed loop on the whole population**: every population automation alone (`vesper.edt.confirm.closed_loop`,
   unchanged) in all 48 sweep environments, each against that environment's baseline (no automation). A room-run cell
   is a caused breach when the room's minimum over days 1-2 is below 10 C with the automation and not in the baseline
   (`vesper.edt.confirm.envelope`). Per run also: the setback (hours within days 1-2 that the commanded setpoint is
   below 10 C, `vesper.edt.setback.setback_intervals`) and the outdoor air over it (the cached ERA5 weather the run
   read, sampled every 10 minutes).
3. **Mass sensitivity**: step 2 again in the environments with any caused breach, with every room's heat capacity
   x S, S in {0.5, 1, 2, 4} (`vesper.env.indoor.knobs(CAPACITY_SCALE=S)`), each S with its own baselines. S = 1 is
   run afresh and checked against step 2.
4. **Reconciliation** with the confirmation samples (which population automations they held; whether every
   confirmation breacher is in the population).

The envelope is computed from each room's lowest and highest reading in the measured days (what `envelope` reads),
so the workers return those extremes instead of whole series. Everything is post hoc. Outputs hold aggregates only
(counts, temperatures, durations, environment labels, anonymous indices A1, A2, ... in sorted id order and R1, R2,
... for repositories in order of first appearance): no automation content, no repository or entity name, no hash, no
path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r2c_setback_population.py --corpus DIR
        [--lab ~/vesper-lab/edt] [--commit 120f4ca] [--catalog catalog/vds] [--workers 4] [--scratch DIR]

Inputs (local, never committed): --lab/sweep-rung4-C.json, --lab/work-C/edt/home.yaml and work-C/cache/weather (the
sweep's home and recorded weather), --lab/confirm-rung4-*.json (the confirmation samples), and the corpus (--corpus).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

DAYS = 3
DAY_S = 86400.0
WINDOW = (1, DAYS - 1)  # the measured days, as the confirmation
SCALES = (0.5, 1.0, 2.0, 4.0)
COLD_C = 10.0
OUTDOOR_STEP_S = 600.0
F_LIKE = (32.0, 50.0)  # a literal setpoint here is below 10 C if it was meant in Fahrenheit
CONFIRMS = ("120f4ca", "dc119cd")


def label(env) -> str:
    return f"{env.city.split(',')[0]} {env.date} n{int(env.north_deg)}"


def _env(e: dict):
    from vesper.edt.types import Env
    return Env(e["city"], e["date"], e["north_deg"], e["rung"])


def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def _span(xs):
    xs = [x for x in xs if x is not None]
    return [_r(min(xs)), _r(max(xs))] if xs else None


# ---- 1. the population ---------------------------------------------------------------------------------------------

def population(sweep: dict, corpus: Path, lab: Path, commit: str, catalog: str, scratch: Path) -> tuple[dict, dict]:
    """(the population {id: low setpoints}, the step-1 report)."""
    from vesper.edt.bind import Unbindable, bind_with_assumptions
    from vesper.edt.corpus import iter_automations
    from vesper.edt.home import build_edt_home
    from vesper.edt.setback import low_setpoints, setpoints
    autos = sweep["automations"]
    run = {i for i, a in autos.items() if a["outcomes"]}
    home_yaml, roles = build_edt_home(scratch / "bind-work", catalog)
    sweep_home = lab / f"work-{commit}" / "edt" / "home.yaml"
    same_home = hashlib.sha256(home_yaml.read_bytes()).digest() == hashlib.sha256(sweep_home.read_bytes()).digest()
    raw_dir = corpus / "raw" if (corpus / "raw").is_dir() else corpus
    parsed = 0
    ctx: dict[str, list] = {}
    templated, f_like = set(), set()
    pop: dict[str, list] = {}
    bound_mismatch, bound_ok = [], 0
    t0 = time.monotonic()
    for a in iter_automations(raw_dir):
        parsed += 1
        raw = a.raw
        sps = setpoints(raw)
        if any(s.templated for s in sps):
            templated.add(a.hash)
        if any(s.value is not None and F_LIKE[0] <= s.value <= F_LIKE[1] for s in sps):
            f_like.add(a.hash)
        low_raw = low_setpoints(raw)
        if low_raw:
            ctx[a.hash] = [s.value for s in low_raw]
        if not (low_raw or a.hash in run):
            continue
        try:
            b = bind_with_assumptions(raw, roles, 0) if isinstance(raw, dict) else Unbindable("not a mapping")
        except Exception:  # noqa: BLE001 - as the sweep: a binder crash is an unbindable automation
            b = Unbindable("binder error")
        if isinstance(b, tuple):
            b = b[0]
        if isinstance(b, Unbindable):
            if low_raw and a.hash in run:
                bound_mismatch.append(a.hash)
            continue
        if a.hash in run:
            stored = autos[a.hash]["bound"]
            if _jsonable(b) == stored:
                bound_ok += 1
            else:
                bound_mismatch.append(a.hash)
            low = low_setpoints(stored)  # the sweep's own bound automation, which closed_loop runs
            if low:
                pop[a.hash] = sorted({s.value for s in low})
    elapsed = time.monotonic() - t0
    # the same over the sweep's stored bound automations, as a cross-check of the re-bind
    from_sweep = {i for i in run if low_setpoints(autos[i]["bound"])}
    repos = {autos[i]["repo"] for i in pop}
    ctx_repos = {autos[i]["repo"] for i in ctx if i in autos}
    values = defaultdict(int)
    for i in pop:
        for v in pop[i]:
            values[str(v)] += 1
    ctx_values = defaultdict(int)
    for vs in ctx.values():
        for v in sorted(set(vs)):
            ctx_values[str(v)] += 1
    report = {
        "rule": "a literal 'temperature' or 'target_temp_low' below 10 C in the data of a climate.* action of the "
                "bound automation (vesper.edt.setback.low_setpoints); templated values cannot be read statically",
        "automations": len(pop), "repositories": len(repos),
        "flagged": sum(1 for i in pop if autos[i]["divergences"]),
        "commanded_setpoints_c": dict(sorted(values.items())),
        "checks": {"parsed_automations": parsed, "parsed_matches_sweep": parsed == sweep["yield"]["parsed"],
                   "edt_home_identical_to_sweep": same_home,
                   "rebound_equal_to_sweep_bound": bound_ok, "rebound_differs_or_unbindable": len(bound_mismatch),
                   "population_from_sweep_bound_equal": from_sweep == set(pop), "rebind_seconds": round(elapsed, 1)},
        "corpus_context": {
            "parsed": parsed,
            "automations_commanding_a_setpoint_below_10_c": len(ctx),
            "repositories": len(ctx_repos),
            "of_which_bound": sum(1 for i in ctx if i in autos and autos[i]["bound"] is not None),
            "of_which_run_at_rung_4": sum(1 for i in ctx if i in run),
            "commanded_setpoints_c": dict(sorted(ctx_values.items(), key=lambda kv: float(kv[0]))),
            "automations_with_a_templated_setpoint": len(templated),
            "automations_with_a_literal_setpoint_32_to_50": len(f_like),
            "note": "templated setpoints are not evaluated; a literal 32-50 is below 10 C only if meant in "
                    "Fahrenheit (the engine reads Celsius); neither is in the population"},
    }
    return pop, report


# ---- 2/3. closed loop ----------------------------------------------------------------------------------------------

def _extremes(temps: dict) -> dict:
    """Each room's lowest and highest reading in the measured days, as a two-point series (what envelope reads)."""
    lo, hi = WINDOW[0] * DAY_S, (WINDOW[1] + 1) * DAY_S
    out = {}
    for room, series in temps.items():
        v = [(t, c) for t, c in series if lo <= t < hi]
        out[room] = [min(v, key=lambda x: x[1]), max(v, key=lambda x: x[1])] if v else []
    return out


def _closed_job(job: dict) -> dict:
    from vesper.edt.confirm import closed_loop
    from vesper.edt.setback import setback_intervals
    from vesper.env import indoor
    with indoor.knobs(CAPACITY_SCALE=job["scale"]):
        r = closed_loop(job["bound"], _env(job["env"]), Path(job["home"]), Path(job["cache"]), days=DAYS,
                        catalog_dir=job["catalog"])
    climate_rows = [{k: row.get(k) for k in ("t", "service", "feature", "value", "accepted", "kind")}
                    for row in r["rows"] if str(row.get("service", "")).startswith("climate.")]
    return {"tag": job["tag"], "rooms": r["rooms"], "extremes": _extremes(r["temperatures"]),
            "setback": setback_intervals(climate_rows, WINDOW[0] * DAY_S, (WINDOW[1] + 1) * DAY_S),
            "low_commands": sum(1 for x in climate_rows if x.get("accepted", True) and isinstance(x["value"],
                                                                                                 (int, float))
                                and x["value"] < COLD_C),
            "refused": bool(r["refused"]), "firings": len(r["firings"])}


def _pool(jobs: list[dict], workers: int) -> list[dict]:
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
            return list(pool.map(_closed_job, jobs, chunksize=2))
    return [_closed_job(j) for j in jobs]


_WEATHER: dict = {}


def outdoor(env_d: dict, cache: Path, intervals: list) -> dict:
    """Mean and minimum outdoor air temperature (ERA5 cache, the run's site) over the intervals, every 10 min."""
    from vesper.edt.trace import env_site
    from vesper.env import weather
    env = _env(env_d)
    site = env_site(env)
    if env.key not in _WEATHER:
        _WEATHER[env.key] = weather.load(site.lat, site.lon, weather.utc_days(site.utc(0), site.utc(DAYS * DAY_S)),
                                         cache / "weather")
    w = _WEATHER[env.key]
    vals = []
    for a, b in intervals:
        t = a
        while t < b:
            v = w.at(site.utc(t))["temperature_2m"] if w is not None else None
            if v is not None:
                vals.append(v)
            t += OUTDOOR_STEP_S
    return {"mean_c": _r(statistics.fmean(vals)) if vals else None, "min_c": _r(min(vals)) if vals else None}


def judge(done: list[dict], envs: dict, names: dict, cache: Path) -> dict:
    """Per scale: per run rows (envelope against that scale's baseline, setback, outdoor air)."""
    from vesper.edt.confirm import envelope
    base = {(r["tag"][0], r["tag"][2]): r for r in done if r["tag"][1] is None}
    runs = defaultdict(list)
    for r in sorted((r for r in done if r["tag"][1] is not None), key=lambda r: (r["tag"][0], r["tag"][1],
                                                                                   r["tag"][2])):
        s, aid, key = r["tag"]
        b = base[(s, key)]
        ev = envelope(r["extremes"], r["rooms"], WINDOW, baseline=b["extremes"])
        cells = {room: v for room, v in ev.items() if v is not None}  # by room id (two rooms share a label)
        caused = {k: v for k, v in cells.items() if v["below_10"]}
        hours = sum(b_ - a_ for a_, b_ in r["setback"]) / 3600
        out = outdoor(envs[key], cache, r["setback"]) if r["setback"] else {"mean_c": None, "min_c": None}
        runs[s].append({
            "automation": names[aid], "env": label(_env(envs[key])), "city": envs[key]["city"].split(",")[0],
            "date": envs[key]["date"], "cells": len(cells), "caused_cells": len(caused),
            "caused": bool(caused), "breached_any_cause": any(v["breached_below_10"] for v in cells.values()),
            "coldest_room_c": _r(min((v["min_c"] for v in cells.values()), default=None)),
            "coldest_caused_room_c": _r(min((v["min_c"] for v in caused.values()), default=None)),
            "baseline_coldest_room_c": _r(min((v["baseline_min_c"] for v in cells.values()
                                               if v["baseline_min_c"] is not None), default=None)),
            "largest_drop_k": _r(-min((v["delta_min_c"] for v in cells.values() if v["delta_min_c"] is not None),
                                      default=0.0)),
            "setback_h": _r(hours), "low_commands": r["low_commands"], "refused": r["refused"],
            "outdoor_mean_over_setback_c": out["mean_c"], "outdoor_min_over_setback_c": out["min_c"],
            "_cells": {k: (v["min_c"], v["baseline_min_c"], v["below_10"]) for k, v in cells.items()},
            "_setback": r["setback"], "_key": key, "_aid": aid})
    return runs


def _per_automation(rows: list[dict], names_sorted: list[str]) -> list[dict]:
    out = []
    for a in names_sorted:
        mine = [r for r in rows if r["automation"] == a]
        hit = [r for r in mine if r["caused"]]
        out.append({
            "automation": a, "runs": len(mine), "runs_with_caused_breach": len(hit),
            "runs_with_setback_in_window": sum(1 for r in mine if r["setback_h"]),
            "breach_envs": sorted(r["env"] for r in hit),
            "coldest_caused_room_c": _r(min((r["coldest_caused_room_c"] for r in hit), default=None)),
            "setback_h_breaching_runs": _span(r["setback_h"] for r in hit),
            "setback_h_all_runs_with_setback": _span(r["setback_h"] for r in mine if r["setback_h"]),
            "outdoor_mean_over_setback_c_breaching": _span(r["outdoor_mean_over_setback_c"] for r in hit),
            "outdoor_min_over_setback_c_breaching": _span(r["outdoor_min_over_setback_c"] for r in hit),
            "baseline_coldest_room_c_breaching_envs": _r(min((r["baseline_coldest_room_c"] for r in hit),
                                                             default=None)),
            "baseline_coldest_room_c_all_envs": _r(min((r["baseline_coldest_room_c"] for r in mine
                                                        if r["baseline_coldest_room_c"] is not None), default=None)),
        })
    return out


def _overall(rows: list[dict]) -> dict:
    hit = [r for r in rows if r["caused"]]
    coldest = sorted(r["coldest_caused_room_c"] for r in hit)
    near = [r for r in hit
            if r["outdoor_mean_over_setback_c"] is not None and r["outdoor_mean_over_setback_c"] < COLD_C]
    by_env = defaultdict(int)
    for r in hit:
        by_env[r["env"]] += 1
    by_city = defaultdict(lambda: [0, 0])
    for r in rows:
        by_city[r["city"]][0] += 1
        by_city[r["city"]][1] += r["caused"]
    return {
        "runs": len(rows), "runs_with_caused_breach": len(hit), "caused_cells": sum(r["caused_cells"] for r in hit),
        "cells": sum(r["cells"] for r in rows),
        "runs_breached_any_cause": sum(1 for r in rows if r["breached_any_cause"]),
        "automations_with_caused_breach": len({r["automation"] for r in hit}),
        "runs_with_setback_in_window": sum(1 for r in rows if r["setback_h"]),
        "runs_with_refused_action": sum(1 for r in rows if r["refused"]),
        "breach_envs": dict(sorted(by_env.items())),
        "by_city_runs_and_breaching": {c: {"runs": v[0], "breaching": v[1]} for c, v in sorted(by_city.items())},
        "coldest_caused_room_per_run_c": ([coldest[0], _r(statistics.median(coldest)), coldest[-1]]
                                          if coldest else None),
        "largest_drop_below_baseline_k": max((r["largest_drop_k"] for r in hit), default=None),
        "setback_h_breaching_runs": _span(r["setback_h"] for r in hit),
        "outdoor_mean_over_setback_c_breaching": _span(r["outdoor_mean_over_setback_c"] for r in hit),
        "outdoor_min_over_setback_c_breaching": _span(r["outdoor_min_over_setback_c"] for r in hit),
        "baseline_coldest_room_c_breaching_envs": _r(min((r["baseline_coldest_room_c"] for r in hit), default=None)),
        "baseline_cells_below_10_all_runs": sum(1 for r in rows for c in r["_cells"].values()
                                                if c[1] is not None and c[1] < COLD_C),
        "near_model_free": {"breaching_runs_with_outdoor_mean_over_setback_below_10_c": len(near),
                            "of": len(hit), "share": _r(len(near) / len(hit), 3) if hit else None},
        "non_breaching_runs_with_setback_and_outdoor_mean_below_10_c": sum(
            1 for r in rows if not r["caused"] and r["outdoor_mean_over_setback_c"] is not None
            and r["outdoor_mean_over_setback_c"] < COLD_C),
        "cold_setback_runs_by_env": _cold_by_env(rows),
    }


def _cold_by_env(rows: list[dict]) -> dict:
    """Runs whose setback's outdoor mean is below 10 C, per city and date: how many, how many breach, their setback
    hours and outdoor mean, and their coldest room."""
    out: dict = {}
    for r in rows:
        if r["outdoor_mean_over_setback_c"] is None or r["outdoor_mean_over_setback_c"] >= COLD_C:
            continue
        k = f"{r['city']} {r['date']}"
        out.setdefault(k, []).append(r)
    return {k: {"runs": len(v), "breaching": sum(r["caused"] for r in v),
                "setback_h": _span(r["setback_h"] for r in v),
                "outdoor_mean_c": _span(r["outdoor_mean_over_setback_c"] for r in v),
                "coldest_room_c": _span(r["coldest_room_c"] for r in v)} for k, v in sorted(out.items())}


def _public(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


# ---- 4. reconciliation ---------------------------------------------------------------------------------------------

def _anon(names: dict, ids) -> list[str]:
    """The anonymous indices of `ids`, in index order (A2 before A10)."""
    return sorted((names[i] for i in ids), key=lambda s: int(s[1:]))


def reconcile(lab: Path, pop: dict, names: dict, sweep: dict, step2: list[dict]) -> dict:
    from vesper.edt.setback import setpoints
    autos = sweep["automations"]
    out = {}
    breaching = {r["_aid"] for r in step2 if r["caused"]}
    for c in CONFIRMS:
        p = lab / f"confirm-rung4-{c}.json"
        if not p.is_file():
            out[c] = None
            continue
        conf = json.loads(p.read_text())
        sampled = {r["id"] for r in conf["results"]}
        breachers = {v["id"] for v in conf["envelope"]["violations"] if v.get("below_10")}
        outside = sorted(breachers - set(pop))
        why = []
        for i in outside:
            a = autos.get(i)
            sp = setpoints(a["bound"]) if a and a["bound"] else []
            why.append({"run_at_rung_4": bool(a and a["outcomes"]), "setpoints": sorted(
                {s.value for s in sp if s.value is not None}), "templated_setpoint": any(s.templated for s in sp)})
        in_pop = sampled & set(pop)
        envs_run = defaultdict(set)
        for r in conf["results"]:
            if r["id"] in pop:
                envs_run[r["id"]].add(r["env_key"])
        out[c] = {"sampled_automations": len(sampled),
                  "population_in_sample": len(in_pop),
                  "population_in_sample_ids": _anon(names, in_pop),
                  "population_in_sample_and_breaching_here": _anon(names, in_pop & breaching),
                  "confirm_breachers": len(breachers),
                  "confirm_breachers_in_population": len(breachers & set(pop)),
                  "confirm_breachers_ids": _anon(names, breachers & set(pop)),
                  "confirm_breachers_outside_population": why,
                  "sampled_population_runs_per_automation": sorted(len(v) for v in envs_run.values()),
                  "population_breaching_here_not_in_sample": len(breaching - sampled)}
    return out


def confirm_reproduction(lab: Path, step2: list[dict]) -> dict:
    """Step 2's cells against the 120f4ca confirmation's for the (automation, environment) pairs both ran."""
    p = lab / "confirm-rung4-120f4ca.json"
    if not p.is_file():
        return {}
    conf = json.loads(p.read_text())
    mine = {(r["_aid"], r["_key"]): r["_cells"] for r in step2}
    cells, dmin, dbase, mism = 0, 0.0, 0.0, 0
    for r in conf["results"]:
        k = (r["id"], r["env_key"])
        if k not in mine:
            continue
        for room, v in r["envelope"].items():
            if v is None:
                continue
            m = mine[k].get(room)
            if m is None:
                continue
            cells += 1
            dmin = max(dmin, abs(v["min_c"] - m[0]))
            dbase = max(dbase, abs(v["baseline_min_c"] - m[1]))
            mism += int(v["below_10"] != m[2])
    return {"cells": cells, "max_abs_min_c_diff": _r(dmin, 3), "max_abs_baseline_min_c_diff": _r(dbase, 3),
            "caused_below_10_mismatches": mism}


# ---- main ----------------------------------------------------------------------------------------------------------

def _scale_summary(rows: list[dict]) -> dict:
    o = _overall(rows)
    return {k: o[k] for k in ("runs", "runs_with_caused_breach", "caused_cells", "cells",
                              "automations_with_caused_breach", "by_city_runs_and_breaching",
                              "coldest_caused_room_per_run_c", "largest_drop_below_baseline_k",
                              "baseline_cells_below_10_all_runs")} | {
        "baseline_coldest_room_c": _r(min((r["baseline_coldest_room_c"] for r in rows
                                           if r["baseline_coldest_room_c"] is not None), default=None)),
        "coldest_room_per_run_c": _span(r["coldest_room_c"] for r in rows),
        "by_automation": {a: sum(1 for r in rows if r["automation"] == a and r["caused"])
                          for a in sorted({r["automation"] for r in rows}, key=lambda s: int(s[1:]))}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", required=True, help="the corpus directory (read-only)")
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--commit", default="120f4ca")
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r2c-scratch"))
    ap.add_argument("--part", default="all", choices=("population", "all"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    out_path = Path(a.out or f"results/edt/setback-population-r2c-{a.commit}.json")
    t_start = time.monotonic()
    sweep = json.loads((lab / f"sweep-rung4-{a.commit}.json").read_text())
    pop, step1 = population(sweep, Path(a.corpus).expanduser(), lab, a.commit, a.catalog, scratch)
    ids = sorted(pop)
    names = {aid: f"A{k + 1}" for k, aid in enumerate(ids)}
    repo_ix: dict[str, str] = {}
    for aid in ids:
        repo_ix.setdefault(sweep["automations"][aid]["repo"], f"R{len(repo_ix) + 1}")
    step1["per_automation"] = [{"automation": names[i], "repository": repo_ix[sweep["automations"][i]["repo"]],
                                "flagged": bool(sweep["automations"][i]["divergences"]), "setpoints_c": pop[i]}
                               for i in ids]
    print(json.dumps({k: v for k, v in step1.items() if k != "per_automation"}, indent=1))
    result = {"analysis": "R2-c the setback population: the below-10 C finding over every automation that commands "
                          "a setpoint below 10 C (post hoc)", "commit_of_inputs": a.commit,
              "step1_population": step1}
    if a.part == "population":
        out_path.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
        print("wrote", out_path)
        return 0

    home = lab / f"work-{a.commit}" / "edt" / "home.yaml"
    cache = lab / f"work-{a.commit}" / "cache"
    envs = {e["key"]: e for e in sweep["envs"]}
    common = {"home": str(home), "cache": str(cache), "catalog": a.catalog}

    def jobs_for(scale: float, keys: list[str]) -> list[dict]:
        js = [{"tag": (scale, None, k), "scale": scale, "bound": None, "env": envs[k], **common} for k in keys]
        js += [{"tag": (scale, aid, k), "scale": scale, "bound": sweep["automations"][aid]["bound"], "env": envs[k],
                **common} for aid in ids for k in keys if k in sweep["automations"][aid]["outcomes"]]
        return js

    t0 = time.monotonic()
    done2 = _pool(jobs_for(1.0, list(envs)), a.workers)
    t2 = time.monotonic() - t0
    step2 = judge(done2, envs, names, cache)[1.0]
    per_auto = _per_automation(step2, [names[i] for i in ids])
    result["step2_closed_loop"] = {
        "rule": "a room-run cell is a caused breach when the room's minimum over days 1-2 is below 10 C with the "
                "automation and not in the same environment's baseline (no automation); setback: hours within "
                "days 1-2 that the last accepted setpoint command is below 10 C; outdoor: ERA5 2 m air "
                "temperature at the site, every 10 min over the setback",
        "environments": len(envs), "overall": _overall(step2), "per_automation": per_auto,
        "breaching_runs": _public([r for r in step2 if r["caused"]]),
        "reproduces_confirmation_120f4ca": confirm_reproduction(lab, step2), "elapsed_s": round(t2, 1)}
    print(json.dumps(result["step2_closed_loop"]["overall"], indent=1))

    breach_keys = sorted({r["_key"] for r in step2 if r["caused"]})
    t0 = time.monotonic()
    done3 = []
    for s in SCALES:
        done3 += _pool(jobs_for(s, breach_keys), a.workers)
    t3 = time.monotonic() - t0
    step3 = judge(done3, envs, names, cache)
    ref = {(r["_aid"], r["_key"]): r for r in step2 if r["_key"] in breach_keys}
    repro = {"runs": 0, "cells": 0, "max_abs_min_c_diff": 0.0, "max_abs_baseline_min_c_diff": 0.0,
             "caused_below_10_mismatches": 0, "setback_mismatches": 0}
    for r in step3[1.0]:
        p = ref[(r["_aid"], r["_key"])]
        repro["runs"] += 1
        repro["setback_mismatches"] += int(p["_setback"] != r["_setback"])
        for room, c in r["_cells"].items():
            q = p["_cells"][room]
            repro["cells"] += 1
            repro["max_abs_min_c_diff"] = max(repro["max_abs_min_c_diff"], abs(c[0] - q[0]))
            repro["max_abs_baseline_min_c_diff"] = max(repro["max_abs_baseline_min_c_diff"], abs(c[1] - q[1]))
            repro["caused_below_10_mismatches"] += int(c[2] != q[2])
    repro["exact"] = (repro["max_abs_min_c_diff"] == 0 and repro["max_abs_baseline_min_c_diff"] == 0
                      and repro["caused_below_10_mismatches"] == 0 and repro["setback_mismatches"] == 0)
    result["step3_mass_sensitivity"] = {
        "scales": list(SCALES), "environments": [label(_env(envs[k])) for k in breach_keys],
        "per_scale": {str(s): _scale_summary(step3[s]) for s in SCALES},
        "s1_reproduces_step2": repro, "elapsed_s": round(t3, 1)}
    result["step4_reconciliation"] = reconcile(lab, pop, names, sweep, step2)
    result["elapsed_s"] = round(time.monotonic() - t_start, 1)
    out_path.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: result[k] for k in ("step3_mass_sensitivity", "step4_reconciliation")}, indent=1))
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
