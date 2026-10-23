"""zigbee-herdsman-converters definitions -> VDS records, one per product including white labels (spec §3)."""
from __future__ import annotations

import copy

from vesper.catalog.normalize.common import finalize_levels, new_record, unique_ids
from vesper.catalog.normalize.ha_map import COMPOSITE_PLATFORM, GET, SET, STATE, zigbee_ha
from vesper.catalog.sources import source_entry

LEAF_TYPES = frozenset({"binary", "numeric", "enum", "text"})
# Static defaults of zigbee-herdsman-converters lib/reporting at 26.123.0: (min_s, max_s, change).
# `change` is converted from raw ZCL units to the feature's own units: temperature and humidity report
# in 0.01 units (100 raw = 1.0); illuminance is logarithmic (10000*log10(lux)+1), so any change counts.
# A device's own configure() may differ, so every use is listed in `assumed` (spec §3.2).
REPORTING_DEFAULTS = {
    "state": (0, 3600, 0), "brightness": (1, 3600, 1), "occupancy": (0, 3600, 0),
    "temperature": (10, 3600, 1.0), "humidity": (10, 3600, 1.0), "illuminance": (10, 3600, 0),
    "power": (5, 3600, 1), "battery": (3600, 65000, 0), "lock_state": (0, 3600, 0),
}
REPORTING_PARENT = {"state": {"light", "switch"}, "brightness": {"light"}}
L2_REASON = ("credited per product by validation evidence: `vesper validate acceptance`, "
             "summarised by `vesper report`; the catalog itself only knows the sources")
L3_REASON = "reporting uses zigbee-herdsman-converters static defaults (assumed); timing not yet validated"
L4_REASON = "no firmware anchor for Zigbee (no Zigbee SoC emulator exists)"


def _identifies(fingerprint: dict) -> bool:
    """True if a zigbee-herdsman-converters fingerprint carries at least one matching value."""
    return any(v not in (None, "", [], {}) for k, v in fingerprint.items() if k != "priority")


def _access(bits: int) -> list[str]:
    return [name for bit, name in ((STATE, "state"), (SET, "set"), (GET, "get")) if bits & bit]


def leaves(exposes: list[dict] | None, parent: str | None = None, prefix: str = ""):
    """Yield (leaf, platform composite type or None, name prefix) for every non-composite expose."""
    for e in exposes or []:
        if "features" in e:
            kind = e.get("type")
            key = e.get("property") or e.get("name") or ""
            if kind in COMPOSITE_PLATFORM:
                yield from leaves(e["features"], kind, prefix)
            else:
                yield from leaves(e["features"], parent, f"{prefix}{key}." if key else prefix)
        else:
            yield e, parent, prefix


def category(exposes: list[dict] | None) -> str:
    tops = {e.get("type") for e in exposes or []}
    names = {leaf.get("name") for leaf, _, _ in leaves(exposes)}
    if "light" in tops:
        return "light"
    for kind, cat in (("lock", "lock"), ("climate", "thermostat"), ("cover", "cover"), ("fan", "fan")):
        if kind in tops:
            return cat
    if "switch" in tops:
        return "plug" if names & {"power", "energy"} else "switch"
    for name, cat in (("water_leak", "leak_sensor"), ("smoke", "smoke_sensor"), ("gas", "gas_sensor"),
                      ("carbon_monoxide", "gas_sensor"), ("contact", "contact_sensor"),
                      ("occupancy", "motion_sensor"), ("presence", "motion_sensor"),
                      ("vibration", "vibration_sensor")):
        if name in names:
            return cat
    if names & {"temperature", "humidity"}:
        return "climate_sensor"
    if names & {"co2", "pm25", "pm10", "voc"}:
        return "air_quality_sensor"
    if names & {"illuminance", "illuminance_lux"}:
        return "light_sensor"
    if names & {"power", "energy"}:
        return "energy_meter"
    return "button" if "action" in names else "other"


