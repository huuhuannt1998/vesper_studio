"""A home's site (place, time zone, orientation, first day) and the sun over it, as Home Assistant computes it."""
import datetime as dt
import math

import pytest

from vesper.env.site import CITIES, SiteError, parse_site
from vesper.env.sun import clear_sky_ghi, direct_fraction, lux, sun_at

CHARLOTTE = {"lat": 35.2271, "lon": -80.8431, "elevation_m": 229, "tz": "America/New_York", "date": "2026-06-21"}


def test_a_site_turns_a_run_s_seconds_into_local_and_utc_time_across_dst():
    s = parse_site(CHARLOTTE)
    assert s.local(12 * 3600).isoformat() == "2026-06-21T12:00:00-04:00"
    assert s.utc(12 * 3600).isoformat() == "2026-06-21T16:00:00+00:00"
    autumn = parse_site({**CHARLOTTE, "date": "2026-11-01"})  # clocks go back at 02:00 that night
    assert autumn.local(3 * 3600).hour == 2  # three real hours after midnight is 02:00 again


def test_a_city_name_stands_for_its_coordinates_and_its_values_can_be_overridden():
    s = parse_site({"city": "Phoenix, AZ, USA", "date": "2026-01-15", "north_deg": 90})
    assert (s.lat, s.tz, s.name, s.north_deg) == (CITIES["Phoenix, AZ, USA"]["lat"], "America/Phoenix",
                                                  "Phoenix, AZ, USA", 90)
    assert s.bearing(90) == 180  # the plan's right faces south when its top faces east


@pytest.mark.parametrize("bad, words", [({**CHARLOTTE, "lat": 95}, "site.lat"), ({**CHARLOTTE, "tz": "Mars/Base"},
                                         "IANA"), ({**CHARLOTTE, "date": "21/06/2026"}, "YYYY-MM-DD"),
                                        ({"city": "Atlantis", "date": "2026-01-01"}, "Atlantis"),
                                        ({**CHARLOTTE, "date": None}, "site.date")])
def test_a_site_that_cannot_be_a_place_says_which_field(bad, words):
    with pytest.raises(SiteError, match=words):
        parse_site(bad)


def test_no_site_keeps_the_old_assumed_curve():
    assert parse_site(None) is None


def test_the_sun_at_solar_noon_on_the_june_solstice_stands_at_90_minus_latitude_plus_the_tilt():
    """Charlotte's solar noon on 2026-06-21 is about 13:25 EDT (longitude 80.84 W, equation of time -1.7 min);
    the noon elevation is 90 - 35.23 + 23.44 = 78.2 degrees, due south."""
    s = parse_site(CHARLOTTE)
    best = max((sun_at(s.lat, s.lon, s.elevation_m, s.utc(13 * 3600 + m * 60)) for m in range(0, 60)),
               key=lambda x: x.elevation)
    assert best.elevation == pytest.approx(78.2, abs=0.3)
    assert best.azimuth == pytest.approx(180, abs=3)


def test_the_sun_is_down_at_midnight_and_rises_in_the_east():
    s = parse_site(CHARLOTTE)
    assert not sun_at(s.lat, s.lon, 0, s.utc(0)).up
    dawn = next(sun_at(s.lat, s.lon, 0, s.utc(t)) for t in range(4 * 3600, 9 * 3600, 60)
                if sun_at(s.lat, s.lon, 0, s.utc(t)).up)
    assert 50 < dawn.azimuth < 70  # midsummer sunrise north of east


def test_the_sun_vector_points_where_the_angles_say():
    s = sun_at(0, 0, 0, dt.datetime(2026, 3, 20, 12, 7, tzinfo=dt.UTC))  # equinox noon on the equator
    east, up, north = s.vector()
    assert up == pytest.approx(math.sin(math.radians(s.elevation)), abs=1e-9) and up > 0.99


def test_clear_sky_irradiance_and_daylight_follow_the_sun():
    assert clear_sky_ghi(-5) == 0 and clear_sky_ghi(0) == 0
    assert 1000 < clear_sky_ghi(90) < 1050  # Haurwitz: 1098 exp(-0.057) = 1037
    assert clear_sky_ghi(30) < clear_sky_ghi(60)
    assert lux(1000) == 110_000 and lux(-3) == 0
    assert direct_fraction(None) == 0.8 and direct_fraction(100) == 0
