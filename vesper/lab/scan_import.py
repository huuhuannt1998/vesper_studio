"""A scan or a 3D model as a home (scan import, task B4): what `vesper lab import` does with a file that is not a
floor plan.

`read_scan` dispatches on the file: a `.json` that is a RoomPlan CapturedRoom or CapturedStructure
(`scan_roomplan`), a `.usdz` (`scan_usd`; RoomPlan's mesh export leaves the openings out, so a `room.json` or a
`room_p.usdz` beside it is read instead, and said), or a mesh, `.glb .gltf .obj .ply` (`scan_mesh`). It returns the
plan as read (metres), the scan's objects as pieces, the warnings, each door's and window's bottom and top when the
scan measured them, and the model the 3D view is made from: a mesh, with the matrix taking it to the plan as read.

`furniture_items` turns the pieces into furniture.json items (`source: scan`) in the rectified plan's frame (rectify
turns the plan square: each piece's centre turns with it and its yaw takes the same angle); `write_model` writes the
mesh in the home's frame as `scene.glb`, the 3D view; `keep_source` copies the original into `<home>/source/`. The
model is the user's: VESPER keeps it beside the home, lists it in `<home>/.gitignore`, and never redistributes it.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from vesper.lab.scan_mesh import SUFFIXES as MESH_SUFFIXES
from vesper.lab.scan_roomplan import ScanError

FORMAT = "vesper-scan/1"
SUFFIXES = (".json", ".usdz", *MESH_SUFFIXES)
SNAP_DEG = 5.0  # a piece turned to within this of the walls' directions stands square to them (scan noise)
SOLID_M = 0.25  # a piece lower than this is walked over (a rug), as the layout check takes it
GITIGNORE = "scene.glb\nsource/\n"


@dataclass
class Scan:
    kind: str  # "roomplan-json" | "roomplan-usdz" | "mesh"
    read: Path  # the file the plan was read from
    given: Path  # the file the user named (the same, unless a better one beside it was read)
    plan: dict  # vesper-plan/1 in metres, as read (before rectify)
    pieces: list[dict]  # {kind, room, box (before yaw), h, yaw}, the plan's room ids
    warnings: list[str]
    heights: dict  # {"doors": [{bottom, top}], "windows": [...]}, one per plan door and window, or []
    notes: list[str] = field(default_factory=list)  # what was chosen (a better file beside the given one)
    meta: dict | None = None  # a mesh's reading: up, scale, floor_y, ceiling_y, transform
    model: Path | None = None  # the mesh the 3D view is made from (its frame: `meta["transform"]` to the plan)


def is_roomplan(raw) -> bool:
    """A RoomPlan CapturedRoom or CapturedStructure (not a `vesper-plan/1`, which has rooms too)."""
    if not isinstance(raw, dict) or raw.get("format") is not None:
        return False
    if isinstance(raw.get("walls"), list) or isinstance(raw.get("sections"), list):
        return True
    rooms = raw.get("rooms")
    return isinstance(rooms, list) and any(isinstance(r, dict) and isinstance(r.get("walls"), list) for r in rooms)


def _usdz_is_mesh_export(path: Path) -> bool | None:
    """Whether a USDZ is RoomPlan's mesh export (no parametric group), or None when that cannot be told (usd-core
    missing, the file unreadable or not RoomPlan's): the reader then says what is wrong."""
    from vesper.lab.scan_usd import is_mesh_export
    return is_mesh_export(path)


