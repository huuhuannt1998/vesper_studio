# Plan W1a: a Lab home from a real HSSD scene, through the world service — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vesper lab scenes` lists the HSSD scenes on this machine and whether each is downloaded. `vesper lab new myhome --scene 102344022` makes a Lab home from a real scene in about 10 s: its walkable floor, its rooms, its doorways and its doors. The Lab's rules, residents and page then work on a real home as they do on the test house.

**Architecture:**
1. **The world service starts here** (`vesper_world/`, spec §3). It is VESPER's code that runs in the Habitat environment (Python 3.9, habitat-sim 0.3.3). The app never imports it; it runs it as `python -s -m vesper_world export --hssd DIR --scene ID --out DIR`.
   - The export loads the articulated scene and takes the door openings out: VESPER opens and closes its doors itself.
   - It recomputes the navmesh with the furniture in it, samples it in 10 cm cells at the main floor's height, and keeps the largest connected part.
   - It writes the map file (`vesper-map/1`), with HSSD's region annotations as rooms, and the openings' positions (`vesper-openings/1`).
   - Pure functions (`export.py`) are unit-tested under VESPER's Python; only `habitat_scene.py` touches Habitat, and imports it lazily.
2. **The app side** (`vesper/lab/scenes.py`, `vesper/cli.py`):
   - A scene is ready when every file its instance needs is here: the stage, each object's config and render asset, each articulated object's URDF. HSSD is a gated download, often taken scene by scene.
   - `vesper lab new --scene` checks that, runs the export, and turns each opening within 0.5 m of a doorway's centre into that doorway's door, closed. The other openings are windows.
   - The player starts in an entryway or living room with floor in it, else the largest indoor room.
   - The existing `--map` path shares the template and the start-room rule.
3. **An opt-in live test** (`VESPER_HABITAT=1`) runs the real export of scene 102344022 and walks the player across its courtyard. The README gains the three commands.

**Tech Stack:** Python ≥3.11 (the app); Python 3.9 with habitat-sim 0.3.3 and numpy (the world service, in its own conda environment); the HSSD dataset (CC BY-NC 4.0, gated).

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md` (approved by the PI). §2 has `vesper lab scenes` and `vesper lab new <dir> --scene <id>`. §3 has the world service in the Habitat environment over a process boundary, and the map file generated once per scene and cached in the home directory, never distributed. §4 is the home file. §9 R1–R4 are the spikes, done 2026-10-26. The PI's decision is the demo with "Map + 3D view", and this plan is W1's first slice.

## Global Constraints

- The world service never touches the twins, the hub or the network, and the app never imports Habitat or `vesper_world`; they meet over a process boundary.
- `vesper_world` stays Python 3.9 code; a test parses it with `feature_version=(3, 9)`.
- Map files made from HSSD stay on the user's machine; nothing derived from HSSD enters the repository.
- Nothing fails silently: a scene that is not here, one not fully downloaded (naming files), a missing Habitat Python, and an export failure are each named. A failed `lab new` leaves no directory behind.
- Shared Mac: unit tests at background priority. The live export is one core and about 0.5 GB for about 10 s.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`. Never `git add -A`.

## Review Focus

1. **A scene whose floor is not one piece** (a courtyard house: the bedroom wing is reached through outdoor doors). The export keeps the largest connected part, and the Lab's door routing finds the doors to open. Pinned by the live test (`doors_on_route` names `dining_room-outdoor`).
2. **Openings that are windows**, not doors, must not become doors: only those within 0.5 m of a doorway's centre count. Pinned by `test_door_openings_on_a_doorway_become_its_doors_and_windows_do_not` and the live test (4 doors of 12 openings).
3. **A start room with no floor in it** (HSSD has closets with no walkable cells) must not be chosen. Pinned by `test_the_player_starts_in_the_entryway_or_living_room_with_floor_else_the_largest_room`.
4. **A failed export** (a crash, a missing Python, a missing scene) leaves no half-made home. Pinned by `test_a_home_that_cannot_be_made_says_why_and_leaves_nothing`.
5. **The world service's code under Python 3.9.** The unit tests run under 3.11, so a 3.10+ construct would pass them and fail in Habitat. Pinned by `test_the_world_service_is_python_3_9_code` and by the live test, which runs it in Habitat.

## Rulings carried into this plan

- **The export takes all openings out**, doors and windows, before the navmesh. Doors become the Lab's doors, which the map world blocks and unblocks. Windows sit above the floor and change nothing walkable. Cost if wrong: none for the floor; a door-height window could read as a door, and the 0.5 m doorway rule keeps it out.
- **A home's doors start closed**, since HSSD's door objects are in place. Residents open doors on their way (W3a), and the player opens them with a click. Cost if wrong: a fresh home needs a door opened to cross a courtyard, which is how the scene is built.
- **The map world stays the authority for movement**, and the world service only produces facts (here the floor and the openings). W1b renders the world from the session's poses rather than owning them, so logical-clock sessions, replay and every rule stay as they are. The spec's §3 says the world service owns the avatar's pose, so W1b will update the spec. Cost if wrong: W1b would have to move movement into Habitat and give up replay on the logical clock.
- **Readiness checks files, not checksums.** A scene with every file present but one corrupt fails at the export with Habitat's message, which is named.

---
### Task 1: The world service's export: an HSSD scene's floor, rooms and openings

**Files:**
- Create: `vesper_world/__init__.py`, `vesper_world/export.py`, `vesper_world/habitat_scene.py`, `vesper_world/__main__.py`
- Create tests: `tests/world/__init__.py` (empty), `tests/world/test_export.py`
- Modify: `pyproject.toml` (the wheel packages `vesper_world` too)

