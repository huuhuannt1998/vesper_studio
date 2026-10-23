"""Matter products (DCL identity) x device types (Apache-2.0 ZAP XML) -> VDS records (spec §3).

A product's features are the mandatory attributes of the mandatory application clusters of its
certified device type. Certification guarantees them; optional clusters of a product are unknown.
"""
from __future__ import annotations

import copy

from vesper.catalog.ingest.matter_zap import DeviceType, ZapModel, resolve_type
from vesper.catalog.normalize.common import finalize_levels, new_record
from vesper.catalog.normalize.ha_map import matter_ha
from vesper.catalog.sources import source_entry

UTILITY_CLUSTERS = frozenset({
    "Descriptor", "Identify", "Groups", "Scenes Management", "Binding", "Fixed Label", "User Label",
    "Basic Information", "Bridged Device Basic Information", "Access Control", "Power Source",
})
# Mandatory attributes that describe the device rather than its state.
DESCRIPTIVE = frozenset({
    "Options", "OnLevel", "LockType", "ControlSequenceOfOperation", "FanModeSequence", "EndProductType", "Type",
    "ConfigStatus", "OccupancySensorType", "OccupancySensorTypeBitmap", "SupportedOperatingModes",
    "MultiPressMax", "ColorMode", "EnhancedColorMode",
})
COMMAND_SET = frozenset({  # attributes that a standard command changes, though not writable directly
    ("On/Off", "OnOff"), ("Level Control", "CurrentLevel"), ("Door Lock", "LockState"),
    ("Color Control", "ColorTemperatureMireds"), ("Thermostat", "SystemMode"),
})
UNITS = {"temperature": ("°C", 0.01), "percent": ("%", None), "percent100ths": ("%", 0.01),
         "power_mw": ("mW", None), "voltage_mv": ("mV", None), "amperage_ma": ("mA", None),
         "energy_mwh": ("mWh", None), "elapsed_s": ("s", None), "epoch_s": ("s", None)}
ATTRIBUTE_UNITS = {("Relative Humidity Measurement", "MeasuredValue"): ("%", 0.01)}
CATEGORY = {
    "On/Off Light": "light", "Dimmable Light": "light", "Color Temperature Light": "light",
    "Extended Color Light": "light",
    "On/Off Plug-in Unit": "plug", "Dimmable Plug-in Unit": "plug", "Mounted On/Off Control": "plug",
    "Mounted Dimmable Load Control": "plug",
    "On/Off Light Switch": "switch", "Dimmer Switch": "switch", "Color Dimmer Switch": "switch",
    "Control Bridge": "switch", "Generic Switch": "button", "Doorbell": "button", "Audio Doorbell": "button",
    "Contact Sensor": "contact_sensor", "Occupancy Sensor": "motion_sensor", "On/Off Sensor": "other",
    "Temperature Sensor": "climate_sensor", "Humidity Sensor": "climate_sensor",
    "Pressure Sensor": "climate_sensor", "Light Sensor": "light_sensor", "Water Leak Detector": "leak_sensor",
    "Rain Sensor": "leak_sensor", "Smoke CO Alarm": "smoke_sensor", "Air Quality Sensor": "air_quality_sensor",
    "Door Lock": "lock", "Door Lock Controller": "lock",
    "Thermostat": "thermostat", "Thermostat Controller": "thermostat", "Room Air Conditioner": "thermostat",
    "Heat Pump": "thermostat",
    "Window Covering": "cover", "Window Covering Controller": "cover", "Closure": "cover",
    "Closure Panel": "cover", "Closure Controller": "cover",
    "Fan": "fan", "Air Purifier": "fan", "Humidifier/Dehumidifier": "appliance",
    "Aggregator": "bridge", "Bridged Node": "bridge", "Thread Border Router": "bridge",
    "Robotic Vacuum Cleaner": "appliance", "Laundry Washer": "appliance", "Laundry Dryer": "appliance",
    "Refrigerator": "appliance", "Dishwasher": "appliance", "Cook Surface": "appliance", "Cooktop": "appliance",
    "Oven": "appliance", "Extractor Hood": "appliance", "Microwave Oven": "appliance",
    "Water Heater": "appliance", "Temperature Controlled Cabinet": "appliance", "Water Valve": "appliance",
    "Pump": "appliance",
    "Basic Video Player": "media", "Casting Video Player": "media", "Speaker": "media", "Content App": "media",
    "Casting Video Client": "media", "Video Remote Control": "media",
    "Camera": "camera", "Video Doorbell": "camera", "Snapshot Camera": "camera", "Floodlight Camera": "camera",
    "EVSE": "energy_meter", "Electrical Sensor": "energy_meter", "Device Energy Management": "energy_meter",
    "Solar Power": "energy_meter", "Battery Storage": "energy_meter", "Electrical Meter": "energy_meter",
    "Electrical Utility Meter": "energy_meter",
}
L2_REASON = "Matter tier not built yet (Plan D)"
L3_REASON = "no timing source yet"
L4_REASON = "no firmware anchor for this product"
L1_SCOPE = "mandatory device-type features only; this product's optional clusters are unknown"


