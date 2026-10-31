"""An HSSD home dressed in Blender (home-generator spec §5, plan W9b): `vesper lab dress` converts each HSSD model
once (KTX2 textures to PNG, which Blender reads), and the build script imports each at the place Habitat gives it,
in Blender's axes: the stage in its own collection, the furniture in its room's, the doors apart (VESPER draws and
swings them, so they are not exported). Edited, the home is exported as scene.glb, which `vesper lab play` draws in
place of HSSD's files. A small synthetic "HSSD" stands in for the dataset."""
import json
import os
import struct
import subprocess
import sys

import pytest

from tests.lab import fake_bpy
from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.dress import dress, prepare_models, scene_data
from vesper.lab.home import load_lab_home
from vesper.lab.world3d import World3d

C = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]  # glTF's +Y up to Blender's +Z up


def box_glb(path, size=0.5):
    """A tiny model: a box, no textures."""
    s = size
    v = [(x, y, z) for x in (-s, s) for y in (0.0, 2 * s) for z in (-s, s)]
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4),
         (1, 5, 7), (1, 7, 3)]
    pos = b"".join(struct.pack("<3f", *p) for p in v)
    idx = b"".join(struct.pack("<3H", *t) for t in f)
    idx += b"\0" * (-len(idx) % 4)
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
            "buffers": [{"byteLength": len(pos) + len(idx)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(f) * 6}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
                           "min": [-s, 0, -s], "max": [s, 2 * s, s]},
                          {"bufferView": 1, "componentType": 5123, "count": len(f) * 3, "type": "SCALAR"}]}
    j = json.dumps(gltf).encode()
    j += b" " * (-len(j) % 4)
    binary = pos + idx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 28 + len(j) + len(binary)) + struct.pack("<II", len(j),
                     0x4E4F534A) + j + struct.pack("<II", len(binary), 0x004E4942) + binary)
    return path


def translation(x, y, z, turn=False):
    """A Habitat world matrix, row by row: a move, and with `turn` a quarter turn about +Y."""
    if turn:
        return [0.0, 0.0, 1.0, x, 0.0, 1.0, 0.0, y, -1.0, 0.0, 0.0, z, 0.0, 0.0, 0.0, 1.0]
    return [1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, z, 0.0, 0.0, 0.0, 1.0]


@pytest.fixture
def hssd_home(tmp_path):
    """The test house with a synthetic HSSD scene: a stage, a chair in the kitchen, a door in the front doorway."""
    (tmp_path / "home").mkdir()
    home = load_lab_home(write_home(tmp_path / "home", LOCKED_HOME), catalog())
    hssd = tmp_path / "hssd"
    for asset in ("stages/house.glb", "objects/c/chair.glb", "objects/d/door.glb", "objects/d/stray.glb"):
        box_glb(hssd / asset)
    kitchen = home.map.room("kitchen")
    kx = sum(x for x, _ in kitchen.polygon) / len(kitchen.polygon)
    kz = sum(z for _, z in kitchen.polygon) / len(kitchen.polygon)
    door = home.map.doorway("hall-porch").center
    items = [{"kind": "stage", "asset": "stages/house.glb", "matrix": translation(0, 0, 0)},
             {"kind": "object", "asset": "objects/c/chair.glb", "matrix": translation(kx, 0, kz, turn=True)},
             {"kind": "part", "asset": "objects/d/door.glb", "matrix": translation(door[0], 0, door[1]),
              "handle": "door_:0000", "doorway": "hall-porch", "opening": "door"},
             {"kind": "object", "asset": "objects/d/stray.glb", "matrix": translation(kx, 0, kz),  # in no doorway
              "handle": "stray_:0000", "opening": "door"}]
    world3d = World3d({"format": "vesper-render/1", "scene": home.scene, "items": items}, hssd,
                      frozenset(i["asset"] for i in items), [], 0.0)
    return home, world3d, (kx, kz)


