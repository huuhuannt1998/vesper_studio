"""Is a home laid out as homes are built and furnished? (environment design 2026-11-03, goal 8; PI 2026-11-03:
"beds should not be in front of the doors"). One checker for every home, whoever furnished it: VESPER's built-in
furniture (`furniture.json`), a model Claude or a person built in Blender (`scene.glb`, its objects' boxes read from
the file), or an HSSD house (its furniture is in its walking map already, so only the map's own rules apply).

Rules, each finding an error (the home breaks it) or a warning (worth a look):
- door: nothing solid within DOOR_CLEAR_M of a doorway's centre in either room it opens into (a door's swing and
  its approach);
- window: nothing taller than a window's sill in front of it (vesper.lab.windows.blocks);
- bed: a bed's head or long side against a wall, not standing free in the room (warning);
- walkway: every pair of a room's doorways joined by a path WALKWAY_M wide;
- reach: every device a hand works within reach of the floor a person can walk to;
- kitchen: with a sink, a cooktop and a fridge named in the model, the work triangle's legs 1.2 to 2.7 m and its
  total at most 7.9 m (NKBA kitchen planning guidelines; warning).
Clearances are planning guidelines, not building code; none is a measurement of a real home.
"""
from __future__ import annotations

import json
import math
import struct
from collections import deque
from pathlib import Path

from vesper.lab.furnishing import DOOR_CLEAR_M, REACH_M
from vesper.lab.geometry import distance
from vesper.lab.windows import blocks, load_windows, room_box

WALKWAY_M = 0.9
BED_WALL_M = 0.2  # a bed this close to a wall stands against it
SOLID_MIN_H = 0.25  # lower than this (a rug, a mat) is walked on
TRIANGLE = (1.2, 2.7, 7.9)  # NKBA: each leg 4-9 ft, the sum at most 26 ft
VESPER_PARTS = ("wall-", "floor:", "lintel:", "threshold:", "window:", "device:", "switch:", "sun")
NAMES = {"bed": "bed", "wardrobe": "wardrobe", "closet": "wardrobe", "sofa": "sofa", "couch": "sofa",
         "fridge": "fridge", "refrigerator": "fridge", "sink": "sink", "stove": "cooktop", "cooktop": "cooktop",
         "hob": "cooktop", "oven": "cooktop", "range": "cooktop", "desk": "desk", "table": "table", "chair": "chair",
         "shelf": "bookshelf", "bookcase": "bookshelf", "cabinet": "cabinet", "tv": "tv_stand", "rug": "rug",
         "carpet": "rug", "plant": "plant", "bath": "bath", "toilet": "toilet", "washer": "washer", "dryer": "dryer"}


def kind_of(name: str) -> str:
    low = name.lower()
    return next((k for word, k in NAMES.items() if word in low), "object")


# -- the pieces of a home -----------------------------------------------------------------------------------------
def pieces_of(home) -> tuple[list[dict], str]:
    """The home's furniture as pieces {name, kind, room, box, h}, and where they came from."""
    here = Path(home.path).parent
    glb = here / "scene.glb"
    if glb.is_file() and not (here / "render.json").is_file():
        return glb_pieces(glb, home.map), "scene.glb"
    f = here / "furniture.json"
    if f.is_file():
        items = json.loads(f.read_text()).get("items") or []
        out = [{"name": f"{i['kind']}@{i['room']}", "kind": i["kind"], "room": i["room"], "box": i["box"],
                "h": i["h"] if i.get("solid", True) else 0.0} for i in items]
        return out + [p for i in items if i["kind"] == "counter" for p in _counter_parts(i)], "furniture.json"
    return [], "none"


def _counter_parts(item) -> list[dict]:
    """The sink and the hob a counter run holds, where furniture3d.js counterParts draws them along it (near its
    ends, 0.35 m in, never closer than 0.46 of its width), as points for the kitchen's work triangle."""
    yaw = math.pi / 2 - math.radians(item["facing"])  # as the view turns the model (devices3d.js facingYaw)
    s = max(0.23 * item["w"], item["w"] / 2 - 0.35)
    out = []
    for kind, lx in (("sink", -s), ("cooktop", s)):
        x, z = item["x"] + lx * math.cos(yaw), item["z"] - lx * math.sin(yaw)
        out.append({"name": f"{kind}@{item['room']}", "kind": kind, "room": item["room"],
                    "box": [round(x, 3), round(z, 3), round(x, 3), round(z, 3)], "h": 0.0})
    return out


