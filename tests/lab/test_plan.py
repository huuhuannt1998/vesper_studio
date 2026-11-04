"""The floor-plan file (`vesper-plan/1`): validation, scaling, rectification and the house checks (floor-plan import,
task A1). Pure geometry: no image, no Claude, no files."""
import copy
import json
import math
import random

import pytest

from vesper.lab import plan as plan_module
from vesper.lab.geometry import polygon_area
from vesper.lab.plan import PlanError, check_house, load_plan, plan_transform, rectify, scale_plan


def rect(x0, z0, x1, z1):
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


def room(rid, typ, poly, name=None):
    return {"id": rid, "name": name or rid, "type": typ, "polygon": poly}


def plan(rooms, doors=(), windows=(), units="px", scale=None, entrance=None):
    return {"format": "vesper-plan/1", "units": units, "scale": scale, "rooms": list(rooms), "doors": list(doors),
            "windows": list(windows), "entrance": entrance}


def two_rooms(**kw):
    """A living room (100 x 60 px) and a kitchen (80 x 60 px) sharing the wall at x = 100."""
    return plan([room("living", "living", rect(0, 0, 100, 60)), room("kitchen", "kitchen", rect(100, 0, 180, 60))],
                doors=[{"at": [100, 30], "width": 9, "rooms": ["living", "kitchen"]}], **kw)


def by_id(p, rid):
    return next(r for r in p["rooms"] if r["id"] == rid)


def extent(r, axis):
    vals = [pt[axis] for pt in r["polygon"]]
    return max(vals) - min(vals)


def total_area(p):
    return sum(polygon_area([tuple(q) for q in r["polygon"]]) for r in p["rooms"])


def tilted(p, degrees):
    """The plan turned by `degrees` about its first corner: a slightly skewed scan."""
    t = math.radians(degrees)
    q = copy.deepcopy(p)
    turn = lambda p: [p[0] * math.cos(t) - p[1] * math.sin(t), p[0] * math.sin(t) + p[1] * math.cos(t)]  # noqa: E731
    for r in q["rooms"]:
        r["polygon"] = [turn(pt) for pt in r["polygon"]]
    for item in (*q["doors"], *q["windows"]):
        item["at"] = turn(item["at"])
    return q


# --- load_plan ---------------------------------------------------------------------------------------------------

def test_a_valid_plan_loads_unchanged():
    raw = two_rooms()
    before = copy.deepcopy(raw)
    assert load_plan(raw) is raw and raw == before


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(format="vesper-plan/2"), "format"),
    (lambda p: p.update(units="feet"), "units"),
    (lambda p: p.update(surprise=1), "surprise"),
    (lambda p: p["rooms"][0].update(type="spaceship"), "spaceship"),
    (lambda p: p["rooms"][1].update(id="living"), "duplicate room id 'living'"),
    (lambda p: p["doors"][0].update(rooms=["living", "garage"]), "garage"),
    (lambda p: p["doors"][0].update(rooms=["living", "living"]), "itself"),
    (lambda p: p["doors"][0].update(width=-1), "width"),
    (lambda p: p.update(entrance=3), "entrance"),
    (lambda p: p.update(scale={"m_per_px": 0}), "scale"),
    (lambda p: p.update(scale={"m_per_px": 0.1, "area_m2": 4}), "scale"),
    (lambda p: p["rooms"][0].update(polygon=[[0, 0], [1, "x"], [1, 1]]), "living"),
])
def test_an_invalid_plan_is_refused_with_what_is_wrong(mutate, message):
    raw = two_rooms()
    mutate(raw)
    with pytest.raises(PlanError, match=message):
        load_plan(raw)


def test_an_open_polygon_is_rejected_naming_the_room():
    raw = two_rooms()
    raw["rooms"][1]["polygon"] = [[100, 0], [180, 0]]  # a line, not a room
    with pytest.raises(PlanError, match=r"room 'kitchen'.*open"):
        load_plan(raw)
    raw["rooms"][1]["polygon"] = [[100, 0], [180, 0], [140, 0]]  # three points on a line: no area
    with pytest.raises(PlanError, match=r"room 'kitchen'.*no area"):
        load_plan(raw)


def test_a_self_intersecting_polygon_is_rejected_naming_the_room():
    raw = two_rooms()
    raw["rooms"][0]["polygon"] = [[0, 0], [100, 60], [100, 0], [0, 60]]  # a bow tie
    with pytest.raises(PlanError, match=r"room 'living'.*crosses itself"):
        load_plan(raw)
    raw["rooms"][0]["polygon"] = [[0, 0], [100, 0], [100, 60], [0, 60], [0, -10]]  # the last edge doubles back
    with pytest.raises(PlanError, match=r"room 'living'"):
        load_plan(raw)


def test_a_polygon_that_repeats_its_first_point_is_closed_not_open():
    raw = two_rooms()
    raw["rooms"][0]["polygon"] = rect(0, 0, 100, 60) + [[0, 0]]
    assert load_plan(raw) is raw


# --- scale_plan --------------------------------------------------------------------------------------------------

def test_an_area_scales_the_whole_plan_to_that_many_square_metres():
    scaled = scale_plan(two_rooms(), area_m2=30)
    assert scaled["units"] == "m" and scaled["scale"] is None
    assert total_area(scaled) == pytest.approx(30, rel=0.01)
    # one factor for both axes: the plan keeps its shape, and the door widths scale with it
    k = scaled["rooms"][0]["polygon"][1][0] / 100
    assert scaled["doors"][0]["width"] == pytest.approx(9 * k)
    assert by_id(scaled, "kitchen")["polygon"][1][0] == pytest.approx(180 * k)


def test_a_stated_length_scales_the_plan():
    p = two_rooms(scale={"length_m": 9.0, "from": [0, 0], "to": [180, 0]})
    scaled = scale_plan(p)
    assert extent(by_id(scaled, "kitchen"), 0) == pytest.approx(4.0)  # 80 px of the 180 px that are 9 m


def test_a_metres_per_pixel_scale_scales_the_plan():
    scaled = scale_plan(two_rooms(scale={"m_per_px": 0.05}))
    assert extent(by_id(scaled, "living"), 0) == pytest.approx(5.0)


def test_a_room_dimension_sets_that_room_to_that_size():
    scaled = scale_plan(two_rooms(), scale="kitchen width 3.2 m")
    assert extent(by_id(scaled, "kitchen"), 0) == pytest.approx(3.2)
    assert extent(by_id(scaled, "kitchen"), 1) == pytest.approx(3.2 * 60 / 80)  # the plan keeps its shape
    # length and depth are along the other axis
    for word in ("length", "depth"):
        s = scale_plan(two_rooms(), scale=f"living {word} 3 m")
        assert extent(by_id(s, "living"), 1) == pytest.approx(3.0)


