"""Running a scenario (security-lab spec §4.5, plan W4a1): each condition, each repeat, its own session and trace;
the attack's effects at their times, the defenses in the attack+defense condition only; the claims checked run by
run. Phantom-Delay's Case 3 on the test house, on the logical clock: the resident leaves and closes the door, an
automation locks it; with the door's report held, an intruder walks in first."""
import json
import textwrap

from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.automations import entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.scenario import load_scenario
from vesper.lab.scenario_run import run_scenario

LOCK_ON_CLOSE = """\
- id: lock_on_close
  alias: Lock the front door when it closes
  triggers:
    - trigger: state
      entity_id: {door}
      to: "off"
  actions:
    - action: lock.lock
      target:
        entity_id: {lock}
"""

CASE_3 = """\
    format: vesper-scenario/1
    title: Case 3, the lock-on-close action delayed
    source:
      paper: {title: "IoT Phantom-Delay Attacks", venue: "DSN 2022"}
      passages: {attack: "the length of the attacking window can be extended to be at least 60 seconds"}
    home: home.yaml
    setup: {automations: automations.yaml}
    actors:
      - {name: intruder, model: male_1, start: {room: porch}}
    timeline:
      - {at: 5, actor: player, do: {open: hall-porch}}
      - {at: 15, actor: player, do: {walk: {room: porch}}}
      - {at: 25, actor: player, do: {close: hall-porch}}
      - {at: 40, actor: player, do: {walk: {room: porch}}}
      - {at: 50, actor: intruder, do: {open: hall-porch}, cites: [attack]}
    attack:
      - {at: 1, vantage: network, effect: delay, link: coordinator, direction: up,
         match: {device: front_door, feature: contact}, hold_s: 60, keep_connection: true, cites: [attack]}
    defenses: [{kind: freshness, max_age_s: 10, action: alert}, {kind: physical_consistency, within_s: 5}]
    expect:
      - claim: the intruder opens the door before it is locked
        predicate: {before: [{actor: intruder, opens: hall-porch},
                             {action: {device: front_lock, feature: state, value: LOCK}}]}
        conditions: {baseline: false, attack: true, attack+defense: true}
        cites: [attack]
      - claim: freshness flags the held report
        predicate: {alert: {defense: freshness, device: front_door}}
        conditions: {baseline: false, attack: false, attack+defense: true}
      - claim: the world disagrees with the hub while the report is held
        predicate: {alert: {defense: physical_consistency, device: front_door}}
        conditions: {baseline: false, attack: false, attack+defense: true}
    runs: {mode: logical, repeat: 2, conditions: [baseline, attack, attack+defense], until: 200}
"""


def scenario(tmp_path, text=CASE_3):
    write_home(tmp_path, LOCKED_HOME)
    ids = entity_ids(load_lab_home(tmp_path / "home.yaml", catalog()))
    (tmp_path / "automations.yaml").write_text(LOCK_ON_CLOSE.format(door=ids["front_door"]["contact"],
                                                                    lock=ids["front_lock"]["state"]))
    (tmp_path / "scenario.yaml").write_text(textwrap.dedent(text))
    return load_scenario(tmp_path / "scenario.yaml", catalog())


def holds(runs, condition, claim=0):
    return [r.verdicts[claim].holds for r in runs if r.condition == condition]


def test_case_3_reproduces_the_intruder_gets_in_only_when_the_door_s_report_is_held(tmp_path):
    runs = run_scenario(scenario(tmp_path), tmp_path / "out")
    assert [(r.condition, r.repeat) for r in runs] == [(c, k) for c in ("baseline", "attack", "attack+defense")
                                                      for k in (1, 2)]
    assert holds(runs, "baseline") == [False, False]  # the lock wins: the intruder finds the door locked
    assert holds(runs, "attack") == [True, True]
    assert holds(runs, "attack+defense") == [True, True]  # freshness detects, it does not prevent (paper §VII-B)
    assert holds(runs, "attack+defense", 1) == [True, True] and holds(runs, "attack", 1) == [False, False]
    assert holds(runs, "attack+defense", 2) == [True, True] and holds(runs, "baseline", 2) == [False, False]


