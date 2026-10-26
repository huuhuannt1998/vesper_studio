"""`python -m vesper_world export --hssd DIR --scene ID --out DIR`: the scene's map file and door openings."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vesper_world.export import ExportError, scene_map, scene_openings


def load_scene(hssd: str, scene: str, radius: float):
    from vesper_world.habitat_scene import load_scene as load
    return load(hssd, scene, radius)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vesper_world", description="VESPER's world service")
    actions = ap.add_subparsers(dest="action", required=True)
    ex = actions.add_parser("export", help="an HSSD scene's map file (map.json) and door openings (openings.json)")
    ex.add_argument("--hssd", required=True, help="the HSSD dataset directory (hssd-hab)")
    ex.add_argument("--scene", required=True, help="the scene id, such as 102344022")
    ex.add_argument("--out", required=True, help="the directory to write map.json and openings.json to")
    ex.add_argument("--radius", type=float, default=0.2, help="the walker's radius in metres (default 0.2)")
    args = ap.parse_args(argv)
    try:
        scene = load_scene(args.hssd, args.scene, args.radius)
        world_map, openings = scene_map(scene), scene_openings(scene)
    except ExportError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "map.json").write_text(json.dumps(world_map))
    (out / "openings.json").write_text(json.dumps(openings, indent=1))
    doors = sum(o.get("kind") == "door" for o in openings["openings"])
    floor = openings["floor"]
    upper = (f"; the ground floor of {len(floor['storeys'])} storeys, leaving out "
             f"{', '.join(floor['left_out'])}" if floor["left_out"] else "")
    print(f"{args.scene}: {world_map['grid']['width']} x {world_map['grid']['height']} cells, "
          f"{len(world_map['rooms'])} rooms, {_count(doors, 'door')}, "
          f"{_count(len(openings['openings']) - doors, 'window')}{upper} -> {out}")
    return 0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


if __name__ == "__main__":
    sys.exit(main())
