"""Open-loop replay: what public automations would do in a recorded environment, without running the simulator.

`replay(home_yaml, trace, automations)` runs the given (bound) automations in VESPER's own engine
(`vesper.lab.automations.Engine`, permissive) against a trace from `vesper.edt.trace`, all in one engine over one
clock pass. Open loop: the sensors' reports are the trace's, whatever the automations do; their actions are recorded
(`Firing`), never executed, so no actuator ever changes.

How the trace is read:
- Sensors report on change, so a trace is a step function: each report is delivered at its `observed_t` on a logical
  clock and the entity holds its value until the next report. Only reports of sensor entities are delivered
  (`sensor`, `binary_sensor`, `event`): an actuator's report (a light, a lock, a thermostat) is the closed loop's.
- The entities start in the header's `initial_state` (state after the home started, before any report). A trace
  without it: an entity has no state until its first report.
- The engine's site is the header's (a rung-2 trace carries the site of a home that had none; a rung-1 trace has
  none, so a sun trigger or condition is refused there: see `Replay.refused`).
- Day 0 is the thermal warm-up: firings before day 1 are dropped, and so are firings at or after the run's end.
"""
from __future__ import annotations

import dataclasses
import functools
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

from vesper.edt.types import Firing

DAY_S = 86400
SENSOR_PLATFORMS = ("sensor", "binary_sensor", "event")  # the entities whose reports are replayed


@dataclass
class Replay:
    """The firings of each given automation (in time order; an automation that did not fire, or could not run, has
    []), why the engine refused the ones it did not run, and what permissive mode ran differently from the file."""
    firings: dict[str, list[Firing]]
    refused: dict[str, str] = field(default_factory=dict)
    approximations: dict[str, list[str]] = field(default_factory=dict)
    start_states: dict[str, str] = field(default_factory=dict)  # entity id -> its state when the run starts


class _TraceRuntime:
    """What the engine reads of a runtime: each twin's features, and its state when the engine starts."""

    def __init__(self, twins: dict, initial_state: dict):
        self.twins, self._initial = twins, initial_state

    def state(self, twin_id: str) -> dict:
        return dict(self._initial.get(twin_id, {}))


class TraceSession:
    """The part of a Lab session the engine uses, over a trace: a clock, the home, the hub's report feed, and an
    `automation_command` that writes a `Firing` down instead of commanding a twin."""

    def __init__(self, home, twins: dict, initial_state: dict, clock):
        self.home, self.clock = home, clock
        self.runtime = _TraceRuntime(twins, initial_state)
        self.firings: list[Firing] = []
        self.skipped: list[tuple[str, str, str]] = []
        self._hub: list = []
        self._last: tuple | None = None  # the command being written down, and which of its parts we have seen
        self._parts: set[str] = set()

    def subscribe_reports(self, fn) -> None:
        self._hub.append(fn)

    def deliver(self, event) -> None:
        for fn in self._hub:
            fn(event)

    def automation_skipped(self, automation: str, trigger: str, reason: str) -> None:
        self.skipped.append((automation, trigger, reason))

    def automation_command(self, automation: str, trigger: str, service: str, entity: str, twin_id: str,
                           feature: str, value) -> None:
        """One Firing per (action, target). A command the engine can execute arrives once per part (a light's state
        and its brightness): the parts of one command are one firing."""
        if twin_id:
            key = (self.clock.now(), automation, trigger, service, entity)
            if key == self._last and feature not in self._parts:
                self._parts.add(feature)
                return
            self._last, self._parts = key, {feature}
        else:
            self._last = None
        self.firings.append(Firing(self.clock.now(), automation, service, entity))


@functools.lru_cache(maxsize=4)
def _catalog(catalog_dir: str) -> dict:
    from vesper.twins.home import load_catalog
    return load_catalog(Path(catalog_dir))


@functools.lru_cache(maxsize=4)
def _home(path: str, mtime_ns: int, catalog_dir: str):
    """(the loaded Lab home, its twins by twin id): neither is changed by a replay, so a sweep loads them once."""
    from vesper.lab.home import load_lab_home
    from vesper.twins.twin import Twin
    home = load_lab_home(Path(path), _catalog(catalog_dir))
    twins = {f"{d.product}@{d.name}": Twin.from_vds(d.record, seed=home.seed, twin_id=f"{d.product}@{d.name}")
             for d in home.devices}
    return home, twins


def read_trace(trace: Path) -> tuple[dict, list[dict]]:
    """(header, reports) of a trace file, the reports in the order they reach the platform (`observed_t`; reports at
    one instant keep their recorded order)."""
    with gzip.open(trace, "rt") as f:
        header = json.loads(next(f))
        reports = [json.loads(line) for line in f]
    reports.sort(key=lambda r: r["observed_t"])  # stable
    return header, reports


def replay_detailed(home_yaml: Path, trace: Path, automations: dict[str, dict], *, until: float | None = None,
                    catalog_dir: str = "catalog/vds") -> Replay:
    """Replay `automations` (id -> bound automation) against `trace` of the EDT home `home_yaml`. `until`: the end
    of the run in logical seconds (default: the trace's RUN_DAYS days; a shorter trace made for a test needs it)."""
    from vesper.env.outdoor import RUN_DAYS
    from vesper.env.site import parse_site
    from vesper.lab.automations import Engine
    from vesper.twins.clock import LogicalClock
    from vesper.twins.runtime import Event
    home_yaml = Path(home_yaml)
    base, twins = _home(str(home_yaml.resolve()), home_yaml.stat().st_mtime_ns, catalog_dir)
    header, reports = read_trace(Path(trace))
    end = RUN_DAYS * DAY_S if until is None else until
    # The trace's own site and midnight start, whatever the home had; no file of automations.
    home = dataclasses.replace(base, site=parse_site(header.get("site")), start_time=0.0, automations=None)
    clock = LogicalClock()
    session = TraceSession(home, twins, header.get("initial_state") or {}, clock)
    labelled, given = [], list(automations.items())
    for key, raw in given:
        labelled.append({**raw, "alias": key} if isinstance(raw, dict) else raw)
    engine = Engine(session, permissive=True, automations=labelled)
    fed = {(e.twin_id, e.feature) for e in engine.entities.values() if e.platform in SENSOR_PLATFORMS}
    for r in reports:
        if (r["twin_id"], r["feature"]) in fed and r["observed_t"] <= end:
            event = Event(r["twin_id"], r["feature"], r["value"], r["t"], r["observed_t"], "trace")
            clock.call_at(r["observed_t"], lambda event=event: session.deliver(event))
    engine.start()
    start_states = dict(engine._state)  # noqa: SLF001 - the engine keeps no public view of its entity states
    clock.advance_to(end)
    out: dict[str, list[Firing]] = {key: [] for key, _ in given}
    for f in session.firings:
        if DAY_S <= f.t < end:
            out[f.automation].append(f)
    by_position = {f"automation {k + 1}": key for k, (key, _) in enumerate(given)}
    refused = {by_position.get(x["automation"], x["automation"]): x["reason"] for x in engine.refused}
    approximations = {a.name: a.approximations for a in engine.automations if a.approximations}
    return Replay(out, refused, approximations, start_states)


def replay(home_yaml: Path, trace: Path, automations: dict[str, dict], *, until: float | None = None,
           catalog_dir: str = "catalog/vds") -> dict[str, list[Firing]]:
    """The firings of each automation (by id) over the trace's days 1 and 2; see `replay_detailed`."""
    return replay_detailed(home_yaml, trace, automations, until=until, catalog_dir=catalog_dir).firings
