import copy
from types import SimpleNamespace

import pytest

from tests.catalog.test_matter_normalize import records as matter_records
from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.mapworld import MapWorld
from vesper.lab.rules import RETRIGGER_S, Rules, ambient_at, is_on
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import MATTER_CONTACT, OCCUPANCY_TIMEOUT_S, Runtime


def lab(tmp_path, text=HOME, at=None, cat=None):
    home = load_lab_home(write_home(tmp_path, text), cat or catalog())
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


def test_a_presence_record_says_whether_the_person_just_came_into_view(tmp_path):
    """The page's event feed shows entries, not every retrigger of a sensor that keeps seeing someone (review)."""
    t = lab(tmp_path, at=(5.0, 1.5))  # the kitchen
    go(t, (1.5, 3.0))  # into the hall
    t.clock.advance(RETRIGGER_S + 1)
    t.world.move("p", 0.1, 0.0)  # moving under the sensor again: a retrigger
    seen = [r["entered"] for r in t.log if r["rule"] == "presence" and r["device"] == "hall_motion"]
    assert seen == [True, False]


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
    assert t.rules.interact("p", "bedroom_lamp")["result"] == "too far"  # the page says why, as PUBG's prompt does
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


def test_nothing_is_used_through_a_wall(tmp_path):
    text = HOME.replace("xy: [3.2, 3.5], mount: wall}", "xy: [5.0, 3.2], mount: wall}")  # on the kitchen | bedroom wall
    t = lab(tmp_path, text, at=(5.0, 2.75))  # in the kitchen, 0.45 m away through the wall
    assert "bedroom_button" not in [target for _, _, target in t.rules.reachable("p")]
    assert t.rules.interact("p", "bedroom_button")["result"] == "other side of the wall"
    assert is_on(t.rules.twin("bedroom_lamp")) is False


def test_a_door_and_the_lock_on_it_are_used_from_either_side(tmp_path):
    t = lab(tmp_path, at=(-0.5, 3.0))  # on the porch, outside the front door
    reach = [target for _, _, target in t.rules.reachable("p")]
    assert "hall-porch" in reach and "front_lock" in reach
    assert t.rules.interact("p", "hall-porch")["result"] == "opened"


def test_a_door_does_not_close_on_the_person_standing_in_it(tmp_path):
    t = lab(tmp_path, at=(0.4, 3.0))  # in the hall, by the front door
    assert t.rules.interact("p", "hall-porch")["result"] == "opened"
    t.world.move("p", -0.4, 0.0)  # into the doorway
    out = t.rules.interact("p", "hall-porch")
    assert (out["ok"], out["result"]) == (False, "obstructed")
    assert t.world.doors["hall-porch"] is True
    t.world.move("p", -0.5, 0.0)  # out onto the porch
    assert t.world.last_move_ok


def test_a_matter_contact_sensor_on_a_closed_door_reads_closed_from_the_start(tmp_path):
    cat = {**catalog(), "matter:100b:0016": matter_records()["matter:100b:0016"]}
    text = HOME.replace("{doorway: bedroom-hall, open: true}", "{doorway: bedroom-hall, open: false}") + \
        '      - {product: "matter:100b:0016", name: bedroom_door, at: {door: bedroom-hall}}\n'
    t = lab(tmp_path, text, cat=cat)
    assert t.world.doors["bedroom-hall"] is False
    assert t.state("bedroom_door")[MATTER_CONTACT] is True  # Matter's StateValue: true is closed


def test_a_radar_presence_sensor_keeps_seeing_a_person_who_sits_still(tmp_path):
    cat = catalog()
    radar = copy.deepcopy(cat["zigbee:acme:pir-1"])
    radar["id"], radar["identity"]["model"] = "zigbee:acme:radar-1", "RADAR-1"
    radar["features"][0].update(name="presence", property="presence", ha="binary_sensor/presence")
    cat[radar["id"]] = radar
    text = HOME + '      - {product: "zigbee:acme:radar-1", name: hall_radar, at: {room: hall, mount: ceiling}}\n'
    t = lab(tmp_path, text, at=(1.5, 3.0), cat=cat)  # in the hall
    t.clock.advance(OCCUPANCY_TIMEOUT_S * 3)  # sitting still
    assert t.state("hall_radar")["presence"] is True
    assert t.state("hall_motion")["occupancy"] is False  # a PIR beside it clears, as a real one does
    go(t, (5.0, 1.5))  # off to the kitchen
    t.clock.advance(OCCUPANCY_TIMEOUT_S + 15)
    assert t.state("hall_radar")["presence"] is False


def test_a_tasmota_lamp_and_plug_are_switched_by_hand_through_their_power(tmp_path):
    """A Tasmota device's on/off is POWER (POWER1 on a power strip), as Tasmota names its relays."""
    from tests.lab.homes import catalog
    from vesper.lab.rules import is_on, toggle_command
    from vesper.twins.clock import LogicalClock
    from vesper.twins.runtime import Runtime
    rt = Runtime(clock=LogicalClock())
    lamp = rt.twins[rt.add(catalog()["tasmota:brite:brite_dimmer_d1"]).id]
    strip = rt.twins[rt.add(catalog()["tasmota:acme:acme_strip_s4"]).id]
    assert toggle_command(lamp) == ("operate", "POWER", "TOGGLE") and is_on(lamp) is False
    assert toggle_command(strip) == ("operate", "POWER1", "TOGGLE") and is_on(strip) is False
