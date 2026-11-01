"""Setting VESPER up on a machine (Lab spec §7, plan W5): where things are, what is missing, and the commands that fetch
or build them.

- The user's configuration (`$XDG_CONFIG_HOME/vesper/config.json`, else `~/.config/vesper/config.json`) remembers what
  setup found or made: the Habitat environment's Python, the HSSD folder, the humanoids. A command-line flag wins,
  then the environment variable, then the configuration, then the default.
- `vesper doctor` checks every prerequisite and names the commands that need it and how to get it.
- `vesper setup habitat` makes the Habitat environment (conda or micromamba, habitat-sim 0.3.3 with Bullet, Python
  3.9). `vesper setup scenes ID...` downloads exactly the HSSD files a scene needs, through the user's own Hugging
  Face login (`hf auth login`, after accepting HSSD's terms); VESPER never reads the token.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HABITAT_ENV = "vesper-habitat"
HABITAT_PACKAGES = ["python=3.9", "habitat-sim=0.3.3", "withbullet", "numpy<2"]
HABITAT_CHANNELS = ["-c", "aihabitat", "-c", "conda-forge"]
HSSD_REPO = "hssd/hssd-hab"
HUMANOIDS_REPO = "ai-habitat/habitat_humanoids"
FETCH_ROUNDS = 6  # a scene's files reveal more files (instance -> configs -> assets, URDFs -> meshes)
NODE_MIN = (22, 13)


class SetupError(RuntimeError):
    pass


# -- configuration ---------------------------------------------------------------------------------------------------
def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "vesper" / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}


_CONFIG_LOCK = threading.Lock()


def save_config(**values) -> Path:
    """Merge `values` into the configuration: under a lock (this process) and a lock file (other processes), through
    a temporary file of its own, so two writers never lose each other's keys."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CONFIG_LOCK, open(path.with_suffix(".lock"), "a") as lock:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:  # Windows: the in-process lock only
            pass
        data = {**load_config(), **{k: str(v) for k, v in values.items() if v is not None}}
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    return path


def configured(key: str) -> str | None:
    return load_config().get(key)


# -- doctor ------------------------------------------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    needed_by: str
    fix: str = ""
    required: bool = False  # VESPER's core (catalog, twins, scenarios on the logical clock) needs it


def _version(cmd: list[str], run) -> str | None:
    try:
        r = run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 and (r.stdout or r.stderr) else None


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def doctor(root: Path, catalog: Path, *, which: Callable = shutil.which, run: Callable = subprocess.run,
           ports: tuple[int, ...] = (18830, 18123)) -> list[Check]:
    from vesper.lab.scenes import DEFAULT_HSSD, HSSD_ENV, habitat_python
    out = [Check("python", sys.version_info >= (3, 11), sys.version.split()[0], "everything", "Python 3.11 or later",
                 required=True)]
    stale = None
    try:
        from vesper.catalog.fingerprint import staleness
        stale = staleness(catalog) if (catalog / "build_report.json").is_file() else "not built"
    except Exception as e:  # noqa: BLE001 - reported, never raised
        stale = str(e)
    out.append(Check("catalog", not stale, stale or f"{catalog} is current", "every command that runs twins",
                     "vesper catalog sync && vesper catalog build (or vesper catalog fetch)", required=True))
    mosq = which("mosquitto") or next((p for p in ("/opt/homebrew/sbin/mosquitto", "/usr/local/sbin/mosquitto",
                                                   "/usr/sbin/mosquitto") if Path(p).is_file()), None)
    out.append(Check("mosquitto", bool(mosq), mosq or "not found", "live runs (Home Assistant in the loop)",
                     "brew install mosquitto, or apt install mosquitto"))
    ha = root / "ha" / "venv" / "bin" / "hass"
    out.append(Check("home assistant", ha.is_file(), str(ha) if ha.is_file() else "not installed",
                     "live runs, vesper validate acceptance|matter|tasmota", "vesper setup ha"))
    node = _version(["node", "--version"], run) if which("node") else None
    node_ok = bool(node) and tuple(int(x) for x in node.lstrip("v").split(".")[:2]) >= NODE_MIN
    out.append(Check("node", node_ok, node or "not found", "the Matter tier", f"Node.js {'.'.join(map(str, NODE_MIN))}"
                     " or later"))
    matter = root / "matter" / "host" / "host.mjs"
    out.append(Check("matter host", matter.is_file(), str(matter) if matter.is_file() else "not installed",
                     "Matter devices on Home Assistant", "vesper setup matter"))
    python = habitat_python(configured("habitat_python"))
    out.append(Check("habitat", python.is_file(), str(python) if python.is_file() else f"no Python at {python}",
                     "homes from HSSD scenes (vesper lab new --scene), the 3D view's people",
                     "vesper setup habitat"))
    hssd = Path(os.environ.get(HSSD_ENV) or configured("hssd") or DEFAULT_HSSD)
    n = len(list((hssd / "scenes-articulated").glob("*.scene_instance.json"))) if hssd.is_dir() else 0
    out.append(Check("hssd", n > 0, f"{n} scene(s) in {hssd}" if n else f"no scenes in {hssd}",
                     "homes from HSSD scenes", "hf auth login (after accepting HSSD's terms); vesper setup scenes ID"))
    browser = next((b for b in ("google-chrome", "chromium", "chrome-headless-shell") if which(b)), None)
    mac_chrome = Path("/Applications/Google Chrome.app")
    out.append(Check("browser", bool(browser) or mac_chrome.exists(), browser or (str(mac_chrome) if
                     mac_chrome.exists() else "not found"), "vesper lab look (automatic visual checks)",
                     "Google Chrome or Chromium"))
    for port in ports:
        out.append(Check(f"port {port}", _port_free(port), "free" if _port_free(port) else "in use",
                         "live runs", "stop what holds it (VESPER never shares a broker or a hub)"))
    return out


