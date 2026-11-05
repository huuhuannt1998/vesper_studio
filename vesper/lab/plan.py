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
import itertools
import math
import re
import statistics

from vesper.lab.generate import AREAS
from vesper.lab.geometry import Point, point_in_polygon, polygon_area

FORMAT = "vesper-plan/1"
ROOM_TYPES = (*AREAS, "corridor", "hall", "balcony", "other")
OUTDOOR_TYPES = ("balcony",)  # outdoor rooms (a balcony, terrace, patio or loggia): built like a porch, no ceiling
OTHER_AREA = (2.0, 60.0)  # the plausible m² of a room type with no typical area (corridor, hall, other)
AREA_FLOOR = {"bathroom": 1.0, "other": 0.5}  # the least m² of a WC typed bathroom, and of a closet or store
AREA_SLACK = (0.5, 1.5)  # a room's plausible area: 0.5 x the smallest typical to 1.5 x the largest
ROOM_LEAST_M2 = 0.5  # an indoor room smaller than this is a mis-read, not a room
SCALE_RANGE = (0.4, 2.5)  # the rooms' median area / their type's typical (middle) outside this: the scale is wrong

SNAP_DEG = 10.0  # an edge within this of one of the two plan directions is straightened onto it
MERGE_M = 0.15  # edges closer than this to one line are one line
GAP_M = 0.25  # a gap between two rooms' facing walls narrower than this is closed
DOOR_REACH_M = 0.5  # a door or window further than this from its wall is not on it
SLIVER_M2 = 0.05  # a leftover piece of a trimmed room smaller than this is dropped
MAX_PASSES = 8  # at most this many rounds of closing wall gaps
TRIM_MAX_M = 0.5  # an overlap thicker than this is two rooms drawn into each other, not a wall drawn twice
_EPS = 1e-9
MAX_ABS = 1e9  # no coordinate, width or scale of a plan is larger than this (metres or pixels)
NARROW_LEAST_M = 0.6  # a door or window wider than its wall is narrowed to fit a wall this long or longer
END_M = 0.05  # a narrowed door or window keeps this much wall at each end; two doors meeting end to end on one wall
# are narrowed to leave twice this between them (a doorway reaching another doorway would join three rooms)
CORNER_M = 0.25  # two doors on the two walls of one corner keep this much wall from the corner (more than the
# corner wall's half thickness and a cell of the walking map: their openings would meet in the corner's wall)

_KEYS = {"format", "units", "scale", "rooms", "doors", "windows", "entrance"}
_ROOM_KEYS = {"id", "name", "type", "polygon"}
_DOOR_KEYS = {"at", "width", "rooms"}
_WINDOW_KEYS = {"at", "width", "room"}
_SCALE_TEXT = re.compile(
    r"^\s*(?P<room>.+?)\s+(?P<dim>width|length|depth)\s+(?P<value>\d+(?:\.\d+)?|\.\d+)\s*(?:m|meters?|metres?)\s*$",
    re.IGNORECASE)


class PlanError(ValueError):
    """A plan VESPER cannot use, with what is wrong with it."""


def door_name(k: int, numbers: list[int] | None = None) -> str:
    """How door k of a plan is named to the user: "door N", N counting from 1 in the plan as read. `numbers` gives each
    door's place (from 0) in the plan as read when doors were left out since (by `rectify`, or the lobby's Leave
    out); None: the plan is the plan as read."""
    return f"door {(k if numbers is None else numbers[k]) + 1}"


def room_names(rooms: list[dict]):
    """How rooms are named to the user in a warning: by their name, quoted ('MH'), with the id after it when another
    room has the same name ('MH' (r4)); by the id when the name is empty. "outside" is 'outside'."""
    names = {r["id"]: (r.get("name") or "").strip() or r["id"] for r in rooms}
    count: dict[str, int] = {}
    for n in names.values():
        count[n] = count.get(n, 0) + 1

    def name(rid: str) -> str:
        if rid not in names:
            return repr(rid)
        n = names[rid]
        return repr(n) if count[n] == 1 else f"{n!r} ({rid})"
    return name


def _numbers_problem(numbers, doors: list) -> str | None:
    if numbers is None:
        return None
    if (not isinstance(numbers, list) or len(numbers) != len(doors)
            or not all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n < MAX_ABS for n in numbers)
            or len(set(numbers)) != len(numbers)):
        return (f"door numbers: expected one number (from 0) per door of the plan, {len(doors)} distinct ones, "
                f"not {numbers!r}")
    return None


