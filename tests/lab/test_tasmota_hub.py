"""The home's Wi-Fi devices on the hub (security-lab spec §3.3, plan W7a2): each a virtual Tasmota device with its
own MQTT connection through its own `wifi/<name>` link, against the in-memory client of the exposure tests."""
import threading
import time

import pytest

from tests.exposure.fake_mqtt import FakeClient
from tests.lab.homes import catalog, with_wifi, write_home
from vesper.lab import linklayer
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.network import WIFI
from vesper.lab.session import Session
from vesper.lab.tasmota_hub import KEEPALIVE_S, TasmotaHub
from vesper.twins.clock import WallClock

BROKER = 18830


class Connections:
    """What the hub's `connect(port, client_id, will, keepalive)` opened: one in-memory client each."""

    def __init__(self):
        self.clients: dict[str, FakeClient] = {}

    def __call__(self, port, client_id, will, keepalive):
        c = self.clients[client_id] = FakeClient()
        c.port, c.will, c.keepalive = port, will, keepalive
        return c


class FakeRelay:
    def __init__(self):
        self.calls, self.ports = [], {}

    def add_link(self, name, upstream, client_is_device):
        self.calls.append(("add_link", name, upstream, client_is_device))
        return self.ports.setdefault(name, 40000 + len(self.ports))

    def add_device(self, key, identify):
        self.calls.append(("add_device", key, key))

    def lose(self, name):
        self.calls.append(("lose", name))

    def rejoin(self, name):
        self.calls.append(("rejoin", name))


def eventually(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("the condition never held")


def start(tmp_path, relay=None):
    home = load_lab_home(write_home(tmp_path, with_wifi()), catalog())
    session = Session(home, clock=WallClock(), live_network=relay is not None)
    session.relay = relay
    connections = Connections()
    hub = TasmotaHub(session, connections, BROKER)
    session.start()
    hub.start()
    return session, hub, connections


def refresh(session):
    """The home changed (here: the Wi-Fi's reach): the link layer looks again, on the session's clock."""
    session.clock.call_at(session.clock.now(), session.links.refresh)


@pytest.fixture
def wifi(tmp_path):
    session, hub, connections = start(tmp_path)
    yield session, hub, connections
    hub.stop()
    session.stop()


def test_each_wi_fi_device_is_a_tasmota_device_on_its_own_connection_with_its_will(wifi):
    session, hub, connections = wifi
    assert sorted(hub.devices) == ["kettle_plug", "reading_lamp"]
    for name, d in hub.devices.items():
        c = connections.clients[d.topic]
        assert (c.port, c.will, c.keepalive) == (BROKER, (f"tele/{d.topic}/LWT", "Offline"), KEEPALIVE_S)
        assert c.last(f"tasmota/discovery/{d.mac}/config")["dn"] == name
    assert hub.entities("kettle_plug") == {"POWER": "switch.kettle_plug"}
    assert hub.entities("reading_lamp") == {"POWER": "light.reading_lamp"}


def test_the_zigbee_hub_leaves_the_wi_fi_devices_to_their_own_and_names_only_matter_ones(wifi):
    session, _, _ = wifi
    assert Hub(session, FakeClient()).skipped == ["front_lock"]


def test_home_assistant_s_command_crosses_the_device_s_link_and_reaches_the_twin_on_the_clock_thread(wifi):
    session, hub, connections = wifi
    d = hub.devices["kettle_plug"]
    threads = []
    session.runtime.on_action(lambda a: threads.append(threading.current_thread().name))
    connections.clients[d.topic].deliver(f"cmnd/{d.topic}/POWER1", "ON")
    eventually(lambda: session.runtime.state(d.twin_id)["POWER"] == "ON")
    assert threads == ["vesper-wallclock"]
    command = next(r for r in session.records if r["kind"] == "input" and r["op"] == "command")
    assert command["actor"] == "hub" and command["args"] == {"device": "kettle_plug", "feature": "POWER",
                                                             "value": "ON"}


def test_a_wi_fi_device_out_of_range_from_the_start_joins_once_it_comes_into_range(tmp_path, monkeypatch):
    """A station out of range never connects, so Home Assistant never hears of it; once in range, after its rejoin
    back-off, it connects and announces itself."""
    monkeypatch.setitem(WIFI, "drop_dbm", -46.0)  # the kettle (-44.9 dBm) is in range, the lamp (-47.4) not
    monkeypatch.setattr(linklayer, "WIFI_REJOIN_S", 0.2)
    session, hub, connections = start(tmp_path)
    try:
        assert sorted(hub.devices) == ["kettle_plug"]
        monkeypatch.setitem(WIFI, "drop_dbm", -85.0)
        refresh(session)
        eventually(lambda: "reading_lamp" in hub.devices)
        d = hub.devices["reading_lamp"]
        assert connections.clients[d.topic].last(f"tasmota/discovery/{d.mac}/config")["dn"] == "reading_lamp"
    finally:
        hub.stop()
        session.stop()


def test_on_the_live_network_each_device_crosses_its_own_relay_link_which_loses_it_out_of_range(tmp_path,
                                                                                               monkeypatch):
    """The relay carries `wifi/<name>`; out of range the station is lost (the broker notices by its keepalive and
    publishes the will); back in range, after the back-off, it rejoins on a new connection."""
    monkeypatch.setattr(linklayer, "WIFI_REJOIN_S", 0.2)
    relay = FakeRelay()
    session, hub, connections = start(tmp_path, relay)
    try:
        d = hub.devices["kettle_plug"]
        assert ("add_link", "wifi/kettle_plug", BROKER, True) in relay.calls
        assert ("add_device", d.topic, d.topic) in relay.calls
        assert connections.clients[d.topic].port == relay.ports["wifi/kettle_plug"]
        monkeypatch.setitem(WIFI, "drop_dbm", -40.0)  # both out of range
        refresh(session)
        eventually(lambda: ("lose", "wifi/kettle_plug") in relay.calls)
        monkeypatch.setitem(WIFI, "drop_dbm", -85.0)
        refresh(session)
        eventually(lambda: ("rejoin", "wifi/kettle_plug") in relay.calls)
        assert [c for c in relay.calls if c[0] == "add_link" and c[1] == "wifi/kettle_plug"] == [
            ("add_link", "wifi/kettle_plug", BROKER, True)]  # one link per device, kept across a loss
    finally:
        hub.stop()
        session.stop()


def test_stop_says_each_device_is_offline(tmp_path):
    session, hub, connections = start(tmp_path)
    clients = {d.topic: connections.clients[d.topic] for d in hub.devices.values()}
    hub.stop()
    session.stop()
    for topic, c in clients.items():
        assert c.raw[-1] == (f"tele/{topic}/LWT", "Offline", True)
