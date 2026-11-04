"""The home generator (home-generator spec §2, plan W9): a number of rooms, or a list of room types, becomes a home's
floor plan, `vesper-map/1`, so walking, reach, residents, the network, scenarios and the 3D view work on it unchanged.

The plan is a corridor with the rooms along both sides: every room opens onto the corridor through a 0.9 m door, so
every room is reachable from the front door, which joins the corridor's west end to a porch outside. Walls are 20 cm
between rooms and 10 cm inside the outer edge; each room's size is drawn, from the seed, within its type's typical
range; the two sides are balanced so the home stays a rectangle. Generated homes are synthetic, and say so.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from vesper.lab.geometry import point_in_polygon
from vesper.lab.worldmap import Grid, Room, WorldMap

CELL = 0.1
MAX_ROOMS = 60
WALL = 0.1  # half a wall: a room's polygon stops this far from its outline
DEPTH = 4.0  # how deep the rooms on each side of the corridor are (m)
MIN_WIDTH = 2.0
CORRIDOR = 1.2
DOOR = 0.9
PORCH = (2.0, 3.0)  # depth, width
# Typical floor areas (m², executor's call from common residential sizes)
AREAS = {"living": (16, 28), "kitchen": (8, 14), "bedroom": (10, 16), "bathroom": (4, 7), "office": (8, 12),
         "dining": (10, 16), "laundry": (4, 6)}
LABELS = {"living": "living room", "kitchen": "kitchen", "bedroom": "bedroom", "bathroom": "bathroom",
          "office": "office", "dining": "dining room", "laundry": "laundry room"}
# The room list a count gives: one kitchen and living room, then bedrooms with a bathroom for every two
ORDER = ["living", "bedroom", "kitchen", "bathroom", "bedroom", "office", "bedroom", "bathroom", "dining", "laundry"]
GROWTH = ["bedroom", "bedroom", "bathroom", "office"]


class GenerateError(ValueError):
    pass


def program(rooms: int) -> list[str]:
    """The room types of a home with this many rooms (the corridor and the porch come with every home)."""
    if isinstance(rooms, bool) or not isinstance(rooms, int) or not 1 <= rooms <= MAX_ROOMS:
        raise GenerateError(f"rooms: {rooms!r} is not 1 to {MAX_ROOMS}")
    out = ORDER[:rooms]
    while len(out) < rooms:
        out.append(GROWTH[(len(out) - len(ORDER)) % len(GROWTH)])
    return out


def floor_plan(types: list[str], seed: int, scene: str | None = None) -> WorldMap:
    for t in types:
        if t not in AREAS:
            raise GenerateError(f"program: {t!r} is not a room type; choose one of {', '.join(AREAS)}")
    if not 1 <= len(types) <= MAX_ROOMS:
        raise GenerateError(f"program: {len(types)} rooms is not 1 to {MAX_ROOMS}")
    rng = random.Random(f"vesper-generate:{seed}")
    counts = {t: types.count(t) for t in set(types)}
    seen: dict[str, int] = {}
    rooms = []  # (id, type, width)
    for t in types:
        seen[t] = seen.get(t, 0) + 1
        rid = t if counts[t] == 1 else f"{t}_{seen[t]}"
        area = rng.uniform(*AREAS[t])
        rooms.append((rid, t, max(MIN_WIDTH, round(area / DEPTH, 1))))
    # each room to the shorter side, in order: the home stays close to a rectangle
    sides: dict[str, list] = {"south": [], "north": []}
    for r in rooms:
        side = "south" if sum(w for *_, w in sides["south"]) <= sum(w for *_, w in sides["north"]) else "north"
        sides[side].append(list(r))
    length = max(sum(w for *_, w in s) for s in sides.values())
    for s in sides.values():  # the last room of the shorter side takes up the rest
        if s:
            s[-1][2] += length - sum(w for *_, w in s)
    length = max(length, MIN_WIDTH)
    z_corridor = (DEPTH, DEPTH + CORRIDOR)
    outlines: list[tuple[str, str, tuple]] = [("corridor", "hallway", (0.0, z_corridor[0], length, z_corridor[1]))]
    doors = []
    for side, (z0, z1), wall_z in (("south", (0.0, DEPTH), DEPTH), ("north", (z_corridor[1], z_corridor[1] + DEPTH),
                                                                         z_corridor[1])):
        x = 0.0
        for rid, t, w in sides[side]:
            outlines.append((rid, LABELS[t], (x, z0, x + w, z1)))
            cx = x + w / 2
            doors.append((cx - DOOR / 2, wall_z - WALL - CELL / 2, cx + DOOR / 2, wall_z + WALL + CELL / 2))
            x += w
    zc = sum(z_corridor) / 2
    porch = (-PORCH[0], zc - PORCH[1] / 2, -WALL, zc + PORCH[1] / 2)
    doors.append((-WALL - CELL / 2, zc - DOOR / 2, WALL + CELL / 2, zc + DOOR / 2))  # the front door
    polys = [Room(rid, label, _rect(x0 + WALL, z0 + WALL, x1 - WALL, z1 - WALL)) for rid, label, (x0, z0, x1, z1)
             in outlines]
    polys.append(Room("porch", "porch/terrace/deck/driveway", _rect(*porch), outdoor=True))
    origin = (-PORCH[0], 0.0)
    width, height = round((length + PORCH[0]) / CELL), round((2 * DEPTH + CORRIDOR) / CELL)
    grid = Grid(origin=origin, cell=CELL, width=width, height=height, walkable=bytearray(width * height))
    for r in polys:
        xs, zs = [x for x, _ in r.polygon], [z for _, z in r.polygon]
        _fill(grid, min(xs), min(zs), max(xs), max(zs))
    for box in doors:
        _fill(grid, *box)
    name = scene or f"generated/{len(types)}-rooms-seed{seed}"
    return WorldMap(scene=name, rooms=polys, grid=grid)


def _rect(x0, z0, x1, z1):
    return ((round(x0, 3), round(z0, 3)), (round(x1, 3), round(z0, 3)), (round(x1, 3), round(z1, 3)),
            (round(x0, 3), round(z1, 3)))


def _fill(grid: Grid, x0: float, z0: float, x1: float, z1: float) -> None:
    """Every cell whose centre lies in the box becomes walkable."""
    i0 = max(0, math.floor((x0 - grid.origin[0]) / grid.cell - 0.5))
    j0 = max(0, math.floor((z0 - grid.origin[1]) / grid.cell - 0.5))
    for j in range(j0, grid.height):
        z = grid.origin[1] + (j + 0.5) * grid.cell
        if z >= z1:
            break
        if z < z0:
            continue
        for i in range(i0, grid.width):
            x = grid.origin[0] + (i + 0.5) * grid.cell
            if x >= x1:
                break
            if x >= x0:
                grid.walkable[j * grid.width + i] = 1


# -- the request and its devices (spec §1, §3) ------------------------------------------------------------------
REQUEST_FORMAT = "vesper-request/1"
PROTOCOLS = ("zigbee", "tasmota", "matter", "zwave")  # on the hub first (Matter with --matter-interface, plan W7a3)
RULES = {"light": "ceiling", "smoke_sensor": "ceiling", "gas_sensor": "ceiling", "air_quality_sensor": "ceiling",
         "motion_sensor": "corner", "contact_sensor": "door", "lock": "door", "switch": "wall", "button": "wall",
         "thermostat": "wall", "plug": "floor"}  # anything else: a table position
SHORT = {"motion_sensor": "motion", "contact_sensor": "contact", "smoke_sensor": "smoke", "gas_sensor": "gas",
         "climate_sensor": "climate", "leak_sensor": "leak", "air_quality_sensor": "air"}
REQUEST_FIELDS = {"format", "rooms", "program", "scene", "seed", "devices", "residents", "furnish", "site"}
DEVICE_FIELDS = {"product", "category", "at", "per", "except", "count", "protocol"}


@dataclass
class Request:
    path: Path
    program: list[str]
    seed: int
    devices: list[dict]
    scene: str | None = None  # an HSSD scene id: the home is that real house, not a generated layout
    furnish: bool = True  # each room gets its furniture (vesper.lab.furnishing), solid on the walking map
    site: dict | None = None  # where and when the home is (vesper.env.site), written to home.yaml as given


def load_request(path: Path) -> Request:
    path = Path(path)
    try:
        spec = yaml.safe_load(path.read_text()) or {}
    except OSError as e:
        raise GenerateError(f"{path}: cannot read it: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise GenerateError(f"{path.name}: not valid YAML: {e}") from e
    if not isinstance(spec, dict):
        raise GenerateError(f"{path.name}: not a mapping")
    if spec.get("format") != REQUEST_FORMAT:
        raise GenerateError(f"format: {spec.get('format')!r} is not {REQUEST_FORMAT}")
    unknown = sorted(set(spec) - REQUEST_FIELDS)
    if unknown:
        raise GenerateError(f"{unknown[0]!r} is not one of a request's fields")
    scene = spec.get("scene")
    if scene is not None:
        if isinstance(scene, int) and not isinstance(scene, bool):
            scene = str(scene)
        if not isinstance(scene, str) or not scene.isdigit():
            raise GenerateError(f"scene: {scene!r} is not an HSSD scene id (vesper lab scenes lists them)")
        if spec.get("rooms") is not None or spec.get("program") is not None:
            raise GenerateError("a request names a scene or rooms (or a program), not both")
    if spec.get("program") is not None:
        types = spec["program"]
        if not isinstance(types, list) or not types:
            raise GenerateError("program: a list of room types")
    else:
        types = program(spec.get("rooms", 5))
    seed = spec.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GenerateError(f"seed: {seed!r} is not a whole number")
    devices = spec.get("devices") or []
    if not isinstance(devices, list) or not all(isinstance(d, dict) for d in devices):
        raise GenerateError("devices: a list of mappings")
    furnish = spec.get("furnish", True)
    if not isinstance(furnish, bool):
        raise GenerateError(f"furnish: {furnish!r} is not true or false")
    return Request(path, list(types), seed, devices, scene, furnish, site_of(spec.get("site")))


def site_of(raw) -> dict | None:
    """A request's site, checked (vesper.env.site) and with a date: today's when it names none, so the home keeps
    the day it was made for."""
    import datetime as dt

    from vesper.env.site import SiteError, parse_site
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GenerateError("site: a mapping (a city, or lat, lon and tz) with an optional date")
    raw = {**raw, "date": str(raw.get("date") or dt.date.today().isoformat())}
    try:
        parse_site(raw)
    except SiteError as e:
        raise GenerateError(str(e)) from e
    return raw


def generate_home(request: Request, catalog: dict, out: Path, export=None) -> None:
    """Write the home (map.json and home.yaml) into `out`, a new directory. A request naming an HSSD scene gets that
    house, exported by the world service in the Habitat environment (`export(scene, out)` stands in for it in tests)."""
    if request.scene is not None:
        return _house(request, catalog, Path(out), export or _export_scene)
    from vesper.lab.windows import plan_windows, write_windows
    world_map = floor_plan(request.program, request.seed)
    devices = place_devices(world_map, request, catalog)
    out = Path(out)
    out.mkdir(parents=True)
    world_map.save(out / "map.json")
    write_windows(out, plan_windows(world_map))
    indoor = [r.id for r in world_map.rooms if not r.outdoor and r.id != "corridor"]
    start = "living" if "living" in indoor else indoor[0]
    lines = ["# Generated by `vesper lab generate` (home-generator spec, plan W9): a synthetic layout, not a real",
             f"# home; {len(indoor)} rooms, seed {request.seed}; every device's position is by rule (assumed).",
             "version: 2", f"scene: {world_map.scene}", "map: map.json", f"seed: {request.seed}",
             'start_time: "07:00"', f"avatar: {{model: female_0, start: {{room: {start}}}}}", "doors: []"]
    lines += _site_lines(request)
    lines.append("devices:" if devices else "devices: []")
    lines += [f"  - {yaml.safe_dump(d, default_flow_style=True, sort_keys=False, width=1000).strip()}"
              for d in devices]
    (out / "home.yaml").write_text("\n".join(lines) + "\n")
    if request.furnish:
        _furnish(out, catalog)


def _site_lines(request: Request) -> list[str]:
    if request.site is None:
        return []
    return [f"site: {yaml.safe_dump(request.site, default_flow_style=True, sort_keys=False, width=1000).strip()}"]


def _furnish(out: Path, catalog: dict) -> None:
    """Furnish the generated home (vesper.lab.furnishing): `furniture.json` beside it, the floor under each solid
    piece taken off `map.json`, the plan without furniture kept as `map.original.json` (as `vesper lab renav`)."""
    import json
    import shutil

    from vesper.lab.furnishing import FORMAT, block, furnish
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    home = load_lab_home(out / "home.yaml", catalog)
    items = furnish(home.map, home.devices, home.avatar["start"], load_windows(out))
    shutil.copyfile(out / "map.json", out / "map.original.json")
    block(home.map, items)
    home.map.save(out / "map.json")
    (out / "furniture.json").write_text(json.dumps({"format": FORMAT, "items": items}, indent=1) + "\n")


def _export_scene(scene: str, out: Path) -> None:
    from vesper.lab import scenes
    try:
        hssd = scenes.hssd_dir(None)
        info = scenes.scene_info(hssd, scene)
        if info.missing:
            raise GenerateError(f"scene {scene} still needs {len(info.missing)} file(s) in {hssd}, such as "
                                f"{', '.join(info.missing[:2])}; download the scene first")
        scenes.export_scene(hssd, scene, out, scenes.habitat_python(None))
    except scenes.SceneError as e:
        raise GenerateError(str(e)) from e


def _house(request: Request, catalog: dict, out: Path, export) -> None:
    """A real furnished house from HSSD, with the request's devices placed in its rooms by the same rules."""
    from vesper.lab import scenes
    out.mkdir(parents=True)
    export(request.scene, out)
    world_map = WorldMap.load(out / "map.json")
    try:
        openings = scenes.read_openings(out / "openings.json")["openings"]
    except scenes.SceneError as e:
        raise GenerateError(str(e)) from e
    doors = scenes.doors_from_openings(world_map, openings)
    from vesper.lab.windows import from_openings, write_windows
    write_windows(out, from_openings(world_map, openings))
    devices = place_devices(world_map, request, catalog)
    indoor = [r.id for r in world_map.rooms if not r.outdoor]
    lines = [f"# A home in HSSD scene {request.scene}, a real furnished house (CC BY-NC 4.0: keep this home out of",
             f"# public repositories); {len(indoor)} rooms; every device's position is by rule (assumed), seed "
             f"{request.seed}.", "version: 2", f"scene: {world_map.scene}", "map: map.json", f"seed: {request.seed}",
             'start_time: "07:00"', f"avatar: {{model: female_0, start: {{room: {scenes.start_room(world_map)}}}}}",
             "doors:" if doors else "doors: []"]
    lines += [f"  - {{doorway: {d}, open: false}}" for d in doors]
    lines += _site_lines(request)
    lines.append("devices:" if devices else "devices: []")
    lines += [f"  - {yaml.safe_dump(d, default_flow_style=True, sort_keys=False, width=1000).strip()}"
              for d in devices]
    (out / "home.yaml").write_text("\n".join(lines) + "\n")


