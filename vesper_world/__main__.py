"""The world service, run in the Habitat environment:

    python -m vesper_world export --hssd DIR --scene ID --out DIR    # a scene's map, openings and render manifest
    python -m vesper_world humanoids --data DIR --out DIR             # Habitat's humanoids' motions, for the browser
    python -m vesper_world renav --glb FILE --map FILE --out FILE     # an edited scene's walkable floor (plan W9b)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vesper_world.export import ExportError, renav_map, scene_map, scene_openings, scene_render


def load_scene(hssd: str, scene: str, radius: float):
    from vesper_world.habitat_scene import load_scene as load
    return load(hssd, scene, radius)


def load_glb(glb: str, radius: float):
    from vesper_world.habitat_scene import load_glb as load
    return load(glb, radius)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vesper_world", description="VESPER's world service")
    actions = ap.add_subparsers(dest="action", required=True)
    ex = actions.add_parser("export", help="an HSSD scene's map file (map.json), openings (openings.json) and "
                                          "render manifest (render.json)")
    ex.add_argument("--hssd", required=True, help="the HSSD dataset directory (hssd-hab)")
    ex.add_argument("--scene", required=True, help="the scene id, such as 102344022")
    ex.add_argument("--out", required=True, help="the directory to write map.json and openings.json to")
    ex.add_argument("--radius", type=float, default=0.2, help="the walker's radius in metres (default 0.2)")
    hu = actions.add_parser("humanoids", help="each Habitat humanoid's walk and standing pose (<name>.motion.json)")
    hu.add_argument("--data", required=True, help="Habitat's humanoid folder (humanoid_data)")
    hu.add_argument("--out", required=True, help="the directory to write the motions to")
    rn = actions.add_parser("renav", help="a scene exported from Blender: the map's walkable floor taken again from "
                                         "its navmesh, its grid and rooms kept")
    rn.add_argument("--glb", required=True, help="the edited scene (scene.glb, +Y up, without the doors)")
    rn.add_argument("--map", required=True, help="the home's map.json")
    rn.add_argument("--out", required=True, help="where to write the new map")
    rn.add_argument("--floor-y", type=float, default=0.0, help="the floor's height (the render manifest's floor_y)")
    rn.add_argument("--radius", type=float, default=0.2, help="the walker's radius in metres (default 0.2)")
    args = ap.parse_args(argv)
    if args.action == "humanoids":
        return _humanoids(Path(args.data), Path(args.out))
    if args.action == "renav":
        return _renav(args)
    try:
        scene = load_scene(args.hssd, args.scene, args.radius)
        world_map, openings, render = scene_map(scene), scene_openings(scene), scene_render(scene)
    except ExportError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "map.json").write_text(json.dumps(world_map))
    (out / "openings.json").write_text(json.dumps(openings, indent=1))
    (out / "render.json").write_text(json.dumps(render))
    doors = sum(o.get("kind") == "door" for o in openings["openings"])
    floor = openings["floor"]
    upper = (f"; the ground floor of {len(floor['storeys'])} storeys, leaving out "
             f"{', '.join(floor['left_out'])}" if floor["left_out"] else "")
    print(f"{args.scene}: {world_map['grid']['width']} x {world_map['grid']['height']} cells, "
          f"{len(world_map['rooms'])} rooms, {_count(doors, 'door')}, "
          f"{_count(len(openings['openings']) - doors, 'window')}{upper} -> {out}")
    return 0


def _renav(args) -> int:
    try:
        old = json.loads(Path(args.map).read_text())
        new = renav_map(old, load_glb(args.glb, args.radius).navigable, args.floor_y)
    except (ExportError, OSError, ValueError, KeyError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(new))
    before = sum(bin(b).count("1") for b in _bits(old))
    after = sum(bin(b).count("1") for b in _bits(new))
    print(f"{old.get('scene')}: {before} walkable cells before, {after} after -> {args.out}")
    return 0


def _bits(m: dict) -> bytes:
    import base64
    return base64.b64decode(m["grid"]["walkable"])


def _humanoids(data: Path, out: Path) -> int:
    from vesper_world.humanoids import HumanoidError, convert_all
    try:
        done = convert_all(data, out) if data.is_dir() else []
    except (HumanoidError, OSError, KeyError, ValueError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not done:
        print(f"FAILED: no humanoids in {data} (<name>/<name>.urdf, .glb and _motion_data_smplx.pkl)", file=sys.stderr)
        return 1
    print(f"{_count(len(done), 'humanoid')} ({', '.join(done)}) -> {out}")
    return 0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


if __name__ == "__main__":
    sys.exit(main())
