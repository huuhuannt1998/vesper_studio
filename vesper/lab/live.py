"""Home Assistant in the loop (Lab spec §2, security-lab spec §3.4): VESPER's broker, the relay and Home Assistant
for a Lab command, and the hub (the Zigbee bridge and the Wi-Fi devices) for a session on the live network.
`vesper lab play|look --hub` and live scenario runs share them; one stack can serve several sessions in turn.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from vesper.exposure.z2m_bridge import OFFLINE_WILL


def zhc_version(catalog_dir: Path) -> str:
    """The zigbee-herdsman-converters version the catalog was built from (what the bridge says it runs)."""
    report = Path(catalog_dir) / "build_report.json"
    if report.is_file():
        return json.loads(report.read_text()).get("inputs", {}).get("zigbee", {}).get("version", "unknown")
    return "unknown"


@contextmanager
def lab_stack(root: Path, home, keep=None):
    """VESPER's broker and Home Assistant (the dashboard framable by the page, the home's automations loaded, the
    Lab login), Home Assistant's MQTT through the relay's hub link; stopped again whatever happens, a stop signal
    included. `keep(ha, rules)` runs before Home Assistant stops (play keeps automations edited in its UI)."""
    from vesper.lab.relay import Relay, SessionSink
    from vesper.validation import stack
    port = home.broker[1]
    rules = home.automations.read_text() if home.automations is not None else "[]\n"
    broker = stack.Mosquitto(Path(root), port=port)
    sink = SessionSink()  # the session starts once Home Assistant is up; the relay must exist before
    relay = Relay(record=sink.record, allowed={port}, now=sink.now, latency=sink.latency)
    try:
        broker.start()
        relay.start()
        hub_port = relay.add_link("hub", port, client_is_device=False)  # Home Assistant's MQTT: the home's network
        ha = stack.HomeAssistant(Path(root), frameable=True, automations=rules)
        try:
            ha.start()
            ha.onboard()
            ha.ensure_mqtt(hub_port)
            if any("tasmota" in d.record["binding"] for d in home.devices):
                ha.ensure_tasmota()  # Home Assistant's Tasmota integration hears the Wi-Fi devices (plan W7a2)
            else:
                ha.forget("tasmota")  # no Wi-Fi device of an earlier home lingers in Home Assistant
            yield SimpleNamespace(ha=ha, port=port, hub_port=hub_port, relay=relay, sink=sink, login=ha.lab_login())
        finally:
            if keep is not None:
                keep(ha, rules)
            ha.stop()
    finally:
        relay.stop()
        broker.stop()


@contextmanager
def lab_hub(session, port: int, catalog_dir: Path, last_seen=None):
    """The session's Zigbee devices on the broker through the bridge, and its Wi-Fi devices each on its own
    connection (`hub.wifi`), for as long as the block runs. `last_seen`: the bridge's Zigbee2MQTT `last_seen`."""
    from vesper.exposure import mqtt
    from vesper.lab.hub import Hub
    from vesper.lab.tasmota_hub import RETRY_S, TasmotaHub
    via = session.relay.add_link("coordinator", port, client_is_device=True) if session.relay else port
    client = mqtt.connect("127.0.0.1", via, "vesper-lab-bridge", will=OFFLINE_WILL)
    try:
        hub = Hub(session, client, zhc_version=zhc_version(catalog_dir), last_seen=last_seen)
        hub.coordinator_port = via
        hub.start()
        try:
            hub.wifi = TasmotaHub(session, lambda p, cid, will, keepalive: mqtt.connect_async(
                "127.0.0.1", p, cid, will=will, keepalive=keepalive, retry_s=RETRY_S), port, disconnect=mqtt.disconnect)
            try:
                hub.wifi.start()
                yield hub
            finally:
                hub.wifi.stop()
        finally:
            hub.stop()
    finally:
        mqtt.disconnect(client)
