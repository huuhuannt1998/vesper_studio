"""`vesper lab import` of a scan or a 3D model (scan import, task B4): a RoomPlan JSON or USDZ, or any mesh (GLB,
glTF, OBJ, PLY), becomes a home as a floor plan does, its objects become the home's furniture (`source: scan`), a
mesh becomes the home's 3D view (`scene.glb`, in the plan's frame), the original is kept in `source/`, and both stay
out of git (`<home>/.gitignore`). Every fixture is hand-made; the real scans are read only when they are there."""
import copy
import json
import math
import sys
from pathlib import Path

import pytest

from tests.lab.test_cli_lab import setup
from tests.lab.test_import import reachable_rooms
from tests.lab.test_scan_roomplan import SCANS
from vesper import cli
from vesper.lab.home import load_lab_home
from vesper.lab.windows import load_windows
from vesper.twins.home import load_catalog

FIXTURES = Path(__file__).parent / "fixtures" / "roomplan"


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """No test runs the real `claude` (a paid reading): it is not on PATH, so a 3D model's default reader is the
    geometric one, and calling it fails the test. A test of Claude's reader puts a fake in its place."""
    import shutil as sh

    from vesper.lab import plan_read
    real = sh.which
    monkeypatch.setattr(sh, "which", lambda name, *a, **k: None if name == "claude" else real(name, *a, **k))

    def refuse(*a, **k):
        raise AssertionError("a test ran the real claude")
    monkeypatch.setattr(plan_read, "_claude", refuse)


def fixture() -> dict:
    return json.loads((FIXTURES / "two_rooms.json").read_text())