def _can_draw() -> bool:
    """usd-core, trimesh and numpy are installed: a RoomPlan USDZ can become the home's 3D view."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("pxr", "trimesh", "numpy"))


def view_file(src: Path) -> Path | None:
    """The RoomPlan export the home's 3D view is made from: the mesh export (RoomPlan's own meshes of the walls, with
    the windows cut into them, and of the objects) when `src` is one or one lies beside it as `<stem>.usdz`, else
    the parametric export (`src`, or `<stem>_p.usdz` beside it); None when there is neither (a JSON alone) or
    usd-core cannot tell. A RoomPlan app writes its JSON and USDZ of one capture in one frame."""
    stem = src.stem[:-2] if src.stem.endswith("_p") else src.stem
    found = [f for f in dict.fromkeys([src, src.with_name(f"{stem}.usdz"), src.with_name(f"{stem}_p.usdz")])
             if f.suffix.lower() == ".usdz" and f.is_file()]
    kinds = {f: _usdz_is_mesh_export(f) for f in found}
    return next((f for f in found if kinds[f] is True), next((f for f in found if kinds[f] is False), None))


def choose(src: Path) -> tuple[Path, str | None]:
    """The file to read for `src`, and a sentence when it is another: a USDZ that is RoomPlan's mesh export (which
    leaves the openings out) is passed over for the JSON beside it (RoomPlan's own, read with no extra installed),
    else the parametric export beside it (`<stem>_p.usdz`)."""
    if src.suffix.lower() != ".usdz":
        return src, None
    stem = src.stem[:-2] if src.stem.endswith("_p") else src.stem
    json_beside = src.with_name(f"{stem}.json")
    parametric = src.with_name(f"{stem}_p.usdz")
    mesh = _usdz_is_mesh_export(src)
    if mesh is False:
        return src, None
    if json_beside.is_file():
        try:
            ok = is_roomplan(json.loads(json_beside.read_text()))
        except (OSError, ValueError):
            ok = False
        if ok:
            why = ("is RoomPlan's mesh export, which leaves the openings out" if mesh else
                   "cannot be told apart from RoomPlan's mesh export (usd-core is not installed, or the file is not "
                   "readable)")
            return json_beside, f"{src.name} {why}: {json_beside.name} beside it is read instead"
    if mesh and parametric.is_file() and parametric != src:
        return parametric, (f"{src.name} is RoomPlan's mesh export, which leaves the openings out: {parametric.name} "
                            "(the parametric export) beside it is read instead")
    return src, None


def read_scan(src: Path, scale: float | None = None) -> Scan:
    """The scan or model at `src` read into a plan (see the module). `scale`: a mesh's metres per unit (a RoomPlan
    file is in metres: a ScanError if one is given). A ScanError with the reason when it cannot be read."""
    src = Path(src)
    suffix = src.suffix.lower()
    if suffix not in SUFFIXES:
        raise ScanError(f"{src.name}: a scan or a 3D model is one of {', '.join(SUFFIXES)}")
    if not src.is_file():
        raise ScanError(f"{src}: no such file")
    if suffix in MESH_SUFFIXES:
        from vesper.lab.scan_mesh import read_mesh
        plan, meta, warnings = read_mesh(src, scale)
        return Scan("mesh", src, src, plan, [], list(warnings), {"doors": [], "windows": []}, meta=meta, model=src)
    if scale is not None:
        raise ScanError(f"{src.name} is a RoomPlan scan, in metres: --scale is for a 3D model in other units")
    read, note = choose(src)
    if read.suffix.lower() == ".json":
        from vesper.lab.scan_roomplan import read_roomplan_json
        try:
            raw = json.loads(read.read_text())
        except OSError as e:
            raise ScanError(f"{read}: cannot read it: {e.strerror}") from None
        except ValueError as e:
            raise ScanError(f"{read.name}: not valid JSON: {e}") from None
        plan, pieces, warnings = read_roomplan_json(raw)
        kind = "roomplan-json"
    else:
        from vesper.lab.scan_usd import read_roomplan_usdz
        plan, pieces, warnings = read_roomplan_usdz(read)
        kind = "roomplan-usdz"
    heights = getattr(warnings, "heights", None) or {"doors": [], "windows": []}
    scan = Scan(kind, read, src, plan, pieces, list(warnings), heights, notes=[note] if note else [])
    view = view_file(src) if _can_draw() else None
    floor_y = getattr(warnings, "floor_y", None)
    if view is not None and floor_y is not None:  # the export in the plan as read: its floor at 0
        scan.model = view
        scan.meta = {"up": "y", "scale": 1.0, "floor_y": round(float(floor_y), 4), "ceiling_y": None,
                     "transform": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, -float(floor_y)], [0.0, 0.0, 1.0, 0.0],
                                   [0.0, 0.0, 0.0, 1.0]]}
    return scan


def set_types(plan: dict, types: list[str]) -> None:
    """The rooms typed in order (`--types`): a mesh names nothing, so its rooms are `other` until the user says. A
    ScanError when the list does not match the rooms or names a type VESPER has not."""
    from vesper.lab.plan import ROOM_TYPES
    rooms = plan["rooms"]
    if len(types) != len(rooms):
        raise ScanError(f"--types names {len(types)} room type{'s' if len(types) != 1 else ''}; the scan has "
                        f"{len(rooms)} room{'s' if len(rooms) != 1 else ''} ({', '.join(r['name'] for r in rooms)})")
    for t in types:
        if t not in ROOM_TYPES:
            raise ScanError(f"--types: {t!r} is not a room type; choose from {', '.join(ROOM_TYPES)}")
    for r, t in zip(rooms, types, strict=True):
        r["type"] = t


# --- the plan as read -> the rectified plan -------------------------------------------------------------------

def turn_point(p, turn: dict) -> tuple[float, float]:
    """A point of the plan as read in the rectified plan's frame (`plan.plan_transform`'s turn)."""
    a = math.radians(turn["angle_deg"])
    cx, cz = turn["centre"]
    dx, dz = p[0] - cx, p[1] - cz
    return cx + dx * math.cos(a) - dz * math.sin(a), cz + dx * math.sin(a) + dz * math.cos(a)


def turn_matrix(turn: dict) -> list[list[float]]:
    """The 4x4 (row-major, on column vectors) of that turn about y, in the plan's x, y (up), z."""
    a = math.radians(turn["angle_deg"])
    c, s = math.cos(a), math.sin(a)
    cx, cz = turn["centre"]
    return [[c, 0.0, -s, cx - c * cx + s * cz], [0.0, 1.0, 0.0, 0.0], [s, 0.0, c, cz - s * cx - c * cz],
            [0.0, 0.0, 0.0, 1.0]]


