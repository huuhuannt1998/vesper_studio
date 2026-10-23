# Plan E: L3 timing fidelity and the fidelity report — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make L3 measurable, and give researchers one citable fidelity report. The pieces:
- latency models, each fitted to a publication or marked `assumed`;
- an exact check that every twin obeys its VDS reporting rules;
- a wall-clock latency measurement at the MQTT boundary, with tests fixed in advance;
- `vesper report`.

**Architecture:**
1. `vesper/twins/latency.py`: `Path` distributions with their sources, and `LatencyModel`, the runtime's `(twin, feature, cause)` hook. The hook subtracts VESPER's measured overhead and adds the poll wait for commands to battery devices.
2. `vesper/validation/timing.py`:
   - `RuleChecker` and `run_rules` (R1–R4, logical clock, seeded scripts);
   - `measure_latency`: n contact-sensor twins, stimulus to subscriber receipt.
3. `vesper/validation/stats.py`: T1 (bootstrap TOST on the median ratio), T2 (Anderson–Darling, case 0) and T3 (P5–P95 coverage), all in the standard library.
4. `vesper/validation/report.py`: catalog plus the latest result of every check, as Markdown and JSON.
5. CLI: `vesper validate rules|latency` and `vesper report`.
6. Two runtime fidelity bugs found by the rule check are fixed first (Task 1).

**Tech Stack:** Python ≥3.11, standard library only: `random`, `statistics.NormalDist`, `math`. Mosquitto only for the live latency run.

**Spec:** `docs/superpowers/specs/2026-10-22-vesper-device-twins-design.md`: §1 criterion 4; §3.3 (Reporting, Latency, Determinism, Output events); §4 (row L3, and the fidelity report); §3.2 (an assumed field does not count toward coverage). Design and pre-registration: `docs/experiments/l3-timing-design.md` (Task 7). Research sources are in RKA `jrn_01M5KBZ350H65H30BD5EC1T7VF`.

## Global Constraints

- CPU only, ≤2 cores, no Docker (spec §1 criterion 6). No new third-party dependency: the statistics use the standard library.
- Determinism (spec §3.3): logical-clock runs reproduce exactly from the seed, and the latency hook samples from each twin's own seeded generator.
- Nothing fails silently (spec §5):
  - a check that never ran is reported as "not run yet";
  - an assumed path is labelled as such;
  - L2 is credited only to products that passed acceptance.
- Shared Mac:
  - this plan's tasks run only unit tests;
  - the live `vesper validate latency` run (Mosquitto) and the full-catalog `vesper validate rules` run happen in VESPER's approved block after another project's experiment, or later with the PI's go.
- The pre-registration (`docs/experiments/l3-timing-design.md`) is committed before any L3 run, and its numbers are not changed afterwards without a new commit that is disclosed.
- Lint gate: `.venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **Floating-point timer edges.** A report at a fractional time `t` gives `(t + 3600) − t = 3599.9999999999995`, and the periodic tick then rescheduled itself at the same instant forever. The tick now compares with the same sum it was scheduled at. Pinned by `test_a_periodic_report_after_a_fractional_report_time_does_not_spin` (Task 1).
2. **A deferred report whose value drifted back** below the reportable change must be dropped, not sent. Pinned by `test_a_deferred_report_is_dropped_if_the_value_drifted_back_below_the_change` (Task 1).
3. **A latency run whose reports never arrive** (a broker or subscriber failure) must fail within a bound, not hang. The timeout is 60 s plus the path's P99.9. Pinned by `test_a_lost_report_times_out_instead_of_hanging` (Task 4).
4. **Concurrent reports in the latency run** must be matched to the right stimulus, by topic and by the opened value. Pinned by `test_latency_is_measured_from_the_stimulus_to_the_subscribers_receipt` (30 concurrent twins, Task 4).
5. **The report must never upgrade evidence:** a check not run shows as not run, and L2 counts only products that passed. Pinned by `test_l2_counts_only_products_that_passed_acceptance` and `test_the_markdown_names_assumptions_and_missing_checks` (Task 5).

## Rulings carried into this plan

- The latency hook gains a `cause` argument (`(twin, feature, cause)`), because the poll wait applies to commands, not to physical reports. Plan C's default hook is None, so only two test lambdas change. Cost if wrong: one signature change.
- The latency boundary is the MQTT subscriber, as spec §4 says ("from the broker log"). A Home Assistant-side boundary is future work. Cost if wrong: the HA-processing share of `local` stays inside the subtracted overhead's model.
- The zigbee L2 reason no longer says "not built yet (Plan C)". L2 is credited from acceptance evidence by `vesper report`, and the catalog build stays source-only.

---

### Task 1: Two runtime fidelity fixes the rule check exposed

**Files:**
- Modify: `vesper/twins/runtime.py` (`_change`, `_flush`, `_schedule_periodic`)
- Modify: `tests/twins/test_runtime.py` (append two tests)

**Interfaces:**
- Produces: a deferred (`min_s`) report is sent only if the value still differs from the last report by at least `change`; the periodic tick reports at exactly `last_t + max_s`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/twins/test_runtime.py`:
```python


def test_a_deferred_report_is_dropped_if_the_value_drifted_back_below_the_change(vds):
    rt, events, (th,) = runtime(vds["TH-1"])
    rt.clock.advance(60)
    rt.stimulate(th, {"type": "ambient", "temperature": 20.0})  # reported at once (last report long ago)
    rt.clock.advance(1)
    rt.stimulate(th, {"type": "ambient", "temperature": 22.0})  # significant, but min_s 10: deferred
    rt.clock.advance(1)
    rt.stimulate(th, {"type": "ambient", "temperature": 20.4})  # back within 1.0 of the last report
    rt.clock.advance(20)
    assert [(e.feature, e.value) for e in events if e.feature == "temperature"] == [("temperature", 20.0)]


def test_a_periodic_report_after_a_fractional_report_time_does_not_spin(vds):
    """(a + max_s) - a can round below max_s; the periodic tick must still report, not reschedule forever."""
    import threading

    rt, events, (plug,) = runtime(vds["PLUG-1"])
    t = 851.518787493795  # found in a rule run: (t + 3600) - t == 3599.9999999999995
    rt.clock.advance_to(t)
    rt.platform_set(plug, "state", "ON")
    done = threading.Event()
    worker = threading.Thread(target=lambda: (rt.clock.advance_to(t + 3700), done.set()), daemon=True)
    worker.start()
    assert done.wait(5), "the periodic tick spins at one instant"
    assert [e.cause for e in events if e.feature == "state"] == ["platform", "periodic"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_runtime.py -q`

Expected: `2 failed, 15 passed`. The drift test reports `('temperature', 20.4)` too many. The fractional test fails with `the periodic tick spins at one instant` (its worker thread keeps spinning until pytest exits; that is expected).

- [ ] **Step 3: Fix the runtime**

In `vesper/twins/runtime.py`, replace:
```python
        if rep.last_t is None or now - rep.last_t >= rule["min_s"]:
```
with:
```python
        if rep.last_t is None or now >= rep.last_t + rule["min_s"]:
```