def place_devices(world_map: WorldMap, request: Request, catalog: dict) -> list[dict]:
    rng = random.Random(f"vesper-generate-devices:{request.seed}")
    rooms = [r for r in world_map.rooms if not r.outdoor and r.id != "corridor"]
    types = {r.id: _type_of(r.id) for r in rooms}
    out: list[dict] = []
    per_room: dict[tuple[str, str], int] = {}
    for k, d in enumerate(request.devices):
        where = f"devices[{k}]"
        unknown = sorted(set(d) - DEVICE_FIELDS)
        if unknown:
            raise GenerateError(f"{where}: {unknown[0]!r} is not one of a device's fields")
        product = _product(d, catalog, rng, where)
        category = catalog[product]["identity"].get("category")
        rule = RULES.get(category, "table")
        targets = _targets(d, world_map, rooms, types, rule, where)
        switch = category == "light" and switchable(catalog[product])
        for target in targets if rule == "door" else _beside(targets, world_map):
            out.append(_place(product, category, rule, target, world_map, per_room, out, switch))
    return out


def switchable(record: dict) -> bool:
    """The product has an on/off a plain wall switch could drive (most lights; not a scene controller)."""
    from vesper.lab.rules import toggle_command
    from vesper.twins.twin import Twin
    return toggle_command(Twin.from_vds(record)) is not None


