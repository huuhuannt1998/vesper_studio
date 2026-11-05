#!/usr/bin/env python3
"""r6b_physics.py -- VESPER revision R6-B (post hoc, protocol Addendum 9): physics corners and validation re-checks
(third internal review, items 3(a, b), 8(a-c), 9 and 16(a, b, e)).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `hbreak`   A9.2: the standard home's H breakdown (walls, glass, ventilation; interior partitions apart).
- `ach`      A9.2: R2-c's 22 setback automations closed loop at ACH {0.3, 0.5, 1.0}: at the nominal setting in all 48
             environments, and at R4-B's worst and best corner in the core four (R4-B's closed-loop worker).
- `valid`    A9.3, A9.4, A9.6, A9.7, A9.8: home A free-floating, the aperture k̂ implied by home B's residual (with
             its bootstrap), the daylight chain, the sun at 0-5 deg, ERA5 against Valencia airport's station.
- `openloop` A9.4, A9.5: the affected automations replayed open loop (the sweep's own worker) against traces
             recorded afresh at aperture x k̂ and at the direct share 0.6 and 0.95, and the reference (x1); at
             `120f4ca` (the clock relation) and at `8fcd672` (also the frame relation, reporting rule `device`).
- `report`   results/edt/r6b-<commit>.json: aggregates only (counts, temperatures, environment labels, anonymous
             indices): no automation, repository or entity name, no hash, no path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r6b_physics.py --part P [--lab ~/vesper-lab/edt]
        [--catalog catalog/vds] [--workers 4] [--scratch DIR] [--appliances DIR --sml2010 DIR --weather-cache DIR]

Inputs (local, never committed): --lab/sweep-rung4-{120f4ca,8fcd672}.json, --lab/work-C/edt/home.yaml and their
cached weather; datasets A and B and their ERA5 cache; the station's records, fetched once into --scratch.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pickle
import random
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import r2c_setback_population as r2c  # noqa: E402
import r4b_error_budget as r4b  # noqa: E402  (its corners, workers, judge and affected set, unchanged)

ACHS = (0.3, 0.5, 1.0)
NOMINAL = r4b.NOMINAL
WORST = (0.0, 1, "pss", 80)  # R4-B's unique worst corner over all 48 environments
BEST = (5.0, 0, 1, 370)  # R4-B's best corner at the nominal warm-up
DIRECT_SHARES = (0.6, 0.95)
SEED = 20261105
BOOT = 2000
K_GRID = [round(0.01 * i, 2) for i in range(201)]
FREE_MARGIN_K = 1.0
MIN_FREE_HOURS = 24
COMMITS = {"120f4ca": "catalog", "8fcd672": "device"}  # sweep -> the twins' reporting rule it ran under


# ---- A9.2: the H breakdown -------------------------------------------------------------------------------------------

def h_breakdown(models: dict, ach_values=ACHS) -> dict:
    """Per room and in total: U A of the outside walls and of the glass, 0.34 ACH V; interior partitions apart."""
    from vesper.env import indoor
    rows, tot = {}, Counter()
    for rid, r in sorted(models.items()):
        glass = sum(w["width"] * w["height"] for w in r.windows)
        walls, glazing, vent = indoor.U_WALL * r.outside_wall_m2, indoor.U_WINDOW * glass, 0.34 * indoor.ACH * r.volume
        inner = sum(indoor.U_INTERIOR * a for a in r.neighbours.values())
        rows[rid] = {"walls_w_k": round(walls, 2), "glass_w_k": round(glazing, 2), "ventilation_w_k": round(vent, 2),
                     "total_w_k": round(walls + glazing + vent, 2), "model_h_w_k": round(r.h_w_per_k, 2),
                     "interior_partitions_w_k": round(inner, 2), "floor_m2": round(r.floor_m2, 2),
                     "volume_m3": round(r.volume, 2), "glass_m2": round(glass, 2),
                     "outside_wall_m2": round(r.outside_wall_m2, 2)}
        tot.update({"walls": walls, "glass": glazing, "ventilation": vent, "interior": inner, "volume": r.volume})
    total = tot["walls"] + tot["glass"] + tot["ventilation"]
    return {"rooms": rows, "ach": indoor.ACH,
            "total_w_k": round(total, 2), "walls_w_k": round(tot["walls"], 2), "glass_w_k": round(tot["glass"], 2),
            "ventilation_w_k": round(tot["ventilation"], 2),
            "shares": {k: round(tot[k] / total, 4) for k in ("walls", "glass", "ventilation")},
            "interior_partitions_w_k_counted_twice": round(tot["interior"], 2),
            "ventilation_w_k_at": {str(a): round(0.34 * a * tot["volume"], 2) for a in ach_values},
            "total_w_k_at": {str(a): round(tot["walls"] + tot["glass"] + 0.34 * a * tot["volume"], 2)
                             for a in ach_values}}


def part_hbreak(d, scratch: Path) -> dict:
    from vesper.env.indoor import room_models
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    from vesper.twins.home import load_catalog
    home = load_lab_home(Path(d["home"]), load_catalog(Path("catalog/vds")))
    out = h_breakdown(room_models(home.map, load_windows(Path(d["home"]).parent)))
    (scratch / "hbreak.json").write_text(json.dumps(out, indent=1))
    return out


# ---- A9.2: ACH closed loop -------------------------------------------------------------------------------------------

def ach_label(ach: float, corner) -> str:
    return f"ACH{ach:g}-{r4b.corner_label(corner)}"


def ach_jobs(d: dict, catalog: str) -> list[dict]:
    keys48, core = sorted(d["envs"]), r4b.r2c_envs(d)
    js = []
    for ach in ACHS:
        for corner, keys in ((NOMINAL, keys48), (WORST, core), (BEST, core)):
            for j in r4b.corner_jobs(d, [corner], keys, catalog):
                j["knobs"] = {**j["knobs"], "ACH": ach}
                j["tag"] = (ach_label(ach, corner), j["tag"][1], j["tag"][2])
                js.append(j)
    return js


def part_ach(d, a, scratch: Path) -> None:
    jobs = ach_jobs(d, a.catalog)
    print(f"ach: {len(jobs)} runs", flush=True)
    t0 = time.monotonic()
    done = r4b._pool(r4b._closed_job, jobs, a.workers, "ach")
    r4b._dump(scratch / "ach.pkl", {"done": done, "elapsed_s": time.monotonic() - t0})


def _summary(rows: list[dict], keys: set, n_auto: int) -> dict:
    sel = [r for r in rows if r["_key"] in keys]
    hit = [r for r in sel if r["caused"]]
    coldest = sorted(r["coldest_caused_room_c"] for r in hit)
    return {"breaching_runs": len(hit), "denominator": n_auto * len(keys), "runs": len(sel),
            "caused_cells": sum(r["caused_cells"] for r in hit),
            "automations": len({r["automation"] for r in hit}),
            "by_city": dict(sorted(Counter(r["city"] for r in hit).items())),
            "by_env": dict(sorted(Counter(r["env"] for r in hit).items())),
            "coldest_caused_room_per_run_c": ([coldest[0], r2c._r(statistics.median(coldest)), coldest[-1]]
                                              if coldest else None),
            "baseline_coldest_room_c": r2c._r(min((r["baseline_coldest_room_c"] for r in sel
                                                   if r["baseline_coldest_room_c"] is not None), default=None)),
            "runs_set": sorted((r["automation"], r["_key"]) for r in hit)}


def report_ach(d, scratch: Path, r2c_published: dict) -> dict:
    p = pickle.loads((scratch / "ach.pkl").read_bytes())
    rows = r2c.judge(p["done"], d["envs"], d["names"], Path(d["cache"]))
    n_auto, core, all48 = len(d["pop"]), set(r4b.r2c_envs(d)), set(d["envs"])
    out = {}
    for ach in ACHS:
        nom = rows.get(ach_label(ach, NOMINAL), [])
        out[f"ACH{ach:g}"] = {"nominal_core": _summary(nom, core, n_auto), "nominal_48": _summary(nom, all48, n_auto),
                              "worst_corner_core": _summary(rows.get(ach_label(ach, WORST), []), core, n_auto),
                              "best_corner_core": _summary(rows.get(ach_label(ach, BEST), []), core, n_auto)}
    pub = {(b["automation"], b["env"]) for b in r2c_published["step2_closed_loop"]["breaching_runs"]}
    mine = {(r["automation"], r["env"]) for r in rows[ach_label(0.5, NOMINAL)] if r["caused"] and r["_key"] in core}
    nom48 = out["ACH0.5"]["nominal_48"]
    checks = {"ach0.5_nominal_core_reproduces_r2c": mine == pub, "r2c_breaching_runs": len(pub),
              "ach0.5_nominal_48_breaching_runs": nom48["breaching_runs"],
              "ach0.5_nominal_48_outside_core":
                  nom48["breaching_runs"] - out["ACH0.5"]["nominal_core"]["breaching_runs"]}
    # the same runs, rank-ordered by ACH (a tighter house should not breach where a leakier one does not)
    sets = {ach: set(map(tuple, out[f"ACH{ach:g}"]["nominal_48"]["runs_set"])) for ach in ACHS}
    checks["monotone_0.3_subset_0.5"] = sets[0.3] <= sets[0.5]
    checks["monotone_0.5_subset_1.0"] = sets[0.5] <= sets[1.0]
    for v in out.values():
        for s in v.values():
            s.pop("runs_set")
    return {"settings": out, "checks": checks, "automations": n_auto,
            "core": sorted(r2c.label(r2c._env(d["envs"][k])) for k in core),
            "worst_corner": r4b.corner_label(WORST), "best_corner": r4b.corner_label(BEST),
            "elapsed_s": round(p["elapsed_s"], 1)}


# ---- A9.4, A9.5: open loop -------------------------------------------------------------------------------------------

def settings(aperture: float | None) -> dict[str, dict]:
    out = {"ref": {}}
    if aperture is not None:
        out[f"ap{aperture:g}"] = {"SOLAR_APERTURE_SCALE": aperture}
    for s in DIRECT_SHARES:
        out[f"ds{s:g}"] = {"DIRECT_SHARE": s}
    return out


def _trace_job(job: dict):
    """One environment's trace recorded under the knobs and the affected set replayed on it (the sweep's worker,
    the current seven-field result); the first firing instants kept for the frame relation."""
    from vesper.edt import sweep as sw
    from vesper.env import indoor
    sw._init(job["home"], job["cache"], r4b._bound_from(job["bound_file"]), job["catalog"], sw.BATCH,
             job["reporting_rule"])
    with indoor.knobs(**job["knobs"]):
        key, outcomes, refused, _approx, secs, failure, rich = sw._run_env(r2c._env(job["env"]))
    first_t = {aid: (r or {}).get("first_t") for aid, r in rich.items()}
    return job["setting"], key, outcomes, refused, secs, failure, first_t


def part_openloop(d, a, scratch: Path, commit: str, names: list[str], aperture: float | None,
                  extra: dict | None = None) -> None:
    ids, *_ = r4b.affected(d)
    weather = Path(d["cache"]) / "weather"
    bfile = scratch / f"bound-affected-{commit}.json"
    bfile.write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    table = {**settings(aperture), **(extra or {})}
    for name in names:
        kn = table[name]
        out = scratch / f"openloop-{commit}-{name}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        cache = scratch / f"cache-{commit}-{name}"
        if not (cache / "weather").is_dir():
            shutil.copytree(weather, cache / "weather")
        jobs = [{"setting": name, "knobs": kn, "env": e, "home": d["home"], "cache": str(cache),
                 "bound_file": str(bfile), "catalog": a.catalog, "reporting_rule": COMMITS[commit]}
                for e in d["sweep"]["envs"]]
        t0 = time.monotonic()
        done = r4b._pool(_trace_job, jobs, a.workers, name)
        r4b._dump(out, {"done": done, "knobs": kn, "elapsed_s": time.monotonic() - t0})
        print(f"{commit} {name}: {len(jobs)} environments in {time.monotonic() - t0:.0f} s", flush=True)


def relations(entry_bound: dict, outcomes: dict[str, dict], first_t: dict[str, float | None], env_of: dict) \
        -> tuple[list[str], list[str]]:
    """(clock divergences, frame divergences) of one automation from its outcomes by env key, as the sweep."""
    from vesper.edt.oracle import divergences, frame_divergences, frames_of
    from vesper.edt.sweep import ENVELOPE
    from vesper.edt.types import Outcome
    outs = {k: Outcome(**o) for k, o in outcomes.items()}
    if not outs:
        return [], []
    kinds = divergences(outs)
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
    by_env = {env_of[k]: o for k, o in outs.items()}
    ft = {env_of[k]: first_t.get(k) for k in outs}
    return sorted(kinds - static), sorted(frame_divergences(by_env, ft, frames_of(entry_bound)) - static)


def report_openloop(d, scratch: Path, commit: str, names: list[str]) -> dict:
    from scripts.edt_analysis.home_variation import orientation_witness
    from vesper.edt.cover import _envs
    from vesper.edt.types import Outcome
    ids, orient, temp, hum, _flagged = r4b.affected(d)
    envs = _envs(d["sweep"])
    by_key = {e.key: e for e in envs}
    run = d["run"]
    frame_pub = {i: run[i].get("frame_divergences") for i in ids}
    has_frame = all(v is not None for v in frame_pub.values())
    pub = {"clock": {i: run[i]["divergences"] for i in ids}}
    if has_frame:
        pub["frame"] = frame_pub
    loaded = {n: pickle.loads((scratch / f"openloop-{commit}-{n}.pkl").read_bytes()) for n in names
              if (scratch / f"openloop-{commit}-{n}.pkl").is_file()}
    outs, firsts, divs = {}, {}, {}
    for n, p in loaded.items():
        o, f = defaultdict(dict), defaultdict(dict)
        for _s, key, outcomes, _ref, _secs, _fail, first_t in p["done"]:
            for aid, x in outcomes.items():
                o[aid][key] = x
                f[aid][key] = first_t.get(aid)
        outs[n], firsts[n] = o, f
        divs[n] = {}
        for i in ids:
            c, fr = relations(run[i]["bound"], o.get(i, {}), f.get(i, {}), by_key)
            divs[n][i] = {"clock": c, "frame": fr}

    def witness(n, aid):
        return orientation_witness({by_key[k]: Outcome(**v) for k, v in outs[n].get(aid, {}).items()}, envs)

    def reproduction(n):
        r = {"outcome_mismatches_exact": sum(1 for i in ids for k, o in run[i]["outcomes"].items()
                                             if outs[n].get(i, {}).get(k) != o),
             "outcomes": sum(len(run[i]["outcomes"]) for i in ids),
             "orientation_witnesses": sum(1 for i in ids if witness(n, i)),
             "failed_environments": sum(1 for x in loaded[n]["done"] if x[5])}
        for rel, p in pub.items():
            r[f"{rel}_flag_mismatches"] = sum(1 for i in ids if bool(divs[n][i][rel]) != bool(p[i]))
            r[f"{rel}_kind_mismatches"] = sum(1 for i in ids if divs[n][i][rel] != p[i])
        return r
    res = {"ref": reproduction("ref")} if "ref" in loaded else {}
    for n in loaded:
        if n == "ref":
            continue
        row = {"knobs": loaded[n]["knobs"], "orientation_witnesses_kept": sum(1 for i in orient if witness(n, i)),
               "orientation_witnesses_of": len(orient),
               "orientation_witnesses_kept_by_read_kind": {
                   kind: [sum(1 for i in orient & grp if witness(n, i)), len(orient & grp)]
                   for kind, grp in (("temperature", temp), ("humidity", hum), ("neither", orient - temp - hum))},
               "new_orientation_witnesses": sum(1 for i in set(ids) - orient if witness(n, i)),
               "affected_outcomes_changed": sum(1 for i in ids for k, o in run[i]["outcomes"].items()
                                                if outs[n].get(i, {}).get(k) != o),
               "failed_environments": sum(1 for x in loaded[n]["done"] if x[5]),
               "elapsed_s": round(loaded[n]["elapsed_s"], 1)}
        for rel, p in pub.items():
            flagged = {i for i in ids if p[i]}

            def flags(group, rel=rel, p=p, flagged=flagged):
                g = sorted(group & flagged)
                return {"published": len(g), "still_flagged": sum(1 for i in g if divs[n][i][rel]),
                        "lost": sum(1 for i in g if not divs[n][i][rel]),
                        "kinds_changed": sum(1 for i in g if divs[n][i][rel] != p[i])}
            row[rel] = {"temperature_flagged": flags(temp), "humidity_flagged": flags(hum),
                        "affected_flag_lost": sum(1 for i in ids if p[i] and not divs[n][i][rel]),
                        "affected_flag_gained": sum(1 for i in ids if not p[i] and divs[n][i][rel]),
                        "affected_kinds_changed": sum(1 for i in ids if divs[n][i][rel] != p[i])}
        res[n] = row
    sets = {"automations": len(ids), "orientation_witnessed": len(orient), "temperature_read": len(temp),
            "humidity_read": len(hum)}
    for rel, p in pub.items():
        flagged = {i for i in ids if p[i]}
        sets[f"{rel}_temperature_flagged"] = len(temp & flagged)
        sets[f"{rel}_humidity_flagged"] = len(hum & flagged)
    return {"commit": commit, "reporting_rule": COMMITS[commit], "relations": sorted(pub), "affected_set": sets,
            "settings": res}


# ---- validation: shared ----------------------------------------------------------------------------------------------

def _datasets(a):
    from vesper.env.site import parse_site
    from vesper.validation import environment as ev
    out = {}
    if Path(a.appliances).is_dir():
        df = ev.load_appliances(a.appliances)
        out["A"] = (df, parse_site({"city": "Chièvres, Belgium", "date": df.index[0].date().isoformat()}),
                    {"t_out": "T_out", "rh_out": "RH_out", "rooms": ev.APPLIANCES_ROOMS})
    if Path(a.sml2010).is_dir():
        df = ev.load_sml2010(a.sml2010)
        out["B"] = (df, parse_site({"city": "Valencia, Spain", "date": df.index[0].date().isoformat()}),
                    {"t_out": "t_out", "rh_out": "rh_out", "rooms": ev.SML_ROOMS})
    return out


def _drive(df, site, spec, cache):
    """The registered check's driving series (as indoor_check) and one shared outdoors."""
    from vesper.validation import environment as ev
    era = ev._era5(site, df.index, cache)
    out_t, out_rh = ev._values(df[spec["t_out"]]), ev._values(df[spec["rh_out"]])
    start = df.index[0].to_pydatetime()
    series = (out_t, out_rh, era["shortwave_radiation"], era["cloud_cover"])
    return start, series, ev._MeasuredOutdoor(site, start, *series), era


