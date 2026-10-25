# Plan W3a: residents who live a day in the Lab — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Lab home's scripted residents live their routine: they walk room to room, use the devices their activities need, react to the house (a dark room, a locked door, an alarm), sometimes forget a light or the lock, and say who did what and why. `vesper lab day` lives a whole day on the logical clock in seconds, and the day replays exactly.

**Architecture:**
1. A routine file (`vesper/lab/routine.py`) is a day's tasks: a time, a duration, a name, a category and a room, or AWAY. A profile (`vesper/lab/profile.py`) holds the habits (the chances of forgetting a light or the lock), the comfort (when a room is too dark), the bedtime, and which device categories each activity uses.
2. The home file names its residents (`vesper/lab/home.py`), each with an avatar, a routine file and a profile; every mistake names the resident and the field.
3. The resident engine (`vesper/lab/resident.py`) acts only as the player does, through the session: `walk_to` and `interact`, within reach and on the same side of the wall, so the sensors and the rules cannot tell a resident from the player. Every action carries `why` (resident, task, reason); a decision not to act ("forgot the light") is a `resident` record. Randomness comes from the home's seed and the resident's name.
4. The session starts the home's residents. A replay lives their day again instead of re-applying their actions, from the time of day the trace started, and checks that they did the same things: a resident's action and a rule (the ambient tick) at the same instant then fall into the clock's order exactly as they did.
5. `vesper lab day` runs the residents on the logical clock (`--from`, `--hours`), writes the trace, and summarises each resident's day. A page that joins a session late gets its recent console, where a resident's decisions have their own line. The example home gets a resident, Alex, and a weekday.

