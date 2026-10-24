"""Hosts many twins on one clock: human use, platform commands, physical stimuli, reporting (spec §3.3).

Every observable change becomes an `Event` with two timestamps: when it physically happened and when
the platform observes it (after the reporting rule and the latency model). A twin that raises an
unexpected error is marked faulted and isolated; the others keep running.
"""
from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from vesper.twins.clock import LogicalClock
from vesper.twins.twin import Twin, TwinError

OCCUPANCY_TIMEOUT_S = 90.0  # assumed: Zigbee2MQTT's common occupancy_timeout default; real devices vary
# Matter twins name features by cluster and attribute (spec §3.1); a physical stimulus drives them with Matter's
# own semantics and units.
MATTER_PRESENCE = {"Occupancy Sensing.Occupancy": (1, 0)}  # the Occupied bit: set, clear
MATTER_CONTACT = "Boolean State.StateValue"  # true while a Contact Sensor's contact is closed
MATTER_AMBIENT = {"temperature": ("Temperature Measurement.MeasuredValue", "Thermostat.LocalTemperature"),
                  "humidity": ("Relative Humidity Measurement.MeasuredValue",),
                  "illuminance": ("Illuminance Measurement.MeasuredValue",)}
MATTER_LUX = "Illuminance Measurement.MeasuredValue"  # 10000 * log10(lux) + 1, 0 for darkness


@dataclass(frozen=True)
class Event:
    twin_id: str
    feature: str
    value: object
    physical_t: float  # when the change happened in the world
    observed_t: float  # when the platform sees the report
    cause: str  # "stimulus", "human", "platform" or "periodic"


@dataclass
class _Reporter:
    last_value: object = None
    last_t: float | None = None
    pending: bool = False
    changed_at: float = 0.0
    cause: str = "stimulus"
    drifted: bool = False  # the pending change fell back within `change` of the last report


@dataclass
class Action:
    t: float
    twin_id: str
    feature: str
    value: object
    actor: str  # "human:<who>" or "platform"
    accepted: bool
    reason: str = ""


