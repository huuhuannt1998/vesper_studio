"""The standard EDT home (several sensor products per room) and the binder that rewrites a public automation's entity
ids onto its devices by role. Needs the built catalog (catalog/vds) for the real products."""
from pathlib import Path

import pytest
import yaml

from vesper.edt.bind import ROLE_PRIORITY, Unbindable, bind, infer_role
from vesper.edt.home import OUTDOOR_LIGHT, OUTDOOR_PLUG, PLUG, PRODUCTS, build_edt_home

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")

ROOMS = ("living", "bedroom_1", "bedroom_2", "kitchen", "bathroom", "office", "corridor")
SITES = (*ROOMS, "porch")  # the rooms and the outdoor porch at the entrance


@pytest.fixture(scope="module")
def edt(tmp_path_factory):
    from vesper.lab.home import load_lab_home
    from vesper.twins.home import load_catalog
    catalog = load_catalog(Path("catalog/vds"))
    path, roles = build_edt_home(tmp_path_factory.mktemp("edt"), "catalog/vds")
    return path, roles, catalog, load_lab_home(path, catalog)


def test_the_priority_puts_measurements_before_the_things_they_are_named_after():
    assert ROLE_PRIORITY == ("illuminance", "temperature", "humidity", "motion", "door", "light", "switch", "lock",
                             "climate", "cover")


def test_one_l1_product_per_sensor_role_with_its_feature(edt):
    """Every L1 Zigbee product shares one reporting rule per quantity (library defaults), so variants of a sensor
    would be identical rows: the home has one product per sensor role."""
    _, _, catalog, _ = edt
    assert set(PRODUCTS) == {"illuminance", "temperature"} and all(len(p) == 1 for p in PRODUCTS.values())
    for feature, (product,) in PRODUCTS.items():
        assert catalog[product]["fidelity"]["reached"]["L1"], product
        assert feature in {f["name"] for f in catalog[product]["features"]}, product


def test_each_product_reports_under_the_rule_the_runtime_reads(edt):
    """The runtime reads twin.vds["behavior"]["reporting"][feature]: {change, min_s, max_s}."""
    _, _, catalog, _ = edt
    for feature, products in PRODUCTS.items():
        for p in products:
            assert set(catalog[p]["behavior"]["reporting"][feature]) == {"change", "min_s", "max_s"}


def test_the_home_has_one_sensor_per_product_in_every_room(edt):
    path, roles, catalog, home = edt
    devices = yaml.safe_load(path.read_text())["devices"]
    by_name = {d["name"]: d for d in devices}
    for room in SITES:
        d = by_name[f"{room}_lux0"]
        assert d["product"] == PRODUCTS["illuminance"][0] and d["at"]["room"] == room and d["at"]["mount"] == "ceiling"
        assert by_name[f"{room}_temp0"]["product"] == PRODUCTS["temperature"][0]
        assert f"{room}_lux1" not in by_name and f"{room}_temp1" not in by_name
    assert {k for k in roles if k[0] == "illuminance"} == {("illuminance", r, 0) for r in SITES}
    assert roles[("illuminance", "kitchen", 0)].startswith("sensor.") and roles[("humidity", "kitchen", 0)]
    sensors = [v for k, v in roles.items() if k[0] in ("illuminance", "temperature")]
    assert len(set(sensors)) == len(sensors)


def test_the_corridor_has_a_light_and_a_motion_sensor_of_the_starter_products(edt):
    path, roles, _, _ = edt
    by_name = {d["name"]: d for d in yaml.safe_load(path.read_text())["devices"]}
    for kind in ("light", "motion"):
        assert by_name[f"corridor_{kind}"]["product"] == by_name[f"kitchen_{kind}"]["product"]
        assert by_name[f"corridor_{kind}"]["at"]["room"] == "corridor"
        assert by_name[f"corridor_{kind}"]["at"]["mount"] == "ceiling"
    assert roles[("light", "corridor", 0)].startswith("light.")
    assert roles[("motion", "corridor", 0)].startswith("binary_sensor.")


def test_hallway_light_and_motion_bind(edt):
    _, roles, _, _ = edt
    raw = {"alias": "h", "triggers": [{"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"}],
           "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.hallway"}}]}
    bound = bind(raw, roles, 0)
    assert bound["triggers"][0]["entity_id"] == roles[("motion", "corridor", 0)]
    assert bound["actions"][0]["target"]["entity_id"] == roles[("light", "corridor", 0)]


def test_a_sensor_variant_that_does_not_exist_is_unbindable(edt):
    _, roles, _, _ = edt
    raw = {"alias": "k", "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.kitchen_illuminance",
                                       "below": 50}], "actions": []}
    r = bind(raw, roles, 1)
    assert isinstance(r, Unbindable) and r.reason.startswith("no variant 1 for illuminance")
    assert isinstance(bind(raw, roles, 0), dict)


def test_the_starter_devices_fill_the_roles_without_product_variants(edt):
    _, roles, _, _ = edt
    assert roles[("light", "kitchen", 0)].startswith("light.") and roles[("motion", "office", 0)].startswith(
        "binary_sensor.")
    assert roles[("door", "living", 0)].startswith("binary_sensor.")
    assert roles[("lock", "living", 0)].startswith("lock.")
    assert roles[("climate", "bedroom_1", 0)].startswith("climate.")
    assert not any(k[0] == "cover" for k in roles)


def test_the_porch_has_outdoor_sensors_an_outdoor_light_and_an_outdoor_plug(edt):
    """The porch (the outdoor room at the entrance) answers the outdoor entities: its sensors read the outdoor air and
    light (vesper.lab.rules drives a device outdoors with them)."""
    path, roles, catalog, _ = edt
    by_name = {d["name"]: d for d in yaml.safe_load(path.read_text())["devices"]}
    assert by_name["porch_light"]["product"] == OUTDOOR_LIGHT and by_name["porch_plug"]["product"] == OUTDOOR_PLUG
    assert "outdoor" in catalog[OUTDOOR_LIGHT]["identity"]["description"].lower()
    assert "outdoor" in catalog[OUTDOOR_PLUG]["identity"]["description"].lower()
    assert all(by_name[n]["at"]["room"] == "porch" for n in ("porch_lux0", "porch_temp0", "porch_light", "porch_plug"))
    for role, domain in (("illuminance", "sensor."), ("temperature", "sensor."), ("humidity", "sensor."),
                         ("motion", "binary_sensor."), ("light", "light."), ("switch", "switch."),
                         ("door", "binary_sensor."), ("lock", "lock.")):
        assert roles[(role, "porch", 0)].startswith(domain), role
    assert roles[("door", "porch", 0)] == roles[("door", "living", 0)]  # the front door opens onto the porch
    assert ("climate", "porch", 0) not in roles and ("cover", "porch", 0) not in roles


