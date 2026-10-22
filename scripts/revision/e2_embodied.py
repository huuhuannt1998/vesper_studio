#!/usr/bin/env python3
"""
E2 "LLM-embodied" workload generator (results/revision/e2/PREREGISTRATION.md).

The 90 LLM persona schedules (results/revision/llm_schedules/*.json) are executed
by a navigating agent on HSSD floor plans (Habitat-sim navmesh over the stage
geometry only, headless, no renderer, no sensors).

Task interpretation is reused verbatim from the schedule-level converter
(scripts/revision/e1v2_workloads.py: _map_room, DEPART_WORDS, RETURN_WORDS,
TRIP_WORDS, OUT_OF_HOME, the WORK / works_from_home / has_commute rule and the
day clipping of _door).  The only difference to LLM-sched is embodiment:
  * every in-home task makes the agent walk (navmesh shortest path, 1.1 m/s)
    from where it is to a fixed seeded point of the task's target room;
  * every door event of the schedule-level converter becomes a physical walk
    out of / into the house through the front door;
  * sensors are derived from the walked trajectory (positions every 0.5 s):
      rooms  ON at region entry, OFF 5 s after exit (re-entry within 5 s merges);
      door   door_open when the agent crosses the front door, door_close 3 s later.
Trace format is identical to e1v2_workloads: sorted [(second, kind, room)].

Added sensitivity variant (not preregistered; see results/revision/e2/DEVIATIONS.md):
traces_pir/ uses the schedule-level PIR model on the same walks (activation at
region entry and every MOTION_INTERVAL s while inside, MOTION_ON s on-time,
no re-triggers while dwelling in a NO_MOTION task).

Subcommands (run with the habitat conda python):
  scenes    eligibility, front door, room points, per-scene metadata
  run       execute 90 schedules x K=3 scenes -> traces/, traces_pir/, manifest.csv
  sanity    SANITY.md + figs/
  humanoid  articulated-humanoid timing validation (2 scenes, 10 legs each)
  all       scenes, run, humanoid, sanity
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e1v2_workloads as W  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HSSD = ROOT / "data/versioned_data/hssd-hab"
HUM = ROOT / "data/versioned_data/habitat_humanoids"
SCHED_DIR = ROOT / "results/revision/llm_schedules"
OUT = ROOT / "results/revision/e2/embodied"
NAVCACHE = OUT / "navmesh"
SCENE_DIR = OUT / "scenes"
CANDIDATES = ROOT / "results/revision/novelty/hssd_regions.json"

SEED = 20261013
K = 3
SPEED = 1.1          # m/s
DT = 0.5             # trajectory sampling while walking (s)
ROOM_OFF_LAG = 5     # room sensor OFF this long after exit (s)
DOOR_CLOSE_LAG = 3   # door_close this long after the crossing (s)
DAY = W.DAY
AGENT_RADIUS, AGENT_HEIGHT = 0.2, 1.6

OUTDOOR_LABELS = {"outdoor", "porch/terrace/deck/driveway"}
CANON_TABLE = {   # preregistered fixed table
    "entryway/foyer/lobby": "entryway",
    "hallway": "hallway",
    "kitchen": "kitchen",
    "living room": "living_room", "dining room": "living_room", "lounge": "living_room",
    "familyroom": "living_room", "tv": "living_room",
    "bedroom": "bedroom",
}
# Documented fallback when a task's canonical target room is absent from the scene
# (or not reachable from the front door).  "__door__" = the point just inside the
# front door (the house's entry area).
FALLBACK = {
    "entryway": ["__door__"],
    "hallway": ["bathroom", "toilet", "__door__"],
    "kitchen": ["living_room", "__door__"],
    "living_room": ["office", "rec_game", "other_room", "kitchen", "__door__"],
    "bedroom": ["living_room", "__door__"],
}


def canon(label: str) -> str:
    return CANON_TABLE.get(label) or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _hab():
    import habitat_sim
    return habitat_sim


# ════════════════════════════════════════════════════════════════════════════
# Geometry helpers
# ════════════════════════════════════════════════════════════════════════════
class Regions:
    """HSSD region annotations (poly_loop x,z + floor height)."""

    def __init__(self, sid: str):
        R = json.loads((HSSD / f"semantics/scenes/{sid}.semantic_config.json").read_text())["region_annotations"]
        self.items = []
        for i, r in enumerate(R):
            pl = r["poly_loop"]
            pl = json.loads(pl) if isinstance(pl, str) else pl
            P = np.asarray(pl, float)[:, [0, 2]]
            self.items.append(dict(i=i, name=r.get("name", r["label"]), label=r["label"],
                                   canon=canon(r["label"]), outdoor=r["label"] in OUTDOOR_LABELS,
                                   P=P, fh=float(r["floor_height"]), path=MplPath(P)))
        self.outdoor_idx = [g["i"] for g in self.items if g["outdoor"]]
        self.indoor_idx = [g["i"] for g in self.items if not g["outdoor"]]

    def member(self, X: np.ndarray) -> np.ndarray:
        """(n_regions, n) boolean: point inside region polygon at its floor."""
        X = np.atleast_2d(np.asarray(X, float))
        M = np.zeros((len(self.items), len(X)), bool)
        for g in self.items:
            h = (X[:, 1] >= g["fh"] - 0.3) & (X[:, 1] <= g["fh"] + 1.0)
            if h.any():
                M[g["i"], h] = g["path"].contains_points(X[h][:, [0, 2]])
        return M

    def classify(self, X):
        """Per point: 'O' outdoor only, 'I' inside an indoor region, '-' neither;
        plus the set of indoor canonical labels containing it."""
        M = self.member(X)
        ins = M[self.indoor_idx].any(0) if self.indoor_idx else np.zeros(M.shape[1], bool)
        out = M[self.outdoor_idx].any(0) if self.outdoor_idx else np.zeros(M.shape[1], bool)
        cls = np.where(ins, "I", np.where(out, "O", "-"))
        labs = []
        for j in range(M.shape[1]):
            labs.append(frozenset(self.items[i]["canon"] for i in self.indoor_idx if M[i, j]))
        return cls, labs, M


def seg_dist(points: np.ndarray, P: np.ndarray) -> np.ndarray:
    """Distance (x,z) from points to polygon boundary."""
    A, B = P, np.roll(P, -1, axis=0)
    AB = B - A
    L2 = np.maximum((AB ** 2).sum(1), 1e-12)
    d = np.full(len(points), np.inf)
    for a, ab, l2 in zip(A, AB, L2):
        t = np.clip(((points - a) @ ab) / l2, 0, 1)
        q = a + t[:, None] * ab
        d = np.minimum(d, np.linalg.norm(points - q, axis=1))
    return d


def arc(pts: np.ndarray) -> np.ndarray:
    if len(pts) < 2:
        return np.zeros(len(pts))
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])


def interp(pts: np.ndarray, s_cum: np.ndarray, s: np.ndarray) -> np.ndarray:
    s = np.clip(np.asarray(s, float), 0, s_cum[-1])
    if len(pts) == 1:
        return np.repeat(pts, len(s), axis=0)
    idx = np.clip(np.searchsorted(s_cum, s, side="right") - 1, 0, len(pts) - 2)
    seg = np.maximum(s_cum[idx + 1] - s_cum[idx], 1e-12)
    f = ((s - s_cum[idx]) / seg)[:, None]
    return pts[idx] + f * (pts[idx + 1] - pts[idx])


def densify(pts: np.ndarray, step: float = 0.05):
    S = arc(pts)
    s = np.arange(0, S[-1] + 1e-9, step)
    if len(s) == 0 or s[-1] < S[-1]:
        s = np.append(s, S[-1])
    return interp(pts, S, s), s


def _make_sim(sid: str, physics: bool):
    hs = _hab()
    cfg = hs.SimulatorConfiguration()
    cfg.scene_id = str(HSSD / f"stages/{sid}.glb")
    cfg.enable_physics = physics
    cfg.create_renderer = False
    ag = hs.agent.AgentConfiguration()
    ag.sensor_specifications = []
    return hs.Simulator(hs.Configuration(cfg, [ag]))


def _navmesh_settings():
    hs = _hab()
    ns = hs.NavMeshSettings()
    ns.set_defaults()
    ns.agent_radius, ns.agent_height = AGENT_RADIUS, AGENT_HEIGHT
    ns.include_static_objects = True
    return ns


FENCE_PIECE, FENCE_GAP, FENCE_HALF_THICK = 0.25, 0.9, 0.03


def fence_boxes(regs: "Regions", D: np.ndarray):
    """Thin boxes along the outdoor-region polygon boundaries that close every
    exterior opening except the front door (gap of +-0.9 m around D).  A piece of
    boundary is fenced unless (a) its outdoor-side point lies inside an indoor
    polygon (the outdoor polygon overlaps the house there), (b) its far side lies
    in another outdoor polygon (outdoor areas stay connected), or (c) it is within
    0.9 m of the front door on the door's floor.  Returns [(center xyz, yaw, half_len)]."""
    boxes = []
    for oi in regs.outdoor_idx:
        g = regs.items[oi]
        P = g["P"]
        same_floor_in = [regs.items[i] for i in regs.indoor_idx if abs(regs.items[i]["fh"] - g["fh"]) < 1.0]
        other_out = [regs.items[i] for i in regs.outdoor_idx if i != oi and abs(regs.items[i]["fh"] - g["fh"]) < 1.0]
        for a, b in zip(P, np.roll(P, -1, axis=0)):
            L = float(np.linalg.norm(b - a))
            if L < 1e-6:
                continue
            d = (b - a) / L
            n = np.array([d[1], -d[0]])
            mid = (a + b) / 2
            if not g["path"].contains_point(mid + 0.15 * n):
                n = -n
            k = max(1, int(np.ceil(L / FENCE_PIECE)))
            keep = []
            for j in range(k):
                c = a + d * (j + 0.5) * L / k
                qi, qo = c + 0.15 * n, c - 0.15 * n
                ok = True
                if any(r["path"].contains_point(qi) for r in same_floor_in):
                    ok = False
                elif any(r["path"].contains_point(qo) for r in other_out) and \
                        not any(r["path"].contains_point(qo) for r in same_floor_in):
                    ok = False
                elif D is not None and abs(g["fh"] + 0.2 - D[1]) < 1.0 and np.linalg.norm(c - D[[0, 2]]) < FENCE_GAP:
                    ok = False
                keep.append(ok)
            j = 0
            while j < k:
                if not keep[j]:
                    j += 1
                    continue
                j2 = j
                while j2 + 1 < k and keep[j2 + 1]:
                    j2 += 1
                s0, s1 = j * L / k, (j2 + 1) * L / k
                c = a + d * (s0 + s1) / 2
                boxes.append(([float(c[0]), g["fh"] + 1.0, float(c[1])], float(np.arctan2(-d[1], d[0])), (s1 - s0) / 2))
                j = j2 + 1
    return boxes


