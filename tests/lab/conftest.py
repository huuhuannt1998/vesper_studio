"""The Lab's tests never reach this machine's Habitat humanoids, its caches or its HSSD setting (playing a home would
otherwise convert them with the real Habitat environment, or draw another scene folder): each test gets none, unless
it sets its own. The opt-in live tests read VESPER_HSSD when they are collected, and give it explicitly."""
import pytest


@pytest.fixture(autouse=True)
def no_machine_humanoids(tmp_path, monkeypatch):
    monkeypatch.setenv("VESPER_HUMANOIDS", str(tmp_path / "no-humanoids"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("VESPER_HSSD", raising=False)
