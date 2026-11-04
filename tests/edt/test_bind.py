"""The standard EDT home (several sensor products per room) and the binder that rewrites a public automation's entity
ids onto its devices by role. Needs the built catalog (catalog/vds) for the real products."""
from pathlib import Path

import pytest
import yaml

from vesper.edt.bind import ROLE_PRIORITY, Unbindable, bind, infer_role
from vesper.edt.home import PRODUCTS, build_edt_home

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")

ROOMS = ("living", "bedroom_1", "bedroom_2", "kitchen", "bathroom", "office", "corridor")


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
    for room in ROOMS:
        d = by_name[f"{room}_lux0"]
        assert d["product"] == PRODUCTS["illuminance"][0] and d["at"]["room"] == room and d["at"]["mount"] == "ceiling"
        assert by_name[f"{room}_temp0"]["product"] == PRODUCTS["temperature"][0]
        assert f"{room}_lux1" not in by_name and f"{room}_temp1" not in by_name
    assert {k for k in roles if k[0] == "illuminance"} == {("illuminance", r, 0) for r in ROOMS}
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
    assert not any(k[0] in ("cover", "switch") for k in roles)


@pytest.mark.parametrize("entity_id,expected", [
    ("sensor.hallway_lux", ("illuminance", "corridor")),
    ("binary_sensor.front_door", ("door", "living")),
    ("sensor.xyz_1", None),
    ("sensor.hallway_light", ("illuminance", "corridor")),
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


@pytest.mark.parametrize("raw", [
    {"alias": "d", "triggers": [{"trigger": "device", "device_id": "abc123", "type": "turned_on"}], "actions": []},
    {"alias": "a", "triggers": [], "actions": [{"action": "light.turn_on", "target": {"area_id": "kitchen"}}]},
    {"alias": "c", "triggers": [], "conditions": [{"condition": "device", "device_id": "abc"}], "actions": []},
])
def test_device_and_area_handles_are_not_modelled(edt, raw):
    _, roles, _, _ = edt
    assert bind(raw, roles, 0) == Unbindable("device_id/area_id not modelled")


def test_an_entity_in_above_or_below_is_bound_like_any_other(edt):
    _, roles, _, _ = edt
    raw = one("sensor.kitchen_temperature", above="sensor.bedroom_temperature", below=30)
    t = bind(raw, roles, 0)["triggers"][0]
    assert t["above"] == roles[("temperature", "bedroom_1", 0)] and t["below"] == 30
    assert isinstance(bind(one("sensor.kitchen_temperature", below="sensor.outdoor_temperature"), roles, 0),
                      Unbindable)


@pytest.mark.parametrize("template", ["{{ is_state('person.me', 'home') }}", "{{ states('group.lamps') }}",
                                      "{{ is_state('input_boolean.guests', 'on') }}"])
def test_a_quoted_id_of_an_unmodelled_domain_in_a_template_is_unbindable(edt, template):
    _, roles, _, _ = edt
    r = bind(one("sun.sun", value_template=template), roles, 0)
    assert isinstance(r, Unbindable) and template.split("'")[1] in r.reason


def test_sun_and_service_names_in_templates_are_left_alone(edt):
    _, roles, _, _ = edt
    t = "{{ is_state('sun.sun', 'below_horizon') and 'light.turn_on' in x }}"
    assert bind(one("sun.sun", value_template=t), roles, 0)["triggers"][0]["value_template"] == t


@pytest.mark.parametrize("entity_id", ["sensor.outdoor_temperature", "sensor.outside_humidity", "light.garden",
                                       "binary_sensor.garage_door", "sensor.porch_lux", "binary_sensor.yard_motion",
                                       "light.balcony"])
def test_outdoor_entities_are_not_modelled(edt, entity_id):
    _, roles, _, _ = edt
    assert bind(one(entity_id), roles, 0) == Unbindable("outdoor entities are not modelled")
    assert infer_role(entity_id) is None
