# Plan D: the Matter tier (L2) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host every Matter-capable twin as a real matter.js node that an unmodified Home Assistant commissions through its own Matter Server, displays and controls: the Matter tier of spec §2 and the "L2, Matter tier" row of spec §4.

**Architecture:**
1. The catalog's Matter records say what Home Assistant 2026.10.2 makes of each attribute (Task 1). Three crosswalk errors, found by reading Home Assistant's own Matter discovery rules, are fixed there: dimmable plugs and dimmable mounted loads are lights; a smoke alarm's alerts are binary sensors and its ExpressedState has no entity; air quality is an enum sensor.
2. The twin runtime applies presence, contact and ambient stimuli to Matter twins in Matter's raw units (Task 2).
3. `vesper/exposure/matter_tier.py` keeps twins and nodes in step (Task 3):
   - `node_spec` gives each twin its Matter identity: the product's vendor and product ids and names, a serial number, a distinct discriminator and a valid setup code;
   - `Host` runs one Node.js process that hosts all nodes and speaks JSON lines;
   - `MatterTier` sends the twins' reports to the nodes and turns a controller's change into a platform command, putting the node back when the twin refuses it.
4. `vesper/validation/stack.py` gains VESPER's own Matter Server (the matter.js `matter-server` that Home Assistant 2026.10 uses), `vesper setup matter`, Home Assistant's Matter config flow, and a lookup of Home Assistant's Matter entities by serial number (Task 4).
5. `vesper/validation/matter_acceptance.py` and `vesper validate matter` commission each sampled node and check its entities, commands and stimuli; `vesper report` credits Matter L2 only to products that passed (Task 5).
6. `vesper/exposure/matter_host/` is the Node side: `codec.mjs` (VDS values ↔ matter.js), `recipes.mjs` (one recipe per device type), `stdout-guard.mjs` (keeps matter.js's logging off the protocol stream) and `host.mjs`, pinned by `package.json` and `package-lock.json` (Task 6).
7. The run book gains Experiment 6 (Task 7).

**Tech Stack:** Python ≥3.11 (standard library, httpx); Node.js ≥22.13 with @matter/main and @matter/nodejs 0.17.9 and matter-server 1.4.0; Home Assistant Core 2026.10.2 (`matter` integration, matter-python-client 1.4.0).

**Spec:** `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md`:
- §1 criterion 2: "Target: L2 for every Matter device type that has certified products in the sensor, plug, light, lock or thermostat categories";
- §2: `vesper/exposure/matter_tier` "Hosts Matter-capable twins as matter.js nodes (Apache-2.0, many per process) and syncs their state with the twin runtime";
- §4, row "L2, Matter tier": "The Matter Server Home Assistant uses … commissions each matter.js node; same entity and command checks … | All sampled types pair and control correctly";
- §3: matter.js and the Matter Server need Node ≥22.13 (amended 2026-10-23); §7 risk 2: Matter cannot use loopback.

## Global Constraints

- Laptop-only, no Docker, at most 2 cores (spec §1 criterion 6). No new Python dependency.
- Node packages exactly as `package-lock.json` pins them, installed with `npm ci --ignore-scripts` into `.vesper/matter/host`, never into the source tree.
- matter.js is a runtime library only: no matter.js model or resource text enters VDS (spec §5 Licensing, amended 2026-10-23).
- VESPER's own instances only, on non-default ports: Home Assistant 127.0.0.1:18123, Matter Server 127.0.0.1:15580, nodes UDP 25540 + *i*. Never 1883, 5540, 5580 or 8123.
- Nothing fails silently: a node that cannot be added or commissioned fails its product and is reported; a run over nothing does not pass; a Home Assistant error is not an empty result.
- Shared Mac: this plan's tasks run only unit tests (Python with fakes; `node --test` on pure modules). `vesper setup matter` (network), the host contract test and `vesper validate matter` (mDNS on the lab network) run only after the PI's go, in a slot agreed in the ledger.
- Lint gate: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **A node the server cannot commission** (attestation, mDNS) must fail its product, not the run, and a run where none of the first 3 nodes can be commissioned must stop. Pinned by `test_a_node_the_server_cannot_commission_fails_without_stopping_the_run` (Task 5) and `test_the_run_gives_up_when_no_node_can_be_commissioned` (Task 5).
2. **A device type without a recipe** (the DCL adds one) must fail that product alone, while a dead host still ends the run. Pinned by `test_a_twin_the_host_cannot_add_fails_alone` and `test_a_dead_host_still_ends_the_run` (Task 3), and by the contract test `test_a_device_type_without_a_recipe_is_refused` (Task 6).
3. **matter.js's console logging** on stdout must not corrupt the JSON-lines protocol. Pinned by `stdout-guard.test.mjs` (Task 6).
4. **Two nodes answering one pairing code** would commission the wrong node. Nodes get distinct 12-bit discriminators and are commissioned by QR code. Pinned by `test_pairing_codes_are_valid_and_distinct_and_long_names_are_cut_to_matters_limit` and `test_a_node_is_commissioned_by_its_qr_code_and_its_node_id_kept` (Task 3).
5. **A Home Assistant error read as an empty home**, or a controller's change that the twin refuses but the node keeps, would misreport results. Pinned by `test_a_template_error_is_not_an_empty_home` (Task 4) and `test_a_change_the_device_would_refuse_is_rejected_and_the_node_put_back` (Task 3).

## Rulings carried into this plan

- **The crosswalk follows Home Assistant 2026.10.2's Matter discovery rules** (`matter/light.py`, `switch.py`, `binary_sensor.py`, `sensor.py`), read from the pinned release. Cost if wrong: a product fails acceptance for a crosswalk error, which the run reports.
- **Sensors expect one entity per attribute; lights, switches, locks and climate one per device**, as Home Assistant makes them. Cost if wrong: a missing or extra expected entity, reported.
- **Descriptive attributes keep the recipe's value on the node.** The twin's default for them is only the first enum value (a heating thermostat would claim "CoolingOnly"); only state is pushed. Cost if wrong: a descriptive attribute differs between twin and node; no check reads it.
- **Commissioning goes from the host straight to the Matter Server's WebSocket** (`commission_with_code`, by QR code), not through Home Assistant's WebSocket API, which forwards to the same server. Cost if wrong: none for what is checked; Home Assistant learns of the node from the server either way.
- **The server runs with `--enable-test-net-dcl`:** matter.js signs each node's PAI and DAC for the product's own vendor and product id with a test PAA, which the server otherwise refuses. Nodes present the real vendor id by default; `--vid-mode test` is the fallback, named in the report. Cost if wrong: commissioning fails under real ids, and the fallback is used and disclosed.
- **L2 is credited only to the sampled products that passed,** not to every product of their device type, as for Zigbee. The report also counts device types. Cost if wrong: under-credit, which a PI decision can lift.
- **Climate probes ask for the mode the recipe supports:** the Thermostat heats only, the Room Air Conditioner cools only. Cost if wrong: one probe value.

---
### Task 1: The catalog's Matter records: booleans, L2 reason, and Home Assistant's own mapping

**Files:**
- Modify: `vesper/catalog/normalize/matter.py` (Matter booleans carry `value_on`/`value_off`; the L2 reason names `vesper validate matter`), `vesper/catalog/normalize/ha_map.py` (dimmable loads are lights; smoke-alarm alerts are binary sensors; air quality is an enum sensor)
- Modify tests: `tests/catalog/test_matter_normalize.py`, `tests/catalog/test_ha_map.py`, `tests/twins/test_twin.py`

**Interfaces:**
- Produces: every Matter `binary` feature has `value_on: true`, `value_off: false`; `matter_ha(device_type, cluster, attribute)` agrees with Home Assistant 2026.10.2's Matter discovery for the 18 sampled device types; `L2_REASON` in `vesper/catalog/normalize/matter.py`.

- [ ] **Step 1: Write the failing tests**

In `tests/catalog/test_matter_normalize.py`, replace:
```python
    assert feats["Door Lock.OperatingMode"]["values"] == ["Normal", "Vacation"]
```
with:
```python
    assert feats["Door Lock.OperatingMode"]["values"] == ["Normal", "Vacation"]


def test_a_matter_boolean_is_true_or_false_and_l2_waits_for_validation_evidence():
    r = records()["matter:100b:0016"]
    state = r["features"][0]
    assert (state["type"], state["value_on"], state["value_off"]) == ("binary", True, False)
    assert "vesper validate matter" in r["fidelity"]["reasons"]["L2"]
```

In `tests/catalog/test_ha_map.py`, replace:
```python
    ("Robotic Vacuum Cleaner", "RVC Run Mode", "CurrentMode", None),
])
```
with:
```python
    ("Robotic Vacuum Cleaner", "RVC Run Mode", "CurrentMode", None),
    # Home Assistant 2026.10.2 matter/light.py claims On/Off for these before matter/switch.py sees it
    ("Dimmable Plug-in Unit", "On/Off", "OnOff", "light"),
    ("Mounted Dimmable Load Control", "Level Control", "CurrentLevel", "light"),
    ("Mounted On/Off Control", "On/Off", "OnOff", "switch/outlet"),
    # matter/binary_sensor.py: a Smoke CO Alarm's alerts are binary sensors; ExpressedState has no entity
    ("Smoke CO Alarm", "Smoke CO Alarm", "ExpressedState", None),
    ("Smoke CO Alarm", "Smoke CO Alarm", "BatteryAlert", "binary_sensor/battery"),
    ("Smoke CO Alarm", "Smoke CO Alarm", "TestInProgress", "binary_sensor/running"),
    ("Smoke CO Alarm", "Smoke CO Alarm", "HardwareFaultAlert", "binary_sensor/problem"),
    ("Smoke CO Alarm", "Smoke CO Alarm", "EndOfServiceAlert", "binary_sensor/problem"),
    ("Smoke CO Alarm", "Smoke CO Alarm", "SmokeState", "binary_sensor/smoke"),
    ("Smoke CO Alarm", "Smoke CO Alarm", "COState", "binary_sensor/carbon_monoxide"),
    ("Air Quality Sensor", "Air Quality", "AirQuality", "sensor/enum"),  # matter/sensor.py "AirQuality"
])
```

In `tests/twins/test_twin.py`, replace:
```python
        t.coerce("baud_rate", True)
```
with:
```python
        t.coerce("baud_rate", True)


def test_a_matter_twin_takes_booleans_for_its_boolean_attributes():
    from tests.catalog.test_matter_normalize import records as matter_records
    t = Twin.from_vds(matter_records()["matter:100b:0016"])
    assert t.state["Boolean State.StateValue"] is False and t.coerce("Boolean State.StateValue", True) is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_matter_normalize.py tests/catalog/test_ha_map.py tests/twins/test_twin.py -q 2>&1 | tail -1`

Expected: `12 failed, 45 passed`: ten new crosswalk cases (the two dimmable loads, six smoke-alarm attributes, CO state, air quality), the Matter-boolean test and the twin's boolean test. (`Mounted On/Off Control` already maps to `switch/outlet` and passes.)

- [ ] **Step 3: Implement**

In `vesper/catalog/normalize/matter.py`, replace:
```python
}
L2_REASON = "Matter tier not built yet (Plan D)"
L3_REASON = "no timing source yet"
```
with:
```python
}
L2_REASON = ("credited per product by validation evidence: `vesper validate matter`, "
             "summarised by `vesper report`; the catalog itself only knows the sources")
L3_REASON = "no timing source yet"
```

In `vesper/catalog/normalize/matter.py`, replace:
```python
                f["flags"] = list(extra)
            else:
```
with:
```python
                f["flags"] = list(extra)
            elif kind == "binary":
                f["value_on"], f["value_off"] = True, False  # a Matter boolean
            else:
```

In `vesper/catalog/normalize/ha_map.py`, replace:
```python
    ("Electrical Energy Measurement", "CumulativeEnergyImported"): "sensor/energy",
    ("Smoke CO Alarm", "ExpressedState"): "sensor/enum",
}
```
with:
```python
    ("Electrical Energy Measurement", "CumulativeEnergyImported"): "sensor/energy",
    ("Air Quality", "AirQuality"): "sensor/enum",
    # Home Assistant 2026.10.2 (matter/binary_sensor.py) shows a smoke alarm's states and alerts as binary
    # sensors and has no entity for its ExpressedState.
    ("Smoke CO Alarm", "SmokeState"): "binary_sensor/smoke",
    ("Smoke CO Alarm", "COState"): "binary_sensor/carbon_monoxide",
    ("Smoke CO Alarm", "BatteryAlert"): "binary_sensor/battery",
    ("Smoke CO Alarm", "TestInProgress"): "binary_sensor/running",
    ("Smoke CO Alarm", "HardwareFaultAlert"): "binary_sensor/problem",
    ("Smoke CO Alarm", "EndOfServiceAlert"): "binary_sensor/problem",
}
```

In `vesper/catalog/normalize/ha_map.py`, replace:
```python
LIGHTING_CLUSTERS = frozenset({"On/Off", "Level Control", "Color Control"})
```
with:
```python
LIGHTING_CLUSTERS = frozenset({"On/Off", "Level Control", "Color Control"})
# Dimmable loads that Home Assistant 2026.10.2 makes lights (matter/light.py claims their On/Off first)
DIMMABLE_LOADS = frozenset({"Dimmable Plug-in Unit", "Mounted Dimmable Load Control"})
```

In `vesper/catalog/normalize/ha_map.py`, replace:
```python
    if cluster in LIGHTING_CLUSTERS:
        if "Light" in device_type and "Switch" not in device_type:
            return "light"
```
with:
```python
    if cluster in LIGHTING_CLUSTERS:
        if ("Light" in device_type and "Switch" not in device_type) or device_type in DIMMABLE_LOADS:
            return "light"
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_matter_normalize.py tests/catalog/test_ha_map.py tests/twins/test_twin.py tests/catalog -q 2>&1 | tail -1`

Expected: `160 passed, 2 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/normalize/matter.py vesper/catalog/normalize/ha_map.py tests/catalog/test_matter_normalize.py tests/catalog/test_ha_map.py tests/twins/test_twin.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(catalog): Matter booleans and the Matter crosswalk Home Assistant 2026.10.2 uses

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: Matter stimuli in the twin runtime

**Files:**
- Modify: `vesper/twins/runtime.py` (presence sets and clears the Occupied bit; a Contact Sensor's Boolean State is true while closed; ambient readings reach Matter attributes in raw units, illuminance logarithmic)
- Modify tests: `tests/twins/test_runtime.py`

**Interfaces:**
- Produces: `MATTER_PRESENCE`, `MATTER_CONTACT = "Boolean State.StateValue"`, `MATTER_AMBIENT`, `MATTER_LUX` in `vesper/twins/runtime.py`; the stimuli `presence`, `contact` and `ambient` act on Matter twins.

- [ ] **Step 1: Write the failing tests**

In `tests/twins/test_runtime.py`, replace:
```python
    assert "KeyError('feature')" in rt.notes[2]
```
with:
```python
    assert "KeyError('feature')" in rt.notes[2]


def matter_vds(device_type, *features):
    return {"id": "matter:fff1:" + device_type.lower().replace(" ", "-"),
            "binding": {"matter": {"device_type": device_type}},
            "features": [dict(name=n, type=t, access=["state", "get"], **extra) for n, t, extra in features]}


def test_presence_sets_and_clears_the_matter_occupied_bit():
    rt, events, (tid,) = runtime(matter_vds("Occupancy Sensor", ("Occupancy Sensing.Occupancy", "bitmap", {})))
    assert rt.stimulate(tid, {"type": "presence"}) == ["Occupancy Sensing.Occupancy"]
    assert rt.state(tid)["Occupancy Sensing.Occupancy"] == 1
    rt.clock.advance(OCCUPANCY_TIMEOUT_S + 1)
    assert rt.state(tid)["Occupancy Sensing.Occupancy"] == 0 and [e.value for e in events] == [1, 0]


def test_a_matter_contact_sensor_is_true_while_closed_and_a_leak_detector_ignores_contact(vds):
    from tests.catalog.test_matter_normalize import records as matter_records
    rt, events, (door,) = runtime(matter_records()["matter:100b:0016"])
    leak = rt.add(matter_vds("Water Leak Detector", ("Boolean State.StateValue", "binary",
                                                     {"value_on": True, "value_off": False}))).id
    assert rt.stimulate(door, {"type": "contact", "state": "closed"}) == ["Boolean State.StateValue"]
    assert rt.state(door)["Boolean State.StateValue"] is True
    rt.stimulate(door, {"type": "contact", "state": "opened"})
    assert rt.state(door)["Boolean State.StateValue"] is False
    assert rt.stimulate(leak, {"type": "contact", "state": "closed"}) == []


def test_ambient_readings_reach_matter_attributes_in_their_raw_units():
    from tests.catalog.test_matter_normalize import records as matter_records
    rt, events, (tid,) = runtime(matter_records()["matter:115f:0003"])
    light = rt.add(matter_vds("Light Sensor", ("Illuminance Measurement.MeasuredValue", "numeric", {}))).id
    rt.stimulate(tid, {"type": "ambient", "temperature": 21.5})
    rt.stimulate(light, {"type": "ambient", "illuminance": 300})
    assert rt.state(tid)["Temperature Measurement.MeasuredValue"] == 2150
    assert rt.state(light)["Illuminance Measurement.MeasuredValue"] == 24772  # 10000 log10(lux) + 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_runtime.py -q 2>&1 | tail -1`

Expected: `3 failed, 23 passed`: the three Matter stimulus tests.

- [ ] **Step 3: Implement**

In `vesper/twins/runtime.py`, replace:
```python

import threading
```
with:
```python

import math
import threading
```

In `vesper/twins/runtime.py`, replace:
```python
OCCUPANCY_TIMEOUT_S = 90.0  # assumed: Zigbee2MQTT's common occupancy_timeout default; real devices vary
```
with:
```python
OCCUPANCY_TIMEOUT_S = 90.0  # assumed: Zigbee2MQTT's common occupancy_timeout default; real devices vary
# Matter twins name features by cluster and attribute (spec §3.1); a physical stimulus drives them with Matter's
# own semantics and units.
MATTER_PRESENCE = {"Occupancy Sensing.Occupancy": (1, 0)}  # the Occupied bit: set, clear
MATTER_CONTACT = "Boolean State.StateValue"  # true while a Contact Sensor's contact is closed
MATTER_AMBIENT = {"temperature": ("Temperature Measurement.MeasuredValue", "Thermostat.LocalTemperature"),
                  "humidity": ("Relative Humidity Measurement.MeasuredValue",),
                  "illuminance": ("Illuminance Measurement.MeasuredValue",)}
MATTER_LUX = "Illuminance Measurement.MeasuredValue"  # 10000 * log10(lux) + 1, 0 for darkness
```

In `vesper/twins/runtime.py`, replace:
```python
                binary(name, s.get("present", True))
        elif kind == "contact":
```
with:
```python
                binary(name, s.get("present", True))
            for name, (on, off) in MATTER_PRESENCE.items():
                if name in feats:
                    out.append((name, on if s.get("present", True) else off))
        elif kind == "contact":
```

In `vesper/twins/runtime.py`, replace:
```python
            binary("contact", s["state"] == "opened")  # Zigbee2MQTT's contact is "on" (value_on) when open
        elif kind == "button_press":
```
with:
```python
            binary("contact", s["state"] == "opened")  # Zigbee2MQTT's contact is "on" (value_on) when open
            if _matter_type(twin) == "Contact Sensor" and MATTER_CONTACT in feats:
                out.append((MATTER_CONTACT, s["state"] == "closed"))
        elif kind == "button_press":
```

In `vesper/twins/runtime.py`, replace:
```python
                    out.extend((n, s[key]) for n in names if n in feats and feats[n]["type"] == "numeric")
        else:
```
with:
```python
                    out.extend((n, s[key]) for n in names if n in feats and feats[n]["type"] == "numeric")
            for key, names in MATTER_AMBIENT.items():
                if key in s:
                    out.extend((n, _matter_raw(n, feats[n], s[key])) for n in names
                               if n in feats and feats[n]["type"] == "numeric")
        else:
```

In `vesper/twins/runtime.py`, replace:
```python
                    for name in names:
                        self._change(twin, name, twin.features[name]["value_off"], "stimulus")
        self.clock.call_at(stamp + OCCUPANCY_TIMEOUT_S, self._guarded(twin, clear))
```
with:
```python
                    for name in names:
                        off = MATTER_PRESENCE[name][1] if name in MATTER_PRESENCE else twin.features[name]["value_off"]
                        self._change(twin, name, off, "stimulus")
        self.clock.call_at(stamp + OCCUPANCY_TIMEOUT_S, self._guarded(twin, clear))
```

In `vesper/twins/runtime.py`, replace:
```python
        self.clock.call_at(stamp + OCCUPANCY_TIMEOUT_S, self._guarded(twin, clear))
```
with:
```python
        self.clock.call_at(stamp + OCCUPANCY_TIMEOUT_S, self._guarded(twin, clear))


def _matter_type(twin: Twin) -> str | None:
    return ((twin.vds.get("binding") or {}).get("matter") or {}).get("device_type")


def _matter_raw(name: str, feature: dict, value: float):
    """A reading in the stimulus's unit (°C, %, lx) as the Matter attribute's raw value."""
    if name == MATTER_LUX:
        return round(10000 * math.log10(value) + 1) if value > 0 else 0
    scale = feature.get("scale")
    return round(value / scale) if scale else value
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins/test_runtime.py tests/twins -q 2>&1 | tail -1`

Expected: `50 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/runtime.py tests/twins/test_runtime.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(twins): presence, contact and ambient stimuli for Matter twins

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The Matter tier: node identity, the host process, twin–node sync

**Files:**
- Create: `vesper/exposure/matter_tier.py`
- Create tests: `tests/exposure/fake_matter_host.py` (a Python stand-in that speaks the host's protocol), `tests/exposure/test_matter_tier.py`

**Interfaces:**
- Consumes: `Runtime` (`add`, `subscribe`, `platform_set`, `state`, `twins`) from `vesper/twins/runtime.py`.
- Produces, in `vesper/exposure/matter_tier.py`:
  - `HOST_DIR`, `TEST_VENDOR_ID = 0xFFF1`, `NODE_PORT_BASE = 25540`, `DISCRIMINATOR_BASE = 0x400`, `MAX_NODES`, `COMMISSION_MARGIN_S`;
  - `NodeSpec` and `node_spec(record, twin_id, index, *, vid_mode="product"|"test") -> NodeSpec`;
  - `HostError`; `Host(storage, *, interface=None, command=None, timeout=60.0, log=None, popen=...)` with `start()`, `request(op, wait=True, timeout=None, **args) -> dict`, `stop()`, `on_event(fn)`, `dead`, `notes`;
  - `MatterTier(runtime, host, specs)` with `start()`, `stop()`, `commission(twin_id, url, timeout) -> int`, and `resolved`, `pairing` (twin → `{"manual", "qr"}`), `node_ids`, `failed`, `notes`.
- The protocol (host side in Task 6): a request `{id, op, ...}` gets `{id, ok, ...}` or `{id, ok: false, error}`; events carry no id (`ready`, `changed`). The host gets `MATTER_STORAGE_PATH`, `MATTER_MDNS_NETWORKINTERFACE` and `MATTER_LOG_FORMAT=plain`.

- [ ] **Step 1: Write the failing tests**

Create `tests/exposure/fake_matter_host.py`:
```python
"""A stand-in for vesper/exposure/matter_host/host.mjs that speaks the same JSON-lines protocol, for the Host tests.

Test-only ops: `env` (returns the MATTER_* variables), `sleep`, `fail`, `die` and `emit` (sends an event first).
"""
import json
import os
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


send({"event": "ready", "matter_js": "fake"})
for line in sys.stdin:
    req = json.loads(line)
    op, rid = req.get("op"), req.get("id")
    if op == "add":
        names = [f["name"] for f in req["features"]]
        send({"id": rid, "ok": True, "resolved": names, "unresolved": [],
              "pairing": {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}})
    elif op in ("set", "start", "remove"):
        send({"id": rid, "ok": True})
    elif op == "env":
        send({"id": rid, "ok": True, "env": {k: v for k, v in os.environ.items() if k.startswith("MATTER_")}})
    elif op == "sleep":
        time.sleep(req["seconds"])
        send({"id": rid, "ok": True})
    elif op == "fail":
        send({"id": rid, "ok": False, "error": "boom"})
    elif op == "die":
        sys.exit(3)
    elif op == "emit":
        send(req["event"])
        send({"id": rid, "ok": True})
    elif op == "shutdown":
        send({"id": rid, "ok": True})
        sys.exit(0)
    else:
        send({"id": rid, "ok": False, "error": f"unknown op {op!r}"})
```

Create `tests/exposure/test_matter_tier.py`:
```python
import sys
import threading
from pathlib import Path

import pytest

from tests.catalog.test_matter_normalize import records as matter_records
from vesper.exposure.matter_tier import (COMMISSION_MARGIN_S, NODE_PORT_BASE, TEST_VENDOR_ID, Host, HostError,
                                         MatterTier, node_spec)
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime

FAKE = [sys.executable, "-I", str(Path(__file__).parent / "fake_matter_host.py")]


# -- node identity -----------------------------------------------------------------------------------------------
def test_a_node_carries_the_products_identity_and_a_serial_home_assistant_keeps():
    rec = matter_records()["matter:100b:0016"]
    spec = node_spec(rec, "matter:100b:0016@0", 3)
    assert (spec.vendor_id, spec.product_id) == (0x100B, 0x0016) and spec.device_type == "Contact Sensor"
    assert spec.vendor_name == "Philips Hue" and spec.port == NODE_PORT_BASE + 3
    assert spec.serial.startswith("VSP-") and "test" not in spec.serial.lower() and len(spec.serial) <= 32
    assert node_spec(rec, "matter:100b:0016@0", 3) == spec  # deterministic
    assert node_spec(rec, "matter:100b:0016@1", 4).serial != spec.serial


def test_test_vendor_mode_keeps_the_names_but_uses_the_test_vendor_id():
    spec = node_spec(matter_records()["matter:100b:0016"], "t", 0, vid_mode="test")
    assert spec.vendor_id == TEST_VENDOR_ID and 0x8000 <= spec.product_id <= 0xFFFF
    assert spec.vendor_name == "Philips Hue"


def test_pairing_codes_are_valid_and_distinct_and_long_names_are_cut_to_matters_limit():
    rec = matter_records()["matter:100b:0016"]
    rec = {**rec, "identity": {**rec["identity"], "model": "M" * 60}}
    specs = [node_spec(rec, f"t{i}", i) for i in range(400)]
    invalid = {0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888, 99999999,
               12345678, 87654321}
    assert all(0 < s.passcode < 99999999 and s.passcode not in invalid for s in specs)
    assert len({s.passcode for s in specs}) == 400 and all(0 <= s.discriminator <= 0xFFF for s in specs)
    # distinct long discriminators: commissioning by QR code finds exactly the node it means
    assert len({s.discriminator for s in specs}) == 400
    assert all(len(s.product_name) <= 32 and len(s.label) <= 32 for s in specs)


def test_a_record_without_a_matter_device_type_cannot_be_a_node():
    with pytest.raises(ValueError, match="device type"):
        node_spec(matter_records()["matter:fff1:8000"], "t", 0)


# -- the host process --------------------------------------------------------------------------------------------
@pytest.fixture
def host(tmp_path):
    h = Host(tmp_path / "nodes", interface="en0", command=FAKE, timeout=5.0)
    h.start()
    yield h
    h.stop()


def test_requests_get_their_own_replies_and_an_error_reply_raises(host):
    assert host.request("add", twin="a", features=[{"name": "x", "type": "binary"}], state={})["resolved"] == ["x"]
    with pytest.raises(HostError, match="boom"):
        host.request("fail")


def test_the_host_gets_its_storage_and_interface(host, tmp_path):
    env = host.request("env")["env"]
    # MATTER_LOG_FORMAT: the host's log is read by people, not a terminal, so no colour codes
    assert env == {"MATTER_STORAGE_PATH": str(tmp_path / "nodes"), "MATTER_MDNS_NETWORKINTERFACE": "en0",
                   "MATTER_LOG_FORMAT": "plain"}


def test_a_slow_reply_times_out(tmp_path):
    h = Host(tmp_path, command=FAKE, timeout=0.3)
    h.start()
    try:
        with pytest.raises(HostError, match="timed out"):
            h.request("sleep", seconds=2)
    finally:
        h.stop()


def test_an_event_handler_may_call_the_host_without_deadlock(host):
    done = threading.Event()

    def handler(event):
        host.request("set", twin="a", feature="x", value=True)  # the reader thread must still deliver this reply
        done.set()
    host.on_event(handler)
    host.request("emit", event={"event": "changed", "twin": "a", "feature": "x", "value": True})
    assert done.wait(5)


def test_a_host_that_dies_fails_the_pending_and_every_later_request(host):
    with pytest.raises(HostError, match="exited"):
        host.request("die")
    with pytest.raises(HostError, match="exited"):
        host.request("set", twin="a", feature="x", value=1)


def test_stop_shuts_the_host_down(tmp_path):
    h = Host(tmp_path, command=FAKE, timeout=5.0)
    h.start()
    h.stop()
    assert h.proc is None


# -- the tier ----------------------------------------------------------------------------------------------------
class FakeHost:
    def __init__(self, unresolved=(), refuse=()):
        self.calls, self.handlers, self.unresolved, self.refuse = [], [], set(unresolved), set(refuse)

    def request(self, op, wait=True, **args):
        self.calls.append((op, args))
        if op == "add" and args["device_type"] in self.refuse:
            raise HostError(f"add: no recipe for device type {args['device_type']!r}")
        if op == "add":
            names = [f["name"] for f in args["features"]]
            return {"resolved": [n for n in names if n not in self.unresolved],
                    "unresolved": [n for n in names if n in self.unresolved],
                    "pairing": {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}}
        if op == "commission":
            return {"node_id": 7}
        return {}

    def on_event(self, fn):
        self.handlers.append(fn)

    def emit(self, **event):
        for fn in self.handlers:
            fn({"event": "changed", **event})

    def sets(self):
        return [(a["twin"], a["feature"], a["value"]) for op, a in self.calls if op == "set"]


def tier(*rids, unresolved=(), refuse=()):
    rt = Runtime(clock=LogicalClock())
    recs = matter_records()
    specs = {}
    for i, rid in enumerate(rids):
        tid = rt.add(recs[rid], twin_id=f"{rid}@{i}").id
        specs[tid] = node_spec(recs[rid], tid, i)
    host = FakeHost(unresolved, refuse)
    t = MatterTier(rt, host, specs)
    t.start()
    return t, rt, host, list(specs)


def test_start_adds_every_twin_with_its_features_and_state_and_notes_what_did_not_resolve():
    t, rt, host, (lamp,) = tier("matter:100b:0018", unresolved={"Color Control.ColorMode"})
    (op, args), = host.calls
    assert op == "add" and args["twin"] == lamp and args["device_type"] == "Color Temperature Light"
    assert {f["name"] for f in args["features"]} == set(rt.twins[lamp].features)
    assert args["state"]["On/Off.OnOff"] is False and args["serial"].startswith("VSP-")
    # a descriptive attribute keeps the node's own value: the twin's default is only the first enum value
    assert "Color Control.ColorMode" not in args["state"] and "Color Control.ColorMode" in {
        f["name"] for f in args["features"]}
    assert t.pairing[lamp] == {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}
    assert t.notes == [f"{lamp}: Color Control.ColorMode has no matter.js attribute; it is not exposed"]


def test_a_node_is_commissioned_by_its_qr_code_and_its_node_id_kept():
    t, rt, host, (door,) = tier("matter:100b:0016")
    assert t.commission(door, "ws://127.0.0.1:15580/ws", timeout=90) == 7 and t.node_ids == {door: 7}
    # the host gives the Matter Server `seconds`; VESPER waits a little longer for the host's own answer
    assert host.calls[-1] == ("commission", {"url": "ws://127.0.0.1:15580/ws", "code": "MT:Y.K90AFN00KA0648G00",
                                             "seconds": 90, "timeout": 90 + COMMISSION_MARGIN_S})


def test_a_twin_the_host_cannot_add_fails_alone():
    t, rt, host, (door, lamp) = tier("matter:100b:0016", "matter:100b:0018", refuse={"Contact Sensor"})
    assert set(t.resolved) == {lamp} and t.failed == {door: "add: no recipe for device type 'Contact Sensor'"}
    with pytest.raises(HostError, match="no node.*no recipe"):
        t.commission(door, "ws://127.0.0.1:15580/ws", timeout=5)
    rt.stimulate(door, {"type": "contact", "state": "closed"})  # its reports go nowhere, quietly
    assert host.sets() == []
    t.stop()
    assert [a["twin"] for op, a in host.calls if op == "remove"] == [lamp]


def test_a_dead_host_still_ends_the_run():
    class Dead(FakeHost):
        dead = "the Matter host exited (1)"

        def request(self, op, wait=True, **args):
            raise HostError(self.dead)

    rt = Runtime(clock=LogicalClock())
    rec = matter_records()["matter:100b:0016"]
    tid = rt.add(rec).id
    with pytest.raises(HostError, match="exited"):
        MatterTier(rt, Dead(), {tid: node_spec(rec, tid, 0)}).start()


def test_the_host_says_whether_it_died(host):
    assert host.dead is None
    with pytest.raises(HostError):
        host.request("die")
    assert "exited" in host.dead


def test_a_reported_value_reaches_the_node():
    t, rt, host, (door,) = tier("matter:100b:0016")
    rt.stimulate(door, {"type": "contact", "state": "closed"})
    assert host.sets() == [(door, "Boolean State.StateValue", True)]


def test_a_controllers_change_becomes_a_platform_command_and_its_report_follows():
    t, rt, host, (lamp,) = tier("matter:100b:0018")
    host.emit(twin=lamp, feature="On/Off.OnOff", value=True)
    assert rt.state(lamp)["On/Off.OnOff"] is True and rt.actions[-1].actor == "platform"
    assert host.sets() == [(lamp, "On/Off.OnOff", True)]


def test_a_change_the_device_would_refuse_is_rejected_and_the_node_put_back():
    t, rt, host, (door,) = tier("matter:100b:0016")
    host.emit(twin=door, feature="Boolean State.StateValue", value=True)  # read-only on a real contact sensor
    assert rt.state(door)["Boolean State.StateValue"] is False and rt.actions[-1].accepted is False
    assert host.sets() == [(door, "Boolean State.StateValue", False)]


def test_an_event_for_an_unknown_twin_is_noted_not_fatal():
    t, rt, host, _ = tier("matter:100b:0016")
    host.emit(twin="nobody", feature="On/Off.OnOff", value=True)
    assert t.notes == ["change for unknown twin 'nobody' ignored"]


def test_stop_removes_every_node():
    t, rt, host, (door, lamp) = tier("matter:100b:0016", "matter:100b:0018")
    t.stop()
    assert [a["twin"] for op, a in host.calls if op == "remove"] == [door, lamp]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/exposure/test_matter_tier.py -q 2>&1 | tail -1`

Expected: `1 error`: collection stops with `ModuleNotFoundError: No module named 'vesper.exposure.matter_tier'`.

- [ ] **Step 3: Implement**

Create `vesper/exposure/matter_tier.py`:
```python
"""Matter tier (spec §2): every Matter twin is a real matter.js node, kept in step with the twin runtime.

One Node.js process, `vesper/exposure/matter_host/host.mjs`, hosts all nodes and speaks JSON lines on its
stdin and stdout. A request carries an `id` and gets a reply with the same `id`, `ok` and either its data or
an `error`. An event carries no id: `changed` reports an attribute that a controller (Home Assistant's Matter
Server) changed on a node. Values on the wire are VDS values: booleans, raw numbers, enum names and bitmap
integers. The host maps them to matter.js and back, so no matter.js data model reaches VESPER's records.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

HOST_DIR = Path(__file__).parent / "matter_host"
TEST_VENDOR_ID = 0xFFF1  # the Matter test vendor, for a commissioner that refuses test attestation under a real VID
NODE_PORT_BASE = 25540  # VESPER's nodes listen on UDP 25540 + i; Matter's default 5540 stays free for others
DISCRIMINATOR_BASE, MAX_NODES = 0x400, 0xC00  # node i uses discriminator 0x400 + i: distinct 12-bit values
COMMISSION_MARGIN_S = 15.0  # how much longer VESPER waits than the host gives the Matter Server
_INVALID_PASSCODES = {0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888,
                      99999999, 12345678, 87654321}  # forbidden setup codes (Matter Core §5.1.7.1)


class HostError(RuntimeError):
    """The Matter host failed, refused a request or did not answer in time."""


@dataclass(frozen=True)
class NodeSpec:
    twin_id: str
    device_type: str  # the ZAP device-type name, e.g. "Extended Color Light"
    vendor_id: int
    product_id: int
    vendor_name: str
    product_name: str
    serial: str  # Home Assistant drops a serial number containing "test"
    label: str  # the node label, which Home Assistant shows as the device name
    port: int
    passcode: int
    discriminator: int


def node_spec(record: dict, twin_id: str, index: int, *, vid_mode: str = "product") -> NodeSpec:
    """The identity a twin presents as a Matter node: the product's vendor and product ids and names, or the
    test vendor id when the commissioner refuses test attestation under a real one (`vid_mode="test"`)."""
    device_type = ((record.get("binding") or {}).get("matter") or {}).get("device_type")
    if not device_type:
        raise ValueError(f"{record['id']} has no Matter device type")
    vid, pid = record["identity"]["ids"]["matter_vid_pid"][0]
    if vid_mode == "test":
        vid, pid = TEST_VENDOR_ID, 0x8000 + index % 0x8000
    elif vid_mode != "product":
        raise ValueError(f"vid_mode is 'product' or 'test', got {vid_mode!r}")
    passcode = 20000001 + index
    if passcode in _INVALID_PASSCODES or not 0 <= index < MAX_NODES:
        raise ValueError(f"no valid setup code for node {index}")
    digest = hashlib.sha256(twin_id.encode()).hexdigest()
    model = record["identity"]["model"]
    return NodeSpec(twin_id, device_type, vid, pid, record["identity"]["vendor"][:32], model[:32],
                    "VSP-" + digest[:16].upper(), f"VESPER {index:04d} {model}"[:32], NODE_PORT_BASE + index,
                    passcode, DISCRIMINATOR_BASE + index)


@dataclass
class _Slot:
    done: threading.Event
    reply: dict | None = None


class Host:
    """The Node.js process that hosts the Matter nodes."""

    def __init__(self, storage: Path, *, interface: str | None = None, command: list[str] | None = None,
                 timeout: float = 60.0, log: Path | None = None, popen: Callable = subprocess.Popen):
        self.storage, self.interface, self.timeout, self.log, self.popen = Path(storage), interface, timeout, log, popen
        self.command = command or ["node", str(HOST_DIR / "host.mjs")]
        self.proc = None
        self.notes: list[str] = []
        self._handlers: list[Callable[[dict], None]] = []
        self._pending: dict[int, _Slot] = {}
        self._lock = threading.Lock()
        self._next = 0
        self._dead: str | None = None
        self._ready = threading.Event()
        self._events: queue.Queue = queue.Queue()
        self._log_file = None

    @property
    def dead(self) -> str | None:
        """Why the host process is gone, or None while it runs."""
        return self._dead

    def on_event(self, fn: Callable[[dict], None]) -> None:
        self._handlers.append(fn)

    def start(self) -> None:
        self.storage.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "MATTER_STORAGE_PATH": str(self.storage), "MATTER_LOG_FORMAT": "plain"}
        if self.interface:
            env["MATTER_MDNS_NETWORKINTERFACE"] = self.interface
        self._log_file = open(self.log, "a") if self.log else None
        self.proc = self.popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=self._log_file or subprocess.DEVNULL, text=True, bufsize=1, env=env)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._dispatch, daemon=True).start()
        if not self._ready.wait(self.timeout):
            self.stop()
            raise HostError(self._dead or f"the Matter host did not start within {self.timeout:.0f} s")

    def request(self, op: str, wait: bool = True, timeout: float | None = None, **args) -> dict:
        if self._dead:
            raise HostError(self._dead)
        with self._lock:
            self._next += 1
            rid, slot = self._next, _Slot(threading.Event())
            if wait:
                self._pending[rid] = slot
            try:
                self.proc.stdin.write(json.dumps({"id": rid, "op": op, **args}) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                self._pending.pop(rid, None)
                raise HostError(self._dead or f"cannot write to the Matter host: {e}") from e
        if not wait:
            return {}
        if not slot.done.wait(self.timeout if timeout is None else timeout):
            self._pending.pop(rid, None)
            raise HostError(f"the Matter host timed out on {op!r}")
        reply = slot.reply or {}
        if not reply.get("ok"):
            raise HostError(f"{op}: {reply.get('error') or self._dead or 'no reply'}")
        return reply

    def stop(self) -> None:
        if self.proc is None:
            return
        if not self._dead:
            try:
                self.request("shutdown", timeout=10)
            except HostError:
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                self.notes.append(f"unparseable line from the Matter host: {line[:200]!r}")
                continue
            if "id" in msg:
                slot = self._pending.pop(msg["id"], None)
                if slot is not None:
                    slot.reply = msg
                    slot.done.set()
                elif not msg.get("ok"):
                    self.notes.append(f"the Matter host refused request {msg['id']}: {msg.get('error')}")
            elif msg.get("event") == "ready":
                self._ready.set()
            else:
                self._events.put(msg)
        self._dead = f"the Matter host exited ({self.proc.wait()})"
        for slot in list(self._pending.values()):
            slot.done.set()
        self._pending.clear()
        self._ready.set()
        self._events.put(None)

    def _dispatch(self) -> None:
        """Events run here, not on the reader thread, so a handler may itself call the host."""
        while (msg := self._events.get()) is not None:
            for fn in self._handlers:
                try:
                    fn(msg)
                except Exception as e:  # a failing handler must not stop the tier
                    self.notes.append(f"event handler failed on {msg!r}: {e!r}")


class MatterTier:
    """Keeps matter.js nodes and twins in step. A reported value goes to the twin's node; a controller's change
    comes back as a platform command, and where the twin rejects it the node is put back."""

    def __init__(self, runtime, host, specs: dict[str, NodeSpec]):
        self.runtime, self.host, self.specs = runtime, host, specs
        self.resolved: dict[str, set[str]] = {}
        self.pairing: dict[str, dict] = {}  # twin -> {"manual": 11-digit code, "qr": "MT:..."}
        self.node_ids: dict[str, int] = {}
        self.failed: dict[str, str] = {}  # twins the host could not add, with its reason
        self.notes: list[str] = []

    def start(self) -> None:
        self.host.on_event(self._on_event)
        for tid, spec in self.specs.items():
            twin = self.runtime.twins[tid]
            names = [n for n, f in twin.features.items() if f["type"] != "unsupported"]
            fields = {k: v for k, v in asdict(spec).items() if k != "twin_id"}
            try:
                reply = self.host.request("add", twin=tid, **fields,
                                          features=[{"name": n, "type": twin.features[n]["type"]} for n in names],
                                          state={n: twin.state[n] for n in names
                                                 if twin.features[n].get("category") != "diagnostic"})
            except HostError as e:  # this product fails; the others go on
                if getattr(self.host, "dead", None):  # the host itself is gone: nothing more can be added
                    raise
                self.failed[tid] = str(e)
                self.notes.append(f"{tid}: not added: {e}")
                continue
            self.resolved[tid] = set(reply["resolved"])
            self.pairing[tid] = reply["pairing"]
            self.notes += [f"{tid}: {n} has no matter.js attribute; it is not exposed" for n in reply["unresolved"]]
        self.runtime.subscribe(self._on_report)

    def commission(self, twin_id: str, url: str, timeout: float) -> int:
        """Have the Matter Server at `url` commission the twin's node, by its QR code: the code carries the
        full 12-bit discriminator, so only this node answers. Returns the node id the server gave it."""
        if twin_id in self.failed:
            raise HostError(f"{twin_id} has no node: {self.failed[twin_id]}")
        reply = self.host.request("commission", timeout=timeout + COMMISSION_MARGIN_S, url=url,
                                  code=self.pairing[twin_id]["qr"], seconds=timeout)
        self.node_ids[twin_id] = int(reply["node_id"])
        return self.node_ids[twin_id]

    def stop(self) -> None:
        for tid in self.resolved:
            try:
                self.host.request("remove", twin=tid)
            except HostError as e:
                self.notes.append(f"{tid}: {e}")

    def _on_report(self, event) -> None:
        if event.feature in self.resolved.get(event.twin_id, ()):
            self.host.request("set", wait=False, twin=event.twin_id, feature=event.feature, value=event.value)

    def _on_event(self, event: dict) -> None:
        if event.get("event") != "changed":
            return
        tid, feature = event.get("twin"), event.get("feature")
        if tid not in self.resolved or feature not in self.runtime.twins[tid].features:
            self.notes.append(f"change for unknown twin {tid!r} ignored" if tid not in self.resolved
                              else f"{tid}: change of unknown feature {feature!r} ignored")
            return
        if not self.runtime.platform_set(tid, feature, event.get("value")):
            self.host.request("set", wait=False, twin=tid, feature=feature, value=self.runtime.state(tid)[feature])
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/exposure/test_matter_tier.py -q 2>&1 | tail -1`

Expected: `20 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/exposure/matter_tier.py tests/exposure/fake_matter_host.py tests/exposure/test_matter_tier.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(exposure): Matter tier: node identity, the host process and twin-node sync

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: VESPER's Matter Server, `vesper setup matter`, and Home Assistant's Matter integration

**Files:**
- Modify: `vesper/validation/stack.py` (`NEEDED_INTEGRATIONS` gains `matter`; `MATTER_SERVER_PORT`, `NODE_MIN`; `Paths.matter_host`, `.matter_server_js`, `.matter_runs`; `setup_matter`; `MatterServer`; `HomeAssistant.ensure_matter`, `.matter_entities`, `._remove_entries`), `vesper/cli.py` (`vesper setup matter`)
- Modify tests: `tests/validation/test_stack.py` (the fake Home Assistant gains stable entry ids, per-domain listing, the Matter flow and `/api/template`), `tests/validation/test_cli.py`

**Interfaces:**
- Consumes: `HOST_DIR` from `vesper/exposure/matter_tier.py` (Task 3).
- Produces, in `vesper/validation/stack.py`:
  - `setup_matter(root, *, run, which, source=None) -> dict` (the pinned dependencies);
  - `MatterServer(root, storage, port=15580, interface=None, popen=..., start_timeout=60.0)` with `url`, `command()`, `start()`, `stop()`;
  - `HomeAssistant.ensure_matter(url)` and `HomeAssistant.matter_entities() -> dict[serial, list[entity_id]]`.
- `vesper setup matter [--root .vesper]` prints the Node packages it installed.

- [ ] **Step 1: Write the failing tests**

In `tests/validation/test_stack.py`, replace:
```python

from vesper.validation.stack import (HA_VERSION, RECOVERY_MARK, HomeAssistant, Mosquitto, Paths, StackError,
                                     port_free, setup_ha)
```
with:
```python

from vesper.validation.stack import (HA_VERSION, MATTER_SERVER_PORT, RECOVERY_MARK, HomeAssistant, MatterServer,
                                     Mosquitto, Paths, StackError, port_free, setup_ha, setup_matter)
```

In `tests/validation/test_stack.py`, replace:
```python
    assert calls[1][-1] == f"homeassistant=={HA_VERSION}" and calls[3][-2:] == reqs
    assert calls[2][3:] == ["frontend", "mqtt", "hassio"]
```
with:
```python
    assert calls[1][-1] == f"homeassistant=={HA_VERSION}" and calls[3][-2:] == reqs
    assert calls[2][3:] == ["frontend", "mqtt", "hassio", "matter"]
```

In `tests/validation/test_stack.py`, replace:
```python
    def __init__(self, entries=None, flow_step="broker", flow_result="create_entry", onboarded=False, storage=None,
                 persists=True):
        self.entries, self.flow_step, self.flow_result, self.requests = entries or [], flow_step, flow_result, []
```
with:
```python
    def __init__(self, entries=None, flow_step="broker", flow_result="create_entry", onboarded=False, storage=None,
                 persists=True, matter_reachable=True, template=(200, "[]")):
        self.entries, self.flow_step, self.flow_result, self.requests = entries or [], flow_step, flow_result, []
```

In `tests/validation/test_stack.py`, replace:
```python
        self.onboarded, self.storage, self.persists = onboarded, storage, persists
```
with:
```python
        self.onboarded, self.storage, self.persists = onboarded, storage, persists
        self.ids = [f"e{i}" for i in range(len(self.entries))]  # entry ids stay put when another entry goes
        self.next_id = len(self.entries)
        self.matter_reachable, self.template, self.templates, self.handler = matter_reachable, template, [], None

    def _add(self, entry):
        self.entries.append(entry)
        self.ids.append(f"e{self.next_id}")
        self.next_id += 1
```

In `tests/validation/test_stack.py`, replace:
```python
        if path == "/api/config/config_entries/entry":
            return httpx.Response(200, json=[{"entry_id": f"e{i}", "domain": e["domain"], "title": "MQTT"}
                                             for i, e in enumerate(self.entries)])
        if path.startswith("/api/config/config_entries/entry/") and request.method == "DELETE":
```
with:
```python
        if path == "/api/config/config_entries/entry":
            domain = request.url.params.get("domain")
            return httpx.Response(200, json=[{"entry_id": i, "domain": e["domain"], "title": e["domain"]}
                                             for i, e in zip(self.ids, self.entries)
                                             if domain in (None, e["domain"])])
        if path.startswith("/api/config/config_entries/entry/") and request.method == "DELETE":
```

In `tests/validation/test_stack.py`, replace:
```python
        if path.startswith("/api/config/config_entries/entry/") and request.method == "DELETE":
            del self.entries[int(path.rsplit("/e", 1)[1])]
            return httpx.Response(200, json={"require_restart": False})
```
with:
```python
        if path.startswith("/api/config/config_entries/entry/") and request.method == "DELETE":
            k = self.ids.index(path.rsplit("/", 1)[1])
            del self.entries[k], self.ids[k]
            return httpx.Response(200, json={"require_restart": False})
```

In `tests/validation/test_stack.py`, replace:
```python
            return httpx.Response(200, json={"require_restart": False})
        if path == "/api/states/sensor.broken":
```
with:
```python
            return httpx.Response(200, json={"require_restart": False})
        if path == "/api/template":
            self.templates.append(json.loads(request.content)["template"])
            status, text = self.template
            return httpx.Response(status, text=text)
        if path == "/api/states/sensor.broken":
```

In `tests/validation/test_stack.py`, replace:
```python
        if path == "/api/config/config_entries/flow":
            return httpx.Response(200, json={"flow_id": "f1", "type": "form", "step_id": self.flow_step})
        if path == "/api/config/config_entries/flow/f1":
```
with:
```python
        if path == "/api/config/config_entries/flow":
            self.handler = json.loads(request.content)["handler"]
            # Without a Supervisor, Home Assistant's Matter flow goes straight to its manual step
            # (matter/config_flow.py)
            step = "manual" if self.handler == "matter" else self.flow_step
            return httpx.Response(200, json={"flow_id": "f1", "type": "form", "step_id": step})
        if path == "/api/config/config_entries/flow/f1":
```

In `tests/validation/test_stack.py`, replace:
```python
            body = json.loads(request.content)
            if "other_settings" not in body:  # Home Assistant 2026.10's broker step requires this section
```
with:
```python
            body = json.loads(request.content)
            if self.handler == "matter":
                if set(body) != {"url"}:
                    return httpx.Response(400, json={"message": "extra keys not allowed"})
                if not self.matter_reachable:
                    return httpx.Response(200, json={"type": "form", "step_id": "manual",
                                                     "errors": {"base": "cannot_connect"}})
                self._add({"domain": "matter", "data": body})
                return httpx.Response(200, json={"type": "create_entry"})
            if "other_settings" not in body:  # Home Assistant 2026.10's broker step requires this section
```

In `tests/validation/test_stack.py`, replace:
```python
                                                 "errors": {"other_settings": "required key not provided"}})
            self.entries.append({"domain": "mqtt", "data": body})
            return httpx.Response(200, json={"type": self.flow_result})
```
with:
```python
                                                 "errors": {"other_settings": "required key not provided"}})
            self._add({"domain": "mqtt", "data": body})
            return httpx.Response(200, json={"type": self.flow_result})
```

In `tests/validation/test_stack.py`, replace:
```python
    assert not Paths(tmp_path).auth_file.exists()
```
with:
```python
    assert not Paths(tmp_path).auth_file.exists()


def matter_sources(tmp_path, lock=True):
    src = tmp_path / "src"
    src.mkdir()
    (src / "package.json").write_text(json.dumps({"dependencies": {"@matter/main": "0.17.9",
                                                                    "matter-server": "1.4.0"}}))
    if lock:
        (src / "package-lock.json").write_text("{}")
    (src / "host.mjs").write_text("// host")
    (src / "host.test.mjs").write_text("// tests stay with the sources")
    return src


def node_runner(version="v22.13.0"):  # exactly the floor
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("cwd")))
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=version + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    return run, calls


def test_setup_matter_installs_the_pinned_node_packages_beside_a_copy_of_the_host(tmp_path):
    run, calls = node_runner()
    pins = setup_matter(tmp_path, run=run, which=lambda n: f"/bin/{n}", source=matter_sources(tmp_path))
    host = Paths(tmp_path).matter_host
    assert pins == {"@matter/main": "0.17.9", "matter-server": "1.4.0"}
    assert sorted(p.name for p in host.iterdir()) == ["host.mjs", "package-lock.json", "package.json"]
    # no install scripts: no package code runs at install (only optional native Bluetooth has any)
    assert (["/bin/npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], host) in calls


def test_setup_matter_needs_node_and_npm(tmp_path):
    run, _ = node_runner()
    with pytest.raises(StackError, match="needs Node.js"):
        setup_matter(tmp_path, run=run, which=lambda n: None if n == "npm" else f"/bin/{n}",
                     source=matter_sources(tmp_path))


def test_setup_matter_refuses_an_old_node(tmp_path):
    run, _ = node_runner("v22.12.0")
    with pytest.raises(StackError, match="Node.js 22.13 or newer"):
        setup_matter(tmp_path, run=run, which=lambda n: f"/bin/{n}", source=matter_sources(tmp_path))


def test_setup_matter_refuses_unpinned_node_packages(tmp_path):
    run, calls = node_runner()
    with pytest.raises(StackError, match="package-lock.json"):
        setup_matter(tmp_path, run=run, which=lambda n: f"/bin/{n}", source=matter_sources(tmp_path, lock=False))
    assert not any(cmd[1:2] == ["ci"] for cmd, _ in calls)


def installed_matter_server(tmp_path):
    binary = Paths(tmp_path).matter_server_js
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    return binary


def test_the_matter_server_serves_home_assistant_on_loopback_from_vespers_storage(tmp_path, monkeypatch):
    import vesper.validation.stack as stack

    binary, seen = installed_matter_server(tmp_path), {}
    monkeypatch.setattr(stack, "wait_for_port", lambda port, timeout, host="127.0.0.1": seen.setdefault("port", port))

    def popen(cmd, stdout, stderr):
        seen["cmd"] = cmd
        return FakeProc()

    port = free_port()
    server = MatterServer(tmp_path, tmp_path / "run" / "server", port=port, interface="en0", popen=popen)
    server.start()
    cmd = seen["cmd"]
    assert cmd[:2] == ["node", str(binary)] and seen["port"] == port and server.url == f"ws://127.0.0.1:{port}/ws"
    assert binary.parts[-4:] == ("matter-server", "dist", "esm", "MatterServer.js")  # the package has no bin
    for flag, value in (("--storage-path", str(tmp_path / "run" / "server")), ("--port", str(port)),
                        ("--listen-address", "127.0.0.1"), ("--primary-interface", "en0")):
        assert cmd[cmd.index(flag) + 1] == value
    assert {"--disable-dashboard", "--disable-ota", "--disable-thread-diagnostics"} <= set(cmd)
    # matter.js nodes carry test attestation certificates, which the server refuses without the test DCL
    assert "--enable-test-net-dcl" in cmd
    assert (tmp_path / "run" / "server").is_dir() and (tmp_path / "run" / "matter-server.log").exists()
    assert MATTER_SERVER_PORT != 5580  # the default Matter Server port stays free for a household's own


def test_the_matter_server_needs_setup(tmp_path):
    with pytest.raises(StackError, match="vesper setup matter"):
        MatterServer(tmp_path, tmp_path / "server", port=free_port()).start()


def test_a_matter_server_that_never_listens_is_stopped(tmp_path, monkeypatch):
    import vesper.validation.stack as stack

    installed_matter_server(tmp_path)
    proc = FakeProc()

    def never(port, timeout, host="127.0.0.1"):
        raise StackError("nothing listens")

    monkeypatch.setattr(stack, "wait_for_port", never)
    with pytest.raises(StackError):
        MatterServer(tmp_path, tmp_path / "server", port=free_port(), popen=lambda cmd, stdout, stderr: proc).start()
    assert proc.terminated


def test_a_busy_matter_server_port_is_refused(tmp_path):
    installed_matter_server(tmp_path)
    s, port = busy_port()
    with s, pytest.raises(StackError, match="already in use"):
        MatterServer(tmp_path, tmp_path / "server", port=port).start()


def test_each_run_recreates_the_matter_entry_and_leaves_mqtt_alone(tmp_path):
    server = FakeServer(entries=[{"domain": "matter", "data": {"url": "ws://localhost:5580/ws"}},
                                 {"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}}])
    ha = onboarded(tmp_path, server)
    ha.ensure_matter("ws://127.0.0.1:15580/ws")
    assert server.entries == [{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}},
                              {"domain": "matter", "data": {"url": "ws://127.0.0.1:15580/ws"}}]
    ha.ensure_mqtt(18831)
    assert [e["domain"] for e in server.entries] == ["matter", "mqtt"]


def test_a_matter_server_home_assistant_cannot_reach_fails_the_run(tmp_path):
    ha = onboarded(tmp_path, FakeServer(matter_reachable=False))
    with pytest.raises(StackError, match="Matter integration was not configured.*cannot_connect"):
        ha.ensure_matter("ws://127.0.0.1:15580/ws")


def test_matter_entities_are_found_by_the_twins_serial_number(tmp_path):
    rows = [["light.vesper_0001", "VSP-0A1B"], ["sensor.vesper_0001_power", "VSP-0A1B"],
            ["binary_sensor.vesper_0002", "VSP-FF00"], ["sensor.other", None]]
    server = FakeServer(template=(200, json.dumps(rows)))
    ha = onboarded(tmp_path, server)
    assert ha.matter_entities() == {"VSP-0A1B": ["light.vesper_0001", "sensor.vesper_0001_power"],
                                    "VSP-FF00": ["binary_sensor.vesper_0002"]}
    assert "integration_entities('matter')" in server.templates[0] and "serial_number" in server.templates[0]


def test_a_template_error_is_not_an_empty_home(tmp_path):
    ha = onboarded(tmp_path, FakeServer(template=(400, "Error rendering template")))
    with pytest.raises(StackError, match="400"):
        ha.matter_entities()
```

In `tests/validation/test_cli.py`, replace:
```python
    assert "FAILED setup: uv not found" in capsys.readouterr().err
```
with:
```python
    assert "FAILED setup: uv not found" in capsys.readouterr().err


def test_setup_matter_reports_the_pinned_node_packages(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(stack_mod, "setup_matter", lambda root: {"@matter/main": "0.17.9", "matter-server": "1.4.0"})
    assert cli.main(["setup", "matter", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "@matter/main 0.17.9" in out and "matter-server 1.4.0" in out and str(tmp_path / "matter" / "host") in out


def test_setup_matter_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    def fail(root):
        raise stack_mod.StackError("needs Node.js")

    monkeypatch.setattr(stack_mod, "setup_matter", fail)
    assert cli.main(["setup", "matter", "--root", str(tmp_path)]) == 1
    assert "FAILED setup: needs Node.js" in capsys.readouterr().err
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_stack.py tests/validation/test_cli.py -q 2>&1 | tail -1`

Expected: `1 error`: collection stops with `ImportError: cannot import name 'MATTER_SERVER_PORT' from 'vesper.validation.stack'`.

- [ ] **Step 3: Implement**

In `vesper/validation/stack.py`, replace:
```python
"""VESPER's own Mosquitto and Home Assistant Core, for acceptance tests (spec §4 L2; §5 `[ha]`).
```
with:
```python
"""VESPER's own Mosquitto, Matter Server and Home Assistant Core, for acceptance tests (spec §4 L2; §5 `[ha]`).
```

In `vesper/validation/stack.py`, replace:
```python

Only VESPER's instances are used, on loopback ports that differ from the defaults (MQTT 18830, HA 18123),
so a machine already running Mosquitto or Home Assistant is never touched. Home Assistant runs from its
own Python 3.14 venv with `--skip-pip`: `setup_ha` installs the pinned release and exactly the
requirements of the integrations VESPER needs. If Home Assistant falls into recovery mode, which would
make it bind the default port 8123, it is stopped and the run fails.
"""
```
with:
```python

Only VESPER's instances are used, on loopback ports that differ from the defaults (MQTT 18830, HA 18123,
Matter Server 15580), so a machine already running Mosquitto, a Matter Server or Home Assistant is never
touched. Home Assistant runs from its own Python 3.14 venv with `--skip-pip`: `setup_ha` installs the
pinned release and exactly the requirements of the integrations VESPER needs. If Home Assistant falls into
recovery mode, which would make it bind the default port 8123, it is stopped and the run fails.
"""
```

In `vesper/validation/stack.py`, replace:
```python
HA_PYTHON = "3.14"
NEEDED_INTEGRATIONS = ("frontend", "mqtt", "hassio")  # bootstrap needs frontend; mqtt's flow imports hassio
MQTT_PORT, HA_PORT = 18830, 18123
RECOVERY_MARK = "Activating recovery mode"
```
with:
```python
HA_PYTHON = "3.14"
# bootstrap needs frontend; mqtt's flow imports hassio; matter talks to VESPER's Matter Server
NEEDED_INTEGRATIONS = ("frontend", "mqtt", "hassio", "matter")
MQTT_PORT, HA_PORT, MATTER_SERVER_PORT = 18830, 18123, 15580
NODE_MIN = (22, 13)  # the pinned matter-server needs Node 22.13 (spec §3, amended 2026-10-23)
RECOVERY_MARK = "Activating recovery mode"
```

In `vesper/validation/stack.py`, replace:
```python
print(json.dumps(sorted(reqs)))
"""
```
with:
```python
print(json.dumps(sorted(reqs)))
"""


# Rendered by Home Assistant's /api/template: [entity_id, the serial number of its device] for every Matter entity
_MATTER_ENTITIES = """{%- set ns = namespace(rows=[]) -%}
{%- for e in integration_entities('matter') -%}
{%- set ns.rows = ns.rows + [[e, device_attr(device_id(e), 'serial_number')]] -%}
{%- endfor -%}
{{ ns.rows | to_json }}"""
```

In `vesper/validation/stack.py`, replace:
```python
        return self.root / "mqtt" / "mosquitto.conf"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
```
with:
```python
        return self.root / "mqtt" / "mosquitto.conf"

    @property
    def matter_host(self) -> Path:
        """The installed copy of the Matter host and its pinned Node packages (`vesper setup matter`)."""
        return self.root / "matter" / "host"

    @property
    def matter_server_js(self) -> Path:
        """The Matter Server's entry point; the npm package declares no executable, so Node runs this file."""
        return self.matter_host / "node_modules" / "matter-server" / "dist" / "esm" / "MatterServer.js"

    @property
    def matter_runs(self) -> Path:
        """One directory per Matter run: the server's fabric, the nodes' storage and both logs."""
        return self.root / "matter" / "runs"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
