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

Variants (protocol Addendum 5, home variation): `build_edt_home(..., variant=NAME)` builds one of VARIANTS instead of
the standard home, each defined by one parameter: the glazing scaled (every window pane's area times a factor, same
positions), another generated floor plan (another seed, the same rooms, the standard home's products), or the
standard plan mirrored east-west. The sensors, lights, plugs and roles are then added by the same rules, so the role
set is the same and an automation binds to the same roles in every variant. `variant=None` is the standard home,
unchanged.
"""
from __future__ import annotations

import json
import math
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
# same starter products, at the corridor's centre and along it: in the standard home CORRIDOR below; in any home
# corridor_devices (the light at the centre, its wall switch 0.5 m from the front-door end, the motion sensor 3/4 of
# the way from that end), which is CORRIDOR exactly in the standard home.
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


def corridor_devices(world_map) -> list[dict]:
    """The corridor's light and motion sensor (CORRIDOR's products and names), placed from the corridor's box and
    its front-door end (the end nearer the porch): the light at the centre, its wall switch 0.5 m along from that end
    and 0.2 m off the corridor's z0 wall, the motion sensor 3/4 of the way from that end, on the centre line."""
    from vesper.lab.windows import room_box
    corridor = world_map.room("corridor")
    x0, z0, x1, z1 = room_box(corridor)
    if z1 - z0 > x1 - x0:
        raise ValueError("the EDT home's corridor must run along x (a generated plan's does)")
    px0, _, px1, _ = room_box(world_map.room(OUTDOOR_ROOM))
    porch_x = (px0 + px1) / 2
    end, sign = (x0, 1) if abs(porch_x - x0) <= abs(porch_x - x1) else (x1, -1)
    light, motion = ({**d, "at": dict(d["at"])} for d in CORRIDOR)
    light["at"]["xy"] = _centre(corridor)
    light["at"]["wall_switch"] = [round(end + sign * 0.5, 2), round(z0 + 0.2, 2)]
    motion["at"]["xy"] = [round(end + sign * 0.75 * (x1 - x0), 2), round((z0 + z1) / 2, 2)]
    return [light, motion]


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
    out = corridor_devices(world_map)
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


# ---- home variation (protocol Addendum 5) ----------------------------------------------------------------------------

VARIANTS = {  # name -> its one parameter, fixed before the variants' first run (Addendum 5)
    "glazing-x0.5": {"glazing": 0.5},  # every pane's area x 0.5, same positions
    "glazing-x2": {"glazing": 2.0},  # every pane's area x 2, same positions
    "plan-seed8": {"seed": 8},  # the generator's six-room plan for seed 8, the standard home's products
    "mirror-ew": {"mirror": True},  # the standard home mirrored east-west
    # protocol Addendum 18 (A18.3): three further generated plans with other window distributions, by its rule
    "plan-seed11": {"seed": 11},
    "plan-seed16": {"seed": 16},
    "plan-seed9": {"seed": 9},
}
PANE_GAP_M = 0.1  # two panes on one wall keep at least this apart (a mullion)
LINTEL_M = 0.05  # a pane's top keeps at least this below the ceiling


def scale_glazing(windows: list[dict], factor: float, world_map) -> list[dict]:
    """Every pane's area times `factor`, same position: width and height times sqrt(factor) about the pane's centre.
    A width that would come nearer than CORNER_M to its wall's end, or than PANE_GAP_M to a neighbouring pane on the
    same wall (the gap shared in proportion to the two widths), is capped there and the height takes the rest, so the
    area is `factor` times the original either way. The pane keeps its vertical centre, moved down if its top would
    come within LINTEL_M of the ceiling (up if its sill would go below the floor); a pane taller than that is an
    error."""
    from vesper.env.indoor import CEILING_M
    from vesper.lab.windows import CORNER_M, room_box
    s = math.sqrt(factor)
    out = []
    for w in windows:
        x0, z0, x1, z1 = room_box(world_map.room(w["room"]))
        on_x = w["side"] in ("z0", "z1")  # the wall runs along x
        along, lo, hi = (w["x"], x0, x1) if on_x else (w["z"], z0, z1)
        half = min(along - lo - CORNER_M, hi - CORNER_M - along)
        for o in windows:
            if o is not w and o["room"] == w["room"] and o["side"] == w["side"]:
                gap = abs((o["x"] if on_x else o["z"]) - along) - PANE_GAP_M
                half = min(half, gap * w["width"] / (w["width"] + o["width"]))
        area = factor * w["width"] * w["height"]
        width = min(w["width"] * s, 2 * half)
        height = area / width
        if height > CEILING_M - LINTEL_M:
            raise ValueError(f"{w['id']}: a {height:.2f} m pane does not fit under the {CEILING_M} m ceiling")
        sill = max(0.0, min(w["sill"] + (w["height"] - height) / 2, CEILING_M - LINTEL_M - height))
        out.append({**w, "width": round(width, 3), "height": round(height, 3), "sill": round(sill, 3)})
    return out


def _mirror_x(axis: float, x: float) -> float:
    return round(axis - x, 3) + 0.0  # + 0.0: no -0.0


def _mirror_grid(g: dict) -> dict:
    from vesper.lab.worldmap import Grid
    grid = Grid.from_json(g)
    cells = bytearray()
    for j in range(grid.height):
        cells += grid.walkable[j * grid.width:(j + 1) * grid.width][::-1]
    return {**g, **Grid(grid.origin, grid.cell, grid.width, grid.height, cells).to_json()}


