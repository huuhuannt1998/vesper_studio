# Environmental differential testing (EDT): the sweep protocol

Fixed on 2026-11-04, before the first sweep over the real corpus. Everything below (environments, oracle thresholds,
the negative control and its halt threshold, the cover criteria, the held-out split) is decided here and is not
tuned on the sweep's results. A change after the first real run is an addendum with its date and reason, and the
earlier numbers are reported next to the new ones.

Code: `vesper/edt/` (corpus, home, bind, trace, replay, oracle, sweep, cover). Commands:

    vesper edt run   --corpus DIR --work DIR --out FILE [--workers N] [--rung R] [--limit N]
    vesper edt cover --sweep FILE --out FILE

Note (2026-11-04, after the runs; commands only, nothing above changes): the full command list.

    vesper edt census  --corpus DIR --out FILE
    vesper edt run     --corpus DIR --work DIR --out FILE [--workers N] [--rung R] [--limit N]
    vesper edt cover   --sweep FILE --out FILE
    vesper edt ladder  --sweep FILE [--sweep FILE ...] --out FILE
    vesper edt confirm --sweep FILE --work DIR --out FILE [--sample 200] [--seed 0] [--workers 2] [--cache DIR]

`vesper edt confirm` is the closed-loop confirmation (`vesper.edt.confirm.confirm`); at dc119cd it was called from a
script with the same arguments.

The corpus is a directory of public Home Assistant configurations given on the command line only; it is read, never
written, and neither its location nor its origin is recorded in any committed file or result.

## 1. Units and counts

- **Automation**: one automation found in the corpus (`vesper.edt.corpus.iter_automations`). Every count is over
  automations de-duplicated by canonical hash (sha256 of the canonical JSON without `id` and `alias`): identical
  copies in several repositories count once, attributed to the first repository in sorted file order.
- **Yield** (reported for every sweep): parsed (de-duplicated automations read), bound (the binder rewrote every
  entity onto the standard home), run (bound and not refused by the engine in at least one environment).
- **Unbindable** automations stay in the result with `bound: null` and the binder's reason; they are counted and
  reported by reason, never dropped.
- **Refused**: an environment in which the engine would not run a bound automation (for instance a sun trigger at
  rung 1, where the trace names no site: "no site"). The refusal and its reason are recorded per automation and per
  environment; a refused environment has no outcome and takes no part in that automation's divergences.

## 2. Environments and rungs

The environment space E is 48 environments: the six cross-site cities (`vesper.validation.crosssite.CITIES`) x four
dates (2026-01-15, 2026-04-15, 2026-07-15, 2026-10-15) x plan north 0 and 180 degrees. There is no product axis: the
standard home has one product per sensor role (every L1 Zigbee product shares one reporting rule per quantity in the
catalog, so product variants would be identical rows); every automation is bound at variant 0.

Each environment is one three-day trace of the standard EDT home (no residents, no automations) from local midnight;
day 0 is the thermal warm-up, days 1 and 2 are measured. Recorded weather is fetched for the whole range once per
city and date. Traces are recorded into `<work>/cache`, one work directory per catalog version.

Rungs (the necessity ladder; the default sweep is rung 4):

- 4: the full environment model (site, recorded weather, windows and the sun, the rooms' thermal model);
- 3: as 4 with daylight scaled by 1/14.8 (validation D's measured error);
- 2: the home has no site (the assumed indoor curve); the trace header names the site, so the engine's sun is known;
- 1: as 2, and the header names no site either: sun triggers and conditions are refused, and the oracle cannot judge
  "cover open at night" (it needs sunset), so that check is off at rung 1 (never flagged).

Rung 1 and 2 environments keep their city, date and north, so they match rung 4 environment by environment.

## 3. Screening: open-loop replay

Every bound automation is replayed against each environment's trace in VESPER's own automation engine (permissive
mode), many automations per engine, actions recorded and never executed (open loop). Sensor reports are a step
function: an entity holds its value until its next report. Firings on day 0 and at or after the run's end are
dropped.

## 4. The oracle (thresholds fixed by the plan's Global Constraints)

Per automation and environment, an outcome over the measured days 1-2:

- `fired`: at least one action in the measured window; `first_tod_h`: the local time of day of the first one;
- **flapping**: more than 6 firings of one automation inside one hour (firings at one instant are one);
- **lamp left on all night**: a light the automation turned on in the measured window and nothing turned off by the
  next 01:00 local (a deadline after the run's end is not judged); night is 00:00-05:00 local;
- **cover open at night**: a cover the automation opened after that day's sunset (or before its sunrise) and did not
  close before midnight; a day with no sunset (or sunrise) has no finding on that side; no site (rung 1): not judged.

Across environments (only those where the automation has an outcome):

- `fires_somewhere_only`: fires in some environments and not in others;
- `time_spread`: two environments' first firings differ by more than 3 h of local time of day (the short way round
  the clock);
- `flaps`, `night_light`, `night_cover`: the envelope kinds, when present in at least one environment.

**Environmental versus static.** An envelope kind (flaps, night_light, night_cover) present in **every** environment
where the automation has an outcome does not depend on the environment: it is recorded under `static` (a bug
whatever the environment), not as a divergence. An envelope kind present in some environments and not others is an
environmental divergence. `fires_somewhere_only` and `time_spread` are environmental by definition.

An automation is **flagged** when its environmental divergences are non-empty. The temperature envelope (below 10 C,
above 30 C) needs the closed loop and is judged only in closed-loop confirmation, not here.

## 5. Negative control and the halt threshold

The negative control is the bound automations whose triggers and conditions read no environment at all:

- `env_kinds` of the automation as written is empty (no sun, illuminance, temperature, humidity or weather), and
- every trigger is a `state`, `time` or `time_pattern` trigger, every condition a `state` or `time` condition (or an
  `and`/`or`/`not` of them), and at least one trigger exists, and
- every entity named in its (bound) triggers and conditions is the standard home's motion, door, light, switch or
  lock entity.

In the standard home with no residents, these automations see the same inputs in every environment (local clock
time is the same everywhere), so their environmental flag rate must be near zero. **Halt threshold, fixed before the
run: if more than 5 % of the negative control is flagged, the harness is wrong; the sweep's results are not used
until the cause is found and fixed, and the halt is reported.** The rate is the share of negative-control
automations (that ran) with any environmental divergence.

## 6. Cover, witnesses, held-out split

**Exposure.** An environment exposes a flagged automation when its outcome there departs from what the automation
does in most environments, kind by kind:

- `fires_somewhere_only`: the environments on the minority side of fired / not fired (on a tie, the not-firing side);
- `time_spread`: the firing environments whose first firing falls outside the automation's modal 3-hour window (the
  circular 3-hour window, starting at one of its firing times, that holds the most firing environments; ties to the
  earliest start time); since the spread exceeds 3 h, at least one firing environment lies outside it;
- `flaps`, `night_light`, `night_cover` (environmental only): the environments where the kind holds.

A set of environments covers an automation if it holds at least one environment that exposes it.

**Cover.** Greedy maximum coverage over the environment-by-automation exposure matrix: repeatedly add the
environment that exposes the most not-yet-covered flagged automations (ties to the smaller environment key) until at
least **80 %** of the flagged automations are covered or no environment adds any. Target size: 5 environments
(reported, not forced).

**Held-out half, by repository.** An automation's half is the parity of the integer value of
`sha256(repo.encode("utf-8"))`, where `repo` is the repository name as the corpus file name gives it, written
`owner/name` (e.g. `alice/home`): even is the training half, odd the held-out half. The cover is computed on the
training half's flagged automations only; its coverage is reported on the held-out half's flagged automations.

**Baselines** (coverage on the held-out half and on all flagged automations):

- random-5: the mean coverage of 100 draws of 5 environments from the sweep's environments, uniformly without
  replacement, `random.Random(0)`;
- the six-city grid: the cross-site experiment's hand-picked grid, the 6 cities x 2026-01-15 and 2026-07-15 x north
  0, i.e. 12 environments (size and coverage reported).

**Non-degeneracy**: no single environment may cover more than **50 %** of the flagged automations. The maximum share
and its environment are reported; a violation is reported as a degenerate cover, not hidden.

**Witnesses.** For a flagged automation, a witness is a pair of environments that differ in exactly one of city,
date or north (the axes; there is no product axis) whose outcome classes differ: fired differs, or both fired with
first firings more than 3 h apart, or an envelope kind (flaps, night_light, night_cover) differs. The reported
minimal witness prefers the smallest change: orientation first, then date, then city, then the smaller environment
keys.

## 7. What the sweep writes

One JSON result (the shared shape of `vesper.edt.sweep`): the rung, the environments, per automation its repository,
hash, environment kinds, the automation as parsed, the bound automation (or null and the unbindable reason), its
outcome per environment, its refusals per environment, its environmental divergences, its static kinds and whether
it is in the negative control; then the yield, the flag counts by kind and the negative-control rate. The cover
report is a second JSON (`vesper edt cover`). Results go to `results/edt/` with a results document; the corpus path
is never written into either.

## Addendum 1 (2026-11-04, before the first full run)

Written after the code review of the sweep and before any full sweep has run; the smoke runs (4 environments, at
most 10,000 automations) were used only to check the harness and fix nothing below on their numbers. Each item
supersedes the section it names.

**A1.1 Cover open at night (corrects section 4).** "Cover open at night" is a cover the automation opened **after
that day's sunset** and did not close before midnight: the plan's wording. The "or before its sunrise" side is
withdrawn; it came from an earlier controller ruling, which this corrects. A cover opened at 06:30, before sunrise,
and left open is not flagged.

**A1.2 What the negative control tests (amends section 5).** The standard home has no residents, so the motion, door,
light, switch and lock entities never change during a trace: the control's automations can only fire on the local
clock (time triggers, and state conditions on constant states). The control therefore tests one thing: that local
clock time lines up across environments (every trace starts at its site's local midnight), so a clock-only automation
fires at the same local time everywhere. It does not test the binder's or the oracle's treatment of sensors.
The result reports `negative_control.n` (control automations that ran), `negative_control.fired` (those that fire in
at least one environment) and the flag rate over **the firing subset** (an automation that never fires anywhere cannot
diverge, so it would only dilute the rate). **Halt: the flag rate over the firing subset above 5 %.** The result
records `negative_control.halt` and the threshold; `vesper edt run` prints HALT and exits non-zero when it trips.

**A1.3 Exposure ties and set-based coverage (amends section 6).** Ties: when an automation fires in exactly as many
environments as it does not, **both sides** expose it (neither is the majority); when several circular 3-hour windows
hold the most firing environments, the environments outside **any** of those windows (the union of their outside
sets) expose it. The minority exposure stays the primary criterion of the cover; its assumption is that an
automation's majority behaviour is the neutral one and a departure from it is the finding, which a reader can
disagree with (an automation that misbehaves in most environments is exposed by its well-behaved minority). Beside
every cover and baseline the report therefore also gives the **set-based coverage**, which assumes nothing: a set S of
environments covers an automation iff its environmental divergences restricted to S are non-empty, i.e. the oracle's
`divergences` over the outcomes in S, with an envelope kind that holds in every environment of S counted as static,
exactly as section 4 does over all environments.

**A1.4 Necessity ladder pass criteria (pre-registered; verbatim).** "Rung 0 matches rung 4 iff its repo-grouped AUC
>= 0.90 and its held-out recall at precision 0.55 >= 0.80. A rung r in {1, 2, 3} reproduces rung 4 iff
reproduced_share >= 0.90, firing-time MAE <= 0.5 h and the Spearman correlation of time spreads >= 0.90. The physics is
necessary for detection iff rung 0 does not match rung 4, and for quantification iff rung 1 or rung 2 does not
reproduce rung 4. Rung 3 reproducing rung 4 means the findings are robust to the daylight model's error."

The ladder's statistical choices (`vesper/edt/ladder.py`):

- rung 0 is a logistic regression (L2 1.0, standardised features) on eight syntactic features of the automation as
  written, over the automations bound at rung 4, labelled "flagged at rung 4";
- cross-validation is 5-fold with whole repositories per fold (near-duplicates never straddle training and test),
  seed 0; the AUC of a plain stratified split is reported beside it, not used;
- recall at precision 0.55 has no oracle threshold: each fold's threshold is chosen on its training rows and applied
  to its held-out rows, the held-out predictions pooled, the base rate reported beside it;
- the AUC's 95 % interval is a percentile bootstrap over repositories (1,000 resamples with replacement, seed 0;
  resamples holding one class are not counted; no interval below 100 usable resamples);
- a fold whose training set holds one class is degenerate: it is excluded (and counted); fewer than 2 usable folds
  gives no AUC;
- rungs 1-3 are compared with rung 4 over the automations bound at both, environments matched on (city, date,
  north_deg): reproduced_share = rung-4 flags also flagged at the rung; firing-time MAE = mean circular difference of
  first-firing times of day where both fire; an automation's time spread = the circular range of its first firings
  (the shortest arc of the clock holding them all); Spearman's rho is over the automations whose rung-4 spread is
  above 0 (a flat spread has no rank).

**A1.5 Failures.** An environment in which every replay of every automation fails (or whose trace cannot be
recorded) is an environment-level failure, recorded once under `failed_envs` with its reason, not as a refusal of
each automation. An automation refused in some environments and run in others is counted in `partially_refused`; its
divergences are over the environments where it ran.

## Addendum 2 (2026-11-04, before the first full run)

Written after the binder-coverage smoke runs (4 environments, the first 10,000 automations, used to check the harness
only) and before any full sweep. It amends section 1 (what "bound" means) and section 7 (what the sweep writes).

**A2.1 Gate-open helpers.** Helper and presence entities (`input_boolean`, `input_select`, `input_number`, `group`,
`person`, `device_tracker`, `zone`, `timer`) have no device in the standard home. An automation whose helper or
presence entities appear **only** in `state` / `numeric_state` **conditions** whose entities are all helpers, at the
top level or inside an `and` (`condition: and`, the `and:` shorthand, or a nested list), is bound with those
conditions **removed**; the state each removed condition requires is recorded as an assumption, beside the bound
automation:

- `state`: `input_boolean.away_mode == off`, or `input_select.mode in [home, guest]` for several allowed states
  (an attribute condition reads `person.me[source] == gps`);
- `numeric_state`: a value inside its range, `input_number.t in (18, 22)`, or `input_number.t > 18` / `< 22` when one
  side is open.

Conditions on one helper are merged (the intersection of their allowed states or ranges); a disabled condition
(`enabled: false`) is removed with no assumption (Home Assistant ignores it); a condition's `for:` is assumed to have
held.

Rationale: a helper gate (away mode off, someone home, the heating season selected) is a mode its author set by hand;
the automation is run in the mode its author gated it for. Helpers and presence never change without residents, and
the standard home has none, so a gate either holds for the whole trace or never does; holding it open is the only
setting in which the automation's environmental behaviour can be observed at all.

**A2.2 What stays unbindable**, each with its own reason:

- a helper in a trigger (helpers and presence change only with residents);
- a helper as a numeric threshold (`above` / `below: input_number.x`);
- a helper inside `or` or `not` (or a state condition with `match: any` over several entities);
- conflicting requirements on one helper (no common state or range);
- a helper in a template, in any other condition kind, in an action, or anywhere else in the automation;
- a helper and a device entity in one condition; a helper state given as YAML's unquoted on/off (Home Assistant
  refuses it); a helper `numeric_state` compared against an entity or a template.

**A2.3 Reporting.** Every sweep result records `assumptions` per automation (empty when none) and reports the yield
and the flagged counts twice: over all bound automations (`yield`, `flagged`) and over the assumption-free subset
(`yield_assumption_free`, `flagged_assumption_free`). Every result is stated over both.

**A2.4 Helpers only set (2026-11-04, before the first full run).** Helpers only set by an action pass through and are
recorded: a helper or presence id (the domains of A2.1 and `counter`, `input_text`, `input_datetime`,
`input_button`) that appears only as the target of an action of its own domain (`input_boolean.turn_on`,
`input_select.select_option`, `input_number.set_value`, `timer.start`, `counter.increment`, ...: under `entity_id`,
`target` or `data`) and is never read by the automation (no trigger, condition, gate or template names it) is left
unbound; the permissive engine records the action. Nothing in the run depends on it, so it is not an assumption. A
helper both set and read stays unbindable, as in A2.2.

**A2.5 Binder conventions (2026-11-04, after the binder review, before the first full run).**
- Outdoor names (garden, patio, porch, yard, terrace, ... and weather services' temperature, humidity and
  illuminance) bind to the porch, the standard home's outdoor room. Sensors of semi-outdoor spaces (garage, shed,
  pool, carport, veranda, greenhouse) are refused ("semi-outdoor spaces are not modelled"); their actuators bind to
  the porch's.
