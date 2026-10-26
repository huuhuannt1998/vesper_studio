# VESPER Lab: a game-like virtual smart home for testing attacks and defenses — design

- **Date:** 2026-10-24
- **Status:** approved by the PI 2026-10-24 ("go. you decide what is the best for the framework/app", RKA `jrn_01M5QSGDYGE158FRVPVWKN0QNZ`).
- **PI direction (RKA):**
  - `jrn_01M5QPEA85YP8ZJ3Q9DTZK5676`: "a framework/application that users can pick the scene and devices and use a virtual actor to interact with devices and hub like in real environments. The 3D base environment is based on Habitat … Like a pokemon game but this is with smart home and virtual devices … a virtual environment for researchers where they can test out attacks and defenses instead of real house";
  - `jrn_01M5QPHFENDDB436RHXRF8Y4QY`: attack/defense testing is the platform's purpose, shown in the paper's demo;
  - `jrn_01M5QPMDZRHQ7A8XJ7WJ829V5K`: the top-down map and the 3D view, both from the start;
  - `jrn_01M5QRQ0N43J612QBEMEGN2YPH`: the six requirements below, and "Keep 'No robots'".
- **Name (PI, 2026-10-26):** the app is **VESPER Studio**; the Lab is its play mode. Its packaging is §7.
- **Builds on:** `2026-10-22-vesper-device-twins-design.md` (sub-project #1: the catalog, the twins, the Zigbee bridge, the Matter tier, an unmodified Home Assistant). This spec covers the rest of the original plan: home composition (#2, by hand), the residents (#3: the player, and scripted residents with routines and reactions), and the security use case (#4). It also replaces the packaging bullets of that spec's §5.

**The PI's requirements (2026-10-24) and where they are met**

| # | Requirement | Where |
|---|---|---|
| 1 | Behavior of residents, interacting with devices | §5 (the rules any actor triggers), §5a (residents: the player, routines and reactions) |
| 2 | How a real product's configuration gets into the virtual home | §4 (a placed device is a catalog product) and sub-project #1 (the catalog: VDS records built from Zigbee2MQTT, the Matter DCL and data model, Z-Wave JS) |
| 3 | Scale in real life and in the virtual home | §5b (space, time and size) |
| 4 | Device logic and behavior | sub-project #1 (each twin's states, ranges, commands, reporting rules, latency and battery, validated at L1–L3), §5 (how devices react to the world and the world to them) |
| 5 | Habitat | §3: Habitat is the 3D base (HSSD homes, navmesh, physics, articulated doors, humanoid avatars). No robots and no robot training (PI, 2026-10-24) |
| 6 | The hub | Home Assistant Core 2026.10.2, unmodified: VESPER's own instance by default, or the user's own (§2, §3) |

## 0. Intent and success

**The experience.** A researcher installs VESPER on a laptop.
1. They pick a 3D home from Habitat's HSSD scenes.
2. They pick real market products from the catalog and place them in its rooms.
3. They play: they walk an avatar through the home, Pokémon-style but in a real home model. Walking into a room trips the motion sensor, opening a door trips its contact sensor, pressing a switch turns the lamp on.
4. An unmodified Home Assistant is the hub. It sees real-looking devices and runs real automations, and the house reacts in both views.
5. They run an attack against that house, switch a defense on, and see and record what changed.
6. They do all of this instead of using a real house.

**Success criteria**
1. A new user goes from installation to playing in a home with at least 8 placed real products using the 5 quickstart commands (§7) plus the placement editor, with no source build.
2. In the running home, every placed device behaves as its twin (sub-project #1's fidelity levels), is controlled by an unmodified Home Assistant, and reacts to the avatar through the rules of §5.
3. The 3D view and the map show the same world state, updated at least 10 times per second on the reference laptop (Apple M-series, 16 GB).
4. Every session is recorded as a trace: the avatar's inputs, world events, device reports, Home Assistant state changes, attack steps and defense alerts. Replaying its inputs reproduces the same device and Home Assistant event sequence on the logical clock.
5. At least 3 attacks and 2 defenses ship, each with a test that shows its effect in a scripted session.
6. A scripted resident lives a full day in a home from its routine and reactions, using the devices; the day replays exactly from its seed (§5a).
7. Laptop only (device-twins spec §1 criterion 6). No robots. No extra hardware. Docker never required.

## 1. Relation to the paper (PI decision)

The IoT-J paper keeps its single claim: real products mapped to faithful virtual twins on a laptop (device-twins spec §8). VESPER Lab is the platform the paper's demonstration runs on. The demonstration becomes:
- one HSSD home with at least 8 real products from at least 4 categories;
- a recorded player session, replayed for the paper, so that it is reproducible;
- a scripted resident living one day from its routine (§5a), as the original demo planned with the walking resident;
- Home Assistant running at least 3 automations;
- one attack and one defense, end to end, with their trace.

Security results are not a contribution of this paper. They belong to later papers built on the platform.

## 2. What the user does (commands)

    vesper lab new myhome --scene 102344022      # a home from an HSSD scene (list: vesper lab scenes)
    vesper lab play myhome                       # opens the browser: map + 3D view + hub + console

- `vesper lab scenes` lists the HSSD scenes that are installed, with room count, area and a top-down thumbnail.
- `vesper lab new <dir> --scene <id>` creates a home directory holding `home.yaml` (§4) and the scene's map file.
- In `vesper lab play`, the browser UI has an **edit mode**. It searches the catalog (`vesper catalog search` underneath), and a click on the map places a product in a room, on a wall, on a door or on a table. Edits are saved to `home.yaml`, which is plain YAML, so it can also be edited and versioned by hand.
- **Play mode:**
  - WASD or arrow keys walk the avatar; E interacts with the nearest door, switch, lamp or plug; Tab cycles through the panels.
  - **Controls and views like a shooter game (PI, 2026-10-26):** "when users control the character, we can use wasd to move and mouse click to interact with devices (like cs2 game)", then "I want the framework/app works like PUBG game. We can have first and third view". So:
    - **The 3D view is the main screen** (plan W1). It is third-person by default, with the camera behind and above the avatar, and V toggles first-person, as in PUBG. Mouse look runs with the pointer locked: in third person the mouse turns the camera and the avatar's heading, in first person it is the eyes.
    - **Controls:** WASD moves relative to where the camera looks. A crosshair marks the screen centre, and a left click interacts with the device or door under it. Reach and the wall still apply: within 1 m and on the same side, otherwise the page says why. F (PUBG's interact key) and E are keyboard alternatives. Esc releases the pointer. The full mapping to PUBG is in §2a.
    - **The 2D map becomes a minimap** in a corner, following the avatar and showing devices, doors, residents and the reach ring. M opens the full map, as in PUBG. On the minimap and the full map, a click on a device or door interacts with it under the same reach rule.
    - **Home Assistant's dashboard, the console and the security console** stay as panels beside or over the view (Tab cycles through them).
    - **No click-to-walk** in play, which matches both games: movement is WASD only. `vesper lab look` gets a scripted `walk` step (to a point, along the planned path) in place of `click_world` walking.
    - **Before W1 is ready:** the map-only page keeps today's layout with WASD and click-to-interact, so the Lab stays usable.
  - Home Assistant's own dashboard (VESPER's instance) is embedded beside the views, so automations are created and inspected in Home Assistant's real UI. VESPER's instance is configured with `http: use_x_frame_options: false`, so the page on 127.0.0.1:18080 may frame it; nothing else is relaxed.
- **The security console** lists attacks and defenses (§6), runs and stops them, and shows alerts. Attacks and defenses can also be scripted.
- `vesper lab replay <trace>` replays a recorded session's inputs, headless or with the views.
- `vesper lab run <script.py>` runs a scripted session through the Python API, with no player: the same actions (walk to, interact) for automated experiments and later for AI residents.

## 2a. Design reference: PUBG (PI, 2026-10-26)

The PI: "I want the framework/app works like PUBG game. We can have first and third view" and "You can use PUBG game as the core design that we want to follow". So PUBG is the core reference for how the Lab looks, feels and flows. When a UI or interaction question comes up, do what PUBG does, unless the research needs something else; then the plan says why.

| PUBG | VESPER Lab |
|---|---|
| Lobby, choosing a map | Start screen: choose a home (HSSD scene, devices, residents), or `vesper lab new` |
| Match settings | Session settings: clock (wall or logical), residents, Home Assistant on or off, defenses, attacks |
| Third-person view, V for first-person | The 3D view, third-person by default; V toggles first-person |
| WASD, mouse look, F to interact | WASD; pointer-lock mouse look; **left click** interacts with what is under the crosshair (the PI's choice); F and E as keyboard alternatives |
| Shift to sprint | Shift walks faster (a running resident, which sensors see as quicker motion); the default stays 1.1 m/s |
| Interaction prompt by the crosshair ("[F] Open door") | "Click: open front_door", "Click: switch kitchen_plug on", or why not ("too far", "other side of the wall", "locked") |
| Minimap; M for the full map | The 2D map as a minimap that follows the avatar; M opens the full map with devices, doors, residents and reach |
| Compass bar | Compass at the top, with the room the avatar is in |
| Kill feed, top right | Event feed, top right: device reports, the hub's commands, rule firings, resident decisions, attack and defense alerts (the console, shortened) |
| Tab: inventory | Tab: panels (Home Assistant's dashboard, the full console, the security console) |
| Team list | Residents list: who, where, doing what (task and reason) |
| Spectating teammates | Follow a resident's camera (third or first person) |
| Replays: timeline, free camera, follow anyone | `vesper lab replay` with the views: timeline scrubbing, free camera, follow any actor |
| Settings: keys, graphics, FPS counter | Key bindings, resolution and frame rate (the world service's limits), an FPS and latency readout |

Not taken from PUBG: combat, the shrinking zone, loot, health. The Lab's "danger" is the security lab (§6): attacks and defenses with their alerts.

## 3. Architecture

```
 Browser (127.0.0.1:18080): map canvas | 3D view (streamed frames) | HA dashboard (iframe, :18123) | console
        ▲ WebSocket: world state, frames, device states, alerts      │ inputs, edits, console commands
        │                                                            ▼
 VESPER app  (Python ≥3.11, `vesper lab play`)
   game rules (§5) · twins runtime + Zigbee bridge + Matter tier · security lab (§6) · recorder
   stack: VESPER's Mosquitto :18830, Home Assistant :18123 (and Matter Server :15580 when Matter devices are placed)
        ▲ JSON lines over stdin/stdout (the Matter host's pattern)   │ avatar commands, door commands, render requests
        │ world events, avatar pose, door states, JPEG frames        ▼
 World service  (`vesper_world`, Habitat env: Python 3.9, habitat-sim + habitat-lab 0.3.3)
   HSSD scene (articulated) · navmesh · room regions · doors · humanoid avatar · offscreen third-person camera
```

- **One authority.** The world service owns the scene, the walkable floor (navmesh), the rooms (HSSD region annotations), the articulated doors, the avatar and its pose. The map and the 3D view are both views of the same state.
- **Two environments, as the device-twins spec requires.** Habitat needs Python 3.9 and VESPER needs at least 3.11. The app starts the world service as a child process from the Habitat environment and talks to it over JSON lines, as it already does with the Matter host.
  - The world service never touches the twins, the hub or the network.
  - The app never imports Habitat.
- **Device events stay on MQTT.** The twins, the bridge and Home Assistant keep the interfaces sub-project #1 tested. Device events are therefore observable on VESPER's broker as on a real Zigbee2MQTT home, which is what attacks and defenses act on (§6).
- **The map file.** It is generated once per scene by the world service and cached in the home directory. It holds the navmesh projected to the floor, the room polygons with names, the doors (id, polygon, hinge), and furniture footprints. The map view and the placement editor need only this file. It is derived from HSSD, so it stays on the user's machine and is never distributed (device-twins spec §5).
- **Rendering.** The world service renders a third-person RGB camera behind the avatar, offscreen, at 640×480. Frames are JPEG-encoded and streamed at the rate the browser takes, at least 10 per second (success criterion 3). Without a working renderer, the app runs with the map only and says so (risk R1).

## 4. The home file

`home.yaml` (version 2; version 1, today's `vesper twins run` file, still loads):

```yaml
version: 2
scene: hssd/102344022            # HSSD scene id; the articulated variant when it exists
seed: 0
avatar: {model: female_0, start: {room: entryway}}   # the player
residents:                                            # scripted residents (§5a)
  - {name: alex, avatar: male_1, profile: default, routine: routines/alex.yaml}
devices:
  - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: door_front}}
  - {product: "zigbee:philips:9290012607", name: living_motion, at: {room: living_room, mount: ceiling}}
  - {product: "matter:100b:0018", name: living_lamp, at: {room: living_room, xy: [3.2, -1.4], mount: table}}
automations: automations.yaml    # Home Assistant automations, loaded into VESPER's instance
security: {defenses: [mqtt_acl]} # defenses switched on at start
broker: {host: 127.0.0.1, port: 18830}
matter_interface: en0            # only when Matter devices are placed
```

- A placement names a room, a door or a point (`xy`, metres in the scene's floor frame) and a mount: `ceiling`, `wall`, `table`, `floor` or `door`.
- The placement editor writes the same fields.
- Loading checks every product against the catalog, every room and door against the map file, and every point against the room's polygon. An error names the field and the nearest valid choice.

## 5. Game rules: how the world and the devices couple

Deterministic rules, in the app, tested on the logical clock with a fake world service:

| World → devices | Rule |
|---|---|
| Presence | A motion or occupancy sensor covers its room (ceiling mount), or a 7 m, 110° cone from a wall mount, with line of sight on the navmesh. The avatar entering coverage applies the twin's `presence` stimulus; the twin's own timeout clears it (sub-project #1, rule R4). |
| Doors | E within 1 m of a door toggles it in the world. A contact sensor on that door gets `contact: opened/closed`. A locked lock on that door makes the door refuse to open, and the player sees "locked". |
| Switches, lamps, plugs | E within 1 m operates the device's toggle, as its physical button does. The twin reports, and Home Assistant sees the change. |
| Ambient | Each room's temperature, humidity and illuminance follow a simple time-of-day model with the scene's windows. Values change in steps and are applied as `ambient` stimuli, so reporting thresholds act as on real sensors. |

| Devices → world | Effect |
|---|---|
| A light's on/off and brightness | Its light source in the 3D scene and its glow on the map |
| A lock's state | Whether its door can open |
| A plug or switch | Its icon; for a switch bound to a light in `home.yaml`, that light |
| Any device | Its icon's state and a tooltip with its last report |

Every rule firing, with its world time, goes to the trace.

## 5a. Residents: who lives in the home and how they use the devices

A home has one or more residents. Each is a humanoid avatar in the world service, and each acts only through the same actions: `walk_to(room or point)`, `interact(device or door)`, `wait(seconds)`. The rules of §5 treat every resident alike, so a sensor cannot tell a player from a scripted resident.

| Resident | Who decides its actions |
|---|---|
| The player | A person, live, with the keyboard (§2) |
| A scripted resident | A **routine** and **reactions** (below) |
| A program | `vesper lab run` scripts, through the Python API. Later: AI residents (#3 work), which use the same actions |

**Routines.** A routine is a day's schedule of activities with times and rooms: sleep in the bedroom, cook in the kitchen, work in the office, leave home.
- Sources:
  - a routine written in `home.yaml`;
  - a schedule from the earlier VESPER work: the 90 persona schedules and the ARAS-derived day plans in `results/revision/`, converted by the existing code (`scripts/revision/e2_embodied.py`, `e1v2_workloads.py`).
- Executing an activity, the resident walks to the activity's room along the navmesh at 1.1 m/s, as `e2_embodied.py` does, and uses the devices that activity needs. The **activity → device** table is part of the resident's profile, for example:
  - cooking uses the kitchen plug or light;
  - entering a dark room turns its light on;
  - leaving a room turns its light off, with the profile's probability of forgetting;
  - going to bed turns the lights off and locks the front door;
  - leaving home locks the front door.

**Reactions.** A resident reacts to the house, not only to the clock:
- a room too dark for the activity: turns the light on;
- a door locked when leaving or coming in: unlocks it;
- a temperature outside the profile's comfort band: changes the thermostat;
- an alarm (smoke, or an automation's siren): leaves the home.

Reactions read the devices' states as the resident would perceive them in the room (a light is on or off; the thermostat's display), never Home Assistant's internal state.

**Profiles.** A profile is the activity → device table, the habits (the probabilities of forgetting a light or a lock) and the comfort band. It lives in `home.yaml`, with defaults, and randomness comes from the home's seed, so a scripted day replays exactly.

**Ground truth.** Every resident action is recorded with who did it and why (routine step, reaction, player input). Defense D2 (§6) and every attack's success predicate judge against this record: "the door opened, and resident 2 opened it at 07:42".

## 5b. Scale: real life and the virtual home

**Space: 1:1.**
- HSSD homes are metric (1 unit = 1 m).
- The avatars are adult humanoids, 1.6–1.8 m tall, walking at 1.1 m/s.
- Devices are placed in metres.
- A sensor's coverage uses the product's published range where the catalog has it. Otherwise it uses a category default: 7 m and 110° for a wall-mounted motion sensor, the room for a ceiling mount. A default is marked `assumed` in the record, as every unsourced value is in sub-project #1.

**Time: real time when Home Assistant is in the loop, faster without it.**
- Play, and every session with Home Assistant, runs on the wall clock (1:1), because Home Assistant runs in real time. Its automations, timers and history are then exactly as in a real home.
- Scripted sessions without Home Assistant can run on the logical clock (sub-project #1) faster than real time, for example a 24-hour resident day in minutes. Home Assistant's automations are then replaced by VESPER's built-in rule engine, which also runs on the logical clock, and the trace says so.
- Device timing is real-world in both modes: reporting intervals, minimum intervals and latencies follow the product's rules and the published latency distributions (L3).

**Size.**
- A real home has tens of devices. VESPER Lab targets at least 100 placed devices and 4 residents in one home at real time on the reference laptop (R6 measures it).
- Headless scripted runs scale out to several homes in parallel, limited by the laptop's cores.
- The catalog offers 13,682 real products (Zigbee 5,836, Matter 5,487, Z-Wave 2,359), of which those at L1 or above can be placed.

## 6. The security lab (first version)

**Vantage points.** Each attack declares where it acts from. All are local to VESPER's own services on 127.0.0.1:
1. a rogue MQTT client on VESPER's broker (a compromised device or app on the home network, at the application layer);
2. a compromised device: a twin whose reports or replies an attack rewrites;
3. a malicious Home Assistant automation or script, installed into VESPER's instance;
4. a stolen Home Assistant token, used against VESPER's REST API.

**Safety boundary.**
- The lab acts only on the broker, Home Assistant and twins that the running session started. Any target outside them is refused before an attack starts.
- No attack touches the lab network or anyone else's devices. There is no radio, ARP, DNS or traffic interception.
- The Matter nodes' mDNS on the LAN is never attacked.
- This keeps the lab within the PI's standing rule against building attack tooling such as ARP spoofing or interception proxies. **That the PI confirms these application-level attacks inside the sandbox is a condition of this spec.**

**Plugins.**
- An attack is a Python class with a name, a vantage point, parameters, `start(session)`, `stop()` and a ground-truth predicate. For example, "the front door reads open while no one opened it".
- A defense is a class that configures the stack or watches the trace, and raises alerts.
- Both are listed in the console and are usable from scripts.

**Initial catalog**

| Attacks | Defenses |
|---|---|
| A1: spoofed sensor reports. A rogue client publishes a door contact `opened`, or a motion `occupancy: true`, on the device's state topic, triggering automations. | D1: broker authentication and per-client ACLs. Only the bridge may publish device topics, and Home Assistant only commands. |
| A2: command injection. A rogue client publishes `{"state": "ON"}` or a lock command on a device's `/set` topic. | D2: physical-consistency check. Every device report is compared with the world's ground truth (was the avatar near the door; did anyone press the switch), and a report with no cause raises an alert. Only a simulator can offer this defense; it is the lab's distinctive measurement. |
| A3: a malicious automation that reads every entity and unlocks the front door when the home is empty. | — |

Each attack and defense has a scripted session and test: with the defense off the attack succeeds, by its predicate; with the defense on it fails or raises an alert, and the trace shows which.

The legacy security framework (tag `legacy-security-v1`) worked at the network and firmware layers. It is not reused as such. Its MQTT-level ideas inform A1 and A2.

## 7. Packaging and installation: VESPER Studio (PI, 2026-10-26)

- **The product is VESPER Studio, a desktop app for macOS and Windows.**
  - PI, 2026-10-26: "Making this as an app for macOS and window is my definition of packaging. I want this to be a
    professional simulation"; the name: "VESPER Studio".
  - It is a native shell (Tauri) around VESPER's local server and its page, with a guided setup of the
    environments and the scenes.
  - Everything runs on the user's machine. A hosted site (an `.io` game) was weighed and set aside as costly
    (PI, 2026-10-26: "If that's costly, we stick with the original plan"). There is no hosted simulation.
- **Unsigned during development** (PI, 2026-10-26: "Unsigned for now"). macOS asks the user to allow the app once
  (Control-click, Open); Windows SmartScreen asks once. Signing and notarization come with the release.
- **The Python package underneath:**
  - `vesper-studio` on PyPI (`vesper` is taken by an unrelated project);
  - the command and the import name stay `vesper`;
  - the page is static HTML and JavaScript (no build step), shipped in the wheel;
  - researchers who prefer a terminal use it alone: `uv tool install vesper-studio`.
- **The Habitat environment:**
  - `vesper setup habitat` creates a conda or micromamba environment with habitat-sim 0.3.3 (with Bullet, Python
    3.9) and records its Python in VESPER's configuration;
  - the world service runs there as a copy of `vesper_world` alone (plan W1a), so nothing of VESPER's own
    environment can shadow Habitat's packages;
  - habitat-sim has no Windows build, so on Windows the world service runs in WSL2, which the app's setup installs
    and drives. Homes made from a map file need no world service.
- **Assets:**
  - `vesper setup scenes` downloads HSSD scene by scene (Hugging Face, after the user accepts its license), and
    Habitat's humanoid data, into the user's data directory;
  - `vesper lab scenes` names what each scene still lacks;
  - assets and the homes made from them stay on the user's machine and are never redistributed.
- **The rest, unchanged from sub-project #1:**
  - `vesper setup ha` and `vesper setup matter`;
  - `vesper doctor`, which reports each prerequisite and the commands that need it;
  - the released, license-filtered catalog: `vesper catalog fetch`, a SHA-256-pinned bundle;
  - `vesper catalog search|show` (present since W1a).
- **The code is public now** (PI, 2026-10-26: "Push everything now"):
  - https://github.com/huuhuannt1998/vesper_studio, under the MIT license, with a README that shows how to install,
    run and use it;
  - this replaces, for the code, the earlier rule that nothing is pushed before submission
    (`dec_01M5K0PHHR183BMCAJEBG1M0XZ`); the release below still comes at submission.
- **Quickstart** (success criterion 1):
  - in the app: download, open, follow the setup, pick a scene, play;
  - in a terminal:

        uv tool install vesper-studio
        vesper setup                 # habitat, scenes and ha, each skipped when already done
        vesper catalog fetch
        vesper lab new ~/vesper-homes/myhome --scene 102344022
        vesper lab play ~/vesper-homes/myhome/home.yaml

- **Release** (at submission):
  - the wheel and source distribution on PyPI;
  - the app's installers (`.dmg`, `.msi`) and the catalog bundle on a GitHub Release;
  - a Zenodo DOI for code, catalog and the paper's results;
  - `CITATION.cff`, a user guide, and `THIRD_PARTY_NOTICES.md` (what is downloaded, from where, under which
    license).

## 8. Testing

- **Unit tests (TDD), with fakes:**
  - the world-service protocol (a fake world, like the fake Matter host);
  - home-file loading and validation;
  - every game rule, on the logical clock;
  - the recorder and replay determinism;
  - each attack and defense's scripted session, against the fake world and the existing in-memory broker and Home Assistant fakes;
  - the UI server's messages;
  - the map projection (JavaScript `node --test` for the map math).
- **The world service:** tests with a tiny synthetic scene, in the Habitat environment; opt-in, like the Matter host contract test.
- **Live:** opt-in, following the run book. On the shared lab Mac, only after the PI's go and in an agreed slot.

## 9. Risks: verify first, with spikes after another project's run (about 10-27)

| # | Risk | Spike | Fallback |
|---|---|---|---|
| R1 | Offscreen rendering of an HSSD home on macOS arm64 at ≥10 frames/s, 640×480, within ~2 GB | Render a third-person camera in one articulated scene for 60 s; measure fps and memory | Habitat's HITL desktop window for 3D; failing that, map only (the PI is told) |
| R2 | The humanoid walking controller (habitat-lab 0.3.3, motion data present for 8 avatars) in our own loop | Walk female_0 along a navmesh path | A simple avatar marker in 3D; the map is unaffected |
| R3 | Opening and closing HSSD's articulated doors | Toggle a door's joint and check that the navmesh blocks or unblocks | Doors as triggers only, drawn open or closed |
| R4 | The Habitat environment's habitat-lab install points at the old `/Users/huanbui/Desktop/vesper`, which no longer exists | Reinstall it, editable, from `habitat-lab-official/` (a light job) | — |
| R5 | HSSD's license (gated download on Hugging Face) | Record its terms in RKA | Users download it; nothing derived is distributed |
| R6 | Laptop budget: world service, app, Home Assistant and broker together | Measure in the R1 spike | Lower resolution or frame rate; the map only |

## 10. Sub-projects and order

Each has its own plan:
1. **Plan W1, world service:** the protocol, scene loading, the map-file export, the navmesh, rooms and doors, the avatars (several), rendering; with the spikes R1–R4 first.
2. **Plan W2, the game app (a vertical slice first):** walk, trip a sensor, Home Assistant turns a lamp on, and both views show it. Then the placement editor, the full rules, the recorder, replay and `vesper lab run`.
3. **Plan W3, residents:** routines, the activity → device tables, reactions, profiles, several residents, the schedule converters from the earlier work, and the logical-clock mode with VESPER's rule engine (§5b).
4. **Plan W4, the security lab:** the vantage points, the plugin API, A1–A3 and D1–D2, the console.
5. **Plan W5, packaging:** `vesper setup habitat|scenes`, `doctor`, the released catalog, the docs, the release checklist.
6. **Plan W6, VESPER Studio for macOS and Windows (§7):** the desktop shell, its guided setup over W5's commands, WSL2 for the world service on Windows, and unsigned builds of both apps from each commit to main. It starts after W1b (the 3D view) and W5.

What can start now, during another project's run (unit tests only):
- W2's protocol, rules, recorder and UI against a fake world;
- W3's routines, reactions and rule engine against a fake world;
- W4 against fakes;
- W5's catalog bundle and `doctor`.

The spikes, the world service and every live test wait for the end of the run and the PI's go. Plan D (Matter tier) is merged (2026-10-24, 0ec6a89). W1a (a home from a real HSSD scene through the world service) is merged (2026-10-26, 2f07a3c); W1b, the 3D view, is next.

**Out of scope:** robots and robot training (PI, 2026-10-24); radio and network-layer emulation; several players at once; LLM residents (later #3 work, which uses `vesper lab run`); automatic home composition (later #2 work); a hosted service; the world service on native Windows (it runs in WSL2, §7).
