"""Apple RoomPlan exports (CapturedRoom / CapturedStructure JSON, as Polycam's Room mode and the 3D Scanner App write
them) read into a `vesper-plan/1` (scan import, task B1). The fixture is hand-written; the real scans under
.vesper/datasets/scans/ are read only when they are there."""
import copy
import json
import math
from pathlib import Path

import pytest

from vesper.lab.geometry import polygon_area
from vesper.lab.plan import check_house, load_plan, rectify
from vesper.lab.scan_roomplan import ScanError, plan_from_parts, read_roomplan_json

FIXTURES = Path(__file__).parent / "fixtures" / "roomplan"
SCANS = Path(__file__).resolve().parents[2] / ".vesper" / "datasets" / "scans"
if not SCANS.is_dir():  # a worktree: the test set lives in the main checkout
    SCANS = Path(__file__).resolve().parents[5] / ".vesper" / "datasets" / "scans"


def fixture() -> dict:
    return json.loads((FIXTURES / "two_rooms.json").read_text())


def area(room) -> float:
    return polygon_area([tuple(p) for p in room["polygon"]])


def by_type(plan, typ):
    found = [r for r in plan["rooms"] if r["type"] == typ]
    assert len(found) == 1, plan["rooms"]
    return found[0]


def doors_between(plan, a, b):
    return [d for d in plan["doors"] if set(d["rooms"]) == {a, b}]


def item(raw, kind, ident):
    return next(x for x in raw[kind] if x["identifier"] == ident)


