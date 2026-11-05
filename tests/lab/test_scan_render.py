"""A mesh read by Claude from its top-down render (scan import, task B3b): the render's pixels and exact scale, and the
plan Claude answers in pixels brought back to metres in the model's frame. Every Claude here is a fake runner; every
mesh is task B3's hand-built two-room house."""
import json
import math

import pytest

trimesh = pytest.importorskip("trimesh")
np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from tests.lab.test_plan_read import Fake, envelope  # noqa: E402
from tests.lab.test_scan_mesh import box, house, save  # noqa: E402
from vesper.lab.geometry import point_in_polygon, polygon_area  # noqa: E402
from vesper.lab.plan import PlanError, check_house, load_plan, rectify  # noqa: E402
from vesper.lab.png import decode  # noqa: E402
from vesper.lab.scan_mesh import read_mesh  # noqa: E402
from vesper.lab.scan_render import COLOURS, read_mesh_with_claude, render_plan_view  # noqa: E402

WALL, FLOOR, FURNITURE, WINDOW = (COLOURS[k][:3] for k in ("wall", "floor", "furniture", "window"))


def furnished():
    """The two-room house with a table (0.75 m tall) in the west room, at x 1.0-2.0, z 2.5-3.3."""
    return trimesh.util.concatenate([house(), box(1.0, 2.0, 0.0, 0.75, 2.5, 3.3)])


def to_pixel(view, x, y, z):
    """Where a point of the file (house coordinates) is drawn in the render: (column, row)."""
    m = np.array(view["transform"])
    f = m @ np.array([x, y, z, 1.0])
    return (int(math.floor((f[0] - view["origin"][0]) / view["m_per_px"])),
            int(math.floor((f[2] - view["origin"][1]) / view["m_per_px"])))


@pytest.fixture
def rendered(tmp_path):
    out = tmp_path / "view.png"
    view = render_plan_view(save(furnished(), tmp_path / "house.glb"), out)
    return view, decode(out.read_bytes())


def colour(img, px):
    return img.pixel(*px)[:3]


def test_the_scale_is_exact_and_the_image_its_size(rendered):
    view, img = rendered
    assert view["m_per_px"] == 1 / 50
    assert [img.width, img.height] == view["size"]
    # the house is 8.12 x 4.12 m, drawn with a 0.5 m margin each side
    assert img.width == pytest.approx((8.12 + 1.0) * 50, abs=3)
    assert img.height == pytest.approx((4.12 + 1.0) * 50, abs=3)
    assert view["meta"]["up"] == "y" and view["meta"]["scale"] == pytest.approx(1.0)
    assert 1.2 <= view["cut_m"] <= 1.5


def test_wall_pixels_lie_where_the_walls_are(rendered):
    """Black along every wall's centre line (except at its openings), and black nowhere far from a wall."""
    view, img = rendered
    on_lines = []
    for t in np.linspace(0.1, 7.9, 79):  # the north (z 0) and south (z 4) walls
        if not 1.5 <= t <= 2.7:  # the window
            on_lines.append((t, 0.0))
        if not 6.0 <= t <= 6.9:  # the entrance
            on_lines.append((t, 4.0))
    for t in np.linspace(0.1, 3.9, 39):  # the west, east and middle walls
        on_lines += [(0.0, t), (8.0, t)]
        if not 1.5 <= t <= 2.4:  # the door between the rooms
            on_lines.append((4.0, t))
    black = [colour(img, to_pixel(view, x, 1.3, z)) == WALL for x, z in on_lines]
    assert sum(black) / len(black) > 0.97
    # every black pixel is within 0.15 m of a wall line
    m_inv = np.linalg.inv(np.array(view["transform"]))
    far = 0
    count = 0
    for v in range(0, img.height, 3):
        for u in range(0, img.width, 3):
            if img.pixel(u, v)[:3] != WALL:
                continue
            count += 1
            x = view["origin"][0] + (u + 0.5) * view["m_per_px"]
            z = view["origin"][1] + (v + 0.5) * view["m_per_px"]
            p = m_inv @ np.array([x, 1.3, z, 1.0])
            d = min(abs(p[2] - 0.0), abs(p[2] - 4.0), abs(p[0] - 0.0), abs(p[0] - 4.0), abs(p[0] - 8.0))
            far += d > 0.15
    assert count > 500 and far == 0


