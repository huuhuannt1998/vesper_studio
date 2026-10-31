"""Every test gets its own VESPER configuration folder: a test that builds a catalog or sets Home Assistant up must
never change the user's ~/.config/vesper."""
import pytest


@pytest.fixture(autouse=True)
def _own_vesper_config(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))
