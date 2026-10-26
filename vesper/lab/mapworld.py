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
        self.plan_work = 0  # grid cells the last plan expanded: what it cost, independent of the machine
        self._legs: dict = {}  # (room, from doorway, to doorway) -> cells: the walk across a room, reused
        self._graph = None  # per map: each room's doorways, and each doorway's cell

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
        self._schedule_walk(actor, token, path, 1, self.clock.now() + STEP_S)
        return path

    def stop(self, actor: str) -> None:
        self._walk[actor] = self._walk.get(actor, 0) + 1

    # -- doors --------------------------------------------------------------------------------
    def add_door(self, doorway_id: str, open: bool = True) -> None:  # noqa: A002 - "open" is the door's state
        try:
            self.map.doorway(doorway_id)
        except MapError as e:
            raise WorldError(str(e)) from e
        if not open:
            self._refuse_if_occupied(doorway_id)
        self.doors[doorway_id] = open
        self._recompute_blocked()

    def set_door(self, doorway_id: str, open: bool) -> bool:  # noqa: A002
        if doorway_id not in self.doors:
            raise WorldError(f"no door on doorway {doorway_id!r}; add it to the home's doors first")
        if self.doors[doorway_id] != open:
            if not open:
                self._refuse_if_occupied(doorway_id)
            self.doors[doorway_id] = open
            self._recompute_blocked()
            self._emit({"event": "door", "id": doorway_id, "open": open})
        return True

    def occupants(self, doorway_id: str) -> list[str]:
        """The actors standing in a doorway: a door cannot close on them."""
        cells = set(self.map.doorway(doorway_id).cells)
        return [a for a, p in self._poses.items() if self.map.grid.cell_of(p.x, p.z) in cells]

    def _refuse_if_occupied(self, doorway_id: str) -> None:
        if who := self.occupants(doorway_id):
            raise WorldError(f"{', '.join(who)} stands in the {doorway_id} doorway; the door cannot close on them")

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
        """Every cell the segment touches is free, and it never squeezes between two blocked cells that meet at
        a corner (A*'s rule). Exact, so the walker accepts every segment the planner straightened."""
        g = self.map.grid
        if not (self._free(*a) and self._free(*b)):
            return False
        x0, z0 = (a[0] - g.origin[0]) / g.cell, (a[1] - g.origin[1]) / g.cell
        dx, dz = (b[0] - a[0]) / g.cell, (b[1] - a[1]) / g.cell
        i, j = math.floor(x0), math.floor(z0)
        si, sj = (1 if dx > 0 else -1), (1 if dz > 0 else -1)
        step_x, step_z = (abs(1 / dx) if dx else math.inf), (abs(1 / dz) if dz else math.inf)
        tx = ((i + 1 - x0) if dx > 0 else (x0 - i)) * step_x if dx else math.inf  # when the line crosses into
        tz = ((j + 1 - z0) if dz > 0 else (z0 - j)) * step_z if dz else math.inf  # the next column / row
        while min(tx, tz) <= 1.0:
            if abs(tx - tz) < 1e-12:  # exactly through a corner
                if not (self._cell_free(i + si, j) and self._cell_free(i, j + sj)):
                    return False
                i, j, tx, tz = i + si, j + sj, tx + step_x, tz + step_z
            elif tx < tz:
                i, tx = i + si, tx + step_x
            else:
                j, tz = j + sj, tz + step_z
            if not self._cell_free(i, j):
                return False
        return True

    def _schedule_walk(self, actor: str, token: int, path: list[Point], k: int, due: float) -> None:
        """The next step at `due`, on the walk's planned timeline: a step that fires late on a busy wall clock
        does not delay the ones after it, so a walk takes as long as on the logical clock."""
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
                self._schedule_walk(actor, token, path, idx, due + STEP_S)
        self.clock.call_at(due, step)

    def doors_on_route(self, start: Point, target: Point) -> list[tuple[str, str | None]] | None:
        """The closed doors on the shortest route as if every door were open, in the order the route crosses
        them, each with the room the route comes from; None if there is no route even then."""
        g = self.map.grid
        s, goal = g.cell_of(*start), self._nearest_free_cell(target, g.is_walkable)
        cells = self._cells(s, goal, g.is_walkable) if s is not None and goal is not None else None
        if cells is None:
            return None
        closed = {c: d for d, is_open in self.doors.items() if not is_open for c in self.map.doorway(d).cells}
        out: list[tuple[str, str | None]] = []
        for before, c in zip(cells, cells[1:]):
            door = closed.get(c)
            if door is not None and closed.get(before) != door and (not out or out[-1][0] != door):
                room = self.map.room_of_cell(*before)
                out.append((door, room.id if room else None))
        return out

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
        s, goal = g.cell_of(*start), self._nearest_free_cell(target)
        if s is None or goal is None:
            return None
        cells = self._cells(s, goal, self._cell_free)
        if cells is None:
            return None
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
        k = 0
        while k < len(points) - 1:  # string-pulling: jump to the farthest point still in a free line of sight
            far = len(points) - 1
            while far > k + 1 and not self._segment_free(points[k], points[far]):
                far -= 1
            straight.append(points[far])
            k = far
        return straight

    def _cells(self, s, goal, free) -> list[tuple[int, int]] | None:
        """A*'s cells from `s` to `goal` over cells where `free(i, j)` holds."""
        diag = math.sqrt(2)

        def h(c):
            dx, dz = abs(c[0] - goal[0]), abs(c[1] - goal[1])
            return (dx + dz) + (diag - 2) * min(dx, dz)

        best, came, frontier = {s: 0.0}, {}, [(h(s), 0, s)]
        tie = 0
        while frontier:
            _, _, c = heapq.heappop(frontier)
            self.plan_work += 1
            if c == goal:
                break
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if (di, dj) == (0, 0):
                        continue
                    n = (c[0] + di, c[1] + dj)
                    if not free(*n) or (di and dj and not (free(c[0] + di, c[1]) and free(c[0], c[1] + dj))):
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
        return cells

    def _nearest_free_cell(self, p: Point, free=None) -> tuple[int, int] | None:
        free = free or self._cell_free
        g = self.map.grid
        c = g.cell_of(*p)
        if c is not None and free(*c):
            return c
        if c is None:
            return None
        for r in range(1, 6):  # within half a metre: a target placed on furniture or a wall
            for i in range(c[0] - r, c[0] + r + 1):
                for j in range(c[1] - r, c[1] + r + 1):
                    if free(i, j):
                        return (i, j)
        return None