# -- habitat -----------------------------------------------------------------------------------------------------------
CONDA_HOMES = (Path.home() / "miniconda3", Path.home() / "miniforge3", Path.home() / "anaconda3")


def conda_tool(which: Callable = shutil.which, homes: tuple = CONDA_HOMES) -> str | None:
    """micromamba, mamba or conda on the PATH, else a conda in one of the usual installs (`homes`)."""
    for tool in ("micromamba", "mamba", "conda"):
        if which(tool):
            return which(tool)
    for base in homes:
        if (Path(base) / "bin" / "conda").is_file():
            return str(Path(base) / "bin" / "conda")
    return None


def setup_habitat(*, which: Callable = shutil.which, run: Callable = subprocess.run, name: str = HABITAT_ENV,
                  dry_run: bool = False, homes: tuple = CONDA_HOMES) -> tuple[list[str], Path]:
    """Create the Habitat environment (once) and record its Python. Returns (the command, the Python)."""
    tool = conda_tool(which, homes)
    if tool is None:
        raise SetupError("no conda, mamba or micromamba: install Miniforge (https://conda-forge.org/download/), "
                         "then run vesper setup habitat again")
    cmd = [tool, "create", "-y", "-n", name, *HABITAT_CHANNELS, *HABITAT_PACKAGES]
    if dry_run:
        return cmd, Path()
    prefix = _env_prefix(tool, name, run)
    if prefix is None:
        r = run(cmd, timeout=3600)
        if r.returncode != 0:
            raise SetupError(f"{' '.join(cmd)} failed (exit {r.returncode})")
        prefix = _env_prefix(tool, name, run)
    if prefix is None:
        raise SetupError(f"the environment {name} was not created")
    python = prefix / "bin" / "python"
    r = run([str(python), "-c", "import habitat_sim; print(habitat_sim.__version__)"], capture_output=True, text=True,
            timeout=120)
    if r.returncode != 0:
        raise SetupError(f"{python} cannot import habitat_sim: {(r.stderr or '').strip()[-300:]}")
    save_config(habitat_python=python)
    return cmd, python


def _env_prefix(tool: str, name: str, run) -> Path | None:
    r = run([tool, "env", "list", "--json"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    try:
        envs = json.loads(r.stdout).get("envs", [])
    except ValueError:
        return None
    return next((Path(e) for e in envs if Path(e).name == name), None)


# -- scenes ------------------------------------------------------------------------------------------------------------
def scene_files(hssd: Path, scene: str) -> list[str]:
    """The files of a scene not here yet: its instance and region annotations first, then what they name."""
    from vesper.lab.scenes import list_scenes
    first = [f"scenes-articulated/{scene}.scene_instance.json", f"semantics/scenes/{scene}.semantic_config.json"]
    missing = [f for f in first if not (hssd / f).is_file()]
    if missing:
        return missing
    found = next((s for s in list_scenes(hssd) if s.id == scene), None)
    if found is None:
        raise SetupError(f"{scene}: its region annotations name no ground-floor rooms, so VESPER cannot make a home "
                         "of it (vesper lab scenes lists the scenes it can)")
    return [m for m in found.missing if not m.endswith("(unreadable)")]


def setup_scenes(hssd: Path, scenes: list[str], *, which: Callable = shutil.which, run: Callable = subprocess.run,
                 say: Callable = print) -> dict[str, int]:
    """Download each scene's files with the user's `hf` CLI, round after round until none is missing."""
    hf = which("hf") or which("huggingface-cli")
    if hf is None:
        raise SetupError("the Hugging Face CLI is missing: pip install -U huggingface_hub, then hf auth login "
                         "(after accepting HSSD's terms at https://huggingface.co/datasets/hssd/hssd-hab)")
    hssd.mkdir(parents=True, exist_ok=True)
    got: dict[str, int] = {}
    for scene in scenes:
        got[scene] = 0
        for _ in range(FETCH_ROUNDS):
            files = scene_files(hssd, scene)
            if not files:
                break
            say(f"{scene}: {len(files)} file(s)")
            for i in range(0, len(files), 200):  # the command line has a length limit
                cmd = [hf, "download", HSSD_REPO, "--repo-type", "dataset", "--local-dir", str(hssd),
                       *files[i:i + 200]]
                r = run(cmd, timeout=3600)
                if r.returncode != 0:
                    raise SetupError(f"hf download failed for {scene} (exit {r.returncode}): is the login done and "
                                     "HSSD's terms accepted?")
            got[scene] += len(files)
        else:
            left = scene_files(hssd, scene)
            if left:
                raise SetupError(f"{scene}: still missing {len(left)} file(s) after {FETCH_ROUNDS} rounds, e.g. "
                                 f"{left[0]}")
    save_config(hssd=hssd.resolve())
    return got
