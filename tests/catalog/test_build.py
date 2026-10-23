import json

import pytest

from tests.catalog.conftest import write_all
from vesper.catalog import build as build_mod
from vesper.catalog.build import build_catalog, dedupe
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
    assert report["inputs"] == {"zigbee": "26.123.0", "dcl": "height-1", "matter_zap": "v1.6.1.0", "zwave": "15.31.2"}
    assert len(load(out, "zigbee")) == 11 and len(load(out, "matter")) == 6 and len(load(out, "zwave")) == 3
    assert sorted(x["file"].rsplit("/", 1)[1] for x in report["rejected"]) == ["broken.json", "nolabel.json"]
    assert report["merged"] == []
    cov = json.loads((out / "coverage.json").read_text())
    assert cov["matter"]["total"] == 6 and cov["matter"]["by_category"]["contact_sensor"]["L1"] == 2
    assert (out / "coverage.csv").read_text().startswith("protocol,category,total,L0,L1,L2,L3,L4\n")


def test_build_is_deterministic(raw_root, tmp_path):
    build_catalog(raw_root, tmp_path / "a")
    build_catalog(raw_root, tmp_path / "b")
    for name in ("zigbee.json", "matter.json", "zwave.json", "coverage.json", "coverage.csv", "build_report.json"):
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
    assert len(load(tmp_path / "vds", "zigbee")) == 11


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


def rec(protocol, vendor, model, *, l1=True, ids=None, record_id=None):
    r = new_record(protocol=protocol, vendor=vendor, model=model, description="", category="light",
                   ids=ids or {}, sources=[source_entry("zigbee", "1")], record_id=record_id)
    r["fidelity"]["reached"].update({"L0": True, "L1": l1})
    return r


def test_dedupe_merges_the_same_product_across_protocols():
    zig = rec("zigbee", "IKEA", "LED2201", l1=False, ids={"zigbee_model": ["a"]})
    mat = rec("matter", "ikea", "led2201", ids={"matter_vid_pid": [[4476, 1]]}, record_id="matter:117c:0001")
    out, merges = dedupe([zig, mat])
    assert len(out) == 1 and merges == [{"kept": "matter:117c:0001", "merged": "zigbee:ikea:led2201"}]
    identity = out[0]["identity"]
    assert identity["protocols"] == ["matter", "zigbee"]
    assert identity["ids"] == {"matter_vid_pid": [[4476, 1]], "zigbee_model": ["a"]}
    assert out[0]["fidelity"]["provenance"]["merged_from"] == "zigbee:ikea:led2201"


def test_dedupe_makes_colliding_ids_unique():
    a = rec("zwave", "AEON Labs", "ZW096", record_id="zwave:0x0086:zw096")
    b = rec("zwave", "Aeotec", "ZW096", record_id="zwave:0x0086:zw096")
    out, merges = dedupe([a, b])
    assert merges == [] and [r["id"] for r in out] == ["zwave:0x0086:zw096", "zwave:0x0086:zw096-2"]


def test_dedupe_keeps_distinct_certified_matter_models_that_share_a_name():
    a = rec("matter", "Hisense", "Air Conditioner", ids={"matter_vid_pid": [[4384, 1]]}, record_id="matter:1120:0001")
    b = rec("matter", "Hisense", "Air Conditioner", ids={"matter_vid_pid": [[4384, 2]]}, record_id="matter:1120:0002")
    zig = rec("zigbee", "Hisense", "Air Conditioner", l1=False, ids={"zigbee_model": ["x"]})
    out, merges = dedupe([a, b, zig])
    assert [r["id"] for r in out] == ["matter:1120:0001", "matter:1120:0002"]
    assert merges == [{"kept": "matter:1120:0001", "merged": "zigbee:hisense:air-conditioner"}]
