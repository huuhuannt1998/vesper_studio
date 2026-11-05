# Environmental differential testing (EDT): results (2026-11-04)

Every number below is in `results/edt/summary-dc119cd.json`, written by `scripts/edt_summary.py` from the local
results (the full sweep, cover, ladder and closed-loop JSONs hold corpus content and are not committed). The
protocol, fixed before the first full run, is `docs/experiments/edt-protocol.md` (sections 1-7, Addenda 1-3); the
design and its research questions are `docs/superpowers/specs/2026-11-04-environmental-differential-testing-design.md`.
"Pre-registered" means fixed in the protocol before the run; anything else is labelled post hoc.

**Revision R-A (ERA5).** The sweep above read Open-Meteo's default archive blend. The paper reports the re-run with the
archive pinned to ERA5 (commit `120f4ca`; `results/edt/summary-120f4ca.json`, `analysis-120f4ca.json`,
`controls-120f4ca.json`); see "ERA5 re-run" below for every number that moved. Numbers in the sections below are the
original `dc119cd` ones unless that section says otherwise.

## What was run

- **Code**: commit `dc119cd` (branch `plan-w4b`), `vesper edt run` / `cover` / `ladder` and the closed-loop
  confirmation (`vesper.edt.confirm.confirm` called from a script, the same call as `vesper edt confirm --sweep
  sweep-rung4.json --work DIR --out FILE --sample 200 --seed 0 --workers 2 --cache DIR`), 6 worker processes, one
  laptop.
- **Corpus**: 45,208 automations from 1,186 public Home Assistant repositories (1,123 of them with automations); de-duplicated by canonical hash
  (protocol section 1), **43,280 automations**, the unit of every count below. 4,984 of them read the environment as
  written (sun, temperature, illuminance, humidity or weather: "environment-coupled"); the census is
  `results/edt/census-20261104.json`.
- **Environments**: 48 = six cities (Phoenix, Seattle, Minneapolis, Stockholm, Singapore, Sydney) x four dates
  (2026-01-14, 04-15, 07-15, 10-14, all Wednesdays, Addendum 3) x plan north 0 and 180. Each environment is one
  three-day trace of the standard EDT home (no residents) from local midnight; day 0 is warm-up, days 1-2 are judged.
- **Rungs**: the full model (rung 4) and the necessity ladder's rungs 3, 2, 1 (protocol section 2), each over all
  48 environments; rung 0 is a syntactic classifier (Addendum 1, A1.4).
- **Screening is open loop** (actions recorded, not executed); a sample of 200 flagged and 200 unflagged
  automations was then run closed loop (actions executed in a full session).

## RQ1: scale and yield

### The funnel (rung 4)

| Subset | Parsed | Bound | Run | Flagged |
|---|---|---|---|---|
| All automations | 43,280 | 9,524 (22.0 %) | 5,934 (62.3 % of bound) | 799 |
| All, assumption-free | 43,280 | 8,486 | 5,067 | 650 |
| Environment-coupled | 4,984 | 1,367 (27.4 %) | 1,213 (88.7 % of bound) | 780 |
| Environment-coupled, assumption-free | 4,719 | 1,102 (23.4 %) | 977 (88.7 % of bound) | 633 |
| Not environment-coupled | 38,296 | 8,157 (21.3 %) | 4,721 (57.9 % of bound) | 19 |

"Bound": every entity rewritten onto the standard home. "Run": bound and not refused by the engine in at least one
environment. "Assumption-free": bound with no gate-open assumption (Addendum 2, A2.1); 1,038 bound automations carry
at least one ("assumed input_boolean.x == off"). The assumption-free environment-coupled row's "parsed" is 4,984
minus the 265 environment-coupled automations bound with an assumption. No environment failed as a whole
(`failed_envs` 0) and no automation was refused in some environments and run in others (`partially_refused` 0).

By the kind of environment an automation reads (an automation can read several):

| Kind | Parsed | Bound | Run | Flagged | Flagged / run |
|---|---|---|---|---|---|
| sun | 2,908 | 1,013 | 895 | 609 | 68.0 % |
| temperature | 1,380 | 231 | 214 | 130 | 60.7 % |
| illuminance | 462 | 79 | 67 | 15 | 22.4 % |
| humidity | 348 | 74 | 63 | 37 | 58.7 % |
| weather | 268 | 9 | 9 | 8 | 88.9 % |

**Against the spec's risk line** (section 6: "parse/bind yield below 50 % of environment-coupled automations would
make the corpus claim about a subset"): 27.4 % of the environment-coupled automations bind (23.4 % assumption-free),
so every corpus-level statement below is about the evaluable subset (1,213 environment-coupled automations that
run). Of the bound environment-coupled automations, 88.7 % run.

### Why automations do not bind or run

Unbindable (33,756 automations, by reason class; `scripts/edt_summary.py` maps the binder's reasons onto these
classes; 24 classes, the top 20 in the JSON):

| Reason class | Automations |
|---|---|
| an entity's name names no role the standard home has | 9,547 |
| a helper or presence entity in a trigger | 7,257 |
| an entity domain the standard home does not model (media players 1,402, alarm panels 853, automations 482, input_datetime 418, scripts 359, scenes 358, vacuums 263, ...) | 6,386 |
| a helper or presence entity in a template or an action | 4,418 |
| device triggers of remotes are not modelled | 942 |
| a helper or presence entity elsewhere (inside or/not, beside a device, as a threshold, in a zone or device condition) | 814 |
| blueprint inputs are not expanded | 794 |
| distinct entities collapse onto one device | 778 |
| the standard home has no cover or climate in that room | 616 |
| a device trigger, condition or action of an unmodelled integration | 366 |
| semi-outdoor spaces are not modelled | 338 |
| device_id/area_id not modelled | 337 |
| device_id targets name no entity | 270 |
| device groups are not modelled | 223 |
| (6 more classes of 152 to 46 each, and 4 classes with 61 in all) | 670 |

Refused by the engine at rung 4 (3,590 bound automations that run nowhere; 25 classes):

| Reason class | Automations |
|---|---|
| it has no trigger | 1,036 |
| an `event` trigger (a device button or remote 363, a phone app 302, a custom event 256, a messaging or web service 244, a Home Assistant internal event 147) | 1,312 |
| an `mqtt` trigger (it needs an MQTT message) | 355 |
| an automation made from a blueprint | 165 |
| a `webhook` trigger | 157 |
| another unsupported trigger or condition | 85 |
| a template feature the engine does not support | 76 |
| a `tag` trigger (NFC) | 72 |
| an entity (templated or unbound) that is not one of this home's | 66 |
| a `value_template` in a numeric_state trigger or condition | 58 |
| a delay or duration that is a template or malformed | 48 |
| (10 more classes) | 160 |

Most refusals are inputs the standard home cannot produce (events, MQTT messages, webhooks, NFC tags, voice
commands), not unsupported semantics.

### The binding audit (protocol A2.5)

Every distinct ambient sensor name (illuminance, temperature, humidity) bound in a 10,000-automation sample was
checked by hand for room, role and medium; the audit names corpus entities and stays outside the repository.

- **Pass 1**: 63 names; 11 mis-binds in 5 classes found and fixed (heating setpoints, modes, LED strips, lamp
  brightness read as sensors; room words in other languages; the entrance as the hallway).
- **Pass 2** (after the fixes): 54 names; **47 correct** (38 bound correctly from their names, 9 room-less names
  bound to the living room by convention), **6 residual mis-binds, 1 uncertain**: audited binding precision 87 % (47/54),
  all residuals names that carry no room or medium.

Residual mis-binds of this kind are visible among the flagged automations (not counted; found while reading
examples): of the 19 flagged automations the census does not count as environment-coupled (RQ2), at least two have a
device's own LED brightness bound as room illuminance; among the temperature-coupled ones, a freezer alarm whose
Fahrenheit threshold (32) lies inside the ambient range (-30..35 C) binds as a room temperature.

### Throughput

Each rung replays the 9,524 bound automations in 48 environments (457,152 automation-environment evaluations;
traces cached):

| Rung | Replay (s) | Wall, whole sweep (s) | Evaluations per minute (6 workers) |
|---|---|---|---|
| 4 | 585.7 | 672 | 46,831 |
| 3 | 615.8 | 689 | 44,542 |
| 2 | 679.8 | 756 | 40,349 |
| 1 | 348.6 | 425 | 78,684 (sun refused: less to replay) |

The closed-loop run's elapsed time was not recorded.

## RQ2: what EDT finds

### Flags

799 of the 5,934 automations that run (13.5 %) are flagged (650 of 5,067 assumption-free), from 303 of the 722
repositories with a run automation. Flagged means at least one environmental divergence (protocol section 4); an
automation can have several, so the kind counts add up to more than 799:

| Divergence kind | All (799) | Assumption-free (650) |
|---|---|---|
| time_spread (first firings > 3 h apart in local time) | 716 | 597 |
| fires_somewhere_only (fires in some environments, not others) | 192 | 150 |
| night_light (lamp left on all night, in some environments only) | 30 | 25 |
| flaps (> 6 firings in an hour, in some environments only) | 10 | 10 |
| night_cover | 0 | 0 |

By what the automation reads: sun 609, temperature 130, humidity 37, illuminance 15, weather 8 (multi-counted);
780 of the 799 are environment-coupled (64.3 % of the 1,213 that run), against 19 of the 4,721 that are not (0.4 %).
Of those 19, 12 read the calendar through a `now()` template (a month or a day-of-month test): the date axis changes
their behaviour for a calendar reason, not a physical one. By inspection, six read a sensor the census's name-token detector
does not count (names in other languages, or a sensor read where the detector does not look), among them the
mis-binds above; one is gated on a sun condition written in a form the detector misses.

**Static versus environmental.** 353 automations have an envelope kind in every environment where they run
(night_light 226, flaps 129): a defect whatever the environment, recorded as `static` and not as a divergence;
150 of them are also flagged for an environmental divergence.

**Weak findings.** 30 flagged automations fire on every report of a sensor (a `state` trigger with no `to`/`from`,
e.g. republishing a temperature to a display); 19 of them are flagged for time_spread only, which then measures
when the sensor first reports a change on the judged day, not a behavioural difference.

