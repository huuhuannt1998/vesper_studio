"""VESPER's own Mosquitto, Matter Server and Home Assistant Core, for acceptance tests (spec §4 L2; §5 `[ha]`).

Only VESPER's instances are used, on loopback ports that differ from the defaults (MQTT 18830, HA 18123,
Matter Server 15580), so a machine already running Mosquitto, a Matter Server or Home Assistant is never
touched. Home Assistant runs from its own Python 3.14 venv with `--skip-pip`: `setup_ha` installs the
pinned release and exactly the requirements of the integrations VESPER needs. If Home Assistant falls into
recovery mode, which would make it bind the default port 8123, it is stopped and the run fails.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

HA_VERSION = "2026.10.2"
HA_PYTHON = "3.14"
# bootstrap needs frontend; mqtt's flow imports hassio; matter talks to VESPER's Matter Server
# The integrations whose requirements setup installs. The Tasmota integration loads its camera platform, which
# imports `stream` (numpy, PyAV): their requirements too, or it fails to set up.
NEEDED_INTEGRATIONS = ("frontend", "mqtt", "hassio", "matter", "tasmota", "camera", "stream")
MQTT_PORT, HA_PORT, MATTER_SERVER_PORT = 18830, 18123, 15580
NODE_MIN = (22, 13)  # the pinned matter-server needs Node 22.13 (spec §3, amended 2026-10-23)
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


# Rendered by Home Assistant's /api/template: [entity_id, the serial number of its device] for every Matter entity
_MATTER_ENTITIES = """{%- set ns = namespace(rows=[]) -%}
{%- for e in integration_entities('matter') -%}
{%- set ns.rows = ns.rows + [[e, device_attr(device_id(e), 'serial_number')]] -%}
{%- endfor -%}
{{ ns.rows | to_json }}"""


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

    @property
    def matter_host(self) -> Path:
        """The installed copy of the Matter host and its pinned Node packages (`vesper setup matter`)."""
        return self.root / "matter" / "host"

    @property
    def matter_server_js(self) -> Path:
        """The Matter Server's entry point; the npm package declares no executable, so Node runs this file."""
        return self.matter_host / "node_modules" / "matter-server" / "dist" / "esm" / "MatterServer.js"

    @property
    def matter_runs(self) -> Path:
        """One directory per Matter run: the server's fabric, the nodes' storage and both logs."""
        return self.root / "matter" / "runs"


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


def setup_matter(root: Path, *, run=subprocess.run, which=shutil.which, source: Path | None = None) -> dict:
    """Copy the Matter host into VESPER's root and install its Node packages exactly as locked.

    The host runs from the copy, not from the installed Python package, so `npm ci` never writes into
    site-packages. Returns the pinned dependencies from package.json.
    """
    from vesper.exposure.matter_tier import HOST_DIR
    source = Path(source or HOST_DIR)
    node, npm = which("node"), which("npm")
    if not node or not npm:
        raise StackError("setting up the Matter tier needs Node.js and npm on PATH (https://nodejs.org/)")
    if not (source / "package-lock.json").exists():
        raise StackError(f"{source} has no package-lock.json; the Node packages would not be pinned")
    try:
        version = run([node, "--version"], check=True, capture_output=True, text=True).stdout.strip()
        major, minor = (int(x) for x in version.lstrip("v").split(".")[:2])
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        raise StackError(f"cannot read the Node.js version: {e}") from e
    if (major, minor) < NODE_MIN:
        raise StackError(f"the Matter tier needs Node.js {NODE_MIN[0]}.{NODE_MIN[1]} or newer, found {version}")
    host = Paths(Path(root)).matter_host
    host.mkdir(parents=True, exist_ok=True)
    for f in source.iterdir():
        if f.is_file() and (f.name.startswith("package") or f.suffix == ".mjs") and ".test." not in f.name:
            shutil.copy2(f, host / f.name)
    try:
        run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], check=True, cwd=host)
    except (subprocess.CalledProcessError, OSError) as e:
        raise StackError(f"installing the Matter host's Node packages failed: {e}") from e
    return json.loads((host / "package.json").read_text()).get("dependencies", {})


