"""HSSD scenes as Lab homes (spec §2, plan W1a): which scenes are here and ready, and a new home from one.

    vesper lab scenes [--hssd DIR]
    vesper lab new myhome --scene 102344022 [--hssd DIR] [--habitat-python PATH]

A scene is ready when every file Habitat loads for its articulated scene instance is on this machine, and HSSD's
object metadata too: HSSD is a gated download, often taken scene by scene, and a half-downloaded file is named, not
a traceback. A new home runs the world service in the Habitat environment (`python -m vesper_world export`), which
writes the scene's map file and its openings, each a door or a window as HSSD's metadata says. A door standing in a
doorway becomes that doorway's door, closed; a door in no doorway is named. A storeyed scene is its ground floor,
and the rooms upstairs are named as left out. The player starts in the entryway or the living room if the scene has
one with floor in it, else in the largest indoor room.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import vesper_world
from vesper.lab.geometry import distance
from vesper.lab.worldmap import WorldMap
from vesper_world.export import KINDS_CSV, OUTDOOR_LABELS, ground_floor, on_floor, storeys

HSSD_ENV, PYTHON_ENV = "VESPER_HSSD", "VESPER_HABITAT_PYTHON"
DEFAULT_HSSD = Path("data/scene_datasets/hssd-hab")  # relative to the working directory, as in the repository
DEFAULT_PYTHON = Path.home() / "miniconda3" / "envs" / "vesper" / "bin" / "python"
WORLD_PACKAGE = Path(vesper_world.__file__).resolve().parent  # run in the Habitat environment, on its own
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
    storeys: int = 1  # rooms, labels and area are the ground floor's

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
    metadata = [] if (hssd / KINDS_CSV).is_file() else [KINDS_CSV]
    out = []
    for path in instances:
        sid = path.name.removesuffix(".scene_instance.json")
        semantics = hssd / "semantics" / "scenes" / f"{sid}.semantic_config.json"
        if not semantics.is_file():
            continue  # no region annotations: the rooms would be unknown
        config = _read(semantics)
        if config is None:
            out.append(SceneInfo(sid, 0, [], 0.0, [f"{_rel(semantics, hssd)} (unreadable)"]))
            continue
        regions = config.get("region_annotations") or []
        if not regions:
            continue
        ground = on_floor(regions, ground_floor(regions))[0]
        indoor = sum(_area(r["poly_loop"]) for r in ground if r["label"] not in OUTDOOR_LABELS)
        instance = _read(path)
        missing = (_missing(hssd, instance, objects) if instance is not None
                   else [f"{_rel(path, hssd)} (unreadable)"])
        out.append(SceneInfo(sid, len(ground), sorted({r["label"] for r in ground}), round(indoor, 1),
                             sorted(metadata + missing), len(storeys(regions))))
    return sorted(out, key=lambda s: (not s.ready, s.id))


def scene_info(hssd: Path, scene: str) -> SceneInfo:
    for s in list_scenes(hssd):
        if s.id == scene:
            return s
    raise SceneError(f"scene {scene!r} is not in {hssd} (vesper lab scenes lists those that are)")


def export_scene(hssd: Path, scene: str, out: Path, python: Path, run=None) -> None:
    """Run the world service's export in the Habitat environment: `out` gets map.json, openings.json and
    render.json."""
    run_world(python, ["export", "--hssd", str(Path(hssd).resolve()), "--scene", scene, "--out",
                       str(Path(out).resolve())], f"scene {scene}", run)


def run_world(python: Path, args: list, what: str, run=None):
    """`python -m vesper_world ARGS` in the Habitat environment; a SceneError that says why when it fails.

    The Habitat environment sees a copy of `vesper_world` and nothing else of VESPER's: VESPER's own packages (a
    newer numpy, say) on its path would shadow Habitat's. It runs in that copy's directory, where nothing the user
    keeps in theirs can shadow it either."""
    import shutil
    import subprocess
    import tempfile
    if not Path(python).is_file():
        raise SceneError(f"the Habitat environment's Python is not at {python}; give it with --habitat-python or "
                         f"{PYTHON_ENV} (the environment with habitat-sim 0.3.3)")
    with tempfile.TemporaryDirectory(prefix="vesper-world-") as path:
        shutil.copytree(WORLD_PACKAGE, Path(path) / "vesper_world", ignore=shutil.ignore_patterns("__pycache__"))
        env = {**os.environ, "PYTHONPATH": path}
        try:
            r = (run or subprocess.run)([str(python), "-s", "-m", "vesper_world", *args], env=env, cwd=path,
                                        capture_output=True, text=True, timeout=EXPORT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise SceneError(f"the world service did not finish {what} within {EXPORT_TIMEOUT_S} s") from None
    if r.returncode != 0:
        raise SceneError(f"{_why(r)} ({what})")
    return r


def _why(r) -> str:
    """What a failed export said: its FAILED line, else the last of what it printed."""
    import signal
    lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
    failed = [ln.removeprefix("FAILED:").strip() for ln in lines if ln.startswith("FAILED:")]
    if failed and failed[-1]:
        return failed[-1]
    try:
        how = (f"was stopped by {signal.Signals(-r.returncode).name}" if r.returncode < 0
               else f"exited with {r.returncode}")
    except ValueError:
        how = f"exited with {r.returncode}"
    said = " / ".join(ln[:200] for ln in lines[-3:])
    return f"the world service {how}: {said}" if said else f"the world service {how} and said nothing"


def read_openings(path: Path) -> dict:
    """The world service's openings.json."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SceneError(f"the world service wrote an unreadable openings.json ({e})") from None
    if not isinstance(d, dict) or not isinstance(d.get("openings"), list):
        raise SceneError("the world service wrote an openings.json without its openings")
    return d