**Interfaces:**
- Consumes: the map file's format, which `WorldMap.load` reads (`vesper/lab/worldmap.py`, W2a).
- Produces:
  - in `vesper_world/export.py`: `MAP_FORMAT`, `OPENINGS_FORMAT`, `CELL_M` (0.1), `OUTDOOR_LABELS`, `ExportError`;
  - also `is_opening(handle)`, `rasterize(navigable, bounds, floor_y, cell)` returning `(origin, width, height, cells)`, `largest_part(cells, width, height)`, `room_id(name)`, `rooms_from_regions(regions)`, `encode(cells)`, `scene_map(scene)` and `scene_openings(scene)`;
  - in `vesper_world/habitat_scene.py`: `Scene(scene, floor_y, bounds, regions, openings)` with `navigable(x, y, z)`, and `load_scene(hssd, scene, radius=0.2)`;
  - in `vesper_world/__main__.py`: `main(argv)` with the `export` action (`--hssd`, `--scene`, `--out`, `--radius`). It writes `map.json` and `openings.json`, exits 1 with `FAILED: …` on an `ExportError`, and 2 on a bad command line.

- [ ] **Step 1: Write the failing tests**

Also create the empty file `tests/world/__init__.py`.

Create `tests/world/test_export.py`:
```python
"""The world service's map export (spec §3, plan W1a): an HSSD scene's navmesh and regions become a vesper-map/1
file and the scene's door openings. The pure parts run here, under VESPER's Python; the package itself must stay
runnable in the Habitat environment (Python 3.9)."""
import ast
import json
from pathlib import Path

import pytest

from vesper.lab.worldmap import WorldMap
from vesper_world import export
from vesper_world.__main__ import main

PACKAGE = Path(export.__file__).parent


def test_the_world_service_is_python_3_9_code():
    for f in sorted(PACKAGE.glob("*.py")):
        ast.parse(f.read_text(), filename=str(f), feature_version=(3, 9))  # no match, no 3.10+ syntax
        assert "from __future__ import annotations" in f.read_text(), f"{f.name}: X | Y annotations need it on 3.9"


def floor(x0, z0, x1, z1):
    """navigable(x, y, z) for a flat floor over [x0, x1] x [z0, z1] at height 0."""
    return lambda x, y, z: x0 <= x <= x1 and z0 <= z <= z1 and abs(y) < 0.5


def test_the_navmesh_is_rasterised_in_10_cm_cells_from_its_bounds():
    origin, width, height, cells = export.rasterize(floor(0.0, 0.0, 2.0, 1.0), ((0.0, 0.0, 0.0), (2.0, 1.0, 1.0)),
                                                    floor_y=0.0)
    assert origin == (0.0, 0.0) and (width, height) == (20, 10) and sum(cells) == 200


def test_only_the_largest_connected_floor_is_kept():
    width, height = 6, 3
    cells = [1, 1, 0, 0, 0, 1,
             1, 1, 0, 0, 0, 0,
             1, 1, 0, 1, 0, 0]  # a 2 x 3 floor, and two specks
    assert export.largest_part([bool(c) for c in cells], width, height) == [
        True, True, False, False, False, False,
        True, True, False, False, False, False,
        True, True, False, False, False, False]


def test_regions_become_rooms_with_stable_ids_and_outdoors_marked():
    regions = [
        {"name": "living room", "label": "living room", "poly_loop": [[0, 0, 0], [4, 0, 0], [4, 0, 3], [0, 0, 3]]},
        {"name": "closet.001", "label": "closet", "poly_loop": [[4, 0, 0], [5, 0, 0], [5, 0, 1]]},
        {"name": "outdoor", "label": "outdoor", "poly_loop": [[-3, 0, 0], [0, 0, 0], [0, 0, 3]]},
        {"name": "porch", "label": "porch/terrace/deck/driveway", "poly_loop": [[0, 0, 3], [1, 0, 3], [1, 0, 4]]},
        {"name": "living-room", "label": "living room", "poly_loop": [[9, 0, 0], [10, 0, 0], [10, 0, 1]]},
    ]
    rooms = export.rooms_from_regions(regions)
    assert [r["id"] for r in rooms] == ["living_room", "closet_001", "outdoor", "porch", "living_room_2"]
    assert rooms[0]["polygon"] == [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]  # (x, z): y is up
    assert [r["outdoor"] for r in rooms] == [False, False, True, True, False]


def test_a_door_opening_is_an_object_named_by_its_number_and_part():
    assert export.is_opening("211-2_:0001") and export.is_opening("4134-0_:0000")
    assert not export.is_opening("0a1b2c3d4e_:0000") and not export.is_opening("chair_:0000")


class FakeScene:
    scene = "102344022"
    floor_y = 0.0
    bounds = ((0.0, 0.0, 0.0), (3.0, 1.0, 2.0))
    regions = [{"name": "kitchen", "label": "kitchen", "poly_loop": [[0, 0, 0], [1.4, 0, 0], [1.4, 0, 2], [0, 0, 2]]},
               {"name": "hallway", "label": "hallway", "poly_loop": [[1.6, 0, 0], [3, 0, 0], [3, 0, 2], [1.6, 0, 2]]}]
    openings = [{"handle": "211-2_:0000", "x": 1.5, "y": 1.1, "z": 1.0}]

    @staticmethod
    def navigable(x, y, z):  # two rooms, and a doorway at z 0.6..1.4 through the wall at x 1.4..1.6
        return 0 <= x <= 3 and 0 <= z <= 2 and (not 1.4 <= x <= 1.6 or 0.6 <= z <= 1.4)


def test_export_writes_a_map_vesper_loads_and_the_openings(tmp_path, monkeypatch):
    import vesper_world.__main__ as cli
    monkeypatch.setattr(cli, "load_scene", lambda hssd, scene, radius: FakeScene)
    assert main(["export", "--hssd", str(tmp_path), "--scene", "102344022", "--out", str(tmp_path / "out")]) == 0
    m = WorldMap.load(tmp_path / "out" / "map.json")
    assert m.scene == "hssd/102344022" and [r.id for r in m.rooms] == ["kitchen", "hallway"]
    assert [d.id for d in m.doorways] == ["hallway-kitchen"]
    openings = json.loads((tmp_path / "out" / "openings.json").read_text())
    assert openings == {"format": "vesper-openings/1", "scene": "hssd/102344022", "openings": FakeScene.openings}


def test_export_names_a_scene_it_cannot_load(tmp_path, monkeypatch, capsys):
    import vesper_world.__main__ as cli

    def missing(hssd, scene, radius):
        raise export.ExportError(f"scene {scene!r} is not in {hssd} (vesper lab scenes lists those that are)")
    monkeypatch.setattr(cli, "load_scene", missing)
    assert main(["export", "--hssd", str(tmp_path), "--scene", "999", "--out", str(tmp_path / "out")]) == 1
    assert "scene '999' is not in" in capsys.readouterr().err
    assert not (tmp_path / "out" / "map.json").exists()


@pytest.mark.parametrize("bad", [[], ["export"], ["export", "--scene", "1"], ["fly"]])
def test_a_bad_command_line_is_named(bad, capsys):
    with pytest.raises(SystemExit) as e:
        main(bad)
    assert e.value.code == 2
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/world -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper_world'`

