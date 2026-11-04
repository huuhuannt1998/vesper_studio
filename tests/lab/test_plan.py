"""The floor-plan file (`vesper-plan/1`): validation, scaling, rectification and the house checks (floor-plan import,
task A1). Pure geometry: no image, no Claude, no files."""
import copy
import math

import pytest

from vesper.lab.geometry import polygon_area
from vesper.lab.plan import PlanError, check_house, load_plan, rectify, scale_plan


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
    with pytest.raises(PlanError, match=r"'living'.*'bathroom'"):
        rectify(inside)
    split = metres([room("living", "living", rect(0, 0, 8, 6)), room("corridor", "corridor", rect(3, -1, 4, 7))])
    with pytest.raises(PlanError, match=r"'living'.*'corridor'"):
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


def test_a_door_between_rooms_that_do_not_touch_is_rejected_naming_both():
    p = metres([room("living", "living", rect(0, 0, 5, 4)), room("kitchen", "kitchen", rect(7, 0, 10.5, 4))],
               doors=[{"at": [5, 2], "width": 0.9, "rooms": ["living", "kitchen"]}])
    with pytest.raises(PlanError, match=r"'living'.*'kitchen'.*do not touch"):
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
    far["windows"][0]["at"] = [2.5, 2]
    with pytest.raises(PlanError, match=r"window 0.*'living'"):
        rectify(far)


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
