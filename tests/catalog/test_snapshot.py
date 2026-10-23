import json

import pytest

from vesper.catalog.errors import SnapshotError
from vesper.catalog.snapshot import current_version, load_snapshot, write_snapshot
from vesper.catalog.sources import SOURCES, source_entry


def test_round_trip_records_hashes_and_current(tmp_path):
    d = write_snapshot(tmp_path, "zigbee", "1.0.0", {"definitions.json": b"[]", "sub/x.xml": b"<a/>"},
                       license="MIT", origin="test", extra={"definitions": 0})
    meta = json.loads((d / "snapshot.json").read_text())
    assert (meta["source"], meta["version"], meta["license"], meta["definitions"]) == ("zigbee", "1.0.0", "MIT", 0)
    assert set(meta["files"]) == {"definitions.json", "sub/x.xml"}
    assert current_version(tmp_path, "zigbee") == "1.0.0"
    meta2, files = load_snapshot(tmp_path, "zigbee")
    assert files == {"definitions.json": b"[]", "sub/x.xml": b"<a/>"}
    assert meta2 == meta


def test_extra_cannot_overwrite_the_manifest_fields(tmp_path):
    d = write_snapshot(tmp_path, "ha", "1", {"a": b"1"}, license="MIT", origin="t", extra={"files": {}, "version": "x"})
    meta = json.loads((d / "snapshot.json").read_text())
    assert meta["version"] == "1" and set(meta["files"]) == {"a"}


def test_tampered_file_is_refused(tmp_path):
    d = write_snapshot(tmp_path, "ha", "2026.10.2", {"a.py": b"x = 1"}, license="Apache-2.0", origin="test")
    (d / "a.py").write_bytes(b"x = 2")
    with pytest.raises(SnapshotError, match="changed since sync"):
        load_snapshot(tmp_path, "ha")


def test_missing_source_names_the_sync_command(tmp_path):
    with pytest.raises(SnapshotError, match="vesper catalog sync --source dcl"):
        load_snapshot(tmp_path, "dcl")


@pytest.mark.parametrize("version,name", [("..", "a"), ("1.0", "../escape"), ("1.0", "/abs"), ("a/b", "x")])
def test_unsafe_paths_are_refused(tmp_path, version, name):
    with pytest.raises(SnapshotError, match="unsafe"):
        write_snapshot(tmp_path, "zigbee", version, {name: b""}, license="MIT", origin="test")


def test_failed_write_leaves_previous_current_untouched(tmp_path):
    write_snapshot(tmp_path, "zigbee", "1.0.0", {"d.json": b"1"}, license="MIT", origin="t")

    class Boom(dict):
        def items(self):
            raise OSError("disk full")

    with pytest.raises(OSError):
        write_snapshot(tmp_path, "zigbee", "2.0.0", Boom(x=b"2"), license="MIT", origin="t")
    assert current_version(tmp_path, "zigbee") == "1.0.0"


def test_matter_comes_only_from_the_apache_zap_xml():
    s = SOURCES["matter_zap"]
    assert s.license == "Apache-2.0" and "zap-templates" in s.origin and "data_model" not in s.origin


def test_dcl_is_not_redistributable_until_its_terms_are_checked():
    assert source_entry("dcl", "height-1") == {
        "source": "dcl", "version": "height-1", "license": "DCL-terms-unverified", "redistributable": False}


def test_an_interrupted_resync_of_the_same_version_keeps_the_last_good_snapshot(tmp_path):
    write_snapshot(tmp_path, "zigbee", "1.0.0", {"a.json": b"good-a", "b.json": b"good-b"}, license="MIT", origin="t")
    with pytest.raises(TypeError):  # the second file fails after the first was written
        write_snapshot(tmp_path, "zigbee", "1.0.0", {"a.json": b"new-a", "b.json": None}, license="MIT", origin="t")
    assert load_snapshot(tmp_path, "zigbee")[1] == {"a.json": b"good-a", "b.json": b"good-b"}
    write_snapshot(tmp_path, "zigbee", "1.0.0", {"a.json": b"new-a"}, license="MIT", origin="t")
    assert load_snapshot(tmp_path, "zigbee")[1] == {"a.json": b"new-a"}
    assert sorted(p.name for p in (tmp_path / "zigbee").iterdir()) == ["1.0.0", "CURRENT"]
