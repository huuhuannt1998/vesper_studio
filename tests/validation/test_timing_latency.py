"""The wall-clock latency measurement, through an in-memory broker (the live run uses Mosquitto)."""
import pytest

from tests.exposure.fake_mqtt import FakeBroker
from tests.twins.conftest import records
from vesper.twins.latency import Path
from vesper.validation.timing import measure_latency

FAST = Path("fast", "lognormal", 0.03, 0.2, "test", "test path")
CONTACT = records()["MCCGQ11LM"]


def test_latency_is_measured_from_the_stimulus_to_the_subscribers_receipt():
    broker = FakeBroker()
    xs = measure_latency(CONTACT, broker.client(), broker.client(), n=30, path=FAST, overhead_s=0.0,
                         spacing_s=0.002, timeout_s=10, seed=0)
    assert len(xs) == 30 and min(xs) > 0
    assert sorted(xs)[15] == pytest.approx(0.03, rel=0.35)


def test_without_a_path_the_measurement_is_vespers_own_overhead():
    broker = FakeBroker()
    xs = measure_latency(CONTACT, broker.client(), broker.client(), n=20, path=None, overhead_s=0.0,
                         spacing_s=0.001, timeout_s=10, seed=0)
    assert len(xs) == 20 and max(xs) < 0.05  # in-memory delivery: no modelled latency at all


def test_a_lost_report_times_out_instead_of_hanging():
    broker = FakeBroker()
    observer = broker.client()
    observer.connected = False  # never receives anything
    with pytest.raises(TimeoutError, match="0 of 5"):
        measure_latency(CONTACT, broker.client(), observer, n=5, path=None, overhead_s=0.0, spacing_s=0.001,
                        timeout_s=0.3, seed=0)
