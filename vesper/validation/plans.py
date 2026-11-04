"""How well a floor-plan image is read: the floor-plan import's evaluation on CubiCasa5K (task A5).

CubiCasa5K (Kalervo et al., SCIA 2019; Zenodo record 2613548) holds 5000 floor-plan images, each with its plan drawn
as an SVG (`model.svg`): rooms as polygons with their class, doors and windows as rectangles in the walls. The SVG's
coordinates are the pixels of the plan's `F1_scaled.png`, one unit a centimetre (each room's hidden dimension label,
"4.21 m x 2.06 m", is checked against its drawn size). Nothing of the dataset is part of VESPER: `fetch` takes only
the files a sample needs out of the published archive (HTTP range requests on the zip), into a git-ignored folder.

For each sampled test plan, `evaluate` reads the image with `plan_read.read_plan_image` (the user's Claude Code, once
per plan: every answer Claude gives is saved under `<dataset>/readings/` the moment it arrives, and a saved reading is
never paid for again), then measures the reading against the ground truth, both in the image's pixels:

- rooms found: F1 at IoU >= 0.5, each true room matched to at most one read room (the highest IoU first);
- room types: the share of matched rooms whose VESPER type is the true room's mapped type (`CLASS_TYPES`); the
  lenient share takes "hall" and "corridor" as one type;
- doors found: the share of true doors with a read door within 0.5 m (by the true scale), one to one;
- area error: the relative error of the total area and of each matched room's area; both plans are measured with
  the TRUE scale, so it is the reading's error, not the scale's;
- the import: the plan scaled (by the scale Claude read, else the true one, as if the user had typed it),
  straightened, checked to be a house (`check_house`), built (`generate_from_plan`) and its layout checked
  (`layoutcheck.check`). The true plan goes through the same steps, so the reading is compared with what the truth
  itself gives.

Every metric is recomputed from the saved readings without Claude (`offline`)."""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
import re
import statistics
import struct
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SOURCE_URL = "https://zenodo.org/records/2613548/files/cubicasa5k.zip?download=1"
SOURCE_RECORD = "https://zenodo.org/records/2613548"
LICENCE = "CC BY-NC-SA 4.0 per its Zenodo record (the dataset's GitHub README says CC BY-NC 4.0)"
ARCHIVE_ROOT = "cubicasa5k/"  # every member of the archive is under this folder
IMAGE = "F1_scaled.png"  # the plan's image whose pixels the SVG's coordinates are
TRUTH = "model.svg"
M_PER_UNIT = 0.01  # CubiCasa5K draws in centimetres (used when a plan has no dimension label to check it by)
IOU_MIN = 0.5  # a read room is the true room when their IoU is at least this
DOOR_REACH_M = 0.5  # a read door within this of a true door finds it
PROBE_PX = 20  # how far beyond a door's or window's wall face the room on each side is looked for
GRID_PX = 2.0  # the IoU's raster: one cell per 2 px (2 cm in CubiCasa5K)
TOUCH_PX = 3.0  # two spaces' outlines this close, with no wall drawn between them, are one open boundary
OPEN_MIN_M = 0.6  # an open boundary at least this long joins the two spaces (an opening in the true plan)
OPEN_WIDTH_M = 0.9  # an opening's width in the true plan: this, or 0.8 x the boundary when that is shorter

# CubiCasa5K's room class (the first word after "Space", as its own loader reads it) -> VESPER's room type. Its own
# grouping is kept where VESPER has the type (a sauna is a bath, an entry or draught lobby an entry hall, an outdoor
# space such as a balcony or terrace a balcony). Every class not listed (closets, storage, garages, "Undefined",
# ...) is "other".
CLASS_TYPES = {
    "LivingRoom": "living", "Lounge": "living",
    "Kitchen": "kitchen",
    "Bedroom": "bedroom",
    "Bath": "bathroom", "Sauna": "bathroom",
    "Dining": "dining", "EatingArea": "dining",
    "Office": "office", "Library": "office", "Den": "office",
    "Utility": "laundry",
    "Entry": "hall", "DraughtLobby": "hall", "Hall": "hall",
    "HallWay": "corridor",
    "Outdoor": "balcony",
}
PASSAGES = {"hall", "corridor"}  # one type for the lenient type accuracy

