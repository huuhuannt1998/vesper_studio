#!/usr/bin/env python3
"""Revision R4-A analyses (protocol Addendum 6) of the environmental differential testing (EDT) sweeps of commit C.

Post hoc in the paper's terms (Addendum 6 follows a review of results already seen); every rule is Addendum 6's,
fixed before the runs. Inputs, in --lab (default ~/vesper-lab/edt), for commit C:
    sweep-rung{4,2.5,2,3,1}-C.json      `vesper edt run` at each rung (with the frame relation and richer outcome)
    work-C/                             the work directory (the EDT home and the rung-4 traces), for the dead rules
    sweep-rung4-120f4ca.json            the reproduction check (the clock relation must give its 797 flags)
The output holds aggregates only: counts, rates, intervals and environment labels (city, date, plan north). It holds
no automation, no repository or entity name, no automation or repository hash and no corpus path.

    .venv/bin/python scripts/edt_analysis/r4a_analyses.py --commit C [--lab DIR] [--workers 5]
        [--out results/edt/analysis-C.json]

Sections (docs/experiments/results-edt.md, "Revision R4-A (Addendum 6)"):
    reproduction        the clock relation on the new rung-4 sweep against 120f4ca's flags
    flags               flag counts by kind, both relations; the sensed sensitivity; transitions; frames
    witnesses           minimal witnesses and the axes admitting one, under the frame relation (and the clock one)
    rates               per-quantity rates with a repository bootstrap, frame relation
    sensor_split        the sensor-driven flags split by rung 2.5: ERA5-only versus room-model-dependent (A6.5)
    dead_rules          automations that fire in no environment, with mechanically derived reasons (A6.6)
    ladder              rungs 2.5, 2, 3, 1 against rung 4 under both relations; per-rung run counts (A6.7)
    rung0               rung 0 refitted on the run and the environment-coupled run automations (A6.7)
    pair_cover          greedy over the 1,128 environment pairs, k = 1, 2, 3, both relations (A6.7)
    richer_outcome      unflagged automations that differ in count, payload or branch (A6.4)
"""
from __future__ import annotations

import argparse
import gc
import itertools
import json
import multiprocessing
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from revision_analyses import cluster_bootstrap, wilson  # noqa: E402

from vesper.edt.cover import AXES, half, restricted_divergences  # noqa: E402
from vesper.edt.oracle import (  # noqa: E402
    SENSED_KINDS,
    frame_divergences,
    frame_restricted_divergences,
    frames_of,
)
from vesper.edt.types import Env, Outcome  # noqa: E402

ENV_KINDS = ("sun", "temperature", "humidity", "illuminance", "weather")
SENSED = set(SENSED_KINDS)
RUNGS = ("4", "2.5", "2", "3", "1")
CLOCK_KEY, FRAME_KEY = "divergences", "frame_divergences"
PAIR_KS = (1, 2, 3)


# ---- shared --------------------------------------------------------------------------------------------------------

def load(lab: Path, c: str, rung: str) -> dict:
    return json.loads((lab / f"sweep-rung{rung}-{c}.json").read_text())


def envs_of(result: dict) -> list[Env]:
    return [Env(e["city"], e["date"], e["north_deg"], e["rung"]) for e in result["envs"]]


def label(env: Env) -> str:
    return f"{env.city.split(',')[0]} {env.date} n{int(env.north_deg)}"


def outs_of(a: dict, envs: list[Env]) -> dict[Env, Outcome]:
    return {e: Outcome(**a["outcomes"][e.key]) for e in envs if e.key in a.get("outcomes", {})}


def first_t_of(a: dict, envs: list[Env]) -> dict[Env, float | None]:
    rich = a.get("rich") or {}
    return {e: (rich.get(e.key) or {}).get("first_t") for e in envs}


def ran(result: dict) -> dict[str, dict]:
    return {i: a for i, a in result["automations"].items() if a["outcomes"]}


def frame_label(a: dict) -> str:
    return "+".join(a.get("frames") or []) or "(none)"


def kinds_label(a: dict) -> str:
    return "+".join(a["kinds"]) or "(none)"


# ---- reproduction --------------------------------------------------------------------------------------------------