def _matmul(a, b) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def model_matrix(transform, turn: dict) -> list[list[float]]:
    """The model's file frame to the home's: the reader's transform, then rectify's turn."""
    return _matmul(turn_matrix(turn), [list(map(float, row)) for row in transform])


def furniture_items(pieces: list[dict], turn: dict, rooms: dict[str, str]) -> list[dict]:
    """The scan's pieces as furniture.json items in the rectified plan's frame, `source: scan`. Each centre turns
    with the plan and its yaw takes the same angle; a piece within SNAP_DEG of the walls' directions stands square to
    them. `box` is the turned footprint's bounding box. `rooms`: the plan's room ids to the home's
    (generate.typed_ids).

    `facing` is the view's: a model's front is its +z, turned so its width runs along (sin f, -cos f) and its front
    along (cos f, sin f) (furniture3d.js, devices3d.js facingYaw). A RoomPlan object's front is its own +z: in the
    three LiteReality scans every bed's, sofa's, cabinet's and sink's -z end stands against a wall (0.00-0.24 m from
    one) and most chairs' +z points at the nearest table. Its width runs along (cos yaw, sin yaw), so its front along
    (-sin yaw, cos yaw): facing = yaw + 90."""
    out = []
    for p in pieces:
        x0, z0, x1, z1 = p["box"]
        w, d = x1 - x0, z1 - z0
        x, z = turn_point(((x0 + x1) / 2, (z0 + z1) / 2), turn)
        yaw = p["yaw"] + turn["angle_deg"]
        square = round(yaw / 90.0) * 90.0
        if abs(yaw - square) <= SNAP_DEG:
            yaw = square
        t = math.radians(yaw)
        hx = abs(w / 2 * math.cos(t)) + abs(d / 2 * math.sin(t))
        hz = abs(w / 2 * math.sin(t)) + abs(d / 2 * math.cos(t))
        facing = round((yaw + 90.0) % 360.0, 3) % 360.0
        h = float(p["h"])
        out.append({"kind": p["kind"], "room": rooms[p["room"]], "x": round(x, 3), "z": round(z, 3),
                    "w": round(w, 3), "d": round(d, 3), "h": round(h, 3), "facing": facing, "solid": h >= SOLID_M,
                    "box": [round(v, 3) for v in (x - hx, z - hz, x + hx, z + hz)], "source": "scan"})
    return out


