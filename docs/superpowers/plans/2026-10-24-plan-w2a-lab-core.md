# Plan W2a: the VESPER Lab app core, headless — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The core of VESPER Lab without the browser and without Habitat: a home's map, people who walk it, real-product twins placed in it, the rules that couple them, and a trace that replays exactly.

**Architecture:**
1. `vesper/lab/worldmap.py` defines the map file (`vesper-map/1`): HSSD's room polygons plus a 10 cm walkable-floor grid. Doorways are derived where the floor passes from one room into another, because room polygons alone cannot tell an opening from a wall.
2. `vesper/lab/mapworld.py` is the world in-process, on the session's clock: avatars that step and walk (A* on the grid at 1.1 m/s), doors that block them, and room and door events. The Habitat world service (plan W1) will offer the same operations and events, and add the 3D view.
3. `vesper/lab/home.py` loads the home file v2 (spec §4), which places real catalog products in rooms, on doors or at points, and names every mistake with the nearest valid choice.
4. `vesper/lab/rules.py` holds the game rules (spec §5):
   - motion sensors see people in their room or cone, and clear when people keep still;
   - doors open by hand unless locked, and contact sensors report it;
   - lamps, plugs, switches, locks and buttons work by hand, and bindings switch their targets;
   - a time-of-day ambient model feeds the sensors;
   - device states feed back to the views.
5. `vesper/lab/session.py` runs a home (twins, world, rules, the player) and writes the trace (`vesper-trace/1`). Replaying the inputs on the logical clock reproduces the device reports exactly (spec success criterion 4).
6. `vesper lab new|check|run|replay` are the headless commands.

**Tech Stack:** Python ≥3.11, standard library plus PyYAML (already a dependency); the twin runtime and clocks of sub-project #1.

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, approved by the PI on 2026-10-24 (RKA `jrn_01M5QSGDYGE158FRVPVWKN0QNZ`). Covered here:
- §2 the commands `new`, `check`, `run`, `replay` (headless; `play` and the browser UI are plan W2b);
- §3 the map file and the single world authority;
- §4 the home file;
- §5 the rules;
- §5b space and time (metres, real-time or logical clock);
- §8 the unit tests.

## Global Constraints

- Laptop only; no new third-party dependency (spec §7 allows one WebSocket library, for W2b).
- No robots (PI, 2026-10-24): actors are people.
- Nothing fails silently:
  - every home-file mistake names its field;
  - a map file that is not `vesper-map/1` is refused;
  - an unreachable walk returns None;
  - a replay that differs names the first different report.
