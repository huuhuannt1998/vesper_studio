"""Recorded weather (the Open-Meteo archive, cached per UTC day) and the outdoor values a run reads, each with its
source; without a record, clear sky and an assumed curve, marked assumed."""
import datetime as dt
import json

import pytest

from vesper.env.outdoor import Outdoor
from vesper.env.site import parse_site
from vesper.env.weather import ATTRIBUTION, WeatherError, cache_path, fetch, load, utc_days

SITE = {"lat": 35.2271, "lon": -80.8431, "elevation_m": 229, "tz": "America/New_York", "date": "2026-01-15"}


class Answer:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.data


def archive(days=("2026-01-15", "2026-01-16")):
    times = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    n = len(times)
    return {"latitude": 35.25, "longitude": -80.85, "elevation": 230.0,
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": times, "temperature_2m": [float(k % 24) for k in range(n)],
                       "relative_humidity_2m": [50.0] * n, "cloud_cover": [100.0] * n,
                       "shortwave_radiation": [0.0 if k % 24 < 12 or k % 24 > 22 else 400.0 for k in range(n)]}}


def test_fetch_writes_one_cached_file_per_utc_day_with_its_attribution(tmp_path):
    asked = {}

    def get(url, params, timeout):
        asked.update(params, url=url)
        return Answer(archive())

    paths = fetch(35.2271, -80.8431, dt.date(2026, 1, 15), dt.date(2026, 1, 16), tmp_path, get=get)
    assert [p.name for p in paths] == ["35.2271_-80.8431_2026-01-15.json", "35.2271_-80.8431_2026-01-16.json"]
    data = json.loads(paths[0].read_text())
    assert data["attribution"] == ATTRIBUTION and data["cell"]["lat"] == 35.25 and len(data["hourly"]["time"]) == 24
    assert asked["timezone"] == "GMT" and "shortwave_radiation" in asked["hourly"] and "archive" in asked["url"]


def test_a_failed_or_malformed_answer_is_one_clear_error(tmp_path):
    with pytest.raises(WeatherError, match="no weather"):
        fetch(1, 2, dt.date(2026, 1, 1), dt.date(2026, 1, 1), tmp_path, get=lambda *a, **k: Answer({}, 500))
    with pytest.raises(WeatherError, match="hourly"):
        fetch(1, 2, dt.date(2026, 1, 1), dt.date(2026, 1, 1), tmp_path, get=lambda *a, **k: Answer({"hourly": {}}))


def test_loading_takes_the_cached_days_from_the_first_to_the_first_gap_and_never_fetches(tmp_path):
    fetch(35.2271, -80.8431, dt.date(2026, 1, 15), dt.date(2026, 1, 16), tmp_path,
          get=lambda *a, **k: Answer(archive()))
    assert load(35.2271, -80.8431, [dt.date(2026, 1, 14), dt.date(2026, 1, 15)], tmp_path) is None  # no first day
    longer = load(35.2271, -80.8431, [dt.date(2026, 1, d) for d in (15, 16, 17)], tmp_path)
    assert len(longer.times) == 48  # a run longer than its cache keeps the recorded days (live 2026-11-04)
    w = load(35.2271, -80.8431, [dt.date(2026, 1, 15), dt.date(2026, 1, 16)], tmp_path)
    assert len(w.times) == 48 and "35.25" in w.source


def test_values_interpolate_between_hours_and_radiation_sits_on_the_hour_s_middle(tmp_path):
    fetch(35.2271, -80.8431, dt.date(2026, 1, 15), dt.date(2026, 1, 16), tmp_path,
          get=lambda *a, **k: Answer(archive()))
    w = load(35.2271, -80.8431, [dt.date(2026, 1, 15), dt.date(2026, 1, 16)], tmp_path)
    v = w.at(dt.datetime(2026, 1, 15, 6, 30, tzinfo=dt.UTC))
    assert v["temperature_2m"] == 6.5
    # the 12:00 radiation (the mean of 11:00-12:00) belongs to 11:30
    assert w.at(dt.datetime(2026, 1, 15, 11, 30, tzinfo=dt.UTC))["shortwave_radiation"] == 400.0
    assert w.at(dt.datetime(2026, 1, 18, 0, 0, tzinfo=dt.UTC))["temperature_2m"] is None


def test_outdoor_values_come_from_the_record_when_there_is_one(tmp_path):
    site = parse_site(SITE)
    fetch(site.lat, site.lon, dt.date(2026, 1, 15), dt.date(2026, 1, 16), tmp_path,
          get=lambda *a, **k: Answer(archive()))
    out = Outdoor.for_run(site, tmp_path, days=1)
    st = out.at(12 * 3600)  # 12:00 EST = 17:00 UTC
    assert st.temperature_c == 17.0 and st.cloud_cover_pct == 100.0 and st.direct_fraction == 0
    assert st.ghi_w_m2 == 400.0 and st.illuminance_lux == 44_000
    assert "Open-Meteo" in st.sources["temperature"] and "astral" in st.sources["sun"]
    assert out.provenance()["weather"]["attribution"] == ATTRIBUTION


def test_without_a_record_the_outdoors_is_clear_sky_and_an_assumed_curve_and_says_so(tmp_path):
    out = Outdoor.for_run(parse_site(SITE), tmp_path)
    assert out.weather is None and out.provenance()["weather"] is None
    noon, night = out.at(12.5 * 3600), out.at(2 * 3600)
    assert noon.ghi_w_m2 > 300 and "clear sky" in noon.sources["irradiance"]
    assert night.ghi_w_m2 == 0 and night.illuminance_lux == 0
    assert "assumed" in noon.sources["temperature"] and noon.temperature_c > night.temperature_c


def test_a_run_s_utc_days_cover_its_local_span():
    site = parse_site(SITE)
    assert utc_days(site.utc(0), site.utc(86400)) == [dt.date(2026, 1, 15), dt.date(2026, 1, 16)]
    assert cache_path("x", 1.5, -2.25, dt.date(2026, 1, 1)).name == "1.5000_-2.2500_2026-01-01.json"



def test_recent_days_come_from_the_forecast_service_older_ones_from_the_archive(tmp_path):
    from vesper.env.weather import ARCHIVE_URL, FORECAST_URL
    urls = []

    def get(url, params=None, timeout=None):
        urls.append((url, params))
        return Answer(archive(("2026-11-01", "2026-11-02")))
    today = dt.date(2026, 11, 4)
    fetch(35.2271, -80.8431, dt.date(2026, 11, 1), dt.date(2026, 11, 2), tmp_path, get=get, today=today)
    fetch(35.2271, -80.8431, dt.date(2026, 1, 15), dt.date(2026, 1, 16), tmp_path, get=lambda u, params=None,
          timeout=None: urls.append((u, params)) or Answer(archive()), today=today)
    assert urls[0][0] == FORECAST_URL and urls[0][1]["past_days"] >= 3 and urls[1][0] == ARCHIVE_URL
    w = load(35.2271, -80.8431, [dt.date(2026, 11, 1)], tmp_path)
    assert "forecast" in w.source