```

In `vesper/validation/stack.py`, replace:
```python

def mosquitto_binary(which=shutil.which) -> str:
```
with:
```python

def setup_matter(root: Path, *, run=subprocess.run, which=shutil.which, source: Path | None = None) -> dict:
    """Copy the Matter host into VESPER's root and install its Node packages exactly as locked.

    The host runs from the copy, not from the installed Python package, so `npm ci` never writes into
    site-packages. Returns the pinned dependencies from package.json.
    """
    from vesper.exposure.matter_tier import HOST_DIR
    source = Path(source or HOST_DIR)
    node, npm = which("node"), which("npm")
    if not node or not npm:
        raise StackError("setting up the Matter tier needs Node.js and npm on PATH (https://nodejs.org/)")
    if not (source / "package-lock.json").exists():
        raise StackError(f"{source} has no package-lock.json; the Node packages would not be pinned")
    try:
        version = run([node, "--version"], check=True, capture_output=True, text=True).stdout.strip()
        major, minor = (int(x) for x in version.lstrip("v").split(".")[:2])
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        raise StackError(f"cannot read the Node.js version: {e}") from e
    if (major, minor) < NODE_MIN:
        raise StackError(f"the Matter tier needs Node.js {NODE_MIN[0]}.{NODE_MIN[1]} or newer, found {version}")
    host = Paths(Path(root)).matter_host
    host.mkdir(parents=True, exist_ok=True)
    for f in source.iterdir():
        if f.is_file() and (f.name.startswith("package") or f.suffix == ".mjs") and ".test." not in f.name:
            shutil.copy2(f, host / f.name)
    try:
        run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], check=True, cwd=host)
    except (subprocess.CalledProcessError, OSError) as e:
        raise StackError(f"installing the Matter host's Node packages failed: {e}") from e
    return json.loads((host / "package.json").read_text()).get("dependencies", {})