def test_a_room_dimension_resolves_by_name_then_by_type():
    p = plan([room("r1", "bedroom", rect(0, 0, 50, 50), name="Guest room"),
              room("r2", "bedroom", rect(50, 0, 150, 50), name="Master bedroom")])
    assert extent(by_id(scale_plan(p, scale="master bedroom width 4 m"), "r2"), 0) == pytest.approx(4.0)
    with pytest.raises(PlanError, match=r"'bedroom'.*2 rooms"):  # a type that two rooms share is ambiguous
        scale_plan(p, scale="bedroom width 4 m")
    with pytest.raises(PlanError, match=r"'office'.*no room"):
        scale_plan(p, scale="office width 4 m")
    p["rooms"][1]["name"] = "Guest room"
    with pytest.raises(PlanError, match=r"'guest room'.*2 rooms"):
        scale_plan(p, scale="guest room width 4 m")


def test_a_room_dimension_follows_a_tilted_plan():
    p = tilted(two_rooms(), 7)
    scaled = scale_plan(p, scale="kitchen width 3.2 m")
    kitchen = by_id(scaled, "kitchen")["polygon"]
    # the kitchen's long side (80 px of 100 px living + 80 px kitchen) is 3.2 m, whatever way the page is turned
    assert math.dist(kitchen[0], kitchen[1]) == pytest.approx(3.2)


@pytest.mark.parametrize("text", ["kitchen is 3.2 m", "kitchen width", "kitchen width -3 m", "kitchen width 0 m",
                                  "kitchen height 3 m", "width 3.2 m", "kitchen width 3.2 feet"])
def test_a_scale_text_that_is_not_a_dimension_is_refused(text):
    with pytest.raises(PlanError, match="scale"):
        scale_plan(two_rooms(), scale=text)


def test_no_scale_is_refused_never_guessed():
    with pytest.raises(PlanError, match="no scale: give --scale or --area"):
        scale_plan(two_rooms())


def test_two_scales_at_once_are_refused():
    with pytest.raises(PlanError, match="not both"):
        scale_plan(two_rooms(), scale="kitchen width 3 m", area_m2=30)
    with pytest.raises(PlanError, match="area"):
        scale_plan(two_rooms(), area_m2=0)


def test_a_given_scale_beats_the_one_in_the_plan():
    p = two_rooms(scale={"m_per_px": 1.0})
    assert total_area(scale_plan(p, area_m2=30)) == pytest.approx(30, rel=0.01)


def test_a_plan_in_metres_needs_no_scale_and_stays_as_it_is():
    p = plan([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
             units="m")
    assert scale_plan(p) == p
    assert scale_plan(p) is not p
    assert total_area(scale_plan(p, area_m2=40)) == pytest.approx(40, rel=0.01)


# --- rectify -----------------------------------------------------------------------------------------------------

def metres(rooms, **kw):
    return plan(rooms, units="m", **kw)


def edges_axis_aligned(r, tol=1e-9):
    poly = r["polygon"]
    return all(abs(a[0] - b[0]) < tol or abs(a[1] - b[1]) < tol for a, b in zip(poly, poly[1:] + poly[:1]))


def test_edges_at_one_and_a_half_degrees_snap_to_the_axes():
    p = tilted(metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))]),
               1.5)
    assert not edges_axis_aligned(p["rooms"][0])
    out = rectify(p)
    for r in out["rooms"]:
        assert edges_axis_aligned(r)
    # the plan keeps its size
    assert polygon_area([tuple(q) for q in by_id(out, "living")["polygon"]]) == pytest.approx(20, rel=0.01)
    assert polygon_area([tuple(q) for q in by_id(out, "kitchen")["polygon"]]) == pytest.approx(14, rel=0.01)


def test_jittered_edges_snap_and_collinear_vertices_merge():
    jitter = [[0, 0], [2.5, 0.05], [5, 0], [5.04, 2], [5, 4], [0.03, 4], [0, 2]]  # a rectangle with drifting edges
    out = rectify(metres([room("living", "living", jitter)]))
    poly = by_id(out, "living")["polygon"]
    assert len(poly) == 4 and edges_axis_aligned(by_id(out, "living"))
    assert polygon_area([tuple(q) for q in poly]) == pytest.approx(20, rel=0.03)


def test_a_notch_narrower_than_a_wall_is_merged_away():
    notch = [[0, 0], [5, 0], [5, 4], [2.5, 4], [2.5, 4.1], [0, 4.1]]  # a 10 cm step in the north wall
    out = rectify(metres([room("living", "living", notch)]))
    assert len(by_id(out, "living")["polygon"]) == 4


def test_two_rooms_overlapping_by_20_cm_the_smaller_keeps_the_strip():
    p = metres([room("living", "living", rect(0, 0, 6, 5)), room("kitchen", "kitchen", rect(5.8, 0, 9, 5))])
    out = rectify(p)
    assert by_id(out, "kitchen")["polygon"] == [[5.8, 0], [9, 0], [9, 5], [5.8, 5]]
    assert polygon_area([tuple(q) for q in by_id(out, "living")["polygon"]]) == pytest.approx(5.8 * 5)
    # whichever order the rooms come in
    q = metres([room("kitchen", "kitchen", rect(5.8, 0, 9, 5)), room("living", "living", rect(0, 0, 6, 5))])
    assert polygon_area([tuple(r) for r in by_id(rectify(q), "living")["polygon"]]) == pytest.approx(5.8 * 5)


def test_an_overlap_that_would_split_or_hollow_a_room_is_refused():
    inside = metres([room("living", "living", rect(0, 0, 8, 6)), room("bathroom", "bathroom", rect(2, 2, 4, 4))])
    with pytest.raises(PlanError, match=r"room 'bathroom' lies inside 'living' by 2.00 m: redraw"):
        rectify(inside)
    split = metres([room("living", "living", rect(0, 0, 8, 6)), room("corridor", "corridor", rect(3, -1, 4, 7))])
    with pytest.raises(PlanError, match=r"'corridor'.*'living'"):
        rectify(split)


def test_a_wall_gap_narrower_than_25_cm_is_closed_to_the_middle_of_the_gap():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5.2, 0, 8.5, 4))])
    out = rectify(p)
    assert by_id(out, "living")["polygon"][1][0] == by_id(out, "kitchen")["polygon"][0][0] == pytest.approx(5.1)
    # a gap of a wall's thickness and more stays (these rooms are not one wall apart)
    far = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5.4, 0, 8.5, 4))])
    assert by_id(rectify(far), "living")["polygon"][1][0] == 5


def test_edges_of_different_rooms_within_15_cm_of_one_line_become_one_line():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0.1, 8.5, 4.1))])
    out = rectify(p)
    z = {pt[1] for pt in by_id(out, "living")["polygon"]} | {pt[1] for pt in by_id(out, "kitchen")["polygon"]}
    assert len(z) == 2  # one south wall and one north wall for the whole plan


