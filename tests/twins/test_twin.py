import pytest

from vesper.twins.twin import Twin, TwinError, stable_seed


def test_initial_state_follows_each_feature_type(vds):
    t = Twin.from_vds(vds["MCCGQ11LM"])
    assert t.state["contact"] is True  # value_off: closed


def test_a_battery_device_starts_with_a_full_battery_not_an_empty_one():
    """Every battery twin started at 0 %, 0 mV and 0 degrees, so Home Assistant listed them all as low batteries
    (found live 2026-11-01). They start full and at room temperature; a mains voltage in V is left alone."""
    feats = [{"name": "battery", "type": "numeric", "range": [0, 100], "unit": "%", "access": ["state"]},
             {"name": "voltage", "type": "numeric", "unit": "mV", "access": ["state"]},
             {"name": "device_temperature", "type": "numeric", "unit": "\u00b0C", "access": ["state"]},
             {"name": "power", "type": "numeric", "unit": "W", "access": ["state"]}]
    t = Twin.from_vds({"id": "zigbee:x:y", "features": feats})
    assert (t.state["battery"], t.state["voltage"], t.state["device_temperature"], t.state["power"]) == (100, 3000,
                                                                                                         20, 0)
    mains = Twin.from_vds({"id": "zigbee:x:z", "features": [{"name": "voltage", "type": "numeric", "unit": "V",
                                                             "access": ["state"]}]})
    assert mains.state["voltage"] == 0
    narrow = Twin.from_vds({"id": "zigbee:x:w", "features": [{"name": "battery", "type": "numeric", "range": [0, 50],
                                                              "access": ["state"]}]})
    assert narrow.state["battery"] == 0  # a start outside the feature's range is not used



def test_a_thermostat_starts_heating_to_a_room_temperature_not_off_at_its_minimum():
    """Every thermostat twin started off at its lowest setpoint (5 degrees), so a home in winter was never heated,
    even when Home Assistant raised the setpoint (found live 2026-11-04). It starts in heat at 20 degrees, as a
    home keeps one; a cooling setpoint at 24."""
    feats = [{"name": "current_heating_setpoint", "type": "numeric", "range": [5, 30], "unit": "\u00b0C",
              "access": ["state", "set"]},
             {"name": "occupied_cooling_setpoint", "type": "numeric", "unit": "\u00b0C", "access": ["state", "set"]},
             {"name": "local_temperature", "type": "numeric", "unit": "\u00b0C", "access": ["state"]},
             {"name": "system_mode", "type": "enum", "values": ["off", "auto", "heat"], "access": ["state", "set"]}]
    t = Twin.from_vds({"id": "zigbee:x:t", "features": feats})
    assert (t.state["current_heating_setpoint"], t.state["occupied_cooling_setpoint"], t.state["local_temperature"],
            t.state["system_mode"]) == (20, 24, 20, "heat")
    auto = Twin.from_vds({"id": "zigbee:x:u", "features": [{"name": "system_mode", "type": "enum",
                                                            "values": ["off", "auto"], "access": ["state", "set"]}]})
    assert auto.state["system_mode"] == "auto"  # no heat mode: auto, never off

def test_a_matter_color_temperature_light_starts_at_the_spec_default_not_below_its_physical_range():
    """Experiment 6 (2026-10-26): starting at 0 and 1 mireds, two products' twins lay below the node's physical
    minimum (135 mireds) and the node refused them. Matter's ColorTemperatureMireds default is 0x00FA (250)."""
    ct = [{"name": f"Color Control.{a}", "type": "numeric", "access": ["state", "get"], **extra}
          for a, extra in (("ColorTemperatureMireds", {}), ("CoupleColorTempToLevelMinMireds", {"range": [1, 65279]}),
                           ("StartUpColorTemperatureMireds", {"range": [1, 65279]}))]
    t = Twin.from_vds({"id": "matter:1460:aa61", "features": ct})
    assert [t.state[f["name"]] for f in ct] == [250, 250, 250]


