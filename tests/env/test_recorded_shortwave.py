"""Revision R7-W (protocol Addendum 14): recorded shortwave. ERA5's direct and diffuse radiation and snow depth, fetched
into a sidecar beside a cached day, read only when an option asks for them; the recorded beam share in place of the
assumed 0.8 (1 - cloud), and a ground-reflected term on vertical glass. Both options off by default, and the model
unchanged to the bit when off."""
import datetime as dt
import json

import pytest

from vesper.env import indoor
from vesper.env import sun as sunmod
from vesper.env.outdoor import Outdoor, OutdoorState
from vesper.env.site import parse_site
from vesper.env.sun import Sun
from vesper.env.weather import (
    SHORTWAVE_VARIABLES,
    VARIABLES,
    WeatherError,
    fetch,
    fetch_shortwave,
    load,
    shortwave_path,
)

SITE = {"lat": 35.2271, "lon": -80.8431, "elevation_m": 229, "tz": "America/New_York", "date": "2026-01-15"}
DAYS = (dt.date(2026, 1, 15), dt.date(2026, 1, 16))


class Answer:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.data


def _times(days):
    return [f"{d}T{h:02d}:00" for d in days for h in range(24)]


def archive(days=("2026-01-15", "2026-01-16")):
    times = _times(days)
    n = len(times)
    return {"latitude": 35.25, "longitude": -80.85, "elevation": 230.0, "hourly_units": {},
            "hourly": {"time": times, "temperature_2m": [float(k % 24) for k in range(n)],
                       "relative_humidity_2m": [50.0] * n, "cloud_cover": [100.0] * n,
                       "shortwave_radiation": [0.0 if k % 24 < 12 or k % 24 > 22 else 400.0 for k in range(n)]}}


def shortwave(days=("2026-01-15", "2026-01-16"), snow=0.0):
    times = _times(days)
    n = len(times)
    day = [k % 24 >= 12 and k % 24 <= 22 for k in range(n)]
    return {"latitude": 35.25, "longitude": -80.85, "elevation": 230.0,
            "hourly_units": {"direct_radiation": "W/m²", "snow_depth": "m"},
            "hourly": {"time": times, "direct_radiation": [300.0 if d else 0.0 for d in day],
                       "diffuse_radiation": [100.0 if d else 0.0 for d in day], "snow_depth": [snow] * n}}


def _cache(tmp_path, snow=0.0):
    fetch(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(archive()))
    fetch_shortwave(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(shortwave(snow=snow)))


# -- the fetch -------------------------------------------------------------------------------------------------------

def test_fetch_shortwave_asks_the_era5_archive_for_three_variables_and_writes_a_sidecar_per_day(tmp_path):
    asked = []

    def get(url, params=None, timeout=None, **kw):
        asked.append((url, dict(params)))
        return Answer(shortwave())

    paths = fetch_shortwave(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=get)
    assert [p.name for p in paths] == ["35.2271_-80.8431_2026-01-15_era5-shortwave.json",
                                       "35.2271_-80.8431_2026-01-16_era5-shortwave.json"]
    assert paths[0] == shortwave_path(tmp_path, SITE["lat"], SITE["lon"], DAYS[0])
    url, params = asked[0]
    assert "archive-api" in url and params["models"] == "era5" and params["timezone"] == "GMT"
    assert params["hourly"] == ",".join(SHORTWAVE_VARIABLES) == "direct_radiation,diffuse_radiation,snow_depth"
    data = json.loads(paths[0].read_text())
    assert data["model"] == "era5" and data["url"].startswith("https://archive-api") and data["params"] == params
    assert set(data["hourly"]) == {"time", *SHORTWAVE_VARIABLES} and len(data["hourly"]["time"]) == 24


def test_fetch_shortwave_never_asks_again_for_days_it_has(tmp_path):
    fetch_shortwave(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(shortwave()))

    def refuse(*a, **k):
        raise AssertionError("fetched a cached day")
    again = fetch_shortwave(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=refuse)
    assert len(again) == 2


def test_fetch_shortwave_fails_clearly(tmp_path):
    with pytest.raises(WeatherError, match="hourly"):
        fetch_shortwave(1, 2, dt.date(2026, 1, 1), dt.date(2026, 1, 1), tmp_path,
                        get=lambda *a, **k: Answer({"hourly": {"time": []}}))


def test_the_sidecar_does_not_overwrite_or_count_as_the_day_s_weather(tmp_path):
    fetch_shortwave(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(shortwave()))
    assert load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path) is None  # only the sidecar: no weather


# -- the load ----------------------------------------------------------------------------------------------------------