def mosquitto_binary(which=shutil.which) -> str:
```

In `vesper/validation/stack.py`, replace:
```python
            wait_for_port(self.port, 10)
        except StackError:
```
with:
```python
            wait_for_port(self.port, 10)
        except StackError:
            self.stop()
            raise

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None


@dataclass
class MatterServer:
    """VESPER's own Matter Server (the matter.js `matter-server`), on loopback for Home Assistant's WebSocket.

    `storage` holds its fabric. Each run gets a new directory, so no node from an earlier run is remembered;
    the log goes beside it. `interface` is the network interface its Matter traffic and mDNS use.
    """
    root: Path
    storage: Path
    port: int = MATTER_SERVER_PORT
    interface: str | None = None
    popen: object = subprocess.Popen
    start_timeout: float = 60.0
    proc: object = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def command(self) -> list[str]:
        # --enable-test-net-dcl: matter.js nodes carry test attestation certificates (a test PAA signs a PAI and
        # DAC for the product's own vendor and product id), which the server refuses without the test DCL.
        # --disable-thread-diagnostics: no mDNS probing of the lab's Thread Border Routers.
        cmd = ["node", str(Paths(Path(self.root)).matter_server_js), "--storage-path", str(self.storage),
               "--port", str(self.port), "--listen-address", "127.0.0.1", "--enable-test-net-dcl",
               "--disable-dashboard", "--disable-ota", "--disable-thread-diagnostics"]
        return cmd + (["--primary-interface", self.interface] if self.interface else [])

    def start(self) -> None:
        if not Paths(Path(self.root)).matter_server_js.exists():
            raise StackError("the Matter Server is not installed; run `vesper setup matter` first")
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a Matter Server")
        storage = Path(self.storage)
        storage.mkdir(parents=True, exist_ok=True)
        with (storage.parent / "matter-server.log").open("w") as log:
            self.proc = self.popen(self.command(), stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_for_port(self.port, self.start_timeout)
        except StackError:
```

In `vesper/validation/stack.py`, replace:
```python
        """
        entries = self.api("GET", "/api/config/config_entries/entry", params={"domain": "mqtt"}).json()
        for e in entries:
            if e.get("domain", "mqtt") == "mqtt":
                r = self.api("DELETE", f"/api/config/config_entries/entry/{e['entry_id']}")
                if r.status_code >= 400:
                    raise StackError(f"could not remove the old MQTT entry ({r.status_code}): {r.text[:200]}")
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "mqtt"}).json()
```
with:
```python
        """
        self._remove_entries("mqtt")
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "mqtt"}).json()
```

In `vesper/validation/stack.py`, replace:
```python
            raise StackError(f"the MQTT integration was not configured: {result}")
