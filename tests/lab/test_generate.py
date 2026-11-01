"""The home generator's floor plan (home-generator spec §2, plan W9): a number of rooms, or a room list, becomes a
`vesper-map/1` every Lab feature reads: rooms that never overlap, each reachable from the front door through doors
wide enough to walk, the same home for the same seed."""
import time

import pytest

from vesper.lab.generate import GenerateError, floor_plan, program
from vesper.lab.worldmap import WorldMap


def test_a_count_becomes_a_room_list_bedrooms_and_bathrooms_growing_together():
    assert program(1) == ["living"]
    assert program(3) == ["living", "bedroom", "kitchen"]
    assert program(5) == ["living", "bedroom", "kitchen", "bathroom", "bedroom"]
    big = program(40)
    assert len(big) == 40 and big.count("bathroom") >= big.count("bedroom") // 3 and big.count("kitchen") == 1
    with pytest.raises(GenerateError, match="rooms: 0 is not 1 to 60"):
        program(0)
    with pytest.raises(GenerateError, match="rooms: 61 is not 1 to 60"):
        program(61)


def reachable(m: WorldMap) -> set[str]:
    """The rooms reachable from the porch through doorways."""
    seen, todo = {"porch"}, ["porch"]
    while todo:
        here = todo.pop()
        for d in m.doorways:
            if here in d.rooms:
                for r in d.rooms:
                    if r not in seen:
                        seen.add(r)
                        todo.append(r)
    return seen


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 9, 12, 20, 33, 60])
@pytest.mark.parametrize("seed", [0, 1, 7])
def test_every_room_is_reachable_through_doors_wide_enough_and_no_two_rooms_overlap(tmp_path, n, seed):
    m = floor_plan(program(n), seed=seed)
    m.save(tmp_path / "map.json")
    m = WorldMap.load(tmp_path / "map.json")  # it is a map every Lab feature reads
    indoor = [r for r in m.rooms if not r.outdoor]
    assert len(indoor) == n + 1  # the rooms and the corridor
    assert [r.id for r in m.rooms if r.outdoor] == ["porch"]
    assert reachable(m) == {r.id for r in m.rooms}
    assert all(d.width >= 0.8 for d in m.doorways)
    front = [d for d in m.doorways if d.outdoor]
    assert len(front) == 1 and "corridor" in front[0].rooms
    boxes = [(min(x for x, _ in r.polygon), min(z for _, z in r.polygon), max(x for x, _ in r.polygon),
              max(z for _, z in r.polygon)) for r in m.rooms]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)


def test_rooms_have_their_type_s_size_and_a_label_the_lab_knows():
    m = floor_plan(["living", "kitchen", "bedroom", "bedroom", "bathroom"], seed=0)
    by_id = {r.id: r for r in m.rooms}
    assert {"living", "kitchen", "bedroom_1", "bedroom_2", "bathroom", "corridor", "porch"} == set(by_id)
    assert by_id["living"].label == "living room" and by_id["corridor"].label == "hallway"

    def area(r):
        xs, zs = [x for x, _ in r.polygon], [z for _, z in r.polygon]
        return (max(xs) - min(xs)) * (max(zs) - min(zs))
    assert 12 <= area(by_id["living"]) <= 32 and 3 <= area(by_id["bathroom"]) <= 9


def test_the_same_request_and_seed_give_the_same_home_and_another_seed_another(tmp_path):
    for k, seed in enumerate((3, 3, 4)):
        floor_plan(program(9), seed=seed).save(tmp_path / f"{k}.json")
    assert (tmp_path / "0.json").read_bytes() == (tmp_path / "1.json").read_bytes()
    assert (tmp_path / "0.json").read_bytes() != (tmp_path / "2.json").read_bytes()


def test_an_unknown_room_type_is_named():
    with pytest.raises(GenerateError, match="program: 'dungeon' is not a room type; choose one of"):
        floor_plan(["living", "dungeon"], seed=0)


def test_sixty_rooms_are_planned_in_seconds():
    """Spec §0: under 5 s on the laptop (it takes about 2 s at background priority on a shared Mac)."""
    t = time.perf_counter()
    floor_plan(program(60), seed=0)
    assert time.perf_counter() - t < 5.0


# -- the request, the devices and the written home (spec §1, §3) --------------------------------------------
import textwrap  # noqa: E402

