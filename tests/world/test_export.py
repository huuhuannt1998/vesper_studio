"""The world service's map export (spec §3, plan W1a): an HSSD scene's navmesh and regions become a vesper-map/1
file and the scene's door openings. The pure parts run here, under VESPER's Python; the package itself must stay
runnable in the Habitat environment (Python 3.9)."""
import ast
import json
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


class FakeScene:
    scene = "102344022"
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
