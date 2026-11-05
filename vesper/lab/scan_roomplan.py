"""An Apple RoomPlan export read into a `vesper-plan/1` (scan import, part B).

Polycam's Room mode, the 3D Scanner App and other RoomPlan apps export what the phone saw as a CapturedRoom (one
room) or a CapturedStructure (several rooms in one frame) in JSON: walls, doors, windows and openings (each a
rectangle: a column-major 4x4 `transform` whose x axis runs along it, and `dimensions` [width, height, depth]),
`sections` (a room label at a point) and `objects` (bed, sofa, storage...). RoomPlan works in metres with y up, so
the plan's x is the scan's x and the plan's z the scan's z: no scale is needed and none is guessed.

Rooms are the regions the walls enclose: the walls (on the imported storey) are cut where they meet or cross, ends
within SNAP_M of each other or of another wall are one point, a wall that stops short of another by up to JOIN_M is
joined to it (and said), and every face of the resulting drawing is a region. A section names the region its centre
lies in; with no sections each region is a room typed `other`. A door, window or opening stands on the wall RoomPlan
says it belongs to (`parentIdentifier`), and joins the rooms found on either side of that wall (or a room and the
outside); an opening is a door with no leaf. The entrance is a door to the outside. What the scan does not show is
left out with a sentence in the warnings, never invented: an upstairs (one storey is imported: the lowest one among
the walls, and a warning says which), a ceiling (RoomPlan scans none: VESPER builds 2.5 m), a door that leads from no
room. A plan's door or window has exactly the keys `load_plan` takes, so the heights of the doors and windows (bottom
and top, from the floor) travel beside the plan, in the warnings' `heights` (`ScanWarnings`).

`plan_from_parts` does the work on plain parts in plan coordinates; the USDZ reader (task B2) feeds it too.
"""
from __future__ import annotations

import math

from vesper.lab.generate import LABELS
from vesper.lab.geometry import Point, point_in_polygon, polygon_area
from vesper.lab.plan import FORMAT, PlanError, _polygon_problem, load_plan

SNAP_M = 0.05  # wall ends (and a wall end and another wall) closer than this are one point
JOIN_M = 0.5  # a wall stopping this short of another wall, or of another wall's free end, is joined to it
PROBES_M = (0.1, 0.2, 0.35, 0.5)  # how far across a wall the rooms on its two sides are looked for (a wall's
# thickness separates two rooms scanned on their own; rectify closes such a gap, and up to 0.5 m at a door)
REACH_M = 0.5  # a door or window naming no wall stands on the nearest wall this close
TWICE_M = 0.3  # two doors joining the same rooms closer than this (or than half the narrower one's width) are one
# door listed twice (two doorways side by side, as in a real meeting room, are a width apart and stay two)
NEAR_M = 0.3  # an object whose centre is in no room stands in the room whose wall is this close (a TV on a wall)
STRAIGHT_M = 0.01  # a corner less than this off the line through its neighbours is not a corner
SLIVER_M2 = 0.05  # a region smaller than this is two walls drawn close together, not a room
ROOM_LEAST_M2 = 1.5  # a region with no section smaller than this is left out (a gap between walls, a duct)
CEILING_M = 2.5  # the ceiling VESPER builds
SAME_M = 0.05  # walls within this of one height are one height
ROUND = 4  # decimals kept in the plan (0.1 mm)
ROUND_M = 10.0 ** -ROUND  # the least width kept

ROOM_TYPES = {"bedroom": "bedroom", "livingRoom": "living", "kitchen": "kitchen", "bathroom": "bathroom",
              "diningRoom": "dining", "unidentified": "other"}
OBJECT_KINDS = {"bed": "bed", "sofa": "sofa", "chair": "chair", "table": "table", "storage": "cabinet",
                "refrigerator": "refrigerator", "oven": "oven", "stove": "stove", "sink": "sink", "toilet": "toilet",
                "bathtub": "bathtub", "washerDryer": "washerDryer", "dishwasher": "dishwasher",
                "television": "television", "fireplace": "fireplace", "stairs": "stairs"}
_LISTS = ("walls", "doors", "windows", "openings", "floors", "objects")


EXTRA = ("install the scan extra: in your VESPER clone, uv tool install --python 3.12 --editable '.[scan]' (or "
         "uv pip install -e '.[scan]' in its .venv)")