from tests.lab.homes import catalog  # noqa: E402
from vesper.lab.generate import generate_home, load_request  # noqa: E402
from vesper.lab.home import load_lab_home  # noqa: E402

REQUEST = """\
    format: vesper-request/1
    rooms: 5
    seed: 2
    devices:
      - {product: "zigbee:acme:lock-1", at: front_door}
      - {product: "zigbee:aqara:mccgq11lm", at: exterior_doors}
      - {category: light, per: room}
      - {category: motion_sensor, per: room, except: [bathroom]}
      - {category: plug, count: 3}
      - {category: button, at: living}
"""


def request(tmp_path, text=REQUEST):
    path = tmp_path / "request.yaml"
    path.write_text(textwrap.dedent(text))
    return load_request(path)


def test_a_request_becomes_a_home_the_lab_loads_with_every_device_placed_by_its_rule(tmp_path):
    out = tmp_path / "home"
    generate_home(request(tmp_path), catalog(), out)
    home = load_lab_home(out / "home.yaml", catalog())
    by_name = {d.name: d for d in home.devices}
    rooms = [r.id for r in home.map.rooms if not r.outdoor and r.id != "corridor"]
    assert by_name["front_door_lock"].product == "zigbee:acme:lock-1" and by_name["front_door_lock"].door
    assert by_name["front_door_contact"].mount == "door"
    lights = [d for d in home.devices if d.record["identity"]["category"] == "light"]
    assert sorted(d.room for d in lights) == sorted(rooms) and all(d.mount == "ceiling" for d in lights)
    motion = [d for d in home.devices if d.record["identity"]["category"] == "motion_sensor"]
    assert {d.room for d in motion} == set(rooms) - {"bathroom"}
    assert len([d for d in home.devices if d.record["identity"]["category"] == "plug"]) == 3
    (button,) = [d for d in home.devices if d.record["identity"]["category"] == "button"]
    assert button.room == "living" and button.mount == "wall"
    assert home.doors[home.devices[0].door] is False  # a door with a lock on it starts closed
    assert "synthetic" in (out / "home.yaml").read_text()


def test_every_wall_device_is_within_a_person_s_reach_from_the_room_s_door(tmp_path):
    from vesper.lab.geometry import distance
    out = tmp_path / "home"
    generate_home(request(tmp_path), catalog(), out)
    home = load_lab_home(out / "home.yaml", catalog())
    for d in home.devices:
        if d.mount == "wall":
            door = next(w for w in home.map.doorways if d.room in w.rooms)
            assert distance(d.position, door.center) <= 1.5, d.name


def test_a_category_picks_the_same_real_product_for_the_same_seed(tmp_path):
    generate_home(request(tmp_path), catalog(), tmp_path / "a")
    generate_home(request(tmp_path), catalog(), tmp_path / "b")
    assert (tmp_path / "a" / "home.yaml").read_text() == (tmp_path / "b" / "home.yaml").read_text()
    assert (tmp_path / "a" / "map.json").read_bytes() == (tmp_path / "b" / "map.json").read_bytes()


@pytest.mark.parametrize("old, new, message", [
    ("format: vesper-request/1", "format: vesper-request/7", r"format: 'vesper-request/7' is not vesper-request/1"),
    ("rooms: 5", "rooms: 99", r"rooms: 99 is not 1 to 60"),
    ("{category: plug, count: 3}", "{category: toaster, count: 3}",
     r"devices\[4\]\.category: no product of category 'toaster'; choose one of"),
    ('{product: "zigbee:acme:lock-1", at: front_door}', '{product: "zigbee:acme:lock-9", at: front_door}',
     r"devices\[0\]\.product: 'zigbee:acme:lock-9' is not in the catalog"),
    ("{category: button, at: living}", "{category: button, at: attic}",
     r"devices\[5\]\.at: 'attic' is not a room, a room type, a doorway, front_door or exterior_doors"),
    ("{category: light, per: room}", "{category: light, per: floor}", r"devices\[2\]\.per: 'floor' is not room"),
])
def test_a_request_s_mistakes_are_named(tmp_path, old, new, message):
    assert old in REQUEST
    with pytest.raises(GenerateError, match=message):
        generate_home(request(tmp_path, REQUEST.replace(old, new)), catalog(), tmp_path / "home")


