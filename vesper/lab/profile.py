"""A resident's profile (spec §5a): the habits, the comfort, and which devices an activity uses.

    profile: {forget_light: 0.2, forget_lock: 0.1, dark_lux: 50, bedtime: "20:00",
              uses: {EATING: [plug], LEISURE: [plug]}}

- forget_light: leaving a room, the chance of leaving its light on;
- forget_lock: leaving home or going to bed, the chance of not locking the front door;
- dark_lux: a room is too dark for an activity below this daylight (the rules' assumed ambient model);
- bedtime: a SLEEP task that starts after this is going to bed;
- uses: per task category, the device categories the task switches on while it lasts and off after
  ("cooking uses the kitchen plug").
Every value has a default; randomness comes from the home's seed, so a day replays exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from vesper.lab.routine import CATEGORIES, RoutineError, _time

DEFAULT_USES = {"EATING": ("plug",), "HOUSEHOLD": ("plug",), "LEISURE": ("plug",), "WORK": ("plug",)}
SETTINGS = ("forget_light", "forget_lock", "dark_lux", "bedtime", "uses")


class ProfileError(ValueError):
    """A profile VESPER cannot use; the message names the setting."""


@dataclass(frozen=True)
class Profile:
    forget_light: float = 0.2
    forget_lock: float = 0.1
    dark_lux: float = 50.0
    bedtime: float = 20 * 3600  # seconds after midnight
    uses: dict = field(default_factory=lambda: dict(DEFAULT_USES))


def load_profile(spec, where: str) -> Profile:
    if spec is None or spec == "default":
        return Profile()
    if not isinstance(spec, dict):
        raise ProfileError(f"{where}: a profile is a mapping of settings, or `default`")
    for key in spec:
        if key not in SETTINGS:
            raise ProfileError(f"{where}: unknown setting {key!r}; the settings are {', '.join(SETTINGS)}")
    values = {}
    for key in ("forget_light", "forget_lock"):
        if key in spec:
            v = spec[key]
            if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 1:
                raise ProfileError(f"{where}.{key}: {v!r} is not a probability (0 to 1)")
            values[key] = float(v)
    if "dark_lux" in spec:
        v = spec["dark_lux"]
        if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
            raise ProfileError(f"{where}.dark_lux: {v!r} is not an illuminance in lux (0 or more)")
        values["dark_lux"] = float(v)
    if "bedtime" in spec:
        try:
            values["bedtime"] = _time(spec["bedtime"], f"{where}.bedtime")
        except RoutineError as e:
            raise ProfileError(str(e)) from None
    if "uses" in spec:
        uses = spec["uses"]
        if not isinstance(uses, dict):
            raise ProfileError(f"{where}.uses: a mapping of task category to device categories")
        out = {}
        for category, devices in uses.items():
            if category not in CATEGORIES:
                raise ProfileError(f"{where}.uses: {category!r} is not one of {', '.join(CATEGORIES)}")
            if not isinstance(devices, list) or not all(isinstance(d, str) for d in devices):
                raise ProfileError(f"{where}.uses.{category}: a list of device categories, such as [plug]")
            out[category] = tuple(devices)
        values["uses"] = out
    return Profile(**values)
