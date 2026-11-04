"""Phase 6 of the environment design (2026-11-03): VESPER's sun against NREL's Solar Position Algorithm (SPA), the
reference the sun's error is reported against. The reference is passed in, so the report's arithmetic is tested
without pvlib; the real comparison runs when pvlib is installed (the `validation` extra)."""
import datetime as dt

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
