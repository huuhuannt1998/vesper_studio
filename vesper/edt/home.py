"""The standard EDT home: the cross-site experiment's generated home (six rooms, seed 7) with, in every indoor room, one
illuminance sensor per product in PRODUCTS["illuminance"] and one temperature/humidity sensor per product in
PRODUCTS["temperature"], ceiling-mounted at the room's centre. A public automation names its sensor by a role (the
room's illuminance, say); which product answers is the experiment's `variant`, so the same automation can be replayed
against several products.

`roles` maps (role, room, variant) -> the entity id of that device's feature. The roles without product variants
(motion, door, light, switch, lock, climate, cover) use variant 0 and the starter devices the generator placed; the
front door's contact and lock and the hallway thermostat are the home's only ones, so they answer in every room. A role
the home has no device for (cover, switch) has no key.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

PRODUCTS = {  # all L1 in catalog/vds on 2026-11-04
    "illuminance": ["zigbee:aqara:rtcgq11lm", "zigbee:aqara:rtcgq14lm",
                    "zigbee:tuya:ts0601_illuminance_temperature_humidity_sensor_1"],
    "temperature": ["zigbee:sonoff:snzb-02", "zigbee:aqara:wsdcgq11lm"],
}
SINGLE = {  # roles the home has one device for, answering in every room: role -> (device, feature)
    "door": ("front_door_contact", "contact"), "lock": ("front_door_lock", "state"),
    "climate": ("corridor_thermostat", "current_heating_setpoint"),
}


def _centre(room) -> list[float]:
    from vesper.lab.windows import room_box
    x0, z0, x1, z1 = room_box(room)
    return [round((x0 + x1) / 2, 2), round((z0 + z1) / 2, 2)]


def sensor_devices(world_map) -> list[dict]:
    """The sensors to add, in the style of crosssite's EXTRA_DEVICES: `<room>_lux<v>` and `<room>_temp<v>`."""
    out = []
    for room in world_map.rooms:
        if room.outdoor:
            continue
        for prefix, feature in (("lux", "illuminance"), ("temp", "temperature")):
            for v, product in enumerate(PRODUCTS[feature]):
                out.append({"product": product, "name": f"{room.id}_{prefix}{v}",
                            "at": {"room": room.id, "mount": "ceiling", "xy": _centre(room)}})
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
    data = yaml.safe_load((out / "home.yaml").read_text())
    added = sensor_devices(WorldMap.load(out / data.get("map", "map.json")))
    data["devices"] = [d for d in data["devices"] if d["name"] not in {x["name"] for x in added}] + added
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    catalog = load_catalog(Path(catalog_dir))
    home = load_lab_home(out / "home.yaml", catalog)
    ids = entity_ids(home)
    rooms = [r.id for r in home.map.rooms if not r.outdoor]
    roles: dict[tuple[str, str, int], str] = {}
    for room in rooms:
        for prefix, features in (("lux", ("illuminance",)), ("temp", ("temperature", "humidity"))):
            for v in range(len(PRODUCTS["illuminance" if prefix == "lux" else "temperature"])):
                for feature in features:
                    roles[(feature, room, v)] = ids[f"{room}_{prefix}{v}"][feature]
        if f"{room}_motion" in ids:
            roles[("motion", room, 0)] = ids[f"{room}_motion"]["occupancy"]
        if f"{room}_light" in ids:
            roles[("light", room, 0)] = ids[f"{room}_light"]["state"]
        for role, (device, feature) in SINGLE.items():
            if device in ids:
                roles[(role, room, 0)] = ids[device][feature]
    return out / "home.yaml", roles
