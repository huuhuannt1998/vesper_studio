"""Dressing a home in Blender (home-generator spec §5, plan W9): `vesper lab dress <home>` writes, beside the home,
`dress/build_home.py`, a script for Blender, and `dress/BRIEF.md`, a brief for whoever runs it (a person, or Claude
through Blender MCP). The script builds the home from its map, in one collection: each room's floor, the walls with
door openings where the map has doorways (and a lintel over each), and an empty where each device is. Whoever dresses
it adds furniture and materials, and exports `scene.glb` beside the home; `vesper lab play` then draws that model.
The map stays what walking, reach and sensing use: the model is only what is drawn.

Coordinates: the map's (x, z) on the floor and y up, in metres, as the 3D view draws them. Blender is z-up and its
glTF export turns +Z up into +Y up, so a map point (x, z) at height h is Blender's (x, -z, h).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

FORMAT = "vesper-dress/1"
WALL_M = 2.5  # the 3D view's walls and doors (view3d.js)
DOOR_M = 2.05
COLLECTION = "VESPER home"
KTX2PNG = Path(__file__).resolve().parent / "ktx2png.mjs"
CACHE_ENV = "VESPER_CACHE"


class DressError(RuntimeError):
    """A home that cannot be dressed (an HSSD model missing, Node not installed)."""


def model_cache() -> Path:
    """Where HSSD's models converted for Blender are kept: derived from HSSD, so never in the repository."""
    return Path(os.environ.get(CACHE_ENV) or Path.home() / ".cache" / "vesper") / "hssd-blender"


def prepare_models(world3d, cache: Path, run=subprocess.run) -> dict:
    """{asset: the model converted for Blender}: each distinct HSSD model of the home once, its KTX2 textures made
    PNG (`ktx2png.mjs`, the 3D view's own Basis transcoder), kept in `cache` for every later home."""
    if world3d.missing:
        raise DressError(f"{len(world3d.missing)} of the scene's models are not in {world3d.root} "
                         f"(first: {world3d.missing[0]})")
    cache = Path(cache)
    models = {a: cache / a for a in sorted(world3d.assets)}
    todo = [[str(world3d.root / a), str(out)] for a, out in models.items()
            if not out.is_file() or out.stat().st_mtime < (world3d.root / a).stat().st_mtime]
    if todo:
        try:
            r = run(["node", str(KTX2PNG), "--batch"], input=json.dumps(todo), capture_output=True, text=True,
                    timeout=3600)
        except FileNotFoundError:
            raise DressError("Node is not installed: it converts HSSD's textures for Blender") from None
        if r.returncode != 0:
            raise DressError(f"converting HSSD's models failed: {(r.stderr or '').strip()[-300:]}")
    return models


def device_height(mount: str | None, category: str | None) -> float:
    """A device's height above the floor by its mount, as the 3D view places its marker (scene3d.js)."""
    if mount == "ceiling":
        return 2.35
    if mount == "table":
        return 0.8
    if mount == "floor":
        return 0.08
    if mount == "door":
        return 1.0 if category == "lock" else 2.0  # a lock at the handle, a contact at the top
    if category in ("switch", "button"):
        return 1.2
    return 1.5 if category == "thermostat" else 1.4


def wall_boxes(grid) -> list[tuple[float, float, float, float]]:
    """The map's walls as boxes (x0, z0, x1, z1) in metres, as the 3D view draws them: the blocked cells with
    walkable floor among their eight neighbours (the cells just outside the grid count as blocked), a run along a row
    one box with the same run in the rows below it."""
    def is_wall(i: int, j: int) -> bool:
        return not grid.is_walkable(i, j) and any(grid.is_walkable(i + di, j + dj)
                                                  for dj in (-1, 0, 1) for di in (-1, 0, 1))
    growing: dict[tuple[int, int], list[int]] = {}  # (i0, i1) -> the box [i0, i1, j0, j1) still growing downward
    boxes: list[list[int]] = []
    for j in range(-1, grid.height + 1):
        i = -1
        while i <= grid.width:
            if not is_wall(i, j):
                i += 1
                continue
            start = i
            while i <= grid.width and is_wall(i, j):
                i += 1
            box = growing.get((start, i))
            if box is not None and box[3] == j:
                box[3] = j + 1
            else:
                growing[(start, i)] = box = [start, i, j, j + 1]
                boxes.append(box)
    (ox, oz), c = grid.origin, grid.cell
    return [(ox + i0 * c, oz + j0 * c, ox + i1 * c, oz + j1 * c)
            for i0, i1, j0, j1 in sorted(boxes, key=lambda b: (b[2], b[0]))]


