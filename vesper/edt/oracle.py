"""The oracle: what one automation did in one environment, and where environments disagree.

Thresholds (fixed by the experiment design):
- measured window: days 1-2 of a three-day run (day 0 is warm-up); a firing's `t` counts seconds from the site's
  first-day local midnight, so its local time of day is (t % 86400) / 3600;
- flapping: more than 6 firings of one automation inside one hour;
- time divergence: the first firing's local time of day differs by more than 3 h (the short way round the
  clock) between two environments that fire;
- night is 00:00-05:00 local; "lamp left on all night" is a light that becomes on in the measured window and is not
  off by the next 01:00; "cover open at night" is a cover that becomes open after that day's sunset and is not closed
  by that day's midnight (a cover opened before sunrise is not judged: docs/experiments/edt-protocol.md, A1.1).

Lights and covers are resolved per target, in time order, from every firing given (warm-up included), starting off
and closed: turn_on/open sets on/open, turn_off/close sets off/closed, toggle flips. A lamp whose 01:00 deadline
falls after the run's end (the end of the last measured day) cannot be judged and is not flagged. A day on which
the sun does not set has no "after sunset", so no finding that day. With no
site at all (a rung-1 trace names none) there is no sunset: "cover open at night" is not judged.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from dataclasses import dataclass

from vesper.edt.types import Env, Firing, Outcome
from vesper.env.sun import sun_events

DAY_S = 86400.0
HOUR_S = 3600.0
FLAP_COUNT = 6          # more than this many firings of one automation ...
FLAP_WINDOW_S = HOUR_S  # ... within this span is flapping
TIME_SPREAD_H = 3.0
LAMP_DEADLINE_H = 1.0   # a lamp turned on must be off by the next 01:00


def _next_deadline(t: float) -> float:
    """The first 01:00 local strictly after `t`."""
    day = t // DAY_S
    d = day * DAY_S + LAMP_DEADLINE_H * HOUR_S
    return d if d > t else d + DAY_S


def _flaps(firings: list[Firing]) -> bool:
    """Seven or more distinct firing times of one automation inside one hour (one trigger can run several actions
    at one instant; those are one firing time)."""
    by_auto: dict[str, set[float]] = {}
    for fr in firings:
        by_auto.setdefault(fr.automation, set()).add(fr.t)
    n = FLAP_COUNT + 1
    for instants in by_auto.values():
        times = sorted(instants)
        if any(times[i + n - 1] - times[i] <= FLAP_WINDOW_S for i in range(len(times) - n + 1)):
            return True
    return False


def _state_after(firings: list[Firing], on: str, off: str, toggle: str) -> dict[str, list[tuple[float, bool]]]:
    """Per target, in time order, the state (True = on/open) after each of its firings; every target starts off."""
    out: dict[str, list[tuple[float, bool]]] = {}
    state: dict[str, bool] = {}
    for fr in sorted(firings, key=lambda fr: fr.t):
        if fr.service == on:
            new = True
        elif fr.service == off:
            new = False
        elif fr.service == toggle:
            new = not state.get(fr.target, False)
        else:
            continue
        state[fr.target] = new
        out.setdefault(fr.target, []).append((fr.t, new))
    return out


def _night_light(firings: list[Firing], lo: float, hi: float) -> bool:
    for steps in _state_after(firings, "light.turn_on", "light.turn_off", "light.toggle").values():
        for t, is_on in steps:
            if not is_on or not lo <= t < hi:
                continue
            deadline = _next_deadline(t)
            if deadline > hi:  # the run ends before the lamp could be judged
                continue
            if not any(not s and t < u <= deadline for u, s in steps):
                return True
    return False


def _night_cover(firings: list[Firing], lo: float, hi: float, site) -> bool:
    if site is None:  # no place, no sunset: not judged
        return False
    for steps in _state_after(firings, "cover.open_cover", "cover.close_cover", "cover.toggle").values():
        for t, is_open in steps:
            if not is_open or not lo <= t < hi:
                continue
            day = int(t // DAY_S)
            sunset = sun_events(site, day)["sunset"]
            at_night = sunset is not None and t >= sunset
            midnight = (day + 1) * DAY_S
            if at_night and midnight <= hi and not any(not s and t < u < midnight for u, s in steps):
                return True
    return False


def outcome(firings: list[Firing], site, measured_days: tuple[int, int] = (1, 2)) -> Outcome:
    """Classify one automation's firings in one environment, over the measured days (inclusive)."""
    lo, hi = measured_days[0] * DAY_S, (measured_days[1] + 1) * DAY_S
    measured = sorted((fr for fr in firings if lo <= fr.t < hi), key=lambda fr: fr.t)
    if not measured:
        return Outcome(False, None, False, False, False)
    return Outcome(fired=True, first_tod_h=(measured[0].t % DAY_S) / HOUR_S, flaps=_flaps(measured),
                   night_light=_night_light(firings, lo, hi), night_cover=_night_cover(firings, lo, hi, site))


