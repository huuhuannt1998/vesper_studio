"""Matter platform acceptance (spec §1 criterion 2; §4 L2, Matter tier).

Home Assistant's Matter integration, through VESPER's own Matter Server, must commission each sampled twin's
matter.js node and then display and control it. Each expected entity, one per Home Assistant mapping of the
twin's features, must appear with the right device class and be available. Controllable entities must carry a
service call through the node to the twin and back. Sensors must show a stimulus applied to the twin.

The sample is one product (or `per_type`) per Matter device type in the categories the spec targets: sensors,
plugs, lights, locks and thermostats. All products of a device type share its certified mandatory features.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from vesper.exposure.matter_tier import HostError
from vesper.twins.runtime import MATTER_CONTACT
from vesper.validation.acceptance import run_probe, wait_until

MATTER_L2_CATEGORIES = ("light", "plug", "lock", "thermostat", "contact_sensor", "motion_sensor", "climate_sensor",
                        "light_sensor", "leak_sensor", "smoke_sensor", "air_quality_sensor")
MATTER_INITIAL = {"Door Lock.LockState": "Unlocked"}  # a lock starts unlocked, so `lock.lock` changes it
AMBIENT = {"temperature": 21.5, "humidity": 45.0, "illuminance": 300.0}  # stimulus values, in Home Assistant units
# The mode a climate probe asks for: the host's Thermostat heats only, its Room Air Conditioner cools only
# (vesper/exposure/matter_host/recipes.mjs).
CLIMATE_MODE = {"Thermostat": "Heat", "Room Air Conditioner": "Cool"}
# Home Assistant makes one entity per device for these platforms, from several attributes; for the others,
# one entity per attribute (a smoke alarm's two "problem" alerts are two binary sensors).
COMPOSITE = frozenset({"light", "switch", "lock", "climate", "cover", "fan"})
ONOFF, LOCK_STATE, SYSTEM_MODE, OCCUPANCY = ("On/Off.OnOff", "Door Lock.LockState", "Thermostat.SystemMode",
                                             "Occupancy Sensing.Occupancy")


@dataclass(frozen=True)
class MatterEntity:
    """One entity Home Assistant should create for a twin: a platform, a device class where one is expected,
    and the twin features behind it."""
    platform: str
    device_class: str | None
    features: tuple[str, ...]


def expected_entities(record: dict) -> list[MatterEntity]:
    groups: dict[str, list[str]] = {}
    for f in record.get("features", []):
        if f["type"] != "unsupported" and f.get("ha") and f.get("category") != "diagnostic":
            key = f["ha"] if f["ha"].partition("/")[0] in COMPOSITE else f"{f['ha']}#{f['name']}"
            groups.setdefault(key, []).append(f["name"])
    out = []
    for key, names in groups.items():
        platform, _, cls = key.partition("#")[0].partition("/")
        out.append(MatterEntity(platform, cls or None, tuple(names)))
    return out


def device_type(record: dict) -> str | None:
    return ((record.get("binding") or {}).get("matter") or {}).get("device_type")


def select_matter_products(records: list[dict], *, per_type: int, seed: int,
                           categories: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """A seeded sample of `per_type` products for every Matter device type, at L1 and with at least one
    expected entity, in the target categories."""
    wanted = set(categories or MATTER_L2_CATEGORIES)
    by_type: dict[str, list[dict]] = {}
    for r in sorted(records, key=lambda r: r["id"]):
        if (r["id"].startswith("matter:") and r["fidelity"]["reached"]["L1"] and device_type(r)
                and r["identity"]["category"] in wanted and expected_entities(r)):
            by_type.setdefault(device_type(r), []).append(r)
    rng = random.Random(seed)
    return [r for t in sorted(by_type) for r in rng.sample(by_type[t], min(per_type, len(by_type[t])))]


def initial_state(record: dict) -> dict:
    names = {f["name"] for f in record.get("features", [])}
    return {k: v for k, v in MATTER_INITIAL.items() if k in names}


def match_entities(expected: list[MatterEntity], states: list[dict]) -> list[dict | None]:
    """Each expected entity's Home Assistant state, or None: an entity of the same platform with the expected
    device class (with none expected, one without a class is preferred). An entity matches at most once."""
    free = sorted(states, key=lambda s: s["entity_id"])
    out = []
    for e in expected:
        candidates = [s for s in free if s["entity_id"].split(".", 1)[0] == e.platform
                      and (e.device_class is None or s.get("attributes", {}).get("device_class") == e.device_class)]
        if e.device_class is None:
            candidates.sort(key=lambda s: s.get("attributes", {}).get("device_class") is not None)
        hit = candidates[0] if candidates else None
        if hit is not None:
            free.remove(hit)
        out.append(hit)
    return out


def matter_probe(e: MatterEntity, twin) -> dict | None:
    """A command or stimulus whose target differs from the twin's current value, and what must follow."""
    s = twin.state
    if e.platform in ("light", "switch") and ONOFF in e.features:
        on = not s[ONOFF]
        return {"call": (e.platform, "turn_on" if on else "turn_off", {}), "twin": (ONOFF, on),
                "ha": "on" if on else "off"}
    if e.platform == "lock" and LOCK_STATE in e.features:
        lock = s[LOCK_STATE] != "Locked"
        return {"call": ("lock", "lock" if lock else "unlock", {}),
                "twin": (LOCK_STATE, "Locked" if lock else "Unlocked"), "ha": "locked" if lock else "unlocked"}
    if e.platform == "climate" and SYSTEM_MODE in twin.features:
        mode = CLIMATE_MODE.get(device_type(twin.vds), "Heat")
        target = mode if s[SYSTEM_MODE] != mode else "Off"
        return {"call": ("climate", "set_hvac_mode", {"hvac_mode": target.lower()}), "twin": (SYSTEM_MODE, target),
                "ha": target.lower()}
    if e.platform == "binary_sensor" and MATTER_CONTACT in e.features and device_type(twin.vds) == "Contact Sensor":
        to = "opened" if s[MATTER_CONTACT] else "closed"  # Boolean State is true while the contact is closed
        return {"stimulus": {"type": "contact", "state": to}, "ha": "on" if to == "opened" else "off"}
    if e.platform == "binary_sensor" and OCCUPANCY in e.features and not s[OCCUPANCY] & 1:
        return {"stimulus": {"type": "presence"}, "ha": "on"}
    if e.platform == "sensor" and e.device_class in AMBIENT:
        value = AMBIENT[e.device_class]
        return {"stimulus": {"type": "ambient", e.device_class: value}, "ha": float(value)}
    return None