### Examples (described generically; the automations stay local)

1. **A sunrise-offset action** (sunrise + 30 min) fires at 08:59 in Stockholm in January and at 04:27 in July; in
   Singapore it fires between 07:17 and 07:42 on all four dates. Flagged time_spread: the kind behind 567 of the
   flagged automations (sun only, time_spread only).
2. **A "three hours before sunset" action** fires at 12:26 in Stockholm in January and 18:49 in July, against
   15:53-16:17 in Singapore.
3. **A lamp turned on at sunset, but only before 21:30**, never turns on in Stockholm in July (sunset is after
   21:30); everywhere else it turns on and nothing in the automation turns it off, so the lamp is left on all night
   (fires_somewhere_only, night_light, time_spread).
4. **A lamp turned on at 06:00 if the sun is still down** fires on every date in Singapore, in January, April and
   October in Minneapolis and Seattle, only in January and October in Phoenix and Stockholm, and in Sydney every date
   except January (its summer); where it fires the lamp stays on, a night_light that exists only in some seasons.
5. **A heating rule** (heat to 21.5 C when an outdoor temperature falls below 20 C) never fires in Singapore, and
   never in Minneapolis, Seattle or Stockholm in January: the temperature is below 20 C the whole time and a
   `numeric_state` trigger fires only on a crossing. It fires at 20:15 in Minneapolis in April and at 02:22 in
   Stockholm in July.
6. **A humidity hysteresis switch** (off above 70 %, on below 55 %, evaluated on every humidity report) flaps,
   more than 6 firings in an hour, in all eight Singapore environments and in Stockholm in July, not in Stockholm,
   Minneapolis or Seattle in January.
7. **Orientation**: a switch turned off when a room's humidity falls below 65 % fires at 11:12 in Sydney in April
   with the plan's north at 180 and not at all at north 0 (orientation changes the sun on the room's windows, and with
   it the room's temperature and relative humidity). Orientation is the minimal witness for 129 flagged
   automations (89 of them temperature-coupled, 31 humidity-coupled).
8. **A lamp turned off when the room's light exceeds 45 lx** fires at 08:58 in Stockholm in January and 04:03 in
   July; between the two orientations of one environment it moves by up to half an hour (06:28 and 06:57 in
   Minneapolis in April), below the 3 h threshold.
9. **A calendar test**: an action at 04:00 gated on `now().month` being a summer month fires only on the July date,
   in every city. EDT flags it (fires_somewhere_only on the date axis); the cause is the calendar, not the
   environment (see the 12 above).

### The negative control (pre-registered halt: > 5 % flagged among control automations that fire)

| Rung | Control automations run | Fire somewhere | Flagged | Halt |
|---|---|---|---|---|
| 4 | 3,466 | 1,247 | 0 (0.0 %) | no |
| 3 | 3,466 | 1,247 | 0 | no |
| 2 | 3,466 | 1,247 | 0 | no |
| 1 | 3,209 | 1,106 | 0 | no |

What this shows, and only this (A1.2): with no residents, the control's automations fire on the local clock alone,
so zero flags means local time lines up across all 48 environments and rungs (each trace starts at its site's local
midnight; with Addendum 3 every judged day is a Thursday or Friday). It does not test the binder's or the oracle's
treatment of sensors, and it excludes templates (A3.2), so it does not cover the calendar templates above.

### Closed-loop confirmation (open-loop screening versus executed actions)

A random sample (seed 0) of 200 flagged and 200 unflagged run automations, each run alone in a full session of the
standard home for three days: a flagged one in the environments where its screening outcome departed from its most
common one, an unflagged one in two random environments; 3,608 runs, none refused. Open and closed outcomes agree when
fired, flaps, night_light and night_cover are equal and the first firings are within one hour.

| Group | Runs | Agree | Rate |
|---|---|---|---|
| Flagged | 3,208 | 3,008 | 93.8 % |
| Unflagged | 400 | 394 | 98.5 % |
| All | 3,608 | 3,402 | **94.3 %** |

The 206 disagreeing runs come from only **14 automations** (11 flagged, 3 unflagged; 12 repositories), and each of
them disagrees in at least 90 % of its runs: by automation, 189 of 200 flagged and 197 of 200 unflagged agree
everywhere. Fields: fired 164, first_tod_h 180, night_light 102, flaps 26 (fired + first_tod_h + night_light 102,
fired + first_tod_h 62, flaps 26, first_tod_h 16). The dominant pattern (142 runs, 8 automations) is a lamp or plug
turned on under the condition that it is off, in 122 of those runs at a sun event: in the closed loop the automation
fires once in the whole run, before the judged days, and nothing turns the lamp off, so on the judged days the
condition fails and it never fires again; the open-loop screening, whose lamps never change, sees it fire every day. This is a real closed-loop effect
(self-disabling by its own action), not a harness error, and it means the open-loop screening over-reports firing for
automations that guard on their own target's state.

### The temperature envelope (closed loop only; causal)

209 of the 3,608 runs (22 automations) command a climate entity; their rooms give 1,463 room-run cells. A cell is a
violation only when the room leaves 10-30 C with the automation and not in the same environment's baseline run with
no automation (protocol section 4; the confirmation's causal rule).

- **Below 10 C: 108 caused room-run breaches in 20 runs, from 7 automations**, all flagged, all from **one
  repository**, all on 2026-01-14: Minneapolis 72 cells (14 runs), Stockholm 36 cells (6 runs). The 7 are variants of
  one pattern: on a cold day (an outdoor temperature below 10 or 12 C) at a fixed time, heat to 18-18.5 C for 45
  minutes to six hours, then set the thermostat to 7 C. In a Minneapolis or Stockholm January the 7 C setback lets the rooms fall
  to 5.3-9.9 C (median 8.4 C), 6.7-13.2 K below the same room in the baseline, whose coldest room-run is 15.6 C.
- **Above 30 C: 0 caused.** 236 room-run cells (47 runs, 13 automations) exceed 30 C with the automation, and every
  one of them also exceeds 30 C in the baseline (Singapore 105, Phoenix 56, Stockholm 39, Minneapolis 28, Sydney 8).
  This is the thermal model's known failure (validation T, `results-2026-11-04-environment.md`: sunny rooms overheat),
  so the above-30 envelope cannot be judged with this model; the causal rule keeps the failure from being reported as
  a finding, but it also hides any real overheating an automation would cause.

How far the below-10 C count depends on the model's thermal mass (post hoc): "Revision R2-a", item 10, at the end;
the same finding over every automation that commands a setpoint below 10 C rather than a sample: "Revision R2-c".

## RQ3: is the physics necessary? (the necessity ladder)

Pass criteria, pre-registered in Addendum 1, A1.4, verbatim: "Rung 0 matches rung 4 iff its repo-grouped AUC >= 0.90
and its held-out recall at precision 0.55 >= 0.80. A rung r in {1, 2, 3} reproduces rung 4 iff reproduced_share >=
0.90, firing-time MAE <= 0.5 h and the Spearman correlation of time spreads >= 0.90. The physics is necessary for
detection iff rung 0 does not match rung 4, and for quantification iff rung 1 or rung 2 does not reproduce rung 4.
Rung 3 reproducing rung 4 means the findings are robust to the daylight model's error."

| Rung | What it has | Result | Verdict |
|---|---|---|---|
| 0 | eight syntactic features of the automation as written (sun trigger, sun condition, numeric_state on illuminance / temperature / humidity / weather, time trigger, number of conditions); logistic regression | grouped AUC **0.939** [0.917, 0.956] (5 folds by repository, 821 repositories, 9,524 automations, base rate 0.084; 0 degenerate folds; random folds 0.947); held-out recall **0.926** at precision 0.646 (threshold set per fold for precision 0.55) | **matches** rung 4 |
| 1 | no site anywhere: sun refused, date unknown | flagged 0; reproduced_share **0.00** (MAE 0.04 h over 65,538 time pairs, rho -0.83) | does not reproduce |
| 2 | no site in the home (assumed indoor curve), the engine's sun known | flagged 615; reproduced_share **0.770**, MAE 0.030 h, rho **0.721** | does not reproduce |
| 3 | rung 4 with daylight scaled by 1/14.8 | flagged 798; reproduced_share **0.999**, MAE 0.004 h, rho 0.992 | **reproduces** |

Verdicts, by the pre-registered rules:

- **The physics is not necessary for detection.** A linter on eight syntactic features ranks which automations EDT
  will flag with AUC 0.939 and finds 92.6 % of them at 64.6 % precision. Most findings are sun rules (609 of 799),
  which are syntactically obvious; the spec's risk section anticipated this ("then the paper's claim is
  quantification").
- **The physics is necessary for quantification.** Without a site (rung 1) nothing is flagged; with the sun but
  without the environment model (rung 2) 23 % of the findings disappear: rung 2 flags 615, 602 of them sun rules,
  and loses almost all the sensor-driven ones (temperature 130 -> 1, humidity 37 -> 0, illuminance 15 -> 2,
  weather 8 -> 0), because indoor sensors read the same assumed curve in every
  place and season. Where both fire, the times agree (MAE 0.03 h), but the spreads rank differently (rho 0.72).
- **Robust to the daylight model's error.** Scaling daylight by 1/14.8 (validation D's measured error) changes one
  flag in 799. This is partly because few findings depend on modelled indoor illuminance (15 automations); it says the
  results do not hinge on the daylight model, not that the daylight model is right.

What this means for the paper's claim: EDT cannot claim that only a physical simulation can tell which automations
are place- or season-sensitive; a syntactic check does that nearly as well. What the simulation adds, and what the
ladder supports, is (a) the quantity: in which environments, at what local times and by how much an automation's
behaviour moves, and (b) the findings driven by sensed physical quantities (temperature, humidity, illuminance,
weather: 181 of the 799 flagged automations read one, 171 of them without the sun), which Home Assistant's own sun
model does not produce at all.

## RQ4: the environment cover

### Pre-registered: minority-exposure cover (protocol section 6, A1.3)

Computed on the training half of the repositories (429 flagged automations), reported on the held-out half (370):

