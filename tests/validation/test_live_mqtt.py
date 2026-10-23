"""Against a real Mosquitto (opt-in: VESPER_STACK_TESTS=1; needs `mosquitto` installed).

VESPER starts its own broker on a free local port; it never uses another broker or port 1883.
"""
import json
import os
import socket
import threading

import pytest

from tests.catalog.fixtures import ZIGBEE_DOC
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.exposure.mqtt import connect, disconnect
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.roundtrip import compare, receive_retained, source_definitions
from vesper.validation.stack import Mosquitto

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_STACK_TESTS") != "1", reason="set VESPER_STACK_TESTS=1")]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path):
    port = free_port()
    broker = Mosquitto(tmp_path, port=port)
    broker.start()
    runtime = Runtime(clock=LogicalClock())
    records = zigbee_records(ZIGBEE_DOC)
    placements = [Placement(runtime.add(r).id, f"vesper_live_{i:02d}") for i, r in enumerate(records)]
    bridge_client, checker = connect("127.0.0.1", port, "live-bridge"), connect("127.0.0.1", port, "live-check")
    bridge = Bridge(runtime, placements, bridge_client, discovery=False, zhc_version=ZIGBEE_DOC["version"])
    bridge.start()
    yield bridge, checker
    bridge.retract()
    disconnect(bridge_client)
    disconnect(checker)
    broker.stop()


def test_definitions_survive_a_real_broker_unchanged(live):
    bridge, checker = live
    devices = receive_retained(checker, "zigbee2mqtt/bridge/devices", timeout=10)
    report = compare(devices, {p.friendly_name: p.twin_id for p in bridge.placements}, source_definitions(ZIGBEE_DOC))
    assert report["identical"] == report["checked"] == len(bridge.placements)


def test_a_set_command_over_the_network_changes_the_twin_and_comes_back_as_state(live):
    bridge, checker = live
    plug = next(p for p in bridge.placements if "state" in bridge.runtime.twins[p.twin_id].features)
    topic, states, got = f"zigbee2mqtt/{plug.friendly_name}", [], threading.Event()

    def on_message(c, userdata, msg):
        if msg.topic == topic and msg.payload:
            states.append(json.loads(msg.payload))
            if states[-1].get("state") == "ON":
                got.set()

    checker.on_message = on_message
    checker.subscribe(topic)
    checker.publish(f"{topic}/set", json.dumps({"state": "ON"}))
    assert got.wait(10), states
    assert bridge.runtime.state(plug.twin_id)["state"] == "ON"