def reproduction(s4: dict, old: dict) -> dict:
    new_f = {i for i, a in ran(s4).items() if a[CLOCK_KEY]}
    old_f = {i for i, a in old["automations"].items() if a["outcomes"] and a["divergences"]}
    same_kinds = sum(1 for i in new_f & old_f
                     if s4["automations"][i][CLOCK_KEY] == old["automations"][i]["divergences"])
    return {"clock_relation_flagged_new": len(new_f), "flagged_120f4ca": len(old_f),
            "identical_set": new_f == old_f, "only_new": len(new_f - old_f), "only_120f4ca": len(old_f - new_f),
            "same_kinds": same_kinds, "by_kind_new": s4["flagged"]["by_kind"],
            "by_kind_120f4ca": old["flagged"]["by_kind"],
            "yield_new": s4["yield"], "yield_120f4ca": old["yield"],
            "negative_control_new": {k: s4["negative_control"][k] for k in ("n", "fired", "flagged", "rate", "halt")}}


# ---- the binder fixes and the reporting rule (A6.9) -----------------------------------------------------------------

def effects(old: dict, check: dict, cat: dict, s4: dict) -> dict:
    """The binder fixes alone (check -> catalog-rule run, both under the catalog's reporting rule) and the reporting
    rule alone (catalog-rule run -> main rung 4, both under the current binder)."""
    def counts(r):
        run = ran(r)
        return {"bound": r["yield"]["bound"], "run": len(run), "clock_flagged": r["flagged"]["n"],
                "frame_flagged": sum(1 for a in run.values() if a.get(FRAME_KEY))}
    a_old, a_cat, a_new = old["automations"], cat["automations"], s4["automations"]
    sensor = [i for i, a in a_old.items() if a["outcomes"] and a["divergences"] and SENSED & set(a["kinds"])]
    unbound = [i for i in sensor if a_cat[i]["bound"] is None]
    unflagged = [i for i in sensor if a_cat[i]["bound"] is not None and not a_cat[i]["divergences"]]
    rebound = [i for i in sensor if a_cat[i]["bound"] is not None and a_cat[i]["bound"] != a_old[i]["bound"]]
    reasons = Counter(a_cat[i]["unbindable"].split(" (for ")[0] for i in unbound)
    illum = [i for i, a in a_cat.items() if a["outcomes"] and "illuminance" in a["kinds"]]
    illum_new = [i for i, a in a_new.items() if a["outcomes"] and "illuminance" in a["kinds"]]

    def flagged_in(r, ids, key):
        return sum(1 for i in ids if r[i].get(key))
    by_rule = {
        "illuminance_reading_run": {"catalog_rule": len(illum), "device_rule": len(illum_new)},
        "clock_relation": {"catalog_rule": flagged_in(a_cat, illum, CLOCK_KEY),
                           "device_rule": flagged_in(a_new, illum_new, CLOCK_KEY)},
        "frame_relation": {"catalog_rule": flagged_in(a_cat, illum, FRAME_KEY),
                           "device_rule": flagged_in(a_new, illum_new, FRAME_KEY)},
        "changed_flag_clock": sum(1 for i in set(illum) & set(illum_new)
                                  if bool(a_cat[i][CLOCK_KEY]) != bool(a_new[i][CLOCK_KEY])),
        "changed_flag_frame": sum(1 for i in set(illum) & set(illum_new)
                                  if bool(a_cat[i].get(FRAME_KEY)) != bool(a_new[i].get(FRAME_KEY))),
        "changed_kinds_clock": sum(1 for i in set(illum) & set(illum_new)
                                   if a_cat[i][CLOCK_KEY] != a_new[i][CLOCK_KEY]),
        "trigger_readers": sum(1 for i in illum_new if "illuminance" in json.dumps(
            a_new[i]["bound"].get("triggers", a_new[i]["bound"].get("trigger")))),
    }
    return {"120f4ca_binder_catalog_rule": counts(check), "current_binder_catalog_rule": counts(cat),
            "current_binder_device_rule": counts(s4),
            "binder_fixes_on_the_179_sensor_driven": {"n": len(sensor), "unbound": len(unbound),
                                                      "unbound_by_reason": dict(reasons.most_common()),
                                                      "bound_differently": len(rebound),
                                                      "bound_but_no_longer_flagged": len(unflagged)},
            "reporting_rule_on_illuminance_readers": by_rule,
            "all_flags_changed_by_the_rule_clock": sum(1 for i, a in a_new.items() if a["outcomes"] and i in a_cat
                                                       and bool(a[CLOCK_KEY]) != bool(a_cat[i][CLOCK_KEY])),
            "all_flags_changed_by_the_rule_frame": sum(1 for i, a in a_new.items() if a["outcomes"] and i in a_cat
                                                       and bool(a.get(FRAME_KEY)) != bool(a_cat[i].get(FRAME_KEY)))}


# ---- flags ---------------------------------------------------------------------------------------------------------

