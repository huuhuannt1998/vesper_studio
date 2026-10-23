from tests.catalog.fixtures import BATTERY, CONTACT, ZIGBEE_DOC, zdef
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.catalog.schema import validate_vds


def by_model():
    return {r["identity"]["model"]: r for r in zigbee_records(ZIGBEE_DOC)}


def test_every_record_is_schema_valid_and_white_labels_are_products():
    recs = zigbee_records(ZIGBEE_DOC)
    assert [validate_vds(r) for r in recs] == [[]] * len(recs)
    assert len(recs) == len(ZIGBEE_DOC["definitions"]) + 3


def test_contact_sensor():
    r = by_model()["MCCGQ11LM"]
    assert r["id"] == "zigbee:aqara:mccgq11lm" and r["identity"]["category"] == "contact_sensor"
    contact = next(f for f in r["features"] if f["name"] == "contact")
    assert contact["ha"] == "binary_sensor/door" and contact["value_on"] is False and contact["access"] == ["state"]
    assert r["fidelity"]["reached"] == {"L0": True, "L1": True, "L2": False, "L3": False, "L4": False}
    assert r["behavior"]["reporting"] == {"battery": {"min_s": 3600, "max_s": 65000, "change": 0}}
    assert r["fidelity"]["assumed"] == ["behavior.reporting.battery"]
    assert set(r["fidelity"]["reasons"]) == {"L2", "L3", "L4"}


def test_light_composites_flatten_with_paths():
    r = by_model()["LED1545G12"]
    names = {f["name"]: f for f in r["features"]}
    assert set(names) == {"state", "brightness", "color.x", "color.y"}
    assert {f["ha"] for f in names.values()} == {"light"} and names["color.x"]["path"] == "light"
    assert names["brightness"]["range"] == [0, 254] and names["state"]["access"] == ["state", "set", "get"]
    assert set(r["behavior"]["reporting"]) == {"state", "brightness"}
    assert r["binding"]["zigbee"]["ota"] is True and r["identity"]["category"] == "light"


def test_plug_and_its_white_labels():
    recs = by_model()
    base = recs["TS011F_plug_1"]
    assert base["identity"]["category"] == "plug"
    same = recs["BW-SHP13"]
    assert same["identity"]["white_label_of"] == base["id"]
    assert same["identity"]["ids"]["zigbee_model"] == ["TS011F"]
    own = recs["TS011F_plug_1_2"]
    assert own["identity"]["ids"] == {"zigbee_model": [],
                                      "zigbee_fingerprint": [{"modelID": "TS011F", "manufacturerName": "_TZ3000_x"}]}
    assert own["identity"]["description"] == "Wall socket"


def test_dynamic_exposes_are_assumed_so_l1_is_not_reached():
    r = by_model()["DYN-1"]
    assert "features" in r["fidelity"]["assumed"]
    assert r["fidelity"]["reasons"]["L1"] == "features evaluated with assumed default options"


def test_failed_expose_evaluation_keeps_the_product_with_a_reason():
    r = by_model()["ERR-1"]
    assert r["features"] == [] and r["fidelity"]["reached"]["L1"] is False
    assert r["fidelity"]["reasons"]["L1"] == "exposes evaluation failed: boom"


def test_unmodeled_primary_feature_blocks_l1():
    r = by_model()["TRV-1"]
    assert r["identity"]["category"] == "thermostat"
    sched = next(f for f in r["features"] if f["name"] == "schedule")
    assert sched["type"] == "unsupported" and sched["unsupported_reason"] == "zigbee expose type 'list' is not modeled"
    assert r["fidelity"]["reasons"]["L1"].startswith("unsupported primary feature types: schedule")


def test_duplicate_feature_names_are_made_unique():
    assert [f["name"] for f in by_model()["TWIN-1"]["features"]] == ["contact", "contact#2"]


def test_product_without_any_id_does_not_reach_l0():
    r = by_model()["NOID-1"]
    assert r["fidelity"]["reasons"]["L0"] == "no zigbee model id or fingerprint with an identifying value"


def test_white_label_without_vendor_inherits_the_parent_vendor():
    r = by_model()["TS011F_plug_EU"]
    assert r["identity"]["vendor"] == "Tuya" and r["id"] == "zigbee:tuya:ts011f_plug_eu"


def test_a_fingerprint_without_any_identifying_value_does_not_reach_l0():
    r = by_model()["NULLFP-1"]
    assert r["fidelity"]["reasons"]["L0"] == "no zigbee model id or fingerprint with an identifying value"


def test_an_endpoints_only_fingerprint_identifies_the_product():
    assert by_model()["EPONLY-1"]["fidelity"]["reached"]["L0"] is True


def test_options_are_kept_as_full_exposes_for_the_round_trip():
    assert by_model()["LED1545G12"]["binding"]["zigbee"]["options"][0]["property"] == "transition"


def halo_doc(reverse=False):
    defs = [zdef("HALO", "HALO Smart Labs", [CONTACT], zigbee_model=["halo"]),
            zdef("HALO+", "HALO Smart Labs", [CONTACT, BATTERY], zigbee_model=["haloplus"],
                 white_label=[{"vendor": "Acme", "model": "H-PLUS", "description": None, "fingerprint": []}])]
    return {"version": "26.123.0", "definitions": defs[::-1] if reverse else defs}


def test_definitions_whose_names_slug_alike_stay_separate_with_stable_ids():
    recs = {r["identity"]["model"]: r for r in zigbee_records(halo_doc())}
    halo, plus = recs["HALO"], recs["HALO+"]
    assert halo["id"] != plus["id"] and halo["id"].startswith("zigbee:halo-smart-labs:halo-")
    assert halo["identity"]["ids"]["zigbee_model"] == ["halo"] and len(plus["features"]) == 2
    assert recs["H-PLUS"]["identity"]["white_label_of"] == plus["id"]
    again = {r["identity"]["model"]: r["id"] for r in zigbee_records(halo_doc(reverse=True))}
    assert again["HALO"] == halo["id"] and again["HALO+"] == plus["id"]


def test_reporting_change_is_in_the_feature_units_not_raw_zcl():
    doc = {"version": "26.123.0", "definitions": [zdef("TH-1", "Acme", [
        {"name": "temperature", "access": 5, "type": "numeric", "property": "temperature", "unit": "°C"},
        {"name": "illuminance", "access": 5, "type": "numeric", "property": "illuminance", "unit": "lx"}])]}
    reporting = zigbee_records(doc)[0]["behavior"]["reporting"]
    assert reporting["temperature"] == {"min_s": 10, "max_s": 3600, "change": 1.0}  # 100 raw = 1.0 °C
    assert reporting["illuminance"]["change"] == 0


def test_l2_is_left_to_validation_evidence():
    rec = zigbee_records({"version": "26.123.0", "definitions": [zdef("TH-1", "Acme", [
        {"name": "temperature", "access": 5, "type": "numeric", "property": "temperature", "unit": "°C"}])]})[0]
    assert rec["fidelity"]["reached"]["L2"] is False
    assert "vesper validate acceptance" in rec["fidelity"]["reasons"]["L2"]
