"""Furniture a generated home gets by itself (PI 2026-11-01: free, in seconds, no Blender and no Claude): each room
its usual pieces against its walls, none in front of a doorway or over a device, and the walking map blocked under
each solid piece, so people walk round it, while every room, doorway and device stays reachable."""
import json
import textwrap

from tests.lab.homes import catalog
from vesper.lab.furnishing import FORMAT, furnish, room_kind
from vesper.lab.generate import generate_home, load_request
from vesper.lab.geometry import distance
from vesper.lab.home import load_lab_home
from vesper.lab.worldmap import WorldMap

REQUEST = """\
    format: vesper-request/1
    program: [living, kitchen, bedroom, bathroom, office, dining, laundry]
    seed: 4
    devices:
      - {product: "zigbee:acme:lock-1", at: front_door}
      - {category: light, per: room}
      - {category: motion_sensor, per: room, except: [bathroom]}
      - {category: plug, count: 3}
"""


def make(tmp_path, text=REQUEST):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "request.yaml").write_text(textwrap.dedent(text))
    out = tmp_path / "home"
    generate_home(load_request(tmp_path / "request.yaml"), catalog(), out)
    return out


def test_a_room_s_kind_comes_from_its_label():
    from vesper.lab.worldmap import Room
    assert [room_kind(Room("r", label, ())) for label in ("living room", "kitchen", "bedroom", "hallway", "garage")] \
        == ["living", "kitchen", "bedroom", "hall", None]


def test_a_generated_home_is_furnished_room_by_room_and_says_so_beside_it(tmp_path):
    out = make(tmp_path)
    data = json.loads((out / "furniture.json").read_text())
    assert data["format"] == FORMAT
    by_room: dict[str, set] = {}
    for item in data["items"]:
        by_room.setdefault(item["room"], set()).add(item["kind"])
    home = load_lab_home(out / "home.yaml", catalog())
    kinds = {r.id: room_kind(r) for r in home.map.rooms}
    room_of = {kind: rid for rid, kind in kinds.items()}
    assert {"sofa", "tv_stand", "coffee_table"} <= by_room[room_of["living"]]
    assert {"bed", "nightstand"} <= by_room[room_of["bedroom"]]
    assert {"counter", "fridge"} <= by_room[room_of["kitchen"]]
    assert {"bath", "toilet", "vanity"} <= by_room[room_of["bathroom"]]
    assert {"desk", "office_chair"} <= by_room[room_of["office"]]
    assert "dining_table" in by_room[room_of["dining"]]
    assert {"washer", "dryer"} <= by_room[room_of["laundry"]]


def test_people_walk_round_solid_furniture_but_reach_every_doorway_and_device(tmp_path):
    out = make(tmp_path)
    home = load_lab_home(out / "home.yaml", catalog())
    original = WorldMap.load(out / "map.original.json")
    g, items = home.map.grid, json.loads((out / "furniture.json").read_text())["items"]
    bed = next(i for i in items if i["kind"] == "bed")
    i, j = g.cell_of(bed["x"], bed["z"])
    assert original.grid.is_walkable(i, j) and not g.is_walkable(i, j)  # the floor under the bed is taken
    rug = next((i for i in items if i["kind"] == "rug"), None)
    if rug:  # a rug is walked on: its floor stays, except where a solid piece stands on it
        bx0, bz0, bx1, bz1 = rug["box"]
        edge = (bx0 + 0.05, (bz0 + bz1) / 2) if bx1 - bx0 > bz1 - bz0 else ((bx0 + bx1) / 2, bz0 + 0.05)
        assert rug["solid"] is False and g.is_walkable(*g.cell_of(*edge))
    reach = _reached(home.map, home.avatar["start"])
    for dw in home.map.doorways:
        assert any(c in reach for c in dw.cells), dw.id
    for d in home.devices:
        hand = d.wall_switch or d.position
        if d.mount == "ceiling" and d.wall_switch is None:
            continue
        assert any(distance(g.center(*c), hand) <= 1.0 for c in reach), d.name


def test_no_piece_stands_in_front_of_a_doorway_or_over_a_wall_device(tmp_path):
    out = make(tmp_path)
    home = load_lab_home(out / "home.yaml", catalog())
    for item in json.loads((out / "furniture.json").read_text())["items"]:
        if not item["solid"]:
            continue
        x0, z0, x1, z1 = item["box"]
        for dw in home.map.doorways:
            if item["room"] in dw.rooms:
                assert _gap(item["box"], dw.center) >= 0.85, (item["kind"], dw.id)
        for d in home.devices:
            if d.mount in ("wall", "floor") or d.wall_switch is not None:
                assert _gap(item["box"], d.wall_switch or d.position) >= 0.4, (item["kind"], d.name)


def test_the_same_request_gives_the_same_furniture_and_a_home_can_ask_for_none(tmp_path):
    a, b = make(tmp_path / "a"), make(tmp_path / "b")
    assert (a / "furniture.json").read_text() == (b / "furniture.json").read_text()
    bare = make(tmp_path / "c", REQUEST.replace("seed: 4", "seed: 4\n    furnish: false"))
    assert not (bare / "furniture.json").exists() and not (bare / "map.original.json").exists()


def test_furnishing_a_map_leaves_the_map_alone_until_it_is_blocked(tmp_path):
    out = make(tmp_path, REQUEST.replace("seed: 4", "seed: 4\n    furnish: false"))
    home = load_lab_home(out / "home.yaml", catalog())
    before = bytes(home.map.grid.walkable)
    items = furnish(home.map, home.devices, home.avatar["start"])
    assert items and bytes(home.map.grid.walkable) == before


def _gap(box, p) -> float:
    x0, z0, x1, z1 = box
    dx = max(x0 - p[0], 0, p[0] - x1)
    dz = max(z0 - p[1], 0, p[1] - z1)
    return (dx * dx + dz * dz) ** 0.5


def _reached(world_map, start) -> set:
    g = world_map.grid
    seen, todo = set(), [g.cell_of(*start)]
    while todo:
        c = todo.pop()
        if c in seen or not g.is_walkable(*c):
            continue
        seen.add(c)
        i, j = c
        todo += [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]
    return seen
