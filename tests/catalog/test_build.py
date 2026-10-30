import json

import pytest

from tests.catalog.conftest import write_all
from tests.catalog.fixtures import TASMOTA_PIN
from vesper.catalog import build as build_mod
from vesper.catalog.build import build_catalog, link_products
from vesper.catalog.errors import CatalogError
from vesper.catalog.normalize.common import new_record
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import source_entry


def load(out, protocol):
    return json.loads((out / f"{protocol}.json").read_text())


def test_build_writes_catalog_coverage_and_report(raw_root, tmp_path):
    out = tmp_path / "vds"
    report = build_catalog(raw_root, out)
    assert report["skipped_sources"] == {} and report["release"] is False
    assert len(load(out, "zigbee")) == 13 and len(load(out, "matter")) == 7 and len(load(out, "zwave")) == 3
    assert len(load(out, "tasmota")) == 3
    assert sorted(x["file"].rsplit("/", 1)[1] for x in report["rejected"]) == [
        "acme_sensor_board", "brite_bulb_rgbcct", "broken.json", "nolabel.json", "not_front_matter",
        "old_numbering_plug"]
    assert report["linked"] == []
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["matter"]["total"] == 7 and cov["matter"]["by_category"]["contact_sensor"]["L1"] == 2
    assert (out / "coverage.csv").read_text().startswith("protocol,category,total,L0,L1,L2,L3,L4\n")


def test_build_is_deterministic(raw_root, tmp_path):
    build_catalog(raw_root, tmp_path / "a")
    build_catalog(raw_root, tmp_path / "b")
    for name in ("zigbee.json", "matter.json", "zwave.json", "tasmota.json", "coverage.json", "coverage.csv",
                 "build_report.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def test_a_missing_source_is_reported_and_leaves_no_stale_file(tmp_path):
    raw = write_all(tmp_path / "raw", skip=("zwave",))
    out = tmp_path / "vds"
    out.mkdir()
    (out / "zwave.json").write_text('[{"stale": true}]')
    report = build_catalog(raw, out)
    assert "vesper catalog sync --source zwave" in report["skipped_sources"]["zwave"]
    assert load(out, "zwave") == []


def test_release_build_keeps_only_redistributable_records(raw_root, tmp_path):
    report = build_catalog(raw_root, tmp_path / "vds", release=True)
    assert report["release"] is True
    assert load(tmp_path / "vds", "matter") == []  # DCL reuse terms are not verified yet
    assert len(load(tmp_path / "vds", "zigbee")) == 13


def test_an_invalid_record_is_rejected_with_its_reason(raw_root, tmp_path, monkeypatch):
    real = build_mod.PRODUCERS["zigbee"]

    def broken(raw):
        recs, rejected, versions = real(raw)
        recs[0]["identity"]["category"] = "toaster"
        return recs, rejected, versions

    monkeypatch.setitem(build_mod.PRODUCERS, "zigbee", broken)
    report = build_catalog(raw_root, tmp_path / "vds")
    bad = next(x for x in report["rejected"] if x.get("id") == "zigbee:aqara:mccgq11lm")
    assert bad["errors"][0].startswith("identity/category:")


def test_a_crosswalk_class_missing_from_home_assistant_fails_the_build(raw_root, tmp_path):
    write_snapshot(raw_root, "ha", "old", {"binary_sensor_const.py": b"class BinarySensorDeviceClass:\n    A = 'a'\n",
                                           "sensor_const.py": b"class SensorDeviceClass:\n    A = 'a'\n"},
                   license="Apache-2.0", origin="fixture")
    with pytest.raises(CatalogError, match="binary_sensor/door"):
        build_catalog(raw_root, tmp_path / "vds")


def rec(protocol, vendor, model, *, ids=None, record_id=None):
    r = new_record(protocol=protocol, vendor=vendor, model=model, description="", category="light",
                   ids=ids or {}, sources=[source_entry("zigbee", "1")], record_id=record_id)
    r["fidelity"]["reached"].update({"L0": True, "L1": True})
    return r


def test_the_same_product_in_two_protocols_is_linked_not_merged():
    zig = rec("zigbee", "IKEA", "LED2201", ids={"zigbee_model": ["a"]})
    mat = rec("matter", "ikea", "led2201", ids={"matter_vid_pid": [[4476, 1]]}, record_id="matter:117c:0001")
    features = [dict(f) for f in zig["features"]]
    links = link_products([zig, mat])
    assert links == [["matter:117c:0001", "zigbee:ikea:led2201"]]
    assert zig["identity"]["same_product_as"] == ["matter:117c:0001"]
    assert mat["identity"]["same_product_as"] == ["zigbee:ikea:led2201"]
    assert zig["identity"]["ids"] == {"zigbee_model": ["a"]} and zig["features"] == features


def test_names_must_match_exactly_to_link():
    a = rec("zigbee", "HALO Smart Labs", "HALO")
    b = rec("matter", "HALO Smart Labs", "HALO+", record_id="matter:1111:0001")
    assert link_products([a, b]) == [] and "same_product_as" not in a["identity"]


def test_release_keeps_a_zigbee_product_linked_to_a_dcl_record(raw_root, tmp_path):
    from tests.catalog.fixtures import DCL_MODELS, DCL_VENDORS
    models = DCL_MODELS + [{"vid": 4447, "pid": 4, "deviceTypeId": 21, "productName": "MCCGQ11LM", "productLabel": ""}]
    write_snapshot(raw_root, "dcl", "height-2", {"models.json": json.dumps(models).encode(),
                                                 "vendors.json": json.dumps(DCL_VENDORS).encode()},
                   license="DCL-terms-unverified", origin="fixture")
    report = build_catalog(raw_root, tmp_path / "full")
    zig = next(r for r in load(tmp_path / "full", "zigbee") if r["id"] == "zigbee:aqara:mccgq11lm")
    assert zig["identity"]["same_product_as"] == ["matter:115f:0004"] and report["linked"]
    released = build_catalog(raw_root, tmp_path / "release", release=True)
    zig = next(r for r in load(tmp_path / "release", "zigbee") if r["id"] == "zigbee:aqara:mccgq11lm")
    assert "same_product_as" not in zig["identity"] and released["linked"] == []
    assert not any(str(x.get("id", "")).startswith("matter:") for x in released["rejected"])


def test_a_corrupt_snapshot_fails_the_build_and_keeps_the_previous_catalog(raw_root, tmp_path):
    out = tmp_path / "vds"
    build_catalog(raw_root, out)
    before = (out / "zigbee.json").read_bytes()
    (raw_root / "zigbee" / "26.123.0" / "definitions.json").write_text("[]")
    with pytest.raises(CatalogError, match="changed since sync"):
        build_catalog(raw_root, out)
    assert (out / "zigbee.json").read_bytes() == before


def test_inputs_record_every_snapshot_version_and_manifest_digest(raw_root, tmp_path):
    inputs = build_catalog(raw_root, tmp_path / "vds")["inputs"]
    assert {k: v["version"] for k, v in inputs.items()} == {
        "ha": "2026.10.2", "zigbee": "26.123.0", "dcl": "height-1", "matter_zap": "v1.6.1.0", "zwave": "15.31.2",
        "tasmota": TASMOTA_PIN}
    assert all(len(v["manifest_sha256"]) == 64 for v in inputs.values())
