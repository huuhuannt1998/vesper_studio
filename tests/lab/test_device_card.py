"""A device's card (home-generator spec §4, plan W9): Room 334's panel from VESPER's own records: the product, its
model basis (fidelity and sources, what is assumed), its connections (its links and their quality), its interfaces
and role (its Home Assistant entities and the automations naming them), its state, and the product's reference."""
import json
import urllib.request

from tests.lab.homes import catalog, write_home
from tests.lab.test_play import play  # noqa: F401 - the fixture
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.automations import entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.play import device_card
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock


def session_with_automation(tmp_path):
    write_home(tmp_path, LOCKED_HOME + "    automations: automations.yaml\n")
    (tmp_path / "automations.yaml").write_text("[]\n")
    ids = entity_ids(load_lab_home(tmp_path / "home.yaml", catalog()))
    (tmp_path / "automations.yaml").write_text(
        "- alias: Lock on close\n  triggers: [{trigger: state, entity_id: %s, to: 'off'}]\n"
        "  actions: [{action: lock.lock, target: {entity_id: %s}}]\n" % (ids["front_door"]["contact"],
                                                                        ids["front_lock"]["state"]))
    s = Session(load_lab_home(tmp_path / "home.yaml", catalog()), clock=LogicalClock())
    s.start(spawn_player=False)
    return s, ids


def test_a_card_tells_the_product_its_model_basis_connections_interfaces_and_state(tmp_path):
    s, ids = session_with_automation(tmp_path)
    card = device_card(s, "front_lock")
    assert (card["name"], card["product"], card["vendor"], card["model"], card["category"], card["protocol"]) == (
        "front_lock", "zigbee:acme:lock-1", "Acme", "LOCK-1", "lock", "zigbee")
    assert card["where"]["door"] == "hall-porch" and card["where"]["mount"] == "door"
    basis = card["model_basis"]
    assert set(basis["reached"]) == {"L0", "L1", "L2", "L3", "L4"} and basis["sources"][0]["source"] == "zigbee"
    assert [c["link"] for c in card["connections"]] == ["zigbee/front_lock", "coordinator", "hub"]
    assert card["connections"][0]["medium"] == "zigbee" and "rssi_dbm" in card["connections"][0]
    assert card["interfaces"]["entities"]["state"] == ids["front_lock"]["state"]
    assert card["interfaces"]["automations"] == ["Lock on close"]
    assert card["state"]["state"] == "UNLOCK"
    assert card["reference"] == "https://www.zigbee2mqtt.io/devices/LOCK-1.html"


def test_the_play_server_serves_a_device_s_card_and_names_an_unknown_one(play):  # noqa: F811
    server = play
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/device/front_door.json") as r:
        card = json.loads(r.read())
    assert card["name"] == "front_door" and card["category"] == "contact_sensor"
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/device/nothing.json")
        raise AssertionError("an unknown device was served")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_a_card_says_whether_a_hand_can_operate_the_device_and_its_effect_now(tmp_path):
    s, _ = session_with_automation(tmp_path)
    card = device_card(s, "front_lock")
    assert card["operable"] is s.rules.operable("front_lock")
    assert card["effect"] == s.rules.effects.get("front_lock")
