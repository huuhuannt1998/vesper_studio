"""`vesper mcp`: VESPER's paper-to-scenario MCP server (security-lab spec §5, plan W4b).

A stdio server on the official MCP Python SDK. Claude Desktop, LM Studio or any MCP client starts it; the model reads a
security paper and, with these tools, sets the paper's attack and defense up in a VESPER home, runs it and reports
claim by claim. Each tool is a thin call into `vesper.lab.workbench`; none takes a host, a port, a command or code.
"""
from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from vesper.lab.workbench import Workbench, WorkbenchError

INSTRUCTIONS = """VESPER sets up a smart-home security paper's attack and defense in a virtual home of real products
(twins of Zigbee, Matter and Wi-Fi devices on an unmodified Home Assistant), runs it, and reports claim by claim
whether the paper's result reproduces. Start with the reproduce_paper prompt, or call capabilities, then homes."""

REPRODUCE_PAPER = """You are reproducing a smart-home security paper in VESPER. The researcher has given you the paper.
Work in this order, with VESPER's tools, and keep the researcher informed:

1. Threat model. Read the paper's threat model and attacker. Call `capabilities` to see what VESPER can express.
   Quote the passages you rely on with `cite` (keys such as threat, attack, result, defense) — quote exactly, never
   paraphrase inside a quote.
2. Home and victim setup. Call `homes`, then `home_describe` on the home that fits (or the closest one). Start a
   draft with `scenario_draft`. Add the products the paper's setup needs (`catalog_search`, then
   `scenario_edit` section setup.devices) and the paper's victim automations (section automations, Home Assistant
   YAML as data, entity ids from home_describe).
3. Attack. Translate each attack step into VESPER's building blocks (section attack): a vantage, an effect, the
   link and the device and feature it matches, hold times taken from the paper. Put physical actions of people
   (an intruder at the door, a resident leaving) in the timeline (section actors and timeline).
4. Defense. Add the paper's defense if VESPER has it (section defenses).
5. Claims. Write each of the paper's claims as a predicate over the run (section expect), with what is expected
   under each condition: baseline, defense, attack, attack+defense.
6. Gaps. Whatever the paper has and VESPER cannot express (a cloud service, radio, a spoofing step, a vantage VESPER
   does not have yet): record it with `gap`, with why. Never fake one.
7. Validate (`scenario_validate`), save (`scenario_save`), run (`scenario_run`), wait (`run_status`), read the
   report (`run_report`).
8. Explain the result to the researcher: which claims reproduced, which did not and why, and the gaps. Ask them
   to review the scenario file; correct it in the conversation if they ask.

Every edit is checked against the home: if a tool says an edit is wrong, read its message (it names the nearest valid
choice) and fix the edit. Do not stop at the first error."""


def build_server(bench: Workbench) -> MCPServer:
    server = MCPServer("vesper", instructions=INSTRUCTIONS)

    def tool(fn):
        """Register `fn` as a tool; a WorkbenchError reaches the model as the tool's error text."""
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except WorkbenchError as e:
                text = str(e)  # never a whole value echoed back, whatever raised it
                raise ToolError(text if len(text) <= 800 else text[:800] + " ...") from e
        call.__name__, call.__doc__, call.__annotations__ = fn.__name__, fn.__doc__, fn.__annotations__
        call.__wrapped__ = fn
        server.tool()(call)
        return fn

    @tool
    def capabilities() -> dict:
        """What VESPER can express: vantages, network effects and their fields, timeline actions, defenses,
        predicate events and forms, run conditions and modes, and the limits."""
        return bench.capabilities()

    @tool
    def homes() -> list:
        """The homes you may use (name, rooms, devices, scene) and the scenarios saved beside each."""
        return bench.homes()

    @tool
    def home_describe(home: str) -> dict:
        """One home: rooms, doorways, devices (product, features with values and ranges, Home Assistant entity
        ids, the network links their messages cross), network links, residents and automations."""
        return bench.home_describe(home)

    @tool
    def catalog_search(query: str = "", category: str | None = None, protocol: str | None = None,
                       limit: int = 20) -> list:
        """Real products from VESPER's catalog: words over id, vendor, model and description; category such as
        contact_sensor, lock, light, plug, motion_sensor; protocol zigbee, matter or tasmota."""
        return bench.catalog_search(query, category, protocol, limit)

    @tool
    def scenario_draft(home: str, title: str, paper: dict | None = None, from_scenario: str | None = None) -> dict:
        """Start a scenario draft on a home. paper: {title, authors, venue, doi}. from_scenario: copy a saved
        scenario of that home instead of starting empty. Returns the draft id."""
        return bench.scenario_draft(home, title, paper, from_scenario)

    @tool
    def scenario_edit(draft: str, section: str, action: str, value: dict | list | str | None = None,
                      index: int | None = None) -> dict:
        """Change one section of a draft: section is title, setup.devices, automations, actors, timeline, attack,
        defenses, expect, runs or gaps; action is add (an item), replace (the item at index, or the whole section
        without index) or remove (the item at index). Kept only if the whole draft stays valid; otherwise the error
        names what is wrong."""
        return bench.scenario_edit(draft, section, action, value, index)

    @tool
    def cite(draft: str, key: str, passage: str) -> dict:
        """Quote the paper exactly: steps and claims cite the passage by its key (threat, attack, result ...)."""
        return bench.cite(draft, key, passage)

    @tool
    def gap(draft: str, what: str, why: str) -> dict:
        """Record what the paper has and VESPER cannot express, and why. Never fake it instead."""
        return bench.gap(draft, what, why)

    @tool
    def scenario_validate(draft: str) -> dict:
        """Check the whole draft and show it as the YAML file it will be."""
        return bench.scenario_validate(draft)

    @tool
    def scenario_save(draft: str, name: str, overwrite: bool = False) -> dict:
        """Save the draft beside its home as scenarios/NAME.yaml (NAME: lower-case letters, digits and -)."""
        return bench.scenario_save(draft, name, overwrite)

    @tool
    def scenario_run(scenario: str, mode: str | None = None, repeat: int | None = None) -> dict:
        """Run a saved scenario (its name from homes) or a draft (its id): every condition, `repeat` times each,
        in the background. mode: logical (default, seconds) or live (Home Assistant, only if the server allows)."""
        return bench.scenario_run(scenario, mode, repeat)

    @tool
    def run_status(run: str) -> dict:
        """A run's progress: running, done or failed; traces written so far."""
        return bench.run_status(run)

    @tool
    def run_report(run: str) -> dict:
        """A finished run's report: gaps, then each claim per condition, and whether the paper's expectation was
        reproduced."""
        return bench.run_report(run)

    @server.prompt()
    def reproduce_paper() -> str:
        """Reproduce a smart-home security paper's attack and defense in VESPER, claim by claim."""
        return REPRODUCE_PAPER

    return server


def serve(roots: list[Path], catalog: dict, *, runs_dir: Path, allow_live: bool = False,
          stack_root: Path | None = None, catalog_dir: Path | None = None) -> None:
    bench = Workbench(roots, catalog, runs_dir=runs_dir, allow_live=allow_live, stack_root=stack_root,
                      catalog_dir=catalog_dir)
    build_server(bench).run("stdio")
