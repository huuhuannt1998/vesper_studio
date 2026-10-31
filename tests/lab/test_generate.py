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