def glb_pieces(path: Path, world_map) -> list[dict]:
    """Every mesh object of a glTF binary that VESPER's shell did not make, as a box on the plan (glTF +Y up: x and
    z are the plan's, y the height)."""
    out = []
    for name, (x0, y0, z0, x1, y1, z1) in glb_boxes(path):
        if name.startswith(VESPER_PARTS) or y1 - y0 <= 0:
            continue
        room = world_map.room_at((x0 + x1) / 2, (z0 + z1) / 2)
        out.append({"name": name, "kind": kind_of(name), "room": room.id if room else None,
                    "box": [round(x0, 3), round(z0, 3), round(x1, 3), round(z1, 3)], "h": round(y1 - max(y0, 0), 3)})
    return out


def glb_boxes(path: Path) -> list[tuple[str, tuple]]:
    """(node name, world box (x0, y0, z0, x1, y1, z1)) of every node with a mesh, from the file's accessors' bounds
    and the nodes' transforms."""
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path}: not a glTF binary")
    length, _ = struct.unpack_from("<I4s", data, 12)  # the first chunk: JSON
    gltf = json.loads(data[20:20 + length])
    nodes, meshes, acc = gltf.get("nodes", []), gltf.get("meshes", []), gltf.get("accessors", [])
    children = {c for n in nodes for c in n.get("children", [])}
    roots = [k for k in range(len(nodes)) if k not in children]
    out = []

    def walk(k, parent):
        n = nodes[k]
        m = _matmul(parent, _local(n))
        if "mesh" in n:
            lo, hi = [math.inf] * 3, [-math.inf] * 3
            for prim in meshes[n["mesh"]].get("primitives", []):
                a = acc[prim["attributes"]["POSITION"]]
                for corner in _corners(a.get("min"), a.get("max")):
                    p = _apply(m, corner)
                    lo, hi = [min(u, v) for u, v in zip(lo, p)], [max(u, v) for u, v in zip(hi, p)]
            if lo[0] < math.inf:
                out.append((n.get("name", f"node{k}"), (*lo, *hi)))
        for c in n.get("children", []):
            walk(c, m)
    for r in roots:
        walk(r, _IDENTITY)
    return out


_IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]  # column-major, as glTF


def _local(n) -> list[float]:
    if "matrix" in n:
        return list(n["matrix"])
    tx, ty, tz = n.get("translation", (0, 0, 0))
    x, y, z, w = n.get("rotation", (0, 0, 0, 1))
    sx, sy, sz = n.get("scale", (1, 1, 1))
    r = [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]
    return [r[0] * sx, r[1] * sx, r[2] * sx, 0, r[3] * sy, r[4] * sy, r[5] * sy, 0, r[6] * sz, r[7] * sz, r[8] * sz, 0,
            tx, ty, tz, 1]


def _matmul(a, b) -> list[float]:
    return [sum(a[r + 4 * k] * b[k + 4 * c] for k in range(4)) for c in range(4) for r in range(4)]


def _apply(m, p) -> list[float]:
    x, y, z = p
    return [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14]]


def _corners(lo, hi):
    if not lo or not hi:
        return []
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


# -- the rules ----------------------------------------------------------------------------------------------------
def _gap(box, p) -> float:
    x0, z0, x1, z1 = box
    return math.hypot(max(x0 - p[0], 0, p[0] - x1), max(z0 - p[1], 0, p[1] - z1))


def _finding(rule, ok, message, severity="error", where=None) -> dict:
    return {"rule": rule, "ok": ok, "severity": severity, "where": where, "message": message}


