from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS, ZAP_FILES
from vesper.catalog.ingest.matter_zap import parse_zap
from vesper.catalog.normalize.matter import matter_records
from vesper.catalog.schema import validate_vds


def records():
    recs = matter_records(DCL_MODELS, DCL_VENDORS, parse_zap(ZAP_FILES), dcl_version="height-1",
                          zap_version="v1.6.1.0")
    return {r["id"]: r for r in recs}


def test_every_record_is_schema_valid():
    recs = records()
    assert len(recs) == len(DCL_MODELS) and all(validate_vds(r) == [] for r in recs.values())


def test_contact_sensor_gets_its_device_type_features():
    r = records()["matter:100b:0016"]
    assert r["identity"]["vendor"] == "Philips Hue" and r["identity"]["category"] == "contact_sensor"
    assert r["identity"]["ids"] == {"matter_vid_pid": [[4107, 22]]}
    assert [f["name"] for f in r["features"]] == ["Boolean State.StateValue"]  # utility clusters skipped
    assert r["features"][0]["ha"] == "binary_sensor/door" and r["features"][0]["access"] == ["state", "get"]
    assert r["fidelity"]["reached"]["L1"] is True
    assert r["fidelity"]["provenance"]["features"] == "connectedhomeip-zap@v1.6.1.0:device type 0x0015"
    assert r["sources"][0]["redistributable"] is False


def test_lock_enum_bitmap_and_command_settable_state():
    feats = {f["name"]: f for f in records()["matter:115f:0001"]["features"]}
    state = feats["Door Lock.LockState"]
    assert state["values"] == ["NotFullyLocked", "Locked", "Unlocked"] and "set" in state["access"]
    assert state["ha"] == "lock"
    modes = feats["Door Lock.SupportedOperatingModes"]
    assert modes["flags"] == ["Normal", "Vacation"] and modes["category"] == "diagnostic"
    assert "Door Lock.Language" not in feats  # optional attribute


def test_temperature_scale_unit_and_descriptive_bounds():
    feats = {f["name"]: f for f in records()["matter:115f:0003"]["features"]}
    mv = feats["Temperature Measurement.MeasuredValue"]
    assert (mv["unit"], mv["scale"], mv["ha"]) == ("°C", 0.01, "sensor/temperature")
    lo = feats["Temperature Measurement.MinMeasuredValue"]
    assert lo["range"] == [-27315, 0x7FFE] and lo["category"] == "diagnostic"


def test_unmodeled_primary_attribute_blocks_l1():
    r = records()["matter:115f:0002"]
    assert r["identity"]["category"] == "appliance"
    assert r["fidelity"]["reasons"]["L1"].startswith("unsupported primary feature types: RVC Run Mode.SupportedModes")


def test_unknown_vendor_name_and_device_type_are_reasons_not_crashes():
    r = records()["matter:fff1:8000"]
    assert r["identity"]["vendor"] == "VID 0xFFF1" and r["identity"]["model"] == "PID 0x8000"
    assert r["fidelity"]["reasons"]["L0"] == ("vendor not in DCL vendor info; no product name; "
                                              "device type 0x03E7 not in the ZAP XML")
    assert r["features"] == [] and r["identity"]["category"] == "other"
