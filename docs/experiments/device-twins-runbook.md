# Run book: device-twin experiments (scale and Matter tiers)

These experiments produce the paper's success criteria 2 and 3: for Zigbee-style twins (scale tier) and for Matter twins hosted as real matter.js nodes (Matter tier; spec §1, §4). All are software-only and laptop-sized, and need no Docker:

| Experiment | Level | Command | Pass criterion (spec §4) |
|---|---|---|---|
| Discovery pre-screen | L2 (offline) | `vesper validate discovery` | Home Assistant's own schemas accept every generated discovery component |
| Round trip | L0/L1 | `vesper validate roundtrip` | every placed twin published exactly once (`complete`), with 0 definition diffs |
| Platform acceptance | L2, scale tier | `vesper validate acceptance` | see "Pass" under Experiment 2; at least 8 categories in `categories_passing` |
| Reporting rules | L3 (offline) | `vesper validate rules` | 0 violations of R1–R4 (`docs/experiments/l3-timing-design.md`) |
| Latency | L3 (live Mosquitto, default QoS) | `vesper validate latency` | G and the residual checks P, as pre-registered |
| Real devices (E2) | L3 (offline, real captures) | `vesper validate realdata` | D, A and X, as pre-registered (amendment 2) |
| Matter acceptance | L2, Matter tier (LAN mDNS) | `vesper validate matter` | every sampled device type pairs, shows and obeys (Experiment 6) |

`vesper report` then writes the fidelity report from the catalog and the latest result of each check.

Commit `docs/experiments/l3-timing-design.md` before the first L3 run: it is the pre-registration.

Every resource figure below is an estimate until the first run measures it. The first run records the actual time and peak memory in the ledger.

## Before any run

1. Build the catalog with the current code: `vesper catalog build` (offline; it reads `catalog/raw/` and writes `catalog/vds/`). If `catalog/raw/` is missing, run `vesper catalog sync` (network) first.
   - Rebuild after every change under `vesper/catalog/`. Commands that run twins refuse a catalog built by other catalog code (exit code 2, "stale"), because it would feed them old rules.
2. Mosquitto is installed natively (macOS: `brew install mosquitto`; Debian/Ubuntu: `apt install mosquitto`). VESPER starts its own instance; it does not use a running one.
3. Home Assistant is set up once, for acceptance only:

       .venv/bin/vesper setup ha

   This needs `uv` and network access to PyPI. It creates `.vesper/ha/venv` (~1 GB) with Python 3.14, Home Assistant Core 2026.10.2 and the requirements of the `frontend`, `mqtt`, `hassio` and `matter` integrations. Home Assistant later runs with `--skip-pip`, so it never installs anything itself.
4. For the Matter tier only, set up the Matter host once:

       .venv/bin/vesper setup matter

   This needs Node.js 22.13 or newer, npm and network access to the npm registry. It copies `vesper/exposure/matter_host/` to `.vesper/matter/host/` and runs `npm ci --ignore-scripts` there: @matter/main and @matter/nodejs 0.17.9 and matter-server 1.4.0, exactly as `package-lock.json` pins them (198 packages, ~330 MB). No package's install script runs.

### On the shared lab Mac only

