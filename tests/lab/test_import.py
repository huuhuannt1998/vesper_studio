"""`vesper lab import` (floor-plan import, task A2): a home built from a `vesper-plan/1` file, the same pipeline as
`vesper lab generate` after the map: rooms, walkable floor and doorways from the plan's polygons, a porch at the
entrance, the plan's windows (and rule windows where a room has none), devices, furniture and the layout check."""
import copy
import json
from collections import deque

import pytest

from tests.lab.test_cli_lab import setup
from vesper import cli
from vesper.lab.generate import GenerateError, Request, floor_plan_from, generate_from_plan
from vesper.lab.home import load_lab_home
from vesper.lab.plan import rectify
from vesper.lab.windows import load_windows
from vesper.twins.home import load_catalog


def rect(x0, z0, x1, z1):
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


def three_rooms(**kw) -> dict:
    """A hand-written plan in metres: a living room with the front door on its west wall, a kitchen and a bedroom
    east of it. Two windows: the living room's north wall and the bedroom's east wall; the kitchen has none."""
    plan = {"format": "vesper-plan/1", "units": "m", "scale": None,
            "rooms": [{"id": "lr", "name": "Living room", "type": "living", "polygon": rect(0, 0, 5, 5)},
                      {"id": "k", "name": "Kitchen", "type": "kitchen", "polygon": rect(5, 0, 8.5, 3)},
                      {"id": "b", "name": "Bedroom", "type": "bedroom", "polygon": rect(5, 3, 8.5, 7)}],
            "doors": [{"at": [0, 2.5], "width": 0.9, "rooms": ["outside", "lr"]},
                      {"at": [5, 1.5], "width": 0.9, "rooms": ["lr", "k"]},
                      {"at": [5, 4.2], "width": 0.9, "rooms": ["lr", "b"]}],
            "windows": [{"at": [2.5, 0], "width": 1.6, "room": "lr"},
                        {"at": [8.5, 5], "width": 1.2, "room": "b"}],
            "entrance": 0}
    plan.update(kw)
    return plan


def write(tmp_path, plan, name="three.json"):
    path = tmp_path / name
    path.write_text(json.dumps(plan))
    return path


def reachable_rooms(world_map, start) -> set[str]:
    g = world_map.grid
    first = g.cell_of(*start)
    seen, todo = {first}, deque([first])
    while todo:
        i, j = todo.popleft()
        for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
            if n not in seen and g.is_walkable(*n):
                seen.add(n)
                todo.append(n)
    return {world_map.room_of_cell(i, j).id for i, j in seen if world_map.room_of_cell(i, j) is not None}


def test_a_hand_written_three_room_plan_in_metres_imports_with_no_scale(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(write(tmp_path, three_rooms())), str(out), "--catalog", str(cat)]) == 0
    assert "3 rooms" in capsys.readouterr().out
    for f in ("home.yaml", "map.json", "windows.json", "furniture.json", "plan.json"):
        assert (out / f).is_file(), f
    text = (out / "home.yaml").read_text()
    # no command named: the lobby's Import a plan builds the same home
    assert text.splitlines()[0] == "# Imported from three.json (floor-plan import): 3 rooms, their doors"
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    assert home.scene == "imported/three"
    ids = {r.id for r in home.map.rooms}
    assert ids == {"living", "kitchen", "bedroom", "porch"}  # named by type, as a generated home's are
    assert home.map.room("porch").outdoor
    assert reachable_rooms(home.map, home.avatar["start"]) == ids  # with the furniture on the floor
    assert "front_door_lock" in text and "kitchen_light" in text
    assert cli.main(["lab", "check", str(out / "home.yaml"), "--catalog", str(cat)]) == 0


def test_the_plan_s_windows_are_kept_and_a_windowless_outside_room_gets_one_by_rule(tmp_path):
    cat, _ = setup(tmp_path)
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(write(tmp_path, three_rooms())), str(out), "--catalog", str(cat)]) == 0
    windows = load_windows(out)
    from_plan = [w for w in windows if w["source"] == "plan"]
    assert sorted((w["room"], w["side"], w["x"], w["z"], w["width"]) for w in from_plan) == [
        ("bedroom", "x1", 8.5, 5.0, 1.2), ("living", "z0", 2.5, 0.0, 1.6)]
    assert {w["facing"] for w in from_plan} == {0, 90}
    kitchen = [w for w in windows if w["room"] == "kitchen"]
    assert kitchen and all(w["source"] == "rule" for w in kitchen)
    assert all(w["source"] == "plan" for w in windows if w["room"] in ("living", "bedroom"))


def test_the_imported_home_passes_the_layout_check(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(write(tmp_path, three_rooms())), str(out), "--catalog", str(cat)]) == 0
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_a_plan_that_is_not_a_house_writes_nothing_and_lists_every_problem(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    plan = three_rooms()
    plan["doors"] = plan["doors"][:2]  # nothing leads to the bedroom
    plan["rooms"][1]["polygon"] = rect(5, 0, 6, 3)  # a 3 m² kitchen
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(write(tmp_path, plan)), str(out), "--catalog", str(cat)]) == 1
    err = capsys.readouterr().err
    assert "room 'b' is not reachable from the entrance" in err
    assert "room 'k' (kitchen): 3.0 m²" in err
    assert not out.exists()


def test_a_plan_file_that_is_not_valid_is_refused_naming_the_mistake(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    plan = three_rooms()
    plan["rooms"][0]["polygon"] = [[0, 0], [5, 0]]
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(write(tmp_path, plan)), str(out), "--catalog", str(cat)]) == 1
    assert "room 'lr': polygon is open" in capsys.readouterr().err and not out.exists()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert cli.main(["lab", "import", str(bad), str(out), "--catalog", str(cat)]) == 1
    assert "bad.json" in capsys.readouterr().err and not out.exists()


