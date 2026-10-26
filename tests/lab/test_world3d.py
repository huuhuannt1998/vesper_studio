"""The 3D view's data (plan W1b): a home's render manifest, its doors, and the only HSSD files the page may get."""
import json

import pytest

from tests.lab.homes import catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.scenes import SceneError
from vesper.lab.world3d import load_world3d

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def at(x, z):
    return [1, 0, 0, x, 0, 1, 0, 0, 0, 0, 1, z, 0, 0, 0, 1]


def scene_home(tmp_path, items=None, hssd=None, files=("stages/s.glb", "objects/openings/211-2.glb")):
    """The test house as a home made from a scene: render.json, openings.json, and an HSSD folder with `files`."""
    root = tmp_path / "hssd"
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"glTF")
    items = items if items is not None else [
        {"kind": "stage", "asset": "stages/s.glb", "matrix": IDENTITY},
        {"kind": "object", "asset": "objects/openings/211-2.glb", "handle": "211-2_:0000", "opening": "door",
         "matrix": at(3.0, 4.5)},  # in the bedroom-hall doorway
        {"kind": "object", "asset": "objects/openings/218-20.glb", "handle": "218-20_:0000", "opening": "window",
         "matrix": at(0.0, 3.0)},  # a floor-length window where the front door is: not a door
        {"kind": "object", "asset": "objects/openings/211-2.glb", "handle": "211-2_:0001", "opening": "door",
         "matrix": at(6.0, 1.5)},  # a door in no doorway
    ]
    (tmp_path / "render.json").write_text(json.dumps({"format": "vesper-render/1", "scene": "test/house",
                                                      "floor_y": 0.25, "hssd": str(hssd or root), "items": items}))
    openings = [{"handle": i["handle"], "kind": i["opening"], "x": i["matrix"][3], "y": 1.1, "z": i["matrix"][11]}
                for i in items if "opening" in i]
    (tmp_path / "openings.json").write_text(json.dumps({"format": "vesper-openings/1", "openings": openings}))
    return load_lab_home(write_home(tmp_path), catalog())


def test_a_home_without_a_render_manifest_has_no_3d_scene(tmp_path):
    assert load_world3d(load_lab_home(write_home(tmp_path), catalog())) is None


def test_a_door_mesh_in_a_door_s_doorway_is_that_door_and_windows_are_not(tmp_path, monkeypatch):
    monkeypatch.delenv("VESPER_HSSD", raising=False)
    w = load_world3d(scene_home(tmp_path))
    items = w.manifest["items"]
    assert [i.get("doorway") for i in items] == [None, "bedroom-hall", None, None]
    assert items[1]["matrix"] == at(3.0, 4.5) and w.floor_y == 0.25
    assert w.root == tmp_path / "hssd" and w.missing == ["objects/openings/218-20.glb"]


@pytest.mark.parametrize("asset", ["../secret.glb", "/etc/passwd.glb", "objects/../../x.glb", "objects/a.txt", ""])
def test_a_manifest_that_names_a_file_outside_hssd_is_refused(tmp_path, asset):
    with pytest.raises(SceneError, match="render.json: item 2"):
        load_world3d(scene_home(tmp_path, items=[{"kind": "stage", "asset": "stages/s.glb", "matrix": IDENTITY},
                                                 {"kind": "object", "asset": asset, "matrix": IDENTITY}]))


ONE_BAD_ITEM = '{"format": "vesper-render/1", "scene": "test/house", "items": [{"matrix": [1]}]}'


@pytest.mark.parametrize("text, words", [("{", "cannot read"), ('{"format": "x"}', "not a VESPER render manifest"),
                                         (ONE_BAD_ITEM, "item 1")])
def test_a_broken_render_manifest_is_named(tmp_path, text, words):
    home = scene_home(tmp_path)
    (tmp_path / "render.json").write_text(text)
    with pytest.raises(SceneError, match=words):
        load_world3d(home)


def test_a_render_manifest_for_another_scene_is_refused(tmp_path):
    home = scene_home(tmp_path)
    manifest = json.loads((tmp_path / "render.json").read_text())
    (tmp_path / "render.json").write_text(json.dumps({**manifest, "scene": "hssd/102344022"}))
    with pytest.raises(SceneError, match="is for hssd/102344022, but the home is on test/house"):
        load_world3d(home)


def test_hssd_is_the_one_given_else_vesper_hssd_else_where_the_home_was_made(tmp_path, monkeypatch):
    home = scene_home(tmp_path)
    monkeypatch.delenv("VESPER_HSSD", raising=False)
    assert load_world3d(home).root == tmp_path / "hssd"
    monkeypatch.setenv("VESPER_HSSD", str(tmp_path / "moved"))
    assert load_world3d(home).root == tmp_path / "moved"
    assert load_world3d(home, tmp_path / "given").root == tmp_path / "given"
