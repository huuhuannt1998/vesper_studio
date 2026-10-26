# Plan W1b: the 3D view — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vesper lab play` opens on a 3D view of the home, played like PUBG (spec §2a): third person (V: first person), mouse look with the pointer captured, WASD where the camera looks, a crosshair whose prompt says what a click or F does, the map as a minimap (M: the full map) and a compass. A home made from an HSSD scene is drawn from the scene's own files, placed exactly as Habitat places them; its people are Habitat's own humanoids, walking with Habitat's walk.

**Architecture:**
1. **The browser draws; Habitat places** (spec §3, decided by the W1b spike of 2026-10-26). The spike rendered scene 102344022's 219 meshes with three.js from the world matrices Habitat gives them, and the image matched Habitat's own render object for object. So the world service, at `lab new`, also writes `render.json` (`vesper-render/1`): each mesh's file inside HSSD and its world matrix (an object's first visual node; an articulated part's link frame, then its URDF origin). Streaming Habitat's frames was set aside: mouse look needs the browser's frame rate, Habitat would have to run during play, and it has no Windows build.
2. **Habitat's humanoids.** Each avatar's skinned model (`humanoid_data/<name>/<name>.glb`) and its walk: the world service turns Habitat's motion pickle (54 joint quaternions, 120 frames a second, in the URDF's joint order, each joint named by its bone) into `<name>.motion.json` once, kept in VESPER's cache. The page plays the walk as far as a person moves and Habitat's standing pose when they stand.
3. **The play server serves only what the home uses:** `/world/render.json`, under `/hssd/` the files the manifest lists (each checked to be a model file inside HSSD), under `/humanoids/` the people's models and walks. A door mesh standing in one of the home's doors' doorways is that door, and swings when the session opens it.
4. **The page** keeps the session as the authority. `view3d.js` (three.js 0.170.0, vendored and pinned by SHA-256, so the page loads nothing from the internet) draws the scene, or, for a home without one, the map's floor, walls and doors; the arithmetic is in `scene3d.js`, tested under node. Without WebGL, or with `?view=map`, the page plays on the map as before.
5. **The looks** get two steps, `aim` (put the crosshair on a device or door) and `view` (map or 3d), and check that the 3D view is drawn.

**Tech Stack:** Python 3.11+ (VESPER), Python 3.9 + habitat-sim 0.3.3 (the world service), three.js 0.170.0 (vendored ES modules, an import map, no build step), node's test runner, Chromium headless for the looks.

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md` (§2, §2a, §3; this plan updates §3 and §10).

## Global Constraints

- Shared Mac (`~/Research/MAC_COORDINATION.md`, `~/Research/BUSY_WINDOWS.json`): unit tests under `taskpolicy -b`; the Habitat runs and the headless looks outside every window (2026-10-27 03:30–10:30Z is another project's), each with a ledger row.
- Nothing derived from HSSD or from Habitat's humanoids is committed: `render.json`, motions and screenshots of them stay on the machine.
- The page loads nothing from the internet: three.js is vendored unchanged from `three@0.170.0`, each file pinned by SHA-256 in `vesper/lab/ui/vendor/three/NOTICE.md`.
- The world service stays Python 3.9 code (`from __future__ import annotations` in every module), run with a copy of `vesper_world` alone on its path.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`.

## Review Focus

- **A real GPU browser with the pointer captured:** mouse look direction and speed, Esc letting go, a click while not captured (it captures, it does not use), a click while captured (it uses what the crosshair is on). The looks run headless with no pointer lock.
- **A scene that is only partly downloaded:** the page loads what is there, says how many files are missing, and plays; the server never serves a file the manifest does not list.
- **Humanoid data that does not fit:** a model without some bone, or a motion for another skeleton: the person stays in the rest or standing pose, or a plain figure; the page does not fail.
- **Two pages at once:** each loads the scene; the play server answers both (HSSD's files may be cached by the browser, the page's own are not).
- **Other browsers:** WebGL2, import maps and KTX2 textures in Safari and Firefox; without WebGL the page says so and plays on the map.

---
### Task 1: The world service: a scene's render manifest, and Habitat's humanoids' walks

