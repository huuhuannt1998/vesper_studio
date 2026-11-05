# Scan and 3D-model import (part B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vesper lab import room.json|room.usdz|scan.glb|scan.obj|scan.ply HOMEDIR` turns an Apple RoomPlan export
(Polycam Room mode, 3D Scanner App) or any room/house mesh into a VESPER home whose 3D view is the model itself.

**Architecture:** Every source becomes a `vesper-plan/1` in metres plus an optional list of named objects, then goes
through part A's `rectify` → `check_house` → `generate_from_plan` unchanged. Structured RoomPlan files are read from
their own walls, doors, windows, openings and sections; any other mesh is sliced horizontally into an occupancy grid
whose free regions are rooms and whose gaps are doors (wall below and above) or windows (wall below and above, gap in
between). The model, turned into the plan's frame, becomes the home's `scene.glb`.

**Tech Stack:** Python 3.12; optional extra `scan` = `trimesh`, `numpy`, `usd-core` (for USDZ);
existing `vesper.lab.plan`, `generate`, `layoutcheck`.

**Spec:** `docs/superpowers/specs/2026-11-04-floor-plan-import-design.md`, part B (PI go 2026-11-04: "You can find
some polycam models datastets and test them out", RKA jrn_01M6MHGDF4BA44KGVQ4AS14WPV).

**Test set:** `.vesper/datasets/scans/` (git-ignored; MANIFEST.json, README.md, `_views/` renders). Tests that read
it skip when it is absent.

## Global Constraints

- Plan coordinates: x east, z south (image y down), metres; glTF +Y up maps to plan height. A source's up axis is
  detected, never assumed (Habitat GLBs are Z-up inside glTF).
- Scale: RoomPlan files are metres. A mesh is accepted as metres when its floor-to-ceiling (or, with no ceiling,
  its tallest wall band) is 2.1-4.0 and its doors 0.6-1.3 wide; otherwise it is rescaled by the factor that puts the
  ceiling at 2.5 m and reported, or refused with "the scale looks wrong: ... give --scale" when neither ceiling nor
  doors can be measured. Never guessed silently.
- Slicing heights above the detected floor: walls at 1.0 m, sills at 0.5 m, lintels at 2.15 m. Grid cell 0.05 m.
  Doorways: gap 0.6-1.6 m wide with wall below 0.1 m absent and lintel present (or ceiling); windows: wall present
  at 0.5 and above 2.15, absent at 1.0-1.6.
- Room labels: RoomPlan `sections` labels map to VESPER types (bedroom, livingRoom→living, kitchen, bathroom,
  diningRoom→dining, unidentified→other); a mesh's rooms are typed `other` and named "room 1..N" unless the user
  passes `--types` (a list) or edits them in the lobby; nothing is invented.
- Objects: RoomPlan categories map to VESPER furniture kinds (bed, sofa, chair, table, storage→cabinet,
  refrigerator, oven, stove, sink, toilet, bathtub, washerDryer, dishwasher, television, fireplace, stairs); they
  go into furniture.json with `"source": "scan"`; built-in furniture is added only to rooms with none.
- The imported model is the user's: it is copied to `<home>/scene.glb` (and the original to `<home>/source/`), and
  `<home>/.gitignore` lists both. Nothing from `.vesper/datasets/scans/` is ever committed; tests use hand-made
  fixtures.
- `trimesh`/`usd-core` are optional (`pip install vesper[scan]`); without them the importer says which extra to
  install. `.json` RoomPlan import needs neither.
- Never push without the PI's OK. Worktree agents first run `git merge --ff-only plan-w4b`.

## Review Focus

- A mesh with no ceiling and no measurable door (a dollhouse cut at 1 unit): refused with the scale message, nothing
  written (Task B3 test).
- A Z-up glTF: detected and turned, rooms found (Task B3 test with a rotated fixture).
- A RoomPlan room whose walls have different heights (1.59, 2.09, 2.42 m): ceiling taken as the tallest wall band,
  plan still built (Task B1 test).
- Outdoor ground planes far larger than the house (24 m "Green Floor"): the floor is the largest connected horizontal
  surface under walls, not the largest plane (Task B3 test).
- A scan whose geometry sprays through a window: the spill outside the walls is not a room (Task B3 test).

---

### Task B1: RoomPlan CapturedRoom/CapturedStructure JSON → vesper-plan/1

**Files:** Create `vesper/lab/scan_roomplan.py`; Test `tests/lab/test_scan_roomplan.py` (fixtures in
`tests/lab/fixtures/roomplan/`, hand-written, two rooms)

**Interfaces:**
- Produces: `read_roomplan_json(raw: dict) -> tuple[dict, list[dict], list[str]]` = (`vesper-plan/1` in metres,
  objects `[{kind, room, box:[x0,z0,x1,z1], h, yaw}]`, warnings). Walls from `walls[*].transform`+`dimensions`;
  doors/windows/openings by `parentIdentifier` onto their wall (an opening is a door with no leaf); rooms from
  `sections` (label, center) as the regions the walls enclose (a room polygon is the wall-bounded region containing
  the section centre; with no sections, each enclosed region is a room typed `other`); `story` > 0 elements are
  ignored with a warning (one storey); entrance = a door on an outside wall (the first by position; warning if none).
  Also `CapturedStructure` (`rooms: [CapturedRoom]`) merged in one frame. Built on
  `plan_from_parts(walls, doors, windows, openings, sections, objects) -> tuple[dict, list[dict], list[str]]`, where
  each wall is `{id, a:[x,z], b:[x,z], height}`, each door/window/opening `{wall, at:[x,z], width, bottom, top}`,
  each section `{label, center:[x,z]}`, each object `{category, center:[x,y,z], size:[w,h,d], yaw}` (plan
  coordinates); B2 feeds the same function.

- [ ] Step 1: failing tests: two-room fixture → 2 rooms of the right types and areas (±2 %), 1 interior door, 1
  entrance, 2 windows; a door's `parentIdentifier` places it on that wall; walls of 1.59/2.09/2.42 m → no
  refusal, warning "ceiling not scanned: 2.5 m assumed"; an upstairs element → warning; the plan passes
  `plan.load_plan`; the LiteReality bedroom JSON (skip if absent) gives one bedroom with its doors and windows.
- [ ] Step 2-4: run, implement, pass (`tests/lab/test_scan_roomplan.py tests/lab/test_plan.py`). Step 5: commit.

### Task B2: RoomPlan USDZ → the same reader

**Files:** Create `vesper/lab/scan_usd.py`; Modify `pyproject.toml` (extra `scan = ["trimesh>=4", "numpy",
"usd-core>=24"]`); Test `tests/lab/test_scan_usd.py`

**Interfaces:**
- Consumes: B1's intermediate (walls, doors, windows, openings, sections, objects).
- Produces: `read_roomplan_usdz(path: Path) -> tuple[dict, list[dict], list[str]]`: from the parametric export
  (`/*/Parametric_grp/Arch_grp/Wall_N_grp/WallN` cubes with `xformOp:scale` (width, height, depth), child prims
  `DoorN`/`WindowN`/`OpeningN` identified by prim name; `Section_grp/<label><n>/<label><n>_centerTop`; objects
  `Object_grp/<Cat>_grp/<Cat><n>`), and from the mesh export when no parametric group exists (bounding boxes of
  `Arch_grp` prims). Without usd-core: `ScanError("install the scan extra: pip install 'vesper[scan]'")`.

- [ ] Step 1: failing tests: a USDA text fixture written by the test with pxr (skip when pxr is absent) → same plan
  as B1's equivalent JSON; the LiteReality `room_p.usdz` (skip if absent) agrees with its JSON on room area (±3 %),
  door and window counts. **Step 2-4. Step 5: commit.**

### Task B3: Any mesh → vesper-plan/1 (up axis, scale, slicing)

**Files:** Create `vesper/lab/scan_mesh.py`; Test `tests/lab/test_scan_mesh.py`

**Interfaces:**
- Produces: `read_mesh(path: Path, scale: float | None = None) -> tuple[dict, dict, list[str]]` = (plan in metres,
  `{"up": "y"|"z"|..., "scale": float, "floor_y": float, "ceiling_y": float | None, "transform": 4x4}`, warnings);
  `ScanError` with the reason on refusal. Steps: load (trimesh: glb/gltf/obj/ply); up axis = the axis whose
  horizontal-face area histogram has the floor/ceiling pair 2.1-4.0 apart (then the largest extent pair as a
  fallback); scale per Global Constraints; slice at 0.5/1.0/2.15 m into 0.05 m occupancy grids; floor = the
  largest connected floor region under walls; rooms = connected free regions of the 1.0 m grid after closing gaps
  0.6-1.6 m wide (each closed gap a door candidate), regions under 1.5 m² or touching the grid edge are outside;
  doors/windows per Global Constraints; entrance = a door from a room to outside, else the widest gap to outside;
  polygons from the region outlines simplified to 0.1 m.
- [ ] Step 1: failing tests with meshes built by trimesh in the test: a two-room box house with a door between and
  an entrance and a window → 2 rooms (areas ±5 %), 1 interior door, 1 entrance, 1 window; the same rotated to Z-up
  → same plan; scaled by 1/2.5 → rescaled with a warning; no ceiling, walls cut at 1.0, no door → ScanError scale;
  a 24 m ground plane around the house → same plan; triangles sprayed 2 m outside a window → no extra room.
  **Step 2-4: run, implement, pass. Step 5: commit.**

### Task B4: `vesper lab import` for scans, the model as the 3D view, objects as furniture

**Files:** Modify `vesper/cli.py` (dispatch on suffix: `.json` (RoomPlan if it has `walls`/`rooms`, else
vesper-plan/1), `.usdz`, `.glb`, `.gltf`, `.obj`, `.ply`; `--scale` as a number for meshes; `--types`),
`vesper/lab/generate.py` (`generate_from_plan(..., objects=None, model=None)`: objects → furniture.json `source:
scan`, built-in furniture only in rooms with none; `model` = the source turned into plan coordinates and written as
`scene.glb` (trimesh export), original copied to `source/`, `.gitignore` written), README ("Importing a scan or a
3D model", licences are the user's); Test `tests/lab/test_import_scan.py`

- [ ] Step 1: failing tests: the B1 fixture imports with no scale; home loads, rooms reachable, furniture from the
  scan's objects in furniture.json, `vesper lab layout` 0 errors; the B3 two-room mesh imports, `scene.glb` exists,
  its boxes (layoutcheck.glb_boxes) fall inside the home's rooms (the transform is right), `.gitignore` lists
  scene.glb and source/; without trimesh a `.glb` import exits 1 naming the extra. **Step 2-4. Step 5: commit.**

### Task B5: Evaluation on the test set

**Files:** Create `vesper/validation/scans.py` (`vesper validate scans --dataset DIR`), `docs/experiments/
results-scan-import.md`; Test `tests/validation/test_scans.py`

- [ ] Step 1: hand truth per model in `DIR/truth.json` (local, from the `_views/` renders and the RoomPlan JSONs:
  rooms, doors, windows per model), written by the implementer and described (not committed) in the doc.
  **Step 2:** failing tests for the scoring on a fixture. **Step 3-4.** **Step 5:** run on all 19 models; report per
  model: stage reached, rooms/doors/windows found vs truth, scale and up detected, layout errors; the share built
  with 0 layout errors per kind (RoomPlan, mesh scan, web model). Commit code and doc (aggregates and model folder
  names only; no model files).
