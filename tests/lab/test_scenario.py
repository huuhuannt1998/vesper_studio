"""The scenario file (security-lab spec §4.1, plan W4a1): `vesper-scenario/1`, checked against its home before
anything runs, every name a device, link, door, room or automation of the home, and every mistake named where it is."""
import textwrap

import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.scenario import ScenarioError, load_scenario

# The test house with a Zigbee lock on the front door instead of the Matter one (Phantom-Delay's Case 3)
LOCKED_HOME = HOME.replace('''      - {product: "matter:115f:0001", name: front_lock, at: {door: hall-porch},
         state: {"Door Lock.LockState": Unlocked}}
''', '''      - {product: "zigbee:acme:lock-1", name: front_lock, at: {door: hall-porch}, state: {state: UNLOCK}}
''')

SCENARIO = """\
    format: vesper-scenario/1
    title: The lock-on-close action is delayed and an intruder walks in
    source:
      paper: {title: "IoT Phantom-Delay Attacks", venue: "DSN 2022", doi: 10.1109/DSN53405.2022.00050}
      passages: {attack: "we can delay the following locking command for a period between 30 seconds to 58 seconds"}
    home: home.yaml
    setup:
      automations: automations.yaml
    actors:
      - {name: intruder, model: male_1, start: {room: porch}}
    timeline:
      - {at: "07:31:00", actor: player, do: {close: hall-porch}}
      - {at: "07:31:20", actor: intruder, do: {open: hall-porch}, cites: [attack]}
    attack:
      - {at: 30, vantage: network, effect: delay, link: coordinator, direction: down,
         match: {device: front_lock, feature: state}, hold_s: 58, keep_connection: true, cites: [attack]}
    defenses:
      - {kind: freshness, max_age_s: 10, action: alert}
    expect:
      - claim: the intruder opens the door before it is locked
        predicate: {before: [{actor: intruder, opens: hall-porch}, {action: {device: front_lock, feature: state,
                                                                             value: LOCK}}]}
        conditions: {baseline: false, attack: true, attack+defense: true}
        cites: [attack]
    runs: {mode: logical, repeat: 3, conditions: [baseline, attack, attack+defense]}
    gaps:
      - {what: "the cloud leg", why: "VESPER has no cloud; the hub leg instead"}
"""


def write(tmp_path, text=SCENARIO, home=LOCKED_HOME):
    write_home(tmp_path, home)
    (tmp_path / "automations.yaml").write_text("[]\n")
    path = tmp_path / "scenario.yaml"
    path.write_text(textwrap.dedent(text))
    return path


def test_a_scenario_loads_with_its_home_its_times_in_seconds_after_the_start_and_its_steps_checked(tmp_path):
    s = load_scenario(write(tmp_path), catalog())
    assert s.title.startswith("The lock-on-close") and s.home.start_time == 7.5 * 3600
    assert s.home.automations == tmp_path / "automations.yaml"
    assert [(a.at, a.actor, a.do) for a in s.timeline] == [(60.0, "player", {"close": "hall-porch"}),
                                                           (80.0, "intruder", {"open": "hall-porch"})]
    (effect,) = s.attack
    assert (effect.at, effect.effect.kind, effect.effect.link, effect.effect.device, effect.effect.feature,
            effect.effect.hold_s, effect.effect.keep_connection) == (30.0, "delay", "coordinator", "front_lock",
                                                                     "state", 58.0, True)
    assert [d.kind for d in s.defenses] == ["freshness"] and s.defenses[0].max_age_s == 10
    assert s.runs.mode == "logical" and s.runs.repeat == 3 and s.runs.conditions == [
        "baseline", "attack", "attack+defense"]
    assert s.actors["intruder"].model == "male_1" and s.claims[0].conditions["attack"] is True
    assert s.gaps == [{"what": "the cloud leg", "why": "VESPER has no cloud; the hub leg instead"}]


def test_setup_places_more_devices_in_the_home_for_this_scenario_only(tmp_path):
    text = SCENARIO.replace("      automations: automations.yaml\n", "      automations: automations.yaml\n"
                            '      devices: [{product: "zigbee:acme:pir-1", name: porch_motion, at: {room: porch, '
                            'mount: ceiling}}]\n')
    s = load_scenario(write(tmp_path, text), catalog())
    assert "porch_motion" in {d.name for d in s.home.devices}
    assert "porch_motion" not in (tmp_path / "home.yaml").read_text()


