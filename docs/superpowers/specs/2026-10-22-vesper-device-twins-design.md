# VESPER sub-project #1: device catalog and virtual-device twins — design

- **Date:** 2026-10-22
- **Status:** design approved section by section by the PI (brainstorming, 2026-10-22); awaiting review of this written spec.
- **RKA:** directives `jrn_01M5JQ8Y4CGW6TCMCN3SGXWCYA`, `jrn_01M5JQJZ4GX32H5A3NQCF93EQ3`, `jrn_01M5JTKBCHPMWF3FR3KT1NHFHB`; research synthesis `jrn_01M5JRG0XTH5P2KWVEX8P13TJK`.

## 0. Context

VESPER is being rebuilt as a **pipeline for other researchers**. A user:
1. creates a 3D virtual home on Habitat's scenes;
2. selects devices from the **real market**;
3. gets virtual devices that mirror those products as faithfully as possible, "from config to firmware";
4. watches residents live with them.

It is not a security or networking paper. The target is an IEEE IoT-J platform paper.

**Constraints set by the PI**
- No robots. Habitat is used for its homes, not for robot training.
- **Laptop or desktop only; no extra hardware, ever.** All validation is software-only.
- Development happens on the shared lab Mac: at most 2 cores per job, background priority, no job without the PI's go.

**The four sub-projects**, each with its own spec, plan and build:
1. **Device catalog and twins** (this spec)
2. Home composer: HSSD homes, device placement, real home setups and automations
3. Residents: multiple embodied occupants who operate devices and react to them
4. Validation at scale, and the use cases: sim-to-real sensing, automation testing, energy and comfort, security

This spec covers #1. It also defines the interface that #2 and #3 use to drive devices.

## 1. Goal, scope, success criteria

**Goal.** Turn real market products into virtual devices automatically. Each device is as faithful as the available sources allow, and unmodified smart-home platforms accept it as the real product.

**Fidelity ladder.** Every device reaches the highest level its sources support.

| Level | Reproduces | Main sources and tools |
|---|---|---|
| L0 Identity | vendor, model, product IDs, category | Zigbee2MQTT supported devices; Matter DCL; Z-Wave JS DB |
| L1 Configuration | features, states, ranges, units, Home Assistant mapping | zigbee-herdsman-converters `exposes`; Matter data-model XML; Z-Wave JS params |
| L2 Protocol-faithful | an interface that real platforms accept as the product | Zigbee2MQTT MQTT API (scale tier); real Matter nodes via matter.js (Matter tier) |
| L3 Behavior/timing | reporting rules, latency, battery/sleep, power draw | Zigbee2MQTT/ZCL reporting defaults; Matter ICD/subscription timing; published latency data; REFIT and UK-DALE profiles |
| L4 Firmware anchor | the same firmware code on an emulated SoC | Renode (nRF52840), Espressif QEMU (ESP32) |
| L5 Stock vendor firmware | out of scope (not feasible at scale on a laptop) | — |

**In scope**
- Catalog ingestion: Zigbee2MQTT definitions, the Matter data model and DCL, the Z-Wave JS DB, and Home Assistant device classes as the shared vocabulary.
- One normalized device schema (VDS) that records each field's level and source.
- A twin generator and runtime.
- Two exposure tiers:
  - a Zigbee2MQTT-compatible MQTT bridge;
  - real Matter nodes.
- A per-level validation harness and a fidelity report.
- 2–6 firmware anchors.
- The stimulus/operate interface for #2 and #3.