def test_sixty_rooms_and_two_hundred_devices_are_generated_in_under_five_seconds(tmp_path):
    from vesper.lab.generate import Request
    devices = [{"category": "light", "per": "room"}, {"category": "motion_sensor", "per": "room"},
               {"category": "plug", "count": 100}]
    t = time.perf_counter()
    generate_home(Request(tmp_path, program(60), 0, devices), catalog(), tmp_path / "big")
    elapsed = time.perf_counter() - t
    assert len(load_lab_home(tmp_path / "big" / "home.yaml", catalog()).devices) >= 200
    assert elapsed < 5.0


@pytest.mark.parametrize("at", ["front_door", "exterior_doors"])
def test_a_device_that_does_not_go_on_a_door_asked_for_at_one_goes_beside_it_indoors(tmp_path, at):
    text = REQUEST.replace("{category: button, at: living}", f"{{category: button, at: {at}}}")
    out = tmp_path / "home"
    generate_home(request(tmp_path, text), catalog(), out)
    home = load_lab_home(out / "home.yaml", catalog())
    (button,) = [d for d in home.devices if d.record["identity"]["category"] == "button"]
    front = next(w for w in home.map.doorways if w.outdoor)
    assert button.mount == "wall" and button.room in front.rooms and not home.map.room(button.room).outdoor


def test_every_generated_ceiling_light_has_a_wall_switch_by_its_room_s_door_within_reach(tmp_path):
    """A plain wall switch, part of the light (PI 2026-10-31), so residents can light a dark room."""
    from vesper.lab.geometry import distance
    from vesper.lab.rules import INTERACT_M
    out = tmp_path / "home"
    generate_home(request(tmp_path), catalog(), out)
    home = load_lab_home(out / "home.yaml", catalog())
    g = home.map.grid
    lights = [d for d in home.devices if d.record["identity"]["category"] == "light"]
    walls = [d.position for d in home.devices if d.mount == "wall"]
    for d in lights:
        assert d.wall_switch is not None, d.name
        doors = [w.center for w in home.map.doorways if d.room in w.rooms]
        assert min(distance(d.wall_switch, c) for c in doors) <= 1.5, d.name
        assert all(distance(d.wall_switch, w) >= 0.3 for w in walls), d.name
        cells = [(g.origin[0] + (i + 0.5) * g.cell, g.origin[1] + (j + 0.5) * g.cell)
                 for j in range(g.height) for i in range(g.width) if g.walkable[j * g.width + i]]
        assert any(distance(c, d.wall_switch) <= INTERACT_M - 0.1 and home.map.room_at(*c)
                   and home.map.room_at(*c).id == d.room for c in cells), d.name
    others = [d for d in home.devices if d.mount == "ceiling" and d.record["identity"]["category"] != "light"]
    assert others and all(d.wall_switch is None for d in others)  # a motion sensor has nothing to switch


def _latch_side(home, d):
    """+1 when the switch is on the latch side of its doorway (the 3D view hinges a door at its low end)."""
    dw = min((w for w in home.map.doorways if d.room in w.rooms),
             key=lambda w: (not w.outdoor, (w.center[0] - d.wall_switch[0]) ** 2
                            + (w.center[1] - d.wall_switch[1]) ** 2))
    xs = [x for x, _ in home.map.room(d.room).polygon]
    along_x = abs(dw.center[0] - min(xs)) > 0.2 and abs(dw.center[0] - max(xs)) > 0.2
    k = 0 if along_x else 1
    return dw, (d.wall_switch[k] - dw.center[k]) > dw.width / 2


@pytest.mark.parametrize("rooms, seed", [(5, 2), (8, 1), (13, 3)])
def test_a_light_s_switch_is_on_the_latch_side_by_the_entrance_and_two_lights_get_two_switches(tmp_path, rooms, seed):
    """An open door's leaf never covers the switch; the hall's switch is by the front door; a room's second light
    has its own switch beside the first (a double plate)."""
    from vesper.lab.geometry import distance
    text = REQUEST.replace("rooms: 5", f"rooms: {rooms}").replace("seed: 2", f"seed: {seed}").replace(
        "      - {category: light, per: room}\n",
        "      - {category: light, per: room}\n      - {category: light, at: living}\n"
        "      - {category: contact_sensor, per: room}\n")
    out = tmp_path / "home"
    generate_home(request(tmp_path, text), catalog(), out)
    home = load_lab_home(out / "home.yaml", catalog())
    lights = [d for d in home.devices if d.record["identity"]["category"] == "light"]
    for d in lights:
        dw, latch = _latch_side(home, d)
        assert latch, (d.name, d.wall_switch, dw.id, dw.center)
        if any(w.outdoor for w in home.map.doorways if d.room in w.rooms):
            assert dw.outdoor, d.name  # the hall's switch by the front door
    living = [d.wall_switch for d in lights if d.room == "living"]
    assert len(living) == 2 and distance(*living) >= 0.1


