"""Real-product VDS records (the catalog fixtures plus a few simple devices) shared by the twin tests."""
import json

import pytest

from tests.catalog.fixtures import BATTERY, LIGHT, POWER, SWITCH, ZIGBEE_DOC, zdef
from vesper.catalog.fingerprint import code_fingerprint
from vesper.catalog.normalize.zigbee import zigbee_records

TEMPERATURE = {"name": "temperature", "access": 5, "type": "numeric", "property": "temperature", "unit": "°C"}
OCCUPANCY = {"name": "occupancy", "access": 1, "type": "binary", "property": "occupancy",
             "value_on": True, "value_off": False}
ACTION = {"name": "action", "access": 1, "type": "enum", "property": "action", "category": "diagnostic",
          "values": ["single", "double", "hold"]}
BRIGHTNESS_ONLY = {"type": "light", "features": [LIGHT["features"][0], LIGHT["features"][1]]}


def records():
    doc = {"version": "26.123.0", "definitions": ZIGBEE_DOC["definitions"] + [
        zdef("TH-1", "Acme", [TEMPERATURE, BATTERY]),
        zdef("PIR-1", "Acme", [OCCUPANCY]),
        zdef("BTN-1", "Acme", [ACTION, BATTERY]),
        zdef("DIM-1", "Acme", [BRIGHTNESS_ONLY]),
        zdef("PLUG-1", "Acme", [SWITCH, POWER]),
    ]}
    return {r["identity"]["model"]: r for r in zigbee_records(doc)}


@pytest.fixture
def vds():
    return records()


def write_catalog(out, recs, **report):
    """A catalog directory as `vesper catalog build` leaves it, built by the current catalog code."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "zigbee.json").write_text(json.dumps(recs))
    (out / "build_report.json").write_text(json.dumps({"code_sha256": code_fingerprint(), **report}))
    return out