def flags(s4: dict) -> dict:
    envs = envs_of(s4)
    autos = ran(s4)
    clock = {i for i, a in autos.items() if a[CLOCK_KEY]}
    frame = {i for i, a in autos.items() if a[FRAME_KEY]}
    sens = {}
    for i, a in autos.items():
        static = set(a["static"])
        k = frame_divergences(outs_of(a, envs), first_t_of(a, envs), frames_of(a["bound"]), sensed_invariant=False)
        sens[i] = sorted(k - static)
    sens_f = {i for i, k in sens.items() if k}
    sun_only_ts = {i for i in clock if autos[i]["kinds"] == ["sun"] and autos[i][CLOCK_KEY] == ["time_spread"]}
    trans = Counter((bool(autos[i][CLOCK_KEY]), bool(autos[i][FRAME_KEY])) for i in autos)
    kept_kinds = Counter(f"{'+'.join(autos[i][CLOCK_KEY])} -> {'+'.join(autos[i][FRAME_KEY]) or '(not flagged)'}"
                         for i in clock)
    return {
        "run": len(autos),
        "clock_relation": {"n": len(clock), "by_kind": s4["flagged"]["by_kind"]},
        "frame_relation": {"n": len(frame), "by_kind": s4["flagged_frame"]["by_kind"],
                           "time_spread_only": sum(1 for i in frame if autos[i][FRAME_KEY] == ["time_spread"]),
                           "share_of_run": round(len(frame) / len(autos), 4)},
        "frame_relation_sensed_not_invariant_sensitivity": {
            "n": len(sens_f), "by_kind": dict(sorted(Counter(k for i in sens_f for k in sens[i]).items())),
            "added_over_frame_relation": len(sens_f - frame)},
        "frame_flagged_subset_of_clock_flagged": frame <= clock,
        "frame_only": len(frame - clock),
        "transitions": {"clock_and_frame": trans[(True, True)], "clock_only": trans[(True, False)],
                        "frame_only": trans[(False, True)], "neither": trans[(False, False)]},
        "clock_kinds_to_frame_kinds": dict(kept_kinds.most_common()),
        "sun_only_time_spread_only": {"n": len(sun_only_ts), "frame_flagged": len(sun_only_ts & frame),
                                      "frame_kinds": dict(Counter('+'.join(autos[i][FRAME_KEY]) or "(not flagged)"
                                                                  for i in sun_only_ts))},
        "frames": {
            "run": dict(Counter(frame_label(a) for a in autos.values()).most_common()),
            "clock_flagged": dict(Counter(frame_label(autos[i]) for i in clock).most_common()),
            "frame_flagged": dict(Counter(frame_label(autos[i]) for i in frame).most_common()),
            "sun_frame_not_computable_run": sum(1 for a in autos.values() if "sun" in (a.get("frames") or [])
                                                and not a.get("sun_frame_computable")),
        },
        "frame_flagged_by_environment_kind": dict(Counter(k for i in frame for k in autos[i]["kinds"])),
        "frame_flagged_by_environment_and_divergence_kinds": dict(Counter(
            f"{kinds_label(autos[i])} / {'+'.join(autos[i][FRAME_KEY])}" for i in frame).most_common(15)),
        "frame_flagged_with_no_environment_kind": sum(1 for i in frame if not autos[i]["kinds"]),
        "repositories": {"run": len({a["repo"] for a in autos.values()}),
                         "clock_flagged": len({autos[i]["repo"] for i in clock}),
                         "frame_flagged": len({autos[i]["repo"] for i in frame})},
        "frame_flagged_assumption_free": sum(1 for i in frame if not autos[i]["assumptions"]),
    }


# ---- witnesses -----------------------------------------------------------------------------------------------------

def _axis(a: Env, b: Env) -> str | None:
    diff = [ax for ax in ("city", "date", "north_deg") if getattr(a, ax) != getattr(b, ax)]
    return diff[0] if len(diff) == 1 else None


