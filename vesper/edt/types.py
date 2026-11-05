"""Types the environmental-differential-testing tasks share: an environment, a firing, an outcome."""
from __future__ import annotations

from dataclasses import dataclass, field


def _escape(text: str, keep: str = "") -> str:
    """Letters and digits (and `keep`) stay; every other character becomes `.<hex code point>.`. Injective."""
    return "".join(c if (c.isascii() and c.isalnum()) or c in keep else f".{ord(c):x}." for c in text)


def _part(value: str | None, keep: str = "") -> str:
    if value is None:
        return "none"
    return ".6e.one" if value == "none" else _escape(value, keep)  # a real "none" must not collide with a missing one


def _north(deg: float | None) -> str:
    if deg is None:
        return "nnone"
    body = str(int(deg)) if float(deg).is_integer() else repr(float(deg))
    return "n" + body.replace("-", "m")


@dataclass(frozen=True)
class Env:
    """Where, when and how a home is placed: a city, a date, the home's north, and the environment-model rung."""
    city: str | None
    date: str | None
    north_deg: float | None
    rung: int = 4

    @property
    def key(self) -> str:
        """A filename-safe string, unique per Env: `<city>-<date>-n<north>-r<rung>` ("none" for a missing part)."""
        return f"{_part(self.city)}-{_part(self.date, '-')}-{_north(self.north_deg)}-r{self.rung}"


@dataclass
class Firing:
    """One automation action that ran: when (logical seconds), which automation, which service, on what, and the
    command parts the engine sent with it ((feature, value) pairs; empty for an action it only records)."""
    t: float
    automation: str
    service: str
    target: str
    data: tuple = field(default=(), compare=False)  # kept for the richer outcome (protocol A6.4), not identity


@dataclass
class Outcome:
    """What an automation did over a run in one Env."""
    fired: bool
    first_tod_h: float | None
    flaps: bool
    night_light: bool
    night_cover: bool
