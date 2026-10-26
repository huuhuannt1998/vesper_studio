"""HSSD scenes as Lab homes (spec §2, plan W1a): which scenes are here and ready, and a new home from one. The
world service is faked here (a stand-in for the Habitat environment's Python); tests/world covers the export and
tests/lab/test_world_export_live.py runs the real one, opt-in."""
import json
import stat
import sys
from pathlib import Path

import pytest
import yaml

from tests.lab.homes import catalog
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli
from vesper.lab.scenes import doors_from_openings, list_scenes, start_room, stray_doors

REPO = Path(__file__).resolve().parents[2]


def fake_hssd(root, scene="102344022", missing_object=False, upstairs=False):
    """A tiny HSSD tree laid out as HSSD is: one scene with a stage, one object (render and collision assets), one
    articulated object (its config, URDF and mesh), two regions, and the metadata that tells doors from windows."""
    for d in ("scenes-articulated", "semantics/scenes", "stages", "objects/a", "urdf/ao1", "metadata"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "hssd-hab-articulated.scene_dataset_config.json").write_text("{}")
    (root / "metadata" / "fpmodels-with-decomposed.csv").write_text(
        "id,name,main_category\n211-2,door,door\n218-20,window,window\n")
    obj = f"a{scene}"
    (root / "scenes-articulated" / f"{scene}.scene_instance.json").write_text(json.dumps({
        "stage_instance": {"template_name": f"stages/{scene}"},
        "object_instances": [{"template_name": obj}],
        "articulated_object_instances": [{"template_name": "ao1"}]}))
    kitchen, yard = [[0, 0, 0], [4, 0, 0], [4, 0, 3], [0, 0, 3]], [[4, 0, 0], [6, 0, 0], [6, 0, 3], [4, 0, 3]]
    regions = [{"name": "kitchen", "label": "kitchen", "floor_height": 0.0, "poly_loop": kitchen},
               {"name": "yard", "label": "outdoor", "floor_height": 0.0, "poly_loop": yard}]
    if upstairs:
        regions.append({"name": "bedroom", "label": "bedroom", "floor_height": 2.8, "poly_loop": kitchen})
    (root / "semantics" / "scenes" / f"{scene}.semantic_config.json").write_text(
        json.dumps({"region_annotations": regions}))
    (root / "stages" / f"{scene}.stage_config.json").write_text(json.dumps({"render_asset": f"{scene}.glb"}))
    (root / "stages" / f"{scene}.glb").write_bytes(b"glb")
    (root / "objects" / "a" / f"{obj}.object_config.json").write_text(
        json.dumps({"render_asset": f"{obj}.glb", "collision_asset": f"{obj}.collider.glb"}))
    if not missing_object:
        (root / "objects" / "a" / f"{obj}.glb").write_bytes(b"glb")
    (root / "objects" / "a" / f"{obj}.collider.glb").write_bytes(b"glb")
    (root / "urdf" / "ao1" / "ao1.ao_config.json").write_text(json.dumps({"urdf_filepath": "ao1.urdf"}))
    (root / "urdf" / "ao1" / "ao1.urdf").write_text('<robot><link><visual><geometry><mesh filename="ao1.glb"/>'
                                                    '</geometry></visual></link></robot>')
    (root / "urdf" / "ao1" / "ao1.glb").write_bytes(b"glb")
    return root


def test_the_scenes_here_are_listed_with_their_rooms_area_and_whether_they_are_ready(tmp_path):
    hssd = fake_hssd(tmp_path / "hssd")
    fake_hssd(hssd, scene="102343992", missing_object=True)
    scenes = {s.id: s for s in list_scenes(hssd)}
    ready, partial = scenes["102344022"], scenes["102343992"]
    assert (ready.rooms, ready.labels, ready.indoor_m2, ready.missing) == (2, ["kitchen", "outdoor"], 12.0, [])
    assert partial.missing == ["objects/a/a102343992.glb"]
    assert [s.id for s in list_scenes(hssd)] == ["102344022", "102343992"]  # the ready ones first


@pytest.mark.parametrize("gone, named", [
    ("objects/a/a102344022.collider.glb", "objects/a/a102344022.collider.glb"),
    ("objects/a/a102344022.object_config.json", "objects/a/a102344022.object_config.json"),
    ("urdf/ao1/ao1.ao_config.json", "urdf/ao1/ao1.ao_config.json"),
    ("urdf/ao1/ao1.urdf", "urdf/ao1/ao1.urdf"),
    ("urdf/ao1/ao1.glb", "urdf/ao1/ao1.glb"),
    ("metadata/fpmodels-with-decomposed.csv", "metadata/fpmodels-with-decomposed.csv"),
])
def test_a_scene_is_ready_only_with_every_file_habitat_loads_named_where_hssd_keeps_it(tmp_path, gone, named):
    hssd = fake_hssd(tmp_path / "hssd")
    (hssd / gone).unlink()
    assert list_scenes(hssd)[0].missing == [named]


