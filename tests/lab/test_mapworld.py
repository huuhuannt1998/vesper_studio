import math

import pytest

from tests.lab.maps import grid_house, house, two_rooms
from vesper.lab.mapworld import SPEED_M_S, STEP_S, MapWorld, WorldError
from vesper.twins.clock import LogicalClock


def world(m=None, doors=()):
    w = MapWorld(m or two_rooms(), LogicalClock())
    events = []
    w.on_event(events.append)
    for d in doors:
        w.add_door(d, open=True)
    return w, events


def test_an_actor_spawns_in_a_room_and_cannot_spawn_in_a_wall():
    w, _ = world()
    assert w.spawn("p", "female_0", (1.0, 1.0)).room == "kitchen"
    with pytest.raises(WorldError, match="not on the walkable floor"):
        w.spawn("q", "male_0", (4.0, 0.5))  # inside the wall


def test_steps_move_within_a_room_and_walls_stop_them():
    w, _ = world()
    w.spawn("p", "female_0", (3.5, 0.5))
    assert w.move("p", 0.3, 0.0).x == pytest.approx(3.8)
    blocked = w.move("p", 0.4, 0.0)  # into the wall at z=0.5, where there is no opening
    assert blocked.x == pytest.approx(3.8) and not w.last_move_ok


def test_a_diagonal_step_into_a_wall_slides_along_it():
    """Holding two keys against a wall walks along the wall, as in a game, instead of freezing (final review)."""
    w, _ = world()
    w.spawn("p", "female_0", (3.8, 0.5))
    p = w.move("p", 0.1768, 0.1768)  # the x part runs into the wall at x = 3.9
    assert w.last_move_ok and (p.x, p.z) == pytest.approx((3.8, 0.6768)) and p.yaw == pytest.approx(90.0)
    stuck = w.move("p", 0.4, 0.0)  # straight into the wall: nothing to slide along
    assert not w.last_move_ok and (stuck.x, stuck.z) == pytest.approx((3.8, 0.6768))


def test_walking_through_a_doorway_changes_room():
    w, events = world()
    w.spawn("p", "female_0", (3.6, 2.0))
    for _ in range(4):
        w.move("p", 0.25, 0.0)
    assert w.pose("p").room == "living"
    assert {"event": "room", "actor": "p", "room": "living", "previous": "kitchen"} in events


def test_a_closed_door_blocks_steps_and_paths_until_it_opens():
    w, events = world(doors=["kitchen-living"])
    w.spawn("p", "female_0", (3.6, 2.0))
    w.set_door("kitchen-living", open=False)
    assert {"event": "door", "id": "kitchen-living", "open": False} in events
    for _ in range(4):
        w.move("p", 0.25, 0.0)
    assert w.pose("p").room == "kitchen"
    assert w.walk_to("p", (6.0, 2.0)) is None  # no other way round
    w.set_door("kitchen-living", open=True)
    assert w.walk_to("p", (6.0, 2.0)) is not None


def test_only_registered_doors_open_and_close():
    w, _ = world()
    with pytest.raises(WorldError, match="no door on doorway 'kitchen-living'"):
        w.set_door("kitchen-living", open=False)
    with pytest.raises(WorldError, match="unknown doorway"):
        w.add_door("nowhere")


def test_walk_to_follows_doorways_at_walking_speed_and_arrives():
    w, events = world(house())
    w.spawn("p", "female_0", (5.0, 1.0))  # kitchen
    path = w.walk_to("p", (5.0, 5.0))  # bedroom: the shared wall is solid, so through the hall
    assert path[0] == pytest.approx((5.0, 1.0)) and path[-1] == pytest.approx((5.0, 5.0), abs=0.1)
    assert any(x < 3.0 for x, _ in path)
    length = sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(path, path[1:]))
    w.clock.advance(length / SPEED_M_S - 1.0)
    assert w.pose("p").room != "bedroom" or not any(e["event"] == "arrived" for e in events)
    w.clock.advance(2.0)
    assert w.pose("p").room == "bedroom" and {"event": "arrived", "actor": "p"} in events
    rooms = [e["room"] for e in events if e["event"] == "room"]
    assert rooms == ["hall", "bedroom"]


def test_a_new_walk_or_a_step_replaces_the_current_walk():
    w, events = world(house())
    w.spawn("p", "female_0", (5.0, 1.0))
    w.walk_to("p", (5.0, 5.0))
    w.clock.advance(1.0)
    w.stop("p")
    here = w.pose("p")
    w.clock.advance(30.0)
    assert w.pose("p") == here and not any(e["event"] == "arrived" for e in events)


def test_walks_are_deterministic():
    def poses():
        w, events = world(house())
        w.spawn("p", "female_0", (5.0, 1.0))
        w.walk_to("p", (1.0, 5.0))
        w.clock.advance(60.0)
        return [(e["x"], e["z"]) for e in events if e["event"] == "pose"]
    assert poses() == poses() and len(poses()) > 10


def test_a_door_cannot_close_on_someone_in_its_doorway():
    w = MapWorld(two_rooms(), LogicalClock())
    w.add_door("kitchen-living", True)
    w.spawn("p", "female_0", (4.0, 2.0))  # in the opening
    with pytest.raises(WorldError, match="p stands in the kitchen-living doorway"):
        w.set_door("kitchen-living", False)
    assert w.doors["kitchen-living"] is True
    w.move("p", 0.5, 0.0)
    assert w.set_door("kitchen-living", False) and w.doors["kitchen-living"] is False