def _stats(pairs) -> dict:
    pairs = [(m, y) for m, y in pairs if m is not None and y is not None]
    if not pairs:
        return {"n": 0, "rmse_c": None, "bias_c": None, "within_2c": None}
    d = [m - y for m, y in pairs]
    return {"n": len(d), "rmse_c": r2c._r(math.sqrt(sum(x * x for x in d) / len(d)), 3),
            "bias_c": r2c._r(sum(d) / len(d), 3), "within_2c": r2c._r(sum(abs(x) <= 2 for x in d) / len(d), 4)}


# ---- A9.3: home A free-floating --------------------------------------------------------------------------------------

def free_hours(measured: list, setpoint: float, margin: float) -> list[int]:
    """The evidently free-floating hours: T(h) < setpoint - margin and T(h) < T(h - 1), both measured."""
    return [h for h in range(1, len(measured)) if measured[h] is not None and measured[h - 1] is not None
            and measured[h] < setpoint - margin and measured[h] < measured[h - 1]]


def episodes_of(hours: list[int]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive hours, (first, last)."""
    out: list[list[int]] = []
    for h in hours:
        if out and h == out[-1][1] + 1:
            out[-1][1] = h
        else:
            out.append([h, h])
    return [tuple(x) for x in out]


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def free_float(df, site, spec, cache) -> dict:
    from vesper.validation import environment as ev
    start, series, shared, _era = _drive(df, site, spec, cache)
    out = {}
    for margin, name in ((FREE_MARGIN_K, "primary_margin_1K"), (0.0, "sensitivity_margin_0K")):
        rows = []
        for col, label in spec["rooms"].items():
            measured = ev._values(df[col])
            s = ev._percentile(measured, ev.SETPOINT_PERCENTILE)
            hours = free_hours(measured, s, margin)
            eps = episodes_of(hours)
            sim = ev.simulate_free_float(site, start, *series, episodes=[(a_, b_, measured[a_ - 1]) for a_, b_ in eps],
                                         outdoor=shared)
            heated, _ = ev.simulate_room(site, start, *series, s, outdoor=shared)
            persist = {h: measured[a_ - 1] for a_, b_ in eps for h in range(a_, b_ + 1)}
            lengths = [b_ - a_ + 1 for a_, b_ in eps]
            rows.append({"room": label, "setpoint_c": round(s, 2), "hours": len(hours), "episodes": len(eps),
                         "episode_h_median": _median(lengths), "episode_h_max": max(lengths, default=None),
                         "free_float": _stats((sim.get(h), measured[h]) for h in hours),
                         "persistence": _stats((persist[h], measured[h]) for h in hours),
                         "registered_heated_same_hours": _stats((heated[h], measured[h]) for h in hours),
                         "in_median": len(hours) >= MIN_FREE_HOURS})
        kept = [r for r in rows if r["in_median"]]
        summ = {k: {"median_rmse_c": _median(r[k]["rmse_c"] for r in kept),
                    "median_within_2c": _median(r[k]["within_2c"] for r in kept)}
                for k in ("free_float", "persistence", "registered_heated_same_hours")}
        c = ev.CRITERIA["T"]
        ff = summ["free_float"]
        summ["free_float"]["would_pass_T_post_hoc"] = (ff["median_rmse_c"] is not None
                                                      and ff["median_rmse_c"] <= c["rmse_c"]
                                                      and ff["median_within_2c"] >= c["within_2c"])
        out[name] = {"rooms": rows, "rooms_in_median": len(kept), "median": summ,
                     "rule": f"T(h) < s - {margin:g} K and T(h) < T(h-1)"}
    return out


def heating_shares(data, cache) -> dict:
    """A9.3: in the hours the registered check uses, how often its heater runs, and how often the measured room is
    below the proxy setpoint, home A against home B."""
    from vesper.validation import environment as ev
    out = {}
    for name, (df, site, spec) in data.items():
        start, series, shared, _ = _drive(df, site, spec, cache)
        rows = []
        for col, label in spec["rooms"].items():
            measured = ev._values(df[col])
            s = ev._percentile(measured, ev.SETPOINT_PERCENTILE)
            log: list = []
            sim, _ = ev.simulate_room(site, start, *series, s, outdoor=shared, heat_log=log)
            used = [h for h in range(len(measured)) if measured[h] is not None and sim[h] is not None]
            rows.append({"room": label, "setpoint_c": round(s, 2), "hours": len(used),
                         "share_hours_heater_on": r2c._r(sum(1 for h in used if log[h]) / len(used), 4),
                         "mean_heating_duty": r2c._r(sum(log[h] for h in used) / len(used), 4),
                         "share_measured_below_setpoint": r2c._r(sum(1 for h in used if measured[h] < s) / len(used),
                                                                 4)})
        out[name] = {"rooms": rows, "median_share_hours_heater_on": _median(r["share_hours_heater_on"] for r in rows)}
    return out


# ---- A9.4: the aperture k̂ implied by home B -------------------------------------------------------------------------

def root_of(ks: list[float], bias: list[float]) -> float | None:
    """The first k where the bias crosses zero going up (linear between grid points); None if b(k0) > 0 or it never
    reaches 0."""
    if bias[0] > 0:
        return None
    if bias[0] == 0:
        return ks[0]
    for i in range(1, len(ks)):
        if bias[i] >= 0:
            b0, b1 = bias[i - 1], bias[i]
            return ks[i - 1] + (ks[i] - ks[i - 1]) * (-b0) / (b1 - b0)
    return None


def khat(df, site, spec, cache, grid=K_GRID, boot=BOOT, seed=SEED) -> dict:
    from vesper.env import indoor
    from vesper.validation import environment as ev
    start, series, shared, _ = _drive(df, site, spec, cache)
    local_day = [t.tz_convert(site.zone).date() for t in df.index]
    days = sorted(set(local_day))
    day_ix = {dd: i for i, dd in enumerate(days)}
    rooms = {}
    for col, label in spec["rooms"].items():
        measured = ev._values(df[col])
        s = ev._percentile(measured, ev.SETPOINT_PERCENTILE)
        sims = []
        for k in grid:
            with indoor.knobs(SOLAR_APERTURE_SCALE=k):
                sims.append(ev.simulate_room(site, start, *series, s, outdoor=shared)[0])
        used = [h for h in range(len(measured)) if measured[h] is not None and sims[0][h] is not None]
        rooms[label] = {"measured": measured, "sims": sims, "used": used, "setpoint": s}
    # per k: per day sums of the bias (both rooms pooled) and counts
    import numpy as np
    S = np.zeros((len(grid), len(days)))
    N = np.zeros(len(days))
    per_room = {}
    for label, r in rooms.items():
        Sr, Nr = np.zeros((len(grid), len(days))), np.zeros(len(days))
        for h in r["used"]:
            j = day_ix[local_day[h]]
            Nr[j] += 1
            for i in range(len(grid)):
                Sr[i, j] += r["sims"][i][h] - r["measured"][h]
        S += Sr
        N += Nr
        b = (Sr.sum(axis=1) / Nr.sum()).tolist()
        per_room[label] = {"hours": int(Nr.sum()), "bias_at_1": r2c._r(b[grid.index(1.0)], 3),
                           "bias_at_0": r2c._r(b[0], 3), "khat": _r4(root_of(grid, b))}
    b = (S.sum(axis=1) / N.sum()).tolist()
    k = root_of(grid, b)
    rng = random.Random(seed)
    draws = []
    for _ in range(boot):
        w = np.zeros(len(days))
        for _j in range(len(days)):
            w[rng.randrange(len(days))] += 1
        bb = ((S * w).sum(axis=1) / (N * w).sum()).tolist()
        draws.append(root_of(grid, bb))
    ok = sorted(x for x in draws if x is not None)
    ci = [ok[int(0.025 * (len(ok) - 1))], ok[int(math.ceil(0.975 * (len(ok) - 1)))]] if ok else None
    # the cross-check from published quantities: k_lin = 1 - b(1) H / mean solar gain over the same hours
    room, env = ev._standard_room(site, shared)
    used_all = sorted({h for r in rooms.values() for h in r["used"]})
    q = [statistics.fmean(env._solar_w(room, shared.at(h * 3600 + j * ev.STEP_S)) for j in range(3600 // ev.STEP_S))
         for h in used_all]
    qbar = statistics.fmean(q)
    b1 = b[grid.index(1.0)]
    k_lin = 1 - b1 * room.h_w_per_k / qbar
    return {"grid": [grid[0], grid[-1], len(grid)], "hours_pooled": int(N.sum()), "days": len(days),
            "bias_pooled_at_1": r2c._r(b1, 3), "bias_pooled_at_0": r2c._r(b[0], 3),
            "bias_pooled_at": {str(x): r2c._r(b[grid.index(x)], 3) for x in (0.0, 0.25, 0.5, 1.0, 2.0)},
            "khat": _r4(k), "khat_rounded": None if k is None else round(k, 2),
            "bootstrap": {"resamples": boot, "seed": seed, "unit": "local day", "ci95": [_r4(x) for x in ci or []],
                          "no_root": boot - len(ok), "median": _r4(ok[len(ok) // 2]) if ok else None},
            "per_room": per_room, "standard_room_h_w_k": r2c._r(room.h_w_per_k, 3),
            "mean_solar_gain_w": r2c._r(qbar, 2),
            "khat_linear_crosscheck": _r4(k_lin),
            "r4b_bracket": [0.25, 2.0], "bracket_contains_khat": k is not None and 0.25 <= k <= 2.0,
            "setpoints_c": {lb: round(r["setpoint"], 2) for lb, r in rooms.items()}}


def _r4(x):
    return None if x is None else round(x, 4)


# ---- A9.6: the daylight chain ----------------------------------------------------------------------------------------

I_SC = 1367.0  # W/m², the solar constant (as Erbs et al. 1982 used)


def erbs_kd(kt: float) -> float:
    """The diffuse fraction of global horizontal irradiance from the clearness index (Erbs, Klein, Duffie 1982)."""
    if kt <= 0.22:
        return 1.0 - 0.09 * kt
    if kt <= 0.80:
        return 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4
    return 0.165


def extraterrestrial_horizontal(when, lat: float, lon: float, elevation_m: float) -> float:
    from vesper.env.sun import sun_at
    doy = when.timetuple().tm_yday
    e0 = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    el = sun_at(lat, lon, elevation_m, when).elevation
    return I_SC * e0 * max(0.0, math.sin(math.radians(el)))


def window_config_factor(depth: float, x1: float, x2: float, z1: float, z2: float, n: int = 400) -> float:
    """The configuration factor from a horizontal element (normal up) to a vertical rectangle at `depth` in front
    of it spanning x1..x2 sideways and z1..z2 above it: (1/pi) integral of z depth / r^4 (midpoint rule)."""
    dx, dz = (x2 - x1) / n, (z2 - z1) / n
    tot = 0.0
    for i in range(n):
        x = x1 + (i + 0.5) * dx
        for j in range(n):
            z = z1 + (j + 0.5) * dz
            r2 = x * x + depth * depth + z * z
            tot += z * depth / (r2 * r2)
    return tot * dx * dz / math.pi


def point_over_average(depths=(2.0, 3.5), w=4.0, d=4.0, height=2.5, win=(1.2, 1.2), sill=0.9, wp=0.85) -> dict:
    """A9.6 factor 2: a point's daylight factor (sky component under a uniform sky, BRE's split-flux IRC) over the
    room's average daylight factor, both unobstructed, the model's T and R."""
    from vesper.env.indoor import REFLECT, T_GLASS
    glass = win[0] * win[1]
    area = 2 * w * d + 2 * (w + d) * height
    adf90 = T_GLASS * glass * 90.0 / (area * (1 - REFLECT ** 2))
    # BRE split flux: 0.85 W / (A (1 - R)) (C Rfw + 5 Rcw), 0.85 the glass's transmittance: here the model's T
    irc = T_GLASS * glass / (area * (1 - REFLECT)) * (39 * REFLECT + 5 * REFLECT)
    out = {"adf_unobstructed_pct": round(adf90, 4), "irc_pct": round(irc, 4)}
    for depth in depths:
        sc = 100 * T_GLASS * window_config_factor(depth, -win[0] / 2, win[0] / 2, sill - wp, sill + win[1] - wp)
        out[f"depth_{depth:g}m"] = {"sc_pct": round(sc, 4), "df_pct": round(sc + irc, 4),
                                    "ratio_to_average": round((sc + irc) / adf90, 4)}
    return out


FRAME = (0.75, 0.7, 0.8)
MAINT = (0.9, 0.9, 0.96)
THETA = (65.0, 60.0, 80.0)


def daylight_chain(df, site, cache, catalog: str) -> dict:
    from vesper.env import sun as sunmod
    from vesper.env.indoor import SKY_ANGLE
    from vesper.validation import environment as ev
    factors = ev.vesper_daylight_factors(catalog)
    d = ev.daylight_check(df, site, factors)
    local = df.index.tz_convert(site.zone)
    mask = (local.hour >= 10) & (local.hour < 16) & (df["irradiance"] >= 300)
    rows = df[mask]
    era = ev._era5(site, rows.index, cache)
    kd_erbs, kd_model, kts = [], [], []
    for t, ghi, cloud in zip(rows.index, rows["irradiance"], era["cloud_cover"], strict=True):
        mid = (t + dt.timedelta(minutes=30)).to_pydatetime()
        i0 = extraterrestrial_horizontal(mid, site.lat, site.lon, site.elevation_m)
        kt = min(1.2, ghi / i0) if i0 > 0 else None
        kts.append(kt)
        kd_erbs.append(erbs_kd(kt) if kt is not None else None)
        kd_model.append(1 - sunmod.direct_fraction(cloud))
    vm = sorted(factors)[len(factors) // 2]
    measured = d["measured_median_pct"]
    r0 = vm / measured
    f1, f1_model = _median(kd_erbs), _median(kd_model)
    pa = point_over_average()
    f2 = pa["depth_2m"]["ratio_to_average"]
    f2_lo = min(pa["depth_3.5m"]["ratio_to_average"], f2)
    f3 = FRAME[0] * MAINT[0] * THETA[0] / SKY_ANGLE
    f3_lo, f3_hi = FRAME[1] * MAINT[1] * THETA[1] / SKY_ANGLE, FRAME[2] * MAINT[2] * THETA[2] / SKY_ANGLE
    chain = [{"step": "R0: VESPER's ADF median over D's measured median", "factor": None, "residual": r0}]
    res = r0 * f1
    chain.append({"step": "1. global to diffuse (Erbs kd, median over D's hours)", "factor": f1, "residual": res})
    res2 = res * f2
    chain.append({"step": "2. point sensor (room centre) over the room average", "factor": f2, "residual": res2,
                  "range_factor": [f2_lo, f2], "range_residual": [res * f2_lo, res * f2]})
    res3 = res2 * f3
    chain.append({"step": "3. frame 0.75 x maintenance 0.9 x sky angle 65/80", "factor": f3, "residual": res3,
                  "range_factor": [f3_lo, f3_hi], "range_residual": [res * f2_lo * f3_lo, res * f2 * f3_hi]})
    for c in chain:
        for k in ("factor", "residual"):
            c[k] = _r4(c[k])
        for k in ("range_factor", "range_residual"):
            if k in c:
                c[k] = [_r4(x) for x in c[k]]
    return {"hours": int(mask.sum()), "vesper_median_adf_pct": round(vm, 4), "measured_median_pct": measured,
            "measured_rooms_pct": {k: d[k]["median_pct"] for k in ("dining room", "room")},
            "kt_median": _r4(_median(kts)), "kd_erbs_median": _r4(f1), "kd_erbs_range": [_r4(min(kd_erbs)),
                                                                                        _r4(max(kd_erbs))],
            "kd_model_median": _r4(f1_model), "residual_after_1_with_model_kd": _r4(r0 * f1_model),
            "point_over_average": pa, "framing": {"frame": FRAME, "maintenance": MAINT, "theta_deg": THETA},
            "chain": chain,
            "arithmetic": {"R0_unrounded": round(r0, 4), "rounded_inputs_1.76_over_0.12": round(1.76 / 0.12, 4),
                           "daylight_scale_0.0676_inverse": round(1 / 0.0676, 4),
                           "registered_D_pass": d["pass"]}}


# ---- A9.7: the sun at 0-5 deg ----------------------------------------------------------------------------------------

EDT_CITIES = ("Phoenix, AZ, USA", "Minneapolis, MN, USA", "Seattle, WA, USA", "Singapore", "Stockholm, Sweden",
              "Sydney, Australia")
LOW_DAYS = (dt.date(2026, 1, 14), dt.date(2026, 1, 15), dt.date(2026, 1, 16))


def low_sun_errors(site, times, reference) -> dict[str, list[float]]:
    """VESPER's sun against `reference` at `times`, kept where the reference's apparent elevation is in (0, 5]."""
    from vesper.env.sun import sun_at
    from vesper.validation.environment import _separation
    ref, _events = reference(site, times)
    out = {"el": [], "az": [], "sep": []}
    for t, (e, az) in zip(times, ref, strict=True):
        if not 0 < e <= 5:
            continue
        s = sun_at(site.lat, site.lon, site.elevation_m, t)
        out["el"].append(s.elevation - e)
        out["az"].append((s.azimuth - az + 180) % 360 - 180)
        out["sep"].append(_separation(s.elevation, s.azimuth, e, az))
    return out


def low_sun(reference=None, cities=EDT_CITIES, days=LOW_DAYS, step_min: int = 1) -> dict:
    from vesper.env.site import parse_site
    from vesper.validation.environment import _stats as vstats
    if reference is None:
        from vesper.validation.environment import spa_reference as reference
    pooled = {"el": [], "az": [], "sep": []}
    per = {}
    for city in cities:
        errs = {"el": [], "az": [], "sep": []}
        for day in days:
            site = parse_site({"city": city, "date": day.isoformat()})
            midnight = dt.datetime.combine(day, dt.time(0), tzinfo=site.zone)
            times = [midnight + dt.timedelta(minutes=m) for m in range(0, 24 * 60, step_min)]
            for k, v in low_sun_errors(site, times, reference).items():
                errs[k] += v
        per[city.split(",")[0]] = {k: vstats(v) for k, v in errs.items()}
        for k in pooled:
            pooled[k] += errs[k]
    return {"band_deg": [0, 5], "step_min": step_min, "days": [x.isoformat() for x in days],
            "pooled": {k: vstats(v) for k, v in pooled.items()}, "per_city": per,
            "registered_S_bound_above_5_deg": 0.05}


# ---- A9.8: ERA5 against the station ----------------------------------------------------------------------------------

ISD_GOOD = {"0", "1", "4", "5", "9"}


def station_hourly(path: Path, index) -> list[float | None]:
    """ISD global-hourly TMP (tenths of C, quality code), the mean of the passing reports in each UTC hour [h, h+1)
    of `index` (UTC, left-labelled as the dataset's resampling)."""
    import csv
    sums: dict = defaultdict(lambda: [0.0, 0])
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            tmp = row.get("TMP") or ""
            if "," not in tmp:
                continue
            v, q = tmp.split(",", 1)
            if v.strip() in ("+9999", "-9999") or q.strip() not in ISD_GOOD:
                continue
            when = dt.datetime.fromisoformat(row["DATE"]).replace(tzinfo=dt.UTC)
            key = when.replace(minute=0, second=0, microsecond=0)
            sums[key][0] += int(v) / 10
            sums[key][1] += 1
    return [(sums[t.to_pydatetime()][0] / sums[t.to_pydatetime()][1]) if sums.get(t.to_pydatetime()) else None
            for t in index]


def _agree(a, b) -> dict:
    from vesper.validation.environment import _agreement
    x = _agreement(a, b)
    return {k: x[k] for k in ("n", "bias", "rmse", "r")}


def era5_vs_station(df, site, cache, station: Path) -> dict:
    from vesper.validation import environment as ev
    if not station.is_file():
        return {"available": False}
    era = ev._era5(site, df.index, cache)["temperature_2m"]
    house = ev._values(df["t_out"])
    st = station_hourly(station, df.index)
    keep = [i for i in range(len(df)) if era[i] is not None and house[i] is not None and st[i] is not None]
    e, h, s = ([x[i] for i in keep] for x in (era, house, st))
    return {"available": True, "source": "NOAA ISD global-hourly, VALENCIA, SP (Manises airport), USAF 082840 "
                                          "WBAN 99999, via the NOAA open data bucket; no login",
            "station_hours_with_a_report": sum(1 for x in st if x is not None), "dataset_hours": len(df),
            "hours_all_three": len(keep),
            "era5_minus_station": _agree(e, s), "house_minus_station": _agree(h, s),
            "era5_minus_house": _agree(e, h),
            "era5_minus_house_all_house_hours": _agree(era, house)}


# ---- valid -----------------------------------------------------------------------------------------------------------

def part_valid(a, scratch: Path, only: list[str] | None = None) -> dict:
    path = scratch / "valid.json"
    out = json.loads(path.read_text()) if path.is_file() else {}
    data = _datasets(a)
    cache = Path(a.weather_cache)

    def want(x):
        return only is None or x in only
    if want("free") and "A" in data:
        out["item8b_free_float_A"] = free_float(*data["A"], cache)
        out["item8b_heating_shares"] = heating_shares(data, cache)
        from vesper.validation.environment import STANDARD_ROOM
        out["item8c_geometry"] = {"both_checks_drive": STANDARD_ROOM,
                                  "note": "the standard room of vesper.validation.environment, for every room of "
                                          "home A and home B; neither home's own geometry"}
    if want("khat") and "B" in data:
        out["item3_khat"] = khat(*data["B"], cache)
    if want("daylight") and "B" in data:
        out["item9_daylight_chain"] = daylight_chain(data["B"][0], data["B"][1], cache, a.catalog)
    if want("sun"):
        out["item16a_low_sun"] = low_sun()
    if want("station") and "B" in data:
        out["item16b_station"] = era5_vs_station(data["B"][0], data["B"][1], cache, Path(a.station))
    path.write_text(json.dumps(out, indent=1, default=str))
    return out


# ---- main ------------------------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("hbreak", "ach", "valid", "openloop", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--commit", default="120f4ca", choices=sorted(COMMITS))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r6-B/scratch"))
    ap.add_argument("--settings", default="ref", help="openloop: comma-separated settings (ref, ap<k>, ds0.6, ds0.95)")
    ap.add_argument("--aperture", type=float, default=None, help="openloop: k̂ (the ap<k> setting)")
    ap.add_argument("--extra-apertures", default="", help="openloop: comma-separated further aperture scales")
    ap.add_argument("--appliances", default=".vesper/datasets/uci-appliances")
    ap.add_argument("--sml2010", default=".vesper/datasets/uci-sml2010")
    ap.add_argument("--weather-cache", default=".vesper/datasets/era5")
    ap.add_argument("--station", default=str(Path.home() / "vesper-lab/edt/r6-B/station/08284099999-2012.csv"))
    ap.add_argument("--only", default="", help="valid: comma-separated subset (free, khat, daylight, sun, station)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    import vesper
    print("vesper from", Path(vesper.__file__).parent, flush=True)
    if a.part == "valid":
        out = part_valid(a, scratch, only=a.only.split(",") if a.only else None)
        print(json.dumps(out, indent=1, default=str)[:6000])
        return 0
    if a.part == "report":
        return report(a, lab, scratch)
    d = r4b.load(lab, a.commit)
    if a.part == "hbreak":
        print(json.dumps(part_hbreak(d, scratch), indent=1))
    elif a.part == "ach":
        part_ach(d, a, scratch)
    elif a.part == "openloop":
        extra = {f"ap{float(x):g}": {"SOLAR_APERTURE_SCALE": float(x)} for x in a.extra_apertures.split(",") if x}
        part_openloop(d, a, scratch, a.commit, a.settings.split(","), a.aperture, extra)
    return 0


def report(a, lab: Path, scratch: Path) -> int:
    import subprocess
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
    out = {"analysis": "R6-B (protocol Addendum 9, post hoc): infiltration corners, home A free-floating, the aperture "
                       "implied by home B, the daylight chain, the direct share, the sun at 0-5 deg, ERA5 against the "
                       "station", "inputs": {"clock": "120f4ca", "frame": "8fcd672"}, "code_commit": commit7}
    d = r4b.load(lab, "120f4ca")
    out["item8a_h_breakdown"] = json.loads((scratch / "hbreak.json").read_text())
    if (scratch / "ach.pkl").is_file():
        pub = json.loads(Path("results/edt/setback-population-r2c-120f4ca.json").read_text())
        out["item8a_ach"] = report_ach(d, scratch, pub)
    if (scratch / "valid.json").is_file():
        out["validation"] = json.loads((scratch / "valid.json").read_text())
    ol = {}
    for commit in COMMITS:
        names = sorted(p.stem.split(f"openloop-{commit}-")[1] for p in scratch.glob(f"openloop-{commit}-*.pkl"))
        if names:
            dc = d if commit == "120f4ca" else r4b.load(lab, commit)
            ol[commit] = report_openloop(dc, scratch, commit, names)
    out["items3_16e_open_loop"] = ol
    path = Path(a.out or f"results/edt/r6b-{commit7}.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