- [ ] **Step 3: Implement**

Create `vesper_world/__init__.py`:
```python
"""VESPER's world service (spec §3): the part of VESPER that runs in the Habitat environment (Python 3.9,
habitat-sim 0.3.3). It never touches the twins, the hub or the network, and the app never imports it: the app
runs it as `python -m vesper_world ...` from the Habitat environment.

    python -m vesper_world export --hssd DIR --scene ID --out DIR    # the scene's map file and door openings
"""
from __future__ import annotations
```

Create `vesper_world/export.py`:
```python
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
```

Create `vesper_world/habitat_scene.py`:
```python
"""An HSSD scene loaded in Habitat, reduced to what the export needs (spec §3, plan W1a). Habitat is imported only
here, and only when a scene is loaded, so the rest of the package runs without it."""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from vesper_world.export import ExportError, is_opening

AGENT_HEIGHT_M = 1.6
AGENT_RADIUS_M = 0.2  # the walkable floor keeps this far from walls and furniture
SNAP_M = 0.5  # how far above or below the floor's height a navmesh point may be and still count


@dataclass
class Scene:
    scene: str
    floor_y: float
    bounds: tuple
    regions: list
    openings: list = field(default_factory=list)
    _pathfinder: object = None

    def navigable(self, x: float, y: float, z: float) -> bool:
        import numpy as np
        return bool(self._pathfinder.is_navigable(np.array([x, y, z], dtype=np.float32), SNAP_M))


def load_scene(hssd: str, scene: str, radius: float = AGENT_RADIUS_M) -> Scene:
    root = Path(hssd)
    config = root / "hssd-hab-articulated.scene_dataset_config.json"
    semantics = root / "semantics" / "scenes" / f"{scene}.semantic_config.json"
    if not config.is_file():
        raise ExportError(f"{root} is not an HSSD dataset (no {config.name})")
    if not (root / "scenes-articulated" / f"{scene}.scene_instance.json").is_file():
        raise ExportError(f"scene {scene!r} is not in {root} (vesper lab scenes lists those that are)")
    if not semantics.is_file():
        raise ExportError(f"scene {scene!r} has no region annotations ({semantics.name}): its rooms are unknown")
    regions = json.loads(semantics.read_text()).get("region_annotations") or []
    if not regions:
        raise ExportError(f"scene {scene!r}: its region annotations are empty")
    import habitat_sim as hs
    cfg = hs.SimulatorConfiguration()
    cfg.scene_dataset_config_file = str(config)
    cfg.scene_id = scene
    cfg.enable_physics = True
    cfg.create_renderer = False
    agent = hs.agent.AgentConfiguration()
    agent.sensor_specifications = []
    sim = hs.Simulator(hs.Configuration(cfg, [agent]))
    try:
        objects = sim.get_rigid_object_manager()
        openings = []
        for handle in [h for h in objects.get_object_handles() if is_opening(h)]:
            t = objects.get_object_by_handle(handle).translation
            openings.append({"handle": handle, "x": round(float(t[0]), 4), "y": round(float(t[1]), 4),
                             "z": round(float(t[2]), 4)})
            objects.remove_object_by_handle(handle)
        settings = hs.NavMeshSettings()
        settings.set_defaults()
        settings.agent_radius, settings.agent_height = radius, AGENT_HEIGHT_M
        settings.include_static_objects = True
        if not sim.recompute_navmesh(sim.pathfinder, settings):
            raise ExportError(f"scene {scene!r}: Habitat could not build its navmesh")
        lo, hi = sim.pathfinder.get_bounds()
        bounds = ((float(lo[0]), float(lo[1]), float(lo[2])), (float(hi[0]), float(hi[1]), float(hi[2])))
        floor_y = statistics.median(float(r.get("floor_height", 0.0)) for r in regions)
        loaded = Scene(scene, floor_y, bounds, regions, openings)
        loaded._pathfinder = sim.pathfinder
        loaded._sim = sim  # kept alive while the pathfinder is used
        return loaded
    except BaseException:
        sim.close()
        raise
```

