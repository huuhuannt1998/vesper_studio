# L3 timing fidelity: design and pre-registration

**Status: Amendment 1 (2026-10-23), the binding version.**
- It supersedes the version in commit 3633ed3. That version was never used for any run and must not be cited.
- Why it was amended, before any run: a fresh code review found three problems.
  1. Its pass rule would fail a perfect pipeline about half the time: the coverage threshold equalled the band's nominal coverage.
  2. Its latency test compared observations with the very distribution VESPER injects, with all paths sharing one random draw.
  3. Its overhead calibration missed the host's timer lateness, which reaches 80–200 ms at background QoS on macOS.
- The commit that adds this amendment is the one to cite. Changing a number below after the first L3 run requires a new, disclosed commit.

**Amendment 2 (2026-10-23, revised 2026-10-24), before any L3 run.** It adds E2, the real-device test (section "E2" below). Nothing in amendment 1 changes, and its commit 7050263 remains the citation for R, G and P. PI decisions behind it: RKA `jrn_01M5MZ2ZNCK1WG5D3628NNWNYY`.
- Revised before any E2 run, and before the dataset was fetched, after a whole-branch code review. The first version (commit f851fc4) was never used and must not be cited. Its flaws:
  1. It replayed each segment as one continuous run, so a device whose clock runs a few seconds off per heartbeat drifted out of the matching window and failed while following its rule.
  2. It counted change-driven intervals, which agree by construction, as evidence.
  3. It keyed streams without the source endpoint.
  4. It missed rejoins (address changes) and undecodable frames as possible loss.
  5. It let manufacturer-specific attributes into standard streams.
  6. It counted other networks' frames against the decryption gate.
  7. Its ZHA rule table omitted the lighting, HVAC and closure handlers.
- The commit that adds this revision is the citation for E2.

**Spec.** This file implements:
- §1 criterion 4: "Reporting intervals and latencies match the VDS rules and the published measurements, within tolerances fixed before running";
- §3.2: assumed fields;
- §3.3: Reporting, Latency, Determinism and Output events;
- §4, row L3.

**Code.**
- `vesper/twins/latency.py`, `vesper/twins/runtime.py`;
- `vesper/validation/timing.py`, `vesper/validation/stats.py`;
- `vesper/validation/zcl.py`, `vesper/validation/capture.py`, `vesper/validation/realdata.py` (E2);
- commands: `vesper validate rules`, `vesper validate latency`, `vesper validate realdata`.

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
5. **The independent real-device test is E2** (amendment 2). Spec §4 asks for a comparison with real-device measurements where public traffic data exists. E2 replays real Zigbee devices' reports through VESPER's reporting runtime, under the rule their hub configured. It tests the reporting mechanism, not the catalog's default rules: those come from Zigbee2MQTT and are reported beside it, descriptively.

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

## E2 (real devices): offline, real captures

**Data.** ZIOTP2025 (Boiano et al., arXiv:2602.03140; Apache-2.0), git commit `c3481e8073f40bdbc1bee4a24e05a0a13a9545ba` of github.com/antonio-boiano/ZIOTP2026. Every `Data/<scenario>/Topology_<A|B>/*.pcapng`, decrypted with the network keys published in `Data/Device_Name.txt`.
- ZigBeeNet is not used: its hub's reporting configuration is unknown, so there is no rule to test against (PI decision).
- Each capture file is a session; no interval spans two files.

**Rule.** The ZIOTP hub was Home Assistant's ZHA, so streams are judged against ZHA's default attribute-reporting configuration.
- Source: zigpy/zha 0.0.70 (2025-08-26, `ebc3cbf`), `zha/zigbee/cluster_handlers/{const,general,measurement,homeautomation,smartenergy,lighting,hvac,closures}.py`. The reporting configuration is identical in 0.0.50 (2025-02-26) and 1.0.0 (2026-02-25), and zha 2.0.0 (2026-06-24) postdates the dataset. Cluster and attribute ids were checked against zigpy.
- Values (min_s / max_s / change, raw ZCL units):
  - default 30/900/1; level 1/900/1; on/off, occupancy, lock state and covering position 0/900/1; power 5/900/1; battery 3600/10800/1;
  - temperature 30/900/50; humidity 30/900/100; colour 30/900/1;
  - thermostat 30/900/25, with its demands 30/900/5.
- The full table (76 attributes) is `ZHA_RULES` in `vesper/validation/realdata.py`.
- The result records the capture dates. Dates outside 2025-02-26 – 2026-02-25 are flagged; the rule is not changed.

**Firmware-governed devices (classified before the data is read).** zigbee-herdsman-converters 26.123.0 configures no attribute reporting for the four Aqara models (MCCGQ11LM, DJT11LM, WB-R02D, MS-S02) and gives each a one-hour check-in quirk: their report timing is set by firmware, not by the hub. Their streams are reported but not judged. Every other device in `Device_Name.txt` is judged. A device name outside the pre-registered table stops the run.

