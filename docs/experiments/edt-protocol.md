# Environmental differential testing (EDT): the sweep protocol

Fixed on 2026-11-04, before the first sweep over the real corpus. Everything below (environments, oracle thresholds,
the negative control and its halt threshold, the cover criteria, the held-out split) is decided here and is not
tuned on the sweep's results. A change after the first real run is an addendum with its date and reason, and the
earlier numbers are reported next to the new ones.

Code: `vesper/edt/` (corpus, home, bind, trace, replay, oracle, sweep, cover). Commands:

    vesper edt run   --corpus DIR --work DIR --out FILE [--workers N] [--rung R] [--limit N]
    vesper edt cover --sweep FILE --out FILE

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
