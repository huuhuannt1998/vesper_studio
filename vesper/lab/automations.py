"""VESPER's built-in automation engine (spec §5b): a home's Home Assistant automations, run without Home Assistant.

Scripted sessions on the logical clock (`vesper lab day`) have no Home Assistant, so this engine runs the home's
`automations.yaml` instead, and the trace says so (the meta record's `automations`). It sees what Home Assistant
would see and acts as Home Assistant would:
- an entity's state comes from the device's *reports* (what reaches Home Assistant through the bridge), mapped
  as the bridge's MQTT discovery maps it (ON/OFF, locked/unlocked, a sensor's value);
- entity ids are the ones that discovery creates (`entity_ids`);
- a service call sends the command that entity's MQTT config sends (a light's JSON `state`, a switch's
  `payload_on`, a lock's `payload_lock`), applied as the bridge applies a command.
Supported: triggers `state` (to, from, for), `numeric_state` (above, below, for) and `time` (at); conditions
`state`, `numeric_state` and `time` (after, before); actions `light.*`, `switch.*` (turn_on, turn_off, toggle),
`lock.lock`/`lock.unlock` and `delay`; mode `single`. An automation using anything else is refused by name, never
run in part; the others run. Every command it sends is an `automation` record, not an input: a replay runs the
engine again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import yaml

from vesper.exposure.ha_discovery import device_discovery
from vesper.exposure.z2m_bridge import flatten, ieee_address

DAY_S = 86400
SERVICES = {"light": ("turn_on", "turn_off", "toggle"), "switch": ("turn_on", "turn_off", "toggle"),
            "lock": ("lock", "unlock")}


class AutomationError(ValueError):
    """An automation the engine cannot run; the message says what."""


@dataclass
class Entity:
    entity_id: str
    device: str
    twin_id: str
    feature: str
    platform: str
    cfg: dict


@dataclass
class Automation:
    name: str
    triggers: list
    conditions: list
    actions: list
    running: bool = field(default=False)


def _discovered(home):
    """(device name, twin id, discovery payload, expected entities) for each Zigbee device on the hub."""
    for d in home.devices:
        if "zigbee" not in d.record["binding"]:
            continue  # Matter devices are not on VESPER's hub yet (plan W2d)
        twin_id = f"{d.product}@{d.name}"
        _, payload, expected, _ = device_discovery(d.record, ieee=ieee_address(twin_id), friendly_name=d.name)
        yield d.name, twin_id, payload, expected


def entity_ids(home) -> dict[str, dict[str, str]]:
    """Device name -> feature -> the entity id Home Assistant creates for it (what automations name)."""
    return {name: {e.feature: e.entity_id for e in expected} for name, _, _, expected in _discovered(home)}


def _seconds(value, where: str) -> float:
    """A Home Assistant duration: 'HH:MM:SS', a mapping of hours/minutes/seconds, or a number of seconds."""
    if isinstance(value, bool):
        raise AutomationError(f"{where}: {value!r} is not a duration")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict):
        unknown = set(value) - {"hours", "minutes", "seconds", "milliseconds"}
        if unknown:
            raise AutomationError(f"{where}: {sorted(unknown)[0]!r} is not a duration field")
        return (float(value.get("hours", 0)) * 3600 + float(value.get("minutes", 0)) * 60
                + float(value.get("seconds", 0)) + float(value.get("milliseconds", 0)) / 1000)
    return _clock(value, where)


def _clock(value, where: str) -> float:
    try:
        parts = [float(p) for p in str(value).split(":")]
        if len(parts) not in (2, 3):
            raise ValueError
    except ValueError:
        raise AutomationError(f"{where}: {value!r} is not a time (HH:MM or HH:MM:SS)") from None
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0)


def _states(value) -> list[str]:
    """A state or a list of states as Home Assistant compares them (YAML's on/off booleans are 'on'/'off')."""
    values = value if isinstance(value, list) else [value]
    return [("on" if v else "off") if isinstance(v, bool) else str(v) for v in values]


def _entities(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


class Engine:
    def __init__(self, session):
        self.session, self.home, self.runtime = session, session.home, session.runtime
        self.entities: dict[str, Entity] = {}
        for name, twin_id, payload, expected in _discovered(self.home):
            for e in expected:
                oid = e.entity_id.split(".", 1)[1]
                self.entities[e.entity_id] = Entity(e.entity_id, name, twin_id, e.feature, e.platform,
                                                    payload["cmps"].get(oid, {}))
        self.automations: list[Automation] = []
        self.refused: list[dict] = []
        self._state: dict[str, str] = {}
        self._changed: dict[str, int] = {}  # entity -> how often its state changed: a `for:` waits on it
        self._load()

    # -- the file -----------------------------------------------------------------------------
    def _load(self) -> None:
        path = self.home.automations
        name = path.name if path else "automations.yaml"
        try:
            raw = yaml.safe_load(path.read_text()) if path else []
        except OSError as e:
            self.refused.append({"automation": name, "reason": f"{name}: cannot read it: {e.strerror}"})
            return
        except yaml.YAMLError as e:
            self.refused.append({"automation": name, "reason": f"{name}: not valid YAML: {e}"})
            return
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            self.refused.append({"automation": name, "reason": f"{name}: a list of automations"})
            return
        for k, a in enumerate(raw):
            label = (a.get("alias") or a.get("id") or f"automation {k + 1}") if isinstance(a, dict) else \
                f"automation {k + 1}"
            try:
                self.automations.append(self._parse(a, label))
            except AutomationError as e:
                self.refused.append({"automation": label, "reason": str(e)})

    def _parse(self, a, label: str) -> Automation:
        if not isinstance(a, dict):
            raise AutomationError("an automation is a mapping")
        mode = a.get("mode", "single")
        if mode != "single":
            raise AutomationError(f"mode {mode!r} is not supported (single only)")
        triggers = [self._trigger(t) for t in _list(a.get("triggers", a.get("trigger")), "triggers")]
        if not triggers:
            raise AutomationError("it has no trigger")
        conditions = [self._condition(c) for c in _list(a.get("conditions", a.get("condition")), "conditions")]
        actions = [self._action(x) for x in _list(a.get("actions", a.get("action")), "actions")]
        return Automation(label, triggers, conditions, actions)

    def _entity(self, entity_id: str) -> Entity:
        e = self.entities.get(entity_id)
        if e is None:
            raise AutomationError(f"entity {entity_id} is not one of this home's")
        return e

    def _trigger(self, t) -> dict:
        kind = t.get("trigger", t.get("platform")) if isinstance(t, dict) else None
        if kind == "state":
            ents = [self._entity(x).entity_id for x in _entities(t.get("entity_id"))]
            out = {"kind": "state", "entities": ents, "to": _states(t["to"]) if "to" in t else None,
                   "from": _states(t["from"]) if "from" in t else None}
        elif kind == "numeric_state":
            ents = [self._entity(x).entity_id for x in _entities(t.get("entity_id"))]
            out = {"kind": "numeric_state", "entities": ents, "above": t.get("above"), "below": t.get("below")}
        elif kind == "time":
            return {"kind": "time", "at": [_clock(x, "at") for x in (t["at"] if isinstance(t.get("at"), list)
                                                                       else [t.get("at")])]}
        else:
            raise AutomationError(f"trigger {kind!r} is not supported")
        out["for"] = _seconds(t["for"], "for") if "for" in t else 0.0
        return out

    def _condition(self, c) -> dict:
        kind = c.get("condition") if isinstance(c, dict) else None
        if kind == "state":
            return {"kind": "state", "entities": [self._entity(x).entity_id for x in _entities(c.get("entity_id"))],
                    "state": _states(c.get("state"))}
        if kind == "numeric_state":
            return {"kind": "numeric_state",
                    "entities": [self._entity(x).entity_id for x in _entities(c.get("entity_id"))],
                    "above": c.get("above"), "below": c.get("below")}
        if kind == "time":
            if "weekday" in c:
                raise AutomationError("condition time: weekday is not supported")
            return {"kind": "time", "after": _clock(c["after"], "after") if "after" in c else None,
                    "before": _clock(c["before"], "before") if "before" in c else None}
        raise AutomationError(f"condition {kind!r} is not supported")

    def _action(self, x) -> dict:
        if not isinstance(x, dict):
            raise AutomationError("an action is a mapping")
        if "delay" in x:
            return {"kind": "delay", "seconds": _seconds(x["delay"], "delay")}
        service = x.get("action", x.get("service"))
        domain, _, verb = str(service).partition(".")
        if verb not in SERVICES.get(domain, ()):
            raise AutomationError(f"action {service!r} is not supported")
        target = x.get("target") or {}
        ents = _entities(target.get("entity_id") or x.get("entity_id"))
        if not ents:
            raise AutomationError(f"action {service}: no target entity")
        for e in ents:
            if self._entity(e).platform != domain:
                raise AutomationError(f"action {service}: {e} is not a {domain}")
        data = dict(x.get("data") or {})
        unknown = set(data) - ({"brightness", "brightness_pct"} if service == "light.turn_on" else set())
        if unknown:
            raise AutomationError(f"action {service}: data {sorted(unknown)[0]!r} is not supported")
        brightness = data.get("brightness")
        if "brightness_pct" in data:  # the light service turns a percentage into 0..255
            brightness = round(float(data["brightness_pct"]) * 255 / 100)
        return {"kind": "call", "service": service, "entities": ents, "brightness": brightness}

    # -- running ------------------------------------------------------------------------------
    def start(self) -> None:
        clock = self.session.clock
        for e in self.entities.values():
            self._state[e.entity_id] = self._state_of(e, self.runtime.state(e.twin_id).get(e.feature))
        self.runtime.subscribe(self._on_report)
        self._midnight = clock.now() - self.home.start_time
        for day in (0, 1):
            self._schedule_day(day)
        clock.call_at(self._midnight + DAY_S, lambda: self._next_day(2))

    def _schedule_day(self, day: int) -> None:
        now = self.session.clock.now()
        for a in self.automations:
            for t in a.triggers:
                if t["kind"] == "time":
                    for at in t["at"]:
                        when = self._midnight + day * DAY_S + at
                        if when >= now:
                            self.session.clock.call_at(when, lambda a=a, at=at: self._fire(a, f"time {at:g}"))

    def _next_day(self, day: int) -> None:
        self._schedule_day(day)
        self.session.clock.call_at(self._midnight + day * DAY_S, lambda: self._next_day(day + 1))

    def _state_of(self, e: Entity, value) -> str:
        if value is None:
            return "unknown"
        f = self.runtime.twins[e.twin_id].features.get(e.feature, {})
        if e.platform in ("binary_sensor", "switch", "light"):
            return "on" if value == f.get("value_on", True) else "off"
        if e.platform == "lock":
            return "locked" if value == f.get("value_on") else "unlocked"
        return str(value)

    def _on_report(self, event) -> None:
        for e in self.entities.values():
            if e.twin_id != event.twin_id or e.feature != event.feature:
                continue
            old, new = self._state.get(e.entity_id), self._state_of(e, event.value)
            if new == old:
                continue
            self._state[e.entity_id] = new
            self._changed[e.entity_id] = self._changed.get(e.entity_id, 0) + 1
            for a in self.automations:
                for t in a.triggers:
                    if e.entity_id in t.get("entities", ()) and self._matches(t, old, new):
                        self._after(t, e.entity_id, a, f"{t['kind']} {e.entity_id} {old} -> {new}")

    def _matches(self, t: dict, old: str, new: str) -> bool:
        if t["kind"] == "state":
            return (t["to"] is None or new in t["to"]) and (t["from"] is None or old in t["from"])
        return self._in_range(t, new) and not self._in_range(t, old)  # numeric_state: entering the range

    @staticmethod
    def _in_range(t: dict, state: str) -> bool:
        try:
            v = float(state)
        except (TypeError, ValueError):
            return False
        return (t.get("above") is None or v > float(t["above"])) and (t.get("below") is None or v < float(t["below"]))

    def _after(self, t: dict, entity: str, a: Automation, why: str) -> None:
        if not t["for"]:
            self._fire(a, why)
            return
        clock, seen = self.session.clock, self._changed.get(entity, 0)

        def held():  # the state has held for the whole `for`: nothing changed it since
            if self._changed.get(entity, 0) == seen:
                self._fire(a, f"{why} for {t['for']:g} s")
        clock.call_at(clock.now() + t["for"], held)

    def _fire(self, a: Automation, why: str) -> None:
        if a.running:  # Home Assistant: "Already running" (mode single), and the trigger is dropped
            self.session.automation_skipped(a.name, why, "already running (mode single)")
            return
        if not all(self._holds(c) for c in a.conditions):
            return
        a.running = True
        self._run(a, list(a.actions), why)

    def _holds(self, c: dict) -> bool:
        if c["kind"] == "state":
            return all(self._state.get(e) in c["state"] for e in c["entities"])
        if c["kind"] == "numeric_state":
            return all(self._in_range(c, self._state.get(e, "")) for e in c["entities"])
        now = (self.session.clock.now() - self._midnight) % DAY_S
        after, before = c["after"], c["before"]
        if after is not None and before is not None and after > before:  # across midnight
            return now >= after or now < before
        return (after is None or now >= after) and (before is None or now < before)

    def _run(self, a: Automation, actions: list, why: str) -> None:
        while actions:
            x = actions.pop(0)
            if x["kind"] == "delay":
                clock = self.session.clock
                clock.call_at(clock.now() + x["seconds"], lambda: self._run(a, actions, why))
                return
            for entity_id in x["entities"]:
                self._call(a, x, self.entities[entity_id], why)
        a.running = False

    def _call(self, a: Automation, x: dict, e: Entity, why: str) -> None:
        domain, verb = x["service"].split(".")
        if verb == "toggle":
            verb = "turn_off" if self._state.get(e.entity_id) == "on" else "turn_on"
        if domain == "light":  # the JSON schema light's command (HA mqtt/light/schema_json.py)
            command = {"state": "ON" if verb == "turn_on" else "OFF"}
            brightness = x["brightness"]
            if verb == "turn_on" and brightness is not None and e.cfg.get("brightness"):
                scale = e.cfg.get("brightness_scale", 255)
                command["brightness"] = max(min(round(brightness / 255 * scale), scale), 1)
        elif domain == "switch":
            command = json.loads(e.cfg["payload_on" if verb == "turn_on" else "payload_off"])
        else:
            command = json.loads(e.cfg["payload_lock" if verb == "lock" else "payload_unlock"])
        for feature, value in flatten(command):
            self.session.automation_command(a.name, why, x["service"], e.entity_id, e.twin_id, feature, value)


def _list(value, where: str) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise AutomationError(f"{where}: a list")
    return value