def test_every_lit_room_has_three_lights_and_three_plugs_the_porch_two_of_each(edt):
    """More than one light or plug per room, so an automation naming several binds them to several devices."""
    path, roles, catalog, _ = edt
    devices = yaml.safe_load(path.read_text())["devices"]
    lit = {d["at"]["room"] for d in devices if catalog[d["product"]]["identity"]["category"] == "light"}
    assert lit == set(SITES)
    for room in lit:
        n = 2 if room == "porch" else 3
        for kind, role in (("light", "light"), ("plug", "switch")):
            names = [f"{room}_{kind}"] + [f"{room}_{kind}_{i}" for i in range(2, n + 1)]
            mine = [d for d in devices if d["name"] in names]
            assert sorted(d["name"] for d in mine) == sorted(names), (room, kind)
            assert len({d["product"] for d in mine}) == 1 and all(d["at"]["room"] == room for d in mine)
            spots = [(d["at"].get("xy") and tuple(d["at"]["xy"])) for d in mine]
            assert len(set(spots)) == n, (room, kind, spots)  # spread out, never on one spot
            assert [k for k in roles if k[:2] == (role, room)] == [(role, room, i) for i in range(n)]
            assert len({roles[(role, room, i)] for i in range(n)}) == n
    assert all(d["product"] == PLUG for d in devices if d["name"].startswith("kitchen_plug"))
    assert catalog[PLUG]["identity"]["category"] == "plug" and catalog[PLUG]["fidelity"]["reached"]["L1"]
    assert all(d["product"] == OUTDOOR_LIGHT for d in devices if d["name"].startswith("porch_light"))


def test_lab_check_finds_no_placement_or_range_problem(edt, capsys):
    path, _, _, _ = edt
    from vesper import cli
    assert cli.main(["lab", "check", str(path), "--catalog", "catalog/vds"]) == 0
    out = capsys.readouterr().out
    assert "devices" in out and "network:" not in out, out


def test_a_room_without_a_motion_sensor_uses_its_illuminance_sensors_occupancy(edt):
    """The illuminance product is a motion sensor with a light sensor: the bathroom (no starter motion sensor) and the
    porch answer motion with it."""
    _, roles, _, _ = edt
    assert roles[("motion", "bathroom", 0)].startswith("binary_sensor.")
    assert roles[("motion", "bathroom", 0)].endswith("_occupancy")
    assert roles[("motion", "kitchen", 0)] != roles[("motion", "bathroom", 0)]


@pytest.mark.parametrize("entity_id,expected", [
    ("sensor.hallway_lux", ("illuminance", "corridor")),
    ("binary_sensor.front_door", ("door", "living")),
    ("sensor.xyz_1", None),
    ("sensor.hallway_light", ("illuminance", "corridor")),
    ("sensor.living_room_light_power", None),
    ("sensor.kitchen_light_energy", None),
    ("sensor.kitchen_temperature_battery", None),
    ("sensor.office_lux_linkquality", None),
    ("sensor.office_humidity_state", None),
    ("binary_sensor.kitchen_motion_battery_low", None),
    ("binary_sensor.kitchen_window_battery", None),
    ("binary_sensor.kitchen_motion", ("motion", "kitchen")),
    ("binary_sensor.backyard_motion", ("motion", "porch")),
    ("light.courtyard", ("light", "porch")),
    ("light.patio", ("light", "porch")),
    ("light.driveway", ("light", "porch")),
    ("light.terrace", ("light", "porch")),
    ("light.deck", ("light", "porch")),
    ("light.carport", ("light", "porch")),
    ("light.shed", ("light", "porch")),
    ("switch.pool_pump", ("switch", "porch")),
    ("light.veranda", ("light", "porch")),
    ("sensor.aussen_station_helligkeit", ("illuminance", "porch")),
    ("sensor.patio_luminance_hue", ("illuminance", "porch")),
    ("sensor.outside_light_sensor_illuminance_lux", ("illuminance", "porch")),
    ("sensor.openweathermap_temperature", ("temperature", "porch")),
    ("sensor.dark_sky_humidity", ("humidity", "porch")),
    ("sensor.weather_temperature", ("temperature", "porch")),
    ("sensor.openweathermap_condition", None),
    ("sensor.stadt_temperatur", ("temperature", "living")),
    ("sensor.wohnzimmer_luftfeuchtigkeit", ("humidity", "living")),
    ("sensor.current_temperature", ("temperature", "living")),
    ("sensor.heating_current_temperature", ("temperature", "living")),
    ("sensor.kitchen_current", None),
    ("sensor.stadt_absolute_luftfeuchtigkeit", None),
    ("sensor.terrace_dew_point", None),
    ("sensor.garden_soil_moisture", None),
    ("sensor.time", None),
    ("sensor.sun_next_rising", None),
    ("sensor.sun_room_temperature", ("temperature", "living")),
    ("light.deckenlampe_keller", ("light", "living")),
    ("light.og_flur_decke", ("light", "corridor")),  # Flur: a hallway; Decke: a ceiling
    ("light.deck", ("light", "porch")),
    ("sensor.dark_sky_daily_high_temperature_0d", None),
    ("sensor.dark_sky_overnight_low_temperature_0d", None),
    ("sensor.weather_temperature_max", None),
    ("sensor.owm_temperature_tomorrow", None),
    ("sensor.weatherbit_temperature_low_1d", None),
    ("sensor.buienradar_temperature_1d", None),
    ("sensor.accuweather_realfeel_temperature", None),
    ("sensor.outdoor_wind_chill_temperature", None),
    ("sensor.temp_outside_lowest_temperature", None),
    ("sensor.kitchen_temperature_trend", None),
    ("sensor.printer_nozzle_temperature", None),
    ("sensor.octoprint_tool0_temperature", None),
    ("sensor.cpu_temperature", None),
    ("sensor.nvme_temperature", None),
    ("sensor.aquarium_temperature", None),
    ("sensor.fridge_temperature", None),
    ("sensor.boiler_temperature", None),
    ("sensor.hot_water_temperature", None),
    ("sensor.bedroom_2_temperature", ("temperature", "bedroom_2")),
    ("sensor.bedroom2_temp", ("temperature", "bedroom_2")),
    ("sensor.bedroom_temperature", ("temperature", "bedroom_1")),
    ("sensor.temp_lux", ("illuminance", "living")),
    ("sensor.kitchen_temperature", ("temperature", "kitchen")),
    ("sensor.bathroom_humidity", ("humidity", "bathroom")),
    ("binary_sensor.office_motion", ("motion", "office")),
    ("binary_sensor.study_occupancy", ("motion", "office")),
    ("light.lounge", ("light", "living")),
    ("light.bedroom_lamp", ("light", "bedroom_1")),
    ("switch.coffee", ("switch", "living")),
    ("lock.front_door", ("lock", "living")),
    ("climate.hall", ("climate", "corridor")),
    ("cover.bath_blind", ("cover", "bathroom")),
    ("weather.home", None),
])
def test_infer_role(entity_id, expected):
    assert infer_role(entity_id) == expected


