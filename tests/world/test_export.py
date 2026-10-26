"""The world service's map export (spec §3, plan W1a): an HSSD scene's navmesh and regions become a vesper-map/1
file and the scene's door openings. The pure parts run here, under VESPER's Python; the package itself must stay
runnable in the Habitat environment (Python 3.9)."""
import ast
import json
import math
from pathlib import Path

import pytest

from vesper.lab.worldmap import WorldMap
from vesper_world import export
from vesper_world.__main__ import main

PACKAGE = Path(export.__file__).parent


def test_the_world_service_is_python_3_9_code():
    for f in sorted(PACKAGE.glob("*.py")):
        ast.parse(f.read_text(), filename=str(f), feature_version=(3, 9))  # no match, no 3.10+ syntax
        assert "from __future__ import annotations" in f.read_text(), f"{f.name}: X | Y annotations need it on 3.9"


def floor(x0, z0, x1, z1):
    """navigable(x, y, z) for a flat floor over [x0, x1] x [z0, z1] at height 0."""
    return lambda x, y, z: x0 <= x <= x1 and z0 <= z <= z1 and abs(y) < 0.5


def test_the_navmesh_is_rasterised_in_10_cm_cells_from_its_bounds():
    origin, width, height, cells = export.rasterize(floor(0.0, 0.0, 2.0, 1.0), ((0.0, 0.0, 0.0), (2.0, 1.0, 1.0)),
                                                    floor_y=0.0)
    assert origin == (0.0, 0.0) and (width, height) == (20, 10) and sum(cells) == 200


def test_only_the_largest_connected_floor_is_kept():
    width, height = 6, 3
    cells = [1, 1, 0, 0, 0, 1,
             1, 1, 0, 0, 0, 0,
             1, 1, 0, 1, 0, 0]  # a 2 x 3 floor, and two specks
    assert export.largest_part([bool(c) for c in cells], width, height) == [
        True, True, False, False, False, False,
        True, True, False, False, False, False,
        True, True, False, False, False, False]


def test_regions_become_rooms_with_stable_ids_and_outdoors_marked():
    regions = [
        {"name": "living room", "label": "living room", "poly_loop": [[0, 0, 0], [4, 0, 0], [4, 0, 3], [0, 0, 3]]},
        {"name": "closet.001", "label": "closet", "poly_loop": [[4, 0, 0], [5, 0, 0], [5, 0, 1]]},
        {"name": "outdoor", "label": "outdoor", "poly_loop": [[-3, 0, 0], [0, 0, 0], [0, 0, 3]]},
        {"name": "porch", "label": "porch/terrace/deck/driveway", "poly_loop": [[0, 0, 3], [1, 0, 3], [1, 0, 4]]},
        {"name": "living-room", "label": "living room", "poly_loop": [[9, 0, 0], [10, 0, 0], [10, 0, 1]]},
    ]
    rooms = export.rooms_from_regions(regions)
    assert [r["id"] for r in rooms] == ["living_room", "closet_001", "outdoor", "porch", "living_room_2"]
    assert rooms[0]["polygon"] == [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]  # (x, z): y is up
    assert [r["outdoor"] for r in rooms] == [False, False, True, True, False]


def test_a_door_opening_is_an_object_named_by_its_number_and_part():
    assert export.is_opening("211-2_:0001") and export.is_opening("4134-0_:0000")
    assert not export.is_opening("0a1b2c3d4e_:0000") and not export.is_opening("chair_:0000")


def test_an_opening_is_a_door_or_a_window_as_hssd_s_metadata_says():
    """Floor-length windows are 2.2 m tall, like doors: only HSSD's own category tells them apart (final review)."""
    kinds = {"211-2": "door", "4134-0": "window"}
    assert export.opening_kind("211-2_:0001", kinds) == "door"
    assert export.opening_kind("4134-0_:0000", kinds) == "window"
    assert export.opening_kind("999-9_:0000", kinds) is None


def test_the_kinds_are_read_from_hssd_s_object_metadata(tmp_path):
    csv = tmp_path / "fpmodels-with-decomposed.csv"
    csv.write_text("id,name,main_category,super_category\n"
                   "211-2,door,door,structure\n4134-0,\"window, tall\",window,structure\n"
                   "0a1b2c3d4e,chair,chair,furniture\n")
    assert export.opening_kinds(csv) == {"211-2": "door", "4134-0": "window"}


