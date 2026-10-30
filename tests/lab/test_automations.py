"""VESPER's built-in automation engine (spec §5b): a home's Home Assistant automations, run without Home Assistant
on the logical clock, seeing what Home Assistant would see (the devices' reports) and acting as it would (the
commands its MQTT entities send)."""
import pytest

from tests.lab.homes import HOME, WIFI_DEVICES, catalog, write_home
from vesper.lab.automations import entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.rules import is_on
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock

H = 3600


PLUG = ('      - {product: "zigbee:acme:plug-1", name: kitchen_plug, at: {room: kitchen, xy: [4.0, 0.5], '
        'mount: floor}}\n')


def lab(tmp_path, automations: str, start="07:00", extra=""):
    (tmp_path / "automations.yaml").write_text(automations)
    text = HOME.replace('start_time: "07:30"', f'start_time: "{start}"') + extra + "    automations: automations.yaml\n"
    home = load_lab_home(write_home(tmp_path, text), catalog())
    ids = entity_ids(home)
    s = Session(home, clock=LogicalClock())
    return home, ids, s


def lamp_on(s):
    return is_on(s.rules.twin("bedroom_lamp"))


def fired(s):
    """The automation engine's commands in a session or in a list of trace records."""
    return [r for r in getattr(s, "records", s) if r["kind"] == "automation"]


HALL_LAMP = """\
- id: hall_lamp
  alias: Bedroom lamp on when someone is in the hall
  triggers:
    - trigger: state
      entity_id: {motion}
      to: "on"
  actions:
    - action: light.turn_on
      target:
        entity_id: {lamp}
"""


def test_entities_are_named_as_home_assistant_names_them_through_the_bridge(tmp_path):
    home, ids, _ = lab(tmp_path, "[]\n")
    assert ids["hall_motion"]["occupancy"].startswith("binary_sensor.v") and ids["bedroom_lamp"]["state"].startswith(
        "light.v")
    assert "front_lock" not in ids  # Matter: not on VESPER's hub yet (plan W2d)


def test_walking_into_the_hall_lets_the_automation_turn_the_lamp_on(tmp_path):
    _, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    assert s.records[0]["automations"]["engine"] == "vesper" and s.records[0]["automations"]["loaded"] == [
        "Bedroom lamp on when someone is in the hall"]
    s.spawn("player", "female_0", (5.0, 1.0))  # in the kitchen
    assert not lamp_on(s)
    s.walk_to((1.5, 3.0))  # into the hall
    s.clock.advance(10)
    assert lamp_on(s)
    (f,) = fired(s)
    assert (f["automation"], f["device"], f["feature"], f["value"], f["accepted"]) == (
        "Bedroom lamp on when someone is in the hall", "bedroom_lamp", "state", "ON", True)
    assert not [r for r in s.records if r["kind"] == "input" and r["actor"] == "hub"]  # not a hub input


def test_conditions_for_and_time_triggers_follow_home_assistant(tmp_path):
    _, ids, s = lab(tmp_path, "", start="06:58")
    motion, lamp = ids["hall_motion"]["occupancy"], ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: morning light
  triggers: [{{trigger: time, at: "07:00:00"}}]
  actions:
    - {{action: light.turn_on, target: {{entity_id: {lamp}}}}}
    - {{delay: "00:05:00"}}
    - {{action: light.turn_off, target: {{entity_id: {lamp}}}}}
- alias: hall lamp at night only
  triggers: [{{trigger: state, entity_id: {motion}, to: "on", for: {{minutes: 1}}}}]
  conditions: [{{condition: time, after: "20:00:00"}}]
  actions: [{{action: light.toggle, target: {{entity_id: {lamp}}}}}]
""")
    s.start(spawn_player=True, automations=True)
    s.clock.advance(2 * 60 + 1)  # 07:00:01
    assert lamp_on(s)
    s.clock.advance(5 * 60)  # 07:05:01: the delay ran out
    assert not lamp_on(s)
    s.walk_to((1.5, 3.0))
    s.clock.advance(3 * 60)
    assert not lamp_on(s)  # in the hall for minutes, but before 20:00
    assert [f["automation"] for f in fired(s)] == ["morning light", "morning light"]


def test_an_automation_the_engine_cannot_run_is_refused_by_name_and_the_others_still_run(tmp_path):
    _, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]) + """\
- alias: at sunset
  triggers: [{trigger: sun, event: sunset}]
  actions: [{action: light.turn_on, target: {entity_id: light.nowhere}}]
- alias: unknown lamp
  triggers: [{trigger: time, at: "08:00"}]
  actions: [{action: light.turn_on, target: {entity_id: light.nowhere}}]