KITCHEN = {"alias": "kitchen lamp", "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.kitchen_illuminance",
                                                  "below": 50}],
           "conditions": [{"condition": "state", "entity_id": "binary_sensor.front_door", "state": "off"}],
           "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
                       {"action": "notify.me", "data": {"message": "{{ states('sensor.kitchen_illuminance') }}"}}]}


def test_a_kitchen_automation_binds_to_the_kitchen_sensor_and_the_kitchen_light(edt):
    _, roles, _, home = edt
    from vesper.lab.automations import Engine
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    bound = bind(KITCHEN, roles, 0)
    assert bound["triggers"][0]["entity_id"] == roles[("illuminance", "kitchen", 0)]
    assert bound["actions"][0]["target"]["entity_id"] == roles[("light", "kitchen", 0)]
    assert bound["conditions"][0]["entity_id"] == roles[("door", "living", 0)]
    assert bound["actions"][0]["action"] == "light.turn_on"  # a service name is not an entity id
    assert roles[("illuminance", "kitchen", 0)] in bound["actions"][1]["data"]["message"]
    assert KITCHEN["triggers"][0]["entity_id"] == "sensor.kitchen_illuminance"  # the input is untouched
    engine = Engine(Session(home, clock=LogicalClock()), permissive=True, automations=[bound])
    assert engine.refused == [] and len(engine.automations) == 1


def test_lists_of_entities_and_sun_pass_through(edt):
    _, roles, _, _ = edt
    raw = {"alias": "x", "triggers": [{"trigger": "state", "entity_id": ["light.kitchen", "light.office"]},
                                      {"trigger": "state", "entity_id": "sun.sun", "to": "below_horizon"}],
           "actions": []}
    bound = bind(raw, roles, 0)
    assert bound["triggers"][0]["entity_id"] == [roles[("light", "kitchen", 0)], roles[("light", "office", 0)]]
    assert bound["triggers"][1]["entity_id"] == "sun.sun"


def test_weather_cannot_be_bound(edt):
    _, roles, _, _ = edt
    raw = {"alias": "w", "triggers": [{"trigger": "state", "entity_id": "weather.home"}], "actions": []}
    assert bind(raw, roles, 0) == Unbindable("weather entities are not modelled")


def test_a_role_the_home_lacks_is_named(edt):
    _, roles, _, _ = edt
    raw = {"alias": "w", "triggers": [{"trigger": "state", "entity_id": "cover.kitchen_blind"}], "actions": []}
    r = bind(raw, roles, 0)
    assert isinstance(r, Unbindable) and "cover" in r.reason and "kitchen" in r.reason
    raw = {"alias": "w", "triggers": [{"trigger": "state", "entity_id": "input_boolean.guests"}], "actions": []}
    assert isinstance(bind(raw, roles, 0), Unbindable)
    raw = {"alias": "w", "triggers": [{"trigger": "state", "entity_id": "sensor.xyz_1"}], "actions": []}
    assert isinstance(bind(raw, roles, 0), Unbindable)


def one(entity_id, **rest):
    return {"alias": "t", "triggers": [{"trigger": "state", "entity_id": entity_id, **rest}], "actions": []}


def test_a_comma_separated_entity_id_string_is_bound_one_by_one(edt):
    _, roles, _, _ = edt
    bound = bind(one("light.kitchen, light.office"), roles, 0)
    assert bound["triggers"][0]["entity_id"] == f"{roles[('light', 'kitchen', 0)]}, {roles[('light', 'office', 0)]}"
    assert isinstance(bind(one("light.kitchen, weather.home"), roles, 0), Unbindable)


def test_an_entity_id_string_that_is_no_entity_is_unbindable_but_all_none_and_templates_pass(edt):
    _, roles, _, _ = edt
    assert isinstance(bind(one("kitchen lamp"), roles, 0), Unbindable)
    assert bind(one("all"), roles, 0)["triggers"][0]["entity_id"] == "all"
    assert bind(one("none"), roles, 0)["triggers"][0]["entity_id"] == "none"
    assert bind(one("{{ 'sun.sun' }}"), roles, 0)["triggers"][0]["entity_id"] == "{{ 'sun.sun' }}"


def test_unquoted_template_access_to_states_is_rewritten(edt):
    _, roles, _, _ = edt
    raw = one("sun.sun", value_template="{{ states.light.kitchen.state == 'on' and states.sensor.hall_lux.state }}")
    text = bind(raw, roles, 0)["triggers"][0]["value_template"]
    assert f"states.{roles[('light', 'kitchen', 0)]}.state" in text
    assert f"states.{roles[('illuminance', 'corridor', 0)]}.state" in text
    assert "light.kitchen" not in text
    assert isinstance(bind(one("sun.sun", value_template="{{ states.weather.home.state }}"), roles, 0), Unbindable)


DEV = "2d64bb7cb97f1dc111fa719568f708a2"  # a device registry id: opaque
REG = "625f8971694496a43da3bd153b1c235f"  # an entity registry id: opaque


def act(**step):
    return {"alias": "a", "triggers": [{"trigger": "time", "at": "07:00"}], "actions": [step]}


def test_an_area_target_binds_to_the_rooms_device_of_the_actions_domain(edt):
    _, roles, _, _ = edt
    def every(role, room):  # Home Assistant expands an area to every entity of the domain in it
        return [roles[(role, room, i)] for i in range(2 if room == "porch" else 3)]

    bound = bind(act(action="light.turn_on", target={"area_id": "kitchen"}), roles, 0)
    assert bound["actions"][0]["target"] == {"entity_id": every("light", "kitchen")}
    bound = bind(act(service="switch.turn_off", target={"area_id": ["bathroom", "living_room"]}), roles, 0)
    assert bound["actions"][0]["target"] == {"entity_id": every("switch", "bathroom") + every("switch", "living")}
    bound = bind(act(action="light.turn_on", target={"area_id": "outdoors", "entity_id": "light.office"}), roles, 0)
    assert bound["actions"][0]["target"] == {"entity_id": [roles[("light", "office", 0)], *every("light", "porch")]}
    bound = bind(act(action="light.turn_off", area_id="hallway"), roles, 0)  # the old style, beside the action
    assert bound["actions"][0] == {"action": "light.turn_off", "target": {"entity_id": every("light", "corridor")}}


