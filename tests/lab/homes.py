"""A small catalog and a home on the synthetic house, for the Lab tests."""
import textwrap

from tests.catalog.fixtures import zdef
from tests.catalog.test_matter_normalize import records as matter_records
from tests.lab.maps import house
from tests.twins.conftest import records as zigbee_records
from vesper.catalog.normalize.zigbee import zigbee_records as normalize_zigbee

LOCK = "matter:115f:0001"
THERMOSTAT = zdef("THERM-1", "Acme", [{"type": "climate", "features": [
    {"name": "local_temperature", "access": 5, "type": "numeric", "property": "local_temperature", "unit": "°C"},
    {"name": "current_heating_setpoint", "access": 7, "type": "numeric", "property": "current_heating_setpoint",
     "unit": "°C", "value_min": 5, "value_max": 30, "value_step": 0.5},
    {"name": "system_mode", "access": 7, "type": "enum", "property": "system_mode", "values": ["off", "auto", "heat"]},
]}])


def catalog():
    from tests.catalog.fixtures import TASMOTA_PIN, TASMOTA_TEMPLATES, tasmota_tarball
    from vesper.catalog.normalize.tasmota import tasmota_records
    out = {r["id"]: r for r in zigbee_records().values()}
    out[LOCK] = matter_records()[LOCK]
    out.update((r["id"], r) for r in tasmota_records(tasmota_tarball(TASMOTA_TEMPLATES), TASMOTA_PIN)[0])
    for r in normalize_zigbee({"version": "26.123.0", "definitions": [THERMOSTAT]}):
        out[r["id"]] = r
    return out


HOME = """\
    version: 2
    scene: test/house
    seed: 3
    start_time: "07:30"
    avatar: {model: female_0, start: {room: hall}}
    doors:
      - {doorway: bedroom-hall, open: true}
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: hall-porch}}
      - {product: "matter:115f:0001", name: front_lock, at: {door: hall-porch},
         state: {"Door Lock.LockState": Unlocked}}
      - {product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}
      - {product: "zigbee:acme:dim-1", name: bedroom_lamp, at: {room: bedroom, xy: [5.0, 5.0], mount: table}}
      - {product: "zigbee:acme:btn-1", name: bedroom_button, at: {room: bedroom, xy: [3.2, 3.5], mount: wall},
         controls: [bedroom_lamp]}
      - {product: "zigbee:acme:th-1", name: kitchen_climate, at: {room: kitchen, xy: [6.5, 0.5], mount: wall,
         facing: 90}}
"""


def write_home(tmp_path, text=HOME, world_map=None):
    (world_map or house()).save(tmp_path / "map.json")
    path = tmp_path / "home.yaml"
    path.write_text(textwrap.dedent(text))
    return path


# A Tasmota (Wi-Fi) kettle plug in the kitchen and a Tasmota dimmer lamp in the bedroom (plan W7a2)
WIFI_DEVICES = (
    '      - {product: "tasmota:acme:acme_plug_p1", name: kettle_plug, at: {room: kitchen, xy: [6.0, 2.0], '
    'mount: floor}}\n'
    '      - {product: "tasmota:brite:brite_dimmer_d1", name: reading_lamp, at: {room: bedroom, xy: [6.5, 5.5], '
    'mount: table}}\n')


def with_wifi(text=HOME):
    """HOME with two Tasmota devices placed."""
    return text.replace("    devices:\n", "    devices:\n" + WIFI_DEVICES, 1)
