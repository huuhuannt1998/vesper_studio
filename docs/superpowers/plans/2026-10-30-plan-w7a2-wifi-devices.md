# W7a2: Wi-Fi Devices (Virtual Tasmota) Implementation Plan, as built

> Written after a test-driven build in a scratch tree, not before it: each task below names its files, the
> interfaces it produces and the tests that drove it. The code is commit 06c7f8e on `w7a2-wifi-devices`.

**Goal:** the home's Wi-Fi devices: virtual Tasmota plugs, switches and dimmers, each with its own MQTT connection
over its own link to the router, which Home Assistant's Tasmota integration discovers and commands, and an attack
on the home's network finds by name.

**Architecture:** the catalog gains Tasmota's device templates. A `TasmotaDevice` speaks Tasmota's topics for one
twin. The network gives each Wi-Fi device a `wifi/<name>` link; the link layer models its reach and rejoin back-off;
on the live network the relay carries the link and models a lost station. A `TasmotaHub` connects each device, one
MQTT client each, as `vesper lab play --hub` and `look --hub` run.

**Tech stack:** Python 3.11+, paho-mqtt, Mosquitto, Home Assistant 2026.10.2 with HATasmota 0.10.2 (MIT).

**Spec:** `docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md` (§3: the home network, §3.3 the Wi-Fi
devices, §3.4 the relay).

## Global Constraints

- VESPER's own ports only (Mosquitto 18830, Home Assistant 18123); never 1883 or 8123; the relay binds 127.0.0.1.
- The devices are virtual: no firmware, no hardware; a locally administered MAC derived from the twin id.
- Tasmota's templates are EPL-2.0: the build writes `NOTICE-tasmota.md` beside the catalog.
- Radio constants are assumed typical values, and the output says so.
- Every effect is a trace input; a replay needs no hub.

## Review Focus

1. A template with several relays and a PWM channel: only the light is announced; its other relays are not.
2. Two device names that slug to the same entity id (`kettle plug`, `kettle_plug`): Home Assistant appends `_2`;
   VESPER's predicted ids do not.
3. Home Assistant or the broker restarting while the devices are connected: the retained discovery and LWT, and
   paho's reconnect, must bring every device back.
4. A station that rejoins while an attack's cut holds its link: the cut must still hold.
5. A command Home Assistant sends while the station is lost never arrives (clean MQTT session); its state in Home
   Assistant is right again only once the device reports.

---

### Task 1: Tasmota's device templates in the catalog

**Files:** create `vesper/catalog/ingest/tasmota.py`, `vesper/catalog/normalize/tasmota.py`,
`tests/catalog/test_tasmota.py`; modify `vesper/catalog/sources.py`, `vesper/catalog/vds.schema.json`,
`vesper/catalog/build.py`, `vesper/cli.py` (sync order), `vesper/twins/home.py`, `tests/catalog/{conftest,fixtures,
test_build,test_cli}.py`, `tests/twins/test_home.py`.

**Produces:** `sync_tasmota(raw_root, client, version=None)`; `tasmota_records(tarball, version) -> (records,
rejected)`; `tasmota_notice(tarball, version)`. A record: id `tasmota:<vendor>:<stem>`, features `POWER` (or
`POWER1..n`) and, for a single PWM channel, `Dimmer` (0–100 %); binding `tasmota: {template, relays, pwm, file, type,
link}`.

**Tests:** the 9 tests of `tests/catalog/test_tasmota.py` (the template9 numbering, relays and PWM, the rejections by
name, the notice); the build's counts and files.

**Result on the real templates (commit 30d9c9b):** 552 records (296 plugs, 231 switches, 25 dimmers); the catalog has
14,234 records.

### Task 2: the virtual Tasmota device

**Files:** create `vesper/exposure/tasmota.py`, `tests/exposure/test_tasmota.py`; modify
`tests/exposure/fake_mqtt.py` (plain-text payloads).

**Produces:** `TasmotaDevice(runtime, twin_id, friendly_name, client, events=None, send=None)` with `start()`,
`stop()`, `retract()`, `will(topic)`, `identify(parts, payload) -> (name, features)`; `mac_of(twin_id)`,
`topic_of(mac)`, `expected_entities(record, friendly_name)`.

**Tests:** discovery as HATasmota reads it; POWER, POWER1 on a one-relay device, a strip's POWER1..4, Dimmer,
Backlog (`NoDelay;…`, all or nothing), STATE and STATUS 10/11, TelePeriod, the will, refused commands.

### Task 3: Home Assistant's Tasmota integration

**Files:** modify `vesper/validation/stack.py` (`ensure_tasmota`, the needed integrations), `tests/validation/
test_stack.py`.

