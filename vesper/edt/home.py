"""The standard EDT home: the cross-site experiment's generated home (six rooms, seed 7) with, in every indoor room and
on the porch, one illuminance sensor per product in PRODUCTS["illuminance"] and one temperature/humidity sensor per
product in PRODUCTS["temperature"], ceiling-mounted at the room's centre. A public automation names its sensor by a
role (the room's illuminance, say); which product answers is the experiment's `variant`, so the same automation can be
replayed against several products.

The porch is the home's outdoor room, at the entrance: its sensors read the outdoor air and light (vesper.lab.rules
drives a device outdoors with them), so an automation's outdoor sensor binds there; it also has outdoor lights. Every
room with a light has three lights and three smart plugs (the switch role), the porch two outdoor ones of each.

`roles` maps (role, room, variant) -> the entity id of that device's feature. For lights and plugs (light, switch)
the third key is the copy instead: every lit room has COPIES of each, (role, room, 0), (role, room, 1), ... The other
roles without product variants (motion, door, lock, climate, cover) use 0 and the starter devices the generator
placed; the front door's contact and lock and the hallway thermostat are the home's only ones, so they answer in every
room (the front door's also on the porch it opens onto; the thermostat not outdoors). A room with no motion sensor
(the bathroom, the porch) answers motion with its illuminance sensor's occupancy: that product is a motion sensor with
a light sensor. A role the home has no device for (cover) has no key.
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
COPIES = {"indoor": 3, "porch": 2}  # lights and plugs per lit room: an automation naming several binds several


def _centre(room) -> list[float]:
    from vesper.lab.windows import room_box
    x0, z0, x1, z1 = room_box(room)
    return [round((x0 + x1) / 2, 2), round((z0 + z1) / 2, 2)]


def _ceiling_spot(room, i: int) -> list[float]:
    """The i-th extra ceiling light's place (i = 1, 2): 1/6 and 5/6 along the room's long axis, on its centre line,
    clear of the generator's 1/4 and 3/4 places (a second ceiling device, a corridor's motion sensor)."""
    from vesper.lab.generate import _inside
    from vesper.lab.windows import room_box
    x0, z0, x1, z1 = room_box(room)
    f = (1 / 6, 5 / 6)[i - 1]
    xy = [x0 + (x1 - x0) * f, (z0 + z1) / 2] if x1 - x0 >= z1 - z0 else [(x0 + x1) / 2, z0 + (z1 - z0) * f]
    return _inside(room, [round(xy[0], 2), round(xy[1], 2)])


def _floor_spot(room, k: int) -> list[float]:
    """The k-th plug's place: the generator's floor spots, along the walls, 0.2 m in."""
    from vesper.lab.generate import _inside
    from vesper.lab.windows import room_box
    x0, z0, x1, z1 = room_box(room)
    spots = [((x0 + x1) / 2, z0 + 0.2), (x1 - 0.2, (z0 + z1) / 2), ((x0 + x1) / 2, z1 - 0.2), (x0 + 0.2, (z0 + z1) / 2)]
    return _inside(room, [round(spots[k % 4][0], 2), round(spots[k % 4][1], 2)])


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


def _copy_name(room: str, kind: str, i: int) -> str:
    return f"{room}_{kind}" if i == 0 else f"{room}_{kind}_{i + 1}"


def copy_devices(world_map, devices: list[dict], catalog: dict) -> list[dict]:
    """In every room with a light: lights up to COPIES (`<room>_light_2`, `_3`, of the room's light product, spread
    along the ceiling) and as many smart plugs (`<room>_plug`, `_plug_2`, ...; the porch's outdoor ones)."""
    first = {}
    for d in devices:
        if catalog[d["product"]]["identity"].get("category") == "light":
            first.setdefault(d["at"].get("room"), d["product"])
    out = []
    for room in world_map.rooms:
        if room.id not in first:
            continue
        n = COPIES["porch" if room.outdoor else "indoor"]
        for i in range(1, n):
            out.append({"product": first[room.id], "name": _copy_name(room.id, "light", i),
                        "at": {"room": room.id, "mount": "ceiling", "xy": _ceiling_spot(room, i)}})
        for k in range(n):
            out.append({"product": OUTDOOR_PLUG if room.outdoor else PLUG, "name": _copy_name(room.id, "plug", k),
                        "at": {"room": room.id, "mount": "floor", "xy": _floor_spot(room, k)}})
    return out


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
    copies = copy_devices(world_map, devices, catalog)
    data["devices"] = [d for d in devices if d["name"] not in {x["name"] for x in copies}] + copies
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
        for kind, role in (("light", "light"), ("plug", "switch")):  # the third key is the copy, in name order
            i = 0
            while _copy_name(room, kind, i) in ids:
                roles[(role, room, i)] = ids[_copy_name(room, kind, i)]["state"]
                i += 1
        for role, (device, feature) in SINGLE.items():
            if device in ids and (room != OUTDOOR_ROOM or role in OUTDOOR_SINGLE):
                roles[(role, room, 0)] = ids[device][feature]
    return out / "home.yaml", roles
