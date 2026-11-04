"""The binder: a public Home Assistant automation names its author's devices; this rewrites every entity id onto the
standard EDT home's devices, by role.

A role is what the entity measures or does (illuminance, motion, light, ...) and a room is where its name says it is.
Both are read from the entity id alone, so they are guesses: the domain says what kind of thing it is, and the words
of its name pick the role among those the domain allows (ROLE_PRIORITY orders the readings: `sensor.temp_lux` is an
illuminance sensor). An entity id that names no role, a role the home has no device for, an outdoor entity, a device or
area handle, or an entity the experiment does not model makes the whole automation `Unbindable`: a half-bound
automation would test something else.

Entity ids are found where an automation puts them: under `entity_id` (one id, a comma-separated string or a list),
under `above`/`below` (a numeric_state compared against an entity), and inside templates, quoted (`states('x.y')`) or
as attribute access (`states.x.y.state`).
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
WORDS = {  # the name tokens that say a sensor's role (an actuator's domain says its own)
    "illuminance": {"lux", "illuminance", "illumination", "luminosity", "lightlevel", "brightness", "light"},
    "temperature": {"temp", "temperature"},
    "humidity": {"humidity", "humid", "rh"},
    "motion": {"motion", "occupancy", "presence", "pir", "movement"},
    "door": {"door", "window", "contact", "opening"},
}
OTHER_MEASURE = {"power", "energy", "current", "voltage", "watt", "watts", "kwh", "battery", "signal", "linkquality",
                 "rssi", "status", "state"}  # a sensor with one of these measures something the home has no role for
ROOMS = (  # (name token prefix, room id): the first that matches; no match is the living room
    ("living", "living"), ("lounge", "living"), ("kitchen", "kitchen"), ("bedroom", "bedroom_1"), ("bath", "bathroom"),
    ("hall", "corridor"), ("corridor", "corridor"), ("office", "office"), ("study", "office"),
)
BEDROOM_2 = re.compile(r"(?<![a-z])(bed|bedroom)_?(2|two)(?![0-9a-z])|(?<![a-z])guest")
OUTDOOR = ("outdoor", "outside", "garden", "garage", "porch", "balcony", "patio", "driveway", "terrace", "deck",
           "carport", "shed", "pool")  # name-token prefixes
OUTDOOR_ANYWHERE = ("yard",)  # and inside a token (backyard, courtyard)
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


@dataclass(frozen=True)
class Unbindable:
    reason: str


def _outdoor(tokens) -> bool:
    return any(t.startswith(prefix) for t in tokens for prefix in OUTDOOR) or any(
        s in t for t in tokens for s in OUTDOOR_ANYWHERE)


def infer_role(entity_id: str) -> tuple[str, str] | None:
    """(role, room) an entity id stands for, or None if its name says no role (or it is outdoors)."""
    domain, _, name = entity_id.partition(".")
    tokens = name.split("_")
    if _outdoor(tokens):
        return None
    role = None
    for r in ROLE_PRIORITY:
        if domain not in DOMAINS[r]:
            continue
        if r in WORDS and OTHER_MEASURE & set(tokens):  # `light_power`, `motion_battery_low` are not the role
            continue
        if r not in WORDS or WORDS[r] & set(tokens):
            role = r
            break
    if role is None:
        return None
    room = next((rid for prefix, rid in ROOMS if any(t.startswith(prefix) for t in tokens)), None)
    if room in (None, "bedroom_1") and BEDROOM_2.search(name):
        room = "bedroom_2"
    return role, room or "living"


def _roles_for(entity_id: str, roles: dict, variant: int) -> str | Unbindable:
    if entity_id in MODELLED_ELSEWHERE:
        return entity_id
    domain, _, name = entity_id.partition(".")
    if domain == "weather":
        return Unbindable("weather entities are not modelled")
    if domain not in BOUND_DOMAINS:
        return Unbindable(f"{entity_id}: {domain} entities are not modelled")
    if _outdoor(name.split("_")):
        return Unbindable("outdoor entities are not modelled")
    found = infer_role(entity_id)
    if found is None:
        return Unbindable(f"{entity_id}: its name names no role the standard home has")
    role, room = found
    if role in VARIANT_ROLES and variant and (role, room, variant) not in roles:
        return Unbindable(f"no variant {variant} for {role} (the home has one product per sensor role)")
    bound = roles.get((role, room, variant if role in VARIANT_ROLES else 0))
    if bound is None:
        return Unbindable(f"the standard home has no {role} in the {room} (for {entity_id})")
    return bound


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
    parts = [p.strip() for p in s.split(",")]
    if all(ENTITY.match(p) for p in parts):
        bound = [_roles_for(p, roles, variant) for p in parts]
        failed = next((b for b in bound if isinstance(b, Unbindable)), None)
        return failed if failed else ", ".join(bound)
    if strict:
        return Unbindable(f"entity_id {text!r} is not an entity id")
    return text


def _rewrite(node, roles: dict, variant: int, key: str | None = None):
    """The node with every entity id rewritten, or the Unbindable the first one that cannot be."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in HANDLES:
                return Unbindable("device_id/area_id not modelled")
            if k == "use_blueprint":
                return Unbindable("blueprints are not expanded")
            v = _rewrite(v, roles, variant, k)
            if isinstance(v, Unbindable):
                return v
            out[k] = v
        return out
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


def bind(raw: dict, roles: dict, variant: int) -> dict | Unbindable:
    """`raw` with every entity id rewritten onto the home's devices (the input is not changed), or why it cannot be."""
    return _rewrite(copy.deepcopy(raw), roles, variant)