def build_fenced_navmesh(sid: str, boxes, tag: str = "fenced") -> Path:
    import magnum as mn
    hs = _hab()
    f = NAVCACHE / f"{sid}.{tag}.navmesh"
    sim = _make_sim(sid, physics=True)
    otm = sim.get_object_template_manager()
    base = otm.get_template_handles("cubeSolid")[0]
    rom = sim.get_rigid_object_manager()
    for j, (c, yaw, hl) in enumerate(boxes):
        t = otm.get_template_by_handle(base)
        t.scale = mn.Vector3(hl, 1.0, FENCE_HALF_THICK)   # cubeSolid spans [-1, 1]^3
        otm.register_template(t, f"fence_{j}")
        o = rom.add_object_by_template_handle(f"fence_{j}")
        # transform first, then STATIC (a STATIC object's transform is frozen)
        o.translation = mn.Vector3(*c)
        o.rotation = mn.Quaternion.rotation(mn.Rad(yaw), mn.Vector3(0, 1, 0))
        o.motion_type = hs.physics.MotionType.STATIC
    ok = sim.recompute_navmesh(sim.pathfinder, _navmesh_settings())
    if not ok:
        sim.close()
        raise RuntimeError("fenced navmesh failed")
    NAVCACHE.mkdir(parents=True, exist_ok=True)
    sim.pathfinder.save_nav_mesh(str(f))
    sim.close()
    return f


class Nav:
    """Standalone navmesh pathfinder (radius 0.2, height 1.6).  fenced=False: the
    stage geometry as preregistered (eligibility, front door).  fenced=True: the
    same stage with every exterior opening except the front door closed (walking)."""

    def __init__(self, sid: str, fenced=False):
        hs = _hab()
        tag = "fenced" if fenced is True else (fenced or "")
        f = NAVCACHE / (f"{sid}.{tag}.navmesh" if tag else f"{sid}.navmesh")
        if not f.exists():
            if fenced:
                raise FileNotFoundError(f"{f}: run the 'scenes' step first")
            NAVCACHE.mkdir(parents=True, exist_ok=True)
            sim = _make_sim(sid, physics=False)
            ok = sim.recompute_navmesh(sim.pathfinder, _navmesh_settings())
            if not ok:
                sim.close()
                raise RuntimeError("navmesh failed")
            sim.pathfinder.save_nav_mesh(str(f))
            sim.close()
        self.pf = hs.PathFinder()
        self.pf.load_nav_mesh(str(f))
        self._hs = hs
        self.cache = {}

    def path(self, a, b):
        key = (tuple(np.round(a, 4)), tuple(np.round(b, 4)))
        if key in self.cache:
            return self.cache[key]
        sp = self._hs.ShortestPath()
        sp.requested_start = np.asarray(a, np.float32)
        sp.requested_end = np.asarray(b, np.float32)
        ok = self.pf.find_path(sp)
        res = None
        if ok and np.isfinite(sp.geodesic_distance) and len(sp.points) > 0:
            res = np.asarray(sp.points, float)
            if len(res) == 1:
                res = np.vstack([res, res])
        self.cache[key] = res
        return res

    def snap(self, p):
        q = np.asarray(self.pf.snap_point(np.asarray(p, np.float32)), float)
        return q if np.isfinite(q).all() else None


# ════════════════════════════════════════════════════════════════════════════
# Scene analysis
# ════════════════════════════════════════════════════════════════════════════
def region_grid(nav: Nav, g: dict, spacing: float):
    lo, hi = g["P"].min(0), g["P"].max(0)
    while True:
        xs = np.arange(lo[0] + spacing / 2, hi[0], spacing)
        zs = np.arange(lo[1] + spacing / 2, hi[1], spacing)
        if len(xs) * len(zs) <= 30000:
            break
        spacing *= 1.5
    if len(xs) == 0 or len(zs) == 0:
        return np.zeros((0, 3))
    G = np.array(np.meshgrid(xs, zs)).reshape(2, -1).T
    G = G[g["path"].contains_points(G)]
    out = []
    for x, z in G:
        q = nav.snap([x, g["fh"] + 0.2, z])
        if q is None or np.hypot(q[0] - x, q[2] - z) > 0.12 or not (g["fh"] - 0.3 <= q[1] <= g["fh"] + 1.0):
            continue
        out.append(q)
    return np.array(out) if out else np.zeros((0, 3))


def analyse_path(regs: Regions, pts: np.ndarray):
    """Where a path that starts outdoors leaves the outdoor polygons for the last
    time ('crossing'), the first indoor region entered after it and the
    remaining path length."""
    X, s = densify(pts, 0.05)
    cls, labs, M = regs.classify(X)
    o = np.where(cls == "O")[0]
    if len(o) == 0 or o[-1] == len(X) - 1:
        return None
    j = o[-1] + 1
    after_in = np.where(cls[j:] == "I")[0]
    if len(after_in) == 0:
        return None
    k = j + after_in[0]
    first_regions = [regs.items[i] for i in regs.indoor_idx if M[i, k]]
    return dict(D=X[j], s_cross=float(s[j]), s_first_in=float(s[k]), s_total=float(s[-1]),
                remaining=float(s[-1] - s[j]), first_canon=sorted({r["canon"] for r in first_regions}),
                first_label=sorted({r["label"] for r in first_regions}),
                X=X, s=s, cls=cls)


