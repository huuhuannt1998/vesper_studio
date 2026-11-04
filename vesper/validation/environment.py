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
