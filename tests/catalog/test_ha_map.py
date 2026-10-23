import pytest

from tests.catalog.ha_2026_10_2 import BINARY_SENSOR, SENSOR
from vesper.catalog.normalize.ha_map import matter_ha, unknown_device_classes, zigbee_ha


def test_crosswalk_uses_only_classes_of_the_pinned_home_assistant():
    assert unknown_device_classes({"binary_sensor": BINARY_SENSOR, "sensor": SENSOR}) == []


def test_a_missing_class_is_reported():
    assert "binary_sensor/door" in unknown_device_classes({"binary_sensor": frozenset(), "sensor": frozenset()})


@pytest.mark.parametrize("leaf,parent,expected", [
    ({"type": "binary", "name": "contact", "access": 1}, None, "binary_sensor/door"),
    ({"type": "binary", "name": "child_lock", "access": 7}, None, "switch"),
    ({"type": "binary", "name": "mystery", "access": 1}, None, "binary_sensor"),
    ({"type": "numeric", "name": "battery", "access": 1}, None, "sensor/battery"),
    ({"type": "numeric", "name": "countdown", "access": 3}, None, "number"),
    ({"type": "numeric", "name": "brightness", "access": 7}, "light", "light"),
    ({"type": "enum", "name": "action", "access": 1}, None, "event"),
    ({"type": "enum", "name": "power_on_behavior", "access": 7}, None, "select"),
    ({"type": "enum", "name": "identify", "access": 2}, None, "button"),
    ({"type": "text", "name": "note", "access": 1}, None, "sensor"),
    ({"type": "list", "name": "schedule", "access": 7}, None, None),
])
def test_zigbee_ha(leaf, parent, expected):
    assert zigbee_ha(leaf, parent) == expected


@pytest.mark.parametrize("device_type,cluster,attribute,expected", [
    ("Contact Sensor", "Boolean State", "StateValue", "binary_sensor/door"),
    ("Water Leak Detector", "Boolean State", "StateValue", "binary_sensor/moisture"),
    ("Extended Color Light", "On/Off", "OnOff", "light"),
    ("On/Off Plug-in Unit", "On/Off", "OnOff", "switch/outlet"),
    ("Room Air Conditioner", "On/Off", "OnOff", "switch"),
    ("Thermostat", "Thermostat", "LocalTemperature", "climate"),
    ("Temperature Sensor", "Temperature Measurement", "MeasuredValue", "sensor/temperature"),
    ("Generic Switch", "Switch", "CurrentPosition", "event"),
    ("Robotic Vacuum Cleaner", "RVC Run Mode", "CurrentMode", None),
])
def test_matter_ha(device_type, cluster, attribute, expected):
    assert matter_ha(device_type, cluster, attribute) == expected
