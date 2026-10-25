"""`vesper lab play` and `vesper lab look`, with the real play server on a free port and a fake browser."""
import json
import re
import urllib.request

import pytest

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
    assert "PASS the page stayed connected" in printed and "PASS the session raised no errors" in printed
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


RULES = "- alias: lamp on when the hall sees someone\n  triggers: []\n  actions: []\n"


class LabMosquitto:
    made = []

    def __init__(self, root, port=18830, **kwargs):
        self.port, self.events = port, []
        LabMosquitto.made.append(self)

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


class LabHA:
    made, fail, watch, traces_at_start = [], None, None, None

    def __init__(self, root, port=18123, frameable=False, automations=None, **kwargs):
        self.port, self.frameable, self.automations, self.events, self.edited = port, frameable, automations, [], None
        LabHA.made.append(self)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.events.append("start")
        if LabHA.watch is not None:  # what the session had written when Home Assistant started
            LabHA.traces_at_start = sorted(LabHA.watch.glob("*.jsonl"))
        if LabHA.fail:
            raise LabHA.fail

    def onboard(self):
        self.events.append("onboard")

    def ensure_mqtt(self, port):
        self.events.append(("ensure_mqtt", port))

    def lab_login(self):
        self.events.append("login")
        return "vesper", "s3cret-lab-login"

    def read_automations(self):
        return self.automations if self.edited is None else self.edited

    def stop(self):
        self.events.append("stop")


def stack_fakes(monkeypatch):
    from tests.exposure.fake_mqtt import FakeClient
    from vesper.exposure import mqtt as mqtt_mod
    from vesper.validation import stack as stack_mod
    LabMosquitto.made, LabHA.made, LabHA.fail, LabHA.watch, LabHA.traces_at_start = [], [], None, None, None
    monkeypatch.setattr(stack_mod, "Mosquitto", LabMosquitto)
    monkeypatch.setattr(stack_mod, "HomeAssistant", LabHA)
    clients = []
    monkeypatch.setattr(mqtt_mod, "connect", lambda *a, **k: clients.append((a, FakeClient())) or clients[-1][1])
    monkeypatch.setattr(mqtt_mod, "disconnect", lambda c: clients.append(("disconnected", c)))
    return clients


def test_play_with_the_hub_runs_broker_and_home_assistant_and_keeps_the_automations(tmp_path, capsys, monkeypatch):
    from websockets.sync.client import connect as ws_connect
    cat, home = setup(tmp_path)
    (home.parent / "automations.yaml").write_text(RULES)
    home.write_text(home.read_text() + "automations: automations.yaml\n")
    clients = stack_fakes(monkeypatch)
    seen = {}

    def interrupted():
        seen["out"] = capsys.readouterr().out
        port = re.search(r"http://127\.0\.0\.1:(\d+)/", seen["out"]).group(1)
        with ws_connect(f"ws://127.0.0.1:{port}/ws", proxy=None, origin=f"http://127.0.0.1:{port}") as ws:
            seen["hub"] = json.loads(ws.recv(timeout=5))["hub"]
        LabHA.made[0].edited = RULES + "- alias: added in the UI\n"
    monkeypatch.setattr(cli, "_wait_for_interrupt", interrupted)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 0
    (ha,), (broker,) = LabHA.made, LabMosquitto.made
    assert broker.port == 18830 and broker.events == ["start", "stop"]
    assert ha.frameable and ha.automations == RULES
    assert ha.events == ["start", "onboard", ("ensure_mqtt", 18830), "login", "stop"]
    assert "log in as vesper, password s3cret-lab-login" in seen["out"]
    assert clients[0][0][:3] == ("127.0.0.1", 18830, "vesper-lab-bridge") and clients[-1][0] == "disconnected"
    assert seen["hub"] == "http://127.0.0.1:18123"
    assert "bedroom_lamp: light." in seen["out"] and "front_lock: not on the hub (Matter: plan W2d)" in seen["out"]
    assert (home.parent / "automations.yaml").read_text() == RULES + "- alias: added in the UI\n"
    assert "automations.yaml updated" in capsys.readouterr().out


