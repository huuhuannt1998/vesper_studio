"""Daylight-following lights (play-view environment design 2026-11-05, §3): per lit room with an illuminance sensor, a
generated home may have one Home Assistant automation that sets the room's lights to make up the gap between the
room's daylight and a target, brightness = clamp((target - daylight) / lamp_lux_at_full, 0, 1) x 254, off below 5 %.
It is a normal automation in the home's automations.yaml, so VESPER's engine and Home Assistant both run it."""
import textwrap

import pytest
import yaml

from tests.catalog.fixtures import BATTERY, zdef
from tests.lab.homes import HOME, catalog, write_home
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.lab.daylight import (DEFAULT_TARGET_LUX, follow_brightness, follow_rules, illuminance_thresholds,
                                 write_daylight_automations)
from vesper.lab.home import load_lab_home
from vesper.lab.rules import LAMP_LUX
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock

LUX = {"name": "illuminance", "label": "Illuminance", "access": 1, "type": "numeric", "property": "illuminance",
       "unit": "lx"}
SENSOR = ('      - {product: "zigbee:acme:lux-1", name: bedroom_lux, at: {room: bedroom, xy: [4.0, 4.0], '
          'mount: wall}}\n')


def lux_catalog():
    out = catalog()
    for r in zigbee_records({"version": "26.123.0", "definitions": [zdef("LUX-1", "Acme", [LUX, BATTERY])]}):
        out[r["id"]] = r
    return out


@pytest.mark.parametrize("daylight, want", [
    (100, round(200 / 400 * 254)),  # half the gap a full room of lamps makes up
    (0, round(300 / 400 * 254)),  # dark: the lamps make up the whole target
    (-200, 254),  # never beyond full
    (300, 0), (900, 0),  # daylight enough: off
    (290, 0),  # 10 lx short is 2.5 %: below 5 %, off
    (270, round(30 / 400 * 254)),  # 7.5 %: on, dim
])
def test_the_rule_makes_up_the_gap_to_the_target_off_below_five_per_cent(daylight, want):
    assert follow_brightness(300, daylight, 400) == want


def test_no_lamps_means_no_brightness():
    assert follow_brightness(300, 0, 0) == 0


def home_with_sensor(tmp_path):
    text = HOME.replace("    devices:\n", "    devices:\n" + SENSOR, 1)
    return write_home(tmp_path, text)


def test_a_lit_room_with_an_illuminance_sensor_gets_one_rule_the_engine_loads(tmp_path):
    path = home_with_sensor(tmp_path)
    cat = lux_catalog()
    made = write_daylight_automations(path, cat)
    assert made == ["bedroom"]  # the bedroom has the dimmable lamp and the sensor; no other room has both
    text = path.read_text()
    assert "automations: automations.yaml" in text
    rules = yaml.safe_load((tmp_path / "automations.yaml").read_text())
    (rule,) = rules
    assert rule["id"] == "vesper_daylight_bedroom" and "daylight" in rule["alias"].lower()
    assert rule["variables"]["target_lux"] == DEFAULT_TARGET_LUX
    assert rule["variables"]["lamp_lux"] == LAMP_LUX and rule["variables"]["lamps_full_lux"] == LAMP_LUX
    home = load_lab_home(path, cat)
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, automations=True)
    assert s.automations.refused == [], s.automations.refused
    assert follow_rules(home) == [{"id": "vesper_daylight_bedroom", "room": "bedroom", "sensor": "bedroom_lux",
                                   "lights": ["bedroom_lamp"], "target_lux": 300, "lamp_lux": LAMP_LUX,
                                   "lamps_full_lux": LAMP_LUX}]


def test_the_engine_sets_the_lamp_to_make_up_the_daylight_gap_while_someone_is_home(tmp_path):
    path = home_with_sensor(tmp_path)
    cat = lux_catalog()
    path.write_text(path.read_text().replace('start_time: "07:30"', 'start_time: "11:00"'))
    write_daylight_automations(path, cat)
    home = load_lab_home(path, cat)
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (1.5, 3.0))  # in the hall: its motion sensor says someone is home
    s.clock.advance(5 * 60)
    lamp = s.rules.twin("bedroom_lamp").state
    # the assumed day (no site): 11:00 gives 300 sin(5 pi / 14) lx of daylight; one lamp makes up the rest
    daylight = round(300 * __import__("math").sin(__import__("math").pi * 5 / 14))
    assert lamp.get("state") == "ON"
    assert abs(lamp["brightness"] - follow_brightness(300, daylight, LAMP_LUX)) <= 2, lamp
    s.clock.advance(10 * 60)  # it holds: the lamp's own light is not taken for daylight
    assert abs(s.rules.twin("bedroom_lamp").state["brightness"] - follow_brightness(300, daylight, LAMP_LUX)) <= 6


def test_a_home_without_such_a_room_gets_no_automations_file(tmp_path):
    path = write_home(tmp_path)
    assert write_daylight_automations(path, catalog()) == []
    assert not (tmp_path / "automations.yaml").exists()
    assert "automations:" not in path.read_text()


def test_the_dialog_reads_the_thresholds_the_home_s_automations_compare_a_sensor_with(tmp_path):
    path = home_with_sensor(tmp_path)
    cat = lux_catalog()
    write_daylight_automations(path, cat)
    rules = yaml.safe_load((tmp_path / "automations.yaml").read_text())
    from vesper.lab.automations import entity_ids
    home = load_lab_home(path, cat)
    lux = entity_ids(home)["bedroom_lux"]["illuminance"]
    rules.append({"id": "dark", "alias": "Lamp on when dark", "triggers": [
        {"trigger": "numeric_state", "entity_id": lux, "below": 50}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": entity_ids(home)["bedroom_lamp"]["state"]}}]})
    (tmp_path / "automations.yaml").write_text(yaml.safe_dump(rules, sort_keys=False))
    home = load_lab_home(path, cat)
    assert illuminance_thresholds(home, "bedroom_lux") == ["lights follow daylight to 300 lx",
                                                           "dark below 50 lx (Lamp on when dark)"]
    assert illuminance_thresholds(home, "bedroom_lamp") == []


def test_generating_a_home_writes_the_rule_only_when_the_request_asks(tmp_path):
    from vesper.lab.generate import generate_home, load_request
    base = textwrap.dedent("""\
        format: vesper-request/1
        rooms: 2
        seed: 1
        devices:
          - {product: "zigbee:acme:dim-1", per: room}
          - {product: "zigbee:acme:lux-1", per: room}
    """)
    cat = lux_catalog()
    (tmp_path / "a.yaml").write_text(base)
    (tmp_path / "b.yaml").write_text(base + "daylight_lights: true\n")
    generate_home(load_request(tmp_path / "a.yaml"), cat, tmp_path / "a")
    generate_home(load_request(tmp_path / "b.yaml"), cat, tmp_path / "b")
    assert not (tmp_path / "a" / "automations.yaml").exists()  # an existing request is unchanged
    rules = yaml.safe_load((tmp_path / "b" / "automations.yaml").read_text())
    assert {r["id"] for r in rules} >= {"vesper_daylight_living", "vesper_daylight_bedroom"}
    load_lab_home(tmp_path / "b" / "home.yaml", cat)  # the home still loads
    (tmp_path / "c.yaml").write_text(base + "daylight_lights: maybe\n")
    from vesper.lab.generate import GenerateError
    with pytest.raises(GenerateError, match="daylight_lights"):
        load_request(tmp_path / "c.yaml")
