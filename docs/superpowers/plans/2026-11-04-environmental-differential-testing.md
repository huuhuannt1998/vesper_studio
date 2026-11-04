# Environmental Differential Testing (EDT) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the environment-coupled automations of a public Home Assistant corpus across places, seasons,
orientations and sensor products in VESPER, flag the ones whose outcome diverges or leaves a safety envelope, and
mine a minimal environment cover with witnesses; plus the evaluation (necessity ladder, closed-loop confirmation).

**Architecture:** Two tiers. Screening is open-loop: for each environment, one VESPER session of a standard home
records every sensor report for three days (a trace); corpus automations, bound to the standard home's devices, are
replayed through VESPER's own automation engine against the trace, many per engine, their actions recorded, not fed
back. Confirmation is closed-loop: flagged automations (and a random sample of unflagged ones) run in a full session
where their actions act. A new package `vesper/edt/` holds the pipeline; the engine gains a permissive mode.

**Tech Stack:** Python 3.12, VESPER (`vesper.lab`, `vesper.env`, `vesper.twins`), PyYAML, numpy/scipy (validation
extra, already installed), multiprocessing for the sweep.

**Spec:** `docs/superpowers/specs/2026-11-04-environmental-differential-testing-design.md` (PI approved 2026-11-04)

## Global Constraints

- Never name the sibling project that collected the corpus in any committed file; the corpus path is only a CLI
  argument (`--corpus DIR`), with no committed default. The corpus is read-only.
- No push to GitHub without the PI's OK. Home Assistant only on 18123/18830; never 1883/8123.
- Environment space E (spec §2): the six cross-site cities (`vesper.validation.crosssite.CITIES`), four dates
  (2026-01-15, 2026-04-15, 2026-07-15, 2026-10-15), plan orientations 0 and 180, and the product variants of
  Task 3. Measured window: days 1-2 of a three-day run (day 0 is warm-up).
- Oracle thresholds (spec §2; fixed here before any sweep): flapping = more than 6 firings of one automation in one
  hour; time divergence = first firing's local time of day differs by more than 3 h between two environments where
  it fires; night = 00:00-05:00 local; "lamp left on all night" = a light the automation turned on and nothing turned
  off before 01:00; "cover open at night" = a cover the automation opened after sunset and did not close before
  midnight. Temperature-envelope (below 10 °C, above 30 °C) only in closed-loop confirmation.
- Cover target: >= 80 % of flagged automations; non-degeneracy: no single environment covers > 50 %; held-out half
  by repository (sha256 of the repo name, even/odd).
- Every count is over automations de-duplicated by canonical hash (identical copies across repositories count once).
- Pre-registration: Task 0's and Task 9's pass criteria are committed in `docs/experiments/environment-validation.md`
  (addendum 3) and `docs/experiments/edt-protocol.md` before their first run.

## Review Focus

- Templated entities (`{{ states('sensor.x') }}`) and template conditions: refused with reason "template", counted in
  the census, never a crash (Task 1, Task 2 tests).
- A `for:` duration or `delay` still pending at the trace's end: the replay ends cleanly and the pending firing is not
  counted (Task 5 test).
- A sun trigger on a day with no sunset (a site above the polar circle in June): outcome "never fires", not a
  crash (Task 6 test with lat 70).
- An entity with no role tokens (`sensor.xyz_1`) or several (`sensor.temp_lux`): unbindable with reason "no role",
  or the first role by ROLE_PRIORITY, deterministically (Task 3 test).
- The same automation copied into many repositories: counted once (Task 1 test on two identical files).

---

### Task 0: Thermal ladder and identifiability (open from the framing)

**Files:**
- Modify: `docs/experiments/environment-validation.md` (append "Addendum 3" before any code runs)
- Create: `vesper/validation/thermal_ladder.py`
- Modify: `vesper/cli.py` (`vesper validate environment --thermal-ladder`)
- Test: `tests/validation/test_thermal_ladder.py`

**Interfaces:**
- Consumes: `load_appliances`, `load_sml2010`, `_era5`, `_values`, `APPLIANCES_ROOMS`, `SML_ROOMS` from
  `vesper.validation.environment`.
