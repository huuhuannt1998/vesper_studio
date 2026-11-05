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


# -- the sensitivity knobs (revision R2-a) ---------------------------------------------------------------------------
def _fingerprint() -> str:
    """Two days of the test house stepped every 10 min, a room heated on and off, one cooled, people and lamps in the
    hall; the sha256 of every room's full-precision temperature and every reading."""
    import hashlib
    e, rows = env(), []
    for k in range(2 * 144):
        out = e.step(k * 600, {"hall": k % 3}, {"bedroom": 1.0 if (k // 18) % 2 else 0.0, "kitchen": -0.5 * (k % 2)},
                     {"hall": k % 5}, 200)
        rows.append((repr([(rid, r.temperature) for rid, r in sorted(e.rooms.items())]), sorted(out.items())))
    return hashlib.sha256(repr(rows).encode()).hexdigest()


# The fingerprint of the model before the knobs existed (vesper/env/indoor.py at 120f4ca, loaded in place of this
# one): at their defaults the knobs change nothing, to the bit.
BEFORE_KNOBS = "671b22a0fa68c482465b3c557564058a282263a1ed876a9fab05eb484b55d8d8"


DEFAULTS = {"DAYLIGHT_SCALE": 1.0, "CAPACITY_SCALE": 1.0, "HUMIDITY_T_OFFSET_C": 0.0, "INTERNAL_GAINS_W_PER_M2": 0.0,
            "ROOF_GROUND": 0.0, "PREWARM_DAYS": 0.0, "SOLAR_APERTURE_SCALE": 1.0, "OUTDOOR_T_OFFSET_C": 0.0,
            "HUMIDITY_CLASS": 1}


def test_the_knobs_at_their_defaults_are_the_model_before_them_to_the_bit():
    """Every knob (R2-a's and Addendum 7's) at its default: the model of 120f4ca, to the bit."""
    from vesper.env import indoor
    assert set(indoor.KNOBS) == set(DEFAULTS)
    assert {k: getattr(indoor, k) for k in DEFAULTS} == DEFAULTS
    assert _fingerprint() == BEFORE_KNOBS
    with indoor.knobs(**DEFAULTS):
        assert _fingerprint() == BEFORE_KNOBS
    e = env()
    assert e.prewarm(0.0, lambda: {}) == 0.0  # no warm-up at the default
    assert not {"capacity_scale", "internal_gains_w_per_m2", "roof_ground", "prewarm_days", "solar_aperture_scale",
                "outdoor_t_offset_c"} & set(e.provenance()["indoor"])


def test_knobs_restore_their_values_even_when_the_block_fails():
    from vesper.env import indoor
    with pytest.raises(RuntimeError), indoor.knobs(CAPACITY_SCALE=4.0, HUMIDITY_T_OFFSET_C=2.0):
        assert (indoor.CAPACITY_SCALE, indoor.HUMIDITY_T_OFFSET_C) == (4.0, 2.0)
        assert env().provenance()["indoor"]["capacity_scale"] == 4.0
        raise RuntimeError
    assert (indoor.CAPACITY_SCALE, indoor.HUMIDITY_T_OFFSET_C) == (1.0, 0.0)
    with pytest.raises(ValueError), indoor.knobs(C_PER_M2=1.0):
        pass
    assert indoor.C_PER_M2 == 165_000.0


def test_a_heavier_room_cools_more_slowly_by_exactly_its_capacity():
    """One step of the exact solution: T - eq shrinks by exp(-h dt / C), so doubling C halves the exponent."""
    import math

    from vesper.env import indoor
    after = {}
    for scale in (1.0, 2.0):
        e = env()
        for r in e.rooms.values():
            r.neighbours = {}
        with indoor.knobs(CAPACITY_SCALE=scale):
            e.step(0, {}, {}, {}, 0)
            e.step(3600, {}, {}, {}, 0)
        after[scale] = e.rooms["hall"].temperature
    eq = env().outdoor.at(3600).temperature_c  # the hall has no windows (no sun), no heating, no people
    d0, d1, d2 = indoor.START_C - eq, after[1.0] - eq, after[2.0] - eq
    assert d0 > d2 > d1 > 0
    assert math.log(d1 / d0) == pytest.approx(2 * math.log(d2 / d0))


def test_the_humidity_offset_moves_only_the_humidity():
    from vesper.env import indoor
    a = env().step(3600, {}, {}, {}, 0)
    with indoor.knobs(HUMIDITY_T_OFFSET_C=2.0):
        b = env().step(3600, {}, {}, {}, 0)
    for rid in a:
        assert a[rid]["temperature"] == b[rid]["temperature"] and a[rid]["illuminance"] == b[rid]["illuminance"]
        assert b[rid]["humidity"] <= a[rid]["humidity"]  # the same vapour at a warmer temperature: drier
    assert any(b[rid]["humidity"] < a[rid]["humidity"] for rid in a)


# -- the error-budget knobs (revision R4-B, protocol Addendum 7) ------------------------------------------------------
def _day(**knob_values) -> dict[str, float]:
    """Every room's temperature after a January day (00:00-24:00, every minute, nobody heating), under the knobs."""
    from vesper.env import indoor
    e = env()
    with indoor.knobs(**knob_values):
        for t in range(0, 24 * 3600 + 1, 60):
            e.step(t, {}, {}, {}, 0)
    return {rid: r.temperature for rid, r in e.rooms.items()}


def test_internal_gains_warm_every_room():
    base, warm = _day(), _day(INTERNAL_GAINS_W_PER_M2=5.0)
    assert all(warm[r] > base[r] + 0.5 for r in base)
    assert all(_day(INTERNAL_GAINS_W_PER_M2=2.0)[r] < warm[r] for r in base)


def test_a_roof_and_a_ground_floor_lose_heat_and_cool_every_room():
    from vesper.env import indoor
    room = env().rooms["bedroom"]
    h0 = room.h_w_per_k
    with indoor.knobs(ROOF_GROUND=1.0):
        assert room.h_w_per_k == pytest.approx(h0 + (0.15 + 0.25) * room.floor_m2)
    base, lossy = _day(), _day(ROOF_GROUND=1.0)
    assert all(lossy[r] < base[r] for r in base)


def test_a_larger_solar_aperture_warms_a_sunny_room_and_leaves_the_windowless_one():
    e = env()
    s = state(elev=30, az=180)
    from vesper.env import indoor
    full = e._solar_w(e.rooms["bedroom"], s)
    with indoor.knobs(SOLAR_APERTURE_SCALE=0.25):
        assert e._solar_w(e.rooms["bedroom"], s) == pytest.approx(0.25 * full)
    base, big, small = _day(), _day(SOLAR_APERTURE_SCALE=2.0), _day(SOLAR_APERTURE_SCALE=0.25)
    assert small["bedroom"] < base["bedroom"] < big["bedroom"]  # the bedroom's window faces the winter sun
    assert big["hall"] > base["hall"]  # through its walls only: the hall has no window
    assert big["hall"] - base["hall"] < big["bedroom"] - base["bedroom"]


def test_an_outdoor_offset_moves_the_outdoor_air_and_the_rooms_and_keeps_the_vapour():
    from vesper.env import indoor
    e, f = env(), env()
    a = e.outdoor_at(12 * 3600)
    with indoor.knobs(OUTDOOR_T_OFFSET_C=2.0):
        b = f.outdoor_at(12 * 3600)
    assert b.temperature_c == pytest.approx(a.temperature_c + 2.0)
    assert b.humidity_pct / 100 * e_sat(b.temperature_c) == pytest.approx(a.humidity_pct / 100 * e_sat(a.temperature_c))
    assert b.humidity_pct < a.humidity_pct
    base, warm, cold = _day(), _day(OUTDOOR_T_OFFSET_C=2.0), _day(OUTDOOR_T_OFFSET_C=-2.0)
    assert all(cold[r] < base[r] < warm[r] for r in base)
    with indoor.knobs(OUTDOOR_T_OFFSET_C=2.0):  # the porch reads env.last
        f.step(0, {}, {}, {}, 0)
    assert f.last.temperature_c == pytest.approx(e.outdoor.at(0).temperature_c + 2.0)


def test_a_higher_humidity_class_raises_the_room_humidity():
    from vesper.env import indoor
    out = {}
    for c in (1, 2, 3):
        with indoor.knobs(HUMIDITY_CLASS=c):
            out[c] = env().step(3600, {}, {}, {}, 0)["bedroom"]["humidity"]
    assert out[1] < out[2] < out[3]


def test_a_longer_warm_up_brings_the_rooms_nearer_their_periodic_state():
    """PREWARM_DAYS days on day 0's weather before the run, the heating asked before every step; inf: the periodic
    steady state (PREWARM_PERIODIC_DAYS days). The rooms start at START_C; the longer the warm-up, the nearer that
    state."""
    import math

    from vesper.env import indoor
    asked, on = [], [False]

    def heating():  # a thermostat in the bedroom, 20 C, 0.3 K band (vesper.lab.rules)
        asked.append(1)
        t = e.rooms["bedroom"].temperature
        on[0] = t < 19.7 or (on[0] and t < 20.3)
        return {"bedroom": 1.0} if on[0] else {}

    e = env()
    with indoor.knobs(PREWARM_DAYS=2.0):
        assert e.prewarm(0.0, heating) == 2.0
    assert len(asked) == 2 * 1440 and e.rooms["bedroom"].temperature > 19.0  # asked once per step; it heated
    temps = {}
    for days in (1.0, 4.0, 9.0, math.inf):  # free-running (no heating): a pure decay toward the periodic state
        e = env()
        with indoor.knobs(PREWARM_DAYS=days):
            lived = e.prewarm(0.0, dict)
        temps[days] = {rid: r.temperature for rid, r in e.rooms.items()}
        assert lived == (days if days != math.inf else indoor.PREWARM_PERIODIC_DAYS)
    steady = temps[math.inf]
    gap = {d: max(abs(temps[d][r] - steady[r]) for r in steady) for d in (1.0, 4.0, 9.0)}
    assert gap[1.0] > gap[4.0] > gap[9.0] and gap[9.0] < 0.1
    assert temps[1.0]["hall"] != indoor.START_C  # it did live the day
    e = env()
    e.step(0, {}, {}, {}, 0)
    with indoor.knobs(PREWARM_DAYS=4.0):
        assert e.prewarm(0.0, heating) == 0.0  # only before the first step
    with indoor.knobs(PREWARM_DAYS=1.5), pytest.raises(ValueError):
        env().prewarm(0.0, heating)
