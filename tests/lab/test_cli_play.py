"""`vesper lab play` and `vesper lab look`, with the real play server on a free port and a fake browser."""
import json
import re
import urllib.request

from tests.lab.test_cli_lab import setup
from tests.lab.test_visual import FakeBrowser
from vesper import cli
from vesper.lab import browser as browser_mod
from vesper.lab.browser import BrowserError


def get(url):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as r:
        return r.read()


def test_play_serves_the_home_until_interrupted_and_keeps_its_trace(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    seen = {}

    def interrupted():
        seen["out"] = capsys.readouterr().out
        seen["page"] = get(re.search(r"http://127\.0\.0\.1:\d+/", seen["out"]).group(0))
    monkeypatch.setattr(cli, "_wait_for_interrupt", interrupted)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser"]) == 0
    assert "6 devices" in seen["out"] and b"VESPER Lab" in seen["page"]
    (trace,) = (home.parent / "traces").glob("*.jsonl")
    assert json.loads(trace.read_text().splitlines()[0])["format"] == "vesper-trace/1"


def test_play_opens_the_browser_unless_told_not_to(tmp_path, monkeypatch):
    cat, home = setup(tmp_path)
    opened = []
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0"]) == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")


def test_look_runs_the_steps_and_reports_each_check(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    steps = tmp_path / "look.yaml"
    steps.write_text("- {shot: start}\n- {press: e}\n")
    fake = FakeBrowser()
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(lambda cls, path=None, **kw: fake))
    out = tmp_path / "look"
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(steps), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "PASS the page is ready" in printed and "PASS start is not blank" in printed
    assert f"1 shots and the report in {out}" in printed
    assert fake.calls[0][1].startswith("http://127.0.0.1:") and ("press", "e", 1) in fake.calls
    assert (out / "start.png").is_file() and (out / "report.md").is_file()
    assert len(list(out.glob("*-look-*.jsonl"))) == 1  # the session's trace, beside the shots


def test_look_fails_when_a_check_fails(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    fake = FakeBrowser(problems=["exception: TypeError: draw"])
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(lambda cls, path=None, **kw: fake))
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--out", str(tmp_path / "o")]) == 1
    assert "FAIL no page errors: exception: TypeError: draw" in capsys.readouterr().out


def test_look_explains_a_missing_browser_and_a_bad_steps_file(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)

    def no_browser(cls, path=None, **kw):
        raise BrowserError("no Chromium-based browser found; install Google Chrome or set VESPER_BROWSER")
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(no_browser))
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--out", str(tmp_path / "o")]) == 1
    assert "FAILED: no Chromium-based browser found" in capsys.readouterr().err
    bad = tmp_path / "bad.yaml"
    bad.write_text("- {fly: 1}\n")
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(bad)]) == 1
    assert "FAILED: step 1: unknown action 'fly'" in capsys.readouterr().err


def test_look_needs_a_reference_directory_to_update(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--update-golden"]) == 1
    assert "--update-golden needs --golden" in capsys.readouterr().err
