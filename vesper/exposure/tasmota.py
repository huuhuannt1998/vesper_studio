"""A virtual Tasmota device on MQTT (security-lab spec §3.3, plan W7a2): one twin, with its own MQTT connection as a
real Tasmota device has, speaking Tasmota's topics so that Home Assistant's Tasmota integration (HATasmota, MIT) takes
it for one.

- Discovery: `tasmota/discovery/<MAC>/config` and `/sensors`, retained, in HATasmota's schema (`ver: 1`).
- Availability: `tele/<topic>/LWT`, `Online` on connecting and the will `Offline`, both retained.
- Commands: `cmnd/<topic>/POWER[n]` (ON, OFF, TOGGLE, or empty to ask; POWER1 is POWER on a device with one relay,
  as HATasmota sends it), `cmnd/<topic>/Dimmer`, `cmnd/<topic>/Backlog` (`NoDelay;<command> <value>;...`, as HATasmota
  sends a light's commands: run in order, as one command), `cmnd/<topic>/STATE`, `cmnd/<topic>/STATUS` (10, 11);
  results on `stat/<topic>/RESULT` and `stat/<topic>/POWER[n]`. Commands are case-insensitive, as Tasmota's. A
  transition (Fade, Speed) is taken and not modelled: a light changes at once.
- State: `tele/<topic>/STATE`, the whole state, every TelePeriod (the twin's periodic report) and on request.

The device is virtual: no firmware, no hardware, no network but VESPER's. Its MAC is a locally administered address
derived from the twin's id, so it is stable and never a real device's.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field

from vesper.exposure.ha_discovery import ExpectedEntity
from vesper.twins.runtime import Runtime

DISCOVERY_PREFIX = "tasmota/discovery"
SOFTWARE = "14.3.0"  # the Tasmota release whose discovery and topics this follows
OPTIONS = {k: 0 for k in ("4", "11", "13", "17", "20", "30", "68", "73", "82", "114", "117")}
RELAYS_ANNOUNCED = 8  # Tasmota announces eight relay slots, unused ones 0
TRANSITIONS = ("FADE", "SPEED")  # taken, not modelled


def mac_of(twin_id: str) -> str:
    """A stable, locally administered, unicast MAC (12 hex digits, upper case) for a twin."""
    b = bytearray(hashlib.sha256(twin_id.encode()).digest()[:6])
    b[0] = (b[0] | 0x02) & 0xFE
    return b.hex().upper()


def topic_of(mac: str) -> str:
    return f"tasmota_{mac[-6:]}"  # Tasmota's default topic


def expected_entities(record: dict, friendly_name: str) -> list[ExpectedEntity]:
    """The entities Home Assistant creates from the device's discovery: HATasmota names relay 1 after the device (its
    friendly name is the device's) and relay n `<device> switch <n>`; a dimmer is one light, its Dimmer the light's
    brightness. What automations name."""
    binding, base = record["binding"]["tasmota"], re.sub(r"[^a-z0-9]+", "_", friendly_name.lower()).strip("_")
    if binding["pwm"]:
        return [ExpectedEntity(f"light.{base}", "light", None, None, "POWER", True)]
    if "POWER" in {f["name"] for f in record["features"]}:
        return [ExpectedEntity(f"switch.{base}", "switch", None, None, "POWER", True)]
    return [ExpectedEntity(f"switch.{base}" if n == 1 else f"switch.{base}_switch_{n}", "switch", None, None,
                           f"POWER{n}", True) for n in range(1, binding["relays"] + 1)]


def _time(clock) -> str:
    s = int(clock.now())
    return f"1970-01-01T{s // 3600 % 24:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


@dataclass
class TasmotaDevice:
    runtime: Runtime
    twin_id: str
    friendly_name: str
    client: object  # one paho client per device, or a stand-in with publish/subscribe/on_message
    events: object = None  # where the device's own reports come from (its link); default the runtime
    send: object = None  # send(name, features, run): a command crosses the device's links
    rejections: list[dict] = field(default_factory=list)

    def __post_init__(self):
        twin = self.runtime.twins[self.twin_id]
        if "tasmota" not in twin.vds["binding"]:
            raise ValueError(f"{self.twin_id} has no Tasmota binding")
        self.binding = twin.vds["binding"]["tasmota"]
        self.features = {f["name"] for f in twin.vds["features"]}
        self._named = {f.upper(): f for f in self.features}  # Tasmota's commands are case-insensitive
        if "POWER" in self.features:
            self._named.setdefault("POWER1", "POWER")  # one relay: POWER1 is POWER
        self.mac = mac_of(self.twin_id)
        self.topic = topic_of(self.mac)
        self._lock = threading.Lock()
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        (self.events or self.runtime).subscribe(self._on_event)

    @staticmethod
    def will(topic: str) -> tuple[str, str]:
        """The retained last will a device's MQTT connection carries: Home Assistant marks it unavailable."""
        return f"tele/{topic}/LWT", "Offline"

    # -- life cycle -----------------------------------------------------------------------------
    def start(self) -> None:
        self._subscribe()
        self._announce()

    def stop(self) -> None:
        self._pub(f"tele/{self.topic}/LWT", "Offline", retain=True)

    def retract(self) -> None:
        """Remove the retained discovery, so Home Assistant forgets the device."""
        for kind in ("config", "sensors"):
            self.client.publish(f"{DISCOVERY_PREFIX}/{self.mac}/{kind}", "", qos=1, retain=True)

    def _subscribe(self) -> None:
        self.client.subscribe(f"cmnd/{self.topic}/#")

    def _on_connect(self, client, userdata, flags, reason, properties=None) -> None:
        """paho reconnects with a clean session: subscribe again and say online (the broker published the will)."""
        self._subscribe()
        self._pub(f"tele/{self.topic}/LWT", "Online", retain=True)

    def _announce(self) -> None:
        relays, pwm = self.binding["relays"], self.binding["pwm"]
        kinds = [2] if pwm else [1] * relays  # 2: a relay that drives a light
        cfg = {
            "ip": "127.0.0.1", "dn": self.friendly_name, "fn": [self.friendly_name] + [None] * 7, "hn": self.topic,
            "mac": self.mac, "md": self.runtime.twins[self.twin_id].vds["identity"]["model"], "ty": 0, "if": 0,
            "cam": 0, "ofln": "Offline", "onln": "Online", "state": ["OFF", "ON", "TOGGLE", "HOLD"],
            "sw": SOFTWARE, "t": self.topic, "ft": "%prefix%/%topic%/", "tp": ["cmnd", "stat", "tele"],
            "rl": (kinds + [0] * RELAYS_ANNOUNCED)[:RELAYS_ANNOUNCED], "swc": [-1] * 8, "swn": [None] * 8,
            "btn": [0] * 4, "so": dict(OPTIONS), "lk": 0, "lt_st": 1 if pwm else 0, "bat": 0, "dslp": 0,
            "sho": [], "sht": [], "ver": 1,
        }
        self._pub(f"{DISCOVERY_PREFIX}/{self.mac}/config", json.dumps(cfg), retain=True)
        self._pub(f"{DISCOVERY_PREFIX}/{self.mac}/sensors", json.dumps({"sn": {"Time": self._now()}, "ver": 1}),
                  retain=True)
        self._pub(f"tele/{self.topic}/LWT", "Online", retain=True)
        self._publish_state()

    # -- outgoing -------------------------------------------------------------------------------
    def _pub(self, topic: str, payload: str, retain: bool = False) -> None:
        self.client.publish(topic, payload, qos=1, retain=retain)

    def _now(self) -> str:
        return _time(self.runtime.clock)

    def _state(self) -> dict:
        s = self.runtime.state(self.twin_id)
        return {n: s[n] for n in sorted(self.features) if n in s}

    def _publish_state(self) -> None:
        body = {"Time": self._now(), "Uptime": "0T00:00:00", **self._state(),
                "Wifi": {"AP": 1, "SSId": "vesper", "RSSI": 80, "Signal": -60}}
        self._pub(f"tele/{self.topic}/STATE", json.dumps(body))

    def _on_event(self, event) -> None:
        if event.twin_id != self.twin_id:
            return
        if event.cause == "periodic":
            self._publish_state()  # TelePeriod
            return
        if getattr(self, "_quiet", False):
            return  # a command's own result is published once, after the command
        self._result({event.feature: event.value})

    def _result(self, values: dict) -> None:
        self._pub(f"stat/{self.topic}/RESULT", json.dumps(values))
        for name, value in values.items():
            if name.startswith("POWER"):
                self._pub(f"stat/{self.topic}/{name}", str(value))

    # -- what the relay asks --------------------------------------------------------------------
    def identify(self, parts: list[str], payload: bytes) -> tuple[str, tuple[str, ...]]:
        """The device's name and the features a message on its topics carries (`parts`: the topic's levels), so an
        effect finds the device's messages as it finds a Zigbee device's."""
        text, last = payload.decode("utf-8", "replace"), parts[-1] if len(parts) > 2 else ""
        if parts[0] == "cmnd":
            commands = text.split(";") if last.upper() == "BACKLOG" else [last]
            names = [self._named.get(c.strip().partition(" ")[0].upper()) for c in commands]
        else:
            try:
                body = json.loads(text)
            except ValueError:
                body = None
            keys = list(body) if isinstance(body, dict) else [last]
            names = [self._named.get(k.upper()) for k in keys]
        return self.friendly_name, tuple(dict.fromkeys(n for n in names if n))

    # -- incoming -------------------------------------------------------------------------------
    def _on_message(self, client, userdata, msg) -> None:
        command = msg.topic.rsplit("/", 1)[-1]
        raw = msg.payload.decode("utf-8", "replace").strip()
        try:
            self._command(command, raw)
        except Exception as e:  # an exception here would stop paho's network loop
            self.rejections.append({"command": command, "payload": raw[:100], "reason": f"internal error: {e!r}"})

    def _command(self, command: str, raw: str) -> None:
        if command.upper() == "STATE":
            self._publish_state()
            return
        if command.upper() == "BACKLOG":
            self._backlog(raw)
            return
        if command.upper() in TRANSITIONS:
            return
        if command.upper() == "STATUS":
            if raw == "11":
                self._pub(f"stat/{self.topic}/STATUS11", json.dumps({"StatusSTS": {"Time": self._now(),
                                                                                    **self._state()}}))
            elif raw == "10":
                self._pub(f"stat/{self.topic}/STATUS10", json.dumps({"StatusSNS": {"Time": self._now()}}))
            return
        name = self._named.get(command.upper())
        if name is not None and raw == "":
            self._result({name: self.runtime.state(self.twin_id)[name]})
            return
        changes = self._changes(command, raw)
        if isinstance(changes, str):
            self.rejections.append({"command": command, "payload": raw[:100], "reason": changes})
            return
        self._run(changes)

    def _backlog(self, raw: str) -> None:
        """`NoDelay;<command> <value>;...`: every command is checked first; one refused, none runs."""
        changes = []
        for part in raw.split(";"):
            command, _, value = part.strip().partition(" ")
            if not command or command.upper() in ("NODELAY", *TRANSITIONS):
                continue
            got = self._changes(command, value.strip())
            if isinstance(got, str):
                self.rejections.append({"command": "Backlog", "payload": raw[:100], "reason": f"{command}: {got}"})
                return
            changes += got
        if changes:
            self._run(changes)

    def _changes(self, command: str, raw: str) -> list[tuple[str, object]] | str:
        """The (feature, value) pairs a command sets, or why it is refused."""
        name = self._named.get(command.upper())
        if name is None:
            return "no such feature"
        if name == "Dimmer":
            try:
                level = int(float(raw))
            except ValueError:
                return "not a number"
            return [("Dimmer", max(0, min(100, level)))] + ([("POWER", "ON")] if level > 0 else [])
        value = raw.upper()
        if value == "TOGGLE":
            value = "OFF" if self.runtime.state(self.twin_id)[name] == "ON" else "ON"
        elif value in ("1", "ON", "TRUE"):
            value = "ON"
        elif value in ("0", "OFF", "FALSE"):
            value = "OFF"
        else:
            return "not ON, OFF or TOGGLE"
        return [(name, value)]

    def _run(self, changes: list[tuple[str, object]]) -> None:
        run = lambda: self._apply(changes)  # noqa: E731
        if self.send is not None:
            self.send(self.friendly_name, tuple(dict.fromkeys(n for n, _ in changes)), run)
        else:
            run()

    def _apply(self, changes: list[tuple[str, object]]) -> None:
        with self._lock:
            self._quiet = True
            try:
                done = {n: v for n, v in changes if self.runtime.platform_set(self.twin_id, n, v)}
            finally:
                self._quiet = False
        state = self.runtime.state(self.twin_id)
        order = sorted(done, key=lambda n: (n != "POWER" and not n.startswith("POWER"), n))
        if done:
            self._result({n: state[n] for n in order})
