"""Any room or house mesh (GLB, glTF, OBJ, PLY) as a `vesper-plan/1` in metres: scan import, part B, task B3.

A photogrammetry or LiDAR scan, or a model from the web, names nothing: its floor plan is read from its shape alone.

1. **Up axis.** Detected, never assumed (Habitat's GLBs are Z-up inside glTF): for each of the file's three axes, the
   faces square to it are histogrammed by height, those facing along it (floors, tops) apart from those facing
   against it (ceilings, undersides). The up axis is the one where a floor and a ceiling 2.1-4.0 apart cover the
   most of each other (the model is then in metres), else the one with the largest flat layer; the axis along which
   the model is thinnest is preferred, as a house is wider than it is tall. Which way is up: furniture tops face up
   in the lower half of a room.
2. **Storeys.** A storey is a floor with a ceiling over it. With several, the lowest is read (the floor the entrance
   is usually on), with a warning; a large ground plane under the house is not its floor: the floor under a ceiling
   is the highest one under it.
3. **Scale.** Floor to ceiling 2.1-4.0: metres. Another height: the factor putting the ceiling at 2.5 m, said in a
   warning. No ceiling: the walls' height, if 2.1-4.0, else the doors' width (0.85 m a door); with neither, refused
   ("the scale looks wrong: ... give --scale"). Never guessed silently; `scale` overrides all of it.
4. **Slices.** The model, turned upright and so that its walls run along x and z, is cut at 0.1, 0.5, 1.0, 1.6 and
   2.15 m above the floor into occupancy grids of 5 cm cells. A free run of 0.6-1.6 m along a wall at 1.0 m, open at
   0.1 m and closed above (a lintel, or the ceiling) is a door; a run walled at 0.5 m and above 2.15 m but open at
   1.0-1.6 m is a window. Both are closed for finding rooms.
5. **Rooms.** The free regions of the 1.0 m grid that do not reach the grid's edge, are at least 1.5 m², at least
   60 cm wide, mostly floored and not the inside of a solid object (a stair, a wardrobe) are rooms. Each grows to the
   middle of its walls, its holes (furniture) are filled, and its outline is straightened to the plan's two
   directions with no step under 10 cm. Rooms are typed "other" and named "room 1..N": nothing is invented.
6. **Doors and windows** join the rooms (or a room and the outside) on their two sides; the entrance is the widest
   door to the outside.

The result goes through part A's `rectify` and `check_house` unchanged. trimesh, numpy and scipy are the optional
`scan` extra; without them `read_mesh` says which extra to install. Downloaded models are data: they are only read by
trimesh, never executed.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from vesper.lab.plan import FORMAT, load_plan

SUFFIXES = (".glb", ".gltf", ".obj", ".ply")
EXTRA = "install the scan extra: pip install 'vesper[scan]'"

CELL_M = 0.05  # the occupancy grids' cell
FLOOR_SLICE_M = 0.1  # a doorway has no wall below this
SILL_SLICE_M = 0.5  # a window has wall here (its sill)
WALL_SLICE_M = 1.0  # the walls: the rooms are the free regions of this slice
HEAD_SLICE_M = 1.6  # a window is open from WALL_SLICE_M to here
LINTEL_SLICE_M = 2.15  # a door and a window have wall here (a lintel)
CEILING_M = (2.1, 4.0)  # a floor-to-ceiling height in this range: the model is in metres
CEILING_DEFAULT_M = 2.5  # a model in other units is scaled to put its ceiling here
DOOR_M = (0.6, 1.6)  # a doorway's width
DOOR_PLAUSIBLE_M = (0.6, 1.3)  # doors of a model in metres are on the whole this wide, else a warning
DOOR_TYPICAL_M = 0.85  # with no ceiling and no wall height, a model is scaled to make its doors this wide
WINDOW_M = (0.3, 4.0)  # a window's width
ROOM_LEAST_M2 = 1.5  # a free region smaller than this is not a room
ROOM_HALF_WIDTH_M = 0.3  # nor one with no point this far from its walls (a wall's hollow, a gap behind a cupboard)
FLOORED = 0.5  # a room's share of cells with floor under them at least this (a spill outside a window has none)
SOLID = 0.6  # a region with a top over this share of it, between 1 m and the ceiling, is inside an object
GROW_M = 0.6  # a room grows into the walls around it up to this far (to the middle of the wall)
SIMPLIFY_M = 0.1  # a room outline has no edge shorter than this
SMALL_GAP_M = 0.5  # gaps in a slice narrower than this are closed (a wall's two faces, holes in a scan)
TOP_BELOW_M = 0.15  # a model with a ceiling is also cut this far under it: the wall over a tall opening
NEAR_WALL_M = 0.4  # that cut bounds rooms only this close to the walls below it
FLANK_M = 0.4  # an opening has this much wall (60 % of it) in line with it on both sides
HORIZONTAL = 0.9  # a face whose normal is this close to the up axis is horizontal
VERTICAL = 0.3  # and one this far from it is vertical (a wall)
COVER = 0.7  # a run counts as walled (or open) in a slice when this share of its cells is
MAX_CELLS = 3000  # a grid side longer than this (150 m) is cropped to the floor


class ScanError(ValueError):
    """A scan or model VESPER cannot read as a floor plan, with the reason."""


def _libs():
    try:
        import numpy
        import scipy.ndimage
        import trimesh
    except ImportError as e:
        raise ScanError(f"reading a 3D model needs trimesh, numpy and scipy: {EXTRA}") from e
    return numpy, scipy.ndimage, trimesh


def read_mesh(path: Path, scale: float | None = None) -> tuple[dict, dict, list[str]]:
    """The floor plan of the mesh at `path` as (`vesper-plan/1` in metres, meta, warnings), or a ScanError saying why
    not. meta: {"up": "y"|"z"|"x"|"-y"|..., the file's axis that is up; "scale": metres per file unit; "floor_y",
    "ceiling_y": the floor's and the ceiling's heights in metres in the upright, scaled model before it is moved
    (ceiling None when there is none); "transform": the 4x4 matrix (row-major lists) taking a point of the file to
    the plan's frame: x east, y up (the floor at 0), z south, in metres, the walls along x and z}. `scale`, when given,
    is metres per unit and replaces the detected one."""
    np, _, trimesh = _libs()
    path = Path(path)
    if path.suffix.lower() not in SUFFIXES:
        raise ScanError(f"{path.name}: a mesh must be one of {', '.join(SUFFIXES)}")
    if scale is not None and (isinstance(scale, bool) or not isinstance(scale, (int, float))
                              or not math.isfinite(scale) or not 0 < scale < 1e9):
        raise ScanError(f"the scale {scale!r} must be a number of metres per unit above 0")
    tris = _load(path, np, trimesh)
    return _read(tris, scale, np)


# --- loading -----------------------------------------------------------------------------------------------------

def _load(path: Path, np, trimesh):
    """Every triangle of the file in its own frame, as an (n, 3, 3) array; ScanError when there are none."""
    try:
        loaded = trimesh.load(str(path), force="scene", skip_materials=True, process=False)
    except Exception as e:  # trimesh raises many kinds on a broken file
        raise ScanError(f"{path.name}: cannot be read as a mesh ({type(e).__name__}: {e})") from e
    parts = []
    for node in loaded.graph.nodes_geometry:
        transform, name = loaded.graph[node]
        geom = loaded.geometry.get(name)
        faces = getattr(geom, "faces", None)
        if faces is None or len(faces) == 0:
            continue
        v = np.asarray(geom.vertices, dtype=np.float64)
        v = v @ np.asarray(transform)[:3, :3].T + np.asarray(transform)[:3, 3]
        parts.append(v[np.asarray(faces)])
    if not parts:
        raise ScanError(f"{path.name}: has no triangles")
    tris = np.concatenate(parts)
    tris = tris[np.isfinite(tris).all(axis=(1, 2))]
    if len(tris) == 0:
        raise ScanError(f"{path.name}: has no triangles with finite coordinates")
    return tris


# --- histograms, rasters -----------------------------------------------------------------------------------------

def _normals(tris, np):
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    norm = np.linalg.norm(cross, axis=1)
    ok = norm > 0
    n = np.zeros_like(cross)
    n[ok] = cross[ok] / norm[ok, None]
    return n, norm / 2


def _peaks(h, w, lo, span, np, most=16):
    """The heights where much horizontal area lies: [(height, area)], at most `most`, sorted by height. Bins of
    span/1000, a peak is the area within 0.5 % of the span of its centre, each peak at least 3 % of the largest."""
    if len(h) == 0 or span <= 0:
        return []
    nb = 1000
    width = span / nb
    idx = np.clip(((h - lo) / width).astype(np.int64), 0, nb - 1)
    hist = np.bincount(idx, weights=w, minlength=nb)
    win = max(2, nb // 200)
    cum = np.concatenate([[0.0], np.cumsum(hist)])
    k = np.arange(nb)
    smooth = cum[np.minimum(k + win + 1, nb)] - cum[np.maximum(k - win, 0)]
    out = []
    first = None
    while len(out) < most:
        i = int(np.argmax(smooth))
        a = smooth[i]
        if a <= 0 or (first is not None and a < 0.03 * first):
            break
        first = a if first is None else first
        sel = (idx >= i - win) & (idx <= i + win)
        height = float(np.average(h[sel], weights=w[sel])) if w[sel].sum() > 0 else lo + (i + 0.5) * width
        out.append((height, float(a)))
        smooth[max(0, i - 2 * win):i + 2 * win + 1] = 0
    return sorted(out)


def _raster(tri2, origin, cell, shape, np):
    """The cells (of a grid with `origin`, `cell` and `shape`) a set of 2D triangles (n, 3, 2) covers. Each triangle
    is sampled on a barycentric lattice finer than half a cell."""
    grid = np.zeros(shape, dtype=bool)
    if len(tri2) == 0:
        return grid
    edge = np.max(np.linalg.norm(tri2 - np.roll(tri2, 1, axis=1), axis=2), axis=1)
    k = np.clip(np.ceil(edge / (cell * 0.5)).astype(np.int64), 1, 4096)
    levels = 2 ** np.ceil(np.log2(k)).astype(np.int64)
    for lev in np.unique(levels):
        sel = tri2[levels == lev]
        a = np.arange(lev + 1)
        u, v = np.meshgrid(a, a, indexing="ij")
        keep = (u + v) <= lev
        u = u[keep] / lev
        v = v[keep] / lev
        chunk = max(1, 4_000_000 // len(u))
        for s in range(0, len(sel), chunk):
            t = sel[s:s + chunk]
            p = (t[:, None, 0] + u[None, :, None] * (t[:, None, 1] - t[:, None, 0])
                 + v[None, :, None] * (t[:, None, 2] - t[:, None, 0])).reshape(-1, 2)
            _mark(grid, p, origin, cell, np)
    return grid


def _mark(grid, p, origin, cell, np):
    ij = np.floor((p - origin) / cell).astype(np.int64)
    ok = (ij[:, 0] >= 0) & (ij[:, 0] < grid.shape[0]) & (ij[:, 1] >= 0) & (ij[:, 1] < grid.shape[1])
    grid[ij[ok, 0], ij[ok, 1]] = True


def _segments(tris, height, np):
    """The cut of the triangles at y = height: (n, 2, 2) segments in (x, z)."""
    y = tris[:, :, 1]
    sel = (y.min(axis=1) < height) & (y.max(axis=1) > height)
    t = tris[sel]
    if len(t) == 0:
        return np.zeros((0, 2, 2))
    d = t[:, :, 1] - height
    d = np.where(d == 0, 1e-12, d)
    pts = []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        di, dj = d[:, i], d[:, j]
        cross = (di > 0) != (dj > 0)
        f = np.where(cross, di / np.where(cross, di - dj, 1.0), np.nan)
        p = t[:, i] + (t[:, j] - t[:, i]) * f[:, None]
        pts.append(p[:, [0, 2]])
    pts = np.stack(pts, axis=1)  # (n, 3, 2), nan where an edge does not cross
    valid = ~np.isnan(pts[:, :, 0])
    order = np.argsort(~valid, axis=1, kind="stable")[:, :2]
    seg = np.take_along_axis(pts, order[:, :, None], axis=1)
    return seg[valid.sum(axis=1) >= 2]


def _raster_segments(seg, origin, cell, shape, np):
    grid = np.zeros(shape, dtype=bool)
    if len(seg) == 0:
        return grid
    length = np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1)
    n = np.clip(np.ceil(length / (cell * 0.5)).astype(np.int64), 1, 100_000) + 1
    which = np.repeat(np.arange(len(seg)), n)
    start = np.cumsum(n) - n
    f = (np.arange(n.sum()) - start[which]) / (n[which] - 1).clip(min=1)
    p = seg[which, 0] + (seg[which, 1] - seg[which, 0]) * f[:, None]
    _mark(grid, p, origin, cell, np)
    return grid


# --- up axis, storeys, scale -------------------------------------------------------------------------------------

_TURN = {  # source up axis -> the rotation (3x3) putting it on +y
    "y": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "-y": ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
    "z": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "-z": ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    "x": ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    "-x": ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
}


class _Layers:
    """Horizontal layers of an upright model: peaks of up-facing (floors, tops) and down-facing (ceilings,
    undersides) area, each with the cells it covers on a coarse plan grid, so a floor and a ceiling can be matched
    by how much of each other they cover."""

    def __init__(self, tris, normals, areas, np):
        self.np = np
        y = tris[:, :, 1].mean(axis=1)
        self.lo = float(tris[:, :, 1].min())
        self.span = float(tris[:, :, 1].max()) - self.lo
        xz = tris[:, :, [0, 2]]
        lo2 = xz.reshape(-1, 2).min(axis=0)
        hi2 = xz.reshape(-1, 2).max(axis=0)
        self.cell = max(float((hi2 - lo2).max()) / 160.0, 1e-12)
        self.origin = lo2
        self.shape = tuple(int(v) + 1 for v in np.ceil((hi2 - lo2) / self.cell))
        self.win = self.span / 200.0
        self.layers = {}
        for sign, key in ((1, "up"), (-1, "down")):
            sel = normals[:, 1] * sign > HORIZONTAL
            hs, ws, t = y[sel], areas[sel], xz[sel]
            peaks = _peaks(hs, ws, self.lo, self.span, np)
            layers = [(hh, a, _raster(t[np.abs(hs - hh) <= self.win], self.origin, self.cell, self.shape, np))
                      for hh, a in peaks]
            self.layers[key] = self._merge(layers)

    def _merge(self, layers):
        """Neighbouring layers less than 1.5 % of the model's height apart are one (a scanned floor is not flat: it
        spreads over a few centimetres), up to three times that thick in all."""
        gap = self.span * 0.015
        out: list[list] = []
        for h, a, c in layers:
            if out and h - out[-1][-1][0] <= gap and h - out[-1][0][0] <= 3 * gap:
                out[-1].append((h, a, c))
            else:
                out.append([(h, a, c)])
        merged = []
        for grp in out:
            area = sum(a for _, a, _ in grp)
            height = sum(h * a for h, a, _ in grp) / area
            cover = grp[0][2].copy()
            for _, _, c in grp[1:]:
                cover |= c
            merged.append((height, area, cover))
        return merged

    def overlap(self, a, b) -> float:
        return float((a & b).sum()) * self.cell ** 2

    def storeys(self, metric: bool):
        """Floors with a ceiling over them: [(floor, ceiling, overlap)], at most one per floor, sorted by floor, none
        overlapping another in height. With `metric`, only ceilings 2.1-4.0 above their floor count. A floor and a
        ceiling pair when they cover 30 % of the smaller of the two. Under one ceiling, of the floors covering at least
        60 % of what the best does, the floor is the lowest that lies mostly under it (not seats or tables, which are
        higher), else the highest (a ground plane far larger than the house is lower). Over one floor the ceiling is
        the lowest covering 80 % of what the best does (a table's underside covers far less). Not in metres, a ceiling
        is at least GENERIC_STOREY of the model's height above its floor (not a table, nor the underside of a rug)."""
        ups, downs = self.layers["up"], self.layers["down"]
        pairs = []
        for d in downs:
            cands = []
            for u in ups:
                gap = d[0] - u[0]
                if metric and not CEILING_M[0] <= gap <= CEILING_M[1]:
                    continue
                if not metric and gap < GENERIC_STOREY * (self.lo + self.span - u[0]):
                    continue
                o = self.overlap(u[2], d[2])
                if o > 0 and o >= 0.3 * min(u[2].sum(), d[2].sum()) * self.cell ** 2:
                    cands.append((u[0], float(u[2].sum()) * self.cell ** 2, o))
            if cands:
                best = max(o for _, _, o in cands)
                good = [c for c in cands if c[2] >= 0.6 * best]
                inside = [c for c in good if c[2] >= 0.5 * c[1]]
                f, _, o = min(inside, key=lambda c: c[0]) if inside else max(good, key=lambda c: c[0])
                pairs.append((f, d[0], o))
        per_floor = []
        for f in sorted({p[0] for p in pairs}):
            mine = [p for p in pairs if p[0] == f]
            best = max(p[2] for p in mine)
            per_floor.append(min((p for p in mine if p[2] >= 0.8 * best), key=lambda p: p[1]))
        kept = []
        for s in sorted(per_floor, key=lambda s: -s[2]):
            if all(s[1] <= k[0] or k[1] <= s[0] for k in kept):
                kept.append(s)
        return sorted(kept)

    def flipped(self) -> _Layers:
        """The same layers with up and down swapped (the model upside down)."""
        other = copy.copy(self)
        other.lo = -(self.lo + self.span)
        other.layers = {"up": sorted(((-h, a, c) for h, a, c in self.layers["down"]), key=lambda t: t[0]),
                        "down": sorted(((-h, a, c) for h, a, c in self.layers["up"]), key=lambda t: t[0])}
        return other

    def biggest_floor(self):
        ups = self.layers["up"]
        if not ups:
            return None
        most = max(a for _, a, _ in ups)
        return min(h for h, a, _ in ups if a >= 0.5 * most)


def _furniture(y, ny, areas, storey, np) -> float:
    """Up-facing area in the lower half of a storey (seats, beds, tables) less down-facing area in its upper half
    (shelves, lamps): large when the storey is the right way up."""
    if storey is None:
        return 0.0
    f, c, _ = storey
    h = c - f
    lower = (ny > HORIZONTAL) & (y > f + 0.15 * h) & (y < f + 0.5 * h)
    upper = (ny < -HORIZONTAL) & (y > f + 0.5 * h) & (y < c - 0.15 * h)
    return float(areas[lower].sum() - areas[upper].sum())


def _orientations(tris, normals, areas, np):
    """The directions worth reading the model with as up, best first: [(score, [up, ...])] per axis, the signs to
    try in order. An axis scores the area of its storeys' floors under their ceilings (2.1-4.0 apart) plus half
    its largest flat layer, a quarter more for the axis the model is thinnest along. Its sign: the one with the
    furniture tops in the lower half of the storey; with no furniture to tell (a bare shell), the one whose floors
    have more area than its ceilings first, then the other."""
    ext = tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0)
    thinnest = "xyz"[int(np.argmin(ext))]
    out = []
    for ax in "xyz":
        r = np.asarray(_TURN[ax], dtype=np.float64)
        y = (tris @ r.T)[:, :, 1].mean(axis=1)
        ny = (normals @ r.T)[:, 1]
        plus = _Layers(tris @ r.T, normals @ r.T, areas, np)
        minus = plus.flipped()
        sp, sm = plus.storeys(metric=True), minus.storeys(metric=True)
        pair = max(sum(o for _, _, o in sp), sum(o for _, _, o in sm))
        flat = max((a for _, a, _ in plus.layers["up"] + plus.layers["down"]), default=0.0)
        score = (pair + 0.5 * flat) * (1.25 if ax == thinnest else 1.0)
        fp = _furniture(y, ny, areas, sp[0] if sp else None, np)
        fm = _furniture(-y, -ny, areas, sm[0] if sm else None, np)
        if abs(fp - fm) > 0.02 * max(flat, 1e-12):
            signs = [ax] if fp > fm else ["-" + ax]
        elif bool(sp) != bool(sm):
            signs = [ax] if sp else ["-" + ax]
        else:
            up = sum(a for _, a, _ in plus.layers["up"])
            down = sum(a for _, a, _ in plus.layers["down"])
            signs = [ax, "-" + ax] if up >= down else ["-" + ax, ax]
        out.append((score, signs))
    out.sort(key=lambda o: (-o[0], o[1][0][-1] != "y"))
    return out


