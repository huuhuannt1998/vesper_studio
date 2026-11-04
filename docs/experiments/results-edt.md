# Environmental differential testing (EDT): results (2026-11-04)

Every number below is in `results/edt/summary-dc119cd.json`, written by `scripts/edt_summary.py` from the local
results (the full sweep, cover, ladder and closed-loop JSONs hold corpus content and are not committed). The
protocol, fixed before the first full run, is `docs/experiments/edt-protocol.md` (sections 1-7, Addenda 1-3); the
design and its research questions are `docs/superpowers/specs/2026-11-04-environmental-differential-testing-design.md`.
"Pre-registered" means fixed in the protocol before the run; anything else is labelled post hoc.

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
