from vesper.catalog.normalize.common import CATEGORIES, finalize_levels, l1_status, make_id, new_record, slug
from vesper.catalog.schema import validate_vds, vds_schema
from vesper.catalog.sources import source_entry

CONTACT = {"name": "contact", "type": "binary", "access": ["state"], "category": None,
           "ha": "binary_sensor/door", "source": "zigbee-herdsman-converters@26.123.0",
           "value_on": False, "value_off": True}


def _rec():
    r = new_record(protocol="zigbee", vendor="Aqara", model="MCCGQ11LM", description="Door and window sensor",
                   category="contact_sensor", ids={"zigbee_model": ["lumi.sensor_magnet.aq2"]},
                   sources=[source_entry("zigbee", "26.123.0")])
    r["features"] = [dict(CONTACT)]
    return r


def test_minimal_record_is_valid():
    assert validate_vds(_rec()) == []


def test_bad_feature_type_reports_its_path():
    r = _rec()
    r["features"][0]["type"] = "colour"
    errors = validate_vds(r)
    assert errors and errors[0].startswith("features/0/type:")


def test_id_must_be_a_protocol_prefixed_slug():
    r = _rec()
    r["id"] = "Aqara MCCGQ11LM"
    assert any(e.startswith("id:") for e in validate_vds(r))


def test_matter_ids_are_a_list_of_vid_pid_pairs():
    r = _rec()
    r["identity"]["ids"] = {"matter_vid_pid": [[4107, 22], [4107, 23]]}
    assert validate_vds(r) == []
    r["identity"]["ids"] = {"matter_vid_pid": [4107, 22]}
    assert validate_vds(r)


def test_category_enum_matches_code():
    assert vds_schema()["properties"]["identity"]["properties"]["category"]["enum"] == list(CATEGORIES)


def test_slug_and_id():
    assert slug("TRADFRI bulb E27 WS�opal 980lm") == "tradfri-bulb-e27-ws-opal-980lm"
    assert slug("  ") == "unknown"
    assert make_id("matter", "Philips Hue", "LCA001") == "matter:philips-hue:lca001"


def test_l1_needs_a_mapped_primary_feature_and_no_unsupported_primary_one():
    config = {**CONTACT, "name": "x", "category": "config"}
    schedule = {**CONTACT, "name": "schedule", "type": "unsupported", "ha": None}
    assert l1_status([CONTACT], []) == (True, None)
    assert l1_status([config], [])[0] is False
    assert l1_status([CONTACT, schedule], [])[1].startswith("unsupported primary feature types: schedule")
    assert l1_status([{**CONTACT, "ha": None}], [])[1] == "no primary feature maps to a Home Assistant platform"
    assert l1_status([CONTACT], ["features"])[1].startswith("features evaluated")


def test_levels_form_a_ladder():
    r = finalize_levels(_rec(), l0_ok=False, l0_reason="vendor unknown")
    assert r["fidelity"]["reached"]["L0"] is False and r["fidelity"]["reached"]["L1"] is False
    assert r["fidelity"]["reasons"] == {"L0": "vendor unknown", "L1": "L0 not reached"}
    r = finalize_levels(_rec(), l0_ok=True, l0_reason=None)
    assert r["fidelity"]["reached"]["L0"] and r["fidelity"]["reached"]["L1"]


def test_an_assumed_identity_field_blocks_l0():
    r = _rec()
    r["fidelity"]["assumed"].append("identity.category")
    finalize_levels(r, l0_ok=True, l0_reason=None)
    assert r["fidelity"]["reasons"]["L0"] == "identity fields assumed: identity.category"


def test_a_button_event_is_primary_even_when_tagged_diagnostic():
    action = {**CONTACT, "name": "action", "type": "enum", "category": "diagnostic", "ha": "event"}
    assert l1_status([action], []) == (True, None)
