"""A running Lab home (spec §2, §3): the twins, the world, the rules, the player, and the trace.

The trace (`vesper-trace/1`) is one JSON object per line: a `meta` line first, then `input` (what a person
did), `rule` (a rule that fired), `world` (doors, rooms, arrivals) and `report` (what a device reported, as the
platform sees it), each with its world time `t`. On the logical clock, replaying the inputs reproduces the
device reports exactly (spec success criterion 4); `replay` and `compare_reports` check it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from vesper.lab.mapworld import MapWorld
from vesper.lab.rules import Rules
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime

TRACE_FORMAT = "vesper-trace/1"
PLAYER = "player"


class Session:
    def __init__(self, home, *, clock=None, world=None, trace: Path | None = None):
        self.home = home
        self.clock = clock or LogicalClock()
        self.runtime = Runtime(clock=self.clock, seed=home.seed)
        self.twin_ids = {d.name: self.runtime.add(d.record, twin_id=f"{d.product}@{d.name}", state=d.state).id
                         for d in home.devices}
        self._names = {tid: name for name, tid in self.twin_ids.items()}
        self.world = world or MapWorld(home.map, self.clock)
        for door, is_open in home.doors.items():
            self.world.add_door(door, is_open)
        self.records: list[dict] = []
        self._file = open(trace, "w") if trace else None  # noqa: SIM115 - closed in stop()
        self.rules = Rules(home, self.runtime, self.world, self.twin_ids,
                           record=lambda r: self._write({**r, "kind": "rule"}))

    # -- life cycle ---------------------------------------------------------------------------
    def start(self, spawn_player: bool = True) -> None:
        self._write({"kind": "meta", "format": TRACE_FORMAT, "home": str(self.home.path), "scene": self.home.scene,
                     "seed": self.home.seed, "start_time": self.home.start_time,
                     "devices": {d.name: d.product for d in self.home.devices}})
        self.runtime.subscribe(self._on_report)
        self.world.on_event(self._on_world)
        self.rules.start()
        if spawn_player:
            self.spawn(PLAYER, self.home.avatar["model"], self.home.avatar["start"])

    def stop(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        if hasattr(self.clock, "close"):
            self.clock.close()

    # -- inputs (traced) ----------------------------------------------------------------------
    def spawn(self, actor: str, model: str, at) -> None:
        self._input(actor, "spawn", {"model": model, "at": list(at)})
        self.world.spawn(actor, model, tuple(at))

    def move(self, dx: float, dz: float, actor: str = PLAYER):
        self._input(actor, "move", {"dx": dx, "dz": dz})
        return self.world.move(actor, dx, dz)

    def walk_to(self, point, actor: str = PLAYER):
        self._input(actor, "walk_to", {"point": list(point)})
        return self.world.walk_to(actor, tuple(point))

    def interact(self, target: str | None = None, actor: str = PLAYER) -> dict:
        outcome = self.rules.interact(actor, target)
        self._input(actor, "interact", {"target": target}, outcome=outcome)
        return outcome

    def wait(self, seconds: float) -> None:
        if hasattr(self.clock, "advance"):
            self.clock.advance(seconds)
        else:
            time.sleep(seconds)

    # -- the trace ----------------------------------------------------------------------------
    def _input(self, actor: str, op: str, args: dict, **extra) -> None:
        self._write({"kind": "input", "actor": actor, "op": op, "args": args, **extra})

    def _on_report(self, event) -> None:
        self._write({"kind": "report", "device": self._names.get(event.twin_id, event.twin_id),
                     "feature": event.feature, "value": event.value, "physical_t": event.physical_t,
                     "observed_t": event.observed_t, "cause": event.cause})

    def _on_world(self, event: dict) -> None:
        if event["event"] != "pose":  # poses follow from the inputs; the trace keeps what they caused
            self._write({"kind": "world", **event})

    def _write(self, record: dict) -> None:
        rec = {"t": round(self.clock.now(), 6), **record}
        self.records.append(rec)
        if self._file is not None:
            self._file.write(json.dumps(rec) + "\n")


def read_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def replay(records: list[dict], home) -> list[dict]:
    """Re-apply a trace's inputs at their times to a new session of `home` on the logical clock."""
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    for r in records:
        if r["kind"] != "input":
            continue
        s.clock.advance_to(r["t"])
        a = r["args"]
        if r["op"] == "spawn":
            s.spawn(r["actor"], a["model"], a["at"])
        elif r["op"] == "move":
            s.move(a["dx"], a["dz"], actor=r["actor"])
        elif r["op"] == "walk_to":
            s.walk_to(a["point"], actor=r["actor"])
        elif r["op"] == "interact":
            s.interact(a["target"], actor=r["actor"])
    if records:
        s.clock.advance_to(max(r["t"] for r in records))
    s.stop()
    return s.records


def compare_reports(original: list[dict], again: list[dict]) -> str | None:
    """None if both traces hold the same device reports in the same order; else the first difference."""
    keys = ("device", "feature", "value", "observed_t", "cause")
    a = [tuple(r[k] for k in keys) for r in original if r["kind"] == "report"]
    b = [tuple(r[k] for k in keys) for r in again if r["kind"] == "report"]
    for k, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"report {k}: the trace has {dict(zip(keys, x))}, the replay {dict(zip(keys, y))}"
    if len(a) != len(b):
        return f"the trace has {len(a)} reports, the replay {len(b)}"
    return None
