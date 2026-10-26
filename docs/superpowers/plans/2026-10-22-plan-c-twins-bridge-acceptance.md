# Plan C: Twins, the Zigbee2MQTT-compatible bridge and Home Assistant acceptance — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn catalog records into running virtual devices that an unmodified Home Assistant accepts as the real products. Ship the two scale-tier experiments of spec §4 as commands:
- `vesper validate roundtrip` (L0/L1: 0 definition diffs);
- `vesper validate acceptance` (L2: discovery, attributes and control through Home Assistant's own API, at least 8 categories).

**Architecture:**
1. **Twins** (`vesper/twins/`):
   - `Twin.from_vds` gives typed, range-checked state per feature.
   - `Runtime` hosts many twins on one clock (logical for reproducible runs, wall for real platforms). It covers `operate` (human), `platform_set` (platform), `stimulate` (physical), the reporting gate (`min_s`/`change`/`max_s`), two-timestamp events, a latency hook, seeded randomness and faulted-twin isolation.
2. **Exposure** (`vesper/exposure/`): a bridge that speaks Zigbee2MQTT's public MQTT contract and publishes each product's real definition. It also emits Home Assistant MQTT device discovery written from HA's documentation. No GPL-3.0 Zigbee2MQTT code is reused.
3. **Validation** (`vesper/validation/`):
   - `stack` runs VESPER's own Mosquitto (127.0.0.1:18830) and a pinned Home Assistant Core 2026.10.2 in its own Python 3.14 venv (127.0.0.1:18123). It onboards HA headless and configures MQTT through the config-flow REST API.
   - `roundtrip` diffs the published definitions against the source snapshot.
   - `acceptance` samples L1 Zigbee products per category and checks every expected entity, plus one control or stimulus probe per entity.
4. **CLI:**
   - `vesper setup ha`;
   - `vesper twins run <home.yaml>`;
   - `vesper validate roundtrip|acceptance`, which write JSON reports under `results/device-twins/`.

**Tech Stack:** Python ≥3.11 (repo venv 3.12), paho-mqtt 2.1 (MQTT client), httpx (HA REST API), PyYAML (home files); pytest, ruff and Jinja2 (dev only: the in-memory HA renders templates as HA does). Runtime services: native Mosquitto, and Home Assistant Core 2026.10.2 in its own uv venv (Python 3.14).

**Spec:** `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md` (§1 success criteria 2 and 3, §2 units `twins/generator`, `twins/runtime`, `exposure/z2m_bridge` and `validation/`, §3.3, §4 rows L0/L1 and L2 scale tier, §5). Plan B (the catalog) is merged on `main` (8fdf24b).

## Global Constraints

- Platforms: macOS (Apple Silicon) and Linux. CPU only, ≤2 cores, no Docker (spec §1 criterion 6, §5).
- Home Assistant is unmodified, with no VESPER code inside it (spec §1 criterion 2). It runs as a Python install in its own environment (spec §2 Processes).
  - Ruling: spec §2 says "its own Python 3.13 environment", but HA 2026.10.2 requires Python ≥3.14.2, so the venv is Python 3.14 (RKA `jrn_01M5K9ER2V5XE0F9XYE37N42KK`).
- Licensing (spec §5): never redistribute or copy the Zigbee2MQTT application (GPL-3.0). VESPER only implements its public MQTT interface, and its HA discovery is written from Home Assistant's own documentation.
- Ports on the shared Mac: VESPER owns 127.0.0.1:18830 (Mosquitto) and 127.0.0.1:18123 (HA). It never binds or connects to 1883 or 8123, which belong to another project on this Mac. Both services refuse to start on a busy port, so VESPER never shares a broker or an HA instance.
- Shared Mac (`/Users/huanbui/Research/MAC_COORDINATION.md`, `~/Research/BUSY_WINDOWS.json`):
  - every process runs under `taskpolicy -b`, ≤2 cores and <4 GB;
  - **no Home Assistant process before 2026-10-24T02:30Z**;
  - light non-HA jobs only outside 03:50–04:50Z, 19:55–20:15Z and 23:58Z–00:30Z, with a ledger row and a message to the peer sessions first;
  - stop at memory pressure ≥2;
  - no experiment without the PI's explicit go.
- Lint gate: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`, with the rule set pinned in `pyproject.toml` by Task 1. The legacy modules kept by Plan A are outside the gate.
- Never `git add -A`; stage the exact paths each task lists. `git checkout`/`switch` fail on the broken gitlink `data/versioned_data/hssd-hab/.git`; this plan never needs them.

## Review Focus

1. **Friendly names that are not safe topic paths** (`+`, `#`, empty levels, a leading `bridge`, or a trailing `set`/`get`/`availability`) would let one device hijack another's command topic or the bridge's own topics. The bridge refuses them at construction. Pinned by `test_a_friendly_name_that_would_break_the_topic_tree_is_refused` (Task 5).
2. **Threads:** with real paho and the wall clock, commands arrive on the network thread while reports fire on the clock thread. The lock order must never invert: the runtime lock is never held while waiting for the bridge lock. Pinned by `test_commands_on_the_network_thread_and_reports_on_the_clock_thread_do_not_deadlock` (Task 5) and the live broker test (Task 7).
3. **Home Assistant refusing a service call** (HTTP ≥400, for example a value it validates differently) must fail that one probe, with HA's message recorded, not abort the run. An unreachable HA still aborts. Pinned by `test_a_service_call_home_assistant_refuses_fails_that_probe_only`. An entity HA never creates fails its product within the timeout: `test_an_entity_home_assistant_never_creates_fails_the_product_without_hanging` (Task 8).
4. **Ctrl-C or any error mid-run** must still stop the HA and Mosquitto processes VESPER started. Pinned by `test_ctrl_c_during_acceptance_still_stops_home_assistant_and_the_broker` and `test_roundtrip_starts_and_always_stops_its_own_broker` (Task 9).
5. **A Home Assistant left onboarded by an earlier run without its saved credentials** (the 2026-10-23 set-up probe left exactly this) must fail with the recovery step, not an opaque 403. Pinned by `test_an_onboarded_home_assistant_without_saved_credentials_says_how_to_reset` (Task 6).

## Rulings carried into this plan

- `vesper/twins/clock.py` is new. Spec §2 says the runtime "reuses the deterministic logical clock from the existing rule engine", but the kept `vesper/simulation/time_manager.py` advances scaled datetimes through `update(real_dt)` and has no seconds-based `call_at` with a deterministic order for same-time events. Cost if wrong: one adapter.
- `vesper setup ha` replaces spec §5's `[ha]` pip extra: a pip extra installs into VESPER's own environment, and HA needs its own Python 3.14 one. Cost if wrong: a packaging alias later.
- Deferred to Plan E (timing and fidelity report):
  - latency distributions per path (the runtime has the hook; the default is 0);
  - sleepy-device batching;
  - battery drain;
  - acceptance over the full catalog in background batches (this plan checks the stratified sample);
  - `vesper report`.
- Deferred to Plan D: the Matter tier (matter.js nodes). Climate, cover and fan entities are skipped with a recorded reason; acceptance counts only the entities it exposes.
- Plan B carry-over (Task 1): the Zigbee reporting `change` defaults were raw ZCL units (temperature 100 = 1.0 °C), but the twin compares them with values in feature units. A 0.5 °C change would never report. Task 1 converts them.

## Experiments this plan sets up (run only after 2026-10-24T02:30Z, with the PI's go)

The run book is Task 10's `docs/experiments/device-twins-runbook.md`. In short:
1. `vesper setup ha` once (network: PyPI, ~1 GB in `.vesper/ha/venv`).
2. `taskpolicy -b vesper validate roundtrip`: Mosquitto only; 5,836 Zigbee definitions; minutes; 1 core; <0.5 GB.
3. `taskpolicy -b vesper validate acceptance --per-category 5`: HA + Mosquitto + twins; ≤1 h; 1–2 cores; ~1 GB.

---

### Task 1: Reporting thresholds in feature units, and a pinned lint rule set

**Files:**
- Modify: `vesper/catalog/normalize/zigbee.py` (`REPORTING_DEFAULTS`)
- Modify: `tests/catalog/test_zigbee_normalize.py` (append one test)
- Modify: `pyproject.toml` (append `[tool.ruff]`)

**Interfaces:**
- Produces: `behavior.reporting[<feature>].change` in the feature's own units (temperature and humidity `1.0`, illuminance `0` meaning any change). Task 3's reporting gate relies on this.

- [ ] **Step 1: Write the failing test**

In `tests/catalog/test_zigbee_normalize.py`, replace:
```python
    assert again["HALO"] == halo["id"] and again["HALO+"] == plus["id"]
```
with:
```python
    assert again["HALO"] == halo["id"] and again["HALO+"] == plus["id"]


def test_reporting_change_is_in_the_feature_units_not_raw_zcl():
    doc = {"version": "26.123.0", "definitions": [zdef("TH-1", "Acme", [
        {"name": "temperature", "access": 5, "type": "numeric", "property": "temperature", "unit": "°C"},
        {"name": "illuminance", "access": 5, "type": "numeric", "property": "illuminance", "unit": "lx"}])]}
    reporting = zigbee_records(doc)[0]["behavior"]["reporting"]
    assert reporting["temperature"] == {"min_s": 10, "max_s": 3600, "change": 1.0}  # 100 raw = 1.0 °C
    assert reporting["illuminance"]["change"] == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_normalize.py -q`

Expected: `1 failed, 14 passed`; the failure is `assert {'min_s': 10,...'change': 100} == {'min_s': 10,...'change': 1.0}`.

- [ ] **Step 3: Convert the defaults**

In `vesper/catalog/normalize/zigbee.py`, replace:
```python
# Static defaults of zigbee-herdsman-converters lib/reporting at 26.123.0: (min_s, max_s, change).
# A device's own configure() may differ, so every use is listed in `assumed` (spec §3.2).
```
with:
```python
# Static defaults of zigbee-herdsman-converters lib/reporting at 26.123.0: (min_s, max_s, change).
# `change` is converted from raw ZCL units to the feature's own units: temperature and humidity report
# in 0.01 units (100 raw = 1.0); illuminance is logarithmic (10000*log10(lux)+1), so any change counts.
# A device's own configure() may differ, so every use is listed in `assumed` (spec §3.2).
```

In `vesper/catalog/normalize/zigbee.py`, replace:
```python
    "state": (0, 3600, 0), "brightness": (1, 3600, 1), "occupancy": (0, 3600, 0),
    "temperature": (10, 3600, 100), "humidity": (10, 3600, 100), "illuminance": (10, 3600, 5),
    "power": (5, 3600, 1), "battery": (3600, 65000, 0), "lock_state": (0, 3600, 0),
```
with:
```python
    "state": (0, 3600, 0), "brightness": (1, 3600, 1), "occupancy": (0, 3600, 0),
    "temperature": (10, 3600, 1.0), "humidity": (10, 3600, 1.0), "illuminance": (10, 3600, 0),
    "power": (5, 3600, 1), "battery": (3600, 65000, 0), "lock_state": (0, 3600, 0),
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_normalize.py -q`

Expected: `15 passed`.

- [ ] **Step 5: Pin the lint rules**

Newer ruff releases (0.17 here) enable hundreds of rules by default, so an unpinned "ruff clean" drifts with the tool version.

Append to `pyproject.toml`:
```toml

[tool.ruff]
line-length = 120

[tool.ruff.lint]
# Pinned: newer ruff releases enable far more rules by default, so an unpinned "ruff clean" drifts.
select = ["E4", "E7", "E9", "E501", "F"]
```

Run: `.venv/bin/ruff check vesper/catalog vesper/cli.py tests/catalog`

Expected: `All checks passed!` (later tasks widen this to the full gate as their directories appear).

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/catalog/normalize/zigbee.py tests/catalog/test_zigbee_normalize.py pyproject.toml
git diff --cached --name-only   # expect exactly the paths above
git commit -m "fix(catalog): reporting thresholds in feature units; pin ruff rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 2: Clocks and the twin model

**Files:**
- Create: `vesper/twins/__init__.py`, `vesper/twins/clock.py`, `vesper/twins/twin.py`
- Create: `tests/twins/__init__.py` (empty), `tests/twins/conftest.py`, `tests/twins/test_clock.py`, `tests/twins/test_twin.py`

**Interfaces:**
- Consumes: Plan B's `vesper.catalog.normalize.zigbee.zigbee_records(doc) -> list[dict]` and `tests.catalog.fixtures`.
- Produces:
  - `clock.LogicalClock(start=0.0)`: `now()`, `call_at(when, fn)`, `advance_to(when)`, `advance(seconds)`;
  - `clock.WallClock()`: `now()`, `call_at(when, fn)`, `close()`;
  - `twin.TwinError(ValueError)`;
  - `twin.stable_seed(*parts) -> int`;
  - `twin.Twin(vds, *, seed=0, twin_id=None)`, with `.id`, `.vds`, `.features: dict[str, dict]`, `.state: dict`, `.rng`, `Twin.from_vds(vds, seed=0, twin_id=None)`, `.feature(name)`, `.coerce(name, value)`, `.clamp(name, value)` and `.payload() -> dict`;
  - the test fixtures `tests.twins.conftest.records() -> dict[model, vds]` and `vds`.

- [ ] **Step 1: Write the failing tests**

Create `tests/twins/conftest.py`:
```python
"""Real-product VDS records (the catalog fixtures plus a few simple devices) shared by the twin tests."""
import pytest

from tests.catalog.fixtures import BATTERY, LIGHT, POWER, SWITCH, ZIGBEE_DOC, zdef
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
```

Create `tests/twins/test_clock.py`:
```python
import threading

import pytest

from vesper.twins.clock import LogicalClock, WallClock


def test_logical_clock_runs_callbacks_in_time_then_schedule_order():
    clock, fired = LogicalClock(), []
    clock.call_at(2, lambda: fired.append(("b", clock.now())))
    clock.call_at(1, lambda: fired.append(("a", clock.now())))
    clock.call_at(2, lambda: fired.append(("c", clock.now())))
    clock.advance(5)
    assert fired == [("a", 1), ("b", 2), ("c", 2)] and clock.now() == 5
    with pytest.raises(ValueError):
        clock.advance_to(4)


def test_wall_clock_fires_in_time_order():
    clock, fired, done = WallClock(), [], threading.Event()
    t0 = clock.now()
    clock.call_at(t0 + 0.06, lambda: (fired.append("b"), done.set()))
    clock.call_at(t0 + 0.02, lambda: fired.append("a"))
    assert done.wait(2)
    clock.close()
    assert fired == ["a", "b"]
```

Create `tests/twins/test_twin.py`:
```python
import pytest

from vesper.twins.twin import Twin, TwinError, stable_seed


def test_initial_state_follows_each_feature_type(vds):
    t = Twin.from_vds(vds["MCCGQ11LM"])
    assert t.state["contact"] is True  # value_off: closed
    assert t.state["battery"] == 0


def test_binary_values_are_matched_like_zigbee2mqtt_and_toggle_flips(vds):
    t = Twin.from_vds(vds["LED1545G12"])
    assert t.coerce("state", "on") == "ON"
    t.state["state"] = "ON"
    assert t.coerce("state", "TOGGLE") == "OFF"
    with pytest.raises(TwinError, match="is not 'ON' or 'OFF'"):
        t.coerce("state", "blink")


def test_numeric_range_and_type_are_enforced(vds):
    t = Twin.from_vds(vds["LED1545G12"])
    assert t.coerce("brightness", 254) == 254
    with pytest.raises(TwinError, match="outside"):
        t.coerce("brightness", 300)
    with pytest.raises(TwinError, match="not a number"):
        t.coerce("brightness", True)


def test_unknown_feature_and_unmodeled_type_are_rejected(vds):
    t = Twin.from_vds(vds["TRV-1"])
    with pytest.raises(TwinError, match="unknown feature"):
        t.coerce("nope", 1)
    with pytest.raises(TwinError, match="not modeled"):
        t.coerce("schedule", [])


def test_physical_values_saturate_at_the_measuring_range(vds):
    t = Twin.from_vds(vds["MCCGQ11LM"])
    assert t.clamp("battery", 140) == 100


def test_payload_is_nested_and_skips_duplicate_exposes(vds):
    light = Twin.from_vds(vds["LED1545G12"]).payload()
    assert light["color"] == {"x": 0, "y": 0} and light["state"] == "OFF"
    assert Twin.from_vds(vds["TWIN-1"]).payload() == {"contact": True}


def test_seeds_are_stable_across_processes():
    assert stable_seed(0, "zigbee:a:b") == stable_seed(0, "zigbee:a:b") != stable_seed(1, "zigbee:a:b")
```

Also create the empty file `tests/twins/__init__.py`.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins -q`

Expected: collection errors, `ModuleNotFoundError: No module named 'vesper.twins'`.

- [ ] **Step 3: Implement**

Create `vesper/twins/__init__.py`:
```python
"""Virtual-device twins generated from VDS records, and the runtime that hosts them (spec §2, §3.3)."""
```

Create `vesper/twins/clock.py`:
```python
"""Clocks for the twin runtime (spec §3.3 Determinism).

`LogicalClock` runs simulated seconds and reproduces a run exactly. `WallClock` runs real seconds for
real platforms; its callbacks fire on one background thread.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable


class LogicalClock:
    """Callbacks run in (time, scheduling order) as the clock is advanced."""

    def __init__(self, start: float = 0.0):
        self._now = start
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()

    def now(self) -> float:
        return self._now

    def call_at(self, when: float, fn: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (max(when, self._now), next(self._seq), fn))

    def advance_to(self, when: float) -> None:
        if when < self._now:
            raise ValueError("a logical clock cannot run backwards")
        while self._queue and self._queue[0][0] <= when:
            at, _, fn = heapq.heappop(self._queue)
            self._now = at
            fn()
        self._now = when

    def advance(self, seconds: float) -> None:
        self.advance_to(self._now + seconds)


class WallClock:
    """Real seconds since creation. Callbacks fire in time order on one daemon thread."""

    def __init__(self):
        self._t0 = time.monotonic()
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, name="vesper-wallclock", daemon=True)
        self._thread.start()

    def now(self) -> float:
        return time.monotonic() - self._t0

    def call_at(self, when: float, fn: Callable[[], None]) -> None:
        with self._cv:
            heapq.heappush(self._queue, (when, next(self._seq), fn))
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._stopped and (not self._queue or self._queue[0][0] > self.now()):
                    self._cv.wait(None if not self._queue else max(0.0, self._queue[0][0] - self.now()))
                if self._stopped:
                    return
                _, _, fn = heapq.heappop(self._queue)
            fn()

    def close(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify()
        self._thread.join(timeout=5)
```

Create `vesper/twins/twin.py`:
```python
"""One virtual twin built from a VDS record: typed, range-checked state per feature (spec §3.3 State)."""
from __future__ import annotations

import hashlib
import random


class TwinError(ValueError):
    """A command the real platform would reject (read-only feature, bad type, out of range)."""


def stable_seed(*parts: object) -> int:
    """A process-independent seed (Python's hash() is salted per process)."""
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


def _initial(f: dict):
    kind = f["type"]
    if kind == "binary":
        return f.get("value_off", False)
    if kind == "numeric":
        lo, hi = f.get("range", [None, None])
        return 0 if lo is None or lo <= 0 <= hi else lo
    if kind == "enum":
        return (f.get("values") or [None])[0]
    if kind == "bitmap":
        return 0
    return "" if kind == "text" else None


def _same(a, b) -> bool:
    return a == b or (isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower())


class Twin:
    def __init__(self, vds: dict, *, seed: int = 0, twin_id: str | None = None):
        self.id: str = twin_id or vds["id"]  # one product can be installed several times
        self.vds = vds
        self.features: dict[str, dict] = {f["name"]: f for f in vds["features"]}
        self.state: dict = {name: _initial(f) for name, f in self.features.items()}
        self.rng = random.Random(stable_seed(seed, self.id))

    @classmethod
    def from_vds(cls, vds: dict, seed: int = 0, twin_id: str | None = None) -> Twin:
        return cls(vds, seed=seed, twin_id=twin_id)

    def feature(self, name: str) -> dict:
        if name not in self.features:
            raise TwinError(f"{self.id}: unknown feature {name!r}")
        return self.features[name]

    def coerce(self, name: str, value):
        """The value a real device would store for `value`, or TwinError."""
        f = self.feature(name)
        kind = f["type"]
        if kind == "binary":
            if "value_toggle" in f and _same(value, f["value_toggle"]):
                return f["value_off"] if self.state[name] == f["value_on"] else f["value_on"]
            for candidate in (f.get("value_on"), f.get("value_off")):
                if _same(value, candidate):
                    return candidate
            raise TwinError(f"{self.id}.{name}: {value!r} is not {f.get('value_on')!r} or {f.get('value_off')!r}")
        if kind == "numeric":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TwinError(f"{self.id}.{name}: {value!r} is not a number")
            lo, hi = f.get("range", [None, None])
            if lo is not None and not lo <= value <= hi:
                raise TwinError(f"{self.id}.{name}: {value!r} is outside [{lo}, {hi}]")
            return value
        if kind == "enum":
            for candidate in f.get("values") or []:
                if _same(value, candidate):
                    return candidate
            raise TwinError(f"{self.id}.{name}: {value!r} is not one of {f.get('values')!r}")
        if kind == "bitmap":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TwinError(f"{self.id}.{name}: {value!r} is not a bitmap value")
            return value
        if kind == "text":
            if not isinstance(value, str):
                raise TwinError(f"{self.id}.{name}: {value!r} is not text")
            return value
        raise TwinError(f"{self.id}.{name}: feature type {kind!r} is not modeled")

    def clamp(self, name: str, value):
        """A physical quantity saturates at the device's measuring range instead of being rejected."""
        f = self.feature(name)
        if f["type"] == "numeric" and "range" in f and isinstance(value, (int, float)):
            lo, hi = f["range"]
            value = min(max(value, lo), hi)
        return self.coerce(name, value)

    def payload(self) -> dict:
        """Current state as nested JSON (Zigbee2MQTT style); duplicate exposes (`name#2`) are not repeated."""
        out: dict = {}
        for name, value in self.state.items():
            if "#" in name or self.features[name]["type"] == "unsupported":
                continue
            *parents, leaf = name.split(".")
            node = out
            for p in parents:
                node = node.setdefault(p, {})
            node[leaf] = value
        return out
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins -q`

Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/__init__.py vesper/twins/clock.py vesper/twins/twin.py tests/twins/__init__.py tests/twins/conftest.py tests/twins/test_clock.py tests/twins/test_twin.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(twins): logical and wall clocks; typed, range-checked twins from VDS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 3: The twin runtime

**Files:**
- Create: `vesper/twins/runtime.py`, `tests/twins/test_runtime.py`

**Interfaces:**
- Consumes: Task 2's `LogicalClock`, `Twin` and `TwinError`; Task 1's reporting rules.
- Produces:
  - `runtime.OCCUPANCY_TIMEOUT_S = 90.0`;
  - `runtime.Event(twin_id, feature, value, physical_t, observed_t, cause)`;
  - `runtime.Action(t, twin_id, feature, value, actor, accepted, reason)`;
  - `runtime.Runtime(clock=LogicalClock(), seed=0, latency=None)`, with `.twins`, `.faulted`, `.actions`, `.notes`, `add(vds, twin_id=None) -> Twin`, `subscribe(fn)`, `state(twin_id) -> dict`, `operate(twin_id, feature, value, actor="resident") -> bool`, `platform_set(twin_id, feature, value) -> bool` and `stimulate(twin_id, stimulus: dict) -> list[str]`.
  - Stimulus types: `presence`, `contact`, `button_press`, `load` and `ambient`.

- [ ] **Step 1: Write the failing tests**

Create `tests/twins/test_runtime.py`:
```python
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Runtime


def runtime(*records, latency=None):
    rt = Runtime(clock=LogicalClock(), latency=latency)
    events = []
    rt.subscribe(events.append)
    ids = [rt.add(r).id for r in records]
    return rt, events, ids


def test_platform_command_on_a_read_only_feature_is_rejected_and_logged(vds):
    rt, events, (tid,) = runtime(vds["MCCGQ11LM"])
    assert rt.platform_set(tid, "contact", False) is False
    assert rt.actions[-1].accepted is False and "read-only" in rt.actions[-1].reason
    assert rt.state(tid)["contact"] is True and events == []


def test_platform_and_human_commands_are_logged_separately(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"])
    assert rt.platform_set(tid, "state", "ON") and rt.operate(tid, "state", "OFF", actor="alice")
    assert [(a.actor, a.value) for a in rt.actions] == [("platform", "ON"), ("human:alice", "OFF")]
    assert [(e.feature, e.value, e.cause) for e in events] == [("state", "ON", "platform"), ("state", "OFF", "human")]


def test_reports_wait_for_min_interval_and_keep_the_physical_time(vds):
    rt, events, (tid,) = runtime(vds["DIM-1"])  # brightness: min 1 s, max 3600 s, change 1
    rt.clock.advance(0.5)
    rt.platform_set(tid, "brightness", 100)
    assert [e.feature for e in events] == []
    rt.clock.advance(0.5)
    (e,) = events
    assert (e.feature, e.value, e.physical_t, e.observed_t) == ("brightness", 100, 0.5, 1.0)


def test_changes_below_the_reportable_change_are_not_reported(vds):
    rt, events, (tid,) = runtime(vds["TH-1"])  # temperature: min 10 s, change 1.0 °C
    rt.clock.advance(20)
    rt.stimulate(tid, {"type": "ambient", "temperature": 0.5})
    rt.stimulate(tid, {"type": "ambient", "temperature": 21.5})
    assert [(e.feature, e.value) for e in events] == [("temperature", 21.5)]


def test_periodic_reports_follow_max_interval(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"])  # power: max 3600 s
    rt.clock.advance(3600)
    assert ("power", "periodic") in [(e.feature, e.cause) for e in events]


def test_presence_sets_occupancy_and_clears_after_the_timeout(vds):
    rt, events, (tid,) = runtime(vds["PIR-1"])
    assert rt.stimulate(tid, {"type": "presence"}) == ["occupancy"]
    rt.clock.advance(OCCUPANCY_TIMEOUT_S - 1)
    assert rt.state(tid)["occupancy"] is True
    rt.stimulate(tid, {"type": "presence"})  # renewed presence restarts the timeout
    rt.clock.advance(OCCUPANCY_TIMEOUT_S - 1)
    assert rt.state(tid)["occupancy"] is True
    rt.clock.advance(1)
    assert rt.state(tid)["occupancy"] is False
    assert [e.value for e in events if e.feature == "occupancy"] == [True, False]


def test_contact_button_load_and_unknown_stimuli(vds):
    rt, events, (door, button, plug) = runtime(vds["MCCGQ11LM"], vds["BTN-1"], vds["PLUG-1"])
    assert rt.stimulate(door, {"type": "contact", "state": "opened"}) == ["contact"]
    assert rt.state(door)["contact"] is False  # Zigbee2MQTT reports contact=false when open
    rt.stimulate(button, {"type": "button_press", "action": "double"})
    rt.stimulate(button, {"type": "button_press", "action": "double"})
    assert [e.value for e in events if e.feature == "action"] == ["double", "double"]
    rt.clock.advance(5)
    assert rt.stimulate(plug, {"type": "load", "watts": 60}) == ["power"]
    assert rt.stimulate(door, {"type": "ambient", "temperature": 20}) == []
    assert any("no matching feature" in n for n in rt.notes)
    assert rt.stimulate(door, {"type": "earthquake"}) == [] and any("unknown stimulus" in n for n in rt.notes)


def test_latency_separates_physical_and_observed_time(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"], latency=lambda twin, feature: 0.25)
    rt.platform_set(tid, "state", "ON")
    assert events == []
    rt.clock.advance(0.25)
    assert (events[0].physical_t, events[0].observed_t) == (0.0, 0.25)


def test_a_failing_twin_is_isolated(vds, monkeypatch):
    rt, events, (bad, good) = runtime(vds["PLUG-1"], vds["LED1545G12"])
    monkeypatch.setattr(rt.twins[bad], "coerce", lambda *a: 1 / 0)
    assert rt.platform_set(bad, "state", "ON") is False and "ZeroDivisionError" in rt.faulted[bad]
    assert rt.platform_set(good, "state", "ON") is True


def test_a_failing_subscriber_does_not_stop_the_runtime(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"])
    rt.subscribe(lambda e: 1 / 0)
    assert rt.platform_set(tid, "state", "ON") and events and any("subscriber failed" in n for n in rt.notes)


def test_logical_runs_are_reproducible(vds):
    def run():
        rt, events, (a, b) = runtime(vds["PIR-1"], vds["TH-1"])
        for t in range(0, 600, 37):
            rt.clock.advance_to(t)
            rt.stimulate(a, {"type": "presence"})
            rt.stimulate(b, {"type": "ambient", "temperature": 18 + t % 7})
        rt.clock.advance(4000)
        return [(e.twin_id, e.feature, e.value, e.physical_t, e.observed_t, e.cause) for e in events]
    assert run() == run()


def test_one_product_can_be_installed_twice(vds):
    rt = Runtime(clock=LogicalClock())
    a = rt.add(vds["MCCGQ11LM"], twin_id="zigbee:aqara:mccgq11lm@hall")
    b = rt.add(vds["MCCGQ11LM"], twin_id="zigbee:aqara:mccgq11lm@back")
    rt.stimulate(a.id, {"type": "contact", "state": "opened"})
    assert rt.state(a.id)["contact"] is False and rt.state(b.id)["contact"] is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_runtime.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.twins.runtime'`.

- [ ] **Step 3: Implement**

Create `vesper/twins/runtime.py`:
```python
"""Hosts many twins on one clock: human use, platform commands, physical stimuli, reporting (spec §3.3).

Every observable change becomes an `Event` with two timestamps: when it physically happened and when
the platform observes it (after the reporting rule and the latency model). A twin that raises an
unexpected error is marked faulted and isolated; the others keep running.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from vesper.twins.clock import LogicalClock
from vesper.twins.twin import Twin, TwinError

OCCUPANCY_TIMEOUT_S = 90.0  # assumed: Zigbee2MQTT's common occupancy_timeout default; real devices vary


@dataclass(frozen=True)
class Event:
    twin_id: str
    feature: str
    value: object
    physical_t: float  # when the change happened in the world
    observed_t: float  # when the platform sees the report
    cause: str  # "stimulus", "human", "platform" or "periodic"


@dataclass
class _Reporter:
    last_value: object = None
    last_t: float | None = None
    pending: bool = False
    changed_at: float = 0.0
    cause: str = "stimulus"


@dataclass
class Action:
    t: float
    twin_id: str
    feature: str
    value: object
    actor: str  # "human:<who>" or "platform"
    accepted: bool
    reason: str = ""


@dataclass
class Runtime:
    clock: object = field(default_factory=LogicalClock)
    seed: int = 0
    latency: Callable[[Twin, str], float] | None = None  # seconds from report to observation; default 0
    twins: dict[str, Twin] = field(default_factory=dict)
    faulted: dict[str, str] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[Event], None]] = []
        self._reporters: dict[tuple[str, str], _Reporter] = {}
        self._last_presence: dict[str, float] = {}

    # -- set-up -------------------------------------------------------------------------------
    def add(self, vds: dict, twin_id: str | None = None) -> Twin:
        with self._lock:
            if (twin_id or vds["id"]) in self.twins:
                raise ValueError(f"twin {twin_id or vds['id']} already added")
            twin = Twin.from_vds(vds, seed=self.seed, twin_id=twin_id)
            self.twins[twin.id] = twin
            for name in twin.features:
                rule = self._rule(twin, name)
                if rule:
                    self._reporters[(twin.id, name)] = _Reporter(last_value=twin.state[name], last_t=self.clock.now())
                    self._schedule_periodic(twin, name, self.clock.now() + rule["max_s"])
            return twin

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subscribers.append(fn)

    def state(self, twin_id: str) -> dict:
        with self._lock:
            return dict(self.twins[twin_id].state)

    # -- inputs -------------------------------------------------------------------------------
    def operate(self, twin_id: str, feature: str, value, actor: str = "resident") -> bool:
        """A person uses the device (spec §3.3 Human use); logged apart from platform commands."""
        return self._command(twin_id, feature, value, f"human:{actor}", "human")

    def platform_set(self, twin_id: str, feature: str, value) -> bool:
        """A command arriving through an exposure tier; rejected exactly where the platform would reject."""
        return self._command(twin_id, feature, value, "platform", "platform")

    def stimulate(self, twin_id: str, stimulus: dict) -> list[str]:
        """A physical input (spec §3.3): presence, contact, button_press, load or ambient."""
        with self._lock:
            twin = self.twins.get(twin_id)
            if twin is None or twin_id in self.faulted:
                return []
            try:
                changes = self._stimulus_changes(twin, stimulus)
                for name, value in changes:
                    self._change(twin, name, twin.clamp(name, value), "stimulus")
                if stimulus.get("type") == "presence" and stimulus.get("present", True) and changes:
                    self._schedule_presence_clear(twin, [n for n, _ in changes])
                if not changes:
                    self.notes.append(f"{twin_id}: stimulus {stimulus.get('type')!r} has no matching feature")
                return [n for n, _ in changes]
            except TwinError as e:
                self.notes.append(f"{twin_id}: stimulus rejected: {e}")
                return []
            except Exception as e:  # isolate a misbehaving twin
                self.faulted[twin_id] = f"{type(e).__name__}: {e}"
                return []

    # -- internals ----------------------------------------------------------------------------
    def _command(self, twin_id, feature, value, actor, cause) -> bool:
        with self._lock:
            twin = self.twins.get(twin_id)
            if twin is None:
                raise KeyError(twin_id)
            if twin_id in self.faulted:
                self.actions.append(Action(self.clock.now(), twin_id, feature, value, actor, False, "twin faulted"))
                return False
            try:
                f = twin.feature(feature)
                if "set" not in f["access"]:
                    raise TwinError(f"{twin_id}.{feature} is read-only")
                new = twin.coerce(feature, value)
            except TwinError as e:
                self.actions.append(Action(self.clock.now(), twin_id, feature, value, actor, False, str(e)))
                return False
            except Exception as e:
                self.faulted[twin_id] = f"{type(e).__name__}: {e}"
                return False
            self.actions.append(Action(self.clock.now(), twin_id, feature, new, actor, True))
            self._change(twin, feature, new, cause)
            return True

    def _rule(self, twin: Twin, name: str) -> dict | None:
        return twin.vds.get("behavior", {}).get("reporting", {}).get(name)

    def _change(self, twin: Twin, name: str, value, cause: str) -> None:
        old = twin.state[name]
        twin.state[name] = value
        rule = self._rule(twin, name)
        now = self.clock.now()
        if rule is None:
            if value != old or name == "action":  # every button press is an event, even a repeated one
                self._emit(twin, name, value, now, cause)
            return
        rep = self._reporters[(twin.id, name)]
        if not self._significant(rep.last_value, value, rule["change"]):
            return
        if rep.last_t is None or now - rep.last_t >= rule["min_s"]:
            self._report(twin, name, now, cause)
        elif not rep.pending:
            rep.pending, rep.changed_at, rep.cause = True, now, cause
            self.clock.call_at(rep.last_t + rule["min_s"], lambda: self._flush(twin, name))

    @staticmethod
    def _significant(old, new, change) -> bool:
        if isinstance(new, (int, float)) and not isinstance(new, bool) and isinstance(old, (int, float)):
            return abs(new - old) >= change if change else new != old
        return new != old

    def _flush(self, twin: Twin, name: str) -> None:
        with self._lock:
            rep = self._reporters[(twin.id, name)]
            if rep.pending and twin.id not in self.faulted:
                rep.pending = False
                self._report(twin, name, rep.changed_at, rep.cause)

    def _report(self, twin: Twin, name: str, physical_t: float, cause: str) -> None:
        rep = self._reporters[(twin.id, name)]
        rep.last_value, rep.last_t = twin.state[name], self.clock.now()
        self._emit(twin, name, twin.state[name], physical_t, cause)

    def _schedule_periodic(self, twin: Twin, name: str, when: float) -> None:
        def tick():
            with self._lock:
                if twin.id in self.faulted:
                    return
                rule, rep = self._rule(twin, name), self._reporters[(twin.id, name)]
                if rep.last_t is not None and self.clock.now() - rep.last_t >= rule["max_s"]:
                    self._report(twin, name, self.clock.now(), "periodic")
                self._schedule_periodic(twin, name, rep.last_t + rule["max_s"])
        self.clock.call_at(when, tick)

    def _emit(self, twin: Twin, name: str, value, physical_t: float, cause: str) -> None:
        delay = max(0.0, self.latency(twin, name)) if self.latency else 0.0
        event = Event(twin.id, name, value, physical_t, self.clock.now() + delay, cause)
        if delay == 0:
            self._deliver(event)
        else:
            self.clock.call_at(event.observed_t, lambda: self._deliver(event))

    def _deliver(self, event: Event) -> None:
        for fn in self._subscribers:
            try:
                fn(event)
            except Exception as e:  # a failing subscriber must not stop the runtime
                self.notes.append(f"subscriber failed on {event.twin_id}.{event.feature}: {e!r}")

    # -- physical stimuli ---------------------------------------------------------------------
    def _stimulus_changes(self, twin: Twin, s: dict) -> list[tuple[str, object]]:
        kind, feats, out = s.get("type"), twin.features, []

        def binary(name, on):
            f = feats.get(name)
            if f and f["type"] == "binary":
                out.append((name, f["value_on"] if on else f["value_off"]))

        if kind == "presence":
            for name in ("occupancy", "presence"):
                binary(name, s.get("present", True))
        elif kind == "contact":
            if s.get("state") not in ("opened", "closed"):
                raise TwinError(f"contact stimulus needs state 'opened' or 'closed', got {s.get('state')!r}")
            binary("contact", s["state"] == "opened")  # Zigbee2MQTT's contact is "on" (value_on) when open
        elif kind == "button_press":
            f = feats.get("action")
            if f and f["type"] == "enum" and f.get("values"):
                wanted = s.get("action")
                out.append(("action", wanted if wanted in f["values"] else f["values"][0]))
        elif kind == "load":
            if "power" in feats:
                out.append(("power", float(s["watts"])))
        elif kind == "ambient":
            for key, names in (("temperature", ("temperature", "local_temperature")), ("humidity", ("humidity",)),
                               ("illuminance", ("illuminance", "illuminance_lux"))):
                if key in s:
                    out.extend((n, s[key]) for n in names if n in feats and feats[n]["type"] == "numeric")
        else:
            raise TwinError(f"unknown stimulus type {kind!r}")
        return out

    def _schedule_presence_clear(self, twin: Twin, names: list[str]) -> None:
        stamp = self.clock.now()
        self.notes.append(f"{twin.id}: occupancy clears after {OCCUPANCY_TIMEOUT_S:.0f} s (assumed timeout)")
        self._last_presence[twin.id] = stamp

        def clear():
            with self._lock:
                if self._last_presence.get(twin.id) == stamp and twin.id not in self.faulted:
                    for name in names:
                        self._change(twin, name, twin.features[name]["value_off"], "stimulus")
        self.clock.call_at(stamp + OCCUPANCY_TIMEOUT_S, clear)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins -q`

Expected: `21 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/runtime.py tests/twins/test_runtime.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(twins): runtime with reporting gate, stimuli, two-timestamp events and fault isolation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 4: Home Assistant MQTT device discovery

**Files:**
- Create: `vesper/exposure/__init__.py`, `vesper/exposure/ha_discovery.py`
- Create: `tests/exposure/__init__.py` (empty), `tests/exposure/conftest.py`, `tests/exposure/test_ha_discovery.py`

**Interfaces:**
- Consumes: VDS features with `ha`, `path`, `access`, `range`, `values`, `value_on/off` and `category` (Plan B).
- Produces:
  - `ha_discovery.DISCOVERY_PREFIX = "homeassistant"`;
  - `ExpectedEntity(entity_id, platform, device_class, unit, feature, controllable)`;
  - `object_id(ieee, feature) -> str`;
  - `event_topic(state_topic, feature) -> str`;
  - `device_discovery(vds, *, ieee, friendly_name, base_topic="zigbee2mqtt") -> (topic, payload, expected: list[ExpectedEntity], skipped: list[(feature, reason)])`.

- [ ] **Step 1: Write the failing tests**

Create `tests/exposure/conftest.py`:
```python
"""The exposure tests use the same product records as the twin tests."""
from tests.twins.conftest import records, vds  # noqa: F401  (vds is a fixture)
```

Create `tests/exposure/test_ha_discovery.py`:
```python
import json

import jinja2

from vesper.exposure.ha_discovery import device_discovery, object_id

IEEE = "0x00124b0000abcdef"


def render(template, payload):
    return jinja2.Environment().from_string(template).render(value_json=payload, value=payload)


def discover(vds, name="dev"):
    return device_discovery(vds, ieee=IEEE, friendly_name=name)


def components(vds):
    return discover(vds)[1]["cmps"]


def test_device_message_topic_device_and_shared_topics(vds):
    topic, payload, expected, skipped = discover(vds["MCCGQ11LM"], "hall/door")
    assert topic == f"homeassistant/device/{IEEE}/config"
    assert payload["dev"] == {"ids": [f"zigbee2mqtt_{IEEE}"], "name": "hall/door", "mf": "Aqara", "mdl": "MCCGQ11LM"}
    assert payload["state_topic"] == "zigbee2mqtt/hall/door" and payload["o"]["name"] == "VESPER"
    assert payload["availability"][0]["topic"] == "zigbee2mqtt/hall/door/availability"


def test_contact_is_a_door_binary_sensor_that_reads_zigbee2mqtt_semantics(vds):
    door = components(vds["MCCGQ11LM"])[object_id(IEEE, "contact")]
    assert door["p"] == "binary_sensor" and door["device_class"] == "door"
    assert render(door["value_template"], {"contact": False}) == "ON"  # contact=false means open
    assert render(door["value_template"], {"contact": True}) == "OFF"


def test_numeric_sensors_carry_class_unit_state_class_and_category(vds):
    battery = components(vds["MCCGQ11LM"])[object_id(IEEE, "battery")]
    assert (battery["device_class"], battery["unit_of_measurement"], battery["state_class"],
            battery["entity_category"]) == ("battery", "%", "measurement", "diagnostic")
    assert render(battery["value_template"], {"battery": 87}) == "87"


def test_switch_commands_are_zigbee2mqtt_set_payloads(vds):
    plug = components(vds["PLUG-1"])[object_id(IEEE, "state")]
    assert plug["p"] == "switch" and plug["command_topic"] == "zigbee2mqtt/dev/set"
    assert json.loads(plug["payload_on"]) == {"state": "ON"} and plug["name"] is None
    assert render(plug["value_template"], {"state": "ON"}) == "ON"


def test_light_is_one_json_schema_entity(vds):
    cmps = components(vds["LED1545G12"])
    light = cmps[object_id(IEEE, "light")]
    assert light["schema"] == "json" and light["brightness"] is True and light["brightness_scale"] == 254
    assert light["supported_color_modes"] == ["xy"]
    assert not any(c["p"] == "number" for c in cmps.values())  # brightness and colour belong to the light


def test_button_actions_are_events_on_their_own_topic(vds):
    event = components(vds["BTN-1"])[object_id(IEEE, "action")]
    assert event["p"] == "event" and event["state_topic"] == "zigbee2mqtt/dev/event/action"
    assert event["event_types"] == ["single", "double", "hold"]


def test_unexposed_features_are_listed_with_a_reason(vds):
    _, payload, expected, skipped = discover(vds["TRV-1"])
    assert ("local_temperature", "climate entities are not exposed yet") in skipped
    assert ("schedule", "feature type not modeled") in skipped
    assert payload["cmps"] == {} and expected == []


def test_expected_entities_match_default_entity_ids(vds):
    _, payload, expected, _ = discover(vds["PLUG-1"])
    ids = {e.entity_id for e in expected}
    assert ids == {f"{c['p']}.{oid}" for oid, c in payload["cmps"].items()}
    assert {e.platform: e.controllable for e in expected} == {"switch": True, "sensor": False}


def test_number_and_select_command_templates_render_to_json():
    vds = {"identity": {"vendor": "V", "model": "M"}, "features": [
        {"name": "countdown", "type": "numeric", "access": ["state", "set"], "category": None, "ha": "number",
         "source": "s", "range": [0, 43200]},
        {"name": "power_on_behavior", "type": "enum", "access": ["state", "set"], "category": None, "ha": "select",
         "source": "s", "values": ["on", "off"]}]}
    cmps = device_discovery(vds, ieee=IEEE, friendly_name="p")[1]["cmps"]
    number, select = cmps[object_id(IEEE, "countdown")], cmps[object_id(IEEE, "power_on_behavior")]
    assert json.loads(render(number["command_template"], 30)) == {"countdown": 30}
    assert json.loads(render(select["command_template"], "off")) == {"power_on_behavior": "off"}
    assert (number["min"], number["max"]) == (0, 43200)
```

Also create the empty file `tests/exposure/__init__.py`.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/exposure/test_ha_discovery.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.exposure'`.

- [ ] **Step 3: Implement**

Create `vesper/exposure/__init__.py`:
```python
"""Exposure tiers: interfaces that real platforms accept as the product (spec §2, L2)."""
```

Create `vesper/exposure/ha_discovery.py`:
```python
"""Home Assistant MQTT device discovery for twins behind the Zigbee2MQTT-compatible bridge.

Written from Home Assistant's MQTT discovery documentation (one `device` discovery message per twin).
It does not copy Zigbee2MQTT's GPL-3.0 Home Assistant extension (spec §5 Licensing).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from vesper import __version__

DISCOVERY_PREFIX = "homeassistant"
_MARK = "__VESPER_VALUE__"


@dataclass(frozen=True)
class ExpectedEntity:
    """An entity Home Assistant should create from the discovery message (used by acceptance checks)."""

    entity_id: str
    platform: str
    device_class: str | None
    unit: str | None
    feature: str  # the VDS feature behind the entity (a light's or lock's "state")
    controllable: bool


def object_id(ieee: str, feature: str) -> str:
    return f"v{ieee[2:]}_" + (re.sub(r"[^a-z0-9]+", "_", feature.lower()).strip("_") or "x")


def event_topic(state_topic: str, feature: str) -> str:
    return f"{state_topic}/event/{re.sub(r'[^a-z0-9]+', '_', feature.lower()).strip('_')}"


def _accessor(name: str) -> str:
    return "value_json" + "".join(f"[{json.dumps(part)}]" for part in name.split("."))


def _nested(name: str, value) -> dict:
    *parents, leaf = name.split(".")
    out = node = {}
    for p in parents:
        node = node.setdefault(p, {})
    node[leaf] = value
    return out


def _command_template(name: str, quote: bool) -> str:
    text = json.dumps(_nested(name, _MARK))
    return text.replace(f'"{_MARK}"', '"{{ value }}"' if quote else "{{ value }}")


def _on_off_template(f: dict) -> str:
    return f"{{{{ 'ON' if {_accessor(f['name'])} == {json.dumps(f['value_on'])} else 'OFF' }}}}"


def _component(f: dict, ieee: str, state_topic: str, set_topic: str):
    """(object id, component config, expected entity) or a reason string."""
    platform, _, cls = f["ha"].partition("/")
    oid, name, unit = object_id(ieee, f["name"]), f["name"], f.get("unit")
    cfg = {"p": platform, "unique_id": oid, "default_entity_id": f"{platform}.{oid}",
           "name": None if f["name"] == "state" else name.replace("_", " ").replace(".", " ")}
    if f.get("category") in ("config", "diagnostic"):
        cfg["entity_category"] = f["category"]
    value = f"{{{{ {_accessor(name)} }}}}"
    controllable = True
    if platform == "binary_sensor":
        cfg["value_template"] = _on_off_template(f)
        controllable = False
    elif platform == "sensor":
        cfg["value_template"] = value
        controllable = False
        if cls == "enum":
            cfg["options"] = [str(v) for v in f.get("values", [])]
        elif unit:
            cfg["unit_of_measurement"] = unit
        if f["type"] == "numeric" and cls != "enum":
            cfg["state_class"] = "total_increasing" if cls == "energy" else "measurement"
    elif platform == "switch":
        cfg |= {"command_topic": set_topic, "value_template": _on_off_template(f),
                "payload_on": json.dumps(_nested(name, f["value_on"])),
                "payload_off": json.dumps(_nested(name, f["value_off"]))}
    elif platform == "number":
        cfg |= {"command_topic": set_topic, "value_template": value, "command_template": _command_template(name, False)}
        if "range" in f:
            cfg["min"], cfg["max"] = f["range"]
        if "step" in f:
            cfg["step"] = f["step"]
        if unit:
            cfg["unit_of_measurement"] = unit
    elif platform == "select":
        cfg |= {"command_topic": set_topic, "value_template": value, "command_template": _command_template(name, True),
                "options": [str(v) for v in f.get("values", [])]}
    elif platform == "button":
        cfg |= {"command_topic": set_topic, "payload_press": json.dumps(_nested(name, (f.get("values") or [name])[0]))}
    elif platform == "text":
        cfg |= {"command_topic": set_topic, "value_template": value, "command_template": _command_template(name, True)}
    elif platform == "event":
        cfg |= {"state_topic": event_topic(state_topic, name), "event_types": [str(v) for v in f.get("values", [])]}
        controllable = False
    else:
        return f"platform {platform!r} is not exposed yet"
    if cls:
        cfg["device_class"] = cls
    return oid, cfg, ExpectedEntity(f"{platform}.{oid}", platform, cls or None,
                                    cfg.get("unit_of_measurement"), name, controllable)


def _light(fs: list[dict], ieee: str, set_topic: str):
    names = {f["name"]: f for f in fs}
    if any("endpoint" in f for f in fs):
        return "multi-endpoint lights are not exposed yet"
    if "state" not in names:
        return "light without an on/off state"
    oid = object_id(ieee, "light")
    cfg = {"p": "light", "schema": "json", "unique_id": oid, "default_entity_id": f"light.{oid}", "name": None,
           "command_topic": set_topic}
    modes = [m for m, feature in (("color_temp", "color_temp"), ("xy", "color.x")) if feature in names]
    if "brightness" in names:
        cfg["brightness"] = True
        cfg["brightness_scale"] = int(names["brightness"].get("range", [0, 254])[1])
    cfg["supported_color_modes"] = modes or (["brightness"] if "brightness" in names else ["onoff"])
    if "color_temp" in names and "range" in names["color_temp"]:
        lo, hi = names["color_temp"]["range"]  # mireds
        cfg |= {"color_temp_kelvin": False, "min_kelvin": round(1e6 / hi), "max_kelvin": round(1e6 / lo)}
    return oid, cfg, ExpectedEntity(f"light.{oid}", "light", None, None, "state", True)


def _lock(fs: list[dict], ieee: str, set_topic: str):
    state = next((f for f in fs if f["name"] == "state" and f["type"] == "binary"), None)
    if state is None:
        return "lock without a binary state"
    oid = object_id(ieee, "lock")
    cfg = {"p": "lock", "unique_id": oid, "default_entity_id": f"lock.{oid}", "name": None,
           "command_topic": set_topic, "value_template": f"{{{{ {_accessor('state')} }}}}",
           "payload_lock": json.dumps({"state": state["value_on"]}),
           "payload_unlock": json.dumps({"state": state["value_off"]}),
           "state_locked": state["value_on"], "state_unlocked": state["value_off"]}
    return oid, cfg, ExpectedEntity(f"lock.{oid}", "lock", None, None, "state", True)


def device_discovery(vds: dict, *, ieee: str, friendly_name: str, base_topic: str = "zigbee2mqtt"):
    """(topic, payload, expected entities, skipped [(feature, reason)]) for one twin."""
    state_topic = f"{base_topic}/{friendly_name}"
    set_topic = f"{state_topic}/set"
    cmps, expected, skipped, grouped = {}, [], [], {}
    for f in vds["features"]:
        if "#" in f["name"]:
            continue
        if f["type"] == "unsupported" or not f["ha"]:
            skipped.append((f["name"], "feature type not modeled" if f["type"] == "unsupported"
                            else "no Home Assistant mapping"))
        elif f.get("path") in ("light", "lock"):
            grouped.setdefault(f["path"], []).append(f)
        elif f.get("path") in ("climate", "cover", "fan"):
            skipped.append((f["name"], f"{f['path']} entities are not exposed yet"))
        else:
            built = _component(f, ieee, state_topic, set_topic)
            if isinstance(built, str):
                skipped.append((f["name"], built))
            else:
                cmps[built[0]] = built[1]
                expected.append(built[2])
    for path, builder in (("light", _light), ("lock", _lock)):
        if path in grouped:
            built = builder(grouped[path], ieee, set_topic)
            if isinstance(built, str):
                skipped.extend((f["name"], built) for f in grouped[path])
            else:
                cmps[built[0]] = built[1]
                expected.append(built[2])
                skipped.extend((f["name"], f"part of the {path} entity") for f in grouped[path]
                               if f["name"] != "state")
    identity = vds["identity"]
    payload = {
        "dev": {"ids": [f"zigbee2mqtt_{ieee}"], "name": friendly_name, "mf": identity["vendor"],
                "mdl": identity["model"]},
        "o": {"name": "VESPER", "sw": __version__},
        "state_topic": state_topic,
        "availability": [{"topic": f"{state_topic}/availability", "value_template": "{{ value_json.state }}"}],
        "cmps": cmps,
    }
    return f"{DISCOVERY_PREFIX}/device/{ieee}/config", payload, expected, skipped
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/exposure/test_ha_discovery.py -q`

Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/exposure/__init__.py vesper/exposure/ha_discovery.py tests/exposure/__init__.py tests/exposure/conftest.py tests/exposure/test_ha_discovery.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(exposure): Home Assistant MQTT device discovery from VDS

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 5: The Zigbee2MQTT-compatible bridge

**Files:**
- Create: `vesper/exposure/z2m_bridge.py`, `tests/exposure/fake_mqtt.py`, `tests/exposure/test_z2m_bridge.py`
- Modify: `pyproject.toml` (add `paho-mqtt>=2.1`; the fakes use its topic matcher, and Task 7's client uses the library)

**Interfaces:**
- Consumes: Task 3's `Runtime`, `Event`; Task 4's `device_discovery`, `ExpectedEntity`, `event_topic`.
- Produces:
  - `z2m_bridge.CONTROL = "vesper/control"`;
  - `Placement(twin_id, friendly_name)`;
  - `ieee_address(twin_id) -> "0x" + 16 hex`;
  - `definition(vds) -> dict`;
  - `device_entry(vds, *, ieee, friendly_name) -> dict`;
  - `topic_safe(name) -> bool`;
  - `flatten(payload) -> list[(dotted_name, value)]`;
  - `Bridge(runtime, placements, client, base_topic="zigbee2mqtt", zhc_version="unknown", discovery=True)`, with `.expected`, `.skipped`, `.rejections`, `start()`, `retract()`, `stop()` and `state_payload(placement)`.
  - Test doubles: `tests.exposure.fake_mqtt.FakeClient` (records publishes, `deliver(topic, payload)`, `last(topic)`, `retained(topic)`) and `FakeBroker` (shared retained store, wildcard subscriptions, queued in-order delivery; `client()`, `last(topic)`, `deliver(topic, payload)`, `published`, `retained`).

- [ ] **Step 1: Add the MQTT dependency**

In `pyproject.toml`, replace:
```toml
  "jsonschema>=4.23",
```
with:
```toml
  "jsonschema>=4.23",
  "paho-mqtt>=2.1",
```

Run: `uv pip install --python .venv/bin/python -e '.[dev]'`

Expected: the install finishes and lists `paho-mqtt==2.1.x` (a PyPI download of ~0.1 MB).

- [ ] **Step 2: Write the failing tests**

Create `tests/exposure/fake_mqtt.py`:
```python
"""An in-memory stand-in for paho's MQTT client: records publishes and injects messages."""
import json
from collections import deque
from types import SimpleNamespace

from paho.mqtt.client import topic_matches_sub


class FakeClient:
    def __init__(self):
        self.published: list[tuple[str, object, bool]] = []
        self.subscriptions: list[str] = []
        self.on_message = None

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload) if payload else None, retain))

    def subscribe(self, topic):
        self.subscriptions.append(topic)

    def deliver(self, topic, payload):
        text = payload if isinstance(payload, bytes | str) else json.dumps(payload)
        raw = text if isinstance(text, bytes) else text.encode()
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=raw))

    def last(self, topic):
        return next((p for t, p, _ in reversed(self.published) if t == topic), None)

    def retained(self, topic):
        return next((r for t, _, r in reversed(self.published) if t == topic), None)


class FakeBroker:
    """One in-memory broker shared by several clients, as Mosquitto would be: retained messages,
    wildcard subscriptions, delivery to the publisher too, and in publish order. Delivery is queued,
    so a handler that publishes is never re-entered."""

    def __init__(self):
        self.published: list[tuple[str, object, bool]] = []  # every client's publishes, decoded
        self.retained: dict[str, bytes] = {}
        self.clients: list[BrokerClient] = []
        self._queue: deque = deque()
        self._delivering = False

    def client(self, *args, **kwargs):
        c = BrokerClient(self)
        self.clients.append(c)
        return c

    def last(self, topic):
        return next((p for t, p, _ in reversed(self.published) if t == topic), None)

    def deliver(self, topic, payload):
        """A message from outside every client (for example Home Assistant sending a command)."""
        self._publish(topic, payload if isinstance(payload, str | bytes) else json.dumps(payload), False)

    def _publish(self, topic, payload, retain):
        raw = payload if isinstance(payload, bytes) else (payload or "").encode()
        self.published.append((topic, json.loads(raw) if raw else None, retain))
        if retain:
            if raw:
                self.retained[topic] = raw
            else:
                self.retained.pop(topic, None)
        for c in self.clients:
            if any(topic_matches_sub(s, topic) for s in c.subscriptions):
                self._queue.append((c, topic, raw))
        self._drain()

    def _subscribe(self, client, sub):
        for topic, raw in list(self.retained.items()):
            if topic_matches_sub(sub, topic):
                self._queue.append((client, topic, raw))
        self._drain()

    def _drain(self):
        if self._delivering:
            return
        self._delivering = True
        try:
            while self._queue:
                c, topic, raw = self._queue.popleft()
                if c.connected and c.on_message:
                    c.on_message(c, None, SimpleNamespace(topic=topic, payload=raw))
        finally:
            self._delivering = False


class BrokerClient:
    def __init__(self, broker):
        self.broker, self.subscriptions, self.on_message, self.connected = broker, [], None, True

    def publish(self, topic, payload, qos=0, retain=False):
        self.broker._publish(topic, payload, retain)

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        self.broker._subscribe(self, topic)

    def unsubscribe(self, topic):
        self.subscriptions.remove(topic)
```

Create `tests/exposure/test_z2m_bridge.py`:
```python
import threading

import pytest

from tests.exposure.fake_mqtt import FakeClient
from vesper.exposure.ha_discovery import event_topic
from vesper.exposure.z2m_bridge import Bridge, Placement, definition, flatten, ieee_address
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime


def bridge(vds, *models):
    rt = Runtime(clock=LogicalClock())
    placements = [Placement(rt.add(vds[m]).id, m.lower()) for m in models]
    client = FakeClient()
    b = Bridge(rt, placements, client, zhc_version="26.123.0")
    b.start()
    return b, rt, client


def test_start_publishes_retained_bridge_topics_with_the_real_definitions(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM", "PLUG-1")
    assert client.last("zigbee2mqtt/bridge/state") == {"state": "online"}
    assert client.retained("zigbee2mqtt/bridge/devices") is True
    coordinator, door, plug = client.last("zigbee2mqtt/bridge/devices")
    assert coordinator["type"] == "Coordinator" and door["type"] == "EndDevice" and plug["type"] == "Router"
    assert door["definition"] == definition(vds["MCCGQ11LM"])
    assert door["definition"]["exposes"] == vds["MCCGQ11LM"]["binding"]["zigbee"]["exposes"]
    assert door["model_id"] == "lumi.sensor_magnet.aq2" and door["ieee_address"] == ieee_address(door_id(rt))
    assert client.last("zigbee2mqtt/mccgq11lm") == {"battery": 0, "contact": True}
    assert client.last("zigbee2mqtt/mccgq11lm/availability") == {"state": "online"}
    assert client.last(f"homeassistant/device/{ieee_address(door_id(rt))}/config")["cmps"]
    assert set(client.subscriptions) == {"zigbee2mqtt/#", "vesper/control/#"}


def door_id(rt):
    return next(t for t in rt.twins if "mccgq11lm" in t)


def test_set_json_and_set_attribute_reach_the_twin_and_republish_state(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    client.deliver("zigbee2mqtt/plug-1/set", {"state": "ON"})
    assert client.last("zigbee2mqtt/plug-1")["state"] == "ON"
    client.deliver("zigbee2mqtt/plug-1/set/state", "OFF")
    assert client.last("zigbee2mqtt/plug-1")["state"] == "OFF"
    assert [a.actor for a in rt.actions] == ["platform", "platform"]


def test_a_rejected_command_is_recorded_and_state_unchanged(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM")
    client.deliver("zigbee2mqtt/mccgq11lm/set", {"contact": False})
    assert b.rejections[-1]["feature"] == "contact" and "read-only" in b.rejections[-1]["reason"]
    assert client.last("zigbee2mqtt/mccgq11lm")["contact"] is True
    client.deliver("zigbee2mqtt/mccgq11lm/set", "not json")
    assert b.rejections[-1]["reason"] == "payload is not a JSON object"


def test_nested_light_commands_and_color_mode(vds):
    b, rt, client = bridge(vds, "LED1545G12")
    client.deliver("zigbee2mqtt/led1545g12/set", {"state": "ON", "brightness": 128, "color": {"x": 0.3, "y": 0.4}})
    state = client.last("zigbee2mqtt/led1545g12")
    assert (state["state"], state["brightness"], state["color"], state["color_mode"]) == ("ON", 128,
                                                                                        {"x": 0.3, "y": 0.4}, "xy")


def test_get_republishes_and_unknown_devices_are_ignored(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    before = len(client.published)
    client.deliver("zigbee2mqtt/plug-1/get", {"state": ""})
    client.deliver("zigbee2mqtt/someone-else/set", {"state": "ON"})
    assert [t for t, _, _ in client.published[before:]] == ["zigbee2mqtt/plug-1"]


def test_button_events_go_to_their_topic_and_are_not_retained_state(vds):
    b, rt, client = bridge(vds, "BTN-1")
    client.deliver("vesper/control/stimulate",
                   {"device": "btn-1", "stimulus": {"type": "button_press", "action": "hold"}})
    topic = event_topic("zigbee2mqtt/btn-1", "action")
    assert client.last(topic) == {"event_type": "hold"} and client.retained(topic) is False
    assert "action" not in client.last("zigbee2mqtt/btn-1")


def test_control_channel_stimulate_operate_and_bad_messages(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM", "PLUG-1")
    client.deliver("vesper/control/stimulate",
                   {"device": "mccgq11lm", "stimulus": {"type": "contact", "state": "opened"}})
    assert client.last("zigbee2mqtt/mccgq11lm")["contact"] is False
    client.deliver("vesper/control/operate", {"device": "plug-1", "feature": "state", "value": "ON", "actor": "bob"})
    assert rt.actions[-1].actor == "human:bob" and client.last("zigbee2mqtt/plug-1")["state"] == "ON"
    client.deliver("vesper/control/stimulate", {"device": "nobody"})
    assert b.rejections[-1]["reason"] == "bad control message"


def test_bridge_requests_get_an_explicit_error_and_stop_goes_offline(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    client.deliver("zigbee2mqtt/bridge/request/permit_join", {"value": True})
    assert client.last("zigbee2mqtt/bridge/response/permit_join")["status"] == "error"
    b.stop()
    assert client.last("zigbee2mqtt/bridge/state") == {"state": "offline"}
    assert client.last("zigbee2mqtt/plug-1/availability") == {"state": "offline"}


def test_placements_must_be_zigbee_twins_with_unique_names(vds):
    rt = Runtime(clock=LogicalClock())
    a, b = rt.add(vds["PLUG-1"]).id, rt.add(vds["MCCGQ11LM"]).id
    with pytest.raises(ValueError, match="duplicate friendly name"):
        Bridge(rt, [Placement(a, "x"), Placement(b, "x")], FakeClient())
    matter = dict(vds["PLUG-1"], id="matter:1:1", binding={"matter": {}})
    rt.add(matter)
    with pytest.raises(ValueError, match="no Zigbee binding"):
        Bridge(rt, [Placement("matter:1:1", "m")], FakeClient())


@pytest.mark.parametrize("name", ["", "a+b", "hall/#", "/hall", "hall/", "hall//door", "bridge/x", "door/set",
                                  "door/get", "door/availability"])
def test_a_friendly_name_that_would_break_the_topic_tree_is_refused(vds, name):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, name)
    with pytest.raises(ValueError, match="friendly name"):
        Bridge(rt, [p], FakeClient())


def test_commands_on_the_network_thread_and_reports_on_the_clock_thread_do_not_deadlock(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    twin_id = b.placements[0].twin_id

    def commands():
        for i in range(300):
            client.deliver("zigbee2mqtt/plug-1/set", {"state": "ON" if i % 2 else "OFF"})

    def reports():
        for i in range(300):
            rt.stimulate(twin_id, {"type": "load", "watts": i})

    threads = [threading.Thread(target=commands), threading.Thread(target=reports)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "bridge and runtime deadlocked"
    assert rt.faulted == {} and b.rejections == []


def test_flatten():
    assert flatten({"state": "ON", "color": {"x": 1, "y": 2}}) == [("state", "ON"), ("color.x", 1), ("color.y", 2)]
```

- [ ] **Step 3: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/exposure/test_z2m_bridge.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.exposure.z2m_bridge'`.

- [ ] **Step 4: Implement**

Create `vesper/exposure/z2m_bridge.py`:
```python
"""Zigbee2MQTT-compatible MQTT interface for twins (spec §2 `vesper/exposure/z2m_bridge`; L2 `z2m-api`).

Implements the MQTT contract Zigbee2MQTT documents: retained `bridge/state`, `bridge/info` and
`bridge/devices` (with each product's real definition), per-device state, `/set`, `/set/<attribute>`,
`/get` and `/availability`, plus Home Assistant device discovery. It reuses no Zigbee2MQTT (GPL-3.0)
code. A VESPER control channel (`vesper/control/stimulate|operate`) lets other processes, such as the
Habitat resident, drive twins over MQTT.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field

from vesper import __version__
from vesper.exposure.ha_discovery import ExpectedEntity, device_discovery, event_topic
from vesper.twins.runtime import Event, Runtime

CONTROL = "vesper/control"


@dataclass(frozen=True)
class Placement:
    twin_id: str
    friendly_name: str


def ieee_address(twin_id: str) -> str:
    return "0x" + hashlib.sha256(twin_id.encode()).hexdigest()[:16]


def definition(vds: dict) -> dict:
    """The `definition` object Zigbee2MQTT publishes for this product in `bridge/devices`."""
    zigbee = vds["binding"]["zigbee"]
    identity = vds["identity"]
    return {"source": "native", "model": identity["model"], "vendor": identity["vendor"],
            "description": identity["description"], "exposes": zigbee["exposes"], "options": zigbee["options"],
            "supports_ota": zigbee["ota"]}


def device_entry(vds: dict, *, ieee: str, friendly_name: str) -> dict:
    ids = vds["identity"]["ids"]
    battery = any(f["name"] == "battery" for f in vds["features"])
    model_id = next(iter(ids.get("zigbee_model") or []), None) or next(
        (fp.get("modelID") for fp in ids.get("zigbee_fingerprint", []) if fp.get("modelID")), None)
    return {"ieee_address": ieee, "type": "EndDevice" if battery else "Router",
            "network_address": int(ieee[2:6], 16) % 0xFFF7 + 1, "supported": True, "disabled": False,
            "friendly_name": friendly_name, "definition": definition(vds),
            "power_source": "Battery" if battery else "Mains (single phase)", "model_id": model_id,
            "interview_state": "SUCCESSFUL", "interviewing": False, "interview_completed": True, "endpoints": {}}


def topic_safe(name: str) -> bool:
    """A friendly name usable as an MQTT topic level path without colliding with the bridge's own topics."""
    parts = name.split("/")
    return (bool(name) and "+" not in name and "#" not in name and all(parts) and parts[0] != "bridge"
            and parts[-1] not in ("set", "get", "availability"))


def flatten(payload: dict, prefix: str = "") -> list[tuple[str, object]]:
    """{"color": {"x": 0.3}} -> [("color.x", 0.3)]; feature names use the same dotted paths."""
    out = []
    for key, value in payload.items():
        if isinstance(value, dict):
            out.extend(flatten(value, f"{prefix}{key}."))
        else:
            out.append((f"{prefix}{key}", value))
    return out


@dataclass
class Bridge:
    runtime: Runtime
    placements: list[Placement]
    client: object  # paho.mqtt.client.Client or a stand-in with publish/subscribe/on_message
    base_topic: str = "zigbee2mqtt"
    zhc_version: str = "unknown"
    discovery: bool = True
    expected: dict[str, list[ExpectedEntity]] = field(default_factory=dict)
    skipped: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    rejections: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self._by_name: dict[str, Placement] = {}
        self._by_twin: dict[str, Placement] = {}
        for p in self.placements:
            vds = self.runtime.twins[p.twin_id].vds
            if "zigbee" not in vds["binding"]:
                raise ValueError(f"{p.twin_id} has no Zigbee binding; it cannot be exposed through this bridge")
            if not topic_safe(p.friendly_name):
                raise ValueError(f"friendly name {p.friendly_name!r} cannot be an MQTT topic here: no '+', '#', "
                                 "empty levels, leading 'bridge' or trailing set/get/availability")
            if p.friendly_name in self._by_name:
                raise ValueError(f"duplicate friendly name {p.friendly_name!r}")
            self._by_name[p.friendly_name] = self._by_twin[p.twin_id] = p
        self._lock = threading.Lock()
        self.client.on_message = self._on_message
        self.runtime.subscribe(self._on_event)

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        b = self.base_topic
        self.client.subscribe(f"{b}/#")
        self.client.subscribe(f"{CONTROL}/#")
        self._publish(f"{b}/bridge/state", {"state": "online"}, retain=True)
        self._publish(f"{b}/bridge/info", {
            "version": f"vesper-{__version__}", "commit": None, "permit_join": False,
            "coordinator": {"ieee_address": "0x" + "0" * 16, "type": "vesper-virtual"},
            "zigbee_herdsman_converters": {"version": self.zhc_version}, "config": {"homeassistant": self.discovery},
        }, retain=True)
        coordinator = {"ieee_address": "0x" + "0" * 16, "type": "Coordinator", "network_address": 0,
                       "supported": False, "disabled": False, "friendly_name": "Coordinator", "definition": None,
                       "power_source": None, "model_id": None, "interview_state": "SUCCESSFUL",
                       "interviewing": False, "interview_completed": True, "endpoints": {}}
        entries = [device_entry(self._vds(p), ieee=ieee_address(p.twin_id), friendly_name=p.friendly_name)
                   for p in self.placements]
        self._publish(f"{b}/bridge/devices", [coordinator, *entries], retain=True)
        for p in self.placements:
            ieee = ieee_address(p.twin_id)
            if self.discovery:
                topic, payload, expected, skipped = device_discovery(self._vds(p), ieee=ieee,
                                                                     friendly_name=p.friendly_name, base_topic=b)
                self.expected[p.twin_id], self.skipped[p.twin_id] = expected, skipped
                self._publish(topic, payload, retain=True)
            self._publish(f"{b}/{p.friendly_name}/availability", {"state": "online"}, retain=True)
            self._publish_state(p)

    def retract(self) -> None:
        """Remove every retained message this bridge published, so Home Assistant forgets the twins."""
        b = self.base_topic
        for p in self.placements:
            for topic in (f"homeassistant/device/{ieee_address(p.twin_id)}/config", f"{b}/{p.friendly_name}",
                          f"{b}/{p.friendly_name}/availability"):
                self.client.publish(topic, "", qos=1, retain=True)
        for topic in ("bridge/devices", "bridge/info", "bridge/state"):
            self.client.publish(f"{b}/{topic}", "", qos=1, retain=True)

    def stop(self) -> None:
        for p in self.placements:
            self._publish(f"{self.base_topic}/{p.friendly_name}/availability", {"state": "offline"}, retain=True)
        self._publish(f"{self.base_topic}/bridge/state", {"state": "offline"}, retain=True)

    # -- outgoing ------------------------------------------------------------------------------
    def _vds(self, p: Placement) -> dict:
        return self.runtime.twins[p.twin_id].vds

    def _publish(self, topic: str, obj, retain: bool = False) -> None:
        self.client.publish(topic, json.dumps(obj, ensure_ascii=False), qos=1, retain=retain)

    def state_payload(self, p: Placement) -> dict:
        twin = self.runtime.twins[p.twin_id]
        payload = twin.payload()
        for name, f in twin.features.items():  # events are not state: they go to their own topic
            if f.get("ha") == "event":
                payload.pop(name.split(".")[0], None)
        light = {n for n, f in twin.features.items() if f.get("path") == "light"}
        if "color_temp" in light or "color.x" in light:
            payload["color_mode"] = "color_temp" if "color_temp" in light else "xy"
        return payload

    def _publish_state(self, p: Placement) -> None:
        self._publish(f"{self.base_topic}/{p.friendly_name}", self.state_payload(p), retain=True)

    def _on_event(self, event: Event) -> None:
        p = self._by_twin.get(event.twin_id)
        if p is None:
            return
        f = self.runtime.twins[event.twin_id].features[event.feature]
        if f.get("ha") == "event":
            topic = event_topic(f"{self.base_topic}/{p.friendly_name}", event.feature)
            self._publish(topic, {"event_type": str(event.value)})
        else:
            self._publish_state(p)

    # -- incoming ------------------------------------------------------------------------------
    def _on_message(self, client, userdata, msg) -> None:
        topic, raw = msg.topic, msg.payload.decode("utf-8", "replace")
        with self._lock:
            if topic.startswith(f"{CONTROL}/"):
                self._control(topic[len(CONTROL) + 1:], raw)
                return
            if not topic.startswith(f"{self.base_topic}/"):
                return
            rest = topic[len(self.base_topic) + 1:]
            if rest.startswith("bridge/request/"):
                self._publish(f"{self.base_topic}/bridge/response/{rest[len('bridge/request/'):]}",
                              {"status": "error", "error": "not supported by the VESPER bridge", "data": {}})
            elif "/set/" in rest:
                name, attribute = rest.split("/set/", 1)
                self._set(name, {attribute: self._parse(raw)}, raw)
            elif rest.endswith("/set"):
                self._set(rest[:-4], self._parse(raw), raw)
            elif rest.endswith("/get") and rest[:-4] in self._by_name:
                self._publish_state(self._by_name[rest[:-4]])

    @staticmethod
    def _parse(raw: str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def _set(self, name: str, command, raw: str) -> None:
        p = self._by_name.get(name)
        if p is None:
            return  # not one of ours, or our own state topic
        if not isinstance(command, dict):
            self.rejections.append({"device": name, "payload": raw, "reason": "payload is not a JSON object"})
            return
        for feature, value in flatten(command):
            if not self.runtime.platform_set(p.twin_id, feature, value):
                self.rejections.append({"device": name, "feature": feature, "value": value,
                                        "reason": self.runtime.actions[-1].reason
                                        if self.runtime.actions else "rejected"})
        self._publish_state(p)

    def _control(self, action: str, raw: str) -> None:
        try:
            msg = json.loads(raw)
            p = self._by_name[msg["device"]]
        except (ValueError, KeyError, TypeError):
            self.rejections.append({"control": action, "payload": raw, "reason": "bad control message"})
            return
        if action == "stimulate":
            self.runtime.stimulate(p.twin_id, msg.get("stimulus", {}))
        elif action == "operate":
            if not self.runtime.operate(p.twin_id, msg.get("feature"), msg.get("value"), msg.get("actor", "resident")):
                self.rejections.append({"device": p.friendly_name, "control": "operate", "reason":
                                        self.runtime.actions[-1].reason if self.runtime.actions else "rejected"})
        self._publish_state(p)
```

- [ ] **Step 5: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/exposure -q`

Expected: `30 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add pyproject.toml vesper/exposure/z2m_bridge.py tests/exposure/fake_mqtt.py tests/exposure/test_z2m_bridge.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(exposure): Zigbee2MQTT-compatible MQTT bridge with real definitions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 6: VESPER's own Mosquitto and Home Assistant

**Files:**
- Create: `vesper/validation/__init__.py`, `vesper/validation/stack.py`
- Create: `tests/validation/__init__.py` (empty), `tests/validation/test_stack.py`
- Modify: `.gitignore` (the line `.vesper/`)

**Interfaces:**
- Produces:
  - constants `stack.HA_VERSION = "2026.10.2"`, `HA_PYTHON = "3.14"`, `NEEDED_INTEGRATIONS`, `MQTT_PORT = 18830`, `HA_PORT = 18123` and `RECOVERY_MARK`;
  - `StackError(RuntimeError)`;
  - `Paths(root)`;
  - `port_free(port)` and `wait_for_port(port, timeout)`;
  - `setup_ha(root, *, run, which) -> list[str]` (the extra requirements it installed);
  - `mosquitto_binary()`;
  - `Mosquitto(root, port=18830)`: `start()`, `stop()`;
  - `HomeAssistant(root, port=18123, popen, transport, start_timeout=300)`: `write_config()`, `start()`, `stop()`, `onboard()`, `token()`, `api(method, path, **kw)`, `ensure_mqtt(broker_port)`, `state(entity_id)` and `call(domain, service, data)`.
- Everything lives under `<root>` (default `.vesper/`, git-ignored): `ha/venv`, `ha/config`, `ha/auth.json` (mode 600) and `mqtt/mosquitto.conf`.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_stack.py`:
```python
import json
import socket
import subprocess

import httpx
import pytest

from vesper.validation.stack import (HA_VERSION, RECOVERY_MARK, HomeAssistant, Mosquitto, Paths, StackError,
                                     port_free, setup_ha)


def test_setup_installs_the_pinned_release_and_the_needed_integrations_packages(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1:2] == ["-c"]:
            extras = ["aiohasupervisor==0.6.0", "paho-mqtt==2.1.0"]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(extras))
        return subprocess.CompletedProcess(cmd, 0)

    reqs = setup_ha(tmp_path, run=run, which=lambda n: f"/bin/{n}")
    assert reqs == ["aiohasupervisor==0.6.0", "paho-mqtt==2.1.0"]
    assert ["/bin/uv", "venv", "--python", "3.14", str(tmp_path / "ha" / "venv")] in calls
    assert calls[1][-1] == f"homeassistant=={HA_VERSION}" and calls[3][-2:] == reqs
    assert calls[2][3:] == ["frontend", "mqtt", "hassio"]


def test_setup_needs_uv(tmp_path):
    with pytest.raises(StackError, match="needs uv"):
        setup_ha(tmp_path, which=lambda n: None)


def busy_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_services_refuse_ports_that_are_already_in_use(tmp_path):
    s, port = busy_port()
    with s:
        assert not port_free(port)
        with pytest.raises(StackError, match="already in use"):
            Mosquitto(tmp_path, port=port, binary="/bin/true").start()
        ha = HomeAssistant(tmp_path, port=port)
        Paths(tmp_path).hass.parent.mkdir(parents=True)
        Paths(tmp_path).hass.touch()
        with pytest.raises(StackError, match="already in use"):
            ha.start()


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def installed(tmp_path):
    Paths(tmp_path).hass.parent.mkdir(parents=True, exist_ok=True)
    Paths(tmp_path).hass.touch()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_recovery_mode_is_refused_and_home_assistant_stopped(tmp_path):
    installed(tmp_path)
    proc = FakeProc()

    def popen(cmd, stdout, stderr):
        stdout.write(f"... WARNING [homeassistant.bootstrap] {RECOVERY_MARK}\n")
        stdout.flush()
        return proc

    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    ha = HomeAssistant(tmp_path, port=free_port(), popen=popen, transport=down, start_timeout=5)
    with pytest.raises(StackError, match="recovery mode"):
        ha.start()
    assert proc.terminated


def test_start_writes_a_loopback_config_and_waits_for_the_api(tmp_path):
    installed(tmp_path)
    seen = {}

    def popen(cmd, stdout, stderr):
        seen["cmd"] = cmd
        return FakeProc()

    port = free_port()
    ha = HomeAssistant(tmp_path, port=port, popen=popen, transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    ha.start()
    cfg = (tmp_path / "ha" / "config" / "configuration.yaml").read_text()
    assert f"server_port: {port}" in cfg and "server_host: 127.0.0.1" in cfg
    assert seen["cmd"][-1] == "--skip-pip"


class FakeServer:
    """Answers the onboarding, token and config-flow endpoints like Home Assistant 2026.10."""

    def __init__(self, entries=None, flow_step="broker", flow_result="create_entry", onboarded=False):
        self.entries, self.flow_step, self.flow_result, self.requests = entries or [], flow_step, flow_result, []
        self.onboarded = onboarded

    def __call__(self, request):
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/api/onboarding/users":
            if self.onboarded:
                return httpx.Response(403, json={"message": "User step already done"})
            return httpx.Response(200, json={"auth_code": "code"})
        if path == "/auth/token":
            return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh",
                                             "expires_in": 1800, "token_type": "Bearer"})
        if path.startswith("/api/onboarding/"):
            return httpx.Response(200, json={})
        if path == "/api/config/config_entries/entry":
            return httpx.Response(200, json=self.entries)
        if path == "/api/config/config_entries/flow":
            return httpx.Response(200, json={"flow_id": "f1", "type": "form", "step_id": self.flow_step})
        if path == "/api/config/config_entries/flow/f1":
            body = json.loads(request.content)
            self.entries.append({"domain": "mqtt", "data": body})
            return httpx.Response(200, json={"type": self.flow_result})
        return httpx.Response(404)


def onboarded(tmp_path, server):
    ha = HomeAssistant(tmp_path, transport=httpx.MockTransport(server))
    (tmp_path / "ha").mkdir(parents=True, exist_ok=True)
    ha.onboard()
    return ha


def test_an_onboarded_home_assistant_without_saved_credentials_says_how_to_reset(tmp_path):
    with pytest.raises(StackError, match=r"already onboarded.*ha/config/\.storage"):
        onboarded(tmp_path, FakeServer(onboarded=True))
    assert not (tmp_path / "ha" / "auth.json").exists()


def test_onboarding_runs_once_and_keeps_only_a_private_refresh_token(tmp_path):
    server = FakeServer()
    onboarded(tmp_path, server)
    auth = Paths(tmp_path).auth_file
    assert json.loads(auth.read_text())["refresh_token"] == "refresh" and auth.stat().st_mode & 0o777 == 0o600
    steps = [p for m, p in server.requests if p.startswith("/api/onboarding/")]
    assert steps == ["/api/onboarding/users", "/api/onboarding/core_config", "/api/onboarding/analytics",
                     "/api/onboarding/integration"]
    onboarded(tmp_path, server)
    assert len([p for m, p in server.requests if p == "/api/onboarding/users"]) == 1


def test_mqtt_is_configured_for_vespers_broker_only(tmp_path):
    server = FakeServer()
    ha = onboarded(tmp_path, server)
    ha.ensure_mqtt(18830)
    assert server.entries == [{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 18830}}]
    ha.ensure_mqtt(18830)  # already configured: no second flow
    assert len([p for m, p in server.requests if p == "/api/config/config_entries/flow"]) == 1


def test_an_mqtt_entry_for_another_broker_is_refused(tmp_path):
    ha = onboarded(tmp_path, FakeServer(entries=[{"domain": "mqtt", "data": {"broker": "127.0.0.1", "port": 1883}}]))
    with pytest.raises(StackError, match="another broker"):
        ha.ensure_mqtt(18830)


def test_an_unexpected_flow_is_refused(tmp_path):
    ha = onboarded(tmp_path, FakeServer(flow_step="addon"))
    with pytest.raises(StackError, match="unexpected MQTT config flow step"):
        ha.ensure_mqtt(18830)
```

Also create the empty file `tests/validation/__init__.py`.

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_stack.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.validation'`.

- [ ] **Step 3: Implement**

Create `vesper/validation/__init__.py`:
```python
"""Software-only validation of the twins (spec §4): round trip, platform acceptance, later timing and anchors."""
```

Create `vesper/validation/stack.py`:
```python
"""VESPER's own Mosquitto and Home Assistant Core, for acceptance tests (spec §4 L2; §5 `[ha]`).

Only VESPER's instances are used, on loopback ports that differ from the defaults (MQTT 18830, HA 18123),
so a machine already running Mosquitto or Home Assistant is never touched. Home Assistant runs from its
own Python 3.14 venv with `--skip-pip`: `setup_ha` installs the pinned release and exactly the
requirements of the integrations VESPER needs. If Home Assistant falls into recovery mode, which would
make it bind the default port 8123, it is stopped and the run fails.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

HA_VERSION = "2026.10.2"
HA_PYTHON = "3.14"
NEEDED_INTEGRATIONS = ("frontend", "mqtt", "hassio")  # bootstrap needs frontend; mqtt's flow imports hassio
MQTT_PORT, HA_PORT = 18830, 18123
RECOVERY_MARK = "Activating recovery mode"
_REQS_SCRIPT = """
import json, sys, importlib.resources as r, importlib.metadata as md
root = r.files("homeassistant") / "components"
seen, reqs, todo = set(), set(), list(sys.argv[1:])
while todo:
    d = todo.pop()
    if d in seen:
        continue
    seen.add(d)
    m = json.loads((root / d / "manifest.json").read_text())
    reqs.update(m.get("requirements", []))
    todo.extend(m.get("dependencies", []))
print(json.dumps(sorted(reqs)))
"""


class StackError(RuntimeError):
    """The local Mosquitto or Home Assistant could not be prepared, started or configured."""


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def ha_venv(self) -> Path:
        return self.root / "ha" / "venv"

    @property
    def ha_python(self) -> Path:
        return self.ha_venv / "bin" / "python"

    @property
    def hass(self) -> Path:
        return self.ha_venv / "bin" / "hass"

    @property
    def ha_config(self) -> Path:
        return self.root / "ha" / "config"

    @property
    def ha_log(self) -> Path:
        return self.root / "ha" / "hass.log"

    @property
    def auth_file(self) -> Path:
        return self.root / "ha" / "auth.json"

    @property
    def mqtt_conf(self) -> Path:
        return self.root / "mqtt" / "mosquitto.conf"


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def wait_for_port(port: int, timeout: float, host: str = "127.0.0.1") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_free(port, host):
            return
        time.sleep(0.2)
    raise StackError(f"nothing listens on {host}:{port} after {timeout:.0f} s")


def setup_ha(root: Path, *, run=subprocess.run, which=shutil.which) -> list[str]:
    """Create Home Assistant's venv and install the pinned release plus the needed integrations' packages."""
    uv = which("uv")
    if not uv:
        raise StackError("setting up Home Assistant needs uv on PATH (https://docs.astral.sh/uv/)")
    paths = Paths(Path(root))
    try:
        if not paths.ha_python.exists():
            run([uv, "venv", "--python", HA_PYTHON, str(paths.ha_venv)], check=True)
        run([uv, "pip", "install", "--python", str(paths.ha_python), f"homeassistant=={HA_VERSION}"], check=True)
        out = run([str(paths.ha_python), "-c", _REQS_SCRIPT, *NEEDED_INTEGRATIONS], check=True,
                  capture_output=True, text=True).stdout
        requirements = json.loads(out)
        if requirements:
            run([uv, "pip", "install", "--python", str(paths.ha_python), *requirements], check=True)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        raise StackError(f"Home Assistant set-up failed: {e}") from e
    return requirements


def mosquitto_binary(which=shutil.which) -> str:
    for candidate in (which("mosquitto"), "/opt/homebrew/sbin/mosquitto", "/usr/local/sbin/mosquitto",
                      "/usr/sbin/mosquitto"):
        if candidate and Path(candidate).exists():
            return candidate
    raise StackError("Mosquitto not found; install it natively (macOS: brew install mosquitto; "
                     "Debian/Ubuntu: apt install mosquitto)")


@dataclass
class Mosquitto:
    root: Path
    port: int = MQTT_PORT
    popen: object = subprocess.Popen
    binary: str | None = None
    proc: object = None

    def start(self) -> None:
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a broker")
        conf = Paths(Path(self.root)).mqtt_conf
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(f"listener {self.port} 127.0.0.1\nallow_anonymous true\npersistence false\n"
                        f"log_dest file {conf.parent.resolve() / 'mosquitto.log'}\n")
        self.proc = self.popen([self.binary or mosquitto_binary(), "-c", str(conf)])
        wait_for_port(self.port, 10)

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
            self.proc = None


@dataclass
class HomeAssistant:
    root: Path
    port: int = HA_PORT
    popen: object = subprocess.Popen
    transport: httpx.BaseTransport | None = None
    start_timeout: float = 300.0
    proc: object = None
    _access: tuple[str, float] | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, timeout=30.0, transport=self.transport)

    def write_config(self) -> None:
        cfg = Paths(Path(self.root)).ha_config
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "configuration.yaml").write_text(
            "homeassistant:\n  name: VESPER\n  unit_system: metric\n  time_zone: UTC\n  country: US\n"
            "  currency: USD\n"
            f"http:\n  server_host: 127.0.0.1\n  server_port: {self.port}\n"
            "api:\nconfig:\nonboarding:\nlogger:\n  default: warning\n")

    def start(self) -> None:
        paths = Paths(Path(self.root))
        if not paths.hass.exists():
            raise StackError("Home Assistant is not installed; run `vesper setup ha` first")
        if not port_free(self.port):
            raise StackError(f"port {self.port} is already in use; refusing to share a Home Assistant")
        self.write_config()
        log = paths.ha_log.open("w")
        self.proc = self.popen([str(paths.hass), "-c", str(paths.ha_config), "--skip-pip"], stdout=log,
                               stderr=subprocess.STDOUT)
        deadline = time.monotonic() + self.start_timeout
        with self._client() as c:
            while time.monotonic() < deadline:
                if RECOVERY_MARK in paths.ha_log.read_text(errors="replace"):
                    self.stop()
                    raise StackError("Home Assistant entered recovery mode (it would bind port 8123); "
                                     f"see {paths.ha_log}")
                try:
                    if c.get("/api/").status_code in (200, 401):
                        return
                except httpx.TransportError:
                    pass
                time.sleep(1)
        self.stop()
        raise StackError(f"Home Assistant did not answer on {self.url} within {self.start_timeout:.0f} s")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    # -- authentication -------------------------------------------------------------------------
    def onboard(self) -> None:
        """Create the local owner account once; afterwards reuse its refresh token."""
        auth = Paths(Path(self.root)).auth_file
        if auth.exists():
            return
        cid = f"{self.url}/"
        with self._client() as c:
            r = c.post("/api/onboarding/users", json={"client_id": cid, "name": "VESPER", "username": "vesper",
                                                      "password": os.urandom(16).hex(), "language": "en"})
            if r.status_code == 403:
                storage = Paths(Path(self.root)).ha_config / ".storage"
                raise StackError(f"Home Assistant is already onboarded but {auth} is missing; stop it, delete "
                                 f"{storage} (VESPER's own instance) and run again")
            if r.status_code != 200:
                raise StackError(f"onboarding failed ({r.status_code}): {r.text[:200]}")
            tokens = c.post("/auth/token", data={"grant_type": "authorization_code", "code": r.json()["auth_code"],
                                                 "client_id": cid}).json()
            h = {"Authorization": f"Bearer {tokens['access_token']}"}
            for step, body in (("core_config", {}), ("analytics", {}),
                               ("integration", {"client_id": cid, "redirect_uri": f"{cid}?auth_callback=1"})):
                c.post(f"/api/onboarding/{step}", json=body, headers=h).raise_for_status()
        auth.write_text(json.dumps({"refresh_token": tokens["refresh_token"], "client_id": cid}))
        auth.chmod(0o600)

    def token(self) -> str:
        if self._access and self._access[1] > time.monotonic() + 30:
            return self._access[0]
        saved = json.loads(Paths(Path(self.root)).auth_file.read_text())
        with self._client() as c:
            r = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
                                            "client_id": saved["client_id"]})
        if r.status_code != 200:
            raise StackError(f"Home Assistant refused the saved credentials ({r.status_code})")
        body = r.json()
        self._access = (body["access_token"], time.monotonic() + body["expires_in"])
        return body["access_token"]

    def api(self, method: str, path: str, **kwargs) -> httpx.Response:
        with self._client() as c:
            return c.request(method, path, headers={"Authorization": f"Bearer {self.token()}"}, **kwargs)

    # -- configuration and use ------------------------------------------------------------------
    def ensure_mqtt(self, broker_port: int) -> None:
        """Configure the MQTT integration for VESPER's broker only; refuse any other broker entry."""
        entries = self.api("GET", "/api/config/config_entries/entry", params={"domain": "mqtt"}).json()
        for e in entries:
            data = e.get("data") or {}
            if data and (data.get("broker"), data.get("port")) != ("127.0.0.1", broker_port):
                raise StackError(f"Home Assistant has an MQTT entry for another broker: {data}")
        if entries:
            return
        flow = self.api("POST", "/api/config/config_entries/flow", json={"handler": "mqtt"}).json()
        if flow.get("step_id") != "broker":
            raise StackError(f"unexpected MQTT config flow step: {flow}")
        result = self.api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                          json={"broker": "127.0.0.1", "port": broker_port}).json()
        if result.get("type") != "create_entry":
            raise StackError(f"the MQTT integration was not configured: {result}")

    def state(self, entity_id: str) -> dict | None:
        r = self.api("GET", f"/api/states/{entity_id}")
        return r.json() if r.status_code == 200 else None

    def call(self, domain: str, service: str, data: dict) -> None:
        r = self.api("POST", f"/api/services/{domain}/{service}", json=data)
        if r.status_code >= 400:
            raise StackError(f"service {domain}.{service} failed ({r.status_code}): {r.text[:200]}")
```

Append to `.gitignore` (unless the line is already there):
```gitignore
.vesper/
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/validation/test_stack.py -q`

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/__init__.py vesper/validation/stack.py tests/validation/__init__.py tests/validation/test_stack.py .gitignore
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): own Mosquitto and pinned Home Assistant, onboarded headless

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 7: The L0/L1 round trip

**Files:**
- Create: `vesper/exposure/mqtt.py`, `vesper/validation/roundtrip.py`
- Create: `tests/exposure/test_roundtrip.py`, `tests/validation/test_live_mqtt.py`
- Modify: `pyproject.toml` (the `stack` test marker)

**Interfaces:**
- Consumes: Task 5's `Bridge`, `Placement` and `FakeBroker`; Task 6's `Mosquitto` (live test only).
- Produces:
  - `mqtt.connect(host, port, client_id, timeout=10.0) -> paho Client` (loop running) and `mqtt.disconnect(client)`;
  - `roundtrip.source_definitions(doc) -> {record_id: definition}`;
  - `diff_paths(a, b) -> list[str]`;
  - `compare(published, twin_of, sources) -> {checked, identical, different, unmatched}`;
  - `receive_retained(client, topic, timeout=30.0)`, which stops listening afterwards.

- [ ] **Step 1: Register the opt-in marker**

In `pyproject.toml`, replace:
```toml
markers = ["network: hits real upstream sources (opt-in: VESPER_NETWORK_TESTS=1)"]
```
with:
```toml
markers = [
  "network: hits real upstream sources (opt-in: VESPER_NETWORK_TESTS=1)",
  "stack: starts a real Mosquitto on a free local port (opt-in: VESPER_STACK_TESTS=1)",
]
```

- [ ] **Step 2: Write the failing tests**

Create `tests/exposure/test_roundtrip.py`:
```python
import json

import pytest

from tests.catalog.fixtures import ZIGBEE_DOC
from tests.exposure.fake_mqtt import FakeBroker, FakeClient
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.roundtrip import compare, diff_paths, receive_retained, source_definitions


def published(records):
    rt = Runtime(clock=LogicalClock())
    placements = [Placement(rt.add(r).id, f"d{i}") for i, r in enumerate(records)]
    client = FakeClient()
    Bridge(rt, placements, client, discovery=False).start()
    devices = json.loads(json.dumps(client.last("zigbee2mqtt/bridge/devices")))  # through JSON, as over MQTT
    return devices, {p.friendly_name: p.twin_id for p in placements}


def test_every_published_definition_equals_its_source_record():
    records = zigbee_records(ZIGBEE_DOC)
    devices, twin_of = published(records)
    report = compare(devices, twin_of, source_definitions(ZIGBEE_DOC))
    assert report == {"checked": len(records), "identical": len(records), "different": {}, "unmatched": []}


def test_a_normalization_change_shows_up_as_a_diff():
    records = zigbee_records(ZIGBEE_DOC)
    records[0]["binding"]["zigbee"]["exposes"][0]["unit"] = "percent"
    devices, twin_of = published(records)
    report = compare(devices, twin_of, source_definitions(ZIGBEE_DOC))
    assert report["identical"] == len(records) - 1
    assert report["different"][records[0]["id"]] == ["/exposes[0]/unit"]


def test_diff_paths_distinguishes_types_lengths_and_keys():
    assert diff_paths({"a": 1}, {"a": 1.0}) == ["/a"]
    assert diff_paths([1, 2], [1]) == ["[len 2 != 1]"]
    assert diff_paths({"a": 1}, {"b": 1}) == ["/a", "/b"]
    assert diff_paths({"a": [{"b": True}]}, {"a": [{"b": True}]}) == []


def test_receive_retained_reads_the_message_then_stops_listening():
    broker = FakeBroker()
    broker.client().publish("z/bridge/devices", json.dumps([{"ieee_address": "0x1"}]), retain=True)
    checker = broker.client()
    assert receive_retained(checker, "z/bridge/devices", timeout=1) == [{"ieee_address": "0x1"}]
    broker.client().publish("z/bridge/devices", "", retain=True)  # the bridge retracting: no error
    assert checker.subscriptions == [] and checker.on_message is None


def test_receive_retained_times_out_without_a_retained_message():
    with pytest.raises(TimeoutError, match="z/bridge/devices"):
        receive_retained(FakeBroker().client(), "z/bridge/devices", timeout=0.05)
```

Create `tests/validation/test_live_mqtt.py`:
```python
"""Against a real Mosquitto (opt-in: VESPER_STACK_TESTS=1; needs `mosquitto` installed).

VESPER starts its own broker on a free local port; it never uses another broker or port 1883.
"""
import json
import os
import socket
import threading

import pytest

from tests.catalog.fixtures import ZIGBEE_DOC
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.exposure.mqtt import connect, disconnect
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.roundtrip import compare, receive_retained, source_definitions
from vesper.validation.stack import Mosquitto

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_STACK_TESTS") != "1", reason="set VESPER_STACK_TESTS=1")]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path):
    port = free_port()
    broker = Mosquitto(tmp_path, port=port)
    broker.start()
    runtime = Runtime(clock=LogicalClock())
    records = zigbee_records(ZIGBEE_DOC)
    placements = [Placement(runtime.add(r).id, f"vesper_live_{i:02d}") for i, r in enumerate(records)]
    bridge_client, checker = connect("127.0.0.1", port, "live-bridge"), connect("127.0.0.1", port, "live-check")
    bridge = Bridge(runtime, placements, bridge_client, discovery=False, zhc_version=ZIGBEE_DOC["version"])
    bridge.start()
    yield bridge, checker
    bridge.retract()
    disconnect(bridge_client)
    disconnect(checker)
    broker.stop()


def test_definitions_survive_a_real_broker_unchanged(live):
    bridge, checker = live
    devices = receive_retained(checker, "zigbee2mqtt/bridge/devices", timeout=10)
    report = compare(devices, {p.friendly_name: p.twin_id for p in bridge.placements}, source_definitions(ZIGBEE_DOC))
    assert report["identical"] == report["checked"] == len(bridge.placements)


def test_a_set_command_over_the_network_changes_the_twin_and_comes_back_as_state(live):
    bridge, checker = live
    plug = next(p for p in bridge.placements if "state" in bridge.runtime.twins[p.twin_id].features)
    topic, states, got = f"zigbee2mqtt/{plug.friendly_name}", [], threading.Event()

    def on_message(c, userdata, msg):
        if msg.topic == topic and msg.payload:
            states.append(json.loads(msg.payload))
            if states[-1].get("state") == "ON":
                got.set()

    checker.on_message = on_message
    checker.subscribe(topic)
    checker.publish(f"{topic}/set", json.dumps({"state": "ON"}))
    assert got.wait(10), states
    assert bridge.runtime.state(plug.twin_id)["state"] == "ON"
```

- [ ] **Step 3: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/exposure/test_roundtrip.py tests/validation/test_live_mqtt.py -q`

Expected: collection errors, `ModuleNotFoundError: No module named 'vesper.validation.roundtrip'` and `No module named 'vesper.exposure.mqtt'`.

- [ ] **Step 4: Implement**

Create `vesper/exposure/mqtt.py`:
```python
"""Connecting to an MQTT broker with paho-mqtt (EPL-2.0 or BSD-3-Clause)."""
from __future__ import annotations

import threading

import paho.mqtt.client as mqtt


def connect(host: str, port: int, client_id: str, timeout: float = 10.0) -> mqtt.Client:
    """A connected client with its network loop running on a background thread."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    ready = threading.Event()
    client.on_connect = lambda c, userdata, flags, reason, props: ready.set() if not reason.is_failure else None
    client.connect(host, port)
    client.loop_start()
    if not ready.wait(timeout):
        client.loop_stop()
        raise ConnectionError(f"no MQTT connection to {host}:{port} within {timeout:.0f} s")
    return client


def disconnect(client: mqtt.Client) -> None:
    client.disconnect()
    client.loop_stop()
```

Create `vesper/validation/roundtrip.py`:
```python
"""L0/L1 round trip (spec §4): every definition the bridge publishes in `bridge/devices` must equal the
source record in the pinned zigbee-herdsman-converters snapshot, field for field (0 diffs)."""
from __future__ import annotations

import json
import threading

from vesper.catalog.normalize.zigbee import zigbee_records


def source_definitions(doc: dict) -> dict[str, dict]:
    """VDS record id -> the definition Zigbee2MQTT would publish, built from the raw snapshot."""
    definitions = []
    for d in doc["definitions"]:
        base = {"source": "native", "model": d["model"], "vendor": d["vendor"], "description": d["description"],
                "exposes": d["exposes"] or [], "options": d["options"], "supports_ota": bool(d["ota"])}
        definitions.append(base)
        for w in d["white_label"]:
            definitions.append({**base, "vendor": w.get("vendor") or d["vendor"], "model": w.get("model"),
                                "description": w.get("description") or d["description"]})
    return {r["id"]: dfn for r, dfn in zip(zigbee_records(doc), definitions, strict=True)}


def diff_paths(a, b, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b), key=str):
            if key not in a or key not in b:
                out.append(f"{path}/{key}")
            else:
                out.extend(diff_paths(a[key], b[key], f"{path}/{key}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}[len {len(a)} != {len(b)}]"]
        return [p for i, (x, y) in enumerate(zip(a, b, strict=True)) for p in diff_paths(x, y, f"{path}[{i}]")]
    return [] if a == b and type(a) is type(b) else [path or "/"]


def compare(published: list[dict], twin_of: dict[str, str], sources: dict[str, dict]) -> dict:
    report = {"checked": 0, "identical": 0, "different": {}, "unmatched": []}
    for device in published:
        if device.get("type") == "Coordinator":
            continue
        twin_id = twin_of.get(device["friendly_name"])
        if twin_id not in sources:
            report["unmatched"].append(device["friendly_name"])
            continue
        report["checked"] += 1
        paths = diff_paths(device["definition"], sources[twin_id])
        if paths:
            report["different"][twin_id] = paths[:20]
        else:
            report["identical"] += 1
    return report


def receive_retained(client, topic: str, timeout: float = 30.0):
    """The retained JSON message on `topic`, read through a separate MQTT client that stops listening after."""
    got, ready = {}, threading.Event()

    def on_message(c, userdata, msg):
        if msg.topic == topic and msg.payload and not ready.is_set():  # an empty payload clears a retained message
            got["payload"] = json.loads(msg.payload)
            ready.set()

    client.on_message = on_message
    client.subscribe(topic)
    try:
        if not ready.wait(timeout):
            raise TimeoutError(f"no retained message on {topic} within {timeout:.0f} s")
    finally:
        client.unsubscribe(topic)
        client.on_message = None
    return got["payload"]
```

- [ ] **Step 5: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/exposure/test_roundtrip.py tests/validation/test_live_mqtt.py -q`

Expected: `5 passed, 2 skipped` (the live tests are opt-in).

- [ ] **Step 6: Run the live broker test (light job; shared-Mac rules apply)**

This starts Mosquitto on a free local port for about 10 seconds. It needs Mosquitto installed natively (macOS: `brew install mosquitto`).
- Run it only outside 03:50–04:50Z, 19:55–20:15Z and 23:58Z–00:30Z.
- First add a row to `/Users/huanbui/Research/MAC_COORDINATION.md` and message the peer sessions (`ListAgents`).
- If it cannot run now, ledger `Ruling: live broker test deferred to the live run` and continue.

Run: `VESPER_STACK_TESTS=1 taskpolicy -b .venv/bin/python -m pytest tests/validation/test_live_mqtt.py -q`

Expected: `2 passed`. Then `pgrep -fl mosquitto` shows no VESPER broker left (another project's broker with a Homebrew config may run; leave it alone). Close the ledger row.

- [ ] **Step 7: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add pyproject.toml vesper/exposure/mqtt.py vesper/validation/roundtrip.py tests/exposure/test_roundtrip.py tests/validation/test_live_mqtt.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): L0/L1 round trip of published definitions; live broker test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 8: L2 acceptance checks against Home Assistant's API

**Files:**
- Create: `vesper/validation/acceptance.py`, `tests/exposure/fake_ha.py`, `tests/exposure/test_acceptance.py`
- Modify: `pyproject.toml` (dev extra `jinja2>=3.1`)

**Interfaces:**
- Consumes: Task 3's `Runtime`; Task 4's `ExpectedEntity`; Task 5's `Bridge`, `Placement` and `FakeClient`; Task 6's `StackError` and `HomeAssistant.state/call` (duck-typed as `ha`).
- Produces:
  - `acceptance.AMBIENT`;
  - `select_products(records, *, per_category, seed, categories=None) -> list[vds]` (L1 Zigbee products only, seeded, stratified by category);
  - `check_product(ha, runtime, bridge, placement, *, timeout, poll) -> {id, category, friendly_name, entities, skipped, passed}`;
  - `summarize(products) -> {products, passed, categories_passing, by_category}`.
  - Test double: `tests.exposure.fake_ha.FakeHA(source)`, where `source` has `.published`, `.last(topic)` and `.deliver(topic, payload)` (a `FakeClient` or a `FakeBroker`).

- [ ] **Step 1: Add the dev dependency**

In `pyproject.toml`, replace:
```toml
dev = ["pytest>=8", "ruff>=0.5"]
```
with:
```toml
dev = ["jinja2>=3.1", "pytest>=8", "ruff>=0.5"]
```

Run: `uv pip install --python .venv/bin/python -e '.[dev]'`

Expected: the install finishes with `jinja2` present.

- [ ] **Step 2: Write the failing tests**

Create `tests/exposure/fake_ha.py`:
```python
"""A minimal in-memory Home Assistant for offline acceptance tests.

It reads the bridge's discovery and state messages from a FakeClient, renders the discovery templates with
Jinja2 as Home Assistant does, and turns service calls into MQTT commands delivered back to the bridge.
"""
import json

import jinja2

_ENV = jinja2.Environment()


def _render(template, value_json=None, value=None):
    return _ENV.from_string(template).render(value_json=value_json, value=value)


class FakeHA:
    def __init__(self, client):
        self.client = client

    def _entities(self):
        out = {}
        for topic, payload, _ in self.client.published:
            if topic.startswith("homeassistant/device/") and payload:
                for cfg in payload["cmps"].values():
                    out[cfg["default_entity_id"]] = (cfg, payload)
        return out

    def state(self, entity_id):
        found = self._entities().get(entity_id)
        if found is None:
            return None
        cfg, device = found
        attrs = {k: cfg[k] for k in ("device_class",) if k in cfg}
        if "unit_of_measurement" in cfg:
            attrs["unit_of_measurement"] = cfg["unit_of_measurement"]
        p = cfg["p"]
        if p == "event":
            last = self.client.last(cfg["state_topic"])
            if last:
                attrs["event_type"] = last["event_type"]
            return {"state": "2026-10-24T00:00:00+00:00" if last else "unknown", "attributes": attrs}
        payload = self.client.last(device["state_topic"])
        if payload is None:
            return {"state": "unknown", "attributes": attrs}
        if p == "light":
            return {"state": "on" if payload.get("state") == "ON" else "off", "attributes": attrs}
        rendered = _render(cfg["value_template"], value_json=payload)
        if p in ("binary_sensor", "switch"):
            return {"state": rendered.lower(), "attributes": attrs}
        if p == "lock":
            return {"state": "locked" if rendered == cfg["state_locked"] else "unlocked", "attributes": attrs}
        return {"state": rendered, "attributes": attrs}

    def call(self, domain, service, data):
        cfg, _ = self._entities()[data["entity_id"]]
        topic = cfg["command_topic"]
        if (domain, service) == ("switch", "turn_on"):
            payload = cfg["payload_on"]
        elif (domain, service) == ("light", "turn_on"):
            payload = json.dumps({"state": "ON", **{k: v for k, v in data.items() if k == "brightness"}})
        elif (domain, service) == ("lock", "lock"):
            payload = cfg["payload_lock"]
        elif (domain, service) == ("number", "set_value"):
            payload = _render(cfg["command_template"], value=data["value"])
        elif (domain, service) == ("select", "select_option"):
            payload = _render(cfg["command_template"], value=data["option"])
        elif (domain, service) == ("button", "press"):
            payload = cfg["payload_press"]
        else:
            raise ValueError(f"unsupported service {domain}.{service}")
        self.client.deliver(topic, payload)
```

Create `tests/exposure/test_acceptance.py`:
```python
from tests.exposure.fake_ha import FakeHA
from tests.exposure.fake_mqtt import FakeClient
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.acceptance import check_product, select_products, summarize
from vesper.validation.stack import StackError


def run(products):
    rt = Runtime(clock=LogicalClock())
    placements = [Placement(rt.add(r).id, f"vesper_{i:03d}") for i, r in enumerate(products)]
    client = FakeClient()
    bridge = Bridge(rt, placements, client)
    bridge.start()
    ha = FakeHA(client)
    rt.clock.advance(60)  # past every min_s, so reports are immediate
    return [check_product(ha, rt, bridge, p, timeout=0, poll=0) for p in placements], bridge, client


def test_selection_is_stratified_seeded_and_only_takes_l1_zigbee_products(vds):
    records = list(vds.values())
    picked = select_products(records, per_category=1, seed=7)
    assert len({r["identity"]["category"] for r in picked}) == len(picked)
    assert all(r["fidelity"]["reached"]["L1"] and r["id"].startswith("zigbee:") for r in picked)
    assert picked == select_products(list(reversed(records)), per_category=1, seed=7)


def test_every_kind_of_entity_passes_against_a_conforming_home_assistant(vds):
    products = [vds[m] for m in ("MCCGQ11LM", "PLUG-1", "LED1545G12", "PIR-1", "TH-1", "BTN-1")]
    results, _, _ = run(products)
    assert [r["passed"] for r in results] == [True] * len(products), results
    kinds = {e["probe"]["kind"] for r in results for e in r["entities"] if "probe" in e}
    assert kinds == {"switch.turn_on", "light.turn_on", "stimulus:contact", "stimulus:presence",
                     "stimulus:ambient", "stimulus:load", "stimulus:button_press"}


def test_a_platform_that_does_not_reach_the_twin_fails_the_probe(vds):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, "plug")
    client = FakeClient()
    bridge = Bridge(rt, [p], client)
    bridge.start()
    rt.clock.advance(60)
    client.on_message = lambda *a: None  # commands from Home Assistant never reach the bridge
    r = check_product(FakeHA(client), rt, bridge, p, timeout=0, poll=0)
    switch = next(e for e in r["entities"] if e["platform"] == "switch")
    assert switch["probe"] == {"ok": False, "kind": "switch.turn_on", "twin_ok": False, "ha_ok": False}
    assert r["passed"] is False


def test_a_missing_entity_or_wrong_unit_fails(vds):
    results, bridge, client = run([vds["TH-1"]])
    p = bridge.placements[0]
    bridge.expected[p.twin_id] = [e.__class__(e.entity_id, e.platform, e.device_class, "K", e.feature,
                                              e.controllable) for e in bridge.expected[p.twin_id]]
    r = check_product(FakeHA(client), bridge.runtime, bridge, p, timeout=0, poll=0)
    assert not r["passed"] and any(e["attributes_ok"] is False for e in r["entities"])


def test_summary_counts_categories_that_fully_pass():
    s = summarize([{"category": "a", "passed": True}, {"category": "a", "passed": False},
                   {"category": "b", "passed": True}])
    assert s == {"products": 3, "passed": 2, "categories_passing": ["b"],
                 "by_category": {"a": {"products": 2, "passed": 1}, "b": {"products": 1, "passed": 1}}}


def test_retract_clears_every_retained_message(vds):
    _, bridge, client = run([vds["PLUG-1"]])
    bridge.retract()
    retained = {}
    for topic, payload, retain in client.published:
        if retain:
            retained[topic] = payload
    assert all(v is None for v in retained.values())


def test_an_entity_home_assistant_never_creates_fails_the_product_without_hanging(vds):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, "plug")
    bridge = Bridge(rt, [p], FakeClient())
    bridge.start()

    class NoEntities(FakeHA):
        def state(self, entity_id):
            return None

    r = check_product(NoEntities(bridge.client), rt, bridge, p, timeout=0, poll=0)
    assert r["passed"] is False and not any(e["created"] for e in r["entities"])


def test_a_service_call_home_assistant_refuses_fails_that_probe_only(vds):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, "plug")
    client = FakeClient()
    bridge = Bridge(rt, [p], client)
    bridge.start()
    rt.clock.advance(60)

    class Refusing(FakeHA):
        def call(self, domain, service, data):
            raise StackError(f"service {domain}.{service} failed (400): bad value")

    r = check_product(Refusing(client), rt, bridge, p, timeout=0, poll=0)
    switch = next(e for e in r["entities"] if e["platform"] == "switch")
    assert switch["probe"]["ok"] is False and "400" in switch["probe"]["error"]
    assert r["passed"] is False
```

- [ ] **Step 3: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/exposure/test_acceptance.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.validation.acceptance'`.

- [ ] **Step 4: Implement**

Create `vesper/validation/acceptance.py`:
```python
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
```

- [ ] **Step 5: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/exposure -q`

Expected: `43 passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add pyproject.toml vesper/validation/acceptance.py tests/exposure/fake_ha.py tests/exposure/test_acceptance.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): L2 acceptance checks through Home Assistant's own API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 9: Home files and the experiment commands

**Files:**
- Create: `vesper/twins/home.py`, `tests/twins/test_home.py`, `tests/validation/test_cli.py`
- Modify: `vesper/cli.py`

**Interfaces:**
- Consumes: everything above; Plan B's `load_snapshot` and `zigbee_records`.
- Produces:
  - `home.Home(records, placements, host, port, seed)`;
  - `load_catalog(catalog_dir) -> {id: vds}`;
  - `load_home(path, catalog_dir) -> Home`;
  - the CLI:
    - `vesper setup ha [--root .vesper]`;
    - `vesper twins run HOME [--catalog catalog/vds]`;
    - `vesper validate roundtrip [--raw-root catalog/raw] [--root .vesper] [--mqtt-port 18830] [--external-broker] [--timeout 120] [--out PATH]`;
    - `vesper validate acceptance [--catalog catalog/vds] [--root .vesper] [--per-category 5] [--category C]... [--seed 0] [--mqtt-port 18830] [--ha-port 18123] [--timeout 20] [--keep] [--out PATH]`.
  - Reports default to `results/device-twins/<kind>-<UTC timestamp>.json`.
  - Exit code: 0 only when every check passed.

- [ ] **Step 1: Write the failing tests**

Create `tests/twins/test_home.py`:
```python
import json

import pytest

from tests.twins.conftest import records
from vesper.twins.home import load_catalog, load_home


@pytest.fixture
def catalog(tmp_path):
    out = tmp_path / "vds"
    out.mkdir()
    (out / "zigbee.json").write_text(json.dumps(list(records().values())))
    return out


def test_a_home_places_real_products_under_friendly_names(tmp_path, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("broker: {host: 127.0.0.1, port: 18831}\nseed: 4\ndevices:\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: hall/door}\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: kitchen/door}\n"
                    "  - {product: 'zigbee:acme:th-1', name: kitchen/climate}\n")
    h = load_home(home, catalog)
    assert [p.friendly_name for p in h.placements] == ["hall/door", "kitchen/door", "kitchen/climate"]
    assert len({p.twin_id for p in h.placements}) == 3  # the same product twice is two devices
    assert [r["id"] for r in h.records] == ["zigbee:aqara:mccgq11lm"] * 2 + ["zigbee:acme:th-1"]
    assert (h.host, h.port, h.seed) == ("127.0.0.1", 18831, 4)


def test_broker_and_seed_default_to_vespers_own_broker(tmp_path, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("devices:\n  - {product: 'zigbee:acme:th-1', name: t}\n")
    h = load_home(home, catalog)
    assert (h.host, h.port, h.seed) == ("127.0.0.1", 18830, 0)


def test_a_product_missing_from_the_catalog_is_named(tmp_path, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("devices:\n  - {product: 'zigbee:acme:nope', name: x}\n")
    with pytest.raises(KeyError, match="zigbee:acme:nope"):
        load_home(home, catalog)


def test_an_unbuilt_catalog_says_how_to_build_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="vesper catalog build"):
        load_catalog(tmp_path)
```

Create `tests/validation/test_cli.py`:
```python
"""The experiment commands, wired to an in-memory broker and Home Assistant: no process is started."""
import json
import time
from types import SimpleNamespace

import pytest

import vesper.exposure.mqtt as mqtt_mod
import vesper.twins.clock as clock_mod
import vesper.validation.acceptance as acceptance_mod
import vesper.validation.roundtrip as roundtrip_mod
import vesper.validation.stack as stack_mod
from tests.catalog.conftest import write_all
from tests.twins.conftest import records
from tests.exposure.fake_ha import FakeHA
from tests.exposure.fake_mqtt import FakeBroker
from vesper import cli


@pytest.fixture
def broker(monkeypatch):
    b = FakeBroker()
    connected = []

    def connect(host, port, client_id, timeout=10.0):
        connected.append((host, port, client_id))
        return b.client()

    monkeypatch.setattr(mqtt_mod, "connect", connect)
    monkeypatch.setattr(mqtt_mod, "disconnect", lambda c: setattr(c, "connected", False))
    b.connected = connected
    return b


@pytest.fixture
def catalog(tmp_path):
    out = tmp_path / "vds"
    out.mkdir()
    (out / "zigbee.json").write_text(json.dumps(list(records().values())))
    (out / "build_report.json").write_text(json.dumps({"inputs": {"zigbee": {"version": "26.123.0"}}}))
    return out


class FakeMosquitto:
    instances = []

    def __init__(self, root, port=18830, **kwargs):
        self.root, self.port, self.events = root, port, []
        FakeMosquitto.instances.append(self)

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


@pytest.fixture
def mosquitto(monkeypatch):
    FakeMosquitto.instances = []
    monkeypatch.setattr(stack_mod, "Mosquitto", FakeMosquitto)
    return FakeMosquitto.instances


def test_setup_ha_reports_what_it_installed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(stack_mod, "setup_ha", lambda root: ["home-assistant-frontend==20260930.4"])
    assert cli.main(["setup", "ha", "--root", str(tmp_path)]) == 0
    assert "home-assistant-frontend==20260930.4" in capsys.readouterr().out


def test_setup_ha_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    def fail(root):
        raise stack_mod.StackError("uv not found")

    monkeypatch.setattr(stack_mod, "setup_ha", fail)
    assert cli.main(["setup", "ha", "--root", str(tmp_path)]) == 1
    assert "FAILED setup: uv not found" in capsys.readouterr().err


def test_twins_run_exposes_the_home_and_goes_offline_on_ctrl_c(monkeypatch, tmp_path, broker, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("broker: {host: 127.0.0.1, port: 18830}\nseed: 3\ndevices:\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: hall/door}\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: kitchen/door}\n")

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupt)
    assert cli.main(["twins", "run", str(home), "--catalog", str(catalog)]) == 0
    assert broker.connected == [("127.0.0.1", 18830, "vesper-bridge")]
    devices = broker.last("zigbee2mqtt/bridge/devices")
    assert [d["friendly_name"] for d in devices] == ["Coordinator", "hall/door", "kitchen/door"]
    assert devices[1]["ieee_address"] != devices[2]["ieee_address"]
    assert broker.last("zigbee2mqtt/bridge/info")["zigbee_herdsman_converters"]["version"] == "26.123.0"
    assert broker.last("zigbee2mqtt/bridge/state") == {"state": "offline"}


def test_roundtrip_publishes_every_definition_unchanged(monkeypatch, tmp_path, broker, mosquitto, capsys):
    raw = write_all(tmp_path / "raw")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--external-broker"]) == 0
    assert mosquitto == []  # an external broker was asked for: none is started
    [path] = (tmp_path / "results" / "device-twins").glob("roundtrip-*.json")
    report = json.loads(path.read_text())
    assert report["checked"] == report["identical"] == 13
    assert report["different"] == {} and report["unmatched"] == []
    assert report["zhc_version"] == "26.123.0" and report["l1_with_diffs"] == []
    assert "13/13 definitions identical" in capsys.readouterr().out
    assert broker.retained == {}  # the bridge retracted every retained message it published


def test_roundtrip_reports_a_definition_that_changed_on_the_way(monkeypatch, tmp_path, broker, mosquitto):
    raw = write_all(tmp_path / "raw")
    real, changed = roundtrip_mod.source_definitions, []

    def tampered(doc):
        sources = real(doc)
        first = sorted(sources)[0]
        sources[first] = {**sources[first], "description": "something else"}
        changed.append(first)
        return sources

    monkeypatch.setattr(roundtrip_mod, "source_definitions", tampered)
    out = tmp_path / "rt.json"
    assert cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--external-broker", "--out", str(out)]) == 1
    assert list(json.loads(out.read_text())["different"]) == changed


def test_roundtrip_starts_and_always_stops_its_own_broker(monkeypatch, tmp_path, mosquitto):
    raw = write_all(tmp_path / "raw")

    def refused(*a, **k):
        raise ConnectionError("no MQTT connection")

    monkeypatch.setattr(mqtt_mod, "connect", refused)
    with pytest.raises(ConnectionError):
        cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--root", str(tmp_path / ".vesper")])
    [m] = mosquitto
    assert m.port == 18830 and m.events == ["start", "stop"]


class StackHA:
    """`stack.HomeAssistant` replaced by the in-memory one, recording its lifecycle."""
    instances = []

    def __init__(self, root, port=18123, **kwargs):
        self.port, self.events, self.fail_start = port, [], False
        StackHA.instances.append(self)

    def start(self):
        self.events.append("start")
        if StackHA.fail_start:
            raise stack_mod.StackError("Home Assistant did not answer")

    def onboard(self):
        self.events.append("onboard")

    def ensure_mqtt(self, port):
        self.events.append(("ensure_mqtt", port))

    def stop(self):
        self.events.append("stop")

    def state(self, entity_id):
        return FakeHA(StackHA.broker).state(entity_id)

    def call(self, domain, service, data):
        FakeHA(StackHA.broker).call(domain, service, data)


@pytest.fixture
def stack(monkeypatch, broker, mosquitto):
    StackHA.instances, StackHA.broker, StackHA.fail_start = [], broker, False
    monkeypatch.setattr(stack_mod, "HomeAssistant", StackHA)
    clocks = []

    class Clock(clock_mod.LogicalClock):
        def __init__(self):
            super().__init__()
            clocks.append(self)

        def close(self):
            pass

    # The twins keep simulated time; each poll of Home Assistant lets 0.25 s of it pass.
    monkeypatch.setattr(clock_mod, "WallClock", Clock)
    monkeypatch.setattr(acceptance_mod, "time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=lambda s: clocks[-1].advance(s)))
    return SimpleNamespace(ha=StackHA.instances, mosquitto=mosquitto)


def test_acceptance_runs_the_whole_stack_and_writes_a_report(tmp_path, catalog, stack, capsys):
    out = tmp_path / "acc.json"
    code = cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--per-category", "1", "--timeout", "2", "--out", str(out)])
    report = json.loads(out.read_text())
    assert code == 0, report["products"]
    [ha], [m] = stack.ha, stack.mosquitto
    assert ha.events == ["start", "onboard", ("ensure_mqtt", 18830), "stop"] and ha.port == 18123
    assert m.events == ["start", "stop"]
    s = report["summary"]
    assert s["passed"] == s["products"] == len(s["by_category"]) and s["products"] >= 5
    assert report["ha_version"] == "2026.10.2" and report["rejections"] == []
    assert f"{s['passed']}/{s['products']} products passed" in capsys.readouterr().out


def test_acceptance_stops_the_stack_when_home_assistant_fails(tmp_path, catalog, stack, capsys):
    StackHA.fail_start = True
    code = cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--out", str(tmp_path / "acc.json")])
    assert code == 1 and "FAILED acceptance: Home Assistant did not answer" in capsys.readouterr().err
    assert stack.ha[0].events == ["start", "stop"] and stack.mosquitto[0].events == ["start", "stop"]
    assert not (tmp_path / "acc.json").exists()


def test_ctrl_c_during_acceptance_still_stops_home_assistant_and_the_broker(monkeypatch, tmp_path, catalog, stack):
    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(StackHA, "onboard", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper")])
    assert stack.ha[0].events == ["start", "stop"] and stack.mosquitto[0].events == ["start", "stop"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_home.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.twins.home'`.

Run: `.venv/bin/python -m pytest tests/validation/test_cli.py -q`

Expected: `9 failed`: eight with `SystemExit: 2` (argparse: `invalid choice: 'setup'` or `'validate'`), and the `twins run` test with `AttributeError: module 'vesper.cli' has no attribute 'time'`.

- [ ] **Step 3: Implement**

Create `vesper/twins/home.py`:
```python
"""A home file: which real products are installed, under which Zigbee2MQTT friendly names.

    broker: {host: 127.0.0.1, port: 18830}
    seed: 0
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: "hall/door"}

Placement in Habitat rooms is sub-project #2; here the room is only part of the friendly name.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from vesper.exposure.z2m_bridge import Placement


@dataclass(frozen=True)
class Home:
    records: list[dict]
    placements: list[Placement]
    host: str
    port: int
    seed: int


def load_catalog(catalog_dir: Path) -> dict[str, dict]:
    records = {}
    for name in ("zigbee", "matter", "zwave"):
        path = Path(catalog_dir) / f"{name}.json"
        if path.is_file():
            records.update((r["id"], r) for r in json.loads(path.read_text(encoding="utf-8")))
    if not records:
        raise FileNotFoundError(f"no catalog in {catalog_dir}; run `vesper catalog build` first")
    return records


def load_home(path: Path, catalog_dir: Path) -> Home:
    spec = yaml.safe_load(Path(path).read_text()) or {}
    catalog = load_catalog(catalog_dir)
    devices = spec.get("devices") or []
    missing = [d.get("product") for d in devices if d.get("product") not in catalog]
    if missing:
        raise KeyError(f"products not in the catalog: {missing}")
    broker = spec.get("broker") or {}
    return Home(records=[catalog[d["product"]] for d in devices],
                placements=[Placement(f"{d['product']}@{d['name']}", d["name"]) for d in devices],
                host=broker.get("host", "127.0.0.1"), port=int(broker.get("port", 18830)),
                seed=int(spec.get("seed", 0)))
```

In `vesper/cli.py`, replace:
```python
"""`vesper` command line (spec §5): `vesper catalog sync` and `vesper catalog build`."""
from __future__ import annotations
```
with:
```python
"""`vesper` command line (spec §5): catalog, Home Assistant set-up, twins and validation."""
from __future__ import annotations
```

In `vesper/cli.py`, replace:
```python
import argparse
import sys
```
with:
```python
import argparse
import json
import sys
```

In `vesper/cli.py`, replace:
```python
import sys
from pathlib import Path
```
with:
```python
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
```

In `vesper/cli.py`, replace:
```python

def main(argv: list[str] | None = None) -> int:
```
with:
```python

def _write_report(out: str | None, kind: str, report: dict) -> Path:
    path = Path(out) if out else Path("results/device-twins") / f"{kind}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return path


def _zhc_version(catalog_dir: Path) -> str:
    report = Path(catalog_dir) / "build_report.json"
    if report.is_file():
        return json.loads(report.read_text()).get("inputs", {}).get("zigbee", {}).get("version", "unknown")
    return "unknown"


def _setup(args) -> int:
    from vesper.validation.stack import StackError, setup_ha
    try:
        requirements = setup_ha(Path(args.root))
    except StackError as e:
        print(f"FAILED setup: {e}", file=sys.stderr)
        return 1
    print(f"Home Assistant ready in {Path(args.root) / 'ha' / 'venv'} (extra packages: {', '.join(requirements)})")
    return 0


def _twins_run(args) -> int:
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_home
    from vesper.twins.runtime import Runtime
    home = load_home(Path(args.home), Path(args.catalog))
    runtime = Runtime(clock=WallClock(), seed=home.seed)
    for record, placement in zip(home.records, home.placements, strict=True):
        runtime.add(record, twin_id=placement.twin_id)
    client = connect(home.host, home.port, "vesper-bridge")
    bridge = Bridge(runtime, home.placements, client, zhc_version=_zhc_version(Path(args.catalog)))
    bridge.start()
    print(f"{len(home.placements)} twins online at mqtt://{home.host}:{home.port}; Ctrl-C stops")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        disconnect(client)
        runtime.clock.close()
    return 0


def _validate_roundtrip(args) -> int:
    from vesper.catalog.normalize.zigbee import zigbee_records
    from vesper.catalog.snapshot import load_snapshot
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge, Placement
    from vesper.twins.clock import LogicalClock
    from vesper.twins.runtime import Runtime
    from vesper.validation.roundtrip import compare, receive_retained, source_definitions
    from vesper.validation.stack import Mosquitto
    _, files = load_snapshot(Path(args.raw_root), "zigbee")
    doc = json.loads(files["definitions.json"])
    records = zigbee_records(doc)
    broker = None if args.external_broker else Mosquitto(Path(args.root), port=args.mqtt_port)
    if broker:
        broker.start()
    try:
        runtime = Runtime(clock=LogicalClock())
        placements = [Placement(runtime.add(r).id, f"vesper_rt_{i:05d}") for i, r in enumerate(records)]
        publisher = connect("127.0.0.1", args.mqtt_port, "vesper-roundtrip-bridge")
        checker = connect("127.0.0.1", args.mqtt_port, "vesper-roundtrip-check")
        try:
            bridge = Bridge(runtime, placements, publisher, discovery=False, zhc_version=doc["version"])
            bridge.start()
            devices = receive_retained(checker, f"{bridge.base_topic}/bridge/devices", timeout=args.timeout)
            report = compare(devices, {p.friendly_name: p.twin_id for p in placements}, source_definitions(doc))
            bridge.retract()
        finally:
            disconnect(publisher)
            disconnect(checker)
    finally:
        if broker:
            broker.stop()
    l1 = {r["id"] for r in records if r["fidelity"]["reached"]["L1"]}
    report |= {"zhc_version": doc["version"], "l1_products": len(l1),
               "l1_with_diffs": sorted(set(report["different"]) & l1)}
    path = _write_report(args.out, "roundtrip", report)
    print(f"round trip: {report['identical']}/{report['checked']} definitions identical "
          f"({len(report['different'])} with diffs, {len(report['unmatched'])} unmatched); report {path}")
    return 0 if not report["different"] and not report["unmatched"] else 1


def _validate_acceptance(args) -> int:
    from vesper.exposure.mqtt import connect, disconnect
    from vesper.exposure.z2m_bridge import Bridge, Placement
    from vesper.twins.clock import WallClock
    from vesper.twins.home import load_catalog
    from vesper.twins.runtime import Runtime
    from vesper.validation.acceptance import check_product, select_products, summarize
    from vesper.validation.stack import HA_VERSION, HomeAssistant, Mosquitto, StackError
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("zigbee:")]
    products = select_products(records, per_category=args.per_category, seed=args.seed,
                               categories=args.category or None)
    broker = Mosquitto(Path(args.root), port=args.mqtt_port)
    ha = HomeAssistant(Path(args.root), port=args.ha_port)
    try:
        broker.start()
        ha.start()
        ha.onboard()
        ha.ensure_mqtt(args.mqtt_port)
        runtime = Runtime(clock=WallClock(), seed=args.seed)
        placements = [Placement(runtime.add(r).id, f"vesper_acc_{i:04d}") for i, r in enumerate(products)]
        client = connect("127.0.0.1", args.mqtt_port, "vesper-acceptance-bridge")
        try:
            bridge = Bridge(runtime, placements, client, zhc_version=_zhc_version(Path(args.catalog)))
            bridge.start()
            results = [check_product(ha, runtime, bridge, p, timeout=args.timeout, poll=0.25) for p in placements]
            if not args.keep:
                bridge.retract()
            bridge.stop()
        finally:
            disconnect(client)
            runtime.clock.close()
    except StackError as e:
        print(f"FAILED acceptance: {e}", file=sys.stderr)
        return 1
    finally:
        ha.stop()
        broker.stop()
    summary = summarize(results)
    report = {"summary": summary, "products": results, "ha_version": HA_VERSION, "seed": args.seed,
              "per_category": args.per_category, "rejections": bridge.rejections, "faulted": runtime.faulted,
              "notes": runtime.notes[:500]}
    path = _write_report(args.out, "acceptance", report)
    print(f"acceptance: {summary['passed']}/{summary['products']} products passed; "
          f"{len(summary['categories_passing'])} categories fully passing; report {path}")
    return 0 if summary["passed"] == summary["products"] else 1


def main(argv: list[str] | None = None) -> int:
```

In `vesper/cli.py`, replace:
```python
    build.add_argument("--release", action="store_true", help="keep only records whose sources allow redistribution")
    args = parser.parse_args(argv)
```
with:
```python
    build.add_argument("--release", action="store_true", help="keep only records whose sources allow redistribution")

    setup = commands.add_parser("setup", help="install local tools")
    setup_actions = setup.add_subparsers(dest="action", required=True)
    setup_ha_cmd = setup_actions.add_parser("ha", help="Home Assistant Core in its own venv (needs uv)")
    setup_ha_cmd.add_argument("--root", default=".vesper")

    twins = commands.add_parser("twins", help="run virtual devices")
    twin_actions = twins.add_subparsers(dest="action", required=True)
    run = twin_actions.add_parser("run", help="expose a home's twins over MQTT until Ctrl-C")
    run.add_argument("home")
    run.add_argument("--catalog", default="catalog/vds")

    validate = commands.add_parser("validate", help="software-only validation (spec §4)")
    checks = validate.add_subparsers(dest="action", required=True)
    rt = checks.add_parser("roundtrip", help="bridge definitions vs the source snapshot (L0/L1)")
    rt.add_argument("--raw-root", default="catalog/raw")
    rt.add_argument("--root", default=".vesper")
    rt.add_argument("--mqtt-port", type=int, default=18830)
    rt.add_argument("--external-broker", action="store_true", help="use a broker already on --mqtt-port")
    rt.add_argument("--timeout", type=float, default=120.0)
    rt.add_argument("--out")
    acc = checks.add_parser("acceptance", help="unmodified Home Assistant discovers and controls twins (L2)")
    acc.add_argument("--catalog", default="catalog/vds")
    acc.add_argument("--root", default=".vesper")
    acc.add_argument("--per-category", type=int, default=5)
    acc.add_argument("--category", action="append", help="limit to a category (repeatable)")
    acc.add_argument("--seed", type=int, default=0)
    acc.add_argument("--mqtt-port", type=int, default=18830)
    acc.add_argument("--ha-port", type=int, default=18123)
    acc.add_argument("--timeout", type=float, default=20.0)
    acc.add_argument("--keep", action="store_true", help="leave the twins discovered in Home Assistant")
    acc.add_argument("--out")
    args = parser.parse_args(argv)
```

In `vesper/cli.py`, replace:
```python
    args = parser.parse_args(argv)
    return _sync(args) if args.action == "sync" else _build(args)
```
with:
```python
    args = parser.parse_args(argv)
    if args.command == "catalog":
        return _sync(args) if args.action == "sync" else _build(args)
    if args.command == "setup":
        return _setup(args)
    if args.command == "twins":
        return _twins_run(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins/test_home.py tests/validation/test_cli.py tests/catalog/test_cli.py -q`

Expected: `20 passed`.

- [ ] **Step 5: Whole suite and lint**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `243 passed, 5 skipped` and `All checks passed!` (the count includes Plan A and B's tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/home.py vesper/cli.py tests/twins/test_home.py tests/validation/test_cli.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(cli): setup ha, twins run, validate roundtrip and acceptance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 10: The experiment run book

**Files:**
- Create: `docs/experiments/device-twins-runbook.md`

**Interfaces:**
- Consumes: the CLI of Task 9.
- Produces: the exact procedure for the live runs after 2026-10-24T02:30Z (spec §4 rows L0/L1 and L2 scale tier).

- [ ] **Step 1: Write the run book**

Create `docs/experiments/device-twins-runbook.md`:
```markdown
# Run book: device-twin experiments (scale tier)

These two experiments produce the paper's success criteria 2 and 3 for Zigbee-style twins (spec §1, §4). Both are software-only and laptop-sized, and need no Docker:

| Experiment | Level | Command | Pass criterion (spec §4) |
|---|---|---|---|
| Round trip | L0/L1 | `vesper validate roundtrip` | 0 definition diffs for every product counted at L1 |
| Platform acceptance | L2, scale tier | `vesper validate acceptance` | every sampled product's entities created with the right platform, device class and unit, and every probe passing; at least 8 categories |

## Before any run

1. The catalog exists. If not, run `vesper catalog sync` (network) then `vesper catalog build`. This gives `catalog/raw/` and `catalog/vds/`.
2. Mosquitto is installed natively (macOS: `brew install mosquitto`; Debian/Ubuntu: `apt install mosquitto`). VESPER starts its own instance; it does not use a running one.
3. Home Assistant is set up once, for acceptance only:

       .venv/bin/vesper setup ha

   This needs `uv` and network access to PyPI. It creates `.vesper/ha/venv` (~1 GB) with Python 3.14, Home Assistant Core 2026.10.2 and the requirements of the `frontend`, `mqtt` and `hassio` integrations. Home Assistant later runs with `--skip-pip`, so it never installs anything itself.

### On the shared lab Mac only

- Read `/Users/huanbui/Research/MAC_COORDINATION.md` and `~/Research/BUSY_WINDOWS.json`. Pick a time outside every window.
- No Home Assistant process before 2026-10-24T02:30Z (another project's experiment).
- The PI has said go for this run, in this session.
- Add a ledger row (start, session, command, cores, memory, Docker: no) and message the peer sessions.
- Run every command under `taskpolicy -b`, and stop if memory pressure (`sysctl -n kern.memorystatus_vm_pressure_level`) stays at 2 or more.
- VESPER uses only 127.0.0.1:18830 (Mosquitto) and 127.0.0.1:18123 (Home Assistant). Never pass `--mqtt-port 1883` or `--ha-port 8123`: those belong to another project on this Mac.

## Experiment 1: round trip (L0/L1)

    taskpolicy -b .venv/bin/vesper validate roundtrip

- Starts Mosquitto on 18830 and loads the pinned zigbee-herdsman-converters snapshot.
- Creates one twin per definition and publishes them all through the bridge.
- Reads the retained `zigbee2mqtt/bridge/devices` back with a separate client, and diffs each definition against the source.
- Retracts every retained message and stops the broker.

Budget: a few minutes, 1 core, <0.5 GB.

Output: `results/device-twins/roundtrip-<UTC>.json`, holding `checked`, `identical`, `different` (record id → differing paths), `unmatched`, `l1_products`, `l1_with_diffs` and `zhc_version`.

Pass: `different` and `unmatched` are both empty (exit code 0). The paper reports `identical/checked` and `l1_with_diffs` (must be `[]`).

## Experiment 2: platform acceptance (L2, scale tier)

    taskpolicy -b .venv/bin/vesper validate acceptance --per-category 5 --seed 0

What it does:
1. Starts Mosquitto (18830) and Home Assistant (18123).
2. On the first run only, onboards Home Assistant headless; the refresh token is saved in `.vesper/ha/auth.json`, mode 600.
3. Adds the MQTT integration for 127.0.0.1:18830 through the config-flow API. It refuses if Home Assistant already has an MQTT entry for any other broker.
4. Samples L1 Zigbee products, five per category, seeded.
5. For each product, waits for every expected entity in Home Assistant's REST API and checks its device class and unit.
6. Runs one probe per entity:
   - a service call that must reach the twin and come back as state (switch, light, lock, number, select);
   - or a physical stimulus that must show up in Home Assistant (contact, presence, ambient, load, button events).
7. Retracts the twins and stops both services.

Options:
- `--keep` leaves the twins discovered in Home Assistant, for a look in its UI at http://127.0.0.1:18123 while it is still running;
- `--category <name>` (repeatable) restricts the sample.

Budget: under 1 h for ~40 products, 1–2 cores, ~1 GB. Reports from sensors with a minimum reporting interval (for example temperature, 10 s) arrive after that interval, exactly as on the real product. Allow `--timeout` of at least 20 s.

Output: `results/device-twins/acceptance-<UTC>.json`, holding:
- `summary`: `products`, `passed`, `categories_passing`, `by_category`;
- per product: each entity's `created`, `attributes_ok` and `probe` (with Home Assistant's error message if it refused a call), plus the `skipped` features and why;
- `rejections`, `faulted`, `notes`, `ha_version` and `seed`.

Pass: `passed == products` and `len(categories_passing) >= 8` (exit code 0 means every product passed). The paper reports per-category pass counts and every skipped entity type (climate, cover and fan are not exposed yet: Plan D/E).

## Running twins for your own Home Assistant

A home file lists real products by catalog id and gives each a Zigbee2MQTT friendly name:

    broker: {host: 127.0.0.1, port: 18830}
    seed: 0
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: "hall/door"}
      - {product: "zigbee:aqara:mccgq11lm", name: "kitchen/door"}

Then:

    .venv/bin/vesper twins run home.yaml

This runs until Ctrl-C. Other processes drive the twins over MQTT:
- `vesper/control/stimulate` with `{"device": "hall/door", "stimulus": {"type": "contact", "state": "opened"}}`;
- `vesper/control/operate` with `{"device": ..., "feature": ..., "value": ...}`.

## When something goes wrong

| Message | Cause | Fix |
|---|---|---|
| `port 18830 is already in use` / `port 18123 is already in use` | an earlier run is still up, or another program uses the port | `pgrep -fl "mosquitto -c .*\.vesper"` / `pgrep -fl "hass -c .*\.vesper"`; stop only VESPER's own processes |
| `Home Assistant is already onboarded but .../auth.json is missing` | an earlier run onboarded Home Assistant but never saved its token | with Home Assistant stopped, delete `.vesper/ha/config/.storage` (VESPER's own instance only) and run again |
| `Home Assistant entered recovery mode (it would bind port 8123)` | an integration failed to load, usually a missing requirement; VESPER stops it at once | rerun `vesper setup ha`; read `.vesper/ha/hass.log` |
| `Home Assistant has an MQTT entry for another broker` | the config was pointed at a different broker by hand | remove that entry in Home Assistant, or delete `.vesper/ha/config/.storage` |
| a product fails with `created: false` | Home Assistant rejected its discovery message | the reason is in `.vesper/ha/hass.log`; this is a real finding: report it |

## Afterwards

Close the ledger row with the actual end time. Keep the JSON reports: they are the evidence the paper cites. Record the run (command, commit, report path, summary numbers) as an RKA note.
```

- [ ] **Step 2: Check every command in it parses**

Run: `.venv/bin/vesper setup ha --help >/dev/null && .venv/bin/vesper twins run --help >/dev/null && .venv/bin/vesper validate roundtrip --help >/dev/null && .venv/bin/vesper validate acceptance --help >/dev/null && echo ok`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add docs/experiments/device-twins-runbook.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "docs: run book for the device-twin experiments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

