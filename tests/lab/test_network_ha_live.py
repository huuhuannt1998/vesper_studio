"""The home's network end to end with the real Home Assistant (opt-in: VESPER_HA_TESTS=1, after `vesper setup ha`;
VESPER_ROOT names its folder, default .vesper). Security-lab spec §0, success criterion 3: cutting one link silences
exactly its devices in Home Assistant.

It runs VESPER's broker and Home Assistant on their own ports (18830, 18123), as `vesper lab play --hub` does, and
takes about a minute.
"""
import os
import time
from pathlib import Path

import pytest

from tests.lab.homes import catalog, write_home
from vesper.exposure.mqtt import connect, disconnect
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.linklayer import Effect
from vesper.lab.relay import Relay, SessionSink
from vesper.lab.session import Session
from vesper.twins.clock import WallClock
from vesper.validation import stack

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_HA_TESTS") != "1", reason="set VESPER_HA_TESTS=1")]

WILL = ("zigbee2mqtt/bridge/state", '{"state": "offline"}')
ZIGBEE = ("front_door", "hall_motion", "bedroom_lamp", "bedroom_button", "kitchen_climate")


def until(check, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def ha_live(tmp_path_factory):
    from vesper.lab import hub as hub_module
    saved = dict(hub_module.AVAILABILITY_S)
    hub_module.AVAILABILITY_S.update(active=2.0, passive=2.0)
    root, tmp = Path(os.environ.get("VESPER_ROOT", ".vesper")), tmp_path_factory.mktemp("home")
    broker = stack.Mosquitto(root, port=18830)
    broker.start()
    sink = SessionSink()
    relay = Relay(record=sink.record, allowed={18830}, now=sink.now, latency=sink.latency)
    relay.start()
    ha = stack.HomeAssistant(root, automations="[]\n")
    ha.start()
    ha.onboard()
    ha.ensure_mqtt(relay.add_link("hub", 18830, client_is_device=False))
    home = load_lab_home(write_home(tmp), catalog())
    session = Session(home, clock=WallClock(), live_network=True)
    session.relay = relay
    sink.attach(session)
    session.start()
    client = connect("127.0.0.1", relay.add_link("coordinator", 18830, client_is_device=True), "vesper-lab-bridge",
                     will=WILL)
    client.reconnect_delay_set(min_delay=1, max_delay=2)
    hub = Hub(session, client)
    hub.start()
    yield session, hub, ha
    hub.stop()
    hub.bridge.retract()
    disconnect(client)
    session.stop()
    ha.stop()
    relay.stop()
    broker.stop()
    hub_module.AVAILABILITY_S.update(saved)


def available(ha, hub, name) -> bool:
    entity = hub.entities(name)
    s = ha.state(sorted(entity.values())[0])
    return s is not None and s["state"] != "unavailable"


def test_home_assistant_sees_every_zigbee_device_through_the_network(ha_live):
    session, hub, ha = ha_live
    for name in ZIGBEE:
        assert until(lambda name=name: available(ha, hub, name)), name


def test_a_cut_hop_makes_only_its_device_unavailable_in_home_assistant(ha_live):
    session, hub, ha = ha_live
    assert until(lambda: all(available(ha, hub, n) for n in ZIGBEE))
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    assert until(lambda: not available(ha, hub, "bedroom_lamp"))
    assert all(available(ha, hub, n) for n in ZIGBEE if n != "bedroom_lamp")
    session.network_clear("zigbee/bedroom_lamp")
    assert until(lambda: available(ha, hub, "bedroom_lamp"))


def test_a_reset_coordinator_link_makes_every_zigbee_device_unavailable_until_the_bridge_is_back(ha_live):
    session, hub, ha = ha_live
    assert until(lambda: all(available(ha, hub, n) for n in ZIGBEE))
    session.network_effect(Effect("cut", link="coordinator", keep_connection=False))
    assert until(lambda: not any(available(ha, hub, n) for n in ZIGBEE))
    session.network_clear("coordinator")
    assert until(lambda: all(available(ha, hub, n) for n in ZIGBEE))
