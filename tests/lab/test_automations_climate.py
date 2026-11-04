"""The engine executes climate actions on real thermostat twins (the catalog's), as Home Assistant's MQTT climate entity
sends them: a setpoint, a mode. What a twin cannot do is recorded (permissive) or refused (strict) with a reason."""
from pathlib import Path

import pytest

from tests.lab.homes import HOME, write_home
from vesper.lab.automations import Engine, entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock
from vesper.twins.home import load_catalog

CATALOG = Path("catalog/vds")
pytestmark = pytest.mark.skipif(not CATALOG.is_dir(), reason="catalog/vds is not in this checkout")

TRV = "zigbee:brennenstuhl:ht-cz-01"       # current_heating_setpoint 5..30, modes off/auto/heat
SONOFF = "zigbee:sonoff:trvzb"             # occupied_heating_setpoint 4..35, modes off/auto/heat
HEAT_PUMP = "zigbee:leviton:rc-2000wh"     # heating and cooling setpoints 10..30, modes off/auto/heat/cool


def thermostat_lab(tmp_path, product, state=""):
    extra = (f'      - {{product: "{product}", name: therm, at: {{room: bedroom, xy: [6.8, 4.5], mount: wall}}'
             f'{state}}}\n')
    (tmp_path / "automations.yaml").write_text("[]\n")
    text = HOME.split("    devices:\n")[0].replace("    doors:", "    doors:") + "    devices:\n" + extra
    text += "    automations: automations.yaml\n"
    home = load_lab_home(write_home(tmp_path, text), load_catalog(CATALOG))
    ids = entity_ids(home)
    climate = next(e for e in ids["therm"].values() if e.startswith("climate."))
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=False)
    return climate, s