@dataclass
class Runtime:
    clock: object = field(default_factory=LogicalClock)
    seed: int = 0
    latency: Callable[[Twin, str, str], float] | None = None  # (twin, feature, cause) -> seconds; default 0
    twins: dict[str, Twin] = field(default_factory=dict)
    faulted: dict[str, str] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[Event], None]] = []
        self._reporters: dict[tuple[str, str], _Reporter] = {}
        self._last_presence: dict[str, float] = {}
        self._last_observed: dict[str, float] = {}  # per twin: reports reach the platform in the order they left

    # -- set-up -------------------------------------------------------------------------------
    def add(self, vds: dict, twin_id: str | None = None, state: dict | None = None) -> Twin:
        """Install a twin, optionally in a given `state` that counts as already reported (a replay starts
        a twin where the real device was)."""
        with self._lock:
            if (twin_id or vds["id"]) in self.twins:
                raise ValueError(f"twin {twin_id or vds['id']} already added")
            twin = Twin.from_vds(vds, seed=self.seed, twin_id=twin_id)
            for name, value in (state or {}).items():
                twin.state[name] = twin.coerce(name, value)
            self.twins[twin.id] = twin
            for name in twin.features:
                rule = self._rule(twin, name)
                if rule:
                    self._reporters[(twin.id, name)] = _Reporter(last_value=twin.state[name], last_t=self.clock.now())
                    self._schedule_periodic(twin, name, self.clock.now() + rule["max_s"])
            return twin

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subscribers.append(fn)

    def state(self, twin_id: str) -> dict:
        with self._lock:
            return dict(self.twins[twin_id].state)

    # -- inputs -------------------------------------------------------------------------------
    def operate(self, twin_id: str, feature: str, value, actor: str = "resident") -> bool:
        """A person uses the device (spec §3.3 Human use); logged apart from platform commands."""
        return self._command(twin_id, feature, value, f"human:{actor}", "human")

    def platform_set(self, twin_id: str, feature: str, value) -> bool:
        """A command arriving through an exposure tier; rejected exactly where the platform would reject."""
        return self._command(twin_id, feature, value, "platform", "platform")

    def stimulate(self, twin_id: str, stimulus: dict) -> list[str]:
        """A physical input (spec §3.3): presence, contact, button_press, load, ambient, or `measured` (one
        feature takes the value the device measured, e.g. a real device's reported value in a replay)."""
        with self._lock:
            twin = self.twins.get(twin_id)
            if twin is None or twin_id in self.faulted:
                return []
            if not isinstance(stimulus, dict):
                self.notes.append(f"{twin_id}: stimulus rejected: not an object: {stimulus!r}")
                return []
            try:
                changes = [(n, twin.clamp(n, v)) for n, v in self._stimulus_changes(twin, stimulus)]
            except (KeyError, TypeError, ValueError) as e:  # a malformed stimulus, not a broken twin
                self.notes.append(f"{twin_id}: stimulus rejected: malformed {stimulus!r}: {e!r}")
                return []
            try:
                for name, value in changes:
                    self._change(twin, name, value, "stimulus")
                if stimulus.get("type") == "presence" and stimulus.get("present", True) and changes:
                    self._schedule_presence_clear(twin, [n for n, _ in changes])
                if not changes:
                    self.notes.append(f"{twin_id}: stimulus {stimulus.get('type')!r} has no matching feature")
                return [n for n, _ in changes]
            except TwinError as e:
                self.notes.append(f"{twin_id}: stimulus rejected: {e}")
                return []
            except Exception as e:  # isolate a misbehaving twin
                self.faulted[twin_id] = f"{type(e).__name__}: {e}"
                return []

    # -- internals ----------------------------------------------------------------------------
    def _command(self, twin_id, feature, value, actor, cause) -> bool:
        with self._lock:
            twin = self.twins.get(twin_id)
            if twin is None:
                raise KeyError(twin_id)
            if twin_id in self.faulted:
                self.actions.append(Action(self.clock.now(), twin_id, feature, value, actor, False, "twin faulted"))
                return False
            try:
                f = twin.feature(feature)
                if "set" not in f["access"]:
                    raise TwinError(f"{twin_id}.{feature} is read-only")
                new = twin.coerce(feature, value)
            except TwinError as e:
                self.actions.append(Action(self.clock.now(), twin_id, feature, value, actor, False, str(e)))
                return False
            except Exception as e:
                self.faulted[twin_id] = f"{type(e).__name__}: {e}"
                return False
            self.actions.append(Action(self.clock.now(), twin_id, feature, new, actor, True))
            self._change(twin, feature, new, cause)
            return True

    def _guarded(self, twin: Twin, fn: Callable[[], None]) -> Callable[[], None]:
        """A scheduled callback that, if it fails, faults only its own twin (spec §5 isolation)."""
        def run():
            try:
                fn()
            except Exception as e:
                with self._lock:
                    self.faulted[twin.id] = f"{type(e).__name__}: {e}"
        return run

    def _rule(self, twin: Twin, name: str) -> dict | None:
        return twin.vds.get("behavior", {}).get("reporting", {}).get(name)

    def _change(self, twin: Twin, name: str, value, cause: str) -> None:
        old = twin.state[name]
        twin.state[name] = value
        rule = self._rule(twin, name)
        now = self.clock.now()
        if rule is None:
            if value != old or name == "action":  # every button press is an event, even a repeated one
                self._emit(twin, name, value, now, cause)
            return
        rep = self._reporters[(twin.id, name)]
        if not self._significant(rep.last_value, value, rule["change"]):
            if rep.pending:
                rep.drifted = True
            return
        if rep.last_t is None or now >= rep.last_t + rule["min_s"]:
            self._report(twin, name, now, cause)
        elif not rep.pending:
            rep.pending, rep.changed_at, rep.cause, rep.drifted = True, now, cause, False
            self.clock.call_at(rep.last_t + rule["min_s"], self._guarded(twin, lambda: self._flush(twin, name)))
        elif rep.drifted:  # reportable again after drifting back: this is the change the report carries
            rep.changed_at, rep.cause, rep.drifted = now, cause, False

    @staticmethod
    def _significant(old, new, change) -> bool:
        if isinstance(new, (int, float)) and not isinstance(new, bool) and isinstance(old, (int, float)):
            return abs(new - old) >= change if change else new != old
        return new != old

    def _flush(self, twin: Twin, name: str) -> None:
        with self._lock:
            rep = self._reporters[(twin.id, name)]
            if rep.pending and twin.id not in self.faulted:
                rep.pending = False
                # the value may have drifted back meanwhile: a deferred report still needs a reportable change
                if self._significant(rep.last_value, twin.state[name], self._rule(twin, name)["change"]):
                    self._report(twin, name, rep.changed_at, rep.cause)

    def _report(self, twin: Twin, name: str, physical_t: float, cause: str) -> None:
        rep = self._reporters[(twin.id, name)]
        rep.last_value, rep.last_t = twin.state[name], self.clock.now()
        self._emit(twin, name, twin.state[name], physical_t, cause)

    def _schedule_periodic(self, twin: Twin, name: str, when: float) -> None:
        def tick():
            with self._lock:
                if twin.id in self.faulted:
                    return
                rule, rep = self._rule(twin, name), self._reporters[(twin.id, name)]
                # compare with the same sum the tick was scheduled at: now - last_t can round below max_s
                if rep.last_t is not None and self.clock.now() >= rep.last_t + rule["max_s"]:
                    self._report(twin, name, self.clock.now(), "periodic")
                self._schedule_periodic(twin, name, rep.last_t + rule["max_s"])
        self.clock.call_at(when, self._guarded(twin, tick))

    def _emit(self, twin: Twin, name: str, value, physical_t: float, cause: str) -> None:
        try:
            delay = max(0.0, self.latency(twin, name, cause)) if self.latency else 0.0
        except Exception as e:  # a broken latency model faults this twin only
            self.faulted[twin.id] = f"{type(e).__name__}: {e}"
            return
        observed = max(self.clock.now() + delay, self._last_observed.get(twin.id, float("-inf")))
        self._last_observed[twin.id] = observed
        event = Event(twin.id, name, value, physical_t, observed, cause)
        if observed <= self.clock.now():
            self._deliver(event)
        else:  # same-time callbacks keep their order, so one twin's reports never overtake each other
            self.clock.call_at(event.observed_t, self._guarded(twin, lambda: self._deliver(event)))

    def _deliver(self, event: Event) -> None:
        for fn in self._subscribers:
            try:
                fn(event)
            except Exception as e:  # a failing subscriber must not stop the runtime
                self.notes.append(f"subscriber failed on {event.twin_id}.{event.feature}: {e!r}")

    # -- physical stimuli ---------------------------------------------------------------------
    def _stimulus_changes(self, twin: Twin, s: dict) -> list[tuple[str, object]]:
        kind, feats, out = s.get("type"), twin.features, []

        def binary(name, on):
            f = feats.get(name)
            if f and f["type"] == "binary":
                out.append((name, f["value_on"] if on else f["value_off"]))

        if kind == "presence":
            for name in ("occupancy", "presence"):
                binary(name, s.get("present", True))
            for name, (on, off) in MATTER_PRESENCE.items():
                if name in feats:
                    out.append((name, on if s.get("present", True) else off))
        elif kind == "contact":
            if s.get("state") not in ("opened", "closed"):
                raise TwinError(f"contact stimulus needs state 'opened' or 'closed', got {s.get('state')!r}")
            binary("contact", s["state"] == "opened")  # Zigbee2MQTT's contact is "on" (value_on) when open
            if _matter_type(twin) == "Contact Sensor" and MATTER_CONTACT in feats:
                out.append((MATTER_CONTACT, s["state"] == "closed"))
        elif kind == "button_press":
            f = feats.get("action")
            if f and f["type"] == "enum" and f.get("values"):
                wanted = s.get("action")
                out.append(("action", wanted if wanted in f["values"] else f["values"][0]))
        elif kind == "load":
            if "power" in feats:
                out.append(("power", float(s["watts"])))
        elif kind == "measured":
            name = s["feature"]
            out.append((name, twin.coerce(name, twin.clamp(name, s["value"]))))
        elif kind == "ambient":
            for key, names in (("temperature", ("temperature", "local_temperature")), ("humidity", ("humidity",)),
                               ("illuminance", ("illuminance", "illuminance_lux"))):
                if key in s:
                    out.extend((n, s[key]) for n in names if n in feats and feats[n]["type"] == "numeric")
            for key, names in MATTER_AMBIENT.items():
                if key in s:
                    out.extend((n, _matter_raw(n, feats[n], s[key])) for n in names
                               if n in feats and feats[n]["type"] == "numeric")
        else:
            raise TwinError(f"unknown stimulus type {kind!r}")
        return out

    def _schedule_presence_clear(self, twin: Twin, names: list[str]) -> None:
        stamp = self.clock.now()
        self.notes.append(f"{twin.id}: occupancy clears after {OCCUPANCY_TIMEOUT_S:.0f} s (assumed timeout)")
        self._last_presence[twin.id] = stamp

        def clear():
            with self._lock:
                if self._last_presence.get(twin.id) == stamp and twin.id not in self.faulted:
                    for name in names:
                        off = MATTER_PRESENCE[name][1] if name in MATTER_PRESENCE else twin.features[name]["value_off"]
                        self._change(twin, name, off, "stimulus")
        self.clock.call_at(stamp + OCCUPANCY_TIMEOUT_S, self._guarded(twin, clear))


def _matter_type(twin: Twin) -> str | None:
    return ((twin.vds.get("binding") or {}).get("matter") or {}).get("device_type")


def _matter_raw(name: str, feature: dict, value: float):
    """A reading in the stimulus's unit (°C, %, lx) as the Matter attribute's raw value."""
    if name == MATTER_LUX:
        return round(10000 * math.log10(value) + 1) if value > 0 else 0
    scale = feature.get("scale")
    return round(value / scale) if scale else value
