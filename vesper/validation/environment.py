"""Validation of the environment (design 2026-11-03, phase 6): how far VESPER's computed sun is from the reference.

The sun: VESPER's (astral 2.2, as Home Assistant's `sun.sun`) against NREL's Solar Position Algorithm (Reda and
Andreas 2004, uncertainty +-0.0003 degrees), as pvlib implements it (`spa_python`, BSD-3-Clause; the `validation`
extra). Over every city VESPER names (vesper.env.site.CITIES, from the equator to 59 degrees north) on the 2026
solstices and equinoxes: the elevation (refraction included in both, SPA's at its standard 1013.25 hPa and 12
degrees C), the azimuth and the angle on the sky between the two positions every `step_min` minutes while the sun
is up, split at 5 degrees (refraction models part near the horizon), and each day's sunrise and sunset (the upper
limb at -0.833 degrees in both) with a level horizon, as SPA's; apart from that, the dip astral adds for the site's
height (Home Assistant passes its elevation to astral too), which moves sunrise earlier and sunset later.

The reference is a parameter, `reference(site, times) -> ([(elevation, azimuth)], {date: {"sunrise", "sunset"}})`
with sunrise and sunset in seconds after the site's local midnight, so the report is testable without pvlib.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import replace

SUN_DAYS = (dt.date(2026, 3, 20), dt.date(2026, 6, 21), dt.date(2026, 9, 22), dt.date(2026, 12, 21))
SPA = "NREL SPA (Reda & Andreas 2004) via pvlib {version} spa_python"
HORIZON_DEG = -0.8333  # sunrise and sunset: the centre's geometric elevation (34' refraction + 16' semidiameter)


def _stats(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mean_abs": None, "p95_abs": None, "max_abs": None}
    a = sorted(abs(e) for e in errors)
    return {"n": len(a), "mean_abs": round(sum(a) / len(a), 6), "p95_abs": round(a[min(len(a) - 1,
                                                                                    math.ceil(0.95 * len(a)) - 1)], 6),
            "max_abs": round(a[-1], 6)}


def _separation(e1: float, a1: float, e2: float, a2: float) -> float:
    """The angle on the sky between two sun positions, degrees: what shadows and daylight see (an azimuth error
    means little with the sun overhead, where azimuth turns fast)."""
    e1, a1, e2, a2 = map(math.radians, (e1, a1, e2, a2))
    c = math.sin(e1) * math.sin(e2) + math.cos(e1) * math.cos(e2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def spa_reference(site, times):
    """NREL SPA's apparent elevation and azimuth at `times`, and the day's sunrise and sunset: when SPA's geometric
    elevation of the sun's centre crosses HORIZON_DEG (to 0.05 s). pvlib's own sun_rise_set_transit_spa was set
    aside: it differed from these crossings of SPA's own positions by up to about 2 minutes (Seattle, 2026-09-22:
    sunset 19:08:42 against 19:06:38)."""
    import pandas as pd
    from pvlib import solarposition
    idx = pd.DatetimeIndex(times)
    pos = solarposition.spa_python(idx, site.lat, site.lon, altitude=site.elevation_m)
    midnight = dt.datetime.combine(site.date, dt.time(0), tzinfo=site.zone)

    def geometric(when: list[dt.datetime]) -> list[float]:
        return solarposition.spa_python(pd.DatetimeIndex(when), site.lat, site.lon, altitude=0)["elevation"].tolist()

    grid = [midnight + dt.timedelta(minutes=m) for m in range(0, 24 * 60 + 1, 5)]
    els = geometric(grid)
    found: dict[str, float | None] = {"sunrise": None, "sunset": None}
    for k in range(len(grid) - 1):
        rising = els[k] <= HORIZON_DEG < els[k + 1]
        if rising or els[k] > HORIZON_DEG >= els[k + 1]:
            a, b = grid[k], grid[k + 1]
            while (b - a).total_seconds() > 0.05:  # bisection to 0.05 s
                m = a + (b - a) / 2
                if (geometric([m])[0] > HORIZON_DEG) == rising:
                    b = m
                else:
                    a = m
            found["sunrise" if rising else "sunset"] = (a - midnight).total_seconds()
    events = {site.date: found}
    return list(zip(pos["apparent_elevation"].tolist(), pos["azimuth"].tolist(), strict=True)), events


def sun_report(reference=None, step_min: int = 10, cities=None, days=SUN_DAYS) -> dict:
    """VESPER's sun against `reference` (default: NREL SPA by pvlib) over the cities and days."""
    from vesper.env.site import CITIES, parse_site
    from vesper.env.sun import sun_at, sun_events
    name = "a given reference"
    if reference is None:
        import pvlib
        reference, name = spa_reference, SPA.format(version=pvlib.__version__)
    errs = {k: [] for k in ("el0", "el5", "az0", "az5", "sep0", "sep5", "rise", "set", "dip")}
    rows = []
    for city in (cities or CITIES):
        for day in days:
            site = parse_site({"city": city, "date": day.isoformat()})
            midnight = dt.datetime.combine(day, dt.time(0), tzinfo=site.zone)
            times = [midnight + dt.timedelta(minutes=m) for m in range(0, 24 * 60, step_min)]
            ref_pos, ref_events = reference(site, times)
            mine = [sun_at(site.lat, site.lon, site.elevation_m, t) for t in times]
            worst_el = 0.0
            for s, (e, a) in zip(mine, ref_pos, strict=True):
                if e <= 0 and s.elevation <= 0:
                    continue
                de, da = s.elevation - e, (s.azimuth - a + 180) % 360 - 180
                sep = _separation(s.elevation, s.azimuth, e, a)
                errs["el0"].append(de)
                errs["az0"].append(da)
                errs["sep0"].append(sep)
                if e > 5:
                    errs["el5"].append(de)
                    errs["az5"].append(da)
                    errs["sep5"].append(sep)
                    worst_el = max(worst_el, abs(de))
            # the algorithm with a level horizon (as SPA's), and apart from it the dip astral (and so Home Assistant)
            # adds for the site's height: an observer looking down to a sea-level horizon sees the sun earlier
            ev, ev0, ref = sun_events(site, 0), sun_events(replace(site, elevation_m=0.0), 0), ref_events.get(day, {})
            row = {"site": city, "day": day.isoformat(), "elevation_m": site.elevation_m,
                   "max_abs_elevation_above_5": round(worst_el, 6)}
            for key, bucket in (("sunrise", "rise"), ("sunset", "set")):
                if ev0.get(key) is not None and ref.get(key) is not None:
                    errs[bucket].append(ev0[key] - ref[key])
                    row[f"{key}_error_s"] = round(ev0[key] - ref[key], 1)
                    errs["dip"].append(ev[key] - ev0[key])
                    row[f"{key}_dip_s"] = round(ev[key] - ev0[key], 1)
                else:
                    row[f"{key}_error_s"] = None  # no sunrise or sunset that day (or only one of the two has it)
            rows.append(row)
    return {"reference": name, "step_min": step_min, "days": [d.isoformat() for d in days],
            "samples": {"above_0": len(errs["el0"]), "above_5": len(errs["el5"])},
            "elevation_deg": {"above_0": _stats(errs["el0"]), "above_5": _stats(errs["el5"])},
            "azimuth_deg": {"above_0": _stats(errs["az0"]), "above_5": _stats(errs["az5"])},
            "position_deg": {"above_0": _stats(errs["sep0"]), "above_5": _stats(errs["sep5"])},
            "sunrise_s": _stats(errs["rise"]), "sunset_s": _stats(errs["set"]),
            "elevation_dip_s": _stats(errs["dip"]), "sites": rows}


