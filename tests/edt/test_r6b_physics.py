"""Revision R6-B's script (protocol Addendum 9): its corners, settings and the relations it recomputes."""
import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r6b_physics", ROOT / "scripts/revision/r6b_physics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r6b = _load()


def test_the_h_breakdown_adds_up_to_each_room_s_loss():
    from tests.lab.maps import house
    from vesper.env.indoor import room_models
    from vesper.lab.windows import plan_windows
    m = house()
    models = room_models(m, plan_windows(m))
    out = r6b.h_breakdown(models)
    for rid, row in out["rooms"].items():
        assert row["walls_w_k"] + row["glass_w_k"] + row["ventilation_w_k"] == pytest.approx(row["model_h_w_k"],
                                                                                            abs=0.03)
    assert sum(out["shares"].values()) == pytest.approx(1.0, abs=1e-3)
    assert out["ventilation_w_k_at"]["1.0"] == pytest.approx(2 * out["ventilation_w_k_at"]["0.5"], abs=0.02)
    assert out["total_w_k_at"]["0.5"] == pytest.approx(out["total_w_k"], abs=0.02)


def test_the_ach_jobs_run_the_nominal_everywhere_and_the_two_corners_in_the_core():
    envs = {f"k{i}": {"key": f"k{i}", "city": "X, Y", "date": "2026-01-14", "north_deg": 0.0, "rung": 4}
            for i in range(6)}
    d = {"envs": envs, "pop": ["a"], "run": {"a": {"bound": {"x": 1}, "outcomes": {k: {} for k in envs}}},
         "home": "h", "cache": "c"}
    r6b.r4b.r2c_envs = lambda d: ["k0", "k1"]  # the core
    try:
        jobs = r6b.ach_jobs(d, "cat")
    finally:
        import importlib
        importlib.reload(r6b.r4b)
    labels = Counter_of(j["tag"][0] for j in jobs)
    assert labels[r6b.ach_label(0.3, r6b.NOMINAL)] == 6 * 2  # baseline + the automation, every environment
    assert labels[r6b.ach_label(1.0, r6b.WORST)] == 2 * 2
    assert {j["knobs"]["ACH"] for j in jobs} == {0.3, 0.5, 1.0}
    worst = next(j for j in jobs if j["tag"][0] == r6b.ach_label(1.0, r6b.WORST))
    assert worst["knobs"]["ROOF_GROUND"] == 1.0 and math.isinf(worst["knobs"]["PREWARM_DAYS"])
    assert worst["knobs"]["CAPACITY_SCALE"] == pytest.approx(80 / 165)
    nominal = next(j for j in jobs if j["tag"][0] == r6b.ach_label(0.5, r6b.NOMINAL))
    assert nominal["knobs"] == {"INTERNAL_GAINS_W_PER_M2": 0.0, "ROOF_GROUND": 0.0, "PREWARM_DAYS": 0.0,
                                "CAPACITY_SCALE": 1.0, "ACH": 0.5}  # the model as built


def Counter_of(xs):
    from collections import Counter
    return Counter(xs)


def test_the_settings_are_the_reference_the_aperture_and_the_two_direct_shares():
    s = r6b.settings(0.31)
    assert s == {"ref": {}, "ap0.31": {"SOLAR_APERTURE_SCALE": 0.31}, "ds0.6": {"DIRECT_SHARE": 0.6},
                 "ds0.95": {"DIRECT_SHARE": 0.95}}


def test_relations_recompute_the_sweep_s_clock_and_frame_divergences():
    """A sunset-triggered automation firing at different clock times but the same time after sunset: the clock
    relation sees a time spread, the frame relation does not."""
    from vesper.edt.oracle import sun_events
    from vesper.edt.types import Env
    envs = [Env("Stockholm, Sweden", "2026-01-14", 0.0, 4), Env("Singapore", "2026-01-14", 0.0, 4)]
    from vesper.edt.oracle import site_of
    bound = {"trigger": [{"platform": "sun", "event": "sunset"}], "action": [{"service": "light.turn_on"}]}
    outcomes, first_t = {}, {}
    for e in envs:
        t = 86400 + sun_events(site_of(e), 1)["sunset"]
        outcomes[e.key] = {"fired": True, "first_tod_h": (t % 86400) / 3600, "flaps": False, "night_cover": False,
                           "night_light": False}
        first_t[e.key] = t
    clock, frame = r6b.relations(bound, outcomes, first_t, {e.key: e for e in envs})
    assert "time_spread" in clock and "time_spread" not in frame