In `vesper/twins/runtime.py`, replace:
```python
            if rep.pending and twin.id not in self.faulted:
                rep.pending = False
                self._report(twin, name, rep.changed_at, rep.cause)
```
with:
```python
            if rep.pending and twin.id not in self.faulted:
                rep.pending = False
                # the value may have drifted back meanwhile: a deferred report still needs a reportable change
                if self._significant(rep.last_value, twin.state[name], self._rule(twin, name)["change"]):
                    self._report(twin, name, rep.changed_at, rep.cause)
```

In `vesper/twins/runtime.py`, replace:
```python
                if rep.last_t is not None and self.clock.now() - rep.last_t >= rule["max_s"]:
```
with:
```python
                # compare with the same sum the tick was scheduled at: now - last_t can round below max_s
                if rep.last_t is not None and self.clock.now() >= rep.last_t + rule["max_s"]:
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins -q`

Expected: `31 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/runtime.py tests/twins/test_runtime.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "fix(twins): drop a deferred report that drifted back; exact periodic tick

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 2: Latency models with their sources

**Files:**
- Create: `vesper/twins/latency.py`, `tests/twins/test_latency.py`
- Modify: `vesper/twins/runtime.py` (the hook takes the event's cause), `tests/twins/test_runtime.py` (two test hooks)

**Interfaces:**
- Consumes: Task 1's runtime.
- Produces:
  - `latency.Path(name, kind, a, b, source, basis)`, with `.sample(rng)`, `.median()`, `.quantile(q)` and `.cdf(x)`;
  - `latency.PATHS`: `local`, `vendor_cloud`, `trigger_action_cloud`, `sleepy_downlink`;
  - `battery_powered(twin)`;
  - `LatencyModel(path_of={}, overhead_s=0.0, default="local", paths=PATHS)`, callable as `(twin, feature, cause) -> seconds`;
  - `Runtime.latency` is now `(twin, feature, cause) -> seconds`.

- [ ] **Step 1: Write the failing tests**

Create `tests/twins/test_latency.py`:
```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/twins/test_latency.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.twins.latency'`.

- [ ] **Step 3: Implement, and pass the event's cause to the hook**

Create `vesper/twins/latency.py`:
```python
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
        return min(1.0, max(0.0, (x - self.a) / (self.b - self.a)))


PATHS = {
    "local": Path("local", "lognormal", 0.1145, 0.17, "Zhan, Li, Zhang, Haddadi, arXiv:2602.22525 (2026)",
                  "MQTT -> Home Assistant REST -> Hue -> confirmation, N=150: median 114.5 ms, P95 152 ms; "
                  "sigma = ln(152/114.5)/1.645"),
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
```

In `vesper/twins/runtime.py`, replace:
```python
    latency: Callable[[Twin, str], float] | None = None  # seconds from report to observation; default 0
```
with:
```python
    latency: Callable[[Twin, str, str], float] | None = None  # (twin, feature, cause) -> seconds; default 0
```

In `vesper/twins/runtime.py`, replace:
```python
            delay = max(0.0, self.latency(twin, name)) if self.latency else 0.0
```
with:
```python
            delay = max(0.0, self.latency(twin, name, cause)) if self.latency else 0.0
```

In `tests/twins/test_runtime.py`, replace:
```python
latency=lambda twin, feature: 0.25)
```
with:
```python
latency=lambda twin, feature, cause: 0.25)
```

In `tests/twins/test_runtime.py`, replace:
```python
    def latency(twin, feature):
```
with:
```python
    def latency(twin, feature, cause):
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/twins -q`

Expected: `38 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/twins/latency.py tests/twins/test_latency.py vesper/twins/runtime.py tests/twins/test_runtime.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(twins): latency models fitted to publications, or marked assumed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 3: The pre-registered statistics

**Files:**
- Create: `vesper/validation/stats.py`, `tests/validation/test_stats.py`

**Interfaces:**
- Consumes: Task 2's `PATHS` (tests only).
- Produces:
  - `stats.MARGIN = 0.10`, `N_SAMPLES = 200`, `N_BOOT = 2000`, `AD_CRITICAL_05 = 2.492`, `COVERAGE_MIN = 0.90`;
  - `bootstrap_median_ratio(xs, reference_median, *, n_boot, seed, level=0.90) -> (lo, hi)`;
  - `anderson_darling(xs, cdf) -> float`;
  - `coverage(xs, lo, hi) -> float`;
  - `assess_path(xs, path, *, n_boot, seed) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_stats.py`:
```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_stats.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.validation.stats'`.

- [ ] **Step 3: Implement**

Create `vesper/validation/stats.py`:
```python
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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/validation/test_stats.py -q`

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/stats.py tests/validation/test_stats.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): pre-registered latency statistics in the standard library

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 4: Rule conformance and the latency measurement

**Files:**
- Create: `vesper/validation/timing.py`, `tests/validation/test_timing_rules.py`, `tests/validation/test_timing_latency.py`

**Interfaces:**
- Consumes:
  - Tasks 1–2;
  - Plan C's `Bridge`, `Placement`, `WallClock` and `LogicalClock`;
  - the test double `FakeBroker`.
- Produces:
  - `timing.RuleChecker(end_t, rules, initial, added, presence_values)`, with `.on_event(e)`, `.on_presence(twin_id, t)` and `.finish() -> {reports, violations}`;
  - `run_rules(records, *, seed, duration_s) -> {products, features_checked, reports, violations, violation_count, faulted, passed, seed, duration_s}`;
  - `measure_latency(record, bridge_client, observer, *, n, path, overhead_s, spacing_s, timeout_s, seed) -> list[float]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_timing_rules.py`:
```python
from tests.twins.conftest import records
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Event
from vesper.validation.timing import RuleChecker, run_rules


def checker():
    rules = {("t", "temperature"): {"min_s": 10, "max_s": 100, "change": 1.0},
             ("t", "occupancy"): {"min_s": 0, "max_s": 3600, "change": 0}}
    return RuleChecker(end_t=150.0, rules=rules, initial={("t", "temperature"): 20.0, ("t", "occupancy"): False},
                       added={"t": 0.0}, presence_values={("t", "occupancy"): (True, False)})


def ev(t, feature, value, cause="stimulus"):
    return Event("t", feature, value, t, t, cause)


def test_reports_that_obey_their_rules_pass():
    c = checker()
    for e in (ev(5, "temperature", 21.5), ev(20, "temperature", 23.0), ev(120, "temperature", 23.0, "periodic")):
        c.on_event(e)
    c.on_presence("t", 30.0)
    c.on_event(ev(30, "occupancy", True))
    c.on_event(ev(30 + OCCUPANCY_TIMEOUT_S, "occupancy", False))
    assert c.finish()["violations"] == []


def test_each_kind_of_violation_is_named():
    c = checker()
    c.on_event(ev(5, "temperature", 21.5))
    c.on_event(ev(8, "temperature", 23.0))  # R1: 3 s after the last report, min_s 10
    c.on_event(ev(30, "temperature", 23.4))  # R3: a change of 0.4 < 1.0
    c.on_event(ev(140, "temperature", 25.0))  # R2: 110 s of silence > max_s 100
    c.on_presence("t", 20.0)
    c.on_event(ev(20, "occupancy", True))
    c.on_event(ev(50, "occupancy", False))  # R4: cleared 30 s after presence, not 90 s
    kinds = [v["rule"] for v in c.finish()["violations"]]
    assert kinds == ["R1 min_s", "R3 change", "R2 max_s", "R4 occupancy timeout"]


def test_the_end_of_the_run_counts_as_silence():
    c = checker()
    c.on_event(ev(10, "temperature", 21.5))
    assert [v["rule"] for v in c.finish()["violations"]] == ["R2 max_s"]  # 140 s to the end > 100 s


def test_every_fixture_twin_obeys_its_rules_under_a_seeded_script():
    vds = records()
    report = run_rules(list(vds.values()), seed=0, duration_s=4 * 3600)
    assert report["products"] == len([r for r in vds.values() if r["fidelity"]["reached"]["L1"]])
    assert report["reports"] > 500 and report["features_checked"] > 10
    assert report["violations"] == [] and report["passed"] is True


def test_the_rule_run_is_reproducible():
    a = run_rules(list(records().values()), seed=1, duration_s=3600)
    b = run_rules(list(records().values()), seed=1, duration_s=3600)
    assert a == b and a["reports"] > 0
```

