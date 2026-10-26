"""The 3D view's data (spec §3, plan W1b): a home's render manifest, its doors, and the HSSD files it draws.

A home made from an HSSD scene holds `render.json` (`vesper-render/1`, written by the world service): every mesh
the scene draws, as a file inside HSSD and the world matrix Habitat gives it. The browser draws the scene from
those files, so the play server serves them, and only them: each asset must be a model file inside HSSD, and a
manifest that names anything else is refused whole. A door mesh standing in one of the home's doors' doorways is
that door, so the page swings it when the door opens. A home without a manifest is drawn from its map.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from vesper.lab.scenes import DEFAULT_HSSD, HSSD_ENV, SceneError, doorway_of

RENDER_FORMAT = "vesper-render/1"
MODEL_SUFFIXES = (".glb", ".gltf", ".bin", ".png", ".jpg", ".jpeg", ".ktx2")


@dataclass
class World3d:
    manifest: dict  # what the page gets: the items, a door's with its `doorway`
    root: Path  # the HSSD folder the assets are in
    assets: frozenset  # the only files the page may get, relative to root
    missing: list  # assets that are not in root
    floor_y: float


def load_world3d(home, hssd: Path | str | None = None) -> World3d | None:
    """The home's 3D scene, or None when it has no render manifest (it is drawn from its map). HSSD is `hssd`,
    else $VESPER_HSSD, else where the home was made, else the default."""
    path = home.path.parent / "render.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SceneError(f"cannot read {path}: {e}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != RENDER_FORMAT:
        raise SceneError(f"{path} is not a VESPER render manifest ({RENDER_FORMAT})")
    if manifest.get("scene") != home.scene:
        raise SceneError(f"{path} is for {manifest.get('scene')}, but the home is on {home.scene}")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise SceneError(f"{path}: no items")
    for k, item in enumerate(items, 1):
        problem = _problem(item)
        if problem:
            raise SceneError(f"render.json: item {k} {problem}")
    root = Path(hssd or os.environ.get(HSSD_ENV) or manifest.get("hssd") or DEFAULT_HSSD)
    doors = _door_handles(home, path.parent / "openings.json")
    page_items = [{**item, "doorway": doors[item["handle"]]} if item.get("handle") in doors else dict(item)
                  for item in items]
    assets = frozenset(item["asset"] for item in items)
    return World3d({"format": RENDER_FORMAT, "scene": home.scene, "items": page_items}, root, assets,
                   sorted(a for a in assets if not (root / a).is_file()), float(manifest.get("floor_y") or 0.0))


def _problem(item) -> str | None:
    if not isinstance(item, dict):
        return "is not an object"
    m = item.get("matrix")
    if not isinstance(m, list) or len(m) != 16 or not all(isinstance(v, int | float) for v in m):
        return "has no 4x4 matrix (16 numbers)"
    asset = item.get("asset")
    if not isinstance(asset, str) or not asset:
        return "has no asset"
    p = PurePosixPath(asset)
    if p.is_absolute() or ".." in p.parts or "\\" in asset or p.suffix.lower() not in MODEL_SUFFIXES:
        return f"names {asset!r}, which is not a model file inside HSSD"
    return None


def _door_handles(home, openings_path: Path) -> dict:
    """{door mesh handle: doorway} for the door meshes that stand in one of the home's doors' doorways."""
    try:
        openings = json.loads(openings_path.read_text()).get("openings") or []
    except (OSError, ValueError):
        return {}  # the doors stay closed meshes: still drawn, never swung
    doors = set(home.doors)  # doorway ids
    out = {}
    for o in openings:
        if isinstance(o, dict) and o.get("kind") == "door" and {"handle", "x", "z"} <= o.keys():
            doorway = doorway_of(home.map, o)
            if doorway in doors:
                out[o["handle"]] = doorway
    return out
