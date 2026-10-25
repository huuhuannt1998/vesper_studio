"""The example home in examples/lab/test-house: it is the tests' house, its products are real catalog products,
and its automation names entities the hub really creates."""
import json
import re
from pathlib import Path

import pytest

from tests.exposure.fake_mqtt import FakeClient
from tests.lab.maps import house
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.session import Session

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "lab" / "test-house"
CATALOG = ROOT / "catalog" / "vds" / "zigbee.json"
ENTITY = r"\b(?:light|binary_sensor|sensor|switch|event|button|select|number)\.v[0-9a-f]{16}_\w+"


def test_the_example_s_map_is_the_test_house(tmp_path):
    house().save(tmp_path / "map.json")
    assert json.loads((EXAMPLE / "map.json").read_text()) == json.loads((tmp_path / "map.json").read_text())


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_the_example_s_products_are_real_and_its_automation_names_entities_the_hub_creates():
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}  # the records only: not a staleness check
    home = load_lab_home(EXAMPLE / "home.yaml", records)
    session = Session(home)
    hub = Hub(session, FakeClient())
    session.start()
    hub.start()
    made = {e for d in home.devices if d.name not in hub.skipped for e in hub.entities(d.name).values()}
    named = set(re.findall(ENTITY, (EXAMPLE / "automations.yaml").read_text()))
    hub.stop()
    session.stop()
    assert named and named <= made


def test_the_example_s_look_for_the_hub_is_a_valid_steps_file():
    from vesper.lab.visual import load_steps
    steps = load_steps(EXAMPLE / "look-hub.yaml")
    assert [next(iter(s)) for s in steps][-1] == "shot"