def witnesses(s4: dict) -> dict:
    envs = envs_of(s4)
    one_axis = [(a, b, _axis(a, b)) for a, b in itertools.combinations(envs, 2) if _axis(a, b)]
    out = {}
    for name, key in (("frame_relation", FRAME_KEY), ("clock_relation", CLOCK_KEY)):
        minimal, axes_sets, none = Counter(), Counter(), 0
        for a in ran(s4).values():
            if not a[key]:
                continue
            o, ft, fr = outs_of(a, envs), first_t_of(a, envs), frames_of(a["bound"])
            found = []
            for x, y, ax in one_axis:
                if x not in o or y not in o:
                    continue
                d = frame_restricted_divergences(o, ft, fr, [x, y]) if key == FRAME_KEY else \
                    restricted_divergences(o, [x, y])
                if d:
                    found.append((x, y, ax))
            if not found:
                none += 1
                continue
            minimal[min(found, key=lambda w: (AXES.index(w[2]), w[0].key, w[1].key))[2]] += 1
            axes_sets["+".join(ax for ax in AXES if any(w[2] == ax for w in found))] += 1
        out[name] = {"minimal_witness_axis": {ax: minimal[ax] for ax in AXES}, "no_witness": none,
                     "axes_admitting_a_witness": dict(axes_sets.most_common())}
    out["note"] = ("axes in the minimal-witness preference order (protocol section 6): north_deg (orientation), date, "
                   "city; under the frame relation a flagged mixed-frame automation may have no pair witness at all "
                   "(Proposition 1 holds per frame, not across frames)")
    return out


# ---- rates ---------------------------------------------------------------------------------------------------------

def rates(s4: dict) -> dict:
    autos = ran(s4)

    def boot(pred_den, key=FRAME_KEY):
        by = defaultdict(lambda: [0, 0])
        for a in autos.values():
            if pred_den(a):
                by[a["repo"]][0] += 1 if a[key] else 0
                by[a["repo"]][1] += 1
        r = cluster_bootstrap({k: tuple(v) for k, v in by.items()}) if by else None
        if r:
            r["wilson95_iid"] = wilson(r["num"], r["den"])
        return r
    out = {"frame_relation": {"flagged_of_run": boot(lambda a: True),
                              "env_coupled_flagged_of_env_coupled_run": boot(lambda a: bool(a["kinds"])),
                              "by_quantity": {k: boot(lambda a, k=k: k in a["kinds"]) for k in ENV_KINDS},
                              "by_frame": {f: boot(lambda a, f=f: f in (a.get("frames") or []))
                                           for f in ("clock", "sun", "sensed")}},
           "clock_relation": {"flagged_of_run": boot(lambda a: True, CLOCK_KEY),
                              "env_coupled_flagged_of_env_coupled_run": boot(lambda a: bool(a["kinds"]), CLOCK_KEY),
                              "by_quantity": {k: boot(lambda a, k=k: k in a["kinds"], CLOCK_KEY) for k in ENV_KINDS}},
           "uninformative_below_n": 30,
           "note": "a rate whose denominator is below 30 automations is marked uninformative in the text"}
    return out


# ---- the sensor-driven split (A6.5) --------------------------------------------------------------------------------

def porch_sensor_ids(catalog_dir: str = "catalog/vds") -> set[str]:
    """The standard home's porch (outdoor) temperature, humidity and illuminance entity ids."""
    import tempfile

    from vesper.edt.home import build_edt_home
    with tempfile.TemporaryDirectory() as tmp:
        _, roles = build_edt_home(Path(tmp), catalog_dir)
    return {eid for (role, room, _), eid in roles.items() if room == "porch" and role in SENSED}


def sensor_split(s4: dict, s25: dict, s2: dict, porch_ids: set[str] | None = None) -> dict:
    a4, a25, a2 = s4["automations"], s25["automations"], s2["automations"]
    porch_ids = porch_sensor_ids() if porch_ids is None else porch_ids
    out = {}
    for name, key in (("clock_relation", CLOCK_KEY), ("frame_relation", FRAME_KEY)):
        sensor = [i for i, a in ran(s4).items() if a[key] and SENSED & set(a["kinds"])]
        era5 = [i for i in sensor if a25.get(i, {}).get(key)]
        same = [i for i in era5 if a25[i][key] == a4[i][key]]
        at2 = [i for i in sensor if a2.get(i, {}).get(key)]

        def by_q(ids):
            return {k: sum(1 for i in ids if k in a4[i]["kinds"]) for k in sorted(SENSED)}
        porch = [i for i in sensor if any(p in json.dumps(a4[i]["bound"]) for p in porch_ids)]
        indoor_only = [i for i in sensor if i not in set(porch)]
        out[name] = {"sensor_driven": len(sensor), "era5_only": len(era5),
                     "room_model_dependent": len(sensor) - len(era5),
                     "era5_only_same_kinds": len(same), "also_flagged_at_rung2": len(at2),
                     "sensor_driven_by_quantity": by_q(sensor), "era5_only_by_quantity": by_q(era5),
                     "sensor_driven_with_sun": sum(1 for i in sensor if "sun" in a4[i]["kinds"]),
                     "era5_only_with_sun": sum(1 for i in era5 if "sun" in a4[i]["kinds"]),
                     "sensor_driven_naming_a_porch_entity": len(porch),
                     "era5_only_naming_a_porch_entity": sum(1 for i in era5 if i in set(porch)),
                     "era5_only_naming_no_porch_entity": sum(1 for i in era5 if i in set(indoor_only))}
    return out


