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
Supported, with Home Assistant's semantics (each in tests/lab/test_automations*.py):
- triggers `state` (to, from, not_to, not_from, attribute, for; with none of to/from/not_to/not_from an attribute
  change fires too, `to: ~` is any change of the state), `numeric_state` (above, below as numbers or a threshold
  entity, attribute, for), `time` (at, weekday), `time_pattern` (hours/minutes/seconds, '*', '/n'),
  `homeassistant` (start fires as the engine starts; shutdown never fires in a run), `template` (for; fires when
  its result turns true, re-rendered on the states it read and each minute if it read the time) and, for a home with
  a site (vesper.env), `sun` (event, offset); a trigger's id (else its index) is `trigger.id`;
- conditions `state` (attribute, for, match), `numeric_state` (attribute), `time` (after, before, weekday),
  `sun` (after and before sunrise or sunset, with offsets; across midnight is either), `trigger` (id), `template`
  (also a bare template string), `and`/`or`/`not` (nested, and their `- or: [...]` shorthand);
- entities as Home Assistant's integrations make them: `sun.sun` (above_horizon/below_horizon, elevation,
  azimuth, next_rising, ... on Home Assistant's update schedule, from the same astral sun at the site and date), a
  climate (its hvac mode, with temperature, current_temperature and hvac_action), a JSON light's brightness;
