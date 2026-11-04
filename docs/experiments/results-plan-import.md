# Floor-plan import on CubiCasa5K (task A5, 2026-11-04)

How well `vesper lab import <image>` reads a floor-plan image, measured against CubiCasa5K's annotations, with
`vesper validate plans` (vesper/validation/plans.py, tests/validation/test_plans.py). THIS ROUND: 10 test plans
(seed 0), the default model; the 50-plan run waits for the PI. With n = 10 every number below is an indication, not
an estimate: one plan moves a share by 10 points, and the plans differ widely (6 to 38 rooms).

## Data

- **Source:** CubiCasa5K (Kalervo et al., "CubiCasa5K: A Dataset and an Improved Multi-Task Model for Floorplan Image
  Analysis", SCIA 2019), Zenodo record https://zenodo.org/records/2613548, archive
  `https://zenodo.org/records/2613548/files/cubicasa5k.zip?download=1` (5,469,495,706 bytes, md5
  0ce0b203d1e3c125b51087b219bd23b9 per Zenodo).
- **Licence:** CC BY-NC-SA 4.0 on the Zenodo record (the dataset's GitHub README says CC BY-NC 4.0). Used only to
  measure; nothing of it is in the repository.
- **What was downloaded:** not the 5.5 GB archive. `vesper validate plans --fetch` reads the zip's central
  directory (2.7 MB) and then only the members the sample needs, by HTTP range requests: `test.txt` (400 test
  plans) and, per sampled plan, `F1_scaled.png` and `model.svg` (21 files, 3.1 MB) into the main checkout's
  git-ignored `.vesper/datasets/cubicasa5k/`, with their sha256 in its `MANIFEST.json`:

| File | sha256 |
|---|---|
| test.txt | 3997ee6378007f10e945b2621921127477530abe8f8c053f43e327a365f5ce5a |
| colorful/12030/F1_scaled.png | 016c9e805948e31d8bec85599845875efb70f9c7cb3770a75a03499196b58acf |
| colorful/12030/model.svg | 2db437b91a46486931a18bb8b5885efa3a818f82ea7dbdfa5eafe0fe080ccf0c |
| colorful/13019/F1_scaled.png | 17ccbc254fa87dd00ed17778385e06002e54e55a34ed3c6588cc7f196af05853 |
| colorful/13019/model.svg | 61bd2bc9ff7c3da62355ca14d4011162576d583b5f803034746d9b2a2c12ab81 |
| high_quality/20188/F1_scaled.png | 179d338bfed7de254d5f1aed0193cb0a649b331abc90a9006dc2e7f53e9f36ea |
| high_quality/20188/model.svg | 5f073aa0f377c3a02fb73de7e8343cd3923631c43f93e17e299fe11405691ff4 |
| high_quality_architectural/1590/F1_scaled.png | 55c9c71e482addf305d1ebedabc808ef6e9518afaaf0a4ce6c6e34f0bf7d78c0 |
| high_quality_architectural/1590/model.svg | 56b1cabba9e250a0fdde358032556532ab139d64bfc8bcfe549480fee93664fb |
| high_quality_architectural/1601/F1_scaled.png | 3ce5ee66016adaebecc068ecc5309783aa5e3bfaf5438fa9fbd237da4546326a |
| high_quality_architectural/1601/model.svg | b4faf2a08896d98668b257ce0836f43684bc7bfde31a827b7515241ee95b1ef3 |
| high_quality_architectural/2029/F1_scaled.png | 37a07be71705e0da2b83b423ede79e19abe832bf004db18a1df7a67efc1cd90c |
| high_quality_architectural/2029/model.svg | e5fabbd91461ab1b2947ff1c4809cb97653609428a3b4ceb678358c57f1b1c7a |
| high_quality_architectural/2090/F1_scaled.png | ffc9f0b188cca1f9c1930d31c5f3771445ddade3bbbb166841efa0f6bc922e58 |
| high_quality_architectural/2090/model.svg | 35a89f83d84a3aca5ffd92645245a6c9d45401853ea22486b649d5efaa51958a |
| high_quality_architectural/2207/F1_scaled.png | d1161dcbeb74dbd0d55ba11bfb351a917bd0409305d6a8a54b1b526be6539654 |
| high_quality_architectural/2207/model.svg | 8caf30dd55b37007455099fbcb88bd86b6ebd0c3cfc641bea63349cd5cfeb6f3 |
| high_quality_architectural/5023/F1_scaled.png | 7bcf152f1178ab3ea02bc169e52672d775f76dbbbe3d3424b48e9b4f59a1f0b1 |
| high_quality_architectural/5023/model.svg | 8ac7854f4d8687ccd6bb6275ca992e69114aceedebbddee3657d924ec01ca12f |
| high_quality_architectural/6276/F1_scaled.png | 482a03ef66de7bfa4e64c96b482caa9bb438f18a29f0eb2fe4ec0e8a467ff9be |
| high_quality_architectural/6276/model.svg | 60c711235c80f840a4124cc7a6d851b3223845f7cf1b91df40c95e8e4f28b87c |

## Protocol

1. **Sample.** The 400 plan folders of `test.txt`, in file order, shuffled with `random.Random(0)`; the first n.
   The sample of 10 is the start of the sample of 50, so the 50-plan run reuses these 10 paid readings.
2. **Ground truth** (`parse_cubicasa_svg`). `model.svg`'s coordinates are the pixels of `F1_scaled.png` (checked on a
   plan's image, and by the readings: Claude's coordinates span the same pixels). Every `Floorplan` group is read:
   two of the ten images (6276, 5023) draw a two-storey home's floors side by side in the one image, so both floors are what the image
   shows. Rooms are the `Space` polygons (outdoor spaces such as balconies and terraces included, typed "other", as
   the reader's prompt asks); doors and windows are the `Door` and `Window` rectangles: middle and width (the long
   side). A door joins the spaces found 20 px beyond each face of its wall ("outside" where there is none); a
   window lights the indoor room beside it. Two spaces whose outlines run together for at least 0.6 m with no wall
   between them (open-plan kitchen, living room and hall) are joined by an opening in the true plan's doors, as the
   reader's prompt asks Claude to do; openings are not counted as drawn doors. **Scale:** each room's hidden
   dimension label ("4.21 m x 2.06 m") against its drawn size: the median gives 0.0100 m/px on the 7 labelled
   plans (0.00999 on one); the 3 unlabelled ones take CubiCasa5K's 1 cm a unit.
3. **Reading.** `plan_read.read_plan_image(F1_scaled.png)`, the user's Claude Code headless (`claude -p`), no
   `--model` (Claude Code's default: Opus 5, which also called Haiku 4.5 for a few tokens), one plan at a time. Every
   CLI envelope is written to `.vesper/datasets/cubicasa5k/readings/<plan>/envelope-<k>.json` the moment it arrives,
   then `plan.json` (or `error.txt`) and `meta.json` (wall time, the envelopes' `total_cost_usd`, models, image
   sha256). A plan with a saved reading is never read again; `--offline` recomputes every metric from them.
4. **Metrics** (both plans in the image's pixels):
   - **Rooms found:** F1 of read rooms vs true rooms, a pair counting when IoU >= 0.5 (IoU on a 2 px raster), one
     to one, highest IoU first. Pooled over the plans (micro); the per-plan mean is also given.
   - **Types:** over matched rooms, the read type equal to the true room's mapped type (below); "lenient" counts
     hall and corridor as one type.
   - **Doors found:** true doors (drawn `Door` elements) with a read door within 0.5 m (50 px at the true scale),
     one to one, nearest first. Door precision likewise.
   - **Area error:** |read - true| / true, for the total area and for each matched room, BOTH measured with the
     true scale (it measures the reading, not the scale).
   - **The import:** the read plan through `vesper lab import`'s steps: scaled (by the scale Claude read, else by
     the true scale, as if the user typed it), `rectify`, `check_house`; when it is a
     house, built by `generate_from_plan` (starter devices, seed 0) and checked by the layout checker
     (`layoutcheck.check`). "Passes check_house" = gets through scaling, rectify and check_house, as the import
     requires. The TRUE plan goes through the same steps, as a baseline for what the pipeline accepts.
   - **Time and notional cost** per reading: wall time of the CLI calls; `total_cost_usd` from the envelopes
     (notional: what the tokens would cost at API prices; the session is a subscription).

### Room classes (CubiCasa5K, the first word after "Space", as its own loader reads it) to VESPER types

| VESPER type | CubiCasa5K classes |
|---|---|
| living | LivingRoom, Lounge |
| kitchen | Kitchen |
| bedroom | Bedroom |
| bathroom | Bath, Sauna (CubiCasa5K's own grouping) |
| dining | Dining, EatingArea |
| office | Office, Library, Den |
| laundry | Utility |
| hall | Entry, DraughtLobby, Hall |
| corridor | HallWay |
| other | every other class: Outdoor (balconies, terraces, gardens), Closet, Storage, DressingRoom, Garage, CarPort, TechnicalRoom, SwimmingPool, Room, Undefined, UserDefined, ... |

## Results (10 plans, 2026-11-04)

Run: `vesper validate plans --dataset .vesper/datasets/cubicasa5k --n 10 --seed 0` (plus one earlier `--n 1`
call that read the first plan; its reading was reused). The results JSON (per-plan matches included) is kept beside
the data, `.vesper/datasets/cubicasa5k/results-n10-seed0.json`, not committed.

| Plan | Image (px) | Floors | True rooms / read / matched | Room F1 | Types | Doors found | Total area error | Read: import | True plan: import | s | $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| high_quality_architectural/6276 | 2171x2708 | 2 | 38 / 27 / 19 | 0.58 | 63% | 8/28 | 10% | stopped at rectify | stopped at scale | 298 | 0.65 |
| high_quality/20188 | 933x615 | 1 | 6 / 6 / 6 | 1.00 | 100% | 6/6 | 3% | stopped at rectify | stopped at rectify | 117 | 0.26 |
| high_quality_architectural/2090 | 989x822 | 1 | 6 / 6 / 6 | 1.00 | 100% | 4/5 | 7% | stopped at rectify | stopped at rectify | 127 | 0.26 |
| high_quality_architectural/2207 | 2323x1418 | 1 | 17 / 15 / 14 | 0.87 | 100% | 12/14 | 14% | house; stopped at build | stopped at rectify | 498 | 0.96 |
| high_quality_architectural/1601 | 1484x793 | 1 | 7 / 11 / 6 | 0.67 | 50% | 5/6 | 44% | stopped at rectify | stopped at rectify | 275 | 0.52 |
| high_quality_architectural/1590 | 1305x1533 | 1 | 10 / 10 / 8 | 0.80 | 75% | 6/8 | 5% | stopped at rectify | stopped at rectify | 199 | 0.40 |
| high_quality_architectural/5023 | 2831x1490 | 2 | 24 / 9 / 4 | 0.24 | 0% | 1/30 | 70% | house; layout 0 err | stopped at scale | 89 | 0.28 |
| colorful/13019 | 1242x985 | 1 | 6 / 7 / 5 | 0.77 | 100% | 3/3 | 2% | stopped at rectify | house; layout 0 err | 65 | 0.18 |
| high_quality_architectural/2029 | 1955x2013 | 1 | 23 / 20 / 20 | 0.93 | 95% | 11/19 | 1% | stopped at rectify | stopped at rectify | 325 | 0.69 |
| colorful/12030 | 1035x535 | 1 | 6 / 4 / 2 | 0.40 | 100% | 3/3 | 3% | stopped at rectify | stopped at rectify | 99 | 0.21 |

"Read: import" is the read plan through the import; "True plan: import" the annotation itself through the same
steps ("stopped at scale" for 6276 and 5023: one annotated space's polygon crosses itself, which `load_plan`
refuses). Types are over the matched rooms of the plan.

### Aggregate (n = 10; pooled over the plans)

| Metric | Value |
|---|---|
| Rooms found: F1 at IoU >= 0.5 | **0.70** (precision 0.78, recall 0.63; 90 of 143 true rooms, 115 read); per-plan mean 0.73, range 0.24 to 1.00 |
| Room types right (matched rooms) | **81%** (73 / 90); hall = corridor changes nothing; **95%** (70 / 74) on the rooms whose CubiCasa5K class names a use (not Room, Undefined, UserDefined) |
| Doors found within 0.5 m | **48%** (59 / 122; door precision 46%) |
| Total area error (true scale) | mean **16%**, median 6% |
| Area error per matched room (true scale) | mean 13%, median 7% |
| Scale read from the image | 6 of 10 (3 dimension lengths, 3 printed floor areas); its error 1.3% to 11.5% (mean 5.2%) |
| Passes check_house (scale, rectify, check_house) | **2 / 10** (the true plans: 1 / 10) |
| Built home passes the layout check with 0 errors | **1 / 10** (the true plans: 1 / 10) |
| Time per reading (wall) | mean **209 s**, median 163 s, 65 to 498 s; 35 min for the 10 |
| Notional cost per reading | mean **$0.44**, median $0.34, $0.18 to $0.96; **$4.40** for the 10 |
| Readings that failed | 0 (one plan, 5023, needed the retry) |

Every reading was by Opus 5 (`claude-opus-5[1m]`, Claude Code's default; Haiku 4.5 also appears in each envelope
for a few tokens). One call per plan except 5023 (two). Task A3 reported $6.53 notional for its one reading; the
envelopes here report $0.18 to $0.96 (6276, the largest image: 22k output tokens, 19k of them thinking, $0.65).

## What fails, and why

**Reading is mostly right; the import's rectify step refuses most plans.** On 7 of the 10 plans Claude found 80% or
more of the rooms (F1 0.67 to 1.00), with the total area within 15% on 6 of them. Yet 8 of the 10 read plans are
refused by `rectify`, one more at the build, and 9 of the 10 annotations themselves are refused when put through the
same steps: for real plans the bottleneck is the import's straightening, not the reading.

1. **A window onto a balcony** (4 of the 8 refusals: 20188, 2090, 1590, 12030). Claude reads the balcony as a room
   ("other", as the prompt asks) and the window between it and the bedroom or living room as lighting the room,
   correctly. The wall between them is thin (15 to 29 cm), rectify closes the gap, the wall becomes a shared wall,
   and the window is then "not within 0.5 m of an outside wall of the room": the import refuses the plan. Example,
   20188: bedroom MH drawn from y = 170, balcony from y = 155, the window at y = 163 between them. The same rule
   refuses 3 of the true plans. A fix in `rectify` (a window on a wall shared with an "other" room such as a
   balcony is kept, or dropped with a warning) would let these through; not done here (task A5 measures).
2. **A door wider than the wall it sits on** (13019, 2029): a door at a corner or between two rooms that share only
   a short stretch of wall after straightening (2029: a 1.39 m sliding opening on a 0.80 m shared wall).
3. **Walls too far apart to straighten** (1601: 0.59 m; the true plan 2.70 m) and **rooms that do not touch** for a
   door (6276: hall to the bridge SILTA across the open void of the second floor).
4. **Built, then refused** (2207): passes check_house, but `generate_from_plan` refuses it: "the floor outside every
   room joins hall_2, kitchen, living in one opening": the read room outlines leave a gap that reaches three rooms.
5. **The one plan that passes everything is a misreading** (5023): two storeys side by side; Claude read 9 rooms of
   the left floor only (4 of 24 true rooms matched, 1 of 30 doors, 0% types, 70% area error), and that small
   coherent plan passes check_house and the layout check with 0 errors. Passing the import says nothing about
   fidelity; the room and door metrics are the measure of reading.

**Where reading itself falls short:**

- **Two-storey images** (6276, 5023): CubiCasa5K draws both floors side by side; Claude read both of 6276 (27
  rooms, F1 0.58) but only one of 5023 (F1 0.24). VESPER's import is one storey: a two-storey image should be
  refused or one floor chosen; today both floors become one "floor" with no connection.
- **Open-plan spaces**: CubiCasa5K splits an open living room, kitchen and hall into separate spaces; Claude often
  reads one room (12030: 4 rooms for 6, F1 0.40, per-room area error median 33%; 13019 one extra).
- **Doors** are the weakest part: 48% found within 0.5 m and 46% of read doors near a true one. The two-storey
  images account for much of it (9 of 58 found); on the other 8 plans 50 of 64 (78%). Part of the unmatched read
  doors are likely openings between open-plan spaces, which the prompt asks for and the annotation does not draw as
  doors (not yet counted).
- **Types**: the 17 type disagreements are mostly the annotation's: 13 are true rooms of class Room, Undefined or
  UserDefined (mapped to "other") that Claude named from the plan's label, e.g. AH (asuinhuone, a habitable room)
  as bedroom, ET (eteinen) as hall, SAUNA as bathroom. On rooms whose class names a use, 70 of 74 types are right; the
  4 wrong are 5023's (Bath read as "Room 05"/"Room 06", a fuel store as a service room).
- **Scale**: where the image prints room areas or dimensions Claude read a scale (6 of 10) within 1 to 12%. The
  printed areas are of habitable rooms and the plan's total includes balconies, so `{"area_m2": ...}` scales are
  the less exact ones (2090: 10%, 1590: 12%).

## Estimate for 50 plans

At this round's rates, 40 more readings take about 2.3 h of wall time (mean 209 s each; 1.2 to 5.5 h at the
extremes of 65 to 498 s) and about **$18 notional** (mean $0.44; $7 to $38 at the extremes), one plan at a time, on
the default model. `vesper validate plans --n 50 --seed 0 --fetch` reuses these 10 readings (the sample of 10 is the
start of the sample of 50) and fetches 80 more files (~10 MB) by range.
