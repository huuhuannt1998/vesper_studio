import json

import pytest

from tests.lab.maps import house, rect, two_rooms
from vesper.lab.geometry import centroid, in_cone, point_in_polygon
from vesper.lab.worldmap import MapError, WorldMap


def test_polygon_geometry():
    square = rect(0, 0, 4, 4)
    assert point_in_polygon((1, 1), square) and not point_in_polygon((5, 1), square)
    assert centroid(square) == pytest.approx((2.0, 2.0))
    assert in_cone((0, 0), 0.0, (3, 0.5), 7.0, 110.0)  # facing +x, a point slightly off-axis
    assert not in_cone((0, 0), 0.0, (-3, 0), 7.0, 110.0)  # behind
    assert not in_cone((0, 0), 0.0, (8, 0), 7.0, 110.0)  # out of range
    assert in_cone((0, 0), 90.0, (0, 3), 7.0, 110.0)  # facing +z


def test_rooms_are_found_by_position():
    m = two_rooms()
    assert m.room_at(1, 1).id == "kitchen" and m.room_at(6, 2).id == "living" and m.room_at(9, 2) is None
    assert m.room("living").label == "living room"


def test_a_wall_gap_between_two_rooms_is_one_doorway():
    (d,) = two_rooms().doorways
    assert d.rooms == ("kitchen", "living") and d.id == "kitchen-living"
    assert d.center == pytest.approx((4.0, 2.0), abs=0.1) and d.width == pytest.approx(1.0, abs=0.15)


def test_a_solid_wall_has_no_doorway():
    assert two_rooms(gap=None).doorways == []


def test_doorways_through_wall_thickness_outside_every_room_are_found():
    doors = {d.id: d for d in house().doorways}
    assert set(doors) == {"hall-kitchen", "bedroom-hall", "hall-porch"}  # never kitchen-bedroom
    assert doors["hall-porch"].center == pytest.approx((0.0, 3.0), abs=0.15)
    assert doors["hall-porch"].outdoor and not doors["hall-kitchen"].outdoor


def test_the_map_file_round_trips_and_derives_the_same_doorways(tmp_path):
    m = house()
    path = tmp_path / "map.json"
    m.save(path)
    loaded = WorldMap.load(path)
    assert loaded.rooms == m.rooms and loaded.grid.walkable == m.grid.walkable
    assert [d.id for d in loaded.doorways] == [d.id for d in m.doorways]
    assert json.loads(path.read_text())["format"] == "vesper-map/1"


def test_a_file_that_is_not_a_vesper_map_is_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(MapError, match="not a VESPER map"):
        WorldMap.load(bad)
    with pytest.raises(MapError, match="unknown room 'garage'.*kitchen"):
        two_rooms().room("garage")


def test_rooms_that_stop_short_of_the_walls_are_refused_rather_than_guessed():
    from vesper.lab.worldmap import Room
    m = house()

    def inset(r):
        xs, zs = [p[0] for p in r.polygon], [p[1] for p in r.polygon]
        return Room(r.id, r.label, rect(min(xs) + 0.1, min(zs) + 0.1, max(xs) - 0.1, max(zs) - 0.1), r.outdoor)
    with pytest.raises(MapError, match="reach the walls") as refused:
        WorldMap(m.scene, [inset(r) for r in m.rooms], m.grid)
    assert all(name in str(refused.value) for name in ("bedroom", "hall", "kitchen"))


@pytest.mark.parametrize("change, message", [
    (lambda d: d.pop("rooms"), r"rooms: missing"),
    (lambda d: d.pop("scene"), r"scene: missing"),
    (lambda d: d["grid"].update(cell=0), r"grid\.cell: 0 is not a positive number"),
    (lambda d: d["grid"].update(width="ninety"), r"grid\.width: 'ninety' is not a positive whole number"),
    (lambda d: d["grid"].update(walkable="AAAA"), r"grid\.walkable: holds 24 cells, the grid has"),
    (lambda d: d["grid"].update(walkable="not base64!"), r"grid\.walkable: not base64"),
    (lambda d: d["rooms"][0].update(polygon=[[0, 0]]), r"rooms\[0\]\.polygon: needs at least 3 points"),
    (lambda d: d["rooms"][1].pop("id"), r"rooms\[1\]\.id: missing"),
])
def test_a_malformed_map_file_names_the_field_instead_of_crashing(tmp_path, change, message):
    house().save(tmp_path / "map.json")
    d = json.loads((tmp_path / "map.json").read_text())
    change(d)
    (tmp_path / "map.json").write_text(json.dumps(d))
    with pytest.raises(MapError, match=message):
        WorldMap.load(tmp_path / "map.json")