def analyse_scene(sid: str) -> dict:
    """Eligibility + front door + room points for one scene."""
    t0 = time.time()
    regs = Regions(sid)
    meta = dict(scene=sid, eligible=False, reason="")
    if not regs.outdoor_idx:
        meta["reason"] = "no outdoor/porch region"
        return meta
    nav = Nav(sid)
    pf = nav.pf
    grids = {}
    for g in regs.items:
        grids[g["i"]] = region_grid(nav, g, 0.5 if g["outdoor"] else 0.25)
    isl = {i: np.array([pf.get_island(np.asarray(p, np.float32)) for p in G], int) if len(G) else np.zeros(0, int)
           for i, G in grids.items()}
    ind_cnt, out_cnt = Counter(), Counter()
    for i in regs.indoor_idx:
        ind_cnt.update(isl[i].tolist())
    for i in regs.outdoor_idx:
        out_cnt.update(isl[i].tolist())
    both = [k for k in ind_cnt if k in out_cnt and k >= 0]
    meta["indoor_grid_points"] = int(sum(ind_cnt.values()))
    if not both:
        meta["reason"] = "outdoor region not on the same navmesh island as any indoor region"
        return meta
    main = max(both, key=lambda k: ind_cnt[k])
    meta["main_island"] = int(main)
    meta["indoor_frac_on_main_island"] = round(ind_cnt[main] / max(1, sum(ind_cnt.values())), 3)

    # ── room points: fixed seeded navigable point per canonical label ──────
    outdoor_pts_all = np.vstack([grids[i][isl[i] == main] for i in regs.outdoor_idx if len(grids[i])]) \
        if any(len(grids[i]) for i in regs.outdoor_idx) else np.zeros((0, 3))
    cand = {}
    for g in regs.items:
        if g["outdoor"]:
            continue
        G = grids[g["i"]]
        if not len(G):
            continue
        G = G[isl[g["i"]] == main]
        if not len(G):
            continue
        ok = None
        bd = seg_dist(G[:, [0, 2]], g["P"])
        ob = np.array([pf.distance_to_closest_obstacle(np.asarray(p, np.float32), 1.0) for p in G])
        notout = ~regs.member(G)[regs.outdoor_idx].any(0) if regs.outdoor_idx else np.ones(len(G), bool)
        for (mb, mo) in [(0.3, 0.25), (0.15, 0.1), (0.0, 0.0)]:
            m = (bd >= mb) & (ob >= mo) & notout
            if m.any():
                ok = G[m]
                break
        if ok is not None:
            cand[g["i"]] = ok
    room_pts, room_region = {}, {}
    for c in sorted({regs.items[i]["canon"] for i in cand}):
        rs = sorted(i for i in cand if regs.items[i]["canon"] == c)
        rng = random.Random(f"{SEED}:{sid}:{c}")
        i = rng.choice(rs)
        room_pts[c] = cand[i][rng.randrange(len(cand[i]))]
        room_region[c] = regs.items[i]["name"]
    meta["rooms_present"] = sorted(room_pts)
    meta["canonical_rooms_present"] = [r for r in W.ROOMS if r in room_pts]
    if not len(outdoor_pts_all) or not room_pts:
        meta["reason"] = "no navigable outdoor or indoor points on the main island"
        return meta

    # ── house interior connectivity: close every exterior opening ─────────────
    # (stage navmesh + thin fences along all outdoor-polygon boundaries).  The
    # islands of this "closed" navmesh are the internally connected parts of the
    # house; the front door must lead into the main part (the part with the most
    # canonical room types, ties broken by the number of indoor points).
    build_fenced_navmesh(sid, fence_boxes(regs, None), tag="closed")
    navc = Nav(sid, fenced="closed")
    pfc = navc.pf

    def closed_island(p):
        q = navc.snap(p)
        if q is None or np.linalg.norm((q - p)[[0, 2]]) > 0.3:
            return -1
        return int(pfc.get_island(np.asarray(q, np.float32)))

    ind_all = np.vstack([cand[i] for i in sorted(cand)])
    rng = random.Random(f"{SEED}:{sid}:indoor")
    sub = ind_all[rng.sample(range(len(ind_all)), min(1500, len(ind_all)))]
    sub_isl = [closed_island(p) for p in sub]
    _, sub_labs, _ = regs.classify(sub)
    ci = Counter(k for k in sub_isl if k >= 0)
    if not ci:
        meta["reason"] = "no indoor points on the closed navmesh"
        return meta
    rooms_in = defaultdict(set)
    for k, L in zip(sub_isl, sub_labs):
        if k >= 0:
            rooms_in[k] |= {r for r in L if r in W.ROOMS}
    # main part of the house: most canonical room types, then most indoor points
    house = max(ci, key=lambda k: (len(rooms_in[k]), ci[k], -k))
    meta["house_main_part_frac"] = round(ci[house] / max(1, sum(ci.values())), 3)
    in_house = {c: closed_island(p) == house for c, p in room_pts.items()}

    def door_pts(a):
        """Point just outside (0.8 m before the crossing, in the outdoor polygon) and
        just inside (>= 0.8 m after it and >= 0.3 m into the first indoor region)."""
        X, s_, cls = a["X"], a["s"], a["cls"]
        k = int(np.argmin(np.abs(s_ - max(0.0, a["s_cross"] - 0.8))))
        while k > 0 and cls[k] != "O":
            k -= 1
        O_ = X[k]
        k = int(np.argmin(np.abs(s_ - min(max(a["s_cross"] + 0.8, a["s_first_in"] + 0.3), a["s_total"]))))
        while k < len(X) - 1 and cls[k] != "I":
            k += 1
        return O_, X[k]

    def into_house(a):
        return closed_island(door_pts(a)[1]) == house

    # ── front door ─────────────────────────────────────────────────────────
    rng = random.Random(f"{SEED}:{sid}:outdoor")
    samp = outdoor_pts_all[rng.sample(range(len(outdoor_pts_all)), min(60, len(outdoor_pts_all)))]
    door_rule = None
    best = None
    if "entryway" in room_pts and in_house["entryway"]:
        # outdoor samples: 60 seeded + the 20 outdoor points closest to the entryway
        tgt = room_pts["entryway"]
        near = outdoor_pts_all[np.argsort(np.linalg.norm((outdoor_pts_all - tgt)[:, [0, 2]], axis=1))[:20]]
        res = []
        for p in np.vstack([samp, near]):
            pts = nav.path(p, tgt)
            if pts is None:
                continue
            a = analyse_path(regs, pts)
            if a is not None and into_house(a):
                res.append(a)
        adj = [a for a in res if "entryway" in a["first_canon"]]
        if adj:
            best = min(adj, key=lambda a: a["remaining"])
            door_rule = "adjacent to entryway"
        elif res:
            best = min(res, key=lambda a: a["remaining"])
            door_rule = "entryway present but no exterior opening adjacent to it: opening nearest the entryway"
    if best is None:
        # Outdoor reference point: navigable point of the largest outdoor region (on
        # the main island) closest to that region's polygon centroid.  Nearest
        # indoor region = the indoor region (of the house's main part) of the indoor
        # navigable point with the smallest walking distance from the reference
        # point; the door is where that path leaves the outdoor polygons.
        sizes = {i: int((isl[i] == main).sum()) for i in regs.outdoor_idx}
        oi = max(sizes, key=lambda i: (sizes[i], -i))
        G = grids[oi][isl[oi] == main]
        cen = regs.items[oi]["P"].mean(0)
        ref = G[int(np.argmin(np.linalg.norm(G[:, [0, 2]] - cen, axis=1)))]
        ind = np.array([p for p in sub if closed_island(p) == house])
        # Euclidean pre-filter, then walking distance on the 200 closest
        ind = ind[np.argsort(np.linalg.norm((ind - ref)[:, [0, 2]], axis=1))[:200]]
        bestd, bestp = np.inf, None
        for q in ind:
            pts = nav.path(ref, q)
            if pts is not None and arc(pts)[-1] < bestd:
                bestd, bestp = arc(pts)[-1], pts
        if bestp is not None:
            a = analyse_path(regs, bestp)
            if a is not None:
                best = a
                pre = "entryway not in the house's main part; " if "entryway" in room_pts else ""
                door_rule = (f"no entryway: {pre}opening on the shortest walk from the outdoor reference point to the "
                             f"nearest indoor region ({','.join(a['first_canon'])})")
                meta["nearest_indoor_region"] = a["first_canon"]
                meta["outdoor_reference"] = ref.round(3).tolist()
    if best is None:
        meta["reason"] = "no exterior opening found on outdoor->indoor paths"
        return meta

    D = best["D"]
    O_d, I_d = door_pts(best)
    # ── walking navmesh: the same stage with every other exterior opening closed ──
    boxes = fence_boxes(regs, D)
    build_fenced_navmesh(sid, boxes)
    navf = Nav(sid, fenced=True)
    pff = navf.pf
    O_w, I_w = navf.snap(O_d), navf.snap(I_d)
    through = navf.path(I_w, O_w) if (O_w is not None and I_w is not None) else None
    door_pass = None
    if through is not None:
        Xt, _ = densify(through, 0.02)
        door_pass = float(np.min(np.linalg.norm((Xt - D)[:, [0, 2]], axis=1)))
    meta["fence_boxes"] = len(boxes)
    if through is None or door_pass > 0.5 or arc(through)[-1] > 4.0:
        meta["reason"] = "front door not passable on the walking (fenced) navmesh"
        meta["door_D"] = D.round(3).tolist()
        return meta
    # room points must be reachable from the front door on the walking navmesh
    lost, moved = [], []
    for c in sorted(room_pts):
        p = room_pts[c]
        if pff.is_navigable(np.asarray(p, np.float32)) and navf.path(I_w, p) is not None \
                and np.linalg.norm(navf.path(I_w, p)[-1] - p) < 0.05:
            continue
        rs = sorted(i for i in cand if regs.items[i]["canon"] == c)
        rng = random.Random(f"{SEED}:{sid}:{c}:fenced")
        pool = [(i, q) for i in rs for q in cand[i]]
        rng.shuffle(pool)
        for i, q in pool[:400]:
            pq = navf.path(I_w, q) if pff.is_navigable(np.asarray(q, np.float32)) else None
            if pq is not None and np.linalg.norm(pq[-1] - q) < 0.05:
                room_pts[c], room_region[c] = q, regs.items[i]["name"]
                moved.append(c)
                break
        else:
            lost.append(c)
            room_pts.pop(c)
            room_region.pop(c)
    meta["rooms_repicked_on_walking_navmesh"] = moved
    meta["rooms_unreachable_on_walking_navmesh"] = lost
    meta["rooms_present"] = sorted(room_pts)
    meta["canonical_rooms_present"] = [r for r in W.ROOMS if r in room_pts]
    # outdoor anchor ("away" point): outdoor point at walking distance ~6 m from the
    # point just outside the door whose path never enters an indoor region (fallback:
    # the point just outside the door).
    rng = random.Random(f"{SEED}:{sid}:anchor")
    pool = outdoor_pts_all[rng.sample(range(len(outdoor_pts_all)), min(300, len(outdoor_pts_all)))]
    anchor, best_err = None, 1e9
    geos = []
    for p in pool:
        if not pff.is_navigable(np.asarray(p, np.float32)):
            continue
        pts = navf.path(O_w, p)
        if pts is None or np.linalg.norm(pts[-1] - p) > 0.05:
            continue
        geos.append((abs(arc(pts)[-1] - 6.0), tuple(p), pts))
    for err, p, pts in sorted(geos, key=lambda x: x[0]):
        Xp, _ = densify(pts, 0.1)
        c, _, _ = regs.classify(Xp)
        if (c == "I").any():
            continue
        anchor, best_err = np.array(p), err
        break
    if anchor is None:
        anchor = O_w.copy()
    # door-adjacent indoor region (for the record and for the "__door__" fallback)
    c_in, l_in, _ = regs.classify(I_w[None])
    meta.update(
        eligible=True, reason="",
        door=dict(D=D.round(3).tolist(), outside=O_w.round(3).tolist(), inside=I_w.round(3).tolist(),
                  anchor=anchor.round(3).tolist(), anchor_walk_m=round(float(best_err + 6.0), 2) if best_err < 1e8 else 0.0,
                  rule=door_rule, adjacent_canon=best["first_canon"], adjacent_label=best["first_label"],
                  inside_point_labels=sorted(l_in[0]), passage_dist_to_D_m=round(door_pass, 3)),
        room_points={c: p.round(3).tolist() for c, p in room_pts.items()},
        room_point_region={c: room_region[c] for c in room_pts},
        regions=[dict(name=g["name"], label=g["label"], canon=g["canon"], outdoor=g["outdoor"],
                      floor_height=g["fh"], poly=g["P"].round(3).tolist()) for g in regs.items],
        navigable_area_m2=round(float(pf.navigable_area), 1),
        walking_navigable_area_m2=round(float(pff.navigable_area), 1),
    )
    # hallway on the door-to-room paths (walking navmesh)
    hw = {}
    for c in W.ROOMS:
        if c in room_pts:
            pts = navf.path(I_w, room_pts[c])
            if pts is None:
                hw[c] = None
                continue
            Xp, _ = densify(pts, 0.1)
            _, labs, _ = regs.classify(Xp)
            seq = []
            for L in labs:
                for r in sorted(L):
                    if not seq or seq[-1] != r:
                        seq.append(r)
            hw[c] = dict(hallway_on_path=any("hallway" in L for L in labs), length_m=round(float(arc(pts)[-1]), 2),
                         room_sequence=seq)
    meta["door_to_room_paths"] = hw
    # inventory of exterior openings reached from the outdoor samples (for the record)
    found = []
    for p in samp[:30]:
        for c in sorted(room_pts):
            pts = nav.path(p, room_pts[c])
            a = analyse_path(regs, pts) if pts is not None else None
            if a is not None:
                found.append((a["D"], tuple(a["first_canon"])))
    clusters = []
    for Dp, adjc in found:
        for cl in clusters:
            if np.linalg.norm((cl["D"] - Dp)[[0, 2]]) < 1.0:
                cl["n"] += 1
                cl["adj"].update(adjc)
                break
        else:
            clusters.append(dict(D=Dp, n=1, adj=Counter(adjc)))
    meta["exterior_openings"] = [dict(x=round(float(c["D"][0]), 2), z=round(float(c["D"][2]), 2), n_paths=c["n"],
                                      adjacent=dict(c["adj"]),
                                      is_front_door=bool(np.linalg.norm((c["D"] - D)[[0, 2]]) < 1.0))
                                 for c in clusters]
    meta["hallway_on_any_door_path"] = any(v and v["hallway_on_path"] for k, v in hw.items() if k != "hallway")
    meta["analysis_s"] = round(time.time() - t0, 2)
    return meta