_SVG = "{http://www.w3.org/2000/svg}"
_LABEL = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*m\s*x\s*([0-9]+(?:\.[0-9]+)?)\s*m")
_PLAN_ID = re.compile(r"^[a-z_]+/[0-9]+$")


# --- the ground truth ---------------------------------------------------------------------------------------------

def _matrix(text: str | None) -> np.ndarray:
    """The 3x3 matrix of an SVG transform attribute (matrix, translate and scale, as CubiCasa5K writes them)."""
    m = np.eye(3)
    for kind, args in re.findall(r"(matrix|translate|scale)\s*\(([^)]*)\)", text or ""):
        v = [float(a) for a in re.split(r"[\s,]+", args.strip()) if a]
        if kind == "matrix" and len(v) == 6:
            t = np.array([[v[0], v[2], v[4]], [v[1], v[3], v[5]], [0, 0, 1]])
        elif kind == "translate":
            t = np.array([[1, 0, v[0]], [0, 1, v[1] if len(v) > 1 else 0.0], [0, 0, 1]])
        elif kind == "scale":
            t = np.array([[v[0], 0, 0], [0, v[1] if len(v) > 1 else v[0], 0], [0, 0, 1]])
        else:
            continue
        m = m @ t
    return m


def _points(el, m: np.ndarray) -> list[list[float]]:
    """A polygon's points in image pixels, without repeated points or the closing repeat of the first."""
    nums = [float(v) for v in re.split(r"[\s,]+", (el.get("points") or "").strip()) if v]
    out: list[list[float]] = []
    for x, y in zip(nums[0::2], nums[1::2], strict=False):
        px, py, _ = m @ np.array([x, y, 1.0])
        p = [float(px) + 0.0, float(py) + 0.0]
        if not out or out[-1] != p:
            out.append(p)
    while len(out) > 1 and out[-1] == out[0]:
        out.pop()
    return out


def _cls(el) -> str:
    return el.get("class") or ""


def _first_polygon(el):
    return next((c for c in el if c.tag == _SVG + "polygon"), None)


def _contains(poly, x: float, y: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _area(poly) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1], strict=True))) / 2


def _opening(poly) -> tuple[list[float], float, tuple[float, float], float]:
    """A door's or window's rectangle: its middle, its width (the long side), the unit normal to its wall (along the
    short side) and the wall's thickness."""
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    a, b, c = poly[0], poly[1], poly[2 % len(poly)]
    e1 = (b[0] - a[0], b[1] - a[1])
    e2 = (c[0] - b[0], c[1] - b[1])
    l1, l2 = math.hypot(*e1), math.hypot(*e2)
    long_, short, sv = (l1, l2, e2) if l1 >= l2 else (l2, l1, e1)
    n = (sv[0] / short, sv[1] / short) if short > 0 else (0.0, 1.0)
    return [cx + 0.0, cy + 0.0], long_, n, short


