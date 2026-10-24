"""Automatic visual checks of the play page (`vesper lab look`): drive it, photograph it, judge it.

A steps file is a YAML list, one action per step:

    - {until: "vesper.state.reach.length > 0", timeout: 5}   # wait until the page shows it
    - {press: e}                                              # a key; `times: 3` repeats it
    - {click_world: [5.0, 4.5]}                               # a click on the map at a world point
    - {wait: 0.5}                                             # seconds
    - {expect: "vesper.state.effects.bedroom_lamp.on", label: "the lamp is on"}
    - {shot: lamp_on}                                         # a screenshot, lamp_on.png

Every look also checks that the page got ready, that the map is drawn, that no shot is blank, that each shot
matches its reference image when references are given, and that the page raised no error. The result is
`report.json`, `report.md` and the screenshots, in the output directory.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from vesper.lab.browser import BrowserError
from vesper.lab.png import PngError, decode, diff, distinct_colors, encode

READY = "window.vesper !== undefined && window.vesper.ready === true"
ACTIONS = ("shot", "press", "click_world", "wait", "until", "expect")
OPTIONS = {"press": {"times"}, "until": {"timeout", "label"}, "expect": {"label"}}
MIN_COLOURS = 8  # fewer colours than this in a shot of the play page means nothing was drawn
DEFAULT_STEPS = [{"shot": "start"}]


class LookError(ValueError):
    """A steps file the checks cannot run; the message names the step."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    url: str
    out: Path
    checks: list[Check] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def load_steps(path: Path) -> list[dict]:
    try:
        steps = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise LookError(f"{path}: {e}") from e
    if not isinstance(steps, list):
        raise LookError(f"{path}: a steps file is a list of steps, one action each")
    for k, step in enumerate(steps, 1):
        _check_step(k, step)
    return steps


def _check_step(k: int, step) -> None:
    if not isinstance(step, dict):
        raise LookError(f"step {k}: a step is a mapping such as {{press: e}}")
    actions = [a for a in step if a in ACTIONS]
    unknown = [a for a in step if a not in ACTIONS and not any(a in o for o in OPTIONS.values())]
    if unknown:
        raise LookError(f"step {k}: unknown action {unknown[0]!r}; the actions are {', '.join(ACTIONS)}")
    if len(actions) != 1:
        raise LookError(f"step {k}: one action per step (found {', '.join(actions) or 'none'})")
    action, value = actions[0], step[actions[0]]
    extra = set(step) - {action} - OPTIONS.get(action, set())
    if extra:
        raise LookError(f"step {k}: {action} takes no {', '.join(sorted(extra))}")
    if action == "shot" and not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value)):
        raise LookError(f"step {k}: a shot's name is letters, digits, '-' and '_' (it names the file)")
    if action == "press":
        times = step.get("times", 1)
        if not isinstance(value, str) or not value:
            raise LookError(f"step {k}: press needs a key, such as e or ArrowUp")
        if isinstance(times, bool) or not isinstance(times, int) or times < 1:
            raise LookError(f"step {k}: times must be a whole number of at least 1")
    if action == "click_world" and not (isinstance(value, list) and len(value) == 2 and all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in value)):
        raise LookError(f"step {k}: click_world needs [x, z] in metres")
    if action == "wait" and (isinstance(value, bool) or not isinstance(value, int | float) or value < 0):
        raise LookError(f"step {k}: wait needs a number of seconds")
    if action in ("until", "expect") and not (isinstance(value, str) and value.strip()):
        raise LookError(f"step {k}: {action} needs a JavaScript expression")


def run_look(browser, url: str, steps: list[dict], out: Path, *, golden: Path | None = None,
             update_golden: bool = False, tolerance: int = 16, max_diff: float = 0.002,
             ready_timeout: float = 15.0) -> Report:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report = Report(url, out)
    checks = report.checks
    try:
        browser.goto(url, ready=READY, timeout=ready_timeout)
        browser.wait_for(READY, timeout=ready_timeout)
        checks.append(Check("the page is ready", True))
    except BrowserError as e:
        checks.append(Check("the page is ready", False, str(e)))
        _finish(browser, report, skipped=(1, len(steps)) if steps else None)
        return report
    colours = browser.evaluate("vesper.canvasColors()") or 0
    checks.append(Check("the map is drawn", colours >= MIN_COLOURS, f"{colours} colours on the map"))
    for k, step in enumerate(steps, 1):
        try:
            if not _step(browser, step, report, golden, update_golden, tolerance, max_diff):
                _finish(browser, report, skipped=(k + 1, len(steps)) if k < len(steps) else None)
                return report
        except (BrowserError, PngError) as e:
            action = next(a for a in step if a in ACTIONS)
            checks.append(Check(f"step {k} ({action})", False, str(e)))
            _finish(browser, report, skipped=(k + 1, len(steps)) if k < len(steps) else None)
            return report
    _finish(browser, report)
    return report


