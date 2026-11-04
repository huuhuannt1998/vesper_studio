#!/usr/bin/env python3
"""r2a_thermal_humidity.py -- VESPER revision R2-a (post hoc): two sensitivity analyses the review asked for.

1. **Thermal mass and the below-10 C finding** (review MUST 3). The 7 automations whose closed-loop runs breached
   below 10 C (`confirm-rung4-C.json`'s envelope violations; A1-A7 in sorted order, as `analysis-C.json` item 9) run
   closed loop again (vesper.edt.confirm.closed_loop, unchanged) in the six January environments the confirmation ran
   them in (Minneapolis, Stockholm, Seattle, 2026-01-14, plan north 0 and 180), with every room's heat capacity scaled
   by S in {0.5, 1, 2, 4} (vesper.env.indoor.CAPACITY_SCALE), each S with its own baseline run (the home with no
   automation, same S). A room-run cell is a caused breach when the room falls below 10 C with the automation and not
   in that baseline (vesper.edt.confirm.envelope). S = 1 must reproduce the confirmation (checked, reported).
   Also: each room's time constant tau = C / H of the standard EDT home and of the validation's standard room.
2. **Humidity decomposition** (review SHOULD 10).
   (a) Validation (vesper.validation.environment, datasets A and B): the room's relative humidity from VESPER's
       modelled vapour pressure (the measured outdoor air's vapour plus EN ISO 13788 class HUMIDITY_CLASS) at the
       MEASURED room temperature, its RMSE beside the fully modelled one (modelled vapour at the modelled temperature);
       and the modelled vapour pressure against the measured indoor one.
   (b) EDT: the humidity-coupled automations that run at rung 4 (the 37 flagged and the unflagged ones) replayed open
       loop (vesper.edt.sweep's own worker) in all 48 environments against traces recorded with the room temperature
       the humidity is computed at offset by -2, 0 and +2 K (vesper.env.indoor.HUMIDITY_T_OFFSET_C; the rooms' own
       temperatures and every other reading unchanged), and how many change flag, divergence kinds or outcomes.
       Offset 0 is recorded afresh with the same code and weather, and checked against the published sweep.

Everything is post hoc. Outputs hold aggregates only (counts, temperatures, RMSEs, environment labels, anonymous
indices A1-A7): no automation, no repository or entity name, no hash, no path.

    PYTHONPATH=. nice .venv/bin/python scripts/revision/r2a_thermal_humidity.py [--part mass|humidity-edt|
        humidity-validation|all] [--lab ~/vesper-lab/edt] [--commit dc119cd] [--scratch DIR] [--workers 2]

Inputs (local, never committed): --lab/sweep-rung4-C.json, confirm-rung4-C.json, confirm-work/edt/home.yaml (the
confirmation's home), work-C/edt/home.yaml (the sweep's home), work-C/cache/weather (the recorded weather they read);
the validation datasets and ERA5 cache under .vesper/datasets (vesper validate environment's defaults).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import shutil
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

CITIES = ("Minneapolis", "Stockholm", "Seattle")
JANUARY = "2026-01-14"
SCALES = (0.5, 1.0, 2.0, 4.0)
OFFSETS = (-2.0, 0.0, 2.0)
DAYS = 3
COLD_C = 10.0
DAY_S = 86400.0
TOD_TOLERANCE_H = 1.0  # the confirmation's agreement rule for first-firing times
ISO13790_KJ_PER_M2K = {"very light": 80, "light": 110, "medium": 165, "heavy": 260, "very heavy": 370}


def label(env) -> str:
    return f"{env.city.split(',')[0]} {env.date} n{int(env.north_deg)}"


def _env(e: dict):
    from vesper.edt.types import Env
    return Env(e["city"], e["date"], e["north_deg"], e["rung"])


# ---- 1. thermal mass -------------------------------------------------------------------------------------------------

def _closed_job(job: dict) -> dict:
    """One closed-loop run (bound None: the baseline) with the heat capacity scaled; its rooms' temperatures."""
    from vesper.edt.confirm import closed_loop
    from vesper.env import indoor
    with indoor.knobs(CAPACITY_SCALE=job["scale"]):
        r = closed_loop(job["bound"], _env(job["env"]), Path(job["home"]), Path(job["cache"]), days=DAYS,
                        catalog_dir=job["catalog"])
    return {"tag": job["tag"], "rooms": r["rooms"], "temperatures": r["temperatures"]}