**Files:**
- Modify: `vesper_world/export.py` (`scene_render`, `urdf_visuals`, `trs`, `matmul`, `flat`)
- Modify: `vesper_world/habitat_scene.py` (every mesh's world matrix, collected before the doors leave the scene)
- Create: `vesper_world/humanoids.py`
- Modify: `vesper_world/__main__.py` (`render.json`; the `humanoids` subcommand)
- Modify: `tests/world/test_export.py`; Create: `tests/world/test_humanoids.py`

**Interfaces:**
- Produces: `render.json` = {"format": "vesper-render/1", "scene": "hssd/<id>", "hssd": <absolute path>, "floor_y", "items": [{"kind": "stage"|"object"|"part", "asset": <path inside HSSD>, "matrix": <16 numbers, row by row>, "handle"?, "opening"?: "door"|"window"|"unknown"}]}; `python -m vesper_world humanoids --data DIR --out DIR` writes `<name>.motion.json` = {"format": "vesper-motion/1", "avatar", "joints": [54 bone names], "fps": 30.0, "stop": [54 x [x, y, z, w]], "walk": [frames][54][4], "cycle_m"}.

- [ ] **Step 1: Write the failing tests**

In `tests/world/test_export.py`, replace:
```python
import json
from pathlib import Path
```
with:
```python
import json
import math
from pathlib import Path
```

In `tests/world/test_export.py`, replace:
```python

class FakeScene:
    scene = "102344022"
    floor_y = 0.0
```
with:
```python

URDF = """<?xml version="1.0" ?>
<robot name="cabinet">
  <link name="root"/>
  <joint name="root_rotation" type="fixed"><parent link="root"/><child link="body"/></joint>
  <link name="body">
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="body.glb" scale="1.0 1.0 1.0"/></geometry>
    </visual>
    <collision><geometry><box size="1 1 1"/></geometry></collision>
  </link>
  <link name="door">
    <visual>
      <origin xyz="0.5 0 0" rpy="1.5707963 0 0"/><geometry><mesh filename="door.glb" scale="2 1 1"/></geometry>
    </visual>
    <visual><geometry><mesh filename="handle.glb"/></geometry></visual>
  </link>
</robot>
"""


def test_an_articulated_object_s_meshes_are_read_from_its_urdf():
    visuals = export.urdf_visuals(URDF)
    assert visuals == {"body": [("body.glb", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))],
                       "door": [("door.glb", (0.5, 0.0, 0.0), (1.5707963, 0.0, 0.0), (2.0, 1.0, 1.0)),
                                ("handle.glb", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))]}


def test_a_mesh_s_place_is_its_link_s_frame_then_its_urdf_origin():
    """URDF's roll, pitch and yaw turn about the fixed x, y and z axes; the spike matched Habitat to 1e-6."""
    m = export.trs((1, 2, 3), (math.pi / 2, 0, 0), (2, 1, 1))
    y_axis = [m[0][1], m[1][1], m[2][1]]  # where the mesh's +y goes: up turns to +z under a quarter roll
    assert [round(v, 6) for v in y_axis] == [0, 0, 1] and [m[0][3], m[1][3], m[2][3]] == [1, 2, 3]
    assert [round(m[0][0], 6), round(m[1][0], 6), round(m[2][0], 6)] == [2, 0, 0]  # the scale stays on its axis
    frame = [[0, 0, 1, 10], [0, 1, 0, 0], [-1, 0, 0, 5], [0, 0, 0, 1]]  # a link turned a quarter about y
    placed = export.matmul(frame, export.trs((1, 0, 0), (0, 0, 0), (1, 1, 1)))
    assert [placed[0][3], placed[1][3], placed[2][3]] == [10, 0, 4]
    assert export.flat(export.IDENTITY_ROWS) == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


class FakeScene:
    scene = "102344022"
    hssd = "/data/hssd-hab"
    floor_y = 0.0
```

In `tests/world/test_export.py`, replace:
```python
                {"handle": "211-2_:0001", "kind": "door", "x": 1.5, "y": 3.9, "z": 1.0}]  # upstairs
```
with:
```python
                {"handle": "211-2_:0001", "kind": "door", "x": 1.5, "y": 3.9, "z": 1.0}]  # upstairs
    render = [{"kind": "stage", "asset": "stages/102344022.glb",
               "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
              {"kind": "object", "asset": "objects/openings/211-2.glb", "handle": "211-2_:0000",
               "opening": "door", "matrix": [1, 0, 0, 1.5, 0, 1, 0, 0, 0, 0, 1, 1.0, 0, 0, 0, 1]}]
```

In `tests/world/test_export.py`, replace:
```python
                        "floor": {"height": 0.0, "storeys": [0.0, 2.8], "left_out": ["bedroom"]}}
    said = capsys.readouterr().out
```
with:
```python
                        "floor": {"height": 0.0, "storeys": [0.0, 2.8], "left_out": ["bedroom"]}}
    render = json.loads((tmp_path / "out" / "render.json").read_text())
    assert render == {"format": "vesper-render/1", "scene": "hssd/102344022", "hssd": "/data/hssd-hab",
                      "floor_y": 0.0, "items": FakeScene.render}
    said = capsys.readouterr().out
```

Create `tests/world/test_humanoids.py`:
```python
"""Habitat's humanoids for the 3D view (plan W1b): each avatar's walk and standing pose, from Habitat's motion file,
as joint rotations a browser can play. The motion files are pickles of numpy arrays, so the conversion runs in the
Habitat environment; the pure part runs here."""
import json

import pytest

from vesper_world import humanoids
from vesper_world.__main__ import main

URDF = """<robot name="female_0">
  <link name="pelvis"/><link name="left_hip"/><link name="left_knee"/>
  <joint name="left_hip" type="spherical"><parent link="pelvis"/><child link="left_hip"/>
    <origin rpy="0 0 0" xyz="0.06 -0.08 -0.02"/></joint>
  <joint name="left_knee" type="spherical"><parent link="left_hip"/><child link="left_knee"/>
    <origin rpy="0 0 0" xyz="0.02 -0.36 -0.02"/></joint>
</robot>
"""
Q, R = [0.0, 0.0, 0.0, 1.0], [0.0, 0.7071068, 0.0, 0.7071068]


def test_the_joints_are_the_urdf_s_moving_joints_in_order_named_by_their_bones():
    assert humanoids.joint_names(URDF) == ["left_hip", "left_knee"]


def test_a_motion_keeps_every_nth_frame_and_the_walk_s_length():
    walk = [[Q, R]] * 9  # 9 frames at 120 fps
    m = humanoids.motion("female_0", ["left_hip", "left_knee"], [R, Q], walk, 120.0, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5,
                                                                                         0.6, 0.7, 0.8], every=4)
    assert m == {"format": "vesper-motion/1", "avatar": "female_0", "joints": ["left_hip", "left_knee"], "fps": 30.0,
                 "stop": [R, Q], "walk": [[Q, R]] * 3, "cycle_m": 0.8}


def test_a_motion_whose_joints_do_not_match_its_skeleton_is_refused():
    with pytest.raises(humanoids.HumanoidError, match="female_0: the walk has 3 joints, the skeleton 2"):
        humanoids.motion("female_0", ["a", "b"], [Q, Q], [[Q, Q, Q]], 120.0, [0.0])


def test_humanoids_converts_every_avatar_it_finds(tmp_path, monkeypatch, capsys):
    data = tmp_path / "humanoid_data"
    for name in ("female_0", "male_0"):
        (data / name).mkdir(parents=True)
        (data / name / f"{name}.urdf").write_text(URDF)
        (data / name / f"{name}.glb").write_bytes(b"glTF")
        (data / name / f"{name}_motion_data_smplx.pkl").write_bytes(b"pkl")
    (data / "assets").mkdir()  # not an avatar
    monkeypatch.setattr(humanoids, "load_motion_file", lambda path: {
        "stop": [Q, Q], "walk": [[Q, R]] * 8, "fps": 120.0, "displacement": [0.0] * 7 + [1.25]})
    assert main(["humanoids", "--data", str(data), "--out", str(tmp_path / "out")]) == 0
    made = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert made == ["female_0.motion.json", "male_0.motion.json"]
    m = json.loads((tmp_path / "out" / "male_0.motion.json").read_text())
    assert m["joints"] == ["left_hip", "left_knee"] and len(m["walk"]) == 2 and m["cycle_m"] == 1.25
    assert "2 humanoids" in capsys.readouterr().out


def test_humanoids_names_a_folder_without_avatars(tmp_path, capsys):
    assert main(["humanoids", "--data", str(tmp_path), "--out", str(tmp_path / "out")]) == 1
    assert "no humanoids in" in capsys.readouterr().err
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/world -q 2>&1 | tail -1`

Expected: `1 error`: collection stops on `ModuleNotFoundError: No module named 'vesper_world.humanoids'`

- [ ] **Step 3: The render manifest's arithmetic**

In `vesper_world/export.py`, replace:
```python
out, and named. Doorways are not exported: VESPER derives them from the floor and the rooms.
Pure functions only: `habitat_scene.py` holds what needs Habitat.
"""
from __future__ import annotations

import base64
import csv
import re
from collections import deque
from math import ceil, floor

MAP_FORMAT = "vesper-map/1"
OPENINGS_FORMAT = "vesper-openings/1"
CELL_M = 0.1
```
with:
```python
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
```

In `vesper_world/export.py`, replace:
```python
                      "left_out": [r["name"] for r in left_out]}}
```
with:
```python
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
```

- [ ] **Step 4: Every mesh's place, from Habitat**

In `vesper_world/habitat_scene.py`, replace:
```python
"""An HSSD scene loaded in Habitat, reduced to what the export needs (spec §3, plan W1a). Habitat is imported only
here, and only when a scene is loaded, so the rest of the package runs without it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from vesper_world.export import KINDS_CSV, ExportError, ground_floor, is_opening, opening_kind, opening_kinds
```
with:
```python
"""An HSSD scene loaded in Habitat, reduced to what the export needs (spec §3, plans W1a and W1b). Habitat is
imported only here, and only when a scene is loaded, so the rest of the package runs without it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from vesper_world.export import (IDENTITY_ROWS, KINDS_CSV, ExportError, flat, ground_floor, is_opening, matmul,
                                 opening_kind, opening_kinds, trs, urdf_visuals)
```

In `vesper_world/habitat_scene.py`, replace:
```python
    openings: list = field(default_factory=list)
    _pathfinder: object = None
```
with:
```python
    openings: list = field(default_factory=list)
    render: list = field(default_factory=list)  # the meshes the scene draws (export.scene_render)
    hssd: str = ""
    _pathfinder: object = None
```

In `vesper_world/habitat_scene.py`, replace:
```python
        objects = sim.get_rigid_object_manager()
        openings = []
```
with:
```python
        objects = sim.get_rigid_object_manager()
        render = _render_items(sim, objects, kinds, root)  # before the doors leave the scene
        openings = []
```

In `vesper_world/habitat_scene.py`, replace:
```python
        floor_y = ground_floor(regions)
        loaded = Scene(scene, floor_y, bounds, regions, openings)
        loaded._pathfinder = sim.pathfinder
        loaded._sim = sim  # kept alive while the pathfinder is used
        return loaded
    except BaseException:
        sim.close()
        raise
```
with:
```python
        floor_y = ground_floor(regions)
        loaded = Scene(scene, floor_y, bounds, regions, openings, render, str(root.resolve()))
        loaded._pathfinder = sim.pathfinder
        loaded._sim = sim  # kept alive while the pathfinder is used
        return loaded
    except BaseException:
        sim.close()
        raise


def _rows(node) -> list:
    """A scene node's world matrix, row by row (Magnum's Matrix4 is indexed column first)."""
    m = node.absolute_transformation()
    return [[float(m[c][r]) for c in range(4)] for r in range(4)]


def _relative(path, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def _render_items(sim, objects, kinds: dict, root: Path) -> list:
    """Every mesh the scene draws, with the world matrix Habitat gives it: the stage, each object (its first visual
    node is its glTF's root), and each articulated object's link meshes (the link's frame, then the URDF's origin)."""
    stage = sim.get_stage_initialization_template()
    items = [{"kind": "stage", "asset": _relative(stage.render_asset_fullpath, root), "matrix": flat(IDENTITY_ROWS)}]
    for handle in objects.get_object_handles():
        o = objects.get_object_by_handle(handle)
        item = {"kind": "object", "asset": _relative(o.creation_attributes.render_asset_fullpath, root),
                "matrix": flat(_rows(o.visual_scene_nodes[0]))}
        if is_opening(handle):
            item.update(handle=handle, opening=opening_kind(handle, kinds) or "unknown")
        items.append(item)
    articulated = sim.get_articulated_object_manager()
    for handle in articulated.get_object_handles():
        ao = articulated.get_object_by_handle(handle)
        urdf = Path(ao.creation_attributes.urdf_fullpath)
        visuals = urdf_visuals(urdf.read_text())
        for link in [-1, *ao.get_link_ids()]:
            frame = _rows(ao.get_link_scene_node(link))
            for mesh, xyz, rpy, scale in visuals.get(ao.get_link_name(link), []):
                items.append({"kind": "part", "asset": _relative(urdf.parent / mesh, root),
                              "matrix": flat(matmul(frame, trs(xyz, rpy, scale)))})
    return items
```

- [ ] **Step 5: The humanoids' walks**

Create `vesper_world/humanoids.py`:
```python
"""Habitat's humanoids for the 3D view (spec §3, plan W1b): each avatar's walk and standing pose, as joint rotations
a browser can play on the avatar's own skinned model (`<name>.glb`).

Habitat drives a humanoid from `<name>_motion_data_smplx.pkl`: a walk cycle (120 frames a second, one quaternion,
x y z w, per moving joint of `<name>.urdf`, in the URDF's order, each joint named by its child link, which is the
model's bone of that name) and a standing pose. The pickle holds numpy arrays, so it is read here, in the Habitat
environment, as Habitat itself reads it; the browser gets `vesper-motion/1` JSON at 30 frames a second.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

MOTION_FORMAT = "vesper-motion/1"
EVERY = 4  # Habitat's 120 frames a second, kept one in four


class HumanoidError(RuntimeError):
    """An avatar whose files do not fit together; the message says which."""


def joint_names(urdf_text: str) -> list:
    """The moving joints of a humanoid's URDF, in order, each named by its child link (the model's bone)."""
    return [j.find("child").get("link") for j in ET.fromstring(urdf_text).findall("joint") if j.get("type") != "fixed"]


def motion(avatar: str, joints, stop, walk, fps: float, displacement, every: int = EVERY) -> dict:
    """The walk (every `every`th frame) and the standing pose, with the distance one walk cycle covers."""
    n = len(joints)
    if len(stop) != n:
        raise HumanoidError(f"{avatar}: the standing pose has {len(stop)} joints, the skeleton {n}")
    for frame in walk:
        if len(frame) != n:
            raise HumanoidError(f"{avatar}: the walk has {len(frame)} joints, the skeleton {n}")
    return {"format": MOTION_FORMAT, "avatar": avatar, "joints": list(joints), "fps": fps / every, "stop": stop,
            "walk": walk[::every], "cycle_m": round(float(displacement[-1]), 4)}


def load_motion_file(path) -> dict:
    """Habitat's motion pickle as lists (needs numpy: the Habitat environment)."""
    import pickle
    with open(path, "rb") as f:
        d = pickle.load(f)  # noqa: S301 - Habitat's own humanoid data, which Habitat loads the same way
    walk = d["walk_motion"]

    def quaternions(rows):
        return [[round(float(v), 5) for v in q] for q in rows]
    return {"stop": quaternions(d["stop_pose"]["joints"]), "walk": [quaternions(f) for f in walk["joints_array"]],
            "fps": float(walk["fps"]), "displacement": [float(v) for v in walk["displacement"]]}


def convert_all(data: Path, out: Path) -> list:
    """Every avatar in Habitat's humanoid folder (`<name>/<name>.urdf`, `.glb` and motion file) as
    `<out>/<name>.motion.json`; the names converted."""
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for folder in sorted(p for p in Path(data).iterdir() if p.is_dir()):
        name = folder.name
        urdf, model = folder / f"{name}.urdf", folder / f"{name}.glb"
        pkl = folder / f"{name}_motion_data_smplx.pkl"
        if not (urdf.is_file() and model.is_file() and pkl.is_file()):
            continue
        m = load_motion_file(pkl)
        converted = motion(name, joint_names(urdf.read_text()), m["stop"], m["walk"], m["fps"], m["displacement"])
        (out / f"{name}.motion.json").write_text(json.dumps(converted))
        done.append(name)
    return done
```

In `vesper_world/__main__.py`, replace:
```python
"""`python -m vesper_world export --hssd DIR --scene ID --out DIR`: the scene's map file and door openings."""
from __future__ import annotations
```
with:
```python
"""The world service, run in the Habitat environment:

    python -m vesper_world export --hssd DIR --scene ID --out DIR    # a scene's map, openings and render manifest
    python -m vesper_world humanoids --data DIR --out DIR             # Habitat's humanoids' motions, for the browser
"""
from __future__ import annotations
```

In `vesper_world/__main__.py`, replace:
```python

from vesper_world.export import ExportError, scene_map, scene_openings
```
with:
```python

from vesper_world.export import ExportError, scene_map, scene_openings, scene_render
```

In `vesper_world/__main__.py`, replace:
```python
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
```
with:
```python
    actions = ap.add_subparsers(dest="action", required=True)
    ex = actions.add_parser("export", help="an HSSD scene's map file (map.json), openings (openings.json) and "
                                          "render manifest (render.json)")
    ex.add_argument("--hssd", required=True, help="the HSSD dataset directory (hssd-hab)")
    ex.add_argument("--scene", required=True, help="the scene id, such as 102344022")
    ex.add_argument("--out", required=True, help="the directory to write map.json and openings.json to")
    ex.add_argument("--radius", type=float, default=0.2, help="the walker's radius in metres (default 0.2)")
    hu = actions.add_parser("humanoids", help="each Habitat humanoid's walk and standing pose (<name>.motion.json)")
    hu.add_argument("--data", required=True, help="Habitat's humanoid folder (humanoid_data)")
    hu.add_argument("--out", required=True, help="the directory to write the motions to")
    args = ap.parse_args(argv)
    if args.action == "humanoids":
        return _humanoids(Path(args.data), Path(args.out))
    try:
        scene = load_scene(args.hssd, args.scene, args.radius)
        world_map, openings, render = scene_map(scene), scene_openings(scene), scene_render(scene)
    except ExportError as e:
```

In `vesper_world/__main__.py`, replace:
```python
    (out / "openings.json").write_text(json.dumps(openings, indent=1))
    doors = sum(o.get("kind") == "door" for o in openings["openings"])
```
with:
```python
    (out / "openings.json").write_text(json.dumps(openings, indent=1))
    (out / "render.json").write_text(json.dumps(render))
    doors = sum(o.get("kind") == "door" for o in openings["openings"])
```

In `vesper_world/__main__.py`, replace:
```python

def _count(n: int, noun: str) -> str:
```
with:
```python

def _humanoids(data: Path, out: Path) -> int:
    from vesper_world.humanoids import HumanoidError, convert_all
    try:
        done = convert_all(data, out) if data.is_dir() else []
    except (HumanoidError, OSError, KeyError, ValueError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not done:
        print(f"FAILED: no humanoids in {data} (<name>/<name>.urdf, .glb and _motion_data_smplx.pkl)", file=sys.stderr)
        return 1
    print(f"{_count(len(done), 'humanoid')} ({', '.join(done)}) -> {out}")
    return 0


def _count(n: int, noun: str) -> str:
```

- [ ] **Step 6: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/world -q 2>&1 | tail -1`

Expected: `22 passed`

- [ ] **Step 7: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper_world/export.py vesper_world/habitat_scene.py vesper_world/humanoids.py vesper_world/__main__.py tests/world/test_export.py tests/world/test_humanoids.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(world): a scene's render manifest (every mesh where Habitat puts it) and Habitat's humanoids' walks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: The Lab serves the 3D scene and the humanoids

**Files:**
- Create: `vesper/lab/world3d.py`, `vesper/lab/humanoids.py`, `tests/lab/test_world3d.py`, `tests/lab/test_humanoids.py`, `tests/lab/conftest.py`
- Modify: `vesper/lab/scenes.py` (`doorway_of` public; `run_world`, the isolated world service, for `export` and `humanoids`)
- Modify: `vesper/lab/play.py` (`/world/render.json`, `/hssd/`, `/humanoids/`; `hello`'s `world3d` and `humanoids`)
- Modify: `vesper/cli.py` (`lab play` and `lab look`: `--hssd`, `--humanoids`, the lines that say what is drawn; the look's browser waits up to 90 s)
- Modify: `tests/lab/test_play.py`, `tests/lab/test_cli_play.py`

**Interfaces:**
- Consumes: Task 1's files.
- Produces: `load_world3d(home, hssd=None) -> World3d | None` (`manifest` with each door mesh's `doorway`, `root`, `assets`, `missing`, `floor_y`); `find_humanoids(avatars, data=None, python=None, cache=None, run=None) -> (Humanoids | None, str)`; `hello()["world3d"]` = {"render", "floor_y", "items", "missing", "missing_files", "hssd"} or null; `hello()["humanoids"]` = {avatar: {"model": "/humanoids/<avatar>.glb", "motion": "/humanoids/<avatar>.motion.json"}} or null.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_world3d.py`:
```python
"""The 3D view's data (plan W1b): a home's render manifest, its doors, and the only HSSD files the page may get."""
import json

import pytest

from tests.lab.homes import catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.scenes import SceneError
from vesper.lab.world3d import load_world3d

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def at(x, z):
    return [1, 0, 0, x, 0, 1, 0, 0, 0, 0, 1, z, 0, 0, 0, 1]


def scene_home(tmp_path, items=None, hssd=None, files=("stages/s.glb", "objects/openings/211-2.glb")):
    """The test house as a home made from a scene: render.json, openings.json, and an HSSD folder with `files`."""
    root = tmp_path / "hssd"
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"glTF")
    items = items if items is not None else [
        {"kind": "stage", "asset": "stages/s.glb", "matrix": IDENTITY},
        {"kind": "object", "asset": "objects/openings/211-2.glb", "handle": "211-2_:0000", "opening": "door",
         "matrix": at(3.0, 4.5)},  # in the bedroom-hall doorway
        {"kind": "object", "asset": "objects/openings/218-20.glb", "handle": "218-20_:0000", "opening": "window",
         "matrix": at(0.0, 3.0)},  # a floor-length window where the front door is: not a door
        {"kind": "object", "asset": "objects/openings/211-2.glb", "handle": "211-2_:0001", "opening": "door",
         "matrix": at(6.0, 1.5)},  # a door in no doorway
    ]
    (tmp_path / "render.json").write_text(json.dumps({"format": "vesper-render/1", "scene": "test/house",
                                                      "floor_y": 0.25, "hssd": str(hssd or root), "items": items}))
    openings = [{"handle": i["handle"], "kind": i["opening"], "x": i["matrix"][3], "y": 1.1, "z": i["matrix"][11]}
                for i in items if "opening" in i]
    (tmp_path / "openings.json").write_text(json.dumps({"format": "vesper-openings/1", "openings": openings}))
    return load_lab_home(write_home(tmp_path), catalog())


def test_a_home_without_a_render_manifest_has_no_3d_scene(tmp_path):
    assert load_world3d(load_lab_home(write_home(tmp_path), catalog())) is None


def test_a_door_mesh_in_a_door_s_doorway_is_that_door_and_windows_are_not(tmp_path, monkeypatch):
    monkeypatch.delenv("VESPER_HSSD", raising=False)
    w = load_world3d(scene_home(tmp_path))
    items = w.manifest["items"]
    assert [i.get("doorway") for i in items] == [None, "bedroom-hall", None, None]
    assert items[1]["matrix"] == at(3.0, 4.5) and w.floor_y == 0.25
    assert w.root == tmp_path / "hssd" and w.missing == ["objects/openings/218-20.glb"]


@pytest.mark.parametrize("asset", ["../secret.glb", "/etc/passwd.glb", "objects/../../x.glb", "objects/a.txt", ""])
def test_a_manifest_that_names_a_file_outside_hssd_is_refused(tmp_path, asset):
    with pytest.raises(SceneError, match="render.json: item 2"):
        load_world3d(scene_home(tmp_path, items=[{"kind": "stage", "asset": "stages/s.glb", "matrix": IDENTITY},
                                                 {"kind": "object", "asset": asset, "matrix": IDENTITY}]))


ONE_BAD_ITEM = '{"format": "vesper-render/1", "scene": "test/house", "items": [{"matrix": [1]}]}'


@pytest.mark.parametrize("text, words", [("{", "cannot read"), ('{"format": "x"}', "not a VESPER render manifest"),
                                         (ONE_BAD_ITEM, "item 1")])
def test_a_broken_render_manifest_is_named(tmp_path, text, words):
    home = scene_home(tmp_path)
    (tmp_path / "render.json").write_text(text)
    with pytest.raises(SceneError, match=words):
        load_world3d(home)


def test_a_render_manifest_for_another_scene_is_refused(tmp_path):
    home = scene_home(tmp_path)
    manifest = json.loads((tmp_path / "render.json").read_text())
    (tmp_path / "render.json").write_text(json.dumps({**manifest, "scene": "hssd/102344022"}))
    with pytest.raises(SceneError, match="is for hssd/102344022, but the home is on test/house"):
        load_world3d(home)


def test_hssd_is_the_one_given_else_vesper_hssd_else_where_the_home_was_made(tmp_path, monkeypatch):
    home = scene_home(tmp_path)
    monkeypatch.delenv("VESPER_HSSD", raising=False)
    assert load_world3d(home).root == tmp_path / "hssd"
    monkeypatch.setenv("VESPER_HSSD", str(tmp_path / "moved"))
    assert load_world3d(home).root == tmp_path / "moved"
    assert load_world3d(home, tmp_path / "given").root == tmp_path / "given"
```

Create `tests/lab/test_humanoids.py`:
```python
"""Habitat's humanoids in the 3D view (plan W1b): which avatars a home's people get, their motions made once by the
world service and cached, and what is said when they are not there."""
import json
import stat
import sys

from vesper.lab.humanoids import find_humanoids

FAKE_WORLD = """#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:4] == ["-s", "-m", "vesper_world", "humanoids"], args
assert sorted(os.listdir(os.environ["PYTHONPATH"])) == ["vesper_world"]
data, out = Path(args[args.index("--data") + 1]), Path(args[args.index("--out") + 1])
if {fail!r}:
    print("FAILED: female_0: the walk has 3 joints, the skeleton 54", file=sys.stderr)
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
for d in sorted(data.iterdir()):
    (out / f"{{d.name}}.motion.json").write_text(json.dumps({{"format": "vesper-motion/1", "avatar": d.name}}))
print(len(list(out.iterdir())), "humanoids")
"""


def fake_python(tmp_path, fail=False):
    path = tmp_path / ("python-fail" if fail else "python")
    path.write_text(FAKE_WORLD.format(python=sys.executable, fail=fail))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def humanoid_data(root, names=("female_0", "male_0")):
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
        for suffix in (".glb", ".urdf", "_motion_data_smplx.pkl"):
            (root / name / f"{name}{suffix}").write_bytes(b"x")
    return root


def test_the_people_s_avatars_are_habitat_s_humanoids_made_once_and_cached(tmp_path):
    data, cache = humanoid_data(tmp_path / "humanoid_data"), tmp_path / "cache"
    h, said = find_humanoids({"female_0", "male_0"}, data, fake_python(tmp_path), cache)
    assert sorted(h.models) == ["female_0", "male_0"] and h.models["male_0"]["glb"] == data / "male_0" / "male_0.glb"
    assert json.loads(h.models["female_0"]["motion"].read_text())["avatar"] == "female_0"
    assert "people: Habitat's humanoids (female_0, male_0)" in said
    again, _ = find_humanoids({"female_0"}, data, tmp_path / "no-python", cache)  # cached: no world service
    assert sorted(again.models) == ["female_0"]


def test_an_avatar_habitat_does_not_have_is_a_plain_figure(tmp_path):
    data = humanoid_data(tmp_path / "humanoid_data", ("female_0",))
    h, said = find_humanoids({"female_0", "robot_7"}, data, fake_python(tmp_path), tmp_path / "cache")
    assert sorted(h.models) == ["female_0"] and "robot_7 is a plain figure" in said


def test_without_habitat_s_humanoids_the_people_are_plain_figures_and_it_says_why(tmp_path):
    h, said = find_humanoids({"female_0"}, tmp_path / "nowhere", fake_python(tmp_path), tmp_path / "cache")
    assert h is None and "plain figures" in said and "VESPER_HUMANOIDS" in said
    data = humanoid_data(tmp_path / "humanoid_data")
    h, said = find_humanoids({"female_0"}, data, tmp_path / "no-python", tmp_path / "cache")
    assert h is None and "plain figures" in said and "Habitat environment's Python" in said
    h, said = find_humanoids({"female_0"}, data, fake_python(tmp_path, fail=True), tmp_path / "cache")
    assert h is None and "the walk has 3 joints" in said
```

Create `tests/lab/conftest.py`:
```python
"""The Lab's tests never reach this machine's Habitat humanoids or its caches (playing a home would otherwise convert
them with the real Habitat environment): each test gets none, unless it sets its own."""
import pytest


@pytest.fixture(autouse=True)
def no_machine_humanoids(tmp_path, monkeypatch):
    monkeypatch.setenv("VESPER_HUMANOIDS", str(tmp_path / "no-humanoids"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
```

Append to `tests/lab/test_play.py`:
```python


def test_hello_says_whether_the_home_has_a_3d_scene_and_what_of_it_is_missing(tmp_path):
    from tests.lab.test_world3d import scene_home
    from vesper.lab.world3d import load_world3d
    (tmp_path / "plain").mkdir()
    plain = Session(load_lab_home(write_home(tmp_path / "plain"), catalog()), clock=WallClock())
    assert hello(plain)["world3d"] is None
    home = scene_home(tmp_path)
    s = Session(home, clock=WallClock())
    assert hello(s, world3d=load_world3d(home, tmp_path / "hssd"))["world3d"] == {
        "render": "/world/render.json", "floor_y": 0.25, "items": 4, "missing": 1,
        "missing_files": ["objects/openings/218-20.glb"], "hssd": str(tmp_path / "hssd")}


def test_the_scene_s_files_are_served_and_nothing_else_of_hssd(tmp_path):
    from tests.lab.test_world3d import scene_home
    from vesper.lab.world3d import load_world3d
    home = scene_home(tmp_path)
    (tmp_path / "hssd" / "objects" / "other.glb").write_bytes(b"glTF")  # in HSSD, but not in this scene
    (tmp_path / "secret.txt").write_text("not for the browser")
    session = Session(home, clock=WallClock())
    session.start()
    server = PlayServer(session, port=0, world3d=load_world3d(home, tmp_path / "hssd"))
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        status, kind, body = get(f"{base}/world/render.json")
        assert status == 200 and kind == "application/json"
        assert [i.get("doorway") for i in json.loads(body)["items"]] == [None, "bedroom-hall", None, None]
        status, kind, body = get(f"{base}/hssd/stages/s.glb")
        assert (status, kind, body) == (200, "model/gltf-binary", b"glTF")
        for path in ("objects/other.glb", "objects/openings/218-20.glb", "../secret.txt", "..%2Fsecret.txt",
                     "stages/../../secret.txt"):
            assert get(f"{base}/hssd/{path}")[0] == 404, path
    finally:
        server.stop()
        session.stop()


def test_a_home_without_a_3d_scene_serves_no_render_manifest(play):
    assert get(f"http://127.0.0.1:{play.port}/world/render.json")[0] == 404
    assert get(f"http://127.0.0.1:{play.port}/hssd/stages/s.glb")[0] == 404


def test_the_people_s_humanoids_are_served_and_only_theirs(tmp_path):
    from vesper.lab.humanoids import Humanoids
    data = tmp_path / "humanoid_data"
    (data / "female_0").mkdir(parents=True)
    (data / "female_0" / "female_0.glb").write_bytes(b"glTF-humanoid")
    (data / "male_0").mkdir()
    (data / "male_0" / "male_0.glb").write_bytes(b"glTF-other")  # Habitat has it; no one in this home is male_0
    (tmp_path / "female_0.motion.json").write_text('{"format": "vesper-motion/1"}')
    h = Humanoids(data, {"female_0": {"glb": data / "female_0" / "female_0.glb",
                                      "motion": tmp_path / "female_0.motion.json"}})
    session = Session(load_lab_home(write_home(tmp_path, IN_BEDROOM), catalog()), clock=WallClock())
    assert hello(session, humanoids=h)["humanoids"] == {
        "female_0": {"model": "/humanoids/female_0.glb", "motion": "/humanoids/female_0.motion.json"}}
    assert hello(session)["humanoids"] is None
    session.start()
    server = PlayServer(session, port=0, humanoids=h)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}/humanoids"
        assert get(f"{base}/female_0.glb") == (200, "model/gltf-binary", b"glTF-humanoid")
        assert get(f"{base}/female_0.motion.json")[:2] == (200, "application/json")
        for path in ("male_0.glb", "female_0.urdf", "../female_0.motion.json", "female_0/female_0.glb"):
            assert get(f"{base}/{path}")[0] == 404, path
    finally:
        server.stop()
        session.stop()
```

In `tests/lab/test_cli_play.py`, replace:
```python

def test_play_opens_the_browser_unless_told_not_to(tmp_path, monkeypatch):
```
with:
```python

def scene_setup(tmp_path):
    from tests.lab.homes import catalog
    from tests.lab.test_world3d import scene_home
    from tests.twins.conftest import write_catalog
    (tmp_path / "home").mkdir()
    return write_catalog(tmp_path / "vds", list(catalog().values())), scene_home(tmp_path / "home").path


def play_output(tmp_path, monkeypatch, capsys, *extra):
    cat, home = (scene_setup if "scene" in extra else setup)(tmp_path)
    seen = {}

    def interrupted():
        seen["out"] = capsys.readouterr().out
        base = re.search(r"http://127\.0\.0\.1:\d+/", seen["out"]).group(0)
        try:
            seen["render"] = json.loads(get(base + "world/render.json"))
        except Exception:  # noqa: BLE001 - a 404: no 3D scene
            seen["render"] = None
    monkeypatch.setattr(cli, "_wait_for_interrupt", interrupted)
    args = [a for a in extra if a != "scene"]
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", *args]) == 0
    return seen, home


def test_play_draws_a_home_made_from_a_scene_from_the_scene_s_files(tmp_path, capsys, monkeypatch):
    seen, home = play_output(tmp_path, monkeypatch, capsys, "scene")
    assert f"3D view: test/house, 4 meshes from {home.parent / 'hssd'}" in seen["out"]
    assert "1 of its 3 files is missing (objects/openings/218-20.glb)" in seen["out"]
    assert len(seen["render"]["items"]) == 4


def test_play_takes_hssd_from_where_it_is_told(tmp_path, capsys, monkeypatch):
    seen, _ = play_output(tmp_path, monkeypatch, capsys, "scene", "--hssd", str(tmp_path / "elsewhere"))
    assert f"4 meshes from {tmp_path / 'elsewhere'}" in seen["out"] and "3 of its 3 files are missing" in seen["out"]


def test_play_draws_a_home_without_a_scene_from_its_map(tmp_path, capsys, monkeypatch):
    seen, _ = play_output(tmp_path, monkeypatch, capsys)
    assert "3D view: drawn from the map" in seen["out"] and seen["render"] is None


def test_play_with_a_broken_render_manifest_says_so_and_draws_the_map(tmp_path, capsys, monkeypatch):
    cat, home = scene_setup(tmp_path)
    (home.parent / "render.json").write_text("{")
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "3D view: cannot read" in out and "the page draws the home from its map" in out


def test_play_draws_the_people_as_habitat_s_humanoids_when_they_are_there(tmp_path, capsys, monkeypatch):
    from tests.lab.test_humanoids import fake_python, humanoid_data
    monkeypatch.setenv("VESPER_HUMANOIDS", str(humanoid_data(tmp_path / "humanoid_data")))
    monkeypatch.setenv("VESPER_HABITAT_PYTHON", str(fake_python(tmp_path)))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    seen, _ = play_output(tmp_path, monkeypatch, capsys)
    assert "people: Habitat's humanoids (female_0)" in seen["out"]
    assert (tmp_path / "cache" / "vesper" / "humanoids" / "female_0.motion.json").is_file()


def test_play_without_habitat_s_humanoids_draws_plain_figures_and_says_why(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("VESPER_HUMANOIDS", str(tmp_path / "nowhere"))
    seen, _ = play_output(tmp_path, monkeypatch, capsys)
    assert "people: plain figures (Habitat's humanoids are not at" in seen["out"]


def test_play_opens_the_browser_unless_told_not_to(tmp_path, monkeypatch):
```

In `tests/lab/test_cli_play.py`, replace:
```python
    steps.write_text("- {shot: start}\n- {press: e}\n")
    fake = FakeBrowser()
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(lambda cls, path=None, **kw: fake))
    out = tmp_path / "look"
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(steps), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
```
with:
```python
    steps.write_text("- {shot: start}\n- {press: e}\n")
    fake, launched = FakeBrowser(), {}
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch",
                        classmethod(lambda cls, path=None, **kw: launched.update(kw) or fake))
    out = tmp_path / "look"
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(steps), "--out", str(out)]) == 0
    assert launched["timeout"] >= 60  # a scene's meshes keep a headless page busy for tens of seconds
    printed = capsys.readouterr().out
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_world3d.py tests/lab/test_humanoids.py tests/lab/test_play.py tests/lab/test_cli_play.py -q 2>&1 | tail -1`

Expected: `2 errors`: collection stops on `ModuleNotFoundError: No module named 'vesper.lab.world3d'` (and on `vesper.lab.humanoids`)

- [ ] **Step 3: The world service, run alike for scenes and humanoids**

In `vesper/lab/scenes.py`, replace:
```python
def export_scene(hssd: Path, scene: str, out: Path, python: Path, run=None) -> None:
    """Run the world service's export in the Habitat environment: `out` gets map.json and openings.json.
```
with:
```python
def export_scene(hssd: Path, scene: str, out: Path, python: Path, run=None) -> None:
    """Run the world service's export in the Habitat environment: `out` gets map.json, openings.json and
    render.json."""
    run_world(python, ["export", "--hssd", str(Path(hssd).resolve()), "--scene", scene, "--out",
                       str(Path(out).resolve())], f"scene {scene}", run)


def run_world(python: Path, args: list, what: str, run=None):
    """`python -m vesper_world ARGS` in the Habitat environment; a SceneError that says why when it fails.
```

In `vesper/lab/scenes.py`, replace:
```python
                         f"{PYTHON_ENV} (the environment with habitat-sim 0.3.3)")
    cmd = [str(python), "-s", "-m", "vesper_world", "export", "--hssd", str(Path(hssd).resolve()), "--scene", scene,
           "--out", str(Path(out).resolve())]
    with tempfile.TemporaryDirectory(prefix="vesper-world-") as path:
        shutil.copytree(WORLD_PACKAGE, Path(path) / "vesper_world", ignore=shutil.ignore_patterns("__pycache__"))
        env = {**os.environ, "PYTHONPATH": path}
        try:
            r = (run or subprocess.run)(cmd, env=env, cwd=path, capture_output=True, text=True,
                                        timeout=EXPORT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise SceneError(f"the world service did not export scene {scene} within {EXPORT_TIMEOUT_S} s") from None
    if r.returncode != 0:
        raise SceneError(f"{_why(r)} (scene {scene})")
```
with:
```python
                         f"{PYTHON_ENV} (the environment with habitat-sim 0.3.3)")
    with tempfile.TemporaryDirectory(prefix="vesper-world-") as path:
        shutil.copytree(WORLD_PACKAGE, Path(path) / "vesper_world", ignore=shutil.ignore_patterns("__pycache__"))
        env = {**os.environ, "PYTHONPATH": path}
        try:
            r = (run or subprocess.run)([str(python), "-s", "-m", "vesper_world", *args], env=env, cwd=path,
                                        capture_output=True, text=True, timeout=EXPORT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise SceneError(f"the world service did not finish {what} within {EXPORT_TIMEOUT_S} s") from None
    if r.returncode != 0:
        raise SceneError(f"{_why(r)} ({what})")
    return r
```

In `vesper/lab/scenes.py`, replace:
```python

def _doorway_of(world_map: WorldMap, opening: dict) -> str | None:
    """The doorway an opening stands in: the one whose centre is within DOOR_SNAP_M."""
```
with:
```python

def doorway_of(world_map: WorldMap, opening: dict) -> str | None:
    """The doorway an opening stands in: the one whose centre is within DOOR_SNAP_M."""
```

In `vesper/lab/scenes.py`, replace:
```python
    for o in openings:
        doorway = _doorway_of(world_map, o) if o.get("kind") == "door" else None
        if doorway and doorway not in doors:
```
with:
```python
    for o in openings:
        doorway = doorway_of(world_map, o) if o.get("kind") == "door" else None
        if doorway and doorway not in doors:
```

In `vesper/lab/scenes.py`, replace:
```python
    """The scene's doors that stand in no doorway of the map: the home leaves them out, and says so."""
    return [o["handle"] for o in openings if o.get("kind") == "door" and _doorway_of(world_map, o) is None]
```
with:
```python
    """The scene's doors that stand in no doorway of the map: the home leaves them out, and says so."""
    return [o["handle"] for o in openings if o.get("kind") == "door" and doorway_of(world_map, o) is None]
```

- [ ] **Step 4: The home's 3D scene, and the people's humanoids**

Create `vesper/lab/world3d.py`:
```python
"""The 3D view's data (spec §3, plan W1b): a home's render manifest, its doors, and the HSSD files it draws.

A home made from an HSSD scene holds `render.json` (`vesper-render/1`, written by the world service): every mesh
the scene draws, as a file inside HSSD and the world matrix Habitat gives it. The browser draws the scene from
those files, so the play server serves them, and only them: each asset must be a model file inside HSSD, and a
manifest that names anything else is refused whole. A door mesh standing in one of the home's doors' doorways is
that door, so the page swings it when the door opens. A home without a manifest is drawn from its map.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from vesper.lab.scenes import DEFAULT_HSSD, HSSD_ENV, SceneError, doorway_of

RENDER_FORMAT = "vesper-render/1"
MODEL_SUFFIXES = (".glb", ".gltf", ".bin", ".png", ".jpg", ".jpeg", ".ktx2")


@dataclass
class World3d:
    manifest: dict  # what the page gets: the items, a door's with its `doorway`
    root: Path  # the HSSD folder the assets are in
    assets: frozenset  # the only files the page may get, relative to root
    missing: list  # assets that are not in root
    floor_y: float


def load_world3d(home, hssd: Path | str | None = None) -> World3d | None:
    """The home's 3D scene, or None when it has no render manifest (it is drawn from its map). HSSD is `hssd`,
    else $VESPER_HSSD, else where the home was made, else the default."""
    path = home.path.parent / "render.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SceneError(f"cannot read {path}: {e}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != RENDER_FORMAT:
        raise SceneError(f"{path} is not a VESPER render manifest ({RENDER_FORMAT})")
    if manifest.get("scene") != home.scene:
        raise SceneError(f"{path} is for {manifest.get('scene')}, but the home is on {home.scene}")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise SceneError(f"{path}: no items")
    for k, item in enumerate(items, 1):
        problem = _problem(item)
        if problem:
            raise SceneError(f"render.json: item {k} {problem}")
    root = Path(hssd or os.environ.get(HSSD_ENV) or manifest.get("hssd") or DEFAULT_HSSD)
    doors = _door_handles(home, path.parent / "openings.json")
    page_items = [{**item, "doorway": doors[item["handle"]]} if item.get("handle") in doors else dict(item)
                  for item in items]
    assets = frozenset(item["asset"] for item in items)
    return World3d({"format": RENDER_FORMAT, "scene": home.scene, "items": page_items}, root, assets,
                   sorted(a for a in assets if not (root / a).is_file()), float(manifest.get("floor_y") or 0.0))


def _problem(item) -> str | None:
    if not isinstance(item, dict):
        return "is not an object"
    m = item.get("matrix")
    if not isinstance(m, list) or len(m) != 16 or not all(isinstance(v, int | float) for v in m):
        return "has no 4x4 matrix (16 numbers)"
    asset = item.get("asset")
    if not isinstance(asset, str) or not asset:
        return "has no asset"
    p = PurePosixPath(asset)
    if p.is_absolute() or ".." in p.parts or "\\" in asset or p.suffix.lower() not in MODEL_SUFFIXES:
        return f"names {asset!r}, which is not a model file inside HSSD"
    return None


def _door_handles(home, openings_path: Path) -> dict:
    """{door mesh handle: doorway} for the door meshes that stand in one of the home's doors' doorways."""
    try:
        openings = json.loads(openings_path.read_text()).get("openings") or []
    except (OSError, ValueError):
        return {}  # the doors stay closed meshes: still drawn, never swung
    doors = set(home.doors)  # doorway ids
    out = {}
    for o in openings:
        if isinstance(o, dict) and o.get("kind") == "door" and {"handle", "x", "z"} <= o.keys():
            doorway = doorway_of(home.map, o)
            if doorway in doors:
                out[o["handle"]] = doorway
    return out
```

Create `vesper/lab/humanoids.py`:
```python
"""Habitat's humanoids in the 3D view (spec §3, plan W1b): the people's skinned models, and their walk.

The models are Habitat's own (`humanoid_data/<name>/<name>.glb`, downloaded by the user under their licence, CC
BY-NC-SA). Each walk is `<name>.motion.json`, made once from Habitat's motion file by the world service
(`python -m vesper_world humanoids`, in the Habitat environment: the file is a numpy pickle) and kept in VESPER's
cache. A person whose avatar Habitat does not have, or every person when the humanoids or the Habitat environment
are missing, is drawn as a plain figure, and the line `find_humanoids` returns says so.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from vesper.lab.scenes import SceneError, run_world

HUMANOIDS_ENV = "VESPER_HUMANOIDS"
DEFAULT_HUMANOIDS = Path("data/humanoids/humanoid_data")  # relative to the working directory, as in the repository


def default_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vesper" / "humanoids"


@dataclass
class Humanoids:
    data: Path
    models: dict  # avatar name -> {"glb": its model, "motion": its walk (JSON)}


def find_humanoids(avatars, data=None, python=None, cache=None, run=None) -> tuple:
    """(Humanoids for the avatars Habitat has, or None; a line that says what the people look like)."""
    from vesper.lab.scenes import habitat_python
    root, cache = Path(data or os.environ.get(HUMANOIDS_ENV) or DEFAULT_HUMANOIDS), Path(cache or default_cache())
    if not root.is_dir():
        return None, (f"people: plain figures (Habitat's humanoids are not at {root}; download them and set "
                      f"{HUMANOIDS_ENV})")
    have = sorted(a for a in avatars if all((root / a / f"{a}{s}").is_file()
                                            for s in (".glb", ".urdf", "_motion_data_smplx.pkl")))
    if not have:
        return None, f"people: plain figures ({', '.join(sorted(avatars))}: no such humanoid in {root})"
    if any(not (cache / f"{a}.motion.json").is_file() for a in have):
        try:  # once: every avatar's walk, from Habitat's motion files
            run_world(habitat_python(python), ["humanoids", "--data", str(root.resolve()), "--out",
                                               str(cache.resolve())], "the humanoids' motions", run)
        except SceneError as e:
            return None, f"people: plain figures ({e})"
    have = [a for a in have if (cache / f"{a}.motion.json").is_file()]
    models = {a: {"glb": root / a / f"{a}.glb", "motion": cache / f"{a}.motion.json"} for a in have}
    plain = sorted(set(avatars) - set(have))
    tail = f"; {', '.join(plain)} {'is a plain figure' if len(plain) == 1 else 'are plain figures'}" if plain else ""
    return (Humanoids(root, models) if models else None), f"people: Habitat's humanoids ({', '.join(have)}){tail}"
```

- [ ] **Step 5: The play server and the commands**

In `vesper/lab/play.py`, replace:
```python

It listens on 127.0.0.1 only, and refuses WebSocket connections from another site's pages. A page gets `hello`
(the whole home) when it connects, then every change:
- `pose` {actor, x, z, yaw, room} after every step;
```
with:
```python

It listens on 127.0.0.1 only, and refuses WebSocket connections from another site's pages. Besides the page's own
files it serves a home's 3D scene (plan W1b): `/world/render.json`, and under `/hssd/` the HSSD files that manifest
lists, and no others; and under `/humanoids/` the people's Habitat humanoids, each a model and a walk. A page
gets `hello` (the whole home) when it connects, then every change:
- `pose` {actor, x, z, yaw, room} after every step;
```

In `vesper/lab/play.py`, replace:
```python
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json"}
QUEUE_MAX = 5000  # messages waiting for one page; a page that falls this far behind is disconnected
```
with:
```python
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json", ".wasm": "application/wasm", ".glb": "model/gltf-binary",
                 ".gltf": "model/gltf+json", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".ktx2": "image/ktx2"}
SCENE_CACHE = "private, max-age=3600"  # HSSD's files do not change: a reload need not fetch the scene again
QUEUE_MAX = 5000  # messages waiting for one page; a page that falls this far behind is disconnected
```

In `vesper/lab/play.py`, replace:
```python

def hello(session, hub_url: str | None = None, recent=()) -> dict:
    home, world, m = session.home, session.world, session.home.map
    return {
        "type": "hello", "scene": home.scene, "player": PLAYER, "hub": hub_url, "reach_m": INTERACT_M,
        "clock": {"t": session.clock.now(), "start_time": home.start_time},
```
with:
```python

def hello(session, hub_url: str | None = None, recent=(), world3d=None, humanoids=None) -> dict:
    home, world, m = session.home, session.world, session.home.map
    return {
        "type": "hello", "scene": home.scene, "player": PLAYER, "hub": hub_url, "reach_m": INTERACT_M,
        "world3d": None if world3d is None else {
            "render": "/world/render.json", "floor_y": world3d.floor_y, "items": len(world3d.manifest["items"]),
            "missing": len(world3d.missing), "missing_files": world3d.missing[:10], "hssd": str(world3d.root)},
        "humanoids": None if humanoids is None else {
            name: {"model": f"/humanoids/{name}.glb", "motion": f"/humanoids/{name}.motion.json"}
            for name in sorted(humanoids.models)},
        "clock": {"t": session.clock.now(), "start_time": home.start_time},
```

In `vesper/lab/play.py`, replace:
```python
    def __init__(self, session, *, host: str = "127.0.0.1", port: int = 18080, hub_url: str | None = None,
                 ui_dir: Path = UI_DIR):
        self.session, self.host, self.hub_url, self.ui_dir = session, host, hub_url, Path(ui_dir)
        self.port: int | None = None
```
with:
```python
    def __init__(self, session, *, host: str = "127.0.0.1", port: int = 18080, hub_url: str | None = None,
                 ui_dir: Path = UI_DIR, world3d=None, humanoids=None):
        self.session, self.host, self.hub_url, self.ui_dir = session, host, hub_url, Path(ui_dir)
        self.world3d = world3d  # vesper.lab.world3d.World3d, or None: the page draws the home from its map
        self.humanoids = humanoids  # vesper.lab.humanoids.Humanoids, or None: the people are plain figures
        self.port: int | None = None
```

In `vesper/lab/play.py`, replace:
```python
            return None
        root = self.ui_dir.resolve()
        f = (root / ("index.html" if path in ("", "/") else path.lstrip("/"))).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            return _response(404, "text/plain; charset=utf-8", b"not found")
        return _response(200, CONTENT_TYPES.get(f.suffix, "application/octet-stream"), f.read_bytes())
```
with:
```python
            return None
        if path == "/world/render.json" or path.startswith("/hssd/"):
            return self._scene_file(path)
        if path.startswith("/humanoids/"):
            return self._humanoid_file(path.removeprefix("/humanoids/"))
        root = self.ui_dir.resolve()
        f = (root / ("index.html" if path in ("", "/") else path.lstrip("/"))).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            return _response(404, "text/plain; charset=utf-8", b"not found")
        return _response(200, CONTENT_TYPES.get(f.suffix, "application/octet-stream"), f.read_bytes())

    def _scene_file(self, path: str):
        w = self.world3d
        if w is None:
            return _response(404, "text/plain; charset=utf-8", b"this home has no 3D scene")
        if path == "/world/render.json":
            return _response(200, "application/json", json.dumps(w.manifest).encode())
        asset = path.removeprefix("/hssd/")
        f = w.root / asset
        if asset not in w.assets or not f.is_file():  # only what the manifest lists, and it was checked when loaded
            return _response(404, "text/plain; charset=utf-8", b"not part of this scene")
        return _response(200, CONTENT_TYPES.get(f.suffix.lower(), "application/octet-stream"), f.read_bytes(),
                         cache=SCENE_CACHE)

    def _humanoid_file(self, name: str):
        h = self.humanoids
        avatar, _, kind = name.partition(".")
        files = h.models.get(avatar) if h is not None else None
        f = files and {"glb": files["glb"], "motion.json": files["motion"]}.get(kind)
        if not f or not f.is_file():  # only the people's own models and walks
            return _response(404, "text/plain; charset=utf-8", b"not a humanoid of this home")
        return _response(200, CONTENT_TYPES[f.suffix], f.read_bytes(), cache=SCENE_CACHE)
```

In `vesper/lab/play.py`, replace:
```python
            try:
                snapshot = await self._on_clock(lambda: hello(self.session, self.hub_url, self._recent))
            except Exception as e:  # noqa: BLE001 - kept for the look, then the page is closed
```
with:
```python
            try:
                snapshot = await self._on_clock(
                    lambda: hello(self.session, self.hub_url, self._recent, self.world3d, self.humanoids))
            except Exception as e:  # noqa: BLE001 - kept for the look, then the page is closed
```

In `vesper/lab/play.py`, replace:
```python

def _response(status: int, content_type: str, body: bytes) -> Response:
    return Response(status, http.HTTPStatus(status).phrase, Headers(
        [("Content-Type", content_type), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]), body)
```
with:
```python

def _response(status: int, content_type: str, body: bytes, cache: str = "no-store") -> Response:
    return Response(status, http.HTTPStatus(status).phrase, Headers(
        [("Content-Type", content_type), ("Content-Length", str(len(body))), ("Cache-Control", cache)]), body)
```

In `vesper/cli.py`, replace:
```python
            return 1
        server = PlayServer(session, port=args.port, hub_url=lab.ha.url if lab else None)
        stack.callback(server.stop)
```
with:
```python
            return 1
        world3d, drawn = _world3d(home, args.hssd)
        humanoids, people = _humanoids(home, args.humanoids)
        server = PlayServer(session, port=args.port, hub_url=lab.ha.url if lab else None, world3d=world3d,
                            humanoids=humanoids)
        stack.callback(server.stop)
```

In `vesper/cli.py`, replace:
```python
              "(Ctrl-C stops)")
        if lab:
```
with:
```python
              "(Ctrl-C stops)")
        print(drawn)
        print(people)
        if lab:
```

In `vesper/cli.py`, replace:
```python
    print(f"trace {trace}")
    return 0
```
with:
```python
    print(f"trace {trace}")
    return 0


LOOK_READY_S = 90  # a scene's meshes load in seconds on a GPU, and in tens of seconds in a headless browser


def _world3d(home, hssd):
    """The home's 3D scene for the page (plan W1b), and a line that says what the 3D view draws."""
    from vesper.lab.scenes import SceneError
    from vesper.lab.world3d import load_world3d
    try:
        w = load_world3d(home, hssd)
    except SceneError as e:
        return None, f"3D view: {e}; the page draws the home from its map"
    if w is None:
        return None, "3D view: drawn from the map (a home made with `vesper lab new --scene` is drawn from its scene)"
    n = len(w.missing)
    gone = "" if not n else (f"; {n} of its {len(w.assets)} files {'is' if n == 1 else 'are'} missing "
                             f"({', '.join(w.missing[:3])}{', …' if n > 3 else ''}): give HSSD's folder with "
                             "--hssd or VESPER_HSSD")
    return w, f"3D view: {home.scene}, {len(w.manifest['items'])} meshes from {w.root}{gone}"


def _humanoids(home, data):
    """Habitat's humanoids for the home's people (plan W1b), and a line that says what the people look like."""
    from vesper.lab.humanoids import find_humanoids
    return find_humanoids({home.avatar["model"], *(r.model for r in home.residents)}, data)
```

In `vesper/cli.py`, replace:
```python
            return 1
        server = PlayServer(session, port=0, hub_url=lab.ha.url if lab else None)
        stack.callback(server.stop)
        server.start()
        try:
            browser = Browser.launch(Path(args.browser) if args.browser else None, width=args.width,
                                     height=args.height)
        except BrowserError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        stack.callback(browser.close)
        report = run_look(browser, server.url, steps, out, golden=Path(args.golden) if args.golden else None,
                          update_golden=args.update_golden,
                          session_errors=lambda: server.errors + list(getattr(session.clock, "errors", [])))
```
with:
```python
            return 1
        world3d, drawn = _world3d(home, args.hssd)
        humanoids, people = _humanoids(home, args.humanoids)
        print(drawn)
        print(people)
        server = PlayServer(session, port=0, hub_url=lab.ha.url if lab else None, world3d=world3d,
                            humanoids=humanoids)
        stack.callback(server.stop)
        server.start()
        try:
            browser = Browser.launch(Path(args.browser) if args.browser else None, width=args.width,
                                     height=args.height, timeout=LOOK_READY_S)
        except BrowserError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        stack.callback(browser.close)
        report = run_look(browser, server.url, steps, out, golden=Path(args.golden) if args.golden else None,
                          update_golden=args.update_golden, ready_timeout=LOOK_READY_S,
                          session_errors=lambda: server.errors + list(getattr(session.clock, "errors", [])))
```

In `vesper/cli.py`, replace:
```python
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_play.add_argument("--hub", action="store_true",
```
with:
```python
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_play.add_argument("--hssd", help="HSSD's folder, for the 3D view (default: $VESPER_HSSD, else where the "
                                         "home was made)")
    lab_play.add_argument("--humanoids", help="Habitat's humanoid folder, for the people (default: "
                                              "$VESPER_HUMANOIDS, else data/humanoids/humanoid_data)")
    lab_play.add_argument("--hub", action="store_true",
```

In `vesper/cli.py`, replace:
```python
    lab_look.add_argument("--hub", action="store_true", help="with VESPER's broker and Home Assistant in the loop")
    lab_look.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
```
with:
```python
    lab_look.add_argument("--hub", action="store_true", help="with VESPER's broker and Home Assistant in the loop")
    lab_look.add_argument("--hssd", help="HSSD's folder, for the 3D view (default: $VESPER_HSSD, else where the "
                                         "home was made)")
    lab_look.add_argument("--humanoids", help="Habitat's humanoid folder, for the people (default: "
                                              "$VESPER_HUMANOIDS, else data/humanoids/humanoid_data)")
    lab_look.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
```

- [ ] **Step 6: Run them and watch them pass, then the Lab's tests**

Run: `.venv/bin/python -m pytest tests/lab/test_world3d.py tests/lab/test_humanoids.py tests/lab/test_play.py tests/lab/test_cli_play.py -q 2>&1 | tail -1`

Expected: `58 passed`

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `337 passed, 5 skipped` (in an export of `main`; the working copy's catalog runs a few of the skipped ones)

- [ ] **Step 7: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/world3d.py vesper/lab/humanoids.py vesper/lab/scenes.py vesper/lab/play.py vesper/cli.py tests/lab/test_world3d.py tests/lab/test_humanoids.py tests/lab/conftest.py tests/lab/test_play.py tests/lab/test_cli_play.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the play server serves a home's 3D scene and its people's Habitat humanoids, and nothing else of them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The page's 3D arithmetic, and three.js vendored

**Files:**
- Create: `vesper/lab/ui/scene3d.js`, `tests/lab/ui/scene3d.test.mjs`
- Modify: `vesper/lab/ui/mapview.js` (`centerView`, the minimap's view), `tests/lab/ui/mapview.test.mjs`
- Create: `vesper/lab/ui/vendor/three/…` (13 files of `three@0.170.0`, unchanged) and `vesper/lab/ui/vendor/three/NOTICE.md`
- Modify: `tests/lab/test_ui.py` (the vendored files are the pinned release; `vendor/` is not one of the page's own files)

**Interfaces:**
- Produces (`scene3d.js`): `EYE_M`, `PIVOT_M`, `BACK_M`, `RAISE_M`, `PITCH_MAX`; `forwardOf(yaw)`, `moveRelative(held, yaw, step?)`, `cameraPose({x, z, floorY, yaw, pitch, mode, distance}) -> {position, target}`, `pullIn(distance, hit)`, `aimAt(from, to)`, `aimCamera(pose, point)`, `deviceHeight(mount, category)`, `devicePoint(d, floorY)`, `wallRuns(cells, w, h)`, `wallBoxes(runs)`, `walkPhase(phase, moved, cycleM)`, `frameAt(phase, count)`, `roomAt(x, z, rooms)`, `hinge(minX, minZ, maxX, maxZ)`, `bearingOf(yaw)`, `compassMarks(yaw, fov)`. (`mapview.js`): `centerView(cx, cz, width, height, scale)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/ui/scene3d.test.mjs`:
```javascript
// The 3D view's arithmetic, run with `node --test` (tests/lab/test_ui.py runs it under pytest).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EYE_M, aimAt, aimCamera, bearingOf, cameraPose, compassMarks, devicePoint, frameAt, hinge, moveRelative, pullIn,
  roomAt, walkPhase, wallBoxes, wallRuns,
} from "../../../vesper/lab/ui/scene3d.js";
import { STEP_M } from "../../../vesper/lab/ui/mapview.js";

const near = (a, b, eps = 1e-9) => a.every((v, k) => Math.abs(v - b[k]) < eps);

test("W walks where the camera looks, S back, A and D sideways; a diagonal is no faster", () => {
  assert.ok(near(moveRelative(new Set(["w"]), 0), [STEP_M, 0])); // facing +x (east)
  assert.ok(near(moveRelative(new Set(["w"]), -90), [0, -STEP_M])); // facing -z (north, up the map)
  assert.ok(near(moveRelative(new Set(["d"]), -90), [STEP_M, 0])); // facing north, D is east
  assert.ok(near(moveRelative(new Set(["a"]), 0), [0, -STEP_M])); // facing east, A is north
  assert.ok(near(moveRelative(new Set(["arrowdown"]), 0), [-STEP_M, 0]));
  const [dx, dz] = moveRelative(new Set(["w", "d"]), 0);
  assert.ok(Math.abs(Math.hypot(dx, dz) - STEP_M) < 1e-9 && dx > 0 && dz > 0);
  assert.equal(moveRelative(new Set(["w", "s"]), 30), null); // they cancel
  assert.equal(moveRelative(new Set(), 30), null);
});

test("first person looks from the eyes; third person from behind and above, at what is ahead", () => {
  const first = cameraPose({ x: 2, z: 3, floorY: 0.5, yaw: 0, pitch: 0, mode: "first" });
  assert.ok(near(first.position, [2, 0.5 + EYE_M, 3]));
  assert.ok(first.target[0] > 2 && Math.abs(first.target[1] - first.position[1]) < 1e-9);
  const third = cameraPose({ x: 2, z: 3, floorY: 0, yaw: 90, pitch: 0, mode: "third" });
  assert.ok(third.position[2] < 3 && third.position[1] > EYE_M); // behind (facing +z) and above the eyes
  assert.ok(third.target[2] > 3); // looking past the avatar
  const shorter = cameraPose({ x: 2, z: 3, floorY: 0, yaw: 90, pitch: 0, mode: "third", distance: 0.5 });
  assert.ok(shorter.position[2] > third.position[2]); // pulled in toward the avatar
  const up = cameraPose({ x: 0, z: 0, yaw: 0, pitch: 30, mode: "first" });
  assert.ok(up.target[1] > up.position[1]); // pitch up looks up
});

test("a wall behind the avatar pulls the camera in, never through the avatar's head", () => {
  assert.equal(pullIn(2.4, Infinity), 2.4);
  assert.equal(pullIn(2.4, 1.0), 0.8); // 0.2 short of the wall
  assert.equal(pullIn(2.4, 0.1), 0.3); // never closer than 0.3
});

test("aiming turns the view to face a point: yaw on the floor, pitch up or down", () => {
  const a = aimAt([0, 1.6, 0], [0, 1.6, -5]);
  assert.ok(Math.abs(a.yaw + 90) < 1e-9 && Math.abs(a.pitch) < 1e-9);
  const b = aimAt([0, 1.6, 0], [1, 2.6, 0]);
  assert.ok(Math.abs(b.yaw) < 1e-9 && Math.abs(b.pitch - 45) < 1e-9);
});

test("aiming puts a point exactly on the crosshair, in either view, however close the camera is pulled in", () => {
  for (const [mode, distance] of [["third", undefined], ["third", 0.7], ["first", undefined]]) {
    for (const point of [[1.5, 2.3, -2], [0.6, 2.35, 0.4], [-3, 0.1, 2]]) {
      const pose = { x: 0, z: 0, floorY: 0.2, mode, distance };
      const cam = cameraPose({ ...pose, ...aimCamera(pose, point) });
      const view = cam.target.map((v, k) => v - cam.position[k]), to = point.map((v, k) => v - cam.position[k]);
      const cross = [view[1] * to[2] - view[2] * to[1], view[2] * to[0] - view[0] * to[2],
        view[0] * to[1] - view[1] * to[0]];
      const off = Math.asin(Math.hypot(...cross) / Math.hypot(...view) / Math.hypot(...to)) * (180 / Math.PI);
      assert.ok(off < 1e-9, `${mode} ${distance} ${point}: ${off} degrees off`); // exact, to rounding
    }
  }
});

test("a device stands where its mount puts it: up on the ceiling, a switch at hand height, a lock at the handle", () => {
  const at = (mount, category) => devicePoint({ x: 1, z: 2, mount, category }, 0.25)[1] - 0.25;
  assert.ok(at("ceiling", "motion_sensor") > 2.2 && at("ceiling", "light") > 2.2);
  assert.ok(Math.abs(at("wall", "switch") - 1.2) < 1e-9);
  assert.ok(at("table", "light") < 1 && at("table", "light") > 0.6);
  assert.ok(at("floor", "leak_sensor") < 0.2);
  assert.ok(at("door", "lock") < 1.2 && at("door", "contact_sensor") > 1.8);
  assert.deepEqual(devicePoint({ x: 1, z: 2, mount: "wall", category: "switch" }, 0).filter((_, k) => k !== 1), [1, 2]);
});

test("the walls of a home without a 3D scene are the blocked cells next to its floor, in runs", () => {
  // . = floor, # = blocked: the ring around the floor is wall; column 0, two cells from any floor, is not. Floor
  // at the grid's edge is walled just outside it (row -1 and 4, column 7): a map often ends at the outer wall.
  const rows = ["######.", "##..#..", "##..#..", "######."];
  const cells = Uint8Array.from(rows.join("").split(""), (c) => (c === "." ? 1 : 0));
  const runs = wallRuns(cells, 7, 4);
  assert.deepEqual(runs, [[-1, 5, 8], [0, 1, 6], [0, 7, 8], [1, 1, 2], [1, 4, 5], [1, 7, 8], [2, 1, 2], [2, 4, 5],
    [2, 7, 8], [3, 1, 6], [3, 7, 8], [4, 5, 8]]); // [j, i0, i1)
});

test("a wall's runs in consecutive rows become one box, so its faces do not flicker against each other", () => {
  const runs = [[-1, 5, 8], [0, 1, 6], [0, 7, 8], [1, 1, 2], [1, 4, 5], [1, 7, 8], [2, 1, 2], [2, 4, 5], [2, 7, 8],
    [3, 1, 6], [3, 7, 8], [4, 5, 8]];
  assert.deepEqual(wallBoxes(runs), [[5, 8, -1, 0], [1, 6, 0, 1], [7, 8, 0, 4], [1, 2, 1, 3], [4, 5, 1, 3],
    [1, 6, 3, 4], [5, 8, 4, 5]]); // [i0, i1, j0, j1)
});

test("a humanoid's walk cycle advances with the distance it covers, so its feet keep pace with the floor", () => {
  assert.equal(walkPhase(0, 0.655, 1.31), 0.5); // half a cycle's distance: half the cycle
  assert.ok(Math.abs(walkPhase(0.9, 0.262, 1.31) - 0.1) < 1e-9); // and round again
  assert.equal(walkPhase(0.3, 0, 1.31), 0.3); // standing still: the cycle waits
});

test("a phase falls between two of the walk's frames, blended by how far between", () => {
  assert.deepEqual(frameAt(0, 33), [0, 1, 0]);
  const [k0, k1, t] = frameAt(0.5, 33); // 16.5 frames in
  assert.deepEqual([k0, k1], [16, 17]);
  assert.ok(Math.abs(t - 0.5) < 1e-9);
  assert.deepEqual(frameAt(32.5 / 33, 33).slice(0, 2), [32, 0]); // the last frame blends into the first
});

test("a point's room is the polygon it is in, or none", () => {
  const rooms = [{ id: "hall", polygon: [[0, 0], [3, 0], [3, 6], [0, 6]] },
    { id: "porch", outdoor: true, polygon: [[-2, 2], [0, 2], [0, 4], [-2, 4]] }];
  assert.equal(roomAt(1, 1, rooms).id, "hall");
  assert.equal(roomAt(-1, 3, rooms).id, "porch");
  assert.equal(roomAt(5, 5, rooms), null);
});

test("a door turns about the end of its long side", () => {
  assert.deepEqual(hinge(2, 5, 3, 5.1), [2, 5.05]); // along x: hinged at its west end
  assert.deepEqual(hinge(4, 1, 4.1, 2), [4.05, 1]); // along z: hinged at its north end
});

test("the compass bar shows the bearings around where the camera looks, north as 0", () => {
  assert.equal(bearingOf(-90), 0); // up the map
  assert.equal(bearingOf(0), 90);
  assert.equal(bearingOf(180), 270);
  const marks = compassMarks(-90, 90);
  const north = marks.find((m) => m.label === "N");
  assert.ok(north && Math.abs(north.offset) < 1e-9);
  assert.ok(marks.some((m) => m.label === "NE" && m.offset > 0) && marks.some((m) => m.label === "NW" && m.offset < 0));
  assert.ok(marks.every((m) => m.offset >= -0.5 && m.offset <= 0.5));
  assert.ok(marks.some((m) => m.label === "15"));
});
```

In `tests/lab/ui/mapview.test.mjs`, replace:
```javascript
import {
  STEP_M, WALK_REPEAT_S, decodeGrid, feedLine, fitView, formatClock, headingName, hintFor, hitTest, moveFor,
  promptFor, recordLine, stepFor, stepInterval, tipPosition, walkKey,
```
with:
```javascript
import {
  STEP_M, WALK_REPEAT_S, centerView, decodeGrid, feedLine, fitView, formatClock, headingName, hintFor, hitTest, moveFor,
  promptFor, recordLine, stepFor, stepInterval, tipPosition, walkKey,
```

In `tests/lab/ui/mapview.test.mjs`, replace:
```javascript
  }
});
```
with:
```javascript
  }
});

test("the minimap's view keeps a point in the middle at a fixed scale, and maps back exactly", () => {
  const v = centerView(3, -2, 200, 160, 20);
  assert.equal(v.scale, 20);
  assert.deepEqual(v.toScreen(3, -2), [100, 80]);
  assert.deepEqual(v.toScreen(4, -1), [120, 100]);
  assert.deepEqual(v.toWorld(120, 100), [4, -1]);
});
```

In `tests/lab/test_ui.py`, replace:
```python
"""The browser page: its JavaScript tests (under node), and that it stands alone."""
import json
```
with:
```python
"""The browser page: its JavaScript tests (under node), and that it stands alone."""
import hashlib
import json
```

In `tests/lab/test_ui.py`, replace:
```python
        assert name.startswith("data:") or (UI_DIR / name).is_file(), name  # data: is inline, not fetched
    for f in UI_DIR.iterdir():
        text = f.read_text()
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", text), f"{f.name} loads from the internet"
        for imported in re.findall(r'from "\./([^"]+)"', text):
            assert (UI_DIR / imported).is_file(), f"{f.name} imports a missing {imported}"
```
with:
```python
        assert name.startswith("data:") or (UI_DIR / name).is_file(), name  # data: is inline, not fetched
    for f in UI_DIR.iterdir():  # the page's own files; vendor/ is checked against its release below
        if not f.is_file():
            continue
        text = f.read_text()
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", text), f"{f.name} loads from the internet"
        for imported in re.findall(r'from "\./([^"]+)"', text):
            assert (UI_DIR / imported).is_file(), f"{f.name} imports a missing {imported}"


def test_the_vendored_three_js_is_the_pinned_release_unchanged():
    vendor = UI_DIR / "vendor" / "three"
    pinned = dict(reversed(line.split("  ", 1)) for line in re.findall(r"^[0-9a-f]{64}  \S+$",
                                                                        (vendor / "NOTICE.md").read_text(), re.M))
    present = {p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file()} - {"NOTICE.md"}
    assert present == set(pinned)
    for name, digest in pinned.items():
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest, name
```

- [ ] **Step 2: Run them and watch them fail**

Run: `node --test tests/lab/ui/scene3d.test.mjs tests/lab/ui/mapview.test.mjs 2>&1 | grep -E '^ℹ (pass|fail) '`

Expected: `ℹ pass 0` and `ℹ fail 2`: neither file loads (`scene3d.js` does not exist; `mapview.js` has no `centerView`)

- [ ] **Step 3: The arithmetic**

Create `vesper/lab/ui/scene3d.js`:
```javascript
// The 3D view's arithmetic (plan W1b): no DOM and no three.js, so `node --test` checks it
// (tests/lab/ui/scene3d.test.mjs). World coordinates are the map's: metres, +x east, +z south (down the map), and
// y up from the floor. A yaw is in degrees as the session's poses have it: 0 faces +x (east), 90 faces +z (south).
import { STEP_M } from "./mapview.js";

export const EYE_M = 1.6; // first person: the eyes
export const PIVOT_M = 1.5; // third person: the camera turns about this point above the avatar's feet
export const BACK_M = 2.4; // ... from this far behind it
export const RAISE_M = 0.3; // ... and this much higher, as PUBG's over-the-shoulder view
export const PITCH_MAX = 80;
const AHEAD_M = 5; // the camera's target lies this far along its view

const rad = (deg) => (deg * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

// Where a yaw faces, on the floor: [x, z].
export const forwardOf = (yaw) => [Math.cos(rad(yaw)), Math.sin(rad(yaw))];

const AXES = { w: [1, 0], arrowup: [1, 0], s: [-1, 0], arrowdown: [-1, 0], a: [0, -1], arrowleft: [0, -1],
  d: [0, 1], arrowright: [0, 1] }; // [forward, right]

// The walking keys held, relative to where the camera looks: one step of `step` metres on the floor, or null.
export function moveRelative(held, yaw, step = STEP_M) {
  let ahead = 0, right = 0;
  for (const key of held) {
    const a = AXES[key];
    if (a) { ahead += a[0]; right += a[1]; }
  }
  const n = Math.hypot(ahead, right);
  if (!n) return null;
  const [fx, fz] = forwardOf(yaw), [rx, rz] = [-fz, fx]; // right of facing east is south
  return [((ahead * fx + right * rx) / n) * step, ((ahead * fz + right * rz) / n) * step];
}

// The camera for an avatar at (x, z) on a floor at floorY: {position, target}, each [x, y, z]. It looks along the
// view (yaw and pitch), so the crosshair at the screen's centre is exactly where the player aims. Third person sits
// `distance` behind the pivot (less when a wall is in the way, see pullIn) and a little higher; first person at the
// eyes.
export function cameraPose({ x, z, floorY = 0, yaw, pitch = 0, mode = "third", distance = BACK_M }) {
  const [fx, fz] = forwardOf(yaw), p = rad(pitch);
  const dir = [fx * Math.cos(p), Math.sin(p), fz * Math.cos(p)];
  const from = mode === "first"
    ? [x, floorY + EYE_M, z]
    : [x - distance * dir[0], floorY + PIVOT_M + RAISE_M - distance * dir[1], z - distance * dir[2]];
  return { position: from, target: from.map((v, k) => v + AHEAD_M * dir[k]) };
}

// The yaw and pitch that put the crosshair on `point`. The third-person camera always lies on the line through the
// raised pivot along the view, whatever its distance, so aiming from that pivot is exact; first person aims from
// the eyes.
export function aimCamera({ x, z, floorY = 0, mode = "third" }, point) {
  const from = [x, floorY + (mode === "first" ? EYE_M : PIVOT_M + RAISE_M), z];
  return aimAt(from, point);
}

// The third-person distance when something is `hit` metres behind the pivot: just short of it, never closer than
// `least` (inside the avatar's head).
export const pullIn = (distance, hit, margin = 0.2, least = 0.3) => Math.min(distance, Math.max(least, hit - margin));

// The yaw and pitch that face `to` from `from` (each [x, y, z]).
export function aimAt(from, to) {
  const dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2];
  return { yaw: deg(Math.atan2(dz, dx)), pitch: deg(Math.atan2(dy, Math.hypot(dx, dz))) };
}

// How high a device sits above the floor, by its mount and category (home.yaml has only its place on the floor).
export function deviceHeight(mount, category) {
  if (mount === "ceiling") return 2.35;
  if (mount === "table") return 0.8;
  if (mount === "floor") return 0.08;
  if (mount === "door") return category === "lock" ? 1.0 : 2.0; // a lock at the handle, a contact at the top
  if (category === "switch" || category === "button") return 1.2;
  return category === "thermostat" ? 1.5 : 1.4; // the other wall mounts
}

export const devicePoint = (d, floorY = 0) => [d.x, floorY + deviceHeight(d.mount, d.category), d.z];

// The walls of a home with no 3D scene: the blocked cells with walkable floor among their eight neighbours, as
// runs along a row, [j, i0, i1) in cells. The cells just outside the grid count as blocked: a map often ends at
// its outer walls, which then stand at j = -1 or height, i = -1 or width.
export function wallRuns(cells, width, height) {
  const walkable = (i, j) => i >= 0 && j >= 0 && i < width && j < height && cells[j * width + i];
  const isWall = (i, j) => {
    if (walkable(i, j)) return false;
    for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) if (walkable(i + di, j + dj)) return true;
    return false;
  };
  const runs = [];
  for (let j = -1; j <= height; j++) {
    let i = -1;
    while (i <= width) {
      if (!isWall(i, j)) { i++; continue; }
      const start = i;
      while (i <= width && isWall(i, j)) i++;
      runs.push([j, start, i]);
    }
  }
  return runs;
}

// The wall runs as boxes, [i0, i1, j0, j1) in cells: a run repeated in the next row extends the box, so a wall
// is one box, not a stack of 10 cm slices whose touching faces flicker.
export function wallBoxes(runs) {
  const open = new Map(), boxes = []; // "i0,i1" -> the box still growing downward
  for (const [j, i0, i1] of runs) {
    const key = `${i0},${i1}`, box = open.get(key);
    if (box && box[3] === j) box[3] = j + 1;
    else {
      const fresh = [i0, i1, j, j + 1];
      open.set(key, fresh);
      boxes.push(fresh);
    }
  }
  return boxes.sort((a, b) => a[2] - b[2] || a[0] - b[0]);
}

// A humanoid's place in its walk cycle (0 to 1), moved on by `moved` metres of a cycle that covers `cycleM`: the
// feet keep pace with the floor, and a person standing still holds the phase.
export function walkPhase(phase, moved, cycleM) {
  const p = (phase + moved / cycleM) % 1;
  return p < 0 ? p + 1 : p;
}

// The two walk frames a phase falls between and how far between: [k0, k1, t]; the last frame blends into the first.
export function frameAt(phase, count) {
  const at = phase * count, k0 = Math.floor(at) % count;
  return [k0, (k0 + 1) % count, at - Math.floor(at)];
}

// The room whose polygon holds (x, z), or null: the map's rooms ([{polygon: [[x, z], ...]}, ...]).
export function roomAt(x, z, rooms) {
  for (const room of rooms) {
    let inside = false;
    const p = room.polygon;
    for (let a = 0, b = p.length - 1; a < p.length; b = a++) {
      if ((p[a][1] > z) !== (p[b][1] > z) && x < ((p[b][0] - p[a][0]) * (z - p[a][1])) / (p[b][1] - p[a][1]) + p[a][0]) {
        inside = !inside;
      }
    }
    if (inside) return room;
  }
  return null;
}

// Where a door turns, on the floor: the end of its long side (its box from minX, minZ to maxX, maxZ).
export const hinge = (minX, minZ, maxX, maxZ) =>
  maxX - minX >= maxZ - minZ ? [minX, (minZ + maxZ) / 2] : [(minX + maxX) / 2, minZ];

const POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
// A compass bearing (0 is north, up the map; 90 east) for a yaw.
export const bearingOf = (yaw) => ((((yaw + 90) % 360) + 360) % 360);

// The compass bar (PUBG's): the marks within `fov` degrees around where the camera looks, each {label, offset}
// with offset from -0.5 (the bar's left end) to 0.5 (its right end); a mark every 15 degrees.
export function compassMarks(yaw, fov = 120) {
  const b = bearingOf(yaw), marks = [];
  for (let t = Math.ceil((b - fov / 2) / 15) * 15; t <= b + fov / 2 + 1e-9; t += 15) {
    const at = ((t % 360) + 360) % 360;
    marks.push({ label: at % 45 === 0 ? POINTS[at / 45] : String(at), offset: (t - b) / fov });
  }
  return marks;
}
```

In `vesper/lab/ui/mapview.js`, replace:
```javascript
    toWorld: (px, pz) => [bounds.minX + (px - ox) / scale, bounds.minZ + (pz - oz) / scale],
  };
```
with:
```javascript
    toWorld: (px, pz) => [bounds.minX + (px - ox) / scale, bounds.minZ + (pz - oz) / scale],
  };
}

// The minimap's view: the point (cx, cz) in the middle of a width x height canvas, at `scale` pixels a metre.
export function centerView(cx, cz, width, height, scale) {
  return {
    scale,
    toScreen: (x, z) => [width / 2 + (x - cx) * scale, height / 2 + (z - cz) * scale],
    toWorld: (px, pz) => [cx + (px - width / 2) / scale, cz + (pz - height / 2) / scale],
  };
```

- [ ] **Step 4: Vendor three.js 0.170.0, unchanged**

Copy the 13 files the page needs out of the npm package (network: about 30 MB, once):

Run: `T=$(mktemp -d) && (cd $T && npm pack three@0.170.0 --silent >/dev/null && tar xzf three-0.170.0.tgz) && V=vesper/lab/ui/vendor/three && for f in build/three.module.min.js examples/jsm/loaders/GLTFLoader.js examples/jsm/loaders/KTX2Loader.js examples/jsm/environments/RoomEnvironment.js examples/jsm/libs/ktx-parse.module.js examples/jsm/libs/zstddec.module.js examples/jsm/math/ColorSpaces.js examples/jsm/utils/BufferGeometryUtils.js examples/jsm/utils/WorkerPool.js examples/jsm/utils/SkeletonUtils.js examples/jsm/libs/basis/basis_transcoder.js examples/jsm/libs/basis/basis_transcoder.wasm LICENSE; do mkdir -p $V/$(dirname $f) && cp $T/package/$f $V/$f; done && ls $V/examples/jsm/libs/basis`

Expected: `basis_transcoder.js` and `basis_transcoder.wasm`

Create `vesper/lab/ui/vendor/three/NOTICE.md`:
````markdown
# three.js 0.170.0, vendored for the 3D view (plan W1b)

These files are copied unchanged from the npm package `three@0.170.0` (`npm pack three@0.170.0`), so the page
loads nothing from the internet. `tests/lab/test_ui.py` checks each file against the SHA-256 below.

- three.js and its examples (`build/`, `examples/jsm/loaders`, `environments`, `math`, `utils`): MIT, see `LICENSE`.
- `examples/jsm/libs/ktx-parse.module.js`: ktx-parse by Don McCurdy, MIT.
- `examples/jsm/libs/zstddec.module.js`: zstddec by Don McCurdy, MIT (it embeds Zstandard's decoder, BSD).
- `examples/jsm/libs/basis/`: the Basis Universal transcoder by Binomial LLC, Apache License 2.0.

```
4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454  LICENSE
08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7  build/three.module.min.js
85869bdd22f7cf0b22e68dcefe6cb710480af0f5c901a76f7a7764fd07fc8014  examples/jsm/environments/RoomEnvironment.js
8478b5b6d6b74e7d3082b89f6417321d8d1dc0307f2b30d4484bb11b441696a1  examples/jsm/libs/basis/basis_transcoder.js
6cf17dc889352c42e9acf8897107978d127005fe3386c36a0e3845e27967630a  examples/jsm/libs/basis/basis_transcoder.wasm
34a3a68f08db30a353802c5a762c5dcd89a168a1b56567019ef4a7563ceaf4d4  examples/jsm/libs/ktx-parse.module.js
5cbf818e842628a4464e748594a6deae18ceddda3c2f541e7b3a0ff5fc7611e2  examples/jsm/libs/zstddec.module.js
45139faddd5aaf48ed2d62203d976e5cbd703db1a592de40527f9f6cf58abd44  examples/jsm/loaders/GLTFLoader.js
fbf80e9b0237ffe2e58132d3fcb124a4a62fbd17f9716418ccf865708c2344df  examples/jsm/loaders/KTX2Loader.js
56981604d66cccc26558c710afe08a15177030f8dd3842255d1cc53b4c9b7f3b  examples/jsm/math/ColorSpaces.js
c25b7930e570e9ec56173cd3b866ec8d2e10016630db3937efb439daf1cedbf6  examples/jsm/utils/BufferGeometryUtils.js
ee412be1aa05f3051c2bda8ea89612c1ba70c5c87de62b56040282120d5eb991  examples/jsm/utils/SkeletonUtils.js
94ff7b608caf3b827ffa95bd6092b9d174732b856915b38ed006b4f049f95675  examples/jsm/utils/WorkerPool.js
```
````

- [ ] **Step 5: Run them and watch them pass**

Run: `node --test tests/lab/ui/scene3d.test.mjs tests/lab/ui/mapview.test.mjs 2>&1 | grep -E '^ℹ (pass|fail) '`

Expected: `ℹ pass 34` and `ℹ fail 0`

Run: `.venv/bin/python -m pytest tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `6 passed`

- [ ] **Step 6: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/ui/scene3d.js vesper/lab/ui/mapview.js tests/lab/ui/scene3d.test.mjs tests/lab/ui/mapview.test.mjs vesper/lab/ui/vendor/three tests/lab/test_ui.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the 3D view's arithmetic, and three.js 0.170.0 vendored unchanged, pinned by SHA-256

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: The 3D view in the page

**Files:**
- Create: `vesper/lab/ui/view3d.js`
- Modify: `vesper/lab/ui/app.js`, `vesper/lab/ui/index.html`, `vesper/lab/ui/style.css`, `tests/lab/ui/app.test.mjs` (the stub page has a body), `tests/lab/test_ui.py` (the import map)

**Interfaces:**
- Consumes: Task 2's `hello` (`world3d`, `humanoids`), Task 3's `scene3d.js` and the vendored three.js.
- Produces: `createView3d(container, hello, onProgress) -> {status, camera, floorY, frame(state, cam, dt), resize(), pick(), pointOf(target), colors()}`; on `window.vesper`: `mode`, `crosshair`, `setView("map"|"3d")`, `aim(target)`, `camera()`, `view3d()`, `view3dColors()`.

- [ ] **Step 1: Write the failing test**

In `tests/lab/test_ui.py`, replace:
```python
        assert name.startswith("data:") or (UI_DIR / name).is_file(), name  # data: is inline, not fetched
    for f in UI_DIR.iterdir():  # the page's own files; vendor/ is checked against its release below
```
with:
```python
        assert name.startswith("data:") or (UI_DIR / name).is_file(), name  # data: is inline, not fetched
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', page, re.S).group(1))["imports"]
    assert imports == {"three": "./vendor/three/build/three.module.min.js",
                       "three/addons/": "./vendor/three/examples/jsm/"}
    for f in UI_DIR.iterdir():  # the page's own files; vendor/ is checked against its release below
```

In `tests/lab/test_ui.py`, replace:
```python
            assert (UI_DIR / imported).is_file(), f"{f.name} imports a missing {imported}"
```
with:
```python
            assert (UI_DIR / imported).is_file(), f"{f.name} imports a missing {imported}"
        for imported in re.findall(r'from "three/addons/([^"]+)"', text):
            assert (UI_DIR / "vendor/three/examples/jsm" / imported).is_file(), f"{f.name}: {imported}"
```

In `tests/lab/ui/app.test.mjs`, replace:
```javascript
  globalThis.requestAnimationFrame = () => 0;
  globalThis.document = { getElementById: (id) => (elements[id] ||= new El(id)), createElement: (tag) => new El(tag) };
  globalThis.window = globalThis;
```
with:
```javascript
  globalThis.requestAnimationFrame = () => 0;
  globalThis.document = { getElementById: (id) => (elements[id] ||= new El(id)), createElement: (tag) => new El(tag),
    body: new El("body") };
  globalThis.window = globalThis;
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `1 failed, 5 passed`: `test_the_page_loads_only_its_own_files` finds no import map

- [ ] **Step 3: The 3D view**

Create `vesper/lab/ui/view3d.js`:
```javascript
// The 3D view (spec §2, §2a, §3; plan W1b): the home drawn with three.js. A home made from an HSSD scene is drawn
// from the scene's own files, each placed with the matrix Habitat gives it (/world/render.json); a home without one
// is drawn from its map: floor, walls and doors. The session stays the authority: people, devices and doors are
// drawn where its messages put them, and a door swings when the session opens it. A person is Habitat's humanoid
// for their avatar (its own model, walking with Habitat's walk as fast as they move, standing in Habitat's standing
// pose), or a plain figure when Habitat's humanoids are not installed. Third person by default, first person for V;
// the crosshair is the screen's centre, and pick() says which device or door it is on.
// The arithmetic is in scene3d.js, tested under node; this file needs a browser with WebGL (the looks check it).
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { clone as cloneSkinned } from "three/addons/utils/SkeletonUtils.js";
import { colorFor, decodeGrid, gridBounds } from "./mapview.js";
import {
  BACK_M, PIVOT_M, RAISE_M, cameraPose, devicePoint, frameAt, hinge, pullIn, roomAt, walkPhase, wallBoxes, wallRuns,
} from "./scene3d.js";

const SKY = 0xbfd6ea, WALL = 0xe9e4da, FLOOR = 0xd2c9b8, OUTSIDE = 0x9fb98e, DOOR = 0x8a5a2b, LOCKED = 0xc0392b;
const WALL_M = 2.5, DOOR_M = 2.05, PICK_M = 8, SWING = Math.PI / 2, SWING_S = 0.35;
const PLAYER = 0x2f6fed, RESIDENTS = [0xd9822b, 0x3fb37f, 0xb36bd9, 0xd94f4f];
const LAMP = 0xffd59a, SEEN = 0xff8c32;
const WALKING_MS = 0.15; // slower than this, a person stands

export async function createView3d(container, hello, onProgress = () => {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); // the looks read it
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.8;
  container.append(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY); // what a window shows
  scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.55;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7f70, 1.0));
  const sun = new THREE.DirectionalLight(0xfff4e0, 0.8);
  sun.position.set(4, 10, 2);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(65, 1, 0.05, 200);
  const floorY = hello.world3d ? hello.world3d.floor_y : 0;
  const solids = []; // what the camera and the crosshair do not pass: the stage, or the map's walls
  const pickables = []; // device markers and doors; their meshes carry userData.target
  const doors = {}; // doorway id -> {pivot, angle}
  const devices = {}; // name -> {marker, light, base}
  const actors = {}; // actor -> {group, x, z, yaw}
  const status = { source: hello.world3d ? "scene" : "map", total: 0, loaded: 0, failed: 0, missing: [] };
  const ktx2 = new KTX2Loader().setTranscoderPath("./vendor/three/examples/jsm/libs/basis/").detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx2);
  const humanoids = {}; // avatar -> a promise of {model, motion}: each loaded once, cloned per person

  const tag = (obj, target, kind) => obj.traverse((o) => { o.userData.target = target; o.userData.kind = kind; });

  function addDoor(id, obj, matrix) { // obj placed by matrix; it turns about its hinge when the door opens
    obj.matrixAutoUpdate = false;
    obj.matrix.copy(matrix);
    const box = new THREE.Box3().setFromObject(obj);
    const [hx, hz] = hinge(box.min.x, box.min.z, box.max.x, box.max.z);
    const pivot = new THREE.Group();
    pivot.position.set(hx, 0, hz);
    obj.matrix.premultiply(new THREE.Matrix4().makeTranslation(-hx, 0, -hz));
    pivot.add(obj);
    scene.add(pivot);
    tag(obj, id, "door");
    pickables.push(obj);
    doors[id] = { pivot, angle: 0 };
  }

  async function loadScene() {
    const manifest = await (await fetch(hello.world3d.render)).json();
    status.total = manifest.items.length;
    await Promise.all(manifest.items.map(async (item) => {
      try {
        const gltf = await loader.loadAsync(`/hssd/${item.asset.split("/").map(encodeURIComponent).join("/")}`);
        const matrix = new THREE.Matrix4().set(...item.matrix); // row by row
        if (item.doorway) {
          addDoor(item.doorway, gltf.scene, matrix);
        } else {
          gltf.scene.matrixAutoUpdate = false;
          gltf.scene.matrix.copy(matrix);
          scene.add(gltf.scene);
        }
        if (item.kind === "stage") solids.push(gltf.scene);
        status.loaded += 1;
      } catch (e) {
        status.failed += 1;
        status.missing.push(item.asset);
      }
      onProgress(status);
    }));
    for (const door of hello.doors) if (!doors[door.id]) addSlab(door.id); // a door the scene has no mesh for
  }

  function addSlab(id) { // a plain door leaf in the doorway's box
    const dw = hello.map.doorways.find((d) => d.id === id);
    if (!dw) return;
    const [x0, z0, x1, z1] = dw.box, alongX = x1 - x0 >= z1 - z0;
    const geo = new THREE.BoxGeometry(alongX ? x1 - x0 : 0.05, DOOR_M, alongX ? 0.05 : z1 - z0);
    const leaf = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: DOOR, roughness: 0.7 }));
    addDoor(id, leaf, new THREE.Matrix4().makeTranslation((x0 + x1) / 2, floorY + DOOR_M / 2, (z0 + z1) / 2));
  }

  function buildFromMap() { // the floor, the walls (blocked cells next to floor) and the doors, from the map
    const g = hello.map.grid, cells = decodeGrid(g.walkable, g.width, g.height), [ox, oz] = g.origin, c = g.cell;
    const inside = [], outside = [], walls = [];
    for (let j = 0; j < g.height; j++) {
      let i = 0;
      while (i < g.width) {
        if (!cells[j * g.width + i]) { i++; continue; }
        const start = i;
        while (i < g.width && cells[j * g.width + i]) i++;
        const x = ox + ((start + i) / 2) * c, z = oz + (j + 0.5) * c, room = roomAt(x, z, hello.map.rooms);
        const box = new THREE.BoxGeometry((i - start) * c, 0.02, c).translate(x, floorY - 0.01, z);
        (room && room.outdoor ? outside : inside).push(box);
      }
    }
    for (const [i0, i1, j0, j1] of wallBoxes(wallRuns(cells, g.width, g.height))) {
      walls.push(new THREE.BoxGeometry((i1 - i0) * c, WALL_M, (j1 - j0) * c)
        .translate(ox + ((i0 + i1) / 2) * c, floorY + WALL_M / 2, oz + ((j0 + j1) / 2) * c));
    }
    const mesh = (parts, color) => {
      const m = new THREE.Mesh(mergeGeometries(parts), new THREE.MeshStandardMaterial({ color, roughness: 0.9 }));
      scene.add(m);
      return m;
    };
    if (inside.length) mesh(inside, FLOOR);
    if (outside.length) mesh(outside, OUTSIDE);
    if (walls.length) solids.push(mesh(walls, WALL));
    const b = gridBounds(g), ceiling = new THREE.Mesh(new THREE.PlaneGeometry(b.maxX - b.minX, b.maxZ - b.minZ),
      new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    ceiling.rotation.x = Math.PI / 2;
    ceiling.position.set((b.minX + b.maxX) / 2, floorY + WALL_M, (b.minZ + b.maxZ) / 2);
    ceiling.visible = false; // not drawn (the house stays open to the sky), but the camera stops under it
    scene.add(ceiling);
    solids.push(ceiling);
    for (const door of hello.doors) addSlab(door.id);
  }

  function label(text, height = 0.12) { // a name tag above a device or a person, `height` metres tall
    const pad = 12, font = "600 28px system-ui, sans-serif", probe = document.createElement("canvas").getContext("2d");
    probe.font = font;
    const w = Math.ceil(probe.measureText(text).width) + 2 * pad, h = 44;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "rgba(18, 22, 31, 0.78)";
    ctx.beginPath();
    ctx.roundRect(0, 0, w, h, 10);
    ctx.fill();
    ctx.font = font;
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, pad, h / 2 + 1);
    const map = new THREE.CanvasTexture(canvas);
    map.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map, transparent: true }));
    sprite.scale.set((height * w) / h, height, 1);
    return sprite;
  }

  for (const d of hello.devices) { // a marker, a name and, for a lamp, its light
    const [x, y, z] = devicePoint(d, floorY), base = new THREE.Color(colorFor(d.category));
    const marker = new THREE.Mesh(new THREE.SphereGeometry(0.07, 20, 14),
      new THREE.MeshStandardMaterial({ color: base, emissive: base, emissiveIntensity: 0.3, roughness: 0.35 }));
    marker.position.set(x, y, z);
    const reach = new THREE.Mesh(new THREE.SphereGeometry(0.25, 8, 6), new THREE.MeshBasicMaterial({ visible: false }));
    reach.position.set(x, y, z); // a larger target for the crosshair than the marker itself
    tag(reach, d.name, "device");
    pickables.push(reach);
    const name = label(d.name, 0.08);
    name.position.set(x, y + 0.16, z);
    scene.add(marker, reach, name);
    let light = null;
    if (d.category === "light") {
      light = new THREE.PointLight(LAMP, 0, 7, 2);
      light.position.set(x, y - 0.1, z);
      scene.add(light);
    }
    devices[d.name] = { marker, light, base };
  }

  function plainFigure(color) { // facing +x: body, head, and a nose that shows its heading
    const skin = new THREE.MeshStandardMaterial({ color, roughness: 0.6 });
    const figure = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.19, 0.95, 6, 14), skin);
    body.position.y = 0.72;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 14), skin);
    head.position.y = 1.52;
    const nose = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.04, 0.05), new THREE.MeshStandardMaterial({ color: 0xffffff }));
    nose.position.set(0.12, 1.53, 0);
    figure.add(body, head, nose);
    return figure;
  }

  function humanoid(model) {
    const h = hello.humanoids && hello.humanoids[model];
    if (!h) return null;
    return humanoids[model] ||= Promise.all([loader.loadAsync(h.model), fetch(h.motion).then((r) => r.json())])
      .then(([gltf, motion]) => ({ model: gltf.scene, motion }));
  }

  function pose(p, quaternions) { // each bone of the person's humanoid to its rotation (x, y, z, w)
    p.bones.forEach((bone, j) => { if (bone) bone.quaternion.fromArray(quaternions[j]); });
  }

  // A person: a plain figure at once, and Habitat's humanoid for their avatar once it has loaded. The promise is
  // settled when the person is as they will look.
  const shadowTexture = (() => { // a soft dark disc: where a person stands
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d"), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, "rgba(0, 0, 0, 0.45)");
    r.addColorStop(1, "rgba(0, 0, 0, 0)");
    g.fillStyle = r;
    g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  })();

  function person(a, isPlayer, k) {
    const group = new THREE.Group();
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 0.8),
      new THREE.MeshBasicMaterial({ map: shadowTexture, transparent: true, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = 0.01;
    group.add(shadow);
    const p = { group, x: a.x, z: a.z, yaw: a.yaw, figure: plainFigure(isPlayer ? PLAYER : RESIDENTS[k % RESIDENTS.length]),
      bones: null, motion: null, phase: 0, walking: false };
    group.add(p.figure);
    if (!isPlayer) { // as in PUBG, the player's own figure carries no name
      const tagged = label(a.actor);
      tagged.position.y = 1.85;
      group.add(tagged);
    }
    scene.add(group);
    const asset = humanoid(a.model);
    p.ready = !asset ? Promise.resolve() : asset.then(({ model, motion }) => {
      const body = cloneSkinned(model);
      body.rotation.y = Math.PI / 2; // Habitat's humanoids face +z; VESPER's figures face +x
      body.traverse((o) => { if (o.isSkinnedMesh) o.frustumCulled = false; }); // its bounds are the rest pose's
      group.remove(p.figure);
      group.add(body);
      p.figure = body;
      p.motion = motion;
      p.bones = motion.joints.map((name) => body.getObjectByName(name) || null);
      pose(p, motion.stop);
    }).catch(() => {}); // the plain figure stays
    return p;
  }

  hello.actors.forEach((a, k) => { actors[a.actor] = person(a, a.actor === hello.player, k); });
  const people = Promise.all(Object.values(actors).map((p) => p.ready));

  if (hello.world3d) await loadScene();
  else buildFromMap();
  await people;
  status.ready = true;
  onProgress(status);

  // -- each frame --------------------------------------------------------------------------------------
  const ray = new THREE.Raycaster(), back = new THREE.Raycaster();
  ray.far = PICK_M;
  let distance = BACK_M, lastProbe = 0;

  function targetOf(obj) {
    for (let o = obj; o; o = o.parent) if (o.userData.target) return { target: o.userData.target, kind: o.userData.kind };
    return null;
  }

  const blend = new THREE.Quaternion();
  function animate(p, moved, dt) { // Habitat's walk while moving, its standing pose when still
    const walking = dt > 0 && moved / dt > WALKING_MS;
    if (!walking) {
      if (p.walking) pose(p, p.motion.stop);
      p.walking = false;
      return;
    }
    p.walking = true;
    p.phase = walkPhase(p.phase, moved, p.motion.cycle_m);
    const walk = p.motion.walk, [k0, k1, t] = frameAt(p.phase, walk.length);
    p.bones.forEach((bone, j) => {
      if (bone) bone.quaternion.fromArray(walk[k0][j]).slerp(blend.fromArray(walk[k1][j]), t);
    });
  }

  function update(state, cam, dt) {
    const k = 1 - Math.exp(-12 * dt); // people glide to where the session put them
    for (const a of Object.values(state.actors)) {
      const p = actors[a.actor] ||= person(a, a.actor === state.player, Object.keys(actors).length);
      const x0 = p.x, z0 = p.z;
      p.x += (a.x - p.x) * k;
      p.z += (a.z - p.z) * k;
      p.group.position.set(p.x, floorY, p.z);
      p.group.rotation.y = (-a.yaw * Math.PI) / 180;
      if (p.bones) animate(p, Math.hypot(p.x - x0, p.z - z0), dt);
    }
    for (const [id, door] of Object.entries(doors)) { // a door swings open and shut over SWING_S
      const e = state.effects[id] || {}, want = e.open ? SWING : 0, step = (SWING / SWING_S) * dt;
      door.angle += Math.max(-step, Math.min(step, want - door.angle));
      door.pivot.rotation.y = door.angle;
      if (door.pivot.children[0] && door.pivot.children[0].material) {
        door.pivot.children[0].material.color.setHex(e.locked ? LOCKED : DOOR); // only the plain leaves
      }
    }
    for (const [name, dev] of Object.entries(devices)) {
      const d = state.devices[name], e = state.effects[name] || {}, m = dev.marker.material;
      const seen = d && d.state && (d.state.occupancy === true || d.state.presence === true);
      if ("locked" in e) m.color.setHex(e.locked ? LOCKED : 0x3fb37f);
      m.emissive.copy(seen ? new THREE.Color(SEEN) : m.color);
      m.emissiveIntensity = e.on === true ? 2.2 : seen ? 1.2 : e.on === false ? 0.05 : 0.3;
      dev.marker.scale.setScalar(seen ? 1.5 : 1);
      if (dev.light) dev.light.intensity = e.on === true ? 4 : 0;
    }
    const me = actors[state.player];
    if (!me) return;
    me.group.visible = cam.mode !== "first";
    const now = performance.now();
    if (cam.mode === "third" && now - lastProbe > 80) { // a wall behind pulls the camera in
      lastProbe = now;
      const pose = cameraPose({ x: me.x, z: me.z, floorY, yaw: cam.yaw, pitch: cam.pitch, mode: "third" });
      const from = new THREE.Vector3(me.x, floorY + PIVOT_M + RAISE_M, me.z);
      const toward = new THREE.Vector3(...pose.position).sub(from).normalize();
      back.set(from, toward);
      back.far = BACK_M + 0.3;
      const hit = back.intersectObjects(solids, true)[0];
      distance = pullIn(BACK_M, hit ? hit.distance : Infinity);
    }
    const pose = cameraPose({ x: me.x, z: me.z, floorY, yaw: cam.yaw, pitch: cam.pitch, mode: cam.mode, distance });
    camera.position.set(...pose.position);
    camera.lookAt(...pose.target);
  }

  return {
    status, camera,
    floorY,
    frame(state, cam, dt) {
      update(state, cam, dt);
      renderer.render(scene, camera);
    },
    resize() {
      const w = container.clientWidth || 1, h = container.clientHeight || 1;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    },
    pick() { // the device or door under the crosshair, unless a wall is nearer
      ray.setFromCamera(new THREE.Vector2(0, 0), camera);
      for (const hit of ray.intersectObjects([...pickables, ...solids], true)) {
        const t = targetOf(hit.object);
        if (t) return t;
        return null; // a wall or the floor is in the way
      }
      return null;
    },
    pointOf(target) { // where the crosshair should be to use `target`
      if (devices[target]) return devices[target].marker.position.toArray();
      const dw = hello.map.doorways.find((d) => d.id === target);
      return dw ? [dw.center[0], floorY + 1.1, dw.center[1]] : null;
    },
    colors() { // how many colours the 3D view shows (1 is a blank view)
      const c = document.createElement("canvas");
      c.width = 64;
      c.height = 40;
      const ctx = c.getContext("2d");
      ctx.drawImage(renderer.domElement, 0, 0, 64, 40);
      const data = ctx.getImageData(0, 0, 64, 40).data, seen = new Set();
      for (let k = 0; k < data.length; k += 4) seen.add(data[k] << 16 | data[k + 1] << 8 | data[k + 2]);
      return seen.size;
    },
  };
}
```

- [ ] **Step 4: The page: 3D first, the map as the minimap, the crosshair, the compass, V and M**

In `vesper/lab/ui/index.html`, replace:
```html
  <link rel="stylesheet" href="style.css">
</head>
```
with:
```html
  <link rel="stylesheet" href="style.css">
  <script type="importmap">{"imports": {"three": "./vendor/three/build/three.module.min.js", "three/addons/": "./vendor/three/examples/jsm/"}}</script>
</head>
```

In `vesper/lab/ui/index.html`, replace:
```html
  <main>
    <section id="map-panel" class="panel" tabindex="0" aria-label="Map of the home">
      <canvas id="map"></canvas>
```
with:
```html
  <main>
    <section id="map-panel" class="panel" tabindex="0" aria-label="The home: the 3D view and the map">
      <div id="view3d" class="view3d" hidden></div>
      <div id="compass" class="compass" hidden aria-hidden="true"></div>
      <div id="crosshair" class="crosshair" hidden aria-hidden="true"></div>
      <div id="prompt3d" class="prompt3d" aria-live="polite"></div>
      <div id="loading" class="loading" hidden></div>
      <canvas id="map"></canvas>
```

In `vesper/lab/ui/index.html`, replace:
```html
  </main>
  <footer>WASD or arrows: walk · Shift: run · click a device or a door, or E or F: use it · Q: next thing in reach ·
    Tab: next panel</footer>
```
with:
```html
  </main>
  <footer id="keys">WASD or arrows: walk · Shift: run · click a device or a door, or E or F: use it · Q: next thing in reach ·
    Tab: next panel</footer>
```

Append to `vesper/lab/ui/style.css`:
```css
/* The 3D view (plan W1b): the main screen, with the map as a minimap in the corner (M: the full map), PUBG's
   compass at the top, and the crosshair with its prompt at the centre. */
.view3d { position: absolute; inset: 0; cursor: crosshair; }
.view3d canvas { display: block; }
body.mode-3d #map-panel > canvas#map { position: absolute; right: 12px; bottom: 12px; width: 220px; height: 220px;
  border: 2px solid rgba(230, 233, 239, 0.7); border-radius: 6px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.45); }
body.mode-3d #map-panel > canvas#map.full { inset: 0; width: 100%; height: 100%; border: 0; border-radius: 0;
  box-shadow: none; }
body.mode-3d .hint { bottom: 12px; }
body.mode-3d .toast { top: 44px; }
.compass { position: absolute; top: 8px; left: 50%; transform: translateX(-50%); width: 46%; height: 28px;
  overflow: hidden; background: rgba(18, 22, 31, 0.55); border-radius: 4px; pointer-events: none; }
.compass span { position: absolute; top: 5px; transform: translateX(-50%); font-size: 12px; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.compass span.point { color: var(--text); font-weight: 700; }
.compass::after { content: ""; position: absolute; left: 50%; bottom: 0; width: 2px; height: 7px;
  background: var(--warn); transform: translateX(-50%); }
.crosshair { position: absolute; left: 50%; top: 50%; width: 18px; height: 18px; transform: translate(-50%, -50%);
  pointer-events: none; }
.crosshair::before, .crosshair::after { content: ""; position: absolute; background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 0 2px rgba(0, 0, 0, 0.9); }
.crosshair::before { left: 8px; top: 0; width: 2px; height: 18px; }
.crosshair::after { left: 0; top: 8px; width: 18px; height: 2px; }
.crosshair.on::before, .crosshair.on::after { background: var(--warn); }
.prompt3d { position: absolute; left: 50%; top: calc(50% + 22px); transform: translateX(-50%); padding: 4px 10px;
  border-radius: 4px; background: rgba(18, 22, 31, 0.8); font-size: 14px; white-space: nowrap; pointer-events: none; }
.prompt3d:empty { display: none; }
.loading { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); padding: 10px 16px;
  border-radius: 6px; background: rgba(18, 22, 31, 0.85); font-size: 14px; }
```

In `vesper/lab/ui/app.js`, replace:
```javascript
// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// It plays like PUBG (spec §2a), map only until the 3D view (plan W1): WASD walks (Shift runs), a click on a device
// or a door uses it (E or F: the nearest in reach), the prompt by the pointer says what a click does or why not,
// the event feed shows what just happened, and the residents list who is doing what. There is no click-to-walk.
// `window.vesper` exposes the page's state to the visual checks (`vesper lab look`).
import {
  colorFor, decodeGrid, feedLine, fitView, formatClock, glyphFor, gridBounds, headingName, hintFor, hitTest, moveFor,
  promptFor, recordLine, stepInterval, tipPosition, walkKey,
} from "./mapview.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const LOG_MAX = 300;
const FEED_MAX = 5, FEED_S = 8; // the event feed keeps this many lines, each this many seconds

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0, feed: [], prompt: "",
  walkTo: (x, z) => send({ type: "walk_to", x, z }), // the looks' scripted walk; play itself has no click-to-walk
  toScreen: (x, z) => { // page coordinates of a world point, for clicks
```
with:
```javascript
// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// It plays like PUBG (spec §2a). The 3D view is the main screen (plan W1b): third person (V: first person), mouse
// look with the pointer captured (a click on the view captures it, Esc lets it go), WASD walks where the camera
// looks (Shift runs), and a click or F uses the device or door under the crosshair, whose prompt says what a click
// does or why not. The map is the minimap in the corner and, with M, the full map; a click on it uses what is under
// the pointer. A browser without WebGL, or `?view=map`, plays on the map alone. The event feed shows what just
// happened and the residents list who is doing what. There is no click-to-walk. `window.vesper` exposes the page's
// state to the visual checks (`vesper lab look`).
import {
  centerView, colorFor, decodeGrid, feedLine, fitView, formatClock, glyphFor, gridBounds, headingName, hintFor,
  hitTest, moveFor, promptFor, recordLine, stepInterval, tipPosition, walkKey,
} from "./mapview.js";
import { PITCH_MAX, aimCamera, compassMarks, moveRelative } from "./scene3d.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const LOG_MAX = 300;
const FEED_MAX = 5, FEED_S = 8; // the event feed keeps this many lines, each this many seconds
const MINI_PX = 220, MINI_SCALE = 14; // the minimap: its size, and pixels a metre
const LOOK_DEG = 0.15; // degrees the view turns per pixel of mouse movement
const KEYS_MAP = "WASD or arrows: walk · Shift: run · click a device or a door, or E or F: use it · Q: next thing in " +
  "reach · Tab: next panel";
const KEYS_3D = "Click the view to look around (Esc lets go) · WASD: walk · Shift: run · click or F: use what the " +
  "crosshair is on · V: first or third person · M: map · Q: next thing in reach · Tab: next panel";

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0, feed: [], prompt: "",
  mode: "map", crosshair: null, // the 3D view's: what the crosshair is on ({target, kind}) or null
  walkTo: (x, z) => send({ type: "walk_to", x, z }), // the looks' scripted walk; play itself has no click-to-walk
  setView: (mode) => setMode(mode), // "map" or "3d": the looks' choice; play picks 3D when the browser has WebGL
  aim: (target) => aim(target), // the looks' aim: turn the view so the crosshair is on a device or door
  camera: () => ({ ...cam }),
  view3d: () => (view3d ? { ...view3d.status } : null),
  view3dColors: () => (view3d ? view3d.colors() : 0),
  toScreen: (x, z) => { // page coordinates of a world point, for clicks
```

In `vesper/lab/ui/app.js`, replace:
```javascript
let sprint = false, hover = null, feedShown = ""; // the target under the pointer; the feed's lines on the page
```
with:
```javascript
let sprint = false, hover = null, feedShown = ""; // the target under the pointer; the feed's lines on the page
let mode = "map", fullMap = false, view3d = null, view3dTried = false, locked = false, lastFrame = 0, lastPick = 0;
const cam = { yaw: 0, pitch: -10, mode: "third" }; // the 3D view's camera: where the player looks
const params = new URLSearchParams(location.search || "");
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  for (const r of h.records || []) onRecord(r, false); // what happened before this page joined: no feed
  layout();
  updateHint();
  updateWhere();
  drawResidents();
}
```
with:
```javascript
  for (const r of h.records || []) onRecord(r, false); // what happened before this page joined: no feed
  const me = state.actors[state.player];
  if (me) cam.yaw = me.yaw;
  setMode(params.get("view") === "map" || typeof WebGL2RenderingContext === "undefined" ? "map" : "3d");
  updateHint();
  updateWhere();
  drawResidents();
}

// -- the 3D view ----------------------------------------------------------------------------------------
function setMode(next) {
  mode = next === "3d" ? "3d" : "map";
  vesper.mode = mode;
  document.body.classList.toggle("mode-3d", mode === "3d");
  for (const id of ["view3d", "compass", "crosshair"]) $(id).hidden = mode !== "3d";
  $("keys").textContent = mode === "3d" ? KEYS_3D : KEYS_MAP;
  fullMap = false;
  canvas.classList.toggle("full", false);
  if (mode === "map") $("prompt3d").textContent = "";
  if (mode === "3d" && !view3dTried) start3d();
  layout();
}

async function start3d() {
  view3dTried = true;
  const box = $("loading"), w = state.hello.world3d;
  box.hidden = false;
  box.textContent = w ? `Loading the scene's ${w.items} meshes…` : "Building the home from its map…";
  try {
    const { createView3d } = await import("./view3d.js");
    view3d = await createView3d($("view3d"), state.hello, (s) => {
      box.textContent = `Loading the scene: ${s.loaded + s.failed} of ${s.total} meshes`;
    });
    view3d.resize();
    box.hidden = true;
    const s = view3d.status;
    if (s.failed) toast(`${s.failed} of the scene's ${s.total} files could not be loaded (${s.missing[0]}${s.failed > 1 ? ", …" : ""})`, true);
  } catch (e) {
    box.hidden = true;
    toast(`The 3D view could not start (${e.message}); the map works`, true);
    view3d = null;
    setMode("map");
  }
}

function aim(target) {
  const me = state && state.actors[state.player];
  const point = view3d && view3d.pointOf(target);
  if (!me || !point) return null;
  Object.assign(cam, aimCamera({ x: me.x, z: me.z, floorY: view3d.floorY, mode: cam.mode }, point));
  lastPick = 0; // the crosshair is checked on the next frame
  return { ...cam };
}

function look(dx, dy) {
  cam.yaw = (cam.yaw + dx * LOOK_DEG) % 360;
  cam.pitch = Math.max(-PITCH_MAX, Math.min(PITCH_MAX, cam.pitch - dy * LOOK_DEG));
  updateWhere();
}

function crosshairTarget() { // what the crosshair is on, when that is in reach
  const c = vesper.crosshair;
  return c && state.reach.some((r) => r.target === c.target) ? c.target : null;
}

function useCrosshair() {
  const c = vesper.crosshair;
  if (!c) return;
  const d = state.devices[c.target];
  if (d && !d.operable) { toast(promptAt(c.target), true); return; } // a sensor: nothing for a hand to do
  const target = c.target;
  send({ type: "interact", target });
}

function updateCrosshair() {
  const c = view3d.pick(), before = vesper.crosshair;
  vesper.crosshair = c;
  const text = c ? promptAt(c.target) : "";
  $("prompt3d").textContent = text;
  $("crosshair").classList.toggle("on", Boolean(c));
  if (!hover) vesper.prompt = text;
  if ((before && before.target) !== (c && c.target)) updateHint();
}

let compassYaw = null;
function drawCompass() {
  if (compassYaw !== null && Math.abs(cam.yaw - compassYaw) < 0.25) return;
  compassYaw = cam.yaw;
  const bar = $("compass");
  bar.innerHTML = "";
  for (const m of compassMarks(cam.yaw, 120)) {
    const s = document.createElement("span");
    s.textContent = m.label;
    s.className = /^\d/.test(m.label) ? "" : "point";
    s.style.left = `${(0.5 + m.offset) * 100}%`;
    bar.append(s);
  }
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  const me = state.actors[state.player];
  $("where").textContent = me ? `${me.room || "between rooms"} · facing ${headingName(me.yaw)}` : "";
}
```
with:
```javascript
  const me = state.actors[state.player];
  $("where").textContent = me ? `${me.room || "between rooms"} · facing ${headingName(mode === "3d" ? cam.yaw : me.yaw)}` : "";
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  }
  const note = document.createElement("li");
  note.className = "muted";
  note.textContent = "The 3D view comes with the Habitat world service (plan W1).";
  list.append(note);
}
```
with:
```javascript
  }
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  selected %= reach.length;
  const more = reach.length > 1 ? ` · Q: next (${reach.length} in reach)` : "";
  $("hint").textContent = hintFor([reach[selected]], state.devices, state.effects) + more;
}
```
with:
```javascript
  selected %= reach.length;
  const aimed = mode === "3d" ? crosshairTarget() : null; // in 3D, E or F uses what the crosshair is on first
  const chosen = aimed ? reach.find((r) => r.target === aimed) : reach[selected];
  const more = reach.length > 1 && !aimed ? ` · Q: next (${reach.length} in reach)` : "";
  $("hint").textContent = hintFor([chosen], state.devices, state.effects) + more;
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  } else if (key === "e" || key === "f") { // F is PUBG's interact key
    const target = state.reach.length ? state.reach[selected % state.reach.length].target : null;
    send({ type: "interact", target });
  } else if (key === "q") {
    selected += 1;
    updateHint();
  }