def along(a, b, y, height):
    """A RoomPlan transform (column-major) and dimensions for a wall-like item from a to b (x, z), its middle at
    height y."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dz)
    ux, uz = dx / length, dz / length
    t = [ux, 0, uz, 0, 0, 1, 0, 0, -uz, 0, ux, 0, (a[0] + b[0]) / 2, y, (a[1] + b[1]) / 2, 1]
    return t, [length, height, 0]


def wall(ident, a, b, height=2.4, floor=-1.2, story=0):
    t, dims = along(a, b, floor + height / 2, height)
    return {"identifier": ident, "category": {"wall": {}}, "story": story, "parentIdentifier": None,
            "dimensions": dims, "transform": t}


def turned(raw, degrees):
    """The whole scan turned about the y axis (a phone held at another angle): positions and axes."""
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    out = copy.deepcopy(raw)

    def turn(x, z):
        return x * c - z * s, x * s + z * c
    for kind in ("walls", "doors", "windows", "openings", "floors", "objects"):
        for it in out[kind]:
            m = it["transform"]
            for col in (0, 2, 3):
                m[4 * col], m[4 * col + 2] = turn(m[4 * col], m[4 * col + 2])
    for sec in out["sections"]:
        sec["center"][0], sec["center"][2] = turn(sec["center"][0], sec["center"][2])
    return out


# --- the two-room fixture ----------------------------------------------------------------------------------------

def test_two_rooms_of_the_right_types_and_areas():
    plan, _, _ = read_roomplan_json(fixture())
    assert plan["format"] == "vesper-plan/1" and plan["units"] == "m"
    assert len(plan["rooms"]) == 2
    assert area(by_type(plan, "living")) == pytest.approx(20.0, rel=0.02)
    assert area(by_type(plan, "bedroom")) == pytest.approx(14.0, rel=0.02)


def test_one_interior_door_one_entrance_and_two_windows():
    plan, _, _ = read_roomplan_json(fixture())
    living, bedroom = by_type(plan, "living")["id"], by_type(plan, "bedroom")["id"]
    assert len(doors_between(plan, living, bedroom)) == 1
    outside = doors_between(plan, living, "outside")
    assert len(outside) == 1 and len(plan["doors"]) == 2
    assert plan["doors"][plan["entrance"]] is outside[0]
    assert outside[0]["at"] == pytest.approx([1.5, 4.0], abs=1e-6)
    assert outside[0]["width"] == pytest.approx(0.9)
    assert sorted(w["room"] for w in plan["windows"]) == sorted([living, bedroom])


def test_the_plan_loads_rectifies_and_is_a_house():
    plan, _, warnings = read_roomplan_json(fixture())
    assert load_plan(plan) is plan
    straight = rectify(plan, warnings)
    assert check_house(straight, warnings=warnings) == []
    assert len(straight["windows"]) == 2


def test_a_doors_parent_wall_places_it_not_its_nearest_wall():
    """A door at (4.85, 3.7) is 0.15 m from the middle wall and 0.3 m from the south wall: on the south wall (its
    parentIdentifier) it leads outside, on the middle wall into the bedroom."""
    raw = fixture()
    door = item(raw, "doors", "D-between")
    door["transform"][12], door["transform"][14] = 4.85, 3.7
    door["dimensions"][0] = 0.5
    plan, _, _ = read_roomplan_json(raw)
    living, bedroom = by_type(plan, "living")["id"], by_type(plan, "bedroom")["id"]
    [between] = doors_between(plan, living, bedroom)
    assert between["at"] == pytest.approx([5.0, 3.7], abs=1e-6)

    door["parentIdentifier"] = "W-south-living"
    door["transform"][:12] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]
    plan, _, _ = read_roomplan_json(raw)
    assert doors_between(plan, living, bedroom) == []
    ats = sorted(d["at"] for d in doors_between(plan, living, "outside"))
    assert ats == [pytest.approx([1.5, 4.0]), pytest.approx([4.85, 4.0])]


def test_a_door_naming_no_wall_stands_on_the_nearest_one_and_a_door_far_from_every_wall_is_left_out():
    raw = fixture()
    item(raw, "doors", "D-between")["parentIdentifier"] = None
    item(raw, "doors", "D-front")["parentIdentifier"] = "no-such-wall"
    plan, _, _ = read_roomplan_json(raw)
    assert len(doors_between(plan, by_type(plan, "living")["id"], by_type(plan, "bedroom")["id"])) == 1
    assert len(plan["doors"]) == 2
    item(raw, "doors", "D-front")["transform"][14] = 3.0  # 1 m from the south wall, 2 m from the middle one
    plan, _, warnings = read_roomplan_json(raw)
    assert len(plan["doors"]) == 1
    assert any("stands on no wall" in w for w in warnings), warnings


def test_walls_of_different_heights_are_built_with_a_warning():
    """RoomPlan scans no ceiling, and a wall seen only in part is short (1.59, 2.09, 2.42 m in one real bedroom)."""
    raw = fixture()
    for ident, h in (("W-north-living", 1.59), ("W-west", 2.09), ("W-east", 1.59), ("W-middle", 2.42)):
        w = item(raw, "walls", ident)
        w["dimensions"][1] = h
        w["transform"][13] = -1.2 + h / 2
    plan, _, warnings = read_roomplan_json(raw)
    assert len(plan["rooms"]) == 2
    assert any(w.startswith("ceiling not scanned: 2.5 m assumed") and "2.42" in w for w in warnings), warnings


def test_walls_of_the_usual_height_say_nothing_of_the_ceiling():
    raw = fixture()
    for w in raw["walls"]:
        w["dimensions"][1] = 2.5
    _, _, warnings = read_roomplan_json(raw)
    assert not any("ceiling" in w for w in warnings), warnings


def test_upstairs_elements_are_left_out_with_a_warning():
    raw = fixture()
    raw["walls"].append(wall("W-upstairs", (0, 0), (0, 9), floor=1.4, story=1))
    raw["sections"].append({"label": "kitchen", "center": [2, 1.4, 2], "story": 1})
    raw["objects"].append({**item(raw, "objects", "O-bed"), "identifier": "O-up", "story": 1})
    plan, objects, warnings = read_roomplan_json(raw)
    assert sorted(r["type"] for r in plan["rooms"]) == ["bedroom", "living"]
    assert len(objects) == 3
    assert any("story 1" in w and "one storey" in w for w in warnings), warnings


def test_an_opening_is_a_door_with_no_leaf():
    raw = fixture()
    front = item(raw, "doors", "D-front")
    raw["doors"].remove(front)
    raw["openings"].append({**front, "identifier": "O-front", "category": {"opening": {}}})
    plan, _, _ = read_roomplan_json(raw)
    living = by_type(plan, "living")["id"]
    [out] = doors_between(plan, living, "outside")
    assert plan["doors"][plan["entrance"]] is out


def test_the_entrance_is_a_door_with_a_leaf_before_an_opening_and_said_when_there_are_several():
    raw = fixture()
    raw["openings"].append({**item(raw, "doors", "D-front"), "identifier": "O-west", "category": {"opening": {}},
                            "parentIdentifier": "W-west",
                            "transform": [0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0.0, -0.185, 1.0, 1]})
    plan, _, warnings = read_roomplan_json(raw)
    assert plan["doors"][plan["entrance"]]["at"] == pytest.approx([1.5, 4.0])
    assert any("2 doors lead outside" in w for w in warnings), warnings


def test_no_door_outside_no_entrance_and_a_warning():
    raw = fixture()
    raw["doors"] = [d for d in raw["doors"] if d["identifier"] != "D-front"]
    plan, _, warnings = read_roomplan_json(raw)
    assert plan["entrance"] is None
    assert any("no door leads outside" in w for w in warnings), warnings
    assert any("no entrance" in p for p in check_house(rectify(plan)))


def test_objects_become_pieces_in_their_rooms():
    plan, objects, _ = read_roomplan_json(fixture())
    living, bedroom = by_type(plan, "living")["id"], by_type(plan, "bedroom")["id"]
    kinds = {o["kind"]: o for o in objects}
    assert set(kinds) == {"sofa", "bed", "cabinet"}
    assert kinds["sofa"]["room"] == living and kinds["bed"]["room"] == bedroom
    assert kinds["cabinet"]["room"] == bedroom
    assert kinds["bed"]["box"] == pytest.approx([5.95, 0.075, 7.55, 2.125])
    assert kinds["bed"]["h"] == pytest.approx(0.55) and kinds["bed"]["yaw"] == pytest.approx(0.0)
    assert kinds["cabinet"]["yaw"] == pytest.approx(90.0)
    assert kinds["cabinet"]["box"] == pytest.approx([7.55, 2.7, 8.75, 3.3])  # before its yaw: 1.2 m by 0.6 m


def test_a_turned_scan_gives_the_same_rooms():
    plan, _, warnings = read_roomplan_json(turned(fixture(), 31.0))
    assert area(by_type(plan, "living")) == pytest.approx(20.0, rel=0.02)
    assert area(by_type(plan, "bedroom")) == pytest.approx(14.0, rel=0.02)
    straight = rectify(plan, warnings)
    assert check_house(straight) == []
    assert len(straight["doors"]) == 2 and len(straight["windows"]) == 2


def test_no_sections_each_walled_region_is_a_room_typed_other():
    raw = fixture()
    raw["sections"] = []
    plan, _, _ = read_roomplan_json(raw)
    assert sorted(r["type"] for r in plan["rooms"]) == ["other", "other"]
    assert sorted(r["name"] for r in plan["rooms"]) == ["room 1", "room 2"]


def test_two_sections_in_one_walled_room_make_one_room_typed_by_the_named_one():
    raw = fixture()
    raw["walls"] = [w for w in raw["walls"] if w["identifier"] != "W-middle"]
    raw["doors"] = [d for d in raw["doors"] if d["identifier"] != "D-between"]
    raw["sections"][0]["label"] = "unidentified"
    plan, _, warnings = read_roomplan_json(raw)
    assert [r["type"] for r in plan["rooms"]] == ["bedroom"]
    assert area(plan["rooms"][0]) == pytest.approx(34.0, rel=0.02)
    assert any("one walled room" in w for w in warnings), warnings


def test_walls_that_stop_short_of_a_corner_are_joined_with_a_warning():
    raw = fixture()
    raw["walls"] = [w for w in raw["walls"] if w["identifier"] != "W-east"]
    raw["walls"].append(wall("W-east", (8.5, 0.3), (8.5, 4.0)))  # a scan that missed the last 30 cm
    item(raw, "windows", "N-bedroom")["parentIdentifier"] = "W-east"
    plan, _, warnings = read_roomplan_json(raw)
    assert area(by_type(plan, "bedroom")) == pytest.approx(14.0, rel=0.02)
    assert any("do not meet" in w for w in warnings), warnings


def test_a_section_outside_every_walled_region_is_left_out_with_a_warning():
    raw = fixture()
    raw["sections"].append({"label": "kitchen", "center": [20.0, -1.2, 20.0], "story": 0})
    plan, _, warnings = read_roomplan_json(raw)
    assert len(plan["rooms"]) == 2
    assert any("kitchen" in w and "no walled region" in w for w in warnings), warnings


# --- CapturedStructure -------------------------------------------------------------------------------------------

def separate_rooms() -> dict:
    """The fixture as two CapturedRooms, each scanned on its own: the bedroom's west wall stands 12 cm east of the
    living room's (a wall's thickness), as a structure merges them."""
    raw = fixture()
    living, bedroom = copy.deepcopy(raw), copy.deepcopy(raw)
    living["walls"] = [w for w in raw["walls"] if w["identifier"] in
                       ("W-north-living", "W-south-living", "W-west", "W-middle")]
    living["windows"] = [item(raw, "windows", "N-living")]
    living["sections"] = [raw["sections"][0]]
    living["objects"] = [item(raw, "objects", "O-sofa")]
    bedroom["walls"] = [wall("B-north", (5.12, 0), (8.5, 0)), wall("B-south", (5.12, 4), (8.5, 4)),
                        item(raw, "walls", "W-east"), wall("B-west", (5.12, 0), (5.12, 4))]
    bedroom["doors"], bedroom["openings"] = [], []
    bedroom["windows"] = [item(raw, "windows", "N-bedroom")]
    bedroom["sections"] = [raw["sections"][1]]
    bedroom["objects"] = [item(raw, "objects", "O-bed")]
    return {"version": 2, "rooms": [living, bedroom]}


def test_a_structure_of_two_rooms_is_read_in_one_frame():
    plan, objects, warnings = read_roomplan_json(separate_rooms())
    living, bedroom = by_type(plan, "living")["id"], by_type(plan, "bedroom")["id"]
    assert area(by_type(plan, "bedroom")) == pytest.approx(3.38 * 4, rel=0.02)
    assert len(doors_between(plan, living, bedroom)) == 1
    assert len(objects) == 2 and len(plan["windows"]) == 2
    straight = rectify(plan, warnings)
    assert check_house(straight) == []


def test_a_structure_with_its_own_merged_lists_reads_those():
    """Apple's CapturedStructure also lists every wall, door and section at its top level: those are read (the
    rooms' own lists would repeat them)."""
    raw = fixture()
    raw["rooms"] = [{"story": 0, "version": 2}, separate_rooms()["rooms"][1]]
    plan, _, _ = read_roomplan_json(raw)
    assert len(plan["rooms"]) == 2 and len(plan["doors"]) == 2


