"""vesper.edt.trace: record every sensor report of a three-day run of the standard EDT home, per environment."""
import gzip
import json
from pathlib import Path

import pytest

from vesper.edt.home import build_edt_home
from vesper.edt.trace import ENVIRONMENTS, record_trace
from vesper.edt.types import Env
from vesper.validation import crosssite

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")

PHOENIX = "Phoenix, AZ, USA"


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    path, _roles = build_edt_home(tmp_path_factory.mktemp("edt"), "catalog/vds")
    return path


def read(path):
    with gzip.open(path, "rt") as f:
        lines = [json.loads(line) for line in f]
    return lines[0], lines[1:]


def test_grid():
    assert len(ENVIRONMENTS) == 48
    assert {e.city for e in ENVIRONMENTS} == set(crosssite.CITIES)
    assert {e.date for e in ENVIRONMENTS} == {"2026-01-14", "2026-04-15", "2026-07-15", "2026-10-14"}  # protocol A3.1
    assert {e.north_deg for e in ENVIRONMENTS} == {0.0, 180.0}
    assert {e.rung for e in ENVIRONMENTS} == {4}
    assert len({e.key for e in ENVIRONMENTS}) == 48


def test_trace_has_header_and_sensor_reports(home, tmp_path):
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    out = record_trace(home, env, tmp_path, hours=14, fetch=False)
    assert out == tmp_path / f"trace-{env.key}.jsonl.gz"
    header, reports = read(out)
    assert header["env"]["key"] == env.key
    assert header["site"]["north_deg"] == 0.0 and header["site"]["date"] == "2026-07-15"
    assert header["start_time"].startswith("2026-07-15T00:00:00")
    assert all(set(r) == {"t", "twin_id", "feature", "value", "observed_t"} for r in reports)
    lux = [r for r in reports if r["feature"] == "illuminance"]
    assert lux and any(r["value"] > 0 for r in lux)
    assert {"temperature", "humidity"} <= {r["feature"] for r in reports}
    assert max(r["t"] for r in reports) <= 14 * 3600


def test_recording_twice_reuses_the_file(home, tmp_path, monkeypatch):
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    first = record_trace(home, env, tmp_path, hours=2, fetch=False)
    stamp = first.stat().st_mtime_ns
    from vesper.edt import trace
    monkeypatch.setattr(trace, "_run", lambda *a, **k: pytest.fail("ran again"))
    assert record_trace(home, env, tmp_path, hours=2, fetch=False) == first
    assert first.stat().st_mtime_ns == stamp
    assert not list(tmp_path.glob("*.tmp*"))


def held_mean(reports, seconds, where=lambda twin_id: "@porch_" not in twin_id):
    """Mean illuminance over the run, each sensor holding its last report (a sensor reports on change, so the report
    count differs between rungs and the plain mean of reports would not compare them). `where`: which sensors (by
    twin id; default the indoor ones, not the porch's)."""
    by = {}
    for r in reports:
        if r["feature"] == "illuminance" and where(r["twin_id"]):
            by.setdefault(r["twin_id"], []).append((r["t"], r["value"]))
    total = 0.0
    for series in by.values():
        for (t, v), (t2, _) in zip(series, [*series[1:], (seconds, 0)], strict=True):
            total += v * (t2 - t)
    return total / seconds


def test_rung3_daylight_is_one_fourteenth_of_rung4(home, tmp_path):
    def daylight(rung):
        _, reports = read(record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, rung), tmp_path, hours=14,
                                       fetch=False))
        return held_mean(reports, 14 * 3600)

    from vesper.env import indoor
    r4, r3 = daylight(4), daylight(3)
    print(f"rung3/rung4 daylight lux ratio = {r3 / r4:.5f} (1/14.8 = {1 / 14.8:.5f})")
    assert r4 > 100
    assert r3 / r4 == pytest.approx(1 / 14.8, rel=0.05)
    assert indoor.DAYLIGHT_SCALE == 1.0  # restored


def test_the_porch_sensors_read_the_outdoor_light_and_air(home, tmp_path):
    """The porch is outdoors: its illuminance is the outdoor daylight, far above any room's, and the rung-3 scale of
    indoor daylight leaves it alone; it reports temperature and humidity too."""
    def porch(twin_id):
        return "@porch_" in twin_id

    traces = {rung: read(record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, rung), tmp_path, hours=14,
                                      fetch=False))[1] for rung in (3, 4)}
    outdoor, indoor = held_mean(traces[4], 14 * 3600, porch), held_mean(traces[4], 14 * 3600)
    assert outdoor > 10 * indoor
    assert held_mean(traces[3], 14 * 3600, porch) == pytest.approx(outdoor, rel=0.05)
    assert {"temperature", "humidity"} <= {r["feature"] for r in traces[4] if porch(r["twin_id"])}


