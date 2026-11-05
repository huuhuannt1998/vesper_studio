"""Any room or house mesh as a `vesper-plan/1` (scan import, task B3): the up axis, the scale, the slices, the rooms,
doors and windows. Every mesh is built here with trimesh; nothing is read from the scan test set."""
import math
import sys

import pytest

trimesh = pytest.importorskip("trimesh")
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from vesper.lab.geometry import centroid, polygon_area  # noqa: E402
from vesper.lab.plan import check_house, load_plan, rectify  # noqa: E402
from vesper.lab.scan_mesh import ScanError, read_mesh  # noqa: E402

T = 0.12  # wall thickness
H = 2.6  # floor to ceiling


def box(x0, x1, y0, y1, z0, z1):
    return trimesh.creation.box(bounds=[[x0, y0, z0], [x1, y1, z1]])


def wall(axis, at, a, b, openings=(), h=H):
    """A wall along x (axis "x": from x=a to x=b, its centre line at z=at) or along z, with openings
    (start, end, bottom, top): full-height pieces between them, the sill below and the lintel above each."""
    pieces = []
    cuts = sorted(openings)
    edges = [a] + [v for o in cuts for v in o[:2]] + [b]
    for s, e in zip(edges[::2], edges[1::2], strict=True):
        if e > s:
            pieces.append((s, e, 0.0, h))
    for s, e, bottom, top in cuts:
        if bottom > 0:
            pieces.append((s, e, 0.0, bottom))
        if top < h:
            pieces.append((s, e, top, h))
    out = []
    for s, e, y0, y1 in pieces:
        if axis == "x":
            out.append(box(s, e, y0, y1, at - T / 2, at + T / 2))
        else:
            out.append(box(at - T / 2, at + T / 2, y0, y1, s, e))
    return out


def house(ceiling=True, wall_h=H, doors=True, window=True, y=0.0, one_room=False):
    """Two rooms of 4 x 4 m (to the wall centre lines), x 0-4 and 4-8, z 0-4: a door between them (z 1.5-2.4), the
    entrance in the south wall of the east room (x 6.0-6.9) and a window in the north wall of the west room (x 1.5-2.7,
    sill 0.9, head 2.1). A floor slab under it and, unless `ceiling` is False, a ceiling slab over it."""
    h = wall_h
    parts = [box(-T / 2, 8 + T / 2, -0.1, 0.0, -T / 2, 4 + T / 2)]
    if ceiling:
        parts.append(box(-T / 2, 8 + T / 2, h, h + 0.1, -T / 2, 4 + T / 2))
    north = [(1.5, 2.7, 0.9, 2.1)] if window else []
    south = [(6.0, 6.9, 0.0, 2.1)] if doors else []
    inner = [(1.5, 2.4, 0.0, 2.1)] if doors else []
    parts += wall("x", 0.0, -T / 2, 8 + T / 2, north, h)
    parts += wall("x", 4.0, -T / 2, 8 + T / 2, south, h)
    parts += wall("z", 0.0, T / 2, 4 - T / 2, (), h)
    parts += wall("z", 8.0, T / 2, 4 - T / 2, (), h)
    if not one_room:
        parts += wall("z", 4.0, T / 2, 4 - T / 2, inner, h)
    mesh = trimesh.util.concatenate(parts)
    mesh.apply_translation([0, y, 0])
    return mesh


def save(mesh, path):
    mesh.export(str(path))
    return path


def rooms_by_x(plan):
    return sorted(plan["rooms"], key=lambda r: centroid([tuple(p) for p in r["polygon"]])[0])


def area(room):
    return polygon_area([tuple(p) for p in room["polygon"]])


def assert_two_room_house(plan):
    load_plan(plan)
    assert plan["units"] == "m"
    rooms = rooms_by_x(plan)
    assert len(rooms) == 2
    for r in rooms:
        assert r["type"] == "other"
        assert area(r) == pytest.approx(16.0, rel=0.05)
    assert [r["name"] for r in plan["rooms"]] == ["room 1", "room 2"]
    west, east = rooms[0]["id"], rooms[1]["id"]
    inner = [d for d in plan["doors"] if "outside" not in d["rooms"]]
    outer = [d for d in plan["doors"] if "outside" in d["rooms"]]
    assert len(inner) == 1 and sorted(inner[0]["rooms"]) == sorted([west, east])
    assert inner[0]["width"] == pytest.approx(0.9, abs=0.1)
    assert len(outer) == 1 and east in outer[0]["rooms"]
    assert outer[0]["width"] == pytest.approx(0.9, abs=0.1)
    assert plan["doors"][plan["entrance"]] is outer[0]
    assert len(plan["windows"]) == 1
    assert plan["windows"][0]["room"] == west
    assert plan["windows"][0]["width"] == pytest.approx(1.2, abs=0.15)
    assert check_house(rectify(plan)) == []
    return rooms