def write(tmp_path, raw, name="two_rooms.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(raw))
    return path


def items(out: Path) -> list[dict]:
    return json.loads((out / "furniture.json").read_text())["items"]


def turned(raw: dict, degrees: float) -> dict:
    """The RoomPlan file turned about y by `degrees` (a scan is seldom square to the world): every transform and
    section centre."""
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    q = copy.deepcopy(raw)

    def rot(x, z):
        return x * c - z * s, x * s + z * c
    for key in ("walls", "doors", "windows", "openings", "floors", "objects"):
        for it in q.get(key) or []:
            m = it["transform"]  # column-major: columns 0-2 the axes, 3 the position
            for col in range(4):
                x, z = rot(m[4 * col], m[4 * col + 2])
                m[4 * col], m[4 * col + 2] = x, z
    for sec in q["sections"]:
        sec["center"][0], sec["center"][2] = rot(sec["center"][0], sec["center"][2])
    return q


# --- a RoomPlan JSON ---------------------------------------------------------------------------------------------

def test_a_roomplan_json_imports_with_no_scale_and_its_objects_are_the_furniture(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "scan"
    src = write(tmp_path, fixture())
    assert cli.main(["lab", "import", str(src), str(out), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert "2 rooms (living, bedroom)" in said and "RoomPlan" in said
    assert "entrance: door" in said
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    assert {r.id for r in home.map.rooms} == {"living", "bedroom", "porch"}
    assert reachable_rooms(home.map, home.avatar["start"]) == {"living", "bedroom", "porch"}  # with the furniture
    got = items(out)
    assert all(i["source"] == "scan" for i in got)  # both rooms have scan objects: no built-in furniture
    assert sorted((i["kind"], i["room"]) for i in got) == [("bed", "bedroom"), ("cabinet", "bedroom"),
                                                            ("sofa", "living")]
    bed = next(i for i in got if i["kind"] == "bed")
    assert (bed["x"], bed["z"]) == (pytest.approx(6.75, abs=0.01), pytest.approx(1.1, abs=0.01))
    assert bed["box"] == [pytest.approx(v, abs=0.01) for v in (5.95, 0.075, 7.55, 2.125)]
    assert bed["h"] == pytest.approx(0.55) and bed["solid"]
    wardrobe = next(i for i in got if i["kind"] == "cabinet")  # turned a quarter: its 1.2 m width along z
    assert wardrobe["box"] == [pytest.approx(v, abs=0.01) for v in (7.85, 2.4, 8.45, 3.6)]
    # the floor under the scan's furniture is off the walking map
    assert not home.map.grid.is_walkable(*home.map.grid.cell_of(6.75, 1.1))
    assert json.loads((out / "map.original.json").read_text())["bare"] is True
    # the windows' sills and heights as scanned, the doors' in the home's provenance
    scanned = {w["room"]: w for w in load_windows(out) if w["source"] == "scan"}
    assert scanned["living"]["sill"] == pytest.approx(0.9) and scanned["living"]["height"] == pytest.approx(1.2)
    prov = json.loads((out / "scan.json").read_text())
    assert prov["format"] == "vesper-scan/1" and prov["kind"] == "roomplan-json"
    assert sorted((d["bottom"], d["top"]) for d in prov["doors"]) == [(0.0, 2.03), (0.0, 2.03)]
    # the original kept, out of git; no model to draw (a JSON has no geometry): the built-in view
    assert (out / "source" / "two_rooms.json").read_text() == src.read_text()
    assert set((out / ".gitignore").read_text().split()) == {"scene.glb", "source/"}
    assert not (out / "scene.glb").exists()
    assert cli.main(["lab", "check", str(out / "home.yaml"), "--catalog", str(cat)]) == 0


def test_an_imported_roomplan_home_passes_the_layout_check(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(write(tmp_path, fixture())), str(out), "--catalog", str(cat)]) == 0
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert "0 error(s)" in said and "3 piece(s) from furniture.json" in said
    # the fixture's sofa stands by the front door and its wardrobe before the bedroom window, as scanned: said as
    # warnings (the real home's furniture), not errors of VESPER's layout
    assert "warn  door: doorway living-porch: sofa (sofa@living) 0.15 m from it (the scan's own furniture" in said
    assert "warn  window: window bedroom_window: cabinet" in said


def test_a_rule_the_scan_s_furniture_alone_breaks_is_a_warning_and_one_ours_breaks_an_error(tmp_path):
    from vesper.lab.layoutcheck import check
    cat, _ = setup(tmp_path)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(write(tmp_path, fixture())), str(out), "--catalog", str(cat)]) == 0
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    sofa = {"name": "sofa@living", "kind": "sofa", "room": "living", "box": [1.45, 2.95, 3.55, 3.85], "h": 0.85}
    assert check(home, pieces=[{**sofa, "scan": True}])["errors"] == 0
    report = check(home, pieces=[sofa])
    assert report["errors"] == 1 and next(f for f in report["findings"] if f["rule"] == "door" and not f["ok"])[
        "severity"] == "error"
    wall = {"name": "cupboard@living", "kind": "cabinet", "room": "living", "box": [0.1, 0.1, 4.9, 3.9], "h": 2.0,
            "scan": True}  # the whole room filled: no way through it, the scan's doing
    report = check(home, pieces=[wall])
    assert report["errors"] == 0
    assert any(f["rule"] in ("walkway", "reach") and not f["ok"] and "the scan's own furniture" in f["message"]
               for f in report["findings"])


def test_a_scanned_piece_faces_away_from_the_wall_its_back_stands_against(tmp_path):
    """RoomPlan's object front is its +z (a bed's head, a wardrobe's back are its -z end, against a wall): the
    view's front, (cos facing, sin facing), points into the room. The fixture's bed has its head on the north wall
    (z = 0), its wardrobe its back on the east wall (x = 8.5)."""
    cat, _ = setup(tmp_path)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(write(tmp_path, fixture())), str(out), "--catalog", str(cat)]) == 0
    got = {i["kind"]: i for i in items(out)}

    def front(item):
        t = math.radians(item["facing"])
        return round(math.cos(t), 6) + 0.0, round(math.sin(t), 6) + 0.0
    assert front(got["bed"]) == (0.0, 1.0)  # south, away from the north wall
    assert front(got["cabinet"]) == (-1.0, 0.0)  # west, away from the east wall


