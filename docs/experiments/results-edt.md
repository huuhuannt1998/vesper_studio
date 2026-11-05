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
none at four times", not as a fixed number. **Superseded:** these counts are the blended-weather (dc119cd)
sample's 7 automations; the paper reports R2-c's population-wide, ERA5 counts instead (53 of 1,056 runs at nominal
mass; 63/53/20/0 runs for half, one, two and four times the mass), and R4-B's error-budget corners.

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

## Revision R4-A (Addendum 6): the frame-relative relation, the dead-rule class, rung 2.5, the richer outcome

**Post hoc in the paper's terms.** Addendum 6 (with A6.9) was written after review 2 and before any run under it.
Code: commit `8fcd672`. Every number below is in `results/edt/summary-8fcd672.json` and `analysis-8fcd672.json`,
produced by `scripts/edt_summary.py --commit 8fcd672 --workers 5` and
`scripts/edt_analysis/r4a_analyses.py --commit 8fcd672`. The runs used a fresh work directory (only the `120f4ca`
ERA5 cache copied in), 5 workers, under nice.

### The check (A6.8): passed

Rung 4 was re-swept with the `120f4ca` binder and the catalog's reporting rule. The clock relation gives **797
flagged automations, the same set with the same kinds as `120f4ca`**:

- 0 only-new and 0 only-old flags;
- by kind: time_spread 716, fires_somewhere_only 192, night_light 30, flaps 9;
- yield 43,280 / 9,524 / 5,934;
- negative control 0 / 1,247.

The richer cache and the new relation change nothing in the old outcome.

### What A6.9 changes (the binder fixes and rho_p)

| Run (rung 4) | bound | run | clock flags | frame flags |
|---|---|---|---|---|
| `120f4ca` binder, catalog rule (the check) | 9,524 | 5,934 | 797 | 207 |
| current binder, catalog rule | 9,515 | 5,926 | 790 | 200 |
| current binder, device rule (the main run) | 9,515 | 5,926 | 790 | 199 |

- **Binder fixes.** Of the 179 sensor-driven flags of `120f4ca`:
  - 7 are now refused as "enclosure or medium, not room air";
  - 2 are bound to another room (the weather-token rule) and stay flagged;
  - none is bound and no longer flagged.

  The two rules therefore remove 7 of the 33 wrong-medium artefacts R4-C labelled. The other 26 are not recognisable
  from their entity names (the audit placed them from the alias, the automation or a companion entity), so a name rule
  cannot reach them.
- **The RTCGQ11LM's real reporting rule (rho_p)** changes no clock-relation flag among the 67 run automations that read
  illuminance: 15 are flagged under both rules, and one changes its kinds. It changes one frame-relation flag overall.

  Under the device rule the resident-free home's illuminance arrives only on the hourly heartbeat. A first firing can
  move by up to that hour, which is below the 3 h tolerance; a fires/does-not-fire outcome hardly moves at all.

  So the reporting function is now exercised and its measured effect on these findings is nil. The illuminance
  findings are not artefacts of the library default. Of the 67, 23 read illuminance in a trigger.

All numbers below are from the main runs: current binder, device rule, sweeps `rung{4,2.5,2,3,1}-8fcd672`.

### Item 1: the frame-relative relation

| | clock relation | frame relation |
|---|---|---|
| flagged (of 5,926 run) | 790 (13.3 %, repo bootstrap [11.9, 15.0]) | **199 (3.4 %, [2.6, 4.3])** |
| env-coupled flagged / env-coupled run | 771 / 1,205 | 184 / 1,205 (15.3 %, [11.7, 19.4]) |
| time_spread | 709 | **8** |
| fires_somewhere_only | 185 | 185 |
| night_light / flaps | 30 / 8 | 30 / 8 |
| repositories with a flag | 302 | 102 |

- **The sun-only, time_spread-only class.** 567 automations at `120f4ca`; 565 of them are no longer flagged. The 2
  that remain have a sun spread above 3 h in their own frame.
- **Transitions.** 195 automations are flagged under both relations and 595 only under the clock relation. **4 are
  flagged only under the frame relation**: their first firings agree on the clock within 3 h but lie more than 180 min
  apart from the sun event they name. So the frame flags are not strictly a subset of the clock flags.
- **Sensitivity (not a criterion).** If the sensed frame is not counted as invariant when a computable frame exists,
  there are 202 flags (time_spread 26), 3 more.
- **Frames of the 199 flags:**

  | Frames | Flags |
  |---|---|
  | sensed | 94 |
  | clock+sensed | 48 |
  | clock+sun | 28 |
  | clock | 13 |
  | sun | 10 |
  | sensed+sun | 5 |
  | clock+sensed+sun | 1 |

  Of the 5,926 run automations, 2,727 declare no frame, and 7 have a sun frame with no computable reference.
- **Witnesses under the frame relation** (minimal: orientation 100, date 98, city 1; 0 with no witness). By the set
  of axes admitting one: all three 100, date+city 84, date 14, city 1. Under the clock relation on the main run the
  minimal witnesses are date 666, orientation 123, city 1. Every frame flag has a pair witness, so the possible failure
  of Proposition 1 for mixed frames does not occur in this corpus.
- **Per quantity, frame relation** (flagged of run reading it, repo bootstrap 95 %):

  | Quantity | Flagged / run | Rate | Repo bootstrap 95 % |
  |---|---|---|---|
  | sun | 44 / 895 | 4.9 % | [3.4, 6.9] |
  | temperature | 104 / 206 | 50.5 % | [37.4, 63.4] |
  | humidity | 36 / 63 | 57.1 % | [43.3, 70.6] |
  | illuminance | 7 / 67 | 10.4 % | [1.6, 19.7] |
  | weather | 8 / 9 | uninformative (n < 30) | |

  The finding moves from "the sun" to the sensed quantities.

### Item 3: rung 2.5 and the sensor-driven split

Sensor-driven flags are those flagged at rung 4 that read a sensed quantity as written. Rung 2.5 gives the site and
ERA5 to the porch only; every indoor room reads rung 2's assumed curve.

| | clock relation | frame relation |
|---|---|---|
| sensor-driven flags (main rung 4) | 172 (the 179 less the 7 enclosures) | 146 |
| **ERA5-only** (still flagged at rung 2.5) | **47** (46 with the same kinds) | **42** (42) |
| **room-model-dependent** | **125** | **104** |
| ERA5-only by quantity | temperature 38, illuminance 8, weather 6, humidity 1 | temperature 38, weather 6, illuminance 4, humidity 0 |
| ERA5-only naming a porch sensor | 46 of 47 | 42 of 42 |
| also flagged at rung 2 | 2 | 0 |

So about a quarter of the sensor-driven findings rest on ERA5 outdoor air alone (validated to 0.75 °C RMSE on home
A), almost all through the porch. The rest depend on the room model.

### Item 2: the dead-rule class

**3,584 of the 5,926 run automations fire in no environment; 390 of them are environment-coupled.** Reasons are
derived mechanically (A6.6, probes over all 48 rung-4 traces) and are not hand-audited:

| Reason | All dead | Environment-coupled |
|---|---|---|
| trigger entities never change (no residents; open loop) | 2,762 | 240 |
| a condition never holds when a trigger fires: on an entity that never changes | 119 | 52 |
| ... a template or another condition | 120 | 21 |
| ... a time condition | 89 | 3 |
| ... a sensed-quantity or sun-state condition | 4 | 3 |
| ... several conditions | 11 | 1 |
| threshold out of the observed range in every environment | 15 | 15 |
| never armed (always on the firing side) | 11 | 11 |
| the actions end before any call | 24 | 7 |
| not derived | 429 | 37 |

No dead automation has a sun condition as its single blocker. "Armed, never crosses in the judged window" does not
occur. The 26 environment-coupled threshold reasons (15 unreachable + 11 never armed) are the candidate "cannot happen
in this climate" rules. R4-C's audit found that its sample's unreachable thresholds were mis-binds, so these need that
audit before they count as findings.

### Item 7: the ladder and the cover, restated

**Per-rung run counts and flags:**

| Rung | Run | Clock flags | Frame flags |
|---|---|---|---|
| 4 | 5,926 | 790 | 199 |
| 3 | 5,926 | 789 | 200 |
| 2.5 | 5,926 | 659 | 93 |
| 2 | 5,926 | 614 | 51 |
| 1 | 4,566 | 0 | 0 |