Create `vesper_world/__main__.py`:
```python
"""`python -m vesper_world export --hssd DIR --scene ID --out DIR`: the scene's map file and door openings."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vesper_world.export import ExportError, scene_map, scene_openings


def load_scene(hssd: str, scene: str, radius: float):
    from vesper_world.habitat_scene import load_scene as load
    return load(hssd, scene, radius)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vesper_world", description="VESPER's world service")
    actions = ap.add_subparsers(dest="action", required=True)
    ex = actions.add_parser("export", help="an HSSD scene's map file (map.json) and door openings (openings.json)")
    ex.add_argument("--hssd", required=True, help="the HSSD dataset directory (hssd-hab)")
    ex.add_argument("--scene", required=True, help="the scene id, such as 102344022")
    ex.add_argument("--out", required=True, help="the directory to write map.json and openings.json to")
    ex.add_argument("--radius", type=float, default=0.2, help="the walker's radius in metres (default 0.2)")
    args = ap.parse_args(argv)
    try:
        scene = load_scene(args.hssd, args.scene, args.radius)
        world_map, openings = scene_map(scene), scene_openings(scene)
    except ExportError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "map.json").write_text(json.dumps(world_map))
    (out / "openings.json").write_text(json.dumps(openings, indent=1))
    print(f"{args.scene}: {world_map['grid']['width']} x {world_map['grid']['height']} cells, "
          f"{len(world_map['rooms'])} rooms, {len(openings['openings'])} openings -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `pyproject.toml`, replace:
```toml
[tool.hatch.build.targets.wheel]
packages = ["vesper"]
```
with:
```toml
[tool.hatch.build.targets.wheel]
packages = ["vesper", "vesper_world"]
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/world -q 2>&1 | tail -1`

Expected: `11 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper_world/__init__.py vesper_world/export.py vesper_world/habitat_scene.py vesper_world/__main__.py pyproject.toml tests/world/__init__.py tests/world/test_export.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(world): the world service's export: an HSSD scene's walkable floor, rooms and door openings, in the Habitat environment

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: `vesper lab scenes` and `vesper lab new --scene`