@pytest.mark.parametrize("old, new, message", [
    ("format: vesper-scenario/1", "format: vesper-scenario/9", r"format: 'vesper-scenario/9' is not vesper-scenario/1"),
    ("link: coordinator", "link: coordinatr",
     r"attack\[0\]\.link: unknown link 'coordinatr'; did you mean coordinator"),
    ("device: front_lock, feature: state}, hold_s", "device: front_lok, feature: state}, hold_s",
     r"attack\[0\]\.match\.device: unknown device 'front_lok'; did you mean front_lock"),
    ("feature: state}, hold_s", "feature: colour}, hold_s",
     r"attack\[0\]\.match\.feature: front_lock has no feature 'colour'"),
    ("do: {open: hall-porch}", "do: {open: hall-porch-x}", r"timeline\[1\]\.do\.open: unknown doorway 'hall-porch-x'"),
    ('at: "07:31:20"', 'at: "7.31"', r"timeline\[1\]\.at: '7.31' is not HH:MM:SS"),
    ('at: "07:31:20"', 'at: "07:00:00"', r"timeline\[1\]\.at: 07:00:00 is before the home's start \(07:30:00\)"),
    ("actor: intruder, do", "actor: burglar, do", r"timeline\[1\]\.actor: unknown actor 'burglar'"),
    ("vantage: network", "vantage: broker", r"attack\[0\]\.vantage: 'broker' comes with plan W4a2"),
    ("effect: delay", "effect: jam", r"attack\[0\]\.effect: 'jam' is not one of delay, drop, duplicate, reorder"),
    ("kind: freshness", "kind: broker_acl", r"defenses\[0\]\.kind: 'broker_acl' comes with plan W4a2"),
    ("kind: freshness", "kind: firewall", r"defenses\[0\]\.kind: 'firewall' is not a defense"),
    ("mode: logical", "mode: quantum", r"runs\.mode: 'quantum' is not logical or live"),
    ("conditions: [baseline, attack, attack+defense]}", "conditions: [baseline, sabotage]}",
     r"runs\.conditions: 'sabotage' is not baseline, attack or attack\+defense"),
    ("{action: {device: front_lock, feature: state,", "{action: {device: front_lok, feature: state,",
     r"expect\[0\]\.predicate\.before\[1\]\.action\.device: unknown device 'front_lok'"),
    ("{actor: intruder, opens: hall-porch}", "{actor: burglar, opens: hall-porch}",
     r"expect\[0\]\.predicate\.before\[0\]\.actor: unknown actor 'burglar'"),
    ("{baseline: false, attack: true, attack+defense: true}", "{attack: true}",
     r"expect\[0\]\.conditions: say what is expected under baseline, attack\+defense"),
])
def test_every_mistake_is_named_where_it_is(tmp_path, old, new, message):
    assert old in SCENARIO
    with pytest.raises(ScenarioError, match=message):
        load_scenario(write(tmp_path, SCENARIO.replace(old, new, 1)), catalog())


def test_a_scenario_never_names_an_address_or_a_port(tmp_path):
    """Safety boundary (spec §7): steps name the home's devices and links, never a host or a port."""
    text = SCENARIO.replace("hold_s: 58,", "hold_s: 58, host: 192.168.1.10,")
    with pytest.raises(ScenarioError, match=r"attack\[0\]: 'host' is not a step's field"):
        load_scenario(write(tmp_path, text), catalog())


def test_setup_automations_are_found_from_a_relative_scenario_path(tmp_path, monkeypatch):
    """`vesper lab scenario run examples/...` passes a relative path: the automations sit beside the scenario."""
    write(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    s = load_scenario(tmp_path.relative_to(tmp_path.parent) / "scenario.yaml", catalog())
    assert s.home.automations.is_file()


def test_a_missing_automations_file_is_named_before_anything_runs(tmp_path):
    path = write(tmp_path)
    (tmp_path / "automations.yaml").unlink()
    with pytest.raises(ScenarioError, match=r"setup\.automations: .*automations\.yaml does not exist"):
        load_scenario(path, catalog())


def test_a_run_mode_given_at_load_overrides_the_file_s_and_is_checked_as_the_file_s_would_be(tmp_path):
    path = write(tmp_path)
    assert load_scenario(path, catalog(), mode="live").runs.mode == "live"
    with pytest.raises(ScenarioError, match=r"mode: 'quantum' is not logical or live"):
        load_scenario(path, catalog(), mode="quantum")


def test_a_live_run_s_freshness_alerts_and_cannot_drop(tmp_path):
    """At Home Assistant's edge VESPER watches what arrives; taking a message away from Home Assistant is W4a2's."""
    path = write(tmp_path, SCENARIO.replace("action: alert}", "action: drop}"))
    assert load_scenario(path, catalog()).defenses[0].action == "drop"  # logical: the hub's edge is VESPER's
    with pytest.raises(ScenarioError, match=r"defenses\[0\]\.action: a live run's freshness alerts; dropping"):
        load_scenario(path, catalog(), mode="live")
