"""The home's Wi-Fi devices end to end (plan W7a2): each a virtual Tasmota device on its own real MQTT connection,
through its own relay link, against a real Mosquitto (VESPER_STACK_TESTS=1; needs `mosquitto`) and the real Home
Assistant with its Tasmota integration (VESPER_HA_TESTS=1, after `vesper setup ha`; VESPER_ROOT names its folder,
default .vesper). VESPER's own ports only (18830, 18123); about two minutes.
"""
import os
import time
from pathlib import Path

import pytest

from tests.lab.homes import catalog, with_wifi, write_home
from vesper.exposure.mqtt import connect, disconnect
from vesper.lab import linklayer, tasmota_hub
from vesper.lab.home import load_lab_home
from vesper.lab.linklayer import Effect
from vesper.lab.network import WIFI
from vesper.lab.relay import Relay, SessionSink
from vesper.lab.session import Session
from vesper.lab.tasmota_hub import TasmotaHub
from vesper.twins.clock import WallClock
from vesper.validation import stack

pytestmark = pytest.mark.stack
STACK = pytest.mark.skipif(os.environ.get("VESPER_STACK_TESTS") != "1", reason="set VESPER_STACK_TESTS=1")
HA = pytest.mark.skipif(os.environ.get("VESPER_HA_TESTS") != "1", reason="set VESPER_HA_TESTS=1")
ROOT = Path(os.environ.get("VESPER_ROOT", ".vesper"))