def _wall_band(tris, normals, areas, floor, np) -> float:
    """The height above `floor` under which 95 % of the vertical faces' area lies (their tops)."""
    sel = (np.abs(normals[:, 1]) < VERTICAL) & (tris[:, :, 1].max(axis=1) > floor)
    if not sel.any():
        return 0.0
    tops = tris[sel][:, :, 1].max(axis=1) - floor
    w = areas[sel]
    order = np.argsort(tops)
    cum = np.cumsum(w[order])
    return float(tops[order][np.searchsorted(cum, 0.95 * cum[-1])])


# --- the plan grid -----------------------------------------------------------------------------------------------

def _dominant_angle(seg, np) -> float:
    """The plan's dominant wall direction in radians, within [-pi/4, pi/4): a length-weighted histogram of segment
    angles modulo 90 degrees, refined by the mean around its peak."""
    if len(seg) == 0:
        return 0.0
    d = seg[:, 1] - seg[:, 0]
    length = np.linalg.norm(d, axis=1)
    ang = np.mod(np.arctan2(d[:, 1], d[:, 0]), math.pi / 2)
    nb = 180
    idx = np.minimum((ang / (math.pi / 2) * nb).astype(np.int64), nb - 1)
    hist = np.bincount(idx, weights=length, minlength=nb)
    smooth = hist + np.roll(hist, 1) + np.roll(hist, -1)
    peak = (int(np.argmax(smooth)) + 0.5) / nb * (math.pi / 2)
    diff = np.mod(ang - peak + math.pi / 4, math.pi / 2) - math.pi / 4
    near = np.abs(diff) < math.radians(2)
    if length[near].sum() > 0:
        peak += float(np.average(diff[near], weights=length[near]))
    peak = math.fmod(peak, math.pi / 2)
    if peak >= math.pi / 4:
        peak -= math.pi / 2
    return peak


