"""Scripted residents (spec §5a): people who live a routine in the home and react to it.

A resident acts only as the player does: it walks, and it uses doors and devices within reach, on the same
side of the wall. So the sensors and the rules cannot tell it from the player. Each of its actions is traced
with who did it and why (`why`: resident, task, reason), the ground truth an attack's success and a defense's
alarm are judged against (spec §6, D2). Decisions not to act ("forgot the light") are traced too.

What it does, from its routine and its profile:
- every day, at each task's time, it goes to the task's room; there it switches on what the task uses (a plug
  while cooking) and switches it off when the task is over, unless the next task uses it too; a switch-off
  names the task that used the device;
- a room too dark for the task (the rules' daylight model, no light on) gets its light, when the task begins
  and whenever the resident looks around (every LOOK_S): the room has grown dark, or someone switched the
  light off;
- a room too cold or too warm for the profile's comfort band, as its thermostat shows it, gets the thermostat
  changed by hand, at the same moments: too cold, a thermostat that is off is switched to heat and its setpoint
  raised to the band's low end; too warm, its setpoint lowered to the band's high end. The rules' room
  temperature does not follow the heating, so once the setpoint is right the resident leaves it;
- leaving a room, it switches the room's lights off, unless it forgets (the profile's chance) or someone else
  stays there (a resident whose task is in that room, or anyone else standing in it);
- going to bed (a SLEEP task that starts within NIGHT_S after the profile's bedtime) it switches off the lights
  of the room it leaves and of the bedroom, and locks the front door unless it forgets; sleep never switches a
  light on;
- leaving home (an AWAY task) it goes out through the front door, closes it and locks it behind, unless it
  forgets; coming home it unlocks, opens and closes it again;
- a closed door on its way it opens, and closes behind it; a walk it cannot make is traced;
- an alarm (a smoke or gas sensor reporting) sends it out of the home, and keeps it out until every alarm has
  cleared; then it goes back to what its routine has it do.
A session that starts in the middle of a task finds the resident at it. Reactions read what a person in the
room would see (a light on or off), never Home Assistant's state.
"""
from __future__ import annotations

import random
from collections import deque

from vesper.lab.geometry import distance
from vesper.lab.home import spawn_point
from vesper.lab.profile import Profile
from vesper.lab.rules import ambient_at, is_locked, is_on, thermostat

ALARMS = {("smoke_sensor", "smoke"), ("gas_sensor", "gas")}
RETRY_S = 2.0  # waiting for a doorway someone stands in
NEAR_M = 0.4  # how far from a device a resident stands to use it
LOOK_S = 60.0  # how often a resident at its task looks around
NIGHT_S = 8 * 3600  # going to bed: a SLEEP task that starts within this long after the profile's bedtime
DAY_S = 24 * 3600
WALK_TRIES = 3  # a walk planned again this often (doors shut on the way) is given up, and the trace says so


