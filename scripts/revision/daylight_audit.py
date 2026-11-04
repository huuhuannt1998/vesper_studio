#!/usr/bin/env python3
"""daylight_audit.py -- VESPER revision R-A: is the indoor daylight model's daylight factor right?

Part 1 (always): for every indoor room of the standard home (the cross-site and EDT home: `vesper lab generate`, six
rooms, seed 7), the inputs the model uses (glazed area W, glass transmittance T, visible sky angle theta, total
internal surface area A = 2 x floor + perimeter x ceiling height, mean reflectance R) and the BRE/CIBSE average
daylight factor DF = W T theta / (A (1 - R^2)) computed here by hand from the room's box, against the model's own
output: 100 x the room's daylight (vesper.env.indoor.HomeEnvironment.daylight) over a 10,000 lux overcast sky
(direct fraction 0). Also the generated-home median the validation's check D uses (vesper_daylight_factors).

Part 2 (with dataset B and its ERA5 cache): check D's hours (10:00-16:00 local, measured irradiance >= 300 W/m2) as
the dataset measures them, the room's illuminance over the outdoor global horizontal illuminance, but for VESPER's
standard-home rooms (Valencia, as built): with the sun (the model's room average) and the sky alone. A daylight
factor is defined under an overcast sky; D's hours are mostly sunny, where the sky is a fifth of the global light.
Post hoc and descriptive (not the pre-registered D, which stands).

Run (from the repository root):
    PYTHONPATH=. python scripts/revision/daylight_audit.py [--sml2010 .vesper/datasets/uci-sml2010
        --weather-cache .vesper/datasets/era5] [--out results/environment/daylight-audit-<date>.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

OVERCAST_LUX = 10_000.0


def standard_home(catalog: str, work: Path):
    from vesper.env.indoor import room_models
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    from vesper.twins.home import load_catalog
    from vesper.validation.crosssite import base_home
    base = base_home(work, catalog)
    home = load_lab_home(base / "home.yaml", load_catalog(Path(catalog)))
    return home, room_models(home.map, load_windows(base))


def part1(home, models) -> list[dict]:
    from vesper.env import sun as sunmod
    from vesper.env.indoor import CEILING_M, REFLECT, SKY_ANGLE, T_GLASS, HomeEnvironment
    from vesper.env.outdoor import OutdoorState
    from vesper.env.site import parse_site
    from vesper.lab.furnishing import room_kind
    from vesper.lab.windows import room_box
    site = parse_site({"city": "Valencia, Spain", "date": "2012-04-15"})
    env = HomeEnvironment(site, None, models)
    overcast = OutdoorState(sun=sunmod.Sun(30.0, 180.0), ghi_w_m2=OVERCAST_LUX / sunmod.LUMINOUS_EFFICACY,
                            illuminance_lux=OVERCAST_LUX, temperature_c=10.0, humidity_pct=80.0,
                            cloud_cover_pct=100.0, direct_fraction=0.0)
    rows = []
    for room in home.map.rooms:
        if room.outdoor:
            continue
        x0, z0, x1, z1 = room_box(room)
        w, d = x1 - x0, z1 - z0
        r = models[room.id]
        glazed = sum(x["width"] * x["height"] for x in r.windows)
        area = 2 * w * d + 2 * (w + d) * CEILING_M  # by hand, not RoomModel.surface_m2
        df = T_GLASS * glazed * SKY_ANGLE / (area * (1 - REFLECT ** 2)) if glazed else 0.0
        model = 100 * env.daylight(room.id, overcast) / OVERCAST_LUX
        rows.append({"room": room.id, "kind": room_kind(room), "w_m": round(w, 2), "d_m": round(d, 2),
                     "floor_m2": round(w * d, 2), "glazed_m2": round(glazed, 3),
                     "glazing_to_floor_pct": round(100 * glazed / (w * d), 1), "T": T_GLASS, "theta_deg": SKY_ANGLE,
                     "R": REFLECT, "A_m2": round(area, 2), "A_model_m2": round(r.surface_m2, 2),
                     "df_formula_pct": round(df, 4), "df_model_pct": round(model, 4),
                     "ratio": round(model / df, 6) if df else None,
                     "windows": [{"w": x["width"], "h": x["height"], "facing": x["facing"]} for x in r.windows]})
    return rows


def part2(home, models, sml2010: str, cache: Path) -> dict:
    from vesper.env import sun as sunmod
    from vesper.env import weather
    from vesper.env.indoor import HomeEnvironment
    from vesper.env.outdoor import OutdoorState
    from vesper.env.site import parse_site
    from vesper.lab.furnishing import room_kind
    from vesper.validation.environment import load_sml2010
    b = load_sml2010(sml2010)
    site = parse_site({"city": "Valencia, Spain", "date": b.index[0].date().isoformat()})
    local = b.index.tz_convert(site.zone)
    hours = b[(local.hour >= 10) & (local.hour < 16) & (b["irradiance"] >= 300)]
    days = weather.utc_days(hours.index[0].to_pydatetime(), hours.index[-1].to_pydatetime())
    if any(weather.cached(cache, site.lat, site.lon, d) is None for d in days):
        weather.fetch(site.lat, site.lon, days[0], days[-1], cache)
    rec = weather.load(site.lat, site.lon, days, cache)
    env = HomeEnvironment(site, None, models)
    habitable = [r.id for r in home.map.rooms if not r.outdoor and room_kind(r) in ("living", "kitchen", "bedroom")]
    per_room = {rid: {"with_sun": [], "sky_only": []} for rid in habitable}
    clouds = []
    for t, row in hours.iterrows():
        when = t.to_pydatetime()
        cloud = rec.at(when).get("cloud_cover") if rec is not None and rec.covers(when) else None
        clouds.append(cloud)
        df_dir = sunmod.direct_fraction(cloud)
        s = sunmod.sun_at(site.lat, site.lon, site.elevation_m, when)
        lux = sunmod.lux(row["irradiance"])
        st = OutdoorState(sun=s, ghi_w_m2=row["irradiance"], illuminance_lux=lux, temperature_c=15.0,
                          humidity_pct=60.0, cloud_cover_pct=cloud, direct_fraction=df_dir)
        for rid in habitable:
            total = env.daylight(rid, st)
            sky = env.daylight(rid, OutdoorState(sun=sunmod.Sun(-10.0, 0.0), ghi_w_m2=st.ghi_w_m2,
                                                 illuminance_lux=lux * (1 - df_dir), temperature_c=15.0,
                                                 humidity_pct=60.0, cloud_cover_pct=cloud, direct_fraction=0.0))
            per_room[rid]["with_sun"].append(100 * total / lux)
            per_room[rid]["sky_only"].append(100 * sky / lux)
    known = [c for c in clouds if c is not None]
    return {"hours": len(hours), "weather_source": None if rec is None else rec.source,
            "cloud_cover_median_pct": statistics.median(known) if known else None,
            "direct_fraction_median": statistics.median(sunmod.direct_fraction(c) for c in clouds),
            "rooms": {rid: {k: round(statistics.median(v), 4) for k, v in x.items()} for rid, x in per_room.items()},
            "median_over_rooms": {k: round(statistics.median(statistics.median(x[k]) for x in per_room.values()), 4)
                                  for k in ("with_sun", "sky_only")}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--sml2010", default=None)
    ap.add_argument("--weather-cache", default=".vesper/datasets/era5")
    ap.add_argument("--ensemble", action="store_true", help="also the 20 generated homes' median (check D's)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="daylight-audit-") as tmp:
        home, models = standard_home(a.catalog, Path(tmp))
        report = {"part1_overcast": part1(home, models)}
        if a.ensemble:
            from vesper.validation.environment import vesper_daylight_factors
            f = vesper_daylight_factors(a.catalog)
            report["ensemble_check_D"] = {"n_rooms": len(f), "median_pct": round(sorted(f)[len(f) // 2], 4),
                                          "range_pct": [round(min(f), 4), round(max(f), 4)]}
        if a.sml2010:
            report["part2_dataset_B_hours"] = part2(home, models, a.sml2010, Path(a.weather_cache))
    print("room | W m2 | W/floor % | T | theta | R | A m2 | DF formula % | VESPER model %")
    for r in report["part1_overcast"]:
        print(f"{r['room']} | {r['glazed_m2']} | {r['glazing_to_floor_pct']} | {r['T']} | {r['theta_deg']} | "
              f"{r['R']} | {r['A_m2']} | {r['df_formula_pct']:.3f} | {r['df_model_pct']:.3f}")
    worst = max((abs(r["ratio"] - 1) for r in report["part1_overcast"] if r["ratio"]), default=0.0)
    print(f"largest relative difference model vs formula: {worst:.2e}")
    for key in ("ensemble_check_D", "part2_dataset_B_hours"):
        if key in report:
            print(key, json.dumps(report[key], indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
