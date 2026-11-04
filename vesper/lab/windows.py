"""A home's windows (environment design 2026-11-03): where daylight comes in, what the residents open and shade,
and what tall furniture must keep clear of.

A generated home gets them by rule, on each room's outside wall (never a wall with a doorway), by the room's kind:
a living room two wide ones, a bedroom one it can be left by, a kitchen one over the counter, a bathroom or laundry a
small high one, a hallway none. An HSSD house has its own windows (its openings.json gives each one's centre); their
size is not given, so they are taken as RULES' default pane and the wall they face as their room's nearest side.

`windows.json` beside home.yaml: {"format": "vesper-windows/1", "items": [window...]}, a window being
{id, room, x, z (the wall's centre line), width, sill, height (m), facing (the outward normal, degrees clockwise from
the plan's top), side (z0|z1|x0|x1 of its room), source ("rule" | "hssd")}. Every size is assumed (a typical one).
"""
from __future__ import annotations

import json
from pathlib import Path

FORMAT = "vesper-windows/1"
WALL = 0.1  # the generator's half wall: a room's polygon stops this far from the wall's centre line
CORNER_M = 0.3  # a window keeps this far from its wall's ends
DOOR_M = 0.6  # a wall with a doorway within this of it gets no window
# kind: (count, width, sill, height), typical sizes (assumed)
RULES = {"living": (2, 1.6, 0.6, 1.4), "bedroom": (1, 1.2, 0.9, 1.2), "kitchen": (1, 1.0, 1.05, 1.0),
         "bathroom": (1, 0.6, 1.5, 0.6), "office": (1, 1.2, 0.9, 1.2), "dining": (1, 1.4, 0.8, 1.3),
         "laundry": (1, 0.6, 1.4, 0.6)}
DEFAULT_PANE = (1.2, 0.9, 1.2)  # an HSSD window's width, sill, height (assumed: not in the scene's openings)
FACING = {"z0": 0, "x1": 90, "z1": 180, "x0": 270}


def room_box(room):
    xs, zs = [p[0] for p in room.polygon], [p[1] for p in room.polygon]
    return min(xs), min(zs), max(xs), max(zs)


def outside(world_map, room, side, box, probe=2 * WALL + 0.15) -> bool:
    """Whether the far side of the room's `side` wall is outdoors along its whole length (no indoor room there)."""
    x0, z0, x1, z1 = box
    for f in (0.15, 0.5, 0.85):
        if side in ("z0", "z1"):
            p = (x0 + f * (x1 - x0), z0 - probe if side == "z0" else z1 + probe)
        else:
            p = (x0 - probe if side == "x0" else x1 + probe, z0 + f * (z1 - z0))
        other = world_map.room_at(*p)
        if other is not None and other.id != room.id and not other.outdoor:
            return False
    return True


def _doors_on(world_map, room, side, box) -> bool:
    """Whether one of the room's doorways stands on (or by) that wall."""
    x0, z0, x1, z1 = box
    for d in world_map.doorways:
        if room.id not in d.rooms:
            continue
        cx, cz = d.center
        if side in ("z0", "z1"):
            near = abs(cz - (z0 if side == "z0" else z1)) <= DOOR_M + WALL and x0 - DOOR_M <= cx <= x1 + DOOR_M
        else:
            near = abs(cx - (x0 if side == "x0" else x1)) <= DOOR_M + WALL and z0 - DOOR_M <= cz <= z1 + DOOR_M
        if near:
            return True
    return False


def plan_windows(world_map) -> list[dict]:
    """The windows of a generated home, by rule."""
    from vesper.lab.furnishing import room_kind
    out = []
    for room in world_map.rooms:
        kind = None if room.outdoor else room_kind(room)
        if kind not in RULES:
            continue
        count, width, sill, height = RULES[kind]
        box = room_box(room)
        x0, z0, x1, z1 = box
        sides = [s for s in ("z0", "z1", "x0", "x1") if outside(world_map, room, s, box)
                 and not _doors_on(world_map, room, s, box)]
        if not sides:
            continue
        length = lambda s: (x1 - x0) if s in ("z0", "z1") else (z1 - z0)  # noqa: E731
        side = max(sides, key=lambda s: (length(s), s in ("z0", "z1")))
        span = length(side)
        w = min(width, span / count - 2 * CORNER_M)
        if w < 0.5:
            continue
        lo = x0 if side in ("z0", "z1") else z0
        for k in range(count):
            along = lo + span * (k + 1) / (count + 1)
            if side in ("z0", "z1"):
                x, z = along, (z0 - WALL if side == "z0" else z1 + WALL)
            else:
                x, z = (x0 - WALL if side == "x0" else x1 + WALL), along
            out.append({"id": f"{room.id}_window{k + 1 if count > 1 else ''}", "room": room.id, "x": round(x, 3),
                        "z": round(z, 3), "width": round(w, 2), "sill": sill, "height": height,
                        "facing": FACING[side], "side": side, "source": "rule"})
    return out


def from_openings(world_map, openings: list[dict]) -> list[dict]:
    """An HSSD house's windows from its openings: each to its nearest indoor room, facing that room's nearest side."""
    out = []
    indoor = [r for r in world_map.rooms if not r.outdoor]
    if not indoor:
        return out
    width, sill, height = DEFAULT_PANE
    for k, o in enumerate(x for x in openings if x.get("kind") == "window"):
        p = (o["x"], o["z"])

        def gap(room):
            x0, z0, x1, z1 = room_box(room)
            return max(x0 - p[0], 0, p[0] - x1) + max(z0 - p[1], 0, p[1] - z1)
        room = min(indoor, key=gap)
        x0, z0, x1, z1 = room_box(room)
        near = {"z0": abs(p[1] - z0), "z1": abs(p[1] - z1), "x0": abs(p[0] - x0), "x1": abs(p[0] - x1)}
        side = min(near, key=near.get)
        y = o.get("y")
        out.append({"id": f"{room.id}_window_{k + 1}", "room": room.id, "x": round(p[0], 3), "z": round(p[1], 3),
                    "width": width, "sill": round(y - height / 2, 2) if isinstance(y, (int, float)) else sill,
                    "height": height, "facing": FACING[side], "side": side, "source": "hssd"})
    return out


def write_windows(home_dir: Path, items: list[dict]) -> Path:
    path = Path(home_dir) / "windows.json"
    path.write_text(json.dumps({"format": FORMAT, "items": items}, indent=1) + "\n")
    return path


def load_windows(home_dir: Path) -> list[dict]:
    """The home's windows, or [] (an older home without windows.json)."""
    path = Path(home_dir) / "windows.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    return list(data.get("items") or []) if data.get("format") == FORMAT else []


def area(w: dict) -> float:
    return w["width"] * w["height"]


def blocks(box, window: dict, height: float, keep_m: float = 0.6) -> bool:
    """Whether a piece with footprint `box` and `height` stands in front of a window: taller than its sill and
    within `keep_m` of the window's span on its wall."""
    if height <= window["sill"] - 0.05:
        return False
    half = window["width"] / 2
    if window["side"] in ("z0", "z1"):
        seg = (window["x"] - half, window["z"], window["x"] + half, window["z"])
    else:
        seg = (window["x"], window["z"] - half, window["x"], window["z"] + half)
    x0, z0, x1, z1 = box
    dx = max(seg[0] - x1, 0, x0 - seg[2])
    dz = max(seg[1] - z1, 0, z0 - seg[3])
    return (dx * dx + dz * dz) ** 0.5 < keep_m + WALL
