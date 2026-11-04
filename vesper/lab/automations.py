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
(at); conditions `state`, `numeric_state` and `time` (after, before); for a home with a site (vesper.env), trigger
`sun` (event sunrise or sunset, offset), condition `sun` (after and before sunrise or sunset, with offsets; after
and before across midnight is either), and the entity `sun.sun` (above_horizon, below_horizon), all from the same
astral sun as Home Assistant's at the site and the run's date; actions `light.*` (brightness,
brightness_pct), `switch.*` (turn_on, turn_off, toggle), `lock.lock`/`lock.unlock`, `climate.set_temperature`
(temperature and hvac_mode; no target temperature range, the discovered entity has none),
`climate.set_hvac_mode`, `climate.turn_on`/`climate.turn_off` on the home's thermostats (the climate entity of
Home Assistant's MQTT discovery: the setpoint and `system_mode` it exposes, within its range and its modes) and
`delay`; `enabled: false`
anywhere; mode `single`. An automation using anything else (a template, an option, a key the engine does not
know, an unquoted on/off that Home Assistant itself refuses) is refused by name when the file is loaded, never run
in part; the others run.
`Engine(permissive=True)` is for replaying public automations: an action it cannot execute is recorded (the service
and its targets, which need not be the home's) and a mode other than single runs as single, both said in the
automation's `approximations`; a condition or trigger it cannot evaluate is still refused.
As in Home Assistant, every trigger of a state change is evaluated before any action runs, a `for:` lasts while
the state still matches (a numeric value, while it stays in range), and conditions are checked before mode
`single` drops a trigger. Every command the engine sends is an `automation` record, not an input: a replay runs
the engine again.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import yaml

from vesper.exposure.ha_discovery import device_discovery
from vesper.exposure.tasmota import expected_entities
from vesper.exposure.z2m_bridge import flatten, ieee_address

DAY_S = 86400
LOOP_LIMIT = 100  # firings of one automation at one instant: more is a loop (it triggers itself), not a day
SERVICES = {"light": ("turn_on", "turn_off", "toggle"), "switch": ("turn_on", "turn_off", "toggle"),
            "lock": ("lock", "unlock"), "climate": ("set_temperature", "set_hvac_mode", "turn_on", "turn_off")}
TURN_ON_MODES = ("heat_cool", "heat", "cool")  # Home Assistant's climate.turn_on: the first of these the entity has
SET_TEMPERATURE_KEYS = {"temperature", "target_temp_low", "target_temp_high", "hvac_mode"}
AUTOMATION_KEYS = {"id", "alias", "description", "mode", "max", "max_exceeded", "initial_state", "trace", "hide_entity",
                   "triggers", "trigger", "conditions", "condition", "actions", "action"}
TRIGGER_KEYS = {"state": {"entity_id", "to", "from", "for"}, "numeric_state": {"entity_id", "above", "below", "for"},
                "time": {"at"}, "sun": {"event", "offset"}}
CONDITION_KEYS = {"state": {"entity_id", "state"}, "numeric_state": {"entity_id", "above", "below"},
                  "time": {"after", "before"}, "sun": {"after", "before", "after_offset", "before_offset"}}
SUN_EVENTS = ("sunrise", "sunset")
SERVICE_NAME = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
ACTION_KEYS = {"action", "service", "target", "entity_id", "data", "data_template", "metadata", "alias",
               "continue_on_error"}
# Home Assistant's light.turn_on (and toggle) data; light.turn_off takes transition and flash
LIGHT_ON_KEYS = {"transition", "brightness", "brightness_pct", "brightness_step", "brightness_step_pct", "color_name",
                 "color_temp", "color_temp_kelvin", "kelvin", "hs_color", "rgb_color", "rgbw_color", "rgbww_color",
                 "xy_color", "white", "profile", "flash", "effect"}
LIGHT_OFF_KEYS = {"transition", "flash"}
COLOUR_KEYS = {"color_name", "hs_color", "rgb_color", "rgbw_color", "rgbww_color", "xy_color"}
TEMPERATURE_KEYS = {"color_temp", "color_temp_kelvin", "kelvin"}
COLOUR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
UNMODELLED = {"transition": "the twin has no fade", "flash": "the twin does not flash",
              "effect": "the twin has no effects", "profile": "Home Assistant's light profiles are not modelled",
              "brightness_step": "a relative brightness is not modelled",
              "brightness_step_pct": "a relative brightness is not modelled"}
HOMEASSISTANT_VERBS = ("turn_on", "turn_off", "toggle")  # homeassistant.<verb>: each entity's own <domain>.<verb>


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
    approximations: list[str] = field(default_factory=list)  # what permissive mode ran differently from the file


@dataclass
class _Held:
    """A `for:` under way: it lasts while `still(state)` holds for the entity's new states."""
    still: Callable[[str], bool]
    alive: bool = True


def _discovered(home):
    """(device name, twin id, discovery payload, expected entities) for each Zigbee device on the hub."""
    for d in home.devices:
        if "zigbee" not in d.record["binding"]:
            continue  # Matter devices: `_matter`
        twin_id = f"{d.product}@{d.name}"
        _, payload, expected, _ = device_discovery(d.record, ieee=ieee_address(twin_id), friendly_name=d.name)
        yield d.name, twin_id, payload, expected


def _wifi(home):
    """(device name, twin id, expected entities) for each Wi-Fi device on the hub: a Tasmota device (plan W7a2)."""
    for d in home.devices:
        if "tasmota" in d.record["binding"]:
            yield d.name, f"{d.product}@{d.name}", expected_entities(d.record, d.name)


# A Matter device's entities, as Home Assistant's Matter integration names them from the node's label (the device
# name, plan W7a3): `<platform>.<name>` for a one-per-device platform, `<platform>.<name>_<device class>` for a
# sensor (Experiment 6's entity ids, 2026-10-26). The feature each one's state follows.
MATTER_PRIMARY = {"light": "On/Off.OnOff", "switch": "On/Off.OnOff", "lock": "Door Lock.LockState",
                  "binary_sensor/door": "Boolean State.StateValue",
                  "binary_sensor/occupancy": "Occupancy Sensing.Occupancy"}


def matter_entities(record: dict, name: str) -> list[tuple[str, tuple, str, str]]:
    """[(entity id, every feature behind it, platform, the feature its state follows)] of a Matter device named
    `name`, the entities VESPER's engine runs: lights, switches, locks, door and occupancy sensors. The name is its
    own slug and at most 32 characters (checked when the home loads), so the ids are Home Assistant's exactly."""
    from vesper.validation.matter_acceptance import expected_entities as expected
    out = []
    for e in expected(record):
        primary = MATTER_PRIMARY.get(e.platform if e.device_class is None else f"{e.platform}/{e.device_class}")
        if primary is None or primary not in e.features:
            continue
        suffix = "" if e.device_class is None else f"_{e.device_class}"
        out.append((f"{e.platform}.{name}{suffix}", e.features, e.platform, primary))
    return out


def _matter(home):
    """(device name, twin id, its entities, `matter_entities`) for each Matter device."""
    for d in home.devices:
        if "matter" in d.record["binding"]:
            yield d.name, f"{d.product}@{d.name}", matter_entities(d.record, d.name)


def entity_ids(home) -> dict[str, dict[str, str]]:
    """Device name -> feature -> the entity id Home Assistant creates for it (what automations name)."""
    ids = {name: {e.feature: e.entity_id for e in expected} for name, _, _, expected in _discovered(home)}
    ids.update({name: {e.feature: e.entity_id for e in expected} for name, _, expected in _wifi(home)})
    for name, _, entities in _matter(home):
        ids[name] = {f: entity_id for entity_id, features, _, _ in entities for f in features}
    return ids


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AutomationError(f"{where}: {value!r} is not a number")
    return float(value)


def _coerced(value, where: str) -> float:
    """A number as Home Assistant's vol.Coerce(float) takes it: a number or a numeric string (a YAML boolean is
    refused here, though Coerce would take it as 0 or 1)."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    return _number(value, where)


def _seconds(value, where: str) -> float:
    """A Home Assistant duration (cv.time_period): 'HH:MM:SS', a mapping of days/hours/minutes/seconds, or a number
    of seconds (a numeric string too)."""
    if isinstance(value, bool):
        raise AutomationError(f"{where}: {value!r} is not a duration")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and ":" not in value:
        try:
            return float(value)
        except ValueError:
            raise AutomationError(f"{where}: {value!r} is not a duration") from None
    if isinstance(value, dict):
        fields = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1, "milliseconds": 0.001}
        if set(value) - set(fields) or not all(isinstance(v, int | float) and not isinstance(v, bool)
                                              for v in value.values()):
            raise AutomationError(f"{where}: {value!r} is not a duration")
        return sum(float(v) * fields[k] for k, v in value.items())
    return _clock(value, where)


def _offset(value, where: str) -> float:
    """A sun offset: a duration that may be negative ('-00:30:00', -1800)."""
    if isinstance(value, str) and value.startswith("-") and ":" in value:
        return -_seconds(value[1:], where)
    return _seconds(value, where)


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
    def __init__(self, session, permissive: bool = False, automations: list[dict] | None = None):
        """`permissive`: an action the engine cannot execute is recorded (`automation_command` with no twin) rather
        than refusing the automation, and a mode other than single runs as single, listed in the automation's
        `approximations`. `automations`: the automations to load, in place of the home's file."""
        self.session, self.home, self.runtime = session, session.home, session.runtime
        self.permissive, self._given = permissive, automations
        self.entities: dict[str, Entity] = {}
        for name, twin_id, payload, expected in _discovered(self.home):
            for e in expected:
                oid = e.entity_id.split(".", 1)[1]
                self.entities[e.entity_id] = Entity(e.entity_id, name, twin_id, e.feature, e.platform,
                                                    payload["cmps"].get(oid, {}))
        for name, twin_id, expected in _wifi(self.home):
            for e in expected:
                self.entities[e.entity_id] = Entity(e.entity_id, name, twin_id, e.feature, e.platform,
                                                    {"tasmota": True})
        for name, twin_id, entities in _matter(self.home):
            for entity_id, _, platform, primary in entities:
                self.entities[entity_id] = Entity(entity_id, name, twin_id, primary, platform, {"matter": True})
        self.site = getattr(self.home, "site", None)
        if self.site is not None:  # Home Assistant's sun, at the home's site and date
            self.entities["sun.sun"] = Entity("sun.sun", "sun", "", "", "sun", {})
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
            raw = self._given if self._given is not None else yaml.safe_load(path.read_text()) if path else []
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
        if not isinstance(a.get("hide_entity", False), bool):  # deprecated in Home Assistant, still a boolean
            raise AutomationError(f"hide_entity: {a['hide_entity']!r} is not true or false")
        mode, approximations = a.get("mode", "single"), []
        if mode != "single":
            if not self.permissive:
                raise AutomationError(f"mode {mode!r} is not supported (single only)")
            approximations.append(f"mode {mode} run as single")
        self._notes = []  # what permissive mode records instead of running, for `approximations`
        triggers = [self._trigger(t) for t in _enabled(_list(a.get("triggers", a.get("trigger")), "triggers"))]
        if not triggers and not _list(a.get("triggers", a.get("trigger")), "triggers"):
            raise AutomationError("it has no trigger")
        conditions = [self._condition(c)
                      for c in _enabled(_list(a.get("conditions", a.get("condition")), "conditions"))]
        actions = [self._action(x) for x in _enabled(_list(a.get("actions", a.get("action")), "actions"))]
        approximations += self._notes
        return Automation(label, triggers, conditions, actions, approximations=approximations)

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
        if kind == "sun":
            self._needs_site(where)
            if t.get("event") not in SUN_EVENTS:
                raise AutomationError(f"{where}: event {t.get('event')!r} is not sunrise or sunset")
            return {"kind": "sun", "event": t["event"],
                    "offset": _offset(t["offset"], f"{where}: offset") if "offset" in t else 0.0}
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
            out["above"] = _coerced(t["above"], f"{where}: above") if "above" in t else None
            out["below"] = _coerced(t["below"], f"{where}: below") if "below" in t else None
            if out["above"] is None and out["below"] is None:
                raise AutomationError(f"{where}: above or below is required")
        return out

    def _condition(self, c) -> dict:
        kind = c.get("condition") if isinstance(c, dict) else None
        if kind not in CONDITION_KEYS:
            raise AutomationError(f"condition {kind!r} is not supported")
        where = f"condition {kind}"
        _keys(c, CONDITION_KEYS[kind] | {"condition", "alias"}, where)
        if kind == "sun":
            self._needs_site(where)
            for k in ("after", "before"):
                if k in c and c[k] not in SUN_EVENTS:
                    raise AutomationError(f"{where}: {k} {c[k]!r} is not sunrise or sunset")
            if "after" not in c and "before" not in c:
                raise AutomationError(f"{where}: after or before is required")
            offset = {k: _offset(c[k], f"{where}: {k}") if k in c else 0.0 for k in ("after_offset", "before_offset")}
            return {"kind": "sun", "after": c.get("after"), "before": c.get("before"), **offset}
        if kind == "time":
            return {"kind": "time", "after": _clock(c["after"], f"{where}: after") if "after" in c else None,
                    "before": _clock(c["before"], f"{where}: before") if "before" in c else None}
        if "entity_id" not in c:
            raise AutomationError(f"{where}: entity_id is required")
        out = {"kind": kind, "entities": [self._entity(x).entity_id for x in _entities(c["entity_id"])]}
        if kind == "state":
            out["state"] = _states(c.get("state"), f"{where}: state")
        else:
            out["above"] = _coerced(c["above"], f"{where}: above") if "above" in c else None
            out["below"] = _coerced(c["below"], f"{where}: below") if "below" in c else None
        return out

    def _needs_site(self, where: str) -> None:
        if self.site is None:
            raise AutomationError(f"{where}: the home has no site, so it has no sun (add site: to home.yaml)")

    def _sun_events(self, day: int) -> dict:
        from vesper.env.sun import sun_events
        return sun_events(self.site, day)

    def _action(self, x) -> dict:
        if not isinstance(x, dict):
            raise AutomationError("an action is a mapping")
        if "delay" in x:
            _keys(x, {"delay", "alias"}, "action delay")
            return {"kind": "delay", "seconds": _seconds(x["delay"], "delay")}
        service = x.get("action", x.get("service"))
        domain, _, verb = str(service).partition(".")
        if domain == "homeassistant" and verb in HOMEASSISTANT_VERBS:
            return self._homeassistant(x, service, verb)
        if domain == "climate" and verb in SERVICES["climate"]:
            try:
                return self._climate(x, service, verb)
            except AutomationError as e:
                if not self.permissive:
                    raise
                self._notes.append(f"{service} recorded, not run: {e}")
                return self._record(x, service)
        if verb not in SERVICES.get(domain, ()):
            if self.permissive and isinstance(service, str) and SERVICE_NAME.fullmatch(service):
                return self._record(x, service)
            raise AutomationError(f"action {service!r} is not supported")
        where = f"action {service}"
        ents, data = self._call_parts(x, where, domain)
        for e in ents:
            if self._entity(e).platform != domain:
                raise AutomationError(f"{where}: {e} is not a {domain}")
        return {"kind": "call", "service": service, "entities": ents,
                "brightness": self._light_data(service, ents, data, where) if domain == "light" else
                self._no_data(data, where)}

    def _call_parts(self, x: dict, where: str, domain: str) -> tuple[list[str], dict]:
        """(target entity ids, data) of a service call, as Home Assistant's script assembles them: `target`, a
        top-level `entity_id` (which wins), else the legacy `entity_id` in `data`/`data_template` (merged in that
        order); `entity_id: all` is every entity of the service's domain, `none` is none."""
        _keys(x, ACTION_KEYS, where)
        target = x.get("target") or {}
        if not isinstance(target, dict):
            raise AutomationError(f"{where}: target {target!r} is not a mapping")
        if set(target) - {"entity_id"}:  # an area or a device: Home Assistant's registries, not the engine's
            raise AutomationError(f"{where}: target {sorted(set(target) - {'entity_id'})[0]!r} is not supported")
        data = {}
        for key in ("data", "data_template"):
            part = x.get(key) or {}
            if not isinstance(part, dict):
                raise AutomationError(f"{where}: {key} {part!r} is not a mapping")
            data.update(part)
        named = x["entity_id"] if "entity_id" in x else target.get("entity_id", data.get("entity_id"))
        data.pop("entity_id", None)
        if named in ("all", "none"):
            ents = [e.entity_id for e in self.entities.values() if e.platform == domain] if named == "all" else []
        else:
            ents = _entities(named)
        if not ents and named != "none":
            raise AutomationError(f"{where}: no target entity")
        return ents, data

    @staticmethod
    def _no_data(data: dict, where: str) -> None:
        if data:
            raise AutomationError(f"{where}: data {sorted(data, key=str)[0]!r} is not supported")

    def _light_data(self, service: str, ents: list[str], data: dict, where: str) -> float | None:
        """The brightness (0..255) a light action sets, from Home Assistant's light service data. As Home Assistant's
        filter_turn_on_params does, a colour or colour temperature none of the lights has a mode for is dropped; a
        parameter the twin cannot apply (a transition, a flash, an effect the light has) is refused, or in permissive
        mode dropped and noted."""
        verb = service.split(".")[1]
        allowed = LIGHT_OFF_KEYS if verb == "turn_off" else LIGHT_ON_KEYS
        modes = set().union(*(self._colour_modes(self._entity(e)) for e in ents)) if ents else set()
        brightness = None
        for key, value in data.items():
            if key not in allowed:
                raise AutomationError(f"{where}: data {key!r} is not supported")
            if key in ("brightness", "brightness_pct"):
                top = 255 if key == "brightness" else 100
                v = _number(value, f"{where}: data {key}")
                if not 0 <= v <= top:
                    raise AutomationError(f"{where}: data {key}: {value!r} is not between 0 and {top}")
                brightness = round(v * 255 / 100) if key == "brightness_pct" else v  # the light service's 0..255
                continue
            if key in COLOUR_KEYS or key in TEMPERATURE_KEYS or key == "white":
                # a light with a colour mode converts between colour forms, and from a colour temperature
                capable = (modes & COLOUR_MODES or ("color_temp" in modes and key in TEMPERATURE_KEYS)
                           or (key == "white" and "white" in modes))
                if not capable:
                    continue  # Home Assistant drops it: the light has no mode for it
                why = "the engine sends no colour"
            elif key == "effect" and not any(self._entity(e).cfg.get("effect") for e in ents):
                continue  # a light without effects: Home Assistant drops it
            else:
                why = UNMODELLED[key]
            if not self.permissive:
                raise AutomationError(f"{where}: data {key!r} is not supported ({why})")
            self._notes.append(f"{service}: {key} ignored")
        return brightness

    @staticmethod
    def _colour_modes(e: Entity) -> set[str]:
        """A light's colour modes as Home Assistant reports them (MQTT discovery's supported_color_modes)."""
        return set(e.cfg.get("supported_color_modes") or ())

    def _homeassistant(self, x: dict, service: str, verb: str) -> dict:
        """homeassistant.turn_on/turn_off/toggle: each entity's own <domain>.<verb>, grouped by domain as Home
        Assistant does; an entity whose domain has no such service is skipped (Home Assistant logs a warning)."""
        where = f"action {service}"
        ents, data = self._call_parts(x, where, "homeassistant")
        steps = []
        for domain in dict.fromkeys(self._entity(e).platform for e in ents):
            if verb not in SERVICES.get(domain, ()):
                continue
            mine = [e for e in ents if self._entity(e).platform == domain]
            steps.append(self._action({"action": f"{domain}.{verb}", "entity_id": mine, "data": data}))
        return steps[0] if len(steps) == 1 else {"kind": "sequence", "steps": steps, "group": True}

    def _climate(self, x: dict, service: str, verb: str) -> dict:
        """A climate action on the home's thermostats: per entity, the (feature, value) commands the climate entity
        sends (Home Assistant's MQTT climate: `temperature_command_template` and `mode_command_template`)."""
        where = f"action {service}"
        ents, data = self._call_parts(x, where, "climate")
        commands = {}
        for entity_id in ents:
            e = self._entity(entity_id)
            if e.platform != "climate":
                raise AutomationError(f"{where}: {entity_id} is not a climate")
            commands[entity_id] = self._climate_commands(e, verb, data, where)
        return {"kind": "climate", "service": service, "entities": ents, "commands": commands}

    def _climate_commands(self, e: Entity, verb: str, data: dict, where: str) -> list[tuple[str, object]]:
        features = self.runtime.twins[e.twin_id].features
        modes = e.cfg.get("modes") or []

        def mode(name) -> tuple[str, str]:
            if "mode_command_topic" not in e.cfg:
                raise AutomationError(f"{where}: {e.entity_id} cannot change its mode")
            if name not in modes:
                raise AutomationError(f"{where}: {e.entity_id} has no mode {name!r} (it has {', '.join(modes)})")
            return "system_mode", name

        def temperature(feature: str, value, key: str) -> tuple[str, float]:
            c = _number(value, f"{where}: {key}")
            # a Fahrenheit entity gets the value converted from Celsius: this assumes a metric Home Assistant
            v = round(c * 9 / 5 + 32, 1) if e.cfg.get("temperature_unit") == "F" else c
            lo, hi = features[feature].get("range") or (None, None)
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                raise AutomationError(f"{where}: {key} {value!r} is outside {e.entity_id}'s range {lo}..{hi}")
            return feature, v

        if verb in ("turn_on", "turn_off"):
            _keys(data, set(), f"{where}: data")
            return [mode("off") if verb == "turn_off" else mode(next(
                (m for m in TURN_ON_MODES if m in modes), TURN_ON_MODES[0]))]
        if verb == "set_hvac_mode":
            _keys(data, {"hvac_mode"}, f"{where}: data")
            return [mode(data.get("hvac_mode"))]
        _keys(data, SET_TEMPERATURE_KEYS, f"{where}: data")
        if "target_temp_low" in data or "target_temp_high" in data:
            # the discovered climate entity has one temperature command topic: no TARGET_TEMPERATURE_RANGE, so Home
            # Assistant refuses the call, whatever setpoints the twin has
            raise AutomationError(f"{where}: the climate entity has no target temperature range "
                                  "(target_temp_low/target_temp_high)")
        if "temperature" not in data:
            raise AutomationError(f"{where}: needs temperature")
        out: list[tuple[str, object]] = []
        if "hvac_mode" in data:  # Home Assistant's order: the mode, then the temperature
            out.append(mode(data["hvac_mode"]))
        out.append(temperature(e.feature, data["temperature"], "temperature"))
        return out

    @staticmethod
    def _record(x: dict, service: str) -> dict:
        """An action the engine cannot execute, kept as the service and the entities it names (which need not be
        the home's: `notify.mobile_app_phone`)."""
        where = f"action {service}"
        target = x.get("target") or {}
        if not isinstance(target, dict):
            raise AutomationError(f"{where}: target {target!r} is not a mapping")
        data = {**(x.get("data") if isinstance(x.get("data"), dict) else {}),
                **(x.get("data_template") if isinstance(x.get("data_template"), dict) else {})}
        targets = _entities(x["entity_id"] if "entity_id" in x else target.get("entity_id", data.get("entity_id")))
        if not all(isinstance(t, str) for t in targets):
            raise AutomationError(f"{where}: entity_id {targets!r} is not entity ids")
        return {"kind": "record", "service": service, "targets": targets}

    # -- running ------------------------------------------------------------------------------
    def start(self) -> None:
        clock = self.session.clock
        for e in self.entities.values():
            if e.platform == "sun":
                continue
            value = self.runtime.state(e.twin_id).get(e.feature)
            self._state[e.entity_id] = "unknown" if e.platform == "event" else self._state_of(e, value)
        self.session.subscribe_reports(self._on_report)  # as Home Assistant hears them: across the links
        self._midnight = clock.now() - self.home.start_time
        if self.site is not None:
            from vesper.env.sun import sun_at
            here = self.site.utc(clock.now() - self._midnight)
            up = sun_at(self.site.lat, self.site.lon, self.site.elevation_m, here).elevation > -0.833
            self._state["sun.sun"] = "above_horizon" if up else "below_horizon"
        for day in (0, 1):
            self._schedule_day(day)
        clock.call_at(self._midnight + DAY_S, lambda: self._next_day(2))

    def _schedule_day(self, day: int) -> None:
        now = self.session.clock.now()
        if self.site is not None:
            events = self._sun_events(day)
            for ev, state in (("sunrise", "above_horizon"), ("sunset", "below_horizon")):
                if events[ev] is not None and self._midnight + events[ev] >= now:
                    self.session.clock.call_at(self._midnight + events[ev],
                                               lambda state=state: self._apply([self._set("sun.sun", state)]))
            for a in self.automations:
                for t in a.triggers:
                    if t["kind"] == "sun" and events[t["event"]] is not None:
                        when = self._midnight + events[t["event"]] + t["offset"]
                        if when >= now:
                            self.session.clock.call_at(when, lambda a=a, t=t: self._fire(
                                a, f"sun {t['event']}{t['offset']:+g} s"))
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
        if e.cfg.get("matter"):  # Matter's values (HA matter/binary_sensor.py, lock.py)
            if e.feature == "Boolean State.StateValue":
                return "off" if value else "on"  # true while the contact is closed; a door sensor is on when open
            if e.feature == "Occupancy Sensing.Occupancy":
                return "on" if int(value) & 1 else "off"
            if e.platform == "lock":
                return "locked" if value == "Locked" else "unlocked"
            return "on" if value else "off"
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
        self._apply(changes)

    def _set(self, entity: str, new: str) -> tuple:
        """A state an entity that no device reports takes (sun.sun): the change, for `_apply`."""
        old, self._state[entity] = self._state.get(entity), new
        return entity, old, new

    def _apply(self, changes: list) -> None:
        changes = [c for c in changes if c[1] != c[2]]
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
        if c["kind"] == "sun":
            since = self.session.clock.now() - self._midnight
            events = self._sun_events(int(since // DAY_S))
            after = None if c["after"] is None else events[c["after"]]
            before = None if c["before"] is None else events[c["before"]]
            if (c["after"] and after is None) or (c["before"] and before is None):
                return False  # the sun does not rise or set that day
            a = None if after is None else after + c["after_offset"]
            b = None if before is None else before + c["before_offset"]
            if a is not None and b is not None and a >= b:  # after sunset and before sunrise: either
                return since >= a or since < b
            return (a is None or since >= a) and (b is None or since < b)
        now = (self.session.clock.now() - self._midnight) % DAY_S
        after, before = c["after"], c["before"]
        if after is not None and before is not None and after >= before:  # across midnight; equal: all day
            return now >= after or now < before
        return (after is None or now >= after) and (before is None or now < before)

    def _run(self, a: Automation, actions: list, why: str) -> None:
        while actions:
            x = actions.pop(0)
            if x["kind"] == "sequence":
                actions[:0] = x["steps"]
                continue
            if x["kind"] == "delay":
                clock = self.session.clock
                clock.call_at(clock.now() + x["seconds"], lambda: self._run(a, actions, why))
                return
            if x["kind"] == "record":  # no twin: the session writes down the call and sends nothing
                for target in x["targets"] or [""]:  # an action that names no entity (notify.notify) is kept too
                    self.session.automation_command(a.name, why, x["service"], target, "", "", None)
                continue
            if x["kind"] == "climate":
                for entity_id in x["entities"]:
                    for feature, value in x["commands"][entity_id]:
                        self.session.automation_command(a.name, why, x["service"], entity_id,
                                                        self.entities[entity_id].twin_id, feature, value)
                continue
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
        if e.cfg.get("matter"):  # Home Assistant's Matter integration (HA matter/light.py, lock.py)
            command = {e.feature: ("Locked" if verb == "lock" else "Unlocked") if domain == "lock"
                       else verb == "turn_on"}
            level = "Level Control.CurrentLevel"
            if (domain == "light" and verb == "turn_on" and brightness is not None
                    and level in self.runtime.twins[e.twin_id].features):  # MoveToLevelWithOnOff: 1..254
                command[level] = max(min(round(brightness / 255 * 254), 254), 1)
        elif e.cfg.get("tasmota"):  # Home Assistant's Tasmota integration: POWER<n>, or a Dimmer (HA tasmota/light.py)
            command = {e.feature: "ON" if verb == "turn_on" else "OFF"}
            if domain == "light" and verb == "turn_on" and brightness is not None:
                dimmer = max(min(round(brightness / 255 * 100), 100), 1)  # its scale_brightness: 1..100
                command = {"Dimmer": dimmer, e.feature: "ON"}  # as the device applies it: dimming lights it
        elif domain == "light":  # the JSON schema light's command (HA mqtt/light/schema_json.py)
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