def _circular_gap(a: float, b: float) -> float:
    """Hours between two times of day, the short way round the clock."""
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def divergences(by_env: dict[Env, Outcome]) -> set[str]:
    """The kinds of disagreement among a set of environments' outcomes."""
    out: set[str] = set()
    outcomes = list(by_env.values())
    fired = [o for o in outcomes if o.fired]
    if fired and len(fired) < len(outcomes):
        out.add("fires_somewhere_only")
    tods = [o.first_tod_h for o in fired if o.first_tod_h is not None]
    if any(_circular_gap(a, b) > TIME_SPREAD_H for i, a in enumerate(tods) for b in tods[i + 1:]):
        out.add("time_spread")
    for kind in ("flaps", "night_light", "night_cover"):
        if any(getattr(o, kind) for o in outcomes):
            out.add(kind)
    return out


# ---- the frame-relative relation (protocol Addendum 6, A6.1-A6.3) ---------------------------------------------------
#
# The clock relation above compares first firings by local clock time. A sun trigger is *specified* to fire at a
# different clock time in another place or season, so that relation flags behaviour the trigger commissions. The
# frame-relative relation compares an automation's first firings in the frame(s) its triggers and conditions declare:
# clock (time of day), sun (minutes from the sun event it names) or sensed (not computable: commissioned).

CLOCK, SUN, SENSED = "clock", "sun", "sensed"
FRAMES = (CLOCK, SUN, SENSED)
SENSED_KINDS = frozenset({"illuminance", "temperature", "humidity", "weather"})
ENVELOPE_KINDS = ("flaps", "night_light", "night_cover")
_NOW = re.compile(r"\b(utc)?now\s*\(")
_SUN_STATES = {"below_horizon": ("sunset",), "above_horizon": ("sunrise",)}
_LOGIC = ("and", "or", "not")


@dataclass(frozen=True)
class SunRef:
    """A sun instant an automation names: sunrise or sunset plus an offset (seconds), or the time the sun's
    elevation crosses `elevation` degrees, rising or setting."""
    event: str  # "sunrise", "sunset" or "elevation"
    offset_s: float = 0.0
    elevation: float | None = None
    rising: bool | None = None


@dataclass(frozen=True)
class Frames:
    """An automation's timing frames and the sun instants its sun frame is measured from (empty: not computable)."""
    frames: frozenset = frozenset()
    sun_refs: tuple = ()

    @property
    def judged(self) -> frozenset:
        """The frames it is judged in: its own, or the clock frame when it declares none."""
        return self.frames or frozenset({CLOCK})

    @property
    def mixed(self) -> bool:
        return len(self.frames) > 1

    def computable(self, frame: str) -> bool:
        return frame == CLOCK or (frame == SUN and bool(self.sun_refs))