def test_an_upper_storey_with_more_rooms_is_still_not_the_ground_floor():
    regions = [{"name": "garden", "label": "outdoor", "floor_height": -0.9},
               {"name": "hall", "label": "hallway", "floor_height": 0.0},
               {"name": "bed1", "label": "bedroom", "floor_height": 2.81},
               {"name": "bed2", "label": "bedroom", "floor_height": 2.81}]
    assert export.ground_floor(regions) == 0.0  # not the lower garden, not the busier upstairs


def test_a_storeyed_scene_is_its_ground_floor_and_says_what_it_leaves_out():
    regions = [{"name": "kitchen", "floor_height": 0.0}, {"name": "bedroom", "floor_height": 2.81},
               {"name": "hall", "floor_height": 0.02}, {"name": "attic", "floor_height": 5.6}]
    assert export.ground_floor(regions) == 0.0
    kept, dropped = export.on_floor(regions, 0.0)
    assert [r["name"] for r in kept] == ["kitchen", "hall"] and [r["name"] for r in dropped] == ["bedroom", "attic"]
    assert export.storeys(regions) == [0.0, 2.81, 5.6]
    openings = [{"handle": "211-2_:0000", "y": 1.1}, {"handle": "211-2_:0001", "y": 3.9}]
    assert [o["handle"] for o in export.openings_on_floor(openings, 0.0)] == ["211-2_:0000"]


URDF = """<?xml version="1.0" ?>
<robot name="cabinet">
  <link name="root"/>
  <joint name="root_rotation" type="fixed"><parent link="root"/><child link="body"/></joint>
  <link name="body">
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="body.glb" scale="1.0 1.0 1.0"/></geometry>
    </visual>
    <collision><geometry><box size="1 1 1"/></geometry></collision>
  </link>
  <link name="door">
    <visual>
      <origin xyz="0.5 0 0" rpy="1.5707963 0 0"/><geometry><mesh filename="door.glb" scale="2 1 1"/></geometry>
    </visual>
    <visual><geometry><mesh filename="handle.glb"/></geometry></visual>
  </link>
</robot>
"""


def test_an_articulated_object_s_meshes_are_read_from_its_urdf():
    visuals = export.urdf_visuals(URDF)
    assert visuals == {"body": [("body.glb", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))],
                       "door": [("door.glb", (0.5, 0.0, 0.0), (1.5707963, 0.0, 0.0), (2.0, 1.0, 1.0)),
                                ("handle.glb", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))]}


def test_a_mesh_s_place_is_its_link_s_frame_then_its_urdf_origin():
    """URDF's roll, pitch and yaw turn about the fixed x, y and z axes; the spike matched Habitat to 1e-6."""
    m = export.trs((1, 2, 3), (math.pi / 2, 0, 0), (2, 1, 1))
    y_axis = [m[0][1], m[1][1], m[2][1]]  # where the mesh's +y goes: up turns to +z under a quarter roll
    assert [round(v, 6) for v in y_axis] == [0, 0, 1] and [m[0][3], m[1][3], m[2][3]] == [1, 2, 3]
    assert [round(m[0][0], 6), round(m[1][0], 6), round(m[2][0], 6)] == [2, 0, 0]  # the scale stays on its axis
    frame = [[0, 0, 1, 10], [0, 1, 0, 0], [-1, 0, 0, 5], [0, 0, 0, 1]]  # a link turned a quarter about y
    placed = export.matmul(frame, export.trs((1, 0, 0), (0, 0, 0), (1, 1, 1)))
    assert [placed[0][3], placed[1][3], placed[2][3]] == [10, 0, 4]
    assert export.flat(export.IDENTITY_ROWS) == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


