"""Daylight in the play view (play-view environment design 2026-11-05): the daylight-following lights a generated home
may have, the thresholds a home's automations compare an illuminance sensor with, and the day's daylight table the
page previews.

The daylight-following rule, the PI's example ("the sun will make the room darker or brighter, so the lights can
adjust"): per lit room that has an illuminance sensor, one Home Assistant automation. When the room's illuminance
changes and someone is home, it sets the room's dimmable lights to

    brightness = clamp((target - daylight) / lamp_lux_at_full, 0, 1) x 254, off below 5 %,

the default target 300 lx. The sensor reads daylight plus the lamps that are on (VESPER's indoor model adds LAMP_LUX
per lamp on, vesper.lab.rules), so the rule takes daylight as the reading less LAMP_LUX per lamp of the room on; and
lamp_lux_at_full is LAMP_LUX per lamp of the room. "Someone is home" is any of the home's motion sensors seeing
someone (a home of sensors has no person entities); a home with none has no such condition. The automation is
written to the home's automations.yaml, so it runs live in Home Assistant and with VESPER's engine alike, and is
editable there. A generated home gets it when its request says `daylight_lights: true`.
"""
from __future__ import annotations

import math
from pathlib import Path

import yaml

from vesper.lab.rules import LAMP_LUX

DEFAULT_TARGET_LUX = 300
OFF_BELOW = 0.05  # a share of full brightness under which the rule turns the lights off
RULE_ID = "vesper_daylight_"  # the rule's id: this and the room's id
ILLUMINANCE = ("illuminance", "illuminance_lux")  # the features an illuminance sensor reports
DIMMABLE = ("brightness", "Level Control.CurrentLevel")
OCCUPANCY = ("occupancy", "presence", "Occupancy Sensing.Occupancy")
DAY_S = 86400


def follow_brightness(target: float, daylight: float, lamps_full: float) -> int:
    """The rule's brightness, 0 (off) to 254, for a room's daylight (lux) and what its lamps add at full."""
    if lamps_full <= 0:
        return 0
    level = min(1.0, max(0.0, (target - daylight) / lamps_full))
    return 0 if level < OFF_BELOW else round(level * 254)


def _features(device) -> set[str]:
    from vesper.twins.twin import Twin
    return set(Twin.from_vds(device.record).features)


def _category(device) -> str:
    return device.record["identity"]["category"]


def rule_for(room: str, room_name: str, sensor: str, lights: list[str], occupancy: list[str],
             target: float = DEFAULT_TARGET_LUX, lamp_lux: float = LAMP_LUX) -> dict:
    """The automation for one room: `sensor` its illuminance entity, `lights` its dimmable lights' entities,
    `occupancy` the home's motion sensors' entities (any one on: someone is home)."""
    # each lamp that is on adds its brightness's share of lamp_lux (Home Assistant's 0..255; a light that does not
    # dim reports none and counts at full), as the room model adds it (vesper.lab.rules.lamp_share)
    lit = " + ".join(f"((state_attr('{e}', 'brightness') | float(255)) / 255 if is_state('{e}', 'on') else 0)"
                     for e in lights)
    rule = {
        "id": f"{RULE_ID}{room}",
        "alias": f"Lights follow daylight: {room_name}",
        "description": (f"Sets the {room_name}'s lights to make up the gap between its daylight and {target:g} lx: "
                        "brightness = clamp((target - daylight) / lamps_full, 0, 1) x 254, off below 5 %. Daylight is "
                        "the reading less what the lamps that are on add. Written by VESPER's home generator."),
        "mode": "queued",
        "variables": {"target_lux": target, "lamp_lux": lamp_lux, "lamps_full_lux": lamp_lux * len(lights)},
        "triggers": [{"trigger": "state", "entity_id": sensor}],
        "conditions": [{"condition": "or", "conditions": [
            {"condition": "state", "entity_id": e, "state": "on"} for e in occupancy]}] if occupancy else [],
        "actions": [
            {"variables": {
                "daylight": f"{{{{ states('{sensor}') | float(0) - lamp_lux * ({lit}) }}}}",
                "level": "{{ [0, [1, (target_lux - daylight) / lamps_full_lux] | min] | max }}"}},
            {"if": [{"condition": "template", "value_template": f"{{{{ level < {OFF_BELOW} }}}}"}],
             "then": [{"action": "light.turn_off", "target": {"entity_id": list(lights)}}],
             "else": [{"action": "light.turn_on", "target": {"entity_id": list(lights)},
                       "data": {"brightness": "{{ (level * 254) | round(0) | int }}"}}]},
        ],
    }
    return rule