def mirror_home(src: Path, out: Path) -> Path:
    """The home in `src` mirrored east-west into `out` (rebuilt): x -> A - x with A the sum of the grid's two x edges,
    so the grid maps onto itself (cell i onto cell width-1-i). Mirrored: the rooms, the walking grid (and the bare
    plan's, map.original.json), the devices and their wall switches, the windows (a pane facing east faces west) and
    the furniture (a piece facing +x faces -x)."""
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    g = json.loads((out / "map.json").read_text())["grid"]
    axis = 2 * g["origin"][0] + g["width"] * g["cell"]
    for name in ("map.json", "map.original.json"):
        if (out / name).is_file():
            d = json.loads((out / name).read_text())
            d["grid"] = _mirror_grid(d["grid"])
            for r in d["rooms"]:
                r["polygon"] = [[_mirror_x(axis, p[0]), p[1]] for p in reversed(r["polygon"])]
            (out / name).write_text(json.dumps(d))
    for name, item in (("windows.json", "window"), ("furniture.json", "piece")):
        if not (out / name).is_file():
            continue
        d = json.loads((out / name).read_text())
        for p in d["items"]:
            p["x"] = _mirror_x(axis, p["x"])
            if item == "window":  # facing: degrees clockwise from the plan's top
                p["facing"] = (360 - p["facing"]) % 360
                p["side"] = {"x0": "x1", "x1": "x0"}.get(p["side"], p["side"])
            else:  # facing: degrees from +x towards +z
                p["facing"] = (180 - p["facing"]) % 360
                bx0, bz0, bx1, bz1 = p["box"]
                p["box"] = [_mirror_x(axis, bx1), bz0, _mirror_x(axis, bx0), bz1]
        (out / name).write_text(json.dumps(d, indent=1) + "\n")
    data = yaml.safe_load((out / "home.yaml").read_text())
    for dev in data["devices"]:
        at = dev.get("at") or {}
        for key in ("xy", "wall_switch"):
            if key in at:
                at[key] = [_mirror_x(axis, at[key][0]), at[key][1]]
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return out


def plan_home(work: Path, catalog_dir: str, seed: int) -> Path:
    """`vesper lab generate`'s six-room home for `seed` with the standard home's products (the generator would draw
    other products from another seed): its starter devices, each of the product the standard (seed 7) home has for
    that category, placed by the generator's rules."""
    import contextlib
    import io

    from vesper import cli
    from vesper.lab.generate import STARTER
    from vesper.twins.home import load_catalog
    from vesper.validation.crosssite import base_home
    out = work / f"base-seed{seed}"
    if (out / "home.yaml").is_file():
        return out
    catalog = load_catalog(Path(catalog_dir))
    standard = yaml.safe_load((base_home(work, catalog_dir) / "home.yaml").read_text())
    by_category: dict[str, str] = {}
    for d in standard["devices"]:
        by_category.setdefault(catalog[d["product"]]["identity"].get("category"), d["product"])
    devices = [{"product": by_category[d["category"]], **{k: v for k, v in d.items() if k != "category"}}
               for d in STARTER]
    request = work / f"request-seed{seed}.yaml"
    request.write_text(yaml.safe_dump({"format": "vesper-request/1", "rooms": 6, "seed": seed, "devices": devices},
                                      sort_keys=False))
    with contextlib.redirect_stdout(io.StringIO()):
        if cli.main(["lab", "generate", str(out), "--request", str(request), "--catalog", catalog_dir]) != 0:
            raise RuntimeError(f"vesper lab generate failed for the seed-{seed} home")
    return out


def variant_base(work: Path, catalog_dir: str, variant: str) -> Path:
    """A variant's home before the EDT devices, under `work`."""
    from vesper.lab.windows import load_windows, write_windows
    from vesper.lab.worldmap import WorldMap
    from vesper.validation.crosssite import base_home
    if variant not in VARIANTS:
        raise ValueError(f"unknown home variant {variant!r}; one of {', '.join(VARIANTS)}")
    spec = VARIANTS[variant]
    if "seed" in spec:
        return plan_home(work, catalog_dir, spec["seed"])
    base, out = base_home(work, catalog_dir), work / f"base-{variant}"
    if "mirror" in spec:
        return mirror_home(base, out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    write_windows(out, scale_glazing(load_windows(out), spec["glazing"], WorldMap.load(out / "map.json")))
    # furnished again for the new panes (the furnisher keeps tall pieces from in front of a window): from the bare
    # plan, as the generator does; furniture takes no part in an EDT trace (no residents)
    from vesper.lab.generate import _furnish
    from vesper.twins.home import load_catalog
    bare = json.loads((out / "map.original.json").read_text())
    bare.pop("bare", None)
    (out / "map.json").write_text(json.dumps(bare))
    _furnish(out, load_catalog(Path(catalog_dir)))
    return out


def build_edt_home(work: Path, catalog_dir: str, variant: str | None = None) -> tuple[Path, dict]:
    """The EDT home under `work/edt` (rebuilt each call): returns its home.yaml and the roles dict. `variant`: one of
    VARIANTS (protocol Addendum 5), or None: the standard home."""
    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    from vesper.lab.worldmap import WorldMap
    from vesper.twins.home import load_catalog
    from vesper.validation.crosssite import base_home
    base = base_home(work, catalog_dir) if variant is None else variant_base(work, catalog_dir, variant)
    out = work / "edt"
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
