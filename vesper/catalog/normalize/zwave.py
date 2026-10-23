"""Z-Wave JS device configs -> VDS records: identity only (spec §1 L0).

The config database lists manufacturers, labels and product ids, but neither a device category nor the
supported command classes (those come from the live Z-Wave interview). The category is therefore a
keyword guess listed in `assumed`, so no Z-Wave record reaches L0 or L1 until a better source exists.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import PurePosixPath

import json5

from vesper.catalog.normalize.common import finalize_levels, new_record, slug
from vesper.catalog.sources import source_entry

DEVICES = "package/config/devices/"
MANUFACTURERS = "package/config/manufacturers.json"
KEYWORDS = (
    (("plug", "outlet", "socket"), "plug"), (("lock", "deadbolt"), "lock"),
    (("dimmer", "switch", "relay"), "switch"), (("bulb", "lamp", "light"), "light"),
    (("thermostat", "trv", "radiator"), "thermostat"), (("motion", "occupancy", "pir", "presence"), "motion_sensor"),
    (("leak", "flood", "water sensor"), "leak_sensor"), (("smoke",), "smoke_sensor"),
    (("door", "window", "contact"), "contact_sensor"), (("shade", "blind", "curtain", "garage", "shutter"), "cover"),
    (("controller", "stick", "gateway", "bridge"), "bridge"),
)
L1_REASON = "device capabilities come from the live Z-Wave interview, not the config database"


def _load(data: bytes):
    text = data.decode("utf-8")
    try:
        return json.loads(text)
    except ValueError:
        return json5.loads(text)  # many device files carry comments


def _category(text: str) -> str:
    low = text.lower()
    for words, cat in KEYWORDS:
        if any(w in low for w in words):
            return cat
    return "other"


def _hex16(value) -> str | None:
    try:
        n = int(str(value), 16)
    except ValueError:
        return None
    return f"0x{n:04x}" if 0 <= n <= 0xFFFF else None


def zwave_records(tarball: bytes, version: str) -> tuple[list[dict], list[dict]]:
    """(records, rejected files). Members are read in memory; nothing is extracted to disk."""
    prov = f"@zwave-js/config@{version}"
    records, rejected = [], []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        manufacturers = {k.lower(): v for k, v in _load(tf.extractfile(MANUFACTURERS).read()).items()}
        for member in tf.getmembers():
            name = member.name
            if (not member.isfile() or not name.startswith(DEVICES) or not name.endswith(".json")
                    or "/templates/" in name or PurePosixPath(name).name.startswith("_")):
                continue
            try:
                cfg = _load(tf.extractfile(member).read())
            except (ValueError, UnicodeDecodeError) as e:
                rejected.append({"file": name, "error": f"unparsable: {e}"})
                continue
            mid = _hex16(cfg.get("manufacturerId")) if isinstance(cfg, dict) else None
            if mid is None or not str(cfg.get("label") or "").strip():
                rejected.append({"file": name, "error": "missing label or manufacturerId"})
                continue
            ids = [{"manufacturer_id": mid, "product_type": pt, "product_id": pi}
                   for d in cfg.get("devices") or []
                   if (pt := _hex16(d.get("productType"))) and (pi := _hex16(d.get("productId")))]
            label = str(cfg["label"]).strip()
            description = str(cfg.get("description") or "").strip()
            vendor = str(cfg.get("manufacturer") or manufacturers.get(mid) or "").strip()
            rec = new_record(protocol="zwave", vendor=vendor or f"manufacturer {mid}", model=label,
                             description=description, category=_category(f"{description} {label}"),
                             ids={"zwave": ids}, sources=[source_entry("zwave", version)],
                             record_id=f"zwave:{mid}:{slug(label)}")
            params = cfg.get("paramInformation")
            rec["binding"] = {"zwave": {"config_params": len(params) if isinstance(params, (list, dict)) else 0,
                                        "firmware_version": cfg.get("firmwareVersion")}}
            fid = rec["fidelity"]
            fid["assumed"].append("identity.category")
            fid["provenance"] = {"identity": f"{prov}:{name.removeprefix('package/')}"}
            finalize_levels(rec, l0_ok=bool(vendor and ids), l0_reason=None)
            fid["reasons"].update({"L1": L1_REASON, "L2": "zwave-mock tier reserved for later",
                                   "L3": "no timing source yet", "L4": "no firmware anchor"})
            records.append(rec)
    return records, rejected
