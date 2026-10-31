"""Scoring a model's scenario against the gold ones (security-lab spec §5.4, plan W4b): devices compared by what they
are, not their names; recall and precision per element kind; the claims' expected conditions compared."""
import json
import re
from pathlib import Path

import pytest

from vesper.lab.scenario_score import score

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "examples" / "lab" / "scenarios" / "phantom-delay"
CATALOG = ROOT / "catalog" / "vds" / "zigbee.json"
pytestmark = pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")


def records():
    return {r["id"]: r for r in json.loads(CATALOG.read_text())}


def test_the_gold_scores_full_marks_against_itself():
    g = [GOLD / "case1-state-update-delay.yaml", GOLD / "case3-action-delay.yaml"]
    r = score(g, g, records())
    assert all(x["recall"] == 1.0 and x["precision"] == 1.0 for x in r["elements"].values()), r["elements"]
    n = r["claims_with_the_gold_expectations"].split("/")[1]
    assert r["claims_with_the_gold_expectations"] == f"{n}/{n}"


def test_a_renamed_device_still_matches_and_a_missing_defense_and_step_are_counted(tmp_path):
    text = (GOLD / "case3-action-delay.yaml").read_text()
    text = text.replace("front_door", "entry_contact")  # the model's own name for the gold's sensor
    text = text.replace("  - {kind: physical_consistency, within_s: 5}\n", "")
    text = re.sub(r"  - claim: the world disagrees.*?\n(?=runs:)", "", text, flags=re.S)
    text = re.sub(r"  - \{at: \"19:30:01\", vantage: network, effect: delay, link: coordinator, direction: down,"
                  r".*?cites: \[attack, threat\]\}\n", "", text, flags=re.S)
    home = (GOLD / "home.yaml").read_text().replace("front_door", "entry_contact")
    (tmp_path / "home.yaml").write_text(home.replace("map: ../../test-house/map.json",
                                                     f"map: {ROOT / 'examples/lab/test-house/map.json'}"))
    automations = (GOLD / "automations.yaml").read_text()
    (tmp_path / "automations.yaml").write_text(automations)
    draft = tmp_path / "draft.yaml"
    draft.write_text(text)
    r = score([draft], [GOLD / "case3-action-delay.yaml"], records())
    e = r["elements"]
    assert e["defenses"]["recall"] == 0.5 and e["defenses"]["precision"] == 1.0
    assert e["attack"]["recall"] == 0.5 and e["attack"]["precision"] == 1.0
    assert e["timeline"]["recall"] == 1.0  # a renamed sensor is still the same contact sensor
