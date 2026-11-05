# Scan and 3D-model import on the scan test set (task B5, 2026-11-04)

How well `vesper lab import <scan or model>` turns a RoomPlan export, a mesh scan or a 3D model from the web into a
VESPER home, measured against a hand-written truth with `vesper validate scans` (vesper/validation/scans.py,
tests/validation/test_scans.py). THIS ROUND: the 19 models of the scan test set, every mesh read by both readers
(geometric, Claude); a RoomPlan file by its own reader. With 4, 8 and 7 models per kind, every share below is an
indication, not an estimate: one model moves a share by 12 to 25 points.

**In-sample.** Task B3's geometric reader (its slicing heights, gap widths, room-size and flank thresholds, the
up-axis scoring) and task B3b's render (cut height, colours, the note given to Claude) were developed and tuned on
this same set; 7 of the 15 Claude readings below were made during B3b. The geometric scores are therefore
in-sample, and Claude's partly so. No held-out scan set exists yet (see "Limits").

## Data

The test set is the main checkout's git-ignored `.vesper/datasets/scans/` (README.md, MANIFEST.json with sources,
licences and the sha256 of every file). Nothing of it is in the repository, and no model content or Claude reading
text is reproduced here: only folder names, counts and VESPER's own messages.

| Folder | Kind (this evaluation) | File imported | Licence (per MANIFEST.json) |
|---|---|---|---|
| litereality-darwin-bedroom-roomplan | RoomPlan (real) | roomplan/room.usdz (its room.json read) | Apache-2.0 |
| litereality-girton-common-room-roomplan | RoomPlan (real) | roomplan/room.usdz (its room.json read) | Apache-2.0 |
| litereality-cued-meeting-room-roomplan | RoomPlan (real) | roomplan/room.usdz (its room.json read) | Apache-2.0 |
| synthetic-domotwin-structure-roomplan | RoomPlan (synthetic CapturedStructure) | structure.json | MIT |
| litereality-darwin-bedroom-lidarmesh | mesh scan (LiDAR) | textured_output.obj | Apache-2.0 |
| litereality-girton-common-room-lidarmesh | mesh scan (LiDAR) | textured_output.obj | Apache-2.0 |
| litereality-cued-meeting-room-lidarmesh | mesh scan (LiDAR) | textured_output.obj | Apache-2.0 |
| arkitscenes-42897559 | mesh scan (ARKit LiDAR) | 42897559_3dod_mesh.yup.glb | ARKitScenes (Apple, non-commercial) |
| arkitscenes-47333441 | mesh scan (ARKit LiDAR) | 47333441_3dod_mesh.yup.glb | ARKitScenes (Apple, non-commercial) |
| synthetic-domotwin-house-mesh | mesh scan (synthetic, scan-like) | house.glb | MIT |
| local-habitat-apartment_1 | mesh scan (Replica, decimated) | apartment_1.glb | CC-BY-NC-4.0 |
| local-habitat-skokloster-castle | mesh scan (photogrammetry) | skokloster-castle.glb | CC-BY-NC-4.0 |
| polypizza-house-interior-romagnoli | web model (low-poly) | .glb | CC-BY 3.0 |
| polypizza-apartment-google | web model (low-poly) | .glb | CC-BY 3.0 |
| polypizza-apartment-chen | web model (low-poly) | .glb | CC-BY 3.0 |
| web-babylon-diligent-kitchen | web model (archviz) | kitchen.glb | Apache-2.0 |
| web-blender-classroom | web model (archviz) | classroom.glb | CC0 |
| web-blender-italian-flat | web model (archviz, 2 storeys) | italian-flat.glb | CC-BY |
| local-habitat-van-gogh-room | web model (Sketchfab) | van-gogh-room.glb | CC-BY-NC-4.0 |