```
with:
```python
            raise StackError(f"the MQTT integration was not configured: {result}")

    def ensure_matter(self, url: str) -> None:
        """Point the Matter integration at VESPER's Matter Server, freshly on every run (as `ensure_mqtt`).

        Without a Supervisor, Home Assistant's Matter flow asks only for the server's WebSocket URL.
        """
        self._remove_entries("matter")
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "matter"}).json()
        if flow.get("step_id") != "manual":
            raise StackError(f"unexpected Matter config flow step: {flow}")
        result = self.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", json={"url": url}).json()
        if result.get("type") != "create_entry":
            raise StackError(f"the Matter integration was not configured: {result}")

    def matter_entities(self) -> dict[str, list[str]]:
        """Home Assistant's Matter entities, grouped by their device's serial number (the twin's node serial)."""
        r = self.api("POST", "/api/template", json={"template": _MATTER_ENTITIES})
        if r.status_code != 200:
            raise StackError(f"listing the Matter entities failed ({r.status_code}): {r.text[:200]}")
        out: dict[str, list[str]] = {}
        for entity_id, serial in json.loads(r.text):
            if serial:
                out.setdefault(serial, []).append(entity_id)
        return out

    def _remove_entries(self, domain: str) -> None:
        """Remove every config entry of `domain`, with the devices and entities it created, so none survives
        from an earlier run as a ghost."""
        entries = self.api("GET", "/api/config/config_entries/entry", params={"domain": domain}).json()
        for e in entries:
            if e.get("domain", domain) == domain:
                r = self.api("DELETE", f"/api/config/config_entries/entry/{e['entry_id']}")
                if r.status_code >= 400:
                    raise StackError(f"could not remove the old {domain} entry ({r.status_code}): {r.text[:200]}")
```

In `vesper/cli.py`, replace:
```python
def _setup(args) -> int:
    from vesper.validation.stack import StackError, setup_ha
    try:
```
with:
```python
def _setup(args) -> int:
    from vesper.validation import stack
    try:
```

In `vesper/cli.py`, replace:
```python
    try:
        requirements = setup_ha(Path(args.root))
    except StackError as e:
        print(f"FAILED setup: {e}", file=sys.stderr)
```
with:
```python
    try:
        if args.action == "matter":
            pins = stack.setup_matter(Path(args.root))
            print(f"Matter host ready in {stack.Paths(Path(args.root)).matter_host} "
                  f"({', '.join(f'{k} {v}' for k, v in pins.items())})")
            return 0
        requirements = stack.setup_ha(Path(args.root))
    except stack.StackError as e:
        print(f"FAILED setup: {e}", file=sys.stderr)
```

In `vesper/cli.py`, replace:
```python
    setup_ha_cmd.add_argument("--root", default=".vesper")
```
with:
```python
    setup_ha_cmd.add_argument("--root", default=".vesper")
    setup_matter_cmd = setup_actions.add_parser("matter", help="the Matter host and Matter Server (needs Node.js)")
    setup_matter_cmd.add_argument("--root", default=".vesper")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/validation/test_stack.py tests/validation/test_cli.py -q 2>&1 | tail -1`

Expected: `56 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/stack.py vesper/cli.py tests/validation/test_stack.py tests/validation/test_cli.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(stack): VESPER's Matter Server, vesper setup matter, Home Assistant's Matter flow

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: Matter acceptance, `vesper validate matter`, and the report