def _category(attribute: str) -> str | None:
    descriptive = (attribute in DESCRIPTIVE or attribute.startswith(("Min", "Max", "NumberOf"))
                   or attribute.endswith(("Capabilities", "Tolerance")))
    return "diagnostic" if descriptive else None


def device_type_features(zap: ZapModel, dt: DeviceType, prov: str) -> list[dict]:
    feats = []
    for cname in dt.server_clusters:
        if cname in UTILITY_CLUSTERS:
            continue
        cluster = zap.clusters.get(cname)
        if cluster is None:
            feats.append({"name": cname, "type": "unsupported", "access": [], "category": None, "ha": None,
                          "source": prov, "unsupported_reason": f"cluster {cname!r} has no ZAP definition"})
            continue
        for a in cluster.attributes:
            if a.optional:
                continue
            kind, extra = resolve_type(zap, a.type)
            settable = a.writable or (cname, a.name) in COMMAND_SET
            f = {"name": f"{cname}.{a.name}", "type": kind,
                 "access": ["state", "get"] + (["set"] if settable else []),
                 "category": _category(a.name),
                 "ha": matter_ha(dt.name, cname, a.name) if kind != "unsupported" else None,
                 "source": prov, "property": a.name}
            if kind == "numeric":
                unit, scale = ATTRIBUTE_UNITS.get((cname, a.name)) or UNITS.get(a.type, (None, None))
                if unit:
                    f["unit"] = unit
                if scale:
                    f["scale"] = scale
                if a.min is not None and a.max is not None:
                    f["range"] = [a.min, a.max]
            elif kind == "enum":
                f["values"] = list(extra)
            elif kind == "bitmap":
                f["flags"] = list(extra)
            else:
                if kind == "unsupported":
                    f["unsupported_reason"] = f"Matter type {a.type!r} is not modeled"
            feats.append(f)
    return feats


def matter_records(models: list[dict], vendors: list[dict], zap: ZapModel, *, dcl_version: str,
                   zap_version: str) -> list[dict]:
    names = {int(v["vendorID"]): (v.get("companyPreferredName") or v.get("vendorName") or "").strip()
             for v in vendors}
    sources = [source_entry("dcl", dcl_version), source_entry("matter_zap", zap_version)]
    features_by_type: dict[int, list[dict]] = {}
    out = []
    for m in models:
        vid, pid, dtid = int(m["vid"]), int(m["pid"]), int(m.get("deviceTypeId") or 0)
        dt = zap.device_types.get(dtid)
        vendor, model = names.get(vid, ""), (m.get("productName") or "").strip()
        rec = new_record(
            protocol="matter", vendor=vendor or f"VID 0x{vid:04X}", model=model or f"PID 0x{pid:04X}",
            description=(m.get("productLabel") or "").strip(),
            category=CATEGORY.get(dt.name, "other") if dt else "other",
            ids={"matter_vid_pid": [[vid, pid]]}, sources=list(sources), record_id=f"matter:{vid:04x}:{pid:04x}",
        )
        fprov = f"connectedhomeip-zap@{zap_version}:device type 0x{dtid:04X}"
        if dt is not None:
            if dtid not in features_by_type:
                features_by_type[dtid] = device_type_features(zap, dt, fprov)
            rec["features"] = copy.deepcopy(features_by_type[dtid])
        rec["binding"] = {"matter": {"device_type_id": dtid, "device_type": dt.name if dt else None,
                                     "server_clusters": list(dt.server_clusters) if dt else []}}
        fid = rec["fidelity"]
        fid["provenance"] = {"identity": f"dcl@{dcl_version}:model/{vid}/{pid}", "features": fprov}
        problems = [why for why, bad in (("vendor not in DCL vendor info", not vendor),
                                         ("no product name", not model),
                                         (f"device type 0x{dtid:04X} not in the ZAP XML", dt is None)) if bad]
        finalize_levels(rec, l0_ok=not problems, l0_reason="; ".join(problems) or None)
        fid["reasons"].update({"L1_scope": L1_SCOPE, "L2": L2_REASON, "L3": L3_REASON, "L4": L4_REASON})
        out.append(rec)
    return out