Create `tests/validation/test_timing_latency.py`:
```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_timing_rules.py tests/validation/test_timing_latency.py -q`

Expected: collection errors, `ModuleNotFoundError: No module named 'vesper.validation.timing'`.

- [ ] **Step 3: Implement**

Create `vesper/validation/timing.py`:
```python
"""L3 timing fidelity (spec §1 criterion 4, §4 row L3). Design and pre-registered tolerances:
docs/experiments/l3-timing-design.md.

Latency (T1-T3) is measured in wall-clock mode at the MQTT boundary: from a physical stimulus to a
separate subscriber receiving the twin's report, through a real broker in the live run.

Rule conformance (R1-R4) runs every twin on the logical clock under a seeded stimulus and command
script and checks each report against the feature's VDS reporting rule, exactly:
- R1: at least `min_s` between consecutive reports;
- R2: at most `max_s` of silence, the end of the run included;
- R3: a non-periodic report changes the value by at least `change` since the last report;
- R4: occupancy clears exactly OCCUPANCY_TIMEOUT_S after the last presence.
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field

from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock, WallClock
from vesper.twins.latency import LatencyModel, Path
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Event, Runtime
from vesper.twins.twin import stable_seed

EPS = 1e-9
PRESENCE = ("occupancy", "presence")


def _significant(old, new, change) -> bool:
    if isinstance(new, (int, float)) and not isinstance(new, bool) and isinstance(old, (int, float)):
        return abs(new - old) >= change - EPS if change else new != old
    return new != old


@dataclass
class RuleChecker:
    end_t: float
    rules: dict[tuple[str, str], dict]
    initial: dict[tuple[str, str], object]
    added: dict[str, float]
    presence_values: dict[tuple[str, str], tuple] = field(default_factory=dict)  # (value_on, value_off)
    violations: list[dict] = field(default_factory=list)
    reports: int = 0

    def __post_init__(self):
        self._last: dict[tuple[str, str], tuple[float, object]] = {}
        self._presence: dict[str, float] = {}

    def on_presence(self, twin_id: str, t: float) -> None:
        self._presence[twin_id] = t

    def _flag(self, rule: str, key, t: float, detail: str) -> None:
        self.violations.append({"rule": rule, "twin": key[0], "feature": key[1], "t": t, "detail": detail})

    def on_event(self, e: Event) -> None:
        key = (e.twin_id, e.feature)
        rule = self.rules.get(key)
        if rule is None:
            return
        self.reports += 1
        t = e.observed_t
        prev_t, prev_value = self._last.get(key, (None, self.initial.get(key)))
        since = self.added.get(e.twin_id, 0.0) if prev_t is None else prev_t
        if prev_t is not None and t - prev_t < rule["min_s"] - EPS:
            self._flag("R1 min_s", key, t, f"{t - prev_t:.3f} s after the last report; min_s {rule['min_s']}")
        if e.cause != "periodic" and not _significant(prev_value, e.value, rule["change"]):
            self._flag("R3 change", key, t, f"{prev_value!r} -> {e.value!r}; change {rule['change']}")
        if t - since > rule["max_s"] + EPS:
            self._flag("R2 max_s", key, t, f"{t - since:.3f} s of silence; max_s {rule['max_s']}")
        values = self.presence_values.get(key)
        if values and e.cause != "periodic" and e.value == values[1] and prev_value == values[0]:
            shown = self._presence.get(e.twin_id)
            if shown is None or abs(t - shown - OCCUPANCY_TIMEOUT_S) > EPS:
                self._flag("R4 occupancy timeout", key, t,
                           f"cleared {t - shown if shown is not None else float('nan'):.3f} s after the last "
                           f"presence; expected {OCCUPANCY_TIMEOUT_S}")
        self._last[key] = (t, e.value)

    def finish(self) -> dict:
        for key, rule in self.rules.items():
            last_t = self._last.get(key, (self.added.get(key[0], 0.0), None))[0]
            if self.end_t - last_t > rule["max_s"] + EPS:
                self._flag("R2 max_s", key, self.end_t, f"{self.end_t - last_t:.3f} s of silence at the end; "
                                                        f"max_s {rule['max_s']}")
        return {"reports": self.reports, "violations": self.violations}


def _script(rt: Runtime, twin, rng: random.Random, duration_s: float, checker: RuleChecker) -> None:
    """Schedule a seeded mix of physical stimuli and platform commands for one twin."""
    feats = twin.features
    actions = []
    if any(n in feats for n in ("temperature", "humidity", "illuminance", "illuminance_lux", "local_temperature")):
        actions.append("ambient")
    if any(n in feats and feats[n]["type"] == "binary" for n in PRESENCE):
        actions.append("presence")
    if "contact" in feats:
        actions.append("contact")
    if "power" in feats:
        actions.append("load")
    if "action" in feats and feats["action"]["type"] == "enum" and feats["action"].get("values"):
        actions.append("button_press")
    settable = [n for n, f in feats.items() if "set" in f["access"] and f["type"] in ("binary", "numeric", "enum")
                and "#" not in n]
    if settable:
        actions.append("command")
    if not actions:
        return
    t = 0.0
    while True:
        t += rng.expovariate(1 / 120.0)
        if t >= duration_s:
            return
        kind = rng.choice(actions)
        rt.clock.call_at(t, _action(rt, twin, kind, rng.random(), settable, checker))


def _action(rt: Runtime, twin, kind: str, u: float, settable: list[str], checker: RuleChecker):
    def run():
        state = rt.state(twin.id)
        if kind == "ambient":
            s = {"type": "ambient"}
            for key, names, step in (("temperature", ("temperature", "local_temperature"), 3.0),
                                     ("humidity", ("humidity",), 8.0), ("illuminance", ("illuminance",
                                                                                       "illuminance_lux"), 400.0)):
                name = next((n for n in names if n in twin.features), None)
                if name is not None:
                    s[key] = float(state[name]) + (u - 0.5) * step
            rt.stimulate(twin.id, s)
        elif kind == "presence":
            checker.on_presence(twin.id, rt.clock.now())
            rt.stimulate(twin.id, {"type": "presence"})
        elif kind == "contact":
            rt.stimulate(twin.id, {"type": "contact", "state": "opened" if u < 0.5 else "closed"})
        elif kind == "load":
            rt.stimulate(twin.id, {"type": "load", "watts": round(u * 2000, 1)})
        elif kind == "button_press":
            values = twin.features["action"]["values"]
            rt.stimulate(twin.id, {"type": "button_press", "action": values[int(u * len(values)) % len(values)]})
        else:
            name = settable[int(u * len(settable)) % len(settable)]
            f = twin.features[name]
            if f["type"] == "binary":
                value = f["value_on"] if u < 0.5 else f["value_off"]
            elif f["type"] == "enum":
                value = f["values"][int(u * 997) % len(f["values"])] if f.get("values") else None
            else:
                lo, hi = f.get("range", (0, 100))
                value = lo + (hi - lo) * u
                value = round(value) if isinstance(lo, int) and isinstance(hi, int) else value
            if value is not None:
                rt.platform_set(twin.id, name, value)
    return run


def run_rules(records: list[dict], *, seed: int, duration_s: float) -> dict:
    """R1-R4 over every L1 twin with reporting rules, on the logical clock; exact, so the pass bar is 100%."""
    rt = Runtime(clock=LogicalClock(), seed=seed)
    twins = [rt.add(r) for r in sorted(records, key=lambda r: r["id"]) if r["fidelity"]["reached"]["L1"]]
    rules, initial, presence = {}, {}, {}
    for twin in twins:
        for name, rule in twin.vds.get("behavior", {}).get("reporting", {}).items():
            if name in twin.features:
                rules[(twin.id, name)] = rule
                initial[(twin.id, name)] = twin.state[name]
                if name in PRESENCE and twin.features[name]["type"] == "binary":
                    presence[(twin.id, name)] = (twin.features[name]["value_on"], twin.features[name]["value_off"])
    checker = RuleChecker(end_t=duration_s, rules=rules, initial=initial, added={t.id: 0.0 for t in twins},
                          presence_values=presence)
    rt.subscribe(checker.on_event)
    for twin in twins:
        _script(rt, twin, random.Random(stable_seed(seed, twin.id, "rules")), duration_s, checker)
    rt.clock.advance_to(duration_s)
    result = checker.finish()
    return {"products": len(twins), "features_checked": len(rules), "reports": result["reports"],
            "violations": result["violations"][:200], "violation_count": len(result["violations"]),
            "faulted": dict(rt.faulted), "passed": not result["violations"] and not rt.faulted,
            "seed": seed, "duration_s": duration_s}


def measure_latency(record: dict, bridge_client, observer, *, n: int, path: Path | None, overhead_s: float,
                    spacing_s: float, timeout_s: float, seed: int) -> list[float]:
    """Seconds from opening each of n contact-sensor twins of `record` to `observer` receiving the report.

    Contact has no reporting rule, so each report leaves at once and only the latency model (or, with no
    path, VESPER's own pipeline: runtime, bridge, broker, client) separates stimulus and receipt.
    """
    clock = WallClock()
    latency = None if path is None else LatencyModel(default=path.name, paths={path.name: path},
                                                     overhead_s=overhead_s)
    rt = Runtime(clock=clock, seed=seed, latency=latency)
    placements = [Placement(rt.add(record, twin_id=f"{record['id']}@lat{i:04d}").id, f"vesper_lat_{i:04d}")
                  for i in range(n)]
    bridge = Bridge(rt, placements, bridge_client, discovery=False)
    opened = rt.twins[placements[0].twin_id].features["contact"]["value_on"]
    topics = {f"{bridge.base_topic}/{p.friendly_name}": p.twin_id for p in placements}
    sent: dict[str, float] = {}
    got: dict[str, float] = {}
    done = threading.Event()

    def on_message(client, userdata, msg):
        received = time.monotonic()
        twin_id = topics.get(msg.topic)
        if twin_id in sent and twin_id not in got and msg.payload:
            if json.loads(msg.payload).get("contact") == opened:
                got[twin_id] = received
                if len(got) == n:
                    done.set()

    observer.on_message = on_message
    observer.subscribe(f"{bridge.base_topic}/+")
    try:
        bridge.start()
        for p in placements:
            sent[p.twin_id] = time.monotonic()
            rt.stimulate(p.twin_id, {"type": "contact", "state": "opened"})
            time.sleep(spacing_s)
        if not done.wait(timeout_s):
            raise TimeoutError(f"{len(got)} of {n} reports arrived within {timeout_s:.0f} s")
    finally:
        bridge.retract()
        bridge.flush()
        clock.close()
    return [got[t] - sent[t] for t in sorted(got)]
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/validation/test_timing_rules.py tests/validation/test_timing_latency.py -q`

Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/timing.py tests/validation/test_timing_rules.py tests/validation/test_timing_latency.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): exact reporting-rule check and wall-clock latency measurement

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 5: The fidelity report, and L2 left to evidence

