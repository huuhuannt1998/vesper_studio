import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import HomeError, load_lab_home


def load(tmp_path, text=HOME):
    return load_lab_home(write_home(tmp_path, text), catalog())


def test_a_home_places_real_products_in_the_scene(tmp_path):
    home = load(tmp_path)
    d = {p.name: p for p in home.devices}
    assert home.scene == "test/house" and home.seed == 3 and home.start_time == 7.5 * 3600
    assert d["front_door"].door == "hall-porch" and d["front_door"].mount == "door"
    assert d["front_door"].position == pytest.approx((0.0, 3.0), abs=0.15) and d["front_door"].room == "hall"
    assert d["hall_motion"].room == "hall" and d["hall_motion"].mount == "ceiling"
    assert d["bedroom_lamp"].position == (5.0, 5.0) and d["bedroom_lamp"].record["id"] == "zigbee:acme:dim-1"
    assert d["bedroom_button"].controls == ("bedroom_lamp",)
    assert d["kitchen_climate"].facing == 90.0
    # a wall mount without `facing` faces its room's centre
    assert 0 <= d["bedroom_button"].facing < 90 or d["bedroom_button"].facing > 270


def test_doors_come_from_the_doors_list_and_from_devices_mounted_on_them(tmp_path):
    home = load(tmp_path)
    assert home.doors == {"bedroom-hall": True, "hall-porch": False}  # a door a device sits on starts closed


def test_the_player_starts_on_the_walkable_floor_of_the_named_room(tmp_path):
    home = load(tmp_path)
    x, z = home.avatar["start"]
    assert home.map.room_at(x, z).id == "hall" and home.map.grid.is_walkable(*home.map.grid.cell_of(x, z))
    assert home.avatar["model"] == "female_0"


@pytest.mark.parametrize("change,message", [
    ('"zigbee:acme:pir-1", name: hall_motion', r"devices\[2\]\.product: 'zigbee:acme:pir-9' is not in the catalog; "
                                               r"did you mean zigbee:acme:pir-1"),
    ("{room: hall, mount: ceiling}", r"devices\[2\]\.at\.room: unknown room 'hal'; did you mean hall"),
    ("xy: [5.0, 5.0]", r"devices\[3\]\.at\.xy: \(1.0, 1.0\) is not in room 'bedroom'"),
    ("{door: hall-porch}}\n      - {product: \"matter", r"devices\[0\]\.at\.door: unknown doorway 'kitchen-bedroom'"),
    ("controls: [bedroom_lamp]", r"devices\[4\]\.controls: unknown device 'lamp'; did you mean bedroom_lamp"),
    ("name: hall_motion", r"devices\[2\]\.name: 'front_door' is used twice"),
    ("mount: ceiling}", r"devices\[2\]\.at\.mount: 'roof' is not one of ceiling, wall, table, floor, door"),
    ("name: front_door", r"devices\[0\]\.name: 'bridge/x' cannot be an MQTT topic"),
])
def test_mistakes_name_the_field_and_the_nearest_valid_choice(tmp_path, change, message):
    replacements = {
        '"zigbee:acme:pir-1", name: hall_motion': '"zigbee:acme:pir-9", name: hall_motion',
        "{room: hall, mount: ceiling}": "{room: hal, mount: ceiling}",
        "xy: [5.0, 5.0]": "xy: [1.0, 1.0]",
        "{door: hall-porch}}\n      - {product: \"matter": "{door: kitchen-bedroom}}\n      - {product: \"matter",
        "controls: [bedroom_lamp]": "controls: [lamp]",
        "name: hall_motion": "name: front_door",
        "mount: ceiling}": "mount: roof}",
        "name: front_door": "name: bridge/x",
    }
    text = HOME.replace(change, replacements[change], 1)
    assert text != HOME
    with pytest.raises(HomeError, match=message):
        load(tmp_path, text)


def test_a_version_1_home_file_points_to_vesper_twins_run(tmp_path):
    with pytest.raises(HomeError, match="version 1.*vesper twins run"):
        load(tmp_path, "devices:\n  - {product: 'zigbee:aqara:mccgq11lm', name: door}\n")


def test_a_wall_mount_needs_a_point(tmp_path):
    text = HOME.replace("at: {room: bedroom, xy: [3.2, 3.5], mount: wall}", "at: {room: bedroom, mount: wall}")
    with pytest.raises(HomeError, match=r"devices\[4\]\.at\.xy: a wall mount needs a point"):
        load(tmp_path, text)


def test_a_device_may_start_in_a_given_state_checked_against_the_real_product(tmp_path):
    assert load(tmp_path).device("front_lock").state == {"Door Lock.LockState": "Unlocked"}
    bad = HOME.replace("{\"Door Lock.LockState\": Unlocked}", "{\"Door Lock.LockState\": Ajar}")
    with pytest.raises(HomeError, match=r"devices\[1\]\.state: .*'Ajar' is not one of"):
        load(tmp_path, bad)
    unknown = HOME.replace("{\"Door Lock.LockState\": Unlocked}", "{\"Door Lock.Bolt\": 1}")
    with pytest.raises(HomeError, match=r"devices\[1\]\.state: .*unknown feature 'Door Lock.Bolt'"):
        load(tmp_path, unknown)


