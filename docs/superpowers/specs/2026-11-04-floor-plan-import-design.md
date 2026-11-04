# Importing a floor-plan image (design, 2026-11-04)

Requirement (PI, verbatim): "Users can import a 2D layout of a house and vesper should be able to generate a
complete 3d and reasonable virtual house." PI choices: an image of a floor plan (PNG/JPG/PDF, a listing or a scan);
built now, in parallel with the paper's differential-testing work.

## What the user does

    vesper lab import plan.png homes/mine --scale "kitchen width 3.2 m"      # or --area 95 (m², the whole home)
    vesper lab import plan.png homes/mine --review                          # opens the plan with what was read

or, in the lobby, **Import a plan**: drop the image, check the rooms VESPER read over it (names, types, the scale),
then Generate. The result is an ordinary VESPER home: walls with windows and doors, furniture, the devices picked,
the 3D view, a site (place and date) if given, and the layout check run on it.

## How

1. **Read the image (Claude's vision).** `claude -p` (headless Claude Code, the same way VESPER's Blender furnishing
   calls Claude, with the user's own login) gets the image and a strict JSON schema, `vesper-plan/1`: each room's
   name, type and outline as a polygon in image pixels; each door (position, width, the two rooms or room and
   outside); each window (position, width, its room); the entrance; and any dimension text or scale bar it can read.
   The answer must validate against the schema; one retry with the validation errors, then a clear failure.
   A `vesper-plan/1` file can also be written by hand or edited, and imported with no Claude call (offline).
2. **Rectify (deterministic, no AI).** Snap walls to the plan's two dominant directions, merge near-collinear edges,
   close gaps narrower than a wall, remove overlaps between rooms (the smaller room keeps the contested strip), move
   each door onto the wall the two rooms share, and turn pixels into metres from the scale (a stated length, the
   home's area, or a scale bar Claude read; refused with a clear message when there is none).
3. **Check it is a house.** Every room reachable from the entrance through doors; each room's area within its type's
   plausible range (the generator's AREAS, widened); ceiling 2.5 m. Failures are listed by room; `--review` shows
   them on the image.
4. **Build.** A `WorldMap` from the rectified plan (rooms, walkable floor, doorways, a porch at the entrance), the
   plan's windows in `windows.json` (rooms with none on an outside wall get windows by rule, marked so), then the
   existing pipeline: devices (`place_devices`), built-in furniture or Claude + Blender, the layout checker, the 3D
   view, the environment.

## Evaluation

On a sample of 50 floor plans from CubiCasa5K (annotated plans with ground-truth room polygons; CC BY-NC 4.0, kept
local, used only to measure): rooms found (F1 at IoU >= 0.5), room types right, doors found, area error after
scaling, and the share of imports that pass the layout check with no error. Reported in the docs.

## Non-goals and limits

- One storey; curved walls become straight segments; furniture drawn on the plan is ignored (VESPER furnishes).
- The image goes to Anthropic through the user's Claude Code session; the README says so, and a hand-written
  `vesper-plan/1` file needs no network.
- An unreadable plan fails with what was wrong; VESPER never invents a layout the image does not show.

## Files

`vesper/lab/plan.py` (the `vesper-plan/1` schema, validation, rectification, scaling), `vesper/lab/plan_read.py`
(the Claude call), `vesper/lab/generate.py` (a map from a plan beside `floor_plan`), `vesper/cli.py`
(`vesper lab import`), the lobby's import panel (`vesper/lab/ui/compose.*`), tests under `tests/lab/`.
