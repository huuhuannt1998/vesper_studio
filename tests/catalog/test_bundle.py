"""The released catalog (plan W5): a bundle installs only with its SHA-256, only its own files, each matching the
manifest."""
import io
import json
import tarfile

import pytest

from vesper.catalog_bundle import BundleError, bundle, fetch


def catalog(tmp_path):
    c = tmp_path / "vds"
    c.mkdir(parents=True)
    (c / "zigbee.json").write_text("[]")
    (c / "build_report.json").write_text(json.dumps({"code_sha256": "ab" * 32, "inputs": {}}))
    return c


def test_a_bundle_installs_and_is_identical(tmp_path):
    path, digest = bundle(release_catalog(tmp_path), tmp_path / "b.tar.gz")
    manifest = fetch(str(path), digest, tmp_path / "out")
    assert (tmp_path / "out" / "zigbee.json").read_text() == "[]" and set(manifest["files"]) == {
        "zigbee.json", "build_report.json"}
    assert bundle(release_catalog(tmp_path / "again"), tmp_path / "c.tar.gz")[1] == digest  # reproducible bytes


def test_a_wrong_hash_or_a_foreign_member_is_refused(tmp_path):
    path, digest = bundle(release_catalog(tmp_path), tmp_path / "b.tar.gz")
    with pytest.raises(BundleError, match="SHA-256"):
        fetch(str(path), "0" * 64, tmp_path / "out")
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("vds/../../escape.json")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"{}"))
    from vesper.catalog_bundle import sha256
    with pytest.raises(BundleError, match="unexpected member"):
        fetch(str(evil), sha256(evil.read_bytes()), tmp_path / "out")
    assert not (tmp_path / "escape.json").exists()
    with pytest.raises(BundleError, match="https"):
        fetch("http://example.com/x.tar.gz", digest, tmp_path / "out")


def release_catalog(tmp_path):
    c = catalog(tmp_path)
    from vesper.catalog.fingerprint import code_fingerprint
    (c / "build_report.json").write_text(json.dumps({"code_sha256": code_fingerprint(), "inputs": {},
                                                     "release": True}))
    return c


def test_only_a_release_build_is_bundled(tmp_path):
    with pytest.raises(BundleError, match="--release"):
        bundle(catalog(tmp_path / "a"), tmp_path / "a.tar.gz")
    path, digest = bundle(release_catalog(tmp_path / "b"), tmp_path / "b.tar.gz")
    assert digest


def test_fetch_never_deletes_a_folder_that_is_not_a_catalog(tmp_path):
    path, digest = bundle(release_catalog(tmp_path / "b"), tmp_path / "b.tar.gz")
    home = tmp_path / "home"
    home.mkdir()
    (home / "thesis.tex").write_text("precious")
    with pytest.raises(BundleError, match="not a catalog"):
        fetch(str(path), digest, home)
    assert (home / "thesis.tex").read_text() == "precious"


def test_a_bundle_from_other_catalog_code_is_refused_and_the_old_catalog_kept(tmp_path):
    c = catalog(tmp_path / "old")
    c2 = catalog(tmp_path / "other")
    (c2 / "build_report.json").write_text(json.dumps({"code_sha256": "0" * 64, "inputs": {}, "release": True}))
    path, digest = bundle(c2, tmp_path / "o.tar.gz")
    with pytest.raises(BundleError, match="other catalog code"):
        fetch(str(path), digest, c)
    assert (c / "zigbee.json").read_text() == "[]"
