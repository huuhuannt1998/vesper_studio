"""L0/L1 round trip (spec §4): every definition the bridge publishes in `bridge/devices` must equal the
source record in the pinned zigbee-herdsman-converters snapshot, field for field (0 diffs)."""
from __future__ import annotations

import json
import threading

from vesper.catalog.normalize.zigbee import zigbee_records


def source_definitions(doc: dict) -> dict[str, dict]:
    """VDS record id -> the definition Zigbee2MQTT would publish, built from the raw snapshot."""
    definitions = []
    for d in doc["definitions"]:
        base = {"source": "native", "model": d["model"], "vendor": d["vendor"], "description": d["description"],
                "exposes": d["exposes"] or [], "options": d["options"], "supports_ota": bool(d["ota"])}
        definitions.append(base)
        for w in d["white_label"]:
            definitions.append({**base, "vendor": w.get("vendor") or d["vendor"], "model": w.get("model"),
                                "description": w.get("description") or d["description"]})
    return {r["id"]: dfn for r, dfn in zip(zigbee_records(doc), definitions, strict=True)}


def diff_paths(a, b, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b), key=str):
            if key not in a or key not in b:
                out.append(f"{path}/{key}")
            else:
                out.extend(diff_paths(a[key], b[key], f"{path}/{key}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}[len {len(a)} != {len(b)}]"]
        return [p for i, (x, y) in enumerate(zip(a, b, strict=True)) for p in diff_paths(x, y, f"{path}[{i}]")]
    return [] if a == b and type(a) is type(b) else [path or "/"]


def compare(published: list[dict], twin_of: dict[str, str], sources: dict[str, dict]) -> dict:
    report = {"checked": 0, "identical": 0, "different": {}, "unmatched": []}
    for device in published:
        if device.get("type") == "Coordinator":
            continue
        twin_id = twin_of.get(device["friendly_name"])
        if twin_id not in sources:
            report["unmatched"].append(device["friendly_name"])
            continue
        report["checked"] += 1
        paths = diff_paths(device["definition"], sources[twin_id])
        if paths:
            report["different"][twin_id] = paths[:20]
        else:
            report["identical"] += 1
    return report


def receive_retained(client, topic: str, timeout: float = 30.0):
    """The retained JSON message on `topic`, read through a separate MQTT client that stops listening after."""
    got, ready = {}, threading.Event()

    def on_message(c, userdata, msg):
        if msg.topic == topic and msg.payload and not ready.is_set():  # an empty payload clears a retained message
            got["payload"] = json.loads(msg.payload)
            ready.set()

    client.on_message = on_message
    client.subscribe(topic)
    try:
        if not ready.wait(timeout):
            raise TimeoutError(f"no retained message on {topic} within {timeout:.0f} s")
    finally:
        client.unsubscribe(topic)
        client.on_message = None
    return got["payload"]
