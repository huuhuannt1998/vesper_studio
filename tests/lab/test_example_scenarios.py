"""The gold Phantom-Delay scenarios in examples/lab/scenarios/phantom-delay (security-lab spec §4.6): their products are
real catalog products, their automations load, and on the logical clock every claim is reproduced."""
import dataclasses
import json
from pathlib import Path

import pytest

from vesper.lab.scenario import load_scenario
from vesper.lab.scenario_run import report, run_scenario

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "examples" / "lab" / "scenarios" / "phantom-delay"
CATALOG = ROOT / "catalog" / "vds" / "zigbee.json"


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
@pytest.mark.parametrize("name", ["case1-state-update-delay", "case3-action-delay"])
def test_a_gold_scenario_reproduces_every_claim_on_the_logical_clock(tmp_path, name):
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}  # the records only: not a staleness check
    s = load_scenario(GOLD / f"{name}.yaml", records)
    s.runs = dataclasses.replace(s.runs, repeat=1)
    text, summary = report(s, run_scenario(s, tmp_path))
    assert summary["refused"] == [], text
    assert [c["claim"] for c in summary["claims"] if not c["reproduced"]] == [], text
    assert [c["claim"] for c in summary["claims"] if c["unmatched"]] == [], text


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_the_page_is_given_the_scenario_s_plan_for_its_condition():
    from vesper.lab.scenario_run import scenario_plan
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}
    s = load_scenario(GOLD / "case3-action-delay.yaml", records)
    plan = scenario_plan(s, "attack")
    assert plan["until"] == 180.0
    assert [(a["at"], a["actor"], a["verb"], a["target"]) for a in plan["timeline"]][:2] == [
        (5.0, "player", "open", "hall-porch"), (15.0, "player", "walk", "porch")]
    held = plan["attack"]
    assert [(a["device"], a["kind"], a["hold_s"], a["direction"]) for a in held] == [
        ("front_door", "delay", 30.0, "up"), ("front_lock", "delay", 40.0, "down")]
    assert plan["defenses"] == [] and len(plan["claims"]) == 5
    assert plan["claims"][1] == {"claim": "the intruder gets into the house", "expected": True}
    assert plan["gaps"] and all("what" in g for g in plan["gaps"])
    calm = scenario_plan(s, "defense")
    assert calm["attack"] == [] and calm["defenses"] == ["freshness", "physical_consistency"]
    assert calm["claims"][1]["expected"] is False


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_a_played_scenario_records_its_verdict_when_its_time_is_up(tmp_path):
    from vesper.lab.scenario_run import start_scenario
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}
    s = load_scenario(GOLD / "case3-action-delay.yaml", records)
    session = Session(s.home, clock=LogicalClock())
    session.start(spawn_player=False, automations=True)
    t0 = start_scenario(session, s, "attack")
    session.clock.advance_to(t0 + 179.0)
    assert not [r for r in session.records if r["kind"] == "verdict"]
    session.clock.advance_to(t0 + 181.0)
    (verdict,) = [r for r in session.records if r["kind"] == "verdict"]
    session.stop()
    assert [(c["holds"], c["expected"]) for c in verdict["claims"]] == [(True, True)] * 3 + [(False, False)] * 2