def _doorway_box(grid, cells) -> list[float]:
    (ox, oz), c = grid.origin, grid.cell
    i = [x for x, _ in cells]
    j = [z for _, z in cells]
    return [ox + min(i) * c, oz + min(j) * c, ox + (max(i) + 1) * c, oz + (max(j) + 1) * c]


def scene_data(home, world3d=None, models: dict | None = None) -> dict:
    """What the build script builds, from the home's map and devices; `export_to` is where the model goes. An HSSD
    home (`world3d`, its models converted: `models`) is HSSD's own meshes at Habitat's places, not walls from its
    map."""
    m = home.map
    data = {
        "format": FORMAT, "scene": home.scene, "wall_m": WALL_M, "door_m": DOOR_M,
        "export_to": str((Path(home.path).parent / "scene.glb").resolve()),
        "rooms": [{"id": r.id, "label": r.label, "polygon": [list(p) for p in r.polygon], "outdoor": r.outdoor}
                  for r in m.rooms],
        "walls": [list(b) for b in wall_boxes(m.grid)],
        "doorways": [{"id": d.id, "rooms": list(d.rooms), "width": d.width, "outdoor": d.outdoor,
                      "box": _doorway_box(m.grid, d.cells)} for d in m.doorways],
        "devices": [{"name": d.name, "category": d.record["identity"].get("category"), "mount": d.mount,
                     "room": d.room, "point": [d.position[0], device_height(d.mount, d.record["identity"].get(
                         "category")), d.position[1]]} for d in home.devices]}
    if world3d is not None:
        data["walls"], data["doorways"] = [], []
        data["hssd"] = {"floor_y": world3d.floor_y, "items": [
            {"kind": i.get("kind"), "asset": i["asset"], "model": str(models[i["asset"]]), "matrix": i["matrix"],
             "doorway": i.get("doorway"), "opening": i.get("opening")} for i in world3d.manifest["items"]]}
    return data


def dress(home, out_dir: Path | None = None, *, world3d=None, cache: Path | None = None,
          run=subprocess.run) -> tuple[Path, Path]:
    """Write `dress/build_home.py` and `dress/BRIEF.md` beside the home (or in `out_dir`); their paths. An HSSD
    home's models are converted first, into `cache` (default `model_cache()`)."""
    out = Path(out_dir) if out_dir is not None else Path(home.path).parent / "dress"
    models = prepare_models(world3d, Path(cache or model_cache()), run=run) if world3d is not None else None
    out.mkdir(parents=True, exist_ok=True)
    data = scene_data(home, world3d, models)
    script, brief = out / "build_home.py", out / "BRIEF.md"
    script.write_text(SCRIPT.replace("__DATA__", json.dumps(data, indent=1)))
    brief.write_text(_brief(home, data, script))
    return script, brief


