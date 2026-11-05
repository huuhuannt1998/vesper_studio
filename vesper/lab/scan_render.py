"""A room or house mesh read by Claude from a top-down render at a known scale (scan import, task B3b).

The geometric reader (`scan_mesh.read_mesh`) is honest but weak on real scans: a closed door reads as a wall, windows
are rarely found and a house comes out as too few rooms. Part A's Claude reader reads drawn floor plans well. This
module draws the mesh as a floor plan and lets that reader read it:

1. **Frame.** The model is put upright, scaled to metres, its floor at 0 and its walls along x and z by
   `scan_mesh.upright_mesh`: read_mesh's own up axis, storey, scale and wall direction, nothing detected twice.
2. **Render.** The model is cut 1.3 m above the floor (lower in a storey under 2.4 m) and drawn from straight above
   at an exact scale (`px_per_m`, 50 by default, so 2 cm a pixel), x east to the right and z south downward (the
   plan's own frame): the walls the cut passes through thick and black, the floor light beige, what stands lower
   than the cut (furniture, counters, low walls) grey, white where nothing was scanned. A window is drawn blue where
   the cut is open but the wall stands both below (at the sill cut, 0.5 m) and above (at the lintel cut, 2.15 m or
   just under the ceiling). Doors are gaps: nothing is drawn in a doorway but its floor. The image is kept within
   1568 px a side and 1.15 megapixels, so it reaches Claude unresized; a larger home is drawn at fewer pixels per
   metre (said in a warning; the scale is still exact).
3. **Read.** `plan_read.read_plan_image` reads the PNG with a short note on what it is (`context`; its instructions
   are unchanged). The plan Claude gives, in the image's pixels, is scaled by the render's exact metres per pixel
   (no --scale is asked for), moved from the image's corner to the model's frame and then so its rooms start at
   (0, 0), as read_mesh's do; the meta's transform takes the file to that frame, so the model can be placed under
   the plan as `scene.glb`.

The render goes to Anthropic through the user's Claude Code session, as a floor-plan image does in part A; nothing
else of the model does. trimesh, numpy and scipy are the `scan` extra.
"""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from vesper.lab import plan_read
from vesper.lab.plan import PlanError, scale_plan
from vesper.lab.png import Image, encode
from vesper.lab.scan_mesh import (
    HORIZONTAL,
    LINTEL_SLICE_M,
    SILL_SLICE_M,
    ScanError,
    _disk,
    _libs,
    _raster,
    _raster_segments,
    _segments,
    upright_mesh,
)

PX_PER_M = 50  # the render's default scale: 2 cm a pixel
CUT_M = 1.3  # the walls are cut this high above the floor (between a window's sill and its head)
LOW_STOREY_M = 2.4  # in a storey lower than this the cut is at CUT_SHARE of it
CUT_SHARE = 0.5
FLOOR_BAND_M = 0.1  # up-facing faces this close to the floor's height are floor
FURNITURE_FROM_M = 0.15  # and geometry from this high to the cut is drawn grey (what stands on the floor)
WALL_FILL_M = 0.2  # gaps in the cut narrower than twice this are closed: a wall's two faces, holes in a scan
WALL_LINE_M = 0.04  # a wall cut is drawn at least this thick to each side of its line
WINDOW_LEAST_M = 0.3  # a window is in line with the walls (it touches a cut wall) and at least this long
PAD_M = 0.5  # white margin around the home
MAX_SIDE_PX = 1568  # Claude sees an image up to this size a side, and this many pixels, without resizing it
MAX_PIXELS = 1_150_000
MAX_EXTENT_M = 150.0  # a model wider than this is cropped to its floor

COLOURS = {  # RGBA
    "outside": (255, 255, 255, 255),
    "floor": (238, 228, 205, 255),
    "furniture": (160, 160, 160, 255),
    "window": (40, 120, 230, 255),
    "wall": (0, 0, 0, 255),
}

CONTEXT = """This picture is not a drawn floor plan: VESPER rendered it from a 3D scan (or a 3D model) of a home,
seen from straight above and cut horizontally {cut:.2f} m above the floor. It is {w} x {h} pixels and its scale is
exact: {ppm:g} pixels per metre, so answer "scale": {{"m_per_px": {mpp:g}}} and give every coordinate and width in
those pixels. What it shows: black is where the cut passes through the walls (and through anything else as tall, such
as a tall cupboard); blue marks a window (wall below and above it, open at the cut); light beige is floor; grey is
what stands lower than the cut (furniture, counters, beds, low walls), seen from above; white is outside or not
scanned. Doors are not drawn: a doorway is a gap in a black wall, usually 0.7-1.0 m ({d0:.0f}-{d1:.0f} px) wide, with
floor through it; a door to the outdoors is such a gap in an outside wall. A scan has holes and noise: a break in a wall
with no floor through it, a ragged outline, or stray marks outside the walls are not rooms or doors. Nothing is
labelled: name each room and choose its type from what stands in it (a bed: bedroom; a toilet, bath or shower:
bathroom; a stove, a sink and a run of counters: kitchen; a sofa: living; a table with chairs around it: dining; a
long narrow passage: corridor); when nothing tells, use "other"."""