def time_constants(home_yaml: Path, catalog: str) -> dict:
    """tau = C / H per room of the EDT home (H: to the outdoors; with the interior walls beside it) and of the
    validation's standard room, hours, at CAPACITY_SCALE 1."""
    from vesper.env.indoor import C_PER_M2, U_INTERIOR, RoomModel, room_models
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    from vesper.twins.home import load_catalog
    from vesper.validation.environment import STANDARD_ROOM
    home = load_lab_home(home_yaml, load_catalog(Path(catalog)))
    models = room_models(home.map, load_windows(home_yaml.parent))
    rooms = {}
    for rid, m in sorted(models.items()):
        c = C_PER_M2 * m.floor_m2
        h_int = sum(U_INTERIOR * a for a in m.neighbours.values())
        rooms[rid] = {"label": home.map.room(rid).label, "floor_m2": round(m.floor_m2, 2),
                      "C_MJ_per_K": round(c / 1e6, 3), "H_out_W_per_K": round(m.h_w_per_k, 2),
                      "H_interior_W_per_K": round(h_int, 2), "tau_h": round(c / m.h_w_per_k / 3600, 1)}
    total_c = sum(C_PER_M2 * m.floor_m2 for m in models.values())
    total_h = sum(m.h_w_per_k for m in models.values())
    s = STANDARD_ROOM
    std = RoomModel("r", s["w"] * s["d"], 2 * (s["w"] + s["d"]), s["outside_wall_m"] * 2.5 - s["window"][0] *
                    s["window"][1], [{"width": s["window"][0], "height": s["window"][1]}])
    c_std = C_PER_M2 * std.floor_m2
    return {"definition": "tau = C / H, C = C_PER_M2 x floor area (EN ISO 13790 'medium', 165 kJ/(m2 K)), "
                          "H = U A of the outside walls and windows + 0.34 ACH V (to the outdoors only)",
            "edt_home_rooms": rooms,
            "edt_home_whole": {"C_MJ_per_K": round(total_c / 1e6, 3), "H_out_W_per_K": round(total_h, 2),
                               "tau_h": round(total_c / total_h / 3600, 1)},
            "validation_standard_room": {"floor_m2": std.floor_m2, "C_MJ_per_K": round(c_std / 1e6, 3),
                                         "H_out_W_per_K": round(std.h_w_per_k, 2),
                                         "tau_h": round(c_std / std.h_w_per_k / 3600, 1)},
            "iso13790_classes_kJ_per_m2K": ISO13790_KJ_PER_M2K,
            "scales_as_iso13790_classes": {str(s): round(s * 165, 1) for s in SCALES}}


