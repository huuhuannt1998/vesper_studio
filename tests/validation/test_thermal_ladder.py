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


def test_the_heater_scale_is_not_identifiable_beside_the_aperture():
    """(1/R, C, P, gA) scaled together leave every temperature unchanged: why the fit holds P."""
    t_out, ghi, heat = _week(240)
    a = {"r": 0.08, "c": 3e6, "p": 1600.0, "ga": 1.5}
    s = 1.7
    b = {"r": a["r"] / s, "c": a["c"] * s, "p": a["p"] * s, "ga": a["ga"] * s}
    one = tl.simulate("1R1C+solar", a, t_out, ghi, heat, t0=19.0)
    two = tl.simulate("1R1C+solar", b, t_out, ghi, heat, t0=19.0)
    assert np.allclose(one, two, atol=1e-9)
