"""Apple RoomPlan USDZ exports (Polycam's Room mode, the 3D Scanner App) read into a `vesper-plan/1` through task B1's
`plan_from_parts` (scan import, task B2). The fixtures are written by the tests with usd-core from B1's hand-written
two-room CapturedRoom, so the USDZ and the JSON readers must agree; the real scans under .vesper/datasets/scans/ are
read only when they are there."""
import json
import math
import sys
from pathlib import Path

import pytest

from vesper.lab.geometry import polygon_area
from vesper.lab.scan_roomplan import ScanError, read_roomplan_json
from vesper.lab.scan_usd import read_roomplan_usdz

from tests.lab.test_scan_roomplan import SCANS  # $VESPER_SCANS, else the main checkout's test set

FIXTURES = Path(__file__).parent / "fixtures" / "roomplan"

KINDS = (("walls", "Wall"), ("doors", "Door"), ("windows", "Window"), ("openings", "Opening"))
THICK = 0.16  # how thick RoomPlan's mesh export draws a wall (a door or window half that)


def fixture() -> dict:
    return json.loads((FIXTURES / "two_rooms.json").read_text())


def area(room) -> float:
    return polygon_area([tuple(p) for p in room["polygon"]])


def _category(it) -> str:
    return next(iter(it["category"]))