def check(home, pieces: list[dict] | None = None, windows: list[dict] | None = None) -> dict:
    """The home's findings, rule by rule, and a summary: {source, findings, errors, warnings, checked}."""
    source = "given"
    if pieces is None:
        pieces, source = pieces_of(home)
    windows = load_windows(Path(home.path).parent) if windows is None else windows
    solid = [p for p in pieces if p["h"] >= SOLID_MIN_H]
    m, findings = home.map, []
    for d in m.doorways:
        # a piece blocks a door only from a room the door opens into (one behind a wall does not); a model's
        # piece outside every room is kept, to be safe
        bad = [p for p in solid if p["room"] in (*d.rooms, None) and _gap(p["box"], d.center) < DOOR_CLEAR_M]
        findings.append(_finding("door", not bad, f"doorway {d.id}: " + (
            ", ".join(f"{p['kind']} ({p['name']}) {(_gap(p['box'], d.center)):.2f} m from it" for p in bad)
            if bad else f"clear for {DOOR_CLEAR_M} m"), where=d.id))
    for w in windows:
        bad = [p for p in solid if p["room"] == w["room"] and blocks(p["box"], w, p["h"])]
        findings.append(_finding("window", not bad, f"window {w['id']}: " + (
            ", ".join(f"{p['kind']} ({p['name']}, {p['h']:.2f} m tall) in front of its {w['sill']:.2f} m sill"
                      for p in bad) if bad else "clear"), where=w["id"]))
    for p in solid:
        if p["kind"] != "bed" or p["room"] is None:
            continue
        x0, z0, x1, z1 = room_box(m.room(p["room"]))
        bx0, bz0, bx1, bz1 = p["box"]
        against = min(bx0 - x0, x1 - bx1, bz0 - z0, z1 - bz1) <= BED_WALL_M
        findings.append(_finding("bed", against, f"{p['name']}: " + ("against a wall" if against else
                                                                     "stands free of every wall"),
                                 severity="warning", where=p["room"]))
    findings += _walkways(home, solid)
    findings += _reach(home, solid)
    findings += _kitchen(pieces)
    errors = [f for f in findings if not f["ok"] and f["severity"] == "error"]
    warnings = [f for f in findings if not f["ok"] and f["severity"] == "warning"]
    return {"source": source, "pieces": len(pieces), "findings": findings, "errors": len(errors),
            "warnings": len(warnings), "checked": sorted({f["rule"] for f in findings})}


def _floor(home, solid) -> tuple:
    """The walkable cells with the furniture taken off: the plan without furniture (map.original.json) when there
    is one, else the map (whose floor may already be taken under the furniture)."""
    from vesper.lab.furnishing import _cells_in
    from vesper.lab.worldmap import WorldMap
    original = Path(home.path).parent / "map.original.json"
    g = (WorldMap.load(original) if original.is_file() else home.map).grid
    free = {(i, j) for j in range(g.height) for i in range(g.width) if g.is_walkable(i, j)}
    for p in solid:
        free -= set(_cells_in(g, p["box"]))
    return g, free


def _walkways(home, solid) -> list[dict]:
    """Each room's doorways joined through floor at least WALKWAY_M wide (cells at least half of it from any
    blocked cell), pair by pair (vesper.lab.furnishing.walk_widths). A home whose map.original.json is a plan
    VESPER drew with no furniture in it (`bare_plan`) is also measured bare, and each pair needs only the width the
    bare plan gives it: a doorway the plan itself reaches by a narrower path (a passage drawn narrower) is a
    warning, the plan's, as long as the furniture keeps that path; a pair the furniture narrows or cuts off is an
    error. Any other home (an HSSD house: its map.original.json has HSSD's furniture in it) needs WALKWAY_M a pair."""
    from vesper.lab.furnishing import walk_k, walk_widths
    g, free = _floor(home, solid)
    bare = _floor(home, [])[1] if bare_plan(home) else None
    top = walk_k(g)
    width = lambda k: f"{(2 * k - 1) * g.cell:.1f} m"  # noqa: E731
    out = []
    for room in home.map.rooms:
        if room.outdoor:
            continue
        dws = [d for d in home.map.doorways if room.id in d.rooms]
        if len(dws) < 2:
            continue
        x0, z0, x1, z1 = room_box(room)
        box = (x0 - 0.3, z0 - 0.3, x1 + 0.3, z1 + 0.3)
        now = walk_widths(g, free, box, [d.center for d in dws])
        need, own = dict.fromkeys(now, top), {}
        if bare is not None:  # and how much floor each doorway has by it: the narrow end of a passage has less
            widths = walk_widths(g, bare, box, [d.center for d in dws], own=True)
            need = {p: k for p, k in widths.items() if p[0] != p[1]}
            own = {p[0]: k for p, k in widths.items() if p[0] == p[1]}
        name = lambda pair: f"{dws[pair[0]].id} and {dws[pair[1]].id}"  # noqa: E731
        cut = [f"no {WALKWAY_M} m path between {name(p)}" if need[p] >= top else
               f"the furniture {'narrows' if now[p] else 'closes'} the plan's {width(need[p])} path between {name(p)}"
               for p in now if now[p] < min(need[p], top)]
        narrow = []  # the doorways the plan reaches only narrower: each group of them, from the room's main group
        groups = _groups(len(dws), [p for p in need if need[p] >= top])
        main = max(groups, key=lambda grp: (len(grp), max(own.get(k, 0) for k in grp), 0 in grp))
        for grp in groups:
            across = [(min(a, b), max(a, b)) for a in grp for b in main]
            if grp is main or any(now[p] < need[p] for p in across):
                continue  # the furniture's doing is an error above
            w = max(need[p] for p in across)
            doors = ", ".join(dws[k].id for k in sorted(grp))
            narrow.append(f"only a {width(w)} path to {doors}" if w else f"no path to {doors}")
        if cut or not narrow:
            out.append(_finding("walkway", not cut, f"{room.id}: " + (
                "; ".join(cut) if cut else f"its doorways joined by a {WALKWAY_M} m path"), where=room.id))
        if narrow:
            out.append(_finding("walkway", False, f"{room.id}: " + "; ".join(narrow) + " even with no furniture: the "
                                "plan's room is narrower there", severity="warning", where=room.id))
    return out


