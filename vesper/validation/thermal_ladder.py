"""Addendum 3 of docs/experiments/environment-validation.md: a ladder of grey-box room-temperature models (1R1C,
1R1C+solar, 2R2C, 2R2C+solar), fitted on dataset A room by room, held out on dataset B, with the identifiability of
every parameter read off its profile likelihood. Whether VESPER's one-node room can be repaired by a higher-order
model, or whether the higher order only soaks up whatever the data hold (a sponge).

Heating is unknown in both datasets. It is never read from the measurement: it is the room's own setpoint rule
applied to the model's own state (a proportional band around the room's 10th-percentile temperature) times a heater
of HEAT_W (its power cannot be fitted beside the solar aperture: see PARAMS). Tests that know their heating pass it
as `heat_on` (a share per hour) instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from vesper.validation.environment import (
    APPLIANCES_ROOMS,
    SETPOINT_PERCENTILE,
    SML_ROOMS,
    STANDARD_ROOM,
    _agreement,
    _era5,
    _percentile,
    _values,
    load_appliances,
    load_sml2010,
)

ORDERS = ("1R1C", "1R1C+solar", "2R2C", "2R2C+solar")
PARAMS = {"1R1C": ("r", "c"), "1R1C+solar": ("r", "c", "ga"),
          "2R2C": ("r_ao", "r_am", "c_a", "c_m"), "2R2C+solar": ("r_ao", "r_am", "c_a", "c_m", "ga")}
# The heater's power is NOT fitted. With the heater's power P and the solar aperture g·A both unknown, scaling
# (1/R, C, P, g·A) by one factor leaves every temperature unchanged (found on a synthetic room, where fits from three
# starts reached the same error with different constants): the data fix only the ratios. P is held at VESPER's own
# heater, 100 W/m² of the standard room; R, C and g·A are read conditional on it.
HEAT_W = 1600.0
# bounds and VESPER's own start for a room of the standard room's 16 m² (the constants of vesper.env.indoor)
# (the registered bounds of addendum 3; tests/validation/test_thermal_ladder.py pins them)
BOUNDS = {"r": (1e-3, 2.0), "r_ao": (1e-3, 2.0), "r_am": (1e-3, 2.0), "c": (4e5, 5e8), "c_a": (4e5, 5e7),
          "c_m": (4e5, 5e8), "ga": (1e-3, 30.0), "m0": (0.01, 20.0)}
START = {"r": 0.085, "r_ao": 0.085, "r_am": 0.03, "c": 2.64e6, "c_a": 1.0e6, "c_m": 2.0e7, "ga": 0.9,
         "m0": 10.0}
# a nuisance of the two-node orders (not profiled, not carried to B): the mass node's start less the air's, +10 °C so
# that it stays positive in the log parametrisation; the mass cannot be assumed to start at the air's temperature
NUISANCE = {"1R1C": (), "1R1C+solar": (), "2R2C": ("m0",), "2R2C+solar": ("m0",)}
M0_SHIFT_C = 10.0
SUB = 6  # 10-minute steps in the hour
DT_S = 3600.0 / SUB
BAND_C = 1.0  # the thermostat's proportional half-band: full heat at setpoint - BAND, none at setpoint + BAND
SPIN_UP_H = 48  # hours at the start of a series not scored
AREA_M2 = STANDARD_ROOM["w"] * STANDARD_ROOM["d"]  # the room the parameters are for
GRID = 15
MAIN_MAX_NFEV = 60  # the main fit's evaluation cap in the registered run (addendum 3 does not register one)
RHO_CAP = 0.99  # not registered in addendum 3; disclosed in the results (n_eff = n (1-rho)/(1+rho))
GRID_SPAN = 30.0  # the profile's log grid runs from 1/30 to 30 times the optimum
CHI2_95 = 3.841  # chi-square, one degree of freedom
WIDE = 10.0  # an interval whose upper end is this many times its lower end spans an order of magnitude
APERTURE_M2 = (0.5, 3.0)  # a plausible solar aperture g·A for a 16 m² room
CRITERIA = {"repair": {"b_rmse_c": 3.0, "b_r": 0.70, "param_tolerance": 0.5}, "neither": {"b_rmse_c": 5.0}}


@dataclass
class FitResult:
    params: dict
    rmse: float
    r: float
    ci95: dict
    aic: float
    ci_open: dict = field(default_factory=dict)  # per parameter: (lower end open, upper end open): not crossed
    ci_end: dict = field(default_factory=dict)  # per parameter, per end: "inside", "grid" (the grid's end) or "bound"
    bound_limited: dict = field(default_factory=dict)  # per parameter, per end: the end is a parameter bound
    fit_info: dict = field(default_factory=dict)  # the chosen start's least_squares status and evaluations
    rho: float = 0.0  # lag-1 autocorrelation of the residual, capped at RHO_CAP
    n: int = 0
    n_eff: float = 0.0
    sse: float = 0.0
    setpoint: float | None = None


def per_m2(params: dict) -> dict:
    """Per unit floor area of the standard room: a resistance as a conductance (W/K per m²), the rest by area."""
    return {k: (1.0 / (v * AREA_M2) if k.startswith("r") else v / AREA_M2) for k, v in params.items()}


def from_per_m2(per: dict, area_m2: float = AREA_M2) -> dict:
    return {k: (1.0 / (v * area_m2) if k.startswith("r") else v * area_m2) for k, v in per.items()}


# -- the models ------------------------------------------------------------------------------------------------
def simulate(order: str, params: dict, t_out, ghi, heat_on, *, t0: float, setpoint: float | None = None):
    """The hourly mean temperature of the model's room, 10-minute steps, inputs held within the hour. `heat_on`: the
    heating share (0-1) for each hour; None for the room's own setpoint rule on the model's state."""
    if heat_on is None and setpoint is None:
        raise ValueError("a setpoint, or the heating shares")
    n = len(t_out)
    to = np.repeat(np.asarray(t_out, float), SUB).tolist()
    sun = np.repeat(np.asarray(ghi, float), SUB).tolist()
    given = None if heat_on is None else np.repeat(np.asarray(heat_on, float), SUB).tolist()
    ga = params.get("ga", 0.0) if order.endswith("+solar") else 0.0
    p = params.get("p", HEAT_W)
    top, inv_band = (setpoint or 0.0) + BAND_C, 1.0 / (2 * BAND_C)
    out = [0.0] * (n * SUB)
    if order.startswith("1R1C"):
        r = params["r"]
        a = math.exp(-DT_S / (r * params["c"]))
        t = t0
        for k in range(n * SUB):
            sh = given[k] if given is not None else min(1.0, max(0.0, (top - t) * inv_band))
            eq = to[k] + r * (ga * sun[k] + p * sh)
            t = eq + (t - eq) * a
            out[k] = t
    else:
        from scipy.linalg import expm
        r_ao, r_am, c_a, c_m = params["r_ao"], params["r_am"], params["c_a"], params["c_m"]
        mat = np.array([[-(1 / r_ao + 1 / r_am) / c_a, 1 / (r_am * c_a)], [1 / (r_am * c_m), -1 / (r_am * c_m)]])
        phi = expm(mat * DT_S)
        gain = np.linalg.solve(mat, phi - np.eye(2))[:, 0]  # the effect of a unit forcing on the air node
        f00, f01, f10, f11 = phi[0, 0], phi[0, 1], phi[1, 0], phi[1, 1]
        m0, m1 = gain[0], gain[1]
        inv_ao, inv_ca = 1.0 / r_ao, 1.0 / c_a
        x0, x1 = t0, t0 + params.get("m0", M0_SHIFT_C) - M0_SHIFT_C
        for k in range(n * SUB):
            sh = given[k] if given is not None else min(1.0, max(0.0, (top - x0) * inv_band))
            u = (to[k] * inv_ao + ga * sun[k] + p * sh) * inv_ca
            x0, x1 = f00 * x0 + f01 * x1 + m0 * u, f10 * x0 + f11 * x1 + m1 * u
            out[k] = x0
    return np.asarray(out).reshape(n, SUB).mean(axis=1)


# -- the fit ---------------------------------------------------------------------------------------------------
def _clean(t_out, ghi, measured):
    t_out = np.array([np.nan if v is None else v for v in t_out], float)
    for k in range(len(t_out)):  # a gap in the outdoor record holds the last value
        if np.isnan(t_out[k]):
            t_out[k] = t_out[k - 1] if k else np.nanmean(t_out)
    sun = np.nan_to_num(np.array([np.nan if v is None else v for v in ghi], float), nan=0.0)
    meas = np.array([np.nan if v is None else v for v in measured], float)
    return t_out, sun, meas


def _start(order: str, k: int) -> dict:
    scale = (1.0, 0.4, 2.5)[k]
    return {n: float(np.clip(START[n] * (scale if n[0] in "rc" else 1.0), *BOUNDS[n]))
            for n in PARAMS[order] + NUISANCE[order]}


class _Problem:
    def __init__(self, order, t_out, ghi, measured, heat_on, setpoint):
        self.order, self.heat_on, self.setpoint = order, heat_on, setpoint
        self.t_out, self.ghi, self.meas = _clean(t_out, ghi, measured)
        self.ok = np.isfinite(self.meas)
        self.ok[:SPIN_UP_H] = False
        first = self.meas[np.isfinite(self.meas)]
        self.t0 = float(first[0]) if len(first) else 20.0
        if heat_on is None and setpoint is None:
            self.setpoint = _percentile([v for v in self.meas.tolist() if v == v], SETPOINT_PERCENTILE)
        self.names = PARAMS[order]
        self.all = PARAMS[order] + NUISANCE[order]

    def sim(self, params: dict):
        return simulate(self.order, params, self.t_out, self.ghi, self.heat_on, t0=self.t0, setpoint=self.setpoint)

    def residual(self, log_free, free, fixed):
        params = {**fixed, **{n: math.exp(v) for n, v in zip(free, log_free, strict=True)}}
        e = (self.sim(params) - self.meas)[self.ok]
        return np.where(np.isfinite(e), e, 1e3)

    def fit(self, free, fixed, x0: dict, max_nfev: int = 60, tol: float = 1e-7):
        from scipy.optimize import least_squares
        lo = [math.log(BOUNDS[n][0]) for n in free]
        hi = [math.log(BOUNDS[n][1]) for n in free]
        start = [min(max(math.log(x0[n]), a), b) for n, a, b in zip(free, lo, hi, strict=True)]
        sol = least_squares(self.residual, start, args=(free, fixed), bounds=(lo, hi), diff_step=1e-4,
                            max_nfev=max_nfev, x_scale=1.0, ftol=tol, xtol=tol)
        return ({n: math.exp(v) for n, v in zip(free, sol.x, strict=True)}, float(2 * sol.cost),
                {"status": int(sol.status), "nfev": int(sol.nfev)})


def _autocorr(e) -> float:
    d = float(np.dot(e, e))
    return 0.0 if d <= 0 or len(e) < 3 else min(RHO_CAP, max(0.0, float(np.dot(e[1:], e[:-1])) / d))


def _profile(prob, name, best: dict, sse_min: float, n_eff: float):
    """The 95 % interval of one parameter: the others re-fitted over a log grid around the optimum."""
    lo_b, hi_b = BOUNDS[name]
    opt = best[name]
    grid = sorted({min(hi_b, max(lo_b, opt * GRID_SPAN ** (2 * k / (GRID - 1) - 1))) for k in range(GRID)} | {opt})
    at = grid.index(opt)
    others = [n for n in prob.all if n != name]
    delta = {}
    for rng in (range(at, len(grid)), range(at, -1, -1)):
        warm = {n: best[n] for n in others}
        for i in rng:
            g = grid[i]
            if i == at:
                delta[i] = 0.0
                continue
            fixed = {name: g}
            if others:
                warm, sse, _ = prob.fit(others, fixed, warm, max_nfev=12, tol=1e-5)
            else:
                sse = float(np.sum(prob.residual([], [], fixed) ** 2))
            delta[i] = n_eff * math.log(max(sse, 1e-12) / max(sse_min, 1e-12))
    ends, status = [], []
    for direction in (-1, 1):
        edge, crossed = opt, False
        i = at
        while 0 <= i + direction < len(grid):
            j = i + direction
            if delta[j] >= CHI2_95:  # crossed between i and j: interpolate in log-parameter
                f = (CHI2_95 - delta[i]) / (delta[j] - delta[i])
                edge = math.exp(math.log(grid[i]) + f * (math.log(grid[j]) - math.log(grid[i])))
                crossed = True
                break
            edge, i = grid[j], j
        ends.append(edge)
        status.append(_end_status(name, edge, crossed))
    return (ends[0], ends[1]), (status[0], status[1])


def _end_status(name: str, edge: float, crossed: bool) -> str:
    """Where an interval end lies: "inside" (the profile crossed the threshold), "bound" (the profile never crossed
    and the end is the parameter's bound, or the optimum itself is on it), or "grid" (never crossed, at the grid's
    end short of the bound: the true end is at least that far)."""
    if crossed:
        return "inside"
    lo, hi = BOUNDS[name]
    return "bound" if math.isclose(edge, lo, rel_tol=1e-6) or math.isclose(edge, hi, rel_tol=1e-6) else "grid"


def fit_model(order: str, t_out, ghi, measured, heat_on, *, setpoint: float | None = None, profile: bool = True,
              starts: int = 2, max_nfev: int | tuple = MAIN_MAX_NFEV) -> FitResult:
    """Least-squares fit of one order to one room's hourly series; the 95 % intervals by profile likelihood.
    `max_nfev`: the main fit's evaluation cap per start; a tuple is tried in turn, from the same starts, until the
    chosen start does not end at its cap (status 0): the post hoc convergence check. The default is the registered
    run's."""
    prob = _Problem(order, t_out, ghi, measured, heat_on, setpoint)
    names = list(prob.all)
    caps = (max_nfev,) if isinstance(max_nfev, int) else tuple(max_nfev)
    for cap in caps:
        best, sse, info = None, math.inf, {}
        for k in range(starts):
            params, s, i = prob.fit(names, {}, _start(order, k), max_nfev=cap)
            if s < sse:
                best, sse, info = params, s, {**i, "start": k, "max_nfev": cap}
        if info["status"] != 0:
            break
    e = (prob.sim(best) - prob.meas)[prob.ok]
    n = len(e)
    rmse = math.sqrt(sse / n) if n else float("nan")
    r = _agreement(prob.sim(best)[prob.ok], prob.meas[prob.ok])["r"]
    rho = _autocorr(e)
    n_eff = max(3.0, n * (1 - rho) / (1 + rho))
    ci, ends = {}, {}
    for name in prob.names:
        if profile:
            ci[name], ends[name] = _profile(prob, name, best, sse, n_eff)
        else:
            ci[name], ends[name] = (best[name], best[name]), ("inside", "inside")
    opened = {k: (v[0] != "inside", v[1] != "inside") for k, v in ends.items()}
    limited = {k: (v[0] == "bound", v[1] == "bound") for k, v in ends.items()}
    aic = n * math.log(max(sse, 1e-12) / n) + 2 * len(names) if n else float("nan")
    return FitResult(params=best, rmse=rmse, r=r, ci95=ci, aic=aic, ci_open=opened, ci_end=ends, bound_limited=limited,
                     fit_info=info, rho=rho, n=n, n_eff=n_eff, sse=sse, setpoint=prob.setpoint)


