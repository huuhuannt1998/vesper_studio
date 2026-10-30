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


def scene_setup(tmp_path):
    from tests.lab.homes import catalog
    from tests.lab.test_world3d import scene_home
    from tests.twins.conftest import write_catalog
    (tmp_path / "home").mkdir()
    return write_catalog(tmp_path / "vds", list(catalog().values())), scene_home(tmp_path / "home").path


def play_output(tmp_path, monkeypatch, capsys, *extra):
    cat, home = (scene_setup if "scene" in extra else setup)(tmp_path)
    seen = {}

    def interrupted():
        seen["out"] = capsys.readouterr().out
        base = re.search(r"http://127\.0\.0\.1:\d+/", seen["out"]).group(0)
        try:
            seen["render"] = json.loads(get(base + "world/render.json"))
        except Exception:  # noqa: BLE001 - a 404: no 3D scene
            seen["render"] = None
    monkeypatch.setattr(cli, "_wait_for_interrupt", interrupted)
    args = [a for a in extra if a != "scene"]
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", *args]) == 0
    return seen, home


def test_play_draws_a_home_made_from_a_scene_from_the_scene_s_files(tmp_path, capsys, monkeypatch):
    seen, home = play_output(tmp_path, monkeypatch, capsys, "scene")
    assert f"3D view: test/house, 4 meshes from {home.parent / 'hssd'}" in seen["out"]
    assert "1 of its 3 files is missing (objects/openings/218-20.glb)" in seen["out"]
    assert len(seen["render"]["items"]) == 4


def test_play_takes_hssd_from_where_it_is_told(tmp_path, capsys, monkeypatch):
    seen, _ = play_output(tmp_path, monkeypatch, capsys, "scene", "--hssd", str(tmp_path / "elsewhere"))
    assert f"4 meshes from {tmp_path / 'elsewhere'}" in seen["out"] and "3 of its 3 files are missing" in seen["out"]


def test_play_draws_a_home_without_a_scene_from_its_map(tmp_path, capsys, monkeypatch):
    seen, _ = play_output(tmp_path, monkeypatch, capsys)
    assert "3D view: drawn from the map" in seen["out"] and seen["render"] is None


def test_play_with_a_broken_render_manifest_says_so_and_draws_the_map(tmp_path, capsys, monkeypatch):
    cat, home = scene_setup(tmp_path)
    (home.parent / "render.json").write_text("{")
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "3D view: cannot read" in out and "the page draws the home from its map" in out


def test_play_draws_the_people_as_habitat_s_humanoids_when_they_are_there(tmp_path, capsys, monkeypatch):
    from tests.lab.test_humanoids import fake_python, humanoid_data
    monkeypatch.setenv("VESPER_HUMANOIDS", str(humanoid_data(tmp_path / "humanoid_data")))
    monkeypatch.setenv("VESPER_HABITAT_PYTHON", str(fake_python(tmp_path)))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    seen, _ = play_output(tmp_path, monkeypatch, capsys)
    assert "people: Habitat's humanoids (female_0)" in seen["out"]
    assert (tmp_path / "cache" / "vesper" / "humanoids" / "female_0.motion.json").is_file()


def test_play_without_habitat_s_humanoids_draws_plain_figures_and_says_why(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("VESPER_HUMANOIDS", str(tmp_path / "nowhere"))
    seen, _ = play_output(tmp_path, monkeypatch, capsys)
    assert "people: plain figures (Habitat's humanoids are not at" in seen["out"]


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
    fake, launched = FakeBrowser(), {}
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch",
                        classmethod(lambda cls, path=None, **kw: launched.update(kw) or fake))
    out = tmp_path / "look"
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(steps), "--out", str(out)]) == 0
    assert launched["timeout"] >= 60  # a scene's meshes keep a headless page busy for tens of seconds
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

    def ensure_tasmota(self):
        self.events.append("ensure_tasmota")

    def lab_login(self):
        self.events.append("login")
        return "vesper", "s3cret-lab-login"

    def read_automations(self):
        return self.automations if self.edited is None else self.edited

    def stop(self):
        self.events.append("stop")


CONNECT_KWARGS = []  # what each connect was given beyond host, port and id


