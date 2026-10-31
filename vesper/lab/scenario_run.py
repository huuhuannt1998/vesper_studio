"""Running a scenario (security-lab spec §4.5, plan W4a1): each condition (baseline, defense, attack, attack +
defense), `repeat` times, each run its own session, seed and trace; the timeline's actors act at their times, the
attack's effects are put on at theirs (inputs of the trace, so a replay puts them on again), and the defenses watch
in the defense condition (their control: no attack) and the attack + defense one. The claims are checked on each
run's trace, from its t0.

Actors are scripted people: the player and the scenario's `actors`. They walk, open and close doors and use devices
as the residents do (to the near side of a door first, through doors on the way). An actor without a key (the
scenario's actors, unless `key: true`) finds a locked door locked and does not unlock it.

Defenses, as the hub's edge sees the reports:
- `freshness` (the paper's timestamp checking): a report older than `max_age_s` when it reaches the hub raises an
  alert, and with `action: drop` is not delivered. A report's time is when the device sent it (`observed_t`), as a
  sender's timestamp would carry; in a live run the bridge publishes it as Zigbee2MQTT's `last_seen`.
- `physical_consistency` (only a simulator can offer it): a door that opened or closed in the world must be heard by
  the hub from its contact sensor within `within_s`; if not, an alert. Rooms and motion are not checked yet.
"""
from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from vesper.lab.home import spawn_point
from vesper.lab.predicates import Verdict, evaluate, events, matches
from vesper.lab.resident import RETRY_S, Resident
from vesper.lab.scenario import CONDITIONS
from vesper.lab.rules import is_locked
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock

SETTLE_S = 300.0  # how long a run goes on after its last step, unless the scenario says until when
DEFENDED = ("defense", "attack+defense")  # the conditions the defenses run in


@dataclass
class Run:
    condition: str
    repeat: int  # 1, 2, ...
    seed: int
    trace: Path
    records: list
    verdicts: list[Verdict]  # one per claim, in the scenario's order
    error: str | None = None  # why the run did not run (Home Assistant not ready, a step that failed): not judged


def run_scenario(scenario, out_dir: Path, *, root: Path | None = None, catalog_dir: Path | None = None) -> list[Run]:
    """Every condition and repeat. A live run (Home Assistant in the loop) starts VESPER's stack once, from `root`
    (`vesper setup ha`'s folder), for all of them; `catalog_dir` names the zigbee-herdsman version the bridge
    reports."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if scenario.runs.mode == "logical":
        return _runs(scenario, out_dir, _run_logical)
    from vesper.lab.live import lab_stack
    with lab_stack(Path(root or ".vesper"), scenario.home) as lab:
        return _runs(scenario, out_dir, lambda s, condition, k, trace: _run_live(
            s, condition, k, trace, lab, Path(catalog_dir or "catalog/vds")))


def _runs(scenario, out_dir: Path, run_once) -> list[Run]:
    """Each run in turn; one that fails (Home Assistant not ready in time, a step that raised) is kept as not run,
    with why, and the others are judged."""
    runs = []
    for condition in scenario.runs.conditions:
        for k in range(1, scenario.runs.repeat + 1):
            trace = out_dir / f"{condition}-{k}.jsonl"
            try:
                seed, records = run_once(scenario, condition, k, trace)
            except (RuntimeError, OSError, ValueError) as e:
                runs.append(Run(condition, k, None, trace, [], [], error=str(e)))
                continue
            runs.append(_judge(scenario, condition, k, seed, trace, records))
    return runs


def _judge(scenario, condition: str, k: int, seed, trace: Path, records: list) -> Run:
    return Run(condition, k, seed, trace, records, [evaluate(c.predicate, judged(records)) for c in scenario.claims])


def judged(records: list[dict]) -> list[dict]:
    """What a run's claims are judged on: its records from t0, when its timeline began (a live run's Home
    Assistant reacts to the new session's first states before that)."""
    t0 = next((r["t0"] for r in records if r.get("kind") == "scenario"), 0.0)
    return [r for r in records if r.get("t", 0.0) >= t0]