**Out of scope:** Habitat placement (#2), residents (#3), physical hardware (never), stock vendor firmware (L5), robots, radio-level effects.

**Success criteria (reported in the paper)**
1. **Coverage.** Count products per level per category.
   - Target: L1 for ≥90% of Zigbee2MQTT and Matter-DCL products.
   - Target: L2 for every Matter device type that has certified products in the sensor, plug, light, lock or thermostat categories.
2. **Platform acceptance.** An unmodified Home Assistant discovers, displays and controls twins from at least 8 categories, with no VESPER code inside Home Assistant.
3. **Conformance.**
   - Matter-tier twins pass the applicable official Matter certification test cases, with the pass rate reported per case.
   - Zigbee-style twins round-trip their source definitions with zero diffs.
4. **Timing fidelity.** Reporting intervals and latencies match the VDS rules and the published measurements, within tolerances fixed before running.
5. **Firmware anchors.** 2–6 anchors run open firmware on emulated SoCs. Under identical inputs, the twin produces the same state sequence and timing within tolerance.
6. **Laptop budget.** Everything runs CPU-only within 2 cores, without Docker. Home Assistant runs as a Python install.

## 2. Architecture

```
 Real sources (pinned, snapshotted with provenance)
 Zigbee2MQTT defs | Matter data model | Matter DCL | Z-Wave JS DB
        │ ingest
        ▼
 catalog: one VDS per product (L0..L4 sections, each tagged with level + source)
        │ generate
        ▼
 twin runtime: many twins per process; logical or wall clock; seeded
   API: operate / stimulate / subscribe   ◄── #2, #3 (Habitat, residents)
        │                                   │
  scale tier: Zigbee2MQTT-compatible       Matter tier: matter.js nodes
  MQTT bridge (Mosquitto)                  (commissioned by HA Matter server)
        └──────────────► unmodified Home Assistant Core (own Python env)
```

| Unit | Responsibility | Interface |
|---|---|---|
| `vesper/catalog/ingest/` | One adapter per source. Snapshot a pinned version; store raw records plus `{source, version/commit, retrieved_at, sha256}`. Zigbee2MQTT definitions are code, so a small Node script (MIT library) exports them to JSON. The other sources are parsed in Python. | `vesper catalog sync` → `catalog/raw/<source>/<version>/` |
| `vesper/catalog/schema` | The VDS JSON Schema, versioned (`vds_version`). | Schema file plus validator |
| `vesper/catalog/normalize` | Raw records → VDS. Deduplicates the same product across sources. Crosswalks features to Home Assistant platforms and device classes, reusing Matterbridge's Zigbee2MQTT → Matter mapping. | `vesper catalog build` → `catalog/vds/*.json` plus coverage summary |
| `vesper/twins/generator` | VDS → twin: per-feature state, command handlers, reporting scheduler, latency and battery behavior, plus category templates for dynamics. | `Twin.from_vds(vds, seed)` |
| `vesper/twins/runtime` | Hosts N twins on one event loop with a pluggable clock (logical or wall). Reuses the deterministic logical clock from the existing rule engine. | `operate`, `stimulate`, `subscribe` (§3) |
| `vesper/exposure/z2m_bridge` | Implements Zigbee2MQTT's MQTT interface: `bridge/devices` (with the real definitions), `bridge/state`, per-device state topics, `/set` and `/get`, and Home Assistant discovery messages. | MQTT, to any broker |
| `vesper/exposure/matter_tier` | Hosts Matter-capable twins as matter.js nodes (Apache-2.0, many per process) and syncs their state with the twin runtime. The official C++ SDK host apps are used for conformance and anchors only. | Matter over IP |
| `vesper/validation/` | Per-level checks (§4) and the fidelity report. | `vesper validate`, `vesper report` |
| `vesper/anchors/` | Renode and QEMU scripts plus the differential-test harness. | `vesper anchors run <id>` |

**Processes**
- Twin runtime and bridge: Python ≥3.11.
- matter.js: Node ≥20.
- Home Assistant Core: its own Python 3.13 environment.
- Mosquitto: native install.
- (Later) Habitat: the existing Python 3.9 conda environment.

Processes talk over MQTT or IP, so version requirements never collide and real platforms connect without code changes.

## 3. The VESPER Device Spec (VDS) and twin behavior

### 3.1 VDS (illustrative; values come from the sources)

```yaml
vds_version: 1
identity:   {vendor, model, category, ids: {zigbee_model: [], matter_vid_pid: null, zwave: null}, protocols: [zigbee]}
features:   # Zigbee2MQTT exposes vocabulary
  - {name: contact, type: binary, access: [state], ha: binary_sensor/door}
  - {name: battery, type: numeric, range: [0,100], unit: "%", access: [state], ha: sensor/battery}
binding:    {zigbee: {endpoints: [{id: 1, in_clusters: [], out_clusters: []}]}}
behavior:
  reporting: {contact: {min_s: 0, max_s: 3600, change: 0}}
  power: {source: battery, sleepy: true, checkin_s: null}
  latency: {local: <distribution ref>}
firmware:   null
fidelity:
  reached: {L0: true, L1: true, L2: z2m-api, L3: partial, L4: false}
  provenance: {"features.contact": "zigbee-herdsman-converters@<commit>:<path>"}
  assumed: ["behavior.latency.local"]
```

### 3.2 Fidelity rules

- A level counts as reached only if every required field at that level comes from a source.
- Defaults are allowed but are listed in `assumed`. Coverage counts exclude anything assumed.
- The L2 value names how faithful the twin is:
  - `z2m-api`: faithful to the Zigbee2MQTT/Home Assistant interface, not to the radio;
  - `matter`: a real Matter stack;
  - `zwave-mock` and `lan-fixture` are reserved for later.

### 3.3 Twin runtime behavior

| Aspect | Behavior |
|---|---|
| State | One typed, range-checked value per feature. A set on a read-only feature, or an out-of-range value, is rejected as the platform would reject it. |
| Reporting | On change, gated by `min_s` and `change`; periodic at `max_s`. Sleepy devices batch reports and check in on their interval. |
| Latency | Physical event → report → platform. Sampled per path (local radio, bridge, cloud) from the referenced distributions. |
| Physical inputs | `stimulate(twin, stimulus)` with stimulus types `presence(room)`, `contact(opened\|closed)`, `button_press`, `load(watts)`, `ambient(temp, lux, humidity)`. Category templates map stimuli to feature changes, e.g. occupancy auto-clears after the product's timeout. |
| Human use | `operate(twin, feature, value, actor)`, e.g. a resident pressing a switch. |
| Platform use | Commands arriving through the exposure tiers. Logged separately from human use. |
| Determinism | A seeded random generator per twin. Logical-clock runs reproduce exactly; wall-clock mode is for real platforms. |
| Output events | Two timestamps: when it physically happened, and when the platform observed it. |
| Not modeled | Radio effects (signal strength, mesh routing, interference) and undocumented vendor quirks. Both are stated as limitations. |

## 4. Validation (software only) and firmware anchors

| Level | Check | Pass criterion |
|---|---|---|
| L0/L1 | Round trip: the bridge republishes each twin's definition, diffed against the source record | 0 diffs for every product counted at L1 |
| L2, scale tier | Start a pinned Home Assistant Core, Mosquitto and the bridge. Through Home Assistant's own API, check the expected entities (platform, device class, unit) and that commands round-trip | 100% of a stratified sample per category; the full catalog in background batches |
| L2, Matter tier | The Home Assistant Matter server commissions each matter.js node; same entity and command checks; official Matter certification test scripts where buildable for laptops | All sampled types pair and control correctly; conformance pass rate per test case |
| L3 | Emitted intervals and latencies from the broker log, tested against the VDS rules and, where public traffic data exists, real-device measurements | Pre-registered statistical tolerances; failures reported |
| L4 | Differential test: identical stimulus and command scripts on the emulated-firmware device and its twin | Same state sequence; timing within tolerance |

**Anchors** (all emulated, no hardware). They are compared at the attribute and report level through the emulator's serial/RPC harness, not by commissioning over an emulated Thread network.
1. Matter light: the Matter SDK `lighting-app` on an emulated nRF52840 (Renode).
2. Matter door lock: the Matter SDK `lock-app` on an emulated nRF52840 (Renode).
3. Smart plug: an ESPHome or ESP-IDF build on an emulated ESP32 (Espressif QEMU), MQTT over virtual Ethernet (no Wi-Fi emulation exists).

No Zigbee firmware emulator was found, so there is no Zigbee anchor (a stated limitation).

**Fidelity report** (each run): coverage per level and category, every `assumed` field, and every check's result. A researcher can cite exactly how faithful their virtual devices are.

## 5. Packaging, testing, errors

**Packaging (laptop only)**
- `pip install vesper` (Python ≥3.11, CPU only).
- Extras:
  - `[matter]` needs Node ≥20;
  - `[ha]` sets up a pinned Home Assistant Core in its own environment;
  - `[habitat]` is the conda environment with habitat-sim 0.3.3, for #2 and #3.
- Platforms: macOS (Apple Silicon) and Linux. Windows via WSL2. Docker optional, never required.
- CLI: `vesper catalog sync|build`, `vesper twins run <home.yaml>`, `vesper validate`, `vesper report`, `vesper anchors run`.

**Licensing**
- Release the derived VDS catalog. Its sources are MIT or Apache-2.0; check the DCL's reuse terms before release.
- Never redistribute:
  - the Zigbee2MQTT application (GPL-3.0): VESPER only implements its public MQTT interface;
  - firmware binaries: publish URL plus hash only;
  - HSSD/HM3D scenes: users download them after accepting those licenses.

**Testing**
- Test-driven unit tests for schema and normalization; for twin behavior (ranges, read-only rejection, reporting rules, determinism); and for the bridge's MQTT contract.
- Opt-in integration tests: Home Assistant acceptance and the anchors.

**Errors**
- Nothing fails silently:
  - A partially parsed product is counted at a lower level, with the reason.
  - An unknown feature type is recorded as unsupported.
  - Snapshots are pinned and hashed, so catalogs rebuild exactly.
- A misbehaving twin is isolated and cannot stop the others.

## 6. Risks to verify first, in the plan

1. The official Matter certification test scripts and the Home Assistant Matter server controller build on macOS/arm64. Fallback: report conformance through matter.js's own test suite plus HA pairing and control.
2. matter.js runs tens of nodes per process on a laptop. Measure it.
3. Zigbee2MQTT `exposes` that depend on device options or firmware version are evaluated with defaults and recorded as such.
4. Build time for the Renode and QEMU firmware. Cross-compiling is real compute and needs the PI's go under the shared-Mac rules.
5. Habitat itself has been frozen since v0.3.4 (Meta ended official maintenance). VESPER pins 0.3.3. This matters for #2 and #3.

## 7. Repository cleanup (after this spec is approved)

These are the rules:
- "Archive" means copying into `~/Research/vesper-archive-2026-10/` with a SHA-256 manifest. Nothing unique is erased.
- "Remove" means `git rm` on a `cleanup/2026-10` branch; git history keeps it.
- The commit needs the PI's explicit OK.
- The separate `~/Research/vesper-sh` repo is not touched.

**Keep**
- `data/` (Habitat assets, CASAS) and `habitat-lab-official/`
- `vesper/core/event_bus.py`, `vesper/core/registry.py`, `vesper/automation/tap_engine.py`
- `vesper/simulation/{task_generator,task_system,time_manager}.py`
- `vesper/matter/*`
- `vesper/utils/dataset.py`
- `vesper/evaluation/{activity_comparison,download_datasets,metrics,scalability_bench}.py`
- From `scripts/revision/`:
  - `e2_embodied.py`
  - `e_a_ha_{stack,rules,driver}.py`
  - `casas_workloads.py`, `casas_data_quality.py`
  - `e1v2_workloads.py`, `e1v2_llm_schedules.py`
  - `rule_corpus_prevalence.py`
  - `pause_in_windows.py`
- `scripts/extract_scene_info.py`
- From `results/revision/`: `llm_schedules/`, `casas/`, `e2/embodied/`, `rule_corpus/`
- `docs/` and this spec

**Defer to #2/#3** (kept for now; decided in those specs)
- `vesper/habitat/*`
- `vesper/devices/{scene_device_placer,scene_configs}.py`
- `vesper/dashboard/app.py`: a possible base for the "create a home, pick devices" UI
- `vesper/agents/*`
- `vesper/engine.py`, `vesper/platform.py`, `vesper/__main__.py`, `vesper/config.py`

**Remove, superseded by the twins once #1 lands**
- `vesper/devices/{base,contact_sensor,light_sensor,motion_sensor,smart_door,security_camera,manager}.py`
- `vesper/hub/{base,manager,virtual_hub}.py`
- `vesper/integrations/device_registry.py`

**Remove (security, network, hardware or Docker scope)**
- `vesper/attacks/`, `vesper/network/`, `vesper/protocol/`
- `vesper/firmware/sensor_templates.py`
- `vesper/hub/physical_hub.py`
- `vesper/integrations/{smartthings,schema_connector,sync_bridge,docker_device_manager}.py`
- `vesper/evaluation/{security_eval,latency_profiler,llm_ablation,experiment_runner,instrumentation,report_generator}.py`
- `vesper/habitat/wifi_firmware_bridge.py`
- `docker/`, `build/`, `esp32_demo/`, `configs/default.yaml`
- `scripts/attacks/`, `scripts/dataset/`
- Every other top-level script in `scripts/`, i.e. all of the security-campaign, Wi-Fi/ns-3, real-pcap and SmartThings demo scripts, except `extract_scene_info.py`
- Tests that cover only removed modules

**Keep for review in #1's plan:** `vesper/firmware/esp32_runner.py`, the ESP32 QEMU runner. It may seed anchor 3.

**Archive (then remove from the working tree)**
- `INFOCOM-2027-manuscript/`, `IoTJ-manuscript/`
- `results/` except the kept items
- `logs/`, `simulator_screenshots/`, `scratchpad_out/`, `$OUTDIR/`
- `typescript`, `vesper-sh.zip`, `gpt-feedback.md`
- `IoT-Phantom-Delay-Attack-main/` (third-party material)
- `HANDOFF_PROMPT.md`, replaced by a new handoff at the end of the cleanup

The queued security experiments (the last two CASAS homes, H5(b)) stay paused. Whether the security case study in #4 needs them is decided in #4's spec. Their scripts and outputs are archived, so nothing is lost.