def test_an_area_the_home_has_no_room_for_or_an_unmodelled_area_action_is_named(edt):
    _, roles, _, _ = edt
    r = bind(act(action="light.turn_on", target={"area_id": "dining_room"}), roles, 0)
    assert r == Unbindable("no room of the standard home for the area (for dining_room)")
    from vesper.edt.sweep import reason_class
    assert reason_class(r.reason) == "no room of the standard home for the area"
    r = bind(act(action="homeassistant.turn_off", target={"area_id": "kitchen"}), roles, 0)
    assert r == Unbindable("area targets of homeassistant actions are not modelled")
    r = bind(act(action="cover.close_cover", target={"area_id": "kitchen"}), roles, 0)
    assert r == Unbindable("the standard home has no cover in the kitchen (for area kitchen)")


def test_empty_handles_are_dropped_and_a_device_only_target_is_named(edt):
    _, roles, _, _ = edt
    bound = bind(act(action="light.turn_on", target={"area_id": [], "device_id": [], "entity_id": ["light.office"]}),
                 roles, 0)
    assert bound["actions"][0]["target"] == {"entity_id": [roles[("light", "office", 0)]]}
    assert bind(act(action="light.turn_on", target={"device_id": DEV}), roles, 0) == Unbindable(
        "device_id targets name no entity")
    bound = bind(act(action="switch.turn_on", target={"device_id": [DEV], "entity_id": "switch.plug13"}), roles, 0)
    assert bound["actions"][0]["target"] == {"entity_id": roles[("switch", "living", 0)]}


def test_device_triggers_conditions_and_actions_bind_through_their_entity_or_role(edt):
    _, roles, _, home = edt
    from vesper.lab.automations import Engine
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    raw = {"alias": "dev", "triggers": [
        {"trigger": "device", "device_id": DEV, "domain": "binary_sensor", "entity_id": REG, "type": "no_motion",
         "id": "clear"},
        {"platform": "device", "device_id": DEV, "domain": "sensor", "entity_id": "sensor.child_temperature",
         "type": "temperature", "below": 23, "for": {"minutes": 2}},
        {"trigger": "device", "device_id": DEV, "domain": "sensor", "entity_id": "sensor.office_lux",
         "type": "illuminance", "above": 300}],
        "conditions": [{"condition": "device", "device_id": DEV, "domain": "light", "entity_id": REG, "type": "is_on"},
                       {"condition": "device", "device_id": DEV, "domain": "sensor",
                        "entity_id": "sensor.kitchen_humidity", "type": "is_humidity", "below": 60}],
        "actions": [{"device_id": DEV, "domain": "light", "entity_id": "light.kitchen_ceiling", "type": "turn_on",
                     "brightness_pct": 40},
                    {"device_id": DEV, "domain": "switch", "entity_id": REG, "type": "toggle"},
                    {"device_id": DEV, "domain": "climate", "entity_id": "climate.thermostat", "type": "set_hvac_mode",
                     "hvac_mode": "heat"}]}
    b = bind(raw, roles, 0)
    assert b["triggers"] == [
        {"trigger": "state", "entity_id": roles[("motion", "living", 0)], "to": "off", "id": "clear"},
        {"platform": "numeric_state", "entity_id": roles[("temperature", "living", 0)], "below": 23,
         "for": {"minutes": 2}},
        {"trigger": "numeric_state", "entity_id": roles[("illuminance", "office", 0)], "above": 300}]
    assert b["conditions"] == [
        {"condition": "state", "entity_id": roles[("light", "living", 0)], "state": "on"},
        {"condition": "numeric_state", "entity_id": roles[("humidity", "kitchen", 0)], "below": 60}]
    assert b["actions"] == [
        {"action": "light.turn_on", "target": {"entity_id": roles[("light", "kitchen", 0)]},
         "data": {"brightness_pct": 40}},
        {"action": "switch.toggle", "target": {"entity_id": roles[("switch", "living", 0)]}},
        {"action": "climate.set_hvac_mode", "target": {"entity_id": roles[("climate", "living", 0)]},
         "data": {"hvac_mode": "heat"}}]
    engine = Engine(Session(home, clock=LogicalClock()), permissive=True, automations=[b])
    assert engine.refused == [] and len(engine.automations) == 1


@pytest.mark.parametrize("step,reason", [
    ({"trigger": "device", "device_id": DEV, "domain": "mqtt", "type": "action", "subtype": "scene_1"},
     "device triggers of remotes are not modelled"),
    ({"trigger": "device", "device_id": DEV, "domain": "zha", "type": "remote_button_short_press",
      "subtype": "turn_on"}, "device triggers of remotes are not modelled"),
    ({"trigger": "device", "device_id": DEV, "domain": "media_player", "entity_id": REG, "type": "turned_off"},
     "media_player entities are not modelled"),
    ({"trigger": "device", "device_id": DEV, "domain": "cover", "entity_id": "cover.wohnzimmer", "type": "position",
      "above": 25}, "cover device triggers are not modelled (for position)"),
    ({"trigger": "device", "device_id": DEV, "domain": "sensor", "entity_id": REG, "type": "value", "above": 3},
     "sensor device triggers are not modelled (for value)"),
    ({"trigger": "device", "device_id": DEV}, "device triggers of remotes are not modelled"),
])
def test_device_steps_the_home_cannot_answer_are_named(edt, step, reason):
    _, roles, _, _ = edt
    assert bind({"alias": "d", "triggers": [step], "actions": []}, roles, 0) == Unbindable(reason)


def test_a_handle_outside_a_target_or_device_step_is_still_refused(edt):
    _, roles, _, _ = edt
    raw = {"alias": "x", "triggers": [{"trigger": "state", "entity_id": "light.kitchen"}],
           "actions": [{"action": "notify.me", "data": {"device_id": DEV, "message": "hi"}}]}
    assert bind(raw, roles, 0) == Unbindable("device_id/area_id not modelled")


def test_an_entity_in_above_or_below_is_bound_like_any_other(edt):
    _, roles, _, _ = edt
    raw = one("sensor.kitchen_temperature", above="sensor.bedroom_temperature", below=30)
    t = bind(raw, roles, 0)["triggers"][0]
    assert t["above"] == roles[("temperature", "bedroom_1", 0)] and t["below"] == 30
    assert isinstance(bind(one("sensor.kitchen_temperature", below="sensor.xyz_1"), roles, 0), Unbindable)


@pytest.mark.parametrize("template", ["{{ is_state('person.me', 'home') }}", "{{ states('group.lamps') }}",
                                      "{{ is_state('input_boolean.guests', 'on') }}"])
def test_a_quoted_id_of_an_unmodelled_domain_in_a_template_is_unbindable(edt, template):
    _, roles, _, _ = edt
    r = bind(one("sun.sun", value_template=template), roles, 0)
    assert isinstance(r, Unbindable) and template.split("'")[1].partition(".")[0] in r.reason


def test_sun_and_service_names_in_templates_are_left_alone(edt):
    _, roles, _, _ = edt
    t = "{{ is_state('sun.sun', 'below_horizon') and 'light.turn_on' in x }}"
    assert bind(one("sun.sun", value_template=t), roles, 0)["triggers"][0]["value_template"] == t


