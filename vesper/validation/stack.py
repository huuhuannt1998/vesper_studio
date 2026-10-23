"""VESPER's own Mosquitto and Home Assistant Core, for acceptance tests (spec §4 L2; §5 `[ha]`).

Only VESPER's instances are used, on loopback ports that differ from the defaults (MQTT 18830, HA 18123),
so a machine already running Mosquitto or Home Assistant is never touched. Home Assistant runs from its
own Python 3.14 venv with `--skip-pip`: `setup_ha` installs the pinned release and exactly the
requirements of the integrations VESPER needs. If Home Assistant falls into recovery mode, which would
make it bind the default port 8123, it is stopped and the run fails.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

HA_VERSION = "2026.10.2"
HA_PYTHON = "3.14"
NEEDED_INTEGRATIONS = ("frontend", "mqtt", "hassio")  # bootstrap needs frontend; mqtt's flow imports hassio
MQTT_PORT, HA_PORT = 18830, 18123
RECOVERY_MARK = "Activating recovery mode"
_REQS_SCRIPT = """
import json, sys, importlib.resources as r, importlib.metadata as md
root = r.files("homeassistant") / "components"
seen, reqs, todo = set(), set(), list(sys.argv[1:])
while todo:
    d = todo.pop()
    if d in seen:
        continue
    seen.add(d)
    m = json.loads((root / d / "manifest.json").read_text())
    reqs.update(m.get("requirements", []))
    todo.extend(m.get("dependencies", []))
