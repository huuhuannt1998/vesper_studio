"""The outdoors of a site at a moment of a run: sun, global horizontal irradiance and illuminance, temperature,
humidity and cloud cover, each with where it came from.

With recorded weather for the time, irradiance, temperature, humidity and cloud cover are recorded (Open-Meteo);
without it, irradiance is clear-sky (Haurwitz) and temperature and humidity follow an assumed daily curve. The sun is
always computed. `provenance` names each value's source, for the run's trace header and the fidelity report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from vesper.env import sun as sunmod
from vesper.env import weather as wx
from vesper.env.site import Site

# The fallback without recorded weather (assumed): 15 °C ± 5, warmest at 15:00 local; 60 % relative humidity.
RUN_DAYS = 3  # the days a session and `vesper lab env --fetch` cover from the site's first day
FALLBACK_MEAN_C, FALLBACK_SWING_C, FALLBACK_RH = 15.0, 5.0, 60.0


@dataclass(frozen=True)
class OutdoorState:
    sun: sunmod.Sun
    ghi_w_m2: float
    illuminance_lux: float
    temperature_c: float
    humidity_pct: float
    cloud_cover_pct: float | None
    direct_fraction: float  # share of horizontal irradiance straight from the sun
    sources: dict = field(default_factory=dict)
    snow_depth_m: float | None = None  # ERA5's, when a revision R7-W option has the recorded shortwave read

    def as_dict(self) -> dict:
        return {"sun_elevation": self.sun.elevation, "sun_azimuth": self.sun.azimuth,
                "ghi_w_m2": round(self.ghi_w_m2, 1), "illuminance_lux": round(self.illuminance_lux),
                "temperature_c": round(self.temperature_c, 1), "humidity_pct": round(self.humidity_pct),
                "cloud_cover_pct": self.cloud_cover_pct}


def shortwave_options() -> bool:
    """Whether a revision R7-W option (vesper.env.sun.RECORDED_SPLIT, GROUND_REFLECTED) is on: only then is the
    recorded shortwave read."""
    return bool(sunmod.RECORDED_SPLIT or sunmod.GROUND_REFLECTED)


class Outdoor:
    """A site's outdoors over a run. `weather` is the cached record (vesper.env.weather.load), or None."""

    def __init__(self, site: Site, weather: wx.Weather | None = None):
        self.site, self.weather = site, weather

    @classmethod
    def for_run(cls, site: Site, cache_dir: Path, days: float | None = None) -> Outdoor:
        """The site with whatever weather the cache holds for the run's first `days` days (never fetched here)."""
        first, last = site.utc(0), site.utc((RUN_DAYS if days is None else days) * 86400)
        if shortwave_options():  # revision R7-W: the recorded shortwave too, only when an option reads it
            return cls(site, wx.load(site.lat, site.lon, wx.utc_days(first, last), cache_dir, shortwave=True))
        rec = wx.load(site.lat, site.lon, wx.utc_days(first, last), cache_dir)
        return cls(site, rec)

    def at(self, seconds: float) -> OutdoorState:
        when = self.site.utc(seconds)
        s = sunmod.sun_at(self.site.lat, self.site.lon, self.site.elevation_m, when)
        rec = self.weather.at(when) if self.weather is not None and self.weather.covers(when) else {}
        sources = {"sun": "computed (astral 2.2, as Home Assistant's sun.sun)"}
        ghi = rec.get("shortwave_radiation")
        if ghi is None:
            ghi = sunmod.clear_sky_ghi(s.elevation)
            sources["irradiance"] = "assumed (clear sky, Haurwitz)"
        else:
            sources["irradiance"] = self.weather.source
        if not s.up:
            ghi = 0.0
        temp, rh, cloud = rec.get("temperature_2m"), rec.get("relative_humidity_2m"), rec.get("cloud_cover")
        if temp is None:
            h = self.site.local(seconds)
            hour = h.hour + h.minute / 60
            temp = FALLBACK_MEAN_C + FALLBACK_SWING_C * math.sin(2 * math.pi * (hour - 9) / 24)
            sources["temperature"] = "assumed (15 °C ± 5 daily curve)"
        else:
            sources["temperature"] = self.weather.source
        if rh is None:
            rh, sources["humidity"] = FALLBACK_RH, "assumed (60 %)"
        else:
            sources["humidity"] = self.weather.source
        sources["cloud_cover"] = self.weather.source if cloud is not None else "unknown (taken as clear)"
        sources["illuminance"] = f"irradiance x {sunmod.LUMINOUS_EFFICACY:g} lm/W (assumed efficacy)"
        if not shortwave_options():
            return OutdoorState(sun=s, ghi_w_m2=ghi, illuminance_lux=sunmod.lux(ghi), temperature_c=temp,
                                humidity_pct=rh, cloud_cover_pct=cloud, direct_fraction=sunmod.direct_fraction(cloud),
                                sources=sources)
        # revision R7-W (protocol Addendum 14): the recorded split and the snow depth, where the options read them
        share = None
        if sunmod.RECORDED_SPLIT:
            share = sunmod.recorded_direct_fraction(rec.get("direct_radiation"), rec.get("diffuse_radiation"))
            sources["split"] = (f"recorded ({self.weather.source})" if share is not None else
                                f"assumed ({sunmod.DIRECT_SHARE:g} x (1 - cloud); no recorded split this hour)")
        return OutdoorState(sun=s, ghi_w_m2=ghi, illuminance_lux=sunmod.lux(ghi), temperature_c=temp,
                            humidity_pct=rh, cloud_cover_pct=cloud,
                            direct_fraction=sunmod.direct_fraction(cloud) if share is None else share,
                            sources=sources, snow_depth_m=rec.get("snow_depth"))

    def provenance(self) -> dict:
        """For a trace header: the site and where each kind of value comes from in this run."""
        return {"site": self.site.as_dict(),
                "weather": None if self.weather is None else {"source": self.weather.source,
                                                              "attribution": wx.ATTRIBUTION,
                                                              "from": self.weather.times[0].isoformat(),
                                                              "to": self.weather.times[-1].isoformat()},
                "sun": "astral 2.2", "clear_sky": "Haurwitz (fallback)",
                "luminous_efficacy_lm_per_w": sunmod.LUMINOUS_EFFICACY}