**What could not be had** (MANIFEST.json `needs_access`): Polycam's Explore gallery (account login; only "savable"
captures, CC 4.0), Sketchfab including "made with Polycam" room scans and RoomPlan exports (login, even for CC
models; the Blender MCP asset libraries were switched off), Apple RoomPlan sample scans (none exist), ScanNet and
ScanNet++ (signed terms), Matterport3D (academic agreement), 3D-FRONT and Structured3D (application form), the full
Replica set (licence acceptance; the decimated apartment_1 is here), ZInD (request form; panoramas and 2D plans).
Six further sources were openly downloadable but state no licence (`found_but_no_licence`: two Polycam sample sets on
Hugging Face, three GitHub repositories with real RoomPlan USDZ/JSON exports or Polycam GLBs, one Polycam GLB) and
were not fetched. So no Polycam-made file is in the set; the LiteReality scans come from the 3D Scanner App, which
wraps the same Apple RoomPlan output Polycam's Room mode exports.

## The truth

Written by hand, before any reading of this task was scored, in `<dataset>/truth.json` (`vesper-scan-truth/1`;
local, never committed). Per model: rooms (a number, or a range when the evidence allows several), each room's
accepted VESPER types, doors between rooms and doors out of them, windows, whether a real doorway leads out, storeys,
whether a ceiling exists, the true metres per unit (with the range accepted as right) and the up axis; and, in
words, how each was told and how sure it is. A count that cannot be told is null and not scored. Counts are of the
lowest storey, the one the importer reads.

- **RoomPlan files (4):** read off the CapturedRoom/CapturedStructure JSON itself (walls, doors, windows, openings,
  sections, story); an opening is a door with no leaf, a door on a wall with no scanned room behind it a door out.
  Certainty high. The meeting room is one room labelled with two RoomPlan sections: 1 or 2 rooms accepted.
- **The three LiteReality LiDAR meshes:** the RoomPlan JSON of the same capture (same room, same frame, per the
  manifest). High, except that whether a door stood open during the scan is not known (a closed door is a surface
  in the mesh).
- **Other meshes (12):** the top-down Blender renders in `_views/` (cut below the ceiling), the model's own object
  names and bounding boxes where it has them (classroom: walls, doorFrames, window bays; italian-flat: Door*, Glass
  Window*; van-gogh-room: Doors, glass), and, where neither shows walls, VESPER's own render at 1.3 m (kept in
  `<dataset>/_eval/renders/`). That render is what the Claude reader sees, so a count read only from it is not
  independent: it was used for the synthetic house's two windows and otherwise left the count unknown (doors and
  windows of the kitchen, the castle hall, the first ARKit room). Certainty is low for apartment_1 (3-4 rooms),
  romagnoli (6-8 rooms; its doors and windows unknown), chen (1-3 rooms: one open shell with three floor finishes)
  and italian-flat (3-7 rooms on the lowest storey; its doors between rooms unknown).

Of the 19 truths, rooms are known for all, room types for 18, doors between rooms for 16, doors out for 12, windows
for 11, the entrance for 11, scale and up axis for all 19, the ceiling for 18.

## Protocol

1. **Import.** For each model and reader, `vesper lab import`'s own steps (`scan_import.read_scan`, then as
   `_lab_import_scan`): the reading, `plan_transform` and `rectify`, `check_house(metric=True)`, then
   `generate_from_plan` with the scan's objects as furniture, the model as `scene.glb` and its provenance, in a
   temporary folder, with the starter devices (seed 0), and `layoutcheck.check` (catalog: the main checkout's
   `catalog/vds`). The stage reached is "read" (no plan), "rectify", "check_house", "build", "layout" or "done".
2. **Readers.** A RoomPlan file is read by its own reader (`--reader` is for meshes): `room.usdz` is RoomPlan's mesh
   export, so its `room.json` is read, as the importer chooses. A mesh is read by the geometric reader
   (`scan_mesh.read_mesh`) and by Claude (`scan_render.read_mesh_with_claude`: a top-down render at 2 cm a pixel,
   read by part A's plan reader through the user's Claude Code, `claude -p`, no `--model`). Both readers share the
   up-axis, storey and scale detection (`scan_mesh.upright_mesh`), so their up and scale results are the same by
   construction.
3. **Claude's readings,** one at a time, kept under `<dataset>/_claude/<folder>/`: every CLI envelope as it arrives,
   the render read, the reading as `--save-plan` writes it (`reading.json`, `vesper-scan-reading/1`) and `meta.json`.
   Task B3b's 7 earlier readings (`claude-plan.json`, `claude-meta.json`, `claude-render.png`) are reused only
   while the render they were read from is byte-identical to the render the code draws now: 6 were; romagnoli's was
   drawn by an earlier B3 (other up axis and scale) and was read again. Their meta has no "entrance drawn" flag: it
   is worked out again from their render. New readings: 7 (arkitscenes x2, skokloster, romagnoli, kitchen,
   italian-flat, van-gogh-room). Two models (google, chen) are refused before any render (scale), so Claude is not
   called. `--offline` reuses everything; the run below is offline.