| Set | Size | Coverage, train | Coverage, held-out | Coverage, all | Set-based, held-out |
|---|---|---|---|---|---|
| Greedy cover (target 80 %, target size 5) | **1** (Stockholm, January, north 0) | 81.8 % | **80.8 %** | 81.4 % | 0.0 % (one environment cannot show a divergence) |
| Random 5 (mean of 100 draws, seed 0) | 5 | - | 64.7 % | 65.6 % | 52.5 % (all: 53.5 %) |
| Six-city grid (6 cities x January and July x north 0) | 12 | - | 95.9 % | 96.7 % | 95.9 % (all: 96.7 %) |

**Non-degeneracy fails** (pre-registered: no single environment may expose more than 50 % of the flagged
automations): the largest single-environment share is **0.830** (Stockholm, July, north 180), and each of the four
Stockholm January and July environments exposes 81-83 % on its own; the next largest (Minneapolis, January) 0.53.
The cover is degenerate, as the spec's risk section anticipated ("one polar-midwinter environment covers
everything"); in this corpus it is the high-latitude extremes, winter or summer, because most findings are sun
times and Stockholm's are typically the earliest or latest of the 48. The cover is reported as degenerate, not as a
1-environment test suite.

### Post hoc (not pre-registered): greedy set-based cover

`posthoc_setcover.py` (local) runs the same greedy over set-based coverage (A1.3: a set covers an automation iff its
divergences restricted to the set are non-empty), which assumes no "neutral" majority behaviour:

| Step | Environment added | Set-based coverage, train | Set-based coverage, held-out |
|---|---|---|---|
| 1 | Phoenix, January, north 0 | 0.0 % | 0.0 % |
| 2 | Stockholm, July, north 180 | **84.6 %** | **87.8 %** |

Two environments reveal 84.6 % (train) and 87.8 % (held-out) of the flagged automations' divergences by themselves.
A single environment covers nothing under this criterion, so the first step is a tie broken by the sweep's environment
order (Phoenix, January is the first); the result is "Stockholm in July plus one contrasting environment", not a
claim about Phoenix.

### Witnesses

Every flagged automation has a one-axis witness (`no_witness` 0). The minimal witness (orientation first, then date,
then city) changes the **date** for 669, the **orientation** for 129, the **city** for 1. The most frequent witness
pair, Minneapolis January versus April (370), reflects the tie-break to the smaller environment key (Minneapolis sorts
first), not a property of Minneapolis.

## Controls: the oracle against known answers (protocol Addendum 4)

The negative control above shows that the harness is consistent (clock-only automations agree across
environments); it cannot show that a divergence the oracle reports is the one that is there. Two synthetic controls,
pre-registered with their pass criteria in Addendum 4 (commit `84452f4`) before either was run, measure that. Every
number below is in `results/edt/controls-ad4822b.json` (the synthetic automations, their per-environment
expectations, the sweep's outcomes and the scores; no corpus content).

- **Run**: commit `ad4822b`, `vesper edt controls --work <the sweep's work directory> --out FILE --workers 4`
  (`nice`), rung 4, all 48 environments, the standard home; every trace was reused from the real sweep's cache (none
  recorded; the rebuilt home has the same hash). Run once; nothing was changed after it.
- **Positive control**: 57 candidates, 13 dropped before the run by the pre-registered boundary rule (all for a
  condition boundary within 10 min of a firing in at least one environment), **44 run**. Expectations are computed
  without the replay: sun events from `vesper.env.sun.sun_events` plus the offset, Home Assistant's sun and time
  condition semantics, and for `numeric_state` Home Assistant's armed rule over the recorded trace's own reports.
- **Negative control**: 16 automations reading porch and indoor temperatures, humidities, illuminances and a motion
  sensor with thresholds or states outside every environment's range.

| Positive, by kind | Automations | Expected divergences | Detected | False positives | First firings compared | MAE (min) | Max (min) |
|---|---|---|---|---|---|---|---|
| sun trigger (sunrise/sunset, -70..+65 min) | 10 | 10 (time_spread) | 10 | 0 | 480 | 0.00 | 0.00 |
| time trigger, no condition | 5 | 0 | 0 | 0 | 240 | 0.00 | 0.00 |
| time trigger + sun condition | 6 | 5 (fires_somewhere_only) | 5 | 0 | 150 | 0.00 | 0.00 |
| sun trigger + time-window condition | 2 | 2 (fires_somewhere_only) | 2 | 0 | 22 | 0.00 | 0.00 |
| numeric_state (porch temperature at its pooled 5th-95th percentiles; living-room and porch illuminance) | 21 | 37 (18 fires_somewhere_only, 19 time_spread) | 37 | 0 | 490 | 0.00 | 0.00 |
| **All** | **44** | **54** (25 fires_somewhere_only, 29 time_spread, 0 flaps) | **54 (100 %)** | **0** | **1,382** | **0.00** | **0.00** |

Per environment, fired / not fired agrees in 2,112 of 2,112 (automation, environment) cells; no automation was
refused or failed in any environment; the expected and observed static kinds agree (none).

| Negative | Automations | Run in 48 environments | Fire anywhere | Flagged |
|---|---|---|---|---|
| numeric_state (temperature < -25 C, humidity < 2 % or > 100.5 %, illuminance > 10^7 lx or < -1 lx) | 11 | 11 | 0 | 0 |
| state (temperature `to: boiling`, illuminance `to: dark`, motion `to: jammed`) | 3 | 3 | 0 | 0 |
| time / sun trigger + an impossible numeric_state condition | 2 | 2 | 0 | 0 |
| **All** | **16** | **16** | **0** | **0** |

**Verdict against Addendum 4.** Positive: detection 54/54 = 100 % (>= 95 %), 0 false positives, first-firing MAE
0.00 min (<= 2 min): **PASS**. Negative: 0 of 16 flagged: **PASS**. No divergence was missed and none was reported
that is not there, so the controls revealed no fault.

What the controls do and do not establish:

- **Zero error is by construction where the two sides share an input.** The expected sun times come from the same
  `vesper.env.sun.sun_events` (astral) that the engine schedules from, and the numeric_state expectations from the same
  recorded trace the replay reads. The controls therefore check everything between those inputs and the oracle's
  verdict: offset arithmetic, per-day scheduling (including a late offset from the warm-up day), the alignment of
  local midnight across six time zones, the measured window, Home Assistant's sun and time condition semantics, the
  numeric_state armed rule, the replay's sensor delivery (0.05 s after `observed_t`), and the oracle's
  fires_somewhere_only and time_spread rules. They do not check the astral sun model or the environment model's
  physics (validations D and T do that).
- **Not exercised**: flapping (no candidate is expected to flap: the porch temperature reports on a change of up to
  about 1 C or hourly, never two reports within 7 min, so a threshold cannot be crossed into range seven times in an
  hour; the controls test only the absence of spurious flaps), the lamp and cover envelopes (every control acts on a
  smart plug, by design), and rungs 1-3.
- **The boundary rule cost the condition kinds most of their candidates**: 8 of 14 time-plus-sun-condition and 5 of 7
  sun-plus-time-window candidates were dropped. Across the 48 environments sunrise falls between 03:57 and 08:29 and
  sunset between 15:26 and 21:49 local, so almost every fixed clock time in those spans lies within 10 min of some
  environment's sun event. The two remaining sun-plus-time-window controls are a thin test of that combination.
- **The negative control's margins are wide**: over the 48 traces the sensors read 15.7-39.0 C indoors, -14.6-41.1 C
  on the porch, 8-100 % relative humidity and 0-106,260 lx, so the thresholds are far outside. It shows that binding,
  the sensors' reports and their delivery add no spurious firing or flag for sensor readers that cannot fire; it says
  nothing about readers near a threshold (that is the positive control's numeric_state kind).

## Deviations and disclosures

- **Addendum 3 (before the full run): dates and the negative control.** The plan's dates put the judged days on
  different weekdays, so weekday-gated automations diverged for a calendar reason (10 of 11 negative-control flags in
  a 10,000-automation smoke run). The dates moved to Wednesdays (2026-01-14, 04-15, 07-15, 10-14) and the control now
  excludes templates and actions on environment entities (the eleventh smoke flag read an illuminance template in an
  action). Weekdays are aligned; months and days of the month are not and cannot be on a date axis, which is why 12
  calendar-template automations are flagged (RQ2).
- **No product axis.** The spec's environment space includes the product (which sensor model reports the quantity).
  The standard home has one product per sensor role (every L1 Zigbee product of a role shares one reporting rule in
  the catalog, so variants would be identical rows), so the axis was dropped in the protocol (section 2) and no
  witness or cover uses it. The spec's claim that product reporting rules change outcomes is not evaluated here.
- **Gate-open assumptions (A2.1).** Helper and presence gates are held open in the mode their author gated for;
  1,038 bound automations (149 flagged) carry such an assumption. Every headline number is given over all bound
  automations and over the assumption-free subset (the funnel, the flag counts); the conclusions do not change.
- **Binder conventions (A2.5).** Outdoor names bind to the porch; semi-outdoor sensors are refused; derived,
  forecast and non-ambient temperatures and thresholds outside -30..35 C are refused; room-less names bind to the
  living room; several entities of one role and room bind to distinct devices (three lights and plugs per lit room).
  Audited precision is 87 % on the sample (RQ1), with residual mis-binds visible among flagged automations.
- **The thermal model.** Indoor temperature carries validation T's failure (sunny rooms overheat). It makes the
  above-30 C envelope unjudgeable (every raw breach is already in the baseline) and limits the temperature-coupled
  findings (130 flagged) to what a model with that bias produces; the below-10 C findings are driven by a 7 C setpoint
  the automations command, and the model's bias runs the other way (too warm).
- **Open loop versus closed loop.** The screening never executes actions, so it cannot see an automation change its
  own inputs or guards. The closed-loop sample agrees on 94.3 % of runs; the disagreements are concentrated in 14
  automations, mostly self-disabling ones (RQ2), which the screening over-reports as firing every day.
- **Not run**: the spec's held-out-city transfer for the cover (the protocol pre-registered only the held-out
  repositories); the intent-labelling study (spec section 4 item 6). Findings are divergences and envelope
  violations, not claimed defects against the authors' intent.