def test_each_run_keeps_its_own_trace_and_the_attack_is_an_input_of_it(tmp_path):
    runs = run_scenario(scenario(tmp_path), tmp_path / "out")
    traces = {r.trace for r in runs}
    assert len(traces) == 6 and all(t.parent == tmp_path / "out" for t in traces)
    attack = [json.loads(line) for line in runs[2].trace.read_text().splitlines()]
    effects = [r for r in attack if r["kind"] == "input" and r["op"] == "effect"]
    assert len(effects) == 1 and effects[0]["t"] == 1.0 and effects[0]["args"]["device"] == "front_door"
    baseline = [json.loads(line) for line in runs[0].trace.read_text().splitlines()]
    assert not [r for r in baseline if r["kind"] == "input" and r["op"] == "effect"]
    assert runs[0].trace.name == "baseline-1.jsonl" and runs[0].seed != runs[1].seed


def test_an_intruder_without_a_key_finds_the_locked_door_locked_and_does_not_unlock_it(tmp_path):
    runs = run_scenario(scenario(tmp_path), tmp_path / "out")
    tries = [r for r in runs[0].records if r["kind"] == "input" and r["actor"] == "intruder" and r["op"] == "interact"]
    assert tries and all(r["args"]["target"] == "hall-porch" for r in tries)
    assert tries[-1]["outcome"]["result"] == "locked"


def test_the_report_lists_the_gaps_first_then_each_claim_reproduced_or_not_with_its_deciding_records(tmp_path):
    from vesper.lab.scenario_run import report
    s = scenario(tmp_path, CASE_3 + '    gaps:\n      - {what: "the cloud leg", why: "VESPER has no cloud"}\n')
    text, summary = report(s, run_scenario(s, tmp_path / "out"))
    assert text.index("## Gaps") < text.index("## Claims") and "the cloud leg: VESPER has no cloud" in text
    assert "the intruder opens the door before it is locked: **reproduced**" in text
    assert "| attack | holds | 2/2 |" in text and "| baseline | does not hold | 0/2 |" in text
    assert "intruder interact hall-porch: opened" in text  # a deciding record, in words
    assert [c["reproduced"] for c in summary["claims"]] == [True, True, True]
    assert summary["claims"][0]["conditions"]["attack"] == {"expected": True, "held": 2, "runs": 2}


def test_a_claim_whose_expectation_fails_says_which_condition_failed_it(tmp_path):
    from vesper.lab.scenario_run import report
    text_in = CASE_3.replace("conditions: {baseline: false, attack: true, attack+defense: true}\n        cites",
                             "conditions: {baseline: true, attack: true, attack+defense: true}\n        cites")
    s = scenario(tmp_path, text_in)
    text, summary = report(s, run_scenario(s, tmp_path / "out"))
    assert "the intruder opens the door before it is locked: **not reproduced** (baseline: expected to hold, held " \
           "in 0 of 2)" in text
    assert summary["claims"][0]["reproduced"] is False


def test_an_automation_the_engine_refuses_is_named_in_the_report_before_the_claims(tmp_path):
    """A victim automation that did not load would leave every claim about it silently unreproduced."""
    from vesper.lab.scenario_run import report
    s = scenario(tmp_path)
    (tmp_path / "automations.yaml").write_text("- alias: broken\n  triggers: [{trigger: sun}]\n  actions: []\n")
    text, summary = report(s, run_scenario(s, tmp_path / "out"))
    assert text.index("## Automations refused") < text.index("## Claims") and "broken" in text
    assert summary["refused"] and summary["refused"][0]["automation"] == "broken"