HOUSE = """\
    format: vesper-request/1
    scene: "102344022"
    seed: 1
    devices:
      - {product: "zigbee:aqara:mccgq11lm", at: front_door}
      - {category: light, per: room}
      - {category: motion_sensor, at: hall}
"""


def fake_export(scene, out):
    """Habitat's export, for tests: the example home's map as the house, no door leaves of its own."""
    import json
    import shutil
    from pathlib import Path
    assert scene == "102344022"
    shutil.copy(Path("examples/lab/test-house/map.json"), Path(out) / "map.json")
    (Path(out) / "openings.json").write_text(json.dumps({"openings": [], "floor": {}}))


def test_a_request_naming_an_hssd_house_places_its_devices_in_that_house_s_rooms(tmp_path):
    out = tmp_path / "home"
    req = request(tmp_path, HOUSE)
    assert req.scene == "102344022"
    generate_home(req, catalog(), out, export=fake_export)
    home = load_lab_home(out / "home.yaml", catalog())
    rooms = {r.id for r in home.map.rooms if not r.outdoor}
    lights = [d for d in home.devices if d.record["identity"]["category"] == "light"]
    assert {d.room for d in lights} == rooms
    (motion,) = [d for d in home.devices if d.record["identity"]["category"] == "motion_sensor"]
    assert motion.room == "hall"  # a house's hallway, where a generated home has its corridor
    text = (out / "home.yaml").read_text()
    assert "HSSD scene 102344022" in text and "synthetic" not in text
    assert (out / "openings.json").is_file()


def test_a_house_request_says_what_is_wrong(tmp_path):
    with pytest.raises(GenerateError, match="scene"):
        request(tmp_path, HOUSE.replace('scene: "102344022"', "scene: [1]"))
    with pytest.raises(GenerateError, match="scene or rooms"):
        request(tmp_path, HOUSE.replace("seed: 1", "seed: 1\n    rooms: 4"))


def test_a_room_type_names_a_house_s_room_that_starts_with_it(tmp_path):
    from pathlib import Path

    def export(scene, out):  # the example home with its kitchen named as HSSD names a dining room
        import json
        text = Path("examples/lab/test-house/map.json").read_text().replace("kitchen", "dining_room")
        (Path(out) / "map.json").write_text(text)
        (Path(out) / "openings.json").write_text(json.dumps({"openings": []}))
    out = tmp_path / "home"
    req = request(tmp_path, HOUSE.replace("{category: motion_sensor, at: hall}", "{category: plug, at: dining}")
                  .replace("{category: light, per: room}", "{category: light, per: room, except: [dining]}"))
    generate_home(req, catalog(), out, export=export)
    home = load_lab_home(out / "home.yaml", catalog())
    (plug,) = [d for d in home.devices if d.record["identity"]["category"] == "plug"]
    assert plug.room == "dining_room"
    assert "dining_room" not in {d.room for d in home.devices if d.record["identity"]["category"] == "light"}


def test_a_position_outside_an_irregular_room_moves_to_the_nearest_spot_inside_it():
    from vesper.lab.generate import _inside
    from vesper.lab.geometry import point_in_polygon
    from vesper.lab.worldmap import Room
    ell = Room("living_room", "living room", ((0, 0), (6, 0), (6, 2), (2, 2), (2, 5), (0, 5)))  # an L
    assert _inside(ell, [1.0, 1.0]) == [1.0, 1.0]  # inside already: unchanged
    moved = _inside(ell, [4.0, 4.0])  # inside the box, outside the L
    assert point_in_polygon(tuple(moved), ell.polygon)
    assert abs(moved[0] - 4.0) + abs(moved[1] - 4.0) < 2.5  # the nearest corner of the L, not its far end