def test_a_wall_gap_shared_with_several_neighbours_closes_to_one_wall_for_all_of_them():
    """An L-shaped living room, a bedroom below it and a hall beside it, each drawn 20 cm apart (as walls are)."""
    living = [[0, 0], [6, 0], [6, 3], [4, 3], [4, 5], [0, 5]]
    p = metres([room("living", "living", living), room("bed", "bedroom", rect(0, 5.2, 4, 9)),
                room("hall", "corridor", rect(4.2, 3.2, 10, 5.0)), room("kitchen", "kitchen", rect(6.2, 0, 10, 3))],
               doors=[{"at": [2, 5.1], "width": 0.9, "rooms": ["living", "bed"]},
                      {"at": [5, 3.1], "width": 0.9, "rooms": ["living", "hall"]},
                      {"at": [6.1, 1.5], "width": 0.9, "rooms": ["living", "kitchen"]},
                      {"at": [8, 3.1], "width": 0.9, "rooms": ["kitchen", "hall"]}])
    out = rectify(p)
    assert [d["rooms"] for d in out["doors"]] == [d["rooms"] for d in p["doors"]]
    assert rectify(out) == out
    xs = sorted({pt[0] for r in out["rooms"] for pt in r["polygon"]})
    zs = sorted({pt[1] for r in out["rooms"] for pt in r["polygon"]})
    assert xs == pytest.approx([0, 4.1, 6.1, 10]) and zs == pytest.approx([0, 3.1, 5.1, 9])  # each wall drawn once


def test_a_room_with_a_slanted_wall_is_left_as_drawn():
    tri = [[0, 0], [5, 0], [5, 4], [0, 1]]  # the north wall slants
    out = rectify(metres([room("living", "living", tri)]))
    assert by_id(out, "living")["polygon"] == [[0, 0], [5, 0], [5, 4], [0, 1]]


def test_a_skewed_jittered_plan_with_wall_gaps_rectifies_to_a_stable_house():
    base = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5.2, 0, 8.5, 4)),
                   room("hall", "corridor", rect(0, 4.2, 8.5, 5.4))],
                  doors=[{"at": [5.1, 2], "width": 0.9, "rooms": ["living", "kitchen"]},
                         {"at": [2, 4.1], "width": 0.9, "rooms": ["living", "hall"]},
                         {"at": [0, 2], "width": 0.9, "rooms": ["living", "outside"]}], entrance=2)
    for degrees in (-20, 3, 30, 44):
        out = rectify(tilted(base, degrees))
        assert rectify(out) == out
        assert all(edges_axis_aligned(r) for r in out["rooms"])
        assert polygon_area([tuple(q) for q in by_id(out, "living")["polygon"]]) == pytest.approx(5.1 * 4.1, rel=0.03)


def test_a_door_off_the_shared_wall_moves_onto_it():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               doors=[{"at": [5.3, 2], "width": 0.9, "rooms": ["living", "kitchen"]}])
    out = rectify(p)
    assert out["doors"][0]["at"] == [5, 2]
    assert out["doors"][0]["width"] == 0.9 and out["doors"][0]["rooms"] == ["living", "kitchen"]


def test_a_door_keeps_clear_of_the_corner_of_its_wall():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               doors=[{"at": [5.2, 3.95], "width": 0.9, "rooms": ["living", "kitchen"]}])
    assert rectify(p)["doors"][0]["at"] == [5, pytest.approx(3.55)]


def test_a_door_between_rooms_that_do_not_touch_is_rejected_naming_both_when_a_room_needs_it():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(7, 0, 10.5, 4))],
               doors=[{"at": [5, 2], "width": 0.9, "rooms": ["living", "kitchen"]},
                      {"at": [0, 2], "width": 0.9, "rooms": ["living", "outside"]}], entrance=1)
    with pytest.raises(PlanError, match=r"'living'.*'kitchen'.*do not touch.*'kitchen' cannot be reached"):
        rectify(p)


def test_a_door_far_from_the_wall_it_belongs_to_is_rejected():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               doors=[{"at": [2, 2], "width": 0.9, "rooms": ["living", "kitchen"]}])
    with pytest.raises(PlanError, match=r"door 0.*'living'.*'kitchen'.*0.5 m"):
        rectify(p)


def test_a_front_door_moves_onto_an_outside_wall_not_a_shared_one():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               doors=[{"at": [5, 3.9], "width": 0.9, "rooms": ["kitchen", "outside"]}])
    at = rectify(p)["doors"][0]["at"]
    assert at == [pytest.approx(5.45), 4]  # on the kitchen's north wall (clear of the corner), not the shared one
    boxed = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
                   doors=[{"at": [2, 2], "width": 0.9, "rooms": ["living", "outside"]}])
    with pytest.raises(PlanError, match=r"door 0.*'living'.*outside"):
        rectify(boxed)


def test_a_window_moves_onto_the_outside_wall_of_its_room():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               windows=[{"at": [2.5, 0.3], "width": 1.2, "room": "living"}])
    assert rectify(p)["windows"][0] == {"at": [2.5, 0], "width": 1.2, "room": "living"}
    far = copy.deepcopy(p)
    far["windows"][0]["at"] = [2.5, 2]  # no outside wall within 50 cm: left out (task A6), with a warning
    warnings = []
    assert rectify(far, warnings)["windows"] == [] and warnings == ["window 0 of 'living' is not on an outside wall: "
                                                                     "left out"]


def test_rectify_rotates_doors_and_windows_with_the_plan():
    p = tilted(metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
                      doors=[{"at": [5, 2], "width": 0.9, "rooms": ["living", "kitchen"]}]), 3)
    out = rectify(p)
    kitchen = by_id(out, "kitchen")["polygon"]
    wall_x = min(pt[0] for pt in kitchen)
    assert out["doors"][0]["at"][0] == pytest.approx(wall_x)


def test_rectify_is_stable_on_a_plan_that_is_already_clean():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               doors=[{"at": [5, 2], "width": 0.9, "rooms": ["living", "kitchen"]},
                      {"at": [0, 2], "width": 0.9, "rooms": ["living", "outside"]}],
               windows=[{"at": [2.5, 4], "width": 1.2, "room": "living"}], entrance=1)
    once = rectify(p)
    assert once == p
    assert rectify(once) == once


def test_rectify_does_not_touch_its_argument_and_wants_metres():
    p = metres([room("living", "living", rect(0, 0, 5.02, 4))])
    before = copy.deepcopy(p)
    rectify(p)
    assert p == before
    with pytest.raises(PlanError, match="metres"):
        rectify(two_rooms())


def test_rectify_keeps_the_room_ids_names_types_and_the_entrance():
    p = metres([room("a", "bedroom", rect(0, 0, 3, 4), name="Kid's room")],
               doors=[{"at": [0, 2], "width": 0.9, "rooms": ["a", "outside"]}], entrance=0)
    out = rectify(p)
    assert out["entrance"] == 0 and out["format"] == "vesper-plan/1" and out["units"] == "m"
    assert (out["rooms"][0]["id"], out["rooms"][0]["name"], out["rooms"][0]["type"]) == ("a", "Kid's room", "bedroom")


# --- check_house -------------------------------------------------------------------------------------------------

