"""Check generated discovery messages with Home Assistant's own schemas (offline; no server, no port).

The fakes in the tests cannot know every rule Home Assistant applies to a discovery message (unit and
device-class pairs, option combinations), so this runs Home Assistant's code itself, inside the venv
`vesper setup ha` created, over any number of products.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from vesper.exposure.ha_discovery import device_discovery
from vesper.exposure.z2m_bridge import ieee_address
from vesper.validation.stack import StackError

SCRIPT = Path(__file__).with_name("ha_schema_check.py")


def discovery_messages(records: list[dict]) -> list[tuple[str, dict]]:
    """(topic, payload) for every L1 Zigbee record, as the bridge would publish it."""
    out = []
    for r in records:
        if r["id"].startswith("zigbee:") and r["fidelity"]["reached"]["L1"]:
            topic, payload, _, _ = device_discovery(r, ieee=ieee_address(r["id"]), friendly_name=r["id"])
            out.append((topic, payload))
    return out


def check_with_ha(messages: list[tuple[str, dict]], ha_python: Path, *, run=subprocess.run) -> list[dict]:
    """One result per component: {topic, component, platform, ok, error}."""
    stdin = "".join(json.dumps({"topic": t, "payload": p}) + "\n" for t, p in messages)
    r = run([str(ha_python), "-I", str(SCRIPT)], input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        raise StackError(f"Home Assistant's schema check failed ({r.returncode}): {r.stderr.strip()[-400:]}")
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def summarize_schema(results: list[dict]) -> dict:
    devices: dict[str, bool] = {}
    for r in results:
        devices[r["topic"]] = devices.get(r["topic"], True) and r["ok"]
    return {"components": len(results), "accepted": sum(r["ok"] for r in results), "devices": len(devices),
            "devices_fully_accepted": sum(devices.values()),
            "errors": dict(Counter(r["error"] for r in results if not r["ok"]).most_common())}