def cmd_scenes():
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    cands = json.loads(CANDIDATES.read_text())
    sids = sorted(s for s, labs in cands.items() if any(k in OUTDOOR_LABELS for k in labs)
                  and (HSSD / f"stages/{s}.glb").exists()
                  and (HSSD / f"semantics/scenes/{s}.semantic_config.json").exists())
    print(f"{len(sids)} candidate scenes with an outdoor/porch region, stage and semantic config")
    summary = []
    for sid in sids:
        try:
            m = analyse_scene(sid)
        except Exception as e:  # recorded, scene ineligible
            m = dict(scene=sid, eligible=False, reason=f"error: {e!r}")
        (SCENE_DIR / f"{sid}.json").write_text(json.dumps(m, indent=1))
        summary.append({k: m.get(k) for k in ("scene", "eligible", "reason", "canonical_rooms_present",
                                                "indoor_frac_on_main_island", "hallway_on_any_door_path")}
                       | {"door_rule": (m.get("door") or {}).get("rule"),
                          "door_adjacent": (m.get("door") or {}).get("adjacent_canon")})
        print(sid, m["eligible"], m.get("reason", ""), (m.get("door") or {}).get("adjacent_canon"), flush=True)
    (OUT / "scenes_summary.json").write_text(json.dumps(summary, indent=1))
    el = [s for s in summary if s["eligible"]]
    print(f"eligible: {len(el)} / {len(summary)}")


def eligible_scenes():
    summ = json.loads((OUT / "scenes_summary.json").read_text())
    return sorted(s["scene"] for s in summ if s["eligible"])


# ════════════════════════════════════════════════════════════════════════════
# Schedule execution
# ════════════════════════════════════════════════════════════════════════════
def build_actions(tasks, works_from_home=True):
    """Door and room actions with exactly the classification of
    e1v2_workloads.schedule_to_trace.  Returns [(time, order, kind, info)]."""
    acts = []
    names = [str(t.get("name", "")).lower() for t in tasks]
    has_commute = any(any(w in n for w in W.DEPART_WORDS + W.RETURN_WORDS) for n in names)
    for idx, (t, name) in enumerate(zip(tasks, names)):
        cat = str(t["category"]).upper()
        start, dur = int(t["start_s"]), max(60, int(t["duration_s"]))
        room = W._map_room(str(t.get("room", "")))
        info = dict(task=idx, name=t.get("name", ""), cat=cat, start=start, end=start + dur, room=room,
                    raw_room=str(t.get("room", "")))
        if any(w in name for w in W.DEPART_WORDS):
            acts.append((start, len(acts), "OUT", info))
            continue
        if any(w in name for w in W.RETURN_WORDS):
            acts.append((start + dur, len(acts), "IN", info))
            continue
        if any(w in name for w in W.TRIP_WORDS) or cat in W.OUT_OF_HOME or room is None:
            acts.append((start, len(acts), "OUT", info))
            acts.append((start + dur, len(acts), "IN", info))
            continue
        if cat == "WORK" and not works_from_home:
            if not has_commute:
                acts.append((start, len(acts), "OUT", info))
                acts.append((start + dur, len(acts), "IN", info))
            continue
        acts.append((start, len(acts), "GOTO", info))
    # _door() clips at the end of the day; a door action at >= DAY never happens
    acts = [a for a in acts if not (a[2] in ("OUT", "IN") and a[0] >= DAY)]
    return sorted(acts, key=lambda a: (a[0], a[1]))


class SceneRT:
    """Scene runtime: navmesh + regions + door + room points."""

    def __init__(self, sid: str):
        self.sid = sid
        self.meta = json.loads((SCENE_DIR / f"{sid}.json").read_text())
        self.regs = Regions(sid)
        self.nav = Nav(sid, fenced=True)   # walking navmesh
        d = self.meta["door"]
        self.D, self.O, self.I, self.A = (np.array(d[k], float) for k in ("D", "outside", "inside", "anchor"))
        self.rooms = {c: np.array(p, float) for c, p in self.meta["room_points"].items()}

    def target(self, room: str):
        """(point, resolved_label, used_fallback)."""
        if room in self.rooms:
            return self.rooms[room], room, False
        for fb in FALLBACK.get(room, ["__door__"]):
            if fb == "__door__":
                return self.I, "__door__", True
            if fb in self.rooms:
                return self.rooms[fb], fb, True
        return self.I, "__door__", True

    def route(self, waypoints):
        """Concatenated shortest paths through waypoints; None if any leg fails.
        Returns (points, arc length at which each waypoint is reached)."""
        out = [np.asarray(waypoints[0], float)[None]]
        wp_s = [0.0]
        total = 0.0
        for a, b in zip(waypoints, waypoints[1:]):
            if np.linalg.norm(np.asarray(a) - np.asarray(b)) < 1e-6:
                wp_s.append(total)
                continue
            p = self.nav.path(a, b)
            if p is None:
                return None, None
            seg = p[1:] if np.linalg.norm(p[0] - out[-1][-1]) < 1e-3 else p
            total += float(arc(np.vstack([out[-1][-1:], seg]))[-1])
            out.append(seg)
            wp_s.append(total)
        return np.vstack(out), wp_s

    def door_arc(self, pts, s_lo, s_hi):
        """Arc length at which the agent passes the front door: the point of the
        route between the outside and inside door points (arc s_lo..s_hi) closest to D."""
        X, s = densify(pts, 0.02)
        m = (s >= min(s_lo, s_hi) - 1e-6) & (s <= max(s_lo, s_hi) + 1e-6)
        idx = np.where(m)[0] if m.any() else np.arange(len(s))
        k = idx[int(np.argmin(np.linalg.norm((X[idx] - self.D)[:, [0, 2]], axis=1)))]
        return float(s[k]), float(np.linalg.norm((X[k] - self.D)[[0, 2]]))


def execute(sched: dict, rt: SceneRT) -> dict:
    """Walk one schedule-day in one scene.  Returns legs + statistics."""
    acts = build_actions(sched["tasks"], sched.get("works_from_home", True))
    stats = Counter()
    door_kinds = [a[2] for a in acts if a[2] in ("OUT", "IN")]
    inside = not (door_kinds and door_kinds[0] == "IN")
    bed, _, fb = rt.target("bedroom")
    pos = bed.copy() if inside else rt.A.copy()
    stats["start_outside"] = int(not inside)
    t_free = 0.0
    legs = []   # dict(t0, pts, s, dur, door_s(list of arc lengths), kind, task, nomotion)
    pending = None
    targets = Counter()

    def add_leg(t0, waypoints, kind, info, door_count):
        nonlocal pos, t_free
        pts, wp_s = rt.route(waypoints)
        if pts is None:
            stats["path_failures"] += 1
            stats[f"path_failures_{kind}"] += 1
            return None
        S = arc(pts)
        door_s = []
        if door_count:
            # waypoints of door legs: [..., I, O, A] (out) or [pos, A, O, I, (target)] (in)
            iO = next(j for j, w in enumerate(waypoints) if w is rt.O)
            iI = next(j for j, w in enumerate(waypoints) if w is rt.I)
            ds, dd = rt.door_arc(pts, wp_s[iO], wp_s[iI])
            door_s.append(ds)
            stats["door_pass_dist_max_cm"] = max(stats["door_pass_dist_max_cm"], int(round(dd * 100)))
        leg = dict(t0=t0, pts=pts, S=S, dur=S[-1] / SPEED, door_s=door_s, kind=kind,
                   task=info["task"], cat=info["cat"], name=info["name"])
        legs.append(leg)
        pos = pts[-1].copy()
        t_free = t0 + leg["dur"]
        return leg

    for i, (t, _, kind, info) in enumerate(acts):
        t0 = max(float(t), t_free)
        if t0 >= DAY:
            stats["actions_after_midnight"] += 1
            continue
        if kind == "GOTO":
            tgt, lab, used_fb = rt.target(info["room"])
            targets[(info["room"], lab)] += 1
            stats["goto_tasks"] += 1
            stats["fallback_targets"] += int(used_fb)
            if not inside:
                nxt = next((a[2] for a in acts[i + 1:] if a[2] in ("OUT", "IN")), None)
                if nxt == "IN":
                    pending = (info, tgt)
                    stats["deferred_tasks"] += 1
                    continue
                stats["implicit_returns"] += 1
                if add_leg(t0, [pos, rt.A, rt.O, rt.I, tgt], "in+goto", info, 1) is not None:
                    inside = True
                continue
            add_leg(t0, [pos, tgt], "goto", info, 0)
        elif kind == "OUT":
            if not inside:
                stats["redundant_out"] += 1
                continue
            if add_leg(t0, [pos, rt.I, rt.O, rt.A], "out", info, 1) is not None:
                inside = False
                pending = None
        elif kind == "IN":
            if inside:
                stats["redundant_in"] += 1
                continue
            wp = [pos, rt.A, rt.O, rt.I]
            if pending is not None and pending[0]["end"] > t0:
                wp.append(pending[1])
                stats["deferred_executed"] += 1
                info = pending[0] | {"cat": pending[0]["cat"]}
                kind_l = "in+goto"
            else:
                kind_l = "in"
            pending = None
            if add_leg(t0, wp, kind_l, info, 1) is not None:
                inside = True
    stats["deferred_dropped"] = stats["deferred_tasks"] - stats["deferred_executed"]
    return dict(legs=legs, stats=stats, targets=targets, start=pos if not legs else legs[0]["pts"][0],
                start_inside=bool(not stats["start_outside"]))


def samples(run: dict, rt: SceneRT):
    """Position samples: every DT s while walking, plus each leg's arrival.
    Returns times (n,), positions (n,3), leg index (n,), is_arrival (n,)."""
    T, P, Li, Arr = [], [], [], []
    legs = run["legs"]
    start = legs[0]["pts"][0] if legs else (rt.A if not run["start_inside"] else rt.target("bedroom")[0])
    T.append(0.0), P.append(np.asarray(start, float)), Li.append(-1), Arr.append(True)
    for j, lg in enumerate(legs):
        n = int(np.floor(lg["dur"] / DT))
        ts = lg["t0"] + DT * np.arange(n + 1)
        ss = SPEED * DT * np.arange(n + 1)
        if lg["dur"] - n * DT > 1e-9:
            ts = np.append(ts, lg["t0"] + lg["dur"])
            ss = np.append(ss, lg["S"][-1])
        X = interp(lg["pts"], lg["S"], ss)
        T.extend(ts.tolist()); P.extend(list(X)); Li.extend([j] * len(ts))
        Arr.extend([False] * (len(ts) - 1) + [True])
    return np.array(T), np.array(P), np.array(Li), np.array(Arr)