@pytest.mark.parametrize("entity_id,role", [
    ("sensor.outdoor_temperature", "temperature"), ("sensor.outside_humidity", "humidity"), ("light.garden", "light"),
    ("binary_sensor.patio_door", "door"), ("sensor.porch_lux", "illuminance"), ("binary_sensor.yard_motion", "motion"),
    ("light.balcony", "light"), ("switch.garden_lights", "switch"), ("lock.patio_door", "lock")])
def test_outdoor_entities_bind_to_the_porch(edt, entity_id, role):
    _, roles, _, _ = edt
    assert bind(one(entity_id), roles, 0)["triggers"][0]["entity_id"] == roles[(role, "porch", 0)]
    assert infer_role(entity_id) == (role, "porch")


@pytest.mark.parametrize("entity_id,role", [("cover.garage_door", "cover"), ("climate.garage", "climate")])
def test_an_outdoor_actuator_the_porch_lacks_is_named(edt, entity_id, role):
    _, roles, _, _ = edt
    reason = f"the standard home has no {role} in the porch (for {entity_id})"
    assert bind(one(entity_id), roles, 0) == Unbindable(reason)


@pytest.mark.parametrize("entity_id,reason", [
    ("sensor.time", "time sensors are not modelled; use time triggers"),
    ("sensor.date", "time sensors are not modelled; use time triggers"),
    ("sensor.date_time", "time sensors are not modelled; use time triggers"),
    ("sensor.sun_next_rising", "sun sensors are not modelled; use sun.sun"),
    ("sensor.sun_solar_elevation", "sun sensors are not modelled; use sun.sun"),
    ("sensor.sun_elevation", "sun sensors are not modelled; use sun.sun"),
    ("sensor.openweathermap_condition", "weather service sensors other than temperature, humidity and illuminance "
                                        "are not modelled"),
    ("sensor.dark_sky_wind_speed", "weather service sensors other than temperature, humidity and illuminance "
                                   "are not modelled"),
])
def test_time_sun_and_weather_service_sensors_have_their_own_reasons(edt, entity_id, reason):
    _, roles, _, _ = edt
    assert bind(one(entity_id), roles, 0) == Unbindable(reason)


def test_an_entity_id_in_variables_or_data_is_bound(edt):
    _, roles, _, _ = edt
    raw = {"alias": "v", "variables": {"lamp": "light.kitchen", "n": 3, "text": "hello.world"}, "triggers": [],
           "actions": [{"action": "light.turn_on", "data": {"entity": "light.office", "brightness": 10}},
                       {"action": "light.turn_on", "target": {"entity_id": "{{ lamp }}"}}]}
    bound = bind(raw, roles, 0)
    assert bound["variables"] == {"lamp": roles[("light", "kitchen", 0)], "n": 3, "text": "hello.world"}
    assert bound["actions"][0]["data"]["entity"] == roles[("light", "office", 0)]
    assert bound["actions"][0]["action"] == "light.turn_on" and bound["actions"][1]["action"] == "light.turn_on"


def test_service_keys_and_sun_are_never_entities(edt):
    _, roles, _, _ = edt
    raw = {"alias": "s", "triggers": [], "actions": [{"service": "light.toggle"}, {"action": "script.turn_on"},
                                                       {"action": "x", "data": {"body": "sun.sun"}}]}
    assert bind(raw, roles, 0) == raw


@pytest.mark.parametrize("value", ["person.me", "cover.garage_door", "weather.home", "cover.kitchen_blind",
                                   "sensor.xyz_1"])
def test_an_unbindable_entity_id_anywhere_makes_the_automation_unbindable(edt, value):
    _, roles, _, _ = edt
    raw = {"alias": "v", "variables": {"x": value}, "triggers": [], "actions": []}
    assert isinstance(bind(raw, roles, 0), Unbindable)


def test_a_blueprint_use_is_not_expanded(edt):
    _, roles, _, _ = edt
    raw = {"alias": "b", "use_blueprint": {"path": "motion_light.yaml", "input": {"motion_entity": "light.kitchen"}}}
    assert bind(raw, roles, 0) == Unbindable("blueprints are not expanded")


@pytest.mark.parametrize("domain", ["alert", "proximity", "air_quality", "geo_location", "plant", "conversation",
                                    "persistent_notification", "assist_satellite", "image_processing", "stt"])
