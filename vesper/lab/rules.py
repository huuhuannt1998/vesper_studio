"""The game rules (spec §5): how people in the world use the devices, and how the devices change the world.

World → devices:
- presence: a ceiling motion sensor covers its room; a wall-mounted one a 7 m, 110° cone in its room. A person
  entering coverage, or moving in it at least every RETRIGGER_S, applies the `presence` stimulus. A person
  keeping still lets a motion (PIR, `occupancy`) sensor clear by the twin's own timeout, as a real PIR does; a
  presence (mmWave radar, `presence`) sensor keeps seeing them, re-stimulated every RETRIGGER_S while they stay
  covered. Line of sight is taken to be the sensor's own room;
- doors: a person within reach opens or closes a door, unless a lock on it is locked or someone stands in the
  doorway; a contact sensor on the door reports the change, and reads the door's state from the start;
- reach: within INTERACT_M, and on the same side of the wall: in the device's room (or in a doorway beside it);
  for a door, or a device on a door, in one of the door's rooms or in its doorway;
- devices: a person within reach operates a lamp, plug, switch or lock by hand, or presses a button; a device
  with `controls` switches those devices too, as a Zigbee binding does;
- ambient: every room's temperature, humidity and illuminance follow the time of day and the lamps that are on
  (an assumed model: no windows, no weather), applied as `ambient` stimuli every AMBIENT_EVERY_S.

Devices → world: `effects` holds what the views show, a light's or plug's on/off, a lock's state, a door's
state; `on_effect` handlers hear every change.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from vesper.lab.geometry import distance, in_cone
from vesper.twins.runtime import MATTER_AMBIENT, MATTER_CONTACT, MATTER_PRESENCE

INTERACT_M = 1.0  # how far a person reaches
WALL_RANGE_M, WALL_FOV_DEG = 7.0, 110.0  # a wall-mounted motion sensor's coverage (assumed category default)
RETRIGGER_S = 10.0  # a person moving inside coverage re-triggers a motion sensor this often
AMBIENT_EVERY_S = 60.0
LAMP_LUX = 200  # what one lamp adds to its room's illuminance (assumed)
ONOFF, LOCK_STATE = "On/Off.OnOff", "Door Lock.LockState"
_AMBIENT = {"temperature", "local_temperature", "humidity", "illuminance", "illuminance_lux"} | {
    n for names in MATTER_AMBIENT.values() for n in names}


def ambient_at(seconds_of_day: float, lamps_on: int) -> dict:
    """The assumed indoor climate at a time of day: 21 °C ± 1.5 (warmest at 15:00), 45 % humidity, daylight
    rising from 06:00 to a 300 lx noon and gone at 20:00, plus LAMP_LUX per lamp on."""
    h = (seconds_of_day / 3600) % 24
    daylight = 300 * math.sin(math.pi * (h - 6) / 14) if 6 < h < 20 else 0.0
    return {"temperature": round(21 + 1.5 * math.sin(2 * math.pi * (h - 9) / 24), 1), "humidity": 45.0,
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


def toggle_command(twin) -> tuple[str, str | None, object] | None:
    """What a person's hand does to this device: ("operate", feature, value), ("press", None, None), or None for
    a device nobody operates by hand (a sensor)."""
    feats, state = twin.features, twin.state
    f = feats.get("state")
    if f and f["type"] == "binary":
        if "value_toggle" in f:
            return ("operate", "state", f["value_toggle"])
        return ("operate", "state", f["value_off"] if state["state"] == f["value_on"] else f["value_on"])
    if ONOFF in feats:
        return ("operate", ONOFF, not state[ONOFF])
    if LOCK_STATE in feats:
        return ("operate", LOCK_STATE, "Unlocked" if state[LOCK_STATE] == "Locked" else "Locked")
    if feats.get("action", {}).get("type") == "enum":
        return ("press", None, None)
    return None


def is_on(twin) -> bool | None:
    f = twin.features.get("state")
    if f and f["type"] == "binary" and twin.vds["identity"]["category"] != "lock":
        return twin.state["state"] == f["value_on"]
    if ONOFF in twin.features:
        return bool(twin.state[ONOFF])
    return None


def is_locked(twin) -> bool | None:
    if LOCK_STATE in twin.features:
        return twin.state[LOCK_STATE] == "Locked"
    f = twin.features.get("state")
    if twin.vds["identity"]["category"] == "lock" and f and f["type"] == "binary":
        return twin.state["state"] == f["value_on"]
    return None


def _is_presence(twin) -> bool:
    return any(twin.features.get(n, {}).get("type") == "binary" for n in ("occupancy", "presence")) or any(
        n in twin.features for n in MATTER_PRESENCE)


def _senses_stillness(twin) -> bool:
    """An mmWave presence sensor, which sees a person who keeps still (a PIR's `occupancy` does not)."""
    return twin.features.get("presence", {}).get("type") == "binary"


def _is_contact(twin) -> bool:
    return twin.features.get("contact", {}).get("type") == "binary" or MATTER_CONTACT in twin.features


class Rules:
    def __init__(self, home, runtime, world, twin_ids: dict[str, str], record: Callable[[dict], None] | None = None):
        self.home, self.runtime, self.world, self.twin_ids = home, runtime, world, twin_ids
        self.record = record or (lambda r: None)
        self.devices = {d.name: d for d in home.devices}
        self._by_twin = {tid: name for name, tid in twin_ids.items()}
        self.effects: dict[str, dict] = {}
        self._effect_handlers: list[Callable[[str, dict], None]] = []
        self._covered: dict[tuple[str, str], bool] = {}
        self._last_trigger: dict[tuple[str, str], float] = {}
        self._doorway_at = {c: d.id for d in home.map.doorways for c in d.cells}
        self._still = {name for name in self.devices if _senses_stillness(self.twin(name))}

    # -- set-up -------------------------------------------------------------------------------
    def start(self) -> None:
        self.world.on_event(self._on_world)
        self.runtime.subscribe(self._on_report)
        for door, is_open in self.world.doors.items():  # closed ones too: a Matter sensor starts out "open"
            self._contact(door, is_open)
        for name in self.devices:
            self._update_effect(name)
        for door in self.world.doors:
            self._update_effect(door)
        self._ambient_tick()
        if self._still:
            self.runtime.clock.call_at(self.now() + RETRIGGER_S, self._presence_tick)

    def on_effect(self, fn: Callable[[str, dict], None]) -> None:
        self._effect_handlers.append(fn)

    def twin(self, name: str):
        return self.runtime.twins[self.twin_ids[name]]

    def now(self) -> float:
        return self.runtime.clock.now()

    # -- people using things ------------------------------------------------------------------
    def reachable(self, actor: str) -> list[tuple[float, str, str]]:
        """(distance, kind, target) within reach, nearest first; doors before devices at the same distance."""
        p = self.world.pose(actor)
        here = (p.x, p.z)
        out = [(distance(here, self.home.map.doorway(d).center), "door", d) for d in self.world.doors]
        out += [(distance(here, dev.position), "device", name) for name, dev in self.devices.items()
                if self._operable(name)]
        return sorted((t for t in out if t[0] <= INTERACT_M and self._can_touch(actor, t[1], t[2])),
                      key=lambda t: (round(t[0], 6), t[1] != "door", t[2]))

    def interact(self, actor: str, target: str | None = None) -> dict:
        if target is None:
            reach = self.reachable(actor)
            if not reach:
                return self._done(actor, {"ok": False, "kind": None, "target": None, "result": "nothing in reach"})
            _, kind, target = reach[0]
        elif target in self.world.doors:
            kind = "door"
        elif target in self.devices:
            if not self._operable(target):
                raise ValueError(f"{target} is not something a person operates")
            kind = "device"
        else:
            raise ValueError(f"unknown door or device {target!r}")
        why_not = self._out_of_reach(actor, kind, target)
        if why_not:
            return self._done(actor, {"ok": False, "kind": kind, "target": target, "result": why_not})
        if kind == "door":
            if not self.world.doors[target] and self._door_locked(target):
                return self._done(actor, {"ok": False, "kind": "door", "target": target, "result": "locked"})
            if self.world.doors[target] and self.world.occupants(target):
                return self._done(actor, {"ok": False, "kind": "door", "target": target, "result": "obstructed"})
            self.world.set_door(target, not self.world.doors[target])
            result = "opened" if self.world.doors[target] else "closed"
            return self._done(actor, {"ok": True, "kind": "door", "target": target, "result": result})
        result = self._use(target, actor)
        device = self.devices[target]
        for other in device.controls:
            self._bound(device.name, other, actor)
        return self._done(actor, {"ok": True, "kind": "device", "target": target, "result": result})

    def adjust(self, actor: str, target: str, feature: str, value) -> dict:
        """A person sets a device's value by hand (a thermostat's setpoint or mode), within reach."""
        if target not in self.devices:
            raise ValueError(f"unknown device {target!r}")
        if feature not in hand_settings(self.twin(target)) or self.devices[target].mount == "ceiling":
            raise ValueError(f"{target}: {feature} is not something a person sets by hand")
        why_not = self._out_of_reach(actor, "device", target)
        if why_not:
            return self._done(actor, {"ok": False, "kind": "device", "target": target, "result": why_not})
        if self.runtime.operate(self.twin_ids[target], feature, value, actor=actor):
            return self._done(actor, {"ok": True, "kind": "device", "target": target,
                                      "result": f"set {feature} to {value}"})
        reason = self.runtime.actions[-1].reason if self.runtime.actions else "refused"
        return self._done(actor, {"ok": False, "kind": "device", "target": target, "result": f"refused: {reason}"})

    def within_reach(self, actor: str, target: str) -> bool:
        """A device a person standing where `actor` is could touch: near enough, on the same side of the wall."""
        return self._out_of_reach(actor, "device", target) is None

    def _out_of_reach(self, actor: str, kind: str, target: str) -> str | None:
        """Why `actor` cannot touch the door or device (the page's prompt says it), or None if it can."""
        p = self.world.pose(actor)
        where = self.home.map.doorway(target).center if kind == "door" else self.devices[target].position
        if distance((p.x, p.z), where) > INTERACT_M:
            return "too far"
        return None if self._can_touch(actor, kind, target) else "other side of the wall"

    def _use(self, name: str, actor: str) -> str:
        kind, feature, value = toggle_command(self.twin(name))
        if kind == "press":
            self.runtime.stimulate(self.twin_ids[name], {"type": "button_press"})
            return "pressed"
        self.runtime.operate(self.twin_ids[name], feature, value, actor=actor)
        return "toggled"

    def _bound(self, controller: str, target: str, actor: str) -> None:
        """A Zigbee binding: a switch sets its target to its own on/off; a button toggles its target."""
        twin = self.twin(target)
        command = toggle_command(twin)
        if command is None or command[0] != "operate":
            return
        _, feature, value = command
        want = is_on(self.twin(controller))
        if want is not None and is_on(twin) == want:
            return
        self.runtime.operate(self.twin_ids[target], feature, value, actor=f"{actor} via {controller}")
        self.record({"t": self.now(), "rule": "binding", "device": target, "via": controller})

    def _done(self, actor: str, outcome: dict) -> dict:
        self.record({"t": self.now(), "rule": "interact", "actor": actor, "target": outcome["target"],
                     "target_kind": outcome["kind"], "ok": outcome["ok"], "result": outcome["result"]})
        return outcome

    def _can_touch(self, actor: str, kind: str, target: str) -> bool:
        """On the same side of the wall as the target: no reaching through a wall."""
        p = self.world.pose(actor)
        doorway = self._doorway_at.get(self.home.map.grid.cell_of(p.x, p.z))
        door = target if kind == "door" else self.devices[target].door
        if door is not None:
            return doorway == door or p.room in self.home.map.doorway(door).rooms
        room = self.devices[target].room
        return p.room == room or (doorway is not None and room in self.home.map.doorway(doorway).rooms)

    def operable(self, name: str) -> bool:
        """Whether a person's hand works this device (a ceiling device is worked by a switch bound to it)."""
        return self._operable(name)

    def _operable(self, name: str) -> bool:
        return self.devices[name].mount != "ceiling" and toggle_command(self.twin(name)) is not None

    def _door_locked(self, door: str) -> bool:
        return any(d.door == door and is_locked(self.twin(n)) for n, d in self.devices.items())

    # -- the world, heard -----------------------------------------------------------------------
    def _on_world(self, event: dict) -> None:
        if event["event"] == "pose":
            self._presence(event)
        elif event["event"] == "door":
            self._contact(event["id"], event["open"])
            self._update_effect(event["id"])

    def _presence(self, ev: dict) -> None:
        actor, here, now = ev["actor"], (ev["x"], ev["z"]), self.now()
        for name, dev in self.devices.items():
            twin = self.twin(name)
            if not _is_presence(twin):
                continue
            covered = ev["room"] == dev.room and (dev.mount != "wall" or in_cone(dev.position, dev.facing, here,
                                                                                 WALL_RANGE_M, WALL_FOV_DEG))
            key = (name, actor)
            was, self._covered[key] = self._covered.get(key, False), covered
            if covered and (not was or now - self._last_trigger.get(key, -math.inf) >= RETRIGGER_S):
                self._trigger(name, actor, now, entered=not was)

    def _presence_tick(self) -> None:
        """A presence (mmWave) sensor keeps seeing a covered person who keeps still."""
        now = self.now()
        for (name, actor), covered in self._covered.items():
            last = self._last_trigger.get((name, actor), -math.inf)
            if covered and name in self._still and now - last >= RETRIGGER_S:
                self._trigger(name, actor, now)
        self.runtime.clock.call_at(now + RETRIGGER_S, self._presence_tick)

    def _trigger(self, name: str, actor: str, now: float, entered: bool = False) -> None:
        """`entered`: the person has just come into the sensor's view (not a retrigger of one it keeps seeing)."""
        self._last_trigger[(name, actor)] = now
        self.runtime.stimulate(self.twin_ids[name], {"type": "presence"})
        self.record({"t": now, "rule": "presence", "device": name, "actor": actor, "entered": entered})

    def _contact(self, door: str, is_open: bool) -> None:
        for name, dev in self.devices.items():
            if dev.door == door and _is_contact(self.twin(name)):
                self.runtime.stimulate(self.twin_ids[name], {"type": "contact",
                                                             "state": "opened" if is_open else "closed"})
                self.record({"t": self.now(), "rule": "contact", "device": name, "open": is_open})

    def _ambient_tick(self) -> None:
        lamps: dict[str, int] = {}
        for name, dev in self.devices.items():
            if dev.room and dev.record["identity"]["category"] == "light" and is_on(self.twin(name)):
                lamps[dev.room] = lamps.get(dev.room, 0) + 1
        for name, dev in self.devices.items():
            if dev.room and _AMBIENT & set(self.twin(name).features):
                self.runtime.stimulate(self.twin_ids[name], {"type": "ambient", **ambient_at(
                    self.home.start_time + self.now(), lamps.get(dev.room, 0))})
        self.runtime.clock.call_at(self.now() + AMBIENT_EVERY_S, self._ambient_tick)

    # -- devices, seen ------------------------------------------------------------------------
    def _on_report(self, event) -> None:
        name = self._by_twin.get(event.twin_id)
        if name is not None:
            self._update_effect(name)
            door = self.devices[name].door
            if door is not None and door in self.world.doors:
                self._update_effect(door)

    def _update_effect(self, key: str) -> None:
        if key in self.devices:
            twin = self.twin(key)
            locked, on = is_locked(twin), is_on(twin)
            effect = {"locked": locked} if locked is not None else {"on": on} if on is not None else None
        else:
            effect = {"open": self.world.doors[key], "locked": self._door_locked(key)}
        if effect is not None and self.effects.get(key) != effect:
            self.effects[key] = effect
            for fn in self._effect_handlers:
                fn(key, effect)