**Files:**
- Create: `vesper/validation/report.py`, `tests/validation/test_report.py`
- Modify: `vesper/catalog/normalize/zigbee.py` (`L2_REASON`), `tests/catalog/test_zigbee_normalize.py` (append one test)

**Interfaces:**
- Consumes: Task 2's `PATHS`; Plan C's `device_discovery`; the catalog files `coverage.json`, `build_report.json` and `<protocol>.json`; result files `results/device-twins/<kind>-<UTC>.json`.
- Produces:
  - `report.KINDS`;
  - `latest(results_dir, kind) -> (file name, data) | None`;
  - `build_report(catalog_dir, results_dir) -> dict`;
  - `render_markdown(report) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/validation/test_report.py`:
```python
import json

from tests.twins.conftest import records
from vesper.validation.report import build_report, latest, render_markdown


def write_catalog(tmp_path):
    out = tmp_path / "vds"
    out.mkdir()
    recs = list(records().values())
    (out / "zigbee.json").write_text(json.dumps(recs))
    l1 = sum(r["fidelity"]["reached"]["L1"] for r in recs)
    (out / "coverage.json").write_text(json.dumps({"zigbee": {"L0": len(recs), "L1": l1, "L2": 0, "L3": 0, "L4": 0,
                                                              "by_category": {}}}))
    (out / "build_report.json").write_text(json.dumps({"inputs": {"zigbee": {"version": "26.123.0",
                                                                             "manifest_sha256": "ab" * 32}}}))
    return out, recs


def write_result(results, name, payload):
    results.mkdir(parents=True, exist_ok=True)
    (results / name).write_text(json.dumps(payload))


def test_the_latest_result_of_each_kind_is_used(tmp_path):
    r = tmp_path / "results"
    write_result(r, "rules-20261024T100000Z.json", {"passed": False})
    write_result(r, "rules-20261024T120000Z.json", {"passed": True})
    name, data = latest(r, "rules")
    assert name == "rules-20261024T120000Z.json" and data == {"passed": True}
    assert latest(r, "acceptance") is None


def test_l2_counts_only_products_that_passed_acceptance(tmp_path):
    catalog, recs = write_catalog(tmp_path)
    results = tmp_path / "results"
    write_result(results, "acceptance-20261024T170000Z.json", {
        "summary": {"products": 2, "passed": 1, "categories_passing": ["plug"], "categories_displayed": ["plug"],
                    "by_category": {}},
        "products": [{"id": recs[0]["id"], "category": "plug", "passed": True},
                     {"id": recs[1]["id"], "category": "light", "passed": False}]})
    report = build_report(catalog, results)
    assert report["l2"]["validated_products"] == [recs[0]["id"]]
    assert report["l2"]["eligible_zigbee"] >= 1 and report["sources"]["zigbee"]["version"] == "26.123.0"
    assert report["checks"]["acceptance"]["file"] == "acceptance-20261024T170000Z.json"
    assert report["checks"]["roundtrip"] is None  # not run yet: shown as missing, not as passed


def test_the_markdown_names_assumptions_and_missing_checks(tmp_path):
    catalog, _ = write_catalog(tmp_path)
    md = render_markdown(build_report(catalog, tmp_path / "results"))
    for heading in ("## Sources", "## Coverage", "## Assumed", "## Checks", "## Timing paths"):
        assert heading in md
    assert "not run yet" in md and "behavior.reporting.state" in md and "sleepy_downlink" in md
```