def test_load_without_the_option_is_what_it_was_even_with_sidecars_present(tmp_path):
    fetch(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(archive()))
    before = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path)
    fetch_shortwave(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(shortwave()))
    after = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path)
    assert set(after.values) == set(VARIABLES)
    assert (after.times, after.values, after.source) == (before.times, before.values, before.source)


def test_load_with_the_option_adds_the_three_series_and_names_them_in_the_source(tmp_path):
    _cache(tmp_path, snow=0.05)
    plain = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path)
    w = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path, shortwave=True)
    assert set(w.values) == set(VARIABLES) | set(SHORTWAVE_VARIABLES)
    assert all(w.values[v] == plain.values[v] for v in VARIABLES)
    assert w.source.startswith(plain.source) and "direct_radiation" in w.source and "2 of 2" in w.source
    # radiation is the mean of the hour before its stamp: it sits on the hour's middle, as shortwave_radiation does
    v = w.at(dt.datetime(2026, 1, 15, 11, 30, tzinfo=dt.UTC))
    assert (v["direct_radiation"], v["diffuse_radiation"], v["shortwave_radiation"]) == (300.0, 100.0, 400.0)
    assert v["snow_depth"] == 0.05


def test_a_day_without_its_sidecar_has_no_recorded_split_and_the_source_says_how_many(tmp_path):
    fetch(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(archive()))
    fetch_shortwave(SITE["lat"], SITE["lon"], DAYS[0], DAYS[0], tmp_path,
                    get=lambda *a, **k: Answer(shortwave(days=("2026-01-15",))))
    w = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path, shortwave=True)
    assert "1 of 2" in w.source
    assert w.values["direct_radiation"][24:] == [None] * 24 and w.values["direct_radiation"][13] == 300.0


def snow(days=("2026-01-15", "2026-01-16"), depth=0.3):
    times = _times(days)
    return {"latitude": 35.2, "longitude": -80.8, "elevation": 230.0, "hourly_units": {"snow_depth": "m"},
            "hourly": {"time": times, "snow_depth": [depth] * len(times)}}


def test_fetch_snow_asks_era5_land_for_the_snow_depth_era5_does_not_serve(tmp_path):
    """Open-Meteo answers snow_depth with nulls under models=era5 (live 2026-11-05); ERA5-Land serves it."""
    from vesper.env.weather import SNOW_MODEL, fetch_snow, snow_path
    asked = []

    def get(url, params=None, timeout=None, **kw):
        asked.append(dict(params))
        return Answer(snow())

    paths = fetch_snow(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=get)
    assert SNOW_MODEL == "era5_land" and asked[0]["models"] == "era5_land" and asked[0]["hourly"] == "snow_depth"
    assert paths[1] == snow_path(tmp_path, SITE["lat"], SITE["lon"], DAYS[1])
    assert paths[0].name == "35.2271_-80.8431_2026-01-15_era5_land-snow.json"
    assert fetch_snow(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=None) == paths  # cached: no request


def test_the_snow_sidecar_gives_the_snow_depth_and_is_named_in_the_source(tmp_path):
    from vesper.env.weather import fetch_snow
    _cache(tmp_path)  # the ERA5 sidecar's snow depth is 0.0 here
    fetch_snow(SITE["lat"], SITE["lon"], DAYS[0], DAYS[0], tmp_path,
               get=lambda *a, **k: Answer(snow(days=("2026-01-15",))))
    w = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path, shortwave=True)
    assert w.values["snow_depth"][:24] == [0.3] * 24 and w.values["snow_depth"][24:] == [0.0] * 24
    assert "ERA5-Land" in w.source and "snow_depth (1 of 2 days)" in w.source
    plain = load(SITE["lat"], SITE["lon"], list(DAYS), tmp_path)
    assert "snow_depth" not in plain.values and "ERA5-Land" not in plain.source


# -- the outdoors ------------------------------------------------------------------------------------------------------

def test_the_options_are_off_by_default_and_live_beside_the_direct_share():
    assert (sunmod.RECORDED_SPLIT, sunmod.GROUND_REFLECTED) == (0, 0)
    assert indoor.knob_value("RECORDED_SPLIT") == 0 and indoor.knob_value("GROUND_REFLECTED") == 0
    assert {"RECORDED_SPLIT", "GROUND_REFLECTED"} <= set(indoor.KNOBS)


def test_off_the_outdoors_reads_no_sidecar_and_keeps_the_assumed_split(tmp_path):
    _cache(tmp_path)
    site = parse_site(SITE)
    out = Outdoor.for_run(site, tmp_path, days=1)
    assert set(out.weather.values) == set(VARIABLES)
    st = out.at(12 * 3600)  # 17:00 UTC; cloud 100 %: the assumed split gives no beam
    assert st.direct_fraction == 0 and st.snow_depth_m is None and "split" not in st.sources


