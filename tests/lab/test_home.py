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