# --- the file ----------------------------------------------------------------------------------------------------

def _num(v) -> bool:
    """A real number of a size a plan could have (below MAX_ABS; a bool, NaN or an int beyond float range is not)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v) and abs(v) <= MAX_ABS
    except OverflowError:
        return False


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
        return "polygon is not a list of [x, y] points (numbers below 1e9 in size)"
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
                return f"scale: {first} must be a number above 0 (and below 1e9)"
            if first == "length_m":
                if not (_is_point(scale["from"]) and _is_point(scale["to"])):
                    return "scale: 'from' and 'to' must be [x, y] points"
                if math.dist(scale["from"], scale["to"]) < _EPS:
                    return "scale: 'from' and 'to' are the same point"
            return None
    return "scale: expected {m_per_px}, {length_m, from, to}, {area_m2} or null"


def load_plan(raw: dict, numbers: list[int] | None = None) -> dict:
    """Check a `vesper-plan/1` file and return it unchanged, or raise a PlanError listing everything wrong (doors
    named as `door_name` names them)."""
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
            errs.append(f"{door_name(k, numbers)}: must be an object with exactly {', '.join(sorted(_DOOR_KEYS))}")
            continue
        if not _is_point(d["at"]):
            errs.append(f"{door_name(k, numbers)}: at must be an [x, y] point")
        if not _num(d["width"]) or d["width"] <= 0:
            errs.append(f"{door_name(k, numbers)}: width must be a number above 0")
        sides = d["rooms"]
        if not isinstance(sides, list) or len(sides) != 2 or not all(isinstance(s, str) for s in sides):
            errs.append(f"{door_name(k, numbers)}: rooms must be two room ids (or 'outside')")
            continue
        for s in sides:
            if s != "outside" and s not in ids:
                errs.append(f"{door_name(k, numbers)}: room {s!r} is not in the plan")
        if sides[0] == sides[1]:
            errs.append(f"{door_name(k, numbers)}: joins {sides[0]!r} to itself")

    windows = raw.get("windows", [])
    if not isinstance(windows, list):
        errs.append("plan: windows must be a list")
        windows = []
    for k, w in enumerate(windows):
        if not isinstance(w, dict) or set(w) != _WINDOW_KEYS:
            errs.append(f"window {k + 1}: must be an object with exactly {', '.join(sorted(_WINDOW_KEYS))}")
            continue
        if not _is_point(w["at"]):
            errs.append(f"window {k + 1}: at must be an [x, y] point")
        if not _num(w["width"]) or w["width"] <= 0:
            errs.append(f"window {k + 1}: width must be a number above 0")
        if not isinstance(w["room"], str) or w["room"] not in ids:
            errs.append(f"window {k + 1}: room {w['room']!r} is not in the plan")

    if not errs and (bad := _numbers_problem(numbers, doors)) is not None:
        errs.append(bad)
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
    try:
        load_plan(out)
    except PlanError as e:
        raise PlanError(f"scale: that scale does not give a usable plan ({e})") from None
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


def _boxes_apart(a: list[Point], b: list[Point]) -> bool:
    return any(max(p[k] for p in a) <= min(p[k] for p in b) or max(p[k] for p in b) <= min(p[k] for p in a)
               for k in (0, 1))


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


def _depth(cells: set[tuple[int, int]], xs: list[float], zs: list[float]) -> float:
    """How far an outline would have to move to clear the cells: the side of the largest square inside them."""
    nx, nz = len(xs) - 1, len(zs) - 1
    prefix = [[0] * (nx + 1) for _ in range(nz)]
    for j in range(nz):
        for i in range(nx):
            prefix[j][i + 1] = prefix[j][i] + ((i, j) in cells)
    best = 0.0
    for i0 in range(nx):
        for i1 in range(i0, nx):
            width = xs[i1 + 1] - xs[i0]
            if width <= best:
                continue
            run = 0.0
            for j in range(nz):
                if prefix[j][i1 + 1] - prefix[j][i0] == i1 - i0 + 1:
                    run += zs[j + 1] - zs[j]
                    best = max(best, min(width, run))
                else:
                    run = 0.0
    return best


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


def _outside_walls(k: int, rings: list[list[Point]], skip: frozenset = frozenset()) -> list[tuple[Point, Point]]:
    """The stretches of room k's wall that no other room shares (a room in `skip`, such as a balcony, sharing it
    leaves it outside)."""
    others = [_edges(r) for i, r in enumerate(rings) if i != k and i not in skip]
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


Seg = tuple[Point, Point]


def _nearest(segs: list[Seg], p: Point, width: float) -> tuple[Point, float, Seg] | None:
    """Where an opening `width` wide goes on the walls: the point of the wall nearest p, moved along the wall until
    its centre is half a width from each end of the stretch; how far p was from the wall; and the stretch. Stretches
    shorter than the opening do not count; None when none is long enough."""
    best: tuple[Point, float, Seg] | None = None
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
            best = (q, d, (a, b))
    return best


def _fit(segs: list[Seg], p: Point, width: float) -> tuple[Point, float, Seg, float] | None:
    """`_nearest`, and the opening's width there: as `_nearest` places it when a stretch within DOOR_REACH_M of p is
    long enough; else the nearest of those and of the shorter stretches of at least NARROW_LEAST_M, an opening on
    one of these narrowed to the stretch's length less END_M at each end. None when no stretch is NARROW_LEAST_M or
    the opening's width long."""
    hit = _nearest(segs, p, width)
    if hit is not None and hit[1] <= DOOR_REACH_M + 1e-9:
        return (*hit, width)
    best = None if hit is None else (*hit, width)
    for a, b in segs:
        length = math.dist(a, b)
        if not NARROW_LEAST_M - 1e-9 <= length < width - 1e-6:
            continue
        narrow = length - 2 * END_M
        got = _nearest([(a, b)], p, narrow)
        if got is not None and (best is None or got[1] < best[1]):
            best = (*got, narrow)
    return best


