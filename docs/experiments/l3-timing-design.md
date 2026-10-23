# L3 timing fidelity: design and pre-registration

**Status: Amendment 1 (2026-10-23), the binding version.**
- It supersedes the version in commit 3633ed3. That version was never used for any run and must not be cited.
- Why it was amended, before any run: a fresh code review found three problems.
  1. Its pass rule would fail a perfect pipeline about half the time: the coverage threshold equalled the band's nominal coverage.
  2. Its latency test compared observations with the very distribution VESPER injects, with all paths sharing one random draw.
  3. Its overhead calibration missed the host's timer lateness, which reaches 80–200 ms at background QoS on macOS.
- The commit that adds this amendment is the one to cite. Changing a number below after the first L3 run requires a new, disclosed commit.

**Spec.** This file implements:
- §1 criterion 4: "Reporting intervals and latencies match the VDS rules and the published measurements, within tolerances fixed before running";
- §3.2: assumed fields;
- §3.3: Reporting, Latency, Determinism and Output events;
- §4, row L3.

**Code.**
- `vesper/twins/latency.py`, `vesper/twins/runtime.py`;
- `vesper/validation/timing.py`, `vesper/validation/stats.py`;
- commands: `vesper validate rules`, `vesper validate latency`.

## What L3 claims, and what it does not

1. **Rule conformance (exact).**
   - Every report a twin emits obeys its VDS reporting rule.
   - Every occupancy or presence feature clears exactly 90 s after the last presence, whether or not it has a reporting rule.
   - The reports of one twin reach the platform in the order they left.
2. **The latency generator reproduces each published distribution** (G, offline).
3. **VESPER's own pipeline preserves the latency it injects** (P, wall clock): the real Mosquitto, the bridge, the MQTT client and the host's scheduling add at most a pre-registered residual.
   - This is a check of VESPER's machinery, not independent evidence about devices.
   - The end-to-end latency a subscriber observes is reported against the published distribution, but descriptively only.
4. **What stays `assumed`.** Per spec §3.2 these do not count toward L3 coverage:
   - reporting rules taken from zigbee-herdsman-converters' static defaults;
   - the `local` path for sensor reports, because its source measured an actuation round trip;
   - the sleepy-device poll wait;
   - every path with no published measurement.
5. **No independent real-device test yet.** Spec §4 asks for a comparison with real-device measurements where public traffic data exists, and that comparison is not implemented here. The redistributable candidates are ZigBeeNet (CC BY 4.0) and ZIOTP2025 (Apache-2.0); their inter-report intervals are to be computed off this Mac. Until then the paper states that L3 is conformance to assumed rules plus the preservation of published latencies. This is a PI decision (RKA `jrn_01M5KBZ350H65H30BD5EC1T7VF`).

## Latency paths (seconds)

| Path | Model | Source and derivation | Status |
|---|---|---|---|
| `local` | Lognormal(median 0.1145, σ 0.17) | Zhan, Li, Zhang, Haddadi, arXiv:2602.22525 (2026): MQTT → Home Assistant REST → Hue → confirmation, N=150; median 114.5 ms, P95 152 ms; σ = ln(152/114.5)/1.645 | sourced for commands; borrowed (assumed) for sensor reports |
| `vendor_cloud` | Lognormal(median 1.369, σ 0.212) | Goksel et al., IEEE SafeThings 2021, Table III (from Lee, Mun, Lee, ICCE 2020): device ↔ vendor cloud 1.4 ± 0.3 s; moment-matched | sourced (secondary) |
| `trigger_action_cloud` | Lognormal(median 84, σ 0.553) | Mi, Qian, Zhang, Wang, IMC 2017: IFTTT trigger→action quartiles 58/84/122 s; σ = ln(122/84)/0.6745 | sourced, for trigger–action automations |
| `sleepy_downlink` | Uniform(0, 5), added to commands to battery devices | Silicon Labs Poll Control default long poll, 5 s | assumed |

