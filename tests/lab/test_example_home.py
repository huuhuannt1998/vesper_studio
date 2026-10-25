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


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_the_example_s_resident_lives_a_whole_day_that_replays_exactly():
    import dataclasses

    from vesper.lab.session import compare_reports, replay
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}
    home = dataclasses.replace(load_lab_home(EXAMPLE / "home.yaml", records), start_time=0)
    (alex,) = home.residents
    session = Session(home)
    session.start(spawn_player=False)
    session.clock.advance(24 * 3600)
    session.stop()
    stuck = [r for r in session.records if r["kind"] == "resident" and r["decision"].startswith("could")]
    assert not stuck  # every task's room and device was reached
    uses = {r["args"]["target"] for r in session.records if r["kind"] == "input" and r["op"] == "interact"}
    assert {"kitchen_plug", "hall-porch", "bedroom_lamp"} <= uses  # cooking, the front door, a dark bedroom
    assert compare_reports(session.records, replay(session.records, home)) is None
    assert alex.routine.tasks[-1].category == "SLEEP"


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_alex_is_out_of_the_hall_when_play_and_the_hub_look_begin():
    """`look-hub.yaml` has the player trip the hall's motion sensor: nobody else may be there first."""
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}
    session = Session(load_lab_home(EXAMPLE / "home.yaml", records))
    session.start(spawn_player=False)
    session.clock.advance(120)
    session.stop()
    assert session.world.pose("alex").room != "hall"
    assert not [r for r in session.records if r["kind"] == "rule" and r.get("device") == "hall_motion"]