def _run_logical(scenario, condition: str, k: int, trace: Path) -> tuple[int, list]:
    home = dataclasses.replace(scenario.home, seed=scenario.home.seed + k - 1)
    session = Session(home, clock=LogicalClock(), trace=trace)
    if condition in DEFENDED:
        for d in scenario.defenses:
            DEFENSES[d.kind](session, d)
    session.subscribe_reports(lambda event: session.scenario_record({
        "kind": "heard", "device": session.device_name(event.twin_id), "feature": event.feature,
        "value": event.value}))
    session.start(spawn_player=False, automations=True)
    session.scenario_record({"kind": "scenario", "t0": 0.0, "condition": condition, "repeat": k,
                             "defenses": _defenses(scenario, condition)})
    _play(scenario, session, condition, t0=0.0)
    try:
        session.clock.advance_to(_until(scenario))
    finally:
        session.stop()
    return home.seed, session.records


def _run_live(scenario, condition: str, k: int, trace: Path, lab, catalog_dir: Path) -> tuple[int, list]:
    """One run with Home Assistant in the loop: a wall-clock session on the live network, its own hub, the stack's
    relay (cleared of the last run's effects); the timeline starts once Home Assistant shows every device (t0)."""
    from vesper.exposure import mqtt
    from vesper.lab.live import lab_hub
    from vesper.twins.clock import WallClock
    home = dataclasses.replace(scenario.home, seed=scenario.home.seed + k - 1)
    session = Session(home, clock=WallClock(), trace=trace, live_network=True)
    session.relay = lab.relay
    lab.sink.attach(session)
    lab.relay.clear()
    session.start(spawn_player=False)  # Home Assistant runs the automations
    epoch0 = time.time() - session.clock.now()
    watch = None
    try:
        with lab_hub(session, lab.port, catalog_dir,
                     last_seen=lambda t: datetime.fromtimestamp(epoch0 + t, UTC).isoformat()) as hub:
            _wait_for_home_assistant(lab.ha, hub)
            watch = mqtt.connect("127.0.0.1", lab.hub_port, "vesper-scenario-watch")
            hub_watch = HubWatch(session, watch)  # what Home Assistant hears: `heard` records, the defenses
            if condition in DEFENDED:
                for d in scenario.defenses:
                    LIVE_DEFENSES[d.kind](session, d, hub_watch)
            t0 = session.clock.now()
            session.scenario_record({"kind": "scenario", "t0": t0, "condition": condition, "repeat": k,
                                     "defenses": _defenses(scenario, condition)})
            _play(scenario, session, condition, t0)
            end = t0 + _until(scenario)
            while session.clock.now() < end:
                time.sleep(0.2)
    finally:
        if watch is not None:
            mqtt.disconnect(watch)
        session.stop()
    _check_clock(session)
    return home.seed, session.records


