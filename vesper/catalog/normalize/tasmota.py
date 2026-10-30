"""Tasmota device templates -> VDS records (security-lab spec §3.3, plan W7a2).

A template names a product (title, model, category) and its module's GPIO pins, each pin a Tasmota component code
(Tasmota's documented components list): relays 224-251 (inverted 256-283), PWM channels 416-420 (inverted 448-452).
A relay is a POWER switch (POWER1..n for several); one PWM channel is a dimmable light (POWER and Dimmer). Colour
lights (several PWM channels), sensors, shutters and templates in the numbering before Tasmota 9.1 are left out, each
named with its reason. Tasmota publishes a state change at once and its whole state every TelePeriod (300 s by
default).
"""
from __future__ import annotations

import io
import json
import re
import tarfile

import yaml

from vesper.catalog.normalize.common import finalize_levels, make_id, new_record, unique_ids
from vesper.catalog.sources import source_entry

TEMPLATE_FILE = re.compile(r"^[^/]+/_templates/([^/]+)$")
RELAYS = (range(224, 252), range(256, 284))  # Relay1..28 and Relay_i1..28
PWM = (range(416, 421), range(448, 453))  # PWM1..5 and PWM_i1..5
TELE_PERIOD_S = 300
CATEGORIES = {"plug": "plug", "switch": "switch", "relay": "switch", "light": "light", "bulb": "light",
              "dimmer": "light"}


def _count(gpio: list, ranges) -> int:
    return sum(1 for code in gpio if isinstance(code, int) and any(code in r for r in ranges))


def _binary(name: str, ha: str, source: str, light: bool) -> dict:
    f = {"name": name, "property": name, "type": "binary", "access": ["state", "set", "get"], "category": None,
         "ha": ha, "source": source, "value_on": "ON", "value_off": "OFF", "value_toggle": "TOGGLE"}
    if light:
        f["path"] = "light"
    return f


def _record(stem: str, fm: dict, version: str) -> tuple[dict | None, str | None]:
    title = str(fm.get("title") or "").strip()
    if not title:
        return None, "no title"
    raw = fm.get("template9")
    if not raw:
        if fm.get("template"):
            return None, "its template uses the GPIO numbering from before Tasmota 9.1 (not converted)"
        return None, "no template"
    try:
        template = json.loads(raw)
        gpio = template["GPIO"]
    except (ValueError, TypeError, KeyError) as e:
        return None, f"its template is not readable: {e}"
    relays, pwm = _count(gpio, RELAYS), _count(gpio, PWM)
    category = CATEGORIES.get(str(fm.get("category") or "").lower())
    if pwm > 1:
        return None, f"a colour light ({pwm} PWM channels): not modelled yet"
    if pwm == 1 and relays > 1:
        return None, f"a dimmer with {relays} relays: not modelled yet"
    if not relays and not pwm:
        return None, "no relay and no dimmer (sensors and other modules are not modelled yet)"
    if category is None:
        category = "light" if pwm else "switch"
    vendor = title.split()[0]
    model = str(fm.get("model") or "").strip() or title
    prov = f"blakadder/templates@{version[:12]}"
    rec = new_record(protocol="tasmota", vendor=vendor, model=model, description=title, category=category,
                     ids={"tasmota_template": [stem]}, sources=[source_entry("tasmota", version)],
                     record_id=make_id("tasmota", vendor, stem))
    if pwm:
        level = {"name": "Dimmer", "property": "Dimmer", "type": "numeric", "access": ["state", "set", "get"],
                 "category": None, "ha": "light", "source": prov, "range": [0, 100], "unit": "%", "path": "light"}
        rec["features"] = [_binary("POWER", "light", prov, light=True), level]
    else:
        names = ["POWER"] if relays == 1 else [f"POWER{k}" for k in range(1, relays + 1)]
        ha = "light" if category == "light" else "switch"
        rec["features"] = [_binary(n, ha, prov, light=ha == "light") for n in names]
    rec["behavior"]["reporting"] = {f["name"]: {"change": 0, "min_s": 0, "max_s": TELE_PERIOD_S}
                                    for f in rec["features"]}
    rec["binding"]["tasmota"] = {"template": template, "relays": relays, "pwm": pwm, "file": stem,
                                 "type": fm.get("type"), "link": fm.get("link")}
    fid = rec["fidelity"]
    fid["provenance"] = {"identity": prov, "features": prov}
    fid["reasons"].update({
        "L2": "credited per product by validation evidence against Home Assistant's Tasmota integration",
        "L3": f"Tasmota's documented defaults: a change at once, the whole state every {TELE_PERIOD_S} s; timing "
              "not yet validated",
        "L4": "Tasmota's firmware (GPL-3.0) is not emulated",
    })
    return finalize_levels(rec, l0_ok=True, l0_reason=None), None


def tasmota_notice(tarball: bytes, version: str) -> str:
    """The notice a catalog with Tasmota records carries: where they come from, and the templates' license."""
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        member = next((m for m in tf.getmembers() if re.fullmatch(r"[^/]+/LICENSE(\.md)?", m.name)), None)
        text = tf.extractfile(member).read().decode("utf-8", "replace") if member else "(no license file found)"
    return (f"# Notice: Tasmota device templates\n\nThe records in `tasmota.json` are derived from the device "
            f"templates of blakadder/templates at commit {version} (https://github.com/blakadder/templates), "
            "distributed under the Eclipse Public License 2.0, whose text follows.\n\n" + text)


def tasmota_records(tarball: bytes, version: str) -> tuple[list[dict], list[dict]]:
    """(records, rejected templates). Members are read in memory; nothing is extracted to disk."""
    records, rejected = [], []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        for member in tf.getmembers():
            m = TEMPLATE_FILE.match(member.name)
            if not member.isfile() or m is None:
                continue
            stem = m.group(1)
            text = tf.extractfile(member).read().decode("utf-8", "replace")
            parts = text.split("---", 2)
            if not text.startswith("---") or len(parts) < 3:
                rejected.append({"file": f"_templates/{stem}", "reason": "no front matter"})
                continue
            try:
                fm = yaml.safe_load(parts[1])
            except yaml.YAMLError as e:
                rejected.append({"file": f"_templates/{stem}", "reason": f"front matter is not YAML: {e}"})
                continue
            if not isinstance(fm, dict):
                rejected.append({"file": f"_templates/{stem}", "reason": "no front matter"})
                continue
            rec, why = _record(stem, fm, version)
            if rec is None:
                rejected.append({"file": f"_templates/{stem}", "reason": why})
            else:
                records.append(rec)
    unique_ids(records)
    return records, rejected