# ---- the ladder (A6.7) ---------------------------------------------------------------------------------------------

def ladder_rows(s4: dict, sr: dict) -> dict:
    from vesper.edt.ladder import _compare

    def relabel(result, key):
        return {**result, "automations": {i: {**a, "divergences": a.get(key) or []}
                                          for i, a in result["automations"].items()}}
    out = {}
    for name, key in (("clock_relation", CLOCK_KEY), ("frame_relation", FRAME_KEY)):
        row = _compare(relabel(s4, key), relabel(sr, key))
        row["reproduces"] = bool(row["reproduced_share"] is not None and row["reproduced_share"] >= 0.90
                                 and row["mae_hours"] is not None and row["mae_hours"] <= 0.5
                                 and row["spearman_time_spread"] is not None and row["spearman_time_spread"] >= 0.90)
        row["flagged_at_rung"] = sum(1 for a in ran(sr).values() if a.get(key))
        out[name] = row
    out["run"] = sr["yield"]["run"]
    out["negative_control"] = {k: sr["negative_control"][k] for k in ("n", "fired", "flagged", "rate", "halt")}
    return out


# ---- rung 0 (A6.7) -------------------------------------------------------------------------------------------------

def rung0(s4: dict) -> dict:
    from vesper.edt.ladder import rung0_fit
    autos = s4["automations"]
    keep = ("population", "label", "features", "n", "n_flagged", "n_repos", "base_rate", "folds", "degenerate_folds",
            "auc", "auc_ci95", "auc_random_folds", "recall_at_precision_0.55", "null")
    out = {}
    for label_name, key in (("frame_relation", FRAME_KEY), ("clock_relation", CLOCK_KEY)):
        for pop in ("run", "coupled_run"):
            for frames in (False, True):
                r = rung0_fit(autos, key, pop, frames)
                r = {k: r[k] for k in keep}
                rec = r["recall_at_precision_0.55"]
                if rec:
                    r["recall_at_precision_0.55"] = {k: rec[k] for k in ("recall", "precision", "base_rate",
                                                                         "n_predicted_positive", "degenerate_folds")}
                out[f"{label_name}/{pop}/{'with' if frames else 'without'}_frame_features"] = r
                print(f"rung0 {label_name} {pop} frames={frames}: auc {r['auc']}", flush=True)
    out["bound_A1.4_clock_relation"] = {k: rung0_fit(autos)[k] for k in ("n", "n_flagged", "auc", "auc_ci95")}
    return out


# ---- greedy over pairs (A6.7) --------------------------------------------------------------------------------------

def pair_cover(s4: dict) -> dict:
    envs = envs_of(s4)
    pairs = list(itertools.combinations(range(len(envs)), 2))
    out = {"pairs": len(pairs)}
    for name, key in (("clock_relation", CLOCK_KEY), ("frame_relation", FRAME_KEY)):
        fl = {i: a for i, a in ran(s4).items() if a[key]}
        part = {i: half(a["repo"]) for i, a in fl.items()}
        cov: dict[tuple, set] = {p: set() for p in pairs}
        for i, a in fl.items():
            o, ft, fr = outs_of(a, envs), first_t_of(a, envs), frames_of(a["bound"])
            for p in pairs:
                x, y = envs[p[0]], envs[p[1]]
                if x not in o or y not in o:
                    continue
                d = frame_restricted_divergences(o, ft, fr, [x, y]) if key == FRAME_KEY else \
                    restricted_divergences(o, [x, y])
                if d:
                    cov[p].add(i)
        groups = {g: {i for i in fl if part[i] == g} for g in ("train", "held_out")}
        groups["all"] = set(fl)
        coverable = set().union(*cov.values())
        rank = {p: (envs[p[0]].key, envs[p[1]].key) for p in pairs}
        chosen, covered, steps = [], set(), {}
        for k in range(1, max(PAIR_KS) + 1):
            best = min(pairs, key=lambda p: (-len((cov[p] - covered) & groups["train"]), rank[p]))
            chosen.append(best)
            covered |= cov[best]
            got = set().union(*(cov[p] for p in chosen))
            steps[str(k)] = {"pairs": [[label(envs[p[0]]), label(envs[p[1]])] for p in chosen],
                             "distinct_envs": len({e for p in chosen for e in p}),
                             **{f"coverage_{g}": round(len(got & ids) / len(ids), 4) if ids else None
                                for g, ids in groups.items()}}
        out[name] = {"flagged": {g: len(v) for g, v in groups.items()},
                     "covered_by_some_pair_all": len(coverable),
                     "no_pair_covers": len(set(fl) - coverable), "greedy": steps}
    out["note"] = ("greedy maximum coverage over environment pairs on the training half, ties to the smaller "
                   "environment keys; for k pairs it is within 1 - 1/e of the best k pairs (submodular coverage); "
                   "k = 1 is the exhaustive best pair")
    return out