**Tech Stack:** Python ≥3.11 (standard library `random`, `collections`); the Lab of W2a–W2c; Node's test runner for the page's JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, approved by the PI on 2026-10-24 (RKA `jrn_01M5QSGDYGE158FRVPVWKN0QNZ`). Covered here:
- §0 success criterion 6: a scripted resident lives a full day from its routine and reactions, using the devices, and the day replays exactly from its seed;
- §4 the home file's `residents` (name, avatar, profile, routine);
- §5a routines (written routine files; a walk to the task's room at 1.1 m/s, on the map), the activity → device table, the reactions (a dark room, a locked door when leaving or coming in, an alarm), profiles with the home's seed, several residents, and the ground truth (`why`);
- §5b scripted sessions without Home Assistant on the logical clock, a 24-hour day in seconds.

Not here (Plan W3b): the converters from the earlier work's schedules (the 90 persona schedules, the ARAS day plans), the thermostat and comfort band, and VESPER's built-in rule engine for the home's automations on the logical clock.

## Global Constraints

- No new dependency.
- A resident acts only through the session's `walk_to` and `interact`, as the player does; it never sets a twin or the world directly.
- Every resident action is an `input` record with `why` = {resident, task, reason}; every decision not to act is a `resident` record.
- A day replays exactly: the same inputs, the same reports, in the same order (`vesper lab replay` says `identical`).
- Nothing fails silently: a home without residents, a time of day that is not one, a routine room that is not on the map, and automations that do not run without Home Assistant are each named.
- Shared Mac (another project's scored run until about 2026-10-27): unit tests only, under `nice -n 19` / `taskpolicy -b`; no Home Assistant, no browser, no Habitat.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **A resident's action and a rule at the same instant** (a task on the minute and the ambient tick) replay in the same order. Pinned by `test_the_home_s_residents_enter_with_the_session_and_a_replay_lives_their_day_again` (Task 3) and `test_a_day_lives_the_residents_and_replays_exactly` (Task 4).
2. **Leaving a room someone else is still in leaves its light on;** the last one out switches it off. Pinned by `test_leaving_a_room_someone_else_is_in_leaves_its_light_on` (Task 3).
3. **A day that starts at night, inside a task that runs past midnight,** finds the resident in bed, with no light switched on. Pinned by `test_a_day_that_starts_at_night_finds_the_resident_in_bed_from_the_night_before` (Task 3).
4. **A day cut short in the middle of a walk** still replays exactly. Pinned by `test_a_day_can_start_later_and_stop_in_the_middle_of_a_walk_and_still_replay_exactly` (Task 4).
5. **A routine edited after the trace was made:** the replay names the first resident action that changed, not only a device report. Pinned by `test_a_replay_lives_the_residents_again_and_names_an_action_that_changed` (Task 4).

## Rulings carried into this plan

- **A replay lives the home's residents again instead of re-applying their actions.** Re-applied, a resident's action and a rule scheduled at the same instant can swap order, and the replay then differs from the day it replays. Re-run, they are deterministic (routine, profile, the home's seed) and fall into the clock's order as before; the replay compares their actions first. Residents not in the home file (a script's) are still re-applied. Cost if wrong: a replay of a day whose routine file changed since reports the first changed action rather than reproducing the old day; the trace still holds what happened.
- **Reactions read what a person in the room would see** (a light's state, a lock's state, a smoke sensor's report), never Home Assistant's state, as §5a says.
- **The daylight model decides "too dark"** (the rules' ambient model, below the profile's `dark_lux`, default 50 lx); sleep never switches a light on, and going to bed switches the bedroom's lights off.
- **A day without Home Assistant does not run the home's automations,** and says so; VESPER's rule engine for them is Plan W3b. Cost if wrong: a day of the example home shows no automation turning the hall lamp on until W3b.

---
### Task 1: Routines and profiles

**Files:**
- Create: `vesper/lab/routine.py`, `vesper/lab/profile.py`
- Create tests: `tests/lab/test_routine.py`, `tests/lab/test_profile.py`

**Interfaces:**
- Consumes: `WorldMap.room(id)` and `MapError` (`vesper/lab/worldmap.py`, W2a).
- Produces:
  - in `vesper/lab/routine.py`: `CATEGORIES`; `RoutineError`; `Task(start, duration, name, category, room)` (seconds after midnight; `room` None when AWAY) with `.end`; `Routine(name, tasks)`; `clock(seconds) -> "HH:MM[:SS]"`; `_time(value, where) -> seconds`; `load_routine(path, world_map) -> Routine` (the name is the file's stem);
  - in `vesper/lab/profile.py`: `DEFAULT_USES`; `ProfileError`; `Profile(forget_light=0.2, forget_lock=0.1, dark_lux=50.0, bedtime=72000, uses=DEFAULT_USES)`; `load_profile(spec, where) -> Profile` (`None` or `default` gives the defaults).

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_routine.py`:
```python
import textwrap

import pytest

from tests.lab.maps import house
from vesper.lab.routine import CATEGORIES, RoutineError, Task, load_routine

DAY = """\
    version: 1
    tasks:
      - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
      - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
      - {at: "08:00", minutes: 240, name: work, category: AWAY}
      - {at: "12:00:30", minutes: 0.5, name: a glass of water, category: EATING, room: kitchen}
"""


def write(tmp_path, text):
    path = tmp_path / "alex.yaml"
    path.write_text(textwrap.dedent(text))
    return path


def test_a_routine_is_a_day_of_tasks_in_rooms_or_away(tmp_path):
    r = load_routine(write(tmp_path, DAY), house())
    assert r.name == "alex"
    assert r.tasks[0] == Task(start=7 * 3600, duration=1800, name="wake up", category="SLEEP", room="bedroom")
    assert r.tasks[2].room is None and r.tasks[2].category == "AWAY"
    assert (r.tasks[3].start, r.tasks[3].duration) == (12 * 3600 + 30, 30)
    assert "AWAY" in CATEGORIES and "SLEEP" in CATEGORIES


@pytest.mark.parametrize("old, new, message", [
    ("room: kitchen}\n      - {at: \"08:00\"", "room: kitchn}\n      - {at: \"08:00\"",
     r"tasks\[1\]\.room: unknown room 'kitchn'; did you mean kitchen"),
    ("category: EATING, room: kitchen}\n      - {at: \"08:00\"", "category: COOKING, room: kitchen}\n"
     "      - {at: \"08:00\"", r"tasks\[1\]\.category: 'COOKING' is not one of"),
    ("{at: \"07:30\", minutes: 20", "{at: \"07:20\", minutes: 20", r"tasks\[1\]\.at: 07:20 is before the end of "
                                                                    r"tasks\[0\] \(07:30\)"),
    ("{at: \"07:00\"", "{at: \"25:00\"", r"tasks\[0\]\.at: '25:00' is not a time of day"),
    ("minutes: 30", "minutes: 0", r"tasks\[0\]\.minutes: 0 is not a positive number of minutes"),
    ("name: wake up, category: SLEEP, room: bedroom", "name: wake up, category: SLEEP, room: porch",
     r"tasks\[0\]\.room: 'porch' is outdoors; an outdoor task is AWAY"),
    ("version: 1", "version: 2", r"version: a routine file is version 1"),
])
def test_mistakes_in_a_routine_name_the_task_and_the_field(tmp_path, old, new, message):
    text = DAY.replace(old, new, 1)
    assert text != DAY
    with pytest.raises(RoutineError, match=message):
        load_routine(write(tmp_path, text), house())


def test_a_routine_that_is_not_a_list_of_tasks_is_refused(tmp_path):
    with pytest.raises(RoutineError, match="tasks: a routine is a list of tasks"):
        load_routine(write(tmp_path, "version: 1\ntasks: {at: '07:00'}\n"), house())
    with pytest.raises(RoutineError, match="not valid YAML"):
        load_routine(write(tmp_path, "version: 1\ntasks: [\n"), house())
```

Create `tests/lab/test_profile.py`:
```python
"""A resident's profile: habits, comfort, and the devices an activity uses."""
import pytest

from vesper.lab.profile import ProfileError, load_profile

H = 3600


@pytest.mark.parametrize("bad, message", [({"forget_light": 1.5}, "forget_light: 1.5 is not a probability"),
                                          ({"dark_lux": -1}, "dark_lux: -1 is not"),
                                          ({"bedtime": "26:00"}, "bedtime: '26:00' is not a time of day"),
                                          ({"uses": {"COOKING": ["plug"]}}, "uses: 'COOKING' is not one of"),
                                          ({"snore": True}, "unknown setting 'snore'")])
def test_a_profile_names_its_mistakes(bad, message):
    with pytest.raises(ProfileError, match=message):
        load_profile(bad, "residents[0].profile")


def test_a_profile_has_sensible_defaults():
    p = load_profile(None, "residents[0].profile")
    assert (p.forget_light, p.forget_lock, p.dark_lux, p.bedtime) == (0.2, 0.1, 50.0, 20 * H)
    assert p.uses["EATING"] == ("plug",) and load_profile({"forget_light": 0}, "x").forget_light == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_routine.py tests/lab/test_profile.py -q 2>&1 | tail -1`

Expected: `2 errors`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.routine'` (and `'vesper.lab.profile'`)

- [ ] **Step 3: Implement**

Create `vesper/lab/routine.py`:
```python
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
```

Create `vesper/lab/profile.py`:
```python
"""A resident's profile (spec §5a): the habits, the comfort, and which devices an activity uses.

    profile: {forget_light: 0.2, forget_lock: 0.1, dark_lux: 50, bedtime: "20:00",
              uses: {EATING: [plug], LEISURE: [plug]}}

- forget_light: leaving a room, the chance of leaving its light on;
- forget_lock: leaving home or going to bed, the chance of not locking the front door;
- dark_lux: a room is too dark for an activity below this daylight (the rules' assumed ambient model);
- bedtime: a SLEEP task that starts after this is going to bed;
- uses: per task category, the device categories the task switches on while it lasts and off after
  ("cooking uses the kitchen plug").
Every value has a default; randomness comes from the home's seed, so a day replays exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from vesper.lab.routine import CATEGORIES, RoutineError, _time

DEFAULT_USES = {"EATING": ("plug",), "HOUSEHOLD": ("plug",), "LEISURE": ("plug",), "WORK": ("plug",)}
SETTINGS = ("forget_light", "forget_lock", "dark_lux", "bedtime", "uses")


class ProfileError(ValueError):
    """A profile VESPER cannot use; the message names the setting."""


@dataclass(frozen=True)
class Profile:
    forget_light: float = 0.2
    forget_lock: float = 0.1
    dark_lux: float = 50.0
    bedtime: float = 20 * 3600  # seconds after midnight
    uses: dict = field(default_factory=lambda: dict(DEFAULT_USES))


def load_profile(spec, where: str) -> Profile:
    if spec is None or spec == "default":
        return Profile()
    if not isinstance(spec, dict):
        raise ProfileError(f"{where}: a profile is a mapping of settings, or `default`")
    for key in spec:
        if key not in SETTINGS:
            raise ProfileError(f"{where}: unknown setting {key!r}; the settings are {', '.join(SETTINGS)}")
    values = {}
    for key in ("forget_light", "forget_lock"):
        if key in spec:
            v = spec[key]
            if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 1:
                raise ProfileError(f"{where}.{key}: {v!r} is not a probability (0 to 1)")
            values[key] = float(v)
    if "dark_lux" in spec:
        v = spec["dark_lux"]
        if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
            raise ProfileError(f"{where}.dark_lux: {v!r} is not an illuminance in lux (0 or more)")
        values["dark_lux"] = float(v)
    if "bedtime" in spec:
        try:
            values["bedtime"] = _time(spec["bedtime"], f"{where}.bedtime")
        except RoutineError as e:
            raise ProfileError(str(e)) from None
    if "uses" in spec:
        uses = spec["uses"]
        if not isinstance(uses, dict):
            raise ProfileError(f"{where}.uses: a mapping of task category to device categories")
        out = {}
        for category, devices in uses.items():
            if category not in CATEGORIES:
                raise ProfileError(f"{where}.uses: {category!r} is not one of {', '.join(CATEGORIES)}")
            if not isinstance(devices, list) or not all(isinstance(d, str) for d in devices):
                raise ProfileError(f"{where}.uses.{category}: a list of device categories, such as [plug]")
            out[category] = tuple(devices)
        values["uses"] = out
    return Profile(**values)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab/test_routine.py tests/lab/test_profile.py -q 2>&1 | tail -1`

Expected: `15 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/routine.py vesper/lab/profile.py tests/lab/test_routine.py tests/lab/test_profile.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): routines and profiles: a resident's day, habits, comfort, and the devices an activity uses

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: Residents in the home file

**Files:**
- Modify: `vesper/lab/home.py` (`ResidentSpec`, `_residents`; `LabHome.residents` becomes a list of `ResidentSpec`)
- Tests: append to `tests/lab/test_home.py`

**Interfaces:**
- Consumes: `load_routine`, `RoutineError` (Task 1); `load_profile`, `ProfileError`, `Profile` (Task 1).
- Produces in `vesper/lab/home.py`: `ResidentSpec(name, model, routine, profile)` (frozen; the avatar `model` defaults to `male_0`); `LabHome.residents: list[ResidentSpec]`, the routine path relative to the home file. Errors: `residents[k].name: …`, `residents[k].routine: …` (the routine's own message after it), `residents[k].profile.<setting>: …`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/lab/test_home.py`:
```python


ALEX = """\
version: 1
tasks:
  - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
  - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
"""
WITH_ALEX = HOME + """\
    residents:
      - {name: alex, avatar: male_1, routine: routines/alex.yaml, profile: {forget_light: 0.3}}
"""


def test_residents_come_with_their_routine_and_profile(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX)
    home = load(tmp_path, WITH_ALEX)
    (alex,) = home.residents
    assert (alex.name, alex.model, alex.profile.forget_light) == ("alex", "male_1", 0.3)
    assert [t.name for t in alex.routine.tasks] == ["wake up", "breakfast"]


@pytest.mark.parametrize("change, message", [
    (("routines/alex.yaml", "routines/bob.yaml"), r"residents\[0\]\.routine: .*bob\.yaml: cannot read it"),
    (("name: alex", "name: player"), r"residents\[0\]\.name: 'player' is the player's name"),
    (("name: alex", "name: front_door"), r"residents\[0\]\.name: 'front_door' is a device's name"),
    (("forget_light: 0.3", "forget_light: 3"), r"residents\[0\]\.profile\.forget_light: 3 is not a probability"),
    (("{name: alex, avatar", "{avatar"), r"residents\[0\]\.name: name the resident"),
])
def test_a_resident_s_mistakes_are_named(tmp_path, change, message):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX)
    with pytest.raises(HomeError, match=message):
        load(tmp_path, WITH_ALEX.replace(*change))


def test_a_routine_that_does_not_fit_the_map_is_named_through_the_resident(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX.replace("room: kitchen", "room: attic"))
    with pytest.raises(HomeError, match=r"residents\[0\]\.routine: tasks\[1\]\.room: unknown room 'attic'"):
        load(tmp_path, WITH_ALEX)


def test_two_residents_may_not_share_a_name(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(ALEX)
    twice = WITH_ALEX + "      - {name: alex, routine: routines/alex.yaml}\n"
    with pytest.raises(HomeError, match=r"residents\[1\]\.name: 'alex' is used twice"):
        load(tmp_path, twice)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_home.py -q 2>&1 | tail -1`

Expected: `8 failed, 30 passed`: the residents are still the home file's raw mappings (no `.name`), and no resident's mistake is named

- [ ] **Step 3: Implement**

In `vesper/lab/home.py`, replace:
```python
from vesper.lab.geometry import Point, centroid, distance, point_in_polygon
from vesper.lab.worldmap import MapError, WorldMap, suggest
```
with:
```python
from vesper.lab.geometry import Point, centroid, distance, point_in_polygon
from vesper.lab.profile import Profile, ProfileError, load_profile
from vesper.lab.routine import Routine, RoutineError, load_routine
from vesper.lab.worldmap import MapError, WorldMap, suggest
```

In `vesper/lab/home.py`, replace:
```python

@dataclass
```
with:
```python

@dataclass(frozen=True)
class ResidentSpec:
    """A resident of the home (spec §5a): who, how they look, the day they live, and their habits."""
    name: str
    model: str
    routine: Routine
    profile: Profile


@dataclass
```

In `vesper/lab/home.py`, replace:
```python
    doors: dict[str, bool]  # doorway id -> open at the start
    residents: list[dict] = field(default_factory=list)
    automations: Path | None = None
```
with:
```python
    doors: dict[str, bool]  # doorway id -> open at the start
    residents: list[ResidentSpec] = field(default_factory=list)
    automations: Path | None = None
```

In `vesper/lab/home.py`, replace:
```python
                   avatar={"model": avatar.get("model", "female_0"), "start": spawn_point(world_map, start)},
                   devices=devices, doors=doors, residents=list(spec.get("residents") or []),
                   automations=path.parent / automations if automations else None,
```
with:
```python
                   avatar={"model": avatar.get("model", "female_0"), "start": spawn_point(world_map, start)},
                   devices=devices, doors=doors,
                   residents=_residents(spec.get("residents") or [], path, world_map, devices),
                   automations=path.parent / automations if automations else None,
```

In `vesper/lab/home.py`, replace:
```python
                   matter_interface=spec.get("matter_interface"))
```
with:
```python
                   matter_interface=spec.get("matter_interface"))


def _residents(raw, path: Path, world_map: WorldMap, devices: list[PlacedDevice]) -> list[ResidentSpec]:
    out: list[ResidentSpec] = []
    for k, r in enumerate(_list(raw, "residents")):
        where = f"residents[{k}]"
        r = _mapping(r, where)
        name = r.get("name")
        if not isinstance(name, str) or not topic_safe(name):
            raise HomeError(f"{where}.name: name the resident (letters, digits, '_' or '-')")
        if name == "player":
            raise HomeError(f"{where}.name: 'player' is the player's name")
        if any(d.name == name for d in devices):
            raise HomeError(f"{where}.name: {name!r} is a device's name")
        if any(s.name == name for s in out):
            raise HomeError(f"{where}.name: {name!r} is used twice")
        routine = r.get("routine")
        if not isinstance(routine, str):
            raise HomeError(f"{where}.routine: the resident's routine file, such as routines/{name}.yaml")
        try:
            day = load_routine(path.parent / routine, world_map)
            profile = load_profile(r.get("profile"), f"{where}.profile")
        except RoutineError as e:
            raise HomeError(f"{where}.routine: {e}") from e
        except ProfileError as e:
            raise HomeError(str(e)) from e
        out.append(ResidentSpec(name, _text(r.get("avatar", "male_0"), f"{where}.avatar"), day, profile))
    return out
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `193 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/home.py tests/lab/test_home.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): a home's residents: each with an avatar, a routine file and a profile, and every mistake named

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The resident engine; the session starts the residents and a replay lives their day again

**Files:**
- Create: `vesper/lab/resident.py`
- Modify: `vesper/lab/session.py` (`start(residents=)`, `why` on `walk_to` and `interact`, `note`, `device_name`; `replay` from the trace's start time, living the home's residents again; `compare_reports` compares the residents' actions first)
- Modify: `vesper/lab/rules.py` (`Rules.operable`)
- Create tests: `tests/lab/test_resident.py`

**Interfaces:**
- Consumes: `ResidentSpec`, `LabHome.residents` (Task 2); `Profile`, `Routine`, `Task` (Task 1); `Session`, `replay`, `compare_reports`, `WALL_TOLERANCE_S` (W2a/W2b); `Rules.reachable`, `Rules.twin`, `ambient_at`, `is_on`, `is_locked` (W2a); `spawn_point` (W2a).
- Produces:
  - in `vesper/lab/resident.py`: `Resident(session, name, model, routine, profile=None)` with `start()`; `ALARMS`, `RETRY_S`, `NEAR_M`;
  - in `vesper/lab/session.py`: `Session.start(spawn_player=True, residents=True)`; `walk_to(point, actor, why=None)`; `interact(target, actor, why=None)`; `note(record)` (a `resident` record); `device_name(twin_id)`; `replay` skips the inputs of the home's residents and lives them again; `compare_reports` returns `resident action k: …` or `the trace has N resident actions, the replay M (…)` before any report difference;
  - in `vesper/lab/rules.py`: `Rules.operable(name) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_resident.py`:
```python
"""Scripted residents (spec §5a), on the logical clock in the test house:
hall [0.1, 2.9] x [0, 6], kitchen [3.1, 7] x [0, 2.9], bedroom [3.1, 7] x [3.1, 6], porch outside the front door."""
import copy
import dataclasses
from types import SimpleNamespace

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.profile import Profile
from vesper.lab.resident import Resident
from vesper.lab.routine import Routine, Task
from vesper.lab.rules import is_locked, is_on
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock

PLUG = ('      - {product: "zigbee:acme:plug-1", name: kitchen_plug, at: {room: kitchen, xy: [4.0, 0.5], '
        'mount: floor}}\n')
H = 3600


def task(at, minutes, name, category, room=None):
    h, m = (int(x) for x in at.split(":"))
    return Task(start=h * H + m * 60, duration=minutes * 60, name=name, category=category, room=room)


def lab(tmp_path, tasks, start="07:00", profile=None, extra="", residents=1, cat=None):
    text = HOME.replace('start_time: "07:30"', f'start_time: "{start}"') + extra
    home = load_lab_home(write_home(tmp_path, text), cat or catalog())
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    people = [Resident(s, f"r{k}", "male_0", Routine(f"r{k}", tuple(tasks[k] if residents > 1 else tasks)),
                       profile or Profile()) for k in range(residents)]
    for p in people:
        p.start()
    h, m = (int(x) for x in start.split(":"))
    return SimpleNamespace(s=s, people=people, home=home, room=lambda name="r0": s.world.pose(name).room,
                           at=lambda hh_mm: s.clock.advance_to(
                               int(hh_mm[:2]) * H + int(hh_mm[3:]) * 60 - (h * H + m * 60)),
                           on=lambda d: is_on(s.rules.twin(d)), locked=lambda d: is_locked(s.rules.twin(d)))


def inputs(s, actor="r0"):
    return [r for r in s.records if r["kind"] == "input" and r["actor"] == actor]


def test_a_resident_follows_the_routine_room_by_room(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"),
                       task("07:30", 20, "breakfast", "EATING", "kitchen"),
                       task("08:00", 30, "read the paper", "LEISURE", "hall")])
    assert t.room() == "bedroom"  # where the day's task in progress is
    t.at("07:32")
    assert t.room() == "kitchen"
    t.at("08:02")
    assert t.room() == "hall"
    walks = [r for r in inputs(t.s) if r["op"] == "walk_to"]
    assert [r["why"]["task"] for r in walks] == ["breakfast", "read the paper"]


def test_cooking_uses_the_kitchen_plug_and_turns_it_off_after(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"),
                       task("07:30", 20, "breakfast", "EATING", "kitchen")], extra=PLUG)
    t.at("07:35")
    assert t.on("kitchen_plug") is True
    t.at("07:55")
    assert t.on("kitchen_plug") is False
    uses = [r for r in inputs(t.s) if r["op"] == "interact"]
    assert [(r["args"]["target"], r["why"]["reason"]) for r in uses] == [("kitchen_plug", "breakfast"),
                                                                         ("kitchen_plug", "breakfast is over")]


def test_a_dark_room_gets_its_light_and_leaving_turns_it_off(tmp_path):
    t = lab(tmp_path, [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING",
                                                                              "kitchen")], start="20:59")
    t.at("21:02")
    assert t.on("bedroom_lamp") is True
    t.at("21:33")
    assert t.on("bedroom_lamp") is False and t.room() == "kitchen"
    reasons = [r["why"]["reason"] for r in inputs(t.s) if r["op"] == "interact"]
    assert reasons == ["a dark room", "leaving the room"]


def test_a_forgetful_resident_leaves_the_light_on_and_the_trace_says_so(tmp_path):
    t = lab(tmp_path, [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING",
                                                                              "kitchen")],
            start="20:59", profile=Profile(forget_light=1.0))
    t.at("21:33")
    assert t.on("bedroom_lamp") is True
    assert any(r["kind"] == "resident" and r["decision"] == "forgot the light" and r["device"] == "bedroom_lamp"
               for r in t.s.records)


def test_going_to_bed_locks_the_front_door_unless_forgotten(tmp_path):
    day = [task("22:00", 30, "tv", "LEISURE", "hall"), task("22:30", 480, "sleep", "SLEEP", "bedroom")]
    t = lab(tmp_path, day, start="21:59")
    t.at("22:40")
    assert t.locked("front_lock") is True and t.room() == "bedroom"
    (tmp_path / "f").mkdir()
    forgetful = lab(tmp_path / "f", day, start="21:59", profile=Profile(forget_lock=1.0))
    forgetful.at("22:40")
    assert forgetful.locked("front_lock") is False


def test_leaving_home_and_coming_back_go_through_the_front_door(tmp_path):
    t = lab(tmp_path, [task("08:00", 10, "coffee", "EATING", "kitchen"), task("08:10", 60, "work", "AWAY"),
                       task("09:10", 20, "lunch prep", "HOUSEHOLD", "kitchen")], start="07:59")
    t.at("08:20")
    assert t.room() == "porch" and t.s.world.doors["hall-porch"] is False and t.locked("front_lock") is True
    t.at("09:15")
    assert t.room() == "kitchen" and t.s.world.doors["hall-porch"] is False
    reasons = [r["why"]["reason"] for r in inputs(t.s) if r["op"] == "interact"]
    assert reasons[:3] == ["leaving home", "leaving home", "leaving home"]  # open, close, lock behind
    assert "coming home" in reasons


def test_two_residents_live_side_by_side_and_the_sensors_see_both(tmp_path):
    a = [task("07:00", 60, "breakfast", "EATING", "kitchen")]
    b = [task("07:00", 60, "tidy up", "HOUSEHOLD", "hall")]
    t = lab(tmp_path, [a, b], residents=2)
    t.at("07:10")
    assert (t.room("r0"), t.room("r1")) == ("kitchen", "hall")
    seen = {r["actor"] for r in t.s.records if r["kind"] == "rule" and r["rule"] == "presence"}
    assert "r1" in seen


def test_leaving_a_room_someone_else_is_in_leaves_its_light_on(tmp_path):
    a = [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING", "kitchen")]
    b = [task("21:00", 60, "read", "LEISURE", "bedroom"), task("22:00", 10, "tea", "EATING", "kitchen")]
    t = lab(tmp_path, [a, b], start="20:59", residents=2, profile=Profile(forget_light=0.0))
    t.at("21:33")
    assert t.room("r0") == "kitchen" and t.on("bedroom_lamp") is True  # r1 is still reading
    t.at("22:03")
    assert t.room("r1") == "kitchen" and t.on("bedroom_lamp") is False  # the last one out switches it off


def test_a_day_that_starts_at_night_finds_the_resident_in_bed_from_the_night_before(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "breakfast", "EATING", "kitchen"),
                       task("22:30", 480, "sleep", "SLEEP", "bedroom")], start="03:00")
    assert t.room() == "bedroom" and t.on("bedroom_lamp") is False
    t.at("07:02")
    assert t.room() == "kitchen"
    assert not [r for r in t.s.records if r["kind"] == "input" and r["op"] == "interact"]  # no light at 03:00


def test_every_resident_action_says_who_and_why(tmp_path):
    t = lab(tmp_path, [task("08:00", 10, "coffee", "EATING", "kitchen"), task("08:10", 60, "work", "AWAY"),
                       task("21:00", 30, "read", "LEISURE", "bedroom"), task("22:30", 480, "sleep", "SLEEP",
                                                                              "bedroom")],
            start="07:59", extra=PLUG)
    t.at("23:00")
    acts = [r for r in t.s.records if r["kind"] == "input" and r["op"] in ("walk_to", "interact")]
    assert acts and all(r["why"]["resident"] == r["actor"] == "r0" and r["why"]["reason"] for r in acts)


def test_a_resident_s_day_replays_exactly_without_the_resident(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"), task("07:30", 20, "breakfast", "EATING",
                                                                              "kitchen"),
                       task("08:00", 60, "errand", "AWAY"), task("21:00", 30, "read", "LEISURE", "bedroom"),
                       task("22:30", 480, "sleep", "SLEEP", "bedroom")], start="06:59", extra=PLUG,
            profile=Profile(forget_light=0.5, forget_lock=0.5))
    t.at("23:30")
    t.s.stop()
    again = replay(t.s.records, t.home)
    assert compare_reports(t.s.records, again) is None
    assert len([r for r in again if r["kind"] == "input"]) == len([r for r in t.s.records if r["kind"] == "input"])


def test_a_smoke_alarm_sends_the_residents_out(tmp_path):
    cat = catalog()
    smoke = copy.deepcopy(cat["zigbee:acme:pir-1"])
    smoke["id"], smoke["identity"]["category"] = "zigbee:acme:smoke-1", "smoke_sensor"
    smoke["features"][0].update(name="smoke", property="smoke", ha="binary_sensor/smoke")
    cat[smoke["id"]] = smoke
    extra = '      - {product: "zigbee:acme:smoke-1", name: kitchen_smoke, at: {room: kitchen, mount: ceiling}}\n'
    t = lab(tmp_path, [task("07:00", 120, "breakfast", "EATING", "kitchen")], extra=extra, cat=cat)
    t.at("07:05")
    t.s.runtime.stimulate(t.s.twin_ids["kitchen_smoke"], {"type": "measured", "feature": "smoke", "value": True})
    t.at("07:08")
    assert t.room() == "porch"
    assert any(r["kind"] == "input" and r.get("why", {}).get("reason") == "an alarm" for r in t.s.records)


def test_going_to_bed_switches_the_bedroom_light_off_and_sleep_never_switches_it_on(tmp_path):
    t = lab(tmp_path, [task("21:00", 60, "read", "LEISURE", "bedroom"), task("22:30", 480, "sleep", "SLEEP",
                                                                               "bedroom")], start="20:59")
    t.at("21:10")
    assert t.on("bedroom_lamp") is True  # reading in the dark
    t.at("22:35")
    assert t.on("bedroom_lamp") is False
    (tmp_path / "b").mkdir()
    late = lab(tmp_path / "b", [task("23:00", 30, "tv", "LEISURE", "hall"), task("23:30", 480, "sleep", "SLEEP",
                                                                                  "bedroom")], start="22:59")
    late.at("23:40")
    assert late.room() == "bedroom" and late.on("bedroom_lamp") is False


MORNING = """\
version: 1
tasks:
  - {at: "07:00", minutes: 30, name: breakfast, category: EATING, room: kitchen}
  - {at: "07:30", minutes: 30, name: read, category: LEISURE, room: bedroom}
"""


def test_the_home_s_residents_enter_with_the_session_and_a_replay_lives_their_day_again(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(MORNING)
    home = load_lab_home(write_home(tmp_path, HOME + "    residents:\n      - {name: alex, routine: "
                                                      "routines/alex.yaml}\n"), catalog())
    s = Session(dataclasses.replace(home, start_time=6 * H + 59 * 60), clock=LogicalClock())  # the file says 07:30
    s.start(spawn_player=False)
    s.clock.advance(H)
    s.stop()
    assert s.world.pose("alex").room == "bedroom"
    again = replay(s.records, home)
    lived = [r for r in again if r["kind"] == "input" and r["actor"] == "alex"]
    assert [r["op"] for r in lived].count("spawn") == 1  # lived again, not lived and re-applied
    assert lived == [r for r in s.records if r["kind"] == "input" and r["actor"] == "alex"]
    assert compare_reports(s.records, again) is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_resident.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.resident'`

- [ ] **Step 3: Implement**

Create `vesper/lab/resident.py`:
```python
"""Scripted residents (spec §5a): people who live a routine in the home and react to it.

A resident acts only as the player does: it walks, and it uses doors and devices within reach, on the same
side of the wall. So the sensors and the rules cannot tell it from the player. Each of its actions is traced
with who did it and why (`why`: resident, task, reason), the ground truth an attack's success and a defense's
alarm are judged against (spec §6, D2). Decisions not to act ("forgot the light") are traced too.

What it does, from its routine and its profile:
- at each task's time it goes to the task's room; there it switches on what the task uses (a plug while
  cooking) and switches it off when the task is over;
- a room too dark for the task (the rules' daylight model, no light on) gets its light;
- leaving a room, it switches the room's lights off, unless it forgets (the profile's chance) or someone else
  is still there;
- going to bed (a SLEEP task after the profile's bedtime) it locks the front door, unless it forgets, and
  switches the bedroom's lights off; sleep never switches a light on;
- leaving home (an AWAY task) it goes out through the front door, closes it and locks it behind, unless it
  forgets; coming home it unlocks, opens and closes it again;
- an alarm (a smoke or gas sensor reporting) sends it out of the home.
Reactions read what a person in the room would see (a light on or off), never Home Assistant's state.
"""
from __future__ import annotations

import random
from collections import deque

from vesper.lab.geometry import distance
from vesper.lab.home import spawn_point
from vesper.lab.profile import Profile
from vesper.lab.rules import ambient_at, is_locked, is_on

ALARMS = {("smoke_sensor", "smoke"), ("gas_sensor", "gas")}
RETRY_S = 2.0  # waiting for a doorway someone stands in
NEAR_M = 0.4  # how far from a device a resident stands to use it


class Resident:
    def __init__(self, session, name: str, model: str, routine, profile: Profile | None = None):
        self.session, self.name, self.model, self.routine = session, name, model, routine
        self.profile = profile or Profile()
        self.home, self.world, self.rules = session.home, session.world, session.rules
        self.rng = random.Random(f"{self.home.seed}:{name}")
        self._steps: deque = deque()
        self._epoch = 0  # a new task or an alarm replaces what was under way
        self._waiting = False
        self._task: int | None = None
        self._task_devices: list[tuple[str, str]] = []  # (device, task name): what the task switched on
        self._alarm = False
        self.world.on_event(self._on_world)
        session.runtime.subscribe(self._on_report)

    # -- the day ------------------------------------------------------------------------------
    def start(self) -> None:
        start = self.home.start_time
        self.session.spawn(self.name, self.model, self._spot(self._room_at(start)))
        for k, t in enumerate(self.routine.tasks):
            if t.start >= start:
                self.session.clock.call_at(t.start - start, lambda k=k: self._begin(k))
            elif t.end > start:
                self._task = k  # in progress: the resident is already there
            if t.end >= start:
                self.session.clock.call_at(t.end - start, lambda k=k: self._end(k))

    def _room_at(self, when: float) -> str:
        tasks = self.routine.tasks
        now = [t for t in tasks if t.start <= when < t.end]
        before = [t for t in tasks if t.start <= when]
        chosen = now[0] if now else before[-1] if before else tasks[-1] if tasks else None  # else yesterday's last
        if chosen is not None and chosen.room is None:
            return self._outdoors() or self._indoor_rooms()[0]
        return chosen.room if chosen is not None else self._indoor_rooms()[0]

    def _begin(self, k: int) -> None:
        task = self.routine.tasks[k]
        self._task = k
        self._epoch += 1
        self._steps.clear()
        self._waiting = False
        steps = [("use", d, False, f"{name} is over") for d, name in self._task_devices]
        self._task_devices = []
        if task.room is None:
            if not self._outside():
                steps += self._leave_home("leaving home")
        else:
            if self._outside():
                steps += self._come_home()
            bedtime = task.category == "SLEEP" and task.start >= self.profile.bedtime
            if bedtime:
                steps.append(("bedtime_lock",))
            steps += [("leave_room", task.room), ("walk_room", task.room, task.name)]
            if bedtime:
                steps.append(("lights_out", task.room))  # going to bed: the lights go off
            elif task.category != "SLEEP":
                steps.append(("dark", task.room))
            steps.append(("devices", task))
        self._steps.extend(steps)
        self._next()

    def _end(self, k: int) -> None:
        tasks = self.routine.tasks
        if k + 1 < len(tasks) and tasks[k + 1].start <= tasks[k].end + 1e-6:
            return  # the next task begins now, and switches this one's devices off first
        self._steps.extend(("use", d, False, f"{name} is over") for d, name in self._task_devices)
        self._task_devices = []
        self._next()

    # -- the steps ----------------------------------------------------------------------------
    def _next(self) -> None:
        epoch = self._epoch
        while self._steps and not self._waiting and epoch == self._epoch:
            self._run(self._steps.popleft())

    def _later(self, seconds: float) -> None:
        epoch, clock = self._epoch, self.session.clock
        self._waiting = True

        def resume():
            if epoch == self._epoch:
                self._waiting = False
                self._next()
        clock.call_at(clock.now() + seconds, resume)

    def _run(self, step: tuple) -> None:
        kind = step[0]
        if kind == "walk_room":
            if self._here() != step[1]:  # already in the room: no walk to its middle
                self._walk(self._spot(step[1]), step[2])
        elif kind == "walk_point":
            self._walk(step[1], step[2], tried=step[3] if len(step) > 3 else False)
        elif kind == "use":
            self._use(*step[1:])
        elif kind == "door":
            self._door(*step[1:])
        elif kind == "leave_room":
            self._lights_off_here(step[1])
        elif kind == "dark":
            self._dark(step[1])
        elif kind == "lights_out":
            for light in reversed(self._lights(step[1])):
                self._steps.appendleft(("use", light, False, "bedtime"))
        elif kind == "devices":
            self._devices(step[1])
        elif kind == "bedtime_lock":
            self._bedtime_lock()
        elif kind == "lock_behind":
            self._lock_behind(step[1], step[2])

    def _walk(self, point, reason: str, tried: bool = False) -> None:
        if self.session.walk_to(point, actor=self.name, why=self._why(reason)) is None:
            if tried:
                self.session.note({"resident": self.name, "decision": "could not get there", "reason": reason,
                                   "point": list(point)})
                return
            # a closed door in the way: open this room's doors, then try again
            self._steps.appendleft(("walk_point", point, reason, True))
            for door in self._closed_doors_here():
                for s in reversed(self._through(door, self._here(), reason, close=False)[:-1]):
                    self._steps.appendleft(s)
            return
        self._waiting = True

    def _on_world(self, event: dict) -> None:
        if event.get("actor") == self.name and event["event"] in ("arrived", "blocked") and self._waiting:
            epoch, clock = self._epoch, self.session.clock

            def resume():
                if epoch == self._epoch:
                    self._waiting = False
                    self._next()
            clock.call_at(clock.now(), resume)

    def _use(self, device: str, want: bool, reason: str, tried: bool = False) -> None:
        if self._state(device) == want:
            return
        target = self._operator(device)
        if target is None:
            self.session.note({"resident": self.name, "decision": "cannot switch it by hand", "device": device})
            return
        if not self._in_reach(target):
            if tried:
                self.session.note({"resident": self.name, "decision": "could not reach it", "device": target})
                return
            self._steps.appendleft(("use", device, want, reason, True))
            self._steps.appendleft(("walk_point", self._near(target), reason))
            return
        self.session.interact(target, actor=self.name, why=self._why(reason))

    def _door(self, door: str, want_open: bool, reason: str, tried: bool = False) -> None:
        if self.world.doors.get(door) == want_open:
            return
        outcome = self.session.interact(door, actor=self.name, why=self._why(reason))
        if outcome["ok"] or tried:
            return
        if outcome["result"] == "locked":
            self._steps.appendleft(("door", door, want_open, reason, True))
            self._steps.appendleft(("use", self._lock_of(door), False, reason))
        elif outcome["result"] == "obstructed":
            self._steps.appendleft(("door", door, want_open, reason, True))
            self._later(RETRY_S)
        else:  # out of reach: step to this side of it first
            self._steps.appendleft(("door", door, want_open, reason, True))
            self._steps.appendleft(("walk_point", self._side(door, self._here()), reason))

    # -- what the resident does ---------------------------------------------------------------
    def _lights_off_here(self, going_to: str) -> None:
        here = self._here()
        if here is None or here == going_to:
            return
        for light in self._lights(here):
            if not self._state(light) or self._others_in(here):
                continue
            if self.rng.random() < self.profile.forget_light:
                self.session.note({"resident": self.name, "decision": "forgot the light", "device": light,
                                   "room": here})
            else:
                self._steps.appendleft(("use", light, False, "leaving the room"))

    def _dark(self, room: str) -> None:
        lights = self._lights(room)
        if not lights or any(self._state(d) for d in lights):
            return
        daylight = ambient_at(self.home.start_time + self.session.clock.now(), 0)["illuminance"]
        if daylight < self.profile.dark_lux:
            self._steps.appendleft(("use", lights[0], True, "a dark room"))

    def _devices(self, task) -> None:
        for category in self.profile.uses.get(task.category, ()):
            for name, d in self._devices_in(task.room):
                if d.record["identity"]["category"] == category and self._operator(name) is not None:
                    self._task_devices.append((name, task.name))
                    self._steps.append(("use", name, True, task.name))
                    break

    def _bedtime_lock(self) -> None:
        door = self._front_door()
        lock = self._lock_of(door) if door else None
        if lock is None or self._state(lock):
            return
        if self.rng.random() < self.profile.forget_lock:
            self.session.note({"resident": self.name, "decision": "forgot to lock the front door", "device": lock})
            return
        inside = self._indoor_side(door)
        self._steps.appendleft(("use", lock, True, "bedtime"))
        self._steps.appendleft(("walk_point", self._side(door, inside), "bedtime"))

    def _lock_behind(self, door: str, reason: str) -> None:
        lock = self._lock_of(door)
        if lock is None:
            return
        if self.rng.random() < self.profile.forget_lock:
            self.session.note({"resident": self.name, "decision": "forgot to lock the front door", "device": lock})
            return
        self._steps.appendleft(("use", lock, True, reason))

    def _leave_home(self, reason: str, lock: bool = True) -> list[tuple]:
        door, out = self._front_door(), self._outdoors()
        if door is None or out is None:
            return [("walk_room", out, reason)] if out else []
        steps = [("leave_room", out)] + self._through(door, self._indoor_side(door), reason)
        if lock:
            steps.append(("lock_behind", door, reason))
        return steps + [("walk_room", out, reason)]

    def _come_home(self) -> list[tuple]:
        door, out = self._front_door(), self._outdoors()
        return self._through(door, out, "coming home") if door and out else []

    def _through(self, door: str, from_room: str, reason: str, close: bool = True) -> list[tuple]:
        """Walk to this side of the door, unlock and open it, go through, and close it behind."""
        dw = self.home.map.doorway(door)
        to_room = next((r for r in dw.rooms if r != from_room), from_room)
        steps = [("walk_point", self._side(door, from_room), reason)]
        lock = self._lock_of(door)
        if lock is not None:
            steps.append(("use", lock, False, reason))
        steps += [("door", door, True, reason), ("walk_point", self._side(door, to_room), reason)]
        if close:
            steps.append(("door", door, False, reason))
        return steps

    def _on_report(self, event) -> None:
        name = self.session.device_name(event.twin_id)
        if name is None:
            return
        category = self.home.device(name).record["identity"]["category"]
        if (category, event.feature) not in ALARMS:
            return
        if event.value is True and not self._alarm:
            self._alarm = True
            if not self._outside():
                self._epoch += 1
                self._steps.clear()
                self._waiting = False
                self._steps.extend(self._leave_home("an alarm", lock=False))
                clock = self.session.clock
                clock.call_at(clock.now(), self._next)
        elif event.value is False:
            self._alarm = False

    # -- what the resident perceives ------------------------------------------------------------
    def _why(self, reason: str) -> dict:
        task = self.routine.tasks[self._task].name if self._task is not None else None
        return {"resident": self.name, "task": task, "reason": reason}

    def _state(self, device: str) -> bool:
        twin = self.rules.twin(device)
        locked = is_locked(twin)
        return bool(locked) if locked is not None else bool(is_on(twin))

    def _operator(self, device: str) -> str | None:
        """The device itself if a hand can work it; else a switch bound to it, the nearest first."""
        if self.rules.operable(device):
            return device
        p = self.world.pose(self.name)
        switches = [(distance((p.x, p.z), d.position), n) for n, d in self.rules.devices.items()
                    if device in d.controls and self.rules.operable(n)]
        return min(switches)[1] if switches else None

    def _in_reach(self, target: str) -> bool:
        return any(t == target for _, _, t in self.rules.reachable(self.name))

    def _here(self) -> str | None:
        p = self.world.pose(self.name)
        if p.room is not None:
            return p.room
        cell = self.home.map.grid.cell_of(p.x, p.z)
        room = self.home.map.room_of_cell(*cell) if cell else None
        return room.id if room else None

    def _outside(self) -> bool:
        here = self._here()
        return here is not None and self.home.map.room(here).outdoor

    def _others_in(self, room: str) -> bool:
        return any(self.world.pose(a).room == room for a in self.world.actors() if a != self.name)

    def _devices_in(self, room: str):
        return [(n, d) for n, d in self.rules.devices.items() if d.room == room and d.door is None]

    def _lights(self, room: str) -> list[str]:
        return [n for n, d in self._devices_in(room) if d.record["identity"]["category"] == "light"]

    def _lock_of(self, door: str | None) -> str | None:
        return next((n for n, d in self.rules.devices.items() if d.door == door and door is not None
                     and is_locked(self.rules.twin(n)) is not None), None)

    def _front_door(self) -> str | None:
        return next((d for d in self.world.doors if self.home.map.doorway(d).outdoor), None)

    def _outdoors(self) -> str | None:
        return next((r.id for r in self.home.map.rooms if r.outdoor), None)

    def _indoor_rooms(self) -> list[str]:
        return [r.id for r in self.home.map.rooms if not r.outdoor] or [self.home.map.rooms[0].id]

    def _indoor_side(self, door: str) -> str:
        dw = self.home.map.doorway(door)
        return next((r for r in dw.rooms if not self.home.map.room(r).outdoor), dw.rooms[0])

    def _closed_doors_here(self) -> list[str]:
        here = self._here()
        return [d for d, is_open in self.world.doors.items()
                if not is_open and here in self.home.map.doorway(d).rooms]

    def _spot(self, room: str):
        return spawn_point(self.home.map, room)

    def _near(self, device: str):
        """Where to stand to use a device: NEAR_M from it, towards its room's spot (never through its wall)."""
        d = self.rules.devices[device]
        if d.door is not None:
            return self._side(d.door, self._here() if self._here() in self.home.map.doorway(d.door).rooms
                              else self._indoor_side(d.door))
        spot = self._spot(d.room)
        gap = distance(d.position, spot)
        if gap <= NEAR_M:
            return spot
        f = NEAR_M / gap
        return (d.position[0] + (spot[0] - d.position[0]) * f, d.position[1] + (spot[1] - d.position[1]) * f)

    def _side(self, door: str, room: str):
        """A walkable point of `room` beside the doorway, clear of the doorway itself."""
        dw, m = self.home.map.doorway(door), self.home.map
        g = m.grid
        ci, cj = g.cell_of(*dw.center) or (0, 0)
        reach = int(1.0 / g.cell) + 1
        best = None
        for j in range(max(0, cj - reach), min(g.height, cj + reach + 1)):
            for i in range(max(0, ci - reach), min(g.width, ci + reach + 1)):
                c = g.center(i, j)
                d = distance(c, dw.center)
                if 0.35 <= d <= 1.0 and (i, j) not in dw.cells and g.is_walkable(i, j):
                    r = m.room_of_cell(i, j)
                    if r is not None and r.id == room and (best is None or d < best[0]):
                        best = (d, c)
        return best[1] if best else self._spot(room)
```

In `vesper/lab/session.py`, replace:
```python

import json
```
with:
```python

import dataclasses
import json
```

In `vesper/lab/session.py`, replace:
```python
    # -- life cycle ---------------------------------------------------------------------------
    def start(self, spawn_player: bool = True) -> None:
        self._write({"kind": "meta", "format": TRACE_FORMAT, "clock": self.clock_kind, "home": str(self.home.path),
```
with:
```python
    # -- life cycle ---------------------------------------------------------------------------
    def start(self, spawn_player: bool = True, residents: bool = True) -> None:
        """Start the home; the player and the home's residents enter it (a replay starts neither: their actions
        are its inputs)."""
        self._write({"kind": "meta", "format": TRACE_FORMAT, "clock": self.clock_kind, "home": str(self.home.path),
```

In `vesper/lab/session.py`, replace:
```python
            self.spawn(PLAYER, self.home.avatar["model"], self.home.avatar["start"])
```
with:
```python
            self.spawn(PLAYER, self.home.avatar["model"], self.home.avatar["start"])
        if residents:
            from vesper.lab.resident import Resident
            for spec in self.home.residents:
                Resident(self, spec.name, spec.model, spec.routine, spec.profile).start()
```

In `vesper/lab/session.py`, replace:
```python

    def walk_to(self, point, actor: str = PLAYER):
        self._input(actor, "walk_to", {"point": list(point)})
        return self.world.walk_to(actor, tuple(point))
```
with:
```python

    def walk_to(self, point, actor: str = PLAYER, why: dict | None = None):
        self._input(actor, "walk_to", {"point": list(point)}, **({"why": why} if why else {}))
        return self.world.walk_to(actor, tuple(point))
```

In `vesper/lab/session.py`, replace:
```python

    def interact(self, target: str | None = None, actor: str = PLAYER) -> dict:
        began = self.clock.now()  # on the wall clock, time passes while the rules work: replay starts it here
```
with:
```python

    def interact(self, target: str | None = None, actor: str = PLAYER, why: dict | None = None) -> dict:
        began = self.clock.now()  # on the wall clock, time passes while the rules work: replay starts it here
```

In `vesper/lab/session.py`, replace:
```python
        outcome = self.rules.interact(actor, target)
        self._input(actor, "interact", {"target": target}, outcome=outcome, t=began)
        return outcome
```
with:
```python
        outcome = self.rules.interact(actor, target)
        self._input(actor, "interact", {"target": target}, outcome=outcome, t=began, **({"why": why} if why else {}))
        return outcome
```

In `vesper/lab/session.py`, replace:
```python
            time.sleep(seconds)
```
with:
```python
            time.sleep(seconds)

    def note(self, record: dict) -> None:
        """A resident's decision not to act (\"forgot the light\"): ground truth, not an input."""
        self._write({"kind": "resident", **record})

    def device_name(self, twin_id: str) -> str | None:
        return self._names.get(twin_id)
```

In `vesper/lab/session.py`, replace:
```python
def replay(records: list[dict], home) -> list[dict]:
    """Re-apply a trace's inputs at their times to a new session of `home` on the logical clock."""
    s = Session(home, clock=LogicalClock())
```
with:
```python
def replay(records: list[dict], home) -> list[dict]:
    """Re-apply a trace's inputs at their times to a new session of `home` on the logical clock, from the time
    of day the trace started (a day may start at another time than the home file's).

    The home's residents live their day again instead of having their actions re-applied: they are
    deterministic (their routine, their profile, the home's seed), and their actions then fall into the clock's
    order exactly as they did, also at an instant shared with a rule (a task on the minute, the ambient tick).
    `compare_reports` checks that they did the same things."""
    meta = records[0] if records and records[0].get("kind") == "meta" else {}
    if meta.get("start_time") is not None:
        home = dataclasses.replace(home, start_time=meta["start_time"])
    residents = {spec.name for spec in home.residents}
    s = Session(home, clock=LogicalClock())
```

In `vesper/lab/session.py`, replace:
```python
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    for k, r in enumerate(records):
```
with:
```python
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=True)
    for k, r in enumerate(records):
```

In `vesper/lab/session.py`, replace:
```python
    for k, r in enumerate(records):
        if r["kind"] != "input":
            continue
```
with:
```python
    for k, r in enumerate(records):
        if r["kind"] != "input" or r["actor"] in residents:
            continue
```

In `vesper/lab/session.py`, replace:
```python
            elif r["op"] == "walk_to":
                s.walk_to(a["point"], actor=r["actor"])
            elif r["op"] == "interact":
```
with:
```python
            elif r["op"] == "walk_to":
                s.walk_to(a["point"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "interact":
```

In `vesper/lab/session.py`, replace:
```python
            elif r["op"] == "interact":
                s.interact(a["target"], actor=r["actor"])
            elif r["op"] == "command":
```
with:
```python
            elif r["op"] == "interact":
                s.interact(a["target"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "command":
```

In `vesper/lab/session.py`, replace:
```python

def compare_reports(original: list[dict], again: list[dict]) -> str | None:
```
with:
```python

def _resident_actions(records: list[dict]) -> list[tuple]:
    return [(r["t"], r["actor"], r["op"], r["args"]) for r in records if r["kind"] == "input" and "why" in r]


def _compare_residents(original: list[dict], again: list[dict], wall: bool) -> str | None:
    """The residents did the same things (on the wall clock: in the same order, at times within the tolerance)."""
    a, b = _resident_actions(original), _resident_actions(again)
    for k, (x, y) in enumerate(zip(a, b)):
        same_time = abs(x[0] - y[0]) <= WALL_TOLERANCE_S if wall else x[0] == y[0]
        if x[1:] != y[1:] or not same_time:
            return (f"resident action {k}: the trace has {x[1]} {x[2]} {x[3]} at t={x[0]:g}, the replay "
                    f"{y[1]} {y[2]} {y[3]} at t={y[0]:g}")
    if len(a) != len(b):
        names = sorted({x[1] for x in a} | {y[1] for y in b})
        return f"the trace has {len(a)} resident actions, the replay {len(b)} ({', '.join(names)})"
    return None


def compare_reports(original: list[dict], again: list[dict]) -> str | None:
```

In `vesper/lab/session.py`, replace:
```python
        return divergence["message"]
    if original and original[0].get("kind") == "meta" and original[0].get("clock") == "wall":
        return _compare_wall(original, again)
    keys = ("device", "feature", "value", "observed_t", "cause")
```
with:
```python
        return divergence["message"]
    wall = bool(original) and original[0].get("kind") == "meta" and original[0].get("clock") == "wall"
    residents = _compare_residents(original, again, wall=wall)  # who did what first: the clearest difference
    if residents or wall:
        return residents or _compare_wall(original, again)
    keys = ("device", "feature", "value", "observed_t", "cause")
```

In `vesper/lab/rules.py`, replace:
```python

    def _operable(self, name: str) -> bool:
```
with:
```python

    def operable(self, name: str) -> bool:
        """Whether a person's hand works this device (a ceiling device is worked by a switch bound to it)."""
        return self._operable(name)

    def _operable(self, name: str) -> bool:
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `207 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/resident.py vesper/lab/session.py vesper/lab/rules.py tests/lab/test_resident.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): residents who live their routine, react to the house and say why; a replay lives their day again

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: `vesper lab day`

**Files:**
- Modify: `vesper/cli.py` (`_lab_day`, `_day_summary`; the `day` action with `--catalog`, `--from`, `--hours`, `--trace`)
- Tests: append to `tests/lab/test_cli_lab.py`

**Interfaces:**
- Consumes: `Session` with residents, `replay`, `compare_reports`, `read_trace` (Task 3); `_time`, `RoutineError` (Task 1); `_new_trace` (W2a).
- Produces: `vesper lab day <home> [--catalog catalog/vds] [--from HH:MM] [--hours 24] [--trace FILE]`; output lines `<resident>: <n> tasks, <w> walks, <u> uses (<reason>: <count>, …)[; did not: <decision> (<count>), …]` and `<r> device reports, <f> rule firings; trace <path>`; exit 1 with `FAILED: …` for a home without residents, a `--from` that is not a time of day, or `--hours` that is not positive.

- [ ] **Step 1: Write the failing tests**

Append to `tests/lab/test_cli_lab.py`:
```python


DAY = """\
version: 1
tasks:
  - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
  - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
  - {at: "08:00", minutes: 240, name: work, category: AWAY}
  - {at: "21:00", minutes: 60, name: read, category: LEISURE, room: bedroom}
  - {at: "22:30", minutes: 480, name: sleep, category: SLEEP, room: bedroom}
"""


def with_alex(tmp_path):
    cat, home = setup(tmp_path)
    (home.parent / "routines").mkdir()
    (home.parent / "routines" / "alex.yaml").write_text(DAY)
    home.write_text(home.read_text() + "residents:\n  - {name: alex, routine: routines/alex.yaml}\n")
    return cat, home


def test_a_day_lives_the_residents_and_replays_exactly(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
    trace = tmp_path / "day.jsonl"
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--trace", str(trace)]) == 0
    out = capsys.readouterr().out
    assert "alex: 5 tasks" in out and "a dark room" in out and "leaving home" in out
    records = [json.loads(x) for x in trace.read_text().splitlines()]
    assert records[0]["clock"] == "logical" and records[0]["start_time"] == 0
    assert max(r["t"] for r in records) <= 24 * 3600
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 0
    assert "identical" in capsys.readouterr().out


def test_a_day_can_start_later_and_stop_in_the_middle_of_a_walk_and_still_replay_exactly(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
    trace = tmp_path / "morning.jsonl"
    hours = 1.5 + 2 / 3600  # 07:30:02: alex is on the way from the bedroom to breakfast
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--trace", str(trace), "--from", "06:00",
                     "--hours", str(hours)]) == 0
    records = [json.loads(x) for x in trace.read_text().splitlines()]
    assert records[0]["start_time"] == 6 * 3600 and max(r["t"] for r in records) <= hours * 3600
    walks = [r for r in records if r["kind"] == "input" and r["op"] == "walk_to"]
    assert walks and walks[-1]["why"]["task"] == "breakfast"
    capsys.readouterr()
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 0
    assert "identical" in capsys.readouterr().out


def test_a_day_says_that_the_home_s_automations_do_not_run_without_home_assistant(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
    (home.parent / "automations.yaml").write_text("[]\n")
    home.write_text(home.read_text() + "automations: automations.yaml\n")
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--hours", "1"]) == 0
    assert "automations.yaml does not run in a day: Home Assistant is not in the loop" in capsys.readouterr().out


def test_a_day_needs_residents_and_a_time_of_day(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat)]) == 1
    assert "has no residents" in capsys.readouterr().err
    cat, home = with_alex(tmp_path / "b")
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--from", "25:00"]) == 1
    assert "--from: '25:00' is not a time of day" in capsys.readouterr().err


def test_a_replay_lives_the_residents_again_and_names_an_action_that_changed(tmp_path, capsys):
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import compare_reports, read_trace, replay
    from vesper.twins.home import load_catalog
    cat, home = with_alex(tmp_path)
    trace = tmp_path / "day.jsonl"
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--trace", str(trace)]) == 0
    records, lab = read_trace(trace), load_lab_home(home, load_catalog(cat))
    again = replay(records, lab)
    mine = [r for r in again if r["kind"] == "input" and r["actor"] == "alex"]
    assert len([r for r in again if r["kind"] == "input" and r["op"] == "spawn" and r["actor"] == "alex"]) == 1
    assert mine == [r for r in records if r["kind"] == "input" and r["actor"] == "alex"]  # lived again, the same
    (home.parent / "routines" / "alex.yaml").write_text(DAY.replace('"21:00", minutes: 60', '"21:10", minutes: 50'))
    diff = compare_reports(records, replay(records, load_lab_home(home, load_catalog(cat))))
    assert diff is not None and "alex" in diff
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_cli_lab.py -q 2>&1 | tail -1`

Expected: `5 failed, 8 passed`: `vesper lab` has no `day` action (argparse exits with `invalid choice: 'day'`)

- [ ] **Step 3: Implement**

In `vesper/cli.py`, replace:
```python
            return _lab_look(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
```
with:
```python
            return _lab_look(args, load_lab_home(Path(args.home), catalog))
        if args.action == "day":
            return _lab_day(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
```

In `vesper/cli.py`, replace:
```python

def _lab_replay(args, catalog, load_lab_home) -> int:
```
with:
```python

def _lab_day(args, home) -> int:
    """The home's residents live a day on the logical clock, faster than real time (spec §5a, §5b)."""
    from vesper.lab.routine import RoutineError, _time
    from vesper.lab.session import Session
    if not home.residents:
        print(f"FAILED: {home.path.name} has no residents; add them under `residents:`, each with a routine file",
              file=sys.stderr)
        return 1
    try:
        home.start_time = _time(args.start, "--from")
    except RoutineError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not args.hours > 0:
        print(f"FAILED: --hours: {args.hours} is not a positive number of hours", file=sys.stderr)
        return 1
    traces = home.path.parent / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else _new_trace(traces, "day")
    if home.automations is not None:
        print(f"note: {home.automations.name} does not run in a day: Home Assistant is not in the loop")
    session = Session(home, trace=trace)
    session.start(spawn_player=False)
    session.clock.advance(args.hours * 3600)
    session.stop()
    for line in _day_summary(session):
        print(line)
    kinds = [r["kind"] for r in session.records]
    print(f"{kinds.count('report')} device reports, {kinds.count('rule')} rule firings; trace {trace}")
    return 0


def _day_summary(session) -> list[str]:
    from collections import Counter
    lines = []
    for spec in session.home.residents:
        acts = [r for r in session.records if r["kind"] == "input" and r["actor"] == spec.name]
        walks = sum(r["op"] == "walk_to" for r in acts)
        uses = Counter(r["why"]["reason"] for r in acts if r["op"] == "interact" and "why" in r)
        skipped = Counter(r["decision"] for r in session.records if r["kind"] == "resident"
                          and r.get("resident") == spec.name)
        line = f"{spec.name}: {len(spec.routine.tasks)} tasks, {walks} walks, {sum(uses.values())} uses"
        if uses:
            line += " (" + ", ".join(f"{why}: {n}" for why, n in uses.most_common()) + ")"
        if skipped:
            line += "; did not: " + ", ".join(f"{what} ({n})" for what, n in skipped.most_common())
        lines.append(line)
    return lines


def _lab_replay(args, catalog, load_lab_home) -> int:
```

In `vesper/cli.py`, replace:
```python
    lab_look.add_argument("--height", type=int, default=800)
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
```
with:
```python
    lab_look.add_argument("--height", type=int, default=800)
    lab_day = lab_actions.add_parser("day", help="the home's residents live a day, faster than real time")
    lab_day.add_argument("home")
    lab_day.add_argument("--catalog", default="catalog/vds")
    lab_day.add_argument("--from", dest="start", default="00:00", help="the time of day it starts (default 00:00)")
    lab_day.add_argument("--hours", type=float, default=24.0, help="how long (default 24)")
    lab_day.add_argument("--trace", help="the trace file (default: traces/<time>-day-<pid>.jsonl next to the home)")
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `212 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py tests/lab/test_cli_lab.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): vesper lab day: the residents live a day on the logical clock, and it replays exactly

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: The page's console: a page that joins late, and the residents' decisions

**Files:**
- Modify: `vesper/lab/play.py` (`RECENT`; `hello(..., recent=())` sends `records`; the server keeps the last `RECENT` records)
- Modify: `vesper/lab/ui/app.js` (the console starts from the hello's records)
- Modify: `vesper/lab/ui/mapview.js` (`describe` for `resident` records)
- Tests: append to `tests/lab/test_play.py` and `tests/lab/ui/mapview.test.mjs`

**Interfaces:**
- Consumes: `Session.note` (Task 3); `PlayServer`, `hello`, `recordLine` (W2b).
- Produces: the hello message's `records` (at most `RECENT` = 200 trace records, oldest first); the console line `<resident>: <decision> (<device>)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/lab/test_play.py`:
```python


def test_a_page_that_joins_late_gets_the_recent_console(play):
    play.session.note({"resident": "alex", "decision": "forgot the light", "device": "bedroom_lamp"})
    with client(play) as ws:
        hello = until(ws, lambda m: m["type"] == "hello")
    kinds = [r["kind"] for r in hello["records"]]
    assert kinds[0] == "meta" and kinds[-1] == "resident" and len(hello["records"]) <= 200
```

Append to `tests/lab/ui/mapview.test.mjs`:
```javascript

test("a resident's decision not to act is a console line", () => {
  assert.equal(recordLine({ t: 1, kind: "resident", resident: "alex", decision: "forgot the light",
    device: "bedroom_lamp" }, 21 * 3600), "21:00:01 alex: forgot the light (bedroom_lamp)");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_play.py tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `2 failed, 19 passed`: the hello has no `records`, and the console has no line for a `resident` record (the JavaScript test)

- [ ] **Step 3: Implement**

In `vesper/lab/play.py`, replace:
```python
import asyncio
import concurrent.futures
```
with:
```python
import asyncio
import collections
import concurrent.futures
```

In `vesper/lab/play.py`, replace:
```python
QUEUE_MAX = 5000  # messages waiting for one page; a page that falls this far behind is disconnected
```
with:
```python
QUEUE_MAX = 5000  # messages waiting for one page; a page that falls this far behind is disconnected
RECENT = 200  # trace records a page gets when it connects: the console of a page that joins late
```

In `vesper/lab/play.py`, replace:
```python

def hello(session, hub_url: str | None = None) -> dict:
    home, world, m = session.home, session.world, session.home.map
```
with:
```python

def hello(session, hub_url: str | None = None, recent=()) -> dict:
    home, world, m = session.home, session.world, session.home.map
```

In `vesper/lab/play.py`, replace:
```python
        "actors": [_pose(world.pose(a)) for a in world.actors()],
        "reach": reach(session)}
```
with:
```python
        "actors": [_pose(world.pose(a)) for a in world.actors()],
        "reach": reach(session), "records": list(recent)}
```

In `vesper/lab/play.py`, replace:
```python
        self._reach = self._reach_key()
        self._loop: asyncio.AbstractEventLoop | None = None
```
with:
```python
        self._reach = self._reach_key()
        self._recent = collections.deque(session.records[-RECENT:], maxlen=RECENT)  # written on the clock thread
        self._loop: asyncio.AbstractEventLoop | None = None
```

In `vesper/lab/play.py`, replace:
```python
        session.rules.on_effect(lambda key, effect: self._emit({"type": "effect", "target": key, "effect": effect}))
        session.on_record(lambda rec: self._emit({"type": "record", "record": rec}))
```
with:
```python
        session.rules.on_effect(lambda key, effect: self._emit({"type": "effect", "target": key, "effect": effect}))
        session.on_record(self._on_record)
```

In `vesper/lab/play.py`, replace:
```python
            try:
                snapshot = await self._on_clock(lambda: hello(self.session, self.hub_url))
            except Exception as e:  # noqa: BLE001 - kept for the look, then the page is closed
```
with:
```python
            try:
                snapshot = await self._on_clock(lambda: hello(self.session, self.hub_url, self._recent))
            except Exception as e:  # noqa: BLE001 - kept for the look, then the page is closed
```

In `vesper/lab/play.py`, replace:
```python

    def _reach_key(self) -> tuple:
```
with:
```python

    def _on_record(self, rec: dict) -> None:
        self._recent.append(rec)
        self._emit({"type": "record", "record": rec})

    def _reach_key(self) -> tuple:
```

In `vesper/lab/ui/app.js`, replace:
```javascript
  }
  layout();
```
with:
```javascript
  }
  $("log").innerHTML = "";
  for (const r of h.records || []) onRecord(r); // what happened before this page joined
  layout();
```

In `vesper/lab/ui/mapview.js`, replace:
```javascript
  if (r.kind === "meta") return `the session starts on ${r.scene}`;
  if (r.kind === "report") return `${r.device} reports ${r.feature} = ${JSON.stringify(r.value)}`;
```
with:
```javascript
  if (r.kind === "meta") return `the session starts on ${r.scene}`;
  if (r.kind === "resident") return `${r.resident}: ${r.decision}` + (r.device ? ` (${r.device})` : "");
  if (r.kind === "report") return `${r.device} reports ${r.feature} = ${JSON.stringify(r.value)}`;
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `213 passed, 1 skipped`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/play.py vesper/lab/ui/app.js vesper/lab/ui/mapview.js tests/lab/test_play.py tests/lab/ui/mapview.test.mjs
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): a page that joins late gets the recent console, and a resident's decisions have their own line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 6: The example home's resident

