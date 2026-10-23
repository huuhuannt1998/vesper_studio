"""The pre-registered latency tests (docs/experiments/l3-timing-design.md), in the standard library only.

- T1: equivalence of medians (TOST at alpha 0.05): the 90% bootstrap interval of observed/published
  median lies within [1 - MARGIN, 1 + MARGIN].
- T2: Anderson-Darling against the fully specified published distribution (secondary; alpha 0.05).
- T3: at least COVERAGE_MIN of the observations fall inside the published P5-P95 band.
"""
from __future__ import annotations

import math
import random
from statistics import median

MARGIN = 0.10
N_SAMPLES = 200
N_BOOT = 2000
AD_CRITICAL_05 = 2.492  # case 0 (all parameters specified), alpha = 0.05
COVERAGE_MIN = 0.90


def bootstrap_median_ratio(xs: list[float], reference_median: float, *, n_boot: int = N_BOOT, seed: int = 0,
                           level: float = 0.90) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(xs)
    ratios = sorted(median(rng.choices(xs, k=n)) / reference_median for _ in range(n_boot))
    tail = (1 - level) / 2
    return ratios[int(math.floor(tail * n_boot))], ratios[int(math.ceil((1 - tail) * n_boot)) - 1]


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


def assess_path(xs: list[float], path, *, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    lo, hi = bootstrap_median_ratio(xs, path.median(), n_boot=n_boot, seed=seed)
    ad = anderson_darling(xs, path.cdf)
    cov = coverage(xs, path.quantile(0.05), path.quantile(0.95))
    t1 = lo >= 1 - MARGIN and hi <= 1 + MARGIN
    t3 = cov >= COVERAGE_MIN
    return {"path": path.name, "source": path.source, "n": len(xs), "median_s": median(xs),
            "published_median_s": path.median(), "ratio_ci90": [lo, hi], "T1_equivalent": t1,
            "T2_ad": ad, "T2_pass": ad < AD_CRITICAL_05, "T3_coverage": cov, "T3_pass": t3, "passed": t1 and t3}