def test_not_a_roomplan_file_is_refused():
    with pytest.raises(ScanError, match="RoomPlan"):
        read_roomplan_json({"format": "vesper-plan/1"})
    with pytest.raises(ScanError, match="RoomPlan"):
        read_roomplan_json([1, 2])


def test_walls_enclosing_nothing_are_refused():
    raw = fixture()
    raw["walls"] = raw["walls"][:2]
    with pytest.raises(ScanError, match="enclose no room"):
        read_roomplan_json(raw)


# --- plan_from_parts (the USDZ reader feeds it too) -------------------------------------------------------------

def test_plan_from_parts_one_room():
    corners = [(0, 0), (4, 0), (4, 3), (0, 3)]
    walls = [{"id": f"w{k}", "a": list(corners[k]), "b": list(corners[(k + 1) % 4]), "height": 2.5} for k in range(4)]
    doors = [{"wall": "w0", "at": [1.0, 0.0], "width": 0.8, "bottom": 0.0, "top": 2.0}]
    windows = [{"wall": "w2", "at": [2.0, 3.0], "width": 1.0, "bottom": 0.9, "top": 2.1}]
    sections = [{"label": "kitchen", "center": [2.0, 1.5]}]
    objects = [{"category": "refrigerator", "center": [3.5, 0.9, 0.5], "size": [0.7, 1.8, 0.7], "yaw": 0.0}]
    plan, pieces, warnings = plan_from_parts(walls, doors, windows, [], sections, objects)
    assert [r["type"] for r in plan["rooms"]] == ["kitchen"]
    assert area(plan["rooms"][0]) == pytest.approx(12.0)
    assert plan["doors"][plan["entrance"]]["rooms"] == [plan["rooms"][0]["id"], "outside"]
    assert pieces[0]["kind"] == "refrigerator" and pieces[0]["room"] == plan["rooms"][0]["id"]
    assert warnings == []


