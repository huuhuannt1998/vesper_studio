# Plan W10: compose a home in the browser (rooms, real devices, their firmware and config)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** `vesper lab compose OUT` opens a local page where a researcher sets the rooms, picks real catalog products
(searching, filtering, reading each one's firmware and configuration), generates the home and plays it.

**Why:** the PI's 2026-10-31 asks: "Users device selection list (firmware and config)" and "I want to allow users to
put in the number of room, pick the device. Then we can build the virtual enviroment". Generation exists
(`vesper lab generate`, plan W9, a `vesper-request/1` file); this is its page. Approved with "Go with your
recommendation" and "Go" (2026-10-31).

**Architecture:** a small HTTP server in `vesper/lab/compose.py` (stdlib `http.server`, threaded, 127.0.0.1 only),
JSON endpoints over the catalog, the validation evidence and `generate_home`; a page `vesper/lab/ui/compose.html`
with `compose.js` (no build step, like the play page). Generating writes `OUT/request.yaml` and the home beside it,
then the page offers Play, which starts `vesper lab play OUT/home.yaml` as a child process on its own port.

**Spec:** home-generator spec (docs/superpowers/specs/2026-10-31-vesper-home-generator-design.md) §1 (the request),
§4 (the device card); device-twins spec §4 (fidelity levels L0-L4).

## Global constraints
- Loopback only (127.0.0.1); never ports 1883/8123; no network beyond the page.
- Honest fidelity: a level shows only with its evidence. L0/L1 from the catalog; L2 from the latest acceptance runs
  (`vesper report`'s credit); L4 only for products with a firmware anchor (an `anchors` results file). A product
  without evidence says "no firmware anchor: a protocol-level twin built from <sources>".
- Z-Wave records are identity only (no twin): shown, not selectable.
- Nothing derived from HSSD; the page draws no 3D.

## Design (my calls, recorded)
- Rooms: a count (1-60) or an explicit list of room types (generate's types), a seed.
- Device rows map 1:1 to a request's device entries: what (a category, or a pinned product), where (every room;
  every room except types; a room type; the front door; exterior doors; N spread), and how many.
- Search: words over id/vendor/model/description/category, filters category and protocol; sorted by evidence level,
  then id; 50 per page.
- Product card: identity; protocol and its network (Zigbee mesh to the coordinator / Wi-Fi + MQTT (Tasmota) / Matter
  over Wi-Fi (on Home Assistant with --matter-interface) / Z-Wave: not simulated); configuration = the features, each
  settable or read-only, with type, range or values, unit; levels with reasons and sources; firmware anchor.
- Generate: the server validates by writing the request and loading it with `load_request`; errors come back as text.
- Play: one child `vesper lab play` at a time; a second Play stops the first.

## Tasks
1. Evidence: `vesper/lab/compose.py` `evidence(results_dir)` -> {product: {"L2": source, "L4": anchor}} from
   `report.build_report`'s L2 credit and `results/device-twins/anchors-*.json`; `levels(record, evidence)`.
   Tests: a fake results dir with an acceptance and an anchors file.
2. Search and card: `search_products(catalog, evidence, q, category, protocol, limit)`, `product_card(record,
   evidence)`. Tests on the test catalog.
3. Generate: `compose_home(request_dict, catalog, out)` -> {home, summary, problems} via `load_request` /
   `generate_home` / `summary_row`; GenerateError text returned. Tests.
4. Server + CLI: `ComposeServer` endpoints (`/`, `/api/meta`, `/api/search`, `/api/product`, `/api/generate`,
   `/api/play`), `vesper lab compose OUT [--catalog] [--port 18081] [--no-browser]`. Tests with a real server on
   port 0 and a fake play launcher.
5. Page: `compose.html`, `compose.js` (rooms, rows, search panel, card, generate, play), the request builder as a
   pure function in `compose.js` exported for node tests. Live check in a headless browser (a look-like script).
6. README, plan as built, review, merge.

## Review focus
1. A request the page builds that `load_request` refuses (empty devices, unknown category): the error is shown.
2. Search with no words lists the best-evidenced products of the filter, not nothing.
3. A Z-Wave product cannot be added; a Matter one says it reaches Home Assistant only with --matter-interface.
4. Generating into a folder that already holds a home: the old home is replaced, its traces kept.
5. Play when a previous play still runs: stopped first; the child dies with the compose server.

## As built (2026-10-31)
All six tasks; suite 1198 passed, UI 47. Live (headless Chromium, the real catalog): 50 products listed; the Yale
Assure Matter Module's card shows its lock-app firmware anchor (L4, 5/5 transitions) and its 5 settings and
readings; a row holds the chosen product; generate wrote 6 rooms, 74.4 m², 15 devices; Play started
`vesper lab play` in 0.8 s, a second Play replaced it, nothing left running. Evidence: L2 from `vesper report`'s
credit (95 Zigbee, 18 Matter products), L4 from `results/device-twins/anchors-20261031T200000Z.json` (3 anchors).

Final review (fresh reviewer): 4 Important fixed with tests: attributes (aria-label) set as attributes; one
generate or play at a time (a lock; buttons disabled while pending); Play refuses a port another program holds and
fails as soon as the child dies (and a SIGTERM stops compose and its child, checked live); concurrent generates run
one after the other. Minors fixed: localhost and [::1] accepted, a foreign Host refused (DNS rebinding), a negative
or oversized Content-Length refused, errors while writing the home answered, evidence loading tolerant, L4 only when
every transition agrees (all anchors files read), no zombie child, a failed generate leaves the play running, a
picked product keeps its row's category. Deferred minors: in "a number of rooms" mode the "one room type" list
offers types the layout may lack (generate's error names it); a POST with no Origin is allowed (browsers send one).