def _area(polygon) -> float:
    return abs(sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def _brief(home, data: dict, script: Path) -> str:
    if "hssd" in data:
        return _hssd_brief(home, data, script)
    rows = []
    for r in data["rooms"]:
        devices = [d["name"] for d in data["devices"] if d["room"] == r["id"]]
        rows.append(f"| `room:{r['id']}` | {r['label']}{' (outdoors)' if r['outdoor'] else ''} | "
                    f"{_area(r['polygon']):.1f} | {', '.join(devices) or '-'} |")
    target = data["export_to"]
    return f"""# Dressing {home.scene} in Blender

VESPER built this home's map; this brief and `build_home.py` let you (or Claude, through Blender MCP) give it
furniture and materials. The result, `scene.glb` beside the home, is what `vesper lab play` draws. The map stays
what VESPER walks, reaches and senses with: the model is only what is drawn.

## 1. Build the shell

In Blender 4.2 or later: Scripting, open `{script}`, Run Script. Or with Blender MCP connected, ask Claude:

> Run the Python file {script} in Blender, then dress the home as its brief says: {script.parent / "BRIEF.md"}

The script makes one collection, `{COLLECTION}`, with:
- a collection per room (`room:<id>`), holding its floor; put the room's furniture in it;
- `walls`: {data['wall_m']} m high, with openings where the doorways are;
- `doorways`: a lintel over each opening, {data['door_m']} m up;
- `devices`: an empty where each device is (`device:<name>`, its category a custom property).

Running it again rebuilds only what it made (walls, floors, lintels, device empties): what you add stays.

## 2. Furnish and texture

| Collection | Room | Area (m²) | Devices |
|---|---|---|---|
{chr(10).join(rows)}

- Furnish each room as what it is, inside its own collection.
- Keep the walkways clear: nothing within a metre of a doorway, and corridors and halls left open. The furniture is
  drawn, not solid: a person walks where the map says the floor is free.
- Do not move, resize or delete the walls, floors, lintels or device empties; do not add doors (VESPER draws them
  and swings them open and shut) or a ceiling (the camera rises above the walls).
- Materials and textures are free; use only assets you may share (for example CC0 ones from Poly Haven), and list
  each with its source and license in `dress/SOURCES.md`.

## 3. Export

With `{COLLECTION}` the active collection, export it as glTF binary, +Y up, modifiers applied, to

    {target}

In Blender's Python, `build_home.py` does it when run with `--export`:

    blender --background your-file.blend --python {script} -- --export

or in the console: `bpy.ops.export_scene.gltf(filepath=r"{target}", export_format="GLB", export_yup=True,
export_apply=True, use_active_collection=True)`.

## 4. Play it

    vesper lab play {home.path}

The 3D view draws `scene.glb` in place of the map's plain walls and furniture, with VESPER's doors, devices and
people. Delete `scene.glb` to go back to the plain look.
"""


def _hssd_brief(home, data: dict, script: Path) -> str:
    rooms = "\n".join(f"| `room:{r['id']}` | {r['label']}{' (outdoors)' if r['outdoor'] else ''} |"
                      for r in data["rooms"])
    target = data["export_to"]
    return f"""# Editing {home.scene} in Blender

This home is an HSSD scene (CC BY-NC 4.0: keep it, and everything made from it, out of public repositories).
`build_home.py` brings it into Blender as Habitat places it, so you (or Claude, through Blender MCP) can move, swap,
add or remove furniture. The result, `scene.glb` beside the home, is what `vesper lab play` draws.

## 1. Bring the scene in

In Blender 4.2 or later: Scripting, open `{script}`, Run Script. Or with Blender MCP connected, ask Claude:

> Run the Python file {script} in Blender, then edit the home as its brief says: {script.parent / "BRIEF.md"}

It imports each of the scene's {len(data['hssd']['items'])} models under an empty named `hssd:<n>:<kind>:<model>`,
which holds the model's place (move the empty to move the piece):
- `stage`: the building itself (walls, floors, windows);
- a collection per room (`room:<id>`), holding its furniture;
- `hssd:other`: the pieces outside every room;
- `devices`: an empty where each device is;
- `VESPER doors`, outside `VESPER home`: the doors. VESPER draws and swings them, so they are not exported.

Running the script again keeps your edits and only refreshes the device empties. To start over, delete the
`VESPER home` and `VESPER doors` collections first.

| Collection | Room |
|---|---|
{rooms}

## 2. Edit

- Keep furniture inside its room and the doorways clear.
- Do not move the stage, the doors or the device empties.
- New models: only ones you may share (for example CC0 ones from Poly Haven), each listed with its source and
  license in `dress/SOURCES.md`.

## 3. Export

With `VESPER home` the active collection, export it as glTF binary, +Y up, modifiers applied, to

    {target}

or run `build_home.py` with `--export`: `blender --background your-file.blend --python {script} -- --export`.

## 4. Make the map follow the furniture

    vesper lab renav {home.path}

Habitat works out the walkable floor again from `scene.glb`, so people walk round the furniture where it now
stands. The rooms and doorways stay as they were. Then `vesper lab play {home.path}`; delete `scene.glb` to go back
to HSSD's own files.
"""


SCRIPT = r'''"""Built by `vesper lab dress` (VESPER home generator, spec section 5): run in Blender 4.2 or later.

Builds the home in one collection, "VESPER home": a collection per room with its floor, the walls with door
openings, a lintel over each doorway, and an empty where each device is. Run again, it rebuilds only what it made
(tagged `vesper_built`): furniture added to the collections stays. With `-- --export` it exports the collection as
it is to scene.glb beside the home.

The map's (x, z) on the floor, y up, become Blender's (x, -z, height): glTF's +Y up export turns them back.
"""
import json
import sys

import bpy

DATA = json.loads(r"""__DATA__""")
ROOT = "VESPER home"
COLORS = {"floor": (0.82, 0.78, 0.70, 1.0), "outdoor": (0.55, 0.62, 0.48, 1.0), "wall": (0.93, 0.92, 0.89, 1.0)}


def material(name):
    """VESPER's own material (`vesper:floor`, ...), never one of the user's that shares its name."""
    m = bpy.data.materials.get("vesper:" + name)
    if m is None:
        m = bpy.data.materials.new("vesper:" + name)
        colour = COLORS.get(name, (0.8, 0.8, 0.8, 1.0))
        m.diffuse_color = colour  # the viewport's
        tree = getattr(m, "node_tree", None)
        if tree is None and hasattr(m, "use_nodes"):  # Blender 4: nodes on request (5 always has them)
            m.use_nodes = True
            tree = m.node_tree
        bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if tree is not None else None
        if bsdf is not None:  # what the glTF export reads
            bsdf.inputs["Base Color"].default_value = colour
    return m


def built(item):
    item["vesper_built"] = 1  # what a rebuild may replace: never what the dressing added
    return item


def clear_built(root):
    """Remove the objects (and their meshes) the last build made; furniture and the rest stay."""
    for obj in list(root.all_objects):
        if not obj.get("vesper_built"):
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.get("vesper_built") and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def collection(name, parent):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
    if c.name not in [x.name for x in parent.children]:
        parent.children.link(c)
    return c


def mesh_object(name, vertices, faces, coll, mat):
    mesh = built(bpy.data.meshes.new(name))
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    mesh.materials.append(material(mat))
    obj = built(bpy.data.objects.new(name, mesh))
    coll.objects.link(obj)
    return obj


def cuboid(name, x0, z0, x1, z1, y0, y1, coll, mat):
    """A box over the map's [x0, x1] x [z0, z1], from y0 to y1 up."""
    b0, b1 = -z1, -z0  # Blender's y
    vertices = [(x0, b0, y0), (x1, b0, y0), (x1, b1, y0), (x0, b1, y0),
                (x0, b0, y1), (x1, b0, y1), (x1, b1, y1), (x0, b1, y1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return mesh_object(name, vertices, faces, coll, mat)


C = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]  # glTF's (and Habitat's) +Y up to Blender's +Z up


def matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def blender_matrix(rows16):
    """A Habitat world matrix (16 numbers, row by row) in Blender's axes: C M C^-1."""
    m = [list(rows16[r * 4:r * 4 + 4]) for r in range(4)]
    return matmul(matmul(C, m), [list(r) for r in zip(*C)])


def as_matrix(rows):
    """Blender's matrix from its rows: a plain nested list would be read column by column."""
    try:
        from mathutils import Matrix
    except ImportError:  # outside Blender (the tests' stand-in)
        return rows
    return Matrix(rows)


def room_at(x, z):
    """The room whose outline holds the map point (x, z), else None."""
    for room in DATA["rooms"]:
        pts, inside = room["polygon"], False
        for (x0, z0), (x1, z1) in zip(pts, pts[1:] + pts[:1]):
            if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
                inside = not inside
        if inside:
            return room["id"]
    return None


def import_hssd(root):
    """Each HSSD model under an empty holding Habitat's matrix (tagged vesper_hssd: a rebuild keeps it, edits and
    all): the stage in `stage`, furniture in its room's collection, doors apart in "VESPER doors" (not exported:
    VESPER draws and swings them)."""
    doors = collection("VESPER doors", bpy.context.scene.collection)
    stage = collection("stage", root)
    other = collection("hssd:other", root)
    for k, item in enumerate(DATA["hssd"]["items"]):
        if item["kind"] == "stage":
            coll = stage
        elif item.get("doorway") or item.get("opening") == "door":  # as the map's export: no door in the floor's way
            coll = doors
        else:
            room = room_at(item["matrix"][3], item["matrix"][11])
            coll = collection("room:" + room, root) if room else other
        before = {o.name for o in bpy.data.objects}
        bpy.ops.import_scene.gltf(filepath=item["model"])
        new = [o for o in bpy.data.objects if o.name not in before]
        stem = item["asset"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        holder = bpy.data.objects.new("hssd:%03d:%s:%s" % (k, item["kind"], stem), None)
        holder["vesper_hssd"], holder["asset"] = 1, item["asset"]
        if item.get("doorway"):
            holder["doorway"] = item["doorway"]
        holder.matrix_world = as_matrix(blender_matrix(item["matrix"]))
        coll.objects.link(holder)
        for o in new:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
            if o.parent is None:
                o.parent = holder


def build():
    """The shell, made or remade: the rooms' floors, the walls, the lintels, the device empties (an HSSD home: its
    models, imported once). What the dressing added or moved stays."""
    root = collection(ROOT, bpy.context.scene.collection)
    clear_built(root)
    root["vesper"] = DATA["format"]
    hssd = DATA.get("hssd")
    for room in DATA["rooms"]:
        rc = collection("room:" + room["id"], root)
        rc["label"] = room["label"]
        if hssd:
            continue  # the stage has its own floors
        polygon = [(x, -z, 0.0) for x, z in room["polygon"]]
        if sum(x0 * y1 - x1 * y0 for (x0, y0, _), (x1, y1, _) in zip(polygon, polygon[1:] + polygon[:1])) < 0:
            polygon.reverse()  # facing up: a navmesh (vesper lab renav) walks only on faces that do
        mesh_object("floor:" + room["id"], polygon, [tuple(range(len(polygon)))], rc,
                    "outdoor" if room["outdoor"] else "floor")
    if hssd and not any(o.get("vesper_hssd") for o in bpy.data.objects):
        import_hssd(root)
    walls = collection("walls", root)
    for k, (x0, z0, x1, z1) in enumerate(DATA["walls"]):
        cuboid("wall-%d" % k, x0, z0, x1, z1, 0.0, DATA["wall_m"], walls, "wall")
    lintels = collection("doorways", root)
    for d in DATA["doorways"]:
        x0, z0, x1, z1 = d["box"]
        cuboid("lintel:" + d["id"], x0, z0, x1, z1, DATA["door_m"], DATA["wall_m"], lintels, "wall")
        # the doorway's own floor: the rooms' floors stop short of the walls, and a navmesh taken from the model
        # (vesper lab renav) needs a way through
        mesh_object("threshold:" + d["id"], [(x0, -z1, 0.0), (x1, -z1, 0.0), (x1, -z0, 0.0), (x0, -z0, 0.0)],
                    [(0, 1, 2, 3)], lintels, "floor")
    devices = collection("devices", root)
    for d in DATA["devices"]:
        x, h, z = d["point"]
        obj = built(bpy.data.objects.new("device:" + d["name"], None))
        obj.empty_display_type, obj.empty_display_size = "SPHERE", 0.07
        obj.location = (x, -z, h)
        obj["category"] = d["category"] or ""
        obj["mount"] = d["mount"] or ""
        devices.objects.link(obj)
    return root


def export():
    """The collection as it is (the shell and what the dressing added) to scene.glb beside the home."""
    layer = bpy.context.view_layer
    layer.active_layer_collection = layer.layer_collection.children[ROOT]
    options = dict(filepath=DATA["export_to"], export_format="GLB", export_yup=True, export_apply=True,
                   use_active_collection=True, export_extras=True)  # extras: each device empty's category
    try:
        bpy.ops.export_scene.gltf(use_active_collection_with_nested=True, **options)
    except TypeError:  # a Blender before the option: nested collections are exported anyway
        bpy.ops.export_scene.gltf(**options)


def main(argv):
    """No `--export`: build (or rebuild) the shell. `--export`: export what is there (building it only if it is
    not). At module level, not under `__main__`: Blender MCP runs the code with exec()."""
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    if "--export" not in args or bpy.data.collections.get(ROOT) is None:
        build()
    if "--export" in args:
        export()


main(sys.argv)
'''
