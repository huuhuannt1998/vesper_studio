# Third-party notices

VESPER Studio's own code is under the MIT license (`LICENSE`). It downloads, builds on, or runs the following; none
of it is redistributed in this repository unless stated.

## Data VESPER downloads to build its catalog (`vesper catalog sync`)

| Source | Where from | License | In a released catalog bundle |
|---|---|---|---|
| zigbee-herdsman-converters (Zigbee2MQTT's device definitions) | npm / GitHub Koenkk/zigbee-herdsman-converters | MIT | yes |
| Z-Wave JS device configuration | GitHub zwave-js/node-zwave-js | MIT | yes |
| Matter data model (ZAP XML) | GitHub project-chip/connectedhomeip | Apache-2.0 | yes |
| Home Assistant's entity vocabulary | GitHub home-assistant/core | Apache-2.0 | yes |
| Tasmota device templates | GitHub blakadder/templates | EPL-2.0 (the build writes `NOTICE-tasmota.md`) | yes, with its notice |
| CSA Distributed Compliance Ledger (certified Matter products) | the DCL's public REST API | terms not yet verified | **no**: left out of a `--release` build |

## Software VESPER installs or runs on your machine

| Software | Used for | License |
|---|---|---|
| Home Assistant Core (`vesper setup ha`) | the hub under test, unmodified | Apache-2.0 |
| Mosquitto (installed by you) | the MQTT broker | EPL-2.0 / EDL-1.0 |
| matter.js and the Matter Server (`vesper setup matter`) | Matter nodes, and the server Home Assistant uses | Apache-2.0 |
| habitat-sim (`vesper setup habitat`) | rooms, doorways and the navigation mesh of HSSD scenes | MIT |
| three.js (vendored, `vesper/lab/ui/vendor/three/`) | the 3D view | MIT |
| Basis Universal transcoder, ktx-parse, zstddec (vendored with three.js's examples) | HSSD textures in the 3D view and for Blender | Apache-2.0 / MIT |
| MCP Python SDK | `vesper mcp` | MIT |

## Data you download yourself, under its own terms

| Data | License | Note |
|---|---|---|
| HSSD (Habitat Synthetic Scenes Dataset), `vesper setup scenes` | CC BY-NC 4.0, gated on Hugging Face | never redistributed with VESPER, nor anything derived from it |
| Habitat's humanoids | CC BY-NC-SA | the same |

## Firmware anchors (evaluation only)

The paper's firmware anchors build connectedhomeip's `lighting-app` and `lock-app` (Apache-2.0) with ESP-IDF
(Apache-2.0) and an ESPHome plug (ESPHome: MIT/GPL-3.0 parts), and run them on Espressif's QEMU (GPL-2.0). None of
them is part of this repository.