def house():
    """A living room, a kitchen and a bedroom off a corridor, with the front door in the living room."""
    return metres(
        [room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4)),
         room("hall", "corridor", rect(0, 4, 8.5, 5.2)), room("bed", "bedroom", rect(0, 5.2, 3.5, 8.7))],
        doors=[{"at": [0, 2], "width": 0.9, "rooms": ["living", "outside"]},
               {"at": [5, 2], "width": 0.9, "rooms": ["living", "kitchen"]},
               {"at": [2, 4], "width": 0.9, "rooms": ["living", "hall"]},
               {"at": [2, 5.2], "width": 0.9, "rooms": ["hall", "bed"]}], entrance=0)


def test_a_connected_plausible_plan_is_a_house():
    assert check_house(house()) == []
    assert check_house(rectify(house())) == []


def test_a_room_nothing_leads_to_is_listed_as_unreachable():
    p = house()
    del p["doors"][3]  # the bedroom loses its door
    problems = check_house(p)
    assert len(problems) == 1 and "'bed'" in problems[0] and "reachable" in problems[0]


def test_every_failing_room_is_listed():
    p = house()
    del p["doors"][3]
    p["rooms"][0]["polygon"] = rect(0, 0, 1.4, 1.4)  # a 2 m² living room
    p["rooms"][1]["polygon"] = rect(5, 0, 5.5, 0.5)  # a 0.25 m² kitchen
    problems = check_house(p)
    text = "\n".join(problems)
    assert "'living'" in text and "'kitchen'" in text and "'bed'" in text
    assert any("'living'" in s and "plausible" in s for s in problems)


def test_a_2_m2_living_room_is_implausible():
    p = metres([room("living", "living", rect(0, 0, 1, 2))],
               doors=[{"at": [0, 1], "width": 0.9, "rooms": ["living", "outside"]}], entrance=0)
    problems = check_house(p)
    assert len(problems) == 1 and "'living'" in problems[0] and "2.0 m²" in problems[0] and "8.0" in problems[0]


def test_the_plausible_range_is_half_the_smallest_to_one_and_a_half_times_the_largest():
    def only(typ, w, d):
        return metres([room("r", typ, rect(0, 0, w, d))],
                      doors=[{"at": [0, d / 2], "width": 0.9, "rooms": ["r", "outside"]}], entrance=0)
    assert check_house(only("living", 2, 4)) == []  # 8 m² = 0.5 x 16
    assert check_house(only("living", 6, 7)) == []  # 42 m² = 1.5 x 28
    assert check_house(only("living", 2, 3.9))
    assert check_house(only("living", 6, 7.1))
    assert check_house(only("other", 1, 2)) == []  # types with no typical area: 2 to 60 m²
    assert check_house(only("other", 1, 1.9))
    assert check_house(only("hall", 6, 10)) == []
    assert check_house(only("hall", 6, 10.1))


def test_a_plan_without_a_front_door_is_not_a_house():
    p = house()
    p["entrance"] = None
    assert any("entrance" in s for s in check_house(p))
    p["entrance"] = 1  # a door between two rooms is no way in
    assert any("entrance" in s and "outside" in s for s in check_house(p))


def test_overlapping_rooms_are_listed():
    p = house()
    p["rooms"][1]["polygon"] = rect(4, 0, 8.5, 4)
    assert any("'living'" in s and "'kitchen'" in s and "overlap" in s for s in check_house(p))


def test_check_house_wants_metres_and_a_valid_plan():
    with pytest.raises(PlanError, match="metres"):
        check_house(two_rooms())
    bad = house()
    bad["rooms"][0]["polygon"] = [[0, 0], [1, 1]]
    with pytest.raises(PlanError, match="living"):
        check_house(bad)


# --- review fixes ------------------------------------------------------------------------------------------------

def test_abutting_rooms_drawn_with_a_few_cm_of_jitter_still_share_a_wall():
    p = metres([room("a", "living", rect(0, 0, 4, 4)), room("b", "kitchen", [[4, 0], [8, 0], [8, 4], [3.96, 4]])],
               doors=[{"at": [4, 2], "width": 0.9, "rooms": ["a", "b"]}])
    out = rectify(p)
    assert out["doors"][0]["at"][0] == by_id(out, "a")["polygon"][1][0] == by_id(out, "b")["polygon"][0][0]
    assert rectify(out) == out


def grid_house(rng, degrees):
    """A 3 x 2 grid of rooms that touch exactly (no gaps), each drawn with 3 cm of jitter, tilted, a door on every
    shared wall and one to the outside."""
    xs, zs = [0.0], [0.0]
    for _ in range(3):
        xs.append(xs[-1] + rng.uniform(3.6, 5.0))
    for _ in range(2):
        zs.append(zs[-1] + rng.uniform(3.6, 4.5))
    types = ["living", "bedroom", "dining", "dining", "bedroom", "living"]
    jit = lambda: rng.uniform(-0.03, 0.03)  # noqa: E731
    rooms, doors = [], []
    for j in range(2):
        for i in range(3):
            c = [(xs[i], zs[j]), (xs[i + 1], zs[j]), (xs[i + 1], zs[j + 1]), (xs[i], zs[j + 1])]
            rooms.append(room(f"r{j}{i}", types[3 * j + i], [[x + jit(), z + jit()] for x, z in c]))
            if i < 2:
                doors.append({"at": [xs[i + 1], (zs[j] + zs[j + 1]) / 2], "width": 0.9,
                              "rooms": [f"r{j}{i}", f"r{j}{i + 1}"]})
            if j == 0:
                doors.append({"at": [(xs[i] + xs[i + 1]) / 2, zs[1]], "width": 0.9, "rooms": [f"r0{i}", f"r1{i}"]})
    doors.append({"at": [0.0, zs[1] / 2], "width": 0.9, "rooms": ["r00", "outside"]})
    return tilted(metres(rooms, doors=doors, entrance=len(doors) - 1), degrees)


def test_gridded_houses_with_jitter_and_tilt_all_rectify_to_a_stable_house():
    rng = random.Random("vesper-plan-fuzz")
    for n in range(30):
        p = grid_house(rng, rng.uniform(-8, 8))
        out = rectify(p)
        assert rectify(out) == out, n
        assert check_house(out) == [], n
        assert all(edges_axis_aligned(r) for r in out["rooms"]), n


def test_a_wall_at_an_angle_that_would_move_a_corner_far_is_left_as_drawn():
    slanted = [[0, 0], [5, 0], [5, 4], [0, 4.83]]  # the north wall is 9.4 degrees off: snapping would move it 0.4 m
    out = rectify(metres([room("living", "living", slanted)]))
    assert by_id(out, "living")["polygon"] == slanted
    nearly = [[0, 0], [5, 0], [5, 4], [0, 4.12]]  # 1.4 degrees off: straightened
    assert len(by_id(rectify(metres([room("living", "living", nearly)])), "living")["polygon"]) == 4
    assert edges_axis_aligned(by_id(rectify(metres([room("living", "living", nearly)])), "living"))