- **Ledger correction.** The run ledger's closed-loop line gives the below-10 C breaches as "Minneapolis 72,
  Stockholm 36 runs"; those are room-run cells. The runs are 14 and 6 (20 in all), from 7 automations in one
  repository.

## Revision analyses (post hoc, on cached results)

**Everything in this section is post hoc**: computed after the results above, for the review, from the same cached
results of commit `dc119cd` (sweeps at rungs 4-1, ladder, closed-loop run, the traces' cached weather); no sweep was
re-run. Script: `scripts/edt_analysis/revision_analyses.py`; every number is in `results/edt/analysis-dc119cd.json`
(aggregates only). Intervals: "repo-bootstrap" resamples whole repositories (1,000 resamples, seed 0, percentile 95 %
interval); "Wilson" treats units as independent.

**1. The time_spread threshold.** Recomputing the oracle's divergences with only the 3 h threshold changed:

| Threshold h | 0.5 | 1 | 2 | **3** | 4 | 6 |
|---|---|---|---|---|---|---|
| Flagged (any kind) | 807 | 807 | 802 | **799** | 794 | 529 |
| time_spread | 735 | 733 | 725 | **716** | 706 | 427 |
| time_spread only | 605 | 605 | 600 | **597** | 592 | 327 |
| Not environment-coupled flagged | 19 | 19 | 19 | **19** | 19 | 16 |

The count is flat from 1 h to 4 h (807 to 794); only at 6 h does it fall (529): the largest first-firing gap lies
between 4 and 6 h for 279 time_spread automations, 265 of which have no other kind. Leave-one-out at 3 h, over the
716 time_spread automations, dropping the environment whose removal shrinks the circular range of first-firing times
most: **693 (96.8 %) are still time_spread** (701 still flagged). The two orientations of a city and date give the
same sun times, so dropping one environment is a weak test; dropping the most extreme (city, date) leaves 675
(94.3 %), dropping the most extreme whole city leaves 431 (60.2 %): 40 % of the time_spread findings rest on one city
being in the set (Stockholm for 255 of the 285, Singapore for 24).

**2. Repository-clustered uncertainty and concentration.**

| Rate | Value | Repo-bootstrap 95 % | Wilson 95 % (independent) | Repositories |
|---|---|---|---|---|
| Flagged / run (799 / 5,934) | 13.5 % | [12.1, 15.2] | [12.6, 14.4] | 722 |
| Environment-coupled flagged / run (780 / 1,213) | 64.3 % | [59.8, 69.0] | [61.6, 67.0] | 369 |
| sun (609 / 895) | 68.0 % | [63.4, 73.2] | [64.9, 71.0] | 312 |
| temperature (130 / 214) | 60.7 % | [48.9, 71.1] | [54.1, 67.1] | 90 |
| humidity (37 / 63) | 58.7 % | [44.6, 72.1] | [46.4, 70.0] | 38 |
| illuminance (15 / 67) | 22.4 % | [8.2, 37.4] | [14.1, 33.7] | 42 |
| weather (8 / 9) | 88.9 % | [40.0, 100] | [56.5, 98.0] | 4 |

Distinct repositories behind the flags: time_spread 294, fires_somewhere_only 97, night_light 20, flaps 9; by what
the automation reads: sun 264, temperature 55, humidity 28, illuminance 8, weather 3, not environment-coupled 15. Flags
per repository (303 repositories with a flag): median 2, maximum 17 (2.1 % of the 799), top-10 repositories 15.3 %,
Gini 0.40 (0.75 over the 722 repositories with a run automation, zeros included). The largest pattern (sun only,
time_spread only, 567 automations) comes from 254 repositories (maximum 15, top-10 share 15.7 %); its 283
sunrise/sunset triggers with a non-zero offset come from 141 repositories (maximum 13, top-10 share 26.5 %), the 227
without an offset from 136, and 57 that read the sun through a condition or template from 41. The weather rate rests
on 4 repositories and the illuminance rate on 8 flagged repositories: their intervals are wide.

**3. Arithmetic reconciliation.**

- *Rung subsets.* The rung-2 flagged set (615) is a subset of rung 4's (799): 615 in both, 0 at rung 2 only, 184 at
  rung 4 only. Rung 3 (798) is a subset too (one rung-4 flag lost); rung 1 flags nothing.
- *184 versus 181.* They count different things. **181** flagged automations read a sensed quantity (temperature,
  humidity, illuminance or weather; 171 without the sun, 10 with it). **184** is the number of rung-4 flags rung 2
  loses: all 171 sensed-only ones, 7 of the 10 that read a sensed quantity and the sun, and 6 of the 19 that are not
  environment-coupled. The 799 split as sun only 599, sensed only 171, sensed and sun 10, not coupled 19; the 190
  flagged automations that do not read the sun are 171 + 19. Rung 2's 615 are 599 sun-only + 3 sensed-and-sun + 13
  not coupled ("602 sun rules" = 599 + 3).
- *Repository counts.* 1,186 is the collection's repository count (not recomputable from the sweep, which only holds
  repositories that contribute an automation); 1,123 repositories have an automation before de-duplication, **1,088**
  after it (each de-duplicated automation keeps one repository); **821** have a bound automation (the ladder's
  repository folds); **722** have a run automation (the denominator in RQ2's "303 of the 722"); **303** have a flagged
  one; 369 have a run environment-coupled automation (634 have an environment-coupled one, de-duplicated).
- *Ladder recall at precision 0.55.* The registered criterion is held-out recall >= 0.80 with the threshold set per
  fold for precision 0.55; pooled over the five held-out folds it gives 740 true positives of 1,145 predicted
  positive: recall 0.926 (740 / 799), realised precision 0.646. The realised precision is above 0.55 because each
  fold's threshold is set for at least 0.55.
- *Per-quantity runs, 1,248 versus 1,213.* 895 + 214 + 67 + 63 + 9 = 1,248 counts an automation once per quantity it
  reads; 35 run environment-coupled automations read two (illuminance + sun 20, temperature + weather 7, humidity +
  temperature 4, humidity + weather 2, sun + temperature 2), and 1,248 - 35 = 1,213 distinct. The same holds for the
  flagged per-kind counts (609 + 130 + 37 + 15 + 8 = 799 multi-counted; 780 distinct environment-coupled).
- *Table I's assumption-free rows over the full parsed population* (the published environment-coupled row divides by
  4,719 = 4,984 - 265, which shrinks the denominator to the automations without an assumption):

| Subset, assumption-free | Parsed | Bound | Run | Flagged |
|---|---|---|---|---|
| All | 43,280 | 8,486 (19.6 %) | 5,067 (59.7 % of bound) | 650 (12.8 % of run) |
| Environment-coupled | 4,984 | 1,102 (**22.1 %**, not 23.4 %) | 977 (88.7 %) | 633 (64.8 %) |
| Not environment-coupled | 38,296 | 7,384 (19.3 %) | 4,090 (55.4 %) | 17 (0.4 %) |

**4. Static classifications on random halves.** On 20 random halves of 24 environments (seed 0), all **353** static
automations stay static in every half: an envelope kind that holds in all 48 environments holds in any subset, so
this direction cannot fail. The non-monotone direction is the converse: of the 40 automations flagged for an envelope
kind (night_light 30, flaps 10), 0-6 per half (mean 1.9) hold it in every environment of the half and would be
static there, not flagged.

**5. Pair-based (set-based) cover.** A set S covers an automation iff some pair of environments in S shows one of its
divergences (equivalently, its divergences restricted to S are non-empty; the script checks the two agree). Greedy on
the training half (429 flagged), starting from the best of the 1,128 pairs, reported on the held-out half (370):

| Size | Greedy set (added in order) | Train | Held-out | Random sets, held-out mean [5th, 95th pct] (100 draws, seed 0) |
|---|---|---|---|---|
| 2 | Minneapolis 01-14 n0, Stockholm 07-15 n180 | 90.2 % | **88.9 %** | 15.9 % [6.8, 51.1] |
| 3 | + Sydney 04-15 n180 | 95.1 % | 93.0 % | 32.2 % [11.1, 88.6] |
| 4 | + Singapore 10-14 n0 | 97.9 % | 97.3 % | 44.1 % [14.3, 91.6] |
| 6 | + Seattle 10-14 n180, Seattle 01-14 n0 | 99.1 % | 98.1 % | 58.1 % [18.7, 93.5] |

A midwinter and a midsummer high-latitude environment together reveal the divergences of 89 % of the held-out
flagged automations, against 16 % for a random pair; the gap stays large up to six environments.

