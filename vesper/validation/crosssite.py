"""RQ3 (environment design 2026-11-03): does a home's place change what its sensors report and its automations do?

One generated home (six rooms, seed 7), the same devices (plus a motion-and-light sensor in the living room and in
the office), the same automations and no residents, lives three logical days from midnight in each city x date x
plan orientation, and once with no site (the assumed indoor curve every home got before). The third day is measured
(WARMUP_DAYS before it: the rooms' thermal mass forgets their start): the minutes the heating runs, each room's
temperature range, the daytime minutes (07:00-22:00) the living room is below DARK_LUX, when each automation first
fires, and the outdoors. Weather is the recorded ERA5 (fetched once into the cache); a day not fetched falls back to
clear sky and the assumed temperature curve, and the run says so.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import yaml

CITIES = ("Phoenix, AZ, USA", "Seattle, WA, USA", "Minneapolis, MN, USA", "Stockholm, Sweden", "Singapore",
          "Sydney, Australia")
DATES = ("2026-01-15", "2026-07-15")
NORTHS = (0.0, 180.0)
HVACS = {  # the hallway thermostat: the starter's radiator thermostat, or a heat pump's that also cools
    "radiator": None,
    "heat pump": {"product": "zigbee:leviton:rc-2000wh",
                  "state": {"system_mode": "auto", "occupied_heating_setpoint": 20, "occupied_cooling_setpoint": 24}},
}
DARK_LUX = 50.0
WARMUP_DAYS = 2  # a room's thermal mass takes days to forget its start (20.5 C): measure the third day
EXTRA_DEVICES = [  # a motion sensor that also reports light and temperature (Philips Hue indoor motion sensor)
    {"product": "zigbee:philips:9290012607", "name": "living_sensor",
     "at": {"room": "living", "mount": "ceiling", "xy": [3.0, 1.9]}},
    {"product": "zigbee:philips:9290012607", "name": "office_sensor",
     "at": {"room": "office", "mount": "ceiling", "xy": [10.0, 7.0]}},
]
RULES = [  # (needs the sun, rule)
    (False, """- alias: living lamp when it gets dark
  triggers: [{{trigger: numeric_state, entity_id: {lux}, below: {dark}}}]
  conditions: [{{condition: time, after: "07:00", before: "22:00"}}]
  actions: [{{action: light.turn_on, target: {{entity_id: {living}}}}}]
"""),
    (False, """- alias: living lamp off when it is light
  triggers: [{{trigger: numeric_state, entity_id: {lux}, above: {light}}}]
  actions: [{{action: light.turn_off, target: {{entity_id: {living}}}}}]
"""),
    (True, """- alias: bedroom lamp half an hour before sunset
  triggers: [{{trigger: sun, event: sunset, offset: "-00:30:00"}}]
  actions: [{{action: light.turn_on, target: {{entity_id: {bedroom}}}}}]
"""),
    (False, """- alias: every lamp off at night
  triggers: [{{trigger: time, at: "23:30"}}]
  actions: [{{action: light.turn_off, target: {{entity_id: [{living}, {bedroom}]}}}}]