def _longest(segs: list[Seg]) -> float:
    return max((math.dist(a, b) for a, b in segs), default=0.0)


def _reached(ids: list[str], doors: list[dict], entrance: int | None, skip=frozenset()) -> set[str]:
    """The rooms reached from the entrance through the doors (but those in `skip`)."""
    graph: dict[str, set[str]] = {i: set() for i in ids}
    for j, d in enumerate(doors):
        if j not in skip and "outside" not in d["rooms"]:
            a, b = d["rooms"]
            graph[a].add(b)
            graph[b].add(a)
    if entrance is None or not 0 <= entrance < len(doors):
        return set()
    seen = {s for s in doors[entrance]["rooms"] if s != "outside"}
    todo = list(seen)
    while todo:
        for nb in graph[todo.pop()]:
            if nb not in seen:
                seen.add(nb)
                todo.append(nb)
    return seen


def _clear(span: tuple[str, float, float, float], c: float) -> float:
    """How much wall there is between an opening (class, offset, from, to) and the point c along its wall: 0 when the
    opening spans c."""
    return max(span[2] - c, c - span[3], 0.0)


def _span(q: Point, seg: Seg, width: float) -> tuple[str, float, float, float] | None:
    """An opening at q, `width` wide on an axis-aligned stretch: (class, offset, from, to) along the wall."""
    (a, b) = seg
    if abs(a[1] - b[1]) < 1e-9:
        return ("h", a[1], q[0] - width / 2, q[0] + width / 2)
    if abs(a[0] - b[0]) < 1e-9:
        return ("v", a[0], q[1] - width / 2, q[1] + width / 2)
    return None


# --- rectify -----------------------------------------------------------------------------------------------------

def _shift(a: list[Point], b: list[Point]) -> float:
    """How far apart two outlines are: the furthest any corner of one lies from the other, both ways."""
    def far(pts: list[Point], ring: list[Point]) -> float:
        return max(min(math.dist(p, _closest(u, v, p)) for u, v in zip(ring, ring[1:] + ring[:1], strict=True))
                   for p in pts)
    return max(far(a, b), far(b, a))


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