def ha_has_matter(root: Path, *, run=subprocess.run) -> bool:
    """Whether VESPER's Home Assistant can load its Matter integration under `--skip-pip`. An environment set up
    before the Matter tier lacks matter-python-client, and Home Assistant's config flow then fails obscurely."""
    try:
        return run([str(Paths(Path(root)).ha_python), "-c", "import matter_server.client"],
                   capture_output=True).returncode == 0
    except OSError:
        return False


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
        try:
            wait_for_port(self.port, 10)
        except StackError:
            self.stop()
            raise

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None


@dataclass
class MatterServer:
    """VESPER's own Matter Server (the matter.js `matter-server`), on loopback for Home Assistant's WebSocket.

    `storage` holds its fabric. Each run gets a new directory, so no node from an earlier run is remembered;
    the log goes beside it. `interface` is the network interface its Matter traffic and mDNS use.
    """
    root: Path
    storage: Path
    port: int = MATTER_SERVER_PORT
    interface: str | None = None
    popen: object = subprocess.Popen
    start_timeout: float = 60.0
    proc: object = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def command(self) -> list[str]:
        # --enable-test-net-dcl: matter.js nodes carry test attestation certificates (a test PAA signs a PAI and
        # DAC for the product's own vendor and product id), which the server refuses without the test DCL.
        # --disable-thread-diagnostics: no mDNS probing of the lab's Thread Border Routers.
        cmd = ["node", str(Paths(Path(self.root)).matter_server_js), "--storage-path", str(self.storage),
               "--port", str(self.port), "--listen-address", "127.0.0.1", "--enable-test-net-dcl",
               "--disable-dashboard", "--disable-ota", "--disable-thread-diagnostics"]
        return cmd + (["--primary-interface", self.interface] if self.interface else [])

    def start(self) -> None:
        if not Paths(Path(self.root)).matter_server_js.exists():
            raise StackError("the Matter Server is not installed; run `vesper setup matter` first")
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a Matter Server")
        storage = Path(self.storage)
        storage.mkdir(parents=True, exist_ok=True)
        with (storage.parent / "matter-server.log").open("w") as log:
            self.proc = self.popen(self.command(), stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_for_port(self.port, self.start_timeout)
        except StackError:
            self.stop()
            raise

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
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
    persist_timeout: float = 30.0  # how long onboarding waits for Home Assistant to save the new token
    frameable: bool = False  # the Lab's play page frames the dashboard (spec §2); nothing else is relaxed
    automations: str | None = None  # the Lab's: automations.yaml, loaded and editable in Home Assistant's UI
    _access: tuple[str, float] | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=30.0, transport=self.transport)

    def write_config(self) -> None:
        cfg = Paths(Path(self.root)).ha_config
        cfg.mkdir(parents=True, exist_ok=True)
        automation = ""
        if self.automations is not None:
            (cfg / "automations.yaml").write_text(self.automations)
            automation = "automation: !include automations.yaml\n"
        (cfg / "configuration.yaml").write_text(
            "homeassistant:\n  name: VESPER\n  unit_system: metric\n  time_zone: UTC\n  country: US\n"
            "  currency: USD\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {self.port}\n"
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
        from websockets.exceptions import WebSocketException
        password = secrets.token_urlsafe(18)
        try:
            with connect(f"ws://127.0.0.1:{self.port}/api/websocket", proxy=None, open_timeout=30) as ws:
                json.loads(ws.recv(timeout=30))  # auth_required
                ws.send(json.dumps({"type": "auth", "access_token": self.token()}))
                if json.loads(ws.recv(timeout=30)).get("type") != "auth_ok":
                    raise StackError("Home Assistant refused VESPER's token on its WebSocket API")
                user = _ws_call(ws, 1, "auth/current_user")
                _ws_call(ws, 2, "config/auth_provider/homeassistant/admin_change_password", user_id=user["id"],
                         password=password)
        except (OSError, TimeoutError, ValueError, WebSocketException) as e:
            raise StackError(f"could not set the Lab login on Home Assistant: {type(e).__name__}: {e}") from e
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump({"username": "vesper", "password": password}, f)
        return "vesper", password

    def write_http_store(self) -> None:
        """Home Assistant 2026.10 keeps its HTTP settings in .storage/http and reads `http:` from YAML only on a
        first boot, and the migration leaves them as a pending trial, which Home Assistant reverts after 5 minutes
        by restarting on 0.0.0.0:8123 (homeassistant/components/http/config.py). So VESPER writes the store before
        every start: this mode's settings as the stable config, and no trial."""
        store = Paths(Path(self.root)).ha_config / ".storage" / "http"
        store.parent.mkdir(parents=True, exist_ok=True)
        stable = {"server_host": ["127.0.0.1"], "server_port": self.port,
                  "cors_allowed_origins": ["https://cast.home-assistant.io"], "login_attempts_threshold": -1,
                  "ip_ban_enabled": True, "ssl_profile": "modern", "use_x_frame_options": not self.frameable,
                  "created_at": datetime.now(UTC).isoformat(), "error": None, "error_message": None}
        store.write_text(json.dumps({"version": 2, "minor_version": 2, "key": "http",
                                     "data": {"stable": stable, "pending": None, "yaml_migration_done": True}},
                                    indent=2))

    def read_automations(self) -> str:
        """automations.yaml as it is now: the home's, plus whatever was edited in Home Assistant's UI."""
        return (Paths(Path(self.root)).ha_config / "automations.yaml").read_text()

    def start(self) -> None:
        paths = Paths(Path(self.root))
        if not paths.hass.exists():
            raise StackError("Home Assistant is not installed; run `vesper setup ha` first")
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a Home Assistant")
        self.write_config()
        self.write_http_store()
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
            (Path(self.root) / "ha" / "lab-login.json").unlink(missing_ok=True)  # it was the old owner's
            tokens = c.post("/auth/token", data={"grant_type": "authorization_code", "code": r.json()["auth_code"],
                                                 "client_id": cid}).json()
            h = {"Authorization": f"Bearer {tokens['access_token']}"}
            for step, body in (("core_config", {}), ("analytics", {}),
                               ("integration", {"client_id": cid, "redirect_uri": f"{cid}?auth_callback=1"})):
                c.post(f"/api/onboarding/{step}", json=body, headers=h).raise_for_status()
        self._wait_until_saved(tokens["refresh_token"])
        auth.write_text(json.dumps({"refresh_token": tokens["refresh_token"], "client_id": cid}))
        auth.chmod(0o600)

    def _wait_until_saved(self, refresh_token: str) -> None:
        """Home Assistant writes its auth store with a delay, and a token it never wrote is refused after a
        restart while onboarding counts as done; so onboarding ends only once the token is on disk."""
        store = Paths(Path(self.root)).ha_config / ".storage" / "auth"
        deadline = time.monotonic() + self.persist_timeout
        while True:
            try:
                tokens = json.loads(store.read_text())["data"]["refresh_tokens"]
                if any(t.get("token") == refresh_token for t in tokens):
                    return
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if time.monotonic() > deadline:
                raise StackError(f"Home Assistant did not save the new refresh token to {store} within "
                                 f"{self.persist_timeout:.0f} s; stop it, delete {store.parent} and run again")
            time.sleep(0.2)

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
        """Point the MQTT integration at VESPER's broker, freshly on every run.

        The REST API does not show which broker an existing entry uses (entries are listed without their
        data), so every existing MQTT entry is removed and one is created. Removing an entry also removes
        the devices and entities it discovered, so no entity from an earlier run survives as a ghost.
        """
        self._remove_entries("mqtt")
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "mqtt"}).json()
        if flow.get("step_id") != "broker":
            raise StackError(f"unexpected MQTT config flow step: {flow}")
        # Home Assistant 2026.10's broker step nests TLS and transport under a required `other_settings`
        # section (mqtt/config_flow.py, OTHER_SETTINGS_SCHEMA): plain TCP, no client or CA certificate.
        result = self.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          json={"broker": "127.0.0.1", "port": broker_port,
                                "other_settings": {"set_client_cert": False, "set_ca_cert": "off",
                                                   "transport": "tcp"}}).json()
        if result.get("type") != "create_entry":
            raise StackError(f"the MQTT integration was not configured: {result}")

    def ensure_matter(self, url: str) -> None:
        """Point the Matter integration at VESPER's Matter Server, freshly on every run (as `ensure_mqtt`).

        Without a Supervisor, Home Assistant's Matter flow asks only for the server's WebSocket URL.
        """
        self._remove_entries("matter")
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "matter"}).json()
        if flow.get("step_id") != "manual":
            raise StackError(f"unexpected Matter config flow step: {flow}")
        result = self.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", json={"url": url}).json()
        if result.get("type") != "create_entry":
            raise StackError(f"the Matter integration was not configured: {result}")

    def ensure_tasmota(self, prefix: str = "tasmota/discovery", timeout: float = 30.0) -> None:
        """Home Assistant's Tasmota integration (one entry), reading the virtual Tasmota devices' discovery under
        `prefix`, freshly on every run (as `ensure_mqtt`)."""
        cure = "run `vesper setup ha` again: it installs the integration's requirements"
        self._remove_entries("tasmota")
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "tasmota"}).json()
        if flow.get("step_id") != "config":
            raise StackError(f"Home Assistant cannot start its Tasmota integration ({flow}); {cure}")
        result = self.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          json={"discovery_prefix": prefix}).json()
        if result.get("type") != "create_entry":
            raise StackError(f"the Tasmota integration was not configured: {result}")
        deadline = time.monotonic() + timeout  # created is not running: its setup may fail on a missing requirement
        while True:
            states = [e.get("state") for e in self.api("GET", "/api/config/config_entries/entry",
                                                         params={"domain": "tasmota"}).json()]
            if states == ["loaded"]:
                return
            if any(st not in ("not_loaded", "setup_in_progress") for st in states) or time.monotonic() > deadline:
                raise StackError(f"Home Assistant's Tasmota integration did not set up ({', '.join(map(str, states))};"
                                 f" its log says why); {cure}")
            time.sleep(0.5)

    def forget(self, domain: str) -> None:
        """No entry of `domain` survives from an earlier run: its devices and entities go with it."""
        self._remove_entries(domain)

    def matter_entities(self) -> dict[str, list[str]]:
        """Home Assistant's Matter entities, grouped by their device's serial number (the twin's node serial)."""
        r = self.api("POST", "/api/template", json={"template": _MATTER_ENTITIES})
        if r.status_code != 200:
            raise StackError(f"listing the Matter entities failed ({r.status_code}): {r.text[:200]}")
        out: dict[str, list[str]] = {}
        for entity_id, serial in json.loads(r.text):
            if serial:
                out.setdefault(serial, []).append(entity_id)
        return out

    def _remove_entries(self, domain: str) -> None:
        """Remove every config entry of `domain`, with the devices and entities it created, so none survives
        from an earlier run as a ghost."""
        entries = self.api("GET", "/api/config/config_entries/entry", params={"domain": domain}).json()
        for e in entries:
            if e.get("domain", domain) == domain:
                r = self.api("DELETE", f"/api/config/config_entries/entry/{e['entry_id']}")
                if r.status_code >= 400:
                    raise StackError(f"could not remove the old {domain} entry ({r.status_code}): {r.text[:200]}")

    def state(self, entity_id: str) -> dict | None:
        """The entity's state, None if Home Assistant has no such entity; any other answer is an error."""
        r = self.api("GET", f"/api/states/{entity_id}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise StackError(f"reading {entity_id} failed ({r.status_code}): {r.text[:200]}")
        return r.json()

    def call(self, domain: str, service: str, data: dict) -> None:
        r = self.api("POST", f"/api/services/{domain}/{service}", json=data)
        if r.status_code >= 400:
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