**Produces:** `HomeAssistant.ensure_tasmota(prefix="tasmota/discovery")`. `vesper setup ha` installs the
requirements of `tasmota`, `camera` and `stream`.

### Task 4: Wi-Fi links, reach and rejoin

**Files:** modify `vesper/lab/network.py`, `vesper/lab/linklayer.py`, `vesper/lab/rules.py`; tests in
`tests/lab/{homes,test_network,test_linklayer,test_rules}.py`.

**Produces:** a `wifi/<name>` link per Tasmota device (medium wifi, TCP, to the router), in `Network.path`;
`out_of_range(..., medium="wifi")`; `linklayer.WIFI_REJOIN_S = 11.0`: back in range, a station is down
("rejoining") until the back-off ends, then up ("rejoined"); `toggle_command` and `is_on` for POWER/POWER1.

### Task 5: the relay finds Tasmota devices and loses stations

**Files:** modify `vesper/lab/relay.py`, `tests/lab/test_relay.py`.

**Produces:** `Relay.add_device(key, identify)`; `Relay.lose(link)` (nothing passes, no connection opens) and
`Relay.rejoin(link)` (the old connection is aborted; a new one passes). Neither records: the link layer does.

### Task 6: the Tasmota hub, the engine and the CLI

**Files:** create `vesper/lab/tasmota_hub.py`, `tests/lab/test_tasmota_hub.py`; modify `vesper/lab/hub.py`
(`skipped` names only Matter devices), `vesper/lab/automations.py`, `vesper/exposure/mqtt.py` (`keepalive`,
`retry_s`), `vesper/cli.py`; tests in `tests/lab/{test_automations,test_cli_play,test_cli_lab}.py`.

**Produces:** `TasmotaHub(session, connect, port, disconnect)` with `start()`, `stop()`, `devices`, `ports`,
`entities(name)`; `KEEPALIVE_S = 30`, `RETRY_S = 10` (Tasmota's defaults). The engine sends POWER, or a Dimmer scaled
as Home Assistant's `scale_brightness` (1..100) with POWER ON. `play --hub` and `look --hub` set up the integration,
connect each device through its relay link and print its entities; `lab check` names a Wi-Fi device out of reach.

### Task 7: end to end

**Files:** create `tests/lab/test_wifi_live.py`.

**Results (2026-10-30):** against a real Mosquitto, a lost station goes Offline by its will and comes back Online on
a new connection. Against the real Home Assistant: the entities are `switch.kettle_plug` and `light.reading_lamp`,
as predicted; `switch.turn_on` and `light.turn_on` with `brightness_pct: 50` reach the twins (POWER1, a Backlog of
Dimmer 50); a delay on `wifi/kettle_plug` holds Home Assistant's command by the device's name while the kettle stays
available; a lamp out of reach goes unavailable and comes back. The first run found the integration failing to set
up (its camera platform imports `stream`, which needs numpy): fixed in Task 3.

### Task 8: documentation

**Files:** `README.md` (the catalog, the hub, a Wi-Fi device in the network section, tests, licensing), the spec's
progress paragraph, this plan.

## Rulings

- Only Tasmota's current template numbering (template9) is read; 1,639 templates in the old numbering are rejected
  by name. Cost if wrong: fewer products.
- Colour lights (173 templates), sensors and shutters are rejected by name for now. Cost: no RGB Wi-Fi bulbs yet.
- A template has no vendor field: the vendor is its title's first word, so a few ids read oddly
  (`tasmota:16a:awp16l`). Cost: search by vendor misses them.
- Fade and Speed are taken and not modelled: a light changes at once. Cost: a transition-timing attack is not
  reproduced on Wi-Fi dimmers.
- Out of range is the link's own state, not an effect: on the live network the relay delivers nothing and opens no
  connection; on rejoin the old connection is aborted, as Tasmota reconnects MQTT after its Wi-Fi returns. Cost if
  wrong: a broker's takeover behaviour differs from a real device's.
- The rejoin back-off is the 11 s the old VESPER measured (spec §3), an assumed value.
- Without Home Assistant, the engine's `light.turn_on` with a brightness sets Dimmer and POWER, as the device would:
  no device runs in a logical session.
- `vesper setup ha` installs the camera and stream integrations' requirements (numpy, PyAV, PyTurboJPEG) because the
  Tasmota integration loads its camera platform. Cost: a larger Home Assistant venv.
- The example home is unchanged (no Wi-Fi device), so its references and screenshots stay valid; the README shows
  how to place one.
- A strip's relays 2..n are predicted as `switch.<name>_switch_<n>` from HATasmota's naming; verified live for one
  relay and a dimmer only.
- The plan is as-built (process): the build was test-driven in a scratch tree without per-task snapshots.
