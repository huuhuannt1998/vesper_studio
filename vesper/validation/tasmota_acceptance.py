"""Platform acceptance, Wi-Fi tier (spec §1 criterion 2; §4 L2): Home Assistant's own Tasmota integration must
discover, display and control VESPER's virtual Tasmota devices, each on its own MQTT connection to VESPER's broker as
a real device has.

For each sampled product, every entity HATasmota should create (a switch per relay, or one light for a PWM dimmer)
must appear and be available; a service call from Home Assistant must reach the twin, and a change at the device (its
button pressed) must reach Home Assistant. A dimmer's brightness must arrive scaled as Tasmota scales it (0-100).
"""
from __future__ import annotations

import random

from vesper.exposure.tasmota import expected_entities
from vesper.validation.acceptance import wait_until

ON, OFF = "ON", "OFF"


def select_tasmota(records: list[dict], *, per_category: int, seed: int,
                   categories: list[str] | None = None) -> list[dict]:
    """A seeded, stratified sample of Tasmota products that make at least one Home Assistant entity."""
    by_category: dict[str, list[dict]] = {}
    for r in sorted(records, key=lambda r: r["id"]):
        if not r["id"].startswith("tasmota:") or (categories and r["identity"]["category"] not in categories):
            continue
        if expected_entities(r, "probe"):
            by_category.setdefault(r["identity"]["category"], []).append(r)
    rng = random.Random(seed)
    return [r for cat in sorted(by_category) for r in rng.sample(by_category[cat], min(per_category,
                                                                                      len(by_category[cat])))]


def _ha_brightness_to_dimmer(b: int) -> int:
    """HATasmota's light: HA brightness 1-255 to Tasmota's Dimmer 1-100 (round(b / 255 * 100), at least 1)."""
    return min(100, max(1, round(b / 255 * 100)))


def check_tasmota_product(ha, runtime, twin_id: str, record: dict, name: str, *, timeout: float = 30.0,
                          poll: float = 0.25) -> dict:
    """One product: its entities appear and are available; Home Assistant's commands reach the twin; the device's own
    changes reach Home Assistant."""
    result = {"id": record["id"], "category": record["identity"]["category"], "entities": [], "passed": False}
    entities = expected_entities(record, name)
    for e in entities:
        row = {"entity_id": e.entity_id, "platform": e.platform, "feature": e.feature}
        state = wait_until(lambda: (lambda s: s if s and s.get("state") not in (None, "unavailable") else None)(
            ha.state(e.entity_id)), timeout, poll)
        row["created"] = state is not None
        if state is None:
            result["entities"].append({**row, "error": "Home Assistant did not create it (or it stayed unavailable)"})
            continue
        twin = runtime.twins[twin_id]
        feature = e.feature
        # Home Assistant -> the device
        data = {"entity_id": e.entity_id}
        if e.platform == "light":
            data["brightness"] = 128
        ha.call(e.platform, "turn_on", data)
        row["command_reached_twin"] = bool(wait_until(lambda: twin.state.get(feature) == ON or None, timeout, poll))
        row["ha_shows_on"] = bool(wait_until(lambda: (ha.state(e.entity_id) or {}).get("state") == "on" or None,
                                             timeout, poll))
        if e.platform == "light" and "Dimmer" in twin.state:
            want = _ha_brightness_to_dimmer(128)
            row["dimmer"] = {"want": want, "twin": twin.state.get("Dimmer")}
            row["dimmer_ok"] = abs((twin.state.get("Dimmer") or 0) - want) <= 1
        # the device -> Home Assistant (its button pressed: an operation at the device)
        runtime.operate(twin_id, feature, OFF, actor="resident")
        row["device_change_reached_ha"] = bool(wait_until(
            lambda: (ha.state(e.entity_id) or {}).get("state") == "off" or None, timeout, poll))
        row["passed"] = row["command_reached_twin"] and row["ha_shows_on"] and row["device_change_reached_ha"] and \
            row.get("dimmer_ok", True)
        result["entities"].append(row)
    result["passed"] = bool(entities) and all(r.get("passed") for r in result["entities"])
    return result


def summarize(results: list[dict]) -> dict:
    cats: dict[str, list[bool]] = {}
    for r in results:
        cats.setdefault(r["category"], []).append(r["passed"])
    return {"products": len(results), "passed": sum(r["passed"] for r in results),
            "entities": sum(len(r["entities"]) for r in results),
            "categories_passing": sorted(c for c, v in cats.items() if v and all(v)),
            "categories": sorted(cats)}