# -- the ladder ------------------------------------------------------------------------------------------------
def _median(values):
    v = sorted(x for x in values if x is not None and x == x)
    return None if not v else (v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2)


def _task(args):
    order, col, t_out, ghi, measured, profile, max_nfev = args
    f = fit_model(order, t_out, ghi, measured, None, profile=profile, max_nfev=max_nfev)
    return order, col, f


def _row_of(f: FitResult) -> dict:
    return {"params": {k: float(v) for k, v in f.params.items()}, "rmse": round(f.rmse, 3),
            "r": None if f.r is None else float(f.r), "aic": round(f.aic, 1), "n": f.n, "n_eff": round(f.n_eff, 1),
            "setpoint_c": round(f.setpoint, 2) if f.setpoint is not None else None,
            "ci95": {k: [float(a), float(b)] for k, (a, b) in f.ci95.items()},
            "ci_open": {k: [bool(a), bool(b)] for k, (a, b) in f.ci_open.items()},
            "ci_end": {k: list(v) for k, v in f.ci_end.items()},
            "bound_limited": {k: [bool(a), bool(b)] for k, (a, b) in f.bound_limited.items()},
            "fit": f.fit_info, "rho": round(f.rho, 4), "rho_capped": bool(f.rho >= RHO_CAP),
            "sse_ratio_threshold": math.exp(CHI2_95 / f.n_eff)}