def fake_convert(cache):
    """prepare_models' runner: the node batch, copying instead of converting; records each batch."""
    batches = []

    def run(argv, input, **kwargs):
        pairs = json.loads(input)
        batches.append((argv, pairs))
        for src, dst in pairs:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, "rb") as a, open(dst, "wb") as b:
                b.write(a.read())
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run, batches


def test_each_distinct_model_is_converted_once_into_the_cache(hssd_home, tmp_path):
    _, world3d, _ = hssd_home
    run, batches = fake_convert(tmp_path / "cache")
    models = prepare_models(world3d, tmp_path / "cache", run=run)
    (argv, pairs), = batches
    assert argv[0] == "node" and argv[1].endswith("ktx2png.mjs") and argv[2] == "--batch"
    assert sorted(models) == sorted(world3d.assets) and len(pairs) == 4
    assert all(p.is_file() and str(p).startswith(str(tmp_path / "cache")) for p in models.values())
    assert prepare_models(world3d, tmp_path / "cache", run=run) == models and len(batches) == 1  # cached


def test_the_scene_data_of_an_hssd_home_names_its_models_and_their_places(hssd_home, tmp_path):
    home, world3d, _ = hssd_home
    run, _ = fake_convert(tmp_path / "cache")
    models = prepare_models(world3d, tmp_path / "cache", run=run)
    data = scene_data(home, world3d, models)
    assert data["walls"] == [] and data["doorways"] == []  # the stage has its own walls and openings
    by_asset = {i["asset"]: i for i in data["hssd"]["items"]}
    chair, door = by_asset["objects/c/chair.glb"], by_asset["objects/d/door.glb"]
    assert chair["model"] == str(models["objects/c/chair.glb"])
    assert chair["matrix"] == world3d.manifest["items"][1]["matrix"]
    assert door["doorway"] == "hall-porch" and by_asset["objects/d/stray.glb"]["opening"] == "door"


def run_script(script, monkeypatch, *args, bpy=None):
    bpy = bpy or fake_bpy.make()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(sys, "argv", ["blender", "--", *args] if args else ["Blender"])
    exec(compile(script.read_text(), str(script), "exec"), {"bpy": bpy})
    return bpy


def dressed(hssd_home, tmp_path):
    home, world3d, _ = hssd_home
    run, _ = fake_convert(tmp_path / "cache")
    return dress(home, world3d=world3d, cache=tmp_path / "cache", run=run)


def test_the_script_places_each_model_where_habitat_does_in_blender_axes(hssd_home, tmp_path, monkeypatch):
    home, world3d, (kx, kz) = hssd_home
    script, _ = dressed(hssd_home, tmp_path)
    bpy = run_script(script, monkeypatch)
    assert len(bpy.imports) == 4
    holder = next(o for o in bpy.data.collections["room:kitchen"].objects if o.name.startswith("hssd:"))
    m = [world3d.manifest["items"][1]["matrix"][r * 4:r * 4 + 4] for r in range(4)]
    cinv = [list(r) for r in zip(*C)]  # a rotation: its inverse is its transpose
    want = [[sum(a * b for a, b in zip(row, col)) for col in zip(*cinv)]
            for row in [[sum(a * b for a, b in zip(r, col)) for col in zip(*m)] for r in C]]
    assert [v for row in holder.matrix_world for v in row] == pytest.approx([v for row in want for v in row])
    assert holder.matrix_world[0][3] == pytest.approx(kx) and holder.matrix_world[1][3] == pytest.approx(-kz)
    imported = [o for o in bpy.data.objects.values() if o.parent is holder]
    assert imported and all(bpy.data.collections["room:kitchen"] in o.users_collection for o in imported)
    assert [o.name for o in bpy.data.collections["stage"].objects if o.name.startswith("hssd:")]
    doors = [o for o in bpy.data.collections["VESPER doors"].objects if o.name.startswith("hssd:")]
    assert sorted(o.name.rsplit(":", 1)[1] for o in doors) == ["door", "stray"]  # a stray door is a door too
    assert [o["doorway"] for o in doors if o.name.endswith(":door")] == ["hall-porch"]
    assert bpy.data.collections["VESPER doors"] not in bpy.data.collections["VESPER home"].children
    assert bpy.data.objects["device:front_lock"].location[2] == pytest.approx(1.0)


