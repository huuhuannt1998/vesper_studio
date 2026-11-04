"""Furniture a generated home gets by itself (PI 2026-11-01): free, in seconds, with no Blender and no Claude.

Each room gets the pieces a home of its kind has (a living room a sofa facing a TV, a bedroom a bed between
nightstands, a kitchen a counter run and a fridge...), each against a wall or where it belongs, at a typical size.
No piece stands within DOOR_CLEAR_M of a doorway or within DEVICE_CLEAR_M of a wall or floor device or a light's wall
switch, and none taller than a window's sill stands in front of it (a sofa, a bed or a desk may sit under one);
and a piece is kept only if, with the floor under it taken, every doorway of its room and every device a
hand reaches there can still be reached on foot, and a room's doorways stay joined by a WALKWAY_M path (an imported
plan's room often has two or more doors). `block` then takes the floor under each solid piece off the walking
map, so people walk round the furniture; the 3D view draws each piece as a model (furniture3d.js).

Rooms are taken as their bounding box (VESPER's generated rooms are rectangles); a piece is also kept inside the
room's polygon. Deterministic: the same map and devices give the same furniture.
"""
from __future__ import annotations

import math
from collections import deque

from vesper.lab.geometry import distance, point_in_polygon
from vesper.lab.windows import blocks as blocks_window

FORMAT = "vesper-furniture/1"
DOOR_CLEAR_M = 0.9  # nothing within this of a doorway's centre
DEVICE_CLEAR_M = 0.45  # nor of a wall or floor device, or a ceiling light's wall switch
WALL_GAP_M = 0.03  # a piece's back off its wall
GAP_M = 0.08  # between two pieces
AISLE_M = 0.8  # what a wall piece leaves across the room
REACH_M = 0.9  # a device stays within this of the floor people walk (rules.INTERACT_M is 1.0)
WALKWAY_M = 0.9  # a room's doorways stay joined by a path this wide (vesper.lab.layoutcheck's walkway rule)

# Each room kind's pieces, in the order they are placed: (kind, width along its wall, depth, height, where, solid).
# where: "back" (the wall facing the room's door, centred), "wall" (any wall, its ends first), "corner", "middle",
# ("before", kind, gap) in front of a piece, ("beside", kind) at a piece's side along its wall, ("facing", kind)
# against the wall across from a piece, ("under", kind) under a piece (a rug), ("around", kind) chairs round a table.
# A counter's width "run" fills its wall, up to RUN_M.
RUN_M = 3.0
SEATS = {("chair", "dining_table"), ("office_chair", "desk")}  # pulled up to it, no gap
PIECES = {
    "living": [("sofa", 2.1, 0.9, 0.85, "back", True),
               ("coffee_table", 1.0, 0.55, 0.42, ("before", "sofa", 0.45), True),
               ("rug", 2.0, 1.4, 0.01, ("under", "coffee_table"), False), ("tv_stand", 1.6, 0.45, 0.5,
               ("facing", "sofa"), True), ("armchair", 0.85, 0.85, 0.85, "wall", True),
               ("bookshelf", 0.9, 0.35, 1.9, "wall", True), ("plant", 0.45, 0.45, 1.2, "corner", True)],
    "kitchen": [("counter", "run", 0.62, 0.9, "back", True), ("fridge", 0.72, 0.72, 1.85, ("beside", "counter"), True),
                ("dining_table", 1.2, 0.8, 0.75, "middle", True), ("chair", 0.45, 0.5, 0.9, ("around", "dining_table"),
                                                                                    True)],
    "bedroom": [("bed", 1.6, 2.05, 0.55, "back", True), ("nightstand", 0.45, 0.4, 0.5, ("beside", "bed"), True),
                ("nightstand", 0.45, 0.4, 0.5, ("beside", "bed"), True), ("rug", 1.8, 1.0, 0.01, ("before", "bed", 0.1),
                                                                                False),
                ("wardrobe", 1.2, 0.6, 2.0, "wall", True), ("plant", 0.4, 0.4, 1.0, "corner", True)],
    "bathroom": [("bath", 1.7, 0.75, 0.55, "back", True), ("vanity", 0.8, 0.5, 0.85, "wall", True),
                 ("toilet", 0.4, 0.65, 0.8, "wall", True)],
    "office": [("desk", 1.4, 0.7, 0.75, "back", True), ("office_chair", 0.6, 0.6, 1.0, ("before", "desk", 0.05), True),
               ("bookshelf", 0.9, 0.35, 1.9, "wall", True), ("plant", 0.45, 0.45, 1.2, "corner", True)],
    "dining": [("dining_table", 1.8, 0.9, 0.75, "middle", True), ("chair", 0.45, 0.5, 0.9, ("around", "dining_table"),
                                                                                    True),
               ("sideboard", 1.6, 0.45, 0.8, "wall", True), ("plant", 0.45, 0.45, 1.2, "corner", True)],
    "laundry": [("washer", 0.6, 0.6, 0.85, "back", True), ("dryer", 0.6, 0.6, 0.85, ("beside", "washer"), True),
                ("cabinet", 0.8, 0.45, 0.9, "wall", True)],
    "hall": [("console", 1.0, 0.35, 0.8, "wall", True), ("plant", 0.4, 0.4, 1.0, "corner", True)],
}
KINDS = {"living": "living", "kitchen": "kitchen", "bed": "bedroom", "bath": "bathroom", "toilet": "bathroom",
         "office": "office", "study": "office", "dining": "dining", "laundry": "laundry", "hall": "hall",
         "corridor": "hall", "entry": "hall"}