**Files:**
- Modify: `vesper/validation/acceptance.py` (`run_probe` takes ids and a comparison and is shared; `_wait` becomes `wait_until`), `vesper/cli.py` (`vesper validate matter`), `vesper/validation/report.py` (kind `matter`, pre-registered `seed 0, per_type 1`, Matter L2 credit)
- Create: `vesper/validation/matter_acceptance.py`
- Create tests: `tests/validation/test_matter_acceptance.py` (a miniature world plays the host and Home Assistant's Matter integration), `tests/validation/test_cli_matter.py`; modify `tests/validation/test_report.py`

**Interfaces:**
- Consumes: `MatterTier`, `HostError`, `node_spec` (Task 3); `MatterServer`, `HomeAssistant.ensure_matter`, `.matter_entities` (Task 4); `MATTER_CONTACT` (Task 2).
- Produces, in `vesper/validation/matter_acceptance.py`: `MATTER_L2_CATEGORIES`, `MatterEntity(platform, device_class, features)`, `expected_entities(record)`, `select_matter_products(records, *, per_type, seed, categories=None)`, `initial_state(record)`, `match_entities(expected, states)`, `matter_probe(entity, twin)`, `check_matter_product(ha, runtime, tier, twin_id, server_url, *, timeout, poll, commission_timeout) -> dict`, `summarize_matter(products) -> dict`.
- `vesper validate matter --interface IF [--per-type 1 --seed 0 --vid-mode product|test --category C ...]` writes `results/device-twins/matter-<UTC>.json`; exit 0 only when every sampled product passed.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_matter_acceptance.py`:
```python
import math

from tests.catalog.test_matter_normalize import records as matter_records
from vesper.exposure.matter_tier import MatterTier, node_spec
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.matter_acceptance import (MATTER_L2_CATEGORIES, MatterEntity, check_matter_product,
                                                 expected_entities, initial_state, match_entities, matter_probe,
                                                 select_matter_products, summarize_matter)

DOOR, DOOR2, LOCK, TEMP, LAMP, RVC = ("matter:100b:0016", "matter:100b:0017", "matter:115f:0001",
                                      "matter:115f:0003", "matter:100b:0018", "matter:115f:0002")


def test_expected_entities_follow_the_home_assistant_mapping_without_diagnostics():
    recs = matter_records()
    assert expected_entities(recs[LAMP]) == [
        MatterEntity("light", None, ("On/Off.OnOff", "Color Control.ColorTemperatureMireds"))]
    assert expected_entities(recs[LOCK]) == [
        MatterEntity("lock", None, ("Door Lock.LockState", "Door Lock.OperatingMode"))]
    assert expected_entities(recs[DOOR]) == [MatterEntity("binary_sensor", "door", ("Boolean State.StateValue",))]
    assert expected_entities(recs[TEMP]) == [
        MatterEntity("sensor", "temperature", ("Temperature Measurement.MeasuredValue",))]
    assert expected_entities(recs[RVC]) == []


def test_sensors_expect_one_entity_per_attribute_and_composites_one_per_device():
    smoke = {"id": "matter:fff1:smoke", "binding": {"matter": {"device_type": "Smoke CO Alarm"}}, "features": [
        {"name": "Smoke CO Alarm.HardwareFaultAlert", "type": "binary", "ha": "binary_sensor/problem"},
        {"name": "Smoke CO Alarm.EndOfServiceAlert", "type": "enum", "ha": "binary_sensor/problem"},
        {"name": "Smoke CO Alarm.ExpressedState", "type": "enum", "ha": None}]}
    assert expected_entities(smoke) == [
        MatterEntity("binary_sensor", "problem", ("Smoke CO Alarm.HardwareFaultAlert",)),
        MatterEntity("binary_sensor", "problem", ("Smoke CO Alarm.EndOfServiceAlert",))]


def test_one_product_per_device_type_in_the_l2_categories_seeded():
    recs = list(matter_records().values())
    picked = select_matter_products(recs, per_type=1, seed=0)
    assert sorted(r["binding"]["matter"]["device_type"] for r in picked) == [
        "Color Temperature Light", "Contact Sensor", "Door Lock", "Temperature Sensor"]
    assert select_matter_products(recs, per_type=1, seed=0) == picked
    doors = {next(r["id"] for r in select_matter_products(recs, per_type=1, seed=s)
                  if r["binding"]["matter"]["device_type"] == "Contact Sensor") for s in range(20)}
    assert doors == {DOOR, DOOR2}  # the seed picks among a type's products
    assert len(select_matter_products(recs, per_type=2, seed=0)) == 5
    assert [r["id"] for r in select_matter_products(recs, per_type=1, seed=0, categories=["lock"])] == [LOCK]
    assert {"light", "plug", "lock", "thermostat", "contact_sensor", "motion_sensor"} <= set(MATTER_L2_CATEGORIES)


def test_a_lock_starts_unlocked_so_that_locking_it_is_a_change():
    recs = matter_records()
    assert initial_state(recs[LOCK]) == {"Door Lock.LockState": "Unlocked"}
    assert initial_state(recs[LAMP]) == {}


def st(entity_id, state="0", **attributes):
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


def test_entities_match_by_domain_and_device_class_and_each_is_used_once():
    expected = [MatterEntity("binary_sensor", "door", ("a",)), MatterEntity("sensor", "temperature", ("b",)),
                MatterEntity("light", None, ("c",)), MatterEntity("sensor", "temperature", ("d",))]
    states = [st("sensor.x_battery", device_class="battery"), st("sensor.x_temperature", device_class="temperature"),
              st("binary_sensor.x_door", device_class="door"), st("button.x_identify", device_class="identify")]
    assert [m and m["entity_id"] for m in match_entities(expected, states)] == [
        "binary_sensor.x_door", "sensor.x_temperature", None, None]
    plain = [st("switch.x_classed", device_class="outlet"), st("switch.x_plain")]
    assert match_entities([MatterEntity("switch", None, ("e",))], plain)[0]["entity_id"] == "switch.x_plain"


# -- a miniature world: the host's nodes plus Home Assistant's Matter integration ------------------------------------
class World:
    """Plays the Matter host (nodes keep the values VESPER sets) and Home Assistant (one entity per expected
    mapping, showing its node's values the way Home Assistant's Matter integration converts them)."""

    def __init__(self, rids, *, missing=(), deaf=(), unpaired=()):
        self.runtime = Runtime(clock=LogicalClock())
        recs = matter_records()
        self.specs, self.nodes, self.handler = {}, {}, None
        self.missing, self.deaf, self.unpaired = set(missing), set(deaf), set(unpaired)
        for i, rid in enumerate(rids):
            tid = self.runtime.add(recs[rid], state=initial_state(recs[rid])).id
            self.specs[tid] = node_spec(recs[rid], tid, i)
        self.tier = MatterTier(self.runtime, self, self.specs)
        self.tier.start()

    # the host
    def request(self, op, wait=True, **a):
        if op == "add":
            self.nodes[a["twin"]] = dict(a["state"])
            return {"resolved": [f["name"] for f in a["features"]], "unresolved": [],
                    "pairing": {"manual": "34970112332", "qr": f"MT:{a['twin']}"}}
        if op == "set":
            self.nodes[a["twin"]][a["feature"]] = a["value"]
        if op == "commission":
            tid = a["code"][3:]
            if tid in self.unpaired:
                from vesper.exposure.matter_tier import HostError
                raise HostError("commission: no commissionable node answered")
            return {"node_id": list(self.specs).index(tid) + 1}
        return {}

    def on_event(self, fn):
        self.handler = fn

    # Home Assistant
    def _entities(self):
        out = {}
        for tid, spec in self.specs.items():
            if tid not in self.tier.node_ids:
                continue
            for n, e in enumerate(expected_entities(self.runtime.twins[tid].vds)):
                eid = f"{e.platform}.node{self.tier.node_ids[tid]}_{n}"
                if eid not in self.missing:
                    out[eid] = (tid, e)
        return out

    def matter_entities(self):
        out = {}
        for eid, (tid, _) in self._entities().items():
            out.setdefault(self.specs[tid].serial, []).append(eid)
        return out

    def state(self, entity_id):
        if entity_id not in self._entities():
            return None
        tid, e = self._entities()[entity_id]
        node, f = self.nodes[tid], e.features[0]
        if e.platform in ("light", "switch"):
            shown = "on" if node[f] else "off"
        elif e.platform == "lock":
            shown = "locked" if node[f] == "Locked" else "unlocked"
        elif e.device_class == "door":
            shown = "off" if node[f] else "on"  # Boolean State true: contact, so the door is closed
        elif e.device_class == "occupancy":
            shown = "on" if node[f] & 1 else "off"
        elif e.device_class == "illuminance":
            shown = str(round(math.pow(10, (node[f] - 1) / 10000), 1))
        else:
            shown = str(node[f] / 100)
        return st(entity_id, shown, **({"device_class": e.device_class} if e.device_class else {}))

    def call(self, domain, service, data):
        tid, e = self._entities()[data["entity_id"]]
        if tid in self.deaf:
            return
        feature, value = {("light", "turn_on"): ("On/Off.OnOff", True), ("switch", "turn_on"): ("On/Off.OnOff", True),
                          ("lock", "lock"): ("Door Lock.LockState", "Locked")}[(domain, service)]
        self.nodes[tid][feature] = value  # the node's cluster server handles the command ...
        self.handler({"event": "changed", "twin": tid, "feature": feature, "value": value})  # ... and reports it


def checked(world, **kw):
    out = []
    for tid in world.specs:
        out.append(check_matter_product(world, world.runtime, world.tier, tid, "ws://127.0.0.1:15580/ws",
                                        timeout=0.2, poll=0.01, commission_timeout=5, **kw))
    return out


def test_products_pair_display_and_obey_through_home_assistant():
    world = World([DOOR, LOCK, TEMP, LAMP])
    results = checked(world)
    assert [r["passed"] for r in results] == [True] * 4 and all(r["exercised"] for r in results)
    door, lock, temp, lamp = results
    assert door["entities"][0]["probe"]["kind"] == "stimulus:contact" and door["node_id"] == 1
    assert lock["entities"][0]["probe"] == {"ok": True, "kind": "lock.lock", "twin_ok": True, "ha_ok": True}
    assert world.runtime.state(lamp["twin_id"])["On/Off.OnOff"] is True
    assert temp["category"] == "climate_sensor" and temp["device_type"] == "Temperature Sensor"
    assert lamp["serial"] == world.specs[lamp["twin_id"]].serial


def test_a_missing_entity_fails_its_product_only():
    world = World([DOOR, LAMP], missing={"light.node2_0"})
    door, lamp = checked(world)
    assert door["passed"] and not lamp["passed"] and lamp["entities"][0]["created"] is False


def test_a_command_that_never_reaches_the_twin_fails():
    world = World([LAMP], deaf={LAMP})
    (lamp,) = checked(world)
    assert not lamp["passed"] and lamp["entities"][0]["probe"]["twin_ok"] is False


def test_a_node_the_server_cannot_commission_fails_without_stopping_the_run():
    world = World([DOOR, LAMP], unpaired={DOOR})
    door, lamp = checked(world)
    assert not door["passed"] and "no commissionable node" in door["commission_error"] and door["node_id"] is None
    assert lamp["passed"]


def test_probes_pick_a_target_different_from_the_current_value():
    world = World([DOOR])
    tid = next(iter(world.specs))
    e = expected_entities(world.runtime.twins[tid].vds)[0]
    assert matter_probe(e, world.runtime.twins[tid])["stimulus"] == {"type": "contact", "state": "closed"}
    world.runtime.stimulate(tid, {"type": "contact", "state": "closed"})
    probe = matter_probe(e, world.runtime.twins[tid])
    assert probe["stimulus"] == {"type": "contact", "state": "opened"} and probe["ha"] == "on"


def test_summary_counts_device_types_and_categories():
    world = World([DOOR, DOOR2, LAMP], missing={"light.node3_0"})
    summary = summarize_matter(checked(world))
    assert summary["products"] == 3 and summary["passed"] == 2
    assert summary["device_types_passing"] == ["Contact Sensor"]
    assert summary["by_device_type"]["Color Temperature Light"] == {"products": 1, "passed": 0, "exercised": 0,
                                                                     "category": "light"}
    assert summary["categories_passing"] == ["contact_sensor"]


def test_a_climate_probe_asks_for_a_mode_the_device_types_recipe_supports():
    from vesper.twins.twin import Twin
    modes = ["Off", "Auto", "Cool", "Heat", "EmergencyHeat", "Precooling", "FanOnly", "Dry", "Sleep"]

    def climate(dt):
        return Twin({"id": f"matter:fff1:{dt}", "binding": {"matter": {"device_type": dt}},
                     "features": [{"name": "Thermostat.SystemMode", "type": "enum", "values": modes, "ha": "climate",
                                   "access": ["state", "get", "set"]}]})

    e = MatterEntity("climate", None, ("Thermostat.SystemMode",))
    assert matter_probe(e, climate("Thermostat"))["call"] == ("climate", "set_hvac_mode", {"hvac_mode": "heat"})
    ac = climate("Room Air Conditioner")
    assert matter_probe(e, ac)["twin"] == ("Thermostat.SystemMode", "Cool")
    ac.state["Thermostat.SystemMode"] = "Cool"
    assert matter_probe(e, ac)["ha"] == "off"
```

Create `tests/validation/test_cli_matter.py`:
```python
"""`vesper validate matter`, wired to fakes: no Matter Server, Home Assistant or Node process is started."""
import json
from types import SimpleNamespace

import pytest

import vesper.exposure.matter_tier as tier_mod
import vesper.twins.clock as clock_mod
import vesper.validation.matter_acceptance as macc_mod
import vesper.validation.stack as stack_mod
from tests.catalog.test_matter_normalize import records as matter_records
from tests.twins.conftest import write_catalog
from vesper import cli


class Events:
    log = []


class FakeServer:
    def __init__(self, root, storage, port=15580, interface=None, **kw):
        self.storage, self.port, self.interface = storage, port, interface
        Events.servers.append(self)

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/ws"

    def start(self):
        Events.log.append("server.start")
        if Events.fail == "server":
            raise stack_mod.StackError("port 15580 is already in use")

    def stop(self):
        Events.log.append("server.stop")


class FakeHA:
    def __init__(self, root, port=18123, **kw):
        self.port = port

    def start(self):
        Events.log.append("ha.start")

    def onboard(self):
        Events.log.append("ha.onboard")

    def ensure_matter(self, url):
        Events.log.append(("ha.ensure_matter", url))

    def stop(self):
        Events.log.append("ha.stop")


class FakeHost:
    def __init__(self, storage, *, interface=None, command=None, log=None, **kw):
        self.storage, self.interface, self.command, self.log, self.notes = storage, interface, command, log, []
        Events.hosts.append(self)

    def start(self):
        Events.log.append("host.start")

    def stop(self):
        Events.log.append("host.stop")

    def on_event(self, fn):
        pass

    def request(self, op, wait=True, **a):
        Events.log.append(f"host.{op}")
        if op == "add":
            Events.adds.append(a)
            return {"resolved": [f["name"] for f in a["features"]], "unresolved": [],
                    "pairing": {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}}
        return {}


def fake_check(ha, runtime, tier, twin_id, server_url, *, timeout, poll, commission_timeout):
    Events.checked.append((twin_id, server_url, commission_timeout))
    spec = tier.specs[twin_id]
    paired = Events.fail != "commission"
    return {"id": twin_id, "twin_id": twin_id, "device_type": spec.device_type,
            "category": runtime.twins[twin_id].vds["identity"]["category"], "serial": spec.serial,
            "node_id": 1 if paired else None, "passed": paired and twin_id not in Events.failing, "exercised": True,
            "entities": []}


@pytest.fixture
def matter(monkeypatch, tmp_path):
    Events.log, Events.servers, Events.hosts, Events.checked, Events.adds = [], [], [], [], []
    Events.fail, Events.failing = None, set()
    monkeypatch.setattr(stack_mod, "MatterServer", FakeServer)
    monkeypatch.setattr(stack_mod, "HomeAssistant", FakeHA)
    monkeypatch.setattr(tier_mod, "Host", FakeHost)
    monkeypatch.setattr(macc_mod, "check_matter_product", fake_check)

    class Clock(clock_mod.LogicalClock):
        def close(self):
            Events.log.append("clock.close")

    monkeypatch.setattr(clock_mod, "WallClock", Clock)
    root = tmp_path / ".vesper"
    host_dir = stack_mod.Paths(root).matter_host
    host_dir.mkdir(parents=True)
    (host_dir / "host.mjs").write_text("// installed")
    (host_dir / "package.json").write_text(json.dumps({"dependencies": {"@matter/main": "0.17.9",
                                                                         "matter-server": "1.4.0"}}))
    catalog = write_catalog(tmp_path / "vds", list(matter_records().values()))
    return SimpleNamespace(root=root, catalog=catalog, host_dir=host_dir)


def run(m, tmp_path, *extra):
    return cli.main(["validate", "matter", "--catalog", str(m.catalog), "--root", str(m.root),
                     "--interface", "en0", "--out", str(tmp_path / "matter.json"), *extra])


def test_matter_acceptance_runs_the_whole_stack_and_writes_a_report(matter, tmp_path, capsys):
    assert run(matter, tmp_path) == 0
    log = Events.log
    assert log[:4] == ["server.start", "ha.start", "ha.onboard", ("ha.ensure_matter", "ws://127.0.0.1:15580/ws")]
    assert log.index("host.start") < log.index("host.add") and log.count("host.add") == 4
    assert log[-4:] == ["host.stop", "clock.close", "ha.stop", "server.stop"] and log.count("host.remove") == 4
    [server], [host] = Events.servers, Events.hosts
    assert server.interface == host.interface == "en0" and server.storage.parent == host.storage.parent
    assert server.storage.name == "server" and host.storage.name == "nodes"
    assert host.command == ["node", str(matter.host_dir / "host.mjs")]
    assert {c[1] for c in Events.checked} == {"ws://127.0.0.1:15580/ws"}
    report = json.loads((tmp_path / "matter.json").read_text())
    assert report["summary"]["passed"] == report["summary"]["products"] == 4
    assert report["matter_packages"] == {"@matter/main": "0.17.9", "matter-server": "1.4.0"}
    assert (report["interface"], report["vid_mode"], report["seed"], report["per_type"]) == ("en0", "product", 0, 1)
    assert report["run"]["parameters"]["per_type"] == 1
    assert "4/4 products passed" in capsys.readouterr().out


def test_a_failing_product_fails_the_run(matter, tmp_path):
    Events.failing = {"matter:115f:0001"}
    assert run(matter, tmp_path) == 1
    report = json.loads((tmp_path / "matter.json").read_text())
    assert report["summary"]["passed"] == 3


def test_the_interface_is_required_because_matter_cannot_use_loopback(matter, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["validate", "matter", "--catalog", str(matter.catalog), "--root", str(matter.root)])
    assert "--interface" in capsys.readouterr().err


def test_a_failed_start_stops_whatever_was_started(matter, tmp_path, capsys):
    Events.fail = "server"
    assert run(matter, tmp_path) == 1
    assert "FAILED matter: port 15580 is already in use" in capsys.readouterr().err
    assert Events.log == ["server.start", "host.stop", "clock.close", "ha.stop", "server.stop"]
    assert not (tmp_path / "matter.json").exists()


def test_the_run_gives_up_when_no_node_can_be_commissioned(matter, tmp_path, capsys):
    Events.fail = "commission"
    assert run(matter, tmp_path) == 1
    assert "commissioned none of the first 3 nodes" in capsys.readouterr().err
    assert len(Events.checked) == 3 and Events.log[-1] == "server.stop"


def test_an_uninstalled_host_is_reported_before_anything_starts(matter, tmp_path, capsys):
    (matter.host_dir / "host.mjs").unlink()
    assert run(matter, tmp_path) == 1
    assert "vesper setup matter" in capsys.readouterr().err and "server.start" not in Events.log


def test_test_vendor_mode_is_passed_to_every_node(matter, tmp_path):
    assert run(matter, tmp_path, "--vid-mode", "test") == 0
    assert {a["vendor_id"] for a in Events.adds} == {tier_mod.TEST_VENDOR_ID}
    assert json.loads((tmp_path / "matter.json").read_text())["vid_mode"] == "test"
```

Append to `tests/validation/test_report.py`:
```python


def matter_result(products, *, per_type=1, vid_mode="product"):
    from vesper.validation.matter_acceptance import summarize_matter
    return {"summary": summarize_matter(products), "products": products, "vid_mode": vid_mode,
            "run": {"parameters": {"seed": 0, "per_type": per_type, "vid_mode": vid_mode}}}


MATTER_PRODUCTS = [
    {"id": "matter:100b:0016", "device_type": "Contact Sensor", "category": "contact_sensor", "passed": True,
     "exercised": True},
    {"id": "matter:115f:0001", "device_type": "Door Lock", "category": "lock", "passed": False, "exercised": True}]


def test_matter_l2_counts_only_matter_products_that_passed(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    cov = json.loads((catalog / "coverage.json").read_text())
    cov["matter"] = {"L0": 5, "L1": 4, "L2": 0, "L3": 0, "L4": 0, "by_category": {}}
    (catalog / "coverage.json").write_text(json.dumps(cov))
    r = tmp_path / "results"
    write_result(r, "matter-20261025T100000Z.json", matter_result(MATTER_PRODUCTS))
    report = build_report(catalog, r)
    assert report["l2"]["matter"]["validated_products"] == ["matter:100b:0016"]
    assert report["l2"]["validated_products"] == []  # Zigbee's credit is untouched
    c = report["checks"]["matter"]
    assert (c["passed"], c["products"], c["products_passed"]) == (False, 2, 1)
    assert c["device_types_passing"] == ["Contact Sensor"] and c["exploratory"] is False
    md = render_markdown(report)
    assert "| matter | 5 | 4 | 1 | 0 | 0 |" in md
    assert "| matter | FAIL | 1/2 products; device types fully passing 1/2; product vendor ids |" in md


def test_a_matter_run_with_other_parameters_is_exploratory_and_test_vendor_ids_are_named(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    r = tmp_path / "results"
    write_result(r, "matter-20261025T100000Z.json", matter_result(MATTER_PRODUCTS[:1], per_type=2, vid_mode="test"))
    report = build_report(catalog, r)
    assert report["checks"]["matter"]["exploratory"] is True and report["checks"]["matter"]["passed"] is True
    assert "Matter's test vendor id" in render_markdown(report)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_matter_acceptance.py tests/validation/test_cli_matter.py tests/validation/test_report.py -q 2>&1 | tail -1`

Expected: `2 errors`: `tests/validation/test_matter_acceptance.py` and `tests/validation/test_cli_matter.py` stop with `ModuleNotFoundError: No module named 'vesper.validation.matter_acceptance'`.

- [ ] **Step 3: Implement**

In `vesper/validation/acceptance.py`, replace:
```python

def _wait(fn: Callable[[], object], timeout: float, poll: float):
    deadline = time.monotonic() + timeout
```
with:
```python

def wait_until(fn: Callable[[], object], timeout: float, poll: float):
    deadline = time.monotonic() + timeout
```

In `vesper/validation/acceptance.py`, replace:
```python
    for e in bridge.expected[placement.twin_id]:
        state = _wait(lambda e=e: ha.state(e.entity_id), wait, poll)
        wait = timeout if state is not None else 0
```
with:
```python
    for e in bridge.expected[placement.twin_id]:
        state = wait_until(lambda e=e: ha.state(e.entity_id), wait, poll)
        wait = timeout if state is not None else 0
```

In `vesper/validation/acceptance.py`, replace:
```python
                                    and (unit is None or attrs.get("unit_of_measurement") == unit))
            row["state_ok"] = bool(_wait(lambda e=e: _displayed(e, twin, bridge.reported(twin.id),
                                                                ha.state(e.entity_id)), timeout, poll))
```
with:
```python
                                    and (unit is None or attrs.get("unit_of_measurement") == unit))
            row["state_ok"] = bool(wait_until(lambda e=e: _displayed(e, twin, bridge.reported(twin.id),
                                                                ha.state(e.entity_id)), timeout, poll))
```

In `vesper/validation/acceptance.py`, replace:
```python
            if probe:
                row["probe"] = _run_probe(ha, runtime, placement, e, probe, timeout, poll)
        else:
```
with:
```python
            if probe:
                row["probe"] = run_probe(ha, runtime, placement.twin_id, e.entity_id, probe, timeout, poll)
        else:
```

In `vesper/validation/acceptance.py`, replace:
```python

def _run_probe(ha, runtime, placement, e, probe, timeout, poll) -> dict:
    if "call" in probe:
```
with:
```python

def run_probe(ha, runtime, twin_id: str, entity_id: str, probe: dict, timeout: float, poll: float,
              matches: Callable[[dict | None, object], bool] = _matches) -> dict:
    """Carry out a probe: a Home Assistant service call that must reach the twin and come back, or a stimulus
    that must show in Home Assistant."""
    if "call" in probe:
```

In `vesper/validation/acceptance.py`, replace:
```python
        try:
            ha.call(domain, service, {"entity_id": e.entity_id, **data})
        except StackError as err:  # Home Assistant refused this call; the run goes on (a dead HA still aborts)
```
with:
```python
        try:
            ha.call(domain, service, {"entity_id": entity_id, **data})
        except StackError as err:  # Home Assistant refused this call; the run goes on (a dead HA still aborts)
```

In `vesper/validation/acceptance.py`, replace:
```python
        feature, value = probe["twin"]
        twin_ok = _wait(lambda: runtime.state(placement.twin_id)[feature] == value, timeout, poll)
        if probe.get("also"):
```
with:
```python
        feature, value = probe["twin"]
        twin_ok = wait_until(lambda: runtime.state(twin_id)[feature] == value, timeout, poll)
        if probe.get("also"):
```

In `vesper/validation/acceptance.py`, replace:
```python
            f2, v2 = probe["also"]
            twin_ok = twin_ok and _wait(lambda: runtime.state(placement.twin_id)[f2] == v2, timeout, poll)
        ha_ok = _wait(lambda: _matches(ha.state(e.entity_id), probe["ha"]), timeout, poll)
        return {"ok": bool(twin_ok and ha_ok), "kind": f"{domain}.{service}", "twin_ok": bool(twin_ok),
```
with:
```python
            f2, v2 = probe["also"]
            twin_ok = twin_ok and wait_until(lambda: runtime.state(twin_id)[f2] == v2, timeout, poll)
        ha_ok = wait_until(lambda: matches(ha.state(entity_id), probe["ha"]), timeout, poll)
        return {"ok": bool(twin_ok and ha_ok), "kind": f"{domain}.{service}", "twin_ok": bool(twin_ok),
```

In `vesper/validation/acceptance.py`, replace:
```python
                "ha_ok": bool(ha_ok)}
    runtime.stimulate(placement.twin_id, probe["stimulus"])
    if "event" in probe:
```
with:
```python
                "ha_ok": bool(ha_ok)}
    runtime.stimulate(twin_id, probe["stimulus"])
    if "event" in probe:
```

In `vesper/validation/acceptance.py`, replace:
```python
    if "event" in probe:
        ok = _wait(lambda: ((ha.state(e.entity_id) or {}).get("attributes", {}).get("event_type") == probe["event"]),
                   timeout, poll)
```
with:
```python
    if "event" in probe:
        ok = wait_until(lambda: ((ha.state(entity_id) or {}).get("attributes", {}).get("event_type") == probe["event"]),
                   timeout, poll)
```

In `vesper/validation/acceptance.py`, replace:
```python
    else:
        ok = _wait(lambda: _matches(ha.state(e.entity_id), probe["ha"]), timeout, poll)
    return {"ok": bool(ok), "kind": f"stimulus:{probe['stimulus']['type']}"}
```
with:
```python
    else:
        ok = wait_until(lambda: matches(ha.state(entity_id), probe["ha"]), timeout, poll)
    return {"ok": bool(ok), "kind": f"stimulus:{probe['stimulus']['type']}"}
```

Create `vesper/validation/matter_acceptance.py`:
```python
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
```

In `vesper/cli.py`, replace:
```python

def main(argv: list[str] | None = None) -> int:
```
with:
```python

def _validate_matter(args) -> int:
    from vesper.exposure import matter_tier
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_catalog
    from vesper.twins.runtime import Runtime
    from vesper.validation import matter_acceptance as macc
    from vesper.validation import stack
    from vesper.validation.acceptance import EMPTY_HA_PRODUCTS_LIMIT
    root = Path(args.root)
    paths = stack.Paths(root)
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("matter:")]
    products = macc.select_matter_products(records, per_type=args.per_type, seed=args.seed,
                                           categories=args.category or None)
    run_dir = paths.matter_runs / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    server = stack.MatterServer(root, run_dir / "server", port=args.server_port, interface=args.interface)
    ha = stack.HomeAssistant(root, port=args.ha_port)
    host = matter_tier.Host(run_dir / "nodes", interface=args.interface,
                            command=["node", str(paths.matter_host / "host.mjs")], log=run_dir / "matter-host.log")
    runtime = Runtime(clock=WallClock(), seed=args.seed)
    tier, results = None, []
    try:
        if not (paths.matter_host / "host.mjs").exists():
            raise stack.StackError("the Matter host is not installed; run `vesper setup matter` first")
        server.start()
        ha.start()
        ha.onboard()
        ha.ensure_matter(server.url)
        specs = {}
        for i, r in enumerate(products):
            tid = runtime.add(r, state=macc.initial_state(r)).id
            specs[tid] = matter_tier.node_spec(r, tid, i, vid_mode=args.vid_mode)
        host.start()
        tier = matter_tier.MatterTier(runtime, host, specs)
        tier.start()
        for tid in specs:
            results.append(macc.check_matter_product(ha, runtime, tier, tid, server.url, timeout=args.timeout,
                                                     poll=0.25, commission_timeout=args.commission_timeout))
            if len(results) == EMPTY_HA_PRODUCTS_LIMIT and all(r["node_id"] is None for r in results):
                raise stack.StackError(f"the Matter Server commissioned none of the first {EMPTY_HA_PRODUCTS_LIMIT} "
                                       f"nodes; check {run_dir / 'matter-server.log'} and the interface "
                                       f"{args.interface!r}")
    except (stack.StackError, matter_tier.HostError) as e:
        print(f"FAILED matter: {e}", file=sys.stderr)
        return 1
    finally:
        if tier is not None:
            tier.stop()
        host.stop()
        runtime.clock.close()
        ha.stop()
        server.stop()
    summary = macc.summarize_matter(results)
    pins_file = paths.matter_host / "package.json"
    report = {"summary": summary, "products": results, "ha_version": stack.HA_VERSION,
              "matter_packages": json.loads(pins_file.read_text()).get("dependencies", {}),
              "seed": args.seed, "per_type": args.per_type, "vid_mode": args.vid_mode, "interface": args.interface,
              "run_dir": str(run_dir), "faulted": runtime.faulted,
              "notes": (tier.notes + host.notes + runtime.notes)[:500]}
    path = _write_report(args.out, "matter", report, args)
    print(f"matter: {summary['passed']}/{summary['products']} products passed; "
          f"{len(summary['device_types_passing'])}/{summary['device_types']} device types fully passing; "
          f"report {path}")
    return 0 if summary["products"] and summary["passed"] == summary["products"] else 1