def _cut_height(ceiling, warnings) -> float:
    if ceiling is not None and ceiling < LOW_STOREY_M:
        cut = CUT_SHARE * ceiling
        warnings.append(f"the storey is {ceiling:.2f} m high: the render cuts it {cut:.2f} m above the floor")
        return cut
    return CUT_M


def _extent(tris, normals, cut, np):
    """The plan area to draw, (lo, hi) in metres: what the cut meets and the floor, both padded; when they differ
    (a ground plane, a spill outside the walls), the part where they overlap; within MAX_EXTENT_M of the floor."""
    seg = _segments(tris, cut, np).reshape(-1, 2)
    floor_sel = (normals[:, 1] > HORIZONTAL) & (np.abs(tris[:, :, 1].mean(axis=1)) < FLOOR_BAND_M)
    floor = tris[floor_sel][:, :, [0, 2]].reshape(-1, 2)
    boxes = [(p.min(axis=0) - PAD_M, p.max(axis=0) + PAD_M) for p in (seg, floor) if len(p)]
    if not boxes:
        raise ScanError("nothing to draw: the model has no floor and nothing stands at the cut")
    lo, hi = boxes[0]
    if len(boxes) == 2:
        lo2, hi2 = np.maximum(boxes[0][0], boxes[1][0]), np.minimum(boxes[0][1], boxes[1][1])
        if (hi2 > lo2).all():
            lo, hi = lo2, hi2
    if ((hi - lo) > MAX_EXTENT_M).any():
        raise ScanError(f"the model is {float((hi - lo).max()):.0f} m across: too large for a home (give --scale if "
                        "its units are not metres)")
    return lo, hi


def _draw(tris, normals, ceiling, px_per_m, np, ndi):
    """(RGBA array (h, w, 4) uint8, origin [x, z], px_per_m used, cut height, warnings)."""
    warnings: list[str] = []
    cut = _cut_height(ceiling, warnings)
    lo, hi = _extent(tris, normals, cut, np)
    span = hi - lo
    fit = min(MAX_SIDE_PX / float(span.max()), math.sqrt(MAX_PIXELS / float(span[0] * span[1])))
    if px_per_m > fit:
        used = math.floor(fit)
        warnings.append(f"the home is {span[0]:.1f} x {span[1]:.1f} m: drawn at {used} px per metre (not "
                        f"{px_per_m:g}) to stay within what Claude sees unresized")
        px_per_m = used
    if px_per_m < 10:
        raise ScanError(f"the model is {float(span.max()):.0f} m across: too large to draw at 10 px per metre or more "
                        "(give --scale if its units are not metres)")
    cell = 1.0 / px_per_m
    shape = (int(math.ceil(float(span[0]) / cell)), int(math.ceil(float(span[1]) / cell)))
    origin = lo

    y = tris[:, :, 1]
    ymean, ymin, ymax = y.mean(axis=1), y.min(axis=1), y.max(axis=1)
    horizontal = normals[:, 1] > HORIZONTAL
    floor_sel = horizontal & (np.abs(ymean) < FLOOR_BAND_M)
    floor = _raster(tris[floor_sel][:, :, [0, 2]], origin, cell, shape, np)
    floor = ndi.binary_closing(floor, structure=_disk(2, np)) | floor
    low = (ymax > FURNITURE_FROM_M) & (ymin < cut) & ~((np.abs(normals[:, 1]) > HORIZONTAL)
                                                         & (ymean < FURNITURE_FROM_M))
    furniture = _raster(_clip_below(tris[low], cut, np)[:, :, [0, 2]], origin, cell, shape, np)

    def cut_at(height):
        line = _raster_segments(_segments(tris, height, np), origin, cell, shape, np)
        line = ndi.binary_dilation(line, structure=_square(max(1, round(WALL_LINE_M * px_per_m)), np))
        return ndi.binary_closing(line, structure=_square(round(WALL_FILL_M * px_per_m), np)) | line

    walls = cut_at(cut)
    lintel = LINTEL_SLICE_M if ceiling is None else min(LINTEL_SLICE_M, ceiling - 0.1)
    window = cut_at(SILL_SLICE_M) & cut_at(lintel) & ~walls
    window = _touching(window, walls, round(WINDOW_LEAST_M * px_per_m), np, ndi)

    img = np.empty(shape + (4,), dtype=np.uint8)
    img[:] = COLOURS["outside"]
    for name, mask in (("floor", floor), ("furniture", furniture), ("window", window), ("wall", walls)):
        img[mask] = COLOURS[name]
    # grid index (i along x, j along z) -> image row j (z down), column i (x right)
    return np.ascontiguousarray(img.transpose(1, 0, 2)), [float(origin[0]), float(origin[1])], px_per_m, cut, \
        warnings