# -- the validation re-checks (A9.3-A9.8) -----------------------------------------------------------------------------
def test_free_floating_hours_are_below_the_setpoint_less_the_margin_and_falling():
    measured = [20.0, 18.5, 18.0, 18.2, None, 17.0, 16.5, 16.0]
    assert r6b.free_hours(measured, 20.0, 1.0) == [1, 2, 6, 7]  # 3 rises, 5 follows a gap
    assert r6b.free_hours(measured, 19.0, 1.0) == [6, 7]  # 18.0 is not below 18
    assert r6b.episodes_of([1, 2, 6, 7, 9]) == [(1, 2), (6, 7), (9, 9)]


def test_the_root_is_interpolated_where_the_bias_first_reaches_zero():
    ks = [0.0, 0.5, 1.0]
    assert r6b.root_of(ks, [-2.0, -1.0, 1.0]) == pytest.approx(0.75)
    assert r6b.root_of(ks, [1.0, 2.0, 3.0]) is None  # warm even with no sun
    assert r6b.root_of(ks, [-3.0, -2.0, -1.0]) is None
    assert r6b.root_of(ks, [0.0, 1.0, 2.0]) == 0.0


def test_erbs_is_continuous_at_its_breaks_and_a_clear_sky_is_mostly_beam():
    assert r6b.erbs_kd(0.22) == pytest.approx(1 - 0.09 * 0.22, abs=2e-3)
    assert r6b.erbs_kd(0.80) == pytest.approx(0.165, abs=2e-3)
    assert r6b.erbs_kd(0.75) < 0.3 < 0.9 < r6b.erbs_kd(0.1)


def test_the_window_s_configuration_factor_is_the_textbook_one():
    """A horizontal element and a vertical rectangle x 0..W, z 0..H at depth D: (1/2pi)(atan(W/D) - D/sqrt(D^2+H^2)
    atan(W/sqrt(D^2+H^2)))."""
    W, H, D = 1.2, 1.5, 2.0
    exact = (math.atan(W / D) - D / math.hypot(D, H) * math.atan(W / math.hypot(D, H))) / (2 * math.pi)
    assert r6b.window_config_factor(D, 0.0, W, 0.0, H) == pytest.approx(exact, rel=1e-4)
    out = r6b.point_over_average()
    assert out["depth_3.5m"]["ratio_to_average"] < out["depth_2m"]["ratio_to_average"]


def test_the_low_sun_band_keeps_only_minutes_with_the_reference_between_0_and_5_degrees():
    import datetime as dt

    from vesper.env.site import parse_site
    from vesper.env.sun import sun_at
    site = parse_site({"city": "Stockholm, Sweden", "date": "2026-01-14"})
    midnight = dt.datetime.combine(site.date, dt.time(0), tzinfo=site.zone)
    times = [midnight + dt.timedelta(minutes=m) for m in range(0, 1440, 5)]

    def exact(site, ts):
        return [(sun_at(site.lat, site.lon, site.elevation_m, t).elevation,
                 sun_at(site.lat, site.lon, site.elevation_m, t).azimuth) for t in ts], {}
    errs = r6b.low_sun_errors(site, times, exact)
    els = [sun_at(site.lat, site.lon, site.elevation_m, t).elevation for t in times]
    assert len(errs["el"]) == sum(1 for e in els if 0 < e <= 5) > 0
    assert max(abs(x) for x in errs["el"] + errs["az"] + errs["sep"]) < 1e-6


def test_station_hours_average_the_passing_reports_of_each_utc_hour(tmp_path):
    import pandas as pd
    p = tmp_path / "s.csv"
    p.write_text('"STATION","DATE","TMP"\n'
                 '"x","2012-03-13T11:00:00","+0120,1"\n"x","2012-03-13T11:30:00","+0140,1"\n'
                 '"x","2012-03-13T12:00:00","+9999,9"\n"x","2012-03-13T12:30:00","+0500,3"\n'
                 '"x","2012-03-13T13:00:00","-0010,5"\n')
    idx = pd.date_range("2012-03-13 11:00", periods=3, freq="1h", tz="UTC")
    assert r6b.station_hourly(p, idx) == [pytest.approx(13.0), None, pytest.approx(-1.0)]