def _groups(n: int, joined: list[tuple[int, int]]) -> list[set[int]]:
    """The doorways 0..n-1 in groups that the pairs `joined` connect."""
    group = {k: {k} for k in range(n)}
    for a, b in joined:
        if group[a] is not group[b]:
            merged = group[a] | group[b]
            for k in merged:
                group[k] = merged
    return list({id(g): g for g in group.values()}.values())


def bare_plan(home) -> bool:
    """Whether the home's map.original.json is a plan VESPER drew with no furniture in it: marked `"bare": true`
    (vesper.lab.generate writes it so), or, for a home made before the mark, a generated or imported scene."""
    original = Path(home.path).parent / "map.original.json"
    if not original.is_file():
        return False
    try:
        if json.loads(original.read_text()).get("bare") is True:
            return True
    except (OSError, ValueError, AttributeError):
        return False
    return str(home.scene).startswith(("generated/", "imported/"))


def _flood(start, area) -> set:
    seen, todo = set(), deque(start)
    while todo:
        c = todo.popleft()
        if c in seen or c not in area:
            continue
        seen.add(c)
        i, j = c
        todo.extend(((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)))
    return seen


def _reach(home, solid) -> list[dict]:
    g, free = _floor(home, solid)
    start = g.cell_of(*home.avatar["start"])
    seen = _flood({start} if start else set(), free)
    out = []
    for d in home.devices:
        if d.mount == "ceiling" and d.wall_switch is None:
            continue
        hand = d.wall_switch or d.position
        ok = any(distance(g.center(*c), hand) <= REACH_M + 0.1 for c in seen)
        out.append(_finding("reach", ok, f"{d.name}: " + ("within reach" if ok else "no floor within reach of it"),
                            where=d.name))
    return out


def _kitchen(pieces) -> list[dict]:
    find = {k: next((p for p in pieces if p["kind"] == k), None) for k in ("sink", "cooktop", "fridge")}
    if not all(find.values()):
        return []
    c = {k: ((p["box"][0] + p["box"][2]) / 2, (p["box"][1] + p["box"][3]) / 2) for k, p in find.items()}
    legs = [distance(c["sink"], c["cooktop"]), distance(c["cooktop"], c["fridge"]), distance(c["fridge"], c["sink"])]
    lo, hi, total = TRIANGLE
    ok = all(lo <= leg <= hi for leg in legs) and sum(legs) <= total
    return [_finding("kitchen", ok, "work triangle legs " + ", ".join(f"{x:.2f}" for x in legs) +
                     f" m, total {sum(legs):.2f} m (NKBA: each {lo}-{hi} m, total at most {total} m)",
                     severity="warning")]