print(json.dumps(sorted(reqs)))
"""


class StackError(RuntimeError):
    """The local Mosquitto or Home Assistant could not be prepared, started or configured."""


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def ha_venv(self) -> Path:
        return self.root / "ha" / "venv"

    @property
    def ha_python(self) -> Path:
        return self.ha_venv / "bin" / "python"

    @property
    def hass(self) -> Path:
        return self.ha_venv / "bin" / "hass"

    @property
    def ha_config(self) -> Path:
        return self.root / "ha" / "config"

    @property
    def ha_log(self) -> Path:
        return self.root / "ha" / "hass.log"

    @property
    def auth_file(self) -> Path:
        return self.root / "ha" / "auth.json"

    @property
    def mqtt_conf(self) -> Path:
        return self.root / "mqtt" / "mosquitto.conf"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def wait_for_port(port: int, timeout: float, host: str = "127.0.0.1") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_free(port, host):
            return
        time.sleep(0.2)
    raise StackError(f"nothing listens on {host}:{port} after {timeout:.0f} s")


def setup_ha(root: Path, *, run=subprocess.run, which=shutil.which) -> list[str]:
    """Create Home Assistant's venv and install the pinned release plus the needed integrations' packages."""
    uv = which("uv")
    if not uv:
        raise StackError("setting up Home Assistant needs uv on PATH (https://docs.astral.sh/uv/)")
    paths = Paths(Path(root))
    try:
        if not paths.ha_python.exists():
            run([uv, "venv", "--python", HA_PYTHON, str(paths.ha_venv)], check=True)
        run([uv, "pip", "install", "--python", str(paths.ha_python), f"homeassistant=={HA_VERSION}"], check=True)
        out = run([str(paths.ha_python), "-c", _REQS_SCRIPT, *NEEDED_INTEGRATIONS], check=True,
                  capture_output=True, text=True).stdout
        requirements = json.loads(out)
        if requirements:
            run([uv, "pip", "install", "--python", str(paths.ha_python), *requirements], check=True)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        raise StackError(f"Home Assistant set-up failed: {e}") from e
    return requirements


def mosquitto_binary(which=shutil.which) -> str:
    for candidate in (which("mosquitto"), "/opt/homebrew/sbin/mosquitto", "/usr/local/sbin/mosquitto",
                      "/usr/sbin/mosquitto"):
        if candidate and Path(candidate).exists():
            return candidate
    raise StackError("Mosquitto not found; install it natively (macOS: brew install mosquitto; "
                     "Debian/Ubuntu: apt install mosquitto)")


@dataclass
class Mosquitto:
    root: Path
    port: int = MQTT_PORT
    popen: object = subprocess.Popen
    binary: str | None = None
    proc: object = None

    def start(self) -> None:
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a broker")
        conf = Paths(Path(self.root)).mqtt_conf
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(f"listener {self.port} 127.0.0.1\nallow_anonymous true\npersistence false\n"
                        f"log_dest file {conf.parent.resolve() / 'mosquitto.log'}\n")
        self.proc = self.popen([self.binary or mosquitto_binary(), "-c", str(conf)])
        wait_for_port(self.port, 10)

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            self.proc = None


@dataclass
class HomeAssistant:
    root: Path
    port: int = HA_PORT
    popen: object = subprocess.Popen
    transport: httpx.BaseTransport | None = None
    start_timeout: float = 300.0
    proc: object = None
    _access: tuple[str, float] | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=30.0, transport=self.transport)

    def write_config(self) -> None:
        cfg = Paths(Path(self.root)).ha_config
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "configuration.yaml").write_text(
            "homeassistant:\n  name: VESPER\n  unit_system: metric\n  time_zone: UTC\n  country: US\n"
            "  currency: USD\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {self.port}\n"
            "api:\nconfig:\nonboarding:\nlogger:\n  default: warning\n")

    def start(self) -> None:
        paths = Paths(Path(self.root))
        if not paths.hass.exists():
            raise StackError("Home Assistant is not installed; run `vesper setup ha` first")
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a Home Assistant")
        self.write_config()
        log = paths.ha_log.open("w")
        self.proc = self.popen([str(paths.hass), "-c", str(paths.ha_config), "--skip-pip"], stdout=log,
                               stderr=subprocess.STDOUT)
        deadline = time.monotonic() + self.start_timeout
        with self._client() as c:
            while time.monotonic() < deadline:
                if RECOVERY_MARK in paths.ha_log.read_text(errors="replace"):
                    self.stop()
                    raise StackError("Home Assistant entered recovery mode (it would bind port 8123); "
                                     f"see {paths.ha_log}")
                try:
                    if c.get("/api/").status_code in (200, 401):
                        return
                except httpx.TransportError:
                    pass
                time.sleep(1)
        self.stop()
        raise StackError(f"Home Assistant did not answer on {self.url} within {self.start_timeout:.0f} s")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    # -- authentication -------------------------------------------------------------------------
    def onboard(self) -> None:
        """Create the local owner account once; afterwards reuse its refresh token."""
        auth = Paths(Path(self.root)).auth_file
        if auth.exists():
            return
        cid = f"{self.url}/"
        with self._client() as c:
            r = c.post("/api/onboarding/users", json={"client_id": cid, "name": "VESPER", "username": "vesper",
                                                      "password": os.urandom(16).hex(), "language": "en"})
            if r.status_code == 403:
                storage = Paths(Path(self.root)).ha_config / ".storage"
                raise StackError(f"Home Assistant is already onboarded but {auth} is missing; stop it, delete "
                                 f"{storage} (VESPER's own instance) and run again")
            if r.status_code != 200:
                raise StackError(f"onboarding failed ({r.status_code}): {r.text[:200]}")
            tokens = c.post("/auth/token", data={"grant_type": "authorization_code", "code": r.json()["auth_code"],
                                                 "client_id": cid}).json()
            h = {"Authorization": f"Bearer {tokens['access_token']}"}
            for step, body in (("core_config", {}), ("analytics", {}),
                               ("integration", {"client_id": cid, "redirect_uri": f"{cid}?auth_callback=1"})):
                c.post(f"/api/onboarding/{step}", json=body, headers=h).raise_for_status()
        auth.write_text(json.dumps({"refresh_token": tokens["refresh_token"], "client_id": cid}))
        auth.chmod(0o600)

    def token(self) -> str:
        if self._access and self._access[1] > time.monotonic() + 30:
            return self._access[0]
        saved = json.loads(Paths(Path(self.root)).auth_file.read_text())
        with self._client() as c:
            r = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
                                            "client_id": saved["client_id"]})
        if r.status_code != 200:
            raise StackError(f"Home Assistant refused the saved credentials ({r.status_code})")
        body = r.json()
        self._access = (body["access_token"], time.monotonic() + body["expires_in"])
        return body["access_token"]

    def api(self, method: str, path: str, **kwargs) -> httpx.Response:
        with self._client() as c:
            return c.request(method, path, headers={"Authorization": f"Bearer {self.token()}"}, **kwargs)

    # -- configuration and use ------------------------------------------------------------------
    def ensure_mqtt(self, broker_port: int) -> None:
        """Configure the MQTT integration for VESPER's broker only; refuse any other broker entry."""
        entries = self.api("GET", "/api/config/config_entries/entry", params={"domain": "mqtt"}).json()
        for e in entries:
            data = e.get("data") or {}
            if data and (data.get("broker"), data.get("port")) != ("127.0.0.1", broker_port):
                raise StackError(f"Home Assistant has an MQTT entry for another broker: {data}")
        if entries:
            return
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "mqtt"}).json()
        if flow.get("step_id") != "broker":
            raise StackError(f"unexpected MQTT config flow step: {flow}")
        result = self.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          json={"broker": "127.0.0.1", "port": broker_port}).json()
        if result.get("type") != "create_entry":
            raise StackError(f"the MQTT integration was not configured: {result}")

    def state(self, entity_id: str) -> dict | None:
        r = self.api("GET", f"/api/states/{entity_id}")
        return r.json() if r.status_code == 200 else None

    def call(self, domain: str, service: str, data: dict) -> None:
        r = self.api("POST", f"/api/services/{domain}/{service}", json=data)
        if r.status_code >= 400:
            raise StackError(f"service {domain}.{service} failed ({r.status_code}): {r.text[:200]}")