@pytest.mark.parametrize("cut", ["objects/a/a102344022.object_config.json", "urdf/ao1/ao1.ao_config.json",
                                 "scenes-articulated/102344022.scene_instance.json",
                                 "semantics/scenes/102344022.semantic_config.json"])
def test_a_half_downloaded_file_is_named_not_a_traceback(tmp_path, cut):
    hssd = fake_hssd(tmp_path / "hssd")
    path = hssd / cut
    path.write_text(path.read_text()[:9])
    assert list_scenes(hssd)[0].missing == [f"{cut} (unreadable)"]


def test_a_storeyed_scene_is_its_ground_floor(tmp_path, capsys):
    hssd = fake_hssd(tmp_path / "hssd", upstairs=True)
    s = list_scenes(hssd)[0]
    assert (s.rooms, s.storeys, s.labels, s.indoor_m2) == (2, 2, ["kitchen", "outdoor"], 12.0)
    assert cli.main(["lab", "scenes", "--hssd", str(hssd)]) == 0
    assert "the ground floor of 2 storeys" in capsys.readouterr().out


def test_door_openings_on_a_doorway_become_its_doors_and_windows_do_not():
    m = house()  # hall-porch at (0.0, 3.0): the front door
    openings = [{"handle": "211-2_:0000", "kind": "door", "x": 0.05, "y": 1.1, "z": 3.1},
                {"handle": "211-2_:0001", "kind": "door", "x": -0.1, "y": 1.1, "z": 2.9},  # the same doorway again
                {"handle": "218-20_:0000", "kind": "window", "x": 7.0, "y": 1.45, "z": 1.5},  # a window in a wall
                {"handle": "218-20_:0001", "kind": "window", "x": 0.0, "y": 1.1, "z": 3.0},  # floor-length, on it
                {"handle": "999-9_:0000", "kind": "unknown", "x": 0.0, "y": 1.1, "z": 3.0}]
    assert doors_from_openings(m, openings) == ["hall-porch"]
    assert stray_doors(m, openings) == []


def test_a_floor_length_window_in_a_doorway_is_not_a_door():
    """The courtyard house's glass walls are 2.2 m windows: taken for doors, they made doorways (final review)."""
    window = {"handle": "218-20_:0001", "kind": "window", "x": 0.0, "y": 1.1, "z": 3.0}
    assert doors_from_openings(house(), [window]) == [] and stray_doors(house(), [window]) == []


def test_a_door_on_no_doorway_is_named():
    stray = {"handle": "211-2_:0009", "kind": "door", "x": 7.0, "y": 1.1, "z": 1.5}
    assert stray_doors(house(), [stray]) == ["211-2_:0009"]


def test_the_player_starts_in_the_entryway_or_living_room_with_floor_else_the_largest_room():
    assert start_room(house()) == "hall"
    from vesper.lab.worldmap import Room, WorldMap
    m = house()
    rooms = [Room("living_room", "living room", ((50, 50), (51, 50), (51, 51))),  # no floor in it
             *m.rooms]
    assert start_room(WorldMap(m.scene, rooms, m.grid)) == "hall"


FAKE_WORLD = """#!{python}
import json, os, sys
from pathlib import Path
# The world service sees vesper_world and nothing else of VESPER's environment (final review: a wheel install's
# site-packages on PYTHONPATH shadowed Habitat's numpy), and runs where no stray package can shadow it either.
path = os.environ["PYTHONPATH"]
assert sorted(os.listdir(path)) == ["vesper_world"] and os.path.samefile(os.getcwd(), path), os.listdir(path)
assert os.path.isfile(os.path.join(path, "vesper_world", "export.py"))
sys.path.insert(0, {root!r})
from tests.lab.maps import house
args = sys.argv[1:]
assert args[:4] == ["-s", "-m", "vesper_world", "export"], args
out = Path(args[args.index("--out") + 1])
assert out.is_absolute(), out
mode = {mode!r}
if mode == "fails":
    print("FAILED: scene '102344022': Habitat could not build its navmesh", file=sys.stderr)
    sys.exit(1)
if mode == "crash":
    print("Fatal Python error: Segmentation fault", file=sys.stderr)
    print("", file=sys.stderr)
    print("Current thread 0x00000001 (most recent call first):", file=sys.stderr)
    print('  File "habitat_scene.py", line 61 in load_scene', file=sys.stderr)
    sys.exit(139)
out.mkdir(parents=True, exist_ok=True)
house().save(out / "map.json")
openings = {{"format": "vesper-openings/1", "scene": "hssd/102344022",
    "openings": [{{"handle": "211-2_:0000", "kind": "door", "x": 0.0, "y": 1.1, "z": 3.0}},
                 {{"handle": "218-20_:0000", "kind": "window", "x": 7.0, "y": 1.45, "z": 1.5}},
                 {{"handle": "211-2_:0009", "kind": "door", "x": 7.0, "y": 1.1, "z": 1.5}}],
    "floor": {{"height": 0.0, "storeys": [0.0, 2.8], "left_out": ["bedroom", "bathroom"]}}}}
(out / "openings.json").write_text(json.dumps(openings))
if mode == "bad-map":
    (out / "map.json").write_text((out / "map.json").read_text()[:40])
if mode == "bad-openings":
    (out / "openings.json").write_text(json.dumps(openings)[:40])
"""


