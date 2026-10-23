"""Home Assistant MQTT device discovery for twins behind the Zigbee2MQTT-compatible bridge.

Written from Home Assistant's MQTT discovery documentation (one `device` discovery message per twin).
It does not copy Zigbee2MQTT's GPL-3.0 Home Assistant extension (spec §5 Licensing).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from vesper import __version__

DISCOVERY_PREFIX = "homeassistant"
_MARK = "__VESPER_VALUE__"


@dataclass(frozen=True)
class ExpectedEntity:
    """An entity Home Assistant should create from the discovery message (used by acceptance checks)."""

    entity_id: str
    platform: str
    device_class: str | None
    unit: str | None
    feature: str  # the VDS feature behind the entity (a light's or lock's "state")
    controllable: bool


def object_id(ieee: str, feature: str) -> str:
    return f"v{ieee[2:]}_" + (re.sub(r"[^a-z0-9]+", "_", feature.lower()).strip("_") or "x")


def event_topic(state_topic: str, feature: str) -> str:
    return f"{state_topic}/event/{re.sub(r'[^a-z0-9]+', '_', feature.lower()).strip('_')}"


def _accessor(name: str) -> str:
    return "value_json" + "".join(f"[{json.dumps(part)}]" for part in name.split("."))


def _nested(name: str, value) -> dict:
    *parents, leaf = name.split(".")
    out = node = {}
    for p in parents:
        node = node.setdefault(p, {})
    node[leaf] = value
    return out


def _command_template(name: str, quote: bool) -> str:
    text = json.dumps(_nested(name, _MARK))
    return text.replace(f'"{_MARK}"', '"{{ value }}"' if quote else "{{ value }}")


def _on_off_template(f: dict) -> str:
    return f"{{{{ 'ON' if {_accessor(f['name'])} == {json.dumps(f['value_on'])} else 'OFF' }}}}"


def _component(f: dict, ieee: str, state_topic: str, set_topic: str):
    """(object id, component config, expected entity) or a reason string."""
    platform, _, cls = f["ha"].partition("/")
    oid, name, unit = object_id(ieee, f["name"]), f["name"], f.get("unit")
    cfg = {"p": platform, "unique_id": oid, "default_entity_id": f"{platform}.{oid}",
           "name": None if f["name"] == "state" else name.replace("_", " ").replace(".", " ")}
    if f.get("category") in ("config", "diagnostic"):
        cfg["entity_category"] = f["category"]
    value = f"{{{{ {_accessor(name)} }}}}"
    controllable = True
    if platform == "binary_sensor":
        cfg["value_template"] = _on_off_template(f)
        controllable = False
    elif platform == "sensor":
        cfg["value_template"] = value
        controllable = False
        if cls == "enum":
            cfg["options"] = [str(v) for v in f.get("values", [])]
        elif unit:
            cfg["unit_of_measurement"] = unit
        if f["type"] == "numeric" and cls != "enum":
            cfg["state_class"] = "total_increasing" if cls == "energy" else "measurement"
    elif platform == "switch":
        # HA's switch is on when the rendered state equals `state_on or payload_on` (mqtt/switch.py), and the
        # payloads here are JSON commands, so the rendered ON/OFF needs state_on/state_off of its own.
        cfg |= {"command_topic": set_topic, "value_template": _on_off_template(f),
                "payload_on": json.dumps(_nested(name, f["value_on"])),
                "payload_off": json.dumps(_nested(name, f["value_off"])), "state_on": "ON", "state_off": "OFF"}
    elif platform == "number":
        cfg |= {"command_topic": set_topic, "value_template": value, "command_template": _command_template(name, False)}
        if "range" in f:
            cfg["min"], cfg["max"] = f["range"]
        if "step" in f:
            cfg["step"] = f["step"]
        if unit:
            cfg["unit_of_measurement"] = unit
    elif platform == "select":
        cfg |= {"command_topic": set_topic, "value_template": value, "command_template": _command_template(name, True),
                "options": [str(v) for v in f.get("values", [])]}
    elif platform == "button":
        cfg |= {"command_topic": set_topic, "payload_press": json.dumps(_nested(name, (f.get("values") or [name])[0]))}
    elif platform == "text":
        cfg |= {"command_topic": set_topic, "value_template": value, "command_template": _command_template(name, True)}
    elif platform == "event":
        cfg |= {"state_topic": event_topic(state_topic, name), "event_types": [str(v) for v in f.get("values", [])]}
        controllable = False
    else:
        return f"platform {platform!r} is not exposed yet"
    if cls:
        cfg["device_class"] = cls
    return oid, cfg, ExpectedEntity(f"{platform}.{oid}", platform, cls or None,
                                    cfg.get("unit_of_measurement"), name, controllable)


def _light(fs: list[dict], ieee: str, set_topic: str):
    names = {f["name"]: f for f in fs}
    if any("endpoint" in f for f in fs):
        return "multi-endpoint lights are not exposed yet"
    if "state" not in names:
        return "light without an on/off state"
    oid = object_id(ieee, "light")
    cfg = {"p": "light", "schema": "json", "unique_id": oid, "default_entity_id": f"light.{oid}", "name": None,
           "command_topic": set_topic}
    modes = [m for m, feature in (("color_temp", "color_temp"), ("xy", "color.x")) if feature in names]
    if "brightness" in names:
        cfg["brightness"] = True
        cfg["brightness_scale"] = int(names["brightness"].get("range", [0, 254])[1])
    cfg["supported_color_modes"] = modes or (["brightness"] if "brightness" in names else ["onoff"])
    if "color_temp" in names and "range" in names["color_temp"]:
        lo, hi = names["color_temp"]["range"]  # mireds
        cfg |= {"color_temp_kelvin": False, "min_kelvin": round(1e6 / hi), "max_kelvin": round(1e6 / lo)}
    return oid, cfg, ExpectedEntity(f"light.{oid}", "light", None, None, "state", True)


def _lock(fs: list[dict], ieee: str, set_topic: str):
    state = next((f for f in fs if f["name"] == "state" and f["type"] == "binary"), None)
    if state is None:
        return "lock without a binary state"
    oid = object_id(ieee, "lock")
    cfg = {"p": "lock", "unique_id": oid, "default_entity_id": f"lock.{oid}", "name": None,
           "command_topic": set_topic, "value_template": f"{{{{ {_accessor('state')} }}}}",
           "payload_lock": json.dumps({"state": state["value_on"]}),
           "payload_unlock": json.dumps({"state": state["value_off"]}),
           "state_locked": state["value_on"], "state_unlocked": state["value_off"]}
    return oid, cfg, ExpectedEntity(f"lock.{oid}", "lock", None, None, "state", True)


def device_discovery(vds: dict, *, ieee: str, friendly_name: str, base_topic: str = "zigbee2mqtt"):
    """(topic, payload, expected entities, skipped [(feature, reason)]) for one twin."""
    state_topic = f"{base_topic}/{friendly_name}"
    set_topic = f"{state_topic}/set"
    cmps, expected, skipped, grouped = {}, [], [], {}
    for f in vds["features"]:
        if "#" in f["name"]:
            continue
        if f["type"] == "unsupported" or not f["ha"]:
            skipped.append((f["name"], "feature type not modeled" if f["type"] == "unsupported"
                            else "no Home Assistant mapping"))
        elif f.get("path") in ("light", "lock"):
            grouped.setdefault(f["path"], []).append(f)
        elif f.get("path") in ("climate", "cover", "fan"):
            skipped.append((f["name"], f"{f['path']} entities are not exposed yet"))
        else:
            built = _component(f, ieee, state_topic, set_topic)
            if isinstance(built, str):
                skipped.append((f["name"], built))
            else:
                cmps[built[0]] = built[1]
                expected.append(built[2])
    for path, builder in (("light", _light), ("lock", _lock)):
        if path in grouped:
            built = builder(grouped[path], ieee, set_topic)
            if isinstance(built, str):
                skipped.extend((f["name"], built) for f in grouped[path])
            else:
                cmps[built[0]] = built[1]
                expected.append(built[2])
                skipped.extend((f["name"], f"part of the {path} entity") for f in grouped[path]
                               if f["name"] != "state")
    identity = vds["identity"]
    payload = {
        "dev": {"ids": [f"zigbee2mqtt_{ieee}"], "name": friendly_name, "mf": identity["vendor"],
                "mdl": identity["model"]},
        "o": {"name": "VESPER", "sw": __version__},
        "state_topic": state_topic,
        "availability": [{"topic": f"{state_topic}/availability", "value_template": "{{ value_json.state }}"}],
        "cmps": cmps,
    }
    return f"{DISCOVERY_PREFIX}/device/{ieee}/config", payload, expected, skipped
