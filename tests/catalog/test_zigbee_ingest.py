import json
import subprocess
from pathlib import Path

import pytest

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest.zigbee import EXPORT_SCRIPT, sync_zigbee
from vesper.catalog.snapshot import load_snapshot

DOC = {"package": "zigbee-herdsman-converters", "version": "26.123.0", "definitions": [{"model": "M"}]}


def which(name):
    return f"/usr/local/bin/{name}"


def fake_run(doc=DOC, node="v26.3.0", fail_install=False):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=node + "\n", stderr="")
        if cmd[1] == "install":
            if fail_install:
                raise subprocess.CalledProcessError(1, cmd)
            (Path(cmd[cmd.index("--prefix") + 1]) / "package-lock.json").write_text("{}")
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[1].endswith("zhc_export.cjs"):
            Path(cmd[3]).write_text(json.dumps(doc))
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(cmd)

    return run, calls


def test_sync_snapshots_the_export_and_the_lock_file(tmp_path):
    run, calls = fake_run()
    sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)
    meta, files = load_snapshot(tmp_path / "raw", "zigbee")
    assert set(files) == {"definitions.json", "package-lock.json"}
    assert meta["version"] == "26.123.0" and meta["definitions"] == 1 and meta["license"] == "MIT"
    install = next(c for c in calls if c[1] == "install")
    assert "--ignore-scripts" in install and install[-1] == "zigbee-herdsman-converters@26.123.0"


@pytest.mark.parametrize("node", ["v18.19.0", "garbage"])
def test_old_or_unknown_node_is_refused(tmp_path, node):
    run, _ = fake_run(node=node)
    with pytest.raises(IngestError, match="Node.js >= 20"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)
    assert not (tmp_path / "raw").exists()


def test_missing_node_is_refused(tmp_path):
    run, _ = fake_run()
    with pytest.raises(IngestError, match="needs Node.js"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=lambda name: None)


def test_failed_npm_install_is_an_ingest_error(tmp_path):
    run, _ = fake_run(fail_install=True)
    with pytest.raises(IngestError, match="command failed: .*npm install"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)


def test_version_mismatch_is_refused(tmp_path):
    run, _ = fake_run(doc={**DOC, "version": "26.124.0"})
    with pytest.raises(IngestError, match="26.123.0"):
        sync_zigbee(tmp_path / "raw", tmp_path / "cache", run=run, which=which)
    assert not (tmp_path / "raw" / "zigbee" / "CURRENT").exists()


def test_export_script_ships_with_the_package():
    text = EXPORT_SCRIPT.read_text()
    assert "prepareDefinition" in text and "isDummyDevice" in text


def test_a_relative_cache_root_reaches_node_as_an_absolute_path(tmp_path, monkeypatch):
    # Node's require() treats "catalog/cache/..." as a package name, not a path.
    monkeypatch.chdir(tmp_path)
    run, calls = fake_run()
    sync_zigbee(Path("raw"), Path("catalog/cache"), run=run, which=which)
    export = next(c for c in calls if c[1].endswith("zhc_export.cjs"))
    assert Path(export[2]).is_absolute() and Path(export[3]).is_absolute()


def test_export_keeps_raw_fingerprints_and_full_options():
    text = EXPORT_SCRIPT.read_text()
    assert "model_id" not in text and "o.name" not in text  # no renaming, no reduction to names
