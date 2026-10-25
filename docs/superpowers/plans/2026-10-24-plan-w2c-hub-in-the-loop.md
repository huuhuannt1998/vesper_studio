# Plan W2c: Home Assistant in the loop — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Lab home played with VESPER's own broker and an unmodified Home Assistant as the hub: its Zigbee devices appear in Home Assistant, Home Assistant's automations act on them, its dashboard is in the play page, and the session still replays without it.

**Architecture:**
1. The twins runtime announces every command (`Runtime.on_action`). A Lab session traces the hub's commands as `hub` inputs, so a replay applies them again with no hub.
2. `vesper/lab/hub.py` puts the session's Zigbee devices on the broker through the same Zigbee2MQTT bridge `vesper twins run` uses, and hands every broker message to the session's clock thread, where the world, the rules and the trace live.
3. VESPER's Home Assistant gets the Lab's two settings: the page may frame its dashboard, and it loads the home's automations. It also gets a login for the hub panel, set once through its admin API. The experiments' configuration is unchanged byte for byte.
4. `vesper lab play --hub` and `vesper lab look --hub` start the broker and Home Assistant, put the home on the hub, show the dashboard in the page, print the login and each device's entities, and stop everything they started, also after a failure. Automations edited in Home Assistant go back into the home.
5. An example home of real products, with an automation, and a run book for the live check.

