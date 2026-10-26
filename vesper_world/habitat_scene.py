"""An HSSD scene loaded in Habitat, reduced to what the export needs (spec §3, plans W1a and W1b). Habitat is
imported only here, and only when a scene is loaded, so the rest of the package runs without it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from vesper_world.export import (IDENTITY_ROWS, KINDS_CSV, ExportError, flat, ground_floor, is_opening, matmul,
                                 opening_kind, opening_kinds, trs, urdf_visuals)

AGENT_HEIGHT_M = 1.6
AGENT_RADIUS_M = 0.2  # the walkable floor keeps this far from walls and furniture
SNAP_M = 0.5  # how far above or below the floor's height a navmesh point may be and still count


@dataclass
class Scene:
    scene: str
    floor_y: float
    bounds: tuple
    regions: list
    openings: list = field(default_factory=list)
    render: list = field(default_factory=list)  # the meshes the scene draws (export.scene_render)
    hssd: str = ""
    _pathfinder: object = None

    def navigable(self, x: float, y: float, z: float) -> bool:
        import numpy as np
        return bool(self._pathfinder.is_navigable(np.array([x, y, z], dtype=np.float32), SNAP_M))


def load_scene(hssd: str, scene: str, radius: float = AGENT_RADIUS_M) -> Scene:
    root = Path(hssd)
    config = root / "hssd-hab-articulated.scene_dataset_config.json"
    semantics = root / "semantics" / "scenes" / f"{scene}.semantic_config.json"
    if not config.is_file():
        raise ExportError(f"{root} is not an HSSD dataset (no {config.name})")
    if not (root / "scenes-articulated" / f"{scene}.scene_instance.json").is_file():
        raise ExportError(f"scene {scene!r} is not in {root} (vesper lab scenes lists those that are)")
    if not semantics.is_file():
        raise ExportError(f"scene {scene!r} has no region annotations ({semantics.name}): its rooms are unknown")
    regions = json.loads(semantics.read_text()).get("region_annotations") or []
    if not regions:
        raise ExportError(f"scene {scene!r}: its region annotations are empty")
    if not (root / KINDS_CSV).is_file():
        raise ExportError(f"{root} has no {KINDS_CSV}: without it doors and windows cannot be told apart "
                          f"(download HSSD's metadata folder)")
    kinds = opening_kinds(root / KINDS_CSV)
    import habitat_sim as hs
    cfg = hs.SimulatorConfiguration()
    cfg.scene_dataset_config_file = str(config)
    cfg.scene_id = scene
    cfg.enable_physics = True
    cfg.create_renderer = False
    agent = hs.agent.AgentConfiguration()
    agent.sensor_specifications = []
    sim = hs.Simulator(hs.Configuration(cfg, [agent]))
    try:
        objects = sim.get_rigid_object_manager()
        render = _render_items(sim, objects, kinds, root)  # before the doors leave the scene
        openings = []
        for handle in [h for h in objects.get_object_handles() if is_opening(h)]:
            t = objects.get_object_by_handle(handle).translation
            kind = opening_kind(handle, kinds) or "unknown"
            openings.append({"handle": handle, "kind": kind, "x": round(float(t[0]), 4), "y": round(float(t[1]), 4),
                             "z": round(float(t[2]), 4)})
            if kind == "door":  # a window stays: it is wall, whatever its height
                objects.remove_object_by_handle(handle)
        settings = hs.NavMeshSettings()
        settings.set_defaults()
        settings.agent_radius, settings.agent_height = radius, AGENT_HEIGHT_M
        settings.include_static_objects = True
        if not sim.recompute_navmesh(sim.pathfinder, settings):
            raise ExportError(f"scene {scene!r}: Habitat could not build its navmesh")
        lo, hi = sim.pathfinder.get_bounds()
        bounds = ((float(lo[0]), float(lo[1]), float(lo[2])), (float(hi[0]), float(hi[1]), float(hi[2])))
        floor_y = ground_floor(regions)
        loaded = Scene(scene, floor_y, bounds, regions, openings, render, str(root.resolve()))
        loaded._pathfinder = sim.pathfinder
        loaded._sim = sim  # kept alive while the pathfinder is used
        return loaded
    except BaseException:
        sim.close()
        raise


def _rows(node) -> list:
    """A scene node's world matrix, row by row (Magnum's Matrix4 is indexed column first)."""
    m = node.absolute_transformation()
    return [[float(m[c][r]) for c in range(4)] for r in range(4)]


def _relative(path, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def _render_items(sim, objects, kinds: dict, root: Path) -> list:
    """Every mesh the scene draws, with the world matrix Habitat gives it: the stage, each object (its first visual
    node is its glTF's root), and each articulated object's link meshes (the link's frame, then the URDF's origin)."""
    stage = sim.get_stage_initialization_template()
    items = [{"kind": "stage", "asset": _relative(stage.render_asset_fullpath, root), "matrix": flat(IDENTITY_ROWS)}]
    for handle in objects.get_object_handles():
        o = objects.get_object_by_handle(handle)
        item = {"kind": "object", "asset": _relative(o.creation_attributes.render_asset_fullpath, root),
                "matrix": flat(_rows(o.visual_scene_nodes[0]))}
        if is_opening(handle):
            item.update(handle=handle, opening=opening_kind(handle, kinds) or "unknown")
        items.append(item)
    articulated = sim.get_articulated_object_manager()
    for handle in articulated.get_object_handles():
        ao = articulated.get_object_by_handle(handle)
        urdf = Path(ao.creation_attributes.urdf_fullpath)
        visuals = urdf_visuals(urdf.read_text())
        for link in [-1, *ao.get_link_ids()]:
            frame = _rows(ao.get_link_scene_node(link))
            for mesh, xyz, rpy, scale in visuals.get(ao.get_link_name(link), []):
                items.append({"kind": "part", "asset": _relative(urdf.parent / mesh, root),
                              "matrix": flat(matmul(frame, trs(xyz, rpy, scale)))})
    return items
