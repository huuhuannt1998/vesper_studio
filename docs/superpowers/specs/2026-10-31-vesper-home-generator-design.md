# VESPER home generator: any number of rooms, the devices you pick (design)

Status: built 2026-10-31 (sub-project W9, plan `docs/superpowers/plans/2026-10-31-plan-w9-home-generator.md`), but for
`--look habitat` (§5). The PI's direction: "I want to allow users to put in the number of room, pick the device. Then we
can build the virtual enviroment. We can use habitat of build our own based on the Blender MCP with Claude", pointing at
the Room 334 walkthrough ("This is what I want but in larger scale. You know my requirements"). The design choices below
are the executor's, under the PI's delegation, and are marked as such.

## 0. Intent

Room 334 (a UNC Charlotte teaching lab, `sites.google.com/charlotte.edu/room334`) is one room as a browser
walkthrough: a three.js scene, WASD to walk, a click on a station opens its panel (interfaces and role, connections,
model basis, the manufacturer's reference). VESPER does the same for whole homes of any size: a researcher says how
many rooms (or which ones) and which devices, and VESPER builds a home they can walk through, click every device of,
put Home Assistant, residents and attacks in, and generate by the hundred.

The standing requirements apply: laptop only, no extra hardware, for other researchers; real catalog products as
twins; Home Assistant as the hub; macOS and Windows (the desktop app, W6, puts a form on this); honesty about what
is synthetic or assumed.

**Success:**
1. `vesper lab generate --rooms 8 --devices devices.yaml home/` writes a home that `vesper lab check` accepts and
   `vesper lab play` walks in 3D, with every room reachable and every device placed and clickable.
2. Any size from 1 to 60 rooms on one floor, at least 200 devices, in under 5 s on the laptop; the same request and
   seed give the same home, byte for byte.
3. A batch: `--count 100 --rooms 3-10` writes 100 valid homes and a summary table.
4. A click on a device opens a Room-334-style panel built from VESPER's own records.
5. Optional looks, never required: an HSSD scene from Habitat, or a Blender-dressed model of the generated home
   (Claude through Blender MCP).

**Non-goals (first build):** several storeys; yards beyond a porch; rooms that are not rectangles; any model or
Blender in the core path. **Anti-goals:** a generated home with a room no one can reach; a switch no one can reach; a
generated layout presented as a real home.

## 1. The request

Flags for the simple case (`--rooms 8`, `--seed 3`) and a file for the rest, `vesper-request/1`:

```yaml
format: vesper-request/1
rooms: 8                                  # or a program, which wins over the count:
program: [living, kitchen, bedroom, bedroom, bedroom, bathroom, bathroom, office]
seed: 0
devices:
  - {product: "zigbee:kwikset:66492-001", at: front_door}         # a real product, pinned
  - {product: "zigbee:aqara:mccgq11lm", at: exterior_doors}
  - {category: light, per: room}                                   # a category: VESPER picks a real product
  - {category: motion_sensor, per: room, except: [bathroom]}
  - {category: plug, count: 6}
  - {category: thermostat, at: hall}
residents: 2                               # optional: generated routines (later; W3 converters)
```

A category picks one real product of the catalog at L1 or above, deterministically from the seed, unless the request
pins one. Every device's position follows the rules of §3 and can be overridden in the written home file afterwards.

## 2. The floor plan

The generator writes `vesper-map/1`, the format every Lab feature already reads, so walking, reach, residents, the
network, scenarios and the 3D view work on a generated home unchanged.

- **The program.** A count becomes a room list by a fixed table (executor's call): 1 = studio; 2 = living, bedroom;
  3 = + kitchen; 4 = + bathroom; then bedrooms, bathrooms (one per two bedrooms), an office, a dining room, a laundry
  in turn. A hall is added from 5 rooms, a corridor spine from 9. Every home gets a porch outside the front door.
- **Sizes.** Each room type has a typical area range (e.g. bedroom 10–16 m², kitchen 8–14 m², bathroom 4–7 m²);
  the seed picks within it.
- **Layout.** A rectangle footprint sized to the program is split by a squarified partition, rooms in program order,
  with the corridor (or the hall) spanning it so every room touches circulation; aspect ratios stay within 1:2.5.
- **Walls and doors.** 20 cm walls, as the existing maps have. Every room gets a 0.9 m door to the corridor, hall or
  living room (a spanning tree of the adjacency graph, from the entrance); a front door joins the hall (or the
  living room) to the porch. Doors are left open by default; the home file lists the ones that close.
- **Checks.** Rooms do not overlap; every room is reachable from the front door on the walkable grid; every door is
  at least 0.8 m wide on the grid; the map loads through `WorldMap`.

## 3. Placing devices

Rules per category, in metres, each position marked `assumed` in the home (executor's call; W8 replaces the sensing
ones with sourced profiles):

| Category | Where |
|---|---|
| light, smoke and gas alarm | the ceiling, at the room's centre |
| motion and presence sensor | a ceiling corner facing the room's centre |
| contact sensor, lock | on the door (`at: front_door`, `exterior_doors`, or a named doorway) |
| switch, button, thermostat | the wall beside the room's door, 1.2 m high, within reach from the door |
| plug | the floor along a wall, spaced around the room |
| anything else | a table position in the room |

Several devices of one rule in one room are spread out. The network follows W7a: the router in the largest indoor
room, the coordinator on the hub, and `check` names any device out of range.

## 4. The walkthrough

The 3D view already draws a home without a scene from its map. For generated homes it gains:
- walls 2.6 m high with door openings, a floor and a ceiling, a light per room type's colour;
- simple furniture per room type (bed, sofa, table, counters, desk as boxes), so a room reads as what it is, placed
  clear of doors and walkways;
- every device as a small model at its position, clickable.

**The device panel** (Room 334's, from VESPER's records):
- the product: vendor, model and category; a photo or link from the catalog source;
- its model basis: fidelity level and the sources behind it, with anything `assumed`;
- its connections: its links and their quality (W7a);
- its interfaces and role: its Home Assistant entities and the automations that name them;
- its live state.

## 5. Optional looks

The generated map is always the source of truth for walking, reach and sensing; a look only changes what is drawn.
- **`--look habitat`** (not built yet: one HSSD scene is downloaded, and `vesper lab new --scene` makes HSSD homes): the HSSD scene (downloaded with `vesper lab scenes`) whose room program is closest, with the
  devices placed by §3 through the existing W1a path. Limited to HSSD's scenes (CC BY-NC).
- **`vesper lab dress <home>`** (Blender): writes a build brief and a Blender Python script from the map: walls,
  doors, rooms with their types, device positions, the scale. A user with Blender and Blender MCP (MIT, installed by
  the user) asks Claude to run it, furnish and texture the rooms, and export `scene.glb` into the home folder. The
  3D view draws it aligned to the map. The GLB is kept, so the home plays again without Blender or a model.
- **ProcTHOR houses in Habitat format** (`hssd/ai2thor-hab`, about 12,000 houses) are a candidate for a later look:
  access is gated and no license is stated, so they stay out until it is.

## 6. Batches

`vesper lab generate --count N --rooms 3-10 --devices devices.yaml --out homes/` writes N homes (seeds 0..N-1) and
`homes/summary.csv`: per home, rooms, floor area, devices per category, devices out of range.

## 7. Testing

- Unit: the program from a count; layouts for 1–60 rooms and many seeds (no overlap, every room reachable, doors
  wide enough, the map loads); placement rules; the request file's mistakes named; determinism.
- CLI: generate, check, a batch and its summary.
- Performance: 60 rooms and 200 devices under 5 s.
- The page: a headless look at a generated 12-room home (walls, furniture, a device panel open), shots read.

## 8. Builder's choices

Left to the builder: the partition algorithm's details; furniture shapes; colours; the exact program table and area
ranges, as long as §2's checks hold.