def test_the_recorded_split_replaces_the_assumed_one(tmp_path):
    _cache(tmp_path)
    site = parse_site(SITE)
    with indoor.knobs(RECORDED_SPLIT=1):
        out = Outdoor.for_run(site, tmp_path, days=1)
        st = out.at(12 * 3600)
    assert st.direct_fraction == pytest.approx(0.75)  # 300 / (300 + 100), though the cloud cover is 100 %
    assert st.ghi_w_m2 == 400.0 and "recorded" in st.sources["split"]
    assert "direct_radiation" in out.weather.source


def test_without_recorded_radiation_the_split_falls_back_to_the_assumed_one(tmp_path):
    fetch(SITE["lat"], SITE["lon"], *DAYS, tmp_path, get=lambda *a, **k: Answer(archive()))
    with indoor.knobs(RECORDED_SPLIT=1):
        st = Outdoor.for_run(parse_site(SITE), tmp_path, days=1).at(12 * 3600)
    assert st.direct_fraction == 0 and "assumed" in st.sources["split"]


def test_the_ground_option_reads_the_snow_depth(tmp_path):
    _cache(tmp_path, snow=0.2)
    with indoor.knobs(GROUND_REFLECTED=1):
        st = Outdoor.for_run(parse_site(SITE), tmp_path, days=1).at(12 * 3600)
    assert st.snow_depth_m == 0.2 and st.direct_fraction == 0  # the split is its own option


def test_ground_albedo_is_point_two_and_point_six_on_snow_deeper_than_a_centimetre():
    assert sunmod.ground_albedo(None) == 0.2 and sunmod.ground_albedo(0.0) == 0.2
    assert sunmod.ground_albedo(0.01) == 0.2 and sunmod.ground_albedo(0.011) == 0.6


# -- the room's solar gain ---------------------------------------------------------------------------------------------

def _room_env():
    from tests.lab.maps import house
    from vesper.env.indoor import HomeEnvironment, room_models
    from vesper.lab.windows import plan_windows
    site = parse_site(SITE)
    m = house()
    return HomeEnvironment(site, Outdoor(site), room_models(m, plan_windows(m)))


def _state(snow=None, direct=0.5):
    return OutdoorState(sun=Sun(30, 180), ghi_w_m2=400.0, illuminance_lux=44_000, temperature_c=0.0,
                        humidity_pct=60, cloud_cover_pct=50.0, direct_fraction=direct, snow_depth_m=snow)


@pytest.mark.parametrize("snow,albedo", [(None, 0.2), (0.0, 0.2), (0.3, 0.6)])
def test_the_ground_reflected_term_is_albedo_times_global_times_half_on_every_window(snow, albedo):
    e = _room_env()
    for rid, r in e.rooms.items():
        off = e._solar_w(r, _state(snow))
        with indoor.knobs(GROUND_REFLECTED=1):
            on = e._solar_w(r, _state(snow))
        glass = sum(w["width"] * w["height"] for w in r.windows)
        want = indoor.SOLAR_TO_AIR * indoor.G_SOLAR * glass * albedo * 400.0 * 0.5
        assert on - off == pytest.approx(want), rid


def test_the_ground_term_follows_the_shades_and_the_aperture():
    e = _room_env()
    r = e.rooms["bedroom"]
    for w in r.windows:
        e.set_shade(w["id"], False)
    off = e._solar_w(r, _state())
    with indoor.knobs(GROUND_REFLECTED=1, SOLAR_APERTURE_SCALE=0.5):
        on = e._solar_w(r, _state())
    with indoor.knobs(SOLAR_APERTURE_SCALE=0.5):
        half = e._solar_w(r, _state())
    glass = sum(w["width"] * w["height"] for w in r.windows)
    assert on - half == pytest.approx(0.5 * indoor.G_SOLAR * glass * 0.2 * 400.0 * 0.5 * indoor.SHADE_SOLAR)
    assert off > 0


def test_the_options_on_are_named_in_the_provenance_and_off_are_not():
    assert not {"recorded_split", "ground_reflected"} & set(_room_env().provenance()["indoor"])
    with indoor.knobs(RECORDED_SPLIT=1, GROUND_REFLECTED=1):
        prov = _room_env().provenance()["indoor"]
        assert prov["recorded_split"] == 1 and prov["ground_reflected"] == 1
        assert indoor.nondefault_knobs() == {"RECORDED_SPLIT": 1, "GROUND_REFLECTED": 1}
