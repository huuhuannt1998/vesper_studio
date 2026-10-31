# W9: the Home Generator, as built

> Written after a test-driven build in a scratch tree, not before it (as plans W7a2 and W4a1): each task names its
> files, the interfaces it produces and the tests that drove it.

**Goal:** a home of any size from a number of rooms or a request, with the devices it asks for as real catalog
products, walkable and clickable in the 3D view as Room 334's walkthrough is; batches of homes; and an optional
Blender-dressed look.

**Architecture:** `vesper/lab/generate.py` turns a room list into a corridor floor plan (`vesper-map/1`), places the
requested devices by rule and writes the home; `vesper lab generate` makes one home or a batch with a summary. The
play server's device card (`/device/<name>.json`) and the page's card (key I) give each device's panel. The 3D view
draws furniture per room type on map-built homes. `vesper/lab/dress.py` writes a Blender build script and a brief;
the play server serves the model it leads to (`scene.glb`) and the 3D view draws it on the map.

**Tech stack:** Python 3.11+, PyYAML; three.js 0.170.0 (vendored); Blender 4.2+ and Blender MCP on the user's side,
optional.

**Spec:** `docs/superpowers/specs/2026-10-31-vesper-home-generator-design.md`.

## Global Constraints

- The generated map is the source of truth for walking, reach and sensing; a look only changes what is drawn.
- Every room is reachable from the front door; generated layouts are labelled synthetic, positions assumed.
- Devices are real catalog products (L1 or above), deterministic from the seed.
- Nothing from HSSD or the humanoids is committed; Blender, Blender MCP and their assets are the user's.

## Review Focus

1. Requests at the edges: 1 room, 60 rooms, a program with an unknown room type, `at` naming a room the program does
   not have, `count` larger than the rooms can hold.
2. A batch whose `--rooms` range is reversed or whose directory exists.
3. The device card for a device with no network link (a home without a network), a Matter device (no hub entities).
4. The Blender script run in a scene that already holds a `VESPER home` collection with the user's furniture in it
   (the rerun removes it: the brief warns that the collection is rebuilt).
5. A dressed model that fails to load (the plain look comes back) or is misaligned (the brief's axes).

---

### Task 1: the floor plan

**Files:** create `vesper/lab/generate.py` (`program`, `floor_plan`), `tests/lab/test_generate.py`.

**Produces:** `program(rooms: int) -> list[str]`; `floor_plan(types, seed, scene=None) -> WorldMap`;
`GenerateError`.

**Tests:** the program table; layouts for 1-60 rooms and several seeds (rooms do not overlap, every room reachable
from the front door, doors at least 0.8 m on the grid, the map loads); determinism; 60 rooms planned in under
5 s.

### Task 2: requests, placement and the home

**Files:** modify `vesper/lab/generate.py` (`load_request`, `place_devices`, `generate_home`, `RULES`, `STARTER`,
`starter`, `summary_row`).

**Produces:** `load_request(path) -> Request`; `generate_home(request, catalog, out)`; `starter(types, catalog)`.

**Tests:** a pinned product, a category per room with exceptions, a count spread over rooms, the front door's lock
and contact; the request's mistakes named; positions by the rules (ceiling centre, corner, door, beside the door,
along a wall, a table); 60 rooms and at least 200 devices generated in under 5 s; the written home checks.

### Task 3: the command

**Files:** modify `vesper/cli.py` (`vesper lab generate`, `_lab_generate`, `_room_range`, `_with_starter`); create
`tests/lab/test_cli_generate.py`.

**Tests:** a number of rooms gives a home with the starter set that `vesper lab check` accepts; an existing
directory refused; a batch writes each home and `summary.csv`; a request file.

### Task 4: the device card

**Files:** modify `vesper/lab/play.py` (`device_card`, `/device/<name>.json`), `vesper/lab/ui/index.html`,
`style.css`, `app.js` (key I, `inspect`, `cardHtml`); create `tests/lab/test_device_card.py`.

**Tests:** the card's product, model basis, connections, interfaces and state; the route; an unknown device is 404.
Headless look: the card open on a generated home (shot read).

### Task 5: furniture

**Files:** modify `vesper/lab/ui/scene3d.js` (`furnitureFor`), `view3d.js`; `tests/lab/ui/scene3d.test.mjs`.

**Tests:** each room type's pieces inside the room, against the wall opposite its door, clear of doorways. Headless
look: a generated 12-room home (shots read).

### Task 6: dressed in Blender

**Files:** create `vesper/lab/dress.py`, `tests/lab/fake_bpy.py`, `tests/lab/test_dress.py`; modify `vesper/cli.py`
(`vesper lab dress`), `vesper/lab/play.py` (`dressed`, `/home/scene.glb`), `vesper/lab/ui/view3d.js`
(`loadDressed`).

**Produces:** `dress(home, out_dir=None) -> (script, brief)`; `scene_data(home) -> dict` (`vesper-dress/1`);
`wall_boxes(grid)`; `device_height(mount, category)`.

**Tests:** the walls as the 3D view draws them; the scene data; the script run against a stand-in `bpy` builds the
rooms, walls, lintels and device empties in Blender's axes in one collection, rebuilds it on a second run leaving
the rest of the scene, and exports `scene.glb` beside the home when asked; the command; the play server serves the
model and tells the page.

### Task 7: documentation

README (generated homes, Blender), the spec's status, this plan.

## Rulings

- The corridor plan (rooms along both sides of one corridor) instead of a squarified partition with a spanning tree
  of doors: reachability by construction, and every room touches circulation. Cost if wrong: homes look alike; a
  second layout can come later behind the same `vesper-map/1`.
- `--look habitat` (the closest HSSD scene) is not built: one HSSD scene is downloaded, and `vesper lab new --scene`
  already makes an HSSD home. Cost if wrong: researchers wanting HSSD furniture on a generated program wait.
- Furniture is drawn, not solid: the map stays the walking truth, so a dressed model cannot block a path the map
  allows. Cost if wrong: a person walks through a sofa a modeller put in a walkway; the brief asks to keep them clear.
- The Blender script rebuilds its own collection on every run; the brief says to keep added furniture in the room
  collections, so a rerun removes it too. Cost if wrong: work lost on a rerun after furnishing (the brief warns).
- The dressed model is drawn at the map's own axes (Blender (x, -z, h), glTF +Y up), with no alignment step. Cost if
  wrong: a model exported with other options is misplaced; the brief gives the exact export call.
- The plan is as-built (process).