def test_an_unmodelled_domain_is_refused_in_variables_and_templates(domain, edt):
    roles = edt[1]
    for raw in ({"triggers": [{"trigger": "time", "at": "07:00"}], "variables": {"x": f"{domain}.front"},
                 "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]},
                {"triggers": [{"trigger": "template", "value_template": f"{{{{ is_state('{domain}.front', 'on') }}}}"}],
                 "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]}):
        assert isinstance(bind(raw, roles, 0), Unbindable), (domain, raw)


def test_a_mixed_case_entity_id_is_lowercased_as_home_assistant_does(edt):
    """Home Assistant's config validation lowercases an entity id (cv.entity_id)."""
    _, roles, _, _ = edt
    assert bind(one("switch.Kitchen_Plug"), roles, 0)["triggers"][0]["entity_id"] == roles[("switch", "kitchen", 0)]
    assert bind(one(["light.Office", "light.kitchen"]), roles, 0)["triggers"][0]["entity_id"] == [
        roles[("light", "office", 0)], roles[("light", "kitchen", 0)]]


@pytest.mark.parametrize("value,reason", [
    ("!input motion_entity", "blueprint inputs are not expanded"),
    ("!include ../devices.yaml", "YAML tags are not resolved (for !include)"),
    ("!secret lamp", "YAML tags are not resolved (for !secret)"),
])
def test_an_unresolved_tag_under_entity_id_has_its_own_reason(edt, value, reason):
    _, roles, _, _ = edt
    assert bind(one(value), roles, 0) == Unbindable(reason)


# --- gate-open helpers (protocol addendum 2) ------------------------------------------------------------------------

LUX = {"condition": "numeric_state", "entity_id": "sensor.kitchen_illuminance", "below": 50}
LAMP = [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]


def gated(*conditions, triggers=None, actions=None, key="conditions"):
    return {"alias": "g", "triggers": triggers or [{"trigger": "time", "at": "19:00:00"}],
            key: list(conditions), "actions": actions or LAMP}


def state(entity_id, value, **rest):
    return {"condition": "state", "entity_id": entity_id, "state": value, **rest}


def numeric(entity_id, **rest):
    return {"condition": "numeric_state", "entity_id": entity_id, **rest}


def test_a_helper_gate_is_removed_and_recorded_as_an_assumption(edt):
    _, roles, _, home = edt
    from vesper.edt.bind import bind_with_assumptions
    from vesper.lab.automations import Engine
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    raw = gated(state("input_boolean.away_mode", "off"), LUX)
    bound, assumptions = bind_with_assumptions(raw, roles, 0)
    assert assumptions == ["input_boolean.away_mode == off"]
    assert bound["conditions"] == [{**LUX, "entity_id": roles[("illuminance", "kitchen", 0)]}]
    assert bind(raw, roles, 0) == bound and "assumptions" not in bound  # the engine refuses unknown top-level keys
    engine = Engine(Session(home, clock=LogicalClock()), permissive=True, automations=[bound])
    assert engine.refused == [] and len(engine.automations) == 1
    assert bind_with_assumptions(gated(LUX), roles, 0)[1] == []


@pytest.mark.parametrize("conditions,expected", [
    ([state("group.family", "home")], ["group.family == home"]),
    ([state("person.me", ["home", "work"])], ["person.me in [home, work]"]),
    ([state("person.me", "gps", attribute="source")], ["person.me[source] == gps"]),
    ([numeric("input_number.t", above=18, below=22)], ["input_number.t in (18, 22)"]),
    ([numeric("input_number.t", above=18)], ["input_number.t > 18"]),
    ([numeric("input_number.t", below=22.5)], ["input_number.t < 22.5"]),
    ([state("input_boolean.a, input_select.b", "on")], ["input_boolean.a == on", "input_select.b == on"]),
    ([{"condition": "and", "conditions": [state("zone.home", "1"), LUX]}], ["zone.home == 1"]),
    ([{"and": [state("timer.t", "idle"), LUX]}], ["timer.t == idle"]),
    ([[state("device_tracker.phone", "home"), LUX]], ["device_tracker.phone == home"]),
    ([state("input_select.mode", ["home", "guest"]), state("input_select.mode", "home")],
     ["input_select.mode == home"]),
    ([numeric("input_number.t", above=18), numeric("input_number.t", below=22)], ["input_number.t in (18, 22)"]),
    ([state("input_boolean.a", "on", enabled=False)], []),
    ([state("input_boolean.a", "on", **{"for": "00:05:00"})], ["input_boolean.a == on"]),
])
def test_gate_shapes_and_their_assumptions(edt, conditions, expected):
    _, roles, _, _ = edt
    from vesper.edt.bind import bind_with_assumptions
    bound, assumptions = bind_with_assumptions(gated(*conditions), roles, 0)
    assert assumptions == expected
    assert "input_" not in str(bound) and "zone." not in str(bound) and "person." not in str(bound)


def test_an_and_left_empty_disappears_and_a_single_condition_form_works(edt):
    _, roles, _, _ = edt
    from vesper.edt.bind import bind_with_assumptions
    bound, a = bind_with_assumptions(gated({"condition": "and", "conditions": [state("input_boolean.a", "on")]}),
                                     roles, 0)
    assert bound["conditions"] == [] and a == ["input_boolean.a == on"]
    raw = {"alias": "g", "trigger": {"platform": "sun", "event": "sunset"},
           "condition": state("input_boolean.a", "on"), "action": LAMP}
    bound, a = bind_with_assumptions(raw, roles, 0)
    assert bound["condition"] == [] and a == ["input_boolean.a == on"]


@pytest.mark.parametrize("raw,reason", [
    (gated(LUX, triggers=[{"trigger": "state", "entity_id": "input_boolean.guest", "to": "on"}]),
     "input_boolean in a trigger (helpers and presence change only with residents)"),
    (gated(LUX, triggers=[{"platform": "zone", "entity_id": "person.me", "zone": "zone.home", "event": "enter"}]),
     "person in a trigger (helpers and presence change only with residents)"),
    (gated(numeric("sensor.kitchen_temperature", above="input_number.t")),
     "input_number as a numeric threshold"),
    (gated(triggers=[{"trigger": "numeric_state", "entity_id": "sensor.kitchen_temperature",
                      "below": "input_number.t"}]), "input_number as a numeric threshold"),
    (gated({"condition": "or", "conditions": [state("input_boolean.a", "on"), LUX]}), "input_boolean inside or/not"),
    (gated({"not": [state("group.family", "home")]}), "group inside or/not"),
    (gated(state("input_boolean.a, input_boolean.b", "on", match="any")), "input_boolean inside or/not"),
    (gated(state("input_boolean.a", "on"), state("input_boolean.a", "off")),
     "conflicting conditions on one helper (for input_boolean.a)"),
    (gated(numeric("input_number.t", above=22), numeric("input_number.t", below=18)),
     "conflicting conditions on one helper (for input_number.t)"),
    (gated({"condition": "template", "value_template": "{{ is_state('input_boolean.a', 'on') }}"}),
     "input_boolean in a template or an action"),
    (gated(LUX, actions=[{"action": "notify.me", "data": {"message": "{{ states('person.me') }}"}}]),
     "person in a template or an action"),
    (gated({"condition": "zone", "entity_id": "person.me", "zone": "zone.home"}), "person in a zone condition"),
    (gated(state("input_boolean.a, light.kitchen", "on")), "input_boolean and a device in one condition"),
    (gated(state("input_boolean.a", True)), "a helper state given as unquoted on/off (Home Assistant refuses it)"),
    (gated(numeric("input_number.t", above="sensor.kitchen_temperature")),
     "input_number compared against an entity or a template"),
])
def test_helpers_anywhere_but_a_gate_stay_unbindable(edt, raw, reason):
    _, roles, _, _ = edt
    assert bind(raw, roles, 0) == Unbindable(reason)


# --- helpers only set by an action (protocol A2.4) ------------------------------------------------------------------

@pytest.mark.parametrize("action", [
    {"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.flag"}},
    {"service": "input_boolean.turn_off", "entity_id": "input_boolean.flag"},
    {"service": "input_select.select_option", "data": {"entity_id": "input_select.mode", "option": "night"}},
    {"action": "input_number.set_value", "target": {"entity_id": ["input_number.t"]}, "data": {"value": 3}},
    {"action": "timer.start", "target": {"entity_id": "timer.t"}},
    {"action": "counter.increment", "entity_id": "counter.c"},
    {"action": "input_text.set_value", "target": {"entity_id": "input_text.note"}, "data": {"value": "dusk"}},
    {"action": "input_datetime.set_datetime", "target": {"entity_id": "input_datetime.last"},
     "data": {"time": "{{ now().strftime('%H:%M:%S') }}"}},
    {"action": "input_button.press", "target": {"entity_id": "input_button.b"}},
])
def test_a_helper_only_set_by_an_action_passes_through_and_the_engine_records_it(edt, action):
    _, roles, _, home = edt
    from vesper.edt.bind import bind_with_assumptions
    from vesper.lab.automations import Engine
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    raw = gated(LUX, actions=[*LAMP, action])
    bound, assumptions = bind_with_assumptions(raw, roles, 0)
    assert assumptions == []  # nothing in the run depends on it
    assert bound["actions"][1] == action
    assert bound["actions"][0]["target"]["entity_id"] == roles[("light", "kitchen", 0)]
    engine = Engine(Session(home, clock=LogicalClock()), permissive=True, automations=[bound])
    assert engine.refused == [] and len(engine.automations) == 1


@pytest.mark.parametrize("raw,reason", [
    (gated(LUX, triggers=[{"trigger": "state", "entity_id": "input_boolean.flag", "to": "on"}],
           actions=[{"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.flag"}}]),
     "input_boolean in a trigger (helpers and presence change only with residents)"),
    (gated(state("input_boolean.flag", "off"),
           actions=[{"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.flag"}}]),
     "input_boolean in a template or an action"),
    (gated(LUX, actions=[{"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.flag"}},
                         {"action": "notify.me", "data": {"message": "{{ states('input_boolean.flag') }}"}}]),
     "input_boolean in a template or an action"),
    (gated(LUX, actions=[{"action": "light.turn_on", "target": {"entity_id": "input_boolean.flag"}}]),
     "input_boolean in a template or an action"),  # not its own domain's service: the engine would refuse it
    (gated(LUX, actions=[{"action": "input_boolean.turn_on", "target": {"entity_id": "{{ 'input_boolean.flag' }}"}}]),
     "input_boolean in a template or an action"),
    (gated(LUX, actions=[{"action": "counter.increment", "entity_id": "counter.c"}],
           triggers=[{"trigger": "state", "entity_id": "counter.c"}]), "counter.c: counter entities are not modelled"),
])
def test_a_helper_set_and_read_or_set_otherwise_stays_unbindable(edt, raw, reason):
    _, roles, _, _ = edt
    assert bind(raw, roles, 0) == Unbindable(reason)



