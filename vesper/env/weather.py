"""The recorded weather of a site's dates: hourly outdoor temperature, relative humidity, cloud cover and shortwave
(global horizontal) radiation from the Open-Meteo historical archive (ERA5 reanalysis; data CC BY 4.0, Open-Meteo
asks for attribution and free use is non-commercial; https://open-meteo.com/en/terms).

`vesper env fetch` downloads days into `.vesper/weather/` once; runs only read that cache, so a logical run is
reproducible and needs no network. A day with no recorded weather leaves the outdoor values to the clear-sky
fallback (`vesper.env.outdoor`), marked assumed.

Open-Meteo's hourly radiation is the mean over the hour before its time stamp, so it is assigned to the hour's
middle; instantaneous values (temperature, humidity, cloud cover) to their time stamp.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
VARIABLES = ("temperature_2m", "relative_humidity_2m", "cloud_cover", "shortwave_radiation")
FORMAT = "vesper-weather/1"
ATTRIBUTION = "Weather data by Open-Meteo.com (ERA5 reanalysis), CC BY 4.0"


class WeatherError(RuntimeError):
    """The archive could not give a site's weather (network, an unknown date, a malformed answer)."""


@dataclass
class Weather:
    """Hourly series for one site, by UTC time."""
    lat: float  # the archive's grid cell, which may differ from the site by a few km
    lon: float
    times: list[dt.datetime]  # UTC, hourly, ascending
    values: dict[str, list[float | None]]
    source: str

    def at(self, when: dt.datetime) -> dict[str, float | None]:
        """Each variable at a time, linearly interpolated (radiation on the hour's middle); None outside the series
        or across a gap."""
        when = when.astimezone(dt.UTC)
        out = {}
        for name, series in self.values.items():
            shift = dt.timedelta(minutes=-30) if name == "shortwave_radiation" else dt.timedelta(0)
            out[name] = _interp([t + shift for t in self.times], series, when)
        return out

    def covers(self, when: dt.datetime) -> bool:
        when = when.astimezone(dt.UTC)
        return bool(self.times) and self.times[0] <= when <= self.times[-1]


def _interp(times: list[dt.datetime], series: list[float | None], when: dt.datetime) -> float | None:
    i = bisect.bisect_right(times, when)
    if i == 0 or i > len(times) or (i == len(times) and when > times[-1]):
        return None
    if i == len(times):
        return series[-1]
    a, b = series[i - 1], series[i]
    if a is None or b is None:
        return None
    span = (times[i] - times[i - 1]).total_seconds()
    f = (when - times[i - 1]).total_seconds() / span if span else 0.0
    return round(a + (b - a) * f, 3)


def cache_path(cache_dir: Path, lat: float, lon: float, day: dt.date) -> Path:
    return Path(cache_dir) / f"{lat:.4f}_{lon:.4f}_{day.isoformat()}.json"


def fetch(lat: float, lon: float, start: dt.date, end: dt.date, cache_dir: Path, *, get=None) -> list[Path]:
    """Download the UTC days start..end (inclusive) for a site into the cache, one file per day; the paths. `get` is
    httpx.get, injectable."""
    if end < start:
        raise WeatherError(f"the last day {end} is before the first {start}")
    if get is None:
        import httpx
        get = httpx.get
    params = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
              "hourly": ",".join(VARIABLES), "timezone": "GMT"}
    try:
        r = get(ARCHIVE_URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001 - the network, the server, a non-JSON answer: one message
        raise WeatherError(f"the Open-Meteo archive gave no weather for {lat}, {lon} {start}..{end}: {e}") from e
    hourly = data.get("hourly") or {}
    times = hourly.get("time")
    if not isinstance(times, list) or any(not isinstance(hourly.get(v), list) for v in VARIABLES):
        raise WeatherError(f"the archive's answer has no hourly {', '.join(VARIABLES)}")
    by_day: dict[str, dict] = {}
    for k, t in enumerate(times):
        day = by_day.setdefault(t[:10], {"time": [], **{v: [] for v in VARIABLES}})
        day["time"].append(t)
        for v in VARIABLES:
            day[v].append(hourly[v][k])
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    paths = []
    for day, series in sorted(by_day.items()):
        p = cache_path(cache_dir, lat, lon, dt.date.fromisoformat(day))
        p.write_text(json.dumps({"format": FORMAT, "attribution": ATTRIBUTION, "url": ARCHIVE_URL,
                                 "site": {"lat": lat, "lon": lon},
                                 "cell": {"lat": data.get("latitude"), "lon": data.get("longitude"),
                                          "elevation": data.get("elevation")},
                                 "units": data.get("hourly_units", {}), "hourly": series}, indent=1))
        paths.append(p)
    return paths


def load(lat: float, lon: float, days: list[dt.date], cache_dir: Path) -> Weather | None:
    """The cached weather of these UTC days, from the first up to the first one missing (a run longer than its
    cache keeps the recorded days; past them the outdoors is assumed), or None when the first is missing. Never
    fetches."""
    times, values, cell = [], {v: [] for v in VARIABLES}, None
    for day in sorted(set(days)):
        p = cache_path(cache_dir, lat, lon, day)
        data = json.loads(p.read_text()) if p.is_file() else {}
        if data.get("format") != FORMAT:
            break
        cell = data.get("cell") or {}
        h = data["hourly"]
        times += [dt.datetime.fromisoformat(t).replace(tzinfo=dt.UTC) for t in h["time"]]
        for v in VARIABLES:
            values[v] += h[v]
    if not times:
        return None
    return Weather(lat=cell.get("lat") or lat, lon=cell.get("lon") or lon, times=times, values=values,
                   source=f"Open-Meteo archive (ERA5), cell {cell.get('lat')}, {cell.get('lon')}")


def utc_days(first: dt.datetime, last: dt.datetime) -> list[dt.date]:
    """The UTC days a local time span touches."""
    a, b = first.astimezone(dt.UTC).date(), last.astimezone(dt.UTC).date()
    return [a + dt.timedelta(days=k) for k in range((b - a).days + 1)]