- Shared Mac (another project's scored run until about 2026-10-27): unit tests only, under `nice -n 19` / `taskpolicy -b`. No Habitat, no Home Assistant, no rendering, no network.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **Walls must stop people,** even where two rooms' polygons touch. Pinned by `test_a_solid_wall_has_no_doorway` and `test_steps_move_within_a_room_and_walls_stop_them` (Tasks 1, 2).
2. **A walking step that turns a corner must not cut through a wall.** The first implementation checked the chord and stopped in a doorway. Pinned by `test_walk_to_follows_doorways_at_walking_speed_and_arrives` (Task 2).
3. **A person keeping still must let a motion sensor clear,** as a real PIR does, and moving must re-trigger it. Pinned by `test_walking_in_trips_a_ceiling_motion_sensor_and_keeping_still_lets_it_clear` (Task 4).
4. **A locked door must stay shut,** and a door's state, its contact sensor and its view effect must agree. Pinned by `test_a_locked_door_stays_shut` and `test_the_front_door_opens_and_closes_by_hand_and_its_contact_sensor_reports_it` (Task 4).
5. **A replay that differs must say where;** a trace record must keep its own kind. Pinned by `test_a_replay_on_a_changed_home_names_the_first_difference` and `test_an_interaction_is_traced_as_a_rule_with_its_target_kind` (Task 5).

## Rulings carried into this plan

- **The walkable-floor grid is required.** Room polygons alone cannot tell an opening from a wall, so VESPER never infers doorways from them. HSSD maps come from the Habitat world service (plan W1); until then the tests use synthetic homes, and `vesper lab new` imports a map file. Cost if wrong: none; W1 adds `--scene`.
- **A wall sensor's line of sight is its own room;** a ceiling sensor covers its room. Cost if wrong: a sensor that sees through a doorway in reality does not here; W1 can refine this with the navmesh.
- **The ambient model is assumed** (no windows, no weather) and documented as such. Cost if wrong: illuminance and temperature curves, which stay plausible.
- **A binding acts on physical use only,** and is recorded as the person's use through the switch (`human:p via bedroom_button`). Cost if wrong: a Home Assistant command to a switch does not propagate to its bound light.
- **Ceiling-mounted devices are not operable by hand;** a bound switch operates them. A door carrying a sensor or a lock starts closed unless the `doors` list says otherwise.
- **The trace leaves out poses,** which follow from the inputs. It keeps inputs, rules, world events and reports.

---
### Task 1: The map file: rooms, the walkable floor, and doorways

**Files:**
- Create: `vesper/lab/__init__.py`, `vesper/lab/geometry.py`, `vesper/lab/worldmap.py`
- Create tests: `tests/lab/__init__.py` (empty), `tests/lab/maps.py` (synthetic homes), `tests/lab/test_worldmap.py`

**Interfaces:**
- Produces:
  - in `vesper/lab/geometry.py`: `point_in_polygon(p, poly)`, `polygon_area(poly)`, `centroid(poly)`, `distance(a, b)`, `in_cone(origin, facing_deg, p, range_m, fov_deg)`;
  - in `vesper/lab/worldmap.py`: `MapError`, `Room(id, label, polygon, outdoor)`, `Doorway(id, rooms, center, width, cells, outdoor)`, `Grid(origin, cell, width, height, walkable)` with `center`, `cell_of`, `is_walkable`;
  - `WorldMap(scene, rooms, grid)` with `doorways`, `room_at`, `room`, `doorway`, `room_of_cell`, `save`, and `WorldMap.load`;
  - `suggest(name, names)`.
- Test fixtures: `tests.lab.maps.two_rooms(gap=(1.5, 2.5))`, `house()`, `rect(...)`, `grid_from(...)`.

- [ ] **Step 1: Write the failing tests**

Create `vesper/lab/__init__.py`:
```python
"""VESPER Lab: a game-like virtual smart home (docs/superpowers/specs/2026-10-24-vesper-lab-design.md)."""
```

Also create the empty file `tests/lab/__init__.py`.

Create `tests/lab/maps.py`:
```python
"""Synthetic homes for the Lab tests: rooms on a 10 cm walkable grid, built in code."""
from vesper.lab.worldmap import Grid, Room, WorldMap

CELL = 0.1


def grid_from(width_m, height_m, walkable, origin=(0.0, 0.0)):
    """A grid whose cell (i, j) is walkable when `walkable(x, z)` holds at its centre."""
    w, h = round(width_m / CELL), round(height_m / CELL)
    g = Grid(origin=origin, cell=CELL, width=w, height=h, walkable=bytearray(w * h))
    for j in range(h):
        for i in range(w):
            x, z = g.center(i, j)
            g.walkable[j * w + i] = 1 if walkable(x, z) else 0
    return g


def rect(x0, z0, x1, z1):
    return ((x0, z0), (x1, z0), (x1, z1), (x0, z1))


def two_rooms(gap=(1.5, 2.5)):
    """Kitchen [0,4]x[0,4] and living room [4,8]x[0,4]; a 20 cm wall at x=4 with an opening at `gap` (z)."""
    def walkable(x, z):
        in_wall = 3.9 <= x < 4.1
        return not in_wall or (gap is not None and gap[0] <= z < gap[1])
    return WorldMap(scene="test/two-rooms", rooms=[Room("kitchen", "kitchen", rect(0, 0, 4, 4)),
                                                   Room("living", "living room", rect(4, 0, 8, 4))],
                    grid=grid_from(8, 4, walkable))


def house():
    """Hall [0,3]x[0,6], kitchen [3,7]x[0,3], bedroom [3,7]x[3,6], porch outside [-2,0]x[2,4].

    Walls are 20 cm thick and lie outside every room polygon, as in a navmesh raster: doorways are gaps
    hall-kitchen at z in [1,2], hall-bedroom at z in [4,5], and the front door hall-porch at z in [2.5,3.5].
    The kitchen and the bedroom share a solid wall.
    """
    rooms = [Room("hall", "hallway", rect(0.1, 0, 2.9, 6)), Room("kitchen", "kitchen", rect(3.1, 0, 7, 2.9)),
             Room("bedroom", "bedroom", rect(3.1, 3.1, 7, 6)),
             Room("porch", "porch/terrace/deck/driveway", rect(-2, 2, -0.1, 4), outdoor=True)]

    def walkable(x, z):
        if -0.1 <= x < 0.1:  # the front wall
            return 2.5 <= z < 3.5
        if 2.9 <= x < 3.1:  # hall | kitchen, bedroom
            return 1 <= z < 2 or 4 <= z < 5
        if x >= 3.1 and 2.9 <= z < 3.1:  # kitchen | bedroom: solid
            return False
        if x < -0.1:
            return 2 <= z < 4  # only the porch is outside
        return 0 <= z < 6 and x < 7
    return WorldMap(scene="test/house", rooms=rooms, grid=grid_from(9, 6, walkable, origin=(-2.0, 0.0)))
```

Create `tests/lab/test_worldmap.py`:
```python
import json

import pytest

from tests.lab.maps import house, rect, two_rooms
from vesper.lab.geometry import centroid, in_cone, point_in_polygon
from vesper.lab.worldmap import MapError, WorldMap


def test_polygon_geometry():
    square = rect(0, 0, 4, 4)
    assert point_in_polygon((1, 1), square) and not point_in_polygon((5, 1), square)
    assert centroid(square) == pytest.approx((2.0, 2.0))
    assert in_cone((0, 0), 0.0, (3, 0.5), 7.0, 110.0)  # facing +x, a point slightly off-axis
    assert not in_cone((0, 0), 0.0, (-3, 0), 7.0, 110.0)  # behind
    assert not in_cone((0, 0), 0.0, (8, 0), 7.0, 110.0)  # out of range
    assert in_cone((0, 0), 90.0, (0, 3), 7.0, 110.0)  # facing +z


def test_rooms_are_found_by_position():
    m = two_rooms()
    assert m.room_at(1, 1).id == "kitchen" and m.room_at(6, 2).id == "living" and m.room_at(9, 2) is None
    assert m.room("living").label == "living room"


def test_a_wall_gap_between_two_rooms_is_one_doorway():
    (d,) = two_rooms().doorways
    assert d.rooms == ("kitchen", "living") and d.id == "kitchen-living"
    assert d.center == pytest.approx((4.0, 2.0), abs=0.1) and d.width == pytest.approx(1.0, abs=0.15)


def test_a_solid_wall_has_no_doorway():
    assert two_rooms(gap=None).doorways == []


def test_doorways_through_wall_thickness_outside_every_room_are_found():
    doors = {d.id: d for d in house().doorways}
    assert set(doors) == {"hall-kitchen", "bedroom-hall", "hall-porch"}  # never kitchen-bedroom
    assert doors["hall-porch"].center == pytest.approx((0.0, 3.0), abs=0.15)
    assert doors["hall-porch"].outdoor and not doors["hall-kitchen"].outdoor


def test_the_map_file_round_trips_and_derives_the_same_doorways(tmp_path):
    m = house()
    path = tmp_path / "map.json"
    m.save(path)
    loaded = WorldMap.load(path)
    assert loaded.rooms == m.rooms and loaded.grid.walkable == m.grid.walkable
    assert [d.id for d in loaded.doorways] == [d.id for d in m.doorways]
    assert json.loads(path.read_text())["format"] == "vesper-map/1"


def test_a_file_that_is_not_a_vesper_map_is_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(MapError, match="not a VESPER map"):
        WorldMap.load(bad)
    with pytest.raises(MapError, match="unknown room 'garage'.*kitchen"):
        two_rooms().room("garage")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_worldmap.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.worldmap'`

- [ ] **Step 3: Implement**

Create `vesper/lab/geometry.py`:
```python
"""Plane geometry on the floor (x, z in metres, as in Habitat's scenes; y is up)."""
from __future__ import annotations

import math

Point = tuple[float, float]


def point_in_polygon(p: Point, poly) -> bool:
    """Ray casting; points exactly on an edge may fall either way."""
    x, z = p
    inside = False
    n = len(poly)
    for k in range(n):
        (x1, z1), (x2, z2) = poly[k], poly[(k + 1) % n]
        if (z1 > z) != (z2 > z) and x < x1 + (z - z1) * (x2 - x1) / (z2 - z1):
            inside = not inside
    return inside


def polygon_area(poly) -> float:
    return abs(sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True))) / 2


def centroid(poly) -> Point:
    poly = list(poly)
    a = sum(x1 * z2 - x2 * z1 for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True)) / 2
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = sum((x1 + x2) * (x1 * z2 - x2 * z1) for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True))
    cz = sum((z1 + z2) * (x1 * z2 - x2 * z1) for (x1, z1), (x2, z2) in zip(poly, poly[1:] + poly[:1], strict=True))
    return (cx / (6 * a), cz / (6 * a))


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def in_cone(origin: Point, facing_deg: float, p: Point, range_m: float, fov_deg: float) -> bool:
    """Whether `p` lies within `range_m` of `origin` and within `fov_deg` around the direction `facing_deg`
    (0° is +x, 90° is +z)."""
    dx, dz = p[0] - origin[0], p[1] - origin[1]
    d = math.hypot(dx, dz)
    if d > range_m:
        return False
    if d < 1e-9:
        return True
    off = (math.degrees(math.atan2(dz, dx)) - facing_deg + 180) % 360 - 180
    return abs(off) <= fov_deg / 2
```

Create `vesper/lab/worldmap.py`:
```python
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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab/test_worldmap.py -q 2>&1 | tail -1`

Expected: `7 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/__init__.py vesper/lab/geometry.py vesper/lab/worldmap.py tests/lab/__init__.py tests/lab/maps.py tests/lab/test_worldmap.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the map file: rooms, the walkable floor, and the doorways between rooms

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: The map world: people who step and walk, doors that stop them

**Files:**
- Create: `vesper/lab/mapworld.py`
- Create tests: `tests/lab/test_mapworld.py`

**Interfaces:**
- Consumes: `WorldMap`, `MapError` (Task 1); a clock with `now()` and `call_at(t, fn)` (`vesper.twins.clock.LogicalClock` or `WallClock`).
- Produces, in `vesper/lab/mapworld.py`:
  - `SPEED_M_S = 1.1`, `STEP_S = 0.25`, `MAX_STEP_M = 0.5`, `WorldError`, `Pose(actor, model, x, z, yaw, room)`;
  - `MapWorld(world_map, clock)` with `spawn(actor, model, at) -> Pose`, `pose(actor)`, `actors()`, `move(actor, dx, dz) -> Pose` (and `last_move_ok`), `walk_to(actor, target) -> list[Point] | None`, `stop(actor)`, `add_door(doorway_id, open=True)`, `set_door(doorway_id, open)`, `doors`, `on_event(fn)`;
  - events `pose`, `room`, `door`, `arrived`, `blocked`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_mapworld.py`:
```python
import pytest

from tests.lab.maps import house, two_rooms
from vesper.lab.mapworld import SPEED_M_S, MapWorld, WorldError
from vesper.twins.clock import LogicalClock


def world(m=None, doors=()):
    w = MapWorld(m or two_rooms(), LogicalClock())
    events = []
    w.on_event(events.append)
    for d in doors:
        w.add_door(d, open=True)
    return w, events


def test_an_actor_spawns_in_a_room_and_cannot_spawn_in_a_wall():
    w, _ = world()
    assert w.spawn("p", "female_0", (1.0, 1.0)).room == "kitchen"
    with pytest.raises(WorldError, match="not on the walkable floor"):
        w.spawn("q", "male_0", (4.0, 0.5))  # inside the wall


def test_steps_move_within_a_room_and_walls_stop_them():
    w, _ = world()
    w.spawn("p", "female_0", (3.5, 0.5))
    assert w.move("p", 0.3, 0.0).x == pytest.approx(3.8)
    blocked = w.move("p", 0.4, 0.0)  # into the wall at z=0.5, where there is no opening
    assert blocked.x == pytest.approx(3.8) and not w.last_move_ok


def test_walking_through_a_doorway_changes_room():
    w, events = world()
    w.spawn("p", "female_0", (3.6, 2.0))
    for _ in range(4):
        w.move("p", 0.25, 0.0)
    assert w.pose("p").room == "living"
    assert {"event": "room", "actor": "p", "room": "living", "previous": "kitchen"} in events


def test_a_closed_door_blocks_steps_and_paths_until_it_opens():
    w, events = world(doors=["kitchen-living"])
    w.spawn("p", "female_0", (3.6, 2.0))
    w.set_door("kitchen-living", open=False)
    assert {"event": "door", "id": "kitchen-living", "open": False} in events
    for _ in range(4):
        w.move("p", 0.25, 0.0)
    assert w.pose("p").room == "kitchen"
    assert w.walk_to("p", (6.0, 2.0)) is None  # no other way round
    w.set_door("kitchen-living", open=True)
    assert w.walk_to("p", (6.0, 2.0)) is not None


def test_only_registered_doors_open_and_close():
    w, _ = world()
    with pytest.raises(WorldError, match="no door on doorway 'kitchen-living'"):
        w.set_door("kitchen-living", open=False)
    with pytest.raises(WorldError, match="unknown doorway"):
        w.add_door("nowhere")


def test_walk_to_follows_doorways_at_walking_speed_and_arrives():
    w, events = world(house())
    w.spawn("p", "female_0", (5.0, 1.0))  # kitchen
    path = w.walk_to("p", (5.0, 5.0))  # bedroom: the shared wall is solid, so through the hall
    assert path[0] == pytest.approx((5.0, 1.0)) and path[-1] == pytest.approx((5.0, 5.0), abs=0.1)
    assert any(x < 3.0 for x, _ in path)
    length = sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(path, path[1:]))
    w.clock.advance(length / SPEED_M_S - 1.0)
    assert w.pose("p").room != "bedroom" or not any(e["event"] == "arrived" for e in events)
    w.clock.advance(2.0)
    assert w.pose("p").room == "bedroom" and {"event": "arrived", "actor": "p"} in events
    rooms = [e["room"] for e in events if e["event"] == "room"]
    assert rooms == ["hall", "bedroom"]


def test_a_new_walk_or_a_step_replaces_the_current_walk():
    w, events = world(house())
    w.spawn("p", "female_0", (5.0, 1.0))
    w.walk_to("p", (5.0, 5.0))
    w.clock.advance(1.0)
    w.stop("p")
    here = w.pose("p")
    w.clock.advance(30.0)
    assert w.pose("p") == here and not any(e["event"] == "arrived" for e in events)


def test_walks_are_deterministic():
    def poses():
        w, events = world(house())
        w.spawn("p", "female_0", (5.0, 1.0))
        w.walk_to("p", (1.0, 5.0))
        w.clock.advance(60.0)
        return [(e["x"], e["z"]) for e in events if e["event"] == "pose"]
    assert poses() == poses() and len(poses()) > 10
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_mapworld.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.mapworld'`

- [ ] **Step 3: Implement**

Create `vesper/lab/mapworld.py`:
```python
"""The map world: avatars that walk a home's map, and the doors that block them (spec §3).

It runs in-process on the session's clock, from the map file alone: the headless world for scripted runs,
the tests and the map-only mode. The Habitat world service (plan W1) offers the same operations and events,
and adds the 3D view.

Events, to every `on_event` handler:
- `{"event": "pose", "actor", "x", "z", "yaw", "room"}` after every step;
- `{"event": "room", "actor", "room", "previous"}` when an actor enters another room;
- `{"event": "door", "id", "open"}` when a door opens or closes;
- `{"event": "arrived", "actor"}` at the end of a walk, `{"event": "blocked", "actor"}` when a walk is cut off.
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from dataclasses import dataclass

from vesper.lab.geometry import Point, distance
from vesper.lab.worldmap import MapError, WorldMap

SPEED_M_S = 1.1  # walking speed (the earlier embodied resident's, e2_embodied.py)
STEP_S = 0.25  # a walking avatar's pose updates this often
MAX_STEP_M = 0.5  # the longest single step a player's input may make


class WorldError(ValueError):
    """An operation the world refuses: an unknown actor or door, a position off the floor."""


@dataclass(frozen=True)
class Pose:
    actor: str
    model: str
    x: float
    z: float
    yaw: float  # degrees; 0 is +x, 90 is +z
    room: str | None  # None in a doorway or outside every room


class MapWorld:
    def __init__(self, world_map: WorldMap, clock):
        self.map, self.clock = world_map, clock
        self.doors: dict[str, bool] = {}  # doorway id -> open
        self.last_move_ok = True
        self._poses: dict[str, Pose] = {}
        self._last_room: dict[str, str | None] = {}
        self._walk: dict[str, int] = {}  # a walk's token; a newer walk or a step cancels the older one
        self._handlers: list[Callable[[dict], None]] = []
        self._blocked: frozenset = frozenset()

    def on_event(self, fn: Callable[[dict], None]) -> None:
        self._handlers.append(fn)

    # -- actors -------------------------------------------------------------------------------
    def spawn(self, actor: str, model: str, at: Point) -> Pose:
        if actor in self._poses:
            raise WorldError(f"actor {actor!r} already exists")
        if not self._free(*at):
            raise WorldError(f"{actor}: {at} is not on the walkable floor")
        room = self.map.room_at(*at)
        self._last_room[actor] = room.id if room else None
        self._poses[actor] = Pose(actor, model, at[0], at[1], 0.0, room.id if room else None)
        self._emit_pose(self._poses[actor])
        return self._poses[actor]

    def pose(self, actor: str) -> Pose:
        if actor not in self._poses:
            raise WorldError(f"unknown actor {actor!r}")
        return self._poses[actor]

    def actors(self) -> list[str]:
        return list(self._poses)

    def move(self, actor: str, dx: float, dz: float) -> Pose:
        """A player's step, cut to MAX_STEP_M; walls and closed doors stop it (`last_move_ok` is False)."""
        p = self.pose(actor)
        self.stop(actor)
        length = math.hypot(dx, dz)
        if length > MAX_STEP_M:
            dx, dz = dx * MAX_STEP_M / length, dz * MAX_STEP_M / length
        target = (p.x + dx, p.z + dz)
        self.last_move_ok = length > 0 and self._segment_free((p.x, p.z), target)
        if self.last_move_ok:
            self._place(actor, target, math.degrees(math.atan2(dz, dx)))
        return self._poses[actor]

    def walk_to(self, actor: str, target: Point) -> list[Point] | None:
        """Walk along the shortest path at SPEED_M_S; None if no path exists (the walk is not started)."""
        p = self.pose(actor)
        self.stop(actor)
        path = self._plan((p.x, p.z), target)
        if path is None:
            return None
        token = self._walk[actor] = self._walk.get(actor, 0) + 1
        self._schedule_walk(actor, token, path, 1)
        return path

    def stop(self, actor: str) -> None:
        self._walk[actor] = self._walk.get(actor, 0) + 1

    # -- doors --------------------------------------------------------------------------------
    def add_door(self, doorway_id: str, open: bool = True) -> None:  # noqa: A002 - "open" is the door's state
        try:
            self.map.doorway(doorway_id)
        except MapError as e:
            raise WorldError(str(e)) from e
        self.doors[doorway_id] = open
        self._recompute_blocked()

    def set_door(self, doorway_id: str, open: bool) -> bool:  # noqa: A002
        if doorway_id not in self.doors:
            raise WorldError(f"no door on doorway {doorway_id!r}; add it to the home's doors first")
        if self.doors[doorway_id] != open:
            self.doors[doorway_id] = open
            self._recompute_blocked()
            self._emit({"event": "door", "id": doorway_id, "open": open})
        return True

    # -- internals ----------------------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        for fn in self._handlers:
            fn(event)

    def _emit_pose(self, p: Pose) -> None:
        self._emit({"event": "pose", "actor": p.actor, "x": p.x, "z": p.z, "yaw": p.yaw, "room": p.room})

    def _place(self, actor: str, at: Point, yaw: float) -> None:
        old = self._poses[actor]
        room = self.map.room_at(*at)
        rid = room.id if room else None
        self._poses[actor] = Pose(actor, old.model, at[0], at[1], yaw, rid)
        self._emit_pose(self._poses[actor])
        if rid is not None and rid != self._last_room.get(actor):
            self._emit({"event": "room", "actor": actor, "room": rid, "previous": self._last_room.get(actor)})
            self._last_room[actor] = rid

    def _recompute_blocked(self) -> None:
        self._blocked = frozenset(c for d, is_open in self.doors.items() if not is_open
                                  for c in self.map.doorway(d).cells)

    def _cell_free(self, i: int, j: int) -> bool:
        return self.map.grid.is_walkable(i, j) and (i, j) not in self._blocked

    def _free(self, x: float, z: float) -> bool:
        c = self.map.grid.cell_of(x, z)
        return c is not None and self._cell_free(*c)

    def _segment_free(self, a: Point, b: Point) -> bool:
        n = max(1, math.ceil(distance(a, b) / (self.map.grid.cell / 2)))
        return all(self._free(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1))

    def _schedule_walk(self, actor: str, token: int, path: list[Point], k: int) -> None:
        def step():
            if self._walk.get(actor) != token:
                return  # replaced or stopped
            p = self._poses[actor]
            budget, here, idx = SPEED_M_S * STEP_S, (p.x, p.z), k
            traversed = [here]  # the step follows the path's corners; a chord across a corner could cut a wall
            while budget > 1e-9 and idx < len(path):
                d = distance(here, path[idx])
                if d <= budget:
                    budget, here, idx = budget - d, path[idx], idx + 1
                else:
                    f = budget / d
                    here, budget = (here[0] + (path[idx][0] - here[0]) * f, here[1] + (path[idx][1] - here[1]) * f), 0
                traversed.append(here)
            if not all(self._segment_free(a, b) for a, b in zip(traversed, traversed[1:])):  # a door closed ahead
                self.stop(actor)
                self._emit({"event": "blocked", "actor": actor})
                return
            yaw = math.degrees(math.atan2(here[1] - p.z, here[0] - p.x)) if here != (p.x, p.z) else p.yaw
            self._place(actor, here, yaw)
            if idx >= len(path):
                self._emit({"event": "arrived", "actor": actor})
            else:
                self._schedule_walk(actor, token, path, idx)
        self.clock.call_at(self.clock.now() + STEP_S, step)

    def _plan(self, start: Point, target: Point) -> list[Point] | None:
        """A* over free cells (8-connected, no corner cutting), then straightened along free lines of sight."""
        g = self.map.grid
        s, goal = g.cell_of(*start), self._nearest_free_cell(target)
        if s is None or goal is None:
            return None
        diag = math.sqrt(2)

        def h(c):
            dx, dz = abs(c[0] - goal[0]), abs(c[1] - goal[1])
            return (dx + dz) + (diag - 2) * min(dx, dz)

        best, came, frontier = {s: 0.0}, {}, [(h(s), 0, s)]
        tie = 0
        while frontier:
            _, _, c = heapq.heappop(frontier)
            if c == goal:
                break
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if (di, dj) == (0, 0):
                        continue
                    n = (c[0] + di, c[1] + dj)
                    if not self._cell_free(*n) or (di and dj and not (self._cell_free(c[0] + di, c[1])
                                                                     and self._cell_free(c[0], c[1] + dj))):
                        continue
                    cost = best[c] + (diag if di and dj else 1.0)
                    if cost < best.get(n, math.inf):
                        best[n], came[n] = cost, c
                        tie += 1
                        heapq.heappush(frontier, (cost + h(n), tie, n))
        if goal not in best:
            return None
        cells = [goal]
        while cells[-1] != s:
            cells.append(came[cells[-1]])
        cells.reverse()
        end = target if self._free(*target) else g.center(*goal)
        points = [start] + [g.center(*c) for c in cells[1:-1]] + [end]
        straight = [points[0]]
        k = 0
        while k < len(points) - 1:  # string-pulling: jump to the farthest point still in a free line of sight
            far = len(points) - 1
            while far > k + 1 and not self._segment_free(points[k], points[far]):
                far -= 1
            straight.append(points[far])
            k = far
        return straight

    def _nearest_free_cell(self, p: Point) -> tuple[int, int] | None:
        g = self.map.grid
        c = g.cell_of(*p)
        if c is not None and self._cell_free(*c):
            return c
        if c is None:
            return None
        for r in range(1, 6):  # within half a metre: a target placed on furniture or a wall
            for i in range(c[0] - r, c[0] + r + 1):
                for j in range(c[1] - r, c[1] + r + 1):
                    if self._cell_free(i, j):
                        return (i, j)
        return None
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `15 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/mapworld.py tests/lab/test_mapworld.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the map world: people who step and walk, doors that stop them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The home file v2: real products placed in the scene

**Files:**
- Create: `vesper/lab/home.py`
- Create tests: `tests/lab/homes.py` (a small catalog and a home on the synthetic house), `tests/lab/test_home.py`

**Interfaces:**
- Consumes: `WorldMap`, `MapError`, `suggest` (Task 1); `topic_safe` from `vesper/exposure/z2m_bridge.py`; `Twin`, `TwinError` from `vesper/twins/twin.py`.
- Produces, in `vesper/lab/home.py`:
  - `HomeError`, `MOUNTS`;
  - `PlacedDevice(name, product, record, room, door, position, mount, facing, controls, state)`;
  - `LabHome(path, map, scene, seed, start_time, avatar, devices, doors, residents, automations, defenses, broker, matter_interface)` with `device(name)`;
  - `load_lab_home(path, catalog) -> LabHome`, `spawn_point(world_map, room_id) -> Point`.
- Test fixtures: `tests.lab.homes.catalog()`, `HOME`, `write_home(tmp_path, text=HOME, world_map=None)`, `LOCK`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/homes.py`:
```python
"""A small catalog and a home on the synthetic house, for the Lab tests."""
import textwrap

from tests.catalog.test_matter_normalize import records as matter_records
from tests.lab.maps import house
from tests.twins.conftest import records as zigbee_records

LOCK = "matter:115f:0001"


def catalog():
    out = {r["id"]: r for r in zigbee_records().values()}
    out[LOCK] = matter_records()[LOCK]
    return out


HOME = """\
    version: 2
    scene: test/house
    seed: 3
    start_time: "07:30"
    avatar: {model: female_0, start: {room: hall}}
    doors:
      - {doorway: bedroom-hall, open: true}
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: hall-porch}}
      - {product: "matter:115f:0001", name: front_lock, at: {door: hall-porch},
         state: {"Door Lock.LockState": Unlocked}}
      - {product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}
      - {product: "zigbee:acme:dim-1", name: bedroom_lamp, at: {room: bedroom, xy: [5.0, 5.0], mount: table}}
      - {product: "zigbee:acme:btn-1", name: bedroom_button, at: {room: bedroom, xy: [3.2, 3.5], mount: wall},
         controls: [bedroom_lamp]}
      - {product: "zigbee:acme:th-1", name: kitchen_climate, at: {room: kitchen, xy: [6.5, 0.5], mount: wall,
         facing: 90}}
"""


def write_home(tmp_path, text=HOME, world_map=None):
    (world_map or house()).save(tmp_path / "map.json")
    path = tmp_path / "home.yaml"
    path.write_text(textwrap.dedent(text))
    return path
```

Create `tests/lab/test_home.py`:
```python
import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import HomeError, load_lab_home


def load(tmp_path, text=HOME):
    return load_lab_home(write_home(tmp_path, text), catalog())


def test_a_home_places_real_products_in_the_scene(tmp_path):
    home = load(tmp_path)
    d = {p.name: p for p in home.devices}
    assert home.scene == "test/house" and home.seed == 3 and home.start_time == 7.5 * 3600
    assert d["front_door"].door == "hall-porch" and d["front_door"].mount == "door"
    assert d["front_door"].position == pytest.approx((0.0, 3.0), abs=0.15) and d["front_door"].room == "hall"
    assert d["hall_motion"].room == "hall" and d["hall_motion"].mount == "ceiling"
    assert d["bedroom_lamp"].position == (5.0, 5.0) and d["bedroom_lamp"].record["id"] == "zigbee:acme:dim-1"
    assert d["bedroom_button"].controls == ("bedroom_lamp",)
    assert d["kitchen_climate"].facing == 90.0
    # a wall mount without `facing` faces its room's centre
    assert 0 <= d["bedroom_button"].facing < 90 or d["bedroom_button"].facing > 270


def test_doors_come_from_the_doors_list_and_from_devices_mounted_on_them(tmp_path):
    home = load(tmp_path)
    assert home.doors == {"bedroom-hall": True, "hall-porch": False}  # a door a device sits on starts closed


def test_the_player_starts_on_the_walkable_floor_of_the_named_room(tmp_path):
    home = load(tmp_path)
    x, z = home.avatar["start"]
    assert home.map.room_at(x, z).id == "hall" and home.map.grid.is_walkable(*home.map.grid.cell_of(x, z))
    assert home.avatar["model"] == "female_0"


@pytest.mark.parametrize("change,message", [
    ('"zigbee:acme:pir-1", name: hall_motion', r"devices\[2\]\.product: 'zigbee:acme:pir-9' is not in the catalog; "
                                               r"did you mean zigbee:acme:pir-1"),
    ("{room: hall, mount: ceiling}", r"devices\[2\]\.at\.room: unknown room 'hal'; did you mean hall"),
    ("xy: [5.0, 5.0]", r"devices\[3\]\.at\.xy: \(1.0, 1.0\) is not in room 'bedroom'"),
    ("{door: hall-porch}}\n      - {product: \"matter", r"devices\[0\]\.at\.door: unknown doorway 'kitchen-bedroom'"),
    ("controls: [bedroom_lamp]", r"devices\[4\]\.controls: unknown device 'lamp'; did you mean bedroom_lamp"),
    ("name: hall_motion", r"devices\[2\]\.name: 'front_door' is used twice"),
    ("mount: ceiling}", r"devices\[2\]\.at\.mount: 'roof' is not one of ceiling, wall, table, floor, door"),
    ("name: front_door", r"devices\[0\]\.name: 'bridge/x' cannot be an MQTT topic"),
])
def test_mistakes_name_the_field_and_the_nearest_valid_choice(tmp_path, change, message):
    replacements = {
        '"zigbee:acme:pir-1", name: hall_motion': '"zigbee:acme:pir-9", name: hall_motion',
        "{room: hall, mount: ceiling}": "{room: hal, mount: ceiling}",
        "xy: [5.0, 5.0]": "xy: [1.0, 1.0]",
        "{door: hall-porch}}\n      - {product: \"matter": "{door: kitchen-bedroom}}\n      - {product: \"matter",
        "controls: [bedroom_lamp]": "controls: [lamp]",
        "name: hall_motion": "name: front_door",
        "mount: ceiling}": "mount: roof}",
        "name: front_door": "name: bridge/x",
    }
    text = HOME.replace(change, replacements[change], 1)
    assert text != HOME
    with pytest.raises(HomeError, match=message):
        load(tmp_path, text)


def test_a_version_1_home_file_points_to_vesper_twins_run(tmp_path):
    with pytest.raises(HomeError, match="version 1.*vesper twins run"):
        load(tmp_path, "devices:\n  - {product: 'zigbee:aqara:mccgq11lm', name: door}\n")


def test_a_wall_mount_needs_a_point(tmp_path):
    text = HOME.replace("at: {room: bedroom, xy: [3.2, 3.5], mount: wall}", "at: {room: bedroom, mount: wall}")
    with pytest.raises(HomeError, match=r"devices\[4\]\.at\.xy: a wall mount needs a point"):
        load(tmp_path, text)


def test_a_device_may_start_in_a_given_state_checked_against_the_real_product(tmp_path):
    assert load(tmp_path).device("front_lock").state == {"Door Lock.LockState": "Unlocked"}
    bad = HOME.replace("{\"Door Lock.LockState\": Unlocked}", "{\"Door Lock.LockState\": Ajar}")
    with pytest.raises(HomeError, match=r"devices\[1\]\.state: .*'Ajar' is not one of"):
        load(tmp_path, bad)
    unknown = HOME.replace("{\"Door Lock.LockState\": Unlocked}", "{\"Door Lock.Bolt\": 1}")
    with pytest.raises(HomeError, match=r"devices\[1\]\.state: .*unknown feature 'Door Lock.Bolt'"):
        load(tmp_path, unknown)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_home.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.home'`

- [ ] **Step 3: Implement**

Create `vesper/lab/home.py`:
```python
"""The Lab home file, version 2 (spec §4): a scene, real products placed in it, the doors, the people.

    version: 2
    scene: hssd/102344022
    map: map.json                      # the scene's map file, next to home.yaml by default
    seed: 0
    start_time: "07:00"
    avatar: {model: female_0, start: {room: entryway}}
    doors: [{doorway: entryway-porch, open: false}]
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: entryway-porch}}
      - {product: "zigbee:philips:9290012607", name: living_motion, at: {room: living_room, mount: ceiling}}
      - {product: "zigbee:ikea:led1545g12", name: lamp, at: {room: living_room, xy: [3.2, -1.4], mount: table}}
      - {product: "zigbee:acme:btn-1", name: switch, at: {room: hall, xy: [0.3, 1], mount: wall},
         controls: [lamp]}

Every mistake names its field and, where one exists, the nearest valid choice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from vesper.exposure.z2m_bridge import topic_safe
from vesper.twins.twin import Twin, TwinError
from vesper.lab.geometry import Point, centroid, distance, point_in_polygon
from vesper.lab.worldmap import MapError, WorldMap, suggest

MOUNTS = ("ceiling", "wall", "table", "floor", "door")


class HomeError(ValueError):
    """A home file VESPER cannot use; the message names the field."""


@dataclass(frozen=True)
class PlacedDevice:
    name: str  # also its Zigbee2MQTT friendly name
    product: str
    record: dict
    room: str | None
    door: str | None
    position: Point
    mount: str
    facing: float | None  # degrees, for wall mounts; 0 is +x, 90 is +z
    controls: tuple[str, ...] = ()  # devices this one switches directly (a Zigbee binding)
    state: dict = field(default_factory=dict)  # starting values, checked against the product's features


@dataclass
class LabHome:
    path: Path
    map: WorldMap
    scene: str
    seed: int
    start_time: float  # seconds after midnight
    avatar: dict  # {"model": str, "start": Point}
    devices: list[PlacedDevice]
    doors: dict[str, bool]  # doorway id -> open at the start
    residents: list[dict] = field(default_factory=list)
    automations: Path | None = None
    defenses: list[str] = field(default_factory=list)
    broker: tuple[str, int] = ("127.0.0.1", 18830)
    matter_interface: str | None = None

    def device(self, name: str) -> PlacedDevice:
        for d in self.devices:
            if d.name == name:
                return d
        raise HomeError(f"unknown device {name!r}; {suggest(name, [d.name for d in self.devices])}")


def load_lab_home(path: Path, catalog: dict[str, dict]) -> LabHome:
    path = Path(path)
    spec = yaml.safe_load(path.read_text()) or {}
    if not isinstance(spec, dict) or spec.get("version") != 2:
        raise HomeError(f"{path}: a version 1 home file (no `version: 2`); it is for `vesper twins run`. "
                        "A Lab home names a scene, a map and where each device is.")
    try:
        world_map = WorldMap.load(path.parent / spec.get("map", "map.json"))
    except MapError as e:
        raise HomeError(f"map: {e}") from e
    doors = {}
    for k, d in enumerate(spec.get("doors") or []):
        _doorway(world_map, d.get("doorway"), f"doors[{k}].doorway")
        doors[d["doorway"]] = bool(d.get("open", True))
    devices: list[PlacedDevice] = []
    raw = spec.get("devices") or []
    names = [d.get("name") for d in raw]
    for k, d in enumerate(raw):
        devices.append(_device(k, d, catalog, world_map, doors, names))
    avatar = spec.get("avatar") or {}
    start = (avatar.get("start") or {}).get("room")
    if start is None:
        raise HomeError("avatar.start.room: name the room the player starts in")
    try:
        world_map.room(start)
    except MapError as e:
        raise HomeError(f"avatar.start.room: {e}") from e
    broker = spec.get("broker") or {}
    automations = spec.get("automations")
    return LabHome(path=path, map=world_map, scene=str(spec.get("scene", world_map.scene)),
                   seed=int(spec.get("seed", 0)), start_time=_clock(spec.get("start_time", "07:00")),
                   avatar={"model": avatar.get("model", "female_0"), "start": spawn_point(world_map, start)},
                   devices=devices, doors=doors, residents=list(spec.get("residents") or []),
                   automations=path.parent / automations if automations else None,
                   defenses=list((spec.get("security") or {}).get("defenses") or []),
                   broker=(broker.get("host", "127.0.0.1"), int(broker.get("port", 18830))),
                   matter_interface=spec.get("matter_interface"))


def spawn_point(world_map: WorldMap, room_id: str) -> Point:
    """The walkable cell of `room_id` nearest its centre."""
    room, g = world_map.room(room_id), world_map.grid
    c = centroid(room.polygon)
    best = None
    for j in range(g.height):
        for i in range(g.width):
            if g.is_walkable(i, j) and (r := world_map.room_of_cell(i, j)) is not None and r.id == room_id:
                d = distance(g.center(i, j), c)
                if best is None or d < best[0]:
                    best = (d, g.center(i, j))
    if best is None:
        raise HomeError(f"room {room_id!r} has no walkable floor")
    return best[1]


def _device(k: int, d: dict, catalog: dict, world_map: WorldMap, doors: dict, names: list) -> PlacedDevice:
    where = f"devices[{k}]"
    product, name = d.get("product"), d.get("name")
    if product not in catalog:
        raise HomeError(f"{where}.product: {product!r} is not in the catalog; {suggest(str(product), list(catalog))}")
    if not isinstance(name, str) or not topic_safe(name):
        raise HomeError(f"{where}.name: {name!r} cannot be an MQTT topic (no '+', '#', empty levels, 'set', 'get', "
                        "'event', a leading 'bridge' or a trailing 'availability')")
    if names.index(name) != k:
        raise HomeError(f"{where}.name: {name!r} is used twice")
    at = d.get("at") or {}
    door = at.get("door")
    mount = at.get("mount", "door" if door else "table" if "xy" in at else "ceiling")
    if mount not in MOUNTS:
        raise HomeError(f"{where}.at.mount: {mount!r} is not one of {', '.join(MOUNTS)}")
    if door is not None:
        dw = _doorway(world_map, door, f"{where}.at.door")
        doors.setdefault(door, False)  # a door with a sensor or a lock on it starts closed
        indoor = [r for r in dw.rooms if not world_map.room(r).outdoor]
        room, position, mount = (indoor or list(dw.rooms))[0], dw.center, "door"
    else:
        room = at.get("room")
        if room is None:
            raise HomeError(f"{where}.at: name a room or a door")
        try:
            polygon = world_map.room(room).polygon
        except MapError as e:
            raise HomeError(f"{where}.at.room: {e}") from e
        if "xy" in at:
            position = (float(at["xy"][0]), float(at["xy"][1]))
            if not point_in_polygon(position, polygon):
                raise HomeError(f"{where}.at.xy: {position} is not in room {room!r}")
        elif mount in ("wall", "table", "floor"):
            raise HomeError(f"{where}.at.xy: a {mount} mount needs a point (metres, in the map's x and z)")
        else:
            position = centroid(polygon)
    facing = None
    if mount == "wall":
        if "facing" in at:
            facing = float(at["facing"]) % 360
        else:
            c = centroid(world_map.room(room).polygon)
            facing = math.degrees(math.atan2(c[1] - position[1], c[0] - position[0])) % 360
    controls = tuple(d.get("controls") or ())
    for target in controls:
        if target not in names:
            raise HomeError(f"{where}.controls: unknown device {target!r}; {suggest(target, [n for n in names if n])}")
    state = dict(d.get("state") or {})
    twin = Twin.from_vds(catalog[product])
    for feature, value in state.items():
        try:
            state[feature] = twin.coerce(feature, value)
        except TwinError as e:
            raise HomeError(f"{where}.state: {e}") from e
    return PlacedDevice(name, product, catalog[product], room, door, position, mount, facing, controls, state)


def _doorway(world_map: WorldMap, doorway_id, where: str):
    try:
        return world_map.doorway(doorway_id)
    except MapError as e:
        raise HomeError(f"{where}: {e}") from e


def _clock(text) -> float:
    try:
        hours, minutes = str(text).split(":")
        return int(hours) * 3600 + int(minutes) * 60
    except ValueError as e:
        raise HomeError(f"start_time: {text!r} is not HH:MM") from e
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `29 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/home.py tests/lab/homes.py tests/lab/test_home.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the home file v2: real products placed in the scene

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: The game rules: people and devices, coupled

**Files:**
- Create: `vesper/lab/rules.py`
- Create tests: `tests/lab/test_rules.py`

**Interfaces:**
- Consumes: `LabHome`, `PlacedDevice` (Task 3); `MapWorld` events and `set_door`, `pose`, `doors` (Task 2); `Runtime.operate`, `.stimulate`, `.subscribe`, `.state`, `.twins`, `.clock` and `MATTER_AMBIENT`, `MATTER_CONTACT`, `MATTER_PRESENCE` from `vesper/twins/runtime.py`.
- Produces, in `vesper/lab/rules.py`:
  - the constants `INTERACT_M`, `WALL_RANGE_M`, `WALL_FOV_DEG`, `RETRIGGER_S`, `AMBIENT_EVERY_S`, `LAMP_LUX`;
  - `ambient_at(seconds_of_day, lamps_on) -> dict`, `toggle_command(twin)`, `is_on(twin)`, `is_locked(twin)`;
  - `Rules(home, runtime, world, twin_ids, record=None)` with `start()`, `reachable(actor)`, `interact(actor, target=None) -> {"ok", "kind", "target", "result"}`, `effects`, `on_effect(fn)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_rules.py`:
```python
from types import SimpleNamespace

import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.mapworld import MapWorld
from vesper.lab.rules import Rules, ambient_at
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Runtime


def lab(tmp_path, text=HOME, at=None):
    home = load_lab_home(write_home(tmp_path, text), catalog())
    clock = LogicalClock()
    runtime = Runtime(clock=clock, seed=home.seed)
    ids = {d.name: runtime.add(d.record, twin_id=f"{d.product}@{d.name}", state=d.state).id for d in home.devices}
    world = MapWorld(home.map, clock)
    for door, is_open in home.doors.items():
        world.add_door(door, is_open)
    log = []
    rules = Rules(home, runtime, world, ids, record=log.append)
    rules.start()
    world.spawn("p", "female_0", at or home.avatar["start"])
    return SimpleNamespace(home=home, clock=clock, runtime=runtime, world=world, rules=rules, log=log, ids=ids,
                           state=lambda name: runtime.state(ids[name]))


def go(t, point):
    t.world.walk_to("p", point)
    t.clock.advance(30.0)


def test_walking_in_trips_a_ceiling_motion_sensor_and_keeping_still_lets_it_clear(tmp_path):
    t = lab(tmp_path, at=(5.0, 1.5))  # the kitchen
    assert t.state("hall_motion")["occupancy"] is False
    go(t, (1.5, 3.0))  # the hall
    assert t.state("hall_motion")["occupancy"] is True
    t.clock.advance(OCCUPANCY_TIMEOUT_S + 5)  # standing still: a PIR sees no motion
    assert t.state("hall_motion")["occupancy"] is False
    t.world.move("p", 0.3, 0.0)
    assert t.state("hall_motion")["occupancy"] is True
    assert any(r["rule"] == "presence" and r["device"] == "hall_motion" for r in t.log)


def test_a_wall_sensor_sees_its_cone_only(tmp_path):
    text = HOME + '      - {product: "zigbee:acme:pir-1", name: kitchen_pir, at: {room: kitchen, xy: [3.2, 0.2],\n' \
                  '         mount: wall, facing: 0}}\n'
    t = lab(tmp_path, text, at=(3.5, 2.7))  # in the kitchen, beside the sensor, outside its 110° cone
    assert t.state("kitchen_pir")["occupancy"] is False
    go(t, (5.0, 1.5))
    assert t.state("kitchen_pir")["occupancy"] is True


def test_the_front_door_opens_and_closes_by_hand_and_its_contact_sensor_reports_it(tmp_path):
    t = lab(tmp_path)
    go(t, (0.4, 3.0))
    assert t.state("front_door")["contact"] is True  # closed (Zigbee2MQTT: contact true = closed)
    assert t.rules.interact("p") == {"ok": True, "kind": "door", "target": "hall-porch", "result": "opened"}
    assert t.world.doors["hall-porch"] is True and t.state("front_door")["contact"] is False
    assert t.rules.interact("p")["result"] == "closed" and t.state("front_door")["contact"] is True


def test_a_locked_door_stays_shut(tmp_path):
    t = lab(tmp_path)
    go(t, (0.4, 3.0))
    assert t.rules.interact("p", "front_lock")["result"] == "toggled"
    assert t.state("front_lock")["Door Lock.LockState"] == "Locked"
    assert t.rules.interact("p") == {"ok": False, "kind": "door", "target": "hall-porch", "result": "locked"}
    assert t.world.doors["hall-porch"] is False
    assert t.rules.effects["hall-porch"] == {"open": False, "locked": True}


def test_a_lamp_toggles_by_hand_and_a_bound_button_switches_it(tmp_path):
    t = lab(tmp_path)
    go(t, (5.0, 4.4))
    assert t.rules.interact("p")["target"] == "bedroom_lamp" and t.state("bedroom_lamp")["state"] == "ON"
    assert t.runtime.actions[-1].actor == "human:p"
    assert t.rules.effects["bedroom_lamp"] == {"on": True}
    go(t, (3.6, 3.6))
    assert t.rules.interact("p", "bedroom_button")["result"] == "pressed"
    assert t.state("bedroom_lamp")["state"] == "OFF" and t.runtime.actions[-1].actor == "human:p via bedroom_button"
    assert t.state("bedroom_button")["action"] is not None


def test_only_what_a_person_can_reach_and_operate_is_a_target(tmp_path):
    t = lab(tmp_path, at=(1.5, 1.0))
    assert t.rules.interact("p") == {"ok": False, "kind": None, "target": None, "result": "nothing in reach"}
    assert t.rules.interact("p", "bedroom_lamp")["result"] == "out of reach"
    with pytest.raises(ValueError, match="hall_motion is not something a person operates"):
        t.rules.interact("p", "hall_motion")


def test_ambient_follows_the_time_of_day_and_the_lamps():
    night, noon = ambient_at(3 * 3600, 0), ambient_at(12.5 * 3600, 0)
    assert night["illuminance"] == 0 and noon["illuminance"] > 200
    assert ambient_at(3 * 3600, 2)["illuminance"] == 400  # two lamps on at night
    assert ambient_at(15 * 3600, 0)["temperature"] > ambient_at(4 * 3600, 0)["temperature"]


def test_the_climate_sensor_follows_the_room(tmp_path):
    t = lab(tmp_path)  # starts 07:30
    morning = t.state("kitchen_climate")["temperature"]
    t.clock.advance(7.5 * 3600)  # 15:00
    assert t.state("kitchen_climate")["temperature"] > morning
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_rules.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.rules'`

- [ ] **Step 3: Implement**

Create `vesper/lab/rules.py`:
```python
"""The game rules (spec §5): how people in the world use the devices, and how the devices change the world.

World → devices:
- presence: a ceiling motion sensor covers its room; a wall-mounted one a 7 m, 110° cone in its room. A person
  entering coverage, or moving in it at least every RETRIGGER_S, applies the `presence` stimulus. A person
  keeping still lets the sensor clear by the twin's own timeout, as a real PIR does. Line of sight is taken to
  be the sensor's own room;
- doors: a person within reach opens or closes a door, unless a lock on it is locked; a contact sensor on the
  door reports the change;
- devices: a person within reach operates a lamp, plug, switch or lock by hand, or presses a button; a device
  with `controls` switches those devices too, as a Zigbee binding does;
- ambient: every room's temperature, humidity and illuminance follow the time of day and the lamps that are on
  (an assumed model: no windows, no weather), applied as `ambient` stimuli every AMBIENT_EVERY_S.

Devices → world: `effects` holds what the views show, a light's or plug's on/off, a lock's state, a door's
state; `on_effect` handlers hear every change.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from vesper.lab.geometry import distance, in_cone
from vesper.twins.runtime import MATTER_AMBIENT, MATTER_CONTACT, MATTER_PRESENCE

INTERACT_M = 1.0  # how far a person reaches
WALL_RANGE_M, WALL_FOV_DEG = 7.0, 110.0  # a wall-mounted motion sensor's coverage (assumed category default)
RETRIGGER_S = 10.0  # a person moving inside coverage re-triggers a motion sensor this often
AMBIENT_EVERY_S = 60.0
LAMP_LUX = 200  # what one lamp adds to its room's illuminance (assumed)
ONOFF, LOCK_STATE = "On/Off.OnOff", "Door Lock.LockState"
_AMBIENT = {"temperature", "local_temperature", "humidity", "illuminance", "illuminance_lux"} | {
    n for names in MATTER_AMBIENT.values() for n in names}


def ambient_at(seconds_of_day: float, lamps_on: int) -> dict:
    """The assumed indoor climate at a time of day: 21 °C ± 1.5 (warmest at 15:00), 45 % humidity, daylight
    rising from 06:00 to a 300 lx noon and gone at 20:00, plus LAMP_LUX per lamp on."""
    h = (seconds_of_day / 3600) % 24
    daylight = 300 * math.sin(math.pi * (h - 6) / 14) if 6 < h < 20 else 0.0
    return {"temperature": round(21 + 1.5 * math.sin(2 * math.pi * (h - 9) / 24), 1), "humidity": 45.0,
            "illuminance": round(daylight + LAMP_LUX * lamps_on)}


def toggle_command(twin) -> tuple[str, str | None, object] | None:
    """What a person's hand does to this device: ("operate", feature, value), ("press", None, None), or None for
    a device nobody operates by hand (a sensor)."""
    feats, state = twin.features, twin.state
    f = feats.get("state")
    if f and f["type"] == "binary":
        if "value_toggle" in f:
            return ("operate", "state", f["value_toggle"])
        return ("operate", "state", f["value_off"] if state["state"] == f["value_on"] else f["value_on"])
    if ONOFF in feats:
        return ("operate", ONOFF, not state[ONOFF])
    if LOCK_STATE in feats:
        return ("operate", LOCK_STATE, "Unlocked" if state[LOCK_STATE] == "Locked" else "Locked")
    if feats.get("action", {}).get("type") == "enum":
        return ("press", None, None)
    return None


def is_on(twin) -> bool | None:
    f = twin.features.get("state")
    if f and f["type"] == "binary" and twin.vds["identity"]["category"] != "lock":
        return twin.state["state"] == f["value_on"]
    if ONOFF in twin.features:
        return bool(twin.state[ONOFF])
    return None


def is_locked(twin) -> bool | None:
    if LOCK_STATE in twin.features:
        return twin.state[LOCK_STATE] == "Locked"
    f = twin.features.get("state")
    if twin.vds["identity"]["category"] == "lock" and f and f["type"] == "binary":
        return twin.state["state"] == f["value_on"]
    return None


def _is_presence(twin) -> bool:
    return any(twin.features.get(n, {}).get("type") == "binary" for n in ("occupancy", "presence")) or any(
        n in twin.features for n in MATTER_PRESENCE)


def _is_contact(twin) -> bool:
    return twin.features.get("contact", {}).get("type") == "binary" or MATTER_CONTACT in twin.features


class Rules:
    def __init__(self, home, runtime, world, twin_ids: dict[str, str], record: Callable[[dict], None] | None = None):
        self.home, self.runtime, self.world, self.twin_ids = home, runtime, world, twin_ids
        self.record = record or (lambda r: None)
        self.devices = {d.name: d for d in home.devices}
        self._by_twin = {tid: name for name, tid in twin_ids.items()}
        self.effects: dict[str, dict] = {}
        self._effect_handlers: list[Callable[[str, dict], None]] = []
        self._covered: dict[tuple[str, str], bool] = {}
        self._last_trigger: dict[tuple[str, str], float] = {}

    # -- set-up -------------------------------------------------------------------------------
    def start(self) -> None:
        self.world.on_event(self._on_world)
        self.runtime.subscribe(self._on_report)
        for door, is_open in self.world.doors.items():
            if is_open:
                self._contact(door, True)
        for name in self.devices:
            self._update_effect(name)
        for door in self.world.doors:
            self._update_effect(door)
        self._ambient_tick()

    def on_effect(self, fn: Callable[[str, dict], None]) -> None:
        self._effect_handlers.append(fn)

    def twin(self, name: str):
        return self.runtime.twins[self.twin_ids[name]]

    def now(self) -> float:
        return self.runtime.clock.now()

    # -- people using things ------------------------------------------------------------------
    def reachable(self, actor: str) -> list[tuple[float, str, str]]:
        """(distance, kind, target) within reach, nearest first; doors before devices at the same distance."""
        p = self.world.pose(actor)
        here = (p.x, p.z)
        out = [(distance(here, self.home.map.doorway(d).center), "door", d) for d in self.world.doors]
        out += [(distance(here, dev.position), "device", name) for name, dev in self.devices.items()
                if self._operable(name)]
        return sorted((t for t in out if t[0] <= INTERACT_M), key=lambda t: (round(t[0], 6), t[1] != "door", t[2]))

    def interact(self, actor: str, target: str | None = None) -> dict:
        if target is None:
            reach = self.reachable(actor)
            if not reach:
                return self._done(actor, {"ok": False, "kind": None, "target": None, "result": "nothing in reach"})
            _, kind, target = reach[0]
        elif target in self.world.doors:
            kind = "door"
        elif target in self.devices:
            if not self._operable(target):
                raise ValueError(f"{target} is not something a person operates")
            kind = "device"
        else:
            raise ValueError(f"unknown door or device {target!r}")
        p = self.world.pose(actor)
        where = self.home.map.doorway(target).center if kind == "door" else self.devices[target].position
        if distance((p.x, p.z), where) > INTERACT_M:
            return self._done(actor, {"ok": False, "kind": kind, "target": target, "result": "out of reach"})
        if kind == "door":
            if not self.world.doors[target] and self._door_locked(target):
                return self._done(actor, {"ok": False, "kind": "door", "target": target, "result": "locked"})
            self.world.set_door(target, not self.world.doors[target])
            result = "opened" if self.world.doors[target] else "closed"
            return self._done(actor, {"ok": True, "kind": "door", "target": target, "result": result})
        result = self._use(target, actor)
        device = self.devices[target]
        for other in device.controls:
            self._bound(device.name, other, actor)
        return self._done(actor, {"ok": True, "kind": "device", "target": target, "result": result})

    def _use(self, name: str, actor: str) -> str:
        kind, feature, value = toggle_command(self.twin(name))
        if kind == "press":
            self.runtime.stimulate(self.twin_ids[name], {"type": "button_press"})
            return "pressed"
        self.runtime.operate(self.twin_ids[name], feature, value, actor=actor)
        return "toggled"

    def _bound(self, controller: str, target: str, actor: str) -> None:
        """A Zigbee binding: a switch sets its target to its own on/off; a button toggles its target."""
        twin = self.twin(target)
        command = toggle_command(twin)
        if command is None or command[0] != "operate":
            return
        _, feature, value = command
        want = is_on(self.twin(controller))
        if want is not None and is_on(twin) == want:
            return
        self.runtime.operate(self.twin_ids[target], feature, value, actor=f"{actor} via {controller}")
        self.record({"t": self.now(), "rule": "binding", "device": target, "via": controller})

    def _done(self, actor: str, outcome: dict) -> dict:
        self.record({"t": self.now(), "rule": "interact", "actor": actor, "target": outcome["target"],
                     "target_kind": outcome["kind"], "ok": outcome["ok"], "result": outcome["result"]})
        return outcome

    def _operable(self, name: str) -> bool:
        return self.devices[name].mount != "ceiling" and toggle_command(self.twin(name)) is not None

    def _door_locked(self, door: str) -> bool:
        return any(d.door == door and is_locked(self.twin(n)) for n, d in self.devices.items())

    # -- the world, heard -----------------------------------------------------------------------
    def _on_world(self, event: dict) -> None:
        if event["event"] == "pose":
            self._presence(event)
        elif event["event"] == "door":
            self._contact(event["id"], event["open"])
            self._update_effect(event["id"])

    def _presence(self, ev: dict) -> None:
        actor, here, now = ev["actor"], (ev["x"], ev["z"]), self.now()
        for name, dev in self.devices.items():
            twin = self.twin(name)
            if not _is_presence(twin):
                continue
            covered = ev["room"] == dev.room and (dev.mount != "wall" or in_cone(dev.position, dev.facing, here,
                                                                                 WALL_RANGE_M, WALL_FOV_DEG))
            key = (name, actor)
            was, self._covered[key] = self._covered.get(key, False), covered
            if covered and (not was or now - self._last_trigger.get(key, -math.inf) >= RETRIGGER_S):
                self._last_trigger[key] = now
                self.runtime.stimulate(self.twin_ids[name], {"type": "presence"})
                self.record({"t": now, "rule": "presence", "device": name, "actor": actor})

    def _contact(self, door: str, is_open: bool) -> None:
        for name, dev in self.devices.items():
            if dev.door == door and _is_contact(self.twin(name)):
                self.runtime.stimulate(self.twin_ids[name], {"type": "contact",
                                                             "state": "opened" if is_open else "closed"})
                self.record({"t": self.now(), "rule": "contact", "device": name, "open": is_open})

    def _ambient_tick(self) -> None:
        lamps: dict[str, int] = {}
        for name, dev in self.devices.items():
            if dev.room and dev.record["identity"]["category"] == "light" and is_on(self.twin(name)):
                lamps[dev.room] = lamps.get(dev.room, 0) + 1
        for name, dev in self.devices.items():
            if dev.room and _AMBIENT & set(self.twin(name).features):
                self.runtime.stimulate(self.twin_ids[name], {"type": "ambient", **ambient_at(
                    self.home.start_time + self.now(), lamps.get(dev.room, 0))})
        self.runtime.clock.call_at(self.now() + AMBIENT_EVERY_S, self._ambient_tick)

    # -- devices, seen ------------------------------------------------------------------------
    def _on_report(self, event) -> None:
        name = self._by_twin.get(event.twin_id)
        if name is not None:
            self._update_effect(name)
            door = self.devices[name].door
            if door is not None and door in self.world.doors:
                self._update_effect(door)

    def _update_effect(self, key: str) -> None:
        if key in self.devices:
            twin = self.twin(key)
            locked, on = is_locked(twin), is_on(twin)
            effect = {"locked": locked} if locked is not None else {"on": on} if on is not None else None
        else:
            effect = {"open": self.world.doors[key], "locked": self._door_locked(key)}
        if effect is not None and self.effects.get(key) != effect:
            self.effects[key] = effect
            for fn in self._effect_handlers:
                fn(key, effect)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `37 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/rules.py tests/lab/test_rules.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the game rules: presence, doors, devices by hand, bindings, ambient, effects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: The session and its trace, replayed exactly

**Files:**
- Create: `vesper/lab/session.py`
- Create tests: `tests/lab/test_session.py`

**Interfaces:**
- Consumes: `LabHome` (Task 3), `MapWorld` (Task 2), `Rules` (Task 4), `Runtime` and `LogicalClock` from sub-project #1.
- Produces, in `vesper/lab/session.py`:
  - `TRACE_FORMAT = "vesper-trace/1"`, `PLAYER = "player"`;
  - `Session(home, clock=None, world=None, trace=None)` with `start(spawn_player=True)`, `stop()`, `spawn`, `move`, `walk_to`, `interact`, `wait`, `runtime`, `world`, `rules`, `twin_ids`, `records`;
  - `read_trace(path)`, `replay(records, home) -> list[dict]`, `compare_reports(original, again) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_session.py`:
```python
import json

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.session import Session, compare_reports, read_trace, replay


def session(tmp_path, text=HOME, trace=True):
    home = load_lab_home(write_home(tmp_path, text), catalog())
    s = Session(home, trace=tmp_path / "trace.jsonl" if trace else None)
    s.start()
    return s


def script(s):
    s.walk_to((0.4, 3.0))
    s.wait(20)
    s.interact()  # the front door opens
    s.wait(5)
    s.walk_to((5.0, 4.4))
    s.wait(30)
    s.interact()  # the bedroom lamp
    s.wait(200)


def test_a_session_runs_the_home_and_traces_inputs_rules_and_reports(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    records = read_trace(tmp_path / "trace.jsonl")
    meta = records[0]
    assert meta["kind"] == "meta" and meta["format"] == "vesper-trace/1" and meta["seed"] == 3
    assert meta["devices"]["front_door"] == "zigbee:aqara:mccgq11lm"
    inputs = [r for r in records if r["kind"] == "input"]
    assert [r["op"] for r in inputs] == ["spawn", "walk_to", "interact", "walk_to", "interact"]
    assert inputs[2]["outcome"]["result"] == "opened"
    reports = [r for r in records if r["kind"] == "report"]
    assert any(r["device"] == "front_door" and r["feature"] == "contact" and r["value"] is False for r in reports)
    assert any(r["device"] == "bedroom_lamp" and r["value"] == "ON" for r in reports)
    assert any(r["kind"] == "rule" and r["rule"] == "presence" for r in records)
    assert all(a["t"] <= b["t"] for a, b in zip(records[1:], records[2:]))


def test_replaying_the_inputs_reproduces_the_same_device_reports(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    original = read_trace(tmp_path / "trace.jsonl")
    home = load_lab_home(tmp_path / "home.yaml", catalog())
    again = replay(original, home)
    assert compare_reports(original, again) is None
    assert len([r for r in again if r["kind"] == "report"]) > 5


def test_a_replay_on_a_changed_home_names_the_first_difference(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    original = read_trace(tmp_path / "trace.jsonl")
    changed = HOME.replace("mount: table}", "mount: table}, state: {state: \"ON\"}")
    other = tmp_path / "other"
    other.mkdir()
    home = load_lab_home(write_home(other, changed), catalog())
    diff = compare_reports(original, replay(original, home))
    assert diff is not None and "bedroom_lamp" in diff


def test_a_session_without_a_trace_still_runs(tmp_path):
    s = session(tmp_path, trace=False)
    script(s)
    s.stop()
    assert s.runtime.state(s.twin_ids["bedroom_lamp"])["state"] == "ON"
    assert not (tmp_path / "trace.jsonl").exists()


def test_the_trace_is_one_json_object_per_line(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    for line in (tmp_path / "trace.jsonl").read_text().splitlines():
        assert isinstance(json.loads(line), dict)


def test_an_interaction_is_traced_as_a_rule_with_its_target_kind(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    door = [r for r in s.records if r.get("rule") == "interact" and r["target"] == "hall-porch"]
    assert door and door[0]["kind"] == "rule" and door[0]["target_kind"] == "door" and door[0]["result"] == "opened"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_session.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.session'`

- [ ] **Step 3: Implement**

Create `vesper/lab/session.py`:
```python
"""A running Lab home (spec §2, §3): the twins, the world, the rules, the player, and the trace.

The trace (`vesper-trace/1`) is one JSON object per line: a `meta` line first, then `input` (what a person
did), `rule` (a rule that fired), `world` (doors, rooms, arrivals) and `report` (what a device reported, as the
platform sees it), each with its world time `t`. On the logical clock, replaying the inputs reproduces the
device reports exactly (spec success criterion 4); `replay` and `compare_reports` check it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from vesper.lab.mapworld import MapWorld
from vesper.lab.rules import Rules
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime

TRACE_FORMAT = "vesper-trace/1"
PLAYER = "player"


class Session:
    def __init__(self, home, *, clock=None, world=None, trace: Path | None = None):
        self.home = home
        self.clock = clock or LogicalClock()
        self.runtime = Runtime(clock=self.clock, seed=home.seed)
        self.twin_ids = {d.name: self.runtime.add(d.record, twin_id=f"{d.product}@{d.name}", state=d.state).id
                         for d in home.devices}
        self._names = {tid: name for name, tid in self.twin_ids.items()}
        self.world = world or MapWorld(home.map, self.clock)
        for door, is_open in home.doors.items():
            self.world.add_door(door, is_open)
        self.records: list[dict] = []
        self._file = open(trace, "w") if trace else None  # noqa: SIM115 - closed in stop()
        self.rules = Rules(home, self.runtime, self.world, self.twin_ids,
                           record=lambda r: self._write({**r, "kind": "rule"}))

    # -- life cycle ---------------------------------------------------------------------------
    def start(self, spawn_player: bool = True) -> None:
        self._write({"kind": "meta", "format": TRACE_FORMAT, "home": str(self.home.path), "scene": self.home.scene,
                     "seed": self.home.seed, "start_time": self.home.start_time,
                     "devices": {d.name: d.product for d in self.home.devices}})
        self.runtime.subscribe(self._on_report)
        self.world.on_event(self._on_world)
        self.rules.start()
        if spawn_player:
            self.spawn(PLAYER, self.home.avatar["model"], self.home.avatar["start"])

    def stop(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if hasattr(self.clock, "close"):
            self.clock.close()

    # -- inputs (traced) ----------------------------------------------------------------------
    def spawn(self, actor: str, model: str, at) -> None:
        self._input(actor, "spawn", {"model": model, "at": list(at)})
        self.world.spawn(actor, model, tuple(at))

    def move(self, dx: float, dz: float, actor: str = PLAYER):
        self._input(actor, "move", {"dx": dx, "dz": dz})
        return self.world.move(actor, dx, dz)

    def walk_to(self, point, actor: str = PLAYER):
        self._input(actor, "walk_to", {"point": list(point)})
        return self.world.walk_to(actor, tuple(point))

    def interact(self, target: str | None = None, actor: str = PLAYER) -> dict:
        outcome = self.rules.interact(actor, target)
        self._input(actor, "interact", {"target": target}, outcome=outcome)
        return outcome

    def wait(self, seconds: float) -> None:
        if hasattr(self.clock, "advance"):
            self.clock.advance(seconds)
        else:
            time.sleep(seconds)

    # -- the trace ----------------------------------------------------------------------------
    def _input(self, actor: str, op: str, args: dict, **extra) -> None:
        self._write({"kind": "input", "actor": actor, "op": op, "args": args, **extra})

    def _on_report(self, event) -> None:
        self._write({"kind": "report", "device": self._names.get(event.twin_id, event.twin_id),
                     "feature": event.feature, "value": event.value, "physical_t": event.physical_t,
                     "observed_t": event.observed_t, "cause": event.cause})

    def _on_world(self, event: dict) -> None:
        if event["event"] != "pose":  # poses follow from the inputs; the trace keeps what they caused
            self._write({"kind": "world", **event})

    def _write(self, record: dict) -> None:
        rec = {"t": round(self.clock.now(), 6), **record}
        self.records.append(rec)
        if self._file is not None:
            self._file.write(json.dumps(rec) + "\n")


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def replay(records: list[dict], home) -> list[dict]:
    """Re-apply a trace's inputs at their times to a new session of `home` on the logical clock."""
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    for r in records:
        if r["kind"] != "input":
            continue
        s.clock.advance_to(r["t"])
        a = r["args"]
        if r["op"] == "spawn":
            s.spawn(r["actor"], a["model"], a["at"])
        elif r["op"] == "move":
            s.move(a["dx"], a["dz"], actor=r["actor"])
        elif r["op"] == "walk_to":
            s.walk_to(a["point"], actor=r["actor"])
        elif r["op"] == "interact":
            s.interact(a["target"], actor=r["actor"])
    if records:
        s.clock.advance_to(max(r["t"] for r in records))
    s.stop()
    return s.records


def compare_reports(original: list[dict], again: list[dict]) -> str | None:
    """None if both traces hold the same device reports in the same order; else the first difference."""
    keys = ("device", "feature", "value", "observed_t", "cause")
    a = [tuple(r[k] for k in keys) for r in original if r["kind"] == "report"]
    b = [tuple(r[k] for k in keys) for r in again if r["kind"] == "report"]
    for k, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"report {k}: the trace has {dict(zip(keys, x))}, the replay {dict(zip(keys, y))}"
    if len(a) != len(b):
        return f"the trace has {len(a)} reports, the replay {len(b)}"
    return None
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `43 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/session.py tests/lab/test_session.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the session and its trace, replayed exactly on the logical clock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 6: `vesper lab new | check | run | replay`

**Files:**
- Modify: `vesper/cli.py` (`HOME_TEMPLATE`, `_lab`, `_lab_new`, `_lab_run`, `_lab_replay`; the `lab` parser; dispatch)
- Create tests: `tests/lab/test_cli_lab.py`

**Interfaces:**
- Consumes: `load_lab_home`, `HomeError` (Task 3); `WorldMap`, `MapError` (Task 1); `Session`, `read_trace`, `replay`, `compare_reports` (Task 5); `load_catalog` from `vesper/twins/home.py`.
- Produces:
  - `vesper lab new <dir> --map <file>`;
  - `vesper lab check <home> [--catalog]`;
  - `vesper lab run <script.py> --home <home> [--catalog] [--trace]`, where the script defines `run(lab)` and receives the `Session`;
  - `vesper lab replay <trace> [--home] [--catalog]`, which exits 0 when the reports are identical and 1 with the first difference.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_cli_lab.py`:
```python
"""`vesper lab`: the headless Lab commands, on the logical clock; nothing is started."""
import json
import textwrap

import yaml

from tests.lab.homes import HOME, catalog, write_home
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli

SCRIPT = """\
    def run(lab):
        lab.walk_to((0.4, 3.0))
        lab.wait(20)
        lab.interact()
        lab.wait(60)
"""


def setup(tmp_path):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    return cat, write_home(home_dir)


def test_check_validates_a_home_and_summarises_it(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "check", str(home), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "6 devices" in out and "2 doors" in out and "4 rooms" in out and "test/house" in out


def test_check_names_the_mistake(tmp_path, capsys):
    cat, home = setup(tmp_path)
    home.write_text(textwrap.dedent(HOME).replace("{room: hall, mount: ceiling}", "{room: hal, mount: ceiling}"))
    assert cli.main(["lab", "check", str(home), "--catalog", str(cat)]) == 1
    assert "FAILED: devices[2].at.room: unknown room 'hal'" in capsys.readouterr().err


def test_run_executes_a_script_and_writes_its_trace(tmp_path, capsys):
    cat, home = setup(tmp_path)
    script = tmp_path / "walk.py"
    script.write_text(textwrap.dedent(SCRIPT))
    trace = tmp_path / "t.jsonl"
    assert cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat),
                     "--trace", str(trace)]) == 0
    lines = [json.loads(x) for x in trace.read_text().splitlines()]
    assert lines[0]["kind"] == "meta" and any(r.get("result") == "opened" for r in lines if r["kind"] == "rule")
    assert "reports" in capsys.readouterr().out