# --- the model and the original --------------------------------------------------------------------------------

def write_model(src: Path, matrix, out: Path) -> None:
    """The model at `src` (a mesh, or a RoomPlan USDZ) moved by `matrix` into the home's frame, as `out` (GLB)."""
    if Path(src).suffix.lower() == ".usdz":
        from vesper.lab.scan_usd import write_glb
        write_glb(Path(src), out, matrix)
        return
    _write_mesh(src, matrix, out)


def _write_mesh(src: Path, matrix, out: Path) -> None:
    """The model at `src` (with its materials) moved by `matrix` (row-major 4x4, on column vectors) into the home's
    frame and written as a glTF binary at `out`. The move is baked into every node's vertices (each node then has no
    transform of its own), so the file's boxes are its geometry's boxes in the home's frame (layoutcheck.glb_boxes)."""
    import numpy as np
    import trimesh
    try:
        scene = trimesh.load(str(src), force="scene")
    except Exception as e:  # trimesh raises many kinds on a broken file
        raise ScanError(f"{src.name}: cannot be read as a model ({type(e).__name__}: {e})") from e
    m = np.asarray(matrix, dtype=np.float64)
    moved = trimesh.Scene()
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        geom = scene.geometry.get(name)
        if geom is None:
            continue
        geom = geom.copy()
        geom.apply_transform(m @ np.asarray(transform, dtype=np.float64))
        moved.add_geometry(geom, node_name=str(node), geom_name=str(node))
    if not moved.geometry:
        raise ScanError(f"{src.name}: has no geometry to draw")
    out.write_bytes(moved.export(file_type="glb"))


def _companions(src: Path) -> list[Path]:
    """The files a model reads beside it: an OBJ's material libraries and their textures, a glTF's buffers and
    images (relative paths only, inside the model's folder)."""
    here = src.parent.resolve()
    found: list[Path] = []

    def add(name: str) -> None:
        p = (src.parent / name.strip()).resolve()
        if p.is_file() and p.is_relative_to(here) and p not in found and p != src.resolve():
            found.append(p)
    suffix = src.suffix.lower()
    try:
        if suffix == ".obj":
            for line in src.read_text(errors="replace").splitlines():
                if line.startswith("mtllib "):
                    add(line[7:])
            for mtl in [p for p in found if p.suffix.lower() == ".mtl"]:
                for line in mtl.read_text(errors="replace").splitlines():
                    parts = line.strip().split()
                    if parts and parts[0].lower().startswith(("map_", "bump", "disp", "decal", "norm")):
                        add(parts[-1])
        elif suffix == ".gltf":
            gltf = json.loads(src.read_text())
            for item in (*gltf.get("buffers", []), *gltf.get("images", [])):
                uri = item.get("uri")
                if isinstance(uri, str) and not uri.startswith("data:") and "://" not in uri:
                    add(uri)
    except (OSError, ValueError):
        pass
    return found


def keep_source(home: Path, files: list[Path], model: Path | None = None) -> list[str]:
    """The originals copied into `<home>/source/` (the file named and the file read when another; a model with the
    files it reads beside it, in their folders), `<home>/.gitignore` written; the names kept."""
    folder = home / "source"
    folder.mkdir()
    names = []
    every = [*files, *(_companions(model) if model is not None else [])]
    for f in dict.fromkeys(Path(f).resolve() for f in every):
        base = Path(model).resolve().parent if model is not None else f.parent
        rel = f.relative_to(base) if f.is_relative_to(base) else Path(f.name)
        dest = folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        names.append(rel.as_posix())
    (home / ".gitignore").write_text(GITIGNORE)
    return names
