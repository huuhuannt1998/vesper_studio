import pytest

from vesper.twins.twin import Twin, TwinError, stable_seed


def test_initial_state_follows_each_feature_type(vds):
    t = Twin.from_vds(vds["MCCGQ11LM"])
    assert t.state["contact"] is True  # value_off: closed
    assert t.state["battery"] == 0


def test_a_matter_color_temperature_light_starts_at_the_spec_default_not_below_its_physical_range():
    """Experiment 6 (2026-10-26): starting at 0 and 1 mireds, two products' twins lay below the node's physical
    minimum (135 mireds) and the node refused them. Matter's ColorTemperatureMireds default is 0x00FA (250)."""
    ct = [{"name": f"Color Control.{a}", "type": "numeric", "access": ["state", "get"], **extra}
          for a, extra in (("ColorTemperatureMireds", {}), ("CoupleColorTempToLevelMinMireds", {"range": [1, 65279]}),
                           ("StartUpColorTemperatureMireds", {"range": [1, 65279]}))]
    t = Twin.from_vds({"id": "matter:1460:aa61", "features": ct})
    assert [t.state[f["name"]] for f in ct] == [250, 250, 250]


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
