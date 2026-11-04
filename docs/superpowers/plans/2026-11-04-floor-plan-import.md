# Floor-Plan Import (part A: images) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vesper lab import plan.png DIR` turns a floor-plan image into a complete VESPER home (map, windows,
devices, furniture, layout check), through a checkable `vesper-plan/1` file that can also be written by hand.

**Architecture:** Claude (headless `claude -p`) reads the image into `vesper-plan/1` JSON (pixels); deterministic code
validates, rectifies and scales it to metres and checks it is a reachable, plausible house; the generator builds a
`WorldMap` from it beside `floor_plan`, then the existing pipeline (devices, windows, furniture, layout check) runs.

**Tech Stack:** Python 3.12, VESPER (`vesper.lab.generate`, `worldmap`, `windows`, `furnishing`, `layoutcheck`), the
Claude Code CLI, vanilla JS for the lobby panel.

**Spec:** `docs/superpowers/specs/2026-11-04-floor-plan-import-design.md` (part A, PI approved 2026-11-04)

## Global Constraints

- `vesper-plan/1` (exact keys): `{"format": "vesper-plan/1", "units": "px" | "m", "scale": {"m_per_px": float} |
  {"length_m": float, "from": [x, y], "to": [x, y]} | {"area_m2": float} | null, "rooms": [{"id": str, "name": str,
  "type": one of generate.AREAS keys or "corridor"|"hall"|"other", "polygon": [[x, y], ...]}], "doors": [{"at": [x,
  y], "width": float, "rooms": [room id, room id | "outside"]}], "windows": [{"at": [x, y], "width": float, "room":
  room id}], "entrance": door index}`. Image y grows downward; plan z = image y.
- Rectification: snap to the two dominant edge directions (histogram of edge angles, 1° bins); merge collinear edges
  within 0.15 m; close gaps narrower than 0.25 m; where two rooms overlap, the smaller room keeps the contested
  strip (spec); doors snap to the nearest shared wall within 0.5 m.
- Plausibility: a room's area within [0.5 x min, 1.5 x max] of `generate.AREAS[type]` (types without an entry: 2 to
  60 m²); every room reachable from the entrance; refusals list every failing room.
- Claude call: `claude -p` with the image attached, `--output-format json`, the schema in the prompt; one retry with
  the validation errors; the CLI never runs Claude for a `.json` input. The README states that the image goes to
  Anthropic through the user's Claude Code session.
- No commit of any CubiCasa5K file (CC BY-NC 4.0): evaluation data stays in `.vesper/datasets/` (git-ignored).
- Never push without the PI's OK. Worktree agents first run `git merge --ff-only plan-w4b` in their worktree.

## Review Focus

- A plan with no scale and no readable dimension: refused with "no scale: give --scale or --area", never guessed
  (Task A1 test).
- A room polygon that is not closed or self-intersects: rejected with the room's id (Task A1 test).
- A door between two rooms that do not touch: moved to the nearest shared wall, or rejected naming both rooms
  (Task A1 test).
- An image Claude cannot read (a photo of a cat): the reader fails with Claude's stated reason after one retry, no
  home is written (Task A3 test with a fake runner).
- A plan in metres (`units: "m"`, hand-written): imported with no scale argument (Task A2 test).

---

### Task A1: `vesper-plan/1`: validation, rectification, scaling, house checks

**Files:** Create `vesper/lab/plan.py`; Test `tests/lab/test_plan.py`