def room_kind(room) -> str | None:
    """The kind of room its label says (generated or HSSD), or None (a garage, a closet: left bare)."""
    words = room.label.split("/")[0].lower()
    return next((kind for key, kind in KINDS.items() if key in words), None)


class _Room:
    def __init__(self, room, world_map, devices, windows=()):
        self.room, self.map = room, world_map
        self.windows = [w for w in windows if w["room"] == room.id]
        xs, zs = [p[0] for p in room.polygon], [p[1] for p in room.polygon]
        self.x0, self.x1, self.z0, self.z1 = min(xs), max(xs), min(zs), max(zs)
        self.doorways = [d for d in world_map.doorways if room.id in d.rooms]
        door = self.doorways[0].center if self.doorways else ((self.x0 + self.x1) / 2, self.z1)
        near = {"z0": abs(door[1] - self.z0), "z1": abs(door[1] - self.z1), "x0": abs(door[0] - self.x0),
                "x1": abs(door[0] - self.x1)}
        self.door_side = min(near, key=near.get)
        self.back = {"z0": "z1", "z1": "z0", "x0": "x1", "x1": "x0"}[self.door_side]
        self.clear = [d.wall_switch or d.position for d in devices
                      if d.room == room.id and (d.wall_switch is not None or d.mount in ("wall", "floor"))]
        self.reach = [d.wall_switch or d.position for d in devices
                      if d.room == room.id and d.mount != "door" and (d.mount != "ceiling" or d.wall_switch)]
        g = world_map.grid
        self.cells = [(i, j) for (i, j) in _cells_in(g, (self.x0, self.z0, self.x1, self.z1))
                      if point_in_polygon(g.center(i, j), room.polygon)]
        self.door_cells = [frozenset(d.cells) for d in self.doorways]

    def walls(self):
        """The room's walls, the back one first and the door's last: (side, facing into the room)."""
        order = [self.back] + [s for s in ("z0", "z1", "x0", "x1") if s not in (self.back, self.door_side)] + \
            [self.door_side]
        return [(s, {"z0": 90, "z1": 270, "x0": 0, "x1": 180}[s]) for s in order]

    def against(self, side, along, w, d):
        """A piece w wide and d deep with its back to wall `side`, its middle `along` the wall: (x, z, facing)."""
        if side == "z0":
            return along, self.z0 + WALL_GAP_M + d / 2, 90
        if side == "z1":
            return along, self.z1 - WALL_GAP_M - d / 2, 270
        if side == "x0":
            return self.x0 + WALL_GAP_M + d / 2, along, 0
        return self.x1 - WALL_GAP_M - d / 2, along, 180

    def span(self, side):
        return (self.x0, self.x1) if side in ("z0", "z1") else (self.z0, self.z1)

    def across(self, side):
        return self.z1 - self.z0 if side in ("z0", "z1") else self.x1 - self.x0