In `tests/catalog/test_zigbee_normalize.py`, replace:
```python
    assert reporting["illuminance"]["change"] == 0
```
with:
```python
    assert reporting["illuminance"]["change"] == 0


def test_l2_is_left_to_validation_evidence():
    rec = zigbee_records({"version": "26.123.0", "definitions": [zdef("TH-1", "Acme", [
        {"name": "temperature", "access": 5, "type": "numeric", "property": "temperature", "unit": "°C"}])]})[0]
    assert rec["fidelity"]["reached"]["L2"] is False
    assert "vesper validate acceptance" in rec["fidelity"]["reasons"]["L2"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_report.py -q`

Expected: collection error, `ModuleNotFoundError: No module named 'vesper.validation.report'`.

Run: `.venv/bin/python -m pytest tests/catalog/test_zigbee_normalize.py -q`

Expected: `1 failed, 15 passed`: `assert 'vesper validate acceptance' in 'exposure tier not built yet (Plan C)'`.

- [ ] **Step 3: Implement**

Create `vesper/validation/report.py`:
```python
"""The fidelity report (spec §4): what a researcher cites about how faithful their virtual devices are.

It combines the catalog (sources, coverage per level and category, every `assumed` field) with the latest
result of every check under results/. A check that has not run is shown as not run, never as passed, and
L2 is credited only to products that passed acceptance.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from vesper.exposure.ha_discovery import device_discovery
from vesper.twins.latency import PATHS

KINDS = ("discovery", "roundtrip", "acceptance", "rules", "latency")


def latest(results_dir: Path, kind: str) -> tuple[str, dict] | None:
    files = sorted(Path(results_dir).glob(f"{kind}-*.json"))
    return (files[-1].name, json.loads(files[-1].read_text())) if files else None


def _records(catalog_dir: Path) -> list[dict]:
    out = []
    for name in ("zigbee", "matter", "zwave"):
        path = Path(catalog_dir) / f"{name}.json"
        if path.is_file():
            out.extend(json.loads(path.read_text()))
    return out


def _summary(kind: str, data: dict) -> dict:
    if kind == "acceptance":
        return {"passed": data["summary"]["passed"] == data["summary"]["products"], **{
            k: data["summary"][k] for k in ("products", "passed", "categories_passing", "categories_displayed")}}
    if kind == "roundtrip":
        return {"passed": bool(data.get("complete")) and not data.get("different"),
                **{k: data.get(k) for k in ("checked", "identical", "l1_with_diffs", "missing", "duplicates")}}
    if kind == "discovery":
        s = data["summary"]
        return {"passed": s["accepted"] == s["components"], **s}
    if kind == "rules":
        return {k: data.get(k) for k in ("passed", "products", "features_checked", "reports", "violation_count")}
    return {"passed": data.get("passed"), "overhead_median_s": data.get("overhead", {}).get("median_s"),
            "paths": {n: {k: r[k] for k in ("median_s", "published_median_s", "ratio_ci90", "T3_coverage", "passed")}
                      for n, r in data.get("paths", {}).items()}}


def build_report(catalog_dir: Path, results_dir: Path) -> dict:
    catalog_dir, results_dir = Path(catalog_dir), Path(results_dir)
    records = _records(catalog_dir)
    coverage = json.loads((catalog_dir / "coverage.json").read_text())
    sources = json.loads((catalog_dir / "build_report.json").read_text()).get("inputs", {})
    assumed = Counter(a for r in records for a in r["fidelity"].get("assumed", []))
    checks = {}
    for kind in KINDS:
        found = latest(results_dir, kind)
        checks[kind] = None if found is None else {"file": found[0], **_summary(kind, found[1])}
    acceptance = latest(results_dir, "acceptance")
    validated = sorted(p["id"] for p in acceptance[1]["products"] if p["passed"]) if acceptance else []
    eligible = sum(1 for r in records if r["id"].startswith("zigbee:") and r["fidelity"]["reached"]["L1"]
                   and device_discovery(r, ieee="0x" + "0" * 16, friendly_name="x")[2])
    return {"sources": sources, "coverage": coverage, "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated},
            "timing_paths": {n: {"source": p.source, "basis": p.basis, "median_s": p.median()}
                             for n, p in PATHS.items()}}


def render_markdown(report: dict) -> str:
    lines = ["# VESPER fidelity report", "", "## Sources", "",
             "| Source | Version | Manifest SHA-256 |", "|---|---|---|"]
    for name, s in sorted(report["sources"].items()):
        lines.append(f"| {name} | {s.get('version', '')} | {str(s.get('manifest_sha256', ''))[:16]}… |")
    lines += ["", "## Coverage", "", "| Protocol | L0 | L1 | L2 (validated) | L3 | L4 |", "|---|---|---|---|---|---|"]
    for proto, c in sorted(report["coverage"].items()):
        l2 = len(report["l2"]["validated_products"]) if proto == "zigbee" else 0
        lines.append(f"| {proto} | {c['L0']} | {c['L1']} | {l2} | {c['L3']} | {c['L4']} |")
    lines += ["", f"L2 ({report['l2']['kind']}) is credited only to products that passed acceptance; "
                  f"{report['l2']['eligible_zigbee']} L1 Zigbee products are eligible (they expose at least one "
                  "Home Assistant entity through the bridge).", "", "## Assumed fields", "",
              "Counted out of coverage (spec §3.2). Most frequent:", "", "| Field | Records |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in list(report["assumed"].items())[:15]]
    lines += ["", "## Checks", "", "| Check | Result | File |", "|---|---|---|"]
    for kind, c in report["checks"].items():
        if c is None:
            lines.append(f"| {kind} | not run yet | |")
        else:
            lines.append(f"| {kind} | {'PASS' if c.get('passed') else 'FAIL'} | {c['file']} |")
    lines += ["", "## Timing paths", "", "| Path | Median | Source |", "|---|---|---|"]
    for name, p in report["timing_paths"].items():
        lines.append(f"| {name} | {p['median_s']:.4g} s | {p['source'] or 'assumed: ' + p['basis']} |")
    return "\n".join(lines) + "\n"
```