# -- the indoor checks (W, T, H, D of docs/experiments/environment-validation.md) ----------------------------------
APPLIANCES_ROOMS = {"T1": "kitchen", "T2": "living room", "T3": "laundry room", "T4": "office", "T5": "bathroom",
                    "T7": "ironing room", "T8": "teenager room", "T9": "parents' room"}
SML_COLUMNS = ("date time t_dining t_room t_forecast co2_dining co2_room rh_dining rh_room lux_dining lux_room rain "
               "dusk wind sun_west sun_east sun_south irradiance motor1 motor2 motor_turbo t_out rh_out dow").split()
SML_ROOMS = {"t_dining": "dining room", "t_room": "room"}
STANDARD_ROOM = {"w": 4.0, "d": 4.0, "outside_wall_m": 4.0, "window": (1.2, 1.2), "facing": 180}
SETPOINT_PERCENTILE = 10  # the measured room's 10th percentile: the floor its occupants keep (pre-registered)
STEP_S = 600
CRITERIA = {"W": {"bias_c": 1.0, "rmse_c": 2.0, "r": 0.90}, "T": {"rmse_c": 1.5, "within_2c": 0.7},
            "H": {"rmse_pp": 10.0}, "D": {"factor": 2.0}}


def load_appliances(folder):
    """Dataset A, hourly, indexed in UTC (its local timestamps read as Europe/Brussels)."""
    import pandas as pd
    df = pd.read_csv(f"{folder}/energydata_complete.csv", parse_dates=["date"])
    df.index = df.pop("date").dt.tz_localize("Europe/Brussels", ambiguous="NaT", nonexistent="NaT").dt.tz_convert(
        "UTC")
    df = df[df.index.notna()]
    return df.resample("1h").mean()


