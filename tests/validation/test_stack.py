import json
import socket
import subprocess

import httpx
import pytest

from vesper.validation.stack import (HA_VERSION, MATTER_SERVER_PORT, RECOVERY_MARK, HomeAssistant, MatterServer,
                                     Mosquitto, Paths, StackError, port_free, setup_ha, setup_matter)


def test_setup_installs_the_pinned_release_and_the_needed_integrations_packages(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1:2] == ["-c"]:
            extras = ["aiohasupervisor==0.6.0", "paho-mqtt==2.1.0"]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(extras))
        return subprocess.CompletedProcess(cmd, 0)

    reqs = setup_ha(tmp_path, run=run, which=lambda n: f"/bin/{n}")
    assert reqs == ["aiohasupervisor==0.6.0", "paho-mqtt==2.1.0"]
    assert ["/bin/uv", "venv", "--python", "3.14", str(tmp_path / "ha" / "venv")] in calls
    assert calls[1][-1] == f"homeassistant=={HA_VERSION}" and calls[3][-2:] == reqs
    assert calls[2][3:] == ["frontend", "mqtt", "hassio", "matter", "tasmota", "camera", "stream"]


def test_setup_needs_uv(tmp_path):
    with pytest.raises(StackError, match="needs uv"):
        setup_ha(tmp_path, which=lambda n: None)


def busy_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_services_refuse_ports_that_are_already_in_use(tmp_path):
    s, port = busy_port()
    with s:
        assert not port_free(port)
        with pytest.raises(StackError, match="already in use"):
            Mosquitto(tmp_path, port=port, binary="/bin/true").start()
        ha = HomeAssistant(tmp_path, port=port)
        Paths(tmp_path).hass.parent.mkdir(parents=True)
        Paths(tmp_path).hass.touch()
        with pytest.raises(StackError, match="already in use"):
            ha.start()


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def installed(tmp_path):
    Paths(tmp_path).hass.parent.mkdir(parents=True, exist_ok=True)
    Paths(tmp_path).hass.touch()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_recovery_mode_is_refused_and_home_assistant_stopped(tmp_path):
    installed(tmp_path)
    proc = FakeProc()

    def popen(cmd, stdout, stderr):
        stdout.write(f"... WARNING [homeassistant.bootstrap] {RECOVERY_MARK}\n")
        stdout.flush()
        return proc

    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    ha = HomeAssistant(tmp_path, port=free_port(), popen=popen, transport=down, start_timeout=5)
    with pytest.raises(StackError, match="recovery mode"):
        ha.start()
    assert proc.terminated


