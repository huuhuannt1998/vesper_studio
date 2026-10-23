"""Zigbee2MQTT-compatible MQTT interface for twins (spec §2 `vesper/exposure/z2m_bridge`; L2 `z2m-api`).

Implements the MQTT contract Zigbee2MQTT documents: retained `bridge/state`, `bridge/info` and
`bridge/devices` (with each product's real definition), per-device state, `/set`, `/set/<attribute>`,
`/get` and `/availability`, plus Home Assistant device discovery. It reuses no Zigbee2MQTT (GPL-3.0)
code. A VESPER control channel (`vesper/control/stimulate|operate`) lets other processes, such as the
Habitat resident, drive twins over MQTT.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field

from vesper import __version__
from vesper.exposure.ha_discovery import ExpectedEntity, device_discovery, event_topic
from vesper.twins.runtime import Event, Runtime

CONTROL = "vesper/control"


@dataclass(frozen=True)
class Placement:
    twin_id: str
    friendly_name: str


def ieee_address(twin_id: str) -> str:
    return "0x" + hashlib.sha256(twin_id.encode()).hexdigest()[:16]


def definition(vds: dict) -> dict:
    """The `definition` object Zigbee2MQTT publishes for this product in `bridge/devices`."""
    zigbee = vds["binding"]["zigbee"]
    identity = vds["identity"]
    return {"source": "native", "model": identity["model"], "vendor": identity["vendor"],
            "description": identity["description"], "exposes": zigbee["exposes"], "options": zigbee["options"],
            "supports_ota": zigbee["ota"]}


def device_entry(vds: dict, *, ieee: str, friendly_name: str) -> dict:
    ids = vds["identity"]["ids"]
    battery = any(f["name"] == "battery" for f in vds["features"])
    model_id = next(iter(ids.get("zigbee_model") or []), None) or next(
        (fp.get("modelID") for fp in ids.get("zigbee_fingerprint", []) if fp.get("modelID")), None)
    return {"ieee_address": ieee, "type": "EndDevice" if battery else "Router",
            "network_address": int(ieee[2:6], 16) % 0xFFF7 + 1, "supported": True, "disabled": False,
            "friendly_name": friendly_name, "definition": definition(vds),
            "power_source": "Battery" if battery else "Mains (single phase)", "model_id": model_id,
            "interview_state": "SUCCESSFUL", "interviewing": False, "interview_completed": True, "endpoints": {}}


def topic_safe(name: str) -> bool:
    """A friendly name usable as an MQTT topic level path without colliding with the bridge's own topics."""
    parts = name.split("/")
    return (bool(name) and "+" not in name and "#" not in name and all(parts) and parts[0] != "bridge"
            and parts[-1] not in ("set", "get", "availability"))


def flatten(payload: dict, prefix: str = "") -> list[tuple[str, object]]:
    """{"color": {"x": 0.3}} -> [("color.x", 0.3)]; feature names use the same dotted paths."""
    out = []
    for key, value in payload.items():
        if isinstance(value, dict):
            out.extend(flatten(value, f"{prefix}{key}."))
        else:
            out.append((f"{prefix}{key}", value))
    return out


