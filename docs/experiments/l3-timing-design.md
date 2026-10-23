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