def _close(state: dict | None, expected) -> bool:
    """Home Assistant's value matches; a converted reading (illuminance is logarithmic in Matter) to 0.1 %."""
    if state is None:
        return False
    if isinstance(expected, float):
        try:
            return math.isclose(float(state["state"]), expected, rel_tol=1e-3, abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return state["state"] == expected


def _lookup(ha, serial: str, expected: list[MatterEntity]) -> tuple[list[dict | None], list[dict]]:
    ids = ha.matter_entities().get(serial, [])
    states = [s for s in (ha.state(i) for i in ids) if s is not None]
    return match_entities(expected, states), states


def check_matter_product(ha, runtime, tier, twin_id: str, server_url: str, *, timeout: float, poll: float,
                         commission_timeout: float) -> dict:
    """Commission one twin's node and check its entities in Home Assistant. A node that cannot be commissioned
    fails its product; the run goes on."""
    twin, spec = runtime.twins[twin_id], tier.specs[twin_id]
    expected = expected_entities(twin.vds)
    wanted = {n for e in expected for n in e.features}
    out = {"id": twin.vds["id"], "twin_id": twin_id, "device_type": spec.device_type,
           "category": twin.vds["identity"]["category"], "serial": spec.serial,
           "vendor_id": spec.vendor_id, "product_id": spec.product_id,
           "unresolved": sorted(wanted - tier.resolved.get(twin_id, set())), "node_id": None,
           "commission_error": None, "extra_entities": []}
    try:
        out["node_id"] = tier.commission(twin_id, server_url, commission_timeout)
    except HostError as e:
        out["commission_error"] = str(e)
    if out["node_id"] is None:
        rows = [{"platform": e.platform, "device_class": e.device_class, "features": list(e.features),
                 "entity_id": None, "created": False, "available": False} for e in expected]
        return {**out, "entities": rows, "passed": False, "exercised": False}
    wait_until(lambda: all(_lookup(ha, spec.serial, expected)[0]), timeout, poll)
    matched, states = _lookup(ha, spec.serial, expected)
    rows = []
    for e, s in zip(expected, matched, strict=True):
        row = {"platform": e.platform, "device_class": e.device_class, "features": list(e.features),
               "entity_id": s["entity_id"] if s else None, "created": s is not None, "available": False}
        if s is not None:
            eid = s["entity_id"]
            row["available"] = bool(wait_until(
                lambda eid=eid: (ha.state(eid) or {}).get("state") not in (None, "unavailable"), timeout, poll))
            probe = matter_probe(e, twin)
            if probe:
                row["probe"] = run_probe(ha, runtime, twin_id, eid, probe, timeout, poll, matches=_close)
        rows.append(row)
    used = {r["entity_id"] for r in rows}
    out["extra_entities"] = sorted(s["entity_id"] for s in states if s["entity_id"] not in used)
    passed = bool(rows) and all(r["created"] and r["available"] and r.get("probe", {"ok": True})["ok"] for r in rows)
    return {**out, "entities": rows, "passed": passed, "exercised": any("probe" in r for r in rows)}


def summarize_matter(products: list[dict]) -> dict:
    """Per device type and per category. A device type passes when every sampled product passed and was
    exercised by a command or stimulus; `device_types_displayed` only asks that every product passed."""
    by_type: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    for p in products:
        t = by_type.setdefault(p["device_type"],
                               {"products": 0, "passed": 0, "exercised": 0, "category": p["category"]})
        c = by_category.setdefault(p["category"], {"products": 0, "passed": 0, "exercised": 0})
        for row in (t, c):
            row["products"] += 1
            row["passed"] += p["passed"]
            row["exercised"] += p["exercised"]

    def passing(rows):
        return sorted(k for k, r in rows.items() if r["passed"] == r["exercised"] == r["products"])

    return {"products": len(products), "passed": sum(p["passed"] for p in products), "device_types": len(by_type),
            "device_types_passing": passing(by_type),
            "device_types_displayed": sorted(k for k, r in by_type.items() if r["passed"] == r["products"]),
            "categories_passing": passing(by_category), "by_device_type": by_type, "by_category": by_category}
