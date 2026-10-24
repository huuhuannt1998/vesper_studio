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


def test_custom_named_and_unreadable_results_do_not_break_the_report(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "rules-20261024T100000Z.json", {"passed": True, "products": 3, "violation_count": 0})
    write_result(r, "rules-final.json", {"passed": False})  # not a timestamped result: ignored
    r.joinpath("latency-20261024T110000Z.json").write_text('{"truncated": ')
    report = build_report(catalog, r)
    assert report["checks"]["rules"]["file"] == "rules-20261024T100000Z.json"
    assert report["checks"]["latency"] == {"file": "latency-20261024T110000Z.json", "unreadable": True,
                                           "passed": False}
    assert "unreadable" in render_markdown(report)


def test_a_run_that_differs_from_the_preregistration_is_exploratory(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "rules-20261024T100000Z.json", {"passed": True, "violation_count": 0,
                                                    "run": {"parameters": {"seed": 7, "hours": 4.0}}})
    assert build_report(catalog, r)["checks"]["rules"]["exploratory"] is True
    write_result(r, "rules-20261024T110000Z.json", {"passed": True, "violation_count": 0,
                                                    "run": {"parameters": {"seed": 0, "hours": 4.0}}})
    assert build_report(catalog, r)["checks"]["rules"]["exploratory"] is False
    write_result(r, "rules-20261024T120000Z.json", {"passed": True, "violation_count": 0})  # no provenance
    assert build_report(catalog, r)["checks"]["rules"]["exploratory"] is True


def test_a_run_over_nothing_does_not_pass(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "acceptance-20261024T100000Z.json", {
        "summary": {"products": 0, "passed": 0, "categories_passing": [], "categories_displayed": [],
                    "by_category": {}}, "products": []})
    write_result(r, "discovery-20261024T100000Z.json", {"summary": {"components": 0, "accepted": 0, "devices": 0,
                                                                    "devices_fully_accepted": 0, "errors": {}}})
    checks = build_report(catalog, r)["checks"]
    assert checks["acceptance"]["passed"] is False and checks["discovery"]["passed"] is False


def test_the_latency_and_rules_rows_carry_their_numbers_and_assumptions(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "latency-20261024T100000Z.json", {
        "passed": True, "overhead_s": 0.004, "generator": {"local": {"passed": True}},
        "calibration": {"passed": True, "median_s": 0.006, "p95_s": 0.012},
        "paths": {"local": {"residual": {"median_s": 0.006, "p95_s": 0.013, "passed": True},
                            "described": {"median_s": 0.115, "published_median_s": 0.1145}}}})
    write_result(r, "rules-20261024T100000Z.json", {"passed": True, "products": 3, "violation_count": 0,
                                                    "reports": 10, "r4_checked": 2})
    md = render_markdown(build_report(catalog, r))
    assert "residual median 6.0 ms" in md and "assumed" in md.split("## Checks")[1]


def test_a_report_over_a_stale_catalog_says_so_first(tmp_path):
    catalog, _ = write_catalog(tmp_path)  # its build report carries no code fingerprint
    report = build_report(catalog, tmp_path / "results")
    assert "predates" in report["catalog_stale"]
    assert render_markdown(report).splitlines()[2].startswith("**Warning: the catalog is stale")


def test_real_device_results_count_as_preregistered_only_on_the_pinned_dataset_commit(tmp_path):
    from vesper.validation.realdata import ZIOTP_COMMIT
    catalog, _ = write_catalog(tmp_path)
    results = tmp_path / "results"
    base = {"passed": True, "judged": 7, "judged_passed": 7, "eligible": 9, "decryption_ok": True,
            "rule_window_ok": True, "run": {"parameters": {"dataset": "x"}}}
    write_result(results, "realdata-20261025T100000Z.json", {**base, "dataset_commit": ZIOTP_COMMIT,
                                                              "dataset_dirty": False})
    row = build_report(catalog, results)["checks"]["realdata"]
    assert row["passed"] and not row["exploratory"] and row["judged_passed"] == 7
    assert "7/7 judged streams reproduced" in render_markdown(build_report(catalog, results))
    write_result(results, "realdata-20261025T110000Z.json", {**base, "dataset_commit": "f" * 40,
                                                              "dataset_dirty": False})
    assert build_report(catalog, results)["checks"]["realdata"]["exploratory"]
    write_result(results, "realdata-20261025T120000Z.json", {**base, "dataset_commit": ZIOTP_COMMIT,
                                                              "dataset_dirty": True})  # a changed checkout
    assert build_report(catalog, results)["checks"]["realdata"]["exploratory"]


def matter_result(products, *, per_type=1, vid_mode="product"):
    from vesper.validation.matter_acceptance import summarize_matter
    return {"summary": summarize_matter(products), "products": products, "vid_mode": vid_mode,
            "run": {"parameters": {"seed": 0, "per_type": per_type, "vid_mode": vid_mode}}}


MATTER_PRODUCTS = [
    {"id": "matter:100b:0016", "device_type": "Contact Sensor", "category": "contact_sensor", "passed": True,
     "exercised": True},
    {"id": "matter:115f:0001", "device_type": "Door Lock", "category": "lock", "passed": False, "exercised": True}]


def test_matter_l2_counts_only_matter_products_that_passed(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    cov = json.loads((catalog / "coverage.json").read_text())
    cov["matter"] = {"L0": 5, "L1": 4, "L2": 0, "L3": 0, "L4": 0, "by_category": {}}
    (catalog / "coverage.json").write_text(json.dumps(cov))
    r = tmp_path / "results"
    write_result(r, "matter-20261025T100000Z.json", matter_result(MATTER_PRODUCTS))
    report = build_report(catalog, r)
    assert report["l2"]["matter"]["validated_products"] == ["matter:100b:0016"]
    assert report["l2"]["validated_products"] == []  # Zigbee's credit is untouched
    c = report["checks"]["matter"]
    assert (c["passed"], c["products"], c["products_passed"]) == (False, 2, 1)
    assert c["device_types_passing"] == ["Contact Sensor"] and c["exploratory"] is False
    md = render_markdown(report)
    assert "| matter | 5 | 4 | 1 | 0 | 0 |" in md
    assert "| matter | FAIL | 1/2 products; device types fully passing 1/2; product vendor ids |" in md


def test_a_matter_run_with_test_vendor_ids_or_a_category_subset_is_exploratory(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "matter-20261025T100000Z.json", matter_result(MATTER_PRODUCTS[:1], vid_mode="test"))
    assert build_report(catalog, r)["checks"]["matter"]["exploratory"] is True
    sub = matter_result(MATTER_PRODUCTS[:1])
    sub["run"]["parameters"]["category"] = ["lock"]
    write_result(r, "matter-20261025T110000Z.json", sub)
    assert build_report(catalog, r)["checks"]["matter"]["exploratory"] is True


def test_a_matter_run_with_other_parameters_is_exploratory_and_test_vendor_ids_are_named(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "matter-20261025T100000Z.json", matter_result(MATTER_PRODUCTS[:1], per_type=2, vid_mode="test"))
    report = build_report(catalog, r)
    assert report["checks"]["matter"]["exploratory"] is True and report["checks"]["matter"]["passed"] is True
    assert "Matter's test vendor id" in render_markdown(report)