# --- review fixes: precision over yield ---------------------------------------------------------------------------

@pytest.mark.parametrize("step", [
    {"trigger": "device", "device_id": DEV, "domain": "sensor", "entity_id": REG, "type": "illuminance", "above": 300},
    {"trigger": "device", "device_id": DEV, "domain": "sensor", "entity_id": REG, "type": "humidity", "below": 30},
    {"platform": "device", "device_id": DEV, "domain": "sensor", "entity_id": REG, "type": "temperature", "above": 2},
])
def test_a_sensor_device_step_on_a_registry_id_names_no_room(edt, step):
    _, roles, _, _ = edt
    assert bind({"alias": "d", "triggers": [step], "actions": LAMP}, roles, 0) == Unbindable(
        "device steps on a registry id name no room")
    cond = {"condition": "device", "device_id": DEV, "domain": "sensor", "entity_id": REG, "type": "is_humidity",
            "below": 60}
    assert bind(gated(cond), roles, 0) == Unbindable("device steps on a registry id name no room")


def test_an_actuator_or_motion_device_step_on_a_registry_id_keeps_the_living_room(edt):
    _, roles, _, _ = edt
    raw = {"alias": "m", "triggers": [{"trigger": "device", "device_id": DEV, "domain": "binary_sensor",
                                       "entity_id": REG, "type": "motion"}],
           "actions": [{"device_id": DEV, "domain": "light", "entity_id": REG, "type": "turn_on"}]}
    b = bind(raw, roles, 0)
    assert b["triggers"][0]["entity_id"] == roles[("motion", "living", 0)]
    assert b["actions"][0]["target"]["entity_id"] == roles[("light", "living", 0)]


def test_changed_states_is_a_state_trigger_on_any_change(edt):
    _, roles, _, home = edt
    from vesper.lab.automations import Engine
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    raw = {"alias": "c", "triggers": [{"trigger": "device", "device_id": DEV, "domain": "light",
                                       "entity_id": "light.office", "type": "changed_states"}], "actions": LAMP}
    b = bind(raw, roles, 0)
    assert b["triggers"] == [{"trigger": "state", "entity_id": roles[("light", "office", 0)], "to": None}]
    engine = Engine(Session(home, clock=LogicalClock()), permissive=True, automations=[b])
    assert engine.refused == []


@pytest.mark.parametrize("entity_id", ["sensor.pool_temperature", "sensor.garage_illuminance",
                                       "sensor.x_garage_average_temperature", "binary_sensor.garage_door",
                                       "binary_sensor.shed_motion", "sensor.greenhouse_humidity",
                                       "sensor.carport_lux", "sensor.veranda_temperature"])
def test_a_sensor_of_a_semi_outdoor_space_is_refused(edt, entity_id):
    _, roles, _, _ = edt
    assert bind(one(entity_id), roles, 0) == Unbindable(f"semi-outdoor spaces are not modelled (for {entity_id})")


@pytest.mark.parametrize("entity_id,role", [("light.garage", "light"), ("switch.pool_pump", "switch"),
                                            ("light.greenhouse", "light")])
def test_an_actuator_of_a_semi_outdoor_space_keeps_the_porch(edt, entity_id, role):
    _, roles, _, _ = edt
    assert bind(one(entity_id), roles, 0)["triggers"][0]["entity_id"] == roles[(role, "porch", 0)]


@pytest.mark.parametrize("threshold", [{"above": 75}, {"below": -40}, {"above": 18, "below": 50}])
def test_a_temperature_threshold_out_of_the_ambient_range_is_refused(edt, threshold):
    _, roles, _, _ = edt
    trigger = {"trigger": "numeric_state", "entity_id": "sensor.kitchen_temperature", **threshold}
    raw = {"alias": "t", "triggers": [trigger], "actions": LAMP}
    assert bind(raw, roles, 0) == Unbindable("threshold looks Fahrenheit or non-ambient")
    assert isinstance(bind(gated(numeric("sensor.kitchen_temperature", **threshold)), roles, 0), Unbindable)
    ok = {"alias": "t", "triggers": [{**trigger, "above": 25, "below": 35}], "actions": LAMP}
    assert isinstance(bind(ok, roles, 0), dict)
    lux = {"alias": "l", "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.kitchen_lux", "above": 300}],
           "actions": LAMP}
    assert isinstance(bind(lux, roles, 0), dict)  # only temperatures


@pytest.mark.parametrize("ids", [("sensor.kitchen_temperature", "sensor.kitchen_temp_2"),
                                 ("binary_sensor.front_door", "binary_sensor.back_door"),
                                 ("climate.living_room", "climate.bedroom")])