def test_start_writes_a_loopback_config_and_waits_for_the_api(tmp_path):
    installed(tmp_path)
    seen = {}

    def popen(cmd, stdout, stderr):
        seen["cmd"] = cmd
        return FakeProc()

    port = free_port()
    ha = HomeAssistant(tmp_path, port=port, popen=popen, transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    ha.start()
    cfg = (tmp_path / "ha" / "config" / "configuration.yaml").read_text()
    assert f"server_port: {port}" in cfg and "server_host: 127.0.0.1" in cfg
    assert seen["cmd"][-1] == "--skip-pip"


class FakeServer:
    """Answers the onboarding, token, state and config-entry endpoints like Home Assistant 2026.10.

    As in Home Assistant's REST API, listed config entries carry no `data` (config_entries.py); the
    broker an entry points at is visible only in this fake's own `entries`.
    """

    def __init__(self, entries=None, flow_step="broker", flow_result="create_entry", onboarded=False, storage=None,
                 persists=True, matter_reachable=True, template=(200, "[]")):
        self.entries, self.flow_step, self.flow_result, self.requests = entries or [], flow_step, flow_result, []
        self.onboarded, self.storage, self.persists = onboarded, storage, persists
        self.ids = [f"e{i}" for i in range(len(self.entries))]  # entry ids stay put when another entry goes
        self.next_id = len(self.entries)
        self.matter_reachable, self.template, self.templates, self.handler = matter_reachable, template, [], None

    def _add(self, entry):
        self.entries.append(entry)
        self.ids.append(f"e{self.next_id}")
        self.next_id += 1

    def __call__(self, request):
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/api/onboarding/users":
            if self.onboarded:
                return httpx.Response(403, json={"message": "User step already done"})
            return httpx.Response(200, json={"auth_code": "code"})
        if path == "/auth/token":
            if self.storage is not None and self.persists:  # Home Assistant's own auth store, as written to disk
                self.storage.mkdir(parents=True, exist_ok=True)
                (self.storage / "auth").write_text(json.dumps({"data": {"refresh_tokens": [{"token": "refresh"}]}}))
            return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh",
                                             "expires_in": 1800, "token_type": "Bearer"})
        if path.startswith("/api/onboarding/"):
            return httpx.Response(200, json={})
        if path == "/api/config/config_entries/entry":
            domain = request.url.params.get("domain")
            return httpx.Response(200, json=[{"entry_id": i, "domain": e["domain"], "title": e["domain"]}
                                             for i, e in zip(self.ids, self.entries)
                                             if domain in (None, e["domain"])])
        if path.startswith("/api/config/config_entries/entry/") and request.method == "DELETE":
            k = self.ids.index(path.rsplit("/", 1)[1])
            del self.entries[k], self.ids[k]
            return httpx.Response(200, json={"require_restart": False})
        if path == "/api/template":
            self.templates.append(json.loads(request.content)["template"])
            status, text = self.template
            return httpx.Response(status, text=text)
        if path == "/api/states/sensor.broken":
            return httpx.Response(500, text="Internal Server Error")
        if path.startswith("/api/states/"):
            return httpx.Response(404, json={"message": "Entity not found."})
        if path == "/api/config/config_entries/flow":
            self.handler = json.loads(request.content)["handler"]
            # Without a Supervisor, Home Assistant's Matter flow goes straight to its manual step
            # (matter/config_flow.py); Tasmota's user flow goes to its config step (tasmota/config_flow.py)
            step = {"matter": "manual", "tasmota": "config"}.get(self.handler, self.flow_step)
            return httpx.Response(200, json={"flow_id": "f1", "type": "form", "step_id": step})
        if path == "/api/config/config_entries/flow/f1":
            body = json.loads(request.content)
            if self.handler == "tasmota":
                if set(body) - {"discovery_prefix"}:
                    return httpx.Response(400, json={"message": "extra keys not allowed"})
                self._add({"domain": "tasmota", "data": {"discovery_prefix": body.get("discovery_prefix",
                                                                                      "tasmota/discovery")}})
                return httpx.Response(200, json={"type": "create_entry"})
            if self.handler == "matter":
                if set(body) != {"url"}:
                    return httpx.Response(400, json={"message": "extra keys not allowed"})
                if not self.matter_reachable:
                    return httpx.Response(200, json={"type": "form", "step_id": "manual",
                                                     "errors": {"base": "cannot_connect"}})
                self._add({"domain": "matter", "data": body})
                return httpx.Response(200, json={"type": "create_entry"})
            if "other_settings" not in body:  # Home Assistant 2026.10's broker step requires this section
                return httpx.Response(200, json={"type": "form", "step_id": "broker",
                                                 "errors": {"other_settings": "required key not provided"}})
            self._add({"domain": "mqtt", "data": body})
            return httpx.Response(200, json={"type": self.flow_result})
        return httpx.Response(404)


def onboarded(tmp_path, server):
    server.storage = Paths(tmp_path).ha_config / ".storage"
    ha = HomeAssistant(tmp_path, transport=httpx.MockTransport(server), persist_timeout=0.2)
    (tmp_path / "ha").mkdir(parents=True, exist_ok=True)
    ha.onboard()
    return ha


def test_an_onboarded_home_assistant_without_saved_credentials_says_how_to_reset(tmp_path):
    with pytest.raises(StackError, match=r"already onboarded.*ha/config/\.storage"):
        onboarded(tmp_path, FakeServer(onboarded=True))
    assert not (tmp_path / "ha" / "auth.json").exists()


def test_onboarding_runs_once_and_keeps_only_a_private_refresh_token(tmp_path):
    server = FakeServer()
    onboarded(tmp_path, server)
    auth = Paths(tmp_path).auth_file
    assert json.loads(auth.read_text())["refresh_token"] == "refresh" and auth.stat().st_mode & 0o777 == 0o600
    steps = [p for m, p in server.requests if p.startswith("/api/onboarding/")]
    assert steps == ["/api/onboarding/users", "/api/onboarding/core_config", "/api/onboarding/analytics",
                     "/api/onboarding/integration"]
    onboarded(tmp_path, server)
    assert len([p for m, p in server.requests if p == "/api/onboarding/users"]) == 1


