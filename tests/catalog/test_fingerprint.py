import json

from vesper.catalog.build import build_catalog
from vesper.catalog.fingerprint import code_fingerprint, staleness


def test_a_build_records_the_fingerprint_of_the_catalog_code(raw_root, tmp_path):
    out = tmp_path / "vds"
    report = build_catalog(raw_root, out)
    assert report["code_sha256"] == code_fingerprint() and len(report["code_sha256"]) == 64
    assert json.loads((out / "build_report.json").read_text())["code_sha256"] == code_fingerprint()
    assert staleness(out) is None


def test_the_fingerprint_follows_source_and_schema_files_only(tmp_path):
    (tmp_path / "normalize").mkdir()
    (tmp_path / "normalize" / "zigbee.py").write_text("CHANGE = 1.0\n")
    (tmp_path / "vds.schema.json").write_text("{}")
    first = code_fingerprint(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (tmp_path / "notes.txt").write_text("not code")
    assert code_fingerprint(tmp_path) == first
    (tmp_path / "normalize" / "zigbee.py").write_text("CHANGE = 100\n")
    assert code_fingerprint(tmp_path) != first


def test_a_catalog_from_other_code_or_without_a_fingerprint_is_stale(tmp_path):
    assert "no build_report.json" in staleness(tmp_path)
    (tmp_path / "build_report.json").write_text(json.dumps({"inputs": {}}))
    assert "predates" in staleness(tmp_path)
    (tmp_path / "build_report.json").write_text(json.dumps({"code_sha256": "0" * 64}))
    assert "other catalog code" in staleness(tmp_path)