def test_scale_restored_and_no_file_when_the_run_fails(home, tmp_path, monkeypatch):
    from vesper.edt import trace
    from vesper.env import indoor

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(trace, "_run", boom)
    with pytest.raises(RuntimeError):
        record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, 3), tmp_path, hours=2, fetch=False)
    assert indoor.DAYLIGHT_SCALE == 1.0
    assert not list(tmp_path.glob("trace-*"))


def test_rung2_keeps_site_in_header_but_runs_without_one(home, tmp_path):
    env2, env1 = Env(PHOENIX, "2026-07-15", 0.0, 2), Env(PHOENIX, "2026-07-15", 0.0, 1)
    h2, r2 = read(record_trace(home, env2, tmp_path, hours=2, fetch=False))
    h1, r1 = read(record_trace(home, env1, tmp_path, hours=2, fetch=False))
    assert h2["site"]["lat"] == pytest.approx(33.4484) and h1["site"] is None
    assert h2["env"]["key"] != h1["env"]["key"]
    assert r2 and r1


def _day(t):
    return {"time": [f"{t}T{h:02d}:00" for h in range(24)], "temperature_2m": [30.0] * 24,
            "relative_humidity_2m": [20.0] * 24, "cloud_cover": [0.0] * 24, "shortwave_radiation": [500.0] * 24}


def test_weather_is_fetched_once_per_city_and_date(home, tmp_path):
    calls = []

    class Resp:
        def __init__(self, params):
            self.params = params

        def raise_for_status(self):
            pass

        def json(self):
            import datetime as dt
            a, b = (dt.date.fromisoformat(self.params[k]) for k in ("start_date", "end_date"))
            days = [a + dt.timedelta(days=k) for k in range((b - a).days + 1)]
            hourly = {k: [] for k in _day("x")}
            for d in days:
                for k, v in _day(d.isoformat()).items():
                    hourly[k] += v
            return {"hourly": hourly, "latitude": 33.4, "longitude": -112.0, "elevation": 331}

    def get(url, params, timeout):
        calls.append(params)
        return Resp(params)

    for north in (0.0, 180.0):
        record_trace(home, Env(PHOENIX, "2026-07-15", north, 4), tmp_path, hours=2, get=get)
    assert len(calls) == 1
    assert list((tmp_path / "weather").glob("*.json"))


def _fake_get(calls=None):
    class Resp:
        def __init__(self, params):
            self.params = params

        def raise_for_status(self):
            pass

        def json(self):
            import datetime as dt
            a, b = (dt.date.fromisoformat(self.params[k]) for k in ("start_date", "end_date"))
            hourly = {k: [] for k in _day("x")}
            for k in range((b - a).days + 1):
                for name, v in _day((a + dt.timedelta(days=k)).isoformat()).items():
                    hourly[name] += v
            return {"hourly": hourly, "latitude": 33.4, "longitude": -112.0, "elevation": 331}

    def get(url, params, timeout):
        if calls is not None:
            calls.append(params)
        return Resp(params)
    return get


def test_same_env_twice_is_byte_identical(home, tmp_path):
    env = Env(PHOENIX, "2026-07-15", 180.0, 4)
    a = record_trace(home, env, tmp_path / "a", hours=3, fetch=False)
    b = record_trace(home, env, tmp_path / "b", hours=3, fetch=False)
    assert a.read_bytes() == b.read_bytes()
    data = a.read_bytes()
    assert data[4:8] == b"\0\0\0\0" and data[3] == 0  # no mtime, no file name in the gzip header


def test_header_has_initial_state_as_the_engine_first_sees_it(home, tmp_path):
    from vesper.env import indoor
    header, reports = read(record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, 4), tmp_path, hours=2, fetch=False))
    init = header["initial_state"]
    assert {r["twin_id"] for r in reports} <= set(init)
    tid = "zigbee:sonoff:snzb-02@living_temp0"
    assert init[tid]["temperature"] == pytest.approx(indoor.START_C, abs=0.6) and init[tid]["temperature"] != 0
    assert init[tid]["humidity"] > 0
    first = next(r for r in reports if r["twin_id"] == tid and r["feature"] == "temperature")
    assert abs(first["value"] - init[tid]["temperature"]) < 1.0  # the first report continues from the initial state