# --- the real scans (skipped when the test set is not on this machine) ----------------------------------------

def scan(name: str) -> dict:
    path = SCANS / name
    if not path.is_file():
        pytest.skip(f"{path} is not here")
    return json.loads(path.read_text())


def test_the_litereality_bedroom():
    plan, objects, warnings = read_roomplan_json(scan("litereality-darwin-bedroom-roomplan/roomplan/room.json"))
    assert [r["type"] for r in plan["rooms"]] == ["bedroom"]
    assert 14.0 < area(plan["rooms"][0]) < 18.0
    assert len(plan["doors"]) == 2 and len(plan["windows"]) == 1 and plan["entrance"] is not None
    assert any(w.startswith("ceiling not scanned: 2.5 m assumed") for w in warnings)
    assert len(objects) == 9
    straight = rectify(plan, warnings)
    assert check_house(straight) == []


@pytest.mark.parametrize("name, types, doors, windows, problems", [
    ("litereality-girton-common-room-roomplan/roomplan/room.json", ["living"], 3, 2, []),
    # a 37 m² meeting room labelled diningRoom: part A's scale check (made for drawings, whose scale is read) takes a
    # dining room that large for a wrong scale; the scan's metres are right
    ("litereality-cued-meeting-room-roomplan/roomplan/room.json", ["dining"], 3, 3, ["the scale looks wrong"]),
])
def test_the_other_litereality_rooms(name, types, doors, windows, problems):
    plan, _, warnings = read_roomplan_json(scan(name))
    assert [r["type"] for r in plan["rooms"]] == types
    assert len(plan["doors"]) == doors and len(plan["windows"]) == windows and plan["entrance"] is not None
    straight = rectify(plan, warnings)
    found = check_house(straight)
    assert len(found) == len(problems) and all(f.startswith(p) for f, p in zip(found, problems, strict=True)), found


def test_the_domotwin_structure():
    """Synthetic, two rooms side by side (a door between, none outside) and an upstairs room."""
    plan, _, warnings = read_roomplan_json(scan("synthetic-domotwin-structure-roomplan/structure.json"))
    assert sorted(r["type"] for r in plan["rooms"]) == ["bedroom", "living"]
    living, bedroom = by_type(plan, "living")["id"], by_type(plan, "bedroom")["id"]
    assert len(doors_between(plan, living, bedroom)) == 1
    assert plan["entrance"] is None
    assert any("no door leads outside" in w for w in warnings)
    assert any("story 1" in w for w in warnings)
