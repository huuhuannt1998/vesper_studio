"""The home's network end to end, against a real Mosquitto (opt-in: VESPER_STACK_TESTS=1; needs `mosquitto`).

The old VESPER's emulated network did not carry the traffic it measured; this test is that failure turned round
(security-lab spec §8): the real bridge (paho) reaches a real broker only through the relay's coordinator link, a
real client in Home Assistant's place only through its hub link, and cutting one link silences exactly what
crosses it.
"""
import json
import os
import socket
import time

import pytest

from tests.lab.homes import catalog, write_home
from vesper.exposure.mqtt import connect, disconnect
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.linklayer import Effect
from vesper.lab.relay import Relay, SessionSink
from vesper.lab.session import Session
from vesper.twins.clock import WallClock
from vesper.validation.stack import Mosquitto

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_STACK_TESTS") != "1", reason="set VESPER_STACK_TESTS=1")]

WILL = ("zigbee2mqtt/bridge/state", json.dumps({"state": "offline"}))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Observer:
    """A client in Home Assistant's place: every message it hears, with the time."""

    def __init__(self, port):
        self.heard: list[tuple[float, str, dict | None]] = []
        self.client = connect("127.0.0.1", port, "observer")
        self.client.on_message = lambda c, u, m: self.heard.append(
            (time.monotonic(), m.topic, json.loads(m.payload) if m.payload else None))
        self.client.subscribe("zigbee2mqtt/#", qos=1)

    def last(self, topic):
        return next((p for _, t, p in reversed(self.heard) if t == topic), None)

    def times(self, topic, **match):
        return [t for t, top, p in self.heard if top == topic and p and all(p.get(k) == v for k, v in match.items())]


def until(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def open_front_door(session):
    """The player walks to the front door and opens it: its contact sensor reports `contact: false`."""
    session.walk_to((0.4, 3.0))
    assert until(lambda: abs(session.world.pose("player").x - 0.4) < 0.15)
    assert session.interact("hall-porch")["result"] == "opened"


@pytest.fixture
def live(tmp_path, monkeypatch):
    from vesper.lab import hub as hub_module
    monkeypatch.setitem(hub_module.AVAILABILITY_S, "active", 1.0)
    monkeypatch.setitem(hub_module.AVAILABILITY_S, "passive", 1.0)
    port = free_port()
    broker = Mosquitto(tmp_path / "mqtt", port=port)
    broker.start()
    sink = SessionSink()
    relay = Relay(record=sink.record, allowed={port}, now=sink.now, latency=sink.latency)
    relay.start()
    hub_port = relay.add_link("hub", port, client_is_device=False)
    home = load_lab_home(write_home(tmp_path), catalog())
    session = Session(home, clock=WallClock(), live_network=True)
    session.relay = relay
    sink.attach(session)
    session.start()
    coord_port = relay.add_link("coordinator", port, client_is_device=True)
    bridge_client = connect("127.0.0.1", coord_port, "vesper-lab-bridge", will=WILL)
    bridge_client.reconnect_delay_set(min_delay=1, max_delay=1)
    hub = Hub(session, bridge_client)
    hub.start()
    observer = Observer(hub_port)
    yield session, hub, observer, relay
    disconnect(observer.client)
    hub.stop()
    disconnect(bridge_client)
    session.stop()
    relay.stop()
    broker.stop()


def test_every_device_s_reports_cross_the_coordinator_and_hub_links_to_home_assistant(live):
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "online"})
    for name in ("front_door", "hall_motion", "bedroom_lamp", "bedroom_button", "kitchen_climate"):
        assert until(lambda name=name: observer.last(f"zigbee2mqtt/{name}") is not None), name
    relay.apply(Effect("cut", link="hub"))  # silent: Home Assistant hears nothing at all
    n = len(observer.heard)
    open_front_door(session)
    time.sleep(0.5)
    assert len(observer.heard) == n
    relay.clear("hub")
    assert until(lambda: observer.times("zigbee2mqtt/front_door", contact=False))  # what waited arrives


def test_a_reset_coordinator_link_makes_the_broker_publish_the_bridge_s_will_and_the_bridge_comes_back(live):
    """Every Zigbee device's discovery lists the bridge's state (availability_mode: all): offline here is every
    device unavailable in Home Assistant."""
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "online"})
    session.network_effect(Effect("cut", link="coordinator", keep_connection=False))
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "offline"})
    session.network_clear("coordinator")
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "online"}, timeout=15)
    events = [(r["link"], r["event"]) for r in session.records if r["kind"] == "net" and r["event"] in ("down", "up")]
    assert ("coordinator", "down") in events and ("coordinator", "up") in events


def test_a_held_report_reaches_home_assistant_late_on_a_session_that_stays_up(live):
    """Phantom-Delay's mechanism, on real TCP: one device's report held for its hold, the keepalives passing, so
    the broker never drops the bridge and never publishes its will."""
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/front_door") is not None)
    session.network_effect(Effect("delay", link="coordinator", direction="up", device="front_door",
                                  feature="contact", hold_s=3))
    open_front_door(session)
    t0 = time.monotonic()
    assert until(lambda: observer.times("zigbee2mqtt/front_door", contact=False), timeout=10)
    assert observer.times("zigbee2mqtt/front_door", contact=False)[0] - t0 >= 2.9
    assert observer.last("zigbee2mqtt/bridge/state") == {"state": "online"}  # no will: the session stayed alive
    held = [r for r in session.records if r["kind"] == "net" and r["event"] == "held"]
    assert held and held[0]["device"] == "front_door"


def test_a_cut_zigbee_hop_silences_only_its_device_and_zigbee2mqtt_marks_only_it_unavailable(live):
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "online"})
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    assert until(lambda: observer.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "offline"})
    for other in ("front_door", "hall_motion", "kitchen_climate"):
        assert observer.last(f"zigbee2mqtt/{other}/availability") == {"state": "online"}
    session.network_clear("zigbee/bedroom_lamp")
    assert until(lambda: observer.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "online"})


def test_the_relay_only_ever_listens_on_loopback(live):
    session, hub, observer, relay = live
    for link in relay._links.values():
        for sock in link.server.sockets:
            assert sock.getsockname()[0] == "127.0.0.1"
