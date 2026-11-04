"""The standard EDT home: the cross-site experiment's generated home (six rooms, seed 7) with, in every indoor room and
on the porch, one illuminance sensor per product in PRODUCTS["illuminance"] and one temperature/humidity sensor per
product in PRODUCTS["temperature"], ceiling-mounted at the room's centre. A public automation names its sensor by a
role (the room's illuminance, say); which product answers is the experiment's `variant`, so the same automation can be
replayed against several products.

The porch is the home's outdoor room, at the entrance: its sensors read the outdoor air and light (vesper.lab.rules
drives a device outdoors with them), so an automation's outdoor sensor binds there; it also has an outdoor light. Every
room with a light has a smart plug (the switch role), the porch an outdoor one.

`roles` maps (role, room, variant) -> the entity id of that device's feature. The roles without product variants
(motion, door, light, switch, lock, climate, cover) use variant 0 and the starter devices the generator placed; the
front door's contact and lock and the hallway thermostat are the home's only ones, so they answer in every room (the
front door's also on the porch it opens onto; the thermostat not outdoors). A room with no motion sensor (the bathroom,
the porch) answers motion with its illuminance sensor's occupancy: that product is a motion sensor with a light sensor.
A role the home has no device for (cover) has no key.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

# One product per sensor role: every L1 Zigbee product shares one reporting rule per quantity in the catalog (library
# defaults), so product variants would be identical rows.
PRODUCTS = {  # both L1 in catalog/vds on 2026-11-04
    "illuminance": ["zigbee:aqara:rtcgq11lm"],
    "temperature": ["zigbee:sonoff:snzb-02"],
}
PLUG = "zigbee:innr:sp-240"  # "Smart plug (EU)", L1: on/off and its power meter
OUTDOOR_PLUG = "zigbee:innr:osp-240"  # "Outdoor smart plug", L1, the same maker's outdoor one
OUTDOOR_LIGHT = "zigbee:osram:4058075816718"  # "SMART+ outdoor wall lantern RGBW", L1
OUTDOOR_ROOM = "porch"  # the generated home's outdoor room, at the entrance
# The generator places a light and a motion sensor in every room but the corridor; the EDT home adds them there, of the
# same starter products, at the corridor's centre and along it.
CORRIDOR = [
    {"product": "zigbee:m-elec:ml-st-bp-dim", "name": "corridor_light",
     "at": {"room": "corridor", "mount": "ceiling", "xy": [5.1, 4.6], "wall_switch": [0.6, 4.3]}},
    {"product": "zigbee:tuya:ih012-rt02", "name": "corridor_motion",
     "at": {"room": "corridor", "mount": "ceiling", "xy": [7.6, 4.6]}},
]
SINGLE = {  # roles the home has one device for, answering in every room: role -> (device, feature)
    "door": ("front_door_contact", "contact"), "lock": ("front_door_lock", "state"),
    "climate": ("corridor_thermostat", "current_heating_setpoint"),
}
OUTDOOR_SINGLE = ("door", "lock")  # of those, the ones that also answer on the porch: its door is the front door


def _centre(room) -> list[float]:
    from vesper.lab.windows import room_box
    x0, z0, x1, z1 = room_box(room)
    return [round((x0 + x1) / 2, 2), round((z0 + z1) / 2, 2)]


def _by_wall(room) -> list[float]:
    """A plug's place: along the room's first wall, 0.2 m in (the generator's first floor spot)."""
    from vesper.lab.windows import room_box
    x0, z0, x1, _ = room_box(room)
    return [round((x0 + x1) / 2, 2), round(z0 + 0.2, 2)]


def sensor_devices(world_map) -> list[dict]:
    """The sensors to add, in the style of crosssite's EXTRA_DEVICES: `<room>_lux<v>` and `<room>_temp<v>` in every
    indoor room and on the porch, and the porch's light."""
    out = [{**d, "at": dict(d["at"])} for d in CORRIDOR]
    for room in world_map.rooms:
        if room.outdoor and room.id != OUTDOOR_ROOM:
            continue
        for prefix, feature in (("lux", "illuminance"), ("temp", "temperature")):
            for v, product in enumerate(PRODUCTS[feature]):
                out.append({"product": product, "name": f"{room.id}_{prefix}{v}",
                            "at": {"room": room.id, "mount": "ceiling", "xy": _centre(room)}})
        if room.id == OUTDOOR_ROOM:
            out.append({"product": OUTDOOR_LIGHT, "name": f"{room.id}_light",
                        "at": {"room": room.id, "mount": "ceiling", "xy": _centre(room)}})
    return out


def plug_devices(world_map, devices: list[dict], catalog: dict) -> list[dict]:
    """A smart plug `<room>_plug` in every room that has a light (the porch's an outdoor one)."""
    lit = {d["at"].get("room") for d in devices if catalog[d["product"]]["identity"].get("category") == "light"}
    return [{"product": OUTDOOR_PLUG if room.outdoor else PLUG, "name": f"{room.id}_plug",
             "at": {"room": room.id, "mount": "floor", "xy": _by_wall(room)}}
            for room in world_map.rooms if room.id in lit]


def build_edt_home(work: Path, catalog_dir: str) -> tuple[Path, dict]:
    """The EDT home under `work/edt` (rebuilt each call): returns its home.yaml and the roles dict."""
    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    from vesper.lab.worldmap import WorldMap
    from vesper.twins.home import load_catalog
    from vesper.validation.crosssite import base_home
    base, out = base_home(work, catalog_dir), work / "edt"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    catalog = load_catalog(Path(catalog_dir))
    data = yaml.safe_load((out / "home.yaml").read_text())
    world_map = WorldMap.load(out / data.get("map", "map.json"))
    added = sensor_devices(world_map)
    devices = [d for d in data["devices"] if d["name"] not in {x["name"] for x in added}] + added
    plugs = plug_devices(world_map, devices, catalog)
    data["devices"] = [d for d in devices if d["name"] not in {x["name"] for x in plugs}] + plugs
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    home = load_lab_home(out / "home.yaml", catalog)
    ids = entity_ids(home)
    rooms = [r.id for r in home.map.rooms if not r.outdoor or r.id == OUTDOOR_ROOM]
    roles: dict[tuple[str, str, int], str] = {}
    for room in rooms:
        for prefix, features in (("lux", ("illuminance",)), ("temp", ("temperature", "humidity"))):
            for v in range(len(PRODUCTS["illuminance" if prefix == "lux" else "temperature"])):
                for feature in features:
                    roles[(feature, room, v)] = ids[f"{room}_{prefix}{v}"][feature]
        if f"{room}_motion" in ids:
            roles[("motion", room, 0)] = ids[f"{room}_motion"]["occupancy"]
        elif "occupancy" in ids[f"{room}_lux0"]:
            roles[("motion", room, 0)] = ids[f"{room}_lux0"]["occupancy"]
        if f"{room}_light" in ids:
            roles[("light", room, 0)] = ids[f"{room}_light"]["state"]
        if f"{room}_plug" in ids:
            roles[("switch", room, 0)] = ids[f"{room}_plug"]["state"]
        for role, (device, feature) in SINGLE.items():
            if device in ids and (room != OUTDOOR_ROOM or role in OUTDOOR_SINGLE):
                roles[(role, room, 0)] = ids[device][feature]
    return out / "home.yaml", roles
