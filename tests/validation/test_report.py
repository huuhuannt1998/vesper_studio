import json

from tests.twins.conftest import records
from vesper.validation.report import build_report, latest, render_markdown


def write_catalog(tmp_path):
    out = tmp_path / "vds"
    out.mkdir()
    recs = list(records().values())
    (out / "zigbee.json").write_text(json.dumps(recs))
    l1 = sum(r["fidelity"]["reached"]["L1"] for r in recs)
    (out / "coverage.json").write_text(json.dumps({"zigbee": {"L0": len(recs), "L1": l1, "L2": 0, "L3": 0, "L4": 0,
                                                              "by_category": {}}}))
    (out / "build_report.json").write_text(json.dumps({"inputs": {"zigbee": {"version": "26.123.0",
                                                                             "manifest_sha256": "ab" * 32}}}))
    return out, recs


def write_result(results, name, payload):
    results.mkdir(parents=True, exist_ok=True)
    (results / name).write_text(json.dumps(payload))


def test_the_latest_result_of_each_kind_is_used(tmp_path):
    r = tmp_path / "results"
    write_result(r, "rules-20261024T100000Z.json", {"passed": False})
    write_result(r, "rules-20261024T120000Z.json", {"passed": True})
    name, data = latest(r, "rules")
    assert name == "rules-20261024T120000Z.json" and data == {"passed": True}
    assert latest(r, "acceptance") is None


def test_l2_counts_only_products_that_passed_acceptance(tmp_path):
    catalog, recs = write_catalog(tmp_path)
    results = tmp_path / "results"
    write_result(results, "acceptance-20261024T170000Z.json", {
        "summary": {"products": 2, "passed": 1, "categories_passing": ["plug"], "categories_displayed": ["plug"],
                    "by_category": {}},
        "products": [{"id": recs[0]["id"], "category": "plug", "passed": True},
                     {"id": recs[1]["id"], "category": "light", "passed": False}]})
    report = build_report(catalog, results)
    assert report["l2"]["validated_products"] == [recs[0]["id"]]
    assert report["l2"]["eligible_zigbee"] >= 1 and report["sources"]["zigbee"]["version"] == "26.123.0"
    assert report["checks"]["acceptance"]["file"] == "acceptance-20261024T170000Z.json"
    assert report["checks"]["roundtrip"] is None  # not run yet: shown as missing, not as passed


def test_the_markdown_names_assumptions_and_missing_checks(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    md = render_markdown(build_report(catalog, tmp_path / "results"))
    for heading in ("## Sources", "## Coverage", "## Assumed", "## Checks", "## Timing paths"):
        assert heading in md
    assert "not run yet" in md and "behavior.reporting.state" in md and "sleepy_downlink" in md
