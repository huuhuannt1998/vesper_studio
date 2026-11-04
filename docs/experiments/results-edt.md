# Environmental differential testing (EDT): results (2026-11-04)

Every number below is in `results/edt/summary-dc119cd.json`, written by `scripts/edt_summary.py` from the local
results (the full sweep, cover, ladder and closed-loop JSONs hold corpus content and are not committed). The
protocol, fixed before the first full run, is `docs/experiments/edt-protocol.md` (sections 1-7, Addenda 1-3); the
design and its research questions are `docs/superpowers/specs/2026-11-04-environmental-differential-testing-design.md`.
"Pre-registered" means fixed in the protocol before the run; anything else is labelled post hoc.

## What was run

- **Code**: commit `dc119cd` (branch `plan-w4b`), `vesper edt run` / `cover` / `ladder` and the closed-loop
  confirmation (`vesper.edt.confirm`), 6 worker processes, one laptop.
- **Corpus**: 45,208 automations from 1,186 public Home Assistant repositories; de-duplicated by canonical hash
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
