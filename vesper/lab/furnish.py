"""Furnishing a composed home in Blender with Claude, from the lobby (PI 2026-11-01; home-generator spec §5).

The steps, each recorded: `shell` writes the home's Blender build script and brief (`vesper lab dress`); `blender`
makes sure Blender is open with its MCP server listening (mcp-for-blender's add-on starts it with Blender), opening
Blender if it is not; `claude` runs Claude Code headless (`claude -p`) with only Blender's MCP tools and the home's
folder, and a spending cap, to build the shell, furnish each room and export `scene.glb`; `model` checks that the
model came back whole; `renav` takes the walkable floor again from the furniture (`vesper lab renav`). The map stays
what VESPER walks, reaches and senses with: the model is what is drawn, and now what people walk round.

Claude's whole run is kept in `dress/claude.jsonl` beside the home. An HSSD house is furnished already: refused.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

BLENDER_PORT = 9876  # mcp-for-blender's default
MCP_PACKAGE = "mcp-for-blender==2.2.6"  # the version VESPER's end-to-end run used (RKA jrn_01M69VKZTYJKGN9BR53QYVWAQ1)
TOOLS = ("mcp__blender", "Read", "Write", "Edit", "Glob", "Grep")  # Blender, and files: no shell, no web
BUDGET_USD = 25.0  # the end-to-end run cost USD 16.44 on Opus
STEPS = ("shell", "blender", "claude", "model", "renav")
LINES = 40  # the progress lines the lobby shows


class FurnishError(RuntimeError):
    """A home that cannot be furnished this way, or a run already going."""


def blender_listening(port: int = BLENDER_PORT) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def blender_app() -> str | None:
    from vesper.setup_tools import configured
    for p in (configured("blender"), "/Applications/Blender.app/Contents/MacOS/Blender", shutil.which("blender")):
        if p and Path(p).is_file():
            return str(p)
    return None


def addon_file() -> Path | None:
    """mcp-for-blender's add-on, as installed into Blender's user scripts (the newest Blender's)."""
    from vesper.setup_tools import configured
    if configured("blender_mcp_addon"):
        return Path(configured("blender_mcp_addon"))
    roots = [Path.home() / "Library" / "Application Support" / "Blender", Path.home() / ".config" / "blender",
             Path.home() / "AppData" / "Roaming" / "Blender Foundation" / "Blender"]
    found = [f for r in roots if r.is_dir() for f in r.glob("*/scripts/addons/addon.py")
             if "MCP for Blender" in f.read_text(errors="replace")[:4000]]
    return max(found, key=lambda f: f.parent.parent.parent.name) if found else None


def blender_startup(addon: Path, port: int) -> str:
    """What a run's own Blender runs at start (`--python-expr`): an empty scene, and mcp-for-blender's add-on loaded
    from its file with its MCP server on `port` (the add-on starts it a moment after it registers)."""
    return f"""import bpy, importlib.util, sys
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
spec = importlib.util.spec_from_file_location("vesper_blendermcp", {str(addon)!r})
m = importlib.util.module_from_spec(spec)
sys.modules["vesper_blendermcp"] = m
spec.loader.exec_module(m)
m.register()
bpy.context.scene.blendermcp_port = {port}
bpy.context.scene.blendermcp_auto_start_server = True
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch_blender(port: int) -> None:
    """A Blender of the run's own (the user's open Blender, and its file, are left alone), with its MCP server on
    `port`. It stays open when the run ends, the home in it."""
    app, addon = blender_app(), addon_file()
    if app is None:
        raise FurnishError("Blender is not installed (https://www.blender.org, 4.2 or later); or set its path with "
                           "`blender` in ~/.config/vesper/config.json")
    if addon is None:
        raise FurnishError("Blender's MCP add-on (mcp-for-blender) is not installed: install its addon.py in Blender "
                           "(Edit, Preferences, Add-ons, Install from disk), or set `blender_mcp_addon` in "
                           "~/.config/vesper/config.json")
    subprocess.Popen([app, "--factory-startup", "--python-expr", blender_startup(addon, port)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _renav(home_yaml: Path, catalog_dir: Path) -> tuple[bool, str]:
    code = "import sys; from vesper.cli import main; sys.exit(main(sys.argv[1:]))"
    r = subprocess.run([sys.executable, "-c", code, "lab", "renav", str(home_yaml), "--catalog", str(catalog_dir)],
                       capture_output=True, text=True)
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    return r.returncode == 0, (lines[-1] if lines else "")


def mcp_config(port: int = BLENDER_PORT) -> dict:
    server = {"type": "stdio", "command": "uvx", "args": ["--from", MCP_PACKAGE, "mcp-for-blender"],
              "env": {"DISABLE_TELEMETRY": "true", "BLENDER_PORT": str(port)}}
    return {"mcpServers": {"blender": server}}


def claude_args(prompt: str, mcp_file: Path, home_dir: Path, *, model: str | None, budget: float) -> list[str]:
    args = ["claude", "-p", prompt, "--mcp-config", str(mcp_file), "--strict-mcp-config",
            "--allowedTools", ",".join(TOOLS), "--add-dir", str(home_dir), "--permission-mode", "acceptEdits",
            "--output-format", "stream-json", "--verbose", "--max-budget-usd", str(budget)]
    return [*args, "--model", model] if model else args


def furnish_prompt(home_dir: Path, script: Path, brief: Path) -> str:
    glb = home_dir / "scene.glb"
    return f"""You are furnishing a smart home for VESPER, a smart-home research lab. Blender is open and connected
