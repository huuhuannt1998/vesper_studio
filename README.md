# VESPER Studio

**A smart-home simulation lab on a laptop: real market devices as virtual twins, living in homes from real 3D
scenes, with an unmodified Home Assistant as the hub.**

![The play page in 3D: the player in the example home's bedroom has just switched on an IKEA lamp; the crosshair's prompt says what a click does next, the console shows the lamp's report, and Alex is at dinner in the kitchen](docs/images/lab-play.png)

VESPER Studio maps 14,234 real products from the Zigbee2MQTT, Matter and Z-Wave catalogs and Tasmota's device templates
to virtual devices, each at the highest fidelity its public definition allows. It places them in a home: a small test
house, or a real 3D scene from the HSSD dataset. Scripted residents live their day there, and Home Assistant sees the
devices exactly as it would see real ones. You can walk the home yourself, as in a game, or let a day run in seconds.
Every session is recorded and replays exactly.

It is research software under active development. The research paper is *VESPER* (in preparation). VESPER Studio
runs as a desktop app for macOS and Windows (unsigned for now; see below) or from a terminal, with its pages in your
browser.

## The app

`vesper studio` opens the lobby: your homes (play one, or compose a new one with real products), what this computer
has and lacks (`vesper doctor`), and buttons that run the setup in the background. The desktop app, **VESPER Studio**,
is a window around the same lobby: it starts `vesper studio` when it opens (inside WSL2 on Windows) and stops it when
it closes. Build it from `studio-app/` with `cargo tauri build`, or take an unsigned build from the repository's
Actions; macOS asks once to allow an unsigned app (Control-click, Open). The app needs VESPER itself installed
(`uv tool install .` from a clone, then `vesper catalog fetch` or `build`).

## What you can do today

| | Command | Needs |
|---|---|---|
| Play a home in the browser, in 3D as in a game: walk, use devices, watch sensors and residents | `vesper lab play` | the catalog |
| Put a real Home Assistant in the loop, with its dashboard in the page | `vesper lab play --hub` | `vesper setup ha`, Mosquitto |
| Let the residents live a day in seconds, with the home's automations | `vesper lab day` | the catalog |
| See the home's network: the router, and each link's signal and latency | `vesper lab network` | the catalog |
| Replay any session and check that it reproduces exactly | `vesper lab replay` | the catalog |
| Reproduce a paper's attack and defense on a home, claim by claim | `vesper lab scenario run` | the catalog; live: `vesper setup ha`, Mosquitto |
| Make a home of any size, with the devices you pick, or a hundred of them | `vesper lab generate` | the catalog |
| Furnish a generated home in Blender, by hand or with Claude | `vesper lab dress` | Blender; optional: Blender MCP |
| Make a home from a real 3D scene (HSSD) | `vesper lab new --scene` | Habitat, HSSD |
| Find a product to place in a home | `vesper catalog search`, `vesper catalog show` | the catalog |
| Turn a persona schedule or an ARAS day into a resident's routine | `vesper lab routine` | — |
| Compose a home in the browser: rooms, real products, their firmware and configuration | `vesper lab compose` | the catalog |
| Check the page automatically in a headless browser | `vesper lab look` | Chromium |
| Have Claude Desktop (or any MCP client) set up a paper's attack and defense, claim by claim | `vesper mcp`, `vesper setup claude-desktop` | the catalog |
| Run the twins on MQTT, and validate them | `vesper twins run`, `vesper validate` | the catalog; some checks `vesper setup ha` |
| Check what is installed and what each missing piece is for | `vesper doctor` | — |
| The lobby: your homes, composing a new one, setup (the desktop app shows it) | `vesper studio` | the catalog |

## Requirements

- **macOS** (tested on Apple silicon) or **Linux**. On Windows, use WSL2 for now.
- **Python 3.11 or newer** and [uv](https://docs.astral.sh/uv/).
- **Node.js 20+ and npm**, to build the device catalog (the Zigbee definitions are JavaScript).
- Optional:
  - [Mosquitto](https://mosquitto.org/) and `vesper setup ha`, to put Home Assistant in the loop;
  - a Chromium-based browser, for `vesper lab look`;
  - Miniconda with habitat-sim 0.3.3 and the HSSD dataset, for homes from real 3D scenes (see below);
  - Habitat's humanoids, so the people in a home are Habitat's own avatars (see below).

## Install

    git clone https://github.com/huuhuannt1998/vesper_studio.git
    cd vesper_studio
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -e ".[dev]"
    source .venv/bin/activate

Then build the device catalog once:

    vesper catalog sync        # downloads the pinned upstream sources (network, a few minutes)
    vesper catalog build       # builds the catalog offline (about a minute, 0.6 GB of memory)

Every command that runs twins refuses a catalog built by older catalog code, so rebuild after an update.

Or install a released catalog instead of building it (the release notes give its SHA-256; the bundle is refused if
it does not match):

    vesper catalog fetch https://github.com/huuhuannt1998/vesper_studio/releases/download/<tag>/vesper-catalog.tar.gz \
        --sha256 <hash>

Then let VESPER check the rest and set up what you need:

    vesper doctor              # every prerequisite: ok, or missing with the commands that need it and the fix
    vesper setup all           # Home Assistant, the Matter host and the Habitat environment, each once
    vesper setup scenes 102344022   # an HSSD scene, exactly its files (after `hf auth login` and HSSD's terms)

`vesper setup habitat` makes a conda (or micromamba) environment with habitat-sim 0.3.3. VESPER remembers where
Habitat and HSSD are in `~/.config/vesper/config.json`; a flag or an environment variable still wins.

## Quickstart: play the example home

    vesper lab play examples/lab/test-house/home.yaml

Your browser opens on http://127.0.0.1:18080/ (add `--no-browser` to only print the address). The page shows the
test house in 3D: a hall, a kitchen, a bedroom and a porch, with six real Zigbee products. Alex, a resident, lives
there on a weekday routine. The test house has no 3D scene of its own, so it is drawn plainly from its map; a home
made from a real scene (below) is drawn from the scene itself.

| Control | What it does |
|---|---|
| **Click** the view | capture the mouse to look around (**Esc** lets it go) |
| **W A S D** or the arrow keys | walk where you look; hold two keys for a diagonal |
| **Shift** | run |
| **Click** or **F** | use what the crosshair is on: switch a plug, open a door, press a button |
| **E** | the same; with nothing under the crosshair, the thing in reach |
| **V** | first person or third person |
| **M** | the full map (a click on it uses a device or door too) |
| **Q** | select the next thing in reach |
| **Tab** | move to the next panel |

As in a game, the prompt under the crosshair says what a click will do ("Click: switch kitchen_plug on"), or why
it cannot ("too far (3.7 m)", "other side of the wall", "a sensor, not used by hand"). A browser without WebGL, or
the address with `?view=map`, plays on the map alone:

![The map page: the same home from above, with Alex at dinner, the event feed, a motion sensor that sees the player, and why the lamp is out of reach](docs/images/lab-map.png)

The page also shows:
- in the header, the room you are in and the direction you face, and a compass at the top of the view;
- bottom right, the minimap;
- top right, the event feed: a sensor seeing someone, a door, a resident's action, an automation;
- the residents, and what each one is doing;
- the console, with every trace record in words.

Stop with Ctrl-C. The session's trace is in `examples/lab/test-house/traces/`.

## Home Assistant in the loop

    vesper setup ha                                        # Home Assistant Core in its own venv, once
    vesper lab play examples/lab/test-house/home.yaml --hub

VESPER starts its own Mosquitto (port 18830) and Home Assistant (port 18123). It puts the home's Zigbee devices on
the broker through a Zigbee2MQTT-compatible bridge, and each Wi-Fi device on a connection of its own, as a Tasmota
device, which Home Assistant's Tasmota integration discovers. It shows Home Assistant's dashboard in the page, and
prints the login and each device's Home Assistant entities.

Matter devices join too with `--matter-interface en0` (or `matter_interface: en0` in the home file), after
`vesper setup matter` (Node.js): VESPER starts its own Matter Server, makes each Matter device a matter.js node named
after it, and has Home Assistant commission it. Matter cannot run over loopback, so the nodes announce themselves by
mDNS on that interface's network. A Matter device's name is its own slug (`front_lock`), so its entities are
`lock.front_lock`, `binary_sensor.back_door_door` and so on, in Home Assistant and in VESPER's own engine alike.
Delaying or dropping a Matter device's traffic is not built yet; scenario runs with Home Assistant use the Zigbee and
Wi-Fi devices.

The example home ships one automation: walking into the hall turns the bedroom lamp on. Edit automations in Home
Assistant's own editor. They are written back to the home's `automations.yaml` when play stops. VESPER never uses
the standard ports 1883 and 8123, so it does not disturb a Home Assistant you already run.

## The home's network

Every home has a network, as a real house does: a router, the hub (Home Assistant and VESPER's broker) wired to
it, and the Zigbee coordinator on the hub, with each Zigbee device on its own hop to the coordinator. Every
device's reports and every command cross their links, so a link's latency, a weak signal or an attack shows up
where it would in a real home. `vesper lab network` shows where the router stands and each link's quality:

    vesper lab network examples/lab/test-house/home.yaml

A link's signal comes from the home itself: the distance and the walls between its two ends, with a shut door
costing a little more. The radio constants are typical published values (ITU-R P.1238's indoor path loss), not
measured on a device, and the output says so. A home can place its router and its coordinator:

    network:
      router: {room: hall}
      coordinator: {room: bedroom, link: wifi}      # its own host, on the router's Wi-Fi

A Wi-Fi device is a virtual Tasmota device: a plug, a switch or a dimmer from Tasmota's device templates, placed as
any other device:

    - {product: "tasmota:athom:athom_ls4p-tas", name: desk_lamp, at: {room: bedroom, xy: [5.5, 5.0], mount: table}}

It has no firmware and no hardware: VESPER speaks Tasmota's MQTT topics for it, with a locally administered MAC
address that is never a real device's. Like a real one, it has its own MQTT connection, over its own link to the
router. Out of the router's range it never connects, and `vesper lab check` names it. A device that goes out of
range is lost: the broker notices only by its keepalive timeout, publishes its will, and Home Assistant marks it
unavailable. Back in range, after a back-off of about 11 s, it connects again.

With `--hub`, Home Assistant's and the bridge's MQTT connections really cross the network: they go through a relay on
127.0.0.1 that only ever forwards to the broker the session started (never ports 1883 or 8123). An attack acts on those
real connections, on whole MQTT messages: it can delay, drop, duplicate, reorder or throttle them, or cut a link,
including a Wi-Fi device's own, where an attack names the device as it does a Zigbee one. A delay can let the keepalives
through, so the connection stays up while a report arrives late (the Phantom-Delay attacks). A cut is either a lost link
or a reset, after which the broker publishes the bridge's will and Home Assistant marks every Zigbee device unavailable.
A device whose own hop is down goes unavailable after Zigbee2MQTT's availability timeout, alone. Without Home Assistant,
on the logical clock, the same effects apply in-process. Every effect is an input of the trace, so a replay applies it
again, and every message an effect touched is in the trace with when it was sent and when it arrived.

## Attacks and defenses from a paper: scenarios

A scenario file (`vesper-scenario/1`) sets up an attack from a research paper and its defenses on a home, says what
the paper claims, and quotes the passages each step and claim rests on. VESPER runs it under four conditions
(baseline; defense, the defenses without the attack, as their control; attack; attack + defense), several times
each, and reports claim by claim whether the paper's result was reproduced:

    vesper lab scenario check examples/lab/scenarios/phantom-delay/case3-action-delay.yaml
    vesper lab scenario run   examples/lab/scenarios/phantom-delay/case3-action-delay.yaml
    vesper lab scenario run   examples/lab/scenarios/phantom-delay/case3-action-delay.yaml --mode live

The examples are the Phantom-Delay attacks (Fu et al., DSN 2022), Table III's Cases 1 and 3:
- **Case 1:** the front door's event is held on the bridge's connection while its keepalives pass, so the
  notification comes a minute late.
- **Case 3:** the door's "closed" event and then the lock command are held, so an intruder walks in before the door
  locks.

The paper's countermeasure, timestamp checking, flags the delayed events but does not stop the late action, as the
paper says. VESPER's own `physical_consistency` check compares the world with what the hub heard and raises its
alert within seconds.

A run on the logical clock uses VESPER's automation engine and takes seconds. With `--mode live`, Home Assistant runs
the automations and the attack acts on the real MQTT connections through the relay.

Every step names a device, link, door or room of the home, never an address or a port. What VESPER cannot reproduce
is written in the file as a gap and listed first in the report. A claim event that matched nothing in any run (a
misnamed value, a device that never acts) is flagged, and a run that could not run is reported as such, the others
judged. `vesper lab scenario report` checks the claims again
on a run's saved traces. Each run's trace replays like any other session.

To watch a scenario play out in the home, give it to `lab play` (or to `lab look`, for screenshots):

    vesper lab play --scenario examples/lab/scenarios/phantom-delay/case3-action-delay.yaml --condition attack
    vesper lab look --scenario examples/lab/scenarios/phantom-delay/case3-action-delay.yaml --steps shots.yaml

The scenario's home opens with its actors in their places; its timeline begins once the page has drawn the home
(the first page to do so, for `play`), on the wall clock. The scenario plays the player, so the page only watches: walking and using are
switched off, looking around still works. The console shows the scenario's start, the attack's effects, what the
network held or dropped, and each defense's alert, in its own colour; the view's feed shows the attack and the
alerts. `--condition` is one of the four (default attack + defense). With `--hub`, Home Assistant runs the
automations and the defenses hear what it hears, as in a live run (the scenario is checked as a live one). Automations
edited in Home Assistant's page while a scenario plays go beside its file, never into it.

### From a paper, with Claude: the MCP server

`vesper mcp` is an MCP server through which Claude Desktop, LM Studio or any MCP client builds a scenario from a
paper. Give the model the paper and the `reproduce_paper` prompt; it reads the threat model, picks a home, quotes the
passages it relies on, and builds the scenario with typed tools (`capabilities`, `homes`, `home_describe`,
`catalog_search`, `scenario_draft`, `scenario_edit`, `cite`, `gap`, `scenario_validate`, `scenario_save`,
`scenario_run`, `run_status`, `run_report`). Every edit is checked against the home before it is kept, so the draft is
always a valid scenario, and an error names the nearest valid choice. What the paper has and VESPER cannot express is
recorded as a gap, never faked. No tool takes a host, a port, a command or code; live runs need `--allow-live`.

    vesper setup claude-desktop --homes ~/vesper-homes      # registers the server (a backup of the config is kept)
    vesper mcp --print-config --homes ~/vesper-homes        # the JSON for any other MCP client

Given only the Phantom-Delay paper's text, Claude built both cases in about ten minutes and reproduced their claims.
`vesper lab scenario score DRAFT --gold GOLD` compares a model's scenario with a hand-written one, element by element.

## A day in seconds, and exact replays

    vesper lab day examples/lab/test-house/home.yaml --hours 24
    vesper lab replay examples/lab/test-house/traces/<the printed trace>.jsonl

`lab day` runs the residents' routines on a logical clock. Without Home Assistant, VESPER's built-in engine runs the
home's `automations.yaml` itself: the same entities, the same triggers and the same commands. It names any
automation it cannot run. `lab replay` re-runs a trace and checks that every device report comes out the same.

## A home of any size: generated homes

`vesper lab generate` makes a home from a number of rooms, 1 to 60 on one floor:

    vesper lab generate homes/eight --rooms 8
    vesper lab play homes/eight/home.yaml

The rooms line both sides of a corridor, each with a 0.9 m door onto it, so every room can be reached from the front
door; a porch is outside. A number of rooms becomes a room list (a living room and a bedroom, then a kitchen, a
bathroom, more bedrooms with a bathroom for every two, an office, a dining room, a laundry), and each room's size is
drawn from its type's usual range. The same request and seed give the same home. Generated layouts are synthetic, and
the home file says so.

Or compose it in the browser:

    vesper lab compose homes/mine

The page sets the rooms (a number, or a list of room types) and the devices, one row per kind of device and where
it goes (every room, every room but some, one room, the front door, every exterior door, or a number spread over
the home). A row takes any real product of its kind, or one picked from the catalog: search by vendor, model or id,
filter by kind and protocol. A product's card shows its network, its configuration (every setting and reading,
settable or read-only, with its values), its firmware and an evidence ladder from L0 to L4. Each level shows only
with its evidence: identity and configuration from the catalog's sources, Home Assistant acceptance from a
validation run, a firmware anchor where the twin was compared with emulated firmware. **Generate home** writes the
request and the home to the folder (an earlier home there goes to `previous/`, its traces stay), and **Play it**
opens it in `vesper lab play`.

To choose the rooms and the devices in a file, write a request (`vesper-request/1`) and pass it with `--request`:

    format: vesper-request/1
    program: [living, kitchen, bedroom, bedroom, bathroom, office]
    seed: 3
    devices:
      - {product: "zigbee:aqara:mccgq11lm", at: front_door}   # a real product, pinned
      - {category: lock, at: front_door}                        # a category: VESPER picks a real product
      - {category: light, per: room}
      - {category: motion_sensor, per: room, except: [bathroom]}
      - {category: plug, count: 4}

A category becomes a real catalog product. Each device goes where its kind usually goes: lights and alarms on the
ceiling, motion sensors in a corner, contact sensors and locks on the door, switches beside the door, plugs along the
walls. A ceiling light gets a plain wall switch beside its room's door, part of the light (`wall_switch: [x, z]` in
its `at:`), so the player and the residents switch it there; a hand-written home can give one to any ceiling device.
Every position is marked as assumed, and can be moved in the home file. Without a request, a home gets a
starter set: a lock and a contact sensor on the front door, a light in every room, motion sensors, a thermostat and a
smoke sensor.

A batch makes many homes and a summary table (rooms, floor area, devices, devices out of range):

    vesper lab generate homes/batch --count 100 --rooms 3-10

In the 3D view every room has simple furniture for what it is, and **I** opens the card of the device under the
crosshair (or in reach): the product, its model basis and sources, its links and their signal, its Home Assistant
entities and the automations that use them, and its state.

### Furnished in Blender

`vesper lab dress` writes two files beside a home: `dress/build_home.py`, a Blender script that builds the home's
floors and walls from its map, and `dress/BRIEF.md`, which says how to furnish it. Run the script in Blender, furnish
and texture the rooms by hand or ask Claude to do it through [Blender MCP](https://github.com/ahujasid/blender-mcp),
then export `scene.glb` beside the home. `vesper lab play` then draws that model in place of the plain walls, and the
map still decides where people can walk and what they can reach. Blender is needed only to make the model, not to play
it.

The same works for a home made from an HSSD scene: `vesper lab dress` converts the scene's models for Blender (their
KTX2 textures become PNG, with the Basis transcoder the 3D view already uses; Node is needed, and the converted files
stay in `~/.cache/vesper`, or `VESPER_CACHE`, never in a repository), and the script imports each model where Habitat
puts it, the furniture grouped by room and the doors kept apart. Move, swap or add furniture, export `scene.glb`, and
`vesper lab play` draws it, with HSSD's own doors.

Furniture in a model is only drawn until the map follows it:

    vesper lab renav homes/eight/home.yaml

has Habitat work out the walkable floor again from `scene.glb`, so people walk round the furniture where it stands;
the rooms stay, and the first map is kept as `map.original.json`. A floor that closes off a doorway or leaves a room
without floor is refused, and the map stays as it was. It needs the Habitat environment, as
`vesper lab new --scene` does. An HSSD scene taken through Blender unedited keeps its walkable floor: 99% of it
(intersection over union) on the one scene tried, every difference within 30 cm of the floor's edge.

## A home from a real 3D scene (HSSD)

Real homes come from [HSSD](https://huggingface.co/datasets/hssd/hssd-hab), a gated dataset (CC BY-NC 4.0). Accept
its license on Hugging Face, log in with `hf auth login`, and download it to `data/scene_datasets/hssd-hab`, or
point `VESPER_HSSD` at your copy. You can download it scene by scene: `vesper lab scenes` says which scenes are
complete, and names the files each one still needs.

VESPER reads the scenes with Habitat, which runs in its own environment:

    conda create -n vesper python=3.9 cmake=3.14.0
    conda install -n vesper habitat-sim=0.3.3 withbullet -c conda-forge -c aihabitat

VESPER looks for that environment's Python at `~/miniconda3/envs/vesper/bin/python`. Set `VESPER_HABITAT_PYTHON`
if yours is elsewhere. Then:

    vesper lab scenes                                          # the scenes here, and which are complete
    vesper lab new ~/vesper-homes/myhome --scene 102344022     # rooms, doorways, doors and the 3D scene, in ~10 s
    vesper catalog search ikea bulb                            # products to place (vesper catalog show <id>)
    vesper lab check ~/vesper-homes/myhome/home.yaml
    vesper lab play ~/vesper-homes/myhome/home.yaml

Keep homes made from HSSD outside this repository: their map files are derived from HSSD, which may not be
redistributed. A new home gets:
- the scene's rooms;
- its walkable floor, with the furniture in it;
- its doors, closed. HSSD's own metadata says which openings are doors and which are windows;
- its 3D scene (`render.json`): every mesh the scene draws, placed exactly where Habitat places it. The page draws
  the scene from HSSD's own files, so `vesper lab play` needs HSSD where the home was made, or `--hssd`.

For the people, download Habitat's humanoids, in the Habitat environment:

    python -m habitat_sim.utils.datasets_download --uids habitat_humanoids --data-path data/

`vesper lab play` then draws each person as the Habitat humanoid their avatar names (`female_0`, `male_1`, …),
walking with Habitat's own walk; it looks for them in `data/humanoids/humanoid_data`, or `VESPER_HUMANOIDS`, and
draws plain figures without them.

`lab new` names a door that opens onto no room of the map. In a scene with storeys, the home is the ground floor,
and `lab new` names the rooms it leaves out.

A home is a plain YAML file (`home.yaml`). It holds:
- the scene and its doors;
- the devices, and where each one is (a room, a wall, a table, a door);
- the player's avatar;
- the residents, with their routines and habits.

`vesper lab check` names every mistake and the nearest valid choice. The full format is in the design document
(`docs/superpowers/specs/2026-10-24-vesper-lab-design.md`).

## Residents and their routines

A resident lives a routine file: tasks with a time, a duration, a category and a room. Its profile holds its
habits:
- how often it forgets a light or the lock;
- when a room is too dark for it;
- its comfort band;
- its bedtime.

Residents walk the home and use the devices their activities need, opening doors on the way. They react to the
house: a dark room, a cold room, an alarm. Every action is recorded with who did it and why.

Routines can be made from earlier schedule data:

    vesper lab routine persona schedule.json --home ~/vesper-homes/myhome/home.yaml        # an LLM persona's day
    vesper lab routine aras DAY_1.txt --resident 1 --home ~/vesper-homes/myhome/home.yaml  # an ARAS day

## Checking the page automatically

    vesper lab look examples/lab/test-house/home.yaml --steps examples/lab/test-house/look-pubg.yaml

`lab look` drives the page in a headless browser, step by step (walk, press, click, aim, view, wait, expect). It
takes screenshots and writes a report. `look-3d.yaml` checks the 3D view: the crosshair, F, first person, the map.

## Development

    .venv/bin/python -m pytest          # about 1,100 tests, two minutes
    .venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper_world \
        tests/lab tests/catalog tests/twins tests/exposure tests/validation tests/world

Opt-in tests need real services:

| Variable | What it enables |
|---|---|
| `VESPER_HABITAT=1` | the world service in Habitat (scene 102344022 downloaded) |
| `VESPER_UI_BROWSER=1` | a real headless browser (the map and the 3D view) |
| `VESPER_STACK_TESTS=1` | a real Mosquitto (also the home's network and its Wi-Fi devices end to end) |
| `VESPER_HA_TESTS=1` | the real Home Assistant of `vesper setup ha` (the network, the Wi-Fi devices and the gold scenarios end to end; about 25 minutes) |
| `VESPER_NETWORK_TESTS=1` | the upstream catalog sources |
| `VESPER_BLENDER=<path to Blender>` | `vesper lab dress`'s script in a real Blender (4.2 or later), headless |

Layout:

| Path | Contents |
|---|---|
| `vesper/catalog/` | the device catalog: sources, normalization, fidelity levels |
| `vesper/twins/` | the twins and their runtime (reporting rules, latency) |
| `vesper/exposure/` | the Zigbee2MQTT-compatible bridge, the virtual Tasmota devices and Home Assistant discovery |
| `vesper/validation/` | validation against Home Assistant |
| `vesper/lab/` | the Lab: homes, the map world, rules, residents, the automation engine, the play server and page (`ui/`), replay |
| `vesper_world/` | the world service, which runs in the Habitat environment (Python 3.9) |
| `vesper/agents/`, `automation/`, `core/`, `simulation/` | VESPER's first version, kept for its earlier experiments; the Lab does not use them |
| `examples/lab/test-house/` | the example home |
| `vesper/lab/generate.py`, `vesper/lab/dress.py` | generated homes, and their Blender build script |
| `examples/lab/scenarios/phantom-delay/` | the gold Phantom-Delay scenarios (Cases 1 and 3), their home and automations |
| `docs/superpowers/specs/` | the design documents |
| `docs/experiments/` | run books and results |

## Roadmap

- **VESPER Studio for macOS and Windows:** signed installers with the release.
- **The home's network, continued:** link effects on Matter devices, and real 802.11 on Linux.
- **The security lab, continued:** more attack vantages (a rogue broker client, a compromised device, a malicious
  automation) and more defenses (broker ACLs, hardened automations, timeouts).
- **Generated homes, continued:** rooms that are not on a corridor, several storeys, and a matching HSSD scene as
  another look.

## License and data

The code is under the MIT license (see `LICENSE`).
- **Device catalog:** built on your machine from pinned upstream sources, under their own licenses:
  - Zigbee2MQTT's converters and Z-Wave JS's configuration (MIT);
  - Tasmota's device templates (blakadder/templates, EPL-2.0); the build writes `NOTICE-tasmota.md` beside the
    catalog, with the source and its license;
  - connectedhomeip's Matter data model and Home Assistant's vocabulary (Apache 2.0);
  - the CSA's Distributed Compliance Ledger, whose terms are not yet verified, so its records are left out of a
    release build.

  `vesper catalog build --release` keeps only records whose sources allow redistribution, and the catalog bundles
  of a release (`vesper catalog bundle`) are made from such a build. See `THIRD_PARTY_NOTICES.md`.
- **3D data:** you download HSSD (CC BY-NC 4.0) and Habitat's humanoids yourself, under their own terms. Nothing
  derived from them is distributed.