def test_each_run_recreates_the_mqtt_entry_for_vespers_broker(tmp_path):
    server = FakeServer()
    ha = onboarded(tmp_path, server)
    ha.ensure_mqtt(18830)
    ha.ensure_mqtt(18831)
    assert server.entries == [{"domain": "mqtt", "data": {
        "broker": "127.0.0.1", "port": 18831,
        "other_settings": {"set_client_cert": False, "set_ca_cert": "off", "transport": "tcp"}}}]


def test_an_mqtt_entry_for_another_broker_is_replaced(tmp_path):
    """The REST API does not show an entry's broker, so VESPER never trusts an existing entry."""
    server = FakeServer(entries=[{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 1883}}])
    ha = onboarded(tmp_path, server)
    ha.ensure_mqtt(18830)
    assert [e["data"]["port"] for e in server.entries] == [18830]
    assert ("DELETE", "/api/config/config_entries/entry/e0") in server.requests


def test_a_home_assistant_error_on_a_state_read_is_not_a_missing_entity(tmp_path):
    ha = onboarded(tmp_path, FakeServer())
    assert ha.state("sensor.absent") is None
    with pytest.raises(StackError, match="500"):
        ha.state("sensor.broken")


def test_a_mosquitto_that_never_listens_is_stopped(tmp_path, monkeypatch):
    import vesper.validation.stack as stack

    proc = FakeProc()

    def never(port, timeout, host="127.0.0.1"):
        raise StackError("nothing listens")

    monkeypatch.setattr(stack, "wait_for_port", never)
    with pytest.raises(StackError):
        Mosquitto(tmp_path, port=free_port(), popen=lambda cmd: proc, binary="/bin/true").start()
    assert proc.terminated


def test_a_mosquitto_that_ignores_terminate_is_killed(tmp_path):
    class Stubborn(FakeProc):
        killed = False

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("mosquitto", timeout)
            return 0

        def kill(self):
            self.killed = True

    proc = Stubborn()
    m = Mosquitto(tmp_path, binary="/bin/true", proc=proc)
    m.stop()
    assert proc.killed and m.proc is None


def test_an_unexpected_flow_is_refused(tmp_path):
    ha = onboarded(tmp_path, FakeServer(flow_step="addon"))
    with pytest.raises(StackError, match="unexpected MQTT config flow step"):
        ha.ensure_mqtt(18830)


def test_onboarding_fails_if_home_assistant_never_saves_the_new_token(tmp_path):
    """Home Assistant writes its auth store with a delay; a token it never saved is refused on the next start."""
    with pytest.raises(StackError, match="did not save"):
        onboarded(tmp_path, FakeServer(persists=False))
    assert not Paths(tmp_path).auth_file.exists()


def matter_sources(tmp_path, lock=True):
    src = tmp_path / "src"
    src.mkdir()
    (src / "package.json").write_text(json.dumps({"dependencies": {"@matter/main": "0.17.9",
                                                                    "matter-server": "1.4.0"}}))
    if lock:
        (src / "package-lock.json").write_text("{}")
    (src / "host.mjs").write_text("// host")
    (src / "host.test.mjs").write_text("// tests stay with the sources")
    return src


def node_runner(version="v22.13.0"):  # exactly the floor
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("cwd")))
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=version + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    return run, calls


