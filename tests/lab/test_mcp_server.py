"""`vesper mcp` (security-lab spec §5, plan W4b): the workbench's tools and the reproduce_paper prompt through a real
MCP client session, and the client config the command writes."""
import asyncio
import json
import subprocess
import sys

import pytest
from mcp import Client

from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.mcp_server import build_server
from vesper.lab.workbench import Workbench

TOOLS = {"capabilities", "homes", "home_describe", "catalog_search", "scenario_draft", "scenario_edit", "cite", "gap",
         "scenario_validate", "scenario_save", "scenario_run", "run_status", "run_report"}


@pytest.fixture
def server(tmp_path):
    (tmp_path / "homes" / "house").mkdir(parents=True)
    write_home(tmp_path / "homes" / "house", LOCKED_HOME)
    return build_server(Workbench([tmp_path / "homes"], catalog(), runs_dir=tmp_path / "runs"))


def session(server, fn):
    async def go():
        async with Client(server) as client:
            return await fn(client)
    return asyncio.run(go())


def test_every_tool_and_the_prompt_are_offered(server):
    async def fn(c):
        tools = {t.name for t in (await c.list_tools()).tools}
        prompts = {p.name for p in (await c.list_prompts()).prompts}
        return tools, prompts
    tools, prompts = session(server, fn)
    assert tools == TOOLS and prompts == {"reproduce_paper"}


def test_no_tool_takes_a_host_a_port_a_command_or_code(server):
    async def fn(c):
        return [(t.name, set((t.input_schema or {}).get("properties", {}))) for t in (await c.list_tools()).tools]
    for name, params in session(server, fn):
        assert not params & {"host", "port", "command", "cmd", "shell", "code", "script", "url", "path"}, name


def test_a_tool_error_reaches_the_model_as_text_naming_the_choice(server):
    async def fn(c):
        d = json.loads((await c.call_tool("scenario_draft", {"home": "homes/house", "title": "t"})).content[0].text)
        return await c.call_tool("scenario_edit", {"draft": d["draft"], "section": "atack", "action": "add",
                                                   "value": {}})
    r = session(server, fn)
    assert r.is_error and "attack" in r.content[0].text


def test_the_prompt_walks_the_model_through_the_paper(server):
    async def fn(c):
        return (await c.get_prompt("reproduce_paper")).messages[0].content.text
    text = session(server, fn)
    for step in ("Threat model", "cite", "gap", "scenario_run", "run_report", "Never fake"):
        assert step in text


def test_print_config_gives_absolute_paths(tmp_path):
    out = subprocess.run([sys.executable, "-m", "vesper.cli", "mcp", "--print-config", "--homes", str(tmp_path),
                          "--catalog", "catalog/vds"], capture_output=True, text=True, check=True).stdout
    entry = json.loads(out)["mcpServers"]["vesper"]
    assert entry["args"][0] == "mcp" and str(tmp_path.resolve()) in entry["args"]
    assert all(a.startswith("/") for a in entry["args"][2::2])


def test_registering_with_claude_desktop_keeps_a_backup_and_other_servers(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    cmd = [sys.executable, "-m", "vesper.cli", "setup", "claude-desktop", "--config", str(config), "--homes",
           str(tmp_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    saved = json.loads(config.read_text())
    assert set(saved["mcpServers"]) == {"other", "vesper"} and saved["theme"] == "dark"
    assert json.loads((tmp_path / "claude_desktop_config.json.vesper-backup").read_text())["mcpServers"] == {
        "other": {"command": "x"}}
    other = json.loads(config.read_text())
    other["mcpServers"]["vesper"] = {"command": "elsewhere"}
    config.write_text(json.dumps(other))
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 1 and "--force" in r.stderr
    assert json.loads(config.read_text())["mcpServers"]["vesper"] == {"command": "elsewhere"}
