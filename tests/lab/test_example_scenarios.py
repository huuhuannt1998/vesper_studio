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