def test_setup_matter_installs_the_pinned_node_packages_beside_a_copy_of_the_host(tmp_path):
    run, calls = node_runner()
    pins = setup_matter(tmp_path, run=run, which=lambda n: f"/bin/{n}", source=matter_sources(tmp_path))
    host = Paths(tmp_path).matter_host
    assert pins == {"@matter/main": "0.17.9", "matter-server": "1.4.0"}
    assert sorted(p.name for p in host.iterdir()) == ["host.mjs", "package-lock.json", "package.json"]
    # no install scripts: no package code runs at install (only optional native Bluetooth has any)
    assert (["/bin/npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], host) in calls


def test_setup_matter_needs_node_and_npm(tmp_path):
    run, _ = node_runner()
    with pytest.raises(StackError, match="needs Node.js"):
        setup_matter(tmp_path, run=run, which=lambda n: None if n == "npm" else f"/bin/{n}",
                     source=matter_sources(tmp_path))


def test_setup_matter_refuses_an_old_node(tmp_path):
    run, _ = node_runner("v22.12.0")
    with pytest.raises(StackError, match="Node.js 22.13 or newer"):
        setup_matter(tmp_path, run=run, which=lambda n: f"/bin/{n}", source=matter_sources(tmp_path))


def test_setup_matter_refuses_unpinned_node_packages(tmp_path):
    run, calls = node_runner()
    with pytest.raises(StackError, match="package-lock.json"):
        setup_matter(tmp_path, run=run, which=lambda n: f"/bin/{n}", source=matter_sources(tmp_path, lock=False))
    assert not any(cmd[1:2] == ["ci"] for cmd, _ in calls)


def test_home_assistant_s_environment_is_checked_for_the_matter_packages(tmp_path):
    from vesper.validation.stack import ha_has_matter
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if seen[0] else 1)

    assert ha_has_matter(tmp_path, run=run)
    assert seen[0][0] == str(Paths(tmp_path).ha_python) and "import matter_server.client" in seen[0][-1]
    assert not ha_has_matter(tmp_path, run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    assert not ha_has_matter(tmp_path, run=lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError(cmd[0])))


def installed_matter_server(tmp_path):
    binary = Paths(tmp_path).matter_server_js
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    return binary


def test_the_matter_server_serves_home_assistant_on_loopback_from_vespers_storage(tmp_path, monkeypatch):
    import vesper.validation.stack as stack

    binary, seen = installed_matter_server(tmp_path), {}
    monkeypatch.setattr(stack, "wait_for_port", lambda port, timeout, host="127.0.0.1": seen.setdefault("port", port))

    def popen(cmd, stdout, stderr):
        seen["cmd"] = cmd
        return FakeProc()

    port = free_port()
    server = MatterServer(tmp_path, tmp_path / "run" / "server", port=port, interface="en0", popen=popen)
    server.start()
    cmd = seen["cmd"]
    assert cmd[:2] == ["node", str(binary)] and seen["port"] == port and server.url == f"ws://127.0.0.1:{port}/ws"
    assert binary.parts[-4:] == ("matter-server", "dist", "esm", "MatterServer.js")  # the package has no bin
    for flag, value in (("--storage-path", str(tmp_path / "run" / "server")), ("--port", str(port)),
                        ("--listen-address", "127.0.0.1"), ("--primary-interface", "en0")):
        assert cmd[cmd.index(flag) + 1] == value
    assert {"--disable-dashboard", "--disable-ota", "--disable-thread-diagnostics"} <= set(cmd)
    # matter.js nodes carry test attestation certificates, which the server refuses without the test DCL
    assert "--enable-test-net-dcl" in cmd
    assert (tmp_path / "run" / "server").is_dir() and (tmp_path / "run" / "matter-server.log").exists()
    assert MATTER_SERVER_PORT != 5580  # the default Matter Server port stays free for a household's own


def test_the_matter_server_needs_setup(tmp_path):
    with pytest.raises(StackError, match="vesper setup matter"):
        MatterServer(tmp_path, tmp_path / "server", port=free_port()).start()


def test_a_matter_server_that_never_listens_is_stopped(tmp_path, monkeypatch):
    import vesper.validation.stack as stack

    installed_matter_server(tmp_path)
    proc = FakeProc()

    def never(port, timeout, host="127.0.0.1"):
        raise StackError("nothing listens")

    monkeypatch.setattr(stack, "wait_for_port", never)
    with pytest.raises(StackError):
        MatterServer(tmp_path, tmp_path / "server", port=free_port(), popen=lambda cmd, stdout, stderr: proc).start()
    assert proc.terminated


def test_a_busy_matter_server_port_is_refused(tmp_path):
    installed_matter_server(tmp_path)
    s, port = busy_port()
    with s, pytest.raises(StackError, match="already in use"):
        MatterServer(tmp_path, tmp_path / "server", port=port).start()


def test_each_run_recreates_the_matter_entry_and_leaves_mqtt_alone(tmp_path):
    server = FakeServer(entries=[{"domain": "matter", "data": {"url": "ws://localhost:5580/ws"}},
                                 {"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}}])
    ha = onboarded(tmp_path, server)
    ha.ensure_matter("ws://127.0.0.1:15580/ws")
    assert server.entries == [{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}},
                              {"domain": "matter", "data": {"url": "ws://127.0.0.1:15580/ws"}}]
    ha.ensure_mqtt(18831)
    assert [e["domain"] for e in server.entries] == ["matter", "mqtt"]


def test_a_matter_server_home_assistant_cannot_reach_fails_the_run(tmp_path):
    ha = onboarded(tmp_path, FakeServer(matter_reachable=False))
    with pytest.raises(StackError, match="Matter integration was not configured.*cannot_connect"):
        ha.ensure_matter("ws://127.0.0.1:15580/ws")


def test_matter_entities_are_found_by_the_twins_serial_number(tmp_path):
    rows = [["light.vesper_0001", "VSP-0A1B"], ["sensor.vesper_0001_power", "VSP-0A1B"],
            ["binary_sensor.vesper_0002", "VSP-FF00"], ["sensor.other", None]]
    server = FakeServer(template=(200, json.dumps(rows)))
    ha = onboarded(tmp_path, server)
    assert ha.matter_entities() == {"VSP-0A1B": ["light.vesper_0001", "sensor.vesper_0001_power"],
                                    "VSP-FF00": ["binary_sensor.vesper_0002"]}
    assert "integration_entities('matter')" in server.templates[0] and "serial_number" in server.templates[0]


def test_a_template_error_is_not_an_empty_home(tmp_path):
    ha = onboarded(tmp_path, FakeServer(template=(400, "Error rendering template")))
    with pytest.raises(StackError, match="400"):
        ha.matter_entities()


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
    assert "use_x_frame_options" not in text  # HA 2026.10 reads http: from YAML once; the store decides (below)
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


def test_every_start_owns_home_assistant_s_http_settings_store(tmp_path):
    """HA 2026.10 keeps its HTTP settings in .storage/http and reads YAML only on a first boot. A pending trial
    (what a YAML migration leaves) is reverted after 5 minutes by restarting on 0.0.0.0:8123. So VESPER writes
    the store before every start: stable = this mode's settings, no pending trial."""
    installed(tmp_path)
    store = tmp_path / "ha" / "config" / ".storage" / "http"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 2, "minor_version": 2, "key": "http", "data": {
        "stable": {"server_port": 8123, "use_x_frame_options": True}, "yaml_migration_done": True,
        "pending": {"server_port": 18123, "server_host": ["127.0.0.1"], "error": None}}}))  # this Mac's, 10-24
    for frameable in (False, True):
        port = free_port()
        ha = HomeAssistant(tmp_path, port=port, popen=lambda cmd, stdout, stderr: FakeProc(), frameable=frameable,
                           transport=httpx.MockTransport(lambda r: httpx.Response(401)))
        ha.start()
        saved = json.loads(store.read_text())
        assert (saved["version"], saved["minor_version"], saved["key"]) == (2, 2, "http")
        data = saved["data"]
        assert data["pending"] is None and data["yaml_migration_done"] is True
        stable = data["stable"]
        assert (stable["server_host"], stable["server_port"]) == (["127.0.0.1"], port)
        assert stable["use_x_frame_options"] is (not frameable)  # the experiments keep the frame protection
        assert (stable["error"], stable["error_message"], stable["ip_ban_enabled"]) == (None, None, True)