def _square(r, np):
    """A square structuring element of half-side r: the walls run along the image's axes, and a square keeps their
    corners square where a disk would round them."""
    return np.ones((2 * r + 1, 2 * r + 1), dtype=bool)


def _touching(mask, walls, least, np, ndi):
    """The parts of `mask` that touch `walls` and are at least `least` pixels long one way or the other."""
    labels, n = ndi.label(mask)
    if n == 0:
        return mask
    near = ndi.binary_dilation(walls, iterations=2)
    keep = np.zeros(n + 1, dtype=bool)
    for k, sl in enumerate(ndi.find_objects(labels), start=1):
        part = labels[sl] == k
        long_enough = max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) >= least
        keep[k] = long_enough and bool((part & near[sl]).any())
    return keep[labels]


def _clip_below(tris, cut, np):
    """The triangles with their vertices above the cut pulled down to it: only what lies below the cut is drawn grey
    (the top of a wall over a window is not furniture). A projection, so pulling vertices down is enough."""
    out = tris.copy()
    out[:, :, 1] = np.minimum(out[:, :, 1], cut)
    return out


def render_plan_view(path: Path, out_png: Path, px_per_m: float = PX_PER_M, scale: float | None = None) -> dict:
    """Draw the mesh at `path` as a top-down floor plan, cut just over a metre above its floor, into the PNG `out_png`.

    Returns {"m_per_px": metres a pixel (exact), "origin": [x, z] in metres of the image's top-left corner in the
    upright frame, "transform": the 4x4 (row-major lists) taking a point of the file to that frame (x east, y up with
    the floor at 0, z south, metres), "warnings": [...], "meta": `scan_mesh.upright_mesh`'s meta (up, scale,
    floor_y, ceiling_y), "size": [width, height] in pixels, "cut_m": the cut's height}. A point (u, v) of the image
    (pixels, v down) is (origin[0] + u * m_per_px, origin[1] + v * m_per_px) on the floor of that frame. `scale`
    (metres per unit) replaces the detected one. ScanError when the model cannot be put upright or scaled."""
    np, ndi, _ = _libs()
    if isinstance(px_per_m, bool) or not isinstance(px_per_m, (int, float)) or not 10 <= px_per_m <= 500:
        raise ScanError(f"px_per_m {px_per_m!r}: expected a number of pixels per metre from 10 to 500")
    tris, normals, _areas, meta, warnings = upright_mesh(path, scale)
    rgba, origin, used, cut, more = _draw(tris, normals, meta["ceiling"], px_per_m, np, ndi)
    h, w = rgba.shape[:2]
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.write_bytes(encode(Image(w, h, rgba.tobytes())))
    return {"m_per_px": 1.0 / used, "origin": origin, "transform": meta["transform"], "warnings": warnings + more,
            "meta": {k: meta[k] for k in ("up", "scale", "floor_y", "ceiling_y")}, "size": [w, h], "cut_m": cut}


ENTRANCE_PROBE_M = 0.1  # the render is looked at this far round the entrance's middle
WALL_SHARE = 0.5  # more of it black than this: the entrance stands in a wall, not in a gap


def _entrance_drawn(raw: dict, png: Path, ppm: float) -> bool | None:
    """Whether the entrance Claude gave stands in a gap of the render (a doorway the scan shows: floor or nothing at
    its middle), or in a black wall (Claude chose one where none is drawn, as its instructions say to when no door to
    the outside shows). None when the answer is not in pixels or names no entrance."""
    from vesper.lab.png import decode
    ent = raw.get("entrance")
    if raw.get("units") != "px" or ent is None:
        return None
    u0, v0 = raw["doors"][ent]["at"]
    img = decode(Path(png).read_bytes())
    r = max(1, round(ENTRANCE_PROBE_M * ppm))
    seen = [img.pixel(u, v)[:3] == COLOURS["wall"][:3]
            for u in range(round(u0) - r, round(u0) + r + 1) for v in range(round(v0) - r, round(v0) + r + 1)
            if 0 <= u < img.width and 0 <= v < img.height and (u - u0) ** 2 + (v - v0) ** 2 <= r * r]
    return bool(seen) and sum(seen) / len(seen) <= WALL_SHARE


