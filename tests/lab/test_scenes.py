"""HSSD scenes as Lab homes (spec §2, plan W1a): which scenes are here and ready, and a new home from one. The
world service is faked here (a stand-in for the Habitat environment's Python); tests/world covers the export and
tests/lab/test_world_export_live.py runs the real one, opt-in."""
import json
import os
import stat
import sys

import pytest
import yaml

from tests.lab.homes import catalog
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli
from vesper.lab.scenes import doors_from_openings, list_scenes, start_room


def fake_hssd(root, scene="102344022", missing_object=False):
    """A tiny HSSD tree: one scene with a stage, one object, one articulated object and two regions."""
    (root / "scenes-articulated").mkdir(parents=True, exist_ok=True)
    (root / "semantics" / "scenes").mkdir(parents=True, exist_ok=True)
    (root / "stages").mkdir(exist_ok=True)
    (root / "objects" / "a").mkdir(parents=True, exist_ok=True)
    (root / "urdf" / "ao1").mkdir(parents=True, exist_ok=True)
    (root / "hssd-hab-articulated.scene_dataset_config.json").write_text("{}")
    obj = f"a{scene}"
    (root / "scenes-articulated" / f"{scene}.scene_instance.json").write_text(json.dumps({
        "stage_instance": {"template_name": f"stages/{scene}"},
        "object_instances": [{"template_name": obj}],
        "articulated_object_instances": [{"template_name": "ao1"}]}))
    kitchen, yard = [[0, 0, 0], [4, 0, 0], [4, 0, 3], [0, 0, 3]], [[4, 0, 0], [6, 0, 0], [6, 0, 3], [4, 0, 3]]
    (root / "semantics" / "scenes" / f"{scene}.semantic_config.json").write_text(json.dumps({"region_annotations": [
        {"name": "kitchen", "label": "kitchen", "floor_height": 0.0, "poly_loop": kitchen},
        {"name": "yard", "label": "outdoor", "floor_height": 0.0, "poly_loop": yard},
    ]}))
    (root / "stages" / f"{scene}.stage_config.json").write_text(json.dumps({"render_asset": f"{scene}.glb"}))
    (root / "stages" / f"{scene}.glb").write_bytes(b"glb")
    (root / "objects" / "a" / f"{obj}.object_config.json").write_text(json.dumps({"render_asset": f"{obj}.glb"}))
    if not missing_object:
        (root / "objects" / "a" / f"{obj}.glb").write_bytes(b"glb")
    (root / "urdf" / "ao1" / "ao1.urdf").write_text("<robot/>")
    return root


def test_the_scenes_here_are_listed_with_their_rooms_area_and_whether_they_are_ready(tmp_path):
    hssd = fake_hssd(tmp_path / "hssd")
    fake_hssd(hssd, scene="102343992", missing_object=True)
    scenes = {s.id: s for s in list_scenes(hssd)}
    ready, partial = scenes["102344022"], scenes["102343992"]
    assert (ready.rooms, ready.labels, ready.indoor_m2, ready.missing) == (2, ["kitchen", "outdoor"], 12.0, [])
    assert partial.missing == ["objects/a/a102343992.glb"]
    assert [s.id for s in list_scenes(hssd)] == ["102344022", "102343992"]  # the ready ones first


def test_door_openings_on_a_doorway_become_its_doors_and_windows_do_not():
    m = house()  # hall-porch at (0.0, 3.0): the front door
    openings = [{"handle": "211-2_:0000", "x": 0.05, "y": 1.1, "z": 3.1},
                {"handle": "211-2_:0001", "x": -0.1, "y": 1.1, "z": 2.9},  # the same doorway again
                {"handle": "218-20_:0000", "x": 7.0, "y": 1.45, "z": 1.5}]  # a window in a wall
    assert doors_from_openings(m, openings) == ["hall-porch"]


def test_the_player_starts_in_the_entryway_or_living_room_with_floor_else_the_largest_room():
    assert start_room(house()) == "hall"
    from vesper.lab.worldmap import Room, WorldMap
    m = house()
    rooms = [Room("living_room", "living room", ((50, 50), (51, 50), (51, 51))),  # no floor in it
             *m.rooms]
    assert start_room(WorldMap(m.scene, rooms, m.grid)) == "hall"


FAKE_WORLD = """#!{python}
import json, sys
from pathlib import Path
sys.path.insert(0, {root!r})
from tests.lab.maps import house
args = sys.argv[1:]
assert args[:4] == ["-s", "-m", "vesper_world", "export"], args
out = Path(args[args.index("--out") + 1])
if {fail!r}:
    print("FAILED: scene '102344022': Habitat could not build its navmesh", file=sys.stderr)
    sys.exit(1)
out.mkdir(parents=True, exist_ok=True)
house().save(out / "map.json")
(out / "openings.json").write_text(json.dumps({{"format": "vesper-openings/1", "scene": "hssd/102344022",
    "openings": [{{"handle": "211-2_:0000", "x": 0.0, "y": 1.1, "z": 3.0}}]}}))
"""


def fake_python(tmp_path, fail=False):
    path = tmp_path / ("python-fail" if fail else "python")
    path.write_text(FAKE_WORLD.format(python=sys.executable, root=os.getcwd(), fail=fail))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_new_home_from_an_hssd_scene_has_the_scene_s_doors_and_passes_check(tmp_path, capsys):
    hssd = fake_hssd(tmp_path / "hssd")
    home = tmp_path / "myhome"
    assert cli.main(["lab", "new", str(home), "--scene", "102344022", "--hssd", str(hssd),
                     "--habitat-python", str(fake_python(tmp_path))]) == 0
    spec = yaml.safe_load((home / "home.yaml").read_text())
    assert spec["doors"] == [{"doorway": "hall-porch", "open": False}] and spec["avatar"]["start"]["room"] == "hall"
    assert (home / "map.json").exists() and (home / "openings.json").exists()
    out = capsys.readouterr().out
    assert "1 door from the scene's openings" in out and "the player starts in hall" in out
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    assert cli.main(["lab", "check", str(home / "home.yaml"), "--catalog", str(cat)]) == 0


@pytest.mark.parametrize("case, words", [
    ("missing", "scene 102344022 needs 1 file that is not in"),
    ("python", "the Habitat environment's Python"),
    ("fails", "Habitat could not build its navmesh"),
    ("unknown", "scene '999' is not in"),
])
def test_a_home_that_cannot_be_made_says_why_and_leaves_nothing(tmp_path, capsys, case, words):
    hssd = fake_hssd(tmp_path / "hssd", missing_object=case == "missing")
    python = tmp_path / "no-python" if case == "python" else fake_python(tmp_path, fail=case == "fails")
    scene = "999" if case == "unknown" else "102344022"
    home = tmp_path / "myhome"
    assert cli.main(["lab", "new", str(home), "--scene", scene, "--hssd", str(hssd), "--habitat-python",
                     str(python)]) == 1
    assert words in capsys.readouterr().err
    assert not home.exists()


def test_scenes_lists_what_is_here(tmp_path, capsys):
    hssd = fake_hssd(tmp_path / "hssd")
    fake_hssd(hssd, scene="102343992", missing_object=True)
    assert cli.main(["lab", "scenes", "--hssd", str(hssd)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:6] == ["102344022", "2", "rooms", "12", "m²", "ready"] and "(kitchen, outdoor)" in out[0]
    assert out[1].split()[:6] == ["102343992", "2", "rooms", "12", "m²", "missing"]
    assert cli.main(["lab", "scenes", "--hssd", str(tmp_path / "nowhere")]) == 1
