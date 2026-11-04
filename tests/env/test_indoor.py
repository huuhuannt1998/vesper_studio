"""What reaches each room of a home in a real place: daylight through its windows (BRE average daylight factor and
direct sun), its temperature (one node per room) and humidity."""
import datetime as dt

import pytest

from tests.lab.maps import house
from vesper.env.indoor import HomeEnvironment, e_sat, room_models
from vesper.env.outdoor import Outdoor, OutdoorState
from vesper.env.site import parse_site
from vesper.env.sun import Sun
from vesper.lab.windows import plan_windows

SITE = {"lat": 35.2271, "lon": -80.8431, "elevation_m": 229, "tz": "America/New_York", "date": "2026-01-15"}


def env(north=0):
    site = parse_site({**SITE, "north_deg": north})
    m = house()
    return HomeEnvironment(site, Outdoor(site), room_models(m, plan_windows(m)))


def state(elev=40, az=180, lux=60_000, direct=0.8, temp=5.0):
    return OutdoorState(sun=Sun(elev, az), ghi_w_m2=lux / 110, illuminance_lux=lux, temperature_c=temp,
                        humidity_pct=60, cloud_cover_pct=None, direct_fraction=direct)


def test_rooms_have_their_floor_walls_and_windows():
    rooms = env().rooms
    assert set(rooms) == {"hall", "kitchen", "bedroom"}
    k = rooms["kitchen"]
    assert k.floor_m2 == pytest.approx(3.9 * 2.9) and len(k.windows) == 1
    assert rooms["hall"].windows == [] and rooms["kitchen"].outside_wall_m2 > 0


def test_a_room_without_windows_gets_no_daylight_and_a_room_with_one_gets_the_sky_s_share():
    e = env()
    overcast = state(direct=0.0, lux=20_000)
    assert e.daylight("hall", overcast) == 0
    lux = e.daylight("kitchen", overcast)
    assert 100 < lux < 600  # a 1 m2 window in an 11 m2 room: a daylight factor near 1-2 %


def test_direct_sun_comes_in_only_through_a_window_the_sun_faces():
    e = env()  # the kitchen's window faces north (the plan's top), the bedroom's south
    south_sun = state(elev=30, az=180)
    assert e.daylight("bedroom", south_sun) > 3 * e.daylight("kitchen", south_sun)
    turned = env(north=180)  # the house turned round: now the kitchen faces south
    assert turned.daylight("kitchen", south_sun) > turned.daylight("bedroom", south_sun)


def test_a_closed_shade_cuts_the_light():
    e = env()
    s = state(elev=30, az=180)
    before = e.daylight("bedroom", s)
    for w in e.rooms["bedroom"].windows:
        e.set_shade(w["id"], False)
    assert e.daylight("bedroom", s) < 0.2 * before


def test_a_room_cools_toward_the_cold_outside_and_heating_warms_it():
    cold, heated = env(), env()
    for t in range(0, 6 * 3600 + 1, 60):  # a January night, 02:00-08:00
        cold.step(2 * 3600 + t, {}, {}, {}, 200)
        heated.step(2 * 3600 + t, {}, {"bedroom": 1.0}, {}, 200)
    assert cold.rooms["bedroom"].temperature < 20.5
    assert heated.rooms["bedroom"].temperature > cold.rooms["bedroom"].temperature + 2


def test_step_gives_each_room_its_values_with_lamps_and_bounded_humidity():
    e = env()
    out = e.step(23 * 3600, {"hall": 2}, {}, {"hall": 1}, 200)
    assert out["hall"]["illuminance"] == 400  # night: two lamps only
    assert all(15 <= v["humidity"] <= 85 for v in out.values())
    assert set(out) == {"hall", "kitchen", "bedroom"}


def test_saturation_vapour_pressure_is_the_textbook_value():
    assert e_sat(20) == pytest.approx(2.338, abs=0.01)
    assert e_sat(0) == pytest.approx(0.6108, abs=1e-4)


def test_provenance_names_the_assumed_constants():
    p = env().provenance()
    assert p["indoor"]["T_glass"] == 0.68 and "BRE" in p["indoor"]["daylight"] and p["site"]["tz"] == SITE["tz"]
    assert p["indoor"]["windows"] == 2


def test_the_sun_s_site_day_is_the_site_s_date():
    e = env()
    assert e.site.utc(0) == dt.datetime(2026, 1, 15, 5, 0, tzinfo=dt.UTC)
