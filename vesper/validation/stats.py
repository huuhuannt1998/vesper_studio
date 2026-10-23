"""The pre-registered L3 latency checks (docs/experiments/l3-timing-design.md), standard library only.

Pass/fail:
- G (generator, offline): GENERATOR_N draws of each path reproduce its published median within
  GENERATOR_MEDIAN_TOL and its P5 and P95 within GENERATOR_QUANTILE_TOL, both as a fraction of the
  path's published median (its natural scale; a relative error at a quantile near 0 means nothing).
- P (pipeline, wall clock): per sample, residual = observed - injected latency, i.e. what VESPER's own
  pipeline and scheduling add; its median must be <= RESIDUAL_MEDIAN_MAX and its P95 <= RESIDUAL_P95_MAX.

Descriptive only (reported, never pass/fail): the end-to-end observed latency against the published
distribution: 90% bootstrap interval of the median ratio, Anderson-Darling (case 0), P5-P95 coverage.
"""
from __future__ import annotations

import math
import random
from statistics import median

N_SAMPLES = 200
N_BOOT = 2000
AD_CRITICAL_05 = 2.492  # case 0 (all parameters specified), alpha = 0.05
GENERATOR_N = 100000  # median error sd <= 0.33 of its tolerance for every path
GENERATOR_MEDIAN_TOL = 0.01
GENERATOR_QUANTILE_TOL = 0.02
RESIDUAL_MEDIAN_MAX = 0.020  # seconds
RESIDUAL_P95_MAX = 0.050  # seconds
CALIBRATION_DELAY = 0.05  # seconds: the constant injected delay that calibrates VESPER's own overhead


def _quantile(sorted_xs: list[float], q: float) -> float:
    """Linear interpolation between order statistics (Hyndman-Fan type 7)."""
    pos = (len(sorted_xs) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def bootstrap_median_ratio(xs: list[float], reference_median: float, *, n_boot: int = N_BOOT, seed: int = 0,
                           level: float = 0.90) -> tuple[float, float]:
    """Percentile interval with symmetric order statistics: k = round((1 - level) / 2 * n_boot)."""
    rng = random.Random(seed)
    n = len(xs)
    ratios = sorted(median(rng.choices(xs, k=n)) / reference_median for _ in range(n_boot))
    k = round((1 - level) / 2 * n_boot)
    return ratios[k], ratios[n_boot - 1 - k]


def anderson_darling(xs: list[float], cdf) -> float:
    """A-squared of the sample against a fully specified continuous distribution."""
    ys = sorted(xs)
    n = len(ys)
    tiny = 1e-12
    f = [min(max(cdf(y), tiny), 1 - tiny) for y in ys]
    s = sum((2 * i + 1) * (math.log(f[i]) + math.log(1 - f[n - 1 - i])) for i in range(n))
    return -n - s / n


def coverage(xs: list[float], lo: float, hi: float) -> float:
    return sum(lo <= x <= hi for x in xs) / len(xs)


def generator_check(path, *, n: int = GENERATOR_N, seed: int = 0) -> dict:
    rng = random.Random(seed)
    xs = sorted(path.sample(rng) for _ in range(n))
    rows = {}
    for label, q, tol in (("median", 0.5, GENERATOR_MEDIAN_TOL), ("p5", 0.05, GENERATOR_QUANTILE_TOL),
                          ("p95", 0.95, GENERATOR_QUANTILE_TOL)):
        got, want = _quantile(xs, q), path.quantile(q)
        rows[label] = {"sampled_s": got, "published_s": want, "error_vs_median": abs(got - want) / path.median(),
                       "tolerance": tol}
    return {"path": path.name, "n": n, "seed": seed, "quantiles": rows,
            "passed": all(r["error_vs_median"] <= r["tolerance"] for r in rows.values())}


def residual_check(residuals: list[float]) -> dict:
    xs = sorted(residuals)
    med, p95 = _quantile(xs, 0.5), _quantile(xs, 0.95)
    return {"n": len(xs), "median_s": med, "p95_s": p95, "max_s": xs[-1], "min_s": xs[0],
            "median_max_s": RESIDUAL_MEDIAN_MAX, "p95_max_s": RESIDUAL_P95_MAX,
            "passed": med <= RESIDUAL_MEDIAN_MAX and p95 <= RESIDUAL_P95_MAX}


def describe_path(xs: list[float], path, *, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    lo, hi = bootstrap_median_ratio(xs, path.median(), n_boot=n_boot, seed=seed)
    return {"path": path.name, "source": path.source, "n": len(xs), "median_s": median(xs),
            "published_median_s": path.median(), "ratio_ci90": [lo, hi], "ad": anderson_darling(xs, path.cdf),
            "coverage_p5_p95": coverage(xs, path.quantile(0.05), path.quantile(0.95))}