**Calibration through the same timer path.** VESPER runs the platform's own processing for real, so the hook subtracts VESPER's overhead. The procedure:
1. Inject a constant delay of 0.05 s (`CALIBRATION_DELAY`) into 200 twins. It is delivered through the same wall-clock timer as every model.
2. Take the median residual `o` of that run (observed − injected).
3. Each path then injects `max(0, L − o)`, where `L` follows the published model.

## Procedures

**R (rules): logical clock, offline.**
- Population: every L1 Zigbee product.
- Checked: its reporting-rule features, and its binary `occupancy`/`presence` features.
- Run: 4 h of simulated time, seed 0.
- Script per twin: events at Poisson times with a mean gap of 120 s. Each event is drawn from the twin's own inputs: ambient random walk, presence, contact, load (0–2000 W), button press, and platform commands to settable features.
- Every report is checked in-process as it is emitted, at its send time, with no latency model.
  - That is the level at which a reporting rule is defined.
  - The bridge republishes each reported value unchanged.
  - Spec §4's "from the broker log" applies to latency.
- Reported alongside: rules dropped because the twin lacks the feature, stimulus rejections, and the number of occupancy clears checked.

**G (generator): offline.**
- For each path, 100,000 draws seeded with `stable_seed(seed, path, "generator")`.

**P (pipeline): wall clock, VESPER's own Mosquitto on 127.0.0.1:18830.**
- Topics: `vesper-latency/…`.
- Calibration first (above).
- Then, for each of `local`, `vendor_cloud` and `trigger_action_cloud`:
  - 200 fresh copies of one contact sensor (`zigbee:aqara:mccgq11lm`); contact has no reporting rule, so a report leaves immediately;
  - twin ids and seed are distinct per path (`stable_seed(seed, path)`), so paths never share draws;
  - each copy is opened once, 50 ms apart;
  - per sample, `injected` is read from the runtime's event and `observed` is the subscriber's receipt minus the stimulus time, on the monotonic clock.
- The per-path timeout is 60 s plus the path's `1 − 0.001/n` quantile, so a spurious timeout has probability below 0.1%.
- **Scheduling requirement:** the wall-clock run must run at the default QoS, not under `taskpolicy -b`, as one process, while no other heavy job runs on the machine.
  - At background QoS, macOS coalesces timers by 80–200 ms. That would measure the host, not VESPER.
  - On the shared lab Mac this needs the PI's explicit approval for the slot.

## Tests (fixed before running)

| Check | Statistic | Pass |
|---|---|---|
| R1 | interval between consecutive reports of a feature with a rule ≥ `min_s` | 100% (exact) |
| R2 | longest silence of such a feature, the end of the run included, ≤ `max_s` | 100% (exact) |
| R3 | a non-periodic report changes the value by ≥ `change` since the last report (numeric), or at all (other types) | 100% (exact) |
| R4 | an occupancy/presence feature clears exactly 90 s after the last presence, and none is still on more than 90 s after its last presence at the end of the run | 100% (exact) |
| R | the run checked at least one feature | required |
| G | sampled median within 1%, and sampled P5 and P95 within 2%, of the published values, both as a fraction of the published median | every path |
| P | residual (observed − injected): median ≤ 20 ms and P95 ≤ 50 ms (type-7 quantiles) | calibration and every path |
| Descriptive | end-to-end observed vs published: 90% bootstrap interval of the median ratio (2,000 resamples, symmetric order statistics, seed `stable_seed(seed, path, "bootstrap")`), Anderson–Darling (case 0, critical value 2.492), P5–P95 coverage | reported, not pass/fail |

The latency result passes when G, the calibration's P and every path's P all pass. Failures are reported, not tuned away. A run with other parameters is labelled exploratory by `vesper report`.

## Context comparisons (no pass/fail)

- ZLeaks Table 7 periodic intervals (Shafqat et al., ACNS 2022) against the twins' `max_s`. They are configured by the hub (SmartThings), so they are context only.
- ZigBeeNet and ZIOTP2025 inter-report intervals: see "What L3 claims", item 5.