def until(check, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return False


def reach(session, drop_dbm: float) -> None:
    """The Wi-Fi's reach changes (as a shut door would change it); the link layer looks again on the clock."""
    WIFI["drop_dbm"] = drop_dbm
    session.clock.call_at(session.clock.now(), session.links.refresh)


class Live:
    """VESPER's broker, the relay, a live-network session with the Wi-Fi home and its Tasmota devices; with
    `ha`, Home Assistant on the relay's hub link, its MQTT and Tasmota integrations set up."""

    def __init__(self, tmp, ha: bool):
        self.saved = (dict(WIFI), tasmota_hub.KEEPALIVE_S, linklayer.WIFI_REJOIN_S)
        tasmota_hub.KEEPALIVE_S, linklayer.WIFI_REJOIN_S = 2, 1.0  # a lost station noticed in 3 s, rejoined in 1 s
        self.broker = stack.Mosquitto(ROOT, port=18830)
        self.broker.start()
        sink = SessionSink()
        self.relay = Relay(record=sink.record, allowed={18830}, now=sink.now, latency=sink.latency)
        self.relay.start()
        self.ha = None
        if ha:
            self.ha = stack.HomeAssistant(ROOT, automations="[]\n")
            self.ha.start()
            self.ha.onboard()
            self.ha.ensure_mqtt(self.relay.add_link("hub", 18830, client_is_device=False))
            self.ha.ensure_tasmota()
        home = load_lab_home(write_home(tmp, with_wifi()), catalog())
        self.session = Session(home, clock=WallClock(), live_network=True)
        self.session.relay = self.relay
        sink.attach(self.session)
        self.session.start()
        self.hub = TasmotaHub(self.session, lambda p, cid, will, keepalive: connect(
            "127.0.0.1", p, cid, will=will, keepalive=keepalive, retry_s=1), 18830, disconnect=disconnect)
        self.hub.start()

    def close(self):
        for d in self.hub.devices.values():
            d.retract()  # Home Assistant forgets them: no ghost in the next run
        self.hub.stop()
        self.session.stop()
        if self.ha:
            self.ha.stop()
        self.relay.stop()
        self.broker.stop()
        WIFI.clear()
        WIFI.update(self.saved[0])
        tasmota_hub.KEEPALIVE_S, linklayer.WIFI_REJOIN_S = self.saved[1:]


class Observer:
    """A client in Home Assistant's place on the broker: what it hears of the Tasmota topics."""

    def __init__(self):
        self.heard: list[tuple[float, str, str]] = []
        self.client = connect("127.0.0.1", 18830, "observer")
        self.client.on_message = lambda c, u, m: self.heard.append((time.monotonic(), m.topic, m.payload.decode()))
        self.client.subscribe("tele/#", qos=1)

    def last(self, topic):
        return next((p for _, t, p in reversed(self.heard) if t == topic), None)


@STACK
def test_a_lost_station_goes_offline_by_its_will_and_comes_back_online_after_it_rejoins(tmp_path):
    live = Live(tmp_path, ha=False)
    observer = Observer()
    try:
        topic = live.hub.devices["kettle_plug"].topic
        assert until(lambda: observer.last(f"tele/{topic}/LWT") == "Online", 10)
        assert live.relay.effects == []
        reach(live.session, -40.0)  # out of range
        assert until(lambda: observer.last(f"tele/{topic}/LWT") == "Offline", 15)  # the broker's keepalive timeout
        reach(live.session, -85.0)
        assert until(lambda: observer.last(f"tele/{topic}/LWT") == "Online", 15)  # a new connection
    finally:
        disconnect(observer.client)
        live.close()


@pytest.fixture(scope="module")
def ha_live(tmp_path_factory):
    live = Live(tmp_path_factory.mktemp("home"), ha=True)
    yield live
    live.close()


def available(ha, entity_id) -> bool:
    s = ha.state(entity_id)
    return s is not None and s["state"] != "unavailable"


@HA
def test_home_assistant_s_tasmota_integration_names_each_device_as_vesper_expects(ha_live):
    ha, hub = ha_live.ha, ha_live.hub
    for name in ("kettle_plug", "reading_lamp"):
        for entity_id in hub.entities(name).values():
            assert until(lambda e=entity_id: available(ha, e)), entity_id


@HA
def test_home_assistant_commands_a_wi_fi_device_as_its_tasmota_integration_does(ha_live):
    ha, s = ha_live.ha, ha_live.session
    kettle, lamp = s.twin_ids["kettle_plug"], s.twin_ids["reading_lamp"]
    assert until(lambda: available(ha, "switch.kettle_plug") and available(ha, "light.reading_lamp"))
    ha.call("switch", "turn_on", {"entity_id": "switch.kettle_plug"})
    assert until(lambda: s.runtime.state(kettle)["POWER"] == "ON", 10)
    assert until(lambda: ha.state("switch.kettle_plug")["state"] == "on", 10)
    ha.call("light", "turn_on", {"entity_id": "light.reading_lamp", "brightness_pct": 50})  # a Backlog of Dimmer
    assert until(lambda: (s.runtime.state(lamp)["Dimmer"], s.runtime.state(lamp)["POWER"]) == (50, "ON"), 10)
    assert until(lambda: ha.state("light.reading_lamp")["state"] == "on", 10)
    assert not hub_rejections(ha_live)


def hub_rejections(live):
    return [r for d in live.hub.devices.values() for r in d.rejections]


@HA
def test_an_attack_on_a_wi_fi_device_s_link_finds_home_assistant_s_command_by_the_device_s_name(ha_live):
    """Phantom-Delay's shape on Wi-Fi: HA's command to the kettle is held on its own link while its keepalives
    pass, so the kettle stays available and switches late."""
    ha, s = ha_live.ha, ha_live.session
    kettle = s.twin_ids["kettle_plug"]
    assert until(lambda: available(ha, "switch.kettle_plug"))
    ha.call("switch", "turn_off", {"entity_id": "switch.kettle_plug"})
    assert until(lambda: s.runtime.state(kettle)["POWER"] == "OFF", 10)
    s.network_effect(Effect("delay", link="wifi/kettle_plug", direction="down", device="kettle_plug",
                            feature="POWER", hold_s=4.0, keep_connection=True))
    t0 = time.monotonic()
    ha.call("switch", "turn_on", {"entity_id": "switch.kettle_plug"})
    assert until(lambda: s.runtime.state(kettle)["POWER"] == "ON", 15)
    assert time.monotonic() - t0 >= 3.5
    assert available(ha, "switch.kettle_plug")
    s.network_clear("wifi/kettle_plug")
    held = [r for r in s.records if r["kind"] == "net" and r["link"] == "wifi/kettle_plug" and r["event"] == "held"]
    assert held and held[0]["device"] == "kettle_plug" and held[0]["feature"] == "POWER"


@HA
def test_a_wi_fi_device_out_of_range_is_unavailable_in_home_assistant_and_comes_back(ha_live):
    ha, s = ha_live.ha, ha_live.session
    assert until(lambda: available(ha, "switch.kettle_plug") and available(ha, "light.reading_lamp"))
    reach(s, -46.0)  # the kettle (-44.9 dBm) stays in range, the lamp (-47.4) is lost
    assert until(lambda: not available(ha, "light.reading_lamp"), 30)
    assert available(ha, "switch.kettle_plug")
    reach(s, -85.0)
    assert until(lambda: available(ha, "light.reading_lamp"), 30)
