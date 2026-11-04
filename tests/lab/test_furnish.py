"""Furnishing a composed home in Blender with Claude, from the lobby (PI 2026-11-01): VESPER writes the shell's build
script and brief (`dress`), makes sure Blender is open with its MCP server, runs Claude headless with only Blender's
MCP tools and the home's folder, checks that `scene.glb` came back whole, and takes the walkable floor again from it
(`renav`). Blender, Claude and renav are fakes here: the run's steps, its record, and each way it can fail."""
import json
import threading

import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.furnish import FurnishError, Furnisher, claude_args, mcp_config

GLB = b"glTF" + (2).to_bytes(4, "little") + (20).to_bytes(4, "little") + b"\0" * 8  # a whole 20-byte GLB


class FakeClaude:
    """`claude -p`: writes its stream-json lines, and (unless told not to) the model it exported."""

    def __init__(self, cmd, cwd=None, stdout=None, stderr=None, text=None, **kw):
        self.cmd, self.cwd = cmd, cwd
        self.lines = [json.dumps({"type": "assistant", "message": {"content": [
                          {"type": "text", "text": "Building the shell, then the living room."},
                          {"type": "tool_use", "name": "mcp__blender__execute_blender_code", "input": {}}]}}),
                      json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 12,
                                  "total_cost_usd": 3.25, "result": "Furnished 4 rooms."})]
        if FakeClaude.export:
            (cwd / "scene.glb").write_bytes(GLB)
        self.stdout = iter(line + "\n" for line in self.lines)
        self.returncode = None

    export = True

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15


def furnisher(tmp_path, *, listening=None, renav=None, popen=FakeClaude, **kw):
    path = write_home(tmp_path, LOCKED_HOME)
    up = {"blender": False} if listening is None else listening
    launched = []
    f = Furnisher(path, catalog(), tmp_path / "catalog", listening=lambda port: up["blender"], port=9911,
                  launch_blender=lambda port: launched.append(port) or up.__setitem__("blender", True), popen=popen,
                  renav=renav or (lambda home, catalog_dir: (True, "map re-taken")), sleep=lambda s: None,
                  layout=kw.pop("layout", lambda home, catalog: (True, "every rule holds")), **kw)
    f.launched = launched
    return f, path


def test_claude_gets_only_blender_s_tools_and_the_home_s_folder_with_a_spending_cap(tmp_path):
    args = claude_args("do it", tmp_path / "mcp.json", tmp_path, model="sonnet", budget=12.5)
    assert args[:3] == ["claude", "-p", "do it"]
    assert "--strict-mcp-config" in args and args[args.index("--mcp-config") + 1] == str(tmp_path / "mcp.json")
    tools = args[args.index("--allowedTools") + 1].split(",")
    assert tools == ["mcp__blender", "Read", "Write", "Edit", "Glob", "Grep"]  # no Bash, no web
    assert args[args.index("--add-dir") + 1] == str(tmp_path)
    assert args[args.index("--max-budget-usd") + 1] == "12.5" and args[args.index("--model") + 1] == "sonnet"
    assert "--model" not in claude_args("x", tmp_path, tmp_path, model=None, budget=5)
    server = mcp_config(9876)["mcpServers"]["blender"]
    assert server["args"][-2:] == ["mcp-for-blender==2.2.6", "mcp-for-blender"]
    assert server["env"]["BLENDER_PORT"] == "9876"


def test_a_run_writes_the_shell_opens_its_own_blender_runs_claude_checks_the_model_and_re_maps_the_floor(tmp_path):
    f, path = furnisher(tmp_path)
    f.run()
    s = f.status()
    assert f.launched == [9911]  # a Blender of its own, its MCP server on the run's port: the user's is left alone
    assert json.loads((tmp_path / "dress" / "mcp.json").read_text())["mcpServers"]["blender"]["env"][
        "BLENDER_PORT"] == "9911"
    assert s["state"] == "done", s
    assert [st["name"] for st in s["steps"]] == ["shell", "blender", "claude", "model", "renav", "layout"]
    assert all(st["ok"] for st in s["steps"])
    assert (tmp_path / "dress" / "build_home.py").is_file() and (tmp_path / "dress" / "BRIEF.md").is_file()
    assert s["cost_usd"] == 3.25 and s["turns"] == 12
    assert "Building the shell, then the living room." in s["lines"]
    assert "Blender: execute_blender_code" in s["lines"]
    assert (tmp_path / "dress" / "claude.jsonl").is_file()  # Claude's whole run, kept beside the home
    prompt = f.claude.cmd[2]
    assert str(tmp_path / "dress" / "BRIEF.md") in prompt and str(tmp_path / "scene.glb") in prompt
    assert f.claude.cwd == tmp_path


def test_claude_finishing_without_a_whole_model_fails_the_run_and_the_floor_is_not_re_mapped(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeClaude, "export", False)
    renav_called = []
    f, _ = furnisher(tmp_path, renav=lambda home, cat: renav_called.append(1) or (True, ""))
    f.run()
    s = f.status()
    assert s["state"] == "failed" and "scene.glb" in s["error"] and not renav_called


def test_blender_that_never_opens_its_mcp_server_fails_the_run_with_what_to_do(tmp_path):
    f, _ = furnisher(tmp_path)
    f._launch_blender = lambda port: None  # Blender starts, but its MCP server never listens
    f.run()
    s = f.status()
    assert s["state"] == "failed" and "MCP" in s["error"] and "Blender" in s["error"]


def test_a_failed_renav_says_why_and_keeps_the_model(tmp_path):
    f, _ = furnisher(tmp_path, renav=lambda home, cat: (False, "the floor fell apart at the hall doorway"))
    f.run()
    s = f.status()
    assert s["state"] == "failed" and "hall doorway" in s["error"] and (tmp_path / "scene.glb").is_file()


def test_an_hssd_house_is_furnished_already_and_is_refused(tmp_path):
    path = write_home(tmp_path, LOCKED_HOME)
    (tmp_path / "render.json").write_text("{}")
    with pytest.raises(FurnishError, match="HSSD"):
        Furnisher(path, catalog(), tmp_path / "catalog")


def test_stopping_a_run_ends_claude_and_says_stopped(tmp_path):
    started = threading.Event()

    class Slow(FakeClaude):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.stdout = self._slow()

        def _slow(self):
            started.set()
            while self.returncode is None:
                yield ""
                threading.Event().wait(0.01)

    f, _ = furnisher(tmp_path, popen=Slow)
    t = f.start()
    assert started.wait(5)
    f.stop()
    t.join(5)
    assert f.status()["state"] == "stopped" and f.claude.returncode == -15


def test_its_blender_starts_with_an_empty_scene_and_the_add_on_listening_on_the_run_s_port(tmp_path):
    from vesper.lab.furnish import blender_startup
    addon = tmp_path / "addon.py"
    code = blender_startup(addon, 9911)
    assert str(addon) in code and "blendermcp_port = 9911" in code and "objects.remove" in code
    compile(code, "startup", "exec")  # Blender runs it with --python-expr


def test_a_model_that_breaks_a_layout_rule_is_flagged_and_kept(tmp_path):
    f, _ = furnisher(tmp_path, layout=lambda home, catalog: (False, "1 error(s): doorway d0: bed 0.3 m from it"))
    f.run()
    s = f.status()
    assert s["state"] == "done" and (tmp_path / "scene.glb").is_file()
    layout = s["steps"][-1]
    assert layout["name"] == "layout" and layout["ok"] is False and "bed" in layout["detail"]