"""),
]


def prepare(base: Path, out: Path, site: dict | None, catalog, hvac: str = "radiator") -> Path:
    """A copy of the base home for one condition: its site (or none), midnight start, the extra sensors and the
    automations; returns its home.yaml."""
    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    data = yaml.safe_load((out / "home.yaml").read_text())
    data["start_time"] = "00:00"
    data.pop("residents", None)
    data["devices"] = [d for d in data["devices"] if d["name"] not in {x["name"] for x in EXTRA_DEVICES}]
    data["devices"] += EXTRA_DEVICES
    if HVACS[hvac]:
        for d in data["devices"]:
            if d["name"] == "corridor_thermostat":
                d["product"], d["state"] = HVACS[hvac]["product"], dict(HVACS[hvac]["state"])
    if site is None:
        data.pop("site", None)
    else:
        data["site"] = site
    data["automations"] = "automations.yaml"
    (out / "automations.yaml").write_text("[]\n")
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    ids = entity_ids(load_lab_home(out / "home.yaml", catalog))
    names = {"lux": ids["living_sensor"]["illuminance"], "living": ids["living_light"]["state"],
             "bedroom": ids["bedroom_1_light"]["state"], "dark": DARK_LUX, "light": 3 * DARK_LUX}
    rules = "".join(text.format(**names) for sun, text in RULES if site is not None or not sun)  # no site: no sun
    (out / "automations.yaml").write_text(rules)
    return out / "home.yaml"


def run_day(home_yaml: Path, catalog) -> dict:
    """One logical day of the home: the measures above, from its records."""
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    home = load_lab_home(home_yaml, catalog)
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=False, automations=True)
    s.clock.advance((WARMUP_DAYS + 1) * 86400 - 1)
    t0 = WARMUP_DAYS * 86400
    recs = [r for r in s.records[1:] if r.get("t", 0) >= t0]
    meta = s.records[0]
    env = [r for r in recs if r.get("rule") == "environment"]
    reports = [r for r in recs if r.get("kind") == "report" and r.get("device") == "living_sensor"
               and r.get("feature") == "illuminance"]
    fired: dict[str, float] = {}
    for r in recs:
        if r.get("kind") == "automation" and r.get("accepted", True):
            fired.setdefault(r["automation"], r["t"] - t0)
    out = {"site": ((meta.get("environment") or {}).get("site") or {}).get("name"),
           "refused": s.automations.refused,
           "automations_first_fired_h": {k: round(v / 3600, 2) for k, v in sorted(fired.items(), key=lambda x: x[1])}}
    if env:
        heat = sum(1 for r in env if r["heating"])
        rooms = {rid: [r["rooms"][rid]["temperature"] for r in env] for rid in env[0]["rooms"]}
        out["heating_min"] = heat  # one record a minute
        out["cooling_min"] = sum(1 for r in env if r.get("cooling"))
        out["rooms_c"] = {rid: {"min": min(v), "max": max(v), "mean": round(sum(v) / len(v), 2)}
                          for rid, v in rooms.items()}
        day = [r for r in env if 7 * 3600 <= r["t"] - t0 < 22 * 3600]
        out["living_dark_min"] = sum(1 for r in day if r["rooms"]["living"]["illuminance"] < DARK_LUX)
        out["outdoor"] = {"min_c": min(r["outdoor"]["temperature_c"] for r in env),
                          "max_c": max(r["outdoor"]["temperature_c"] for r in env),
                          "max_sun_el": max(r["outdoor"]["sun_elevation"] for r in env)}
        out["weather"] = ((meta.get("environment") or {}).get("weather") or {}).get("source", "none: assumed")
    else:  # no site: the assumed curve; the living sensor's reports are what the automations saw
        day = [r for r in reports if 7 * 3600 <= r["t"] - t0 < 22 * 3600]
        out["heating_min"] = None
        out["living_lux_reports"] = len(reports)
        out["living_dark_min"] = None if not day else "see reports"
    return out


def base_home(work: Path, catalog_dir: str) -> Path:
    """The experiment's home: `vesper lab generate`, six rooms, seed 7, its starter devices."""
    import contextlib
    import io

    from vesper import cli
    out = work / "base"
    if not (out / "home.yaml").is_file():
        work.mkdir(parents=True, exist_ok=True)
        (work / "request.yaml").write_text("format: vesper-request/1\nrooms: 6\nseed: 7\n")
        with contextlib.redirect_stdout(io.StringIO()):
            if cli.main(["lab", "generate", str(out), "--request", str(work / "request.yaml"), "--catalog",
                         catalog_dir]) != 0:
                raise RuntimeError("vesper lab generate failed for the cross-site home")
    return out


def crosssite_report(base: Path | None, work: Path, catalog_dir: str = "catalog/vds", fetch: bool = True,
                     cities=CITIES, dates=DATES, norths=NORTHS, hvacs=tuple(HVACS)) -> dict:
    """Every condition, and the home without a site. `base`: the home (default: base_home)."""
    base = base or base_home(work, catalog_dir)
    from vesper.env import weather
    from vesper.env.outdoor import RUN_DAYS
    from vesper.env.site import parse_site
    from vesper.twins.home import load_catalog
    catalog = load_catalog(Path(catalog_dir))
    work.mkdir(parents=True, exist_ok=True)
    runs = []
    baseline = prepare(base, work / "no-site", None, catalog)
    runs.append({"city": None, "date": None, "north_deg": None, "hvac": "radiator",
                 **run_day(baseline, catalog)})
    for city in cities:
        for date in dates:
            site = parse_site({"city": city, "date": date})
            cache = work / "weather"
            if fetch:
                days = weather.utc_days(site.utc(0), site.utc(RUN_DAYS * 86400))
                weather.fetch(site.lat, site.lon, days[0], days[-1], cache)
            for north in norths:
                for hvac in hvacs:
                    place = city.split(",")[0].lower().replace(" ", "-")
                    name = f"{place}-{date}-n{int(north)}-{hvac.replace(' ', '-')}"
                    home_yaml = prepare(base, work / name, {"city": city, "date": date, "north_deg": north}, catalog,
                                        hvac)
                    if cache.exists():
                        shutil.copytree(cache, work / name / "weather", dirs_exist_ok=True)
                    measured = (dt.date.fromisoformat(date) + dt.timedelta(days=WARMUP_DAYS)).isoformat()
                    runs.append({"city": city, "date": date, "measured_date": measured, "north_deg": north,
                                 "hvac": hvac, **run_day(home_yaml, catalog)})
    return {"design": __doc__.split("\n\n")[1].replace("\n", " "), "dark_lux": DARK_LUX,
            "cities": list(cities), "dates": list(dates), "norths": list(norths),
            "hvacs": {k: v for k, v in HVACS.items() if k in hvacs},
            "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "runs": runs}