def test_rooms_30_cm_apart_with_a_door_between_them_share_a_wall():
    p = metres([room("a", "living", rect(0, 0, 4, 4)), room("b", "kitchen", rect(4.3, 0, 8, 4))],
               doors=[{"at": [4.15, 2], "width": 0.9, "rooms": ["a", "b"]}])
    out = rectify(p)
    assert by_id(out, "a")["polygon"][1][0] == by_id(out, "b")["polygon"][0][0] == pytest.approx(4.15)
    assert out["doors"][0]["at"] == [pytest.approx(4.15), 2]
    p["doors"] = []
    assert rectify(p)["rooms"] == p["rooms"]  # without a door the rooms stay as drawn


def test_a_door_does_not_pull_together_walls_far_from_it():
    p = metres([room("a", "living", rect(0, 0, 4, 4)), room("b", "kitchen", rect(4.3, 0, 8, 4)),
                room("c", "office", rect(0, 4.3, 3, 8))],
               doors=[{"at": [4.15, 2], "width": 0.9, "rooms": ["a", "b"]}])
    out = rectify(p)
    assert by_id(out, "c")["polygon"] == rect(0, 4.3, 3, 8)


def test_check_house_finds_an_overlap_of_rooms_that_are_not_rectilinear():
    chamfered = [[0, 0], [4.6, 0], [5, 0.4], [5, 4], [0, 4]]  # the corner is cut at 45 degrees
    p = house()
    p["rooms"][0]["polygon"] = chamfered
    p["rooms"][1]["polygon"] = rect(4.6, 0, 8.5, 4)  # 0.4 m into the living room
    assert any("'living'" in s and "'kitchen'" in s and "overlap" in s for s in check_house(p))
    p["rooms"][1]["polygon"] = rect(5, 0, 8.5, 4)
    assert not any("overlap" in s for s in check_house(p))


JUNK = [10**400, 1e308, None, 3, -1.5, "x", "", [], {}, [1], [[1, 2]], True, {"a": 1}, float("nan")]


def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for k, v in node.items():
            yield from paths(v, (*prefix, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, (*prefix, i))


def test_any_field_of_the_wrong_type_is_a_plan_error_never_a_crash():
    base = house()
    base["windows"] = [{"at": [2.5, 0], "width": 1.2, "room": "living"}]
    base["scale"] = {"length_m": 3.0, "from": [0, 0], "to": [1, 0]}
    for path in paths(base):
        if not path:
            continue
        for junk in [*JUNK, "<delete>"]:
            plan = copy.deepcopy(base)
            parent = plan
            for key in path[:-1]:
                parent = parent[key]
            if junk == "<delete>":
                if isinstance(parent, dict):
                    del parent[path[-1]]
            else:
                parent[path[-1]] = junk
            try:
                load_plan(plan)
            except PlanError:
                continue
            for call in (lambda p: scale_plan(p, area_m2=50), lambda p: rectify(p) if p["units"] == "m" else None):
                try:
                    call(plan)
                except PlanError:
                    pass
    plan = house()
    plan["windows"] = [{"at": [1, 0], "width": 1, "room": ["living"]}]
    with pytest.raises(PlanError, match="window 0"):
        load_plan(plan)


def test_a_door_near_a_corner_is_measured_to_its_wall_then_kept_clear_of_the_corner():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
               doors=[{"at": [5.39, 3.95], "width": 0.9, "rooms": ["living", "kitchen"]}])
    assert rectify(p)["doors"][0]["at"] == [5, pytest.approx(3.55)]


def test_a_room_drawn_inside_another_is_refused_with_how_far_in_it_is():
    p = metres([room("living", "living", rect(0, 0, 8, 6)), room("kitchen", "kitchen", rect(2, 1.5, 5, 4.5))])
    with pytest.raises(PlanError, match=r"room 'kitchen' lies inside 'living' by 3.00 m: redraw them as separate"):
        rectify(p)


def test_plan_transform_is_the_turn_rectify_applies():
    base = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5, 0, 8.5, 4))],
                  doors=[{"at": [5, 2], "width": 0.9, "rooms": ["living", "kitchen"]}])
    assert plan_transform(base) == {"angle_deg": 0.0, "centre": [pytest.approx(4.625), pytest.approx(2)]}
    p = tilted(base, 6)
    tf = plan_transform(p)
    assert tf["angle_deg"] == pytest.approx(-6)
    t, (cx, cz) = math.radians(tf["angle_deg"]), tf["centre"]
    turn = lambda q: (cx + (q[0] - cx) * math.cos(t) - (q[1] - cz) * math.sin(t),  # noqa: E731
                      cz + (q[0] - cx) * math.sin(t) + (q[1] - cz) * math.cos(t))
    out = rectify(p)
    for before, after in zip(p["rooms"], out["rooms"], strict=True):
        for a, b in zip(before["polygon"], after["polygon"], strict=True):
            assert turn(a) == pytest.approx(tuple(b), abs=1e-6)
    assert turn(p["doors"][0]["at"]) == pytest.approx(tuple(out["doors"][0]["at"]), abs=1e-6)


# Plans a reviewer's fuzz found refused as 'rooms do not touch': walls joined first used up the 25 cm span that
# a door-joined pair of walls (a few mm apart) needed.
DOOR_PAIRS_REFUSED = [
    json.loads(
        '{"format":"vesper-plan/1","units":"m","scale":null,"rooms":[{"id":"r00","name":"r00","type":"oth'
        'er","polygon":[[0.0084,0.0018],[3.4247,0.4125],[2.9969,3.869],[-0.417,3.4561]]},{"id":"r01","nam'
        'e":"r01","type":"other","polygon":[[-0.4133,3.4297],[3.0073,3.8114],[2.5793,7.1184],[-0.8204,6.6'
        '66]]},{"id":"r10","name":"r10","type":"other","polygon":[[3.291,0.4368],[6.3556,0.8236],[5.9354,'
        '4.2213],[2.9089,3.8813]]},{"id":"r11","name":"r11","type":"other","polygon":[[3.1538,3.9237],[5.'
        '9152,4.2628],[5.4973,7.4498],[2.7478,7.0981]]}],"doors":[{"at":[1.2709,3.6532],"width":0.8,"room'
        's":["r00","r01"]},{"at":[3.1894,2.1471],"width":0.8,"rooms":["r00","r10"]},{"at":[2.7707,5.4832]'
        ',"width":0.8,"rooms":["r01","r11"]},{"at":[4.4423,4.0512],"width":0.8,"rooms":["r10","r11"]},{"a'
        't":[-0.2158,1.7197],"width":0.9,"rooms":["r00","outside"]}],"windows":[],"entrance":4}'),
    json.loads(
        '{"format":"vesper-plan/1","units":"m","scale":null,"rooms":[{"id":"r00","name":"r00","type":"oth'
        'er","polygon":[[0.0223,-0.003],[4.7957,-0.5801],[5.3116,3.8127],[0.5239,4.4085]]},{"id":"r01","n'
        'ame":"r01","type":"other","polygon":[[0.5544,4.2093],[5.3031,3.6155],[5.7537,7.5026],[0.9914,8.0'
        '607]]},{"id":"r10","name":"r10","type":"other","polygon":[[4.6128,-0.5682],[8.158,-1.0444],[8.68'
        '63,3.4276],[5.1664,3.8414]]},{"id":"r11","name":"r11","type":"other","polygon":[[5.304,3.8043],['
        '8.7045,3.3828],[9.1619,7.054],[5.751,7.4694]]},{"id":"r20","name":"r20","type":"other","polygon"'
        ':[[8.0232,-1.0197],[11.634,-1.424],[12.1942,2.9862],[8.5285,3.416]]},{"id":"r21","name":"r21","t'
        'ype":"other","polygon":[[8.6505,3.2843],[12.202,2.8338],[12.6852,6.658],[9.1323,7.0812]]},{"id":'
        '"r30","name":"r30","type":"other","polygon":[[11.5282,-1.459],[14.7327,-1.8261],[15.2811,2.5652]'
        ',[12.0835,2.9767]]},{"id":"r31","name":"r31","type":"other","polygon":[[12.0869,2.793],[15.2331,'
        '2.4229],[15.6938,6.2592],[12.5644,6.646]]}],"doors":[{"at":[2.9356,4.1175],"width":0.8,"rooms":['
        '"r00","r01"]},{"at":[5.0467,1.6132],"width":0.8,"rooms":["r00","r10"]},{"at":[5.5502,5.6571],"wi'
        'dth":0.8,"rooms":["r01","r11"]},{"at":[7.012,3.61],"width":0.8,"rooms":["r10","r11"]},{"at":[8.4'
        '277,1.1922],"width":0.8,"rooms":["r10","r20"]},{"at":[8.9312,5.2361],"width":0.8,"rooms":["r11",'
        '"r21"]},{"at":[10.4574,3.181],"width":0.8,"rooms":["r20","r21"]},{"at":[11.9374,0.7553],"width":'
        '0.8,"rooms":["r20","r30"]},{"at":[12.4409,4.7992],"width":0.8,"rooms":["r21","r31"]},{"at":[13.7'
        '372,2.7727],"width":0.8,"rooms":["r30","r31"]},{"at":[0.2748,2.2073],"width":0.9,"rooms":["r00",'
        '"outside"]}],"windows":[],"entrance":10}'),
]