def test_the_script_run_again_keeps_the_edited_scene_and_imports_nothing(hssd_home, tmp_path, monkeypatch):
    script, _ = dressed(hssd_home, tmp_path)
    bpy = run_script(script, monkeypatch)
    holder = next(o for o in bpy.data.collections["room:kitchen"].objects if o.name.startswith("hssd:"))
    holder.matrix_world = [[1.0, 0, 0, 9.0], [0, 1.0, 0, 9.0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]  # moved in Blender
    run_script(script, monkeypatch, bpy=bpy)
    assert len(bpy.imports) == 4 and holder.matrix_world[0][3] == 9.0
    assert "device:front_lock" in bpy.data.objects  # the device markers are made again


def test_export_leaves_the_doors_out(hssd_home, tmp_path, monkeypatch):
    script, _ = dressed(hssd_home, tmp_path)
    bpy = run_script(script, monkeypatch, "--export")
    (export,) = bpy.exports
    assert export["use_active_collection"] is True
    assert bpy.context.view_layer.active_layer_collection.collection.name == "VESPER home"


def test_play_draws_an_edited_hssd_home_from_its_own_model(hssd_home):
    from vesper.lab.play import hello
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    home, world3d, _ = hssd_home
    session = Session(home, clock=LogicalClock())
    session.start(spawn_player=False)
    assert hello(session, world3d=world3d)["dressed"] is None
    (home.path.parent / "scene.glb").write_bytes(b"glTF-model")
    h = hello(session, world3d=world3d)
    assert h["dressed"] == "/home/scene.glb" and h["world3d"]["render"] == "/world/render.json"  # its doors still


@pytest.mark.skipif(not os.environ.get("VESPER_BLENDER"), reason="set VESPER_BLENDER to a Blender 4.2+ executable")
def test_real_blender_keeps_each_hssd_model_where_habitat_puts_it(hssd_home, tmp_path):
    """Imported, placed and exported by a real Blender: scene.glb's chair stands where Habitat's matrix puts it, in
    the same Y-up axes, so the 3D view draws it unchanged; the door is not in it."""
    home, world3d, (kx, kz) = hssd_home
    run, _ = fake_convert(tmp_path / "cache")
    script, _ = dress(home, world3d=world3d, cache=tmp_path / "cache", run=run)
    subprocess.run([os.environ["VESPER_BLENDER"], "-b", "--factory-startup", "--python", str(script), "--",
                    "--export"], check=True, capture_output=True, timeout=300)
    raw = (home.path.parent / "scene.glb").read_bytes()
    gltf = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    chair = next(n for n in gltf["nodes"] if n.get("name", "").startswith("hssd:") and "chair" in n["name"])
    assert chair["translation"] == pytest.approx([kx, 0.0, kz], abs=1e-4)
    assert chair["rotation"] == pytest.approx([0.0, 0.7071068, 0.0, 0.7071068], abs=1e-4)  # the quarter turn about +Y
    assert not [n for n in gltf["nodes"] if n.get("name", "").startswith("hssd:") and n["name"].endswith(":door")]


def test_vesper_lab_dress_converts_an_hssd_home_s_models_and_says_what_it_did(tmp_path, capsys, monkeypatch):
    """The command on a home with a render manifest: Node converts its models (none has KTX2 here), into the cache
    VESPER_CACHE names; the brief says how an HSSD home is edited."""
    import shutil

    from tests.lab.test_cli_lab import setup
    from vesper.cli import main
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    cat, home_file = setup(tmp_path)
    home = load_lab_home(home_file, catalog())
    hssd = tmp_path / "hssd"
    box_glb(hssd / "stages/house.glb")
    box_glb(hssd / "objects/c/chair.glb")
    (home_file.parent / "render.json").write_text(json.dumps({
        "format": "vesper-render/1", "scene": home.scene, "hssd": str(hssd), "floor_y": 0.0,
        "items": [{"kind": "stage", "asset": "stages/house.glb", "matrix": translation(0, 0, 0)},
                  {"kind": "object", "asset": "objects/c/chair.glb", "matrix": translation(1, 0, 1)}]}))
    monkeypatch.setenv("VESPER_CACHE", str(tmp_path / "cache"))
    assert main(["lab", "dress", str(home_file), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "2 HSSD models" in out and "keep them out of the repository" in out
    assert (tmp_path / "cache" / "hssd-blender" / "objects/c/chair.glb").is_file()
    brief = (home_file.parent / "dress" / "BRIEF.md").read_text()
    assert "VESPER doors" in brief and "vesper lab renav" in brief


def test_vesper_lab_renav_takes_the_map_s_floor_from_the_exported_scene_and_keeps_the_original(
        tmp_path, capsys, monkeypatch):
    """The world service (here a stand-in) writes the new map; the home must still load on it; the original map is
    kept beside it, once."""
    from tests.lab.test_cli_lab import setup
    from vesper.cli import main
    from vesper.lab import scenes
    cat, home_file = setup(tmp_path)
    home_dir = home_file.parent
    (home_dir / "scene.glb").write_bytes(b"glTF")
    original = (home_dir / "map.json").read_text()
    calls = []

    def run_world(python, args, what, run=None):
        calls.append(args)
        out = args[args.index("--out") + 1]
        new = json.loads(original)
        new["grid"]["walkable"] = new["grid"]["walkable"]  # the same floor: the stand-in's edit moved nothing
        open(out, "w").write(json.dumps(new))
        return subprocess.CompletedProcess(args, 0, "hssd: 10 walkable cells before, 9 after", "")
    monkeypatch.setattr(scenes, "run_world", run_world)
    assert main(["lab", "renav", str(home_file), "--catalog", str(cat), "--habitat-python", sys.executable]) == 0
    (args,) = calls
    assert args[0] == "renav" and args[args.index("--glb") + 1] == str((home_dir / "scene.glb").resolve())
    assert (home_dir / "map.original.json").read_text() == original
    assert "walkable cells" in capsys.readouterr().out
    assert main(["lab", "renav", str(home_file), "--catalog", str(cat), "--habitat-python", sys.executable]) == 0
    assert (home_dir / "map.original.json").read_text() == original  # still the first one


def test_vesper_lab_renav_needs_the_exported_scene(tmp_path, capsys):
    from tests.lab.test_cli_lab import setup
    from vesper.cli import main
    cat, home_file = setup(tmp_path)
    assert main(["lab", "renav", str(home_file), "--catalog", str(cat)]) == 1
    assert "no scene.glb" in capsys.readouterr().err


# -- renav refuses a floor that lost a doorway or a room (the final review) -----------------------------------
def floor_without(map_json: dict, room_polygon) -> dict:
    """A copy of a map with every walkable cell inside one room's outline taken away."""
    from vesper.lab.worldmap import Grid
    g = Grid.from_json(map_json["grid"])
    cells = bytearray(g.walkable)
    for j in range(g.height):
        for i in range(g.width):
            x, z = g.center(i, j)
            xs, zs = [p[0] for p in room_polygon], [p[1] for p in room_polygon]
            if min(xs) <= x <= max(xs) and min(zs) <= z <= max(zs):
                cells[j * g.width + i] = 0
    return {**map_json, "grid": Grid(g.origin, g.cell, g.width, g.height, cells).to_json()}


def test_renav_problems_name_a_lost_doorway_an_emptied_room_and_a_collapsed_floor(tmp_path):
    from vesper.lab.dress import renav_problems
    from vesper.lab.worldmap import WorldMap
    (tmp_path / "home").mkdir()
    home = load_lab_home(write_home(tmp_path / "home", LOCKED_HOME), catalog())
    old_json = json.loads((tmp_path / "home" / "map.json").read_text())
    old = WorldMap.load(tmp_path / "home" / "map.json")
    assert renav_problems(old, old) == []
    kitchen = home.map.room("kitchen")
    (tmp_path / "new.json").write_text(json.dumps(floor_without(old_json, kitchen.polygon)))
    problems = renav_problems(old, WorldMap.load(tmp_path / "new.json"))
    assert any("kitchen" in p and "no walkable floor" in p for p in problems)
    assert any("doorway" in p and "kitchen" in p for p in problems)


def test_renav_that_loses_a_room_is_refused_and_leaves_the_map_as_it_was(tmp_path, capsys, monkeypatch):
    from tests.lab.test_cli_lab import setup
    from vesper.cli import main
    from vesper.lab import scenes
    cat, home_file = setup(tmp_path)
    home_dir = home_file.parent
    (home_dir / "scene.glb").write_bytes(b"glTF")
    original = (home_dir / "map.json").read_text()
    kitchen = load_lab_home(home_file, catalog()).map.room("kitchen")

    def run_world(python, args, what, run=None):  # a sofa dragged across the kitchen's doorway
        open(args[args.index("--out") + 1], "w").write(json.dumps(floor_without(json.loads(original), kitchen.polygon)))
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(scenes, "run_world", run_world)
    assert main(["lab", "renav", str(home_file), "--catalog", str(cat), "--habitat-python", sys.executable]) == 1
    err = capsys.readouterr().err
    assert "kitchen" in err and "the map is unchanged" in err
    assert (home_dir / "map.json").read_text() == original
    assert not (home_dir / "map.original.json").exists() or (home_dir / "map.original.json").read_text() == original


def test_starting_over_imports_the_scene_again_when_its_collections_are_gone(hssd_home, tmp_path, monkeypatch):
    """Deleting the collections (Blender MCP's collections.remove) leaves their objects in bpy.data, unlinked: a
    rerun still brings the scene in."""
    script, _ = dressed(hssd_home, tmp_path)
    bpy = run_script(script, monkeypatch)
    scene = bpy.context.scene.collection
    for name in ("VESPER home", "VESPER doors"):
        top = bpy.data.collections[name]
        scene.children.unlink(top)
        for c in [top, *top.children]:
            bpy.data.collections.remove(c)
    assert any(o.get("vesper_hssd") for o in bpy.data.objects)  # the holders are still in bpy.data
    run_script(script, monkeypatch, bpy=bpy)
    assert len(bpy.imports) == 8
    assert [o for o in bpy.data.collections["room:kitchen"].objects if o.name.startswith("hssd:")]


def test_a_converted_model_cut_short_is_converted_again(hssd_home, tmp_path):
    """A conversion killed while writing leaves a file newer than its source but short of the length its header
    gives: it is not taken for a converted model."""
    _, world3d, _ = hssd_home
    run, batches = fake_convert(tmp_path / "cache")
    models = prepare_models(world3d, tmp_path / "cache", run=run)
    chair = models["objects/c/chair.glb"]
    chair.write_bytes(chair.read_bytes()[:40])  # cut short, and newer than HSSD's file
    prepare_models(world3d, tmp_path / "cache", run=run)
    assert [[src.rsplit("/", 1)[1] for src, _ in pairs] for _, pairs in batches[1:]] == [["chair.glb"]]
    assert len(chair.read_bytes()) == struct.unpack("<I", chair.read_bytes()[8:12])[0]


@pytest.mark.parametrize("path", ["somehome/dress/build_home.py", "somehome/dress/BRIEF.md",
                                  "somehome/map.original.json", "somehome/scene.glb"])
def test_what_dress_and_renav_write_beside_a_home_is_never_committed(path):
    """An HSSD home made inside the repository by mistake: its build script (HSSD's matrices, a cache path), its
    first map and its model are derived from HSSD (CC BY-NC) and stay out of git."""
    import shutil
    from pathlib import Path
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=root, capture_output=True)
    assert r.returncode == 0, f"{path} is not ignored"
