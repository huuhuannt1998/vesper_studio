"""The hub, as Room 334's gateway: where the page draws it (the hello's network), and its card: Home Assistant, and
every device of the home with how it reaches the hub, its link now and its state, so a researcher checks all the
devices from one place."""
import json
import urllib.request

from tests.lab.test_device_card import session_with_automation
from tests.lab.test_play import play  # noqa: F401 - the fixture
from vesper.lab.play import hello, hub_card


def test_the_hello_says_where_the_router_the_hub_and_the_coordinator_are(tmp_path):
    s, _ = session_with_automation(tmp_path)
    net = hello(s)["network"]
    n = s.home.network
    assert net["hub"] == {"room": n.hub.room, "x": n.hub.position[0], "z": n.hub.position[1]}
    assert set(net) == {"router", "hub", "coordinator"}


def test_the_hub_card_lists_every_device_with_its_link_and_state_and_names_home_assistant(tmp_path):
    s, ids = session_with_automation(tmp_path)
    card = hub_card(s, "http://127.0.0.1:18123")
    assert card["hub_url"] == "http://127.0.0.1:18123"
    assert card["room"] == s.home.network.hub.room
    names = [d["name"] for d in card["devices"]]
    assert names == sorted(d.name for d in s.home.devices)
    lock = next(d for d in card["devices"] if d["name"] == "front_lock")
    assert lock["protocol"] == "zigbee" and lock["category"] == "lock"
    assert lock["path"] == ["zigbee/front_lock", "coordinator", "hub"] and lock["up"] is True
    assert lock["state"]["state"] == "UNLOCK" and lock["entities"] == len(ids["front_lock"])
    assert card["counts"] == {"devices": len(names), "up": len(names), "unlinked": 0}


def test_without_home_assistant_the_card_says_vesper_runs_the_automations(tmp_path):
    s, _ = session_with_automation(tmp_path)
    assert hub_card(s, None)["hub_url"] is None


def test_the_play_server_serves_the_hub_card(play):  # noqa: F811
    with urllib.request.urlopen(f"http://127.0.0.1:{play.port}/hub.json") as r:
        card = json.loads(r.read())
    assert card["hub_url"] == "http://127.0.0.1:18123" and card["devices"]


def test_the_page_and_the_hub_card_carry_home_assistant_s_lab_login(tmp_path):
    s, _ = session_with_automation(tmp_path)
    login = {"user": "vesper", "password": "lab-pass"}
    assert hello(s, "http://127.0.0.1:18123", hub_login=login)["hub_login"] == login
    assert hello(s)["hub_login"] is None
    assert hub_card(s, "http://127.0.0.1:18123", login)["login"] == login