**Files:**
- Create: `examples/lab/test-house/routines/alex.yaml`
- Modify: `examples/lab/test-house/home.yaml` (Alex under `residents:`; the `vesper lab day` line in the header)
- Tests: append to `tests/lab/test_example_home.py`

**Interfaces:**
- Consumes: the example home of W2c and the catalog's product records (`catalog/vds/zigbee.json`, read directly; the test is skipped without a local catalog); `Session`, `replay`, `compare_reports` (Task 3).

- [ ] **Step 1: Write the failing test**

Append to `tests/lab/test_example_home.py`:
```python


@pytest.mark.skipif(not CATALOG.is_file(), reason="no local catalog (`vesper catalog build`)")
def test_the_example_s_resident_lives_a_whole_day_that_replays_exactly():
    import dataclasses

    from vesper.lab.session import compare_reports, replay
    records = {r["id"]: r for r in json.loads(CATALOG.read_text())}
    home = dataclasses.replace(load_lab_home(EXAMPLE / "home.yaml", records), start_time=0)
    (alex,) = home.residents
    session = Session(home)
    session.start(spawn_player=False)
    session.clock.advance(24 * 3600)
    session.stop()
    stuck = [r for r in session.records if r["kind"] == "resident" and r["decision"].startswith("could")]
    assert not stuck  # every task's room and device was reached
    uses = {r["args"]["target"] for r in session.records if r["kind"] == "input" and r["op"] == "interact"}
    assert {"kitchen_plug", "hall-porch", "bedroom_lamp"} <= uses  # cooking, the front door, a dark bedroom
    assert compare_reports(session.records, replay(session.records, home)) is None
    assert alex.routine.tasks[-1].category == "SLEEP"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/lab/test_example_home.py -q 2>&1 | tail -1`

