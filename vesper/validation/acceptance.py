"""Platform acceptance (spec §1 criterion 2; §4 L2, scale tier).

An unmodified Home Assistant must discover, display and control twins exposed through the
Zigbee2MQTT-compatible bridge. For each sampled product, every expected entity must appear with the right
device class and unit; controllable entities must carry a Home Assistant service call through to the twin;
sensors must show a physical stimulus applied to the twin.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable

from vesper.exposure.ha_discovery import ExpectedEntity, device_discovery
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.runtime import Runtime
from vesper.validation.stack import StackError

AMBIENT = {"temperature": 21.5, "local_temperature": 21.5, "humidity": 45.0, "illuminance": 300,
           "illuminance_lux": 300}
_AMBIENT_KEY = {"temperature": "temperature", "local_temperature": "temperature", "humidity": "humidity",
                "illuminance": "illuminance", "illuminance_lux": "illuminance"}


def select_products(records: list[dict], *, per_category: int, seed: int,
                    categories: list[str] | None = None) -> list[dict]:
    """A seeded, stratified sample of Zigbee products at L1 that expose at least one entity."""
    by_category: dict[str, list[dict]] = {}
    for r in sorted(records, key=lambda r: r["id"]):
        if not r["id"].startswith("zigbee:") or not r["fidelity"]["reached"]["L1"]:
            continue
        if categories and r["identity"]["category"] not in categories:
            continue
        if device_discovery(r, ieee="0x" + "0" * 16, friendly_name="probe")[2]:
            by_category.setdefault(r["identity"]["category"], []).append(r)
    rng = random.Random(seed)
    return [r for cat in sorted(by_category) for r in rng.sample(by_category[cat], min(per_category,
                                                                                      len(by_category[cat])))]


def _probe(e: ExpectedEntity, twin) -> dict | None:
    """What to do with an entity, and what the twin and Home Assistant must show afterwards."""
    f = twin.features[e.feature]
    if e.platform == "switch":
        return {"call": ("switch", "turn_on", {}), "twin": (e.feature, f["value_on"]), "ha": "on"}
    if e.platform == "light":
        data, extra = {}, None
        if "brightness" in twin.features and "range" in twin.features["brightness"]:
            data, extra = {"brightness": 128}, ("brightness", 128)
        return {"call": ("light", "turn_on", data), "twin": ("state", f["value_on"]), "also": extra, "ha": "on"}
    if e.platform == "lock":
        return {"call": ("lock", "lock", {}), "twin": ("state", f["value_on"]), "ha": "locked"}
    if e.platform == "number" and "range" in f:
        lo, hi = f["range"]
        value = lo + (hi - lo) // 2 if isinstance(lo, int) and isinstance(hi, int) else (lo + hi) / 2
        return {"call": ("number", "set_value", {"value": value}), "twin": (e.feature, value), "ha": float(value)}
    if e.platform == "select" and f.get("values"):
        option = f["values"][-1]
        return {"call": ("select", "select_option", {"option": str(option)}), "twin": (e.feature, option),
                "ha": str(option)}
    if e.platform == "binary_sensor" and e.feature == "contact":
        return {"stimulus": {"type": "contact", "state": "opened"}, "ha": "on"}
    if e.platform == "binary_sensor" and e.feature in ("occupancy", "presence"):
        return {"stimulus": {"type": "presence"}, "ha": "on"}
    if e.platform == "sensor" and e.feature in AMBIENT:
        return {"stimulus": {"type": "ambient", _AMBIENT_KEY[e.feature]: AMBIENT[e.feature]},
                "ha": float(AMBIENT[e.feature])}
    if e.platform == "sensor" and e.feature == "power":
        return {"stimulus": {"type": "load", "watts": 60.0}, "ha": 60.0}
    if e.platform == "event" and f.get("values"):
        return {"stimulus": {"type": "button_press", "action": f["values"][0]}, "event": str(f["values"][0])}
    return None


def _matches(state: dict | None, expected) -> bool:
    if state is None:
        return False
    if isinstance(expected, float):
        try:
            return abs(float(state["state"]) - expected) < 1e-6
        except (TypeError, ValueError):
            return False
    return state["state"] == expected


def _wait(fn: Callable[[], object], timeout: float, poll: float):
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(poll)


def check_product(ha, runtime: Runtime, bridge: Bridge, placement: Placement, *, timeout: float,
                  poll: float) -> dict:
    twin = runtime.twins[placement.twin_id]
    entities = []
    for e in bridge.expected[placement.twin_id]:
        state = _wait(lambda e=e: ha.state(e.entity_id), timeout, poll)
        row = {"entity_id": e.entity_id, "platform": e.platform, "feature": e.feature, "created": state is not None}
        if state is not None:
            attrs = state.get("attributes", {})
            row["attributes_ok"] = ((e.device_class is None or attrs.get("device_class") == e.device_class)
                                    and (e.unit is None or attrs.get("unit_of_measurement") == e.unit))
            probe = _probe(e, twin)
            if probe:
                row["probe"] = _run_probe(ha, runtime, placement, e, probe, timeout, poll)
        entities.append(row)
    passed = bool(entities) and all(r["created"] and r.get("attributes_ok", False)
                                    and r.get("probe", {"ok": True})["ok"] for r in entities)
    return {"id": twin.id, "category": twin.vds["identity"]["category"], "friendly_name": placement.friendly_name,
            "entities": entities, "skipped": bridge.skipped.get(twin.id, []), "passed": passed}


def _run_probe(ha, runtime, placement, e, probe, timeout, poll) -> dict:
    if "call" in probe:
        domain, service, data = probe["call"]
        try:
            ha.call(domain, service, {"entity_id": e.entity_id, **data})
        except StackError as err:  # Home Assistant refused this call; the run goes on (a dead HA still aborts)
            return {"ok": False, "kind": f"{domain}.{service}", "twin_ok": False, "ha_ok": False, "error": str(err)}
        feature, value = probe["twin"]
        twin_ok = _wait(lambda: runtime.state(placement.twin_id)[feature] == value, timeout, poll)
        if probe.get("also"):
            f2, v2 = probe["also"]
            twin_ok = twin_ok and _wait(lambda: runtime.state(placement.twin_id)[f2] == v2, timeout, poll)
        ha_ok = _wait(lambda: _matches(ha.state(e.entity_id), probe["ha"]), timeout, poll)
        return {"ok": bool(twin_ok and ha_ok), "kind": f"{domain}.{service}", "twin_ok": bool(twin_ok),
                "ha_ok": bool(ha_ok)}
    runtime.stimulate(placement.twin_id, probe["stimulus"])
    if "event" in probe:
        ok = _wait(lambda: ((ha.state(e.entity_id) or {}).get("attributes", {}).get("event_type") == probe["event"]),
                   timeout, poll)
    else:
        ok = _wait(lambda: _matches(ha.state(e.entity_id), probe["ha"]), timeout, poll)
    return {"ok": bool(ok), "kind": f"stimulus:{probe['stimulus']['type']}"}


def summarize(products: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for p in products:
        row = by_category.setdefault(p["category"], {"products": 0, "passed": 0})
        row["products"] += 1
        row["passed"] += p["passed"]
    return {"products": len(products), "passed": sum(p["passed"] for p in products),
            "categories_passing": sorted(c for c, r in by_category.items() if r["passed"] == r["products"]),
            "by_category": by_category}
