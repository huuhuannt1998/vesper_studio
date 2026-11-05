"""Environment traces: what every sensor of the standard EDT home reports, over three days, in one environment.

An environment is a city x date x plan orientation x model rung (`Env`). A trace is the home with no automations and
no residents living RUN_DAYS days from local midnight (day 0 is the thermal warm-up) in that environment; the file
`trace-<Env.key>.jsonl.gz` holds a header line and then every report, one JSON object per line {"t", "twin_id",
"feature", "value", "observed_t"}, with t and observed_t logical seconds from the run's start. Later tasks replay
public automations against these traces, so an automation never needs the simulator.

The header: "env" (the Env and its key), "site" (or null), "start_time" (ISO local start), "seconds" (run length),
"weather" (the weather source the run read; null: clear-sky fallback), "home_sha256" (of the home.yaml recorded) and
"initial_state" ({twin_id: state}, as the environment rules left every twin before the first instant: they move
temperature, humidity and the thermostat's setpoint before the first report, which arrives at t=10). A trace is
reused only if env, seconds, weather and home hash all match, and so do the indoor-model knobs set away from their
defaults (header "indoor_knobs", absent at the defaults); the same request gives the same bytes.

The reports are the twins' own, recorded before the link layer: no Zigbee latency, drops or retries. They include
the actuators' reports (lights, running_state, battery and so on), which a replay must ignore: only the sensors an
automation names are inputs.

Rungs (how much of the environment model the home gets):
  4  the full model (site, recorded weather, windows and the sun, the room's thermal model);
  3  the full model with daylight scaled by 1/14.8 (what validation D measured against a real lux sensor);
  2.5 the site and its recorded weather, so the porch's (outdoor) sensors read the outdoor air as at rung 4, but every
     indoor room reads rung 2's assumed curve (protocol Addendum 6, A6.5);
  2  no site in the home (the assumed indoor curve every home had before); the header still names the site, so the
     sun is known to anything that reads the trace;
  1  as 2, and the header names no site either.
"""
from __future__ import annotations

import contextlib
import dataclasses
import gzip
import hashlib
import io
import itertools
import json
import os
import shutil
import tempfile
from pathlib import Path

import yaml

from vesper.edt.types import Env
from vesper.validation.crosssite import CITIES

DATES = ("2026-01-14", "2026-04-15", "2026-07-15", "2026-10-14")  # all Wednesdays (protocol A3.1)
NORTHS = (0.0, 180.0)
RUNG3_DAYLIGHT_SCALE = 1 / 14.8
RUNGS = (1, 2, 2.5, 3, 4)


def has_home_site(env: Env) -> bool:
    """Whether the home itself is placed (site and recorded weather): rungs 2.5, 3 and 4."""
    return env.rung >= 2.5
ENVIRONMENTS: list[Env] = [Env(city, date, north, 4)
                           for city, date, north in itertools.product(CITIES, DATES, NORTHS)]


def record_trace(home_yaml: Path, env: Env, cache: Path, *, hours: float | None = None, fetch: bool = True,
                 get=None, catalog_dir: str = "catalog/vds", reporting_rule: str = "catalog") -> Path:
    """Record `env`'s trace of the EDT home `home_yaml` into `cache/trace-<env.key>.jsonl.gz`; its path. A trace
    already there is reused (nothing runs) only if its header agrees with this request on the environment, the run
    length, the weather source and the home's hash; otherwise it is recorded again, atomically. Recorded weather
    (rungs 2.5, 3 and 4) is fetched into `cache/weather` once per city and date, shared by every environment there;
    `fetch=False` uses whatever is cached (else the assumed clear sky). `hours`: a shorter run than RUN_DAYS days,
    for tests. `get`: httpx.get, injectable. `reporting_rule`: the twins' reporting (vesper.twins.runtime
    REPORTING_RULES): "catalog", the catalog's rules (the traces of 120f4ca, byte for byte), or "device", a device's
    real rule where it departs from the catalog (the RTCGQ11LM's illuminance only with motion or the heartbeat;
    protocol A6.9), named in the header and the file name. The same request gives the same bytes."""
    from vesper.env.outdoor import RUN_DAYS
    if env.rung not in RUNGS:
        raise ValueError(f"rung {env.rung}: 1, 2, 2.5, 3 or 4")
    cache, home_yaml = Path(cache), Path(home_yaml)
    suffix = "" if reporting_rule == "catalog" else f"-rho_{reporting_rule}"
    out = cache / f"trace-{env.key}{suffix}.jsonl.gz"
    cache.mkdir(parents=True, exist_ok=True)
    site = env_site(env)
    if site is not None and has_home_site(env) and fetch:
        _fetch_weather(site, cache / "weather", get)
    seconds = (RUN_DAYS * 86400) if hours is None else hours * 3600
    seconds = int(seconds) if float(seconds).is_integer() else seconds
    home_sha = hashlib.sha256(home_yaml.read_bytes()).hexdigest()
    weather = _cached_weather_source(site, cache / "weather") if site is not None and has_home_site(env) else None
    want = {"env": {**dataclasses.asdict(env), "key": env.key}, "seconds": seconds, "weather": weather,
            "home_sha256": home_sha}
    if reporting_rule != "catalog":  # the catalog's traces keep their header as before
        want["reporting_rule"] = reporting_rule
    from vesper.env.indoor import nondefault_knobs
    knobs = nondefault_knobs()  # a trace recorded under an indoor-model knob is only reused under the same knobs
    if knobs:
        want["indoor_knobs"] = knobs
    old = _header(out) if out.is_file() else None
    if old is not None and all(old.get(k) == v for k, v in want.items()) and \
            old.get("indoor_knobs") == want.get("indoor_knobs"):
        return out
    shown = site if env.rung > 1 else None  # rung 1 names no site, not even through its time zone
    tmp = out.with_name(f".{out.name}.tmp{os.getpid()}")
    try:
        initial, weather_ran, reports = _run(home_yaml, env, site, cache / "weather", seconds, catalog_dir,
                                             reporting_rule)
        header = {**want, "site": shown.as_dict() if shown else None, "start_time": _start_time(shown, env),
                  "weather": weather_ran, "initial_state": initial}
        with tmp.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
                io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(header) + "\n")
            for r in reports:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp, out)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return out


