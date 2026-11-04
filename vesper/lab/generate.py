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


# -- a home from a floor plan (floor-plan import, task A2) ------------------------------------------------------
# A plan's room types (vesper.lab.plan.ROOM_TYPES): the label each gets on the map (what the furnisher reads, so an
# "other" room gets no furniture of a kind) and the stem of its id (what the device placer reads)
PLAN_LABELS = {**LABELS, "corridor": "hallway", "hall": "hall", "other": "other"}
PLAN_STEMS = {"other": "room"}
PORCH_LABEL = "porch/terrace/deck/driveway"
PORCH_TRIES = ((2.0, 3.0), (2.0, 2.0), (1.5, 2.0), (1.0, 1.5))  # (depth, width): the first that fits outside
MARGIN = 0.3  # the map's floor grid reaches this far past the plan, so every outer wall has cells
_TOL = 1e-6


def typed_ids(plan_m: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    """The plan with its rooms renamed as a generated home's are, by type (living, bedroom_1, bedroom_2, ...: the
    device placer and a request's `at:` read a room's type from its id), and (new id, plan id, name) per room."""
    import copy
    plan = copy.deepcopy(plan_m)
    stems = [PLAN_STEMS.get(r["type"], r["type"]) for r in plan["rooms"]]
    seen: dict[str, int] = {}
    rename, out = {}, []
    for r, stem in zip(plan["rooms"], stems, strict=True):
        seen[stem] = seen.get(stem, 0) + 1
        new = stem if stems.count(stem) == 1 else f"{stem}_{seen[stem]}"
        rename[r["id"]] = new
        out.append((new, r["id"], r["name"]))
        r["id"] = new
    for d in plan.get("doors") or []:
        d["rooms"] = [s if s == "outside" else rename[s] for s in d["rooms"]]
    for w in plan.get("windows") or []:
        w["room"] = rename[w["room"]]
    return plan, out


def floor_plan_from(plan_m: dict, scene: str) -> WorldMap:
    """The map of a rectified plan in metres (vesper.lab.plan): its rooms keep their ids, each room's polygon is its
    outline taken in by half a wall (WALL), as a generated room's is, so a wall between two rooms is solid floor-less
    cells; each door opens the wall across its width; an outside door gets an outdoor room before it (the entrance's
    `porch`, PORCH deep and wide when it fits, any other `porch_2`, ...). Rooms may be any simple polygon."""
    from vesper.lab.plan import PlanError, load_plan
    try:
        load_plan(plan_m)
    except PlanError as e:
        raise GenerateError(str(e)) from None
    if plan_m["units"] != "m":
        raise GenerateError("the plan is in pixels: scale it to metres first (vesper.lab.plan.scale_plan)")
    outlines = {r["id"]: _outline(r["polygon"]) for r in plan_m["rooms"]}
    rooms = []
    for r in plan_m["rooms"]:
        inner = _inset(outlines[r["id"]], WALL)
        if inner is None:
            raise GenerateError(f"room {r['id']!r}: a part of it is narrower than its walls ({2 * WALL:.1f} m)")
        rooms.append(Room(r["id"], PLAN_LABELS[r["type"]], tuple((round(x, 3), round(z, 3)) for x, z in inner)))
    doors = plan_m.get("doors") or []
    entrance = plan_m.get("entrance")
    order = ([entrance] if entrance is not None else []) + [k for k in range(len(doors)) if k != entrance]
    openings, porches, joins = [], [], {}
    taken = set(outlines)
    for k in order:
        d = doors[k]
        sides = [s for s in d["rooms"] if s != "outside"]
        where = min(((s, e) for s in sides for e in _edges(outlines[s])), key=lambda se: _gap(se[1], d["at"]))
        (a, b), along = where[1], _unit(where[1])
        openings.append((tuple(d["at"]), along, d["width"]))
        pair = set(sides)
        if "outside" in d["rooms"]:
            pid = _unique("porch", taken)
            taken.add(pid)
            inward = _inward(outlines[sides[0]], (a, b))
            porch = _porch(tuple(d["at"]), along, (-inward[0], -inward[1]), d["width"],
                           [*outlines.values(), *(p.polygon for p in porches)])
            if porch is None:
                raise GenerateError(f"door {k} (from {sides[0]!r} to the outside): another room stands where its "
                                    "porch would be; check the plan")
            porches.append(Room(pid, PORCH_LABEL, porch, outdoor=True))
            pair.add(pid)
        joins[k] = pair
    every = [*rooms, *porches]
    xs = [x for poly in [*outlines.values(), *(p.polygon for p in porches)] for x, _ in poly]
    zs = [z for poly in [*outlines.values(), *(p.polygon for p in porches)] for _, z in poly]
    ox, oz = (round(math.floor((v - MARGIN) / CELL + _TOL) * CELL, 6) for v in (min(xs), min(zs)))
    width, height = math.ceil((max(xs) + MARGIN - ox) / CELL), math.ceil((max(zs) + MARGIN - oz) / CELL)
    grid = Grid(origin=(ox, oz), cell=CELL, width=width, height=height, walkable=bytearray(width * height))
    for r in every:
        _fill_polygon(grid, r.polygon)
    for at, along, w in openings:
        _fill_door(grid, at, along, w)
    from vesper.lab.worldmap import MapError
    try:
        world_map = WorldMap(scene=scene, rooms=every, grid=grid)
    except MapError as e:
        raise GenerateError(str(e)) from None
    found = [set(d.rooms) for d in world_map.doorways]
    for k, pair in sorted(joins.items()):
        if pair not in found:
            raise GenerateError(f"door {k} does not open between {' and '.join(sorted(pair))} on the map: check "
                                "that it stands on the wall they share")
    return world_map


def _outline(poly) -> list[tuple[float, float]]:
    pts = [(float(x), float(z)) for x, z in poly]
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    return pts


def _edges(ring):
    return [(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring))]


