#!/usr/bin/env python3
"""
E1v2 workload sources for the paired phantom-delay replay (journal revision).

Every source produces "day traces": a sorted list of (second, kind, room) with
kind in {"motion", "motion_off", "door_open", "door_close"}.  "motion" is a
sensor ACTIVATION (0->1); "motion_off" is the matching deactivation.  The
replay harness consumes only this format, so the attack, rule set and outcome
definitions are identical across sources.

Sources
  aras_a    ARAS House A, 30 real days (sensor transitions)
  aras_b    ARAS House B, 30 real days (second real home)
  synth     inhomogeneous Poisson per room and door, hourly rates matched to aras_a
  shuffled  per-sensor day permutation of aras_a (keeps each sensor's daily
            dynamics, destroys cross-sensor coupling such as door <-> room)
  markov    first-order Markov chain over activation symbols fitted on aras_a,
            gaps drawn from aras_a's hour-of-day gap distribution
  llm       persona-conditioned LLM daily schedules (see e1v2_llm_schedules.py)

Neither ARAS home has an entryway motion sensor; entry presence is therefore
inferred uniformly for every source as an entryway activation 2 s after each
physical door opening (a person passing the door).  This sensor is NOT the
attacked one, so under attack it reports in real time while the door state is
held -- the stale-state condition the phantom-delay attack creates.
"""
from __future__ import annotations

import json
import random
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

Trace = List[Tuple[int, str, str]]
DAY = 86400
ROOMS = ["entryway", "hallway", "kitchen", "living_room", "bedroom"]
ARAS_ROOT = Path(__file__).resolve().parents[2] / "data/datasets/aras/Aras"

# ARAS House A (0-based columns); mapping identical to the INFOCOM runner.
HOUSE_A = {
    "door": 11,  # Co1 House Door
    "map": {2: "living_room", 3: "living_room", 4: "living_room", 5: "living_room",
            6: "living_room", 7: "kitchen", 8: "kitchen", 14: "hallway",
            15: "kitchen", 16: "kitchen", 0: "bedroom", 9: "bedroom",
            19: "bedroom", 10: "hallway", 12: "hallway", 17: "hallway"},
}
# ARAS House B (0-based columns), from the dataset README.
HOUSE_B = {
    "door": 2,  # co3 House Door
    "map": {0: "kitchen", 1: "kitchen",          # co1/co2 kitchen cupboard
            3: "bedroom", 4: "bedroom",          # co4/co5 wardrobe door
            5: "hallway",                        # co6 shower cabinet door (bathroom)
            6: "kitchen",                        # di2 tap
            7: "living_room", 8: "living_room", 9: "living_room",  # fo1-3 chairs
            10: "kitchen", 11: "kitchen",        # ph1 fridge, ph2 kitchen drawer
            12: "living_room", 13: "living_room",  # pr1/pr2 couch
            14: "bedroom", 15: "bedroom",        # pr3/pr4 bed
            16: "living_room",                   # pr5 armchair
            17: "hallway",                       # so1 bathroom door
            18: "kitchen",                       # so2 kitchen
            19: "hallway"},                      # so3 closet
}
ENTRY_LAG = 2  # seconds from physical door opening to inferred entry presence


def _add_entry_presence(trace: Trace) -> Trace:
    extra = [(t + ENTRY_LAG, "motion", "entryway") for (t, k, _) in trace
             if k == "door_open" and t + ENTRY_LAG < DAY]
    extra += [(t + ENTRY_LAG + 5, "motion_off", "entryway") for (t, k, _) in trace
              if k == "door_open" and t + ENTRY_LAG + 5 < DAY]
    return sorted(trace + extra)


# ── ARAS ─────────────────────────────────────────────────────────────────────
def _aras_raw(house: str, day: int) -> List[Tuple[int, int, int]]:
    """(second, column, new_value) for every sensor transition of one day."""
    f = ARAS_ROOT / f"House {house}" / f"DAY_{day}.txt"
    trans, prev = [], None
    with open(f) as fh:
        for s, line in enumerate(fh):
            parts = line.split()
            if len(parts) < 20:
                continue
            cur = [int(x) for x in parts[:20]]
            if prev is not None:
                for c in range(20):
                    if cur[c] != prev[c]:
                        trans.append((s, c, cur[c]))
            prev = cur
    return trans


def _raw_to_trace(raw, spec) -> Trace:
    out: Trace = []
    for (s, c, v) in raw:
        if c == spec["door"]:
            out.append((s, "door_open" if v == 1 else "door_close", "entryway"))
        elif c in spec["map"]:
            out.append((s, "motion" if v == 1 else "motion_off", spec["map"][c]))
    return sorted(out)