def write_usd(raw: dict, path: Path, export: str = "parametric", up: str = "Y", mpu: float = 1.0) -> Path:
    """A RoomPlan CapturedRoom (JSON) written as RoomPlan's USDZ export writes it: `/room_p/Parametric_grp` (unit
    cubes scaled to each element's dimensions) or `/room/Mesh_grp` (meshes drawn THICK behind each element's plane,
    a wall carried on by THICK past one end, no openings). Doors, windows and openings stand in their wall's
    `Wall_N_grp` and carry the wall's Category, as composition leaves them in Apple's files. `up`/`mpu`: the stage's
    up axis and metres per unit, the whole room put under a root transform that undoes them."""
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom
    path.unlink(missing_ok=True)
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z if up == "Z" else UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, mpu)
    rootname = "/room_p" if export == "parametric" else "/room"
    root = UsdGeom.Xform.Define(stage, rootname)
    stage.SetDefaultPrim(root.GetPrim())
    if up == "Z" or mpu != 1.0:
        to_z = Gf.Matrix4d(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1) if up == "Z" else Gf.Matrix4d(1)
        root.AddTransformOp().Set(to_z * Gf.Matrix4d().SetScale(1.0 / mpu))
    group = f"{rootname}/{'Parametric_grp' if export == 'parametric' else 'Mesh_grp'}"

    def matrix(t):
        return Gf.Matrix4d(*[float(v) for v in t])  # column-major JSON = USD's row-vector rows

    def box_mesh(where, lo, hi):
        mesh = UsdGeom.Mesh.Define(stage, where)
        pts = [Gf.Vec3f(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        mesh.CreatePointsAttr(pts)
        mesh.CreateFaceVertexCountsAttr([4] * 6)
        mesh.CreateFaceVertexIndicesAttr([0, 1, 3, 2, 4, 6, 7, 5, 0, 4, 5, 1, 2, 3, 7, 6, 0, 2, 6, 4, 1, 5, 7, 3])
        mesh.CreateExtentAttr([Gf.Vec3f(*lo), Gf.Vec3f(*hi)])
        return mesh

    def element(where, it, kind, category):
        t, (w, h, d) = it["transform"], it["dimensions"]
        if export == "parametric":
            cube = UsdGeom.Cube.Define(stage, where)
            cube.CreateSizeAttr(1.0)
            cube.AddTransformOp().Set(matrix(t))
            cube.AddScaleOp().Set(Gf.Vec3f(w, h, d if d > 0 else 0.0005))
            prim = cube.GetPrim()
        else:
            if kind == "objects":
                lo, hi = (-w / 2, -h / 2, -d / 2), (w / 2, h / 2, d / 2)
            elif kind == "walls":
                lo, hi = (-w / 2, -h / 2, -THICK), (w / 2 + THICK, h / 2, 0.0)
            else:
                lo, hi = (-w / 2, -h / 2, -THICK / 2), (w / 2, h / 2, 0.0)
            mesh = box_mesh(where, lo, hi)
            mesh.AddTransformOp().Set(matrix(t))
            prim = mesh.GetPrim()
        prim.SetCustomDataByKey("Category", category)

    walls = {w["identifier"]: k for k, w in enumerate(raw["walls"])}
    for k, w in enumerate(raw["walls"]):
        grp = UsdGeom.Xform.Define(stage, f"{group}/Arch_grp/Wall_{k}_grp").GetPrim()
        grp.SetCustomDataByKey("Category", "Wall")
        grp.SetCustomDataByKey("UUID", w["identifier"])
        element(f"{group}/Arch_grp/Wall_{k}_grp/Wall{k}", w, "walls", "Wall")
    for kind, name in KINDS[1:]:
        if kind == "openings" and export != "parametric":
            continue  # RoomPlan's mesh export leaves the openings out
        for n, it in enumerate(raw[kind]):
            k = walls.get(it.get("parentIdentifier"))
            where = f"{group}/Arch_grp/Wall_{k}_grp" if k is not None else f"{group}/Arch_grp/Loose_{kind}_grp"
            UsdGeom.Xform.Define(stage, where)
            element(f"{where}/{name}{n}", it, kind, "Wall")
    for n, f in enumerate(raw["floors"]):
        grp = UsdGeom.Xform.Define(stage, f"{group}/Floor_grp").GetPrim()
        grp.SetCustomDataByKey("Category", "Floor")
        w, d = f["dimensions"][0], f["dimensions"][1]
        mesh = box_mesh(f"{group}/Floor_grp/Floor{n}", (-w / 2, -d / 2, -THICK if export == "mesh" else 0.0),
                        (w / 2, d / 2, 0.0))
        mesh.AddTransformOp().Set(matrix(f["transform"]))
    count: dict[str, int] = {}
    for o in raw["objects"]:
        cat = _category(o)
        cap = cat[0].upper() + cat[1:]
        grp = UsdGeom.Xform.Define(stage, f"{group}/Object_grp/{cap}_grp").GetPrim()
        grp.SetCustomDataByKey("Category", cap)
        count[cap] = count.get(cap, 0) + 1
        element(f"{group}/Object_grp/{cap}_grp/{cap}{count[cap] - 1}", o, "objects", cap)
    labels: dict[str, int] = {}
    for s in raw["sections"]:
        n = labels.get(s["label"], 0)
        labels[s["label"]] = n + 1
        name = f"{s['label']}{n}"
        sec = UsdGeom.Xform.Define(stage, f"{rootname}/Section_grp/{name}")
        turn = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), 33.0))
        sec.AddTransformOp().Set(turn * Gf.Matrix4d().SetTranslate(Gf.Vec3d(*s["center"])))
        top = UsdGeom.Xform.Define(stage, f"{rootname}/Section_grp/{name}/{name}_centerTop")
        top.AddTransformOp().Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(0, 1.2, 0)))
    stage.GetRootLayer().Save()
    if path.suffix == ".usdz":
        raise AssertionError("write a .usda and package it with usdz()")
    return path


def usdz(usda: Path) -> Path:
    pytest.importorskip("pxr")
    from pxr import Sdf, UsdUtils
    out = usda.with_suffix(".usdz")
    assert UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(usda)), str(out))
    return out


def same_plan(got: dict, want: dict) -> None:
    """Two plans equal up to the order of their doors and windows and float rounding."""
    assert [(r["id"], r["name"], r["type"]) for r in got["rooms"]] == \
        [(r["id"], r["name"], r["type"]) for r in want["rooms"]]
    for a, b in zip(got["rooms"], want["rooms"], strict=True):
        assert len(a["polygon"]) == len(b["polygon"])
        assert area(a) == pytest.approx(area(b), rel=1e-4)

    def key(d):
        return (tuple(round(v, 3) for v in d["at"]), round(d["width"], 3), tuple(d.get("rooms") or [d.get("room")]))
    assert sorted(map(key, got["doors"])) == sorted(map(key, want["doors"]))
    assert sorted(map(key, got["windows"])) == sorted(map(key, want["windows"]))
    assert (got["entrance"] is None) == (want["entrance"] is None)
    if want["entrance"] is not None:
        assert key(got["doors"][got["entrance"]]) == key(want["doors"][want["entrance"]])


