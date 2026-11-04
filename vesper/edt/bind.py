"""The binder: a public Home Assistant automation names its author's devices; this rewrites every entity id onto the
standard EDT home's devices, by role.

A role is what the entity measures or does (illuminance, motion, light, ...) and a room is where its name says it is.
Both are read from the entity id alone, so they are guesses: the domain says what kind of thing it is, and the words
of its name pick the role among those the domain allows (ROLE_PRIORITY orders the readings: `sensor.temp_lux` is an
illuminance sensor). An outdoor name (garden, patio, ...) or a weather service's sensor is on the porch, the home's
outdoor room. An entity id that names no role, a role the home has no device for, a device or area handle that cannot
be resolved, or an entity the experiment does not model makes the whole automation `Unbindable`: a half-bound
automation would test something else.

Entity ids are found where an automation puts them: under `entity_id` (one id, a comma-separated string or a list),
under `above`/`below` (a numeric_state compared against an entity), and inside templates, quoted (`states('x.y')`) or
as attribute access (`states.x.y.state`).

Handles: an action's `area_id` target becomes the room's device of the action's domain (`light.turn_on` on the
kitchen: the kitchen's light); a `device_id` beside an `entity_id` is dropped (the entity says what is meant); a device
trigger, condition or action (`domain` + `type`) becomes the state, numeric_state or service step Home Assistant's
device automation stands for, on its entity, or on the role its type names (an opaque registry id names no room: an
actuator's or motion/door step takes the living room's, a sensor's is refused).

Precision over yield (protocol A2.5): semi-outdoor sensors, forecasts, statistics and non-ambient temperatures, a
temperature threshold outside -30..45, and two distinct names bound onto one home entity are refused.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

ROLE_PRIORITY = ("illuminance", "temperature", "humidity", "motion", "door", "light", "switch", "lock", "climate",
                 "cover")
VARIANT_ROLES = ("illuminance", "temperature", "humidity")  # the roles several products answer; the rest use variant 0
DOMAINS = {  # the domains each role's entity can have
    "illuminance": ("sensor",), "temperature": ("sensor",), "humidity": ("sensor",),
    "motion": ("binary_sensor",), "door": ("binary_sensor",), "light": ("light",), "switch": ("switch",),
    "lock": ("lock",), "climate": ("climate",), "cover": ("cover",),
}
WORDS = {  # the name tokens that say a sensor's role (an actuator's domain says its own); a few in other languages
    "illuminance": {"lux", "illuminance", "illumination", "luminosity", "luminance", "lightlevel", "brightness",
                    "light", "helligkeit", "luminosite", "helderheid"},
    "temperature": {"temp", "temperature", "temperatur", "temperatuur", "temperatura"},
    "humidity": {"humidity", "humid", "rh", "luftfeuchtigkeit", "luftfeuchte", "feuchtigkeit", "feuchte", "humidite",
                 "humedad", "umidita", "vochtigheid", "luchtvochtigheid", "fuktighet", "luftfuktighet"},
    "motion": {"motion", "occupancy", "presence", "pir", "movement", "bewegung", "beweging", "mouvement",
               "movimiento", "movimento"},
    "door": {"door", "window", "contact", "opening", "tuer", "deur", "porte", "puerta", "porta", "fenster", "fenetre",
             "ventana", "finestra", "raam"},
}
ROLE_WORDS = set().union(*WORDS.values())
OTHER_MEASURE = {"power", "energy", "current", "voltage", "watt", "watts", "kwh", "battery", "signal", "linkquality",
                 "rssi", "status", "state", "dew", "dewpoint", "absolute", "soil", "moisture", "target", "setpoint",
                 "forecast", "feels", "apparent",
                 # forecasts, statistics, derived values
                 "high", "low", "max", "min", "lowest", "highest", "mean", "average", "avg", "daily", "overnight",
                 "today", "tomorrow", "realfeel", "feel", "chill", "windchill", "trend",
                 # non-ambient temperatures
                 "tool", "nozzle", "hotend", "printer", "octoprint", "cpu", "gpu", "disk", "nvme", "water", "aquarium",
                 "pool", "fridge", "freezer", "boiler", "pipe"}  # a sensor with one of these measures something the
# home has no role for (`current` before a role word is the adjective: current_temperature)
OTHER_MEASURE_TOKEN = re.compile(r"^(\d+[dh]|tool\d+)$")  # a forecast's horizon (0d, 1d, 12h), a printer's tool0
ROOMS = (  # (name token prefix, room id): the first that matches; no match is the living room
    ("living", "living"), ("lounge", "living"), ("kitchen", "kitchen"), ("bedroom", "bedroom_1"), ("bath", "bathroom"),
    ("hall", "corridor"), ("corridor", "corridor"), ("office", "office"), ("study", "office"),
)
OUTDOOR_ROOM = "porch"  # the home's outdoor room (vesper.edt.home.OUTDOOR_ROOM)
BEDROOM_2 = re.compile(r"(?<![a-z])(bed|bedroom)_?(2|two)(?![0-9a-z])|(?<![a-z])guest")
OUTDOOR = ("outdoor", "outside", "garden", "garage", "porch", "balcony", "patio", "driveway", "terrace", "carport",
           "shed", "pool", "veranda", "greenhouse", "lawn", "aussen", "buiten", "exterior", "exterieur", "garten",
           "jardin", "terrass", "balkon")  # name-token prefixes
OUTDOOR_TOKENS = {"deck"}  # whole tokens only (German Decke, Deckenlampe: a ceiling)
SEMI_OUTDOOR = ("garage", "shed", "pool", "carport", "veranda", "greenhouse")  # name-token prefixes: their sensors
# do not read the outdoor air or light (refused); their actuators bind to the porch's
SENSOR_ROLES = ("illuminance", "temperature", "humidity", "motion", "door")
AMBIENT_C = (-30, 45)  # a temperature threshold outside this looks Fahrenheit or non-ambient
OUTDOOR_ANYWHERE = ("yard",)  # and inside a token (backyard, courtyard)
WEATHER_SERVICES = ("weather", "openweathermap", "owm", "darksky", "pirateweather", "accuweather", "buienradar",
                    "wetter", "meteo", "weatherbit", "climacell", "wunderground")  # name-token prefixes: outdoors
TIME_WORDS = {"time", "date", "utc", "iso", "beat", "internet", "local"}  # `sensor.time`, `sensor.date_time`
SUN_SENSOR_WORDS = {"next", "solar", "elevation", "azimuth", "rising", "setting", "dawn", "dusk", "noon", "midnight",
                    "adjusted"}  # the sun integration's sensors: sensor.sun_next_rising, sensor.sun_solar_elevation
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
QUOTED = re.compile(r"""(['"])([a-z_]+\.[a-z0-9_]+)\1""")  # an entity id inside a template string
STATES = re.compile(r"\bstates\.([a-z_]+)\.([a-z0-9_]+)")  # `states.light.kitchen.state` in a template
BOUND_DOMAINS = {d for ds in DOMAINS.values() for d in ds} | {"weather", "sun"}
UNMODELLED_DOMAINS = {  # domains of entities the standard home does not have (a quoted one in a template is one)
    "person", "group", "input_boolean", "input_number", "input_select", "input_text", "input_datetime", "input_button",
    "device_tracker", "zone", "scene", "script", "automation", "alarm_control_panel", "media_player", "fan", "vacuum",
    "camera", "timer", "counter", "calendar", "water_heater", "humidifier", "number", "select", "button", "update",
    "remote", "siren", "valve", "lawn_mower", "event", "text", "schedule", "todo", "tts", "notify", "image",
    "alert", "proximity", "air_quality", "geo_location", "plant", "conversation", "persistent_notification",
    "assist_satellite", "image_processing", "stt", "wake_word", "ai_task", "date", "time", "datetime",
    "mailbox", "logbook", "system_log", "recorder", "zwave_js", "zha", "mqtt",
}
SERVICES = {"turn_on", "turn_off", "toggle", "lock", "unlock", "open", "close", "open_cover", "close_cover",
            "set_temperature", "set_hvac_mode", "set_humidity"}  # a quoted `light.turn_on` is a service, not an entity
HANDLES = ("device_id", "area_id")
MODELLED_ELSEWHERE = {"sun.sun"}  # the engine provides it at the home's site
AREA_ROLES = {"light": "light", "switch": "switch", "lock": "lock", "climate": "climate", "cover": "cover"}
REMOTE_DOMAINS = {"mqtt", "zha", "deconz", "hue", "tradfri", "lutron_caseta", "homekit_controller", "shelly",
                  "zwave_js", "tasmota", "esphome", "xiaomi_aqara"}  # integrations whose device triggers are buttons
REMOTE_TYPES = ("remote_button", "button", "action", "click", "press")  # type prefixes of a button's device trigger
NUMERIC = "numeric"  # a device step's value: compared with above/below
# Home Assistant's device automations: (domain, type) -> (role, state). Role None: the entity's name says it.
DEVICE_TRIGGERS = {
    **{(d, t): (d, s) for d in ("light", "switch") for t, s in (("turned_on", "on"), ("turned_off", "off"),
                                                                ("changed_states", None))},
    **{("binary_sensor", t): (r, s) for t, r, s in (
        ("motion", "motion", "on"), ("no_motion", "motion", "off"), ("occupied", "motion", "on"),
        ("not_occupied", "motion", "off"), ("present", "motion", "on"), ("not_present", "motion", "off"),
        ("opened", "door", "on"), ("not_opened", "door", "off"), ("turned_on", None, "on"),
        ("turned_off", None, "off"))},
    ("lock", "locked"): ("lock", "locked"), ("lock", "unlocked"): ("lock", "unlocked"),
    **{("sensor", t): (t, NUMERIC) for t in ("temperature", "humidity", "illuminance")},
    ("sensor", "value"): (None, NUMERIC),
}
DEVICE_CONDITIONS = {
    **{(d, t): (d, s) for d in ("light", "switch") for t, s in (("is_on", "on"), ("is_off", "off"))},
    **{("binary_sensor", t): (r, s) for t, r, s in (
        ("is_motion", "motion", "on"), ("is_no_motion", "motion", "off"), ("is_occupied", "motion", "on"),
        ("is_not_occupied", "motion", "off"), ("is_present", "motion", "on"), ("is_not_present", "motion", "off"),
        ("is_open", "door", "on"), ("is_not_open", "door", "off"), ("is_on", None, "on"), ("is_off", None, "off"))},
    ("lock", "is_locked"): ("lock", "locked"), ("lock", "is_unlocked"): ("lock", "unlocked"),
    **{("sensor", f"is_{t}"): (t, NUMERIC) for t in ("temperature", "humidity", "illuminance")},
    ("sensor", "is_value"): (None, NUMERIC),
}
DEVICE_ACTIONS = {  # (domain, type) -> (role, service)
    **{(d, t): (d, t) for d in ("light", "switch") for t in ("turn_on", "turn_off", "toggle")},
    **{("lock", t): ("lock", t) for t in ("lock", "unlock", "open")},
    ("climate", "set_hvac_mode"): ("climate", "set_hvac_mode"),
}
DEVICE_STEP_KEYS = {"device_id", "domain", "entity_id", "type", "trigger", "platform", "condition", "metadata",
                    "subtype"}  # what a device step's translation consumes; the rest passes on
STEP_KEYS = {"id", "alias", "enabled", "for", "continue_on_error", "variables", "above", "below"}  # kept on the step


@dataclass(frozen=True)
class Unbindable:
    reason: str


def _outdoor(tokens) -> bool:
    return any(t.startswith(prefix) for t in tokens for prefix in OUTDOOR) or bool(OUTDOOR_TOKENS & set(tokens)) or \
        any(s in t for t in tokens for s in OUTDOOR_ANYWHERE) or _weather_service(tokens)


def _semi_outdoor(entity_id: str) -> bool:
    """A sensor of a garage, shed, pool, carport, veranda or greenhouse (protocol A2.5)."""
    domain, _, name = entity_id.partition(".")
    return domain in ("sensor", "binary_sensor") and any(
        t.startswith(p) for t in name.split("_") for p in SEMI_OUTDOOR)


def _weather_service(tokens) -> bool:
    name = "_".join(tokens)
    return any(t.startswith(p) for t in tokens for p in WEATHER_SERVICES) or "dark_sky" in name or \
        "pirate_weather" in name


def _other_measure(tokens) -> bool:
    other = (OTHER_MEASURE & set(tokens)) | {t for t in tokens if OTHER_MEASURE_TOKEN.match(t)}
    if "current" in other and all(i + 1 < len(tokens) and tokens[i + 1] in ROLE_WORDS
                                  for i, t in enumerate(tokens) if t == "current"):
        other.discard("current")
    return bool(other)


def _room(tokens, default: str | None = "living") -> str | None:
    """The room a name's tokens say: the porch if outdoors, else ROOMS' first match, else `default`."""
    if _outdoor(tokens):
        return OUTDOOR_ROOM
    room = next((rid for prefix, rid in ROOMS if any(t.startswith(prefix) for t in tokens)), None)
    if room in (None, "bedroom_1") and BEDROOM_2.search("_".join(tokens)):
        room = "bedroom_2"
    return room or default


def infer_role(entity_id: str) -> tuple[str, str] | None:
    """(role, room) an entity id stands for (room "porch" outdoors), or None if its name says no role."""
    domain, _, name = entity_id.partition(".")
    tokens = name.split("_")
    role = None
    for r in ROLE_PRIORITY:
        if domain not in DOMAINS[r]:
            continue
        if r in WORDS and _other_measure(tokens):  # `light_power`, `motion_battery_low` are not the role
            continue
        if r not in WORDS or WORDS[r] & set(tokens):
            role = r
            break
    if role is None:
        return None
    return role, _room(tokens)


def _no_role(entity_id: str) -> Unbindable:
    """Why an entity id's name names no role: time, sun and weather service sensors have their own reasons."""
    domain, _, name = entity_id.partition(".")
    tokens = name.split("_")
    if domain == "sensor" and set(tokens) <= TIME_WORDS and {"time", "date"} & set(tokens):
        return Unbindable("time sensors are not modelled; use time triggers")
    if domain == "sensor" and tokens[0] == "sun" and len(tokens) > 1 and tokens[1] in SUN_SENSOR_WORDS:
        return Unbindable("sun sensors are not modelled; use sun.sun")
    if domain in ("sensor", "binary_sensor") and _weather_service(tokens):
        return Unbindable("weather service sensors other than temperature, humidity and illuminance are not modelled")
    return Unbindable(f"{entity_id}: its name names no role the standard home has")


_SOURCES: dict[str, list[str]] | None = None  # during a bind: home entity -> the automation's names bound onto it


def _lookup(role: str, room: str, roles: dict, variant: int, what: str, source: str | None = None) -> str | Unbindable:
    if role in VARIANT_ROLES and variant and (role, room, variant) not in roles:
        return Unbindable(f"no variant {variant} for {role} (the home has one product per sensor role)")
    bound = roles.get((role, room, variant if role in VARIANT_ROLES else 0))
    if bound is None:
        return Unbindable(f"the standard home has no {role} in the {room} (for {what})")
    if _SOURCES is not None:
        names = _SOURCES.setdefault(bound, [])
        if (source or what) not in names:
            names.append(source or what)
    return bound


def _roles_for(entity_id: str, roles: dict, variant: int) -> str | Unbindable:
    if entity_id in MODELLED_ELSEWHERE:
        return entity_id
    domain = entity_id.partition(".")[0]
    if domain == "weather":
        return Unbindable("weather entities are not modelled")
    if domain not in BOUND_DOMAINS:
        return Unbindable(f"{entity_id}: {domain} entities are not modelled")
    if _semi_outdoor(entity_id):
        return Unbindable(f"semi-outdoor spaces are not modelled (for {entity_id})")
    found = infer_role(entity_id)
    if found is None:
        return _no_role(entity_id)
    return _lookup(*found, roles, variant, entity_id)


def _in_template(entity_id: str, roles: dict, variant: int) -> str | Unbindable:
    """What a template's mention of `entity_id` becomes: services, sun and strings that are no entity stay."""
    domain, _, name = entity_id.partition(".")
    if entity_id in MODELLED_ELSEWHERE or name in SERVICES:
        return entity_id
    if domain in BOUND_DOMAINS:
        return _roles_for(entity_id, roles, variant)
    if domain in UNMODELLED_DOMAINS:
        return Unbindable(f"{entity_id}: {domain} entities are not modelled")
    return entity_id


def _template(text: str, roles: dict, variant: int) -> str | Unbindable:
    failed: list[Unbindable] = []

    def quoted(m):
        r = _in_template(m.group(2), roles, variant)
        if isinstance(r, Unbindable):
            failed.append(r)
            return m.group(0)
        return m.group(1) + r + m.group(1)

    def attribute(m):
        r = _in_template(f"{m.group(1)}.{m.group(2)}", roles, variant)
        if isinstance(r, Unbindable):
            failed.append(r)
            return m.group(0)
        return f"states.{r}"

    out = STATES.sub(attribute, QUOTED.sub(quoted, text))
    return failed[0] if failed else out


def _reference(text: str, roles: dict, variant: int, strict: bool) -> str | Unbindable:
    """A string under `entity_id` (strict) or `above`/`below` (not strict: a number or a template is no entity)."""
    s = text.strip()
    if "{{" in s or "{%" in s:
        return _template(text, roles, variant)
    if strict and s in ("all", "none"):
        return text
    if strict and s.startswith("!"):  # the corpus loader keeps a local tag as "!tag value"
        tag = s.split()[0]
        return Unbindable("blueprint inputs are not expanded" if tag == "!input" else
                          f"YAML tags are not resolved (for {tag})")
    parts = [p.strip().lower() if strict else p.strip() for p in s.split(",")]  # cv.entity_id lowercases
    if all(ENTITY.match(p) for p in parts):
        bound = [_roles_for(p, roles, variant) for p in parts]
        failed = next((b for b in bound if isinstance(b, Unbindable)), None)
        return failed if failed else ", ".join(bound)
    if strict:
        return Unbindable(f"entity_id {text!r} is not an entity id")
    return text


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _listed(value) -> list:
    return value if isinstance(value, list) else [value]


def _area_room(area) -> str | None:
    """The room an area's name says, or None (an area the home has no room for, or a registry id)."""
    if not isinstance(area, str):
        return None
    tokens = [t for t in re.split(r"[^a-z0-9]+", area.lower()) if t]
    return _room(tokens, None) if tokens else None


def _target(node: dict, roles: dict, variant: int) -> dict | Unbindable | None:
    """The bound target of an action whose target (or the action itself, the old style) names an area or a device;
    None if it names neither."""
    target = node.get("target")
    if target is not None and not isinstance(target, dict):
        return None
    target = dict(target or {})
    for k in HANDLES + ("entity_id",):  # the old style: beside the action
        if k in node and not (k == "entity_id" and "target" in node):
            target[k] = node[k]
    target = {k: v for k, v in target.items() if not (k in HANDLES and _empty(v))}
    if not any(k in target for k in HANDLES):
        return None
    areas = _listed(target.pop("area_id", []))
    devices = _listed(target.pop("device_id", []))
    entities = target.get("entity_id")
    if devices and _empty(entities):
        return Unbindable("device_id targets name no entity")
    if not _empty(entities):
        entities = _rewrite(entities, roles, variant, "entity_id")
        if isinstance(entities, Unbindable):
            return entities
    if areas:
        domain = str(node.get("action") or node.get("service")).partition(".")[0]
        role = AREA_ROLES.get(domain)
        if role is None:
            return Unbindable(f"area targets of {domain} actions are not modelled")
        found = []
        for area in areas:
            room = _area_room(area)
            if room is None:
                return Unbindable(f"no room of the standard home for the area (for {area})")
            bound = _lookup(role, room, roles, 0, f"area {area}")
            if isinstance(bound, Unbindable):
                return bound
            found.append(bound)
        entities = ([] if _empty(entities) else _listed(entities)) + found
    out = {k: v for k, v in target.items() if k != "entity_id"}
    out["entity_id"] = entities
    return out


def _device_entity(node: dict, domain: str, role: str | None, roles: dict, variant: int, what: str):
    """The home entity a device step acts on: its entity id's binding, else the role its type names (in the room its
    entity id's name says). An opaque registry id names no room: an actuator's or a motion/door step binds in the
    living room, a sensor's is refused. None: neither says a role."""
    eid = node.get("entity_id")
    if isinstance(eid, str) and ENTITY.match(eid):
        if infer_role(eid) is not None or role is None or eid.partition(".")[0] not in BOUND_DOMAINS \
                or _semi_outdoor(eid):
            return _roles_for(eid, roles, variant)
        tokens = eid.partition(".")[2].split("_")
        if _other_measure(tokens):
            return _roles_for(eid, roles, variant)
        return _lookup(role, _room(tokens), roles, variant, what, eid)
    if role is None:
        return None
    if role in VARIANT_ROLES:
        return Unbindable("device steps on a registry id name no room")
    return _lookup(role, "living", roles, variant, what, f"registry id {eid}")


def _device_step(node: dict, roles: dict, variant: int) -> dict | Unbindable:
    """A device trigger, condition or action as the entity step it stands for, on the home's entity."""
    kind = "trigger" if "device" in (node.get("trigger"), node.get("platform")) else \
        "condition" if node.get("condition") == "device" else "action"
    domain, typ = node.get("domain"), node.get("type")
    if kind == "trigger" and ("subtype" in node or not domain or not typ or domain in REMOTE_DOMAINS
                              or str(typ).startswith(REMOTE_TYPES)):
        return Unbindable("device triggers of remotes are not modelled")
    if not domain or not typ:
        return Unbindable(f"device {kind}s with no domain or type are not modelled")
    if domain in UNMODELLED_DOMAINS or domain == "weather":
        return Unbindable(f"{domain} entities are not modelled")
    table = {"trigger": DEVICE_TRIGGERS, "condition": DEVICE_CONDITIONS, "action": DEVICE_ACTIONS}[kind]
    spec = table.get((domain, typ))
    what = f"device {domain} {typ}"
    entity = None if spec is None else _device_entity(node, domain, spec[0], roles, variant, what)
    if entity is None:
        return Unbindable(f"{domain} device {kind}s are not modelled (for {typ})")
    if isinstance(entity, Unbindable):
        return entity
    step = _translated(node, kind, domain, spec, entity)
    return _ambient(step, roles) or step


def _translated(node: dict, kind: str, domain: str, spec: tuple, entity: str) -> dict:
    kept = {k: v for k, v in node.items() if k in STEP_KEYS}
    if kind == "action":
        data = {k: v for k, v in node.items() if k not in DEVICE_STEP_KEYS | STEP_KEYS}
        out = {"action": f"{domain}.{spec[1]}", "target": {"entity_id": entity}}
        return {**out, **({"data": data} if data else {}), **kept}
    value = spec[1]
    if kind == "trigger":
        key = "platform" if "platform" in node else "trigger"
        head = {key: "numeric_state" if value == NUMERIC else "state", "entity_id": entity}
        return {**head, **({"to": value} if value != NUMERIC else {}), **kept}  # changed_states: to null
    head = {"condition": "numeric_state" if value == NUMERIC else "state", "entity_id": entity}
    return {**head, **({"state": value} if value != NUMERIC else {}), **kept}


SETTABLE = {"input_boolean", "input_select", "input_number", "group", "person", "device_tracker", "zone", "timer",
            "counter", "input_text", "input_datetime", "input_button"}  # helpers an action may set (protocol A2.4)


def _plain_ids(value, domain: str) -> list[str] | None:
    """The ids `value` names if it names plain (not templated) ids of `domain` only, else None."""
    out = []
    for v in value if isinstance(value, list) else [value]:
        if not isinstance(v, str) or "{{" in v or "{%" in v:
            return None
        parts = [p.strip().lower() for p in v.split(",")]
        if not all(ENTITY.match(p) and p.partition(".")[0] == domain for p in parts):
            return None
        out += parts
    return out or None


def _set_slots(node: dict) -> dict[str, list[str]]:
    """Where an action of a helper domain's own service names the helpers it sets (`entity_id` beside the action, in
    its `target` or its `data`): slot -> the ids. A helper set this way passes through unbound (protocol A2.4)."""
    service = node.get("action") or node.get("service")
    if not isinstance(service, str) or service.partition(".")[0] not in SETTABLE or any(k in node for k in HANDLES):
        return {}
    domain = service.partition(".")[0]
    out = {}
    for slot in ("entity_id", "target", "data"):
        holder = node if slot == "entity_id" else node.get(slot)
        if isinstance(holder, dict) and "entity_id" in holder:
            ids = _plain_ids(holder["entity_id"], domain)
            if ids:
                out[slot] = ids
    return out


def _ambient(step: dict, roles: dict) -> Unbindable | None:
    """A numeric_state step on a temperature whose threshold lies outside AMBIENT_C (Fahrenheit, or no room air)."""
    kind = step.get("trigger") or step.get("platform") or step.get("condition")
    if kind != "numeric_state" or "attribute" in step or "value_template" in step:
        return None
    temps = {v for (role, _, _), v in roles.items() if role == "temperature"}
    eids = [p.strip() for e in _listed(step.get("entity_id")) if isinstance(e, str) for p in e.split(",")]
    if not temps & set(eids):
        return None
    for side in ("above", "below"):
        n = _number(step.get(side))
        if n is not None and not AMBIENT_C[0] <= n <= AMBIENT_C[1]:
            return Unbindable("threshold looks Fahrenheit or non-ambient")
    return None


def _rewrite(node, roles: dict, variant: int, key: str | None = None):
    """The node with every entity id rewritten, or the Unbindable the first one that cannot be."""
    if isinstance(node, dict):
        node = {k: v for k, v in node.items() if not (k in HANDLES and _empty(v))}  # an empty handle names nothing
        slots = _set_slots(node)
        if slots:  # the helpers this action sets stay as written; the rest of it is bound as usual
            out = {}
            for k, v in node.items():
                if k == "entity_id" and k in slots:
                    out[k] = v
                    continue
                if k in slots:
                    inner = _rewrite({kk: vv for kk, vv in node[k].items() if kk != "entity_id"}, roles, variant, k)
                    if isinstance(inner, Unbindable):
                        return inner
                    out[k] = {**inner, "entity_id": node[k]["entity_id"]}
                    continue
                v = _rewrite(v, roles, variant, k)
                if isinstance(v, Unbindable):
                    return v
                out[k] = v
            return out
        if any(k in node for k in HANDLES) and ({"domain", "type"} <= set(node) or "device" in (
                node.get("trigger"), node.get("platform"), node.get("condition"))):
            return _device_step(node, roles, variant)
        target = None
        if isinstance(node.get("action") or node.get("service"), str):
            target = _target(node, roles, variant)
            if isinstance(target, Unbindable):
                return target
        moved = {*HANDLES, "target", *(() if "target" in node else ("entity_id",))}  # now in the bound target
        out = {}
        for k, v in node.items():
            if target is not None and k in moved:
                continue
            if k in HANDLES:
                return Unbindable("device_id/area_id not modelled")
            if k == "use_blueprint":
                return Unbindable("blueprints are not expanded")
            v = _rewrite(v, roles, variant, k)
            if isinstance(v, Unbindable):
                return v
            out[k] = v
        if target is not None:
            out["target"] = target
        return _ambient(out, roles) or out
    if isinstance(node, list):
        out = []
        for v in node:
            v = _rewrite(v, roles, variant, key)
            if isinstance(v, Unbindable):
                return v
            out.append(v)
        return out
    if isinstance(node, str):
        if key in ("action", "service"):  # a service name, not an entity
            return node
        if key in ("entity_id", "above", "below"):
            return _reference(node, roles, variant, strict=key == "entity_id")
        if "{{" in node or "{%" in node:
            return _template(node, roles, variant)
        if ENTITY.match(node) and node.partition(".")[0] in BOUND_DOMAINS | UNMODELLED_DOMAINS:
            return _in_template(node, roles, variant)  # an id anywhere else (variables, data): an entity reference
    return node


# ---- gate-open helpers (docs/experiments/edt-protocol.md, addendum 2) ------------------------------------------------

GATE_DOMAINS = {"input_boolean", "input_select", "input_number", "group", "person", "device_tracker", "zone", "timer"}
LOGIC = ("or", "not")


def _ids_in(text: str, key: str | None) -> list[str]:
    """The entity ids a string names, as the binder reads them: a template's quoted or attribute-access ids, else a
    plain id or comma list (lowercased); a service name names none."""
    if key in ("action", "service"):
        return []
    if "{{" in text or "{%" in text:
        return [m.group(2) for m in QUOTED.finditer(text)] + [f"{a}.{b}" for a, b in STATES.findall(text)]
    parts = [p.strip().lower() for p in text.split(",")]
    return parts if all(ENTITY.match(p) for p in parts) else []


def _helpers(node, key: str | None = None, logic: bool = False, template: bool = False):
    """(entity id, key it sits under, inside or/not, inside a template) for every helper or presence entity in node."""
    if isinstance(node, dict):
        inside = logic or node.get("condition") in LOGIC or any(k in node for k in LOGIC)
        for k, v in node.items():
            yield from _helpers(v, k, inside, template)
    elif isinstance(node, list):
        for v in node:
            yield from _helpers(v, key, logic, template)
    elif isinstance(node, str):
        templated = "{{" in node or "{%" in node
        for eid in _ids_in(node, key):
            if eid.partition(".")[0] in GATE_DOMAINS:
                yield eid, key, logic, templated


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_gate(c: dict, reqs: dict) -> Unbindable | None:
    """Merge one helper condition's requirement into `reqs`: (entity, attribute) -> {"states", "lo", "hi"}."""
    eids = _ids_in(c["entity_id"], "entity_id") if isinstance(c["entity_id"], str) else \
        [e.strip().lower() for e in c["entity_id"]]
    domain = eids[0].partition(".")[0]
    attr = c.get("attribute")
    if any(e.partition(".")[0] == "group" for e in eids) and (c["condition"] != "state" or not set(
            _listed(c.get("state"))) <= {"home", "not_home"}):
        return Unbindable("device groups are not modelled")  # a presence group requires home / not_home
    if c["condition"] == "state":
        if c.get("match") == "any" and len(eids) > 1:
            return Unbindable(f"{domain} inside or/not")
        states = c.get("state")
        states = states if isinstance(states, list) else [states]
        if not all(isinstance(s, str) for s in states):
            return Unbindable("a helper state given as unquoted on/off (Home Assistant refuses it)")
        for eid in eids:
            r = reqs.setdefault((eid, attr), {"states": None, "lo": None, "hi": None})
            r["states"] = set(states) if r["states"] is None else r["states"] & set(states)
        return None
    if "value_template" in c:
        return Unbindable(f"{domain} in a template or an action")
    if c.get("above") is None and c.get("below") is None:
        return Unbindable("a helper numeric_state gate with neither above nor below")
    bounds = {}
    for side in ("above", "below"):
        if c.get(side) is not None:
            bounds[side] = _number(c[side])
            if bounds[side] is None:
                return Unbindable(f"{domain} compared against an entity or a template")
    for eid in eids:
        r = reqs.setdefault((eid, attr), {"states": None, "lo": None, "hi": None})
        if "above" in bounds:
            r["lo"] = bounds["above"] if r["lo"] is None else max(r["lo"], bounds["above"])
        if "below" in bounds:
            r["hi"] = bounds["below"] if r["hi"] is None else min(r["hi"], bounds["below"])
    return None


def _strip_gates(conds, reqs: dict):
    """The conditions without their helper gates (top level and inside `and`), recording each gate in `reqs`; an
    Unbindable when a gate cannot be held open."""
    items = conds if isinstance(conds, list) else [conds]
    out = []
    for c in items:
        if isinstance(c, list):  # a nested list: an implicit `and`
            sub = _strip_gates(c, reqs)
            if isinstance(sub, Unbindable):
                return sub
            if sub:
                out.append(sub)
            continue
        if not isinstance(c, dict):
            out.append(c)
            continue
        found = list(_helpers(c))
        if found and c.get("enabled") is False:  # Home Assistant ignores a disabled condition
            continue
        if c.get("condition") == "and" or ("and" in c and "condition" not in c):
            k = "conditions" if c.get("condition") == "and" else "and"
            sub = _strip_gates(c.get(k) or [], reqs)
            if isinstance(sub, Unbindable):
                return sub
            if sub:
                out.append({**c, k: sub})
            continue
        if c.get("condition") in ("state", "numeric_state") and isinstance(c.get("entity_id"), str | list) and found \
                and not any(lg or tp or key != "entity_id" for _, key, lg, tp in found):
            eids = _ids_in(c["entity_id"], "entity_id") if isinstance(c["entity_id"], str) else \
                [str(e).strip().lower() for e in c["entity_id"]]
            if any(e.partition(".")[0] not in GATE_DOMAINS for e in eids):
                return Unbindable(f"{found[0][0].partition('.')[0]} and a device in one condition")
            failed = _record_gate(c, reqs)
            if failed:
                return failed
            continue
        out.append(c)
    return out


def _assumptions(reqs: dict) -> list[str] | Unbindable:
    out = []
    for (eid, attr), r in sorted(reqs.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        name = f"{eid}[{attr}]" if attr else eid
        states, lo, hi = r["states"], r["lo"], r["hi"]
        if lo is not None and hi is not None and lo >= hi:
            return Unbindable(f"conflicting conditions on one helper (for {eid})")
        if states is not None and (lo is not None or hi is not None):
            states = {s for s in states if (n := _number(s)) is not None and (lo is None or n > lo)
                      and (hi is None or n < hi)}
        if states is not None:
            if not states:
                return Unbindable(f"conflicting conditions on one helper (for {eid})")
            out.append(f"{name} == {next(iter(states))}" if len(states) == 1 else
                       f"{name} in [{', '.join(sorted(states))}]")
        elif lo is not None and hi is not None:
            out.append(f"{name} in ({lo}, {hi})")
        elif lo is not None:
            out.append(f"{name} > {lo}")
        elif hi is not None:
            out.append(f"{name} < {hi}")
    return out


def _helper_refusal(raw: dict) -> Unbindable | None:
    """Why a helper left after the gates are removed makes the automation unbindable, by where it is."""
    def first(node):
        return next(iter(_helpers(node)), None)

    for k in ("trigger", "triggers"):
        for eid, key, _, _ in _helpers(raw.get(k)):
            domain = eid.partition(".")[0]
            if key in ("above", "below"):
                return Unbindable(f"{domain} as a numeric threshold")
            return Unbindable(f"{domain} in a trigger (helpers and presence change only with residents)")
    for k in ("condition", "conditions"):
        for c in _listed(raw.get(k)):
            found = first(c)
            if found is None:
                continue
            eid, key, logic, templated = found
            domain = eid.partition(".")[0]
            if key in ("above", "below"):
                return Unbindable(f"{domain} as a numeric threshold")
            if logic:
                return Unbindable(f"{domain} inside or/not")
            kind = c.get("condition") if isinstance(c, dict) else None
            if templated or kind in (None, "template"):
                return Unbindable(f"{domain} in a template or an action")
            return Unbindable(f"{domain} inside an `and` condition" if kind == "and" else
                              f"{domain} in a {kind} condition")
    rest = {k: v for k, v in raw.items() if k not in ("trigger", "triggers", "condition", "conditions")}
    found = first(rest)
    if found:
        return Unbindable(f"{found[0].partition('.')[0]} in a template or an action")
    return None


def _without_sets(node, found: set):
    """A copy of `node` without the helper ids its actions only set (`_set_slots`), collecting them in `found`."""
    if isinstance(node, list):
        return [_without_sets(v, found) for v in node]
    if not isinstance(node, dict):
        return node
    slots = _set_slots(node)
    out = {}
    for k, v in node.items():
        if k == "entity_id" and k in slots:
            found.update(slots[k])
            continue
        if k in slots:
            found.update(slots[k])
            v = {kk: vv for kk, vv in v.items() if kk != "entity_id"}
        out[k] = _without_sets(v, found)
    return out


def bind_with_assumptions(raw: dict, roles: dict, variant: int) -> tuple[dict, list[str]] | Unbindable:
    """`raw` bound as `bind` does, with its helper gates held open: (the bound automation, the assumptions, i.e. the
    state each removed gate requires); or why it cannot be bound. The engine refuses unknown top-level keys, so the
    assumptions come beside the bound automation, not in it."""
    raw = copy.deepcopy(raw)
    reqs: dict = {}
    for k in ("condition", "conditions"):
        if k in raw and raw[k] is not None:
            stripped = _strip_gates(raw[k], reqs)
            if isinstance(stripped, Unbindable):
                return stripped
            if stripped != _listed(raw[k]):  # a gate was removed (else the conditions stay as written)
                raw[k] = stripped
    assumptions = _assumptions(reqs)
    if isinstance(assumptions, Unbindable):
        return assumptions
    set_ids: set[str] = set()
    read = _without_sets(raw, set_ids)  # the automation as it reads helpers: the ones its actions only set removed
    gated_and_set = sorted(set_ids & {eid for eid, _ in reqs})
    if gated_and_set:
        return Unbindable(f"{gated_and_set[0].partition('.')[0]} in a template or an action")
    refusal = _helper_refusal(read)
    if refusal:
        return refusal
    global _SOURCES
    _SOURCES = {}
    try:
        bound = _rewrite(raw, roles, variant)
        sources = _SOURCES
    finally:
        _SOURCES = None
    if isinstance(bound, Unbindable):
        return bound
    collapsed = next((names for names in sources.values() if len(names) > 1), None)
    if collapsed:
        return Unbindable(f"distinct entities collapse onto one device (for {collapsed[0]}, {collapsed[1]})")
    return bound, assumptions


def bind(raw: dict, roles: dict, variant: int) -> dict | Unbindable:
    """`raw` with every entity id rewritten onto the home's devices (the input is not changed), or why it cannot be;
    helper gates are held open (`bind_with_assumptions` also says what each one assumes)."""
    r = bind_with_assumptions(raw, roles, variant)
    return r if isinstance(r, Unbindable) else r[0]