""")
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 1.0))
    refused = s.records[0]["automations"]["refused"]
    assert refused == [{"automation": "at sunset", "reason": "trigger 'sun' is not supported"},
                       {"automation": "unknown lamp", "reason": "entity light.nowhere is not one of this home's"}]
    s.walk_to((1.5, 3.0))
    s.clock.advance(10)
    assert lamp_on(s)


def test_a_day_with_automations_replays_exactly_without_doubling_them(tmp_path):
    home, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 1.0))
    s.walk_to((1.5, 3.0))
    s.clock.advance(120)
    s.stop()
    again = replay(s.records, home)
    assert compare_reports(s.records, again) is None
    assert len(fired(again)) == len(fired(s)) == 1


@pytest.mark.parametrize("bad, message", [
    ("- alias: x\n  triggers: [{trigger: state, entity_id: binary_sensor.a, to: 'on'}]\n"
     "  actions: []\n  mode: queued\n", "mode 'queued' is not supported"),
    ("{not: a list}\n", "automations.yaml: a list of automations"),
])
def test_a_file_the_engine_cannot_read_is_named(tmp_path, bad, message):
    _, _, s = lab(tmp_path, bad)
    s.start(automations=True)
    meta = s.records[0]["automations"]
    assert any(message in r["reason"] for r in meta["refused"])


def test_a_for_starts_again_when_the_state_changes_before_it_has_held(tmp_path):
    _, ids, s = lab(tmp_path, "")
    lamp = ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: lamp off after five minutes
  triggers: [{{trigger: state, entity_id: {lamp}, to: "on", for: "00:05:00"}}]
  actions: [{{action: light.turn_off, target: {{entity_id: {lamp}}}}}]
""")
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 4.4))  # by the lamp
    s.interact("bedroom_lamp")  # on at 0 min
    s.wait(120)
    s.interact("bedroom_lamp")  # off at 2 min
    s.wait(60)
    s.interact("bedroom_lamp")  # on again at 3 min
    s.wait(4 * 60 + 30)  # 7.5 min: on for 4.5 min only
    assert lamp_on(s) and not fired(s)
    s.wait(60)  # 8.5 min: on for 5.5 min
    assert not lamp_on(s) and len(fired(s)) == 1


def test_mode_single_drops_a_trigger_while_the_automation_runs(tmp_path):
    _, ids, s = lab(tmp_path, "", start="06:58")
    lamp = ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: blink
  triggers: [{{trigger: time, at: "07:00"}}, {{trigger: time, at: "07:01"}}]
  actions:
    - {{action: light.turn_on, target: {{entity_id: {lamp}}}}}
    - {{delay: {{minutes: 2}}}}
    - {{action: light.turn_off, target: {{entity_id: {lamp}}}}}
""")
    s.start(spawn_player=False, automations=True)
    s.wait(6 * 60)
    assert [r["value"] for r in fired(s)] == ["ON", "OFF"]
    (skip,) = [r for r in s.records if r["kind"] == "automation_skipped"]
    assert (skip["automation"], skip["reason"]) == ("blink", "already running (mode single)")


def quiet(tmp_path, write, start="07:00", extra=""):
    """A session with the engine whose rules do not run (no ambient tick): the test drives the readings."""
    _, ids, s = lab(tmp_path, "", start=start, extra=extra)
    (tmp_path / "automations.yaml").write_text(write(ids))
    s.rules.start = lambda: None
    s.start(spawn_player=False, automations=True)
    return ids, s


def measure(s, device, feature, value):
    s.runtime.stimulate(s.twin_ids[device], {"type": "measured", "feature": feature, "value": value})


def test_a_change_another_automation_makes_at_the_same_instant_stops_a_for(tmp_path):
    """Home Assistant evaluates every trigger of a state change before any action runs; the second automation's
    `for:` then sees the lamp switched off again at once, and never fires (final review, W3b)."""
    _, ids, s = lab(tmp_path, "")
    lamp = ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: never leave the lamp on
  triggers: [{{trigger: state, entity_id: {lamp}, to: "on"}}]
  actions: [{{action: light.turn_off, target: {{entity_id: {lamp}}}}}]
- alias: lamp on for five minutes
  triggers: [{{trigger: state, entity_id: {lamp}, to: "on", for: "00:05:00"}}]
  actions: [{{action: light.turn_off, target: {{entity_id: {lamp}}}}}]
""")
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 4.4))
    s.interact("bedroom_lamp")
    s.wait(10 * 60)
    assert [f["automation"] for f in fired(s)] == ["never leave the lamp on"]