def _yaw(angle, np):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


class _Grids:
    """The upright model in metres, its walls along the grid's axes, cut into occupancy grids."""

    def __init__(self, tris, normals, areas, ceiling, np, ndi):
        self.np, self.ndi = np, ndi
        self.ceiling = ceiling
        self.lintel = LINTEL_SLICE_M if ceiling is None else min(LINTEL_SLICE_M, ceiling - 0.1)
        heights = {"floor": FLOOR_SLICE_M, "sill": SILL_SLICE_M, "wall": WALL_SLICE_M, "head": HEAD_SLICE_M,
                   "lintel": self.lintel}
        if ceiling is not None and ceiling - TOP_BELOW_M > self.lintel + 0.1:
            heights["top"] = ceiling - TOP_BELOW_M  # the wall over an opening taller than the lintel cut
        tall = tris[(tris[:, :, 1].max(axis=1) > 0.0) & (tris[:, :, 1].min(axis=1) < max(heights.values()) + 0.01)]
        segs = {k: _segments(tall, h, np) for k, h in heights.items()}
        floor_sel = (normals[:, 1] > HORIZONTAL) & (np.abs(tris[:, :, 1].mean(axis=1)) < 0.1)
        pts = np.concatenate([s.reshape(-1, 2) for s in segs.values()] + [np.zeros((0, 2))])
        if len(pts) == 0:
            raise ScanError("no walls: nothing of the model stands 1 m above its floor")
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        if ((hi - lo) / CELL_M).max() > MAX_CELLS:
            floor_pts = tris[floor_sel][:, :, [0, 2]].reshape(-1, 2)
            if len(floor_pts):
                lo = np.maximum(lo, floor_pts.min(axis=0) - 2.0)
                hi = np.minimum(hi, floor_pts.max(axis=0) + 2.0)
            if ((hi - lo) / CELL_M).max() > MAX_CELLS:
                raise ScanError(f"the model is {float((hi - lo).max()):.0f} m across: too large for a home "
                                "(give --scale if its units are not metres)")
        pad = 1.0
        self.origin = lo - pad
        self.shape = tuple(int(v) for v in np.ceil((hi - lo + 2 * pad) / CELL_M) + 1)
        disk = _disk(int(round(SMALL_GAP_M / 2 / CELL_M)), np)
        self.raw = {k: _raster_segments(s, self.origin, CELL_M, self.shape, np) for k, s in segs.items()}
        self.occ = {k: ndi.binary_closing(g, structure=disk) | g for k, g in self.raw.items()}
        # the walls openings are looked for in: the cuts at 1.0 and at 1.6 m together (a scan misses a wall at one
        # height or the other behind furniture; doors and windows are open at both)
        both = self.raw["wall"] | self.raw["head"]
        self.walls = ndi.binary_closing(both, structure=disk) | both
        # and the walls rooms are bounded by: those and the cut at 2.15 m, where furniture rarely reaches and the wall
        # over a door or a window closes it (a scan's wall missed lower down is often there); and, near those walls,
        # the cut just under the ceiling (over a taller opening; away from the walls it holds beams and lamps)
        upper = both | self.raw["lintel"]
        if "top" in self.raw:
            near = ndi.binary_dilation(self.walls, structure=_disk(int(round(NEAR_WALL_M / CELL_M)), np))
            upper = upper | (self.raw["top"] & near)
        self.bounds = ndi.binary_closing(upper, structure=disk) | upper
        # a slice that does not reach a height (walls cut lower, or the ceiling under the lintel) says nothing there
        self.has = {k: bool(g.any()) for k, g in self.raw.items()}
        self.floor = ndi.binary_dilation(
            _raster(tris[floor_sel][:, :, [0, 2]], self.origin, CELL_M, self.shape, np), iterations=1)
        top_hi = (ceiling - 0.2) if ceiling is not None else 2.3
        tops = (normals[:, 1] > HORIZONTAL) & (tris[:, :, 1].mean(axis=1) > WALL_SLICE_M + 0.05) & (
            tris[:, :, 1].mean(axis=1) < top_hi)
        self.tops = _raster(tris[tops][:, :, [0, 2]], self.origin, CELL_M, self.shape, np)

    def point(self, i, j):
        """A position given in grid corner coordinates (cell (i, j) spans [i, i+1] x [j, j+1]) in metres."""
        return (float(self.origin[0] + i * CELL_M), float(self.origin[1] + j * CELL_M))