def test_devices_are_placed_clear_of_the_scan_s_furniture(tmp_path):
    """A wall device the rules would put behind a scanned piece moves to the nearest free point by a wall; a home
    with no scanned piece in the way places its devices exactly as before."""
    from vesper.lab.generate import AVOID_M, Request, floor_plan_from, place_devices, starter, typed_ids
    from vesper.lab.plan import rectify
    from vesper.lab.scan_roomplan import read_roomplan_json
    cat = load_catalog(setup(tmp_path)[0])
    plan, _, _ = read_roomplan_json(fixture())
    plan = typed_ids(rectify(plan))[0]
    m = floor_plan_from(plan, "imported/x")
    request = Request(Path("."), ["living", "bedroom"], 0, starter(["living", "bedroom"], cat)[0])
    free = place_devices(m, request, cat)
    assert place_devices(m, request, cat, avoid=[]) == free
    wall = next(d for d in free if d["at"].get("mount") == "wall")
    x, z = wall["at"]["xy"]
    box = [x - 0.4, z - 0.4, x + 0.4, z + 0.4]  # a cabinet right where it would go
    moved = next(d for d in place_devices(m, request, cat, avoid=[box]) if d["name"] == wall["name"])
    mx, mz = moved["at"]["xy"]
    assert not (box[0] - AVOID_M < mx < box[2] + AVOID_M and box[1] - AVOID_M < mz < box[3] + AVOID_M)
    assert math.dist((mx, mz), (x, z)) < 1.0
    room = m.room(wall["at"]["room"])
    from vesper.lab.generate import _edges, _gap
    assert min(_gap(e, (mx, mz)) for e in _edges(list(room.polygon))) <= 0.25  # still on a wall


def test_built_in_furniture_goes_only_into_rooms_the_scan_has_no_objects_in(tmp_path):
    cat, _ = setup(tmp_path)
    raw = fixture()
    raw["objects"] = [o for o in raw["objects"] if o["identifier"] == "O-sofa"]  # the bedroom left empty
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(write(tmp_path, raw)), str(out), "--catalog", str(cat)]) == 0
    got = items(out)
    assert [(i["kind"], i["room"]) for i in got if i.get("source") == "scan"] == [("sofa", "living")]
    assert not any(i["room"] == "living" and i.get("source") != "scan" for i in got)
    assert any(i["kind"] == "bed" and i["room"] == "bedroom" and i.get("source") != "scan" for i in got)


def test_a_turned_scan_s_objects_turn_with_its_walls(tmp_path):
    """Rectify turns the plan square; each object's centre turns with it and its yaw takes the same angle, so the
    bed stands where it stood in its room, square to the walls."""
    cat, _ = setup(tmp_path)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(write(tmp_path, turned(fixture(), 31))), str(out),
                     "--catalog", str(cat)]) == 0
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    xs = [p[0] for p in home.map.room("bedroom").polygon]
    zs = [p[1] for p in home.map.room("bedroom").polygon]
    bed = next(i for i in items(out) if i["kind"] == "bed")
    # the bedroom is x 5-8.5, z 0-4 to its walls' centre lines (its polygon 0.1 inside them): the bed at (6.75, 1.1)
    assert bed["x"] - (min(xs) - 0.1) == pytest.approx(1.75, abs=0.05)
    assert bed["z"] - (min(zs) - 0.1) == pytest.approx(1.1, abs=0.05)
    assert bed["facing"] % 90 == 0
    x0, z0, x1, z1 = bed["box"]
    assert sorted([x1 - x0, z1 - z0]) == [pytest.approx(1.6, abs=0.01), pytest.approx(2.05, abs=0.01)]


def test_a_large_measured_room_is_built_with_a_warning_not_refused_for_its_scale(tmp_path, capsys):
    """A 34 m² room labelled a dining room is 2.6 times a dining room's usual size: a drawing would be refused as a
    wrong scale, a scan is in metres, so it is said and built (the Cued meeting room)."""
    cat, _ = setup(tmp_path)
    raw = fixture()
    raw["walls"] = [w for w in raw["walls"] if w["identifier"] != "W-middle"]
    raw["doors"] = [d for d in raw["doors"] if d["identifier"] != "D-between"]
    raw["sections"] = [{"label": "diningRoom", "center": [4.25, -1.2, 2.0], "story": 0}]
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(write(tmp_path, raw)), str(out), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert "warning: the rooms are on the whole 2.6 times their usual size: measured in metres" in said
    assert "1 rooms (dining)" in said or "1 room (dining)" in said