**Files:**
- Create: `vesper/lab/scenes.py`
- Modify: `vesper/cli.py` (`lab new` takes `--scene` or `--map`, with `--hssd` and `--habitat-python`; `lab scenes`; `_lab_scenes`; the home template's doors; `SceneError` is caught)
- Create tests: `tests/lab/test_scenes.py`

**Interfaces:**
- Consumes: Task 1's `python -m vesper_world export` (as a process) and its files; `WorldMap.load`, `WorldMap.doorways`, `WorldMap.labels` (W2a); `distance` (`vesper/lab/geometry.py`).
- Produces:
  - in `vesper/lab/scenes.py`: `SceneError`, `SceneInfo(id, rooms, labels, indoor_m2, missing)` with `.ready`, `hssd_dir(given)`, `habitat_python(given)`, `list_scenes(hssd)` (the ready ones first), `scene_info(hssd, scene)`, `export_scene(hssd, scene, out, python, run=None)`, `doors_from_openings(world_map, openings)`, `start_room(world_map)`;
  - the CLI `vesper lab scenes [--hssd DIR]` prints `<id>  <n> rooms  <m²> m²  ready|missing <k> files  (<labels>)`;
  - the CLI `vesper lab new DIR (--scene ID | --map FILE) [--hssd DIR] [--habitat-python PATH]` prints `created … : <r> rooms, <d> doorways, <k> doors from the scene's openings; the player starts in <room>. …`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_scenes.py`:
```python
"""HSSD scenes as Lab homes (spec §2, plan W1a): which scenes are here and ready, and a new home from one. The
world service is faked here (a stand-in for the Habitat environment's Python); tests/world covers the export and
tests/lab/test_world_export_live.py runs the real one, opt-in."""
import json
import os
import stat
import sys

import pytest
import yaml

from tests.lab.homes import catalog
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli
from vesper.lab.scenes import doors_from_openings, list_scenes, start_room


def fake_hssd(root, scene="102344022", missing_object=False):
    """A tiny HSSD tree: one scene with a stage, one object, one articulated object and two regions."""
    (root / "scenes-articulated").mkdir(parents=True, exist_ok=True)
    (root / "semantics" / "scenes").mkdir(parents=True, exist_ok=True)
    (root / "stages").mkdir(exist_ok=True)
    (root / "objects" / "a").mkdir(parents=True, exist_ok=True)
    (root / "urdf" / "ao1").mkdir(parents=True, exist_ok=True)
    (root / "hssd-hab-articulated.scene_dataset_config.json").write_text("{}")
    obj = f"a{scene}"
    (root / "scenes-articulated" / f"{scene}.scene_instance.json").write_text(json.dumps({
        "stage_instance": {"template_name": f"stages/{scene}"},
        "object_instances": [{"template_name": obj}],
        "articulated_object_instances": [{"template_name": "ao1"}]}))
    kitchen, yard = [[0, 0, 0], [4, 0, 0], [4, 0, 3], [0, 0, 3]], [[4, 0, 0], [6, 0, 0], [6, 0, 3], [4, 0, 3]]
    (root / "semantics" / "scenes" / f"{scene}.semantic_config.json").write_text(json.dumps({"region_annotations": [
        {"name": "kitchen", "label": "kitchen", "floor_height": 0.0, "poly_loop": kitchen},
        {"name": "yard", "label": "outdoor", "floor_height": 0.0, "poly_loop": yard},
    ]}))
    (root / "stages" / f"{scene}.stage_config.json").write_text(json.dumps({"render_asset": f"{scene}.glb"}))
    (root / "stages" / f"{scene}.glb").write_bytes(b"glb")
    (root / "objects" / "a" / f"{obj}.object_config.json").write_text(json.dumps({"render_asset": f"{obj}.glb"}))
    if not missing_object:
        (root / "objects" / "a" / f"{obj}.glb").write_bytes(b"glb")
    (root / "urdf" / "ao1" / "ao1.urdf").write_text("<robot/>")
    return root


def test_the_scenes_here_are_listed_with_their_rooms_area_and_whether_they_are_ready(tmp_path):
    hssd = fake_hssd(tmp_path / "hssd")
    fake_hssd(hssd, scene="102343992", missing_object=True)
    scenes = {s.id: s for s in list_scenes(hssd)}
    ready, partial = scenes["102344022"], scenes["102343992"]
    assert (ready.rooms, ready.labels, ready.indoor_m2, ready.missing) == (2, ["kitchen", "outdoor"], 12.0, [])
    assert partial.missing == ["objects/a/a102343992.glb"]
    assert [s.id for s in list_scenes(hssd)] == ["102344022", "102343992"]  # the ready ones first


def test_door_openings_on_a_doorway_become_its_doors_and_windows_do_not():
    m = house()  # hall-porch at (0.0, 3.0): the front door
    openings = [{"handle": "211-2_:0000", "x": 0.05, "y": 1.1, "z": 3.1},
                {"handle": "211-2_:0001", "x": -0.1, "y": 1.1, "z": 2.9},  # the same doorway again
                {"handle": "218-20_:0000", "x": 7.0, "y": 1.45, "z": 1.5}]  # a window in a wall
    assert doors_from_openings(m, openings) == ["hall-porch"]


def test_the_player_starts_in_the_entryway_or_living_room_with_floor_else_the_largest_room():
    assert start_room(house()) == "hall"
    from vesper.lab.worldmap import Room, WorldMap
    m = house()
    rooms = [Room("living_room", "living room", ((50, 50), (51, 50), (51, 51))),  # no floor in it
             *m.rooms]
    assert start_room(WorldMap(m.scene, rooms, m.grid)) == "hall"


FAKE_WORLD = """#!{python}
import json, sys
from pathlib import Path
sys.path.insert(0, {root!r})
from tests.lab.maps import house
args = sys.argv[1:]
assert args[:4] == ["-s", "-m", "vesper_world", "export"], args
out = Path(args[args.index("--out") + 1])
if {fail!r}:
    print("FAILED: scene '102344022': Habitat could not build its navmesh", file=sys.stderr)
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
house().save(out / "map.json")
(out / "openings.json").write_text(json.dumps({{"format": "vesper-openings/1", "scene": "hssd/102344022",
    "openings": [{{"handle": "211-2_:0000", "x": 0.0, "y": 1.1, "z": 3.0}}]}}))
"""


def fake_python(tmp_path, fail=False):
    path = tmp_path / ("python-fail" if fail else "python")
    path.write_text(FAKE_WORLD.format(python=sys.executable, root=os.getcwd(), fail=fail))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_new_home_from_an_hssd_scene_has_the_scene_s_doors_and_passes_check(tmp_path, capsys):
    hssd = fake_hssd(tmp_path / "hssd")
    home = tmp_path / "myhome"
    assert cli.main(["lab", "new", str(home), "--scene", "102344022", "--hssd", str(hssd),
                     "--habitat-python", str(fake_python(tmp_path))]) == 0
    spec = yaml.safe_load((home / "home.yaml").read_text())
    assert spec["doors"] == [{"doorway": "hall-porch", "open": False}] and spec["avatar"]["start"]["room"] == "hall"
    assert (home / "map.json").exists() and (home / "openings.json").exists()
    out = capsys.readouterr().out
    assert "1 door from the scene's openings" in out and "the player starts in hall" in out
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    assert cli.main(["lab", "check", str(home / "home.yaml"), "--catalog", str(cat)]) == 0


@pytest.mark.parametrize("case, words", [
    ("missing", "scene 102344022 needs 1 file that is not in"),
    ("python", "the Habitat environment's Python"),
    ("fails", "Habitat could not build its navmesh"),
    ("unknown", "scene '999' is not in"),
])
def test_a_home_that_cannot_be_made_says_why_and_leaves_nothing(tmp_path, capsys, case, words):
    hssd = fake_hssd(tmp_path / "hssd", missing_object=case == "missing")
    python = tmp_path / "no-python" if case == "python" else fake_python(tmp_path, fail=case == "fails")
    scene = "999" if case == "unknown" else "102344022"
    home = tmp_path / "myhome"
    assert cli.main(["lab", "new", str(home), "--scene", scene, "--hssd", str(hssd), "--habitat-python",
                     str(python)]) == 1
    assert words in capsys.readouterr().err
    assert not home.exists()


def test_scenes_lists_what_is_here(tmp_path, capsys):
    hssd = fake_hssd(tmp_path / "hssd")
    fake_hssd(hssd, scene="102343992", missing_object=True)
    assert cli.main(["lab", "scenes", "--hssd", str(hssd)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:6] == ["102344022", "2", "rooms", "12", "m²", "ready"] and "(kitchen, outdoor)" in out[0]
    assert out[1].split()[:6] == ["102343992", "2", "rooms", "12", "m²", "missing"]
    assert cli.main(["lab", "scenes", "--hssd", str(tmp_path / "nowhere")]) == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_scenes.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.scenes'`

- [ ] **Step 3: Implement**

Create `vesper/lab/scenes.py`:
```python
"""HSSD scenes as Lab homes (spec §2, plan W1a): which scenes are here and ready, and a new home from one.

    vesper lab scenes [--hssd DIR]
    vesper lab new myhome --scene 102344022 [--hssd DIR] [--habitat-python PATH]

A scene is ready when every file its articulated scene instance needs is on this machine: HSSD is a gated
download, often taken scene by scene. A new home runs the world service in the Habitat environment
(`python -m vesper_world export`), which writes the scene's map file and its door openings. The openings that
stand in a doorway become the home's doors, closed; the others are windows. The player starts in the entryway or
the living room if the scene has one with floor in it, else in the largest indoor room.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import vesper
from vesper.lab.geometry import distance
from vesper.lab.worldmap import WorldMap

HSSD_ENV, PYTHON_ENV = "VESPER_HSSD", "VESPER_HABITAT_PYTHON"
DEFAULT_HSSD = Path("data/scene_datasets/hssd-hab")  # relative to the working directory, as in the repository
DEFAULT_PYTHON = Path.home() / "miniconda3" / "envs" / "vesper" / "bin" / "python"
WORLD_ROOT = Path(vesper.__file__).resolve().parent.parent  # where the `vesper_world` package lives
DOOR_SNAP_M = 0.5  # an opening this close to a doorway's centre is that doorway's door
START_LABELS = ("entryway", "foyer", "lobby", "living room", "familyroom", "lounge", "hall")
EXPORT_TIMEOUT_S = 600


class SceneError(RuntimeError):
    """A scene VESPER cannot make a home from; the message says why and what to do."""


@dataclass
class SceneInfo:
    id: str
    rooms: int
    labels: list[str]
    indoor_m2: float
    missing: list[str] = field(default_factory=list)  # files the scene needs that are not here, relative to HSSD

    @property
    def ready(self) -> bool:
        return not self.missing


def hssd_dir(given: str | None) -> Path:
    return Path(given or os.environ.get(HSSD_ENV) or DEFAULT_HSSD)


def habitat_python(given: str | None) -> Path:
    return Path(given or os.environ.get(PYTHON_ENV) or DEFAULT_PYTHON)


def list_scenes(hssd: Path) -> list[SceneInfo]:
    """The scenes with an articulated instance and region annotations here, the ready ones first."""
    hssd = Path(hssd)
    instances = sorted((hssd / "scenes-articulated").glob("*.scene_instance.json"))
    if not instances:
        raise SceneError(f"{hssd} holds no HSSD scenes (scenes-articulated/*.scene_instance.json); set "
                         f"--hssd or {HSSD_ENV}")
    objects = _object_configs(hssd)
    out = []
    for path in instances:
        sid = path.name.removesuffix(".scene_instance.json")
        regions = _regions(hssd, sid)
        if regions is None:
            continue  # no region annotations: the rooms would be unknown
        indoor = sum(_area(r["poly_loop"]) for r in regions if r["label"] not in _outdoor_labels())
        out.append(SceneInfo(sid, len(regions), sorted({r["label"] for r in regions}), round(indoor, 1),
                             _missing(hssd, json.loads(path.read_text()), objects)))
    return sorted(out, key=lambda s: (not s.ready, s.id))


def scene_info(hssd: Path, scene: str) -> SceneInfo:
    for s in list_scenes(hssd):
        if s.id == scene:
            return s
    raise SceneError(f"scene {scene!r} is not in {hssd} (vesper lab scenes lists those that are)")


def export_scene(hssd: Path, scene: str, out: Path, python: Path, run=None) -> None:
    """Run the world service's export in the Habitat environment: `out` gets map.json and openings.json."""
    import subprocess
    if not Path(python).is_file():
        raise SceneError(f"the Habitat environment's Python is not at {python}; give it with --habitat-python or "
                         f"{PYTHON_ENV} (the environment with habitat-sim 0.3.3)")
    env = {**os.environ, "PYTHONPATH": str(WORLD_ROOT)}
    cmd = [str(python), "-s", "-m", "vesper_world", "export", "--hssd", str(Path(hssd).resolve()), "--scene", scene,
           "--out", str(out)]
    try:
        r = (run or subprocess.run)(cmd, env=env, capture_output=True, text=True, timeout=EXPORT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SceneError(f"the world service did not export scene {scene} within {EXPORT_TIMEOUT_S} s") from None
    if r.returncode != 0:
        lines = [ln for ln in (r.stderr or "").splitlines() if ln.startswith("FAILED") or "Error" in ln]
        raise SceneError((lines[-1].removeprefix("FAILED: ") if lines else f"the world service exited with "
                          f"{r.returncode}") + f" (scene {scene})")


def doors_from_openings(world_map: WorldMap, openings: list[dict]) -> list[str]:
    """The doorways that have a door: an opening within DOOR_SNAP_M of the doorway's centre (windows are not)."""
    doors = []
    for o in openings:
        if not world_map.doorways:
            break
        nearest = min(world_map.doorways, key=lambda d: distance(d.center, (o["x"], o["z"])))
        if distance(nearest.center, (o["x"], o["z"])) <= DOOR_SNAP_M and nearest.id not in doors:
            doors.append(nearest.id)
    return doors


def start_room(world_map: WorldMap) -> str:
    """Where the player starts: an entryway or living room with floor in it, else the largest indoor room."""
    cells: dict[int, int] = {}
    for k in world_map.labels:
        if k >= 0:
            cells[k] = cells.get(k, 0) + 1
    with_floor = [(k, r) for k, r in enumerate(world_map.rooms) if cells.get(k)]
    indoor = [(k, r) for k, r in with_floor if not r.outdoor] or with_floor
    for word in START_LABELS:
        for _, r in indoor:
            if word in r.label.lower():
                return r.id
    if not indoor:
        raise SceneError(f"{world_map.scene}: no room has walkable floor in it")
    return max(indoor, key=lambda kr: cells[kr[0]])[1].id


def _regions(hssd: Path, scene: str) -> list | None:
    path = hssd / "semantics" / "scenes" / f"{scene}.semantic_config.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text()).get("region_annotations") or None


def _outdoor_labels() -> tuple[str, ...]:
    return ("outdoor", "porch/terrace/deck/driveway", "balcony")  # vesper_world.export.OUTDOOR_LABELS


def _area(loop) -> float:
    pts = [(p[0], p[2]) for p in loop]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def _object_configs(hssd: Path) -> dict[str, Path]:
    return {p.name.removesuffix(".object_config.json"): p for p in (hssd / "objects").rglob("*.object_config.json")}


def _missing(hssd: Path, instance: dict, objects: dict[str, Path]) -> list[str]:
    """The files this scene instance needs that are not here: the stage, each object's config and render asset,
    and each articulated object's URDF."""
    missing: set[str] = set()

    def need(config: Path) -> None:
        if not config.is_file():
            missing.add(str(config.relative_to(hssd)))
            return
        asset = json.loads(config.read_text()).get("render_asset")
        if asset and not (config.parent / asset).is_file():
            missing.add(str((config.parent / asset).relative_to(hssd)))
    stage = (instance.get("stage_instance") or {}).get("template_name")
    if stage:
        need(hssd / f"{stage}.stage_config.json")
    for o in instance.get("object_instances") or []:
        name = o["template_name"].split("/")[-1]
        need(objects.get(name, hssd / "objects" / f"{name}.object_config.json"))
    for a in instance.get("articulated_object_instances") or []:
        name = a["template_name"]
        if not (hssd / "urdf" / name / f"{name}.urdf").is_file():
            missing.add(f"urdf/{name}/{name}.urdf")
    return sorted(missing)
```

In `vesper/cli.py`, replace:
```python
avatar: {{model: female_0, start: {{room: {room}}}}}
doors: []
devices: []
```
with:
```python
avatar: {{model: female_0, start: {{room: {room}}}}}
{doors}
devices: []
```

In `vesper/cli.py`, replace:
```python
    from vesper.twins.home import load_catalog
    try:
        if args.action == "new":
            return _lab_new(args, WorldMap)
        catalog = load_catalog(Path(args.catalog))
```
with:
```python
    from vesper.twins.home import load_catalog
    from vesper.lab.scenes import SceneError
    try:
        if args.action == "new":
            return _lab_new(args, WorldMap)
        if args.action == "scenes":
            return _lab_scenes(args)
        catalog = load_catalog(Path(args.catalog))
```

In `vesper/cli.py`, replace:
```python
        return _lab_replay(args, catalog, load_lab_home)
    except (HomeError, MapError, TraceError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
```
with:
```python
        return _lab_replay(args, catalog, load_lab_home)
    except (HomeError, MapError, TraceError, SceneError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _lab_scenes(args) -> int:
    """The HSSD scenes here: rooms, indoor area, and whether every file each needs is here (spec §2)."""
    from vesper.lab.scenes import hssd_dir, list_scenes
    for s in list_scenes(hssd_dir(args.hssd)):
        state = "ready" if s.ready else f"missing {len(s.missing)} file{'s' if len(s.missing) != 1 else ''}"
        print(f"{s.id}  {s.rooms} rooms  {s.indoor_m2:g} m²  {state}  ({', '.join(s.labels)})")
    return 0
```

In `vesper/cli.py`, replace:
```python
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
```
with:
```python
def _lab_new(args, world_map_cls) -> int:
    """A home directory from a map file, or from an HSSD scene through the world service (spec §2, plan W1a)."""
    import shutil

    from vesper.lab.scenes import (SceneError, doors_from_openings, export_scene, habitat_python, hssd_dir,
                                   scene_info, start_room)
    target = Path(args.dir)
    if target.exists():
        print(f"FAILED: {target} already exists", file=sys.stderr)
        return 1
    doors: list[str] = []
    if args.scene:
        hssd = hssd_dir(args.hssd)
        try:
            info = scene_info(hssd, args.scene)
            if info.missing:
                n = len(info.missing)
                raise SceneError(f"scene {args.scene} needs {n} file{'s' if n != 1 else ''} that "
                                 f"{'are' if n != 1 else 'is'} not in {hssd}, such as {', '.join(info.missing[:3])}; "
                                 "download the scene's files first")
            target.mkdir(parents=True)
            export_scene(hssd, args.scene, target, habitat_python(args.habitat_python))
        except SceneError as e:
            if target.exists():
                shutil.rmtree(target)  # made here, just now: nothing of the user's is in it
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        world_map = world_map_cls.load(target / "map.json")
        doors = doors_from_openings(world_map, json.loads((target / "openings.json").read_text())["openings"])
    else:
        world_map = world_map_cls.load(Path(args.map))
        target.mkdir(parents=True)
        world_map.save(target / "map.json")
    start = start_room(world_map)
    door_lines = "doors:" + "".join(f"\n  - {{doorway: {d}, open: false}}" for d in doors) if doors else "doors: []"
    (target / "home.yaml").write_text(HOME_TEMPLATE.format(
        scene=world_map.scene, room=start, doors=door_lines, rooms=", ".join(r.id for r in world_map.rooms),
        doorway=(world_map.doorways[0].id if world_map.doorways else "<doorway>"),
        doorways=", ".join(d.id for d in world_map.doorways) or "none"))
    found = f", {len(doors)} door{'s' if len(doors) != 1 else ''} from the scene's openings" if args.scene else ""
    print(f"created {target / 'home.yaml'} on {world_map.scene}: {len(world_map.rooms)} rooms, "
          f"{len(world_map.doorways)} doorways{found}; the player starts in {start}. "
          "Place devices (vesper catalog search), then `vesper lab check`")
    return 0
```

In `vesper/cli.py`, replace:
```python
    lab_actions = lab.add_subparsers(dest="action", required=True)
    lab_new = lab_actions.add_parser("new", help="create a home directory from a scene's map file")
    lab_new.add_argument("dir")
    lab_new.add_argument("--map", required=True, help="a vesper-map/1 file")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
```
with:
```python
    lab_actions = lab.add_subparsers(dest="action", required=True)
    lab_new = lab_actions.add_parser("new", help="create a home directory from an HSSD scene or a map file")
    lab_new.add_argument("dir")
    lab_source = lab_new.add_mutually_exclusive_group(required=True)
    lab_source.add_argument("--scene", help="an HSSD scene id (vesper lab scenes lists them)")
    lab_source.add_argument("--map", help="a vesper-map/1 file")
    lab_new.add_argument("--hssd", help="the HSSD dataset (default: $VESPER_HSSD or data/scene_datasets/hssd-hab)")
    lab_new.add_argument("--habitat-python", help="the Habitat environment's Python (default: $VESPER_HABITAT_PYTHON "
                                                  "or ~/miniconda3/envs/vesper/bin/python)")
    lab_scenes = lab_actions.add_parser("scenes", help="the HSSD scenes here, and whether each is ready")
    lab_scenes.add_argument("--hssd", help="the HSSD dataset (default: $VESPER_HSSD or data/scene_datasets/hssd-hab)")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab/test_scenes.py tests/lab/test_cli_lab.py tests/world -q 2>&1 | tail -1`

Expected: `33 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/scenes.py vesper/cli.py tests/lab/test_scenes.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): vesper lab scenes and vesper lab new --scene: a Lab home from a real HSSD scene, its doors from the scene's openings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The live export, opt-in, and the README

**Files:**
- Create: `tests/lab/test_world_export_live.py`
- Modify: `README.md` (the Habitat environment's role; the three commands)

**Interfaces:**
- Consumes: Tasks 1 and 2; the Session and the map world (W2a, W3b's door routing).

- [ ] **Step 1: Write the live test**

Create `tests/lab/test_world_export_live.py`:
```python
"""A home from a real HSSD scene, through the real world service in the Habitat environment: opt-in
(VESPER_HABITAT=1), like the Matter host contract test. Scene 102344022 must be downloaded (`vesper lab scenes`
says ready); it takes about 10 s and 0.5 GB."""
import os
from pathlib import Path

import pytest
import yaml

from vesper import cli
from vesper.lab.home import load_lab_home, spawn_point
from vesper.lab.scenes import habitat_python, hssd_dir, list_scenes
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock

SCENE = "102344022"
HSSD = hssd_dir(None)


def _ready() -> bool:
    try:
        return any(s.id == SCENE and s.ready for s in list_scenes(HSSD)) and habitat_python(None).is_file()
    except Exception:  # noqa: BLE001 - no HSSD here: the test is skipped
        return False


pytestmark = pytest.mark.skipif(os.environ.get("VESPER_HABITAT") != "1" or not _ready(),
                                reason=f"opt-in: VESPER_HABITAT=1, scene {SCENE} downloaded, the Habitat Python")


def test_a_real_scene_becomes_a_home_whose_rooms_doors_and_walks_work(tmp_path):
    home_dir = tmp_path / "home"
    assert cli.main(["lab", "new", str(home_dir), "--scene", SCENE, "--hssd", str(HSSD)]) == 0
    spec = yaml.safe_load((home_dir / "home.yaml").read_text())
    assert {d["doorway"] for d in spec["doors"]} == {"dining_room-outdoor", "bedroom-outdoor", "other_room-outdoor",
                                                     "bathroom_001-outdoor"}  # the scene's 4 doors; 8 windows
    home = load_lab_home(Path(home_dir / "home.yaml"), {})
    assert len(home.map.rooms) == 20 and len(home.map.doorways) >= 20
    assert home.map.room_at(*home.avatar["start"]).id == "living_room"
    s = Session(home, clock=LogicalClock())
    s.start()
    w, me = s.world, s.world.pose("player")
    bedroom = spawn_point(home.map, "bedroom")  # across the courtyard: through a door
    assert w.doors_on_route((me.x, me.z), bedroom) == [("dining_room-outdoor", "dining_room")]
    assert w.plan_work < sum(home.map.grid.walkable) / 10
    for door in list(w.doors):
        w.set_door(door, True)
    assert w.walk_to("player", bedroom) is not None
    s.clock.advance(120)
    assert w.pose("player").room == "bedroom"
```

- [ ] **Step 2: Run it: skipped by default, then for real**

Run: `.venv/bin/python -m pytest tests/lab/test_world_export_live.py -q 2>&1 | tail -1`

Expected: `1 skipped`

Run: `VESPER_HABITAT=1 taskpolicy -b .venv/bin/python -m pytest tests/lab/test_world_export_live.py -q 2>&1 | tail -1`

Expected: `1 passed` in about 12 s, in the repository's working copy, where scene 102344022 is downloaded (`data/scene_datasets/hssd-hab`) and the Habitat environment is at `~/miniconda3/envs/vesper`

- [ ] **Step 3: The README**

In `README.md`, replace:
```markdown

Habitat-based parts use a separate conda environment with habitat-sim 0.3.3 (Python 3.9).
```
with:
```markdown

Habitat-based parts use a separate conda environment with habitat-sim 0.3.3 (Python 3.9); VESPER runs its world
service there (`python -m vesper_world`) and finds it at `$VESPER_HABITAT_PYTHON`
(default `~/miniconda3/envs/vesper/bin/python`).

## VESPER Lab: a home from an HSSD scene

    vesper lab scenes                                   # the HSSD scenes here, and whether each is downloaded
    vesper lab new myhome --scene 102344022             # its map, its rooms and its doors, in about 10 s
    vesper lab check myhome/home.yaml                   # then place devices (vesper catalog search)

HSSD is a gated download (CC BY-NC 4.0) at `$VESPER_HSSD` (default `data/scene_datasets/hssd-hab`); the map
files made from it stay on your machine. Design: `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`.
```

- [ ] **Step 4: The whole suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `794 passed, 14 skipped` in the repository's working copy (`790 passed, 18 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add tests/lab/test_world_export_live.py README.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "test(lab): a real HSSD scene becomes a Lab home, opt-in; the README says how

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```



---

## After this plan

- **W1b, the 3D view:** the world service as a long-running process (JSON lines, as the Matter host does). It mirrors the session's poses and door states into the furnished scene, animates the humanoids, and renders third- and first-person frames, which the page shows as its main view: V switches the camera, with pointer lock, the crosshair (picking through the world service), and the minimap with M for the full map.
- **HSSD download help** (`vesper lab scenes` names what is missing; W5's `vesper setup scenes` fetches it).
- **W2d** (Matter on the hub; the placement editor, now on real homes), **W4** the security lab, **W5** packaging.