def daylight_rules(home) -> list[dict]:
    """The daylight-following automations a home can have: one per room with a dimmable light and an illuminance
    sensor on the hub, in the map's room order."""
    from vesper.lab.automations import entity_ids
    ids = entity_ids(home)
    rooms = {r.id: r for r in home.map.rooms if not r.outdoor}
    occupancy = [ids[d.name][f] for d in home.devices if d.name in ids for f in OCCUPANCY if f in ids[d.name]]
    out = []
    for room in rooms:
        here = [d for d in home.devices if d.room == room and d.name in ids]
        lights = [ids[d.name].get("state") or ids[d.name].get("On/Off.OnOff") for d in here
                  if _category(d) == "light" and _features(d) & set(DIMMABLE)]
        lights = [e for e in lights if e and e.startswith("light.")]
        sensors = [ids[d.name][f] for d in here for f in ILLUMINANCE if f in ids[d.name]
                   and ids[d.name][f].startswith("sensor.")]
        if lights and sensors:
            name = (rooms[room].label or room).split("/")[0]
            out.append(rule_for(room, name, sensors[0], lights, occupancy))
    return out


def write_daylight_automations(home_yaml: Path, catalog: dict) -> list[str]:
    """Give the home at `home_yaml` its daylight-following automations (in its automations file, automations.yaml
    unless home.yaml names another; a rule of the same id is replaced) and name the file in home.yaml. The rooms that
    got one; none: nothing is written."""
    from vesper.lab.home import load_lab_home
    home_yaml = Path(home_yaml)
    home = load_lab_home(home_yaml, catalog)
    rules = daylight_rules(home)
    if not rules:
        return []
    path = home.automations or home_yaml.parent / "automations.yaml"
    kept = []
    if path.is_file():
        kept = [a for a in (yaml.safe_load(path.read_text()) or []) if not str(a.get("id", "")).startswith(RULE_ID)]
    path.write_text(yaml.safe_dump(kept + rules, sort_keys=False, allow_unicode=True, width=100))
    if home.automations is None:
        text = home_yaml.read_text()
        home_yaml.write_text(text + ("" if text.endswith("\n") else "\n") + f"automations: {path.name}\n")
    return [r["id"].removeprefix(RULE_ID) for r in rules]


def _automations(home) -> list[dict]:
    if home.automations is None or not home.automations.is_file():
        return []
    try:
        raw = yaml.safe_load(home.automations.read_text()) or []
    except (OSError, yaml.YAMLError):
        return []
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def follow_rules(home) -> list[dict]:
    """The home's daylight-following rules as the page previews them: [{id, room, sensor, lights, target_lux,
    lamp_lux, lamps_full_lux}] with device names (an edited or disabled one is left out)."""
    from vesper.lab.automations import entity_ids
    by_entity = {e: name for name, feats in entity_ids(home).items() for e in feats.values()}
    out = []
    for a in _automations(home):
        if not str(a.get("id", "")).startswith(RULE_ID) or a.get("enabled") is False:
            continue
        v = a.get("variables") or {}
        triggers = a.get("triggers") or a.get("trigger") or []
        sensor = next((by_entity.get(t.get("entity_id")) for t in triggers if isinstance(t, dict)), None)
        lights = []
        for step in a.get("actions") or []:
            for branch in ("then", "else"):
                for act in (step.get(branch) or []) if isinstance(step, dict) else []:
                    ents = ((act.get("target") or {}).get("entity_id")) or []
                    for e in [ents] if isinstance(ents, str) else ents:
                        if by_entity.get(e) and by_entity[e] not in lights:
                            lights.append(by_entity[e])
        try:
            numbers = {k: float(v[k]) for k in ("target_lux", "lamp_lux", "lamps_full_lux")}
        except (KeyError, TypeError, ValueError):
            continue
        if sensor is None or not lights:
            continue
        out.append({"id": a["id"], "room": a["id"].removeprefix(RULE_ID), "sensor": sensor, "lights": lights,
                    **{k: int(x) if x == int(x) else x for k, x in numbers.items()}})
    return out


