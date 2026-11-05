#!/usr/bin/env python3
"""r7w_weather.py -- VESPER revision R7-W (post hoc, protocol Addendum 14): recorded shortwave and three more ERA5 years
(fourth internal review, item 9).

Parts (raw results local, under --scratch; `report` assembles the committed aggregates):

- `fetch`    ERA5 from the Open-Meteo archive (models=era5), politely (a generic User-Agent, no e-mail, a pause between
             requests), every request logged with its URL in --scratch/requests.jsonl; a day already cached is never
             asked for again. (a) direct_radiation, diffuse_radiation and snow_depth for the 4 dates x 6 sites the
             recorded weather covers (sidecars; the recorded days themselves are not fetched again); (b) the same
             windows, every variable, in each further year; (c) the shortwave sidecars for home B's days (dataset B,
             Valencia), for k̂.
- `sweep`    one rung-4 sweep (48 environments, current binder, device reporting rule) in a fresh work directory:
             `--what shortwave` with both A14 options on (2026, the recorded weather copied from work-120f4ca and the
             sidecars from --scratch), or `--what year --year Y` (the published model, the year's recorded weather).
             Output: --lab/sweep-rung4-r7w-<shortwave|yY>-<commit7>.json.
- `setback`  R2-c's 22 setback automations closed loop alone, against the baseline, in the core four (Minneapolis and
             Stockholm, January 14, both orientations) of a year (R4-B's worker and judge at its nominal corner).
- `khat`     R6-B's k̂ (the aperture implied by home B's residual) with the assumed split (the reproduction), the
             recorded split, and the recorded split with the ground-reflected term; from the cached weather only.
- `report`   results/edt/r7w-<commit7>.json: aggregates only (counts, kinds, temperatures, environment labels); no
             automation, repository or entity name, no hash, no path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r7w_weather.py --part P [--lab ~/vesper-lab/edt]
        [--scratch ~/vesper-lab/edt/r7-W] [--corpus DIR] [--workers 4] [--catalog catalog/vds] [--year Y]

Inputs (local, never committed): --lab/sweep-rung4-{8fcd672,r6d-5b54586,120f4ca}.json, --lab/work-120f4ca (the
standard home, its recorded weather), results/edt/r6b-66407d5.json (the published k̂), dataset B and its cached ERA5.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "edt_analysis"))
sys.path.insert(0, str(HERE.parents[2]))

YEARS = (2023, 2024, 2025)
RECORDED_YEAR = 2026
MAIN, R6D = "8fcd672", "r6d-5b54586"
SHORTWAVE_KNOBS = {"RECORDED_SPLIT": 1, "GROUND_REFLECTED": 1}
WEATHER_FROM = "work-120f4ca"
PAUSE_S = 2.0
USER_AGENT = "python-httpx"  # generic: names no person, no address
CLOCK_KEY, FRAME_KEY = "divergences", "frame_divergences"
CORE_CITIES = ("Minneapolis", "Stockholm")


def commit7() -> str:
    return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                          check=True, cwd=HERE.parents[2]).stdout.strip()


# ---- fetch -----------------------------------------------------------------------------------------------------------

def polite_get(log: Path):
    import httpx

    def get(url, params=None, timeout=60):
        time.sleep(PAUSE_S)
        r = httpx.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        with log.open("a") as f:
            f.write(json.dumps({"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "url": str(r.url),
                                "status": r.status_code, "user_agent": USER_AGENT}) + "\n")
        return r
    return get


def windows(year: int | None):
    """(site, first UTC day, last UTC day) of every city and date, as a trace's weather fetch has them."""
    from vesper.edt.trace import env_site, environments
    from vesper.env import weather
    from vesper.env.outdoor import RUN_DAYS
    seen = {}
    for env in environments(year):
        if (env.city, env.date) in seen:
            continue
        site = env_site(env)
        days = weather.utc_days(site.utc(0), site.utc(RUN_DAYS * 86400))
        seen[(env.city, env.date)] = (site, days[0], days[-1])
    return list(seen.values())