def same_heights(plan: dict, warnings, want_plan: dict, want_warnings) -> None:
    """Each door's and window's bottom and top (ScanWarnings.heights, in the plan's order) as the JSON reader gives
    them, matched by position."""
    for kind in ("doors", "windows"):
        assert len(warnings.heights[kind]) == len(plan[kind])

        def by_at(p, w):
            return {tuple(round(v, 3) for v in d["at"]): h for d, h in zip(p[kind], w.heights[kind], strict=True)}
        got, want = by_at(plan, warnings), by_at(want_plan, want_warnings)
        assert got.keys() == want.keys()
        for at, h in want.items():
            assert got[at] == pytest.approx(h, abs=1e-4), (kind, at)


def same_pieces(got: list[dict], want: list[dict]) -> None:
    def key(p):
        return (p["kind"], p["room"], tuple(round(v, 3) for v in p["box"]), round(p["h"], 3), round(p["yaw"], 2))
    assert sorted(map(key, got)) == sorted(map(key, want))


# --- the two-room fixture as RoomPlan's parametric export ------------------------------------------------------

@pytest.mark.parametrize("packaged", [False, True])
def test_the_parametric_export_gives_the_plan_the_json_gives(tmp_path, packaged):
    """Doors and windows carry their wall's Category (composition), so they are told apart by their prim names."""
    raw = fixture()
    path = write_usd(raw, tmp_path / "room_p.usda")
    if packaged:
        path = usdz(path)
    plan, pieces, warnings = read_roomplan_usdz(path)
    want_plan, want_pieces, want_warnings = read_roomplan_json(raw)
    same_plan(plan, want_plan)
    same_pieces(pieces, want_pieces)
    assert warnings == want_warnings
    same_heights(plan, warnings, want_plan, want_warnings)


def test_an_opening_on_its_wall_and_short_walls_as_the_json_reads_them(tmp_path):
    raw = fixture()
    front = next(d for d in raw["doors"] if d["identifier"] == "D-front")
    raw["doors"].remove(front)
    raw["openings"].append({**front, "identifier": "O-front", "category": {"opening": {}}})
    for w, h in zip(raw["walls"], (1.59, 2.09, 2.42, 2.42, 2.09, 1.59, 2.42), strict=True):
        w["dimensions"][1] = h
        w["transform"][13] = -1.2 + h / 2
    plan, pieces, warnings = read_roomplan_usdz(write_usd(raw, tmp_path / "room_p.usda"))
    want_plan, want_pieces, want_warnings = read_roomplan_json(raw)
    same_plan(plan, want_plan)
    same_pieces(pieces, want_pieces)
    assert warnings == want_warnings
    same_heights(plan, warnings, want_plan, want_warnings)
    assert any(w.startswith("ceiling not scanned") and "2.42" in w for w in warnings), warnings


def test_a_turned_scan_in_centimetres_with_z_up_gives_the_same_plan(tmp_path):
    raw = fixture()
    plan, pieces, warnings = read_roomplan_usdz(write_usd(raw, tmp_path / "room_p.usda", up="Z", mpu=0.01))
    want_plan, want_pieces, want_warnings = read_roomplan_json(raw)
    same_plan(plan, want_plan)
    same_pieces(pieces, want_pieces)
    assert warnings == want_warnings
    same_heights(plan, warnings, want_plan, want_warnings)


# --- the mesh export: no parametric group -------------------------------------------------------------------------

def test_the_mesh_export_gives_the_same_rooms_doors_and_windows_and_says_what_it_lacks(tmp_path):
    raw = fixture()
    plan, pieces, warnings = read_roomplan_usdz(write_usd(raw, tmp_path / "room.usda", export="mesh"))
    want_plan, want_pieces, want_warnings = read_roomplan_json(raw)
    same_plan(plan, want_plan)
    same_pieces(pieces, want_pieces)
    same_heights(plan, warnings, want_plan, want_warnings)
    assert any("mesh export" in w and "openings" in w for w in warnings), warnings