In `vesper/catalog/normalize/zigbee.py`, replace:
```python
REPORTING_PARENT = {"state": {"light", "switch"}, "brightness": {"light"}}
L2_REASON = "exposure tier not built yet (Plan C)"
L3_REASON = "reporting uses zigbee-herdsman-converters static defaults (assumed); timing not yet validated"
```
with:
```python
REPORTING_PARENT = {"state": {"light", "switch"}, "brightness": {"light"}}
L2_REASON = ("credited per product by validation evidence: `vesper validate acceptance`, "
             "summarised by `vesper report`; the catalog itself only knows the sources")
L3_REASON = "reporting uses zigbee-herdsman-converters static defaults (assumed); timing not yet validated"
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/validation/test_report.py tests/catalog/test_zigbee_normalize.py -q`

Expected: `19 passed`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/validation/report.py tests/validation/test_report.py vesper/catalog/normalize/zigbee.py tests/catalog/test_zigbee_normalize.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(validation): fidelity report; L2 credited from acceptance evidence

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 6: `vesper validate rules|latency` and `vesper report`

**Files:**
- Modify: `vesper/cli.py`, `tests/validation/test_cli.py` (append three tests)

**Interfaces:**
- Consumes: Tasks 3–5; Plan C's `connect`, `disconnect`, `Mosquitto` and `load_catalog`.
- Produces:
  - `vesper validate rules [--catalog] [--hours 4] [--seed 0] [--out]`: exit 0 only with zero violations;
  - `vesper validate latency [--catalog] [--product zigbee:aqara:mccgq11lm] [--paths local,vendor_cloud,trigger_action_cloud] [--n 200] [--spacing 0.05] [--seed 0] [--root .vesper] [--mqtt-port 18830] [--external-broker] [--out]`: exit 0 only if every path passes T1 and T3;
  - `vesper report [--catalog] [--results results/device-twins] [--out-dir results/device-twins]`.

- [ ] **Step 1: Write the failing tests**

In `tests/validation/test_cli.py`, replace:
```python
    assert f"{seen['n'] - 1}/{seen['n']} components accepted" in capsys.readouterr().out
```
with:
```python
    assert f"{seen['n'] - 1}/{seen['n']} components accepted" in capsys.readouterr().out


def test_rules_command_checks_every_twin_offline(tmp_path, catalog, capsys):
    out = tmp_path / "rules.json"
    code = cli.main(["validate", "rules", "--catalog", str(catalog), "--hours", "2", "--seed", "0", "--out", str(out)])
    report = json.loads(out.read_text())
    assert code == 0 and report["passed"] and report["reports"] > 0 and report["violation_count"] == 0
    assert "0 violations" in capsys.readouterr().out


def test_latency_command_measures_overhead_then_each_path(monkeypatch, tmp_path, broker, mosquitto, catalog, capsys):
    import random

    import vesper.validation.timing as timing_mod
    from vesper.twins.latency import PATHS

    calls = []

    def fake_measure(record, bridge_client, observer, *, n, path, overhead_s, spacing_s, timeout_s, seed):
        calls.append((path.name if path else None, overhead_s, n))
        rng = random.Random(seed)
        return [0.004] * n if path is None else [path.sample(rng) for _ in range(n)]

    monkeypatch.setattr(timing_mod, "measure_latency", fake_measure)
    out = tmp_path / "lat.json"
    code = cli.main(["validate", "latency", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--paths", "local,vendor_cloud", "--n", "200", "--out", str(out)])
    report = json.loads(out.read_text())
    assert [c[0] for c in calls] == [None, "local", "vendor_cloud"] and calls[1][1] == 0.004
    assert report["overhead"]["median_s"] == 0.004 and set(report["paths"]) == {"local", "vendor_cloud"}
    assert report["preregistration"]["margin"] == 0.10 and report["paths"]["local"]["source"] == PATHS["local"].source
    assert code == (0 if report["passed"] else 1) and mosquitto[0].events == ["start", "stop"]
    assert "local:" in capsys.readouterr().out


def test_report_command_writes_markdown_and_json(tmp_path, catalog, capsys):
    (catalog / "coverage.json").write_text(json.dumps({"zigbee": {"L0": 1, "L1": 1, "L2": 0, "L3": 0, "L4": 0}}))
    out = tmp_path / "report"
    assert cli.main(["report", "--catalog", str(catalog), "--results", str(tmp_path / "none"),
                     "--out-dir", str(out)]) == 0
    [md] = out.glob("fidelity-*.md")
    [js] = out.glob("fidelity-*.json")
    assert "## Checks" in md.read_text() and json.loads(js.read_text())["checks"]["roundtrip"] is None
    assert "fidelity report" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/validation/test_cli.py -q`

Expected: `3 failed, 17 passed`. Each fails on `SystemExit: 2` (argparse: `invalid choice: 'rules'`, `'latency'` or `'report'`).

- [ ] **Step 3: Implement**

