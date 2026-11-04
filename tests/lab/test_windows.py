"""Windows (environment design 2026-11-03): by rule on each room's outside wall, never a wall with a doorway, by the
room's kind; an HSSD house's from its openings; and tall furniture kept out from in front of them."""
import json

from tests.lab.homes import catalog
from tests.lab.maps import house
from tests.lab.test_furnishing import make
from vesper.lab.home import load_lab_home
from vesper.lab.windows import FORMAT, blocks, from_openings, load_windows, plan_windows, write_windows


def test_rooms_get_windows_on_their_outside_wall_by_kind_and_a_hallway_none():
    ws = {w["room"]: w for w in plan_windows(house())}
    assert set(ws) == {"kitchen", "bedroom"}  # the hallway has none
    k, b = ws["kitchen"], ws["bedroom"]
    assert (k["side"], k["facing"], k["z"]) == ("z0", 0, -0.1)  # the plan's top: its longest outside wall
    assert (b["side"], b["facing"], b["z"]) == ("z1", 180, 6.1)
    assert k["sill"] == 1.05 and b["width"] == 1.2 and k["source"] == "rule"


def test_no_window_goes_on_a_wall_with_a_doorway():
    for w in plan_windows(house()):
        assert w["side"] != "x0"  # the kitchen's and the bedroom's doors are on their hall side


def test_a_generated_home_writes_windows_beside_it_and_its_furniture_keeps_clear_of_them(tmp_path):
    out = make(tmp_path)
    windows = load_windows(out)
    assert windows and json.loads((out / "windows.json").read_text())["format"] == FORMAT
    living = [w for w in windows if w["room"].startswith("living")]
    assert len(living) == 2
    items = json.loads((out / "furniture.json").read_text())["items"]
    for item in items:
        if item["solid"]:
            for w in windows:
                if w["room"] == item["room"]:
                    assert not blocks(item["box"], w, item["h"]), (item["kind"], w["id"])


def test_a_low_piece_may_sit_under_a_window_and_a_tall_one_may_not():
    w = {"side": "z0", "x": 2.0, "z": -0.1, "width": 1.2, "sill": 0.9, "height": 1.2}
    under = [1.2, 0.05, 2.8, 1.0]  # a sofa's footprint against that wall
    assert not blocks(under, w, 0.85)
    assert blocks(under, w, 2.0)  # a wardrobe there
    assert not blocks([5.0, 0.05, 6.0, 0.6], w, 2.0)  # beside it, not before it


def test_an_hssd_house_s_windows_face_their_nearest_room_wall(tmp_path):
    openings = [{"handle": "w1", "kind": "window", "x": 5.0, "y": 1.5, "z": 6.05},
                {"handle": "d1", "kind": "door", "x": 0.0, "y": 1.0, "z": 3.0}]
    ws = from_openings(house(), openings)
    assert len(ws) == 1 and ws[0]["room"] == "bedroom" and ws[0]["side"] == "z1" and ws[0]["source"] == "hssd"
    assert ws[0]["sill"] == 0.9  # its centre at 1.5 m, a 1.2 m pane (assumed)
    write_windows(tmp_path, ws)
    assert load_windows(tmp_path) == ws


def test_an_older_home_without_windows_has_none(tmp_path):
    assert load_windows(tmp_path) == []
    home_dir = make(tmp_path / "h")
    (home_dir / "windows.json").unlink()
    assert load_lab_home(home_dir / "home.yaml", catalog()).site is None
