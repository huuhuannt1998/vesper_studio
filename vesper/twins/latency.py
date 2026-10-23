"""Latency from a physical event, or a command, to the platform observing the report (spec §3.3; L3).

Each path is a distribution fitted to a publication. A path with no published measurement is
`assumed` (source None) and does not count toward L3 (spec §3.2). The published end-to-end figures
include the platform's own processing, which VESPER runs for real, so the hook subtracts VESPER's
measured in-silico overhead (design: docs/experiments/l3-timing-design.md).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from statistics import NormalDist

_N = NormalDist()


@dataclass(frozen=True)
class Path:
    name: str
    kind: str  # "lognormal" (a = median s, b = sigma) or "uniform" (a = low s, b = high s)
    a: float
    b: float
    source: str | None  # the publication; None means assumed
    basis: str  # what the source measured, and how the parameters were derived

    def sample(self, rng: random.Random) -> float:
        if self.kind == "lognormal":
            return rng.lognormvariate(math.log(self.a), self.b)
        return rng.uniform(self.a, self.b)

    def median(self) -> float:
        return self.a if self.kind == "lognormal" else (self.a + self.b) / 2

    def quantile(self, q: float) -> float:
        if self.kind == "lognormal":
            return self.a * math.exp(self.b * _N.inv_cdf(q))
        return self.a + (self.b - self.a) * q

    def cdf(self, x: float) -> float:
        if self.kind == "lognormal":
            return _N.cdf((math.log(x) - math.log(self.a)) / self.b) if x > 0 else 0.0
        if self.b == self.a:  # a constant delay
            return 1.0 if x >= self.a else 0.0
        return min(1.0, max(0.0, (x - self.a) / (self.b - self.a)))


PATHS = {
    "local": Path("local", "lognormal", 0.1145, 0.17, "Zhan, Li, Zhang, Haddadi, arXiv:2602.22525 (2026)",
                  "MQTT -> Home Assistant REST -> Hue -> confirmation, N=150: median 114.5 ms, P95 152 ms; "
                  "sigma = ln(152/114.5)/1.645. Borrowed: measured on an actuation round trip, applied here to "
                  "sensor reports as well, so for reports it counts as assumed"),
    "vendor_cloud": Path("vendor_cloud", "lognormal", 1.369, 0.212,
                         "Goksel et al., IEEE SafeThings 2021, Table III (from Lee, Mun, Lee, ICCE 2020)",
                         "device <-> vendor cloud 1.4 +/- 0.3 s; lognormal moment-matched to that mean and sd"),
    "trigger_action_cloud": Path("trigger_action_cloud", "lognormal", 84.0, 0.553,
                                 "Mi, Qian, Zhang, Wang, IMC 2017 (doi:10.1145/3131365.3131369)",
                                 "IFTTT trigger-to-action quartiles 58/84/122 s; sigma = ln(122/84)/0.6745"),
    "sleepy_downlink": Path("sleepy_downlink", "uniform", 0.0, 5.0, None,
                            "a command waits for the next poll of a sleepy end device; 5 s is Silicon Labs' "
                            "Poll Control default long-poll interval (a vendor default, so assumed)"),
}


def battery_powered(twin) -> bool:
    """Battery devices are Zigbee end devices that sleep between polls (as the bridge declares them)."""
    return "battery" in twin.features


@dataclass
class LatencyModel:
    """The runtime's latency hook, `(twin, feature, cause) -> seconds`, sampled from the twin's own
    seeded generator so logical-clock runs reproduce exactly."""

    path_of: dict[str, str] = field(default_factory=dict)  # twin id -> path name
    overhead_s: float = 0.0  # VESPER's own measured pipeline latency, already real
    default: str = "local"
    paths: dict[str, Path] = field(default_factory=lambda: dict(PATHS))

    def path(self, twin) -> Path:
        return self.paths[self.path_of.get(twin.id, self.default)]

    def __call__(self, twin, feature: str, cause: str = "stimulus") -> float:
        delay = max(0.0, self.path(twin).sample(twin.rng) - self.overhead_s)
        if cause == "platform" and battery_powered(twin):
            delay += self.paths.get("sleepy_downlink", PATHS["sleepy_downlink"]).sample(twin.rng)
        return delay
