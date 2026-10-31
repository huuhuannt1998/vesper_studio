# W9b: HSSD Scenes to Blender and Back, as built

> Written after a test-driven build (as plans W7a2, W4a1 and W9): each task names its files, the interfaces it
> produces and the tests that drove it.

**Goal:** an HSSD home edited in Blender (by hand, or by Claude through Blender MCP) and played in VESPER, its walkable
floor following the edited furniture; the same walkability for any dressed home.

**Architecture:** `vesper/lab/ktx2png.mjs` converts HSSD's models for Blender with the vendored Basis transcoder;
`vesper/lab/dress.py` prepares them (`prepare_models`, cached) and writes a build script that imports each where
Habitat puts it; `vesper/lab/play.py` and `ui/view3d.js` draw `scene.glb` with HSSD's doors; the world service's
`renav` action (`vesper_world`) takes the walkable floor again from `scene.glb`, and `vesper lab renav` installs it.

**Tech stack:** Python 3.11+, Node 18+, Blender 4.2+ (5.2.3 tested), habitat-sim 0.3.3 (Python 3.9).

**Spec:** `docs/superpowers/specs/2026-10-31-vesper-home-generator-design.md` §5.

## Global Constraints

- Nothing derived from HSSD in a repository: converted models in `~/.cache/vesper` (`VESPER_CACHE`), homes outside.
- The map stays the walking truth; `renav` changes only the walkable cells, on the same grid, rooms and doorways kept.
- Shared Mac: Node, Blender and Habitat at background priority, at most 2 cores, under 4 GB.

## Review Focus

1. A rerun of the build script on an edited HSSD scene: nothing re-imported, the edits kept.
2. A model exported with other options than the brief's (Z up, no modifiers): misplaced or ignored by renav.
3. `renav` on a scene whose floor falls apart (a doorway blocked): the home refused, the old map kept.
4. HSSD scenes with storeys (floor_y not 0).
5. A KTX2 texture the transcoder cannot read.

### Task 1: models for Blender

**Files:** create `vesper/lab/ktx2png.mjs`, `tests/lab/ui/ktx2png.test.mjs`.
**Produces:** `convertGlb(buf, transcode)`, `encodePng`, `readGlb`, `basisTranscoder()`; `node ktx2png.mjs IN OUT |
--batch`. **Tests:** PNG rows; a KTX2 image becomes PNG and the rest stays byte for byte; no KTX2, no change; a real
HSSD model (when HSSD is here).

### Task 2: the HSSD build script

**Files:** modify `vesper/lab/dress.py` (`prepare_models`, `model_cache`, `DressError`, `scene_data(home, world3d,
models)`, `dress(..., world3d, cache, run)`, `import_hssd`, `as_matrix`, `_hssd_brief`), `vesper/cli.py`
(`_lab_dress`); create `tests/lab/test_dress_hssd.py`; extend `tests/lab/fake_bpy.py` (an importer, parents).
**Tests:** each model converted once, cached; the scene data; each model at C·M·C⁻¹ in its room's collection, doors
(in a doorway or not) apart; a rerun keeps edits; export leaves the doors out; the command; in a real Blender
(`VESPER_BLENDER`): the chair's translation and quarter turn survive import and export.

### Task 3: playing the edited scene

**Files:** modify `vesper/lab/play.py` (`dressed` with or without `world3d`), `vesper/lab/ui/view3d.js`
(`loadScene(keep)`, `loadDressed` solid for HSSD, fallback to HSSD's meshes). **Tests:** the hello; headless looks
of the round-tripped HSSD home and the MCP-furnished home (shots read).

### Task 4: the map follows the furniture

**Files:** modify `vesper_world/export.py` (`renav_map`; `largest_part` with no floor), `vesper_world/habitat_scene.py`
(`load_glb`, a +Y-up stage config), `vesper_world/__main__.py` (`renav`), `vesper/cli.py` (`vesper lab renav`),
`vesper/lab/dress.py` (doorway thresholds, floors facing up). **Tests:** the grid and rooms kept, the floor from the
navmesh, the largest part; no floor refused; the action; the command (the original kept once, a home that does not
load refused); thresholds; floors facing up. Live: the HSSD round trip and the furnished home (`~/vesper-lab/w9/
eval-w9b.md`).

## Rulings

- Textures are converted with the vendored Basis transcoder in Node (no new dependency) rather than a KTX tool.
- An HSSD rerun never re-imports (its models are what is edited); to start over, delete the collections.
- Every door (`opening: door`) is left out of the export, as the map's own export leaves them out of the navmesh;
  doors in no doorway included cost 0.026 IoU on the round trip.
- `renav` keeps the old grid (same cells, same doorways) rather than the new navmesh's bounds.
- Reported, not fixed: Blender MCP's code tool writes files outside Claude Code's permission model.
- The plan is as-built (process).

## After the final review

No Critical, 3 Important, all fixed test-first, and two minors raised to Important by their effect:
- `renav` refuses a floor that closes a doorway, leaves a room without floor, or falls below a quarter of the old
  floor (`renav_problems`); the map stays byte for byte, and the refusal is tested.
- Starting over works: the import is gated on the models still in the collections, not on any in `bpy.data`.
- A converted model is trusted only when its GLB header's length is the file's (a killed conversion is redone).
- `**/dress/` and `**/map.original.json` are gitignored (HSSD-derived for HSSD homes).

Deferred minors: stray doors missing from a dressed HSSD view; renav's temporary stage config not removed; the cache
not keyed by the converter's version; one bad model aborts the batch without its name; `.gltf` assets, images by uri
and second buffers not handled; a bad render.json gives a traceback in renav; a map shared between homes; a failed
import half-way stays partial; upper-storey furniture grouped by ground-floor rooms.
