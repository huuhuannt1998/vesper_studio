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
import math
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


def from_plan(plan_m: dict, world_map) -> list[dict]:
    """An imported home's windows (floor-plan import): each of the plan's windows where the plan puts it (on its
    room's outline, the wall's centre line) at the plan's width, the sill and height its room's kind has by RULES
    (DEFAULT_PANE for a hall or another room), facing out of the wall it is on (source "plan"); then, in a room of a
    RULES kind with none, windows by rule (source "rule"): `plan_windows`' for a rectangle, else (or when those give
    none) `_on_real_walls`', so every such room with an outside wall gets one. A hall, a corridor or another room
    gets none by rule, as a generated hallway. The map's room ids are the plan's (generate.floor_plan_from)."""
    from vesper.lab.furnishing import room_kind
    from vesper.lab.generate import _edges, _gap, _outline
    outlines = {r["id"]: _outline(r["polygon"]) for r in plan_m["rooms"]}
    windows = plan_m.get("windows") or []
    count = {w["room"]: sum(1 for x in windows if x["room"] == w["room"]) for w in windows}
    seen: dict[str, int] = {}
    out = []
    for w in windows:
        rid, at = w["room"], (float(w["at"][0]), float(w["at"][1]))
        ring = outlines[rid]
        facing, side = _facing(ring, min(_edges(ring), key=lambda e: _gap(e, at)))
        kind = room_kind(world_map.room(rid))
        sill, height = RULES[kind][2:] if kind in RULES else DEFAULT_PANE[1:]
        seen[rid] = seen.get(rid, 0) + 1
        out.append({"id": f"{rid}_window{seen[rid] if count[rid] > 1 else ''}", "room": rid, "x": round(at[0], 3),
                    "z": round(at[1], 3), "width": round(float(w["width"]), 2), "sill": sill, "height": height,
                    "facing": facing, "side": side, "source": "plan"})
    by_rule = plan_windows(world_map)
    for room in world_map.rooms:
        rid = room.id
        if rid in count or rid not in outlines or room.outdoor or room_kind(room) not in RULES:
            continue
        ring = outlines[rid]
        boxed = len(ring) == 4 and all(min(abs(a[0] - b[0]), abs(a[1] - b[1])) < 1e-9 for a, b in _edges(ring))
        mine = [r for r in by_rule if r["room"] == rid] if boxed else []
        out += mine or _on_real_walls(world_map, room, ring)
    return out


def _facing(ring, edge) -> tuple[int, str]:
    """Out of the room through that edge of its outline: degrees clockwise from the plan's top, and the nearest of
    z0, x1, z1, x0."""
    from vesper.lab.generate import _inward
    nx, nz = _inward(ring, edge)
    facing = round(math.degrees(math.atan2(-nx, nz))) % 360
    return facing, min(FACING, key=lambda s: min(abs(FACING[s] - facing), 360 - abs(FACING[s] - facing)))


def _on_real_walls(world_map, room, ring) -> list[dict]:
    """A room's rule windows on its own outline, for a room that is not a rectangle (its box's sides need not be
    walls) or whose box gave none: the RULES count and size on the longest stretch of an outside wall, keeping
    CORNER_M from its ends and DOOR_M past a doorway's side, a wall with no doorway first; fewer, or narrower down to
    0.5 m, when the stretch is short. [] when the room has no outside wall."""
    from vesper.lab.furnishing import room_kind
    from vesper.lab.generate import _edges, _gap, _inward
    count, width, sill, height = RULES[room_kind(room)]
    stretches = []  # (no doorway on the wall, length, edge, lo, hi): lo/hi along the edge from its first corner
    for edge in _edges(ring):
        (ax, az), (bx, bz) = edge
        length = math.hypot(bx - ax, bz - az)
        ux, uz = (bx - ax) / length, (bz - az) / length
        nx, nz = _inward(ring, edge)
        probe = WALL + 0.15
        if any((o := world_map.room_at(ax + f * length * ux - probe * nx, az + f * length * uz - probe * nz))
               is not None and not o.outdoor for f in (0.15, 0.5, 0.85)):
            continue  # another room (or this one) is behind that wall
        free = [(WALL, length - WALL)]
        doors = [d for d in world_map.doorways if room.id in d.rooms and _gap(edge, d.center) <= DOOR_M + WALL]
        for d in doors:
            t = (d.center[0] - ax) * ux + (d.center[1] - az) * uz
            cut = (t - d.width / 2 - DOOR_M, t + d.width / 2 + DOOR_M)
            free = [part for lo, hi in free for part in ((lo, min(hi, cut[0])), (max(lo, cut[1]), hi)) if
                    part[1] - part[0] > 1e-9]
        stretches += [(not doors, hi - lo, edge, lo, hi) for lo, hi in free]
    for _, span, edge, lo, hi in sorted(stretches, key=lambda s: (s[0], round(s[1], 6)), reverse=True):
        for n in range(count, 0, -1):
            w = min(width, span / n - 2 * CORNER_M)
            if w >= 0.5:
                break
        else:
            continue
        (ax, az), (bx, bz) = edge
        length = math.hypot(bx - ax, bz - az)
        facing, side = _facing(ring, edge)
        spots = sorted((ax + (bx - ax) * t / length, az + (bz - az) * t / length)
                       for t in (lo + span * (k + 1) / (n + 1) for k in range(n)))
        return [{"id": f"{room.id}_window{k + 1 if n > 1 else ''}", "room": room.id, "x": round(x, 3),
                 "z": round(z, 3), "width": round(w, 2), "sill": sill, "height": height, "facing": facing,
                 "side": side, "source": "rule"} for k, (x, z) in enumerate(spots)]
    return []


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
