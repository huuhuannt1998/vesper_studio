"""A home from a real HSSD scene, through the real world service in the Habitat environment: opt-in
(VESPER_HABITAT=1), like the Matter host contract test. Scene 102344022 must be downloaded (`vesper lab scenes`
says ready); it takes about 10 s and 0.5 GB. With Habitat's humanoids downloaded, their motions are made too."""
import os
from pathlib import Path

import pytest
import yaml

from vesper import cli
from vesper.lab.home import load_lab_home, spawn_point
from vesper.lab.humanoids import DEFAULT_HUMANOIDS, find_humanoids
from vesper.lab.scenes import habitat_python, hssd_dir, list_scenes
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock

SCENE = "102344022"
HSSD = hssd_dir(None)


def _ready() -> bool:
    try:
        return any(s.id == SCENE and s.ready for s in list_scenes(HSSD)) and habitat_python(None).is_file()
    except Exception:  # noqa: BLE001 - no HSSD here: the test is skipped
        return False


pytestmark = pytest.mark.skipif(os.environ.get("VESPER_HABITAT") != "1" or not _ready(),
                                reason=f"opt-in: VESPER_HABITAT=1, scene {SCENE} downloaded, the Habitat Python")


def test_a_real_scene_becomes_a_home_whose_rooms_doors_and_walks_work(tmp_path, capsys):
    home_dir = tmp_path / "home"
    assert cli.main(["lab", "new", str(home_dir), "--scene", SCENE, "--hssd", str(HSSD)]) == 0
    spec = yaml.safe_load((home_dir / "home.yaml").read_text())
    # The scene's 4 doors and 8 windows (HSSD's metadata): its 2.2 m glass windows are walls, not doors. Two doors
    # open onto outside HSSD did not annotate (the front door, the garage's): no doorway, and named.
    assert {d["doorway"] for d in spec["doors"]} == {"dining_room-outdoor", "bedroom-outdoor"}
    assert "2 doors of the scene stand in no doorway (203-0_:0000, 212-0_:0000)" in capsys.readouterr().out
    home = load_lab_home(Path(home_dir / "home.yaml"), {})
    assert len(home.map.rooms) == 20 and len(home.map.doorways) >= 20
    from vesper.lab.world3d import load_world3d
    w = load_world3d(home, HSSD)  # every mesh, placed by Habitat; the two doors swing with the home's doors
    assert len(w.manifest["items"]) == 219 and w.manifest["items"][0]["kind"] == "stage" and w.missing == []
    assert sorted(i["doorway"] for i in w.manifest["items"] if "doorway" in i) == ["bedroom-outdoor",
                                                                                "dining_room-outdoor"]
    assert home.map.room_at(*home.avatar["start"]).id == "living_room"
    s = Session(home, clock=LogicalClock())
    s.start()
    w, me = s.world, s.world.pose("player")
    bedroom = spawn_point(home.map, "bedroom")  # across the courtyard: through a door
    assert w.doors_on_route((me.x, me.z), bedroom) == [("dining_room-outdoor", "dining_room")]
    assert w.plan_work < sum(home.map.grid.walkable) / 10
    for door in list(w.doors):
        w.set_door(door, True)
    assert w.walk_to("player", bedroom) is not None
    s.clock.advance(120)
    assert w.pose("player").room == "bedroom"


@pytest.mark.skipif(not (DEFAULT_HUMANOIDS / "female_0" / "female_0.glb").is_file(),
                    reason="Habitat's humanoids are not downloaded (data/humanoids/humanoid_data)")
def test_habitat_s_humanoids_walk_as_in_habitat(tmp_path):
    import json
    h, said = find_humanoids({"female_0", "male_1"}, DEFAULT_HUMANOIDS, cache=tmp_path)
    assert said == "people: Habitat's humanoids (female_0, male_1)"
    m = json.loads(h.models["female_0"]["motion"].read_text())
    assert len(m["joints"]) == 54 and m["joints"][0] == "left_hip" and m["fps"] == 30.0
    assert len(m["walk"]) == 33 and abs(m["cycle_m"] - 1.31) < 0.01  # Habitat's walk: 1.31 m a cycle