def _disk(r, np):
    if r <= 0:
        return np.ones((1, 1), dtype=bool)
    a = np.arange(-r, r + 1)
    return (a[:, None] ** 2 + a[None, :] ** 2) <= r * r


# --- openings ----------------------------------------------------------------------------------------------------

def _runs(line, np):
    """The free runs [start, end) of a boolean row that have a wall cell on both sides."""
    if len(line) < 3:
        return []
    padded = np.concatenate([[True], line, [True]]).astype(np.int8)
    d = np.diff(padded)
    starts = np.nonzero(d == -1)[0]
    ends = np.nonzero(d == 1)[0]
    return [(int(s), int(e)) for s, e in zip(starts, ends, strict=True) if s > 0 and e < len(line)]


def _openings(g: _Grids, door_range=DOOR_M):
    """The doors and windows of the grids: [{kind, axis, cells: [(i, j)...], width (m), centre (i, j), across}],
    `axis` 0 for a wall along x (its runs along the grid's first index), 1 along z; `centre` in grid corner
    coordinates (`_Grids.point`); `across` the wall's thickness in cells.

    A run is a free stretch of one grid row (or column) between two wall cells, with the wall going on in line beyond
    both its ends (60 % of FLANK_M). A doorway: door_range wide, open at 0.1 m. A window: WINDOW_M wide, walled at
    0.5 m (its sill) and at the lintel height, open at 1.6 m. No lintel is asked of a doorway: a scan rarely sees the
    wall over a door, and a full-height opening is a way through too (RoomPlan's "opening"); `_sides` then keeps only
    the runs that are a way through their wall."""
    np = g.np
    occ = g.occ
    wall = g.walls
    flank = int(round(FLANK_M / CELL_M))
    found = []
    for axis in (0, 1):
        w = wall if axis == 0 else wall.T
        o = {k: (v if axis == 0 else v.T) for k, v in occ.items()}
        for row in range(w.shape[1]):
            line = w[:, row]
            if line.all() or not line.any():
                continue
            for s, e in _runs(line, np):
                length = (e - s) * CELL_M
                cells = slice(s, e)
                def frac(k, cells=cells, row=row, o=o):
                    return float(o[k][cells, row].mean())
                kind = None
                # an opening is a gap in a wall's line: the wall goes on beyond both of its ends (a run beside a
                # thin wall, between two corners, is not)
                before, after = w[max(0, s - flank):s, row], w[e:e + flank, row]
                in_line = min(before.mean(), after.mean())
                # or an opening next to a corner: the wall goes on beyond one end, the other is the corner's wall
                corner = max(before.mean(), after.mean()) >= 0.6 and before[-3:].all() and after[:3].all()
                if in_line < 0.6 and not corner:
                    continue
                if door_range[0] <= length <= door_range[1] and frac("floor") <= 1 - COVER:
                    kind = "door"
                if kind is None and WINDOW_M[0] <= length <= WINDOW_M[1] and frac("sill") >= COVER and (
                        frac("head") <= 1 - COVER) and (not g.has["lintel"] or frac("lintel") >= COVER):
                    kind = "window"
                if kind:
                    found.append((axis, row, s, e, kind, float(in_line)))
    # one opening per group of runs in neighbouring rows that overlap; its kind is that of the run most in line with
    # its wall (beside a thin wall, a row off its line can look like a door where the line has a window)
    found.sort()
    groups: list[list] = []
    for run in found:
        axis, row, s, e = run[:4]
        for grp in groups:
            a2, r2, s2, e2 = grp[-1][:4]
            if a2 == axis and r2 == row - 1 and s < e2 and s2 < e:
                grp.append(run)
                break
        else:
            groups.append([run])
    rank = {"window": 1, "door": 0}
    out = []
    for grp in groups:
        axis = grp[0][0]
        kind = max(grp, key=lambda r: (r[5], rank[r[4]]))[4]
        rows = [r[1] for r in grp]
        widths = sorted((r[3] - r[2]) for r in grp if r[4] == kind)
        mid = sorted((r[2] + r[3]) / 2 for r in grp if r[4] == kind)[len(widths) // 2]
        cells = []
        for r in grp:
            cells += [((k, r[1]) if axis == 0 else (r[1], k)) for k in range(r[2], r[3])]
        row_mid = (min(rows) + max(rows) + 1) / 2
        centre = (mid, row_mid) if axis == 0 else (row_mid, mid)
        out.append({"kind": kind, "axis": axis, "cells": cells, "width": widths[len(widths) // 2] * CELL_M,
                    "centre": centre, "across": len(rows)})
    return out


# --- rooms -------------------------------------------------------------------------------------------------------

def _regions(g: _Grids, openings):
    """The free regions of the wall slice with the doors and windows closed: (labels, rooms, outside label set,
    barrier). rooms: [label] kept as rooms."""
    np, ndi = g.np, g.ndi
    barrier = g.bounds.copy()
    for o in openings:
        if o["kind"] in ("door", "window"):
            idx = np.array(o["cells"])
            barrier[idx[:, 0], idx[:, 1]] = True
    four = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    labels, n = ndi.label(~barrier, structure=four)
    edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])).tolist()) - {0}
    rooms = []
    if n:
        idx = np.arange(1, n + 1)
        counts = ndi.sum(np.ones_like(labels), labels, idx)
        floored = ndi.sum(g.floor, labels, idx)
        topped = ndi.sum(g.tops, labels, idx)
        dist = ndi.distance_transform_edt(~barrier) * CELL_M
        widest = ndi.maximum(dist, labels, idx)
        for k, c, f, t, wd in zip(idx, counts, floored, topped, widest, strict=True):
            if int(k) in edge or c * CELL_M ** 2 < ROOM_LEAST_M2 or wd < ROOM_HALF_WIDTH_M:
                continue
            if f / c < FLOORED or t / c >= SOLID:
                continue
            rooms.append(int(k))
    return labels, rooms, edge, barrier