# ---- the richer outcome (A6.4) -------------------------------------------------------------------------------------

def richer(s4: dict) -> dict:
    autos = ran(s4)
    out = {}
    for name, key in (("clock_relation", CLOCK_KEY), ("frame_relation", FRAME_KEY)):
        un = [a for a in autos.values() if not a[key]]
        firing = [a for a in un if any(o["fired"] for o in a["outcomes"].values())]
        out[name] = {"unflagged": len(un), "unflagged_firing_somewhere": len(firing),
                     "differ_any": sum(1 for a in un if a.get("rich_divergences")),
                     "by_kind": dict(Counter(k for a in un for k in a.get("rich_divergences", []))),
                     "combinations": dict(Counter("+".join(a.get("rich_divergences", [])) for a in un
                                                  if a.get("rich_divergences")).most_common()),
                     "env_coupled_differ_any": sum(1 for a in un if a.get("rich_divergences") and a["kinds"])}
    out["all_run_by_kind"] = s4.get("rich_divergent")
    return out


# ---- the dead-rule class (A6.6) ------------------------------------------------------------------------------------

_DW: dict = {}


def _dead_init(home_yaml: str, catalog_dir: str, probes: dict, watched: list) -> None:
    _DW.update(home_yaml=home_yaml, catalog_dir=catalog_dir, probes=probes, watched=watched)


def _series(home_yaml: Path, trace: Path, entities: list[str], catalog_dir: str) -> dict[str, list]:
    """Each watched entity's state over the run as the engine holds it: [(t, state)], from its start state."""
    import dataclasses

    from vesper.edt.replay import REPORT_DELAY_S, SENSOR_PLATFORMS, TraceSession, _home, read_trace
    from vesper.env.outdoor import RUN_DAYS
    from vesper.env.site import parse_site
    from vesper.lab.automations import Engine
    from vesper.twins.clock import LogicalClock
    from vesper.twins.runtime import Event
    base, twins = _home(str(home_yaml.resolve()), home_yaml.stat().st_mtime_ns, str(Path(catalog_dir).resolve()))
    header, reports = read_trace(trace)
    end = RUN_DAYS * 86400
    home = dataclasses.replace(base, site=parse_site(header.get("site")), start_time=0.0, automations=None)
    clock = LogicalClock()
    session = TraceSession(home, twins, header.get("initial_state") or {}, clock)
    engine = Engine(session, permissive=True, automations=[])
    entities = [e for e in entities if e in engine.entities]
    fed = {(e.twin_id, e.feature) for e in engine.entities.values() if e.platform in SENSOR_PLATFORMS}
    for r in reports:
        if (r["twin_id"], r["feature"]) in fed and r["observed_t"] <= end:
            event = Event(r["twin_id"], r["feature"], r["value"], r["t"], r["observed_t"], "trace")
            clock.call_at(r["observed_t"] + REPORT_DELAY_S, lambda event=event: session.deliver(event))
    engine.start()
    out = {e: [(0.0, engine._state.get(e))] for e in entities}  # noqa: SLF001

    def note(_event):
        for e in entities:
            s = engine._state.get(e)  # noqa: SLF001
            if s != out[e][-1][1]:
                out[e].append((clock.now(), s))
    session.subscribe_reports(note)  # after the engine's own handler
    clock.advance_to(end)
    return out


def _dead_env(trace: str) -> tuple[str, dict, dict]:
    from vesper.edt.replay import replay_detailed
    r = replay_detailed(Path(_DW["home_yaml"]), Path(trace), _DW["probes"], catalog_dir=_DW["catalog_dir"])
    fired = {k: bool(v) for k, v in r.firings.items()}
    series = _series(Path(_DW["home_yaml"]), Path(trace), _DW["watched"], _DW["catalog_dir"])
    return trace, fired, series


