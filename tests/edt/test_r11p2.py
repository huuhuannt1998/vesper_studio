"""Revision R11-P2's script (protocol Addendum 23): its settings, the residual day blocks and the per-site draws, the
frame-relation city witness, the families, the thermostat-off commands and the in-context setback judgement."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r11p2", ROOT / "scripts/revision/r11p2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p2 = _load()


# ---- settings --------------------------------------------------------------------------------------------------------

def test_the_moisture_settings_are_x0_and_x0_5_beside_the_reference():
    from vesper.env import indoor
    s = p2.moisture_settings()
    assert s == {"mx0": {"MOISTURE_EXCESS_SCALE": 0.0}, "mx0.5": {"MOISTURE_EXCESS_SCALE": 0.5}}
    assert all(set(k) <= set(indoor.KNOBS) for k in s.values())


def test_the_outdoor_settings_carry_each_draw_s_series_under_the_series_knob():
    draws = {"1": {"series": {"A": [1.0, 2.0]}}, "2": {"series": {"A": [3.0, 4.0]}}}
    s = p2.outdoor_settings(draws)
    assert s == {"od1": {"OUTDOOR_T_SERIES": {"A": [1.0, 2.0]}}, "od2": {"OUTDOOR_T_SERIES": {"A": [3.0, 4.0]}}}


def test_the_context_corners_are_r4b_s_nominal_and_its_2_w_corner():
    from vesper.env import indoor
    c = p2.context_corners()
    assert list(c) == ["G0-Foff-W1-C165", "G2-Foff-W1-C165"]
    assert all(indoor.KNOB_DEFAULTS[k] == v for k, v in c["G0-Foff-W1-C165"].items())
    g2 = c["G2-Foff-W1-C165"]
    assert g2["INTERNAL_GAINS_W_PER_M2"] == 2.0 and g2["ROOF_GROUND"] == 0.0 and g2["PREWARM_DAYS"] == 0.0
    assert g2["CAPACITY_SCALE"] == 1.0 and "PER_ROOM_THERMOSTAT" not in g2


# ---- residuals, blocks and draws -------------------------------------------------------------------------------------

def _hourly(tz: str, first: dt.date, days: int, value=lambda k: float(k)):
    """{utc instant: value} for every local hour of `days` days from `first` (DST included)."""
    z = ZoneInfo(tz)
    start = dt.datetime.combine(first, dt.time(0), tzinfo=z).astimezone(dt.UTC)
    end = dt.datetime.combine(first + dt.timedelta(days=days), dt.time(0), tzinfo=z).astimezone(dt.UTC)
    out, t, k = {}, start, 0
    while t < end:
        out[t] = value(k)
        t += dt.timedelta(hours=1)
        k += 1
    return out


def test_day_blocks_are_whole_local_days_in_local_hour_order_and_skip_gaps_and_clock_changes():
    r = _hourly("Europe/Madrid", dt.date(2012, 3, 23), 4)  # 23rd, 24th, 25th (clocks forward), 26th
    gap = sorted(r)[30]  # an hour of the 24th
    del r[gap]
    blocks = p2.day_blocks(r, "Europe/Madrid")
    assert [b["date"] for b in blocks] == ["2012-03-23", "2012-03-26"]
    assert all(len(b["values"]) == 24 for b in blocks)
    assert blocks[0]["values"] == [float(k) for k in range(24)]  # local midnight first


def test_lag_one_autocorrelation_uses_consecutive_hours_only():
    r = _hourly("UTC", dt.date(2016, 1, 1), 2, value=lambda k: float(k % 2))  # alternating: -1
    assert p2.lag1(r) == pytest.approx(-1.0)
    smooth = _hourly("UTC", dt.date(2016, 1, 1), 2, value=lambda k: float(k))
    assert p2.lag1(smooth) == pytest.approx(1.0)
    gappy = {t: v for k, (t, v) in enumerate(sorted(r.items())) if k % 2 == 0}  # no two consecutive hours
    assert p2.lag1(gappy) is None


def test_each_city_draws_one_source_and_four_of_its_day_blocks_negated_and_the_draws_are_reproducible():
    blocks = {"Belgium": [[1.0] * 24, [2.0] * 24], "Valencia": [[10.0] * 24, [20.0] * 24, [30.0] * 24]}
    cities = ["Z city", "A city", "M city"]
    one = p2.draw_series(blocks, cities, 1)
    assert one == p2.draw_series(blocks, cities, 1)
    assert one != p2.draw_series(blocks, cities, 2)
    assert set(one["series"]) == set(cities) and set(one["source"]) == set(cities)
    for c in cities:
        s, src = one["series"][c], one["source"][c]
        assert len(s) == p2.HOURS == 73
        allowed = {-v for b in blocks[src] for v in b}
        assert set(s) <= allowed  # every value is minus a residual of the city's own source
    srcs = {p2.draw_series(blocks, cities, d)["source"][c] for d in range(1, 9) for c in cities}
    assert srcs == {"Belgium", "Valencia"}


def test_the_drawn_series_summary_is_its_pooled_mean_and_rms():
    out = p2.series_summary({"A": [1.0, -1.0], "B": [3.0, 3.0]})
    assert out["mean_c"] == pytest.approx(1.5) and out["rmse_c"] == pytest.approx((20 / 4) ** 0.5)


# ---- witnesses and families ------------------------------------------------------------------------------------------

def _env(city, date, north):
    from vesper.edt.types import Env
    return Env(city, date, north, 4)


def _out(fired, tod=None):
    from vesper.edt.types import Outcome
    return Outcome(fired=fired, first_tod_h=tod, flaps=False, night_light=False, night_cover=False)


def test_a_city_witness_needs_two_cities_of_one_date_and_bearing_to_diverge_in_the_frame():
    from vesper.edt.oracle import frames_of
    fr = frames_of({"trigger": [{"platform": "time", "at": "07:00:00"}], "action": []})
    a0, a1 = _env("X", "2026-01-14", 0.0), _env("X", "2026-01-14", 180.0)
    b0, b1 = _env("Y", "2026-01-14", 0.0), _env("Y", "2026-01-14", 180.0)
    ft = {e: None for e in (a0, a1, b0, b1)}
    outs = {a0: _out(True, 7.0), a1: _out(True, 7.0), b0: _out(False), b1: _out(False)}
    assert p2.frame_city_witness(outs, ft, fr)
    outs = {a0: _out(True, 7.0), a1: _out(False), b0: _out(True, 7.0), b1: _out(False)}  # bearings only
    assert not p2.frame_city_witness(outs, ft, fr)
    c0 = _env("Y", "2026-04-15", 0.0)  # another city but also another date: not a one-axis pair
    outs = {a0: _out(True, 7.0), c0: _out(False)}
    assert not p2.frame_city_witness(outs, {a0: None, c0: None}, fr)


def test_the_families_read_the_memberships_and_the_groups():
    rec = {"t": {"frame": ["time_spread"], "clock": ["time_spread"], "fo": True, "fc": False, "co": False},
           "h": {"frame": ["fires_somewhere_only"], "clock": [], "fo": True, "fc": True, "co": False},
           "e": {"frame": ["time_spread"], "clock": ["time_spread"], "fo": False, "fc": True, "co": False},
           "n": {"frame": [], "clock": ["flaps"], "fo": False, "fc": False, "co": True}}
    groups = {"temperature": {"t", "e", "n"}, "humidity": {"h", "n"}, "era5_only": {"e"}}
    f = p2.family_sets(rec, groups)
    assert f["frame_flags"] == {"t", "h", "e"}
    assert f["frame_temperature_flags"] == {"t", "e"} and f["frame_humidity_flags"] == {"h"}
    assert f["era5_only_flags"] == {"e"} and f["frame_city_witnesses"] == {"h", "e"}
    assert f["frame_orientation_witnesses"] == {"t", "h"} and f["humidity_orientation_witnesses"] == {"h"}
    assert f["clock_flags"] == {"t", "e", "n"} and f["clock_humidity_flags"] == {"n"}


def test_the_bracket_over_settings_counts_core_possible_and_survival():
    members = {"ref": {"a", "b", "c"}, "od1": {"a", "b"}, "od2": {"a", "c", "d"}}
    out = p2.bracket(members, {"a", "b", "c"}, ["od1", "od2"])
    assert out["robust_core"] == 1 and out["possible_set"] == 4 and out["lost_somewhere"] == 2
    assert out["gained_somewhere"] == 1 and out["settings"] == 2
    assert out["survival_histogram"] == {"0": 0, "1": 2, "2": 1}  # of the published: a 2, b 1, c 1


# ---- the context runs ------------------------------------------------------------------------------------------------

def test_thermostat_off_commands_are_turn_off_or_an_off_mode_and_only_when_accepted():
    rows = [{"t": 1.0, "service": "climate.turn_off", "feature": None, "value": None, "accepted": True,
             "automation": "edt:2"},
            {"t": 2.0, "service": "climate.set_hvac_mode", "feature": "system_mode", "value": "off", "accepted": True,
             "automation": "edt:0"},
            {"t": 3.0, "service": "climate.set_hvac_mode", "feature": "system_mode", "value": "heat",
             "accepted": True, "automation": "edt:1"},
            {"t": 4.0, "service": "climate.turn_off", "value": None, "accepted": False, "automation": "edt:3"},
            {"t": 5.0, "service": "climate.set_temperature", "feature": "setpoint", "value": 7, "accepted": True,
             "automation": "edt:0"}]
    assert p2.off_commands(rows) == [(1.0, "edt:2"), (2.0, "edt:0")]
    assert p2.off_class([(1.0, "edt:2"), (2.0, "edt:0")]) == "target_turns_off"
    assert p2.off_class([(1.0, "edt:2")]) == "companion_turns_off"
    assert p2.off_class([]) == "setpoint_only"


def _run(rooms_min: dict, offs=(), setback=(), climate=()):
    return {"rooms": sorted(rooms_min), "extremes": {r: [(90000.0, v), (90001.0, v + 5)] for r, v in rooms_min.items()},
            "setback": list(setback), "setback_all": list(setback), "climate": list(climate), "offs": list(offs)}


def test_the_setback_judgement_counts_alone_context_survival_new_own_and_the_rule_kind():
    base = _run({"bed": 15.0, "hall": 18.0})
    runs = {("G2", "base", None, "k"): base,
            # x: breaches alone and in context, its own (the context without it does not)
            ("G2", "alone", "x", "k"): _run({"bed": 8.0, "hall": 18.0}),
            ("G2", "ctx", "x", "k"): _run({"bed": 7.0, "hall": 18.0}),
            ("G2", "rest", "x", "k"): _run({"bed": 15.0, "hall": 18.0}),
            # y: breaches only in context, a companion turns the thermostat off
            ("G2", "alone", "y", "k"): _run({"bed": 14.0, "hall": 18.0}),
            ("G2", "ctx", "y", "k"): _run({"bed": 4.0, "hall": 18.0}, offs=[(3600.0, "edt:3")]),
            ("G2", "rest", "y", "k"): _run({"bed": 4.0, "hall": 18.0}, offs=[(3600.0, "edt:2")]),
            # z: breaches alone, restored in context by a companion's 19 C between its setback's start and end
            ("G2", "alone", "z", "k"): _run({"bed": 6.0, "hall": 18.0}, setback=[(100.0, 200.0)]),
            ("G2", "ctx", "z", "k"): _run({"bed": 16.0, "hall": 18.0}, setback=[(100.0, 200.0)],
                                          climate=[(150.0, "edt:1", "climate.x", 19.0, True)]),
            ("G2", "rest", "z", "k"): _run({"bed": 16.0, "hall": 18.0})}
    out = p2.judge_setback(runs, "G2", ["x", "y", "z"], ["k"], names={"x": "A1", "y": "A2", "z": "A3"},
                           repo_of={"A1": "R1", "A2": "R1", "A3": "R2"}, static_off={"A2"})
    assert out["runs"] == 3 and out["breaching_alone"] == 2 and out["breaching_in_context"] == 2
    assert out["survive"] == 1 and out["survive_and_own"] == 1 and out["new_in_context"] == 1
    assert out["lost"] == 1 and out["why_lost"] == {"restored_by_companion": 1}
    assert out["automations_breaching_alone"] == 2 and out["automations_breaching_in_context"] == 2
    assert out["repositories_breaching_in_context"] == 1
    assert out["rule_kind"]["new_in_context"] == {"target_turns_off": 0, "companion_turns_off": 1,
                                                  "setpoint_only": 0}
    assert out["rule_kind"]["in_context"] == {"target_turns_off": 0, "companion_turns_off": 1, "setpoint_only": 1}
    assert out["rule_kind"]["new_in_context_target_static"] == {"setpoint_only": 0, "turns_thermostat_off": 1}
    assert out["coldest_caused_room_in_context_c"] == [4.0, 5.5, 7.0]