def test_two_room_house(tmp_path):
    plan, meta, warnings = read_mesh(save(house(), tmp_path / "house.glb"))
    rooms = assert_two_room_house(plan)
    assert meta["up"] == "y"
    assert meta["scale"] == pytest.approx(1.0)
    assert meta["floor_y"] == pytest.approx(0.0, abs=0.05)
    assert meta["ceiling_y"] == pytest.approx(H, abs=0.05)
    assert len(meta["transform"]) == 4 and all(len(row) == 4 for row in meta["transform"])
    # the door between the rooms is on their shared wall, the window on the west room's north wall
    inner = next(d for d in plan["doors"] if "outside" not in d["rooms"])
    west_box = rooms[0]["polygon"]
    xs = [p[0] for p in west_box]
    assert inner["at"][0] == pytest.approx(max(xs), abs=0.15)
    assert not any("scale" in w for w in warnings)


def test_transform_maps_the_model_into_the_plan(tmp_path):
    """The meta's transform takes a point of the file to the plan's frame: the window's centre lands on the plan's
    window, the floor at height 0."""
    plan, meta, _ = read_mesh(save(house(), tmp_path / "house.glb"))
    m = np.array(meta["transform"])
    window_centre = m @ np.array([2.1, 1.5, 0.0, 1.0])
    assert window_centre[1] == pytest.approx(1.5, abs=0.05)
    assert [window_centre[0], window_centre[2]] == pytest.approx(plan["windows"][0]["at"], abs=0.15)


def test_z_up_model_is_turned(tmp_path):
    """The same house turned so its up is +z (as Habitat's GLBs are, inside glTF): detected, turned, same plan."""
    y_up, _, _ = read_mesh(save(house(), tmp_path / "y.glb"))
    mesh = house()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))  # +y becomes +z
    z_up, meta, _ = read_mesh(save(mesh, tmp_path / "z.glb"))
    assert meta["up"] == "z"
    assert_two_room_house(z_up)
    for a, b in zip(rooms_by_x(y_up), rooms_by_x(z_up), strict=True):
        assert centroid([tuple(p) for p in a["polygon"]]) == pytest.approx(
            centroid([tuple(p) for p in b["polygon"]]), abs=0.1)


def test_upside_down_shell_is_turned(tmp_path):
    """A bare shell of closed slabs looks the same either way up; read both ways, the plan with the doors wins."""
    mesh = house()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))  # +y becomes -y
    plan, meta, warnings = read_mesh(save(mesh, tmp_path / "flipped.glb"))
    assert meta["up"] == "-y"
    assert any("which way is up" in w for w in warnings)
    assert_two_room_house(plan)


def test_no_door_to_the_outside_is_said(tmp_path):
    mesh = house()
    mesh = trimesh.util.concatenate([mesh, box(6.0, 6.9, 0.0, H, 4 - T / 2, 4 + T / 2)])  # the entrance walled up
    plan, _, warnings = read_mesh(save(mesh, tmp_path / "closed.glb"))
    assert plan["entrance"] is None
    assert len(plan["rooms"]) == 2 and len(plan["doors"]) == 1
    assert any("no entrance found" in w for w in warnings)


def test_small_scale_model_is_rescaled_with_a_warning(tmp_path):
    mesh = house(wall_h=2.5)  # the ceiling at 2.5 m, so the rescale is exactly 2.5
    mesh.apply_scale(1 / 2.5)
    plan, meta, warnings = read_mesh(save(mesh, tmp_path / "small.ply"))
    assert meta["scale"] == pytest.approx(2.5, rel=0.03)
    assert any("scale" in w and "--scale" in w for w in warnings)
    assert_two_room_house(plan)


def test_a_given_scale_is_used(tmp_path):
    mesh = house(wall_h=2.5)
    mesh.apply_scale(1 / 2.5)
    plan, meta, warnings = read_mesh(save(mesh, tmp_path / "small.glb"), scale=2.5)
    assert meta["scale"] == 2.5
    assert not any("rescaled" in w for w in warnings)
    assert_two_room_house(plan)


