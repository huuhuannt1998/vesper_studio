"""A missing Python dependency (an install older than a dependency) is a one-line reason and a fix, not a traceback:
the Studio app shows VESPER's last line of output, so that line has to say what is wrong."""
import builtins

import pytest

from vesper import cli


def _without(monkeypatch, name):
    real = builtins.__import__

    def fake(module, *a, **k):
        if module == name or module.startswith(name + "."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(module, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)


def test_a_missing_dependency_says_which_and_how_to_reinstall(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_main", lambda argv=None: __import__("astral"))
    _without(monkeypatch, "astral")
    assert cli.main(["doctor"]) == 3
    err = capsys.readouterr().err.strip().splitlines()
    assert err[-1].startswith("VESPER cannot start: the Python package 'astral' is missing")
    assert "uv tool install --python 3.12 --editable '.[scan]' --force" in err[-1]


def test_a_missing_module_of_vesper_itself_is_not_hidden(monkeypatch):
    def broken(argv=None):
        raise ModuleNotFoundError("No module named 'vesper.nowhere'", name="vesper.nowhere")
    monkeypatch.setattr(cli, "_main", broken)
    with pytest.raises(ModuleNotFoundError):
        cli.main(["doctor"])