def _feature(leaf: dict, parent: str | None, prefix: str, prov: str) -> dict:
    kind = leaf.get("type")
    f = {
        "name": prefix + str(leaf.get("property") or leaf.get("name") or "unnamed"),
        "type": kind if kind in LEAF_TYPES else "unsupported",
        "access": _access(int(leaf.get("access") or 0)),
        "category": leaf.get("category"),
        "ha": zigbee_ha(leaf, parent) if kind in LEAF_TYPES else None,
        "source": prov,
    }
    if parent:
        f["path"] = parent
    for key in ("property", "endpoint", "unit", "values", "value_on", "value_off", "value_toggle"):
        if key in leaf:
            f[key] = leaf[key]
    if "value_min" in leaf and "value_max" in leaf:
        f["range"] = [leaf["value_min"], leaf["value_max"]]
    if "value_step" in leaf:
        f["step"] = leaf["value_step"]
    if f["type"] == "unsupported":
        f["unsupported_reason"] = f"zigbee expose type {kind!r} is not modeled"
    return f


def _record(d, vendor, model, description, zigbee_model, fingerprint, white_label_of, prov, sources) -> dict:
    exposes = d["exposes"] or []
    rec = new_record(
        protocol="zigbee", vendor=(vendor or "").strip() or "unknown", model=(model or "").strip() or "unknown",
        description=description or "", category=category(exposes),
        ids={"zigbee_model": list(zigbee_model), "zigbee_fingerprint": [dict(f) for f in fingerprint]},
        sources=list(sources), white_label_of=white_label_of,
    )
    fid = rec["fidelity"]
    seen: dict[str, int] = {}
    for leaf, parent, prefix in leaves(exposes):
        f = _feature(leaf, parent, prefix, prov)
        n = seen.get(f["name"], 0)
        seen[f["name"]] = n + 1
        if n:
            f["name"] = f"{f['name']}#{n + 1}"
        rec["features"].append(f)
        name = leaf.get("name")
        if (name in REPORTING_DEFAULTS and int(leaf.get("access") or 0) & STATE
                and parent in REPORTING_PARENT.get(name, {parent})):
            mn, mx, ch = REPORTING_DEFAULTS[name]
            rec["behavior"]["reporting"][f["name"]] = {"min_s": mn, "max_s": mx, "change": ch}
            fid["assumed"].append(f"behavior.reporting.{f['name']}")
    rec["binding"] = {"zigbee": {"exposes": copy.deepcopy(exposes), "options": list(d["options"]),
                                 "ota": bool(d["ota"])}}
    fid["provenance"] = {"identity": prov, "features": prov}
    if d["exposes_dynamic"]:
        fid["assumed"].append("features")
    l0_ok = bool(zigbee_model) or any(_identifies(f) for f in fingerprint)
    finalize_levels(rec, l0_ok=l0_ok,
                    l0_reason=None if l0_ok else "no zigbee model id or fingerprint with an identifying value")
    if d.get("error"):
        fid["reached"]["L1"] = False
        fid["reasons"]["L1"] = f"exposes evaluation failed: {d['error']}"
    fid["reasons"].update({"L2": L2_REASON, "L3": L3_REASON, "L4": L4_REASON})
    return rec


def zigbee_records(doc: dict) -> list[dict]:
    """All products in one zhc export: each definition plus each of its white-label rebrands."""
    version = doc["version"]
    prov = f"zigbee-herdsman-converters@{version}"
    sources = [source_entry("zigbee", version)]
    out, bases = [], []
    for d in doc["definitions"]:
        base = _record(d, d["vendor"], d["model"], d["description"], d["zigbee_model"], d["fingerprint"],
                       None, prov, sources)
        out.append(base)
        bases.append(None)
        for w in d["white_label"]:
            own = bool(w.get("fingerprint"))  # a rebrand with its own fingerprint is identified by it alone
            out.append(_record(d, w.get("vendor") or d["vendor"], w.get("model"),
                               w.get("description") or d["description"],
                               [] if own else d["zigbee_model"], w.get("fingerprint") if own else d["fingerprint"],
                               base["id"], prov, sources))
            bases.append(base)
    unique_ids(out)  # e.g. "HALO" and "HALO+" share a slug but are different products
    for rec, base in zip(out, bases, strict=True):
        if base is not None:
            rec["identity"]["white_label_of"] = base["id"]
    return out