def test_play_with_the_hub_stops_what_it_started_when_home_assistant_fails(tmp_path, capsys, monkeypatch):
    from vesper.validation.stack import StackError
    cat, home = setup(tmp_path)
    stack_fakes(monkeypatch)
    LabHA.fail = StackError("Home Assistant is not installed; run `vesper setup ha` first")
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 1
    assert "FAILED: Home Assistant is not installed" in capsys.readouterr().err
    assert LabMosquitto.made[0].events == ["start", "stop"] and LabHA.made[0].events == ["start", "stop"]


def test_play_with_the_hub_names_a_missing_automations_file_before_starting_anything(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    home.write_text(home.read_text() + "automations: missing.yaml\n")
    stack_fakes(monkeypatch)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 1
    assert "missing.yaml" in capsys.readouterr().err and LabMosquitto.made == []


def test_look_with_the_hub_drives_the_page_with_home_assistant_in_the_loop(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    clients = stack_fakes(monkeypatch)
    fake = FakeBrowser()
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(lambda cls, path=None, **kw: fake))
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--out", str(tmp_path / "o"), "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 0
    assert LabMosquitto.made[0].events == ["start", "stop"] and LabHA.made[0].events[-1] == "stop"
    assert clients[-1][0] == "disconnected" and "PASS the session raised no errors" in capsys.readouterr().out


def test_the_session_and_its_clock_start_after_home_assistant_is_up(tmp_path, monkeypatch):
    cat, home = setup(tmp_path)
    stack_fakes(monkeypatch)
    LabHA.watch = home.parent / "traces"
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 0
    assert LabHA.traces_at_start == []  # no session yet: its time starts once Home Assistant has started
    (trace,) = (home.parent / "traces").glob("*.jsonl")
    assert json.loads(trace.read_text().splitlines()[0])["kind"] == "meta"


def test_a_stop_signal_while_home_assistant_starts_stops_the_broker_too(tmp_path, monkeypatch):
    cat, home = setup(tmp_path)
    stack_fakes(monkeypatch)
    LabHA.fail = SystemExit(143)  # what SIGTERM becomes
    with pytest.raises(SystemExit):
        cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                  "--root", str(tmp_path / ".vesper")])
    assert LabMosquitto.made[0].events == ["start", "stop"] and LabHA.made[0].events == ["start", "stop"]


def test_look_with_the_hub_stops_everything_when_the_bridge_cannot_connect(tmp_path, capsys, monkeypatch):
    from vesper.exposure import mqtt as mqtt_mod
    cat, home = setup(tmp_path)
    stack_fakes(monkeypatch)

    def refused(*a, **k):
        raise ConnectionError("no MQTT connection")
    monkeypatch.setattr(mqtt_mod, "connect", refused)
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--out", str(tmp_path / "o"), "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 1
    assert "FAILED: ConnectionError: no MQTT connection" in capsys.readouterr().err
    assert LabMosquitto.made[0].events == ["start", "stop"] and LabHA.made[0].events[-1] == "stop"


def test_automations_from_home_assistant_never_overwrite_a_file_the_home_does_not_name(tmp_path, capsys,
                                                                                       monkeypatch):
    cat, home = setup(tmp_path)
    (home.parent / "automations.yaml").write_text("precious\n")
    stack_fakes(monkeypatch)

    def edited_in_home_assistant():
        LabHA.made[0].edited = RULES
    monkeypatch.setattr(cli, "_wait_for_interrupt", edited_in_home_assistant)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 0
    assert (home.parent / "automations.yaml").read_text() == "precious\n"
    assert (home.parent / "automations-from-home-assistant.yaml").read_text() == RULES
    assert "automations-from-home-assistant.yaml" in capsys.readouterr().out