@pytest.mark.parametrize("old, new, message", [
    ("xy: [5.0, 5.0]", "xy: [5.0]", r"devices\[3\]\.at\.xy: \[5\.0\] is not a point \[x, z\]"),
    ("xy: [5.0, 5.0]", "xy: '5,5'", r"devices\[3\]\.at\.xy: '5,5' is not a point \[x, z\]"),
    ("facing: 90", "facing: north", r"devices\[5\]\.at\.facing: 'north' is not a number"),
    ("seed: 3", "seed: three", r"seed: 'three' is not a whole number"),
    ("seed: 3", "seed: 3\n    broker: {port: mqtt}", r"broker\.port: 'mqtt' is not a whole number"),
    ("start: {room: hall}", "start: hall", r"avatar\.start: 'hall' is not a mapping"),
    ('{product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}', "zigbee:acme:pir-1",
     r"devices\[2\]: 'zigbee:acme:pir-1' is not a mapping"),
    ("doors:\n      - {doorway: bedroom-hall, open: true}", "doors: {bedroom-hall: true}", r"doors: .* is not a list"),
    ("at: {room: hall, mount: ceiling}", "at: hall", r"devices\[2\]\.at: 'hall' is not a mapping"),
    ("{doorway: bedroom-hall, open: true}", "{door: bedroom-hall}", r"doors\[0\]\.doorway: name a doorway"),
    ('state: {"Door Lock.LockState": Unlocked}', "state: [Unlocked]",
     r"devices\[1\]\.state: \['Unlocked'\] is not a mapping"),
    ('product: "zigbee:acme:pir-1"', 'product: ["zigbee:acme:pir-1"]',
     r"devices\[2\]\.product: \['zigbee:acme:pir-1'\] is not text"),
    ("open: true}", 'open: "false"}', r"doors\[0\]\.open: 'false' is not true or false"),
    ("controls: [bedroom_lamp]", "controls: bedroom_lamp", r"devices\[4\]\.controls: 'bedroom_lamp' is not a list"),
    ('start_time: "07:30"', 'start_time: "25:99"', r"start_time: '25:99' is not HH:MM"),
    ("version: 2", "version: 2\n    devices: [", r"home\.yaml: not valid YAML"),
])
def test_a_malformed_home_file_names_the_field_instead_of_crashing(tmp_path, old, new, message):
    text = HOME.replace(old, new, 1)
    assert text != HOME
    with pytest.raises(HomeError, match=message):
        load(tmp_path, text)


ALEX = """\
version: 1
tasks:
  - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
  - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
"""
WITH_ALEX = HOME + """\
    residents:
      - {name: alex, avatar: male_1, routine: routines/alex.yaml, profile: {forget_light: 0.3}}
"""


def test_residents_come_with_their_routine_and_profile(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX)
    home = load(tmp_path, WITH_ALEX)
    (alex,) = home.residents
    assert (alex.name, alex.model, alex.profile.forget_light) == ("alex", "male_1", 0.3)
    assert [t.name for t in alex.routine.tasks] == ["wake up", "breakfast"]


@pytest.mark.parametrize("change, message", [
    (("routines/alex.yaml", "routines/bob.yaml"), r"residents\[0\]\.routine: .*bob\.yaml: cannot read it"),
    (("name: alex", "name: player"), r"residents\[0\]\.name: 'player' is the player's name"),
    (("name: alex", "name: front_door"), r"residents\[0\]\.name: 'front_door' is a device's name"),
    (("forget_light: 0.3", "forget_light: 3"), r"residents\[0\]\.profile\.forget_light: 3 is not a probability"),
    (("{name: alex, avatar", "{avatar"), r"residents\[0\]\.name: name the resident"),
    (("name: alex", "name: hub"), r"residents\[0\]\.name: 'hub' is the hub's name"),
])
def test_a_resident_s_mistakes_are_named(tmp_path, change, message):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX)
    with pytest.raises(HomeError, match=message):
        load(tmp_path, WITH_ALEX.replace(*change))


def test_a_routine_that_does_not_fit_the_map_is_named_through_the_resident(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX.replace("room: kitchen", "room: attic"))
    with pytest.raises(HomeError, match=r"residents\[0\]\.routine: tasks\[1\]\.room: unknown room 'attic'"):
        load(tmp_path, WITH_ALEX)


def test_two_residents_may_not_share_a_name(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX)
    twice = WITH_ALEX + "      - {name: alex, routine: routines/alex.yaml}\n"
    with pytest.raises(HomeError, match=r"residents\[1\]\.name: 'alex' is used twice"):
        load(tmp_path, twice)


def test_an_away_task_needs_somewhere_outside_to_go(tmp_path):
    from tests.lab.maps import house
    from vesper.lab.worldmap import WorldMap
    m = house()
    indoors = WorldMap(scene=m.scene, rooms=[r for r in m.rooms if not r.outdoor], grid=m.grid)
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX + '  - {at: "08:00", minutes: 60, name: work, '
                                                            'category: AWAY}\n')
    text = """\
        version: 2
        scene: test/house
        avatar: {model: female_0, start: {room: hall}}
        devices:
          - {product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}
        residents:
          - {name: alex, routine: routines/alex.yaml}
    """
    with pytest.raises(HomeError, match=r"residents\[0\]\.routine: tasks\[2\] \(work\) is AWAY, but the map has "
                                        r"no outdoor room to go to"):
        load_lab_home(write_home(tmp_path, text, world_map=indoors), catalog())