def test_a_planned_walk_is_never_cut_off_at_the_corners_of_its_own_path():
    clock = LogicalClock()
    w = MapWorld(house(), clock)
    w.add_door("bedroom-hall", True)
    w.spawn("p", "female_0", (0.4, 3.0))
    events = []
    w.on_event(lambda e: events.append(e["event"]) if e["event"] in ("arrived", "blocked") else None)
    assert w.walk_to("p", (5.0, 4.4)) is not None
    clock.advance(10.0)
    assert events == ["arrived"] and w.pose("p").room == "bedroom"


def test_a_step_cannot_squeeze_between_two_wall_cells_that_meet_at_a_corner():
    from tests.lab.maps import grid_from
    from vesper.lab.worldmap import Room, WorldMap
    m = WorldMap("test/diagonal", [Room("a", "a", ((0, 0), (2, 0), (2, 2), (0, 2)))],
                 grid_from(2, 2, lambda x, z: int(x * 10) != int(z * 10)))  # a one-cell staircase wall
    w = MapWorld(m, LogicalClock())
    w.spawn("p", "female_0", (0.65, 0.55))  # cell (6, 5), below the wall
    w.move("p", -0.1, 0.1)  # through the corner shared by wall cells (5, 5) and (6, 6)
    assert not w.last_move_ok and (w.pose("p").x, w.pose("p").z) == (0.65, 0.55)


def test_a_walk_to_a_point_that_is_not_a_finite_place_has_no_path():
    w = MapWorld(house(), LogicalClock())
    w.spawn("p", "female_0", (1.5, 3.0))
    for target in ((1e308, -1e308), (float("nan"), 3.0), (float("inf"), 0.0)):
        assert w.walk_to("p", target) is None
    assert w.map.grid.cell_of(1e308, 0.0) is None


def test_late_callbacks_do_not_slow_a_walk_down():
    class LateClock(LogicalClock):  # a busy machine: every callback fires 0.1 s late
        def call_at(self, when, fn):
            super().call_at(when + 0.1, fn)
    clock = LateClock()
    w = MapWorld(house(), clock)
    w.add_door("bedroom-hall", True)
    w.spawn("p", "female_0", (0.4, 3.0))
    arrived = []
    w.on_event(lambda e: arrived.append(clock.now()) if e["event"] == "arrived" else None)
    path = w.walk_to("p", (5.0, 4.4))
    planned = sum(math.dist(a, b) for a, b in zip(path, path[1:])) / SPEED_M_S
    clock.advance(20.0)
    assert arrived and arrived[0] <= planned + STEP_S + 0.1 + 1e-6


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def test_a_walk_in_a_straight_line_needs_no_search():
    w, _ = world(house())
    w.spawn("p", "female_0", (4.0, 1.0))
    assert w.walk_to("p", (6.5, 2.5)) == [(4.0, 1.0), (6.5, 2.5)]
    assert w.plan_work == 0


def test_a_walk_across_a_home_sized_floor_searches_only_the_rooms_on_its_way():
    w, _ = world(grid_house())  # 36 x 24 m, 24 rooms: a whole-floor search took over a second here
    w.spawn("p", "female_0", (1.0, 1.0))
    path = w.walk_to("p", (35.0, 23.0))
    assert path is not None and all(w._segment_free(a, b) for a, b in zip(path, path[1:]))
    walkable = sum(w.map.grid.walkable)
    assert w.plan_work < walkable / 10, (w.plan_work, walkable)
    whole = w._plan_grid((1.0, 1.0), (35.0, 23.0))  # the whole-floor search: the shortest path on the grid
    assert path_length(path) <= 1.1 * path_length(whole)


def test_finding_the_closed_doors_on_a_way_searches_only_the_rooms_on_it():
    """A resident whose walk a closed door blocks asks which doors to open (W3a); on a home-sized floor that
    must not fall back to a whole-floor search either (final review, W3b)."""
    m = grid_house()
    w, _ = world(m, doors=[d.id for d in m.doorways])
    w.set_door("r00-r10", False)
    w.set_door("r00-r01", False)  # r00 is shut
    assert w.doors_on_route((1.0, 1.0), (35.0, 23.0)) in ([("r00-r10", "r00")], [("r00-r01", "r00")])
    assert w.plan_work < sum(w.map.grid.walkable) / 10, w.plan_work
    w.set_door("r00-r10", True)
    w.set_door("r00-r01", True)
    assert w.doors_on_route((1.0, 1.0), (35.0, 23.0)) == []  # every door open: nothing to open


def test_a_closed_door_on_the_way_is_walked_around_and_a_shut_room_is_out_of_reach():
    m = grid_house(cols=3, rows=2)
    doors = [d.id for d in m.doorways]
    w, _ = world(m, doors=doors)
    w.spawn("p", "female_0", (1.0, 1.0))  # in r00
    w.set_door("r00-r10", False)
    path = w.walk_to("p", (9.0, 1.0))  # r10: round through r01 and r11
    assert path is not None and max(z for _, z in path) > 6.0
    w.set_door("r00-r01", False)
    assert w.walk_to("p", (9.0, 1.0)) is None  # every way out of r00 is shut


def test_repeated_walks_reuse_what_the_first_one_searched():
    w, _ = world(grid_house())
    w.spawn("p", "female_0", (1.0, 1.0))
    w.walk_to("p", (35.0, 23.0))
    first = w.plan_work
    w.stop("p")
    w.walk_to("p", (35.0, 23.0))
    assert 0 < w.plan_work < first  # only the legs from the start and to the target are searched again
    assert w._legs  # the legs between doorways are kept