def test_run_without_a_run_function_is_refused(tmp_path, capsys):
    cat, home = setup(tmp_path)
    script = tmp_path / "empty.py"
    script.write_text("x = 1\n")
    assert cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat)]) == 1
    assert "defines no run(lab)" in capsys.readouterr().err


def test_replay_confirms_a_trace_and_reports_a_tampered_one(tmp_path, capsys):
    cat, home = setup(tmp_path)
    script = tmp_path / "walk.py"
    script.write_text(textwrap.dedent(SCRIPT))
    trace = tmp_path / "t.jsonl"
    cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat), "--trace", str(trace)])
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 0
    assert "identical" in capsys.readouterr().out
    lines = trace.read_text().splitlines()
    k = next(i for i, x in enumerate(lines) if json.loads(x)["kind"] == "report")
    rec = json.loads(lines[k])
    rec["observed_t"] += 1.0
    lines[k] = json.dumps(rec)
    trace.write_text("\n".join(lines) + "\n")
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 1
    assert "report 0" in capsys.readouterr().out


def test_new_creates_a_home_from_a_map_that_check_accepts(tmp_path, capsys):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    house().save(tmp_path / "scene.map.json")
    assert cli.main(["lab", "new", str(tmp_path / "myhome"), "--map", str(tmp_path / "scene.map.json")]) == 0
    spec = yaml.safe_load((tmp_path / "myhome" / "home.yaml").read_text())
    assert spec["version"] == 2 and spec["scene"] == "test/house" and spec["avatar"]["start"]["room"] == "hall"
    assert (tmp_path / "myhome" / "map.json").exists()
    assert cli.main(["lab", "check", str(tmp_path / "myhome" / "home.yaml"), "--catalog", str(cat)]) == 0
    assert cli.main(["lab", "new", str(tmp_path / "myhome"), "--map", str(tmp_path / "scene.map.json")]) == 1
    assert "already exists" in capsys.readouterr().err
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_cli_lab.py -q 2>&1 | tail -1`

Expected: `6 failed`; each with `argparse.ArgumentError: argument command: invalid choice: 'lab'`

- [ ] **Step 3: Implement**

In `vesper/cli.py`, replace:
```python