def illuminance_thresholds(home, device: str) -> list[str]:
    """What the home's automations compare an illuminance sensor's reading with, in words: "dark below 50 lx (its
    alias)", "bright above 500 lx (...)", and "lights follow daylight to 300 lx" for a daylight-following rule."""
    from vesper.lab.automations import entity_ids
    mine = {e for f, e in entity_ids(home).get(device, {}).items() if f in ILLUMINANCE}
    if not mine:
        return []
    out = []
    for rule in follow_rules(home):
        if rule["sensor"] == device:
            out.append(f"lights follow daylight to {rule['target_lux']:g} lx")
    for a in _automations(home):
        if str(a.get("id", "")).startswith(RULE_ID):
            continue
        name = a.get("alias") or a.get("id") or "an automation"
        found = []

        def walk(node):
            if isinstance(node, dict):
                kind = node.get("trigger") or node.get("platform") or node.get("condition")
                ents = node.get("entity_id")
                ents = [ents] if isinstance(ents, str) else ents or []
                if kind == "numeric_state" and mine & set(ents):
                    if isinstance(node.get("below"), int | float):
                        found.append(f"dark below {node['below']:g} lx ({name})")
                    if isinstance(node.get("above"), int | float):
                        found.append(f"bright above {node['above']:g} lx ({name})")
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(a)
        out += [f for f in found if f not in out]
    return out


def daylight_table(session, step: float = 900) -> dict | None:
    """The day's daylight, for the page's time-of-day preview: one row per `step` seconds of the session's current
    day, each {t (seconds after the first day's local midnight), hour, sun_azimuth, sun_elevation, illuminance_lux,
    direct_fraction, temperature_c, cloud_cover_pct, rooms: {room: daylight lux with the shades as they are now}}.
    From the cached weather and the session's own vesper.env model; no network. None for a home with no site."""
    env = session.rules.env
    if env is None:
        return None
    step = float(step)
    if not math.isfinite(step) or step < 60 or step > 3600:
        raise ValueError("step: 60 to 3600 seconds")
    now = session.home.start_time + session.clock.now()
    day0 = math.floor(now / DAY_S) * DAY_S
    site = session.home.site
    rows = []
    for k in range(int(DAY_S // step) + 1):
        t = day0 + k * step
        state = env.outdoor_at(t)
        rows.append({"t": t, "hour": round(k * step / 3600, 4), "sun_azimuth": state.sun.azimuth,
                     "sun_elevation": state.sun.elevation, "illuminance_lux": round(state.illuminance_lux),
                     "direct_fraction": round(state.direct_fraction, 3),
                     "temperature_c": round(state.temperature_c, 1), "cloud_cover_pct": state.cloud_cover_pct,
                     "rooms": {rid: round(env.daylight(rid, state)) for rid in env.rooms}})
    return {"date": site.local(day0 + 1).date().isoformat(), "day_start": day0, "now": now, "step": step,
            "lamp_lux": LAMP_LUX, "rules": follow_rules(session.home), "rows": rows}