def _as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def _texts(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in ("id", "alias", "conditions"):
                yield from _texts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _texts(v)


def _template_frames(step, out: set) -> None:
    """The frames a step's templates read: now() is the clock; sun.sun the sun; a sensed entity the sensed frame."""
    from vesper.edt.corpus import _TPL_ENTITY_RE, _entity_kinds
    for text in _texts(step):
        if "{{" not in text and "{%" not in text:
            continue
        if _NOW.search(text):
            out.add(CLOCK)
        for m in _TPL_ENTITY_RE.finditer(text):
            kinds = _entity_kinds(m.group(1) or m.group(2))
            if "sun" in kinds:
                out.add(SUN)
            if kinds & SENSED_KINDS:
                out.add(SENSED)


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _offset_s(value) -> float | None:
    """A sun offset in seconds, as the engine reads it (None when it cannot be read)."""
    from vesper.lab.automations import AutomationError, _offset
    if value is None:
        return 0.0
    try:
        return _offset(value, "offset")
    except (AutomationError, TypeError, ValueError):
        return None


def _entity_frames(step: dict, out: set, refs: list, condition: bool) -> None:
    """A state or numeric_state step: its entities' frames, and the sun instants a `sun.sun` one names."""
    from vesper.edt.corpus import _entity_kinds, _ids
    kind = str(step.get("platform") or step.get("trigger") or step.get("condition") or "")
    attribute = step.get("attribute") if isinstance(step.get("attribute"), str) else None
    for eid in _ids(step.get("entity_id")):
        kinds = _entity_kinds(eid, attribute)
        if eid.split(".", 1)[0] == "sun":
            out.add(SUN)
            if kind == "state" and attribute is None:
                wanted = _as_list(step.get("state") if condition else step.get("to"))
                events = [e for w in wanted for e in _SUN_STATES.get(str(w), ())] if wanted else \
                    ["sunrise", "sunset"]
                refs.extend(SunRef(e) for e in events)
            elif kind == "numeric_state" and attribute == "elevation":
                for key, rising in (("above", True), ("below", False)):
                    value = _number(step.get(key))
                    if value is not None:
                        refs.append(SunRef("elevation", elevation=value, rising=rising))
        if kinds & SENSED_KINDS:
            out.add(SENSED)


def _trigger_frames(t, out: set, refs: list) -> None:
    if not isinstance(t, dict):
        return
    kind = str(t.get("platform") or t.get("trigger") or "")
    if kind == "sun":
        out.add(SUN)
        offset = _offset_s(t.get("offset"))
        if t.get("event") in ("sunrise", "sunset") and offset is not None:
            refs.append(SunRef(t["event"], offset))
    elif kind in ("time", "time_pattern"):
        out.add(CLOCK)
    elif kind in ("state", "numeric_state"):
        _entity_frames(t, out, refs, condition=False)
    _template_frames(t, out)


def _condition_frames(c, out: set, refs: list) -> None:
    if isinstance(c, str):  # the shorthand template condition
        _template_frames(c, out)
        return
    if isinstance(c, list):
        for x in c:
            _condition_frames(x, out, refs)
        return
    if not isinstance(c, dict):
        return
    kind = c.get("condition")
    keys = set(c) - {"alias", "enabled"}
    if kind is None and len(keys) == 1 and next(iter(keys)) in _LOGIC:  # the `- or: [...]` shorthand
        _condition_frames(c[next(iter(keys))], out, refs)
        return
    if kind in _LOGIC:
        _condition_frames(c.get("conditions"), out, refs)
        return
    if kind == "sun":
        out.add(SUN)
        for key in ("after", "before"):
            offset = _offset_s(c.get(f"{key}_offset"))
            if c.get(key) in ("sunrise", "sunset") and offset is not None:
                refs.append(SunRef(c[key], offset))
    elif kind == "time":
        out.add(CLOCK)
    elif kind in ("state", "numeric_state"):
        _entity_frames(c, out, refs, condition=True)
    _template_frames(c, out)


def frames_of(bound: dict) -> Frames:
    """An automation's timing frames from its (bound) triggers and conditions (A6.1), and the sun instants its sun
    frame is measured from: its sun triggers', or, when they name none, its sun conditions' (A6.2)."""
    if not isinstance(bound, dict):
        return Frames()
    out: set[str] = set()
    trig_refs: list[SunRef] = []
    cond_refs: list[SunRef] = []
    for t in _as_list(bound.get("trigger", bound.get("triggers"))):
        _trigger_frames(t, out, trig_refs)
    _condition_frames(_as_list(bound.get("condition", bound.get("conditions"))), out, cond_refs)
    refs = trig_refs or cond_refs
    return Frames(frozenset(out), tuple(dict.fromkeys(refs)) if SUN in out else ())


@functools.lru_cache(maxsize=65536)
def _ref_instant(lat: float, lon: float, elevation_m: float, tz: str, date: str, day: int, ref: SunRef):
    """Seconds after the run's first local midnight of `ref` on run day `day` (None when it does not happen)."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    from vesper.env.site import Site
    site = Site(lat, lon, tz, dt.date.fromisoformat(date), elevation_m)
    if ref.event in ("sunrise", "sunset"):
        base = sun_events(site, day)[ref.event]
        return None if base is None else base + ref.offset_s
    from astral import Observer, SunDirection
    from astral.sun import time_at_elevation
    zone = ZoneInfo(tz)
    midnight = dt.datetime.combine(site.date, dt.time(0), tzinfo=zone)
    try:
        when = time_at_elevation(Observer(latitude=lat, longitude=lon, elevation=elevation_m), ref.elevation,
                                 site.date + dt.timedelta(days=day),
                                 SunDirection.RISING if ref.rising else SunDirection.SETTING, tzinfo=zone)
    except (ValueError, TypeError, ArithmeticError):
        return None
    return (when - midnight).total_seconds()


def sun_relative_min(site, refs, t: float) -> float | None:
    """Signed minutes from the nearest of `refs` (on the firing's day or a neighbouring one) to a firing at `t`
    (seconds after the run's first local midnight); None when no reference instant exists then."""
    if site is None or not refs:
        return None
    day = int(t // DAY_S)
    key = (site.lat, site.lon, site.elevation_m, site.tz, site.date.isoformat())
    instants = [x for d in (day - 1, day, day + 1) for r in refs if (x := _ref_instant(*key, d, r)) is not None]
    if not instants:
        return None
    return (t - min(instants, key=lambda x: abs(t - x))) / 60.0


@functools.lru_cache(maxsize=256)
def _site_of_env(city, date, north_deg):
    from vesper.edt.trace import env_site
    return env_site(Env(city, date, north_deg, 4))


def site_of(env: Env):
    """The environment's place and date (whatever its rung), or None when it names none."""
    if env.city is None or env.date is None:
        return None
    return _site_of_env(env.city, env.date, env.north_deg)


def _clock_spread(tods: list[float], h: float) -> bool:
    return any(_circular_gap(a, b) > h for i, a in enumerate(tods) for b in tods[i + 1:])


def frame_values(by_env: dict[Env, Outcome], first_t: dict[Env, float | None], frames: Frames) -> dict:
    """Per frame, the first firings' frame-relative values over the environments where it fires: clock, hours of
    the day; sun, minutes from the nearest reference (an environment with none that day left out)."""
    fired = [e for e, o in by_env.items() if o.fired and o.first_tod_h is not None]
    out = {CLOCK: [by_env[e].first_tod_h for e in fired]}
    if frames.sun_refs:
        vals = []
        for e in fired:
            t = first_t.get(e)
            v = None if t is None else sun_relative_min(site_of(e), frames.sun_refs, t)
            if v is not None:
                vals.append(v)
        out[SUN] = vals
    return out


def frame_invariance(by_env: dict[Env, Outcome], first_t: dict[Env, float | None], frames: Frames,
                     h: float = TIME_SPREAD_H, sensed_invariant: bool = True) -> dict[str, bool]:
    """Per judged frame, whether the first firings are invariant within `h` in it (A6.3); a frame that is not
    computable counts as invariant. `sensed_invariant=False` (A6.3's sensitivity): the sensed frame is left out when
    the automation has a computable frame."""
    vals = frame_values(by_env, first_t, frames)
    out: dict[str, bool] = {}
    for f in sorted(frames.judged):
        if f == CLOCK:
            out[f] = not _clock_spread(vals[CLOCK], h)
        elif f == SUN:
            v = vals.get(SUN, [])
            out[f] = not frames.sun_refs or len(v) < 2 or max(v) - min(v) <= h * 60.0
        else:
            out[f] = True
    if not sensed_invariant and SENSED in out and any(frames.computable(f) for f in out if f != SENSED):
        del out[SENSED]
    return out


def frame_divergences(by_env: dict[Env, Outcome], first_t: dict[Env, float | None], frames: Frames,
                      h: float = TIME_SPREAD_H, sensed_invariant: bool = True) -> set[str]:
    """The kinds of disagreement under the frame-relative relation (A6.3): the clock relation's, with `time_spread`
    held iff the first firings are invariant within `h` in none of the automation's frames."""
    out = divergences(by_env) - {"time_spread"}
    inv = frame_invariance(by_env, first_t, frames, h, sensed_invariant)
    if inv and not any(inv.values()):
        out.add("time_spread")
    return out


def frame_restricted_divergences(by_env: dict[Env, Outcome], first_t: dict[Env, float | None], frames: Frames,
                                 envs, h: float = TIME_SPREAD_H, sensed_invariant: bool = True) -> set[str]:
    """Divergences under the frame relation over `envs` alone, minus an envelope kind holding in all of them."""
    outs = {e: by_env[e] for e in envs if e in by_env}
    if not outs:
        return set()
    kinds = frame_divergences(outs, first_t, frames, h, sensed_invariant)
    return {k for k in kinds if k not in ENVELOPE_KINDS or not all(getattr(o, k) for o in outs.values())}


# ---- the richer outcome (A6.4; reported only, never part of a flag) -------------------------------------------------

def rich_outcome(firings: list[Firing], branches=None, measured_days: tuple[int, int] = (1, 2)) -> dict:
    """Beside the outcome, in the judged window: the first firing's time (seconds after the run's first local
    midnight), the firing count (distinct instants), a canonical hash of the set of distinct (service, entity, data)
    calls, and the sorted set of (choose step, option) pairs taken (`branches`: (t, step, option) tuples)."""
    lo, hi = measured_days[0] * DAY_S, (measured_days[1] + 1) * DAY_S
    measured = [fr for fr in firings if lo <= fr.t < hi]
    calls = sorted({json.dumps([fr.service, fr.target, [list(p) for p in (fr.data or ())]], default=str)
                    for fr in measured})
    taken = sorted({(int(n), int(opt)) for t, n, opt in (branches or ()) if lo <= t < hi})
    return {"first_t": min((fr.t for fr in measured), default=None),
            "count": len({fr.t for fr in measured}),
            "payload": hashlib.sha256(json.dumps(calls).encode()).hexdigest()[:16] if calls else None,
            "branches": [list(b) for b in taken]}


def rich_divergences(rich_by_env: dict) -> set[str]:
    """`count_differs`, `payload_differs`, `branch_differs` over the environments where the automation fires."""
    fired = [r for r in rich_by_env.values() if r and r.get("count")]
    out: set[str] = set()
    if len({r["count"] for r in fired}) > 1:
        out.add("count_differs")
    if len({r.get("payload") for r in fired}) > 1:
        out.add("payload_differs")
    if len({json.dumps(r.get("branches") or []) for r in fired}) > 1:
        out.add("branch_differs")
    return out