**6. Oracle blind spots.** Of the 433 environment-coupled automations that run but are not flagged (1,213 - 780):
**391 fire in no environment** (90.3 %), 30 fire everywhere with exactly the same first time, and 12 fire everywhere
with first times that differ by up to 3 h (4 up to 1 h, 5 by 1-2 h, 3 by 2-3 h); 10 of the 433 carry a static
envelope kind. Not in the cache: firing counts per environment (the sweep keeps per environment only whether it
fired, the first firing's time of day and the three envelope booleans), so automations that fire a different number
of times (below the flap threshold), or at different later times after an identical first one, cannot be counted
without replaying them.

**7. The self-state subset.** Syntactically (the bound automation's conditions name an entity its actions target,
e.g. "turn the lamp on if it is off"; service names excluded, template references included): **631 of the 5,934 run
automations (10.6 %) and 35 of the 799 flagged (4.4 %)**; a further 616 run (58 flagged) name an action target only in
a trigger (e.g. "off ten minutes after it turned on"). Of the 14 automations whose closed-loop runs disagree, **10**
are condition-type self-state (none trigger-only; 4 neither). In the closed loop, runs of condition-type self-state
automations agree in 63 of 227 (27.8 %), trigger-only ones in 207 of 207, all others in 3,132 of 3,174 (98.7 %).

**8. The closed loop's two arms.**

| Arm | Runs agree | Wilson 95 % (runs) | Repo-bootstrap 95 % (runs) | Automations agreeing everywhere (Wilson 95 %) | Repositories |
|---|---|---|---|---|---|
| Flagged | 3,008 / 3,208 = 93.8 % | [92.9, 94.6] | [89.0, 97.6] | 189 / 200 [90.4, 96.9] | 137 |
| Unflagged | 394 / 400 = 98.5 % | [96.8, 99.3] | [96.5, 100] | 197 / 200 [95.7, 99.5] | 143 |
| All | 3,402 / 3,608 = 94.3 % | [93.5, 95.0] | [90.0, 97.8] | 386 / 400 [94.2, 97.9] | 236 |

The run-level Wilson intervals are too narrow: runs of one automation are not independent (each disagreeing
automation disagrees in at least 90 % of its runs). The repository-clustered intervals are the honest ones.

**9. The below-10 C breaches (data).** The 20 breaching runs come from 7 automations (A1-A7) of one repository. Their
commanded setpoints are 18 and 7 C (A1-A3, A5, A6) or 18.5 and 7 C (A4, A7); the outdoor-temperature thresholds they
guard on are 12 C (A1) or 10 C (the others). A1, A2 and A5 breach in Minneapolis and Stockholm (4 runs each), A3, A4,
A6 and A7 in Minneapolis only (2 runs each).

| City (2026-01-14, both orientations) | Breaching runs / closed-loop runs of the 7 there | Room-cells | Outdoor mean (min), days 1-2 | Room minimum per run | Baseline coldest room |
|---|---|---|---|---|---|
| Minneapolis | 14 / 14 | 72 | -6.9 C (-14.7) | 5.3-9.4 C | 15.6-17.5 C |
| Stockholm | 6 / 14 | 36 | 1.8 C (0.5) | 8.0-9.5 C | 18.5 C |

Per run, the coldest breached room is 5.3-9.5 C (median 8.5) and the largest drop below the baseline 7.7-13.2 K. The
same 7 automations ran closed loop in nine other city-dates without a breach, among them Seattle in January (14 runs,
outdoor mean 7.5 C), Minneapolis in October (14 runs, 8.5 C) and April (12 runs, 14.2 C) and Stockholm in April and
October (8.8 and 10.8 C): the 7 C setback takes the rooms below 10 C only with a sub-zero or near-zero outdoor day.
Outdoor values: recorded weather (Open-Meteo ERA5) read from the traces' cache, every 10 minutes over the measured
days.

**Against the paper and the sections above.** (a) The post hoc set-based greedy above (Phoenix January, then
Stockholm July: 84.6 % / 87.8 %) is beaten by starting from the best pair (Minneapolis January with Stockholm July:
90.2 % / 88.9 %); its first step was a forced tie, and "two environments" understates what two can do. (b) The
assumption-free environment-coupled bind share is 22.1 % of 4,984, not 23.4 % of 4,719. (c) "Loses almost all the
sensor-driven ones" is exact for the 171 that read no sun (all lost); 184 is the number lost at rung 2, not a count of
sensor-driven flags. (d) Run-level closed-loop agreement intervals must be clustered: flagged 93.8 % [89.0, 97.6]
by repository. (e) The open-loop over-report of self-disabling automations is bounded: 35 flagged automations (4.4 %)
have the condition-type self-state shape. (f) Of the time_spread findings, 40 % depend on a single city being in the
set (leave-one-city-out; Stockholm alone for 255).

## Revision R2-a (post hoc): thermal mass and the below-10 C finding; humidity under a temperature error

**Post hoc, not pre-registered**, for the review (MUST 3, SHOULD 10). Unlike the analyses above these re-run
simulations: closed-loop runs and open-loop traces with two new sensitivity knobs of the indoor model,
`vesper.env.indoor.CAPACITY_SCALE` (every room's heat capacity x S) and `HUMIDITY_T_OFFSET_C` (kelvin added to the
temperature the relative humidity is computed at, nothing else), both set through `indoor.knobs` and restored after;
at their defaults (1, 0) the model is bit-identical to before (a pinned fingerprint test, `tests/env/test_indoor.py`).
Inputs: the local results of `dc119cd` (sweep, confirmation, their homes and the cached weather they read; never
fetched again). Script: `scripts/revision/r2a_thermal_humidity.py`; numbers in
`results/edt/thermal-mass-r2a-dc119cd-20261104.json` and `results/edt/humidity-offset-r2a-dc119cd-20261104.json`
(aggregates only).

**10. Thermal mass.** The 7 automations of item 9 (A1-A7) were run closed loop again (`vesper.edt.confirm.closed_loop`,
unchanged) in the six January environments the confirmation ran them in (Minneapolis, Stockholm, Seattle,
2026-01-14, plan north 0 and 180): 42 runs per scale, each scale with its own six baselines (no automation, same
scale), and the confirmation's causal rule (a room-run cell counts when the room's minimum over days 1-2 is below
10 C with the automation and not in the baseline). **At S = 1 the re-run reproduces the confirmation exactly**: 294
cells, every minimum and baseline minimum identical, no breach classified differently. Nominal C is EN ISO 13790's
"medium" class, 165 kJ/(m² K) of floor; S = 0.5 is about its "very light" class (80), S = 2 lies between "heavy"
(260) and "very heavy" (370), S = 4 is beyond every class.

| S (kJ/(m² K)) | Runs with a caused breach (of 42) | Minneapolis / Stockholm / Seattle (of 14 each) | Caused cells (of 294) | Automations | Coldest caused room per run: min / median / max | Baseline coldest room |
|---|---|---|---|---|---|---|
| 0.5 (82.5) | **28** | 14 / 14 / 0 | 194 | 7 | 3.3 / 6.1 / 9.4 C | 14.8 C |
| **1 (165)** | **20** | 14 / 6 / 0 | 108 | 7 | 5.3 / 8.5 / 9.5 C | 15.6 C |
| 2 (330) | **6** | 6 / 0 / 0 | 26 | 3 (A1, A2, A5) | 7.7 / 8.75 / 9.7 C | 16.8 C |
| 4 (660) | **0** | 0 / 0 / 0 | 0 | 0 | none (Minneapolis rooms reach 12.4-15.8 C) | 17.7 C |

No baseline room falls below 10 C at any scale, so every breach is caused. Seattle's January never breaches (coldest
room per run 11.7-13.7 C at S = 0.5); Stockholm's coldest room per run is 6.3-9.4 C at S = 0.5, 8.0-12.6 at 1,
11.6-14.8 at 2, 14.6-16.2 at 4. The largest drop below the baseline is 13.2 K at S = 0.5 and 1, 11.1 K at 2.
**The finding is not robust to thermal mass**: the mechanism (a 7 C setback on a sub-zero day lets the rooms fall
well below the baseline's) is there at every scale, but whether the rooms cross 10 C within the two measured days
is not. It survives a twofold heavier house only in Minneapolis (6 of 14 runs), and vanishes at fourfold.

*Time constants* (tau = C / H, H the room's loss to the outdoors: walls, windows, 0.5 air changes per hour; the
interior walls only move heat between rooms that are all cooling): the EDT home's rooms 38.7 h (the smaller bedroom),
44.5 h (office), 45.5 h (living room), 46.3 h (the larger bedroom), 56.7 h (kitchen), 64.5 h (bathroom), 76.4 h
(hallway); the whole house 48.9 h (13.7 MJ/K over 77.8 W/K); the validation's standard room 62.1 h. As an order of
magnitude (no standard source verified here), free-running dwellings decay with time constants of roughly 10-30 h for
light timber frame and 50-100 h or more for heavy masonry: **VESPER's nominal house sits between the two, at the
light end of masonry**, not at a short time constant. The timber-frame houses typical of Minneapolis would be at or
below S = 0.5 (more breaches); a heavy masonry house at S = 1-2 (fewer).

*Bias direction, plainly.* Two known errors of the model push the below-10 C count **up** (toward inflating the
finding): validation A's cold bias (the model's rooms 0.9-1.7 C colder than the measured ones, median RMSE 2.08 C)
and any thermal mass lower than the real house's. Against them, VESPER's tau of about 49 h is already longer than a
light timber frame's, which would push the other way for such a house. The count therefore rests on two uncertain
quantities and should be reported as "20 of 42 runs at the model's nominal mass, 6-28 for half to twice that mass,
none at four times", not as a fixed number.