def test_header_provenance(home, tmp_path):
    import hashlib
    header, _ = read(record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, 4), tmp_path, hours=2, fetch=False))
    assert header["seconds"] == 7200 and header["weather"] is None
    assert header["home_sha256"] == hashlib.sha256(home.read_bytes()).hexdigest()


def test_a_short_trace_is_not_reused_for_a_full_request(home, tmp_path):
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    record_trace(home, env, tmp_path, hours=2, fetch=False)
    header, _ = read(record_trace(home, env, tmp_path, fetch=False))
    assert header["seconds"] == 3 * 86400
    assert not list(tmp_path.glob(".*tmp*"))


def test_a_different_home_is_rerecorded(home, tmp_path):
    import shutil
    other = tmp_path / "other"
    shutil.copytree(home.parent, other)
    with (other / "home.yaml").open("a") as f:
        f.write("\n# edited\n")
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    cache = tmp_path / "cache"
    h1, _ = read(record_trace(home, env, cache, hours=2, fetch=False))
    h2, _ = read(record_trace(other / "home.yaml", env, cache, hours=2, fetch=False))
    assert h1["home_sha256"] != h2["home_sha256"]


def test_weather_used_when_cached_and_trace_rerecorded_when_it_arrives(home, tmp_path):
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    h1, _ = read(record_trace(home, env, tmp_path, hours=2, fetch=False))
    assert h1["weather"] is None
    h2, _ = read(record_trace(home, env, tmp_path, hours=2, get=_fake_get()))  # now fetched into the cache
    assert h2["weather"] is not None and "Open-Meteo" in h2["weather"]
    h3, _ = read(record_trace(home, env, tmp_path, hours=2, fetch=False))  # reused: same weather
    assert h3 == h2


def test_rung1_start_time_does_not_leak_the_timezone(home, tmp_path):
    header, _ = read(record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, 1), tmp_path, hours=2, fetch=False))
    assert header["start_time"] == "2026-07-15T00:00:00" and header["site"] is None


def test_header_site_round_trips_through_the_site_parser(home, tmp_path):
    from vesper.env.site import parse_site
    header, _ = read(record_trace(home, Env(PHOENIX, "2026-07-15", 180.0, 4), tmp_path, hours=2, fetch=False))
    site = parse_site(header["site"])
    assert site.as_dict() == header["site"]
    assert site.north_deg == 180.0 and site.tz == "America/Phoenix"


def test_every_environment_s_measured_days_share_their_weekdays():
    import datetime as dt
    days = {dt.date.fromisoformat(e.date).weekday() for e in ENVIRONMENTS}
    assert days == {2}  # protocol A3.1: every start date a Wednesday, so measured days 1-2 are Thursday and Friday
    assert sorted({e.date for e in ENVIRONMENTS}) == ["2026-01-14", "2026-04-15", "2026-07-15", "2026-10-14"]