@pytest.mark.parametrize("plan", DOOR_PAIRS_REFUSED, ids=["mixed", "overlap"])
def test_a_door_joined_pair_of_walls_closes_even_when_a_cluster_has_used_up_the_usual_span(plan):
    out = rectify(plan)
    assert rectify(out) == out
    assert check_house(out) == []


def thick_house(rng, wall, tilt_deg, jitter=0.02):
    """A 3 x 2 grid whose interior walls are `wall` thick (the rooms' outlines stop short of the grid lines), a door
    in every wall, one to the outside, jittered and tilted."""
    cols = [0, *sorted(rng.sample([3, 4, 5, 6, 7], 2)), 10]
    rows = [0, 4, 8]
    h = wall / 2
    rooms, doors = [], []
    for i in range(3):
        for j in range(2):
            rooms.append(room(f"r{i}{j}", "other", rect(cols[i] + (h if i else 0), rows[j] + (h if j else 0),
                                                       cols[i + 1] - (h if i < 2 else 0),
                                                       rows[j + 1] - (h if j < 1 else 0))))
    for i in range(3):
        doors.append({"at": [(cols[i] + cols[i + 1]) / 2, 4], "width": 0.8, "rooms": [f"r{i}0", f"r{i}1"]})
    for i in range(2):
        for j in range(2):
            doors.append({"at": [cols[i + 1], rows[j] + 2], "width": 0.8, "rooms": [f"r{i}{j}", f"r{i + 1}{j}"]})
    doors.append({"at": [0, 2], "width": 0.9, "rooms": ["r00", "outside"]})
    p = tilted(metres(rooms, doors=doors, entrance=len(doors) - 1), rng.uniform(-tilt_deg, tilt_deg))
    for r in p["rooms"]:
        r["polygon"] = [[x + rng.uniform(-jitter, jitter), z + rng.uniform(-jitter, jitter)] for x, z in r["polygon"]]
    return p


@pytest.mark.parametrize("wall, tilt", [(0.3, 0), (0.3, 5), (0.45, 5)])
def test_houses_with_thick_walls_and_doors_everywhere_rectify_to_a_fixed_point(wall, tilt):
    rng = random.Random(f"vesper-plan-thick-{wall}-{tilt}")
    for n in range(30):
        p = thick_house(rng, wall, tilt)
        out = rectify(p)
        assert rectify(out) == out, n
        assert check_house(out) == [], n


def overlap_strip(depth, long=6.0, l_shaped=False):
    """Living room 8 x 8 and a room whose outline reaches `depth` into it along one wall (or along two, as an L)."""
    other = [[8 - depth, 0], [8 + 3, 0], [8 + 3, long], [8 - depth, long]]
    if l_shaped:
        other = [[8 - depth, 0], [11, 0], [11, long], [2, long], [2, long - depth], [8 - depth, long - depth]]
    return metres([room("living", "living", rect(0, 0, 8, 8)), room("study", "other", other)])


@pytest.mark.parametrize("depth", [0.2, 0.45])
def test_a_thin_strip_of_overlap_is_trimmed(depth):
    out = rectify(overlap_strip(depth))
    assert polygon_area([tuple(q) for q in by_id(out, "living")["polygon"]]) == pytest.approx(64 - depth * 6)


def test_an_l_shaped_strip_of_overlap_is_trimmed():
    out = rectify(overlap_strip(0.2, l_shaped=True))
    assert polygon_area([tuple(q) for q in by_id(out, "living")["polygon"]]) < 64


@pytest.mark.parametrize("w, h, depth", [(0.55, 6, "0.55"), (0.9, 0.9, "0.90"), (0.8, 1.2, "0.80"), (0.7, 1.5, "0.70"),
                                         (0.6, 3, "0.60")])
def test_a_thicker_overlap_than_half_a_metre_is_refused_with_its_depth(w, h, depth):
    p = metres([room("living", "living", rect(0, 0, 8, 8)), room("study", "other", rect(8 - w, 1, 8 - w + 3, 1 + h))])
    with pytest.raises(PlanError, match=rf"'study'.*'living' by {depth} m: redraw them as separate rooms"):
        rectify(p)


@pytest.mark.parametrize("bad", [10**400, -(10**400), 1e308, 1e12], ids=["huge-int", "huge-negative", "1e308", "1e12"])
def test_numbers_beyond_any_plan_are_a_plan_error_not_an_overflow(bad):
    p = house()
    p["rooms"][0]["polygon"][1] = [bad, 0]
    with pytest.raises(PlanError, match="living"):
        load_plan(p)
    q = house()
    q["doors"][0]["width"] = bad
    with pytest.raises(PlanError, match="door 0"):
        load_plan(q)
    s = two_rooms(scale={"m_per_px": bad})
    with pytest.raises(PlanError, match="scale"):
        scale_plan(s)
    with pytest.raises(PlanError, match="area"):
        scale_plan(two_rooms(), area_m2=bad)


