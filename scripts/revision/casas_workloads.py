#!/usr/bin/env python3
"""
CASAS "HH" smart homes (hh101..hh130) -> e1v2 day traces.

INGESTION ONLY.  This module builds traces in exactly the format consumed by
the paired replay harness (see e1v2_workloads.py): a sorted list of
(second_of_day:int, kind:str, room:str) with kind in
{"motion", "motion_off", "door_open", "door_close"}.  It runs no attack
replay and computes no attack outcomes; those belong to a preregistered study.

Input format (data/datasets/casas/data/hhNNN.csv), one message per line:
    "YYYY-MM-DD HH:MM:SS.ffffff <Location> <State>"
The per-sensor IDs of the original CASAS release were already collapsed to a
location label in these files, so a location can carry several physical
sensors and several sensor types.  Sensor type is recovered from the state
vocabulary (evidence: results/revision/casas/room_map.csv):
    ON / OFF       PIR motion (area) sensors              -> used
    OPEN / CLOSE   door / cabinet contacts                -> only OutsideDoor used
    0..100         light/dimmer level messages            -> ignored
    TAP*, DEPRESS, RELEASE, HOLD_*   push buttons          -> ignored

Conversion (mirrors e1v2_workloads._raw_to_trace for ARAS):
  * door_open / door_close come ONLY from the OutsideDoor OPEN/CLOSE contact;
    the door room label is "entryway" as in ARAS.  Kitchen/Bathroom/Bedroom
    OPEN/CLOSE contacts are interior (fridge/cabinet/room doors: <=3% of their
    closes are followed by 30 min of whole-home silence vs 11-34% for
    OutsideDoor) and are dropped.
  * Contact bounce: a CLOSE followed by an OPEN less than DOOR_DEBOUNCE_S
    later is removed (the door stays open), then repeated identical door
    states are collapsed.  ARAS is sampled at 1 Hz, so sub-second bounces are
    invisible there; CASAS logs them with microsecond stamps.
  * Each motion ON -> "motion", each OFF -> "motion_off", room-mapped, with no
    room-level de-duplication: ARAS emits one transition per physical sensor
    and several sensors share a room, so repeated room-level "motion" without
    an intervening "motion_off" is expected there too.  In CASAS, repeated ON
    at one location occurs only at locations with several physical sensors
    (single-sensor locations alternate ON/OFF exactly).
  * Entry presence is REAL: OutsideDoor ON/OFF is an entry-area motion sensor
    (present in every HH home).  No inferred presence is added (unlike ARAS).
  * Day = calendar date of the local timestamp; events outside the day are not
    carried over and no synthetic events are created at midnight (as ARAS:
    a door open at midnight shows up as a lone door_close the next day).
  * Exact duplicate lines are dropped; out-of-order lines are sorted.  Seconds
    are floored; when a later event of the same stream (the door, or one room)
    lands in the same second as an earlier one and tuple order would put it
    first (door_open->door_close, motion_off->motion), it is moved to the next
    second, so sorted() (tuple order, as used for ARAS) cannot reverse a
    stream.  Cross-stream order within one second is lost, as in ARAS.
  * Days are dropped by fixed, mapping-independent rules (DAY RULES below).

Room mapping (MAPPINGS): canonical rule rooms are e1v2_workloads.ROOMS.
Bathroom, DiningRoom, WorkArea and Office get NON-rule labels by default and
are kept in the trace; pass rule_rooms_only=True to drop them (the harness'
build_scene only creates devices for e1v2_workloads.ROOMS).

Public API
    list_homes(recompute=False) -> List[str]
    casas_days(home, max_days=None, start_day=0, mapping="default",
               rule_rooms_only=False) -> Dict[str, Trace]
    iter_casas_days(...)        same, lazily (large homes have >1M events)
    casas_day_dates(home)       day key -> ISO date
    day_records(home)           per calendar date: usable flag + drop reasons
    restrict_to_rule_rooms(trace)  drop non-rule rooms from a trace
"""
from __future__ import annotations

import bisect
import csv
import datetime as _dt
import functools
import sys
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e1v2_workloads import DAY, ROOMS, Trace  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CASAS_ROOT = REPO / "data/datasets/casas/data"
OUT_DIR = REPO / "results/revision/casas"
HH_HOMES = tuple(f"hh{n}" for n in range(101, 131))