def doorway_of(world_map: WorldMap, opening: dict) -> str | None:
    """The doorway an opening stands in: the one whose centre is within DOOR_SNAP_M."""
    if not world_map.doorways:
        return None
    nearest = min(world_map.doorways, key=lambda d: distance(d.center, (opening["x"], opening["z"])))
    return nearest.id if distance(nearest.center, (opening["x"], opening["z"])) <= DOOR_SNAP_M else None


def doors_from_openings(world_map: WorldMap, openings: list[dict]) -> list[str]:
    """The doorways that have a door: a door (not a window, whatever its height) standing in it."""
    doors = []
    for o in openings:
        doorway = doorway_of(world_map, o) if o.get("kind") == "door" else None
        if doorway and doorway not in doors:
            doors.append(doorway)
    return doors


def stray_doors(world_map: WorldMap, openings: list[dict]) -> list[str]:
    """The scene's doors that stand in no doorway of the map: the home leaves them out, and says so."""
    return [o["handle"] for o in openings if o.get("kind") == "door" and doorway_of(world_map, o) is None]


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


def _read(path: Path):
    """A JSON file's contents, or None for one that is half-downloaded or otherwise unreadable."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _rel(path: Path, hssd: Path) -> str:
    return Path(os.path.relpath(path, hssd)).as_posix()


def _area(loop) -> float:
    pts = [(p[0], p[2]) for p in loop]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def _object_configs(hssd: Path) -> dict[str, Path]:
    return {p.name.removesuffix(".object_config.json"): p for p in (hssd / "objects").rglob("*.object_config.json")}


def _object_path(hssd: Path, name: str) -> Path:
    """Where HSSD keeps an object's config: openings, decomposed parts, or by the first character of its id."""
    if re.match(r"^\d+-\d+$", name):
        return hssd / "objects" / "openings" / f"{name}.object_config.json"
    if "_part_" in name:
        return hssd / "objects" / "decomposed" / name.split("_part_")[0] / f"{name}.object_config.json"
    return hssd / "objects" / name[0] / f"{name}.object_config.json"


def _missing(hssd: Path, instance: dict, objects: dict[str, Path]) -> list[str]:
    """The files this scene instance needs that are not here, as Habitat loads them: the stage's and each
    object's config with its render and collision assets, and each articulated object's config, URDF and meshes."""
    missing: set[str] = set()

    def need(config: Path) -> dict | None:
        if not config.is_file():
            missing.add(_rel(config, hssd))
            return None
        d = _read(config)
        if d is None:
            missing.add(f"{_rel(config, hssd)} (unreadable)")
        return d

    def assets(config: Path) -> None:
        d = need(config)
        for key in ("render_asset", "collision_asset"):
            asset = (d or {}).get(key)
            if asset and not (config.parent / asset).is_file():
                missing.add(_rel(config.parent / asset, hssd))
    stage = (instance.get("stage_instance") or {}).get("template_name")
    if stage:
        assets(hssd / f"{stage}.stage_config.json")
    for o in instance.get("object_instances") or []:
        name = o["template_name"].split("/")[-1]
        assets(objects.get(name) or _object_path(hssd, name))
    for a in instance.get("articulated_object_instances") or []:
        name = a["template_name"].split("/")[-1]
        folder = hssd / "urdf" / name
        config = need(folder / f"{name}.ao_config.json")
        urdf = folder / ((config or {}).get("urdf_filepath") or f"{name}.urdf")
        if not urdf.is_file():
            missing.add(_rel(urdf, hssd))
            continue
        for mesh in sorted(set(re.findall(r'filename="([^"]+)"', urdf.read_text(errors="replace")))):
            if not (folder / mesh).is_file():
                missing.add(_rel(folder / mesh, hssd))
    return sorted(missing)