def _step(browser, step: dict, report: Report, golden, update_golden, tolerance, max_diff) -> bool:
    """Run one step; False when a failed step makes the rest meaningless."""
    checks = report.checks
    if "press" in step:
        browser.press(step["press"], times=step.get("times", 1))
    elif "click_world" in step:
        x, z = step["click_world"]
        px, py = browser.evaluate(f"vesper.toScreen({x}, {z})")
        browser.click(px, py)
    elif "wait" in step:
        deadline = time.monotonic() + step["wait"]
        while time.monotonic() < deadline:
            browser.pump(min(0.05, max(0.0, deadline - time.monotonic())))
    elif "until" in step:
        name = step.get("label", step["until"])
        try:
            browser.wait_for(step["until"], timeout=step.get("timeout", 10))
        except BrowserError as e:
            checks.append(Check(name, False, str(e)))
            return False
        checks.append(Check(name, True))
    elif "expect" in step:
        value = browser.evaluate(step["expect"])
        checks.append(Check(step.get("label", step["expect"]), bool(value), json.dumps(value)))
    elif "shot" in step:
        _shot(browser, step["shot"], report, golden, update_golden, tolerance, max_diff)
    return True


def _shot(browser, name: str, report: Report, golden, update_golden, tolerance, max_diff) -> None:
    data = browser.screenshot()
    (report.out / f"{name}.png").write_bytes(data)
    report.shots.append(f"{name}.png")
    img = decode(data)
    n = distinct_colors(img, limit=MIN_COLOURS + 1)
    report.checks.append(Check(f"{name} is not blank", n > MIN_COLOURS,
                               f"{n} colour{'s' if n != 1 else ''}{'+' if n > MIN_COLOURS else ''}"))
    if golden is None:
        return
    ref = Path(golden) / f"{name}.png"
    if update_golden:
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_bytes(data)
        report.checks.append(Check(f"{name} matches its reference", True, f"reference saved to {ref}"))
        return
    if not ref.is_file():
        report.checks.append(Check(f"{name} matches its reference", False,
                                   f"no reference {ref}; run with --update-golden to save one"))
        return
    d = diff(decode(ref.read_bytes()), img, tolerance=tolerance)
    if d.image is not None and d.fraction > max_diff:
        (report.out / f"{name}.diff.png").write_bytes(encode(d.image))
    detail = d.note or f"{d.fraction:.3%} of pixels differ (at most {max_diff:.3%} may)"
    report.checks.append(Check(f"{name} matches its reference", d.fraction <= max_diff, detail))


def _finish(browser, report: Report, skipped: tuple[int, int] | None = None) -> None:
    if skipped is not None:
        a, b = skipped
        report.checks.append(Check(f"step {a}" if a == b else f"steps {a}-{b}", False, "skipped after a failed step"))
    browser.pump(0.1)
    problems = list(browser.problems)
    report.checks.append(Check("no page errors", not problems, "; ".join(problems)))
    _write(report)


def _write(report: Report) -> None:
    summary = {"url": report.url, "ok": report.ok, "when": datetime.now(UTC).isoformat(timespec="seconds"),
               "checks": [asdict(c) for c in report.checks], "shots": report.shots}
    (report.out / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [f"# VESPER Lab look: {'PASS' if report.ok else 'FAIL'}", "", f"{report.url}, {summary['when']}", ""]
    lines += [f"- {'PASS' if c.ok else 'FAIL'} {c.name}" + (f": {c.detail}" if c.detail else "") for c in report.checks]
    lines += ["", "Shots:"] + [f"- {s}" for s in report.shots]
    (report.out / "report.md").write_text("\n".join(lines) + "\n")
