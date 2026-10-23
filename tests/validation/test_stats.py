import random

import pytest

from vesper.twins.latency import PATHS, Path
from vesper.validation.stats import (AD_CRITICAL_05, RESIDUAL_MEDIAN_MAX, RESIDUAL_P95_MAX, anderson_darling,
                                     bootstrap_median_ratio, coverage, describe_path, generator_check,
                                     residual_check)


def sample(path, n, seed, scale=1.0):
    rng = random.Random(seed)
    return [path.sample(rng) * scale for _ in range(n)]


def test_the_bootstrap_interval_of_the_median_ratio_brackets_one_for_the_same_distribution():
    lo, hi = bootstrap_median_ratio(sample(PATHS["local"], 200, 1), PATHS["local"].median(), n_boot=2000, seed=0)
    assert lo < 1 < hi and hi - lo < 0.2


def test_the_bootstrap_interval_uses_symmetric_order_statistics():
    xs = [float(i) for i in range(1, 102)]
    lo, hi = bootstrap_median_ratio(xs, 51.0, n_boot=2000, seed=0)
    assert lo < 1 < hi
    assert bootstrap_median_ratio(xs, 51.0, n_boot=2000, seed=0) == (lo, hi)  # reproducible


def test_anderson_darling_accepts_the_model_and_rejects_a_shifted_sample():
    path = PATHS["local"]
    assert anderson_darling(sample(path, 200, 4), path.cdf) < AD_CRITICAL_05
    assert anderson_darling(sample(path, 200, 4, scale=1.4), path.cdf) > AD_CRITICAL_05


def test_coverage_counts_samples_inside_the_published_band():
    assert coverage([1, 2, 3, 4, 10], 1.5, 4) == pytest.approx(0.6)


def test_every_paths_generator_reproduces_its_published_distribution():
    for name, path in PATHS.items():
        check = generator_check(path, seed=0)
        assert check["passed"], (name, check)


def test_a_wrong_generator_fails_its_check():
    class Biased(Path):
        def sample(self, rng):
            return super().sample(rng) * 1.05

    biased = Biased(*[getattr(PATHS["local"], f) for f in ("name", "kind", "a", "b", "source", "basis")])
    assert generator_check(biased, seed=0)["passed"] is False


def test_the_residual_check_bounds_what_the_pipeline_adds():
    assert residual_check([0.004] * 150 + [0.015] * 50)["passed"] is True
    assert residual_check([RESIDUAL_MEDIAN_MAX * 1.5] * 200)["passed"] is False
    assert residual_check([0.005] * 180 + [RESIDUAL_P95_MAX * 2] * 20)["passed"] is False


def test_describing_a_path_reports_without_passing_judgement():
    d = describe_path(sample(PATHS["local"], 200, 5), PATHS["local"], seed=0)
    assert set(d) >= {"n", "median_s", "published_median_s", "ratio_ci90", "ad", "coverage_p5_p95"}
    assert "passed" not in d