def test_doors_are_gaps_windows_blue_floor_light_furniture_grey(rendered):
    view, img = rendered
    assert colour(img, to_pixel(view, 4.0, 1.3, 1.95)) == FLOOR  # the door between the rooms: floor through it
    assert colour(img, to_pixel(view, 6.45, 1.3, 4.0)) == FLOOR  # the entrance
    assert colour(img, to_pixel(view, 2.1, 1.3, 0.0)) == WINDOW  # the window in the north wall
    assert colour(img, to_pixel(view, 6.0, 0.0, 2.0)) == FLOOR  # the east room's floor
    assert colour(img, to_pixel(view, 1.5, 0.75, 2.9)) == FURNITURE  # the table, under the cut
    assert colour(img, (2, 2)) == COLOURS["outside"][:3]


def test_a_z_up_model_is_drawn_the_same(tmp_path):
    mesh = furnished()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    view = render_plan_view(save(mesh, tmp_path / "z.glb"), tmp_path / "z.png")
    img = decode((tmp_path / "z.png").read_bytes())
    assert view["meta"]["up"] == "z"
    # house point (4.0, 1.3, 0.5) is (4.0, -0.5, 1.3) once +y became +z
    assert colour(img, to_pixel(view, 4.0, -0.5, 1.3)) == WALL
    assert colour(img, to_pixel(view, 2.1, 0.0, 1.3)) == WINDOW


def answer_in_pixels(view):
    """What Claude would answer for the render: the two rooms (to their walls' inner faces), the door between, the
    entrance and the window, in the image's pixels."""
    t = 0.06  # half a wall

    def px(x, z):
        u, v = to_pixel(view, x, 1.3, z)
        return [u, v]

    def rect(x0, z0, x1, z1):
        return [px(x0, z0), px(x1, z0), px(x1, z1), px(x0, z1)]
    ppm = 1 / view["m_per_px"]
    plan = {"format": "vesper-plan/1", "units": "px", "scale": {"m_per_px": view["m_per_px"]},
            "rooms": [{"id": "r1", "name": "Bedroom", "type": "bedroom", "polygon": rect(t, t, 4 - t, 4 - t)},
                      {"id": "r2", "name": "Living room", "type": "living", "polygon": rect(4 + t, t, 8 - t, 4 - t)}],
            "doors": [{"at": px(4.0, 1.95), "width": 0.9 * ppm, "rooms": ["r1", "r2"]},
                      {"at": px(6.45, 4.0), "width": 0.9 * ppm, "rooms": ["r2", "outside"]}],
            "windows": [{"at": px(2.1, 0.0), "width": 1.2 * ppm, "room": "r1"}],
            "entrance": 1}
    return {"floors_drawn": 1, "plan": plan}


def test_a_reading_in_pixels_comes_back_in_metres_in_the_models_frame(tmp_path):
    path = save(furnished(), tmp_path / "house.glb")
    view = render_plan_view(path, tmp_path / "probe.png")
    fake = Fake(envelope(json.dumps(answer_in_pixels(view))))
    plan, meta, warnings = read_mesh_with_claude(path, runner=fake)
    load_plan(plan)
    assert plan["units"] == "m" and plan["scale"] is None
    rooms = {r["id"]: r for r in plan["rooms"]}
    for r in rooms.values():
        assert polygon_area([tuple(p) for p in r["polygon"]]) == pytest.approx(3.88 ** 2, rel=0.03)
    assert min(p[0] for r in rooms.values() for p in r["polygon"]) == pytest.approx(0.0, abs=1e-6)
    assert min(p[1] for r in rooms.values() for p in r["polygon"]) == pytest.approx(0.0, abs=1e-6)
    assert plan["doors"][0]["width"] == pytest.approx(0.9, abs=0.01)
    assert plan["windows"][0]["width"] == pytest.approx(1.2, abs=0.01)
    # the transform takes the file to the plan: each room's middle, the window and the door land where the plan has them
    m = np.array(meta["transform"])

    def plan_xz(x, y, z):
        f = m @ np.array([x, y, z, 1.0])
        return f[0], f[2], f[1]
    for rid, centre in (("r1", (2.0, 0.0, 2.0)), ("r2", (6.0, 0.0, 2.0))):
        x, z, y = plan_xz(*centre)
        assert y == pytest.approx(0.0, abs=0.05)
        assert point_in_polygon((x, z), [tuple(p) for p in rooms[rid]["polygon"]])
    x, z, _ = plan_xz(2.1, 1.5, 0.0)
    assert [x, z] == pytest.approx(plan["windows"][0]["at"], abs=0.05)
    x, z, _ = plan_xz(4.0, 1.0, 1.95)
    assert [x, z] == pytest.approx(plan["doors"][0]["at"], abs=0.05)
    # read_mesh's meta, with the reader's own beside it
    geo = read_mesh(path)[1]
    for k in ("up", "scale", "floor_y", "ceiling_y"):
        assert meta[k] == pytest.approx(geo[k], abs=1e-6)
    assert meta["reader"] == "claude" and meta["m_per_px"] == 1 / 50 and meta["floors_drawn"] == 1
    assert meta["render"] is None
    assert not any("outside the" in w or "stated a scale" in w for w in warnings)
    assert check_house(rectify(plan)) == []
    # Claude was told what the picture is, at what scale, and kept to the one copy of it
    prompt = fake.calls[0][2]
    assert "3D scan" in prompt and "50 pixels per metre" in prompt and '"m_per_px": 0.02' in prompt
    assert prompt.index("About this image:") < prompt.index("The image:")