@dataclass
class Bridge:
    runtime: Runtime
    placements: list[Placement]
    client: object  # paho.mqtt.client.Client or a stand-in with publish/subscribe/on_message
    base_topic: str = "zigbee2mqtt"
    zhc_version: str = "unknown"
    discovery: bool = True
    expected: dict[str, list[ExpectedEntity]] = field(default_factory=dict)
    skipped: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    rejections: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self._by_name: dict[str, Placement] = {}
        self._by_twin: dict[str, Placement] = {}
        for p in self.placements:
            vds = self.runtime.twins[p.twin_id].vds
            if "zigbee" not in vds["binding"]:
                raise ValueError(f"{p.twin_id} has no Zigbee binding; it cannot be exposed through this bridge")
            if not topic_safe(p.friendly_name):
                raise ValueError(f"friendly name {p.friendly_name!r} cannot be an MQTT topic here: no '+', '#', "
                                 "empty levels, leading 'bridge' or trailing set/get/availability")
            if p.friendly_name in self._by_name:
                raise ValueError(f"duplicate friendly name {p.friendly_name!r}")
            self._by_name[p.friendly_name] = self._by_twin[p.twin_id] = p
        self._lock = threading.Lock()
        self.client.on_message = self._on_message
        self.runtime.subscribe(self._on_event)

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        b = self.base_topic
        self.client.subscribe(f"{b}/#")
        self.client.subscribe(f"{CONTROL}/#")
        self._publish(f"{b}/bridge/state", {"state": "online"}, retain=True)
        self._publish(f"{b}/bridge/info", {
            "version": f"vesper-{__version__}", "commit": None, "permit_join": False,
            "coordinator": {"ieee_address": "0x" + "0" * 16, "type": "vesper-virtual"},
            "zigbee_herdsman_converters": {"version": self.zhc_version}, "config": {"homeassistant": self.discovery},
        }, retain=True)
        coordinator = {"ieee_address": "0x" + "0" * 16, "type": "Coordinator", "network_address": 0,
                       "supported": False, "disabled": False, "friendly_name": "Coordinator", "definition": None,
                       "power_source": None, "model_id": None, "interview_state": "SUCCESSFUL",
                       "interviewing": False, "interview_completed": True, "endpoints": {}}
        entries = [device_entry(self._vds(p), ieee=ieee_address(p.twin_id), friendly_name=p.friendly_name)
                   for p in self.placements]
        self._publish(f"{b}/bridge/devices", [coordinator, *entries], retain=True)
        for p in self.placements:
            ieee = ieee_address(p.twin_id)
            if self.discovery:
                topic, payload, expected, skipped = device_discovery(self._vds(p), ieee=ieee,
                                                                     friendly_name=p.friendly_name, base_topic=b)
                self.expected[p.twin_id], self.skipped[p.twin_id] = expected, skipped
                self._publish(topic, payload, retain=True)
            self._publish(f"{b}/{p.friendly_name}/availability", {"state": "online"}, retain=True)
            self._publish_state(p)

    def retract(self) -> None:
        """Remove every retained message this bridge published, so Home Assistant forgets the twins."""
        b = self.base_topic
        for p in self.placements:
            for topic in (f"homeassistant/device/{ieee_address(p.twin_id)}/config", f"{b}/{p.friendly_name}",
                          f"{b}/{p.friendly_name}/availability"):
                self.client.publish(topic, "", qos=1, retain=True)
        for topic in ("bridge/devices", "bridge/info", "bridge/state"):
            self.client.publish(f"{b}/{topic}", "", qos=1, retain=True)

    def stop(self) -> None:
        for p in self.placements:
            self._publish(f"{self.base_topic}/{p.friendly_name}/availability", {"state": "offline"}, retain=True)
        self._publish(f"{self.base_topic}/bridge/state", {"state": "offline"}, retain=True)

    # -- outgoing ------------------------------------------------------------------------------
    def _vds(self, p: Placement) -> dict:
        return self.runtime.twins[p.twin_id].vds

    def _publish(self, topic: str, obj, retain: bool = False) -> None:
        self.client.publish(topic, json.dumps(obj, ensure_ascii=False), qos=1, retain=retain)

    def state_payload(self, p: Placement) -> dict:
        twin = self.runtime.twins[p.twin_id]
        payload = twin.payload()
        for name, f in twin.features.items():  # events are not state: they go to their own topic
            if f.get("ha") == "event":
                payload.pop(name.split(".")[0], None)
        light = {n for n, f in twin.features.items() if f.get("path") == "light"}
        if "color_temp" in light or "color.x" in light:
            payload["color_mode"] = "color_temp" if "color_temp" in light else "xy"
        return payload

    def _publish_state(self, p: Placement) -> None:
        self._publish(f"{self.base_topic}/{p.friendly_name}", self.state_payload(p), retain=True)

    def _on_event(self, event: Event) -> None:
        p = self._by_twin.get(event.twin_id)
        if p is None:
            return
        f = self.runtime.twins[event.twin_id].features[event.feature]
        if f.get("ha") == "event":
            topic = event_topic(f"{self.base_topic}/{p.friendly_name}", event.feature)
            self._publish(topic, {"event_type": str(event.value)})
        else:
            self._publish_state(p)

    # -- incoming ------------------------------------------------------------------------------
    def _on_message(self, client, userdata, msg) -> None:
        topic, raw = msg.topic, msg.payload.decode("utf-8", "replace")
        with self._lock:
            if topic.startswith(f"{CONTROL}/"):
                self._control(topic[len(CONTROL) + 1:], raw)
                return
            if not topic.startswith(f"{self.base_topic}/"):
                return
            rest = topic[len(self.base_topic) + 1:]
            if rest.startswith("bridge/request/"):
                self._publish(f"{self.base_topic}/bridge/response/{rest[len('bridge/request/'):]}",
                              {"status": "error", "error": "not supported by the VESPER bridge", "data": {}})
            elif "/set/" in rest:
                name, attribute = rest.split("/set/", 1)
                self._set(name, {attribute: self._parse(raw)}, raw)
            elif rest.endswith("/set"):
                self._set(rest[:-4], self._parse(raw), raw)
            elif rest.endswith("/get") and rest[:-4] in self._by_name:
                self._publish_state(self._by_name[rest[:-4]])

    @staticmethod
    def _parse(raw: str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def _set(self, name: str, command, raw: str) -> None:
        p = self._by_name.get(name)
        if p is None:
            return  # not one of ours, or our own state topic
        if not isinstance(command, dict):
            self.rejections.append({"device": name, "payload": raw, "reason": "payload is not a JSON object"})
            return
        for feature, value in flatten(command):
            if not self.runtime.platform_set(p.twin_id, feature, value):
                self.rejections.append({"device": name, "feature": feature, "value": value,
                                        "reason": self.runtime.actions[-1].reason
                                        if self.runtime.actions else "rejected"})
        self._publish_state(p)

    def _control(self, action: str, raw: str) -> None:
        try:
            msg = json.loads(raw)
            p = self._by_name[msg["device"]]
        except (ValueError, KeyError, TypeError):
            self.rejections.append({"control": action, "payload": raw, "reason": "bad control message"})
            return
        if action == "stimulate":
            self.runtime.stimulate(p.twin_id, msg.get("stimulus", {}))
        elif action == "operate":
            if not self.runtime.operate(p.twin_id, msg.get("feature"), msg.get("value"), msg.get("actor", "resident")):
                self.rejections.append({"device": p.friendly_name, "control": "operate", "reason":
                                        self.runtime.actions[-1].reason if self.runtime.actions else "rejected"})
        self._publish_state(p)