def test_distinct_entities_that_collapse_onto_one_device_are_refused(edt, ids):
    _, roles, _, _ = edt
    raw = {"alias": "c", "triggers": [{"trigger": "state", "entity_id": ids[0]}],
           "actions": [{"action": "notify.me", "data": {"message": f"{{{{ states('{ids[1]}') }}}}"}}]}
    r = bind(raw, roles, 0)
    assert r == Unbindable(f"distinct entities collapse onto one device (for {ids[0]}, {ids[1]})")
    from vesper.edt.sweep import reason_class
    assert reason_class(r.reason) == "distinct entities collapse onto one device"
    same = {"alias": "s", "triggers": [{"trigger": "state", "entity_id": ids[0]}],
            "actions": [{"action": "notify.me", "data": {"message": f"{{{{ states('{ids[0]}') }}}}"}}]}
    assert isinstance(bind(same, roles, 0), dict)  # one entity named twice is fine


def test_distinct_lights_of_a_room_bind_to_distinct_lights_in_order_of_first_appearance(edt):
    _, roles, _, _ = edt
    names = ["light.kitchen_spots", "light.kitchen_ceiling", "light.kitchen_island"]
    raw = {"alias": "k", "triggers": [{"trigger": "state", "entity_id": names[0], "to": "on"}],
           "actions": [{"action": "light.turn_on", "target": {"entity_id": names[1:]}},
                       {"action": "light.turn_off", "target": {"entity_id": names[0]}}]}
    b = bind(raw, roles, 0)
    lights = [roles[("light", "kitchen", i)] for i in range(3)]
    assert b["triggers"][0]["entity_id"] == lights[0] and b["actions"][0]["target"]["entity_id"] == lights[1:]
    assert b["actions"][1]["target"]["entity_id"] == lights[0] and bind(raw, roles, 0) == b  # deterministic
    plugs = bind(act(action="switch.turn_on", target={"entity_id": ["switch.porch_a", "switch.garden_b"]}), roles, 0)
    assert plugs["actions"][0]["target"]["entity_id"] == [roles[("switch", "porch", 0)], roles[("switch", "porch", 1)]]
    four = act(action="light.turn_on", target={"entity_id": [*names, "light.kitchen_hood"]})
    assert bind(four, roles, 0) == Unbindable(
        "distinct entities collapse onto one device (for light.kitchen_spots, light.kitchen_hood)")
    area = act(action="light.turn_on", target={"area_id": "kitchen", "entity_id": "light.kitchen"})
    assert bind(area, roles, 0)["actions"][0]["target"]["entity_id"] == lights  # named, then the area, once each


@pytest.mark.parametrize("cond,reason", [
    (state("group.all_lights", "on"), "device groups are not modelled"),
    (numeric("group.temps", above=3), "device groups are not modelled"),
    (numeric("input_number.t"), "a helper numeric_state gate with neither above nor below"),
    ({"condition": "and", "conditions": [{"condition": "zone", "entity_id": "person.me", "zone": "zone.home"}]},
     "person inside an `and` condition"),
])
def test_review_gate_refusals(edt, cond, reason):
    _, roles, _, _ = edt
    assert bind(gated(cond), roles, 0) == Unbindable(reason)
    assert isinstance(bind(gated(state("group.family", "not_home")), roles, 0), dict)


# --- re-review tokens ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("entity_id,expected", [
    ("sensor.x_external_temperature", ("temperature", "porch")),
    ("sensor.x_ute_temperature", ("temperature", "porch")),
    ("sensor.x_balcon_temperature", ("temperature", "porch")),
    ("sensor.parkeerplaats_lux", ("illuminance", "porch")),
    ("sensor.parking_illuminance", ("illuminance", "porch")),
    ("light.x_lyktstolpe", ("light", "porch")),
    ("sensor.fuse_box_temperature", None),
    ("sensor.beer_cooler_temperature", None),
    ("sensor.x_frys_temperature", None),
    ("sensor.freezer_temperature", None),
    ("sensor.water_tank_temperature", None),
    ("sensor.x_ballon_temperature", None),
    ("sensor.temp_mech_room", None),
    ("sensor.probe_bathroom_wall", None),
    ("sensor.temperature_derivative", None),
    ("sensor.heating_set_temperature", None),
    ("sensor.temperature_set", None),
])
def test_rereview_tokens(entity_id, expected):
    assert infer_role(entity_id) == expected


@pytest.mark.parametrize("entity_id", ["sensor.pumphus_temperature", "sensor.pumphouse_humidity",
                                       "sensor.pump_house_temperature"])
def test_a_pump_house_sensor_is_semi_outdoor(edt, entity_id):
    _, roles, _, _ = edt
    assert bind(one(entity_id), roles, 0) == Unbindable(f"semi-outdoor spaces are not modelled (for {entity_id})")


@pytest.mark.parametrize("threshold,ok", [({"above": 36}, False), ({"above": 35}, True), ({"below": -31}, False)])
def test_the_ambient_temperature_range_is_minus_30_to_35(edt, threshold, ok):
    _, roles, _, _ = edt
    raw = {"alias": "t", "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.kitchen_temperature",
                                       **threshold}], "actions": LAMP}
    r = bind(raw, roles, 0)
    assert isinstance(r, dict) if ok else r == Unbindable("threshold looks Fahrenheit or non-ambient")


def test_a_room_less_actuator_name_binds_to_the_living_rooms_copies(edt):
    _, roles, _, _ = edt
    b = bind(act(action="light.turn_on", target={"entity_id": ["light.lamp", "light.strip"]}), roles, 0)
    assert b["actions"][0]["target"]["entity_id"] == [roles[("light", "living", 0)], roles[("light", "living", 1)]]


# --- classes found by the hand audit of bound ambient sensor names --------------------------------------------------

@pytest.mark.parametrize("entity_id,expected", [
    ("sensor.keuken_lux", ("illuminance", "kitchen")),
    ("sensor.schlafzimmer_actual_temperature", ("temperature", "bedroom_1")),
    ("sensor.entrance_luminance", ("illuminance", "corridor")),
    ("sensor.entryway_lux", ("illuminance", "corridor")),
    ("sensor.entrada_luminance", ("illuminance", "corridor")),
    ("sensor.foyer_temperature", ("temperature", "corridor")),
    ("sensor.flur_temperatur", ("temperature", "corridor")),
    ("sensor.woonkamer_temperatuur", ("temperature", "living")),
    ("sensor.badkamer_luchtvochtigheid", ("humidity", "bathroom")),
    ("sensor.chambre_temperature", ("temperature", "bedroom_1")),
    ("sensor.office_heating_temp", None),  # a heating setpoint (fed to climate.set_temperature)
    ("sensor.ac_operation_mode_temp", None),  # a mode, not a temperature
    ("sensor.kitchen_strip_light", None),  # an LED strip's state
    ("sensor.office_lamp_brightness", None),  # a lamp's brightness setting
    ("sensor.heating_current_temperature", ("temperature", "living")),
    ("sensor.outdoor_brightness", ("illuminance", "porch")),
])
def test_audit_classes(entity_id, expected):
    assert infer_role(entity_id) == expected
