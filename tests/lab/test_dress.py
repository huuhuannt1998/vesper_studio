"""Dressing a home in Blender (home-generator spec §5, plan W9): `vesper lab dress` writes a build script and a
brief beside the home; the script, run in Blender (by a person, or by Claude through Blender MCP), builds the home's
floors and walls from its map, door openings where the map has doorways, and an empty where each device is, all in
one collection, and exports `scene.glb` beside the home; `vesper lab play` draws that model on the map."""
import sys
import urllib.request

import pytest

from tests.lab import fake_bpy
from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.cli import main
from vesper.lab.dress import device_height, dress, scene_data, wall_boxes
from vesper.lab.home import load_lab_home
from vesper.lab.worldmap import Grid


def home(tmp_path):
    return load_lab_home(write_home(tmp_path, LOCKED_HOME), catalog())


def test_the_walls_are_the_blocked_cells_beside_the_floor_as_the_3d_view_draws_them():
    """A 3 x 2 floor: a ring of walls just outside it, the top and bottom rows whole, the sides one box each."""
    grid = Grid((0.0, 0.0), 0.1, 3, 2, bytearray([1] * 6))
    boxes = wall_boxes(grid)
    assert len(boxes) == 4
    assert [v for b in boxes for v in b] == pytest.approx([-0.1, -0.1, 0.4, 0.0, -0.1, 0.0, 0.0, 0.2,
                                                           0.3, 0.0, 0.4, 0.2, -0.1, 0.2, 0.4, 0.3])


def test_a_device_stands_at_its_mount_s_height():
    assert (device_height("ceiling", "light"), device_height("door", "lock"), device_height("door", "contact_sensor"),
            device_height("wall", "switch"), device_height("floor", "plug")) == (2.35, 1.0, 2.0, 1.2, 0.08)


def test_the_scene_data_has_the_rooms_walls_doorways_and_devices_of_the_home(tmp_path):
    h = home(tmp_path)
    data = scene_data(h)
    assert data["format"] == "vesper-dress/1" and data["export_to"] == str((tmp_path / "scene.glb").resolve())
    assert {r["id"] for r in data["rooms"]} == {r.id for r in h.map.rooms}
    assert data["walls"] and {d["id"] for d in data["doorways"]} == {d.id for d in h.map.doorways}
    lock = next(d for d in data["devices"] if d["name"] == "front_lock")
    x, z = h.device("front_lock").position
    assert lock["point"] == pytest.approx([x, 1.0, z]) and lock["category"] == "lock"


def run_script(script, monkeypatch, *args):
    bpy = fake_bpy.make()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(sys, "argv", ["blender", "--background", "--python", str(script), "--", *args])
    exec(compile(script.read_text(), str(script), "exec"), {"__name__": "__main__"})
    return bpy


def test_dress_writes_a_build_script_and_a_brief_beside_the_home(tmp_path):
    h = home(tmp_path)
    script, brief = dress(h)
    assert (script, brief) == (tmp_path / "dress" / "build_home.py", tmp_path / "dress" / "BRIEF.md")
    compile(script.read_text(), str(script), "exec")
    text = brief.read_text()
    assert all(f"`room:{r.id}`" in text for r in h.map.rooms if not r.outdoor)
    assert str((tmp_path / "scene.glb").resolve()) in text and "build_home.py" in text


def test_the_script_builds_the_home_in_blender_s_axes_in_one_collection(tmp_path, monkeypatch):
    h = home(tmp_path)
    script, _ = dress(h)
    data = scene_data(h)
    bpy = run_script(script, monkeypatch)
    root = bpy.data.collections["VESPER home"]
    assert root in bpy.context.scene.collection.children
    names = {c.name for c in root.children}
    assert {"walls", "doorways", "devices"} <= names and {f"room:{r.id}" for r in h.map.rooms} <= names
    walls = bpy.data.collections["walls"].objects
    assert len(walls) == len(data["walls"])
    x0, z0, x1, z1 = data["walls"][0]
    xs, ys, zs = zip(*walls[0].data.vertices)
    assert (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)) == pytest.approx((x0, x1, -z1, -z0, 0.0, 2.5))
    lock = bpy.data.objects["device:front_lock"]
    x, z = h.device("front_lock").position
    assert lock.data is None and lock.location == pytest.approx((x, -z, 1.0)) and lock["category"] == "lock"
    assert len(bpy.data.collections["doorways"].objects) == len(h.map.doorways)  # the lintels over the doors
    assert bpy.exports == []  # building only, unless asked to export


def test_the_script_run_again_replaces_its_home_and_leaves_the_rest_of_the_scene(tmp_path, monkeypatch):
    script, _ = dress(home(tmp_path))
    bpy = run_script(script, monkeypatch)
    lamp = bpy.data.objects.new("my lamp", None)
    bpy.context.scene.collection.objects.link(lamp)
    before = sorted(bpy.data.objects.keys()), sorted(bpy.data.meshes.keys())
    exec(compile(script.read_text(), str(script), "exec"), {"__name__": "__main__"})
    assert (sorted(bpy.data.objects.keys()), sorted(bpy.data.meshes.keys())) == before
    assert lamp in bpy.context.scene.collection.objects


