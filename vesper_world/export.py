"""An HSSD scene as a VESPER map (`vesper-map/1`) and its door openings (`vesper-openings/1`).

The walkable floor is Habitat's navmesh, recomputed with the furniture in it and the door openings taken out
(VESPER opens and closes its doors itself), sampled in 10 cm cells at the main floor's height. Only the largest
connected part is kept: a stair landing or a speck of navmesh on a counter is not floor a person walks on.
Rooms are HSSD's region annotations. Doorways are not exported: VESPER derives them from the floor and the rooms.
Pure functions only: `habitat_scene.py` holds what needs Habitat.
"""
from __future__ import annotations

import base64
import re
from collections import deque
from math import ceil, floor

MAP_FORMAT = "vesper-map/1"
OPENINGS_FORMAT = "vesper-openings/1"
CELL_M = 0.1
OUTDOOR_LABELS = ("outdoor", "porch/terrace/deck/driveway", "balcony")  # HSSD's region labels for outside
_OPENING = re.compile(r"^\d+-\d+_:\d+$")  # HSSD's door and window objects: objects/openings/<n>-<k>


class ExportError(RuntimeError):
    """A scene the world service cannot export; the message says why."""


def is_opening(handle: str) -> bool:
    return bool(_OPENING.match(handle))


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
            "rooms": rooms_from_regions(scene.regions)}


def scene_openings(scene) -> dict:
    return {"format": OPENINGS_FORMAT, "scene": f"hssd/{scene.scene}", "openings": list(scene.openings)}
