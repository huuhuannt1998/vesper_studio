"""The home's Wi-Fi devices on the hub (security-lab spec §3.3, plan W7a2): each a virtual Tasmota device with its own
MQTT connection to VESPER's broker, as a real one has, through its own `wifi/<name>` link (on the live network, the
relay's).

A device's commands and reports cross its links as a Zigbee device's do, and every message from the broker is handed
to the session's clock thread. Joining never waits for the connection: the device is on the hub at once, its client
connects (and reconnects) on its own thread, and every connection announces the device. A station out of range from
the start never connects, so Home Assistant never hears of it, until it comes into range and joins. A station that
goes out of range is lost: on the live network the relay passes nothing of it, and the broker notices only by its
keepalive timeout, publishing its will (Home Assistant marks it unavailable); back in range, after its rejoin
back-off, it connects anew and says it is online.
"""
from __future__ import annotations

from collections.abc import Callable

from vesper.exposure.tasmota import TasmotaDevice, expected_entities, mac_of, topic_of
from vesper.lab.hub import ClockThreadClient

KEEPALIVE_S = 30  # Tasmota's MqttKeepAlive: the broker notices a lost station after 1.5 times this
RETRY_S = 10  # Tasmota's MqttRetry: how soon it tries the broker again after losing it


class TasmotaHub:
    def __init__(self, session, connect: Callable, port: int,
                 open_client: Callable = lambda client: client.loop_start(),
                 disconnect: Callable = lambda client: None):
        """`connect(port, client_id, will, keepalive)`: an MQTT client for VESPER's broker on `port` (or the relay's
        port for the device's link), not yet connected; `open_client(client)` lets it connect, without waiting;
        `disconnect(client)` closes one."""
        self.session, self._connect, self._open, self._disconnect = session, connect, open_client, disconnect
        self.port = port
        self._wifi = {d.name: d for d in session.home.devices if "tasmota" in d.record["binding"]}
        self.devices: dict[str, TasmotaDevice] = {}
        self._clients: dict = {}
        self._ports: dict[str, int] = {}  # device -> its relay link's port
        self._running = False

    def start(self) -> None:
        self._running = True
        relay = self.session.relay
        for name in self._wifi:
            link = f"wifi/{name}"
            if relay is not None:  # every device's link exists from the start: an attack can name it
                self._ports[name] = relay.add_link(link, self.port, client_is_device=True)
            if self._up(name):
                self._join(name)
            elif relay is not None:
                relay.lose(link)
        self.session.on_record(self._on_record)

    def stop(self) -> None:
        self._running = False
        for name, device in list(self.devices.items()):
            device.stop()
            self._disconnect(self._clients[name])

    @property
    def ports(self) -> dict[str, int]:
        """Each device's relay link port, on the live network."""
        return dict(self._ports)

    def entities(self, name: str) -> dict[str, str]:
        """The Home Assistant entities of one Wi-Fi device, by feature: what an automation names."""
        return {e.feature: e.entity_id for e in expected_entities(self._wifi[name].record, name)}

    def _up(self, name: str) -> bool:
        links = self.session.links
        return links is None or links.is_up(f"wifi/{name}")

    def _on_record(self, r: dict) -> None:
        """The link layer's word on a station's reach (not an attack's cut, which acts on the link itself)."""
        if not self._running or r.get("kind") != "net" or not r["link"].startswith("wifi/"):
            return
        name, relay = r["link"][len("wifi/"):], self.session.relay
        if name not in self._wifi:
            return
        if r["event"] == "down" and r.get("reason") == "out of range":
            if relay is not None:
                relay.lose(r["link"])
        elif r["event"] == "up" and r.get("reason") in ("rejoined", "in range"):
            if relay is not None:
                relay.rejoin(r["link"])
            if name not in self.devices:
                self._join(name)

    def _join(self, name: str) -> None:
        s, relay, links = self.session, self.session.relay, self.session.links
        twin_id = s.twin_ids[name]
        topic = topic_of(mac_of(twin_id))
        client = self._connect(self._ports.get(name, self.port), topic, TasmotaDevice.will(topic), KEEPALIVE_S)
        device = TasmotaDevice(s.runtime, twin_id, name, ClockThreadClient(client, s.clock), events=links,
                               send=(lambda n, features, run: links.down(n, features, run)) if links else None)
        if relay is not None:
            relay.add_device(topic, device.identify)
        self._clients[name], self.devices[name] = client, device
        self._open(client)  # the device announces itself on each connection (its on_connect)