def _cells_in(g, box):
    """The grid cells whose centres lie in box (x0, z0, x1, z1)."""
    x0, z0, x1, z1 = box
    (ox, oz), c = g.origin, g.cell
    i0, i1 = max(0, int((x0 - ox) / c)), min(g.width - 1, int((x1 - ox) / c))
    j0, j1 = max(0, int((z0 - oz) / c)), min(g.height - 1, int((z1 - oz) / c))
    return [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)
            if x0 <= ox + (i + 0.5) * c <= x1 and z0 <= oz + (j + 0.5) * c <= z1]


def footprint(x, z, w, d, facing) -> tuple[float, float, float, float]:
    """The box (x0, z0, x1, z1) of a piece w wide and d deep at (x, z) facing `facing` degrees (a right angle)."""
    along_x = facing % 180 == 90  # faces along z: its width runs along x
    hw, hd = (w / 2, d / 2) if along_x else (d / 2, w / 2)
    return (x - hw, z - hd, x + hw, z + hd)


def _gap(box, p) -> float:
    x0, z0, x1, z1 = box
    return ((max(x0 - p[0], 0, p[0] - x1)) ** 2 + (max(z0 - p[1], 0, p[1] - z1)) ** 2) ** 0.5


def _overlaps(a, b, gap=GAP_M) -> bool:
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def furnish(world_map, devices, start=None, windows=()) -> list[dict]:
    """The furniture for every room of the map, as dicts (kind, room, x, z, w, d, h, facing, solid, box). The map
    is not changed: `block` takes the floor under the solid pieces. `windows`: the home's (vesper.lab.windows)."""
    g = world_map.grid
    taken = set()  # cells under the pieces placed so far
    items = []
    for room in world_map.rooms:
        kind = None if room.outdoor else room_kind(room)
        if kind is None:
            continue
        r = _Room(room, world_map, devices, windows)
        placed: dict[str, dict] = {}
        for name, w, d, h, where, solid in PIECES[kind]:
            for item in _candidates(r, name, w, d, h, where, placed):
                if _fits(r, item, items, taken, solid):
                    cells = set(_cells_in(g, item["box"])) if solid else set()
                    if solid and not _still_reachable(r, taken | cells):
                        continue
                    taken |= cells
                    items.append(item)
                    placed.setdefault(name, item)
                    if where[0] != "around":
                        break
    return items


def block(world_map, items) -> None:
    """Take the floor under each solid piece off the walking map."""
    g = world_map.grid
    for item in items:
        if item["solid"]:
            for i, j in _cells_in(g, item["box"]):
                g.walkable[j * g.width + i] = 0


def _item(r, name, x, z, w, d, h, facing, solid) -> dict:
    box = footprint(x, z, w, d, facing)
    return {"kind": name, "room": r.room.id, "x": round(x, 3), "z": round(z, 3), "w": round(w, 3), "d": round(d, 3),
            "h": h, "facing": facing % 360, "solid": solid, "box": [round(v, 3) for v in box]}


def _candidates(r, name, w, d, h, where, placed):
    """Where the piece could go, best first."""
    solid = name != "rug"
    if isinstance(where, tuple):
        found = _beside(r, name, w, d, h, where, placed, solid)
        if where[0] in ("beside", "facing"):  # no room there: any wall
            found = (*found, *_candidates(r, name, w, d, h, "wall", placed))
        if where[0] == "facing" and placed.get(where[1]) is not None:  # a TV the sofa sees: not round a corner
            found = (i for i in found if _in_sight(r.room.polygon, placed[where[1]], i))
        yield from found
        return
    yield from _on_walls(r, name, w, d, h, where, solid)