def main(argv: list[str] | None = None) -> int:
```
with:
```python

HOME_TEMPLATE = """\
# A VESPER Lab home (docs/superpowers/specs/2026-10-24-vesper-lab-design.md §4).
# Place real products from the catalog (`vesper catalog search`), then check it: vesper lab check home.yaml
version: 2
scene: {scene}
map: map.json
seed: 0
start_time: "07:00"
avatar: {{model: female_0, start: {{room: {room}}}}}
doors: []
devices: []
# devices:
#   - {{product: "zigbee:aqara:mccgq11lm", name: front_door, at: {{door: {doorway}}}}}
#   - {{product: "zigbee:philips:9290012607", name: motion, at: {{room: {room}, mount: ceiling}}}}
#
# Rooms: {rooms}
# Doorways: {doorways}
"""


def _lab(args) -> int:
    from vesper.lab.home import HomeError, load_lab_home
    from vesper.lab.worldmap import MapError, WorldMap
    from vesper.twins.home import load_catalog
    try:
        if args.action == "new":
            return _lab_new(args, WorldMap)
        catalog = load_catalog(Path(args.catalog))
        if args.action == "check":
            home = load_lab_home(Path(args.home), catalog)
            print(f"{home.scene}: {len(home.map.rooms)} rooms, {len(home.map.doorways)} doorways, "
                  f"{len(home.doors)} doors, {len(home.devices)} devices; the player starts in "
                  f"{home.map.room_at(*home.avatar['start']).id}")
            return 0
        if args.action == "run":
            return _lab_run(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
    except (HomeError, MapError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _lab_new(args, world_map_cls) -> int:
    target = Path(args.dir)
    if target.exists():
        print(f"FAILED: {target} already exists", file=sys.stderr)
        return 1
    world_map = world_map_cls.load(Path(args.map))
    indoor = [r for r in world_map.rooms if not r.outdoor] or world_map.rooms
    start = next((r for word in ("entry", "hall", "living") for r in indoor if word in r.label.lower()), indoor[0])
    target.mkdir(parents=True)
    world_map.save(target / "map.json")
    (target / "home.yaml").write_text(HOME_TEMPLATE.format(
        scene=world_map.scene, room=start.id, rooms=", ".join(r.id for r in world_map.rooms),
        doorway=(world_map.doorways[0].id if world_map.doorways else "<doorway>"),
        doorways=", ".join(d.id for d in world_map.doorways) or "none"))
    print(f"created {target / 'home.yaml'} on {world_map.scene}; add devices, then `vesper lab check`")
    return 0


def _lab_run(args, home) -> int:
    import runpy

    from vesper.lab.session import Session
    namespace = runpy.run_path(str(args.script))
    if not callable(namespace.get("run")):
        print(f"FAILED: {args.script} defines no run(lab)", file=sys.stderr)
        return 1
    trace = Path(args.trace) if args.trace else home.path.parent / "traces" / \
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    session = Session(home, trace=trace)
    session.start()
    try:
        namespace["run"](session)
    finally:
        session.stop()
    kinds = [r["kind"] for r in session.records]
    print(f"{kinds.count('input')} inputs, {kinds.count('rule')} rule firings, {kinds.count('report')} reports; "
          f"trace {trace}")
    return 0


def _lab_replay(args, catalog, load_lab_home) -> int:
    from vesper.lab.session import compare_reports, read_trace, replay
    records = read_trace(Path(args.trace))
    home = load_lab_home(Path(args.home or records[0]["home"]), catalog)
    diff = compare_reports(records, replay(records, home))
    if diff:
        print(f"different: {diff}")
        return 1
    print(f"identical: {sum(r['kind'] == 'report' for r in records)} reports reproduced")
    return 0


def main(argv: list[str] | None = None) -> int:
```

In `vesper/cli.py`, replace:
```python
    mat.add_argument("--out")
    args = parser.parse_args(argv)
```
with:
```python
    mat.add_argument("--out")
    lab = commands.add_parser("lab", help="VESPER Lab: a game-like virtual smart home")
    lab_actions = lab.add_subparsers(dest="action", required=True)
    lab_new = lab_actions.add_parser("new", help="create a home directory from a scene's map file")
    lab_new.add_argument("dir")
    lab_new.add_argument("--map", required=True, help="a vesper-map/1 file")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
    lab_check.add_argument("home")
    lab_check.add_argument("--catalog", default="catalog/vds")
    lab_run = lab_actions.add_parser("run", help="run a scripted session (logical clock) and write its trace")
    lab_run.add_argument("script", help="a Python file defining run(lab)")
    lab_run.add_argument("--home", required=True)
    lab_run.add_argument("--catalog", default="catalog/vds")
    lab_run.add_argument("--trace")
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
    lab_replay.add_argument("trace")
    lab_replay.add_argument("--home", help="the home file (default: the one the trace names)")
    lab_replay.add_argument("--catalog", default="catalog/vds")
    args = parser.parse_args(argv)
```

In `vesper/cli.py`, replace:
```python
    args = parser.parse_args(argv)
    if args.command in ("setup", "twins", "validate"):
        _install_sigterm()
```
with:
```python
    args = parser.parse_args(argv)
    if args.command in ("setup", "twins", "validate", "lab"):
        _install_sigterm()
```

In `vesper/cli.py`, replace:
```python
        return _twins_run(args)
    if args.action == "discovery":
```
with:
```python
        return _twins_run(args)
    if args.command == "lab":
        return _lab(args)
    if args.action == "discovery":
```

- [ ] **Step 4: Run them, then the whole suite**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `49 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `527 passed, 12 skipped` in the repository's working copy (an export without the local research data reports `526 passed, 13 skipped`: one `test_repo_layout` test skips there)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py tests/lab/test_cli_lab.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): vesper lab new, check, run and replay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## After this plan

- **W2b:** `vesper lab play`: the browser UI (map canvas, Home Assistant's dashboard, console, the 3D stream once W1 exists) and the session wired to VESPER's broker, Home Assistant and the Matter tier on the wall clock.
- **W3:** residents (routines, reactions, profiles).
- **W4:** the security lab.
- **W1:** the Habitat world service: HSSD maps from the navmesh (`vesper lab new --scene`), the humanoid avatars, the 3D view. Its spikes wait for the end of another project's run.