def test_the_porch_temperature_and_humidity_are_the_outdoor_air_within_rounding(home, tmp_path):
    """Each porch report equals the outdoor model's air at the ambient tick it was taken on (rules round the
    temperature to 0.1 C and the humidity to 1 %; the sensor reports a tick's value at most one tick late)."""
    from vesper.edt.trace import env_site
    from vesper.env.outdoor import Outdoor
    from vesper.lab.rules import AMBIENT_EVERY_S
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    _, reports = read(record_trace(home, env, tmp_path, hours=14, fetch=False))
    outdoor = Outdoor.for_run(env_site(env), tmp_path / "weather")
    tol = {"temperature": 0.051, "humidity": 0.51}
    seen = {k: 0 for k in tol}
    for r in reports:
        if "@porch_" not in r["twin_id"] or r["feature"] not in tol:
            continue
        k = int(r["t"] // AMBIENT_EVERY_S)
        air = [outdoor.at(j * AMBIENT_EVERY_S) for j in (k - 1, k) if j >= 0]
        values = [a.temperature_c if r["feature"] == "temperature" else a.humidity_pct for a in air]
        assert min(abs(r["value"] - v) for v in values) <= tol[r["feature"]], (r, values)
        seen[r["feature"]] += 1
    assert all(n > 0 for n in seen.values()), seen


def test_rung_2_5_porch_reads_the_outdoor_air_and_rooms_the_assumed_curve(home, tmp_path):
    """Protocol A6.5: at rung 2.5 the porch's sensors are rung 4's (the site's outdoor air and light) and every indoor
    room's are rung 2's (the assumed curve); the header names the site; the knob is restored after."""
    from vesper.lab import rules

    def porch(twin_id):
        return "@porch_" in twin_id

    def indoor(twin_id):
        return not porch(twin_id)

    traces = {rung: read(record_trace(home, Env(PHOENIX, "2026-07-15", 0.0, rung), tmp_path, hours=14,
                                      fetch=False)) for rung in (2, 2.5, 4)}
    h25, r25 = traces[2.5]
    assert h25["site"]["lat"] == pytest.approx(33.4484) and h25["env"]["key"].endswith("-r2.5")

    def series(reports, where, feature):
        return sorted((r["t"], r["twin_id"], r["value"]) for r in reports if r["feature"] == feature
                      and where(r["twin_id"]))
    for feature in ("temperature", "humidity", "illuminance"):
        assert series(r25, porch, feature) == series(traces[4][1], porch, feature), feature
        assert series(r25, indoor, feature) == series(traces[2][1], indoor, feature), feature
    assert series(r25, porch, "temperature") != series(traces[2][1], porch, "temperature")
    assert rules.INDOOR_ASSUMED is False


def test_the_device_reporting_rule_sends_illuminance_only_on_the_hourly_heartbeat_without_residents(home, tmp_path):
    """Protocol A6.9: under the RTCGQ11LM's real rule (no illuminance without movement) a resident-free home's
    illuminance sensors report only on their hourly heartbeat; under the catalog's rule they report every change.
    The rule is named in the header and the file name; the catalog's trace keeps its old name and header."""
    from vesper.twins import runtime
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    cat = record_trace(home, env, tmp_path, hours=8, fetch=False)
    dev = record_trace(home, env, tmp_path, hours=8, fetch=False, reporting_rule="device")
    assert cat.name == f"trace-{env.key}.jsonl.gz" and dev.name == f"trace-{env.key}-rho_device.jsonl.gz"
    (hc, rc), (hd, rd) = read(cat), read(dev)
    assert "reporting_rule" not in hc and hd["reporting_rule"] == "device"

    def lux(reports):
        return [r for r in reports if r["feature"] == "illuminance"]
    assert len(lux(rd)) < len(lux(rc))
    by_twin: dict = {}
    for r in lux(rd):
        by_twin.setdefault(r["twin_id"], []).append(r["t"])
    assert by_twin and all(len(ts) <= 8 for ts in by_twin.values())  # at most one an hour
    assert all(b - a >= 3600 - 1e-6 for ts in by_twin.values() for a, b in zip(ts, ts[1:], strict=False))
    assert [r for r in rd if r["feature"] != "illuminance"] == [r for r in rc if r["feature"] != "illuminance"]
    assert runtime.REPORTING_RULE == "catalog"


def test_a_trace_recorded_under_a_knob_is_never_reused_for_another_setting(home, tmp_path, monkeypatch):
    """The reuse key names every indoor knob that is not at its default (review R4-B): a trace recorded with the
    solar aperture doubled is not served to a nominal request, nor a nominal one to a knobbed request; the default
    setting's header is as before (no knob key), so traces recorded before the knobs are still reused."""
    from vesper.edt import trace
    from vesper.env import indoor
    env = Env(PHOENIX, "2026-07-15", 0.0, 4)
    nominal = record_trace(home, env, tmp_path, hours=2, fetch=False)
    header, _ = read(nominal)
    assert "indoor_knobs" not in header
    with indoor.knobs(SOLAR_APERTURE_SCALE=2.0):
        knobbed = record_trace(home, env, tmp_path, hours=2, fetch=False)
    header, _ = read(knobbed)
    assert header["indoor_knobs"] == {"SOLAR_APERTURE_SCALE": 2.0}
    monkeypatch.setattr(trace, "_run", lambda *a, **k: pytest.fail("ran again"))
    with indoor.knobs(SOLAR_APERTURE_SCALE=2.0):
        assert record_trace(home, env, tmp_path, hours=2, fetch=False) == knobbed  # the same setting: reused
    monkeypatch.undo()
    header, _ = read(record_trace(home, env, tmp_path, hours=2, fetch=False))
    assert "indoor_knobs" not in header  # the nominal request re-recorded, not the knobbed trace served
    with indoor.knobs(HUMIDITY_CLASS=1, CAPACITY_SCALE=1.0):  # set to their defaults: the default key
        assert indoor.nondefault_knobs() == {}