def main(argv: list[str] | None = None) -> int:
```

In `vesper/cli.py`, replace:
```python
    acc.add_argument("--out")
    args = parser.parse_args(argv)
```
with:
```python
    acc.add_argument("--out")
    mat = checks.add_parser("matter", help="Home Assistant commissions, displays and controls Matter twins (L2)")
    mat.add_argument("--catalog", default="catalog/vds")
    mat.add_argument("--root", default=".vesper")
    mat.add_argument("--interface", required=True,
                     help="the network interface the nodes advertise on (Matter cannot use loopback), e.g. en0")
    mat.add_argument("--per-type", type=int, default=1, help="products per Matter device type")
    mat.add_argument("--category", action="append", help="limit to a category (repeatable)")
    mat.add_argument("--seed", type=int, default=0)
    mat.add_argument("--vid-mode", choices=("product", "test"), default="product",
                     help="present each product's vendor id, or Matter's test vendor id")
    mat.add_argument("--ha-port", type=int, default=18123)
    mat.add_argument("--server-port", type=int, default=15580)
    mat.add_argument("--timeout", type=float, default=30.0)
    mat.add_argument("--commission-timeout", type=float, default=120.0)
    mat.add_argument("--out")
    args = parser.parse_args(argv)
```

In `vesper/cli.py`, replace:
```python
        return _validate_realdata(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```
with:
```python
        return _validate_realdata(args)
    if args.action == "matter":
        return _validate_matter(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```

In `vesper/validation/report.py`, replace:
```python
run over nothing does not pass; a run whose parameters differ from the pre-registration, or that does
not record them, is labelled exploratory; and L2 is credited only to products that passed acceptance.
"""
```
with:
```python
run over nothing does not pass; a run whose parameters differ from the pre-registration, or that does
not record them, is labelled exploratory; and L2 is credited only to products that passed acceptance
(Zigbee through the bridge, Matter through `vesper validate matter`).
"""
```

In `vesper/validation/report.py`, replace:
```python

KINDS = ("discovery", "roundtrip", "acceptance", "rules", "latency", "realdata")
# The pre-registered parameters (docs/experiments/l3-timing-design.md and the run book).
```
with:
```python

KINDS = ("discovery", "roundtrip", "acceptance", "matter", "rules", "latency", "realdata")
# The pre-registered parameters (docs/experiments/l3-timing-design.md and the run book).
```

In `vesper/validation/report.py`, replace:
```python
    "acceptance": {"seed": 0, "per_category": 5},
}
```
with:
```python
    "acceptance": {"seed": 0, "per_category": 5},
    "matter": {"seed": 0, "per_type": 1},
}
```

In `vesper/validation/report.py`, replace:
```python
                "products_passed": s["passed"], "passed": s["products"] > 0 and s["passed"] == s["products"]}
    if kind == "roundtrip":
```
with:
```python
                "products_passed": s["passed"], "passed": s["products"] > 0 and s["passed"] == s["products"]}
    if kind == "matter":
        s = data["summary"]
        return {**{k: s[k] for k in ("products", "device_types", "device_types_passing", "categories_passing")},
                "products_passed": s["passed"], "vid_mode": data.get("vid_mode"),
                "passed": s["products"] > 0 and s["passed"] == s["products"]}
    if kind == "roundtrip":
```

In `vesper/validation/report.py`, replace:
```python
        else []
    eligible = sum(1 for r in records if r["id"].startswith("zigbee:") and r["fidelity"]["reached"]["L1"]
```
with:
```python
        else []
    matter = latest(results_dir, "matter")
    matter_validated = sorted(p["id"] for p in matter[1]["products"] if p["passed"]) if matter and matter[1] else []
    eligible = sum(1 for r in records if r["id"].startswith("zigbee:") and r["fidelity"]["reached"]["L1"]
```

In `vesper/validation/report.py`, replace:
```python
            "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated},
            "timing_paths": {n: {"source": p.source, "basis": p.basis, "median_s": p.median()}
```
with:
```python
            "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated,
                                     "matter": {"kind": "matter-server", "validated_products": matter_validated}},
            "timing_paths": {n: {"source": p.source, "basis": p.basis, "median_s": p.median()}
```

In `vesper/validation/report.py`, replace:
```python
                f"{len(c.get('categories_passing') or [])}")
    if kind == "roundtrip":
```
with:
```python
                f"{len(c.get('categories_passing') or [])}")
    if kind == "matter":
        ids = "Matter's test vendor id" if c.get("vid_mode") == "test" else "product vendor ids"
        return (f"{c.get('products_passed')}/{c.get('products')} products; device types fully passing "
                f"{len(c.get('device_types_passing') or [])}/{c.get('device_types')}; {ids}")
    if kind == "roundtrip":
```

In `vesper/validation/report.py`, replace:
```python
    for proto, c in sorted(report["coverage"].items()):
        l2 = len(report["l2"]["validated_products"]) if proto == "zigbee" else 0
        lines.append(f"| {proto} | {c['L0']} | {c['L1']} | {l2} | {c['L3']} | {c['L4']} |")
```
with:
```python
    for proto, c in sorted(report["coverage"].items()):
        l2 = {"zigbee": len(report["l2"]["validated_products"]),
              "matter": len(report["l2"]["matter"]["validated_products"])}.get(proto, 0)
        lines.append(f"| {proto} | {c['L0']} | {c['L1']} | {l2} | {c['L3']} | {c['L4']} |")
```

In `vesper/validation/report.py`, replace:
```python
                  f"{report['l2']['eligible_zigbee']} L1 Zigbee products are eligible (they expose at least one "
                  "Home Assistant entity through the bridge).", "", "## Assumed fields", "",
              "Counted out of coverage (spec §3.2). Most frequent:", "", "| Field | Records |", "|---|---|"]
```
with:
```python
                  f"{report['l2']['eligible_zigbee']} L1 Zigbee products are eligible (they expose at least one "
                  "Home Assistant entity through the bridge). Matter L2 (matter-server) is credited only to "
                  "products whose node Home Assistant commissioned, displayed and controlled.", "",
              "## Assumed fields", "",
              "Counted out of coverage (spec §3.2). Most frequent:", "", "| Field | Records |", "|---|---|"]
```

- [ ] **Step 4: Run them, then the whole suite**

Run: `.venv/bin/python -m pytest tests/validation/test_matter_acceptance.py tests/validation/test_cli_matter.py tests/validation/test_report.py -q 2>&1 | tail -1`

Expected: `30 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `468 passed, 7 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/acceptance.py vesper/validation/matter_acceptance.py vesper/cli.py vesper/validation/report.py tests/validation/test_matter_acceptance.py tests/validation/test_cli_matter.py tests/validation/test_report.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): Matter acceptance, vesper validate matter, Matter L2 in the report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 6: The Node host

**Files:**
- Create: `vesper/exposure/matter_host/package.json`, `vesper/exposure/matter_host/package-lock.json` (generated), `vesper/exposure/matter_host/codec.mjs`, `vesper/exposure/matter_host/recipes.mjs`, `vesper/exposure/matter_host/stdout-guard.mjs`, `vesper/exposure/matter_host/host.mjs`
- Create tests: `vesper/exposure/matter_host/codec.test.mjs`, `vesper/exposure/matter_host/recipes.test.mjs`, `vesper/exposure/matter_host/stdout-guard.test.mjs` (pure, `node --test`), `tests/exposure/test_matter_host_contract.py` (opt-in: the real host, offline)
- Modify: `.gitignore` (`node_modules/`)

**Interfaces:**
- Consumes: the protocol of Task 3 (`add`, `set`, `remove`, `commission`, `shutdown`; events `ready`, `changed`); `setup_matter` copies `package*.json` and every `.mjs` except tests (Task 4).
- Produces: ops `add` → `{resolved, unresolved, initial_errors, pairing: {manual, qr}}`, `set`, `get` → `{values}`, `remove`, `commission` → `{node_id}` (runs beside the request queue), `shutdown`; `VESPER_MATTER_OFFLINE=1` builds nodes without starting them (tests only).
- Recipes for the 18 device types the acceptance samples, probed offline against matter.js 0.17.9 (2026-10-23): with them every endpoint initializes and every catalog feature resolves.

- [ ] **Step 1: Write the failing Node tests**

Create `vesper/exposure/matter_host/codec.test.mjs`:
```javascript
// node --test: the codec needs no matter.js; the schemas below copy the shape of matter.js 0.17.9's model.
import { test } from "node:test";
import assert from "node:assert/strict";
import { bitmapCodec, codecFor, enumCodec, norm, resolveFeature } from "./codec.mjs";

const LOCK_STATE = { name: "LockState", metabase: { name: "enum8" }, children: [
  { name: "NotFullyLocked", id: 0 }, { name: "Locked", id: 1 }, { name: "Unlocked", id: 2 }, { name: "Unlatched", id: 3 }] };
// a derived attribute type keeps its values on the base type
const SENSOR_TYPE = { name: "OccupancySensorType", children: [], base: { children: [
  { name: "PIR", id: 0 }, { name: "PIRAndUltrasonic", id: 2 }] } };
const OCCUPANCY = { name: "Occupancy", metabase: { name: "map8" }, children: [{ name: "Occupied", constraint: "0" }] };
const WIDE = { name: "Wide", metabase: { name: "map16" }, children: [
  { name: "Low", constraint: "0" }, { name: "Mode", constraint: "1 to 3" }, { name: "High", constraint: "15" }] };

test("names are compared without case or punctuation", () => {
  assert.equal(norm("On/Off"), norm("onOff"));
  assert.equal(norm("Door Lock"), "doorlock");
});

test("an enum name maps to its code and back", () => {
  const c = enumCodec(LOCK_STATE);
  assert.equal(c.encode("Unlocked"), 2);
  assert.equal(c.encode("unlocked"), 2);
  assert.equal(c.decode(1), "Locked");
  assert.equal(enumCodec(SENSOR_TYPE).encode("PIRAndUltrasonic"), 2);
  assert.throws(() => c.encode("Ajar"), /not a value of LockState/);
});

test("a bitmap integer maps to matter.js's object of fields and back", () => {
  const occ = bitmapCodec(OCCUPANCY);
  assert.deepEqual(occ.encode(1), { occupied: true });
  assert.equal(occ.decode({ occupied: true }), 1);
  assert.equal(occ.decode({ occupied: false }), 0);
  const wide = bitmapCodec(WIDE);
  const raw = 1 + (5 << 1) + (1 << 15);
  assert.deepEqual(wide.encode(raw), { low: true, mode: 5, high: true });
  assert.equal(wide.decode(wide.encode(raw)), raw);
});

test("a bit position the codec cannot read is an error, not a silent zero", () => {
  const bad = { name: "Bad", children: [{ name: "X", constraint: "desc" }] };
  assert.throws(() => bitmapCodec(bad), /cannot read bit position/);
});

