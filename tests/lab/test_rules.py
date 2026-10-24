from types import SimpleNamespace

import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.mapworld import MapWorld
from vesper.lab.rules import Rules, ambient_at
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Runtime


def lab(tmp_path, text=HOME, at=None):
    home = load_lab_home(write_home(tmp_path, text), catalog())
    clock = LogicalClock()
    runtime = Runtime(clock=clock, seed=home.seed)
    ids = {d.name: runtime.add(d.record, twin_id=f"{d.product}@{d.name}", state=d.state).id for d in home.devices}
    world = MapWorld(home.map, clock)
    for door, is_open in home.doors.items():
        world.add_door(door, is_open)
    log = []
    rules = Rules(home, runtime, world, ids, record=log.append)
    rules.start()
    world.spawn("p", "female_0", at or home.avatar["start"])
    return SimpleNamespace(home=home, clock=clock, runtime=runtime, world=world, rules=rules, log=log, ids=ids,
                           state=lambda name: runtime.state(ids[name]))


def go(t, point):
    t.world.walk_to("p", point)
    t.clock.advance(30.0)


def test_walking_in_trips_a_ceiling_motion_sensor_and_keeping_still_lets_it_clear(tmp_path):
    t = lab(tmp_path, at=(5.0, 1.5))  # the kitchen
    assert t.state("hall_motion")["occupancy"] is False
    go(t, (1.5, 3.0))  # the hall
    assert t.state("hall_motion")["occupancy"] is True
    t.clock.advance(OCCUPANCY_TIMEOUT_S + 5)  # standing still: a PIR sees no motion
    assert t.state("hall_motion")["occupancy"] is False
    t.world.move("p", 0.3, 0.0)
    assert t.state("hall_motion")["occupancy"] is True
    assert any(r["rule"] == "presence" and r["device"] == "hall_motion" for r in t.log)


def test_a_wall_sensor_sees_its_cone_only(tmp_path):
    text = HOME + '      - {product: "zigbee:acme:pir-1", name: kitchen_pir, at: {room: kitchen, xy: [3.2, 0.2],\n' \
                  '         mount: wall, facing: 0}}\n'
    t = lab(tmp_path, text, at=(3.5, 2.7))  # in the kitchen, beside the sensor, outside its 110° cone
    assert t.state("kitchen_pir")["occupancy"] is False
    go(t, (5.0, 1.5))
    assert t.state("kitchen_pir")["occupancy"] is True


def test_the_front_door_opens_and_closes_by_hand_and_its_contact_sensor_reports_it(tmp_path):
    t = lab(tmp_path)
    go(t, (0.4, 3.0))
    assert t.state("front_door")["contact"] is True  # closed (Zigbee2MQTT: contact true = closed)
    assert t.rules.interact("p") == {"ok": True, "kind": "door", "target": "hall-porch", "result": "opened"}
    assert t.world.doors["hall-porch"] is True and t.state("front_door")["contact"] is False
    assert t.rules.interact("p")["result"] == "closed" and t.state("front_door")["contact"] is True


def test_a_locked_door_stays_shut(tmp_path):
    t = lab(tmp_path)
    go(t, (0.4, 3.0))
    assert t.rules.interact("p", "front_lock")["result"] == "toggled"
    assert t.state("front_lock")["Door Lock.LockState"] == "Locked"
    assert t.rules.interact("p") == {"ok": False, "kind": "door", "target": "hall-porch", "result": "locked"}
    assert t.world.doors["hall-porch"] is False
    assert t.rules.effects["hall-porch"] == {"open": False, "locked": True}


def test_a_lamp_toggles_by_hand_and_a_bound_button_switches_it(tmp_path):
    t = lab(tmp_path)
    go(t, (5.0, 4.4))
    assert t.rules.interact("p")["target"] == "bedroom_lamp" and t.state("bedroom_lamp")["state"] == "ON"
    assert t.runtime.actions[-1].actor == "human:p"
    assert t.rules.effects["bedroom_lamp"] == {"on": True}
    go(t, (3.6, 3.6))
    assert t.rules.interact("p", "bedroom_button")["result"] == "pressed"
    assert t.state("bedroom_lamp")["state"] == "OFF" and t.runtime.actions[-1].actor == "human:p via bedroom_button"
    assert t.state("bedroom_button")["action"] is not None


def test_only_what_a_person_can_reach_and_operate_is_a_target(tmp_path):
    t = lab(tmp_path, at=(1.5, 1.0))
    assert t.rules.interact("p") == {"ok": False, "kind": None, "target": None, "result": "nothing in reach"}
    assert t.rules.interact("p", "bedroom_lamp")["result"] == "out of reach"
    with pytest.raises(ValueError, match="hall_motion is not something a person operates"):
        t.rules.interact("p", "hall_motion")


def test_ambient_follows_the_time_of_day_and_the_lamps():
    night, noon = ambient_at(3 * 3600, 0), ambient_at(12.5 * 3600, 0)
    assert night["illuminance"] == 0 and noon["illuminance"] > 200
    assert ambient_at(3 * 3600, 2)["illuminance"] == 400  # two lamps on at night
    assert ambient_at(15 * 3600, 0)["temperature"] > ambient_at(4 * 3600, 0)["temperature"]


def test_the_climate_sensor_follows_the_room(tmp_path):
    t = lab(tmp_path)  # starts 07:30
    morning = t.state("kitchen_climate")["temperature"]
    t.clock.advance(7.5 * 3600)  # 15:00
    assert t.state("kitchen_climate")["temperature"] > morning
