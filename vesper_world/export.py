"""An HSSD scene as a VESPER map (`vesper-map/1`) and its door openings (`vesper-openings/1`).

The walkable floor is Habitat's navmesh, recomputed with the furniture in it and the doors taken out (VESPER
opens and closes its doors itself; windows stay, as walls), sampled in 10 cm cells at the ground floor's height.
Whether an opening is a door or a window is HSSD's own metadata: floor-length windows are as tall as doors. Only the
largest connected part of the floor is kept: a stair landing or a speck of navmesh on a counter is not floor a
person walks on. Rooms are HSSD's region annotations on the ground floor; a storeyed scene's upper floors are left
out, and named. Doorways are not exported: VESPER derives them from the floor and the rooms.
The render manifest (`vesper-render/1`, plan W1b) lists every mesh the scene draws, each with the world matrix
Habitat gives it: the browser draws the scene from HSSD's own files, placed exactly as Habitat places them.
Pure functions only: `habitat_scene.py` holds what needs Habitat.
"""
from __future__ import annotations

import base64
import csv
import math
import re
import xml.etree.ElementTree as ET
from collections import deque
from math import ceil, floor

MAP_FORMAT = "vesper-map/1"
OPENINGS_FORMAT = "vesper-openings/1"
RENDER_FORMAT = "vesper-render/1"
IDENTITY_ROWS = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
CELL_M = 0.1
OUTDOOR_LABELS = ("outdoor", "porch/terrace/deck/driveway", "balcony")  # HSSD's region labels for outside
_OPENING = re.compile(r"^\d+-\d+_:\d+$")  # HSSD's door and window objects: objects/openings/<n>-<k>
KINDS_CSV = "metadata/fpmodels-with-decomposed.csv"  # HSSD's object metadata; main_category is door or window
STOREY_M = 0.5  # regions whose floors are this close are one storey
OPENING_ABOVE_M = (-0.5, 2.4)  # an opening's centre this far above a floor belongs to it (doors: 1.1 to 1.8 m)


class ExportError(RuntimeError):
    """A scene the world service cannot export; the message says why."""


def is_opening(handle: str) -> bool:
    return bool(_OPENING.match(handle))


def opening_kind(handle: str, kinds: dict) -> str | None:
    """'door' or 'window', as HSSD's metadata says of the object; None for one it does not know."""
    return kinds.get(handle.split("_:")[0])


def opening_kinds(path) -> dict:
    """{object id: main_category} for HSSD's openings (ids <n>-<k>), from its metadata CSV."""
    with open(path, newline="") as f:
        return {row["id"]: row["main_category"] for row in csv.DictReader(f) if re.match(r"^\d+-\d+$", row["id"])}


def storeys(regions) -> list:
    """The floor heights a scene's regions stand on, one per storey (its lowest), from the bottom up."""
    out = []
    for h in sorted(float(r.get("floor_height", 0.0)) for r in regions):
        if not out or h - out[-1] > STOREY_M:
            out.append(h)
    return out


def ground_floor(regions) -> float:
    """The storey a home is made from: the lowest one with an indoor room (in HSSD, every storeyed scene stands
    on 0.0; its upper floors may hold more rooms)."""
    levels = storeys(regions)
    for h in levels:
        if any(r.get("label") not in OUTDOOR_LABELS for r in on_floor(regions, h)[0]):
            return h
    return levels[0]


def on_floor(regions, floor_y: float):
    """(the regions on the floor at `floor_y`, the others)."""
    kept = [r for r in regions if abs(float(r.get("floor_height", 0.0)) - floor_y) <= STOREY_M]
    return kept, [r for r in regions if not abs(float(r.get("floor_height", 0.0)) - floor_y) <= STOREY_M]


def openings_on_floor(openings, floor_y: float):
    lo, hi = OPENING_ABOVE_M
    return [o for o in openings if lo <= o["y"] - floor_y <= hi]


def rasterize(navigable, bounds, floor_y: float, cell: float = CELL_M):
    """((x0, z0), width, height, cells): cell centres over the navmesh's bounds, walkable where `navigable(x, y, z)`
    holds at the floor's height; cells are row-major, z rows, x columns."""
    (lx, _, lz), (hx, _, hz) = bounds
    x0, z0 = floor(lx / cell) * cell, floor(lz / cell) * cell
    width, height = int(ceil((hx - x0) / cell - 1e-9)), int(ceil((hz - z0) / cell - 1e-9))
    cells = [bool(navigable(x0 + (i + 0.5) * cell, floor_y, z0 + (j + 0.5) * cell))
             for j in range(height) for i in range(width)]
    return (round(x0, 6), round(z0, 6)), width, height, cells