def test_a_scan_takes_no_pixel_scale_and_types_must_match_its_rooms(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    src = write(tmp_path, fixture())
    assert cli.main(["lab", "import", str(src), str(tmp_path / "a"), "--scale", "2", "--catalog", str(cat)]) == 1
    assert "in metres" in capsys.readouterr().err
    assert cli.main(["lab", "import", str(src), str(tmp_path / "b"), "--types", "kitchen", "--catalog",
                     str(cat)]) == 1
    assert "2 rooms" in capsys.readouterr().err
    assert cli.main(["lab", "import", str(src), str(tmp_path / "c"), "--types", "kitchen,garage", "--catalog",
                     str(cat)]) == 1
    assert "'garage'" in capsys.readouterr().err
    assert not any((tmp_path / n).exists() for n in "abc")
    assert cli.main(["lab", "import", str(src), str(tmp_path / "d"), "--types", "kitchen,office", "--catalog",
                     str(cat)]) == 0
    assert "2 rooms (kitchen, office)" in capsys.readouterr().out


# --- a RoomPlan USDZ ---------------------------------------------------------------------------------------------

def assert_view_on_the_plan(out: Path, floor=(-0.05, 0.05)):
    """scene.glb's nodes stand on the home's rooms (their outline, walls drawn about it), its floor at 0, with no
    RoomPlan door in it (VESPER draws the doors)."""
    from vesper.lab.layoutcheck import glb_boxes
    plan = json.loads((out / "plan.json").read_text())
    xs = [p[0] for r in plan["rooms"] for p in r["polygon"]]
    zs = [p[1] for r in plan["rooms"] for p in r["polygon"]]
    boxes = glb_boxes(out / "scene.glb")
    assert boxes and not any(n.startswith(("Door", "Opening")) for n, _ in boxes)
    for name, (x0, y0, z0, x1, y1, z1) in boxes:
        assert min(xs) - 0.25 <= x0 <= x1 <= max(xs) + 0.25 and min(zs) - 0.25 <= z0 <= z1 <= max(zs) + 0.25, name
    floors = [b for n, b in boxes if n.startswith("Floor")]  # the floor's top (its slab drawn below it) at 0
    assert floor[0] <= (max(b[4] for b in floors) if floors else min(b[1] for _, b in boxes)) <= floor[1]
    return boxes


def phone_export(tmp_path, export="mesh", with_json=True) -> Path:
    """B1's fixture as a RoomPlan app writes it: room.usdz (mesh export) or room_p.usdz, and room.json beside."""
    pytest.importorskip("pxr")
    pytest.importorskip("trimesh")
    from tests.lab.test_scan_usd import usdz, write_usd
    folder = tmp_path / "phone"
    folder.mkdir(exist_ok=True)
    given = usdz(write_usd(fixture(), folder / ("room.usda" if export == "mesh" else "room_p.usda"), export=export))
    if with_json:
        write(folder, fixture(), "room.json")
    return given


def test_a_mesh_export_usdz_beside_its_json_reads_the_json_and_draws_the_export(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    given = phone_export(tmp_path)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(given), str(out), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert "room.usdz is RoomPlan's mesh export" in said and "room.json beside it is read instead" in said
    prov = json.loads((out / "scan.json").read_text())
    assert prov["kind"] == "roomplan-json" and prov["scene"] == "scene.glb"
    assert {p.name for p in (out / "source").iterdir()} == {"room.usdz", "room.json"}
    names = {n for n, _ in assert_view_on_the_plan(out)}
    assert {"Wall0", "Window0"} <= names and any(n.startswith("Bed") for n in names)
    assert [i["source"] for i in items(out)] == ["scan"] * 3  # with the scan drawn, no built-in furniture


def test_a_roomplan_json_with_its_mesh_export_beside_is_drawn_by_it(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    phone_export(tmp_path)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(tmp_path / "phone" / "room.json"), str(out), "--catalog", str(cat)]) == 0
    assert "3D view:" in capsys.readouterr().out
    assert_view_on_the_plan(out)
    assert {p.name for p in (out / "source").iterdir()} == {"room.json", "room.usdz"}  # the view's source kept too
    cat2 = tmp_path / "alone"
    cat2.mkdir()
    write(cat2, fixture(), "room.json")  # a JSON alone: no geometry, VESPER draws the home, and nothing to say
    assert cli.main(["lab", "import", str(cat2 / "room.json"), str(tmp_path / "plain"), "--catalog", str(cat)]) == 0
    assert "scene.glb is left out" not in capsys.readouterr().out and not (tmp_path / "plain" / "scene.glb").exists()


def test_a_parametric_usdz_alone_is_drawn_from_its_boxes(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    given = phone_export(tmp_path, export="parametric", with_json=False)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(given), str(out), "--catalog", str(cat)]) == 0
    assert "2 rooms (living, bedroom)" in capsys.readouterr().out
    assert_view_on_the_plan(out)
    assert (out / "source" / "room_p.usdz").is_file()
    assert sorted(i["kind"] for i in items(out)) == ["bed", "cabinet", "sofa"]


