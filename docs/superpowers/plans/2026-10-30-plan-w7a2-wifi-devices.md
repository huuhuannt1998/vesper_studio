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

## Final review (Opus, 2026-10-30) and its fix pass

No Critical findings. Five Important, and two Minors re-graded Important by what a user gets (ghost entities, doubled
telemetry), each fixed test-first (RED, then GREEN; suite 974 passed, 29 skipped; live 13 passed):

- **Joining blocked the session's clock.** A first connection through a cut link stalled the clock for 10 s, and the
  device never joined. Now the client connects on its own thread (`mqtt.connect_async`), the device is on the hub at
  once, and every connection announces it, as Tasmota does, which also brings it back after a broker restart.
  Tests: `test_joining_never_waits_for_the_connection_and_the_device_announces_once_it_is_up`,
  `test_every_connection_announces_the_device_again_as_tasmota_does`,
  `test_a_connection_that_comes_up_after_stop_does_not_say_the_device_is_online_again`.
- **A command's result was published twice** when reports came through a link. The device now skips its own
  platform-caused reports. Test: `test_a_command_s_result_is_published_once_though_the_reports_come_later_through_a_link`.
- **Predicted entity ids could differ from Home Assistant's** (non-ASCII names) or clash silently. A Wi-Fi device's
  name must now be its own slug, and no two predicted entity ids may clash; both are refused at load. Test:
  `test_a_wi_fi_device_s_name_is_the_entity_id_home_assistant_gives_it_and_no_other_s`.
- **An older Home Assistant venv failed silently.** `ensure_tasmota` now waits for the entry to load, and names the
  cure (run `vesper setup ha` again) when it does not, or when the integration cannot start. Test:
  `test_a_tasmota_integration_that_cannot_run_is_named_with_its_cure`.
- **A lost station's messages left no trace** on the live network. The relay now drops them before queuing and
  records `dropped`, reason `out of range`, with device, feature and direction. This also ends the tasks that waited
  forever on an infinite latency. Tests: `test_a_lost_station_passes_nothing_…`,
  `test_home_assistant_s_command_to_a_lost_station_is_dropped_and_named`.
- **Ghost Tasmota entities after switching homes.** A home without Wi-Fi devices now removes the integration's
  entries (`HomeAssistant.forget`). Test: the hub play test's events.
- **Telemetry once per feature.** A dimmer sent two `tele/STATE` per period and a strip four. Now there is one
  per device, on the device's own TelePeriod (300 s). Test:
  `test_the_whole_state_is_published_once_a_tele_period_however_many_features_the_device_has`.

**Deferred minors:**
- A start-up race: a reach change between the session's start and the hub's first pass is missed.
- A cut that ends while the station is lost records `up, cut ended`.
- `tele/STATE` reports a constant RSSI, not the link's.
- `mqtt.connect` leaves a half-open connection on a timeout.
- The validation report does not count Tasmota records.
- Tasmota topics use 24 bits of the MAC; two devices could collide, with no check at load.
