"""The home's Matter devices on Home Assistant (security-lab spec §3.3, plan W7a3): each a matter.js node of
VESPER's Matter host, labelled with its device name and commissioned into Home Assistant through VESPER's Matter
Server; Home Assistant's entity ids, which VESPER's own engine predicts, read back from Home Assistant."""
import threading
from types import SimpleNamespace
import time

from tests.lab.homes import HOME, catalog, write_home
from vesper.exposure.matter_tier import node_spec
from vesper.lab.automations import entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.session import Session
from vesper.twins.clock import WallClock

MATTER = HOME + ('      - {product: "matter:100b:0016", name: back_door, at: {door: bedroom-hall}}\n'
                 '      - {product: "matter:100b:0018", name: hall_light, at: {room: hall, mount: ceiling}}\n')


def home(tmp_path, text=MATTER):
    from tests.catalog.test_matter_normalize import records
    return load_lab_home(write_home(tmp_path, text), {**catalog(), **records()})


def test_a_lab_node_is_labelled_with_its_device_name_which_home_assistant_names_it_by():
    lock = catalog()["matter:115f:0001"]  # noqa: F841 - the lab's lock
    assert node_spec(lock, "matter:115f:0001@front_lock", 0, label="front_lock").label == "front_lock"
    assert node_spec(lock, "matter:115f:0001@front_lock", 0).label.startswith("VESPER 0000")


def test_vesper_predicts_home_assistant_s_matter_entity_ids_by_its_naming(tmp_path):
    ids = entity_ids(home(tmp_path))
    assert ids["front_lock"]["Door Lock.LockState"] == "lock.front_lock"
    assert ids["back_door"]["Boolean State.StateValue"] == "binary_sensor.back_door_door"
    assert ids["hall_light"]["On/Off.OnOff"] == "light.hall_light"


class FakeHost:
    """VESPER's Matter host: answers add, commission, set, remove; `fire` sends an event from its reader thread."""

    def __init__(self):
        self.requests, self.handlers, self.started, self.stopped = [], [], False, False
        self.notes, self.dead = [], None

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def on_event(self, fn):
        self.handlers.append(fn)

    def request(self, op, wait=True, timeout=None, **a):
        self.requests.append((op, a))
        if op == "add":
            return {"resolved": [f["name"] for f in a["features"]], "unresolved": [],
                    "pairing": {"manual": "34970112332", "qr": f"MT:{a['label']}"}}
        if op == "commission":
            return {"node_id": len([r for r in self.requests if r[0] == "commission"])}
        return {}

    def fire(self, event):
        t = threading.Thread(target=lambda: [fn(event) for fn in self.handlers], name="matter-host-reader")
        t.start()
        t.join()


class FakeHA:
    """Home Assistant's Matter entities by node serial, named by the node's label as Home Assistant names them
    (`names`: label -> entity ids; by default its usual naming)."""

    USUAL = {"front_lock": ["lock.front_lock", "select.front_lock_operating_mode"],
             "back_door": ["binary_sensor.back_door_door"],
             "hall_light": ["light.hall_light", "number.hall_light_on_level"]}

    def __init__(self, host, names=None):
        self.host, self.names = host, names or self.USUAL

    def matter_entities(self):
        commissioned = {a["code"][3:] for op, a in self.host.requests if op == "commission"}
        return {a["serial"]: self.names[a["label"]] for op, a in self.host.requests
                if op == "add" and a["label"] in commissioned}

    def state(self, entity_id):
        cls = "door" if entity_id.endswith("_door") else None
        return {"entity_id": entity_id, "state": "off", "attributes": {"device_class": cls} if cls else {}}


def run(tmp_path, names=None):
    from vesper.lab.live import lab_matter
    session = Session(home(tmp_path), clock=WallClock())
    session.start(spawn_player=False)
    host = FakeHost()
    return session, host, lambda: lab_matter(session, "ws://127.0.0.1:15580/ws", FakeHA(host, names), host=host,
                                             timeout=2, poll=0.01)


def test_the_session_s_matter_devices_are_commissioned_and_their_entities_read_back(tmp_path):
    session, host, matter_of = run(tmp_path)
    try:
        with matter_of() as matter:
            adds = [a for op, a in host.requests if op == "add"]
            assert host.started and sorted(a["label"] for a in adds) == ["back_door", "front_lock", "hall_light"]
            assert [op for op, _ in host.requests].count("commission") == 3
            assert matter.entities["front_lock"]["Door Lock.LockState"] == "lock.front_lock"
            assert matter.entities["back_door"]["Boolean State.StateValue"] == "binary_sensor.back_door_door"
            assert matter.renamed == {} and matter.failed == {}
    finally:
        session.stop()
    assert host.stopped and [op for op, _ in host.requests].count("remove") == 3


def test_an_entity_home_assistant_names_otherwise_than_predicted_is_reported(tmp_path):
    names = {**FakeHA.USUAL, "front_lock": ["lock.front_lock_2"]}
    session, host, matter_of = run(tmp_path, names)
    try:
        with matter_of() as matter:
            assert matter.entities["front_lock"]["Door Lock.LockState"] == "lock.front_lock_2"
            assert matter.renamed == {"lock.front_lock": "lock.front_lock_2"}
    finally:
        session.stop()


def test_home_assistant_s_commands_reach_the_twin_on_the_session_s_clock(tmp_path):
    session, host, matter_of = run(tmp_path)
    seen = []
    real = session.runtime.platform_set
    session.runtime.platform_set = lambda *a, **k: seen.append(threading.current_thread().name) or real(*a, **k)
    try:
        with matter_of():
            host.fire({"event": "changed", "twin": "matter:115f:0001@front_lock", "feature": "Door Lock.LockState",
                       "value": "Locked"})
            deadline = time.monotonic() + 2
            while not seen and time.monotonic() < deadline:
                time.sleep(0.01)
            assert seen == ["vesper-wallclock"]
            assert session.runtime.state("matter:115f:0001@front_lock")["Door Lock.LockState"] == "Locked"
    finally:
        session.stop()