test("booleans, numbers and text pass unchanged", () => {
  for (const type of ["binary", "numeric", "text"]) {
    const c = codecFor(type, null);
    assert.equal(c.encode(42), 42);
    assert.equal(c.decode(true), true);
  }
  assert.equal(codecFor("enum", LOCK_STATE).encode("Locked"), 1);
});

test("a feature resolves against the endpoint state and the cluster model", () => {
  const state = { onOff: { onOff: false }, doorLock: { lockState: 2, operatingMode: 0 } };
  const models = { doorlock: { attributes: { lockState: { schema: LOCK_STATE } } } };
  assert.deepEqual(resolveFeature(state, models, "Door Lock.LockState"),
                   { cluster: "doorLock", attribute: "lockState", schema: LOCK_STATE });
  assert.deepEqual(resolveFeature(state, models, "On/Off.OnOff"), { cluster: "onOff", attribute: "onOff", schema: null });
  assert.equal(resolveFeature(state, models, "Color Control.ColorMode"), null);
  assert.equal(resolveFeature(state, models, "Door Lock.DoorState"), null);
  assert.equal(resolveFeature(state, models, "NoDot"), null);
});
```

Create `vesper/exposure/matter_host/recipes.test.mjs`:
```javascript
// node --test: the recipes are data; buildType is checked against stand-ins for matter.js's exports.
import { test } from "node:test";
import assert from "node:assert/strict";
import { norm } from "./codec.mjs";
import { RECIPES, buildType } from "./recipes.mjs";

// The 18 Matter device types the acceptance samples: those with certified products in the spec's L2 categories
// (catalog at DCL height-324961) and at least one Home Assistant entity.
const TYPES = ["Color Temperature Light", "Contact Sensor", "Dimmable Light", "Dimmable Plug-in Unit", "Door Lock",
  "Extended Color Light", "Mounted Dimmable Load Control", "Mounted On/Off Control", "Occupancy Sensor",
  "On/Off Light", "On/Off Plug-in Unit", "Rain Sensor", "Room Air Conditioner", "Smoke CO Alarm",
  "Temperature Sensor", "Thermostat", "Water Leak Detector", "Air Quality Sensor"];

test("there is one recipe per targeted device type", () => {
  assert.deepEqual(Object.keys(RECIPES).sort(), [...TYPES].sort());
});

test("each recipe names the matter.js device of the same Matter device type", () => {
  for (const [zap, r] of Object.entries(RECIPES)) {
    assert.match(r.device, /Device$/, zap);
    assert.equal(norm(r.device.slice(0, -"Device".length)), norm(zap), zap);
  }
});

test("the climate recipes match the modes the acceptance probe asks for", () => {
  // vesper/validation/matter_acceptance.py CLIMATE_MODE: Thermostat -> Heat, Room Air Conditioner -> Cool
  assert.equal(RECIPES["Thermostat"].init.thermostat.controlSequenceOfOperation, 2); // HeatingOnly
  assert.deepEqual(RECIPES["Thermostat"].with, [["ThermostatServer", ["Heating"]]]);
  assert.equal(RECIPES["Room Air Conditioner"].init.thermostat.controlSequenceOfOperation, 0); // CoolingOnly
  assert.deepEqual(RECIPES["Room Air Conditioner"].with[0], ["ThermostatServer", ["Cooling"]]);
});

test("a lock starts unlocked, as the acceptance twin does", () => {
  assert.equal(RECIPES["Door Lock"].init.doorLock.lockState, 2); // Unlocked
});

test("buildType adds each server with its features to the device", () => {
  const calls = [];
  const device = { with: (...servers) => { calls.push(servers); return "built"; } };
  const behaviors = { ThermostatServer: { with: (...f) => `ThermostatServer(${f})` }, FanControlServer: { with: (...f) => `Fan(${f})` } };
  assert.equal(buildType(RECIPES["Room Air Conditioner"], { RoomAirConditionerDevice: device }, behaviors), "built");
  assert.deepEqual(calls, [["ThermostatServer(Cooling)", "Fan()"]]);
  assert.equal(buildType(RECIPES["Contact Sensor"], { ContactSensorDevice: device }, behaviors), device);
  assert.throws(() => buildType(RECIPES["Contact Sensor"], {}, behaviors), /ContactSensorDevice/);
});
```

Create `vesper/exposure/matter_host/stdout-guard.test.mjs`:
```javascript
// node --test: once the guard is imported, only protocol messages reach stdout.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const guard = fileURLToPath(new URL("./stdout-guard.mjs", import.meta.url));
const child = `
  const { send } = await import(${JSON.stringify(guard)});
  console.log("log line"); console.info("info line"); console.debug("debug line");
  process.stdout.write("raw write\\n");
  send({ event: "ready" });
  console.warn("warning");
`;

