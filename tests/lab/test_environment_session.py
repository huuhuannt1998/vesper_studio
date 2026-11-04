"""A home with a site (environment design 2026-11-03): its rooms' light and warmth come from the place and the date,
heating follows its thermostat, a person opens and closes shades, and the trace says where every value came from."""
from tests.lab.homes import HOME, catalog, write_home
from tests.lab.maps import house
from tests.lab.test_rules import lab
from vesper.lab.home import load_lab_home
from vesper.lab.session import Session, replay
from vesper.lab.windows import plan_windows, write_windows
from vesper.twins.clock import LogicalClock

SITE = '    site: {lat: 35.2271, lon: -80.8431, elevation_m: 229, tz: America/New_York, date: "2026-06-21"}\n'
THERMOSTAT = ('      - {product: "zigbee:acme:therm-1", name: bedroom_thermostat, at: {room: bedroom, xy: [6.8, 4.5], '
              'mount: wall, facing: 180}, state: {system_mode: heat, current_heating_setpoint: 25}}\n')
SITED = HOME.replace('    start_time: "07:30"\n', '    start_time: "07:30"\n' + SITE) + THERMOSTAT


def sited(tmp_path, text=SITED):
    write_windows(tmp_path, plan_windows(house()))
    return lab(tmp_path, text)


def test_each_tick_is_traced_with_the_outdoors_and_every_room(tmp_path):
    t = sited(tmp_path)
    env = [r for r in t.log if r.get("rule") == "environment"]
    assert env and set(env[0]["rooms"]) == {"hall", "kitchen", "bedroom"}
    assert {"sun_elevation", "illuminance_lux", "temperature_c"} <= set(env[0]["outdoor"])


def test_a_south_window_fills_the_bedroom_with_noon_light_and_the_night_is_dark(tmp_path):
    t = sited(tmp_path)  # starts 07:30 on the June solstice
    t.clock.advance(5.5 * 3600)  # 13:00
    noon = t.rules.rooms_now["bedroom"]["illuminance"]
    assert noon > 500 and t.rules.daylight("hall") == 0  # the hallway has no window
    t.clock.advance(10 * 3600)  # 23:00
    assert t.rules.daylight("bedroom") == 0


def test_a_heating_thermostat_warms_its_room_and_not_the_others(tmp_path):
    t = sited(tmp_path)
    t.clock.advance(3 * 3600)
    rooms = t.rules.rooms_now
    assert rooms["bedroom"]["temperature"] > rooms["kitchen"]["temperature"]
    assert t.rules._heating["bedroom_thermostat"] in (True, False)
    assert t.state("bedroom_thermostat")["local_temperature"] == rooms["bedroom"]["temperature"]


def test_a_shade_is_worked_from_inside_its_room_only(tmp_path):
    t = sited(tmp_path)
    window = t.rules.windows_in("bedroom")[0]["id"]
    assert t.rules.shade("p", window, False)["result"] == "out of reach"  # the player is in the hall
    t.world.walk_to("p", (5.0, 4.5))
    t.clock.advance(30)
    assert t.rules.shade("p", window, False)["result"] == "closed"
    assert t.rules.env.shades[window] is False
    assert any(r.get("rule") == "shade" and r["window"] == window for r in t.log)


def test_without_a_site_the_rooms_follow_the_assumed_curve_as_before(tmp_path):
    t = lab(tmp_path)
    assert t.rules.env is None and t.rules.sun_up() is None
    assert not any(r.get("rule") == "environment" for r in t.log)


def test_the_trace_names_the_environment_s_sources_and_a_replay_closes_the_shade_again(tmp_path):
    write_windows(tmp_path, plan_windows(house()))
    home = load_lab_home(write_home(tmp_path, SITED), catalog())
    s = Session(home, clock=LogicalClock())
    s.start(residents=False)
    meta = s.records[0]
    assert meta["kind"] == "meta" and meta["environment"]["site"]["date"] == "2026-06-21"
    assert meta["environment"]["weather"] is None  # nothing cached: clear sky, marked assumed
    s.walk_to((5.0, 4.5))
    s.clock.advance(30)
    window = s.rules.windows_in("bedroom")[0]["id"]
    assert s.shade(window, False)["result"] == "closed"
    s.clock.advance(120)
    s.stop()
    again = replay(s.records, home)
    assert any(r.get("kind") == "rule" and r.get("rule") == "shade" for r in again)


def test_a_thermostat_reports_heat_while_it_heats_and_idle_when_it_stops(tmp_path):
    """Home Assistant showed a heating thermostat as idle (live 2026-11-04): its running_state now follows."""
    text = SITED.replace("zigbee:acme:therm-1", "zigbee:acme:therm-2").replace("current_heating_setpoint: 25",
                                                                               "current_heating_setpoint: 30")
    t = sited(tmp_path, text)
    t.clock.advance(120)
    assert t.rules._heating["bedroom_thermostat"] is True
    assert t.state("bedroom_thermostat")["running_state"] == "heat"
    t.runtime.platform_set(t.rules.twin_ids["bedroom_thermostat"], "current_heating_setpoint", 5)  # it stops next tick
    t.clock.advance(120)
    assert t.rules._heating["bedroom_thermostat"] is False
    assert t.state("bedroom_thermostat")["running_state"] == "idle"



def test_a_cooling_thermostat_keeps_a_summer_room_cooler_and_reports_cool(tmp_path):
    """No home could cool (found 2026-11-04: summer rooms settled at 27-38 degrees): a thermostat with a cooling
    setpoint in cool (or auto) cools its room above it, and says so."""
    phoenix = SITED.replace('lat: 35.2271, lon: -80.8431, elevation_m: 229, tz: America/New_York, date: "2026-06-21"',
                            'city: "Phoenix, AZ, USA", date: "2026-07-15"')
    def run(mode, path):
        text = phoenix.replace("zigbee:acme:therm-1", "zigbee:acme:therm-3").replace(
            "state: {system_mode: heat, current_heating_setpoint: 25}",
            f"state: {{system_mode: \"{mode}\", occupied_cooling_setpoint: 18}}")  # no weather cached: a mild day
        path.mkdir()
        t = sited(path, text)
        t.seen = set()
        for _ in range(8 * 60):  # 07:30 to 15:30, what the thermostat says each minute
            t.clock.advance(60)
            t.seen.add(t.state("bedroom_thermostat")["running_state"])
        return t
    cooled, off = run("cool", tmp_path / "cool"), run("off", tmp_path / "off")
    assert cooled.rules.rooms_now["bedroom"]["temperature"] < off.rules.rooms_now["bedroom"]["temperature"] - 1
    assert "cool" in cooled.seen and "cool" not in off.seen
    assert any("bedroom_thermostat" in r.get("cooling", []) for r in cooled.log if r.get("rule") == "environment")