**11. Humidity flags under a +-2 K temperature error.** The rung-4 humidity-coupled automations that ran (63: the
37 flagged and 26 unflagged) were replayed open loop (`vesper.edt.sweep`'s own worker) in all 48 environments against
traces recorded afresh with the temperature the humidity is computed at offset by -2, 0 and +2 K (the rooms' own
temperature and every other reading unchanged; the porch's outdoor humidity is not a room's and is unchanged). **Offset
0 reproduces the published sweep exactly** (3,024 automation-environment outcomes, every divergence kind identical).

| Offset | Flagged (37): flag lost / gained | Divergence kinds changed | Automations with any outcome change | Outcomes changed (of 1,776) exactly / beyond 1 h | Kinds after (published: time_spread 34, fires_somewhere_only 31, flaps 7) |
|---|---|---|---|---|---|
| -2 K | 1 / 0 | 2 | 34 | 721 / 543 | 35, 31, 6 |
| +2 K | 0 / 0 | 3 | 34 | 598 / 452 | 33, 31, 7 |

**36 of the 37 flagged humidity automations stay flagged under both offsets** (one loses its flag at -2 K); 4 change
which divergence kinds they show under one offset or the other. What changes a lot is *when* they fire: 34 of the 37
change at least one environment's outcome, and 25-31 % of their automation-environment outcomes move beyond the
confirmation's one-hour tolerance. The flag (that the automation behaves differently across environments) is robust
to a 2 K error in the room temperature; the per-environment behaviour (first firing times) is not. The 26 unflagged
ones fire in no environment, at any offset, so their stability says nothing. (Humidity's own model error, without the
temperature: `results-2026-11-04-environment.md`, "Revision R2-a".)

## ERA5 re-run (revision R-A; the numbers the paper reports)

Open-Meteo's archive was pinned to ERA5 (`0c7f1ff`, `models=era5`); rungs 4 and 3 were re-swept at `120f4ca` in a
fresh work directory, then the cover, ladder, closed-loop confirmation (same sample size and seed), controls, summary
and revision analyses re-ran unchanged. Rungs 1 and 2 read no weather, so their `dc119cd` sweeps are reused. Every
number is in `results/edt/summary-120f4ca.json` and `analysis-120f4ca.json`.

| Quantity | blended (`dc119cd`) | ERA5 (`120f4ca`) |
|---|---|---|
| parsed / bound / run | 43,280 / 9,524 / 5,934 | same |
| flagged (rung 4); env-coupled flagged of env-coupled run | 799; 780 / 1,213 | 797; 778 / 1,213 (64.1 %, repo bootstrap 59.5-68.9 %) |
| flagged of run, repo bootstrap 95 % | 13.5 % | 13.4 % [12.0, 15.1] |
| time_spread / fires_somewhere_only / flaps | 716 / 192 / 10 | 716 / 192 / 9 |
| temperature-coupled flagged | 130 | 128 |
| negative control (firing subset) | 0 / 1,247 | 0 / 1,247 |
| rung 3 flagged (reproduced) | 798 (0.999) | 796 (0.999) |
| rung 2 / rung 1 reproduced | 0.770 / 0.00 | 0.772 / 0.00 |
| rung 0 AUC [95 %]; recall at precision | 0.939 [0.917, 0.956]; 0.926 @ 0.646 | 0.945 [0.925, 0.962]; 0.927 @ 0.639 |
| pre-registered cover | 1 env (Stockholm Jan, n0), held-out 0.808, max share 0.83 (degenerate) | 1 env (Stockholm Jul, n180), held-out 0.854, max share 0.84 (degenerate) |
| post hoc pair cover (Minneapolis Jan n0 + Stockholm Jul n180) | train 0.902, held-out 0.889 | train 0.895, held-out 0.900 |
| six-city grid baseline, held-out | 0.959 | 0.981 |
| closed loop: overall / flagged / unflagged agreement | 94.3 / 93.8 / 98.5 % | 94.8 / 94.3 / 99.0 % |
| closed loop: flagged arm, repo bootstrap 95 % | [0.890, 0.976] | [0.899, 0.979] |
| below-10 C caused (sample): runs / cells / automations | 20 / 108 / 7 (1 repository) | 10 / 42 / 4 (the same repository) |
| above-30 C: runs (every one with a baseline above 30 too) | 47 | 112 |
| controls: positive; sensor-path negative | 54/54, 0 FP, MAE 0; 0/16 | same |

**The below-10 C count is a sample artefact as much as a weather effect.** The confirmation samples 200 flagged
automations with seed 0 from the flagged set; the set moved by two automations, so the sample changed (163 of 400
sampled automations shared). Of the 7 `dc119cd` breachers, 5 are in the ERA5 sample and 4 of them still breach (one
stops with ERA5 weather); no new breacher appears. A headline count must not depend on what a sample includes, so
revision R2-c (below) runs the whole population of automations that command a setback setpoint below 10 C, closed
loop, in every environment.

The upper envelope stays unjudgeable: every above-30 C cell has its no-automation baseline above 30 C as well (the
standard home has no cooling), and ERA5's warmer summer days double the count.

## Home variation (protocol Addendum 5; descriptive, no pass/fail threshold)

Pre-registered in Addendum 5 before any variant ran. The standard home and four variants of it, each swept once at
rung 4 over the same 48 environments and the same corpus, with the same recorded ERA5 weather:
- glazing x0.5 and glazing x2: every window pane's area halved or doubled, at the same place;
- plan seed 8: another generated floor plan with the same rooms and products;
- mirrored: the standard plan mirrored east-west.

The base is the standard home's rung-4 sweep at `120f4ca`; the variants ran at `6d22cf0` (whose standard home is
byte-identical to it). Script: `scripts/edt_analysis/home_variation.py`. Every number below is in
`results/edt/home-variation.json` (aggregates only). Every home binds the same automations to the same roles: 43,280
parsed, 9,524 bound, 5,934 run in each, and the comparison is over those 5,934. In every home the negative control
flags 0 of its 1,247 firing automations; no home halted.

| Home | Flagged | time_spread | fires_somewhere_only | night_light | flaps | Minimal witness: date / orientation / city |
|---|---|---|---|---|---|---|
| Standard | 797 | 716 | 192 | 30 | 9 | 667 / 129 / 1 |
| Glazing x0.5 | 785 | 698 | 182 | 30 | 9 | 673 / 111 / 1 |
| Glazing x2 | 785 | 700 | 193 | 30 | 9 | 667 / 117 / 1 |
| Plan seed 8 | 779 | 704 | 189 | 30 | 9 | 666 / 112 / 1 |
| Mirrored | 797 | 719 | 192 | 30 | 9 | 668 / 128 / 1 |

By environment kind as written (a flagged automation counts once in each of its kinds):

| Home | sun | temperature | humidity | illuminance | weather | none |
|---|---|---|---|---|---|---|
| Standard | 609 | 128 | 37 | 15 | 8 | 19 |
| Glazing x0.5 | 609 | 117 | 37 | 15 | 8 | 18 |
| Glazing x2 | 610 | 117 | 36 | 16 | 8 | 18 |
| Plan seed 8 | 609 | 111 | 37 | 15 | 8 | 18 |
| Mirrored | 609 | 128 | 37 | 15 | 8 | 19 |

Static findings (night_light, flaps in every environment) number 353 in every home.

**Across homes.** 800 automations are flagged in at least one home:

| Flagged in | All 5 homes | At least 3 | Only 1 |
|---|---|---|---|
| Automations | 768 (96.0 %) | 797 (99.6 %) | 3 (0.4 %) |

By number of homes, 1 to 5: 3, 0, 16, 13, 768. Of the standard home's 797 flagged, 768 (96.4 %) are flagged in every
variant.

Against the standard home, the flagged sets have these Jaccard indices: glazing x0.5 0.985, glazing x2 0.978, plan
seed 8 0.977, mirrored 1.000. The variants lose rather than add flags:

| Variant | Standard-home flags not flagged in the variant | Flags only in the variant |
|---|---|---|
| Glazing x0.5 | 12 | 0 |
| Glazing x2 | 15 | 3 |
| Plan seed 8 | 18 | 0 |
| Mirrored | 0 | 0 |

By kind, the share of automations flagged in all five homes:
- divergence kind: time_spread 93.2 % of 725; fires_somewhere_only 93.3 % of 194; night_light and flaps 100 %;
- environment kind: sun 609 of 610; humidity 36 of 37; weather 8 of 8; illuminance 15 of 16; **temperature 102 of 130
  (78.5 %)**.

The home-dependent flags are the temperature-reading automations. The sun-driven majority does not depend on the
plan, which is expected: the sun's times do not depend on the house.

**The orientation witnesses.** An automation has an orientation witness when two of its environments differing only
in plan north have different outcome classes. In this oracle "has an orientation witness" and "minimal witness is
orientation" are the same count, because orientation is tried first.

| Home | Automations with an orientation witness |
|---|---|
| Standard | 129 |
| Glazing x0.5 | 111 |
| Glazing x2 | 117 |
| Plan seed 8 | 112 |
| Mirrored | 128 |

How the standard home's 129 fare in each variant:

| Variant | Keep an orientation witness | Flagged, but only on another axis | Not flagged | Jaccard (witnessed sets) |
|---|---|---|---|---|
| Glazing x0.5 | 109 | 8 | 12 | 0.83 |
| Glazing x2 | 113 | 1 | 15 | 0.85 |
| Plan seed 8 | 109 | 2 | 18 | 0.83 |
| Mirrored | 126 | 3 | 0 | 0.96 |

Across all four variants, the 129 split as follows:

| Keep an orientation witness in | All 4 variants | 3 | 2 | 0 or 1 |
|---|---|---|---|---|
| Standard-home witnessed automations | **88 (68 %)** | 23 | 18 | none |

Over all five homes, 135 automations have an orientation witness somewhere: 88 in all five, 131 (97 %) in at least
three, 3 in only one.

By kind, of the standard home's 129, these keep the witness in all four variants:
- temperature: 54 of 90;
- humidity: 29 of 32;
- sun: 3 of 3;
- illuminance: 3 of 3.

Reading it (as Addendum 5 said it would be read):
- The flags are robust to the home: 96 % of the standard home's flagged automations are flagged in every variant,
  and almost all of the rest are flagged in three or four homes.
- The orientation findings are the most home-dependent result, as the paper says, but most are not one plan's
  artifact. Two thirds (88 of 129) keep an orientation witness in every variant, and every one keeps it in at least
  two.
- Mirroring the plan east-west changes almost nothing: the same 797 flagged, 126 of 129 witnesses kept. So the
  orientation sensitivity is not owed to the bedroom's window facing west rather than east.
- What does move the orientation findings is how much glass the home has and which rooms have which façade (glazing,
  the other plan). That moves the temperature-reading automations (54 of 90 stable) more than the humidity ones (29 of
  32).
- Five homes are a small sample of synthetic plans, not a population of real homes.

## Revision R2-c (post hoc): the setback population

**Post hoc, not pre-registered**, for the review (MUST 3). The below-10 C finding above rests on the confirmation's
seeded sample of 200 flagged automations, so it moves with the sample: 7 breaching automations on the blended-weather
sweep (`dc119cd`), 4 on the ERA5 sweep (`120f4ca`), mostly because the flagged set moved by 2 and the sample with it.
Here the population is fixed statically instead, and every member is run. Inputs: the local ERA5 sweep `120f4ca`
(rung 4), its home and recorded weather, the two confirmation reports, and the corpus (read-only, re-bound). Script:
`scripts/revision/r2c_setback_population.py` (the static rule is `vesper.edt.setback`, tested in
`tests/edt/test_setback.py`); numbers in `results/edt/setback-population-r2c-120f4ca.json` (aggregates only;
automations are A1-A22 in sorted id order, repositories R1-R6 in order of first appearance).

