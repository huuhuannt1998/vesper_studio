"""VESPER's built-in automation engine (spec §5b): a home's Home Assistant automations, run without Home Assistant.

Scripted sessions on the logical clock (`vesper lab day`) have no Home Assistant, so this engine runs the home's
`automations.yaml` instead, and the trace says so (the meta record's `automations`). It sees what Home Assistant
would see and acts as Home Assistant would:
- an entity's state comes from the device's *reports* (what reaches Home Assistant through the bridge), mapped
  as the bridge's MQTT discovery maps it (ON/OFF, locked/unlocked, a sensor's value);
- entity ids are the ones that discovery creates (`entity_ids`);
- a service call sends the command that entity's MQTT config sends (a light's JSON `state`, a switch's
  `payload_on`, a lock's `payload_lock`), applied as the bridge applies a command.
- an event entity's state (a button's `action`) is the time of its last event, so every press is a change.
Supported: triggers `state` (to, from, for; `to: ~` is any change), `numeric_state` (above, below, for) and `time`
(at); conditions `state`, `numeric_state` and `time` (after, before); actions `light.*` (brightness,
brightness_pct), `switch.*` (turn_on, turn_off, toggle), `lock.lock`/`lock.unlock` and `delay`; `enabled: false`
anywhere; mode `single`. An automation using anything else (a template, an option, a key the engine does not
know, an unquoted on/off that Home Assistant itself refuses) is refused by name when the file is loaded, never run
in part; the others run.
As in Home Assistant, every trigger of a state change is evaluated before any action runs, a `for:` lasts while
the state still matches (a numeric value, while it stays in range), and conditions are checked before mode
`single` drops a trigger. Every command the engine sends is an `automation` record, not an input: a replay runs
the engine again.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

import yaml

from vesper.exposure.ha_discovery import device_discovery
from vesper.exposure.z2m_bridge import flatten, ieee_address

DAY_S = 86400
LOOP_LIMIT = 100  # firings of one automation at one instant: more is a loop (it triggers itself), not a day
SERVICES = {"light": ("turn_on", "turn_off", "toggle"), "switch": ("turn_on", "turn_off", "toggle"),
            "lock": ("lock", "unlock")}
AUTOMATION_KEYS = {"id", "alias", "description", "mode", "max_exceeded", "initial_state", "trace",
                   "triggers", "trigger", "conditions", "condition", "actions", "action"}
TRIGGER_KEYS = {"state": {"entity_id", "to", "from", "for"}, "numeric_state": {"entity_id", "above", "below", "for"},
                "time": {"at"}}
CONDITION_KEYS = {"state": {"entity_id", "state"}, "numeric_state": {"entity_id", "above", "below"},
                  "time": {"after", "before"}}
ACTION_KEYS = {"action", "service", "target", "entity_id", "data", "alias", "continue_on_error"}


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


@dataclass
class _Held:
    """A `for:` under way: it lasts while `still(state)` holds for the entity's new states."""
    still: Callable[[str], bool]
    alive: bool = True


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


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AutomationError(f"{where}: {value!r} is not a number")
    return float(value)


def _seconds(value, where: str) -> float:
    """A Home Assistant duration: 'HH:MM:SS', a mapping of hours/minutes/seconds, or a number of seconds."""
    if isinstance(value, bool):
        raise AutomationError(f"{where}: {value!r} is not a duration")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict):
        fields = {"hours": 3600, "minutes": 60, "seconds": 1, "milliseconds": 0.001}
        if set(value) - set(fields) or not all(isinstance(v, int | float) and not isinstance(v, bool)
                                              for v in value.values()):
            raise AutomationError(f"{where}: {value!r} is not a duration")
        return sum(float(v) * fields[k] for k, v in value.items())
    return _clock(value, where)