def _in_sight(polygon, a: dict, b: dict, step: float = 0.1) -> bool:
    """Whether the line between two pieces' centres stays in the room (always, in a rectangular room)."""
    n = max(1, int(distance((a["x"], a["z"]), (b["x"], b["z"])) / step))
    return all(point_in_polygon((a["x"] + (b["x"] - a["x"]) * k / n, a["z"] + (b["z"] - a["z"]) * k / n), polygon)
               for k in range(n + 1))


def _beside(r, name, w, d, h, where, placed, solid):
    """Where a piece goes relative to another already placed."""
    if True:
        how, other = where[0], placed.get(where[1])
        if other is None:
            return
        ox, oz, f = other["x"], other["z"], other["facing"]
        fx, fz = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[f]
        if how == "before":
            gap = where[2]
            depth_other = other["d"]
            yield _item(r, name, ox + fx * (depth_other / 2 + gap + d / 2), oz + fz * (depth_other / 2 + gap + d / 2),
                        w, d, h, f, solid)
        elif how == "under":
            yield _item(r, name, ox, oz, w, d, h, f, solid)
        elif how == "beside":
            side_x, side_z = -fz, fx  # along the other piece's wall
            for s in (1, -1):
                off = other["w"] / 2 + GAP_M + w / 2
                bx, bz = ox + side_x * s * off, oz + side_z * s * off
                # its back to the same wall as the other piece
                bx, bz = bx - fx * (other["d"] - d) / 2, bz - fz * (other["d"] - d) / 2
                yield _item(r, name, bx, bz, w, d, h, f, solid)
        elif how == "facing":
            side = {90: "z1", 270: "z0", 0: "x1", 180: "x0"}[f]
            lo, hi = r.span(side)
            along = ox if side in ("z0", "z1") else oz
            for a in _slide(lo + w / 2 + 0.05, hi - w / 2 - 0.05, along):
                x, z, facing = r.against(side, a, w, d)
                yield _item(r, name, x, z, w, d, h, facing, solid)
        elif how == "around":  # chairs on the table's two long sides, facing it
            long_x = other["box"][2] - other["box"][0] >= other["box"][3] - other["box"][1]
            length = max(other["w"], other["d"])
            n = max(1, int(length // 0.6))
            for k in range(n):
                t = (k + 0.5) / n - 0.5
                for s in (1, -1):
                    if long_x:
                        cx, cz = ox + t * length, oz + s * ((other["box"][3] - other["box"][1]) / 2 + 0.05 + d / 2)
                        facing = 270 if s > 0 else 90
                    else:
                        cx, cz = ox + s * ((other["box"][2] - other["box"][0]) / 2 + 0.05 + d / 2), oz + t * length
                        facing = 180 if s > 0 else 0
                    yield _item(r, name, cx, cz, w, d, h, facing, solid)


def _on_walls(r, name, w, d, h, where, solid):
    """Against a wall, in the middle, or in a corner."""
    if where == "middle":
        cx, cz = (r.x0 + r.x1) / 2, (r.z0 + r.z1) / 2
        f = {"z0": 270, "z1": 90, "x0": 180, "x1": 0}[r.back]  # its long side along the back wall
        for dx, dz in ((0, 0), (0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3), (0.6, 0), (-0.6, 0), (0, 0.6), (0, -0.6)):
            yield _item(r, name, cx + dx, cz + dz, w, d, h, f, solid)
        return
    for side, _facing in r.walls():  # "back" prefers the back wall, and takes another when that one is full
        lo, hi = r.span(side)
        width = min(RUN_M, hi - lo - 0.1 - 0.8) if w == "run" else w  # a run leaves room for the fridge
        if (w == "run" and width < 0.6) or r.across(side) - d < AISLE_M:
            continue
        a0, a1 = lo + width / 2 + 0.05, hi - width / 2 - 0.05
        if a1 < a0:
            continue
        if where == "corner":
            alongs = [a0, a1]
        elif where == "back" or w == "run":
            alongs = list(_slide(a0, a1, (lo + hi) / 2))
        else:
            alongs = list(_slide(a0, a1, a0)) + list(_slide(a0, a1, a1))
        for a in alongs:
            x, z, facing = r.against(side, a, width, d)
            yield _item(r, name, x, z, width, d, h, facing, solid)


def _slide(lo, hi, start, step=0.1):
    """Positions from `start` outwards (both ways) within [lo, hi]."""
    if hi < lo:
        return
    start = min(max(start, lo), hi)
    yield start
    k = 1
    while start - k * step >= lo - 1e-9 or start + k * step <= hi + 1e-9:
        for p in (start + k * step, start - k * step):
            if lo - 1e-9 <= p <= hi + 1e-9:
                yield p
        k += 1


def _fits(r, item, items, taken, solid) -> bool:
    g, box = r.map.grid, item["box"]
    corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
    if not all(r.x0 - 1e-6 <= x <= r.x1 + 1e-6 and r.z0 - 1e-6 <= z <= r.z1 + 1e-6 for x, z in corners):
        return False
    inner = (box[0] + 0.02, box[1] + 0.02, box[2] - 0.02, box[3] - 0.02)
    if not all(point_in_polygon(p, r.room.polygon) for p in ((inner[0], inner[1]), (inner[2], inner[1]),
                                                              (inner[0], inner[3]), (inner[2], inner[3]))):
        return False
    if not solid:
        return not any(_overlaps(box, o["box"], 0) for o in items if o["kind"] == "rug")
    cells = _cells_in(g, box)
    if not cells or any(not g.is_walkable(i, j) or (i, j) in taken for i, j in cells):
        return False
    if any(_overlaps(box, o["box"], 0 if (item["kind"], o["kind"]) in SEATS else GAP_M) for o in items if o["solid"]):
        return False
    if any(_gap(box, dw.center) < DOOR_CLEAR_M for dw in r.doorways):
        return False
    if any(blocks_window(box, w, item["h"]) for w in r.windows):
        return False
    return all(_gap(box, p) >= DEVICE_CLEAR_M for p in r.clear)


def _still_reachable(r, taken) -> bool:
    """With `taken` off the floor, the room's doorways connect, its devices are within reach of the floor left, and
    at least a third of its floor is left."""
    g = r.map.grid
    free = {c for c in r.cells if g.is_walkable(*c) and c not in taken}
    doors = [{c for c in cells if g.is_walkable(*c)} for cells in r.door_cells]
    area = free.union(*doors) if doors else set(free)
    if not doors or not doors[0]:
        return True
    seen, todo = set(), deque(doors[0])
    while todo:
        c = todo.popleft()
        if c in seen or c not in area:
            continue
        seen.add(c)
        i, j = c
        todo.extend(((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)))
    if any(cells and not (cells & seen) for cells in doors):
        return False
    if len(seen & free) < len(r.cells) / 3:
        return False
    if not all(any(distance(g.center(*c), p) <= REACH_M for c in seen) for p in r.reach):
        return False
    return _walkway(r, taken)


def _walkway(r, taken) -> bool:
    """With `taken` off the floor, a room with two or more doorways keeps them joined by floor WALKWAY_M wide, as
    the layout check measures it (vesper.lab.layoutcheck: cells with a (2k - 1)-cell square of floor about them,
    within the room's box and 0.3 m round it)."""
    if len(r.doorways) < 2:
        return True
    g = r.map.grid
    k = max(1, math.ceil(WALKWAY_M / 2 / g.cell))
    box = (r.x0 - 0.3, r.z0 - 0.3, r.x1 + 0.3, r.z1 + 0.3)
    near = (box[0] - k * g.cell, box[1] - k * g.cell, box[2] + k * g.cell, box[3] + k * g.cell)
    free = {c for c in _cells_in(g, near) if g.is_walkable(*c) and c not in taken}
    inside = {c for c in _cells_in(g, box) if all((c[0] + di, c[1] + dj) in free for di in range(-k + 1, k)
                                                   for dj in range(-k + 1, k))}
    starts = [{c for c in inside if distance(g.center(*c), d.center) <= WALKWAY_M} for d in r.doorways]
    seen, todo = set(), deque(starts[0])
    while todo:
        c = todo.popleft()
        if c in seen or c not in inside:
            continue
        seen.add(c)
        i, j = c
        todo.extend(((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)))
    return all(s & seen for s in starts[1:])