def load_sml2010(folder):
    """Dataset B, hourly, indexed in UTC (its times are UTC)."""
    import pandas as pd
    parts = [pd.read_csv(f"{folder}/NEW-DATA-{k}.T15.txt", sep=r"\s+", comment="#", header=None, names=SML_COLUMNS)
             for k in (1, 2)]
    df = pd.concat(parts)
    df.index = pd.to_datetime(df.pop("date") + " " + df.pop("time"), format="%d/%m/%Y %H:%M", utc=True)
    return df.sort_index().resample("1h").mean()


def _era5(site, index, cache):
    """ERA5's hourly temperature, humidity and irradiance at the site, on `index` (fetched once, cached)."""
    from vesper.env import weather
    first, last = index[0].date(), index[-1].date()
    weather.fetch(site.lat, site.lon, first, last, cache)
    rec = weather.load(site.lat, site.lon, weather.utc_days(index[0].to_pydatetime(), index[-1].to_pydatetime()),
                       cache)
    rows = [rec.at(t.to_pydatetime()) if rec is not None and rec.covers(t.to_pydatetime()) else {} for t in index]
    return {k: [r.get(k) for r in rows] for k in ("temperature_2m", "relative_humidity_2m", "shortwave_radiation",
                                                  "cloud_cover")}