def largest_part(cells, width: int, height: int):
    """`cells` with only their largest 4-connected part left walkable."""
    part, sizes = [-1] * len(cells), []
    for start, walkable in enumerate(cells):
        if not walkable or part[start] >= 0:
            continue
        k, n, queue = len(sizes), 0, deque([start])
        part[start] = k
        while queue:
            c = queue.popleft()
            n += 1
            i, j = c % width, c // width
            for ni, nj in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                nc = nj * width + ni
                if 0 <= ni < width and 0 <= nj < height and cells[nc] and part[nc] < 0:
                    part[nc] = k
                    queue.append(nc)
        sizes.append(n)
    keep = max(range(len(sizes)), key=lambda k: sizes[k]) if sizes else -1
    return [p == keep for p in part]


def room_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "room"


def rooms_from_regions(regions):
    """HSSD's region annotations as the map's rooms: ids from their names, polygons in (x, z) (y is up)."""
    rooms, seen = [], {}
    for r in regions:
        base = room_id(r["name"])
        seen[base] = seen.get(base, 0) + 1
        rid = base if seen[base] == 1 else f"{base}_{seen[base]}"
        rooms.append({"id": rid, "label": r["label"], "polygon": [[float(p[0]), float(p[2])] for p in r["poly_loop"]],
                      "outdoor": r["label"] in OUTDOOR_LABELS})
    return rooms


def encode(cells) -> str:
    """The walkable bits, least significant first, as VESPER's map file stores them."""
    bits = bytearray((len(cells) + 7) // 8)
    for k, walkable in enumerate(cells):
        if walkable:
            bits[k >> 3] |= 1 << (k & 7)
    return base64.b64encode(bytes(bits)).decode()


def scene_map(scene) -> dict:
    """The map file for a loaded scene (see `habitat_scene.Scene`)."""
    origin, width, height, cells = rasterize(scene.navigable, scene.bounds, scene.floor_y)
    cells = largest_part(cells, width, height)
    if not any(cells):
        raise ExportError(f"scene {scene.scene}: no walkable floor at height {scene.floor_y:g} m")
    return {"format": MAP_FORMAT, "scene": f"hssd/{scene.scene}",
            "grid": {"origin": list(origin), "cell": CELL_M, "width": width, "height": height,
                     "walkable": encode(cells)},
            "rooms": rooms_from_regions(on_floor(scene.regions, scene.floor_y)[0])}


def scene_openings(scene) -> dict:
    """The ground floor's doors and windows, each with its kind, and which storeys the export left out."""
    left_out = on_floor(scene.regions, scene.floor_y)[1]
    return {"format": OPENINGS_FORMAT, "scene": f"hssd/{scene.scene}",
            "openings": openings_on_floor(scene.openings, scene.floor_y),
            "floor": {"height": scene.floor_y, "storeys": storeys(scene.regions),
                      "left_out": [r["name"] for r in left_out]}}


def scene_render(scene) -> dict:
    """The render manifest: each mesh's file (relative to HSSD) and world matrix (16 numbers, row by row)."""
    return {"format": RENDER_FORMAT, "scene": f"hssd/{scene.scene}", "hssd": scene.hssd, "floor_y": scene.floor_y,
            "items": list(scene.render)}


def urdf_visuals(text: str) -> dict:
    """{link name: [(mesh file, xyz, rpy, scale), ...]}: the meshes an articulated object draws, per link."""
    def numbers(value, default):
        return tuple(float(v) for v in value.split()) if value else default
    out = {}
    for link in ET.fromstring(text).iter("link"):
        meshes = []
        for visual in link.findall("visual"):
            mesh, origin = visual.find("geometry/mesh"), visual.find("origin")
            if mesh is None:
                continue
            meshes.append((mesh.get("filename"),
                           numbers(origin.get("xyz") if origin is not None else None, (0.0, 0.0, 0.0)),
                           numbers(origin.get("rpy") if origin is not None else None, (0.0, 0.0, 0.0)),
                           numbers(mesh.get("scale"), (1.0, 1.0, 1.0))))
        if meshes:
            out[link.get("name")] = meshes
    return out


def trs(xyz, rpy, scale):
    """A URDF origin as a 4x4 matrix (rows): translate, then turn (roll, pitch, yaw about the fixed x, y and z
    axes), then scale."""
    roll, pitch, yaw = rpy
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw),
                              math.sin(yaw))
    r = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
         [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
         [-sp, cp * sr, cp * cr]]
    return [[r[i][0] * scale[0], r[i][1] * scale[1], r[i][2] * scale[2], float(xyz[i])] for i in range(3)] + \
        [[0.0, 0.0, 0.0, 1.0]]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def flat(rows) -> list:
    """A 4x4 matrix as the manifest stores it: 16 numbers, row by row (three.js's Matrix4.set order)."""
    return [round(float(v), 6) + 0.0 for row in rows for v in row]
