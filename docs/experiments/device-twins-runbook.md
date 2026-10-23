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