def parse_cubicasa_svg(text: str) -> dict:
    """The ground truth of a CubiCasa5K `model.svg`: every floor it draws (a two-storey home's floors stand side by
    side in the one image, `F1_scaled.png`, so both are what the image shows) as one `vesper-plan/1` in image
    pixels, and "floors", how many; and

    - "m_per_px": the scale, from the rooms' dimension labels (the median over the rooms that have one), else 1 cm a
      unit ("scale_source" says which);
    - "classes": each room's CubiCasa5K class; "door_points": the middle of every door drawn (doors found are
      measured against these, whether or not the rooms either side of a door could be told).

    A door joins the rooms found just beyond each face of its wall ("outside" where there is none); a door with the
    same room or nothing on both sides is in "door_points" only. A window lights the room on its inner side. The
    entrance is a door to the outside, a hall's first."""
    root = ET.fromstring(text)
    floors: list = []
    _floors(root, np.eye(3), floors)
    if not floors:
        raise ValueError("model.svg: no floor plan (no group of class Floorplan)")
    spaces, doors, windows = [], [], []

    def walk(el, m):
        m = m @ _matrix(el.get("transform"))
        kind = _cls(el).split()
        if kind and kind[0] == "Space" and (poly := _first_polygon(el)) is not None:
            spaces.append((el, m, _points(poly, m)))
            return
        if kind and kind[0] in ("Door", "Window") and (poly := _first_polygon(el)) is not None:
            (doors if kind[0] == "Door" else windows).append(_points(poly, m))
            return
        for c in el:
            walk(c, m)
    for floor, base in floors:
        for c in floor:
            walk(c, base)

    rooms, classes, ratios = [], [], []
    for k, (el, _m, poly) in enumerate(spaces):
        words = _cls(el).split()[1:]
        cls = words[0] if words else "Undefined"
        classes.append(cls)
        rooms.append({"id": f"r{k + 1}", "name": " ".join(words) or "Undefined",
                      "type": CLASS_TYPES.get(cls, "other"), "polygon": poly})
        label = next((t.text for t in el.iter(_SVG + "text") if t.text and _LABEL.search(t.text)), None)
        if label and len(poly) >= 3:
            a, b = (float(v) for v in _LABEL.search(label).groups())
            w = max(p[0] for p in poly) - min(p[0] for p in poly)
            h = max(p[1] for p in poly) - min(p[1] for p in poly)
            if w > 0 and h > 0 and a > 0 and b > 0:
                pairs = [(a / w, b / h), (a / h, b / w)]  # a turned room may give its sizes the other way round
                r1, r2 = min(pairs, key=lambda p: abs(math.log(p[0] / p[1])))
                ratios.append(math.sqrt(r1 * r2))
    m_per_px = statistics.median(ratios) if ratios else M_PER_UNIT

    areas = [_area(r["polygon"]) if len(r["polygon"]) >= 3 else 0.0 for r in rooms]

    def room_at(x: float, y: float) -> str:
        inside = [k for k, r in enumerate(rooms) if len(r["polygon"]) >= 3 and _contains(r["polygon"], x, y)]
        return rooms[min(inside, key=lambda k: areas[k])]["id"] if inside else "outside"

    def sides(poly) -> tuple[list[float], float, list[str]]:
        at, width, n, thick = _opening(poly)
        d = thick / 2 + PROBE_PX
        return at, width, [room_at(at[0] + s * d * n[0], at[1] + s * d * n[1]) for s in (-1, 1)]

    plan_doors, door_points = [], []
    for poly in doors:
        if len(poly) < 3:
            continue
        at, width, (a, b) = sides(poly)
        door_points.append(at)
        if a != b:
            plan_doors.append({"at": at, "width": width, "rooms": [a, b]})
    kinds = {r["id"]: c for r, c in zip(rooms, classes, strict=True)}
    plan_windows = []
    for poly in windows:
        if len(poly) < 3:
            continue
        at, width, (a, b) = sides(poly)
        inside = [s for s in (a, b) if s != "outside"]
        inside.sort(key=lambda s: kinds[s] == "Outdoor")  # a window onto a balcony lights the room, not the balcony
        if inside:
            plan_windows.append({"at": at, "width": width, "room": inside[0]})
    joined = {frozenset(d["rooms"]) for d in plan_doors}
    openings = 0
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            a, b = rooms[i], rooms[j]
            if frozenset((a["id"], b["id"])) in joined:
                continue
            if (hit := _touching(a["polygon"], b["polygon"], OPEN_MIN_M / m_per_px)) is not None:
                at, length = hit
                plan_doors.append({"at": at, "width": min(0.8 * length, OPEN_WIDTH_M / m_per_px),
                                   "rooms": [a["id"], b["id"]]})
                openings += 1
    types = {r["id"]: r["type"] for r in rooms}
    out_doors = [k for k, d in enumerate(plan_doors) if "outside" in d["rooms"]]
    halls = [k for k in out_doors if any(types.get(s) == "hall" for s in plan_doors[k]["rooms"])]
    entrance = (halls or out_doors or [None])[0]
    plan = {"format": "vesper-plan/1", "units": "px", "scale": {"m_per_px": m_per_px}, "rooms": rooms,
            "doors": plan_doors, "windows": plan_windows, "entrance": entrance}
    return {"plan": plan, "m_per_px": m_per_px,
            "scale_source": "labels" if ratios else "assumed 1 cm a unit", "classes": classes,
            "door_points": door_points, "openings": openings, "floors": len(floors)}