**1. The population (static, model-free).** An automation is in it when it runs at rung 4 and its bound actions
command a climate setpoint below 10 C: a literal `temperature` or `target_temp_low` below 10 in the data of a
`climate.*` action (nested `choose` / `if` / `repeat` steps included; a mode change counts only if it carries such a
value). **22 automations from 6 repositories** (18 flagged, 4 not), commanding **7 C (19) or 5 C (3)**. Re-binding
the corpus reproduces the sweep exactly (43,280 parsed; all 5,934 run automations re-bind to the sweep's bound
automation; the EDT home is byte-identical; the rule on the sweep's stored bound automations gives the same 22).
Context over the whole parsed corpus: 41 automations from 11 repositories command a literal setpoint below 10 C
(4-8 C), bound or not; the other 19 do not bind. Not counted: 386 automations whose climate setpoint is a template
(not evaluated statically) and 6 with a literal 32-50 (below 10 C only if meant in Fahrenheit).

**2. Closed loop on the whole population.** Every one of the 22 alone (`vesper.edt.confirm.closed_loop`, unchanged)
in all 48 sweep environments, 1,056 runs, each against its environment's baseline (no automation); the confirmation's
causal rule (a room-run cell counts when the room's minimum over days 1-2 is below 10 C with the automation and not in
the baseline; 7 indoor rooms per run, the hallway thermostat heating them all). The setback is the hours within days
1-2 that the last accepted setpoint command is below 10 C; the outdoor air is the cached ERA5 2 m temperature at the
site every 10 minutes over the setback. Check: for the 98 (automation, environment) pairs the `120f4ca` confirmation
also ran, all 686 cells are identical (minimum, baseline minimum, breach). No command was refused.

| A | Repo | Flagged | Setpoint | Runs with a setback (of 48) | Caused breach (of 48) | Where (2026-01-14) | Coldest caused room | Setback in breaching runs | Outdoor mean / min over it | Baseline coldest room |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 | R1 | no | 5 C | 0 | 0 | | | | | |
| A2 | R2 | yes | 7 C | 16 | 4 | M n0/n180, S n0/n180 | 5.2 C | 47.3 h | -6.4..2.1 / -13.6..0.9 C | 15.6 C |
| A3 | R2 | yes | 7 C | 22 | 4 | M, S (both) | 5.2 | 46.5 | -6.4..2.1 / -13.6..0.9 | 15.6 |
| A4 | R3 | yes | 7 C | 20 | 0 | | | | | |
| A5 | R2 | yes | 7 C | 8 | 0 | | | | | |
| A6 | R2 | yes | 7 C | 26 | 4 | M, S (both) | 5.3 | 46.5 | -6.4..2.1 / -13.6..0.9 | 15.6 |
| A7 | R2 | yes | 7 C | 22 | 4 | M, S (both) | 5.2 | 46.0 | -6.4..2.1 / -13.6..0.9 | 15.6 |
| A8 | R2 | yes | 7 C | 14 | 2 | M (both) | 8.7 | 36.0 | -6.0 / -13.6 | 15.6 |
| A9 | R2 | yes | 7 C | 14 | 4 | M, S (both) | 5.4 | 46.0 | -6.4..2.0 / -13.6..0.9 | 15.6 |
| A10 | R2 | yes | 7 C | 14 | 4 | M, S (both) | 5.4 | 46.0 | -6.4..2.0 / -13.6..0.9 | 15.6 |
| A11 | R4 | yes | 5 C | 40 | 1 | M n180 | 2.7 | 48.0 | -6.4 / -13.6 | 15.6 |
| A12 | R2 | yes | 7 C | 14 | 2 | M (both) | 9.2 | 36.0 | -6.0 / -13.6 | 15.6 |
| A13 | R5 | no | 7 C | 48 | 4 | M, S (both) | 3.4 | 48.0 | -6.4..2.1 / -13.6..0.9 | 15.6 |
| A14 | R6 | no | 5 C | 0 | 0 | | | | | |
| A15 | R2 | yes | 7 C | 26 | 4 | M, S (both) | 5.3 | 47.0 | -6.4..2.1 / -13.6..0.9 | 15.6 |
| A16 | R2 | yes | 7 C | 14 | 4 | M, S (both) | 5.4 | 46.0 | -6.4..2.0 / -13.6..0.9 | 15.6 |
| A17 | R3 | no | 7 C | 0 | 0 | | | | | |
| A18 | R2 | yes | 7 C | 14 | 2 | M (both) | 8.7 | 40.0 | -6.2 / -13.6 | 15.6 |
| A19 | R2 | yes | 7 C | 10 | 0 | | | | | |
| A20 | R2 | yes | 7 C | 12 | 4 | M, S (both) | 5.2 | 47.0 | -6.4..2.1 / -13.6..0.9 | 15.6 |
| A21 | R2 | yes | 7 C | 14 | 2 | M (both) | 8.7 | 38.0 | -6.1 / -13.6 | 15.6 |
| A22 | R2 | yes | 7 C | 22 | 4 | M, S (both) | 5.2 | 46.0 | -6.4..2.1 / -13.6..0.9 | 15.6 |

(M Minneapolis, S Stockholm; "both" is plan north 0 and 180; outdoor ranges run from Minneapolis to Stockholm.)

**Overall: 53 of 1,056 runs have a caused breach (315 of 7,392 room-run cells), from 16 automations in 3
repositories (R2 14, R4 1, R5 1)**, all on 2026-01-14: Minneapolis 31 runs (15 at n0, 16 at n180), Stockholm 22
(11 + 11); no other city or date. The coldest caused room per run is 2.7-9.8 C (median 8.1 C), up to 15.7 K below
the same room in the baseline; no baseline room falls below 10 C anywhere (coldest 15.6 C in these environments). The
breaching runs' setbacks last 36-48 of the 48 measured hours, with outdoor air averaging -6.4 to 2.1 C over them
(minimum -13.6 to 0.9 C). A1, A14 and A17 never command their setback within the measured days in any environment; A4, A5 and A19 command it
but never breach.

*The near-model-free condition.* **All 53 breaching runs (100 %) have an outdoor mean below 10 C over the setback**:
that is the condition under which any heat-loss model moves an unheated room toward the outdoor air and so toward a
5-7 C setpoint. It is necessary, not sufficient, within two days: 148 other runs also set back with the outdoor mean
below 10 C and do not breach. Per city and date, runs with a cold setback (outdoor mean below 10 C) and how many
breach: Minneapolis January 35 / 31 (outdoor -12.6 to -6.0 C);
Stockholm January 34 / 22 (2.0-2.9 C); Seattle January 35 / 0 (4.5-7.1 C; coldest room 12.4 C); Minneapolis
October 33 / 0 (7.7-8.6 C); Seattle April 32 / 0, Stockholm April 22 / 0, Minneapolis April 10 / 0 (4.1-8.7 C). So
the rule and the climate decide *that* the rooms head for the setpoint; the model decides *whether* they get below
10 C within the 48 measured hours, which takes a sub-zero or near-zero outdoor mean and a setback of 36 h or more.

**3. Mass sensitivity on the same population.** Step 2 again in the four environments with a breach (Minneapolis and
Stockholm, 2026-01-14, plan north 0 and 180), all 22 automations (88 runs per scale), every room's heat capacity
x S (`vesper.env.indoor.knobs(CAPACITY_SCALE=S)`), each S with its own four baselines. **S = 1, run afresh,
reproduces step 2 exactly** (88 runs, 616 cells: every minimum, baseline minimum, breach and setback interval
identical).

| S (kJ/(m² K)) | Runs with a caused breach (of 88) | Minneapolis / Stockholm (of 44 each) | Caused cells (of 616) | Automations | Coldest caused room per run: min / median / max | Baseline coldest room | Largest drop below baseline |
|---|---|---|---|---|---|---|---|
| 0.5 (82.5) | **63** | 33 / 30 | 439 | 17 | -2.9 / 5.8 / 9.4 C | 14.7 C | 21.8 K |
| **1 (165)** | **53** | 31 / 22 | 315 | 16 | 2.7 / 8.1 / 9.8 C | 15.6 C | 15.7 K |
| 2 (330) | **20** | 20 / 0 | 67 | 12 | 7.7 / 9.0 / 9.9 C | 16.8 C | 11.2 K |
| 4 (660) | **0** | 0 / 0 | 0 | 0 | none (rooms 12.4-18.7 C) | 17.7 C | |

No baseline room falls below 10 C at any scale. As in R2-a, whether the rooms cross 10 C within two days depends on
the mass: the population count runs 63 / 53 / 20 / 0 for half, nominal, twice and four times the mass, Stockholm
drops out at twice, everything at four times.

**4. Reconciliation with the confirmation samples.** Every confirmation breacher is in the population: **7 of 7** on
`dc119cd` (A6, A7, A8, A12, A16, A18, A21) and **4 of 4** on `120f4ca` (A7, A8, A12, A21); none is outside it, so no
other mechanism (turning the heating off, a templated setpoint) produced a confirmation breach. The `dc119cd` sample
held 8 of the 22 (the 7 breachers and A19, which never breaches); the `120f4ca` sample held 6 (A6, A7, A8, A12, A15,
A21), all of which breach here, but A6 and A15 were not run in a cold January environment: the confirmation runs a
flagged automation only in the environments where its screening outcome departs from its most common one, and on the
ERA5 sweep Minneapolis and Stockholm in January were not among those for these two. So the sample's 4 versus 7 is
the sample (which automations, and which environments per automation), not the physics: the population count on the
ERA5 sweep is 16 automations, of which the `120f4ca` sample missed 10 and the `dc119cd` sample 9.

**The finding, reframed (MUST 3).** 22 automations from 6 repositories command a setback setpoint of 5 or 7 C, below
the 10 C envelope; in Minneapolis and Stockholm in January, with outdoor air averaging -6.4 to 2.1 C over a setback
of 36-48 hours (minimum -13.6 to 0.9 C), 16 of them (3 repositories) let the rooms fall to 2.7-9.8 C (median 8.1 C;
nominal mass) in 53 of 1,056 runs, while no room of the same home without them falls below 15.6 C; with half the
mass 63 of the 88 cold-January runs breach, with twice the mass 20 (Minneapolis only), with four times none. The
rule and the climate set the direction; the thermal model sets how cold and how fast.

## Revision R4-C (post hoc): binder audit on the flagged set, recall, dead rules, scope, reproducibility

**Everything in this section is post hoc** (review 2, items 2, 8, 9 and 11), on the ERA5 rung-4 sweep at `120f4ca`; no
sweep was re-run. The per-automation labels name corpus entities and stay outside the repository; only counts are
here. Intervals: Wilson 95 % unless marked "repo bootstrap" (1,000 resamples of whole repositories, seed 0).
Reproducibility facts and the referee's arithmetic are in `docs/experiments/edt-reproducibility.md`.

### 1. The binder on the 179 sensor-driven flags (item 11)

All 179 flagged automations that read temperature, humidity, illuminance or weather (`analysis-120f4ca.json`,
`3_reconciliation.sensor_driven`) were labelled by hand from their YAML and entity names: is every bound role the role
the author meant, in the medium and the room the author meant? "Correct by convention" is a room-less name, or a room
the standard home lacks (a den, "upstairs", a cinema room), bound to the living room as A2.5 prescribes, with nothing
in the automation placing it elsewhere; the verdicts follow the A2.5 audit's for the names both audits saw (one
exception, below).