def run(s, actions, permissive=False):
    e = Engine(s, permissive=permissive, automations=[
        {"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}], "actions": actions}])
    if e.automations:
        e._fire(e.automations[0], "test")
        s.clock.advance(10)  # the command crosses the device's link
    return e


def state(s):
    return s.runtime.state(s.twin_ids["therm"])


def commands(s):
    return [(r["service"], r["feature"], r["value"], r["accepted"]) for r in s.records if r["kind"] == "automation"]


def call(climate, service, **data):
    return {"action": f"climate.{service}", "target": {"entity_id": climate}, "data": data}


def test_set_temperature_sets_the_thermostats_setpoint(tmp_path):
    climate, s = thermostat_lab(tmp_path, TRV)
    run(s, [call(climate, "set_temperature", temperature=23)])
    assert state(s)["current_heating_setpoint"] == 23
    assert commands(s) == [("climate.set_temperature", "current_heating_setpoint", 23, True)]


def test_the_setpoint_is_the_one_the_climate_entity_exposes_on_another_product(tmp_path):
    climate, s = thermostat_lab(tmp_path, SONOFF)
    run(s, [call(climate, "set_temperature", temperature=31.5)])
    assert state(s)["occupied_heating_setpoint"] == 31.5  # this product goes to 35
    assert commands(s)[0][1] == "occupied_heating_setpoint"


def test_set_temperature_can_also_set_the_mode(tmp_path):
    climate, s = thermostat_lab(tmp_path, TRV, ", state: {system_mode: \"off\"}")
    run(s, [call(climate, "set_temperature", temperature=21, hvac_mode="heat")])
    assert (state(s)["current_heating_setpoint"], state(s)["system_mode"]) == (21, "heat")


def test_a_temperature_outside_the_range_is_refused_strict_and_recorded_permissive(tmp_path):
    climate, s = thermostat_lab(tmp_path, TRV)
    before = state(s)["current_heating_setpoint"]
    strict = Engine(s, automations=[{"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}],
                                     "actions": [call(climate, "set_temperature", temperature=35)]}])
    assert strict.automations == [] and "5" in strict.refused[0]["reason"] and "30" in strict.refused[0]["reason"]
    e = run(s, [call(climate, "set_temperature", temperature=35)], permissive=True)
    assert e.refused == [] and "35" in e.automations[0].approximations[0]
    assert state(s)["current_heating_setpoint"] == before
    assert commands(s) == [("climate.set_temperature", "", None, True)]  # written down, nothing sent


def test_set_hvac_mode_sets_the_mode_the_twin_supports(tmp_path):
    climate, s = thermostat_lab(tmp_path, TRV, ", state: {system_mode: \"off\"}")
    run(s, [call(climate, "set_hvac_mode", hvac_mode="heat")])
    assert state(s)["system_mode"] == "heat"
    assert commands(s) == [("climate.set_hvac_mode", "system_mode", "heat", True)]


@pytest.mark.parametrize("mode", ["cool", "heat_cool"])
def test_a_mode_the_twin_lacks_is_refused_strict_and_recorded_permissive_with_the_reason(tmp_path, mode):
    climate, s = thermostat_lab(tmp_path, TRV)
    actions = [call(climate, "set_hvac_mode", hvac_mode=mode)]
    strict = Engine(s, automations=[{"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}],
                                     "actions": actions}])
    assert strict.automations == [] and mode in strict.refused[0]["reason"]
    e = run(s, actions, permissive=True)
    assert e.refused == [] and mode in e.automations[0].approximations[0]
    assert state(s)["system_mode"] != mode and commands(s) == [("climate.set_hvac_mode", "", None, True)]


def test_a_heat_pump_takes_cool(tmp_path):
    climate, s = thermostat_lab(tmp_path, HEAT_PUMP)
    run(s, [call(climate, "set_hvac_mode", hvac_mode="cool")])
    assert state(s)["system_mode"] == "cool"


def test_a_range_sets_the_heating_and_cooling_setpoints_of_a_twin_that_has_both(tmp_path):
    climate, s = thermostat_lab(tmp_path, HEAT_PUMP)
    run(s, [call(climate, "set_temperature", target_temp_low=19, target_temp_high=25)])
    assert (state(s)["occupied_heating_setpoint"], state(s)["occupied_cooling_setpoint"]) == (19, 25)


def test_a_range_on_a_heating_only_twin_is_recorded_with_the_reason(tmp_path):
    climate, s = thermostat_lab(tmp_path, TRV)
    e = run(s, [call(climate, "set_temperature", target_temp_low=19, target_temp_high=25)], permissive=True)
    assert "target_temp" in e.automations[0].approximations[0]
    assert commands(s) == [("climate.set_temperature", "", None, True)]


def test_turn_off_sets_off_and_turn_on_picks_heat(tmp_path):
    climate, s = thermostat_lab(tmp_path, TRV, ", state: {system_mode: \"auto\"}")
    run(s, [call(climate, "turn_off")])
    assert state(s)["system_mode"] == "off"
    run(s, [call(climate, "turn_on")])
    assert state(s)["system_mode"] == "heat"
    assert [c[:3] for c in commands(s)] == [("climate.turn_off", "system_mode", "off"),
                                            ("climate.turn_on", "system_mode", "heat")]


def test_a_thermostat_that_is_not_the_homes_stays_recorded_in_permissive_mode(tmp_path):
    _, s = thermostat_lab(tmp_path, TRV)
    e = run(s, [call("climate.not_here", "set_temperature", temperature=21)], permissive=True)
    assert e.refused == [] and commands(s) == [("climate.set_temperature", "", None, True)]
    assert Engine(s, automations=[{"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}],
                                   "actions": [call("climate.not_here", "set_temperature", temperature=21)]}]
                  ).refused


def test_covers_are_still_only_recorded(tmp_path):
    _, s = thermostat_lab(tmp_path, TRV)
    e = run(s, [{"action": "cover.open_cover", "target": {"entity_id": "cover.blind"}}], permissive=True)
    assert e.refused == [] and commands(s) == [("cover.open_cover", "", None, True)]
    assert Engine(s, automations=[{"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}],
                                   "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.blind"}}]}]
                  ).refused