**Procedure.**
1. **Decryption.** tshark decrypts each file with its topology's key and verifies every frame's MIC.
   - The key is tried in the byte order printed, then reversed, and the order that decrypts is recorded. A wrong key cannot pass the MIC check, so this choice is not a free parameter.
   - The file's network is the PAN with the most decrypted frames. Secured frames of other PANs on the channel are counted and reported, but not used.
   - Frames whose FCS check failed are counted and never used.
   - A file without secured frames fails D, because the key cannot be verified on it.
2. **Keying and duplicates.** Frames are keyed on the NWK source, which is the originating device, not the relaying hop, and mapped to the device through `Device_Name.txt`, earlier addresses included.
   - Relayed copies and APS retries are dropped: a repeat of the same (source, APS counter) within 10 s. The first copy seen is kept.
3. **Possible loss is cut out.** Any of these marks possible sniffer loss: a step other than +1 in a device's APS counter, a change of its short address (a rejoin), or a report frame of the cluster that cannot be decoded exactly. An interval that spans any of them is cut out.
4. **Streams.** Each ZCL Report Attributes record becomes one report of one (device, source endpoint, cluster, attribute) stream.
   - Manufacturer-specific reports are counted and left out, because their attribute ids are the vendor's own.
5. **Prediction, interval by interval.** For each pair of consecutive real reports (a, b) in a stream, VESPER's runtime (`vesper/twins/runtime.py`) runs on the logical clock:
   - the twin is installed in a's state, as just reported at a's time;
   - b's value is applied as a `measured` stimulus at b's time;
   - the twin's first report after a is its prediction of the device's next report.
   - Every interval is anchored at a real report, so a device clock's drift does not accumulate.
6. **Classes.** An interval is a `change` interval when b carries a reportable change between `min_s` and `max_s` after a. There the twin agrees by construction, because it is given b's value at b's time; such intervals are reported as a check of the runtime, never as evidence. Every other interval is informative:
   - `steady`: no reportable change, so the heartbeat is due;
   - `early_change`: a change before `min_s`;
   - `late_change`: a change after a silence longer than `max_s`.
7. **Verdict.** The prediction matches b if it lies within 1 s of b (change-driven) or within ±10% of `max_s` of it (heartbeat).
   - Otherwise the interval is `extra` when the twin would have reported first, and `missed` when the device reported first.

**Criteria (fixed before running).**

| Check | Statistic | Pass |
|---|---|---|
| D | MIC-verified decryption, per file, on the file's network | ≥ 99% of its secured frames, every file |
| E | a stream is judged if its device is not firmware-governed and it has ≥ 20 informative intervals | at least one judged stream |
| A | per judged stream: informative intervals in which the twin's prediction matches the device's next report | ≥ 95% |
| Descriptive | per stream: the classes; agreement on `change` intervals (a runtime check); `extra` and `missed`; intervals ≥ `min_s` − 1 s; silences ≤ 1.1 × `max_s`; median `steady` interval ÷ `max_s`; early intervals carrying a reportable change; the same prediction under VESPER's catalog rule (Zigbee2MQTT defaults, converted to raw units). Per file: foreign and bad-FCS frames, undecodable and manufacturer-specific reports | reported, not pass/fail |

E2 passes when D holds for every file, at least one stream is judged, and every judged stream passes A.
- A result from another dataset commit, or from a checkout with local changes, is labelled exploratory by `vesper report`.
- Failures are reported, not tuned away.

**What E2 claims.** A pass certifies that, on the informative intervals of devices whose firmware follows the hub's reporting configuration, VESPER's runtime predicts each next report as the device made it. That covers the heartbeat at `max_s`, the deferral to `min_s` and the reportable-change gate.
- It does not validate the catalog's default rules. Zigbee2MQTT and ZHA configure the same devices differently (for example, a heartbeat of 3600 s against 900 s), and the descriptive row shows by how much.
- The prediction sees only the values a device reported. A change the device measured but never sent is invisible to it, so E2 cannot show that a twin reports no more often than the real device would under the same physical input.
- Keeping the first copy seen of a report misdates it when the sniffer missed the original transmission and caught an APS retry. This is rare, and it affects only the intervals on either side of that report.
- A failure is attributed with the descriptive statistics: `missed` with early intervals points to the device; `extra` with silences longer than `max_s` points to missing heartbeats.

## Context comparisons (no pass/fail)

- ZLeaks Table 7 periodic intervals (Shafqat et al., ACNS 2022) against the twins' `max_s`. They are configured by the hub (SmartThings), so they are context only.