In `vesper/cli.py`, replace:
```python

def _validate_discovery(args) -> int:
```
with:
```python

def _report(args) -> int:
    from vesper.validation.report import build_report, render_markdown
    report = build_report(Path(args.catalog), Path(args.results))
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"fidelity-{stamp}.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    (out / f"fidelity-{stamp}.md").write_text(render_markdown(report))
    print(f"fidelity report: {out / f'fidelity-{stamp}.md'}")
    return 0


def _validate_rules(args) -> int:
    from vesper.twins.home import load_catalog
    from vesper.validation.timing import run_rules
    records = [r for r in load_catalog(Path(args.catalog)).values() if r["id"].startswith("zigbee:")]
    report = run_rules(records, seed=args.seed, duration_s=args.hours * 3600)
    path = _write_report(args.out, "rules", report)
    print(f"rules: {report['products']} products, {report['features_checked']} reporting features, "
          f"{report['reports']} reports, {report['violation_count']} violations; report {path}")
    return 0 if report["passed"] else 1


def _validate_latency(args) -> int:
    from statistics import median

    from vesper.exposure.mqtt import connect, disconnect
    from vesper.twins.home import load_catalog
    from vesper.twins.latency import PATHS
    from vesper.validation import stats, timing
    from vesper.validation.stack import Mosquitto, StackError
    record = load_catalog(Path(args.catalog))[args.product]
    broker = None if args.external_broker else Mosquitto(Path(args.root), port=args.mqtt_port)
    try:
        if broker:
            broker.start()
        bridge_client = connect("127.0.0.1", args.mqtt_port, "vesper-latency-bridge")
        observer = connect("127.0.0.1", args.mqtt_port, "vesper-latency-observer")
        try:
            def run(path, overhead_s):
                timeout = 60.0 + (path.quantile(0.999) if path else 0.0)
                return timing.measure_latency(record, bridge_client, observer, n=args.n, path=path,
                                              overhead_s=overhead_s, spacing_s=args.spacing, timeout_s=timeout,
                                              seed=args.seed)
            overhead = run(None, 0.0)
            overhead_s = median(overhead)
            results = {}
            for name in args.paths.split(","):
                xs = run(PATHS[name], overhead_s)
                results[name] = stats.assess_path(xs, PATHS[name], seed=args.seed) | {"samples_s": xs}
        finally:
            disconnect(bridge_client)
            disconnect(observer)
    except (StackError, TimeoutError) as e:
        print(f"FAILED latency: {e}", file=sys.stderr)
        return 1
    finally:
        if broker:
            broker.stop()
    report = {"product": args.product, "seed": args.seed,
              "overhead": {"n": len(overhead), "median_s": overhead_s, "max_s": max(overhead), "samples_s": overhead},
              "paths": results, "passed": all(r["passed"] for r in results.values()),
              "preregistration": {"design": "docs/experiments/l3-timing-design.md", "margin": stats.MARGIN,
                                  "n": args.n, "bootstrap": stats.N_BOOT, "ad_critical": stats.AD_CRITICAL_05,
                                  "coverage_min": stats.COVERAGE_MIN}}
    path = _write_report(args.out, "latency", report)
    for name, r in results.items():
        print(f"{name}: median {r['median_s'] * 1000:.1f} ms vs published {r['published_median_s'] * 1000:.1f} ms; "
              f"ratio 90% CI [{r['ratio_ci90'][0]:.3f}, {r['ratio_ci90'][1]:.3f}]; coverage {r['T3_coverage']:.2f}; "
              f"{'PASS' if r['passed'] else 'FAIL'}")
    print(f"overhead median {overhead_s * 1000:.2f} ms; report {path}")
    return 0 if report["passed"] else 1


def _validate_discovery(args) -> int:
```

In `vesper/cli.py`, replace:
```python

    twins = commands.add_parser("twins", help="run virtual devices")
```
with:
```python

    rep = commands.add_parser("report", help="the fidelity report: coverage, assumptions and every check's result")
    rep.add_argument("--catalog", default="catalog/vds")
    rep.add_argument("--results", default="results/device-twins")
    rep.add_argument("--out-dir", default="results/device-twins")

    twins = commands.add_parser("twins", help="run virtual devices")
```

In `vesper/cli.py`, replace:
```python
    checks = validate.add_subparsers(dest="action", required=True)
    disc = checks.add_parser("discovery", help="Home Assistant's own schemas over every discovery message")
```
with:
```python
    checks = validate.add_subparsers(dest="action", required=True)
    rules = checks.add_parser("rules", help="L3: every twin obeys its reporting rules (logical clock, offline)")
    rules.add_argument("--catalog", default="catalog/vds")
    rules.add_argument("--hours", type=float, default=4.0)
    rules.add_argument("--seed", type=int, default=0)
    rules.add_argument("--out")
    lat = checks.add_parser("latency", help="L3: latency at the MQTT boundary vs the published distributions")
    lat.add_argument("--catalog", default="catalog/vds")
    lat.add_argument("--product", default="zigbee:aqara:mccgq11lm", help="a contact sensor to replicate")
    lat.add_argument("--paths", default="local,vendor_cloud,trigger_action_cloud")
    lat.add_argument("--n", type=int, default=200)
    lat.add_argument("--spacing", type=float, default=0.05, help="seconds between stimuli")
    lat.add_argument("--seed", type=int, default=0)
    lat.add_argument("--root", default=".vesper")
    lat.add_argument("--mqtt-port", type=int, default=18830)
    lat.add_argument("--external-broker", action="store_true")
    lat.add_argument("--out")
    disc = checks.add_parser("discovery", help="Home Assistant's own schemas over every discovery message")
```

In `vesper/cli.py`, replace:
```python
        return _setup(args)
    if args.command == "twins":
```
with:
```python
        return _setup(args)
    if args.command == "report":
        return _report(args)
    if args.command == "twins":
```

In `vesper/cli.py`, replace:
```python
        return _validate_discovery(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```
with:
```python
        return _validate_discovery(args)
    if args.action == "rules":
        return _validate_rules(args)
    if args.action == "latency":
        return _validate_latency(args)
    return _validate_roundtrip(args) if args.action == "roundtrip" else _validate_acceptance(args)
```

- [ ] **Step 4: Run them, the whole suite and lint**

Run: `.venv/bin/python -m pytest tests/validation/test_cli.py -q`

Expected: `20 passed`.

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/catalog tests/twins tests/exposure tests/validation`

Expected: `309 passed, 6 skipped` in the working copy, or `308 passed, 7 skipped` in a fresh export, where the local-data check also skips; then `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py tests/validation/test_cli.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(cli): validate rules and latency; vesper report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

### Task 7: The pre-registration and the run book

**Files:**
- Create: `docs/experiments/l3-timing-design.md`
- Modify: `docs/experiments/device-twins-runbook.md`

**Interfaces:**
- Produces: the pre-registered L3 procedures and tolerances, which must match `stats.py` and `timing.py`; and the run book entries for Experiments 3–4 and the report.

- [ ] **Step 1: Write the pre-registration**