def thermal_mass(lab: Path, commit: str, catalog: str, workers: int) -> dict:
    from vesper.edt.confirm import envelope
    from vesper.lab.home import load_lab_home
    from vesper.twins.home import load_catalog
    sweep = json.loads((lab / f"sweep-rung4-{commit}.json").read_text())
    confirm = json.loads((lab / f"confirm-rung4-{commit}.json").read_text())
    home = lab / "confirm-work" / "edt" / "home.yaml"
    cache = lab / f"work-{commit}" / "cache"
    ids = sorted({v["id"] for v in confirm["envelope"]["violations"] if v.get("below_10")})
    name = {aid: f"A{i + 1}" for i, aid in enumerate(ids)}
    envs = {e["key"]: e for e in sweep["envs"]
            if e["date"] == JANUARY and e["city"].split(",")[0] in CITIES}
    ran = sorted({(r["id"], r["env_key"]) for r in confirm["results"] if r["id"] in name and r["env_key"] in envs})
    labels = {r.id: r.label for r in load_lab_home(home, load_catalog(Path(catalog))).map.rooms}
    jobs = []
    for s in SCALES:
        for key in envs:
            jobs.append({"tag": (s, None, key), "scale": s, "bound": None, "env": envs[key], "home": str(home),
                         "cache": str(cache), "catalog": catalog})
        for aid, key in ran:
            jobs.append({"tag": (s, aid, key), "scale": s, "bound": sweep["automations"][aid]["bound"],
                         "env": envs[key], "home": str(home), "cache": str(cache), "catalog": catalog})
    t0 = time.monotonic()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
            done = list(pool.map(_closed_job, jobs))
    else:
        done = [_closed_job(j) for j in jobs]
    elapsed = time.monotonic() - t0
    base = {(r["tag"][0], r["tag"][2]): r["temperatures"] for r in done if r["tag"][1] is None}
    # the confirmation's own envelope rows, to check S = 1 reproduces them
    published = {(r["id"], r["env_key"]): r["envelope"] for r in confirm["results"] if (r["id"], r["env_key"]) in ran}
    per_scale, repro = {}, {"cells": 0, "max_abs_min_c_diff": 0.0, "max_abs_baseline_min_c_diff": 0.0,
                            "caused_below_10_mismatches": 0}
    for s in SCALES:
        rows = []
        for r in done:
            sc, aid, key = r["tag"]
            if sc != s or aid is None:
                continue
            env = _env(envs[key])
            ev = envelope(r["temperatures"], r["rooms"], (1, DAYS - 1), baseline=base[(s, key)])
            for room, v in ev.items():
                if v is None:
                    continue
                rows.append({"automation": name[aid], "city": env.city.split(",")[0], "env": label(env),
                             "room": labels.get(room, room), **{k: v[k] for k in (
                                 "min_c", "baseline_min_c", "below_10", "breached_below_10", "delta_min_c")}})
                if s == 1.0:
                    p = published[(aid, key)].get(room)
                    if p is not None:
                        repro["cells"] += 1
                        repro["max_abs_min_c_diff"] = max(repro["max_abs_min_c_diff"], abs(p["min_c"] - v["min_c"]))
                        repro["max_abs_baseline_min_c_diff"] = max(repro["max_abs_baseline_min_c_diff"],
                                                                   abs(p["baseline_min_c"] - v["baseline_min_c"]))
                        repro["caused_below_10_mismatches"] += int(p["below_10"] != v["below_10"])
        per_scale[str(s)] = _mass_summary(rows, len(ran))
    repro["max_abs_min_c_diff"] = round(repro["max_abs_min_c_diff"], 3)
    repro["max_abs_baseline_min_c_diff"] = round(repro["max_abs_baseline_min_c_diff"], 3)
    return {"analysis": "R2-a.1 thermal-mass sensitivity of the below-10 C finding (post hoc)",
            "commit_of_inputs": commit, "scales": list(SCALES), "environments": sorted(label(_env(e))
                                                                                        for e in envs.values()),
            "automations": len(ids), "runs_per_scale": len(ran), "baselines_per_scale": len(envs),
            "rule": "a cell (room x run) is a caused breach when the room's minimum over days 1-2 is below 10 C with "
                    "the automation and not in the same environment's baseline at the same scale",
            "per_scale": per_scale, "reproduces_confirmation_at_scale_1": repro,
            "time_constants": time_constants(home, catalog), "elapsed_s": round(elapsed, 1)}


def _mass_summary(rows: list[dict], n_runs: int) -> dict:
    caused = [r for r in rows if r["below_10"]]
    runs_caused = {(r["automation"], r["env"]) for r in caused}
    by_city = {}
    for c in CITIES:
        rc = [r for r in rows if r["city"] == c]
        cc = [r for r in rc if r["below_10"]]
        runs_c = {(r["automation"], r["env"]) for r in rc}
        coldest = {}
        for r in rc:
            k = (r["automation"], r["env"])
            coldest[k] = min(coldest.get(k, math.inf), r["min_c"])
        by_city[c] = {"runs": len(runs_c), "runs_with_caused_breach": len({(r["automation"], r["env"]) for r in cc}),
                      "caused_cells": len(cc),
                      "coldest_room_per_run_c": [min(coldest.values()), max(coldest.values())] if coldest else None,
                      "baseline_coldest_room_c": min((r["baseline_min_c"] for r in rc), default=None),
                      "baseline_cells_below_10": sum(1 for r in rc if r["baseline_min_c"] < COLD_C)}
    coldest_caused = sorted(min(r["min_c"] for r in caused if (r["automation"], r["env"]) == k) for k in runs_caused)
    return {"runs": n_runs, "runs_with_caused_breach": len(runs_caused), "caused_cells": len(caused),
            "cells": len(rows), "breached_cells_any_cause": sum(1 for r in rows if r["breached_below_10"]),
            "automations_with_caused_breach": len({r["automation"] for r in caused}),
            "coldest_room_in_caused_runs_c": ([coldest_caused[0], statistics.median(coldest_caused),
                                               coldest_caused[-1]] if coldest_caused else None),
            "largest_drop_below_baseline_k": round(-min((r["delta_min_c"] for r in caused), default=0.0), 2),
            "by_city": by_city,
            "by_automation": {a: sum(1 for r in caused if r["automation"] == a) for a in
                              sorted({r["automation"] for r in rows}, key=lambda s: int(s[1:]))}}