def _points(plan: dict):
    for r in plan["rooms"]:
        yield from r["polygon"]
    for d in plan.get("doors") or []:
        yield d["at"]
    for w in plan.get("windows") or []:
        yield w["at"]


def _moved(plan: dict, dx: float, dz: float) -> dict:
    for r in plan["rooms"]:
        r["polygon"] = [[round(x + dx, 3), round(z + dz, 3)] for x, z in r["polygon"]]
    for item in (plan.get("doors") or []) + (plan.get("windows") or []):
        item["at"] = [round(item["at"][0] + dx, 3), round(item["at"][1] + dz, 3)]
        item["width"] = round(item["width"], 3)
    return plan


def read_mesh_with_claude(path: Path, runner=None, *, scale: float | None = None, model: str | None = None,
                          timeout: float = plan_read.TIMEOUT_S, stop=None, render_to: Path | None = None,
                          px_per_m: float = PX_PER_M) -> tuple[dict, dict, list[str]]:
    """The floor plan of the mesh at `path`, read by Claude from its top-down render, as (`vesper-plan/1` in metres,
    meta, warnings): the same triple as `scan_mesh.read_mesh`, for the same next steps (`rectify`, `check_house`,
    the model placed as `scene.glb` by meta["transform"]).

    meta: read_mesh's keys ("up", "scale", "floor_y", "ceiling_y", "transform": the file to the plan's frame, x east,
    y up with the floor at 0, z south, metres, the rooms starting at (0, 0)) and "reader": "claude", "m_per_px",
    "cut_m", "floors_drawn" (Claude's count), "render" (the PNG's path when `render_to` keeps it, else None) and
    "entrance_drawn" (`_entrance_drawn`: False when Claude put the entrance in a drawn wall).
    `runner`, `model`, `timeout` and `stop` are `plan_read.read_plan_image`'s; `scale` replaces the detected metres
    per unit. Raises ScanError when the model cannot be drawn and PlanError when Claude's answers are not a plan (with
    Claude's text), as read_plan_image does."""
    tmp = Path(tempfile.mkdtemp(prefix="vesper-scan-"))
    try:
        png = Path(render_to) if render_to is not None else tmp / "plan.png"
        view = render_plan_view(path, png, px_per_m=px_per_m, scale=scale)
        mpp, (w, h) = view["m_per_px"], view["size"]
        ppm = 1.0 / mpp
        context = CONTEXT.format(cut=view["cut_m"], w=w, h=h, ppm=round(ppm, 6), mpp=round(mpp, 6), d0=0.7 * ppm,
                                 d1=1.0 * ppm)
        said: dict = {}
        raw = plan_read.read_plan_image(png, runner=runner, model=model, timeout=timeout, stop=stop, meta=said,
                                        context=context)
        drawn = _entrance_drawn(raw, png, ppm)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    warnings = list(view["warnings"])
    stated = raw.get("scale")
    if isinstance(stated, dict) and "m_per_px" in stated and not math.isclose(stated["m_per_px"], mpp, rel_tol=0.05):
        warnings.append(f"Claude stated a scale of {stated['m_per_px']:g} m per pixel; the render's exact "
                        f"{mpp:g} is used")
    if raw["units"] == "m":
        warnings.append("Claude answered in metres, not in the image's pixels: taken as metres from its top-left "
                        "corner")
        factor_plan = scale_plan(raw)
    else:
        pts = list(_points(raw))
        far = [p for p in pts if not (-0.02 * w <= p[0] <= 1.02 * w and -0.02 * h <= p[1] <= 1.02 * h)]
        if far:
            warnings.append(f"{len(far)} of the {len(pts)} points Claude gave lie outside the {w} x {h} image: "
                            "check the plan against the render")
        factor_plan = scale_plan({**raw, "scale": {"m_per_px": mpp}})
    # from the image's corner to the upright frame, then so the rooms start at (0, 0)
    ox, oz = view["origin"]
    corners = [p for r in factor_plan["rooms"] for p in r["polygon"]]
    sx, sz = min(p[0] for p in corners), min(p[1] for p in corners)
    plan_m = _moved(factor_plan, -sx, -sz)
    t = [row[:] for row in view["transform"]]
    t[0][3] -= ox + sx
    t[2][3] -= oz + sz
    meta = {**view["meta"], "transform": t, "reader": "claude", "m_per_px": mpp, "cut_m": view["cut_m"],
            "floors_drawn": said.get("floors_drawn"), "render": str(png) if render_to is not None else None,
            "entrance_drawn": drawn}
    if said.get("floors_drawn") not in (None, 1):
        warnings.append(f"Claude saw {said['floors_drawn']} floors in the render of one storey: check the plan")
    return plan_m, meta, warnings


__all__ = ["PX_PER_M", "PlanError", "ScanError", "read_mesh_with_claude", "render_plan_view"]