test("console output goes to stderr and send() alone writes to stdout", () => {
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", child], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, '{"event":"ready"}\n');
  for (const line of ["log line", "info line", "debug line", "raw write", "warning"]) {
    assert.ok(r.stderr.includes(line), line);
  }
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd vesper/exposure/matter_host && node --test 2>&1 | grep -E '^ℹ (pass|fail)'`

Expected: `ℹ pass 0` then `ℹ fail 3`: each test file stops with `ERR_MODULE_NOT_FOUND` (the module it imports does not exist yet).

- [ ] **Step 3: Implement the pure modules**

Create `vesper/exposure/matter_host/codec.mjs`:
```javascript
// VDS values <-> matter.js values, driven by the attribute's schema in the matter.js model.
//
// VESPER's records name Matter things the way connectedhomeip's ZAP XML does ("Door Lock.LockState" =
// "Unlocked"); matter.js names the same things in camel case (doorLock.lockState = 2). Names are matched
// after normalization: lower case, letters and digits only. Pure functions: schemas are passed in, so the
// tests run without matter.js installed.

export const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");

export function children(schema) {
  return schema.children?.length ? schema.children : (schema.base?.children ?? []);
}

/** A VDS enum name <-> its numeric code. */
export function enumCodec(schema) {
  const byName = new Map(children(schema).map((c) => [norm(c.name), c.id]));
  const byCode = new Map(children(schema).map((c) => [c.id, c.name]));
  return {
    encode(name) {
      const code = byName.get(norm(name));
      if (code === undefined) throw new Error(`${JSON.stringify(name)} is not a value of ${schema.name}`);
      return code;
    },
    decode: (code) => byCode.get(code) ?? code,
  };
}

/** A bitmap's fields: single bits ("3") or ranges ("4 to 6"), keyed as matter.js keys them. */
export function bitmapFields(schema) {
  return children(schema).map((c) => {
    const r = /^(\d+)(?:\s*to\s*(\d+))?$/.exec(String(c.constraint).trim());
    if (!r) throw new Error(`cannot read bit position ${c.constraint} of ${schema.name}.${c.name}`);
    const lo = Number(r[1]);
    return { key: c.name.charAt(0).toLowerCase() + c.name.slice(1), lo, hi: r[2] === undefined ? lo : Number(r[2]) };
  });
}

/** A VDS bitmap integer <-> the object matter.js keeps ({occupied: true}). */
export function bitmapCodec(schema) {
  const fields = bitmapFields(schema);
  return {
    encode: (n) => Object.fromEntries(fields.map((f) => {
      const v = Math.floor(n / 2 ** f.lo) % 2 ** (f.hi - f.lo + 1);
      return [f.key, f.lo === f.hi ? v === 1 : v];
    })),
    decode: (o) => fields.reduce((n, f) => n + (f.lo === f.hi ? (o?.[f.key] ? 1 : 0) : (o?.[f.key] ?? 0)) * 2 ** f.lo, 0),
  };
}

const IDENTITY = { encode: (v) => v, decode: (v) => v };

/** The codec for one VDS feature type; `schema` is needed only for enums and bitmaps. */
export function codecFor(type, schema) {
  if (type === "enum") return enumCodec(schema);
  if (type === "bitmap") return bitmapCodec(schema);
  return IDENTITY;
}

/** The key of `object` whose normalized name is `name`'s ("On/Off" finds "onOff"), or undefined. */
export function findKey(object, name) {
  return Object.keys(object ?? {}).find((k) => norm(k) === norm(name));
}

/**
 * Resolve a VDS feature name ("Cluster.Attribute") against an endpoint's state: {cluster, attribute, schema}
 * or null. `models` maps normalized cluster names to matter.js cluster models ({attributes: {name: {schema}}}).
 */
export function resolveFeature(state, models, featureName) {
  const dot = featureName.indexOf(".");
  if (dot < 0) return null;
  const cluster = findKey(state, featureName.slice(0, dot));
  const attribute = cluster && findKey(state[cluster], featureName.slice(dot + 1));
  if (!attribute) return null;
  const model = models[norm(featureName.slice(0, dot))];
  const modelKey = model && findKey(model.attributes, attribute);
  return { cluster, attribute, schema: modelKey ? model.attributes[modelKey].schema : null };
}
```

Create `vesper/exposure/matter_host/recipes.mjs`:
```javascript
// One recipe per Matter device type (named as in connectedhomeip's ZAP XML): the matter.js device, the
// servers and features it needs beyond matter.js's defaults, and the initial values Matter requires there.
//
// Probed offline against matter.js 0.17.9 on 2026-10-23: with these recipes every endpoint initializes and
// every feature of the catalog's records for the type resolves. Without them, colour lights, the door lock,
// the room air conditioner and the air quality sensor fail Matter's conformance checks ("Matter requires you
// to set this attribute"); occupancy and smoke sensors and thermostats start without their mandatory cluster.

const COLOR_TEMPERATURE = {
  colorMode: 2, enhancedColorMode: 2, colorTempPhysicalMinMireds: 153, colorTempPhysicalMaxMireds: 500,
  coupleColorTempToLevelMinMireds: 153, startUpColorTemperatureMireds: null, colorTemperatureMireds: 250,
};

export const RECIPES = {
  "On/Off Light": { device: "OnOffLightDevice" },
  "Dimmable Light": { device: "DimmableLightDevice" },
  "Color Temperature Light": {
    device: "ColorTemperatureLightDevice", with: [["ColorControlServer", ["ColorTemperature"]]],
    init: { colorControl: { ...COLOR_TEMPERATURE } },
  },
  "Extended Color Light": {
    device: "ExtendedColorLightDevice", with: [["ColorControlServer", ["ColorTemperature", "Xy"]]],
    init: { colorControl: { ...COLOR_TEMPERATURE, currentX: 24939, currentY: 24701 } },
  },
  "On/Off Plug-in Unit": { device: "OnOffPlugInUnitDevice" },
  "Dimmable Plug-in Unit": { device: "DimmablePlugInUnitDevice" },
  "Mounted On/Off Control": { device: "MountedOnOffControlDevice" },
  "Mounted Dimmable Load Control": { device: "MountedDimmableLoadControlDevice" },
  "Contact Sensor": { device: "ContactSensorDevice" },
  "Water Leak Detector": { device: "WaterLeakDetectorDevice" },
  "Rain Sensor": { device: "RainSensorDevice" },
  "Occupancy Sensor": { device: "OccupancySensorDevice", with: [["OccupancySensingServer", ["PassiveInfrared"]]] },
  "Temperature Sensor": { device: "TemperatureSensorDevice" },
  "Smoke CO Alarm": { device: "SmokeCoAlarmDevice", with: [["SmokeCoAlarmServer", ["SmokeAlarm"]]] },
  "Air Quality Sensor": {
    device: "AirQualitySensorDevice",
    with: [["AirQualityServer", ["Fair", "Moderate", "VeryPoor", "ExtremelyPoor"]]], // every VDS level is valid
    init: { airQuality: { airQuality: 0 } },
  },
  "Door Lock": {
    device: "DoorLockDevice",
    init: { doorLock: { lockType: 0, actuatorEnabled: true, lockState: 2, operatingMode: 0, wrongCodeEntryLimit: 3,
                        userCodeTemporaryDisableTime: 10 } }, // lockState 2: Unlocked
  },
  "Thermostat": { // heats only: controlSequenceOfOperation 2 (HeatingOnly)
    device: "ThermostatDevice", with: [["ThermostatServer", ["Heating"]]],
    init: { thermostat: { localTemperature: 2000, controlSequenceOfOperation: 2, systemMode: 4 } },
  },
  "Room Air Conditioner": { // cools only: controlSequenceOfOperation 0 (CoolingOnly)
    device: "RoomAirConditionerDevice", with: [["ThermostatServer", ["Cooling"]], ["FanControlServer", []]],
    init: { thermostat: { localTemperature: 2400, controlSequenceOfOperation: 0, systemMode: 3 },
            fanControl: { fanModeSequence: 0, fanMode: 0, percentSetting: 0, percentCurrent: 0 } },
  },
};

/** The endpoint type for a recipe, from matter.js's `devices` and `behaviors` exports. */
export function buildType(recipe, devices, behaviors) {
  const device = devices[recipe.device];
  if (!device) throw new Error(`matter.js has no ${recipe.device}`);
  if (!recipe.with?.length) return device;
  return device.with(...recipe.with.map(([server, features]) => behaviors[server].with(...features)));
}
```

Create `vesper/exposure/matter_host/stdout-guard.mjs`:
```javascript
// Imported first by host.mjs. matter.js logs through console.debug/info/log, which Node writes to stdout, but
// stdout carries the host's JSON-lines protocol. Everything else written to stdout goes to stderr (the host's
// log) instead; `send` writes protocol messages to the real stdout.
import { format } from "node:util";

const write = process.stdout.write.bind(process.stdout);
for (const name of ["log", "info", "debug", "trace"]) {
  console[name] = (...args) => process.stderr.write(format(...args) + "\n");
}
process.stdout.write = (chunk, ...rest) => process.stderr.write(chunk, ...rest);

/** One protocol message as one line on the real stdout. */
export const send = (message) => write(JSON.stringify(message) + "\n");
```

- [ ] **Step 4: Run them and watch them pass**

Run: `cd vesper/exposure/matter_host && node --test 2>&1 | grep -E '^ℹ (pass|fail)'`

Expected: `ℹ pass 12` then `ℹ fail 0`

- [ ] **Step 5: The host, its pinned packages and its contract test**

Create `vesper/exposure/matter_host/host.mjs`:
```javascript
// VESPER's Matter host (spec §2 Matter tier): every Matter twin as a real matter.js node, all in this process.
//
// Protocol (vesper/exposure/matter_tier.py): JSON lines. A request {id, op, ...} gets {id, ok, ...} or
// {id, ok: false, error}. Events carry no id: {event: "ready"} once, then {event: "changed", twin, feature,
// value} whenever a controller (Home Assistant's Matter Server) changes a twin's attribute. Values on the wire
// are VDS values; codec.mjs maps them to matter.js and back.
//
// Environment: MATTER_STORAGE_PATH (node storage), MATTER_MDNS_NETWORKINTERFACE (the interface nodes announce
// on; Matter cannot use loopback), VESPER_MATTER_OFFLINE=1 (build nodes but never start them: tests only).
import { send } from "./stdout-guard.mjs"; // first: keeps matter.js's console logging off the protocol stream
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { ServerNode } from "@matter/main";
import * as behaviors from "@matter/main/behaviors";
import * as clusters from "@matter/main/clusters";
import * as devices from "@matter/main/devices";
import { codecFor, norm, resolveFeature } from "./codec.mjs";
import { RECIPES, buildType } from "./recipes.mjs";

const OFFLINE = process.env.VESPER_MATTER_OFFLINE === "1";
const MODELS = Object.fromEntries(Object.entries(clusters).filter(([, c]) => c?.Complete?.attributes)
  .map(([name, c]) => [norm(name.replace(/Cluster$/, "")), { attributes: c.Complete.attributes }]));
const nodes = new Map(); // twin id -> {node, endpoint, features: Map(name -> {cluster, attribute, codec})}
const message = (e) => String(e?.message ?? e).slice(0, 500);
const storageId = (twin) => "n" + Buffer.from(twin).toString("hex").slice(0, 120); // a file-safe, unique name

function nodeOf(twin) {
  const n = nodes.get(twin);
  if (!n) throw new Error(`no node for twin ${twin}`);
  return n;
}

function featureOf(n, twin, feature) {
  const f = n.features.get(feature);
  if (!f) throw new Error(`${twin} has no exposed feature ${feature}`);
  return f;
}

async function add(r) {
  const recipe = RECIPES[r.device_type];
  if (!recipe) throw new Error(`no recipe for device type ${JSON.stringify(r.device_type)}`);
  if (nodes.has(r.twin)) throw new Error(`twin ${r.twin} already has a node`);
  const node = await ServerNode.create({
    id: storageId(r.twin),
    network: { port: r.port },
    commissioning: { passcode: r.passcode, discriminator: r.discriminator },
    productDescription: { name: r.product_name, deviceType: devices[recipe.device].deviceType },
    basicInformation: {
      vendorName: r.vendor_name, vendorId: r.vendor_id, productName: r.product_name, productLabel: r.product_name,
      productId: r.product_id, serialNumber: r.serial, nodeLabel: r.label, hardwareVersion: 1, softwareVersion: 1,
    },
  });
  try {
    const endpoint = await node.add(buildType(recipe, devices, behaviors), { id: "ep1", ...(recipe.init ?? {}) });
    const features = new Map();
    const unresolved = [];
    for (const f of r.features) {
      const where = resolveFeature(endpoint.state, MODELS, f.name);
      if (!where || ((f.type === "enum" || f.type === "bitmap") && !where.schema)) {
        unresolved.push(f.name);
        continue;
      }
      features.set(f.name, { cluster: where.cluster, attribute: where.attribute, codec: codecFor(f.type, where.schema) });
    }
    const initialErrors = [];
    for (const [name, value] of Object.entries(r.state ?? {})) {
      const f = features.get(name);
      if (!f || value === null || value === undefined) continue;
      try {
        await endpoint.set({ [f.cluster]: { [f.attribute]: f.codec.encode(value) } });
      } catch (e) {
        initialErrors.push(`${name}: ${message(e)}`);
      }
    }
    for (const [name, f] of features) {
      endpoint.events[f.cluster]?.[`${f.attribute}$Changed`]?.on((value, _old, context) => {
        if (context?.offline) return; // VESPER's own write (op "set"), not a controller's
        send({ event: "changed", twin: r.twin, feature: name, value: f.codec.decode(value) });
      });
    }
    if (!OFFLINE) await node.start(); // resolves once online and commissionable (announced by mDNS)
    nodes.set(r.twin, { node, endpoint, features });
    const codes = node.state.commissioning.pairingCodes;
    return { resolved: [...features.keys()], unresolved, initial_errors: initialErrors,
             pairing: { manual: codes.manualPairingCode, qr: codes.qrPairingCode } };
  } catch (e) {
    await node.close().catch(() => {});
    throw e;
  }
}

async function set({ twin, feature, value }) {
  const n = nodeOf(twin);
  const f = featureOf(n, twin, feature);
  await n.endpoint.set({ [f.cluster]: { [f.attribute]: f.codec.encode(value) } });
  return {};
}

function get({ twin }) {
  const n = nodeOf(twin);
  const values = {};
  for (const [name, f] of n.features) values[name] = f.codec.decode(n.endpoint.state[f.cluster][f.attribute]);
  return { values };
}

async function remove({ twin }) {
  const n = nodeOf(twin);
  nodes.delete(twin);
  await n.node.close();
  return {};
}

/** Ask the Matter Server at `url` to commission a node by its pairing code (its WebSocket API). */
function commission({ url, code, seconds }) {
  return new Promise((resolve, reject) => {
    let ws;
    const fail = (why) => { clearTimeout(timer); try { ws?.close(); } catch {} reject(new Error(`commission: ${why}`)); };
    const timer = setTimeout(() => fail(`no answer from ${url} within ${seconds} s`), seconds * 1000);
    try {
      ws = new WebSocket(url);
    } catch (e) {
      fail(message(e));
      return;
    }
    ws.onerror = (e) => fail(`cannot reach the Matter Server at ${url}: ${message(e?.error ?? e?.message ?? "error")}`);
    ws.onopen = () => ws.send(JSON.stringify({ message_id: "vesper-1", command: "commission_with_code",
                                               args: { code, network_only: true } }));
    ws.onmessage = (m) => {
      let reply;
      try { reply = JSON.parse(m.data); } catch { return; }
      if (reply.message_id !== "vesper-1") return; // the server's greeting and events
      clearTimeout(timer);
      ws.close();
      if (reply.error_code !== undefined) reject(new Error(`commission: ${reply.details ?? `error ${reply.error_code}`}`));
      else resolve({ node_id: Number(reply.result?.node_id) });
    };
  });
}

async function shutdown() {
  const all = [...nodes.values()];
  nodes.clear();
  await Promise.allSettled(all.map((n) => n.node.close()));
  return {};
}

const OPS = { add, set, get, remove, commission, shutdown };

async function answer(req) {
  const op = OPS[req.op];
  try {
    if (!op) throw new Error(`unknown op ${JSON.stringify(req.op)}`);
    send({ id: req.id, ok: true, ...(await op(req)) });
  } catch (e) {
    send({ id: req.id, ok: false, error: message(e) });
  }
}

const input = createInterface({ input: process.stdin });
let queue = Promise.resolve(); // node requests run one at a time, in order: a set never overtakes its add
input.on("line", (line) => {
  let req;
  try { req = JSON.parse(line); } catch { process.stderr.write(`unparseable request: ${line.slice(0, 200)}\n`); return; }
  if (req.op === "commission") { // minutes long and touches no node here: it must not hold up the twins' reports
    answer(req);
    return;
  }
  queue = queue.then(() => answer(req)).then(() => { if (req.op === "shutdown") process.exit(0); });
});
input.on("close", () => { queue = queue.then(shutdown).then(() => process.exit(0)); });

const pins = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")).dependencies;
send({ event: "ready", packages: pins, offline: OFFLINE });
```

Create `vesper/exposure/matter_host/package.json`:
```json
{
  "name": "vesper-matter-host",
  "private": true,
  "type": "module",
  "description": "VESPER's Matter tier: twins as matter.js nodes, and the Matter Server Home Assistant uses",
  "license": "MIT",
  "engines": {
    "node": ">=22.13.0"
  },
  "scripts": {
    "test": "node --test"
  },
  "dependencies": {
    "@matter/main": "0.17.9",
    "@matter/nodejs": "0.17.9",
    "matter-server": "1.4.0"
  }
}
```

Generate the lock from the npm registry (metadata only: nothing is installed and no script runs), then check the three pins:

Run: `cd vesper/exposure/matter_host && npm install --package-lock-only --ignore-scripts --no-audit --no-fund >/dev/null && node -e "const l=require('./package-lock.json').packages; console.log(['@matter/main','@matter/nodejs','matter-server'].map(n=>n+'@'+l['node_modules/'+n].version).join(' '))"`

Expected: `@matter/main@0.17.9 @matter/nodejs@0.17.9 matter-server@1.4.0`. (The plan was tested with the lock npm resolved on 2026-10-23: lockfile version 3, 198 packages.)

Run: `cd vesper/exposure/matter_host && node --check host.mjs && echo ok`

Expected: `ok`

Create `tests/exposure/test_matter_host_contract.py`:
```python
"""The real Matter host (host.mjs on matter.js), offline: nodes are built but never started, so no socket is
opened and nothing is announced by mDNS (VESPER_MATTER_OFFLINE=1).

Opt-in: VESPER_MATTER_HOST_TESTS=1, with the host installed by `vesper setup matter` (or VESPER_MATTER_HOST_DIR
naming an installed copy). With catalog/vds present, every device type the Matter acceptance can sample is built.
"""
import json
import os
import socket
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.catalog.test_matter_normalize import records as matter_records
from vesper.exposure.matter_tier import Host, HostError, node_spec
from vesper.validation.matter_acceptance import select_matter_products

pytestmark = pytest.mark.skipif(not os.environ.get("VESPER_MATTER_HOST_TESTS"),
                                reason="opt-in: VESPER_MATTER_HOST_TESTS=1 (needs `vesper setup matter`)")
HOST_DIR = Path(os.environ.get("VESPER_MATTER_HOST_DIR", ".vesper/matter/host"))


@pytest.fixture
def host(tmp_path, monkeypatch):
    if not (HOST_DIR / "node_modules").is_dir():
        pytest.skip(f"no installed host in {HOST_DIR}; run `vesper setup matter`")
    monkeypatch.setenv("VESPER_MATTER_OFFLINE", "1")
    h = Host(tmp_path / "nodes", command=["node", str(HOST_DIR / "host.mjs")], timeout=120,
             log=tmp_path / "host.log")
    h.start()
    yield h
    h.stop()


def add(host, record, index, state=None):
    spec = node_spec(record, record["id"], index)
    feats = [f for f in record["features"] if f["type"] != "unsupported"]
    fields = {k: v for k, v in asdict(spec).items() if k != "twin_id"}
    return host.request("add", twin=record["id"], **fields, features=[{"name": f["name"], "type": f["type"]}
                                                                         for f in feats], state=state or {})


def test_a_node_resolves_its_features_and_has_pairing_codes(host):
    reply = add(host, matter_records()["matter:100b:0018"], 0, {"On/Off.OnOff": True})
    assert reply["unresolved"] == [] and reply["initial_errors"] == []
    assert reply["pairing"]["qr"].startswith("MT:") and len(reply["pairing"]["manual"]) in (11, 21)
    assert host.request("get", twin="matter:100b:0018")["values"]["On/Off.OnOff"] is True


def test_values_cross_the_codec_both_ways(host):
    lock = matter_records()["matter:115f:0001"]
    add(host, lock, 1)
    host.request("set", twin=lock["id"], feature="Door Lock.LockState", value="Locked")
    host.request("set", twin=lock["id"], feature="Door Lock.OperatingMode", value="Vacation")
    values = host.request("get", twin=lock["id"])["values"]
    assert values["Door Lock.LockState"] == "Locked" and values["Door Lock.OperatingMode"] == "Vacation"
    with pytest.raises(HostError, match="not a value of"):
        host.request("set", twin=lock["id"], feature="Door Lock.LockState", value="Ajar")


def test_a_device_type_without_a_recipe_is_refused(host):
    rec = matter_records()["matter:100b:0016"]
    rec = {**rec, "binding": {"matter": {**rec["binding"]["matter"], "device_type": "Heat Pump"}}}
    with pytest.raises(HostError, match="no recipe for device type \"Heat Pump\""):
        add(host, rec, 2)


def test_a_removed_node_is_gone(host):
    rec = matter_records()["matter:100b:0016"]
    add(host, rec, 3)
    host.request("remove", twin=rec["id"])
    with pytest.raises(HostError, match="no node"):
        host.request("set", twin=rec["id"], feature="Boolean State.StateValue", value=True)


def test_commissioning_against_no_server_fails_quickly(host):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(HostError, match="commission"):
        host.request("commission", timeout=30, url=f"ws://127.0.0.1:{port}/ws", code="MT:Y.K90AFN00KA0648G00",
                     seconds=10)


def test_every_device_type_the_acceptance_can_sample_builds_and_resolves(host):
    path = Path("catalog/vds/matter.json")
    if not path.exists():
        pytest.skip("no built catalog")
    picked = select_matter_products(json.loads(path.read_text()), per_type=1, seed=0)
    problems = {}
    for i, rec in enumerate(picked):
        try:
            reply = add(host, rec, 10 + i)
        except HostError as e:
            problems[rec["binding"]["matter"]["device_type"]] = str(e)
            continue
        if reply["unresolved"] or reply["initial_errors"]:
            problems[rec["binding"]["matter"]["device_type"]] = reply
    assert len(picked) >= 17 and problems == {}
```

Append to `.gitignore`:
```

# npm, if run in the Matter host sources (`vesper setup matter` installs into .vesper/matter/host)
node_modules/
```

Run: `.venv/bin/python -m pytest tests/exposure/test_matter_host_contract.py -q -rs 2>&1 | tail -1`

Expected: `6 skipped` (opt-in: `VESPER_MATTER_HOST_TESTS=1` and an installed host; see Step 7)

- [ ] **Step 6: Lint and commit**

Run: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/exposure/matter_host/package.json vesper/exposure/matter_host/package-lock.json vesper/exposure/matter_host/codec.mjs vesper/exposure/matter_host/recipes.mjs vesper/exposure/matter_host/stdout-guard.mjs vesper/exposure/matter_host/host.mjs vesper/exposure/matter_host/codec.test.mjs vesper/exposure/matter_host/recipes.test.mjs vesper/exposure/matter_host/stdout-guard.test.mjs tests/exposure/test_matter_host_contract.py .gitignore
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(exposure): the Matter host: matter.js nodes behind a JSON-lines protocol

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7 (after the PI's go, in a ledger slot; no node is started): install and run the contract test**

```bash
taskpolicy -b .venv/bin/vesper setup matter          # network: npm ci --ignore-scripts, ~330 MB
VESPER_MATTER_HOST_TESTS=1 taskpolicy -b .venv/bin/python -m pytest tests/exposure/test_matter_host_contract.py -q
```

Expected: `vesper setup matter` prints the three pinned packages; the contract test passes (6 tests). `test_every_device_type_the_acceptance_can_sample_builds_and_resolves` needs the catalog rebuilt with Task 1's crosswalk (`vesper catalog build`) and builds all 18 device types with nothing unresolved.

### Task 7: The run book's Experiment 6

**Files:**
- Modify: `docs/experiments/device-twins-runbook.md`

- [ ] **Step 1: Write Experiment 6 and the Matter set-up, ports and fixes**

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown
# Run book: device-twin experiments (scale tier)
```
with:
```markdown
# Run book: device-twin experiments (scale and Matter tiers)
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

These experiments produce the paper's success criteria 2 and 3 for Zigbee-style twins (spec §1, §4). All are software-only and laptop-sized, and need no Docker:
```
with:
```markdown

These experiments produce the paper's success criteria 2 and 3: for Zigbee-style twins (scale tier) and for Matter twins hosted as real matter.js nodes (Matter tier; spec §1, §4). All are software-only and laptop-sized, and need no Docker:
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown
| Real devices (E2) | L3 (offline, real captures) | `vesper validate realdata` | D, A and X, as pre-registered (amendment 2) |
```
with:
```markdown
| Real devices (E2) | L3 (offline, real captures) | `vesper validate realdata` | D, A and X, as pre-registered (amendment 2) |
| Matter acceptance | L2, Matter tier (LAN mDNS) | `vesper validate matter` | every sampled device type pairs, shows and obeys (Experiment 6) |
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

   This needs `uv` and network access to PyPI. It creates `.vesper/ha/venv` (~1 GB) with Python 3.14, Home Assistant Core 2026.10.2 and the requirements of the `frontend`, `mqtt` and `hassio` integrations. Home Assistant later runs with `--skip-pip`, so it never installs anything itself.
```
with:
```markdown

   This needs `uv` and network access to PyPI. It creates `.vesper/ha/venv` (~1 GB) with Python 3.14, Home Assistant Core 2026.10.2 and the requirements of the `frontend`, `mqtt`, `hassio` and `matter` integrations. Home Assistant later runs with `--skip-pip`, so it never installs anything itself.
4. For the Matter tier only, set up the Matter host once:

       .venv/bin/vesper setup matter

   This needs Node.js 22.13 or newer, npm and network access to the npm registry. It copies `vesper/exposure/matter_host/` to `.vesper/matter/host/` and runs `npm ci --ignore-scripts` there: @matter/main and @matter/nodejs 0.17.9 and matter-server 1.4.0, exactly as `package-lock.json` pins them (198 packages, ~330 MB). No package's install script runs.
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown
- Run every command under `taskpolicy -b`, and stop if memory pressure (`sysctl -n kern.memorystatus_vm_pressure_level`) stays at 2 or more.
- VESPER uses only 127.0.0.1:18830 (Mosquitto) and 127.0.0.1:18123 (Home Assistant). Never pass `--mqtt-port 1883` or `--ha-port 8123`: those belong to another project on this Mac.
```
with:
```markdown
- Run every command under `taskpolicy -b`, and stop if memory pressure (`sysctl -n kern.memorystatus_vm_pressure_level`) stays at 2 or more.
- VESPER uses only 127.0.0.1:18830 (Mosquitto) and 127.0.0.1:18123 (Home Assistant), plus, for the Matter tier, 127.0.0.1:15580 (its Matter Server) and UDP 25540 and up (its nodes). Never pass `--mqtt-port 1883` or `--ha-port 8123`: those belong to another project on this Mac.
- A Matter run also announces its nodes by mDNS on the lab network (Experiment 6): book that slot with the other projects explicitly.
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

## The fidelity report
```
with:
```markdown

## Experiment 6: Matter acceptance (L2, Matter tier)

    taskpolicy -b .venv/bin/vesper validate matter --interface en0 --per-type 1 --seed 0

Pre-registered: `--per-type 1 --seed 0`, with each product's own vendor and product id (`--vid-mode product`, the default).

What it does:
1. Starts VESPER's own Matter Server (the matter.js `matter-server` Home Assistant 2026.10 uses) with a new fabric under `.vesper/matter/runs/<UTC>/server`. It serves its WebSocket on 127.0.0.1:15580 only. Its controller uses a random UDP port.
   - `--enable-test-net-dcl` is required: matter.js nodes carry test attestation certificates (a test PAA signs a PAI and DAC for the product's own vendor and product id), which the server otherwise refuses.
   - `--disable-thread-diagnostics` keeps it from probing the network's Thread border routers.
2. Starts Home Assistant (18123), removes every Matter integration entry and adds a fresh one for the server's URL.
3. Samples one L1 product per Matter device type in the spec's categories (sensors, plugs, lights, locks, thermostats; 18 device types in the current catalog), seeded.
4. Starts one Node process that hosts every sampled twin as a matter.js node. Node *i* listens on UDP 25540 + *i*, announces itself by mDNS on `--interface`, and carries the product's vendor and product id and names, a serial number "VSP-…" and a distinct 12-bit discriminator.
5. For each product in turn, asks the server to commission the node by its QR code, then waits for its entities in Home Assistant, found by the node's serial number.
   - Each expected entity, one per Home Assistant mapping of the twin's features, must exist with the right platform and device class and be available.
   - One probe per entity, aimed at a value different from the current one: a service call through Home Assistant that must reach the twin and come back (light and plug on/off, lock, thermostat mode), or a stimulus on the twin that must show in Home Assistant (contact, presence, temperature, humidity, illuminance).
   - A node the server cannot commission fails its product, and the run goes on. If none of the first 3 nodes can be commissioned, the run stops.
6. Removes every node and stops the Node process, Home Assistant and the server, also after an error, Ctrl-C or SIGTERM.

Options: `--category <name>` (repeatable) restricts the sample; `--vid-mode test` presents Matter's test vendor id (0xFFF1) instead, for a server that refuses test certificates under a real vendor id. The report then says so.

**Network.** Matter cannot use loopback: matter.js skips internal interfaces. So the nodes announce themselves by mDNS on the lab network, visible to other machines, to other projects' Home Assistant instances and to Apple and Google home hubs, which may offer to add them. Commissioning needs the node's setup code, so nobody else can take a node over. On the shared lab Mac, every Matter run needs a slot agreed with the other projects in the ledger. The nodes are gone when the run ends.

Budget (estimate): 18 nodes in one Node process, ~0.5 GB; commissioning takes seconds to a minute per node, so ~15–30 min in all; 1–2 cores.

Output: `results/device-twins/matter-<UTC>.json`, holding:
- `summary`: `products`, `passed`, `device_types`, `device_types_passing` (every product passed and was exercised), `device_types_displayed`, `categories_passing`, `by_device_type`, `by_category`;
- per product: its node id or commissioning error, its serial, the features the host could not expose (`unresolved`), each expected entity's `created`, `available` and `probe`, and the Home Assistant entities no expectation used (`extra_entities`);
- `matter_packages`, `ha_version`, `vid_mode`, `interface`, `seed`, `per_type`, `run_dir` (the server's and the host's logs) and `notes`.

Pass: `passed == products > 0` (exit code 0). The paper reports `device_types_passing` and `device_types_displayed`: leak, rain, smoke and air-quality sensors have no stimulus yet, so they can only be displayed.

L2 (matter-server) is credited only to products whose node passed. All products of a device type share its certified mandatory features, but a product that was not sampled is not credited.

## The fidelity report
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

L2 is credited only to products that passed acceptance.
```
with:
```markdown

L2 is credited only to products that passed acceptance: Zigbee products through the bridge (Experiment 2), Matter products through the Matter Server (Experiment 6).
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown
| a product fails with `created: false` | Home Assistant rejected its discovery message | the reason is in `.vesper/ha/hass.log`; this is a real finding: report it |
```
with:
```markdown
| a product fails with `created: false` | Home Assistant rejected its discovery message | the reason is in `.vesper/ha/hass.log`; this is a real finding: report it |
| `the Matter host is not installed` / `the Matter Server is not installed` | `vesper setup matter` has not run | run it (Node.js 22.13+, npm) |
| `the Matter Server commissioned none of the first 3 nodes` | mDNS does not reach the nodes on `--interface`, or the server refuses their certificates | check the interface (`ifconfig`); read `matter-server.log` and `matter-host.log` in the run directory; try `--vid-mode test` |
| a Matter product fails with `commission_error` | the server could not commission that node | the reason is in the run directory's `matter-server.log`; report it |
```

- [ ] **Step 2: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add docs/experiments/device-twins-runbook.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "docs(runbook): Experiment 6, Matter acceptance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## After the plan: the live run (not part of this plan's tasks)

1. With the PI's go, rebuild the catalog (`vesper catalog build`, ~75 s): Task 1 changed the crosswalk, so every command that runs twins refuses the old catalog until then.
2. `vesper setup matter`, then the contract test (Task 6, Step 7).
3. Book a slot with the other sessions in the ledger: the nodes announce themselves by mDNS on the lab network. Then Experiment 6 of the run book, under `taskpolicy -b`.