def test_a_scale_that_makes_the_plan_absurdly_large_is_refused():
    with pytest.raises(PlanError, match="scale"):
        scale_plan(two_rooms(scale={"m_per_px": 1e8}))


def four_rooms(doors_between=True):
    """Two columns of two rooms: a0/b0 drawn 0.60 m apart, a1/b1 drawn 0.35 m apart, the tops of a0 and a1 (and the
    bottoms of b0 and b1) within 15 cm of one line."""
    rooms = [room("a0", "other", rect(0, 0, 4, 3.7)), room("b0", "other", rect(0, 4.3, 4, 8)),
             room("a1", "other", rect(4, 0, 8, 3.825)), room("b1", "other", rect(4, 4.175, 8, 8))]
    doors = [{"at": [4, 2], "width": 0.8, "rooms": ["a0", "a1"]}, {"at": [4, 6], "width": 0.8, "rooms": ["b0", "b1"]},
             {"at": [0, 2], "width": 0.9, "rooms": ["a0", "outside"]}]
    if doors_between:
        doors += [{"at": [2, 4], "width": 0.8, "rooms": ["a0", "b0"]},
                  {"at": [6, 4], "width": 0.8, "rooms": ["a1", "b1"]}]
    return metres(rooms, doors=doors, entrance=2)


def test_wall_gaps_do_not_chain_across_passes_into_a_merge_wider_than_the_door_reach():
    with pytest.raises(PlanError, match=r"door \d between 'a[01]' and 'b[01]': the rooms do not touch"):
        rectify(four_rooms())
    out = rectify(four_rooms(doors_between=False))  # no door between the rows: nothing pulls them together
    assert by_id(out, "a0")["polygon"][2][1] == by_id(out, "a1")["polygon"][2][1]  # the rows' tops line up...
    for i in "01":  # ...and the rows stay apart: nothing closed a gap, nothing joined a top to a bottom
        assert by_id(out, f"b{i}")["polygon"][0][1] - by_id(out, f"a{i}")["polygon"][2][1] > 0.4


def max_shift(before, after):
    """The furthest any corner of one polygon is from the outline of the other, both ways."""
    def far(pts, ring):
        return max(min(math.dist(p, plan_module._closest(a, b, p))
                       for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) for p in pts)
    a, b = [tuple(p) for p in before], [tuple(p) for p in after]
    return max(far(a, b), far(b, a))


def test_walls_never_move_more_than_half_a_metre_whatever_the_gaps():
    rng = random.Random("vesper-plan-creep")
    refused = settled = 0
    for n in range(60):
        cols = [0.0]
        for _ in range(rng.choice([3, 4, 5])):
            cols.append(cols[-1] + rng.uniform(2.5, 4))
        rooms, doors = [], []
        for i in range(len(cols) - 1):
            bottom, top = 4 - rng.uniform(0, 0.5), 4 + rng.uniform(0, 0.5)  # gaps of up to 1 m
            x0 = cols[i] + (rng.uniform(0, 0.1) if i else 0)
            rooms += [room(f"a{i}", "other", rect(x0, 0, cols[i + 1], bottom)),
                      room(f"b{i}", "other", rect(x0, top, cols[i + 1], 8))]
            doors.append({"at": [(cols[i] + cols[i + 1]) / 2, 4], "width": 0.8, "rooms": [f"a{i}", f"b{i}"]})
            if i:
                doors += [{"at": [cols[i], 2], "width": 0.8, "rooms": [f"a{i - 1}", f"a{i}"]},
                          {"at": [cols[i], 6], "width": 0.8, "rooms": [f"b{i - 1}", f"b{i}"]}]
        doors.append({"at": [0, 2], "width": 0.8, "rooms": ["a0", "outside"]})  # the entrance: a door no room
        p = metres(rooms, doors=doors, entrance=len(doors) - 1)  # needs is left out, one the b rooms need refused
        try:
            out = rectify(p)
        except PlanError:
            refused += 1
            continue
        settled += 1
        for before, after in zip(p["rooms"], out["rooms"], strict=True):
            assert max_shift(before["polygon"], after["polygon"]) <= 0.5 + 1e-6, (n, before["id"])
    assert refused and settled  # the generator reaches both outcomes


def test_a_plan_that_does_not_settle_in_the_allowed_passes_is_refused(monkeypatch):
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(5.2, 0, 8.5, 4))],
               doors=[{"at": [5.1, 2], "width": 0.9, "rooms": ["living", "kitchen"]}])
    assert rectify(p)  # settles with the passes allowed
    monkeypatch.setattr(plan_module, "MAX_PASSES", 1)
    with pytest.raises(PlanError, match="does not settle"):
        rectify(p)
    clean = metres([room("living", "living", rect(0, 0, 5, 4))])
    assert rectify(clean) == clean  # nothing to settle: one pass confirms it


# --- real plans (task A6): an outdoor room, and what the image cannot support left out or narrowed, with a warning --
# Each plan below is a small hand-made copy of a way CubiCasa5K plans were refused (docs/experiments/
# results-plan-import.md). VESPER never invents a layout: it leaves out or narrows what cannot be built, and says so.

def test_a_balcony_is_an_outdoor_room_type():
    assert "balcony" in plan_module.ROOM_TYPES and plan_module.OUTDOOR_TYPES == ("balcony",)
    load_plan(metres([room("b", "balcony", rect(0, 0, 2, 1))]))


def with_balcony():
    """A living room and, 20 cm north of it (a thin wall drawn as a gap), a balcony; the window between them."""
    return metres([room("living", "living", rect(0, 0, 5, 4)), room("bal", "balcony", rect(0, 4.2, 5, 5.5))],
                  doors=[{"at": [0, 2], "width": 0.9, "rooms": ["living", "outside"]},
                         {"at": [4, 4.1], "width": 0.9, "rooms": ["living", "bal"]}],
                  windows=[{"at": [2.5, 4.1], "width": 1.2, "room": "living"}], entrance=0)


def test_a_window_onto_a_balcony_is_kept_on_the_wall_they_share():
    warnings = []
    out = rectify(with_balcony(), warnings)
    assert min(pt[1] for pt in by_id(out, "bal")["polygon"]) == pytest.approx(4.1)  # the gap closed: a shared wall
    assert out["windows"] == [{"at": [pytest.approx(2.5), pytest.approx(4.1)], "width": 1.2, "room": "living"}]
    assert warnings == []


def test_a_window_on_no_outside_wall_is_left_out_with_a_warning_not_refused():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("store", "other", rect(0, 4.2, 5, 5.5))],
               doors=[{"at": [4, 4.1], "width": 0.9, "rooms": ["living", "store"]}],
               windows=[{"at": [2.5, 0], "width": 1.2, "room": "living"},
                        {"at": [2.5, 4.1], "width": 1.2, "room": "living"}])  # onto the store room: indoors
    warnings = []
    out = rectify(p, warnings)
    assert out["windows"] == [{"at": [2.5, 0], "width": 1.2, "room": "living"}]
    assert warnings == ["window 1 of 'living' is not on an outside wall: left out"]
    middle = copy.deepcopy(p)
    middle["windows"] = [{"at": [2.5, 2], "width": 1.2, "room": "living"}]  # in the middle of the room
    assert rectify(middle)["windows"] == []  # no list given: no warning kept, the window still left out