def fake_python(tmp_path, mode="ok"):
    path = tmp_path / f"python-{mode}"
    path.write_text(FAKE_WORLD.format(python=sys.executable, root=str(REPO), mode=mode))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def nothing_left(tmp_path, name="myhome"):
    return not (tmp_path / name).exists() and not [p.name for p in tmp_path.iterdir() if p.name.startswith(f".{name}")]


def test_a_new_home_from_an_hssd_scene_has_the_scene_s_doors_and_passes_check(tmp_path, capsys, monkeypatch):
    hssd = fake_hssd(tmp_path / "hssd")
    monkeypatch.chdir(tmp_path)  # a home named relative to where the user is
    assert cli.main(["lab", "new", "myhome", "--scene", "102344022", "--hssd", str(hssd),
                     "--habitat-python", str(fake_python(tmp_path))]) == 0
    home = tmp_path / "myhome"
    spec = yaml.safe_load((home / "home.yaml").read_text())
    assert spec["doors"] == [{"doorway": "hall-porch", "open": False}] and spec["avatar"]["start"]["room"] == "hall"
    assert (home / "map.json").exists() and (home / "openings.json").exists()
    assert nothing_left(tmp_path, "nohome") and not [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    out = capsys.readouterr().out
    assert "1 door from the scene's openings" in out and "the player starts in hall" in out
    assert "1 door of the scene stands in no doorway (211-2_:0009)" in out
    assert "the ground floor of 2 storeys; left out: bedroom, bathroom" in out
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    assert cli.main(["lab", "check", str(home / "home.yaml"), "--catalog", str(cat)]) == 0


@pytest.mark.parametrize("case, words", [
    ("missing", "scene 102344022 needs 1 file that is not in"),
    ("python", "the Habitat environment's Python"),
    ("fails", "Habitat could not build its navmesh"),
    ("crash", "the world service exited with 139: Fatal Python error: Segmentation fault / Current thread"),
    ("unknown", "scene '999' is not in"),
    ("bad-map", "cannot read the map file"),
    ("bad-openings", "openings.json"),
])
def test_a_home_that_cannot_be_made_says_why_and_leaves_nothing(tmp_path, capsys, case, words):
    hssd = fake_hssd(tmp_path / "hssd", missing_object=case == "missing")
    python = tmp_path / "no-python" if case == "python" else fake_python(tmp_path, case)
    scene = "999" if case == "unknown" else "102344022"
    assert cli.main(["lab", "new", str(tmp_path / "myhome"), "--scene", scene, "--hssd", str(hssd),
                     "--habitat-python", str(python)]) == 1
    err = capsys.readouterr().err
    assert words in err and "Traceback" not in err
    assert nothing_left(tmp_path)


def test_a_home_from_a_map_file_that_fails_leaves_nothing(tmp_path, capsys):
    from vesper.lab.worldmap import Room, WorldMap
    m = house()
    floorless = tmp_path / "floorless.json"
    WorldMap(m.scene, [Room("attic", "attic", ((50, 50), (51, 50), (51, 51)))], m.grid).save(floorless)
    assert cli.main(["lab", "new", str(tmp_path / "myhome"), "--map", str(floorless)]) == 1
    assert "no room has walkable floor" in capsys.readouterr().err
    assert nothing_left(tmp_path)


def test_a_home_interrupted_while_it_is_made_leaves_nothing(tmp_path, monkeypatch):
    import vesper.lab.scenes as scenes
    hssd = fake_hssd(tmp_path / "hssd")

    def interrupted(hssd, scene, out, python, run=None):
        house().save(Path(out) / "map.json")
        raise KeyboardInterrupt
    monkeypatch.setattr(scenes, "export_scene", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["lab", "new", str(tmp_path / "myhome"), "--scene", "102344022", "--hssd", str(hssd),
                  "--habitat-python", sys.executable])
    assert nothing_left(tmp_path)


def test_a_home_where_it_cannot_be_written_is_named_not_a_traceback(tmp_path, capsys):
    m = house()
    m.save(tmp_path / "map.json")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert cli.main(["lab", "new", str(locked / "myhome"), "--map", str(tmp_path / "map.json")]) == 1
    finally:
        locked.chmod(0o700)
    err = capsys.readouterr().err
    assert f"cannot make {locked / 'myhome'}" in err and "Permission denied" in err
    assert list(locked.iterdir()) == []


def test_scenes_lists_what_is_here(tmp_path, capsys):
    hssd = fake_hssd(tmp_path / "hssd")
    fake_hssd(hssd, scene="102343992", missing_object=True)
    assert cli.main(["lab", "scenes", "--hssd", str(hssd)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:6] == ["102344022", "2", "rooms", "12", "m²", "ready"] and "(kitchen, outdoor)" in out[0]
    assert out[1].split()[:6] == ["102343992", "2", "rooms", "12", "m²", "missing"]
    assert cli.main(["lab", "scenes", "--hssd", str(tmp_path / "nowhere")]) == 1
