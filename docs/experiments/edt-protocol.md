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
