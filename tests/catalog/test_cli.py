import json

import pytest

from vesper import cli
from vesper.catalog.errors import IngestError


def test_build_command(raw_root, tmp_path, capsys):
    out = tmp_path / "vds"
    assert cli.main(["catalog", "build", "--raw-root", str(raw_root), "--out", str(out)]) == 0
    assert "built 21 records" in capsys.readouterr().out
    assert len(json.loads((out / "zigbee.json").read_text())) == 11


def test_build_fails_loudly_when_a_source_was_never_synced(tmp_path, capsys):
    from tests.catalog.conftest import write_all

    raw = write_all(tmp_path / "raw", skip=("dcl",))
    assert cli.main(["catalog", "build", "--raw-root", str(raw), "--out", str(tmp_path / "vds")]) == 1
    assert "SKIPPED matter:" in capsys.readouterr().err


def test_build_without_home_assistant_vocabulary_fails(tmp_path, capsys):
    assert cli.main(["catalog", "build", "--raw-root", str(tmp_path), "--out", str(tmp_path / "vds")]) == 1
    assert "FAILED build:" in capsys.readouterr().err


def test_sync_runs_every_source_and_reports_failures(tmp_path, monkeypatch, capsys):
    ran = []

    def fake(source, raw_root, cache_root, client):
        ran.append(source)
        if source == "dcl":
            raise IngestError("ledger down")
        return raw_root / source

    monkeypatch.setattr(cli, "_sync_one", fake)
    assert cli.main(["catalog", "sync", "--raw-root", str(tmp_path)]) == 1
    assert ran == ["ha", "zigbee", "matter_zap", "dcl", "zwave"]
    assert "FAILED dcl: ledger down" in capsys.readouterr().err


def test_sync_one_source(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_sync_one", lambda s, r, c, client: r / s)
    assert cli.main(["catalog", "sync", "--source", "ha", "--raw-root", str(tmp_path)]) == 0


def test_unknown_source_is_a_usage_error():
    with pytest.raises(SystemExit) as e:
        cli.main(["catalog", "sync", "--source", "data_model"])
    assert e.value.code == 2