**Tech Stack:** Python ≥3.11; the existing Zigbee2MQTT bridge, `vesper.validation.stack` (Mosquitto, Home Assistant 2026.10.2), `websockets` (Home Assistant's WebSocket API for the login).

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, approved by the PI on 2026-10-24 (RKA `jrn_01M5QSGDYGE158FRVPVWKN0QNZ`). Covered here:
- §0 the experience, steps 3–4 (Home Assistant sees real-looking devices and runs real automations, and the house reacts);
- §2 play mode's Home Assistant dashboard in the page, with `use_x_frame_options: false` and nothing else relaxed;
- §3 device events on VESPER's broker; one authority, the session's clock thread;
- §4 the home file's `automations`;
- §5b play on the wall clock with Home Assistant;
- success criterion 4: the trace reproduces the device sequence without Home Assistant;
- §10 W2's vertical slice: walk, trip a sensor, Home Assistant turns a lamp on, and the views show it.

## Global Constraints

- No new dependency.
- Home Assistant's configuration for the experiments (`vesper validate …`) stays byte for byte as it is; only the Lab's adds `use_x_frame_options: false` and `automation: !include automations.yaml`.
- The Lab never shares a broker or a Home Assistant: a port in use is a clear failure, and whatever the command started is stopped again.
- Nothing fails silently: a missing automations file is named before anything starts; a Matter device is listed as not on the hub.
- Shared Mac (another project's scored run until about 2026-10-27): unit tests only, under `nice -n 19` / `taskpolicy -b`. The live check (`docs/experiments/lab-runbook.md`) needs the PI's go and an agreed slot.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **Every broker message is handled on the clock thread,** never on the MQTT network thread. Pinned by `test_home_assistant_s_command_reaches_the_twin_on_the_clock_thread_and_is_traced` (Task 2).
2. **A session played with Home Assistant replays without it.** Pinned by `test_commands_from_the_hub_are_traced_and_replayed_without_it` (Task 1).
3. **The experiments' Home Assistant configuration does not change.** Pinned by `test_the_lab_s_home_assistant_may_be_framed_and_loads_the_home_s_automations` (Task 3).
4. **A failure while starting the hub stops what was started.** Pinned by `test_play_with_the_hub_stops_what_it_started_when_home_assistant_fails` (Task 4).
5. **The spec's vertical slice holds end to end:** a walk trips the sensor, Home Assistant's automation turns the lamp on, and the page shows it. Pinned by `test_walking_in_lets_an_automation_turn_the_lamp_on_and_the_page_shows_it` (Task 2).

## Rulings carried into this plan

- **Matter devices are not on the hub yet.** The Matter tier's commissioning and host process belong in their own plan (W2d); here they are listed as not on the hub. Cost if wrong: a home with Matter products shows them on the map but not in Home Assistant until W2d.
- **Home Assistant's entity ids stay the bridge's** (from each device's IEEE address, stable per home and device name), as the paper's acceptance runs use them. `play --hub` prints them, and the example's automation names them. Cost if wrong: automations are less readable than real Zigbee2MQTT's friendly-name entities.
- **The Lab login is set through Home Assistant's admin API and kept in a file readable by its owner only,** because onboarding keeps no password and the hub panel needs one. Nothing else in Home Assistant's authentication is relaxed. Cost if wrong: anyone who can read the owner's files can log into VESPER's loopback-only Home Assistant.
- **Automations edited in Home Assistant are written back into the home** when play stops, so a home directory is self-contained. Cost if wrong: an edit a user meant to throw away is kept (it is a plain file under their own control).
- **A `hub` input carries the value the twin accepted** (or, refused, the value sent and the reason), and a replay applies it with `platform_set`, exactly as the bridge did.

---
### Task 1: Commands from the hub are traced and replayed

**Files:**
- Modify: `vesper/twins/runtime.py` (`Runtime.on_action`; every logged command is announced)
- Modify: `vesper/lab/session.py` (`hub` inputs; replay applies them)
- Tests: append to `tests/twins/test_runtime.py` and `tests/lab/test_session.py`

**Interfaces:**
- Produces:
  - in `vesper/twins/runtime.py`: `Runtime.on_action(fn)`, called with each `Action` as it is logged (accepted or refused, human or platform);
  - in `vesper/lab/session.py`: trace records `{"kind": "input", "actor": "hub", "op": "command", "args": {"device", "feature", "value"}, "outcome": {"accepted"[, "reason"]}}`, and `replay` applying them with `runtime.platform_set` (an unknown device is the replay's first difference).

- [ ] **Step 1: Write the failing tests**

Append to `tests/twins/test_runtime.py`:
```python


def test_every_command_is_heard_as_it_is_logged_accepted_or_not(vds):
    rt, _, (tid,) = runtime(vds["PLUG-1"])
    heard = []
    rt.on_action(heard.append)
    rt.platform_set(tid, "state", "ON")
    rt.platform_set(tid, "state", "SIDEWAYS")
    rt.operate(tid, "state", "OFF", actor="alice")
    assert heard == rt.actions
    assert [(a.actor, a.accepted) for a in heard] == [("platform", True), ("platform", False), ("human:alice", True)]
```

Append to `tests/lab/test_session.py`:
```python


def test_commands_from_the_hub_are_traced_and_replayed_without_it(tmp_path):
    s = session(tmp_path, trace=False)
    s.wait(5)
    assert s.runtime.platform_set(s.twin_ids["bedroom_lamp"], "state", "ON")  # what a bridge does for the hub
    assert not s.runtime.platform_set(s.twin_ids["hall_motion"], "occupancy", True)  # read-only: refused
    s.interact()  # a person's use is an interaction, not a hub command
    s.wait(10)
    s.stop()
    commands = [r for r in s.records if r["kind"] == "input" and r["op"] == "command"]
    assert [(r["actor"], r["args"], r["outcome"]["accepted"]) for r in commands] == [
        ("hub", {"device": "bedroom_lamp", "feature": "state", "value": "ON"}, True),
        ("hub", {"device": "hall_motion", "feature": "occupancy", "value": True}, False)]
    again = replay(s.records, s.home)
    assert compare_reports(s.records, again) is None
    assert any(r["kind"] == "report" and r["device"] == "bedroom_lamp" and r["value"] == "ON" for r in again)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_runtime.py tests/lab/test_session.py -q 2>&1 | tail -1`

Expected: `2 failed, 40 passed`: `Runtime` has no attribute `on_action`

- [ ] **Step 3: Implement**

In `vesper/twins/runtime.py`, replace:
```python
        self._subscribers: list[Callable[[Event], None]] = []
        self._reporters: dict[tuple[str, str], _Reporter] = {}
```
with:
```python
        self._subscribers: list[Callable[[Event], None]] = []
        self._action_listeners: list[Callable[[Action], None]] = []
        self._reporters: dict[tuple[str, str], _Reporter] = {}
```

In `vesper/twins/runtime.py`, replace:
```python
        self._subscribers.append(fn)
```
with:
```python
        self._subscribers.append(fn)

    def on_action(self, fn: Callable[[Action], None]) -> None:
        """`fn(action)` hears every command as it is logged, accepted or refused (a Lab session traces the
        hub's commands so that a session with Home Assistant replays without it)."""
        self._action_listeners.append(fn)

    def _log(self, action: Action) -> None:
        self.actions.append(action)
        for fn in self._action_listeners:
            fn(action)
```

In `vesper/twins/runtime.py`, replace:
```python
            if twin_id in self.faulted:
                self.actions.append(Action(self.clock.now(), twin_id, feature, value, actor, False, "twin faulted"))
                return False
```
with:
```python
            if twin_id in self.faulted:
                self._log(Action(self.clock.now(), twin_id, feature, value, actor, False, "twin faulted"))
                return False
```

In `vesper/twins/runtime.py`, replace:
```python
            except TwinError as e:
                self.actions.append(Action(self.clock.now(), twin_id, feature, value, actor, False, str(e)))
                return False
```
with:
```python
            except TwinError as e:
                self._log(Action(self.clock.now(), twin_id, feature, value, actor, False, str(e)))
                return False
```

In `vesper/twins/runtime.py`, replace:
```python
                return False
            self.actions.append(Action(self.clock.now(), twin_id, feature, new, actor, True))
            self._change(twin, feature, new, cause)
```
with:
```python
                return False
            self._log(Action(self.clock.now(), twin_id, feature, new, actor, True))
            self._change(twin, feature, new, cause)
```

In `vesper/lab/session.py`, replace:
```python
                         for d in home.devices}
        self._names = {tid: name for name, tid in self.twin_ids.items()}
```
with:
```python
                         for d in home.devices}
        self.runtime.on_action(self._on_action)
        self._names = {tid: name for name, tid in self.twin_ids.items()}
```

In `vesper/lab/session.py`, replace:
```python

    def _on_report(self, event) -> None:
```
with:
```python

    def _on_action(self, a) -> None:
        """A command from the hub (through a bridge) is an input: replay applies it again, with no hub."""
        if a.actor == "platform":
            outcome = {"accepted": a.accepted} if a.accepted else {"accepted": False, "reason": a.reason}
            self._input("hub", "command", {"device": self._names.get(a.twin_id, a.twin_id), "feature": a.feature,
                                           "value": a.value}, outcome=outcome)

    def _on_report(self, event) -> None:
```

In `vesper/lab/session.py`, replace:
```python
                s.interact(a["target"], actor=r["actor"])
        except (ValueError, WorldError) as e:  # the home changed under the trace: that is the difference
```
with:
```python
                s.interact(a["target"], actor=r["actor"])
            elif r["op"] == "command":
                if a["device"] not in s.twin_ids:
                    raise ValueError(f"unknown device {a['device']!r}")
                s.runtime.platform_set(s.twin_ids[a["device"]], a["feature"], a["value"])
        except (ValueError, WorldError) as e:  # the home changed under the trace: that is the difference
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins tests/lab -q 2>&1 | tail -1`

Expected: `207 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/runtime.py vesper/lab/session.py tests/twins/test_runtime.py tests/lab/test_session.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): commands from the hub are traced, and a replay applies them without the hub

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: The hub: the home's Zigbee devices on the broker, handled on the clock thread

**Files:**
- Create: `vesper/lab/hub.py`
- Create tests: `tests/lab/test_hub.py` (the exposure tests' in-memory MQTT client and Home Assistant)

**Interfaces:**
- Consumes: `Bridge`, `Placement` (`vesper/exposure/z2m_bridge.py`); `Session` (Task 1); `PlayServer` (W2b) in the vertical-slice test.
- Produces in `vesper/lab/hub.py`: `ClockThreadClient(client, clock)` (an MQTT client whose `on_message` and `on_connect` run on the clock's thread); `Hub(session, client, *, zhc_version="unknown", base_topic="zigbee2mqtt")` with `start()`, `stop()`, `skipped` (the devices not on the hub) and `entities(name) -> dict[feature, entity_id]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_hub.py`:
```python
"""The hub in the loop, against the in-memory broker client and Home Assistant of the exposure tests."""
import json
import threading
import time

import pytest

from tests.exposure.fake_ha import FakeHA
from tests.exposure.fake_mqtt import FakeClient
from tests.lab.homes import catalog, write_home
from tests.lab.test_play import client, until
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.play import PlayServer
from vesper.lab.session import Session
from vesper.twins.clock import WallClock


@pytest.fixture
def lab(tmp_path):
    home = load_lab_home(write_home(tmp_path), catalog())  # the player starts in the hall
    session = Session(home, clock=WallClock())
    mqtt = FakeClient()
    hub = Hub(session, mqtt)
    session.start()
    hub.start()
    yield session, hub, mqtt
    hub.stop()
    session.stop()


def eventually(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("the condition never held")


def test_every_zigbee_device_is_on_the_hub_and_matter_ones_are_named(lab):
    session, hub, mqtt = lab
    assert hub.skipped == ["front_lock"]  # Matter: plan W2d
    entities = set(FakeHA(mqtt)._entities())
    for name in ("front_door", "hall_motion", "bedroom_lamp", "bedroom_button", "kitchen_climate"):
        assert set(hub.entities(name).values()) <= entities and hub.entities(name)
    assert hub.entities("bedroom_lamp")["state"].startswith("light.")


def test_home_assistant_s_command_reaches_the_twin_on_the_clock_thread_and_is_traced(lab):
    session, hub, mqtt = lab
    threads = []
    session.runtime.on_action(lambda a: threads.append(threading.current_thread().name))
    FakeHA(mqtt).call("light", "turn_on", {"entity_id": hub.entities("bedroom_lamp")["state"]})
    eventually(lambda: session.rules.effects.get("bedroom_lamp") == {"on": True})
    assert threads == ["vesper-wallclock"]
    command = next(r for r in session.records if r["kind"] == "input" and r["op"] == "command")
    assert command["actor"] == "hub" and command["args"]["device"] == "bedroom_lamp"


def test_walking_in_lets_an_automation_turn_the_lamp_on_and_the_page_shows_it(tmp_path):
    text = """\
        version: 2
        scene: test/house
        avatar: {model: female_0, start: {room: kitchen}}
        doors:
          - {doorway: bedroom-hall, open: true}
        devices:
          - {product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}
          - {product: "zigbee:acme:dim-1", name: bedroom_lamp, at: {room: bedroom, xy: [5.0, 5.0], mount: table}}
    """
    home = load_lab_home(write_home(tmp_path, text), catalog())
    session = Session(home, clock=WallClock())
    mqtt = FakeClient()
    hub = Hub(session, mqtt)
    session.start()
    hub.start()
    server = PlayServer(session, port=0)
    server.start()
    ha = FakeHA(mqtt)
    motion, lamp = hub.entities("hall_motion")["occupancy"], hub.entities("bedroom_lamp")["state"]

    def automation():  # Home Assistant's: when the hall sees someone, the bedroom lamp turns on
        while not stop.is_set():
            if ha.state(motion)["state"] == "on" and ha.state(lamp)["state"] == "off":
                ha.call("light", "turn_on", {"entity_id": lamp})
            time.sleep(0.05)
    stop = threading.Event()
    threading.Thread(target=automation, daemon=True).start()
    try:
        with client(server) as ws:
            until(ws, lambda m: m["type"] == "hello")
            assert ha.state(lamp)["state"] == "off"
            ws.send(json.dumps({"type": "walk_to", "x": 1.5, "z": 1.5}))  # from the kitchen into the hall
            until(ws, lambda m: m["type"] == "effect" and m["target"] == "bedroom_lamp" and m["effect"] == {"on": True},
                  timeout=10)
        assert ha.state(lamp)["state"] == "on"
        assert any(r["kind"] == "input" and r["op"] == "command" for r in session.records)
    finally:
        stop.set()
        server.stop()
        hub.stop()
        session.stop()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_hub.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.hub'`

- [ ] **Step 3: Implement**

Create `vesper/lab/hub.py`:
```python
"""The hub in the loop (spec §2, §3): the running home's Zigbee devices on VESPER's broker, through the same
Zigbee2MQTT bridge `vesper twins run` uses, so that an unmodified Home Assistant sees them and commands them.

Every message from the broker is handed to the session's clock thread, like every other change to the world:
the rules, the world and the trace are only ever touched there. Home Assistant's commands reach the twins as
platform commands, so the session traces them (`hub` inputs) and a replay needs no hub. Matter devices are not
on the hub yet (plan W2d); `skipped` names them.
"""
from __future__ import annotations

from vesper.exposure.z2m_bridge import Bridge, Placement


class ClockThreadClient:
    """An MQTT client whose message and connect callbacks run on a clock's thread."""

    def __init__(self, client, clock):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_clock", clock)

    def __getattr__(self, name):
        return getattr(self._client, name)

    def __setattr__(self, name, value):
        if name in ("on_message", "on_connect") and callable(value):
            fn, clock = value, self._clock

            def on_clock(*args):
                clock.call_at(clock.now(), lambda: fn(*args))
            value = on_clock
        setattr(self._client, name, value)


class Hub:
    def __init__(self, session, client, *, zhc_version: str = "unknown", base_topic: str = "zigbee2mqtt"):
        self.session = session
        devices = session.home.devices
        self.skipped = [d.name for d in devices if "zigbee" not in d.record["binding"]]
        self._placements = {d.name: Placement(session.twin_ids[d.name], d.name) for d in devices
                            if "zigbee" in d.record["binding"]}
        self.bridge = Bridge(session.runtime, list(self._placements.values()), ClockThreadClient(client, session.clock),
                             base_topic=base_topic, zhc_version=zhc_version)

    def start(self) -> None:
        self.bridge.start()

    def stop(self) -> None:
        self.bridge.stop()
        self.bridge.flush()

    def entities(self, name: str) -> dict[str, str]:
        """The Home Assistant entities of one device, by feature: what an automation names."""
        p = self._placements.get(name)
        if p is None:
            raise KeyError(f"{name} is not on the hub" + (" (Matter: plan W2d)" if name in self.skipped else ""))
        return {e.feature: e.entity_id for e in self.bridge.expected.get(p.twin_id, [])}
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `159 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/hub.py tests/lab/test_hub.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the hub: the home's Zigbee devices on the broker, handled on the clock thread

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: VESPER's Home Assistant, as the Lab needs it

**Files:**
- Modify: `vesper/validation/stack.py` (`HomeAssistant.frameable`, `HomeAssistant.automations`, `read_automations`, `lab_login`, `_ws_call`)
- Tests: append to `tests/validation/test_stack.py` (a stand-in for Home Assistant's WebSocket API)

**Interfaces:**
- Produces in `vesper/validation/stack.py`: `HomeAssistant(..., frameable=False, automations=None)`; `read_automations() -> str`; `lab_login() -> (username, password)`, set once with `config/auth_provider/homeassistant/admin_change_password` and kept in `<root>/ha/lab-login.json` (mode 0600).

- [ ] **Step 1: Write the failing tests**

Append to `tests/validation/test_stack.py`:
```python


def test_the_lab_s_home_assistant_may_be_framed_and_loads_the_home_s_automations(tmp_path):
    plain = HomeAssistant(tmp_path / "plain", port=18123)
    plain.write_config()
    assert (tmp_path / "plain" / "ha" / "config" / "configuration.yaml").read_text() == (  # experiments' config
        "homeassistant:\n  name: VESPER\n  unit_system: metric\n  time_zone: UTC\n  country: US\n  currency: USD\n"
        "http:\n  server_host: 127.0.0.1\n  server_port: 18123\n"
        "api:\nconfig:\nonboarding:\nlogger:\n  default: warning\n")
    rules = "- alias: lamp on when the hall sees someone\n  triggers: []\n  actions: []\n"
    lab = HomeAssistant(tmp_path / "lab", port=18123, frameable=True, automations=rules)
    lab.write_config()
    cfg = tmp_path / "lab" / "ha" / "config"
    text = (cfg / "configuration.yaml").read_text()
    assert "http:\n  server_host: 127.0.0.1\n  server_port: 18123\n  use_x_frame_options: false\n" in text
    assert text.endswith("automation: !include automations.yaml\n")
    assert (cfg / "automations.yaml").read_text() == rules == lab.read_automations()


class FakeHAWebSocket:
    """Home Assistant's WebSocket API, as far as the Lab's login uses it (auth, current user, admin password)."""

    def __init__(self):
        from websockets.sync.server import serve
        import threading
        self.passwords, self.seen = {}, []
        self.server = serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handle(self, ws):
        ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(ws.recv())
        if auth.get("access_token") != "token-1":
            ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
            return
        ws.send(json.dumps({"type": "auth_ok"}))
        for raw in ws:
            msg = json.loads(raw)
            self.seen.append(msg["type"])
            if msg["type"] == "auth/current_user":
                result = {"id": "user-owner", "name": "VESPER", "is_owner": True}
            elif msg["type"] == "config/auth_provider/homeassistant/admin_change_password":
                self.passwords[msg["user_id"]] = msg["password"]
                result = None
            else:
                ws.send(json.dumps({"id": msg["id"], "type": "result", "success": False,
                                    "error": {"code": "unknown_command", "message": "Unknown command."}}))
                continue
            ws.send(json.dumps({"id": msg["id"], "type": "result", "success": True, "result": result}))


def test_the_lab_login_is_set_once_through_the_admin_api_and_kept_private(tmp_path):
    ws = FakeHAWebSocket()
    ha = HomeAssistant(tmp_path, port=ws.port)
    ha.token = lambda: "token-1"
    user, password = ha.lab_login()
    saved = tmp_path / "ha" / "lab-login.json"
    assert user == "vesper" and len(password) >= 16 and ws.passwords == {"user-owner": password}
    assert json.loads(saved.read_text()) == {"username": "vesper", "password": password}
    assert saved.stat().st_mode & 0o777 == 0o600
    assert ha.lab_login() == (user, password)  # kept: set once, not on every play
    assert ws.seen.count("config/auth_provider/homeassistant/admin_change_password") == 1
    ws.server.shutdown()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_stack.py -q -k 'framed or lab_login' 2>&1 | tail -1`

Expected: `2 failed, 27 deselected`: `HomeAssistant` takes no `frameable`, and has no `lab_login`

- [ ] **Step 3: Implement**

In `vesper/validation/stack.py`, replace:
```python
import os
import shutil
```
with:
```python
import os
import secrets
import shutil
```

In `vesper/validation/stack.py`, replace:
```python
    persist_timeout: float = 30.0  # how long onboarding waits for Home Assistant to save the new token
    _access: tuple[str, float] | None = field(default=None, repr=False)
```
with:
```python
    persist_timeout: float = 30.0  # how long onboarding waits for Home Assistant to save the new token
    frameable: bool = False  # the Lab's play page frames the dashboard (spec §2); nothing else is relaxed
    automations: str | None = None  # the Lab's: automations.yaml, loaded and editable in Home Assistant's UI
    _access: tuple[str, float] | None = field(default=None, repr=False)
```

In `vesper/validation/stack.py`, replace:
```python
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "configuration.yaml").write_text(
```
with:
```python
        cfg.mkdir(parents=True, exist_ok=True)
        frame = "  use_x_frame_options: false\n" if self.frameable else ""
        automation = ""
        if self.automations is not None:
            (cfg / "automations.yaml").write_text(self.automations)
            automation = "automation: !include automations.yaml\n"
        (cfg / "configuration.yaml").write_text(
```

In `vesper/validation/stack.py`, replace:
```python
            "  currency: USD\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {self.port}\n"
            "api:\nconfig:\nonboarding:\nlogger:\n  default: warning\n")
```
with:
```python
            "  currency: USD\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {self.port}\n{frame}"
            f"api:\nconfig:\nonboarding:\nlogger:\n  default: warning\n{automation}")

    def lab_login(self) -> tuple[str, str]:
        """A login for the Lab's hub panel (Home Assistant's dashboard in the play page). Onboarding gives the
        owner a random password nobody keeps; the Lab sets one once, through Home Assistant's admin API, and keeps
        it in ha/lab-login.json, readable by its owner only. Delete that file to set a new one."""
        path = Path(self.root) / "ha" / "lab-login.json"
        if path.is_file():
            saved = json.loads(path.read_text())
            return saved["username"], saved["password"]
        from websockets.sync.client import connect
        password = secrets.token_urlsafe(18)
        with connect(f"ws://127.0.0.1:{self.port}/api/websocket", proxy=None, open_timeout=30) as ws:
            json.loads(ws.recv(timeout=30))  # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": self.token()}))
            if json.loads(ws.recv(timeout=30)).get("type") != "auth_ok":
                raise StackError("Home Assistant refused VESPER's token on its WebSocket API")
            user = _ws_call(ws, 1, "auth/current_user")
            _ws_call(ws, 2, "config/auth_provider/homeassistant/admin_change_password", user_id=user["id"],
                     password=password)
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump({"username": "vesper", "password": password}, f)
        return "vesper", password

    def read_automations(self) -> str:
        """automations.yaml as it is now: the home's, plus whatever was edited in Home Assistant's UI."""
        return (Paths(Path(self.root)).ha_config / "automations.yaml").read_text()
```

In `vesper/validation/stack.py`, replace:
```python
            raise StackError(f"service {domain}.{service} failed ({r.status_code}): {r.text[:200]}")
```
with:
```python
            raise StackError(f"service {domain}.{service} failed ({r.status_code}): {r.text[:200]}")


def _ws_call(ws, msg_id: int, kind: str, **fields):
    """One command on Home Assistant's WebSocket API; its result, or a StackError naming the refusal."""
    ws.send(json.dumps({"id": msg_id, "type": kind, **fields}))
    while True:
        msg = json.loads(ws.recv(timeout=30))
        if msg.get("id") == msg_id:
            if not msg.get("success"):
                raise StackError(f"Home Assistant refused {kind}: {msg.get('error', {}).get('message')}")
            return msg.get("result")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/validation -q 2>&1 | tail -1`

Expected: `167 passed, 4 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/stack.py tests/validation/test_stack.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(stack): the Lab's Home Assistant: a frameable dashboard, the home's automations, a login for the hub panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: `vesper lab play --hub` and `vesper lab look --hub`; the hub in the console

**Files:**
- Modify: `vesper/cli.py` (`_lab_play`, `_start_hub`, `_stop_hub`, `_keep_automations`, `_print_entities`, `_lab_look`; `--hub` and `--root` for `play` and `look`)
- Modify: `vesper/lab/ui/mapview.js` (`recordLine` for `hub` commands)
- Tests: append to `tests/lab/test_cli_play.py` (fakes for Mosquitto, Home Assistant and the MQTT connection) and `tests/lab/ui/mapview.test.mjs`

**Interfaces:**
- Consumes: `Hub` (Task 2); `Mosquitto`, `HomeAssistant`, `StackError` (Task 3); `mqtt.connect`, `mqtt.disconnect`; `OFFLINE_WILL`, `_zhc_version`.
- Produces: `vesper lab play <home> --hub [--root .vesper]` and `vesper lab look <home> --hub [--root .vesper]`; console lines `the hub sets <device> <feature> = <value>` (`: refused (<reason>)` when refused).

- [ ] **Step 1: Write the failing tests**

Append to `tests/lab/test_cli_play.py`:
```python


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
    made, fail = [], None

    def __init__(self, root, port=18123, frameable=False, automations=None, **kwargs):
        self.port, self.frameable, self.automations, self.events, self.edited = port, frameable, automations, [], None
        LabHA.made.append(self)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.events.append("start")
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
    LabMosquitto.made, LabHA.made, LabHA.fail = [], [], None
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
```

Append to `tests/lab/ui/mapview.test.mjs`:
```javascript

test("a command from the hub is a console line, and a refused one says why", () => {
  const start = 7 * 3600;
  assert.equal(recordLine({ t: 3, kind: "input", actor: "hub", op: "command",
    args: { device: "bedroom_lamp", feature: "state", value: "ON" }, outcome: { accepted: true } }, start),
    "07:00:03 the hub sets bedroom_lamp state = \"ON\"");
  assert.equal(recordLine({ t: 4, kind: "input", actor: "hub", op: "command",
    args: { device: "hall_motion", feature: "occupancy", value: true },
    outcome: { accepted: false, reason: "occupancy is read-only" } }, start),
    "07:00:04 the hub sets hall_motion occupancy = true: refused (occupancy is read-only)");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_cli_play.py tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `5 failed, 8 passed`: `vesper lab play` and `look` know no `--hub` (4), and the console has no line for a hub command (the JavaScript test)

- [ ] **Step 3: Implement**

In `vesper/cli.py`, replace:
```python
    from vesper.lab.play import PlayError, PlayServer
    from vesper.lab.session import Session
```
with:
```python
    from vesper.lab.play import PlayError, PlayServer
    from vesper.validation.stack import StackError
    from vesper.lab.session import Session
```

In `vesper/cli.py`, replace:
```python
    trace = Path(args.trace) if args.trace else _new_trace(traces, "play")
    session = Session(home, clock=WallClock(), trace=trace)
```
with:
```python
    trace = Path(args.trace) if args.trace else _new_trace(traces, "play")
    if args.hub and home.automations is not None and not home.automations.is_file():
        print(f"FAILED: automations: {home.automations} does not exist", file=sys.stderr)
        return 1
    session = Session(home, clock=WallClock(), trace=trace)
```

In `vesper/cli.py`, replace:
```python
    session = Session(home, clock=WallClock(), trace=trace)
    session.start()
    server = PlayServer(session, port=args.port)
    try:
```
with:
```python
    session = Session(home, clock=WallClock(), trace=trace)
    started: list[tuple[str, object]] = []  # the hub's parts, stopped in reverse order
    server, rules = None, None
    try:
```

In `vesper/cli.py`, replace:
```python
    try:
        try:
```
with:
```python
    try:
        if args.hub:
            try:
                rules = _start_hub(args, home, session, started)
            except StackError as e:
                print(f"FAILED: {e}", file=sys.stderr)
                return 1
        session.start()
        ha = dict(started).get("ha")
        server = PlayServer(session, port=args.port, hub_url=ha.url if ha else None)
        try:
```

In `vesper/cli.py`, replace:
```python
              "(Ctrl-C stops)")
        if not args.no_browser:
```
with:
```python
              "(Ctrl-C stops)")
        if args.hub:
            user, password = dict(started)["login"]
            print(f"Home Assistant is in the hub panel ({dict(started)['ha'].url}): log in as {user}, password "
                  f"{password} (kept in {Path(args.root) / 'ha' / 'lab-login.json'})")
            _print_entities(home, dict(started)["hub"])
        if not args.no_browser:
```

In `vesper/cli.py`, replace:
```python
        _wait_for_interrupt()
    finally:
        server.stop()
        session.stop()
    print(f"trace {trace}")
```
with:
```python
        _wait_for_interrupt()
    finally:
        if server is not None:
            server.stop()
        if started:
            _stop_hub(started, home, rules)
        session.stop()
    print(f"trace {trace}")
```

In `vesper/cli.py`, replace:
```python
    print(f"trace {trace}")
    return 0
```
with:
```python
    print(f"trace {trace}")
    return 0


def _start_hub(args, home, session, started: list) -> str:
    """VESPER's broker, its Home Assistant (framed by the page, with the home's automations), and the home's
    Zigbee devices on the broker. Returns the automations Home Assistant started with."""
    from vesper.exposure import mqtt
    from vesper.lab.hub import Hub
    from vesper.validation import stack
    root, port = Path(args.root), home.broker[1]
    rules = home.automations.read_text() if home.automations is not None else "[]\n"
    broker = stack.Mosquitto(root, port=port)
    started.append(("broker", broker))
    broker.start()
    ha = stack.HomeAssistant(root, frameable=True, automations=rules)
    started.append(("ha", ha))
    ha.start()
    ha.onboard()
    ha.ensure_mqtt(port)
    started.append(("login", ha.lab_login()))
    client = mqtt.connect("127.0.0.1", port, "vesper-lab-bridge", will=OFFLINE_WILL)
    started.append(("client", client))
    hub = Hub(session, client, zhc_version=_zhc_version(Path(args.catalog)))
    hub.start()
    started.append(("hub", hub))
    return rules


def _stop_hub(started: list, home, rules: str | None) -> None:
    from vesper.exposure import mqtt
    for name, part in reversed(started):
        if name == "hub":
            part.stop()
        elif name == "client":
            mqtt.disconnect(part)
        elif name == "ha":
            _keep_automations(part, home, rules)
            part.stop()
        elif name == "broker":
            part.stop()


def _keep_automations(ha, home, rules: str | None) -> None:
    """Automations edited in Home Assistant's UI go back into the home, which stays self-contained."""
    try:
        now = ha.read_automations()
    except OSError:
        return
    if rules is None or now == rules:
        return
    target = home.automations or home.path.parent / "automations.yaml"
    target.write_text(now)
    print(f"{target.name} updated from Home Assistant" + ("" if home.automations else
          f"; add `automations: {target.name}` to {home.path.name} to load it next time"))


def _print_entities(home, hub) -> None:
    print("Home Assistant entities, for automations:")
    for d in home.devices:
        if d.name in hub.skipped:
            print(f"  {d.name}: not on the hub (Matter: plan W2d)")
        else:
            print(f"  {d.name}: {', '.join(sorted(set(hub.entities(d.name).values())))}")
```

In `vesper/cli.py`, replace:
```python
    from vesper.twins.clock import WallClock
    if args.update_golden and not args.golden:
```
with:
```python
    from vesper.twins.clock import WallClock
    from vesper.validation.stack import StackError
    if args.update_golden and not args.golden:
```

In `vesper/cli.py`, replace:
```python
    out.mkdir(parents=True, exist_ok=True)
    session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
```
with:
```python
    out.mkdir(parents=True, exist_ok=True)
    if args.hub and home.automations is not None and not home.automations.is_file():
        print(f"FAILED: automations: {home.automations} does not exist", file=sys.stderr)
        return 1
    session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
```

In `vesper/cli.py`, replace:
```python
    session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
    session.start()
```
with:
```python
    session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
    started: list[tuple[str, object]] = []
    rules = None
    if args.hub:
        try:
            rules = _start_hub(args, home, session, started)
        except StackError as e:
            _stop_hub(started, home, rules)
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
    session.start()
```

In `vesper/cli.py`, replace:
```python
    session.start()
    server = PlayServer(session, port=0)
    try:
```
with:
```python
    session.start()
    ha = dict(started).get("ha")
    server = PlayServer(session, port=0, hub_url=ha.url if ha else None)
    try:
```

In `vesper/cli.py`, replace:
```python
    finally:
        server.stop()
        session.stop()
    for c in report.checks:
```
with:
```python
    finally:
        server.stop()
        if started:
            _stop_hub(started, home, rules)
        session.stop()
    for c in report.checks:
```

In `vesper/cli.py`, replace:
```python
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_look = lab_actions.add_parser("look", help="drive the play page in a headless browser and check what it shows")
```
with:
```python
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_play.add_argument("--hub", action="store_true",
                          help="put Home Assistant in the loop: start VESPER's broker and Home Assistant (after "
                               "`vesper setup ha`), and show its dashboard in the page")
    lab_play.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
    lab_look = lab_actions.add_parser("look", help="drive the play page in a headless browser and check what it shows")
```

In `vesper/cli.py`, replace:
```python
    lab_look.add_argument("--browser", help="a Chromium-based browser (default: found, or VESPER_BROWSER)")
    lab_look.add_argument("--width", type=int, default=1280)
```
with:
```python
    lab_look.add_argument("--browser", help="a Chromium-based browser (default: found, or VESPER_BROWSER)")
    lab_look.add_argument("--hub", action="store_true", help="with VESPER's broker and Home Assistant in the loop")
    lab_look.add_argument("--root", default=".vesper", help="where `vesper setup ha` installed the stack")
    lab_look.add_argument("--width", type=int, default=1280)
```

In `vesper/lab/ui/mapview.js`, replace:
```javascript
  }
  return null; // inputs are shown by what they cause
}
```
with:
```javascript
  }
  if (r.kind === "input" && r.op === "command") { // Home Assistant, through the bridge
    const { device, feature, value } = r.args;
    const refused = r.outcome && !r.outcome.accepted ? `: refused (${r.outcome.reason})` : "";
    return `the hub sets ${device} ${feature} = ${JSON.stringify(value)}${refused}`;
  }
  return null; // a person's inputs are shown by what they cause
}
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `163 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py vesper/lab/ui/mapview.js tests/lab/test_cli_play.py tests/lab/ui/mapview.test.mjs
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): vesper lab play --hub and look --hub: Home Assistant in the loop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: An example home with an automation, and the run book for the live check

**Files:**
- Create: `examples/lab/test-house/home.yaml`, `examples/lab/test-house/map.json` (the tests' house), `examples/lab/test-house/automations.yaml`, `examples/lab/test-house/look-hub.yaml`
- Create: `docs/experiments/lab-runbook.md`
- Create tests: `tests/lab/test_example_home.py`

**Interfaces:**
- Consumes: `tests.lab.maps.house`; the catalog's product records (`catalog/vds/zigbee.json`, read directly: the test asks whether the products exist, not whether the catalog is current); `Hub` (Task 2); `load_steps` (W2b).

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_example_home.py`:
```python
"""The example home in examples/lab/test-house: it is the tests' house, its products are real catalog products,
and its automation names entities the hub really creates."""
import json
import re
from pathlib import Path

import pytest

from tests.exposure.fake_mqtt import FakeClient
from tests.lab.maps import house
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.session import Session

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "lab" / "test-house"
CATALOG = ROOT / "catalog" / "vds" / "zigbee.json"
ENTITY = r"\b(?:light|binary_sensor|sensor|switch|event|button|select|number)\.v[0-9a-f]{16}_\w+"


def test_the_example_s_map_is_the_test_house(tmp_path):
    house().save(tmp_path / "map.json")
    assert json.loads((EXAMPLE / "map.json").read_text()) == json.loads((tmp_path / "map.json").read_text())


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_the_example_s_products_are_real_and_its_automation_names_entities_the_hub_creates():
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}  # the records only: not a staleness check
    home = load_lab_home(EXAMPLE / "home.yaml", records)
    session = Session(home)
    hub = Hub(session, FakeClient())
    session.start()
    hub.start()
    made = {e for d in home.devices if d.name not in hub.skipped for e in hub.entities(d.name).values()}
    named = set(re.findall(ENTITY, (EXAMPLE / "automations.yaml").read_text()))
    hub.stop()
    session.stop()
    assert named and named <= made


def test_the_example_s_look_for_the_hub_is_a_valid_steps_file():
    from vesper.lab.visual import load_steps
    steps = load_steps(EXAMPLE / "look-hub.yaml")
    assert [next(iter(s)) for s in steps][-1] == "shot"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_example_home.py -q 2>&1 | tail -1`

Expected: `3 failed` in the repository's working copy, where `catalog/vds/zigbee.json` exists (`2 failed, 1 skipped` without a local catalog): the example's files do not exist yet

- [ ] **Step 3: Create the example home and the run book**

Create `examples/lab/test-house/home.yaml`:
```yaml
# VESPER Lab's example home: the small test house (hall, kitchen, bedroom, porch) with real Zigbee products.
#   vesper lab check examples/lab/test-house/home.yaml
#   vesper lab play  examples/lab/test-house/home.yaml          # the map, on the wall clock
#   vesper lab play  examples/lab/test-house/home.yaml --hub    # with VESPER's Home Assistant in the loop
version: 2
scene: test/house
seed: 0
start_time: "19:30"
avatar: {model: female_0, start: {room: kitchen}}
doors:
  - {doorway: bedroom-hall, open: true}
devices:
  - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: hall-porch}}
  - {product: "zigbee:philips:9290012607", name: hall_motion, at: {room: hall, mount: ceiling}}
  - {product: "zigbee:ikea:led1545g12", name: bedroom_lamp, at: {room: bedroom, xy: [5.0, 5.0], mount: table}}
  - {product: "zigbee:ikea:e1743", name: bedroom_switch, at: {room: bedroom, xy: [3.2, 3.5], mount: wall},
     controls: [bedroom_lamp]}
  - {product: "zigbee:aqara:wsdcgq11lm", name: kitchen_climate, at: {room: kitchen, xy: [6.5, 0.5], mount: wall,
     facing: 90}}
  - {product: "zigbee:innr:sp-120", name: kitchen_plug, at: {room: kitchen, xy: [4.0, 0.5], mount: floor}}
automations: automations.yaml
```

Create `examples/lab/test-house/map.json`:
```json
{"format": "vesper-map/1", "scene": "test/house", "grid": {"origin": [-2.0, 0.0], "cell": 0.1, "width": 90, "height": 60, "walkable": "AADg////+f////8DAID////n/////w8AAP7//5//////PwAA+P//f/7/////AADg////+f////8DAID////n/////w8AAP7//5//////PwAA+P//f/7/////AADg////+f////8DAID////n/////w8AAP7/////////PwAA+P//////////AADg//////////8DAID//////////w8AAP7/////////PwAA+P//////////AADg//////////8DAID//////////w8AAP7/////////PwAA+P/////////////n////+f///////5/////n////////f/7//5//////////+f//f/7////////n////+f/////////////n/////////////5//////////////f/7/////////////+f////////////8HAAAAAPD//////x8AAAAAwP//////f/7/////////////+f/////////////n/////////////5//////////+f//f/7////////n////+f///////5/////n////////f/7//5//////////+f//f/7/////AADg//////////8DAID//////////w8AAP7/////////PwAA+P//////////AADg//////////8DAID//////////w8AAP7/////////PwAA+P//////////AADg//////////8DAID//////////w8AAP7//5//////PwAA+P//f/7/////AADg////+f////8DAID////n/////w8AAP7//5//////PwAA+P//f/7/////AADg////+f////8DAID////n/////w8AAP7//5//////PwAA+P//f/7/////"}, "rooms": [{"id": "hall", "label": "hallway", "polygon": [[0.1, 0], [2.9, 0], [2.9, 6], [0.1, 6]], "outdoor": false}, {"id": "kitchen", "label": "kitchen", "polygon": [[3.1, 0], [7, 0], [7, 2.9], [3.1, 2.9]], "outdoor": false}, {"id": "bedroom", "label": "bedroom", "polygon": [[3.1, 3.1], [7, 3.1], [7, 6], [3.1, 6]], "outdoor": false}, {"id": "porch", "label": "porch/terrace/deck/driveway", "polygon": [[-2, 2], [-0.1, 2], [-0.1, 4], [-2, 4]], "outdoor": true}]}
```

Create `examples/lab/test-house/automations.yaml`:
```yaml
# Home Assistant automations for the example home, loaded into VESPER's instance by `vesper lab play --hub`.
# Edits made in Home Assistant's automation editor are written back here when play stops.
- id: vesper_example_hall_lamp
  alias: Bedroom lamp on when someone is in the hall
  triggers:
    - trigger: state
      entity_id: binary_sensor.v3702287e0bbb1e03_occupancy
      to: "on"
  actions:
    - action: light.turn_on
      target:
        entity_id: light.v7c530a5e0539de76_light
```

Create `examples/lab/test-house/look-hub.yaml`:
```yaml
# vesper lab look examples/lab/test-house/home.yaml --hub --steps examples/lab/test-house/look-hub.yaml
# The hall's motion sensor turns the bedroom lamp on through Home Assistant's automation (automations.yaml).
- {shot: start}
- {click_world: [1.5, 1.5]}
- {until: "vesper.state.actors.player.room === 'hall'", label: "the player walks into the hall", timeout: 15}
- {until: "vesper.state.effects.bedroom_lamp.on === true", label: "Home Assistant's automation turns the bedroom lamp on",
   timeout: 15}
- {wait: 0.5}
- {shot: lamp_on_by_automation}
```

Create `docs/experiments/lab-runbook.md`:
```markdown
# Run book: VESPER Lab, live checks

The Lab's unit tests run against an in-memory broker and Home Assistant. These checks run it against VESPER's own Mosquitto and Home Assistant, so they start real services. On the shared lab Mac each one needs the PI's go and a slot agreed in the ledger: never during another project's experiment, with ports 18830, 18123 and 18080 free, `taskpolicy -b`, at most 2 cores.

## Before any check

1. Build the catalog with the current code: `vesper catalog build`. Commands refuse a catalog built by other catalog code.
2. Mosquitto is installed, and Home Assistant is set up once with `vesper setup ha` (see the device-twins run book, "Before any run").
3. For `vesper lab look`: a Chromium-based browser. Playwright's cached `chrome-headless-shell` is found first, then Google Chrome; `VESPER_BROWSER` names another.

## Lab check 1: Home Assistant in the loop

    taskpolicy -b .venv/bin/vesper lab play examples/lab/test-house/home.yaml --hub

What it does:
1. Starts VESPER's Mosquitto on 127.0.0.1:18830 and its Home Assistant on 127.0.0.1:18123. The Lab's configuration differs from the experiments' in two lines only: the play page may frame the dashboard (`use_x_frame_options: false`), and the home's `automations.yaml` is loaded (Home Assistant's automation editor writes to it).
2. Onboards Home Assistant if needed, adds the MQTT integration, and sets the Lab login once (kept in `.vesper/ha/lab-login.json`, readable by its owner only).
3. Puts the home's Zigbee devices on the broker through the Zigbee2MQTT bridge. Matter devices are not on the hub yet (plan W2d); the list says so.
4. Opens the page at http://127.0.0.1:18080/ and prints the login and each device's Home Assistant entities.

Check, each one a pass criterion:
1. The hub panel shows Home Assistant's dashboard, and the printed login works inside the page.
2. Home Assistant lists the six devices, with the printed entities.
3. Walking from the kitchen into the hall turns the hall's motion entity on in Home Assistant within about a second. The automation "Bedroom lamp on when someone is in the hall" fires, the bedroom lamp glows on the map, and the console shows `the hub sets bedroom_lamp state = "ON"`.
4. Switching the kitchen plug on in Home Assistant turns its icon on in the map.
5. Pressing E at the bedroom switch toggles the lamp through its binding, and Home Assistant shows it.
6. An automation edited in Home Assistant is written back to `examples/lab/test-house/automations.yaml` when play stops.
7. Ctrl-C stops the page, the bridge, Home Assistant and Mosquitto, and frees the ports.
8. `vesper lab replay <the printed trace> --home examples/lab/test-house/home.yaml` says the reports are identical. A wall-clock trace is compared per device, with report times within 0.5 s.

Then, for the record, the same story as an automatic look:

    taskpolicy -b .venv/bin/vesper lab look examples/lab/test-house/home.yaml --hub \
        --steps examples/lab/test-house/look-hub.yaml --out results/lab/hub-<UTC>

Pass: exit code 0, and the shots read: the lamp lit after the walk, by the automation.

Budget (estimate until the first run measures it): Home Assistant 0.5–1 GB and one core while it starts (minutes on a first start); Mosquitto and the page negligible; a headless browser about 0.3 GB for a minute.

Record in the ledger: the time, the trace path, the look's `report.md`, and anything that failed.

## When something goes wrong

- **A port is in use:** another VESPER instance, or another project's service. The Lab never shares a broker or a Home Assistant; stop the other one or wait for its slot.
- **The login fails:** delete `.vesper/ha/lab-login.json`; the next `--hub` sets a new one.
- **Entities are missing in Home Assistant:** check that the MQTT integration points at 127.0.0.1:18830 (Settings, Devices & services).
```

- [ ] **Step 4: Run them, then the whole suite**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `166 passed, 1 skipped` in the working copy (`165 passed, 2 skipped` without a local catalog)

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `647 passed, 13 skipped` in the repository's working copy (an export without the local research data and catalog reports `645 passed, 15 skipped`)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add examples/lab/test-house/home.yaml examples/lab/test-house/map.json examples/lab/test-house/automations.yaml examples/lab/test-house/look-hub.yaml docs/experiments/lab-runbook.md tests/lab/test_example_home.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "docs(lab): an example home with an automation, and the run book for the live hub check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## After this plan

- **The live check** (`docs/experiments/lab-runbook.md`, Lab check 1), after another project's run and with the PI's go: rebuild the catalog, then play the example home with the hub, and look.
- **W2d:** Matter devices on the hub (the Matter tier's host and commissioning, per run); the placement editor (spec §2 edit mode).
- **W3** residents, **W4** the security lab, **W1** the Habitat world service and the 3D view.
