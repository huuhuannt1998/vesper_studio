"""The floor-plan file (`vesper-plan/1`): what a floor-plan image becomes before a home is built from it.

A plan lists the rooms (a polygon each), the doors and windows, and the entrance. It comes from Claude reading an
image (in pixels, `units: "px"`) or from a hand-written file (in metres, `units: "m"`, no scale needed). Four steps,
all deterministic and offline:

- `load_plan` checks the file and says what is wrong with it, room by room;
- `scale_plan` turns pixels into metres from a stated scale (never a guessed one);
- `rectify` straightens the drawing: the plan's two dominant wall directions become the axes, near-collinear edges
  merge, gaps narrower than a wall close, overlaps between rooms are removed (the smaller room keeps the contested
  strip) and each door and window moves onto the wall it belongs to;
- `check_house` lists every way the result is not a house: a room nothing leads to, an implausible size, no way in.

Image y grows downward and the plan's z is the image's y, so no axis is flipped. Rooms are polygons; the rectifier
works on rectilinear ones (a wall at another angle than the plan's two directions is left as drawn, and a room with
one is not trimmed against its neighbours).
"""
from __future__ import annotations

import copy
import math
import re

from vesper.lab.generate import AREAS
from vesper.lab.geometry import Point, point_in_polygon, polygon_area

FORMAT = "vesper-plan/1"
ROOM_TYPES = (*AREAS, "corridor", "hall", "other")
OTHER_AREA = (2.0, 60.0)  # the plausible m² of a room type with no typical area (corridor, hall, other)
AREA_SLACK = (0.5, 1.5)  # a room's plausible area: 0.5 x the smallest typical to 1.5 x the largest

SNAP_DEG = 10.0  # an edge within this of one of the two plan directions is straightened onto it
MERGE_M = 0.15  # edges closer than this to one line are one line
GAP_M = 0.25  # a gap between two rooms' facing walls narrower than this is closed
DOOR_REACH_M = 0.5  # a door or window further than this from its wall is not on it
SLIVER_M2 = 0.05  # a leftover piece of a trimmed room smaller than this is dropped
TRIM_MAX_M = 0.5  # an overlap thicker than this is two rooms drawn into each other, not a wall drawn twice
_EPS = 1e-9

_KEYS = {"format", "units", "scale", "rooms", "doors", "windows", "entrance"}
_ROOM_KEYS = {"id", "name", "type", "polygon"}
_DOOR_KEYS = {"at", "width", "rooms"}
_WINDOW_KEYS = {"at", "width", "room"}
_SCALE_TEXT = re.compile(
    r"^\s*(?P<room>.+?)\s+(?P<dim>width|length|depth)\s+(?P<value>\d+(?:\.\d+)?|\.\d+)\s*(?:m|meters?|metres?)\s*$",
    re.IGNORECASE)


class PlanError(ValueError):
    """A plan VESPER cannot use, with what is wrong with it."""


# --- the file ----------------------------------------------------------------------------------------------------

def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_point(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(c) for c in v)


def _ring(poly) -> list[Point]:
    """A polygon's corners as tuples; a closing repeat of the first corner is dropped."""
    pts = [(float(p[0]), float(p[1])) for p in poly]
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < _EPS:
        pts.pop()
    return pts


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _within(a: Point, b: Point, c: Point) -> bool:
    """Whether c, known to be on the line ab, lies between a and b."""
    return (min(a[0], b[0]) - _EPS <= c[0] <= max(a[0], b[0]) + _EPS
            and min(a[1], b[1]) - _EPS <= c[1] <= max(a[1], b[1]) + _EPS)


