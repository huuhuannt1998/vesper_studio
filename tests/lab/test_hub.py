"""The hub in the loop, against the in-memory broker client and Home Assistant of the exposure tests."""
import json
import threading
import time

import pytest

from tests.exposure.fake_ha import FakeHA
from tests.exposure.fake_mqtt import FakeClient
from tests.lab.homes import catalog, write_home
from tests.lab.test_play import client, until
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.play import PlayServer
from vesper.lab.session import Session
from vesper.twins.clock import WallClock


@pytest.fixture
def lab(tmp_path):
    home = load_lab_home(write_home(tmp_path), catalog())  # the player starts in the hall
    session = Session(home, clock=WallClock())
    mqtt = FakeClient()
    hub = Hub(session, mqtt)
    session.start()
    hub.start()
    yield session, hub, mqtt
    hub.stop()
    session.stop()


def eventually(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("the condition never held")


def test_every_zigbee_device_is_on_the_hub_and_matter_ones_are_named(lab):
    session, hub, mqtt = lab
    assert hub.skipped == ["front_lock"]  # Matter: plan W2d
    entities = set(FakeHA(mqtt)._entities())
    for name in ("front_door", "hall_motion", "bedroom_lamp", "bedroom_button", "kitchen_climate"):
        assert set(hub.entities(name).values()) <= entities and hub.entities(name)
    assert hub.entities("bedroom_lamp")["state"].startswith("light.")


def test_home_assistant_s_command_reaches_the_twin_on_the_clock_thread_and_is_traced(lab):
    session, hub, mqtt = lab
    threads = []
    session.runtime.on_action(lambda a: threads.append(threading.current_thread().name))
    FakeHA(mqtt).call("light", "turn_on", {"entity_id": hub.entities("bedroom_lamp")["state"]})
    eventually(lambda: session.rules.effects.get("bedroom_lamp") == {"on": True})
    assert threads == ["vesper-wallclock"]
    command = next(r for r in session.records if r["kind"] == "input" and r["op"] == "command")
    assert command["actor"] == "hub" and command["args"]["device"] == "bedroom_lamp"


def test_walking_in_lets_an_automation_turn_the_lamp_on_and_the_page_shows_it(tmp_path):
    text = """\
        version: 2
        scene: test/house
        avatar: {model: female_0, start: {room: kitchen}}
        doors:
          - {doorway: bedroom-hall, open: true}
        devices:
          - {product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}
          - {product: "zigbee:acme:dim-1", name: bedroom_lamp, at: {room: bedroom, xy: [5.0, 5.0], mount: table}}
    """
    home = load_lab_home(write_home(tmp_path, text), catalog())
    session = Session(home, clock=WallClock())
    mqtt = FakeClient()
    hub = Hub(session, mqtt)
    session.start()
    hub.start()
    server = PlayServer(session, port=0)
    server.start()
    ha = FakeHA(mqtt)
    motion, lamp = hub.entities("hall_motion")["occupancy"], hub.entities("bedroom_lamp")["state"]

    def automation():  # Home Assistant's: when the hall sees someone, the bedroom lamp turns on
        while not stop.is_set():
            if ha.state(motion)["state"] == "on" and ha.state(lamp)["state"] == "off":
                ha.call("light", "turn_on", {"entity_id": lamp})
            time.sleep(0.05)
    stop = threading.Event()
    threading.Thread(target=automation, daemon=True).start()
    try:
        with client(server) as ws:
            until(ws, lambda m: m["type"] == "hello")
            assert ha.state(lamp)["state"] == "off"
            ws.send(json.dumps({"type": "walk_to", "x": 1.5, "z": 1.5}))  # from the kitchen into the hall
            until(ws, lambda m: m["type"] == "effect" and m["target"] == "bedroom_lamp" and m["effect"] == {"on": True},
                  timeout=10)
        assert ha.state(lamp)["state"] == "on"
        assert any(r["kind"] == "input" and r["op"] == "command" for r in session.records)
    finally:
        stop.set()
        server.stop()
        hub.stop()
        session.stop()


def test_a_device_whose_hop_stays_down_goes_unavailable_after_zigbee2mqtt_s_timeout_and_comes_back(lab, monkeypatch):
    """Zigbee2MQTT marks a device offline when it stops answering for its availability timeout (10 min for a
    mains-powered device, 25 h for a battery one); here shortened. Only that device: the others stay available."""
    from vesper.lab import hub as hub_module
    from vesper.lab.linklayer import Effect
    session, hub, mqtt = lab
    monkeypatch.setitem(hub_module.AVAILABILITY_S, "active", 0.3)
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    eventually(lambda: mqtt.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "offline"})
    assert mqtt.last("zigbee2mqtt/hall_motion/availability") == {"state": "online"}
    session.network_clear("zigbee/bedroom_lamp")
    eventually(lambda: mqtt.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "online"})


def test_a_command_to_a_device_behind_a_cut_hop_never_arrives(lab):
    from vesper.lab.linklayer import Effect
    session, hub, mqtt = lab
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    FakeHA(mqtt).call("light", "turn_on", {"entity_id": hub.entities("bedroom_lamp")["state"]})
    time.sleep(0.3)
    assert session.rules.effects.get("bedroom_lamp") != {"on": True}
    assert any(r["kind"] == "net" and r["event"] == "dropped" and r.get("direction") == "down"
               for r in session.records)