| Verdict | Automations |
|---|---|
| correct (the name says role and room) | 75 |
| correct by convention | 44 |
| mis-bind | **51** |
| uncertain (four bare board names, a redacted name, two "loft" sensors that may be an attic, a 12-18 C band that may be a cellar, a lux sensor that may be outdoors) | 9 |

- **Binder precision per automation: 119 / 170 = 70.0 % [62.7, 76.4]** (uncertain excluded); 119 / 179 = 66.5 %
  [59.3, 73.0] with the uncertain counted as wrong. This replaces A2.5's 87 % on 54 names as the precision of the
  bindings that produce findings.
- **Share of the sensor-driven flags attributable to a mis-bind: 51 / 179 = 28.5 % [22.4, 35.5]; repo bootstrap
  [18.8, 39.2]** (33 of the 83 repositories behind these flags). Of all 797 flags, 51 (6.4 %).
- **By class:**

| Class | Automations | The flag is |
|---|---|---|
| a heated or closed enclosure read as a room (server room or closet, comms cupboard, rack, humidor, deep freezer, 3D-printer filament box, grow box) | 14 | an artefact |
| indoor and outdoor swapped: a room sensor named `weather_<room>` or an Aqara `lumi.weather` sensor bound to the porch (7), an outdoor station or balcony sensor bound to a room (3) | 10 | an artefact |
| water or a bath read as room air (hot tub, sauna, fermenting beer, heating water) | 5 | an artefact |
| an unconditioned or semi-outdoor space read as the living room (attic, conservatory) | 3 | an artefact |
| a derived value read as relative humidity ("excess humidity") | 1 | an artefact |
| **wrong medium or quantity, together** | **33 (18.4 % [13.4, 24.8])** | |
| right medium, wrong room: a room word the binder lacks in the entity's own name (French *salle de bain*, *ensuite*, *nursery*, *masterbedroom*, German *Bad*) | 5 | likely to survive (another room of the same home) |
| right medium, wrong room: the room is named only in the alias or a companion entity (a bathroom fan's room-less humidity sensor, a study's sensor, a child's bedroom) | 13 | likely to survive |

  By quantity (an automation can read several): temperature 32 of 128, humidity 16 of 37, illuminance 3 of 15,
  weather 7 of 8 (the `weather_<room>` names). By flag kind: 47 of the 51 are `fires_somewhere_only`, 49
  `time_spread`, 2 `night_light`.
- **Two binder rules generate most of the artefacts:** a `weather` token sends a name to the porch even when the rest
  of the name is a room (`weather_<room>`, Aqara's model id `lumi.weather`), and no rule recognises an enclosure
  (server, comms, rack, humidor, freeze, filament box, grow box) as non-ambient.
- **Against A2.5.** The A2.5 audit's pass 1 and pass 2 used the same frame (every ambient name bound in the first
  10,000 de-duplicated automations in file order); pass 2 re-audited it after the fixes pass 1 prompted, so its
  87 % is a within-sample figure after tuning, not an independent estimate. This audit is over automations, not names,
  over the whole corpus. One label differs from A2.5: an office humidity sensor A2.5 called correct is, by its
  automation (an alert above 30 % on a 3D printer's filament box), an enclosure.

**Recall (a labelled sample of refusals).** The binder refused 3,617 of the 4,984 environment-coupled automations.
A seeded sample of 100 (Python `random.sample` over the sorted hashes, seed 20261105) was labelled: did a correct
binding to the standard home, as it is, exist? The 33 refusals whose reason could hide a miss (a name with no role, a
Fahrenheit or non-ambient threshold, a semi-outdoor space, a collapse, a device step) were read in full; the 67
refused by design (a helper or presence entity, an unmodelled domain, a cover, a scene) were checked entity by entity.

| Label | Automations |
|---|---|
| a correct binding existed with the home's roles as they are (Danish *bevægelse* twice, German *Belegung*: motion words the binder lacks; an upper sentinel threshold of 100 beside a real 34 C one) | 4 |
| one existed only with a value translation (three Fahrenheit thresholds, a door as a text sensor Closed/Open, two light groups) | 6 |
| none existed | 90 |

- **False-refusal rate: 4 % [1.6, 9.8] strict, 10 % [5.5, 17.4] with translations.**
- **Recall estimate**, recall = TP / (TP + FN) with TP = 1,367 bound x precision and FN = 3,617 x false-refusal
  rate, using the 70.0 % above as the precision of all bound environment-coupled automations (a proxy: those include
  many sun-only automations whose bindings are lamps and motion sensors, so it is likely low): **0.87 strict
  (0.71-0.95 over both intervals), 0.73 with translations (0.58-0.84)**.
- **Disjoint from A2.5:** yes in units. 27 of the 100 lie in A2.5's 10,000-automation frame, but A2.5 audited bound
  sensor names only and never a refused automation.

### 2. The dead-rule audit (item 2)

391 environment-coupled automations run and fire in no environment (`analysis-120f4ca.json`,
`6_blind_spots.classes.fired_in_no_environment`). A seeded sample of 100 (seed 20261104; 70 repositories) was
labelled by why it never fires, from its bound YAML and the range each bound sensor reports over the 48 judged
windows:

| Why it never fires | Automations |
|---|---|
| missing occupancy or presence: a motion, door, doorbell or lock event a resident makes | **55** [45.2, 64.4] |
| a needed event never happens: a device state a resident or another rule sets (a lamp, plug or thermostat turned on or off by hand) 17, Home Assistant starting 3, a sensor coming back from unavailable 1 | **21** [14.2, 30.0] |
| a binding or standard-home artefact: an outdoor, shed or unheated-room sensor bound indoors so its threshold is never crossed (6), a window sun sensor bound as room light, a Fahrenheit threshold inside a template, a text-state sensor bound as numeric humidity, a diagnostic sensor bound as motion (1 each); the home has no covers (1) or no cooling (2) | **13** [7.8, 21.0] |
| a calendar condition outside the four swept dates (two once-a-year reminders, one weekend-only rule; all four dates are Wednesdays) | 3 |
| **genuinely dead** | **6 [2.8, 12.5]** |
| other: an empty action list, the only action disabled | 2 |

Of the six genuinely dead rules, five cannot fire in any home as written (author errors: a numeric window above 75
and below 10; a motion sensor required to go from state "22:30" to "06:30"; a template that yields "ture"; climate
states "on", "cooling" and "heating", which a climate entity never takes), and one is a condition unsatisfiable at
these six sites on these dates (sunrise minus 15 minutes after 08:30). None in the sample has a threshold that is
unreachable for a correctly bound sensor, and no armed crossing is impossible except where a mis-bound sensor never
crosses (counted as an artefact). Scaled to the 391: about 23 genuinely dead rules (11-49), about 215 waiting for a
resident and 82 for an event, 51 artefacts.

### 3. Scope (item 8)

- **Covers.** 1,116 of the 43,280 automations command a cover (a cover service or a cover device action); 361 of
  them are environment-coupled, 292 read the sun. The binder refused 648 automations naming a cover as its reason
  (611 "the standard home has no cover in <room>", 37 cover device steps); 403 of these command a cover, 245 read
  one. These are in the class "the standard home has no cover or climate in that room" (616) and the device-step
  classes, **not** inside "an entity domain the standard home does not model (6,386)": the binder knows the cover
  role, the home has no cover. 702 cover-commanding automations are refused first for another reason, 11 bind (their
  cover command targets no cover entity: `all`, a template or a blueprint input), 4 of them run.
- **Blueprints.** The referee's 165 is not the corpus's blueprint count: it is the blueprint *definitions* that bound
  and were refused by the engine for their `blueprint:` key. The 43,280 hold **1,416 blueprint definitions** (3.3 %,
  309 repositories): 794 refused for `!input`, 457 for another binder reason, 165 by the engine; none runs.
  **Blueprint instances** (`use_blueprint`) are not automations to the corpus loader: 2,224 (2,216 distinct) in 263
  repositories, none in the 43,280. So shared blueprints, definitions and instances, are outside the evaluable
  population entirely.
- **Aqara RTCGQ11LM illuminance.** Yes, it is motion-gated. zigbee-herdsman-converters 26.123.0 (the catalog's
  source) handles its illuminance cluster with `lumi_illuminance`, whose comment reads "there is no illuminance
  without movement" (issue Koenkk/zigbee-herdsman-converters#1925) and which publishes occupancy with every
  illuminance report; otherwise the device's roughly hourly heartbeat (`genBasic` 0xFF01, key 11, read for this
  model only) carries an illuminance value. The catalog record has the library-default reporting rule (illuminance
  min 10 s, max 3600 s, any change), so the twin reports a free-running light level the real sensor would not send in
  a home with no residents. **It affects all 67 run illuminance-reading automations**: every illuminance sensor of the
  standard home is this product (`vesper/edt/home.py`, `PRODUCTS`). 25 of the 67 read illuminance in a trigger (12
  flagged), whose first firing could move by up to the heartbeat interval; 42 only in a condition (3 flagged), which
  would read a value up to an hour old.

### 4. Arithmetic (item 9)

3,590 is right (9,524 bound - 5,934 run; the refused-reason table sums to 3,591 because one automation carries two
reasons); 42,031 per minute is over the 652.6 s environment phase and 36,768 over the 746 s wall; "Random 5" is
65.1 % under minority exposure and 52.6 % under pair coverage, both from the same 100 draws. Details:
`edt-reproducibility.md`.
