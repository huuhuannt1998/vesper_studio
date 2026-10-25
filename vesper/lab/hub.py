"""The hub in the loop (spec §2, §3): the running home's Zigbee devices on VESPER's broker, through the same
Zigbee2MQTT bridge `vesper twins run` uses, so that an unmodified Home Assistant sees them and commands them.

Every message from the broker is handed to the session's clock thread, like every other change to the world:
the rules, the world and the trace are only ever touched there. Home Assistant's commands reach the twins as
platform commands, so the session traces them (`hub` inputs) and a replay needs no hub. Matter devices are not
on the hub yet (plan W2d); `skipped` names them.
"""
from __future__ import annotations

from vesper.exposure.z2m_bridge import Bridge, Placement


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
        self.skipped = [d.name for d in devices if "zigbee" not in d.record["binding"]]
        self._placements = {d.name: Placement(session.twin_ids[d.name], d.name) for d in devices
                            if "zigbee" in d.record["binding"]}
        self.bridge = Bridge(session.runtime, list(self._placements.values()), ClockThreadClient(client, session.clock),
                             base_topic=base_topic, zhc_version=zhc_version)

    def start(self) -> None:
        self.bridge.start()

    def stop(self) -> None:
        self.bridge.stop()
        self.bridge.flush()

    def entities(self, name: str) -> dict[str, str]:
        """The Home Assistant entities of one device, by feature: what an automation names."""
        p = self._placements.get(name)
        if p is None:
            raise KeyError(f"{name} is not on the hub" + (" (Matter: plan W2d)" if name in self.skipped else ""))
        return {e.feature: e.entity_id for e in self.bridge.expected.get(p.twin_id, [])}
