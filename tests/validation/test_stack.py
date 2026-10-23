import json
import socket
import subprocess

import httpx
import pytest

from vesper.validation.stack import (HA_VERSION, RECOVERY_MARK, HomeAssistant, Mosquitto, Paths, StackError,
                                     port_free, setup_ha)


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
    assert calls[2][3:] == ["frontend", "mqtt", "hassio"]


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
    """Answers the onboarding, token and config-flow endpoints like Home Assistant 2026.10."""

    def __init__(self, entries=None, flow_step="broker", flow_result="create_entry", onboarded=False):
        self.entries, self.flow_step, self.flow_result, self.requests = entries or [], flow_step, flow_result, []
        self.onboarded = onboarded

    def __call__(self, request):
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/api/onboarding/users":
            if self.onboarded:
                return httpx.Response(403, json={"message": "User step already done"})
            return httpx.Response(200, json={"auth_code": "code"})
        if path == "/auth/token":
            return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh",
                                             "expires_in": 1800, "token_type": "Bearer"})
        if path.startswith("/api/onboarding/"):
            return httpx.Response(200, json={})
        if path == "/api/config/config_entries/entry":
            return httpx.Response(200, json=self.entries)
        if path == "/api/config/config_entries/flow":
            return httpx.Response(200, json={"flow_id": "f1", "type": "form", "step_id": self.flow_step})
        if path == "/api/config/config_entries/flow/f1":
            body = json.loads(request.content)
            self.entries.append({"domain": "mqtt", "data": body})
            return httpx.Response(200, json={"type": self.flow_result})
        return httpx.Response(404)


def onboarded(tmp_path, server):
    ha = HomeAssistant(tmp_path, transport=httpx.MockTransport(server))
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


def test_mqtt_is_configured_for_vespers_broker_only(tmp_path):
    server = FakeServer()
    ha = onboarded(tmp_path, server)
    ha.ensure_mqtt(18830)
    assert server.entries == [{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}}]
    ha.ensure_mqtt(18830)  # already configured: no second flow
    assert len([p for m, p in server.requests if p == "/api/config/config_entries/flow"]) == 1


def test_an_mqtt_entry_for_another_broker_is_refused(tmp_path):
    ha = onboarded(tmp_path, FakeServer(entries=[{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 1883}}]))
    with pytest.raises(StackError, match="another broker"):
        ha.ensure_mqtt(18830)


def test_an_unexpected_flow_is_refused(tmp_path):
    ha = onboarded(tmp_path, FakeServer(flow_step="addon"))
    with pytest.raises(StackError, match="unexpected MQTT config flow step"):
        ha.ensure_mqtt(18830)
