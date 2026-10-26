"""HSSD scenes as Lab homes (spec §2, plan W1a): which scenes are here and ready, and a new home from one.

    vesper lab scenes [--hssd DIR]
    vesper lab new myhome --scene 102344022 [--hssd DIR] [--habitat-python PATH]

A scene is ready when every file its articulated scene instance needs is on this machine: HSSD is a gated
download, often taken scene by scene. A new home runs the world service in the Habitat environment
(`python -m vesper_world export`), which writes the scene's map file and its door openings. The openings that
stand in a doorway become the home's doors, closed; the others are windows. The player starts in the entryway or
the living room if the scene has one with floor in it, else in the largest indoor room.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import vesper
from vesper.lab.geometry import distance
from vesper.lab.worldmap import WorldMap

HSSD_ENV, PYTHON_ENV = "VESPER_HSSD", "VESPER_HABITAT_PYTHON"
DEFAULT_HSSD = Path("data/scene_datasets/hssd-hab")  # relative to the working directory, as in the repository
DEFAULT_PYTHON = Path.home() / "miniconda3" / "envs" / "vesper" / "bin" / "python"
WORLD_ROOT = Path(vesper.__file__).resolve().parent.parent  # where the `vesper_world` package lives
DOOR_SNAP_M = 0.5  # an opening this close to a doorway's centre is that doorway's door
START_LABELS = ("entryway", "foyer", "lobby", "living room", "familyroom", "lounge", "hall")
EXPORT_TIMEOUT_S = 600


class SceneError(RuntimeError):
    """A scene VESPER cannot make a home from; the message says why and what to do."""


@dataclass
class SceneInfo:
    id: str
    rooms: int
    labels: list[str]
    indoor_m2: float
    missing: list[str] = field(default_factory=list)  # files the scene needs that are not here, relative to HSSD

    @property
    def ready(self) -> bool:
        return not self.missing


def hssd_dir(given: str | None) -> Path:
    return Path(given or os.environ.get(HSSD_ENV) or DEFAULT_HSSD)


def habitat_python(given: str | None) -> Path:
    return Path(given or os.environ.get(PYTHON_ENV) or DEFAULT_PYTHON)


def list_scenes(hssd: Path) -> list[SceneInfo]:
    """The scenes with an articulated instance and region annotations here, the ready ones first."""
    hssd = Path(hssd)
    instances = sorted((hssd / "scenes-articulated").glob("*.scene_instance.json"))
    if not instances:
        raise SceneError(f"{hssd} holds no HSSD scenes (scenes-articulated/*.scene_instance.json); set "
                         f"--hssd or {HSSD_ENV}")
    objects = _object_configs(hssd)
    out = []
    for path in instances:
        sid = path.name.removesuffix(".scene_instance.json")
        regions = _regions(hssd, sid)
        if regions is None:
            continue  # no region annotations: the rooms would be unknown
        indoor = sum(_area(r["poly_loop"]) for r in regions if r["label"] not in _outdoor_labels())
        out.append(SceneInfo(sid, len(regions), sorted({r["label"] for r in regions}), round(indoor, 1),
                             _missing(hssd, json.loads(path.read_text()), objects)))
    return sorted(out, key=lambda s: (not s.ready, s.id))


def scene_info(hssd: Path, scene: str) -> SceneInfo:
    for s in list_scenes(hssd):
        if s.id == scene:
            return s
    raise SceneError(f"scene {scene!r} is not in {hssd} (vesper lab scenes lists those that are)")


def export_scene(hssd: Path, scene: str, out: Path, python: Path, run=None) -> None:
    """Run the world service's export in the Habitat environment: `out` gets map.json and openings.json."""
    import subprocess
    if not Path(python).is_file():
        raise SceneError(f"the Habitat environment's Python is not at {python}; give it with --habitat-python or "
                         f"{PYTHON_ENV} (the environment with habitat-sim 0.3.3)")
    env = {**os.environ, "PYTHONPATH": str(WORLD_ROOT)}
    cmd = [str(python), "-s", "-m", "vesper_world", "export", "--hssd", str(Path(hssd).resolve()), "--scene", scene,
           "--out", str(out)]
    try:
        r = (run or subprocess.run)(cmd, env=env, capture_output=True, text=True, timeout=EXPORT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise SceneError(f"the world service did not export scene {scene} within {EXPORT_TIMEOUT_S} s") from None
    if r.returncode != 0:
        lines = [ln for ln in (r.stderr or "").splitlines() if ln.startswith("FAILED") or "Error" in ln]
        raise SceneError((lines[-1].removeprefix("FAILED: ") if lines else f"the world service exited with "
                          f"{r.returncode}") + f" (scene {scene})")


def doors_from_openings(world_map: WorldMap, openings: list[dict]) -> list[str]:
    """The doorways that have a door: an opening within DOOR_SNAP_M of the doorway's centre (windows are not)."""
    doors = []
    for o in openings:
        if not world_map.doorways:
            break
        nearest = min(world_map.doorways, key=lambda d: distance(d.center, (o["x"], o["z"])))
        if distance(nearest.center, (o["x"], o["z"])) <= DOOR_SNAP_M and nearest.id not in doors:
            doors.append(nearest.id)
    return doors


def start_room(world_map: WorldMap) -> str:
    """Where the player starts: an entryway or living room with floor in it, else the largest indoor room."""
    cells: dict[int, int] = {}
    for k in world_map.labels:
        if k >= 0:
            cells[k] = cells.get(k, 0) + 1
    with_floor = [(k, r) for k, r in enumerate(world_map.rooms) if cells.get(k)]
    indoor = [(k, r) for k, r in with_floor if not r.outdoor] or with_floor
    for word in START_LABELS:
        for _, r in indoor:
            if word in r.label.lower():
                return r.id
    if not indoor:
        raise SceneError(f"{world_map.scene}: no room has walkable floor in it")
    return max(indoor, key=lambda kr: cells[kr[0]])[1].id


def _regions(hssd: Path, scene: str) -> list | None:
    path = hssd / "semantics" / "scenes" / f"{scene}.semantic_config.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text()).get("region_annotations") or None


def _outdoor_labels() -> tuple[str, ...]:
    return ("outdoor", "porch/terrace/deck/driveway", "balcony")  # vesper_world.export.OUTDOOR_LABELS


def _area(loop) -> float:
    pts = [(p[0], p[2]) for p in loop]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def _object_configs(hssd: Path) -> dict[str, Path]:
    return {p.name.removesuffix(".object_config.json"): p for p in (hssd / "objects").rglob("*.object_config.json")}


def _missing(hssd: Path, instance: dict, objects: dict[str, Path]) -> list[str]:
    """The files this scene instance needs that are not here: the stage, each object's config and render asset,
    and each articulated object's URDF."""
    missing: set[str] = set()

    def need(config: Path) -> None:
        if not config.is_file():
            missing.add(str(config.relative_to(hssd)))
            return
        asset = json.loads(config.read_text()).get("render_asset")
        if asset and not (config.parent / asset).is_file():
            missing.add(str((config.parent / asset).relative_to(hssd)))
    stage = (instance.get("stage_instance") or {}).get("template_name")
    if stage:
        need(hssd / f"{stage}.stage_config.json")
    for o in instance.get("object_instances") or []:
        name = o["template_name"].split("/")[-1]
        need(objects.get(name, hssd / "objects" / f"{name}.object_config.json"))
    for a in instance.get("articulated_object_instances") or []:
        name = a["template_name"]
        if not (hssd / "urdf" / name / f"{name}.urdf").is_file():
            missing.add(f"urdf/{name}/{name}.urdf")
    return sorted(missing)