def _unit(edge):
    (ax, az), (bx, bz) = edge
    n = math.hypot(bx - ax, bz - az)
    return (bx - ax) / n, (bz - az) / n


def _gap(edge, p) -> float:
    """The distance from p to the segment."""
    (ax, az), (bx, bz) = edge
    dx, dz = bx - ax, bz - az
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - az) * dz) / (dx * dx + dz * dz)))
    return math.hypot(p[0] - ax - t * dx, p[1] - az - t * dz)


def _signed(ring) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) / 2


def _inward(ring, edge):
    """The unit normal of one of the ring's edges, pointing into the ring."""
    ux, uz = _unit(edge)
    s = 1.0 if _signed(ring) > 0 else -1.0
    return -uz * s, ux * s


def _inset(ring, d: float):
    """The polygon `d` inside the ring (each edge moved in by d, corners where the moved edges meet), or None when a
    part of the room is too narrow for that (an edge would turn round, or the result would cross itself)."""
    from vesper.lab.plan import _polygon_problem
    n = len(ring)
    lines = []
    for e in _edges(ring):
        nx, nz = _inward(ring, e)
        lines.append(((e[0][0] + d * nx, e[0][1] + d * nz), _unit(e)))
    out = []
    for i in range(n):
        (p, t), (q, u) = lines[i - 1], lines[i]
        cross = t[0] * u[1] - t[1] * u[0]
        if abs(cross) < 1e-9:  # two edges on one line: the corner just moves in
            out.append(q)
            continue
        lam = ((q[0] - p[0]) * u[1] - (q[1] - p[1]) * u[0]) / cross
        out.append((p[0] + lam * t[0], p[1] + lam * t[1]))
    if _polygon_problem([list(p) for p in out]) is not None:
        return None
    if _signed(out) * _signed(ring) <= 0 or abs(_signed(out)) >= abs(_signed(ring)):
        return None
    for (a, b), e in zip(_edges(out), _edges(ring), strict=True):  # every edge keeps its direction
        if math.dist(a, b) > 1e-9 and sum(x * y for x, y in zip(_unit((a, b)), _unit(e), strict=True)) <= 0:
            return None
    return out


def _porch(at, along, out, door_w: float, others):
    """An outdoor room before an outside door: from WALL to `depth` out from the wall, `width` along it, centred on
    the door; the first of PORCH_TRIES that stands clear of every room."""
    for depth, width in PORCH_TRIES:
        width = max(width, door_w + 0.4)
        corners = []
        for s, t in ((-width / 2, WALL), (width / 2, WALL), (width / 2, depth), (-width / 2, depth)):
            corners.append((round(at[0] + s * along[0] + t * out[0], 3), round(at[1] + s * along[1] + t * out[1], 3)))
        if _clear(corners, others):
            return tuple(corners)
    return None


def _clear(poly, others) -> bool:
    """Whether the polygon shares no floor with any of the others (sampled every 5 cm, just inside it)."""
    xs, zs = [x for x, _ in poly], [z for _, z in poly]
    steps = 0.05
    nx, nz = int((max(xs) - min(xs)) / steps), int((max(zs) - min(zs)) / steps)
    for i in range(nx):
        for j in range(nz):
            p = (min(xs) + (i + 0.5) * steps, min(zs) + (j + 0.5) * steps)
            if point_in_polygon(p, poly) and any(point_in_polygon(p, o) for o in others):
                return False
    return not any(point_in_polygon(c, poly) for o in others for c in o)


