"""Routines from the earlier VESPER work's schedules (spec §5a): the 90 LLM persona schedules and the ARAS day plans.

Both name canonical rooms (living_room, kitchen, bedroom, bathroom, entryway, hallway), which `room_map` finds among
the home's rooms by name, falling back to the nearest kind of room when a home has none (a home without a living
room uses its hallway), and every fallback is said. A persona day is classified exactly as the earlier work did
(scripts/revision/e1v2_workloads.py): leaving and coming home by the task's words, trips and errands, and work away
from home. Tasks run in the order they start, each until the next begins; what is clipped or dropped is said.
"""
from __future__ import annotations

import json
import math

from vesper.lab.routine import Task, clock
from vesper.lab.worldmap import MapError

DAY_S = 86400
MIN_TASK_S = 60  # shorter tasks are dropped (persona) or merged into their neighbour (ARAS)
CANONICAL = ("living_room", "kitchen", "bedroom", "bathroom", "entryway", "hallway")
# a map room's name -> canonical room; the first keyword that matches wins
KEYWORDS = (("kitchen", "kitchen"), ("dining", "kitchen"), ("bath", "bathroom"), ("toilet", "bathroom"),
            ("shower", "bathroom"), ("entry", "entryway"), ("foyer", "entryway"), ("lobby", "entryway"),
            ("hall", "hallway"), ("corridor", "hallway"), ("bed", "bedroom"), ("living", "living_room"),
            ("lounge", "living_room"), ("family", "living_room"))
# a home without that kind of room uses the first of these it has (the earlier work folded bath into hallway)
FALLBACK = {"living_room": ("hallway", "entryway", "bedroom"), "entryway": ("hallway", "living_room"),
            "hallway": ("entryway", "living_room"), "bathroom": ("hallway", "bedroom"),
            "kitchen": ("living_room", "hallway"), "bedroom": ("living_room", "hallway")}
# the earlier work's words (e1v2_workloads.py)
DEPART_WORDS = ("commute to", "leave for", "leave home", "head out", "go to work", "drive to")
RETURN_WORDS = ("commute home", "return home", "arrive home", "come home", "back home")
TRIP_WORDS = ("grocery", "shopping", "errand", "walk", "jog", "gym", "appointment", "class", "outing", "visit")
OUT_OF_HOME = {"ERRANDS"}
OUTDOOR_WORDS = ("outside", "outdoor", "garden", "yard", "park", "gym", "store", "shop", "market", "street", "work",
                 "campus", "school", "hospital", "cafe", "garage")
# ARAS (README of House A and B): activity id -> (name, category, canonical room; None = away; "" = where it was)
ARAS = {1: ("Other", "IDLE", ""), 2: ("Going Out", "AWAY", None),
        3: ("Preparing Breakfast", "HOUSEHOLD", "kitchen"), 4: ("Having Breakfast", "EATING", "kitchen"),
        5: ("Preparing Lunch", "HOUSEHOLD", "kitchen"), 6: ("Having Lunch", "EATING", "kitchen"),
        7: ("Preparing Dinner", "HOUSEHOLD", "kitchen"), 8: ("Having Dinner", "EATING", "kitchen"),
        9: ("Washing Dishes", "HOUSEHOLD", "kitchen"), 10: ("Having Snack", "EATING", "kitchen"),
        11: ("Sleeping", "SLEEP", "bedroom"), 12: ("Watching TV", "LEISURE", "living_room"),
        13: ("Studying", "WORK", "living_room"), 14: ("Having Shower", "HYGIENE", "bathroom"),
        15: ("Toileting", "HYGIENE", "bathroom"), 16: ("Napping", "SLEEP", "bedroom"),
        17: ("Using Internet", "LEISURE", "living_room"), 18: ("Reading Book", "LEISURE", "living_room"),
        19: ("Laundry", "HOUSEHOLD", "bathroom"), 20: ("Shaving", "HYGIENE", "bathroom"),
        21: ("Brushing Teeth", "HYGIENE", "bathroom"), 22: ("Talking on the Phone", "SOCIAL", "living_room"),
        23: ("Listening to Music", "LEISURE", "living_room"), 24: ("Cleaning", "HOUSEHOLD", "living_room"),
        25: ("Having Conversation", "SOCIAL", "living_room"), 26: ("Having Guest", "SOCIAL", "living_room"),
        27: ("Changing Clothes", "HYGIENE", "bedroom")}