def test_a_file_that_is_neither_a_plan_nor_an_image_is_refused(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    for name, message in (("plan.txt", "expected a vesper-plan/1 .json file"),
                          ("plan.svg", "expected a vesper-plan/1 .json file"),
                          ("plan", "expected a vesper-plan/1 .json file")):
        src = tmp_path / name
        src.write_text("x")
        assert cli.main(["lab", "import", str(src), str(tmp_path / "mine"), "--catalog", str(cat)]) == 2
        assert message in capsys.readouterr().err and not (tmp_path / "mine").exists()


def test_an_existing_directory_is_never_overwritten(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "mine"
    out.mkdir()
    assert cli.main(["lab", "import", str(write(tmp_path, three_rooms())), str(out), "--catalog", str(cat)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_a_plan_in_pixels_takes_a_scale(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    plan = three_rooms(units="px")
    for r in plan["rooms"]:
        r["polygon"] = [[x * 100, z * 100] for x, z in r["polygon"]]
    for item in (*plan["doors"], *plan["windows"]):
        item["at"] = [item["at"][0] * 100, item["at"][1] * 100]
        item["width"] *= 100
    path = write(tmp_path, plan)
    assert cli.main(["lab", "import", str(path), str(tmp_path / "a"), "--catalog", str(cat)]) == 1
    assert "no scale: give --scale or --area" in capsys.readouterr().err and not (tmp_path / "a").exists()
    assert cli.main(["lab", "import", str(path), str(tmp_path / "b"), "--scale", "kitchen width 3.5 m",
                     "--catalog", str(cat)]) == 0
    home = load_lab_home(tmp_path / "b" / "home.yaml", load_catalog(cat))
    xs = [p[0] for p in home.map.room("kitchen").polygon]
    assert max(xs) - min(xs) == pytest.approx(3.5 - 0.2, abs=1e-6)  # the room inside its walls
    assert cli.main(["lab", "import", str(path), str(tmp_path / "c"), "--area", "57.5", "--catalog", str(cat)]) == 0


def test_a_request_sets_the_devices_but_not_the_rooms(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    req = tmp_path / "req.yaml"
    req.write_text("format: vesper-request/1\nseed: 3\nfurnish: false\ndevices:\n  - {category: light, per: room}\n")
    out = tmp_path / "mine"
    path = write(tmp_path, three_rooms())
    assert cli.main(["lab", "import", str(path), str(out), "--request", str(req), "--catalog", str(cat)]) == 0
    text = (out / "home.yaml").read_text()
    assert "living_light" in text and "front_door_lock" not in text and not (out / "furniture.json").exists()
    req.write_text("format: vesper-request/1\nrooms: 4\n")
    assert cli.main(["lab", "import", str(path), str(tmp_path / "x"), "--request", str(req),
                     "--catalog", str(cat)]) == 1
    assert "an imported home's rooms come from the plan" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_the_map_has_rooms_inside_their_walls_doorways_and_a_porch_outside_the_entrance():
    m = floor_plan_from(rectify(three_rooms()), "imported/t")
    assert m.scene == "imported/t"
    assert [r.id for r in m.rooms] == ["lr", "k", "b", "porch"]  # the plan's ids, the porch last
    assert m.room("lr").polygon == ((0.1, 0.1), (4.9, 0.1), (4.9, 4.9), (0.1, 4.9))
    assert m.room("lr").label == "living room" and m.room("b").label == "bedroom"
    assert sorted(d.id for d in m.doorways) == ["b-lr", "k-lr", "lr-porch"]
    front = m.doorway("lr-porch")
    assert front.outdoor and front.center[0] == pytest.approx(0.0, abs=0.06)
    assert front.width == pytest.approx(0.9, abs=0.05)
    porch = m.room("porch")
    assert porch.outdoor and max(x for x, _ in porch.polygon) == pytest.approx(-0.1)
    assert min(x for x, _ in porch.polygon) == pytest.approx(-2.0)
    # the wall between living room and kitchen is solid away from the door
    assert not m.grid.is_walkable(*m.grid.cell_of(5.0, 0.6))
    assert m.grid.is_walkable(*m.grid.cell_of(5.0, 1.5))


def test_an_l_shaped_room_and_a_back_door_are_built_and_the_front_door_is_the_entrance(tmp_path):
    plan = three_rooms()
    plan["rooms"][0]["polygon"] = [[0, 0], [5, 0], [5, 7], [2.5, 7], [2.5, 5], [0, 5]]  # 30 m²
    plan["doors"].append({"at": [8.5, 1.5], "width": 0.9, "rooms": ["k", "outside"]})
    m = floor_plan_from(rectify(plan), "imported/l")
    assert m.room("lr").polygon == ((0.1, 0.1), (4.9, 0.1), (4.9, 6.9), (2.6, 6.9), (2.6, 4.9), (0.1, 4.9))
    assert {r.id for r in m.rooms if r.outdoor} == {"porch", "porch_2"}
    assert sorted(d.id for d in m.doorways if d.outdoor) == ["k-porch_2", "lr-porch"]
    assert m.room_at(3.5, 6.0).id == "lr"
    cat, _ = setup(tmp_path)
    out = tmp_path / "l"
    request = Request(tmp_path, ["living", "kitchen", "bedroom"], 0, [{"category": "lock", "at": "front_door"}],
                      furnish=False)
    generate_from_plan(rectify(plan), request, load_catalog(cat), out, source="l.json")
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    lock = next(d for d in home.devices if d.name == "front_door_lock")
    assert home.map.doorway(lock.door).rooms == ("living", "porch")
    assert reachable_rooms(home.map, home.avatar["start"]) == {r.id for r in home.map.rooms}


def test_a_room_that_is_not_a_rectangle_gets_its_rule_windows_on_its_longest_real_outside_wall():
    from vesper.lab.windows import from_plan
    plan = {"format": "vesper-plan/1", "units": "m",
            "rooms": [{"id": "lr", "name": "", "type": "living",
                       "polygon": [[0, 0], [6, 0], [6, 3], [3, 3], [3, 5], [0, 5]]}],
            "doors": [{"at": [1.5, 0], "width": 0.9, "rooms": ["outside", "lr"]}], "entrance": 0}
    plan = rectify(plan)
    windows = from_plan(plan, floor_plan_from(plan, "imported/l"))
    # the room's box's longest outside side (z = 5) is a wall only for x 0..3: the windows go on x = 0, 5 m long
    # and without the door, two by the living room's rule
    assert [(w["x"], w["z"], w["side"], w["facing"], w["width"], w["source"]) for w in windows] == [
        (0.0, 1.7, "x0", 270, 1.6, "rule"), (0.0, 3.3, "x0", 270, 1.6, "rule")]


USHAPE = {"format": "vesper-plan/1", "units": "m", "scale": None,
          "rooms": [{"id": "lr", "name": "U", "type": "living",
                     "polygon": [[0, 0], [7, 0], [7, 5], [5, 5], [5, 2], [2, 2], [2, 5], [0, 5]]},
                    {"id": "k", "name": "Kitchen", "type": "kitchen", "polygon": rect(2, 2, 5, 5)}],
          "doors": [{"at": [3.5, 0], "width": 0.9, "rooms": ["outside", "lr"]},
                    {"at": [3.5, 2], "width": 0.9, "rooms": ["lr", "k"]}],
          "windows": [], "entrance": 0}


def test_every_room_with_an_outside_wall_gets_a_window_a_u_shaped_one_too(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "u"
    assert cli.main(["lab", "import", str(write(tmp_path, USHAPE, "ushape.json")), str(out),
                     "--catalog", str(cat)]) == 0
    windows = load_windows(out)
    living = [w for w in windows if w["room"] == "living"]
    assert len(living) == 2 and all(w["source"] == "rule" and w["side"] in ("x0", "x1") for w in living)
    assert all(w["x"] in (0.0, 7.0) and 0 < w["z"] < 5 for w in living)  # on the U's outer walls
    assert [(w["side"], w["source"]) for w in windows if w["room"] == "kitchen"] == [("z1", "rule")]
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0


def test_a_corridor_a_hall_and_another_room_are_named_and_furnished_by_type(tmp_path):
    plan = three_rooms()
    plan["rooms"] = [
        {"id": "h", "name": "Entry", "type": "hall", "polygon": rect(0, 0, 3, 2)},
        {"id": "c", "name": "Passage", "type": "corridor", "polygon": rect(3, 0, 9, 1.2)},
        {"id": "s", "name": "Storage\nroom", "type": "other", "polygon": rect(3, 1.2, 6, 4)},
        {"id": "bd", "name": "Bed", "type": "bedroom", "polygon": rect(6, 1.2, 9, 5)}]
    plan["doors"] = [{"at": [0, 1], "width": 0.9, "rooms": ["outside", "h"]},
                     {"at": [3, 0.6], "width": 0.9, "rooms": ["h", "c"]},
                     {"at": [4.5, 1.2], "width": 0.8, "rooms": ["c", "s"]},
                     {"at": [7.5, 1.2], "width": 0.9, "rooms": ["c", "bd"]}]
    plan["windows"] = []
    cat, _ = setup(tmp_path)
    out = tmp_path / "typed"
    generate_from_plan(rectify(plan), Request(tmp_path, [], 0, []), load_catalog(cat), out, source="typed.json")
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    labels = {r.id: r.label for r in home.map.rooms}
    assert labels == {"hall": "hall", "hallway": "hallway", "room": "other", "bedroom": "bedroom",
                      "porch": "porch/terrace/deck/driveway"}
    assert "#   room: s (Storage room)" in (out / "home.yaml").read_text()  # a name never breaks the comment
    rooms = {i["room"] for i in json.loads((out / "furniture.json").read_text())["items"]}
    assert "room" not in rooms and "bedroom" in rooms
    assert home.map.room_at(*home.avatar["start"]).id == "hall"  # no living room: where the entrance leads


def test_in_a_u_shaped_room_the_tv_faces_the_sofa_across_the_room_not_across_the_notch():
    from vesper.lab.furnishing import furnish
    from vesper.lab.geometry import point_in_polygon
    plan = {"format": "vesper-plan/1", "units": "m",
            "rooms": [{"id": "lr", "name": "", "type": "living",
                       "polygon": [[0, 0], [7, 0], [7, 6], [5, 6], [5, 2.5], [2, 2.5], [2, 6], [0, 6]]}],
            "doors": [{"at": [0.75, 0], "width": 0.9, "rooms": ["outside", "lr"]}], "entrance": 0}
    m = floor_plan_from(rectify(plan), "imported/u")
    items = {i["kind"]: i for i in furnish(m, [], None, [])}
    sofa, tv = items["sofa"], items.get("tv_stand")
    room = m.room("lr").polygon
    assert tv is None or all(point_in_polygon((sofa["x"] + (tv["x"] - sofa["x"]) * k / 20,
                                               sofa["z"] + (tv["z"] - sofa["z"]) * k / 20), room) for k in range(21))


def test_a_room_with_two_doors_keeps_a_walkway_between_them_when_furnished(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    plan = three_rooms()
    plan["doors"].append({"at": [8.5, 1.5], "width": 0.9, "rooms": ["k", "outside"]})  # the kitchen's back door
    out = tmp_path / "back"
    assert cli.main(["lab", "import", str(write(tmp_path, plan)), str(out), "--catalog", str(cat)]) == 0
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_generate_from_plan_refuses_a_plan_that_is_not_a_house(tmp_path):
    plan = three_rooms()
    plan["doors"] = plan["doors"][:2]
    cat, _ = setup(tmp_path)
    with pytest.raises(GenerateError, match="room 'b' is not reachable"):
        generate_from_plan(rectify(plan), Request(tmp_path, [], 0, []), load_catalog(cat), tmp_path / "x")
    assert not (tmp_path / "x").exists()


APT2BED = {"format": "vesper-plan/1", "units": "m", "scale": None,
           "rooms": [{"id": "lr", "name": "Living", "type": "living", "polygon": rect(0, 0, 6, 4.5)},
                     {"id": "k", "name": "Kitchen", "type": "kitchen", "polygon": rect(6, 0, 9.5, 4.5)},
                     {"id": "c", "name": "Hall", "type": "corridor", "polygon": rect(0, 4.5, 9.5, 5.7)},
                     {"id": "b1", "name": "Bed 1", "type": "bedroom", "polygon": rect(0, 5.7, 4, 9.5)},
                     {"id": "ba", "name": "Bath", "type": "bathroom", "polygon": rect(4, 5.7, 6, 9.5)},
                     {"id": "b2", "name": "Bed 2", "type": "bedroom", "polygon": rect(6, 5.7, 9.5, 9.5)}],
           "doors": [{"at": [9.5, 5.1], "width": 0.9, "rooms": ["outside", "c"]},
                     {"at": [3, 4.5], "width": 1.2, "rooms": ["c", "lr"]},
                     {"at": [8, 4.5], "width": 0.9, "rooms": ["c", "k"]},
                     {"at": [2, 5.7], "width": 0.9, "rooms": ["c", "b1"]},
                     {"at": [5, 5.7], "width": 0.8, "rooms": ["c", "ba"]},
                     {"at": [8, 5.7], "width": 0.9, "rooms": ["c", "b2"]}],
           "windows": [{"at": [3, 0], "width": 1.6, "room": "lr"}, {"at": [0, 2.2], "width": 1.6, "room": "lr"},
                       {"at": [9.5, 2], "width": 1.0, "room": "k"}, {"at": [0, 7.5], "width": 1.2, "room": "b1"},
                       {"at": [9.5, 7.5], "width": 1.2, "room": "b2"}],
           "entrance": 0}


def test_an_imported_corridor_is_a_hallway_with_its_light_and_motion_sensor(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "apt"
    assert cli.main(["lab", "import", str(write(tmp_path, APT2BED, "apt2bed.json")), str(out),
                     "--catalog", str(cat)]) == 0
    text = (out / "home.yaml").read_text()
    assert "#   hallway: c (Hall)" in text
    for name in ("hallway_light", "hallway_motion", "hallway_thermostat", "bedroom_1_light", "bedroom_2_light"):
        assert f"name: {name}," in text, name
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0


TWODOORS = {"format": "vesper-plan/1", "units": "m", "scale": None,
            "rooms": [{"id": "lr", "name": "Living", "type": "living", "polygon": rect(0, 0, 5, 5)},
                      {"id": "k", "name": "Kitchen", "type": "kitchen", "polygon": rect(5, 0, 8.5, 4)},
                      {"id": "b", "name": "Bed", "type": "bedroom", "polygon": rect(0, 5, 4, 9)},
                      {"id": "ba", "name": "Bath", "type": "bathroom", "polygon": rect(4, 5, 6.5, 7.5)}],
            "doors": [{"at": [0, 2.5], "width": 0.9, "rooms": ["outside", "lr"]},
                      {"at": [5, 2], "width": 0.9, "rooms": ["lr", "k"]},
                      {"at": [2, 5], "width": 0.9, "rooms": ["lr", "b"]},
                      {"at": [4, 6.5], "width": 0.8, "rooms": ["b", "ba"]},
                      {"at": [4, 0], "width": 1.6, "rooms": ["lr", "outside"]},  # a patio door
                      {"at": [6, 0], "width": 0.9, "rooms": ["k", "outside"]}],  # and a garden door, 2 m along
            "windows": [{"at": [1.5, 0], "width": 1.2, "room": "lr"}], "entrance": 0}


def test_two_outside_doors_close_together_open_onto_one_porch(tmp_path, capsys):
    m = floor_plan_from(rectify(TWODOORS), "imported/two")
    assert sorted(r.id for r in m.rooms if r.outdoor) == ["porch", "porch_2"]
    assert sorted(d.id for d in m.doorways if d.outdoor) == ["k-porch_2", "lr-porch", "lr-porch_2"]
    xs = [x for x, _ in m.room("porch_2").polygon]
    assert min(xs) <= 4 - 0.8 and max(xs) >= 6 + 0.45  # it reaches past both doors
    cat, _ = setup(tmp_path)
    out = tmp_path / "two"
    assert cli.main(["lab", "import", str(write(tmp_path, TWODOORS, "twodoors.json")), str(out),
                     "--catalog", str(cat)]) == 0
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0


def test_a_porch_keeps_a_wall_back_from_another_room_and_a_room_in_its_way_is_named():
    plan = three_rooms()
    plan["rooms"].append({"id": "shed", "name": "Shed", "type": "other", "polygon": rect(-3, 0, -2.05, 5)})
    m = floor_plan_from(plan, "imported/shed")
    assert min(x for x, _ in m.room("porch").polygon) == pytest.approx(-1.5)  # shrunk: 2 m would touch the shed
    plan["rooms"][-1]["polygon"] = rect(-1.5, 1, -0.6, 4)
    with pytest.raises(GenerateError, match="room 'shed' stands where its porch would be"):
        floor_plan_from(plan, "imported/shed")


def test_a_door_that_does_not_open_between_its_rooms_is_named():
    plan = three_rooms()
    plan["rooms"][1]["polygon"] = rect(6, 0, 9.5, 3)  # the kitchen a metre east of the living room
    plan["doors"][1]["at"] = [5.5, 1.5]
    with pytest.raises(GenerateError, match="door 2 does not open between k and lr"):  # doors counted from 1
        floor_plan_from(plan, "imported/gap")
    with pytest.raises(GenerateError, match="door 5 does not open between k and lr"):  # door 5 as read
        floor_plan_from(plan, "imported/gap", numbers=[0, 4, 5])


def test_a_porch_in_the_way_is_named_by_its_door_as_read():
    plan = three_rooms()
    plan["doors"].append({"at": [0, 4.0], "width": 0.9, "rooms": ["lr", "outside"]})  # a back door by the front one
    plan["rooms"].append({"id": "shed", "name": "Shed", "type": "other", "polygon": rect(-2.5, 4.6, -1.0, 5.0)})
    with pytest.raises(GenerateError, match=r"door 9 \(from 'lr' to the outside\)"):
        floor_plan_from(plan, "imported/two", numbers=[0, 1, 2, 8])


def test_a_part_of_a_room_narrower_than_its_walls_is_named():
    plan = three_rooms()
    plan["rooms"][0]["polygon"] = [[0, 0], [5, 0], [5, 5], [0.15, 5], [0.15, 6], [0, 6]]  # a 15 cm arm
    with pytest.raises(GenerateError, match="room 'lr': a part of it is narrower than its walls"):
        floor_plan_from(plan, "imported/thin")


def test_a_failure_after_writing_began_leaves_no_home(tmp_path, monkeypatch):
    from vesper.lab import generate
    cat, _ = setup(tmp_path)

    def broken(out, catalog):
        raise RuntimeError("the furniture broke")
    monkeypatch.setattr(generate, "_furnish", broken)
    with pytest.raises(RuntimeError):
        generate_from_plan(rectify(three_rooms()), Request(tmp_path, [], 0, []), load_catalog(cat), tmp_path / "x")
    assert not (tmp_path / "x").exists()


# A plan Claude read from a floor-plan image (task A3's live try), in pixels with a scale bar: the hall's arm to
# bedroom R2 is 120 px (0.91 m) between wall centre lines, 0.71 m inside its walls
PLAN2 = {"format": "vesper-plan/1", "units": "px", "scale": {"length_m": 10.668, "from": [100, 70], "to": [1500, 70]},
         "rooms": [{"id": "r1", "name": "PARKING", "type": "other", "polygon": rect(100, 100, 460, 680)},
                   {"id": "r2", "name": "Store", "type": "other", "polygon": rect(100, 680, 460, 920)},
                   {"id": "r3", "name": "KIT", "type": "kitchen", "polygon": rect(100, 920, 380, 1300)},
                   {"id": "r4", "name": "HALL", "type": "hall",
                    "polygon": [[460, 100], [1100, 100], [1100, 1300], [980, 1300], [980, 920], [460, 920]]},
                   {"id": "r5", "name": "R3", "type": "bedroom", "polygon": rect(380, 920, 980, 1300)},
                   {"id": "r6", "name": "R1", "type": "bedroom", "polygon": rect(1100, 100, 1500, 500)},
                   {"id": "r7", "name": "B", "type": "bathroom", "polygon": rect(1100, 500, 1500, 720)},
                   {"id": "r8", "name": "R2", "type": "bedroom", "polygon": rect(1100, 720, 1500, 1300)}],
         "doors": [{"at": [300, 680], "width": 120, "rooms": ["r1", "r2"]},
                   {"at": [460, 780], "width": 120, "rooms": ["r2", "r4"]},
                   {"at": [220, 920], "width": 120, "rooms": ["r2", "r3"]},
                   {"at": [720, 920], "width": 160, "rooms": ["r4", "r5"]},
                   {"at": [1100, 300], "width": 120, "rooms": ["r4", "r6"]},
                   {"at": [1100, 610], "width": 120, "rooms": ["r4", "r7"]},
                   {"at": [1100, 1110], "width": 120, "rooms": ["r4", "r8"]},
                   {"at": [740, 100], "width": 140, "rooms": ["r4", "outside"]}],
         "windows": [], "entrance": 7}


def test_a_passage_the_plan_draws_narrower_than_a_walkway_is_the_plan_s_not_the_furniture_s(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "plan2"
    assert cli.main(["lab", "import", str(write(tmp_path, PLAN2, "plan2.json")), str(out), "--catalog", str(cat)]) == 0
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    text = capsys.readouterr().out
    assert "0 error(s)" in text
    hall = [line for line in text.splitlines() if "walkway: hall:" in line]
    assert hall == ["  warn  walkway: hall: only a 0.7 m path to bedroom_3-hall even with no furniture: the plan's "
                    "room is narrower there"]  # one line per narrow doorway, not one per pair
    # the hall is furnished all the same: what the furniture may not do is close a walkway the plan has
    assert "hall" in {i["room"] for i in json.loads((out / "furniture.json").read_text())["items"]}


# The reviewer's recessed entrance: the front door at the back of a 1.2 m recess between two bedrooms
RECESS = {"format": "vesper-plan/1", "units": "m", "scale": None,
          "rooms": [{"id": "lr", "name": "Living", "type": "living", "polygon": rect(0, 0, 6, 5)},
                    {"id": "b1", "name": "Bed 1", "type": "bedroom", "polygon": rect(0, 5, 2.4, 9)},
                    {"id": "b2", "name": "Bed 2", "type": "bedroom", "polygon": rect(3.6, 5, 6, 9)}],
          "doors": [{"at": [3, 5], "width": 0.9, "rooms": ["outside", "lr"]},
                    {"at": [1.2, 5], "width": 0.9, "rooms": ["lr", "b1"]},
                    {"at": [4.8, 5], "width": 0.9, "rooms": ["lr", "b2"]}],
          "windows": [], "entrance": 0}


def test_a_recessed_entrance_gets_a_porch_that_fits_inside_the_recess(tmp_path, capsys):
    m = floor_plan_from(rectify(RECESS), "imported/recess")
    xs, zs = [x for x, _ in m.room("porch").polygon], [z for _, z in m.room("porch").polygon]
    assert 2.4 + 0.1 <= min(xs) and max(xs) <= 3.6 - 0.1 and max(xs) - min(xs) >= 0.9  # half a wall off each bedroom
    assert min(zs) == pytest.approx(5.1) and max(zs) - min(zs) >= 0.6
    assert "lr-porch" in {d.id for d in m.doorways}
    cat, _ = setup(tmp_path)
    out = tmp_path / "recess"
    assert cli.main(["lab", "import", str(write(tmp_path, RECESS, "recess.json")), str(out),
                     "--catalog", str(cat)]) == 0
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_a_recess_narrower_than_a_porch_s_least_width_is_refused_naming_the_rooms():
    plan = copy.deepcopy(RECESS)
    plan["rooms"][1]["polygon"] = rect(0, 5, 2.55, 9)
    plan["rooms"][2]["polygon"] = rect(3.45, 5, 6, 9)  # 0.9 m between the bedrooms: 0.7 m once each keeps 0.1 m
    with pytest.raises(GenerateError, match="room 'b1', room 'b2' stand where its porch would be"):
        floor_plan_from(plan, "imported/recess")


# The reviewer's homes h3/arm5 and h3/arm2: a living room with a 1 m arm (0.8 m inside its walls) to a third room,
# and furniture placed by hand that closes a path (the map is the plan's: floor_plan_from rebuilds it)
ARM_LIVING = [[0.0, 0.0], [5.0, 0.0], [5.0, 1.5], [8.0, 1.5], [8.0, 2.5], [5.0, 2.5], [5.0, 4.0], [0.0, 4.0]]
ARM5 = {"format": "vesper-plan/1", "units": "m", "scale": None,
        "rooms": [{"id": "living", "name": "L", "type": "living", "polygon": ARM_LIVING},
                  {"id": "bathroom", "name": "Bath", "type": "bathroom", "polygon": rect(8, 0, 10, 3)},
                  {"id": "bedroom", "name": "Bed", "type": "bedroom", "polygon": rect(0, 4, 4, 8)}],
        "doors": [{"at": [0.0, 2.0], "width": 0.9, "rooms": ["outside", "living"]},
                  {"at": [8.0, 2.0], "width": 0.8, "rooms": ["living", "bathroom"]},
                  {"at": [2.0, 4.0], "width": 0.9, "rooms": ["living", "bedroom"]}],
        "windows": [], "entrance": 0}
ARM5_FURNITURE = [  # kind, room, box, h; the sideboard and the cabinet wall the bedroom door off the front door
    ("tv_stand", "living", [0.15, 0.13, 1.75, 0.58], 0.5), ("armchair", "living", [0.13, 2.95, 0.98, 3.8], 0.85),
    ("bookshelf", "living", [2.95, 3.52, 3.85, 3.87], 1.9), ("bath", "bathroom", [9.12, 0.65, 9.87, 2.35], 0.55),
    ("vanity", "bathroom", [8.15, 0.13, 8.95, 0.63], 0.85), ("toilet", "bathroom", [9.22, 0.15, 9.87, 0.55], 0.8),
    ("bed", "bedroom", [1.2, 5.82, 2.8, 7.87], 0.55), ("nightstand", "bedroom", [2.88, 7.47, 3.33, 7.87], 0.5),
    ("nightstand", "bedroom", [0.15, 7.47, 0.6, 7.87], 0.5), ("wardrobe", "bedroom", [3.27, 4.15, 3.87, 5.35], 2.0),
    ("plant", "bedroom", [3.45, 7.47, 3.85, 7.87], 1.0), ("sideboard", "living", [0.9, 2.9, 4.9, 3.1], 0.9),
    ("cabinet", "living", [0.9, 3.1, 1.1, 3.9], 0.9)]
ARM2 = {"format": "vesper-plan/1", "units": "m", "scale": None,
        "rooms": [{"id": "living", "name": "L", "type": "living", "polygon": ARM_LIVING},
                  {"id": "kitchen", "name": "Kitchen", "type": "kitchen", "polygon": rect(8, 0, 11, 4)}],
        "doors": [{"at": [0.0, 2.0], "width": 0.9, "rooms": ["outside", "living"]},
                  {"at": [8.0, 2.0], "width": 0.8, "rooms": ["living", "kitchen"]}],
        "windows": [], "entrance": 0}
ARM2_FURNITURE = [  # the bookshelf stands across the arm: the kitchen's only way in is closed
    ("sofa", "living", [2.75, 2.97, 4.85, 3.87], 0.85), ("coffee_table", "living", [3.3, 1.97, 4.3, 2.52], 0.42),
    ("tv_stand", "living", [3.0, 0.13, 4.6, 0.58], 0.5), ("armchair", "living", [0.13, 2.95, 0.98, 3.8], 0.85),
    ("bookshelf", "living", [1.15, 3.52, 2.05, 3.87], 1.9), ("counter", "kitchen", [10.25, 0.55, 10.87, 3.45], 0.9),
    ("fridge", "kitchen", [8.15, 0.13, 8.87, 0.85], 1.85), ("dining_table", "kitchen", [9.1, 1.4, 9.9, 2.6], 0.75),
    ("bookshelf", "living", [6.05, 1.5, 6.55, 2.5], 1.9)]


def home_with(tmp_path, plan, furniture):
    """The plan's home, unfurnished by VESPER, with these pieces in furniture.json (and the bare plan kept)."""
    import shutil
    cat, _ = setup(tmp_path)
    out = tmp_path / "arm"
    generate_from_plan(rectify(plan), Request(tmp_path, [], 0, [], furnish=False), load_catalog(cat), out)
    shutil.copyfile(out / "map.json", out / "map.original.json")
    items = [{"kind": k, "room": room, "x": (box[0] + box[2]) / 2, "z": (box[1] + box[3]) / 2, "w": box[3] - box[1],
              "d": box[2] - box[0], "h": h, "facing": 180, "solid": True, "box": box}  # facing -x (the counter's)
             for k, room, box, h in furniture]
    (out / "furniture.json").write_text(json.dumps({"format": "vesper-furniture/1", "items": items}))
    return cat, out


@pytest.mark.parametrize(("plan", "furniture", "message"), [
    (ARM5, ARM5_FURNITURE, "living: no 0.9 m path between bedroom-living and living-porch"),
    (ARM2, ARM2_FURNITURE, "living: the furniture closes the plan's 0.7 m path between kitchen-living and "
                           "living-porch")])
def test_furniture_that_closes_a_path_is_an_error_even_when_the_room_has_a_narrow_arm(tmp_path, capsys, plan,
                                                                                     furniture, message):
    cat, out = home_with(tmp_path, plan, furniture)
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 1
    text = capsys.readouterr().out
    walkway = next(line for line in text.splitlines() if line.startswith("  ERROR walkway: living: "))
    assert message.removeprefix("living: ") in walkway, text


@pytest.mark.parametrize(("plan", "furniture", "closing"), [(ARM5, ARM5_FURNITURE, ("sideboard", "cabinet")),
                                                            (ARM2, ARM2_FURNITURE, ("bookshelf",))])
def test_the_furnisher_keeps_every_pair_of_doorways_joined_as_wide_as_the_plan_joins_them(plan, furniture, closing):
    from vesper.lab.furnishing import _cells_in, _Room, _walkway
    m = floor_plan_from(rectify(plan), "imported/arm")
    r = _Room(m.room("living"), m, [], [])
    boxes = [box for kind, room, box, _ in furniture if room == "living" and kind in closing]
    boxes = boxes[-1:] if closing == ("bookshelf",) else boxes  # arm2: the bookshelf in the arm, not the other
    assert _walkway(r, set()) and not _walkway(r, {c for b in boxes for c in _cells_in(m.grid, b)})


def test_a_furnished_home_marks_its_map_without_furniture_as_the_bare_plan(tmp_path):
    cat, _ = setup(tmp_path)
    out = tmp_path / "three"
    generate_from_plan(rectify(three_rooms()), Request(tmp_path, [], 0, []), load_catalog(cat), out)
    original = json.loads((out / "map.original.json").read_text())
    assert original["bare"] is True and original["format"] == "vesper-map/1"


def test_an_hssd_house_s_map_original_is_not_a_bare_plan_every_pair_needs_a_walkway(tmp_path, capsys):
    """A dressed or renav'd HSSD house keeps HSSD's own walking map as map.original.json, its furniture already in
    it: narrowness there is no proof of the plan's, so each pair of doorways needs its 0.9 m path."""
    cat, out = home_with(tmp_path, ARM2, [])  # no furniture: the arm alone is narrower than a walkway
    home = out / "home.yaml"
    assert cli.main(["lab", "layout", str(home), "--catalog", str(cat)]) == 0  # imported/: the plan's (a warning)
    home.write_text(home.read_text().replace("scene: imported/", "scene: hssd/"))
    capsys.readouterr()
    assert cli.main(["lab", "layout", str(home), "--catalog", str(cat)]) == 1
    text = capsys.readouterr().out
    assert "ERROR walkway: living: no 0.9 m path between kitchen-living and living-porch" in text
    assert "even with no furniture" not in text
    original = out / "map.original.json"  # VESPER's own bare map says so, whatever the scene
    original.write_text(json.dumps({**json.loads(original.read_text()), "bare": True}))
    assert cli.main(["lab", "layout", str(home), "--catalog", str(cat)]) == 0


# --- real plans (task A6): a balcony, and two doors meeting end to end (CubiCasa5K plan 2207) ----------------------

def balcony_plan() -> dict:
    """A living room with the front door, a bedroom beside it, and a balcony north of the living room drawn 15 cm
    away (the thin wall between them as a gap); a door and a window from the living room onto the balcony."""
    return {"format": "vesper-plan/1", "units": "m", "scale": None,
            "rooms": [{"id": "lr", "name": "Living room", "type": "living", "polygon": rect(0, 0, 5, 4)},
                      {"id": "bd", "name": "Bedroom", "type": "bedroom", "polygon": rect(5, 0, 8.5, 4)},
                      {"id": "bal", "name": "Balcony", "type": "balcony", "polygon": rect(0, 4.15, 5, 5.5)}],
            "doors": [{"at": [0, 2], "width": 0.9, "rooms": ["outside", "lr"]},
                      {"at": [5, 2], "width": 0.9, "rooms": ["lr", "bd"]},
                      {"at": [1, 4.1], "width": 0.9, "rooms": ["lr", "bal"]}],
            "windows": [{"at": [3.2, 4.1], "width": 1.6, "room": "lr"},
                        {"at": [8.5, 2], "width": 1.2, "room": "bd"}],
            "entrance": 0}


def test_a_balcony_is_built_as_an_outdoor_room_like_a_porch(tmp_path, capsys):
    import yaml
    cat, _ = setup(tmp_path)
    out = tmp_path / "bal"
    warnings = []
    generate_from_plan(rectify(balcony_plan(), warnings), Request(tmp_path, [], 0, []), load_catalog(cat), out,
                       source="bal.json")
    assert warnings == []
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    assert [r.id for r in home.map.rooms] == ["living", "bedroom", "porch", "balcony"]  # the porch first outdoors
    balcony = home.map.room("balcony")
    assert balcony.outdoor and balcony.label == "balcony/terrace"
    assert sorted(d.id for d in home.map.doorways) == ["balcony-living", "bedroom-living", "living-porch"]
    living = [w for w in load_windows(out) if w["room"] == "living"]
    assert [(w["source"], w["x"], w["z"]) for w in living] == [("plan", 3.2, 4.075)]  # onto the balcony
    assert not [w for w in load_windows(out) if w["room"] == "balcony"]
    devices = yaml.safe_load((out / "home.yaml").read_text())["devices"]
    assert not [d for d in devices if (d.get("at") or {}).get("room") == "balcony"]  # no indoor device outdoors
    assert not [i for i in json.loads((out / "furniture.json").read_text())["items"] if i["room"] == "balcony"]
    assert "# Imported from bal.json (floor-plan import): 2 rooms" in (out / "home.yaml").read_text()
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_a_resident_leaves_by_the_front_door_onto_the_porch_not_the_balcony(tmp_path):
    from types import SimpleNamespace

    from vesper.lab.resident import Resident
    cat, _ = setup(tmp_path)
    out = tmp_path / "bal"
    generate_from_plan(rectify(balcony_plan()), Request(tmp_path, [], 0, []), load_catalog(cat), out)
    home = load_lab_home(out / "home.yaml", load_catalog(cat))
    resident = Resident.__new__(Resident)
    resident.home = home
    resident.world = SimpleNamespace(doors={"balcony-living": False, "bedroom-living": False, "living-porch": False})
    assert resident._front_door() == "living-porch" and resident._outdoors() == "porch"


def t_junction(dx=0.0) -> dict:
    """A kitchen along the top; under it a hall (x 0 to 2) and a living room (x 2 to 5); a door from the kitchen
    into each, the two meeting end to end where the hall's and the living room's wall comes up, as CubiCasa5K plan
    2207 was read (kitchen to hall 2.17 m and kitchen to living room 2.23 m, on 2.18 m and 2.29 m of wall)."""
    return {"format": "vesper-plan/1", "units": "m", "scale": None,
            "rooms": [{"id": "k", "name": "", "type": "kitchen", "polygon": rect(dx, 0, 5 + dx, 2.5)},
                      {"id": "h", "name": "", "type": "hall", "polygon": rect(dx, 2.5, 2 + dx, 4.5)},
                      {"id": "lr", "name": "", "type": "living", "polygon": rect(2 + dx, 2.5, 5 + dx, 7)}],
            "doors": [{"at": [1.0 + dx, 2.5], "width": 1.99, "rooms": ["k", "h"]},
                      {"at": [3.5 + dx, 2.5], "width": 2.99, "rooms": ["k", "lr"]},
                      {"at": [5 + dx, 5], "width": 0.9, "rooms": ["lr", "outside"]}],
            "windows": [], "entrance": 2}


@pytest.mark.parametrize("dx", [0.0, 0.013, 0.037, 0.05, 0.071, 0.099])
def test_two_doors_meeting_end_to_end_open_two_doorways_not_one_into_three_rooms(dx):
    m = floor_plan_from(rectify(t_junction(dx)), "imported/t")
    assert sorted(d.id for d in m.doorways) == ["h-k", "k-lr", "lr-porch"]


def test_the_t_junction_plan_is_built_and_passes_the_layout_check(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "t"
    generate_from_plan(rectify(t_junction()), Request(tmp_path, [], 0, []), load_catalog(cat), out)
    assert cli.main(["lab", "layout", str(out / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_the_import_prints_what_it_left_out_or_narrowed_and_builds_the_home(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    plan = three_rooms()
    plan["windows"].append({"at": [5, 1.0], "width": 1.0, "room": "k"})  # on the wall it shares with the living room
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(write(tmp_path, plan)), str(out), "--catalog", str(cat)]) == 0
    text = capsys.readouterr().out
    assert "warning: window 2 of 'k' is not on an outside wall: left out" in text
    assert (out / "home.yaml").is_file()