def _touching(p, q, min_len: float) -> tuple[list[float], float] | None:
    """Where two outlines run along each other with no wall between them (edges parallel and within TOUCH_PX) for at
    least `min_len`: the middle of the longest such stretch and its length; None if they do not."""
    best = None
    for a1, a2 in zip(p, p[1:] + p[:1], strict=True):
        ux, uy = a2[0] - a1[0], a2[1] - a1[1]
        la = math.hypot(ux, uy)
        if la == 0:
            continue
        ux, uy = ux / la, uy / la
        for b1, b2 in zip(q, q[1:] + q[:1], strict=True):
            vx, vy = b2[0] - b1[0], b2[1] - b1[1]
            lb = math.hypot(vx, vy)
            if lb == 0 or abs(ux * vy - uy * vx) / lb > math.sin(math.radians(2)):
                continue
            if abs((b1[0] - a1[0]) * -uy + (b1[1] - a1[1]) * ux) > TOUCH_PX:
                continue
            t1 = (b1[0] - a1[0]) * ux + (b1[1] - a1[1]) * uy
            t2 = (b2[0] - a1[0]) * ux + (b2[1] - a1[1]) * uy
            lo, hi = max(0.0, min(t1, t2)), min(la, max(t1, t2))
            if hi - lo >= min_len and (best is None or hi - lo > best[1]):
                off = ((b1[0] - a1[0]) * -uy + (b1[1] - a1[1]) * ux) / 2
                mid = (lo + hi) / 2
                best = ([a1[0] + ux * mid - uy * off + 0.0, a1[1] + uy * mid + ux * off + 0.0], hi - lo)
    return best


def _floors(el, m, out: list) -> None:
    """Every group of class Floorplan under el, with the transform down to it, in document order."""
    m = m @ _matrix(el.get("transform"))
    if _cls(el).split()[:1] == ["Floorplan"]:
        out.append((el, m))
        return
    for c in el:
        _floors(c, m, out)


# --- the metrics --------------------------------------------------------------------------------------------------

