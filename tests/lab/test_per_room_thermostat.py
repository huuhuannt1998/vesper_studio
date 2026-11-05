"""Revision R8-Y (protocol Addendum 18, item 5(a)): the per-room thermostat option of the indoor model, off by
default. Off, a hallway thermostat heats every room that has no thermostat of its own on its own reading (central
heating, the model as built, to the bit); on, every such room has a thermostat of its own that holds the hallway
thermostat's setpoint and mode on the room's own temperature."""
import hashlib

from tests.lab.homes import HOME
from tests.lab.test_rules import lab
from tests.lab.maps import house
from vesper.lab.windows import plan_windows, write_windows

WINTER = '    site: {lat: 44.98, lon: -93.27, elevation_m: 260, tz: America/Chicago, date: "2026-01-14"}\n'
HALL_THERMOSTAT = ('      - {product: "zigbee:acme:therm-1", name: hall_thermostat, at: {room: hall, xy: [0.5, 5.5], '
                   'mount: wall, facing: 90}, state: {system_mode: heat, current_heating_setpoint: 24}}\n')
CENTRAL = HOME.replace('    start_time: "07:30"\n', '    start_time: "07:30"\n' + WINTER) + HALL_THERMOSTAT


def central(tmp_path):
    write_windows(tmp_path, plan_windows(house()))
    t = lab(tmp_path, CENTRAL)
    seen = []
    step = t.rules.env.step

    def recording(seconds, lamps, heating, people, lamp_lux):
        seen.append((seconds, sorted(heating.items())))
        return step(seconds, lamps, heating, people, lamp_lux)
    t.rules.env.step = recording
    return t, seen


def fingerprint(tmp_path) -> str:
    """Twelve hours of the hallway-heated test house (setpoint 24 C against the fallback 15 C outdoor curve), a minute
    apart: the sha256 of every tick's heating shares and every room's full-precision temperature."""
    t, seen = central(tmp_path)
    rows = []
    for _ in range(12 * 60):
        t.clock.advance(60.0)
        rows.append((seen[-1], sorted((rid, r.temperature) for rid, r in t.rules.env.rooms.items())))
    return hashlib.sha256(repr(rows).encode()).hexdigest()


# the hallway-heated test house before the option existed (vesper/lab/rules.py and vesper/env/indoor.py at daa0154)
BEFORE_OPTION = "ab57e9266d8c3186c15eb5a9202edfa18cbf2af7cb479c2f9dbd1e99374325d2"


def test_the_option_is_off_by_default_and_named_a_knob():
    from vesper.env import indoor
    assert indoor.PER_ROOM_THERMOSTAT == 0 and "PER_ROOM_THERMOSTAT" in indoor.KNOBS
    assert indoor.KNOB_DEFAULTS["PER_ROOM_THERMOSTAT"] == 0


def test_off_the_hallway_heats_every_room_to_the_bit_of_the_model_before_it(tmp_path):
    assert fingerprint(tmp_path) == BEFORE_OPTION


def test_off_every_unserved_room_takes_the_hallway_thermostat_s_share(tmp_path):
    t, seen = central(tmp_path)
    t.clock.advance(6 * 3600)
    for _, shares in seen:
        s = dict(shares)
        assert s["kitchen"] == s["bedroom"] == s["hall"]


def test_on_each_room_heats_on_its_own_reading_to_the_hallway_thermostat_s_setpoint(tmp_path):
    from vesper.env import indoor
    from vesper.lab.rules import HEAT_BAND_C
    with indoor.knobs(PER_ROOM_THERMOSTAT=1):
        t, seen = central(tmp_path)
        differs, rooms = 0, t.rules.env.rooms
        for _ in range(12 * 60):
            before = {rid: r.temperature for rid, r in rooms.items()}
            t.clock.advance(60.0)
            s = dict(seen[-1][1])
            differs += len({s["hall"], s["kitchen"], s["bedroom"]}) > 1
            for rid in ("kitchen", "bedroom"):  # a room well below the setpoint heats, one well above does not
                if before[rid] < 24 - HEAT_BAND_C - 0.05:
                    assert s[rid] == 1.0, rid
                if before[rid] > 24 + HEAT_BAND_C + 0.05:
                    assert s[rid] == 0.0, rid
        assert differs > 0  # the rooms no longer switch together
        for rid in ("hall", "kitchen", "bedroom"):  # no room is left below the setpoint (the sun may lift one above)
            assert rooms[rid].temperature >= 24 - HEAT_BAND_C - 0.2, rid


def test_on_a_setback_on_the_hallway_thermostat_lowers_every_room_s_thermostat(tmp_path):
    from vesper.env import indoor
    with indoor.knobs(PER_ROOM_THERMOSTAT=1):
        t, seen = central(tmp_path)
        t.clock.advance(3600)
        t.runtime.platform_set(t.rules.twin_ids["hall_thermostat"], "current_heating_setpoint", 5)
        t.clock.advance(3 * 3600)
        assert {rid for _, rid in t.rules._room_heating} == {"kitchen", "bedroom"}  # the rooms' own thermostats
        assert all(v == 0.0 for _, shares in seen[-60:] for _, v in shares)  # none heats to 5 C at 15-25 C
        t.runtime.platform_set(t.rules.twin_ids["hall_thermostat"], "current_heating_setpoint", 30)
        t.clock.advance(120)
        assert all(v == 1.0 for _, v in seen[-1][1])  # and all follow it up


def test_on_the_hallway_thermostat_reports_its_own_room_only(tmp_path):
    from vesper.env import indoor
    with indoor.knobs(PER_ROOM_THERMOSTAT=1):
        t, _ = central(tmp_path)
        t.clock.advance(2 * 3600)
        assert set(t.rules._heating) == {"hall_thermostat"}  # the rooms' own thermostats are the model's, not twins
        assert t.state("hall_thermostat")["local_temperature"] == t.rules.rooms_now["hall"]["temperature"]
