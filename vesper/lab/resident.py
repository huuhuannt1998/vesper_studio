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