class ScanError(PlanError):
    """A scan or 3D model VESPER cannot turn into a plan, with the reason."""


class ScanWarnings(list):
    """The warnings (sentences), and `heights`: {"doors": [...], "windows": [...]}, one {"bottom", "top"} (metres
    from the floor) per door and window of the plan, in the plan's order. `load_plan` takes no other keys on a door or
    window, so the heights travel here. (`rectify` may leave doors or windows out; their heights stay in this order.)"""

    def __init__(self, items=(), heights: dict | None = None):
        super().__init__(items)
        self.heights = heights if heights is not None else {"doors": [], "windows": []}


# --- the RoomPlan file -------------------------------------------------------------------------------------------

def _num(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _category(it: dict) -> str | None:
    """`{"door": {"isOpen": false}}` -> "door" (RoomPlan version 2); a plain string in older exports."""
    cat = it.get("category")
    if isinstance(cat, dict) and len(cat) == 1:
        return next(iter(cat))
    return cat if isinstance(cat, str) else None


def _frame(it) -> tuple[list[float], list[float]] | None:
    """An element's transform (16 numbers, column-major) and dimensions (3 numbers), or None."""
    if not isinstance(it, dict):
        return None
    t, d = it.get("transform"), it.get("dimensions")
    if not (isinstance(t, list) and len(t) == 16 and all(_num(v) for v in t)):
        return None
    if not (isinstance(d, list) and len(d) == 3 and all(_num(v) for v in d)):
        return None
    return [float(v) for v in t], [float(v) for v in d]


def _story(it: dict) -> int:
    s = it.get("story", 0)
    return s if isinstance(s, int) and not isinstance(s, bool) else 0


def _lists(raw: dict) -> tuple[dict[str, list], list]:
    """The element lists and the sections of a CapturedRoom, or of a CapturedStructure, list by list: its own merged
    list when it has a non-empty one (Apple's), else its rooms' lists together (they share one frame)."""
    rooms = raw.get("rooms") if isinstance(raw.get("rooms"), list) else []
    rooms = [r for r in rooms if isinstance(r, dict)]
    lists: dict[str, list] = {}
    for k in (*_LISTS, "sections"):
        own = raw.get(k)
        if isinstance(own, list) and own:
            lists[k] = own
        else:
            lists[k] = [x for r in rooms if isinstance(r.get(k), list) for x in r[k]]
    sections = lists.pop("sections")
    return lists, [s for s in sections if isinstance(s, dict)]


def read_roomplan_json(raw: dict) -> tuple[dict, list[dict], list[str]]:
    """A RoomPlan CapturedRoom or CapturedStructure (the JSON, already parsed) as (the `vesper-plan/1` in metres, the
    objects as pieces of furniture `{kind, room, box, h, yaw}`, warnings as `ScanWarnings`). See `plan_from_parts`.
    One storey is imported: the lowest among the walls (a warning says which when it is not story 0 or when the scan
    has others; elements on the others are left out). A ScanError when the file is not RoomPlan's or its walls
    enclose no room."""
    if not isinstance(raw, dict) or not (isinstance(raw.get("walls"), list) or isinstance(raw.get("rooms"), list)):
        raise ScanError("not a RoomPlan file: expected a CapturedRoom or CapturedStructure (with walls, or rooms)")
    lists, sections = _lists(raw)
    framed = {k: [(it, f) for it in v if (f := _frame(it)) is not None] for k, v in lists.items()}
    warnings: list[str] = []
    for k in _LISTS:
        if (bad := len(lists[k]) - len(framed[k])) > 0:
            warnings.append(f"{bad} of the scan's {k} have no usable transform or dimensions: left out")
    if not framed["walls"]:
        raise ScanError("the RoomPlan file has no walls")

    storey = min(_story(it) for it, _ in framed["walls"])
    upstairs = sorted(({_story(it) for v in framed.values() for it, _ in v} | {_story(s) for s in sections})
                      - {storey})
    if upstairs:
        n = (sum(1 for v in framed.values() for it, _ in v if _story(it) != storey)
             + sum(1 for s in sections if _story(s) != storey))
        where = ", ".join(f"story {s}" for s in upstairs)
        warnings.append(f"{n} elements on {where} left out: one storey is imported, story {storey} is read")
    elif storey != 0:
        warnings.append(f"the scan's walls are on story {storey}: story {storey} is read")
    framed = {k: [(it, f) for it, f in v if _story(it) == storey] for k, v in framed.items()}
    sections = [s for s in sections if _story(s) == storey]

    floors = [t[13] for _, (t, _) in framed["floors"]]
    floor_y = min(floors) if floors else min(t[13] - d[1] / 2 for _, (t, d) in framed["walls"])

    def ends(t, d) -> tuple[list[float], list[float]] | None:
        ux, uz = t[0], t[2]
        norm = math.hypot(ux, uz)
        if norm < 1e-6:
            return None
        ux, uz = ux / norm, uz / norm
        half = d[0] / 2
        return [t[12] - ux * half, t[14] - uz * half], [t[12] + ux * half, t[14] + uz * half]

    walls = []
    for it, (t, d) in framed["walls"]:
        if (ab := ends(t, d)) is None or d[0] <= 0:
            continue
        walls.append({"id": str(it.get("identifier")), "a": ab[0], "b": ab[1], "height": d[1]})

    def openings(kind: str) -> list[dict]:
        out = []
        for it, (t, d) in framed[kind]:
            parent = it.get("parentIdentifier")
            out.append({"wall": parent if isinstance(parent, str) else None, "at": [t[12], t[14]], "width": d[0],
                        "bottom": t[13] - d[1] / 2 - floor_y, "top": t[13] + d[1] / 2 - floor_y})
        return out

    objects = []
    for it, (t, d) in framed["objects"]:
        objects.append({"category": _category(it) or "unknown", "center": [t[12], t[13] - floor_y, t[14]],
                        "size": d, "yaw": math.degrees(math.atan2(t[2], t[0]))})
    secs = []
    for s in sections:
        c = s.get("center")
        if isinstance(c, list) and len(c) == 3 and all(_num(v) for v in c):
            secs.append({"label": s.get("label") if isinstance(s.get("label"), str) else "unidentified",
                         "center": [c[0], c[2]]})
    plan, pieces, more = plan_from_parts(walls, openings("doors"), openings("windows"), openings("openings"), secs,
                                         objects)
    more[:0] = warnings
    return plan, pieces, more


# --- the walls as a drawing --------------------------------------------------------------------------------------

def _snap(points: list[Point]) -> list[Point]:
    """Each point moved to the middle of the points within SNAP_M of it (chained)."""
    parent = list(range(len(points)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if math.dist(points[i], points[j]) <= SNAP_M:
                parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(points)):
        groups.setdefault(root(i), []).append(i)
    out: list[Point] = [(0.0, 0.0)] * len(points)
    for members in groups.values():
        mid = (sum(points[i][0] for i in members) / len(members), sum(points[i][1] for i in members) / len(members))
        for i in members:
            out[i] = mid
    return out


def _param(p: Point, a: Point, b: Point) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / (dx * dx + dz * dz)


def _at(a: Point, b: Point, t: float) -> Point:
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _closest(p: Point, a: Point, b: Point) -> Point:
    return _at(a, b, max(0.0, min(1.0, _param(p, a, b))))


def _cross(a: Point, b: Point, c: Point, d: Point) -> tuple[float, float] | None:
    """Where segments ab and cd cross: (t along ab, u along cd), or None when they are parallel."""
    rx, rz, sx, sz = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    den = rx * sz - rz * sx
    if abs(den) < 1e-12:
        return None
    qx, qz = c[0] - a[0], c[1] - a[1]
    return (qx * sz - qz * sx) / den, (qx * rz - qz * rx) / den


def _graph(segs: list[tuple[Point, Point]]) -> tuple[list[Point], set[tuple[int, int]]]:
    """The walls as a planar drawing: points, and edges (i, j) with i < j. Ends within SNAP_M are one point, a wall
    is cut where another's end touches it or where two cross, and a wall drawn twice is one edge."""
    flat = _snap([p for s in segs for p in s])
    segs = [(flat[2 * k], flat[2 * k + 1]) for k in range(len(segs))]
    segs = [s for s in segs if math.dist(*s) > SNAP_M]
    verts = list(dict.fromkeys(p for s in segs for p in s))
    pieces: list[tuple[Point, Point]] = []
    for i, (a, b) in enumerate(segs):
        length = math.dist(a, b)
        cuts = [(0.0, a), (1.0, b)]
        for v in verts:
            t = _param(v, a, b)
            if (SNAP_M / length < t < 1 - SNAP_M / length and math.dist(v, _at(a, b, t)) <= SNAP_M):
                cuts.append((t, v))
        for j, (c, d) in enumerate(segs):
            if j != i and (tu := _cross(a, b, c, d)) is not None:
                t, u = tu
                lc = math.dist(c, d)
                if SNAP_M / length < t < 1 - SNAP_M / length and -SNAP_M / lc <= u <= 1 + SNAP_M / lc:
                    cuts.append((t, _at(a, b, t)))
        cuts.sort()
        pieces += [(p, q) for (_, p), (_, q) in zip(cuts, cuts[1:], strict=False)]
    flat = _snap([p for s in pieces for p in s])
    points: list[Point] = []
    index: dict[Point, int] = {}
    edges: set[tuple[int, int]] = set()
    for k in range(len(pieces)):
        ij = []
        for p in (flat[2 * k], flat[2 * k + 1]):
            if p not in index:
                index[p] = len(points)
                points.append(p)
            ij.append(index[p])
        if ij[0] != ij[1]:
            edges.add((min(ij), max(ij)))
    return points, edges


def _neighbours(points: list[Point], edges: set[tuple[int, int]]) -> dict[int, set[int]]:
    nb: dict[int, set[int]] = {i: set() for i in range(len(points))}
    for i, j in edges:
        nb[i].add(j)
        nb[j].add(i)
    return nb


def _joins(points: list[Point], edges: set[tuple[int, int]]) -> list[tuple[Point, Point]]:
    """Walls that stop short: each free end carried on along its wall to the first wall within JOIN_M, else to the
    nearest other free end within JOIN_M."""
    nb = _neighbours(points, edges)
    free = [v for v, n in nb.items() if len(n) == 1]
    done: set[int] = set()
    out: list[tuple[Point, Point]] = []
    for v in free:
        if v in done:
            continue
        (u,) = nb[v]
        p, q = points[v], points[u]
        length = math.dist(p, q)
        d = ((p[0] - q[0]) / length, (p[1] - q[1]) / length)
        far = (p[0] + d[0] * JOIN_M, p[1] + d[1] * JOIN_M)
        best = None
        for i, j in edges:
            if v in (i, j):
                continue
            if (tu := _cross(p, far, points[i], points[j])) is not None:
                t, s = tu
                if 1e-9 < t <= 1 and -1e-9 <= s <= 1 + 1e-9 and (best is None or t < best[0]):
                    best = (t, _at(p, far, t))
        if best is not None:
            hit = best[1]
            out.append((p, hit))
            done.add(v)
            done.update(w for w in free if math.dist(points[w], hit) <= SNAP_M)
            continue
        near = [w for w in free if w not in (v, u) and w not in done and 0 < math.dist(p, points[w]) <= JOIN_M]
        if near:
            w = min(near, key=lambda w: math.dist(p, points[w]))
            out.append((p, points[w]))
            done.update((v, w))
    return out


def _prune(points: list[Point], edges: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """The edges without the walls that end free (they enclose nothing)."""
    edges = set(edges)
    while True:
        nb = _neighbours(points, edges)
        loose = {v for v, n in nb.items() if len(n) == 1}
        if not loose:
            return edges
        edges = {e for e in edges if e[0] not in loose and e[1] not in loose}


def _faces(points: list[Point], edges: set[tuple[int, int]]) -> list[list[Point]]:
    """The regions the edges enclose (each as a ring, counter-clockwise in x, z); the unbounded outside of each
    drawing is not one."""
    nb = _neighbours(points, edges)
    angle = {(v, w): math.atan2(points[w][1] - points[v][1], points[w][0] - points[v][0]) for v in nb for w in nb[v]}
    seen: set[tuple[int, int]] = set()
    rings = []
    for start in sorted(angle):
        if start in seen:
            continue
        cycle = []
        u, v = start
        while (u, v) not in seen:
            seen.add((u, v))
            cycle.append(u)
            back = angle[(v, u)]
            # the sharpest left turn: the next edge clockwise from the way back
            u, v = v, min(nb[v], key=lambda w: (back - angle[(v, w)]) % (2 * math.pi) or 2 * math.pi)
        ring = _clean([points[k] for k in cycle])
        if len(ring) >= 3 and _signed(ring) > SLIVER_M2:
            rings.append(ring)
    return rings


def _signed(ring: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) / 2


def _clean(ring: list[Point]) -> list[Point]:
    """A ring without repeated corners, spikes (a wall walked there and back) or corners on a straight line."""
    ring = [(round(x, ROUND) + 0.0, round(z, ROUND) + 0.0) for x, z in ring]
    changed = True
    while changed and len(ring) >= 3:
        changed = False
        for k in range(len(ring)):
            a, b, c = ring[k - 1], ring[k], ring[(k + 1) % len(ring)]
            if a == b or a == c or math.dist(b, _closest(b, a, c)) < STRAIGHT_M:
                del ring[k]
                changed = True
                break
    return ring


# --- the plan ----------------------------------------------------------------------------------------------------

def _heights(item: dict) -> dict:
    """A door's or window's bottom and top (metres from the floor; None when the source gives none)."""
    return {k: (round(float(item[k]), ROUND) if _num(item.get(k)) else None) for k in ("bottom", "top")}


def _where(p) -> str:
    return f"({p[0]:.2f}, {p[1]:.2f})"


def _ceiling(walls: list[dict]) -> str | None:
    """What to say of the ceiling RoomPlan does not scan: VESPER builds CEILING_M; the walls' heights show the room's
    (the tallest walls; shorter ones were seen only in part)."""
    heights = sorted(float(w["height"]) for w in walls if _num(w.get("height")))
    if not heights or (heights[-1] - heights[0] <= SAME_M and abs(heights[-1] - CEILING_M) <= SAME_M):
        return None
    top = heights[-1]
    lower = [h for h in heights if h < top - SAME_M]
    if not lower:
        return f"ceiling not scanned: {CEILING_M} m assumed (the walls are {top:.2f} m high)"
    others = f"{lower[0]:.2f}" if lower[-1] - lower[0] <= SAME_M else f"{lower[0]:.2f} to {lower[-1]:.2f}"
    return (f"ceiling not scanned: {CEILING_M} m assumed (the tallest walls are {top:.2f} m high; others {others} m, "
            "seen only in part)")


def plan_from_parts(walls: list[dict], doors: list[dict], windows: list[dict], openings: list[dict],
                    sections: list[dict], objects: list[dict]) -> tuple[dict, list[dict], list[str]]:
    """A `vesper-plan/1` in metres from a scan's parts, all in plan coordinates (x, z in metres; heights from the
    floor): walls `{id, a: [x, z], b: [x, z], height}`; doors, windows and openings `{wall, at: [x, z], width, bottom,
    top}` (wall: a wall's id, or None for the nearest wall within REACH_M); sections `{label, center: [x, z]}` (a
    RoomPlan room label); objects `{category, center: [x, y, z], size: [w, h, d], yaw}` (a RoomPlan category; yaw in
    degrees from +x toward +z).

    Returns (plan, pieces, warnings). A piece is `{kind, room, box: [x0, z0, x1, z1], h, yaw}`: its kind (RoomPlan's
    category, storage as cabinet), the room it stands in, its footprint before its yaw (centre less and plus half its
    width along x and half its depth along z), its height, and the degrees it is turned about its centre from +x
    toward +z (rectify's turn, `plan.plan_transform`, adds to it). The warnings are a `ScanWarnings`, whose `heights`
    keep each plan door's and window's bottom and top. A door (or opening) joining the same rooms as an earlier one
    within TWICE_M or half the narrower one's width is that door listed twice (each room of a structure lists it):
    one door. A ScanError when the walls enclose no room."""
    warnings = ScanWarnings()
    if (said := _ceiling(walls)) is not None:
        warnings.append(said)

    segs = [(tuple(map(float, w["a"])), tuple(map(float, w["b"]))) for w in walls]
    segs = [s for s in segs if math.dist(*s) > 1e-9]
    points, edges = _graph(segs)
    if joins := _joins(points, edges):
        for p, q in joins:
            mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            warnings.append(f"the walls do not meet at {_where(mid)}: joined across {math.dist(p, q):.2f} m")
        points, edges = _graph(segs + joins)
    faces = _faces(points, _prune(points, edges))

    # rooms: the region each section lies in (the smallest, should regions nest)
    found: dict[int, list[dict]] = {}
    for s in sections:
        c = tuple(s["center"])
        inside = [k for k, f in enumerate(faces) if point_in_polygon(c, f)]
        if not inside:
            warnings.append(f"the scan's {s['label']!r} at {_where(c)} lies in no walled region: left out")
            continue
        found.setdefault(min(inside, key=lambda k: polygon_area(faces[k])), []).append(s)
    order = list(found)
    rest = sorted((k for k in range(len(faces)) if k not in found),
                  key=lambda k: (min(p[0] for p in faces[k]), min(p[1] for p in faces[k])))
    rooms_at: list[tuple[list[Point], str]] = []
    for k in order:
        labels = [s["label"] for s in found[k]]
        for label in labels:
            if label not in ROOM_TYPES:
                warnings.append(f"the scan's room label {label!r} is not a room type VESPER knows: typed other")
        types = [ROOM_TYPES.get(label, "other") for label in labels]
        named = [t for t in types if t != "other"]
        typ = named[0] if named else "other"
        if len(labels) > 1:
            listed = ", ".join(repr(x) for x in labels[:-1]) + f" and {labels[-1]!r}"
            warnings.append(f"the scan's {listed} lie in one walled room: one room, typed {typ}")
        rooms_at.append((faces[k], typ))
    for k in rest:
        size = polygon_area(faces[k])
        where = _where(min(faces[k]))
        if size < ROOM_LEAST_M2:
            warnings.append(f"a walled region of {size:.1f} m² at {where} is under {ROOM_LEAST_M2} m² and names no "
                            f"room: left out")
            continue
        if sections:
            warnings.append(f"a walled region of {size:.1f} m² at {where} has no room label in the scan: typed other")
        rooms_at.append((faces[k], "other"))
    if not rooms_at:
        raise ScanError("the scan's walls enclose no room: a room needs walls all round it")
    if not sections:
        warnings.append(f"the scan labels no rooms: {len(rooms_at)} walled regions typed other")

    rooms = []
    count: dict[str, int] = {}
    totals = {t: sum(1 for _, x in rooms_at if x == t) for _, t in rooms_at}
    for n, (ring, typ) in enumerate(rooms_at, start=1):
        count[typ] = count.get(typ, 0) + 1
        if typ == "other":
            name = f"room {count[typ]}"
        else:
            name = LABELS[typ] + (f" {count[typ]}" if totals[typ] > 1 else "")
        rooms.append({"id": f"r{n}", "name": name, "type": typ, "polygon": [list(p) for p in ring]})
    rings = [r["polygon"] for r in rooms]

    by_id = {str(w["id"]): w for w in walls if math.dist(w["a"], w["b"]) > 1e-9}  # a wall of no length is no wall

    def wall_of(item: dict) -> tuple[Point, Point] | None:
        w = by_id.get(str(item["wall"])) if item.get("wall") is not None else None
        if w is not None:
            return tuple(w["a"]), tuple(w["b"])
        at = tuple(item["at"])
        near = min(segs, key=lambda s: math.dist(at, _closest(at, *s)), default=None)
        return near if near is not None and math.dist(at, _closest(at, *near)) <= REACH_M else None

    def room_at(p: Point) -> str | None:
        hits = [k for k, ring in enumerate(rings) if point_in_polygon(p, ring)]
        return rooms[min(hits, key=lambda k: polygon_area(rings[k]))]["id"] if hits else None

    def room_near(p: Point) -> str | None:
        """The room p is in, else the room with a wall within NEAR_M of p (the nearest)."""
        if (inside := room_at(p)) is not None:
            return inside
        gaps = [(min(math.dist(p, _closest(p, tuple(a), tuple(b))) for a, b in zip(ring, ring[1:] + ring[:1],
                                                                                   strict=True)), k)
                for k, ring in enumerate(rings)]
        gap, k = min(gaps)
        return rooms[k]["id"] if gap <= NEAR_M else None

    def sides(item: dict) -> tuple[Point, list[str | None]] | None:
        seg = wall_of(item)
        if seg is None:
            return None
        a, b = seg
        q = _closest(tuple(item["at"]), a, b)
        length = math.dist(a, b)
        n = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)
        found_ = []
        for s in (1, -1):
            hit = None
            for d in PROBES_M:
                if (hit := room_at((q[0] + s * d * n[0], q[1] + s * d * n[1]))) is not None:
                    break
            found_.append(hit)
        return q, found_

    plan_doors: list[dict] = []
    leaf: list[bool] = []
    for kind, items in (("door", doors), ("opening", openings)):
        for item in items:
            if not (_num(item.get("width")) and item["width"] >= ROUND_M):
                warnings.append(f"the {kind} at {_where(item['at'])} has no width: left out")
                continue
            got = sides(item)
            if got is None:
                warnings.append(f"the {kind} at {_where(item['at'])} stands on no wall of the scan: left out")
                continue
            q, (left, right) = got
            if left is None and right is None:
                warnings.append(f"the {kind} at {_where(q)} leads from no room: left out")
                continue
            if left == right:
                warnings.append(f"the {kind} at {_where(q)} stands in a wall inside one room: left out")
                continue
            pair = [left, right] if left is not None and right is not None else [left or right, "outside"]
            width = round(float(item["width"]), ROUND)
            twice = next((k for k, d in enumerate(plan_doors) if set(d["rooms"]) == set(pair)
                          and math.dist(d["at"], q) <= max(TWICE_M, min(d["width"], width) / 2)), None)
            if twice is not None:
                warnings.append(f"the {kind} at {_where(q)} is door {twice + 1} at {_where(plan_doors[twice]['at'])} "
                                f"listed twice: one door")
                leaf[twice] = leaf[twice] or kind == "door"
                continue
            plan_doors.append({"at": [round(q[0], ROUND) + 0.0, round(q[1], ROUND) + 0.0], "width": width,
                               "rooms": pair})
            leaf.append(kind == "door")
            warnings.heights["doors"].append(_heights(item))

    plan_windows = []
    for item in windows:
        if not (_num(item.get("width")) and item["width"] >= ROUND_M):
            warnings.append(f"the window at {_where(item['at'])} has no width: left out")
            continue
        got = sides(item)
        if got is None:
            warnings.append(f"the window at {_where(item['at'])} stands on no wall of the scan: left out")
            continue
        q, (left, right) = got
        if left is None and right is None:
            warnings.append(f"the window at {_where(q)} belongs to no room: left out")
            continue
        if left is not None and right is not None:
            warnings.append(f"the window at {_where(q)} is in a wall between two rooms, not an outside wall: left out")
            continue
        plan_windows.append({"at": [round(q[0], ROUND) + 0.0, round(q[1], ROUND) + 0.0],
                             "width": round(float(item["width"]), ROUND), "room": left or right})
        warnings.heights["windows"].append(_heights(item))

    out = [k for k, d in enumerate(plan_doors) if "outside" in d["rooms"]]
    entrance = min(out, key=lambda k: (not leaf[k], plan_doors[k]["at"][0], plan_doors[k]["at"][1]), default=None)
    if entrance is None:
        warnings.append("no door leads outside: the plan has no entrance")
    elif len(out) > 1:
        warnings.append(f"{len(out)} doors lead outside: door {entrance + 1} at {_where(plan_doors[entrance]['at'])} "
                        "is the entrance (the first by position: a door before an opening, then west to east along x); "
                        "the others stay doors to the outside")

    pieces = []
    for o in objects:
        kind = OBJECT_KINDS.get(o["category"])
        c = o["center"]
        if kind is None:
            warnings.append(f"the scan's {o['category']!r} at {_where((c[0], c[2]))} is not a piece VESPER knows: "
                            f"left out")
            continue
        room = room_near((c[0], c[2]))
        if room is None:
            warnings.append(f"the scan's {o['category']} at {_where((c[0], c[2]))} stands in no room: left out")
            continue
        w, h, d = (float(v) for v in o["size"])
        yaw = (float(o["yaw"]) + 180.0) % 360.0 - 180.0
        pieces.append({"kind": kind, "room": room,
                       "box": [round(v, ROUND) + 0.0 for v in (c[0] - w / 2, c[2] - d / 2, c[0] + w / 2, c[2] + d / 2)],
                       "h": round(h, ROUND), "yaw": round(yaw, 3) + 0.0})

    plan = {"format": FORMAT, "units": "m", "scale": None, "rooms": rooms, "doors": plan_doors,
            "windows": plan_windows, "entrance": entrance}
    for r in rooms:
        if (bad := _polygon_problem(r["polygon"])) is not None:
            raise ScanError(f"the scan's walls make room {r['name']!r} a shape VESPER cannot use: {bad}")
    load_plan(plan)
    return plan, pieces, warnings