def presence_intervals(T, labs, end=float(DAY)):
    """Per label: list of [enter, exit) from samples (labels hold until the next sample)."""
    iv = defaultdict(list)
    cur = {}
    for t, L in zip(T, labs):
        for r in list(cur):
            if r not in L:
                iv[r].append((cur.pop(r), t))
        for r in L:
            if r not in cur:
                cur[r] = t
    for r, a in cur.items():
        iv[r].append((a, end))
    return iv


def _emit_merged(ints, room):
    """Integer (on, off) intervals -> merged motion/motion_off events (an interval
    starting at or before the previous OFF second continues it: no second
    activation, no OFF in between)."""
    merged = []
    for on, off in sorted(ints):
        if merged and on <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], off)
        else:
            merged.append([on, off])
    ev = []
    for on, off in merged:
        if on < DAY:
            ev.append((on, "motion", room))
        if off < DAY:
            ev.append((off, "motion_off", room))
    return ev


def occupancy_events(iv):
    """Preregistered room sensor: ON at entry, OFF 5 s after exit; re-entry within
    the 5 s keeps it on.  Presence that lasts to the end of the day has no OFF."""
    ev = []
    for r, ivs in iv.items():
        ints = [(int(a), int(b + ROOM_OFF_LAG) if b < DAY else 10 * DAY) for a, b in ivs]
        ev += _emit_merged(ints, r)
    return ev


def pir_events(iv, quiet):
    """Schedule-level PIR model on the walked presence: a trigger at region entry and
    every MOTION_INTERVAL s while inside, each MOTION_ON s long; periodic
    re-triggers are suppressed while dwelling in a NO_MOTION task (quiet intervals).
    Overlapping pulses merge (one activation)."""
    ev = []
    q = sorted(quiet)

    def is_quiet(t):
        return any(a <= t < b for a, b in q)

    for r, ivs in iv.items():
        pulses = []
        for a, b in sorted(ivs):
            pulses.append((int(a), int(a) + W.MOTION_ON))
            t = a + W.MOTION_INTERVAL
            while t < b and t < DAY:
                if not is_quiet(t):
                    pulses.append((int(t), int(t) + W.MOTION_ON))
                t += W.MOTION_INTERVAL
        ev += _emit_merged(pulses, r)
    return ev


def door_events(run):
    """door_open at each front-door crossing, door_close 3 s later; a crossing
    while the door is still open extends the opening (no second door_open)."""
    crossings = sorted(lg["t0"] + s / SPEED for lg in run["legs"] for s in lg["door_s"])
    opens = []
    for tc in crossings:
        t = int(tc)
        if t >= DAY:
            continue
        if opens and t < opens[-1][1]:
            opens[-1][1] = t + DOOR_CLOSE_LAG
        else:
            opens.append([t, t + DOOR_CLOSE_LAG])
    ev = []
    for a, b in opens:
        ev.append((a, "door_open", "entryway"))
        if b < DAY:
            ev.append((b, "door_close", "entryway"))
    return ev, crossings


def quiet_intervals(run):
    """Dwell intervals of NO_MOTION tasks (arrival -> next leg start), plus the
    overnight stay in the bedroom before the first leg when the day starts at home."""
    legs = run["legs"]
    out = []
    if run["start_inside"]:
        out.append((0.0, legs[0]["t0"] if legs else float(DAY)))
    for j, lg in enumerate(legs):
        if lg["kind"] in ("goto", "in+goto") and lg["cat"] in W.NO_MOTION:
            a = lg["t0"] + lg["dur"]
            b = legs[j + 1]["t0"] if j + 1 < len(legs) else float(DAY)
            out.append((a, b))
    return out


def run_traces(sched, rt):
    run = execute(sched, rt)
    T, P, Li, Arr = samples(run, rt)
    cls, labs, _ = rt.regs.classify(P)
    iv = presence_intervals(T, labs)
    dev, crossings = door_events(run)
    trace = sorted(occupancy_events(iv) + dev)
    trace_pir = sorted(pir_events(iv, quiet_intervals(run)) + dev)
    return run, trace, trace_pir, dict(T=T, P=P, Li=Li, Arr=Arr, cls=cls, labs=labs, crossings=crossings)


