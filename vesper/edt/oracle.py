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
