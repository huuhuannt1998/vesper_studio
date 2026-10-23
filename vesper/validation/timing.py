"""L3 timing fidelity (spec §1 criterion 4, §4 row L3). Design and pre-registered tolerances:
docs/experiments/l3-timing-design.md.

Latency (T1-T3) is measured in wall-clock mode at the MQTT boundary: from a physical stimulus to a
separate subscriber receiving the twin's report, through a real broker in the live run.

Rule conformance (R1-R4) runs every twin on the logical clock under a seeded stimulus and command
script and checks each report against the feature's VDS reporting rule, exactly:
- R1: at least `min_s` between consecutive reports;
- R2: at most `max_s` of silence, the end of the run included;
- R3: a non-periodic report changes the value by at least `change` since the last report;
- R4: occupancy clears exactly OCCUPANCY_TIMEOUT_S after the last presence.
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field

from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock, WallClock
from vesper.twins.latency import LatencyModel, Path
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Event, Runtime
from vesper.twins.twin import stable_seed

EPS = 1e-9
PRESENCE = ("occupancy", "presence")


def _significant(old, new, change) -> bool:
    if isinstance(new, (int, float)) and not isinstance(new, bool) and isinstance(old, (int, float)):
        return abs(new - old) >= change - EPS if change else new != old
    return new != old


@dataclass
class RuleChecker:
    end_t: float
    rules: dict[tuple[str, str], dict]
    initial: dict[tuple[str, str], object]
    added: dict[str, float]
    presence_values: dict[tuple[str, str], tuple] = field(default_factory=dict)  # (value_on, value_off)
    violations: list[dict] = field(default_factory=list)
    reports: int = 0
    r4_checked: int = 0

    def __post_init__(self):
        self._last: dict[tuple[str, str], tuple[float, object]] = {}
        self._presence: dict[str, float] = {}

    def on_presence(self, twin_id: str, t: float) -> None:
        self._presence[twin_id] = t

    def _flag(self, rule: str, key, t: float, detail: str) -> None:
        self.violations.append({"rule": rule, "twin": key[0], "feature": key[1], "t": t, "detail": detail})

    def on_event(self, e: Event) -> None:
        key = (e.twin_id, e.feature)
        rule, values = self.rules.get(key), self.presence_values.get(key)
        if rule is None and values is None:
            return
        t = e.observed_t
        prev_t, prev_value = self._last.get(key, (None, self.initial.get(key)))
        if rule is not None:
            self.reports += 1
            since = self.added.get(e.twin_id, 0.0) if prev_t is None else prev_t
            if prev_t is not None and t - prev_t < rule["min_s"] - EPS:
                self._flag("R1 min_s", key, t, f"{t - prev_t:.3f} s after the last report; min_s {rule['min_s']}")
            if e.cause != "periodic" and not _significant(prev_value, e.value, rule["change"]):
                self._flag("R3 change", key, t, f"{prev_value!r} -> {e.value!r}; change {rule['change']}")
            if t - since > rule["max_s"] + EPS:
                self._flag("R2 max_s", key, t, f"{t - since:.3f} s of silence; max_s {rule['max_s']}")
        if values and e.cause != "periodic" and e.value == values[1] and prev_value == values[0]:
            self.r4_checked += 1
            shown = self._presence.get(e.twin_id)
            if shown is None or abs(t - shown - OCCUPANCY_TIMEOUT_S) > EPS:
                self._flag("R4 occupancy timeout", key, t,
                           f"cleared {t - shown if shown is not None else float('nan'):.3f} s after the last "
                           f"presence; expected {OCCUPANCY_TIMEOUT_S}")
        self._last[key] = (t, e.value)

    def finish(self) -> dict:
        for key, rule in self.rules.items():
            last_t = self._last.get(key, (self.added.get(key[0], 0.0), None))[0]
            if self.end_t - last_t > rule["max_s"] + EPS:
                self._flag("R2 max_s", key, self.end_t, f"{self.end_t - last_t:.3f} s of silence at the end; "
                                                        f"max_s {rule['max_s']}")
        for key, (on, _) in self.presence_values.items():
            shown = self._presence.get(key[0])
            last = self._last.get(key, (None, self.initial.get(key)))[1]
            if last == on and shown is not None and shown + OCCUPANCY_TIMEOUT_S <= self.end_t - EPS:
                self._flag("R4 occupancy never cleared", key, self.end_t,
                           f"still on {self.end_t - shown:.3f} s after the last presence")
        return {"reports": self.reports, "violations": self.violations, "r4_checked": self.r4_checked}


def _script(rt: Runtime, twin, rng: random.Random, duration_s: float, checker: RuleChecker) -> None:
    """Schedule a seeded mix of physical stimuli and platform commands for one twin."""
    feats = twin.features
    actions = []
    if any(n in feats for n in ("temperature", "humidity", "illuminance", "illuminance_lux", "local_temperature")):
        actions.append("ambient")
    if any(n in feats and feats[n]["type"] == "binary" for n in PRESENCE):
        actions.append("presence")
    if "contact" in feats:
        actions.append("contact")
    if "power" in feats:
        actions.append("load")
    if "action" in feats and feats["action"]["type"] == "enum" and feats["action"].get("values"):
        actions.append("button_press")
    settable = [n for n, f in feats.items() if "set" in f["access"] and f["type"] in ("binary", "numeric", "enum")
                and "#" not in n]
    if settable:
        actions.append("command")
    if not actions:
        return
    t = 0.0
    while True:
        t += rng.expovariate(1 / 120.0)
        if t >= duration_s:
            return
        kind = rng.choice(actions)
        rt.clock.call_at(t, _action(rt, twin, kind, rng.random(), settable, checker))


def _action(rt: Runtime, twin, kind: str, u: float, settable: list[str], checker: RuleChecker):
    def run():
        state = rt.state(twin.id)
        if kind == "ambient":
            s = {"type": "ambient"}
            for key, names, step in (("temperature", ("temperature", "local_temperature"), 3.0),
                                     ("humidity", ("humidity",), 8.0), ("illuminance", ("illuminance",
                                                                                       "illuminance_lux"), 400.0)):
                name = next((n for n in names if n in twin.features), None)
                if name is not None:
                    s[key] = float(state[name]) + (u - 0.5) * step
            rt.stimulate(twin.id, s)
        elif kind == "presence":
            checker.on_presence(twin.id, rt.clock.now())
            rt.stimulate(twin.id, {"type": "presence"})
        elif kind == "contact":
            rt.stimulate(twin.id, {"type": "contact", "state": "opened" if u < 0.5 else "closed"})
        elif kind == "load":
            rt.stimulate(twin.id, {"type": "load", "watts": round(u * 2000, 1)})
        elif kind == "button_press":
            values = twin.features["action"]["values"]
            rt.stimulate(twin.id, {"type": "button_press", "action": values[int(u * len(values)) % len(values)]})
        else:
            name = settable[int(u * len(settable)) % len(settable)]
            f = twin.features[name]
            if f["type"] == "binary":
                value = f["value_on"] if u < 0.5 else f["value_off"]
            elif f["type"] == "enum":
                value = f["values"][int(u * 997) % len(f["values"])] if f.get("values") else None
            else:
                lo, hi = f.get("range", (0, 100))
                value = lo + (hi - lo) * u
                value = round(value) if isinstance(lo, int) and isinstance(hi, int) else value
            if value is not None:
                rt.platform_set(twin.id, name, value)
    return run


def run_rules(records: list[dict], *, seed: int, duration_s: float) -> dict:
    """R1-R4 over every L1 twin with reporting rules, on the logical clock; exact, so the pass bar is 100%."""
    rt = Runtime(clock=LogicalClock(), seed=seed)
    twins = [rt.add(r) for r in sorted(records, key=lambda r: r["id"]) if r["fidelity"]["reached"]["L1"]]
    rules, initial, presence, dropped = {}, {}, {}, 0
    for twin in twins:
        for name, rule in twin.vds.get("behavior", {}).get("reporting", {}).items():
            if name in twin.features:
                rules[(twin.id, name)] = rule
                initial[(twin.id, name)] = twin.state[name]
            else:
                dropped += 1  # a rule for a feature the twin does not have: counted, not silently lost
        for name in PRESENCE:  # R4 holds whether or not the feature has a reporting rule
            f = twin.features.get(name)
            if f and f["type"] == "binary":
                presence[(twin.id, name)] = (f["value_on"], f["value_off"])
                initial[(twin.id, name)] = twin.state[name]
    checker = RuleChecker(end_t=duration_s, rules=rules, initial=initial, added={t.id: 0.0 for t in twins},
                          presence_values=presence)
    rt.subscribe(checker.on_event)
    for twin in twins:
        _script(rt, twin, random.Random(stable_seed(seed, twin.id, "rules")), duration_s, checker)
    rt.clock.advance_to(duration_s)
    result = checker.finish()
    checked = len(rules) + len(presence)
    return {"products": len(twins), "products_with_rules": len({k[0] for k in rules}),
            "features_checked": len(rules), "presence_features": len(presence), "rules_dropped": dropped,
            "reports": result["reports"], "r4_checked": result["r4_checked"],
            "violations": result["violations"][:200], "violation_count": len(result["violations"]),
            "stimulus_rejections": sum("rejected" in n for n in rt.notes), "faulted": dict(rt.faulted),
            "passed": checked > 0 and not result["violations"] and not rt.faulted,
            "seed": seed, "duration_s": duration_s}


BASE_TOPIC = "vesper-latency"  # its own topics: never another bridge's (a real Zigbee2MQTT's included)


def measure_latency(record: dict, bridge_client, observer, *, n: int, path: Path, overhead_s: float,
                    spacing_s: float, timeout_s: float, seed: int) -> list[dict]:
    """Open each of n contact-sensor twins of `record` once; per sample, the latency the model injected
    and the latency `observer` saw (stimulus to receipt, monotonic clock).

    Contact has no reporting rule, so a report leaves at once and is delayed only by the injected latency
    plus VESPER's own pipeline and scheduling; `observed_s - injected_s` is that residual. Each path gets
    its own twin ids and seed, so paths never share draws.
    """
    clock = WallClock()
    model = LatencyModel(default=path.name, paths={path.name: path}, overhead_s=overhead_s)
    rt = Runtime(clock=clock, seed=stable_seed(seed, path.name), latency=model)
    placements = [Placement(rt.add(record, twin_id=f"{record['id']}@lat-{path.name}-{i:04d}").id,
                            f"lat_{path.name}_{i:04d}") for i in range(n)]
    bridge = Bridge(rt, placements, bridge_client, base_topic=BASE_TOPIC, discovery=False)
    opened = rt.twins[placements[0].twin_id].features["contact"]["value_on"]
    topics = {f"{BASE_TOPIC}/{p.friendly_name}": p.twin_id for p in placements}
    sent: dict[str, float] = {}
    got: dict[str, float] = {}
    injected: dict[str, float] = {}
    done = threading.Event()

    def on_event(e: Event) -> None:
        if e.feature == "contact" and e.value == opened:
            injected[e.twin_id] = e.observed_t - e.physical_t

    def on_message(client, userdata, msg):
        received = time.monotonic()
        if getattr(msg, "retain", False):  # a retained copy is not a live report
            return
        twin_id = topics.get(msg.topic)
        if twin_id in sent and twin_id not in got and msg.payload:
            if json.loads(msg.payload).get("contact") == opened:
                got[twin_id] = received
                if len(got) == n:
                    done.set()

    rt.subscribe(on_event)
    observer.on_message = on_message
    observer.subscribe(f"{BASE_TOPIC}/+")
    try:
        bridge.start()
        for p in placements:
            sent[p.twin_id] = time.monotonic()
            rt.stimulate(p.twin_id, {"type": "contact", "state": "opened"})
            time.sleep(spacing_s)
        if not done.wait(timeout_s):
            raise TimeoutError(f"{len(got)} of {n} reports arrived within {timeout_s:.0f} s")
    finally:
        clock.close()  # first, so no late delivery republishes a state after the retraction
        bridge.retract()
        bridge.flush()
    return [{"twin": t, "injected_s": injected[t], "observed_s": got[t] - sent[t]} for t in sorted(got)]