def _segments_meet(a: Point, b: Point, c: Point, d: Point, eps: float) -> bool:
    """Whether the segments ab and cd share a point (crossing, touching or overlapping)."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 * o2 < -eps * eps and o3 * o4 < -eps * eps:
        return True
    return ((abs(o1) <= eps and _within(a, b, c)) or (abs(o2) <= eps and _within(a, b, d))
            or (abs(o3) <= eps and _within(c, d, a)) or (abs(o4) <= eps and _within(c, d, b)))


def _polygon_problem(poly) -> str | None:
    """What is wrong with a room's polygon, or None."""
    if not isinstance(poly, (list, tuple)) or not all(_is_point(p) for p in poly):
        return "polygon is not a list of [x, y] points"
    ring = _ring(poly)
    n = len(ring)
    if n < 3:
        return "polygon is open: it needs at least 3 distinct points, with the last joining the first"
    if any(math.dist(ring[k], ring[(k + 1) % n]) < _EPS for k in range(n)):
        return "polygon repeats a point"
    extent = max(max(p[0] for p in ring) - min(p[0] for p in ring), max(p[1] for p in ring) - min(p[1] for p in ring))
    eps = _EPS * max(1.0, extent) ** 2
    if all(abs(_orient(ring[0], ring[1], c)) <= eps for c in ring[2:]):
        return "polygon has no area (its points lie on a line)"
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        # the next edge doubling straight back on this one is a spike
        c = ring[(i + 2) % n]
        if abs(_orient(a, b, c)) <= eps and (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0:
            return "polygon crosses itself (an edge doubles back on the one before it)"
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # the closing edge is next to the first
            if _segments_meet(a, b, ring[j], ring[(j + 1) % n], eps):
                return f"polygon crosses itself (edges {i} and {j} meet)"
    return "polygon has no area" if polygon_area(ring) <= eps else None


def _scale_problem(scale) -> str | None:
    if scale is None:
        return None
    if not isinstance(scale, dict):
        return "scale: not an object"
    forms = {"m_per_px": {"m_per_px"}, "area_m2": {"area_m2"}, "length_m": {"length_m", "from", "to"}}
    for first, keys in forms.items():
        if first in scale:
            if set(scale) != keys:
                return f"scale: {first!r} stands alone or with its own keys ({', '.join(sorted(keys))}), not mixed"
            if not _num(scale[first]) or scale[first] <= 0:
                return f"scale: {first} must be a number above 0"
            if first == "length_m":
                if not (_is_point(scale["from"]) and _is_point(scale["to"])):
                    return "scale: 'from' and 'to' must be [x, y] points"
                if math.dist(scale["from"], scale["to"]) < _EPS:
                    return "scale: 'from' and 'to' are the same point"
            return None
    return "scale: expected {m_per_px}, {length_m, from, to}, {area_m2} or null"


def load_plan(raw: dict) -> dict:
    """Check a `vesper-plan/1` file and return it unchanged, or raise a PlanError listing everything wrong."""
    if not isinstance(raw, dict):
        raise PlanError("plan: the file is not a JSON object")
    errs: list[str] = []
    for k in sorted(set(raw) - _KEYS, key=str):
        errs.append(f"plan: unknown key {k!r}")
    for k in ("format", "units", "rooms"):
        if k not in raw:
            errs.append(f"plan: missing key {k!r}")
    if "format" in raw and raw["format"] != FORMAT:
        errs.append(f"plan: format {raw['format']!r} is not {FORMAT!r}")
    if "units" in raw and raw["units"] not in ("px", "m"):
        errs.append(f"plan: units {raw['units']!r} is not 'px' or 'm'")
    if (bad := _scale_problem(raw.get("scale"))) is not None:
        errs.append(f"plan: {bad}")

    rooms = raw.get("rooms")
    ids: set[str] = set()
    if "rooms" in raw:
        if not isinstance(rooms, list) or not rooms:
            errs.append("plan: rooms must be a non-empty list")
            rooms = []
        for k, r in enumerate(rooms):
            if not isinstance(r, dict):
                errs.append(f"room {k}: not an object")
                continue
            rid = r.get("id")
            label = f"room {rid!r}" if isinstance(rid, str) and rid else f"room {k}"
            if set(r) != _ROOM_KEYS:
                errs.append(f"{label}: keys must be exactly {', '.join(sorted(_ROOM_KEYS))}")
            if not isinstance(rid, str) or not rid or rid == "outside":
                errs.append(f"{label}: id must be a non-empty string other than 'outside'")
            elif rid in ids:
                errs.append(f"plan: duplicate room id {rid!r}")
            else:
                ids.add(rid)
            if "name" in r and not isinstance(r["name"], str):
                errs.append(f"{label}: name must be a string")
            if "type" in r and r["type"] not in ROOM_TYPES:
                errs.append(f"{label}: type {r['type']!r} is not one of {', '.join(ROOM_TYPES)}")
            if "polygon" in r and (bad := _polygon_problem(r["polygon"])) is not None:
                errs.append(f"{label}: {bad}")

    doors = raw.get("doors", [])
    if not isinstance(doors, list):
        errs.append("plan: doors must be a list")
        doors = []
    for k, d in enumerate(doors):
        if not isinstance(d, dict) or set(d) != _DOOR_KEYS:
            errs.append(f"door {k}: must be an object with exactly {', '.join(sorted(_DOOR_KEYS))}")
            continue
        if not _is_point(d["at"]):
            errs.append(f"door {k}: at must be an [x, y] point")
        if not _num(d["width"]) or d["width"] <= 0:
            errs.append(f"door {k}: width must be a number above 0")
        sides = d["rooms"]
        if not isinstance(sides, list) or len(sides) != 2 or not all(isinstance(s, str) for s in sides):
            errs.append(f"door {k}: rooms must be two room ids (or 'outside')")
            continue
        for s in sides:
            if s != "outside" and s not in ids:
                errs.append(f"door {k}: room {s!r} is not in the plan")
        if sides[0] == sides[1]:
            errs.append(f"door {k}: joins {sides[0]!r} to itself")

    windows = raw.get("windows", [])
    if not isinstance(windows, list):
        errs.append("plan: windows must be a list")
        windows = []
    for k, w in enumerate(windows):
        if not isinstance(w, dict) or set(w) != _WINDOW_KEYS:
            errs.append(f"window {k}: must be an object with exactly {', '.join(sorted(_WINDOW_KEYS))}")
            continue
        if not _is_point(w["at"]):
            errs.append(f"window {k}: at must be an [x, y] point")
        if not _num(w["width"]) or w["width"] <= 0:
            errs.append(f"window {k}: width must be a number above 0")
        if not isinstance(w["room"], str) or w["room"] not in ids:
            errs.append(f"window {k}: room {w['room']!r} is not in the plan")

    ent = raw.get("entrance")
    if ent is not None and (isinstance(ent, bool) or not isinstance(ent, int) or not 0 <= ent < len(doors)):
        errs.append(f"plan: entrance {ent!r} is not the index of one of the {len(doors)} doors")
    if errs:
        raise PlanError("; ".join(errs))
    return raw


# --- the plan's directions ---------------------------------------------------------------------------------------

def _skew(rings: list[list[Point]]) -> float:
    """The angle, in degrees within (-45, 45], by which the plan's dominant wall direction (the nearer one to the
    image's horizontal) is turned: a histogram of edge angles in 1-degree bins, edge lengths as weights. Both of the
    plan's directions (a quarter turn apart) fall in the same bin."""
    bins = [0.0] * 90
    edges: list[tuple[float, float]] = []
    for ring in rings:
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            length = math.dist(a, b)
            if length < _EPS:
                continue
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90.0
            bins[int(ang) % 90] += length
            edges.append((ang, length))
    if not edges:
        return 0.0
    peak = max(range(90), key=lambda k: bins[k - 1] + bins[k] + bins[(k + 1) % 90])
    centre = peak + 0.5
    sx = sy = 0.0
    for ang, length in edges:
        if abs((ang - centre + 45.0) % 90.0 - 45.0) <= 3.0:
            sx += length * math.cos(math.radians(4 * ang))
            sy += length * math.sin(math.radians(4 * ang))
    mean = (math.degrees(math.atan2(sy, sx)) / 4.0) % 90.0
    if mean > 45.0:
        mean -= 90.0
    return 0.0 if abs(mean) < 1e-9 else mean


def _rotate(p: Point, about: Point, degrees: float) -> Point:
    t = math.radians(degrees)
    dx, dz = p[0] - about[0], p[1] - about[1]
    return (about[0] + dx * math.cos(t) - dz * math.sin(t), about[1] + dx * math.sin(t) + dz * math.cos(t))


# --- scale -------------------------------------------------------------------------------------------------------

def _find_room(plan: dict, text: str) -> dict:
    """The room a scale text names: by name first, then by type."""
    want = text.strip().lower()
    for field in ("name", "type"):
        found = [r for r in plan["rooms"] if r[field].strip().lower() == want]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            ids = ", ".join(repr(r["id"]) for r in found)
            raise PlanError(f"scale: {want!r} is the {field} of {len(found)} rooms ({ids}); name one of them")
    names = ", ".join(repr(r["name"]) for r in plan["rooms"])
    raise PlanError(f"scale: {want!r} matches no room name or type (the rooms are {names})")


def _factor_from_text(plan: dict, text: str) -> float:
    m = _SCALE_TEXT.match(text)
    if not m:
        raise PlanError(f"scale: cannot read {text!r}; write it like 'kitchen width 3.2 m' (width, length or depth)")
    value = float(m["value"])
    if value <= 0:
        raise PlanError(f"scale: {text!r} is not a size above 0")
    r = _find_room(plan, m["room"])
    ring = _ring(r["polygon"])
    theta = math.radians(_skew([_ring(q["polygon"]) for q in plan["rooms"]]))
    u = (math.cos(theta), math.sin(theta)) if m["dim"].lower() == "width" else (-math.sin(theta), math.cos(theta))
    proj = [p[0] * u[0] + p[1] * u[1] for p in ring]
    drawn = max(proj) - min(proj)
    if drawn < _EPS:
        raise PlanError(f"scale: room {r['id']!r} has no {m['dim'].lower()} to scale by")
    return value / drawn


def _total_area(plan: dict) -> float:
    return sum(polygon_area(_ring(r["polygon"])) for r in plan["rooms"])


def scale_plan(plan: dict, scale: str | None = None, area_m2: float | None = None) -> dict:
    """The plan in metres. `scale` is a room dimension ("kitchen width 3.2 m": the room by name, then by type; width
    along the plan's first direction, length or depth along the other) and `area_m2` the whole plan's floor area;
    either one beats the scale written in the file. A plan in pixels with none of them is refused, never guessed; a
    plan already in metres needs none."""
    load_plan(plan)
    if scale is not None and area_m2 is not None:
        raise PlanError("scale: give --scale or --area, not both")
    if area_m2 is not None and (isinstance(area_m2, bool) or not _num(area_m2) or area_m2 <= 0):
        raise PlanError(f"scale: area {area_m2!r} is not a number of square metres above 0")
    stated = plan.get("scale")
    factor: float
    if scale is not None:
        factor = _factor_from_text(plan, scale)
    elif area_m2 is not None:
        factor = math.sqrt(area_m2 / _total_area(plan))
    elif plan["units"] == "m":
        factor = 1.0
    elif stated is None:
        raise PlanError("no scale: give --scale or --area (a room dimension like 'kitchen width 3.2 m', or the "
                        "home's floor area in m²)")
    elif "m_per_px" in stated:
        factor = float(stated["m_per_px"])
    elif "length_m" in stated:
        factor = float(stated["length_m"]) / math.dist(stated["from"], stated["to"])
    else:
        factor = math.sqrt(float(stated["area_m2"]) / _total_area(plan))
    out = _normal(plan)
    for r in out["rooms"]:
        r["polygon"] = [[x * factor, z * factor] for x, z in r["polygon"]]
    for d in out["doors"]:
        d["at"] = [d["at"][0] * factor, d["at"][1] * factor]
        d["width"] *= factor
    for w in out["windows"]:
        w["at"] = [w["at"][0] * factor, w["at"][1] * factor]
        w["width"] *= factor
    out["units"], out["scale"] = "m", None
    return out


def _normal(plan: dict) -> dict:
    """A deep copy with every key present."""
    out = copy.deepcopy(plan)
    out.setdefault("scale", None)
    out.setdefault("doors", [])
    out.setdefault("windows", [])
    out.setdefault("entrance", None)
    return out


# --- rectilinear rings -------------------------------------------------------------------------------------------
# After the plan is turned square, a wall is 'h' (along x, at z = offset), 'v' (along z, at x = offset) or None
# (a wall at another angle, left as drawn).

def _cls(a: Point, b: Point) -> str | None:
    """'h' or 'v' for a wall within SNAP_DEG of an axis whose far end would move by no more than MERGE_M when it is
    straightened about its middle (a long wall a few degrees off is left as drawn); None otherwise."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    ang = math.degrees(math.atan2(abs(dz), abs(dx)))  # 0 along x, 90 along z
    length = math.hypot(dx, dz)
    if ang <= SNAP_DEG:
        return "h" if length * math.sin(math.radians(ang)) / 2 <= MERGE_M else None
    if ang >= 90.0 - SNAP_DEG:
        return "v" if length * math.sin(math.radians(90.0 - ang)) / 2 <= MERGE_M else None
    return None


def _rebuild(starts: list[Point], cls: list, off: list) -> list[Point]:
    """The corners of a ring whose edge k starts at `starts[k]`, lies on the line (cls[k], off[k])."""
    n = len(starts)
    out: list[Point] = []
    for k in range(n):
        a, b = (k - 1) % n, k
        p = starts[k]
        if cls[a] is not None and cls[b] is not None and cls[a] != cls[b]:
            h, v = (a, b) if cls[a] == "h" else (b, a)
            q = (off[v], off[h])
        elif cls[b] is not None:
            q = (p[0], off[b]) if cls[b] == "h" else (off[b], p[1])
        elif cls[a] is not None:
            q = (p[0], off[a]) if cls[a] == "h" else (off[a], p[1])
        else:
            q = p
        out.append((round(q[0], 9) + 0.0, round(q[1], 9) + 0.0))
    return out


def _straighten(ring: list[Point]) -> list[Point] | None:
    """The ring with its walls on the plan's two directions, consecutive walls on one line merged, and steps
    narrower than MERGE_M ironed out; None when nothing of a polygon is left."""
    n = len(ring)
    groups = []  # [class, offset, length, start corner]
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]
        c = _cls(a, b)
        off = (a[1] + b[1]) / 2 if c == "h" else (a[0] + b[0]) / 2 if c == "v" else None
        groups.append([c, off, math.dist(a, b), a])
    while len(groups) >= 3:
        for i in range(len(groups)):
            j = (i + 1) % len(groups)
            if groups[i][0] is not None and groups[i][0] == groups[j][0]:
                gi, gj = groups[i], groups[j]
                total = gi[2] + gj[2]
                gi[1] = (gi[1] * gi[2] + gj[1] * gj[2]) / total if total > 0 else gi[1]
                gi[2] = total
                del groups[j]
                break
        else:
            for i, g in enumerate(groups):
                prev, nxt = groups[i - 1], groups[(i + 1) % len(groups)]
                if (g[2] < MERGE_M and prev[0] is not None and prev[0] == nxt[0] and len(groups) > 4
                        and abs(prev[1] - nxt[1]) <= MERGE_M):
                    del groups[i]
                    break
            else:
                break
    if len(groups) < 3:
        return None
    return _rebuild([g[3] for g in groups], [g[0] for g in groups], [g[1] for g in groups])


def _edges(ring: list[Point]) -> list[dict]:
    """Each wall of a (straightened) ring: its class, offset, extent along the wall, which side the room is on
    (+1 toward larger offsets), and its end points."""
    sigma = 1.0 if sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) > 0 else -1.0
    out = []
    for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
        c = _cls(a, b)
        if c == "h":
            e = {"cls": "h", "off": a[1], "t0": min(a[0], b[0]), "t1": max(a[0], b[0]),
                 "s": int(sigma * (1 if b[0] > a[0] else -1))}
        elif c == "v":
            e = {"cls": "v", "off": a[0], "t0": min(a[1], b[1]), "t1": max(a[1], b[1]),
                 "s": int(-sigma * (1 if b[1] > a[1] else -1))}
        else:
            e = {"cls": None, "off": None, "t0": 0.0, "t1": 0.0, "s": 0}
        e["a"], e["b"] = a, b
        out.append(e)
    return out


def _is_rectilinear(ring: list[Point]) -> bool:
    return all(abs(a[0] - b[0]) < _EPS or abs(a[1] - b[1]) < _EPS
               for a, b in zip(ring, ring[1:] + ring[:1], strict=True))


def _grid(*rings: list[Point]) -> tuple[list[float], list[float]]:
    xs = sorted({round(p[0], 9) for r in rings for p in r})
    zs = sorted({round(p[1], 9) for r in rings for p in r})
    return xs, zs


def _cells(ring: list[Point], xs: list[float], zs: list[float]) -> set[tuple[int, int]]:
    return {(i, j) for i in range(len(xs) - 1) for j in range(len(zs) - 1)
            if point_in_polygon(((xs[i] + xs[i + 1]) / 2, (zs[j] + zs[j + 1]) / 2), ring)}


def _cell_area(cells: set[tuple[int, int]], xs: list[float], zs: list[float]) -> float:
    return sum((xs[i + 1] - xs[i]) * (zs[j + 1] - zs[j]) for i, j in cells)


def _overlap_area(a: list[Point], b: list[Point]) -> float:
    """The area two rooms share: exact for rectilinear rooms, sampled (5 cm, coarser for very large rooms) for
    others."""
    if _is_rectilinear(a) and _is_rectilinear(b):
        xs, zs = _grid(a, b)
        return _cell_area(_cells(a, xs, zs) & _cells(b, xs, zs), xs, zs)
    x0, x1 = max(min(p[0] for p in a), min(p[0] for p in b)), min(max(p[0] for p in a), max(p[0] for p in b))
    z0, z1 = max(min(p[1] for p in a), min(p[1] for p in b)), min(max(p[1] for p in a), max(p[1] for p in b))
    if x1 <= x0 or z1 <= z0:
        return 0.0
    step = max(0.05, math.sqrt((x1 - x0) * (z1 - z0) / 40000))
    nx, nz = int((x1 - x0) / step), int((z1 - z0) / step)
    hits = 0
    for i in range(nx):
        for j in range(nz):
            c = (x0 + (i + 0.5) * step, z0 + (j + 0.5) * step)
            if point_in_polygon(c, a) and point_in_polygon(c, b):
                hits += 1
    return hits * step * step


def _loops(cells: set[tuple[int, int]], xs: list[float], zs: list[float]) -> list[list[Point]]:
    """The boundary loops of a set of grid cells; counter-clockwise ones are outlines, clockwise ones holes."""
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, j in cells:
        if (i, j - 1) not in cells:
            nxt.setdefault((i, j), []).append((i + 1, j))
        if (i + 1, j) not in cells:
            nxt.setdefault((i + 1, j), []).append((i + 1, j + 1))
        if (i, j + 1) not in cells:
            nxt.setdefault((i + 1, j + 1), []).append((i, j + 1))
        if (i - 1, j) not in cells:
            nxt.setdefault((i, j + 1), []).append((i, j))
    loops = []
    while nxt:
        start = min(nxt)
        cur, path = start, [start]
        heading = None
        while True:
            options = nxt[cur]
            if heading is not None and len(options) > 1:  # a pinch: keep to the cell being walked (turn left)
                options = sorted(options, key=lambda o: -math.atan2(
                    heading[0] * (o[1] - cur[1]) - heading[1] * (o[0] - cur[0]),
                    heading[0] * (o[0] - cur[0]) + heading[1] * (o[1] - cur[1])))
            step = options[0]
            nxt[cur].remove(step)
            if not nxt[cur]:
                del nxt[cur]
            heading = (step[0] - cur[0], step[1] - cur[1])
            cur = step
            if cur == start:
                break
            path.append(cur)
        loops.append([(xs[i], zs[j]) for i, j in path])
    return loops


def _signed_area(ring: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) / 2


def _trim(big_id: str, big: list[Point], small_id: str, small: list[Point]) -> list[Point]:
    """`big` without `small`; a PlanError when that leaves a hole or splits the room."""
    xs, zs = _grid(big, small)
    loops = _loops(_cells(big, xs, zs) - _cells(small, xs, zs), xs, zs)
    outlines = [lp for lp in loops if _signed_area(lp) > 0]
    if len(outlines) < len(loops):
        raise PlanError(f"rooms {big_id!r} and {small_id!r} overlap so that {small_id!r} lies inside {big_id!r}; "
                        f"move or resize one of them")
    outlines = [lp for lp in outlines if _signed_area(lp) >= SLIVER_M2]
    if len(outlines) != 1:
        raise PlanError(f"rooms {big_id!r} and {small_id!r} overlap so that {small_id!r} would cut {big_id!r} "
                        f"in {len(outlines)} pieces; move or resize one of them")
    return outlines[0]


# --- the shared wall ---------------------------------------------------------------------------------------------

def _seg(e: dict, lo: float, hi: float) -> tuple[Point, Point]:
    return ((lo, e["off"]), (hi, e["off"])) if e["cls"] == "h" else ((e["off"], lo), (e["off"], hi))


def _shared_walls(a: list[Point], b: list[Point]) -> list[tuple[Point, Point]]:
    """The stretches of wall two rooms have in common: the same line, the rooms on opposite sides."""
    out = []
    for ea in _edges(a):
        for eb in _edges(b):
            if (ea["cls"] is not None and ea["cls"] == eb["cls"] and abs(ea["off"] - eb["off"]) < 1e-6
                    and ea["s"] == -eb["s"]):
                lo, hi = max(ea["t0"], eb["t0"]), min(ea["t1"], eb["t1"])
                if hi - lo > 1e-6:
                    out.append(_seg(ea, lo, hi))
    return out


def _outside_walls(k: int, rings: list[list[Point]]) -> list[tuple[Point, Point]]:
    """The stretches of room k's wall that no other room shares."""
    others = [_edges(r) for i, r in enumerate(rings) if i != k]
    out = []
    for e in _edges(rings[k]):
        if e["cls"] is None:
            out.append((e["a"], e["b"]))
            continue
        covered = sorted((max(e["t0"], o["t0"]), min(e["t1"], o["t1"])) for es in others for o in es
                         if o["cls"] == e["cls"] and abs(o["off"] - e["off"]) < 1e-6 and o["s"] == -e["s"]
                         and min(e["t1"], o["t1"]) - max(e["t0"], o["t0"]) > 1e-6)
        at = e["t0"]
        for lo, hi in covered:
            if lo - at > 1e-6:
                out.append(_seg(e, at, lo))
            at = max(at, hi)
        if e["t1"] - at > 1e-6:
            out.append(_seg(e, at, e["t1"]))
    return [s for s in out if math.dist(*s) > 1e-6]


def _closest(a: Point, b: Point, p: Point) -> Point:
    dx, dz = b[0] - a[0], b[1] - a[1]
    t = 0.0 if dx == dz == 0 else ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / (dx * dx + dz * dz)
    t = max(0.0, min(1.0, t))
    return (a[0] + t * dx, a[1] + t * dz)


def _nearest(segs: list[tuple[Point, Point]], p: Point, width: float) -> tuple[Point, float] | None:
    """Where an opening `width` wide goes on the walls: the point of the wall nearest p, moved along the wall until
    its centre is half a width from each end of the stretch; and how far p was from the wall. Stretches shorter than
    the opening do not count; None when none is long enough."""
    best: tuple[Point, float] | None = None
    for a, b in segs:
        length = math.dist(a, b)
        if length < width - 1e-6:
            continue
        q = _closest(a, b, p)
        d = math.dist(p, q)
        f = min(0.5, (width / 2) / length)
        dx, dz = b[0] - a[0], b[1] - a[1]
        q = _closest((a[0] + dx * f, a[1] + dz * f), (b[0] - dx * f, b[1] - dz * f), q)
        if best is None or d < best[1]:
            best = (q, d)
    return best


def _longest(segs: list[tuple[Point, Point]]) -> float:
    return max((math.dist(a, b) for a, b in segs), default=0.0)


# --- rectify -----------------------------------------------------------------------------------------------------

def _turn(rings: list[list[Point]]) -> tuple[float, Point]:
    points = [p for r in rings for p in r]
    about = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
    skew = _skew(rings)
    return (-skew if skew != 0.0 else 0.0), about


def plan_transform(plan_m: dict) -> dict:
    """The turn `rectify` applies first, for drawing the original over the rectified plan: every point (x, z) of the
    input goes to (cx + dx cos a - dz sin a, cz + dx sin a + dz cos a) with dx, dz = x - cx, z - cz, a =
    angle_deg in degrees and (cx, cz) = centre. Straightening then moves walls by at most a few centimetres."""
    load_plan(plan_m)
    angle, about = _turn([_ring(r["polygon"]) for r in plan_m["rooms"]])
    return {"angle_deg": angle, "centre": [about[0], about[1]]}


def rectify(plan_m: dict) -> dict:
    """The plan straightened. In order: the plan is turned so its dominant wall direction is the x axis; every wall
    within 10 degrees of an axis is made exactly parallel to it, consecutive walls on one line become one wall, and
    steps narrower than 15 cm are ironed out; where two rooms overlap the larger loses the strip (the smaller keeps
    it); a gap narrower than 25 cm between two rooms' facing walls closes to its middle, and walls of different rooms
    within 15 cm of one line become one line; each door moves onto the wall its two rooms share (or the outside wall
    of its room), each window onto an outside wall of its room. A door or window more than 50 cm from its wall, or a
    door between rooms with no wall in common, is a PlanError naming them."""
    load_plan(plan_m)
    if plan_m["units"] != "m":
        raise PlanError("rectify: the plan is in pixels; scale it to metres first (scale_plan)")
    plan = _normal(plan_m)
    ids = [r["id"] for r in plan["rooms"]]
    rings = [_ring(r["polygon"]) for r in plan["rooms"]]
    doors, windows = plan["doors"], plan["windows"]

    # 1. turn the plan square
    angle, about = _turn(rings)
    if angle != 0.0:
        rings = [[_rotate(p, about, angle) for p in r] for r in rings]
        for item in (*doors, *windows):
            item["at"] = list(_rotate(tuple(item["at"]), about, angle))

    # 2. straighten each room
    def tidy(k: int, ring: list[Point]) -> list[Point]:
        out = _straighten(ring)
        if out is None or (bad := _polygon_problem([list(p) for p in out])) is not None:
            raise PlanError(f"room {ids[k]!r}: nothing of a room is left after straightening"
                            + (f" ({bad})" if out is not None else ""))
        return out
    rings = [tidy(k, r) for k, r in enumerate(rings)]

    # 3. overlaps: the smaller room keeps the strip (a thin one: a thicker one is two rooms drawn into each other)
    areas = [polygon_area(r) for r in rings]
    for i in range(len(rings)):
        for j in range(i + 1, len(rings)):
            a, b = rings[i], rings[j]
            if not (_is_rectilinear(a) and _is_rectilinear(b)):
                continue
            if (max(p[0] for p in a) <= min(p[0] for p in b) or max(p[0] for p in b) <= min(p[0] for p in a)
                    or max(p[1] for p in a) <= min(p[1] for p in b) or max(p[1] for p in b) <= min(p[1] for p in a)):
                continue
            xs, zs = _grid(a, b)
            both = _cells(a, xs, zs) & _cells(b, xs, zs)
            area = _cell_area(both, xs, zs)
            if area <= _EPS:
                continue
            big, small = (i, j) if areas[i] >= areas[j] else (j, i)
            loops = _loops(both, xs, zs)
            perimeter = sum(math.dist(p, q) for lp in loops for p, q in zip(lp, lp[1:] + lp[:1], strict=True))
            if 2 * area / perimeter > TRIM_MAX_M:
                pts = [p for lp in loops for p in lp]
                depth = min(max(p[0] for p in pts) - min(p[0] for p in pts),
                            max(p[1] for p in pts) - min(p[1] for p in pts))
                verb = "lies inside" if area >= 0.95 * areas[small] else "overlaps"
                raise PlanError(f"room {ids[small]!r} {verb} {ids[big]!r} by {depth:.1f} m: redraw them as "
                                f"separate rooms")
            rings[big] = tidy(big, _trim(ids[big], rings[big], ids[small], rings[small]))

    # 4. gaps and near-collinear walls between rooms: walls that should be one line are joined (closest first, a line
    # never spanning more than a gap) and the line is put in the middle of them
    info = [_edges(r) for r in rings]
    walls = [(k, i) for k, es in enumerate(info) for i, e in enumerate(es) if e["cls"] is not None]
    pairs = []
    door_at = {}  # the pairs of rooms a door joins -> where the doors are
    for d in doors:
        if "outside" not in d["rooms"]:
            door_at.setdefault(frozenset(ids.index(r) for r in d["rooms"]), []).append(tuple(d["at"]))
    for x, (ka, ia) in enumerate(walls):
        for kb, ib in walls[x + 1:]:
            ea, eb = info[ka][ia], info[kb][ib]
            if ka == kb or ea["cls"] != eb["cls"]:
                continue
            along = min(ea["t1"], eb["t1"]) - max(ea["t0"], eb["t0"])
            if ea["s"] == eb["s"]:  # both rooms on the same side: one wall drawn twice
                if abs(ea["off"] - eb["off"]) <= MERGE_M and along > -0.3:
                    pairs.append((abs(ea["off"] - eb["off"]), (ka, ia), (kb, ib), GAP_M))
            else:  # the rooms back to back: a wall drawn as a gap between them (or as a sliver of overlap)
                inner, outer = (ea, eb) if ea["s"] == -1 else (eb, ea)
                gap = outer["off"] - inner["off"]
                if along <= 0.1 or gap >= DOOR_REACH_M or gap <= -MERGE_M:
                    continue
                if gap < GAP_M:
                    pairs.append((abs(gap), (ka, ia), (kb, ib), GAP_M))
                else:  # a wider gap closes only where a door between the two rooms opens onto it
                    lo, hi = max(ea["t0"], eb["t0"]), min(ea["t1"], eb["t1"])
                    a, b = _seg({"cls": ea["cls"], "off": (inner["off"] + outer["off"]) / 2}, lo, hi)
                    if any(math.dist(p, _closest(a, b, p)) <= DOOR_REACH_M
                           for p in door_at.get(frozenset((ka, kb)), [])):
                        pairs.append((gap, (ka, ia), (kb, ib), DOOR_REACH_M))
    parent = {w: w for w in walls}
    span = {w: (info[w[0]][w[1]]["off"],) * 2 for w in walls}

    def root(w):
        while parent[w] != w:
            parent[w] = parent[parent[w]]
            w = parent[w]
        return w
    for _, wa, wb, limit in sorted(pairs):
        ra, rb = root(wa), root(wb)
        if ra == rb:
            continue
        lo, hi = min(span[ra][0], span[rb][0]), max(span[ra][1], span[rb][1])
        if hi - lo < limit:
            parent[rb] = ra
            span[ra] = (lo, hi)
    for w in walls:
        lo, hi = span[root(w)]
        info[w[0]][w[1]]["off"] = (lo + hi) / 2
    rings = [tidy(k, _rebuild([e["a"] for e in es], [e["cls"] for e in es], [e["off"] for e in es]))
             for k, es in enumerate(info)]

    # 5. doors and windows onto their walls
    for k, d in enumerate(doors):
        a, b = d["rooms"]
        at = tuple(d["at"])
        if "outside" in d["rooms"]:
            room = b if a == "outside" else a
            segs = _outside_walls(ids.index(room), rings)
            what = f"door {k} between {a!r} and {b!r}"
            if not segs:
                raise PlanError(f"{what}: {room!r} has no outside wall")
            wall = "the nearest outside wall"
        else:
            segs = _shared_walls(rings[ids.index(a)], rings[ids.index(b)])
            what = f"door {k} between {a!r} and {b!r}"
            if not segs:
                raise PlanError(f"{what}: the rooms do not touch (they have no wall in common)")
            wall = "their shared wall"
        hit = _nearest(segs, at, d["width"])
        if hit is None:
            raise PlanError(f"{what}: the door ({d['width']:.2f} m) is wider than the wall it belongs to "
                            f"(at most {_longest(segs):.2f} m)")
        q, dist = hit
        if dist > DOOR_REACH_M + 1e-9:
            raise PlanError(f"{what}: it is {dist:.2f} m from {wall} (more than {DOOR_REACH_M} m)")
        d["at"] = [round(q[0], 9) + 0.0, round(q[1], 9) + 0.0]
    for k, w in enumerate(windows):
        segs = _outside_walls(ids.index(w["room"]), rings)
        hit = _nearest(segs, tuple(w["at"]), w["width"]) if segs else None
        if segs and hit is None:
            raise PlanError(f"window {k} of {w['room']!r}: the window ({w['width']:.2f} m) is wider than the wall it "
                            f"belongs to (at most {_longest(segs):.2f} m)")
        if hit is None or hit[1] > DOOR_REACH_M + 1e-9:
            raise PlanError(f"window {k} of {w['room']!r}: it is not within {DOOR_REACH_M} m of an outside wall "
                            f"of the room")
        w["at"] = [round(hit[0][0], 9) + 0.0, round(hit[0][1], 9) + 0.0]

    for r, ring in zip(plan["rooms"], rings, strict=True):
        r["polygon"] = [[x, z] for x, z in ring]
    return plan


# --- is it a house -----------------------------------------------------------------------------------------------

def check_house(plan_m: dict) -> list[str]:
    """Every way a plan in metres is not a house (an empty list when it is): rooms that overlap, a room whose area is
    outside [0.5 x the smallest, 1.5 x the largest] of its type's typical area (`generate.AREAS`; 2 to 60 m² for
    types with none), no entrance (a door to the outside), and rooms not reachable from it through doors."""
    load_plan(plan_m)
    if plan_m["units"] != "m":
        raise PlanError("check_house: the plan is in pixels; scale it to metres first (scale_plan)")
    problems: list[str] = []
    rooms, doors = plan_m["rooms"], plan_m.get("doors", [])
    rings = {r["id"]: _ring(r["polygon"]) for r in rooms}
    for r in rooms:
        lo, hi = ((AREA_SLACK[0] * AREAS[r["type"]][0], AREA_SLACK[1] * AREAS[r["type"]][1])
                  if r["type"] in AREAS else OTHER_AREA)
        area = polygon_area(rings[r["id"]])
        if not lo <= area <= hi:
            problems.append(f"room {r['id']!r} ({r['type']}): {area:.1f} m² is outside the plausible {lo:.1f} to "
                            f"{hi:.1f} m² for its type")
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            ra, rb = rings[a["id"]], rings[b["id"]]
            if (over := _overlap_area(ra, rb)) > SLIVER_M2:
                problems.append(f"rooms {a['id']!r} and {b['id']!r} overlap by {over:.2f} m²")
    ent = plan_m.get("entrance")
    if ent is None:
        problems.append("no entrance: the plan names no door as the entrance")
        return problems
    door = doors[ent]
    if "outside" not in door["rooms"]:
        problems.append(f"entrance door {ent} joins {door['rooms'][0]!r} and {door['rooms'][1]!r}, not a room and "
                        f"the outside")
    graph: dict[str, set[str]] = {r["id"]: set() for r in rooms}
    for d in doors:
        a, b = d["rooms"]
        if a != "outside" and b != "outside":
            graph[a].add(b)
            graph[b].add(a)
    seen = {s for s in door["rooms"] if s != "outside"}
    todo = list(seen)
    while todo:
        for nb in graph[todo.pop()]:
            if nb not in seen:
                seen.add(nb)
                todo.append(nb)
    problems.extend(f"room {r['id']!r} is not reachable from the entrance through doors"
                    for r in rooms if r["id"] not in seen)
    return problems