LOCATIONS = ("OutsideDoor", "Hall", "Kitchen", "LivingRoom", "DiningRoom", "Chair",
             "WorkArea", "Office", "Bedroom", "Bathroom")
_LOC = {n: i for i, n in enumerate(LOCATIONS)}
ON, OFF, OPEN, CLOSE = 0, 1, 2, 3
_STATE = {"ON": ON, "OFF": OFF, "OPEN": OPEN, "CLOSE": CLOSE}

DOOR_LOCATION = "OutsideDoor"        # exterior door contact (OPEN/CLOSE)
ENTRY_LOCATION = "OutsideDoor"       # entry-area motion sensor (ON/OFF)
DOOR_DEBOUNCE_S = 2.0

# ── Room mappings ────────────────────────────────────────────────────────────
# Locations whose canonical room is unambiguous; identical in every mapping.
CORE_MAP = {"OutsideDoor": "entryway", "Hall": "hallway", "Kitchen": "kitchen",
            "LivingRoom": "living_room", "Bedroom": "bedroom"}
AUX_LOCATIONS = ("DiningRoom", "Chair", "WorkArea", "Office")
NON_RULE_LABELS = {"Bathroom": "bathroom", "DiningRoom": "dining_room",
                   "WorkArea": "work_area", "Office": "office"}
MAPPINGS = {
    # primary: Chair -> living_room (co-activates with LivingRoom in most homes);
    # DiningRoom/WorkArea/Office are ambiguous across homes -> own non-rule labels.
    "default": {**CORE_MAP, "Chair": "living_room", "Bathroom": "bathroom",
                "DiningRoom": "dining_room", "WorkArea": "work_area", "Office": "office"},
    # sensitivity: every auxiliary living area counts as living room.
    "all_living": {**CORE_MAP, "Chair": "living_room", "Bathroom": "bathroom",
                   "DiningRoom": "living_room", "WorkArea": "living_room",
                   "Office": "living_room"},
    # "adjacency" is resolved per home: each auxiliary location goes to the
    # canonical room it most often co-activates with (consecutive ONs at two
    # locations <= ADJ_WINDOW_S apart, usable days only).
}
ADJ_WINDOW_S = 10.0

# ── Day rules (fixed before any CASAS outcome is computed) ──────────────────
DAYTIME = (7 * 3600, 23 * 3600)      # window in which silence is suspicious
SILENCE_MAX_S = 4 * 3600             # > 4 h without any binary-sensor message ...
BRACKET_S = 600                      # ... unless an exterior-door message lies within
                                     # 10 min before its start AND after its end
RUN_DAYS = 3                         # >= 3 consecutive calendar days silent -> dead sensor
DOOR_FAULT_OPEN_S = 20 * 3600        # raw door state OPEN > 20 h of a day -> faulty contact
COVERAGE_MIN = 0.5                   # sensor "present" if active on >= 50% of days
CLOCK_JUMP_S = 60.0                  # backward jump in file order (DST fall-back)
QUALIFY_MIN_DAYS = 30
QUALIFY_MIN_ROOMS = 4
RULES_VERSION = "casas-v1"


# ── Parsing ──────────────────────────────────────────────────────────────────
@dataclass
class _HomeData:
    home: str
    dates: List[str]
    ordinal: Dict[str, int]
    t: Dict[str, array]          # seconds of day (float), sorted
    loc: Dict[str, array]        # location index
    st: Dict[str, array]         # ON/OFF/OPEN/CLOSE code
    n_lines: int = 0
    n_malformed: int = 0
    n_exact_dup: int = 0
    other_states: Counter = field(default_factory=Counter)   # (loc, "numeric"|"button") -> n
    unknown_locations: Counter = field(default_factory=Counter)
    clock_jump: Dict[str, float] = field(default_factory=dict)  # date -> max backward jump (s)