def start_scenario(session, scenario, condition: str, hub_watch: HubWatch | None = None) -> float:
    """A scenario in a session that is already running (`vesper lab play --scenario`, plan W4c): the defenses its
    condition runs, its actors in their places and its timeline from now (t0), which it returns. Without a hub the
    defenses watch the twins' reports (the session runs the automations); with one, `hub_watch` gives them what Home
    Assistant hears. The caller starts the session without its own player: the scenario's player is scripted."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; the conditions are {', '.join(CONDITIONS)}")
    if condition in DEFENDED:
        for d in scenario.defenses:
            if hub_watch is None:
                DEFENSES[d.kind](session, d)
            else:
                LIVE_DEFENSES[d.kind](session, d, hub_watch)
    t0 = session.clock.now()
    session.scenario_record({"kind": "scenario", "t0": t0, "condition": condition, "title": scenario.title,
                             "defenses": _defenses(scenario, condition)})
    _play(scenario, session, condition, t0)
    return t0


def _check_clock(session) -> None:
    """A step that raised on the wall clock's thread (an effect, an actor's action) is kept in `clock.errors`; the
    run did not run as the scenario says, so it fails, with the error."""
    if session.clock.errors:
        raise RuntimeError(f"a step failed: {'; '.join(session.clock.errors[:3])}")


def _defenses(scenario, condition: str) -> list[str]:
    return [d.kind for d in scenario.defenses] if condition in DEFENDED else []


def _play(scenario, session, condition: str, t0: float) -> None:
    """The actors in their places, the timeline's actions and (attack conditions) the attack's effects, at their
    times after t0."""
    home, clock = session.home, session.clock
    actors = {"player": ScenarioActor(session, "player", home.avatar["model"], home.avatar["start"], key=True)}
    for a in scenario.actors.values():
        actors[a.name] = ScenarioActor(session, a.name, a.model, spawn_point(home.map, a.start), key=False)
    for actor in actors.values():
        actor.start()
    for a in scenario.timeline:
        (verb, target), = a.do.items()
        clock.call_at(t0 + a.at, lambda a=a, verb=verb, target=target: actors[a.actor].do(verb, target))
    if condition in ("attack", "attack+defense"):
        for step in scenario.attack:
            effect = step.effect if step.effect.until is None else dataclasses.replace(
                step.effect, until=t0 + step.effect.until)
            clock.call_at(t0 + step.at, lambda effect=effect: session.network_effect(effect))


MAIN_PLATFORMS = ("binary_sensor", "light", "lock", "switch", "climate", "cover", "fan")


def _wait_for_home_assistant(ha, hub, timeout: float = 90.0) -> None:
    """Until Home Assistant shows each Zigbee device's main entities and each joined Wi-Fi device's (the first run:
    discovery; later runs: the new session's retained states)."""
    want = [e.entity_id for entities in hub.bridge.expected.values() for e in entities
            if e.platform in MAIN_PLATFORMS]
    wifi = getattr(hub, "wifi", None)
    if wifi is not None:
        want += [entity for name in wifi.devices for entity in wifi.entities(name).values()]
    deadline = time.monotonic() + timeout
    while True:
        missing = [e for e in want if (st := ha.state(e)) is None or st["state"] == "unavailable"]
        if not missing:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"Home Assistant did not show {', '.join(missing[:3])} within {timeout:.0f} s")
        time.sleep(0.5)


def _until(scenario) -> float:
    if scenario.runs.until is not None:
        return scenario.runs.until
    ends = [a.at for a in scenario.timeline] + [s.effect.until or s.at + s.effect.hold_s for s in scenario.attack]
    return max(ends, default=0.0) + SETTLE_S


class ScenarioActor(Resident):
    """A scripted person: no routine, only the scenario's actions, as a resident carries them out."""

    def __init__(self, session, name: str, model: str, at, key: bool):
        super().__init__(session, name, model, routine=None)
        self.at, self.key = tuple(at), key
        self._dos: list[tuple] = []  # the scenario's own steps still queued (a stopped walk leaves them)

    def start(self) -> None:
        self.session.spawn(self.name, self.model, self.at)

    def do(self, verb: str, target) -> None:
        if verb == "walk":
            step = ("walk_room", target["room"] if isinstance(target, dict) else target, "scenario")
        elif verb in ("open", "close"):
            step = ("door", target, verb == "open", "scenario")
        else:
            step = ("toggle", target)
        self._dos.append(step)
        self._steps.append(step)
        if not self._waiting:
            self._next()

    def _run(self, step: tuple) -> None:
        self._dos = [d for d in self._dos if d is not step]
        if step[0] == "toggle":
            self._use(step[1], not bool(self._state(step[1])), "scenario")
        else:
            super()._run(step)

    def _door(self, door: str, want_open: bool, reason: str, tried: bool = False) -> None:
        if self.key:
            super()._door(door, want_open, reason, tried)
            return
        if self.world.doors.get(door) == want_open:
            return
        outcome = self.session.interact(door, actor=self.name, why=self._why(reason))
        if outcome["ok"] or tried or outcome["result"] == "locked":  # no key: a locked door stays locked
            return
        self._steps.appendleft(("door", door, want_open, reason, True))
        if outcome["result"] == "obstructed":
            self._later(RETRY_S)
        else:
            self._steps.appendleft(("walk_point", self._side(door, self._here()), reason))

    def _use(self, device: str, want: bool, reason: str, tries: int = 0, task: str | None = None) -> None:
        if not self.key and is_locked(self.rules.twin(device)) is True:
            # no key: a locked lock on the way (a walk through its door) stops that walk there, and the trace says
            # so; the scenario's next actions stay
            self.session.note({"resident": self.name, "decision": "no key", "device": device, "reason": reason})
            while self._steps and not any(self._steps[0] is d for d in self._dos):
                self._steps.popleft()
            return
        super()._use(device, want, reason, tries, task)

    def _on_report(self, event) -> None:
        """A scripted actor acts only as the scenario says (no fleeing an alarm)."""