def test_the_script_exports_the_collection_as_scene_glb_beside_the_home_when_asked(tmp_path, monkeypatch):
    script, _ = dress(home(tmp_path))
    bpy = run_script(script, monkeypatch, "--export")
    (export,) = bpy.exports
    assert export["filepath"] == str((tmp_path / "scene.glb").resolve())
    assert (export["export_format"], export["export_yup"], export["use_active_collection"]) == ("GLB", True, True)
    assert bpy.context.view_layer.active_layer_collection.collection.name == "VESPER home"


def test_vesper_lab_dress_writes_the_files_and_says_what_to_do_next(tmp_path, capsys):
    from tests.lab.test_cli_lab import setup
    cat, home_file = setup(tmp_path)
    assert main(["lab", "dress", str(home_file), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert (home_file.parent / "dress" / "build_home.py").is_file() and "BRIEF.md" in out and "scene.glb" in out


def test_play_draws_a_dressed_home_s_model_when_it_has_one(tmp_path):
    from vesper.lab.play import PlayServer, hello
    from vesper.lab.session import Session
    from vesper.twins.clock import WallClock
    h = home(tmp_path)
    session = Session(h, clock=WallClock())
    session.start(spawn_player=False)
    try:
        assert hello(session)["dressed"] is None
        (tmp_path / "scene.glb").write_bytes(b"glTF-model")
        assert hello(session)["dressed"] == "/home/scene.glb"
        ui = tmp_path / "ui"
        ui.mkdir()
        (ui / "index.html").write_text("<h1>lab</h1>")
        server = PlayServer(session, port=0, ui_dir=ui)
        server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/home/scene.glb") as r:
                assert (r.read(), r.headers["Content-Type"]) == (b"glTF-model", "model/gltf-binary")
        finally:
            server.stop()
    finally:
        session.stop()


def furnish(bpy, room, name, data=None):
    """What a person (or Claude) adds while dressing: an object in a room's collection."""
    obj = bpy.data.objects.new(name, data)
    bpy.data.collections[f"room:{room}"].objects.link(obj)
    return obj


def test_blender_mcp_s_exec_of_the_script_builds_the_home(tmp_path, monkeypatch):
    """Blender MCP runs code as exec(code, {"bpy": bpy}): no __main__, and Blender's own argv."""
    script, _ = dress(home(tmp_path))
    bpy = fake_bpy.make()
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setattr(sys, "argv", ["Blender"])
    exec(compile(script.read_text(), str(script), "exec"), {"bpy": bpy})
    assert "VESPER home" in bpy.data.collections and bpy.data.collections["walls"].objects


def test_export_keeps_the_furniture_the_dressing_added(tmp_path, monkeypatch):
    script, _ = dress(home(tmp_path))
    bpy = run_script(script, monkeypatch)
    sofa = furnish(bpy, "kitchen", "sofa", bpy.data.meshes.new("sofa"))
    walls = list(bpy.data.collections["walls"].objects)
    monkeypatch.setattr(sys, "argv", ["blender", "--background", "f.blend", "--python", str(script), "--", "--export"])
    exec(compile(script.read_text(), str(script), "exec"), {"bpy": bpy})
    assert sofa in bpy.data.collections["room:kitchen"].objects and "sofa" in bpy.data.meshes
    assert list(bpy.data.collections["walls"].objects) == walls  # exported as it is, not rebuilt
    assert len(bpy.exports) == 1


def test_a_rebuild_keeps_what_the_dressing_added_whatever_it_is(tmp_path, monkeypatch):
    """A lamp's light, a camera: not meshes; a rebuild replaces only what the script built."""
    script, _ = dress(home(tmp_path))
    bpy = run_script(script, monkeypatch)
    lamp = furnish(bpy, "kitchen", "lamp", fake_bpy._Named("lamp light"))
    sofa = furnish(bpy, "kitchen", "sofa", bpy.data.meshes.new("sofa"))
    built = sorted(o.name for o in bpy.data.collections["VESPER home"].all_objects if o.get("vesper_built"))
    exec(compile(script.read_text(), str(script), "exec"), {"bpy": bpy})
    room = bpy.data.collections["room:kitchen"].objects
    assert lamp in room and sofa in room and "sofa" in bpy.data.meshes
    assert sorted(o.name for o in bpy.data.collections["VESPER home"].all_objects if o.get("vesper_built")) == built
    assert [c for c in bpy.data.collections.keys() if c.startswith("VESPER home.")] == []
    assert {"vesper:floor", "vesper:wall"} <= set(bpy.data.materials.keys())
