"""Paper-to-scenario with a local model (security-lab spec §5.4, success criterion 2): an OpenAI-compatible endpoint
(LM Studio's server) drives VESPER's MCP server through tool calls, as an MCP client app would. Evaluation only.

    python scripts/mcp_openai_eval.py --endpoint http://127.0.0.1:1234/v1 --model qwen2.5-7b-instruct-1m \
        --mcp-config mcp.json --paper paper.txt --task task.md --out run.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from mcp import Client, StdioServerParameters

from vesper.lab.mcp_server import REPRODUCE_PAPER

RESULT_CHARS = 8000  # a tool result is cut here, as a small context would otherwise fill with one home_describe


def tool_schema(t) -> dict:
    schema = dict(t.input_schema or {"type": "object", "properties": {}})
    schema.pop("title", None)
    return {"type": "function", "function": {"name": t.name, "description": (t.description or "")[:1000],
                                             "parameters": schema}}


async def main(a) -> int:
    cfg = json.loads(Path(a.mcp_config).read_text())["mcpServers"]["vesper"]
    log = open(a.out, "w")  # noqa: SIM115 - closed below

    def write(kind, **x):
        log.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **x}) + "\n")
        log.flush()
    async with Client(StdioServerParameters(command=cfg["command"], args=cfg["args"])) as mcp:
        tools = [tool_schema(t) for t in (await mcp.list_tools()).tools]
        paper = Path(a.paper).read_text()[:a.paper_chars]
        messages = [{"role": "system", "content": REPRODUCE_PAPER},
                    {"role": "user", "content": Path(a.task).read_text() + "\n\nTHE PAPER (text):\n\n" + paper}]
        calls = errors = 0
        async with httpx.AsyncClient(timeout=a.timeout) as http:
            for turn in range(a.max_turns):
                r = await http.post(f"{a.endpoint}/chat/completions", json={
                    "model": a.model, "messages": messages, "tools": tools, "temperature": a.temperature,
                    "max_tokens": 2048})
                if r.status_code != 200:
                    write("http_error", status=r.status_code, body=r.text[:2000])
                    break
                msg = r.json()["choices"][0]["message"]
                messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v})
                write("assistant", turn=turn, content=msg.get("content"), tool_calls=msg.get("tool_calls"))
                if not msg.get("tool_calls"):
                    break
                for call in msg["tool_calls"]:
                    calls += 1
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                        res = await mcp.call_tool(name, args)
                        text = "\n".join(getattr(c, "text", "") for c in res.content)
                        if res.is_error:
                            errors += 1
                    except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001 - the model reads the error
                        text, errors = f"error: {e}", errors + 1
                    write("tool", name=name, arguments=call["function"].get("arguments"), result=text[:4000])
                    messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                                     "content": text[:RESULT_CHARS]})
        write("end", turns=turn + 1, calls=calls, errors=errors)
    log.close()
    print(f"{turn + 1} turns, {calls} tool calls, {errors} errors; transcript {a.out}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="http://127.0.0.1:1234/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--mcp-config", required=True)
    p.add_argument("--paper", required=True, help="the paper as text")
    p.add_argument("--paper-chars", type=int, default=80000)
    p.add_argument("--task", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--timeout", type=float, default=600)
    raise SystemExit(asyncio.run(main(p.parse_args())))
