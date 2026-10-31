# Plan W4b: the paper-to-scenario MCP server (as built)

**Goal:** `vesper mcp`, a stdio MCP server through which Claude Desktop, LM Studio or any MCP client reads a smart-home
security paper and sets its attack and defense up in a VESPER home, runs it and reports claim by claim
(security-lab spec §5).

**Why:** the PI's 2026-10-26 decision: the security lab's main function is paper-to-scenario through an MCP server;
Phantom-Delay is its test case. Full authority on the project, PI 2026-10-31 ("Go. I give you full authority ...").

**Spec:** `docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md` §5 (server, tools, prompt, evaluation), §7.

## Built
- `vesper/lab/workbench.py`: the tools' logic as plain functions. `capabilities`, `homes`, `home_describe` (rooms,
  doorways, devices with features and Home Assistant entity ids, the links each device's messages cross),
  `catalog_search`, `scenario_draft` (empty, or copied from a saved scenario), `scenario_edit` (section + add /
  replace / remove), `cite`, `gap`, `scenario_validate`, `scenario_save`, `scenario_run` (background thread),
  `run_status`, `run_report`.
- `vesper/lab/mcp_server.py`: the MCP adapter on the official SDK (mcp 2.x `MCPServer`), the `reproduce_paper`
  prompt and the server instructions. A workbench error reaches the model as the tool's error text (`ToolError`).
- `vesper mcp [--homes DIR]... [--catalog] [--runs] [--root] [--allow-live] [--print-config]`;
  `vesper setup claude-desktop` registers the server in Claude Desktop's config (atomic write, a backup kept, a
  different `vesper` entry kept unless `--force`).
- `vesper lab scenario score DRAFT... --gold GOLD...`: the evaluation's scorer (spec §5.4).
- `scripts/mcp_openai_eval.py`: an OpenAI-compatible endpoint (LM Studio) driving the MCP server, for the local-model
  evaluation.

## Decisions (mine, recorded)
- **A draft is always a valid scenario.** Each edit is applied to a copy, the copy is loaded with `load_scenario`
  (and its automations with VESPER's engine), and kept only if it loads; the error names what is wrong and the
  nearest valid choice. An empty draft is valid, so the model builds it step by step.
- **Automations are data in the draft** (Home Assistant YAML as a list), written beside the scenario on save as
  `NAME.automations.yaml`; an empty list means the home's own automations, both when checked and when saved.
- **Only names `homes()` lists are reachable**, resolved inside the configured roots; saves go to
  `<home>/scenarios/NAME.yaml` with a slug name and never overwrite unless asked.
- **Live runs need `--allow-live`** on the server (they start VESPER's Home Assistant); logical runs by default. At
  most 50 runs per call.
- **The scorer compares devices by category and feature**, not by name, since a model may add its own sensor.

## Tests
`tests/lab/test_workbench.py` (11), `tests/lab/test_mcp_server.py` (6: every tool and the prompt through a real MCP
client session, no host/port/command/code parameter, errors as text, `--print-config`, Claude Desktop registration),
`tests/lab/test_scenario_score.py` (2). Suite: 1223 passed, 33 skipped.
