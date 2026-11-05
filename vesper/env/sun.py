"""The sun over a site: elevation and azimuth by astral, the library Home Assistant's `sun.sun` uses, so the two
agree on the same site and clock; and the clear-sky irradiance that stands in when no weather is recorded.

Clear sky is Haurwitz's model (GHI = 1098 cos z exp(-0.057 / cos z) W/m², z the zenith angle; Haurwitz 1945, as
pvlib implements it), the simplest published clear-sky model; it is an assumption, since a real sky has clouds.
Outdoor horizontal illuminance is irradiance times a luminous efficacy: 110 lm/W, a typical global daylight value
(Perez et al. 1990 give about 100 to 125), assumed.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from astral import Observer
from astral.sun import azimuth, elevation

LUMINOUS_EFFICACY = 110.0  # lm/W, global daylight (assumed; Perez et al. 1990 range ~100-125)
# the beam share of horizontal irradiance under a clear sky (assumed); a sensitivity knob (revision R6-B, protocol
# Addendum 9), set only through vesper.env.indoor.knobs; 0.8 as built
DIRECT_SHARE = 0.8


@dataclass(frozen=True)
class Sun:
    elevation: float  # degrees above the horizon (refraction included, as Home Assistant reports it)
    azimuth: float  # compass bearing, degrees clockwise from true north

    @property
    def up(self) -> bool:
        return self.elevation > 0

    def vector(self) -> tuple[float, float, float]:
        """A unit vector toward the sun: (east, up, north)."""
        e, a = math.radians(self.elevation), math.radians(self.azimuth)
        return (math.cos(e) * math.sin(a), math.sin(e), math.cos(e) * math.cos(a))


def sun_at(lat: float, lon: float, elevation_m: float, when: dt.datetime) -> Sun:
    """The sun's position at a time (an aware datetime)."""
    obs = Observer(latitude=lat, longitude=lon, elevation=elevation_m)
    when = when.astimezone(dt.UTC)
    return Sun(elevation=round(elevation(obs, when), 3), azimuth=round(azimuth(obs, when) % 360, 3))


def clear_sky_ghi(sun_elevation_deg: float) -> float:
    """Global horizontal irradiance under a clear sky, W/m² (Haurwitz); 0 with the sun down."""
    cz = math.sin(math.radians(sun_elevation_deg))
    return 0.0 if cz <= 0 else 1098.0 * cz * math.exp(-0.057 / cz)


def lux(ghi_w_m2: float) -> float:
    """Outdoor horizontal illuminance from global horizontal irradiance (assumed efficacy)."""
    return max(0.0, ghi_w_m2) * LUMINOUS_EFFICACY


def direct_fraction(cloud_cover_pct: float | None) -> float:
    """The share of horizontal irradiance that comes straight from the sun, not from the sky: about 0.8 under a clear
    sky (DIRECT_SHARE), falling to 0 under overcast (assumed linear in cloud cover)."""
    c = 0.0 if cloud_cover_pct is None else min(max(cloud_cover_pct, 0.0), 100.0) / 100
    return DIRECT_SHARE * (1 - c)


def sun_events(site, day: int) -> dict[str, float | None]:
    """Sunrise and sunset of a run's day (`day` 0 is the site's first day), as seconds after the first day's local
    midnight, as astral gives them to Home Assistant (the sun's upper limb, refraction included); None for a day
    the sun does not rise or set (inside the polar circles)."""
    from astral.sun import sunrise, sunset
    obs = Observer(latitude=site.lat, longitude=site.lon, elevation=site.elevation_m)
    date = site.date + dt.timedelta(days=day)
    midnight = dt.datetime.combine(site.date, dt.time(0), tzinfo=site.zone)
    out: dict[str, float | None] = {}
    for name, fn in (("sunrise", sunrise), ("sunset", sunset)):
        try:
            out[name] = (fn(obs, date, tzinfo=site.zone) - midnight).total_seconds()
        except ValueError:
            out[name] = None
    return out