class Freshness:
    def __init__(self, session, defense):
        self.session, self.defense = session, defense
        session.report_filters.append(self.check)

    def check(self, event) -> bool:
        age = self.session.clock.now() - event.observed_t
        if age <= self.defense.max_age_s:
            return True
        device = self.session.device_name(event.twin_id)
        self.session.scenario_record({"kind": "alert", "defense": "freshness", "device": device,
                                     "feature": event.feature, "value": event.value, "age_s": round(age, 6),
                                     "max_age_s": self.defense.max_age_s})
        if self.defense.action == "drop":
            self.session.scenario_record({"kind": "net", "link": "hub", "event": "dropped", "device": device,
                                         "feature": event.feature, "direction": "up", "effect": "freshness"})
            return False
        return True


class PhysicalConsistency:
    """The world's doors against what the hub hears from the contact sensors mounted on them."""

    def __init__(self, session, defense, watch=None):
        """`watch`: in a live run, what Home Assistant hears (a HubWatch); else the hub's in-process reports."""
        self.session, self.defense = session, defense
        self.heard: dict[str, float] = {}  # device -> when the hub last heard its contact
        self.sensors = {d.door: d.name for d in session.home.devices
                        if d.door is not None and any(f["name"] == "contact" for f in d.record["features"])}
        session.world.on_event(self._on_world)
        if watch is not None:
            watch.listeners.append(lambda device, payload: self._hear(device) if "contact" in payload else None)
        else:
            session.subscribe_reports(self._on_report)

    def _on_report(self, event) -> None:
        if event.feature == "contact":
            self._hear(self.session.device_name(event.twin_id))

    def _hear(self, device: str) -> None:
        self.heard[device] = self.session.clock.now()

    def _on_world(self, event: dict) -> None:
        if event["event"] != "door" or event["id"] not in self.sensors:
            return
        device, clock, since = self.sensors[event["id"]], self.session.clock, self.session.clock.now()

        def check():
            if self.heard.get(device, float("-inf")) < since:
                self.session.scenario_record({"kind": "alert", "defense": "physical_consistency", "device": device,
                                             "door": event["id"], "open": event["open"], "world_t": since,
                                             "waited_s": self.defense.within_s})
        clock.call_at(since + self.defense.within_s, check)


DEFENSES = {"freshness": Freshness, "physical_consistency": PhysicalConsistency}


class HubWatch:
    """What Home Assistant hears, from its own edge (a live run): an MQTT client on the relay's hub link, the
    devices' state messages handed to the session's clock thread and written as `heard` records. The retained
    states a new subscription gets are old news, not reports, and are skipped."""

    def __init__(self, session, client, base_topic: str = "zigbee2mqtt"):
        from vesper.lab.hub import ClockThreadClient
        self.session, self.base = session, base_topic
        self.listeners: list = []  # fn(device, payload)
        self.client = ClockThreadClient(client, session.clock)
        self.client.on_message = self._on_message
        self.client.subscribe(f"{base_topic}/+")

    def _on_message(self, client, userdata, msg) -> None:
        device = msg.topic.split("/", 1)[1]
        if getattr(msg, "retain", False) or device == "bridge":
            return
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        if isinstance(payload, dict):
            self.session.scenario_record({"kind": "heard", "device": device, "values": payload})
            for fn in list(self.listeners):
                fn(device, payload)


