"""Setback setpoints: what an automation commands a thermostat to, read statically from its actions, and how long a
live run's commanded setpoint stays below a threshold (revision R2-c, post hoc).

`setpoints(automation)`: every target temperature a `climate.*` action of the automation names, as written in its
`data` (or legacy `data_template`): `temperature` and `target_temp_low`, whatever the climate service (a
`climate.set_hvac_mode` that also carries a `temperature` counts; one that only changes the mode sets no value). A
literal number (or a numeric string) is a value; anything else (a template, a reference) is `templated` and has no
value. `low_setpoints(automation, below)`: the literal values below `below` (default 10 C, the confirmation's cold
envelope). Only the automation's actions are read (`action` / `actions`, nested `choose` / `if` / `repeat` /
`parallel` / `sequence` steps included); a script it calls is not followed.

`setback_intervals(rows, start, end, below)`: from a closed-loop run's automation rows (`vesper.edt.confirm.
closed_loop`'s "rows": the commands the engine sent, at delivery), the time intervals within [start, end) during which
the last accepted setpoint command was below `below`. Before the first command the thermostat holds its own setpoint
(20 C for the EDT home's), so only commanded setpoints count.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

COLD_C = 10.0
SETPOINT_FIELDS = ("temperature", "target_temp_low")
MODE_FEATURES = ("system_mode",)  # a thermostat command that changes its mode, not its setpoint


@dataclass(frozen=True)
class Setpoint:
    service: str  # the climate service, as written (lower case)
    field: str  # temperature or target_temp_low
    value: float | None  # None when templated
    templated: bool


def _walk(node) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for x in node:
            yield from _walk(x)


def _actions(automation):
    if not isinstance(automation, dict):
        return None
    return next((automation[k] for k in ("action", "actions") if automation.get(k) is not None), None)


def climate_steps(automation) -> Iterator[tuple[str, dict]]:
    """(service, step) for every step of the automation's actions that calls a `climate.*` service (the service
    under `service:` or, in newer syntax, `action:`)."""
    for step in _walk(_actions(automation)):
        svc = step.get("service") if isinstance(step.get("service"), str) else step.get("action")
        if isinstance(svc, str) and svc.strip().lower().startswith("climate."):
            yield svc.strip().lower(), step


def _number(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def setpoints(automation) -> list[Setpoint]:
    """Every target temperature the automation's climate actions name (see the module doc)."""
    out = []
    for svc, step in climate_steps(automation):
        data: dict = {}
        for k in ("data_template", "data"):  # `data` wins where both name a field, as Home Assistant merges them
            if isinstance(step.get(k), dict):
                data.update(step[k])
        for f in SETPOINT_FIELDS:
            if f in data:
                v = _number(data[f])
                out.append(Setpoint(svc, f, v, v is None))
    return out


def low_setpoints(automation, below: float = COLD_C) -> list[Setpoint]:
    """The literal setpoints the automation commands below `below`."""
    return [s for s in setpoints(automation) if s.value is not None and s.value < below]


def setback_intervals(rows: list[dict], start: float, end: float, below: float = COLD_C) -> list[tuple[float, float]]:
    """The intervals within [start, end) during which the last accepted setpoint command in `rows` was below
    `below` (see the module doc)."""
    cmds = sorted((r["t"], float(r["value"])) for r in rows
                  if r.get("kind", "automation") == "automation" and r.get("accepted", True)
                  and str(r.get("service", "")).startswith("climate.") and r.get("feature") not in MODE_FEATURES
                  and _number(r.get("value")) is not None)
    out: list[tuple[float, float]] = []
    for k, (t, v) in enumerate(cmds):
        if v >= below:
            continue
        a, b = max(t, start), min(cmds[k + 1][0] if k + 1 < len(cmds) else end, end)
        if b > a:
            if out and out[-1][1] == a:  # two low commands in a row are one setback
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return out