def test_a_light_s_unknown_physical_limits_start_as_the_widest_the_attributes_allow():
    """The DCL publishes no limits, so the twin claims none: 1 to 0xFEFF mireds (Matter's PhysicalMax default),
    with the 250-mired start inside."""
    limits = [{"name": "Color Control.ColorTempPhysicalMinMireds", "type": "numeric", "access": ["state", "get"],
               "range": [1, 65279]},
              {"name": "Color Control.ColorTempPhysicalMaxMireds", "type": "numeric", "access": ["state", "get"]}]
    t = Twin.from_vds({"id": "matter:1460:aa61", "features": limits})
    assert (t.state[limits[0]["name"]], t.state[limits[1]["name"]]) == (1, 0xFEFF)


def test_binary_values_are_matched_like_zigbee2mqtt_and_toggle_flips(vds):
    t = Twin.from_vds(vds["LED1545G12"])
    assert t.coerce("state", "on") == "ON"
    t.state["state"] = "ON"
    assert t.coerce("state", "TOGGLE") == "OFF"
    with pytest.raises(TwinError, match="is not 'ON' or 'OFF'"):
        t.coerce("state", "blink")


def test_numeric_range_and_type_are_enforced(vds):
    t = Twin.from_vds(vds["LED1545G12"])
    assert t.coerce("brightness", 254) == 254
    with pytest.raises(TwinError, match="outside"):
        t.coerce("brightness", 300)
    with pytest.raises(TwinError, match="not a number"):
        t.coerce("brightness", True)


def test_unknown_feature_and_unmodeled_type_are_rejected(vds):
    t = Twin.from_vds(vds["TRV-1"])
    with pytest.raises(TwinError, match="unknown feature"):
        t.coerce("nope", 1)
    with pytest.raises(TwinError, match="not modeled"):
        t.coerce("schedule", [])


def test_physical_values_saturate_at_the_measuring_range(vds):
    t = Twin.from_vds(vds["MCCGQ11LM"])
    assert t.clamp("battery", 140) == 100


def test_payload_is_nested_and_skips_duplicate_exposes(vds):
    light = Twin.from_vds(vds["LED1545G12"]).payload()
    assert light["color"] == {"x": 0, "y": 0} and light["state"] == "OFF"
    assert Twin.from_vds(vds["TWIN-1"]).payload() == {"contact": True}


def test_seeds_are_stable_across_processes():
    assert stable_seed(0, "zigbee:a:b") == stable_seed(0, "zigbee:a:b") != stable_seed(1, "zigbee:a:b")


def test_a_numeric_enum_option_accepts_the_string_home_assistants_select_sends():
    vds = {"id": "zigbee:acme:meter", "features": [
        {"name": "baud_rate", "type": "enum", "access": ["state", "set"], "values": [2400, 9600, 38400]}]}
    t = Twin.from_vds(vds)
    assert t.coerce("baud_rate", "38400") == 38400 and t.coerce("baud_rate", 9600) == 9600
    with pytest.raises(TwinError, match="not one of"):
        t.coerce("baud_rate", "3840")
    with pytest.raises(TwinError, match="not one of"):
        t.coerce("baud_rate", True)


def test_a_matter_twin_takes_booleans_for_its_boolean_attributes():
    from tests.catalog.test_matter_normalize import records as matter_records
    t = Twin.from_vds(matter_records()["matter:100b:0016"])
    assert t.state["Boolean State.StateValue"] is False and t.coerce("Boolean State.StateValue", True) is True


def test_a_nullable_feature_holds_null_and_another_does_not():
    """The firmware anchors read null from nullable Matter attributes (L4); a twin can hold it there only."""
    vds = {"id": "matter:test:1", "vds_version": 1, "identity": {}, "features": [
        {"name": "Level Control.OnLevel", "type": "numeric", "range": [0, 254], "access": ["state", "get", "set"],
         "nullable": True},
        {"name": "Level Control.CurrentLevel", "type": "numeric", "range": [1, 254],
         "access": ["state", "get", "set"]}]}
    t = Twin.from_vds(vds)
    assert t.coerce("Level Control.OnLevel", None) is None
    with pytest.raises(TwinError, match="not a number"):
        t.coerce("Level Control.CurrentLevel", None)
