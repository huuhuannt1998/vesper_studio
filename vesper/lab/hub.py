"""The hub in the loop (spec §2, §3): the running home's Zigbee devices on VESPER's broker, through the same
Zigbee2MQTT bridge `vesper twins run` uses, so that an unmodified Home Assistant sees them and commands them.

Every message from the broker is handed to the session's clock thread, like every other change to the world:
the rules, the world and the trace are only ever touched there. Home Assistant's commands reach the twins as
platform commands, so the session traces them (`hub` inputs) and a replay needs no hub. Wi-Fi devices are on the
hub through their own connections (`TasmotaHub`, plan W7a2); Matter devices are not on the hub yet (plan W2d):
`skipped` names them.
"""
from __future__ import annotations

from vesper.exposure.z2m_bridge import Bridge, Placement, device_entry, ieee_address

# Zigbee2MQTT's availability timeouts: a device that stops answering is marked offline after this long
# (its `availability.active.timeout` and `passive.timeout` defaults: 10 min and 1500 min).
AVAILABILITY_S = {"active": 600.0, "passive": 90000.0}


class ClockThreadClient:
    """An MQTT client whose message and connect callbacks run on a clock's thread."""

    def __init__(self, client, clock):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_clock", clock)

    def __getattr__(self, name):
        return getattr(self._client, name)

    def __setattr__(self, name, value):
        if name in ("on_message", "on_connect") and callable(value):
            fn, clock = value, self._clock

            def on_clock(*args):
                clock.call_at(clock.now(), lambda: fn(*args))
            value = on_clock
        setattr(self._client, name, value)


class Hub:
    def __init__(self, session, client, *, zhc_version: str = "unknown", base_topic: str = "zigbee2mqtt"):
        self.session = session
        devices = session.home.devices
        self.skipped = [d.name for d in devices if not {"zigbee", "tasmota"} & set(d.record["binding"])]
        self._placements = {d.name: Placement(session.twin_ids[d.name], d.name) for d in devices
                            if "zigbee" in d.record["binding"]}
        links = session.links  # the home's network: each device's reports and commands cross its hop
        self.bridge = Bridge(session.runtime, list(self._placements.values()), ClockThreadClient(client, session.clock),
                             base_topic=base_topic, zhc_version=zhc_version, events=links,
                             send=(lambda name, feature, run: links.down(name, feature, run)) if links else None)
        self._passive = {name: device_entry(session.runtime.twins[p.twin_id].vds, ieee=ieee_address(p.twin_id),
                                            friendly_name=name)["type"] == "EndDevice"
                         for name, p in self._placements.items()}
        self._down: dict[str, int] = {}  # device -> the down event it is waiting out
        self._offline: set[str] = set()
        session.on_record(self._on_record)
        for name in self._placements:  # a hop down before the hub started (out of range from the start) counts too
            if links is not None and f"zigbee/{name}" in links.network.links and not links.is_up(f"zigbee/{name}"):
                self._on_record({"kind": "net", "link": f"zigbee/{name}", "event": "down"})

    def start(self) -> None:
        self.bridge.start()

    def stop(self) -> None:
        self.bridge.stop()
        self.bridge.flush()

    def _on_record(self, r: dict) -> None:
        """A device's hop down for Zigbee2MQTT's availability timeout makes it unavailable; up again, available."""
        if r.get("kind") != "net" or not r["link"].startswith("zigbee/") or r["event"] not in ("down", "up"):
            return
        name = r["link"][len("zigbee/"):]
        if name not in self._placements:
            return
        if r["event"] == "down":
            token = self._down[name] = self._down.get(name, 0) + 1
            timeout = AVAILABILITY_S["passive" if self._passive[name] else "active"]
            self.session.clock.call_at(self.session.clock.now() + timeout, lambda: self._expired(name, token))
        else:
            self._down[name] = self._down.get(name, 0) + 1  # any wait in progress is void
            if name in self._offline:
                self._offline.discard(name)
                self.bridge.set_available(name, True)

    def _expired(self, name: str, token: int) -> None:
        if self._down.get(name) == token:
            self._offline.add(name)
            self.bridge.set_available(name, False)

    def entities(self, name: str) -> dict[str, str]:
        """The Home Assistant entities of one device, by feature: what an automation names."""
        p = self._placements.get(name)
        if p is None:
            raise KeyError(f"{name} is not on the hub" + (" (Matter: plan W2d)" if name in self.skipped else ""))
        return {e.feature: e.entity_id for e in self.bridge.expected.get(p.twin_id, [])}
