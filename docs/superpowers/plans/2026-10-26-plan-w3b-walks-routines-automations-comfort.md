# Plan W3b: walks that do not stall, routines from the earlier work, the home's automations without Home Assistant, and the comfort band — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The residents of W3a walk a home-sized floor without stalling the clock, get their routines from the earlier work's 90 persona schedules and the ARAS day plans, live a day in which VESPER runs the home's Home Assistant automations itself, and change the thermostat when a room is too cold or too warm. A Matter color-temperature twin starts at a temperature a light can show.

**Architecture:**
1. **Walks** (`vesper/lab/mapworld.py`): a straight line when the line of sight is free; else a route over the open doorways (A* by straight-line distance), each leg searched only inside its room and kept for later walks; the whole-floor search only for a point in no room. W3a measured 1.27 s per walk on a 36×24 m floor, which stalls the clock thread.
2. **Routines from the earlier work** (`vesper/lab/convert.py`, `vesper lab routine`): a persona schedule (JSON) or an ARAS day (`DAY_n.txt`, one row per second) becomes a routine file. The canonical rooms (living_room, kitchen, bedroom, bathroom, entryway, hallway) map to the home's rooms by kind, with fallbacks and `--room` overrides, and every choice is noted in the file's header. A trip, a commute and work away from home become AWAY tasks. The converted file is checked with `load_routine` before it is kept.
3. **The automation engine** (`vesper/lab/automations.py`): a session started with `automations=True` runs the home's `automations.yaml` itself. Entity ids are the ones MQTT discovery gives Home Assistant. Entity states come from the devices' reports. A service call sends the command the entity's MQTT config sends, applied as the bridge applies it (`platform_set`). An automation the engine cannot run is refused by name; the others run. Each command is an `automation` record, not an input, and a replay runs the engine again. `vesper lab day` runs the engine and names what it refused.
4. **The comfort band** (`vesper/lab/profile.py`, `vesper/lab/rules.py`, `vesper/lab/resident.py`, `vesper/lab/session.py`): a profile's `comfort: [low, high]` (°C). A resident at its task who finds the room too cold, as its thermostat shows it, switches a thermostat that is off to heat and raises the setpoint to `low`. Too warm, it lowers the setpoint to `high`. A thermostat's setpoint and mode are set by hand through `Session.adjust` (an input `set`, within reach, replayed).
5. **A Matter color-temperature twin** (`vesper/twins/twin.py`) starts at the spec's 250 mireds, not at the type's minimum (Experiment 6's finding).

**Tech Stack:** Python ≥3.11 (standard library `heapq`, `math`, `json`); PyYAML (already a dependency); the Lab of W2a–W3a; the exposure tier's MQTT discovery (`vesper/exposure/ha_discovery.py`) and Zigbee2MQTT bridge helpers (`vesper/exposure/z2m_bridge.py`).

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, approved by the PI on 2026-10-24 (RKA `jrn_01M5QSGDYGE158FRVPVWKN0QNZ`). Covered here:
- §5a the converters from the earlier work's schedules (the 90 persona schedules, the ARAS day plans for two residents); the reaction "a temperature outside the profile's comfort band: changes the thermostat"; profiles with the comfort band;
- §5b scripted sessions without Home Assistant: "Home Assistant's automations are then replaced by VESPER's built-in rule engine, which also runs on the logical clock, and the trace says so"; scale: a walk on a home-sized floor does not stall the clock;
- Experiment 6's finding (`docs/experiments/results-2026-10-26.md`): twins start inside a product's physical limits.

## Global Constraints

- No new dependency.
- A resident acts only through the session (`walk_to`, `interact`, and now `adjust`), as the player does.
- The engine sees only what Home Assistant would see (the devices' reports, through the bridge's discovery mapping) and acts only as Home Assistant would (the entity's MQTT command, applied with `platform_set`). Its commands are `automation` records, never inputs.
- A day replays exactly: `vesper lab replay` says `identical`, with the engine and the comfort band in the day.
- Nothing fails silently: an automation the engine cannot run, a converted routine that would not load, a room the converter could not find, and a comfort band that is not one are each named.
- Shared Mac: another project's experiment runs ~04:40–10:00Z on 2026-10-26. During its window, only sub-minute targeted tests at background priority (`taskpolicy -b nice -n 19`); no full suite, no Home Assistant, no Mosquitto, no Docker.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **An automation's `for:` interrupted** (the state changes back before it has held) must start again, not fire on the old timer. Pinned by `test_a_for_starts_again_when_the_state_changes_before_it_has_held` (Task 3).
2. **A trigger while the automation is still running** (a `delay`) is dropped, as Home Assistant's `mode: single` does, and the trace says so. Pinned by `test_mode_single_drops_a_trigger_while_the_automation_runs` (Task 3).
3. **A walk through a closed door on a large floor:** the routed plan walks around a closed door, and finds no way into a shut room. Pinned by `test_a_closed_door_on_the_way_is_walked_around_and_a_shut_room_is_out_of_reach` (Task 1).
4. **ARAS seconds that do not divide into whole minutes** must still load as a routine (no overlap from rounding). Pinned by `test_back_to_back_tasks_of_odd_seconds_still_load_as_a_routine` (Task 2).
5. **A thermostat the player sets out of reach, past its range, or a feature no hand sets:** "out of reach", refused, and a named error. Pinned by `test_the_player_sets_a_thermostat_within_reach_and_the_replay_matches` (Task 4).

## Rulings carried into this plan

- **The routed plan is held to the whole-floor plan, not made equal to it.** It is at most 10% longer on the test floors (pinned), and it searches fewer than a tenth of the floor's cells. Cost if wrong: a resident takes a slightly longer route between two rooms; Habitat's pathfinder replaces both in W1.
- **The engine refuses an automation as a whole** when it uses a trigger, condition, action or mode it does not run (sun, templates, `mode: queued`, a service outside `light.*`, `switch.*`, `lock.lock/unlock`, `delay`), or names an entity this home does not have. It never runs part of an automation. Cost if wrong: a day without the refused automation; the meta record and `vesper lab day` name it.
- **Matter devices are not on VESPER's hub yet** (plan W2d), so the engine sees only the Zigbee devices' entities, as Home Assistant does in the Lab today.
- **The rules' room temperature does not follow the heating:** setting a thermostat changes the device (what the platform and an attack see), not the room. Once the setpoint is in the band the resident leaves it, so it never fiddles. Cost if wrong: a cold room stays cold for the whole day; heating physics can come with W4's scenarios.
- **The catalog's missing physical limits are reported, not fixed here:** matching the Matter device types' required attribute names leniently (`COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MIN` against the cluster's `..._MIN_MIREDS`) would change every color-temperature light's record and the catalog's numbers. That needs the PI.
- **`vesper lab play` keeps Home Assistant (or nothing) for automations;** the engine runs where §5b puts it, in scripted sessions on the logical clock.

---
### Task 1: Walks that do not stall the clock

**Files:**
- Modify: `vesper/lab/mapworld.py` (`plan_work`, `_legs`, `_graph`; `_plan` routes over the doorways; `_plan_grid`, `_route`, `_leg`, `_in_room`, `_doorway_graph`, `_graph_route`, `_straighten`)
- Modify tests: `tests/lab/maps.py` (`grid_house`), `tests/lab/test_mapworld.py`

**Interfaces:**
- Consumes: `WorldMap.labels`, `WorldMap.doorways` (`Doorway.id`, `.rooms`, `.cells`, `.center`), `Grid.cell_of`, `Grid.center` (W2a).
- Produces: `MapWorld.plan_work` (the cells the last plan expanded); `MapWorld._plan_grid(start, target)` (the whole-floor reference); `MapWorld._legs` (the kept legs); in `tests/lab/maps.py`: `grid_house(cols=6, rows=4, size=6.0)` (rooms `r<col><row>`, 20 cm walls, a 1 m doorway in the middle of every inner wall). `walk_to`, `doors_on_route` and `_plan`'s result keep their meaning.

- [ ] **Step 1: Write the failing tests**

Append to `tests/lab/maps.py`:
```python


def grid_house(cols=6, rows=4, size=6.0):
    """cols x rows rooms of `size` metres (r<col><row>), 20 cm walls, and a 1 m doorway in the middle of every
    wall between neighbours: a home-sized floor (36 x 24 m by default) where a walk crosses many rooms."""
    def walkable(x, z):
        in_vwall = any(abs(x - c * size) < 0.1 for c in range(1, cols))
        in_hwall = any(abs(z - r * size) < 0.1 for r in range(1, rows))
        if in_vwall and in_hwall:
            return False
        if in_vwall:
            return abs(z % size - size / 2) < 0.5
        if in_hwall:
            return abs(x % size - size / 2) < 0.5
        return True
    rooms = [Room(f"r{c}{r}", "room", rect(c * size + (0.1 if c else 0), r * size + (0.1 if r else 0),
                                          (c + 1) * size - (0.1 if c < cols - 1 else 0),
                                          (r + 1) * size - (0.1 if r < rows - 1 else 0)))
             for c in range(cols) for r in range(rows)]
    return WorldMap(scene="test/grid-house", rooms=rooms, grid=grid_from(cols * size, rows * size, walkable))
```

In `tests/lab/test_mapworld.py`, replace:
```python

from tests.lab.maps import house, two_rooms
from vesper.lab.mapworld import SPEED_M_S, STEP_S, MapWorld, WorldError
```
with:
```python

from tests.lab.maps import grid_house, house, two_rooms
from vesper.lab.mapworld import SPEED_M_S, STEP_S, MapWorld, WorldError
```

In `tests/lab/test_mapworld.py`, replace:
```python
    assert arrived and arrived[0] <= planned + STEP_S + 0.1 + 1e-6
```
with:
```python
    assert arrived and arrived[0] <= planned + STEP_S + 0.1 + 1e-6


def path_length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def test_a_walk_in_a_straight_line_needs_no_search():
    w, _ = world(house())
    w.spawn("p", "female_0", (4.0, 1.0))
    assert w.walk_to("p", (6.5, 2.5)) == [(4.0, 1.0), (6.5, 2.5)]
    assert w.plan_work == 0


def test_a_walk_across_a_home_sized_floor_searches_only_the_rooms_on_its_way():
    w, _ = world(grid_house())  # 36 x 24 m, 24 rooms: a whole-floor search took over a second here
    w.spawn("p", "female_0", (1.0, 1.0))
    path = w.walk_to("p", (35.0, 23.0))
    assert path is not None and all(w._segment_free(a, b) for a, b in zip(path, path[1:]))
    walkable = sum(w.map.grid.walkable)
    assert w.plan_work < walkable / 10, (w.plan_work, walkable)
    whole = w._plan_grid((1.0, 1.0), (35.0, 23.0))  # the whole-floor search: the shortest path on the grid
    assert path_length(path) <= 1.1 * path_length(whole)


def test_a_closed_door_on_the_way_is_walked_around_and_a_shut_room_is_out_of_reach():
    m = grid_house(cols=3, rows=2)
    doors = [d.id for d in m.doorways]
    w, _ = world(m, doors=doors)
    w.spawn("p", "female_0", (1.0, 1.0))  # in r00
    w.set_door("r00-r10", False)
    path = w.walk_to("p", (9.0, 1.0))  # r10: round through r01 and r11
    assert path is not None and max(z for _, z in path) > 6.0
    w.set_door("r00-r01", False)
    assert w.walk_to("p", (9.0, 1.0)) is None  # every way out of r00 is shut


def test_repeated_walks_reuse_what_the_first_one_searched():
    w, _ = world(grid_house())
    w.spawn("p", "female_0", (1.0, 1.0))
    w.walk_to("p", (35.0, 23.0))
    first = w.plan_work
    w.stop("p")
    w.walk_to("p", (35.0, 23.0))
    assert 0 < w.plan_work < first  # only the legs from the start and to the target are searched again
    assert w._legs  # the legs between doorways are kept
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_mapworld.py -q 2>&1 | tail -1`

Expected: `3 failed, 14 passed`: `AttributeError: 'MapWorld' object has no attribute 'plan_work'` (the straight line, the home-sized floor, and the reused legs; the closed-door test already passes)

- [ ] **Step 3: Implement**

In `vesper/lab/mapworld.py`, replace:
```python
        self._blocked: frozenset = frozenset()
```
with:
```python
        self._blocked: frozenset = frozenset()
        self.plan_work = 0  # grid cells the last plan expanded: what it cost, independent of the machine
        self._legs: dict = {}  # (room, from doorway, to doorway) -> cells: the walk across a room, reused
        self._graph = None  # per map: each room's doorways, and each doorway's cell
```

In `vesper/lab/mapworld.py`, replace:
```python
    def _plan(self, start: Point, target: Point) -> list[Point] | None:
        """A* over free cells (8-connected, no corner cutting), then straightened along free lines of sight."""
        g = self.map.grid
```
with:
```python
    def _plan(self, start: Point, target: Point) -> list[Point] | None:
        """The shortest walk, found cheaply on a home-sized floor (spec §5b: the clock thread must not stall):
        - a straight line when its line of sight is free (no search);
        - else a route through the open doorways (A* over the doorways, by straight-line distance), each leg
          searched only inside its room and kept for later walks; a leg a room cannot hold drops its edge;
        - else, for a point in no room, A* over the whole floor.
        Then straightened along free lines of sight."""
        self.plan_work = 0
        g = self.map.grid
        s, goal = g.cell_of(*start), self._nearest_free_cell(target)
        if s is None or goal is None:
            return None
        end = target if self._free(*target) else g.center(*goal)
        if self._segment_free(start, end):
            return [start, end]
        cells = self._route(start, s, goal, end)
        if cells is None:
            return None
        return self._straighten([start] + [g.center(*c) for c in cells[1:-1]] + [end])

    def _plan_grid(self, start: Point, target: Point) -> list[Point] | None:
        """A* over the whole floor, then straightened: the reference the routed plan is held to."""
        self.plan_work = 0
        g = self.map.grid
```

In `vesper/lab/mapworld.py`, replace:
```python
        end = target if self._free(*target) else g.center(*goal)
        points = [start] + [g.center(*c) for c in cells[1:-1]] + [end]
        straight = [points[0]]
```
with:
```python
        end = target if self._free(*target) else g.center(*goal)
        return self._straighten([start] + [g.center(*c) for c in cells[1:-1]] + [end])

    def _route(self, start: Point, s, goal, end: Point):
        """Cells from s to goal through the open doorways, or None if there is no way."""
        w, labels = self.map.grid.width, self.map.labels
        rs, rg = labels[s[1] * w + s[0]], labels[goal[1] * w + goal[0]]
        if rs < 0 or rg < 0 or not self._cell_free(*s):
            return self._cells(s, goal, self._cell_free)  # a point in no room: the whole floor
        if rs == rg:
            here = self._cells(s, goal, self._in_room(rs, ()))
            if here is not None:
                return here
        graph = self._doorway_graph()
        doors_of, cell_of = graph
        open_ = {d.id: d for d in self.map.doorways if self.doors.get(d.id, True)}
        dead: set = set()
        for _ in range(4 * len(open_) + 4):
            route = self._graph_route(start, end, rs, rg, doors_of, open_, dead)
            if route is None:
                return None
            cells, ok = [s], True
            for (ra, a, b) in route:  # legs: in room ra, from a to b ("S", "G" or a doorway id)
                ca = s if a == "S" else cell_of[a]
                cb = goal if b == "G" else cell_of[b]
                leg = self._leg(ra, a, b, ca, cb)
                if leg is None:
                    dead.add((ra, a, b))
                    ok = False
                    break
                cells += leg[1:]
            if ok:
                return cells
        return None

    def _leg(self, room: int, a: str, b: str, ca, cb):
        """The cells across `room` from a to b; doorway-to-doorway legs are kept (door states never change
        a room's own cells, and a closed doorway is never part of a route)."""
        keep = a != "S" and b != "G"
        if keep and (room, a, b) in self._legs:
            return self._legs[(room, a, b)]
        ends = tuple(d for d in (a, b) if d not in ("S", "G"))
        cells = self._cells(ca, cb, self._in_room(room, ends))
        if keep:
            self._legs[(room, a, b)] = cells
            self._legs[(room, b, a)] = cells[::-1] if cells else cells
        return cells

    def _in_room(self, room: int, doorways):
        """free(i, j) for a search confined to `room` and the named doorways' own cells."""
        w, labels = self.map.grid.width, self.map.labels
        extra = set().union(*(self.map.doorway(d).cells for d in doorways)) if doorways else set()

        def free(i, j):
            return self._cell_free(i, j) and (labels[j * w + i] == room or (i, j) in extra)
        return free

    def _doorway_graph(self):
        """Per room: its doorways; per doorway: the cell a route passes (the doorway cell nearest its centre)."""
        if self._graph is None:
            index = {r.id: k for k, r in enumerate(self.map.rooms)}
            doors_of: dict = {}
            cell_of: dict = {}
            for d in self.map.doorways:
                for r in d.rooms:
                    doors_of.setdefault(index[r], []).append(d)
                cell_of[d.id] = min(d.cells, key=lambda c: (distance(self.map.grid.center(*c), d.center), c))
            self._graph = (doors_of, cell_of)
        return self._graph

    def _graph_route(self, start, end, rs, rg, doors_of, open_, dead):
        """A* over S, the open doorways and G, by straight-line distance; returns [(room, from, to)] legs."""
        index = {r.id: k for k, r in enumerate(self.map.rooms)}
        pos = {"S": start, "G": end, **{d: open_[d].center for d in open_}}

        def edges(node):
            rooms = [rs] if node == "S" else [index[r] for r in open_[node].rooms]
            for ra in rooms:
                if ra == rg and (ra, node, "G") not in dead:
                    yield ra, "G"
                for d in doors_of.get(ra, []):
                    if d.id in open_ and d.id != node and (ra, node, d.id) not in dead:
                        yield ra, d.id

        frontier, best, came = [(distance(start, end), 0.0, "S")], {"S": 0.0}, {}
        while frontier:
            _, cost, node = heapq.heappop(frontier)
            if node == "G":
                legs = []
                while node != "S":
                    prev, ra = came[node]
                    legs.append((ra, prev, node))
                    node = prev
                return legs[::-1]
            if cost > best.get(node, math.inf):
                continue
            for ra, nxt in edges(node):
                c = cost + distance(pos[node], pos[nxt])
                if c < best.get(nxt, math.inf):
                    best[nxt], came[nxt] = c, (node, ra)
                    heapq.heappush(frontier, (c + distance(pos[nxt], end), c, nxt))
        return None

    def _straighten(self, points: list[Point]) -> list[Point]:
        straight = [points[0]]
```

In `vesper/lab/mapworld.py`, replace:
```python
            _, _, c = heapq.heappop(frontier)
            if c == goal:
```
with:
```python
            _, _, c = heapq.heappop(frontier)
            self.plan_work += 1
            if c == goal:
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `232 passed, 1 skipped` in the repository's working copy (`229 passed, 4 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/mapworld.py tests/lab/maps.py tests/lab/test_mapworld.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "perf(lab): walks route over the open doorways and keep their legs: a home-sized floor no longer stalls the clock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: Routines from the earlier work: `vesper lab routine`

**Files:**
- Create: `vesper/lab/convert.py`
- Modify: `vesper/cli.py` (the `routine` action: `_lab_routine`; `source` persona|aras, `file`, `--home`, `--catalog`, `--resident`, `--room`, `--out`)
- Create tests: `tests/lab/test_convert.py`

**Interfaces:**
- Consumes: `Task`, `Routine`, `load_routine`, `RoutineError`, `CATEGORIES` (W3a `vesper/lab/routine.py`); `WorldMap.rooms` (`Room.id`, `.kind`, `.outdoor`), `WorldMap.room`, `MapError` (W2a).
- Produces in `vesper/lab/convert.py`: `CANONICAL`; `ConvertError`; `canonical(name)`; `room_map(world_map, overrides=None) -> (rooms, notes)`; `from_persona(schedule, world_map, overrides=None) -> (tasks, notes)`; `from_aras(rows, resident, world_map, overrides=None) -> (tasks, notes)`; `routine_yaml(tasks, source, notes) -> str` (a routine file whose header names its source and notes). CLI: `vesper lab routine {persona,aras} FILE --home HOME [--resident 1|2] [--room CANONICAL=ROOM ...] [--out FILE]`; it prints the notes and `<n> tasks: <path>`; exit 1 with `FAILED: …` for a file that exists, a resident that is not 1 or 2, a `--room` that is not `canonical=room`, or a converted routine that does not load (and leaves no file).

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_convert.py`:
```python
"""Routines from the earlier work's schedules (spec §5a): the 90 LLM persona schedules and the ARAS day plans."""
import json

import pytest
import yaml

from tests.lab.homes import catalog, write_home
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli
from vesper.lab.convert import ConvertError, from_aras, from_persona, room_map, routine_yaml
from vesper.lab.routine import load_routine

H = 3600


def persona(tasks, wfh=True):
    return {"model": "gemma-2-9b-it", "persona": 7, "date": "2026-10-14", "works_from_home": wfh,
            "tasks": [dict(zip(("name", "category", "room", "start_s", "duration_s"), t)) for t in tasks]}


def test_canonical_rooms_find_the_home_s_rooms_and_say_where_they_fell_back():
    rooms, notes = room_map(house())
    assert rooms["kitchen"] == "kitchen" and rooms["bedroom"] == "bedroom" and rooms["hallway"] == "hall"
    assert rooms["living_room"] == "hall" and rooms["entryway"] == "hall"  # the test house has neither
    assert any("living_room" in n and "hall" in n for n in notes)
    rooms, _ = room_map(house(), {"living_room": "bedroom"})
    assert rooms["living_room"] == "bedroom"
    with pytest.raises(ConvertError, match="--room living_room=attic: unknown room 'attic'"):
        room_map(house(), {"living_room": "attic"})


def test_a_persona_day_becomes_a_routine_with_its_trips_and_commute_away():
    day = persona([("Wake Up", "SLEEP", "bedroom", 7 * H, 900),
                   ("Prepare Breakfast", "HOUSEHOLD", "kitchen", 7 * H + 900, 900),
                   ("Leave for work", "WORK", "entryway", 8 * H, 600),
                   ("Work at the office", "WORK", "living_room", 8 * H + 600, 8 * H),
                   ("Commute home", "WORK", "entryway", 17 * H, 1800),
                   ("Grocery shopping", "HOUSEHOLD", "kitchen", 18 * H, 3600),
                   ("Dinner", "EATING", "kitchen", 19 * H, 1800),
                   ("Watch TV", "LEISURE", "living_room", 20 * H, 7200),
                   ("Sleep", "SLEEP", "bedroom", 22 * H + 1800, 8 * H)], wfh=False)
    tasks, notes = from_persona(day, house())
    shape = [(t.name, t.category, t.room) for t in tasks]
    assert shape == [("Wake Up", "SLEEP", "bedroom"), ("Prepare Breakfast", "HOUSEHOLD", "kitchen"),
                     ("away: Leave for work .. Commute home", "AWAY", None),
                     ("away: Grocery shopping", "AWAY", None), ("Dinner", "EATING", "kitchen"),
                     ("Watch TV", "LEISURE", "hall"), ("Sleep", "SLEEP", "bedroom")]
    away = tasks[2]
    assert (away.start, away.end) == (8 * H, 17 * H + 1800)


def test_work_at_home_stays_home_and_work_away_without_a_commute_is_an_away_task():
    tasks, _ = from_persona(persona([("Work", "WORK", "living_room", 9 * H, 8 * H)], wfh=True), house())
    assert (tasks[0].category, tasks[0].room) == ("WORK", "hall")
    tasks, _ = from_persona(persona([("Work", "WORK", "living_room", 9 * H, 8 * H)], wfh=False), house())
    assert (tasks[0].category, tasks[0].room, tasks[0].name) == ("AWAY", None, "away: Work")


def test_tasks_run_in_start_order_an_overlap_is_clipped_and_a_task_under_a_minute_is_dropped():
    tasks, notes = from_persona(persona([("Breakfast", "EATING", "kitchen", 7 * H, 3600),
                                         ("Shower", "HYGIENE", "bathroom", 7 * H + 1800, 900),
                                         ("Glass of water", "EATING", "kitchen", 9 * H, 30)]), house())
    assert [(t.name, t.start, t.end) for t in tasks] == [("Breakfast", 7 * H, 7 * H + 1800),
                                                          ("Shower", 7 * H + 1800, 7 * H + 2700)]
    assert any("Breakfast" in n and "clipped" in n for n in notes)
    assert any("Glass of water" in n and "under a minute" in n for n in notes)


def test_a_converted_routine_loads_and_names_its_source(tmp_path):
    day = persona([("Wake Up", "SLEEP", "bedroom", 7 * H, 900), ("Dinner", "EATING", "kitchen", 19 * H, 1800)])
    tasks, notes = from_persona(day, house())
    path = tmp_path / "persona7.yaml"
    path.write_text(routine_yaml(tasks, source="persona gemma-2-9b-it p07 2026-10-14", notes=notes))
    assert path.read_text().startswith("# from: persona gemma-2-9b-it p07 2026-10-14")
    r = load_routine(path, house())
    assert [t.name for t in r.tasks] == ["Wake Up", "Dinner"] and r.tasks[1].room == "kitchen"


def aras_rows(runs_r1, runs_r2):
    """One ARAS day: 20 sensor columns, then each resident's activity id per second."""
    def expand(runs):
        out = []
        for act, seconds in runs:
            out += [act] * seconds
        return out + [out[-1]] * (86400 - len(out))
    a, b = expand(runs_r1), expand(runs_r2)
    return [[0] * 20 + [x, y] for x, y in zip(a, b)]


def test_an_aras_day_becomes_one_routine_per_resident_and_short_runs_merge():
    rows = aras_rows([(11, 7 * H), (21, 300), (4, 30), (3, 1500), (2, 9 * H), (12, 2 * H)],
                     [(11, 8 * H), (14, 900), (17, 4 * H)])
    r1, notes1 = from_aras(rows, 1, house())
    assert [(t.name, t.category, t.room) for t in r1[:5]] == [
        ("Sleeping", "SLEEP", "bedroom"), ("Brushing Teeth", "HYGIENE", "hall"),
        ("Preparing Breakfast", "HOUSEHOLD", "kitchen"), ("Going Out", "AWAY", None),
        ("Watching TV", "LEISURE", "hall")]
    assert any("Having Breakfast" in n and "30 s" in n for n in notes1)  # merged into its neighbour
    r2, _ = from_aras(rows, 2, house())
    assert [t.name for t in r2] == ["Sleeping", "Having Shower", "Using Internet"]


def test_sleep_across_midnight_is_one_task():
    rows = aras_rows([(11, 6 * H), (4, 1800), (12, 15 * H), (11, 2 * H + 1800)], [(11, 86400)])
    tasks, _ = from_aras(rows, 1, house())
    assert tasks[0].name == "Having Breakfast" and tasks[-1].name == "Sleeping"
    assert (tasks[-1].start, tasks[-1].duration) == (21 * H + 1800, 2 * H + 1800 + 6 * H)


def test_the_cli_writes_a_routine_from_either_source(tmp_path, capsys):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    home = write_home(tmp_path)
    src = tmp_path / "gemma-2-9b-it_p07_2026-10-14.json"
    src.write_text(json.dumps(persona([("Dinner", "EATING", "kitchen", 19 * H, 1800)])))
    out = tmp_path / "routines" / "p07.yaml"
    assert cli.main(["lab", "routine", "persona", str(src), "--home", str(home), "--catalog", str(cat),
                     "--out", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["tasks"][0]["room"] == "kitchen"
    assert "living_room -> hall" in capsys.readouterr().out
    day = tmp_path / "DAY_1.txt"
    day.write_text("\n".join(" ".join(str(v) for v in r) for r in aras_rows([(11, 86400)], [(12, 86400)])))
    assert cli.main(["lab", "routine", "aras", str(day), "--resident", "2", "--home", str(home), "--catalog",
                     str(cat), "--out", str(tmp_path / "routines" / "r2.yaml")]) == 0
    assert yaml.safe_load((tmp_path / "routines" / "r2.yaml").read_text())["tasks"][0]["name"] == "Watching TV"
    assert cli.main(["lab", "routine", "aras", str(day), "--resident", "3", "--home", str(home), "--catalog",
                     str(cat)]) == 1
    assert "--resident: 3 is not 1 or 2" in capsys.readouterr().err


def test_back_to_back_tasks_of_odd_seconds_still_load_as_a_routine(tmp_path):
    rows = aras_rows([(11, 3378), (21, 1234), (4, 1999), (12, 86400 - 3378 - 1234 - 1999)], [(11, 86400)])
    tasks, notes = from_aras(rows, 1, house())
    path = tmp_path / "odd.yaml"
    path.write_text(routine_yaml(tasks, source="test", notes=notes))
    r = load_routine(path, house())
    assert [t.name for t in r.tasks] == ["Sleeping", "Brushing Teeth", "Having Breakfast", "Watching TV"]
    assert all(abs(a.duration - b.duration) < 0.01 for a, b in zip(r.tasks, tasks))


def test_a_routine_that_would_not_load_is_never_left_behind(tmp_path, capsys, monkeypatch):
    import vesper.lab.convert as convert
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    home = write_home(tmp_path)
    src = tmp_path / "p.json"
    src.write_text(json.dumps(persona([("Dinner", "EATING", "kitchen", 19 * H, 1800)])))
    monkeypatch.setattr(convert, "routine_yaml", lambda *a, **k: "version: 2\ntasks: []\n")  # a converter defect
    out = tmp_path / "routines" / "p.yaml"
    args = ["lab", "routine", "persona", str(src), "--home", str(home), "--catalog", str(cat), "--out", str(out)]
    assert cli.main(args) == 1
    assert "does not load" in capsys.readouterr().err
    assert not out.exists() and not list((tmp_path / "routines").glob("*"))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_convert.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.convert'`

- [ ] **Step 3: Implement**

Create `vesper/lab/convert.py`:
```python
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


def routine_yaml(tasks: list[Task], source: str, notes: list[str] = ()) -> str:
    """A routine file (version 1) with its source and the conversion's notes as comments."""
    lines = [f"# from: {source}"] + [f"# {n}" for n in notes] + ["version: 1", "tasks:"]
    for t in tasks:
        # whole minutes stay whole; else rounded down at 4 decimals (< 6 ms): a task never runs into the next
        minutes = int(t.duration // 60) if t.duration % 60 == 0 else math.floor(t.duration / 60 * 1e4) / 1e4
        fields = [f"at: {json.dumps(clock(t.start))}", f"minutes: {minutes}", f"name: {json.dumps(t.name)}",
                  f"category: {t.category}"] + ([f"room: {t.room}"] if t.room else [])
        lines.append("  - {" + ", ".join(fields) + "}")
    return "\n".join(lines) + "\n"
```

In `vesper/cli.py`, replace:
```python
            return _lab_day(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
```
with:
```python
            return _lab_day(args, load_lab_home(Path(args.home), catalog))
        if args.action == "routine":
            return _lab_routine(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
```

In `vesper/cli.py`, replace:
```python

def _day_summary(session) -> list[str]:
```
with:
```python

def _lab_routine(args, home) -> int:
    """A resident's routine from the earlier work's schedules: a persona day or an ARAS day (spec §5a)."""
    from vesper.lab.convert import ConvertError, from_aras, from_persona, routine_yaml
    from vesper.lab.routine import RoutineError, load_routine
    src = Path(args.file)
    try:
        overrides = {}
        for item in args.room:
            c, sep, room = item.partition("=")
            if not (sep and c and room):
                raise ConvertError(f"--room {item}: give it as canonical=room, such as living_room=hall")
            overrides[c] = room
        if args.source == "persona":
            try:
                schedule = json.loads(src.read_text())
            except (OSError, ValueError) as e:
                raise ConvertError(f"{src}: cannot read the schedule: {e}") from None
            tasks, notes = from_persona(schedule, home.map, overrides)
            name, label = src.stem, f"persona schedule {src.name}"
        else:
            try:
                rows = [line.split() for line in src.read_text().splitlines() if line.strip()]
            except OSError as e:
                raise ConvertError(f"{src}: cannot read the ARAS day: {e.strerror}") from None
            tasks, notes = from_aras(rows, args.resident, home.map, overrides)
            house = (src.parent.name.split() or ["x"])[-1].lower()
            name = f"aras-{house}-{src.stem.lower().replace('_', '')}-r{args.resident}"
            label = f"ARAS {src.parent.name}/{src.name}, resident {args.resident}"
    except ConvertError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not tasks:
        print(f"FAILED: {src.name} gives no task for this home", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else home.path.parent / "routines" / f"{name}.yaml"
    if out.exists():
        print(f"FAILED: {out} exists; a routine is never overwritten (choose another --out)", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.tmp.yaml")  # checked before it takes the routine's name
    tmp.write_text(routine_yaml(tasks, source=label, notes=notes))
    try:
        load_routine(tmp, home.map)
    except RoutineError as e:  # a converter defect, never the user's: say so and leave nothing behind
        tmp.unlink()
        print(f"FAILED: the converted routine does not load: {e}", file=sys.stderr)
        return 1
    tmp.rename(out)
    for n in notes:
        print(n)
    print(f"{len(tasks)} tasks: {out}")
    return 0


def _day_summary(session) -> list[str]:
```

In `vesper/cli.py`, replace:
```python
    lab_day.add_argument("--trace", help="the trace file (default: traces/<time>-day-<pid>.jsonl next to the home)")
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
```
with:
```python
    lab_day.add_argument("--trace", help="the trace file (default: traces/<time>-day-<pid>.jsonl next to the home)")
    lab_routine = lab_actions.add_parser("routine", help="a resident's routine from a persona or an ARAS day")
    lab_routine.add_argument("source", choices=("persona", "aras"))
    lab_routine.add_argument("file", help="a persona schedule (.json) or an ARAS day (DAY_n.txt)")
    lab_routine.add_argument("--home", required=True, help="the home whose rooms the routine uses")
    lab_routine.add_argument("--catalog", default="catalog/vds")
    lab_routine.add_argument("--resident", type=int, default=1, help="ARAS: resident 1 or 2")
    lab_routine.add_argument("--room", action="append", default=[], metavar="CANONICAL=ROOM",
                             help="map a canonical room (living_room, kitchen, bedroom, bathroom, entryway, hallway)")
    lab_routine.add_argument("--out", help="the routine file (default: routines/<name>.yaml next to the home)")
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `242 passed, 1 skipped` in the repository's working copy (`239 passed, 4 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/convert.py vesper/cli.py tests/lab/test_convert.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): vesper lab routine: a resident's routine from a persona schedule or an ARAS day, its rooms and choices noted

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The home's automations without Home Assistant: VESPER's engine

**Files:**
- Create: `vesper/lab/automations.py`
- Modify: `vesper/lab/session.py` (`start(..., automations=False)`; the meta record's `automations`; `automation_command`, `automation_skipped`; `_on_action` writes an `automation` record for the engine's commands; `replay` runs the engine when the trace did)
- Modify: `vesper/cli.py` (`vesper lab day` runs the engine and names what it refused)
- Create tests: `tests/lab/test_automations.py`; modify `tests/lab/test_cli_lab.py` (the W3a test that a day does not run the automations becomes a test that it does)

**Interfaces:**
- Consumes: `device_discovery(vds, ieee=, friendly_name=)` returning `(topic, payload, expected, skipped)` with `payload["cmps"]` and `ExpectedEntity(entity_id, platform, device_class, unit, feature, controllable)` (`vesper/exposure/ha_discovery.py`); `ieee_address(twin_id)`, `flatten(command)` (`vesper/exposure/z2m_bridge.py`); `Runtime.subscribe`, `Runtime.state`, `Runtime.platform_set`, `Runtime.on_action`, `Runtime.twins` (`vesper/twins/runtime.py`); `LabHome.automations` (a path or None) and `LabHome.start_time` (W2c/W3a).
- Produces:
  - in `vesper/lab/automations.py`: `AutomationError`; `entity_ids(home) -> {device: {feature: entity_id}}`; `Engine(session)` with `.automations` (each with `.name`), `.refused` (`[{"automation", "reason"}]`), `start()`;
  - in `vesper/lab/session.py`: `Session.automations` (the engine or None); `Session.automation_command(automation, trigger, service, entity, twin_id, feature, value) -> bool`; `Session.automation_skipped(automation, trigger, reason)`; trace records `automation` (`automation`, `trigger`, `service`, `entity`, `device`, `feature`, `value`, `accepted`[, `reason`]) and `automation_skipped`; the meta record's `automations` = {`engine`: `vesper`, `loaded`, `refused`};
  - `vesper lab day`: `<file>: <n> of <m> automations run by VESPER's engine (Home Assistant is not in the loop)`, a `  refused: <automation>: <reason>` line each, and `<r> device reports, <f> rule firings, <a> automation commands; trace <path>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_automations.py`:
```python
"""VESPER's built-in automation engine (spec §5b): a home's Home Assistant automations, run without Home Assistant
on the logical clock, seeing what Home Assistant would see (the devices' reports) and acting as it would (the
commands its MQTT entities send)."""
import pytest

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.automations import entity_ids
from vesper.lab.home import load_lab_home
from vesper.lab.rules import is_on
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock

H = 3600


def lab(tmp_path, automations: str, start="07:00"):
    (tmp_path / "automations.yaml").write_text(automations)
    text = HOME.replace('start_time: "07:30"', f'start_time: "{start}"') + "    automations: automations.yaml\n"
    home = load_lab_home(write_home(tmp_path, text), catalog())
    ids = entity_ids(home)
    s = Session(home, clock=LogicalClock())
    return home, ids, s


def lamp_on(s):
    return is_on(s.rules.twin("bedroom_lamp"))


def fired(s):
    """The automation engine's commands in a session or in a list of trace records."""
    return [r for r in getattr(s, "records", s) if r["kind"] == "automation"]


HALL_LAMP = """\
- id: hall_lamp
  alias: Bedroom lamp on when someone is in the hall
  triggers:
    - trigger: state
      entity_id: {motion}
      to: "on"
  actions:
    - action: light.turn_on
      target:
        entity_id: {lamp}
"""


def test_entities_are_named_as_home_assistant_names_them_through_the_bridge(tmp_path):
    home, ids, _ = lab(tmp_path, "[]\n")
    assert ids["hall_motion"]["occupancy"].startswith("binary_sensor.v") and ids["bedroom_lamp"]["state"].startswith(
        "light.v")
    assert "front_lock" not in ids  # Matter: not on VESPER's hub yet (plan W2d)


def test_walking_into_the_hall_lets_the_automation_turn_the_lamp_on(tmp_path):
    _, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    assert s.records[0]["automations"]["engine"] == "vesper" and s.records[0]["automations"]["loaded"] == [
        "Bedroom lamp on when someone is in the hall"]
    s.spawn("player", "female_0", (5.0, 1.0))  # in the kitchen
    assert not lamp_on(s)
    s.walk_to((1.5, 3.0))  # into the hall
    s.clock.advance(10)
    assert lamp_on(s)
    (f,) = fired(s)
    assert (f["automation"], f["device"], f["feature"], f["value"], f["accepted"]) == (
        "Bedroom lamp on when someone is in the hall", "bedroom_lamp", "state", "ON", True)
    assert not [r for r in s.records if r["kind"] == "input" and r["actor"] == "hub"]  # not a hub input


def test_conditions_for_and_time_triggers_follow_home_assistant(tmp_path):
    _, ids, s = lab(tmp_path, "", start="06:58")
    motion, lamp = ids["hall_motion"]["occupancy"], ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: morning light
  triggers: [{{trigger: time, at: "07:00:00"}}]
  actions:
    - {{action: light.turn_on, target: {{entity_id: {lamp}}}}}
    - {{delay: "00:05:00"}}
    - {{action: light.turn_off, target: {{entity_id: {lamp}}}}}
- alias: hall lamp at night only
  triggers: [{{trigger: state, entity_id: {motion}, to: "on", for: {{minutes: 1}}}}]
  conditions: [{{condition: time, after: "20:00:00"}}]
  actions: [{{action: light.toggle, target: {{entity_id: {lamp}}}}}]
""")
    s.start(spawn_player=True, automations=True)
    s.clock.advance(2 * 60 + 1)  # 07:00:01
    assert lamp_on(s)
    s.clock.advance(5 * 60)  # 07:05:01: the delay ran out
    assert not lamp_on(s)
    s.walk_to((1.5, 3.0))
    s.clock.advance(3 * 60)
    assert not lamp_on(s)  # in the hall for minutes, but before 20:00
    assert [f["automation"] for f in fired(s)] == ["morning light", "morning light"]


def test_an_automation_the_engine_cannot_run_is_refused_by_name_and_the_others_still_run(tmp_path):
    _, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]) + """\
- alias: at sunset
  triggers: [{trigger: sun, event: sunset}]
  actions: [{action: light.turn_on, target: {entity_id: light.nowhere}}]
- alias: unknown lamp
  triggers: [{trigger: time, at: "08:00"}]
  actions: [{action: light.turn_on, target: {entity_id: light.nowhere}}]
""")
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 1.0))
    refused = s.records[0]["automations"]["refused"]
    assert refused == [{"automation": "at sunset", "reason": "trigger 'sun' is not supported"},
                       {"automation": "unknown lamp", "reason": "entity light.nowhere is not one of this home's"}]
    s.walk_to((1.5, 3.0))
    s.clock.advance(10)
    assert lamp_on(s)


def test_a_day_with_automations_replays_exactly_without_doubling_them(tmp_path):
    home, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 1.0))
    s.walk_to((1.5, 3.0))
    s.clock.advance(120)
    s.stop()
    again = replay(s.records, home)
    assert compare_reports(s.records, again) is None
    assert len(fired(again)) == len(fired(s)) == 1


@pytest.mark.parametrize("bad, message", [
    ("- alias: x\n  triggers: [{trigger: state, entity_id: binary_sensor.a, to: 'on'}]\n"
     "  actions: []\n  mode: queued\n", "mode 'queued' is not supported"),
    ("{not: a list}\n", "automations.yaml: a list of automations"),
])
def test_a_file_the_engine_cannot_read_is_named(tmp_path, bad, message):
    _, _, s = lab(tmp_path, bad)
    s.start(automations=True)
    meta = s.records[0]["automations"]
    assert any(message in r["reason"] for r in meta["refused"])


def test_a_for_starts_again_when_the_state_changes_before_it_has_held(tmp_path):
    _, ids, s = lab(tmp_path, "")
    lamp = ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: lamp off after five minutes
  triggers: [{{trigger: state, entity_id: {lamp}, to: "on", for: "00:05:00"}}]
  actions: [{{action: light.turn_off, target: {{entity_id: {lamp}}}}}]
""")
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 4.4))  # by the lamp
    s.interact("bedroom_lamp")  # on at 0 min
    s.wait(120)
    s.interact("bedroom_lamp")  # off at 2 min
    s.wait(60)
    s.interact("bedroom_lamp")  # on again at 3 min
    s.wait(4 * 60 + 30)  # 7.5 min: on for 4.5 min only
    assert lamp_on(s) and not fired(s)
    s.wait(60)  # 8.5 min: on for 5.5 min
    assert not lamp_on(s) and len(fired(s)) == 1


def test_mode_single_drops_a_trigger_while_the_automation_runs(tmp_path):
    _, ids, s = lab(tmp_path, "", start="06:58")
    lamp = ids["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(f"""\
- alias: blink
  triggers: [{{trigger: time, at: "07:00"}}, {{trigger: time, at: "07:01"}}]
  actions:
    - {{action: light.turn_on, target: {{entity_id: {lamp}}}}}
    - {{delay: {{minutes: 2}}}}
    - {{action: light.turn_off, target: {{entity_id: {lamp}}}}}
""")
    s.start(spawn_player=False, automations=True)
    s.wait(6 * 60)
    assert [r["value"] for r in fired(s)] == ["ON", "OFF"]
    (skip,) = [r for r in s.records if r["kind"] == "automation_skipped"]
    assert (skip["automation"], skip["reason"]) == ("blink", "already running (mode single)")
```

In `tests/lab/test_cli_lab.py`, replace:
```python

def test_a_day_says_that_the_home_s_automations_do_not_run_without_home_assistant(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
```
with:
```python

def test_a_day_runs_the_home_s_automations_with_vesper_s_engine_and_names_the_refused(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
```

In `tests/lab/test_cli_lab.py`, replace:
```python
    cat, home = with_alex(tmp_path)
    (home.parent / "automations.yaml").write_text("[]\n")
    home.write_text(home.read_text() + "automations: automations.yaml\n")
```
with:
```python
    cat, home = with_alex(tmp_path)
    (home.parent / "automations.yaml").write_text("""\
- alias: wake-up light
  triggers: [{trigger: time, at: "07:10"}]
  actions: [{action: light.turn_on, target: {entity_id: light.nowhere}}]
- alias: at sunset
  triggers: [{trigger: sun, event: sunset}]
  actions: []
""")
    home.write_text(home.read_text() + "automations: automations.yaml\n")
```

In `tests/lab/test_cli_lab.py`, replace:
```python
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--hours", "1"]) == 0
    assert "automations.yaml does not run in a day: Home Assistant is not in the loop" in capsys.readouterr().out
```
with:
```python
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--hours", "1"]) == 0
    out = capsys.readouterr().out
    assert "automations.yaml: 0 of 2 automations run by VESPER's engine (Home Assistant is not in the loop)" in out
    assert "refused: wake-up light: entity light.nowhere is not one of this home's" in out
    assert "refused: at sunset: trigger 'sun' is not supported" in out
    assert "0 automation commands" in out
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_automations.py tests/lab/test_cli_lab.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.automations'`

- [ ] **Step 3: Implement**

Create `vesper/lab/automations.py`:
```python
"""VESPER's built-in automation engine (spec §5b): a home's Home Assistant automations, run without Home Assistant.

Scripted sessions on the logical clock (`vesper lab day`) have no Home Assistant, so this engine runs the home's
`automations.yaml` instead, and the trace says so (the meta record's `automations`). It sees what Home Assistant
would see and acts as Home Assistant would:
- an entity's state comes from the device's *reports* (what reaches Home Assistant through the bridge), mapped
  as the bridge's MQTT discovery maps it (ON/OFF, locked/unlocked, a sensor's value);
- entity ids are the ones that discovery creates (`entity_ids`);
- a service call sends the command that entity's MQTT config sends (a light's JSON `state`, a switch's
  `payload_on`, a lock's `payload_lock`), applied as the bridge applies a command.
Supported: triggers `state` (to, from, for), `numeric_state` (above, below, for) and `time` (at); conditions
`state`, `numeric_state` and `time` (after, before); actions `light.*`, `switch.*` (turn_on, turn_off, toggle),
`lock.lock`/`lock.unlock` and `delay`; mode `single`. An automation using anything else is refused by name, never
run in part; the others run. Every command it sends is an `automation` record, not an input: a replay runs the
engine again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import yaml

from vesper.exposure.ha_discovery import device_discovery
from vesper.exposure.z2m_bridge import flatten, ieee_address

DAY_S = 86400
SERVICES = {"light": ("turn_on", "turn_off", "toggle"), "switch": ("turn_on", "turn_off", "toggle"),
            "lock": ("lock", "unlock")}


class AutomationError(ValueError):
    """An automation the engine cannot run; the message says what."""


@dataclass
class Entity:
    entity_id: str
    device: str
    twin_id: str
    feature: str
    platform: str
    cfg: dict


@dataclass
class Automation:
    name: str
    triggers: list
    conditions: list
    actions: list
    running: bool = field(default=False)


def _discovered(home):
    """(device name, twin id, discovery payload, expected entities) for each Zigbee device on the hub."""
    for d in home.devices:
        if "zigbee" not in d.record["binding"]:
            continue  # Matter devices are not on VESPER's hub yet (plan W2d)
        twin_id = f"{d.product}@{d.name}"
        _, payload, expected, _ = device_discovery(d.record, ieee=ieee_address(twin_id), friendly_name=d.name)
        yield d.name, twin_id, payload, expected


def entity_ids(home) -> dict[str, dict[str, str]]:
    """Device name -> feature -> the entity id Home Assistant creates for it (what automations name)."""
    return {name: {e.feature: e.entity_id for e in expected} for name, _, _, expected in _discovered(home)}


def _seconds(value, where: str) -> float:
    """A Home Assistant duration: 'HH:MM:SS', a mapping of hours/minutes/seconds, or a number of seconds."""
    if isinstance(value, bool):
        raise AutomationError(f"{where}: {value!r} is not a duration")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict):
        unknown = set(value) - {"hours", "minutes", "seconds", "milliseconds"}
        if unknown:
            raise AutomationError(f"{where}: {sorted(unknown)[0]!r} is not a duration field")
        return (float(value.get("hours", 0)) * 3600 + float(value.get("minutes", 0)) * 60
                + float(value.get("seconds", 0)) + float(value.get("milliseconds", 0)) / 1000)
    return _clock(value, where)


def _clock(value, where: str) -> float:
    try:
        parts = [float(p) for p in str(value).split(":")]
        if len(parts) not in (2, 3):
            raise ValueError
    except ValueError:
        raise AutomationError(f"{where}: {value!r} is not a time (HH:MM or HH:MM:SS)") from None
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) == 3 else 0)


def _states(value) -> list[str]:
    """A state or a list of states as Home Assistant compares them (YAML's on/off booleans are 'on'/'off')."""
    values = value if isinstance(value, list) else [value]
    return [("on" if v else "off") if isinstance(v, bool) else str(v) for v in values]


def _entities(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


class Engine:
    def __init__(self, session):
        self.session, self.home, self.runtime = session, session.home, session.runtime
        self.entities: dict[str, Entity] = {}
        for name, twin_id, payload, expected in _discovered(self.home):
            for e in expected:
                oid = e.entity_id.split(".", 1)[1]
                self.entities[e.entity_id] = Entity(e.entity_id, name, twin_id, e.feature, e.platform,
                                                    payload["cmps"].get(oid, {}))
        self.automations: list[Automation] = []
        self.refused: list[dict] = []
        self._state: dict[str, str] = {}
        self._changed: dict[str, int] = {}  # entity -> how often its state changed: a `for:` waits on it
        self._load()

    # -- the file -----------------------------------------------------------------------------
    def _load(self) -> None:
        path = self.home.automations
        name = path.name if path else "automations.yaml"
        try:
            raw = yaml.safe_load(path.read_text()) if path else []
        except OSError as e:
            self.refused.append({"automation": name, "reason": f"{name}: cannot read it: {e.strerror}"})
            return
        except yaml.YAMLError as e:
            self.refused.append({"automation": name, "reason": f"{name}: not valid YAML: {e}"})
            return
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            self.refused.append({"automation": name, "reason": f"{name}: a list of automations"})
            return
        for k, a in enumerate(raw):
            label = (a.get("alias") or a.get("id") or f"automation {k + 1}") if isinstance(a, dict) else \
                f"automation {k + 1}"
            try:
                self.automations.append(self._parse(a, label))
            except AutomationError as e:
                self.refused.append({"automation": label, "reason": str(e)})

    def _parse(self, a, label: str) -> Automation:
        if not isinstance(a, dict):
            raise AutomationError("an automation is a mapping")
        mode = a.get("mode", "single")
        if mode != "single":
            raise AutomationError(f"mode {mode!r} is not supported (single only)")
        triggers = [self._trigger(t) for t in _list(a.get("triggers", a.get("trigger")), "triggers")]
        if not triggers:
            raise AutomationError("it has no trigger")
        conditions = [self._condition(c) for c in _list(a.get("conditions", a.get("condition")), "conditions")]
        actions = [self._action(x) for x in _list(a.get("actions", a.get("action")), "actions")]
        return Automation(label, triggers, conditions, actions)

    def _entity(self, entity_id: str) -> Entity:
        e = self.entities.get(entity_id)
        if e is None:
            raise AutomationError(f"entity {entity_id} is not one of this home's")
        return e

    def _trigger(self, t) -> dict:
        kind = t.get("trigger", t.get("platform")) if isinstance(t, dict) else None
        if kind == "state":
            ents = [self._entity(x).entity_id for x in _entities(t.get("entity_id"))]
            out = {"kind": "state", "entities": ents, "to": _states(t["to"]) if "to" in t else None,
                   "from": _states(t["from"]) if "from" in t else None}
        elif kind == "numeric_state":
            ents = [self._entity(x).entity_id for x in _entities(t.get("entity_id"))]
            out = {"kind": "numeric_state", "entities": ents, "above": t.get("above"), "below": t.get("below")}
        elif kind == "time":
            return {"kind": "time", "at": [_clock(x, "at") for x in (t["at"] if isinstance(t.get("at"), list)
                                                                       else [t.get("at")])]}
        else:
            raise AutomationError(f"trigger {kind!r} is not supported")
        out["for"] = _seconds(t["for"], "for") if "for" in t else 0.0
        return out

    def _condition(self, c) -> dict:
        kind = c.get("condition") if isinstance(c, dict) else None
        if kind == "state":
            return {"kind": "state", "entities": [self._entity(x).entity_id for x in _entities(c.get("entity_id"))],
                    "state": _states(c.get("state"))}
        if kind == "numeric_state":
            return {"kind": "numeric_state",
                    "entities": [self._entity(x).entity_id for x in _entities(c.get("entity_id"))],
                    "above": c.get("above"), "below": c.get("below")}
        if kind == "time":
            if "weekday" in c:
                raise AutomationError("condition time: weekday is not supported")
            return {"kind": "time", "after": _clock(c["after"], "after") if "after" in c else None,
                    "before": _clock(c["before"], "before") if "before" in c else None}
        raise AutomationError(f"condition {kind!r} is not supported")

    def _action(self, x) -> dict:
        if not isinstance(x, dict):
            raise AutomationError("an action is a mapping")
        if "delay" in x:
            return {"kind": "delay", "seconds": _seconds(x["delay"], "delay")}
        service = x.get("action", x.get("service"))
        domain, _, verb = str(service).partition(".")
        if verb not in SERVICES.get(domain, ()):
            raise AutomationError(f"action {service!r} is not supported")
        target = x.get("target") or {}
        ents = _entities(target.get("entity_id") or x.get("entity_id"))
        if not ents:
            raise AutomationError(f"action {service}: no target entity")
        for e in ents:
            if self._entity(e).platform != domain:
                raise AutomationError(f"action {service}: {e} is not a {domain}")
        data = dict(x.get("data") or {})
        unknown = set(data) - ({"brightness", "brightness_pct"} if service == "light.turn_on" else set())
        if unknown:
            raise AutomationError(f"action {service}: data {sorted(unknown)[0]!r} is not supported")
        brightness = data.get("brightness")
        if "brightness_pct" in data:  # the light service turns a percentage into 0..255
            brightness = round(float(data["brightness_pct"]) * 255 / 100)
        return {"kind": "call", "service": service, "entities": ents, "brightness": brightness}

    # -- running ------------------------------------------------------------------------------
    def start(self) -> None:
        clock = self.session.clock
        for e in self.entities.values():
            self._state[e.entity_id] = self._state_of(e, self.runtime.state(e.twin_id).get(e.feature))
        self.runtime.subscribe(self._on_report)
        self._midnight = clock.now() - self.home.start_time
        for day in (0, 1):
            self._schedule_day(day)
        clock.call_at(self._midnight + DAY_S, lambda: self._next_day(2))

    def _schedule_day(self, day: int) -> None:
        now = self.session.clock.now()
        for a in self.automations:
            for t in a.triggers:
                if t["kind"] == "time":
                    for at in t["at"]:
                        when = self._midnight + day * DAY_S + at
                        if when >= now:
                            self.session.clock.call_at(when, lambda a=a, at=at: self._fire(a, f"time {at:g}"))

    def _next_day(self, day: int) -> None:
        self._schedule_day(day)
        self.session.clock.call_at(self._midnight + day * DAY_S, lambda: self._next_day(day + 1))

    def _state_of(self, e: Entity, value) -> str:
        if value is None:
            return "unknown"
        f = self.runtime.twins[e.twin_id].features.get(e.feature, {})
        if e.platform in ("binary_sensor", "switch", "light"):
            return "on" if value == f.get("value_on", True) else "off"
        if e.platform == "lock":
            return "locked" if value == f.get("value_on") else "unlocked"
        return str(value)

    def _on_report(self, event) -> None:
        for e in self.entities.values():
            if e.twin_id != event.twin_id or e.feature != event.feature:
                continue
            old, new = self._state.get(e.entity_id), self._state_of(e, event.value)
            if new == old:
                continue
            self._state[e.entity_id] = new
            self._changed[e.entity_id] = self._changed.get(e.entity_id, 0) + 1
            for a in self.automations:
                for t in a.triggers:
                    if e.entity_id in t.get("entities", ()) and self._matches(t, old, new):
                        self._after(t, e.entity_id, a, f"{t['kind']} {e.entity_id} {old} -> {new}")

    def _matches(self, t: dict, old: str, new: str) -> bool:
        if t["kind"] == "state":
            return (t["to"] is None or new in t["to"]) and (t["from"] is None or old in t["from"])
        return self._in_range(t, new) and not self._in_range(t, old)  # numeric_state: entering the range

    @staticmethod
    def _in_range(t: dict, state: str) -> bool:
        try:
            v = float(state)
        except (TypeError, ValueError):
            return False
        return (t.get("above") is None or v > float(t["above"])) and (t.get("below") is None or v < float(t["below"]))

    def _after(self, t: dict, entity: str, a: Automation, why: str) -> None:
        if not t["for"]:
            self._fire(a, why)
            return
        clock, seen = self.session.clock, self._changed.get(entity, 0)

        def held():  # the state has held for the whole `for`: nothing changed it since
            if self._changed.get(entity, 0) == seen:
                self._fire(a, f"{why} for {t['for']:g} s")
        clock.call_at(clock.now() + t["for"], held)

    def _fire(self, a: Automation, why: str) -> None:
        if a.running:  # Home Assistant: "Already running" (mode single), and the trigger is dropped
            self.session.automation_skipped(a.name, why, "already running (mode single)")
            return
        if not all(self._holds(c) for c in a.conditions):
            return
        a.running = True
        self._run(a, list(a.actions), why)

    def _holds(self, c: dict) -> bool:
        if c["kind"] == "state":
            return all(self._state.get(e) in c["state"] for e in c["entities"])
        if c["kind"] == "numeric_state":
            return all(self._in_range(c, self._state.get(e, "")) for e in c["entities"])
        now = (self.session.clock.now() - self._midnight) % DAY_S
        after, before = c["after"], c["before"]
        if after is not None and before is not None and after > before:  # across midnight
            return now >= after or now < before
        return (after is None or now >= after) and (before is None or now < before)

    def _run(self, a: Automation, actions: list, why: str) -> None:
        while actions:
            x = actions.pop(0)
            if x["kind"] == "delay":
                clock = self.session.clock
                clock.call_at(clock.now() + x["seconds"], lambda: self._run(a, actions, why))
                return
            for entity_id in x["entities"]:
                self._call(a, x, self.entities[entity_id], why)
        a.running = False

    def _call(self, a: Automation, x: dict, e: Entity, why: str) -> None:
        domain, verb = x["service"].split(".")
        if verb == "toggle":
            verb = "turn_off" if self._state.get(e.entity_id) == "on" else "turn_on"
        if domain == "light":  # the JSON schema light's command (HA mqtt/light/schema_json.py)
            command = {"state": "ON" if verb == "turn_on" else "OFF"}
            brightness = x["brightness"]
            if verb == "turn_on" and brightness is not None and e.cfg.get("brightness"):
                scale = e.cfg.get("brightness_scale", 255)
                command["brightness"] = max(min(round(brightness / 255 * scale), scale), 1)
        elif domain == "switch":
            command = json.loads(e.cfg["payload_on" if verb == "turn_on" else "payload_off"])
        else:
            command = json.loads(e.cfg["payload_lock" if verb == "lock" else "payload_unlock"])
        for feature, value in flatten(command):
            self.session.automation_command(a.name, why, x["service"], e.entity_id, e.twin_id, feature, value)


def _list(value, where: str) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise AutomationError(f"{where}: a list")
    return value
```

In `vesper/lab/session.py`, replace:
```python
The trace (`vesper-trace/1`) is one JSON object per line: a `meta` line first, then `input` (what a person
did), `rule` (a rule that fired), `world` (doors, rooms, arrivals) and `report` (what a device reported, as the
platform sees it), each with its world time `t`. On the logical clock, replaying the inputs reproduces the
device reports exactly (spec success criterion 4); `replay` and `compare_reports` check it.
```
with:
```python
The trace (`vesper-trace/1`) is one JSON object per line: a `meta` line first, then `input` (what a person
did), `rule` (a rule that fired), `automation` (a command from VESPER's built-in automation engine), `world`
(doors, rooms, arrivals) and `report` (what a device reported, as the platform sees it), each with its world
time `t`. On the logical clock, replaying the inputs reproduces the
device reports exactly (spec success criterion 4); `replay` and `compare_reports` check it.
```

In `vesper/lab/session.py`, replace:
```python
        self.residents: dict = {}  # name -> the scripted Resident living in this session
        self.world = world or MapWorld(home.map, self.clock)
```
with:
```python
        self.residents: dict = {}  # name -> the scripted Resident living in this session
        self.automations = None  # VESPER's automation engine, when the session runs the home's automations
        self._acting: dict | None = None  # the automation whose command is being applied
        self.world = world or MapWorld(home.map, self.clock)
```

In `vesper/lab/session.py`, replace:
```python
    # -- life cycle ---------------------------------------------------------------------------
    def start(self, spawn_player: bool = True, residents: bool = True) -> None:
        """Start the home: the home's residents enter it and begin what their routine has them do now, then the
```
with:
```python
    # -- life cycle ---------------------------------------------------------------------------
    def start(self, spawn_player: bool = True, residents: bool = True, automations: bool = False) -> None:
        """Start the home: the home's residents enter it and begin what their routine has them do now, then the
```

In `vesper/lab/session.py`, replace:
```python
        player enters. A replay starts the residents (it lives their day again) but not the player, whose actions
        are its inputs; starting the residents first keeps their first actions before the player's, in both."""
        self._write({"kind": "meta", "format": TRACE_FORMAT, "clock": self.clock_kind, "home": str(self.home.path),
                     "scene": self.home.scene,
                     "seed": self.home.seed, "start_time": self.home.start_time,
                     "devices": {d.name: d.product for d in self.home.devices}})
        self.runtime.subscribe(self._on_report)
```
with:
```python
        player enters. A replay starts the residents (it lives their day again) but not the player, whose actions
        are its inputs; starting the residents first keeps their first actions before the player's, in both.

        `automations`: run the home's automations with VESPER's engine (a session with no Home Assistant); the
        meta record says which ran and which were refused, and a replay runs them again."""
        meta = {"kind": "meta", "format": TRACE_FORMAT, "clock": self.clock_kind, "home": str(self.home.path),
                "scene": self.home.scene,
                "seed": self.home.seed, "start_time": self.home.start_time,
                "devices": {d.name: d.product for d in self.home.devices}}
        if automations:
            from vesper.lab.automations import Engine
            self.automations = Engine(self)
            meta["automations"] = {"engine": "vesper", "loaded": [a.name for a in self.automations.automations],
                                   "refused": self.automations.refused}
        self._write(meta)
        self.runtime.subscribe(self._on_report)
```

In `vesper/lab/session.py`, replace:
```python
        self.rules.start()
        if residents and self.home.residents:
```
with:
```python
        self.rules.start()
        if self.automations is not None:
            self.automations.start()
        if residents and self.home.residents:
```

In `vesper/lab/session.py`, replace:
```python

    # -- the trace ----------------------------------------------------------------------------
```
with:
```python

    def automation_command(self, automation: str, trigger: str, service: str, entity: str, twin_id: str,
                           feature: str, value) -> bool:
        """The automation engine sends a command as Home Assistant would, through the bridge (`platform_set`)."""
        self._acting = {"automation": automation, "trigger": trigger, "service": service, "entity": entity}
        try:
            return self.runtime.platform_set(twin_id, feature, value)
        finally:
            self._acting = None

    def automation_skipped(self, automation: str, trigger: str, reason: str) -> None:
        self._write({"kind": "automation_skipped", "automation": automation, "trigger": trigger, "reason": reason})

    # -- the trace ----------------------------------------------------------------------------
```

In `vesper/lab/session.py`, replace:
```python
    def _on_action(self, a) -> None:
        """A command from the hub (through a bridge) is an input: replay applies it again, with no hub."""
        if a.actor == "platform":
```
with:
```python
    def _on_action(self, a) -> None:
        """A command from the hub (through a bridge) is an input: replay applies it again, with no hub. A command
        from VESPER's automation engine is not: a replay runs the engine again."""
        if a.actor == "platform":
```

In `vesper/lab/session.py`, replace:
```python
            outcome = {"accepted": a.accepted} if a.accepted else {"accepted": False, "reason": a.reason}
            self._input("hub", "command", {"device": self._names.get(a.twin_id, a.twin_id), "feature": a.feature,
```
with:
```python
            outcome = {"accepted": a.accepted} if a.accepted else {"accepted": False, "reason": a.reason}
            if self._acting is not None:
                self._write({"kind": "automation", **self._acting, "device": self._names.get(a.twin_id, a.twin_id),
                             "feature": a.feature, "value": a.value, **outcome})
                return
            self._input("hub", "command", {"device": self._names.get(a.twin_id, a.twin_id), "feature": a.feature,
```

In `vesper/lab/session.py`, replace:
```python
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=True)
    for k, r in enumerate(records):
```
with:
```python
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False, residents=True, automations=bool(meta.get("automations")))
    for k, r in enumerate(records):
```

In `vesper/cli.py`, replace:
```python
    trace = Path(args.trace) if args.trace else _new_trace(traces, "day")
    if home.automations is not None:
        print(f"note: {home.automations.name} does not run in a day: Home Assistant is not in the loop")
    session = Session(home, trace=trace)
```
with:
```python
    trace = Path(args.trace) if args.trace else _new_trace(traces, "day")
    session = Session(home, trace=trace)
```

In `vesper/cli.py`, replace:
```python
    session = Session(home, trace=trace)
    session.start(spawn_player=False)
    session.clock.advance(args.hours * 3600)
```
with:
```python
    session = Session(home, trace=trace)
    session.start(spawn_player=False, automations=home.automations is not None)
    if session.automations is not None:  # Home Assistant's place is taken by VESPER's engine (spec §5b)
        engine = session.automations
        print(f"{home.automations.name}: {len(engine.automations)} of {len(engine.automations) + len(engine.refused)}"
              " automations run by VESPER's engine (Home Assistant is not in the loop)")
        for r in engine.refused:
            print(f"  refused: {r['automation']}: {r['reason']}")
    session.clock.advance(args.hours * 3600)
```

In `vesper/cli.py`, replace:
```python
    kinds = [r["kind"] for r in session.records]
    print(f"{kinds.count('report')} device reports, {kinds.count('rule')} rule firings; trace {trace}")
    return 0
```
with:
```python
    kinds = [r["kind"] for r in session.records]
    print(f"{kinds.count('report')} device reports, {kinds.count('rule')} rule firings, "
          f"{kinds.count('automation')} automation commands; trace {trace}")
    return 0
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `251 passed, 1 skipped` in the repository's working copy (`248 passed, 4 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/automations.py vesper/lab/session.py vesper/cli.py tests/lab/test_automations.py tests/lab/test_cli_lab.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): VESPER's automation engine runs the home's automations in a day without Home Assistant, and the trace says so

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: The comfort band: a resident changes the thermostat

**Files:**
- Modify: `vesper/lab/profile.py` (`comfort`, default `(20.0, 24.0)`)
- Modify: `vesper/lab/rules.py` (`HEAT_SETPOINTS`, `thermostat(twin)`, `hand_settings(twin)`, `Rules.adjust`, `Rules.within_reach`)
- Modify: `vesper/lab/session.py` (`Session.adjust`; `replay` applies a `set` input)
- Modify: `vesper/lab/resident.py` (the `comfort` step at a task's start and at every look; `_comfort`, `_set`, `_within`)
- Modify tests: `tests/lab/homes.py` (a thermostat product, `zigbee:acme:therm-1`, in the test catalog), `tests/lab/test_profile.py`; create `tests/lab/test_comfort.py`

**Interfaces:**
- Consumes: `Session.start`, `replay`, `compare_reports` (Task 3); `Resident` steps, `_why`, `_near`, `_devices_in`, `LOOK_S` (W3a); `Rules._can_touch`, `_done`, `INTERACT_M` (W2a); `zdef` (`tests/catalog/fixtures.py`), `zigbee_records` (`vesper/catalog/normalize/zigbee.py`).
- Produces:
  - `Profile.comfort: tuple[float, float]`; `load_profile` accepts `comfort: [low, high]` and names anything else (`<where>.comfort: <value> is not a comfort band (…)`);
  - in `vesper/lab/rules.py`: `thermostat(twin) -> dict | None` (`temperature`, `setpoint_feature`, `setpoint`, `range`, `mode_feature`, `mode`, `modes`); `hand_settings(twin) -> tuple`; `Rules.adjust(actor, target, feature, value) -> outcome` (`ok`, `kind`, `target`, `result`: `set <feature> to <value>`, `out of reach`, or `refused: <reason>`; `ValueError` `<device>: <feature> is not something a person sets by hand`); `Rules.within_reach(actor, target) -> bool`;
  - `Session.adjust(target, feature, value, actor="player", why=None) -> outcome` (an input `set` with args `target`, `feature`, `value`);
  - a resident's `set` inputs carry `why.reason` `too cold` or `too warm`.

- [ ] **Step 1: Write the failing tests**

In `tests/lab/homes.py`, replace:
```python

from tests.catalog.test_matter_normalize import records as matter_records
```
with:
```python

from tests.catalog.fixtures import zdef
from tests.catalog.test_matter_normalize import records as matter_records
```

In `tests/lab/homes.py`, replace:
```python
from tests.twins.conftest import records as zigbee_records
```
with:
```python
from tests.twins.conftest import records as zigbee_records
from vesper.catalog.normalize.zigbee import zigbee_records as normalize_zigbee
```

In `tests/lab/homes.py`, replace:
```python
LOCK = "matter:115f:0001"
```
with:
```python
LOCK = "matter:115f:0001"
THERMOSTAT = zdef("THERM-1", "Acme", [{"type": "climate", "features": [
    {"name": "local_temperature", "access": 5, "type": "numeric", "property": "local_temperature", "unit": "°C"},
    {"name": "current_heating_setpoint", "access": 7, "type": "numeric", "property": "current_heating_setpoint",
     "unit": "°C", "value_min": 5, "value_max": 30, "value_step": 0.5},
    {"name": "system_mode", "access": 7, "type": "enum", "property": "system_mode", "values": ["off", "auto", "heat"]},
]}])
```

In `tests/lab/homes.py`, replace:
```python
    out[LOCK] = matter_records()[LOCK]
    return out
```
with:
```python
    out[LOCK] = matter_records()[LOCK]
    for r in normalize_zigbee({"version": "26.123.0", "definitions": [THERMOSTAT]}):
        out[r["id"]] = r
    return out
```

In `tests/lab/test_profile.py`, replace:
```python
                                          ({"uses": {"COOKING": ["plug"]}}, "uses: 'COOKING' is not one of"),
                                          ({"snore": True}, "unknown setting 'snore'")])
def test_a_profile_names_its_mistakes(bad, message):
```
with:
```python
                                          ({"uses": {"COOKING": ["plug"]}}, "uses: 'COOKING' is not one of"),
                                          ({"snore": True}, "unknown setting 'snore'"),
                                          ({"comfort": [24, 20]}, r"comfort: \[24, 20\] is not a comfort band"),
                                          ({"comfort": 21}, "comfort: 21 is not a comfort band")])
def test_a_profile_names_its_mistakes(bad, message):
```

In `tests/lab/test_profile.py`, replace:
```python
    assert p.uses["EATING"] == ("plug",) and load_profile({"forget_light": 0}, "x").forget_light == 0
```
with:
```python
    assert p.uses["EATING"] == ("plug",) and load_profile({"forget_light": 0}, "x").forget_light == 0
    assert p.comfort == (20.0, 24.0) and load_profile({"comfort": [19, 22.5]}, "x").comfort == (19.0, 22.5)
```

Create `tests/lab/test_comfort.py`:
```python
"""The comfort band (spec §5a reactions): a resident who finds a room too cold or too warm, as its thermostat shows
it, changes the thermostat by hand. The rules' room temperature follows the time of day (21 °C +- 1.5, lowest at
03:00): 20.25 °C at 07:00, 22.45 °C at 14:00."""
import pytest

from tests.lab.homes import HOME, catalog, write_home
from tests.lab.test_resident import inputs, lab, task
from vesper.lab.home import load_lab_home
from vesper.lab.profile import Profile
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock


def thermostat(setpoint=17, mode="off"):
    return ('      - {product: "zigbee:acme:therm-1", name: bedroom_thermostat, at: {room: bedroom, xy: [6.8, 4.5], '
            f'mount: wall}}, state: {{current_heating_setpoint: {setpoint}, system_mode: "{mode}"}}}}\n')


def state(t):
    s = t.s.runtime.state(t.s.twin_ids["bedroom_thermostat"])
    return s["current_heating_setpoint"], s["system_mode"]


def sets(s):
    return [(r["args"]["feature"], r["args"]["value"], r["why"]["reason"]) for r in inputs(s) if r["op"] == "set"]


def test_a_room_too_cold_gets_its_heating_switched_on_and_turned_up(tmp_path):
    t = lab(tmp_path, [task("07:00", 60, "read", "LEISURE", "bedroom")], profile=Profile(comfort=(21.5, 24.0)),
            extra=thermostat())
    t.at("07:03")
    assert state(t) == (21.5, "heat")
    assert sets(t.s) == [("system_mode", "heat", "too cold"), ("current_heating_setpoint", 21.5, "too cold")]
    t.at("07:30")
    assert len(sets(t.s)) == 2  # the heating is on its way: nothing more to do


def test_a_room_too_warm_gets_its_heating_turned_down(tmp_path):
    t = lab(tmp_path, [task("14:00", 60, "work", "WORK", "bedroom")], start="14:00",
            profile=Profile(comfort=(19.0, 22.0)), extra=thermostat(setpoint=25, mode="heat"))
    t.at("14:03")
    assert state(t) == (22.0, "heat") and sets(t.s) == [("current_heating_setpoint", 22.0, "too warm")]


@pytest.mark.parametrize("tasks, comfort", [([task("07:00", 60, "read", "LEISURE", "bedroom")], (19.0, 24.0)),
                                            ([task("07:00", 60, "sleep in", "SLEEP", "bedroom")], (21.5, 24.0))])
def test_a_comfortable_or_sleeping_resident_leaves_the_thermostat_alone(tmp_path, tasks, comfort):
    t = lab(tmp_path, tasks, profile=Profile(comfort=comfort), extra=thermostat())
    t.at("07:30")
    assert state(t) == (17, "off") and sets(t.s) == []


def test_the_player_sets_a_thermostat_within_reach_and_the_replay_matches(tmp_path):
    home = load_lab_home(write_home(tmp_path, HOME + thermostat()), catalog())
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    s.spawn("player", "female_0", (3.5, 4.5))  # in the bedroom, across it from the thermostat
    assert s.adjust("bedroom_thermostat", "current_heating_setpoint", 23)["result"] == "out of reach"
    s.walk_to((6.5, 4.5))
    s.wait(10)
    outcome = s.adjust("bedroom_thermostat", "current_heating_setpoint", 23)
    assert outcome["ok"] and s.runtime.state(s.twin_ids["bedroom_thermostat"])["current_heating_setpoint"] == 23
    assert not s.adjust("bedroom_thermostat", "current_heating_setpoint", 40)["ok"]  # its range is 5 to 30
    with pytest.raises(ValueError, match="local_temperature is not something a person sets by hand"):
        s.adjust("bedroom_thermostat", "local_temperature", 30)
    s.wait(60)
    s.stop()
    assert compare_reports(s.records, replay(s.records, home)) is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_comfort.py tests/lab/test_profile.py -q 2>&1 | tail -1`

Expected: `8 failed, 5 passed`: `Profile` has no `comfort` (`TypeError: Profile.__init__() got an unexpected keyword argument 'comfort'`, and the profile's own three tests), and `Session` has no `adjust`

- [ ] **Step 3: Implement**

In `vesper/lab/profile.py`, replace:
```python

    profile: {forget_light: 0.2, forget_lock: 0.1, dark_lux: 50, bedtime: "20:00",
              uses: {EATING: [plug], LEISURE: [plug]}}
```
with:
```python

    profile: {forget_light: 0.2, forget_lock: 0.1, dark_lux: 50, bedtime: "20:00", comfort: [20, 24],
              uses: {EATING: [plug], LEISURE: [plug]}}
```

In `vesper/lab/profile.py`, replace:
```python
- bedtime: a SLEEP task that starts after this is going to bed;
- uses: per task category, the device categories the task switches on while it lasts and off after
```
with:
```python
- bedtime: a SLEEP task that starts after this is going to bed;
- comfort: the room temperatures (°C, low then high) the resident is comfortable in; outside them, it changes the
  room's thermostat;
- uses: per task category, the device categories the task switches on while it lasts and off after
```

In `vesper/lab/profile.py`, replace:
```python
DEFAULT_USES = {"EATING": ("plug",), "HOUSEHOLD": ("plug",), "LEISURE": ("plug",), "WORK": ("plug",)}
SETTINGS = ("forget_light", "forget_lock", "dark_lux", "bedtime", "uses")
```
with:
```python
DEFAULT_USES = {"EATING": ("plug",), "HOUSEHOLD": ("plug",), "LEISURE": ("plug",), "WORK": ("plug",)}
SETTINGS = ("forget_light", "forget_lock", "dark_lux", "bedtime", "comfort", "uses")
```

In `vesper/lab/profile.py`, replace:
```python
    bedtime: float = 20 * 3600  # seconds after midnight
    uses: dict = field(default_factory=lambda: dict(DEFAULT_USES))
```
with:
```python
    bedtime: float = 20 * 3600  # seconds after midnight
    comfort: tuple[float, float] = (20.0, 24.0)  # °C, low then high
    uses: dict = field(default_factory=lambda: dict(DEFAULT_USES))
```

In `vesper/lab/profile.py`, replace:
```python
            raise ProfileError(str(e)) from None
    if "uses" in spec:
```
with:
```python
            raise ProfileError(str(e)) from None
    if "comfort" in spec:
        v = spec["comfort"]
        if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int | float) and not isinstance(x, bool)
                                                           for x in v) and v[0] < v[1]):
            raise ProfileError(f"{where}.comfort: {v!r} is not a comfort band (two temperatures in °C, low then "
                               "high, such as [20, 24])")
        values["comfort"] = (float(v[0]), float(v[1]))
    if "uses" in spec:
```

In `vesper/lab/rules.py`, replace:
```python
            "illuminance": round(daylight + LAMP_LUX * lamps_on)}
```
with:
```python
            "illuminance": round(daylight + LAMP_LUX * lamps_on)}


HEAT_SETPOINTS = ("occupied_heating_setpoint", "current_heating_setpoint")


def thermostat(twin) -> dict | None:
    """What a person reads off a heating thermostat and what its buttons set, or None for another device: the
    room temperature it shows, its heating setpoint (feature, value, range) and its mode (feature, value, modes).
    Only the setpoint and the mode are set by hand."""
    feats, state = twin.features, twin.state
    sp = next((n for n in HEAT_SETPOINTS if n in feats and "set" in feats[n].get("access", ())), None)
    if sp is None:
        return None
    mode = feats.get("system_mode")
    hand = mode is not None and mode["type"] == "enum" and "set" in mode.get("access", ())
    return {"temperature": state.get("local_temperature"), "setpoint_feature": sp, "setpoint": state[sp],
            "range": feats[sp].get("range"), "mode_feature": "system_mode" if hand else None,
            "mode": state.get("system_mode"), "modes": list(mode.get("values") or []) if hand else []}


def hand_settings(twin) -> tuple[str, ...]:
    """The features a person sets by hand to a value (not a toggle): a thermostat's setpoint and mode."""
    t = thermostat(twin)
    return tuple(f for f in (t["setpoint_feature"], t["mode_feature"]) if f) if t else ()
```

In `vesper/lab/rules.py`, replace:
```python

    def _use(self, name: str, actor: str) -> str:
```
with:
```python

    def adjust(self, actor: str, target: str, feature: str, value) -> dict:
        """A person sets a device's value by hand (a thermostat's setpoint or mode), within reach."""
        if target not in self.devices:
            raise ValueError(f"unknown device {target!r}")
        if feature not in hand_settings(self.twin(target)) or self.devices[target].mount == "ceiling":
            raise ValueError(f"{target}: {feature} is not something a person sets by hand")
        if not self.within_reach(actor, target):
            return self._done(actor, {"ok": False, "kind": "device", "target": target, "result": "out of reach"})
        if self.runtime.operate(self.twin_ids[target], feature, value, actor=actor):
            return self._done(actor, {"ok": True, "kind": "device", "target": target,
                                      "result": f"set {feature} to {value}"})
        reason = self.runtime.actions[-1].reason if self.runtime.actions else "refused"
        return self._done(actor, {"ok": False, "kind": "device", "target": target, "result": f"refused: {reason}"})

    def within_reach(self, actor: str, target: str) -> bool:
        """A device a person standing where `actor` is could touch: near enough, on the same side of the wall."""
        p = self.world.pose(actor)
        return distance((p.x, p.z), self.devices[target].position) <= INTERACT_M and \
            self._can_touch(actor, "device", target)

    def _use(self, name: str, actor: str) -> str:
```

In `vesper/lab/session.py`, replace:
```python

    def wait(self, seconds: float) -> None:
```
with:
```python

    def adjust(self, target: str, feature: str, value, actor: str = PLAYER, why: dict | None = None) -> dict:
        """Set a device's value by hand (a thermostat's setpoint or mode), within reach."""
        began = self.clock.now()
        outcome = self.rules.adjust(actor, target, feature, value)
        self._input(actor, "set", {"target": target, "feature": feature, "value": value}, outcome=outcome, t=began,
                    **({"why": why} if why else {}))
        return outcome

    def wait(self, seconds: float) -> None:
```

In `vesper/lab/session.py`, replace:
```python
                s.interact(a["target"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "command":
```
with:
```python
                s.interact(a["target"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "set":
                s.adjust(a["target"], a["feature"], a["value"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "command":
```

In `vesper/lab/session.py`, replace:
```python
        except (ValueError, WorldError) as e:  # the home changed under the trace: that is the difference
            what = r["op"] + (f" {a['target']}" if r["op"] == "interact" and a.get("target") else "")
            s._write({"kind": "divergence", "input": k, "message": f"input {k} ({what} at t={r['t']:g}): {e}"})
```
with:
```python
        except (ValueError, WorldError) as e:  # the home changed under the trace: that is the difference
            what = r["op"] + (f" {a['target']}" if r["op"] in ("interact", "set") and a.get("target") else "")
            s._write({"kind": "divergence", "input": k, "message": f"input {k} ({what} at t={r['t']:g}): {e}"})
```

In `vesper/lab/resident.py`, replace:
```python
  light off;
- leaving a room, it switches the room's lights off, unless it forgets (the profile's chance) or someone else
```
with:
```python
  light off;
- a room too cold or too warm for the profile's comfort band, as its thermostat shows it, gets the thermostat
  changed by hand, at the same moments: too cold, a thermostat that is off is switched to heat and its setpoint
  raised to the band's low end; too warm, its setpoint lowered to the band's high end. The rules' room
  temperature does not follow the heating, so once the setpoint is right the resident leaves it;
- leaving a room, it switches the room's lights off, unless it forgets (the profile's chance) or someone else
```

In `vesper/lab/resident.py`, replace:
```python
from vesper.lab.profile import Profile
from vesper.lab.rules import ambient_at, is_locked, is_on
```
with:
```python
from vesper.lab.profile import Profile
from vesper.lab.rules import ambient_at, is_locked, is_on, thermostat
```

In `vesper/lab/resident.py`, replace:
```python
                steps.append(("dark", task.room))
            steps.append(("devices", task))
```
with:
```python
                steps.append(("dark", task.room))
                steps.append(("comfort", task.room))
            steps.append(("devices", task))
```

In `vesper/lab/resident.py`, replace:
```python
            return
        self._dark(task.room)
```
with:
```python
            return
        self._comfort(task.room)
        self._dark(task.room)
```

In `vesper/lab/resident.py`, replace:
```python
            self._dark(step[1])
        elif kind == "lights_out":
```
with:
```python
            self._dark(step[1])
        elif kind == "comfort":
            self._comfort(step[1])
        elif kind == "set":
            self._set(*step[1:])
        elif kind == "lights_out":
```

In `vesper/lab/resident.py`, replace:
```python
            self._steps.appendleft(("use", lights[0], True, "a dark room"))
```
with:
```python
            self._steps.appendleft(("use", lights[0], True, "a dark room"))

    def _comfort(self, room: str) -> None:
        low, high = self.profile.comfort
        for name, d in self._devices_in(room):
            t = thermostat(self.rules.twin(name))
            if t is None or d.mount == "ceiling" or not isinstance(t["temperature"], int | float):
                continue
            steps = []
            if t["temperature"] < low:
                if t["mode"] == "off" and "heat" in t["modes"]:
                    steps.append(("set", name, t["mode_feature"], "heat", "too cold"))
                if t["setpoint"] < low:
                    steps.append(("set", name, t["setpoint_feature"], _within(low, t["range"]), "too cold"))
            elif t["temperature"] > high and t["setpoint"] > high:
                steps.append(("set", name, t["setpoint_feature"], _within(high, t["range"]), "too warm"))
            for s in reversed(steps):
                self._steps.appendleft(s)
            return  # a room's first thermostat is the one a person turns to

    def _set(self, device: str, feature: str, value, reason: str, tries: int = 0, task: str | None = None) -> None:
        if self.rules.twin(device).state.get(feature) == value:
            return
        if not self.rules.within_reach(self.name, device):
            if tries:
                self.session.note({"resident": self.name, "decision": "could not reach it", "device": device})
                return
            self._steps.appendleft(("set", device, feature, value, reason, 1, task))
            self._steps.appendleft(("walk_point", self._near(device), reason, 0, task))
            return
        self.session.adjust(device, feature, value, actor=self.name, why=self._why(reason, task))
```

In `vesper/lab/resident.py`, replace:
```python
        return best[1] if best else self._spot(room)
```
with:
```python
        return best[1] if best else self._spot(room)


def _within(value: float, bounds) -> float:
    """`value` inside a feature's range, if it has one."""
    if not bounds:
        return value
    return min(max(value, bounds[0]), bounds[1])
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `258 passed, 1 skipped` in the repository's working copy (`255 passed, 4 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/profile.py vesper/lab/rules.py vesper/lab/session.py vesper/lab/resident.py tests/lab/homes.py tests/lab/test_profile.py tests/lab/test_comfort.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the comfort band: a resident who finds a room too cold or too warm changes its thermostat by hand

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: A Matter color-temperature twin starts at a temperature a light can show

**Files:**
- Modify: `vesper/twins/twin.py` (`SPEC_START`; `_initial` uses it inside the feature's range)
- Modify: `docs/experiments/results-2026-10-26.md` (the finding's cause and fix)
- Tests: `tests/twins/test_twin.py`

**Interfaces:**
- Consumes: `Twin`, `_initial` (`vesper/twins/twin.py`).
- Produces: `SPEC_START` (feature name -> starting value); a twin's `Color Control.ColorTemperatureMireds`, `CoupleColorTempToLevelMinMireds` and `StartUpColorTemperatureMireds` start at 250.

- [ ] **Step 1: Write the failing test**

In `tests/twins/test_twin.py`, replace:
```python
    assert t.state["battery"] == 0
```
with:
```python
    assert t.state["battery"] == 0


def test_a_matter_color_temperature_light_starts_at_the_spec_default_not_below_its_physical_range():
    """Experiment 6 (2026-10-26): starting at 0 and 1 mireds, two products' twins lay below the node's physical
    minimum (135 mireds) and the node refused them. Matter's ColorTemperatureMireds default is 0x00FA (250)."""
    ct = [{"name": f"Color Control.{a}", "type": "numeric", "access": ["state", "get"], **extra}
          for a, extra in (("ColorTemperatureMireds", {}), ("CoupleColorTempToLevelMinMireds", {"range": [1, 65279]}),
                           ("StartUpColorTemperatureMireds", {"range": [1, 65279]}))]
    t = Twin.from_vds({"id": "matter:1460:aa61", "features": ct})
    assert [t.state[f["name"]] for f in ct] == [250, 250, 250]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/twins/test_twin.py -q 2>&1 | tail -1`

Expected: `1 failed, 9 passed`: `assert [0, 1, 1] == [250, 250, 250]`

- [ ] **Step 3: Implement, and record the finding's cause**

In `vesper/twins/twin.py`, replace:
```python

def _initial(f: dict):
```
with:
```python

# Starting values the type alone gets wrong. Matter's ColorTemperatureMireds defaults to 0x00FA (250 mireds, 4000 K);
# the coupled minimum and the start-up temperature are manufacturer-specific and must lie in the light's physical
# range, which the catalog does not know (the DCL publishes no limits), so they start at the same 250 mireds, inside
# the usual tunable-white range (153 to 370 mireds, 6500 K to 2700 K). The type's minimum, 0 or 1 mired, is outside
# every light's range.
SPEC_START = {"Color Control.ColorTemperatureMireds": 250, "Color Control.CoupleColorTempToLevelMinMireds": 250,
              "Color Control.StartUpColorTemperatureMireds": 250}


def _initial(f: dict):
```

In `vesper/twins/twin.py`, replace:
```python
def _initial(f: dict):
    kind = f["type"]
```
with:
```python
def _initial(f: dict):
    if f["name"] in SPEC_START:
        lo, hi = f.get("range", [None, None])
        if lo is None or lo <= SPEC_START[f["name"]] <= hi:
            return SPEC_START[f["name"]]
    kind = f["type"]
```

In `docs/experiments/results-2026-10-26.md`, replace:
```markdown
- Finding for the catalog: for two color-temperature lights (matter:1460:aa61, matter:1387:70c9), the twin's starting `ColorTemperatureMireds` (0) and `CoupleColorTempToLevelMinMireds` (1) lie below the product's physical minimum (135 mireds). The node refused them and the twins took the node's 250 and 153. Starting values should respect the product's physical limits.
- Occupancy clears after 90 s, an assumed timeout, for matter:1359:06ce.
```
with:
```markdown
- Finding for the catalog: for two color-temperature lights (matter:1460:aa61, matter:1387:70c9), the twin's starting `ColorTemperatureMireds` (0) and `CoupleColorTempToLevelMinMireds` (1) lie below the product's physical minimum (135 mireds). The node refused them and the twins took the node's 250 and 153. Starting values should respect the product's physical limits.
  - Cause: the catalog has no physical limits. In the Matter data model (connectedhomeip ZAP v1.6.1.0), the color-temperature device types require `COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MIN` and `..._MAX`, but the Color Control cluster defines those attributes as `COLOR_CONTROL_COLOR_TEMP_PHYSICAL_MIN_MIREDS` and `..._MAX_MIREDS`. The normalizer matches the required names exactly, so it drops `ColorTempPhysicalMinMireds` and `ColorTempPhysicalMaxMireds` (no record in the catalog has them). The DCL publishes no limits either.
  - Fixed in the twins (Plan W3b): `ColorTemperatureMireds` starts at the spec's default, 0x00FA (250 mireds); the coupled minimum and the start-up temperature start at the same 250, inside the usual tunable-white range (153 to 370 mireds).
  - Not changed: matching the required names leniently would add both attributes to every color-temperature light's record and change the catalog's numbers; that waits for the PI.
- Occupancy clears after 90 s, an assumed timeout, for matter:1359:06ce.
```

- [ ] **Step 4: Run it, then the whole suite**

Run: `.venv/bin/python -m pytest tests/twins -q 2>&1 | tail -1`

Expected: `52 passed`

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `743 passed, 13 skipped` in the repository's working copy (`739 passed, 17 skipped` in an export of `main` without the local catalog)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/twin.py tests/twins/test_twin.py docs/experiments/results-2026-10-26.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "fix(twins): a Matter color-temperature twin starts at the spec's 250 mireds, inside a light's physical range

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```



---

## After this plan

- **The live checks,** Monday after the other experiment ends, with the PI: Lab check 1 hands-on (`docs/experiments/lab-runbook.md`), then a day of the example home with VESPER's engine compared against the same day with Home Assistant in the loop.
- **The PUBG-style page** (spec §2a): WASD and click-to-interact, the minimap, the event feed; no click-to-walk.
- **W2d** (Matter on the hub; the placement editor), **W4** the security lab, **W1** the Habitat world service and the 3D view, **W5** packaging.
- **For the PI:** whether to match the Matter device types' required attribute names leniently, so the catalog gains the physical color-temperature limits (it changes the catalog's numbers).