def _rule(order: str, b_rmse, b_r, stable: bool, aperture, wide: bool) -> tuple[str, bool, bool, bool]:
    """The registered rule: (verdict, repair, sponge, aperture plausible)."""
    solar = order.endswith("+solar")
    aperture_ok = (not solar) or (aperture is not None and APERTURE_M2[0] <= aperture <= APERTURE_M2[1])
    c = CRITERIA["repair"]
    repair = bool(b_rmse is not None and b_rmse <= c["b_rmse_c"] and b_r is not None and b_r >= c["b_r"] and stable
                  and aperture_ok and not wide)
    sponge = bool(wide or (solar and not aperture_ok))
    return ("repair" if repair else "sponge" if sponge else "neither"), repair, sponge, aperture_ok


def _verdict(order: str, b_rmse, b_r, stable: bool, aperture, wide: bool) -> dict:
    verdict, repair, sponge, aperture_ok = _rule(order, b_rmse, b_r, stable, aperture, wide)
    # post hoc, a diagnostic only: what the rule would say without the interval-width condition
    without = _rule(order, b_rmse, b_r, stable, aperture, False)[0]
    return {"verdict": verdict, "repair": repair, "sponge": sponge, "aperture_plausible": aperture_ok,
            "parameters_stable": stable, "any_interval_spans_10x": wide,
            "verdict_without_width_post_hoc": without,
            "rests_on_width_alone": bool(wide and without != "sponge"),
            "neither_clear": bool(b_rmse is not None and b_rmse > CRITERIA["neither"]["b_rmse_c"] and not wide),
            "inconclusive_middle": bool(verdict == "neither" and b_rmse is not None
                                        and b_rmse <= CRITERIA["neither"]["b_rmse_c"])}