def _grow(g: _Grids, labels, rooms, edge, barrier):
    """Each room's cells grown to the middle of its walls (a wall cell goes to the nearest free cell's room, the
    outside taking its half of an outside wall), its holes filled, smoothed: {label: mask}."""
    np, ndi = g.np, g.ndi
    keep = np.isin(labels, rooms) | np.isin(labels, list(edge))
    dist, (ii, jj) = ndi.distance_transform_edt(~keep, return_indices=True)
    nearest = labels[ii, jj]
    grown = np.where(keep, labels, np.where(dist * CELL_M <= GROW_M, nearest, 0))
    masks = {}
    taken = np.zeros(labels.shape, dtype=bool)
    for k in rooms:
        taken |= grown == k
    smooth = np.ones((3, 3), dtype=bool)
    for k in rooms:
        m = grown == k
        m = ndi.binary_fill_holes(m) & ~(taken & ~m)
        m = ndi.binary_closing(ndi.binary_opening(m, structure=smooth), structure=smooth) & ~(taken & ~m) | (
            labels == k)
        lab, n = ndi.label(m)
        if n > 1:
            sizes = ndi.sum(m, lab, range(1, n + 1))
            m = lab == (int(np.argmax(sizes)) + 1)
        masks[k] = m
    return masks


def _outline(mask, np) -> list[tuple[int, int]]:
    """The outer boundary of a 4-connected cell mask as a loop of grid corners (i, j), counter-clockwise in (i, j):
    cell (i, j) is the square [i, i+1] x [j, j+1]. Where two cells touch only at a corner the loop turns left, so
    they stay apart (4-connectivity); the longest loop is the outer one."""
    m = np.pad(mask, 1)
    out: dict[tuple[int, int], list[tuple[int, int]]] = {}
    ii, jj = np.nonzero(m[1:-1, 1:-1])
    for i, j in zip(ii.tolist(), jj.tolist(), strict=True):
        if not m[i + 1, j]:  # nothing below: along +i at j
            out.setdefault((i, j), []).append((i + 1, j))
        if not m[i + 2, j + 1]:  # nothing to the right: along +j at i+1
            out.setdefault((i + 1, j), []).append((i + 1, j + 1))
        if not m[i + 1, j + 2]:  # nothing above: along -i at j+1
            out.setdefault((i + 1, j + 1), []).append((i, j + 1))
        if not m[i, j + 1]:  # nothing to the left: along -j at i
            out.setdefault((i, j + 1), []).append((i, j))
    used: set[tuple] = set()
    loops = []
    for start in sorted(out):
        for first in out[start]:
            if (start, first) in used:
                continue
            loop = [start]
            prev, cur = start, first
            used.add((start, first))
            while cur != start:
                loop.append(cur)
                nxt = [n for n in out.get(cur, []) if (cur, n) not in used]
                if not nxt:
                    break
                if len(nxt) > 1:  # a pinch: turn left
                    dx, dz = cur[0] - prev[0], cur[1] - prev[1]
                    nxt.sort(key=lambda n, dx=dx, dz=dz: -(dx * (n[1] - cur[1]) - dz * (n[0] - cur[0])))
                used.add((cur, nxt[0]))
                prev, cur = cur, nxt[0]
            if cur == start and len(loop) >= 4:
                loops.append(loop)
    if not loops:
        return []
    return max(loops, key=lambda lp: abs(_shoelace(lp)))


