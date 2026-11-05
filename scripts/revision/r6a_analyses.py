#!/usr/bin/env python3
"""Revision R6-A analyses (protocol Addendum 8) of the environmental differential testing (EDT) sweeps of 8fcd672.

Post hoc in the paper's terms (Addendum 8 was written after the third internal review), with every rule fixed in
Addendum 8 before any number below was computed. Inputs, in --lab (default ~/vesper-lab/edt):
    sweep-rung4-8fcd672.json, sweep-rung2.5-8fcd672.json   the R4-A main runs (rich cache, both relations)
    work-8fcd672/                                           the EDT home and the rung-4 traces (A8.4, A8.5)
    sweep-rung4-120f4ca.json, audit-r4c/                    the R4-C dead-rule audit (A8.7; its labels stay local)
    sweep-rung4-r6a-{base,variant-*}-<C>.json              A8.8's check and home-variant sweeps (when present)
The output holds aggregates only: counts, rates, intervals and environment labels. It holds no automation, no
repository or entity name, no automation or repository hash and no corpus path.

    .venv/bin/python scripts/revision/r6a_analyses.py --sweeps-commit C [--lab DIR] [--workers 4]
        [--out results/edt/r6a-<HEAD7>.json]

Sections (docs/experiments/results-edt.md, "Revision R6-A (Addendum 8)"):
    confirmatory    A8.1  rung 0 trained on the registered training half, judged on the held-out half
    paired_auc      A8.2  paired repository bootstrap of AUC(8+4) - AUC(8)
    exemptions      A8.3  the sun exemption, the sensed frame among the flags, two sun events, now() with sun.sun
    frame_switch    A8.4  the frame of the first firing among the clock-only flags
    crossing        A8.5  the crossing-time sensed reference (an option, off by default)
    humidity_2_5    A8.6  humidity flags at rung 2.5
    dead_rules      A8.7  the dead-rule headline with three intervals
    variants        A8.8  the frame relation on the four home variants
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import multiprocessing
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "edt_analysis"))
from r4a_analyses import FRAME_KEY, CLOCK_KEY, envs_of, first_t_of, outs_of, porch_sensor_ids, ran  # noqa: E402
from revision_analyses import cluster_bootstrap, wilson  # noqa: E402

from vesper.edt import oracle  # noqa: E402
from vesper.edt.corpus import _entity_kinds, _ids  # noqa: E402
from vesper.edt.ladder import (  # noqa: E402
    oof_scores,
    paired_auc_diff_bootstrap,
    rung0_fit,
    rung0_split_fit,
    split_scores,
)

DAY = 86400.0
LO, HI = DAY, 3 * DAY  # the judged window: days 1 and 2
MAIN = "8fcd672"
RELATIONS = (("frame_relation", FRAME_KEY), ("clock_relation", CLOCK_KEY))
POPS = ("run", "bound", "coupled_run")
VARIANTS = ("glazing-x0.5", "glazing-x2", "plan-seed8", "mirror-ew")


def r4(x, k=4):
    return None if x is None else round(float(x), k)


def frame_label(frames) -> str:
    return "+".join(sorted(frames)) or "(none)"


# ---- A8.1 / A8.2 ----------------------------------------------------------------------------------------------------

def confirmatory(autos: dict) -> dict:
    out = {}
    for name, key in RELATIONS:
        for pop in POPS:
            row = {}
            for frames in (False, True):
                r = rung0_split_fit(autos, key, pop, frames)
                r["auc"], r["recall"], r["precision"], r["base_rate"] = (r4(r[k]) for k in
                                                                         ("auc", "recall", "precision", "base_rate"))
                r["auc_ci95"] = [r4(v) for v in r["auc_ci95"]] if r["auc_ci95"] else None
                row["8_plus_4" if frames else "8"] = r
            y8, s8, g8, _ = split_scores(autos, key, pop, False)
            y12, s12, g12, _ = split_scores(autos, key, pop, True)
            assert list(y8) == list(y12) and g8 == g12  # the same held-out rows
            row["paired_difference_held_out"] = _round_pair(paired_auc_diff_bootstrap(s8, s12, y8, g8))
            out[f"{name}/{pop}"] = row
            print(f"confirmatory {name}/{pop}: 8 {row['8']['auc']} / {row['8']['recall']}; "
                  f"8+4 {row['8_plus_4']['auc']} / {row['8_plus_4']['recall']}", flush=True)
    out["verdict"] = {k: v["8"]["matches"] for k, v in out.items() if isinstance(v, dict) and "8" in v}
    return out


def diagnostics(autos: dict) -> dict:
    """Not registered (added after A8.1's numbers were seen, 2026-11-05): why the split and A6.7's pooled AUCs differ.
    Per A6.7 fold, the AUC of its own model on its own test rows; and the split run the other way (trained on the
    held-out half, judged on the training half)."""
    from vesper.edt import ladder as lad
    from vesper.edt.cover import half
    out = {}
    for name, key in RELATIONS:
        for frames in (False, True):
            groups, y, x = lad._design(autos, key, "run", frames)
            _, folds, _ = lad._cross_validate(x, y, lad.grouped_folds(groups, y, lad.FOLDS))
            tr = np.array([half(g) == "train" for g in groups], dtype=bool)
            _, s_rev = lad._standardised_scores(x, y, ~tr, tr)
            out[f"{name}/run/{'8_plus_4' if frames else '8'}"] = {
                "a67_per_fold_auc": [r4(lad.auc(f["test_scores"], f["test_labels"])) for f in folds],
                "reverse_split_auc": r4(lad.auc(s_rev, y[tr]))}
    return out


def _round_pair(p: dict) -> dict:
    return {**p, **{k: r4(p[k]) for k in ("auc_a", "auc_b", "diff")},
            "ci95": [r4(v) for v in p["ci95"]] if p["ci95"] else None}


def paired_auc(autos: dict) -> dict:
    out = {}
    for name, key in RELATIONS:
        for pop in ("run", "coupled_run"):
            y8, s8, g8 = oof_scores(autos, key, pop, False)
            y12, s12, g12 = oof_scores(autos, key, pop, True)
            assert list(y8) == list(y12) and g8 == g12  # the same folds, rows and repositories
            p = _round_pair(paired_auc_diff_bootstrap(s8, s12, y8, g8))
            a67 = (rung0_fit(autos, key, pop, False)["auc"], rung0_fit(autos, key, pop, True)["auc"])
            p["reproduces_a67_aucs"] = abs(a67[0] - p["auc_a"]) < 1e-4 and abs(a67[1] - p["auc_b"]) < 1e-4
            p["n"], p["n_flagged"] = len(y8), int(np.sum(y8))
            out[f"{name}/{pop}"] = p
            print(f"paired {name}/{pop}: {p['diff']} {p['ci95']}", flush=True)
    return out


# ---- A8.3 -----------------------------------------------------------------------------------------------------------

def _steps(bound: dict):
    """Every trigger and every condition (nested and/or/not, the `- or:` shorthand), as (role, step)."""
    def conds(c):
        if isinstance(c, list):
            for x in c:
                yield from conds(x)
        elif isinstance(c, str):
            yield c
        elif isinstance(c, dict):
            yield c
            keys = set(c) - {"alias", "enabled"}
            if c.get("condition") in ("and", "or", "not"):
                yield from conds(c.get("conditions"))
            elif "condition" not in c and len(keys) == 1 and next(iter(keys)) in ("and", "or", "not"):
                yield from conds(c[next(iter(keys))])
    for t in oracle._as_list(bound.get("trigger", bound.get("triggers"))):
        yield "trigger", t
    for c in conds(oracle._as_list(bound.get("condition", bound.get("conditions")))):
        yield "condition", c


def _template_texts(step):
    return [t for t in oracle._texts(step) if "{{" in t or "{%" in t] if not isinstance(step, str) else [step]


def sun_exemption_reasons(bound: dict) -> set[str]:
    """Why a sun frame has no computable reference (A8.3): azimuth, template, or other (named)."""
    from vesper.edt.corpus import _TPL_ENTITY_RE
    out: set[str] = set()
    for _, s in _steps(bound):
        texts = _template_texts(s)
        for text in texts:
            if any("sun" in _entity_kinds(m.group(1) or m.group(2)) for m in _TPL_ENTITY_RE.finditer(text)):
                out.add("template")
                if "azimuth" in text:
                    out.add("azimuth")
        if not isinstance(s, dict):
            continue
        kind = str(s.get("platform") or s.get("trigger") or s.get("condition") or "")
        attr = s.get("attribute") if isinstance(s.get("attribute"), str) else None
        on_sun = any(e.split(".", 1)[0] == "sun" for e in _ids(s.get("entity_id")))
        if kind == "sun":
            events = [s.get(k) for k in ("event", "after", "before") if s.get(k) is not None]
            offsets = [s.get(k) for k in ("offset", "after_offset", "before_offset") if k in s]
            if not any(e in ("sunrise", "sunset") for e in events):
                out.add("other: a sun step naming no sunrise or sunset")
            elif any(oracle._offset_s(o) is None for o in offsets):
                out.add("other: an unreadable offset")
        elif kind in ("state", "numeric_state") and on_sun:
            if attr == "azimuth":
                out.add("azimuth")
            elif kind == "numeric_state" and attr == "elevation":
                if all(oracle._number(s.get(k)) is None for k in ("above", "below")):
                    out.add("other: an elevation with no numeric bound")
            elif kind == "numeric_state":
                out.add(f"other: numeric_state on sun.sun {'attribute ' + attr if attr else 'state'}")
            elif attr is not None:
                out.add(f"other: state on sun.sun attribute {attr}")
            else:
                wanted = oracle._as_list(s.get("state") if "condition" in s else s.get("to"))
                if wanted and not any(str(w) in oracle._SUN_STATES for w in wanted):
                    out.add("other: a sun.sun state that is neither above_horizon nor below_horizon")
    return out or {"other: not classified"}


def _trig_cond_refs(bound: dict):
    trig, cond = [], []
    for t in oracle._as_list(bound.get("trigger", bound.get("triggers"))):
        oracle._trigger_frames(t, set(), trig)
    oracle._condition_frames(oracle._as_list(bound.get("condition", bound.get("conditions"))), set(), cond)
    return trig, cond


def _ev(refs) -> set[str]:
    return {r.event for r in refs}


def exemptions(autos: dict) -> dict:
    from vesper.edt.corpus import _TPL_ENTITY_RE
    run = ran({"automations": autos})
    clock = {i for i, a in run.items() if a[CLOCK_KEY]}
    frame = {i for i, a in run.items() if a[FRAME_KEY]}
    fr = {i: oracle.frames_of(a["bound"]) for i, a in run.items()}
    groups = {"run": set(run), "clock_flagged": clock, "frame_flagged": frame}

    def tally(ids, fn):
        return {g: fn(ids & members) for g, members in groups.items()}

    # the sun exemption
    sun_nc = {i for i in run if oracle.SUN in fr[i].frames and not fr[i].sun_refs}
    assert sun_nc == {i for i, a in run.items()
                      if "sun" in (a.get("frames") or []) and not a.get("sun_frame_computable")}
    reasons = {i: sun_exemption_reasons(run[i]["bound"]) for i in sun_nc}

    def by_reason(ids):
        return {"n": len(ids), "by_reason": dict(Counter(r for i in ids for r in reasons[i]).most_common()),
                "by_frames": dict(Counter(frame_label(fr[i].frames) for i in ids).most_common())}
    sun = tally(sun_nc, by_reason)
    sun["frame_flagged_kinds"] = dict(Counter("+".join(run[i][FRAME_KEY]) for i in sun_nc & frame))
    sun["clock_flagged_kinds"] = dict(Counter("+".join(run[i][CLOCK_KEY]) for i in sun_nc & clock))

    # the sensed frame among the flags
    sensed = {g: {"n": sum(1 for i in m if oracle.SENSED in fr[i].frames),
                  "by_frames": dict(Counter(frame_label(fr[i].frames) for i in m
                                            if oracle.SENSED in fr[i].frames).most_common())}
              for g, m in groups.items()}

    # two sun events
    used_two, ignored = set(), set()
    for i in run:
        if oracle.SUN not in fr[i].frames:
            continue
        if len(_ev(fr[i].sun_refs)) > 1:
            used_two.add(i)
        trig, cond = _trig_cond_refs(run[i]["bound"])
        if trig and cond and _ev(cond) - _ev(trig):
            ignored.add(i)

    def ev_count(ids):
        return {"n": len(ids), "by_events": dict(Counter("+".join(sorted(_ev(fr[i].sun_refs))) for i in ids))}

    # one template reading both now() and sun.sun
    both = set()
    for i, a in run.items():
        for _, s in _steps(a["bound"]):
            for text in _template_texts(s):
                if oracle._NOW.search(text) and any("sun" in _entity_kinds(m.group(1) or m.group(2))
                                                    for m in _TPL_ENTITY_RE.finditer(text)):
                    both.add(i)
    return {
        "sun_frame_not_computable": sun,
        "sensed_frame": sensed,
        "two_sun_events_in_the_references_used": tally(used_two, ev_count),
        "trigger_and_condition_events_differ_conditions_unused": tally(ignored, lambda ids: {"n": len(ids)}),
        "template_reading_now_and_sun": tally(both, lambda ids: {
            "n": len(ids), "sun_frame_computable_elsewhere": sum(1 for i in ids if fr[i].sun_refs),
            "by_frames": dict(Counter(frame_label(fr[i].frames) for i in ids))}),
        "treatment": {
            "sun_frame_not_computable": "counted as invariant (A6.3), so the automation's timing is never flagged in "
                                        "the sun frame; with another frame it is judged by that frame alone",
            "two_sun_events": "the sun-relative minutes are measured from the nearest of every named reference "
                              "instant on the firing's day or a neighbouring day (sun_relative_min)",
            "trigger_and_condition_events_differ": "the triggers' references are used and the conditions' ignored "
                                                   "(frames_of: trigger references first)",
            "template_reading_now_and_sun": "the template adds both the clock and the sun frame; it supplies no sun "
                                            "reference, so its sun frame is computable only through another step",
        },
    }


# ---- the engine's view of the cached traces (A8.4, A8.5) -----------------------------------------------------------

_W: dict = {}


def _init(home_yaml: str, catalog_dir: str, watched: list, attrs: list) -> None:
    _W.update(home_yaml=home_yaml, catalog_dir=catalog_dir, watched=watched, attrs=attrs)


def _env_series(trace: str) -> tuple[str, dict]:
    """Per watched entity (and `entity|attribute`), its value over the run as the engine holds it: [(t, value)],
    from its start state; a new point whenever the value changes."""
    import dataclasses

    from vesper.edt.replay import REPORT_DELAY_S, SENSOR_PLATFORMS, TraceSession, _home, read_trace
    from vesper.env.outdoor import RUN_DAYS
    from vesper.env.site import parse_site
    from vesper.lab.automations import Engine
    from vesper.twins.clock import LogicalClock
    from vesper.twins.runtime import Event
    home_yaml = Path(_W["home_yaml"])
    base, twins = _home(str(home_yaml.resolve()), home_yaml.stat().st_mtime_ns, str(Path(_W["catalog_dir"]).resolve()))
    header, reports = read_trace(Path(trace))
    end = RUN_DAYS * 86400
    home = dataclasses.replace(base, site=parse_site(header.get("site")), start_time=0.0, automations=None)
    clock = LogicalClock()
    session = TraceSession(home, twins, header.get("initial_state") or {}, clock)
    engine = Engine(session, permissive=True, automations=[])
    ents = [e for e in _W["watched"] if e in engine.entities]
    attr_keys = [(e, a) for e, a in _W["attrs"] if e in engine.entities]
    fed = {(e.twin_id, e.feature) for e in engine.entities.values() if e.platform in SENSOR_PLATFORMS}
    for r in reports:
        if (r["twin_id"], r["feature"]) in fed and r["observed_t"] <= end:
            event = Event(r["twin_id"], r["feature"], r["value"], r["t"], r["observed_t"], "trace")
            clock.call_at(r["observed_t"] + REPORT_DELAY_S, lambda event=event: session.deliver(event))
    engine.start()
    state, attrs = engine._state, engine._attrs  # noqa: SLF001 - the engine keeps no public view
    out = {e: [(0.0, state.get(e))] for e in ents}
    out.update({f"{e}|{a}": [(0.0, (attrs.get(e) or {}).get(a))] for e, a in attr_keys})

    def note(_event):
        for e in ents:
            if state.get(e) != out[e][-1][1]:
                out[e].append((clock.now(), state.get(e)))
        for e, a in attr_keys:
            v = (attrs.get(e) or {}).get(a)
            if v != out[f"{e}|{a}"][-1][1]:
                out[f"{e}|{a}"].append((clock.now(), v))
    session.subscribe_reports(note)  # after the engine's own handler
    clock.advance_to(end)
    return trace, out


def env_series(envs, cache: Path, home_yaml: Path, watched: set, attrs: set, workers: int, catalog_dir: str) -> dict:
    from vesper.edt.trace import trace_file
    paths = {str(trace_file(cache, e, "device")): e for e in envs}
    missing = [p for p in paths if not Path(p).is_file()]
    assert not missing, f"{len(missing)} cached traces missing, e.g. {missing[0]}"
    ctx = multiprocessing.get_context("spawn")
    out = {}
    with ctx.Pool(workers, initializer=_init,
                  initargs=(str(home_yaml), catalog_dir, sorted(watched), sorted(attrs))) as pool:
        for k, (trace, series) in enumerate(pool.imap_unordered(_env_series, sorted(paths)), 1):
            out[paths[trace]] = series
            if k % 12 == 0:
                print(f"series {k}/{len(paths)}", flush=True)
    return out


def _trigger_entities(bound: dict) -> tuple[set, set]:
    from vesper.edt.corpus import _TPL_ENTITY_RE
    ents, attrs = set(), set()
    for t in oracle._as_list(bound.get("trigger", bound.get("triggers"))):
        if not isinstance(t, dict):
            continue
        attr = t.get("attribute") if isinstance(t.get("attribute"), str) else None
        for e in _ids(t.get("entity_id")):
            if e.split(".", 1)[0] != "sun":
                ents.add(e)
                if attr:
                    attrs.add((e, attr))
        for text in oracle._texts(t):
            for m in _TPL_ENTITY_RE.finditer(text):
                ents.add(m.group(1) or m.group(2))
    return ents, attrs


def _changes(series_env: dict) -> dict:
    return {k: [t for t, _ in v[1:]] for k, v in series_env.items()}


# ---- A8.4 -----------------------------------------------------------------------------------------------------------

def _trigger_frames_of(bound: dict) -> set[str]:
    out = set()
    for t in oracle._as_list(bound.get("trigger", bound.get("triggers"))):
        if isinstance(t, dict) and t.get("enabled") is not False:
            f: set = set()
            oracle._trigger_frames(t, f, [])
            out |= f or {"none"}
    return out


def frame_switch(autos: dict, envs, series: dict) -> dict:
    run = ran({"automations": autos})
    pop = {i: a for i, a in run.items() if a[CLOCK_KEY] and not a[FRAME_KEY]}
    changes = {e: _changes(s) for e, s in series.items()}
    per = {}
    status = Counter()
    for i, a in pop.items():
        ft = first_t_of(a, envs)
        outs = outs_of(a, envs)
        inferred, n_fired, n_amb, n_un = {}, 0, 0, 0
        for e, o in outs.items():
            if not o.fired or ft.get(e) is None:
                continue
            n_fired += 1
            cand = oracle.first_firing_frames(a["bound"], ft[e], oracle.site_of(e), changes[e])
            if len(cand) == 1:
                inferred[e] = next(iter(cand))
            elif cand:
                n_amb += 1
            else:
                n_un += 1
        tf = _trigger_frames_of(a["bound"])
        per[i] = {"trigger_frames": tf, "fired": n_fired, "inferred": inferred, "ambiguous": n_amb, "uninferred": n_un}
        status["inferred"] += len(inferred)
        status["ambiguous"] += n_amb
        status["uninferred"] += n_un
    multi = {i for i, p in per.items() if len(p["trigger_frames"]) > 1}
    complete = {i for i, p in per.items() if p["fired"] and len(p["inferred"]) == p["fired"]}
    switch = {i for i, p in per.items() if len(p["inferred"]) >= 2 and len(set(p["inferred"].values())) > 1}
    pairs = Counter(frame_label(set(per[i]["inferred"].values())) for i in switch)
    return {
        "population_clock_only": len(pop),
        "rich_cache_records_the_trigger": False,
        "first_firings": {"total": sum(status.values()), **dict(status)},
        "with_triggers_in_more_than_one_frame": len(multi),
        "trigger_frames_of_the_population": dict(Counter(frame_label(p["trigger_frames"]) for p in per.values())
                                                 .most_common()),
        "every_first_firing_inferred": len(complete),
        "every_first_firing_inferred_with_triggers_in_more_than_one_frame": len(complete & multi),
        "frame_switch": len(switch),
        "frame_switch_every_firing_inferred": len(switch & complete),
        "frame_switch_by_frames": dict(pairs.most_common()),
        "frame_switch_by_environment_kind": dict(Counter(k for i in switch for k in pop[i]["kinds"])),
        "frame_switch_clock_kinds": dict(Counter("+".join(pop[i][CLOCK_KEY]) for i in switch)),
        "registered_as_a_kind": len(switch) >= 10,
        "registration_rule": "registered as an outcome kind iff at least 10 automations show it (A8.4)",
    }


# ---- A8.5 -----------------------------------------------------------------------------------------------------------

def _quantiles(xs):
    if not xs:
        return None
    a = np.array(xs, dtype=float)
    return {"n": len(xs), "min": r4(a.min(), 1), "p25": r4(np.percentile(a, 25), 1), "median": r4(np.median(a), 1),
            "p75": r4(np.percentile(a, 75), 1), "p90": r4(np.percentile(a, 90), 1), "max": r4(a.max(), 1)}


def _bins(xs):
    edges = (("0", lambda v: v == 0), ("(0, 60]", lambda v: 0 < v <= 60), ("(60, 180]", lambda v: 60 < v <= 180),
             ("(180, 720]", lambda v: 180 < v <= 720), ("> 720", lambda v: v > 720))
    return {k: sum(1 for v in xs if f(v)) for k, f in edges}


def crossing(autos: dict, envs, series: dict) -> dict:
    run = ran({"automations": autos})
    frame = {i for i, a in run.items() if a[FRAME_KEY]}
    refs = {i: oracle.sensed_crossing_refs(a["bound"]) for i, a in run.items()}
    applies = {i for i, r in refs.items() if r}
    # check: the option off reproduces the published frame relation exactly
    mismatch = 0
    spreads, theta_all, new_flags, added = {}, {}, {}, set()
    for i, a in run.items():
        outs, ft, fr = outs_of(a, envs), first_t_of(a, envs), oracle.frames_of(a["bound"])
        static = set(a["static"])
        if i not in applies:
            continue
        if sorted(oracle.frame_divergences(outs, ft, fr) - static) != sorted(a[FRAME_KEY]):
            mismatch += 1
        cross = {e: oracle.crossing_instant(refs[i], series[e], LO, HI) for e in envs}
        theta = [(ft[e] - cross[e]) / 60.0 for e, o in outs.items()
                 if o.fired and ft.get(e) is not None and cross.get(e) is not None]
        theta_all[i] = theta
        if len(theta) >= 2:
            spreads[i] = max(theta) - min(theta)
        k = sorted(oracle.frame_divergences(outs, ft, fr, sensed_crossing=cross) - static)
        new_flags[i] = k
        if k and not a[FRAME_KEY]:
            added.add(i)
    for i in set(run) - applies:  # the option changes nothing for them; the check covers them too
        a = run[i]
        if sorted(oracle.frame_divergences(outs_of(a, envs), first_t_of(a, envs), oracle.frames_of(a["bound"]))
                  - set(a["static"])) != sorted(a[FRAME_KEY]):
            mismatch += 1
    changed_kinds = sum(1 for i in applies & frame if new_flags[i] != sorted(run[i][FRAME_KEY]))
    ts_gain = {i for i in applies if "time_spread" in new_flags[i] and "time_spread" not in run[i][FRAME_KEY]}
    sensed_flags = {i for i in frame if oracle.SENSED in oracle.frames_of(run[i]["bound"]).frames}

    def category(i):
        th = theta_all[i]
        if min(th) < 0:
            return "precedes (some first firing before the first crossing)"
        has_cond = bool(oracle._as_list(run[i]["bound"].get("condition", run[i]["bound"].get("conditions"))))
        return f"latency ({'with' if has_cond else 'no'} conditions)"
    flag_total = len(frame) + len(added)
    return {
        "option_off_reproduces_the_published_frame_relation": mismatch == 0, "mismatches": mismatch,
        "computable_sensed_frame": {"run": len(applies), "of_the_frame_flags": len(applies & frame),
                                    "of_the_sensed_frame_flags": len(applies & sensed_flags),
                                    "sensed_frame_flags": len(sensed_flags),
                                    "with_two_or_more_values": len(spreads),
                                    "with_no_value": sum(1 for i in applies if not theta_all[i]),
                                    "with_no_value_that_fire": sum(1 for i in applies if not theta_all[i] and any(
                                        o["fired"] for o in run[i]["outcomes"].values()))},
        "spread_min": {"all": _quantiles(list(spreads.values())), "bins_all": _bins(list(spreads.values())),
                       "frame_flags": _quantiles([spreads[i] for i in spreads if i in frame]),
                       "bins_frame_flags": _bins([spreads[i] for i in spreads if i in frame])},
        "frame_relation_with_the_option": {
            "n": flag_total, "published": len(frame), "added": len(added),
            "frame_flags_whose_kinds_change": changed_kinds,
            "added_by_frames": dict(Counter(frame_label(oracle.frames_of(run[i]["bound"]).frames) for i in added)
                                    .most_common()),
            "added_by_environment_kind": dict(Counter(k for i in added for k in run[i]["kinds"])),
            "added_by_kinds": dict(Counter("+".join(new_flags[i]) for i in added)),
            "added_by_category": dict(Counter(category(i) for i in added).most_common()),
            "added_clock_relation_flagged": sum(1 for i in added if run[i][CLOCK_KEY]),
            "added_spread_min": _quantiles([spreads[i] for i in added]),
            "time_spread_gained": len(ts_gain),
            "time_spread_gained_on_published_flags": len(ts_gain & frame),
            "time_spread_gained_by_frames": dict(Counter(frame_label(oracle.frames_of(run[i]["bound"]).frames)
                                                         for i in ts_gain).most_common()),
            "time_spread_gained_by_environment_kind": dict(Counter(k for i in ts_gain for k in run[i]["kinds"])),
            "time_spread_gained_published_kinds": dict(Counter("+".join(run[i][FRAME_KEY]) for i in ts_gain)),
            "time_spread_gained_by_category": dict(Counter(category(i) for i in ts_gain).most_common()),
            "time_spread_gained_spread_min": _quantiles([spreads[i] for i in ts_gain]),
            "time_spread_gained_theta_min": _quantiles([v for i in ts_gain for v in theta_all[i]]),
        },
    }


# ---- A8.6 -----------------------------------------------------------------------------------------------------------

def humidity(autos4: dict, autos25: dict, catalog_dir: str) -> dict:
    porch = porch_sensor_ids(catalog_dir)
    out = {}
    for name, key in RELATIONS:
        ids = [i for i, a in autos4.items() if a["outcomes"] and a[key] and "humidity" in a["kinds"]]
        kept = [i for i in ids if (autos25.get(i) or {}).get(key)]
        out[name] = {"humidity_flags_rung4": len(ids), "flagged_at_rung2_5": len(kept),
                     "same_kinds_at_rung2_5": sum(1 for i in kept if autos25[i][key] == autos4[i][key]),
                     "naming_a_porch_sensor": sum(1 for i in ids if any(p in json.dumps(autos4[i]["bound"])
                                                                         for p in porch)),
                     "kept_naming_a_porch_sensor": sum(1 for i in kept if any(p in json.dumps(autos4[i]["bound"])
                                                                               for p in porch))}
    return out


# ---- A8.7 -----------------------------------------------------------------------------------------------------------

def hypergeom_interval(k: int, n: int, big_n: int, alpha: float = 0.05) -> list[int]:
    """The exact interval for the population count K: every K in 0..N with P(X <= k) >= alpha/2 and
    P(X >= k) >= alpha/2, X ~ Hypergeometric(N, K, n)."""
    def pmf(x, kk):
        return math.comb(kk, x) * math.comb(big_n - kk, n - x) / math.comb(big_n, n)
    keep = []
    for kk in range(big_n + 1):
        lo = sum(pmf(x, kk) for x in range(0, k + 1))
        hi = sum(pmf(x, kk) for x in range(k, min(n, kk) + 1))
        if lo >= alpha / 2 and hi >= alpha / 2:
            keep.append(kk)
    return [min(keep), max(keep)]


def dead_rules(lab: Path, autos_main: dict) -> dict:
    import pickle
    audit = lab / "audit-r4c"
    samples = json.loads((audit / "samples.json").read_text())
    labels = {r.split("\t")[0]: r.split("\t")[1] for r in (audit / "labels-dead.tsv").read_text().splitlines()
              if r and not r.startswith("#")}
    old = pickle.load(open(audit / "sweep4.pkl", "rb"))["automations"]  # noqa: SIM115 - the 120f4ca sweep
    by12 = {k[:12]: k for k in old}
    sample = [by12[h[:12]] for h in samples["dead_sample"]]
    assert len(sample) == len(labels) == 100 and {h[:12] for h in sample} == set(labels)
    big_n = samples["n_dead"]
    genuine = {h for h in sample if labels[h[:12]].startswith("G")}
    k, n = len(genuine), len(sample)
    per_repo: dict = {}
    for h in sample:
        a, b = per_repo.get(old[h]["repo"], (0, 0))
        per_repo[old[h]["repo"]] = (a + (h in genuine), b + 1)
    boot = cluster_bootstrap(per_repo)
    wl = wilson(k, n)
    hg = hypergeom_interval(k, n, big_n)

    def dead_main(h):
        a = autos_main.get(h)
        return bool(a and a["outcomes"] and a["kinds"] and not any(o["fired"] for o in a["outcomes"].values()))
    main_dead = sum(1 for i, a in autos_main.items() if a["outcomes"] and a["kinds"]
                    and not any(o["fired"] for o in a["outcomes"].values()))
    del old
    gc.collect()
    return {
        "population_120f4ca": big_n, "sample": n, "seed": samples["seed_dead"], "genuinely_dead": k,
        "sample_repositories": len(per_repo),
        "point": round(big_n * k / n, 1),
        "wilson95_scaled": [round(big_n * wl[0], 1), round(big_n * wl[1], 1)],
        "hypergeometric_exact95": hg,
        "repository_bootstrap95_scaled": [round(big_n * boot["ci95"][0], 1), round(big_n * boot["ci95"][1], 1)],
        "headline": "the hypergeometric interval (sampling without replacement from a finite population, A8.7)",
        "population_8fcd672": main_dead,
        "sample_still_dead_at_8fcd672": sum(1 for h in sample if dead_main(h)),
        "genuine_still_dead_at_8fcd672": sum(1 for h in genuine if dead_main(h)),
    }


# ---- A8.8 -----------------------------------------------------------------------------------------------------------

def variants(lab: Path, c: str, autos_main: dict) -> dict:
    out: dict = {"sweeps_commit": c}
    base_path = lab / f"sweep-rung4-r6a-base-{c}.json"
    main_frame = {i for i, a in autos_main.items() if a["outcomes"] and a[FRAME_KEY]}
    if not base_path.is_file():
        return {**out, "null": "the check sweep is not there"}
    base = json.loads(base_path.read_text())["automations"]
    diff = {"outcomes": 0, "clock": 0, "frame": 0, "rich": 0, "ids": set(base) != set(autos_main)}
    cause = Counter()
    for i, a in autos_main.items():
        b = base.get(i) or {}
        diff["outcomes"] += a.get("outcomes") != b.get("outcomes")
        diff["clock"] += a.get(CLOCK_KEY) != b.get(CLOCK_KEY)
        diff["frame"] += a.get(FRAME_KEY) != b.get(FRAME_KEY)
        diff["rich"] += (a.get("rich") or {}) != (b.get("rich") or {})
        ra, rb = a.get("rich") or {}, b.get("rich") or {}
        if a.get("outcomes") != b.get("outcomes"):
            fa, fb = a.get("refused") or {}, b.get("refused") or {}
            budget = fa != fb and all("took longer than" in r for r in {**fa, **fb}.values())
            cause["outcome: a wall-clock template-compile refusal in one run only" if budget
                  else "outcome: other"] += 1
        elif ra != rb:
            same = all({k: v for k, v in (ra.get(e) or {}).items() if k not in ("count", "branches")} ==
                       {k: v for k, v in (rb.get(e) or {}).items() if k not in ("count", "branches")}
                       for e in set(ra) | set(rb))
            cause["rich only: firing count or branches" if same else "rich only: other"] += 1
    flags_same = not diff["clock"] and not diff["frame"] and not diff["ids"]
    # A8.8's check failed on exactness (2026-11-05, a dated deviation in Addendum 8): no flag of either relation
    # differs; the differing outcomes are refusals by the engine's wall-clock template-compile budget, the rich-only
    # ones the firing count or branches of very frequent automations. The variants are used, with this stated.
    used = flags_same and set(cause) <= {"outcome: a wall-clock template-compile refusal in one run only",
                                         "rich only: firing count or branches"}
    out["check"] = {**diff, "passed": not any(diff.values()), "differences_by_cause": dict(cause),
                    "flags_identical": flags_same, "used_with_the_stated_deviation": used}
    del base
    gc.collect()
    if not out["check"]["passed"] and not used:
        return {**out, "null": "the check failed: no variant number is used"}
    held = {}
    for v in VARIANTS:
        p = lab / f"sweep-rung4-r6a-variant-{v}-{c}.json"
        if not p.is_file():
            out[v] = {"null": "not run"}
            continue
        s = json.loads(p.read_text())
        av = s["automations"]
        runv = {i for i, a in av.items() if a["outcomes"]}
        fv = {i for i in runv if av[i].get(FRAME_KEY)}
        held[v] = main_frame & fv
        out[v] = {"run": len(runv), "clock_flagged": sum(1 for i in runv if av[i][CLOCK_KEY]),
                  "frame_flagged": len(fv), "of_the_199_frame_flagged": len(main_frame & fv),
                  "of_the_199_run": len(main_frame & runv),
                  "of_the_199_same_kinds": sum(1 for i in main_frame & fv if av[i][FRAME_KEY] ==
                                               autos_main[i][FRAME_KEY]),
                  "frame_flagged_not_in_the_199": len(fv - main_frame),
                  "negative_control": {k: s["negative_control"].get(k) for k in ("n", "flagged", "rate", "halt")}}
        del s, av
        gc.collect()
    if len(held) == len(VARIANTS):
        out["of_the_199_in_all_four"] = len(set.intersection(*held.values()))
        out["of_the_199_in_none"] = len(main_frame - set.union(*held.values()))
    out["main_frame_flags"] = len(main_frame)
    return out


# ---- main -----------------------------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lab", type=Path, default=Path.home() / "vesper-lab" / "edt")
    ap.add_argument("--sweeps-commit", required=True, help="the commit of A8.8's check and variant sweeps")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--only", nargs="*", help="sections to run (default: all)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    lab = args.lab
    head = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    out = args.out or ROOT / "results" / "edt" / f"r6a-{head}.json"
    report = json.loads(out.read_text()) if out.is_file() and args.only else {}
    report.update({"commit": head, "base": MAIN, "post_hoc": True,
                   "protocol": "docs/experiments/edt-protocol.md, Addendum 8"})
    want = set(args.only or ["confirmatory", "paired_auc", "exemptions", "frame_switch", "crossing", "humidity_2_5",
                             "dead_rules", "variants"])
    s4 = json.loads((lab / f"sweep-rung4-{MAIN}.json").read_text())
    autos, envs = s4["automations"], envs_of(s4)
    if "confirmatory" in want:
        report["confirmatory"] = confirmatory(autos)
        report["confirmatory_diagnostics_unregistered"] = diagnostics(autos)
    if "paired_auc" in want:
        report["paired_auc"] = paired_auc(autos)
    if "exemptions" in want:
        report["exemptions"] = exemptions(autos)
    if want & {"frame_switch", "crossing"}:
        run = ran(s4)
        watched, attrs = set(), set()
        for a in run.values():
            if (a[CLOCK_KEY] and not a[FRAME_KEY]) or oracle.sensed_crossing_refs(a["bound"]):
                e, at = _trigger_entities(a["bound"])
                watched |= e
                attrs |= at
        work = lab / f"work-{MAIN}"
        series = env_series(envs, work / "cache", work / "edt" / "home.yaml", watched, attrs, args.workers,
                            args.catalog)
        if "frame_switch" in want:
            report["frame_switch"] = frame_switch(autos, envs, series)
            print("frame switch:", report["frame_switch"]["frame_switch"], flush=True)
        if "crossing" in want:
            report["crossing"] = crossing(autos, envs, series)
            print("crossing:", report["crossing"]["frame_relation_with_the_option"]["n"], flush=True)
        del series
    if "humidity_2_5" in want:
        a25 = {i: {k: a.get(k) for k in (CLOCK_KEY, FRAME_KEY)}
               for i, a in json.loads((lab / f"sweep-rung2.5-{MAIN}.json").read_text())["automations"].items()}
        report["humidity_2_5"] = humidity(autos, a25, args.catalog)
    if "dead_rules" in want:
        report["dead_rules"] = dead_rules(lab, autos)
    if "variants" in want:
        report["variants"] = variants(lab, args.sweeps_commit, autos)
    out.write_text(json.dumps(report, indent=1, default=sorted) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    random.seed(0)
    main()