def part_fetch(a, scratch: Path, lab: Path) -> None:
    from vesper.env import weather
    era = scratch / "era5"
    log = scratch / "requests.jsonl"
    get = polite_get(log)
    recorded = lab / WEATHER_FROM / "cache" / "weather"
    for site, first, last in windows(None):  # (a): the recorded days are there; only the sidecars are new
        days = [first + dt.timedelta(days=k) for k in range((last - first).days + 1)]
        missing = [d for d in days if weather.cached(recorded, site.lat, site.lon, d) is None]
        if missing:
            raise SystemExit(f"the recorded weather lacks {missing} for {site.name}: not fetching it")
        weather.fetch_shortwave(site.lat, site.lon, first, last, era, get=get)
        weather.fetch_snow(site.lat, site.lon, first, last, era, get=get)
    for year in YEARS:  # (b)
        for site, first, last in windows(year):
            days = [first + dt.timedelta(days=k) for k in range((last - first).days + 1)]
            if any(weather.cached(era, site.lat, site.lon, d) is None for d in days):
                weather.fetch(site.lat, site.lon, first, last, era, get=get)
            weather.fetch_shortwave(site.lat, site.lon, first, last, era, get=get)
            weather.fetch_snow(site.lat, site.lon, first, last, era, get=get)
    data = _dataset_b(a)  # (c)
    if data is not None:
        df, site, _spec = data
        valid = scratch / "era5-valid"
        first, last = df.index[0].date(), df.index[-1].date()
        _copy_base(Path(a.weather_cache), valid, site, first, last)
        weather.fetch_shortwave(site.lat, site.lon, first, last, valid, get=get)
        weather.fetch_snow(site.lat, site.lon, first, last, valid, get=get)
    n = sum(1 for _ in log.open()) if log.is_file() else 0
    print(f"fetch done; {n} requests logged in {log}", flush=True)


def _copy_base(src: Path, dst: Path, site, first, last) -> None:
    """The validation's cached ERA5 days, copied (never fetched again) beside their sidecars."""
    from vesper.env import weather
    dst.mkdir(parents=True, exist_ok=True)
    d = first
    while d <= last:
        p = weather.cached(src, site.lat, site.lon, d)
        if p is None:
            raise SystemExit(f"dataset B's cached ERA5 lacks {d}: not fetching it")
        if not (dst / p.name).is_file():
            shutil.copy2(p, dst / p.name)
        d += dt.timedelta(days=1)


# ---- sweep -----------------------------------------------------------------------------------------------------------

def part_sweep(a, scratch: Path, lab: Path) -> None:
    from vesper.edt.sweep import sweep
    from vesper.edt.trace import environments
    from vesper.env import weather
    c = commit7()
    label = "shortwave" if a.what == "shortwave" else f"y{a.year}"
    work = lab / f"work-r7w-{label}-{c}"
    out = lab / f"sweep-rung4-r7w-{label}-{c}.json"
    if work.exists():
        raise SystemExit(f"work directory exists: {work}")
    dst = work / "cache" / "weather"
    dst.mkdir(parents=True)
    era = scratch / "era5"
    if a.what == "shortwave":
        year, knobs = None, SHORTWAVE_KNOBS
        shutil.copytree(lab / WEATHER_FROM / "cache" / "weather", dst, dirs_exist_ok=True)
    else:
        year, knobs = a.year, None
    for site, first, last in windows(year):
        d = first
        while d <= last:
            names = [weather.shortwave_path(era, site.lat, site.lon, d),
                     weather.snow_path(era, site.lat, site.lon, d)] if knobs else []
            if year is not None:
                names.append(weather.cache_path(era, site.lat, site.lon, d))
            for p in names:
                if not p.is_file():
                    raise SystemExit(f"not fetched: {p.name} (run --part fetch)")
                shutil.copy2(p, dst / p.name)
            d += dt.timedelta(days=1)
    (scratch / "runs.jsonl").open("a").write(json.dumps({"what": label, "commit": c, "workers": a.workers,
                                                         "start": dt.datetime.now(dt.UTC).isoformat()}) + "\n")
    t0 = time.monotonic()
    result = sweep(Path(a.corpus), work, environments(year), rung=4, workers=a.workers, fetch=False,
                   catalog_dir=a.catalog, log=lambda m: print(m, file=sys.stderr, flush=True),
                   reporting_rule="device", binder="current", knobs=knobs)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(result, sort_keys=True) + "\n")
    tmp.replace(out)
    (scratch / "runs.jsonl").open("a").write(json.dumps({"what": label, "commit": c, "out": out.name,
                                                         "elapsed_s": round(time.monotonic() - t0),
                                                         "end": dt.datetime.now(dt.UTC).isoformat()}) + "\n")
    print(f"{label}: flagged {result['flagged']['n']} clock, {result['flagged_frame']['n']} frame; {out}", flush=True)


