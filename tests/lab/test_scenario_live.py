"""A gold Phantom-Delay scenario live (security-lab spec §8: "the gold Phantom-Delay scenario reproduces its claims
live"): VESPER's Mosquitto and the real Home Assistant (VESPER_HA_TESTS=1, after `vesper setup ha`; VESPER_ROOT names
its folder, default .vesper), the attack through the relay on the bridge's connection, the defenses at Home
Assistant's edge. Four conditions; Case 3 twice each (runs in a row on one Home Assistant)."""
import dataclasses
import json
import os
from pathlib import Path

import pytest

from vesper.lab.scenario import load_scenario
from vesper.lab.scenario_run import report, run_scenario

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_HA_TESTS") != "1", reason="set VESPER_HA_TESTS=1")]
ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "examples" / "lab" / "scenarios" / "phantom-delay"


@pytest.mark.parametrize("name", ["case3-action-delay", "case1-state-update-delay"])
def test_a_gold_scenario_reproduces_its_claims_with_home_assistant_in_the_loop(tmp_path, name):
    records = {r["id"]: r for r in json.loads((ROOT / "catalog" / "vds" / "zigbee.json").read_text())}
    s = load_scenario(GOLD / f"{name}.yaml", records, mode="live")
    s.runs = dataclasses.replace(s.runs, repeat=2 if name.startswith("case3") else 1)  # Case 3: runs in a row
    runs = run_scenario(s, tmp_path, root=Path(os.environ.get("VESPER_ROOT", ROOT / ".vesper")),
                        catalog_dir=ROOT / "catalog" / "vds")
    text, summary = report(s, runs)
    (tmp_path / "report.md").write_text(text)
    assert [c["claim"] for c in summary["claims"] if not c["reproduced"]] == [], text
    (tmp_path / "summary.json").write_text(json.dumps(summary, indent=1))
    attack = next(r for r in runs if r.condition == "attack")
    commands = [r for r in attack.records if r["kind"] == "input" and r["actor"] == "hub"]
    assert commands, "Home Assistant, not VESPER's engine, sent the automation's command"
    assert not [r for r in attack.records if r["kind"] == "automation"]
    assert not [r for r in runs if r.error], text