Expected: `1 failed, 3 passed` in the repository's working copy, where `catalog/vds/zigbee.json` exists: the example home has no residents (`2 passed, 2 skipped` without a local catalog)

- [ ] **Step 3: Give the example home its resident**

Create `examples/lab/test-house/routines/alex.yaml`:
```yaml
# Alex's weekday: `vesper lab day examples/lab/test-house/home.yaml` lives it in seconds; `vesper lab play` in real time.
version: 1
tasks:
  - {at: "06:30", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
  - {at: "07:00", minutes: 30, name: breakfast, category: EATING, room: kitchen}
  - {at: "07:30", minutes: 540, name: work, category: AWAY}
  - {at: "16:30", minutes: 60, name: cooking, category: HOUSEHOLD, room: kitchen}
  - {at: "17:30", minutes: 30, name: dinner, category: EATING, room: kitchen}
  - {at: "19:00", minutes: 120, name: tv, category: LEISURE, room: hall}
  - {at: "21:00", minutes: 60, name: read, category: LEISURE, room: bedroom}
  - {at: "22:30", minutes: 480, name: sleep, category: SLEEP, room: bedroom}
```

In `examples/lab/test-house/home.yaml`, replace:
```yaml
#   vesper lab play  examples/lab/test-house/home.yaml --hub    # with VESPER's Home Assistant in the loop
version: 2
```
with:
```yaml
#   vesper lab play  examples/lab/test-house/home.yaml --hub    # with VESPER's Home Assistant in the loop
#   vesper lab day   examples/lab/test-house/home.yaml          # Alex lives a day, in seconds
version: 2
```

