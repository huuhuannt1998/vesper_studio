"""The wall-clock latency measurement, through an in-memory broker (the live run uses Mosquitto).

Assertions on observed times are loose on purpose: at background QoS macOS fires timers up to ~0.2 s
late, which is exactly what the residual check measures in the live run.
"""
from statistics import median

import pytest

from tests.exposure.fake_mqtt import FakeBroker
from tests.twins.conftest import records
from vesper.twins.latency import Path
from vesper.validation.timing import BASE_TOPIC, measure_latency

FAST = Path("fast", "lognormal", 0.03, 0.2, "test", "test path")
OTHER = Path("other", "lognormal", 0.03, 0.2, "test", "same distribution, another path")
CONTACT = records()["MCCGQ11LM"]


def run(broker, path, n=30, seed=0, **kw):
    return measure_latency(CONTACT, broker.client(), broker.client(), n=n, path=path, overhead_s=0.0,
                           spacing_s=0.002, timeout_s=kw.get("timeout_s", 10), seed=seed)


def test_each_sample_carries_the_injected_and_the_observed_latency():
    samples = run(FakeBroker(), FAST)
    assert len(samples) == 30
    injected = [s["injected_s"] for s in samples]
    assert median(injected) == pytest.approx(0.03, rel=0.35)
    assert all(-0.005 < s["observed_s"] - s["injected_s"] < 0.5 for s in samples)


def test_each_path_draws_its_own_samples():
    a = [s["injected_s"] / 0.03 for s in run(FakeBroker(), FAST)]
    b = [s["injected_s"] / 0.03 for s in run(FakeBroker(), OTHER)]
    assert a != b  # same seed, same distribution, independent streams


def test_the_run_has_its_own_topics_and_leaves_nothing_retained():
    broker = FakeBroker()
    run(broker, FAST, n=5)
    assert all(t.startswith(f"{BASE_TOPIC}/") for t, _, _ in broker.published)
    assert broker.retained == {}


def test_a_lost_report_times_out_instead_of_hanging():
    broker = FakeBroker()
    observer = broker.client()
    observer.connected = False  # never receives anything
    with pytest.raises(TimeoutError, match="0 of 5"):
        measure_latency(CONTACT, broker.client(), observer, n=5, path=FAST, overhead_s=0.0, spacing_s=0.001,
                        timeout_s=0.3, seed=0)