def _shoelace(ring) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1], strict=True)) / 2


def _corners(ring):
    """The ring with the points where it does not turn removed."""
    out = []
    n = len(ring)
    for k in range(n):
        a, b, c = ring[k - 1], ring[k], ring[(k + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) != 0:
            out.append(b)
    return out


def _simplify(ring, least):
    """A rectilinear ring with no edge shorter than `least`: the shortest edge is removed by moving the shorter of
    its two neighbours (parallel to each other) onto the longer's line, until none is short."""
    ring = _corners([tuple(map(float, p)) for p in ring])
    for _ in range(10_000):
        n = len(ring)
        if n <= 4:
            break
        lens = [math.dist(ring[k], ring[(k + 1) % n]) for k in range(n)]
        k = min(range(n), key=lambda i: lens[i])
        if lens[k] >= least:
            break
        # edge k runs ring[k] -> ring[k+1]; its neighbours: edge k-1 (ring[k-1] -> ring[k]) and k+1
        a0, a1 = ring[k - 1], ring[k]
        b0, b1 = ring[(k + 1) % n], ring[(k + 2) % n]
        move_prev = lens[k - 1] <= lens[(k + 1) % n]
        horizontal = abs(a1[1] - a0[1]) < 1e-9  # edge k-1 along x (so edge k is along y)
        if move_prev:  # move edge k-1 onto edge k+1's line
            if horizontal:
                ring[k - 1] = (a0[0], b0[1])
                ring[k] = (a1[0], b0[1])
            else:
                ring[k - 1] = (b0[0], a0[1])
                ring[k] = (b0[0], a1[1])
        else:
            if horizontal:
                ring[(k + 1) % n] = (b0[0], a1[1])
                ring[(k + 2) % n] = (b1[0], a1[1])
            else:
                ring[(k + 1) % n] = (a1[0], b0[1])
                ring[(k + 2) % n] = (a1[0], b1[1])
        ring = _corners(_dedupe(ring))
        if len(ring) < 4:
            return []
    return ring


def _dedupe(ring):
    out = []
    for p in ring:
        if not out or math.dist(out[-1], p) > 1e-9:
            out.append(p)
    while len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-9:
        out.pop()
    return out


# --- the whole -----------------------------------------------------------------------------------------------------

GENERIC_STOREY = 0.4  # with no metric storey, a ceiling is at least this share of the model's height above its floor
AMBIGUOUS = 0.67  # a second axis scoring this share of the best one is read too, and the plan decides


def _read(tris, scale, np):
    """Read the model with the most likely up direction; when the shape alone cannot tell (a bare shell: a second
    axis scores close to the best, or no furniture shows which way is up), read it each way and keep the plan with
    the most doors and windows, then rooms (said in a warning)."""
    import scipy.ndimage as ndi
    normals, areas = _normals(tris, np)
    keep = areas > 0
    tris, normals, areas = tris[keep], normals[keep], areas[keep]
    if len(tris) == 0:
        raise ScanError("the model's triangles all have no area")
    ranked = _orientations(tris, normals, areas, np)
    tries = list(ranked[0][1])
    for score, signs in ranked[1:]:
        if score >= AMBIGUOUS * ranked[0][0] and score > 0:
            tries += signs
    if len(tries) == 1:
        return _read_up(tris, normals, areas, tries[0], scale, np, ndi)
    best, first_error = None, None
    for up in tries:
        try:
            got = _read_up(tris, normals, areas, up, scale, np, ndi)
        except ScanError as e:
            first_error = first_error or e
            continue
        p = got[0]
        quality = (len(p["doors"]) + len(p["windows"]), p["entrance"] is not None, len(p["rooms"]))
        if best is None or quality > best[0]:
            best = (quality, got)
    if best is None:
        raise first_error
    plan, meta, warnings = best[1]
    warnings.insert(0, f"the shape alone does not show which way is up: read with {meta['up']} up, the way that "
                       f"finds the most doors and windows (of {', '.join(tries)})")
    return plan, meta, warnings


def _read_up(tris, normals, areas, up, scale, np, ndi):
    warnings: list[str] = []
    turn = np.asarray(_TURN[up], dtype=np.float64)
    tris = tris @ turn.T
    normals = normals @ turn.T

    layers = _Layers(tris, normals, areas, np)
    metric = layers.storeys(metric=True)
    floor = ceiling = None
    if metric:
        floor, ceiling = _lowest(metric, warnings)
    else:
        generic = layers.storeys(metric=False)
        if generic:
            floor, ceiling = _lowest(generic, warnings)
    band = None
    if floor is None:
        floor = layers.biggest_floor()
        if floor is None:
            raise ScanError("no floor: the model has no horizontal surface facing up")
        band = _wall_band(tris, normals, areas, floor, np)
    measured = None
    if scale is not None:
        s = float(scale)
        if ceiling is not None and not CEILING_M[0] <= (ceiling - floor) * s <= CEILING_M[1]:
            warnings.append(f"with the given scale the ceiling is {(ceiling - floor) * s:.2f} m above the floor")
    elif ceiling is not None:
        height = ceiling - floor
        if CEILING_M[0] <= height <= CEILING_M[1]:
            s = 1.0
        else:
            s = CEILING_DEFAULT_M / height
            warnings.append(f"the scale: the ceiling is {height:.4g} units above the floor, so the model was rescaled "
                            f"by {s:.4g} to put it at {CEILING_DEFAULT_M} m; give --scale if that is wrong")
    elif band is not None and CEILING_M[0] <= band <= CEILING_M[1]:
        s = 1.0
        warnings.append(f"no ceiling found: the walls' height, {band:.2f} m, is taken as the storey's")
    else:
        s, measured = _scale_from_doors(tris, normals, areas, floor, band, np, ndi)
        if s is None:
            raise ScanError(f"the scale looks wrong: the model has no ceiling, its walls are {band:.3g} units tall "
                            "and it has no door to measure; give --scale (metres per unit)")
        warnings.append(f"the scale: no ceiling and walls {band:.3g} units tall, so the model was rescaled by {s:.4g}"
                        f" to make its doors {DOOR_TYPICAL_M} m wide (they measure {measured:.3g} units); give "
                        "--scale if that is wrong")

    floor_m = floor * s
    ceiling_m = None if ceiling is None else ceiling * s
    up_scaled = tris * s
    up_scaled[:, :, 1] -= floor_m
    ceiling_rel = None if ceiling_m is None else ceiling_m - floor_m
    angle = _dominant_angle(_segments(up_scaled, WALL_SLICE_M, np), np)
    yaw = _yaw(angle, np)
    up_scaled = up_scaled @ yaw.T
    norm2 = normals @ yaw.T

    g = _Grids(up_scaled, norm2, areas * s * s, ceiling_rel, np, ndi)
    openings = _openings(g)
    labels, rooms, edge, barrier = _regions(g, openings)
    if not rooms:
        raise ScanError("no room: the walls 1 m above the floor enclose no space of 1.5 m² or more")
    masks = _grow(g, labels, rooms, edge, barrier)

    polys = {}
    for k, m in masks.items():
        loop = _outline(m, np)
        ring = _simplify([(g.origin[0] + i * CELL_M, g.origin[1] + j * CELL_M) for i, j in loop], SIMPLIFY_M)
        if len(ring) >= 4 and abs(_shoelace(ring)) >= ROOM_LEAST_M2 * 0.5:
            polys[k] = ring
    if not polys:
        raise ScanError("no room: the regions found have no usable outline")
    order = sorted(polys, key=lambda k: (round(_centre(polys[k])[1], 1), _centre(polys[k])[0]))
    ids = {k: f"r{n + 1}" for n, k in enumerate(order)}

    # move the plan so its rooms start at (0, 0)
    allpts = np.array([p for k in order for p in polys[k]])
    shift = allpts.min(axis=0)

    def plan_pt(p):
        return [round(float(p[0] - shift[0]), 3), round(float(p[1] - shift[1]), 3)]

    rooms_out = []
    for n, k in enumerate(order):
        ring = polys[k]
        if _shoelace(ring) < 0:
            ring = ring[::-1]
        rooms_out.append({"id": ids[k], "name": f"room {n + 1}", "type": "other",
                          "polygon": [plan_pt(p) for p in ring]})

    doors, windows, door_widths = [], [], []
    to_dropped = []
    skipped_windows = 0
    for o in openings:
        if o["kind"] not in ("door", "window"):
            continue
        sides = _sides(g, o, labels, ids, edge)
        if sides is None:
            continue
        a, b = sides
        at = plan_pt(g.point(*o["centre"]))
        width = round(float(o["width"]), 3)
        if o["kind"] == "door":
            if a == b:
                continue
            door_widths.append(width)
            if "dropped" in (a, b):
                if a == b == "dropped" or "outside" in (a, b):
                    continue
                to_dropped.append({"at": at, "width": width, "rooms": [a if a != "dropped" else b, "outside"]})
                continue
            if a == "outside" and b == "outside":
                continue
            pair = sorted([a, b], key=lambda r: (r == "outside", r))
            doors.append({"at": at, "width": width, "rooms": pair})
        else:
            inside = [x for x in (a, b) if x not in ("outside", "dropped")]
            if len(inside) == 1 and a != b:
                windows.append({"at": at, "width": width, "room": inside[0]})
            elif len(inside) == 2 and a != b:
                skipped_windows += 1
    if skipped_windows:
        warnings.append(f"{skipped_windows} window(s) between two rooms left out (a window is on an outside wall)")
    outer = [k for k, d in enumerate(doors) if "outside" in d["rooms"]]
    entrance = None
    if outer:
        entrance = max(outer, key=lambda k: (doors[k]["width"], -k))
    elif to_dropped:
        best = max(to_dropped, key=lambda d: d["width"])
        doors.append(best)
        entrance = len(doors) - 1
        warnings.append(f"no door to the outside found: the widest door out of the rooms found ({best['width']:.2f} m"
                        f", into a space too small or open to be a room) is taken as the entrance")
    else:
        warnings.append("no entrance found: no door leads from a room to the outside; choose one")
    if scale is None and measured is None and s == 1.0 and door_widths:
        med = sorted(door_widths)[len(door_widths) // 2]
        if not DOOR_PLAUSIBLE_M[0] <= med <= DOOR_PLAUSIBLE_M[1]:
            warnings.append(f"the doors are on the whole {med:.2f} m wide (usually {DOOR_PLAUSIBLE_M[0]}-"
                            f"{DOOR_PLAUSIBLE_M[1]} m): check the scale")
    reached = {r for d in doors for r in d["rooms"]}
    for r in rooms_out:
        if r["id"] not in reached:
            warnings.append(f"{r['name']}: no door found into it")

    plan = {"format": FORMAT, "units": "m", "scale": None, "rooms": rooms_out, "doors": doors, "windows": windows,
            "entrance": entrance}
    load_plan(plan)

    m = np.eye(4)
    m[:3, :3] = yaw @ (np.eye(3) * s) @ turn
    m[:3, 3] = [-shift[0], -floor_m, -shift[1]]
    meta = {"up": up, "scale": s, "floor_y": round(floor_m, 4),
            "ceiling_y": None if ceiling_m is None else round(ceiling_m, 4),
            "transform": [[float(v) for v in row] for row in m]}
    return plan, meta, warnings


def _centre(ring):
    xs = [p[0] for p in ring]
    zs = [p[1] for p in ring]
    return ((min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2)


def _lowest(storeys, warnings):
    best = max(o for _, _, o in storeys)
    kept = [s for s in storeys if s[2] >= 0.3 * best]
    f, c, _ = kept[0]
    if len(kept) > 1:
        warnings.append(f"{len(kept)} storeys found (floors at {', '.join(f'{s[0]:.3g}' for s in kept)} units): the "
                        "lowest is read, the others are left out")
    return f, c


def _sides(g: _Grids, o, labels, ids, edge):
    """What lies on the two sides of an opening, walking out across its wall up to 1 m: a room id, "outside", or
    "dropped" (a free region that is not a room and does not reach the edge). An opening is a way through its wall:
    the walk crosses only the opening itself; a run along a jagged wall's face, or in front of a door leaf or a pane,
    is not one (None). The walk starts at the opening's middle, else a quarter or 40 % of its width to either side
    (a scan's door frame or an open leaf can stand in the way)."""
    ci, cj = o["centre"]
    width = o["width"] / CELL_M
    for shift in (0.0, -0.25 * width, 0.25 * width, -0.4 * width, 0.4 * width):
        i, j = (int(ci + shift), int(cj)) if o["axis"] == 0 else (int(ci), int(cj + shift))
        got = _walk(g, o, labels, ids, edge, i, j)
        if got is not None:
            return got
    return None


def _walk(g: _Grids, o, labels, ids, edge, i, j):
    own = set(o["cells"])
    reach = int(round(1.0 / CELL_M))
    out = []
    for sign in (-1, 1):
        found = None
        for step in range(0, reach + 1):
            ii, jj = (i, j + sign * step) if o["axis"] == 0 else (i + sign * step, j)
            if not (0 <= ii < labels.shape[0] and 0 <= jj < labels.shape[1]):
                found = "outside"
                break
            if (ii, jj) in own:
                continue
            if g.walls[ii, jj]:
                return None
            lab = int(labels[ii, jj])
            if lab == 0:  # under the wall above the opening
                continue
            found = ids.get(lab) or ("outside" if lab in edge else "dropped")
            break
        if found is None:
            return None
        out.append(found)
    return out[0], out[1]


def _scale_from_doors(tris, normals, areas, floor, band, np, ndi):
    """With no ceiling and walls of no plausible height: the scale making the model's doorways DOOR_TYPICAL_M wide,
    measured on slices at the heights they would have if the walls were 2.5 m tall; (None, None) with no doorway."""
    if not band or band <= 0:
        return None, None
    s0 = CEILING_DEFAULT_M / band
    t = tris * s0
    t[:, :, 1] -= floor * s0
    angle = _dominant_angle(_segments(t, WALL_SLICE_M, np), np)
    t = t @ _yaw(angle, np).T
    try:
        g = _Grids(t, normals @ _yaw(angle, np).T, areas * s0 * s0, None, np, ndi)
    except ScanError:
        return None, None
    doors = [o for o in _openings(g, door_range=(0.3, 2.4)) if o["kind"] == "door"]
    if not doors:
        return None, None
    widths = sorted(o["width"] for o in doors)
    med = widths[len(widths) // 2] / s0
    return DOOR_TYPICAL_M / med, med
