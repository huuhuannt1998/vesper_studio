# Environmental differential testing of home automations (design, 2026-11-04)

Status: draft for the PI's approval. Decision: RKA dec_01M6KJJEPH70NH4GCX15M5SJ1Z (PI chose this as the paper's
core mechanism, IoT-J). Framing source: a peer-review pass.

## 1. Why

A home automation is written once, in one home, and shared, copied and installed in others. Whether it does what
its author meant depends on where and when it runs: a "lamp on below 50 lx" rule fires at 14:53 in a Stockholm
January and 21:27 in its July (VESPER's cross-site run), a sunset rule never runs inside the polar circle, a
temperature rule meant for a heated house oscillates in a sunny one. Existing analyses of home automations check a
rule set in one home: static conflict and safety analysis (rule interaction, physical-channel interactions), or
testing with generated event sequences in one environment. None asks *for which homes, places and seasons does this
automation do something else*, and none can answer it, because it needs the same automation run with real products
in many real environments.

Of 45,208 automations in 1,186 public Home Assistant repositories (1,123 of them with automations), 5,102 (11.3 %, in 629 repositories) trigger on or
check the environment: the sun 3,029, temperature 1,362, illuminance 418, humidity 322, weather 314 (counted
2026-11-04 over the corpus used by the earlier rule-prevalence study).

## 2. The mechanism (the paper's contribution)

**Environmental differential testing (EDT).** Given an automation A, a set E of environments, and an oracle O, run A
in a home under each e in E and report the environments where O flags A's behaviour, with a minimal witness.

- **Environment space E** = place (lat, lon, climate) x date (season) x plan orientation x product (which real
  sensor model reports the quantity, with its catalog reporting rules: sampling, reportable change, intervals).
  The product axis is what VESPER's catalog twins add: the same rule on a lux sensor that reports only 10 % changes
  can miss its threshold.
- **Binding** (a mechanism of its own): a corpus automation names entities of its author's home
  (`sensor.hallway_lux`, `light.porch`). EDT binds each referenced entity to a twin of a role-compatible product in a
  standard home by its domain, device class and name tokens (illuminance, temperature, motion, a light, a cover, a
  climate, a lock), and records the binding; an automation whose entities cannot all be bound is reported as
  unbindable, not dropped.
- **Typed divergence oracle O** (outcomes, not raw traces): never fires; fires outside its author's evident window
  (a "when it gets dark" lamp firing at 07:20); flaps (more than k toggles per hour); and a **safety envelope**
  violation, stated before the run: a room below 10 °C or above 30 °C under the automation's control, a lamp left on
  all night, a cover open at night, heating and cooling fighting. The envelope is definitional (precision by
  construction); "against the author's intent" is not claimed without the labeling study in section 4.
- **Minimal environment cover**: find a small set of environments (target 5) that exposes at least 80 % of the
  automations O flags anywhere in E, by greedy set cover over the environment-by-automation outcome matrix; it is
  the mechanism's reusable artifact: a test suite a platform or an author can run in seconds.
- **Minimal witness**: for each flagged automation, the nearest environment to a neutral one (same place, other
  date; same date, other orientation; one product swap) that flips its outcome, by bisection over one axis.

Formal model (for the paper): an automation is a guarded transition system over entity states; an environment is a
function from time to physical fields; a binding maps entities to (product, location); the product's reporting
function maps fields to reported states. EDT's outcome is O(run(A, bind, e)); divergence is defined over the
outcome lattice, not trace equality, so harmless timing jitter is not a finding.

## 3. Evaluation (what each research question needs)

- **RQ1 (scale and yield):** how many corpus automations parse into VESPER's engine (with the supported-feature
  gap listed), bind, and run; throughput (automation-environment evaluations per minute).
- **RQ2 (what EDT finds):** how many environment-coupled automations O flags, by kind, with examples; a negative
  control set (automations on device state or clock only) whose flag rate must be near zero, else the harness is
  wrong.
- **RQ3 (is the physics necessary? the necessity ladder):** the same corpus and oracle at five rungs: 0 a static
  YAML linter (syntactic features only), 1 state-only sensors with no site, 2 Home Assistant's own sun integration
  plus an outdoor proxy, 3 the full field with daylight rescaled by validation D's error, 4 the full field. The
  linter may detect place-sensitivity but cannot quantify it; if rung 0 matches rung 4, the paper says so.
- **RQ4 (the cover):** size of the minimal environment cover for 80 % of flagged automations, its transfer to a
  held-out half of the corpus and held-out cities, against random environments and the hand-picked six-city grid;
  non-degeneracy: no single environment covers more than 50 %.
- Fidelity of the inputs (existing work, kept and tightened): the sun, the weather, the indoor model's validation
  and its grades; the daylight bracket (done 2026-11-04: firing times keep their order within a factor of 2 of the
  daylight model, rho >= 0.99, and not at the dataset's 1/14.8, rho 0.17), stated as the result's dependence.

## 4. What is built, what is new work

Built: twins and catalog, the automation engine (state, numeric_state, time, sun triggers and conditions), the
environment engine, the cross-site runner, the corpus and its parser (both syntax generations).

New:
1. Corpus extraction into VESPER's engine format, with the unsupported-feature census (templates, scripts,
   blueprints, `for:`, choose/if blocks: support the common ones, count the rest).
2. The binder (role inference + product choice per role) and a standard home with every role present.
3. The typed oracle and the safety envelope.
4. The sweep runner (precomputed environment traces per place-date-orientation, automations replayed against them;
   parallel), outcome matrix, cover and witness search.
5. The ladder's rungs 0-3.
6. An intent-labeling study (two labelers, 100 flagged automations, kappa) only if the paper wants to claim
   "defects"; otherwise findings are reported as envelope violations and divergences.

## 5. Non-goals and limits

- Not a verifier: EDT samples environments; it proves nothing for environments it did not run.
- Not a claim about the automations' authors' homes: the binding places each automation in a standard home.
- Indoor temperature carries validation T's failure (sunny rooms overheat): temperature-envelope findings in sunny
  rooms are reported with that caveat, or the thermal model is repaired first (the thermal ladder test).
- No new device classes (Matter appliances, residents' appliance use) for this paper.

## 6. Risks

- Parse/bind yield below 50 % of environment-coupled automations would make the corpus claim about a subset.
- Divergences may be dominated by sun rules, which are syntactically obvious (rung 0 wins): then the paper's
  claim is quantification and the product axis, not detection.
- The cover may be degenerate (one polar-midwinter environment covers everything): reported as such.

## 7. Plan outline (detailed plan after approval)

Week 1: thermal ladder and identifiability test (open from the framing); corpus extraction and census. Week 2:
binder, standard home, oracle, negative control. Week 3: sweep runner at scale, outcome matrix, RQ2. Week 4: ladder
rungs 0-3 (RQ3), cover and witnesses (RQ4). Week 5: paper rewrite (problem, insight, mechanism, RQs) and an ARC
peer-review pass (stage 18) on the revised manuscript.