# ---- 2b. humidity offset, EDT open loop ------------------------------------------------------------------------------

def _trace_job(job: dict):
    """Record one environment's trace with the humidity's temperature offset and replay the automations against it
    (vesper.edt.sweep's own worker)."""
    from vesper.edt import sweep as sw
    from vesper.env import indoor
    sw._init(job["home"], job["cache"], job["bound"], job["catalog"], sw.BATCH)
    with indoor.knobs(HUMIDITY_T_OFFSET_C=job["offset"]):
        key, outcomes, refused, _approx, secs, failure = sw._run_env(_env(job["env"]))
    return job["offset"], key, outcomes, refused, secs, failure


def _divergences(outcomes: dict[str, dict]) -> list[str]:
    from vesper.edt.oracle import divergences
    from vesper.edt.sweep import ENVELOPE
    from vesper.edt.types import Outcome
    outs = {k: Outcome(**o) for k, o in outcomes.items()}
    if not outs:
        return []
    kinds = divergences(outs)
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
    return sorted(kinds - static)


def _same(a: dict, b: dict, tol_h: float) -> bool:
    if any(a[f] != b[f] for f in ("fired", "flaps", "night_light", "night_cover")):
        return False
    x, y = a["first_tod_h"], b["first_tod_h"]
    if (x is None) != (y is None):
        return False
    if x is None:
        return True
    d = abs(x - y) % 24.0
    return min(d, 24.0 - d) <= tol_h


def humidity_edt(lab: Path, commit: str, catalog: str, workers: int, scratch: Path) -> dict:
    sweep = json.loads((lab / f"sweep-rung4-{commit}.json").read_text())
    home = lab / f"work-{commit}" / "edt" / "home.yaml"
    weather = lab / f"work-{commit}" / "cache" / "weather"
    run = {aid: a for aid, a in sweep["automations"].items() if a["outcomes"] and "humidity" in a["kinds"]}
    flagged = sorted(aid for aid, a in run.items() if a["divergences"])
    bound = {aid: a["bound"] for aid, a in run.items()}
    jobs = []
    for off in OFFSETS:
        cache = scratch / f"cache-humidity-offset{off:+.0f}"
        if not (cache / "weather").is_dir():
            shutil.copytree(weather, cache / "weather")
        jobs += [{"offset": off, "env": e, "home": str(home), "cache": str(cache), "bound": bound,
                  "catalog": catalog} for e in sweep["envs"]]
    t0 = time.monotonic()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
            done = list(pool.map(_trace_job, jobs))
    else:
        done = [_trace_job(j) for j in jobs]
    elapsed = time.monotonic() - t0
    failures = [(o, k, f) for o, k, _, _, _, f in done if f]
    outs = {off: {aid: {} for aid in run} for off in OFFSETS}
    refused = Counter()
    for off, key, outcomes, ref, _, _ in done:
        for aid, o in outcomes.items():
            outs[off][aid][key] = o
        refused[off] += len(ref)
    divs = {off: {aid: _divergences(o) for aid, o in outs[off].items()} for off in OFFSETS}
    published = {aid: a["divergences"] for aid, a in run.items()}
    repro = {"flag_mismatches": sum(1 for aid in run if bool(divs[0.0][aid]) != bool(published[aid])),
             "kind_mismatches": sum(1 for aid in run if divs[0.0][aid] != published[aid]),
             "outcome_mismatches_exact": sum(1 for aid in run for k, o in run[aid]["outcomes"].items()
                                             if outs[0.0][aid].get(k) != o),
             "outcomes": sum(len(a["outcomes"]) for a in run.values())}

    def compare(off: float, ids: list[str]) -> dict:
        ref = 0.0
        changed_flag = [aid for aid in ids if bool(divs[off][aid]) != bool(divs[ref][aid])]
        changed_kinds = [aid for aid in ids if divs[off][aid] != divs[ref][aid]]
        pairs = [(aid, k) for aid in ids for k in outs[ref][aid]]
        exact = sum(1 for aid, k in pairs if outs[off][aid].get(k) != outs[ref][aid][k])
        loose = sum(1 for aid, k in pairs if outs[off][aid].get(k) is None
                    or not _same(outs[off][aid][k], outs[ref][aid][k], TOD_TOLERANCE_H))
        autos_any = sum(1 for aid in ids if any(outs[off][aid].get(k) != outs[ref][aid][k] for k in outs[ref][aid]))
        return {"automations": len(ids), "flag_changed": len(changed_flag),
                "flag_lost": sum(1 for a in changed_flag if divs[ref][a]),
                "flag_gained": sum(1 for a in changed_flag if not divs[ref][a]),
                "divergence_kinds_changed": len(changed_kinds),
                "automations_with_any_outcome_change": autos_any,
                "automation_env_outcomes": len(pairs), "outcomes_changed_exact": exact,
                "outcomes_changed_beyond_confirmation_tolerance": loose,
                "kinds_after": dict(Counter(k for a in ids for k in divs[off][a]))}

    unflagged = sorted(aid for aid in run if not run[aid]["divergences"])
    result = {"analysis": "R2-a.2b humidity flag stability under a +-2 K error in the temperature the humidity is "
                          "computed at (post hoc)", "commit_of_inputs": commit, "offsets_k": list(OFFSETS),
              "environments": len(sweep["envs"]),
              "humidity_coupled_run": len(run), "humidity_coupled_flagged_published": len(flagged),
              "reference": "offset 0, recorded afresh with the same code and weather",
              "offset0_against_published_sweep": repro, "failed_environments": len(failures),
              "refused_replays": dict((str(k), v) for k, v in refused.items()),
              "flagged": {str(off): compare(off, flagged) for off in OFFSETS if off != 0.0},
              "unflagged": {str(off): compare(off, unflagged) for off in OFFSETS if off != 0.0},
              "elapsed_s": round(elapsed, 1)}
    either = [aid for aid in flagged if any(bool(divs[o][aid]) != bool(divs[0.0][aid]) for o in OFFSETS)]
    kinds_either = [aid for aid in flagged if any(divs[o][aid] != divs[0.0][aid] for o in OFFSETS)]
    result["flagged_either_offset"] = {"flag_changed": len(either), "divergence_kinds_changed": len(kinds_either)}
    return result


