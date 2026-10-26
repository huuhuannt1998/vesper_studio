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
import time
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


_RESERVED_LEVELS = ("set", "get", "event")  # command topics and the event topics of `ha_discovery`


def topic_safe(name: str) -> bool:
    """A friendly name usable as an MQTT topic path without colliding with the bridge's own topics or with
    another device's command and event topics."""
    parts = name.split("/")
    return (bool(name) and "+" not in name and "#" not in name and all(parts) and parts[0] != "bridge"
            and not any(part in _RESERVED_LEVELS for part in parts) and parts[-1] != "availability")


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
    events: object = None  # where reports come from (anything with `subscribe`): a home's network; default the runtime
    send: object = None  # send(name, feature, run): a command crosses the device's links, `run()` when it arrives

    def __post_init__(self):
        self._by_name: dict[str, Placement] = {}
        self._by_twin: dict[str, Placement] = {}
        for p in self.placements:
            vds = self.runtime.twins[p.twin_id].vds
            if "zigbee" not in vds["binding"]:
                raise ValueError(f"{p.twin_id} has no Zigbee binding; it cannot be exposed through this bridge")
            if not topic_safe(p.friendly_name):
                raise ValueError(f"friendly name {p.friendly_name!r} cannot be an MQTT topic here: no '+', '#', "
                                 "empty levels, leading 'bridge', set/get/event levels or trailing availability")
            if p.friendly_name in self._by_name:
                raise ValueError(f"duplicate friendly name {p.friendly_name!r}")
            self._by_name[p.friendly_name] = self._by_twin[p.twin_id] = p
        self._lock = threading.Lock()
        self._reported: dict[str, dict] = {}  # per twin: the values it has reported, as Zigbee2MQTT caches them
        self._color_mode: dict[str, str] = {}
        self._pending: list = []  # QoS 1 deliveries paho may still hold
        self._local = threading.local()
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        (self.events or self.runtime).subscribe(self._on_event)

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        b = self.base_topic
        for p in self.placements:
            twin = self.runtime.twins[p.twin_id]
            self._reported[p.twin_id] = dict(twin.state)
            light = {n for n, f in twin.features.items() if f.get("path") == "light"}
            if "color_temp" in light or "color.x" in light:
                self._color_mode[p.twin_id] = "color_temp" if "color_temp" in light else "xy"
        self._subscribe()
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
            topics = [f"{b}/{p.friendly_name}", f"{b}/{p.friendly_name}/availability"]
            if self.discovery:  # only what this bridge published: never another tool's discovery topics
                topics.insert(0, f"homeassistant/device/{ieee_address(p.twin_id)}/config")
            for topic in topics:
                self._track(self.client.publish(topic, "", qos=1, retain=True))
        for topic in ("bridge/devices", "bridge/info", "bridge/state"):
            self._track(self.client.publish(f"{b}/{topic}", "", qos=1, retain=True))

    def set_available(self, friendly_name: str, online: bool) -> None:
        """Mark one device available or not, as Zigbee2MQTT's availability check does when a device stops answering."""
        self._publish(f"{self.base_topic}/{friendly_name}/availability", {"state": "online" if online else "offline"},
                      retain=True)

    def stop(self) -> None:
        for p in self.placements:
            self._publish(f"{self.base_topic}/{p.friendly_name}/availability", {"state": "offline"}, retain=True)
        self._publish(f"{self.base_topic}/bridge/state", {"state": "offline"}, retain=True)

    def flush(self, timeout: float = 30.0) -> None:
        """Wait until paho has delivered every queued message; call before disconnecting, which drops them."""
        deadline = time.monotonic() + timeout
        for info in self._pending:
            try:
                info.wait_for_publish(max(0.0, deadline - time.monotonic()))
            except (RuntimeError, ValueError):  # not queued (connection gone); nothing left to wait for
                pass
        self._pending = [i for i in self._pending if not i.is_published()]

    def reported(self, twin_id: str) -> dict:
        """The values the twin has reported so far: what a platform can know."""
        return dict(self._reported.get(twin_id, {}))

    # -- outgoing ------------------------------------------------------------------------------
    def _vds(self, p: Placement) -> dict:
        return self.runtime.twins[p.twin_id].vds

    def _publish(self, topic: str, obj, retain: bool = False) -> None:
        self._track(self.client.publish(topic, json.dumps(obj, ensure_ascii=False), qos=1, retain=retain))

    def _track(self, info) -> None:
        if hasattr(info, "wait_for_publish"):
            self._pending.append(info)
            if len(self._pending) > 5000:
                self._pending = [i for i in self._pending if not i.is_published()]

    def state_payload(self, p: Placement) -> dict:
        """The device's state message: the reported values only (the reporting gate holds on the wire)."""
        twin = self.runtime.twins[p.twin_id]
        payload = twin.payload(self._reported[p.twin_id])
        for name, f in twin.features.items():  # events are not state: they go to their own topic
            if f.get("ha") == "event":
                payload.pop(name.split(".")[0], None)
        if p.twin_id in self._color_mode:
            payload["color_mode"] = self._color_mode[p.twin_id]
        return payload

    def _publish_state(self, p: Placement) -> None:
        self._publish(f"{self.base_topic}/{p.friendly_name}", self.state_payload(p), retain=True)

    def _on_event(self, event: Event) -> None:
        p = self._by_twin.get(event.twin_id)
        if p is None:
            return
        f = self.runtime.twins[event.twin_id].features[event.feature]
        if f.get("ha") == "event":
            topic = f"{self.base_topic}/{p.friendly_name}"
            self._publish(event_topic(topic, event.feature), {"event_type": str(event.value)})
            # Zigbee2MQTT also carries the action in a device message; it is never part of retained state
            self._publish(topic, {**self.state_payload(p), event.feature: event.value})
            return
        self._reported[event.twin_id][event.feature] = event.value
        if not getattr(self._local, "quiet", False):  # a command publishes once, after all its features
            self._publish_state(p)

    # -- incoming ------------------------------------------------------------------------------
    def _subscribe(self) -> None:
        self.client.subscribe(f"{self.base_topic}/#")
        self.client.subscribe(f"{CONTROL}/#")

    def _on_connect(self, client, userdata, flags, reason, properties=None) -> None:
        """paho reconnects by itself, but with a clean session: subscribe again, and say the bridge is online (the
        broker published its will, `offline`, when the connection died)."""
        self._subscribe()
        self._publish(f"{self.base_topic}/bridge/state", {"state": "online"}, retain=True)

    def _on_message(self, client, userdata, msg) -> None:
        topic, raw = msg.topic, msg.payload.decode("utf-8", "replace")
        try:
            self._handle(topic, raw)
        except Exception as e:  # an exception here would stop paho's network loop for every twin
            self.rejections.append({"topic": topic, "payload": raw[:200], "reason": f"internal error: {e!r}"})

    def _handle(self, topic: str, raw: str) -> None:
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
                self._get(self._by_name[rest[:-4]], self._parse(raw))

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
        if self.send is not None:
            features = tuple(f for f, _ in flatten(command))  # what the command sets: an effect can narrow to them
            self.send(name, features, lambda: self._apply(p, name, command))
        else:
            self._apply(p, name, command)

    def _apply(self, p: Placement, name: str, command: dict) -> None:
        accepted = []
        self._local.quiet = True
        try:
            for feature, value in flatten(command):
                if self.runtime.platform_set(p.twin_id, feature, value):
                    accepted.append(feature)
                else:
                    self.rejections.append({"device": name, "feature": feature, "value": value,
                                            "reason": self.runtime.actions[-1].reason
                                            if self.runtime.actions else "rejected"})
        finally:
            self._local.quiet = False
        state = self.runtime.state(p.twin_id)
        for feature in accepted:  # like Zigbee2MQTT, publish a successful command's result at once
            self._reported[p.twin_id][feature] = state[feature]
        if p.twin_id in self._color_mode:
            if any(f.startswith("color.") for f in accepted):
                self._color_mode[p.twin_id] = "xy"
            elif "color_temp" in accepted:
                self._color_mode[p.twin_id] = "color_temp"
        self._publish_state(p)

    def _get(self, p: Placement, command) -> None:
        """Like Zigbee2MQTT's /get: read the attributes from the device, then publish its state."""
        state = self.runtime.state(p.twin_id)
        names = [f for f, _ in flatten(command)] if isinstance(command, dict) and command else list(state)
        events = {n for n, f in self.runtime.twins[p.twin_id].features.items() if f.get("ha") == "event"}
        for name in names:
            if name in state and name not in events:
                self._reported[p.twin_id][name] = state[name]
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
        # nothing is published here: the twin's own reports (through the gate) reach MQTT via _on_event
