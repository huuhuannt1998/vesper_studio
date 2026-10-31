# W4a1: the Scenario Engine and the Gold Phantom-Delay Scenarios, as built

> Written after a test-driven build in a scratch tree, not before it (as plan W7a2): each task names its files, the
> interfaces it produces and the tests that drove it.

**Goal:** an attack from a research paper and its defenses, set up on a VESPER home as a plain scenario file, run
under baseline, attack and attack + defense, and reported claim by claim; the Phantom-Delay paper's Cases 1 and 3 as
the gold scenarios, logical and live.

**Architecture:** `vesper/lab/scenario.py` loads `vesper-scenario/1` and checks every name against the home;
`vesper/lab/predicates.py` evaluates claims on a trace; `vesper/lab/scenario_run.py` runs each condition and repeat
(logical: VESPER's engine and the in-process link layer; live: Home Assistant and the relay, from
`vesper/lab/live.py`), with scripted actors and the defenses, and writes the report.

**Tech stack:** Python 3.11+, PyYAML, paho-mqtt, Mosquitto, Home Assistant 2026.10.2.

**Spec:** `docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md` §4 (the decisions of this build: §4.7).

## Global Constraints

- Scenarios name the home's devices, links, doors, rooms and automations, never a host, a port or a command (spec §7);
  unknown fields are refused.
- VESPER's own services only: Mosquitto 18830, Home Assistant 18123, the relay on 127.0.0.1; never 1883 or 8123.
- Every effect is a trace input (a run's trace replays like any session); a defense's alerts are ground truth.
- Gaps are written in the file and listed first in the report, never faked.

## Review Focus

1. Times: a scenario's HH:MM:SS against the home's start time, a run past midnight, `until` before the last step.
2. Live runs on a Home Assistant whose entities exist from an earlier run (stale states, the first run's discovery),
   and the readiness wait timing out.
3. Two actors acting on the same door at once, or an actor whose walk is blocked: the scenario's later actions.
4. Defenses with an effect on a link other than the coordinator (a Zigbee hop, a Wi-Fi link) in a live run.
5. A claim whose predicate never matches anything in any condition (a misnamed value such as `ON` vs `on`).

---

### Task 1: the scenario file

**Files:** create `vesper/lab/scenario.py`, `tests/lab/test_scenario.py`; modify `vesper/lab/home.py` (`patch`),
`tests/lab/homes.py` (a Zigbee test lock).

**Produces:** `load_scenario(path, catalog, mode=None) -> Scenario` (title, source, home, actors, timeline: `Action`,
attack: `AttackStep(at, vantage, effect: Effect, cites)`, defenses: `Defense`, claims: `Claim`, runs: `Runs`, gaps);
`ScenarioError`; `load_lab_home(path, catalog, patch=None)`.

**Tests:** a scenario loads with its times in seconds after the start; `setup.devices` for this scenario only; 15
named mistakes (format, link, device, feature, doorway, time, actor, W4a2 vantages and defenses, run mode,
conditions, expectations, predicates); no host or port; relative paths; a missing automations file; the mode
override; a live run's freshness cannot drop.

### Task 2: predicates

**Files:** create `vesper/lab/predicates.py`, `tests/lab/test_predicates.py`.

**Produces:** `evaluate(predicate, trace) -> Verdict(holds, records, detail)`; `check_predicate(predicate, home,
actors, where)`; `PredicateError`. Events: an actor's `opens`/`closes`/`enters`/`uses`, `door`, `report`, `action`
(VESPER's engine or Home Assistant), `alert`, `net`; forms `before`, `after`, `gap`, `count`, `never`, `all`, `any`,
`not`.

### Task 3: runs on the logical clock

**Files:** create `vesper/lab/scenario_run.py`, `tests/lab/test_scenario_run.py`; modify `vesper/lab/session.py`
(`report_filters`, one hub delivery point, `scenario_record`).

**Produces:** `run_scenario(scenario, out_dir, *, root=None, catalog_dir=None) -> list[Run]`; `ScenarioActor`;
`Freshness`, `PhysicalConsistency`.

**Tests:** Case 3 on the test house reproduces (the intruder gets in only with the door's report held; freshness and
physical consistency alert in attack + defense only); each run its own trace and seed; the attack an input; an
intruder without a key finds the door locked and never unlocks it, even walking through the door.

### Task 4: the report and the CLI

**Files:** modify `vesper/lab/scenario_run.py` (`report`, `rejudge`, `describe`), `vesper/cli.py`
(`vesper lab scenario check|run|report`); create `tests/lab/test_cli_scenario.py`.

**Produces:** `report(scenario, runs) -> (markdown, summary)`: gaps, refused automations, then per claim and
condition: expected, held k/n, the deciding records, reproduced or not and why.

### Task 5: live runs

**Files:** create `vesper/lab/live.py` (`lab_stack`, `lab_hub`, `zhc_version`, moved from the CLI),
`tests/lab/test_scenario_watch.py`, `tests/lab/test_scenario_live.py`; modify `vesper/exposure/z2m_bridge.py`
(`last_seen`, `OFFLINE_WILL`), `vesper/lab/hub.py`, `vesper/lab/relay.py` (a link added again is the same link),
`vesper/lab/scenario_run.py` (`_run_live`, `HubWatch`, `LiveFreshness`), `vesper/cli.py` (`--mode`, `--root`).

**Tests:** the bridge's `last_seen`; the relay's repeated link; the hub-edge watch (a stale `last_seen` alerts,
retained state does not; physical consistency through the watch); the gold scenarios live (opt-in).

### Task 6: the gold scenarios and documentation

**Files:** create `examples/lab/scenarios/phantom-delay/{home.yaml,automations.yaml,case1-state-update-delay.yaml,
case3-action-delay.yaml}`, `tests/lab/test_example_scenarios.py`; README (a scenarios section, the table, layout,
roadmap), the spec (§4.7, progress), this plan.

**Result (logical, 5 runs per condition, the real catalog):** Case 1: 4 of 4 claims reproduced; Case 3: 6 of 6.

## Rulings

- W4a is split (W4a1 now, W4a2 the other vantages and defenses): the paper's demo needs one attack and one defense.
- Case 10 waits for W7a3 (PI's choice), recorded in spec §4.7.
- Freshness measures from `observed_t` (the bridge's time, as a sender's timestamp), not the physical change: a
  debounced report would otherwise look stale. Cost if wrong: a defense that stamps at the physical change alerts
  more.
- A live run's freshness only alerts; dropping before Home Assistant is W4a2's.
- Physical consistency checks doors only; rooms and motion need W8's sensing model.
- A scenario's own actors have no key and never unlock a lock; the player has one.
- The paper's voice notification is a hall light (VESPER's engine has no notify service); written as a gap.
- Live runs start the timeline at t0, once Home Assistant shows every main entity; the trace records t0.
- `state_at` waits for W4a2.
- The stack and hub helpers moved from the CLI to `vesper/lab/live.py` (shared by play, look and scenarios).
- The plan is as-built (process).
