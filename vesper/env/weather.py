"""The recorded weather of a site's dates: hourly outdoor temperature, relative humidity, cloud cover and shortwave
(global horizontal) radiation from the Open-Meteo historical archive, pinned to the ERA5 reanalysis (`models=era5`,
0.25 degree grid; without it the archive answers with its default blend of ERA5, ERA5-Land and ECMWF IFS; data CC BY
4.0, Open-Meteo asks for attribution and free use is non-commercial; https://open-meteo.com/en/terms). A day within
the last ARCHIVE_LAG_DAYS (the archive is about five days behind) comes from Open-Meteo's forecast service instead,
which keeps the past days of its weather models (`models=best_match`, a different product: forecast models, not a
reanalysis); both are free and need no key. Each cached day names its product (`model`, and in its file name), and a
loaded record's `source` names every product it read.

A day cached before the pin (2026-11-04, revision R-A: no `models=`, no model in its file name) is the archive's
default blend: it is still read when nothing newer is cached, so an old home keeps its weather, but its source says
"default blend", never ERA5; a fetch writes the ERA5 file beside it (old files are never deleted) and that wins.

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
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"  # recent days (the archive is about 5 days behind)
ARCHIVE_LAG_DAYS = 6  # a day this recent comes from the forecast service's past days, not the archive
VARIABLES = ("temperature_2m", "relative_humidity_2m", "cloud_cover", "shortwave_radiation")
FORMAT = "vesper-weather/1"
ATTRIBUTION = "Weather data by Open-Meteo.com, CC BY 4.0 (ERA5: Copernicus Climate Change Service)"
ARCHIVE_MODEL = "era5"  # the archive's product, pinned (revision R-A)
FORECAST_MODEL = "best_match"  # the forecast service's default product, named explicitly
PRODUCTS = {  # a cached day's model -> its product, as a record's source names it
    ARCHIVE_MODEL: "Open-Meteo archive, ERA5 (models=era5)",
    FORECAST_MODEL: "Open-Meteo forecast, best_match (models' past days)",
}
LEGACY_ARCHIVE = "Open-Meteo archive, default blend (no models=: ERA5, ERA5-Land, ECMWF IFS; cached before the pin)"


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


def cache_path(cache_dir: Path, lat: float, lon: float, day: dt.date, model: str = ARCHIVE_MODEL) -> Path:
    """A day's cache file for one product (`model`): the product is part of the name."""
    return Path(cache_dir) / f"{lat:.4f}_{lon:.4f}_{day.isoformat()}_{model}.json"


def legacy_cache_path(cache_dir: Path, lat: float, lon: float, day: dt.date) -> Path:
    """A day cached before the pin: no product in its name (the archive's default blend, or the forecast's)."""
    return Path(cache_dir) / f"{lat:.4f}_{lon:.4f}_{day.isoformat()}.json"


def cached(cache_dir: Path, lat: float, lon: float, day: dt.date) -> Path | None:
    """The day's product-named cache file (ERA5 first, then the forecast's), or None: a day cached only before the
    pin counts as not cached, so a fetch replaces it."""
    for model in (ARCHIVE_MODEL, FORECAST_MODEL):
        if (p := cache_path(cache_dir, lat, lon, day, model)).is_file():
            return p
    return None


def fetch(lat: float, lon: float, start: dt.date, end: dt.date, cache_dir: Path, *, get=None,
          today: dt.date | None = None) -> list[Path]:
    """Download the UTC days start..end (inclusive) for a site into the cache, one file per day; the paths. Days
    within ARCHIVE_LAG_DAYS of `today` (default: today, UTC) come from the forecast service. `get` is httpx.get,
    injectable."""
    if end < start:
        raise WeatherError(f"the last day {end} is before the first {start}")
    if get is None:
        import httpx
        get = httpx.get
    today = today or dt.datetime.now(dt.UTC).date()
    url = FORECAST_URL if end > today - dt.timedelta(days=ARCHIVE_LAG_DAYS) else ARCHIVE_URL
    model = ARCHIVE_MODEL if url == ARCHIVE_URL else FORECAST_MODEL
    params = {"latitude": lat, "longitude": lon, "hourly": ",".join(VARIABLES), "timezone": "GMT", "models": model}
    if url == ARCHIVE_URL:
        params |= {"start_date": start.isoformat(), "end_date": end.isoformat()}
    else:  # the forecast service counts days back from today and ahead of it
        params |= {"past_days": max(1, (today - start).days + 1), "forecast_days": max(1, (end - today).days + 1)}
    try:
        r = get(url, params=params, timeout=60)
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
        if not start <= dt.date.fromisoformat(day) <= end:
            continue  # the forecast service answers whole ranges around today
        p = cache_path(cache_dir, lat, lon, dt.date.fromisoformat(day), model)
        p.write_text(json.dumps({"format": FORMAT, "attribution": ATTRIBUTION, "url": url, "model": model,
                                 "product": PRODUCTS[model],
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
    times, values, cell, products = [], {v: [] for v in VARIABLES}, None, []
    for day in sorted(set(days)):
        p = cached(cache_dir, lat, lon, day) or legacy_cache_path(cache_dir, lat, lon, day)
        data = json.loads(p.read_text()) if p.is_file() else {}
        if data.get("format") != FORMAT:
            break
        cell = data.get("cell") or {}
        if (product := _product(data)) not in products:
            products.append(product)
        h = data["hourly"]
        times += [dt.datetime.fromisoformat(t).replace(tzinfo=dt.UTC) for t in h["time"]]
        for v in VARIABLES:
            values[v] += h[v]
    if not times:
        return None
    return Weather(lat=cell.get("lat") or lat, lon=cell.get("lon") or lon, times=times, values=values,
                   source=" + ".join(products) + f", cell {cell.get('lat')}, {cell.get('lon')}")


def _product(data: dict) -> str:
    """A cached day's product: its model's, or for a day cached before the pin, by the service it came from."""
    if data.get("model") in PRODUCTS:
        return PRODUCTS[data["model"]]
    return PRODUCTS[FORECAST_MODEL] if data.get("url") == FORECAST_URL else LEGACY_ARCHIVE


def utc_days(first: dt.datetime, last: dt.datetime) -> list[dt.date]:
    """The UTC days a local time span touches."""
    a, b = first.astimezone(dt.UTC).date(), last.astimezone(dt.UTC).date()
    return [a + dt.timedelta(days=k) for k in range((b - a).days + 1)]