def _stable(ratios: dict) -> bool:
    """B's own refit within +-50 % of A's median, every parameter."""
    t = CRITERIA["repair"]["param_tolerance"]
    return all(1 - t <= x <= 1 + t for x in ratios.values())


def _outcome(orders, verdicts: dict, aic: dict) -> tuple[str, str]:
    """The ladder's outcome: the simplest order that repairs, else the order with the lowest summed AIC on A."""
    chosen = next((o for o in orders if verdicts[o] == "repair"), None) or min(orders, key=lambda o: aic[o])
    return chosen, verdicts[chosen]


def code_sha256() -> str:
    import hashlib
    with open(__file__, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def ladder_report(appliances: str, sml2010: str, cache, *, orders=ORDERS, workers: int = 2,
                  profile: bool = True, max_nfev: int | tuple = MAIN_MAX_NFEV) -> dict:
    """Addendum 3: every order fitted on each of dataset A's rooms, held out once on dataset B, the parameters'
    profile-likelihood intervals, and the outcome."""
    from concurrent.futures import ProcessPoolExecutor

    from vesper.env.site import parse_site
    a = load_appliances(appliances)
    site_a = parse_site({"city": "Chièvres, Belgium", "date": a.index[0].date().isoformat()})
    ghi_a = _era5(site_a, a.index, cache)["shortwave_radiation"]
    t_a = _values(a["T_out"])
    b = load_sml2010(sml2010)
    site_b = parse_site({"city": "Valencia, Spain", "date": b.index[0].date().isoformat()})
    ghi_b = _era5(site_b, b.index, cache)["shortwave_radiation"]
    t_b = _values(b["t_out"])
    tasks = [(o, col, t_a, ghi_a, _values(a[col]), profile, max_nfev) for o in orders for col in APPLIANCES_ROOMS]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            done = list(pool.map(_task, tasks))
    else:
        done = [_task(t) for t in tasks]
    refit = [(o, col, t_b, ghi_b, _values(b[col]), False, max_nfev) for o in orders for col in SML_ROOMS]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            refit_done = list(pool.map(_task, refit))
    else:
        refit_done = [_task(t) for t in refit]
    report = {"protocol": "docs/experiments/environment-validation.md, addendum 3", "area_m2": AREA_M2,
              "criteria": CRITERIA, "orders": {}}
    for order in orders:
        names = PARAMS[order]
        a_fits = {col: f for o, col, f in done if o == order}
        med = {n: _median([f.params[n] for f in a_fits.values()]) for n in names}
        ident = {}
        for n in names:
            lo, hi = _median([f.ci95[n][0] for f in a_fits.values()]), _median([f.ci95[n][1] for f in a_fits.values()])
            ident[n] = {"lo": float(lo), "hi": float(hi), "span": float(hi / lo), "wide": bool(hi / lo >= WIDE),
                        "rooms_wide": sum(f.ci95[n][1] / f.ci95[n][0] >= WIDE for f in a_fits.values()),
                        "rooms_open": sum(any(f.ci_open[n]) for f in a_fits.values()),
                        "rooms_bound_limited": sum(any(f.bound_limited[n]) for f in a_fits.values()),
                        "rooms_grid_limited": sum("grid" in f.ci_end[n] for f in a_fits.values())}
        held = from_per_m2(per_m2(med))  # A's parameters per m², handed to B's 16 m² room unchanged
        b_rows = []
        for col, label in SML_ROOMS.items():
            measured = _values(b[col])
            setpoint = _percentile([v for v in measured if v is not None and v == v], SETPOINT_PERCENTILE)
            t, g, m = _clean(t_b, ghi_b, measured)
            sim = simulate(order, held, t, g, None, t0=float(m[np.isfinite(m)][0]), setpoint=setpoint)
            keep = np.isfinite(m)
            keep[:SPIN_UP_H] = False
            ag = _agreement(sim[keep], m[keep])
            b_rows.append({"room": label, "column": col, "setpoint_c": round(setpoint, 2), **ag})
        b_fits = {col: f for o, col, f in refit_done if o == order}
        b_med = {n: _median([f.params[n] for f in b_fits.values()]) for n in names}
        ratio = {n: b_med[n] / med[n] for n in names}
        stable = _stable(ratio)
        b_rmse, b_r = _median([x["rmse"] for x in b_rows]), _median([x["r"] for x in b_rows])
        wide = any(v["wide"] for v in ident.values())
        verdict = _verdict(order, b_rmse, b_r, stable, med.get("ga"), wide)
        report["orders"][order] = {
            "A": {"rooms": {col: _row_of(f) | {"room": APPLIANCES_ROOMS[col]} for col, f in a_fits.items()},
                  "median_rmse": _median([f.rmse for f in a_fits.values()]),
                  "median_r": _median([f.r for f in a_fits.values()]), "median_params": med,
                  "per_m2": per_m2(med),
                  "n_eff": {"min": min(f.n_eff for f in a_fits.values()), "max": max(f.n_eff for f in a_fits.values()),
                            "rho_capped_rooms": sum(f.rho >= RHO_CAP for f in a_fits.values()),
                            "sse_ratio_threshold_max": max(math.exp(CHI2_95 / f.n_eff) for f in a_fits.values()),
                            "sse_ratio_threshold_min": min(math.exp(CHI2_95 / f.n_eff) for f in a_fits.values())}},
            "B": {"held_out": b_rows, "median_rmse": b_rmse, "median_r": b_r,
                  "refit": {col: _row_of(f) | {"room": SML_ROOMS[col]} for col, f in b_fits.items()},
                  "refit_median_params": b_med, "refit_over_A": {n: round(x, 3) for n, x in ratio.items()}},
            "identifiability": ident, "aic_A": round(sum(f.aic for f in a_fits.values()), 1), **verdict}
    verdicts = {o: report["orders"][o]["verdict"] for o in orders}
    aics = {o: report["orders"][o]["aic_A"] for o in orders}
    chosen, outcome = _outcome(orders, verdicts, aics)
    report["best_aic_order"] = min(orders, key=lambda o: aics[o])
    report["outcome_order"], report["outcome"] = chosen, outcome
    report["code_sha256"] = code_sha256()
    report["main_max_nfev"] = [max_nfev] if isinstance(max_nfev, int) else list(max_nfev)
    return report