Create `docs/experiments/l3-timing-design.md`:
```markdown
# L3 timing fidelity: design and pre-registration

**Status.** This file is the pre-registration. It is committed before any L3 run, and that commit hash is cited in the paper. Changing a number below after the first run requires a new commit and is disclosed.

**Spec.** This file implements:
- §1 criterion 4: "Reporting intervals and latencies match the VDS rules and the published measurements, within tolerances fixed before running";
- §3.3: Reporting, Latency, Determinism and Output events;
- §4, row L3.

**Code.** `vesper/twins/latency.py`, `vesper/validation/timing.py`, `vesper/validation/stats.py`.

**Commands.** `vesper validate rules`, `vesper validate latency`.

## What L3 claims, and what it does not

1. **Rule conformance (deterministic).** Every report a twin emits obeys its VDS reporting rule, and every occupancy clears after its timeout.
2. **Latency at the platform boundary.** A real MQTT subscriber, receiving through VESPER's real Mosquitto, observes twin reports with the latency distribution published for the product's path.
3. **What stays `assumed`.** Per spec §3.2 these do not count toward L3 coverage, so the coverage table shows L3 as partial for most products:
   - reporting rules taken from zigbee-herdsman-converters' static defaults, because a product's own `configure()` may differ;
   - every path with no published measurement: the Zigbee radio leg alone, Matter subscription reports, SmartThings or Tuya cloud;
   - the sleepy-device poll delay, which is a vendor default.

## Latency paths (seconds)

| Path | Model | Source and derivation | Status |
|---|---|---|---|
| `local` | Lognormal(median 0.1145, σ 0.17) | Zhan, Li, Zhang, Haddadi, arXiv:2602.22525 (2026): MQTT → Home Assistant REST → Hue → confirmation, N=150; median 114.5 ms, P95 152 ms; σ = ln(152/114.5)/1.645 | sourced |
| `vendor_cloud` | Lognormal(median 1.369, σ 0.212) | Goksel et al., IEEE SafeThings 2021, Table III (from Lee, Mun, Lee, ICCE 2020): device ↔ vendor cloud 1.4 ± 0.3 s; moment-matched | sourced (secondary) |
| `trigger_action_cloud` | Lognormal(median 84, σ 0.553) | Mi, Qian, Zhang, Wang, IMC 2017: IFTTT trigger→action quartiles 58/84/122 s; σ = ln(122/84)/0.6745 | sourced |
| `sleepy_downlink` | Uniform(0, 5) added to commands to battery devices | Silicon Labs Poll Control default long poll, 5 s | assumed |

**Avoiding double counting.** The published end-to-end figures include the platform's own processing. VESPER runs that processing for real. The procedure:
1. Measure the overhead first: n = 200 samples with no latency model (twin → bridge → Mosquitto → subscriber).
2. Take `o`, the median of that overhead.
3. The hook then samples `max(0, L − o)`, where `L` follows the published model.

The overhead itself is reported.

## Procedures

**R (rules), logical clock, offline.**
- Population: every L1 Zigbee product with reporting rules.
- Run: 4 h of simulated time, seed 0.
- Script per twin: events at Poisson times with a mean gap of 120 s. Each event is drawn from the twin's own inputs: ambient random walk, presence, contact, load (0–2000 W), button press, and platform commands to settable features.
- Every report is checked as it is emitted.

**T (latency), wall clock, live Mosquitto (127.0.0.1:18830).**
- Each path uses n = 200 copies of one contact sensor (`zigbee:aqara:mccgq11lm`; contact has no reporting rule, so a report leaves immediately).
- Each copy is opened once, 50 ms apart.
- Latency is measured on the monotonic clock: the subscriber's receipt minus the time the stimulus was applied.
- Paths tested: `local`, `vendor_cloud`, `trigger_action_cloud`.
- `sleepy_downlink` is assumed and not tested.
- Seed 0.

## Tests (fixed before running)

| Check | Statistic | Pass |
|---|---|---|
| R1 | interval between consecutive reports of a feature with a rule ≥ `min_s` | 100% (exact) |
| R2 | longest silence of such a feature, the end of the run included, ≤ `max_s` | 100% (exact) |
| R3 | a non-periodic report changes the value by ≥ `change` since the last report (numeric), or at all (other types) | 100% (exact) |
| R4 | occupancy clears exactly 90 s after the last presence | 100% (exact) |
| T1 | median observed/published: the 90% bootstrap CI (2,000 resamples, seed 0) lies within [0.90, 1.10]. This is TOST at α = 0.05 with a ±10% margin. | per path |
| T2 | Anderson–Darling against the fully specified published distribution; critical value 2.492 (α = 0.05) | secondary, reported |
| T3 | ≥ 90% of observations inside the published P5–P95 | per path |

A path passes when T1 and T3 both pass. Failures are reported, not tuned away.

## Context comparisons (no pass/fail)

- ZLeaks Table 7 periodic intervals (Shafqat et al., ACNS 2022) against the twins' `max_s`. Those intervals are configured by the hub (SmartThings), so they are context only.
- Inter-report intervals from ZigBeeNet (CC BY 4.0) and ZIOTP2025 (Apache-2.0), computed off this Mac, for products those datasets share with the catalog.
```

- [ ] **Step 2: Extend the run book**

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown
| Platform acceptance | L2, scale tier | `vesper validate acceptance` | see "Pass" under Experiment 2; at least 8 categories in `categories_passing` |
```
with:
```markdown
| Platform acceptance | L2, scale tier | `vesper validate acceptance` | see "Pass" under Experiment 2; at least 8 categories in `categories_passing` |
| Reporting rules | L3 (offline) | `vesper validate rules` | 0 violations of R1–R4 (`docs/experiments/l3-timing-design.md`) |
| Latency | L3 (live Mosquitto) | `vesper validate latency` | T1 and T3 per path, as pre-registered |

`vesper report` then writes the fidelity report from the catalog and the latest result of each check.

Commit `docs/experiments/l3-timing-design.md` before the first L3 run: it is the pre-registration.
```

In `docs/experiments/device-twins-runbook.md`, replace:
```markdown

## Running twins for your own Home Assistant
```
with:
```markdown

## Experiment 3: reporting rules (L3, offline, no services)

    taskpolicy -b .venv/bin/vesper validate rules --hours 4 --seed 0

This runs every L1 Zigbee product with reporting rules on the logical clock, under a seeded script of stimuli and platform commands. Every report is checked against R1–R4 as it is emitted.
- It is CPU only and starts nothing. Simulated time runs far faster than real time.
- Budget (estimate): minutes for the full catalog, 1 core, <1 GB.
- Output: `results/device-twins/rules-<UTC>.json`, holding the products and features checked, the report count, the violations (first 200, plus the total), and any faulted twins.
- Pass: `passed` is true (zero violations, nothing faulted).

## Experiment 4: latency (L3, live Mosquitto)

    taskpolicy -b .venv/bin/vesper validate latency --n 200 --seed 0

1. Starts VESPER's own Mosquitto on 18830.
2. Measures VESPER's own pipeline overhead (200 contact-sensor twins opened once each, no latency model).
3. Measures each path: `local`, `vendor_cloud`, `trigger_action_cloud`. Each uses 200 fresh twins and the published model minus that overhead.
4. Applies T1–T3.

Budget (estimate):
- `trigger_action_cloud`'s median is 84 s, but its 200 twins run concurrently, so the whole run takes ~5–10 min;
- 1 core, <1 GB.

Output: `results/device-twins/latency-<UTC>.json`, holding the overhead, then per path the samples, median, published median, 90% CI of their ratio, Anderson–Darling statistic and coverage. The pre-registration constants are included.

Pass: T1 and T3 hold for every path.

## The fidelity report

    .venv/bin/vesper report

This writes `results/device-twins/fidelity-<UTC>.md` and `.json`. They contain:
- source versions;
- coverage per level;
- the most common `assumed` fields;
- each check's latest result: a check that never ran is shown as "not run yet", never as passed;
- the timing paths with their sources.

L2 is credited only to products that passed acceptance.

## Running twins for your own Home Assistant
```

- [ ] **Step 3: Check the documented constants and commands against the code**

Run: `.venv/bin/python -c "from vesper.validation import stats; assert (stats.MARGIN, stats.N_SAMPLES, stats.N_BOOT, stats.AD_CRITICAL_05, stats.COVERAGE_MIN) == (0.10, 200, 2000, 2.492, 0.90); print('ok')" && .venv/bin/vesper validate rules --help >/dev/null && .venv/bin/vesper validate latency --help >/dev/null && .venv/bin/vesper report --help >/dev/null && echo ok`

Expected: `ok` twice.

- [ ] **Step 4: Commit (this commit is the pre-registration: record its hash in the ledger)**

```bash
cd /Users/huanbui/Research/vesper
git add docs/experiments/l3-timing-design.md docs/experiments/device-twins-runbook.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "docs: L3 pre-registration; run book for rules, latency and the report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

