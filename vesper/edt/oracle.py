"""The oracle: what one automation did in one environment, and where environments disagree.

Thresholds (fixed by the experiment design):
- measured window: days 1-2 of a three-day run (day 0 is warm-up); a firing's `t` counts seconds from the site's
  first-day local midnight, so its local time of day is (t % 86400) / 3600;
- flapping: more than 6 firings of one automation inside one hour;
- time divergence: the first firing's local time of day differs by more than 3 h between environments that fire;
- night is 00:00-05:00 local; "lamp left on all night" is a light the automation turned on and nothing turned off
  (same entity, light.turn_off) before the next 01:00; "cover open at night" is a cover the automation opened after
  that day's sunset and did not close (cover.close_cover, same entity) before that day's midnight.

A turn-off or close is looked up among all the firings given, not only the measured ones, so an off that falls just
past the measured window still counts; the run itself ends at day 3, so nothing is seen beyond it. A day on which
the sun does not set has no "after sunset", so no night-cover finding.
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
    by_auto: dict[str, list[float]] = {}
    for fr in firings:
        by_auto.setdefault(fr.automation, []).append(fr.t)
    for times in by_auto.values():
        times.sort()
        n = FLAP_COUNT + 1
        if any(times[i + n - 1] - times[i] <= FLAP_WINDOW_S for i in range(len(times) - n + 1)):
            return True
    return False


def _night_light(measured: list[Firing], every: list[Firing]) -> bool:
    for on in measured:
        if on.service != "light.turn_on":
            continue
        deadline = _next_deadline(on.t)
        if not any(o.service == "light.turn_off" and o.target == on.target and on.t < o.t <= deadline for o in every):
            return True
    return False


def _night_cover(measured: list[Firing], every: list[Firing], site) -> bool:
    for op in measured:
        if op.service != "cover.open_cover":
            continue
        day = int(op.t // DAY_S)
        sunset = sun_events(site, day)["sunset"]
        midnight = (day + 1) * DAY_S
        if sunset is None or not sunset <= op.t < midnight:
            continue
        if not any(c.service == "cover.close_cover" and c.target == op.target and op.t < c.t < midnight
                   for c in every):
            return True
    return False


def outcome(firings: list[Firing], site, measured_days: tuple[int, int] = (1, 2)) -> Outcome:
    """Classify one automation's firings in one environment, over the measured days (inclusive)."""
    lo, hi = measured_days[0] * DAY_S, (measured_days[1] + 1) * DAY_S
    measured = sorted((fr for fr in firings if lo <= fr.t < hi), key=lambda fr: fr.t)
    if not measured:
        return Outcome(False, None, False, False, False)
    return Outcome(fired=True, first_tod_h=(measured[0].t % DAY_S) / HOUR_S, flaps=_flaps(measured),
                   night_light=_night_light(measured, firings), night_cover=_night_cover(measured, firings, site))


def divergences(by_env: dict[Env, Outcome]) -> set[str]:
    """The kinds of disagreement among a set of environments' outcomes."""
    out: set[str] = set()
    outcomes = list(by_env.values())
    fired = [o for o in outcomes if o.fired]
    if fired and len(fired) < len(outcomes):
        out.add("fires_somewhere_only")
    tods = [o.first_tod_h for o in fired if o.first_tod_h is not None]
    if tods and max(tods) - min(tods) > TIME_SPREAD_H:
        out.add("time_spread")
    for kind in ("flaps", "night_light", "night_cover"):
        if any(getattr(o, kind) for o in outcomes):
            out.add(kind)
    return out