def test_an_intruder_without_a_key_never_unlocks_a_lock_even_on_its_way_through_a_door(tmp_path):
    """A walk through a closed door unlocks its lock for a resident, who has a key; not for an intruder."""
    text = CASE_3.replace("      - {at: 50, actor: intruder, do: {open: hall-porch}, cites: [attack]}\n",
                          "      - {at: 50, actor: intruder, do: {open: hall-porch}, cites: [attack]}\n"
                          "      - {at: 55, actor: intruder, do: {walk: {room: hall}}}\n")
    runs = run_scenario(scenario(tmp_path, text), tmp_path / "out")
    baseline = runs[0].records
    by_intruder = [r for r in baseline if r["kind"] == "input" and r["actor"] == "intruder" and r["op"] == "interact"]
    assert not [r for r in by_intruder if r["args"]["target"] == "front_lock"]
    assert not [r for r in by_intruder if r["outcome"].get("result") == "opened"]
    assert not [r for r in baseline if r["kind"] == "world" and r.get("event") == "room" and r["actor"] == "intruder"
                and r["room"] == "hall"]


def test_an_intruder_without_a_key_walks_through_a_closed_door_whose_lock_is_unlocked(tmp_path):
    """No key is needed for an unlocked lock: the walk opens the door and goes through."""
    text = CASE_3.replace("      - {at: 50, actor: intruder, do: {open: hall-porch}, cites: [attack]}\n",
                          "      - {at: 50, actor: intruder, do: {walk: {room: hall}}}\n")
    s = scenario(tmp_path, text)
    (tmp_path / "automations.yaml").write_text("[]\n")  # nothing locks the door
    runs = run_scenario(load_scenario(tmp_path / "scenario.yaml", catalog()), tmp_path / "out")
    assert [r for r in runs[0].records if r["kind"] == "world" and r.get("event") == "room"
            and r["actor"] == "intruder" and r["room"] == "hall"], s.title


def test_a_walk_stopped_by_a_locked_door_leaves_the_actor_s_next_actions_in_place(tmp_path):
    text = CASE_3.replace("      - {at: 50, actor: intruder, do: {open: hall-porch}, cites: [attack]}\n",
                          "      - {at: 50, actor: intruder, do: {walk: {room: hall}}}\n"
                          "      - {at: 50.5, actor: intruder, do: {open: hall-porch}}\n")
    runs = run_scenario(scenario(tmp_path, text), tmp_path / "out")
    baseline = runs[0].records
    assert [r for r in baseline if r["kind"] == "resident" and r.get("decision") == "no key"]
    assert [r for r in baseline if r["kind"] == "input" and r["actor"] == "intruder" and r["op"] == "interact"
            and r["args"]["target"] == "hall-porch"]


def _trace(path, records):
    from vesper.lab.session import TRACE_FORMAT
    meta = {"kind": "meta", "format": TRACE_FORMAT, "clock": "wall", "seed": 7, "t": 0.0}
    path.write_text("".join(json.dumps(r) + "\n" for r in [meta, *records]))


LOCK_SENT = {"kind": "input", "actor": "hub", "op": "command",
             "args": {"device": "front_lock", "feature": "state", "value": "LOCK"}, "outcome": {"accepted": True}}
INTRUDER_OPENS = {"kind": "input", "actor": "intruder", "op": "interact", "args": {"target": "hall-porch"},
                  "outcome": {"ok": True, "result": "opened"}}