In `examples/lab/test-house/home.yaml`, replace:
```yaml
automations: automations.yaml
```
with:
```yaml
automations: automations.yaml
residents:
  - {name: alex, avatar: male_1, routine: routines/alex.yaml, profile: {forget_light: 0.2, forget_lock: 0.1}}
```

- [ ] **Step 4: Run it, then the whole suite**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `214 passed, 1 skipped` in the working copy (`213 passed, 2 skipped` without a local catalog)

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `698 passed, 13 skipped` in the repository's working copy (an export of `main` with only the catalog added reports `697 passed, 14 skipped`)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add examples/lab/test-house/routines/alex.yaml examples/lab/test-house/home.yaml tests/lab/test_example_home.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "docs(lab): the example home's resident: Alex's weekday, lived in seconds with vesper lab day

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## After this plan

- **Plan W3b:** the converters from the earlier work's schedules (the 90 persona schedules in `results/revision/llm_schedules/`, the ARAS day plans for two residents); the thermostat and the comfort band; VESPER's built-in rule engine running the home's automations on the logical clock (§5b), named in the trace.
- **The live checks** after another project's run, with the PI's go: rebuild the catalog, then Lab check 1 (`docs/experiments/lab-runbook.md`), now with Alex in the house.
- **W2d** (Matter on the hub; the placement editor), **W4** the security lab, **W1** the Habitat world service and the 3D view, **W5** packaging.
