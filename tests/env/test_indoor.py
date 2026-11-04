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


def test_only_solar_to_air_of_the_sun_through_the_glass_warms_the_room(monkeypatch):
    from vesper.env import indoor
    e = env()
    full = e._solar_w(e.rooms["bedroom"], state())
    assert indoor.SOLAR_TO_AIR == 1.0 and full > 0  # the default: all of it (the first validation run's model)
    monkeypatch.setattr(indoor, "SOLAR_TO_AIR", 0.25)
    assert e._solar_w(e.rooms["bedroom"], state()) == pytest.approx(0.25 * full)


def test_rooms_know_the_walls_they_share_with_their_neighbours():
    rooms = env().rooms
    assert rooms["kitchen"].neighbours == pytest.approx({"hall": 2.9 * 2.5, "bedroom": 3.9 * 2.5})
    assert rooms["hall"].neighbours == pytest.approx({"kitchen": 2.9 * 2.5, "bedroom": 2.9 * 2.5})
    assert "porch" not in rooms["hall"].neighbours  # an outdoor space is not a room's neighbour


def test_heat_passes_through_an_interior_wall_from_the_warm_room_to_the_cool_one():
    """A sunny or heated room shares its heat with the rooms beside it (found 2026-11-04: with rooms isolated, a
    small south kitchen reached 28 degrees in a Seattle January)."""
    e = env()
    for t in range(0, 3 * 3600 + 1, 60):
        e.step(2 * 3600 + t, {}, {"bedroom": 1.0}, {}, 200)
    alone = env()
    for r in alone.rooms.values():
        r.neighbours = {}
    for t in range(0, 3 * 3600 + 1, 60):
        alone.step(2 * 3600 + t, {}, {"bedroom": 1.0}, {}, 200)
    assert e.rooms["bedroom"].temperature < alone.rooms["bedroom"].temperature  # it gave heat away
    assert e.rooms["kitchen"].temperature > alone.rooms["kitchen"].temperature  # and its neighbour took it


def test_a_humidity_class_adds_moisture_in_cold_weather_and_none_in_warm(monkeypatch):
    """EN ISO 13788's internal humidity classes: 270 Pa per class at or below 0 degrees outside, none at 20."""
    from vesper.env import indoor
    assert indoor.HUMIDITY_CLASS == 1  # fitted on dataset A, held out on B (addendum 2)
    monkeypatch.setattr(indoor, "HUMIDITY_CLASS", 0)
    assert indoor.moisture_excess_kpa(-5.0) == 0.0  # the first run's model
    monkeypatch.setattr(indoor, "HUMIDITY_CLASS", 3)
    assert indoor.moisture_excess_kpa(-5.0) == pytest.approx(0.81)
    assert indoor.moisture_excess_kpa(10.0) == pytest.approx(0.405)
    assert indoor.moisture_excess_kpa(25.0) == 0.0
    dry, wet = env(), env()
    monkeypatch.setattr(indoor, "HUMIDITY_CLASS", 0)
    a = dry.step(3600, {}, {}, {}, 0)["bedroom"]["humidity"]
    monkeypatch.setattr(indoor, "HUMIDITY_CLASS", 3)
    b = wet.step(3600, {}, {}, {}, 0)["bedroom"]["humidity"]
    assert b > a


def test_an_overcast_room_s_daylight_is_the_hand_computed_bre_average_daylight_factor():
    """Revision R-A: a 4 x 4 m room, 2.5 m ceiling, one 1.2 x 1.2 m window. By hand, BRE ADF = T W theta /
    (A (1 - R^2)) = 0.68 x 1.44 x 80 / (72 x 0.75) = 1.4507 % (A = 2 x 16 floor and ceiling + 16 x 2.5 walls,
    window included); under a 10,000 lux overcast sky (no direct share) the room's average is 145.07 lux."""
    from vesper.env.indoor import RoomModel
    site = parse_site(SITE)
    win = {"id": "w", "room": "r", "width": 1.2, "height": 1.2, "facing": 180}
    room = RoomModel("r", 16.0, 16.0, 4 * 2.5 - 1.44, [win])
    assert room.surface_m2 == pytest.approx(72.0)
    e = HomeEnvironment(site, Outdoor(site), {"r": room})
    lux = e.daylight("r", state(elev=30, az=180, lux=10_000, direct=0.0))
    assert lux == pytest.approx(10_000 * 1.4507 / 100, rel=1e-4)
