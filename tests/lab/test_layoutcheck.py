"""The layout checker (PI 2026-11-03: "beds should not be in front of the doors"): one set of rules for every home,
whoever furnished it, built-in furniture or a model exported from Blender."""
import json
import struct

from tests.lab.homes import catalog
from tests.lab.test_furnishing import make
from vesper.lab.home import load_lab_home
from vesper.lab.layoutcheck import check, glb_boxes, glb_pieces, kind_of


def home_of(tmp_path):
    out = make(tmp_path)
    return load_lab_home(out / "home.yaml", catalog()), out


def test_a_generated_furnished_home_keeps_every_rule(tmp_path):
    home, _ = home_of(tmp_path)
    report = check(home)
    bad = [f for f in report["findings"] if not f["ok"] and f["severity"] == "error"]
    assert report["source"] == "furniture.json" and not bad, bad
    assert {"door", "window", "bed", "walkway", "reach", "kitchen"} <= set(report["checked"])


def test_a_wardrobe_across_a_door_is_an_error_that_names_the_doorway(tmp_path):
    home, _ = home_of(tmp_path)
    d = home.map.doorways[0]
    x, z = d.center
    piece = {"name": "wardrobe.001", "kind": "wardrobe", "room": d.rooms[0],
             "box": [x - 0.6, z - 0.3, x + 0.6, z + 0.3], "h": 2.0}
    report = check(home, pieces=[piece])
    door = next(f for f in report["findings"] if f["rule"] == "door" and f["where"] == d.id)
    assert not door["ok"] and "wardrobe" in door["message"] and report["errors"] >= 1


def test_a_tall_piece_before_a_window_is_an_error_and_a_low_one_is_not(tmp_path):
    home, out = home_of(tmp_path)
    w = json.loads((out / "windows.json").read_text())["items"][0]
    half = w["width"] / 2
    box = ([w["x"] - half, w["z"] - 0.05, w["x"] + half, w["z"] + 0.6] if w["side"] == "z0" else
           [w["x"] - half, w["z"] - 0.6, w["x"] + half, w["z"] + 0.05] if w["side"] == "z1" else
           [w["x"] - 0.6, w["z"] - half, w["x"] + 0.6, w["z"] + half])
    tall = check(home, pieces=[{"name": "shelf", "kind": "bookshelf", "room": w["room"], "box": box, "h": 1.9}])
    low = check(home, pieces=[{"name": "sofa", "kind": "sofa", "room": w["room"], "box": box, "h": 0.4}])
    find = lambda r: next(f for f in r["findings"] if f["rule"] == "window" and f["where"] == w["id"])  # noqa: E731
    assert not find(tall)["ok"] and find(low)["ok"]


def test_a_bed_standing_free_of_every_wall_is_a_warning(tmp_path):
    home, _ = home_of(tmp_path)
    room = next(r for r in home.map.rooms if r.id.startswith("bedroom"))
    xs, zs = [p[0] for p in room.polygon], [p[1] for p in room.polygon]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    report = check(home, pieces=[{"name": "bed", "kind": "bed", "room": room.id,
                                  "box": [cx - 0.5, cz - 0.5, cx + 0.5, cz + 0.5], "h": 0.5}])
    bed = next(f for f in report["findings"] if f["rule"] == "bed")
    assert not bed["ok"] and bed["severity"] == "warning"


def test_a_corridor_blocked_from_wall_to_wall_breaks_the_walkway(tmp_path):
    home, _ = home_of(tmp_path)
    corridor = home.map.room("corridor")
    xs, zs = [p[0] for p in corridor.polygon], [p[1] for p in corridor.polygon]
    mid = (min(xs) + max(xs)) / 2
    report = check(home, pieces=[{"name": "crate", "kind": "object", "room": "corridor",
                                  "box": [mid - 0.3, min(zs), mid + 0.3, max(zs)], "h": 1.0}])
    walk = next(f for f in report["findings"] if f["rule"] == "walkway" and f["where"] == "corridor")
    assert not walk["ok"]


def test_the_kitchen_triangle_is_measured_when_a_sink_a_cooktop_and_a_fridge_are_there(tmp_path):
    home, _ = home_of(tmp_path)
    near = [{"name": n, "kind": k, "room": "kitchen", "box": [x, 0.5, x, 0.5], "h": 0.9}
            for n, k, x in (("sink", "sink", 1.0), ("hob", "cooktop", 2.5), ("fridge", "fridge", 4.0))]
    tri = next(f for f in check(home, pieces=near)["findings"] if f["rule"] == "kitchen")
    assert tri["ok"] is False and "NKBA" in tri["message"]  # the sink-fridge leg is 3 m


def glb(nodes, meshes, accessors):
    js = json.dumps({"asset": {"version": "2.0"}, "nodes": nodes, "meshes": meshes, "accessors": accessors}).encode()
    js += b" " * (-len(js) % 4)
    return b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js)) + struct.pack("<I4s", len(js), b"JSON") + js


def test_boxes_are_read_from_a_gltf_binary_with_their_node_transforms(tmp_path):
    f = tmp_path / "scene.glb"
    f.write_bytes(glb(
        nodes=[{"name": "Bed.001", "mesh": 0, "translation": [2, 0, 3]},
               {"name": "wall-0-0", "mesh": 0},
               {"name": "Room", "children": [3], "translation": [1, 0, 0]},
               {"name": "Wardrobe", "mesh": 0, "rotation": [0, 0.7071068, 0, 0.7071068], "scale": [2, 1, 1]}],
        meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
        accessors=[{"min": [-0.5, 0, -0.25], "max": [0.5, 1, 0.25]}]))
    boxes = dict(glb_boxes(f))
    assert boxes["Bed.001"] == (1.5, 0, 2.75, 2.5, 1, 3.25)
    x0, y0, z0, x1, y1, z1 = boxes["Wardrobe"]  # turned 90 degrees, 2 wide: 2 along z, 0.5 along x, under Room
    assert round(x1 - x0, 3) == 0.5 and round(z1 - z0, 3) == 2.0 and round((x0 + x1) / 2, 3) == 1.0
    from tests.lab.maps import house
    pieces = glb_pieces(f, house())
    assert {p["name"] for p in pieces} == {"Bed.001", "Wardrobe"}  # VESPER's own wall is not furniture
    assert next(p for p in pieces if p["name"] == "Bed.001")["kind"] == "bed"


def test_a_model_s_object_names_say_what_they_are():
    assert (kind_of("Refrigerator_Samsung"), kind_of("kitchen sink"), kind_of("Couch.003"), kind_of("Cube")) == \
        ("fridge", "sink", "sofa", "object")


def test_a_counter_run_s_sink_and_hob_are_where_the_view_draws_them():
    from vesper.lab.layoutcheck import _counter_parts
    sink, hob = _counter_parts({"x": 0.0, "z": 0.0, "w": 2.0, "facing": 90, "room": "kitchen"})
    gap = ((sink["box"][0] - hob["box"][0]) ** 2 + (sink["box"][1] - hob["box"][1]) ** 2) ** 0.5
    assert abs(gap - 1.3) < 1e-6  # 2 m run: each 0.35 m in from its end