def test_cut_walls_without_ceiling_or_door_are_refused(tmp_path):
    """A dollhouse cut at 1 unit, no ceiling, no door: nothing tells the scale, so the import is refused."""
    mesh = house(ceiling=False, wall_h=1.0, doors=False, window=False)
    with pytest.raises(ScanError, match=r"the scale looks wrong.*--scale"):
        read_mesh(save(mesh, tmp_path / "doll.glb"))


def test_large_ground_plane_is_not_the_floor(tmp_path):
    """A 24 m ground plane around the house, 15 cm below its floor: the floor is the one under the walls."""
    ground = box(-8, 16, -0.4, -0.15, -10, 14)
    plan, meta, _ = read_mesh(save(trimesh.util.concatenate([house(), ground]), tmp_path / "garden.obj"))
    assert meta["floor_y"] == pytest.approx(0.0, abs=0.05)
    assert_two_room_house(plan)


def test_spray_outside_a_window_is_not_a_room(tmp_path):
    """LiDAR geometry sprayed 1.5-2.5 m outside the window: not a room, and the house is read as before."""
    rng = np.random.default_rng(0)
    n = 3000
    centres = np.column_stack([rng.uniform(1.0, 3.2, n), rng.uniform(0.0, 2.5, n), rng.uniform(-2.5, -1.5, n)])
    offsets = rng.uniform(-0.04, 0.04, (n, 3, 3))
    tris = centres[:, None, :] + offsets
    spray = trimesh.Trimesh(vertices=tris.reshape(-1, 3), faces=np.arange(3 * n).reshape(-1, 3), process=False)
    plan, _, _ = read_mesh(save(trimesh.util.concatenate([house(), spray]), tmp_path / "spray.glb"))
    assert_two_room_house(plan)


def test_two_storeys_read_the_lowest_with_a_warning(tmp_path):
    upper = house(y=H + 0.2, one_room=True, window=False)
    plan, meta, warnings = read_mesh(save(trimesh.util.concatenate([house(), upper]), tmp_path / "two.glb"))
    assert meta["floor_y"] == pytest.approx(0.0, abs=0.05)
    assert any("storeys" in w for w in warnings)
    assert_two_room_house(plan)


def test_without_trimesh_the_extra_is_named(tmp_path, monkeypatch):
    path = save(house(), tmp_path / "house.glb")
    monkeypatch.setitem(sys.modules, "trimesh", None)
    with pytest.raises(ScanError, match=r"vesper\[scan\]"):
        read_mesh(path)


def test_unknown_suffix_and_bad_scale(tmp_path):
    path = tmp_path / "house.stl"
    path.write_bytes(b"solid x\nendsolid x\n")
    with pytest.raises(ScanError, match="glb"):
        read_mesh(path)
    glb = save(house(), tmp_path / "house.glb")
    for bad in (0, -1.0, float("nan")):
        with pytest.raises(ScanError, match="scale"):
            read_mesh(glb, scale=bad)


def test_a_rescale_beyond_x5_is_refused_unless_a_scale_is_given(tmp_path):
    """A model in decimetres (the ceiling 0.25 units up) would be rescaled x10: refused, with the way out named."""
    mesh = house(wall_h=2.5)
    mesh.apply_scale(1 / 10)
    path = save(mesh, tmp_path / "tiny.glb")
    with pytest.raises(ScanError, match=r"the scale looks wrong: the model would be rescaled by 10.*give --scale"):
        read_mesh(path)
    plan, meta, _ = read_mesh(path, scale=10)
    assert meta["scale"] == 10
    assert_two_room_house(plan)


def test_several_rooms_and_no_door_between_them_are_refused(tmp_path):
    """Two rooms, each with its own door to the outside and none between them (a model whose inner doors were not
    found): refused, not built as two houses."""
    parts = [box(-T / 2, 8 + T / 2, -0.1, 0.0, -T / 2, 4 + T / 2),
             box(-T / 2, 8 + T / 2, H, H + 0.1, -T / 2, 4 + T / 2)]
    parts += wall("x", 0.0, -T / 2, 8 + T / 2, (), H)
    parts += wall("x", 4.0, -T / 2, 8 + T / 2, [(1.5, 2.4, 0.0, 2.1), (6.0, 6.9, 0.0, 2.1)], H)
    parts += wall("z", 0.0, T / 2, 4 - T / 2, (), H)
    parts += wall("z", 8.0, T / 2, 4 - T / 2, (), H)
    parts += wall("z", 4.0, T / 2, 4 - T / 2, (), H)
    path = save(trimesh.util.concatenate(parts), tmp_path / "apart.glb")
    with pytest.raises(ScanError, match="2 rooms and no door between any of them: the model's doors were not found"):
        read_mesh(path)