def stack_fakes(monkeypatch):
    from tests.exposure.fake_mqtt import FakeClient
    from vesper.exposure import mqtt as mqtt_mod
    from vesper.validation import stack as stack_mod
    LabMosquitto.made, LabHA.made, LabHA.fail, LabHA.watch, LabHA.traces_at_start = [], [], None, None, None
    monkeypatch.setattr(stack_mod, "Mosquitto", LabMosquitto)
    monkeypatch.setattr(stack_mod, "HomeAssistant", LabHA)
    clients = []
    CONNECT_KWARGS.clear()

    def connect(*a, **k):
        CONNECT_KWARGS.append((a, k))
        clients.append((a, FakeClient()))
        return clients[-1][1]
    monkeypatch.setattr(mqtt_mod, "connect", connect)
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
    # Home Assistant and the bridge reach the broker through the home's network: the relay's hub and coordinator
    # links, on ports of their own (security-lab spec §3.4), never the broker's directly
    (_, hub_port), ((_, coord_port, name), _) = ha.events[2], clients[0]
    assert ha.events[:2] == ["start", "onboard"] and ha.events[2][0] == "ensure_mqtt" and ha.events[3:] == [
        "login", "stop"]
    assert name == "vesper-lab-bridge" and len({hub_port, coord_port, 18830}) == 3 and clients[-1][0] == "disconnected"
    assert f"network: Home Assistant's MQTT through the relay's hub link (port {hub_port}), the bridge's through its " \
           f"coordinator link (port {coord_port})" in seen["out"]
    assert "log in as vesper, password s3cret-lab-login" in seen["out"]
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


def test_lab_network_shows_the_router_each_link_and_what_is_not_on_the_network(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "network", str(home), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "router: hall (1.5, 3.0); the hub is wired to it; the Zigbee coordinator is on the hub" in out
    assert "assumed" in out  # the radio constants are typical values, not measured
    lamp = next(line for line in out.splitlines() if line.strip().startswith("zigbee/bedroom_lamp"))
    assert "dBm" in lamp and "1 wall" in lamp and "ms" in lamp
    assert "front_lock: a Matter device: on the network with plan W7a3" in out


def test_play_with_the_hub_puts_each_wi_fi_device_on_its_own_connection_through_its_own_link(tmp_path, capsys,
                                                                                             monkeypatch):
    """A virtual Tasmota device connects as a real one: its own client (Tasmota's keepalive, its retained will),
    through its own `wifi/<name>` relay link; Home Assistant's Tasmota integration is set up to hear it."""
    from tests.lab.homes import with_wifi, write_home
    from vesper.lab.tasmota_hub import KEEPALIVE_S
    cat, _ = setup(tmp_path)
    (tmp_path / "wifi").mkdir()
    home = write_home(tmp_path / "wifi", with_wifi())
    clients = stack_fakes(monkeypatch)
    seen = {}
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: seen.setdefault("out", capsys.readouterr().out))
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper")]) == 0
    (ha,) = LabHA.made
    assert "ensure_tasmota" in ha.events
    opened = {c[0][2]: c for c in clients if c[0] != "disconnected"}  # client id -> (args, client)
    wifi = {cid: args for cid, (args, _) in opened.items() if cid.startswith("tasmota_")}
    assert len(wifi) == 2
    ports = {args[1] for args in wifi.values()}
    bridge_port = opened["vesper-lab-bridge"][0][1]
    assert len(ports) == 2 and bridge_port not in ports and 18830 not in ports  # each through its own link
    for cid, (args, client) in ((cid, opened[cid]) for cid in wifi):
        kwargs = next(k for a, k in CONNECT_KWARGS if a == args)
        assert kwargs["will"] == (f"tele/{cid}/LWT", "Offline") and kwargs["keepalive"] == KEEPALIVE_S
        assert client.raw[-1] == (f"tele/{cid}/LWT", "Offline", True)  # stopped: offline
    assert sum(1 for c in clients if c[0] == "disconnected") == 3
    assert "kettle_plug: switch.kettle_plug" in seen["out"] and "reading_lamp: light.reading_lamp" in seen["out"]
    assert "Wi-Fi devices through their own links" in seen["out"]
