#!/usr/bin/env python3
"""r11p2.py -- VESPER revision R11-P2 (post hoc, protocol Addendum 23): bounded re-runs after the eighth internal
review (items 6, 7 and 10(c)).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `ref`       A23.0: R7-B's replay set (954 run automations of 8fcd672) open loop in all 48 environments on traces
              recorded afresh with every knob at its default: the reference of A23.1 and A23.2.
- `moisture`  A23.1: the same at MOISTURE_EXCESS_SCALE x0 and x0.5 (EN ISO 13788's class excess).
- `draws`     A23.2: the hourly residual series ERA5 minus station (Belgium: dataset A's outdoor temperature; Valencia:
              NOAA ISD Manises, R6-B's record), from cached inputs only (no fetch); their checks against W and A9.8;
              their local day blocks; the 8 per-city draws (day-block bootstrap), written to --scratch/draws.json.
- `outdoor`   A23.2: the replay set at each draw's OUTDOOR_T_SERIES.
- `context`   A23.3: R2-c's 22 setback automations (120f4ca) in the core four, closed loop, hallway thermostat, alone /
              with their repository's other run automations / the context without them, plus a baseline per
              environment, at R4-B's corners G0-Foff-W1-C165 (the check) and G2-Foff-W1-C165.
- `report`    results/edt/r11p2-<commit>.json: aggregates only (counts, environment labels, anonymous indices A1..,
              R1..): no automation, repository or entity name, no hash, no path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r11p2.py --part P [--lab ~/vesper-lab/edt]
        [--catalog catalog/vds] [--workers 4] [--scratch DIR] [--settings a,b,...]

Inputs (local, never committed): --lab/sweep-rung4-{8fcd672,r6d-5b54586,120f4ca}.json, sweep-rung2.5-8fcd672.json,
work-8fcd672 and work-120f4ca (their homes and cached weather); the validation datasets A and B and their ERA5 cache;
R6-B's station record.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pickle
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import r2c_setback_population as r2c  # noqa: E402  (its labels and extremes, unchanged)
import r4b_error_budget as r4b  # noqa: E402  (its corners, pool, loader and dump, unchanged)
import r7b_room_terms as r7b  # noqa: E402  (its replay set, part_run, loaders and memberships, unchanged)

COMMIT = "8fcd672"
BASE = "r6d-5b54586"  # A13.6's convention: the published memberships and the reference check
SETBACK_COMMIT = "120f4ca"
SEED = 20261105
DRAWS = 8
SOURCES = ("Belgium", "Valencia")
HOURS = 73  # RUN_DAYS x 24 + 1 hourly values: the run's whole span, interpolated between hours
COLD_C = 10.0
WINDOW_S = (86400.0, 3 * 86400.0)  # days 1-2, R4-B's judged window
OFF = ("target_turns_off", "companion_turns_off", "setpoint_only")
CHECKS = {"Belgium": {"bias": -0.08, "rmse": 0.97}, "Valencia": {"bias": 0.70, "rmse": 1.47}}
FAMILIES = ("frame_flags", "frame_temperature_flags", "frame_humidity_flags", "era5_only_flags",
            "frame_city_witnesses", "frame_orientation_witnesses", "humidity_orientation_witnesses", "clock_flags",
            "clock_humidity_flags")
ITEM6 = ("frame_humidity_flags", "clock_humidity_flags", "humidity_orientation_witnesses", "frame_flags",
         "clock_flags")
ITEM7 = ("frame_temperature_flags", "frame_city_witnesses", "era5_only_flags", "frame_flags", "frame_humidity_flags",
         "frame_orientation_witnesses", "clock_flags")


# ---- settings --------------------------------------------------------------------------------------------------------

def moisture_settings() -> dict[str, dict]:
    return {"mx0": {"MOISTURE_EXCESS_SCALE": 0.0}, "mx0.5": {"MOISTURE_EXCESS_SCALE": 0.5}}


def outdoor_settings(draws: dict) -> dict[str, dict]:
    return {f"od{d}": {"OUTDOOR_T_SERIES": draws[d]["series"]} for d in sorted(draws, key=int)}


def context_corners() -> dict[str, dict]:
    return {r4b.corner_label(c): r4b.corner_knobs(c) for c in ((0.0, 0, 1, 165), (2.0, 0, 1, 165))}


# ---- residuals, day blocks, draws ------------------------------------------------------------------------------------

def day_blocks(residuals: dict[dt.datetime, float], tz: str) -> list[dict]:
    """The local calendar days (in `tz`) with all 24 local hours present exactly once, each as its 24 values in local
    hour order. A day with a clock change has 23 or 25 local hours and is never a block."""
    from zoneinfo import ZoneInfo
    z = ZoneInfo(tz)
    days: dict = defaultdict(list)
    for t, v in residuals.items():
        lt = t.astimezone(z)
        days[lt.date()].append((lt.hour, t, v))
    out = []
    for day in sorted(days):
        rows = sorted(days[day], key=lambda x: x[1])
        start = dt.datetime.combine(day, dt.time(0), tzinfo=z)
        length = (dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=z) - start).total_seconds()
        if length != 86400 or [h for h, _t, _v in rows] != list(range(24)):
            continue
        out.append({"date": day.isoformat(), "values": [v for _h, _t, v in rows]})
    return out


def lag1(residuals: dict[dt.datetime, float]) -> float | None:
    """The lag-1 autocorrelation over pairs of consecutive hours both present (None with fewer than 3 pairs)."""
    pairs = [(v, residuals[t + dt.timedelta(hours=1)]) for t, v in residuals.items()
             if t + dt.timedelta(hours=1) in residuals]
    if len(pairs) < 3:
        return None
    xs, ys = [a for a, _ in pairs], [b for _, b in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)


def draw_series(blocks: dict[str, list[list[float]]], cities: list[str], draw: int) -> dict:
    """Draw `draw` (A23.2): for each city in sorted order a source site (each with probability 1/2), then RUN_DAYS + 1
    of its day blocks with replacement, concatenated and negated; the first HOURS values."""
    import numpy as np
    rng = np.random.default_rng([SEED, draw])
    sites = sorted(blocks)
    series, source = {}, {}
    for city in sorted(cities):
        site = sites[int(rng.integers(len(sites)))]
        pick = rng.integers(0, len(blocks[site]), size=math.ceil(HOURS / 24))
        values = [-float(v) for k in pick for v in blocks[site][int(k)]]
        series[city], source[city] = values[:HOURS], site
    return {"series": series, "source": source}


def series_summary(series: dict[str, list[float]]) -> dict:
    """The pooled mean and root mean square of the drawn values, over every city."""
    v = [x for s in series.values() for x in s]
    return {"mean_c": statistics.fmean(v), "rmse_c": math.sqrt(statistics.fmean(x * x for x in v))}


def _era5_no_fetch(site, index, cache) -> list[float | None]:
    """ERA5's hourly air temperature at the site on `index`, from the cache only (vesper.validation.environment's
    reading without its fetch)."""
    from vesper.env import weather
    rec = weather.load(site.lat, site.lon, weather.utc_days(index[0].to_pydatetime(), index[-1].to_pydatetime()),
                       Path(cache))
    rows = [rec.at(t.to_pydatetime()) if rec is not None and rec.covers(t.to_pydatetime()) else {} for t in index]
    return [r.get("temperature_2m") for r in rows]


def residual_series(a) -> dict:
    """{site: (tz, {utc hour: ERA5 - measured})}, each with its agreement against the W check / A9.8."""
    import r6b_physics as r6b

    from vesper.env.site import parse_site
    from vesper.validation import environment as ev
    out = {}
    df = ev.load_appliances(a.appliances)
    site = parse_site({"city": "Chièvres, Belgium", "date": df.index[0].date().isoformat()})
    era, meas = _era5_no_fetch(site, df.index, a.weather_cache), ev._values(df["T_out"])
    keep = [i for i in range(len(df)) if era[i] is not None and meas[i] is not None and era[i] == era[i]]
    out["Belgium"] = (site.tz, {df.index[i].to_pydatetime(): era[i] - meas[i] for i in keep},
                      ev._agreement([era[i] for i in keep], [meas[i] for i in keep]))
    df = ev.load_sml2010(a.sml2010)
    site = parse_site({"city": "Valencia, Spain", "date": df.index[0].date().isoformat()})
    era, house = _era5_no_fetch(site, df.index, a.weather_cache), ev._values(df["t_out"])
    st = r6b.station_hourly(Path(a.station), df.index)
    keep = [i for i in range(len(df)) if era[i] is not None and house[i] is not None and st[i] is not None]
    out["Valencia"] = (site.tz, {df.index[i].to_pydatetime(): era[i] - st[i] for i in keep},
                       ev._agreement([era[i] for i in keep], [st[i] for i in keep]))
    return out


def part_draws(a, scratch: Path, cities: list[str]) -> dict:
    path = scratch / "draws.json"
    if path.is_file():
        print("have", path.name, flush=True)
        return json.loads(path.read_text())
    res = residual_series(a)
    meta, blocks = {}, {}
    for site, (tz, r, agree) in res.items():
        b = day_blocks(r, tz)
        blocks[site] = [x["values"] for x in b]
        check = CHECKS[site]
        meta[site] = {"hours": len(r), "agreement": agree, "tz": tz, "day_blocks": len(b),
                      "lag1_autocorrelation": lag1(r),
                      "check_reproduces": round(agree["bias"], 2) == check["bias"]
                      and round(agree["rmse"], 2) == check["rmse"], "check_expected": check}
    draws = {}
    for d in range(1, DRAWS + 1):
        x = draw_series(blocks, cities, d)
        x["summary"] = series_summary(x["series"])
        x["sources_used"] = dict(Counter(x["source"].values()))
        draws[str(d)] = x
    out = {"residuals": meta, "draws": draws, "seed": SEED, "hours": HOURS}
    path.write_text(json.dumps(out, indent=1))
    return out


# ---- witnesses, memberships, families --------------------------------------------------------------------------------

def frame_city_witness(outs: dict, first_t: dict, frames) -> bool:
    """Under the frame relation: two environments of one date and bearing in two cities over which the
    automation's frame-restricted divergences are not empty (the city axis, computed as r7b's orientation one)."""
    from vesper.edt.oracle import frame_restricted_divergences
    by_slot: dict[tuple, list] = defaultdict(list)
    for e in outs:
        by_slot[(e.date, e.north_deg, e.rung)].append(e)
    for group in by_slot.values():
        for i, x in enumerate(group):
            for y in group[i + 1:]:
                if x.city != y.city and frame_restricted_divergences(outs, first_t, frames, [x, y]):
                    return True
    return False


def memberships(bound: dict, outcomes: dict, first_t: dict, by_key: dict, envs) -> dict:
    from vesper.edt.oracle import frames_of
    from vesper.edt.types import Outcome
    m = r7b.memberships(bound, outcomes, first_t, by_key, envs)
    outs = {by_key[k]: Outcome(**o) for k, o in outcomes.items()}
    ft = {by_key[k]: first_t.get(k) for k in outcomes}
    m["fc"] = bool(m["frame"]) and frame_city_witness(outs, ft, frames_of(bound))
    return m


def family_sets(rec: dict[str, dict], groups: dict[str, set]) -> dict[str, set]:
    frame = {i for i, m in rec.items() if m["frame"]}
    clock = {i for i, m in rec.items() if m["clock"]}
    fo = {i for i, m in rec.items() if m["fo"]}
    return {"frame_flags": frame, "frame_temperature_flags": frame & groups["temperature"],
            "frame_humidity_flags": frame & groups["humidity"], "era5_only_flags": frame & groups["era5_only"],
            "frame_city_witnesses": {i for i, m in rec.items() if m["fc"]}, "frame_orientation_witnesses": fo,
            "humidity_orientation_witnesses": fo & groups["humidity"], "clock_flags": clock,
            "clock_humidity_flags": clock & groups["humidity"]}


def bracket(members: dict[str, set], published: set, names: list[str]) -> dict:
    """Over the named settings: the robust core, the possible set, lost and gained against the published, the
    per-setting range, and how many published members hold at k of the settings."""
    sets = [members[n] for n in names]
    core, possible = set.intersection(*sets), set.union(*sets)
    held = Counter(sum(i in s for s in sets) for i in published)
    return {"published": len(published), "robust_core": len(core), "possible_set": len(possible),
            "lost_somewhere": len(published - core), "gained_somewhere": len(possible - published),
            "per_setting_min": min(len(s) for s in sets), "per_setting_max": max(len(s) for s in sets),
            "settings": len(names), "survival_histogram": {str(k): held.get(k, 0) for k in range(len(names) + 1)}}


# ---- the open-loop runs ----------------------------------------------------------------------------------------------

def run_open(d, a, scratch: Path, table: dict[str, dict], names: list[str], ids: list[str]) -> None:
    bound = scratch / "bound-replay.json"
    if not bound.is_file():
        bound.write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    r7b.part_run(d, a, scratch, table, names, bound, d["home"], "p2")


def _load(scratch: Path, name: str):
    return r7b._load_setting(scratch / f"ol-p2-{name}.pkl")


def report_open(lab: Path, scratch: Path) -> dict | None:
    if not (scratch / "ol-p2-ref.pkl").is_file():
        return None
    from vesper.edt.cover import _envs
    d8 = r4b.load(lab, COMMIT)
    s25 = json.loads((lab / f"sweep-rung2.5-{COMMIT}.json").read_text())
    groups = r7b.groups_of(d8, s25)
    ids = r7b.replay_set(d8)
    base = r4b.load(lab, BASE)
    envs = _envs(d8["sweep"])
    by_key = {e.key: e for e in envs}
    run8 = d8["run"]

    def recs_of(d, outs, firsts):
        return {i: memberships(d["run"][i]["bound"], outs.get(i, {}), firsts.get(i, {}), by_key, envs) for i in ids}

    pub_rec = {i: memberships(base["run"][i]["bound"], base["run"][i]["outcomes"],
                              {k: (r or {}).get("first_t") for k, r in (base["run"][i].get("rich") or {}).items()},
                              by_key, envs) for i in ids}
    published = family_sets(pub_rec, groups)
    ref_outs, ref_firsts, ref_meta = _load(scratch, "ref")
    ref_rec = recs_of(base, ref_outs, ref_firsts)
    pub8 = {i: memberships(run8[i]["bound"], run8[i]["outcomes"],
                           {k: (r or {}).get("first_t") for k, r in (run8[i].get("rich") or {}).items()},
                           by_key, envs) for i in ids}
    out = {"replay_set": {"automations": len(ids), **{f"reads_{k}": len(groups[k] & set(ids))
                                                      for k in ("temperature", "humidity", "illuminance")},
                          "era5_only_frame_flags": len(groups["era5_only"])},
           "published": {f: len(s) for f, s in published.items()},
           "published_8fcd672": {f: len(s) for f, s in family_sets(pub8, groups).items()},
           "reference": {"against_5b54586": r7b._check_reference(base, ids, ref_outs, pub_rec, ref_rec),
                         "against_8fcd672": r7b._check_reference(d8, ids, ref_outs, pub8,
                                                                 recs_of(d8, ref_outs, ref_firsts)),
                         **ref_meta}}
    th = groups["temperature"] | groups["humidity"]

    def changed(outs, inside: set) -> dict:
        diff = [(i, k) for i in ids for k, o in ref_outs.get(i, {}).items() if outs.get(i, {}).get(k) != o]
        return {"outcomes_changed_inside": sum(1 for i, _ in diff if i in inside),
                "outcomes_changed_outside": sum(1 for i, _ in diff if i not in inside),
                "automations_changed_outside": len({i for i, _ in diff if i not in inside})}

    def item(names: list[str], families, inside: set, primary_names: list[str], tag: str) -> dict:
        have = [n for n in names if (scratch / f"ol-p2-{n}.pkl").is_file()]
        if not have:
            return {"settings_run": 0}
        sets, recs, per = {"ref": family_sets(ref_rec, groups)}, {"ref": ref_rec}, {}
        for n in have:
            outs, firsts, meta = _load(scratch, n)
            recs[n] = recs_of(base, outs, firsts)
            sets[n] = family_sets(recs[n], groups)
            per[n] = {"counts": {f: len(sets[n][f]) for f in families},
                      "lost": {f: len(published[f] - sets[n][f]) for f in families},
                      "gained": {f: len(sets[n][f] - published[f]) for f in families},
                      **changed(outs, inside), **meta}
        prim = [n for n in primary_names if n in have]
        res = {"settings_run": len(have), "per_setting": per}
        for label, ns in ((tag, prim), (f"{tag}_with_reference", ["ref", *prim])):
            if prim:
                res[label] = {f: bracket({n: sets[n][f] for n in ns}, published[f], ns) for f in families}
        if prim:
            fp = published["frame_flags"]
            res["frame_flags_kinds_changed_somewhere"] = sum(
                1 for i in fp if any(recs[n][i]["frame"] != pub_rec[i]["frame"] for n in ["ref", *prim]))
            res["humidity_flags_kinds_changed_somewhere"] = sum(
                1 for i in published["frame_humidity_flags"]
                if any(recs[n][i]["frame"] != pub_rec[i]["frame"] for n in ["ref", *prim]))
        return res

    mnames = list(moisture_settings())
    out["item6_moisture"] = item(mnames, ITEM6, groups["humidity"], mnames, "over_x0_x0.5_x1")
    # over x0, x0.5, x1: the bracket "over_..._with_reference" is the registered one (the reference is x1)
    draws = json.loads((scratch / "draws.json").read_text()) if (scratch / "draws.json").is_file() else None
    if draws:
        onames = list(outdoor_settings(draws["draws"]))
        out["item7_outdoor"] = item(onames, ITEM7, th, onames, "over_8_draws")
        out["item7_outdoor"]["residuals"] = draws["residuals"]
        out["item7_outdoor"]["draws"] = {k: {"sources_used": v["sources_used"],
                                             "summary": {s: round(x, 3) for s, x in v["summary"].items()},
                                             "source_by_city": {c.split(",")[0]: s for c, s in v["source"].items()}}
                                         for k, v in draws["draws"].items()}
    return out


# ---- the context runs (A23.3) ----------------------------------------------------------------------------------------

def off_commands(rows: list[dict]) -> list[tuple[float, str]]:
    """The accepted thermostat-off commands of a run, (t, automation label): `climate.turn_off`, or a climate service
    whose value is the mode off."""
    out = []
    for row in rows:
        svc = str(row.get("service", ""))
        if not svc.startswith("climate.") or row.get("accepted", True) is False:
            continue
        if svc == "climate.turn_off" or str(row.get("value", "")).strip().lower() == "off":
            out.append((row["t"], row["automation"]))
    return out


def off_class(offs: list[tuple[float, str]]) -> str:
    labels = {lb for _t, lb in offs}
    if "edt:0" in labels:
        return "target_turns_off"
    return "companion_turns_off" if labels else "setpoint_only"


def _ctx_job(job: dict) -> dict:
    """One closed-loop run as r4b's `_closed_job` (the same fields), plus the run's accepted thermostat-off commands."""
    from vesper.edt.confirm import closed_loop
    from vesper.edt.setback import setback_intervals
    from vesper.env import indoor
    r4b._catalog_once()
    with indoor.knobs(**job["knobs"]):
        r = closed_loop(job["bound"], r2c._env(job["env"]), Path(job["home"]), Path(job["cache"]), days=r4b.DAYS,
                        catalog_dir=job["catalog"], companions=job.get("companions"))
    lo, hi = WINDOW_S[0], WINDOW_S[1] + 86400.0
    climate = [{k: row.get(k) for k in ("t", "service", "feature", "value", "accepted", "kind", "automation",
                                         "entity")}
               for row in r["rows"] if str(row.get("service", "")).startswith("climate.")]
    target = climate if job.get("companions") is None else [x for x in climate if x["automation"] == "edt:0"]
    out = {"tag": job["tag"], "rooms": r["rooms"], "extremes": r2c._extremes(r["temperatures"]),
           "setback": setback_intervals(target, lo, hi), "refused": bool(r["refused"]),
           "firings": len(r["firings"]), "offs": off_commands(climate) if job.get("companions") is not None else []}
    if job.get("companions") is not None:
        out["setback_all"] = setback_intervals(climate, lo, hi)
        out["climate"] = [(x["t"], x["automation"], x["entity"], x["value"], x.get("accepted", True))
                          for x in climate if isinstance(x["value"], (int, float))]
    return out


def part_context(a, lab: Path, scratch: Path, only: list[str]) -> None:
    d = r4b.load(lab, SETBACK_COMMIT)
    keys = r4b.r2c_envs(d)
    for label, kn in context_corners().items():
        if only and label not in only:
            continue
        out = scratch / f"ctx-{label}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        common = {"home": d["home"], "cache": d["cache"], "catalog": a.catalog, "knobs": dict(kn)}
        jobs = [{"tag": (label, "base", None, k), "bound": None, "env": d["envs"][k], **common} for k in keys]
        for aid in d["pop"]:
            comp = [d["run"][i]["bound"] for i in r4b._repo_set(d, aid)]
            for k in keys:
                e = d["envs"][k]
                jobs += [{"tag": (label, "alone", aid, k), "bound": d["run"][aid]["bound"], "env": e,
                          "companions": [], **common},
                         {"tag": (label, "ctx", aid, k), "bound": d["run"][aid]["bound"], "env": e,
                          "companions": comp, **common},
                         {"tag": (label, "rest", aid, k), "bound": None, "env": e, "companions": comp, **common}]
        jobs.sort(key=lambda j: -len(j.get("companions") or []))
        print(f"context {label}: {len(jobs)} runs", flush=True)
        t0 = time.monotonic()
        done = r4b._pool(_ctx_job, jobs, a.workers, f"context {label}")
        r4b._dump(out, {"done": done, "elapsed_s": time.monotonic() - t0, "knobs": kn})
        print(f"context {label}: {time.monotonic() - t0:.0f} s", flush=True)


def _breaches(run: dict, baseline: dict) -> tuple[bool, float | None]:
    from vesper.edt.confirm import envelope
    ev = envelope(run["extremes"], run["rooms"], (1, 2), baseline=baseline)
    caused = [v["min_c"] for v in ev.values() if v and v["below_10"]]
    return bool(caused), (min(caused) if caused else None)


def _span3(xs) -> list | None:
    xs = sorted(x for x in xs if x is not None)
    return [round(xs[0], 2), round(statistics.median(xs), 2), round(xs[-1], 2)] if xs else None


def judge_setback(runs: dict, label: str, pop: list[str], keys: list[str], names: dict[str, str],
                  repo_of: dict[str, str], static_off: set[str]) -> dict:
    """R4-B's A7.4 judgement of the setback runs at one corner, with A23.3's rule kind."""
    rows = []
    for aid in pop:
        for k in keys:
            base = runs[(label, "base", None, k)]["extremes"]
            al, cx, rest = (runs[(label, m, aid, k)] for m in ("alone", "ctx", "rest"))
            b_al, _ = _breaches(al, base)
            b_cx, cold_cx = _breaches(cx, base)
            own, _ = _breaches(cx, rest["extremes"])
            why = None
            if b_al and not b_cx:
                restorers = [lb for a_, b_ in al["setback"] for t, lb, _e, val, acc in cx["climate"]
                             if lb != "edt:0" and acc and a_ <= t < b_ and val >= COLD_C]
                why = ("restored_by_companion" if restorers else "no_setback_in_context" if not cx["setback"]
                       else "other")
            rows.append({"automation": names[aid], "key": k, "alone": b_al, "ctx": b_cx, "own": own and b_cx,
                         "why": why, "cold_ctx": cold_cx, "off": off_class(cx.get("offs", [])),
                         "static_off": names[aid] in static_off})

    def kinds(sel):
        c = Counter(r["off"] for r in sel)
        return {k: c.get(k, 0) for k in OFF}

    def static(sel):
        c = Counter("turns_thermostat_off" if r["static_off"] else "setpoint_only" for r in sel)
        return {k: c.get(k, 0) for k in ("setpoint_only", "turns_thermostat_off")}
    ctx = [r for r in rows if r["ctx"]]
    new = [r for r in ctx if not r["alone"]]
    lost = [r for r in rows if r["alone"] and not r["ctx"]]
    autos_ctx = {r["automation"] for r in ctx}
    return {"runs": len(rows), "breaching_alone": sum(r["alone"] for r in rows),
            "breaching_in_context": len(ctx), "survive": sum(r["alone"] and r["ctx"] for r in rows),
            "survive_and_own": sum(r["alone"] and r["own"] for r in rows), "new_in_context": len(new),
            "new_in_context_own": sum(r["own"] for r in new), "in_context_own": sum(r["own"] for r in ctx),
            "lost": len(lost), "why_lost": dict(Counter(r["why"] for r in lost)),
            "automations_breaching_alone": len({r["automation"] for r in rows if r["alone"]}),
            "automations_breaching_in_context": len(autos_ctx),
            "repositories_breaching_in_context": len({repo_of[x] for x in autos_ctx}),
            "automations_new_in_context": sorted({r["automation"] for r in new}),
            "automations_in_context": sorted(autos_ctx),
            "coldest_caused_room_in_context_c": _span3(r["cold_ctx"] for r in ctx),
            "rule_kind": {"in_context": kinds(ctx), "new_in_context": kinds(new),
                          "in_context_target_static": static(ctx), "new_in_context_target_static": static(new)},
            "per_run": [{"automation": r["automation"], "alone": r["alone"], "in_context": r["ctx"], "own": r["own"],
                         "why_lost": r["why"], "rule_kind": r["off"]} for r in rows]}


def report_context(lab: Path, scratch: Path) -> dict | None:
    import r8y_physics as r8y
    d = r4b.load(lab, SETBACK_COMMIT)
    keys = r4b.r2c_envs(d)
    repo_ix: dict[str, str] = {}
    for aid in d["pop"]:
        repo_ix.setdefault(d["run"][aid]["repo"], f"R{len(repo_ix) + 1}")
    repo_of = {d["names"][aid]: repo_ix[d["run"][aid]["repo"]] for aid in d["pop"]}
    static_off = {d["names"][aid] for aid in d["pop"] if r8y.turns_thermostat_off(d["run"][aid]["bound"])}
    out = {"environments": [r2c.label(r2c._env(d["envs"][k])) for k in keys], "automations": len(d["pop"]),
           "turns_thermostat_off_statically": len(static_off), "corners": {}}
    for label in context_corners():
        path = scratch / f"ctx-{label}.pkl"
        if not path.is_file():
            continue
        p = pickle.loads(path.read_bytes())
        runs = {r["tag"]: r for r in p["done"]}
        out["corners"][label] = {"elapsed_s": round(p["elapsed_s"], 1),
                                 "refused_runs": sum(1 for r in p["done"] if r["refused"]),
                                 **judge_setback(runs, label, d["pop"], keys, d["names"], repo_of, static_off)}
    r4b_pub = json.loads((Path(__file__).resolve().parents[2] / "results/edt/error-budget-r4b-120f4ca.json")
                         .read_text())["item6_context"]["setback"]
    out["r4b_published_nominal"] = {k: r4b_pub[k] for k in ("runs", "breaching_alone", "breaching_in_context",
                                                             "survive", "survive_and_own", "new_in_context", "lost",
                                                             "why_lost")}
    g0 = out["corners"].get("G0-Foff-W1-C165")
    if g0:
        pub = {(r["automation"], r["env"]): r for r in r4b_pub["per_run"]}
        mine = {(r["automation"], out["environments"][i % len(keys)]): r
                for i, r in enumerate(g0["per_run"])}
        out["check_g0_against_r4b"] = {
            "alone_equal": sum(1 for k, r in mine.items() if pub[k]["breach_alone"] == r["alone"]),
            "in_context_equal": sum(1 for k, r in mine.items() if pub[k]["breach_context"] == r["in_context"]),
            "runs": len(mine),
            "in_context_differ": sorted(f"{a} {e}: R4-B {pub[(a, e)]['breach_context']}, now {r['in_context']}"
                                        for (a, e), r in mine.items()
                                        if pub[(a, e)]["breach_context"] != r["in_context"])}
    return out


# ---- main ------------------------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("ref", "moisture", "draws", "outdoor", "context", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r11-P2/scratch"))
    ap.add_argument("--settings", default="", help="moisture/outdoor/context: comma-separated subset")
    ap.add_argument("--appliances", default=".vesper/datasets/uci-appliances")
    ap.add_argument("--sml2010", default=".vesper/datasets/uci-sml2010")
    ap.add_argument("--weather-cache", default=".vesper/datasets/era5")
    ap.add_argument("--station", default=str(Path.home() / "vesper-lab/edt/r6-B/station/08284099999-2012.csv"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    import vesper
    print("vesper from", Path(vesper.__file__).parent, flush=True)
    only = [x for x in a.settings.split(",") if x]
    if a.part == "report":
        return report(a, lab, scratch)
    if a.part == "context":
        part_context(a, lab, scratch, only)
        return 0
    d = r4b.load(lab, COMMIT)
    if a.part == "draws":
        out = part_draws(a, scratch, sorted({e["city"] for e in d["sweep"]["envs"]}))
        print(json.dumps({"residuals": out["residuals"],
                          "draws": {k: {"summary": v["summary"], "sources_used": v["sources_used"]}
                                    for k, v in out["draws"].items()}}, indent=1))
        return 0
    ids = r7b.replay_set(d)
    if a.part == "ref":
        run_open(d, a, scratch, {"ref": {}}, ["ref"], ids)
    elif a.part == "moisture":
        table = moisture_settings()
        run_open(d, a, scratch, table, only or list(table), ids)
    elif a.part == "outdoor":
        draws = json.loads((scratch / "draws.json").read_text())["draws"]
        table = outdoor_settings(draws)
        run_open(d, a, scratch, table, only or list(table), ids)
    return 0


def report(a, lab: Path, scratch: Path) -> int:
    import subprocess
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
    out = {"analysis": "R11-P2 (protocol Addendum 23, post hoc): the moisture excess at x0 / x0.5 / x1, a per-site "
                       "day-block outdoor perturbation (8 draws), the in-context setback set at 2 W/m2",
           "inputs": {"replay_set_and_partition": COMMIT, "published_memberships_and_reference": BASE,
                      "setback_population": SETBACK_COMMIT, "reporting_rule": r7b.RULE},
           "code_commit": commit7,
           "open_loop": report_open(lab, scratch),
           "item10c_context": report_context(lab, scratch)}
    path = Path(a.out or f"results/edt/r11p2-{commit7}.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