def test_vesper_s_engine_runs_an_automation_on_matter_devices_as_home_assistant_would(tmp_path):
    from vesper.twins.clock import LogicalClock
    text = MATTER.replace("    devices:\n", "    automations: automations.yaml\n    devices:\n", 1)
    h = home(tmp_path, text)
    (tmp_path / "automations.yaml").write_text(
        "- id: lock_behind\n  alias: Lock the front door when the back door closes\n"
        "  triggers: [{trigger: state, entity_id: binary_sensor.back_door_door, to: 'off'}]\n"
        "  actions: [{action: lock.lock, target: {entity_id: lock.front_lock}},\n"
        "            {action: light.turn_on, target: {entity_id: light.hall_light}}]\n")
    session = Session(h, clock=LogicalClock())
    session.start(spawn_player=False, automations=True)
    lock, contact, light = (f"{p}@{n}" for p, n in (("matter:115f:0001", "front_lock"),
                                                    ("matter:100b:0016", "back_door"),
                                                    ("matter:100b:0018", "hall_light")))
    assert session.automations.refused == []
    session.runtime.stimulate(contact, {"type": "contact", "state": "opened"})
    session.clock.advance(1)
    session.runtime.stimulate(contact, {"type": "contact", "state": "closed"})
    session.clock.advance(1)
    assert session.runtime.state(lock)["Door Lock.LockState"] == "Locked"
    assert session.runtime.state(light)["On/Off.OnOff"] is True
    session.stop()


class FakeServer:
    made = []

    def __init__(self, root, storage, port=15580, interface=None, **kw):
        self.storage, self.interface, self.events = storage, interface, []
        FakeServer.made.append(self)

    @property
    def url(self):
        return "ws://127.0.0.1:15580/ws"

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


def stack_with_matter(monkeypatch, tmp_path):
    from tests.lab.test_cli_play import LabHA, stack_fakes
    from vesper.validation import stack as stack_mod
    stack_fakes(monkeypatch)
    FakeServer.made = []
    monkeypatch.setattr(stack_mod, "MatterServer", FakeServer)
    monkeypatch.setattr(stack_mod, "ha_has_matter", lambda root: True)
    monkeypatch.setattr(LabHA, "ensure_matter", lambda self, url: self.events.append(("ensure_matter", url)),
                        raising=False)
    monkeypatch.setattr(LabHA, "forget", lambda self, name: self.events.append(("forget", name)), raising=False)
    host_dir = stack_mod.Paths(tmp_path / ".vesper").matter_host
    host_dir.mkdir(parents=True)
    (host_dir / "host.mjs").write_text("// installed")
    return LabHA


def test_the_stack_starts_vesper_s_matter_server_for_a_home_with_matter_devices_on_an_interface(
        tmp_path, monkeypatch):
    from vesper.lab.live import lab_stack
    ha_class = stack_with_matter(monkeypatch, tmp_path)
    h = home(tmp_path)
    with lab_stack(tmp_path / ".vesper", h, matter_interface="en0") as lab:
        (server,) = FakeServer.made
        assert server.interface == "en0" and server.events == ["start"] and lab.matter_server is server
        assert ("ensure_matter", server.url) in ha_class.made[-1].events
    assert server.events == ["start", "stop"]
    with lab_stack(tmp_path / ".vesper", h) as lab:  # no interface: Matter stays off the hub
        assert lab.matter_server is None and len(FakeServer.made) == 1
        assert ("forget", "matter") in ha_class.made[-1].events  # no node of an earlier home lingers


def play_hub(tmp_path, monkeypatch, capsys, *extra):
    import contextlib
    from tests.lab.test_cli_lab import setup
    from vesper import cli
    from vesper.lab import live
    stack_with_matter(monkeypatch, tmp_path)
    seen = {}

    @contextlib.contextmanager
    def fake_matter(session, url, ha, *, host, **kw):
        seen.update(url=url, interface=host.interface, devices=[d.name for d in session.home.devices
                                                                if "matter" in d.record["binding"]])
        yield SimpleNamespace(entities={"front_lock": {"Door Lock.LockState": "lock.front_lock"}}, renamed={},
                              failed={})
    monkeypatch.setattr(live, "lab_matter", fake_matter)
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    cat, h = setup(tmp_path / "c")
    assert cli.main(["lab", "play", str(h), "--catalog", str(cat), "--port", "0", "--no-browser", "--hub",
                     "--root", str(tmp_path / ".vesper"), *extra]) == 0
    return seen, capsys.readouterr().out


def test_play_with_the_hub_puts_the_matter_devices_on_home_assistant_on_the_interface_given(
        tmp_path, monkeypatch, capsys):
    seen, out = play_hub(tmp_path, monkeypatch, capsys, "--matter-interface", "en0")
    assert seen == {"url": "ws://127.0.0.1:15580/ws", "interface": "en0", "devices": ["front_lock"]}
    assert "Matter: 1 of 1 devices on Home Assistant through VESPER's Matter Server (nodes on en0)" in out
    assert "  front_lock: lock.front_lock" in out


def test_play_with_the_hub_and_no_interface_says_how_to_put_the_matter_devices_on_it(tmp_path, monkeypatch, capsys):
    seen, out = play_hub(tmp_path, monkeypatch, capsys)
    assert seen == {}
    assert "  front_lock: not on Home Assistant (a Matter device: give --matter-interface, e.g. en0" in out