def test_a_numeric_state_for_holds_while_the_value_stays_in_range(tmp_path):
    ids, s = quiet(tmp_path, lambda ids: f"""\
- alias: hot kitchen
  triggers: [{{trigger: numeric_state, entity_id: {ids["kitchen_climate"]["temperature"]}, above: 25,
               for: "00:10:00"}}]
  actions: [{{action: light.toggle, target: {{entity_id: {ids["bedroom_lamp"]["state"]}}}}}]
""")
    for value in (20, 26, 27, 27.5):  # into the range at 120 s, then moving inside it
        measure(s, "kitchen_climate", "temperature", value)
        s.wait(120)
    s.wait(10 * 60)
    measure(s, "kitchen_climate", "temperature", 26)  # in range since 120 s: it has fired; 26 is no new entry
    s.wait(5 * 60)
    measure(s, "kitchen_climate", "temperature", 20)  # out
    measure(s, "kitchen_climate", "temperature", 26)  # in again
    s.wait(5 * 60)
    measure(s, "kitchen_climate", "temperature", 24)  # out before ten minutes: no second firing
    s.wait(20 * 60)
    assert [round(f["t"]) for f in fired(s)] == [720]


def test_every_press_of_a_button_changes_its_event_entity(tmp_path):
    """An event entity's state is the time of its last event, so every press is a state change (final review)."""
    ids, s = quiet(tmp_path, lambda ids: f"""\
- alias: button toggles the plug
  triggers: [{{trigger: state, entity_id: {ids["bedroom_button"]["action"]}}}]
  actions: [{{action: switch.toggle, target: {{entity_id: {ids["kitchen_plug"]["state"]}}}}}]
""", extra=PLUG)
    s.spawn("player", "female_0", (3.5, 3.6))  # by the bedroom button
    for _ in range(3):
        s.interact("bedroom_button")
        s.wait(30)
    assert [f["value"] for f in fired(s)] == ["ON", "OFF", "ON"]


def test_to_null_is_any_change_and_a_disabled_trigger_never_fires(tmp_path):
    ids, s = quiet(tmp_path, lambda ids: f"""\
- alias: any lamp change
  triggers: [{{trigger: state, entity_id: {ids["bedroom_lamp"]["state"]}, to: ~}}]
  actions: [{{action: switch.toggle, target: {{entity_id: {ids["kitchen_plug"]["state"]}}}}}]
- alias: disabled
  triggers: [{{trigger: state, entity_id: {ids["bedroom_lamp"]["state"]}, enabled: false}}]
  actions: [{{action: switch.toggle, target: {{entity_id: {ids["kitchen_plug"]["state"]}}}}}]
""", extra=PLUG)
    s.spawn("player", "female_0", (5.0, 4.4))
    s.interact("bedroom_lamp")
    s.wait(30)
    s.interact("bedroom_lamp")
    s.wait(30)
    assert [f["automation"] for f in fired(s)] == ["any lamp change", "any lamp change"]


REFUSED = [
    ("data: {brightness_pct: \"{{ states('input_number.dim') }}\"}",
     "action light.turn_on: data brightness_pct: \"{{ states('input_number.dim') }}\" is not a number"),
    ("data_template: {brightness: 100}", "action light.turn_on: 'data_template' is not supported"),
]


