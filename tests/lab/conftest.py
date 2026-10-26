"""The Lab's tests never reach this machine's Habitat humanoids or its caches (playing a home would otherwise convert
them with the real Habitat environment): each test gets none, unless it sets its own."""
import pytest


@pytest.fixture(autouse=True)
def no_machine_humanoids(tmp_path, monkeypatch):
    monkeypatch.setenv("VESPER_HUMANOIDS", str(tmp_path / "no-humanoids"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