def rectify(plan_m: dict, warnings: list[str] | None = None, numbers: list[int] | None = None) -> dict:
    """The plan straightened. In order: the plan is turned so its dominant wall direction is the x axis; every wall
    within 10 degrees of an axis is made exactly parallel to it, consecutive walls on one line become one wall, and
    steps narrower than 15 cm are ironed out; where two rooms overlap the larger loses the strip (the smaller keeps
    it); a gap narrower than 25 cm between two rooms' facing walls closes to its middle, and walls of different rooms
    within 15 cm of one line become one line; each door moves onto the wall its two rooms share (or the outside wall
    of its room), each window onto an outside wall of its room (a wall onto a balcony is one).

    What the image cannot support is left out or narrowed, never invented, and a sentence saying so is appended to
    `warnings` (when a list is given): a window on no outside wall, or in an outdoor room, is left out; a door or
    window wider than its wall is narrowed to the wall's length less 10 cm (a door's wall must be 60 cm long); two
    doors meeting end to end on one wall are narrowed to leave 10 cm of wall between them; two doors on the two walls
    of one corner are moved along their walls to leave CORNER_M of wall at the corner (narrowed, and said, only when
    a wall is too short); a door between rooms with no wall in common is left out when no room needs it to be reached
    from the entrance. An entrance into an entrance porch (`entrance_porch`) is said. A PlanError names what is
    left: a door more than 50 cm from its wall, on a wall shorter than 60 cm, between rooms with no wall in common
    that a room needs, two doors at a corner with too little wall for CORNER_M, or between two rooms that cannot be
    straightened.

    Doors are named as `door_name` names them: `numbers` is each door's place in the plan as read (None: this plan
    is the plan as read); a list given is updated in place to the doors kept."""
    load_plan(plan_m, numbers)
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

    turned = [list(r) for r in rings]

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

    def trim_overlaps(rings: list[list[Point]]) -> list[list[Point]]:
        rings = list(rings)
        for i in range(len(rings)):
            for j in range(i + 1, len(rings)):
                a, b = rings[i], rings[j]
                if not (_is_rectilinear(a) and _is_rectilinear(b)):
                    continue
                if _boxes_apart(a, b):
                    continue
                xs, zs = _grid(a, b)
                both = _cells(a, xs, zs) & _cells(b, xs, zs)
                area = _cell_area(both, xs, zs)
                if area <= _EPS:
                    continue
                big, small = (i, j) if areas[i] >= areas[j] else (j, i)
                depth = _depth(both, xs, zs)
                if depth > TRIM_MAX_M + 1e-9:
                    verb = "lies inside" if area >= 0.95 * areas[small] else "overlaps"
                    raise PlanError(f"room {ids[small]!r} {verb} {ids[big]!r} by {depth:.2f} m: redraw them as "
                                    f"separate rooms")
                rings[big] = tidy(big, _trim(ids[big], rings[big], ids[small], rings[small]))
        return rings

    # 4. gaps and near-collinear walls between rooms: walls that should be one line are joined (closest first, a line
    # never spanning more than a gap, or half a metre where a door between two rooms opens onto it) and the line is
    # put in the middle of them. Closing a gap changes which walls meet end to end, so the pass is repeated until
    # nothing moves (closing walls can leave a sliver of overlap, which is trimmed in the next round).
    door_at: dict[frozenset, list] = {}  # the pairs of rooms a door joins -> where the doors are
    for d in doors:
        if "outside" not in d["rooms"]:
            door_at.setdefault(frozenset(ids.index(r) for r in d["rooms"]), []).append(tuple(d["at"]))

    straight = rings
    rings = trim_overlaps(rings)
    # a room trimmed by an overlap is measured from where the trim left it (the trim itself is at most TRIM_MAX_M deep)
    baseline = [turned[k] if rings[k] == straight[k] else rings[k] for k in range(len(rings))]
    info0 = [_edges(r) for r in rings]  # the walls as drawn (turned, straightened, trimmed), before any gap is closed

    def origin(k: int, e: dict) -> tuple[float, float]:
        """Where the wall e of room k was drawn: the offsets of the walls it comes from. Limits are checked on these,
        not on where earlier rounds put the walls, so that rounds cannot chain gaps into a wider merge."""
        offs = [o["off"] for o in info0[k]
                if o["cls"] == e["cls"] and o["s"] == e["s"] and abs(o["off"] - e["off"]) <= DOOR_REACH_M + 1e-6
                and min(o["t1"], e["t1"]) - max(o["t0"], e["t0"]) > 1e-6]
        return (min(offs), max(offs)) if offs else (e["off"], e["off"])

    def close_walls(rings: list[list[Point]]) -> list[list[Point]]:
        info = [_edges(r) for r in rings]
        walls = [(k, i) for k, es in enumerate(info) for i, e in enumerate(es) if e["cls"] is not None]
        pairs = []
        for x, (ka, ia) in enumerate(walls):
            for kb, ib in walls[x + 1:]:
                ea, eb = info[ka][ia], info[kb][ib]
                if ka == kb or ea["cls"] != eb["cls"]:
                    continue
                along = min(ea["t1"], eb["t1"]) - max(ea["t0"], eb["t0"])
                if ea["s"] == eb["s"]:  # both rooms on the same side: one wall drawn twice
                    if abs(ea["off"] - eb["off"]) <= MERGE_M and along > -0.3:
                        pairs.append((abs(ea["off"] - eb["off"]), (ka, ia), (kb, ib), False))
                    continue
                # the rooms back to back: a wall drawn as a gap between them (or as a sliver of overlap)
                inner, outer = (ea, eb) if ea["s"] == -1 else (eb, ea)
                gap = outer["off"] - inner["off"]
                if along <= 0.1 or gap >= DOOR_REACH_M or gap <= -MERGE_M:
                    continue
                lo, hi = max(ea["t0"], eb["t0"]), min(ea["t1"], eb["t1"])
                a, b = _seg({"cls": ea["cls"], "off": (inner["off"] + outer["off"]) / 2}, lo, hi)
                door = any(math.dist(p, _closest(a, b, p)) <= DOOR_REACH_M
                           for p in door_at.get(frozenset((ka, kb)), []))
                if gap < GAP_M or door:
                    pairs.append((abs(gap), (ka, ia), (kb, ib), door))
        parent = {w: w for w in walls}
        span = {w: origin(w[0], info[w[0]][w[1]]) for w in walls}
        size = dict.fromkeys(walls, 1)
        has_door = dict.fromkeys(walls, False)

        def root(w):
            while parent[w] != w:
                parent[w] = parent[parent[w]]
                w = parent[w]
            return w
        for _, wa, wb, door in sorted(pairs):
            ra, rb = root(wa), root(wb)
            if ra == rb:
                has_door[ra] = has_door[ra] or door
                continue
            lo, hi = min(span[ra][0], span[rb][0]), max(span[ra][1], span[rb][1])
            wide = door or has_door[ra] or has_door[rb]
            if hi - lo < (DOOR_REACH_M if wide else GAP_M):
                parent[rb] = ra
                span[ra] = (lo, hi)
                size[ra] += size[rb]
                has_door[ra] = wide
        for w in walls:
            r = root(w)
            if size[r] > 1:
                info[w[0]][w[1]]["off"] = (span[r][0] + span[r][1]) / 2
        return [tidy(k, _rebuild([e["a"] for e in es], [e["cls"] for e in es], [e["off"] for e in es]))
                for k, es in enumerate(info)]

    for _ in range(MAX_PASSES):
        closed = close_walls(trim_overlaps(rings))
        if closed == rings:
            break
        rings = closed
    else:
        raise PlanError(f"the plan does not settle: closing the gaps between its rooms was still moving walls after "
                        f"{MAX_PASSES} rounds; check that the rooms' walls line up")
    moved = [_shift(baseline[k], rings[k]) for k in range(len(rings))]
    far = [f"{ids[k]!r} ({moved[k]:.2f} m)" for k in range(len(rings)) if moved[k] > DOOR_REACH_M + 1e-6]
    if far:
        raise PlanError(f"rectifying would move the walls of {', '.join(far)} more than {DOOR_REACH_M} m: the rooms' "
                        f"walls are too far apart or too far off to be straightened; redraw them")

    # 5. doors and windows onto their walls; what the walls cannot carry is left out or narrowed, and said
    def say(text: str) -> None:
        if warnings is not None:
            warnings.append(text)

    rnd = lambda q: [round(q[0], 9) + 0.0, round(q[1], 9) + 0.0]  # noqa: E731
    named = room_names(plan["rooms"])  # a warning names rooms as the user knows them; an error by id (the lobby
    outdoor = frozenset(k for k, r in enumerate(plan["rooms"]) if r["type"] in OUTDOOR_TYPES)
    entrance = plan["entrance"]
    loose: list[int] = []  # doors between rooms with no wall in common
    spans: dict[int, tuple] = {}  # door -> (class, offset, from, to) of its opening on an axis-aligned wall
    stretch: dict[int, tuple[float, float]] = {}  # door -> (from, to) of the stretch of wall it stands on
    for k, d in enumerate(doors):
        a, b = d["rooms"]
        at = tuple(d["at"])
        what = f"{door_name(k, numbers)} between {a!r} and {b!r}"  # marks the rooms an error names by id)
        said = f"{door_name(k, numbers)} between {named(a)} and {named(b)}"
        if "outside" in d["rooms"]:
            room = b if a == "outside" else a
            segs = _outside_walls(ids.index(room), rings)
            if not segs:
                raise PlanError(f"{what}: {room!r} has no outside wall")
            wall, its = "the nearest outside wall", "its outside wall"
        else:
            segs = _shared_walls(rings[ids.index(a)], rings[ids.index(b)])
            if not segs:
                if k == entrance:
                    raise PlanError(f"{what}: the rooms do not touch (they have no wall in common)")
                loose.append(k)
                continue
            wall, its = "their shared wall", "their wall"
        hit = _fit(segs, at, d["width"])
        if hit is None:
            raise PlanError(f"{what}: the door ({d['width']:.2f} m) is wider than the wall it belongs to "
                            f"(at most {_longest(segs):.2f} m)")
        q, dist, seg, width = hit
        if dist > DOOR_REACH_M + 1e-9:
            raise PlanError(f"{what}: it is {dist:.2f} m from {wall} (more than {DOOR_REACH_M} m)")
        if width < d["width"]:
            say(f"{said} ({d['width']:.2f} m) is wider than {its} ({math.dist(*seg):.2f} m): narrowed to "
                f"{width:.2f} m")
            d["width"] = round(width, 9)
        d["at"] = rnd(q)
        if (span := _span(q, seg, d["width"])) is not None:
            spans[k] = span
            t = [p[0] if span[0] == "h" else p[1] for p in seg]
            stretch[k] = (min(t), max(t))

    # two doors meeting end to end on one wall would open one gap into three rooms: a wall is left between them
    lines: dict[tuple, list[int]] = {}
    for k, (cls, off, _, _) in spans.items():
        lines.setdefault((cls, round(off, 6)), []).append(k)
    for line in lines.values():
        line.sort(key=lambda k: spans[k][2] + spans[k][3])
        for i, j in zip(line, line[1:]):
            if set(doors[i]["rooms"]) == set(doors[j]["rooms"]):
                continue
            cls, off, lo_i, hi_i = spans[i]
            _, _, lo_j, hi_j = spans[j]
            if lo_j - hi_i >= 2 * END_M - 1e-9:
                continue
            mid = (hi_i + lo_j) / 2
            hi_i, lo_j = mid - END_M, mid + END_M
            if min(hi_i - lo_i, hi_j - lo_j) < NARROW_LEAST_M - 2 * END_M - 1e-9:
                raise PlanError(f"{door_name(i, numbers)} and {door_name(j, numbers)[5:]} overlap on one wall: "
                                "move one of them")
            for k, lo, hi in ((i, lo_i, hi_i), (j, lo_j, hi_j)):
                spans[k] = (cls, off, lo, hi)
                doors[k]["width"] = round(hi - lo, 9)
                doors[k]["at"] = rnd(((lo + hi) / 2, off) if cls == "h" else (off, (lo + hi) / 2))
            say(f"doors {door_name(i, numbers)[5:]} and {door_name(j, numbers)[5:]} meet end to end on one wall: "
                f"narrowed to {hi_i - lo_i:.2f} m and "
                f"{hi_j - lo_j:.2f} m to leave {2 * END_M:.2f} m of wall between them")

    # two doors on the two walls of one corner, one within CORNER_M of it and the other within twice that, would open
    # through the corner's wall into each other: one gap into three rooms. Each is moved along its wall to leave
    # CORNER_M of wall at the corner, and narrowed (said) only when its wall is too short for that
    for _ in range(MAX_PASSES):
        moved = False
        for i, j in itertools.combinations(sorted(spans), 2):
            if spans[i][0] == spans[j][0]:
                continue
            at = {i: spans[j][1], j: spans[i][1]}  # where the other door's wall line crosses this door's
            clear = {k: _clear(spans[k], c) for k, c in at.items()}
            if not (min(clear.values()) < CORNER_M - 1e-9 and max(clear.values()) < 2 * CORNER_M):
                continue
            for k, c in at.items():
                if clear[k] >= CORNER_M - 1e-9:
                    continue
                cls, off, lo, hi = spans[k]
                (s0, s1), width = stretch[k], hi - lo
                if c <= (lo + hi) / 2:
                    lo = c + CORNER_M
                    hi = min(lo + width, s1 - END_M)
                else:
                    hi = c - CORNER_M
                    lo = max(hi - width, s0 + END_M)
                if hi - lo < NARROW_LEAST_M - 2 * END_M - 1e-9:
                    raise PlanError(f"{door_name(i, numbers)} and {door_name(j, numbers)[5:]} meet at a corner, with "
                                    f"too little wall to leave {CORNER_M:.2f} m of it at the corner: move one of them")
                if hi - lo < width - 1e-9:
                    a, b = doors[k]["rooms"]
                    other = j if k == i else i
                    say(f"{door_name(k, numbers)} between {named(a)} and {named(b)} ({width:.2f} m) meets "
                        f"{door_name(other, numbers)} at a corner: narrowed to {hi - lo:.2f} m to leave "
                        f"{CORNER_M:.2f} m of wall at the corner")
                spans[k] = (cls, off, lo, hi)
                doors[k]["width"] = round(hi - lo, 9)
                doors[k]["at"] = rnd(((lo + hi) / 2, off) if cls == "h" else (off, (lo + hi) / 2))
            moved = True
        if not moved:
            break

    # a door between rooms that do not touch: left out when no room needs it to be reached from the entrance (with
    # no entrance, kept as it is: check_house says the entrance is missing, and this is decided once there is one)
    if loose and entrance is not None:
        lost = _reached(ids, doors, entrance) - _reached(ids, doors, entrance, frozenset(loose))
        if lost:
            need = next((k for k in loose if _reached(ids, doors, entrance, frozenset(loose) - {k})
                         - _reached(ids, doors, entrance, frozenset(loose))), loose[0])
            a, b = doors[need]["rooms"]
            raise PlanError(f"{door_name(need, numbers)} between {a!r} and {b!r}: the rooms do not touch (they have "
                            f"no wall in common), and without it {', '.join(repr(r) for r in ids if r in lost)} "
                            f"cannot be reached from the entrance")
        for k in loose:
            a, b = doors[k]["rooms"]
            say(f"{door_name(k, numbers)} between {named(a)} and {named(b)}: the rooms do not touch (they have no "
                f"wall in common): left out (no room needs it to be reached from the entrance)")
        plan["entrance"] = entrance - sum(1 for k in loose if k < entrance)
        plan["doors"] = [d for k, d in enumerate(doors) if k not in loose]
        if numbers is not None:
            numbers[:] = [n for k, n in enumerate(numbers) if k not in loose]

    kept, dropped = [], []  # dropped: (its warning's place, its room) for a window on no outside wall
    for k, w in enumerate(windows):
        r = ids.index(w["room"])
        what = f"window {k + 1} of {named(w['room'])}"  # counted from 1, as doors are
        if r in outdoor:
            say(f"{what} is in an outdoor room: left out")
            continue
        segs = _outside_walls(r, rings, outdoor)  # a wall onto a balcony is an outside wall
        hit = _fit(segs, tuple(w["at"]), w["width"]) if segs else None
        if hit is None or hit[1] > DOOR_REACH_M + 1e-9:
            say(f"{what} is not on an outside wall: left out")
            if warnings is not None:
                dropped.append((len(warnings) - 1, r))
            continue
        q, _, seg, width = hit
        if width < w["width"]:
            say(f"{what} ({w['width']:.2f} m) is wider than its wall ({math.dist(*seg):.2f} m): narrowed to "
                f"{width:.2f} m")
            w["width"] = round(width, 9)
        w["at"] = rnd(q)
        kept.append(w)
    plan["windows"] = kept
    from vesper.lab.windows import CORNER_M as WINDOW_CORNER_M, RULES
    lit = {ids.index(w["room"]) for w in kept}
    for at, r in dropped:  # a room of a kind with windows, left with none, gets them by rule where it has a wall out
        if (plan["rooms"][r]["type"] in RULES and r not in lit
                and _longest(_outside_walls(r, rings, outdoor)) >= 0.5 + 2 * WINDOW_CORNER_M):
            warnings[at] += "; the room gets a rule window"

    if (porch := entrance_porch(plan)) is not None:
        k, room = porch
        inside = next(s for s in plan["doors"][k]["rooms"] if s != room)
        say(f"the entrance opens into the balcony {named(room)}, an entrance porch: the house door is "
            f"{door_name(k, numbers)}, from it into {named(inside)}; {named(room)} is built as the porch "
            "in front of it")

    for r, ring in zip(plan["rooms"], rings, strict=True):
        r["polygon"] = [[x, z] for x, z in ring]
    return plan