def test_a_new_onboarding_forgets_the_old_lab_login(tmp_path):
    stale = tmp_path / "ha" / "lab-login.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"username": "vesper", "password": "for-an-owner-that-is-gone"}))
    onboarded(tmp_path, FakeServer())
    assert not stale.exists()


def test_a_lab_login_that_cannot_reach_home_assistant_is_a_stack_error(tmp_path):
    ha = HomeAssistant(tmp_path, port=free_port())
    ha.token = lambda: "token-1"
    with pytest.raises(StackError, match="Lab login"):
        ha.lab_login()


def test_each_run_recreates_one_tasmota_entry_on_its_discovery_prefix(tmp_path):
    """Home Assistant's Tasmota integration (plan W7a2) allows one entry; it reads the devices' discovery."""
    server = FakeServer(entries=[{"domain": "tasmota", "data": {"discovery_prefix": "old/prefix"}},
                                 {"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}}])
    ha = onboarded(tmp_path, server)
    ha.ensure_tasmota()
    ha.ensure_tasmota()
    assert server.entries == [{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}},
                              {"domain": "tasmota", "data": {"discovery_prefix": "tasmota/discovery"}}]


def test_setup_installs_what_the_tasmota_integration_imports():
    """Home Assistant's Tasmota integration loads its camera platform, which imports `stream` (numpy, PyAV): without
    their requirements the integration fails to set up and no Tasmota entity appears (found live, plan W7a2)."""
    from vesper.validation.stack import NEEDED_INTEGRATIONS
    assert {"tasmota", "camera", "stream"} <= set(NEEDED_INTEGRATIONS)