**Against rung 4** (A1.4's reproduction rule):

| Rung | Clock relation: reproduced, MAE, rho | Frame relation: reproduced share | Reproduces? |
|---|---|---|---|
| 3 | 0.999, 0.006 h, 0.987 | 0.995 | yes / yes |
| 2.5 | 0.834, 0.024 h, 0.790 | 0.462 | no / no |
| 2 | 0.777, 0.031 h, 0.721 | 0.251 | no / no |
| 1 (sanity check) | 0.00 | 0.00 | no / no |

- **Necessity, restated on rung 2 alone:** rung 2 does not reproduce rung 4 under either relation, so **the physics is
  necessary for quantification**. Under the frame relation the gap is far larger: rung 2 finds a quarter of the
  findings. Rung 2.5 sits between rungs 2 and 4.
- **Rung 0, refitted** (A1.4's model, repo-grouped 5-fold CV, repo bootstrap 95 %):

  | Flags | Population | 8 features: AUC | + 4 frame features: AUC |
  |---|---|---|---|
  | frame | run (5,926; 199 flagged) | **0.782** [0.711, 0.851] | 0.919 [0.884, 0.948] |
  | frame | env-coupled run (1,205; 184) | **0.783** [0.714, 0.838] | 0.831 [0.778, 0.875] |
  | clock | run (5,926; 790) | 0.946 [0.929, 0.962] | 0.981 [0.975, 0.986] |
  | clock | env-coupled run (1,205; 771) | 0.847 [0.809, 0.883] | 0.846 [0.808, 0.882] |

  - Held-out recall at precision 0.55, frame flags, 8 features: 0.63 at a realised precision of 0.41 on the run
    automations; 0.78 at 0.29 on the environment-coupled ones.
  - The bound-population (A1.4) clock fit on the main run is 0.942.
  - Under the frame relation the eight syntactic features no longer match rung 4 (AUC 0.78, below A1.4's 0.90). With
    the frame features, which are themselves syntactic, the AUC rises to 0.92 over all run automations, mostly by
    telling sensed-frame rules from the rest. Within the environment-coupled run automations it stays at 0.83.
- **Greedy over the 1,128 environment pairs** (chosen on the training half; within 1 - 1/e of the best k pairs; k = 1
  is the exhaustive best pair):

  | k | Pairs added | Clock relation: train / held-out / all | Frame relation: train / held-out / all |
  |---|---|---|---|
  | 1 | Minneapolis Jan n0 + Stockholm Jul n180 | 0.885 / 0.901 / 0.892 | 0.637 / 0.686 / 0.658 |
  | 2 | + Seattle Oct n180 + Singapore Jul n180 | 0.965 / 0.964 / 0.965 | 0.894 / 0.837 / 0.869 |
  | 3 | + Minneapolis Apr n0 + Stockholm Jan n0 | 0.979 / 0.981 / 0.980 | 0.956 / 0.930 / 0.945 |

  Every frame flag is covered by some pair (199 of 199).

### Item 14: the richer outcome (reported only)

Unflagged automations that differ between the environments where they fire:

| | Clock relation | Frame relation |
|---|---|---|
| unflagged | 5,136 | 5,727 |
| unflagged and differing in count, payload or branch | 20 | 54 |
| count differs | 17 | 51 |
| branch differs | 6 | 6 |
| payload differs | 3 | 4 |
| of them environment-coupled | 14 | 44 |

So the projected outcome hides little beyond itself. Over all run automations: count 169, payload 9, branch 11.

## Revision R4-B (Addendum 7): the error-budget corners, propagating the physics errors, and the rule set

**Post hoc** (after the second internal review), registered before its runs as protocol Addendum 7
(`docs/experiments/edt-protocol.md`; the change to its periodic steady state, made before any corner run, is dated there). Base: the ERA5 sweep
`120f4ca` (rung 4), its home and recorded weather, R2-c's setback population. New indoor-model knobs (all set through
`vesper.env.indoor.knobs`; at their defaults the model is bit-identical, by the pinned fingerprint test):
`INTERNAL_GAINS_W_PER_M2`, `ROOF_GROUND` (U 0.15 roof, 0.25 ground, over the floor, to the outdoor air),
`PREWARM_DAYS` (warm-up days before the run on its first day's weather; inf: a 30-day periodic steady state),
`SOLAR_APERTURE_SCALE` (on G_SOLAR x glass), `OUTDOOR_T_OFFSET_C` (vapour pressure kept) and `HUMIDITY_CLASS`.
`vesper.edt.confirm.closed_loop` gained `companions` (several automations in one session, firings attributed by
index; tested). Every open-loop setting recorded its traces in a cache of its own, and a trace's reuse key now also
names the knobs set away from their defaults (`vesper.edt.trace`, tested), so no trace can be served across settings.
Script: `scripts/revision/r4b_error_budget.py`; numbers: `results/edt/error-budget-r4b-120f4ca.json`
(aggregates; automations A1-A22 as in R2-c, repositories R1-R6).

**Stated conditions** (the review asked): every room starts at **20.5 C** (START_C) at the first instant of the
warm-up; the baseline (no automation) thermostat is the hallway's, at its own setpoint **20 C**, band 0.3 K, heating
every indoor room at up to 100 W/m² of floor; the integration step is 60 s; days 1-2 are judged.

### Item 4: the setback population over the error-budget corners (closed loop, A7.2)

The 22 setback automations of R2-c, each alone, against the same corner's baseline in the same environment, R2-c's
causal rule. **The nominal corner (gains 0, no roof or ground, 1-day warm-up, 165 kJ/(m² K)) reproduces R2-c step 2
exactly**: the same 53 breaching runs, every coldest room, caused-cell count, baseline minimum, setback length and
drop identical. No baseline room falls below 10 C at any corner (coldest 14.3 C).

*Design as registered.* Stage 1 ran the four screen corners (gains 0, roof and ground on, 80 kJ/(m² K), each warm-up)
in all 48 environments (4,416 runs): 92-94 breaching runs of 1,056, in **9 environments**: Minneapolis and Stockholm
January (both orientations), Seattle January (both), Stockholm October (both) and Minneapolis October n0. E therefore
holds 9 environments (more than 8), so stage 2 ran all 120 corners in R2-c's four ("the core") and the warm-ups 1 day
and periodic only (60 corners) in the other five (17,342 runs, plus 598 reused from stage 1). Outside E every corner
counts as no breach; the check holds: **at no corner does any run breach that does not breach at the screen corner of
the same warm-up** (0 of 120 corners). Denominators, the same environments for every corner: the core, 22 x 4 = 88
runs (all 120 corners); J, the January environments with a breach at any corner (Minneapolis, Stockholm, Seattle,
both orientations), 22 x 6 = 132 runs (60 corners); all 48 environments, 1,056 runs (60 corners).

| Gains W/m² | Roof + ground | C kJ/(m² K) | Core (of 88), warm-up 1 / 5 / 10 days / periodic | J (of 132), 1 day / periodic | All 48 (of 1,056), 1 day / periodic | Minneapolis / Stockholm / Seattle (1 day, over E) | Automations (1 day) | Coldest caused room per run, min / median / max (1 day) |
|---|---|---|---|---|---|---|---|---|
| 0 | off | 80 | 63/63/63/63 | 64 / 64 | 64 / 64 | 33 / 30 / 1 | 17 | [-3.1, 5.9, 9.9] |
| 0 | off | 110 | 57/54/57/57 | 57 / 57 | 57 / 57 | 33 / 24 / 0 | 17 | [-0.7, 6.4, 9.9] |
| 0 | off | 165 | 53/53/53/53 | 53 / 53 | 53 / 53 | 31 / 22 / 0 | 16 | [2.7, 8.1, 9.8] |
| 0 | off | 260 | 23/22/22/22 | 23 / 22 | 23 / 22 | 23 / 0 / 0 | 12 | [6.0, 7.4, 8.6] |
| 0 | off | 370 | 11/20/18/18 | 11 / 18 | 11 / 18 | 11 / 0 / 0 | 8 | [8.6, 9.4, 9.9] |
| 0 | on | 80 | 63/63/63/63 | 84 / 84 | 93 / 94 | 33 / 39 / 21 | 17 | [-5.7, 6.3, 9.8] |
| 0 | on | 110 | 63/63/63/63 | 69 / 70 | 69 / 70 | 33 / 30 / 6 | 17 | [-4.0, 6.4, 9.8] |
| 0 | on | 165 | 54/54/54/54 | 54 / 54 | 54 / 54 | 32 / 22 / 0 | 17 | [-1.0, 6.3, 9.8] |
| 0 | on | 260 | 52/53/52/53 | 52 / 53 | 52 / 53 | 30 / 22 / 0 | 16 | [2.8, 8.1, 9.9] |
| 0 | on | 370 | 25/26/26/26 | 25 / 26 | 25 / 26 | 23 / 2 / 0 | 12 | [5.6, 6.7, 9.7] |
| 2 | off | 80 | 59/57/59/57 | 59 / 57 | 59 / 57 | 33 / 26 / 0 | 17 | [-1.1, 6.0, 9.9] |
| 2 | off | 110 | 53/53/53/53 | 53 / 53 | 53 / 53 | 31 / 22 / 0 | 16 | [1.0, 7.6, 9.2] |
| 2 | off | 165 | 37/38/38/38 | 37 / 38 | 37 / 38 | 29 / 8 / 0 | 16 | [4.1, 6.3, 9.9] |
| 2 | off | 260 | 23/22/22/22 | 23 / 22 | 23 / 22 | 23 / 0 / 0 | 12 | [7.2, 8.6, 9.5] |
| 2 | off | 370 | 2/5/6/7 | 2 / 7 | 2 / 7 | 2 / 0 / 0 | 1 | [9.6, 9.75, 9.9] |
| 2 | on | 80 | 63/63/63/63 | 66 / 66 | 66 / 66 | 33 / 30 / 3 | 17 | [-4.3, 5.4, 9.9] |
| 2 | on | 110 | 63/63/63/63 | 63 / 63 | 63 / 63 | 33 / 30 / 0 | 17 | [-2.6, 5.9, 9.7] |
| 2 | on | 165 | 53/53/53/53 | 53 / 53 | 53 / 53 | 31 / 22 / 0 | 16 | [0.2, 6.8, 8.4] |
| 2 | on | 260 | 41/44/42/42 | 41 / 42 | 41 / 42 | 27 / 14 / 0 | 15 | [3.8, 5.9, 9.9] |
| 2 | on | 370 | 23/22/22/22 | 23 / 22 | 23 / 22 | 23 / 0 / 0 | 12 | [6.2, 7.5, 8.4] |
| 5 | off | 80 | 43/43/43/43 | 43 / 43 | 43 / 43 | 33 / 10 / 0 | 17 | [1.7, 5.5, 9.9] |
| 5 | off | 110 | 31/31/31/31 | 31 / 31 | 31 / 31 | 31 / 0 / 0 | 16 | [3.7, 6.2, 9.5] |
| 5 | off | 165 | 23/23/23/23 | 23 / 23 | 23 / 23 | 23 / 0 / 0 | 12 | [6.1, 7.6, 9.1] |
| 5 | off | 260 | 8/10/9/9 | 8 / 9 | 8 / 9 | 8 / 0 / 0 | 6 | [8.9, 9.55, 9.8] |
| 5 | off | 370 | 0/0/0/0 | 0 / 0 | 0 / 0 | 0 / 0 / 0 | 0 | None |
| 5 | on | 80 | 63/63/63/63 | 63 / 63 | 63 / 63 | 33 / 30 / 0 | 17 | [-2.2, 5.5, 9.5] |
| 5 | on | 110 | 55/55/55/55 | 55 / 55 | 55 / 55 | 33 / 22 / 0 | 17 | [-0.6, 6.1, 8.6] |
| 5 | on | 165 | 47/47/47/47 | 47 / 47 | 47 / 47 | 31 / 16 / 0 | 16 | [1.9, 7.6, 9.8] |
| 5 | on | 260 | 23/23/26/22 | 23 / 22 | 23 / 22 | 23 / 0 / 0 | 12 | [5.2, 6.2, 7.2] |
| 5 | on | 370 | 23/22/22/22 | 23 / 22 | 23 / 22 | 23 / 0 / 0 | 12 | [7.4, 8.6, 9.4] |

**The interval.** Over the core, the breaching runs range from **0 to 63 of 88** across the 120 corners (nominal 53);
over J from **0 to 84 of 132** (nominal 53); over all 48 environments from **0 to 94 of 1,056** (nominal 53).
- *Worst corner*: no internal gains, roof and ground on, 80 kJ/(m² K), periodic warm-up: 94 of 1,056 (84 of J), in
  Minneapolis and Stockholm January (63), Seattle January (21), Stockholm October (9) and Minneapolis October (1);
  the coldest caused room reaches -6.0 C. In the core, 63 of 88 is reached by 24 corners: it is the ceiling of what
  the 17 automations that command their setback in the cold days can do.
- *Best corner*: 5 W/m² of internal gains, no roof or ground, 370 kJ/(m² K) (very heavy), at every warm-up: 0.
- **Minneapolis breaches at 116 of the 120 corners** (all but the four best); **Stockholm at 76**; Seattle only at 8
  of the 60 J corners (80-110 kJ/(m² K), 6 of them with roof and ground on).

*What moves it* (core mean over the other factors): capacity 80 / 110 / 165 / 260 / 370 kJ/(m² K): 58.8 / 53.5 /
44.6 / 28.6 / 15.3; internal gains 0 / 2 / 5 W/m²: 47.0 / 42.0 / 31.7; roof and ground off / on: 32.9 / 47.5;
warm-up 1 / 5 / 10 days / periodic: 39.9 / 40.3 / 40.4 / 40.2. The warm-up hardly matters, because the
hallway thermostat holds the house at 20 C before the setback whatever the start (its largest effect is at 370 kJ/(m²
K) and no gains, 11 runs at 1 day against 18-20 with a longer warm-up: from 20.5 C the heavy house starts warmer than
its periodic state). The review's two big terms point the ways it said: internal gains fewer breaches, roof and ground
more, of similar size (-15 and +15 runs on the core mean); capacity over the physical ISO 13790 range is the largest
term, and with it Stockholm drops out at 260-370 kJ/(m² K) unless the roof and ground are on.

### Item 5: propagating the solar-aperture, outdoor-temperature and humidity-class errors (open loop, A7.3)

The affected set: 281 automations that run at rung 4 and read a temperature (214) or a humidity (63) by their names
as written, or have an orientation witness (129); 128 flagged temperature and 37 flagged humidity automations among
them. Each setting re-recorded all 48 traces in its own cache and replayed the set with the sweep's worker. **The
reference (aperture x1, recorded afresh) reproduces the published sweep exactly**: 13,488 outcomes, 0 flag, kind or
outcome mismatches, the same 129 orientation witnesses, no failed environment.

| Setting | Orientation witnesses kept (of 129): temperature / humidity / other | Temperature flags kept (of 128) | Humidity flags kept (of 37) | Affected: flags lost / gained, kinds changed | Outcomes changed (of 13,488) |
|---|---|---|---|---|---|
| aperture x0.25 | 66: 36 of 90 / 27 of 32 / 4 of 8 (new: 0) | 109 | 37 | 20 / 0, 35 | 2,297 |
| aperture x0.5 | 111: 73 of 90 / 32 of 32 / 7 of 8 (new: 0) | 119 | 37 | 10 / 0, 26 | 2,110 |
| aperture x2 | 109: 76 of 90 / 27 of 32 / 7 of 8 (new: 3) | 115 | 36 | 15 / 2, 26 | 2,575 |

Reading: halving the solar aperture keeps 111 of the 129 orientation witnesses; a quarter keeps 66. The humidity
side is the robust one: every humidity witness survives a halved aperture and 27 of 32 a quartered one, and **no
humidity flag is lost at a smaller aperture** (one at x2). The temperature side is the fragile one: 73 of 90 temperature witnesses
survive x0.5, 36 of 90 survive x0.25, and 9 (x0.5) or 19 (x0.25) of the 128 temperature flags are lost. A smaller
aperture creates no new flag or witness; doubling it creates 2 flags and 3 witnesses and loses 13 temperature flags. The review's reading holds for the orientation witnesses that read
a temperature: they are the findings most exposed to the term that fails validation (B's ~7 C warm bias); if the true
aperture is half the model's, about one in five of them goes; if a quarter, three in five.

**Outdoor air +-2 K** (ERA5's disclosed bias at home B):

| Offset | Temperature flags kept (of 128): lost / kinds changed | Humidity flags (of 37): lost / kinds changed | Orientation witnesses kept (of 129) | Outcomes changed |
|---|---|---|---|---|
| -2 K | 114: 14 / 16 | 0 / 2 | 112 | 2,273 |
| +2 K | 128: 0 / 1 | 1 / 5 | 125 | 2,237 |

A colder outdoor air (-2 K) loses 14 of the 128 temperature flags and 17 orientation witnesses; a warmer one (+2 K)
none and 4: the flags sit nearer a cold-side threshold than a warm one. No humidity flag is lost at -2 K.

**Humidity class** (EN ISO 13788, 270 / 540 / 810 Pa per class at 0 C or below outside; class 1 is the model):

class 2 (540 Pa) changes no flag, no kind and no orientation witness (380 outcomes move, first firing times); class 3 (810 Pa) loses 1 of the 37 humidity flags and one orientation witness, changes the kinds of 2 (488 outcomes move). The humidity flags are insensitive to the fitted class.

**The 85 % clamp.** Room relative-humidity series of the baseline home (7 indoor rooms x 48 environments = 336
series, days 1-2):

| Setting | Series reaching 85 % (the clamp) | ... reaching 70 % | ... reaching 55 % | Minutes at the clamp | Environments with a series at the clamp |
|---|---|---|---|---|---|
| as built (aperture x1, class 1, offset 0) | 51 | 79 | 215 | 4.0 % | 8 |
| class 2 (540 Pa) | 51 | 92 | 231 | 4.0 % | 8 |
| class 3 (810 Pa) | 51 | 150 | 283 | 4.0 % | 8 |
| aperture x0.25 | 66 | 111 | 251 | 14.2 % | 10 |
| aperture x0.5 | 56 | 95 | 245 | 10.1 % | 8 |
| aperture x2 | 21 | 55 | 159 | 0.9 % | 5 |
| outdoor -2 K | 56 | 92 | 214 | 6.5 % | 8 |
| outdoor +2 K | 41 | 64 | 189 | 3.0 % | 6 |

The clamp is reached only in Singapore (and, with the aperture at a quarter, in two Sydney environments): 51 of 336
series at the model as built, all 8 Singapore environments, 4 % of all room-minutes. It does not move with the
humidity class, because the class's moisture excess falls to zero at an outdoor temperature of 20 C and Singapore is
above it; what moves it is the room temperature (aperture x0.25: 66 series; x2: 21). The 70 % threshold the reported
automations use is crossed by 79 series as built and 150 at class 3.

*Check of the affected set* (every automation that runs, replayed at x2 and at +2 K): outside the 281, 3 of 5,653 automations change an outcome at x2 (50 outcomes, 1 flag) and 1 at +2 K (48 outcomes, 0 flags). The set chosen by the names as written misses a few automations that the binder puts on a room's temperature or humidity sensor (5 of the 8 orientation witnesses outside the temperature and humidity groups read one): the counts above are therefore complete for the 129, 128 and 37 they are stated over, and the whole corpus moves by at most one flag more.

### Item 6: isolation versus the rule set (closed loop, A7.4)

`closed_loop(..., companions=[...])` runs several automations in one session (one engine; tested). The context of an
automation is every other automation of its repository that binds and runs at rung 4. The 147 repositories involved
hold 11,111 parsed automations, of which 2,808 bind and 2,193 run: **about four in five of an author's rules cannot
run in the standard home**, so every context below is partial (a missing companion might restore, or switch off,
what the present ones do not). Counting the corpus directly gives the same
per-repository counts as the sweep for all 147 (no automation outside the sweep). Every run is also made "alone"
through the same labelled path; alone, the setback population gives **53 breaching runs again, exactly R2-c's**.

**Setback (the 22, R2-c's four breach environments, 88 runs).**

| | Runs | Automations |
|---|---|---|
| Caused breach alone (R2-c) | 53 | 16 |
| ... that survive with the context | **5** | 2 (A13 4, A11 1) |
| ... of which the automation's own (no breach with the context alone) | **1** | 1 (A11) |
| Lost with the context | 48 | 14 |
| New breaches with the context | 10 | 3 |
| Breaching runs with the context, in all | 15 | 4 |
| ... of which the automation's own | 3 | 1 (A11) |

Why the 48 go away: **every one is restored by a companion** that commands the same author thermostat back to
18-19 C between the 7 C commands (the 14 lost automations are R2's: its setbacks alternate with heating schedules on
the same entity; the commanded setpoint is below 10 C for 34-35 of the 48 hours in context, but never for long
enough). None depends on a binding collision (a companion's thermostat that was a different author entity): 0 of 48.
What survives is not a setback: A13's companions turn the thermostat off (`climate.turn_off`), so its rooms reach
2.3 C with or without A13; A11 turns the thermostat off before setting 5 C, and its companions set 19-22 C but never
turn it back on, so its rooms fall to 3.0 C with the whole rule set (3 runs, two of them new) and to 15.6 C without
it. A4 and A17 breach only in context, again through companions' `climate.turn_off`.

**night_light.** An automation's lamp counts as off when any automation of the run turns it off before 01:00.

| Population | Runs | night_light alone (closed loop) | Survive in context | Lost: off by the same author lamp / only by another author lamp bound onto it | New in context | Automations: alone / in context somewhere |
|---|---|---|---|---|---|---|
| night_light divergence (30), every env with one | 848 | 802 | **101** | 460 / 241 | 46 | 29 / 9 |
| static night_light (226), 2 envs each | 452 | 422 | **228** | 156 / 36 | 16 | 211 / 122 |

For the 30 automations flagged with a night_light divergence the finding is mostly an isolation artifact: 701 of
802 night_light runs go away with the rule set, 460 of them because a companion turns **the same author lamp** off
(the off-rule the review predicted); 241 go away only through a companion that turns off a different author lamp the
binder put on the same home lamp (a binding collision: those losses are an artifact of the standard home, not
evidence either way). 9 of the 29 keep a night_light somewhere with their repository present (21 lose it in every
environment run; 6 of the 21 only through collisions). The static night_light (226) is more robust: 228 of 422 runs
and 122 of 211 automations keep it.

### The setback finding, restated (review items 4 and 6)

Over the 120 error-budget corners (internal gains 0-5 W/m², roof and ground off or on, warm-up 1 day to periodic,
heat capacity 80-370 kJ/(m² K)), 0 to 63 of the 88 Minneapolis and Stockholm January runs breach (53 at the model as
built; 0 to 94 of all 1,056 runs), Minneapolis at 116 of the 120 corners and Stockholm at 76, but run with the rest of
their repository's rules, 48 of the nominal 53 breaching runs go away because a companion rule restores the
setpoint, and the 15 breaching runs that remain come from rules that turn the thermostat off, not from a setback.

*Concerns, stated.* The ground loss goes to the outdoor air (pessimistic); the warm-up repeats the run's first day;
about four in five of a repository's automations cannot run in the standard home, so a context is partial; every
setback automation's repository puts its climate entities on one hallway thermostat (no restore here depended on a
different author entity, but the night_light losses partly do); the aperture knob scales the solar heat, not the
daylight (which has its own knob and changes one flag in 797 at rung 3).

## Revision R6-A (Addendum 8): the confirmatory rung-0 test, the paired AUC bootstrap, the exemptions, the frame switch, the crossing-time reference, humidity at rung 2.5, the dead-rule interval, the home variants

**Post hoc** (after the third internal review). Every analysis was registered as protocol Addendum 8
(`docs/experiments/edt-protocol.md`), committed alone before any of its numbers was computed. The deviations found
after the runs are dated there (A8.9).

- *Inputs:* the cached `8fcd672` sweeps (rung 4 and 2.5, with the rich outcome cache) and their rung-4 traces, the
  R4-C dead-rule audit, and A8.8's five new rung-4 sweeps.
- *Code:*
  - `vesper.edt.ladder` (`rung0_split_fit`, `split_scores`, `oof_scores`, `paired_auc_diff_bootstrap`);
  - `vesper.edt.oracle` (the `sensed_crossing` option of the frame relation, off by default; `sensed_crossing_refs`,
    `first_crossing`, `crossing_instant`, `first_firing_frames`);
  - `vesper.edt.trace.trace_file`, which `record_trace` now writes through, so analyses read traces by the sweep's key.

  All of it is tested, and the published frame relation is unchanged at the default: with the option off, all 5,926
  run automations reproduce their `frame_divergences`.
- *Script and numbers:* `scripts/revision/r6a_analyses.py`, `results/edt/r6a-d169ae5.json` (aggregates only).
  Review 3's item numbers are in brackets.

### Item 1(c): the confirmatory rung-0 test on the registered split (A8.1)

The split is section 6's repository halves. The model is fitted once on the training half. Its threshold is the best
recall at precision >= 0.55 on the training half's own scores, applied unchanged to the held-out half.

The criterion, written before the held-out half was scored, is A1.4's: *rung 0 matches rung 4 under the frame
relation iff its held-out AUC >= 0.90 and its held-out recall at precision 0.55 >= 0.80.* It is a pre-specified
secondary analysis of a post hoc relation.

Primary population, frame relation: the run automations. The training half has 3,149 automations (113 flagged, 367
repositories), the held-out half 2,777 (86 flagged, 355 repositories).

| Flags / population | Features | Held-out AUC [repo bootstrap 95 %] | Recall (flagged found) | Realised precision | Matches? |
|---|---|---|---|---|---|
| **frame / run** | **8** | **0.849** [0.781, 0.906] | **0.628** (54 / 86) | 0.454 | **no** |
| frame / run | 8+4 | 0.841 [0.724, 0.929] | 0.698 (60 / 86) | 0.403 | no |
| frame / bound | 8 | 0.877 [0.824, 0.927] | 0.628 (54 / 86) | 0.432 | no |
| frame / bound | 8+4 | 0.866 [0.767, 0.945] | 0.698 (60 / 86) | 0.397 | no |
| frame / env-coupled run | 8 | 0.859 [0.787, 0.918] | 0.806 (58 / 72) | 0.427 | no (AUC) |
| frame / env-coupled run | 8+4 | 0.894 [0.838, 0.933] | 0.833 (60 / 72) | 0.405 | no (AUC) |
| clock / run | 8 | 0.934 [0.904, 0.961] | 0.899 (328 / 365) | 0.642 | yes |
| clock / run | 8+4 | 0.953 [0.928, 0.975] | 0.956 (349 / 365) | 0.611 | yes |
| clock / bound | 8 | 0.928 [0.900, 0.955] | 0.907 (331 / 365) | 0.577 | yes |
| clock / env-coupled run | 8 | 0.857 [0.804, 0.901] | 1.000 (347 / 347) | 0.664 | no (AUC) |

- **The verdict.** Under the frame relation, the eight-feature rung 0 does not match rung 4 on the registered split:
  held-out AUC 0.849 and recall 0.628, both below A1.4's thresholds. So, as a secondary analysis on this split, **the
  physics is necessary for detecting frame-relation divergences.**
- **Under the clock relation** the same procedure matches (0.934 and 0.899), which repeats the registered A1.4
  verdict on the held-out half.
- Every frame-relation row fails, the 8+4 linter's too. The closest is the environment-coupled 8+4 row (AUC 0.894,
  recall 0.833).
- The realised held-out precision is below 0.55 in every frame row (0.40-0.45). A training-half threshold does not
  keep its precision on the other half.
- **The frame features do not help on this split.** The paired held-out difference AUC(8+4) - AUC(8) is **-0.007**
  [-0.097, 0.072] (355 held-out repositories) on the run automations. It is +0.035 [-0.003, 0.083] on the
  environment-coupled ones.

### Item 7(b): the paired bootstrap of the AUC difference (A8.2)

A6.7's out-of-fold scores (5 folds of whole repositories, seed 0): the same folds and rows, 8 against 8+4 features.
Repositories are resampled with replacement (1,000 resamples, seed 0). The point AUCs reproduce A6.7's.

| Flags / population | AUC 8 | AUC 8+4 | Difference [paired repo bootstrap 95 %] |
|---|---|---|---|
| **frame / run (5,926; 199)** | 0.782 | 0.919 | **+0.137 [0.083, 0.193]** |
| frame / env-coupled run (1,205; 184) | 0.783 | 0.831 | +0.048 [0.025, 0.075] |
| clock / run (5,926; 790) | 0.946 | 0.981 | +0.035 [0.021, 0.050] |
| clock / env-coupled run (1,205; 771) | 0.847 | 0.846 | -0.001 [-0.005, 0.003] |

So within A6.7's cross-validation, "the frames raise it to 0.92" is supported by a paired interval that excludes 0.

**Not registered: why A8.1 and A8.2 disagree** (added after both were seen; A8.9).

- *The eight-feature model, per fold.* Each A6.7 fold's own model scores 0.881, 0.877, 0.814, 0.862 and 0.919 on its
  own test rows, but the five folds pooled give 0.782. The pooled out-of-fold AUC mixes five differently calibrated
  models, and that costs the eight-feature model about 0.07-0.10.
- *With the frame features,* the per-fold AUCs are 0.961, 0.911, 0.964, 0.949 and 0.922, and pooled 0.919: here the
  pooling costs little.
- *The split run the other way* (trained on the held-out half) gives 0.866 (8 features) and 0.969 (8+4). The gain from
  the frame features therefore depends on which repositories train the model: about +0.10 one way, -0.01 the other.

The robust statement is the verdict, not the size of the gain. Neither syntactic linter reaches A1.4's bar under the
frame relation on either the registered split or the cross-validation. The "0.78 -> 0.92" contrast overstates the
frame features' contribution: about half of it is the pooling artifact of the eight-feature model.

### Items 5(c) and 5(e): the exemptions and the ambiguities (A8.3)

- **The sun exemption.** 7 of the 5,926 run automations have a sun frame with no computable reference.
  - Reasons: a template reading `sun.sun` (5; elevation or state in a template condition), or a `sun.sun` state that
    is neither horizon state (2: `unavailable`, and a misspelt `below_horizion`).
  - None reads the azimuth.
  - **None of the 7 is flagged under either relation** (0 of 790 clock flags, 0 of 199 frame flags), so the second
    exemption removes no flag in this corpus.
- **The sensed frame among the flags.** 148 of the 199 frame flags declare a sensed frame: sensed 94, clock+sensed
  48, sensed+sun 5, clock+sensed+sun 1. Review 3's 147 omits the last. Of the 790 clock flags 177 do, and of the 5,926
  run automations 355.
- **Two different sun events** (all are sunrise and sunset):
  - 98 run automations name both in the references the relation uses: 34 clock flags, 3 frame flags. The code
    measures the sun-relative minutes from the nearest of all the named instants (on the firing's day or a
    neighbouring one).
  - 7 run automations name different events in their triggers and their conditions: 7 clock flags, 3 frame flags. The
    code uses the triggers' references and ignores the conditions'.
- **A template reading both `now()` and `sun.sun`.** 1 run automation, flagged under neither relation. Such a template
  adds both the clock and the sun frame but supplies no sun reference. This automation's sun frame is computable
  through another step.

### Item 14: the frame-switch re-tabulation (A8.4)

The population is the **595** automations flagged under the clock relation and not under the frame relation.

**The rich cache records the first firing's instant, not which trigger fired.** The frame of each first firing was
therefore inferred from the instant, the triggers and the cached traces, by A8.4's rules (time of day, time pattern,
sun instant, a change of a trigger entity, or a template's entity or minute).

- **Only 40 of the 595 have triggers in more than one frame.** The rest cannot switch: 519 are sun-only, 24
  sensed-only, 12 clock-only.
- Of the 28,560 first firings, 28,314 were inferred, 0 were ambiguous and 246 were uninferred (an action delay, for
  instance). Every first firing was inferred for 587 of the 595, including all 40 that could switch.
- **frame_switch: 3 automations** (all clock+sun triggers, every firing inferred). For example, "at 18:30 or at sunset
  minus 20 min" fires first by the sun in January and October and by the clock in April and July.
- This is below A8.4's threshold of 10, so **frame_switch is not registered as a kind.** The masking risk the review
  raised (a frame disjunction hiding a switch of trigger) is 3 automations of 595 here.

### Item 13: the crossing-time sensed reference (A8.5; an option, off by default)

- **Which automations it reaches.** 157 run automations have a numeric_state trigger on a sensed entity with a numeric
  bound, so their sensed frame becomes computable: **91 of the 148 sensed-frame flags.**
  - The other 57 sensed-frame flags read their quantity through a state trigger, a template or a condition, and stay
    exempt.
  - Of the 157, 100 fire. 95 have a θ_sen in at least two environments, and 57 never fire.
- **θ_sen spread over the 95:**
  - median 0 min; 84 exactly 0 (the firing is at the first crossing, in every environment);
  - 2 in (0, 60] min, none in (60, 720];
  - **9 above 720 min** (max 2,864).
- **Flags.** The frame relation with the option on still flags **199 automations: none added.** But **8 of the 199
  gain time_spread** (spreads 1,277-2,864 min):
  - by frame: 7 clock+sensed, 1 sensed+sun;
  - by quantity: 7 temperature, 1 illuminance;
  - before, all 8 were flagged fires_somewhere_only only (one with night_light too).
- **What the new findings are,** by A8.5's rule:
  - **4 are environment-dependent latency, gated by a condition.** In some environments the first crossing happens
    outside the automation's time window ("after 17:30, before 06:30", or "after sunrise, before sunset"), so it fires
    at a later crossing, up to a day and a half later.
  - **4 "precede":** a clock trigger in the same automation (a time pattern) fires before the first crossing in some
    environments.

  So the crossing reference turns the exemption into a real frame for 91 of the 148 sensed-frame flags. The only new
  class it surfaces is condition-gated latency, and in this corpus it is small (4 automations).

### Item 16(d): humidity flags at rung 2.5 (A8.6)

The rung-2.5 sweep (porch on ERA5, indoor rooms on the assumed curve) is cached from R4-A.

- **Of the 36 frame-relation humidity flags, 0 are flagged at rung 2.5.**
- Of the 37 clock-relation humidity flags, 1 is (with the same kinds); it is the only one that names a porch sensor.

So the humidity findings are not ERA5-grounded: they all rest on the room model's humidity. They stay in the
room-model-dependent class, and do not move to the ERA5-grounded one.

### Item 15(b): the dead-rule headline (A8.7)

R4-C labelled 6 of a seeded sample of 100 (70 repositories) of the 391 environment-coupled dead automations at
`120f4ca` as genuinely dead. The estimate is 391 x 6 / 100 = 23.5, with three intervals:

| Interval method | Genuinely dead, of 391 |
|---|---|
| **exact finite-population (hypergeometric inversion; the headline)** | **11-45** |
| Wilson 95 % on 6/100, times 391 (R4-C's "11-49") | 10.9-48.8 |
| repository-clustered bootstrap (70 repositories, 1,000 resamples, seed 0), times 391 | 7.5-44.9 |

**Headline: about 23 genuinely dead rules (exact 95 % interval 11-45) of 391.** R4-C's 11-49 used Wilson, which
ignores that the sample was drawn without replacement from 391. The clustered bootstrap's lower end is lower (7.5):
the 6 come from few repositories.

At `8fcd672` the population is 390, and 99 of the 100 sampled and all 6 genuinely dead ones are still in it.

### Item 16(h): the frame relation on the four home variants (A8.8)

A5.1's four variants were swept again at rung 4 with the main run's settings (current binder, device reporting rule,
rich cache, both relations), 4 workers. The five sweeps (with the check) took 76 min of wall time.

**The check is not exact (A8.9).** The standard home swept again with the `8fcd672` traces gives **the same flags
under both relations for every automation**, and the same yield. But 4 automations differ in one environment's
outcome, by a load-dependent refusal from the engine's 0.5 s wall-clock template-compile budget; none of them is
flagged. And 4 differ in the rich cache only, by a firing count of one or a branch set. The cause is found, so the
variants are used with this stated.

| Variant | Run | Clock flags | Frame flags | Of the 199 still frame-flagged (same kinds) | New frame flags |
|---|---|---|---|---|---|
| standard (`8fcd672`) | 5,926 | 790 | 199 | | |
| glazing x0.5 | 5,926 | 779 | 190 | 190 (190) | 0 |
| glazing x2 | 5,926 | 776 | 198 | 196 (196) | 2 |
| plan seed 8 | 5,926 | 772 | 196 | 196 (196) | 0 |
| mirror east-west | 5,926 | 790 | 199 | 199 (199) | 0 |

- **187 of the 199 frame flags hold in all four variants, and none is lost in all four.**
- The mirrored home reproduces the frame relation exactly.
- Halving the glazing loses 9, doubling it loses 3 and adds 2, and the other plan loses 3.
- The negative control flags 0 in every variant.

So the frame relation's findings are a property of the automations far more than of this one home's plan and windows.
The 12 that do not hold everywhere are the glazing- and plan-sensitive ones.

## Revision R6-B (Addendum 9): infiltration, home A free-floating, the implied aperture, the daylight chain, the beam share

**Post hoc** (after the third internal review), registered before its runs as protocol Addendum 9
(`docs/experiments/edt-protocol.md`, commit `ce93b79`; nothing in it was changed after a run). Base: the ERA5 sweeps
`120f4ca` (clock relation) and `8fcd672` (frame relation), their homes and recorded weather, R2-c's setback population,
R4-B's corners and open-loop path. New indoor-model knobs, bit-identical at their defaults by the extended fingerprint
test: `ACH` (0.5) and `DIRECT_SHARE` (0.8, the coefficient in direct = 0.8 (1 - cloud), in `vesper.env.sun`, set
through `vesper.env.indoor.knobs` and named in a trace's reuse key when moved). The validation room gained a heater log
and a free-floating episode runner (`vesper.validation.environment`, tested; the registered check is unchanged).
Script: `scripts/revision/r6b_physics.py`; numbers: `results/edt/r6b-66407d5.json` (aggregates only).
No registered verdict changes; where a registered criterion is applied again it says "would pass (post hoc)".

### Item 8(a): infiltration (closed loop, A9.2)

**The H breakdown of the standard home** (the EDT home, 7 indoor rooms, at 0.5 ACH): **77.8 W/K** = outside walls
28.3 W/K (36 %) + glass 14.2 W/K (18 %) + ventilation 35.3 W/K (**45 %**). At 0.3 ACH the ventilation is 21.2 W/K
(house 63.7 W/K); at 1.0 ACH 70.5 W/K (house 113.0 W/K). Interior partitions (U 2.0 W/(m² K)) move heat between rooms,
not out of the house, and are not in H. Every earlier result held infiltration at 0.5 ACH; a deep setback's stack
effect would raise it.

R2-c's 22 setback automations, each alone, against the same setting's baseline, R2-c's causal rule:

| ACH | Nominal, core (of 88) | Nominal, all 48 (of 1,056) | Automations (nominal) | Minneapolis / Stockholm | Coldest caused room per run, min / median / max (nominal) | Baseline coldest room | Worst corner G0-Fon-Wpss-C80, core (of 88) | Best corner G5-Foff-W1-C370, core (of 88) |
|---|---|---|---|---|---|---|---|---|
| 0.3 | 31 | 31 | 12 | 23 / 8 | 5.0 / 7.1 / 9.8 C | 15.5 C | 63 (coldest -4.8 C) | 0 |
| 0.5 | **53** | **53** | 16 | 31 / 22 | 2.7 / 8.1 / 9.8 C | 15.6 C | 63 (-6.0 C) | 0 |
| 1.0 | 54 | 54 | 17 | 32 / 22 | **-1.3** / 6.3 / 9.6 C | 15.9 C | **67** (-7.2 C) | **23** (7.2 C) |

- *Checks.* At 0.5 ACH the nominal setting reproduces R2-c's 53 breaching runs exactly (the same runs), and the two
  corners reproduce R4-B's (63 and 0). Over all 48 environments the nominal 53 are all in the core: **A7.2's "outside
  E, no breach" held at the nominal corner** (0 of the 44 other environments). The breaching runs are nested: every
  run that breaches at 0.3 breaches at 0.5, and every one at 0.5 at 1.0.
- *Reading.* The count is not monotone in size so much as in depth: from 0.5 to 1.0 ACH one more run breaches (54) but
  the coldest caused room falls from 2.7 to -1.3 C; from 0.5 to 0.3 the count falls to 31. **The ACH term moves the
  core count over 31-54 at the nominal setting and over 0-67 with the corners**: 1.0 ACH takes the worst corner past
  R4-B's ceiling of 63 (to 67, 18 automations) and the best corner from 0 to 23. Mean over the three settings run
  (nominal, worst, best) of the core count: 31.3 / 38.7 / 48.0 at 0.3 / 0.5 / 1.0 ACH. R4-B's 0-63 interval is
  therefore not a bound once infiltration is free: with it, **0-67 of 88**. The full 120-corner product was not
  re-run at each ACH (A9.2 registered the nominal setting and the two corners only).

### Item 8(b, c): home A free-floating (A9.3)

An hour of one of home A's eight rooms counts as evidently free-floating when the measured room is below the proxy
setpoint (its 10th percentile) minus 1 K and falling. Each episode (a run of such hours) is simulated without heating
from the measured temperature of the hour before it.

| Rule | Rooms in the median | Hours per room | Episode length, median per room | Free-float median RMSE / within 2 K | Persistence (the last measured value held) | The registered heated model, same hours |
|---|---|---|---|---|---|---|
| below s - 1 K and falling (primary) | 8 of 8 | 49-138 | 2-8 h (longest 29 h) | **0.90 C / 0.95** | 0.58 C / 1.00 | 1.86 C / 0.70 |
| below s and falling (sensitivity) | 8 of 8 | 159-248 | 2-4 h (longest 36 h) | 0.77 C / 0.97 | 0.78 C / 1.00 | 1.25 C / 0.88 |

Per room (primary), the free-float RMSE is 0.32-1.30 C with a bias of -0.16 to -0.81 C (the model cools a little too
fast). On these hours **T's criterion would pass (post hoc)**: median RMSE 0.90 C <= 1.5, within 2 K 0.95 >= 0.7, where
the registered heated model on the same hours has 1.86 C (the proxy holds it at the setpoint while the room is a
kelvin or more below). **But this is weak evidence**: the episodes are short (half of them 2-8 hours), and holding the
last measured temperature does as well or better (0.58 C primary, 0.78 C in the sensitivity). The free-float pass
shows that the one-node room's cooling rate is not wrong by much over a few hours; it does not show skill beyond
persistence. The registered T fail on A stands.

*How home B's check differs.* In the hours the registered check uses, its heater runs in **0 of 1,036 hours in both of
B's rooms**, against a median of 14 % of hours (12-17 %, duty 4-5 %) in A's eight rooms; the measured room is below
the proxy in 10 % of hours in both homes (the percentile's definition). B's check is therefore a free-floating solar
test throughout, A's a heated one, which is what the review said; B's failure (+7.1 and +7.5 K) is the binding one for
the orientation findings.

*Item 8(c), geometry.* Both checks drive the same standard room (`STANDARD_ROOM`): 4 x 4 m, 2.5 m high, one 4 m
outside wall, one 1.2 x 1.2 m window facing south, VESPER's constants, for every measured room of A (8) and B (2). It
is neither home's own test-room geometry, which weakens both checks as evidence for any particular room.

### Item 3(a, b): the aperture implied by home B's residual (A9.4)

The registered T simulation of home B with SOLAR_APERTURE_SCALE k on a grid 0-2 (step 0.01), both rooms, the mean
bias over the 2 x 1,036 hours the check uses: b(1) = +7.29 K (dining room 7.13, room 7.45, as registered), b(0) =
-2.11 K, b(0.5) = +2.42 K, b(2) = +17.04 K. **k̂ = 0.250** (dining room 0.264, room 0.236), **95 % interval
0.205-0.289** (2,000 bootstrap resamples of B's 51 local days; every resample had a root). The cross-check from the
published quantities, k̂_lin = 1 - b(1) H / Q̄ with the room's H = 11.8 W/K and its mean modelled solar gain Q̄ =
120.8 W over the same hours, gives 0.287 (the heater never runs in B, so the linear reading is close). **R4-B's
x0.25-x2 bracket contains k̂, at its lower end**; the interval's lower half lies below it.

Propagation at k̂ (0.25, the bracket's point) and at the interval's ends (0.21, 0.29), R4-B's aperture path, every
trace recorded afresh; each reference (x1, recorded afresh with the current code) reproduces its sweep exactly
(120f4ca: 13,488 outcomes, 0 flag, kind or outcome mismatches, 129 witnesses; 8fcd672: 13,104 outcomes, 0 mismatches
under either relation, 123 witnesses). At 0.25 the clock set reproduces R4-B's x0.25 row exactly.

| Set | Aperture | Orientation witnesses kept: temperature / humidity / other (new) | Temperature flags kept | Humidity flags kept | Affected: lost / gained, kinds changed |
|---|---|---|---|---|---|
| clock, 120f4ca | x0.21 | 69 of 129: 43 of 90 / 23 of 32 / 4 of 8 (0) | 110 of 128 | 37 of 37 | 19 / 0, 43 |
| clock, 120f4ca | **x0.25 = k̂** | **66 of 129**: 36 / 27 / 4 (0) | **109 of 128** | **37 of 37** | 20 / 0, 35 |
| clock, 120f4ca | x0.29 | 83 of 129: 54 / 26 / 4 (2) | 102 of 128 | 37 of 37 | 28 / 0, 47 |
| frame, 8fcd672 | x0.21 | 69 of 123: 42 of 83 / 24 of 33 / 4 of 8 (1) | 92 of 104 | 36 of 36 | 13 / 0, 17 |
| frame, 8fcd672 | **x0.25 = k̂** | **64 of 123**: 34 / 27 / 4 (0) | **89 of 104** | **36 of 36** | 16 / 0, 20 |
| frame, 8fcd672 | x0.29 | 81 of 123: 52 / 26 / 4 (2) | 94 of 104 | 36 of 36 | 11 / 0, 11 |
| frame, 8fcd672 | x0.5 | 109 of 123 (1) | 96 of 104 | 36 of 36 | 9 / 0, 9 |
| frame, 8fcd672 | x2 | 103 of 123 (4) | 104 of 104 | 35 of 36 | 1 / 1, 2 |

(At 8fcd672 the clock relation gives, at k̂: 106 of 121 temperature flags, 37 of 37 humidity; the witnesses are the
same, being outcome classes.) **Restated at k̂: about half the orientation witnesses survive (66 of 129 on the clock
set, 64 of 123 on the frame set), the humidity flags all survive (37 of 37; 36 of 36), and about 85 % of the
temperature flags do (109 of 128; 89 of 104).** Over the interval 0.21-0.29 the witnesses range over 66-83 (clock) and
64-81 (frame), not monotonically in the aperture (x0.29 keeps more witnesses than x0.25 but loses more temperature
flags): a witness is a difference of outcome classes between the two orientations, and which thresholds are crossed
in which orientation changes with the aperture in steps. Under the frame relation the temperature flags are more
robust to the aperture than under the clock relation (x2: 104 of 104 against 108 of 121).

### Item 16(e): the beam-share coefficient (A9.5)

DIRECT_SHARE moves the beam/diffuse split of both the solar heat and the daylight (the outdoor illuminance itself is
unchanged):

| Set | Direct share | Orientation witnesses kept (new) | Temperature flags kept | Humidity flags kept | Affected: lost / gained, kinds changed | Outcomes changed |
|---|---|---|---|---|---|---|
| clock, 120f4ca | 0.6 | 111 of 129: 74 / 31 / 7 (1) | 112 of 128 | 37 of 37 | 17 / 0, 24 | 1,444 of 13,488 |
| clock, 120f4ca | 0.95 | 126 of 129: 87 / 32 / 8 (0) | 128 of 128 | 37 of 37 | 0 / 0, 2 | 1,401 |
| frame, 8fcd672 | 0.6 | 102 of 123: 67 / 31 / 5 (1) | 101 of 104 | 36 of 36 | 4 / 0, 4 | 1,348 of 13,104 |
| frame, 8fcd672 | 0.95 | 120 of 123: 80 / 33 / 8 (0) | 104 of 104 | 36 of 36 | 0 / 0, 0 | 1,296 |

A smaller beam share (0.6) costs about as much as halving the aperture (111 against R4-B's 111 of 129 witnesses at
x0.5); a larger one (0.95) almost nothing. The coefficient now has a sensitivity row; the orientation findings that
read a temperature are again the exposed ones.

### Item 9: the daylight decomposition chain (A9.6; the registered D "fail" stands)

Check D's 240 hours (10:00-16:00 local, measured irradiance >= 300 W/m²). The model's side is multiplied by each factor
in turn; the residual is model / measured.

| Step | Factor | Residual |
|---|---|---|
| R0: VESPER's median average daylight factor (80 generated rooms) 1.7608 % / D's measured 0.1191 % | | **14.78** |
| 1. Global to diffuse under the measured sky: Erbs et al. (1982) diffuse fraction from B's measured clearness index (median kt 0.72), median over the hours | 0.215 (0.16-0.96 by hour) | **3.18** |
| 2. Point sensor against the room average (standard 4 x 4 m room, sky component of the window under a uniform sky plus BRE's split-flux internal reflection, over its unobstructed ADF 1.63 %): at the room's centre 1.43, 0.5 m from the back wall 0.62 | 1.43 (range 0.62-1.43) | 4.55 (range 1.97-4.55) |
| 3. Framing and obstruction: frame 0.75 (0.7-0.8) x maintenance 0.9 (0.9-0.96) x visible sky 65/80 deg (60-80) | 0.548 (0.47-0.77) | **2.49** (range 0.93-3.49) |

With the model's own diffuse fraction (1 - 0.8 (1 - cloud), ERA5 cloud, median 0.272) in step 1 instead of Erbs's, the
residual after step 1 is 4.02. **After the three named factors the residual is 2.5 (0.9-3.5), down from 14.8**: most
of the factor of 15 is the comparison (a daylight factor is defined on the diffuse sky; D divided by the global light
on mostly clear spring days), and the rest is within what an unknown sensor position and an obstructed, framed window
can make. A point sensor need not read below the room average: in a small side-lit room its centre reads above it
(1.43); only a sensor near the back reads below (0.62). The comparison is not like-for-like and no comparable
measurement was available; D's registered failure stands, and rung 3 (daylight x 1/14.8) still reproduces rung 4, only
67 run automations reading illuminance.

*The arithmetic.* **14.8** is 1.7608 / 0.1191 = 14.78 unrounded (DAYLIGHT_SCALE 0.0676 = 1 / 14.79); **14.67** is the
same quotient from the rounded 1.76 / 0.12. The earlier "about 3.7 of the factor of 15" is 1 / 0.27, the sky's share
of the global light in the model's own split (direct share 0.73 at the median cloud), and its "4-8 unexplained" is
0.52 % / 0.119 % = 4.4 and 0.94 % / 0.119 % = 7.9, computed on the standard home's four habitable rooms (median
overcast DF 1.93 %), not on D's 80-room ensemble (1.76 %): the two halves of that sentence used different room
populations, which is why it could not be reconstructed from 1.76 / 0.12. On the ensemble with the model's split the
remainder is 4.0 (14.78 x 0.272).

### Item 16(a): the sun at 0-5 deg elevation (A9.7)

VESPER's sun (astral) against NREL SPA (pvlib), every minute of the three run days of each EDT city's January
environment (2026-01-14 to 16), only where SPA's apparent elevation is in (0, 5] deg: **1,344 minutes**, of them 403
in Stockholm. Pooled: elevation error mean 0.0042 deg, 95th percentile 0.0074, max **0.0085 deg**; azimuth max
0.0037 deg; position on the sky max **0.0085 deg** (Stockholm alone: max 0.0078). The registered S bound (0.05 deg)
was set above 5 deg; at 0-5 deg the error is a sixth of it, so Stockholm's January, which lies almost wholly in this
band, is as well placed as the verified band.

### Item 16(b): ERA5 against the official station for home B (A9.8)

NOAA's ISD global-hourly record for Valencia airport (Manises, USAF 082840 WBAN 99999), free with no login, fetched
once and cached locally (not committed); a station hour is the mean of the passing reports in the UTC hour, as the
dataset is resampled. Over the **1,035** of B's 1,197 hours with ERA5, the house sensor and the station:

| Comparison | Bias | RMSE | r |
|---|---|---|---|
| ERA5 - station | **+0.70 C** | **1.47 C** | 0.965 |
| house sensor - station | +2.80 C | 3.31 C | 0.930 |
| ERA5 - house sensor (W's comparison) | -2.09 C | 2.63 C | 0.931 |

The last row reproduces W on B (-2.087 / 2.633 over 1,036 hours). **Against the official station ERA5 would pass W's
criterion (post hoc)**: |bias| 0.70 <= 1.0 C, RMSE 1.47 <= 2.0 C, r 0.965 >= 0.90. B's registered W failure is the
house-mounted sensor reading 2.8 C warm of the airport, not ERA5 being 2 C cold. (The station is at the airport, about
9 km west of VESPER's Valencia site; the comparison is ERA5 at VESPER's cell against the nearest
official record, not at the house.) The registered W verdict on B stands as registered.

### What this changes, stated

- The setback count is sensitive to infiltration in the direction the review expected and more than R4-B's interval
  allowed: 31-54 of 88 at the nominal setting, 0-67 of 88 with R4-B's extreme corners.
- Home A free-floating passes T's criterion post hoc, but no better than persistence on episodes of a few hours.
- Home B's residual implies an aperture of 0.25 (0.21-0.29), inside R4-B's bracket at its end: at it, about half the
  orientation witnesses, about 85 % of the temperature flags and all humidity flags survive, on both relations.
- The daylight factor of 15 shrinks to about 2.5 with the comparison's named factors.
- The sun at 0-5 deg is as accurate as above 5 deg; ERA5 matches Valencia airport within W's criterion, so B's W
  failure is the house sensor's.

## Revision R6-C (Addendum 10): the engine against unmodified Home Assistant 2026.10.2

**Post hoc** (after the third internal review, item 2), registered as protocol Addendum 10 before any comparison run
in its four environments; the deviations made after the run are dated there. Harness:
`vesper/validation/ha_diff.py` and `ha_diff_driver.py` (Home Assistant 2026.10.2, astral 2.2, unmodified, in its own
venv, in-process on a virtual clock; no port is opened). Comparison run at `134b209` with the published engine;
secondary run with the fixed engine at `3dcd4e7`; aggregates `results/edt/r6c-ha-diff-533ceb1.json` (automations by
sample index only). Same inputs: the cached device-rule traces of the main sweep (`8fcd672`), every entity state as
VESPER's engine derives it, fed to Home Assistant at the same logical second; `sun.sun` is Home Assistant's own.
Environments: Minneapolis 2026-01-14 n0, Stockholm 2026-07-15 n180, Sydney 2026-01-14 n0, Sydney 2026-07-15 n180.
Sample: 263 automations, stratified as registered (8 time_spread frame flags, 40 other frame flags, 30 sun with
offset, 30 numeric_state, 25 time_pattern, 30 template, 30 trigger `for:`, 25 choose/if, 25 other sun, 20 other),
**1,052 automation-environment pairs**.

**Gate.** VESPER's side reproduces the published sweep's outcome and richer outcome exactly in **1,052 of 1,052**
pairs. Home Assistant loaded all 263 automations in every environment (none unavailable).

### What the two engines did (the published engine)

| | Pairs (of 1,052) |
|---|---|
| both fire | 630 |
| neither fires | 414 |
| VESPER only | **8** (2 automations, all four environments each) |
| Home Assistant only | **0** |

- **First-firing difference Δ = VESPER minus Home Assistant**, over the 630 jointly firing pairs: 63 exactly 0; 576
  (91.4 %) within 1 s; **608 (96.5 %) within 60 s**; all 630 within 3 h. Median -0.08 s, 5th to 95th percentile -0.46
  to +1.0 s; extremes -119 s and +121 s.
- **Payload** (630 jointly firing pairs): (service, target) sets equal in 626; distinct-instant counts equal in 618;
  brightness comparable in 30, equal in 26 (the 4 others are the comparison artifact below).
- **Discrepant** (exactly one fires, |Δ| >= 1 s, or calls, counts or brightness differ): **78 pairs in 26
  automations**; another 501 pairs differ by under a second only.

By trigger kind (an automation counts under every kind it has; pairs where both fire):

| Kind | Automations | Both fire | |Δ| < 1 s | |Δ| < 60 s | Discrepant pairs | One engine only | Δ min / max (s) |
|---|---|---|---|---|---|---|---|
| sun trigger with an offset | 32 | 92 | 92 | 92 | 0 | 0 | -0.4 / 0.0 |
| sun trigger (any) | 59 | 186 | 186 | 186 | 4 | 4 | -0.4 / 0.0 |
| other sun use (sun.sun state/elevation, sun condition) | 63 | 202 | 148 | 180 | 66 | 8 | -119 / +121 |
| numeric_state trigger | 49 | 111 | 80 | 102 | 35 | 4 | -119 / +121 |
| time_pattern trigger | 29 | 86 | 86 | 86 | 4 | 0 | -0.5 / -0.1 |
| time trigger | 75 | 246 | 240 | 242 | 10 | 0 | -119 / +1 |
| template (trigger, condition, value_template) | 38 | 55 | 55 | 55 | 8 | 0 | -0.4 / 0.0 |
| trigger `for:` | 50 | 76 | 69 | 72 | 7 | 0 | -119 / +1 |
| choose / if | 47 | 129 | 117 | 124 | 14 | 0 | -119 / +1 |
| state trigger | 74 | 110 | 85 | 97 | 33 | 0 | -119 / +1 |

Sun triggers (event times) agree to under half a second in all 186 jointly firing pairs; the minute-scale differences
are all in automations that read the `sun.sun` entity. The sub-second differences are Home Assistant's random 50-500 ms
offset of time triggers (seeded), which VESPER does not model. Of the sampled strata, the template and `for:` strata
mostly hold automations that fire nowhere (the resident-free home rarely changes what they watch): 44 and 36 jointly
firing pairs.

### Why: every discrepancy classified (0 unexplained)

| Class | Pairs | Automations | Cause |
|---|---|---|---|
| engine bug | 58 | 21 | `sun.sun`: VESPER switched its state by an older rule (next rising after next setting), updated it one second after each event, wrote events and position as two state changes, and updated the position every 8 and 4 minutes in nautical and civil twilight where 2026.10.2 uses 4 and 2; 2026.10.2 sets the state from the rounded elevation (above -0.833°), at the event itself, in one write. A `sun.sun` state turns up to 2 minutes later after sunrise, an elevation threshold is crossed one update earlier or later (±119-121 s), an any-change trigger fires 545 vs 497 times (Minneapolis) |
| engine bug | 4 | 1 | `light.turn_on` with `kelvin` (and `color_temp`): gone from 2026.10.2's schema, so Home Assistant's call fails and the run stops; VESPER dropped the parameter and turned the light on |
| HA semantics VESPER lacks | 4 | 1 | `initial_state: off`: Home Assistant never runs the automation; VESPER ignores the key |
| HA semantics VESPER lacks | 4 | 1 | the sub-second offset of time triggers, decisive here: a `now()` window ending at exactly 10:00:00 is open in VESPER and closed 0.26 s later in Home Assistant (8 vs 4 firings) |
| harness artifact | 4 | 1 | a stub `shell_command` answers an empty response, so a template reading `response['stdout']` fails in Home Assistant (VESPER records the call without rendering its data) |
| harness artifact | 4 | 1 | the comparison read Home Assistant's `brightness_pct: 0` (a turn-off) as a brightness (a post-run correction, A10 deviations) |

The engine-bug classes are confirmed by the fixed engine: **with the fixes (`870125e`, `3dcd4e7`) the discrepant
pairs fall from 78 to 16 (4 automations: exactly the HA-semantics and harness rows above), the one-engine-only pairs
from 8 to 4, and every one of the 630 jointly firing pairs is within 0.5 s** (max |Δ| 0.50 s).

### The flags (A10.5)

- **Direct substitution** (Home Assistant's outcome in place of VESPER's in the four environments, the sweep's in the
  other 44; the recomputation reproduces all 199 frame flags with VESPER's own outcomes): **0 of the 48 sampled frame
  flags change** (0 of the 8 time_spread flags, 0 of the 40 others), and none of their clock kinds.
- **Margin** (m = the largest |Δ| among jointly firing pairs, 121 s; the 99th percentile is the same): 18 of the 199
  frame flags (2 of the 8 time_spread) and 65 of the 790 clock flags have a time-of-day spread within 2m of the 3 h
  tolerance, so a shift of that size could move them. With the fixed engine m is under 1 s.
- **Propagation as registered** (1,000 replicates, per-environment independent draws; artifacts as agreeing): frame
  flags changing kinds: mean 6.5, 95 % range 3-10 of 199; time_spread flags 2.7, 0-5 of 8; clock flags 272.5,
  250-303 of 790. Almost all of it is one effect: the 8 VESPER-only pairs (1.25 % of VESPER's firing pairs) dropped
  independently into each of a flag's 48 environments turn about half of all flags into "fires somewhere only".
  Those 8 pairs are automation-level (the two automations never fire in Home Assistant anywhere).
- **Propagation by automation** (post hoc, beside the registered one): frame flags 2.2, 0-5 of 199; time_spread 0.08,
  0-1 of 8; clock flags 9.3, 5-15 of 790. A flag changes here when its automation is drawn as one Home Assistant never
  runs (it then fires nowhere and is no longer flagged).

### Criterion (A10.7): fails on (3)

| Part | Value | Limit | |
|---|---|---|---|
| (1) firing-set symmetric difference | 8 of 1,052 | at most 21 | pass |
| (2) jointly firing pairs with |Δ| < 60 s (artifacts agree) | 600 of 622 (96.5 %) | at least 95 % | pass |
| (3a) time_spread flags changed by direct substitution | 0 of 8 | 0 | pass |
| (3b) 97.5th percentile of frame flags changed, propagation as registered | 10 of 199 | at most 9 | **fail** |

So, as registered, the engine does not pass: the registered propagation's upper bound is one flag over its limit,
driven by two automations VESPER runs and Home Assistant does not, spread independently over environments. The
direct evidence is that no sampled flag moves, and the post hoc automation-level propagation bounds the change at 5
of 199 frame flags and 1 of 8 time_spread flags.

### What the fixes would change (not re-run; A10.8)

| Construct | Run automations (of 5,926) | Fire somewhere | Clock flags | Frame flags | time_spread frame flags |
|---|---|---|---|---|---|
| reads `sun.sun` (sun-entity fix) | 235 | 104 | 74 | 18 | 4 |
| `kelvin` / `color_temp` in a light call | 42 | 12 | 8 | 0 | 0 |
| `initial_state: off` (not fixed: HA semantics) | 83 | 28 | 11 | 3 | 0 |

In the sample, the fixed engine changes VESPER's outcome in 85 of the 1,052 pairs (22 automations, all of which read
`sun.sun`; one of them also uses `kelvin`), and changes **none of the 48 sampled frame flags**. The fixes move firings by
at most about two minutes, an order of magnitude below the 3 h tolerance; a sweep re-run would move at most the flags
within that margin (18 frame flags under the published engine's worst case). The `kelvin` fix makes 12 automations
that fire somewhere stop at the failing call: at most 8 clock flags and no frame flag. `initial_state: off` is not
modelled: 28 automations that fire somewhere would never run in Home Assistant, and 3 of them carry a frame flag.

*Concerns, stated.* The input stream is VESPER's own mapping of the trace to entity states (its MQTT-discovery
emulation is not under test), and an entity's attributes are the ones VESPER models. Services no loaded integration
provides are stubs (empty responses); `light`, `switch`, `lock` and `climate` are Home Assistant's own, with no entity
behind them, so an action never changes a state on either side (open loop). The sample over-represents the trigger
kinds the oracle depends on and VESPER-firing automations; it measures the engine on them, not a corpus rate. The
template and `for:` strata are thin in firing pairs (44 and 36) because the resident-free home seldom moves what they
watch. One environment of the harness shakedown (Phoenix 2026-04-15) had shown the `sun.sun` and `kelvin` differences
before the run; their occurrence in the four is a confirmation.

## Revision R6-D (Addendum 11): the engine-corrected sweep

**Post hoc** (registered as Addendum 11 at `5b54586`, before the run). The run is one rung-4 sweep at `5b54586`: the
current binder, the device reporting rule, the recorded ERA5 weather and fresh traces. That commit carries Addendum 10's
two engine fixes: `sun.sun` as Home Assistant 2026.10.2 has it, and `kelvin`/`color_temp` refused. It ran
2026-11-05 08:21–08:31Z with 5 workers. Script: `scripts/revision/r6d_compare.py`; aggregates:
`results/edt/r6d-5b54586.json`. The sweep at `8fcd672` stays the primary result.

| | `8fcd672` (published) | `5b54586` (engine-corrected) |
|---|---|---|
| bound / run | 9,515 / 5,926 | 9,515 / 5,926 |
| flags, frame relation | 199 (185 fires_somewhere_only, 30 night_light, 8 flaps, 8 time_spread) | 199, the same kinds |
| flags, clock relation | 790 (709 time_spread) | 785 (704 time_spread) |
| negative control | 0 of 1,247 firing | 0 of 1,243 firing |
| environment-coupled run automations that never fire | 390 | 395 |

- **Frame relation:** all 199 flags hold with the same kinds. None is lost and none is new; the 8 time_spread flags all
  hold. The defects' reach among the run automations is 235 reading `sun.sun` and 36 using `kelvin`/`color_temp` (by a
  text match on the bound automation). None of these changes a frame flag.
- **Clock relation:** 785 of the 790 flags hold with the same kinds. The 5 lost flags are all time_spread, and all
  within the defects' reach: 4 `kelvin`/`color_temp` (now refused mid-run, as Home Assistant refuses them) and 1
  `sun.sun`. No flag is new.

Read with Addendum 10: the registered propagation criterion failed on the sampled discrepancies (10 of 199 > 9). With
the defects fixed and the whole population re-run, the frame relation's flag set does not change at all.

## Revision R7-C (Addendum 15): the engine test's semantic stratum

**Post hoc** (after the fourth internal review, item 8(a)). Registered as protocol Addendum 15 at `f556117`, before
any comparison run of its stratum. A15.11 (`eab00ca`) was written after the shakedown and before the first comparison
run. Harness: Addendum 10's (`ha_diff.run_env`, Home Assistant 2026.10.2 in-process on a virtual clock, no port),
with `vesper/validation/ha_semantics.py` added. Comparison and reading runs at `95dcc37`; fixed engine at `fa3ce1f`.
Aggregates: `results/edt/r7c-ha-diff-b76a6f3.json` (automations by sample index only). The engine is the
engine-corrected sweep's (`r6d-5b54586`, which carries Addendum 10's fixes), on that sweep's traces, in Addendum 10's
four environments.

**Sample.** 227 automations, **908 automation-environment pairs**: the denominators below. They are never pooled with
Addendum 10's 263. The sub-strata are 48 frame flags (every one of the 199 that carries a construct; 13 of them were
also in the 263), 70 `restart`/`queued`/`parallel`, 19 `wait_for_trigger`, 60 `delay`, 20 `initial_state` off and 10
`initial_state` on. By construct (an automation counts under each it carries): 97 carry a delay, 24 a
wait_for_trigger, 56 `restart`, 11 `parallel`, 5 `queued`, 23 `initial_state` off and 42 on.

**Gate.** VESPER's side reproduces the sweep's outcome and richer outcome in **908 of 908** pairs. Home Assistant
loaded all 227 automations in every environment.

### What VESPER's engine does with the constructs (`vesper/lab/automations.py`)

| Construct | VESPER's engine | In this stratum |
|---|---|---|
| `mode: single` (the default) | as Home Assistant: conditions first, then a trigger during a run is dropped | Home Assistant logged a dropped trigger ("Already running") in 16 jointly firing pairs (300 drops, all in delay automations); the firing counts agree in all 16 |
| `restart`, `queued`, `parallel` | run as `single` (permissive replay; listed as an approximation, refused in strict mode) | no difference that a mode causes: Home Assistant with and without the mode has the same firing and instant counts in all 288 pairs of mode automations, and the same recorded calls in 284; in the other 4 (one queued automation) the seeded sub-second offsets fall differently, as A15.1 expects |
| `max`, `max_exceeded` | accepted, ignored | no difference |
| `initial_state` | accepted, ignored: the automation always starts enabled | `off`: Home Assistant never runs it (62 pairs); `on`: no difference |
| `delay` | holds the run, which stays running for `single`; a templated delay is refused, so none is replayed | 228 jointly firing pairs, all agree (firings, instants, calls) |
| `wait_for_trigger` | holds the run until a trigger or the timeout; `continue_on_timeout` defaults to true | 12 jointly firing pairs, all agree |

So the stratum exercises `single`'s dropping, delays and waits, and `initial_state: off`. It does **not** exercise
`restart`, `queued` or `parallel`. Only 16 sampled automations combine such a mode with a delay or a wait. 4 of them
fire jointly (16 pairs), and in no pair did a trigger change what Home Assistant did. In the sweep, 116 run automations
combine a non-single mode with a hold. 7 of them fire somewhere, 1 carries a clock flag and none a frame flag. That
is the most a mode could touch.

### What the two engines did

| | Pairs (of 908) |
|---|---|
| both fire | 424 |
| neither fires | 417 |
| VESPER only | **67** (19 automations) |
| Home Assistant only | **0** |

- **First firing**, over the 424 jointly firing pairs: **all 424 within 0.5 s** (Δ = VESPER minus Home Assistant from
  -0.50 to 0.00 s, median -0.23 s; 32 exactly 0). The sub-second differences are Home Assistant's 50-500 ms offset of
  time triggers.
- **Firing counts** (424): the number of firings is equal in **423**; distinct instants equal in 419. In the other 5,
  VESPER has fewer instants (4) or more (1); all are the offset: one automation's simultaneous calls fall on different
  sub-second instants in Home Assistant.
- **Payloads** (424): (service, target) sets equal in 420; brightness comparable in 34, equal in 34.
- **Oracle components** (908): fired and first time of day agree in 841 (the 67 one-engine pairs differ); **flaps
  agree in 908** (46 flapping pairs in each engine); night_cover in 908; night_light in 907.
- **Discrepant** (A10.4(4)): **76 pairs in 21 automations**; another 383 pairs differ by under a second only. Without
  the 13 frame flags also in the 263 (214 automations, 856 pairs) the 76 are unchanged: the 13 add no discrepancy.

By sub-stratum (pairs; jointly firing; discrepant):

| Sub-stratum | Automations | Pairs | Both fire | VESPER only | Discrepant | Firing count equal |
|---|---|---|---|---|---|---|
| frame_flag | 48 | 192 | 76 | 3 | 4 | 75 of 76 |
| mode | 70 | 280 | 128 | 4 | 12 | 128 of 128 |
| wait_for_trigger | 19 | 76 | 8 | 0 | 0 | 8 of 8 |
| delay | 60 | 240 | 180 | 0 | 0 | 180 of 180 |
| initial_state_off | 20 | 80 | 0 | 60 | 60 | - |
| initial_state_on | 10 | 40 | 32 | 0 | 0 | 32 of 32 |

### Why: every discrepancy classified (rules A15.6, A15.11)

| Class | Pairs | Automations | Cause |
|---|---|---|---|
| HA semantics VESPER lacks | 62 | 17 | `initial_state: off`: Home Assistant never runs the automation; VESPER ignores the key (the reading run, without the key, agrees) |
| HA semantics VESPER lacks | 4 | 1 | permissive replay records a call to a script without rendering its data; Home Assistant renders it and the template fails (it picks at random from a group attribute the input stream does not carry), so its run stops (a `parallel` automation) |
| HA semantics VESPER lacks | 5 | 2 | the 50-500 ms offset of time triggers: instant counts (4 pairs, one `queued` automation's simultaneous calls) and one pair with a condition read 0.37 s later |
| engine bug | 4 | 1 | a `sun` condition `before` an event, with an offset, tested by a trigger at that same instant: Home Assistant 2026.10.2 holds it there (it fails only after), VESPER held it only strictly before, so the two took different branches (a `restart` automation; seen in the shakedown, A15.11(3)) |
| unexplained | 1 | 1 | by hand: the same offset. A time trigger at 07:00:00 with an illuminance condition; the sensor reports at 07:00:00.05. VESPER reads the old value and fires, Home Assistant, 0.37 s later, the new one and does not. No registered rule covers a pair where only one engine fires, so it stays unexplained |
| harness artifact | 0 | 0 | |

No difference is due to `mode`, `max`, `max_exceeded`, `delay` or `wait_for_trigger`. The reading run (Home Assistant
given each automation without `mode`, `max`, `max_exceeded`, `initial_state`) has 5 one-engine pairs and 14
discrepant pairs in 4 automations: exactly the data, offset, sun-condition and unexplained rows above.

**The engine fix** (`e6e3355`, failing tests first). A `sun` condition now behaves as in Home Assistant 2026.10.2:
`before` and `after` each hold at their own instant, and only after sunset with before sunrise is "either" (the night,
both strict). Any other pair is "both"; VESPER used to read any pair whose after came later than its before as
either. With the fix the discrepant pairs fall from 76 to 72 (20 automations). The 4 pairs of the sun-condition
automation now agree. VESPER's oracle outcome changes in 0 of the 908 pairs: the fix changes which service the branch
calls, not when it fires.

**Reach** (static; the sweeps are not re-run):

| Construct | Run automations (of 5,926) | Fire somewhere | Clock flags | Frame flags (of 199) | time_spread frame flags |
|---|---|---|---|---|---|
| any of the stratum's | 1,553 | 476 | 180 | 48 | 2 |
| a mode other than single | 330 | 41 | 9 | **2** (both `restart`) | 0 |
| ... with a delay or a wait | 116 | 7 | 1 | 0 | 0 |
| `initial_state` off (not modelled) | 83 | 28 | 11 | 3 | 0 |
| `delay` | 453 | 126 | 42 | 20 (1 of them flaps) | 2 |
| `wait_for_trigger` | 63 | 3 | 0 | 0 | 0 |
| a `sun` condition (the fix's domain) | 146 | 40 | 33 | 17 | 2 |
| ... with a trigger at its changed boundary (the fix's reach) | 2 | 2 | 2 | 0 | 0 |
| ... an after and before on one event, after not earlier (either becomes both) | 0 | 0 | 0 | 0 | 0 |

So 2 of the 199 frame flags involve a non-default mode (both `restart`, neither with a hold), and the sun-condition
fix reaches no frame flag. Its reach is 2 run automations with clock flags.

### The flags (A15.5)

- **Direct substitution** (Home Assistant's outcome in the four environments, the sweep's in the other 44): **0 of
  the 48 frame flags change**, including the 2 time_spread flags, and no clock kind changes.
- **Margin**: m = 0.50 s, the largest |Δ|; 0 of the 48 frame flags and 0 of the 2 time_spread flags lie within 2m of
  the 3 h tolerance.
- **Propagation, primary (by automation; artifacts agree; 1,000 replicates)**: frame flags changing kinds: mean 5.9,
  95 % range **2-11** of the 48; time_spread 0.28, 0-1 of 2; clock flags carrying a construct 23.4, 14-33 of 180. A
  flag changes when it draws one of the 18 sampled automations (of the 148 VESPER fires) that Home Assistant fires in
  none of the four environments: 17 `initial_state: off`, 1 whose call data fails. The flag then fires nowhere.
- **Propagation, secondary**: per environment, frame flags 2.3, 2-3 of 48, with both time_spread flags changing in
  every replicate (a dropped firing in one of their 48 environments adds "fires somewhere only"). By automation with
  every pair as observed: the same as the primary (no pair is an artifact).

### Criterion (A15.7): fails on (1) and (3b)

| Part | Value | Limit | |
|---|---|---|---|
| (1) pairs where exactly one engine fires | 67 of 908 | at most 18 | **fail** |
| (2) jointly firing pairs with \|Δ\| < 60 s | 424 of 424 (100 %) | at least 95 % | pass |
| (3a) time_spread flags changed by direct substitution | 0 of 2 | 0 | pass |
| (3b) 97.5th percentile of frame flags changed, primary propagation | 11 (of the 48) | at most 9 | **fail** |

As registered, the engine fails on this stratum. Both failures have one cause, which this addendum and Addendum 10
both name: VESPER ignores `initial_state: off`. That accounts for 62 of the 67 one-engine pairs; the other 5 are the
data VESPER does not render (4) and the offset (1). The stratum over-samples the key on purpose (20 of 227
automations, plus 3 frame flags), so 67 of 908 is not a corpus rate. In the sweep, 83 of 5,926 run automations carry
the key, 28 of them fire somewhere, and **3 of the 199 frame flags** do. Those 3 would fire nowhere under Home
Assistant and lose their flag. The propagation does not use that knowledge: it hands each of the 48 flags a random
sampled automation's behaviour, so about one flag in eight (18 of 148) inherits "never runs". The direct evidence is
that no sampled flag changes.

### Beside the stratum (A15.9, descriptive)

Addendum 10's 263 held 62 automations that carry a construct (248 pairs; its fixed-engine records, `3dcd4e7`). They
show 116 jointly firing pairs, all within 0.44 s, (service, target) sets equal in 116 and instants equal in 112. There
are 4 one-engine pairs, from 1 `initial_state: off` automation, and 12 discrepant pairs in 3 automations. They are
three of R6-C's rows: `initial_state` off, the offset, and the brightness-0 comparison artifact.

*Concerns, stated.* The stratum tests `single`'s dropping (16 pairs with drops, counts equal), delays, waits and
`initial_state`. It does not test `restart`, `queued` or `parallel` under overlapping triggers: the one place they
would differ never happened in the sample, and the sweep has at most 7 firing automations where it could. Those modes
remain "run as single", a documented approximation with that reach. The inputs are VESPER's own trace-to-state
mapping, and services outside the home's four domains are stubs, as in Addendum 10. The one unexplained pair and two
of the offset pairs come from Home Assistant's sub-second offset meeting a report 50 ms after the hour or a
same-instant fan-out. VESPER fires time triggers at the whole second.

## Revision R7-A (Addendum 12): orientation witnesses, robust core, bookkeeping, binder discount, null difference, dead reasons, the env_latency variant, the cover bound

**Post hoc** (after the fourth internal review), registered as protocol Addendum 12 at `2cbf868`, committed alone before
any number below was computed. Code: `vesper.edt.oracle` gained the `env_latency` option of the frame relation, off by
default (tested; off, all 5,926 run automations of `8fcd672` reproduce their published frame divergences);
`r4a_analyses.dead_rules` was split into `dead_reasons` and itself, unchanged. Script:
`scripts/revision/r7a_analyses.py` (tests `tests/edt/test_r7a_analyses.py`); numbers: `results/edt/r7a-25d6b90.json`
(aggregates only). At most 3 worker processes, under nice. Review 4's item numbers are in brackets. $S_R$ is `120f4ca`,
$S_6$ is `8fcd672`.

### Item 1(b): what an orientation witness is, and the three populations (A12.1)

An *orientation pair* is two environments that differ only in plan north. Three rules are in the code:

| Rule | $S_R$ | $S_6$ |
|---|---|---|
| frame flag with an orientation pair on which the frame relation, restricted to the pair, diverges (§V-B's) | (no frame relation) | **100** of 199 |
| clock flag with an orientation pair on which the clock relation, restricted to the pair, diverges | 129 of 797 | 123 of 790 |
| clock flag with an orientation pair whose outcome classes differ (R4-B's and R6-B's `affected` set) | 129 | 123 |

- The two clock rules give the same set on both sweeps; R4-B's 129 and R6-B's 123 are therefore the clock relation's
  orientation witnesses on $S_R$ and $S_6$.
- **The frame relation's 100 are a subset of $S_6$'s 123.** Of the other 23, 19 are not frame flags at all (their
  orientation difference is a time-of-day spread the sensed frame exempts) and 4 are frame flags whose orientation
  pairs do not diverge under the frame relation.
- By what they read as written, the 100 are temperature 64, humidity 30, neither 6 (3 read illuminance); the 123 are
  83, 32 and 8.

*The aperture propagation, re-derived on the 100.* R6-B's frame-set aperture settings were replayed on their own cached
traces (hard-linked; 0 of 48 traces re-recorded at every setting) for 327 automations: R6-B's affected set united with
the 199 frame flags. The check: the x1 replay reproduces every frame flag, kind, clock kind and frame witness of the
327 (0 mismatches). **Deviation, dated 2026-11-05:** the replays ran with the current code, which carries Addendum 11's
engine fixes; 470 of the 15,696 replayed outcomes (13 automations) differ from `8fcd672`'s, all 13 reading `sun.sun`
(2 also using `kelvin`/`color_temp`), and none changes a flag, a kind or a witness.

| Aperture | Frame orientation witnesses kept (of 100): temperature / humidity / neither | New | Temperature flags kept (of 104) | Humidity flags kept (of 36) |
|---|---|---|---|---|
| x0.21 | **61**: 39 of 64 / 19 of 30 / 3 of 6 | 3 | 92 | 36 |
| x0.25 = k̂ | **56**: 30 / 23 / 3 | 1 | 89 | 36 |
| x0.29 | **68**: 44 / 21 / 3 | 0 | 94 | 36 |
| x0.5 | 82: 51 / 27 / 4 | 4 | 96 | 36 |
| x1 | 100 | 0 | 104 | 36 |
| x2 | 94: 63 / 25 / 6 | 6 | 104 | 35 |

So on the frame relation's own population, **56 of 100 orientation witnesses survive at k̂ (61-68 over its interval)**,
against R6-B's 64 of 123 on the clock relation's population. The temperature and humidity rows equal R6-B's (89 of 104,
36 of 36 at k̂), because those families were already the frame relation's there.

### Item 4: robust core and possible set (A12.4)

The robust core is the published finding held at every sampled setting of a bracket; the possible set is the union
over its settings, within the 327 replayed (the engine bracket: over all run automations). R4-B's outdoor-air and
humidity-class settings were run on the frame set for the first time (traces recorded afresh, 48 environments each, no
failure).

| Bracket (settings) | Orientation witnesses (100): core / possible | Temperature flags (104): core / possible | Humidity flags (36): core / possible |
|---|---|---|---|
| aperture, R4-B (x0.25, x0.5, x1, x2) | **52** / 107 | 88 / 105 | 35 / 36 |
| outdoor air, R4-B (-2, 0, +2 K) | 91 / 106 | 103 / 104 | 36 / 37 |
| humidity class, R4-B (1, 2, 3) | 97 / 102 | 104 / 104 | 35 / 36 |
| k̂, R6-B (x0.21, x0.25, x0.29) | **47** / 77 | 88 / 95 | 36 / 36 |
| direct share, R6-B (0.6, 0.8, 0.95) | 92 / 101 | 101 / 104 | 36 / 36 |
| engine, R6-D (published, corrected; whole sweeps) | 100 / 100 | 104 / 104 | 36 / 36 |
| every setting together (13) | **42** / 107 | **88** / 105 | **34** / 37 |

- The lumped solar-gain term governs the orientation witnesses: **42 of 100 are held at every setting sampled**, 47 at
  every point of k̂'s interval, 52 over R4-B's aperture bracket. The pointwise counts are not monotone in the aperture
  (56 at x0.25, 61 at x0.21, 68 at x0.29), which is why a core is the quantity to quote.
- The temperature flags have a core of 88 of 104 over everything sampled; the humidity flags 34 of 36. Outdoor air and
  humidity class move few findings on the frame set (core 91-104).
- The possible sets add at most 7 witnesses, 1 temperature flag and 1 humidity flag beyond the published ones.

### Items 1(d), 1(f), 8(c): bookkeeping (A12.2)

- **790 against 797.** All 790 clock flags of $S_6$ are clock flags of $S_R$ (2 with other kinds); the 7 lost are all
  refused at $S_6$ by the binder's enclosure-or-medium rule. None is new.
- **390 against 391.** The 390 environment-coupled dead automations of $S_6$ are all among $S_R$'s 391; the one more is
  refused at $S_6$ by the same rule.
- **Bound counts.** $S_R$ binds **9,524** (457,152 = 9,524 x 48 automation-environment evaluations). The 9 bound at
  $S_R$ and not at $S_6$ are all refused by the enclosure-or-medium rule, so 9,524 - 9 = 9,515. "Refuse 7 and rebind
  2" counts only the 179 sensor-driven flags: the rule refuses 9 automations, 7 of them flagged; the weather-token rule
  binds 2 automations differently (both run and are clock-flagged at both sweeps) and changes no count. Run: 5,934 ->
  5,926.
- **Engine refusals.** $S_R$: 9,524 bound = 5,934 run + **3,590** refused in every environment, none refused in some
  only. $S_6$: 9,515 = 5,926 + **3,589**, plus 2 automations refused in some environments and run in others. So
  9,515 - 3,590 = 5,925 mixes the two sweeps; at $S_6$ it is 9,515 - 3,589 = 5,926.
- **The reason list, closed** (one reason per automation, its first over the environments in key order):

| Class | $S_R$ | $S_6$ |
|---|---|---|
| no trigger | 1,036 | 1,036 |
| `event` triggers | 1,312 | 1,312 |
| MQTT | 355 | 355 |
| webhooks | 157 | 157 |
| NFC tags | 72 | 72 |
| blueprint definitions | 165 | 165 |
| **other** (121-122 reason classes) | **493** | **492** |
| total | 3,590 | 3,589 |

  The other line's largest classes: an entity that is not one of the home's (66), a `value_template` numeric_state
  trigger (44), a `conversation` trigger (36), an unsupported action (32), `parallel` (25). The one-reason rule closes
  R4-C's 3,591: one automation of $S_R$ carries two reason classes over the environments (7 at $S_6$).

### Item 8(b): the discrepant pair (A12.3)

R6-C's published-engine run, 1,052 pairs = 630 both fire + 414 neither + 8 one engine only. A pair is discrepant iff
exactly one engine fires, or both fire and |Δ| >= 1 s, or the calls, the distinct-instant counts or the brightness
differ. Disjointly: **78 = 8 (one engine only) + 54 (both fire, |Δ| >= 1 s: 630 - 576) + 16 (both fire within 1 s, a
payload difference: count 8, calls 4, brightness 4)**. 4 of the 54 also differ in payload; no neither-fires pair is
discrepant; 608 of the 630 are within 60 s; 501 pairs differ by under a second only.

### Item 7: a binder-discounted flag count (A12.5)

Of the 199 frame flags, 148 declare a sensed frame and are discounted; the other 51 stay. 44 frame flags read the sun,
6 of them among the 148. R4-C's labels cover **146 of the 148** (correct 65, correct by convention 37, mis-bind 40,
uncertain 4); 2 are uncovered and take the covered share. Repository bootstrap over the 148's 72 repositories (1,000
resamples, seed 0; each resample's discounted part rescaled to 148 flags):

| Rule | Mis-binds among the covered | Discounted count D | Repo bootstrap 95 % |
|---|---|---|---|
| **primary: uncertain as not a mis-bind** | 40 of 146 | **158.5** of 199 | **[139.0, 173.7]** |
| uncertain as a mis-bind | 44 | 154.4 | [134.3, 170.8] |
| wrong medium only (C1-C4, C6) | 24 | 174.7 | [158.5, 187.0] |
| the review's form, 51 + 148 x (1 - 51/179) | | 156.8 | [141.0, 171.2] (R4-C's ends) |

So the binder takes the frame relation's 199 to about **158 [139, 174]**, about 4 in 5; counting only wrong-medium
artefacts, 175.

### Item 8(a), counting: non-default execution semantics (A12.6)

As written, over the 199 frame flags / 790 clock flags / 5,926 run automations: a `mode` other than `single` 2 / 9 /
330 (restart 2 / 6 / 260, queued 0 / 2 / 31, parallel 0 / 1 / 39); `max` 0 / 2 / 48; `max_exceeded` 2 / 2 / 97;
`initial_state` 26 / 130 / 918, of which false or off 3 / 11 / 83; a `delay` step 20 / 43 / 453; `wait_for_trigger`
0 / 0 / 63; **any of these 50 / 183 / 1,572**. The engine runs every mode as `single` (it records the approximation)
and ignores `initial_state`; lane C tests the engine.

### Item 10(a): the null-difference control (A12.7)

Run with the current (engine-corrected) code: the 9,515 bound automations of $S_6$, its home, device rule, rung 4,
north 0, the four dates. 20 ERA5 requests for the five new sites (URLs in the lab directory); no cached weather fetched
again. 44 environment runs, none failed.

- **Duplicated environment** (12 environments, each recorded and replayed in two fresh caches): **0 flags under
  either relation over 71,111 automation-pairs.** Two engine non-determinism findings, neither flagging: one
  automation refused in one copy of Phoenix January by the 0.5 s wall-clock template-compile budget (A8.9's known
  load-dependent refusal), and one once-a-minute automation with 2,879 against 2,880 firings in one copy of Stockholm
  April (the same first firing; the cause is not established, the wall-clock budget is the likely one).
- **Near-identical sites** (same date and north; every pair in different ERA5 cells; mean absolute hourly outdoor
  difference 0.15-1.36 C):

| Pairs | Automation-pairs | Clock flags (rate) | Frame flags (rate) | Environment-coupled: clock / frame rate |
|---|---|---|---|---|
| primary, 20-50 km: Minneapolis-Anoka, Stockholm-Södertälje, Phoenix-Mesa (12) | 71,112 | 92 (0.13 %) | **72 (0.10 %)** | 0.63 % / 0.49 % |
| secondary: Minneapolis-St Paul (14 km), Stockholm-Uppsala (64 km) (8) | 47,408 | 58 (0.12 %) | 45 (0.09 %) | 0.59 % / 0.46 % |
| for scale: R6-D's city pairs, same date and north (60) | | mean 1.83 % (0.34-7.46) | mean 0.99 % (0.30-1.94) | |

  Per pair, the frame flags are 2-13 of 5,926 (primary). The pooled primary frame flags are fires_somewhere_only 57,
  flaps 10, night_light 6, time_spread 1; 71 of 72 are environment-coupled, reading temperature 35, humidity 30, sun 8.
  So a near-identical pair flags about a tenth of what a city pair flags, almost all through thresholds on recorded
  temperature and humidity that a 0.2-1.4 C weather difference crosses. These are real differences of recorded weather
  (the duplicate control holds), not a false positive of the physical path; they also show that a threshold finding
  can rest on a fraction of a kelvin.

### Item 10(b): dead-reason precision (A12.8)

The mechanical reasons, re-derived at $S_6$ for the 390 environment-coupled dead automations, reproduce R4-A's
environment-coupled column exactly. Joined to R4-C's dead audit: 99 of its 100 are in the 390. Matching by the
registered table:

| Mechanical reason | Audited | Matches | Precision [Wilson 95 %] |
|---|---|---|---|
| trigger entities never change | 63 | 60 | **0.95** [0.87, 0.98] |
| a condition never holds: on an entity that never changes | 13 | 10 | 0.77 [0.50, 0.92] |
| a condition never holds: a template or another | 5 | 3 | 0.60 [0.23, 0.88] |
| a condition never holds: a time condition | 1 | 1 | |
| a condition never holds: a sensed or sun-state condition | 1 | 1 | |
| threshold out of the observed range | 3 | 3 | (all binding artefacts) |
| never armed | 2 | 2 | (both binding artefacts) |
| the actions end before any call | 3 | 2 | |
| not derived | 8 | not comparable | |

**Overall 82 of the 91 comparable: 0.90 [0.82, 0.95]**; 8 of the 99 (8 %) are not derived (the auditor: E 4, B 2,
G2 2), and no "several conditions" case was sampled. Every audited "threshold out of range" and "never armed"
reason is a binding artefact, as R4-C found.

### Item 11(a): the flags in the not-environment-coupled block (A12.9)

Read by hand (labels local). Frame relation, 15: **12 genuinely not environmental** (11 a month or day-of-month
template condition, which the four dates switch; 1 a UTC day number taken at a local hour, which the time zone
switches) and **3 detector leaks** (2 a Dutch quantity word the name vocabulary lacks; 1 a humidity read in an action's
`choose` condition, which `env_kinds` does not walk). Clock relation, 19: the same 12 genuine, 5 leaks (the 3 above,
one more Dutch temperature, and a sun condition inside an `and:` shorthand that `step_kinds` does not walk), and 2
binder artefacts (a device's LED brightness and a potentiometer bound as illuminance; the detector was right).

*Against Table VI:* the 13 flags with $\Phi_A$ = {clock} under each relation are all in this block: 12 are the genuine
date and zone cases and 1 is the action-level humidity leak, which the frame inference misses too. The block's other
flags have a sensed frame (frame 2, clock 5) or clock+sun (clock 1), so `frames_of` saw what `env_kinds` missed. The
detector therefore leaks 3 of the 15 frame flags (5 of the 19 clock flags): the 1,205 environment-coupled run
automations miss at least these.

### Item 12: the named variant frame+crossing+env_latency (A12.10)

On the `8fcd672` cache: 157 run automations have a computable sensed frame. The variant flags **199, the same set**
(none added, none lost); time_spread 16 (the published 8 plus A8.5's 8), **env_latency 4**. The 4 are exactly A8.5's 4
"latency" automations among the 8 that gain time_spread (the other 4 "precede": a clock trigger fires before the
crossing); all 4 also carry time_spread, all have conditions (clock+sensed 3, sensed+sun 1; temperature 3, illuminance
1), latency spreads 1,277-2,340 min. The published relation is unchanged.

### Item 13: the cover's upper bound (A12.11)

Coverage of 6 environments = flags with a witness pair inside them. MILP (scipy HiGHS, 600 s limit); local search from
greedy's 6 and 20 random starts.

| Relation, population | Greedy's 3 pairs | Greedy's 6, induced | Best found (local search = MILP incumbent) | Upper bound (MILP) | LP relaxation |
|---|---|---|---|---|---|
| frame, all 199 | 188 | 190 | 190 | **193.5** (time limit) | 199 (vacuous) |
| frame, training half 113 | 108 | 109 | 109 | **109, optimal** | 113 |
| clock, all 790 | 774 | 777 | 777 | 785.9 (time limit) | 790 |
| clock, training half 425 | 416 | 418 | 418 | 419.2 (time limit) | 425 |

So the best six-environment suite covers at most 193 of the 199 frame flags: greedy's 3 pairs are within 5 flags
(2.6 %) of the optimum and greedy's six environments within 3; on the training half greedy's six are optimal. The LP
relaxation is vacuous here (it spreads 6 over 48 environments), so the bound is the MILP's.

## Revision R7-D (Addendum 16): the engine-corrected sweep after Addendum 15

**Post hoc** (Addendum 16 and its dated deviation A16.3). Two engine changes, each with a failing test first:
- automations with `initial_state: off` are refused at binding as disabled at startup;
- the `sun` condition's `before` boundary (R7-C, `e6e3355`).

The first run, at `66b93f7`, gave one new frame flag. Its cause (A16.3) is the template budget, which was 0.5 s of
**wall-clock** time: a busy machine (three other lanes were running) failed a render of a `now()` template trigger in
one environment, and the trigger silently held false. The budget now counts CPU time (`db19e34`, failing test first).
The sweep ran again at `db19e34`: rung 4, current binder, device rule, recorded ERA5 weather, fresh traces, 3 workers,
2026-11-05 10:10–10:47Z. Aggregates are in `results/edt/r7d-66b93f7.json` and `results/edt/r7d-db19e34.json`
(`scripts/revision/r6d_compare.py`).

| | `8fcd672` (published) | `5b54586` (R6-D) | `66b93f7` (A16, wall-clock budget) | `db19e34` (A16.3) |
|---|---|---|---|---|
| bound / run | 9,515 / 5,926 | 9,515 / 5,926 | 8,590 / 5,843 | 8,590 / 5,843 |
| frame-relation flags | 199 | 199 | 197 (196 held, 1 new) | **196, all held with the same kinds** |
| clock-relation flags | 790 | 785 | 775 (774 held, 1 new) | 774, all held with the same kinds |
| time_spread (frame / clock) | 8 / 709 | 8 / 704 | 8 / 694 | 8 / 694 |
| negative control | 0 / 1,247 firing | 0 / 1,243 | 0 / 1,229 | 0 / 1,229 |
| environment-coupled never-firing | 390 | 395 | 387 | 387 |

- **The refusal.** It removes 925 bound automations (8,590 = 9,515 − 925), 83 of them run.
- **Lost flags at `db19e34`, against `8fcd672`.**
  - Frame relation: 3, all `initial_state: off` (R7-C counted 3 among the 199).
  - Clock relation: 16, of which 11 are `initial_state: off` and 5 are `kelvin`/`color_temp`, now refused as Home
    Assistant 2026.10.2 refuses them.
  - Nothing is new under either relation.
- **The one new flag at `66b93f7`.** It was the load-dependent template timeout and does not recur at `db19e34`. Lane
  R7-A's duplicated-environment control (Addendum 12) shows the same budget's one refusal; no flag changes there.
- **Reading.** With every engine defect the two differential tests found now fixed, and the population re-run, the
  frame relation's findings are the published 199 less the 3 automations Home Assistant would never have run.

## Revision R7-W (Addendum 14): recorded shortwave, k̂ again, three more ERA5 years

**Post hoc** (registered as Addendum 14 at `52ded7b`, after the fourth internal review, before its runs; the options at
`986b5d6`, the script `scripts/revision/r7w_weather.py`). Every run ran at `52ded7b` on 2026-11-05, 09:26–10:59Z,
with 4 workers under nice, one after another. Aggregates: `results/edt/r7w-f15fd97.json`. The report adds two
diagnostics written after the runs (A14.9, dated): weekday readers, and setback runs with a commanded setback. The
sweep at `8fcd672` stays the paper's primary result.

**The data.** 266 requests to the Open-Meteo archive, all answered (HTTP 200), each URL logged; nothing cached was
fetched again. The archive answers `snow_depth` with nulls under `models=era5`, so the snow depth is ERA5-Land's
(stated in A14.1 before any run). Every one of the 48 traces of the shortwave sweep read the recorded split and the
snow depth for all four of its days, and carries both knobs in its header.

### Item 9(a): the recorded split and the ground term, both on (A14.3)

The recorded beam share is larger than the assumed 0.8 (1 - cloud) in every one of the 24 city-date windows: over their
sun-up hours, the mean of the window means is **0.55 recorded against 0.32 assumed**. The largest gaps are where the
cloud-cover rule counts the sky as nearly overcast (Singapore 0.41–0.64 against 0.08–0.19; Phoenix in July 0.47
against 0.10). Two windows have snow deeper than 1 cm: Minneapolis January
(up to 0.07 m) and Stockholm January (up to 0.22 m), where the albedo is 0.6.

| | `8fcd672` (published) | `5b54586` (R6-D, options off) | `52ded7b`, both options on |
|---|---|---|---|
| bound / run | 9,515 / 5,926 | 9,515 / 5,926 | 9,515 / 5,926 |
| frame flags | 199 | 199 | **198** (184 fires_somewhere_only, 30 night_light, 8 flaps, 8 time_spread) |
| clock flags | 790 | 785 | 783 (700 time_spread) |
| negative control | 0 of 1,247 firing | 0 of 1,243 | 0 of 1,243 |

- **Frame relation, against `8fcd672`:** 198 of the 199 hold with the same kinds, 0 with other kinds; **1 is lost** (a
  fires_somewhere_only flag of a temperature reader); **0 are new**. Against R6-D the same: 198 held, 1 lost, 0 new.
- **Clock relation:** against `8fcd672`, 774 hold with the same kinds, 9 with other kinds, 7 are lost (6 time_spread, 1
  fires_somewhere_only; 5 sun readers, 2 temperature readers), 0 new. Against R6-D (the same engine), 2 are lost (both
  temperature readers) and 0 new: the other 5 are R6-D's own engine corrections.
- **Orientation witnesses.** Frame relation: 101 automations have one (100 at `8fcd672`): 95 held, 5 lost, 6 new;
  minimal witness orientation 101, date 96, city 1. Clock relation: 123 (123 at `8fcd672`): 119 held, 4 lost, 4 new.

So replacing the assumed split by the recorded one, and adding the snow-driven ground reflection, moves one frame flag
of 199 and about one in twenty of the orientation witnesses, in both directions, although the beam share it changes
is on average 0.23 higher. The axis that witnesses half of the frame flags does not rest on the 0.8 (1 - cloud) rule.

### Item 9(a): k̂ with the recorded split (A14.4)

The reproduction holds exactly: the assumed split gives R6-B's **k̂ = 0.25 [0.2052, 0.2894]** (2,072 hours, 51 days).
Over home B's 637 daylight hours the recorded beam share is 0.70 on average, the assumed one 0.51.

| Setting | k̂ [bootstrap 95 %] | per room (dining room / room) | k̂_lin | b(1), K | In R4-B's x0.25–x2? |
|---|---|---|---|---|---|
| assumed split (reproduction) | 0.25 [0.205, 0.289] | 0.264 / 0.236 | 0.287 | +7.29 | at its lower end |
| **recorded split** | **0.244 [0.203, 0.283]** | 0.258 / 0.230 | 0.277 | +7.53 | just below (0.244 < 0.25) |
| recorded split + ground term | 0.208 [0.171, 0.242] | 0.220 / 0.197 | 0.245 | +9.24 | below |
| assumed split + ground term | 0.213 [0.175, 0.246] | 0.225 / 0.201 | 0.253 | +9.00 | below |

The recorded split moves k̂ by -0.006, inside its own interval; the ground term moves it by -0.04 (it adds about a
sixth to the modelled solar gain: 123 -> 145 W mean with the recorded split, 121 -> 142 W with the assumed one). Home B still needs about a quarter of the model's solar
aperture, now a little less; with the ground term, R4-B's bracket no longer reaches it.

### Item 9(b): three more ERA5 years (A14.5)

The dates keep their month and day; their weekdays do not (A14.9): 2026's are Wednesdays (measured days Thursday and
Friday), 2023's all Saturdays, 2025's all Tuesdays, and **2024's one Sunday and three Mondays**.

| Year | run | frame flags | clock flags | negative control (flagged / firing) | frame flags vs `8fcd672`: same kinds / other kinds / lost / new | clock: lost / new |
|---|---|---|---|---|---|---|
| 2026 (`8fcd672`) | 5,926 | 199 | 790 | 0 / 1,247 | | |
| 2023 | 5,926 | 196 | 771 | 0 / 1,282 | 188 / 2 / 9 / 6 | 27 / 8 |
| 2024 | 5,926 | **238** | 822 | **30 / 1,252** | 191 / 0 / 8 / **47** | 14 / 46 |
| 2025 | 5,926 | 196 | 780 | 0 / 1,230 | 196 / 0 / 3 / 0 | 11 / 1 |

No environment failed in any year. Every lost frame flag in every year is fires_somewhere_only, of a temperature or
humidity reader (or, once a year, one reading no environment quantity).

- **2024 is a weekday artifact, not a weather one** (diagnostic, A14.9). Of 2024's 47 new frame flags, 44 read the day
  of the week, and 45 read no environment quantity at all; **all 30 flagged negative-control automations read the
  weekday**. The 30 are below A1.2's 5 % halt (2.4 %), so the run stands, but the negative control did its job: in a
  year whose dates fall on different weekdays the oracle sees the weekday, as it should. 360 of the 5,926 run
  automations read the weekday. In 2023, 5 of the 6 new frame flags and 4 of the 9 lost read it as well (their dates
  fall on another weekday than 2026's); in 2025, 2 of the 3 lost.
- **Stability of the 199** (A14.5): **185 are flagged in every one of the three years**, 196 in some year, 3 in none
  (all fires_somewhere_only: 2 temperature readers and 1 reading no environment quantity); 11 in two of the three.
  183 keep the same kinds in every year. By kind, held in every year: 171 of 185 fires_somewhere_only, and all 30
  night_light, 8 flaps and 8 time_spread. 190 are flagged in both 2023 and 2025, the two years whose dates share one
  weekday. 13 of the 199 read the weekday.
- **The 790 clock flags:** 755 are flagged in every year, 782 in some year.
- **Orientation witnesses** (frame relation; 100 at `8fcd672`): 97 in 2023 (91 of the 100 held), 92 in 2024 (90 held),
  96 in 2025 (91 held). Clock relation (123): 105, 101 and 116.

### Item 9(b): the setback breach count per year (A14.6)

R2-c's 22 setback automations alone, the core four (Minneapolis and Stockholm, January 14, both orientations), R4-B's
nominal corner; 88 runs per year. **The 2026 rerun reproduces R2-c exactly: 53 of 88.**

| Year | breaching runs (of 88) | automations | Minneapolis / Stockholm | coldest caused room, min / median / max | baseline's coldest room | outdoor air days 1–2, mean (min): Minneapolis / Stockholm | runs with a commanded setback (A14.9) |
|---|---|---|---|---|---|---|---|
| 2026 | **53** | 16 | 31 / 22 | 2.7 / 8.1 / 9.8 C | 15.6 C | -6.4 (-13.6) / +2.0 (+0.9) C | 69 |
| 2023 | **17** | 6 | 10 / 7 | 7.8 / 9.1 / 9.9 C | 17.0 C | +1.4 (-0.6) / +3.5 (+1.6) C | 62 |
| 2024 | **61** | 16 | 31 / 30 | -2.4 / 5.2 / 8.1 C | 16.0 C | -18.0 (-21.7) / -10.7 (-17.5) C | 62 |
| 2025 | **35** | 16 | 31 / 4 | 4.5 / 6.0 / 9.8 C | 15.3 C | -5.5 (-19.1) / +3.3 (-1.1) C | 61 |

- The count follows the January weather: **17, 35, 53 and 61 of 88** over the four years, from a mild Minneapolis
  January (2023, mean +1.4 C) to a very cold one (2024, -18 C, when Stockholm breaches as often as Minneapolis). In
  every year the baseline never goes below 15.3 C.
- Minneapolis breaches in all four years (31 of 44 runs in three of them, 10 in 2023); Stockholm from 4 to 30.
- The weekday moves which runs command a setback at all: 69 runs in 2026 (18 automations), 61–62 in the other years (16
  automations); 5 of the 22 read the weekday. Every breaching run is among the commanded ones in every year, so the
  spread from 17 to 61 is the weather's, on 61–62 commanded runs.

### Item 9(b): the best cover pair per year (A14.7)

| Sweep | clock relation, k = 1 pair (coverage all) | frame relation, k = 1 pair (coverage all) |
|---|---|---|
| `8fcd672` (2026) | Minneapolis 01-14 n0 + Stockholm 07-15 n180 (0.892) | Minneapolis 01-14 n0 + Stockholm 07-15 n180 (0.658) |
| `5b54586` (2026) | the same (0.892) | the same (0.658) |
| 2023 | Stockholm 01-14 n0 + Stockholm 07-15 n0 (0.895) | Stockholm 01-14 n180 + Stockholm 07-15 n0 (0.607) |
| 2024 | Seattle 01-14 n0 + Stockholm 07-15 n180 (0.906) | Stockholm 01-14 n0 + Stockholm 07-15 n180 (0.740) |
| 2025 | Minneapolis 01-14 n0 + Stockholm 07-15 n180 (0.919) | Minneapolis 01-14 n0 + Stockholm 07-15 n180 (0.755) |

**The best pair changes in two of the three years** (2023 and 2024, under both relations). What holds in every year is
its shape: a cold-climate January environment with Stockholm in July; which cold city and which orientation are the
weather's. Every frame flag of every year is covered by some pair.

### What this changes, stated

- **9(a):** the assumed beam share was too low by about 0.23 on average, but the frame flags and the orientation
  witnesses barely depend on it: 198 of 199 frame flags kept with their kinds, none new; 101 orientation witnesses
  against 100, 95 of them the same automations. k̂ moves from 0.25 to 0.244 with the recorded split, and to 0.21 with
  the ground term.
- **9(b):** the frame flags are stable across weather years: 185 of 199 (93 %) are flagged in each of 2023, 2024 and
  2025, and 196 in some year. The setback breach count is not: 17 to 61 of 88 over four Januaries, with 53 the recorded
  year's. The single-year count is one draw of a quantity the January weather sets; the paper should state it as such.
  The best cover pair is weather-specific in its cities and orientations.
- **A limitation found on the way:** dates that fall on different weekdays make the oracle see weekday conditions
  (2024: 47 new frame flags, 30 negative-control flags, all weekday readers). A3.1's choice of one weekday for every
  date is what keeps the registered sweeps free of this; a multi-year design must keep the weekday fixed or exempt
  weekday readers.

## Revision R7-B (Addendum 13): the room-model terms behind the flags

**Post hoc** (after the fourth internal review, items 6(a-c) and 11(b)), registered before its runs as protocol
Addendum 13 (`docs/experiments/edt-protocol.md`, commit `8dfc59e`), with one dated deviation (A13.6, below). New
knob: `INDOOR_T_OFFSET_C` (`vesper.env.indoor`). It adds a constant to every indoor room's temperature as its sensors
read it, and the humidity is read at that temperature; the heating control and the daylight are unchanged. It is
bit-identical at 0 by the extended fingerprint test. Script: `scripts/revision/r7b_room_terms.py`; numbers:
`results/edt/r7b-84ac429.json` (aggregates only).

Every setting recorded all 48 traces afresh in a cache of its own and replayed the **replay set** with the sweep's
worker (reporting rule `device`). The replay set is 954 run automations: the 199 frame flags, the 790 clock flags of
`8fcd672`, and every automation reading a temperature (206), a humidity (63) or an illuminance (67). It ran with 4
workers under nice; the 120 corners took 2.7 h, and no environment failed at any setting.

**The reference, and the deviation (A13.6).** The current code carries Addendum 10's two engine fixes, so a reference
recorded now reproduces R6-D's engine-corrected sweep `5b54586`, not `8fcd672`'s clock outcomes.
- Each of the three references (corners, offsets, the illuminance home) reproduces `5b54586` exactly: 0 of 45,792
  outcomes differ (0 of 3,216 for the 67 illuminance readers), and every family below is identical.
- Against `8fcd672` they reproduce the frame divergences exactly: 0 kind mismatches, so the 199 and the 100 frame
  orientation witnesses are identical. They differ in 3,012 of 45,791 outcomes (66 automations) and in the clock
  divergences of 5 (R6-D's 790 -> 785).
- So every robust core and possible set below is set against `5b54586`'s memberships. For the frame relation these
  are `8fcd672`'s exactly; the clock relation's are 785. The replay set and R4-A's rung-2.5 partition stay
  `8fcd672`'s.

**Families** (A13.1):
- frame flags (199);
- orientation witnesses under the frame relation: frame flags with two bearings of one city and date over which the
  frame-restricted divergences are not empty (100, R4-A's count);
- temperature flags: frame flags reading a temperature (104);
- humidity flags: frame flags reading a humidity (36);
- sensor-driven room-model flags: frame flags reading a sensed quantity and not frame-flagged at rung 2.5 (104);
- secondary: the clock flags (785), and the clock relation's outcome-class orientation witnesses (Table XI's and
  R6-B's definition; 123).

The **robust core** is the set flagged at every setting of the bracket, the reference included; the **possible set**
is the set flagged at any. The possible set is taken within the replay set, so it is a lower bound on the corpus-wide
union.

### Item 6(a): the flags over R4-B's 120 corners (A13.1)

The corners: gains 0/2/5 W/m² x roof and ground off/on x warm-up 1/5/10 days/periodic x capacity
80/110/165/260/370 kJ/(m² K). Open loop, every trace recorded afresh.

| Family | Published | Robust core (all 120) | Possible set (any of 120) | Lost somewhere / gained somewhere | Per corner, min-max |
|---|---|---|---|---|---|
| Frame flags | 199 | **159** | **200** | 40 / 1 | 182-199 |
| Orientation witnesses, frame relation | 100 | **42** | **109** | 58 / 9 | 80-101 |
| Temperature flags | 104 | **80** | **105** | 24 / 1 | 91-105 |
| Humidity flags | 36 | **21** | **36** | 15 / 0 | 21-36 |
| Sensor-driven room-model flags | 104 | **65** | **105** | 39 / 1 | 87-104 |
| Clock flags (secondary) | 785 | 728 | 787 | 57 / 2 | 751-785 |
| Clock outcome-class orientation witnesses (secondary) | 123 | 48 | 129 | 75 / 6 | 91-123 |

44 of the 199 frame flags change their kinds at some corner.

*What moves it* (mean count per level, over the other three factors):

| Family | Warm-up 1 / 5 / 10 days / periodic | Gains 0 / 2 / 5 W/m² | Roof and ground off / on | Capacity 80 / 110 / 165 / 260 / 370 |
|---|---|---|---|---|
| Frame flags | 192.5 / 189.3 / 188.6 / 188.3 | 190.8 / 190.0 / 188.2 | 188.8 / 190.6 | 189.7 / 189.5 / 190.7 / 189.7 / 188.7 |
| Orientation witnesses, frame | 92.6 / 92.5 / 91.3 / 91.3 | 94.0 / 91.9 / 90.0 | 91.9 / 92.0 | 92.6 / 92.8 / 93.0 / 91.4 / 90.0 |
| Temperature flags | 100.3 / 103.6 / 103.5 / 103.4 | 102.3 / 102.7 / 103.1 | 102.4 / 103.0 | 103.8 / 103.0 / 103.8 / 102.2 / 100.8 |
| Humidity flags | **33.6 / 26.7 / 26.1 / 25.9** | 29.6 / 28.4 / 26.2 | 27.4 / 28.7 | 26.9 / 27.5 / 27.9 / 28.7 / 29.2 |
| Room-model flags | 97.8 / 94.3 / 93.6 / 93.3 | 95.9 / 95.1 / 93.3 | 93.9 / 95.7 | 94.7 / 94.5 / 95.7 / 94.9 / 93.9 |

- The frame flags hold up per corner (182-199 of 199) but less well as a set: 40 are lost at some corner, so the
  robust core is 159. Most of the orientation witnesses move: 42 of 100 are witnesses at every corner, 109 at some.
- **The warm-up is the largest single term for the humidity flags.** All 36 hold only at the 1-day warm-up (18 of its
  30 corners). At 5 days or more no corner keeps more than 31, and most keep 26-27.
- The fewest humidity flags (21) are at 9 corners, all with 5 W/m² of gains, no roof or ground, and a warm-up of 5 days
  or more. So about a third of the humidity flags rest on the run's start state (every room at 20.5 C at the first
  instant), not on the climate alone. The internal gains are second (fewer flags with more gains).
- Capacity, the largest term for the setback population (R4-B), moves the mean flag counts by at most 4.
- Extremes: frame flags fall to 182 at four corners (G0-Fon-Wpss-C370, G5-Foff-W10-C165, G5-Foff-W10-C260,
  G5-Foff-Wpss-C260). Witnesses range from 80 (G0-Foff-W1-C370) to 101 (G0-Fon-W1-C110). The count is not monotone in
  any factor, as R6-B found for the aperture.

### Item 6(b): an indoor-temperature offset (A13.2)

The registered values are the review's:
- -1.7 and -0.9 K: home A's rooms are simulated 0.9-1.7 C too cold (R2-a, and the validation report's "T on A");
- +7 K: home B's rooms are about 7 K too warm (b(1) = +7.29 K, R6-B);
- +2 K: **no indoor residual of +2 K is measured**. It is Reviewer 3's "±2 K for symmetry".

The values are residuals (model minus measured). An offset of the residual's sign adds to the error; the correcting
offset has the opposite sign. So the opposite signs were run as a secondary set.

| Offset (K) | Frame flags (of 199) | Orientation witnesses, frame (of 100) | Temperature flags (of 104) | Humidity flags (of 36) | Room-model flags (of 104) | Clock flags (of 785) |
|---|---|---|---|---|---|---|
| -7 (secondary) | 197 | 95 | 102 | 36 | 102 | 783 |
| -2 (secondary) | 202 | 106 | 107 | 36 | 107 | 788 |
| **-1.7** | 200 | 103 | 105 | 36 | 105 | 786 |
| **-0.9** | 199 | 103 | 104 | 36 | 104 | 785 |
| 0 (reference) | 199 | 100 | 104 | 36 | 104 | 785 |
| +0.9 (secondary) | 193 | 91 | 99 | 36 | 99 | 779 |
| +1.7 (secondary) | 185 | 81 | 91 | 36 | 91 | 772 |
| **+2** | 185 | 86 | 92 | 35 | 91 | 773 |
| **+7** | 148 | 49 | 71 | **19** | **54** | 737 |

| Family | Published | Registered {0, -1.7, -0.9, +2, +7}: robust core / possible set | All nine values: robust core / possible set |
|---|---|---|---|
| Frame flags | 199 | **146 / 201** | 141 / 204 |
| Orientation witnesses, frame relation | 100 | **40 / 109** | 33 / 113 |
| Temperature flags | 104 | **69 / 106** | 64 / 109 |
| **Humidity flags** | 36 | **19 / 36** | 19 / 36 |
| **Sensor-driven room-model flags** | 104 | **52 / 106** | 47 / 109 |
| Clock flags (secondary) | 785 | 734 / 788 | 729 / 791 |
| Clock outcome-class orientation witnesses (secondary) | 123 | 69 / 130 | 64 / 133 |

The review was right that this is a governing term.
- Colder readings (down to -7 K) change little: every humidity flag holds, and the room-model count stays within 3 of
  the reference.
- Warmer readings remove flags, steadily. +0.9 K takes the room-model count from 104 to 99, +2 K to 91.
- **At +7 K (home B's residual) 17 of the 36 humidity flags and 50 of the 104 room-model flags go**, and 51 of the 199
  frame flags in all.
- 56 of the 199 frame flags change their kinds at some registered value.

So 19 of the 36 humidity flags survive every offset run; the other 17 are all lost at +7 K (one of them also at +2 K).
Of the 104 room-model flags, 52 survive the registered bracket. These flags sit on one side of a threshold: they need
the rooms no warmer (or no drier) than the model makes them.

### Item 6(c): the humidity class (A13.3)

**The class is fitted.** EN ISO 13788's class was chosen by the environment validation's Addendum 2
(`docs/experiments/environment-validation.md`, registered at `3bc6cda` before the fit).
- On dataset A it is the class among 0-5 with the smallest H median RMSE: class 0 9.3, **class 1 8.2**, 2 14.2, 3
  22.0, 4 30.0, 5 37.1 points.
- It was judged once on B (16.2 -> 14.9 points) and so adopted by the addendum's rule
  (`results/environment/fit-humidity-20261104T065407Z.json`, commits `c383b91` and `36a13f9`).
- `vesper/env/indoor.py` says so ("1: fitted on dataset A and held out on B"). `results-2026-11-04-environment.md`
  adds that the standard describes class 1 as "unoccupied buildings, storage of dry goods", calls it "a fitted
  value, not a description of the homes", and notes it was chosen with A's cold bias in place.
- The phrase "deliberately conservative", or anything like it about the class, appears nowhere in `vesper/` or
  `docs/`. It is the manuscript's alone, and the repository contradicts it.

R4-B's class 2 and 3 runs and A13.2 above are the bracket around it.

### Item 11(b): illuminance with a change-reporting sensor (A13.4)

- Only the catalog's Zigbee products have illuminance (none in Z-Wave, Matter or Tasmota). 143 of them expose no
  occupancy, and every illuminance product with a reporting rule has the catalog's library default: on change, at
  most every 10 s, at least hourly.
- The product run is `zigbee:tuya:s-lux-zb` ("Light sensor", L1), placed as `<room>_lux1` beside each of the 8
  RTCGQ11LM sensors (7 rooms and the porch).
- Under the `device` rule it reports on change; the RTCGQ11LM reports only with motion or on its hourly heartbeat.
  The new sensor's rule is assumed (the library default), not measured.

| The 67 run illuminance readers, rung 4, 48 environments | RTCGQ11LM (reference) | Change-reporting sensor |
|---|---|---|
| Frame flags | 7 | 7 (0 lost, 0 gained, 0 kinds changed) |
| Clock flags | 15 | 15 (0 lost, 0 gained, 1 kinds changed) |
| Fire in some environment | 17 | 17 |

- 65 of the 67 name an RTCGQ11LM illuminance entity (79 entities replaced); the other 2 name none.
- The change-reporting sensor moves 526 of the 3,216 outcomes, in 16 automations: first firings move, because the
  readings now arrive within seconds instead of within the hour.
- But no flag is gained or lost, and the same 17 fire somewhere. So the illuminance path's few flags are not an
  artefact of the RTCGQ11LM's heartbeat.
- 50 of the 67 still fire nowhere with either sensor. That is the resident-free home's doing (no motion, no lamps
  switched), not the sensor's: the illuminance class stays mostly outside what the instrument can test.

### What this changes, stated

- Over R4-B's 120 corners, **159 of the 199 frame flags are flagged at every corner**, and 200 at some. By family
  (robust core / possible set): temperature 80 / 105 of 104, humidity 21 / 36 of 36, room-model 65 / 105 of 104,
  frame orientation witnesses 42 / 109 of 100.
- The humidity flags' governing terms are the indoor temperature level and the run's start state, not the aperture
  or the class. 19 of 36 survive the registered offset bracket and 21 of 36 survive the corners; a warm-up of 5 days
  or more takes 5-15 of them.
- The core over both brackets together was not computed (Addendum 13 registered each bracket alone). In each family
  it is at most the smaller of the two cores.
- The class is fitted, not "deliberately conservative".
- A change-reporting lux sensor changes no flag among the 67 illuminance readers.

*Concerns, stated.*
- The offset holds the heating trajectory fixed. What it brackets is a reading error of +7 K with the true room at
  the model's temperature, not a house that is really 7 K warmer (such a house would heat less).
- The corners and offsets are open loop over a 954-automation replay set, so flags outside it are not seen.
- The warm-up's large effect is partly the start temperature (20.5 C) every run assumes, which Addendum 13 did not
  vary on its own.
- The light sensor's reporting is the catalog default, not a measured rule.

## Revision R8-X (Addendum 17): the joint robust core, the glazing core, the sun reference, the exemption's residual, the sun flags' location, the R7 rates

**Post hoc: written after the fifth internal review, before its runs.** Registered as protocol Addendum 17 at `74ca232`,
committed alone before any number below was computed. Nothing was run again: every number is recomputed from saved
runs (R7-A's and R7-B's open-loop pickles, and whole sweeps). Script: `scripts/revision/r8x_analyses.py` (tests
`tests/edt/test_r8x_analyses.py`, written first). Numbers: `results/edt/r8x-516ece6.json` (aggregates only). At most 3
worker processes, under nice. Review 5's item numbers are in brackets.

### Item 1: the joint robust core (A17.1)

**The checks hold.** Recomputed here from the saved outcomes and first-firing instants, each source's own cores
reproduce the published ones:
- R7-A: every setting together 42 / 88 / 34 (witnesses / temperature / humidity), and all six bracket rows;
- R7-B: corners 159 / 42 / 80 / 21 and registered offsets 146 / 40 / 69 / 19 (frame flags / witnesses / temperature /
  humidity);
- R7-W's shortwave run: 198 frame flags and 95 witnesses held;
- R7-A's x1 replay and R7-B's two references give the published members of all four families exactly.

R7-A's frame-flag family was never computed there; over its 13 settings it is 179 of 199.

The joint core is the published members held at every one of the **139 settings**: R7-A 13, R7-B's corners 120, its
registered offsets 5, R7-W 1. The joint possible set is the union, each source within its own replayed set (327, 954,
or all 5,926 run automations).

| Family | Published | R7-A (13) | R7-B corners (120) | R7-B offsets (5) | R7-W (1) | Glazing (A17.2) | **Joint core (139)** | Joint possible set | **Joint core with glazing (141)** |
|---|---|---|---|---|---|---|---|---|---|
| Frame flags | 199 | 179 | 159 | 146 | 198 | 187 | **129** | 203 | **128** |
| Orientation witnesses | 100 | 42 | 42 | 40 | 95 | 79 | **7** | 111 | **7** |
| Temperature flags | 104 | 88 | 80 | 69 | 103 | 96 | **54** | 107 | **54** |
| Humidity flags | 36 | 34 | 21 | 19 | 36 | 35 | **18** | 37 | **18** |

*Which source binds* (the joint core of the 139 without each source in turn):

| Family | Without R7-A | Without corners | Without offsets | Without R7-W |
|---|---|---|---|---|
| Frame flags | 132 | 135 | 156 | 129 |
| Orientation witnesses | 22 | 13 | 25 | 7 |
| Temperature flags | 55 | 60 | 79 | 54 |
| Humidity flags | 19 | 18 | 20 | 18 |

- **The single-bracket cores do not compose.** The solar-gain brackets (R7-A), the room-model corners (R7-B) and the
  indoor offset (R7-B) each keep about 40 of the 100 orientation witnesses, but not the same 40. R7-A's and the
  offsets' cores share only 13, and all three share 7. So **7 of the 100 orientation witnesses are held at every
  sampled setting.** The abstract's "42 of 100" is one bracket's core, not the propagation's output.
- The frame flags keep **129 of 199** jointly (65 %), not the 159 of the corners alone; the indoor offset takes the
  most (without it, 156).
- Temperature flags: **54 of 104**. Humidity flags: **18 of 36**; the offset and the corners remove almost the same
  ones (they share 19).
- With the secondary offsets {+1.7, +0.9, -2, -7} K added as well (145 settings), the cores are unchanged: 128, 7, 54
  and 18.
- The joint possible sets add at most 11 automations to any family (witnesses 111 of a published 100).

*What these numbers are.* The settings move one bracket at a time around the nominal model; no product of brackets was
run. So the joint core is the matrix-AND over sampled settings. **It is an upper bound on the core over the brackets'
product**, which can only be smaller; the possible set is a lower bound on that product's union. And within each
bracket, an intersection over sampled points over-estimates the intersection over the bracket's interval: a finding
lost between two sampled points is not seen (R7-A found the aperture counts not monotone, 56 / 61 / 68 at x0.25 / 0.21
/ 0.29, so the gaps matter). Both caveats make the true core smaller, not larger.

### Item 4(a): the glazing variants (A17.2)

The standard home swept at `ba8a54c` reproduces all four families exactly. Per variant (of the published family):

| Variant | Frame flags held / lost / gained | Witnesses held / lost / gained | Temperature | Humidity |
|---|---|---|---|---|
| glazing x0.5 | 190 / 9 / 0 | **82 / 18 / 5** | 96 / 8 / 0 | 36 / 0 / 0 |
| glazing x2 | 196 / 3 / 2 | **95 / 5 / 7** | 104 / 0 / 1 | 35 / 1 / 0 |
| plan seed 8 (secondary) | 196 / 3 / 0 | 92 / 8 / 6 | 101 / 3 / 0 | 36 / 0 / 0 |
| mirror east-west (secondary) | 199 / 0 / 0 | 95 / 5 / 0 | 104 / 0 / 0 | 36 / 0 / 0 |

- **Over the glazing bracket {x0.5, x1, x2} the orientation-witness core is 79 of 100** (possible set 109), the frame
  flags 187 of 199, temperature 96 of 104, humidity 35 of 36. Halving the panes costs 18 witnesses; doubling them, 5.
- Over the two plan variants (secondary) the witness core is 88. The mirrored home keeps every frame flag but moves 5
  witnesses: which bearing witnesses an automation depends on which rooms face which way.
- **The joint core including the glazing variants (141 settings) is 128 / 7 / 54 / 18.** The glazing removes one frame
  flag beyond the 139 settings and no witness: the witnesses it moves are already lost elsewhere.

### Item 6(b): the sun reference fixed to the first named event (A17.3)

The recomputation with the published references reproduces every published frame divergence of the population (0
mismatches).
- **114 run automations** name two or more distinct sun instants, so the nearest-instant rule chooses among them; 5 are
  frame flags.
- **101** name two or more distinct events: 98 both sunrise and sunset (R6-A's 98, reproduced) and 3 with an elevation
  crossing. 4 of the 101 are frame flags.
- **Fixed to the first named instant**, the frame relation flags **210**, not 199: **11 gained, 0 lost**, and 2 change
  kinds. All 13 changes are `time_spread`, which goes from 2 to 15 in the 114. 12 of the 13 are among the 101; the
  other names one event at two offsets. No orientation witness changes.

So the choice is not small. These automations fire at sunrise in some environments and at sunset in others; measured
from one event, the other event's firings sit hours away. A first-named rule therefore flags ordinary
"open at sunrise, close at sunset" automations as time-spread. The nearest-instant rule is the right reading, and it
should be made formal as a set-valued reference rather than replaced.

### Item 6(c): the exemption's residual exposure (A17.4)

Population: the 5,727 run automations that are not frame flags (5,926 - 199). The crossing-time construction reaches
157 run automations (66 of them in the 5,727).

| Outside the crossing-time construction | Automations (of 5,727) | Fire in 2 or more environments | Clock-relation time_spread | Frames |
|---|---|---|---|---|
| **only reference-free frames (the review's)** | **111** | 21 | **20** | sensed 104, sun without a reference 7 |
| at least one reference-free frame (the exemption's full reach) | 148 | 32 | 21 | also clock+sensed 25, sensed+sun 10, clock+sensed+sun 2 |

- **111 non-flagged run automations declare only reference-free frames and fall outside the crossing-time
  construction.** 90 of them fire in at most one environment, where no spread can exist.
- **20 of them carry `time_spread` under the clock relation**: a first firing whose time of day spreads by more than
  3 h, which the frame relation leaves unjudged. 17 of the 20 read a temperature. This is an upper bound on what the
  exemption hides: a sensed rule may be right to fire hours apart in two climates.
- Counting every automation with any reference-free frame (a non-computable frame counts as invariant, so one is
  enough to exempt it) gives 148, of which 21 carry a clock time spread.
- Beside A8.5's measured part (lifting the exemption on its 157 adds 0 flags), the unmeasured exposure is therefore at
  most **20 of 5,727 automations (0.35 %)**, 21 under the wider reading.

### Item 7(b): the location precision of the 44 sun-frame flags (A17.5)

All 44 sun-reading frame flags were read by hand, R4-C's way. The standard home rebuilt for the audit is identical to
`8fcd672`'s. Labels are local.
- **Located: 26**, 5 of them with no room-scoped bound role.
- **Mislocated: 18.** Of these, 10 are an outdoor place bound indoors (a carport, a driveway, a garden, a balcony, a
  front-door or porch light, an outdoor sign, a pond pump, an outdoor weather station), 4 name the room in the entity
  (a bedroom, a kitchen, an ensuite), 3 name it only in the alias (a bedroom, a hallway), and 1 is an outbuilding.
- Uncertain: 0.
- **Location precision 26 / 44 = 0.59, Wilson 95 % [0.44, 0.72], repository bootstrap [0.41, 0.76]** (33
  repositories).

So the sun readers are not exempt from binder error: two in five are mislocated. Two facts temper what this means for
the flags.
- **Only 5 of the 18 have the mislocated role read by a trigger or a condition.** The other 13 bind a light or switch
  the rule only acts on. VESPER's oracle judges when the rule fires and whether a lamp stays on; neither depends on the
  room a commanded light is in.
- **The binder's room rule is the cause, not the sun.** The binder has no outdoor place but the porch, so a light
  named for a carport, driveway or garden goes to the living room.

*The binder discount, extended* (the primary rule as registered: every mislocation counts as a mis-bind, as the review
reads it). A12.5's D recomputes to 158.45 (reported as 158.5).
- 14 of the 18 mislocated are among the 38 sun readers outside the 148. **D_ext = 158.5 - 14 = 144.5 of 199,
  repository bootstrap [122.9, 162.9]** (94 repositories; the 148 and the 38 together).
- Secondary: counting also the 3 sun readers inside the 148 whose actions this check found mislocated, though R4-C's
  sensor labels are correct: 141.4 [119.8, 159.5]. The fourth inside, the outdoor illuminance, is already a mis-bind
  there.
- If only the mislocations a guard reads counted, the discount would be 3 (the guard-read ones among the 38), not 14.
  The 14 is the review's reading, not the oracle's.

### Item 2: every rate in the R7 sections (A17.6)

29 rates were checked across R7-A, R7-B, R7-C, R7-D and R7-W against their numerators and denominators: Wilson
intervals, percentages and stated shares. The environment-coupled null rates and the cover coverages were checked
against the result files.

**27 hold. Two do not:**
- *R7-B, item 6(a):* "about a third of the humidity flags rest on the run's start state". The 21-flag corners lose 15
  of 36, **42 %**. They also carry 5 W/m² of gains and no roof or ground. The warm-up alone moves the mean count from
  33.6 to 26.7, that is 6.9 of 36 or **19 %**. Neither reading is a third. It should read "between a fifth (the warm-up
  alone) and two fifths (the extreme corners)".
- *R7-W, item 9(b) A14.7:* the 2024 frame-relation pair coverage is **0.739** (176 / 238 = 0.73950), not 0.740. The
  result file's 0.7395 was rounded twice.

The paper's §V-B sentence the review cites does not occur in these documents: R4-A's table pairs 771 / 1,205 and
184 / 1,205 correctly (§"Revision R4-A").

Also noted, not a rate: R7-B's reference check counts 45,792 outcomes against `5b54586` and 45,791 against `8fcd672`.
The difference is the one outcome `8fcd672` refused in one environment; both are right.

### What this changes, stated

- **The propagation's own output is the joint core:** 129 of 199 frame flags, 54 of 104 temperature flags, 18 of 36
  humidity flags and **7 of 100 orientation witnesses** hold at all 139 sampled settings. Adding the glazing variants
  (141 settings) gives 128 / 54 / 18 / 7.
- These are upper bounds on the core over the brackets' product, and over each bracket's interval.
- The orientation witnesses do not survive the brackets jointly. Each bracket keeps about 40, but a different 40. The
  orientation-relevant glazing bracket alone keeps 79, and the recorded beam keeps 95, so the witnesses are robust to
  the orientation terms and fragile to the room-temperature level and the gain magnitude.
- The nearest-instant sun reference matters: replacing it by the first named event adds 11 frame flags, all spurious
  time spreads.
- The sensed exemption leaves at most 20 non-flagged run automations with an unjudged time-of-day spread.
- The sun-frame flags are mislocated in 18 of 44, but only 5 through a role their timing reads. As the review reads
  the discount, D falls from 158.5 to 144.5 [122.9, 162.9].
- Two R7 rates were wrong: "about a third" (two readings, 19 % and 42 %) and 0.740 for 0.739.

*Concerns, stated.*
- The joint core weights every setting equally and is sensitive to the number of settings. 120 of the 139 are R7-B's
  corners, and any one setting can remove a member.
- The 7 is a demanding count. A witness needs some orientation pair to diverge, and a small change can move that to
  another pair or to none; holding at all 139 settings is a strict test.
- The location labels are one reader's (mine), against R4-C's conventions, with no second rater.

## Revision R8-Y (Addendum 18): per-room thermostats, a wall/glass U corner, and the orientation witnesses over generated plans

**Post hoc** (after the fifth internal review, items 5 and 4(b)), registered before its runs as protocol Addendum 18
(`docs/experiments/edt-protocol.md`, commit `c415635`), with additions after the runs dated there (A18.5). New knobs
(`vesper.env.indoor`, bit-identical at their defaults by the extended fingerprint test and a fingerprint of the
hallway-heated test house taken before the option): `U_WALL` (0.35), `U_WINDOW` (1.4) and `PER_ROOM_THERMOSTAT` (0).
New home variants: `plan-seed9`, `plan-seed11`, `plan-seed16`. Script: `scripts/revision/r8y_physics.py` (tests
`tests/edt/test_r8y_physics.py`); numbers: `results/edt/r8y-1d03220.json` (aggregates only). 4 workers under nice;
the 2,668 closed-loop runs took 12.5 min, the five plan replays 3.6 min; no run or environment failed.

### Item 5(a): the setback population with a thermostat in every room (A18.1)

R2-c's 22 setback automations, each alone, closed loop, in the core four (Minneapolis and Stockholm, 2026-01-14, plan
north 0 and 180): 88 runs, each against its own setting's baseline, R2-c's causal rule, R4-B's nominal corner. With the
option on, every heated room has its own thermostat at the hallway thermostat's setpoint and mode (a command to the
hallway thermostat sets them all; A18.0's rule).

- *Check.* The hallway setting (the model as built) **reproduces R2-c step 2 exactly**: the same 53 breaching runs, and
  every coldest caused room, caused-cell count, baseline minimum, coldest room, largest drop and setback length (0
  mismatches).

| Thermostats | Breaching runs (of 88) | Automations | Minneapolis / Stockholm | Caused cells (of 616) | Coldest caused room per breaching run, min / median / max | Coldest room per run, all 88 | Baseline coldest room |
|---|---|---|---|---|---|---|---|
| hallway (as built) | **53** | 16 | 31 / 22 | 315 | 2.7 / 8.1 / 9.8 C | 2.7 / 9.3 / 18.5 C | 15.6 C |
| per room | **46** | 13 | 24 / 22 | 279 | **2.7 / 7.2 / 9.9 C** | 2.7 / 9.85 / 19.7 C | 19.7 C |

- **The per-room breaching runs are a subset of the hallway's** (46 of 53; 0 gained). The 7 lost are Minneapolis runs of 4
  automations (A8, A12, A21 two each, A18 one) whose coldest room now stays at 10.0-10.8 C. Over all 48 environments the
  per-room setting breaches in 46 of 1,056 runs, **all in the core** (0 outside it).
- *The coldest room moves.* Under the hallway thermostat the coldest room of the 88 runs is bedroom 1 in 77 and bedroom
  2 in 11; with a thermostat per room it is bedroom 1 in 48, **the bathroom in 36** and bedroom 2 in 4. 36 of 88 runs
  change their coldest room (bedroom 1 -> bathroom 29, bedroom 2 -> bathroom 7), 17 of them among the 53 runs breaching
  in either; the coldest room's minimum rises by 0.0 / 0.9 / 4.1 K (min / median / max). Of the breaching runs, the
  coldest caused room is bedroom 1 in 47 and bedroom 2 in 6 (hallway), bedroom 1 in 28, the bathroom in 17 and bedroom
  2 in 1 (per room). Caused breaches reach every room in both settings: 40-49 caused cells per room under the hallway
  thermostat, 37-45 per room.
- *Why the minimum barely moves (A18.5, a diagnostic after the runs).* With a thermostat per room, a 7 C setback is
  held: the coldest room of a setpoint-only rule bottoms out at **6.7 C** (the setpoint less the 0.3 K band). The
  per-room minimum below 6.7 C comes from **2 automations that turn the thermostat off** (of 4 that do; A11 and A13):

| Breaching runs, coldest caused room min / median / max | Setpoint-only rules (18) | Rules that turn the thermostat off (4; 2 breach) |
|---|---|---|
| hallway | 48 runs, 5.2 / 8.3 / 9.8 C | 5 runs, 2.7 / 3.5 / 8.4 C |
| per room | 41 runs, **6.7** / 7.7 / 9.9 C | 5 runs, 2.7 / 3.8 / 8.5 C |

  Per room, a setpoint-only rule's coldest room ends 0.3 K below to 10.2 K above its commanded setpoint (median 2.35 K
  above). So the hallway thermostat's distribution of heat is what takes a setpoint-only rule's rooms 1.5 K below its
  setpoint (5.2 C at 7 C); the 2.7 C extreme is a rule that switches the heating off, which no thermostat layout holds.

### Item 5(b): the wall/glass U corner (A18.2)

The same 88 runs at U_wall {0.35, 0.6, 1.0} x U_glass {1.4, 2.0, 2.8} W/(m² K), everything else nominal. **(0.35, 1.4)
is the current setting** (the row above). The standard home's H runs from 77.8 W/K (as built: walls 28.3, glass 14.2,
ventilation 35.3) to 144.6 W/K at (1.0, 2.8) (walls 80.8, glass 28.5).

| U_wall / U_glass | H (W/K) | Hallway: breaching runs (of 88), Minneapolis / Stockholm, caused cells | Hallway: coldest caused room min / median / max | Hallway: baseline coldest | Per room: breaching runs | Per room: coldest caused room min / median / max |
|---|---|---|---|---|---|---|
| **0.35 / 1.4** | 77.8 | **53**, 31 / 22, 315 | 2.7 / 8.1 / 9.8 C | 15.6 C | 46 | 2.7 / 7.2 / 9.9 C |
| 0.35 / 2.0 | 83.9 | 53, 31 / 22, 345 | 1.8 / 7.6 / 9.3 | 15.4 | 51 | 1.8 / 7.7 / 9.9 |
| 0.35 / 2.8 | 92.0 | 53, 31 / 22, 359 | 0.7 / 7.0 / 8.5 | 15.1 | 53 | 0.9 / 7.1 / 9.5 |
| 0.6 / 1.4 | 98.0 | 54, 32 / 22, 363 | -0.1 / 6.5 / 9.8 | 14.9 | 53 | 0.0 / 6.7 / 8.9 |
| 0.6 / 2.0 | 104.1 | 55, 33 / 22, 369 | -0.7 / 6.3 / 9.8 | 14.8 | 53 | -0.6 / 6.7 / 8.5 |
| 0.6 / 2.8 | 112.2 | 57, 33 / 24, 376 | -1.4 / 6.1 / 9.7 | 14.5 | 55 | -1.3 / 6.7 / 9.8 |
| 1.0 / 1.4 | 130.3 | 63, 33 / 30, 409 | -3.0 / 5.3 / 9.4 | 14.0 | 61 | -2.9 / 6.7 / 9.8 |
| 1.0 / 2.0 | 136.4 | 63, 33 / 30, 414 | -3.4 / 5.1 / 9.3 | 13.8 | 63 | -3.2 / 6.7 / 9.8 |
| 1.0 / 2.8 | 144.6 | **63**, 33 / 30, 421 | **-3.8** / 4.9 / 8.8 | 13.6 | 63 | -3.7 / 6.7 / 9.4 |

- **Every step toward a leakier envelope strengthens the finding**: under either thermostat layout the breaching-run
  sets are nested along both U axes (each a subset of the next), the count rises 53 -> 63 (hallway) and 46 -> 63 (per
  room), and the coldest caused room falls to -3.8 C. No baseline room falls below 10 C at any corner (hallway coldest
  13.6 C; per room 19.7 C everywhere).
- At the pre-1980 corner (1.0 / 1.4 and leakier) the per-room count reaches the hallway's (61-63), so the
  thermostat layout matters only for a well-insulated house.
- Split as in A18.5, the setpoint-only rules under per-room thermostats bottom out at **6.7 C at every corner**; their
  hallway minimum falls from 5.2 C to 2.1 C with U. The sub-6.7 C per-room minima at every corner are the two
  thermostat-off rules (2.7 C as built, -3.7 C at 1.0 / 2.8).

### The setback finding, restated (A18.2's reading)

Over the registered primary settings (the hallway thermostat at the nine U corners, and per-room thermostats as built),
**46 to 63 of the 88 core cold-January runs breach**, with the coldest caused room between **-3.8 and 9.9 C**; the
secondary per-room x U product gives the same ranges (46-63; -3.7 to 9.9 C). The finding survives a thermostat in
every room in kind (46 of 88, every one also breaching under the hallway thermostat) and is strengthened by every
leakier envelope. What changes is its mechanism's attribution: with per-room thermostats a setpoint-only setback
holds every room at its setpoint (6.7 C at 7 C, still below 10 C), so the "coldest room reaches 2.7 C" is the
thermostat-off rule's, and a setpoint-only rule's coldest room below its setpoint (5.2 C as built) is the hallway
thermostat's distribution of heat.

### Item 4(b): the orientation witnesses over generated plans (A18.3)

The generator's window rule offers only four room -> facing assignments over seeds 7-39 (the living room's panes always
face the plan's top, the kitchen's the bottom, bedroom 1's west, the office's east; only the bathroom and bedroom 2
vary). A18.3's rule gave **seeds 11 and 16** (the two assignments not yet swept) and **seed 9** (seed 8's assignment,
a larger living-room pane). In each home, all 48 rung-4 traces were recorded afresh (0 copied; 48 recorded each) and the
199 frame flags of `8fcd672` replayed on them.

- *Check (i).* The standard home, built and recorded afresh, reproduces `8fcd672`'s frame flags, kinds and orientation
  witnesses over the 199 exactly (0 mismatches each), and `5b54586`'s outcomes exactly (0 of 9,552). It differs from
  `8fcd672` in 470 outcomes, the count of R7-A's dated deviation (Addendum 10's engine fixes, which `5b54586` carries);
  none changes a flag, a kind or a witness.
- *Check (ii).* Seed 8 replayed this way agrees with A8.8's full sweep of `plan-seed8` on every frame flag, kind and
  witness over the 199 (0 mismatches). So the restricted replay is the full sweep for these automations.

| Home | Frame flags kept (of 199, same kinds) | Orientation witnesses kept (of 100): temperature / humidity / neither (of 64 / 30 / 6) | Lost | New among the 199 |
|---|---|---|---|---|
| seed 9 (new) | 196 | **93**: 61 / 28 / 4 | 7 | 5 |
| seed 11 (new) | 199 | **94**: 62 / 27 / 5 | 6 | 5 |
| seed 16 (new) | 197 | **94**: 62 / 27 / 5 | 6 | 1 |
| seed 8 (A8.8's plan, restricted replay) | 196 | 92: 59 / 28 / 5 | 8 | 6 |
| mirror east-west (A8.8's full sweep) | 199 | 95: 61 / 28 / 6 | 5 | 0 |

- **The core over the three new plans: 86 of the 100 orientation witnesses** (temperature 58 of 64, humidity 25 of
  30, neither 3 of 6); 9 hold in two of the three, 5 in one, none in none. The possible set is 105 (5 new, within the
  199).
- Secondary: over the four generated plans (with seed 8) the core is **85**; with the mirrored home too, **81** (10
  hold in four of the five, 5 in three, 4 in two). The possible set is 107.
- The frame flags themselves: 194 of 199 hold in every plan (all five homes), none is gained.

So the floor plan is a smaller term for the orientation witnesses than the lumped solar gain (R7-A: 42 of 100 over
13 settings, 47 over k̂'s interval): 81-86 of the 100 do not owe their orientation sensitivity to the standard home's
window arrangement.

*Concerns, stated.*
- The generated plans vary only two of seven rooms' window facings (and every room's size), so the core over plans
  over-estimates the core over real homes' window distributions; a finite set of plans, like a finite set of
  settings, over-estimates the intersection over its population.
- The witnesses outside the 199 (an automation that would become a frame flag in another plan) are not seen.
- The per-room thermostats follow one controller (A18.0's binding rule); a home whose room thermostats keep their own
  setpoints would not see the setback at all in the other rooms, which is not run.
- The U corner holds the heating at 100 W/m² of floor; the leakiest corner's baseline still holds every room above
  13.6 C, so no baseline breach is excluded by the causal rule.

## Revision R9-Z (Addendum 19): the setback headline at 2 and 5 W/m², two orientation cores, the frame relation's tolerance grid

**Post hoc: written after the sixth internal review, before its runs.** Registered as protocol Addendum 19 at
`1418968`, committed alone before any number below was computed. Script: `scripts/revision/r9z_analyses.py` (tests
`tests/edt/test_r9z_analyses.py`, written first; the oracle's new `h_sun_min` is bit-identical at its default).
Numbers: `results/edt/r9z-736ef83.json` (items 2, 3, 5(a)) and `results/edt/r9z-ce9a316.json` (all items, item 10 added). At most 3 workers, under nice. Review 6's item numbers are
in brackets.

### Item 2: the setback headline at a realistic internal-gains setting (A19.1)

R2-c's 22 setback automations, each alone, closed loop, in the core four (Minneapolis and Stockholm, 2026-01-14, plan
north 0 and 180): 88 runs, each against its own setting's baseline, R2-c's causal rule. The corner: internal gains 2
W/m², medium capacity (165 kJ/(m² K)), the envelope as built (no roof or ground loss; U 0.35 / 1.4 W/(m² K)), the
nominal 1-day warm-up (R4-B's `G2-Foff-W1-C165`); and the same at 5 W/m².

- *Checks.* R4-B's saved runs at both corners reproduce its published rows (breaching runs, caused cells, automations,
  repositories, coldest caused room, baseline). Run again with the current code under the hallway thermostat, both
  corners reproduce the saved runs exactly (the same 88 runs, the same breaching runs, 0 field mismatches in coldest
  caused room, caused cells, baseline minimum, coldest room and setback length).
- *Per-room thermostats.* R8-Y's per-room runs exist only at internal gains 0, so they did not exist at these corners;
  they were run now (A18.0's binding rule). 368 closed-loop runs in 2.5 min, 3 workers.

| Corner | Thermostats | Breaching runs (of 88) | Minneapolis / Stockholm | Caused cells (of 616) | Automations / repositories (per repository) | Coldest caused room per breaching run, min / median / max | Baseline coldest room |
|---|---|---|---|---|---|---|---|
| **2 W/m²** | **hallway (as built)** | **37** | 29 / 8 | 196 | **16 / 3** (14, 1, 1) | **4.1 / 6.3 / 9.9 C** | 15.6 C |
| 2 W/m² | per room | 31 | 23 / 8 | 185 | 12 / 3 (10, 1, 1) | 4.2 / 6.7 / 9.9 C | 19.7 C |
| 5 W/m² | hallway | 23 | 23 / 0 | 142 | 12 / 3 (10, 1, 1) | 6.1 / 7.6 / 9.1 C | 15.6 C |
| 5 W/m² | per room | 23 | 23 / 0 | 137 | 12 / 3 (10, 1, 1) | 6.4 / 8.0 / 9.1 C | 19.7 C |
| 0 W/m² (R2-c; R8-Y) | hallway / per room | 53 / 46 | 31 / 22; 24 / 22 | 315 / 279 | 16 / 3; 13 | 2.7 / 8.1 / 9.8; 2.7 / 7.2 / 9.9 C | 15.6 / 19.7 C |

- **The headline at a physically possible corner: 37 of the 88 core runs breach (16 automations from 3 repositories,
  14 of them from one), with the coldest caused room between 4.1 and 9.9 C (median 6.3 C).** With a thermostat in
  every room, 31 (12 automations). At 5 W/m², 23 of 88 under either layout, all in Minneapolis (12 automations).
- The per-room breaching runs are a subset of the hallway's at both corners (2 W/m²: 6 lost, all Minneapolis, from 4
  automations; 5 W/m²: none lost). The warm-up hardly matters: 37 / 38 / 38 / 38 runs at 1, 5, 10 days and periodic
  (2 W/m²), 23 at every warm-up (5 W/m²). Beyond the core, R4-B finds no other breaching run at either corner (J and
  all 48: 37 and 23).
- *Split by whether the rule turns the thermostat off* (A18.5's split, registered here; 4 of the 22 do):

| Corner, thermostats | Setpoint-only rules (18): runs, coldest caused room min / median / max | Thermostat-off rules (4): runs, coldest caused room |
|---|---|---|
| 2 W/m², hallway | 32, 5.2 / 6.3 / 9.9 C | 5, 4.1 / 4.8 / 9.7 C |
| 2 W/m², per room | 26, **6.7** / 6.7 / 9.9 C | 5, 4.2 / 5.1 / 9.8 C |
| 5 W/m², hallway | 20, 6.1 / 7.8 / 9.1 C | 3, 6.4 / 6.7 / 6.9 C |
| 5 W/m², per room | 20, 6.7 / 8.0 / 9.1 C | 3, 6.4 / 6.9 / 7.1 C |

  As at 0 W/m², a thermostat per room holds a setpoint-only rule's rooms at 6.7 C (a 7 C setpoint less the band), still
  below 10 C; the sub-6.7 C rooms come from the rules that switch the heating off.
- *The coldest room* is bedroom 1 in 34 of the 37 breaching runs at 2 W/m² under the hallway thermostat (bedroom 2 in
  3); per room, bedroom 1 in 25 and the bathroom in 6.
- **In context: not available.** R4-B's companion runs (A7.4) were made at the nominal corner only, so they say
  nothing about these corners; none was run (A19.1). At the nominal corner, 48 of the 53 breaching runs went away in
  context, and the 15 that remained came from rules that turn the thermostat off.
### Item 3: two orientation cores (A19.2)

All memberships are read from R8-X's and R8-Y's saved records; nothing was replayed. *Checks:* each bracket alone gives
its published core (recorded beam 95, glazing 79, plans 9/11/16 86), R8-Y's plan witnesses recompute from its saved
replays unchanged, and R8-X's own code gives the all-bracket cores again (129 / 7 / 54 / 18 over 139 settings; 128 / 7
/ 54 / 18 over 141).

**(a) The attributable, orientation-relevant core: 75 of the 100 orientation witnesses** hold at all six settings of
the three brackets whose attribution condition holds (the recorded beam/diffuse split; glazing x0.5 and x2; plans seed
9, 11 and 16). The possible set is 110 (10 outside the published 100).

- *Which bracket binds.* Without the recorded beam the core is still 75; without the glazing, 84; without the plans,
  78. Glazing x0.5 is the single binding setting (82 without it).
- *How many settings each witness survives:* 75 hold in all 6, 8 in 5, 12 in 4, 5 in 3, none in fewer.
- *Secondary.* With plan seed 8 and the mirrored home added (8 settings) the core is **72**. Over the same six
  settings the frame flags keep **187 of 199** (possible set 201).

**(b) The all-bracket joint core, restated: 7 of 100** over A17.1's 139 settings (7 also over 141 with the glazing
variants, and 7 over 144 with the three plans too). It includes the lumped-magnitude brackets (k̂ and the aperture
scale), the room-temperature-level brackets (the indoor offset) and the room-model corners, whose attribution
condition fails: they bound the size of a term, not which wall the beam reaches.

**(c) Orientation-isotropic settings.** By A19.2(c)'s rule, of the 139 settings **128 are orientation-isotropic**
(R7-B's 119 non-reference corners, its 4 indoor offsets, R7-A's outdoor air ±2 K, humidity classes 2 and 3 and the
engine correction), **8 are orientation-relevant** (R7-A's five aperture scales and two direct shares, R7-W's recorded
shortwave) and 3 are copies of the unperturbed model. 120 of the 139 are R7-B's corners (one of them its reference).
Split by class, the witnesses' joint core is **44** over the 8 orientation-relevant settings and **22** over the 128
isotropic ones: the isotropic settings remove more witnesses, but neither class alone comes near the 7, which needs
both.

**(d) What the 75 have in common** (against the 25 witnesses outside the core; a witness may read more than one
room):

| Feature | In the core (75) | Outside (25) |
|---|---|---|
| reads the living room (window facing 0, the plan's top; two large panes) | 67 | 13 |
| reads the bathroom (facing 180) | 1 | 6 |
| reads the corridor (no window) | 0 | 3 |
| reads bedroom 1 (270) / the office (90) / the kitchen (180) / the porch | 4 / 3 / 1 / 5 | 2 / 1 / 1 / 1 |
| the rooms it reads face one way only | 72 | 21 |
| reads a temperature / a humidity / neither (A12's read kind) | 51 / 22 / 2 | 13 / 8 / 4 |
| frame kinds: fires_somewhere_only / night_light / flaps / time_spread | 72 / 7 / 4 / 1 | 23 / 0 / 3 / 0 |
| judged frames: sensed only / mixed / clock only | 55 / 19 / 1 | 17 / 8 / 0 |

- The shared feature is the room: **89 % of the core (67 of 75) read the living room**, against 52 % (13 of 25)
  outside it. Readers of the bathroom (7 witnesses, 6 of them outside the core) and of the windowless corridor (3, all
  outside) are the ones the attributable brackets remove.
- The divergence is almost always whether the rule fires at all (fires_somewhere_only, 72 of 75), and the rule is
  almost always sensed (74 of 75 judged in a sensed frame, alone or mixed): a threshold on a room's temperature or
  humidity that one bearing crosses and the other does not.
- *What this cannot say.* Every generated plan keeps the living room's panes on the plan's top side (A18.3), glazing
  scales every pane alike and the beam split is global: **no attributable bracket moves the living room's facing.** So
  the 67 are robust to every attributable term that leaves their room facing the same way, which is weaker than
  robust to where the room's window points. The binder also puts a room-less author sensor in the living room (A2.5's
  convention), so "reads the living room" partly means "names no room". Both are stated limits, not tested.

### Item 5(a): the frame relation's tolerance sensitivity (A19.3)

The oracle now takes a sun tolerance apart from the clock one (`h_sun_min`; at its default the relation is unchanged,
tested). Re-judged on `8fcd672`'s saved outcomes and first-firing instants, no re-simulation. *Check:* (3 h, 180 min)
reproduces the 199 and every published frame kind set (0 mismatches).

| h_clk \ h_sun | 60 min | 180 min | 360 min |
|---|---|---|---|
| 1 h | 213 (+14, -0; time_spread 23) | 199 (0, 0; 8) | 198 (0, -1; 7) |
| **3 h** | 207 (+8, -0; 16) | **199** (8) | 198 (0, -1; 7) |
| 6 h | 202 (+4, -1; 11) | 198 (0, -1; 7) | 198 (0, -1; 7) |

Frame flags of the 5,926 run automations (gained, lost against the published 199; time_spread flags). In every cell
fires_somewhere_only is 185, night_light 30 and flaps 8: the tolerances move only time_spread.

- **Over the nine cells the frame relation flags 198 to 213** (199 published). Across the clock tolerance alone at the
  published sun tolerance it is **198-199**: from 1 h to 6 h the frame count moves by one flag, where the clock
  relation moves by 268 (799 at 1 h, 790 at 3 h, 531 at 6 h, the same saved outcomes; the 806 and 541 the paper
  quotes are `120f4ca`'s, not `8fcd672`'s). The one flag lost at 6 h, or at 360 min, is a mixed-frame time_spread
  flag.
- The sun tolerance matters more than the clock one: 60 min adds 8 time_spread flags at 3 h (14 at 1 h), almost all
  of them sun-only or mixed-frame automations whose sun-relative firings range over 1-3 h. At 180 min and above the
  count is flat.
- *Where 3 h comes from* (as found): the EDT plan's Global Constraints (`docs/superpowers/plans/2026-11-04-...`, "fixed
  here before any sweep") and protocol §4 set "more than 3 h" for the clock relation, and A6.3 carried it to the sun
  frame as 180 min. No document gives a reason for the value or says what it represents; the design spec states the
  divergence ("fires outside its author's evident window") without a number.

### Item 10: the cached-data checks (A19.4)

Run after the first results commit (`ce9a316`); see A19.4's dated note. All three read saved outcomes or cached traces
only.

**(a) The humidity clamp against the 36 humidity frame flags.** All 36 read an indoor room's humidity role, and none
reads the porch's. Over the 1,728 (flag, environment) cells:

- **7 of the 36 have a first firing at a clamped reading** (22 cells: Singapore 13, Phoenix 9). Which bound, the 85 %
  ceiling or the 15 % floor, is not split here.
- **None** has an A8.5 crossing instant at a clamped value.
- **None** has a numeric bound at or beyond the clamp.
- Every one of the 36 reads a series that reaches the clamp somewhere in days 1-2 (403 cells, Phoenix and Singapore
  only). That is the upper bound, and it says only that the clamp is reached in those two cities.

So the part of the class that can rest on the clamp is at most **7 of 36**. In those 7 the rule fires while its sensor
sits at a clamp; no humidity threshold is crossed because of one.

**(b) Multiply caused dead rules.** A6.6's cascade, in the order it is applied:

1. the actions end before any call;
2. a condition never holds when a trigger fires (blocking condition named by drop-one probes);
3. not derived;
4. trigger entities never change;
5. threshold out of the observed range in every environment;
6. never armed;
7. threshold unreachable or never armed, by environment;
8. armed numeric_state never crosses in the judged window.

The three predicates were evaluated independently (1,170 probes over the 48 cached traces, 9.9 min). *Check:* T, the
triggers never fire, holds for all 303 trigger-stage reasons and for none of the 87 action or condition reasons.

| Predicates that hold | T only | C only | A only | T+C | T+A | C+A | none |
|---|---|---|---|---|---|---|---|
| Automations (of 390) | 257 | 75 | 4 | 32 | 14 | 3 | 5 |

- **49 of the 390 (12.6 %) are multiply caused** (two predicates hold), and 39 of them are A6.6's "trigger entities
  never change". Table VII's distribution therefore depends on its order for about one dead rule in eight.
- Of the 240 "never change" automations, 25 also have a condition that never holds and 14 have actions that make no
  call.
- *Not evaluable:* C for 2 automations (a condition names the trigger), A for 34 (an action names the trigger).
- *Limit:* the 5-minute pattern can miss a condition that holds for less than 5 minutes.
- The 5 with no predicate are action- or condition-stage reasons that neither pattern probe confirms: a
  5-minute probe finds their conditions holding, or their actions calling, at some instant that is not a trigger's.

**(c) The engine-discrepancy propagation, blocked by environment.** The recomputation reproduces R6-C's registered and
by-automation summaries exactly (1,052 pairs, 8 artifact pairs).

| Propagation (1,000 replicates) | Frame flags changed: mean, 95 % range (of 199) | time_spread (of 8) | Against A10.7's limit (p97.5 at most 9) |
|---|---|---|---|
| registered (per environment, independent) | 6.5, 3-10 | 2.7, 0-5 | fails by 1 |
| **environment-blocked** (A19.4(c)) | **6.5, 3-10** | 2.7, 0-5 | **fails by 1** |
| by automation (R6-C, post hoc) | 2.2, 0-5 | 0.08, 0-1 | passes |

Blocking by environment changes nothing: the mean is 6.49 against 6.52, the 97.5th percentile is 10 in both and 23
flags ever change against 24. So the independence assumption does not bias the propagated count in either direction,
and the criterion fails by 1, not by more. The registered propagation's excess comes from scattering automation-level
discrepancies (the two automations Home Assistant never runs) over a flag's environments. Drawing by automation removes
it; drawing by environment does not. With only 4 compared environments, a site- and date-systematic defect has few
blocks to show itself in; that limit is stated.

## Revision R9-R (Addendum 20): the re-triggering stratum

**Post hoc** (after the sixth internal review, item 5(b)). Registered as protocol Addendum 20 at `03a9c2e`, before
any run. Harness (A20.3, instrumentation only) at `b126515`; the comparison before the fix ran there. Engine fix at
`6a9023b` (failing tests first, `tests/lab/test_automations_modes.py`); the comparison after the fix ran there.
Aggregates: `results/edt/r9r-6a9023b.json` (`scripts/revision/r9r_results.py`). Harness: Addendum 10's
(`ha_diff.input_stream`, `ha_job`, `run_ha`), Home Assistant 2026.10.2 in-process on the virtual clock, no port.
Code: `vesper/validation/ha_retrigger.py`.

**The stratum.** 8 synthetic automations (A20.1), each triggered by one motion sensor turning on: 1 `single` (the
control), 3 `restart` (a 300 s delay; two 100 s delays between calls; a wait with a 200 s timeout), 2 `queued` (max 3,
300 s; default max, 95 s), 2 `parallel` (max 2 with `max_exceeded: silent`, 300 s; default max, 300 s). The trace
(A20.2) is the Minneapolis 2026-01-14 header with 19 pulses a day: a spaced re-trigger (08:00, 08:02, 08:04), a
burst (15 pulses 70 s apart from 12:00) and an isolated trigger (18:00). Over the judged days, 114 state changes of
the one sensor, the only entity that changes. Both engines loaded all 8; Home Assistant had none unavailable.

### Before the fix: 1 of 8 agree

With modes run as `single` (the documented permissive approximation), VESPER agrees with Home Assistant only on the
`single` control. Over the two judged days:

| # | mode | firings V / HA | run starts V / HA | cancellations V / HA | flaps V / HA | differs in |
|---|---|---|---|---|---|---|
| 1 | single | 20 / 20 | 10 / 10 | 0 / 0 | no / no | nothing |
| 2 | restart | 20 / 44 | 10 / 38 | 0 / 32 | no / **yes** | counts, calls, sequence, runs, outcome |
| 3 | restart (two delays) | 64 / 60 | 16 / 38 | 0 / 32 | yes / yes | counts, calls, sequence, runs |
| 4 | restart (wait) | 32 / 44 | 16 / 38 | 0 / 32 | yes / yes | counts, calls, sequence, runs |
| 5 | queued, max 3 | 20 / 40 | 10 / 20 | 0 / 0 | no / **yes** | counts, calls, sequence, starts, outcome |
| 6 | queued | 48 / 76 | 24 / 38 | 0 / 0 | yes / yes | firings, calls, sequence, starts |
| 7 | parallel, max 2 | 20 / 36 | 10 / 18 | 0 / 0 | no / **yes** | counts, calls, sequence, starts, outcome |
| 8 | parallel | 20 / 76 | 10 / 38 | 0 / 0 | no / **yes** | counts, calls, sequence, starts, outcome |

The `single` control agrees in every measure, including the 28 triggers each engine drops ("Already running"). The
flaps component, the oracle's only re-triggering-sensitive envelope kind, differs in 4 of the 7 others: VESPER's
single-mode reading under-counts firings. Home Assistant's counts match the semantics of `helpers/script.py` worked
by hand (for example automation 5: per day 10 accepted runs and 9 triggers dropped at max).

### The fix (A20.5)

`restart` stops the run under way (a pending delay or wait never resumes it) and starts a new one; `queued` holds a
run until the runs before it end; `parallel` runs beside them; both drop a trigger once `max` runs (running and
waiting; default 10) exist. Conditions are still checked first. An invalid `mode`, a `max` below 2 or not a number,
or an unknown `max_exceeded` is refused, as Home Assistant's schema refuses it. The permissive replay no longer lists
"mode X run as single" as an approximation, and strict mode no longer refuses the three modes (two older tests that
asserted the approximation and the refusal were changed to assert the new behaviour).

### After the fix: 8 of 8 agree

Every measure of A20.4 is equal for all 8: firings, distinct instants, the (service, target) counts, the sequences
(every firing at the same instant; the trigger is a `state` trigger, so no sub-second offset), run starts (10, 38,
38, 38, 20, 38, 18, 38), cancellations (32 for each `restart`, 0 elsewhere), and all five outcome components.
Dropped triggers agree where Home Assistant logs them: 28 for `single`, 18 for queued max 3. For parallel max 2,
VESPER drops 20; Home Assistant logs none because the automation is `silent`, and its 18 accepted runs agree.

### Reach (A20.6)

*Static.* Run automations with a mode other than `single`: in `8fcd672`, 330 of 5,926 (260 `restart`, 31 `queued`,
39 `parallel`); 41 fire somewhere, 9 carry a clock flag and **2 a frame flag (both `restart`)** of the 199. In
`db19e34`, 329 of 5,843 (259, 31, 39); 41 fire, 9 clock flags, the same **2 of the 196** frame flags. The one
automation in `8fcd672` and not in `db19e34` is a `restart` refused since Addendum 16 as disabled at startup.

*Dynamic.* The 329 automations were replayed on each of `db19e34`'s 48 cached traces (15,792 pairs), before and after
the fix.
- **Gate.** Before the fix, 15,791 of 15,792 pairs reproduce the sweep's outcome and richer outcome. One pair does
  not: a `queued` automation in Stockholm 2026-07-15 north 0, with 1,560 distinct firing instants against the
  sweep's 1,559 and the outcome otherwise equal. It is excluded, as registered. The same pair differs identically
  with the fixed engine, so the fix is not its cause. Most likely it is a sweep-time effect such as the template
  budget (A16.3). It was not investigated further.
- **The fix changes nothing.** **0 of 15,792 pairs** change (no outcome component, first firing, count, payload or
  branch). **0 of the 329 automations** change, and so do 0 flags: the 9 clock flags and the 2 frame flags are held
  with the same kinds, and there are none lost and none new. The reason is in the run counts. Over the 15,792 pairs
  the old engine dropped **0** triggers and the fixed engine cancelled **0** runs; both accepted 654,685 runs. In
  the sweep's inputs, no automation with a non-single mode is ever triggered while it is running, so the mode never
  acts.

### The 2 `restart` frame flags on their own environments (A20.7)

Both flags were run through the harness with Home Assistant on all 48 of their environments (`db19e34`'s traces),
and R7-C's four within them, before and after the fix:

| Flag | sweep frame kinds | envs | VESPER reproduces sweep | both fire | one engine only | discrepant (A10.4(4)) | run starts V / HA | cancellations V / HA | max \|Δ\| |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `fires_somewhere_only` | 48 | 48 | 17 | 0 | 0 | 22 / 22 | 0 / 0 | 0.0 s |
| 2 | `fires_somewhere_only`, `night_light` | 48 | 48 | 36 | 0 | 0 | 67 / 67 | 0 / 0 | 0.0 s |

The numbers are the same before and after the fix. With Home Assistant's outcomes in place of VESPER's in all 48
environments, **both flags survive with their sweep kinds**: frame `fires_somewhere_only` (plus `night_light` for
flag 2), and clock with `time_spread` added. In R7-C's four environments alone: 1 and 2 jointly firing pairs, 0
discrepant. Home Assistant logged no "Restarting" for either in any environment: no trigger arrives during their
runs. So the 2 flags owe nothing to the `restart` approximation.

*Reading.* Until this revision, the engine's `restart`, `queued` and `parallel` were untested approximations, and
they were wrong under re-triggering: 7 of 7 non-single automations disagreed with Home Assistant, 4 of them on the
oracle's flaps component. They are now Home Assistant's semantics, verified 8 of 8 on a trace built to re-trigger
them. The fix moves none of the sweep's 15,792 non-single-mode pairs and none of its flags, because the sweep never
re-triggers such an automation mid-run. So the published 199 and the engine-corrected 196 stand as they are.

*Concerns, stated.* The stratum is 8 automations, one trigger kind (`state`) and one environment. It tests the mode
machinery under known re-triggering, not the modes' interaction with every action kind (the two delays, one wait and
plain calls are covered; `repeat`, `choose` and a self-triggering loop inside a restarted run are not). The dynamic
reach is over the sweep's own inputs, which are open loop with no residents: an automation that re-triggers only on
occupancy or on its own actions' effects is outside it, as everywhere in this instrument. The one gate-differing pair
is unexplained. `tests/lab/test_ui.py`'s JavaScript check fails in this worktree because node skips one page test;
it is untouched by this lane and was deselected.

## Revision R10-Q (Addendum 21): the standard home's geometry, the rung-0 trade-off curve, the interior beam-light term

**Post hoc** (after the seventh internal review, items 4, 5 and 6(a)), registered before its runs as protocol
Addendum 21 (`docs/experiments/edt-protocol.md`, commit `0084a15`). New knob: `BEAM_LIGHT_SCALE`
(`vesper.env.indoor`, commit `c496a0c`). It multiplies the interior beam (direct-sun) term of a room's daylight and
nothing else; the diffuse term, the solar heat and `DAYLIGHT_SCALE` are unchanged. It is bit-identical at 1 by the
extended fingerprint test. Script: `scripts/revision/r10q.py`; numbers: `results/edt/r10q-b5f9a19.json` (aggregates
only). No sweep was run again.

### Item 5: the standard home's geometry (A21.1)

Read from the simulator's input: `8fcd672`'s EDT standard home (`work-8fcd672/edt/`, whose `map.json` and
`windows.json` are byte-identical to `base/`'s), through `vesper.env.indoor.room_models` at the defaults. The home is
a generated 6-room plan (seed 7) with a hallway: 7 indoor spaces and an outdoor porch, which has no model. Ceiling
height 2.5 m everywhere. Floor areas are the rooms' polygons, inside the walls. Bearings are given for the sweep's two
plan orientations (north 0 / north 180).

| Space | Floor (m²) | Volume (m³) | Opaque outside wall (m²) | Glass (m²), facing at north 0 / 180 | H walls / glass / ventilation (W/K) | H (W/K) | C (MJ/K) | τ = C/H (h) |
|---|---|---|---|---|---|---|---|---|
| Living room | 22.80 | 57.0 | 20.02 | 2 x 2.24 = 4.48, N / S | 7.01 / 6.27 / 9.69 | 22.97 | 3.762 | 45.5 |
| Bedroom (larger) | 14.44 | 36.1 | 17.56 | 1.44, N / S | 6.15 / 2.02 / 6.14 | 14.30 | 2.383 | 46.3 |
| Office | 12.92 | 32.3 | 16.56 | 1.44, E / W | 5.80 / 2.02 / 5.49 | 13.30 | 2.132 | 44.5 |
| Hallway | 10.00 | 25.0 | 5.00 | none | 1.75 / 0 / 4.25 | 6.00 | 1.650 | 76.4 |
| Bedroom (smaller) | 9.12 | 22.8 | 14.06 | 1.44, W / E | 4.92 / 2.02 / 3.88 | 10.81 | 1.505 | 38.7 |
| Kitchen | 6.84 | 17.1 | 3.50 | 1.00, S / N | 1.22 / 1.40 / 2.91 | 5.53 | 1.129 | 56.7 |
| Bathroom | 6.84 | 17.1 | 4.14 | 0.36, S / N | 1.45 / 0.50 / 2.91 | 4.86 | 1.129 | 64.5 |
| **House** | **82.96** | **207.4** | **80.84** | **10.16** | **28.29 / 14.22 / 35.26** | **77.78** | **13.688** | **48.9** |

- **The check holds:** H = 77.78 W/K = 28.29 walls (36.4 %) + 14.22 glass (18.3 %) + 35.26 ventilation (45.3 %),
  R6-B's 77.8 = 28.3 + 14.2 + 35.3 to its printed precision; C = 13.69 MJ/K and C/H = 48.9 h, R2-a's.
- **The ventilation arithmetic.** 0.5 ACH x 207.4 m³ x ρc_p / 3600. The model's constant is 0.34 Wh/(m³ K), that is
  ρc_p = 1,224 J/(m³ K), which gives **35.26 W/K**. With the textbook 1.2 kg/m³ x 1,005 J/(kg K) = 1,206 J/(m³ K) the
  same volume gives 34.74 W/K. Read backwards, 35.3 W/K is 207.6 m³ at the model's constant (210.7 m³ at the textbook
  one), not the review's ≈214 m³; that estimate used ρc_p ≈ 1,190 J/(m³ K).
- **The capacity.** C = 165 kJ/(m² K) x 82.96 m² = 13.69 MJ/K. C/H = 13.69e6 / 77.78 / 3600 = 48.9 h; read backwards,
  48.9 h and 165 kJ/(m² K) give 83.0 m², the floor area above.
- The interior partitions (U 2.0 W/(m² K)) move heat between rooms and are not in H (R6-B).
- The glass is 10.16 m², 12.2 % of the floor area; 5 of the 7 windows are on the plan's north-south axis (the living
  room's two and the larger bedroom's at plan north; the kitchen's and the bathroom's at plan south), 2 on the east-west
  axis (the office east, the smaller bedroom west). Turning the plan by 180 degrees swaps each window's facing.

### Item 6(a): the rung-0 trade-off curve (A21.2)

The scores are A6.7's and A8.1's, recomputed with their own code, folds, seed and rows. **The checks hold:** the pooled
out-of-fold AUCs reproduce A8.2's (frame 0.7824 with 8 features, 0.9194 with 8+4; clock 0.9459, 0.9809) and the
held-out AUCs reproduce A8.1's (frame 0.8485, 0.8411; clock 0.9338, 0.9531). Population: the 5,926 automations that
run at rung 4 (199 frame flags, base rate 3.4 %; 790 clock flags). An automation passes when its score is at least the
threshold; the eight features are few and discrete, so many automations share a score and pass together.

**Frame flags, pooled out-of-fold scores (all 5,926):**

| Features | P at R 0.8 (passed) | P at R 0.9 (passed) | P at R 0.95 (passed) | R at P 0.3 (passed) | R at P 0.5 (passed) | R at P 0.7 (passed) |
|---|---|---|---|---|---|---|
| 8 (A8.1's model) | 0.063 (2,539) | **0.034 (5,258)** | 0.034 (5,555) | 0.643 (406) | 0.633 (250) | 0.030 (7) |
| 8+4 | 0.350 (457) | **0.071 (2,523)** | 0.063 (3,038) | 0.814 (518) | 0.618 (216) | 0.030 (6) |

**Frame flags, A8.1's registered split (held-out 2,777, 86 flags):**

| Features | P at R 0.8 (passed) | P at R 0.9 (passed) | P at R 0.95 (passed) | R at P 0.3 (passed) | R at P 0.5 (passed) | R at P 0.7 |
|---|---|---|---|---|---|---|
| 8 | 0.057 (1,301) | 0.058 (1,383) | 0.036 (2,376) | 0.628 (116) | 0.558 (93) | 0 |
| 8+4 | 0.141 (488) | 0.032 (2,612) | 0.032 (2,612) | 0.779 (216) | 0.558 (93) | 0 |

**Clock flags, for comparison:**

| Scores, features | P at R 0.8 (passed) | P at R 0.9 (passed) | P at R 0.95 (passed) | R at P 0.3 | R at P 0.5 | R at P 0.7 |
|---|---|---|---|---|---|---|
| out-of-fold, 8 | 0.850 (745) | 0.794 (896) | 0.208 (3,639) | 0.943 | 0.927 | 0.925 |
| out-of-fold, 8+4 | 0.835 (758) | 0.812 (877) | 0.764 (984) | 0.987 | 0.984 | 0.980 |
| split, 8 | 0.823 (356) | 0.642 (514) | 0.194 (1,786) | 0.907 | 0.907 | 0.885 |
| split, 8+4 | 0.810 (363) | 0.755 (440) | 0.611 (570) | 0.964 | 0.964 | 0.945 |

**The implied budget** (frame flags, recall 0.9; triage at random would pass 0.9 x 5,926 = 5,333 in expectation):

- **8 features: 5,258 of 5,926 (88.7 %; repository bootstrap 80.7-93.7 %)**, against random's 5,333. Rung 0 as
  registered does not shrink the simulation budget for the frame relation's flags at 90 % recall: its precision there
  (0.034) is the base rate.
- 8+4 features: 2,523 of 5,926 (42.6 %; 15.7-52.0 %). On the split, scaled to 5,926: 2,951 (8) and 5,574 (8+4); the
  split and the pooled scores disagree by a factor of two in opposite directions, so neither budget is stable.
- At recall 0.8 the 8+4 model passes 457 (7.7 %, precision 0.35); the curve is steep between 0.8 and 0.9, where
  the remaining flags share their scores with thousands of unflagged automations.
- For the clock relation's flags the budget is small: 896 of 5,926 (15.1 %; 13.2-27.8 %) with 8 features, 877
  (14.8 %; 13.3-16.6 %) with 8+4.

So a syntactic triage is cost-effective for the clock relation's flags and is not, at 90 % recall, for the frame
relation's: what the frame relation finds is mostly not visible in the automation's syntax. The pooled scores come
from five models, so a threshold over them is a pooled operating point, not one deployable model's (A21.2's limit).

### Item 4(a): the interior beam-light term bracketed by k̂ (A21.3)

The 67 run automations that read an illuminance (R7-B's A13.4 population), as bound, in the standard home, open loop
in all 48 environments, every trace recorded afresh; about 30 s per setting with 4 workers, no environment failed.
**The reference check holds:** recorded now, it reproduces `5b54586` on the 67 exactly (0 of 3,216 outcomes differ;
0 membership mismatches), so the published memberships are 7 frame flags, 15 clock flags and 17 automations firing
somewhere.

| BEAM_LIGHT_SCALE | Frame flags (of 7): lost / gained | Clock flags (of 15): lost / gained | Fire somewhere (of 67) | Outcomes changed (of 3,216), automations |
|---|---|---|---|---|
| 1 (reference) | 7: 0 / 0 | 15: 0 / 0 | 17 | 0 |
| 0.289 | 7: 0 / 0 | 15: 0 / 0 | 17 | 19, in 2 |
| **0.250** (k̂) | 7: 0 / 0 | 15: 0 / 0 | 17 | 19, in 2 |
| 0.205 | 7: 0 / 0 | 15: 0 / 0 | 17 | 18, in 2 |

**Robust core / possible set over the four settings: frame 7 / 7, clock 15 / 15**; no flag changes its kinds; the
same 17 fire somewhere at every setting. Scaling the interior beam light by k̂'s interval moves the first firing of 2
automations in 18-19 environment outcomes and changes no flag.

### Item 4(b): the change-reporting sensor at a corrected daylight level (A21.4)

R7-B's home (a `zigbee:tuya:s-lux-zb` beside each of the 8 RTCGQ11LM sensors; 79 entity references replaced in 65 of
the 67) and its swap. **Both anchors hold:** the augmented home as bound reproduces `5b54586` exactly (0 of 3,216), and
the swap at the defaults reproduces R7-B's swap (7 frame and 15 clock flags, 0 lost, 0 gained, 1 clock flag's kinds
changed, 17 firing somewhere; 526 outcomes in 16 automations move against the reference). Daylight is scaled by
`DAYLIGHT_SCALE`, the knob rung 3 uses at 1/14.8; check D's residual after A9.6's three factors is model / measured =
2.49 (0.93-3.49). Outcomes changed are counted against the swap anchor.

| Setting (swapped home) | Daylight x | Beam x | Frame (of 7): flagged, lost / gained | Clock (of 15): flagged, lost / gained | Fire somewhere (of 67) | Outcomes changed (automations) |
|---|---|---|---|---|---|---|
| swap anchor | 1 | 1 | 7, 0 / 0 | 15, 0 / 0 | 17 | 0 |
| **primary: 1/2.49** | 0.402 | 1 | 7, 0 / 0 | 15, 0 / 0 | 17 | 169 (8) |
| **primary: 1/2.49** | 0.402 | 0.25 | **6, 1 / 0** | 14, 1 / 0 | 16 | 187 (8) |
| primary: 1/0.93 | 1.075 | 1 | 7, 0 / 0 | 15, 0 / 0 | 17 | 94 (7) |
| primary: 1/0.93 | 1.075 | 0.25 | 7, 0 / 0 | 15, 0 / 0 | 17 | 97 (7) |
| primary: 1/3.49 | 0.287 | 1 | 7, 0 / 0 (1 changes kinds) | 15, 0 / 0 | 17 | 205 (8) |
| primary: 1/3.49 | 0.287 | 0.25 | **6, 1 / 0** | 14, 1 / 0 | 16 | 232 (8) |
| secondary: x2.49 | 2.49 | 1 | 7, **1 / 1** | 15, 1 / 1 | 17 | 114 (9) |
| secondary: x2.49 | 2.49 | 0.25 | 6, 1 / 0 | 14, 1 / 0 | 16 | 107 (8) |
| secondary: x0.93 | 0.93 | 1 | 7, 0 / 0 | 15, 0 / 0 | 17 | 104 (7) |
| secondary: x0.93 | 0.93 | 0.25 | 7, 0 / 0 | 15, 0 / 0 | 17 | 108 (7) |
| secondary: x3.49 | 3.49 | 1 | 7, 1 / 1 | 15, 1 / 1 | 17 | 124 (9) |
| secondary: x3.49 | 3.49 | 0.25 | 7, 1 / 1 | 15, 1 / 1 | 17 | 114 (9) |

| Bracket | Frame: robust core / possible set (of 7) | Clock: robust core / possible set (of 15) | Fire somewhere: at every setting / at some |
|---|---|---|---|
| primary six + swap anchor (7 settings) | **6 / 7** (1 lost, 0 gained) | 14 / 15 | 16 / 17 |
| primary at beam 1 + swap anchor | 7 / 7 | 15 / 15 | 17 / 17 |
| primary at beam 0.25 | 6 / 7 | 14 / 15 | 16 / 17 |
| all 13 swapped settings | 5 / 8 (2 lost, 1 gained) | 13 / 16 | 15 / 18 |

What moves, by anonymous index among the 67 (kinds are the oracle's names):
- **One flag (A30) is lost when the daylight is corrected downward and the beam cut to k̂** (1/2.49 and 1/3.49 at beam
  0.25). Its published frame kinds are *fires\_somewhere\_only* and *night\_light*; at those settings it fires in no
  environment, so it is no longer flagged under either relation. At 1/3.49 with the beam as built it keeps
  *fires\_somewhere\_only* (firing in 2 environments) and loses *night\_light*.
- Under the literal multiplication (the secondary set, the rooms 2.5-3.5 times brighter), one flag (A26, kind
  *fires\_somewhere\_only*) stops firing anywhere and one unflagged automation (A41) starts firing in 6-16
  environments and becomes a *fires\_somewhere\_only* flag.
- So each change is a *fires\_somewhere\_only* flag appearing or disappearing: an illuminance threshold that the room's
  daylight crosses in some environments at one daylight level and in none at another. No change is a change of timing
  or of which environments fire among those that do.
- 50 of the 67 fire nowhere at the reference; across all 13 swapped settings 15 fire somewhere at every setting and 18
  at some. The resident-free home leaves the illuminance class mostly untested whatever the daylight level.

### What this changes, stated

- The standard home's geometry is printed (82.96 m² over 7 spaces, 2.5 m, 207.4 m³, 10.16 m² of glass); it reproduces
  R6-B's H = 77.8 W/K and its 45 % ventilation share, and C/H = 48.9 h, from the simulator's own input. The review's
  derived 214 m³ is 207.4 m³ at the model's ρc_p (1,224 J/(m³ K)).
- Rung 0's trade-off curve: to catch 90 % of the frame relation's flags the registered eight-feature triage passes
  5,258 of 5,926 (88.7 %), about what random triage would (5,333); with the four frame features, 2,523 (42.6 %). For the
  clock relation's flags it passes 896 (15.1 %). The single operating point the paper reports does not show this.
- **The interior beam term is inert over k̂'s interval**: robust core 7 of 7 frame and 15 of 15 clock illuminance flags.
- **At the corrected daylight level the inert-path claim holds for 6 of the 7 frame flags, not all 7**, by A21.5's
  reading: with daylight corrected by check D's residual (1/2.49 or 1/3.49) and the beam cut to k̂, one
  *fires\_somewhere\_only* flag fires nowhere and is lost; none is gained. With the beam as built, the correction loses
  none. Under the literal reading (daylight x 2.49-3.49) one flag is lost and one gained. The manuscript's statement
  that the illuminance path's flags do not depend on the daylight level should be restated with these counts: they do
  not depend on the sensor's reporting or on the beam term alone, but 1-2 of them are existence-of-firing flags that
  appear or disappear with the daylight level.

*Concerns, stated.*
- Open loop over the 67 readers only; daylight reaches nothing else in the resident-free home.
- Each daylight correction is one factor for every room and hour; check D's residual is a median over 240 hours of one
  dataset, and the comparison behind it is not like-for-like (A9.6).
- The beam bracket scales a magnitude, not the beam's direction or timing; the change-reporting sensor's rule is the
  catalog default (R7-B).
- The trade-off curve's pooled scores come from five fold models; the split's held-out half is one model on 2,777 rows,
  and the two give budgets that differ by a factor of two in opposite directions for the frame flags.

## Revision R11-K (Addendum 24): a blind second rater

**Post hoc** (Addendum 24, `90af7ab`, written before any comparison). The second rater did not build the binder and
never saw the first rater's labels. It labelled seeded samples (seed 20261105) by the first audits' definitions:
- 50 of the 179 binder cases;
- 30 of the 100 dead-rule cases;
- 25 of the 44 sun-reader locations.

Script `scripts/revision/r11k_agreement.py`; aggregates in `results/edt/r11k-90af7ab.json`. The labels stay local.

| Audit | n | raw agreement | Cohen's κ | binary (uncertain dropped) |
|---|---|---|---|---|
| binder (4-way label) | 50 | 46 | 0.88 | misbind vs not: 45 of 45, κ 1.00 |
| dead-rule reason (class) | 30 | 29 | 0.95 | genuine vs not: 29 of 30, κ 0.84 |
| sun-reader location | 25 | 23 | 0.84 | located vs mislocated: 23 of 23, κ 1.00 |

- **Binder.** On the same 50 cases the misbind share is 12 of 49 decided (Wilson [0.15, 0.38]) for the first rater and
  11 of 45 ([0.14, 0.39]) for the second. Where both call a case a misbind, they agree on its class in 10 of 11.
- **Sun locations.** On the same 25, location precision is 16 of 25 ([0.45, 0.80]) for the first rater and 14 of 23
  ([0.41, 0.78]) for the second.
- **Where they differ.** Every disagreement involves an "uncertain" label on one side, except one dead-rule case
  (genuine, G1, for one rater; a binding or standard-home artefact, B, for the other).
- **Reading.** The audits that set the binder discount and the location precision reproduce under a second, blind
  rater.