to you through the Blender MCP tools; the home's files are in {home_dir}.

1. Read the brief: {brief}
2. Build the home's shell in Blender by running the Python file {script} with the Blender MCP tool that executes
   Python code (exec its text with sys.argv = ["blender"]). It makes the collection "VESPER home": each room's
   floor in its own collection, the walls with door openings, a lintel over each doorway, and an empty where each
   device is.
3. Furnish every room as the brief says, as what it is (a living room, a kitchen, a bedroom...), each piece in its
   room's collection. Keep every doorway clear for a metre, corridors and halls open, and the wall in front of each
   `switch:` empty clear. Do not move, resize or delete the walls, floors, lintels or device empties; add no doors
   and no ceiling. Use Blender's primitives, or CC0 assets only, and list each asset with its source and licence in
   {home_dir / "dress" / "SOURCES.md"}. If you write your furnishing as a script, keep it as
   {home_dir / "dress" / "furnish.py"}.
4. Export: run {script} again with sys.argv = ["blender", "--", "--export"]; it writes {glb}. Check the file exists.
5. Save the Blender file as {home_dir / "dress" / "home.blend"}. This Blender was opened for this home alone.

Change no other file of the home (not home.yaml, not map.json): VESPER takes the walkable floor from your model
afterwards. Finish with one short paragraph: what each room got."""


class Furnisher:
    """One furnishing run of the home at `home_yaml`; `run()` does it (`start()` in a thread), `status()` says how
    far it is."""

    def __init__(self, home_yaml: Path, catalog: dict, catalog_dir: Path, *, model: str | None = None,
                 budget: float = BUDGET_USD, port: int | None = None, listening=blender_listening,
                 launch_blender=_launch_blender, popen=subprocess.Popen, renav=_renav, wait_s: float = 120.0,
                 sleep=time.sleep):
        self.home_yaml, self.home_dir = Path(home_yaml), Path(home_yaml).parent
        if (self.home_dir / "render.json").is_file():
            raise FurnishError("this home is an HSSD house: it comes furnished; Blender furnishes a home VESPER "
                               "generated from a number or list of rooms")
        self.catalog, self.catalog_dir = catalog, Path(catalog_dir)
        self.model, self.budget, self.port = model, budget, port
        self._listening, self._launch_blender, self._popen, self._renav = listening, launch_blender, popen, renav
        self.wait_s, self._sleep = wait_s, sleep
        self.claude = None
        self._stopped = False
        self._lock = threading.Lock()
        self._state = {"state": "ready", "home": str(self.home_yaml), "steps": [], "lines": deque(maxlen=LINES),
                       "error": None, "cost_usd": None, "turns": None, "started": None, "ended": None,
                       "model": model, "budget_usd": budget}

    # -- the record ---------------------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            s = dict(self._state)
            s["lines"] = list(s["lines"])
            s["steps"] = [dict(x) for x in s["steps"]]
            return s

    def _step(self, name: str, ok: bool | None, detail: str = "") -> None:
        with self._lock:
            steps = self._state["steps"]
            if steps and steps[-1]["name"] == name:
                steps[-1].update(ok=ok, detail=detail)
            else:
                steps.append({"name": name, "ok": ok, "detail": detail})

    def _line(self, text: str) -> None:
        with self._lock:
            self._state["lines"].append(text)

    def _end(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self._state.update(state=state, error=error, ended=datetime.now(UTC).isoformat(timespec="seconds"))

    # -- the run --------------------------------------------------------------------------------------
    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="vesper-furnish", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stopped = True
        if self.claude is not None and self.claude.poll() is None:
            self.claude.terminate()

    def run(self) -> None:
        with self._lock:
            self._state.update(state="running", started=datetime.now(UTC).isoformat(timespec="seconds"))
        try:
            script, brief = self._shell()
            self._blender()
            self._claude(script, brief)
            if self._stopped:
                self._end("stopped")
                return
            self._model()
            self._renav_step()
        except FurnishError as e:
            self._end("stopped" if self._stopped else "failed", str(e))
            return
        except Exception as e:  # noqa: BLE001 - recorded for the lobby, never raised into its thread
            self._end("failed", f"{type(e).__name__}: {e}")
            return
        self._end("done")

    def _shell(self) -> tuple[Path, Path]:
        from vesper.lab.dress import dress
        from vesper.lab.home import load_lab_home
        self._step("shell", None, "writing the shell's build script and brief")
        script, brief = dress(load_lab_home(self.home_yaml, self.catalog))
        self._step("shell", True, str(script.parent))
        return script, brief

    def _blender(self) -> None:
        self.port = self.port or free_port()
        self._step("blender", None, f"opening a Blender of its own, its MCP server on port {self.port}")
        if not self._listening(self.port):
            self._line("Opening Blender…")
            self._launch_blender(self.port)
            for _ in range(int(self.wait_s)):  # a second each: Blender and its add-on start in 10 to 30 s
                if self._listening(self.port) or self._stopped:
                    break
                self._sleep(1.0)
            if not self._listening(self.port):
                self._step("blender", False)
                raise FurnishError(f"Blender opened, but its MCP server did not listen on port {self.port} within "
                                   f"{int(self.wait_s)} s: check that the mcp-for-blender add-on works in Blender "
                                   "(its MCP tab, in the sidebar), then try again")
        self._step("blender", True, f"Blender is open, its MCP server on port {self.port}; it stays open after")

    def _claude(self, script: Path, brief: Path) -> None:
        dress_dir = script.parent
        (dress_dir / "mcp.json").write_text(json.dumps(mcp_config(self.port), indent=1))
        glb = self.home_dir / "scene.glb"
        if glb.exists():  # an older model is set aside: the check below must see the one this run exported
            glb.replace(dress_dir / f"scene.before-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.glb")
        self._step("claude", None, "Claude is building and furnishing the home in Blender")
        args = claude_args(furnish_prompt(self.home_dir, script, brief), dress_dir / "mcp.json", self.home_dir,
                           model=self.model, budget=self.budget)
        log = (dress_dir / "claude.jsonl").open("w")
        try:
            self.claude = self._popen(args, cwd=self.home_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True)
            result = None
            for raw in self.claude.stdout:
                if not raw.strip():
                    continue
                log.write(raw if raw.endswith("\n") else raw + "\n")
                result = self._read(raw) or result
            code = self.claude.wait()
        finally:
            log.close()
        if self._stopped:
            self._step("claude", False, "stopped")
            return
        if result is None or result.get("is_error") or code not in (0, None):
            why = (result or {}).get("result") or (result or {}).get("subtype") or f"claude exited with {code}"
            self._step("claude", False, str(why)[:300])
            raise FurnishError(f"Claude did not finish furnishing: {str(why)[:300]}")
        self._step("claude", True, f"{result.get('num_turns')} turns, USD {result.get('total_cost_usd')}")

    def _read(self, raw: str) -> dict | None:
        """One stream-json line: its text and Blender calls become progress lines; the result is returned."""
        try:
            msg = json.loads(raw)
        except ValueError:
            self._line(raw.strip()[:200])
            return None
        if msg.get("type") == "assistant":
            for part in (msg.get("message") or {}).get("content") or []:
                if part.get("type") == "text" and part.get("text", "").strip():
                    self._line(part["text"].strip()[:400])
                elif part.get("type") == "tool_use":
                    name = str(part.get("name", ""))
                    self._line(f"Blender: {name.split('__')[-1]}" if name.startswith("mcp__blender")
                               else f"{name}: {json.dumps(part.get('input') or {})[:120]}")
        elif msg.get("type") == "result":
            with self._lock:
                self._state.update(cost_usd=msg.get("total_cost_usd"), turns=msg.get("num_turns"))
            if msg.get("result"):
                self._line(str(msg["result"]).strip()[:600])
            return msg
        return None

    def _model(self) -> None:
        from vesper.lab.dress import _whole
        glb = self.home_dir / "scene.glb"
        if not _whole(glb):
            self._step("model", False)
            raise FurnishError(f"Claude finished, but {glb} is {'not whole' if glb.exists() else 'not there'}: "
                               "nothing was exported (its run is in dress/claude.jsonl)")
        self._step("model", True, f"scene.glb, {glb.stat().st_size / 1e6:.1f} MB")

    def _renav_step(self) -> None:
        self._step("renav", None, "taking the walkable floor from the furniture")
        ok, last = self._renav(self.home_yaml, self.catalog_dir)
        self._step("renav", ok, last)
        if not ok:
            raise FurnishError(f"the model is kept, but the walkable floor could not be taken from it: {last}")
