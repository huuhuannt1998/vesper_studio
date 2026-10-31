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