def _mask(poly, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """The cells (centres xs x ys) inside the polygon, by the even-odd rule."""
    X, Y = np.meshgrid(xs, ys)
    inside = np.zeros(X.shape, dtype=bool)
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1], strict=True):
        if y1 == y2:
            continue
        crosses = (y1 > Y) != (y2 > Y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = x1 + (Y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (X < xint)
    return inside


def _polys(plan: dict | None) -> list[list[list[float]]]:
    return [[[float(x), float(y)] for x, y in r["polygon"]] for r in (plan or {}).get("rooms", [])]


def _scale_read(pred: dict, total_px: float) -> float | None:
    """The metres per pixel the reading's own scale implies, if it gave one."""
    s = pred.get("scale")
    if not s:
        return None
    if "m_per_px" in s:
        return float(s["m_per_px"])
    if "length_m" in s:
        return float(s["length_m"]) / math.dist(s["from"], s["to"])
    return math.sqrt(float(s["area_m2"]) / total_px) if total_px > 0 else None


def _pairs(dist: list[tuple[float, int, int]]) -> list[tuple[float, int, int]]:
    """One-to-one: the best pairs first, each side used once."""
    used_a, used_b, out = set(), set(), []
    for d, a, b in sorted(dist):
        if a not in used_a and b not in used_b:
            used_a.add(a)
            used_b.add(b)
            out.append((d, a, b))
    return out


def score(pred: dict | None, truth: dict, res: float = GRID_PX) -> dict:
    """A reading (`vesper-plan/1` in the image's pixels, or None for no reading) against the ground truth
    (`parse_cubicasa_svg`'s dict): rooms found (F1 at IoU >= 0.5, one to one), types right, doors found, area error
    by the true scale, and the error of the scale the reading gave."""
    s = truth["m_per_px"]
    trooms, prooms = truth["plan"]["rooms"], (pred or {}).get("rooms", [])
    tpoly, ppoly = _polys(truth["plan"]), _polys(pred)
    tarea, parea = [_area(p) for p in tpoly], [_area(p) for p in ppoly]
    pts = [p for poly in tpoly + ppoly for p in poly]
    matches = []
    if tpoly and ppoly:
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        xs, ys = np.arange(x0 + res / 2, x1, res), np.arange(y0 + res / 2, y1, res)
        tm, pm = [_mask(p, xs, ys) for p in tpoly], [_mask(p, xs, ys) for p in ppoly]
        tn, pn = [int(m.sum()) for m in tm], [int(m.sum()) for m in pm]
        cand = []
        for i, a in enumerate(tm):
            for j, b in enumerate(pm):
                inter = int(np.logical_and(a, b).sum())
                union = tn[i] + pn[j] - inter
                iou = inter / union if union else 0.0
                if iou >= IOU_MIN:
                    cand.append((-iou, i, j))
        for neg, i, j in sorted(_pairs(cand), key=lambda t: t[1]):
            matches.append({"true": trooms[i]["id"], "pred": prooms[j]["id"], "iou": -neg,
                            "true_type": trooms[i]["type"], "pred_type": prooms[j]["type"],
                            "true_name": trooms[i]["name"], "pred_name": prooms[j]["name"],
                            "area_error": abs(parea[j] - tarea[i]) / tarea[i] if tarea[i] else None})
    nm, nt, npred = len(matches), len(trooms), len(prooms)
    precision = nm / npred if npred else 0.0
    recall = nm / nt if nt else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    correct = sum(m["true_type"] == m["pred_type"] for m in matches)
    lenient = sum(m["true_type"] == m["pred_type"] or {m["true_type"], m["pred_type"]} <= PASSAGES for m in matches)

    tdoors = truth["door_points"]
    pdoors = [d["at"] for d in (pred or {}).get("doors", [])]
    reach = DOOR_REACH_M / s
    found = _pairs([(math.dist(a, b), i, j) for i, a in enumerate(tdoors) for j, b in enumerate(pdoors)
                    if math.dist(a, b) <= reach])
    errs = [m["area_error"] for m in matches if m["area_error"] is not None]
    t_total, p_total = sum(tarea), sum(parea)
    read_scale = _scale_read(pred, p_total) if pred else None
    return {
        "rooms_true": nt, "rooms_pred": npred, "rooms_matched": nm,
        "room_precision": precision, "room_recall": recall, "room_f1": f1,
        "type_correct": correct, "type_correct_lenient": lenient,
        "type_accuracy": correct / nm if nm else None, "type_accuracy_lenient": lenient / nm if nm else None,
        "doors_true": len(tdoors), "doors_pred": len(pdoors), "doors_found": len(found),
        "door_recall": len(found) / len(tdoors) if tdoors else 0.0,
        "door_precision": len(found) / len(pdoors) if pdoors else 0.0,
        "area_true_m2": t_total * s * s, "area_pred_m2": p_total * s * s if pred else None,
        "area_error_total": abs(p_total - t_total) / t_total if pred and t_total else None,
        "area_error_rooms_mean": statistics.fmean(errs) if errs else None,
        "area_error_rooms_median": statistics.median(errs) if errs else None,
        "scale_read_m_per_px": read_scale,
        "scale_error": abs(read_scale - s) / s if read_scale is not None else None,
        "matches": matches,
    }


def _share(values: list) -> float | None:
    return sum(bool(v) for v in values) / len(values) if values else None


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def aggregate(rows: list[dict]) -> dict:
    """The sample's metrics: rooms, types and doors pooled over the plans (a plan not read counts with nothing
    found), the area errors averaged over the plans read, the shares passing check_house and the layout check (out of
    every plan), and the time and notional cost of a reading."""
    sc = [r["score"] for r in rows]
    tot = lambda k: sum(s[k] for s in sc)  # noqa: E731
    matched, pred, true = tot("rooms_matched"), tot("rooms_pred"), tot("rooms_true")
    p = matched / pred if pred else 0.0
    r = matched / true if true else 0.0
    builds = [row.get("build") or {} for row in rows]
    truths = [row.get("truth_build") or {} for row in rows]
    room_errs = [m["area_error"] for s in sc for m in s["matches"] if m["area_error"] is not None]
    return {
        "plans": len(rows), "plans_read": sum(not row.get("error") for row in rows),
        "room_precision": p, "room_recall": r, "room_f1": 2 * p * r / (p + r) if p + r else 0.0,
        "room_f1_per_plan_mean": _mean([s["room_f1"] for s in sc]),
        "type_accuracy": tot("type_correct") / matched if matched else None,
        "type_accuracy_lenient": tot("type_correct_lenient") / matched if matched else None,
        "door_recall": tot("doors_found") / tot("doors_true") if tot("doors_true") else 0.0,
        "door_precision": tot("doors_found") / tot("doors_pred") if tot("doors_pred") else 0.0,
        "area_error_total_mean": _mean([s["area_error_total"] for s in sc]),
        "area_error_total_median": (statistics.median(v) if (v := [s["area_error_total"] for s in sc
                                                                   if s["area_error_total"] is not None]) else None),
        "area_error_rooms_mean": statistics.fmean(room_errs) if room_errs else None,
        "area_error_rooms_median": statistics.median(room_errs) if room_errs else None,
        "check_house_share": _share([b.get("check_house") for b in builds]) if rows else None,
        "layout_zero_errors_share": _share([b.get("layout_errors") == 0 for b in builds]) if rows else None,
        "truth_check_house_share": _share([t.get("check_house") for t in truths]) if rows else None,
        "truth_layout_zero_errors_share": _share([t.get("layout_errors") == 0 for t in truths]) if rows else None,
        "seconds_mean": _mean([row.get("seconds") for row in rows]),
        "seconds_total": sum(row.get("seconds") or 0 for row in rows),
        "cost_usd_mean": _mean([row.get("cost_usd") for row in rows]),
        "cost_usd_total": sum(row.get("cost_usd") or 0 for row in rows),
    }


# --- the import of a reading --------------------------------------------------------------------------------------

def build_check(plan_px: dict, m_per_px: float, catalog: dict | None) -> dict:
    """What `vesper lab import` makes of a plan in pixels: scaled by its own scale (else by `m_per_px`, the true one,
    as if the user had typed it), straightened (what it left out or narrowed in "warnings"), checked to be a house;
    then, with a catalog, built into a home in a temporary folder and its layout checked. "stage" is where it stopped
    ("done" when the layout was checked)."""
    from vesper.lab.generate import GenerateError, Request, generate_from_plan, starter
    from vesper.lab.plan import PlanError, check_house, rectify, scale_plan
    own = plan_px.get("scale") is not None or plan_px.get("units") == "m"
    out = {"scale_from": "the plan's own scale" if own else "the true scale", "stage": "scale", "check_house": False,
           "problems": [], "warnings": [], "layout_errors": None, "layout_warnings": None, "layout_failures": [],
           "devices": None}
    try:
        scaled = scale_plan(plan_px if own else {**plan_px, "scale": {"m_per_px": m_per_px}})
        out["stage"] = "rectify"
        plan = rectify(scaled, out["warnings"])
        out["stage"] = "check_house"
        out["problems"] = check_house(plan)
    except PlanError as e:
        out["problems"] = [str(e)]
        return out
    if out["problems"]:
        return out
    out["check_house"] = True
    if catalog is None:
        return out
    out["stage"] = "build"
    from vesper.lab.home import load_lab_home
    from vesper.lab.layoutcheck import check
    types = [r["type"] for r in plan["rooms"]]
    try:
        with tempfile.TemporaryDirectory(prefix="vesper-plans-") as tmp:
            devices, _left = starter(types, catalog)
            home_dir = Path(tmp) / "home"
            generate_from_plan(plan, Request(Path("."), types, 0, devices), catalog, home_dir, source="plan.json")
            home = load_lab_home(home_dir / "home.yaml", catalog)
            out["devices"] = len(home.devices)
            out["stage"] = "layout"
            report = check(home)
    except (GenerateError, PlanError, ValueError, OSError) as e:
        out["problems"] = [f"{type(e).__name__}: {e}"]
        return out
    out.update(stage="done", layout_errors=report["errors"], layout_warnings=report["warnings"],
               layout_failures=[f"{f['rule']}: {f['message']}" for f in report["findings"]
                                if not f["ok"] and f["severity"] == "error"])
    return out


# --- the sample and its files -------------------------------------------------------------------------------------

def sample(ids: list[str], n: int, seed: int) -> list[str]:
    """`n` plan ids (such as "high_quality/1245") from the split's list, in a seeded order: the sample of n is the
    start of the sample of any larger number, so a larger run reuses the readings of a smaller one."""
    clean = []
    for raw in ids:
        pid = raw.strip().strip("/")
        if not pid:
            continue
        if not _PLAN_ID.match(pid):
            raise ValueError(f"{raw!r} is not a CubiCasa5K plan folder (like /high_quality/1245/)")
        clean.append(pid)
    order = list(clean)
    random.Random(seed).shuffle(order)
    return order[:n]


class _Ranged(io.RawIOBase):
    """A remote file read with HTTP range requests: the zip's directory and the members asked for, nothing else."""

    def __init__(self, url: str):
        import httpx
        self._http = httpx.Client(follow_redirects=True, timeout=120)
        self._url = url
        head = self._http.head(url)
        head.raise_for_status()
        self._size = int(head.headers["content-length"])
        self._pos = 0
        self.fetched = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self._pos = offset if whence == 0 else self._pos + offset if whence == 1 else self._size + offset
        return self._pos

    def readinto(self, buf) -> int:
        if self._pos >= self._size or not len(buf):
            return 0
        end = min(self._size, self._pos + len(buf)) - 1
        r = self._http.get(self._url, headers={"Range": f"bytes={self._pos}-{end}"})
        r.raise_for_status()
        if r.status_code != 206:
            raise OSError(f"{self._url}: the server ignored the range request (status {r.status_code})")
        data = r.content
        buf[:len(data)] = data
        self._pos += len(data)
        self.fetched += len(data)
        return len(data)


def _safe(name: str) -> str:
    parts = name.split("/")
    if not name or name.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{name!r} is not a file inside the dataset")
    return name


def fetch(dest: Path, names: list[str], opener=None) -> list[str]:
    """Copy the named files (relative to the archive's `cubicasa5k/` folder) that `dest` lacks out of the published
    archive, by range requests (`opener()` returns a seekable file of the zip instead, for tests). Their sha256 go to
    `dest/MANIFEST.json` with the source and licence. Returns the names copied."""
    dest = Path(dest)
    missing = [n for n in map(_safe, names) if not (dest / n).is_file()]
    if not missing:
        return []
    raw = opener() if opener is not None else io.BufferedReader(_Ranged(SOURCE_URL), buffer_size=1 << 16)
    manifest_path = dest / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.update(url=SOURCE_URL, record=SOURCE_RECORD, licence=LICENCE)
    files = manifest.setdefault("files", {})
    with zipfile.ZipFile(raw) as z:
        for name in missing:
            data = z.read(ARCHIVE_ROOT + name)  # the archive's CRC is checked as it is read
            path = dest / name
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            part.write_bytes(data)
            part.replace(path)
            files[name] = hashlib.sha256(data).hexdigest()
    manifest["fetched_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return missing


def _png_size(path: Path) -> list[int] | None:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return list(struct.unpack(">II", head[16:24]))


# --- reading and measuring ----------------------------------------------------------------------------------------

def _envelope_facts(text: str) -> dict:
    try:
        env = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(env, dict):
        return {}
    return {"cost_usd": env.get("total_cost_usd") or 0.0, "duration_ms": env.get("duration_ms") or 0,
            "models": sorted(env.get("modelUsage") or {}), "usage": env.get("usage")}


def _read(image: Path, folder: Path, runner, model: str | None, timeout: float) -> tuple[dict | None, str | None]:
    """Read one image, saving each answer as it arrives (envelope-<k>.json), then plan.json or error.txt and
    meta.json: nothing paid for is lost if a later step fails."""
    from vesper.lab import plan_read
    from vesper.lab.plan import PlanError
    folder.mkdir(parents=True, exist_ok=True)
    inner = runner or plan_read._claude
    calls: list[dict] = []

    def run(args, cwd=None, timeout=plan_read.TIMEOUT_S):
        t0 = time.monotonic()
        out = inner(args, cwd=cwd, timeout=timeout)
        k = len(calls) + 1
        (folder / f"envelope-{k}.json").write_text(out)
        calls.append({"seconds": time.monotonic() - t0, **_envelope_facts(out)})
        return out
    started, t0 = datetime.now(UTC), time.monotonic()
    plan, error = None, None
    try:
        plan = plan_read.read_plan_image(image, runner=run, model=model, timeout=timeout)
    except PlanError as e:
        error = str(e)
    seconds = time.monotonic() - t0
    if plan is not None:
        (folder / "plan.json").write_text(json.dumps(plan, indent=1) + "\n")
    else:
        (folder / "error.txt").write_text(error + "\n")
    meta = {"image": image.name, "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "model_arg": model, "models": sorted({m for c in calls for m in c.get("models", [])}),
            "calls": len(calls), "seconds": seconds, "cost_usd": sum(c.get("cost_usd", 0.0) for c in calls),
            "duration_ms": sum(c.get("duration_ms", 0) for c in calls), "per_call": calls,
            "read_at": started.isoformat(timespec="seconds"), "ok": plan is not None}
    (folder / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return plan, error


def readings_dir(dataset: Path) -> Path:
    return Path(dataset) / "readings"


def evaluate(dataset: Path, n: int, seed: int, runner=None, model: str | None = None, timeout: float | None = None,
             catalog: dict | None = None, offline: bool = False, fetch_missing: bool = False, res: float = GRID_PX,
             say=lambda text: None) -> dict:
    """The evaluation on `n` seeded plans of the test split in `dataset` (CubiCasa5K's folders, `test.txt`). A plan
    with a saved reading is measured from it; any other is read now (one at a time), or, `offline`, listed in
    "not_read". `fetch_missing` copies missing dataset files from the archive first (`fetch`)."""
    from vesper.lab import plan_read
    dataset = Path(dataset)
    timeout = plan_read.TIMEOUT_S if timeout is None else timeout
    if fetch_missing:
        fetch(dataset, ["test.txt"])
    ids = sample((dataset / "test.txt").read_text().splitlines(), n, seed)
    need = [f"{pid}/{f}" for pid in ids for f in (TRUTH, IMAGE)]
    if fetch_missing and (got := fetch(dataset, need)):
        say(f"fetched {len(got)} file(s) from {SOURCE_URL}")
    missing = [x for x in need if not (dataset / x).is_file()]
    if missing:
        raise FileNotFoundError(f"{dataset}: missing {', '.join(missing[:4])}{' ...' if len(missing) > 4 else ''} "
                                "(give --fetch to copy them from the archive)")
    rows, not_read = [], []
    for k, pid in enumerate(ids, 1):
        truth = parse_cubicasa_svg((dataset / pid / TRUTH).read_text(encoding="utf-8"))
        folder = readings_dir(dataset) / pid.replace("/", "-")
        image = dataset / pid / IMAGE
        if (folder / "plan.json").is_file():
            plan, error = json.loads((folder / "plan.json").read_text()), None
        elif (folder / "error.txt").is_file():
            plan, error = None, (folder / "error.txt").read_text().strip()
        elif offline:
            not_read.append(pid)
            continue
        else:
            say(f"[{k}/{len(ids)}] reading {pid} with Claude ...")
            plan, error = _read(image, folder, runner, model, timeout)
        meta = json.loads((folder / "meta.json").read_text()) if (folder / "meta.json").is_file() else {}
        row = {"id": pid, "image_px": _png_size(image), "truth_rooms": len(truth["plan"]["rooms"]),
               "truth_scale_source": truth["scale_source"], "truth_m_per_px": truth["m_per_px"],
               "truth_floors": truth["floors"], "truth_openings": truth["openings"],
               "seconds": meta.get("seconds"), "cost_usd": meta.get("cost_usd"), "calls": meta.get("calls"),
               "models": meta.get("models"), "error": error, "score": score(plan, truth, res=res),
               "build": build_check(plan, truth["m_per_px"], catalog) if plan is not None else None,
               "truth_build": build_check(truth["plan"], truth["m_per_px"], catalog)}
        rows.append(row)
        sc = row["score"]
        say(f"[{k}/{len(ids)}] {pid}: rooms {sc['rooms_matched']}/{sc['rooms_true']} (read {sc['rooms_pred']}), "
            f"doors {sc['doors_found']}/{sc['doors_true']}" + (f", FAILED: {error[:120]}" if error else ""))
    return {"protocol": {"dataset": "CubiCasa5K test split", "source": SOURCE_URL, "licence": LICENCE, "n": n,
                         "seed": seed, "model": model, "image": IMAGE, "iou_min": IOU_MIN,
                         "door_reach_m": DOOR_REACH_M, "grid_px": res, "class_types": CLASS_TYPES},
            "plans": rows, "not_read": not_read, "aggregate": aggregate(rows)}