def _clock(value, where: str) -> float:
    try:
        if not isinstance(value, str):
            raise ValueError
        parts = [float(p) for p in value.split(":")]
        if len(parts) not in (2, 3):
            raise ValueError
    except ValueError:
        raise AutomationError(f"{where}: {value!r} is not a time (HH:MM or HH:MM:SS)") from None
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0)


def _states(value, where: str) -> list[str]:
    """A state or a list of states. Home Assistant's schema takes strings only, on purpose: an unquoted on/off
    is a YAML boolean, and Home Assistant refuses the automation."""
    values = value if isinstance(value, list) else [value]
    for v in values:
        if not isinstance(v, str):
            raise AutomationError(f"{where}: {v!r} is not a quoted state; Home Assistant refuses YAML's unquoted "
                                  "on/off")
    return values


def _entities(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


def _keys(item: dict, allowed: set, where: str = "") -> None:
    unknown = sorted(set(item) - allowed - {"enabled"}, key=str)
    if unknown:
        raise AutomationError(f"{where + ': ' if where else ''}{unknown[0]!r} is not supported")
    if not isinstance(item.get("enabled", True), bool):
        raise AutomationError(f"{where}: enabled: {item['enabled']!r} is not true or false")


def _enabled(items: list) -> list:
    return [x for x in items if not (isinstance(x, dict) and x.get("enabled") is False)]


class Engine:
    def __init__(self, session):
        self.session, self.home, self.runtime = session, session.home, session.runtime
        self.entities: dict[str, Entity] = {}
        for name, twin_id, payload, expected in _discovered(self.home):
            for e in expected:
                oid = e.entity_id.split(".", 1)[1]
                self.entities[e.entity_id] = Entity(e.entity_id, name, twin_id, e.feature, e.platform,
                                                    payload["cmps"].get(oid, {}))
        self._of_feature: dict[tuple[str, str], list[Entity]] = {}
        for e in self.entities.values():
            self._of_feature.setdefault((e.twin_id, e.feature), []).append(e)
        self.automations: list[Automation] = []
        self.refused: list[dict] = []
        self._state: dict[str, str] = {}
        self._held: dict[str, list[_Held]] = {}  # entity -> the `for:` timers waiting on it
        self._burst: tuple[float, dict] = (-1.0, {})  # (instant, automation -> firings at it): the loop guard
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
        if "use_blueprint" in a:
            raise AutomationError("blueprint automations are not supported")
        _keys(a, AUTOMATION_KEYS)
        mode = a.get("mode", "single")
        if mode != "single":
            raise AutomationError(f"mode {mode!r} is not supported (single only)")
        triggers = [self._trigger(t) for t in _enabled(_list(a.get("triggers", a.get("trigger")), "triggers"))]
        if not triggers and not _list(a.get("triggers", a.get("trigger")), "triggers"):
            raise AutomationError("it has no trigger")
        conditions = [self._condition(c)
                      for c in _enabled(_list(a.get("conditions", a.get("condition")), "conditions"))]
        actions = [self._action(x) for x in _enabled(_list(a.get("actions", a.get("action")), "actions"))]
        return Automation(label, triggers, conditions, actions)

    def _entity(self, entity_id) -> Entity:
        e = self.entities.get(entity_id) if isinstance(entity_id, str) else None
        if e is None:
            raise AutomationError(f"entity {entity_id} is not one of this home's")
        return e

    def _trigger(self, t) -> dict:
        kind = t.get("trigger", t.get("platform")) if isinstance(t, dict) else None
        if kind not in TRIGGER_KEYS:
            raise AutomationError(f"trigger {kind!r} is not supported")
        where = f"trigger {kind}"
        _keys(t, TRIGGER_KEYS[kind] | {"trigger", "platform", "id"}, where)
        if kind == "time":
            at = t.get("at")
            return {"kind": "time", "at": [_clock(x, f"{where}: at") for x in (at if isinstance(at, list) else [at])]}
        if "entity_id" not in t:
            raise AutomationError(f"{where}: entity_id is required")
        out = {"kind": kind, "entities": [self._entity(x).entity_id for x in _entities(t["entity_id"])],
               "for": _seconds(t["for"], "for") if "for" in t else 0.0}
        if kind == "state":
            out["to"] = _states(t["to"], f"{where}: to") if t.get("to") is not None else None
            out["from"] = _states(t["from"], f"{where}: from") if t.get("from") is not None else None
            out["from_only"] = "from" in t and "to" not in t  # a `for:` then lasts while the state is not `from`
        else:
            out["above"] = _number(t["above"], f"{where}: above") if "above" in t else None
            out["below"] = _number(t["below"], f"{where}: below") if "below" in t else None
            if out["above"] is None and out["below"] is None:
                raise AutomationError(f"{where}: above or below is required")
        return out

    def _condition(self, c) -> dict:
        kind = c.get("condition") if isinstance(c, dict) else None
        if kind not in CONDITION_KEYS:
            raise AutomationError(f"condition {kind!r} is not supported")
        where = f"condition {kind}"
        _keys(c, CONDITION_KEYS[kind] | {"condition", "alias"}, where)
        if kind == "time":
            return {"kind": "time", "after": _clock(c["after"], f"{where}: after") if "after" in c else None,
                    "before": _clock(c["before"], f"{where}: before") if "before" in c else None}
        if "entity_id" not in c:
            raise AutomationError(f"{where}: entity_id is required")
        out = {"kind": kind, "entities": [self._entity(x).entity_id for x in _entities(c["entity_id"])]}
        if kind == "state":
            out["state"] = _states(c.get("state"), f"{where}: state")
        else:
            out["above"] = _number(c["above"], f"{where}: above") if "above" in c else None
            out["below"] = _number(c["below"], f"{where}: below") if "below" in c else None
        return out

    def _action(self, x) -> dict:
        if not isinstance(x, dict):
            raise AutomationError("an action is a mapping")
        if "delay" in x:
            _keys(x, {"delay", "alias"}, "action delay")
            return {"kind": "delay", "seconds": _seconds(x["delay"], "delay")}
        service = x.get("action", x.get("service"))
        domain, _, verb = str(service).partition(".")
        if verb not in SERVICES.get(domain, ()):
            raise AutomationError(f"action {service!r} is not supported")
        where = f"action {service}"
        _keys(x, ACTION_KEYS, where)
        target = x.get("target") or {}
        if not isinstance(target, dict):
            raise AutomationError(f"{where}: target {target!r} is not a mapping")
        if set(target) - {"entity_id"}:  # an area or a device: Home Assistant's registries, not the engine's
            raise AutomationError(f"{where}: target {sorted(set(target) - {'entity_id'})[0]!r} is not supported")
        ents = _entities(target.get("entity_id") or x.get("entity_id"))
        if not ents:
            raise AutomationError(f"{where}: no target entity")
        for e in ents:
            if self._entity(e).platform != domain:
                raise AutomationError(f"{where}: {e} is not a {domain}")
        data = x.get("data") or {}
        if not isinstance(data, dict):
            raise AutomationError(f"{where}: data {data!r} is not a mapping")
        allowed = {"brightness": 255, "brightness_pct": 100} if service == "light.turn_on" else {}
        brightness = None
        for key, value in data.items():
            if key not in allowed:
                raise AutomationError(f"{where}: data {key!r} is not supported")
            v = _number(value, f"{where}: data {key}")
            if not 0 <= v <= allowed[key]:
                raise AutomationError(f"{where}: data {key}: {value!r} is not between 0 and {allowed[key]}")
            brightness = round(v * 255 / 100) if key == "brightness_pct" else v  # the light service's 0..255
        return {"kind": "call", "service": service, "entities": ents, "brightness": brightness}

    # -- running ------------------------------------------------------------------------------
    def start(self) -> None:
        clock = self.session.clock
        for e in self.entities.values():
            value = self.runtime.state(e.twin_id).get(e.feature)
            self._state[e.entity_id] = "unknown" if e.platform == "event" else self._state_of(e, value)
        self.session.subscribe_reports(self._on_report)  # as Home Assistant hears them: across the links
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
        """A state change, as Home Assistant handles one: the `for:` timers it ends end, then every trigger it
        matches is evaluated, and only then do actions run (later, at the same instant)."""
        changes = []
        for e in self._of_feature.get((event.twin_id, event.feature), ()):
            old = self._state.get(e.entity_id)
            if e.platform == "event":  # the time of the last event: every event is a change
                new = f"{event.observed_t:.6f}"
            else:
                new = self._state_of(e, event.value)
                if new == old:
                    continue
            self._state[e.entity_id] = new
            changes.append((e.entity_id, old, new))
        for entity, _, new in changes:
            for h in self._held.get(entity, ()):
                h.alive = h.alive and h.still(new)
            self._held[entity] = [h for h in self._held.get(entity, ()) if h.alive]
        for entity, old, new in changes:
            for a in self.automations:
                for t in a.triggers:
                    if entity in t.get("entities", ()) and self._matches(t, old, new):
                        self._triggered(a, t, entity, old, new)

    def _matches(self, t: dict, old: str, new: str) -> bool:
        if t["kind"] == "state":
            return (t["to"] is None or new in t["to"]) and (t["from"] is None or old in t["from"])
        return self._in_range(t, new) and not self._in_range(t, old)  # numeric_state: entering the range

    @staticmethod
    def _in_range(t: dict, state) -> bool:
        try:
            v = float(state)
        except (TypeError, ValueError):
            return False
        return (t.get("above") is None or v > t["above"]) and (t.get("below") is None or v < t["below"])

    def _triggered(self, a: Automation, t: dict, entity: str, old: str, new: str) -> None:
        why, clock = f"{t['kind']} {entity} {old} -> {new}", self.session.clock
        if not t["for"]:
            clock.call_at(clock.now(), lambda: self._fire(a, why))
            return
        if t["kind"] == "numeric_state":
            still = lambda s: self._in_range(t, s)  # noqa: E731 - Home Assistant: while the value stays in range
        elif t["from_only"]:
            still = lambda s: s != old  # noqa: E731
        else:
            still = lambda s: s == new  # noqa: E731
        h = _Held(still)
        self._held.setdefault(entity, []).append(h)

        def held():
            if h.alive:
                h.alive = False
                self._held[entity] = [x for x in self._held.get(entity, ()) if x is not h]
                self._fire(a, f"{why} for {t['for']:g} s")
        clock.call_at(clock.now() + t["for"], held)

    def _fire(self, a: Automation, why: str) -> None:
        if not all(self._holds(c) for c in a.conditions):
            return
        if a.running:  # Home Assistant: "Already running" (mode single), and the trigger is dropped
            self.session.automation_skipped(a.name, why, "already running (mode single)")
            return
        now = self.session.clock.now()
        if self._burst[0] != now:
            self._burst = (now, {})
        count = self._burst[1][a.name] = self._burst[1].get(a.name, 0) + 1
        if count > LOOP_LIMIT:
            if count == LOOP_LIMIT + 1:
                self.session.automation_skipped(a.name, why, f"it fired {LOOP_LIMIT} times at this instant: a loop")
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
        if after is not None and before is not None and after >= before:  # across midnight; equal: all day
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
        brightness = x.get("brightness")
        if domain == "light" and verb == "turn_on" and brightness == 0:
            verb = "turn_off"  # Home Assistant: turning a light on at brightness 0 turns it off
        if domain == "light":  # the JSON schema light's command (HA mqtt/light/schema_json.py)
            command = {"state": "ON" if verb == "turn_on" else "OFF"}
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