4. **Metrics,** per model, against the truth:
   - **rooms, doors between rooms, doors out, windows:** the count of the plan as read against the true count or
     range; right when inside it; the error is the distance to it. A model no plan was read from scores wrong where
     the truth knows the count.
   - **types:** the read rooms' types matched one to one to the true rooms' accepted types (a maximum matching),
     pooled over the models.
   - **entrance:** "real" (a doorway the reader saw), "assumed" (Claude put it where the render shows a wall, or the
     geometric reader took the widest door out of the rooms into a space too small to be a room) or "none". Right
     when real and the model shows a doorway out, or not real when it shows none.
   - **scale, up axis, ceiling:** the reader's metres per unit inside the true range; its up axis the true one; a
     ceiling found as the model has one. For a mesh no plan was read from, they are scored from the detection
     (`upright_mesh`) when it succeeds.
   - **check_house** and **built with 0 layout errors:** shares of every model.

## Results (2026-11-04)

Run: `vesper validate scans --dataset .vesper/datasets/scans --reader both --offline --catalog catalog/vds`
(code dd9f02e, clean tree; the online run before it, `--reader claude` on the 9 meshes with no current reading, made
the 7 new readings). The results JSON (per-model facts, scores, warnings, layout findings) is kept beside the data,
`.vesper/datasets/scans/_eval/results-b5-dd9f02e.json`, not committed.

### By kind and reader

Shares are of the models whose truth knows the field (the count in brackets); types are pooled over rooms.

| | RoomPlan / own reader | Mesh scan / geometric | Mesh scan / Claude | Web model / geometric | Web model / Claude |
|---|---|---|---|---|---|
| Models | 4 | 8 | 8 | 7 | 7 |
| A plan read | 4 | 5 | 8 | **0** | 5 |
| Rooms right | **4/4** | 5/8 | 5/8 | 0/7 | 3/7 |
| Room types right | **5/5** | 5/12 | 7/12 | 0/14 | 11/14 |
| Doors between rooms right | 4/4 | 5/8 | 5/8 | 0/4 | 2/4 |
| Doors out right (mean abs. error) | 4/4 (0) | 1/5 (1.4) | 2/5 (1.0) | 0/3 (0.67) | 3/3 (0) |
| Windows right (mean abs. error) | 4/4 (0) | 1/4 (1.5) | 2/4 (0.75) | 0/3 (2.7) | 2/3 (0.33) |
| Entrance right | 4/4 | 2/5 | 4/5 | 0/2 | 1/2 |
| Scale right | 4/4 | 7/8 | 7/8 | 5/7 | 5/7 |
| Up axis right | 4/4 | 7/8 | 7/8 | 4/7 | 4/7 |
| Passes check_house | 3/4 | 2/8 | **8/8** | 0/7 | 5/7 |
| **Built with 0 layout errors** | **3/4** | **2/8** | **8/8** | **0/7** | **5/7** |
| Stages | done 3, check_house 1 | done 2, check_house 3, read 3 | done 8 | read 7 | done 5, read 2 |

Over all 15 meshes: the geometric reader read 5, built 2 (13%) with 0 layout errors; Claude read 13, built 13 (87%)
with 0 layout errors. Every home that passed check_house was built with 0 layout errors.

### Per model

"a / b": read / true (a range "min-max"); x: wrong; "?": the truth does not know; "-": nothing read.