# --- is it a house -----------------------------------------------------------------------------------------------

def entrance_porch(plan_m: dict) -> tuple[int, str] | None:
    """(the house door, the porch) when the entrance leads from outside into an outdoor room (a balcony) that has
    exactly one door into an indoor room: that outdoor room is an entrance porch (a Finnish kuisti, a veranda), the
    door from it into the home is the house door, and the entrance (its door to the outside) stays the way in. None
    when the entrance leads straight indoors, or the outdoor room has no door into the home or several."""
    doors = plan_m.get("doors") or []
    ent = plan_m.get("entrance")
    if ent is None or not 0 <= ent < len(doors) or "outside" not in doors[ent]["rooms"]:
        return None
    types = {r["id"]: r["type"] for r in plan_m["rooms"]}
    room = next((s for s in doors[ent]["rooms"] if s != "outside"), None)
    if types.get(room) not in OUTDOOR_TYPES:
        return None
    into = [k for k, d in enumerate(doors)
            if room in d["rooms"] and any(s not in ("outside", room) and types.get(s) not in OUTDOOR_TYPES
                                          for s in d["rooms"])]
    return (into[0], room) if len(into) == 1 else None


def check_house(plan_m: dict, numbers: list[int] | None = None, warnings: list[str] | None = None) -> list[str]:
    """Every way a plan in metres is not a house (an empty list when it is): rooms that overlap, a room under 0.5 m²
    (a mis-read, not a room), a scale that cannot be right, no entrance (a door to the outside; one into a
    balcony only when the balcony is an entrance porch, `entrance_porch`), and rooms not
    reachable from it through doors. Doors are named as `door_name` names them (`numbers`: as `rectify`'s).

    The scale cannot be right when the rooms are on the whole far from their usual size: the median, over the indoor
    rooms of a type with a typical area (`generate.AREAS`), of a room's area divided by the middle of its type's
    typical range is below SCALE_RANGE[0] or above SCALE_RANGE[1]. Otherwise each indoor room's area is judged
    against its type's plausible range ([0.5 x the smallest, 1.5 x the largest] typical area; 2 to 60 m² for a type
    with none; a WC typed bathroom down to 1 m², a closet or store, "other", to 0.5 m²: AREA_FLOOR), and a room
    outside it is built as read with a sentence appended to `warnings` (when a list is given): real rooms are often
    smaller or larger than a generated home's. An outdoor room, such as a balcony, has any size."""
    load_plan(plan_m, numbers)
    if plan_m["units"] != "m":
        raise PlanError("check_house: the plan is in pixels; scale it to metres first (scale_plan)")
    problems: list[str] = []
    rooms, doors = plan_m["rooms"], plan_m.get("doors", [])
    rings = {r["id"]: _ring(r["polygon"]) for r in rooms}
    indoor = [r for r in rooms if r["type"] not in OUTDOOR_TYPES]
    areas = {r["id"]: polygon_area(rings[r["id"]]) for r in indoor}
    ratios = [areas[r["id"]] / ((AREAS[r["type"]][0] + AREAS[r["type"]][1]) / 2) for r in indoor
              if r["type"] in AREAS and areas[r["id"]] >= ROOM_LEAST_M2]
    whole = statistics.median(ratios) if ratios else 1.0
    scale_ok = SCALE_RANGE[0] <= whole <= SCALE_RANGE[1]
    if not scale_ok:
        problems.append(f"the scale looks wrong: rooms are on the whole {whole:.1f} times their usual size; give "
                        f"--scale or --area")
    named = room_names(rooms)
    for r in indoor:
        lo, hi = ((AREA_SLACK[0] * AREAS[r["type"]][0], AREA_SLACK[1] * AREAS[r["type"]][1])
                  if r["type"] in AREAS else OTHER_AREA)
        area = areas[r["id"]]
        lo = AREA_FLOOR.get(r["type"], lo)
        if area < ROOM_LEAST_M2:
            problems.append(f"room {r['id']!r} ({r['type']}): {area:.1f} m² is under {ROOM_LEAST_M2} m²: too small "
                            f"to be a room")
        elif not lo - 1e-9 <= area <= hi + 1e-9 and scale_ok and warnings is not None:
            warnings.append(f"room {named(r['id'])} ({r['type']}): {area:.1f} m² is outside the plausible {lo:.1f} "
                            f"to {hi:.1f} m² for its type: built as read")
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
    inside = next((r for r in rooms if r["id"] in door["rooms"] and r["type"] in OUTDOOR_TYPES), None)
    if "outside" in door["rooms"] and inside is not None and entrance_porch(plan_m) is None:
        problems.append(f"the entrance opens into the balcony {room_names(rooms)(inside['id'])}: choose the door from "
                        f"it into the home")
    if "outside" not in door["rooms"]:
        problems.append(f"entrance {door_name(ent, numbers)} joins {door['rooms'][0]!r} and {door['rooms'][1]!r}, "
                        f"not a room and the outside")
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