- Forecasts, statistics and derived values (high/low, min/max, mean, daily, tomorrow, feels-like, wind chill, trend,
  `1d`/`0h`, ...) and non-ambient temperatures (3D printers, CPUs, disks, water, pools, fridges, soil, batteries,
  boilers, pipes) name no role. A temperature compared with a threshold above 45 or below -30 is refused ("threshold
  looks Fahrenheit or non-ambient"). Two distinct entities that would bind onto one home entity are refused
  ("distinct entities collapse onto one device").
- Device triggers, conditions and actions are translated as Home Assistant defines them (device automations of the
  light, switch, binary_sensor, sensor, lock and climate integrations). Actuator and motion/door device steps on an
  opaque registry id (no name) bind in the living room; sensor device steps (illuminance, temperature, humidity) on
  one are refused ("device steps on a registry id name no room").
- A `group` is a gate only when the state it requires is `home` / `not_home` (a presence group); any other group gate
  is refused ("device groups are not modelled"); a helper `numeric_state` gate with neither `above` nor `below` is
  refused.
- (2026-11-04, after the collapse measurement) Every lit room has three lights and three plugs, the porch two outdoor
  lights and two outdoor plugs (the same products; sensors stay one per role and room). Distinct entity ids of one
  role in one room bind to distinct devices in order of first appearance in the automation; only an automation that
  names more than the room has is refused ("distinct entities collapse onto one device"). An `area_id` target is
  every device of the action's domain in that room, as Home Assistant expands it.
- (2026-11-04, after the re-review) More name tokens: outdoor sensors `extern`/`external`, `ute`, `balcon`,
  `parking`/`parkeerplaats`; the outdoor actuator `lyktstolpe`; semi-outdoor (sensors refused) `pumphus`/`pumphouse`/
  `pump_house`; non-ambient (no sensor role) `fuse`, `cooler`, `frys`, `freezer`, `tank`, `ballon`, `probe`, `mech`,
  `derivative`, `set`. The ambient range of a temperature threshold narrows to -30..35 C. An actuator name with no
  room word binds to the living room's copies. Every ambient sensor name bound in the 10,000-automation sample is
  audited by hand (illuminance, temperature, humidity; room, role, verdict); the audit lives outside the repository
  (it names corpus entities) and only its counts are reported here.
  Audit counts (2026-11-04): pass 1, 63 ambient sensor names bound, 11 mis-binds in 5 classes found and fixed
  (heating setpoints, modes, LED strips, lamp brightness, room words in other languages and the entrance as the
  hallway); pass 2, 54 names: 47 correct (9 of them room-less names bound to the living room by convention), 6
  residual mis-binds and 1 uncertain, all names that carry no room or medium (only their automation's context places
  them).




## Addendum 3 (2026-11-04, before the first full run)

Found in a 10,000-automation smoke run once the engine supported `weekday` conditions and triggers: the plan's four
dates (2026-01-15 Thursday, 04-15 Wednesday, 07-15 Wednesday, 10-15 Thursday) put the measured days on different
weekdays, so an automation gated on weekdays diverged between environments for a calendar reason, not an
environmental one (10 of 11 negative-control flags in that smoke run).

**A3.1 Dates.** The four dates become **2026-01-14, 2026-04-15, 2026-07-15, 2026-10-14**, all Wednesdays, so every
environment's measured days (1 and 2) are Thursday and Friday in local time. The seasons they sample are unchanged to
within a day. The six cities and the two orientations are unchanged. This replaces the plan's Global Constraints'
dates for the sweep and every rung; the cross-site validation (`vesper validate crosssite`) keeps its own dates.

**A3.2 Negative control reads nothing of the environment anywhere.** An automation is in the negative control only if,
besides section 5's rule, no template appears anywhere in it (triggers, conditions or actions) and every entity its
actions name has a motion, door, light, switch or lock role (an action whose data reads an illuminance template was
the eleventh smoke flag).

## Addendum 4 (2026-11-04, controls, before their first run)

Written before either control below has been run (no synthetic automation has been replayed). The real-corpus sweep's
results are unchanged; this adds two synthetic controls that measure the oracle's **accuracy** against answers known
without the replay, where section 5's negative control measures only its internal consistency. Code:
`vesper/edt/controls.py`; command:

    vesper edt controls --work DIR --out FILE [--workers N]

Both controls run through the unchanged sweep (`vesper.edt.sweep.sweep`): rung 4, all 48 environments of section 2,
the standard home, the same trace cache as the real sweep when `--work` names its work directory. Every synthetic
automation is ours, written against role names the binder binds (`sensor.porch_temperature`,
`sensor.living_room_illuminance`, ...), checked to bind before the run, and acts only on a smart plug
(`switch.turn_on`), so no lamp or cover envelope can arise. The automations and the per-environment expectations are
written into the result.

**A4.1 Positive control: analytically known behaviour.** About 50 automations of five kinds; for each, its expected
firing times per environment are computed without the replay, then reduced to an outcome (fired, first firing's local
time of day, flapping) and to the expected environmental divergences exactly as section 4 defines them (written out
independently of `vesper.edt.oracle`):

- `sun_trigger`: sunrise or sunset with a fixed offset (-70 to +70 min). Expected firing times: the site's
  `vesper.env.sun.sun_events` for days 0-2 plus the offset, kept in the measured window. Fires everywhere; across the
  48 environments its first firing spreads by more than 3 h (expected `time_spread`).
- `time_trigger`: a time trigger with no condition. Fires everywhere at the same local time: expected no divergence.
- `time_sun_condition`: a time trigger with a sun condition (after sunrise or before sunset, Home Assistant's
  semantics: the day's event plus offset). Expected fired/not fired per environment from the site's sun events.
- `sun_time_condition`: a sunrise or sunset trigger with a time-of-day window condition (`after`/`before`, Home
  Assistant's semantics, the window possibly across midnight).
- `numeric_state`: an `above` or `below` trigger on the porch temperature (thresholds at the 5th to 95th percentile of
  the porch temperature pooled over the 48 traces' measured days), the living room's illuminance and the porch's
  illuminance. Expected firing times are computed **from the trace itself** (the recorded reports, not the replay):
  Home Assistant's armed semantics (armed at start iff the initial value is out of range; a report into range while
  armed fires and disarms; a report out of range re-arms), each report delivered at its `observed_t` + 0.05 s.

No boundary may be close: a candidate automation is dropped **before the run**, by the generator from the analytic
expectations alone, if in any environment a firing candidate lies within 10 min of a condition's boundary, or its
expected first-firing spread lies within 10 min of the 3 h threshold; numeric thresholds are chosen half-way between
recorded values (the sensors report in steps of 0.1 C and 1 lx), so no report equals one.

Scoring. An expected divergence is a pair (automation, kind) with kind in `fires_somewhere_only`, `time_spread`,
`flaps` among the automation's expected environmental divergences; it is detected if the sweep reports that kind for
that automation. A false positive is a pair the sweep reports that is not expected. The first-firing error is the
circular difference between the sweep's `first_tod_h` and the expected one, over every (automation, environment)
where both fire. Reported: the detection rate overall and per divergence kind and per automation kind, the false
positives with their automations, the first-firing MAE and maximum (minutes) overall and per kind, and the
per-environment fired agreement. An automation that does not bind, is refused or fails counts as not detected.

**Pass (positive):** detection rate >= 95 % of the expected divergences, 0 false positives, first-firing MAE <= 2 min.

**A4.2 Sensor-path negative control: provably environment-independent sensor readers.** About 15 automations that read
a sensor but can never fire in any environment, so they exercise binding, the sensors' reporting and the replay's
sensor delivery without any environmental effect: `numeric_state` triggers below -25 C on indoor and outdoor
temperatures (the binder refuses thresholds outside -30..35 C), below 2 % or above 100.5 % relative humidity, above
10^7 lx or below -1 lx; `state` triggers to states no sensor of that role reports (a temperature `to: boiling`, an
illuminance `to: dark`, a motion sensor `to: jammed`); a time trigger with a `numeric_state` condition below -25 C;
a sunset trigger with an illuminance condition above 10^7 lx. Expected: none fires in any environment, none is flagged.

**Pass (negative):** 0 flagged (no environmental divergence). Anything that fires is reported with its environments,
and is a finding even when it is not flagged.

The results go to `results/edt/controls-<commit>.json` and a section of `docs/experiments/results-edt.md` with the
verdict against these criteria; a miss or a false positive is reported with its cause, and nothing above is changed
after the run.

## Addendum 5 (2026-11-04, home variation, before its first run)

Written before any sweep of a home other than the standard one has run. Reason (the review): every result above comes
from one standard home, so the orientation axis's witnesses (section 6) cannot tell "this automation is
orientation-sensitive" from "this automation is sensitive to this plan's windows" (the bedroom's window faces west,
the office's east, the living room's two panes the plan's top). This addendum varies the home itself. Nothing above
changes: the environments (section 2, Addendum 3's dates), rung 4, the oracle, the binder, the negative control and its
halt rule apply to every variant as to the standard home. The standard home's rung-4 sweep at 120f4ca (recorded ERA5
weather) is the base.

Code: `vesper.edt.home.build_edt_home(work, catalog_dir, variant=None)` (`VARIANTS`); command:

    vesper edt run --corpus DIR --work DIR --out FILE --workers 6 --rung 4 --home-variant NAME

`variant=None` (no `--home-variant`) is the standard home, byte for byte (its home.yaml's sha256 is pinned in
`tests/edt/test_home_variants.py`); the sweep's result then has no `home_variant` key.

**A5.1 The four variants**, each defined by one parameter, fixed here:

- **glazing-x0.5** and **glazing-x2**: every window pane's area times 0.5 or 2, at the same place (the same wall, the
  same centre along it, the same facing). Rule (`scale_glazing`): width and height times sqrt(factor) about the pane's
  centre; a width that would come nearer than 0.3 m (`windows.CORNER_M`) to its wall's end, or nearer than 0.1 m to a
  neighbouring pane on the same wall, is capped there and the height takes the rest, so the area is exactly the factor
  times the original; the pane keeps its vertical centre, moved down if its top would come within 0.05 m of the 2.5 m
  ceiling (up if its sill would go below the floor). Applied to the standard home this gives (width x height, sill;
  m):

  | Pane | Standard | x0.5 | x2 |
  |---|---|---|---|
  | living room, 2 panes, facing the plan's top | 1.6 x 1.4, 0.6 | 1.131 x 0.99, 0.805 | 1.9 x 2.358, 0.092 (width capped) |
  | bedroom 2, top | 1.2 x 1.2, 0.9 | 0.849 x 0.849, 1.076 | 1.697 x 1.697, 0.651 |
  | bedroom 1, west (plan left) | 1.2 x 1.2, 0.9 | 0.849 x 0.849, 1.076 | 1.697 x 1.697, 0.651 |
  | kitchen, bottom | 1.0 x 1.0, 1.05 | 0.707 x 0.707, 1.196 | 1.2 x 1.667, 0.717 (width capped) |
  | bathroom, bottom | 0.6 x 0.6, 1.5 | 0.424 x 0.424, 1.588 | 0.849 x 0.849, 1.376 |
  | office, east (plan right) | 1.2 x 1.2, 0.9 | 0.849 x 0.849, 1.076 | 1.697 x 1.697, 0.651 |

  The windows change the indoor model through the glass area (daylight, solar gain, heat loss); the furniture is
  placed again by the generator's furnisher for the new panes (it keeps tall pieces from in front of a window);
  furniture takes no part in an EDT trace (no residents). The plan, the devices and their places are the standard
  home's.
- **plan-seed8**: another generated floor plan, `vesper lab generate` with six rooms and **seed 8**, the first seed
  after the standard home's 7 (chosen by that rule, not by looking at any plan's results). Every six-room request has
  the same room program (living room, two bedrooms, kitchen, bathroom, office, with the corridor and the porch), so
  every seed has every room and every role; seed 8 was checked to give all eight rooms and the standard home's 101
  roles. Its plan: the living room, bathroom and bedroom 2 along the plan's top side (in the standard home, the
  living room and bedroom 2), bedroom 1, kitchen and office along the bottom (standard: bedroom 1, kitchen, bathroom,
  office), every room's size redrawn (the corridor 9.1 m long, standard 10.0 m); its windows by the generator's rule:
  the living room's two panes 1.45 m wide facing the top, the bathroom's the top, bedroom 2's east (standard: the
  top), bedroom 1's west, the kitchen's the bottom, the office's east. The generator would draw other products from
  another seed, so the products are fixed to the standard home's, category by category (the request names them),
  and placed by the generator's rules (with seed 7 this rebuilds the standard home exactly; tested).
- **mirror-ew**: the standard home mirrored east-west, x -> 8.2 - x (the sum of the walking grid's two x edges, so
  the grid maps onto itself): rooms, walking grid, devices and wall switches, windows (a pane facing east faces west)
  and furniture. Bedroom 1's window faces east, the office's west, the porch and the front door are on the east end;
  every pane's size and every room's size are unchanged.

In every variant the EDT devices (section 2's sensors in every room and on the porch, the corridor's light and motion
sensor, three lights and three plugs per lit room) are added by the same rules from the rooms' boxes (the corridor's
light at its centre, its wall switch 0.5 m from the front-door end, its motion sensor 3/4 of the way from that end;
in the standard home these are exactly the earlier fixed places). The role set and every entity id are the standard
home's, so an automation binds identically in all five homes (the binder sees only roles and ids); only placement and
the environment model's inputs differ. Each variant passes `vesper lab check` and the layout check with no error
(tested).

**A5.2 Runs.** Each variant once at rung 4 over all 48 environments of the same corpus, one after the other, each in
its own work directory, `--workers 6`, after the standard home's rung-4 and rung-3 sweeps at 120f4ca finished. Every
variant reads the same recorded weather as the base (the base work directory's ERA5 cache, copied in before the run;
no trace is copied). A variant whose negative control halts (section 5) is reported as halted, and its numbers are not
used in A5.3.

**A5.3 What is reported** (descriptive only: **there is no pass/fail threshold**; nothing below is a criterion the
variants must meet, and no number is tuned or chosen after seeing it). Results: `results/edt/home-variation.json` and a
section of `docs/experiments/results-edt.md`, aggregates only.

- Per home (the standard and the four variants): the yield (parsed, bound, run); the negative control (n, firing,
  flagged, rate); the flagged count; flagged automations by divergence kind; flagged automations by environment kind
  (the automation's kinds as written: sun, illuminance, temperature, humidity, weather; an automation counts once in
  each of its kinds); the minimal-witness axis distribution (section 6: orientation, date, city, none).
- Across homes, over the automations that ran in all five homes: of those flagged in at least one home, the share
  flagged in **all five**, in **at least three**, and in **only one** (with counts), and the number flagged in each
  number of homes (1 to 5); of the standard home's flagged, the share flagged in every variant; the same by
  divergence kind (an automation and a kind: flagged with that kind).
- The orientation witnesses' stability. An automation has an orientation witness in a home when two of its
  environments differing only in plan north (same city and date) have different outcome classes (section 6's
  witness rule, `vesper.edt.cover.witnesses`, axis `north_deg`). Reported: per home, the number of automations with an
  orientation witness and the number whose minimal witness is orientation; of the standard home's automations whose
  minimal witness is orientation, how many have an orientation witness in each variant, in all four, in none, and
  how many are not flagged at all in each variant; the overlap of the orientation-witnessed sets between each
  variant and the standard home (Jaccard).

How it will be read (stated now): an automation flagged in all five homes, or with an orientation witness in all
five, does not owe that to one plan or one window; one flagged (or orientation-witnessed) only in the standard home
owes it to that home. The mirror separates "faces east" from "faces west" for the same plan; the other plan changes
which rooms have which façade; glazing changes how much the windows weigh. Five homes are a small sample of plans, not
a population of real homes; that limit is stated with the results.

## Addendum 6 (2026-11-04): the frame-relative relation, the dead-rule class, rung 2.5, the richer outcome

Written after the second internal review of the results above and before any run under it. Because it follows a
review of results already seen, everything it adds is **post hoc** in the paper's terms (chosen after the first
results); its rules are nevertheless fixed here, before the runs that use them, and nothing below is tuned on those
runs. Nothing earlier changes: the relation of section 4 (now called the *clock relation*) is computed beside the new
one on every sweep, and every earlier verdict stands as reported.

**A6.1 Frames.** An automation's timing frames are read from its bound triggers and conditions (nested `and` / `or` /
`not` included; conditions inside actions and `choose` options are not):

- **clock**: a `time` or `time_pattern` trigger; a `time` condition; a template trigger or condition reading `now()`
  or `utcnow()`;
- **sun**: a `sun` trigger; a `sun` condition; a `state` or `numeric_state` trigger or condition on `sun.sun`; a
  template reading `sun.sun`;
- **sensed**: a `state` or `numeric_state` trigger or condition, or a template, on an entity that reads illuminance,
  temperature, humidity or weather (the rules `vesper.edt.corpus.env_kinds` uses).

Anything else (motion, doors, actuators, helpers, start events) gives no frame. An automation with no frame is judged
in the clock frame, as before.

**A6.2 Frame-relative first firing.** In each environment where the automation fires, its first firing in the judged
window has one value per frame:

- clock: its local time of day (the outcome's `first_tod_h`);
- sun: the signed minutes from the nearest *reference instant* (on the firing's day or either neighbouring day). The
  reference instants are those its sun triggers name: sunrise or sunset plus the trigger's offset; a `state` trigger
  on `sun.sun` to `below_horizon` (sunset), to `above_horizon` (sunrise), or with no `to` (both); a `numeric_state`
  trigger on the sun's elevation: the time the elevation crosses the threshold, rising for `above` and setting for
  `below`. An automation with no such trigger takes those of its sun conditions (`after` / `before` events with
  their offsets, `sun.sun` states, elevation thresholds). They are computed from the environment's site and date with
  the astral code the replay's engine uses (`vesper.env.sun.sun_events`; astral's `time_at_elevation` for an
  elevation). A sun frame with no reference that can be computed (a template, the azimuth) is not computable;
- sensed: not computable.

**A6.3 The frame-relative relation.** Per frame: the clock frame is invariant iff the circular spread of the first
firings' times of day is at most h = 3 h (section 4); the sun frame iff the range (max minus min) of its sun-relative
minutes is at most 180 min, over the environments where a reference exists that day; a frame that is not computable
counts as invariant (its timing is commissioned by the quantity it reads). The timing divergence under the relation
(`time_spread` in its result) holds iff **no** frame of the automation is invariant. Hence: a single-frame automation
is flagged for timing only if its spread exceeds h in its own frame; a pure sensed automation never is; a mixed-frame
automation only if it is invariant in none of its frames. `fires_somewhere_only`, `flaps`, `night_light`,
`night_cover` and the static rule are unchanged. An automation is flagged under the relation iff its divergences
under it (`frame_divergences`) are non-empty; `divergences` stays the clock relation's.

Reported beside it, as a sensitivity and not a criterion: the same relation with the sensed frame *not* counted as
invariant whenever the automation has a computable frame. Witnesses under the relation: a pair differing in one axis
whose divergences under the relation, restricted to the pair, are non-empty; the minimal witness's preference is
section 6's. Also reported per automation: the set of axes that admit a witness. Proposition 1 (a set diverges iff
one of its pairs does) holds for each single frame but not for mixed-frame timing; the number of automations flagged
under the relation with no pair witness at all is reported.

**A6.4 The richer outcome (reported only, never part of any flag).** Per environment, beside the outcome, the sweep
keeps: the firing count (distinct firing instants in the judged window); the payload, a canonical hash of the set of
distinct (service, entity, data) calls in the judged window, where data are the command parts the engine sends (a
light's state and brightness, a thermostat's mode and setpoint, a switch's or lock's payload; an action the engine
only records contributes its service and entity, since the engine does not render its data); and the branches, the
set of (choose or if step, option taken) pairs in the judged window (the default as option -1). Over the environments
where the automation fires: `count_differs`, `payload_differs`, `branch_differs` when they are not all equal. Reported:
how many automations that are **not** flagged (clock relation, and frame relation) differ under each.

