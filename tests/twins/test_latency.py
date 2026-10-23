import math
import random

import pytest

from vesper.twins.latency import PATHS, LatencyModel, Path, battery_powered
from vesper.twins.runtime import Runtime


def test_paths_carry_their_publication_or_are_marked_assumed():
    assert PATHS["local"].source.startswith("Zhan") and PATHS["sleepy_downlink"].source is None
    assert all(p.basis for p in PATHS.values())


def test_lognormal_paths_reproduce_the_published_summary():
    local, cloud, ifttt = PATHS["local"], PATHS["vendor_cloud"], PATHS["trigger_action_cloud"]
    assert local.median() == pytest.approx(0.1145) and local.quantile(0.95) == pytest.approx(0.152, abs=0.002)
    assert ifttt.quantile(0.25) == pytest.approx(58, abs=1) and ifttt.quantile(0.75) == pytest.approx(122, abs=1)
    mean = cloud.a * math.exp(cloud.b ** 2 / 2)  # moment-matched to 1.4 ± 0.3 s
    sd = mean * math.sqrt(math.exp(cloud.b ** 2) - 1)
    assert mean == pytest.approx(1.4, abs=0.01) and sd == pytest.approx(0.3, abs=0.01)


def test_samples_follow_the_distribution():
    rng = random.Random(1)
    xs = sorted(PATHS["local"].sample(rng) for _ in range(4000))
    assert xs[2000] == pytest.approx(0.1145, rel=0.03)
    assert PATHS["local"].cdf(PATHS["local"].quantile(0.3)) == pytest.approx(0.3)
    u = Path("u", "uniform", 0.0, 5.0, None, "test")
    assert u.median() == 2.5 and u.cdf(1.0) == 0.2 and u.quantile(0.2) == 1.0


def test_the_hook_subtracts_vespers_own_overhead_and_never_goes_negative(vds):
    rt = Runtime()
    twin = rt.add(vds["PLUG-1"])
    model = LatencyModel(overhead_s=0.1)
    draws = [model(twin, "state", "stimulus") for _ in range(500)]
    assert min(draws) >= 0 and sorted(draws)[250] == pytest.approx(0.1145 - 0.1, abs=0.01)


def test_commands_to_battery_devices_also_wait_for_the_next_poll(vds):
    rt = Runtime()
    door, plug = rt.add(vds["MCCGQ11LM"]), rt.add(vds["PLUG-1"])
    assert battery_powered(door) and not battery_powered(plug)
    model = LatencyModel()
    door_cmd = sorted(model(door, "contact", "platform") for _ in range(500))
    door_report = sorted(model(door, "contact", "stimulus") for _ in range(500))
    plug_cmd = sorted(model(plug, "state", "platform") for _ in range(500))
    assert door_cmd[250] > door_report[250] + 2.0 and plug_cmd[250] == pytest.approx(0.1145, abs=0.02)


def test_a_device_can_be_put_on_another_path(vds):
    rt = Runtime()
    plug = rt.add(vds["PLUG-1"])
    model = LatencyModel(path_of={plug.id: "vendor_cloud"})
    assert sorted(model(plug, "state", "stimulus") for _ in range(300))[150] == pytest.approx(1.37, rel=0.1)


def test_the_same_seed_gives_the_same_latencies(vds):
    def draws(seed):
        rt = Runtime(seed=seed, latency=LatencyModel())
        seen = []
        rt.subscribe(seen.append)
        plug = rt.add(vds["PLUG-1"]).id
        rt.platform_set(plug, "state", "ON")
        rt.clock.advance(10)
        return [e.observed_t - e.physical_t for e in seen]

    assert draws(3) == draws(3) != draws(4)