```
with:
```javascript
  } else if (key === "e" || key === "f") { // F is PUBG's interact key
    const aimed = mode === "3d" ? crosshairTarget() : null;
    const target = aimed || (state.reach.length ? state.reach[selected % state.reach.length].target : null);
    send({ type: "interact", target });
  } else if (key === "q") {
    selected += 1;
    updateHint();
  } else if (key === "v" && mode === "3d") { // PUBG's first or third person
    cam.mode = cam.mode === "third" ? "first" : "third";
  } else if (key === "m" && mode === "3d") { // PUBG's full map
    fullMap = !fullMap;
    canvas.classList.toggle("full", fullMap);
    layout();
  }
```

In `vesper/lab/ui/app.js`, replace:
```javascript
function step() {
  const d = moveFor(held);
  if (!d) return;
```
with:
```javascript
function step() {
  const d = mode === "3d" ? moveRelative(held, cam.yaw) : moveFor(held);
  if (!d) return;
```

In `vesper/lab/ui/app.js`, replace:
```javascript
canvas.addEventListener("mouseleave", () => { hover = null; updatePrompt(); });
```
with:
```javascript
canvas.addEventListener("mouseleave", () => { hover = null; updatePrompt(); });

const view3dBox = $("view3d"); // a click captures the pointer for mouse look; a click while captured uses
view3dBox.addEventListener("click", () => { // what the crosshair is on
  if (mode !== "3d" || !view3d) return;
  if (!locked && view3dBox.requestPointerLock) { view3dBox.requestPointerLock(); return; }
  useCrosshair();
});
document.addEventListener?.("pointerlockchange", () => { locked = document.pointerLockElement === view3dBox; });
document.addEventListener?.("mousemove", (e) => { if (locked) look(e.movementX || 0, e.movementY || 0); });
```

In `vesper/lab/ui/app.js`, replace:
```javascript
// -- drawing ----------------------------------------------------------------------------------------
function layout() {
  const box = canvas.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(box.width * dpr);
  canvas.height = Math.round(box.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!state) return;
  view = fitView(gridBounds(state.hello.map.grid), box.width, box.height, 28);
  floor = drawFloor(box.width, box.height, dpr);
  dirty = true;
}

function drawFloor(width, height, dpr) {
  const g = state.hello.map.grid, off = document.createElement("canvas");
  off.width = Math.round(width * dpr);
```
with:
```javascript
// -- drawing ----------------------------------------------------------------------------------------
const mini = () => mode === "3d" && !fullMap; // the map is the minimap

function mapSize() { // the map canvas's size on the page, in CSS pixels
  if (mini()) return { width: MINI_PX, height: MINI_PX };
  const box = canvas.parentElement.getBoundingClientRect();
  return { width: box.width, height: box.height };
}

function layout() {
  const { width, height } = mapSize(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (view3d) view3d.resize();
  if (!state) return;
  view = fitView(gridBounds(state.hello.map.grid), width, height, 28);
  floor = drawFloor(width, height, dpr);
  dirty = true;
}

function followView() { // the minimap keeps the player in its middle
  const me = state.actors[state.player], { width, height } = mapSize();
  return me ? centerView(me.x, me.z, width, height, MINI_SCALE) : view;
}

function drawFloor(width, height, dpr) {
  const g = state.hello.map.grid, off = document.createElement("canvas");
  if (mini()) { // the whole floor at the minimap's scale, drawn shifted each frame
    const b = gridBounds(g), full = fitView(b, (b.maxX - b.minX) * MINI_SCALE, (b.maxZ - b.minZ) * MINI_SCALE, 0);
    return drawFloorWith(full, (b.maxX - b.minX) * MINI_SCALE, (b.maxZ - b.minZ) * MINI_SCALE, dpr, off);
  }
  return drawFloorWith(view, width, height, dpr, off);
}

function drawFloorWith(at, width, height, dpr, off) {
  const g = state.hello.map.grid, view = at;
  off.width = Math.round(width * dpr);
```

In `vesper/lab/ui/app.js`, replace:
```javascript
    c.textAlign = "left";
    c.fillText(room.label.split("/")[0], lx + 8, lz + 18);
    c.fillStyle = "#d9d3c7";
```
with:
```javascript
    c.textAlign = "left";
    if (!mini()) c.fillText(room.label.split("/")[0], lx + 8, lz + 18); // the minimap is too small for names
    c.fillStyle = "#d9d3c7";
```

In `vesper/lab/ui/app.js`, replace:
```javascript
function draw() {
  const { width, height } = canvas.parentElement.getBoundingClientRect();
  ctx.fillStyle = "#12161f";
  ctx.fillRect(0, 0, width, height);
  if (!state || !view) return;
  ctx.drawImage(floor, 0, 0, width, height);
  const reachable = new Set(state.reach.map((r) => r.target));
```
with:
```javascript
function draw() {
  const { width, height } = mapSize();
  ctx.fillStyle = "#12161f";
  ctx.fillRect(0, 0, width, height);
  if (!state || !view) return;
  if (mini()) {
    view = followView();
    const b = gridBounds(state.hello.map.grid), [x0, z0] = view.toScreen(b.minX, b.minZ);
    ctx.drawImage(floor, x0, z0, (b.maxX - b.minX) * MINI_SCALE, (b.maxZ - b.minZ) * MINI_SCALE);
  } else {
    ctx.drawImage(floor, 0, 0, width, height);
  }
  const reachable = new Set(state.reach.map((r) => r.target));
```

In `vesper/lab/ui/app.js`, replace:
```javascript
    state.hello.clock.t + (performance.now() - helloAt) / 1000);
  if (!vesper.ready) vesper.ready = true;
}
```
with:
```javascript
    state.hello.clock.t + (performance.now() - helloAt) / 1000);
}
```

In `vesper/lab/ui/app.js`, replace:
```javascript

const deviceRadius = () => Math.max(8, 0.16 * view.scale);
```
with:
```javascript

const deviceRadius = () => (mini() ? 4 : Math.max(8, 0.16 * view.scale));
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, x, z + 0.5);
  ctx.textBaseline = "alphabetic";
  ctx.font = "11px system-ui, sans-serif";
  label(lock ? `${d.name} · ${d.effect.locked ? "locked" : "unlocked"}` : d.name, x, z + r + 13);
  if (inReach) ring(x, z, r + 10, isChosen);
```
with:
```javascript
  ctx.textBaseline = "middle";
  if (!mini()) ctx.fillText(glyph, x, z + 0.5);
  ctx.textBaseline = "alphabetic";
  ctx.font = "11px system-ui, sans-serif";
  if (!mini()) label(lock ? `${d.name} · ${d.effect.locked ? "locked" : "unlocked"}` : d.name, x, z + r + 13);
  if (inReach) ring(x, z, r + 10, isChosen);
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  ctx.textAlign = "center";
  label(isPlayer ? "you" : a.actor, x, z - r - 5);
}

window.addEventListener("resize", layout);
(function loop() {
  if (dirty) { dirty = false; draw(); }
  requestAnimationFrame(loop);
```
with:
```javascript
  ctx.textAlign = "center";
  if (!mini()) label(isPlayer ? "you" : a.actor, x, z - r - 5);
}

window.addEventListener("resize", layout);
(function loop() {
  const now = performance.now();
  if (mode === "3d" && view3d && state) {
    view3d.frame(state, cam, Math.min(0.1, (now - (lastFrame || now)) / 1000));
    if (now - lastPick > 100) { lastPick = now; updateCrosshair(); }
    drawCompass();
    dirty = true; // the minimap follows the player every frame
  }
  lastFrame = now;
  if (dirty) {
    dirty = false;
    draw();
    if (!vesper.ready && state && view && (mode === "map" || (view3d && view3d.status.ready))) vesper.ready = true;
  }
  requestAnimationFrame(loop);
```

- [ ] **Step 5: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `6 passed`

Run: `for f in vesper/lab/ui/*.js; do node --check $f || echo BAD $f; done; echo checked`

Expected: `checked`, and no `BAD` line

- [ ] **Step 6: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/ui/view3d.js vesper/lab/ui/app.js vesper/lab/ui/index.html vesper/lab/ui/style.css tests/lab/ui/app.test.mjs tests/lab/test_ui.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the 3D view: the scene or the map in three.js, Habitat's humanoids, the crosshair, first and third person, the minimap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: The looks, the live checks, and the docs

**Files:**
- Modify: `vesper/lab/visual.py` (`aim`, `view`; the 3D view is drawn), `tests/lab/test_visual.py`
- Create: `examples/lab/test-house/look-3d.yaml`; Modify: `look-pubg.yaml`, `look-hub.yaml` (`{view: map}` first)
- Modify: `tests/lab/test_look_browser.py` (the map steps ask for the map; a 3D look), `tests/lab/test_world_export_live.py` (the render manifest; the humanoids' walks)
- Modify: `README.md`, `docs/superpowers/specs/2026-10-24-vesper-lab-design.md` (§3: the renderer decided; §10)

- [ ] **Step 1: Write the failing tests**

In `tests/lab/test_visual.py`, replace:
```python
from vesper.lab.png import Image, decode, encode
from vesper.lab.visual import CONNECTED, READY, LookError, load_steps, run_look
```
with:
```python
from vesper.lab.png import Image, decode, encode
from vesper.lab.visual import CONNECTED, READY, VIEW3D_COLOURS, VIEW3D_READY, LookError, load_steps, run_look
```

In `tests/lab/test_visual.py`, replace:
```python
    ("- {shot: '../escape'}", "step 1: a shot's name"),
    ("shot: a", "a list of steps"),
```
with:
```python
    ("- {shot: '../escape'}", "step 1: a shot's name"),
    ("- {aim: [1, 2]}", "step 1: aim needs the name of a device or door"),
    ("- {view: top}", "step 1: view is map or 3d"),
    ("shot: a", "a list of steps"),
```

In `tests/lab/test_visual.py`, replace:
```python
    assert ("evaluate", "vesper.walkTo(1.5, 4.5)") in b.calls and not [c for c in b.calls if c[0] == "click"]
```
with:
```python
    assert ("evaluate", "vesper.walkTo(1.5, 4.5)") in b.calls and not [c for c in b.calls if c[0] == "click"]


def test_aim_turns_the_view_onto_a_device_and_fails_for_one_the_view_does_not_have(tmp_path):
    b = FakeBrowser({'vesper.aim("kitchen_plug")': {"yaw": -30, "pitch": -12, "mode": "third"},
                     'vesper.aim("nowhere")': None})
    report = run_look(b, "http://x/", [{"aim": "kitchen_plug"}, {"aim": "nowhere"}, {"shot": "never"}], tmp_path)
    assert ("evaluate", 'vesper.aim("kitchen_plug")') in b.calls
    failed = [c for c in report.checks if not c.ok]
    assert [c.name for c in failed] == ["aim at nowhere", "step 3"] and "no device or door" in failed[0].detail
    assert report.shots == [] and failed[1].detail == "skipped after a failed step"


def test_view_switches_to_3d_waits_for_it_and_checks_it_is_drawn(tmp_path):
    b = FakeBrowser({VIEW3D_READY: True, VIEW3D_COLOURS: 30})
    report = run_look(b, "http://x/", [{"view": "3d"}, {"view": "map"}], tmp_path)
    assert ("evaluate", 'vesper.setView("3d")') in b.calls and ("evaluate", 'vesper.setView("map")') in b.calls
    assert ("wait_for", VIEW3D_READY, 90) in b.calls
    names = [c.name for c in report.checks if c.ok]
    assert "the 3D view is ready" in names and "the 3D view is drawn" in names and report.ok


def test_a_page_that_starts_in_3d_must_draw_it(tmp_path):
    blank = run_look(FakeBrowser({VIEW3D_COLOURS: 1}), "http://x/", [], tmp_path / "a")
    assert [c.name for c in blank.checks if not c.ok] == ["the 3D view is drawn"]
    on_map = run_look(FakeBrowser({VIEW3D_COLOURS: None}), "http://x/", [], tmp_path / "b")
    assert on_map.ok and "the 3D view is drawn" not in [c.name for c in on_map.checks]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_visual.py -q 2>&1 | tail -1`

Expected: `1 error`: `ImportError: cannot import name 'VIEW3D_COLOURS' from 'vesper.lab.visual'`

- [ ] **Step 3: The look steps**

In `vesper/lab/visual.py`, replace:
```python
    - {walk: [1.5, 4.5]}                                      # the player walks there along the planned path
    - {wait: 0.5}                                             # seconds
    - {expect: "vesper.state.effects.bedroom_lamp.on", label: "the lamp is on"}
    - {shot: lamp_on}                                         # a screenshot, lamp_on.png

Every look also checks that the page got ready, that the map is drawn, that no shot is blank, that each shot
matches its reference image when references are given, that the page stayed connected to the session, that the
```
with:
```python
    - {walk: [1.5, 4.5]}                                      # the player walks there along the planned path
    - {view: 3d}                                              # the 3D view (or map); it waits until it is drawn
    - {aim: kitchen_plug}                                     # turn the 3D view so the crosshair is on it
    - {wait: 0.5}                                             # seconds
    - {expect: "vesper.state.effects.bedroom_lamp.on", label: "the lamp is on"}
    - {shot: lamp_on}                                         # a screenshot, lamp_on.png

Every look also checks that the page got ready, that the map is drawn (and the 3D view, when the page shows it),
that no shot is blank, that each shot
matches its reference image when references are given, that the page stayed connected to the session, that the
```

In `vesper/lab/visual.py`, replace:
```python
CONNECTED = "window.vesper !== undefined && window.vesper.connected === true"
ACTIONS = ("shot", "press", "click_world", "walk", "wait", "until", "expect")
OPTIONS = {"press": {"times"}, "until": {"timeout", "label"}, "expect": {"label"}}
```
with:
```python
CONNECTED = "window.vesper !== undefined && window.vesper.connected === true"
VIEW3D_READY = "vesper.view3d() !== null && vesper.view3d().ready === true"
VIEW3D_COLOURS = "vesper.mode === '3d' ? vesper.view3dColors() : null"  # null: the page plays on the map
VIEW3D_TIMEOUT_S = 90
ACTIONS = ("shot", "press", "click_world", "walk", "wait", "until", "expect", "aim", "view")
OPTIONS = {"press": {"times"}, "until": {"timeout", "label"}, "expect": {"label"}}
```

In `vesper/lab/visual.py`, replace:
```python
        raise LookError(f"step {k}: {action} needs a JavaScript expression")
```
with:
```python
        raise LookError(f"step {k}: {action} needs a JavaScript expression")
    if action == "aim" and not (isinstance(value, str) and value.strip()):
        raise LookError(f"step {k}: aim needs the name of a device or door, such as kitchen_plug")
    if action == "view" and value not in ("map", "3d"):
        raise LookError(f"step {k}: view is map or 3d")
```

In `vesper/lab/visual.py`, replace:
```python
    checks.append(Check("the map is drawn", colours >= MIN_COLOURS, f"{colours} colours on the map"))
    for k, step in enumerate(steps, 1):
```
with:
```python
    checks.append(Check("the map is drawn", colours >= MIN_COLOURS, f"{colours} colours on the map"))
    _check_3d(browser, checks)
    for k, step in enumerate(steps, 1):
```

In `vesper/lab/visual.py`, replace:
```python
        checks.append(Check(step.get("label", step["expect"]), bool(value), json.dumps(value)))
    elif "shot" in step:
        _shot(browser, step["shot"], report, golden, update_golden, tolerance, max_diff)
    return True
```
with:
```python
        checks.append(Check(step.get("label", step["expect"]), bool(value), json.dumps(value)))
    elif "aim" in step:
        if browser.evaluate(f"vesper.aim({json.dumps(step['aim'])})") is None:
            checks.append(Check(f"aim at {step['aim']}", False, "no device or door of that name in the 3D view"))
            return False
    elif "view" in step:
        browser.evaluate(f"vesper.setView({json.dumps(step['view'])})")
        if step["view"] == "3d":
            try:
                browser.wait_for(VIEW3D_READY, timeout=VIEW3D_TIMEOUT_S)
            except BrowserError as e:
                checks.append(Check("the 3D view is ready", False, str(e)))
                return False
            checks.append(Check("the 3D view is ready", True))
            _check_3d(browser, checks)
    elif "shot" in step:
        _shot(browser, step["shot"], report, golden, update_golden, tolerance, max_diff)
    return True


def _check_3d(browser, checks) -> None:
    """When the page shows the 3D view, that it is drawn: not one flat colour."""
    colours = browser.evaluate(VIEW3D_COLOURS)
    if colours is not None:
        checks.append(Check("the 3D view is drawn", colours >= MIN_COLOURS, f"{colours} colours in the 3D view"))
```

Run: `.venv/bin/python -m pytest tests/lab/test_visual.py -q 2>&1 | tail -1`

Expected: `21 passed`

- [ ] **Step 4: The example looks and the opt-in checks**

Create `examples/lab/test-house/look-3d.yaml`:
```yaml
# vesper lab look examples/lab/test-house/home.yaml --steps examples/lab/test-house/look-3d.yaml
# The 3D view (spec §2a, plan W1b), drawn from the house's map: third person, the crosshair and its prompt, F, first
# person (V), and the full map (M).
- {until: "vesper.mode === '3d'", label: "the page opens on the 3D view"}
- {shot: start}
- {walk: [4.3, 0.9]}
- {until: "vesper.state.reach.some(r => r.target === 'kitchen_plug')", label: "the plug comes into reach", timeout: 15}
- {aim: kitchen_plug}
- {wait: 0.5}
- {expect: "vesper.crosshair && vesper.crosshair.target === 'kitchen_plug'", label: "the crosshair is on the plug"}
- {expect: "vesper.prompt.startsWith('Click: switch kitchen_plug')", label: "the prompt says what a click does"}
- {shot: aimed}
- {press: f}
- {until: "vesper.lastResult && vesper.lastResult.target === 'kitchen_plug' && vesper.lastResult.ok === true",
   label: "F uses what the crosshair is on", timeout: 10}
- {press: v}
- {wait: 0.5}
- {expect: "vesper.camera().mode === 'first'", label: "V: first person"}
- {shot: first_person}
- {press: v}
- {press: m}
- {wait: 0.4}
- {shot: full_map}
```

In `examples/lab/test-house/look-pubg.yaml`, replace:
```yaml
# no click-to-walk, the event feed.
- {shot: start}
```
with:
```yaml
# no click-to-walk, the event feed.
- {view: map}  # these checks are of the map; the page opens on the 3D view (look-3d.yaml)
- {shot: start}
```

In `examples/lab/test-house/look-hub.yaml`, replace:
```yaml
# The hall's motion sensor turns the bedroom lamp on through Home Assistant's automation (automations.yaml).
- {shot: start}
```
with:
```yaml
# The hall's motion sensor turns the bedroom lamp on through Home Assistant's automation (automations.yaml).
- {view: map}  # these checks are of the map; the page opens on the 3D view (look-3d.yaml)
- {shot: start}
```

In `tests/lab/test_look_browser.py`, replace:
```python

It plays the bedroom scene and checks the page the way a person would see it: the lamp in reach, E lights it
(and the pixels around it get brighter), a click on the lamp switches it off again, and a scripted walk takes the
player into the hall (play itself walks by WASD only).
"""
```
with:
```python

It plays the bedroom scene and checks the page the way a person would see it. On the map: the lamp in reach, E
lights it (and the pixels around it get brighter), a click on the lamp switches it off again, and a scripted walk
takes the player into the hall (play itself walks by WASD only). In the 3D view (plan W1b): the crosshair aimed at
the lamp, F lights it, V turns to first person.
"""
```

In `tests/lab/test_look_browser.py`, replace:
```python
STEPS = [
    {"shot": "start"},
```
with:
```python
STEPS = [
    {"view": "map"},
    {"shot": "start"},
```

In `tests/lab/test_look_browser.py`, replace:
```python
    assert bluish(hall, *began) < 0.05  # and is gone from where it started
```
with:
```python
    assert bluish(hall, *began) < 0.05  # and is gone from where it started


STEPS_3D = [
    {"until": "vesper.mode === '3d'", "label": "the page opens on the 3D view"},
    {"aim": "bedroom_lamp"},
    {"wait": 0.3},
    {"expect": "vesper.crosshair && vesper.crosshair.target === 'bedroom_lamp'",
     "label": "the crosshair is on the lamp"},
    {"expect": "vesper.prompt.startsWith('Click: switch bedroom_lamp on')",
     "label": "the prompt says what a click does"},
    {"press": "f"},
    {"until": "vesper.state.effects.bedroom_lamp.on === true", "label": "F lights the lamp under the crosshair",
     "timeout": 10},
    {"press": "v"},
    {"expect": "vesper.camera().mode === 'first'", "label": "V turns to first person"},
    {"shot": "first_person"},
]


def test_a_real_browser_plays_the_home_in_3d(tmp_path):
    home = load_lab_home(write_home(tmp_path, IN_BEDROOM), catalog())
    session = Session(home, clock=WallClock(), trace=tmp_path / "trace.jsonl")
    session.start()
    server = PlayServer(session, port=0)
    server.start()
    try:
        with Browser.launch(width=1280, height=800, timeout=90) as browser:
            report = run_look(browser, server.url, STEPS_3D, tmp_path / "look", ready_timeout=90)
    finally:
        server.stop()
        session.stop()
    assert report.ok, [c for c in report.checks if not c.ok]
    assert any(c.name == "the 3D view is drawn" and c.ok for c in report.checks)
```

In `tests/lab/test_world_export_live.py`, replace:
```python
(VESPER_HABITAT=1), like the Matter host contract test. Scene 102344022 must be downloaded (`vesper lab scenes`
says ready); it takes about 10 s and 0.5 GB."""
import os
```
with:
```python
(VESPER_HABITAT=1), like the Matter host contract test. Scene 102344022 must be downloaded (`vesper lab scenes`
says ready); it takes about 10 s and 0.5 GB. With Habitat's humanoids downloaded, their motions are made too."""
import os
```

In `tests/lab/test_world_export_live.py`, replace:
```python
from vesper.lab.home import load_lab_home, spawn_point
from vesper.lab.scenes import habitat_python, hssd_dir, list_scenes
```
with:
```python
from vesper.lab.home import load_lab_home, spawn_point
from vesper.lab.humanoids import DEFAULT_HUMANOIDS, find_humanoids
from vesper.lab.scenes import habitat_python, hssd_dir, list_scenes
```

In `tests/lab/test_world_export_live.py`, replace:
```python
    assert len(home.map.rooms) == 20 and len(home.map.doorways) >= 20
    assert home.map.room_at(*home.avatar["start"]).id == "living_room"
```
with:
```python
    assert len(home.map.rooms) == 20 and len(home.map.doorways) >= 20
    from vesper.lab.world3d import load_world3d
    w = load_world3d(home, HSSD)  # every mesh, placed by Habitat; the two doors swing with the home's doors
    assert len(w.manifest["items"]) == 219 and w.manifest["items"][0]["kind"] == "stage" and w.missing == []
    assert sorted(i["doorway"] for i in w.manifest["items"] if "doorway" in i) == ["bedroom-outdoor",
                                                                                "dining_room-outdoor"]
    assert home.map.room_at(*home.avatar["start"]).id == "living_room"
```

In `tests/lab/test_world_export_live.py`, replace:
```python
    assert w.pose("player").room == "bedroom"
```
with:
```python
    assert w.pose("player").room == "bedroom"


@pytest.mark.skipif(not (DEFAULT_HUMANOIDS / "female_0" / "female_0.glb").is_file(),
                    reason="Habitat's humanoids are not downloaded (data/humanoids/humanoid_data)")
def test_habitat_s_humanoids_walk_as_in_habitat(tmp_path):
    import json
    h, said = find_humanoids({"female_0", "male_1"}, DEFAULT_HUMANOIDS, cache=tmp_path)
    assert said == "people: Habitat's humanoids (female_0, male_1)"
    m = json.loads(h.models["female_0"]["motion"].read_text())
    assert len(m["joints"]) == 54 and m["joints"][0] == "left_hip" and m["fps"] == 30.0
    assert len(m["walk"]) == 33 and abs(m["cycle_m"] - 1.31) < 0.01  # Habitat's walk: 1.31 m a cycle
```

Run: `VESPER_UI_BROWSER=1 taskpolicy -b .venv/bin/python -m pytest tests/lab/test_look_browser.py -q 2>&1 | tail -1`

Expected: `2 passed` in about 30 s (a headless Chromium; the 3D view under software rendering)

Run: `VESPER_HABITAT=1 taskpolicy -b .venv/bin/python -m pytest tests/lab/test_world_export_live.py -q 2>&1 | tail -1`

Expected: `2 passed` in about 10 s, in the working copy where scene 102344022 and Habitat's humanoids are downloaded

- [ ] **Step 5: The docs**

In `README.md`, replace:
```markdown
|---|---|---|
| Play a home in the browser: walk, use devices, watch sensors and residents | `vesper lab play` | the catalog |
| Put a real Home Assistant in the loop, with its dashboard in the page | `vesper lab play --hub` | `vesper setup ha`, Mosquitto |
```
with:
```markdown
|---|---|---|
| Play a home in the browser, in 3D as in a game: walk, use devices, watch sensors and residents | `vesper lab play` | the catalog |
| Put a real Home Assistant in the loop, with its dashboard in the page | `vesper lab play --hub` | `vesper setup ha`, Mosquitto |
```

In `README.md`, replace:
```markdown
  - a Chromium-based browser, for `vesper lab look`;
  - Miniconda with habitat-sim 0.3.3 and the HSSD dataset, for homes from real 3D scenes (see below).
```
with:
```markdown
  - a Chromium-based browser, for `vesper lab look`;
  - Miniconda with habitat-sim 0.3.3 and the HSSD dataset, for homes from real 3D scenes (see below);
  - Habitat's humanoids, so the people in a home are Habitat's own avatars (see below).
```

In `README.md`, replace:
```markdown
Your browser opens on http://127.0.0.1:18080/ (add `--no-browser` to only print the address). The page shows the
test house: a hall, a kitchen, a bedroom and a porch, with six real Zigbee products. Alex, a resident, lives there
on a weekday routine.

| Control | What it does |
|---|---|
| **W A S D** or the arrow keys | walk; hold two keys for a diagonal |
| **Shift** | run |
| **Click** a device or a door | use it: switch a plug, open a door, press a button |
| **E** or **F** | use the thing in reach (the nearest first) |
| **Q** | select the next thing in reach |
| **Tab** | move to the next panel |

As in a game, the prompt by the pointer says what a click will do ("Click: switch kitchen_plug on"), or why it
cannot ("too far (3.7 m)", "other side of the wall", "a sensor, not used by hand"). Clicking the floor does not
walk you there. The page also shows:
- in the header, the room you are in and the direction you face;
- top right, the event feed: a sensor seeing someone, a door, a resident's action, an automation;
```
with:
```markdown
Your browser opens on http://127.0.0.1:18080/ (add `--no-browser` to only print the address). The page shows the
test house in 3D: a hall, a kitchen, a bedroom and a porch, with six real Zigbee products. Alex, a resident, lives
there on a weekday routine. The test house has no 3D scene of its own, so it is drawn plainly from its map; a home
made from a real scene (below) is drawn from the scene itself.

| Control | What it does |
|---|---|
| **Click** the view | capture the mouse to look around (**Esc** lets it go) |
| **W A S D** or the arrow keys | walk where you look; hold two keys for a diagonal |
| **Shift** | run |
| **Click** or **F** | use what the crosshair is on: switch a plug, open a door, press a button |
| **E** | the same; with nothing under the crosshair, the thing in reach |
| **V** | first person or third person |
| **M** | the full map (a click on it uses a device or door too) |
| **Q** | select the next thing in reach |
| **Tab** | move to the next panel |

As in a game, the prompt under the crosshair says what a click will do ("Click: switch kitchen_plug on"), or why
it cannot ("too far (3.7 m)", "other side of the wall", "a sensor, not used by hand"). A browser without WebGL, or
the address with `?view=map`, plays on the map alone. The page also shows:
- in the header, the room you are in and the direction you face, and a compass at the top of the view;
- bottom right, the minimap;
- top right, the event feed: a sensor seeing someone, a door, a resident's action, an automation;
```

In `README.md`, replace:
```markdown
    vesper lab scenes                                          # the scenes here, and which are complete
    vesper lab new ~/vesper-homes/myhome --scene 102344022     # rooms, doorways and doors, in about 10 s
    vesper catalog search ikea bulb                            # products to place (vesper catalog show <id>)
```
with:
```markdown
    vesper lab scenes                                          # the scenes here, and which are complete
    vesper lab new ~/vesper-homes/myhome --scene 102344022     # rooms, doorways, doors and the 3D scene, in ~10 s
    vesper catalog search ikea bulb                            # products to place (vesper catalog show <id>)
```

In `README.md`, replace:
```markdown
- its walkable floor, with the furniture in it;
- its doors, closed. HSSD's own metadata says which openings are doors and which are windows.
```
with:
```markdown
- its walkable floor, with the furniture in it;
- its doors, closed. HSSD's own metadata says which openings are doors and which are windows;
- its 3D scene (`render.json`): every mesh the scene draws, placed exactly where Habitat places it. The page draws
  the scene from HSSD's own files, so `vesper lab play` needs HSSD where the home was made, or `--hssd`.

For the people, download Habitat's humanoids, in the Habitat environment:

    python -m habitat_sim.utils.datasets_download --uids habitat_humanoids --data-path data/

`vesper lab play` then draws each person as the Habitat humanoid their avatar names (`female_0`, `male_1`, …),
walking with Habitat's own walk; it looks for them in `data/humanoids/humanoid_data`, or `VESPER_HUMANOIDS`, and
draws plain figures without them.
```

In `README.md`, replace:
```markdown

`lab look` drives the page in a headless browser, step by step (walk, press, click, wait, expect). It takes
screenshots and writes a report.
```
with:
```markdown

`lab look` drives the page in a headless browser, step by step (walk, press, click, aim, view, wait, expect). It
takes screenshots and writes a report. `look-3d.yaml` checks the 3D view: the crosshair, F, first person, the map.
```

In `README.md`, replace:
```markdown
| `VESPER_HABITAT=1` | the world service in Habitat (scene 102344022 downloaded) |
| `VESPER_UI_BROWSER=1` | a real headless browser |
| `VESPER_STACK_TESTS=1` | a real Mosquitto |
```
with:
```markdown
| `VESPER_HABITAT=1` | the world service in Habitat (scene 102344022 downloaded) |
| `VESPER_UI_BROWSER=1` | a real headless browser (the map and the 3D view) |
| `VESPER_STACK_TESTS=1` | a real Mosquitto |
```

In `README.md`, replace:
```markdown

- **The 3D view:** third and first person, as in a game, with a minimap.
- **Guided setup:** `vesper setup habitat`, scene downloads, and `vesper doctor`.
```
with:
```markdown

- **Guided setup:** `vesper setup habitat`, scene downloads, and `vesper doctor`.
```

In `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, replace:
````markdown
```
 Browser (127.0.0.1:18080): map canvas | 3D view (streamed frames) | HA dashboard (iframe, :18123) | console
        ▲ WebSocket: world state, frames, device states, alerts      │ inputs, edits, console commands
        │                                                            ▼
 VESPER app  (Python ≥3.11, `vesper lab play`)
   game rules (§5) · twins runtime + Zigbee bridge + Matter tier · security lab (§6) · recorder
   stack: VESPER's Mosquitto :18830, Home Assistant :18123 (and Matter Server :15580 when Matter devices are placed)
        ▲ JSON lines over stdin/stdout (the Matter host's pattern)   │ avatar commands, door commands, render requests
        │ world events, avatar pose, door states, JPEG frames        ▼
 World service  (`vesper_world`, Habitat env: Python 3.9, habitat-sim + habitat-lab 0.3.3)
   HSSD scene (articulated) · navmesh · room regions · doors · humanoid avatar · offscreen third-person camera
```

- **One authority.** The world service owns the scene, the walkable floor (navmesh), the rooms (HSSD region annotations), the articulated doors, the avatar and its pose. The map and the 3D view are both views of the same state.
- **Two environments, as the device-twins spec requires.** Habitat needs Python 3.9 and VESPER needs at least 3.11. The app starts the world service as a child process from the Habitat environment and talks to it over JSON lines, as it already does with the Matter host.
  - The world service never touches the twins, the hub or the network.
  - The app never imports Habitat.
- **Device events stay on MQTT.** The twins, the bridge and Home Assistant keep the interfaces sub-project #1 tested. Device events are therefore observable on VESPER's broker as on a real Zigbee2MQTT home, which is what attacks and defenses act on (§6).
- **The map file.** It is generated once per scene by the world service and cached in the home directory. It holds the navmesh projected to the floor, the room polygons with names, the doors (id, polygon, hinge), and furniture footprints. The map view and the placement editor need only this file. It is derived from HSSD, so it stays on the user's machine and is never distributed (device-twins spec §5).
- **Rendering.** The world service renders a third-person RGB camera behind the avatar, offscreen, at 640×480. Frames are JPEG-encoded and streamed at the rate the browser takes, at least 10 per second (success criterion 3). Without a working renderer, the app runs with the map only and says so (risk R1).
````
with:
````markdown
```
 Browser (127.0.0.1:18080): 3D view (three.js) + minimap | HA dashboard (iframe, :18123) | console
        ▲ WebSocket: world state, device states, alerts              │ inputs, edits, console commands
        │ HTTP: the page, the render manifest, HSSD's meshes,        ▼
        │       Habitat's humanoids (only those the home uses)
 VESPER app  (Python ≥3.11, `vesper lab play`)
   map world (§5: floor, rooms, doors, reach) · game rules · twins runtime + Zigbee bridge + Matter tier ·
   security lab (§6) · recorder
   stack: VESPER's Mosquitto :18830, Home Assistant :18123 (and Matter Server :15580 when Matter devices are placed)
        ▲ files: map.json, openings.json, render.json; motions       │ `export` (once per home), `humanoids` (once)
        │                                                            ▼
 World service  (`vesper_world`, Habitat env: Python 3.9, habitat-sim 0.3.3), run as a child process
   HSSD scene (articulated) · navmesh · room regions · doors vs windows · every mesh's world matrix · humanoid motions
```

- **One authority for each thing.** Habitat is the authority on the scene: its walkable floor (navmesh), its rooms (HSSD region annotations), its doors, and where every mesh stands. The world service exports these once per home (plan W1a, W1b). At play time the app's map world, built from that export, is the authority on the state: where each person is, which doors are open, what is in reach. The map, the minimap and the 3D view are views of that one state.
- **Two environments, as the device-twins spec requires.** Habitat needs Python 3.9 and VESPER needs at least 3.11. The app runs the world service as a child process in the Habitat environment, with a copy of `vesper_world` alone on its path (nothing of VESPER's can shadow Habitat's packages), when it makes a home (`export`) and once for the humanoids (`humanoids`).
  - The world service never touches the twins, the hub or the network.
  - The app never imports Habitat.
- **Device events stay on MQTT.** The twins, the bridge and Home Assistant keep the interfaces sub-project #1 tested. Device events are therefore observable on VESPER's broker as on a real Zigbee2MQTT home, which is what attacks and defenses act on (§6).
- **The map file.** It is generated once per scene by the world service and cached in the home directory. It holds the navmesh projected to the floor, the room polygons with names, the doors (id, polygon, hinge), and furniture footprints. The map view and the placement editor need only this file. It is derived from HSSD, so it stays on the user's machine and is never distributed (device-twins spec §5).
- **Rendering (decided by the W1b spike, 2026-10-26).** The browser draws the 3D view with three.js 0.170.0 (vendored, MIT), from the scene's own files: HSSD's stage, objects and articulated parts, each placed with the world matrix Habitat gives it (`render.json`, written by `export`; the spike matched all 219 meshes of scene 102344022 to Habitat's placement within 5×10⁻⁷, and its render to Habitat's own, object for object). Streaming Habitat's frames was set aside: mouse look needs the browser's frame rate, a round trip per frame would lag, Habitat would have to run during play, and habitat-sim has no Windows build. The people are Habitat's humanoids (their skinned models, walking with Habitat's own walk motion, standing in its standing pose), or plain figures without them. A home without a scene is drawn from its map (floor, walls, doors). Without WebGL the page plays on the map alone and says so.
````

In `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, replace:
```markdown

The spikes, the world service and every live test wait for the end of the run and the PI's go. Plan D (Matter tier) is merged (2026-10-24, 5eb2aed). W1a (a home from a real HSSD scene through the world service) is merged (2026-10-26, 544477b); W1b, the 3D view, is next.
```
with:
```markdown

The spikes, the world service and every live test wait for the end of the run and the PI's go. Plan D (Matter tier) is merged (2026-10-24, 5eb2aed). W1a (a home from a real HSSD scene through the world service) is merged (2026-10-26, 544477b), and W1b (the 3D view: three.js from the scene's files placed by Habitat, Habitat's humanoids, PUBG's controls) after it. Next: W5 (setup), W4 (the security lab, Phantom-Delay first), W6 (the desktop app).
```

- [ ] **Step 6: The whole suite, lint and commit**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `853 passed, 20 skipped` in an export of `main` (the working copy, with its catalog: more pass, fewer skip)

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/visual.py tests/lab/test_visual.py examples/lab/test-house/look-3d.yaml examples/lab/test-house/look-pubg.yaml examples/lab/test-house/look-hub.yaml tests/lab/test_look_browser.py tests/lab/test_world_export_live.py README.md docs/superpowers/specs/2026-10-24-vesper-lab-design.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): looks aim and choose the view; the 3D view checked live; README and spec section 3 say how it is drawn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```



---

## After this plan

- **W5, setup:** `vesper setup habitat|scenes|humanoids`, `vesper doctor`, the released catalog; scenes downloaded one by one.
- **W4, the security lab** (PI 2026-10-26): any attack or defense as a scenario file, set up by hand or by a model that reads the paper; Phantom-Delay (Fu et al., DSN 2022) first, inside VESPER's own bridge.
- **W6, VESPER Studio for macOS and Windows** (spec §7).
- **Later in the 3D view:** the placement editor (W2d) on real homes, spectating a resident, replays with a free camera.