# ---- setback ---------------------------------------------------------------------------------------------------------

def core_envs(year: int) -> dict[str, dict]:
    from vesper.edt.trace import environments
    out = {}
    for e in environments(year):
        if e.city.split(",")[0] in CORE_CITIES and e.date.endswith("-01-14"):
            out[e.key] = {"city": e.city, "date": e.date, "north_deg": e.north_deg, "rung": e.rung, "key": e.key}
    return out


def part_setback(a, scratch: Path, lab: Path) -> None:
    import pickle

    import r4b_error_budget as r4b
    d = r4b.load(lab, "120f4ca")  # R2-c's population, as R4-B ran it
    year = a.year
    cache = lab / WEATHER_FROM / "cache" if year == RECORDED_YEAR else \
        next(lab.glob(f"work-r7w-y{year}-*/cache"), None)
    if cache is None:
        raise SystemExit(f"no sweep work directory for {year} (its weather): run --part sweep --what year first")
    envs = core_envs(year)
    kn = r4b.corner_knobs(r4b.NOMINAL)
    common = {"home": d["home"], "cache": str(cache), "catalog": a.catalog, "knobs": kn}
    jobs = [{"tag": ("nominal", None, k), "bound": None, "env": envs[k], **common} for k in sorted(envs)]
    jobs += [{"tag": ("nominal", aid, k), "bound": d["run"][aid]["bound"], "env": envs[k], **common}
             for aid in d["pop"] for k in sorted(envs)]
    t0 = time.monotonic()
    done = r4b._pool(r4b._closed_job, jobs, a.workers, f"setback {year}")
    path = scratch / f"setback-{year}.pkl"
    path.write_bytes(pickle.dumps({"done": done, "envs": envs, "cache": str(cache), "population": len(d["pop"]),
                                   "elapsed_s": time.monotonic() - t0}))
    print(f"setback {year}: {len(done)} runs in {time.monotonic() - t0:.0f} s -> {path}", flush=True)