- Read `/Users/huanbui/Research/MAC_COORDINATION.md` and `~/Research/BUSY_WINDOWS.json`. Pick a time outside every window.
- No Home Assistant process before 2026-10-24T02:30Z (another project's experiment).
- The PI has said go for this run, in this session.
- Add a ledger row (start, session, command, cores, memory, Docker: no) and message the peer sessions.
- Run every command under `taskpolicy -b`, and stop if memory pressure (`sysctl -n kern.memorystatus_vm_pressure_level`) stays at 2 or more.
- VESPER uses only 127.0.0.1:18830 (Mosquitto) and 127.0.0.1:18123 (Home Assistant), plus, for the Matter tier, 127.0.0.1:15580 (its Matter Server) and UDP 25540 and up (its nodes). Never pass `--mqtt-port 1883` or `--ha-port 8123`: those belong to another project on this Mac.
- A Matter run also announces its nodes by mDNS on the lab network (Experiment 6): book that slot with the other projects explicitly.

## Experiment 0: discovery pre-screen (offline, no server)

    taskpolicy -b .venv/bin/vesper validate discovery

This builds the discovery message of every L1 Zigbee product and validates it with Home Assistant 2026.10.2's own code, inside `.vesper/ha/venv`. It runs the steps of `mqtt/discovery.py`, then each platform's `DISCOVERY_SCHEMA`, which includes the sensor unit and device-class rules.
- No Home Assistant instance is started and no port is opened. The importing alone takes ~1–2 min at background priority.
- Output: `results/device-twins/discovery-<UTC>.json`, holding the accepted and total component counts, rejected components with Home Assistant's message, and errors grouped by message.
- Products whose components are rejected here would fail acceptance for that reason. Report them as findings about the catalog crosswalk; this check does not hide them.

## Experiment 1: round trip (L0/L1)

    taskpolicy -b .venv/bin/vesper validate roundtrip

- Starts Mosquitto on 18830 and loads the pinned zigbee-herdsman-converters snapshot.
- Creates one twin per definition and publishes them all through the bridge.
- Reads the retained `zigbee2mqtt/bridge/devices` back with a separate client, and diffs each definition against the source.
- Retracts every retained message, waits until the broker has them, and stops the broker.

Budget (estimate): minutes, 1 core. Memory is unmeasured: the retained `bridge/devices` message alone is ~19 MB, so expect up to ~1 GB.

Output: `results/device-twins/roundtrip-<UTC>.json`, holding:
- `checked`, `identical`;
- `different` (record id → differing paths);
- `unmatched`, `missing`, `duplicates`, `complete`;
- `l1_products`, `l1_with_diffs`, `zhc_version`.

Pass: `complete` is true and `different` is empty (exit code 0). The paper reports `identical/checked` and `l1_with_diffs` (must be `[]`).

The oracle is VESPER's rebuild of the Zigbee2MQTT definition object from the same snapshot. Phrase it as "the source definition survives normalization and the bridge unchanged", not as byte equality with Zigbee2MQTT's own output: Zigbee2MQTT adds fields such as `icon`.

## Experiment 2: platform acceptance (L2, scale tier)

    taskpolicy -b .venv/bin/vesper validate acceptance --per-category 5 --seed 0

What it does:
1. Starts Mosquitto (18830) and Home Assistant (18123).
2. On the first run only, onboards Home Assistant headless; the refresh token is saved in `.vesper/ha/auth.json`, mode 600.
3. Removes every existing MQTT integration entry, with the devices and entities it had discovered, and adds a fresh one for 127.0.0.1:18830 through the config-flow API. So no entity from an earlier run survives as a "restored" ghost.
4. Samples L1 Zigbee products, five per category, seeded.
5. For each product, waits for its entities in Home Assistant's REST API.
   - The first entity waits up to `--timeout`, then the rest are read at once: one discovery message creates them all.
   - If the first 3 products show no entity at all, the run stops with an error instead of waiting for hours.
6. Checks each entity:
   - its device class and unit (after Home Assistant's own unit rewrites, e.g. µ → μ);
   - that it is live (not `unavailable`, not restored);
   - that it shows the value the twin last reported.
7. Runs one probe per entity. A command's target always differs from the current value, so a command that never arrives fails.
   - a service call that must reach the twin and come back as state (switch, light, lock, number, select);
   - or a physical stimulus that must show up in Home Assistant (contact, presence, ambient, load, button events).
8. Announces the twins offline, retracts them (in a `finally`, so also after an error, Ctrl-C or SIGTERM), waits until the broker has every message, and stops both services.

Options:
- `--keep` skips the retraction, so Home Assistant's registry keeps the twins' devices until the next run. Home Assistant itself is stopped at the end either way.
- `--category <name>` (repeatable) restricts the sample.

Budget (estimate): ~95 products at `--per-category 5` (19 categories), well under 1 h if Home Assistant creates the entities, 1–2 cores, ~1 GB.
- Reports from sensors with a minimum reporting interval (for example temperature, 10 s) arrive after that interval, exactly as on the real product.
- Allow `--timeout` of at least 20 s.

Output: `results/device-twins/acceptance-<UTC>.json`, holding:
- `summary`: `products`, `passed`, `categories_passing`, `by_category`;
- per product: each entity's `created`, `attributes_ok` and `probe` (with Home Assistant's error message if it refused a call), plus the `skipped` features and why;
- `rejections`, `faulted`, `notes`, `ha_version` and `seed`.

Pass: `passed == products` and `len(categories_passing) >= 8` (exit code 0 means every product passed).

`categories_passing` counts a category only when every product in it:
- passed all of its checks;
- was exercised by at least one command or stimulus;
- had its core function exposed.

Thermostat (climate), cover and fan are not exposed yet, so they never count. `categories_displayed` is the weaker count, in which every product merely passed its display checks. The paper reports both, the per-category numbers, and every skipped entity type.

## Experiment 3: reporting rules (L3, offline, no services)

    taskpolicy -b .venv/bin/vesper validate rules --hours 4 --seed 0

This runs every L1 Zigbee product with reporting rules on the logical clock, under a seeded script of stimuli and platform commands. Every report is checked against R1–R4 as it is emitted.
- It is CPU only and starts nothing. Simulated time runs far faster than real time.
- Budget (estimate): minutes for the full catalog, 1 core, <1 GB.
- Output: `results/device-twins/rules-<UTC>.json`, holding:
  - the products, reporting features and presence features checked;
  - the report count and the occupancy clears checked;
  - rules dropped for features the twin lacks;
  - stimulus rejections;
  - the violations (first 200, plus the total), and any faulted twins;
  - the run's provenance.
- Pass: `passed` is true: at least one feature checked, zero violations, nothing faulted.

## Experiment 4: latency (L3, live Mosquitto)

    .venv/bin/vesper validate latency --seed 0

**Scheduling requirement.** Run this one at the default QoS, without `taskpolicy -b`, as the only job on the machine.
- At background QoS macOS coalesces timers by 80–200 ms, which would measure the host, not VESPER (pre-registration, "Procedures").
- It is light: 1 core, <1 GB, ~10 min.
- On the shared lab Mac it still needs the PI's explicit approval for the slot.

What it does:
1. **G (offline):** checks that each path's generator reproduces its published distribution (100,000 draws).
2. **Calibration:** starts VESPER's own Mosquitto on 18830, using topics under `vesper-latency/`. Then it injects a constant 50 ms into 200 contact-sensor twins, through the same timer path as every model, to measure VESPER's own overhead.
3. **P (each path):** for `local`, `vendor_cloud` and `trigger_action_cloud`, 200 fresh twins with their own seed. Each sample records the injected and the observed latency.
   - The residual, observed − injected, must stay within median ≤ 20 ms and P95 ≤ 50 ms.
   - The end-to-end latency against the published distribution is reported descriptively.

Output: `results/device-twins/latency-<UTC>.json`, holding:
- the generator checks, the calibration and the overhead;
- per path: the residual check, the descriptive comparison, and every sample (injected and observed);
- the pre-registered constants, and the run's provenance (parameters, git commit, dirty state, catalog versions).

A run that fails, for example on a timeout, still writes its result with the error.

Pass: G, the calibration's residual check and every path's residual check all pass (exit code 0).

## Experiment 5: real devices (E2, L3, offline)

Fetch the dataset once (network, ~95 MB), at the pre-registered commit:

    git clone https://github.com/antonio-boiano/ZIOTP2026 .vesper/datasets/ZIOTP2026
    git -C .vesper/datasets/ZIOTP2026 checkout c3481e8073f40bdbc1bee4a24e05a0a13a9545ba

It needs tshark from Wireshark (macOS: `brew install wireshark`; tested with 4.6.3). Then:

    nice -n 19 .venv/bin/vesper validate realdata

What it does (pre-registration, section "E2"):
1. Decrypts every Zigbee capture of both topologies with the network keys the dataset publishes, verifying each frame's MIC.
2. Turns every standard ZCL attribute report into a (device, endpoint, cluster, attribute) stream, drops relayed copies and retries, and cuts out intervals where a frame may have been lost (an APS-counter gap, a rejoin, an undecodable report).
3. For each pair of consecutive real reports, installs a twin in the first report's state in VESPER's own runtime, under the rule ZHA configured, and compares its next report with the device's.
4. Repeats the prediction under VESPER's catalog rule, as a description only.

- CPU only: seconds to a minute, 1 core, <1 GB. It starts no service and uses no network.
- Output: `results/device-twins/realdata-<UTC>.json`, holding:
  - per file: its SHA-256, its network PAN, the decryption rate, the key byte order, and what was set aside (other networks' and bad-FCS frames, undecodable and manufacturer-specific reports);
  - per stream: the rule, the interval classes, the agreement on informative intervals and the descriptive statistics;
  - the capture dates, the dataset commit and the run's provenance.
- Pass (exit code 0):
  - every file decrypts at least 99% on its own network;
  - at least one stream is judged;
  - in every judged stream, the twin's prediction matches the device's next report in at least 95% of the informative intervals.
- A run on another dataset commit, or on a checkout with local changes, is exploratory.

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

**Network.** Matter cannot use loopback: matter.js skips internal interfaces. So the nodes announce themselves by mDNS on the lab network, visible to other machines, to other projects' Home Assistant instances and to Apple and Google home hubs, which may offer to add them. Each run draws its nodes' setup codes and discriminators at random and never prints them, so another controller would have to guess one of about 2.2 million setup codes while the nodes are commissionable (the commissioning phase, about 15–30 min). On the shared lab Mac, every Matter run needs a slot agreed with the other projects in the ledger. The nodes are gone when the run ends.

Budget (estimate): 18 nodes in one Node process, ~0.5 GB; commissioning takes seconds to a minute per node, so ~15–30 min in all; 1–2 cores.

Output: `results/device-twins/matter-<UTC>.json`, holding:
- `summary`: `products`, `passed`, `device_types`, `device_types_passing` (every product passed and was exercised), `device_types_displayed`, `categories_passing`, `by_device_type`, `by_category`;
- per product: its node id or commissioning error, its serial, the features the host could not expose (`unresolved`), each expected entity's `created`, `available` and `probe`, and the Home Assistant entities no expectation used (`extra_entities`);
- `matter_packages`, `ha_version`, `vid_mode`, `interface`, `seed`, `per_type`, `run_dir` (the server's and the host's logs) and `notes`.

Pass: `passed == products > 0` (exit code 0). The paper reports `device_types_passing` and `device_types_displayed`: leak, rain, smoke and air-quality sensors have no stimulus yet, so they can only be displayed.

L2 (matter-server) is credited only to products whose node passed. All products of a device type share its certified mandatory features, but a product that was not sampled is not credited.

## The fidelity report

    .venv/bin/vesper report

This writes `results/device-twins/fidelity-<UTC>.md` and `.json`. They contain:
- source versions;
- coverage per level;
- the most common `assumed` fields;
- each check's latest result: a check that never ran is shown as "not run yet", never as passed. A run over nothing does not pass. A run whose parameters differ from the pre-registration, or that records none, is marked exploratory;
- the timing paths with their sources.

L2 is credited only to products that passed acceptance: Zigbee products through the bridge (Experiment 2), Matter products through the Matter Server (Experiment 6).

## Running twins for your own Home Assistant

A home file lists real products by catalog id and gives each a Zigbee2MQTT friendly name. Point `broker` at your own running broker: VESPER's port 18830 exists only while a `validate` command runs. On the shared lab Mac, never use 1883.

Friendly names may contain `/` (rooms), but no `+`, `#`, empty levels, a level named `set`, `get` or `event`, a leading `bridge`, or a trailing `availability`.

    broker: {host: 127.0.0.1, port: 1884}
    seed: 0
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: "hall/door"}
      - {product: "zigbee:aqara:mccgq11lm", name: "kitchen/door"}

Then:

    .venv/bin/vesper twins run home.yaml

This runs until Ctrl-C or SIGTERM. The bridge registers an MQTT Last Will, so a crash still leaves `bridge/state` offline. Other processes drive the twins over MQTT:
- `vesper/control/stimulate` with `{"device": "hall/door", "stimulus": {"type": "contact", "state": "opened"}}`;
- `vesper/control/operate` with `{"device": ..., "feature": ..., "value": ...}`.

## When something goes wrong

| Message | Cause | Fix |
|---|---|---|
| `port 18830 is already in use` / `port 18123 is already in use` | an earlier run is still up, or another program uses the port | `pgrep -fl "mosquitto -c .*\.vesper"` / `pgrep -fl "hass -c .*\.vesper"`; stop only VESPER's own processes |
| `Home Assistant is already onboarded but .../auth.json is missing` | an earlier run onboarded Home Assistant but never saved its token | with Home Assistant stopped, delete `.vesper/ha/config/.storage` (VESPER's own instance only) and run again |
| `Home Assistant created no entity for the first 3 products` | the MQTT integration is not receiving VESPER's broker, or the discovery messages are rejected | read `.vesper/ha/hass.log`; run `vesper validate discovery` |
| `Home Assistant entered recovery mode (it would bind port 8123)` | an integration failed to load, usually a missing requirement; VESPER stops it at once | rerun `vesper setup ha`; read `.vesper/ha/hass.log` |
| a product fails with `created: false` | Home Assistant rejected its discovery message | the reason is in `.vesper/ha/hass.log`; this is a real finding: report it |
| `the Matter host is not installed` / `the Matter Server is not installed` | `vesper setup matter` has not run | run it (Node.js 22.13+, npm) |
| `the Matter Server commissioned none of the first 3 nodes` | mDNS does not reach the nodes on `--interface`, or the server refuses their certificates | check the interface (`ifconfig`); read `matter-server.log` and `matter-host.log` in the run directory; try `--vid-mode test` |
| a Matter product fails with `commission_error` | the server could not commission that node | the reason is in the run directory's `matter-server.log`; report it |
| `Home Assistant's environment lacks the Matter integration's packages` | `.vesper/ha/venv` was set up before the Matter tier | run `vesper setup ha` again; it now installs matter-python-client |
| `Home Assistant showed no entity for the first 3 commissioned nodes` | the Matter integration entry is missing or broken | read `.vesper/ha/hass.log`; check the Matter entry in Home Assistant |

## Afterwards

Close the ledger row with the actual end time. Keep the JSON reports: they are the evidence the paper cites. Record the run (command, commit, report path, summary numbers) as an RKA note.
