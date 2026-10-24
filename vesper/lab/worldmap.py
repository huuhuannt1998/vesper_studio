"""The map file (`vesper-map/1`): a home's rooms, its walkable floor, and the doorways between rooms.

Rooms are HSSD's region polygons. The walkable floor is a grid of 10 cm cells, rasterised from Habitat's
navmesh by the world service. Doorways are not stored: they are where walkable floor passes from one room to
another, so they follow from the floor and the rooms. Room polygons alone cannot tell an opening from a wall,
which is why the floor grid is required.
"""
from __future__ import annotations

import base64
import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

from vesper.lab.geometry import Point, point_in_polygon

FORMAT = "vesper-map/1"


class MapError(ValueError):
    """A map file VESPER cannot use, or a name it does not contain."""


@dataclass(frozen=True)
class Room:
    id: str
    label: str  # HSSD's region label, e.g. "kitchen", "porch/terrace/deck/driveway"
    polygon: tuple[Point, ...]
    outdoor: bool = False


@dataclass(frozen=True)
class Doorway:
    id: str
    rooms: tuple[str, str]
    center: Point
    width: float
    cells: frozenset[tuple[int, int]]
    outdoor: bool = False  # one side is outside the home: a front or back door


@dataclass
class Grid:
    origin: Point  # the (x, z) corner of cell (0, 0)
    cell: float
    width: int
    height: int
    walkable: bytearray  # row-major, 1 = walkable

    def center(self, i: int, j: int) -> Point:
        return (self.origin[0] + (i + 0.5) * self.cell, self.origin[1] + (j + 0.5) * self.cell)

    def cell_of(self, x: float, z: float) -> tuple[int, int] | None:
        i, j = int((x - self.origin[0]) // self.cell), int((z - self.origin[1]) // self.cell)
        return (i, j) if 0 <= i < self.width and 0 <= j < self.height else None

    def is_walkable(self, i: int, j: int) -> bool:
        return 0 <= i < self.width and 0 <= j < self.height and bool(self.walkable[j * self.width + i])

    def to_json(self) -> dict:
        bits = bytearray((len(self.walkable) + 7) // 8)
        for k, v in enumerate(self.walkable):
            if v:
                bits[k >> 3] |= 1 << (k & 7)
        return {"origin": list(self.origin), "cell": self.cell, "width": self.width, "height": self.height,
                "walkable": base64.b64encode(bytes(bits)).decode()}

    @classmethod
    def from_json(cls, d: dict) -> Grid:
        bits = base64.b64decode(d["walkable"])
        n = d["width"] * d["height"]
        return cls(tuple(d["origin"]), d["cell"], d["width"], d["height"],
                   bytearray((bits[k >> 3] >> (k & 7)) & 1 for k in range(n)))


@dataclass
class WorldMap:
    scene: str
    rooms: list[Room]
    grid: Grid
    doorways: list[Doorway] = field(init=False)
    labels: list[int] = field(init=False, repr=False)  # per cell: index into rooms, or -1

    def __post_init__(self):
        self.labels = self._label_cells()
        self.doorways = self._find_doorways()

    # -- queries ------------------------------------------------------------------------------
    def room_at(self, x: float, z: float) -> Room | None:
        return next((r for r in self.rooms if point_in_polygon((x, z), r.polygon)), None)

    def room(self, room_id: str) -> Room:
        for r in self.rooms:
            if r.id == room_id:
                return r
        raise MapError(f"unknown room {room_id!r}; {suggest(room_id, [r.id for r in self.rooms])}")

    def doorway(self, doorway_id: str) -> Doorway:
        for d in self.doorways:
            if d.id == doorway_id:
                return d
        raise MapError(f"unknown doorway {doorway_id!r}; {suggest(doorway_id, [d.id for d in self.doorways])}")

    def room_of_cell(self, i: int, j: int) -> Room | None:
        k = self.labels[j * self.grid.width + i]
        return self.rooms[k] if k >= 0 else None

    # -- the file -----------------------------------------------------------------------------
    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({
            "format": FORMAT, "scene": self.scene, "grid": self.grid.to_json(),
            "rooms": [{"id": r.id, "label": r.label, "polygon": [list(p) for p in r.polygon], "outdoor": r.outdoor}
                      for r in self.rooms]}))

    @classmethod
    def load(cls, path: Path) -> WorldMap:
        try:
            d = json.loads(Path(path).read_text())
        except (OSError, ValueError) as e:
            raise MapError(f"cannot read the map file {path}: {e}") from e
        if not isinstance(d, dict) or d.get("format") != FORMAT:
            raise MapError(f"{path} is not a VESPER map ({FORMAT})")
        rooms = [Room(r["id"], r["label"], tuple(tuple(p) for p in r["polygon"]), bool(r.get("outdoor")))
                 for r in d["rooms"]]
        return cls(d["scene"], rooms, Grid.from_json(d["grid"]))

    # -- derivation ---------------------------------------------------------------------------
    def _label_cells(self) -> list[int]:
        g, labels = self.grid, [-1] * (self.grid.width * self.grid.height)
        for k, room in enumerate(self.rooms):
            xs, zs = [p[0] for p in room.polygon], [p[1] for p in room.polygon]
            lo, hi = g.cell_of(min(xs), min(zs)) or (0, 0), g.cell_of(max(xs), max(zs)) or (g.width - 1, g.height - 1)
            for j in range(max(0, lo[1]), min(g.height, hi[1] + 1)):
                for i in range(max(0, lo[0]), min(g.width, hi[0] + 1)):
                    c = j * g.width + i
                    if g.walkable[c] and labels[c] < 0 and point_in_polygon(g.center(i, j), room.polygon):
                        labels[c] = k
        return labels

    def _find_doorways(self) -> list[Doorway]:
        """Walkable cells in no room (a doorway through a wall's thickness) and cells next to another room's
        cells form doorway candidates; a candidate region touching two rooms is a doorway between them."""
        g, labels = self.grid, self.labels
        w, h = g.width, g.height

        def label(i, j):
            return labels[j * w + i] if g.is_walkable(i, j) else None  # None: not walkable

        candidate = set()
        for j in range(h):
            for i in range(w):
                here = label(i, j)
                if here is None:
                    continue
                if here < 0 or any((n := label(i + di, j + dj)) is not None and n >= 0 and n != here
                                   for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    candidate.add((i, j))
        out, seen = [], set()
        for start in sorted(candidate):
            if start in seen:
                continue
            comp, stack = set(), [start]
            seen.add(start)
            while stack:
                i, j = stack.pop()
                comp.add((i, j))
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        n = (i + di, j + dj)
                        if n in candidate and n not in seen:
                            seen.add(n)
                            stack.append(n)
            touching = {labels[c[1] * w + c[0]] for c in comp} | {
                label(i + di, j + dj) for i, j in comp for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))}
            rooms = sorted(self.rooms[k].id for k in touching if k is not None and k >= 0)
            if len(rooms) < 2:
                continue
            centers = [g.center(i, j) for i, j in comp]
            ext_i = max(i for i, _ in comp) - min(i for i, _ in comp) + 1
            ext_j = max(j for _, j in comp) - min(j for _, j in comp) + 1
            pair = (rooms[0], rooms[1])
            out.append(Doorway("-".join(pair), pair, (sum(c[0] for c in centers) / len(centers),
                                                      sum(c[1] for c in centers) / len(centers)),
                               max(ext_i, ext_j) * g.cell, frozenset(comp),
                               outdoor=any(self.room(r).outdoor for r in pair)))
        out.sort(key=lambda d: (d.id, d.center))
        named, count = [], {}
        for d in out:  # two doorways between the same rooms: kitchen-living, kitchen-living-2
            count[d.id] = count.get(d.id, 0) + 1
            named.append(d if count[d.id] == 1 else Doorway(f"{d.id}-{count[d.id]}", d.rooms, d.center, d.width,
                                                             d.cells, d.outdoor))
        return named


def suggest(name: str, names: list[str]) -> str:
    """'did you mean …?' with the closest names, or names containing `name`; else the choices themselves."""
    close = difflib.get_close_matches(name, names, n=3)
    if len(name) >= 3:
        close += [n for n in names if name.lower() in n.lower() and n not in close][:3 - len(close)]
    return f"did you mean {', '.join(close)}?" if close else f"choose one of {', '.join(sorted(names)[:10]) or 'none'}"
