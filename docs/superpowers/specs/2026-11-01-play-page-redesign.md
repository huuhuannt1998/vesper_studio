# The play page, redesigned (PI 2026-11-01: "Now")

The PI compared VESPER's play page with the Room 334 walkthrough (sites.google.com/charlotte.edu/room334): "The
design is very clear and the interaction is very detailed". We ran Room 334 and compared (session notes): it shows
real-looking equipment, a named tour, a click-to-inspect dialog with role, interfaces, connections, model basis and
manufacturer source, an inspect list of every item, a dimensioned floor plan, on-screen arrows, and a live control
panel per testbed with a diagram and an event log. VESPER has more under the hood (a real hub, real protocols,
attacks, evidence levels) and shows less of it. This redesign closes that gap. The PUBG controls stay.

## Subject, audience, job

A live, instrumented house as a test rig, for smart-home security and IoT researchers. The page's job: see the house
and its real products, see what each device is and what it is doing now, and watch an attack and a defense unfold.

## Design tokens

Light chrome around the 3D view, as Room 334's; the colour carries information, not decoration.

| Token | Value | Use |
|---|---|---|
| paper | `#F6F7F5` | page background (cool porcelain, not cream) |
| panel | `#FFFFFF` | panels and dialogs |
| ink | `#18232B` | text |
| muted | `#5F6B72` | secondary text |
| line | `#DDE2E0` | rules and borders |
| accent | `#1F6F78` | selection, primary buttons, focus |
| zigbee / matter / wifi | `#C27A14` / `#6A4FC4` / `#2F7FD1` | a device's protocol, wherever it appears |
| alert / held / ok | `#C23B2E` / `#B7791F` / `#2E8A57` | attack and defense alerts, held messages, healthy links |

Type: the system UI sans, tabular numerals for times and values; four sizes (12, 13, 15, 20 px) and two weights
(400, 600). Sentence case everywhere; no all-caps eyebrows.

## Layout

```
┌ VESPER Lab  hssd/102344022        living room · facing E        06:00:43   ● connected ┐
│ [Living room] [Kitchen] [Hallway] [Bedroom] … [Floor plan]  │ Devices (10)  [inspect ▾]  │
│                                                             │ ─────────────────────────  │
│                          3D view                            │ Residents                  │
│                    ┌────────────────┐                       │ ─────────────────────────  │
│                    │ living_light   │  (label by crosshair) │ Home Assistant             │
│ ┌ Phantom-Delay, attack ──────────┐ └────────────────┘     │ ─────────────────────────  │
│ │ timeline · claims · event log   │               ↑  [map] │ Console                    │
│ └─────────────────────────────────┘             ← ↓ →      │                            │
└ Drag or click to look · WASD walk · click or F use · I details · M plan ─────────────────┘
```

## What changes

1. **Devices are hardware, not dots.** Each category is a small procedural three.js model at its real size and
   mount (ceiling light, motion puck, contact pair, deadbolt, plug, thermostat, climate sensor, smoke detector,
   switch, button, leak sensor; a generic box otherwise), facing into its room. State shows as real hardware shows
   it: a light glows and lights the room, a sensor's LED flashes on detection, a lock's bolt and status light
   change, a plug's ring lights, a display shows its reading. Procedural, so no asset files and no licences.
2. **The device dialog** (I, or a device in the inspect list): what it is (vendor, model, category, protocol, room),
   its live state and the action a person can take, how it reaches the hub (each link: medium, signal, latency, up),
   what Home Assistant makes of it (entities, automations), and its evidence (L0 to L4, each with its reason and
   source, assumed fields named). Same data as the old card, laid out as Room 334's.
3. **Getting around.** A tour bar of the home's rooms (a click walks the player there), an inspect list of every
   device by room (a click turns the view to it and opens its dialog), the full map restyled as a floor plan with
   room names and device symbols (M), on-screen arrows for a trackpad, and a reset view.
4. **The scenario panel.** While a scenario plays: its title and condition, the timeline of its steps against the
   clock (the attack's holds, the doors, the lock, the actors), each claim as it is decided, and the alerts, with the
   console's records in plain words.

## Not changing

The protocol between page and server, the controls (WASD, click or F to use, V, M, Q, Tab), the map engine, replay,
and `vesper lab look`. The page stays vanilla JS with the vendored three.js 0.170, no build step.