def _numeric_status(series: list, above, below) -> str:
    def inside(state):
        try:
            v = float(state)
        except (TypeError, ValueError):
            return None
        return (above is None or v > above) and (below is None or v < below)
    flags_ = [inside(s) for _, s in series]
    known = [f for f in flags_ if f is not None]
    if not known or not any(known):
        return "unreachable"
    if all(known):
        return "never_armed"
    return "armed_no_crossing"


def _cond_kind(c) -> str:
    if isinstance(c, str):
        return "template or other"
    if not isinstance(c, dict):
        return "template or other"
    kind = c.get("condition")
    if kind == "sun":
        return "sun condition (unsatisfiable at these latitudes and dates)"
    if kind == "time":
        return "time condition"
    if kind in ("state", "numeric_state"):
        from vesper.edt.corpus import _entity_kinds, _ids
        ids = _ids(c.get("entity_id"))
        attr = c.get("attribute") if isinstance(c.get("attribute"), str) else None
        if any(_entity_kinds(e, attr) & SENSED for e in ids) or any(e.startswith("sun.") for e in ids):
            return "sensed-quantity or sun-state condition"
        return "condition on an entity that never changes (motion, door, actuator)"
    return "template or other"


def dead_rules(s4: dict, lab: Path, c: str, workers: int, catalog_dir: str) -> dict:
    import tempfile

    from vesper.edt.corpus import _entity_kinds, _ids
    from vesper.edt.home import build_edt_home
    autos = ran(s4)
    dead = {i: a for i, a in autos.items() if not any(o["fired"] for o in a["outcomes"].values())}
    with tempfile.TemporaryDirectory() as tmp:
        _, roles = build_edt_home(Path(tmp), catalog_dir)
    probe = [{"action": "switch.turn_on", "target": {"entity_id": roles[("switch", "living", 0)]}}]

    def listed(x):
        return x if isinstance(x, list) else [] if x is None else [x]
    probes, conds = {}, {}
    for i, a in dead.items():
        b = a["bound"]
        trig = b.get("triggers", b.get("trigger"))
        base = {k: v for k, v in b.items() if k in ("trigger_variables", "variables", "mode")}
        cs = listed(b.get("conditions", b.get("condition")))
        conds[i] = cs
        probes[f"{i}|P1"] = {**base, "triggers": trig, "actions": probe}
        probes[f"{i}|P2"] = {**base, "triggers": trig, "conditions": cs, "actions": probe}
        if len(cs) > 1:
            for j in range(len(cs)):
                probes[f"{i}|drop{j}"] = {**base, "triggers": trig, "conditions": cs[:j] + cs[j + 1:],
                                          "actions": probe}
    watched = sorted({e for a in dead.values() for t in listed(a["bound"].get("triggers", a["bound"].get("trigger")))
                      if isinstance(t, dict) for e in _ids(t.get("entity_id"))})
    work = lab / f"work-{c}"
    home_yaml = work / "edt" / "home.yaml"
    rule = s4.get("reporting_rule", "catalog")
    suffix = "" if rule == "catalog" else f"-rho_{rule}"  # vesper.edt.trace.record_trace's file name
    traces = [str(work / "cache" / f"trace-{e.key}{suffix}.jsonl.gz") for e in envs_of(s4)]
    fired_any: dict[str, bool] = defaultdict(bool)
    series_by_env: dict[str, dict] = {}
    with multiprocessing.get_context("spawn").Pool(min(workers, len(traces)), _dead_init,
                                                   (str(home_yaml), catalog_dir, probes, watched)) as pool:
        for trace, fired, series in pool.imap_unordered(_dead_env, traces):
            for k, v in fired.items():
                fired_any[k] |= v
            series_by_env[trace] = series
            print(f"dead-rule probes: {len(series_by_env)}/{len(traces)}", flush=True)
    reasons = {}
    for i, a in dead.items():
        if fired_any[f"{i}|P2"]:
            reasons[i] = "the actions end before any call"
            continue
        if fired_any[f"{i}|P1"]:
            cs = conds[i]
            blockers = [j for j in range(len(cs)) if fired_any.get(f"{i}|drop{j}")] if len(cs) > 1 else [0]
            if len(blockers) == 1:
                reasons[i] = "a condition never holds when a trigger fires: " + _cond_kind(cs[blockers[0]])
            else:
                reasons[i] = "a condition never holds when a trigger fires: several conditions"
            continue
        statuses, inert, other = [], 0, 0
        trig = [t for t in listed(a["bound"].get("triggers", a["bound"].get("trigger")))]
        for t in trig:
            kind = str(t.get("platform") or t.get("trigger") or "") if isinstance(t, dict) else ""
            ids = _ids(t.get("entity_id")) if isinstance(t, dict) else []
            attr = t.get("attribute") if isinstance(t, dict) and isinstance(t.get("attribute"), str) else None
            if kind == "numeric_state" and ids and attr is None and all(_entity_kinds(e) & SENSED for e in ids) \
                    and all(isinstance(t.get(k), (int, float)) or t.get(k) is None for k in ("above", "below")):
                for e in ids:
                    for series in series_by_env.values():
                        statuses.append(_numeric_status(series.get(e, []), t.get("above"), t.get("below")))
            elif kind == "state" and ids and all(len(s.get(e, [])) <= 1 for s in series_by_env.values()
                                                 for e in ids):
                inert += 1
            else:
                other += 1
        if other or not trig:
            reasons[i] = "not derived"
        elif not statuses:
            reasons[i] = "trigger entities never change (no residents; open loop)"
        elif all(s == "unreachable" for s in statuses):
            reasons[i] = "threshold out of the observed range in every environment"
        elif all(s == "never_armed" for s in statuses):
            reasons[i] = "never armed"
        elif all(s in ("unreachable", "never_armed") for s in statuses):
            reasons[i] = "threshold unreachable or never armed, by environment"
        else:
            reasons[i] = "armed numeric_state never crosses in the judged window"
    coupled = {i for i, a in dead.items() if a["kinds"]}
    return {"dead": len(dead), "dead_environment_coupled": len(coupled), "run": len(autos),
            "by_reason": dict(Counter(reasons.values()).most_common()),
            "by_reason_environment_coupled": dict(Counter(reasons[i] for i in coupled).most_common()),
            "environment_coupled_by_quantity": {k: sum(1 for i in coupled if k in dead[i]["kinds"])
                                                for k in ENV_KINDS},
            "by_frames": dict(Counter(frame_label(a) for a in dead.values()).most_common()),
            "probes": {"replayed": len(probes), "environments": len(traces)},
            "status": "post hoc (Addendum 6, A6.6); reasons derived mechanically, not hand-audited"}