def setback_summary(scratch: Path, year: int, names: dict) -> dict | None:
    import pickle

    import r2c_setback_population as r2c
    path = scratch / f"setback-{year}.pkl"
    if not path.is_file():
        return None
    s = pickle.loads(path.read_bytes())
    rows = r2c.judge(s["done"], s["envs"], names, Path(s["cache"]))["nominal"]
    hit = [r for r in rows if r["caused"]]
    coldest = sorted(r["coldest_caused_room_c"] for r in hit)
    out_air = _outdoor_core(s["envs"], Path(s["cache"]))
    return {"year": year, "breaching_runs": len(hit), "denominator": len(rows),
            "automations": len({r["automation"] for r in hit}),
            "by_env": dict(sorted(Counter(r["env"] for r in hit).items())),
            "by_city": dict(sorted(Counter(r["city"] for r in hit).items())),
            "coldest_caused_room_c": [coldest[0], coldest[len(coldest) // 2], coldest[-1]] if coldest else None,
            "baseline_coldest_room_c": min((r["baseline_coldest_room_c"] for r in rows
                                            if r["baseline_coldest_room_c"] is not None), default=None),
            "outdoor_days_1_2_c": out_air}


def _outdoor_core(envs: dict, cache: Path) -> dict:
    """The outdoor air over days 1-2 of each core city's run (hourly, the run's recorded weather): mean and minimum."""
    from vesper.edt.trace import env_site
    from vesper.edt.types import Env
    from vesper.env import weather
    out = {}
    for e in envs.values():
        if e["north_deg"] != 0.0:
            continue
        site = env_site(Env(e["city"], e["date"], e["north_deg"], e["rung"]))
        w = weather.load(site.lat, site.lon, weather.utc_days(site.utc(0), site.utc(3 * 86400)), cache / "weather")
        vals = [v for h in range(24, 72) if (v := (w.at(site.utc(h * 3600)) if w else {}).get("temperature_2m"))
                is not None]
        out[e["city"].split(",")[0]] = {"mean_c": round(sum(vals) / len(vals), 2) if vals else None,
                                        "min_c": min(vals) if vals else None, "hours": len(vals)}
    return out


# ---- khat ------------------------------------------------------------------------------------------------------------

def _dataset_b(a):
    import r6b_physics as r6b
    a.appliances = "/nonexistent"
    return r6b._datasets(a).get("B")


def _measured_outdoor(recorded: bool):
    from vesper.validation import environment as ev

    class Measured(ev._MeasuredOutdoor):
        """As the validation's outdoors, with the hour's recorded split and snow depth where the A14 options read
        them (the split only when `recorded`)."""

        def __init__(self, site, start, temp, rh, ghi, cloud, direct, diffuse, snow):
            super().__init__(site, start, temp, rh, ghi, cloud)
            self.direct, self.diffuse, self.snow = direct, diffuse, snow

        def _at(self, seconds: float):
            import dataclasses

            from vesper.env.sun import recorded_direct_fraction
            st = super()._at(seconds)
            k = min(int(seconds // 3600), len(self.temp) - 1)
            share = recorded_direct_fraction(self.direct[k], self.diffuse[k]) if recorded else None
            return dataclasses.replace(st, direct_fraction=st.direct_fraction if share is None else share,
                                       snow_depth_m=self.snow[k])
    return Measured


def _drive_from_cache(cache: Path, recorded: bool | None):
    """R6-B's `_drive`, from the cached weather only (never fetched). `recorded` None: R6-B's own outdoors."""
    def drive(df, site, spec, _cache):
        from vesper.env import weather
        from vesper.validation import environment as ev
        idx = df.index
        rec = weather.load(site.lat, site.lon, weather.utc_days(idx[0].to_pydatetime(), idx[-1].to_pydatetime()),
                           cache, shortwave=recorded is not None)
        rows = [rec.at(t.to_pydatetime()) if rec is not None and rec.covers(t.to_pydatetime()) else {} for t in idx]
        era = {k: [r.get(k) for r in rows] for k in ("temperature_2m", "relative_humidity_2m", "shortwave_radiation",
                                                     "cloud_cover", "direct_radiation", "diffuse_radiation",
                                                     "snow_depth")}
        out_t, out_rh = ev._values(df[spec["t_out"]]), ev._values(df[spec["rh_out"]])
        start = idx[0].to_pydatetime()
        series = (out_t, out_rh, era["shortwave_radiation"], era["cloud_cover"])
        if recorded is None:
            shared = ev._MeasuredOutdoor(site, start, *series)
        else:
            shared = _measured_outdoor(recorded)(site, start, *series, era["direct_radiation"],
                                                 era["diffuse_radiation"], era["snow_depth"])
        return start, series, shared, era
    return drive


KHAT_SETTINGS = {"assumed": (None, {}), "recorded_split": (True, {}),
                 "recorded_split_ground": (True, {"GROUND_REFLECTED": 1}),
                 "assumed_split_ground": (False, {"GROUND_REFLECTED": 1})}


def part_khat(a, scratch: Path, lab: Path) -> None:
    import r6b_physics as r6b

    from vesper.env import indoor
    data = _dataset_b(a)
    if data is None:
        raise SystemExit("dataset B not found (--sml2010)")
    df, site, spec = data
    cache = scratch / "era5-valid"
    name = a.setting
    recorded, knobs = KHAT_SETTINGS[name]
    r6b._drive = _drive_from_cache(cache, recorded)
    t0 = time.monotonic()
    with indoor.knobs(**knobs):
        out = r6b.khat(df, site, spec, cache)
    out["elapsed_s"] = round(time.monotonic() - t0)
    if recorded is not None:
        out["recorded_split_hours"] = _split_cover(df, site, cache)
    (scratch / f"khat-{name}.json").write_text(json.dumps(out, indent=1))
    print(name, out["khat"], out["bootstrap"]["ci95"], f"{out['elapsed_s']} s", flush=True)


def _split_cover(df, site, cache) -> dict:
    """Over home B's daylight hours: how many have a recorded split, and its mean against the assumed one."""
    from vesper.env import weather
    from vesper.env.sun import direct_fraction, recorded_direct_fraction, sun_at
    idx = df.index
    rec = weather.load(site.lat, site.lon, weather.utc_days(idx[0].to_pydatetime(), idx[-1].to_pydatetime()), cache,
                       shortwave=True)
    rec_s, ass = [], []
    for t in idx:
        when = t.to_pydatetime()
        if not rec.covers(when) or not sun_at(site.lat, site.lon, site.elevation_m, when).up:
            continue
        v = rec.at(when)
        r = recorded_direct_fraction(v.get("direct_radiation"), v.get("diffuse_radiation"))
        if r is not None:
            rec_s.append(r)
            ass.append(direct_fraction(v.get("cloud_cover")))
    return {"daylight_hours_with_split": len(rec_s), "mean_recorded_share": _mean(rec_s),
            "mean_assumed_share_same_hours": _mean(ass), "source": rec.source.split(";")[-1].strip()}


def _mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


# ---- report ----------------------------------------------------------------------------------------------------------

def ran(result: dict) -> dict[str, dict]:
    return {i: a for i, a in result["automations"].items() if a.get("outcomes")}


def flags(autos: dict, key: str) -> dict[str, list[str]]:
    return {i: sorted(a.get(key) or []) for i, a in autos.items() if a.get(key)}


def compare(old: dict, new: dict) -> dict:
    """Held (same kinds, other kinds), lost and new flags of `new` against `old`, by relation and by kind."""
    ro, rn = ran(old), ran(new)
    out = {"run": {"old": len(ro), "new": len(rn)}}
    for name, key in (("clock_relation", CLOCK_KEY), ("frame_relation", FRAME_KEY)):
        fo, fn = flags(ro, key), flags(rn, key)
        lost, new_ = [i for i in fo if i not in fn], [i for i in fn if i not in fo]
        out[name] = {"old": len(fo), "new": len(fn),
                     "held_same_kinds": sum(1 for i in fo if fn.get(i) == fo[i]),
                     "held_other_kinds": sum(1 for i in fo if i in fn and fn[i] != fo[i]),
                     "lost": len(lost), "new_flags": len(new_),
                     "lost_by_kind": dict(sorted(Counter(k for i in lost for k in fo[i]).items())),
                     "new_by_kind": dict(sorted(Counter(k for i in new_ for k in fn[i]).items())),
                     "lost_by_quantity": dict(sorted(Counter(q for i in lost for q in ro[i]["kinds"]
                                                             or ["(none)"]).items())),
                     "new_by_quantity": dict(sorted(Counter(q for i in new_ for q in rn[i]["kinds"]
                                                            or ["(none)"]).items()))}
    return out


def orientation_witnessed(result: dict) -> dict[str, set]:
    """The flagged automations with an orientation witness (two environments differing only in plan north whose
    outcomes diverge, restricted to the pair), under each relation; as r4a's witnesses."""
    import r4a_analyses as r4a

    from vesper.edt.cover import restricted_divergences
    from vesper.edt.oracle import frame_restricted_divergences, frames_of
    envs = r4a.envs_of(result)
    pairs = [(x, y) for x, y in itertools.combinations(envs, 2) if r4a._axis(x, y) == "north_deg"]
    out = {}
    for name, key in (("clock_relation", CLOCK_KEY), ("frame_relation", FRAME_KEY)):
        got = set()
        for i, a in ran(result).items():
            if not a[key]:
                continue
            o, ft, fr = r4a.outs_of(a, envs), r4a.first_t_of(a, envs), frames_of(a["bound"])
            for x, y in pairs:
                if x in o and y in o and (frame_restricted_divergences(o, ft, fr, [x, y]) if key == FRAME_KEY
                                          else restricted_divergences(o, [x, y])):
                    got.add(i)
                    break
        out[name] = got
    return out


def witness_report(old: dict, new: dict, old_w: dict | None = None) -> dict:
    import r4a_analyses as r4a
    ow = old_w or orientation_witnessed(old)
    nw = orientation_witnessed(new)
    mw = r4a.witnesses(new)
    out = {}
    for name in ("clock_relation", "frame_relation"):
        out[name] = {"minimal_witness_axis": mw[name]["minimal_witness_axis"],
                     "with_an_orientation_witness": len(nw[name]), "old": len(ow[name]),
                     "held": len(ow[name] & nw[name]), "lost": len(ow[name] - nw[name]),
                     "new": len(nw[name] - ow[name])}
    return out


def md_label(pair: list) -> list[str]:
    """A pair's environment labels without the year."""
    return [" ".join(p.split(" ")[:1] + [p.split(" ")[1][5:]] + p.split(" ")[2:]) for p in pair]


def cover_pair(result: dict) -> dict:
    import r4a_analyses as r4a
    pc = r4a.pair_cover(result)
    return {name: {"pair": md_label(pc[name]["greedy"]["1"]["pairs"][0]),
                   "coverage_all": pc[name]["greedy"]["1"]["coverage_all"],
                   "coverage_train": pc[name]["greedy"]["1"]["coverage_train"],
                   "coverage_held_out": pc[name]["greedy"]["1"]["coverage_held_out"],
                   "covered_by_some_pair": pc[name]["covered_by_some_pair_all"],
                   "flagged": pc[name]["flagged"]["all"]}
            for name in ("clock_relation", "frame_relation")}


def summary(result: dict) -> dict:
    y, nc = result["yield"], result["negative_control"]
    return {"bound": y["bound"], "run": y["run"], "failed_envs": len(result.get("failed_envs") or {}),
            "clock_flags": result["flagged"]["n"], "clock_by_kind": result["flagged"]["by_kind"],
            "frame_flags": result["flagged_frame"]["n"], "frame_by_kind": result["flagged_frame"]["by_kind"],
            "negative_control": {"n": nc["n"], "fired": nc["fired"], "flagged": nc["flagged"]},
            "indoor_knobs": result.get("indoor_knobs")}


def weather_check(lab: Path, label: str, c: str) -> dict:
    """Every trace's weather source: how many read the recorded shortwave for all their days."""
    import gzip
    srcs = Counter()
    for p in sorted((lab / f"work-r7w-{label}-{c}" / "cache").glob("trace-*.jsonl.gz")):
        with gzip.open(p, "rt") as f:
            h = json.loads(f.readline())
        w = h.get("weather") or ""
        srcs["recorded split and snow depth, every day" if "recorded shortwave" in w and
             w.count("(4 of 4 days)") == 2 else
             ("recorded shortwave, some days" if "recorded shortwave" in w else "no recorded shortwave")] += 1
        srcs["knobs " + json.dumps(h.get("indoor_knobs"), sort_keys=True)] += 1
    return dict(srcs)


def split_context(scratch: Path, lab: Path) -> dict:
    """Over the 24 recorded city-date windows' sun-up hours (days 0-2 local): the recorded beam share against the
    assumed 0.8 (1 - cloud), and the windows with snow deeper than 1 cm at some hour."""
    from vesper.edt.trace import env_site, environments
    from vesper.env import weather
    from vesper.env.sun import SNOW_DEPTH_M, direct_fraction, recorded_direct_fraction, sun_at
    lab_weather = lab / WEATHER_FROM / "cache" / "weather"
    tmp = scratch / "split-context"
    tmp.mkdir(exist_ok=True)
    rows, snowy = {}, []
    seen = set()
    for env in environments(None):
        if (env.city, env.date) in seen:
            continue
        seen.add((env.city, env.date))
        site = env_site(env)
        days = weather.utc_days(site.utc(0), site.utc(3 * 86400))
        for d in days:
            for src in (weather.cached(lab_weather, site.lat, site.lon, d),
                        weather.shortwave_path(scratch / "era5", site.lat, site.lon, d),
                        weather.snow_path(scratch / "era5", site.lat, site.lon, d)):
                if src is not None and src.is_file() and not (tmp / src.name).is_file():
                    shutil.copy2(src, tmp / src.name)
        w = weather.load(site.lat, site.lon, days, tmp, shortwave=True)
        rec, ass, gap, snow = [], [], 0, 0.0
        for h in range(72):
            when = site.utc(h * 3600 + 1800)
            if not sun_at(site.lat, site.lon, site.elevation_m, when).up or not w.covers(when):
                continue
            v = w.at(when)
            r = recorded_direct_fraction(v.get("direct_radiation"), v.get("diffuse_radiation"))
            if r is None:
                gap += 1
                continue
            rec.append(r)
            ass.append(direct_fraction(v.get("cloud_cover")))
            snow = max(snow, v.get("snow_depth") or 0.0)
        lab_ = f"{env.city.split(',')[0]} {env.date[5:]}"
        rows[lab_] = {"sun_up_hours": len(rec), "no_split": gap, "mean_recorded_share": _mean(rec),
                      "mean_assumed_share": _mean(ass), "max_snow_depth_m": round(snow, 3)}
        if snow > SNOW_DEPTH_M:
            snowy.append(lab_)
    allr = [r for r in rows.values() if r["mean_recorded_share"] is not None]
    return {"windows": rows, "snow_covered_windows": snowy,
            "mean_over_windows": {"recorded": _mean([r["mean_recorded_share"] for r in allr]),
                                  "assumed": _mean([r["mean_assumed_share"] for r in allr])}}


def report(a, scratch: Path, lab: Path) -> None:
    import gc

    import r4b_error_budget as r4b
    c = commit7()
    out: dict = {"analysis": "R7-W (protocol Addendum 14, post hoc): recorded shortwave and three more ERA5 years",
                 "code_commit": c, "inputs": {"published": MAIN, "engine_corrected": R6D}}
    old = json.loads((lab / f"sweep-rung4-{MAIN}.json").read_text())
    old_w = orientation_witnessed(old)
    base199 = flags(ran(old), FRAME_KEY)
    base_clock = flags(ran(old), CLOCK_KEY)
    out["published"] = {"summary": summary(old), "orientation_witnesses": {k: len(v) for k, v in old_w.items()},
                        "cover_pair": cover_pair(old)}
    r6d = json.loads((lab / f"sweep-rung4-{R6D}.json").read_text())
    r6d_w = orientation_witnessed(r6d)
    out["engine_corrected"] = {"summary": summary(r6d), "cover_pair": cover_pair(r6d)}
    sw_path = next(iter(sorted(lab.glob(f"sweep-rung4-r7w-shortwave-{a.run_commit or c}.json"))), None)
    if sw_path is not None:
        sw = json.loads(sw_path.read_text())
        rc = sw_path.stem.split("-")[-1]
        out["shortwave"] = {"sweep_commit": rc, "summary": summary(sw), "weather": weather_check(lab, "shortwave", rc),
                            "against_published": compare(old, sw), "against_engine_corrected": compare(r6d, sw),
                            "orientation_witnesses_against_published": witness_report(old, sw, old_w),
                            "orientation_witnesses_against_engine_corrected": witness_report(r6d, sw, r6d_w),
                            "split_context": split_context(scratch, lab)}
        del sw
        gc.collect()
    del r6d
    gc.collect()
    years: dict = {}
    held: dict[str, list[int]] = {i: [] for i in base199}
    held_same: dict[str, list[int]] = {i: [] for i in base199}
    clock_held: dict[str, list[int]] = {i: [] for i in base_clock}
    for y in YEARS:
        p = next(iter(sorted(lab.glob(f"sweep-rung4-r7w-y{y}-{a.run_commit or c}.json"))), None)
        if p is None:
            continue
        s = json.loads(p.read_text())
        fr, fc = flags(ran(s), FRAME_KEY), flags(ran(s), CLOCK_KEY)
        for i in base199:
            if i in fr:
                held[i].append(y)
                if fr[i] == base199[i]:
                    held_same[i].append(y)
        for i in base_clock:
            if i in fc:
                clock_held[i].append(y)
        years[str(y)] = {"sweep_commit": p.stem.split("-")[-1], "summary": summary(s),
                         "against_published": compare(old, s),
                         "orientation_witnesses_against_published": witness_report(old, s, old_w),
                         "cover_pair": cover_pair(s)}
        del s
        gc.collect()
    out["years"] = years
    ny = len(years)
    if ny:
        out["stability_of_the_199"] = {
            "years": sorted(years), "frame_flags_2026": len(base199),
            "flagged_in_every_year": sum(1 for v in held.values() if len(v) == ny),
            "flagged_in_some_year": sum(1 for v in held.values() if v),
            "flagged_in_no_year": sum(1 for v in held.values() if not v),
            "flagged_in_k_years": dict(sorted(Counter(len(v) for v in held.values()).items())),
            "same_kinds_in_every_year": sum(1 for v in held_same.values() if len(v) == ny),
            "by_kind_held_in_every_year": dict(sorted(Counter(k for i, v in held.items() if len(v) == ny
                                                              for k in base199[i]).items())),
            "by_kind_held_in_no_year": dict(sorted(Counter(k for i, v in held.items() if not v
                                                           for k in base199[i]).items())),
            "by_quantity_held_in_no_year": dict(sorted(Counter(q for i, v in held.items() if not v
                                                               for q in old["automations"][i]["kinds"]
                                                               or ["(none)"]).items()))}
        out["stability_of_the_clock_flags"] = {
            "clock_flags_2026": len(base_clock),
            "flagged_in_every_year": sum(1 for v in clock_held.values() if len(v) == ny),
            "flagged_in_some_year": sum(1 for v in clock_held.values() if v)}
    d = r4b.load(lab, "120f4ca")
    names = d["names"]
    del d
    gc.collect()
    out["setback"] = {str(y): setback_summary(scratch, y, names) for y in (RECORDED_YEAR, *YEARS)}
    out["setback"]["published_r2c_nominal"] = 53
    kh = {}
    for name in KHAT_SETTINGS:
        p = scratch / f"khat-{name}.json"
        if p.is_file():
            k = json.loads(p.read_text())
            kh[name] = {x: k.get(x) for x in ("khat", "bootstrap", "bias_pooled_at_1", "bias_pooled_at_0",
                                                "khat_linear_crosscheck", "mean_solar_gain_w", "per_room",
                                                "hours_pooled", "days", "recorded_split_hours")}
    pub = json.loads((HERE.parents[2] / "results/edt/r6b-66407d5.json").read_text())["validation"]["item3_khat"]
    kh["published_r6b"] = {"khat": pub["khat"], "ci95": pub["bootstrap"]["ci95"]}
    out["khat"] = kh
    lines = (scratch / "requests.jsonl").read_text().splitlines() if (scratch / "requests.jsonl").is_file() else []
    out["fetch"] = {"requests": len(lines), "user_agent": USER_AGENT, "pause_s": PAUSE_S,
                    "statuses": dict(Counter(json.loads(x)["status"] for x in lines)),
                    "endpoint": "https://archive-api.open-meteo.com/v1/archive (models=era5)",
                    "log": "requests.jsonl in the lane's scratch directory (every URL)"}
    dst = Path(a.out or HERE.parents[2] / f"results/edt/r7w-{c}.json")
    dst.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {dst}", flush=True)


# ---- main ------------------------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("fetch", "sweep", "setback", "khat", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r7-W"))
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--what", choices=("shortwave", "year"), default="shortwave")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--setting", choices=sorted(KHAT_SETTINGS), default="recorded_split")
    ap.add_argument("--sml2010", default=".vesper/datasets/uci-sml2010")
    ap.add_argument("--weather-cache", default=".vesper/datasets/era5")
    ap.add_argument("--run-commit", default=None, help="report: the commit the sweeps ran at (default HEAD)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.workers > 4:
        raise SystemExit("at most 4 workers (protocol Addendum 14)")
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    if a.part == "fetch":
        part_fetch(a, scratch, lab)
    elif a.part == "sweep":
        if not a.corpus or (a.what == "year" and a.year not in YEARS):
            raise SystemExit("--corpus, and --year in 2023-2025 for --what year")
        part_sweep(a, scratch, lab)
    elif a.part == "setback":
        if a.year not in (RECORDED_YEAR, *YEARS):
            raise SystemExit("--year 2026 (the reproduction) or 2023-2025")
        part_setback(a, scratch, lab)
    elif a.part == "khat":
        part_khat(a, scratch, lab)
    else:
        report(a, scratch, lab)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