class FakeScene:
    scene = "102344022"
    hssd = "/data/hssd-hab"
    floor_y = 0.0
    bounds = ((0.0, 0.0, 0.0), (3.0, 1.0, 2.0))
    regions = [{"name": "kitchen", "label": "kitchen", "floor_height": 0.0,
                "poly_loop": [[0, 0, 0], [1.4, 0, 0], [1.4, 0, 2], [0, 0, 2]]},
               {"name": "hallway", "label": "hallway", "floor_height": 0.0,
                "poly_loop": [[1.6, 0, 0], [3, 0, 0], [3, 0, 2], [1.6, 0, 2]]},
               {"name": "bedroom", "label": "bedroom", "floor_height": 2.8,  # upstairs: left out
                "poly_loop": [[0, 0, 0], [1.4, 0, 0], [1.4, 0, 2], [0, 0, 2]]}]
    openings = [{"handle": "211-2_:0000", "kind": "door", "x": 1.5, "y": 1.1, "z": 1.0},
                {"handle": "211-2_:0001", "kind": "door", "x": 1.5, "y": 3.9, "z": 1.0}]  # upstairs
    render = [{"kind": "stage", "asset": "stages/102344022.glb",
               "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
              {"kind": "object", "asset": "objects/openings/211-2.glb", "handle": "211-2_:0000",
               "opening": "door", "matrix": [1, 0, 0, 1.5, 0, 1, 0, 0, 0, 0, 1, 1.0, 0, 0, 0, 1]}]

    @staticmethod
    def navigable(x, y, z):  # two rooms, and a doorway at z 0.6..1.4 through the wall at x 1.4..1.6
        return 0 <= x <= 3 and 0 <= z <= 2 and (not 1.4 <= x <= 1.6 or 0.6 <= z <= 1.4)


def test_export_writes_a_map_vesper_loads_and_the_openings(tmp_path, monkeypatch, capsys):
    import vesper_world.__main__ as cli
    monkeypatch.setattr(cli, "load_scene", lambda hssd, scene, radius: FakeScene)
    assert main(["export", "--hssd", str(tmp_path), "--scene", "102344022", "--out", str(tmp_path / "out")]) == 0
    m = WorldMap.load(tmp_path / "out" / "map.json")
    assert m.scene == "hssd/102344022" and [r.id for r in m.rooms] == ["kitchen", "hallway"]
    assert [d.id for d in m.doorways] == ["hallway-kitchen"]
    openings = json.loads((tmp_path / "out" / "openings.json").read_text())
    assert openings == {"format": "vesper-openings/1", "scene": "hssd/102344022", "openings": FakeScene.openings[:1],
                        "floor": {"height": 0.0, "storeys": [0.0, 2.8], "left_out": ["bedroom"]}}
    render = json.loads((tmp_path / "out" / "render.json").read_text())
    assert render == {"format": "vesper-render/1", "scene": "hssd/102344022", "hssd": "/data/hssd-hab",
                      "floor_y": 0.0, "items": FakeScene.render}
    said = capsys.readouterr().out
    assert "2 rooms, 1 door, 0 windows" in said and "the ground floor of 2 storeys, leaving out bedroom" in said


def test_export_names_a_scene_it_cannot_load(tmp_path, monkeypatch, capsys):
    import vesper_world.__main__ as cli

    def missing(hssd, scene, radius):
        raise export.ExportError(f"scene {scene!r} is not in {hssd} (vesper lab scenes lists those that are)")
    monkeypatch.setattr(cli, "load_scene", missing)
    assert main(["export", "--hssd", str(tmp_path), "--scene", "999", "--out", str(tmp_path / "out")]) == 1
    assert "scene '999' is not in" in capsys.readouterr().err
    assert not (tmp_path / "out" / "map.json").exists()


@pytest.mark.parametrize("bad", [[], ["export"], ["export", "--scene", "1"], ["fly"]])
def test_a_bad_command_line_is_named(bad, capsys):
    with pytest.raises(SystemExit) as e:
        main(bad)
    assert e.value.code == 2


def test_a_scene_file_is_named_by_its_place_in_the_hssd_folder_even_when_it_links_elsewhere(tmp_path):
    """HSSD downloaded by an older huggingface_hub keeps its large files as links into the cache, and a subfolder may
    be another disk. The page asks the server for a file by its place in the folder, so that is its name."""
    from vesper_world.habitat_scene import _relative
    root, blobs = tmp_path / "hssd-hab", tmp_path / "cache" / "blobs"
    (root / "objects" / "a").mkdir(parents=True)
    blobs.mkdir(parents=True)
    (blobs / "f00d").write_bytes(b"glTF")
    (root / "objects" / "a" / "chair.glb").symlink_to(blobs / "f00d")
    (tmp_path / "disk" / "stages").mkdir(parents=True)
    (root / "stages").symlink_to(tmp_path / "disk" / "stages")
    assert _relative(root / "objects" / "a" / "chair.glb", root) == "objects/a/chair.glb"
    assert _relative(str(root / "stages" / "102344022.glb"), root) == "stages/102344022.glb"
    real = tmp_path / "real"
    (real / "objects").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)  # the folder given as a link, the file as its real place
    assert _relative(real / "objects" / "x.glb", tmp_path / "link") == "objects/x.glb"


def test_a_scene_file_outside_the_hssd_folder_is_named_not_a_crash(tmp_path):
    from vesper_world.habitat_scene import _relative
    with pytest.raises(export.ExportError, match="outside the HSSD folder"):
        _relative(tmp_path / "elsewhere" / "x.glb", tmp_path / "hssd-hab")