class ConvertError(ValueError):
    """A schedule VESPER cannot turn into a routine for this home; the message says what to give."""


def canonical(name: str) -> str | None:
    n = name.lower().replace("_", " ").replace("/", " ").replace("-", " ")
    return next((c for kw, c in KEYWORDS if kw in n), None)


def room_map(world_map, overrides: dict | None = None) -> tuple[dict, list[str]]:
    """Canonical room -> the home's room id, and a note per mapping (fallbacks say so)."""
    indoor = [r for r in world_map.rooms if not r.outdoor]
    found: dict = {}
    for r in indoor:
        c = canonical(r.label) or canonical(r.id)
        if c is not None and c not in found:
            found[c] = r.id
    rooms, notes = {}, []
    for c, room in (overrides or {}).items():
        if c not in CANONICAL:
            raise ConvertError(f"--room {c}={room}: {c!r} is not one of {', '.join(CANONICAL)}")
        try:
            world_map.room(room)
        except MapError as e:
            raise ConvertError(f"--room {c}={room}: {e}") from None
        rooms[c] = room
        notes.append(f"{c} -> {room} (given)")
    for c in CANONICAL:
        if c in rooms:
            continue
        if c in found:
            rooms[c] = found[c]
            notes.append(f"{c} -> {found[c]}")
            continue
        alt = next((found[f] for f in FALLBACK[c] if f in found), None)
        if alt is not None:
            rooms[c] = alt
            notes.append(f"{c} -> {alt} (no {c.replace('_', ' ')} on this map)")
    return rooms, notes


def _room(rooms: dict, c: str, where: str) -> str:
    if c not in rooms:
        raise ConvertError(f"{where}: no room on this map for {c}; give one with --room {c}=<room>")
    return rooms[c]


def from_persona(schedule: dict, world_map, overrides: dict | None = None) -> tuple[list[Task], list[str]]:
    """A persona day (results/revision/llm_schedules/*.json) as tasks for this home, and notes."""
    rooms, notes = room_map(world_map, overrides)
    raw = sorted(schedule.get("tasks") or [], key=lambda t: int(t["start_s"]))
    wfh = bool(schedule.get("works_from_home", True))
    names = [str(t.get("name", "")).lower() for t in raw]
    commute = any(any(w in n for w in DEPART_WORDS + RETURN_WORDS) for n in names)
    items, away = [], None  # items: [start, end, name, category, room]; away: an open (start, name)
    for t, low in zip(raw, names):
        name, cat = str(t.get("name", "")).strip(), str(t["category"]).upper()
        start, end = int(t["start_s"]), int(t["start_s"]) + int(t["duration_s"])
        room_raw = str(t.get("room", "")).lower().replace(" ", "_")
        c = room_raw if room_raw in CANONICAL else canonical(room_raw)
        outdoors = c is None or any(w in room_raw for w in OUTDOOR_WORDS)
        if any(w in low for w in DEPART_WORDS):
            if away is None:
                away = (start, name)
            continue
        if any(w in low for w in RETURN_WORDS):
            if away is not None:
                items.append([away[0], end, f"away: {away[1]} .. {name}", "AWAY", None])
                away = None
            continue
        if any(w in low for w in TRIP_WORDS) or cat in OUT_OF_HOME or outdoors:
            items.append([start, end, f"away: {name}", "AWAY", None])
            continue
        if cat == "WORK" and not wfh:
            if not commute:
                items.append([start, end, f"away: {name}", "AWAY", None])
            continue
        if away is not None:  # in-home task while away with no return: the resident came home
            items.append([away[0], start, f"away: {away[1]}", "AWAY", None])
            notes.append(f"{away[1]}: no return; home again for {name} at {clock(start)}")
            away = None
        items.append([start, end, name, cat if cat != "AWAY" else "IDLE", _room(rooms, c, name)])
    if away is not None:
        items.append([away[0], DAY_S, f"away: {away[1]}", "AWAY", None])
    return _sequence(items, notes), notes


