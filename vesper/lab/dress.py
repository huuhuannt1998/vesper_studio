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
from pathlib import Path

FORMAT = "vesper-dress/1"
WALL_M = 2.5  # the 3D view's walls and doors (view3d.js)
DOOR_M = 2.05
COLLECTION = "VESPER home"


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


def scene_data(home) -> dict:
    """What the build script builds, from the home's map and devices; `export_to` is where the model goes."""
    m = home.map
    return {
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


def dress(home, out_dir: Path | None = None) -> tuple[Path, Path]:
    """Write `dress/build_home.py` and `dress/BRIEF.md` beside the home (or in `out_dir`); their paths."""
    out = Path(out_dir) if out_dir is not None else Path(home.path).parent / "dress"
    out.mkdir(parents=True, exist_ok=True)
    data = scene_data(home)
    script, brief = out / "build_home.py", out / "BRIEF.md"
    script.write_text(SCRIPT.replace("__DATA__", json.dumps(data, indent=1)))
    brief.write_text(_brief(home, data, script))
    return script, brief


def _area(polygon) -> float:
    return abs(sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def _brief(home, data: dict, script: Path) -> str:
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
        m.diffuse_color = COLORS.get(name, (0.8, 0.8, 0.8, 1.0))
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


def build():
    """The shell, made or remade: the rooms' floors, the walls, the lintels, the device empties. What the dressing
    added to the collections stays."""
    root = collection(ROOT, bpy.context.scene.collection)
    clear_built(root)
    root["vesper"] = DATA["format"]
    for room in DATA["rooms"]:
        rc = collection("room:" + room["id"], root)
        rc["label"] = room["label"]
        polygon = [(x, -z, 0.0) for x, z in room["polygon"]]
        mesh_object("floor:" + room["id"], polygon, [tuple(range(len(polygon)))], rc,
                    "outdoor" if room["outdoor"] else "floor")
    walls = collection("walls", root)
    for k, (x0, z0, x1, z1) in enumerate(DATA["walls"]):
        cuboid("wall-%d" % k, x0, z0, x1, z1, 0.0, DATA["wall_m"], walls, "wall")
    lintels = collection("doorways", root)
    for d in DATA["doorways"]:
        x0, z0, x1, z1 = d["box"]
        cuboid("lintel:" + d["id"], x0, z0, x1, z1, DATA["door_m"], DATA["wall_m"], lintels, "wall")
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
                   use_active_collection=True)
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