def _product(d: dict, catalog: dict, rng: random.Random, where: str) -> str:
    if "product" in d:
        if d["product"] not in catalog:
            raise GenerateError(f"{where}.product: {d['product']!r} is not in the catalog")
        return d["product"]
    category = d.get("category")
    found = [pid for pid, r in catalog.items() if r["identity"].get("category") == category
             and (d.get("protocol") is None or pid.split(":")[0] == d["protocol"])]
    if not found:
        known = sorted({r["identity"].get("category") for r in catalog.values()} - {None})
        raise GenerateError(f"{where}.category: no product of category {category!r}; choose one of "
                            f"{', '.join(known)}")
    def rank(pid):  # a light its wall switch can switch, then sourced at L1, then the protocols on the hub
        reached = (catalog[pid].get("fidelity") or {}).get("reached") or {}
        proto = pid.split(":")[0]
        return (category == "light" and not switchable(catalog[pid]), not reached.get("L1", False),
                PROTOCOLS.index(proto) if proto in PROTOCOLS else 9)
    best = min(rank(p) for p in found)
    return rng.choice(sorted(p for p in found if rank(p) == best))


def _targets(d: dict, world_map: WorldMap, rooms, types: dict, rule: str, where: str) -> list:
    """Where each device of the entry goes: rooms (Room) or doorways (Doorway)."""
    if d.get("per") is not None and d["per"] != "room":
        raise GenerateError(f"{where}.per: {d['per']!r} is not room")
    skip = set(d.get("except") or [])
    if d.get("per") == "room":
        chosen = [r for r in rooms if r.id not in skip and not any(_is_type(types[r.id], t) for t in skip)]
    elif d.get("count") is not None:
        n = d["count"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise GenerateError(f"{where}.count: {n!r} is not a whole number of devices")
        chosen = [rooms[i % len(rooms)] for i in range(n)]
    elif d.get("at") is not None:
        at = d["at"]
        outdoor = [w for w in world_map.doorways if w.outdoor]
        if at == "front_door":
            return outdoor[:1]
        if at == "exterior_doors":
            return outdoor
        if any(w.id == at for w in world_map.doorways):
            return [w for w in world_map.doorways if w.id == at]
        if at in ("hall", "corridor"):  # a generated home's corridor, or a house's hallway
            chosen = ([r for r in world_map.rooms if r.id == "corridor"]
                      or [r for r in rooms if types[r.id] in ("hall", "hallway", "corridor", "entryway")][:1]
                      or rooms[:1])
        else:
            chosen = [r for r in rooms if r.id == at] or [r for r in rooms if _is_type(types[r.id], at)][:1]
        if not chosen:
            raise GenerateError(f"{where}.at: {at!r} is not a room, a room type, a doorway, front_door or "
                                "exterior_doors")
    else:
        chosen = rooms[:1]
    if rule == "door":  # a contact sensor or a lock goes on the room's door
        return [next(w for w in world_map.doorways if r.id in w.rooms) for r in chosen]
    return chosen


def _beside(targets: list, world_map: WorldMap) -> list:
    """A device that does not go on a door, asked for at one (a doorbell at the front door): the door's indoor
    room, where its rule places it beside the door."""
    out = []
    for t in targets:
        if isinstance(t, Room):
            out.append(t)
            continue
        out.append(next(world_map.room(r) for r in t.rooms if not world_map.room(r).outdoor))
    return out


def _place(product, category, rule, target, world_map: WorldMap, per_room: dict, out: list,
           switch: bool = True) -> dict:
    short = SHORT.get(category, category)
    taken = {d["name"] for d in out}
    if rule == "door":
        stem = "front_door" if target.outdoor else target.id.replace("-", "_")
        name = _unique(f"{stem}_{short}", taken)
        return {"product": product, "name": name, "at": {"door": target.id}}
    room = target
    k = per_room.get((room.id, rule), 0)
    per_room[(room.id, rule)] = k + 1
    name = _unique(f"{room.id}_{short}", taken)
    xs, zs = [x for x, _ in room.polygon], [z for _, z in room.polygon]
    x0, z0, x1, z1 = min(xs), min(zs), max(xs), max(zs)
    door = next((w for w in world_map.doorways if room.id in w.rooms), None)
    if rule == "ceiling":
        at = {"room": room.id, "mount": "ceiling"}
        if k:
            at["xy"] = [round(x0 + (x1 - x0) * (0.25 + 0.5 * (k % 2)), 2), round(z0 + (z1 - z0) * 0.5, 2)]
        if category == "light" and switch:  # its plain wall switch (PI 2026-10-31), by
            # the entrance; a second light's beside it. A light with no on/off (a scene panel) gets none.
            entry = next((w for w in world_map.doorways if room.id in w.rooms and w.outdoor), door)
            nth = sum(1 for d in out if d["at"].get("room") == room.id and "wall_switch" in d["at"])
            at["wall_switch"] = _by_door(entry, room, (x0, z0, x1, z1), SWITCH_M + 0.12 * nth)
    elif rule == "corner":  # in the corner farthest from the door, watching the room and the door
        corners = [(x0 + 0.3, z0 + 0.3), (x1 - 0.3, z0 + 0.3), (x1 - 0.3, z1 - 0.3), (x0 + 0.3, z1 - 0.3)]
        far = max(corners, key=lambda c: (c[0] - door.center[0]) ** 2 + (c[1] - door.center[1]) ** 2) \
            if door else corners[0]
        at = {"room": room.id, "mount": "ceiling", "xy": [round(far[0], 2), round(far[1], 2)]}
    elif rule == "wall":  # beside the door, past the lights' switches, inside the room, within reach from it
        box = (x0, z0, x1, z1)
        xy = _by_door(door, room, box, WALL_M + 0.3 * k)
        if _by_door(door, room, box, SWITCH_M) == xy or _by_door(door, room, box, WALL_M + 0.3 * k + 0.01) == xy:
            xy = _by_door(door, room, box, -(SWITCH_M + 0.3 * k))  # no wall left on the latch side: the other side
        at = {"room": room.id, "mount": "wall", "xy": xy}
    elif rule == "floor":  # along the walls, spread round the room
        spots = [((x0 + x1) / 2, z0 + 0.2), (x1 - 0.2, (z0 + z1) / 2), ((x0 + x1) / 2, z1 - 0.2),
                 (x0 + 0.2, (z0 + z1) / 2)]
        p = spots[k % 4]
        at = {"room": room.id, "mount": "floor", "xy": [round(p[0], 2), round(p[1], 2)]}
    else:
        c = ((x0 + x1) / 2 + 0.4 * (k % 3), (z0 + z1) / 2)
        at = {"room": room.id, "mount": "table", "xy": [round(c[0], 2), round(c[1], 2)]}
    for key in ("xy", "wall_switch"):  # the rules work on the room's box: a house's room is seldom one
        if key in at:
            at[key] = _inside(room, at[key])
    return {"product": product, "name": name, "at": at}


def _inside(room: Room, xy: list, step: float = 0.1, margin: float = 0.15) -> list:
    """`xy` if it is in the room, else the nearest point at least `margin` inside it (on a `step` grid)."""
    if point_in_polygon((xy[0], xy[1]), room.polygon):
        return xy
    xs, zs = [x for x, _ in room.polygon], [z for _, z in room.polygon]
    best, best_d = None, float("inf")
    nx, nz = int((max(xs) - min(xs)) / step) + 1, int((max(zs) - min(zs)) / step) + 1
    for i in range(nx):
        for j in range(nz):
            p = (min(xs) + i * step, min(zs) + j * step)
            d = (p[0] - xy[0]) ** 2 + (p[1] - xy[1]) ** 2
            if d < best_d and all(point_in_polygon((p[0] + dx, p[1] + dz), room.polygon)
                                  for dx, dz in ((0, 0), (margin, 0), (-margin, 0), (0, margin), (0, -margin))):
                best, best_d = p, d
    return [round(best[0], 2), round(best[1], 2)] if best else xy


SWITCH_M = 0.6  # a light's wall switch: from the doorway's centre, 0.15 m past a 0.9 m door's latch-side frame
WALL_M = 1.0  # a wall device (a switch, a button, a thermostat): further along the same wall


def _by_door(door, room, box, offset: float) -> list:
    """A point on the room's wall beside `door`, `offset` along the wall on its latch side (the 3D view hinges a door
    at its low end, so the latch is at the high end: an open door's leaf never covers it), 0.15 m inside the room."""
    x0, z0, x1, z1 = box
    if door is None:
        return [round(min(x0 + 0.15 + offset, x1 - 0.15), 2), round(z0 + 0.15, 2)]
    cx, cz = door.center
    if abs(cx - x0) < 0.2 or abs(cx - x1) < 0.2:  # a door in a wall that runs along z
        x = x0 + 0.15 if abs(cx - x0) < abs(cx - x1) else x1 - 0.15
        return [round(x, 2), round(min(max(cz + offset, z0 + 0.15), z1 - 0.15), 2)]
    z = z0 + 0.15 if abs(cz - z0) < abs(cz - z1) else z1 - 0.15
    return [round(min(max(cx + offset, x0 + 0.15), x1 - 0.15), 2), round(z, 2)]


def _unique(name: str, taken: set) -> str:
    if name not in taken:
        return name
    k = 2
    while f"{name}_{k}" in taken:
        k += 1
    return f"{name}_{k}"


def _is_type(room_type: str, wanted: str) -> bool:
    """A generated home's room type names a house's room that starts with it: living, a living_room."""
    return room_type == wanted or room_type.startswith(f"{wanted}_") or room_type.startswith(f"{wanted}room")


def _type_of(room_id: str) -> str:
    return room_id.rsplit("_", 1)[0] if room_id.rsplit("_", 1)[-1].isdigit() else room_id


# -- the starter set and batches (spec §1, §6) ------------------------------------------------------------------
STARTER = [{"category": "lock", "at": "front_door"}, {"category": "contact_sensor", "at": "front_door"},
           {"category": "light", "per": "room"}, {"category": "motion_sensor", "per": "room", "except": ["bathroom"]},
           {"category": "thermostat", "at": "hall"}, {"category": "smoke_sensor", "at": "kitchen"}]


def starter(types: list[str], catalog: dict) -> tuple[list[dict], list[str]]:
    """The devices of a home asked for by its number of rooms only, and the starter categories left out (none of
    that category in the catalog, or no room of that type)."""
    have = {r["identity"].get("category") for r in catalog.values()}
    keep, left = [], []
    for d in STARTER:
        if d["category"] not in have or (d.get("at") not in (None, "front_door", "hall") and d["at"] not in types):
            left.append(d["category"])
        else:
            keep.append(dict(d))
    return keep, left


def summary_row(name: str, home) -> dict:
    """One home of a batch: its rooms, floor area, devices, and those out of their network's range."""
    from vesper.lab.network import out_of_range
    indoor = [r for r in home.map.rooms if not r.outdoor and r.id != "corridor"]

    def area(r):
        xs, zs = [x for x, _ in r.polygon], [z for _, z in r.polygon]
        return (max(xs) - min(xs)) * (max(zs) - min(zs))
    far = []
    if home.network is not None:
        for medium in ("zigbee", "wifi"):
            far += out_of_range(home.network, home.map, home.doors, medium=medium)
    return {"home": name, "rooms": len(indoor), "area_m2": round(sum(area(r) for r in home.map.rooms
                                                                       if not r.outdoor), 1),
            "devices": len(home.devices), "out_of_range": len(far)}
