"""The map world: avatars that walk a home's map, and the doors that block them (spec §3).

It runs in-process on the session's clock, from the map file alone: the headless world for scripted runs,
the tests and the map-only mode. The Habitat world service (plan W1) offers the same operations and events,
and adds the 3D view.

Events, to every `on_event` handler:
- `{"event": "pose", "actor", "x", "z", "yaw", "room"}` after every step;
- `{"event": "room", "actor", "room", "previous"}` when an actor enters another room;
- `{"event": "door", "id", "open"}` when a door opens or closes;
- `{"event": "arrived", "actor"}` at the end of a walk, `{"event": "blocked", "actor"}` when a walk is cut off.
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from dataclasses import dataclass

from vesper.lab.geometry import Point, distance
from vesper.lab.worldmap import MapError, WorldMap

SPEED_M_S = 1.1  # walking speed (the earlier embodied resident's, e2_embodied.py)
STEP_S = 0.25  # a walking avatar's pose updates this often
MAX_STEP_M = 0.5  # the longest single step a player's input may make


class WorldError(ValueError):
    """An operation the world refuses: an unknown actor or door, a position off the floor."""


@dataclass(frozen=True)
class Pose:
    actor: str
    model: str
    x: float
    z: float
    yaw: float  # degrees; 0 is +x, 90 is +z
    room: str | None  # None in a doorway or outside every room


class MapWorld:
    def __init__(self, world_map: WorldMap, clock):
        self.map, self.clock = world_map, clock
        self.doors: dict[str, bool] = {}  # doorway id -> open
        self.last_move_ok = True
        self._poses: dict[str, Pose] = {}
        self._last_room: dict[str, str | None] = {}
        self._walk: dict[str, int] = {}  # a walk's token; a newer walk or a step cancels the older one
        self._handlers: list[Callable[[dict], None]] = []
        self._blocked: frozenset = frozenset()

    def on_event(self, fn: Callable[[dict], None]) -> None:
        self._handlers.append(fn)

    # -- actors -------------------------------------------------------------------------------
    def spawn(self, actor: str, model: str, at: Point) -> Pose:
        if actor in self._poses:
            raise WorldError(f"actor {actor!r} already exists")
        if not self._free(*at):
            raise WorldError(f"{actor}: {at} is not on the walkable floor")
        room = self.map.room_at(*at)
        self._last_room[actor] = room.id if room else None
        self._poses[actor] = Pose(actor, model, at[0], at[1], 0.0, room.id if room else None)
        self._emit_pose(self._poses[actor])
        return self._poses[actor]

    def pose(self, actor: str) -> Pose:
        if actor not in self._poses:
            raise WorldError(f"unknown actor {actor!r}")
        return self._poses[actor]

    def actors(self) -> list[str]:
        return list(self._poses)

    def move(self, actor: str, dx: float, dz: float) -> Pose:
        """A player's step, cut to MAX_STEP_M; walls and closed doors stop it (`last_move_ok` is False)."""
        p = self.pose(actor)
        self.stop(actor)
        length = math.hypot(dx, dz)
        if length > MAX_STEP_M:
            dx, dz = dx * MAX_STEP_M / length, dz * MAX_STEP_M / length
        target = (p.x + dx, p.z + dz)
        self.last_move_ok = length > 0 and self._segment_free((p.x, p.z), target)
        if self.last_move_ok:
            self._place(actor, target, math.degrees(math.atan2(dz, dx)))
        return self._poses[actor]

    def walk_to(self, actor: str, target: Point) -> list[Point] | None:
        """Walk along the shortest path at SPEED_M_S; None if no path exists (the walk is not started)."""
        p = self.pose(actor)
        self.stop(actor)
        path = self._plan((p.x, p.z), target)
        if path is None:
            return None
        token = self._walk[actor] = self._walk.get(actor, 0) + 1
        self._schedule_walk(actor, token, path, 1)
        return path

    def stop(self, actor: str) -> None:
        self._walk[actor] = self._walk.get(actor, 0) + 1

    # -- doors --------------------------------------------------------------------------------
    def add_door(self, doorway_id: str, open: bool = True) -> None:  # noqa: A002 - "open" is the door's state
        try:
            self.map.doorway(doorway_id)
        except MapError as e:
            raise WorldError(str(e)) from e
        self.doors[doorway_id] = open
        self._recompute_blocked()

    def set_door(self, doorway_id: str, open: bool) -> bool:  # noqa: A002
        if doorway_id not in self.doors:
            raise WorldError(f"no door on doorway {doorway_id!r}; add it to the home's doors first")
        if self.doors[doorway_id] != open:
            self.doors[doorway_id] = open
            self._recompute_blocked()
            self._emit({"event": "door", "id": doorway_id, "open": open})
        return True

    # -- internals ----------------------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        for fn in self._handlers:
            fn(event)

    def _emit_pose(self, p: Pose) -> None:
        self._emit({"event": "pose", "actor": p.actor, "x": p.x, "z": p.z, "yaw": p.yaw, "room": p.room})

    def _place(self, actor: str, at: Point, yaw: float) -> None:
        old = self._poses[actor]
        room = self.map.room_at(*at)
        rid = room.id if room else None
        self._poses[actor] = Pose(actor, old.model, at[0], at[1], yaw, rid)
        self._emit_pose(self._poses[actor])
        if rid is not None and rid != self._last_room.get(actor):
            self._emit({"event": "room", "actor": actor, "room": rid, "previous": self._last_room.get(actor)})
            self._last_room[actor] = rid

    def _recompute_blocked(self) -> None:
        self._blocked = frozenset(c for d, is_open in self.doors.items() if not is_open
                                  for c in self.map.doorway(d).cells)

    def _cell_free(self, i: int, j: int) -> bool:
        return self.map.grid.is_walkable(i, j) and (i, j) not in self._blocked

    def _free(self, x: float, z: float) -> bool:
        c = self.map.grid.cell_of(x, z)
        return c is not None and self._cell_free(*c)

    def _segment_free(self, a: Point, b: Point) -> bool:
        n = max(1, math.ceil(distance(a, b) / (self.map.grid.cell / 2)))
        return all(self._free(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1))

    def _schedule_walk(self, actor: str, token: int, path: list[Point], k: int) -> None:
        def step():
            if self._walk.get(actor) != token:
                return  # replaced or stopped
            p = self._poses[actor]
            budget, here, idx = SPEED_M_S * STEP_S, (p.x, p.z), k
            traversed = [here]  # the step follows the path's corners; a chord across a corner could cut a wall
            while budget > 1e-9 and idx < len(path):
                d = distance(here, path[idx])
                if d <= budget:
                    budget, here, idx = budget - d, path[idx], idx + 1
                else:
                    f = budget / d
                    here, budget = (here[0] + (path[idx][0] - here[0]) * f, here[1] + (path[idx][1] - here[1]) * f), 0
                traversed.append(here)
            if not all(self._segment_free(a, b) for a, b in zip(traversed, traversed[1:])):  # a door closed ahead
                self.stop(actor)
                self._emit({"event": "blocked", "actor": actor})
                return
            yaw = math.degrees(math.atan2(here[1] - p.z, here[0] - p.x)) if here != (p.x, p.z) else p.yaw
            self._place(actor, here, yaw)
            if idx >= len(path):
                self._emit({"event": "arrived", "actor": actor})
            else:
                self._schedule_walk(actor, token, path, idx)
        self.clock.call_at(self.clock.now() + STEP_S, step)

    def _plan(self, start: Point, target: Point) -> list[Point] | None:
        """A* over free cells (8-connected, no corner cutting), then straightened along free lines of sight."""
        g = self.map.grid
        s, goal = g.cell_of(*start), self._nearest_free_cell(target)
        if s is None or goal is None:
            return None
        diag = math.sqrt(2)

        def h(c):
            dx, dz = abs(c[0] - goal[0]), abs(c[1] - goal[1])
            return (dx + dz) + (diag - 2) * min(dx, dz)

        best, came, frontier = {s: 0.0}, {}, [(h(s), 0, s)]
        tie = 0
        while frontier:
            _, _, c = heapq.heappop(frontier)
            if c == goal:
                break
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if (di, dj) == (0, 0):
                        continue
                    n = (c[0] + di, c[1] + dj)
                    if not self._cell_free(*n) or (di and dj and not (self._cell_free(c[0] + di, c[1])
                                                                     and self._cell_free(c[0], c[1] + dj))):
                        continue
                    cost = best[c] + (diag if di and dj else 1.0)
                    if cost < best.get(n, math.inf):
                        best[n], came[n] = cost, c
                        tie += 1
                        heapq.heappush(frontier, (cost + h(n), tie, n))
        if goal not in best:
            return None
        cells = [goal]
        while cells[-1] != s:
            cells.append(came[cells[-1]])
        cells.reverse()
        end = target if self._free(*target) else g.center(*goal)
        points = [start] + [g.center(*c) for c in cells[1:-1]] + [end]
        straight = [points[0]]
        k = 0
        while k < len(points) - 1:  # string-pulling: jump to the farthest point still in a free line of sight
            far = len(points) - 1
            while far > k + 1 and not self._segment_free(points[k], points[far]):
                far -= 1
            straight.append(points[far])
            k = far
        return straight

    def _nearest_free_cell(self, p: Point) -> tuple[int, int] | None:
        g = self.map.grid
        c = g.cell_of(*p)
        if c is not None and self._cell_free(*c):
            return c
        if c is None:
            return None
        for r in range(1, 6):  # within half a metre: a target placed on furniture or a wall
            for i in range(c[0] - r, c[0] + r + 1):
                for j in range(c[1] - r, c[1] + r + 1):
                    if self._cell_free(i, j):
                        return (i, j)
        return None