class Resident:
    def __init__(self, session, name: str, model: str, routine, profile: Profile | None = None):
        self.session, self.name, self.model, self.routine = session, name, model, routine
        self.profile = profile or Profile()
        self.home, self.world, self.rules = session.home, session.world, session.rules
        self.rng = random.Random(f"{self.home.seed}:{name}")
        self._steps: deque = deque()
        self._epoch = 0  # a new task or an alarm replaces what was under way
        self._waiting = False
        self._walking: tuple | None = None  # (point, reason, tries, task): the walk under way
        self._task: int | None = None
        self._task_devices: dict[str, str] = {}  # device -> the task that switched it on
        self._between = False  # a task has ended and the next has not begun
        self._alarms: set[str] = set()  # the alarm sensors reporting now
        session.residents[name] = self
        self.world.on_event(self._on_world)
        session.runtime.subscribe(self._on_report)

    @property
    def _alarm(self) -> bool:
        return bool(self._alarms)

    @property
    def task(self):
        """The routine's task under way, or None between tasks."""
        return self.routine.tasks[self._task] if self._task is not None and not self._between else None

    # -- the day ------------------------------------------------------------------------------
    def start(self) -> None:
        start, clock = self.home.start_time, self.session.clock
        self._start = clock.now()
        self._midnight = self._start - start  # the session's time at the midnight that begins its first day
        self.session.spawn(self.name, self.model, self._spot(self._room_at(start)))
        under_way = self._under_way(start)
        if under_way is not None:  # the resident is at it already, and goes on with it
            k, end = under_way
            self._task = k
            clock.call_at(self._start, self._settle)
            clock.call_at(self._midnight + end, lambda k=k: self._end(k))
        self._schedule_day(0)
        self._schedule_day(1)
        clock.call_at(self._midnight + DAY_S, lambda: self._next_day(2))
        clock.call_at(self._start + LOOK_S, self._look)

    def _schedule_day(self, day: int) -> None:
        """Every task begins and ends at its times on every day; day 0 is the day the session starts on."""
        for k, t in enumerate(self.routine.tasks):
            at = self._midnight + day * DAY_S + t.start
            if at >= self._start:
                self.session.clock.call_at(at, lambda k=k: self._begin(k))
                self.session.clock.call_at(at + t.duration, lambda k=k: self._end(k))

    def _next_day(self, day: int) -> None:
        """At the midnight that begins day `day - 1`: day `day` is scheduled, a day before its tasks are due."""
        self._schedule_day(day)
        self.session.clock.call_at(self._midnight + day * DAY_S, lambda: self._next_day(day + 1))

    def _under_way(self, when: float) -> tuple[int, float] | None:
        """(task, end) of the task that began before `when` and has not ended (yesterday's may run past
        midnight), in seconds after the first day's midnight."""
        for day in (0, -1):
            for k, t in enumerate(self.routine.tasks):
                begin = day * DAY_S + t.start
                if begin < when < begin + t.duration:
                    return k, begin + t.duration
        return None

    def _room_at(self, when: float) -> str:
        tasks = self.routine.tasks
        now = [t for t in tasks if t.start <= when < t.end] or [t for t in tasks if t.end - DAY_S > when]
        before = [t for t in tasks if t.start <= when]
        chosen = now[0] if now else before[-1] if before else tasks[-1] if tasks else None  # else yesterday's last
        if chosen is not None and chosen.room is None:
            return self._outdoors() or self._indoor_rooms()[0]
        return chosen.room if chosen is not None else self._indoor_rooms()[0]

    def _settle(self) -> None:
        """The task under way when the session starts: in its room already, the resident does what it does."""
        task = self.routine.tasks[self._task]
        self.session.task_record(self.name, task)
        if task.room is not None and not self._alarm:
            if task.category != "SLEEP":
                self._steps.append(("dark", task.room))
            self._steps.append(("devices", task))
            self._next()

    def _begin(self, k: int) -> None:
        task = self.routine.tasks[k]
        self._task, self._between = k, False
        self.session.task_record(self.name, task)
        self._epoch += 1
        self._steps.clear()
        self._waiting, self._walking = False, None
        if self._alarm:  # kept out of the home
            if task.room is not None:
                self.session.note({"resident": self.name, "decision": "stayed out: an alarm", "task": task.name})
            return
        outside = self._outside()
        if task.room is None:
            steps = [] if outside else self._switch_off(keep=()) + self._leave_home("leaving home")
        else:
            steps = (self._come_home() if outside else []) + self._switch_off(keep=self._uses(task))
            bed = self._going_to_bed(task)
            steps.append(("leave_room", task.room))
            if bed:
                steps.append(("bedtime_lock",))
            steps.append(("walk_room", task.room, task.name))
            if bed:
                steps.append(("lights_out", task.room))  # going to bed: the lights go off
            elif task.category != "SLEEP":
                steps.append(("dark", task.room))
                steps.append(("comfort", task.room))
            steps.append(("devices", task))
        self._steps.extend(steps)
        self._next()

    def _end(self, k: int) -> None:
        if self._task != k or self._alarm:
            return  # out of the home for an alarm: what was left on stays on until the resident is back
        tasks = self.routine.tasks
        gap = (tasks[(k + 1) % len(tasks)].start - tasks[k].end) % DAY_S
        if gap < 1e-6 or DAY_S - gap < 1e-6:
            return  # the next task begins now, and switches off what it does not use
        self._between = True
        self.session.task_record(self.name, None)
        self._steps.extend(self._switch_off(keep=()))
        self._next()

    def _switch_off(self, keep) -> list[tuple]:
        """Steps that switch off the devices earlier tasks switched on, except those in `keep` (the next task
        uses them: they stay on, and are now that task's)."""
        steps = [("use", d, False, f"{task} is over", 0, task) for d, task in self._task_devices.items()
                 if d not in keep]
        self._task_devices = {d: task for d, task in self._task_devices.items() if d in keep}
        return steps

    def _going_to_bed(self, task) -> bool:
        return task.category == "SLEEP" and (task.start - self.profile.bedtime) % DAY_S < NIGHT_S

    def _look(self) -> None:
        """Every LOOK_S: at its task and in its room, a resident sees a room grown dark, or its light switched
        off by someone else."""
        clock = self.session.clock
        clock.call_at(clock.now() + LOOK_S, self._look)
        if self._alarm or self._steps or self._waiting or self._task is None:
            return
        task = self.routine.tasks[self._task]
        if task.room is None or task.category == "SLEEP" or self._here() != task.room:
            return
        self._comfort(task.room)
        self._dark(task.room)
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
            self._walk(*step[1:])
        elif kind == "use":
            self._use(*step[1:])
        elif kind == "door":
            self._door(*step[1:])
        elif kind == "leave_room":
            self._lights_off_here(step[1])
        elif kind == "dark":
            self._dark(step[1])
        elif kind == "comfort":
            self._comfort(step[1])
        elif kind == "set":
            self._set(*step[1:])
        elif kind == "lights_out":
            for light in reversed(self._lights(step[1])):
                self._steps.appendleft(("use", light, False, "bedtime"))
        elif kind == "devices":
            self._devices(step[1])
        elif kind == "bedtime_lock":
            self._bedtime_lock()
        elif kind == "lock_behind":
            self._lock_behind(step[1], step[2])

    def _walk(self, point, reason: str, tries: int = 0, task: str | None = None) -> None:
        if self.session.walk_to(point, actor=self.name, why=self._why(reason, task)) is not None:
            self._waiting, self._walking = True, (point, reason, tries, task)
            return
        route = self.world.doors_on_route(self._xy(), point) if tries < WALK_TRIES else None
        if not route:  # no way even through the doors, or no closed door in the way: the trace says so
            self.session.note({"resident": self.name, "decision": "could not get there", "reason": reason,
                               "point": list(point)})
            return
        self._steps.appendleft(("walk_point", point, reason, tries + 1, task))
        for door, room in reversed(route):  # through each closed door on the way, in order, closing it behind
            for s in reversed(self._through(door, room or self._here(), reason)):
                self._steps.appendleft(s)

    def _on_world(self, event: dict) -> None:
        if event.get("actor") != self.name or event["event"] not in ("arrived", "blocked") or not self._waiting:
            return
        if event["event"] == "blocked" and self._walking is not None:  # a door shut on the way: go again
            point, reason, tries, task = self._walking
            self._steps.appendleft(("walk_point", point, reason, tries + 1, task))
        self._walking = None
        epoch, clock = self._epoch, self.session.clock

        def resume():
            if epoch == self._epoch:
                self._waiting = False
                self._next()
        clock.call_at(clock.now(), resume)

    def _use(self, device: str, want: bool, reason: str, tries: int = 0, task: str | None = None) -> None:
        if self._state(device) == want:
            return
        target = self._operator(device)
        if target is None:
            self.session.note({"resident": self.name, "decision": "cannot switch it by hand", "device": device})
            return
        if not self._in_reach(target):
            if tries:
                self.session.note({"resident": self.name, "decision": "could not reach it", "device": target})
                return
            self._steps.appendleft(("use", device, want, reason, 1, task))
            self._steps.appendleft(("walk_point", self._near(target), reason, 0, task))
            return
        self.session.interact(target, actor=self.name, why=self._why(reason, task))

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

    def _devices(self, task) -> None:
        for name in self._uses(task):
            self._task_devices[name] = task.name
            self._steps.append(("use", name, True, task.name))

    def _uses(self, task) -> list[str]:
        """The devices `task` switches on: for each device category its activity uses, the first such device in
        its room that a hand can work."""
        out = []
        for category in self.profile.uses.get(task.category, ()):
            for name, d in self._devices_in(task.room):
                if d.record["identity"]["category"] == category and self._operator(name) is not None:
                    out.append(name)
                    break
        return out

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

    def _leave_home(self, reason: str, alarm: bool = False) -> list[tuple]:
        """Out through the front door, closing it behind; leaving for the day, also the lights and the lock (an
        alarm leaves both)."""
        door, out = self._front_door(), self._outdoors()
        if out is None:
            self.session.note({"resident": self.name, "decision": "could not leave: the map has no outdoor room",
                               "reason": reason})
            return []
        if door is None:
            return [("walk_room", out, reason)]
        steps = ([] if alarm else [("leave_room", out)]) + self._through(door, self._indoor_side(door), reason)
        if not alarm:
            steps.append(("lock_behind", door, reason))
        return steps + [("walk_room", out, reason)]

    def _come_home(self) -> list[tuple]:
        door, out = self._front_door(), self._outdoors()
        return self._through(door, out, "coming home") if door and out else []

    def _through(self, door: str, from_room: str, reason: str) -> list[tuple]:
        """Walk to this side of the door, unlock and open it, go through, and close it behind."""
        dw = self.home.map.doorway(door)
        to_room = next((r for r in dw.rooms if r != from_room), from_room)
        steps = [("walk_point", self._side(door, from_room), reason)]
        lock = self._lock_of(door)
        if lock is not None:
            steps.append(("use", lock, False, reason))
        return steps + [("door", door, True, reason), ("walk_point", self._side(door, to_room), reason),
                        ("door", door, False, reason)]

    def _on_report(self, event) -> None:
        name = self.session.device_name(event.twin_id)
        if name is None:
            return
        category = self.home.device(name).record["identity"]["category"]
        if (category, event.feature) not in ALARMS:
            return
        was = self._alarm
        if event.value is True:
            self._alarms.add(name)
        elif event.value is False:
            self._alarms.discard(name)
        clock = self.session.clock
        if self._alarm and not was and not self._outside():
            self._epoch += 1
            self._steps.clear()
            self._waiting, self._walking = False, None
            self._steps.extend(self._leave_home("an alarm", alarm=True))
            clock.call_at(clock.now(), self._next)
        elif was and not self._alarm:
            clock.call_at(clock.now(), self._back_to_routine)

    def _back_to_routine(self) -> None:
        """Every alarm has cleared: the resident goes back to the task its routine has it do now."""
        if not self._alarm and self._task is not None and self.routine.tasks[self._task].room is not None:
            self._begin(self._task)

    # -- what the resident perceives ------------------------------------------------------------
    def _why(self, reason: str, task: str | None = None) -> dict:
        if task is None and self._task is not None:
            task = self.routine.tasks[self._task].name
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
        """Someone else stays in `room`: a resident whose task keeps it there (judged by the task, so two
        residents leaving together do not each wait for the other), or anyone else standing in it."""
        for actor in self.world.actors():
            other = self.session.residents.get(actor)
            if actor == self.name:
                continue
            if other is not None and other._task is not None:
                if other._staying_in() == room:
                    return True
            elif self.world.pose(actor).room == room:
                return True
        return False

    def _staying_in(self) -> str | None:
        return None if self._alarm or self._task is None else self.routine.tasks[self._task].room

    def _xy(self):
        p = self.world.pose(self.name)
        return (p.x, p.z)

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


def _within(value: float, bounds) -> float:
    """`value` inside a feature's range, if it has one."""
    if not bounds:
        return value
    return min(max(value, bounds[0]), bounds[1])
