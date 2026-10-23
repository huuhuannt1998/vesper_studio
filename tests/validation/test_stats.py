import random

import pytest

from vesper.twins.latency import PATHS
from vesper.validation.stats import AD_CRITICAL_05, anderson_darling, assess_path, bootstrap_median_ratio, coverage


def sample(path, n, seed, scale=1.0):
    rng = random.Random(seed)
    return [path.sample(rng) * scale for _ in range(n)]


def test_the_bootstrap_interval_of_the_median_ratio_brackets_one_for_the_same_distribution():
    lo, hi = bootstrap_median_ratio(sample(PATHS["local"], 200, 1), PATHS["local"].median(), n_boot=2000, seed=0)
    assert lo < 1 < hi and hi - lo < 0.2


def test_the_bootstrap_is_reproducible():
    xs = sample(PATHS["local"], 200, 2)
    assert bootstrap_median_ratio(xs, 0.1145, n_boot=500, seed=3) == bootstrap_median_ratio(xs, 0.1145, n_boot=500,
                                                                                            seed=3)


def test_anderson_darling_accepts_the_model_and_rejects_a_shifted_sample():
    path = PATHS["local"]
    assert anderson_darling(sample(path, 200, 4), path.cdf) < AD_CRITICAL_05
    assert anderson_darling(sample(path, 200, 4, scale=1.4), path.cdf) > AD_CRITICAL_05


def test_coverage_counts_samples_inside_the_published_band():
    assert coverage([1, 2, 3, 4, 10], 1.5, 4) == pytest.approx(0.6)


def test_a_path_is_equivalent_only_within_the_preregistered_margin():
    path = PATHS["local"]
    good = assess_path(sample(path, 200, 5), path, n_boot=2000, seed=0)
    assert good["T1_equivalent"] and good["T3_pass"] and good["passed"]
    slow = assess_path(sample(path, 200, 5, scale=1.3), path, n_boot=2000, seed=0)
    assert not slow["T1_equivalent"] and not slow["passed"]
    assert set(good) >= {"n", "median_s", "published_median_s", "ratio_ci90", "T2_ad", "T2_pass", "T3_coverage"}
