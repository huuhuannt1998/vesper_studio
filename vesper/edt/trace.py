"""Environment traces: what every sensor of the standard EDT home reports, over three days, in one environment.

An environment is a city x date x plan orientation x model rung (`Env`). A trace is the home with no automations and
no residents living RUN_DAYS days from local midnight (day 0 is the thermal warm-up) in that environment; the file
`trace-<Env.key>.jsonl.gz` holds a header line {"env", "site", "start_time"} and then every report, one JSON object
per line {"t", "twin_id", "feature", "value", "observed_t"}, with t and observed_t logical seconds from the run's
start. Later tasks replay public automations against these traces, so an automation never needs the simulator.

Rungs (how much of the environment model the home gets):
  4  the full model (site, recorded weather, windows and the sun, the room's thermal model);
  3  the full model with daylight scaled by 1/14.8 (what validation D measured against a real lux sensor);
  2  no site in the home (the assumed indoor curve every home had before); the header still names the site, so the
     sun is known to anything that reads the trace;
  1  as 2, and the header names no site either.
"""
from __future__ import annotations

import contextlib
import dataclasses
import gzip
import itertools
import json
import os
import shutil
import tempfile
from pathlib import Path

import yaml

from vesper.edt.types import Env
from vesper.validation.crosssite import CITIES

DATES = ("2026-01-15", "2026-04-15", "2026-07-15", "2026-10-15")
NORTHS = (0.0, 180.0)
RUNG3_DAYLIGHT_SCALE = 1 / 14.8
ENVIRONMENTS: list[Env] = [Env(city, date, north, 4)
                           for city, date, north in itertools.product(CITIES, DATES, NORTHS)]


def record_trace(home_yaml: Path, env: Env, cache: Path, *, hours: float | None = None, fetch: bool = True,
                 get=None, catalog_dir: str = "catalog/vds") -> Path:
    """Record `env`'s trace of the EDT home `home_yaml` into `cache/trace-<env.key>.jsonl.gz`; its path. A trace
    already there is reused (nothing runs). Recorded weather (rungs 3 and 4) is fetched into `cache/weather` once
    per city and date, shared by every environment there; `fetch=False` uses whatever is cached (else the assumed
    clear sky). `hours`: a shorter run than RUN_DAYS days, for tests. `get`: httpx.get, injectable."""
    from vesper.env.outdoor import RUN_DAYS
    if env.rung not in (1, 2, 3, 4):
        raise ValueError(f"rung {env.rung}: 1, 2, 3 or 4")
    cache = Path(cache)
    out = cache / f"trace-{env.key}.jsonl.gz"
    if out.is_file():
        return out
    cache.mkdir(parents=True, exist_ok=True)
    site = _site(env)
    if site is not None and env.rung >= 3 and fetch:
        _fetch_weather(site, cache / "weather", get)
    seconds = (RUN_DAYS * 86400) if hours is None else hours * 3600
    header = {"env": {**dataclasses.asdict(env), "key": env.key},
              "site": site.as_dict() if site and env.rung > 1 else None, "start_time": _start_time(site, env)}
    tmp = out.with_name(f".{out.name}.tmp{os.getpid()}")
    try:
        reports = _run(Path(home_yaml), env, site, cache / "weather", seconds, catalog_dir)
        with gzip.open(tmp, "wt") as f:
            f.write(json.dumps(header) + "\n")
            for r in reports:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, out)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return out


def _site(env: Env):
    """The environment's place (city, date, orientation), or None when it names none."""
    from vesper.env.site import parse_site
    if env.city is None or env.date is None:
        if env.rung >= 3:
            raise ValueError(f"rung {env.rung} needs a city and a date: {env}")
        return None
    return parse_site({"city": env.city, "date": env.date, "north_deg": env.north_deg or 0.0})


def _start_time(site, env: Env) -> str | None:
    if site is not None:
        return site.local(0).isoformat()
    return f"{env.date}T00:00:00" if env.date else None


def _fetch_weather(site, cache: Path, get) -> None:
    from vesper.env import weather
    from vesper.env.outdoor import RUN_DAYS
    days = weather.utc_days(site.utc(0), site.utc(RUN_DAYS * 86400))
    if all(weather.cache_path(cache, site.lat, site.lon, d).is_file() for d in days):
        return
    weather.fetch(site.lat, site.lon, days[0], days[-1], cache, get=get)


def _run(home_yaml: Path, env: Env, site, weather_cache: Path, seconds: float, catalog_dir: str) -> list[dict]:
    """Live the home `seconds` from local midnight in `env`; its reports. The home is copied, never touched."""
    from vesper.env import indoor
    old_scale = indoor.DAYLIGHT_SCALE
    if env.rung == 3:
        indoor.DAYLIGHT_SCALE = RUNG3_DAYLIGHT_SCALE
    try:
        with tempfile.TemporaryDirectory(prefix="edt-trace-") as tmp:
            return _live(_copy_home(home_yaml, Path(tmp) / "home", site if env.rung >= 3 else None, weather_cache),
                         seconds, catalog_dir)
    finally:
        indoor.DAYLIGHT_SCALE = old_scale


def _copy_home(home_yaml: Path, out: Path, site, weather_cache: Path) -> Path:
    """A copy of the EDT home for one run: the site (or none), midnight start, no residents, no automations."""
    shutil.copytree(home_yaml.parent, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    data = yaml.safe_load((out / "home.yaml").read_text())
    data["start_time"] = "00:00"
    data.pop("residents", None)
    data.pop("site", None)
    if site is not None:
        data["site"] = {k: v for k, v in site.as_dict().items() if k != "name"} | {"city": site.name}
    data["automations"] = "automations.yaml"
    (out / "automations.yaml").write_text("[]\n")
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    if site is not None and weather_cache.is_dir():
        shutil.copytree(weather_cache, out / "weather", dirs_exist_ok=True)
    return out / "home.yaml"


def _live(home_yaml: Path, seconds: float, catalog_dir: str) -> list[dict]:
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    from vesper.twins.home import load_catalog
    home = load_lab_home(home_yaml, load_catalog(Path(catalog_dir)))
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=False, automations=True)
    try:
        s.clock.advance(seconds)
    finally:
        s.stop()
    return [{"t": r["t"], "twin_id": s.twin_ids[r["device"]], "feature": r["feature"], "value": r["value"],
             "observed_t": r["observed_t"]} for r in s.records if r.get("kind") == "report"]