class LiveFreshness:
    """Freshness at Home Assistant's edge: a report's age is now less its Zigbee2MQTT `last_seen`; it alerts (a
    watcher cannot take a message away from Home Assistant)."""

    def __init__(self, session, defense, watch: HubWatch, now=time.time):
        self.session, self.defense, self._now = session, defense, now
        watch.listeners.append(self._check)

    def _check(self, device: str, payload: dict) -> None:
        seen = payload.get("last_seen")
        if not isinstance(seen, str):
            return
        try:
            age = self._now() - datetime.fromisoformat(seen).timestamp()
        except ValueError:
            return
        if age > self.defense.max_age_s:
            self.session.scenario_record({"kind": "alert", "defense": "freshness", "device": device,
                                         "age_s": round(age, 6), "max_age_s": self.defense.max_age_s})


LIVE_DEFENSES = {"freshness": lambda session, d, watch: LiveFreshness(session, d, watch),
                 "physical_consistency": lambda session, d, watch: PhysicalConsistency(session, d, watch=watch)}


# -- the report ----------------------------------------------------------------------------------------------
def rejudge(scenario, out_dir: Path) -> list[Run]:
    """The claims checked again on the traces a run saved (the scenario's claims may have changed since)."""
    from vesper.lab.session import TraceError, read_trace
    runs = []
    for condition in scenario.runs.conditions:
        for k in range(1, scenario.runs.repeat + 1):
            trace = Path(out_dir) / f"{condition}-{k}.jsonl"
            if not trace.exists():
                runs.append(Run(condition, k, None, trace, [], [], error=f"no trace ({trace.name})"))
                continue
            try:
                records = read_trace(trace)
            except TraceError as e:
                runs.append(Run(condition, k, None, trace, [], [], error=str(e)))
                continue
            seed = next((r.get("seed") for r in records if r["kind"] == "meta"), None)
            runs.append(_judge(scenario, condition, k, seed, trace, records))
    return runs


