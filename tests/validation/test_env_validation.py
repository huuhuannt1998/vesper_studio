"""Phase 6 of the environment design (2026-11-03): VESPER's sun against NREL's Solar Position Algorithm (SPA), the
reference the sun's error is reported against. The reference is passed in, so the report's arithmetic is tested
without pvlib; the real comparison runs when pvlib is installed (the `validation` extra)."""
import datetime as dt
import math

import pytest

from vesper.env.site import CITIES
from vesper.validation.environment import SUN_DAYS, sun_report


def exact(site, times):
    """A reference that agrees with VESPER exactly: VESPER's own sun."""
    from dataclasses import replace

    from vesper.env.sun import sun_at, sun_events
    pos = [sun_at(site.lat, site.lon, site.elevation_m, t) for t in times]
    level = replace(site, elevation_m=0.0)  # a reference's sunrise and sunset are for a level horizon, as SPA's
    return [(p.elevation, p.azimuth) for p in pos], {site.date: sun_events(level, 0)}


def test_the_report_covers_every_city_and_day_and_is_zero_against_itself():
    r = sun_report(reference=exact, step_min=60)
    assert {row["site"] for row in r["sites"]} == set(CITIES) and r["days"] == [d.isoformat() for d in SUN_DAYS]
    assert r["elevation_deg"]["above_5"]["max_abs"] == 0 and r["azimuth_deg"]["above_5"]["max_abs"] == 0
    assert r["position_deg"]["above_5"]["max_abs"] < 1e-4  # acos near 1: float noise
    assert r["sunrise_s"]["max_abs"] == 0 and r["samples"]["above_5"] > 0


def test_an_offset_reference_shows_as_that_error():
    def off(site, times):
        pos, events = exact(site, times)
        return [(e + 0.1, (a + 0.2) % 360) for e, a in pos], {d: {k: (v + 30 if v is not None else None)
                                                                  for k, v in ev.items()} for d, ev in events.items()}
    r = sun_report(reference=off, step_min=60)
    assert r["elevation_deg"]["above_5"]["max_abs"] == pytest.approx(0.1, abs=1e-6)
    assert r["azimuth_deg"]["above_5"]["max_abs"] == pytest.approx(0.2, abs=1e-6)
    assert r["sunrise_s"]["max_abs"] == pytest.approx(30, abs=1e-6)


def test_vesper_s_sun_against_nrel_spa():
    pytest.importorskip("pvlib")
    r = sun_report(step_min=30)
    assert r["reference"].startswith("NREL SPA")
    assert r["elevation_deg"]["above_5"]["max_abs"] < 0.05 and r["position_deg"]["above_5"]["max_abs"] < 0.05
    assert r["sunrise_s"]["max_abs"] < 60 and r["sunset_s"]["max_abs"] < 60  # 34 s at most on 2026-11-04
    assert r["elevation_dip_s"]["max_abs"] > 60  # the site's height (as Home Assistant's): reported apart
    assert dt.date(2026, 6, 21) in SUN_DAYS


def test_validate_sun_writes_its_report(tmp_path, capsys):
    pytest.importorskip("pvlib")
    from vesper import cli
    out = tmp_path / "sun.json"
    assert cli.main(["validate", "sun", "--step", "60", "--out", str(out)]) == 0
    import json
    data = json.loads(out.read_text())
    assert data["reference"].startswith("NREL SPA") and "run" in data
    assert "sun: position within" in capsys.readouterr().out


def test_agreement_and_the_weather_lag():
    from vesper.validation.environment import _agreement, weather_check
    a = _agreement([1.0, 2.0, 3.0, None], [1.5, 2.5, 3.5, 9.0])
    assert (a["n"], a["bias"], a["rmse"], a["r"]) == (3, -0.5, 0.5, 1.0)
    measured = [math.sin(k / 4) * 5 for k in range(200)]
    era = measured[2:] + [None, None]  # ERA5 two hours ahead of the station
    w = weather_check(measured, era)
    assert w["best_lag_h"] == -2 and w["rmse_by_lag"][-2] == 0.0


def test_the_heated_room_holds_its_setpoint_and_the_unheated_cools_to_the_outdoors():
    from vesper.env.site import parse_site
    from vesper.validation.environment import simulate_room
    site = parse_site({"city": "Chièvres, Belgium", "date": "2016-01-15"})
    start = dt.datetime(2016, 1, 15, tzinfo=dt.UTC)
    n = 96
    t, h = simulate_room(site, start, [2.0] * n, [80.0] * n, [0.0] * n, [100.0] * n, setpoint=20.0)
    assert all(19.4 < x < 20.6 for x in t[1:]) and all(15 <= x <= 85 for x in h)
    cold, _ = simulate_room(site, start, [2.0] * n, [80.0] * n, [0.0] * n, [100.0] * n, setpoint=-50.0)
    assert abs(cold[-1] - 2.0) < 0.5 * abs(cold[0] - 2.0)  # unheated (it starts at its setpoint): toward the outdoors
    gap, _ = simulate_room(site, start, [2.0, None, 2.0], [80.0] * 3, [0.0] * 3, [100.0] * 3, setpoint=20.0)
    assert gap[1] is None and gap[2] is not None


def test_the_datasets_are_read_hourly_in_utc(tmp_path):
    from vesper.validation.environment import load_appliances, load_sml2010
    (tmp_path / "energydata_complete.csv").write_text(
        '"date","T1","T_out"\n"2016-01-11 17:00:00","20","5"\n"2016-01-11 17:10:00","22","7"\n')
    a = load_appliances(tmp_path)
    assert str(a.index[0]) == "2016-01-11 16:00:00+00:00" and a["T1"].iloc[0] == 21  # Brussels in winter: UTC+1
    row = "13/03/2012 11:45 " + " ".join(["1"] * 22) + "\n"
    for k in (1, 2):
        (tmp_path / f"NEW-DATA-{k}.T15.txt").write_text("# header\n" + row)
    b = load_sml2010(tmp_path)
    assert str(b.index[0]) == "2012-03-13 11:00:00+00:00"