@pytest.mark.parametrize("automation, reason", [
    ("triggers: [{trigger: state, entity_id: LAMP, to: \"on\", for: {minutes: \"{{ x }}\"}}]\n  actions: []",
     "for: {'minutes': '{{ x }}'} is not a duration"),
    ("triggers: [{trigger: numeric_state, entity_id: TEMP, above: input_number.hot}]\n  actions: []",
     "trigger numeric_state: above: 'input_number.hot' is not a number"),
    ("triggers: [{trigger: state, entity_id: LAMP, not_to: \"on\"}]\n  actions: []",
     "trigger state: 'not_to' is not supported"),
    ("triggers: [{trigger: state, entity_id: LAMP, attribute: brightness}]\n  actions: []",
     "trigger state: 'attribute' is not supported"),
    ("triggers: [{trigger: state, to: \"on\"}]\n  actions: []", "trigger state: entity_id is required"),
    ("triggers: [{trigger: state, entity_id: LAMP, to: on}]\n  actions: []",
     "trigger state: to: True is not a quoted state; Home Assistant refuses YAML's unquoted on/off"),
    ("triggers: [{trigger: time, at: \"07:00\"}]\n  conditions: [{condition: state, entity_id: LAMP, "
     "state: \"on\", for: \"00:01:00\"}]\n  actions: []", "condition state: 'for' is not supported"),
    ("triggers: [{trigger: time, at: \"07:00\"}]\n  actions: [{action: light.turn_on, target: {entity_id: LAMP}, "
     + REFUSED[0][0] + "}]", REFUSED[0][1]),
    ("triggers: [{trigger: time, at: \"07:00\"}]\n  actions: [{action: light.turn_on, target: {entity_id: LAMP}, "
     + REFUSED[1][0] + "}]", REFUSED[1][1]),
    ("use_blueprint: {path: homeassistant/motion_light.yaml}", "blueprint automations are not supported"),
    ("triggers: [{trigger: time, at: \"07:00\"}]\n  variables: {x: 1}\n  actions: []", "'variables' is not supported"),
])
def test_what_the_engine_cannot_run_is_refused_as_a_whole_and_the_day_goes_on(tmp_path, automation, reason):
    _, ids, s = lab(tmp_path, "")
    text = ("- alias: bad\n  " + automation + "\n").replace("LAMP", ids["bedroom_lamp"]["state"]).replace(
        "TEMP", ids["kitchen_climate"]["temperature"])
    (tmp_path / "automations.yaml").write_text(text + HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                       lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    meta = s.records[0]["automations"]
    assert meta["refused"] == [{"automation": "bad", "reason": reason}] and len(meta["loaded"]) == 1
    s.spawn("player", "female_0", (5.0, 1.0))
    s.walk_to((1.5, 3.0))
    s.wait(3600)  # the hour runs; the good automation still works
    assert lamp_on(s)


def test_conditions_come_before_mode_single_and_an_empty_time_window_is_always_true(tmp_path):
    """Home Assistant checks an automation's conditions before it finds the automation running; a time condition
    whose after equals its before holds all day."""
    _, ids, s = lab(tmp_path, "", start="06:58")
    lamp = ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: blink once
  triggers: [{{trigger: time, at: "07:00"}}, {{trigger: time, at: "07:01"}}]
  conditions: [{{condition: time, before: "07:00:30"}}]
  actions:
    - {{action: light.turn_on, target: {{entity_id: {lamp}}}}}
    - {{delay: {{minutes: 2}}}}
    - {{action: light.turn_off, target: {{entity_id: {lamp}}}}}
- alias: always
  triggers: [{{trigger: time, at: "07:05"}}]
  conditions: [{{condition: time, after: "12:00", before: "12:00"}}]
  actions: [{{action: light.turn_on, target: {{entity_id: {lamp}}}, data: {{brightness_pct: 0}}}}]
""")
    s.start(spawn_player=False, automations=True)
    s.wait(10 * 60)
    assert not [r for r in s.records if r["kind"] == "automation_skipped"]
    assert [(f["automation"], f["value"]) for f in fired(s)] == [("blink once", "ON"), ("blink once", "OFF"),
                                                                ("always", "OFF")]  # brightness 0 turns it off


WIFI_ON = """\
- id: morning
  alias: Kettle on and the reading lamp at half when someone is in the hall
  triggers:
    - trigger: state
      entity_id: {motion}
      to: "on"
  actions:
    - action: switch.turn_on
      target:
        entity_id: switch.kettle_plug
    - action: light.turn_on
      target:
        entity_id: light.reading_lamp
      data:
        brightness_pct: 50
"""


def test_a_wi_fi_device_s_entities_are_named_and_commanded_as_home_assistant_s_tasmota_integration_does(tmp_path):
    """The engine sends what the device would receive from Home Assistant: POWER, and a Dimmer scaled to 1..100,
    which lights it, as Tasmota does."""
    _, ids, s = lab(tmp_path, "", extra="")
    (tmp_path / "automations.yaml").write_text(WIFI_ON.format(motion=ids["hall_motion"]["occupancy"]))
    text = HOME.replace("    devices:\n", "    devices:\n" + WIFI_DEVICES, 1) + "    automations: automations.yaml\n"
    home = load_lab_home(write_home(tmp_path, text), catalog())
    ids = entity_ids(home)
    assert ids["kettle_plug"] == {"POWER": "switch.kettle_plug"}
    assert ids["reading_lamp"] == {"POWER": "light.reading_lamp"}
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, automations=True)
    assert s.automations.refused == []
    s.spawn("player", "female_0", (5.0, 1.0))  # in the kitchen
    s.walk_to((1.5, 3.0))  # into the hall
    s.clock.advance(10)
    kettle, lamp = s.runtime.state(s.twin_ids["kettle_plug"]), s.runtime.state(s.twin_ids["reading_lamp"])
    assert kettle["POWER"] == "ON" and (lamp["Dimmer"], lamp["POWER"]) == (50, "ON")
    assert {(f["device"], f["feature"], f["value"]) for f in fired(s)} == {
        ("kettle_plug", "POWER", "ON"), ("reading_lamp", "Dimmer", 50), ("reading_lamp", "POWER", "ON")}