def _header(path: Path) -> dict | None:
    """A trace's header line, or None when the file cannot be read as one."""
    try:
        with gzip.open(path, "rt") as f:
            return json.loads(f.readline())
    except (OSError, EOFError, ValueError):
        return None


def _cached_weather_source(site, cache: Path) -> str | None:
    """The weather source a run of `site` would read from `cache` (None: clear-sky fallback)."""
    from vesper.env.outdoor import Outdoor
    w = Outdoor.for_run(site, cache).weather
    return None if w is None else w.source


def env_site(env: Env):
    """The environment's place (city, date, orientation), or None when it names none."""
    from vesper.env.site import parse_site
    if env.city is None or env.date is None:
        if has_home_site(env):
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
    if all(weather.cached(cache, site.lat, site.lon, d) is not None for d in days):  # pre-pin days: refetched
        return
    weather.fetch(site.lat, site.lon, days[0], days[-1], cache, get=get)


def _run(home_yaml: Path, env: Env, site, weather_cache: Path, seconds: float, catalog_dir: str,
         reporting_rule: str = "catalog"):
    """Live the home `seconds` from local midnight in `env`: (initial state, weather source, reports). The home is
    copied, never touched."""
    from vesper.twins.runtime import reporting_rule as rule_of
    with rung_scaling(env), rule_of(reporting_rule), tempfile.TemporaryDirectory(prefix="edt-trace-") as tmp:
        return _live(copy_home(home_yaml, Path(tmp) / "home", site if has_home_site(env) else None, weather_cache),
                     seconds, catalog_dir)


@contextlib.contextmanager
def rung_scaling(env: Env):
    """Within the block, the model is the rung's: rung 3 scales daylight by 1/14.8; rung 2.5 has the indoor rooms
    read the assumed curve (vesper.lab.rules.INDOOR_ASSUMED); any other rung leaves both untouched."""
    from vesper.env import indoor
    from vesper.lab import rules
    old_scale, old_indoor = indoor.DAYLIGHT_SCALE, rules.INDOOR_ASSUMED
    if env.rung == 3:
        indoor.DAYLIGHT_SCALE = RUNG3_DAYLIGHT_SCALE
    if env.rung == 2.5:
        rules.INDOOR_ASSUMED = True
    try:
        yield
    finally:
        indoor.DAYLIGHT_SCALE, rules.INDOOR_ASSUMED = old_scale, old_indoor


def copy_home(home_yaml: Path, out: Path, site, weather_cache: Path, automations: list[dict] | None = None) -> Path:
    """A copy of the EDT home for one run: the site (or none), midnight start, no residents, and `automations` as the
    home's automations (default none)."""
    shutil.copytree(home_yaml.parent, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    data = yaml.safe_load((out / "home.yaml").read_text())
    data["start_time"] = "00:00"
    data.pop("residents", None)
    data.pop("site", None)
    if site is not None:
        data["site"] = {k: v for k, v in site.as_dict().items() if k != "name"} | {"city": site.name}
    data["automations"] = "automations.yaml"
    (out / "automations.yaml").write_text(yaml.safe_dump(automations or [], sort_keys=False, allow_unicode=True))
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    if site is not None and weather_cache.is_dir():
        shutil.copytree(weather_cache, out / "weather", dirs_exist_ok=True)
    return out / "home.yaml"


def _live(home_yaml: Path, seconds: float, catalog_dir: str):
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    from vesper.twins.home import load_catalog
    home = load_lab_home(home_yaml, load_catalog(Path(catalog_dir)))
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=False, automations=True)
    # the state the environment rules left the twins in, before the first instant passes
    initial = {tid: json.loads(json.dumps(dict(t.state), default=str)) for tid, t in s.runtime.twins.items()}
    weather = (((s.records[0].get("environment") or {}).get("weather")) or {}).get("source")
    try:
        s.clock.advance(seconds)
    finally:
        s.stop()
    reports = [{"t": r["t"], "twin_id": s.twin_ids[r["device"]], "feature": r["feature"], "value": r["value"],
                "observed_t": r["observed_t"]} for r in s.records if r.get("kind") == "report"]
    return initial, weather, reports
