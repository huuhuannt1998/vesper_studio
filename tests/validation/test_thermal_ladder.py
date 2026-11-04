"""Addendum 3 of docs/experiments/environment-validation.md: the thermal ladder and its identifiability check.
Synthetic rooms with known parameters; the real datasets are not needed."""
import numpy as np
import pytest

from vesper.validation import thermal_ladder as tl

TRUE_2R2C = {"r_ao": 0.08, "r_am": 0.03, "c_a": 1.2e6, "c_m": 2.5e7, "p": 1600.0, "ga": 1.6}


def _week(hours: int = 480, seed: int = 3):
    """An outdoor temperature with a day, a slow weather and a fast part; clear and cloudy days; a heating schedule
    that changes from day to day, so that every time scale is excited."""
    rng = np.random.default_rng(seed)
    h = np.arange(hours)
    walk = np.cumsum(rng.normal(0, 0.35, hours))
    t_out = 6 + 5 * np.sin(2 * np.pi * (h - 9) / 24) + walk - walk.mean()
    day = np.clip(np.sin(2 * np.pi * (h - 6) / 24), 0, None)
    ghi = 650 * day * np.repeat(rng.uniform(0.15, 1.0, hours // 24 + 1), 24)[:hours]
    heat = np.zeros(hours)
    for d in range(hours // 24):
        on = rng.integers(0, 24, 2)
        heat[d * 24 + on[0]:d * 24 + on[0] + int(rng.integers(2, 9))] = rng.uniform(0.4, 1.0)
        heat[d * 24 + on[1]:d * 24 + on[1] + int(rng.integers(1, 6))] = 1.0
    return t_out, ghi, heat


def test_a_known_2r2c_room_is_recovered():
    t_out, ghi, heat = _week()
    clean = tl.simulate("2R2C+solar", TRUE_2R2C, t_out, ghi, heat, t0=19.0)
    measured = clean + np.random.default_rng(1).normal(0, 0.005, len(clean))
    fit = tl.fit_model("2R2C+solar", t_out, ghi, measured, heat)
    for name, truth in TRUE_2R2C.items():
        if name == "p":
            continue  # the heater is held, not fitted
        assert fit.params[name] == pytest.approx(truth, rel=0.10), name
        lo, hi = fit.ci95[name]
        assert lo <= truth <= hi, (name, lo, truth, hi)
    assert fit.rmse < 0.05 and fit.r > 0.99 and np.isfinite(fit.aic)


def test_an_unidentifiable_parameter_has_a_wide_interval():
    t_out, _, heat = _week(240)
    truth = {"r": 0.08, "c": 3e6, "p": 1600.0, "ga": 1.5}
    zero = np.zeros(len(t_out))  # no irradiance: the aperture changes nothing
    measured = tl.simulate("1R1C+solar", truth, t_out, zero, heat, t0=19.0)
    measured = measured + np.random.default_rng(2).normal(0, 0.02, len(measured))
    fit = tl.fit_model("1R1C+solar", t_out, zero, measured, heat)
    lo, hi = fit.ci95["ga"]
    assert hi / lo > 10
    assert fit.ci95["r"][1] / fit.ci95["r"][0] < 10  # the others are pinned down


def test_the_orders_are_nested_in_their_parameters():
    assert tl.PARAMS["1R1C"] == ("r", "c")
    assert set(tl.PARAMS["1R1C+solar"]) == set(tl.PARAMS["1R1C"]) | {"ga"}
    assert set(tl.PARAMS["2R2C+solar"]) == set(tl.PARAMS["2R2C"]) | {"ga"}
    assert tl.ORDERS == ("1R1C", "1R1C+solar", "2R2C", "2R2C+solar")


def test_parameters_per_square_metre_round_trip():
    truth = {k: v for k, v in TRUE_2R2C.items() if k != "p"}
    per = tl.per_m2(truth)
    assert per["c_m"] == pytest.approx(truth["c_m"] / 16)
    assert tl.from_per_m2(per) == pytest.approx(truth)


def test_the_thermostat_holds_the_room_near_its_setpoint_by_its_own_state():
    t_out = np.full(240, -2.0)
    ghi = np.zeros(240)
    p = {"r": 0.08, "c": 3e6, "p": 1500.0}  # the heater is a parameter of the simulation, held in the fit
    sim = tl.simulate("1R1C", p, t_out, ghi, None, t0=17.0, setpoint=19.0)
    assert 17.5 < sim[-1] < 20.5  # far above the unheated -2 + ...; the heating answers the room's own state
    cold = tl.simulate("1R1C", {**p, "p": 20.0}, t_out, ghi, None, t0=17.0, setpoint=19.0)
    assert cold[-1] < sim[-1] - 1


def test_the_ladder_report_names_the_outcome(monkeypatch):
    import pandas as pd
    hours = 24 * 12
    t_out, ghi, heat = _week(hours, seed=5)

    def frame(cols, t_name):
        idx = pd.date_range("2016-02-01", periods=hours, freq="1h", tz="UTC")
        data = {t_name: t_out}
        for k, col in enumerate(cols):
            room = tl.simulate("1R1C+solar", {"r": 0.08 + 0.01 * k, "c": 3e6, "p": 1600.0, "ga": 1.0}, t_out, ghi,
                               heat, t0=19.0)
            data[col] = room + np.random.default_rng(k).normal(0, 0.05, hours)
        return pd.DataFrame(data, index=idx)

    monkeypatch.setattr(tl, "load_appliances", lambda folder: frame(["T1", "T2"], "T_out"))
    monkeypatch.setattr(tl, "load_sml2010", lambda folder: frame(["t_dining", "t_room"], "t_out"))
    monkeypatch.setattr(tl, "_era5", lambda site, index, cache: {"shortwave_radiation": list(ghi),
                                                                 "temperature_2m": list(t_out)})
    monkeypatch.setattr(tl, "APPLIANCES_ROOMS", {"T1": "kitchen", "T2": "living room"})
    report = tl.ladder_report("a", "b", None, orders=("1R1C", "1R1C+solar"), workers=1)
    assert report["outcome"] in {"repair", "sponge", "neither"}
    assert set(report["orders"]) == {"1R1C", "1R1C+solar"}
    row = report["orders"]["1R1C+solar"]
    assert {"A", "B", "identifiability", "verdict", "aic_A"} <= set(row)
    assert set(row["identifiability"]) == set(tl.PARAMS["1R1C+solar"])
    assert row["verdict"] in {"repair", "sponge", "neither"}
    assert row["verdict_without_width_post_hoc"] in {"repair", "sponge", "neither"}
    assert report["code_sha256"] == tl.code_sha256() and row["A"]["n_eff"]["min"] > 0
    room = next(iter(row["A"]["rooms"].values()))
    assert {"fit", "ci_end", "bound_limited", "rho", "sse_ratio_threshold"} <= set(room) and room["fit"]["nfev"] > 0


def test_the_heater_scale_is_not_identifiable_beside_the_aperture():
    """(1/R, C, P, gA) scaled together leave every temperature unchanged: why the fit holds P."""
    t_out, ghi, heat = _week(240)
    a = {"r": 0.08, "c": 3e6, "p": 1600.0, "ga": 1.5}
    s = 1.7
    b = {"r": a["r"] / s, "c": a["c"] * s, "p": a["p"] * s, "ga": a["ga"] * s}
    one = tl.simulate("1R1C+solar", a, t_out, ghi, heat, t0=19.0)
    two = tl.simulate("1R1C+solar", b, t_out, ghi, heat, t0=19.0)
    assert np.allclose(one, two, atol=1e-9)


# -- review of 2026-11-04: registered bounds, interval-end flags, the verdict rule, the report's provenance --------
def test_the_bounds_are_the_ones_registered_in_addendum_3():
    """Addendum 3: R, R_ao, R_am 1e-3-2 K/W; C and C_m 4e5-5e8 J/K (C_a 4e5-5e7); g·A 1e-3-30 m2; the mass-start
    nuisance -10..+10 C (m0 = offset + 10, so 0.01-20 in the log parametrisation)."""
    registered = {"r": (1e-3, 2.0), "r_ao": (1e-3, 2.0), "r_am": (1e-3, 2.0), "c": (4e5, 5e8), "c_a": (4e5, 5e7),
                  "c_m": (4e5, 5e8), "ga": (1e-3, 30.0), "m0": (0.01, 20.0)}
    assert tl.BOUNDS == registered
    from pathlib import Path
    text = Path(__file__).resolve().parents[2].joinpath("docs/experiments/environment-validation.md").read_text()
    flat = text.replace("\n", " ")
    assert "R_am 1e-3-2 K/W" in flat and "C and C_m 4e5-5e8 J/K (C_a 4e5-5e7)" in flat


@pytest.mark.parametrize("edge, crossed, expected", [
    (0.5, True, "inside"), (1e-3, False, "bound"), (2.0, False, "bound"), (0.03, False, "grid")])
def test_an_interval_end_is_inside_at_a_bound_or_at_the_grid(edge, crossed, expected):
    assert tl._end_status("r", edge, crossed) == expected


def test_an_optimum_on_a_bound_is_open_and_bound_limited():
    t_out, ghi, heat = _week(240)
    truth = {"r": 0.08, "c": 1.5e5, "p": 1600.0}  # a capacity below the bound: the optimum is pinned to it
    measured = tl.simulate("1R1C", truth, t_out, ghi, heat, t0=19.0)
    measured = measured + np.random.default_rng(4).normal(0, 0.02, len(measured))
    fit = tl.fit_model("1R1C", t_out, ghi, measured, heat)
    assert fit.params["c"] == pytest.approx(tl.BOUNDS["c"][0], rel=1e-3)
    assert fit.ci_open["c"][0] is True and fit.ci_end["c"][0] == "bound" and fit.bound_limited["c"][0] is True


def test_a_grid_limited_end_is_not_a_bound_limited_one():
    t_out, _, heat = _week(240)
    truth = {"r": 0.08, "c": 3e6, "p": 1600.0, "ga": 1.5}
    zero = np.zeros(len(t_out))
    measured = tl.simulate("1R1C+solar", truth, t_out, zero, heat, t0=19.0)
    fit = tl.fit_model("1R1C+solar", t_out, zero, measured, heat)
    assert fit.ci_end["ga"] == ("grid", "grid") and fit.ci_open["ga"] == (True, True)
    assert fit.bound_limited["ga"] == (False, False)
    assert fit.ci_end["r"] == ("inside", "inside")


def test_the_main_fit_reports_its_status_and_evaluations():
    t_out, ghi, heat = _week(240)
    measured = tl.simulate("1R1C", {"r": 0.08, "c": 3e6, "p": 1600.0}, t_out, ghi, heat, t0=19.0)
    fit = tl.fit_model("1R1C", t_out, ghi, measured, heat, profile=False)
    assert fit.fit_info["status"] in (1, 2, 3, 4, 0, -1) and fit.fit_info["nfev"] > 0
    assert 0 <= fit.fit_info["start"] < 2


def test_the_code_hash_is_the_files():
    import hashlib
    assert tl.code_sha256() == hashlib.sha256(open(tl.__file__, "rb").read()).hexdigest()


@pytest.mark.parametrize("order, b_rmse, b_r, stable, aperture, wide, verdict", [
    # repair at the boundary: 3.0 C, r = 0.70, parameters within +-50 %, aperture 0.5-3 m2, no interval 10x wide
    ("1R1C+solar", 3.0, 0.70, True, 1.0, False, "repair"),
    ("2R2C", 3.0, 0.70, True, None, False, "repair"),            # no aperture to judge in a non-solar order
    ("1R1C+solar", 3.01, 0.70, True, 1.0, False, "neither"),
    ("1R1C+solar", 3.0, 0.699, True, 1.0, False, "neither"),
    ("1R1C+solar", 3.0, 0.70, False, 1.0, False, "neither"),     # parameters not within +-50 %
    ("1R1C+solar", 3.0, 0.70, True, 0.5, False, "repair"),       # aperture window is inclusive
    ("1R1C+solar", 3.0, 0.70, True, 3.0, False, "repair"),
    ("1R1C+solar", 3.0, 0.70, True, 0.49, False, "sponge"),      # an implausible aperture
    ("1R1C+solar", 3.0, 0.70, True, 3.01, False, "sponge"),
    ("1R1C+solar", 3.0, 0.70, True, None, False, "sponge"),
    ("2R2C+solar", 1.0, 0.99, True, 1.0, True, "sponge"),        # an order of magnitude wide voids a good fit
    ("2R2C", 2.6, 0.83, False, None, True, "sponge"),
    ("2R2C", 4.0, 0.74, False, None, False, "neither"),          # the middle: well identified, 3 < RMSE <= 5
    ("2R2C", 6.0, 0.60, True, None, False, "neither"),           # the clear neither: RMSE > 5, well identified
    ("2R2C", 6.0, 0.60, True, None, True, "sponge"),
])
def test_the_verdict_rule(order, b_rmse, b_r, stable, aperture, wide, verdict):
    assert tl._verdict(order, b_rmse, b_r, stable, aperture, wide)["verdict"] == verdict


def test_the_verdict_names_the_middle_and_the_clear_neither():
    middle = tl._verdict("2R2C", 4.0, 0.74, False, None, False)
    clear = tl._verdict("2R2C", 5.01, 0.74, False, None, False)
    edge = tl._verdict("2R2C", 5.0, 0.74, False, None, False)
    assert middle["inconclusive_middle"] and not middle["neither_clear"]
    assert clear["neither_clear"] and not clear["inconclusive_middle"]
    assert edge["inconclusive_middle"] and not edge["neither_clear"]
    assert tl._verdict("2R2C", 6.0, 0.6, True, None, True)["neither_clear"] is False  # not well identified


def test_the_verdict_without_the_width_condition_is_named_as_post_hoc():
    v = tl._verdict("2R2C+solar", 4.08, 0.74, False, 0.52, True)
    assert v["verdict"] == "sponge" and v["verdict_without_width_post_hoc"] == "neither"
    v = tl._verdict("1R1C", 2.59, 0.83, False, None, True)
    assert v["verdict"] == "sponge" and v["verdict_without_width_post_hoc"] == "neither"
    v = tl._verdict("1R1C", 2.59, 0.83, True, None, True)
    assert v["verdict"] == "sponge" and v["verdict_without_width_post_hoc"] == "repair"


@pytest.mark.parametrize("ratios, stable", [
    ({"r": 0.5, "c": 1.5}, True), ({"r": 0.499, "c": 1.0}, False), ({"r": 1.0, "c": 1.501}, False),
    ({"r": 1.0}, True)])
def test_parameters_are_stable_within_fifty_percent(ratios, stable):
    assert tl._stable(ratios) is stable


@pytest.mark.parametrize("verdicts, aic, order, outcome", [
    # the simplest repairing order, not the best AIC
    ({"1R1C": "neither", "1R1C+solar": "repair", "2R2C": "neither", "2R2C+solar": "repair"},
     {"1R1C": 10, "1R1C+solar": 30, "2R2C": 20, "2R2C+solar": 5}, "1R1C+solar", "repair"),
    ({"1R1C": "repair", "1R1C+solar": "repair", "2R2C": "repair", "2R2C+solar": "repair"},
     {"1R1C": 10, "1R1C+solar": 5, "2R2C": 20, "2R2C+solar": 1}, "1R1C", "repair"),
    # no repair: the order with the lowest AIC speaks
    ({"1R1C": "neither", "1R1C+solar": "sponge", "2R2C": "neither", "2R2C+solar": "sponge"},
     {"1R1C": 10, "1R1C+solar": 30, "2R2C": 20, "2R2C+solar": 5}, "2R2C+solar", "sponge"),
    ({"1R1C": "neither", "1R1C+solar": "sponge", "2R2C": "neither", "2R2C+solar": "sponge"},
     {"1R1C": 3, "1R1C+solar": 30, "2R2C": 20, "2R2C+solar": 5}, "1R1C", "neither"),
])
def test_the_outcome_is_the_simplest_repair_else_the_best_aic(verdicts, aic, order, outcome):
    assert tl._outcome(tuple(verdicts), verdicts, aic) == (order, outcome)


# -- the post hoc convergence check: the main fit's cap is a parameter; the registered default is unchanged ----------
def test_the_main_fit_cap_defaults_to_the_one_the_run_used_and_can_be_raised():
    import inspect
    assert tl.MAIN_MAX_NFEV == 60
    assert inspect.signature(tl.fit_model).parameters["max_nfev"].default == tl.MAIN_MAX_NFEV
    assert inspect.signature(tl.ladder_report).parameters["max_nfev"].default == tl.MAIN_MAX_NFEV
    t_out, ghi, heat = _week(240)
    truth = {"r_ao": 0.08, "r_am": 0.03, "c_a": 1.2e6, "c_m": 2.5e7, "p": 1600.0, "ga": 1.6}
    measured = tl.simulate("2R2C+solar", truth, t_out, ghi, heat, t0=19.0)
    capped = tl.fit_model("2R2C+solar", t_out, ghi, measured, heat, profile=False, max_nfev=3)
    assert capped.fit_info["status"] == 0 and capped.fit_info["max_nfev"] == 3  # stopped by the cap
    raised = tl.fit_model("2R2C+solar", t_out, ghi, measured, heat, profile=False, max_nfev=(3, 400))
    assert raised.fit_info["status"] != 0 and raised.fit_info["max_nfev"] == 400 and raised.sse < capped.sse
    one = tl.fit_model("2R2C+solar", t_out, ghi, measured, heat, profile=False, max_nfev=(400, 1000))
    assert one.fit_info["max_nfev"] == 400  # no escalation when the first cap converges