def test_a_live_run_is_judged_from_its_t0_not_on_what_home_assistant_did_while_getting_ready(tmp_path):
    """Before t0 Home Assistant reacts to the new session's first states (here: it locks the door); that is not
    the run."""
    from vesper.lab.scenario_run import rejudge
    s = scenario(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    for condition in s.runs.conditions:
        for k in (1, 2):
            _trace(out / f"{condition}-{k}.jsonl", [
                {**LOCK_SENT, "t": 1.0}, {"kind": "scenario", "t0": 5.0, "t": 5.0},
                {**INTRUDER_OPENS, "t": 10.0}, {**LOCK_SENT, "t": 20.0}])
    runs = rejudge(s, out)
    assert all(r.verdicts[0].holds for r in runs)


def test_a_logical_run_s_trace_says_its_timeline_begins_at_0(tmp_path):
    runs = run_scenario(scenario(tmp_path), tmp_path / "out")
    assert [r for r in runs[0].records if r["kind"] == "scenario"][0]["t0"] == 0.0


def test_a_run_that_fails_is_reported_as_not_run_and_the_others_are_judged(tmp_path):
    from vesper.lab.scenario_run import _runs, report
    from vesper.lab.session import read_trace
    s = scenario(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    def run_once(scenario, condition, k, trace):
        if (condition, k) == ("attack", 2):
            raise RuntimeError("Home Assistant did not show lock.front_lock within 90 s")
        _trace(trace, [{"kind": "scenario", "t0": 0.0, "t": 0.0}, {**INTRUDER_OPENS, "t": 10.0}])
        return 7, read_trace(trace)

    runs = _runs(s, out, run_once)
    failed = [r for r in runs if r.error]
    assert [(r.condition, r.repeat) for r in failed] == [("attack", 2)] and "within 90 s" in failed[0].error
    markdown, summary = report(s, runs)
    assert "attack 2: not run" in markdown and "within 90 s" in markdown
    assert summary["claims"][0]["conditions"]["attack"] == {"expected": True, "held": 1, "runs": 1, "not_run": 1}


def test_the_report_can_be_rebuilt_when_a_run_left_no_trace(tmp_path):
    from vesper.lab.scenario_run import rejudge
    s = scenario(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    for condition in s.runs.conditions:
        for k in (1, 2):
            if (condition, k) != ("baseline", 2):
                _trace(out / f"{condition}-{k}.jsonl", [{"kind": "scenario", "t0": 0.0, "t": 0.0}])
    runs = rejudge(s, out)
    (missing,) = [r for r in runs if r.error]
    assert (missing.condition, missing.repeat) == ("baseline", 2) and "no trace" in missing.error


def test_a_live_step_that_raised_on_the_clock_fails_its_run_with_the_error():
    """A step that raises on the wall clock's thread is kept in clock.errors; the run is not judged as if it ran."""
    import pytest

    from vesper.lab.scenario_run import _check_clock

    class Clock:
        errors = ["NetworkError: a throttle paces a TCP link"]

    class S:
        clock = Clock()
    with pytest.raises(RuntimeError, match="a step failed: NetworkError: a throttle"):
        _check_clock(S())


def test_the_defense_condition_runs_the_defenses_without_the_attack_as_their_control(tmp_path):
    """A defense that alerts on everything would reproduce 'it flags the held report' if it were never run without
    the attack: the defense condition is that control."""
    text = CASE_3.replace("conditions: {baseline: false, attack: false, attack+defense: true}",
                          "conditions: {baseline: false, defense: false, attack: false, attack+defense: true}").replace(
        "conditions: {baseline: false, attack: true, attack+defense: true}",
        "conditions: {baseline: false, defense: false, attack: true, attack+defense: true}").replace(
        "conditions: [baseline, attack, attack+defense]", "conditions: [baseline, defense, attack, attack+defense]")
    runs = run_scenario(scenario(tmp_path, text), tmp_path / "out")
    defense = [r for r in runs if r.condition == "defense"]
    assert len(defense) == 2
    assert all([x for x in r.records if x["kind"] == "scenario"][0]["defenses"] == ["freshness",
                                                                                  "physical_consistency"]
               for r in defense)
    assert not [x for r in defense for x in r.records if x["kind"] == "alert"]
    assert [x for x in runs[0].records if x["kind"] == "scenario"][0]["defenses"] == []


def test_the_report_flags_an_event_of_a_claim_that_matched_nothing_in_any_run(tmp_path):
    """A claim over an event that never happens holds or fails for the wrong reason (a misnamed value, a device
    that never acts): the report says so."""
    from vesper.lab.scenario_run import report
    text = CASE_3.replace("predicate: {alert: {defense: freshness, device: front_door}}",
                          "predicate: {never: {alert: {defense: freshness, device: hall_motion}}}").replace(
        "conditions: {baseline: false, attack: false, attack+defense: true}\n      - claim: the world",
        "conditions: {baseline: true, attack: true, attack+defense: true}\n      - claim: the world", 1)
    s = scenario(tmp_path, text)
    markdown, summary = report(s, run_scenario(s, tmp_path / "out"))
    assert summary["claims"][1]["unmatched"] == [{"alert": {"defense": "freshness", "device": "hall_motion"}}]
    assert summary["claims"][0]["unmatched"] == []
    assert "matched nothing in any run" in markdown


def test_heard_is_when_the_hub_has_the_report_and_report_when_the_device_sent_it(tmp_path):
    """The door's 'opened' report is sent at once and held 60 s on the bridge's connection: the hub hears it a
    minute later, in the attack conditions only."""
    text = CASE_3.replace("predicate: {alert: {defense: freshness, device: front_door}}",
                          "predicate: {gap: [{report: {device: front_door, feature: contact, value: false}}, "
                          "{heard: {device: front_door, feature: contact, value: false}}], at_least_s: 59}").replace(
        "conditions: {baseline: false, attack: false, attack+defense: true}\n      - claim: the world",
        "conditions: {baseline: false, attack: true, attack+defense: true}\n      - claim: the world", 1)
    runs = run_scenario(scenario(tmp_path, text), tmp_path / "out")
    assert holds(runs, "baseline", 1) == [False, False]
    assert holds(runs, "attack", 1) == [True, True]


def test_a_live_run_waits_for_the_wifi_devices_entities_too():
    import pytest

    from vesper.lab.scenario_run import _wait_for_home_assistant

    class HA:
        def state(self, entity):
            return None if entity == "switch.kettle_plug" else {"state": "off"}

    class Wifi:
        devices = {"kettle_plug": object()}

        def entities(self, name):
            return {"POWER": f"switch.{name}"}

    class Bridge:
        expected = {}

    class Hub:
        bridge, wifi = Bridge(), Wifi()
    with pytest.raises(RuntimeError, match="switch.kettle_plug"):
        _wait_for_home_assistant(HA(), Hub(), timeout=0.0)


# -- a scenario started in a running session: what `vesper lab play --scenario` shows (plan W4c) ----------------
def running_session(s, at=50.0):
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    session = Session(s.home, clock=LogicalClock())
    session.start(spawn_player=False, automations=True)
    session.clock.advance(at)  # the page has been open a while
    return session


def test_a_scenario_started_in_a_running_session_plays_from_that_moment(tmp_path):
    from vesper.lab.scenario_run import start_scenario
    s = scenario(tmp_path)
    session = running_session(s)
    t0 = start_scenario(session, s, "attack+defense")
    session.clock.advance(200)
    records = session.records
    (start,) = [r for r in records if r["kind"] == "scenario"]
    assert t0 == 50.0 and start["t0"] == 50.0 and start["condition"] == "attack+defense" and start["title"] == s.title
    effect = next(r for r in records if r["kind"] == "input" and r["op"] == "effect")
    assert effect["t"] == 51.0  # the attack's "at: 1" counts from the scenario's start
    assert [r for r in records if r["kind"] == "alert" and r["defense"] == "freshness"]
    opened = next(r for r in records if r["kind"] == "input" and r.get("actor") == "intruder"
                  and r.get("outcome", {}).get("result") == "opened")
    assert opened["t"] >= 100.0


def test_a_baseline_started_in_a_running_session_puts_on_no_attack_and_no_defense(tmp_path):
    from vesper.lab.scenario_run import start_scenario
    s = scenario(tmp_path)
    session = running_session(s)
    start_scenario(session, s, "baseline")
    session.clock.advance(200)
    assert not [r for r in session.records if r["kind"] in ("alert",) or (r["kind"] == "input" and r["op"] == "effect")]
