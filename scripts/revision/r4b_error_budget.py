#!/usr/bin/env python3
"""r4b_error_budget.py -- VESPER revision R4-B (post hoc, protocol Addendum 7): the physics error budget (review
items 4 and 5) and isolation versus the rule set (review item 6).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `screen`  A7.2 stage 1: the 22 setback automations of R2-c, each alone, closed loop, in all 48 environments, at the
            four screen corners (internal gains 0, roof and ground on, 80 kJ/(m² K), each warm-up length), each corner
            with its own baselines.
- `full`    A7.2 stage 2: all 120 corners (gains {0, 2, 5} W/m² x roof and ground {off, on} x warm-up {1, 5, 10 days,
            periodic} x heat capacity {80, 110, 165, 260, 370} kJ/(m² K)) in E (the screen's breach environments
            and R2-c's four).
- `openloop` A7.3: the affected automations (temperature or humidity read, or an orientation witness) replayed open
            loop (vesper.edt.sweep's own worker) in all 48 environments against traces recorded afresh with the
            solar aperture x {0.25, 0.5, 1, 2}, the outdoor air -2 / +2 K, the humidity class 2 and 3 (270, 540,
            810 Pa per class; 1 is the reference); at aperture x2 and +2 K every automation that runs is replayed.
- `clamp`   A7.3: the baseline home's room relative-humidity series (7 rooms x 48 environments, days 1-2) at each
            setting, and how many reach 85 % (the model's clamp), 70 % and 55 %.
- `context` A7.4: the setback automations and the night_light ones closed loop alone and with their repository's
            other automations that bind and run at rung 4 (vesper.edt.confirm.closed_loop's `companions`).
- `report`  results/edt/error-budget-r4b-<commit>.json: aggregates only (counts, temperatures, environment labels,
            anonymous indices A1.., R1..): no automation, repository or entity name, no hash, no path.

    PYTHONPATH=. nice -n 10 .venv/bin/python scripts/revision/r4b_error_budget.py --part P [--lab ~/vesper-lab/edt]
        [--commit 120f4ca] [--catalog catalog/vds] [--workers 4] [--scratch DIR] [--corpus DIR]

Inputs (local, never committed): --lab/sweep-rung4-C.json, --lab/work-C/edt/home.yaml and work-C/cache/weather;
results/edt/setback-population-r2c-C.json (R2-c's published numbers, for the reproduction check). `--corpus` (read
only, optional, `context` only): counts each involved repository's automations in the corpus as a check of the
sweep's record.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import pickle
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r2c_setback_population as r2c  # noqa: E402  (its judge, extremes and outdoor helpers, unchanged)

DAYS = 3
DAY_S = 86400.0
WINDOW = (1, DAYS - 1)
COLD_C = 10.0
GAINS = (0.0, 2.0, 5.0)
FABRIC = (0, 1)
WARMUPS = (1, 5, 10, "pss")  # days (day 0 of the run counts as the first), or the periodic steady state
CAPACITIES = (80, 110, 165, 260, 370)  # EN ISO 13790 internal heat capacity classes, kJ/(m² K) of floor
NOMINAL_C = 165
NOMINAL = (0.0, 0, 1, NOMINAL_C)
SCREEN = [(0.0, 1, w, 80) for w in WARMUPS]
R2C_ENVS = ("Minneapolis", "Stockholm")
R2C_DATE = "2026-01-14"
MAX_E_FULL = 8
APERTURES = (0.25, 0.5, 1.0, 2.0)
OFFSETS = (-2.0, 2.0)
CLASSES = (1, 2, 3)
CLAMP_PCT, THRESHOLDS = 85, (70, 55)
SETTINGS = {  # open-loop settings: name -> indoor knobs; "ap1" is the reference (the model as built)
    "ap0.25": {"SOLAR_APERTURE_SCALE": 0.25}, "ap0.5": {"SOLAR_APERTURE_SCALE": 0.5}, "ap1": {},
    "ap2": {"SOLAR_APERTURE_SCALE": 2.0}, "out-2": {"OUTDOOR_T_OFFSET_C": -2.0}, "out+2": {"OUTDOOR_T_OFFSET_C": 2.0},
    "hc2": {"HUMIDITY_CLASS": 2}, "hc3": {"HUMIDITY_CLASS": 3},
}
FULL_CHECK = ("ap2", "out+2")  # every run automation replayed: those outside the affected set must not move
STATIC_NL_ENVS = 2
LIGHT_SERVICES = ("light.turn_on", "light.turn_off", "light.toggle")


# ---- corners ---------------------------------------------------------------------------------------------------------

def corner_label(c) -> str:
    g, f, w, cap = c
    return f"G{g:g}-F{'on' if f else 'off'}-W{w}-C{cap}"


def corner_knobs(c) -> dict:
    g, f, w, cap = c
    return {"INTERNAL_GAINS_W_PER_M2": float(g), "ROOF_GROUND": float(f),
            "PREWARM_DAYS": math.inf if w == "pss" else float(w - 1), "CAPACITY_SCALE": cap / NOMINAL_C}


ALL_CORNERS = list(itertools.product(GAINS, FABRIC, WARMUPS, CAPACITIES))


# ---- workers ---------------------------------------------------------------------------------------------------------

_BOUND_FILES: dict[str, dict] = {}


def _catalog_once() -> None:
    """The device catalog parsed once per worker process, not once per run (a fifth of a run's time; the catalog is
    only read; the nominal corner's exact reproduction of R2-c checks that nothing changed)."""
    from vesper.twins import home as twins_home
    if getattr(twins_home, "_r4b_once", False):
        return
    original, parsed = twins_home.load_catalog, {}

    def once(catalog_dir):
        key = str(Path(catalog_dir).resolve())
        if key not in parsed:
            parsed[key] = original(catalog_dir)
        return parsed[key]
    twins_home.load_catalog = once
    twins_home._r4b_once = True


def _closed_job(job: dict) -> dict:
    """One closed-loop run: the target alone (`companions` None), or with companions (labelled by index). Returns
    each room's extremes over days 1-2, the setback intervals (target's commands, and all commands), and the light
    and climate commands per label."""
    from vesper.edt.confirm import closed_loop
    from vesper.edt.setback import setback_intervals
    from vesper.env import indoor
    _catalog_once()
    with indoor.knobs(**job["knobs"]):
        r = closed_loop(job["bound"], r2c._env(job["env"]), Path(job["home"]), Path(job["cache"]), days=DAYS,
                        catalog_dir=job["catalog"], companions=job.get("companions"))
    lo, hi = WINDOW[0] * DAY_S, (WINDOW[1] + 1) * DAY_S
    climate = [{k: row.get(k) for k in ("t", "service", "feature", "value", "accepted", "kind", "automation",
                                         "entity")}
               for row in r["rows"] if str(row.get("service", "")).startswith("climate.")]
    target = climate if job.get("companions") is None else [x for x in climate if x["automation"] == "edt:0"]
    out = {"tag": job["tag"], "rooms": r["rooms"], "extremes": r2c._extremes(r["temperatures"]),
           "setback": setback_intervals(target, lo, hi),
           "low_commands": sum(1 for x in target if x.get("accepted", True) and isinstance(x["value"], (int, float))
                               and x["value"] < COLD_C),
           "refused": bool(r["refused"]), "firings": len(r["firings"])}
    if job.get("companions") is not None:
        out["setback_all"] = setback_intervals(climate, lo, hi)
        out["climate"] = [(x["t"], x["automation"], x["entity"], x["value"], x.get("accepted", True))
                          for x in climate if isinstance(x["value"], (int, float))]
        out["lights"] = [(f.t, f.automation, f.service, f.target) for fs in r["firings_by"].values() for f in fs
                         if f.service in LIGHT_SERVICES]
        out["outcome_alone_rule"] = {k: getattr(r["outcome"], k) for k in ("fired", "first_tod_h", "flaps",
                                                                            "night_light")}
    return out


def _pool(fn, jobs: list[dict], workers: int, log: str = "") -> list:
    t0 = time.monotonic()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
            out = []
            for k, r in enumerate(pool.map(fn, jobs, chunksize=1)):
                out.append(r)
                if (k + 1) % 200 == 0:
                    print(f"{log} {k + 1}/{len(jobs)} {time.monotonic() - t0:.0f} s", flush=True)
            return out
    return [fn(j) for j in jobs]


# ---- common inputs ---------------------------------------------------------------------------------------------------

def load(lab: Path, commit: str) -> dict:
    from vesper.edt.setback import low_setpoints
    sweep = json.loads((lab / f"sweep-rung4-{commit}.json").read_text())
    autos = sweep["automations"]
    run = {i: a for i, a in autos.items() if a["outcomes"]}
    pop = sorted(i for i in run if low_setpoints(run[i]["bound"]))
    names = {aid: f"A{k + 1}" for k, aid in enumerate(pop)}
    envs = {e["key"]: e for e in sweep["envs"]}
    return {"sweep": sweep, "autos": autos, "run": run, "pop": pop, "names": names, "envs": envs,
            "home": str(lab / f"work-{commit}" / "edt" / "home.yaml"),
            "cache": str(lab / f"work-{commit}" / "cache")}


def corner_jobs(d: dict, corners, keys, catalog: str) -> list[dict]:
    common = {"home": d["home"], "cache": d["cache"], "catalog": catalog}
    js = []
    for c in corners:
        kn = corner_knobs(c)
        js += [{"tag": (corner_label(c), None, k), "knobs": kn, "bound": None, "env": d["envs"][k], **common}
               for k in keys]
        js += [{"tag": (corner_label(c), aid, k), "knobs": kn, "bound": d["run"][aid]["bound"], "env": d["envs"][k],
                **common} for aid in d["pop"] for k in keys if k in d["run"][aid]["outcomes"]]
    return js


def _dump(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(obj))
    tmp.replace(path)


def r2c_envs(d: dict) -> list[str]:
    return sorted(k for k, e in d["envs"].items() if e["date"] == R2C_DATE and e["city"].split(",")[0] in R2C_ENVS)


# ---- A7.2 ------------------------------------------------------------------------------------------------------------

def part_screen(d, a, scratch: Path) -> None:
    jobs = corner_jobs(d, SCREEN, sorted(d["envs"]), a.catalog)
    print(f"screen: {len(jobs)} runs", flush=True)
    t0 = time.monotonic()
    done = _pool(_closed_job, jobs, a.workers, "screen")
    _dump(scratch / "screen.pkl", {"done": done, "elapsed_s": time.monotonic() - t0})


def e_set(d, scratch: Path) -> list[str]:
    screen = pickle.loads((scratch / "screen.pkl").read_bytes())
    rows = r2c.judge(screen["done"], d["envs"], d["names"], Path(d["cache"]))
    keys = {r["_key"] for rs in rows.values() for r in rs if r["caused"]}
    return sorted(keys | set(r2c_envs(d)))


def part_full(d, a, scratch: Path) -> None:
    keys = e_set(d, scratch)
    core = r2c_envs(d)
    jobs = corner_jobs(d, ALL_CORNERS, core, a.catalog)
    rest = [k for k in keys if k not in core]
    if rest:
        corners = ALL_CORNERS if len(keys) <= MAX_E_FULL else [c for c in ALL_CORNERS if c[2] in (1, "pss")]
        jobs += corner_jobs(d, corners, rest, a.catalog)
    # the screen corners' runs in E are the same runs (deterministic): taken from stage 1, not run again
    screen = {r["tag"]: r for r in pickle.loads((scratch / "screen.pkl").read_bytes())["done"]}
    reused = [screen[j["tag"]] for j in jobs if j["tag"] in screen]
    jobs = [j for j in jobs if j["tag"] not in screen]
    print(f"full: E = {len(keys)} environments, {len(jobs)} runs (+{len(reused)} from the screen)", flush=True)
    t0 = time.monotonic()
    done = reused + _pool(_closed_job, jobs, a.workers, "full")
    _dump(scratch / "full.pkl", {"done": done, "E": keys, "reduced_outside_core": len(keys) > MAX_E_FULL,
                                 "elapsed_s": time.monotonic() - t0})


def _city(label: str) -> str:
    return label.split(" ")[0]


def _corner_summary(rows: list[dict], sets: dict[str, set], n_auto: int, repo_of: dict) -> dict:
    """A corner's numbers: breaching runs over each named environment set (numerator / 22 x |set|; None where the
    corner did not run every environment of the set), and the details over the runs it has."""
    hit = [r for r in rows if r["caused"]]
    ran = {r["_key"] for r in rows}
    coldest = sorted(r["coldest_caused_room_c"] for r in hit)
    out = {}
    for name, keys in sets.items():
        complete = keys <= ran or name == "48"
        out[f"breaching_runs_{name}"] = sum(1 for r in hit if r["_key"] in keys) if complete else None
        out[f"denominator_{name}"] = n_auto * len(keys)
    return out | {
        "caused_cells": sum(r["caused_cells"] for r in hit),
        "automations": len({r["automation"] for r in hit}),
        "repositories": len({repo_of[r["automation"]] for r in hit}),
        "by_city": dict(sorted(Counter(r["city"] for r in hit).items())),
        "by_env": dict(sorted(Counter(r["env"] for r in hit).items())),
        "coldest_caused_room_per_run_c": ([coldest[0], r2c._r(statistics.median(coldest)), coldest[-1]]
                                          if coldest else None),
        "baseline_coldest_room_c": r2c._r(min((r["baseline_coldest_room_c"] for r in rows
                                               if r["baseline_coldest_room_c"] is not None), default=None)),
        "baseline_cells_below_10": sum(1 for r in rows for c in r["_cells"].values()
                                       if c[1] is not None and c[1] < COLD_C),
        "setback_h_breaching": r2c._span(r["setback_h"] for r in hit),
        "environments_run": len(ran)}


def report_corners(d, scratch: Path, r2c_published: dict) -> dict:
    screen = pickle.loads((scratch / "screen.pkl").read_bytes())
    full = pickle.loads((scratch / "full.pkl").read_bytes())
    cache = Path(d["cache"])
    srows = r2c.judge(screen["done"], d["envs"], d["names"], cache)
    frows = r2c.judge(full["done"], d["envs"], d["names"], cache)
    keys = full["E"]
    repo_ix: dict[str, str] = {}
    for aid in d["pop"]:
        repo_ix.setdefault(d["run"][aid]["repo"], f"R{len(repo_ix) + 1}")
    repo_of = {d["names"][aid]: repo_ix[d["run"][aid]["repo"]] for aid in d["pop"]}
    n_auto = len(d["pop"])
    core = set(r2c_envs(d))
    january = {k for k in keys if d["envs"][k]["date"].endswith("-01-14")}
    j_keys = {r["_key"] for rs in list(frows.values()) + list(srows.values()) for r in rs
              if r["caused"] and r["_key"] in january}
    # the environment sets every corner is counted over: R2-c's four (every corner ran them), J (the January
    # environments with a breach at any corner), all 48 (outside E counted as no breach, A7.2)
    sets = {"core": core, "J": j_keys, "48": set(d["envs"])}
    stage1 = {lab: _corner_summary(rows, sets, n_auto, repo_of) for lab, rows in sorted(srows.items())}
    per = {}
    for c in ALL_CORNERS:
        lab = corner_label(c)
        per[lab] = {"gains_w_m2": c[0], "roof_ground": bool(c[1]), "warmup": c[2], "capacity_kj_m2k": c[3],
                    **_corner_summary(frows.get(lab, []), sets, n_auto, repo_of)}
        if per[lab]["environments_run"] < len(keys):  # W 5 or 10 outside the core (A7.2's fallback)
            per[lab]["breaching_runs_48"] = None

    def span(name):
        vals = {lab: p[f"breaching_runs_{name}"] for lab, p in per.items() if p[f"breaching_runs_{name}"] is not None}
        lo, hi = min(vals.values()), max(vals.values())
        return {"corners": len(vals), "min": lo, "max": hi, "denominator": n_auto * len(sets[name]),
                "best_corners": sorted(lab for lab, v in vals.items() if v == lo),
                "worst_corners": sorted(lab for lab, v in vals.items() if v == hi),
                "nominal": per[corner_label(NOMINAL)][f"breaching_runs_{name}"],
                "corners_with_no_breach": sum(1 for v in vals.values() if v == 0)}

    def marginal(i, levels):
        out = {}
        for lv in levels:
            xs = [per[corner_label(c)]["breaching_runs_core"] for c in ALL_CORNERS if c[i] == lv]
            out[str(lv)] = {"mean": r2c._r(statistics.fmean(xs)), "min": min(xs), "max": max(xs)}
        return out
    # monotonicity against the screen corner of the same warm-up (on E)
    by_key = {lab: {(r["automation"], r["_key"]) for r in rows if r["caused"]} for lab, rows in frows.items()}
    mono = {}
    for w in WARMUPS:
        scr = by_key.get(corner_label((0.0, 1, w, 80)), set())
        extra = Counter()
        for c in ALL_CORNERS:
            if c[2] == w:
                for _run in by_key.get(corner_label(c), set()) - scr:
                    extra[corner_label(c)] += 1
        mono[str(w)] = {"runs_breaching_at_a_corner_not_at_the_screen_corner": sum(extra.values()),
                        "corners_with_such_runs": len(extra)}
    every = {city: all(p["by_city"].get(city, 0) > 0 for p in per.values()) for city in ("Minneapolis", "Stockholm")}
    every_j = {city: all(p["by_city"].get(city, 0) > 0 for p in per.values() if p["breaching_runs_J"] is not None)
               for city in ("Seattle",)}
    # nominal reproduces R2-c (on R2-c's four environments)
    nom = [r for r in frows[corner_label(NOMINAL)] if r["_key"] in core]
    pub = {(b["automation"], b["env"]): b for b in r2c_published["step2_closed_loop"]["breaching_runs"]}
    mine = {(r["automation"], r["env"]): r for r in nom if r["caused"]}
    fields = ("coldest_caused_room_c", "caused_cells", "baseline_coldest_room_c", "setback_h", "largest_drop_k")
    repro = {"breaching_runs_here": len(mine), "breaching_runs_r2c": len(pub),
             "same_runs": set(mine) == set(pub),
             "field_mismatches": sum(1 for k in set(mine) & set(pub) for f in fields if mine[k][f] != pub[k][f])}
    repro["exact"] = repro["same_runs"] and repro["field_mismatches"] == 0
    label = lambda k: r2c.label(r2c._env(d["envs"][k]))  # noqa: E731
    return {
        "design": {"corners": len(ALL_CORNERS), "factors": {"internal_gains_w_m2": list(GAINS),
                                                           "roof_ground": ["off", "on"],
                                                           "warmup": [str(w) for w in WARMUPS],
                                                           "capacity_kj_m2k": list(CAPACITIES)},
                   "screen_corners": [corner_label(c) for c in SCREEN], "screen_environments": len(d["envs"]),
                   "E": [label(k) for k in keys], "core": sorted(label(k) for k in core),
                   "J": sorted(label(k) for k in j_keys),
                   "reduced_outside_core": full["reduced_outside_core"],
                   "note": "E holds 9 environments (> 8): outside the core only warm-up 1 day and periodic were run "
                           "(A7.2), so a W5 or W10 corner has a core count and no J or 48 count",
                   "automations": n_auto, "rooms_start_c": 20.5, "baseline_setpoint_c": 20.0,
                   "thermostat": "hallway, heats every indoor room, band 0.3 K, 100 W/m2 of floor",
                   "outside_E": "counted as no breach (A7.2); checked by monotonicity on E"},
        "stage1_all_48": stage1,
        "per_corner": per,
        "summary": {"core": span("core"), "J": span("J"), "48": span("48"),
                    "city_breaches_at_every_corner_core": every, "seattle_breaches_at_every_J_corner": every_j,
                    "marginal_core": {"internal_gains_w_m2": marginal(0, GAINS), "roof_ground": marginal(1, FABRIC),
                                      "warmup": marginal(2, WARMUPS), "capacity_kj_m2k": marginal(3, CAPACITIES)}},
        "checks": {"monotone_vs_screen_corner": mono, "screen_runs_in_E_reused_in_stage_2": True,
                   "nominal_reproduces_r2c_step2": repro},
        "elapsed_s": {"screen": round(screen["elapsed_s"], 1), "full": round(full["elapsed_s"], 1)},
    }


# ---- A7.3 open loop --------------------------------------------------------------------------------------------------

def affected(d: dict) -> tuple[list[str], set, set, set, set]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.edt_analysis.home_variation import orientation_witness
    from vesper.edt.cover import _envs, outcomes_of
    envs = _envs(d["sweep"])
    run = d["run"]
    flagged = {i for i, a in run.items() if a["divergences"]}
    orient = {i for i in flagged if orientation_witness(outcomes_of(run[i], envs), envs)}
    temp = {i for i in run if "temperature" in run[i]["kinds"]}
    hum = {i for i in run if "humidity" in run[i]["kinds"]}
    return sorted(orient | temp | hum), orient, temp, hum, flagged


def _bound_from(path: str) -> dict:
    if path not in _BOUND_FILES:
        _BOUND_FILES.clear()
        _BOUND_FILES[path] = json.loads(Path(path).read_text())
    return _BOUND_FILES[path]


def _trace_job(job: dict):
    from vesper.edt import sweep as sw
    from vesper.env import indoor
    sw._init(job["home"], job["cache"], _bound_from(job["bound_file"]), job["catalog"], sw.BATCH)
    with indoor.knobs(**job["knobs"]):
        key, outcomes, refused, _approx, secs, failure = sw._run_env(r2c._env(job["env"]))
    return job["setting"], key, outcomes, refused, secs, failure


def part_openloop(d, a, scratch: Path, only: list[str] | None = None) -> None:
    ids, *_ = affected(d)
    weather = Path(d["cache"]) / "weather"
    bfiles = {"affected": scratch / "bound-affected.json", "all": scratch / "bound-all.json"}
    bfiles["affected"].write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    bfiles["all"].write_text(json.dumps({i: a_["bound"] for i, a_ in d["run"].items()}))
    for name, kn in SETTINGS.items():
        if only and name not in only:
            continue
        out = scratch / f"openloop-{name}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        cache = scratch / f"cache-{name}"
        if not (cache / "weather").is_dir():
            shutil.copytree(weather, cache / "weather")
        which = "all" if name in FULL_CHECK else "affected"
        jobs = [{"setting": name, "knobs": kn, "env": e, "home": d["home"], "cache": str(cache),
                 "bound_file": str(bfiles[which]), "catalog": a.catalog} for e in d["sweep"]["envs"]]
        t0 = time.monotonic()
        done = _pool(_trace_job, jobs, a.workers, name)
        _dump(out, {"done": done, "which": which, "elapsed_s": time.monotonic() - t0})
        print(f"{name}: {len(jobs)} environments in {time.monotonic() - t0:.0f} s", flush=True)


def _outcomes(done) -> dict[str, dict[str, dict]]:
    outs: dict[str, dict] = defaultdict(dict)
    for _s, key, outcomes, _ref, _secs, _f in done:
        for aid, o in outcomes.items():
            outs[aid][key] = o
    return outs


def report_openloop(d, scratch: Path) -> dict:
    from scripts.edt_analysis.home_variation import orientation_witness
    from vesper.edt.cover import _envs
    from vesper.edt.types import Outcome
    ids, orient, temp, hum, flagged = affected(d)
    envs = _envs(d["sweep"])
    by_key = {e.key: e for e in envs}
    res = {}
    loaded = {n: pickle.loads((scratch / f"openloop-{n}.pkl").read_bytes()) for n in SETTINGS
              if (scratch / f"openloop-{n}.pkl").is_file()}
    divs = {}
    outs = {}
    for n, p in loaded.items():
        outs[n] = _outcomes(p["done"])
        divs[n] = {aid: r2a_div(outs[n].get(aid, {})) for aid in ids}
    published = {aid: d["run"][aid]["divergences"] for aid in ids}

    def witness(n, aid):
        o = {by_key[k]: Outcome(**v) for k, v in outs[n].get(aid, {}).items()}
        return orientation_witness(o, envs)
    ref = "ap1"
    repro = {"flag_mismatches": sum(1 for i in ids if bool(divs[ref][i]) != bool(published[i])),
             "kind_mismatches": sum(1 for i in ids if divs[ref][i] != published[i]),
             "outcome_mismatches_exact": sum(1 for i in ids for k, o in d["run"][i]["outcomes"].items()
                                             if outs[ref].get(i, {}).get(k) != o),
             "outcomes": sum(len(d["run"][i]["outcomes"]) for i in ids),
             "orientation_witnesses": sum(1 for i in ids if witness(ref, i)),
             "failed_environments": sum(1 for x in loaded[ref]["done"] if x[5])}
    for n in loaded:
        if n == ref:
            continue
        o_kept = sum(1 for i in orient if witness(n, i))
        o_new = sum(1 for i in set(ids) - orient if witness(n, i))

        def flags(group):
            g = sorted(group & flagged)
            return {"published": len(g), "still_flagged": sum(1 for i in g if divs[n][i]),
                    "lost": sum(1 for i in g if not divs[n][i]),
                    "kinds_changed": sum(1 for i in g if divs[n][i] != published[i])}
        kept_by = {kind: [sum(1 for i in orient & grp if witness(n, i)), len(orient & grp)]
                   for kind, grp in (("temperature", temp), ("humidity", hum),
                                     ("neither", orient - temp - hum))}
        res[n] = {"orientation_witnesses_kept": o_kept, "orientation_witnesses_of": len(orient),
                  "orientation_witnesses_kept_by_read_kind": kept_by,
                  "new_orientation_witnesses": o_new,
                  "humidity_flagged": flags(hum), "temperature_flagged": flags(temp),
                  "affected_flag_lost": sum(1 for i in ids if published[i] and not divs[n][i]),
                  "affected_flag_gained": sum(1 for i in ids if not published[i] and divs[n][i]),
                  "affected_kinds_changed": sum(1 for i in ids if divs[n][i] != published[i]),
                  "affected_outcomes_changed": sum(1 for i in ids for k, o in d["run"][i]["outcomes"].items()
                                                   if outs[n].get(i, {}).get(k) != o),
                  "failed_environments": sum(1 for x in loaded[n]["done"] if x[5]),
                  "elapsed_s": round(loaded[n]["elapsed_s"], 1)}
        if loaded[n]["which"] == "all":
            others = sorted(set(d["run"]) - set(ids))
            res[n]["check_outside_affected"] = {
                "automations": len(others),
                "outcomes_changed": sum(1 for i in others for k, o in d["run"][i]["outcomes"].items()
                                        if outs[n].get(i, {}).get(k) != o),
                "automations_with_a_change": sum(1 for i in others if any(
                    outs[n].get(i, {}).get(k) != o for k, o in d["run"][i]["outcomes"].items())),
                "flags_changed": sum(1 for i in others if bool(r2a_div(outs[n].get(i, {})))
                                     != bool(d["run"][i]["divergences"]))}
    return {"affected_set": {"automations": len(ids), "orientation_witnessed": len(orient),
                             "temperature_read": len(temp), "humidity_read": len(hum),
                             "temperature_flagged": len(temp & flagged), "humidity_flagged": len(hum & flagged)},
            "reference_ap1_against_published": repro, "settings": res}


def r2a_div(outcomes: dict[str, dict]) -> list[str]:
    """The sweep's divergences from an automation's outcomes (static envelope kinds removed), as r2a."""
    from vesper.edt.oracle import divergences
    from vesper.edt.sweep import ENVELOPE
    from vesper.edt.types import Outcome
    outs = {k: Outcome(**o) for k, o in outcomes.items()}
    if not outs:
        return []
    kinds = divergences(outs)
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
    return sorted(kinds - static)


# ---- A7.3 clamp ------------------------------------------------------------------------------------------------------

def _series_job(job: dict) -> dict:
    """The baseline home (no automation) in one environment under the knobs: each indoor room's relative humidity
    and temperature over days 1-2 (the environment rule's every-minute values)."""
    from vesper.edt.trace import copy_home, env_site
    from vesper.env import indoor
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    from vesper.twins.home import load_catalog
    env = r2c._env(job["env"])
    site = env_site(env)
    with indoor.knobs(**job["knobs"]), tempfile.TemporaryDirectory(prefix="r4b-series-") as tmp:
        run_yaml = copy_home(Path(job["home"]), Path(tmp) / "home", site, Path(job["cache"]) / "weather", None)
        home = load_lab_home(run_yaml, load_catalog(Path(job["catalog"])))
        s = Session(home, clock=LogicalClock())
        s.start(spawn_player=False, residents=False, automations=False)
        try:
            s.clock.advance(DAYS * DAY_S)
        finally:
            s.stop()
        indoor_rooms = [r.id for r in home.map.rooms if not r.outdoor]
    lo, hi = WINDOW[0] * DAY_S, (WINDOW[1] + 1) * DAY_S
    rh: dict[str, list[int]] = {r: [] for r in indoor_rooms}
    for rec in s.records:
        if rec.get("rule") == "environment" and lo <= rec["t"] < hi:
            for r in indoor_rooms:
                if r in rec["rooms"]:
                    rh[r].append(rec["rooms"][r]["humidity"])
    return {"setting": job["setting"], "key": env.key,
            "rooms": {r: {"max": max(v), "min": min(v), "at_clamp": sum(1 for x in v if x >= CLAMP_PCT),
                          "n": len(v)} for r, v in rh.items() if v}}


def part_clamp(d, a, scratch: Path) -> None:
    jobs = []
    for name, kn in SETTINGS.items():
        jobs += [{"setting": name, "knobs": kn, "env": e, "home": d["home"], "cache": d["cache"],
                  "catalog": a.catalog} for e in d["sweep"]["envs"]]
    t0 = time.monotonic()
    done = _pool(_series_job, jobs, a.workers, "clamp")
    _dump(scratch / "clamp.pkl", {"done": done, "elapsed_s": time.monotonic() - t0})


def report_clamp(d, scratch: Path) -> dict:
    p = pickle.loads((scratch / "clamp.pkl").read_bytes())
    out = {}
    for name in SETTINGS:
        rows = [r for r in p["done"] if r["setting"] == name]
        series = [(r["key"], room, v) for r in rows for room, v in r["rooms"].items()]
        reach = {str(t): sum(1 for _, _, v in series if v["max"] >= t) for t in (CLAMP_PCT, *THRESHOLDS)}
        clamp_envs = sorted({r2c.label(r2c._env(d["envs"][k])) for k, _, v in series if v["max"] >= CLAMP_PCT})
        out[name] = {"series": len(series), "reach_pct": reach,
                     "share_of_minutes_at_clamp": r2c._r(sum(v["at_clamp"] for *_, v in series)
                                                         / max(1, sum(v["n"] for *_, v in series)), 4),
                     "environments_with_a_series_at_clamp": len(clamp_envs),
                     "cities_at_clamp": sorted(Counter(_city(x) for x in clamp_envs).items())}
    return {"setting_knobs": {n: kn for n, kn in SETTINGS.items()}, "per_setting": out,
            "elapsed_s": round(p["elapsed_s"], 1)}


# ---- A7.4 context ----------------------------------------------------------------------------------------------------

def _repo_set(d: dict, aid: str) -> list[str]:
    repo = d["run"][aid]["repo"]
    return sorted(i for i, a in d["run"].items() if a["repo"] == repo and i != aid)


def nl_targets(d: dict) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    run = d["run"]
    div = {i: sorted(k for k, o in run[i]["outcomes"].items() if o["night_light"])
           for i in run if "night_light" in run[i]["divergences"]}
    static = {i: sorted(k for k, o in run[i]["outcomes"].items() if o["night_light"])[:STATIC_NL_ENVS]
              for i in run if "night_light" in run[i]["static"]}
    return div, static


def part_context(d, a, scratch: Path) -> None:
    common = {"home": d["home"], "cache": d["cache"], "catalog": a.catalog, "knobs": {}}
    jobs = []
    for aid in d["pop"]:
        comp = [d["run"][i]["bound"] for i in _repo_set(d, aid)]
        for k in r2c_envs(d):
            e = d["envs"][k]
            jobs += [{"tag": ("setback", "alone", aid, k), "bound": d["run"][aid]["bound"], "env": e,
                      "companions": [], **common},
                     {"tag": ("setback", "ctx", aid, k), "bound": d["run"][aid]["bound"], "env": e,
                      "companions": comp, **common},
                     {"tag": ("setback", "rest", aid, k), "bound": None, "env": e, "companions": comp, **common}]
    for k in r2c_envs(d):
        jobs.append({"tag": ("setback", "base", None, k), "bound": None, "env": d["envs"][k], **common})
    div, static = nl_targets(d)
    for group, targets in (("nl_div", div), ("nl_static", static)):
        for aid, keys in sorted(targets.items()):
            comp = [d["run"][i]["bound"] for i in _repo_set(d, aid)]
            for k in keys:
                e = d["envs"][k]
                jobs += [{"tag": (group, "alone", aid, k), "bound": d["run"][aid]["bound"], "env": e,
                          "companions": [], **common},
                         {"tag": (group, "ctx", aid, k), "bound": d["run"][aid]["bound"], "env": e,
                          "companions": comp, **common}]
    # the biggest rule sets first, so the pool's tail is short
    jobs.sort(key=lambda j: -len(j.get("companions") or []))
    print(f"context: {len(jobs)} runs", flush=True)
    t0 = time.monotonic()
    done = _pool(_closed_job, jobs, a.workers, "context")
    _dump(scratch / "context.pkl", {"done": done, "elapsed_s": time.monotonic() - t0})


def sources_of(raw: dict, roles: dict) -> dict[str, list[str]] | None:
    """The author's entity names the binder put on each home entity, for one automation (None: it does not bind)."""
    from vesper.edt import bind as b
    captured: dict = {}
    original = b._rewrite

    def spy(*args, **kw):
        out = original(*args, **kw)
        captured.update({k: list(v) for k, v in (b._SOURCES or {}).items()})
        return out
    b._rewrite = spy
    try:
        r = b.bind_with_assumptions(raw, roles, 0)
    except Exception:  # noqa: BLE001 - as the sweep
        return None
    finally:
        b._rewrite = original
    return None if isinstance(r, b.Unbindable) else captured


def _night_light_ctx(lights: list, target: str, lo: float, hi: float) -> tuple[bool, list[dict]]:
    """The oracle's night_light for `target`'s lamps, a lamp counting as off when any automation of the run turned it
    off; and, per judged lamp-on, who turned it off by the deadline (labels) if anyone."""
    from vesper.edt.oracle import _next_deadline
    state: dict[str, bool] = {}
    steps: dict[str, list] = defaultdict(list)
    for t, label, svc, ent in sorted(lights):
        new = True if svc == "light.turn_on" else False if svc == "light.turn_off" else not state.get(ent, False)
        state[ent] = new
        steps[ent].append((t, new, label))
    found, detail = False, []
    for ent, ss in steps.items():
        for t, on, label in ss:
            if not on or label != target or not lo <= t < hi:
                continue
            deadline = _next_deadline(t)
            if deadline > hi:
                continue
            offs = [lb for u, s, lb in ss if not s and t < u <= deadline]
            detail.append({"entity": ent, "t": t, "off_by": sorted(set(offs))})
            if not offs:
                found = True
    return found, detail


def report_context(d, scratch: Path, catalog: str, corpus: Path | None) -> dict:
    from vesper.edt.confirm import envelope
    from vesper.edt.home import build_edt_home
    p = pickle.loads((scratch / "context.pkl").read_bytes())
    runs = {r["tag"]: r for r in p["done"]}
    lo, hi = WINDOW[0] * DAY_S, (WINDOW[1] + 1) * DAY_S
    _, roles = build_edt_home(scratch / "bind-work", catalog)
    src_cache: dict[str, dict | None] = {}

    def src(i):
        if i not in src_cache:
            src_cache[i] = sources_of(d["run"][i]["raw"], roles)
        return src_cache[i]

    def label_id(aid, label):
        k = int(label.split(":")[1])
        return aid if k == 0 else _repo_set(d, aid)[k - 1]

    def same_author(aid, other, home_entity) -> bool | None:
        a_, o_ = src(aid), src(other)
        if a_ is None or o_ is None:
            return None
        return bool(set(a_.get(home_entity, [])) & set(o_.get(home_entity, [])))

    # repositories
    involved = sorted({d["run"][i]["repo"] for i in d["pop"]} | {d["run"][i]["repo"] for g in nl_targets(d)
                                                                  for i in g})
    by_repo = defaultdict(lambda: [0, 0, 0])
    for a in d["autos"].values():
        if a["repo"] in involved:
            by_repo[a["repo"]][0] += 1
            by_repo[a["repo"]][1] += a["bound"] is not None
            by_repo[a["repo"]][2] += bool(a["outcomes"])
    corpus_check = None
    if corpus is not None:
        from vesper.edt.corpus import iter_automations
        raw_dir = corpus / "raw" if (corpus / "raw").is_dir() else corpus
        n = Counter()
        hashes = set(d["autos"])
        miss = 0
        for a in iter_automations(raw_dir):
            if a.hash in hashes and d["autos"][a.hash]["repo"] in involved:
                n[d["autos"][a.hash]["repo"]] += 1
            elif a.hash not in hashes:
                miss += 1
        corpus_check = {"repositories_matching_sweep_counts": sum(1 for r in involved if n[r] == by_repo[r][0]),
                        "repositories": len(involved), "corpus_automations_not_in_sweep": miss}

    # --- setback
    sb = []
    for aid in d["pop"]:
        for k in r2c_envs(d):
            base = runs[("setback", "base", None, k)]["extremes"]
            al, cx, rest = (runs[("setback", m, aid, k)] for m in ("alone", "ctx", "rest"))
            ev_al = envelope(al["extremes"], al["rooms"], WINDOW, baseline=base)
            ev_cx = envelope(cx["extremes"], cx["rooms"], WINDOW, baseline=base)
            ev_own = envelope(cx["extremes"], cx["rooms"], WINDOW, baseline=rest["extremes"])
            caused_al = any(v and v["below_10"] for v in ev_al.values())
            caused_cx = any(v and v["below_10"] for v in ev_cx.values())
            own_cx = any(v and v["below_10"] for v in ev_own.values())
            rest_breach = any(v and v["min_c"] < COLD_C for v in
                              envelope(rest["extremes"], rest["rooms"], WINDOW).values() if v)
            why = None
            restorers = []
            if caused_al and not caused_cx:
                for a_, b_ in al["setback"]:
                    for t, label, ent, val, acc in cx["climate"]:
                        if label != "edt:0" and acc and a_ <= t < b_ and val >= COLD_C:
                            restorers.append((label, ent))
                if restorers:
                    why = "restored_by_companion"
                elif not cx["setback"]:
                    why = "no_setback_in_context"
                else:
                    why = "other"
            same = [same_author(aid, label_id(aid, lb), ent) for lb, ent in set(restorers)]
            sb.append({"automation": d["names"][aid], "env": r2c.label(r2c._env(d["envs"][k])),
                       "breach_alone": caused_al, "breach_context": caused_cx, "own_in_context": own_cx and caused_cx,
                       "context_without_it_breaches": rest_breach, "why_lost": why,
                       "restorers": len({lb for lb, _ in restorers}),
                       "restorer_same_author_entity": sum(1 for s in same if s),
                       "restorer_other_author_entity": sum(1 for s in same if s is False),
                       "setback_h_alone": r2c._r(sum(b - a for a, b in al["setback"]) / 3600),
                       "setback_h_context": r2c._r(sum(b - a for a, b in cx["setback_all"]) / 3600),
                       "companions": len(_repo_set(d, aid))})
    lost = [r for r in sb if r["breach_alone"] and not r["breach_context"]]
    setback = {
        "environments": [r2c.label(r2c._env(d["envs"][k])) for k in r2c_envs(d)],
        "runs": len(sb), "breaching_alone": sum(r["breach_alone"] for r in sb),
        "breaching_in_context": sum(r["breach_context"] for r in sb),
        "survive": sum(r["breach_alone"] and r["breach_context"] for r in sb),
        "survive_and_own": sum(r["breach_alone"] and r["own_in_context"] for r in sb),
        "new_in_context": sum(r["breach_context"] and not r["breach_alone"] for r in sb),
        "context_without_it_breaches": sum(r["context_without_it_breaches"] for r in sb),
        "lost": len(lost), "why_lost": dict(Counter(r["why_lost"] for r in lost)),
        "lost_restorer_same_author_entity": sum(1 for r in lost if r["restorer_same_author_entity"]),
        "lost_restorer_only_other_author_entity": sum(1 for r in lost if r["restorer_other_author_entity"]
                                                      and not r["restorer_same_author_entity"]),
        "automations_breaching_alone": len({r["automation"] for r in sb if r["breach_alone"]}),
        "automations_breaching_in_context": len({r["automation"] for r in sb if r["breach_context"]}),
        "automations_own_in_context": len({r["automation"] for r in sb if r["breach_alone"] and r["own_in_context"]}),
        "setback_h_breaching_alone": r2c._span(r["setback_h_alone"] for r in sb if r["breach_alone"]),
        "setback_h_context_of_those": r2c._span(r["setback_h_context"] for r in sb if r["breach_alone"]),
        "per_run": sb}

    # --- night_light
    nl = {}
    div, static = nl_targets(d)
    for group, targets in (("nl_div", div), ("nl_static", static)):
        rows = []
        for aid, keys in sorted(targets.items()):
            for k in keys:
                al, cx = runs[(group, "alone", aid, k)], runs[(group, "ctx", aid, k)]
                nl_al, _ = _night_light_ctx(al["lights"], "edt:0", lo, hi)
                nl_cx, detail = _night_light_ctx(cx["lights"], "edt:0", lo, hi)
                why, same, other = None, 0, 0
                if nl_al and not nl_cx:
                    offs = [(x["entity"], lb) for x in detail for lb in x["off_by"] if lb != "edt:0"]
                    if not detail:
                        why = "no_lamp_on_in_context"
                    elif offs:
                        why = "turned_off_by_companion"
                        flags = [same_author(aid, label_id(aid, lb), ent) for ent, lb in set(offs)]
                        same, other = sum(1 for f in flags if f), sum(1 for f in flags if f is False)
                    else:
                        why = "other"
                rows.append({"aid": aid, "key": k, "alone": nl_al, "ctx": nl_cx, "why": why,
                             "same_author": same, "other_author": other,
                             "screen": d["run"][aid]["outcomes"][k]["night_light"]})
        lost = [r for r in rows if r["alone"] and not r["ctx"]]
        autos_alone = {r["aid"] for r in rows if r["alone"]}
        autos_ctx = {r["aid"] for r in rows if r["ctx"]}
        nl[group] = {"automations": len(targets), "runs": len(rows),
                     "closed_loop_alone_night_light": sum(r["alone"] for r in rows),
                     "context_night_light": sum(r["ctx"] for r in rows),
                     "survive": sum(r["alone"] and r["ctx"] for r in rows), "lost": len(lost),
                     "new_in_context": sum(r["ctx"] and not r["alone"] for r in rows),
                     "why_lost": dict(Counter(r["why"] for r in lost)),
                     "lost_off_by_same_author_entity": sum(1 for r in lost if r["same_author"]),
                     "lost_off_only_by_other_author_entity": sum(1 for r in lost if r["other_author"]
                                                                 and not r["same_author"]),
                     "automations_night_light_alone": len(autos_alone),
                     "automations_night_light_in_context_somewhere": len(autos_ctx),
                     "automations_lost_everywhere": len(autos_alone - autos_ctx),
                     "automations_lost_everywhere_only_by_other_author_entity": len({
                         a for a in autos_alone - autos_ctx
                         if all(r["why"] == "turned_off_by_companion" and r["other_author"] and not r["same_author"]
                                for r in lost if r["aid"] == a)}),
                     "companions_per_automation": r2c._span(len(_repo_set(d, a)) for a in targets)}
    rset = sorted(involved)
    return {"rule": "context = every other automation of the repository in the sweep that binds and runs at rung 4; "
                    "closed loop, one session; the target's outcome from its own firings, a lamp it turned on off when "
                    "any automation of the run turned it off",
            "repositories": {"involved": len(rset),
                             "automations_parsed": sum(by_repo[r][0] for r in rset),
                             "bound": sum(by_repo[r][1] for r in rset), "run": sum(by_repo[r][2] for r in rset)},
            "corpus_check": corpus_check, "setback": setback, "night_light": nl,
            "elapsed_s": round(p["elapsed_s"], 1)}


# ---- main ------------------------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("screen", "full", "openloop", "clamp", "context", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--commit", default="120f4ca")
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r4b-scratch"))
    ap.add_argument("--corpus", default=None, help="the corpus directory (read-only; optional check in `report`)")
    ap.add_argument("--settings", default=None, help="openloop: comma-separated subset of the settings")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    d = load(lab, a.commit)
    if a.part == "screen":
        part_screen(d, a, scratch)
    elif a.part == "full":
        part_full(d, a, scratch)
    elif a.part == "openloop":
        part_openloop(d, a, scratch, a.settings.split(",") if a.settings else None)
    elif a.part == "clamp":
        part_clamp(d, a, scratch)
    elif a.part == "context":
        part_context(d, a, scratch)
    else:
        r2c_pub = json.loads(Path(f"results/edt/setback-population-r2c-{a.commit}.json").read_text())
        out = {"analysis": "R4-B (protocol Addendum 7, post hoc): the setback population over the error-budget "
                           "corners, the aperture / outdoor / humidity-class propagation, isolation vs the rule set",
               "commit_of_inputs": a.commit}
        if (scratch / "full.pkl").is_file():
            out["item4_setback_corners"] = report_corners(d, scratch, r2c_pub)
        if (scratch / "openloop-ap1.pkl").is_file():
            out["item5_open_loop"] = report_openloop(d, scratch)
        if (scratch / "clamp.pkl").is_file():
            out["item5_humidity_clamp"] = report_clamp(d, scratch)
        if (scratch / "context.pkl").is_file():
            out["item6_context"] = report_context(d, scratch, a.catalog,
                                                  Path(a.corpus).expanduser() if a.corpus else None)
            for row in out["item6_context"]["setback"]["per_run"]:
                row.pop("aid", None)
        path = Path(a.out or f"results/edt/error-budget-r4b-{a.commit}.json")
        path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