@functools.lru_cache(maxsize=1)
def _load_home(home: str) -> _HomeData:
    if home not in HH_HOMES:
        raise ValueError(f"{home!r} is not a CASAS HH home")
    buckets: Dict[str, Tuple[array, array, array]] = {}
    last_sec: Dict[str, float] = {}
    hd = _HomeData(home, [], {}, {}, {}, {})
    with open(CASAS_ROOT / f"{home}.csv") as fh:
        for line in fh:
            hd.n_lines += 1
            p = line.split()
            if len(p) != 4:
                hd.n_malformed += 1
                continue
            d, ts, loc, s = p
            li = _LOC.get(loc)
            if li is None:
                hd.unknown_locations[loc] += 1
                continue
            try:
                sec = int(ts[0:2]) * 3600 + int(ts[3:5]) * 60 + float(ts[6:])
            except ValueError:
                hd.n_malformed += 1
                continue
            prev = last_sec.get(d)
            if prev is not None and sec < prev:
                if prev - sec > hd.clock_jump.get(d, 0.0):
                    hd.clock_jump[d] = prev - sec
            last_sec[d] = sec
            sc = _STATE.get(s)
            if sc is None:
                hd.other_states[(loc, "numeric" if s.isdigit() else "button")] += 1
                continue
            b = buckets.get(d)
            if b is None:
                b = buckets[d] = (array("d"), array("B"), array("B"))
            b[0].append(sec)
            b[1].append(li)
            b[2].append(sc)
    for d in sorted(buckets):
        t, lo, st = buckets[d]
        order = sorted(range(len(t)), key=t.__getitem__)       # stable: file order on ties
        T, L, S = array("d"), array("B"), array("B")
        cur_t, seen = None, set()
        for i in order:
            if t[i] != cur_t:
                cur_t, seen = t[i], set()
            key = (lo[i], st[i])
            if key in seen:
                hd.n_exact_dup += 1
                continue
            seen.add(key)
            T.append(t[i]); L.append(lo[i]); S.append(st[i])
        hd.dates.append(d)
        hd.ordinal[d] = _dt.date.fromisoformat(d).toordinal()
        hd.t[d], hd.loc[d], hd.st[d] = T, L, S
    return hd


def _abs(ordinal: int, sec: float) -> float:
    return ordinal * float(DAY) + sec


# ── Exterior door stream ─────────────────────────────────────────────────────
@dataclass
class _DoorStream:
    events: List[Tuple[int, float, int]]   # (ordinal, sec, OPEN|CLOSE), debounced
    raw_abs: List[float]                    # every raw OutsideDoor OPEN/CLOSE (abs s)
    raw_code: List[int]                     # ... and its state code
    n_raw: int
    n_bounce_removed: int
    n_repeat_removed: int


def _debounce(raw: List[Tuple[int, float, int]], debounce_s: float):
    """raw: time-ordered (ordinal, sec, OPEN|CLOSE).  Remove CLOSE->OPEN pairs
    closer than debounce_s (contact bounce: the door stays open), then collapse
    repeated identical states.  Returns (events, n_bounce_removed, n_repeat_removed)."""
    kept, i, bounce = [], 0, 0
    while i < len(raw):
        o, s, c = raw[i]
        if (c == CLOSE and i + 1 < len(raw) and raw[i + 1][2] == OPEN
                and _abs(raw[i + 1][0], raw[i + 1][1]) - _abs(o, s) < debounce_s):
            i += 2
            bounce += 2
            continue
        kept.append(raw[i])
        i += 1
    out, rep = [], 0
    for e in kept:
        if out and out[-1][2] == e[2]:
            rep += 1
            continue
        out.append(e)
    return out, bounce, rep


@functools.lru_cache(maxsize=2)
def _door_stream(home: str, debounce_s: float = DOOR_DEBOUNCE_S) -> _DoorStream:
    hd = _load_home(home)
    di = _LOC[DOOR_LOCATION]
    raw = []
    for d in hd.dates:
        o, t, lo, st = hd.ordinal[d], hd.t[d], hd.loc[d], hd.st[d]
        for i in range(len(t)):
            if lo[i] == di and st[i] in (OPEN, CLOSE):
                raw.append((o, t[i], st[i]))
    out, bounce, rep = _debounce(raw, debounce_s)
    return _DoorStream(out, [_abs(o, s) for (o, s, _) in raw], [c for (_, _, c) in raw],
                       len(raw), bounce, rep)


# ── Day rules ────────────────────────────────────────────────────────────────
@dataclass
class DayRecord:
    date: str
    usable: bool
    reasons: Tuple[str, ...]
    key: Optional[str]            # casas_<home>_dNNNN for usable days