**A6.5 Rung 2.5.** Between rungs 2 and 3: the home has its site and the recorded weather (ERA5), so the porch's
(outdoor) sensors read the outdoor air temperature, humidity and illuminance exactly as at rung 4; every indoor
room's sensors read rung 2's assumed curve (`vesper.lab.rules.ambient_at`, with the lamps that are on), not the room
model. The trace header names the site, as at rungs 2-4. Section 2's environments, the ladder's comparison with
rung 4 (A1.4) and every report of a rung apply to it. The sensor-driven flags (flagged at rung 4 and reading a sensed
quantity as written) are split into **ERA5-only** (flagged at rung 2.5 too) and **room-model-dependent** (not flagged
at rung 2.5), under the clock relation (the paper's 179) and under the frame relation; the share that keeps the same
divergence kinds at rung 2.5 is reported beside.

**A6.6 The dead-rule class.** An automation is *dead* when it runs at rung 4 (has an outcome in at least one
environment) and fires in none. The class needs no contrast and is anti-monotone in E (a larger grid can only remove
members). Each dead automation's reason is derived mechanically by replaying the dead automations again, open loop,
against the same rung-4 traces in two probes whose only action is one call (`switch.turn_on` on a plug): P1, its
triggers alone (no conditions); P2, its triggers and its conditions. Reasons, the first that applies:

- (a) P2 fires somewhere: *the actions end before any call* (an action-level condition, a `choose` with no matching
  option, a delay past the window);
- (b) P1 fires somewhere and P2 nowhere: *a condition never holds when a trigger fires*. The blocking condition is
  found by replaying P1 plus every top-level condition but one; it is named by kind: a sun condition (*unsatisfiable
  at these latitudes and dates*), a time condition, a sensed-quantity condition, a condition on an entity that never
  changes (motion, door, actuator), another or a template; more than one: *several conditions*;
- (c) P1 fires nowhere: from the traces, for each `numeric_state` trigger on a sensed entity, per environment:
  *unreachable* (no value over the run lies on the firing side), *never armed* (every value lies on it, so Home
  Assistant never arms the trigger), or *armed, no crossing in the judged window*. Every (trigger, environment)
  unreachable: *threshold out of the observed range in every environment*; every one never armed: *never armed*; only
  those two, mixed: *threshold unreachable or never armed, by environment*; otherwise *armed numeric_state never
  crosses in the judged window*. Every trigger a `state` trigger on entities that never change in a trace (no
  residents; actuators held by the open loop): *trigger entities never change*. Anything else: *not derived*.

Reported: counts by reason, over all dead automations and over the environment-coupled ones. A hand audit of the
reasons is a separate task, not part of this addendum.

**A6.7 The ladder and the cover, restated.**

- The necessity criterion for quantification is restated on rung 2 alone: *the physics is necessary for
  quantification iff rung 2 does not reproduce rung 4* (A1.4's reproduction rule). Rung 1, which flags nothing by
  construction, is a sanity check only. Reported under the clock and the frame relations, rung 2.5 beside it.
- Rung 0 is refitted against the frame relation's flags (A1.4's model, folds, bootstrap and held-out threshold),
  separately over the automations that run (not the bound ones) and over the environment-coupled automations that
  run; each with the eight A1.4 features, and with those plus four frame features (clock, sun, sensed frame present;
  more than one frame). The clock relation's flags are refitted on the same two populations, for the denominator
  correction alone.
- Greedy maximum coverage over the 1,128 environment pairs, for k = 1, 2, 3 pairs: a pair covers an automation iff
  its divergences (clock or frame relation) restricted to the pair are non-empty; chosen on the training half, ties
  to the smaller environment keys; coverage reported on the training half, the held-out half and all. Greedy keeps
  its 1 - 1/e guarantee over pairs; k = 1 is the exhaustive best pair.
- Every rung's run count (automations with an outcome) is reported.

**A6.8 Runs.** At the commit that implements this addendum, in a fresh work directory whose ERA5 cache is copied
from the `120f4ca` work directory (no trace is copied), at rung 4, 2.5, 2, 3 and 1, in that order, 5 worker
processes. **Check before anything is reported:** the clock relation on the new rung-4 sweep must give the 797
flagged automations of `120f4ca` exactly; if it does not, the cause is found before any number is used. Section 5's
halt rule applies to every rung.

**A6.9 Two binder defects and the illuminance reporting rule (added 2026-11-05, before any run under this
addendum).** The R4-C audit of the 179 sensor-driven flags (results-edt.md, "Revision R4-C") found two binder rules
that generate artefacts and a reporting rule the catalog gets wrong. Fixed here, before the runs:

- *Weather tokens.* A `weather` token (a weather service's name) sent every name to the porch, `weather_<room>` names
  and Aqara's `lumi.weather` room sensors included. Now a name that is outdoors only by such a token is bound by its
  room tokens; with none, a `lumi_weather` name takes the room-less convention (the living room, A2.5) and any other
  name stays on the porch. A name with an outdoor place word (garden, outdoor, balcony, ...) is on the porch as before.
- *Enclosures and media.* A temperature, humidity or illuminance sensor whose name says an enclosure or a medium
  (server room, comms cupboard, rack, humidor, freezer or fridge, filament box, grow box or tent, terrarium, vivarium,
  aquarium, water, pool, a tub, a sauna, a fermenter) is refused: "enclosure or medium, not room air". A semi-outdoor
  space (A2.5) is refused as before.
- *The reporting rule rho_p.* The standard home's only illuminance sensor, the Aqara RTCGQ11LM, sends illuminance only
  with a motion detection, or on its roughly hourly heartbeat (zigbee-herdsman-converters 26.123.0, `lumi_illuminance`:
  "there is no illuminance without movement"); the catalog gives it the library default (any change, at most every
  10 s, at least every 3600 s). The twin now follows the device: under the *device* rule its illuminance is reported
  only with an occupancy "on" report and on the heartbeat (every 3600 s after its last report), so in the
  resident-free home only the heartbeat. The catalog's rule stays available (`--reporting-rule catalog`); the device
  rule is the default of an EDT sweep from this addendum on, and nothing outside EDT changes (the runtime's default
  stays the catalog's).

**A6.8, as amended by A6.9 (supersedes A6.8 above).** At the commit that implements A6.1-A6.9, in a fresh work
directory whose ERA5 cache is copied from the `120f4ca` work directory (no trace is copied), 5 worker processes, in
this order:

1. *The check:* rung 4 with the `120f4ca` binder and the catalog's reporting rule. The clock relation must give the
   797 flagged automations of `120f4ca` exactly; if it does not, the cause is found before any number is used.
2. *The main runs:* rungs 4, 2.5, 2, 3 and 1 with the current binder and the device reporting rule. Every number of
   A6.3-A6.7 is reported from these.
3. *The reporting-rule comparison:* rung 4 with the current binder and the catalog's rule. Against run 1 it measures
   the binder fixes alone, against the main rung 4 the reporting rule alone. Reported: flags under both rules, both
   relations, overall and for the illuminance-reading automations that run (the audit's 67 at `120f4ca`), and how
   many of the 179 sensor-driven flags of `120f4ca` the binder fixes unbind or unflag.

Section 5's halt rule applies to every run.

## Addendum 7 (2026-11-04, post hoc, after the second internal review; before any of its runs)

**Post hoc**: written after the second internal review (Major Revision) asked for it and after the results it revisits
were seen (revision R2-a, R2-c and the ERA5 re-run in `docs/experiments/results-edt.md`), but before any run below.
Nothing earlier changes; the numbers below are reported beside the earlier ones, not in place of them. Base: the
rung-4 ERA5 sweep at 120f4ca, its home, its recorded weather (never fetched again) and the R2-c setback population.
Script: `scripts/revision/r4b_error_budget.py`; results: `results/edt/error-budget-r4b-120f4ca.json` (aggregates and
anonymous indices only) and a section "Revision R4-B (Addendum 7)" of `results-edt.md`.

**A7.1 New knobs of the indoor model** (`vesper.env.indoor`, set only through `indoor.knobs`; at the defaults the model
is the one built, to the bit: the pinned fingerprint test covers them):

- `INTERNAL_GAINS_W_PER_M2` (default 0): a constant heat gain per m² of floor in every indoor room (appliances,
  standby, distribution losses), added to the room's heat balance.
- `ROOF_GROUND` (default 0, off; 1 on): every indoor room also loses heat through a roof, U_ROOF = 0.15 W/(m² K), and a
  ground floor, U_GROUND = 0.25 W/(m² K), each over its floor area (the home is one storey), both to the outdoor air
  (a ground at the outdoor air temperature is the pessimistic reading; stated with the results).
- `PREWARM_DAYS` (default 0): extra warm-up before the run's first instant. The rooms start at START_C (20.5 C) that
  many days earlier and live them on the run's first day's weather repeated (each earlier day is day 0 again), the
  thermostat at the home's own setpoint (20 C, the EDT home's thermostat before any command; heating band 0.3 K,
  100 W/m² of floor) and no automation, every 60 s (the session's ambient step); then the run starts as before (day 0
  in the run is still the run's own day 0). `inf`: the periodic steady state of day 0, lived as a fixed 30 days of
  the repeated day (more than six of the slowest free-running time constant, C / H about 110 h at 370 kJ/(m² K)).
  (Changed before any corner run, from "repeated until no room's temperature at the day's end differs from the day
  before's by more than 0.001 K, at most 365 days": the knob's test and one smoke run (Minneapolis January, 370
  kJ/(m² K)) showed that a heated room's on-off cycle is not locked to the day, so even its daily mean keeps a
  0.001-0.02 K day-to-day jitter from day 8 on and a tolerance below it is never met.) Warm-up
  length in the sense of the review = 1 + PREWARM_DAYS days (day 0 in the run counts): 1, 5, 10 days are 0, 4, 9.
- `SOLAR_APERTURE_SCALE` (default 1): the solar heat through every window (G_SOLAR x glass area x the irradiance on
  it) times this. Daylight is not scaled (it has DAYLIGHT_SCALE).
- `OUTDOOR_T_OFFSET_C` (default 0): kelvin added to the outdoor air temperature wherever the model reads it (every
  room's heat balance, the porch's sensors). The outdoor air's vapour pressure is kept, so the porch's relative
  humidity is recomputed at the shifted temperature (the bias is in the temperature, not the moisture).
- `HUMIDITY_CLASS` becomes a knob (default 1, as built): EN ISO 13788 class 1, 2, 3 = 270, 540, 810 Pa of indoor
  vapour-pressure excess at an outdoor temperature at or below 0 C.

Each knob is tested to move the model in its expected direction (gains warm a room, roof and ground cool it, a larger
aperture warms a sunny room, an outdoor offset moves the rooms the same way, a higher class raises the humidity, a
longer warm-up brings a room nearer its periodic state).

**A7.2 Review item 4: the setback population over the error-budget corners.** The 22 automations of R2-c (same static
rule), each alone, closed loop (`vesper.edt.confirm.closed_loop`, unchanged; three days, days 1-2 judged), against
the same corner's baseline (no automation) in the same environment; R2-c's causal rule (a room-run cell counts when
the room's minimum over days 1-2 is below 10 C with the automation and not in the baseline). The rooms start at
20.5 C; the baseline thermostat holds 20 C.

Corners (the full product, 120): internal gains G in {0, 2, 5} W/m² x roof and ground F in {off, on} x warm-up W in
{1, 5, 10 days, periodic steady state} x heat capacity C in EN ISO 13790's five classes {80, 110, 165, 260, 370}
kJ/(m² K) (CAPACITY_SCALE = C / 165). The nominal corner (G 0, F off, W 1 day, C 165) is the model as built.

Design (fractional, stated now, because the product over all 48 environments, 120 x 48 x 23 runs, is about 132,000
runs):
- *Stage 1, screen, all 48 environments*: the four corners (G 0, F on, C 80, each W), the corner that loses the most
  heat fastest at each warm-up length, for all 22 automations and the baselines (4 x 48 x 23 runs).
- *Stage 2, full product on E*: all 120 corners, all 22 automations, in E = the environments with any caused breach in
  stage 1, together with R2-c's four breach environments (Minneapolis and Stockholm, 2026-01-14, both orientations).
  If E holds more than 8 environments, stage 2 runs W in {1 day, periodic} only outside those four (stated with the
  results).
- *Outside E* every corner is counted as no breach. This rests on the stage-1 corner being the most breach-prone at
  its warm-up length; it is checked on E (at every corner, the set of breaching runs is compared with that of the
  stage-1 corner of the same W, and every run that breaches at a corner but not at its screen corner is reported).

Reported, per corner: breaching runs as a numerator over two fixed denominators in the same environments for every
corner: (a) 22 x |J| runs, J = the January environments of E with a caused breach at any corner; (b) 22 x 48 runs (all
environments). Also per corner: caused cells, automations and repositories with a breach, per city, the coldest caused
room per run (min / median / max) and the baseline's coldest room. Summaries: the minimum and maximum over the 120
corners with the corners that attain them (the worst and the best corner), the marginal mean per level of each factor,
whether Minneapolis and Stockholm each breach at every corner. Check: the nominal corner reproduces R2-c step 2 on E
exactly (every minimum, baseline minimum and breach), reported.

**A7.3 Review item 5: propagating the solar-aperture, outdoor-temperature and humidity-class errors (open loop).**
Affected set: the automations that run at rung 4 and read a temperature or a humidity (environment kinds as written),
or have an orientation witness (A5.3's rule). Each setting below records all 48 traces afresh in its own cache (the
sweep's weather copied in, no trace copied) and replays the affected set with the sweep's own worker; the reference
is the aperture-1 setting (class 1, offset 0), recorded afresh, which must reproduce the published sweep's outcomes for
the affected set exactly (reported).

- Aperture x {0.25, 0.5, 1, 2} (SOLAR_APERTURE_SCALE): how many of the 129 automations with an orientation witness
  keep one, how many of the 37 flagged humidity automations and of the 128 flagged temperature automations stay
  flagged; flags lost and gained, divergence kinds changed, over the affected set.
- Outdoor temperature -2 and +2 K (OUTDOOR_T_OFFSET_C): how many of the 128 flagged temperature automations change
  flag (lost, gained) or divergence kinds; the same for the humidity ones and the orientation witnesses.
- Humidity class 1, 2, 3 (270, 540, 810 Pa): how many room relative-humidity series reach the model's 85 % clamp (a
  series is one indoor room in one environment over days 1-2 of the baseline home, no automation; 7 rooms x 48
  environments), and how many reach 70 % and 55 % (the reported automations' thresholds); and how many of the 37
  humidity flags change.
- Check (the affected set is the right one): at aperture x2 and at +2 K, every automation that runs at rung 4 is
  replayed; the outcomes of those outside the affected set must be identical to the published sweep's (reported).

**A7.4 Review item 6: isolation versus the rule set.** Context of an automation: every other automation of its
repository in the sweep that binds and runs at rung 4 (its stored bound form); automations of the repository that do
not bind (or were de-duplicated into another repository) cannot run and are counted, not run. First: the engine must
run several automations in one session (`closed_loop` gets a `companions` argument, tested; alone it is unchanged).
Each run labels every automation by its index so that firings are attributed.

- Setback (the 22): in R2-c's four breach environments, each automation (i) alone, (ii) with its context, (iii) its
  context without it. A breach survives when the room-run cell breaches in (ii) and not in the no-automation baseline;
  it is the automation's when it also does not breach in (iii). Reported: of R2-c's 53 breaching runs, how many
  survive with the context, how many are the automation's own; new breaches in context; and, per lost one, why: a
  companion commands a setpoint of 10 C or more during the setback (the restore; whether the companion's thermostat
  is the same author entity or a different one the binder put on the home's single thermostat), the automation no
  longer commands its setback, or other.
- night_light: the 30 automations flagged with a night_light divergence, in every environment where the screening
  outcome has night_light; and the 226 with a static night_light, in the first two such environments (sorted keys).
  Each alone and with its context. The automation's outcome is the oracle's own, from its own firings, except that a
  lamp it turned on counts as turned off by any automation of the run (the rule set's "nothing turned it off").
  Reported: how many night_light outcomes (run level) and automations survive with the context, and per lost one why:
  a companion turns that lamp off before 01:00 (same author entity, or a different one the binder put on the same home
  lamp: a binding collision, which would make the loss an artifact), the automation no longer turns it on, or other.

## Addendum 8 (2026-11-05, post hoc, after the third internal review; before any of its runs)

**Post hoc**: written after the third internal review asked for these analyses, and after the results they revisit
were seen (revision R4-A and R4-C in `docs/experiments/results-edt.md`), but before any number below is computed or
run. Nothing earlier changes. The numbers below are reported beside the earlier ones, not in place of them. A change
made after a run is dated here and called a deviation.

Base: the rung-4 and rung-2.5 sweeps at `8fcd672` (current binder, device reporting rule), their rich outcome cache
(A6.4) and their traces (`work-8fcd672/cache`). The A6.7 rung-0 refits, the R4-C dead-rule audit (100 of 391,
seed 20261104) and the Addendum 5 home variants are the other inputs. Script: `scripts/revision/r6a_analyses.py`.
Results: `results/edt/r6a-<commit7>.json` (aggregates only: no automation, repository, entity or hash), and a section
"Revision R6-A (Addendum 8)" of `results-edt.md`. Review 3's item numbers are given in brackets.

**A8.1 The confirmatory rung-0 test on the registered split [1(c)].** This is a pre-specified secondary analysis of a
post hoc relation (the frame relation is Addendum 6's).

- *Split:* section 6's held-out half by repository (`vesper.edt.cover.half`): even is training, odd is held out.
- *Model:* A1.4's rung 0, a logistic regression (L2 1.0, standardised features) fitted once on all training-half
  rows. It is fitted with the eight A1.4 features, and again with those plus A6.7's four frame features (8+4).
- *Label:* flagged under the frame relation at rung 4 (non-empty `frame_divergences`).
- *Primary population:* the automations that run at rung 4. This is the population of A6.7's frame-relation refit
  (5,926; 199 flagged). The bound (A1.4's population) and the environment-coupled run automations are reported beside
  it, as secondary.
- *Threshold:* chosen on the training half's own (in-sample) scores, as A1.4's folds do: the best recall at
  precision >= 0.55 there (`ladder.recall_at_precision`). It is applied unchanged to the held-out half.
- *Reported, on the held-out half:*
  - the AUC, with a 95 % percentile bootstrap over held-out repositories (1,000 resamples, seed 0, A1.4's rule);
  - the recall and the realised precision at the training threshold;
  - the base rate, and the training and held-out counts (rows, flagged, repositories).

**Detection criterion, written before any held-out number is computed.** A1.4's form and thresholds, applied to the
frame relation on this split: *rung 0 matches rung 4 under the frame relation iff its held-out AUC >= 0.90 and its
held-out recall at precision 0.55 (the training-half threshold) >= 0.80.*

- The verdict is the eight-feature model's (A1.4's rung 0). The 8+4 model is judged by the same rule and reported
  beside it, as a richer syntactic linter.
- If rung 0 does not match, the physics is necessary for detection of frame-relation divergences on this split, as a
  secondary analysis. If it matches, it is not.
- The realised held-out precision is reported but is not part of the criterion, as in A1.4.
- *For comparison:* the same fit, threshold and criterion on the clock relation's flags (non-empty `divergences`), on
  the same split and populations.

**A8.2 The paired bootstrap of the AUC difference [7(b)].** On the run automations, the frame relation's flags:
A6.7's out-of-fold scores (5-fold, whole repositories per fold, seed 0), once with 8 features and once with 8+4, on
the same folds and the same rows.

- The difference is AUC(8+4) minus AUC(8).
- Its 95 % interval is a percentile bootstrap that resamples whole repositories with replacement (1,000 resamples,
  numpy `default_rng(0)`). Both AUCs are recomputed on each resample's rows; a resample holding one class is not
  counted.
- The same is reported for the environment-coupled run automations, for the clock relation on both populations, and
  for the A8.1 held-out half (resampling held-out repositories).

**A8.3 The two exemptions and two ambiguities [5(c), 5(e)].** Counts over the run automations, the clock relation's
790 flags and the frame relation's 199 flags (rung 4, `8fcd672`).

- *The sun exemption.* These are automations with a sun frame and no computable sun reference (A6.2). Each is
  classified by the sun-reading steps it has:
  - *azimuth*: a step on `sun.sun`'s azimuth;
  - *template*: a template reading `sun.sun`;
  - *other*: e.g. a sun condition naming no event, an unreadable offset, another attribute.
  An automation may have several reasons.
- *The sensed frame among the 199,* by frame combination.
- *Two sun events.* Automations whose sun references name more than one event:
  - (i) the references the relation uses (A6.2) name two or more of sunrise, sunset and an elevation;
  - (ii) its triggers and its conditions name different events, so the conditions' references are not used.
- *Templates reading both `now()` and `sun.sun`.* One template text that matches both. Each count is given with how
  `vesper.edt.oracle` treats it.

**A8.4 The frame-switch re-tabulation [14].** Population: the automations flagged under the clock relation and not
under the frame relation (R4-A reports 595).

- The rich cache records the first firing's instant (`first_t`), not which trigger produced it, and nothing below
  invents it. In each environment where the automation fires, the frame of its first firing is *inferred* from what
  is recorded and from the cached rung-4 traces. A trigger is a *candidate* for a first firing at `t` when, after its
  `for` duration is taken off `t`:
  - *time*: one of its `at` times of day equals the local time of day of `t` within 1 s;
  - *time_pattern*: `t` matches its hours, minutes and seconds;
  - *sun*, and a `state` or `numeric_state` trigger on `sun.sun`: `t` is within 5 s of a sun reference instant it
    names (A6.2), or within 15 min for an elevation;
  - *state or numeric_state* on another entity: `t` is within 0.01 s of a change, in the engine's replay of the
    trace, of one of its entities (of the named attribute when there is one);
  - *template*: `t` is a change instant of an entity it reads, or a whole minute when it reads `now()`.
- A candidate trigger contributes its frame (A6.1; a trigger with no frame contributes "none").
- The first firing's frame is inferred when the candidates' frames are exactly one frame. It is *ambiguous* when they
  are more than one, and *uninferred* when there is no candidate (for example, an action delay).
- **frame_switch**: the inferred frames of an automation's first firings are not all equal over the environments
  where they are inferred (at least two).
- Reported:
  - how many of the population have triggers in more than one frame (only those can switch);
  - how many have every first firing inferred;
  - the frame_switch count (a lower bound when some firings are uninferred);
  - the switches by frame pair.
- *frame_switch* is registered as an outcome kind if at least 10 automations show it. It would be reported beside the
  relations' flags and is never part of the 199. Below 10, the count is reported and the kind is not registered.

**A8.5 The crossing-time sensed reference [13].** An option of the frame relation, off by default, so that the
relation as published (A6.3) is unchanged.

- *Which automations it applies to:* those whose bound triggers include a `numeric_state` trigger on a sensed entity
  (`env_kinds`), with a numeric `above` and/or `below` and no `value_template`.
- *The reference instant:* in each environment, the first crossing in the judged window (days 1 and 2) of any such
  trigger's entity (its attribute when one is named). The crossing is in the triggering direction, as the engine's
  replay of the cached trace holds it. A crossing is a report at which the previous numeric value lies outside the
  trigger's range and the new one inside it (range: above < v < below, with a missing bound open).
- *θ_sen:* the signed minutes from that instant to the first firing.
- *Invariance:* the sensed frame is invariant iff the range of θ_sen over the environments where the automation fires
  and a crossing exists is at most 180 min (fewer than two values: invariant), the same tolerance as the sun frame.
  Every other sensed frame stays non-computable, and so invariant as before.
- *Traces:* read through the sweep's own cache key (`trace-<Env.key>-rho_device.jsonl.gz`, one function shared with
  `record_trace`). No trace is recorded again.
- *Reported:*
  - how many automations, and how many of the 199 and of their sensed-frame flags, get a computable sensed frame;
  - the θ_sen spread distribution: min, quartiles, 90th percentile and max, and counts in 0, (0, 60], (60, 180],
    (180, 720] and > 720 min;
  - the frame relation's flag count with the option on. It can only add time_spread flags: those added are listed by
    frame combination.
- *What the added flags are,* by a rule fixed now:
  - *latency* when every θ_sen is >= 0 (the firing follows the first crossing by an environment-dependent delay),
    split by whether the automation has conditions;
  - *precedes* when some θ_sen < 0 (the first firing comes before the first crossing).

**A8.6 Humidity flags at rung 2.5 [16(d)].** The cached rung-2.5 sweep at `8fcd672` exists (R4-A).

- Population: the humidity-reading automations flagged at rung 4, under the clock relation and under the frame
  relation.
- Reported: how many are flagged at rung 2.5 too (under the same relation), how many with the same kinds, and how many
  name a porch sensor.

**A8.7 The dead-rule headline [15(b)].** R4-C sampled 100 of the 391 environment-coupled dead automations at `120f4ca`
(seed 20261104; 70 repositories) and labelled 6 genuinely dead.

- The population count is estimated as 391 x 6 / 100 (23.5). It gets three intervals, each stated with its method:
  1. Wilson 95 % on 6/100, times 391 (R4-C's "11-49");
  2. **the headline:** the exact finite-population interval. The set of K in 0..391 for which drawing 100 without
     replacement from 391 holding K gives P(X <= 6) >= 0.025 and P(X >= 6) >= 0.025 (the hypergeometric inversion);
  3. a repository-clustered percentile bootstrap of the share (70 repositories, 1,000 resamples, seed 0), times 391.
- Also reported: how many of the 100 and of the 6 are still environment-coupled dead at `8fcd672` (390).

**A8.8 The frame relation on the four home variants [16(h)].** A5.1's four variants are swept again at rung 4 at the
commit that implements this addendum, with the main run's settings: current binder, device reporting rule, the rich
cache and both relations.

- *Settings:* 48 environments; 4 workers under `nice -n 5`; one fresh work directory per variant, with only the
  `8fcd672` ERA5 cache copied in (no trace copied, never fetched again).
- *Check first:* the standard home, swept at this commit with the `8fcd672` rung-4 traces copied in, must reproduce
  the `8fcd672` rung-4 outcomes and both relations' flags exactly. If it does not, the cause is found before any
  variant number is used.
- *Reported, per variant:*
  - the run count;
  - the clock and frame flag counts;
  - how many of the 199 `8fcd672` frame flags are frame-flagged in the variant;
  - how many of the 199 are frame-flagged in all four variants, and in none.
- *Budget:* if the five sweeps would take more than about 2.5 h of wall time, the variants not run are listed and
  skipped.

**A8.9 Deviations and additions after the runs (2026-11-05).** Each was made after the numbers it concerns had been
seen. None changes a registered rule.

- *A8.8's check is not exact.* The standard home swept at `ba8a54c` gives the same flags as `8fcd672` under both
  relations, for every automation. It also gives the same yield. But 4 automations differ in one environment's outcome
  and 4 more in the rich cache only.
  - The 4 outcome differences are refusals by the engine's wall-clock template-compile budget (0.5 s), present in one
    run and not the other. They are load-dependent and none is flagged.
  - The 4 rich-only differences are the firing count (by one) or the branch set of automations that fire every
    minute or so.
  - The cause is found, so the variants are used, with this stated. The check's exactness criterion as written is
    not met.
- *Where the sweeps ran.* A8.8's sweeps ran at `ba8a54c`, the first commit of this addendum's implementation. The
  later commits change only `vesper.edt.ladder` (new functions) and `vesper.edt.oracle` (A8.5's option, off by
  default, and new functions), none of which a sweep calls with a non-default argument.
- *A8.3's "other" reasons are named.* Two sun exemptions fell under "other" and were named from what they read: a
  `sun.sun` state that is neither `above_horizon` nor `below_horizon`. The rule allows a named "other".
- *A8.5 adds no flag.* The option adds no automation to the 199. It does add `time_spread` to 8 of them, so A8.5's
  "those added" and its two categories are reported for these 8 kind changes.
- *A8.1: an unregistered diagnostic.* The held-out AUC of the eight-feature model (0.85) is well above A6.7's pooled
  out-of-fold AUC (0.78). To show why, two things are reported beside the registered numbers, labelled as not
  registered:
  - each A6.7 fold's AUC of its own model;
  - the split run the other way (trained on the held-out half, judged on the training half).

## Addendum 9 (2026-11-05, post hoc, after the third internal review; before any of its runs)

**Post hoc**: written after the third internal review asked for it (its items 3(a, b), 8(a-c), 9 and 16(a, b, e)) and
after every result it revisits was seen (revision R4-B, the environment validation, the daylight audit), but before
any run below. Nothing earlier changes; every number below is reported beside the earlier ones. Base: the rung-4 ERA5
sweeps at `120f4ca` (the clock relation) and `8fcd672` (Addendum 6's frame relation), their homes and recorded
weather (never fetched again), R2-c's setback population and R4-B's code paths. Script:
`scripts/revision/r6b_physics.py`; results: `results/edt/r6b-<commit7>.json` (aggregates and anonymous indices only)
and a section "Revision R6-B (Addendum 9)" of `results-edt.md`. No pass/fail criterion below is a new registered
verdict; where a registered criterion is applied again it is labelled "would pass / would fail (post hoc)", and every
registered verdict stands.

**A9.1 Two new knobs, bit-identical at their defaults** (set only through `vesper.env.indoor.knobs`; the pinned
fingerprint test is extended to cover them):

- `ACH` (default 0.5, the model as built): the air changes per hour of every room's ventilation term 0.34 ACH V. It
  was already a module constant; it becomes a named knob, so a trace recorded under another value is keyed by it.
- `DIRECT_SHARE` (default 0.8): the coefficient of the beam share of horizontal irradiance, direct = DIRECT_SHARE x
  (1 - cloud), in `vesper.env.sun.direct_fraction`. It sets the beam/diffuse split of both the solar heat and the
  daylight (the outdoor illuminance itself is unchanged). It lives in `vesper.env.sun` and is set through the same
  `indoor.knobs`.

**A9.2 Item 8(a): infiltration in the setback corner budget (closed loop).**

- The H breakdown of the standard home (the EDT home of `120f4ca`): per indoor room and in total, U A of the outside
  walls, U A of the glass and 0.34 ACH V of the ventilation, in W/K and as shares of the total, at ACH 0.5 (and the
  ventilation term at 0.3 and 1.0). Interior partitions move heat between rooms, not out of the house, and are
  reported apart.
- Runs: R2-c's 22 setback automations, each alone, closed loop, against the same setting's baseline, R2-c's causal
  rule, exactly as A7.2. ACH in {0.3, 0.5, 1.0} at the nominal setting of everything else (gains 0, roof and ground
  off, warm-up 1 day, 165 kJ/(m² K)), (a) over R2-c's four core environments (Minneapolis and Stockholm, 2026-01-14,
  both orientations; denominator 22 x 4 = 88 runs) and (b) over all 48 environments (denominator 22 x 48 = 1,056
  runs; here every environment is run, nothing is counted as no breach). Then (c) ACH x the R4-B worst corner
  (G0-Fon-Wpss-C80, the unique worst over all 48) and best corner (G5-Foff-W1-C370), over the core (88 runs each).
- Reported per setting: breaching runs over the denominator, caused cells, automations, per city, the coldest caused
  room per run (min / median / max) and the baseline's coldest room. Check: ACH 0.5 at the nominal setting reproduces
  R2-c's 53 breaching runs of 88 exactly, and over all 48 environments gives 53 of 1,056 if A7.2's "outside E no
  breach" held at the nominal corner (reported either way).

**A9.3 Item 8(b, c): home A free-floating.** Home A's check heats the standard room to the measured room's 10th
percentile (the registered proxy). An hour h of one of A's eight rooms is **evidently free-floating** when its measured
hourly mean is below the proxy setpoint minus 1 K and falling: T(h) < s - 1 K and T(h) < T(h - 1), both hours measured.
An episode is a maximal run of consecutive such hours. Each episode is simulated without heating: the standard room,
driven exactly as the registered check (A's measured outdoor temperature and humidity, ERA5's irradiance and cloud),
starts the episode's first hour at the measured T(h0 - 1) and is stepped through the episode (600 s steps, hourly
means). Reported per room and as the median over rooms (T's statistic): the free-float RMSE and the share of hours
within 2 K over the pooled episode hours, with n hours and n episodes; beside them (i) persistence (T(h0 - 1) held
through the episode) and (ii) the registered heated simulation on the same hours. Rooms with fewer than 24
free-floating hours are reported but left out of the median. Sensitivity (secondary): the same with no 1 K margin
(T(h) < s and falling). T's registered criterion (median RMSE <= 1.5 C, median within-2 C >= 0.7) is applied as
"would pass / would fail (post hoc)". For home B, the difference is quantified: the share of the hours the check uses
in which the registered simulation's heater runs, A against B, and the share of measured hours below the proxy.
Item 8(c): which geometry each check drives is recorded from the code (`STANDARD_ROOM`).

**A9.4 Item 3(a, b): the aperture factor implied by home B's residual.** Home B as the validation code loads it
(`load_sml2010`, its proxy setpoints, ERA5 irradiance and cloud, measured outdoor air), the registered T simulation
with SOLAR_APERTURE_SCALE = k on a grid k = 0, 0.01, ..., 2.00, both rooms. The mean bias b(k) (simulated minus
measured) over the hours the check uses (both values present), pooled over the two rooms; **k̂ is the root of b(k)
= 0**, by linear interpolation between grid points (per room as well). Uncertainty: 2,000 bootstrap resamples of B's
local (Europe/Madrid) days with replacement (seed 20261105), k̂ recomputed from the same grid on each; the 2.5th and
97.5th percentiles. If b(0) > 0, k̂ is "below 0, not attainable" and k = 0 is used below. Cross-check from published
quantities: k̂_lin = 1 - b(1) H / Q̄, H the standard room's loss (W/K) and Q̄ its mean modelled solar gain over the
same hours. Stated: whether R4-B's x0.25-x2 bracket contains k̂.

- Then the open-loop propagation at k̂ (rounded to two decimals) with R4-B's aperture path (A7.3: the affected set,
  every trace recorded afresh in a cache of its own, replayed with the sweep's worker). **Clock set** at `120f4ca`:
  how many of the 129 orientation witnesses, the 128 temperature flags and the 37 humidity flags survive. **Frame set**
  at `8fcd672` (reporting rule `device`, as that sweep): the same counts under the frame relation (`frame_divergences`,
  with the first firings' instants from the replay), over that sweep's own affected set; the clock counts at
  `8fcd672` too. Each commit's reference (aperture x1, recorded afresh with the current code) must reproduce that
  sweep's published outcomes (and at `8fcd672` its frame flags) for the affected set exactly; reported. R4-B's own
  path unpacked the worker's older six-field result; the new script reads the current seven fields (the seventh, the
  richer outcome, carries the first firing instants the frame relation needs) and is otherwise R4-B's.
- If time allows (stated either way): the same at the bootstrap interval's two ends, and R4-B's aperture points x0.25,
  x0.5, x2 on the frame set.

**A9.5 Item 16(e): the direct-share coefficient.** DIRECT_SHARE in {0.6, 0.95} (0.8 the reference), by the same
open-loop path and on both sets as A9.4; reported as A9.4 (orientation witnesses, temperature and humidity flags kept,
flags lost and gained, outcomes changed).

**A9.6 Item 9: the daylight decomposition chain** (dataset B, check D's 240 hours: 10:00-16:00 local, measured
irradiance >= 300 W/m²; descriptive, the registered D "fail" stands). Start: R0 = VESPER's median average daylight
factor over D's 80 generated rooms / D's measured median (indoor over global horizontal illuminance). Each factor
multiplies the model's side, and the residual model/measured is reported after each:

1. *Global to diffuse under the measured sky.* A daylight factor is defined against the diffuse (sky) horizontal
   illuminance; D divides by the global. Primary: the hourly diffuse fraction kd by Erbs et al. (1982) from the
   clearness index kt = measured GHI / extraterrestrial horizontal irradiance at the hour's middle; the factor is the
   median over D's hours of kd (the model's side is DF x kd hour by hour, then the median). Secondary: the model's own
   kd = 1 - DIRECT_SHARE (1 - cloud), ERA5 cloud.
2. *Point sensor against the work-plane room average.* For the standard validation room (4 x 4 m, 2.5 m high, one
   1.2 x 1.2 m window, assumed sill 0.9 m, centred, work plane 0.85 m), the point daylight factor (sky component of the
   window under a uniform sky by the configuration factor of the rectangle, plus BRE's split-flux internal reflected
   component with R = 0.5 everywhere, no obstruction) at the room's centre and 0.5 m from the back wall, over the same
   room's average daylight factor with no obstruction (theta = 90). Central: the centre; range: the back to the centre.
3. *Framing and obstruction.* The BRE formula takes W as the glazed area: a frame factor 0.75 (range 0.7-0.8) on the
   model's window opening, a maintenance factor 0.9 (0.9-0.96) for dirt, and a visible sky angle of 65 deg (60-80)
   against the model's 80 deg; T = 0.68 itself kept. Central and range reported.

Also: what remains after all three, and the arithmetic of 14.8 against 14.67 (where each comes from).

**A9.7 Item 16(a): the sun at 0-5° elevation.** VESPER's sun (astral) against NREL SPA (pvlib), as the registered S,
restricted to the minutes where SPA's apparent elevation is above 0 and at most 5 deg, every 1 min, for the six EDT
cities on the three run days of the January environments (2026-01-14 to 16). Reported per city and pooled: n, mean
and 95th percentile and max absolute elevation, azimuth and position error. S's registered 0.05 deg bound was set above
5 deg; it is shown beside these as a reference, not applied.

**A9.8 Item 16(b): ERA5 against the nearest official station for home B.** Only if a free source with no login and no
captcha gives hourly station temperature for B's period (NOAA's ISD global-hourly CSV for Valencia airport, Manises,
USAF 082840 WBAN 99999, is the candidate): fetched once, politely, cached under `~/vesper-lab/edt/r6-B/`, never
committed. Station hours: the mean of the reports (quality code passed, temperature not missing) within each UTC hour
[h, h + 1), the dataset's own resampling. On B's hours with all three values: bias and RMSE (and r) of ERA5 against the
station, of the house sensor against the station, and of ERA5 against the house sensor (W's comparison, for the
reproduction). Otherwise: "not available", stated.

## Addendum 10 (2026-11-05, post hoc, after the third internal review; before any comparison run in its environments)

**Post hoc**: written after the third internal review (item 2: differentially test the automation engine against an
unmodified, pinned Home Assistant) and before any comparison run in the four environments below. Nothing earlier
changes: the published sweep (rung 4, 8fcd672, current binder, device reporting rule) stands, and this addendum's
numbers are reported beside it. Code: `vesper/validation/ha_diff.py` (VESPER's side and the analysis) and
`vesper/validation/ha_diff_driver.py` (Home Assistant's side; it imports nothing of VESPER). Results:
`results/edt/r6c-ha-diff-<commit7>.json` (aggregates and anonymous sample indices only) and a section "Revision R6-C
(Addendum 10)" of `results-edt.md`.

**A10.0 What was seen before this addendum (disclosed).** The harness was built and shaken down on one environment
that is not one of the four and is never reported: Phoenix, 2026-04-15, north 0 (rung 4, device rule), first on 40
and then on all 263 automations of the sample (A10.2). The shakedown changed the harness three times: (1) the home's
entity domains' own integrations (`light`, `switch`, `lock`, `climate`) are set up, so their services validate data
with Home Assistant's own schemas (a stub accepted a `brightness_pct` of 120 that the real `light.turn_on` refuses);
(2) a stub service asked for a response (`response_variable`) answers an empty one, not none; (3) the services that
would end or reload the harness's Home Assistant (`homeassistant.restart`, `stop`, `reload_*`; `automation.reload`)
are replaced by handlers that do nothing, so the call is recorded and the run goes on. The shakedown also showed, in
that environment, discrepancies of the engine itself that this addendum's classes (A10.6) would call engine bugs
(the `sun.sun` state is switched by a rule older than Home Assistant 2026.10.2's; a `kelvin` light parameter
2026.10.2 refuses) and HA semantics VESPER lacks (`initial_state: off`; the 50-500 ms offset of time triggers). Their
occurrence in the four environments is therefore a confirmation, not a discovery; it is still reported in full. Any
change to the harness after the first run in the four environments is a deviation, dated and reported.

**A10.1 Design.** Same inputs, two engines, one environment at a time.

- *The inputs.* The environment's cached trace (`work-8fcd672/cache`, the device rule; never recorded again).
  VESPER's engine runs once with no automation and writes down every entity state change it derives from the trace
  (the reports through the home's MQTT discovery) and every entity's start state: the open-loop input stream, the
  same whatever the automations do. Home Assistant receives exactly that stream (`hass.states.async_set` at the same
  logical second) for every entity except `sun.sun`, which is Home Assistant's own `sun` integration at the
  environment's site; an event entity's state is the time of its event, as Home Assistant's event entities have it.
  So the comparison is of the automation engines (and of VESPER's emulation of the `sun` entity), given identical
  entity states; VESPER's trace-to-state mapping is not under test.
- *VESPER's side.* `vesper.edt.replay.replay_detailed`, the sweep's own replay, unchanged, with the sample in one
  engine. Gate: for every (automation, environment) pair it must reproduce the published sweep's cached outcome
  (`fired`, `first_tod_h`, `flaps`, `night_light`, `night_cover`) and richer outcome (first firing, instant count,
  payload hash) exactly; a pair that does not is reported and excluded.
- *Home Assistant's side.* Home Assistant 2026.10.2 (`.vesper/ha/venv`, astral 2.2, unmodified) in-process, set up
  with its own bootstrap functions: the base functionality, the core integrations with the core config set to the
  environment's latitude, longitude, elevation and time zone, `sun`, the home domains' integrations (A10.0 (1)) and
  `automation` with its dependencies; the sample loaded as the sweep binds it (the bound YAML, unmodified but for the
  `alias`, which is the replay's position label, as in VESPER's replay). The clock is virtual: the event loop's
  `time()`, `time.time`, `dt_util.utcnow` and `dt_util.now` read one variable that only the driver moves, from the
  trace's local midnight, to the next timer the loop holds or the next state change, so every timer fires at exactly
  its scheduled instant. Home Assistant's random sub-second offset of time triggers is drawn from a seeded generator
  (seed 20261105). Service calls are recorded by a subclass of Home Assistant's service registry and executed by
  nothing that changes a state (the replay is open loop on both sides); a service no loaded integration provides is a
  stub that does nothing. A call its service refuses (a schema error) is not a firing.
- *Why not a container with a wall clock* (the review's wording): a trace spans three days; replaying it in real
  time would take three days per environment, and a speeded-up wall clock is what Home Assistant's own tests replace
  with exactly this kind of frozen, stepped clock. In-process, Home Assistant's own automation, trigger, condition,
  template, script and `sun` code runs unmodified and deterministically. Port 8123 is never used; nothing listens on
  any port.
- *What is compared*, per (automation, environment), over the judged window (days 1 and 2, as the oracle judges): a
  firing is one action call on one target, as VESPER's `Firing` (Home Assistant's `homeassistant.turn_on/off/toggle`
  counts as the domain calls it makes, as VESPER records it; a call is attributed to the automation whose context
  made it). Fired or not; the first firing's time, Δ = VESPER minus Home Assistant, in seconds; the set of (service,
  target) pairs; the number of distinct firing instants; a light's requested brightness (0..255) where both engines
  request one. The oracle's outcome (`vesper.edt.oracle.outcome`) is computed from each engine's firings.

**A10.2 The sample.** Eligible: the automations that run at rung 4 with an outcome in every one of the four
environments (5,926). Each is assigned to the first stratum whose rule it meets, in this order, and each stratum's
size is drawn with seed 20261105 (`ha_diff.draw_sample`):

| Stratum | Rule | Size |
|---|---|---|
| time_spread frame flags | `time_spread` among its frame divergences | all (8) |
| frame flags | any frame divergence | 40 of 191 |
| sun_offset | a sun trigger with a non-zero offset | 30 |
| numeric_state | a numeric_state trigger | 30 |
| time_pattern | a time_pattern trigger | 25 |
| template | a template trigger, a template condition, a value_template | 30 |
| for | a trigger `for:` | 30 |
| choose | `choose` or `if` in the actions | 25 |
| sun_other | another sun trigger, a state or numeric_state trigger on `sun.sun`, a sun condition | 25 |
| other | none of the above | 20 |

In the strata after the frame flags, three quarters of each draw come from automations VESPER fires in at least one
of the four environments and one quarter from those it fires in none (a short side is filled from the other). Total
263 automations, 1,052 (automation, environment) pairs: the denominators of everything below. Per-trigger-kind
results count an automation under every feature it has (`ha_diff.features`), as well as by stratum.

**A10.3 Environments.** Minneapolis 2026-01-14 north 0; Stockholm 2026-07-15 north 180 (59.3° N: the night has no
astronomical dusk); Sydney 2026-01-14 north 0 (southern summer, daylight saving); Sydney 2026-07-15 north 180. January
and July, both hemispheres, a high latitude; the first two are the sweep's best environment pair.

**A10.4 Measures.**

1. The firing-set symmetric difference: pairs where exactly one engine fires (VESPER only, Home Assistant only), and
   the automations with at least one such pair; Home Assistant refusing to load an automation counts as not firing
   and is its own row.
2. The first-firing difference Δ over the pairs where both fire: its distribution (quantiles, the share within 1 s,
   60 s, 3 h) per trigger-kind feature and per stratum.
3. Payload agreement over the pairs where both fire: equal (service, target) sets; equal instant counts; equal
   brightness where comparable (counts and shares).
4. A pair is *discrepant* if exactly one fires, or |Δ| ≥ 1 s, or the call sets, the instant counts or the
   brightness differ. A pair whose only difference is |Δ| < 1 s is counted apart (sub-second).

**A10.5 The flags (the 199 frame flags, the 8 time_spread frame flags; the 790 clock flags beside them).**

- (a) *Direct substitution* for the sampled flags (the 8 and the 40): Home Assistant's outcome and first firing in
  place of VESPER's in each of the four environments, the sweep's in the other 44, the flag recomputed under both
  relations exactly as the sweep computes it (envelope kinds static in every environment removed); a flag changes if
  its set of kinds changes. Check: with VESPER's own outcomes the recomputation reproduces the sweep's flags.
- (b) *Propagation* to every one of the 199 (and the 790): 1,000 Monte Carlo replicates (seed 20261105). For each
  flag and each of its 48 environments, independently, one observed pair is drawn among the pairs with the same
  VESPER firing status: if VESPER fires there and Home Assistant did not in the drawn pair, the environment's outcome
  becomes not fired; if both fired, its first firing moves by -Δ; if VESPER does not fire there and Home Assistant
  did in the drawn pair, the outcome becomes fired at a first time of day drawn from Home Assistant's observed ones
  on such pairs. Envelope kinds are kept. Reported: the number of flags that change kinds (mean, 2.5 and 97.5
  percentiles), and how many flags ever change. Primary: the pairs classified harness artifacts (A10.6) count as
  agreeing; secondary: every pair as observed.
- (c) *Margin*: how many flags have a time_spread decision that moving every first firing by at most m could change
  (in some judged frame the spread lies within 2m of the 3 h tolerance), for m = the 99th percentile and the maximum
  of |Δ| over the jointly firing, non-artifact pairs.

**A10.6 Classes.** Every discrepant pair is classified by its root cause, read from both engines' calls and Home
Assistant's log, into: *engine bug* (a construct VESPER's engine documents as supported with Home Assistant's
semantics, run differently from 2026.10.2); *HA semantics VESPER lacks* (a construct VESPER accepts but does not model,
or a documented approximation: an ignored key, permissive mode, Home Assistant's sub-second offsets); *harness
artifact* (the harness, not either engine: a stub service's response or effect, an attribute the input stream does
not carry); *unexplained*. Each class is reported with its pairs and automations and the rule used to assign it.

**A10.7 Criterion (fixed now).** VESPER's engine agrees with Home Assistant on this sample if, with the pairs
classified harness artifacts counted as agreeing: (1) the firing-set symmetric difference is at most 2 % of the
pairs (at most 21 of 1,052); (2) at least 95 % of the jointly firing pairs have |Δ| < 60 s; (3) direct substitution
changes none of the 8 time_spread flags, and the 97.5th percentile of changed frame flags in (b) is at most 5 % of
199 (at most 9). Otherwise it fails and is reported as failing; the paper reports the published sweep and this
analysis beside it.

**A10.8 Engine bugs.** A real engine bug gets a failing test and a fix; the sweeps are not re-run. Reported for each:
the run automations of the sweep that use the construct (a static count), and, as a secondary analysis, the
comparison of A10.4 repeated with the fixed engine on the same sample and environments.

**Deviations (2026-11-05, after the run in the four environments at 134b209; nothing above is changed):**

- *The brightness comparison.* A10.4(3) read Home Assistant's `brightness_pct: 0` as a brightness of 0, which Home
  Assistant's light integration turns into a turn-off and VESPER records as one; one automation's four pairs differed
  only in this (checked by hand in Minneapolis; the same template in the other three). The comparison now reads a brightness of 0 as none (533ceb1), and A10.6 gained the
  rule that names it a harness artifact. The per-pair records were not re-run; the rule classifies them.
- *The engine fix took two commits.* 870125e (the `sun.sun` state rule and update instant, `kelvin`/`color_temp`); the
  first fixed-engine comparison still showed the `sun.sun` position updates off by one interval in twilight, from two
  wrong phase intervals; 3dcd4e7 fixes them. The secondary analysis reported is the comparison at 3dcd4e7.
- *Propagation.* A10.5(b) as registered is reported. In it, a not-fired outcome has no envelope kind (clarification:
  it cannot flap without firing). Added post hoc, beside it and not part of A10.7: the same propagation with draws by
  automation (each flag draws one sampled automation and every environment draws from that automation's own pairs),
  because the observed one-engine-only pairs are automation-level (an automation fires in no environment in Home
  Assistant), which per-environment independent draws scatter over a flag's 48 environments.

## Addendum 11 (2026-11-05, post hoc, after Addendum 10's results; before its run)

**Post hoc**: written after Addendum 10's differential test found two engine defects, fixed with failing tests first
(`sun.sun` as Home Assistant 2026.10.2 has it, and its updates at the twilight phase intervals; `kelvin` and
`color_temp` refused by 2026.10.2's light schema). It is written before the run below. Nothing earlier changes: the
sweep at `8fcd672` stays the paper's primary result, every Addendum 8-10 analysis rests on it, and this addendum adds
one engine-corrected sweep reported beside it.

**A11.1 The run.**
- **Code:** the commit that adds this addendum, which carries both fixes.
- **Sweep:** one rung-4 sweep. Same corpus, standard home and 48 environments as `8fcd672`'s main run. Current binder,
  device reporting rule.
- **Weather:** the ERA5 recording copied from `work-120f4ca` (never fetched again).
- **Traces:** re-recorded in a fresh work directory, as Addendum 6 did.
- **Resources:** 5 workers, under nice. Script `~/vesper-lab/edt/run_r6d.sh`.
- **Output:** `sweep-rung4-r6d-<commit7>.json` beside the others.

**A11.2 Reported, beside `8fcd672`'s main run.**
- Bound and run counts.
- Flags under the clock relation and under the frame relation, by kind. Negative control.
- How many of `8fcd672`'s 199 frame flags hold (flagged with the same kinds; flagged with other kinds), how many are
  lost, and how many are new. The same for the 8 `time_spread` flags.
- The never-firing (dead) count among run automations.
- For every lost or new frame flag: whether the automation reads `sun.sun` or uses `kelvin`/`color_temp` (the defects'
  reach). Anything else counts as "other".

No pass/fail criterion. If the run disagrees with `8fcd672` beyond the two defects' reach, that is reported as a
finding about the engine, not explained away.

## Addendum 15 (2026-11-05, post hoc, after the fourth internal review; before any comparison run of its stratum)

**Post hoc**: written after the fourth internal review, before its runs (item 8(a): the engine differential test
does not exercise `mode`, `max`, `max_exceeded`, `initial_state`, `delay` or `wait_for_trigger`), and before any
comparison run of the stratum below. Nothing earlier changes: Addendum 10's 263 automations, its criterion and its
results stand, and this addendum's numbers are reported beside them, never pooled with them. Code:
`vesper/validation/ha_semantics.py` (the stratum, the reading run, the analysis), on Addendum 10's harness
(`ha_diff.run_env`, `ha_diff_driver.py`), unchanged but for three additions that are inert at their defaults: the
per-pair firing count, the count of Home Assistant's "Already running" log lines, and an optional transform of what
Home Assistant is given. Results: `results/edt/r7c-ha-diff-<commit7>.json` (aggregates and anonymous sample indices
only) and a section "Revision R7-C (Addendum 15)" of `results-edt.md`.

**A15.0 What was seen before this addendum (disclosed).** Static counts only, on the two sweeps' bound automations
(no engine was run): the pool sizes of A15.2; that the engine-corrected sweep (`r6d-5b54586`) has the same 199 frame
flags as `8fcd672`; that `ha_diff.draw_sample` on `8fcd672` reproduces Addendum 10's 263 exactly; and the reach
counts of A15.8 (among the 5,926 run automations: 1,553 carry a construct of the stratum; 330 a mode other than
`single`, of which 2 are frame flags, both `restart`; 48 of the 199 frame flags carry a construct, 2 of them
time_spread). No comparison with Home Assistant has been run on any automation of this stratum.

**A15.1 Design.** Addendum 10's design (A10.1), with three changes.

- *The engine and the sweep.* VESPER's side is the engine at this addendum's commit. That is the engine of the
  engine-corrected sweep (`r6d-5b54586`, Addendum 11, which carries Addendum 10's two fixes): no engine code changed
  between `5b54586` and this commit. The gate is that sweep: VESPER's replay must reproduce its cached outcome and
  richer outcome in every pair; a pair that does not is reported and excluded. The inputs are that sweep's cached
  device-rule traces (`work-r6d-5b54586/cache`, never recorded again). The published engine (`8fcd672`) differs from
  this one only in the two defects Addendum 10 fixed; their reach within this stratum is reported.
- *What VESPER's engine does with the constructs*, stated from `vesper.lab.automations` and reported:
  - `mode: single` runs as Home Assistant runs it: conditions are checked first, then a trigger that arrives during
    a run is dropped.
  - In the replay (permissive), `restart`, `queued` and `parallel` run as `single` and are listed as an
    approximation; strict mode refuses them.
  - `max` and `max_exceeded` are accepted and ignored.
  - `initial_state` is accepted and ignored: the automation always starts enabled.
  - `delay` holds the run, which stays "running" for `single`. Only a fixed duration runs; a templated delay is
    refused, so none is replayed.
  - `wait_for_trigger` holds the run until one of its triggers fires or its timeout ends; `continue_on_timeout`
    defaults to true.

  The run tests each statement.
- *The reading run* (diagnostic). Home Assistant runs the stratum twice per environment: as bound (the comparison),
  and given VESPER's reading of each automation, the bound automation without `mode`, `max`, `max_exceeded` and
  `initial_state` (`ha_semantics.vesper_reading`). VESPER's side is the same in both. The reading run is not a second
  comparison; it only classifies (A15.6). Home Assistant's seeded sub-second offsets of time triggers may fall
  differently in the reading run, because an automation that `initial_state: off` keeps from starting draws no
  offset in the comparison run.

What is compared per pair is A10.1's, plus the firing count (the number of firings, one per call and target, besides
A10.4's distinct instants) and each of the oracle's five outcome components (fired; the first firing's time of day,
equal within 1 s; flaps; night_light; night_cover).

**A15.2 The stratum.** Eligible: the run automations of `r6d-5b54586` with an outcome in each of Addendum 10's four
environments that carry a construct: a `delay` or a `wait_for_trigger` anywhere in the actions, a `mode` other than
`single`, or an `initial_state` (`ha_semantics.carries`; `max` and `max_exceeded` alone do not count). Each is
assigned to the first sub-stratum whose rule it meets, in this order. The sizes are drawn with seed 20261105
(`ha_semantics.draw_semantic_sample`), from automations not in Addendum 10's 263, except the frame flags, which are
all taken:

| Sub-stratum | Rule | Pool | Size |
|---|---|---|---|
| frame_flag | one of the 199 frame flags | 48 (13 of them in the 263) | all (48) |
| mode | `restart`, `queued` or `parallel` | 315 (33 fire in the four) | 70 |
| wait_for_trigger | a `wait_for_trigger` | 19 (2 fire) | all (19) |
| delay | a `delay` | 330 (93 fire) | 60 |
| initial_state_off | `initial_state` false/off | 71 (24 fire) | 20 |
| initial_state_on | `initial_state` true/on only | 721 (253 fire) | 10 |

As in A10.2, three quarters of each drawn sub-stratum come from automations VESPER fires in at least one of the four
environments and a quarter from those it fires in none, a short side filled from the other (so the mode sub-stratum
takes all 33 that fire). Total **227 automations, 908 automation-environment pairs**: the denominators of everything
below. Per-construct results count an automation under every construct it carries, besides by sub-stratum. The 13
frame flags also in the 263 are reported within the stratum and, apart, the stratum without them (214 automations,
856 pairs).

**A15.3 Environments.** Addendum 10's four (A10.3). A shakedown on Addendum 10's shakedown environment (Phoenix
2026-04-15 north 0, never reported) precedes the comparison runs. A harness change after the first comparison run is
a dated deviation.

**A15.4 Measures.** A10.4's, over the stratum's 908 pairs, reported for the stratum, per sub-stratum and per
construct, never pooled with the 263:

1. Pairs where exactly one engine fires (VESPER only, Home Assistant only), and their automations. Home Assistant
   refusing to load an automation counts as not firing and is its own row.
2. The first-firing difference Δ (VESPER minus Home Assistant) over the jointly firing pairs: quantiles, and the
   shares within 1 s, 60 s and 3 h.
3. Firing counts over the jointly firing pairs: equal distinct instants, equal number of firings, and the direction
   of a difference (VESPER fewer or more). Also the pairs where Home Assistant logged a trigger it dropped because
   the automation was already running (mode `single`'s "Already running").
4. Payloads over the jointly firing pairs: equal (service, target) sets; equal brightness where comparable.
5. The oracle's outcome components over all 908 pairs: how many agree, per component; the pairs each engine flags as
   flapping.
6. A discrepant pair is A10.4(4)'s: exactly one fires, or |Δ| >= 1 s, or the call sets, the instant counts or the
   brightness differ. A pair whose only difference is |Δ| < 1 s is counted apart.
7. Beside the stratum, descriptive: the reading run's own measures 1-6.

**A15.5 The flags.** Over the 48 frame flags that carry a construct, the 2 time_spread flags among them, and the
clock flags that carry one. The other 151 frame flags carry none, so this stratum says nothing about them.

- (a) *Direct substitution* (A10.5(a)) for the 48: Home Assistant's outcome and first firing in place of VESPER's in
  the four environments, the sweep's in the other 44; a flag changes if its set of kinds changes.
- (b) *Propagation* (A10.5(b), 1,000 replicates, seed 20261105), with one change. The primary propagation draws by
  automation: each flag draws one sampled automation that fires somewhere, and every environment draws from that
  automation's own pairs. Addendum 10 showed that its registered per-environment draws scatter an automation-level
  difference over 48 environments, and the constructs here (`initial_state: off`, a mode) act per automation. Beside
  it (secondary): the per-environment propagation, and the by-automation propagation with every pair as observed.
  Primary: harness artifacts count as agreeing.
- (c) *Margin* (A10.5(c)) for the 48 and the 2, with m the largest |Δ| of the non-artifact jointly firing pairs.

**A15.6 Classes.** A10.6's four classes. Every discrepant pair is assigned by these rules, in order:

1. If a fixed engine exists (A15.8) and the pair agrees with it: *engine bug*, the defect named.
2. If the pair agrees in the reading run: *HA semantics VESPER lacks*, named by the key the automation carries (its
   mode, run as single; `initial_state: off`; otherwise the other ignored keys).
3. A10.6's rules as Addendum 10 applies them: `initial_state: off` with VESPER firing and Home Assistant not (HA
   semantics); a stub service's empty response, and Home Assistant's brightness 0 read as a brightness (harness
   artifacts); the 50-500 ms offset of time triggers (HA semantics), only where the first firings differ by more
   than 0 and less than 1 s.
4. Otherwise *unexplained*. Each unexplained pair is read by hand from both engines' calls and Home Assistant's log.
   A difference that is VESPER's engine running a construct it documents as supported (A15.1) differently from
   2026.10.2 is an engine-bug candidate, handled by A15.8. Any other cause is reported as found and stays
   unexplained in the counts unless a rule above covers it.

**A15.7 Criterion (fixed now; Addendum 10's four parts over this stratum).** VESPER's engine agrees with Home
Assistant on the semantic stratum if, with the pairs classified harness artifacts counted as agreeing:

1. the pairs where exactly one engine fires are at most 2 % of the stratum's pairs (at most 18 of 908);
2. at least 95 % of the jointly firing pairs have |Δ| < 60 s;
3. (a) direct substitution changes none of the stratum's time_spread frame flags (2); (b) the 97.5th percentile of
   the frame flags whose kinds change in the primary propagation (A15.5(b), over the 48) is at most 9 (5 % of 199, as
   in A10.7).

Otherwise it fails and is reported as failing. Pairs classified "HA semantics VESPER lacks" count as discrepant: the
criterion measures the engine as it runs the sweeps.

**A15.8 Engine defects.** A difference that is VESPER's engine running a documented-as-supported construct
differently from Home Assistant 2026.10.2 gets a failing test and a fix; the sweeps are not re-run. Reported for
each: its reach among the 5,926 run automations, the 199 frame flags and the clock flags (a static count); and, as a
secondary analysis, this addendum's comparison repeated with the fixed engine on the same stratum and environments
(its gate then reports the pairs the fix changes). "HA semantics VESPER lacks" (a mode run as single, `initial_state`
ignored) is not fixed here; its reach is reported the same way.

**A15.9 Beside the stratum (descriptive).** Addendum 10's 263 already held 62 automations that carry a construct
(`ha_semantics.carries` on `8fcd672`). Their agreement in Addendum 10's fixed-engine records (`3dcd4e7`) is tabulated
with the same measures, for completeness.

**A15.10 Resources.** At most 2 processes at a time, under `nice -n 5`; each runs one environment's VESPER replay,
then its Home Assistant run. Home Assistant runs in-process on the virtual clock, as in Addendum 10; no port is
opened.

**A15.11 After the shakedown, before any comparison run (2026-11-05).** The shakedown (A15.3: Phoenix 2026-04-15,
the stratum's 227 automations, comparison and reading runs, at `f556117`; never reported) ran without error, and
VESPER's side reproduced the sweep in all 227 pairs. It showed three things, which change the harness and the rules
as follows before the first comparison run. Nothing above is otherwise changed.

1. *Harness: duplicate ids.* Two sampled automations (different files) carry the same `id`. Home Assistant's entity
   registry then creates only the first, and the other is "unavailable" in every run. From now on the harness gives
   Home Assistant a duplicate `id` with a suffix that makes it unique (`ha_diff.run_env`). The `id` is only the
   automation's unique id in Home Assistant; nothing an automation does depends on it. Addendum 10's 263 hold no
   duplicate id, so its runs are unchanged.
2. *Rule: data VESPER does not render.* Permissive replay records a call to a service the home does not have (a
   script, a notifier) without rendering its data. Home Assistant renders it, and a template error there stops its
   run (in the shakedown, a template that picks at random from a group attribute the input stream does not carry).
   Rule 3 of A15.6 gains, first: Home Assistant's log for the pair reports an error rendering a data template, and
   every call that only VESPER made is to a service outside the home's four entity domains (`light`, `switch`,
   `lock`, `climate`). The pair is then *HA semantics VESPER lacks*, "permissive replay records a call without
   rendering its data".
3. *A sun condition's boundary (disclosed).* One `restart` automation, triggered by a sun event with an offset,
   tests a sun condition `before` the same event with the same offset. At that instant Home Assistant 2026.10.2's
   sun condition holds (it fails only *after* `before`; `components/sun/condition.py`), and VESPER's does not (it
   holds only strictly before). So the two take different branches. This is an engine-bug candidate of A15.6(4)
   in a construct outside the stratum's six (the `sun` condition, documented as supported). It is not fixed before
   the comparison runs. If the four environments show it, it is handled by A15.8 like any other defect, and its
   occurrence there is a confirmation, not a discovery.

## Addendum 16 (2026-11-05, post hoc, after Addendum 15's results; before its run)

**Post hoc**: written after Addendum 15's differential test, whose two failing parts trace to one cause. VESPER
replayed automations with `initial_state: off` as if enabled; Home Assistant starts them disabled. Written before the
run below. Two changes are made, each with a failing test first: the sun condition's `before` boundary (R7-C,
`e6e3355`), and such automations are now refused at binding as "disabled at startup (initial_state: off)", because
nothing in a single automation's replay enables them. They are neither run nor counted as dead rules. Nothing earlier
changes. The `8fcd672` sweep stays primary. R6-D (Addendum 11) is the engine-corrected comparison this run extends.

**A16.1 The run.** As A11.1:
- one rung-4 sweep at the commit that adds this addendum;
- current binder, device reporting rule;
- the recorded ERA5 weather, fresh traces;
- 3 workers, under nice;
- script `~/vesper-lab/edt/run_r6d.sh` with this commit's worktree, output `sweep-rung4-r6d-<commit7>.json`.

**A16.2 Reported**, beside `8fcd672` and R6-D (`5b54586`), with `scripts/revision/r6d_compare.py`:
- bound and run counts, and how many automations the new refusal removes;
- frame and clock flags by kind, with held, lost and new against `8fcd672`;
- the negative control;
- environment-coupled never-firing count.

Every lost frame flag is checked for `initial_state: off` (R7-C counted 3 among the 199) and for the `sun` condition
`before` boundary (R7-C: 0). No pass/fail criterion.

**A16.3 Deviation (2026-11-05, after A16's run, before a second run).** A16's sweep at `66b93f7` gave one new frame
flag. The automation is a template trigger reading `now()` and a switch's `last_updated`. It did not fire in one
environment (Singapore, 2026-10-14, north 180°), where the published sweep and R6-D had it fire. No engine code that it
uses changed between the two sweeps.

The cause is the template budget. It was 0.5 s of wall-clock time, and a render failing it silently fails its
trigger. The sweep ran while other lanes loaded the machine. R6-A had reported the same kind of load-dependent
change (A8.9).

The fix: the budget counts the thread's CPU time, with a failing test first. A second sweep then runs at the fix's
commit, exactly as A16.1 (3 workers). Both runs are reported. The second one is the engine-corrected result.

## Addendum 12 (2026-11-05, post hoc: written after the fourth internal review, before its runs)

**Post hoc**: written after the fourth internal review asked for these analyses and after every result they revisit
was seen (revisions R4-A to R6-D in `docs/experiments/results-edt.md`), but before any number below is computed or
run. Nothing earlier changes, and every earlier number stands; what follows is reported beside it. A change made after
a run is dated at the end of this addendum and called a deviation. Review 4's item numbers are in brackets.

Base: the rung-4 sweeps `120f4ca` ($S_R$, the registered sweep) and `8fcd672` ($S_6$, the paper's primary sweep;
current binder, device reporting rule), their rich outcome caches and traces; the open-loop replays of R4-B
(`r4b-scratch`) and R6-B (`r6-B/scratch`); the engine-corrected sweep `5b54586` (R6-D); the R4-C audit labels
(`audit-r4c/labels-{flagged,dead}.tsv`); R6-C's pair records. Script: `scripts/revision/r7a_analyses.py`. Results:
`results/edt/r7a-<commit7>.json` (aggregates and anonymous indices only: no automation text, alias, entity name,
repository or hash) and a section "Revision R7-A (Addendum 12)" of `results-edt.md`. At most 3 worker processes, under
nice. "Frame flags" are $S_6$'s 199 under the frame relation and "clock flags" its 790 under the clock relation, unless
a sweep is named.

**A12.1 What an orientation witness is, and the three populations [1(b)].** From the code:

- *Pair.* Two environments of one sweep that differ in exactly one axis (`vesper.edt.cover._axis`). An *orientation
  pair* differs only in plan north (same city, date and rung).
- *Witness under a relation.* A pair on which the relation, restricted to the two environments, finds a divergence:
  `oracle.frame_restricted_divergences` for the frame relation, `restricted_divergences` for the clock relation (the
  rule of `scripts/edt_analysis/r4a_analyses.py`, `witnesses`). An automation is **orientation-witnessed under a
  relation** iff it is flagged under that relation and some orientation pair is a witness under it.
- *Outcome-class witness.* R4-B and R6-B (`r4b_error_budget.affected`) used another rule: a clock flag with an
  orientation pair whose outcome classes differ (`cover._differ`: fired, first time of day beyond 3 h, or an envelope
  kind), whatever the relation.

Reported: the size of each population on $S_R$ and $S_6$ (the expected values are §V-B's 100, R4-B's 129 and R6-B's
123), and their overlaps on $S_6$: the frame relation's set against the two clock-relation sets, with how many of the
frame relation's set are not clock flags.

*The aperture propagation, re-derived on the frame relation's own set.* The population is $S_6$'s frame flags that are
orientation-witnessed under the frame relation. Each aperture setting already run on the frame set (R6-B: x0.21,
x0.25, x0.29, x0.5, x2; the reference x1) is replayed on that setting's cached traces: R6-B's caches, hard-linked into
this lane's scratch. A trace is never re-recorded unless its header disagrees, and that is reported. The replayed set is
R6-B's affected set (A9.4) united with all 199 frame flags. Reported per setting:

- how many of the population keep a frame-relation orientation witness, split by what they read as written
  (temperature, humidity, neither);
- how many automations of the replayed set gain one;
- the temperature and humidity frame flags kept.

The reference x1 must reproduce the published frame relation for every replayed automation. This is a check; a
mismatch is reported, not repaired.

**A12.2 Bookkeeping reconciliations [1(d), 1(f), 8(c)].** Computed per automation from the sweep files; aggregates
reported.

- *790 against 797.* The clock flags of $S_6$ against those of $S_R$: kept, lost and new. For every lost or new flag,
  the cause: refused by the binder at $S_6$; bound differently; run differently; other.
- *390 against 391.* The environment-coupled run automations that fire in no environment, at $S_6$ and at $S_R$: the
  same decomposition.
- *Bound counts.* $S_R$'s bound count (expected 9,524 = 457,152 / 48) against $S_6$'s 9,515. The automations bound at
  one and not at the other, by cause, and how many of those bound at both have a different binding (the "rebind").
- *Engine refusals.*
  - At $S_6$: bound minus run; the automations refused in every environment and those refused in some only
    (`partially_refused`), so that bound = run + refused-everywhere is checked.
  - The refusal reasons, one per automation: its first refusal reason over the environments in key order
    (`sweep.reason_class`). They go into the six named classes ("it has no trigger"; `trigger event is not supported`,
    with any parenthesis; `trigger mqtt`; `trigger webhook`; `trigger tag`; `'blueprint' is not supported`) and an
    **other** line holding every remaining reason, with the other line's five largest reason classes.
  - The same table at $S_R$, so that 3,590 is closed.

**A12.3 The discrepant pair [8(b)].** R6-C's rule (`ha_diff.discrepant`): a pair is discrepant iff any of these holds:

- exactly one engine fires;
- both fire and |Δ| >= 1 s;
- the call sets, the distinct-instant counts or the compared brightness differ;
- Home Assistant could not load the automation.

Reported on the published-engine run's 1,052 pair records: the disjoint decomposition into one engine only; both fire
with |Δ| >= 1 s; and both fire with |Δ| < 1 s and a payload difference, by payload field. So 78 is rebuilt from 1,052 =
630 + 414 + 8, 576 within 1 s and 608 within 60 s. The 501 sub-second-only pairs are reported beside it.

**A12.4 Robust core and possible set [4].**

- *Definitions.* Take a bracket (a set of sampled settings of one term) and a finding family $F$ (a set of
  automations with a finding).
  - The **robust core** is the automations of the published $F$ that hold the finding at every sampled setting of
    the bracket.
  - The **possible set** is the union over the bracket's settings of the automations holding the finding at that
    setting, within the replayed set.
  - A finding is held as follows. An orientation witness: orientation-witnessed under the frame relation (A12.1). A
    temperature flag: flagged under the frame relation and reading a temperature by the names as written (`kinds`). A
    humidity flag: likewise for humidity.
- *Brackets, with their settings.* The nominal model is one of a bracket's settings where the bracket contains it.
  - aperture (R4-B): x0.25, x0.5, x1, x2;
  - outdoor air (R4-B): -2 K, 0, +2 K;
  - humidity class (R4-B): 1, 2, 3;
  - $\hat k$ (R6-B): x0.21, x0.25, x0.29 (the nominal x1 lies outside this bracket and is not in it);
  - direct share (R6-B): 0.6, 0.8, 0.95;
  - engine (R6-D): the published engine (`8fcd672`) and the corrected one (`5b54586`), as whole sweeps;
  - all of the above together: the intersection and the union over every setting sampled in any bracket.
- *Runs.* The outdoor-air and humidity-class settings were run only on the clock set (R4-B, `120f4ca`). They are run
  once on the frame set with R6-B's code path (`r6b_physics._trace_job`): the sweep's worker, the current seven-field
  result, reporting rule `device`, the `8fcd672` home and its cached weather. Traces are recorded afresh in this lane's
  scratch, one cache per setting, and the replayed set is A12.1's. The x1 reference replay of A12.1 is the check.
- *Reported:* per bracket and family, the published count, the robust core and the possible set, with the possible
  set's members outside the published family. The engine bracket's possible set is over all run automations.

**A12.5 A binder-discounted flag count [7].**

- *Population.* Of the 199 frame flags, those declaring a sensed frame (148, A8.3) are discounted; the 51 others stay
  as they are. The review's "44 sun-frame flags" are the flags reading the sun; those among the 148 are discounted like
  the rest, and the overlap is reported.
- *Labels.* An R4-C label (`labels-flagged.tsv`, keyed by the first 12 characters of the automation's hash) covers a
  flag if the flag's hash carries that key. Reported: how many of the 148 are covered, and by which label.
- *Primary estimate.* D = 51 + (the covered of the 148 not labelled `misbind`) + (the uncovered of the 148) x (1 - the
  mis-bind share among the covered). `uncertain` counts as not a mis-bind here, the review's 28.5 % basis. The
  sensitivity row counts `uncertain` as a mis-bind.
- *Interval.* A percentile bootstrap over repositories (1,000 resamples, numpy `default_rng(0)`): the repositories of
  the 148 are resampled with replacement and D is recomputed, the uncovered share taken from the resample's covered
  flags.
- *Beside it:*
  - the review's form, 51 + 148 x (1 - 51/179), with R4-C's repository-bootstrap ends of the mis-bind share
    ([18.8, 39.2] %);
  - D with only the wrong-medium mis-binds discounted (R4-C's classes C1-C4 and C6), since a wrong-room mis-bind (C5)
    is likely to survive in another room of the same home.

**A12.6 Non-default execution semantics among the flags [8(a), counting only].** Counted over the automations as
written (their raw configuration):

- a `mode` other than `single`;
- a `max`; a `max_exceeded`;
- an `initial_state` (and how many set it false or off);
- a `delay` step anywhere in the actions; a `wait_for_trigger` step anywhere in the actions;
- any of these.

Counted over the 199 frame flags, the 790 clock flags and the 5,926 run automations. Lane C runs the engine tests;
nothing here runs the engine.

**A12.7 The null-difference control [10(a)].** A new experiment, run once with the current code. That is the engine of
`5b54586`, so where a published sweep is needed for comparison it is R6-D's.

- *Setup.* The 9,515 automations of $S_6$, as bound there; the `8fcd672` standard home; reporting rule `device`; rung 4.
  The path is R6-B's open loop (the sweep's own worker), with every new site registered in each worker's site table at
  start. Dates: the four swept dates. North 0 for every environment.
- *Duplicated environment.* The three base sites (Minneapolis, Stockholm, Phoenix) at the four dates: 12
  environments. Each is recorded and replayed twice, in two fresh, separate caches (copies of the cached `120f4ca`
  ERA5 weather; no fetch), and each such twice-run environment is a pair of two grid points. **Expected flag rate: 0.**
  Reported, per relation: the automations flagged over the 12 pairs (the relation restricted to the pair) and the
  outcome and rich-outcome mismatches. Anything other than 0 is an engine non-determinism finding, reported with its
  cause.
- *Near-identical sites.* Each pair is the base site with a town in the same climate and time zone, at the same date
  and north; the coordinates are the towns' centres.
  - Primary pairs, the town 20-50 km away: Minneapolis with Anoka, MN (45.1977, -93.3872; 26 km); Stockholm with
    Södertälje (59.1955, 17.6253; 29 km); Phoenix with Mesa, AZ (33.4152, -111.8315; 23 km).
  - Secondary pairs, the review's named examples, outside 20-50 km: Minneapolis with St Paul (44.9537, -93.0900;
    14 km) and Stockholm with Uppsala (59.8586, 17.6389; 64 km).
  - A pair is 2 environments: the base site's (from the first duplicate cache) and the town's. 5 pairs x 4 dates = 20
    pairs.
- *Weather.* One ERA5 request per new site and date (the Open-Meteo archive, `models=era5`, through VESPER's
  fetcher, with a generic User-Agent and no e-mail). It is cached under `~/vesper-lab/edt/r7-A/`, with the request URLs
  recorded there. Nothing else is fetched, and no cached weather is fetched again.
- *Reported, per pair and pooled over the primary and over the secondary pairs, per relation (frame and clock):*
  - the automations flagged over the pair, of those with an outcome in both environments, and the same restricted to
    the environment-coupled ones;
  - the kinds;
  - the two sites' ERA5 cells and their mean absolute hourly difference of outdoor temperature over the run;
  - for scale, the same per-pair rate over R6-D's one-axis city pairs at the same dates and north (15 city pairs x 4
    dates).

  No pass/fail threshold. Unless the duplicate control fails, a flag over a near-identical pair is a real difference
  of recorded weather; each pooled flag is reported by kind and by what it reads.

**A12.8 Dead-reason precision [10(b)].** The mechanical reasons (A6.6, `r4a_analyses.dead_rules`) are re-derived at
$S_6$ for the 390 environment-coupled dead automations, by the same code. The check: the reason counts reproduce
R4-A's environment-coupled column. They are joined to R4-C's dead audit (100 of $S_R$'s 391; 99 are in $S_6$'s 390).
The auditor's classes are P (missing occupancy or presence), E (a needed event never happens), B (a binding or
standard-home artefact), S (a calendar date outside the swept dates), G1-G3 (genuinely dead: threshold unreachable,
condition unsatisfiable, armed crossing impossible) and X (other). A mechanical reason **matches** the diagnosis iff
the auditor's class is in its set:

| Mechanical reason | Matching auditor classes |
|---|---|
| trigger entities never change (no residents; open loop) | P, E |
| a condition never holds: on an entity that never changes | P, E |
| a condition never holds: a time condition | S, G2 |
| a condition never holds: a template or another condition | S, G2 |
| a condition never holds: a sensed-quantity or sun-state condition | B, G2 |
| a condition never holds: several conditions | not comparable (no single blocker) |
| threshold out of the observed range in every environment | B, G1 |
| never armed | B, G1, G3 |
| the actions end before any call | X |
| not derived | not comparable |

Reported: per mechanical reason, the audited automations and the matches (precision, Wilson 95 %), and the "not
derived" and "not comparable" shares of the 99. The class decides a match; the audit's notes are not used to invent a
diagnosis beyond it.

**A12.9 The flags in the not-environment-coupled block [11(a)].** These are the 15 frame flags and 19 clock flags of
$S_6$ whose automation reads no environment quantity by its names as written (`kinds` empty). Each is read locally,
its bound configuration and its outcomes (no text is committed), and put into one cause class:

- *L-sun:* a detector leak: it reads the sun (an attribute, a template or a form the name detector misses);
- *L-sensed:* a detector leak: it reads a sensed quantity the name detector misses (the binder put a sensed role under
  it, or it reads one through an attribute or a template);
- *L-actuator:* the environment reaches it through a state the home's environment rules move (a thermostat's action or
  running state, for instance), so the home couples it, not its names;
- *N-date:* a genuine non-environment cause: a date or calendar dependence (the dates differ across environments);
- *N-zone:* a genuine non-environment cause: the site's time zone or UTC offset;
- *other:* anything else, described in one aggregate line.

For a leak, the detector rule that missed it is named (by function, in `vesper.edt.corpus.env_kinds`). The classes
are reconciled with Table VI's frame partition: each automation's frame set $\Phi_A$ (`frames_of`), so that the 13
clock-only flags of each relation are matched against the 19 and the 15.

**A12.10 A named variant: the crossing-time reference with env_latency [12].** The published frame relation stays as
it is. The variant, named `frame+crossing+env_latency`, is the frame relation with A8.5's crossing-time sensed reference
on (`sensed_crossing`), plus one outcome kind.

- *env_latency.* Take the environments where the automation fires and its sensed reference has a first crossing in the
  judged window, and in each the latency θ_e = first firing - first crossing, in minutes. The kind holds iff at least
  two such θ_e exist, every one is >= 0, and max θ - min θ exceeds the time-spread tolerance (180 min). An automation
  that fires before its crossing in some environment (A8.5's "precede") is not env_latency; one whose firing waits on
  its conditions by more than the tolerance in some pair of environments is.
- *Code:* an option of the oracle, off by default (`frame_divergences(..., env_latency=False)`), tested first. Off, it
  reproduces the published relation bit for bit on all 5,926 run automations of $S_6$.
- *Reported on the `8fcd672` cache* (its traces and rich outcomes, as A8.5): the flags under the variant, by kind; how
  many carry env_latency, and how many of those also carry time_spread; and the overlap with A8.5's 8 that gain
  time_spread (of which 4 were latency).

**A12.11 The cover's upper bound [13].** The coverage matrix is recomputed from $S_6$: each flag's witness pairs over
the 1,128 environment pairs (A12.1's rule; R4-A's pair cover). The problem: choose 6 environments to maximise the flags
with a witness pair inside the 6 (induced pairs).

- *Solver:* scipy's `milp` (HiGHS), with binary x (48 environments, summing to 6), y (pairs, y <= x at each end) and z
  (flags, z <= the sum of its pairs' y, z <= 1); the objective is the sum of z.
- *Reported for each relation, on all flags and on the training half (section 6's split):*
  - the LP relaxation's bound;
  - the MILP's optimum, or its best bound and incumbent at a 600 s limit;
  - a local-search lower bound (single swaps from greedy's 6 environments and from 20 seeded random starts,
    `default_rng(0)`);
  - greedy's 3 pairs (R4-A), evaluated on their own pairs and on the 15 pairs their 6 environments induce;
  - the gap from greedy to the upper bound.

**A12 deviations (dated 2026-11-05, after the runs).**

- *A12.1, A12.4:* the open-loop replays ran with the current code, which carries Addendum 11's engine fixes, not
  `8fcd672`'s engine. The x1 check still reproduces every flag, kind and witness of the 327 replayed automations;
  470 outcomes of 13 automations differ, all 13 reading `sun.sun`.
- *A12.5:* a bootstrap resample holds a different number of the 148 flags; its discounted part is rescaled to 148 so
  that the interval is on D's own population size.
- *A12.7:* the new sites' elevations (not stated in A12.7) are the towns' rounded published values; they enter only
  the sun's position.
- *A12.9:* two flags fit no named class (a non-light brightness sensor bound as illuminance) and are counted under
  *other*, as registered.

## Addendum 14 (2026-11-05, post hoc: written after the fourth internal review, before its runs)

**Post hoc**: written after the fourth internal review (its item 9, "replace two assumed shortwave inputs with
recorded ones, and test the single-weather-year design") and after every result it revisits was seen, but before any
run below. Nothing earlier changes: `8fcd672` stays the paper's primary sweep and every earlier number stands. Code:
the options and the year parameter are at `986b5d6` (tests first); the script is
`scripts/revision/r7w_weather.py` (`c0b624c`); every run below is at the commit that adds this addendum. Results:
`results/edt/r7w-<commit7>.json` (aggregates only) and a section "Revision R7-W (Addendum 14)" of `results-edt.md`.
No pass/fail criterion is registered here.

**A14.1 Weather fetched (the only new data).** The Open-Meteo archive (`https://archive-api.open-meteo.com/v1/archive`),
hourly, GMT, politely: a generic User-Agent (`python-httpx`), no e-mail address, 2 s between requests, every request's
URL logged (`requests.jsonl` in the lane's scratch directory, `~/vesper-lab/edt/r7-W/`). A day already cached is never
asked for again; the recorded weather of `work-120f4ca` is never fetched again.

- (a) `direct_radiation` and `diffuse_radiation` (`models=era5`, W/m², the mean of the hour before the stamp, on the
  horizontal) for the 6 cities x 4 dates, over the same UTC days the recorded weather covers (4 per city and date).
- **Snow depth (stated before any run).** The archive answers `snow_depth` with nulls under `models=era5` (seen in
  the first fetch, 2026-11-05). It is taken from ERA5-Land (`models=era5_land`, 0.1°, the land component of the same
  reanalysis), the same windows. Where ERA5-Land has no value (a sea cell), the albedo is 0.2.
- (b) The three years before the recorded one. The recorded year is 2026, so the years are 2023, 2024 and 2025 (the
  brief's two choices coincide). Same month and day (01-14, 04-15, 07-15, 10-14); the weekday is not kept (the dates
  are Wednesdays only in 2026). Every variable the sweep reads (`models=era5`), plus (a)'s and the snow depth.
- (c) For k̂ (A14.4): (a)'s variables and the snow depth over home B's days (dataset B, Valencia, 2012). Home B's
  recorded ERA5 days are copied from the validation's cache, not fetched again.

**A14.2 Two options, off by default, bit-identical when off** (indoor knobs that live in `vesper.env.sun`, set only
through `vesper.env.indoor.knobs`; the pinned fingerprint test covers them; a trace recorded under them carries them
in its header and is keyed by them; with both off the weather loaded, every trace and every sweep are as before, to
the byte).

- `RECORDED_SPLIT` 1: the beam share of horizontal irradiance at a moment is direct / (direct + diffuse), the two
  recorded series interpolated as the global one is (on the hour's middle), in place of 0.8 (1 - cloud). The global
  irradiance itself is unchanged (ERA5's direct + diffuse equals its global). Where an hour has no recorded split, or
  both are 0, the assumed share stands. The share sets the beam/diffuse split of both the solar heat and the daylight,
  as `DIRECT_SHARE` does (A9.1).
- `GROUND_REFLECTED` 1: every window's solar heat gains G x glass x 0.5 x albedo x global horizontal irradiance (0.5,
  a vertical surface's view of the ground), through the shade as the rest of the solar heat, and scaled by the solar
  aperture as the rest. Albedo 0.2, or 0.6 where the snow depth exceeds 0.01 m (assumed constants, the review's).
  **Solar heat only; the daylight is unchanged** (the BRE daylight factor is a sky-only formula, and the illuminance
  path is inert under the device reporting rule, R6-A).

**A14.3 Item 9(a): one sweep with both options on.** Rung 4, the 48 environments of `8fcd672`, the current binder, the
device reporting rule, a fresh work directory, at most 4 workers under nice. Weather: `work-120f4ca`'s recording
copied, plus (a)'s sidecars. Check, reported: every trace's header names the recorded split and the snow depth for
all its days, and the two knobs. Reported against `8fcd672` (primary) and against R6-D's `5b54586` (the same engine,
the options off):

- Bound and run counts, negative control, failed environments.
- Under the clock relation and the frame relation: flags, by kind; how many flags hold with the same kinds, hold with
  other kinds, are lost, are new; the lost and the new by kind and by the quantity the automation reads.
- **Orientation witnesses**: per relation, the minimal-witness axis counts (R4-A's method, `r4a_analyses.witnesses`;
  orientation 100 frame / 123 clock at `8fcd672`), and the set of flagged automations that have an orientation witness
  (two environments differing only in plan north whose outcomes diverge, restricted to the pair, under that relation):
  its size, held, lost and new.
- Context: per city and date, over the sun-up hours of the run's three local days, the mean recorded beam share
  beside the mean assumed one; the windows with snow deeper than 1 cm.

**A14.4 Item 9(a): k̂ again.** R6-B's method (A9.4) unchanged: home B, the grid k = 0..2 by 0.01, the pooled bias's
root, 2,000 bootstrap resamples of local days (seed 20261105), the per-room roots and k̂_lin. Driven from the cached
weather only (R6-B's code path fetches; here it is replaced by a read of the cache). Settings: (i) the assumed split,
the reproduction: it must give R6-B's k̂ = 0.25 [0.2052, 0.2894]; if it does not, that is reported first and the
others are read against the reproduced value; (ii) **the recorded split (the reported k̂)**; (iii) the recorded split
with the ground-reflected term; (iv) the assumed split with the ground-reflected term (to separate the two). Stated
for each: whether R4-B's x0.25-x2 bracket contains k̂. Also: home B's daylight hours with a recorded split, and the
mean recorded and assumed shares over them. The open-loop propagation at a new k̂ (A9.4's second step) is not run.

**A14.5 Item 9(b): one sweep per year.** For each of 2023, 2024 and 2025: rung 4, the 48 environments with the year's
dates (`vesper.edt.trace.environments(year)`), the published model (no knob, the options off), that year's recorded
weather only, the current binder, the device reporting rule, a fresh work directory, at most 4 workers under nice,
the three runs one after another. Reported per year: bound and run counts, negative control, failed environments,
flags under both relations by kind, and the comparison with `8fcd672` as A14.3 (held, lost, new; orientation
witnesses).

- **Stability of the 199** (`8fcd672`'s frame flags; R6-D keeps all 199 with the same kinds): how many are flagged
  (any kinds) in each year, in **every** one of the three years, in **some** year, in none; the number flagged in
  k = 0..3 years; how many keep the same kinds in every year; by kind, those held in every year and those held in no
  year; by quantity, those held in no year. The same "every / some" counts for the 790 clock flags.

**A14.6 Item 9(b): the setback breach count per year.** R2-c's 22 setback automations (the population as R4-B used
it, from `120f4ca`'s sweep), R4-B's closed-loop worker and R2-c's judge (a caused breach: a room below 10 C over days
1-2 with the automation and not in the same environment's baseline), at R4-B's nominal corner (every knob at its
default), in the core four: Minneapolis and Stockholm, January 14, both orientations; denominator 22 x 4 = 88 runs per
year. The standard home of `work-120f4ca`; each year's weather from its sweep's work directory. 2026 is run again as
the reproduction: it must give R2-c's 53 of 88; if it does not, that is reported first. Reported per year: breaching
runs of 88, automations, per city, the coldest caused room per run (min / median / max), the baseline's coldest room,
and the outdoor air over days 1-2 per city (mean and minimum).

**A14.7 Item 9(b): the best cover pair per year.** R4-A's post hoc pair cover (`r4a_analyses.pair_cover`: greedy over
the 1,128 environment pairs on the training half; k = 1 is the exhaustive best pair), under both relations, on each
year's sweep and on `8fcd672` and `5b54586`. The pair "changes" when its two environments' labels without the year
(city, month-day, plan north) differ from `8fcd672`'s (Minneapolis 01-14 n0 + Stockholm 07-15 n180, under both
relations). Reported: the pair and its training, held-out and overall coverage.

**A14.8 How it will be read (stated now).** No criterion. If many of the 199 are not flagged in other years, the
paper's "seasons" are four dates of one year, and the flags are statements about those dates' weather; that is
reported as it comes out. A flag of the 199 that is not flagged in some year is not thereby wrong: an automation that
diverges only under a particular weather (a cold night, a cloudy noon) is a weather-conditional finding.

**A14.9 Deviations (dated 2026-11-05, after the runs; nothing above is changed).** All runs ran at `52ded7b` as
registered. After seeing 2024's sweep, whose negative control flags 30 of its 1,252 firing automations (below A1.2's 5 %
halt, so no halt), two diagnostics were added to the report (`f15fd97`); they are not registered analyses.

- *The weekday.* A3.1 put every 2026 date on a Wednesday; A14.1(b) does not keep the weekday. In 2023 the four dates are
  all Saturdays and in 2025 all Tuesdays, but in 2024 one is a Sunday and three are Mondays, so an automation that
  reads the day of the week can diverge across 2024's dates for no weather reason. Reported per year: how many new
  flags, lost flags and flagged negative-control automations read the weekday (a time condition's `weekday`, or a
  template's `weekday()`, by a text match); of the 199, how many read it and how many are flagged in both 2023 and
  2025 (the two years whose dates share one weekday each).
- *The setback.* The other years' measured days also fall on other weekdays than 2026's (Thursday and Friday), so a
  weekday-scheduled setback may not be commanded at all. Reported per year: the runs (of 88) in which the automation
  commanded its setback in the measured days, how many of them breach, and how many of the 22 read the weekday.

## Addendum 13 (2026-11-05, post hoc: written after the fourth internal review, before its runs)

**Post hoc**: written after the fourth internal review asked for it (its items 6(a), 6(b), 6(c) and 11(b)) and after
every result it revisits was seen (R4-A's partition, R4-B's corners, R6-B's propagation, the environment validation),
but before any run below. Nothing earlier changes; every number below is reported beside the earlier ones, and
earlier numbers stand. Base: the rung-4 ERA5 sweep `8fcd672` (the frame relation, the twins' reporting rule
`device`), its home and recorded weather (never fetched again), and its rung-2.5 sweep for R4-A's partition; R4-B's
corner definitions and R6-B's open-loop worker, unchanged. Script: `scripts/revision/r7b_room_terms.py`; results:
`results/edt/r7b-<commit7>.json` (aggregates only) and a section "Revision R7-B (Addendum 13)" of `results-edt.md`.
No pass/fail criterion below is a registered verdict; every analysis is descriptive.

**A13.0 One new knob, bit-identical at its default** (set only through `vesper.env.indoor.knobs`; the pinned
fingerprint test is extended to it): `INDOOR_T_OFFSET_C` (default 0.0) adds a constant to every indoor room's air
temperature as its sensors read it, and the room's relative humidity is its vapour pressure (unchanged) over the
saturation pressure at that shifted temperature. The room's own temperature, its heat balance and therefore every
thermostat's control (the home's heating reads the model's room, not a sensor) are unchanged; daylight and the porch
(outdoor air) are unchanged. So an offset moves what every indoor sensor reads with the heating trajectory held: it
represents an error in the modelled room temperature level, which is how the validation residuals were measured.

**A13.1 Item 6(a): the flags over R4-B's 120 corners (open loop).**

- *Replay set.* Every automation that runs at rung 4 of `8fcd672` and is flagged under either relation (the frame
  relation's 199, the clock relation's 790) or reads a temperature, a humidity or an illuminance by its names as
  written (206, 63, 67): 954 automations. The clock set is included because a timing probe (one environment, not a
  reported number) put a setting at about 6 s per environment for the 954.
- *Settings.* R4-B's 120 corners: internal gains {0, 2, 5} W/m² x roof and ground {off, on} x warm-up {1, 5, 10 days,
  periodic (30 days on the first day's weather)} x heat capacity {80, 110, 165, 260, 370} kJ/(m² K), as
  `r4b_error_budget.corner_knobs`. Each records all 48 traces afresh under its knobs in a cache of its own (the trace's
  reuse key names the knobs) and replays the replay set on them with the sweep's worker (R6-B's, reporting rule
  `device`). The nominal corner (G0-Foff-W1-C165) is every knob at its default: it is the reference and must reproduce
  `8fcd672`'s outcomes for the replay set exactly, and its clock and frame divergences (reported). The review's "only
  the environments the flags need" does not apply: a flag is a divergence across the 48 environments of its relation,
  so every corner records all 48.
- *Families* (membership at a setting, computed by the same functions at every setting and on the published outcomes):
  1. frame flags (199);
  2. **orientation witnesses, frame relation**: frame flags with a one-axis orientation witness under the frame
     relation (two environments of one city and date differing only in the plan's bearing over which the
     frame-restricted divergences are not empty; R4-A's "axes admitting a witness", the north axis; 100 in R4-A);
  3. temperature flags: frame flags that read a temperature (104);
  4. humidity flags: frame flags that read a humidity (36);
  5. **sensor-driven room-model flags**: frame flags that read a sensed quantity (temperature, humidity, illuminance,
     weather) and are not frame-flagged at the published rung 2.5 (R4-A's partition, 104). Rung 2.5 is not re-run:
     its indoor rooms read rung 2's assumed curve, which no knob here touches.
  Secondary: the clock flags (790), and the clock relation's outcome-class orientation witnesses (R6-B's and Table
  XI's definition: clock flags whose outcome classes differ between the two bearings of one city and date; 123).
- *Reported per family*: the published count; the **robust core** (members at every one of the 120 settings, the
  reference included); the **possible set** (members at any of them); lost somewhere and gained somewhere; the
  per-corner minimum and maximum and the corners at each; the mean count per level of each factor (over the other
  three). For the frame flags also how many change their kinds at some corner. The possible set is within the replay
  set: an automation outside it that would gain a flag at some corner is not seen (R4-B's check found 3 of 5,653 such
  automations changing an outcome at aperture x2, 1 flag), so it is a lower bound on the corpus-wide union.

**A13.2 Item 6(b): an indoor-temperature offset (open loop).** The replay set and families of A13.1, at
`INDOOR_T_OFFSET_C` in **{-1.7, -0.9, +2, +7} K** (registered, the review's values), each with all 48 traces recorded
afresh, against the reference at 0 (recorded afresh too, which must reproduce `8fcd672` as in A13.1). Sources, from
the validation sections of `results-edt.md` and the validation report it cites: -0.9 and -1.7 K, room temperature on
home A, every room simulated 0.9-1.7 C too cold (R2-a, "Bias direction"; `results-2026-11-04-environment.md`, "T on
A"); +7 K, home B, both rooms about 7 K too warm (+7.1 and +7.5 K; b(1) = +7.29 K, R6-B A9.3 and A9.4). No indoor
residual of +2 K is measured: the +2 is the review's "±2 K for symmetry" (Reviewer 3, W2), the size of R2-a's and
R4-B's earlier two-kelvin offsets; it is reported as that.
- *Sign.* These are residuals, model minus measured. An offset of the residual's own sign adds to the model's error;
  the one that would correct it has the opposite sign. So, secondary: the opposite signs **{+1.7, +0.9, -2, -7} K**,
  run the same way.
- *Reported*: for the humidity flags (36) and the sensor-driven room-model flags (104), the robust core and possible
  set over {0, -1.7, -0.9, +2, +7} (primary), and over all nine values (secondary), with each value's count; the
  same for every family of A13.1.

**A13.3 Item 6(c): the humidity class** (a fact read from the repository, no run): where VESPER's EN ISO 13788 class
comes from (the code's comment, the validation protocol's addendum 2 and its result file and commits), and whether
"deliberately conservative" or an equivalent appears in the code (`vesper/`) or the documents (`docs/`). Reported as
found.

**A13.4 Item 11(b): illuminance with a change-reporting sensor.**

- *Search* (done before writing this, to name the product): `catalog/vds` (zigbee, zwave, matter, tasmota) for a
  product that reports illuminance and exposes no occupancy. Only the Zigbee catalog has illuminance products; 143 of
  them expose no occupancy, and every illuminance product with a reporting rule in the catalog has the library default
  (a change, at most every 10 s, at least every 3,600 s). None is in the twins' `CARRIED_BY` (the motion-carried rule
  A6.9 gave the RTCGQ11LM), so under the reporting rule `device` each reports its illuminance on change.
- *Product*: **`zigbee:tuya:s-lux-zb`** ("Light sensor", L1 in the catalog, illuminance and battery reporting, no
  occupancy). Its reporting rule is the catalog's library default, assumed (as the RTCGQ11LM's was before A6.9), so
  this tests what an illuminance reader sees with a change-reporting light sensor, not this product's measured timing.
  The other L1 dedicated light sensors (`xfy-cgq-zigb`, `tze284_aaeasoll`, `ti0001-illuminance`) have the same rule,
  so the choice does not change a trace.
- *Home*: `8fcd672`'s EDT home with a `<room>_lux1` of that product beside each of the 8 `<room>_lux0` (the seven
  indoor rooms and the porch), at the same place. Every existing entity id is unchanged (tested); the motion role keeps
  the RTCGQ11LM's occupancy.
- *Runs* (rung 4, all 48 environments, reporting rule `device`, traces recorded afresh): **ref**, the 67 run
  automations that read an illuminance, bound as in `8fcd672`, in the augmented home: it must reproduce their
  published outcomes exactly (reported); **swap**, the same 67 with every RTCGQ11LM illuminance entity they name
  replaced by its `lux1` neighbour's (whole entity ids, templates included; nothing else changes).
- *Reported*: how many of the 67 name a replaced entity and how many entities were replaced; under each relation the
  flags at ref and at swap, flags gained and lost, kinds changed; outcomes changed; automations that fire somewhere.

**A13.5 Resources.** At most 4 workers, under `nice -n 5`; about three hours. Raw outcomes stay in
`~/vesper-lab/edt/r7-B/scratch` (local); each setting's traces are removed after its outcomes are kept. Any deviation
is dated below this addendum.

**A13.6 Deviation (2026-11-05, after the first reference run, before any other setting was looked at).** A13.1 says
the reference "must reproduce `8fcd672`'s outcomes exactly". It cannot: the current code carries Addendum 10's two
engine fixes (`sun.sun` as Home Assistant 2026.10.2 has it; `kelvin`/`color_temp` refused), committed before this
addendum, which R6-D's engine-corrected sweep `5b54586` (Addendum 11) ran with. The first reference (offset 0)
reproduces `5b54586`'s outcomes for the 954 automations exactly (0 of 45,792 differ, the same bound automations) and
`8fcd672`'s frame divergences exactly (0 kind mismatches; the 199 and every family of the frame relation identical),
but differs from `8fcd672` in the outcomes of 66 automations and in the clock divergences of 5 (R6-D's 790 -> 785, all
within the fixes' reach). So: the published memberships every robust core and possible set is set against are
`5b54586`'s (the frame relation's are `8fcd672`'s exactly, so nothing changes for the five registered families; the
clock relation's are 785, not 790), and each reference is reported against both sweeps. The replay set (954) and
R4-A's rung-2.5 partition stay `8fcd672`'s. Nothing else changes.

## Addendum 17 (2026-11-05, post hoc: written after the fifth internal review, before its runs)

**Post hoc**: written after the fifth internal review asked for these analyses (its items 1, 2, 4(a), 6(b), 6(c) and
7(b)) and after every result they revisit was seen (R7-A to R7-W in `docs/experiments/results-edt.md`), but before any
number below is computed. Nothing earlier changes and every earlier number stands; what follows is reported beside it.
A change made after a computation is dated at the end of this addendum and called a deviation. Review 5's item numbers
are in brackets. **No sweep, corner or offset is run again**: every analysis reads saved runs. Script:
`scripts/revision/r8x_analyses.py` (tests first, `tests/edt/test_r8x_analyses.py`); results:
`results/edt/r8x-<commit7>.json` (aggregates and anonymous indices only: no automation text, alias, entity name,
repository or hash) and a section "Revision R8-X (Addendum 17)" of `results-edt.md`. At most 3 worker processes, under
nice. Scratch: `~/vesper-lab/edt/r8-X/` (local). "Frame flags" are `8fcd672`'s 199 under the frame relation. No
pass/fail criterion is registered here; every analysis is descriptive.

**A17.1 The joint robust core [1].**

- *Families* (membership at a setting, by one function for every source, as A12.4 and A13.1 define them):
  1. **frame flags**: flagged under the frame relation (199);
  2. **orientation witnesses**: frame flags with an orientation pair (two environments of one city, date and rung that
     differ only in plan north) on which the frame relation restricted to the pair diverges (100);
  3. **temperature flags**: frame flags reading a temperature by the names as written (`kinds`; 104);
  4. **humidity flags**: likewise for humidity (36).
- *Sources.* Every per-setting membership is recomputed from the saved outcomes and first-firing instants, because the
  lanes did not save memberships per finding for every family (R7-A did not compute the frame-flag family at all):
  - **R7-A's 13 settings (A12.4)**: the open-loop replays of its 327 automations at aperture x0.21, x0.25, x0.29,
    x0.5, x2, direct share 0.6 and 0.95, outdoor air -2 and +2 K, humidity class 2 and 3
    (`r7-A/scratch/openloop-8fcd672-*.pkl`); the published sweep (`8fcd672`, the nominal setting); and the
    engine-corrected sweep (`5b54586`, whole). The x1 replay is A12's check, not a setting, as there.
  - **R7-B's 120 corners (A13.1)** and **its 5 registered indoor offsets** {0, -1.7, -0.9, +2, +7} K (A13.2), on its
    954 replayed automations (`r7-B/scratch/ol-c-*.pkl`, `ol-t-*.pkl`). The secondary offsets {+1.7, +0.9, -2, -7} K
    are reported beside the joint core and are not in it.
  - **R7-W's recorded-shortwave run (A14.3)**: the whole sweep at `52ded7b` with both options on.
- *Checks first.* Each source's own cores, recomputed here, must equal the published ones: R7-A's "every setting
  together" 42 / 88 / 34 and its per-bracket rows (A12.4); R7-B's corners 159 / 42 / 80 / 21 and registered offsets
  146 / 40 / 69 / 19 (frame flags / witnesses / temperature / humidity); R7-W's 198 frame flags and 95 witnesses held.
  A mismatch is reported, and its cause found before a joint number is used.
- *Definitions.* The **joint robust core** of a family is its published members held at every setting of every source
  (13 + 120 + 5 + 1 = 139 settings, each source's nominal counted as that source has it). The **joint possible set** is
  the union over the same settings, each source within its own replayed set (the whole sweeps over all run
  automations), so it is a lower bound on the corpus-wide union. Reported per family: the published count, the joint
  core and the joint possible set; each source's own core beside them; and the joint core without each source in turn
  (which source binds).
- *What it is and is not.* The settings vary one bracket at a time around the nominal model. The AND over them is the
  matrix-AND over sampled settings, not the core over the product of the brackets, which is not run. The product's
  core is contained in it, so **the joint core is an upper bound on the core over the brackets' product** (and the
  joint possible set a lower bound on its union). Separately, within one bracket an intersection over sampled points
  over-estimates the intersection over the bracket's interval: a finding lost between two sampled points is not seen.
  Both caveats are stated with the numbers.

**A17.2 The glazing variants [4(a)].** Addendum 5's two glazing variants (every pane's area x0.5 and x2), swept at rung
4 under the frame relation by A8.8 at `ba8a54c` (`sweep-rung4-r6a-variant-glazing-x0.5-ba8a54c.json` and `-x2-`,
whole sweeps).

- *Check first:* the standard home at `ba8a54c` (`sweep-rung4-r6a-base-ba8a54c.json`) reproduces the 199 frame flags
  and the 100 witnesses (reported).
- *Reported:* per variant, each family's members held, lost and gained; the **orientation-witness core over the
  glazing bracket** {x0.5, x1 (nominal), x2}, with the same for the other three families; and the **joint core
  including them** (A17.1's 139 settings plus the two variants: 141). The other two A5 variants (plan seed 8, mirrored
  east-west) vary the plan, not the glazing; their cores are reported as secondary and are not in the joint core.

**A17.3 The sun reference fixed to the first named event [6(b)].** Population: the 5,926 run automations of `8fcd672`.
The sun references the relation uses are `frames_of(bound).sun_refs` (A6.2: the sun triggers', or, when they name
none, the sun conditions'), in the order they are named.

- *Ambiguous:* (i) the references name two or more distinct sun instants (`SunRef`s differing in event, offset or
  elevation): the automations where the nearest-instant rule chooses; (ii) of these, those naming two or more distinct
  events (sunrise, sunset, an elevation crossing): the review's "two or more named sun events". R6-A counted 98 of
  kind (ii) among the run automations, 3 of them frame flags. Reported: (i) and (ii), and the frame flags among each.
- *The variant:* the frame relation with each automation's sun references cut to the first named (`sun_refs[:1]`);
  everything else (the nearest occurrence of that one instant on the firing's day or a neighbouring one, the 180 min
  tolerance) is unchanged. It is computed in the script on the cached outcomes and first-firing instants; the oracle
  is not changed. Check first: with the references as published, the recomputation reproduces every published frame
  divergence of population (i).
- *Reported:* frame flags gained, lost and with other kinds; `time_spread` counts; orientation witnesses changed
  (secondary).

**A17.4 The residual exposure of the sensed exemption [6(c)].** Population: the run automations of `8fcd672` that are
not frame flags (5,926 - 199 = 5,727, checked).

- *A reference-free frame:* the sensed frame (never computable in the published relation), or the sun frame when its
  references are empty (A8.3's sun exemption). *Outside the crossing-time construction:* `sensed_crossing_refs(bound)`
  is empty (A8.5: no `numeric_state` trigger on a sensed entity with a numeric bound and no `value_template`).
- *Primary (the review's):* the automations that declare at least one frame and only reference-free ones, and fall
  outside the crossing-time construction.
- *Secondary:* those that declare at least one reference-free frame, whatever else they declare, and fall outside it.
  Under the published relation such an automation can never carry `time_spread` either, because one invariant frame is
  enough and a non-computable frame counts as invariant. So this is the exemption's full reach.
- *Reported for each:* the count of the 5,727; how many fire in two or more environments (only these could spread); how
  many carry `time_spread` under the clock relation (a time-of-day spread the exemption leaves unjudged: an upper bound
  on what it could hide); and what they read.

**A17.5 The location precision of the 44 sun-frame flags [7(b)].**

- *Population:* the frame flags of `8fcd672` reading the sun by the names as written (`"sun" in kinds`): 44 (A12.5).
  **All 44 are checked; nothing is sampled.**
- *Procedure (R4-C's):* each is dumped locally, its raw configuration beside its bound one with each bound entity's
  role and room in the standard EDT home (rebuilt as R4-C's `roles.py` did, and checked identical to the `8fcd672`
  home), and read by hand. The labels stay local (`r8-X/labels-sunloc.tsv`).
- *Labels:*
  - **located**: every room-scoped bound role (in a trigger, a condition or an action) is in the room the author meant,
    or bound by A2.5's convention (a room-less name, or a room the home lacks, bound to the living room, with nothing
    in the automation placing it elsewhere). Whether it has no room-scoped role at all is recorded too;
  - **mislocated**: some bound role is in another room than the one the author names (in the entity's name, the alias
    or a companion entity) and the home has that room (R4-C's C5n, C5c), or it has the wrong medium (C1-C4, C6).
    Whether the mislocated role is read by a trigger or a condition (a guard) or is only acted on is recorded;
  - **uncertain**.
- *Reported:* the location precision, located / (located + mislocated), with a Wilson 95 % interval and a repository
  bootstrap (the 44's repositories resampled with replacement, 1,000 resamples, numpy `default_rng(0)`); the
  sensitivity counting uncertain as mislocated; the labels by class.
- *The binder discount, extended.* A12.5's D leaves the 51 frame flags without a sensed frame undiscounted; 38 of them
  read the sun (44 - 6). **D_ext = D - (the mislocated among those 38)**, under A12.5's primary rule. Its interval is a
  repository bootstrap over the 148 and the 38 together (1,000 resamples, `default_rng(0)`; each part rescaled to its
  own size, as A12.5). Secondary: the 6 sun readers among the 148 count as mis-binds when either R4-C's label or this
  check says so. If none of the 44 is mislocated, D is unchanged, and that is the result.

**A17.6 Every rate in the R7 sections, re-checked [2].** Docs only. Every rate stated in the sections R7-A, R7-B, R7-C,
R7-D and R7-W of `results-edt.md` (a percentage, a fraction or a share, with or without an interval) is recomputed from
the numerator and denominator the text gives, or, where the text gives only the rate, from the result file it cites. A
mismatch is a rate that differs from its recomputation beyond its stated rounding, or a numerator and a denominator
from different populations. Reported: how many rates were checked, and every mismatch with its corrected value. The
earlier sections are not edited; the corrections are stated in this lane's section.

**A17 deviations (dated 2026-11-05, after the computations).**

- *A17.3:* after the first computation, the 101 automations naming two or more events were split into sunrise and
  sunset (98, R6-A's count) and those with an elevation crossing (3), to reconcile with R6-A. Nothing else changed.
- *A17.5:* R4-C's classes were written for sensors. Here C4 ("indoor read as outdoor") is used, the other way round,
  for any role of an outdoor place bound to an indoor room (a light, a switch or a sensor), and C3 for an outbuilding.
- *A17.6:* the one R7-B sentence that states a share loosely ("about a third") was checked under its two readings and
  counted once.

## Addendum 18 (2026-11-05, post hoc: written after the fifth internal review, before its runs)

**Post hoc**: written after the fifth internal review asked for it (its items 5 and 4(b)) and after every result it
revisits was seen (R2-c's setback population, R4-B's corners, R6-B's infiltration, A8.8's home variants, R7-A's
orientation witnesses), but before any run below. Nothing earlier changes; earlier numbers stand. Script:
`scripts/revision/r8y_physics.py`; results: `results/edt/r8y-<commit7>.json` (aggregates only: counts, temperatures,
environment and room labels, anonymous indices) and a section "Revision R8-Y (Addendum 18)" of `results-edt.md`. No
pass/fail criterion below is a registered verdict; every analysis is descriptive. At most 4 worker processes, under
`nice -n 5`. Raw results stay in `~/vesper-lab/edt/r8-Y/` (local).

**A18.0 Three new knobs, bit-identical at their defaults** (set only through `vesper.env.indoor.knobs`, named in a
trace's reuse key when moved; the pinned fingerprint test is extended to them):

- `U_WALL` (default 0.35 W/(m² K)) and `U_WINDOW` (default 1.4 W/(m² K)): the outside walls' and the glass's U values,
  which were already module constants; now knobs. A room's H moves by exactly the U change times the opaque outside
  wall's or the glass's area.
- `PER_ROOM_THERMOSTAT` (default 0, off). Off, the model as built: a thermostat in a hallway (`CENTRAL_ROOMS`) heats
  every indoor room that has no thermostat of its own, all of them switched together on the hallway's own reading
  (central heating). On (1): every such room has a thermostat of its own, which holds **the hallway thermostat's
  setpoint and mode** on **the room's own air temperature**, with the same rule and band as a real one (heats below
  setpoint - 0.3 K, stops above setpoint + 0.3 K; cooling likewise). The hallway keeps its real thermostat. The
  rooms' thermostats are the model's, not devices: they report nothing and no automation sees them. The off path is
  pinned by a fingerprint of the hallway-heated test house taken at `daa0154` (`tests/lab/test_per_room_thermostat.py`).

**The binding rule, decided now.** The setback automations name one climate entity, the EDT home's hallway thermostat
(the binder maps every climate role there; A5.1). With the option on, a command to that thermostat **sets every heated
room's thermostat** (they follow its setpoint and mode): a zoned system with one controller and a sensor and valve per
room (a TRV per radiator following a central schedule). The alternative, that only the hallway's own thermostat takes
the command and the others keep 20 C, would test the setback's reach, not the heat distribution the review asks
about, and it is not run. The rooms an automation's climate target heats (`confirm.controlled_rooms`, the 7 indoor
rooms) and R2-c's causal rule are unchanged.

**A18.1 Item 5(a): the setback population with a thermostat per room (closed loop).**

- *Population and path.* R2-c's 22 setback automations (`120f4ca`, rung 4, bound as stored), each alone, closed loop
  (`vesper.edt.confirm.closed_loop`, R4-B's and R6-B's worker `r4b_error_budget._closed_job`), in the core four
  environments (Minneapolis and Stockholm, 2026-01-14, plan north 0 and 180): 88 runs, each against its environment's
  baseline (no automation) under the same setting, R2-c's causal rule (a room-run cell counts when the room's minimum
  over days 1-2 is below 10 C with the automation and not in the baseline; 7 rooms per run). Everything else at R4-B's
  nominal corner (internal gains 0, no roof or ground, 1-day warm-up, 165 kJ/(m² K), 0.5 ACH): every other knob at its
  default.
- *Settings.* **hallway** (the option off: the model as built) and **per-room** (`PER_ROOM_THERMOSTAT` 1).
- *Check.* The hallway setting must reproduce R2-c step 2 on the core: the same 53 breaching runs, every coldest
  caused room, caused-cell count and baseline minimum. If it does not (the code now carries Addendum 10's engine
  fixes), the cause is found and stated before the per-room numbers are used, and they are set against the reference.
- *Reported, per setting:* breaching runs (of 88), automations, Minneapolis / Stockholm, caused cells (of 616); the
  **coldest caused room per breaching run** (min / median / max), and the **coldest room per run over all 88 runs with
  the automation**, breaching or not (min / median / max); the baseline's coldest room; the largest drop below
  baseline; runs with a commanded setback.
- *The coldest room's identity:* for each of the 88 runs, the room (by its generic id: corridor, living, kitchen,
  bathroom, office, bedroom_1, bedroom_2) with the lowest day 1-2 minimum with the automation; tabulated per setting;
  the number of runs whose coldest room differs between hallway and per-room, over the 88 and over the runs breaching
  in either; the per-run change of the coldest room's minimum (per-room minus hallway; min / median / max); and the
  rooms holding a caused breach (cells per room) in each setting. Also, per-room only, how far below the commanded
  setpoint the coldest room goes (its minimum minus the setpoint).
- *Secondary* (registered): per-room in all 48 environments (22 x 48 runs, 48 baselines), reported as R6-B's
  nominal-48 row: breaching runs over all 48 and how many fall outside the core.

**A18.2 Item 5(b): a wall/glass U corner (closed loop).**

- *Corners:* U_wall in **{0.35, 0.6, 1.0}** x U_glass in **{1.4, 2.0, 2.8}** W/(m² K): 9 corners (post-1990 to
  pre-1980 construction, the review's values). **(0.35, 1.4) is the current setting** (the model as built), so it is
  the hallway setting of A18.1, run once and used for both.
- *Runs:* A18.1's population, environments, path and causal rule, at every corner with everything else nominal;
  **primary under the hallway thermostat** (the model as built), each corner with its own four baselines; **secondary,
  the same nine corners under per-room thermostats** (the U x thermostat product, 18 settings, cheap at the measured
  throughput of about 2 runs a second on 4 workers).
- *Reported, per corner:* the standard home's H (outside walls, glass, ventilation; total W/K; interior partitions
  apart, as R6-B's breakdown); the A18.1 statistics (breaching runs of 88, automations, cities, caused cells, coldest
  caused room per run min / median / max, coldest room per run over all 88, baseline coldest room); whether the
  breaching-run sets are nested along each U axis (descriptive); and the baseline: whether any baseline room falls
  below 10 C at a leaky corner (the heating's 100 W/m² may not hold 20 C there), which the causal rule then excludes.
- *The finding, restated* (how it will be read, stated now): the breaching-run count and the coldest-caused-room
  range over the primary settings of A18.1 and A18.2 (the hallway U corners and the per-room nominal) are what §V-D's
  "restated" sentence may quote, with the secondary product beside it. No range is chosen after seeing it.

**A18.3 Item 4(b): the orientation witnesses over generated floor plans (open loop).**

- *The plans, by a rule fixed now, applied to plans only (no result was computed).* `vesper lab generate`'s six-room
  plan for a seed, with the standard home's products (A5.1's `plan-seed8` mechanism, `vesper.edt.home.plan_home`).
  Probing seeds 7-39 (the plans and their windows only) shows the generator's window rule gives **four room -> facing
  assignments**: every plan puts the living room's two panes on the plan's top side (0), the kitchen's on the bottom
  (180), bedroom 1's west (270) and the office's east (90); only the bathroom (0 or 180) and bedroom 2 (0, 90 or 180)
  vary. The four are the standard home's (bathroom 180, bedroom 2 0; seeds 7, 14, 17, ...), seed 8's (0, 90; seeds
  8-10, 12, 13, ...), seed 11's (0, 180; 11, 24, 29, ...) and seed 16's (180, 90; 16, 18, 33, 35). The standard home
  and seed 8 are already swept (A8.8). **Rule:** in seed order from 9, accept a seed whose room -> facing assignment
  is not the standard home's, not seed 8's and not an accepted seed's; when fewer than three exist in seeds 9-39, fill
  up in seed order with seeds whose glass area per facing (N, E, S, W; m², two decimals) differs from the standard
  home's, seed 8's and every accepted seed's. **It gives seeds 11 and 16, then 9.** Their glass per facing (N / E / S /
  W): standard 5.92 / 1.44 / 1.36 / 1.44; seed 8 4.42 / 2.88 / 1.00 / 1.44; **seed 9 4.70 / 2.88 / 1.00 / 1.44; seed
  11 4.84 / 1.44 / 2.44 / 1.44; seed 16 4.48 / 2.88 / 1.36 / 1.44**. Seed 9 has seed 8's assignment with a larger
  living-room pane; it is the third plan only because the generator offers no fourth assignment. Every room is
  redrawn in every plan. Each is `VARIANTS` `plan-seed11`, `plan-seed16`, `plan-seed9`; each has the standard home's
  role set and entity ids and passes `vesper lab check` and the layout check (tested), so every automation binds as in
  the standard home.
- *Runs (restricted, as the review allows).* `vesper edt run` cannot restrict to a set of automations, so the sweep's
  own worker is used as in R6-B's and R7-A's open loop (`vesper.edt.sweep._run_env` via `r6b_physics._trace_job`): in
  each home, all 48 environments' rung-4 traces **recorded afresh** (a fresh cache with `8fcd672`'s ERA5 weather
  copied in, no trace copied, nothing fetched), and **the 199 frame flags of `8fcd672`** replayed on them, bound as
  stored, reporting rule `device`. Automations outside the 199 are not replayed, so a witness gained outside them is
  not seen (the possible set is within the 199).
- *Checks, run the same way:* (i) **standard**, the standard home built afresh with fresh traces: its frame flags,
  kinds and orientation witnesses over the 199 against `8fcd672`'s (mismatches reported; its outcomes are compared
  with `5b54586`'s, which A13.6 found the current code reproduces); (ii) **seed 8**, against A8.8's full sweep of
  `plan-seed8` (`ba8a54c`): frame flags and orientation witnesses over the 199. A frame-flag or witness mismatch in
  (i) or (ii) is investigated and stated before any plan number is used; the plan numbers are then reported against
  both `8fcd672` and check (i).
- *Families* (R7-A's functions, `r7a_analyses.setting_findings`): a frame flag in a home is an automation of the 199
  whose frame relation over that home's 48 outcomes is not empty; an **orientation witness** is a frame flag with an
  orientation pair (same city and date, plan north 0 and 180) on which the frame relation restricted to the pair
  diverges (R7-A A12.1's definition; 100 in `8fcd672`).
- *Reported, per plan (9, 11, 16; and seed 8 from check (ii)):* frame flags kept of the 199 (and with the same kinds),
  orientation witnesses kept of the 100 (by read kind: temperature 64, humidity 30, neither 6), witnesses lost, new
  witnesses among the 199. **The core over plans**: the published witnesses that are witnesses in every one of the
  three new plans (primary); secondary, over the four generated plans (with seed 8) and over the four plus the
  mirrored home (A8.8's `mirror-ew` sweep, its witnesses computed the same way from the full sweep). The possible
  set (witnesses in any plan, within the 199). The witness ids per plan are kept in the scratch directory for a joint
  core; they are not committed.
- *How it will be read* (stated now): a witness held in every plan does not owe its orientation sensitivity to the
  standard home's window arrangement; one lost in a plan owes it partly to which rooms face which way. The plans vary
  only two of seven rooms' window facings (and every room's size), so the core over plans is an upper bound on the
  core over real homes' window distributions; that limit is stated with the results.

**A18.4 Resources.** About 18 x 92 + 1,104 closed-loop runs (about 25 min) and 5 x 48 trace recordings with the 199
replayed (about an hour), at most 4 workers. Any deviation is dated below this addendum.