# ---- main ----------------------------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lab", type=Path, default=Path.home() / "vesper-lab" / "edt")
    ap.add_argument("--commit", required=True)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--skip-dead", action="store_true", help="leave out the dead-rule probes (for a quick look)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    lab, c = args.lab, args.commit
    s4 = load(lab, c, "4")
    report: dict = {"commit": c, "post_hoc": True, "protocol": "docs/experiments/edt-protocol.md, Addendum 6"}
    old = json.loads((lab / "sweep-rung4-120f4ca.json").read_text())
    check = load(lab, c, "4-check120f4ca")
    report["reproduction"] = {**reproduction(check, old),
                              "run": "rung 4, the 120f4ca binder, the catalog reporting rule (A6.8 as amended)"}
    cat = load(lab, c, "4-catalogrule")
    report["binder_and_reporting_rule"] = effects(old, check, cat, s4)
    del old, check, cat
    gc.collect()
    print("reproduction:", report["reproduction"]["identical_set"], flush=True)
    report["flags"] = flags(s4)
    report["witnesses"] = witnesses(s4)
    report["rates"] = rates(s4)
    report["richer_outcome"] = richer(s4)
    report["pair_cover"] = pair_cover(s4)
    print("pair cover done", flush=True)
    report["rung0"] = rung0(s4)
    report["ladder"] = {"criterion": "the physics is necessary for quantification iff rung 2 does not reproduce rung 4 "
                                     "(A6.7); rung 1 is a sanity check; rung 2.5 is reported beside",
                        "rung4": {"run": s4["yield"]["run"], "clock_flagged": s4["flagged"]["n"],
                                  "frame_flagged": s4["flagged_frame"]["n"]}}
    small = {}
    for rung in ("2.5", "2", "3", "1"):
        sr = load(lab, c, rung)
        report["ladder"][f"rung{rung}"] = ladder_rows(s4, sr)
        if rung in ("2.5", "2"):
            small[rung] = {"automations": {i: {k: a.get(k) for k in (CLOCK_KEY, FRAME_KEY, "outcomes")}
                                           for i, a in sr["automations"].items()}}
        del sr
        gc.collect()
        print(f"ladder rung {rung} done", flush=True)
    report["sensor_split"] = sensor_split(s4, small["2.5"], small["2"])
    del small
    if not args.skip_dead:
        report["dead_rules"] = dead_rules(s4, lab, c, args.workers, args.catalog)
    out = args.out or Path("results/edt") / f"analysis-{c}.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