def test_the_render_can_be_kept(tmp_path):
    path = save(furnished(), tmp_path / "house.glb")
    view = render_plan_view(path, tmp_path / "probe.png")
    keep = tmp_path / "kept" / "render.png"
    _, meta, _ = read_mesh_with_claude(path, runner=Fake(envelope(json.dumps(answer_in_pixels(view)))),
                                       render_to=keep)
    assert meta["render"] == str(keep) and keep.read_bytes() == (tmp_path / "probe.png").read_bytes()


def test_a_wrong_stated_scale_and_points_off_the_image_are_said(tmp_path):
    path = save(furnished(), tmp_path / "house.glb")
    view = render_plan_view(path, tmp_path / "probe.png")
    answer = answer_in_pixels(view)
    answer["plan"]["scale"] = {"m_per_px": 0.05}
    answer["plan"]["windows"][0]["at"] = [view["size"][0] * 2, 10]
    plan, _, warnings = read_mesh_with_claude(path, runner=Fake(envelope(json.dumps(answer))))
    assert any("stated a scale of 0.05" in w for w in warnings)
    assert any("outside the" in w for w in warnings)
    assert plan["doors"][0]["width"] == pytest.approx(0.9, abs=0.01)  # the render's scale, not the stated one


def test_two_failed_answers_raise_with_claudes_text(tmp_path):
    path = save(furnished(), tmp_path / "house.glb")
    fake = Fake(envelope("I can see two rooms but cannot place the walls."),
                envelope("Sorry, the walls are unclear to me."))
    with pytest.raises(PlanError, match="the walls are unclear to me"):
        read_mesh_with_claude(path, runner=fake)
    assert len(fake.calls) == 2


def test_claude_saying_it_is_no_plan_is_raised(tmp_path):
    path = save(furnished(), tmp_path / "house.glb")
    fake = Fake(envelope(json.dumps({"error": "this is a scatter of dots"})),
                envelope(json.dumps({"error": "still a scatter of dots"})))
    with pytest.raises(PlanError, match="still a scatter of dots"):
        read_mesh_with_claude(path, runner=fake)


def test_a_low_storey_is_cut_lower(tmp_path):
    view = render_plan_view(save(house(wall_h=2.2), tmp_path / "low.glb"), tmp_path / "low.png")
    assert view["cut_m"] == pytest.approx(1.1, abs=0.05)
    assert any("cuts it" in w for w in view["warnings"])


def test_a_large_home_is_drawn_at_fewer_pixels_per_metre(tmp_path):
    mesh = house()
    mesh.apply_scale([5.0, 1.0, 5.0])  # 40 x 20 m, the walls still 2.6 m tall
    view = render_plan_view(save(mesh, tmp_path / "big.glb"), tmp_path / "big.png")
    img = decode((tmp_path / "big.png").read_bytes())
    assert max(img.width, img.height) <= 1568 and img.width * img.height <= 1_150_000
    assert view["m_per_px"] > 1 / 50
    assert any("px per metre" in w for w in view["warnings"])


def test_bad_px_per_m(tmp_path):
    from vesper.lab.scan_mesh import ScanError
    path = save(house(), tmp_path / "house.glb")
    for bad in (0, 5, True, "50"):
        with pytest.raises(ScanError, match="px_per_m"):
            render_plan_view(path, tmp_path / "x.png", px_per_m=bad)