| Model | Reader | Reading | Stage | Rooms | Types | Doors in | Doors out | Windows | Entrance | Up | Scale | Layout errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| litereality-darwin-bedroom-roomplan | RoomPlan | | done | 1 / 1 | 1/1 | 0 / 0 | 2 / 2 | 1 / 1 | real | y | 1 | 0 |
| litereality-girton-common-room-roomplan | RoomPlan | | done | 1 / 1 | 1/1 | 0 / 0 | 3 / 3 | 2 / 2 | real | y | 1 | 0 |
| litereality-cued-meeting-room-roomplan | RoomPlan | | done | 1 / 1-2 | 1/1 | 0 / 0 | 3 / 3 | 3 / 3 | real | y | 1 | 0 |
| synthetic-domotwin-structure-roomplan | RoomPlan | | check_house | 2 / 2 | 2/2 | 1 / 1 | 0 / 0 | 0 / 0 | none (right) | y | 1 | - |
| litereality-darwin-bedroom-lidarmesh | geometric | | check_house | 1 / 1 | 0/1 | 0 / 0 | 0 / 2 x | 0 / 1 x | none x | y | 1 | - |
| | Claude | B3b | done | 2 / 1 x | 1/1 | 1 / 0 x | 1 / 2 x | 2 / 1 x | assumed x | y | 1 | 0 |
| litereality-girton-common-room-lidarmesh | geometric | | done | 1 / 1 | 0/1 | 0 / 0 | 2 / 3 x | 0 / 2 x | real | y | 1 | 0 |
| | Claude | B3b | done | 2 / 1 x | 1/1 | 1 / 0 x | 1 / 3 x | 2 / 2 | real | y | 1 | 0 |
| litereality-cued-meeting-room-lidarmesh | geometric | | check_house | 1 / 1-2 | 1/1 | 0 / 0 | 0 / 3 x | 0 / 3 x | none x | y | 1 | - |
| | Claude | B3b | done | 1 / 1-2 | 1/1 | 0 / 0 | 1 / 3 x | 5 / 3 x | real | y | 1 | 0 |
| arkitscenes-42897559 | geometric | | read | - / 1 x | 0/1 | - | ? | ? | ? | y | 1 | - |
| | Claude | new | done | 2 / 1 x | 0/1 | 1 / 0 x | 1 / ? | 1 / ? | assumed (?) | y | 1 | 0 |
| arkitscenes-47333441 | geometric | | read | - / 1 x | 0/1 | - | - / 1-2 x | ? | - x | **x** x | 1 | - |
| | Claude | new | done | 1 / 1 | 0/1 | 0 / 0 | 1 / 1-2 | 2 / ? | real | **x** x | 1 | 0 |
| synthetic-domotwin-house-mesh | geometric | | done | 3 / 3 | 3/3 | 2 / 2 | 1 / 1 | 2 / 2 | real | y | 1 | 0 |
| | Claude | B3b | done | 3 / 3 | 2/3 | 2 / 2 | 1 / 1 | 2 / 2 | real | y | 1 | 0 |
| local-habitat-apartment_1 | geometric | | read | - / 3-4 x | 0/3 | - x | ? | ? | ? | z | 1 | - |
| | Claude | B3b | done | 3 / 3-4 | 2/3 | 2 / 1-3 | 1 / ? | 2 / ? | real (?) | z | 1 | 0 |
| local-habitat-skokloster-castle | geometric | | check_house | 1 / 1 | 1/1 | 0 / 0 | 0 / ? | 1 / ? | none (?) | z | **0.399** x | - |
| | Claude | new | done | 1 / 1 | 0/1 | 0 / 0 | 1 / ? | 7 / ? | real (?) | z | **0.399** x | 0 |
| polypizza-house-interior-romagnoli | geometric | | read | - / 6-8 x | 0/7 | ? | ? | ? | ? | **-y** x | 3.10 | - |
| | Claude | new | done | 9 / 6-8 x | 7/7 | 8 / ? | 1 / ? | 8 / ? | real (?) | **-y** x | 3.10 | 0 |
| polypizza-apartment-google | geometric | | read | - / 1 x | 0/1 | - x | ? | ? | ? | - x | refused x | - |
| | Claude | render refused | read | - / 1 x | 0/1 | - x | ? | ? | ? | - x | refused x | - |
| polypizza-apartment-chen | geometric | | read | - / 1-3 x | ? | ? | ? | ? | ? | - x | refused x | - |
| | Claude | render refused | read | - / 1-3 x | ? | ? | ? | ? | ? | - x | refused x | - |
| web-babylon-diligent-kitchen | geometric | | read | - / 1 x | 0/1 | - x | ? | ? | ? | y | 1 | - |
| | Claude | new | done | 1 / 1 | 0/1 | 0 / 0 | 1 / ? | 3 / ? | real (?) | y | 1 | 0 |
| web-blender-classroom | geometric | | read | - / 2 x | 0/2 | - / 2 x | - / 0-1 x | - / 4-5 x | ? | y | 1 | - |
| | Claude | B3b | done | 2 / 2 | 2/2 | 1 / 2 x | 1 / 0-1 | 3 / 4-5 x | assumed (?) | y | 1 | 0 |
| web-blender-italian-flat | geometric | | read | - / 3-7 x | 0/2 | ? | - / 1-2 x | - / 3-5 x | - x | y | 1 | - |
| | Claude | new | done | 8 / 3-7 x | 2/2 | 7 / ? | 1 / 1-2 | 5 / 3-5 | real | y | 1 | 0 |
| local-habitat-van-gogh-room | geometric | | read | - / 1 x | 0/1 | - x | - / 1-2 x | - / 1 x | - x | y | 1 | - |
| | Claude | new | done | 1 / 1 | 0/1 | 0 / 0 | 1 / 1-2 | 1 / 1 | assumed x | y | 1 | 0 |

