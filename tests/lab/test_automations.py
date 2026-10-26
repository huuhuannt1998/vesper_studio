"""VESPER's built-in automation engine (spec §5b): a home's Home Assistant automations, run without Home Assistant
on the logical clock, seeing what Home Assistant would see (the devices' reports) and acting as it would (the
commands its MQTT entities send)."""
import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.automations import entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.rules import is_on
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock

H = 3600


def lab(tmp_path, automations: str, start="07:00"):
    (tmp_path / "automations.yaml").write_text(automations)
    text = HOME.replace('start_time: "07:30"', f'start_time: "{start}"') + "    automations: automations.yaml\n"
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
