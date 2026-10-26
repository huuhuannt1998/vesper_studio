"""The comfort band (spec §5a reactions): a resident who finds a room too cold or too warm, as its thermostat shows
it, changes the thermostat by hand. The rules' room temperature follows the time of day (21 °C +- 1.5, lowest at
03:00): 20.25 °C at 07:00, 22.45 °C at 14:00."""
import pytest

from tests.lab.homes import HOME, catalog, write_home
from tests.lab.test_resident import inputs, lab, task
from vesper.lab.home import load_lab_home
from vesper.lab.profile import Profile
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock


def thermostat(setpoint=17, mode="off"):
    return ('      - {product: "zigbee:acme:therm-1", name: bedroom_thermostat, at: {room: bedroom, xy: [6.8, 4.5], '
            f'mount: wall}}, state: {{current_heating_setpoint: {setpoint}, system_mode: "{mode}"}}}}\n')


def state(t):
    s = t.s.runtime.state(t.s.twin_ids["bedroom_thermostat"])
    return s["current_heating_setpoint"], s["system_mode"]


def sets(s):
    return [(r["args"]["feature"], r["args"]["value"], r["why"]["reason"]) for r in inputs(s) if r["op"] == "set"]


def test_a_room_too_cold_gets_its_heating_switched_on_and_turned_up(tmp_path):
    t = lab(tmp_path, [task("07:00", 60, "read", "LEISURE", "bedroom")], profile=Profile(comfort=(21.5, 24.0)),
            extra=thermostat())
    t.at("07:03")
    assert state(t) == (21.5, "heat")
    assert sets(t.s) == [("system_mode", "heat", "too cold"), ("current_heating_setpoint", 21.5, "too cold")]
    t.at("07:30")
    assert len(sets(t.s)) == 2  # the heating is on its way: nothing more to do


def test_a_room_too_warm_gets_its_heating_turned_down(tmp_path):
    t = lab(tmp_path, [task("14:00", 60, "work", "WORK", "bedroom")], start="14:00",
            profile=Profile(comfort=(19.0, 22.0)), extra=thermostat(setpoint=25, mode="heat"))
    t.at("14:03")
    assert state(t) == (22.0, "heat") and sets(t.s) == [("current_heating_setpoint", 22.0, "too warm")]


@pytest.mark.parametrize("tasks, comfort", [([task("07:00", 60, "read", "LEISURE", "bedroom")], (19.0, 24.0)),
                                            ([task("07:00", 60, "sleep in", "SLEEP", "bedroom")], (21.5, 24.0))])
def test_a_comfortable_or_sleeping_resident_leaves_the_thermostat_alone(tmp_path, tasks, comfort):
    t = lab(tmp_path, tasks, profile=Profile(comfort=comfort), extra=thermostat())
    t.at("07:30")
    assert state(t) == (17, "off") and sets(t.s) == []


def test_the_player_sets_a_thermostat_within_reach_and_the_replay_matches(tmp_path):
    home = load_lab_home(write_home(tmp_path, HOME + thermostat()), catalog())
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    s.spawn("player", "female_0", (3.5, 4.5))  # in the bedroom, across it from the thermostat
    assert s.adjust("bedroom_thermostat", "current_heating_setpoint", 23)["result"] == "out of reach"
    s.walk_to((6.5, 4.5))
    s.wait(10)
    outcome = s.adjust("bedroom_thermostat", "current_heating_setpoint", 23)
    assert outcome["ok"] and s.runtime.state(s.twin_ids["bedroom_thermostat"])["current_heating_setpoint"] == 23
    assert not s.adjust("bedroom_thermostat", "current_heating_setpoint", 40)["ok"]  # its range is 5 to 30
    with pytest.raises(ValueError, match="local_temperature is not something a person sets by hand"):
        s.adjust("bedroom_thermostat", "local_temperature", 30)
    s.wait(60)
    s.stop()
    assert compare_reports(s.records, replay(s.records, home)) is None