**Interfaces:**
- Produces: `PlanError(ValueError)`; `load_plan(raw: dict) -> dict` (validated, unchanged); `scale_plan(plan,
  scale: str | None = None, area_m2: float | None = None) -> dict` (units "m"; `scale` strings like "kitchen width
  3.2 m" resolve against the named room's bounding box width); `rectify(plan_m: dict) -> dict`; `check_house(plan_m:
  dict) -> list[str]` (problems, empty when it is a house).

- [ ] **Step 1: Failing tests:** a two-room plan in pixels with `{"area_m2": 30}` scales to 30 m² total within 1 %;
  "kitchen width 3.2 m" sets the kitchen's width to 3.2 m; no scale -> PlanError "no scale: give --scale or --area";
  an open polygon -> PlanError naming the room; edges at 1.5° snap to axis; two rooms overlapping by 0.2 m: the smaller
  keeps the strip; a door 0.3 m off the shared wall moves onto it; a door between rooms 2 m apart -> PlanError naming
  both; an unreachable room is listed by `check_house`; a 2 m² living room is listed as implausible.
- [ ] **Step 2: Run, fail. Step 3: implement. Step 4: pass (`tests/lab/test_plan.py`). Step 5: commit.**

### Task A2: A home from a plan, and `vesper lab import FILE.json`

**Files:** Modify `vesper/lab/generate.py` (add `floor_plan_from(plan_m: dict, scene: str) -> WorldMap` and
`generate_from_plan(plan_m, request: Request, catalog, out: Path) -> None`), `vesper/lab/windows.py` (windows from a
plan's windows: `from_plan(plan_m, world_map) -> list[dict]`, rooms with none on an outside wall get rule windows
marked `"source": "rule"`), `vesper/cli.py` (`vesper lab import SRC DIR [--scale TEXT] [--area M2] [--request YAML]
[--catalog]`); Test `tests/lab/test_import.py`

**Interfaces:**
- Consumes: Task A1's `load_plan`, `scale_plan`, `rectify`, `check_house`.
- Produces: the CLI above; a home directory identical in layout to `vesper lab generate`'s (home.yaml, map.json,
  windows.json, furniture.json), its `scene: imported/<file stem>`, and a header comment saying it was imported.

- [ ] **Step 1: Failing tests:** a hand-written three-room plan in metres imports with no scale; the home loads
  (`load_lab_home`), every room is reachable on the walking map, the plan's two windows are in windows.json with
  `"source": "plan"`, a windowless outside room got a rule window; `vesper lab layout` on it has 0 errors; a plan
  failing `check_house` writes nothing and exits 1 listing the problems.
- [ ] **Step 2-4: run, implement, pass (`tests/lab/test_import.py tests/lab/test_generate*.py`). Step 5: commit.**

### Task A3: Reading an image with Claude

**Files:** Create `vesper/lab/plan_read.py`; Modify `vesper/cli.py` (image paths: `.png .jpg .jpeg .pdf` go through
the reader); `README.md` (a short "Importing a floor plan" section with the privacy sentence); Test
`tests/lab/test_plan_read.py`

**Interfaces:**
- Produces: `read_plan_image(path: Path, runner=None) -> dict` (a validated `vesper-plan/1` in pixels; `runner(args:
  list[str]) -> str` defaults to running the `claude` CLI and returning its stdout); `PROMPT` (the instructions and
  the schema; asks for every room, door, window, the entrance and any dimension text, and to answer only JSON).

- [ ] **Step 1: Failing tests** with a fake runner: a valid answer returns the plan; an invalid first answer and a
  valid second one returns the second (the retry carries the errors); two invalid answers raise PlanError with
  Claude's last text; the args include the image path and `--output-format json`.
- [ ] **Step 2-4: run, implement, pass. Step 5: commit.** Then a live try on one public floor-plan image of the
  implementer's choice (not committed), result described in the report.

### Task A4: The lobby's import panel

**Files:** Modify `vesper/lab/compose.py` (an `/import` POST taking the image, returning the read plan and problems),
`vesper/lab/ui/compose.html`, `vesper/lab/ui/compose.js` (an **Import a plan** section: drop an image, see the rooms
read over it with their names and types editable, the scale field, the problems list, then Generate); Test
`tests/lab/ui/compose.test.mjs`, `tests/lab/test_compose.py`

- [ ] **Step 1: Failing tests:** the overlay draws one outline per room in image coordinates; editing a room's type
  changes the request sent; `/import` with a fake reader returns plan and problems. **Step 2-4: run, implement,
  pass. Step 5: commit.**

### Task A5: Evaluation on CubiCasa5K

**Files:** Create `vesper/validation/plans.py`, `docs/experiments/results-plan-import.md`; Test
`tests/validation/test_plans.py`

- [ ] **Step 1:** Download CubiCasa5K's test split to `.vesper/datasets/cubicasa5k/` (record its URL, licence and
  checksum in the results doc). **Step 2: failing tests** for the metrics on a hand-made ground truth: room F1 at
  IoU >= 0.5, type accuracy, door recall, area error. **Step 3-4: implement, pass.** **Step 5:** run the reader on 50
  test plans (seeded choice), write the metrics and the share passing `check_house` and the layout check; commit code
  and the doc (no dataset files).