def test_a_window_of_a_balcony_is_left_out_with_a_warning():
    p = with_balcony()
    p["windows"].append({"at": [2.5, 5.5], "width": 1.0, "room": "bal"})
    warnings = []
    out = rectify(p, warnings)
    assert [w["room"] for w in out["windows"]] == ["living"]
    assert warnings == ["window 1 of 'bal' is in an outdoor room: left out"]


def test_a_door_wider_than_its_shared_wall_is_narrowed_to_fit_with_a_warning():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("hall", "corridor", rect(5, 0, 5.8, 1.2))],
               doors=[{"at": [5, 0.6], "width": 1.5, "rooms": ["living", "hall"]}])
    warnings = []
    out = rectify(p, warnings)
    assert out["doors"][0] == {"at": [5, pytest.approx(0.6)], "width": pytest.approx(1.1), "rooms": ["living", "hall"]}
    assert warnings == ["door 0 between 'living' and 'hall' (1.50 m) is wider than their wall (1.20 m): narrowed to "
                        "1.10 m"]


def test_a_door_on_a_wall_shorter_than_60_cm_is_still_refused():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("hall", "corridor", rect(5, 0, 6, 0.5))],
               doors=[{"at": [5, 0.25], "width": 0.9, "rooms": ["living", "hall"]}])
    with pytest.raises(PlanError, match=r"door 0 between 'living' and 'hall'.*wider than the wall.*0.50 m"):
        rectify(p)


def test_a_window_wider_than_its_wall_is_narrowed_and_one_on_a_tiny_stretch_left_out():
    p = metres([room("living", "living", rect(0, 0, 5, 4))], windows=[{"at": [2.5, 0], "width": 6, "room": "living"}])
    warnings = []
    assert rectify(p, warnings)["windows"] == [{"at": [2.5, 0], "width": pytest.approx(4.9), "room": "living"}]
    assert warnings == ["window 0 of 'living' (6.00 m) is wider than its wall (5.00 m): narrowed to 4.90 m"]
    tiny = metres([room("living", "living", rect(0, 0, 5, 4)), room("k", "kitchen", rect(0, 4, 4.5, 7)),
                   room("b", "bedroom", rect(5, 0, 8, 7))],  # the outside wall by the window: 0.5 m of it
                  windows=[{"at": [4.75, 4], "width": 1.0, "room": "living"}])
    warnings = []
    assert rectify(tiny, warnings)["windows"] == []
    assert warnings == ["window 0 of 'living' is not on an outside wall: left out"]


def test_a_door_between_rooms_that_do_not_touch_is_left_out_when_the_house_stays_connected():
    p = house()
    p["doors"].insert(0, {"at": [5, 4.6], "width": 0.9, "rooms": ["kitchen", "bed"]})  # across the corridor
    p["entrance"] = 1
    warnings = []
    out = rectify(p, warnings)
    assert [d["rooms"] for d in out["doors"]] == [d["rooms"] for d in house()["doors"]]
    assert out["entrance"] == 0  # still the front door
    assert warnings == ["door 0 between 'kitchen' and 'bed': the rooms do not touch (they have no wall in common): "
                        "left out (no room needs it to be reached from the entrance)"]
    assert check_house(out) == []


def test_a_door_between_rooms_that_do_not_touch_is_refused_when_a_room_needs_it():
    p = house()
    p["doors"][3] = {"at": [2, 5.2], "width": 0.9, "rooms": ["kitchen", "bed"]}  # the bedroom's only door
    with pytest.raises(PlanError, match=r"door 3 between 'kitchen' and 'bed': the rooms do not touch.*'bed'"):
        rectify(p)


def t_junction(left=1.98, right=2.43):
    """A kitchen along the top; under it a hall (x 0 to 2) and a living room (x 2 to 5); a door from the kitchen
    into each, the two meeting end to end where the hall's and the living room's wall comes up (plan 2207)."""
    return metres([room("kitchen", "kitchen", rect(0, 0, 5, 2.5)), room("hall", "hall", rect(0, 2.5, 2, 4.5)),
                   room("living", "living", rect(2, 2.5, 5, 7))],
                  doors=[{"at": [1.0, 2.5], "width": left, "rooms": ["kitchen", "hall"]},
                         {"at": [2.01 + right / 2, 2.5], "width": right, "rooms": ["kitchen", "living"]},
                         {"at": [5, 5], "width": 0.9, "rooms": ["living", "outside"]}], entrance=2)


def test_two_doors_meeting_end_to_end_on_one_wall_are_narrowed_to_leave_wall_between_them():
    warnings = []
    out = rectify(t_junction(), warnings)
    a, b = out["doors"][:2]
    hall_end, living_start = a["at"][0] + a["width"] / 2, b["at"][0] - b["width"] / 2
    assert hall_end == pytest.approx(1.95) and living_start == pytest.approx(2.05)
    assert a["at"][0] - a["width"] / 2 == pytest.approx(0.01) and b["at"][0] + b["width"] / 2 == pytest.approx(4.44)
    assert warnings == ["doors 0 and 1 meet end to end on one wall: narrowed to 1.94 m and 2.39 m to leave 0.10 m of "
                        "wall between them"]
    assert rectify(out, []) == out  # stable


def test_doors_apart_on_a_wall_or_of_the_same_two_rooms_are_left_as_they_are():
    warnings = []
    out = rectify(t_junction(left=0.9, right=0.9), warnings)
    assert warnings == [] and [d["width"] for d in out["doors"]] == [0.9, 0.9, 0.9]
    p = t_junction(left=0.9, right=0.9)
    p["doors"][1] = {"at": [1.45, 2.5], "width": 0.9, "rooms": ["kitchen", "hall"]}  # 5 cm from the first
    p["doors"].append({"at": [3, 2.5], "width": 0.9, "rooms": ["kitchen", "living"]})
    out = rectify(p, warnings)
    assert warnings == [] and [d["at"] for d in out["doors"][:2]] == [[1.0, 2.5], [1.45, 2.5]]


def test_rectify_without_a_warnings_list_still_leaves_out():
    p = house()
    p["doors"].append({"at": [5, 4.6], "width": 0.9, "rooms": ["kitchen", "bed"]})
    assert len(rectify(p)["doors"]) == 4


def test_a_balcony_s_area_is_not_judged_by_an_indoor_room_s_range():
    def with_bal(w, d):
        return metres([room("living", "living", rect(0, 0, 5, 4)), room("bal", "balcony", rect(0, 4, w, 4 + d))],
                      doors=[{"at": [0, 2], "width": 0.9, "rooms": ["living", "outside"]},
                             {"at": [0.5, 4], "width": 0.8, "rooms": ["living", "bal"]}], entrance=0)
    assert check_house(with_bal(1.2, 1.0)) == []  # 1.2 m²: a small balcony
    assert check_house(with_bal(5, 14)) == []  # 70 m²: a large terrace
