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
