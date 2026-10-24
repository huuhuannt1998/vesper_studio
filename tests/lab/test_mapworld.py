import pytest

from tests.lab.maps import house, two_rooms
from vesper.lab.mapworld import SPEED_M_S, MapWorld, WorldError
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