def _sequence(items: list, notes: list[str]) -> list[Task]:
    """In start order, each until the next begins; under a minute dropped; the day's end respected."""
    items = sorted(items, key=lambda i: i[0])
    out = []
    for k, (start, end, name, cat, room) in enumerate(items):
        if start >= DAY_S:
            notes.append(f"{name}: starts after midnight ({clock(start)}); dropped")
            continue
        nxt = next((i[0] for i in items[k + 1:] if i[0] < DAY_S), None)
        if nxt is not None and nxt < end:
            notes.append(f"{name}: clipped at {clock(nxt)} (the next task begins)")
            end = nxt
        if end - start < MIN_TASK_S:
            notes.append(f"{name}: under a minute; dropped")
            continue
        out.append([start, end, name, cat, room])
    if len(out) > 1 and out[-1][1] > out[0][0] + DAY_S:  # the last task overruns tomorrow's first
        notes.append(f"{out[-1][2]}: clipped at {clock(out[0][0])} (tomorrow's first task)")
        out[-1][1] = out[0][0] + DAY_S
    return [Task(start=float(s), duration=float(e - s), name=n, category=c, room=r) for s, e, n, c, r in out]


def from_aras(rows, resident: int, world_map, overrides: dict | None = None) -> tuple[list[Task], list[str]]:
    """One ARAS day (DAY_n.txt: one row per second, 20 sensors, then resident 1's and 2's activity) as tasks."""
    if resident not in (1, 2):
        raise ConvertError(f"--resident: {resident} is not 1 or 2")
    rooms, notes = room_map(world_map, overrides)
    col = 19 + resident
    runs: list[list] = []  # [activity, start, seconds]
    for t, row in enumerate(rows):
        a = int(row[col])
        if a not in ARAS:
            raise ConvertError(f"row {t + 1}: activity {a} is not an ARAS activity (1–27)")
        if runs and runs[-1][0] == a:
            runs[-1][2] += 1
        else:
            runs.append([a, t, 1])
    merged: list[list] = []
    for a, start, n in runs:
        if n < MIN_TASK_S and merged:
            notes.append(f"{ARAS[a][0]} ({n} s at {clock(start)}): merged into {ARAS[merged[-1][0]][0]}")
            merged[-1][2] += n
        elif merged and merged[-1][0] == a:
            merged[-1][2] += n
        else:
            merged.append([a, start, n])
    if len(merged) > 1 and merged[0][2] < MIN_TASK_S:
        notes.append(f"{ARAS[merged[0][0]][0]} ({merged[0][2]} s at 00:00): merged into {ARAS[merged[1][0]][0]}")
        merged[1][1], merged[1][2] = 0, merged[1][2] + merged[0][2]
        merged.pop(0)
    if len(merged) > 1 and merged[0][0] == merged[-1][0] and merged[0][1] == 0:  # one task across midnight
        merged[-1][2] += merged[0][2]
        merged.pop(0)
        notes.append(f"{ARAS[merged[-1][0]][0]}: runs across midnight, one task")
    tasks, here = [], None
    for a, start, n in merged:
        name, cat, c = ARAS[a]
        if c == "":
            room = here or _room(rooms, "living_room", name)
        else:
            room = None if c is None else _room(rooms, c, name)
        tasks.append(Task(start=float(start), duration=float(n), name=name, category=cat, room=room))
        here = room if room is not None else here
    return tasks, notes


def minutes(seconds: float) -> float | int:
    """A task's duration as a routine file gives it: whole minutes stay whole; else rounded down at 4 decimals
    (< 6 ms), and a step further down if the decimal reads back a hair longer than the task (249 s is 4.15 min,
    which reads back as 249.00000000000003 s): a task never runs into the next."""
    if seconds % 60 == 0:
        return int(seconds // 60)
    m = math.floor(seconds / 60 * 1e4) / 1e4
    while float(repr(m)) * 60 > seconds:
        m = round(m - 1e-4, 4)
    return m


def routine_yaml(tasks: list[Task], source: str, notes: list[str] = ()) -> str:
    """A routine file (version 1) with its source and the conversion's notes as comments."""
    lines = [f"# from: {source}"] + [f"# {n}" for n in notes] + ["version: 1", "tasks:"]
    for t in tasks:
        fields = [f"at: {json.dumps(clock(t.start))}", f"minutes: {minutes(t.duration)}", f"name: {json.dumps(t.name)}",
                  f"category: {t.category}"] + ([f"room: {t.room}"] if t.room else [])
        lines.append("  - {" + ", ".join(fields) + "}")
    return "\n".join(lines) + "\n"
