"""Where a home is, and when its first day is: `site:` in home.yaml.

    site: {lat: 35.2271, lon: -80.8431, elevation_m: 229, tz: America/New_York, north_deg: 0, date: 2026-01-15}

`north_deg` orients the floor plan: the compass bearing, clockwise from true north, of the plan's top (the 3D
view's -z). A wall whose outside faces the plan's top faces `north_deg`; one facing the plan's right faces
`north_deg + 90`. `date` is the local date of the session's first day, so a run's clock (seconds after that day's
local midnight) is a real time, and a logical run replays the same day every time.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SiteError(ValueError):
    """A site that cannot be a place on Earth."""


@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    tz: str
    date: dt.date
    elevation_m: float = 0.0
    north_deg: float = 0.0
    name: str | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def local(self, seconds: float) -> dt.datetime:
        """The local time `seconds` after the first day's local midnight (several days on, DST included)."""
        midnight = dt.datetime.combine(self.date, dt.time(0), tzinfo=self.zone)
        return (midnight.astimezone(dt.UTC) + dt.timedelta(seconds=seconds)).astimezone(self.zone)

    def utc(self, seconds: float) -> dt.datetime:
        return self.local(seconds).astimezone(dt.UTC)

    def bearing(self, plan_deg: float) -> float:
        """The compass bearing of a direction given clockwise from the plan's top."""
        return (self.north_deg + plan_deg) % 360

    def as_dict(self) -> dict:
        out = {"lat": self.lat, "lon": self.lon, "elevation_m": self.elevation_m, "tz": self.tz,
               "north_deg": self.north_deg, "date": self.date.isoformat()}
        return {**out, "name": self.name} if self.name else out


# Places the lobby offers by name. The first five are the validation sites' cities and climates far apart.
CITIES: dict[str, dict] = {
    "Charlotte, NC, USA": {"lat": 35.2271, "lon": -80.8431, "elevation_m": 229, "tz": "America/New_York"},
    "Phoenix, AZ, USA": {"lat": 33.4484, "lon": -112.0740, "elevation_m": 331, "tz": "America/Phoenix"},
    "Seattle, WA, USA": {"lat": 47.6062, "lon": -122.3321, "elevation_m": 56, "tz": "America/Los_Angeles"},
    "Valencia, Spain": {"lat": 39.4699, "lon": -0.3763, "elevation_m": 15, "tz": "Europe/Madrid"},
    "Chièvres, Belgium": {"lat": 50.5876, "lon": 3.8077, "elevation_m": 60, "tz": "Europe/Brussels"},
    "Minneapolis, MN, USA": {"lat": 44.9778, "lon": -93.2650, "elevation_m": 264, "tz": "America/Chicago"},
    "Stockholm, Sweden": {"lat": 59.3293, "lon": 18.0686, "elevation_m": 28, "tz": "Europe/Stockholm"},
    "Singapore": {"lat": 1.3521, "lon": 103.8198, "elevation_m": 15, "tz": "Asia/Singapore"},
    "Sydney, Australia": {"lat": -33.8688, "lon": 151.2093, "elevation_m": 58, "tz": "Australia/Sydney"},
}


def _number(raw: dict, key: str, lo: float, hi: float, default: float | None = None) -> float:
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SiteError(f"site.{key}: a number from {lo:g} to {hi:g}" + ("" if default is None else
                                                                         f" (default {default:g})"))
    if not lo <= v <= hi:
        raise SiteError(f"site.{key}: {v} is not from {lo:g} to {hi:g}")
    return float(v)


def parse_site(raw) -> Site | None:
    """A home's `site:`, or None when it has none (the assumed indoor curve then stands, as before)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SiteError("site: a mapping with lat, lon, tz and date (or a city from vesper.env.site.CITIES)")
    if "city" in raw:
        city = CITIES.get(raw["city"])
        if city is None:
            raise SiteError(f"site.city: {raw['city']!r} is not one of {', '.join(CITIES)}")
        raw = {**city, "name": raw["city"], **{k: v for k, v in raw.items() if k != "city"}}
    tz = raw.get("tz")
    try:
        ZoneInfo(tz) if isinstance(tz, str) else None
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise SiteError(f"site.tz: {tz!r} is not an IANA time zone") from e
    if not isinstance(tz, str):
        raise SiteError("site.tz: an IANA time zone, such as America/New_York")
    date = raw.get("date")
    if isinstance(date, str):
        try:
            date = dt.date.fromisoformat(date)
        except ValueError as e:
            raise SiteError(f"site.date: {date!r} is not YYYY-MM-DD") from e
    if not isinstance(date, dt.date) or isinstance(date, dt.datetime):
        raise SiteError("site.date: the local date of the first day, YYYY-MM-DD")
    name = raw.get("name")
    return Site(lat=_number(raw, "lat", -90, 90), lon=_number(raw, "lon", -180, 180), tz=tz, date=date,
                elevation_m=_number(raw, "elevation_m", -500, 9000, 0.0),
                north_deg=_number(raw, "north_deg", -360, 360, 0.0) % 360,
                name=name if isinstance(name, str) else None)