def test_without_usd_core_a_usdz_is_built_with_vesper_s_view_and_says_so(tmp_path, capsys, monkeypatch):
    cat, _ = setup(tmp_path)
    phone_export(tmp_path)
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pxr" else real(name, *a))
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(tmp_path / "phone" / "room.json"), str(out), "--catalog", str(cat)]) == 0
    assert "warning: scene.glb is left out: making the 3D view from the USDZ needs usd-core" in \
        capsys.readouterr().out
    assert not (out / "scene.glb").exists()


# --- any mesh ----------------------------------------------------------------------------------------------------

def mesh_house(tmp_path, degrees=0.0, shift=(0.0, 0.0, 0.0), name="house.glb") -> Path:
    """Task B3's two-room box house (x 0-8, z 0-4 to the walls' centre lines), turned about y and moved."""
    pytest.importorskip("trimesh")
    pytest.importorskip("scipy")
    from tests.lab.test_scan_mesh import house, save
    import trimesh
    mesh = house()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.radians(degrees), [0, 1, 0]))
    mesh.apply_translation(shift)
    return save(mesh, tmp_path / name)


def test_a_mesh_imports_and_becomes_the_home_s_3d_view_in_the_plan_s_frame(tmp_path, capsys):
    from vesper.lab.layoutcheck import glb_boxes
    cat, _ = setup(tmp_path)
    src = mesh_house(tmp_path, degrees=25, shift=(10.0, 3.0, -7.0))
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(src), str(out), "--types", "living,bedroom", "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert "2 rooms (living, bedroom)" in said and "scene.glb" in said
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    assert reachable_rooms(home.map, home.avatar["start"]) == {"living", "bedroom", "porch"}
    plan = json.loads((out / "plan.json").read_text())
    xs = [p[0] for r in plan["rooms"] for p in r["polygon"]]
    zs = [p[1] for r in plan["rooms"] for p in r["polygon"]]
    boxes = glb_boxes(out / "scene.glb")
    assert boxes
    lo = [min(b[k] for _, b in boxes) for k in range(3)]
    hi = [max(b[3 + k] for _, b in boxes) for k in range(3)]
    # the model's walls stand on the rooms' outline (their 12 cm thickness about it), its floor at 0
    for got, want in ((lo[0], min(xs)), (hi[0], max(xs)), (lo[2], min(zs)), (hi[2], max(zs))):
        assert got == pytest.approx(want, abs=0.15)
    assert lo[1] == pytest.approx(-0.1, abs=0.05) and hi[1] == pytest.approx(2.7, abs=0.05)
    for _, (x0, _, z0, x1, _, z1) in boxes:
        assert min(xs) - 0.15 <= x0 <= x1 <= max(xs) + 0.15 and min(zs) - 0.15 <= z0 <= z1 <= max(zs) + 0.15
    assert (out / "source" / "house.glb").read_bytes() == src.read_bytes()
    assert set((out / ".gitignore").read_text().split()) == {"scene.glb", "source/"}
    prov = json.loads((out / "scan.json").read_text())
    assert prov["kind"] == "mesh" and prov["model"]["up"] == "y" and prov["model"]["scale"] == pytest.approx(1.0)
    # the model draws its own furniture: none is added that the view would hide; the layout check reads the home's
    # pieces, not the model's walls
    assert items(out) == []
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def claude_reads(monkeypatch, src: Path, entrance_at=(6.45, 4.0)) -> object:
    """`claude` on PATH, answering for `src`'s render what B3b's test answers: the two rooms, the door between them,
    the entrance (at house point `entrance_at`) and the window, in the render's pixels. Returns the fake."""
    import shutil as sh

    from tests.lab.test_plan_read import Fake, envelope
    from tests.lab.test_scan_render import answer_in_pixels, to_pixel
    from vesper.lab import plan_read
    from vesper.lab.scan_render import render_plan_view
    view = render_plan_view(src, src.with_suffix(".probe.png"))
    answer = answer_in_pixels(view)
    answer["plan"]["doors"][1]["at"] = list(to_pixel(view, entrance_at[0], 1.3, entrance_at[1]))
    fake = Fake(envelope(json.dumps(answer)))
    real = sh.which
    monkeypatch.setattr(sh, "which", lambda name, *a, **k: "/usr/local/bin/claude" if name == "claude" else
                        real(name, *a, **k))
    monkeypatch.setattr(plan_read, "_claude", fake)
    return fake