The ceiling (found or not, as the model has one) is right for all 4 RoomPlan files and all 8 mesh scans, and for 4
of the 6 web models whose truth knows it: romagnoli, read upside down, gets a ceiling it does not have, and google is
refused before any is looked for.

**Time and notional cost of Claude's readings.** The 7 new readings: one CLI call each (Opus 5,
`claude-opus-5[1m]`, Claude Code's default; Haiku 4.5 also in each envelope for a few tokens), 33 to 246 s (mean 85
s), $0.11 to $0.46 notional (mean $0.19, **$1.31** in all; romagnoli, 9 rooms, the most). Task B3b's 6 reused
readings took 47 to 383 s; their cost was not kept.

## What fails, and why

**RoomPlan files import as scanned.** All four are read exactly (rooms, types, doors, windows, entrance), and the
three real scans build with 0 layout errors. The fourth, the synthetic two-room CapturedStructure, stops at
check_house ("no entrance"): its only door is between its two rooms, so there is no way in. That is the file, not
the reader; the import asks the user to choose an entrance rather than invent one.

**The three worst failures:**

1. **The geometric reader reads no web model (0 of 7) and 3 of 8 mesh scans not at all.** Its two refusals:
   - "no room: the walls 1 m above the floor enclose no space of 1.5 m² or more" (kitchen, van-gogh-room,
     romagnoli, both ARKit rooms). An archviz set models only the walls the camera sees (the kitchen two, the Van
     Gogh room three), so no slice is closed; romagnoli's walls are 1 unit tall and it is read upside down (failure
     2), so the 1 m slice passes over them; the ARKit scans are open where the scan stopped and sprayed through the
     doorways.
   - "N rooms and no door between any of them: the model's doors were not found" (classroom, italian-flat,
     apartment_1). A modelled door leaf, a door frame with a transom, or a glazed partition is solid at 1.0 and 1.6 m,
     so the doorway reads as wall; B3 refuses rather than invent a door. Where it reads, it misses windows (1 of 4
     right) and doors out (1 of 5): a closed door in a LiDAR scan is a surface, and darwin and cued stop at
     check_house with no entrance.
   Claude, reading the same model's render, reads 13 of the 15 and builds all 13: it closes a room the render shows
   only partly walled, and places doors where the render shows a wall with a door leaf or a gap in the furniture.
