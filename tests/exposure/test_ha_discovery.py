import json

import jinja2

from vesper.exposure.ha_discovery import device_discovery, object_id

IEEE = "0x00124b0000abcdef"


def render(template, payload):
    return jinja2.Environment().from_string(template).render(value_json=payload, value=payload)


def discover(vds, name="dev"):
    return device_discovery(vds, ieee=IEEE, friendly_name=name)


def components(vds):
    return discover(vds)[1]["cmps"]


def test_device_message_topic_device_and_shared_topics(vds):
    topic, payload, expected, skipped = discover(vds["MCCGQ11LM"], "hall/door")
    assert topic == f"homeassistant/device/{IEEE}/config"
    assert payload["dev"] == {"ids": [f"zigbee2mqtt_{IEEE}"], "name": "hall/door", "mf": "Aqara", "mdl": "MCCGQ11LM"}
    assert payload["state_topic"] == "zigbee2mqtt/hall/door" and payload["o"]["name"] == "VESPER"
    assert payload["availability"][0]["topic"] == "zigbee2mqtt/hall/door/availability"


def test_contact_is_a_door_binary_sensor_that_reads_zigbee2mqtt_semantics(vds):
    door = components(vds["MCCGQ11LM"])[object_id(IEEE, "contact")]
    assert door["p"] == "binary_sensor" and door["device_class"] == "door"
    assert render(door["value_template"], {"contact": False}) == "ON"  # contact=false means open
    assert render(door["value_template"], {"contact": True}) == "OFF"


def test_numeric_sensors_carry_class_unit_state_class_and_category(vds):
    battery = components(vds["MCCGQ11LM"])[object_id(IEEE, "battery")]
    assert (battery["device_class"], battery["unit_of_measurement"], battery["state_class"],
            battery["entity_category"]) == ("battery", "%", "measurement", "diagnostic")
    assert render(battery["value_template"], {"battery": 87}) == "87"


def test_switch_commands_are_zigbee2mqtt_set_payloads(vds):
    plug = components(vds["PLUG-1"])[object_id(IEEE, "state")]
    assert plug["p"] == "switch" and plug["command_topic"] == "zigbee2mqtt/dev/set"
    assert json.loads(plug["payload_on"]) == {"state": "ON"} and plug["name"] is None
    assert render(plug["value_template"], {"state": "ON"}) == "ON"


def test_light_is_one_json_schema_entity(vds):
    cmps = components(vds["LED1545G12"])
    light = cmps[object_id(IEEE, "light")]
    assert light["schema"] == "json" and light["brightness"] is True and light["brightness_scale"] == 254
    assert light["supported_color_modes"] == ["xy"]
    assert not any(c["p"] == "number" for c in cmps.values())  # brightness and colour belong to the light


def test_button_actions_are_events_on_their_own_topic(vds):
    event = components(vds["BTN-1"])[object_id(IEEE, "action")]
    assert event["p"] == "event" and event["state_topic"] == "zigbee2mqtt/dev/event/action"
    assert event["event_types"] == ["single", "double", "hold"]


def test_unexposed_features_are_listed_with_a_reason(vds):
    _, payload, expected, skipped = discover(vds["TRV-1"])
    assert ("local_temperature", "climate entities are not exposed yet") in skipped
    assert ("schedule", "feature type not modeled") in skipped
    assert payload["cmps"] == {} and expected == []


def test_expected_entities_match_default_entity_ids(vds):
    _, payload, expected, _ = discover(vds["PLUG-1"])
    ids = {e.entity_id for e in expected}
    assert ids == {f"{c['p']}.{oid}" for oid, c in payload["cmps"].items()}
    assert {e.platform: e.controllable for e in expected} == {"switch": True, "sensor": False}


def test_number_and_select_command_templates_render_to_json():
    vds = {"identity": {"vendor": "V", "model": "M"}, "features": [
        {"name": "countdown", "type": "numeric", "access": ["state", "set"], "category": None, "ha": "number",
         "source": "s", "range": [0, 43200]},
        {"name": "power_on_behavior", "type": "enum", "access": ["state", "set"], "category": None, "ha": "select",
         "source": "s", "values": ["on", "off"]}]}
    cmps = device_discovery(vds, ieee=IEEE, friendly_name="p")[1]["cmps"]
    number, select = cmps[object_id(IEEE, "countdown")], cmps[object_id(IEEE, "power_on_behavior")]
    assert json.loads(render(number["command_template"], 30)) == {"countdown": 30}
    assert json.loads(render(select["command_template"], "off")) == {"power_on_behavior": "off"}
    assert (number["min"], number["max"]) == (0, 43200)
