"""An Apple RoomPlan USDZ export read into a `vesper-plan/1` (scan import, task B2).

Polycam's Room mode, the 3D Scanner App and other RoomPlan apps also export a USDZ, in one of two forms:

- the parametric export (`room_p.usdz` in the 3D Scanner App): `/<root>/Parametric_grp/Arch_grp/Wall_N_grp/WallN`, a
  unit cube scaled to (width, height, ~0) by its `xformOp:scale` after the `xformOp:transform` that is RoomPlan's own
  4x4; the wall's doors, windows and openings stand beside it in its group as `DoorN`, `WindowN`, `OpeningN`. They
  are composed from internal layers whose Category the wall group's overrides, so each is known by its prim name;
- the mesh export (`room.usdz`): the same groups under `Mesh_grp`, each element a mesh whose local origin is
  RoomPlan's centre of the element and which is drawn some centimetres thick behind its plane (a wall also carried
  on past one end to fill the corner). RoomPlan leaves the openings out of this export.

Both carry `Section_grp/<label><n>/<label><n>_centerTop` (a room label and its centre), `Floor_grp/FloorN` and
`Object_grp/<Cat>_grp/<Cat><n>` (the group's customData `Category`). Every element is read as the oriented box of
its geometry in the stage's frame turned to y up and metres, and handed to `scan_roomplan.plan_from_parts`: what the
JSON reader does with RoomPlan's CapturedRoom, this does with its USDZ. The parametric group is read when there is
one; the mesh export only when there is not, and then the warnings say so.

usd-core (`pxr`) is an optional dependency: the `scan` extra (`EXTRA` says how to install it).
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from vesper.lab.scan_roomplan import EXTRA, ScanError, plan_from_parts

ARCH = re.compile(r"^(Wall|Door|Window|Opening)(\d+)$")
FLOOR = re.compile(r"^Floor\d*$")
NUMBERED = re.compile(r"^(.*?)(\d+)$")
EPS = 1e-6
KINDS = {"Door": "doors", "Window": "windows", "Opening": "openings"}


def _pxr():
    try:
        from pxr import Gf, Usd, UsdGeom
    except ImportError as e:
        raise ScanError(f"reading a USDZ needs usd-core: {EXTRA}") from e
    return Gf, Usd, UsdGeom


def _open(path: Path):
    _, Usd, _ = _pxr()
    if not path.is_file():
        raise ScanError(f"{path}: no such file")
    try:
        stage = Usd.Stage.Open(str(path), Usd.Stage.LoadAll)
    except Exception as e:  # pxr.Tf.ErrorException: a broken layer, a bad package
        said = re.findall(r" : '(.*?)'\s*(?=\n\s*Error in |$)", str(e), flags=re.S)
        first = (said[0] if said else str(e)).strip().replace(str(path), path.name)
        raise ScanError(f"{path.name} cannot be read as USD (usd-core: {' '.join(first.split())[:240]})") from e
    if stage is None:
        raise ScanError(f"{path.name} cannot be read as USD")
    return stage


def _correction(stage):
    """The matrix (row vectors, as USD's) that takes the stage's frame to y up and metres: RoomPlan writes y up in
    metres, so for its files this is the identity."""
    Gf, _, UsdGeom = _pxr()
    turn = Gf.Matrix4d(1)
    if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
        turn = Gf.Matrix4d(1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1)  # (x, y, z) z-up -> (x, z, -y) y-up
    return turn * Gf.Matrix4d().SetScale(UsdGeom.GetStageMetersPerUnit(stage))


def _named(stage, name: str) -> list:
    return [p for p in stage.Traverse() if p.GetName() == name]


def _under(prim) -> list:
    """Every prim below `prim`, in the stage's order."""
    from pxr import Usd
    return [p for p in Usd.PrimRange(prim) if p != prim]


class _Boxes:
    """Oriented boxes of geometric prims in metres, y up."""

    def __init__(self, stage):
        Gf, Usd, UsdGeom = _pxr()
        self.Gf, self.time = Gf, Usd.TimeCode.Default()
        self.cache = UsdGeom.BBoxCache(self.time, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render,
                                                   UsdGeom.Tokens.proxy])
        self.fix = _correction(stage)
        self.UsdGeom = UsdGeom

    def box(self, prim, plane: bool) -> dict | None:
        """{center: [x, y, z], axis: [ux, uz] (the box's x axis, level), size: [w, h, d], top: its highest y}, or
        None for a prim with no geometry or an x axis that is not level. `plane`: an architectural element, whose local
        origin is RoomPlan's centre of it even in the mesh export (drawn thick behind its plane, a wall carried on past
        one end): the width is the part of the box symmetric about the origin, and it stands in the plane through it."""
        if not prim.IsA(self.UsdGeom.Boundable):
            return None
        rng = self.cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
        if rng.IsEmpty():
            return None
        lo, hi = rng.GetMin(), rng.GetMax()
        mid, size = [(lo[i] + hi[i]) / 2 for i in range(3)], [hi[i] - lo[i] for i in range(3)]
        if plane:
            if lo[0] < -EPS and hi[0] > EPS:
                mid[0], size[0] = 0.0, 2 * min(-lo[0], hi[0])
            if lo[2] <= EPS and hi[2] >= -EPS:
                mid[2] = 0.0
        m = self.UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(self.time) * self.fix
        Gf = self.Gf
        c = m.Transform(Gf.Vec3d(*mid))
        ax, ay, az = (m.TransformDir(Gf.Vec3d(*v)) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        level = math.hypot(ax[0], ax[2])
        if level < EPS or level < 0.99 * ax.GetLength():
            return None
        return {"center": [c[0], c[1], c[2]], "axis": [ax[0] / level, ax[2] / level],
                "size": [size[0] * ax.GetLength(), size[1] * ay.GetLength(), size[2] * az.GetLength()],
                "top": Gf.BBox3d(rng, m).ComputeAlignedRange().GetMax()[1]}  # a floor's local z is up


def _number(prim) -> int:
    m = NUMBERED.match(prim.GetName())
    return int(m.group(2)) if m else 0


def _source(stage) -> tuple[object, bool]:
    """The group the elements are read from and whether it is the mesh export: `Parametric_grp` when there is one,
    else the parent of an `Arch_grp` (the mesh export's `Mesh_grp`)."""
    for grp in _named(stage, "Parametric_grp"):
        if grp.GetChild("Arch_grp"):
            return grp, False
    arch = _named(stage, "Arch_grp")
    if not arch:
        raise ScanError("not a RoomPlan export: the file has no Arch_grp (RoomPlan's walls, doors and windows)")
    return arch[0].GetParent(), True


def read_roomplan_usdz(path: Path) -> tuple[dict, list[dict], list[str]]:
    """A RoomPlan USDZ export (parametric, else mesh) as (the `vesper-plan/1` in metres, the objects as pieces of
    furniture `{kind, room, box, h, yaw}`, warnings as `ScanWarnings`, whose `heights` hold each door's and window's
    bottom and top), through `scan_roomplan.plan_from_parts`. A ScanError when usd-core
    is not installed, the file cannot be opened, or it holds no RoomPlan walls."""
    path = Path(path)
    stage = _open(path)
    boxes = _Boxes(stage)
    group, mesh = _source(stage)
    warnings: list[str] = []
    if mesh:
        warnings.append("read from RoomPlan's mesh export (no parametric group): walls, doors and windows from the "
                        "boxes of their meshes; RoomPlan leaves the openings out of the mesh export, so none are "
                        "read (the parametric export or the JSON has them)")

    arch = group.GetChild("Arch_grp")
    found: dict[str, list] = {"walls": [], "doors": [], "windows": [], "openings": []}
    unread = 0
    for prim in _under(arch):
        m = ARCH.match(prim.GetName())
        if not m:
            continue
        b = boxes.box(prim, plane=True)
        if b is None:
            unread += 1
            continue
        kind = "walls" if m.group(1) == "Wall" else KINDS[m.group(1)]
        found[kind].append((int(m.group(2)), prim, b))
    for v in found.values():
        v.sort(key=lambda x: x[0])
    if not found["walls"]:
        raise ScanError("not a RoomPlan export: its Arch_grp holds no walls (WallN)")

    floors = [b["top"] for p in _under(group) if FLOOR.match(p.GetName()) and (b := boxes.box(p, plane=False))]
    floor_y = min(floors) if floors else min(b["center"][1] - b["size"][1] / 2 for _, _, b in found["walls"])

    def ends(b) -> tuple[list[float], list[float]]:
        (x, _, z), (ux, uz), half = b["center"], b["axis"], b["size"][0] / 2
        return [x - ux * half, z - uz * half], [x + ux * half, z + uz * half]

    walls, wall_of = [], {}
    for _, prim, b in found["walls"]:
        if b["size"][0] <= 0:
            continue
        a, z = ends(b)
        ident = str(prim.GetPath())
        walls.append({"id": ident, "a": a, "b": z, "height": b["size"][1]})
        wall_of.setdefault(str(prim.GetParent().GetPath()), ident)

    def elements(kind: str) -> list[dict]:
        out = []
        for _, prim, b in found[kind]:
            c, h = b["center"], b["size"][1]
            out.append({"wall": wall_of.get(str(prim.GetParent().GetPath())), "at": [c[0], c[2]],
                        "width": b["size"][0], "bottom": c[1] - h / 2 - floor_y, "top": c[1] + h / 2 - floor_y})
        return out

    objects = []
    obj = group.GetChild("Object_grp")
    for prim in _under(obj) if obj else []:
        parent = prim.GetParent()
        if parent == obj or not prim.IsA(boxes.UsdGeom.Gprim):
            continue
        b = boxes.box(prim, plane=False)
        if b is None:
            unread += 1
            continue
        cat = parent.GetCustomData().get("Category")
        if not isinstance(cat, str) or not cat:
            m = NUMBERED.match(prim.GetName())
            cat = m.group(1) if m else prim.GetName()
        cat = cat[0].lower() + cat[1:]
        c = b["center"]
        objects.append({"category": cat, "center": [c[0], c[1] - floor_y, c[2]], "size": b["size"],
                        "yaw": math.degrees(math.atan2(b["axis"][1], b["axis"][0]))})
    if unread:
        warnings.append(f"{unread} of the export's elements have no geometry VESPER can read (empty, or not standing "
                        f"upright): left out")

    sections = []
    for grp in _named(stage, "Section_grp"):
        for sec in grp.GetChildren():
            m = NUMBERED.match(sec.GetName())
            label = m.group(1) if m else sec.GetName()
            top = sec.GetChild(f"{sec.GetName()}_centerTop")
            at = boxes.UsdGeom.Xformable(top if top else sec).ComputeLocalToWorldTransform(boxes.time) * boxes.fix
            t = at.ExtractTranslation()
            sections.append({"label": label or "unidentified", "center": [t[0], t[2]]})

    plan, pieces, more = plan_from_parts(walls, elements("doors"), elements("windows"), elements("openings"),
                                         sections, objects)
    more[:0] = warnings  # a ScanWarnings: the doors' and windows' heights travel in it
    more.floor_y = floor_y
    return plan, pieces, more


# --- the export as the home's 3D view ----------------------------------------------------------------------------

LEFT_TO_VESPER = re.compile(r"^(Door|Opening)\d+$")  # VESPER draws its own doors (they open and close)


def is_mesh_export(path: Path) -> bool | None:
    """Whether a USDZ is RoomPlan's mesh export (no parametric group): True, False, or None when that cannot be told
    (usd-core missing, the file unreadable or not RoomPlan's)."""
    try:
        return _source(_open(Path(path)))[1]
    except (ScanError, ImportError):
        return None


def write_glb(path: Path, out: Path, matrix) -> int:
    """The export's geometry (each mesh, or each cube of the parametric export) in the stage's frame turned to y up
    and metres, moved by `matrix` (row-major 4x4 on column vectors: the stage to the home), written as a glTF binary
    at `out`, each prim one node coloured by its displayColor (and displayOpacity). RoomPlan's doors and openings are
    left out: VESPER draws its own doors, which open. Returns how many prims were drawn; a ScanError when none is."""
    Gf, Usd, UsdGeom = _pxr()
    try:
        import numpy as np
        import trimesh
    except ImportError as e:
        raise ScanError(f"writing the 3D view needs trimesh and numpy: {EXTRA}") from e
    stage = _open(Path(path))
    fix, time = _correction(stage), Usd.TimeCode.Default()
    m = np.asarray(matrix, dtype=np.float64)
    scene = trimesh.Scene()
    names: set[str] = set()
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Gprim) or LEFT_TO_VESPER.match(prim.GetName()):
            continue
        img = UsdGeom.Imageable(prim)
        if img.ComputeVisibility(time) == UsdGeom.Tokens.invisible or img.ComputePurpose() == UsdGeom.Tokens.guide:
            continue
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            pts = np.asarray(mesh.GetPointsAttr().Get(time) or [], dtype=np.float64).reshape(-1, 3)
            counts = list(mesh.GetFaceVertexCountsAttr().Get(time) or [])
            idx = list(mesh.GetFaceVertexIndicesAttr().Get(time) or [])
            faces, at = [], 0
            for n in counts:  # each polygon as a fan of triangles
                faces += [[idx[at], idx[at + i], idx[at + i + 1]] for i in range(1, n - 1)]
                at += n
        elif prim.IsA(UsdGeom.Cube):
            size = UsdGeom.Cube(prim).GetSizeAttr().Get(time) or 2.0
            box = trimesh.creation.box(extents=[size] * 3)
            pts, faces = np.asarray(box.vertices, dtype=np.float64), box.faces.tolist()
        else:
            continue
        if not faces or not len(pts):
            continue
        world = np.asarray(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time) * fix, dtype=np.float64)
        v = np.c_[pts, np.ones(len(pts))] @ world  # USD's row vectors
        v = (np.c_[v[:, :3], np.ones(len(v))] @ m.T)[:, :3]
        if np.linalg.det(world[:3, :3] @ m[:3, :3].T) < 0:  # a mirroring transform: keep the faces outward
            faces = [f[::-1] for f in faces]
        tri = trimesh.Trimesh(vertices=v, faces=np.asarray(faces), process=False)
        pv = UsdGeom.PrimvarsAPI(prim)
        color = pv.GetPrimvar("displayColor").Get(time) if pv.HasPrimvar("displayColor") else None
        alpha = pv.GetPrimvar("displayOpacity").Get(time) if pv.HasPrimvar("displayOpacity") else None
        rgb = [float(c) for c in color[0]] if color else [0.85, 0.85, 0.85]
        a = float(alpha[0]) if alpha else 1.0
        tri.visual.face_colors = [int(round(255 * min(max(c, 0.0), 1.0))) for c in (*rgb, a)]
        name = prim.GetName()
        k = 2
        while name in names:
            name, k = f"{prim.GetName()}_{k}", k + 1
        names.add(name)
        scene.add_geometry(tri, node_name=name, geom_name=name)
    if not scene.geometry:
        raise ScanError(f"{Path(path).name} has no geometry to draw")
    Path(out).write_bytes(scene.export(file_type="glb"))
    return len(scene.geometry)