2. **The up axis is wrong for 2 models, the scale for 1, and both readers inherit it.**
   arkitscenes-47333441 (a valid Y-up GLB) is taken as **x** up ("the shape alone does not show which way is up: x
   up is taken"); why was not traced (the scan sprays heavily through its doorways, per the manifest). romagnoli is
   taken as **-y** up ("-y up is taken (of -y, y)": a dollhouse with no ceiling and walls 1 unit tall, which the
   up-axis scoring cannot tell from its upside-down self). skokloster-castle is in metres with a 7.2 m ceiling, beyond the 2.1-4.0 m the scale rule accepts, so it is
   rescaled by 0.399 to put the ceiling at 2.5 m (said in a warning): the Global Constraints' rule, by design, cannot
   tell a hall from a dollhouse. Claude reads whatever render it is given: it still read 1 room and its doorway from
   the sideways ARKit render, and 9 rooms of romagnoli seen from below (a mirror image of its plan), but the home
   built is in the wrong frame (romagnoli) or at 0.4 of its size (skokloster). Given `--scale 1`, the geometric
   reader reads skokloster (3 rooms where there is one hall).
3. **Two web models are refused by both readers for their scale:** google (1 unit about 4 micrometres: "the model
   would be rescaled by 3.122e-06 ... give --scale") and chen (no ceiling, walls 4.36 units, no door to measure:
   "give --scale"). The refusal is right and its message names the fix, but nothing is built without the user. With
   the true scale given, the geometric reader still finds no closed room in either (google is a single furnished
   box; chen an open shell); Claude was not run on them with a scale (it would cost a reading each).

**Where Claude's reading falls short:**

- **Spill read as rooms.** On the real LiDAR scans Claude reads the geometry sprayed through an opening, or a
  half-scanned neighbour, as a second room (darwin, girton, arkitscenes-42897559: 2 rooms for 1), with a door
  between, and so too few doors out (girton: 1 of 3 openings). On the larger models it over-segments
  (romagnoli 9 rooms for 6-8, italian-flat 8 for 3-7).
- **The entrance is assumed where the scan shows none,** honestly flagged: darwin (its two doors were closed in the
  scan), the classroom (no door out is modelled), arkitscenes-42897559 and van-gogh-room (whose two doors are in
  walls the render draws whole). The import says "entrance assumed: ... check it in the lobby".
- **Windows** are right on 4 of 7 known: cued gets 5 for 3 (window frames and the glazed door read as windows), the
  classroom 3 of its 4-5 bays, darwin 2 for 1.
- **Types** are right on 18 of 26 true rooms (pooled with the mesh scans). Wrong: the kitchen read as a living room,
  the ARKit bathroom as a living room and the ARKit bedroom as living + kitchen, van-gogh-room typed "other" though
  its bed shows, the castle hall typed "hall" (the truth accepts "other" or "living"; "hall" is defensible, so this
  one is the truth's strictness). The geometric reader types every room "other" by design (nothing is invented; the
  user passes `--types`): its 5 right are the rooms the truth also accepts as "other".

**Passing the import is not fidelity.** Every one of Claude's 13 readings builds with 0 layout errors, including
romagnoli read upside down and skokloster at 0.4 of its size. The count and frame metrics above are the measure of
reading; "built" says only that the plan is a house VESPER can simulate.

## Limits

- **Small and in-sample.** 19 models, 4/8/7 per kind; B3's thresholds and B3b's render were tuned on this set, so
  the geometric results (and, less, Claude's) overstate what a new scan would get. A held-out set needs the sources
  listed under "What could not be had" (Polycam and Sketchfab logins, ScanNet's terms) or the PI's own scans.
- **No Polycam file, few real RoomPlan files.** The 3 real RoomPlan scans are one-room captures from one app; the
  only multi-room CapturedStructure is synthetic (and its USDZ does not parse). Multi-room RoomPlan merging is not
  tested on real data.
- **The truth is counts, not geometry.** Rooms are scored by count and type, not by outline or area (the models'
  frames differ from the plans'); a reading with the right number of wrong rooms scores right. Doors and windows
  likewise. The RoomPlan truths are exact; for meshes the truth is a judgement from renders and names, with ranges
  where it is unsure and nulls where it cannot tell, and for a few counts it rests on VESPER's own render (marked
  in truth.json).
- **One storey.** Two-storey models (domotwin, italian-flat) are scored on their lowest storey, as the importer reads.
- **Not repeated.** Each Claude reading was made once; their variance is not measured. 6 of the 15 are B3b's.
- **Side checks not in the tables:** with `--scale` given (2.6 romagnoli, 4e-6 google, 1.0 chen and skokloster), the
  geometric reader still refuses romagnoli, google and chen ("no room") and reads skokloster as 3 rooms.

## Reproduce

```
vesper validate scans --dataset .vesper/datasets/scans --reader both --offline   # the kept readings, no Claude
vesper validate scans --dataset .vesper/datasets/scans --reader claude           # reads any mesh not read yet
```