- actions `light.*` (brightness, brightness_pct; a colour the light has no mode for is dropped, as Home
  Assistant drops it), `switch.*`, `lock.lock`/`lock.unlock`, `homeassistant.turn_on/turn_off/toggle`,
  `climate.set_temperature` (temperature and hvac_mode; no target temperature range, the discovered entity has
  none), `climate.set_hvac_mode`, `climate.turn_on`/`climate.turn_off` on the home's thermostats (the climate
  entity of Home Assistant's MQTT discovery: the setpoint and `system_mode` it exposes, within its range and its
  modes), targets in `target`, `entity_id` or the legacy data/data_template entity_id (`all` too); `delay`,
  `choose`, `if`/`then`/`else`, `sequence`, a condition step (false ends its own sequence), `stop`, `repeat`
  (count, while, until, for_each), `wait_for_trigger` (timeout, continue_on_timeout) and `variables` (and an
  automation's variables and trigger_variables);
- templates: the subset of vesper.lab.ha_template (states, is_state, state_attr, now, Home Assistant's forgiving
  filters, ...), each refused by name outside it;
- `enabled: false` anywhere; mode `single` (`max`, `max_exceeded` accepted).
An automation using anything else (a template outside the subset, wait_template, parallel, an event, webhook or
other trigger no simulated home fires, a key the engine does not know, an unquoted on/off that Home Assistant itself
refuses) is refused by name when the file is loaded, never run in part; the others run.
`Engine(permissive=True)` is for replaying public automations: an action it cannot execute is recorded (the service
and its targets, which need not be the home's), a light parameter the twin cannot apply (a transition) is dropped, and a
mode other than single runs as single, all said in the
automation's `approximations`; a condition or trigger it cannot evaluate is still refused.
As in Home Assistant, every trigger of a state change is evaluated before any action runs, a `for:` lasts while
the state still matches (a numeric value, while it stays in range), and conditions are checked before mode
`single` drops a trigger. Every command the engine sends is an `automation` record, not an input: a replay runs
the engine again.
"""
from __future__ import annotations

import bisect
import json
import math
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
                   "triggers", "trigger", "conditions", "condition", "actions", "action", "variables",
                   "trigger_variables"}
TRIGGER_KEYS = {"state": {"entity_id", "to", "from", "not_to", "not_from", "attribute", "for"},
                "numeric_state": {"entity_id", "above", "below", "attribute", "value_template", "for"},
                "time": {"at", "weekday"}, "sun": {"event", "offset"}, "time_pattern": {"hours", "minutes", "seconds"},
                "homeassistant": {"event"}, "template": {"value_template", "for"}}
CONDITION_KEYS = {"state": {"entity_id", "state", "attribute", "for", "match"}, "template": {"value_template"},
                  "numeric_state": {"entity_id", "above", "below", "attribute", "value_template"},
                  "time": {"after", "before", "weekday"}, "sun": {"after", "before", "after_offset", "before_offset"}}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Home Assistant's sun entity: its events in the order it checks them, (attribute, astral event, depression, the
# phase until that event); a phase's position update interval (4 minutes is one degree of the sun's arc)
SUN_TIMELINE = (("", "dawn", 18.0, "night"), ("", "dawn", 12.0, "astronomical_twilight"),
                ("next_dawn", "dawn", 6.0, "nautical_twilight"), ("next_rising", "sunrise", None, "twilight"),
                ("", "dawn", -10.0, "small_day"), ("next_noon", "noon", None, None), ("", "dusk", -10.0, "day"),
                ("next_setting", "sunset", None, "small_day"), ("next_dusk", "dusk", 6.0, "twilight"),
                ("", "dusk", 12.0, "nautical_twilight"), ("", "dusk", 18.0, "astronomical_twilight"),
                ("next_midnight", "midnight", None, None))
SUN_PHASE_BY_ELEVATION = ((10, "day"), (0, "small_day"), (-6, "twilight"), (-12, "nautical_twilight"),
                          (-18, "astronomical_twilight"), (-90, "night"))
SUN_HORIZON_DEG = -0.833  # Home Assistant's ELEVATION_HORIZON: sun.sun is above_horizon while the elevation is above it
SUN_PHASE_UPDATE_S = {"night": 20 * 60, "astronomical_twilight": 8 * 60, "nautical_twilight": 8 * 60,
                      "twilight": 4 * 60, "small_day": 2 * 60, "day": 4 * 60}
PATTERN_MAX = {"hours": 23, "minutes": 59, "seconds": 59}
# Triggers no simulated home fires: what each one needs
TRIGGER_NEEDS = {"webhook": "it needs an HTTP request", "mqtt": "it needs an MQTT message",
                 "tag": "it needs an NFC tag scan", "conversation": "it needs a voice or text command",
                 "device": "a device trigger needs Home Assistant's device registry",
                 "zone": "it needs a person's location", "geo_location": "it needs a geolocation feed",
                 "calendar": "it needs a calendar", "persistent_notification": "it needs a notification",
                 "event": None}  # event: by its event type (`_event_class`)
DEVICE_EVENTS = ("zha_event", "deconz_event", "zwave_js_", "hue_event", "shelly.", "homematic.", "flic_", "knx_",
                 "lutron_caseta", "esphome.", "tasmota", "xiaomi_aqara", "nanoleaf", "rfxtrx", "dahua", "hikvision",
                 "nuki_", "matter_", "sonos", "button_", "remote_")
APP_EVENTS = ("mobile_app_", "ios.", "html5_notification", "alexa_", "android", "companion")
SERVICE_EVENTS = ("telegram_", "whatsapp", "gotify", "ifttt_", "feedreader", "yandex_", "transmission_", "signal_",
                  "matrix_", "discord", "slack", "pushover", "imap", "email", "webostv", "notify")
INTERNAL_EVENTS = ("state_changed", "call_service", "homeassistant_", "automation_", "script_", "lovelace_",
                   "entity_registry_", "device_registry_", "area_registry_", "component_loaded", "service_registered",
                   "device_tracker_new_device", "event_template_reloaded", "core_config_updated", "themes_updated",
                   "user_added", "user_removed", "timer.", "panels_updated", "logbook_entry", "tag_scanned")
SUN_EVENTS = ("sunrise", "sunset")
LOGIC = ("and", "or", "not")  # Home Assistant's `not`: none of its conditions holds
REPEAT_LIMIT = 10000  # Home Assistant stops a repeat after this many iterations
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
# Gone from Home Assistant 2026.10.2's light.turn_on schema (only color_temp_kelvin is left): a call with one of
# them fails when it runs ("not a valid option"), and the run stops there
REMOVED_LIGHT_KEYS = {"kelvin", "color_temp"}
COLOUR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
UNMODELLED = {"transition": "the twin has no fade", "flash": "the twin does not flash",
              "effect": "the twin has no effects", "profile": "Home Assistant's light profiles are not modelled",
              "brightness_step": "a relative brightness is not modelled",
              "brightness_step_pct": "a relative brightness is not modelled"}
HOMEASSISTANT_VERBS = ("turn_on", "turn_off", "toggle")  # homeassistant.<verb>: each entity's own <domain>.<verb>
STEP_KEYS = {"alias", "continue_on_error", "enabled"}  # any action step may have them
UNSUPPORTED_STEPS = {
    "wait_template": "a template wait", "parallel": "parallel branches",
    "event": "it fires an event, which only Home Assistant's other listeners see",
    "scene": "scenes are Home Assistant's", "device_id": "a device action needs Home Assistant's device registry",
    "set_conversation_response": "conversations are Home Assistant's"}


class AutomationError(ValueError):
    """An automation the engine cannot run; the message says what."""


class ServiceRefuses(AutomationError):
    """A call Home Assistant's service refuses when it runs (its schema): the run stops at it."""


@dataclass
class Entity:
    entity_id: str
    device: str
    twin_id: str
    feature: str  # what its commands set (a climate's setpoint) and, unless `state_feature`, what its state follows
    platform: str
    cfg: dict
    state_feature: str = ""
    attrs: dict[str, str] = field(default_factory=dict)  # attribute -> the feature it follows
    static: dict = field(default_factory=dict)  # attributes that do not change (a climate's hvac_modes)
    actions: dict = field(default_factory=dict)  # a climate's running state -> its hvac_action
    optimistic: bool = False  # a climate whose mode nothing reports: its state is the mode last commanded

    def features(self) -> set[str]:
        return {self.feature, self.state_feature, *self.attrs.values()} - {""}


def _json_key(template) -> str:
    """The feature an MQTT discovery value template reads (`value_json["x"]` or `value_json.x`), or ''."""
    m = re.search(r"""value_json(?:\[["']([^"']+)["']\]|\.([A-Za-z0-9_]+))""", template or "")
    return (m.group(1) or m.group(2)) if m else ""


def _model(e: Entity) -> None:
    """What Home Assistant's MQTT entity reports beyond the state the engine always had: a climate's state is its
    hvac mode, with its setpoint, current temperature and hvac action as attributes; a JSON light's brightness."""
    cfg = e.cfg
    if e.platform == "climate":
        e.state_feature = _json_key(cfg.get("mode_state_template"))
        # Home Assistant's MQTT climate without a mode state topic is optimistic: off until a mode is commanded
        e.optimistic = not e.state_feature
        e.attrs = {k: v for k, v in (("temperature", _json_key(cfg.get("temperature_state_template")) or e.feature),
                                     ("current_temperature", _json_key(cfg.get("current_temperature_template"))),
                                     ("hvac_action", _json_key(cfg.get("action_template")))) if v}
        e.static = {"hvac_modes": list(cfg.get("modes") or []), "min_temp": cfg.get("min_temp", 7),
                    "max_temp": cfg.get("max_temp", 35)}
        m = re.search(r"set m = (\{[^}]*\})", cfg.get("action_template") or "")
        if m:
            e.actions = json.loads(m.group(1))
    elif e.platform == "light" and cfg.get("brightness"):
        e.attrs = {"brightness": "brightness"}


@dataclass
class Automation:
    name: str
    triggers: list
    conditions: list
    actions: list
    running: bool = field(default=False)
    approximations: list[str] = field(default_factory=list)  # what permissive mode ran differently from the file
    variables: dict = field(default_factory=dict)  # rendered when it fires, before its conditions
    trigger_variables: dict = field(default_factory=dict)  # rendered when it loads, for its trigger templates


@dataclass
class _Tpl:
    """A compiled template inside a value (a variable, service data)."""
    template: object


@dataclass
class _Held:
    """A `for:` under way: it lasts while `still(state, attributes)` holds for the entity's new states."""
    still: Callable[[str, dict], bool]
    alive: bool = True


@dataclass
class _Frame:
    """A sequence under way: its remaining steps; for a repeat's iteration, the repeat and the iteration (from 1)."""
    steps: list
    repeat: dict | None = None
    index: int = 0


@dataclass
class _Run:
    """One run of an automation: why it fired, its variables (`trigger`, `wait`) and the sequences under way."""
    automation: Automation
    why: str
    context: dict
    frames: list[_Frame]
    counts: dict = field(default_factory=dict)  # a repeat whose count is a template -> its count this run


@dataclass
class _Watch:
    """A template trigger under way (Home Assistant's template trigger over track_template_result): `fire(why,
    trigger variables)` when its result turns true after it was false (armed), at once or after `for`."""
    trigger: dict
    fire: Callable
    variables: dict
    armed: bool = False
    last: object = None  # the last rendered result (a TemplateFailed is a result too)
    info: object = None  # what the last render read
    rendered_at: float = float("-inf")
    pending: bool = False  # a rate-limited render is scheduled
    minute: bool = False  # the per-minute render is scheduled
    delay: list = field(default_factory=list)  # [alive] of the `for` timer under way
    active: bool = True


@dataclass
class _Wait:
    """A wait_for_trigger under way: its triggers, and `done(why, trigger variables)` to resume the run."""
    triggers: list
    active: bool = True
    done: Callable = field(default=lambda why, tvars: None)


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
            _model(e)
            for feature in e.features() or {""}:
                self._of_feature.setdefault((e.twin_id, feature), []).append(e)
        self.automations: list[Automation] = []
        self.refused: list[dict] = []
        self._state: dict[str, str] = {}
        self._attrs: dict[str, dict] = {}  # entity -> its attributes
        self._feat: dict[tuple[str, str], object] = {}  # (twin, feature) -> its last reported value
        self._changed: dict[str, float] = {}  # entity -> when its state last changed (Home Assistant's last_changed)
        self._sun_phase, self._sun_change = None, None
        self._templates = None  # vesper.lab.ha_template.Templates, on first use
        self._varnames: set[str] = set()
        self._watches: list[_Watch] = []  # the template triggers under way
        self._modes: dict[str, str] = {}  # an optimistic climate -> the mode last commanded
        self._armed: dict[int, set] = {}  # owner (automation or wait) -> its armed (numeric_state trigger, entity)
        self._held: dict[str, list[_Held]] = {}  # entity -> the `for:` timers waiting on it
        self._waits: list[_Wait] = []  # the wait_for_trigger steps under way
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
        self._chooses = 0  # the automation's choose and if steps, numbered in the order they are written
        self._varnames = set(_variable_names(a))  # what templates may read besides trigger, wait and repeat
        trigger_variables = self._variables(a.get("trigger_variables"), "trigger_variables")
        variables = self._variables(a.get("variables"), "variables")
        raw_triggers = _list(a.get("triggers", a.get("trigger")), "triggers")
        if not raw_triggers:
            raise AutomationError("it has no trigger")
        triggers = self._triggers(raw_triggers)
        conditions = self._conditions(a.get("conditions", a.get("condition")), "conditions")
        actions = self._actions(a.get("actions", a.get("action")))
        approximations += self._notes
        return Automation(label, triggers, conditions, actions, approximations=approximations,
                          variables=variables, trigger_variables=trigger_variables)

    # -- templates ----------------------------------------------------------------------------------
    @property
    def templates(self):
        """The template renderer (vesper.lab.ha_template), made on first use; without jinja2 every template is
        refused."""
        if self._templates is None:
            try:
                from vesper.lab.ha_template import Templates
                self._templates = Templates(self)
            except ImportError:
                raise AutomationError("templates are not supported (jinja2 is not installed)") from None
        return self._templates

    def _template(self, source: str, where: str):
        from vesper.lab.ha_template import TemplateRefused
        try:
            return self.templates.compile(source, {"trigger", "wait", "repeat"} | self._varnames,
                                          set(self.entities), where)
        except TemplateRefused as e:
            raise AutomationError(str(e)) from None

    def _complex(self, value, where: str):
        """A value that may hold templates (a variable, Home Assistant's render_complex): templates compiled."""
        from vesper.lab.ha_template import is_template
        if is_template(value):
            return _Tpl(self._template(value, where))
        if isinstance(value, dict):
            return {k: self._complex(v, where) for k, v in value.items()}
        if isinstance(value, list):
            return [self._complex(v, where) for v in value]
        return value

    def _variables(self, raw, where: str) -> dict:
        if raw is None:
            return {}
        if not isinstance(raw, dict) or not all(isinstance(k, str) for k in raw):
            raise AutomationError(f"{where}: a mapping of names")
        return {k: self._complex(v, f"{where}: {k}") for k, v in raw.items()}

    def _rendered(self, value, context: dict):
        """A complex value rendered as Home Assistant's render_complex renders it (each template's result parsed
        as the literal it spells); a failed render raises TemplateFailed."""
        from vesper.lab.ha_template import parse_result
        if isinstance(value, _Tpl):
            return parse_result(self.templates.render(value.template, context)[0])
        if isinstance(value, dict):
            return {k: self._rendered(v, context) for k, v in value.items()}
        if isinstance(value, list):
            return [self._rendered(v, context) for v in value]
        return value

    def _render_variables(self, variables: dict, context: dict) -> bool:
        """Render variables into the context, in order (each sees the ones before); False if one fails."""
        from vesper.lab.ha_template import TemplateFailed
        for k, v in variables.items():
            try:
                context[k] = self._rendered(v, context)
            except TemplateFailed:
                return False
        return True

    def _now(self):
        """Home Assistant's `trigger.now` of a time trigger: the local time (None for a home without a site)."""
        if self.site is None:
            return None
        return self.site.utc(self.session.clock.now() - self._midnight).astimezone(self.site.zone)

    def _datetime_of(self, t: float):
        from vesper.lab.ha_template import TemplateFailed
        if self.site is None:
            raise TemplateFailed("the home has no site, so it has no date")
        return self.site.utc(t - self._midnight)

    def _snapshot(self, entity: str, state, attrs: dict, changed: float):
        from vesper.lab.ha_template import FixedState
        return FixedState(entity, state, attrs, changed, self._datetime_of)

    def _triggers(self, raw: list) -> list[dict]:
        """The enabled triggers, each with its id: its `id`, else its index among all the triggers (Home
        Assistant's `trigger.id`, disabled ones counted)."""
        out = []
        for idx, t in enumerate(raw):
            if isinstance(t, dict) and t.get("enabled") is False:
                continue
            parsed = self._trigger(t)
            tid = t.get("id", idx)
            if isinstance(tid, bool) or not isinstance(tid, str | int):
                raise AutomationError(f"trigger id: {tid!r} is not a string")
            out.append({**parsed, "id": str(tid), "idx": str(idx)})
        return out

    def _conditions(self, raw, where: str) -> list[dict]:
        """A list of conditions (one condition alone is a list of one), the disabled ones left out."""
        if isinstance(raw, str | dict):
            raw = [raw]
        return [self._condition(c) for c in _enabled(_list(raw, where))]

    def _actions(self, raw) -> list[dict]:
        return [self._action(x) for x in _enabled(_list(raw, "actions"))]

    def _next_choose(self) -> int:
        """The next number for a choose or if step of the automation being parsed (0, 1, ... as written)."""
        n = getattr(self, "_chooses", 0)
        self._chooses = n + 1
        return n

    def _entity(self, entity_id) -> Entity:
        e = self.entities.get(entity_id) if isinstance(entity_id, str) else None
        if e is None:
            raise AutomationError(f"entity {entity_id} is not one of this home's")
        return e

    def _trigger(self, t) -> dict:
        kind = t.get("trigger", t.get("platform")) if isinstance(t, dict) else None
        if isinstance(kind, str) and kind in TRIGGER_NEEDS:
            why = TRIGGER_NEEDS[kind] or _event_class(t.get("event_type"))
            raise AutomationError(f"trigger {kind} is not supported ({why})")
        if kind not in TRIGGER_KEYS:
            raise AutomationError(f"trigger {kind!r} is not supported")
        where = f"trigger {kind}"
        _keys(t, TRIGGER_KEYS[kind] | {"trigger", "platform", "id", "alias"}, where)
        if kind == "sun":
            self._needs_site(where)
            if t.get("event") not in SUN_EVENTS:
                raise AutomationError(f"{where}: event {t.get('event')!r} is not sunrise or sunset")
            return {"kind": "sun", "event": t["event"],
                    "offset": _offset(t["offset"], f"{where}: offset") if "offset" in t else 0.0}
        if kind == "time":
            at = t.get("at")
            return {"kind": "time", "at": [_clock(x, f"{where}: at") for x in (at if isinstance(at, list) else [at])],
                    "weekday": self._weekdays(t["weekday"], where) if "weekday" in t else None}
        if kind == "time_pattern":
            if not set(t) & set(PATTERN_MAX):
                raise AutomationError(f"{where}: hours, minutes or seconds is required")
            given = {k: _pattern(t[k], PATTERN_MAX[k], f"{where}: {k}") for k in PATTERN_MAX if k in t}
            # Home Assistant: a larger unit given defaults the smaller ones to zero; a missing one matches any value
            if "minutes" not in given and "hours" in given:
                given["minutes"] = {0}
            if "seconds" not in given and "minutes" in given:
                given["seconds"] = {0}
            return {"kind": "time_pattern", **{k: sorted(given.get(k, range(PATTERN_MAX[k] + 1))) for k in PATTERN_MAX}}
        if kind == "template":
            if not isinstance(t.get("value_template"), str):
                raise AutomationError(f"{where}: value_template is required")
            return {"kind": "template", "template": self._template(t["value_template"], where),
                    "for": _seconds(t["for"], f"{where}: for") if "for" in t else 0.0}
        if kind == "homeassistant":
            if t.get("event") not in ("start", "shutdown"):
                raise AutomationError(f"{where}: event {t.get('event')!r} is not start or shutdown")
            return {"kind": "homeassistant", "event": t["event"]}  # a run never shuts Home Assistant down
        if "entity_id" not in t:
            raise AutomationError(f"{where}: entity_id is required")
        out = {"kind": kind, "entities": [self._entity(x).entity_id for x in _entities(t["entity_id"])],
               "for": _seconds(t["for"], "for") if "for" in t else 0.0, "attribute": self._attribute(t, where)}
        if kind == "state":
            # an attribute's values may be anything; a state is a quoted string (Home Assistant's two schemas)
            values = (lambda v, w: v if isinstance(v, list) else [v]) if out["attribute"] else _states
            for key in ("to", "from", "not_to", "not_from"):
                out[key] = values(t[key], f"{where}: {key}") if t.get(key) is not None else None
            out["match_all"] = not {"to", "from", "not_to", "not_from"} & set(t)
            out["from_only"] = "from" in t and "to" not in t  # a `for:` then lasts while the state is not `from`
        else:
            self._no_value_template(t, where)
            out["above"] = self._bound(t, "above", where)
            out["below"] = self._bound(t, "below", where)
            if out["above"] is None and out["below"] is None:
                raise AutomationError(f"{where}: above or below is required")
        return out

    @staticmethod
    def _attribute(item: dict, where: str) -> str | None:
        attribute = item.get("attribute")
        if attribute is not None and not isinstance(attribute, str):
            raise AutomationError(f"{where}: attribute {attribute!r} is not a name")
        return attribute

    @staticmethod
    def _no_value_template(item: dict, where: str) -> None:
        if "value_template" in item:
            raise AutomationError(f"{where}: value_template is not supported (a template)")

    def _bound(self, item: dict, key: str, where: str):
        """A numeric_state threshold: a number (vol.Coerce(float)), or a number, sensor or input_number entity whose
        state is the threshold when it is checked."""
        if key not in item:
            return None
        value = item[key]
        if isinstance(value, str) and re.fullmatch(r"(input_number|number|sensor)\.\w+", value):
            return {"entity": self._entity(value).entity_id}
        return _coerced(value, f"{where}: {key}")

    def _condition(self, c) -> dict:
        if isinstance(c, str):  # Home Assistant's shorthand template condition
            return {"kind": "template", "template": self._template(c, "condition template")}
        if isinstance(c, dict) and c.get("condition") == "template":
            _keys(c, {"condition", "value_template", "alias"}, "condition template")
            if not isinstance(c.get("value_template"), str):
                raise AutomationError("condition template: value_template is required")
            return {"kind": "template", "template": self._template(c["value_template"], "condition template")}
        if isinstance(c, dict) and "condition" not in c and len(set(c) - {"alias", "enabled"}) == 1 and \
                next(iter(set(c) - {"alias", "enabled"})) in LOGIC:  # Home Assistant's shorthand: `- or: [...]`
            kind = next(iter(set(c) - {"alias", "enabled"}))
            c = {"condition": kind, "conditions": c[kind]}
        kind = c.get("condition") if isinstance(c, dict) else None
        if isinstance(kind, list):  # `condition: [...]` is not Home Assistant's
            raise AutomationError("condition: a list is not a condition type")
        if kind in LOGIC:
            _keys(c, {"condition", "conditions", "alias"}, f"condition {kind}")
            return {"kind": kind, "conditions": self._conditions(c.get("conditions"), f"condition {kind}")}
        if kind == "trigger":
            _keys(c, {"condition", "id", "alias"}, "condition trigger")
            ids = c.get("id") if isinstance(c.get("id"), list) else [c.get("id")]
            if not ids or not all(isinstance(i, str | int) and not isinstance(i, bool) for i in ids):
                raise AutomationError(f"condition trigger: id {c.get('id')!r} is not a trigger id")
            return {"kind": "trigger", "ids": [str(i) for i in ids]}
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
                    "before": _clock(c["before"], f"{where}: before") if "before" in c else None,
                    "weekday": self._weekdays(c["weekday"], where) if "weekday" in c else None}
        if "entity_id" not in c:
            raise AutomationError(f"{where}: entity_id is required")
        out = {"kind": kind, "entities": [self._entity(x).entity_id for x in _entities(c["entity_id"])],
               "attribute": self._attribute(c, where)}
        if kind == "state":
            if "state" not in c:
                raise AutomationError(f"{where}: state is required")
            if out["attribute"]:
                out["state"] = c["state"] if isinstance(c["state"], list) else [c["state"]]
            else:
                out["state"] = _states(c["state"], f"{where}: state")
            for v in out["state"]:  # Home Assistant compares with an input entity's state: not one of this home's
                if isinstance(v, str) and re.fullmatch(r"input_\w+\.\w+", v):
                    self._entity(v)
            out["for"] = _seconds(c["for"], f"{where}: for") if "for" in c else None
            if c.get("match", "all") not in ("all", "any"):
                raise AutomationError(f"{where}: match {c['match']!r} is not all or any")
            out["any"] = c.get("match") == "any"
        else:
            self._no_value_template(c, where)
            out["above"] = self._bound(c, "above", where)
            out["below"] = self._bound(c, "below", where)
        return out

    def _needs_site(self, where: str) -> None:
        if self.site is None:
            raise AutomationError(f"{where}: the home has no site, so it has no sun (add site: to home.yaml)")

    def _weekdays(self, value, where: str) -> set[int]:
        """Home Assistant's weekday option (mon..sun, one or a list): the days, Monday 0; the site's date says which
        day a run's day is."""
        if self.site is None:
            raise AutomationError(f"{where}: weekday: the home has no site, so it has no date (add site: to home.yaml)")
        days = value if isinstance(value, list) else [value]
        if not days or any(d not in WEEKDAYS for d in days):
            raise AutomationError(f"{where}: weekday {value!r} is not mon..sun")
        return {WEEKDAYS.index(d) for d in days}

    def _weekday(self, day: int) -> int:
        """The weekday (Monday 0) of the run's day `day` (0 is the site's date)."""
        import datetime as dt
        return (self.site.date + dt.timedelta(days=day)).weekday()

    def _sun_events(self, day: int) -> dict:
        from vesper.env.sun import sun_events
        return sun_events(self.site, day)

    def _action(self, x) -> dict:
        if not isinstance(x, dict):
            raise AutomationError("an action is a mapping")
        step = {k: v for k, v in x.items() if k not in STEP_KEYS}  # alias, continue_on_error, enabled
        if "delay" in step:
            _keys(step, {"delay"}, "action delay")
            return {"kind": "delay", "seconds": _seconds(x["delay"], "delay")}
        if "condition" in step or (len(step) == 1 and next(iter(step)) in LOGIC):  # a test: false ends the sequence
            return {"kind": "check", "condition": self._condition(step)}
        if "choose" in step:
            _keys(step, {"choose", "default"}, "action choose")
            n = self._next_choose()
            options = []
            for o in _list(step["choose"], "action choose"):
                if not isinstance(o, dict) or "sequence" not in o:
                    raise AutomationError("action choose: an option is a mapping with conditions and a sequence")
                _keys(o, {"conditions", "sequence", "alias"}, "action choose")
                options.append((self._conditions(o.get("conditions"), "action choose: conditions"),
                                self._actions(o["sequence"])))
            return {"kind": "choose", "options": options, "default": self._actions(step.get("default")), "n": n}
        if "if" in step:
            _keys(step, {"if", "then", "else"}, "action if")
            if "then" not in step:
                raise AutomationError("action if: then is required")
            n = self._next_choose()
            return {"kind": "choose", "options": [(self._conditions(step["if"], "action if"),
                                                   self._actions(step["then"]))],
                    "default": self._actions(step.get("else")), "n": n}
        if "sequence" in step:
            _keys(step, {"sequence"}, "action sequence")
            return {"kind": "sequence", "steps": self._actions(step["sequence"])}
        if "stop" in step:
            _keys(step, {"stop", "error", "response_variable"}, "action stop")
            return {"kind": "stop", "error": step.get("error") is True}
        if "repeat" in step:
            return self._repeat(step)
        if "variables" in step:
            _keys(step, {"variables"}, "action variables")
            return {"kind": "variables", "variables": self._variables(step["variables"], "action variables")}
        if "wait_for_trigger" in step:
            _keys(step, {"wait_for_trigger", "timeout", "continue_on_timeout"}, "action wait_for_trigger")
            return {"kind": "wait", "triggers": self._triggers(_list(step["wait_for_trigger"], "wait_for_trigger")),
                    "timeout": _seconds(step["timeout"], "action wait_for_trigger: timeout") if "timeout" in step
                    else None, "continue": step.get("continue_on_timeout", True) is not False}
        for key, why in UNSUPPORTED_STEPS.items():
            if key in step:
                if self.permissive and key in ("event", "scene"):
                    name = step[key]
                    if not isinstance(name, str):
                        raise AutomationError(f"action {key}: {name!r} is not a name")
                    return {"kind": "record", "service": f"event.{name}" if key == "event" else "scene.turn_on",
                            "targets": [] if key == "event" else [name]}
                raise AutomationError(f"action {key} is not supported ({why})")
        service = x.get("action", x.get("service", x.get("service_template")))
        from vesper.lab.ha_template import is_template
        if "service_template" in x or is_template(service):
            return self._dynamic(x, service)
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
        if domain == "light" and any(isinstance(v, _Tpl) for v in (self._complex(v, where) for v in data.values())):
            # rendered when it runs; a value Home Assistant's light service refuses then stops the run, as there
            return {"kind": "call", "service": service, "entities": ents, "brightness": None,
                    "data": self._complex(data, where), "where": where}
        try:
            brightness = self._light_data(service, ents, data, where) if domain == "light" else \
                self._no_data(data, where)
        except ServiceRefuses as e:  # Home Assistant runs the automation and the call fails when reached
            return {"kind": "fail", "why": str(e)}
        return {"kind": "call", "service": service, "entities": ents, "brightness": brightness}

    def _dynamic(self, x: dict, service) -> dict:
        """A service named by a template (service_template, or a template in action/service): rendered when it
        runs. Strict mode refuses it (what it would call is not known when the file loads); permissive mode runs
        the rendered service as it would run that service written out, or records it."""
        if not self.permissive:
            raise AutomationError("action service_template is not supported (a service chosen when it runs)")
        if not isinstance(service, str):
            raise AutomationError(f"action {service!r} is not supported")
        rest = {k: v for k, v in x.items() if k not in ("action", "service", "service_template")}
        return {"kind": "dynamic", "template": self._template(service, "action service_template"), "rest": rest}

    def _repeat(self, step: dict) -> dict:
        """repeat: count (a number), while (checked before each iteration), until (after each) or for_each (a
        literal list); each iteration is a sequence of its own, as in Home Assistant."""
        where = "action repeat"
        _keys(step, {"repeat"}, where)
        r = step["repeat"]
        if not isinstance(r, dict) or "sequence" not in r:
            raise AutomationError(f"{where}: a mapping with a sequence")
        _keys(r, {"count", "while", "until", "for_each", "sequence"}, where)
        loops = [k for k in ("count", "while", "until", "for_each") if k in r]
        if len(loops) != 1:
            raise AutomationError(f"{where}: one of count, while, until and for_each is required")
        out = {"kind": "repeat", "steps": self._actions(r["sequence"]), "loop": loops[0]}
        if loops[0] == "count" and isinstance(r["count"], str) and "{" in r["count"]:
            out["count"], out["count_template"] = 0, self._template(r["count"], f"{where}: count")
        elif loops[0] == "count":
            count = _coerced(r["count"], f"{where}: count")
            if count != int(count):
                raise AutomationError(f"{where}: count {r['count']!r} is not a whole number")
            out["count"] = int(count)
        elif loops[0] == "for_each":
            if not isinstance(r["for_each"], list):
                raise AutomationError(f"{where}: for_each {r['for_each']!r} is not a list")
            out["count"], out["items"] = len(r["for_each"]), r["for_each"]
        else:
            out["conditions"] = self._conditions(r[loops[0]], f"{where}: {loops[0]}")
        return out

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
            if key in REMOVED_LIGHT_KEYS and verb != "turn_off":
                raise ServiceRefuses(f"{where}: data {key!r} is not a valid option (Home Assistant 2026.10.2)")
            if key not in allowed:
                raise AutomationError(f"{where}: data {key!r} is not supported")
            if key == "brightness":  # Home Assistant: vol.Coerce(int), clamped to 0..255
                try:
                    if isinstance(value, bool):
                        raise ValueError
                    brightness = min(max(int(value), 0), 255)
                except (TypeError, ValueError):
                    raise AutomationError(f"{where}: data brightness: {value!r} is not a number") from None
                continue
            if key == "brightness_pct":  # vol.Coerce(float) within 0..100, then round(pct * 255 / 100)
                v = _coerced(value, f"{where}: data {key}")
                if not 0 <= v <= 100:
                    raise AutomationError(f"{where}: data {key}: {value!r} is not between 0 and 100")
                brightness = round(v * 255 / 100)
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
            twin_state = self.runtime.state(e.twin_id)
            for feature in e.features():
                self._feat[(e.twin_id, feature)] = twin_state.get(feature)
            self._state[e.entity_id] = "unknown" if e.platform == "event" else self._entity_state(e)
            self._attrs[e.entity_id] = self._entity_attrs(e)
        self.session.subscribe_reports(self._on_report)  # as Home Assistant hears them: across the links
        self._midnight = clock.now() - self.home.start_time
        self._changed = dict.fromkeys(self._state, clock.now())
        if self.site is not None:
            self._sun_update_events(initial=True)
        for a in self.automations:
            self._arm(id(a), a.triggers)
        for day in (0, 1):
            self._schedule_day(day)
        clock.call_at(self._midnight + DAY_S, lambda: self._next_day(2))
        for a in self.automations:
            for t in a.triggers:
                if t["kind"] == "time_pattern":
                    self._pattern_next(a, t)
                elif t["kind"] == "homeassistant" and t["event"] == "start":  # Home Assistant has just started
                    clock.call_at(clock.now(), lambda a=a, t=t: self._fire(a, "homeassistant start",
                                                                           _tvars(t, event="start")))
                elif t["kind"] == "template":
                    self._watch(t, lambda why, tv, a=a: self._fire(a, why, tv), a.trigger_variables)

    def _schedule_day(self, day: int) -> None:
        now = self.session.clock.now()
        if self.site is not None:
            events = self._sun_events(day)
            for a in self.automations:
                for t in a.triggers:
                    if t["kind"] == "sun" and events[t["event"]] is not None:
                        when = self._midnight + events[t["event"]] + t["offset"]
                        if when >= now:
                            self.session.clock.call_at(when, lambda a=a, t=t: self._fire(
                                a, f"sun {t['event']}{t['offset']:+g} s",
                                _tvars(t, event=t["event"], offset=t["offset"])))
        for a in self.automations:
            for t in a.triggers:
                if t["kind"] == "time" and (t["weekday"] is None or self._weekday(day) in t["weekday"]):
                    for at in t["at"]:
                        when = self._midnight + day * DAY_S + at
                        if when >= now:
                            self.session.clock.call_at(when, lambda a=a, t=t, at=at: self._fire(
                                a, f"time {at:g}", _tvars(t, now=self._now(), entity_id=None)))

    # -- Home Assistant's sun entity (sun/entity.py) ------------------------------------------------
    def _utc(self, t: float):
        return self.site.utc(t - self._midnight)

    def _logical(self, when) -> float:
        return self._midnight + (when - self.site.utc(0.0)).total_seconds()

    def _astral_next(self, event: str, after, depression: float | None = None):
        """Home Assistant's get_location_astral_event_next: the first `event` strictly after `after` (UTC), trying
        the local dates from the day before on; None if it does not happen within a year (the polar day or night)."""
        import datetime as dt

        from astral import Observer
        from astral import sun as astral_sun
        obs = Observer(latitude=self.site.lat, longitude=self.site.lon, elevation=self.site.elevation_m)
        date = after.astimezone(self.site.zone).date()
        fn = getattr(astral_sun, event)
        for mod in range(-1, 367):
            day = date + dt.timedelta(days=mod)
            try:
                t = fn(obs, day, depression, tzinfo=dt.UTC) if depression is not None else fn(obs, day, tzinfo=dt.UTC)
            except ValueError:
                continue
            if t > after:
                return t
        return None

    def _sun_update_events(self, initial: bool = False) -> None:
        """Home Assistant's update_events (2026.10.2): the next of the sun's twelve events and the phase until it, and
        the next_* attributes; then the position (one state write with both), and again at that next event."""
        clock = self.session.clock
        utc = self._utc(clock.now())
        nxt, change, phase = {}, None, None
        for attr, event, depression, before in SUN_TIMELINE:
            t = self._astral_next(event, utc, depression)
            if attr:
                nxt[attr] = t
            if t is not None and (change is None or t < change):
                change, phase = t, before
        if change is not None and phase is None:  # the next event is noon or midnight: the phase from the elevation
            elevation = self._sun_position(change, rounded=False)[0]
            phase = next(p for limit, p in SUN_PHASE_BY_ELEVATION if elevation >= limit)
        self._sun_phase = phase
        self._sun_change = None if change is None else self._logical(change)
        attrs = {**self._attrs.get("sun.sun", {}),
                 **{k: None if v is None else v.isoformat() for k, v in nxt.items()},
                 "rising": nxt["next_noon"] is not None and nxt["next_midnight"] is not None
                 and nxt["next_noon"] < nxt["next_midnight"]}
        self._sun_update_position(initial, attrs)
        if self._sun_change is not None:
            clock.call_at(self._sun_change, self._sun_update_events)

    def _sun_position(self, when, rounded: bool = True) -> tuple[float, float]:
        from astral import Observer
        from astral.sun import azimuth, elevation
        obs = Observer(latitude=self.site.lat, longitude=self.site.lon, elevation=self.site.elevation_m)
        el, az = elevation(obs, when), azimuth(obs, when)
        return (round(el, 2), round(az, 2)) if rounded else (el, az)

    def _sun_update_position(self, initial: bool = False, attrs: dict | None = None) -> None:
        """Home Assistant's update_sun_position: the elevation and azimuth (two decimals) now, the state from them
        (above_horizon while the elevation is above SUN_HORIZON_DEG, as 2026.10.2's sun entity has it), written once
        with `attrs` (update_events' attributes, else the current ones); and again after the phase's interval unless
        the next event comes within 1.25 intervals."""
        clock = self.session.clock
        elevation, azimuth = self._sun_position(self._utc(clock.now()))
        attrs = {**(self._attrs.get("sun.sun", {}) if attrs is None else attrs), "elevation": elevation,
                 "azimuth": azimuth}
        state = "above_horizon" if elevation > SUN_HORIZON_DEG else "below_horizon"
        if initial:
            self._state["sun.sun"], self._attrs["sun.sun"] = state, attrs
            self._changed["sun.sun"] = clock.now()
        else:
            self._apply([self._set("sun.sun", state, attrs)])
        delta = SUN_PHASE_UPDATE_S.get(self._sun_phase)
        if delta is None or self._sun_change is None or clock.now() + delta * 1.25 > self._sun_change:
            return
        clock.call_at(clock.now() + delta, self._sun_update_position)

    def _pattern_next(self, a: Automation, t: dict) -> None:
        """Schedule a time_pattern trigger's next firing (each firing schedules the one after)."""
        when = self._next_pattern(t, self.session.clock.now())

        def fire():
            self._pattern_next(a, t)
            self._fire(a, f"time_pattern {(when - self._midnight) % DAY_S:g}", _tvars(t, now=self._now()))
        self.session.clock.call_at(when, fire)

    def _next_pattern(self, t: dict, after: float) -> float:
        """The first instant after `after` whose local hour, minute and second all match the pattern."""
        since = after - self._midnight
        day = int(since // DAY_S)
        for d in (day, day + 1):
            h0, m0 = (int(since - d * DAY_S) // 3600, int(since - d * DAY_S) % 3600 // 60) if d == day else (0, 0)
            for h in t["hours"][bisect.bisect_left(t["hours"], h0):]:
                for m in t["minutes"][bisect.bisect_left(t["minutes"], m0) if h == h0 else 0:]:
                    for s in t["seconds"]:
                        x = d * DAY_S + h * 3600 + m * 60 + s
                        if x > since:
                            return self._midnight + x
        raise AssertionError("a time pattern matches every day")

    def _next_day(self, day: int) -> None:
        self._schedule_day(day)
        self.session.clock.call_at(self._midnight + day * DAY_S, lambda: self._next_day(day + 1))

    # -- entity states, as Home Assistant's integrations make them -----------------------------------
    def _entity_state(self, e: Entity) -> str:
        value = self._feat.get((e.twin_id, e.state_feature or e.feature))
        if e.platform == "climate":  # Home Assistant's climate state is its hvac mode, never its setpoint
            if e.optimistic:
                return self._modes.get(e.entity_id, "off")
            return "unknown" if value is None else str(value)
        return self._state_of(e, value)

    def _entity_attrs(self, e: Entity) -> dict:
        """The attributes the engine models: a light's brightness (0..255, None while off, as Home Assistant's
        JSON light scales it), a climate's setpoint, current temperature and hvac action (its MQTT templates)."""
        out = dict(e.static)
        for name, feature in e.attrs.items():
            value = self._feat.get((e.twin_id, feature))
            if e.platform == "light" and name == "brightness":
                if self._entity_state(e) != "on" or not value:
                    value = None if self._entity_state(e) != "on" else self._attrs.get(e.entity_id, {}).get(name)
                else:  # color_util.value_to_brightness((1, scale), value)
                    value = math.ceil(value * 255 / e.cfg.get("brightness_scale", 255))
            elif name == "hvac_action" and value is not None:
                value = e.actions.get(value, "idle") if e.actions else value
            out[name] = value
        return out

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
        self._feat[(event.twin_id, event.feature)] = event.value
        changes = []
        for e in self._of_feature.get((event.twin_id, event.feature), ()):
            old, old_attrs = self._state.get(e.entity_id), self._attrs.get(e.entity_id, {})
            if e.platform == "event":  # the time of the last event: every event is a change
                new, attrs = f"{event.observed_t:.6f}", old_attrs
            else:
                new, attrs = self._entity_state(e), self._entity_attrs(e)
                if new == old and attrs == old_attrs:
                    continue
            changes.append(self._set(e.entity_id, new, attrs))
        self._apply(changes)

    def _set(self, entity: str, new: str, attrs: dict | None = None) -> tuple:
        """An entity's new state (and attributes): the change, for `_apply`."""
        old, old_attrs = self._state.get(entity), self._attrs.get(entity, {})
        self._state[entity], self._attrs[entity] = new, old_attrs if attrs is None else attrs
        if new != old:
            self._changed[entity] = self.session.clock.now()
        return entity, old, new, old_attrs, self._attrs[entity]

    def _apply(self, changes: list) -> None:
        changes = [c for c in changes if c[1] != c[2] or c[3] != c[4]]
        for entity, _, new, _, attrs in changes:
            for h in self._held.get(entity, ()):
                h.alive = h.alive and h.still(new, attrs)
            self._held[entity] = [h for h in self._held.get(entity, ()) if h.alive]
        for change in changes:
            entity = change[0]
            for a in self.automations:
                for t in a.triggers:
                    if entity in t.get("entities", ()) and self._matches(t, change, id(a)):
                        self._triggered(t, change, lambda why, tv, a=a: self._fire(a, why, tv))
            for w in list(self._waits):
                for t in w.triggers:
                    if w.active and entity in t.get("entities", ()) and self._matches(t, change, id(w)):
                        self._triggered(t, change, w.done)
        if changes and self._watches:
            self._watches = [w for w in self._watches if w.active]
            for w in list(self._watches):
                info = w.info
                if info is not None and (info.all_states or any(
                        c[0] in info.entities or c[0].split(".")[0] in info.domains for c in changes)):
                    self._render_watch(w, changes[-1])

    @staticmethod
    def _value(t: dict, state, attrs: dict):
        """What a state or numeric_state trigger or condition looks at: the state, or its `attribute`."""
        return state if t.get("attribute") is None else (attrs or {}).get(t["attribute"])

    def _arm(self, owner: int, triggers: list) -> None:
        """Home Assistant's numeric_state trigger at setup: each (trigger, entity) out of range is armed."""
        armed = self._armed.setdefault(owner, set())
        for t in triggers:
            if t["kind"] == "numeric_state":
                for entity in t["entities"]:
                    if not self._in_range(t, self._value(t, self._state.get(entity), self._attrs.get(entity))):
                        armed.add((id(t), entity))

    def _matches(self, t: dict, change: tuple, owner: int) -> bool:
        """Home Assistant's state trigger (an attribute trigger ignores the other attributes' changes; with none of
        to/from/not_to/not_from any change fires, an attribute's too, else the value must change) and numeric_state
        trigger (armed per entity while out of range, it fires when the value is in range and it was armed, so a
        threshold entity that moved counts when the entity next changes)."""
        entity, old, new, old_attrs, new_attrs = change
        before, after = self._value(t, old, old_attrs), self._value(t, new, new_attrs)
        if t["kind"] == "numeric_state":
            armed, key = self._armed.setdefault(owner, set()), (id(t), entity)
            if not self._in_range(t, after):
                armed.add(key)
                return False
            if key in armed:
                armed.discard(key)
                return True
            return False
        if t.get("attribute") is not None and before == after:
            return False
        if not t["match_all"] and before == after:
            return False
        return ((t["to"] is None or after in t["to"]) and (t["from"] is None or before in t["from"])
                and (t["not_to"] is None or after not in t["not_to"])
                and (t["not_from"] is None or before not in t["not_from"]))

    def _threshold(self, value) -> float | None:
        if not isinstance(value, dict):
            return value
        try:  # a threshold entity's state
            return float(self._state.get(value["entity"]))
        except (TypeError, ValueError):
            return None

    def _in_range(self, t: dict, state) -> bool:
        try:
            v = float(state)
        except (TypeError, ValueError):
            return False
        above, below = self._threshold(t.get("above")), self._threshold(t.get("below"))
        if (t.get("above") is not None and above is None) or (t.get("below") is not None and below is None):
            return False  # a threshold entity without a number: Home Assistant's condition error
        return (above is None or v > above) and (below is None or v < below)

    def _triggered(self, t: dict, change: tuple, fire: Callable) -> None:
        """A trigger matched a change: `fire(why, trigger variables)` now, or once its `for:` has held (while the
        value stays what it became, in range, or, with from and no to, not what it was)."""
        entity, old, new, old_attrs, new_attrs = change
        why, clock = f"{t['kind']} {entity} {old} -> {new}", self.session.clock
        changed = self._changed.get(entity, clock.now())
        tvars = _tvars(t, entity_id=entity, from_state=self._snapshot(entity, old, old_attrs, changed),
                       to_state=self._snapshot(entity, new, new_attrs, changed), attribute=t.get("attribute"),
                       **{"for": t["for"] or None})
        if not t["for"]:
            clock.call_at(clock.now(), lambda: fire(why, tvars))
            return
        before, after = self._value(t, old, old_attrs), self._value(t, new, new_attrs)
        if t["kind"] == "numeric_state":
            still = lambda s, a: self._in_range(t, self._value(t, s, a))  # noqa: E731
        elif t["from_only"]:
            still = lambda s, a: self._value(t, s, a) != before  # noqa: E731
        else:
            still = lambda s, a: self._value(t, s, a) == after  # noqa: E731
        h = _Held(still)
        self._held.setdefault(entity, []).append(h)

        def held():
            if h.alive:
                h.alive = False
                self._held[entity] = [x for x in self._held.get(entity, ()) if x is not h]
                fire(f"{why} for {t['for']:g} s", tvars)
        clock.call_at(clock.now() + t["for"], held)

    # -- template triggers -------------------------------------------------------------------------
    def _watch(self, t: dict, fire: Callable, variables: dict, rendered: dict | None = None) -> _Watch:
        """Start a template trigger: rendered now, armed if false (Home Assistant arms it at setup only then);
        `variables` are rendered for it, or `rendered` (a wait's: its run's variables) is used as it is."""
        if rendered is None:
            rendered = {}
            self._render_variables(variables, rendered)
        w = _Watch(t, fire, rendered)
        result = self._watch_render(w)
        w.armed = not isinstance(result, tuple) and not self._truthy(result)
        self._watches.append(w)
        return w

    @staticmethod
    def _truthy(result) -> bool:
        from vesper.lab.ha_template import result_as_boolean
        return result_as_boolean(result)

    def _watch_render(self, w: _Watch):
        """Render a watched template; a failure is the result ('error', message). The result is kept, and the
        render scheduled again at the next minute if it read the time."""
        from vesper.lab.ha_template import TemplateFailed
        clock = self.session.clock
        try:
            result, w.info = self.templates.render(w.trigger["template"], w.variables)
        except TemplateFailed as e:
            result = ("error", str(e))
            w.info = self.templates.view.info
        w.rendered_at, w.last = clock.now(), result
        if w.info.time and not w.minute:  # Home Assistant re-renders a template that reads the time each minute
            w.minute = True
            since = clock.now() - self._midnight

            def minute():
                w.minute = False
                if w.active:
                    self._render_watch(w, None)
            clock.call_at(self._midnight + (since // 60 + 1) * 60, minute)
        return result

    def _render_watch(self, w: _Watch, change: tuple | None) -> None:
        """Home Assistant's template trigger listener, on a new result: a failure is logged and ignored; a change
        cancels a `for` under way; false arms it; true fires it if it was armed. A template reading all the states
        renders at most once a minute, one reading a domain at most once a second."""
        clock = self.session.clock
        limit = 60.0 if w.info.all_states else 1.0 if w.info.domains else 0.0
        if clock.now() - w.rendered_at < limit:
            if not w.pending:
                w.pending = True

                def later():
                    w.pending = False
                    if w.active:
                        self._render_watch(w, None)
                clock.call_at(w.rendered_at + limit, later)
            return
        before = w.last
        result = self._watch_render(w)
        if result == before or isinstance(result, tuple):
            return
        for alive in w.delay:
            alive[0] = False
        w.delay = []
        if not self._truthy(result):
            w.armed = True
            return
        if not w.armed:
            return
        w.armed = False
        t = w.trigger
        extra = {}
        if change is not None:
            entity, old, new, old_attrs, new_attrs = change
            changed = self._changed.get(entity, clock.now())
            extra = {"entity_id": entity, "from_state": self._snapshot(entity, old, old_attrs, changed),
                     "to_state": self._snapshot(entity, new, new_attrs, changed)}
        tvars = _tvars(t, **extra, **{"for": t["for"] or None})
        why = f"template {result}"
        if not t["for"]:
            clock.call_at(clock.now(), lambda: w.fire(why, tvars))
            return
        alive = [True]
        w.delay.append(alive)

        def held():
            if alive[0] and w.active:
                w.fire(f"{why} for {t['for']:g} s", tvars)
        clock.call_at(clock.now() + t["for"], held)

    def _fire(self, a: Automation, why: str, trigger: dict | None = None) -> None:
        context = {"trigger": trigger or {}}
        if (a.variables or a.trigger_variables) and not (
                self._render_variables(a.trigger_variables, context) and self._render_variables(a.variables, context)):
            self.session.automation_skipped(a.name, why, "a variable's template failed")
            return
        if not all(self._holds(c, context) for c in a.conditions):
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
        self._run(_Run(a, why, context, [_Frame(list(a.actions))]))

    def _holds(self, c: dict, context: dict) -> bool:
        kind = c["kind"]
        if kind == "and":
            return all(self._holds(x, context) for x in c["conditions"])
        if kind == "or":
            return any(self._holds(x, context) for x in c["conditions"])
        if kind == "not":
            return not any(self._holds(x, context) for x in c["conditions"])
        if kind == "trigger":
            return context.get("trigger", {}).get("id") in c["ids"]
        if kind == "template":  # Home Assistant: an error in the template is a failed condition
            from vesper.lab.ha_template import TemplateFailed
            try:
                return self._truthy(self.templates.render(c["template"], context)[0])
            except TemplateFailed:
                return False
        if kind == "state":  # Home Assistant: `for` is how long the state has been unchanged (last_changed)
            now = self.session.clock.now()
            each = (self._value(c, self._state.get(e), self._attrs.get(e)) in c["state"]
                    and (c["attribute"] is None or c["attribute"] in self._attrs.get(e, {}))
                    and (c["for"] is None or now - c["for"] > self._changed.get(e, now)) for e in c["entities"])
            return any(each) if c["any"] else all(each)
        if kind == "numeric_state":
            return all(self._in_range(c, self._value(c, self._state.get(e, ""), self._attrs.get(e)))
                       for e in c["entities"])
        if kind == "sun":
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
        since = self.session.clock.now() - self._midnight
        if c["weekday"] is not None and self._weekday(int(since // DAY_S)) not in c["weekday"]:
            return False  # Home Assistant: the weekday of now, whatever the window
        now = since % DAY_S
        after, before = c["after"], c["before"]
        if after is not None and before is not None and after >= before:  # across midnight; equal: all day
            return now >= after or now < before
        return (after is None or now >= after) and (before is None or now < before)

    def _run(self, run: _Run) -> None:
        """Run the steps until a delay or a wait (which resume it) or the end. Each frame is a sequence of its own
        (choose, if, sequence, a repeat's iteration): a condition step that fails ends that sequence, as Home
        Assistant's sub-scripts end, and `stop` ends the run."""
        a, clock = run.automation, self.session.clock
        while run.frames:
            f = run.frames[-1]
            if not f.steps:
                self._end_frame(run)
                continue
            x = f.steps.pop(0)
            kind = x["kind"]
            if kind == "delay":
                clock.call_at(clock.now() + x["seconds"], lambda: self._run(run))
                return
            if kind == "wait":
                self._wait(run, x)
                return
            if kind in ("stop", "fail"):  # fail: a call Home Assistant's service refuses ends the run
                run.frames.clear()
            elif kind == "check":
                if not self._holds(x["condition"], run.context):
                    self._end_frame(run)
            elif kind == "choose":
                taken = next((k for k, (conditions, _) in enumerate(x["options"])
                              if all(self._holds(c, run.context) for c in conditions)), -1)
                steps = x["options"][taken][1] if taken >= 0 else x["default"]
                note = getattr(self.session, "automation_branch", None)  # a session that records branches
                if note is not None and "n" in x:
                    note(a.name, x["n"], taken)
                run.frames.append(_Frame(list(steps)))
            elif kind == "sequence":
                if x.get("group"):  # one action that is several calls (homeassistant.turn_on): not a sequence
                    f.steps[:0] = x["steps"]
                else:
                    run.frames.append(_Frame(list(x["steps"])))
            elif kind == "repeat":
                self._iterate(run, x, 1)
            elif kind == "variables":  # a template that fails stops the run, as in Home Assistant
                if not self._render_variables(x["variables"], run.context):
                    run.frames.clear()
            elif kind == "dynamic":
                step = self._resolved(x, run.context)
                if step is None:  # Home Assistant: a template error or an unknown service stops the run
                    run.frames.clear()
                else:
                    f.steps.insert(0, step)
            elif kind == "call" and x.get("data") is not None:  # light data rendered now
                step = self._resolved_data(x, run.context)
                if step is None:  # a value the light service refuses stops the run, as in Home Assistant
                    run.frames.clear()
                else:
                    f.steps.insert(0, step)
            elif kind == "record":  # no twin: the session writes down the call and sends nothing
                for target in x["targets"] or [""]:  # an action that names no entity (notify.notify) is kept too
                    self.session.automation_command(a.name, run.why, x["service"], target, "", "", None)
            elif kind == "climate":
                for entity_id in x["entities"]:
                    for feature, value in x["commands"][entity_id]:
                        self.session.automation_command(a.name, run.why, x["service"], entity_id,
                                                        self.entities[entity_id].twin_id, feature, value)
                        if feature == "system_mode" and self.entities[entity_id].optimistic:
                            self._modes[entity_id] = value  # an optimistic climate takes the mode it was sent
                            self._apply([self._set(entity_id, value)])
            else:
                for entity_id in x["entities"]:
                    self._call(a, x, self.entities[entity_id], run.why)
        a.running = False

    def _resolved(self, x: dict, context: dict) -> dict | None:
        """A templated service's step, now: the rendered service as written out, or None if it cannot run."""
        from vesper.lab.ha_template import TemplateFailed
        notes, self._notes = self._notes, []
        try:
            name = self.templates.render(x["template"], context)[0]
            return self._action({**x["rest"], "action": name})
        except (TemplateFailed, AutomationError):
            return None
        finally:
            self._notes = notes

    def _resolved_data(self, x: dict, context: dict) -> dict | None:
        from vesper.lab.ha_template import TemplateFailed
        notes, self._notes = self._notes, []
        try:
            data = self._rendered(x["data"], context)
            brightness = self._light_data(x["service"], x["entities"], data, x["where"])
        except (TemplateFailed, AutomationError):
            return None
        finally:
            self._notes = notes
        return {k: v for k, v in x.items() if k != "data"} | {"brightness": brightness}

    def _end_frame(self, run: _Run) -> None:
        f = run.frames.pop()
        if f.repeat is not None:
            self._iterate(run, f.repeat, f.index + 1)

    def _iterate(self, run: _Run, x: dict, index: int) -> None:
        """Start iteration `index` (from 1) of a repeat if its loop says so: count/for_each up to the count, while
        its conditions hold (checked before), until they hold (checked after the previous iteration)."""
        if x.get("count_template") is not None and index == 1:  # rendered when the repeat starts
            from vesper.lab.ha_template import TemplateFailed, parse_result
            try:
                count = parse_result(self.templates.render(x["count_template"], run.context)[0])
                run.counts[id(x)] = int(_coerced(count, "repeat count"))
            except (TemplateFailed, AutomationError):
                run.frames.clear()  # Home Assistant: the run stops with an error
                return
        count = run.counts.get(id(x), x.get("count"))
        if x["loop"] in ("count", "for_each"):
            go = index <= count
        elif index > REPEAT_LIMIT:
            run.frames.clear()  # Home Assistant aborts the script
            return
        elif x["loop"] == "while":
            go = all(self._holds(c, run.context) for c in x["conditions"])
        else:
            go = index == 1 or not all(self._holds(c, run.context) for c in x["conditions"])
        if go:
            run.context["repeat"] = {"index": index, "first": index == 1}
            if x["loop"] in ("count", "for_each"):
                run.context["repeat"]["last"] = index == count
            if x["loop"] == "for_each":
                run.context["repeat"]["item"] = x["items"][index - 1]
            run.frames.append(_Frame(list(x["steps"]), x, index))

    def _wait(self, run: _Run, x: dict) -> None:
        """wait_for_trigger: the run resumes when one of the triggers fires (`wait.trigger`), or at the timeout
        (then it ends unless continue_on_timeout); with no timeout it waits as long as it takes."""
        clock = self.session.clock
        w = _Wait(x["triggers"])

        watches = []

        def end():
            w.active = False
            self._waits.remove(w)
            self._armed.pop(id(w), None)
            for watch in watches:
                watch.active = False

        def done(why, tvars):
            if w.active:
                end()
                run.context["wait"] = {"trigger": tvars, "remaining": None}
                self._run(run)
        w.done = done
        self._waits.append(w)
        self._arm(id(w), x["triggers"])
        for t in x["triggers"]:
            when = self._next_time(t)
            if when is not None:
                clock.call_at(when, lambda t=t: done(f"{t['kind']} (wait)", _tvars(t, now=self._now())))
            if t["kind"] == "template":
                watches.append(self._watch(t, done, {}, rendered=run.context))
        if x["timeout"] is not None:
            def timeout():
                if w.active:
                    end()
                    run.context["wait"] = {"trigger": None, "remaining": 0}
                    if not x["continue"]:
                        run.frames.clear()
                    self._run(run)
            clock.call_at(clock.now() + x["timeout"], timeout)

    def _next_time(self, t: dict) -> float | None:
        """When a time or sun trigger next fires after now (None for the other triggers)."""
        now = self.session.clock.now()
        since = now - self._midnight
        day = int(since // DAY_S)
        if t["kind"] == "time":
            return min((self._midnight + d * DAY_S + at for d in range(day, day + 8) for at in t["at"]
                        if self._midnight + d * DAY_S + at > now
                        and (t["weekday"] is None or self._weekday(d) in t["weekday"])), default=None)
        if t["kind"] == "time_pattern":
            return self._next_pattern(t, now)
        if t["kind"] == "sun":
            for d in (day, day + 1, day + 2):
                ev = self._sun_events(d)[t["event"]]
                if ev is not None and self._midnight + ev + t["offset"] > now:
                    return self._midnight + ev + t["offset"]
        return None

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


def _variable_names(node) -> list[str]:
    """Every variable an automation defines (its variables, trigger_variables and variables steps)."""
    out = []
    if isinstance(node, dict):
        for key in ("variables", "trigger_variables"):
            if isinstance(node.get(key), dict):
                out += [k for k in node[key] if isinstance(k, str)]
        for v in node.values():
            out += _variable_names(v)
    elif isinstance(node, list):
        for v in node:
            out += _variable_names(v)
    return out


def _pattern(value, maximum: int, where: str) -> set[int]:
    """One field of a time_pattern, as Home Assistant's TimePattern takes it: '*' (any), '/n' (multiples of n) or a
    number (a numeric string too), 0..maximum."""
    try:
        if value == "*":
            return set(range(maximum + 1))
        if isinstance(value, str) and value.startswith("/"):
            n = int(value[1:])
            if not 0 < n <= maximum:
                raise ValueError
            return {v for v in range(maximum + 1) if v % n == 0}
        if isinstance(value, bool):
            raise ValueError
        n = int(value)
        if not 0 <= n <= maximum or (isinstance(value, float) and value != n):
            raise ValueError
        return {n}
    except (TypeError, ValueError):
        raise AutomationError(f"{where}: {value!r} is not a time pattern") from None


def _event_class(event_type) -> str:
    """What fires an event of this type, for a refusal that can be counted: a device, a phone app, a messaging or web
    service, Home Assistant itself, or another automation or script."""
    types = [str(x) for x in (event_type if isinstance(event_type, list) else [event_type])]
    for prefixes, what in ((DEVICE_EVENTS, "a device's button or remote event"), (APP_EVENTS, "a phone app's event"),
                           (SERVICE_EVENTS, "a messaging or web service's event"),
                           (INTERNAL_EVENTS, "a Home Assistant internal event")):
        if any(x.startswith(prefixes) for x in types):
            return what
    return "a custom event another automation or script fires"


DESCRIPTIONS = {"time": "time", "time_pattern": "time pattern", "homeassistant": "Home Assistant starting",
                "template": "template"}


def _tvars(t: dict, **extra) -> dict:
    """Home Assistant's `trigger` variables of a trigger that fired: its platform, id, index and description, and
    what else it says (an entity's from_state/to_state, a sun event, the time `now`)."""
    kind = t["kind"]
    if kind in ("state", "numeric_state"):
        description = f"{'numeric ' if kind == 'numeric_state' else ''}state of {extra.get('entity_id')}"
    elif kind == "sun":
        description = f"{t['event']} with offset"
    else:
        description = DESCRIPTIONS.get(kind, kind)
    return {"platform": kind, "id": t["id"], "idx": t["idx"], "description": description, **extra}


def _list(value, where: str) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise AutomationError(f"{where}: a list")
    return value
