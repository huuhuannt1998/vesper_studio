"""A resident's routine (spec §5a): a day's tasks, each at a time, for a while, in a room or away from home.

    version: 1
    tasks:
      - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
      - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
      - {at: "08:00", minutes: 240, name: work, category: AWAY}

The categories are the earlier persona schedules' (vesper/simulation/task_generator.py), plus AWAY: out of the
home. Tasks are in order and do not overlap; a gap is time spent where the last task left the resident. A task
may run past midnight (sleep). Every mistake names the task and the field.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from vesper.lab.worldmap import MapError

CATEGORIES = ("SLEEP", "HYGIENE", "EATING", "WORK", "IDLE", "SOCIAL", "EXERCISE", "HOUSEHOLD", "LEISURE", "AWAY")


class RoutineError(ValueError):
    """A routine VESPER cannot follow; the message names the task and the field."""


@dataclass(frozen=True)
class Task:
    start: float  # seconds after midnight
    duration: float  # seconds
    name: str
    category: str
    room: str | None  # None: away from home

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass(frozen=True)
class Routine:
    name: str
    tasks: tuple[Task, ...]


def clock(seconds: float) -> str:
    s = int(round(seconds)) % 86400
    return f"{s // 3600:02d}:{s // 60 % 60:02d}" + (f":{s % 60:02d}" if s % 60 else "")


def _time(value, where: str) -> float:
    try:
        parts = [int(p) for p in str(value).split(":")]
        if len(parts) not in (2, 3):
            raise ValueError
        h, m, s = parts + [0] * (3 - len(parts))
        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
            raise ValueError
    except ValueError:
        raise RoutineError(f"{where}: {value!r} is not a time of day (HH:MM or HH:MM:SS)") from None
    return h * 3600 + m * 60 + s


def load_routine(path: Path, world_map) -> Routine:
    path = Path(path)
    try:
        spec = yaml.safe_load(path.read_text())
    except OSError as e:
        raise RoutineError(f"{path}: cannot read it: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise RoutineError(f"{path.name}: not valid YAML: {e}") from e
    if not isinstance(spec, dict) or spec.get("version") != 1:
        raise RoutineError("version: a routine file is version 1")
    raw = spec.get("tasks")
    if not isinstance(raw, list):
        raise RoutineError("tasks: a routine is a list of tasks")
    tasks: list[Task] = []
    for k, t in enumerate(raw):
        where = f"tasks[{k}]"
        if not isinstance(t, dict):
            raise RoutineError(f"{where}: a task is a mapping such as {{at: '07:00', minutes: 30, ...}}")
        start = _time(t.get("at"), f"{where}.at")
        minutes = t.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int | float) or not math.isfinite(minutes) \
                or minutes <= 0:
            raise RoutineError(f"{where}.minutes: {minutes!r} is not a positive number of minutes")
        name = t.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RoutineError(f"{where}.name: name the task")
        category = t.get("category")
        if category not in CATEGORIES:
            raise RoutineError(f"{where}.category: {category!r} is not one of {', '.join(CATEGORIES)}")
        room = t.get("room")
        if category == "AWAY":
            if room not in (None, "away"):
                raise RoutineError(f"{where}.room: an AWAY task is out of the home; it names no room")
            room = None
        else:
            if not isinstance(room, str):
                raise RoutineError(f"{where}.room: name the room (or make the task AWAY)")
            try:
                if world_map.room(room).outdoor:
                    raise RoutineError(f"{where}.room: {room!r} is outdoors; an outdoor task is AWAY")
            except MapError as e:
                raise RoutineError(f"{where}.room: {e}") from e
        if tasks and start < tasks[-1].end:
            raise RoutineError(f"{where}.at: {clock(start)} is before the end of tasks[{k - 1}] "
                               f"({clock(tasks[-1].end)})")
        tasks.append(Task(start=start, duration=float(minutes) * 60, name=name.strip(), category=category,
                          room=room))
    return Routine(path.stem, tuple(tasks))