# ---- 2a. humidity decomposition, validation --------------------------------------------------------------------------

def _clamped_rh(e_kpa, t_c):
    from vesper.env.indoor import e_sat
    if e_kpa is None or t_c is None:
        return None
    return min(85.0, max(15.0, 100 * e_kpa / e_sat(t_c)))


def humidity_validation(appliances: str, sml2010: str, era5_cache: str) -> dict:
    from vesper.env import indoor
    from vesper.env.site import parse_site
    from vesper.validation import environment as ve
    datasets = {}
    specs = {"A": (ve.load_appliances, appliances, "Chièvres, Belgium", "T_out", "RH_out", ve.APPLIANCES_ROOMS,
                   {c: "RH_" + c[1:] for c in ve.APPLIANCES_ROOMS}),
             "B": (ve.load_sml2010, sml2010, "Valencia, Spain", "t_out", "rh_out", ve.SML_ROOMS,
                   {"t_dining": "rh_dining", "t_room": "rh_room"})}
    for name, (load, folder, city, t_out, rh_out, rooms, rh_of) in specs.items():
        df = load(folder)
        site = parse_site({"city": city, "date": df.index[0].date().isoformat()})
        era = ve._era5(site, df.index, Path(era5_cache))
        out_t, out_rh = ve._values(df[t_out]), ve._values(df[rh_out])
        start = df.index[0].to_pydatetime()
        shared = ve._MeasuredOutdoor(site, start, out_t, out_rh, era["shortwave_radiation"], era["cloud_cover"])
        # the room's vapour pressure as VESPER models it (no people in the validation): the outdoor air's, measured,
        # plus EN ISO 13788's excess for HUMIDITY_CLASS at that hour's outdoor temperature (indoor.step's e_in)
        e_in = [None if t is None or h is None else h / 100 * indoor.e_sat(t) + indoor.moisture_excess_kpa(t)
                for t, h in zip(out_t, out_rh, strict=True)]
        rows = []
        for col, room in rooms.items():
            measured_t, measured_rh = ve._values(df[col]), ve._values(df[rh_of[col]])
            setpoint = ve._percentile(measured_t, ve.SETPOINT_PERCENTILE)
            sim_t, sim_h = ve.simulate_room(site, start, out_t, out_rh, era["shortwave_radiation"],
                                            era["cloud_cover"], setpoint, outdoor=shared)
            at_measured = [_clamped_rh(e, t) for e, t in zip(e_in, measured_t, strict=True)]
            at_modelled = [_clamped_rh(e, t) for e, t in zip(e_in, sim_t, strict=True)]
            e_meas = [None if t is None or h is None else h / 100 * indoor.e_sat(t)
                      for t, h in zip(measured_t, measured_rh, strict=True)]
            vap = ve._agreement([None if e is None else 10 * e for e in e_in],
                                [None if e is None else 10 * e for e in e_meas])  # hPa
            rows.append({"room": room,
                         "modelled": ve._agreement(sim_h, measured_rh),
                         "modelled_vapour_at_modelled_t_hourly": ve._agreement(at_modelled, measured_rh),
                         "modelled_vapour_at_measured_t": ve._agreement(at_measured, measured_rh),
                         "temperature": ve._agreement(sim_t, measured_t),
                         "vapour_pressure_hpa": vap})

        def med(key):
            v = sorted(r[key]["rmse"] for r in rows)
            return v[len(v) // 2]

        def med_bias(key):
            v = sorted(r[key]["bias"] for r in rows)
            return v[len(v) // 2]
        datasets[name] = {"rooms": rows, "median_rmse_pp": {
            "modelled (validation H)": med("modelled"),
            "modelled vapour at modelled T, hourly (check)": med("modelled_vapour_at_modelled_t_hourly"),
            "modelled vapour at measured T": med("modelled_vapour_at_measured_t")},
            "median_bias_pp": {"modelled": med_bias("modelled"),
                               "modelled vapour at measured T": med_bias("modelled_vapour_at_measured_t")},
            "median_vapour_pressure_hpa": {"rmse": med("vapour_pressure_hpa"), "bias": med_bias("vapour_pressure_hpa")},
            "median_temperature_rmse_c": med("temperature")}
    return {"analysis": "R2-a.2a humidity decomposition: modelled vapour at the measured room temperature (post hoc)",
            "humidity_class": indoor.HUMIDITY_CLASS, "class_pa_at_or_below_0c": indoor.CLASS_PA,
            "moisture_excess_kpa": {"t_out_0c_or_below": indoor.moisture_excess_kpa(0.0),
                                    "t_out_10c": indoor.moisture_excess_kpa(10.0),
                                    "t_out_20c_or_above": indoor.moisture_excess_kpa(20.0)},
            "person_vapour_kpa": indoor.PERSON_VAPOUR_KPA,
            "notes": ["RH clamped to 15-85 % as indoor.step does; the validation's modelled RH is the hourly mean of "
                      "10-min integer readings, the decomposition's is per hour unrounded (the 'check' row shows the "
                      "difference this makes)", "bias: model minus measured"],
            "datasets": datasets}


# ---- main ------------------------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", default="all", choices=("mass", "humidity-edt", "humidity-validation", "all"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--commit", default="dc119cd")
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r2a-scratch"))
    ap.add_argument("--appliances", default=".vesper/datasets/uci-appliances")
    ap.add_argument("--sml2010", default=".vesper/datasets/uci-sml2010")
    ap.add_argument("--weather-cache", default=".vesper/datasets/era5")
    ap.add_argument("--date", default=dt.date.today().strftime("%Y%m%d"))
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)

    def write(path: str, data: dict) -> None:
        Path(path).write_text(json.dumps(data, indent=1, sort_keys=True, default=str) + "\n")
        print("wrote", path)
    if a.part in ("mass", "all"):
        write(f"results/edt/thermal-mass-r2a-{a.commit}-{a.date}.json", thermal_mass(lab, a.commit, a.catalog,
                                                                                     a.workers))
    if a.part in ("humidity-edt", "all"):
        write(f"results/edt/humidity-offset-r2a-{a.commit}-{a.date}.json",
              humidity_edt(lab, a.commit, a.catalog, a.workers, scratch))
    if a.part in ("humidity-validation", "all"):
        write(f"results/environment/humidity-decomposition-r2a-{a.date}.json",
              humidity_validation(a.appliances, a.sml2010, a.weather_cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