def aras_days(house: str = "A", days=range(1, 31)) -> Dict[str, Trace]:
    spec = HOUSE_A if house == "A" else HOUSE_B
    return {f"aras_{house.lower()}_d{d:02d}": _add_entry_presence(_raw_to_trace(_aras_raw(house, d), spec))
            for d in days}


# ── Statistics of ARAS A used to fit the synthetic baselines ────────────────
def _aras_a_stats():
    raws = {d: _aras_raw("A", d) for d in range(1, 31)}
    rate = defaultdict(float)          # (hour, room|'door') -> activations per second
    on_dur = defaultdict(list)         # room|'door' -> on-durations (s)
    for d, raw in raws.items():
        last_on = {}
        for (s, c, v) in raw:
            key = "door" if c == HOUSE_A["door"] else HOUSE_A["map"].get(c)
            if key is None:
                continue
            if v == 1:
                rate[(s // 3600, key)] += 1.0 / (30 * 3600)
                last_on[c] = s
            elif c in last_on:
                on_dur[key].append(max(1, s - last_on.pop(c)))
    return raws, rate, on_dur


def synthetic_days(n_days: int = 30, seed: int = 7) -> Dict[str, Trace]:
    """Inhomogeneous Poisson per room and door, hourly rates matched to ARAS A."""
    _, rate, on_dur = _aras_a_stats()
    rng = random.Random(seed)
    out = {}
    for k in range(n_days):
        tr: Trace = []
        for key in ROOMS[1:] + ["door"]:
            for h in range(24):
                lam = rate.get((h, key), 0.0)
                t = h * 3600.0
                while lam > 0:
                    t += rng.expovariate(lam)
                    if t >= (h + 1) * 3600:
                        break
                    s = int(t)
                    dur = rng.choice(on_dur[key]) if on_dur[key] else 5
                    if key == "door":
                        tr += [(s, "door_open", "entryway")]
                        if s + dur < DAY:
                            tr += [(s + dur, "door_close", "entryway")]
                    else:
                        tr += [(s, "motion", key)]
                        if s + dur < DAY:
                            tr += [(s + dur, "motion_off", key)]
        out[f"synth_d{k:02d}"] = _add_entry_presence(sorted(tr))
    return out


def shuffled_days(n_days: int = 30, seed: int = 11) -> Dict[str, Trace]:
    """Per-sensor day permutation of ARAS A: column c of shuffled day k comes
    from ARAS day perm_c[k]; marginals per sensor and time of day are kept."""
    raws, _, _ = _aras_a_stats()
    rng = random.Random(seed)
    cols = [HOUSE_A["door"]] + list(HOUSE_A["map"])
    perms = {c: rng.sample(range(1, 31), 30) for c in cols}
    out = {}
    for k in range(n_days):
        raw = []
        for c in cols:
            raw += [(s, cc, v) for (s, cc, v) in raws[perms[c][k]] if cc == c]
        out[f"shuf_d{k:02d}"] = _add_entry_presence(_raw_to_trace(sorted(raw), HOUSE_A))
    return out


def markov_days(n_days: int = 30, seed: int = 13) -> Dict[str, Trace]:
    """First-order Markov chain over activation symbols (rooms + 'door') fitted
    on ARAS A; inter-activation gaps sampled from ARAS A gaps of the same hour."""
    raws, _, on_dur = _aras_a_stats()
    trans = defaultdict(lambda: defaultdict(int))
    gaps = defaultdict(list)
    for raw in raws.values():
        seq = [(s, "door" if c == HOUSE_A["door"] else HOUSE_A["map"].get(c))
               for (s, c, v) in raw if v == 1 and (c == HOUSE_A["door"] or c in HOUSE_A["map"])]
        for (s0, a), (s1, b) in zip(seq, seq[1:]):
            trans[a][b] += 1
            gaps[s0 // 3600].append(s1 - s0)
    rng = random.Random(seed)
    syms = sorted(trans)
    out = {}
    for k in range(n_days):
        tr: Trace = []
        cur = rng.choice(syms)
        t = rng.choice(gaps[0]) if gaps[0] else 60
        while t < DAY:
            dur = rng.choice(on_dur[cur]) if on_dur[cur] else 5
            if cur == "door":
                tr += [(t, "door_open", "entryway")]
                if t + dur < DAY:
                    tr += [(t + dur, "door_close", "entryway")]
            else:
                tr += [(t, "motion", cur)]
                if t + dur < DAY:
                    tr += [(t + dur, "motion_off", cur)]
            nxt = trans[cur]
            tot = sum(nxt.values())
            r, acc = rng.random() * tot, 0
            for b, n in nxt.items():
                acc += n
                if r <= acc:
                    cur = b
                    break
            g = gaps[min(t // 3600, 23)] or [60]
            t += max(1, rng.choice(g))
        out[f"markov_d{k:02d}"] = _add_entry_presence(sorted(tr))
    return out


# ── LLM schedules ────────────────────────────────────────────────────────────
MOTION_INTERVAL = 30      # PIR re-trigger period while the resident is in a room
MOTION_ON = 10            # PIR on-time per trigger
DOOR_DWELL = 15           # door open time when leaving / arriving
NO_MOTION = {"SLEEP", "IDLE"}
OUT_OF_HOME = {"ERRANDS"}


ROOM_KEYWORDS = [("kitchen", "kitchen"), ("dining", "kitchen"), ("bath", "hallway"),
                 ("toilet", "hallway"), ("hall", "hallway"), ("corridor", "hallway"),
                 ("foyer", "entryway"), ("entry", "entryway"), ("bed", "bedroom"),
                 ("living", "living_room"), ("lounge", "living_room"), ("family", "living_room"),
                 ("office", "living_room"), ("study", "living_room")]
OUTDOOR_WORDS = ("outside", "outdoor", "garden", "yard", "park", "gym", "store", "shop",
                 "market", "street", "work", "campus", "school", "hospital", "cafe", "garage")


def _map_room(room: str):
    r = room.lower().replace(" ", "_")
    if r in ROOMS:
        return r
    if any(w in r for w in OUTDOOR_WORDS):
        return None
    for kw, target in ROOM_KEYWORDS:
        if kw in r:
            return target
    return "living_room"


DEPART_WORDS = ("commute to", "leave for", "leave home", "head out", "go to work", "drive to")
RETURN_WORDS = ("commute home", "return home", "arrive home", "come home", "back home")
TRIP_WORDS = ("grocery", "shopping", "errand", "walk", "jog", "gym", "appointment", "class",
              "outing", "visit")


def _door(tr: Trace, s: int):
    if s < DAY:
        tr.append((s, "door_open", "entryway"))
        if s + DOOR_DWELL < DAY:
            tr.append((s + DOOR_DWELL, "door_close", "entryway"))


def schedule_to_trace(tasks: List[dict], works_from_home: bool = True) -> Trace:
    """Schedule tasks [{name, category, room, start_s, duration_s}] -> trace.

    The schedule generator is offered only the five home rooms, so departures
    are recognized from the task name, applied identically to every model:
      departure words  -> door opens at task start, no in-home motion during;
      return words     -> door opens at task end, no in-home motion during;
      round-trip words, ERRANDS, or an outdoor room -> door at start and end.
    WORK of a persona that does not work from home is spent away; it opens the
    door at start and end only if the schedule has no explicit commute task.
    In-home tasks: room activation at start and every MOTION_INTERVAL s."""
    tr: Trace = []
    names = [str(t.get("name", "")).lower() for t in tasks]
    has_commute = any(any(w in n for w in DEPART_WORDS + RETURN_WORDS) for n in names)
    for t, name in zip(tasks, names):
        cat = str(t["category"]).upper()
        start, dur = int(t["start_s"]), max(60, int(t["duration_s"]))
        room = _map_room(str(t.get("room", "")))
        if any(w in name for w in DEPART_WORDS):
            _door(tr, start)
            continue
        if any(w in name for w in RETURN_WORDS):
            _door(tr, start + dur)
            continue
        if any(w in name for w in TRIP_WORDS) or cat in OUT_OF_HOME or room is None:
            _door(tr, start)
            _door(tr, start + dur)
            continue
        if cat == "WORK" and not works_from_home:
            if not has_commute:
                _door(tr, start)
                _door(tr, start + dur)
            continue
        if cat in NO_MOTION:
            continue
        s = start
        while s < start + dur and s < DAY:
            tr += [(s, "motion", room)]
            if s + MOTION_ON < DAY:
                tr += [(s + MOTION_ON, "motion_off", room)]
            s += MOTION_INTERVAL
    return _add_entry_presence(sorted(tr))


def llm_days(schedule_dir: Path) -> Dict[str, Trace]:
    out = {}
    for f in sorted(Path(schedule_dir).glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("tasks"):
            out[f"llm_{f.stem}"] = schedule_to_trace(d["tasks"], d.get("works_from_home", True))
    return out


def load_source(name: str, llm_dir: Path | None = None) -> Dict[str, Trace]:
    if name == "aras_a":
        return aras_days("A")
    if name == "aras_b":
        return aras_days("B")
    if name == "synth":
        return synthetic_days()
    if name == "shuffled":
        return shuffled_days()
    if name == "markov":
        return markov_days()
    if name == "llm":
        return llm_days(llm_dir)
    raise ValueError(name)