def load_schedules():
    out = []
    for f in sorted(SCHED_DIR.glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("tasks"):
            out.append((f.stem, d))
    return out


def draw_scenes(schedules, eligible):
    rng = random.Random(SEED)
    return {stem: rng.sample(eligible, K) for stem, _ in schedules}


def cmd_run():
    eligible = eligible_scenes()
    schedules = load_schedules()
    draw = draw_scenes(schedules, eligible)
    (OUT / "scene_draw.json").write_text(json.dumps(dict(seed=SEED, K=K, eligible=eligible, draw=draw), indent=1))
    for sub in ("traces", "traces_pir"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    by_scene = defaultdict(list)
    for stem, d in schedules:
        for sid in draw[stem]:
            by_scene[sid].append((stem, d))
    rows = []
    for sid in sorted(by_scene):
        rt = SceneRT(sid)
        for stem, d in by_scene[sid]:
            run, tr, trp, _ = run_traces(d, rt)
            name = f"{stem}__{sid}"
            st = run["stats"]
            sched_tr = W.schedule_to_trace(d["tasks"], d.get("works_from_home", True))
            meta = dict(schedule=stem, scene=sid, model=d.get("model"), date=d.get("date"),
                        works_from_home=d.get("works_from_home", True), stats=dict(st),
                        targets={f"{a}->{b}": n for (a, b), n in run["targets"].items()})
            (OUT / "traces" / f"{name}.json").write_text(json.dumps(meta | {"sensor_model": "occupancy (preregistered)", "trace": tr}))
            (OUT / "traces_pir" / f"{name}.json").write_text(json.dumps(meta | {"sensor_model": "schedule-level PIR (added sensitivity)", "trace": trp}))
            rooms = sorted({r for (_, k, r) in tr if k == "motion"})
            rows.append(dict(
                schedule=stem, scene=sid, model=d.get("model"), date=d.get("date"),
                n_door_openings=sum(1 for e in tr if e[1] == "door_open"),
                n_door_openings_sched=sum(1 for e in sched_tr if e[1] == "door_open"),
                rooms_visited=";".join(rooms), n_rooms_visited=len(rooms),
                n_canonical_rooms_visited=len([r for r in rooms if r in W.ROOMS]),
                path_failures=st["path_failures"],
                total_walking_time_s=round(sum(lg["dur"] for lg in run["legs"]), 1),
                n_legs=len(run["legs"]), fallback_targets=st["fallback_targets"], goto_tasks=st["goto_tasks"],
                implicit_returns=st["implicit_returns"], redundant_out=st["redundant_out"],
                redundant_in=st["redundant_in"], deferred_tasks=st["deferred_tasks"],
                deferred_dropped=st["deferred_dropped"], start_outside=st["start_outside"],
                n_events=len(tr), n_events_pir=len(trp)))
        print(sid, len(by_scene[sid]), "runs", flush=True)
    with open(OUT / "manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["schedule"], r["scene"])))
    print("runs:", len(rows))


# ════════════════════════════════════════════════════════════════════════════
# Sanity checks
# ════════════════════════════════════════════════════════════════════════════
def cmd_sanity():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    eligible = eligible_scenes()
    schedules = dict(load_schedules())
    draw = json.loads((OUT / "scene_draw.json").read_text())["draw"]
    manifest = list(csv.DictReader(open(OUT / "manifest.csv")))
    agg = Counter()
    teleports, max_step = 0, 0.0
    door_unclosed, door_other, door_cross_total, door_far = 0, 0, 0, 0
    other_examples = []
    indoor_legs_outdoors, indoor_legs = 0, 0
    after_open = Counter()
    first_room_after_open = Counter()
    per_run = []
    examples = []
    hall_target = Counter()
    fallback_map = Counter()
    by_scene = defaultdict(list)
    for stem, sids in draw.items():
        for sid in sids:
            by_scene[sid].append(stem)
    rts = {}
    for sid in sorted(by_scene):
        rt = rts.setdefault(sid, SceneRT(sid))
        for stem in by_scene[sid]:
            d = schedules[stem]
            run, tr, trp, smp = run_traces(d, rt)
            # same result as the files on disk
            disk = json.loads((OUT / "traces" / f"{stem}__{sid}.json").read_text())["trace"]
            agg["mismatch_with_disk"] += int([tuple(e) for e in disk] != [tuple(e) for e in tr])
            T, P, Li, cls = smp["T"], smp["P"], smp["Li"], smp["cls"]
            # teleports: consecutive samples > 1.5 m apart
            dist = np.linalg.norm(np.diff(P, axis=0), axis=1)
            teleports += int((dist > 1.5).sum())
            max_step = max(max_step, float(dist.max()) if len(dist) else 0.0)
            # samples on the navmesh
            agg["samples"] += len(P)
            agg["samples_off_navmesh"] += int(sum(not rt.nav.pf.is_navigable(np.asarray(p, np.float32), 0.5) for p in P[::7]))
            agg["samples_checked_navmesh"] += len(P[::7])
            # door open/close pairing
            ev = [e for e in tr if e[1].startswith("door")]
            state = "closed"
            for (t, k, _) in ev:
                if k == "door_open":
                    if state == "open":
                        agg["door_open_while_open"] += 1
                    state = "open"
                    door_cross_total += 1
                else:
                    if state == "closed":
                        agg["door_close_while_closed"] += 1
                    state = "closed"
            if state == "open":
                door_unclosed += 1
            # crossings detected geometrically: O<->I transitions anywhere
            last = None
            for j in range(len(P)):
                c = cls[j]
                if c == "-":
                    continue
                if last is not None and c != cls[last] and Li[j] == Li[last]:
                    mid = (P[j] + P[last]) / 2
                    dd = float(np.linalg.norm((mid - rt.D)[[0, 2]]))
                    agg["geometric_crossings"] += 1
                    if dd > 1.5:
                        door_other += 1
                        if len(other_examples) < 5:
                            other_examples.append((stem, sid, round(float(T[j]), 1), round(dd, 2)))
                last = j
            # in-home legs (inside -> inside) that pass through outdoor polygons, or
            # through unlabeled navmesh > 1 m from every indoor polygon (outside the house)
            for j, lg in enumerate(run["legs"]):
                if lg["kind"] == "goto":
                    indoor_legs += 1
                    Xl, _ = densify(lg["pts"], 0.1)
                    cl_, _, _ = rt.regs.classify(Xl)
                    if (cl_ == "O").any():
                        indoor_legs_outdoors += 1
                    gap = Xl[cl_ == "-"]
                    if len(gap):
                        dmin = np.full(len(gap), np.inf)
                        for i_ in rt.regs.indoor_idx:
                            dmin = np.minimum(dmin, seg_dist(gap[:, [0, 2]], rt.regs.items[i_]["P"]))
                        if (dmin > 1.0).any():
                            agg["indoor_legs_outside_house_unlabeled"] += 1
            # what happens right after each opening (split by crossing direction)
            motions = [(t, r) for (t, k, r) in tr if k == "motion"]
            direction = {}
            for lg in run["legs"]:
                for ds in lg["door_s"]:
                    direction.setdefault(int(lg["t0"] + ds / SPEED), "leaving" if lg["kind"] == "out" else "entering")
            for (t, k, _) in tr:
                if k != "door_open":
                    continue
                agg["openings"] += 1
                dr = direction.get(t, "?")
                agg["openings_" + dr] += 1
                for R in ("entryway", "hallway"):
                    if any(t <= s <= t + DOOR_CLOSE_LAG and r == R for s, r in motions):
                        after_open[(R, "door_physically_open(<=3s)")] += 1
                        after_open[(R, "door_physically_open(<=3s)", dr)] += 1
                    if any(t <= s <= t + 60 and r == R for s, r in motions):
                        after_open[(R, "<=60s")] += 1
                        after_open[(R, "<=60s", dr)] += 1
                nxt = [r for s, r in motions if t <= s <= t + DOOR_CLOSE_LAG]
                first_room_after_open[nxt[0] if nxt else "(none)"] += 1
            # direction of each crossing (entering vs leaving)
            for lg in run["legs"]:
                if lg["door_s"]:
                    agg["crossings_" + ("leaving" if lg["kind"] == "out" else "entering")] += 1
            for (a, b), n in run["targets"].items():
                if a == "hallway":
                    hall_target[b] += n
                fallback_map[(a, b)] += n
            sched_tr = W.schedule_to_trace(d["tasks"], d.get("works_from_home", True))
            per_run.append(dict(
                stem=stem, sid=sid,
                door_emb=sum(1 for e in tr if e[1] == "door_open"),
                door_sched=sum(1 for e in sched_tr if e[1] == "door_open"),
                rooms_emb=len({r for (_, k, r) in tr if k == "motion" and r in W.ROOMS}),
                rooms_sched=len({r for (_, k, r) in sched_tr if k == "motion" and r in W.ROOMS}),
                act_emb=sum(1 for e in tr if e[1] == "motion" and e[2] in W.ROOMS),
                act_pir=sum(1 for e in trp if e[1] == "motion" and e[2] in W.ROOMS),
                act_sched=sum(1 for e in sched_tr if e[1] == "motion"),
                walk=sum(lg["dur"] for lg in run["legs"])))
            if len(examples) < 3 and stem.endswith("2026-10-14") and sid not in [e[1] for e in examples] \
                    and any(lg["door_s"] for lg in run["legs"]):
                examples.append((stem, sid, run, tr, smp))
    # raw room-field statistics of the schedules
    raw_room = Counter()
    for stem, d in schedules.items():
        for t in d["tasks"]:
            raw_room[(str(t.get("room", "")), W._map_room(str(t.get("room", ""))))] += 1

    # ── figures ───────────────────────────────────────────────────────────
    figdir = OUT / "figs"
    figdir.mkdir(parents=True, exist_ok=True)
    figs = []
    palette = {"entryway": "#d62728", "hallway": "#ff7f0e", "kitchen": "#2ca02c", "living_room": "#1f77b4",
               "bedroom": "#9467bd"}
    for (stem, sid, run, tr, smp) in examples:
        rt = rts[sid]
        fig, ax = plt.subplots(figsize=(9, 8))
        V = np.asarray(rt.nav.pf.build_navmesh_vertices(), float)
        tri = V.reshape(-1, 3, 3)
        # one floor only: the floor of the front door
        keep = np.abs(tri[:, :, 1].mean(1) - rt.D[1]) < 1.0
        from matplotlib.collections import PolyCollection
        ax.add_collection(PolyCollection(tri[keep][:, :, [0, 2]], facecolor="#dddddd", edgecolor="none"))
        for g in rt.regs.items:
            if abs(g["fh"] + 0.2 - rt.D[1]) > 1.0:
                continue
            col = "#8c564b" if g["outdoor"] else palette.get(g["canon"], "#7f7f7f")
            P = np.vstack([g["P"], g["P"][:1]])
            ax.plot(P[:, 0], P[:, 1], color=col, lw=1.2, alpha=0.8)
            c = g["P"].mean(0)
            ax.text(c[0], c[1], g["canon"] if not g["outdoor"] else g["label"].split("/")[0], fontsize=7,
                    color=col, ha="center", va="center")
        P = smp["P"]
        ax.plot(P[:, 0], P[:, 2], color="black", lw=0.8, alpha=0.7, label="trajectory (one day)")
        for c, p in rt.rooms.items():
            if c in W.ROOMS:
                ax.plot(p[0], p[2], "o", color=palette[c], ms=6)
        ax.plot(rt.D[0], rt.D[2], "*", color="red", ms=18, label="front door (sensor)")
        ax.plot(rt.A[0], rt.A[2], "s", color="#8c564b", ms=7, label="outdoor 'away' point")
        ax.set_aspect("equal")
        ax.autoscale()
        nd = sum(1 for e in tr if e[1] == "door_open")
        ax.set_title(f"scene {sid}, schedule {stem}\n{nd} door openings; door adjacent to "
                     f"{rt.meta['door']['adjacent_canon']}", fontsize=9)
        ax.set_xlabel("x (m)"), ax.set_ylabel("z (m)")
        ax.legend(loc="best", fontsize=7)
        f = figdir / f"topdown_{sid}__{stem}.png"
        fig.tight_layout()
        fig.savefig(f, dpi=130)
        plt.close(fig)
        figs.append(f)

    # ── write SANITY.md ───────────────────────────────────────────────────
    import statistics as stx

    def ms(xs):
        return f"{stx.mean(xs):.2f} (sd {stx.pstdev(xs):.2f}, median {stx.median(xs):g}, range {min(xs)}-{max(xs)})"

    summ = json.loads((OUT / "scenes_summary.json").read_text())
    el_meta = [json.loads((SCENE_DIR / f"{s}.json").read_text()) for s in eligible]
    rule_cnt = Counter((m["door"]["rule"].split(":")[0]) for m in el_meta)
    adj_cnt = Counter(",".join(m["door"]["adjacent_canon"]) for m in el_meta)
    used = sorted({s for v in draw.values() for s in v})
    st_tot = Counter()
    for r in manifest:
        for k in ("path_failures", "fallback_targets", "goto_tasks", "implicit_returns", "redundant_out",
                  "redundant_in", "deferred_tasks", "deferred_dropped", "start_outside", "n_legs"):
            st_tot[k] += int(r[k])
    door_pass_max = max(json.loads((OUT / "traces" / f"{r['schedule']}__{r['scene']}.json").read_text())["stats"]
                        .get("door_pass_dist_max_cm", 0) for r in manifest)
    L = []
    L.append("# E2 LLM-embodied: sanity checks\n")
    L.append(f"Generated by `scripts/revision/e2_embodied.py sanity` on {time.strftime('%Y-%m-%d %H:%M')}. "
             "No attack replay, P(unsafe) or predictor was computed here.\n")
    L.append("## Scenes\n")
    L.append(f"- Candidates (outdoor or porch/terrace/deck/driveway region, stage + semantic config): {len(summ)}")
    L.append(f"- Eligible (outdoor region connected on the stage navmesh to an indoor region through an exterior opening): **{len(eligible)}**")
    inel = Counter(s["reason"] for s in summ if not s["eligible"])
    for k, v in inel.items():
        L.append(f"  - ineligible, {k}: {v}")
    L.append(f"- Front-door rule used: " + "; ".join(f"{k}: {v}" for k, v in rule_cnt.items()))
    L.append(f"- Region adjacent to the front door (canonical): " + "; ".join(f"{k}: {v}" for k, v in adj_cnt.most_common()))
    L.append(f"- Eligible scenes with a hallway on at least one door-to-room path: "
             f"{sum(1 for m in el_meta if m['hallway_on_any_door_path'])} / {len(el_meta)}")
    L.append(f"- Canonical rooms present (eligible scenes): " + "; ".join(
        f"{r}: {sum(1 for m in el_meta if r in m['rooms_present'])}" for r in W.ROOMS))
    L.append(f"- Scenes drawn at least once (K = {K}, seed {SEED}): {len(used)}\n")
    L.append("## Executions\n")
    L.append(f"- Schedules: {len(schedules)}; executions (schedule x scene): {len(manifest)}")
    L.append(f"- Legs walked: {st_tot['n_legs']}; **path failures: {st_tot['path_failures']}** "
             "(a failing leg is dropped and the agent stays where it is; none were needed if 0)")
    L.append(f"- In-home tasks: {st_tot['goto_tasks']}; target room absent -> documented fallback: "
             f"{st_tot['fallback_targets']} ({100 * st_tot['fallback_targets'] / max(1, st_tot['goto_tasks']):.1f}%)")
    L.append(f"- Physical-consistency adjustments (embodiment, counted over all executions): implicit return "
             f"(in-home task while away with no scheduled return) {st_tot['implicit_returns']}; departure while already "
             f"away (no-op) {st_tot['redundant_out']}; return while already home (no-op) {st_tot['redundant_in']}; "
             f"in-home tasks started while away and deferred to the scheduled return {st_tot['deferred_tasks']} "
             f"(dropped because over by then: {st_tot['deferred_dropped']}); executions that start the day away "
             f"(first door action is a return): {st_tot['start_outside']}")
    L.append("- Task target -> resolved target (all executions): " + "; ".join(
        f"{a}->{b}: {n}" for (a, b), n in sorted(fallback_map.items())) +
        " (`__door__` = the point just inside the front door; fallback chains: " +
        "; ".join(f"{k}: {' > '.join(v)}" for k, v in FALLBACK.items()) + ")")
    low = [m for m in el_meta if m.get("house_main_part_frac", 1) < 0.8 or len(m["canonical_rooms_present"]) < 3]
    L.append(f"- Eligible scenes where the front door's connected part of the house holds < 80% of the indoor "
             f"navigable points, or fewer than 3 canonical rooms are reachable: {len(low)} "
             + "(" + "; ".join(f"{m['scene']}: part {m.get('house_main_part_frac')}, rooms {m['canonical_rooms_present']}"
                               for m in low) + ")")
    L.append(f"- Room targets of in-home tasks that were `hallway`: "
             f"{sum(hall_target.values())} tasks over all executions "
             f"(resolved to: {dict(hall_target)}). In these 90 schedules every task's `room` field is already one of "
             f"the five canonical rooms, so `_map_room` is the identity: hallway targets caused by its "
             f"bathroom/toilet->hallway mapping: **0**. Raw room field -> target: "
             + "; ".join(f"{a}->{b}: {n}" for (a, b), n in sorted(raw_room.items())) + "\n")
    L.append("## Checks\n")
    L.append(f"1. **No teleports**: consecutive position samples > 1.5 m apart: **{teleports}** "
             f"(max step {max_step:.2f} m; walking step at 0.5 s is 0.55 m). Samples checked against the navmesh "
             f"(every 7th): {agg['samples_checked_navmesh']}, off-navmesh: {agg['samples_off_navmesh']}.")
    L.append(f"2. **door_open always followed by door_close**: openings {door_cross_total}; open at end of day "
             f"without close: {door_unclosed}; open-while-open: {agg['door_open_while_open']}; "
             f"close-while-closed: {agg['door_close_while_closed']}.")
    L.append(f"3. **Door crossings only at the front door**: geometric outdoor<->indoor transitions in the sampled "
             f"trajectories: {agg['geometric_crossings']}; of these farther than 1.5 m from the front door: "
             f"**{door_other}**{(' e.g. ' + str(other_examples)) if other_examples else ''}. In-home legs (inside -> "
             f"inside) that pass through an outdoor polygon: {indoor_legs_outdoors} / {indoor_legs}; through unlabeled "
             f"navmesh more than 1 m from every indoor polygon: {agg['indoor_legs_outside_house_unlabeled']}. "
             f"Max distance between a routed door passage and the door point: {door_pass_max} cm. "
             f"Crossings leaving: {agg['crossings_leaving']}, entering: {agg['crossings_entering']}.")
    L.append(f"4. **Agent never stuck**: path failures {st_tot['path_failures']} over {st_tot['n_legs']} legs.")
    L.append(f"5. **Traces on disk reproduce** from a fresh run: mismatches {agg['mismatch_with_disk']}.\n")
    L.append("### Spot-printed trajectories (room sequence per leg)\n")
    for (stem, sid, run, tr, smp) in examples:
        L.append(f"**{stem} in scene {sid}** (door adjacent to {rts[sid].meta['door']['adjacent_canon']})\n")
        L.append("```")
        for j, lg in enumerate(run["legs"][:40]):
            seq = []
            for k_ in np.where(smp["Li"] == j)[0]:
                L_ = smp["labs"][k_]
                for r in sorted(L_) or (["(outdoors)"] if smp["cls"][k_] == "O" else ["(unlabeled)"]):
                    if not seq or seq[-1] != r:
                        seq.append(r)
            h = int(lg["t0"] // 3600), int(lg["t0"] % 3600 // 60), int(lg["t0"] % 60)
            dmark = " [DOOR]" if lg["door_s"] else ""
            L.append(f"{h[0]:02d}:{h[1]:02d}:{h[2]:02d} {lg['kind']:8s} {lg['name'][:28]:28s} "
                     f"{lg['dur']:5.1f}s {' > '.join(seq)}{dmark}")
        L.append("```\n")
    L.append("## Comparison with the schedule-level traces of the same schedules\n")
    L.append("Per execution (270 = 90 schedules x 3 scenes); schedule-level values repeated per scene.\n")
    L.append("| quantity | embodied | schedule-level |\n|---|---|---|")
    L.append(f"| door openings / day | {ms([r['door_emb'] for r in per_run])} | {ms([r['door_sched'] for r in per_run])} |")
    L.append(f"| canonical rooms with an activation / day | {ms([r['rooms_emb'] for r in per_run])} | {ms([r['rooms_sched'] for r in per_run])} |")
    L.append(f"| canonical-room activations / day (occupancy model) | {ms([r['act_emb'] for r in per_run])} | {ms([r['act_sched'] for r in per_run])} |")
    L.append(f"| canonical-room activations / day (added PIR variant) | {ms([r['act_pir'] for r in per_run])} | - |")
    L.append(f"| walking time / day (s) | {ms([round(r['walk']) for r in per_run])} | - |")
    L.append("\nActivation counts differ by construction of the sensor model: the preregistered occupancy sensor fires "
             "once per region entry and stays ON while the agent dwells; the schedule-level converter re-triggers every "
             f"{W.MOTION_INTERVAL} s during in-home tasks (none during SLEEP/IDLE); the added PIR variant re-triggers every "
             f"{W.MOTION_INTERVAL} s whenever the agent is inside a room region, also between tasks (the agent is still "
             "there), except while dwelling in a SLEEP/IDLE task.")
    diff = Counter(np.sign(r["door_emb"] - r["door_sched"]) for r in per_run)
    L.append(f"\nDoor openings embodied vs schedule-level per execution: fewer {diff[-1]}, equal {diff[0]}, more {diff[1]}. "
             "Differences come only from physical consistency (cannot leave while away / return while home; "
             "implicit returns), and from crossings closer than 3 s merging into one opening.\n")
    L.append("## Entryway / hallway activations right after door openings (counts only)\n")
    L.append(f"Door openings: {agg['openings']}.\n")
    L.append("| trigger room | activation while door physically open (t_open..t_open+3 s) | activation within 60 s |\n|---|---|---|")
    for R in ("entryway", "hallway"):
        L.append(f"| {R} | {after_open[(R, 'door_physically_open(<=3s)')]} | {after_open[(R, '<=60s')]} |")
    L.append(f"\nBy crossing direction (openings entering {agg['openings_entering']}, leaving {agg['openings_leaving']}): " +
             "; ".join(f"{R} {dr}: {after_open[(R, 'door_physically_open(<=3s)', dr)]} within 3 s, "
                       f"{after_open[(R, '<=60s', dr)]} within 60 s"
                       for R in ("entryway", "hallway") for dr in ("entering", "leaving")) +
             ". When leaving, the agent has already passed the door-adjacent room before the door opens, so "
             "activations right after an opening come from entries.")
    L.append("\nFirst room activation while the door is physically open (all openings): " +
             "; ".join(f"{k}: {v}" for k, v in first_room_after_open.most_common()))
    sched_cnt = Counter()
    for stem, d in schedules.items():
        tr = W.schedule_to_trace(d["tasks"], d.get("works_from_home", True))
        for (t, k, _) in tr:
            if k == "door_open":
                sched_cnt["openings"] += 1
                ms_ = [(s, r) for (s, kk, r) in tr if kk == "motion" and t <= s <= t + DOOR_CLOSE_LAG]
                for R in ("entryway", "hallway"):
                    if any(r == R for s, r in ms_):
                        sched_cnt[R] += 1
    L.append(f"\nFor reference, schedule-level (90 traces, entryway is inferred 2 s after every opening; "
             f"door open 15 s): openings {sched_cnt['openings']}, entryway activation within 3 s "
             f"{sched_cnt['entryway']}, hallway within 3 s {sched_cnt['hallway']}.\n")
    hv = OUT / "humanoid_validation.json"
    if hv.exists():
        h = json.loads(hv.read_text())
        sm = h["summary"]
        L.append("## Humanoid validation (not used for outcomes)\n")
        L.append(f"Articulated Habitat humanoid (`{h['config'].get('urdf')}`, HumanoidRearrangeController with "
                 f"`{h['config'].get('walk_motion')}`, lin_speed {h['config'].get('lin_speed')} m/s, "
                 f"ctrl {h['config'].get('ctrl_freq_hz')} Hz, ang_speed {h['config'].get('ang_speed')}), headless, on the "
                 f"same walking navmesh, following the same legs as the navmesh agent; scenes {h['scenes']}.\n")
        L.append(f"- Legs: {sm['n_legs']}; reached goal: {sm['legs_reached_goal']}; same room-entry sequence: "
                 f"{sm['legs_same_room_sequence']}; matched room entries: {sm['n_matched_entries']}")
        L.append(f"- Room-entry time, humanoid vs navmesh agent (continuous): mean |diff| {sm['mean_abs_entry_diff_s']} s, "
                 f"max {sm['max_abs_entry_diff_s']} s")
        L.append(f"- Room-entry time, humanoid vs the 0.5 s sampled trace pipeline: mean |diff| "
                 f"{sm['mean_abs_entry_diff_vs_0p5s_pipeline_s']} s, max {sm['max_abs_entry_diff_vs_0p5s_pipeline_s']} s")
        L.append(f"- Front-door crossing time |diff| on door legs: {sm['door_crossing_abs_diff_s']} s")
        L.append(f"- Leg duration diff (humanoid - agent): mean {sm['mean_leg_duration_diff_s']} s, max |diff| "
                 f"{sm['max_abs_leg_duration_diff_s']} s\n")
    L.append("## Figures\n")
    for f in figs:
        L.append(f"- `{f.relative_to(ROOT)}`")
    (OUT / "SANITY.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


# ════════════════════════════════════════════════════════════════════════════
# Humanoid validation (not used for outcomes)
# ════════════════════════════════════════════════════════════════════════════
def entries_along(T, labs):
    out = []
    prev = frozenset()
    for t, L in zip(T, labs):
        for r in sorted(L - prev):
            out.append((r, float(t)))
        prev = L
    return out


def cmd_humanoid(n_scenes=2, n_legs=10):
    hs = _hab()
    import magnum as mn
    from omegaconf import OmegaConf
    from habitat.articulated_agents.humanoids.kinematic_humanoid import KinematicHumanoid
    from habitat.articulated_agent_controllers import HumanoidRearrangeController

    eligible = eligible_scenes()
    scenes = random.Random(f"{SEED}:humanoid").sample(eligible, n_scenes)
    schedules = load_schedules()
    draw = json.loads((OUT / "scene_draw.json").read_text())["draw"]
    urdf = HUM / "female_2/female_2.urdf"
    motion = HUM / "female_2/female_2_motion_data_smplx.pkl"
    walk = HUM / "walking_motion_processed_smplx.pkl"
    results = []
    report = dict(scenes=scenes, legs=[], config={})
    for sid in scenes:
        rt = SceneRT(sid)
        # legs: the walked legs of the first schedule (sorted) drawn for this scene,
        # first n_legs legs of length >= 2 m, door legs included as they come
        stems = [s for s, _ in schedules if sid in draw[s]]
        # distinct legs (start/end rounded to 0.1 m) of length >= 2 m from the
        # schedules drawn for this scene: up to 3 door legs (out/in), rest in-home
        door_l, home_l, seen = [], [], set()
        for stem in stems:
            run = execute(dict(schedules)[stem], rt)
            for lg in run["legs"]:
                key = (tuple(np.round(lg["pts"][0], 1)), tuple(np.round(lg["pts"][-1], 1)))
                if lg["S"][-1] < 2.0 or key in seen:
                    continue
                seen.add(key)
                (door_l if lg["door_s"] else home_l).append((stem, lg))
        legs = door_l[:3] + home_l[:n_legs - min(3, len(door_l))]
        cfg = hs.SimulatorConfiguration()
        cfg.scene_id = str(HSSD / f"stages/{sid}.glb")
        cfg.enable_physics = True
        cfg.create_renderer = False
        ag = hs.agent.AgentConfiguration()
        ag.sensor_specifications = []
        sim = hs.Simulator(hs.Configuration(cfg, [ag]))
        sim.pathfinder.load_nav_mesh(str(NAVCACHE / f"{sid}.navmesh"))
        acfg = OmegaConf.create(dict(articulated_agent_urdf=str(urdf), motion_data_path=str(motion),
                                     articulated_agent_type="KinematicHumanoid",
                                     auto_update_sensor_transform=False, joint_start_noise=0.0,
                                     joint_start_override=None, ik_arm_urdf=None))
        hum = KinematicHumanoid(acfg, sim)
        hum.reconfigure()
        ctrl = HumanoidRearrangeController(str(walk))
        # control rate chosen so that an integer number of mocap frames per step
        # gives 1.1 m/s (the controller truncates frames/step to an integer)
        n_frames = 3
        ctrl_freq = SPEED / (n_frames * ctrl.dist_per_step_size)
        ang_speed = 10.0   # habitat-lab default (OracleNavActionConfig.ang_speed)
        ctrl.set_framerate_for_linspeed(SPEED, ang_speed, ctrl_freq)
        # guard against float truncation in int(fps / motion_fps) (2.9999 -> 2)
        ctrl.motion_fps = ctrl.walk_motion.fps / (n_frames + 1e-6)
        report["config"] = dict(urdf=str(urdf.relative_to(ROOT)), walk_motion=str(walk.relative_to(ROOT)),
                                ctrl_freq_hz=round(ctrl_freq, 3), lin_speed=SPEED, ang_speed=ang_speed,
                                dist_thresh=0.2, mocap_frames_per_step=n_frames)
        dt = 1.0 / ctrl_freq
        for (stem, lg) in legs:
            pts = lg["pts"]
            # waypoints the agent route was built from: corners of the concatenated path
            hum.base_pos = mn.Vector3(*pts[0])
            v = pts[1] - pts[0]
            # face the first segment
            hum.base_rot = float(np.arctan2(-v[2], v[0])) if np.linalg.norm(v[[0, 2]]) > 1e-6 else 0.0
            ctrl.reset(hum.base_transformation)
            ctrl.prev_orientation = None
            ctrl.walk_mocap_frame = 0
            k = 1
            t = 0.0
            H_T, H_P = [0.0], [np.array(hum.base_pos)]
            max_steps = int((lg["S"][-1] / SPEED) * ctrl_freq * 4 + 600)
            for step in range(max_steps):
                cur = np.array(hum.base_pos)
                # advance waypoint when within 0.2 m (intermediate) of the next corner
                while k < len(pts) - 1 and np.linalg.norm((pts[k] - cur)[[0, 2]]) < 0.2:
                    k += 1
                tgt = pts[k]
                if k == len(pts) - 1 and np.linalg.norm((tgt - cur)[[0, 2]]) < 0.2:
                    break
                sp = hs.ShortestPath()
                sp.requested_start, sp.requested_end = cur.astype(np.float32), tgt.astype(np.float32)
                nxt = np.array(sp.points[1]) if sim.pathfinder.find_path(sp) and len(sp.points) > 1 else tgt
                rel = nxt - cur
                ctrl.obj_transform_base = hum.base_transformation
                ctrl.calculate_walk_pose(mn.Vector3(rel[0], 0.0, rel[2]))
                # keep the root on the navmesh (as OracleNavAction._update_controller_to_navmesh)
                base_offset = hum.params.base_offset
                prev_q = hum.base_pos
                tq = ctrl.obj_transform_base.translation + base_offset
                fq = sim.pathfinder.try_step(prev_q, tq)
                ctrl.obj_transform_base.translation += (fq - tq)
                hum.set_joint_transform(ctrl.joint_pose, ctrl.obj_transform_offset, ctrl.obj_transform_base)
                t += dt
                H_T.append(t)
                H_P.append(np.array(hum.base_pos))
            H_T, H_P = np.array(H_T), np.array(H_P)
            # navmesh agent along the same leg, fine resolution and the 0.5 s pipeline
            sF = np.arange(0, lg["S"][-1], SPEED * 0.02)
            A_T, A_P = sF / SPEED, interp(pts, lg["S"], sF)
            sC = np.arange(0, lg["S"][-1], SPEED * DT)
            C_T, C_P = sC / SPEED, interp(pts, lg["S"], sC)
            _, hl, _ = rt.regs.classify(H_P)
            _, al, _ = rt.regs.classify(A_P)
            _, cl, _ = rt.regs.classify(C_P)
            he, ae, ce = entries_along(H_T, hl), entries_along(A_T, al), entries_along(C_T, cl)

            # the starting room (entry at t = 0) is not an entry event
            ae = [e for e in ae if e[1] > 0]
            ce = [e for e in ce if e[1] > 0]
            he = [e for e in he if e[1] > 0]

            def match(x, y):
                cnt, out = Counter(), []
                yi = defaultdict(list)
                for r, t_ in y:
                    yi[r].append(t_)
                for r, t_ in x:
                    n = cnt[r]
                    cnt[r] += 1
                    if n < len(yi[r]):
                        out.append((r, t_, yi[r][n]))
                return out

            door_diff = None
            if lg["door_s"]:
                kh = int(np.argmin(np.linalg.norm((H_P - rt.D)[:, [0, 2]], axis=1)))
                door_diff = round(float(H_T[kh] - lg["door_s"][0] / SPEED), 2)
            m_fine = match(ae, he)
            m_pipe = match(ce, he)
            reached = float(np.linalg.norm((H_P[-1] - pts[-1])[[0, 2]]))
            report["legs"].append(dict(
                scene=sid, schedule=stem, kind=lg["kind"], length_m=round(float(lg["S"][-1]), 2),
                agent_s=round(lg["S"][-1] / SPEED, 2), humanoid_s=round(float(H_T[-1]), 2),
                humanoid_final_dist_m=round(reached, 2),
                agent_entries=[(r, round(t_, 2)) for r, t_ in ae], humanoid_entries=[(r, round(t_, 2)) for r, t_ in he],
                same_room_sequence=[r for r, _ in ae] == [r for r, _ in he],
                door_crossing_diff_s=door_diff,
                entry_diffs_fine=[round(b - a, 2) for (_, a, b) in m_fine],
                entry_diffs_pipeline=[round(b - a, 2) for (_, a, b) in m_pipe]))
            print(sid, stem, lg["kind"], report["legs"][-1]["length_m"], "agent", report["legs"][-1]["agent_s"],
                  "humanoid", report["legs"][-1]["humanoid_s"], "diffs", report["legs"][-1]["entry_diffs_fine"], flush=True)
        sim.close()
    fine = [abs(x) for lg in report["legs"] for x in lg["entry_diffs_fine"]]
    pipe = [abs(x) for lg in report["legs"] for x in lg["entry_diffs_pipeline"]]
    durd = [lg["humanoid_s"] - lg["agent_s"] for lg in report["legs"]]
    report["summary"] = dict(
        n_legs=len(report["legs"]), n_matched_entries=len(fine),
        mean_abs_entry_diff_s=round(float(np.mean(fine)), 2) if fine else None,
        max_abs_entry_diff_s=round(float(np.max(fine)), 2) if fine else None,
        mean_abs_entry_diff_vs_0p5s_pipeline_s=round(float(np.mean(pipe)), 2) if pipe else None,
        max_abs_entry_diff_vs_0p5s_pipeline_s=round(float(np.max(pipe)), 2) if pipe else None,
        mean_leg_duration_diff_s=round(float(np.mean(durd)), 2), max_abs_leg_duration_diff_s=round(float(np.max(np.abs(durd))), 2),
        legs_same_room_sequence=sum(lg["same_room_sequence"] for lg in report["legs"]),
        door_crossing_abs_diff_s=[abs(lg["door_crossing_diff_s"]) for lg in report["legs"]
                                  if lg["door_crossing_diff_s"] is not None],
        legs_reached_goal=sum(lg["humanoid_final_dist_m"] < 0.3 for lg in report["legs"]))
    (OUT / "humanoid_validation.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["summary"], indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scenes", "run", "sanity", "humanoid", "all"])
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd in ("scenes", "all"):
        cmd_scenes()
    if a.cmd in ("run", "all"):
        cmd_run()
    if a.cmd in ("humanoid", "all"):
        cmd_humanoid()
    if a.cmd in ("sanity", "all"):
        cmd_sanity()


if __name__ == "__main__":
    main()