- Produces: `fit_model(order: str, t_out, ghi, measured, heat_on) -> FitResult` with `order` in
  ("1R1C", "1R1C+solar", "2R2C", "2R2C+solar"); `FitResult(params: dict, rmse: float, r: float, ci95: dict[str,
  tuple[float, float]], aic: float)`; `ladder_report(appliances, sml2010, cache) -> dict`.

- [ ] **Step 1: Append Addendum 3 to the protocol and commit it.** Content: the four model orders; fit on dataset A
  per room by least squares (`scipy.optimize.least_squares`) with heating as an unknown input proxied by the room's
  10th-percentile setpoint rule; held out on B with parameters per unit floor area unchanged; identifiability by
  profile likelihood (95 % interval per parameter); the three outcomes of ARC pass 2's H4 verbatim: repair (B RMSE
  <= 3.0 °C, r >= 0.70, parameters within ±50 %, solar aperture g·A within 0.5-3 m² for a 16 m² room), sponge (an
  interval spanning an order of magnitude or an implausible aperture), neither (B RMSE > 5 °C, well identified).
- [ ] **Step 2: Failing tests.** `test_a_known_2r2c_room_is_recovered` (simulate a 2R2C room with known params on a
  synthetic week, fit "2R2C+solar", assert each param within 10 % and its ci95 contains the truth);
  `test_an_unidentifiable_parameter_has_a_wide_interval` (zero irradiance: the solar aperture's ci95 spans > 10x);
  `test_the_ladder_report_names_the_outcome` (report["outcome"] in {"repair", "sponge", "neither"}).
- [ ] **Step 3: Run them; expect import errors.** `.venv/bin/python -m pytest tests/validation/test_thermal_ladder.py -q`
- [ ] **Step 4: Implement** the four models as discrete-time recurrences on 10-minute steps, the fits, profile
  likelihood (re-fit with one parameter fixed over a grid of 15 values; interval where the cost rises by < the
  chi-square 95 % threshold), AIC, and `ladder_report`.
- [ ] **Step 5: Tests pass; run `vesper validate environment --thermal-ladder`; write the result into
  `docs/experiments/results-2026-11-04-environment.md`; commit code, report JSON and doc.**

### Task 1: Corpus extraction and census

**Files:**
- Create: `vesper/edt/__init__.py`, `vesper/edt/corpus.py`
- Modify: `vesper/cli.py` (`vesper edt census --corpus DIR --out FILE`)
- Test: `tests/edt/__init__.py`, `tests/edt/test_corpus.py`

**Interfaces:**
- Produces: `CorpusAutomation` (dataclass: `repo: str, file: str, path: tuple, raw: dict, hash: str`);
  `iter_automations(raw_dir: Path) -> Iterator[CorpusAutomation]` (de-duplicated by `hash`, first occurrence in
  sorted file order kept); `env_kinds(raw: dict) -> set[str]` over triggers and conditions, kinds in {"sun",
  "illuminance", "temperature", "humidity", "weather"}; `census(raw_dir) -> dict`.

- [ ] **Step 1: Failing tests** with a tiny corpus in `tmp_path` (files named `owner~repo##path~file.yaml` as in the
  real corpus): a legacy and a modern automation are both found; a `!secret`/`!include` tag does not crash;
  two identical files in two repos give one automation; `env_kinds` of a `platform: sun` trigger is {"sun"} and of a
  `numeric_state` on `sensor.living_lux` is {"illuminance"}; a `template:` trigger entity is not an automation.
- [ ] **Step 2: Run; expect failures.**
- [ ] **Step 3: Implement** with a YAML loader that turns every local tag into the literal string `"!tag value"`
  (as `scripts/revision/rule_corpus_prevalence.py` does; copy its loader and `discover` logic into this module, do
  not import or modify the revision script, whose outputs must stay byte-identical); canonical hash = sha256 of
  `json.dumps(raw, sort_keys=True)` minus `id` and `alias`.
- [ ] **Step 4: Tests pass; run the census on the real corpus** (`vesper edt census --corpus <path>`); expected
  totals near 45,208 automations and 5,102 environment-coupled before de-duplication; record both, after
  de-duplication too, in `results/edt/census-<stamp>.json`.
- [ ] **Step 5: Commit.**

### Task 2: The engine's permissive mode

**Files:**
- Modify: `vesper/lab/automations.py`
- Test: `tests/lab/test_automations_permissive.py`

**Interfaces:**
- Produces: `Engine(session, permissive: bool = False, automations: list[dict] | None = None)`. With
  `permissive=True`: an action whose service is not in `SERVICES` parses to `{"kind": "record", "service": str,
  "targets": list[str]}` and runs by calling `session.automation_command(name, why, service, target, "", "", None)`
  once per target (no twin); `mode` other than "single" is run as single and listed in
  `Automation.approximations: list[str]`; `automations` given directly replaces reading `home.automations`.
  `permissive=False` behaves exactly as today (every existing test stays green).

- [ ] **Step 1: Failing tests:** a `notify.mobile_app` and a `climate.set_temperature` action are recorded with their
  targets; `mode: restart` loads with approximation "mode restart run as single"; a template condition is still
  refused ("condition 'template' is not supported"); `permissive=False` still refuses `notify.*`.
- [ ] **Step 2: Run; expect failures. Step 3: implement. Step 4: run `tests/lab` in full; all pass. Step 5: commit.**

### Task 3: The standard EDT home and the binder

**Files:**
- Create: `vesper/edt/home.py`, `vesper/edt/bind.py`
- Test: `tests/edt/test_bind.py`

**Interfaces:**
- Consumes: `base_home(work, catalog_dir)` from `vesper.validation.crosssite`.
- Produces: `PRODUCTS = {"illuminance": ["zigbee:aqara:rtcgq11lm", "zigbee:aqara:rtcgq14lm",
  "zigbee:tuya:ts0601_illuminance_temperature_humidity_sensor_1"], "temperature": ["zigbee:sonoff:snzb-02",
  "zigbee:aqara:wsdcgq11lm"]}` (all L1 in `catalog/vds` on 2026-11-04; the variants must differ in their reporting
  rules (reportable change or report intervals) for the product axis to mean anything: a test asserts it, and a
  variant identical to another is replaced by another L1 product of the same feature); `build_edt_home(work: Path, catalog_dir: str) -> tuple[Path, dict]` returning home.yaml and
  `roles: dict[(role, room, variant), entity_id]`; `ROLE_PRIORITY = ("illuminance", "temperature", "humidity",
  "motion", "door", "light", "switch", "lock", "climate", "cover")`; `infer_role(entity_id: str) -> tuple[str, str] |
  None` (role, room; room from tokens living/lounge, kitchen, bedroom, bath, hall/corridor, office/study, default
  "living"); `bind(raw: dict, roles: dict, variant: int) -> dict | Unbindable` returning the automation with every
  entity id rewritten (`Unbindable(reason: str)` otherwise); `sun.sun` passes through.

- [ ] **Step 1: Failing tests:** the illuminance variants' twins differ in reporting rules (from their records);
  `sensor.hallway_lux` -> ("illuminance", "corridor"); `binary_sensor.front_door` ->
  ("door", "living"); `sensor.xyz_1` -> None; `sensor.temp_lux` -> illuminance (priority); a numeric_state on
  `sensor.kitchen_illuminance` with `light.kitchen` action binds to the home's kitchen illuminance sensor of variant
  1 and its kitchen light, and the result loads in `Engine(permissive=True)` with no refusal; an automation naming
  `weather.home` is `Unbindable("weather entities are not modelled")`.
- [ ] **Step 2: Run; expect failures. Step 3: implement** (the home adds, in each room, one illuminance sensor per
  `PRODUCTS["illuminance"]` variant and one temperature/humidity sensor per `PRODUCTS["temperature"]` variant,
  ceiling-mounted at the room's centre). **Step 4: pass.** Also switch `vesper.validation.crosssite.EXTRA_DEVICES`
  from the Philips sensor (not L1) to `zigbee:aqara:rtcgq11lm`, rerun `vesper validate crosssite`, and note the change
  in `docs/experiments/results-2026-11-04-crosssite.md`. **Step 5: commit.**

### Task 4: Environment traces

**Files:**
- Create: `vesper/edt/trace.py`
- Test: `tests/edt/test_trace.py`

**Interfaces:**
- Produces: `Env` (frozen dataclass: `city: str | None, date: str | None, north_deg: float | None, rung: int = 4`);
  `ENVIRONMENTS: list[Env]` (the Global Constraints grid, rung 4); `record_trace(home_yaml: Path, env: Env, cache:
  Path) -> Path` writing `trace-<key>.jsonl.gz`: one header line `{"env", "site", "start_time"}` then every report
  `{"t", "twin_id", "feature", "value", "observed_t"}` of a three-day session with no automations and no residents;
  idempotent (an existing file is reused). Rungs: 4 full; 3 full with `indoor.DAYLIGHT_SCALE = 1/14.8`; 2 a no-site
  copy of the home (assumed indoor curve) with the site kept in the header for the sun; 1 a no-site copy and no site
  in the header.

- [ ] **Step 1: Failing tests** (two-hour sessions via a `hours` parameter, `fetch=False` weather): a trace has a
  header and reports of the illuminance variants; recording twice reuses the file; rung 3's mean daylight lux is
  about 1/14.8 of rung 4's (within 5 %).
- [ ] **Step 2-4: run, implement (reuse `prepare` and weather fetch from `vesper.validation.crosssite`), pass.
  Step 5: commit.**

### Task 5: Open-loop replay

**Files:**
- Create: `vesper/edt/replay.py`
- Test: `tests/edt/test_replay.py`

**Interfaces:**
- Consumes: `Engine(session, permissive=True, automations=...)` (Task 2), traces (Task 4).
- Produces: `Firing` (dataclass: `t: float, automation: str, service: str, target: str`); `replay(home_yaml: Path,
  trace: Path, automations: dict[str, dict]) -> dict[str, list[Firing]]` keyed by automation id. Implementation: a
  `TraceSession` with the home, a `Runtime` holding the home's twins, a `LogicalClock`, `subscribe_reports` that
  receives the trace's reports at their `observed_t` (scheduled on the clock), and `automation_command` that appends a
  `Firing` instead of commanding a twin; the run covers the trace's three days; firings before day 1 are dropped.

- [ ] **Step 1: Failing tests:** a "lamp on below 50 lx" automation fires once at the trace's evening crossing; a
  `for: "02:00:00"` trigger pending at the end yields no firing and no error; 200 automations replay against one
  trace in under 10 s; replaying the same inputs twice gives identical firings.
- [ ] **Step 2-4: run, implement, pass. Step 5: commit.**

### Task 6: The oracle

**Files:**
- Create: `vesper/edt/oracle.py`
- Test: `tests/edt/test_oracle.py`

**Interfaces:**
- Produces: `Outcome` (dataclass: `fired: bool, first_tod_h: float | None, flaps: bool, night_light: bool,
  night_cover: bool`); `outcome(firings: list[Firing], site, measured_days=(1, 2)) -> Outcome` using the Global
  Constraints thresholds; `divergences(by_env: dict[Env, Outcome]) -> set[str]` with kinds {"fires_somewhere_only",
  "time_spread", "flaps", "night_light", "night_cover"} ("fires_somewhere_only": fired in some environments and not
  in others; "time_spread": max - min `first_tod_h` > 3; the envelope kinds when any environment shows them).

- [ ] **Step 1: Failing tests:** seven firings in one hour -> flaps; a light turned on at 22:00 with no turn-off ->
  night_light; firings only in Stockholm January -> "fires_somewhere_only"; a sunset trigger at lat 70 in June
  (no sunset) -> `Outcome(fired=False, ...)` with no exception; identical outcomes everywhere -> empty set.
- [ ] **Step 2-4: run, implement, pass. Step 5: commit.**

### Task 7: The sweep, cover and witnesses

**Files:**
- Create: `vesper/edt/sweep.py`, `vesper/edt/cover.py`
- Modify: `vesper/cli.py` (`vesper edt run --corpus DIR --work DIR --out FILE [--workers N] [--rung R] [--limit N]`)
- Create: `docs/experiments/edt-protocol.md` (committed before the first full sweep: the Global Constraints'
  thresholds, the negative-control definition, the cover criteria, the held-out split)
- Test: `tests/edt/test_cover.py`, `tests/edt/test_sweep.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5, 6.
- Produces: `sweep(corpus, work, envs, rung, workers, limit) -> dict` with per automation: repo, hash, kinds,
  binding (variant per role), outcomes per env, divergences; the negative control = bound automations whose
  triggers and conditions name only motion, door, light, switch, lock entities or time, never environment kinds;
  `greedy_cover(flagged: dict[str, set[Env]], target=0.8) -> list[Env]`; `witnesses(by_env, envs) ->
  list[(Env, Env, axis)]` (pairs differing in exactly one of city, date, north_deg, product variant whose outcome
  classes differ); `cover_report(sweep_result) -> dict` with the cover on the training half, its coverage on the
  held-out half, random-5 and the six-city-grid baselines (mean of 100 random draws, seed 0), and the
  non-degeneracy check.

- [ ] **Step 1: Failing tests:** greedy cover on a hand matrix picks the 2 environments a 4-automation example
  needs; non-degeneracy flags a matrix where one environment covers 60 %; witnesses find the single axis that flips
  a toy outcome; `sweep(..., limit=20)` on the tiny corpus of Task 1 returns outcomes for every env and a negative
  control rate.
- [ ] **Step 2-4: run, implement (multiprocessing pool over environments; each worker records the trace if missing
  and replays every bound automation in batches of 500), pass.**
- [ ] **Step 5: commit the protocol doc, then run the full sweep** (`--workers 8`); write `results/edt/sweep-*.json`
  and `results/edt/cover-*.json`; commit them with a results doc `docs/experiments/results-edt.md` (yield, flags by
  kind with ten examples, negative-control rate, cover size and held-out coverage, baselines).

### Task 8: Closed-loop confirmation

**Files:**
- Create: `vesper/edt/confirm.py`
- Test: `tests/edt/test_confirm.py`

**Interfaces:**
- Produces: `confirm(sweep_result, work, sample: int = 200, seed: int = 0) -> dict`: for a random sample of flagged
  automations and an equal sample of unflagged ones, run each bound automation alone in a full three-day
  `Session(automations=True)` of the standard home in the environments where its screening outcome differed (or two
  random environments for unflagged ones); report agreement of outcome classes between open and closed loop, and
  temperature-envelope violations (below 10 °C, above 30 °C in a room the automation's climate actions control).

- [ ] **Step 1: Failing test:** a lamp-on/lamp-off pair on illuminance that flaps closed-loop (its own lamp lifts the
  lux) but not open-loop is reported as a disagreement. **Step 2-4: run, implement, pass. Step 5: run on the sweep;
  add agreement to the results doc; commit.**

### Task 9: The necessity ladder

**Files:**
- Create: `vesper/edt/ladder.py`
- Modify: `vesper/cli.py` (`vesper edt ladder --sweep FILE`)
- Test: `tests/edt/test_ladder.py`

**Interfaces:**
- Consumes: `sweep(..., rung=r)` for r in 1, 2, 3 (Task 7), rung 4's result.
- Produces: `linter_features(raw: dict) -> dict[str, float]` (8 syntactic features: has sun trigger, has sun
  condition, numeric_state on each environment kind, time trigger, number of conditions);
  `ladder_report(results_by_rung: dict[int, dict]) -> dict`: rung 0 detector AUC (logistic regression, 5-fold, on
  "flagged at rung 4"), recall at precision 0.55; for rungs 1-3, the share of rung-4 flags reproduced, firing-time
  MAE against rung 4, rank correlation of time spreads.

- [ ] **Step 1: Failing tests** on a synthetic result set (AUC of a perfect feature is 1.0; MAE of identical rungs
  is 0). **Step 2-4: run, implement, pass. Step 5: run rungs 1-3 sweeps and the report; add to the results doc;
  commit.**

Not in this plan (spec §4 item 6, optional): the intent-labeling study. Findings are reported as divergences and
envelope violations, never as the authors' defects, unless the PI asks for that claim.

### Task 10: The paper

**Files:** `paper-latex/sections/*.tex` (local only, never pushed), `~/Research/AutoResearchClaw/runs/vesper-environment/`

- [ ] **Step 1:** Rewrite abstract, introduction (problem, insight, mechanism, RQs), design (the EDT section with the
  formal model of the spec §2), evaluation (RQ1-RQ4 from Tasks 7-9, the environment validation, the thermal ladder,
  the daylight bracket) and discussion (limits: open loop, standard-home binding, indoor temperature's grade); change
  "how faithful each twin is" to "faithful to its product's published definition".
- [ ] **Step 2:** Build the PDF (`latexmk`), flatten it (`runs/tools/flatten.py`), run ARC stage 18 (peer review) on
  it with the IoT-J review prompt; address its findings; report to the PI.