def _fill_polygon(grid: Grid, poly) -> None:
    """Every cell whose centre lies in the polygon becomes walkable."""
    xs, zs = [x for x, _ in poly], [z for _, z in poly]
    lo, hi = grid.cell_of(min(xs), min(zs)) or (0, 0), grid.cell_of(max(xs), max(zs)) or (grid.width - 1,
                                                                                           grid.height - 1)
    for j in range(max(0, lo[1]), min(grid.height, hi[1] + 1)):
        for i in range(max(0, lo[0]), min(grid.width, hi[0] + 1)):
            if point_in_polygon(grid.center(i, j), poly):
                grid.walkable[j * grid.width + i] = 1


def _fill_door(grid: Grid, at, along, width: float) -> None:
    """The cells of a door through its wall: `width` along the wall (a half-open span, as `_fill`), and a cell past
    each face of the wall, so the opening meets the floor on both sides."""
    reach = width / 2 + 2 * CELL
    lo, hi = grid.cell_of(at[0] - reach, at[1] - reach), grid.cell_of(at[0] + reach, at[1] + reach)
    lo, hi = lo or (0, 0), hi or (grid.width - 1, grid.height - 1)
    for j in range(max(0, lo[1]), min(grid.height, hi[1] + 1)):
        for i in range(max(0, lo[0]), min(grid.width, hi[0] + 1)):
            cx, cz = grid.center(i, j)
            u = (cx - at[0]) * along[0] + (cz - at[1]) * along[1]
            v = -(cx - at[0]) * along[1] + (cz - at[1]) * along[0]
            if -width / 2 - _TOL <= u < width / 2 - _TOL and abs(v) < WALL + CELL - _TOL:
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
        raise GenerateError("site: a mapping (a place, a city, or lat, lon and tz) with an optional date")
    raw = {**raw, "date": str(raw.get("date") or dt.date.today().isoformat())}
    if "place" in raw:  # looked up once, now; the home keeps the answer and never needs the network
        from vesper.env import geocode
        try:
            found = geocode.geocode(raw["place"])
        except geocode.GeocodeError as e:
            raise GenerateError(str(e)) from e
        raw = {**found, **{k: v for k, v in raw.items() if k != "place"}}
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


def generate_from_plan(plan_m: dict, request: Request, catalog: dict, out: Path, source: str = "plan.json") -> None:
    """Write the home of a rectified plan in metres into `out`, a new directory, as `generate_home` writes a generated
    one: its rooms renamed by type (`typed_ids`, listed in home.yaml's header), the map (`floor_plan_from`), the plan's
    windows and rule windows where a room has none (vesper.lab.windows.from_plan), the request's devices placed by
    rule, the built-in furniture, and the plan itself (`plan.json`, with the map's room ids). `source` is the plan's
    file name: the scene is `imported/<its stem>`. The request's program is not used: the rooms are the plan's."""
    import json

    from vesper.lab.plan import PlanError, check_house
    from vesper.lab.windows import from_plan, write_windows
    if request.scene is not None:
        raise GenerateError("an imported home's rooms come from the plan: a request for it names no scene")
    try:
        problems = check_house(plan_m)
    except PlanError as e:
        raise GenerateError(str(e)) from None
    if problems:
        raise GenerateError("the plan is not a house: " + "; ".join(problems))
    plan, names = typed_ids(plan_m)
    world_map = floor_plan_from(plan, f"imported/{Path(source).stem}")
    devices = place_devices(world_map, request, catalog)
    windows = from_plan(plan, world_map)
    out = Path(out)
    out.mkdir(parents=True)
    world_map.save(out / "map.json")
    write_windows(out, windows)
    (out / "plan.json").write_text(json.dumps(plan, indent=1) + "\n")
    indoor = [r.id for r in world_map.rooms if not r.outdoor]
    entrance = plan.get("entrance")
    inside = [s for s in plan["doors"][entrance]["rooms"] if s != "outside"] if entrance is not None else []
    start = "living" if "living" in indoor else (inside or indoor)[0]
    one_line = lambda text: " ".join(str(text).split())  # noqa: E731  a name never breaks the comment
    lines = [f"# Imported from {one_line(Path(source).name)} by `vesper lab import` (floor-plan import): {len(indoor)} "
             "rooms, their doors",
             "# and windows as the plan draws them, its walls straightened; the porch, windows marked source: rule,",
             f"# the furniture and every device's position are by rule (assumed), seed {request.seed}. The rooms (the "
             "plan's id and name):"]
    for new, old, name in names:
        lines.append(f"#   {new}: {one_line(old)}" + (f" ({one_line(name)})" if one_line(name) else ""))
    lines += ["version: 2", f"scene: {world_map.scene}", "map: map.json", f"seed: {request.seed}",
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
        if at == "front_door":  # an imported home's entrance opens onto `porch`; a back door onto porch_2, ...
            return sorted(outdoor, key=lambda w: "porch" not in w.rooms)[:1]
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
