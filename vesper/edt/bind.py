"""The binder: a public Home Assistant automation names its author's devices; this rewrites every entity id onto the
standard EDT home's devices, by role.

A role is what the entity measures or does (illuminance, motion, light, ...) and a room is where its name says it is.
Both are read from the entity id alone, so they are guesses: the domain says what kind of thing it is, and the words
of its name pick the role among those the domain allows (ROLE_PRIORITY orders the readings: `sensor.temp_lux` is an
illuminance sensor). An entity id that names no role, a role the home has no device for, or an entity the experiment
does not model makes the whole automation `Unbindable`: a half-bound automation would test something else.
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
    "illuminance": {"lux", "illuminance", "illumination", "luminosity", "lightlevel", "brightness"},
    "temperature": {"temp", "temperature"},
    "humidity": {"humidity", "humid", "rh"},
    "motion": {"motion", "occupancy", "presence", "pir", "movement"},
    "door": {"door", "window", "contact", "opening"},
}
ROOMS = (  # (name token prefix, room id): the first that matches; no match is the living room
    ("living", "living"), ("lounge", "living"), ("kitchen", "kitchen"), ("bedroom", "bedroom_1"), ("bath", "bathroom"),
    ("hall", "corridor"), ("corridor", "corridor"), ("office", "office"), ("study", "office"),
)
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
QUOTED = re.compile(r"""(['"])([a-z_]+\.[a-z0-9_]+)\1""")  # an entity id inside a template string
BOUND_DOMAINS = {d for ds in DOMAINS.values() for d in ds} | {"weather", "sun"}
MODELLED_ELSEWHERE ={"sun.sun"}  # the engine provides it at the home's site


@dataclass(frozen=True)
class Unbindable:
    reason: str


def infer_role(entity_id: str) -> tuple[str, str] | None:
    """(role, room) an entity id stands for, or None if its name says no role."""
    domain, _, name = entity_id.partition(".")
    tokens = name.split("_")
    role = None
    for r in ROLE_PRIORITY:
        if domain not in DOMAINS[r]:
            continue
        if r not in WORDS or WORDS[r] & set(tokens) or (r == "illuminance" and "light" in tokens and "level" in tokens):
            role = r
            break
    if role is None:
        return None
    room = next((rid for prefix, rid in ROOMS if any(t.startswith(prefix) for t in tokens)), "living")
    if room == "bedroom_1" and re.search(r"(bedroom|bed)_?(2|two)\b|guest", name):
        room = "bedroom_2"
    return role, room


def _roles_for(entity_id: str, roles: dict, variant: int) -> str | Unbindable:
    if entity_id in MODELLED_ELSEWHERE:
        return entity_id
    if entity_id.startswith("weather."):
        return Unbindable("weather entities are not modelled")
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


def _rewrite(node, roles: dict, variant: int, key: str | None = None):
    """The node with every entity id rewritten, or the Unbindable the first one that cannot be."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
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
        if key == "entity_id" and ENTITY.match(node):
            return _roles_for(node, roles, variant)
        if "{{" in node or "{%" in node:  # a template: the quoted ids in it
            failed = []

            def sub(m):
                if m.group(2).split(".")[0] not in BOUND_DOMAINS:  # a quoted service name or a string, not an entity
                    return m.group(0)
                r = _roles_for(m.group(2), roles, variant)
                if isinstance(r, Unbindable):
                    failed.append(r)
                    return m.group(0)
                return m.group(1) + r + m.group(1)
            text = QUOTED.sub(sub, node)
            return failed[0] if failed else text
    return node


def bind(raw: dict, roles: dict, variant: int) -> dict | Unbindable:
    """`raw` with every entity id rewritten onto the home's devices (the input is not changed), or why it cannot be."""
    return _rewrite(copy.deepcopy(raw), roles, variant)