def report(scenario, runs: list[Run]) -> tuple[str, dict]:
    """The report (markdown) and its summary: the gaps first, then per claim and condition whether it held, in how
    many runs, the records that decided it in the first run, and whether the paper's expectation was reproduced."""
    paper = scenario.source.get("paper") or {}
    lines = [f"# {scenario.title}", ""]
    if paper:
        lines += [", ".join(str(paper[k]) for k in ("title", "venue", "doi") if paper.get(k)) + ".", ""]
    lines += [f"{scenario.runs.mode.capitalize()} runs, {scenario.runs.repeat} per condition: "
              f"{', '.join(scenario.runs.conditions)}.", "", "## Gaps", ""]
    lines += [f"- {g.get('what')}: {g.get('why')}" for g in scenario.gaps] or ["None recorded."]
    failed = [r for r in runs if r.error]
    if failed:
        lines += ["", "## Runs not run", "", "Not judged; each claim's counts below are over the runs that ran:", ""]
        lines += [f"- {r.condition} {r.repeat}: not run: {r.error}" for r in failed]
    refused = _refused(runs)
    if refused:
        lines += ["", "## Automations refused", "",
                  "VESPER's engine did not run these, so no claim about them can hold:", ""]
        lines += [f"- {r['automation']}: {r['reason']}" for r in refused]
    lines += ["", "## Claims", ""]
    claims = []
    for i, claim in enumerate(scenario.claims):
        rows, conditions, wrong = [], {}, []
        for condition in scenario.runs.conditions:
            these = [r for r in runs if r.condition == condition and not r.error]
            not_run = sum(1 for r in runs if r.condition == condition and r.error)
            held = sum(r.verdicts[i].holds for r in these)
            expected = claim.conditions[condition]
            conditions[condition] = {"expected": expected, "held": held, "runs": len(these),
                                     **({"not_run": not_run} if not_run else {})}
            if not these:
                wrong.append(f"{condition}: no run ran")
            elif held != (len(these) if expected else 0):
                wrong.append(f"{condition}: expected {'to hold' if expected else 'not to hold'}, held in {held} "
                             f"of {len(these)}")
            first = these[0].verdicts[i] if these else None
            deciding = "; ".join(describe(r) for r in first.records[:4]) if first else ""
            rows.append(f"| {condition} | {'holds' if expected else 'does not hold'} | {held}/{len(these)} | "
                        f"{deciding} |")
        verdict = "**reproduced**" if not wrong else f"**not reproduced** ({'; '.join(wrong)})"
        unmatched = _unmatched(claim, runs)
        lines += [f"### {claim.claim}: {verdict}", ""]
        lines += [f"Its event {json.dumps(e)} matched nothing in any run: the claim may hold or fail for the wrong "
                  "reason (a name or value that never occurs, a device that never acts)." for e in unmatched]
        lines += ([""] if unmatched else []) + ["| condition | expected | held | deciding records (first run) |",
                                                "|---|---|---|---|", *rows, ""]
        claims.append({"claim": claim.claim, "reproduced": not wrong, "conditions": conditions, "cites":
                       list(claim.cites), "unmatched": unmatched})
    summary = {"scenario": str(scenario.path), "title": scenario.title, "gaps": scenario.gaps, "refused": refused,
               "claims": claims,
               "runs": [{"condition": r.condition, "repeat": r.repeat, "seed": r.seed, "trace": r.trace.name,
                         "holds": [v.holds for v in r.verdicts], **({"error": r.error} if r.error else {})}
                        for r in runs]}
    return "\n".join(lines), summary


def _unmatched(claim, runs: list[Run]) -> list[dict]:
    """The claim's events no record matched in any run that ran."""
    ran = [judged(r.records) for r in runs if not r.error]
    out = []
    for e in events(claim.predicate):
        if e not in out and not any(matches(e, x) for records in ran for x in records):
            out.append(e)
    return out


def _refused(runs: list[Run]) -> list[dict]:
    """The automations VESPER's engine refused in any run (the same file every run: once each)."""
    out = []
    for run in runs:
        meta = next((r for r in run.records if r.get("kind") == "meta"), {})
        for x in (meta.get("automations") or {}).get("refused", []):
            if x not in out:
                out.append(x)
    return out


def describe(r: dict) -> str:
    """A trace record in a few words."""
    t, kind = f"t={r.get('t', 0):.2f}", r.get("kind")
    if kind == "input":
        args, out = r.get("args") or {}, r.get("outcome") or {}
        if r.get("op") == "command":
            return f"{t} hub command {args.get('device')} {args.get('feature')}={args.get('value')}"
        return f"{t} {r.get('actor')} {r.get('op')} {args.get('target', args.get('device', ''))}: " \
               f"{out.get('result', '')}".rstrip(": ")
    if kind == "automation":
        return f"{t} automation {r.get('automation')}: {r.get('device')} {r.get('feature')}={r.get('value')}"
    if kind == "report":
        return f"{t} report {r.get('device')} {r.get('feature')}={r.get('value')}"
    if kind == "heard":
        if "values" in r:
            return f"{t} hub heard {r.get('device')} {json.dumps(r['values'])}"
        return f"{t} hub heard {r.get('device')} {r.get('feature')}={r.get('value')}"
    if kind == "alert":
        return f"{t} alert {r.get('defense')}: {r.get('device')}"
    if kind == "world":
        if r.get("event") == "door":
            return f"{t} door {r.get('id')} {'opened' if r.get('open') else 'closed'}"
        return f"{t} {r.get('actor')} enters {r.get('room')}"
    if kind == "net":
        return f"{t} net {r.get('link')} {r.get('event')} {r.get('device')} {r.get('feature')}"
    return f"{t} {kind}"