def _runs_of_silence(first_ord: int, last_ord: int, silent) -> set:
    """Ordinals inside runs of >= RUN_DAYS consecutive calendar days where
    silent(ordinal) is True (missing dates are passed in as silent)."""
    out, run = set(), []
    for o in range(first_ord, last_ord + 2):
        if o <= last_ord and silent(o):
            run.append(o)
        else:
            if len(run) >= RUN_DAYS:
                out.update(run)
            run = []
    return out


@functools.lru_cache(maxsize=4)
def _day_quality(home: str) -> Tuple[Tuple[DayRecord, ...], Dict[str, Dict[str, int]]]:
    """Per present calendar date: usable flag and drop reasons.  Uses raw
    messages only, so the selected days do not depend on the room mapping or
    on the door debounce.  Also returns per-date ON counts per location."""
    hd = _load_home(home)
    ds = _door_stream(home)
    door_abs = ds.raw_abs
    present = {hd.ordinal[d]: d for d in hd.dates}
    first, last = min(present), max(present)
    reasons: Dict[str, List[str]] = {d: [] for d in hd.dates}

    # R0 recording boundary (partial first/last day)
    reasons[hd.dates[0]].append("recording_boundary")
    if hd.dates[-1] != hd.dates[0]:
        reasons[hd.dates[-1]].append("recording_boundary")
    # R1 clock jump (DST fall-back repeats an hour in local time)
    for d, j in hd.clock_jump.items():
        if j > CLOCK_JUMP_S and d in reasons:
            reasons[d].append("clock_jump")

    # per-date counts
    on_counts: Dict[str, Dict[str, int]] = {}
    door_msgs: Dict[int, int] = {}
    spans = []          # (abs_start, abs_end) silences > SILENCE_MAX_S between messages
    prev = None
    for d in hd.dates:
        o, t, lo, st = hd.ordinal[d], hd.t[d], hd.loc[d], hd.st[d]
        c = Counter()
        nd = 0
        for i in range(len(t)):
            a = _abs(o, t[i])
            if prev is not None and a - prev > SILENCE_MAX_S:
                spans.append((prev, a))
            prev = a
            if st[i] == ON:
                c[LOCATIONS[lo[i]]] += 1
            elif lo[i] == _LOC[DOOR_LOCATION] and st[i] in (OPEN, CLOSE):
                nd += 1
        on_counts[d] = dict(c)
        door_msgs[o] = nd

    # R2 unexplained daytime silence
    for (a, b) in spans:
        i = bisect.bisect_left(door_abs, a - BRACKET_S)
        pre = i < len(door_abs) and door_abs[i] <= a
        j = bisect.bisect_left(door_abs, b)
        post = j < len(door_abs) and door_abs[j] <= b + BRACKET_S
        if pre and post:
            continue
        for o in range(int(a // DAY), int(b // DAY) + 1):
            if o not in present:
                continue
            lo_, hi_ = max(a, _abs(o, DAYTIME[0])), min(b, _abs(o, DAYTIME[1]))
            if hi_ - lo_ > SILENCE_MAX_S and "daytime_silence" not in reasons[present[o]]:
                reasons[present[o]].append("daytime_silence")

    # R3 exterior door contact silent for >= RUN_DAYS days
    for o in _runs_of_silence(first, last, lambda o: door_msgs.get(o, 0) == 0):
        if o in present:
            reasons[present[o]].append("door_sensor_silent")

    # R3b exterior door contact stuck/inverted: the RAW (undebounced) door state,
    # carried across midnight, is OPEN for > 20 h of the day.  (hh119 from
    # 2014-10-09 rests OPEN and emits only ~0.2 s CLOSE pulses.)
    open_s: Dict[int, float] = defaultdict(float)
    for k in range(len(door_abs) - 1):
        if ds.raw_code[k] != OPEN:
            continue
        x, b = door_abs[k], door_abs[k + 1]
        while x < b:
            o = int(x // DAY)
            e = min(b, (o + 1) * float(DAY))
            open_s[o] += e - x
            x = e
    for o, v in open_s.items():
        if v > DOOR_FAULT_OPEN_S and o in present:
            reasons[present[o]].append("door_contact_fault")

    # R4 a core room motion sensor that is normally active is silent >= RUN_DAYS days
    cand = [d for d in hd.dates if not reasons[d]]
    for locname in CORE_MAP:
        n_act = sum(1 for d in cand if on_counts[d].get(locname, 0) > 0)
        if not cand or n_act < COVERAGE_MIN * len(cand):
            continue
        dead = _runs_of_silence(first, last, lambda o, L=locname: o not in present
                                or on_counts[present[o]].get(L, 0) == 0)
        for o in dead:
            if o in present:
                reasons[present[o]].append(f"room_sensor_silent:{locname}")

    recs, k = [], 0
    for d in hd.dates:
        r = tuple(reasons[d])
        if not r:
            k += 1
        recs.append(DayRecord(d, not r, r, f"casas_{home}_d{k:04d}" if not r else None))
    return tuple(recs), on_counts


def day_records(home: str) -> List[DayRecord]:
    return list(_day_quality(home)[0])


def casas_day_dates(home: str) -> Dict[str, str]:
    """Usable day key -> ISO calendar date."""
    return {r.key: r.date for r in _day_quality(home)[0] if r.usable}


# ── Mapping resolution ───────────────────────────────────────────────────────
@functools.lru_cache(maxsize=4)
def _adjacency(home: str) -> Dict[str, Counter]:
    """loc -> Counter(neighbour loc) of consecutive ONs at two different
    locations <= ADJ_WINDOW_S apart, over usable days (both directions)."""
    hd = _load_home(home)
    nb: Dict[str, Counter] = defaultdict(Counter)
    for r in _day_quality(home)[0]:
        if not r.usable:
            continue
        t, lo, st = hd.t[r.date], hd.loc[r.date], hd.st[r.date]
        pt, pl = None, None
        for i in range(len(t)):
            if st[i] != ON:
                continue
            if pt is not None and lo[i] != pl and t[i] - pt <= ADJ_WINDOW_S:
                a, b = LOCATIONS[pl], LOCATIONS[lo[i]]
                nb[a][b] += 1
                nb[b][a] += 1
            pt, pl = t[i], lo[i]
    return dict(nb)


def resolve_mapping(home: str, mapping: Union[str, Dict[str, str]] = "default") -> Dict[str, str]:
    """CASAS location -> trace room label for one home."""
    if isinstance(mapping, dict):
        return dict(mapping)
    if mapping in MAPPINGS:
        return dict(MAPPINGS[mapping])
    if mapping != "adjacency":
        raise ValueError(f"unknown mapping {mapping!r}")
    m = {**CORE_MAP, "Bathroom": "bathroom"}
    nb = _adjacency(home)
    for aux in AUX_LOCATIONS:
        c = nb.get(aux)
        cands = [(n, loc) for loc, n in (c or {}).items() if loc in CORE_MAP]
        if cands:
            m[aux] = CORE_MAP[max(cands)[1]]
        else:
            m[aux] = NON_RULE_LABELS.get(aux, "living_room")
    return m


# ── Trace construction ───────────────────────────────────────────────────────
@dataclass
class _BuildStats:
    bumped: int = 0          # events moved by 1 s to keep same-stream order
    bumped_door: int = 0     # ... of which exterior-door events
    dropped_eod: int = 0     # events bumped past 23:59:59 (dropped)


def _door_by_date(home: str) -> Dict[int, List[Tuple[float, int]]]:
    out: Dict[int, List[Tuple[float, int]]] = defaultdict(list)
    for (o, s, c) in _door_stream(home).events:
        out[o].append((s, c))
    return out


def _build_trace(hd: _HomeData, date: str, door_events: List[Tuple[float, int]],
                 rmap: Dict[str, str], stats: Optional[_BuildStats] = None) -> Trace:
    t, lo, st = hd.t[date], hd.loc[date], hd.st[date]
    ev = []                                             # (sec, order, stream, kind)
    for i in range(len(t)):
        c = st[i]
        if c == ON or c == OFF:
            room = rmap.get(LOCATIONS[lo[i]])
            if room is not None:
                ev.append((t[i], i, room, "motion" if c == ON else "motion_off"))
    for (s, c) in door_events:
        ev.append((s, -1, "\0door", "door_open" if c == OPEN else "door_close"))
    ev.sort(key=lambda e: (e[0], e[1]))
    last: Dict[str, Tuple[int, str]] = {}
    out: Trace = []
    for (sec, _, stream, kind) in ev:
        s = int(sec)
        p = last.get(stream)
        if p is not None:                 # keep this stream's tuples non-decreasing
            if s < p[0]:
                s = p[0]
            if s == p[0] and kind < p[1]:  # e.g. door_close after door_open, same second
                s += 1
                if stats is not None:
                    stats.bumped += 1
                    stats.bumped_door += stream == "\0door"
        if s >= DAY:
            if stats is not None:
                stats.dropped_eod += 1
            continue
        last[stream] = (s, kind)
        out.append((s, kind, "entryway" if stream == "\0door" else stream))
    return sorted(out)


def restrict_to_rule_rooms(trace: Trace) -> Trace:
    """Drop events of rooms the e1v2 rule set does not know (bathroom, ...)."""
    keep = set(ROOMS)
    return [e for e in trace if e[2] in keep]


def iter_casas_days(home: str, max_days: Optional[int] = None, start_day: int = 0,
                    mapping: Union[str, Dict[str, str]] = "default",
                    rule_rooms_only: bool = False,
                    _stats: Optional[_BuildStats] = None) -> Iterator[Tuple[str, Trace]]:
    hd = _load_home(home)
    usable = [r for r in _day_quality(home)[0] if r.usable]
    sel = usable[start_day:] if max_days is None else usable[start_day:start_day + max_days]
    rmap = resolve_mapping(home, mapping)
    if rule_rooms_only:
        rmap = {k: v for k, v in rmap.items() if v in ROOMS}
    doors = _door_by_date(home)
    for r in sel:
        yield r.key, _build_trace(hd, r.date, doors.get(hd.ordinal[r.date], []), rmap, _stats)


def casas_days(home: str, max_days: Optional[int] = None, start_day: int = 0,
               mapping: Union[str, Dict[str, str]] = "default",
               rule_rooms_only: bool = False) -> Dict[str, Trace]:
    """Usable days of one HH home as e1v2 traces, keyed casas_<home>_dNNNN
    (NNNN = 1-based rank among the home's usable days, chronological, so keys
    do not depend on start_day/max_days).  start_day is 0-based into that list."""
    return dict(iter_casas_days(home, max_days, start_day, mapping, rule_rooms_only))


# ── Home qualification ───────────────────────────────────────────────────────
def home_summary(home: str) -> dict:
    """Cheap per-home facts used by list_homes (default mapping)."""
    recs, on_counts = _day_quality(home)
    usable = [r for r in recs if r.usable]
    hd = _load_home(home)
    doors = _door_by_date(home)
    n = len(usable)
    door_days = sum(1 for r in usable
                    if any(c == OPEN for (_, c) in doors.get(hd.ordinal[r.date], [])))
    rooms = {}
    for room in ROOMS:
        locs = [l for l, v in MAPPINGS["default"].items() if v == room]
        k = sum(1 for r in usable if any(on_counts[r.date].get(l, 0) > 0 for l in locs))
        rooms[room] = k / n if n else 0.0
    present = [r for r in ROOMS if rooms[r] >= COVERAGE_MIN]
    door_ok = n > 0 and door_days >= COVERAGE_MIN * n
    return dict(home=home, usable_days=n, door_contact_present=door_ok,
                rule_rooms_present=present, room_coverage=rooms,
                qualifies=door_ok and len(present) >= QUALIFY_MIN_ROOMS and n >= QUALIFY_MIN_DAYS)


def list_homes(recompute: bool = False) -> List[str]:
    """HH homes with an exterior door contact (door opening on >= 50% of usable
    days), >= 4 canonical rule rooms with motion on >= 50% of usable days, and
    >= 30 usable days.  Reads results/revision/casas/homes.csv when it was
    written by the same RULES_VERSION, otherwise recomputes (~2 min)."""
    f = OUT_DIR / "homes.csv"
    if not recompute and f.exists():
        with open(f) as fh:
            rows = list(csv.DictReader(fh))
        if rows and all(r.get("rules_version") == RULES_VERSION for r in rows):
            return [r["home"] for r in rows if r["qualifies"] == "True"]
    return [h for h in HH_HOMES if home_summary(h)["qualifies"]]


__all__ = ["casas_days", "iter_casas_days", "list_homes", "casas_day_dates", "day_records",
           "restrict_to_rule_rooms", "resolve_mapping", "home_summary", "MAPPINGS", "HH_HOMES",
           "DayRecord"]
