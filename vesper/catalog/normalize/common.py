"""Helpers shared by the normalizers: ids, record skeletons and the fidelity ladder (spec §3.1, §3.2)."""
from __future__ import annotations

import re

from vesper.catalog.schema import VDS_VERSION

CATEGORIES = (
    "light", "plug", "switch", "button", "lock", "thermostat", "cover", "fan",
    "contact_sensor", "motion_sensor", "leak_sensor", "smoke_sensor", "gas_sensor",
    "vibration_sensor", "climate_sensor", "air_quality_sensor", "light_sensor",
    "energy_meter", "bridge", "appliance", "media", "camera", "other",
)
LEVELS = ("L0", "L1", "L2", "L3", "L4")
_NOT_SLUG = re.compile(r"[^a-z0-9._-]+")


def slug(text: str) -> str:
    s = _NOT_SLUG.sub("-", text.strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    return s or "unknown"


def make_id(protocol: str, vendor: str, model: str) -> str:
    return f"{protocol}:{slug(vendor)}:{slug(model)}"


def new_record(
    *, protocol: str, vendor: str, model: str, description: str, category: str, ids: dict,
    sources: list[dict], record_id: str | None = None, white_label_of: str | None = None,
) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    return {
        "vds_version": VDS_VERSION,
        "id": record_id or make_id(protocol, vendor, model),
        "identity": {
            "vendor": vendor, "model": model, "description": description, "category": category,
            "ids": ids, "protocols": [protocol], "white_label_of": white_label_of,
        },
        "features": [],
        "binding": {},
        "behavior": {"reporting": {}},
        "firmware": None,
        "fidelity": {"reached": {lv: False for lv in LEVELS}, "provenance": {}, "assumed": [], "reasons": {}},
        "sources": sources,
    }


def l1_status(features: list[dict], assumed: list[str]) -> tuple[bool, str | None]:
    """L1 needs a primary feature mapped to Home Assistant, no unmodeled primary feature, nothing assumed.

    A feature is primary unless its source marks it config or diagnostic. An event (a button's
    `action`) is always primary: it is the device's function even where the source tags it diagnostic.
    """
    primary = [f for f in features if f["category"] is None or f["ha"] == "event"]
    if not primary:
        return False, "no primary (non-config, non-diagnostic) features"
    unsupported = sorted(f["name"] for f in primary if f["type"] == "unsupported")
    if unsupported:
        return False, "unsupported primary feature types: " + ", ".join(unsupported[:5])
    if not any(f["ha"] for f in primary):
        return False, "no primary feature maps to a Home Assistant platform"
    if "features" in assumed:
        return False, "features evaluated with assumed default options"
    return True, None


def finalize_levels(rec: dict, *, l0_ok: bool, l0_reason: str | None) -> dict:
    """Set L0 and L1 by the spec's rules. Levels form a ladder, so L1 requires L0."""
    fid = rec["fidelity"]
    assumed_identity = [a for a in fid["assumed"] if a.startswith("identity")]
    fid["reached"]["L0"] = bool(l0_ok and not assumed_identity)
    if not fid["reached"]["L0"]:
        fid["reasons"]["L0"] = l0_reason or "identity fields assumed: " + ", ".join(assumed_identity)
    ok, why = l1_status(rec["features"], fid["assumed"])
    if ok and not fid["reached"]["L0"]:
        ok, why = False, "L0 not reached"
    fid["reached"]["L1"] = ok
    if not ok:
        fid["reasons"]["L1"] = why
    return rec