# --- refusals ----------------------------------------------------------------------------------------------------

def test_without_usd_core_the_reader_names_the_extra(tmp_path, monkeypatch):
    path = tmp_path / "room.usdz"
    path.write_bytes(b"PK\x03\x04")
    monkeypatch.setitem(sys.modules, "pxr", None)
    with pytest.raises(ScanError, match=r"pip install 'vesper\[scan\]'"):
        read_roomplan_usdz(path)


def test_a_file_usd_core_cannot_open_is_refused_not_a_crash(tmp_path):
    pytest.importorskip("pxr")
    path = tmp_path / "room.usdz"
    path.write_bytes(b"PK\x03\x04 this is no usdz at all")
    with pytest.raises(ScanError, match="cannot be read"):
        read_roomplan_usdz(path)
    with pytest.raises(ScanError, match="no such file"):
        read_roomplan_usdz(tmp_path / "missing.usdz")


def test_a_usd_file_with_no_roomplan_walls_is_refused(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    path = tmp_path / "cube.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.Cube.Define(stage, "/World/Box")
    stage.GetRootLayer().Save()
    with pytest.raises(ScanError, match="not a RoomPlan export"):
        read_roomplan_usdz(path)


# --- the real scans (skipped when the test set is not on this machine) ----------------------------------------

def scan_file(name: str) -> Path:
    pytest.importorskip("pxr")
    path = SCANS / name
    if not path.is_file():
        pytest.skip(f"{path} is not here")
    return path


@pytest.mark.parametrize("folder", ["litereality-darwin-bedroom-roomplan", "litereality-girton-common-room-roomplan",
                                    "litereality-cued-meeting-room-roomplan"])
def test_the_litereality_parametric_export_agrees_with_its_json(folder):
    usd = scan_file(f"{folder}/roomplan/room_p.usdz")
    plan, pieces, warnings = read_roomplan_usdz(usd)
    want, want_pieces, want_warnings = read_roomplan_json(json.loads((usd.parent / "room.json").read_text()))
    assert [r["type"] for r in plan["rooms"]] == [r["type"] for r in want["rooms"]]
    assert sum(map(area, plan["rooms"])) == pytest.approx(sum(map(area, want["rooms"])), rel=0.03)
    assert len(plan["doors"]) == len(want["doors"]) and len(plan["windows"]) == len(want["windows"])
    assert (plan["entrance"] is None) == (want["entrance"] is None)
    assert len(pieces) == len(want_pieces)
    same_heights(plan, warnings, want, want_warnings)


def test_the_litereality_bedroom_mesh_export_has_the_same_room():
    usd = scan_file("litereality-darwin-bedroom-roomplan/roomplan/room.usdz")
    plan, _, warnings = read_roomplan_usdz(usd)
    want, _, _ = read_roomplan_json(json.loads((usd.parent / "room.json").read_text()))
    assert [r["type"] for r in plan["rooms"]] == ["bedroom"]
    assert area(plan["rooms"][0]) == pytest.approx(area(want["rooms"][0]), rel=0.03)
    assert len(plan["doors"]) == 2 and len(plan["windows"]) == 1
    assert any("mesh export" in w for w in warnings)


def test_the_domotwin_usdz_usd_core_cannot_open_is_refused():
    with pytest.raises(ScanError, match="cannot be read"):
        read_roomplan_usdz(scan_file("synthetic-domotwin-structure-roomplan/structure.usdz"))


def test_the_yaw_of_a_turned_object_is_kept(tmp_path):
    raw = fixture()
    t = math.radians(30.0)
    bed = next(o for o in raw["objects"] if o["identifier"] == "O-bed")
    bed["transform"][:12] = [math.cos(t), 0, math.sin(t), 0, 0, 1, 0, 0, -math.sin(t), 0, math.cos(t), 0]
    _, pieces, _ = read_roomplan_usdz(write_usd(raw, tmp_path / "room_p.usda"))
    [got] = [p for p in pieces if p["kind"] == "bed"]
    assert got["yaw"] == pytest.approx(30.0, abs=1e-3)
    assert got["box"] == pytest.approx([5.95, 0.075, 7.55, 2.125], abs=1e-4)