def test_a_mesh_is_read_by_claude_by_default_when_claude_is_here_and_its_reading_kept(tmp_path, capsys, monkeypatch):
    cat, _ = setup(tmp_path)
    src = mesh_house(tmp_path)
    fake = claude_reads(monkeypatch, src)
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(src), str(out), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert len(fake.calls) == 1 and "reader: Claude, from a top-down render" in said
    assert "2 rooms (bedroom, living)" in said and "entrance assumed" not in said
    assert_view_on_the_plan(out, floor=(-0.15, 0.05))  # the house's floor slab is 0.1 m thick, below 0
    saved = out.with_name("scan.plan.json")  # kept before anything could refuse it
    reading = json.loads(saved.read_text())
    assert reading["format"] == "vesper-scan-reading/1" and reading["meta"]["reader"] == "claude"
    assert reading["source"] == str(src.resolve()) and len(reading["plan"]["rooms"]) == 2
    assert json.loads((out / "scan.json").read_text())["model"]["up"] == "y"
    # built again from the reading, with no Claude
    again = tmp_path / "again"
    assert cli.main(["lab", "import", str(saved), str(again), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert len(fake.calls) == 1 and "the reading saved in scan.plan.json is used" in said
    assert (json.loads((again / "plan.json").read_text())["rooms"] == json.loads((out / "plan.json").read_text())[
        "rooms"])
    assert (again / "scene.glb").is_file()


def test_without_claude_a_mesh_is_read_geometrically_and_a_reading_kept_only_when_asked(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    src = mesh_house(tmp_path)
    assert cli.main(["lab", "import", str(src), str(tmp_path / "a"), "--catalog", str(cat)]) == 0
    assert "reader: geometric, from the model's shape" in capsys.readouterr().out
    assert not (tmp_path / "a.plan.json").exists()
    keep = tmp_path / "kept.json"
    assert cli.main(["lab", "import", str(src), str(tmp_path / "b"), "--reader", "geometric", "--save-plan",
                     str(keep), "--catalog", str(cat)]) == 0
    assert json.loads(keep.read_text())["meta"]["reader"] == "geometric"
    assert cli.main(["lab", "import", str(src), str(tmp_path / "c"), "--save-plan", str(keep), "--catalog",
                     str(cat)]) == 1  # never over a reading kept earlier
    assert "already exists" in capsys.readouterr().err and not (tmp_path / "c").exists()


def test_an_entrance_claude_put_in_a_drawn_wall_is_said_to_be_assumed(tmp_path, capsys, monkeypatch):
    cat, _ = setup(tmp_path)
    src = mesh_house(tmp_path)
    claude_reads(monkeypatch, src, entrance_at=(5.0, 4.0))  # the south wall, solid there
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(src), str(out), "--reader", "claude", "--catalog", str(cat)]) == 0
    assert "entrance assumed: door 2 (the render shows a wall there" in capsys.readouterr().out


def test_a_roomplan_scan_takes_no_reader(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    assert cli.main(["lab", "import", str(write(tmp_path, fixture())), str(tmp_path / "x"), "--reader", "claude",
                     "--catalog", str(cat)]) == 1
    assert "--reader is for a 3D model" in capsys.readouterr().err


def test_a_mesh_s_scale_is_a_number(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    src = mesh_house(tmp_path)
    assert cli.main(["lab", "import", str(src), str(tmp_path / "a"), "--scale", "kitchen width 3 m", "--catalog",
                     str(cat)]) == 1
    assert "metres per unit" in capsys.readouterr().err
    assert cli.main(["lab", "import", str(src), str(tmp_path / "b"), "--scale", "1", "--catalog", str(cat)]) == 0


def test_without_trimesh_a_mesh_import_names_the_scan_extra(tmp_path, monkeypatch, capsys):
    cat, _ = setup(tmp_path)
    src = tmp_path / "house.glb"
    src.write_bytes(b"glTF")
    monkeypatch.setitem(sys.modules, "trimesh", None)  # import trimesh raises ImportError
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(src), str(out), "--catalog", str(cat)]) == 1
    assert "install the scan extra" in (err := capsys.readouterr().err) and "'.[scan]'" in err
    assert not out.exists()


def test_a_mesh_refused_writes_nothing(tmp_path, capsys):
    pytest.importorskip("trimesh")
    import trimesh
    cat, _ = setup(tmp_path)
    src = tmp_path / "cube.obj"
    trimesh.creation.box(extents=[1, 1, 1]).export(str(src))
    out = tmp_path / "scan"
    assert cli.main(["lab", "import", str(src), str(out), "--catalog", str(cat)]) == 1
    assert "FAILED" in capsys.readouterr().err and not out.exists()


def test_the_model_turns_as_the_plan_s_points_do():
    """scene.glb's matrix (rectify's turn after the reader's transform) moves a point as the plan's turn does."""
    from vesper.lab.scan_import import model_matrix, turn_point
    turn = {"angle_deg": -31.0, "centre": [4.0, 2.5]}
    shift = [[1, 0, 0, 1.5], [0, 1, 0, -0.2], [0, 0, 1, 3.0], [0, 0, 0, 1]]  # a reader's transform: a move
    m = model_matrix(shift, turn)
    x, y, z = 2.0, 1.0, -1.0
    got = [sum(m[r][c] * v for c, v in enumerate((x, y, z, 1.0))) for r in range(3)]
    want = turn_point((x + 1.5, z + 3.0), turn)
    assert got == [pytest.approx(want[0]), pytest.approx(0.8), pytest.approx(want[1])]


def test_a_regenerated_home_sets_aside_a_scan_s_files_too():
    from vesper.lab.compose import HOME_FILES
    assert {"scene.glb", "scan.json", "source", ".gitignore"} <= set(HOME_FILES)


def test_one_scan_error_class():
    from vesper.lab import scan_mesh, scan_roomplan, scan_usd
    assert scan_mesh.ScanError is scan_roomplan.ScanError is scan_usd.ScanError


# --- the real scans (local, never committed) ---------------------------------------------------------------------

def test_the_cued_meeting_room_imports_with_a_scale_warning(tmp_path, capsys):
    src = SCANS / "litereality-cued-meeting-room-roomplan" / "roomplan" / "room.json"
    if not src.is_file():
        pytest.skip("the scan test set is not here")
    cat, _ = setup(tmp_path)
    out = tmp_path / "cued"
    assert cli.main(["lab", "import", str(src), str(out), "--catalog", str(cat)]) == 0
    said = capsys.readouterr().out
    assert "times their usual size: measured in metres, so built as measured" in said
    assert any(i["source"] == "scan" for i in items(out))
    assert (out / "source" / "room.json").is_file() and (out / ".gitignore").is_file()


@pytest.mark.parametrize("name", ["room.json", "room_p.usdz"])
def test_the_darwin_bedroom_imports_its_doors_kept_clear_of_its_stepped_wall_s_corners(tmp_path, capsys, name):
    src = SCANS / "litereality-darwin-bedroom-roomplan" / "roomplan" / name
    if not src.is_file():
        pytest.skip("the scan test set is not here")
    if name.endswith(".usdz"):
        pytest.importorskip("pxr")
    cat, _ = setup(tmp_path)
    out = tmp_path / "darwin"
    assert cli.main(["lab", "import", str(src), str(out), "--catalog", str(cat)]) == 0
    assert "stands by a corner where its room's wall turns out past it" in capsys.readouterr().out
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    assert {r.id for r in home.map.rooms} == {"bedroom", "porch", "porch_2"}
    # the real bed faces into the room: its head (its back, -front) is at a wall, its foot is not
    from vesper.lab.generate import _edges, _gap
    ring = [tuple(p) for p in home.map.room("bedroom").polygon]
    bed = next(i for i in items(out) if i["kind"] == "bed")
    f = (math.cos(math.radians(bed["facing"])), math.sin(math.radians(bed["facing"])))
    head = (bed["x"] - f[0] * bed["d"] / 2, bed["z"] - f[1] * bed["d"] / 2)
    foot = (bed["x"] + f[0] * bed["d"] / 2, bed["z"] + f[1] * bed["d"] / 2)
    to_wall = lambda p: min(_gap(e, p) for e in _edges(ring))  # noqa: E731
    assert to_wall(head) < 0.2 < to_wall(foot)