def _pairs(a, b):
    out = [(x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None and x == x and y == y]
    return [x for x, _ in out], [y for _, y in out]


def _agreement(model, measured) -> dict:
    m, y = _pairs(list(model), list(measured))
    if len(m) < 3:
        return {"n": len(m), "bias": None, "rmse": None, "r": None}
    n = len(m)
    d = [a - b for a, b in zip(m, y, strict=True)]
    mm, my = sum(m) / n, sum(y) / n
    sm = math.sqrt(sum((a - mm) ** 2 for a in m)) or 1e-12
    sy = math.sqrt(sum((b - my) ** 2 for b in y)) or 1e-12
    r = sum((a - mm) * (b - my) for a, b in zip(m, y, strict=True)) / (sm * sy)
    return {"n": n, "bias": round(sum(d) / n, 3), "rmse": round(math.sqrt(sum(x * x for x in d) / n), 3),
            "r": round(r, 4), "within_2": round(sum(abs(x) <= 2 for x in d) / n, 4)}


def weather_check(series, era5_t) -> dict:
    """W: ERA5 against the measured outdoor temperature; the best lag within +-3 h is reported beside lag 0."""
    out = {"lag_0": _agreement(era5_t, series)}
    lags = {}
    for lag in range(-3, 4):
        shifted = (era5_t[lag:] + [None] * lag) if lag >= 0 else ([None] * -lag + era5_t[:lag])
        lags[lag] = _agreement(shifted, series)["rmse"]
    best = min((k for k in lags if lags[k] is not None), key=lambda k: lags[k], default=0)
    out["best_lag_h"], out["rmse_by_lag"] = best, lags
    c = CRITERIA["W"]
    a = out["lag_0"]
    out["pass"] = a["rmse"] is not None and abs(a["bias"]) <= c["bias_c"] and a["rmse"] <= c["rmse_c"] and \
        a["r"] >= c["r"]
    return out


class _MeasuredOutdoor:
    """The outdoors of a validation run: the dataset's temperature and humidity, ERA5's irradiance and cloud, the
    computed sun; hour by hour, held within the hour."""

    def __init__(self, site, start, temp, rh, ghi, cloud):
        self.site, self.start = site, start
        self.temp, self.rh, self.ghi, self.cloud = temp, rh, ghi, cloud
        self._seen: dict[float, object] = {}  # every room and every fitted constant sees the same outdoors

    def at(self, seconds: float):
        if seconds not in self._seen:
            self._seen[seconds] = self._at(seconds)
        return self._seen[seconds]

    def _at(self, seconds: float):
        from vesper.env.outdoor import OutdoorState
        from vesper.env.sun import direct_fraction, lux, sun_at
        k = min(int(seconds // 3600), len(self.temp) - 1)
        when = self.start + dt.timedelta(seconds=seconds)
        s = sun_at(self.site.lat, self.site.lon, self.site.elevation_m, when)
        ghi = (self.ghi[k] or 0.0) if s.up else 0.0
        return OutdoorState(sun=s, ghi_w_m2=ghi, illuminance_lux=lux(ghi), temperature_c=self.temp[k],
                            humidity_pct=self.rh[k], cloud_cover_pct=self.cloud[k],
                            direct_fraction=direct_fraction(self.cloud[k]))


def simulate_room(site, start, temp, rh, ghi, cloud, setpoint: float, outdoor=None) -> tuple[list[float], list[float]]:
    """T and H: the standard room driven by these hourly series, heated to `setpoint` within VESPER's band; its
    hourly mean temperature and humidity. `outdoor`: a _MeasuredOutdoor of the same series, to share."""
    from vesper.env.indoor import HomeEnvironment, RoomModel
    from vesper.lab.rules import HEAT_BAND_C
    s = STANDARD_ROOM
    win = {"id": "w", "room": "r", "width": s["window"][0], "height": s["window"][1], "facing": s["facing"]}
    room = RoomModel("r", s["w"] * s["d"], 2 * (s["w"] + s["d"]),
                     s["outside_wall_m"] * 2.5 - s["window"][0] * s["window"][1], [win])
    room.temperature = setpoint
    outdoor = outdoor or _MeasuredOutdoor(site, start, temp, rh, ghi, cloud)
    env = HomeEnvironment(replace(site, north_deg=0.0), outdoor, {"r": room})
    heating, t_out, h_out = False, [], []
    for hour, value in enumerate(temp):
        if value is None:  # a gap in the record: no value, and the room goes on from where it was
            t_out.append(None)
            h_out.append(None)
            env._t = None
            continue
        ts, hs = [], []
        for k in range(3600 // STEP_S):
            heating = room.temperature < setpoint - HEAT_BAND_C or (
                heating and room.temperature < setpoint + HEAT_BAND_C)
            v = env.step(hour * 3600 + k * STEP_S, {}, {"r": 1.0 if heating else 0.0}, {}, 0)["r"]
            ts.append(room.temperature)
            hs.append(v["humidity"])
        t_out.append(sum(ts) / len(ts))
        h_out.append(sum(hs) / len(hs))
    return t_out, h_out


def _values(series) -> list[float | None]:
    return [None if v != v else float(v) for v in series.tolist()]  # NaN (a gap) is None


def _percentile(values: list[float], q: float) -> float:
    v = sorted(x for x in values if x is not None)
    return v[min(len(v) - 1, max(0, round(q / 100 * (len(v) - 1))))]


def _daily_swing(index, values, tz) -> float | None:
    days: dict = {}
    for t, v in zip(index, values, strict=True):
        if v is not None:
            days.setdefault(t.tz_convert(tz).date(), []).append(v)
    swings = sorted(max(v) - min(v) for v in days.values() if len(v) >= 20)
    return round(swings[len(swings) // 2], 2) if swings else None


def indoor_check(df, site, cache, *, t_out: str, rh_out: str, rooms: dict[str, str], rh_of: dict[str, str]) -> dict:
    """W, T and H on one dataset (hourly, UTC-indexed)."""
    era = _era5(site, df.index, cache)
    out_t, out_rh = _values(df[t_out]), _values(df[rh_out])
    report = {"site": site.name, "hours": len(df), "first": str(df.index[0]), "last": str(df.index[-1]),
              "W": weather_check(out_t, era["temperature_2m"]),
              "W_humidity": _agreement(era["relative_humidity_2m"], out_rh)}
    start = df.index[0].to_pydatetime()
    shared = _MeasuredOutdoor(site, start, out_t, out_rh, era["shortwave_radiation"], era["cloud_cover"])
    rows = []
    for col, label in rooms.items():
        measured = _values(df[col])
        setpoint = _percentile(measured, SETPOINT_PERCENTILE)
        sim_t, sim_h = simulate_room(site, start, out_t, out_rh, era["shortwave_radiation"], era["cloud_cover"],
                                     setpoint, outdoor=shared)
        t = _agreement(sim_t, measured)
        row = {"room": label, "column": col, "setpoint_c": round(setpoint, 2), "temperature": t,
               "swing_measured_c": _daily_swing(df.index, measured, site.zone),
               "swing_simulated_c": _daily_swing(df.index, sim_t, site.zone)}
        if col in rh_of:
            row["humidity"] = _agreement(sim_h, _values(df[rh_of[col]]))
        rows.append(row)
    report["rooms"] = rows
    rmse = sorted(r["temperature"]["rmse"] for r in rows)
    within = sorted(r["temperature"]["within_2"] for r in rows)
    report["T"] = {"median_rmse_c": rmse[len(rmse) // 2], "median_within_2c": within[len(within) // 2]}
    report["T"]["pass"] = report["T"]["median_rmse_c"] <= CRITERIA["T"]["rmse_c"] and \
        report["T"]["median_within_2c"] >= CRITERIA["T"]["within_2c"]
    h = sorted(r["humidity"]["rmse"] for r in rows if "humidity" in r)
    report["H"] = {"median_rmse_pp": h[len(h) // 2] if h else None}
    report["H"]["pass"] = bool(h) and report["H"]["median_rmse_pp"] <= CRITERIA["H"]["rmse_pp"]
    return report


def vesper_daylight_factors(catalog: str = "catalog/vds", seeds=range(1, 21), rooms: int = 6) -> list[float]:
    """D: the average daylight factor (%) VESPER's indoor model gives the living rooms, kitchens and bedrooms of
    generated homes."""
    import tempfile
    from pathlib import Path

    from vesper import cli
    from vesper.env.indoor import REFLECT, SKY_ANGLE, T_GLASS, room_models
    from vesper.lab.furnishing import room_kind
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    from vesper.twins.home import load_catalog
    cat = load_catalog(Path(catalog))
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in seeds:
            req = Path(tmp) / f"r{seed}.yaml"
            req.write_text(f"format: vesper-request/1\nrooms: {rooms}\nseed: {seed}\n")
            home_dir = Path(tmp) / f"h{seed}"
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["lab", "generate", str(home_dir), "--request", str(req), "--catalog", catalog])
            home = load_lab_home(home_dir / "home.yaml", cat)
            models = room_models(home.map, load_windows(home_dir))
            for room in home.map.rooms:
                if room.outdoor or room_kind(room) not in ("living", "kitchen", "bedroom"):
                    continue
                r = models[room.id]
                glass = sum(w["width"] * w["height"] for w in r.windows)
                out.append(T_GLASS * glass * SKY_ANGLE / (r.surface_m2 * (1 - REFLECT ** 2)))
    return out


def daylight_check(df, site, vesper_factors: list[float]) -> dict:
    """D (dataset B): the measured indoor over outdoor horizontal illuminance by day, against VESPER's factors."""
    from vesper.env.sun import LUMINOUS_EFFICACY
    local = df.index.tz_convert(site.zone)
    day = (local.hour >= 10) & (local.hour < 16) & (df["irradiance"] >= 300)
    rows = df[day]
    out = {"hours": int(day.sum())}
    vm = sorted(vesper_factors)[len(vesper_factors) // 2]
    out["vesper_median_pct"] = round(vm, 3)
    out["vesper_range_pct"] = [round(min(vesper_factors), 3), round(max(vesper_factors), 3)]
    for col, label in (("lux_dining", "dining room"), ("lux_room", "room")):
        ratio = sorted((100 * rows[col] / (rows["irradiance"] * LUMINOUS_EFFICACY)).dropna().tolist())
        out[label] = {"median_pct": round(ratio[len(ratio) // 2], 4), "p90_pct": round(ratio[int(0.9 * len(ratio))], 4),
                      "n": len(ratio)}
    measured = sorted(out[k]["median_pct"] for k in ("dining room", "room"))
    med = (measured[0] + measured[1]) / 2
    out["measured_median_pct"] = round(med, 4)
    out["pass"] = vm / CRITERIA["D"]["factor"] <= med <= vm * CRITERIA["D"]["factor"]
    return out


def environment_report(appliances: str | None, sml2010: str | None, cache, catalog: str = "catalog/vds") -> dict:
    """W, T, H and D as docs/experiments/environment-validation.md declares them."""
    from vesper.env.site import parse_site
    report = {"protocol": "docs/experiments/environment-validation.md", "criteria": CRITERIA,
              "standard_room": STANDARD_ROOM, "setpoint_percentile": SETPOINT_PERCENTILE}
    if appliances:
        a = load_appliances(appliances)
        site = parse_site({"city": "Chièvres, Belgium", "date": a.index[0].date().isoformat()})
        report["A"] = indoor_check(a, site, cache, t_out="T_out", rh_out="RH_out", rooms=APPLIANCES_ROOMS,
                                   rh_of={c: "RH_" + c[1:] for c in APPLIANCES_ROOMS})
    if sml2010:
        b = load_sml2010(sml2010)
        site = parse_site({"city": "Valencia, Spain", "date": b.index[0].date().isoformat()})
        report["B"] = indoor_check(b, site, cache, t_out="t_out", rh_out="rh_out", rooms=SML_ROOMS,
                                   rh_of={"t_dining": "rh_dining", "t_room": "rh_room"})
        report["D"] = daylight_check(b, site, vesper_daylight_factors(catalog))
    return report


FIT_ACH = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)  # addendum 1
FIT_SOLAR_TO_AIR = (1.0, 0.75, 0.5, 0.35, 0.25)


def _rooms_with(df, site, cache, spec, ach: float, solar: float) -> dict:
    from vesper.env import indoor
    old = indoor.ACH, indoor.SOLAR_TO_AIR
    indoor.ACH, indoor.SOLAR_TO_AIR = ach, solar
    try:
        r = indoor_check(df, site, cache, **spec)
    finally:
        indoor.ACH, indoor.SOLAR_TO_AIR = old
    return {"ach": ach, "solar_to_air": solar, "T": r["T"], "H": r["H"],
            "rooms": [{"room": x["room"], "temperature": x["temperature"]} for x in r["rooms"]]}


def fit_report(appliances: str, sml2010: str, cache) -> dict:
    """Addendum 1: the 30 pairs on dataset A, the best by T's median RMSE (ties by H's), then once on B."""
    from vesper.env import indoor
    from vesper.env.site import parse_site
    a = load_appliances(appliances)
    site_a = parse_site({"city": "Chièvres, Belgium", "date": a.index[0].date().isoformat()})
    spec_a = {"t_out": "T_out", "rh_out": "RH_out", "rooms": APPLIANCES_ROOMS,
              "rh_of": {c: "RH_" + c[1:] for c in APPLIANCES_ROOMS}}
    grid = [_rooms_with(a, site_a, cache, spec_a, ach, s) for ach in FIT_ACH for s in FIT_SOLAR_TO_AIR]
    best = min(grid, key=lambda g: (g["T"]["median_rmse_c"], g["H"]["median_rmse_pp"]))
    b = load_sml2010(sml2010)
    site_b = parse_site({"city": "Valencia, Spain", "date": b.index[0].date().isoformat()})
    spec_b = {"t_out": "t_out", "rh_out": "rh_out", "rooms": SML_ROOMS,
              "rh_of": {"t_dining": "rh_dining", "t_room": "rh_room"}}
    held_out = _rooms_with(b, site_b, cache, spec_b, best["ach"], best["solar_to_air"])
    baseline_b = _rooms_with(b, site_b, cache, spec_b, indoor.ACH, indoor.SOLAR_TO_AIR)
    return {"protocol": "docs/experiments/environment-validation.md, addendum 1",
            "current": {"ach": indoor.ACH, "solar_to_air": indoor.SOLAR_TO_AIR},
            "grid_on_A": [{k: g[k] for k in ("ach", "solar_to_air", "T", "H")} for g in grid],
            "chosen": {"ach": best["ach"], "solar_to_air": best["solar_to_air"], "A": best},
            "held_out_B": held_out, "baseline_B": baseline_b,
            "unchanged": (best["ach"], best["solar_to_air"]) == (indoor.ACH, indoor.SOLAR_TO_AIR),
            "adopt": (best["ach"], best["solar_to_air"]) != (indoor.ACH, indoor.SOLAR_TO_AIR)
            and held_out["T"]["median_rmse_c"] <= baseline_b["T"]["median_rmse_c"]}
