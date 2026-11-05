#!/usr/bin/env python3
"""r9z_analyses.py -- VESPER revision R9-Z (post hoc, protocol Addendum 19): the sixth internal review's items 2, 3,
5(a) and 10, on saved runs and cached traces (no sweep is run again; item 2 runs four small closed-loop settings).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `setback`  A19.1  R2-c's 22 setback automations closed loop at R4-B's corners G2-Foff-W1-C165 and G5-Foff-W1-C165,
                    under the hallway thermostat (a check against R4-B's saved runs) and under per-room thermostats.
- `grid`     A19.3  the frame relation at h_clk {1, 3, 6} h x h_sun {60, 180, 360} min on `8fcd672`'s saved outcomes.
- `cores`    A19.2  the attributable orientation core, the all-bracket core restated, the isotropy count, the
                    witnesses' structural features (from R8-X's and R8-Y's saved memberships).
- `clamp`    A19.4(a)  the humidity flags' readings against the 85 % / 15 % clamp (cached traces).
- `dead`     A19.4(b)  the dead rules' three reason predicates (probes replayed on the cached traces).
- `propagation` A19.4(c)  R6-C's discrepancy propagation, blocked by environment.
- `report`   results/edt/r9z-<commit>.json: aggregates only (counts, temperatures, generic room labels, anonymous
             indices): no automation, alias, entity name, repository, hash or path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r9z_analyses.py --part P [--workers 3]

Inputs (local, never committed): --lab/sweep-rung4-*.json, --lab/work-*/ (homes and cached traces), --lab/r4b-scratch,
--lab/r7-A/scratch/dead-reasons.pkl, --lab/r8-X/scratch/members.pkl, --lab/r8-Y/scratch/plan-*.pkl,
--lab/r6-C/pairs-134b209.json.
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "edt_analysis"))
sys.path.insert(0, str(ROOT / "scripts" / "revision"))
sys.path.insert(0, str(ROOT))

from vesper.edt import oracle  # noqa: E402
from vesper.edt.types import Outcome  # noqa: E402

S6 = "8fcd672"
SETBACK_COMMIT = "120f4ca"
PROPAGATION_SEED = 20261105
CLAMP_HI, CLAMP_LO, CLAMP_EPS = 85.0, 15.0, 0.05
WINDOW = (86400.0, 3 * 86400.0)  # days 1-2
PATTERN = {"platform": "time_pattern", "minutes": "/5"}
H_CLK = (1.0, 3.0, 6.0)
H_SUN = (60.0, 180.0, 360.0)
KINDS = ("fires_somewhere_only", "time_spread", "flaps", "night_light", "night_cover")
R4B_LABEL = {"G2": "G2-Foff-W1-C165", "G5": "G5-Foff-W1-C165"}
EID = re.compile(r"\b(?:sensor|binary_sensor|light|switch|climate|cover|weather|input_\w+|fan|lock|media_player|"
                 r"person|device_tracker|group|zone|scene|script|button|number|select|water_heater|humidifier|"
                 r"vacuum|alarm_control_panel|remote|camera)\.[A-Za-z0-9_]+")
TRIGGER_WORD = re.compile(r"\btrigger\b")

# A6.6's cascade, in the order `r4a_analyses.dead_reasons` applies it (A19.4(b))
DEAD_ORDER = [
    "the actions end before any call (the triggers and conditions pass, P2 fires)",
    "a condition never holds when a trigger fires (P1 fires, P2 does not; the blocking condition by drop-one probes: "
    "sun, time, sensed-quantity or sun-state, an entity that never changes, template or other, several)",
    "not derived (a trigger of a kind the derivation does not read, or no trigger)",
    "trigger entities never change (no residents; open loop)",
    "threshold out of the observed range in every environment",
    "never armed",
    "threshold unreachable or never armed, by environment",
    "armed numeric_state never crosses in the judged window",
]


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _span(xs) -> list | None:
    xs = sorted(x for x in xs if x is not None)
    return [_r(xs[0]), _r(statistics.median(xs)), _r(xs[-1])] if xs else None


def dump(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(pickle.dumps(obj))
    tmp.replace(path)


def load_pkl(path: Path):
    return pickle.loads(Path(path).read_bytes())


def load_sweep(lab: Path, name: str) -> dict:
    return json.loads((lab / f"sweep-rung4-{name}.json").read_text())


def envs_by_key(sweep: dict) -> dict:
    import r4a_analyses as r4a
    return {e.key: e for e in r4a.envs_of(sweep)}


# ---- A19.1: the setback corners --------------------------------------------------------------------------------------

def setback_settings() -> dict[str, dict]:
    import r4b_error_budget as r4b
    out = {}
    for tag, g in (("G2", 2.0), ("G5", 5.0)):
        kn = r4b.corner_knobs((g, 0, 1, 165))
        out[f"hall-{tag}"] = dict(kn)
        out[f"room-{tag}"] = {**kn, "PER_ROOM_THERMOSTAT": 1}
    return out


def repository_breakdown(rows: list[dict], repo_of: dict) -> dict:
    """Distinct breaching automations, their repositories and the automations per repository."""
    autos = sorted({r["automation"] for r in rows if r["caused"]})
    per = Counter(repo_of[a] for a in autos)
    return {"automations": len(autos), "repositories": len(per),
            "automations_per_repository": dict(sorted(per.items()))}


def part_setback(a, lab: Path, scratch: Path) -> None:
    import r4b_error_budget as r4b
    out = scratch / "setback.pkl"
    if out.is_file():
        print("have", out.name, flush=True)
        return
    d = r4b.load(lab, SETBACK_COMMIT)
    core = r4b.r2c_envs(d)
    jobs = []
    for label, kn in setback_settings().items():
        for j in r4b.corner_jobs(d, [r4b.NOMINAL], core, a.catalog):
            j["knobs"] = dict(kn)
            j["tag"] = (label, j["tag"][1], j["tag"][2])
            jobs.append(j)
    print(f"setback: {len(jobs)} runs", flush=True)
    t0 = time.monotonic()
    done = r4b._pool(r4b._closed_job, jobs, a.workers, "setback")
    dump(out, {"done": done, "elapsed_s": time.monotonic() - t0})


def report_setback(lab: Path, scratch: Path) -> dict:
    import r2c_setback_population as r2c
    import r4b_error_budget as r4b
    import r8y_physics as r8y
    d = r4b.load(lab, SETBACK_COMMIT)
    core, n_auto = set(r4b.r2c_envs(d)), len(d["pop"])
    repo_ix: dict[str, str] = {}
    for aid in d["pop"]:
        repo_ix.setdefault(d["run"][aid]["repo"], f"R{len(repo_ix) + 1}")
    repo_of = {d["names"][aid]: repo_ix[d["run"][aid]["repo"]] for aid in d["pop"]}
    off = {d["names"][aid] for aid in d["pop"] if r8y.turns_thermostat_off(d["run"][aid]["bound"])}
    r4b_pub = json.loads((ROOT / "results/edt/error-budget-r4b-120f4ca.json").read_text())
    per_corner = r4b_pub["item4_setback_corners"]["per_corner"]
    full = load_pkl(lab / "r4b-scratch" / "full.pkl")
    frows = r2c.judge(full["done"], d["envs"], d["names"], Path(d["cache"]))
    p = load_pkl(scratch / "setback.pkl")
    rows = r2c.judge(p["done"], d["envs"], d["names"], Path(d["cache"]))
    fields = ("coldest_caused_room_c", "caused_cells", "baseline_coldest_room_c", "coldest_room_c", "setback_h")
    out = {"automations": n_auto, "core": sorted(r2c.label(r2c._env(d["envs"][k])) for k in core),
           "elapsed_s": round(p["elapsed_s"], 1), "corners": {}}

    def stats(rs):
        s = r8y.summary(rs, core, n_auto)
        sel = [r for r in rs if r["_key"] in core]
        s.update(repository_breakdown(sel, repo_of))
        s["off_split"] = r8y.off_split(rs, core, off)
        return s

    for tag, label in R4B_LABEL.items():
        saved = [r for r in frows[label] if r["_key"] in core]
        hall, room = [r for r in rows[f"hall-{tag}"] if r["_key"] in core], [r for r in rows[f"room-{tag}"]
                                                                              if r["_key"] in core]
        pub = per_corner[label]
        s_saved = stats(saved)
        check_saved = {"breaching_runs": [s_saved["breaching_runs"], pub["breaching_runs_core"]],
                       "caused_cells": [s_saved["caused_cells"], pub["caused_cells"]],
                       "automations": [s_saved["automations"], pub["automations"]],
                       "repositories": [s_saved["repositories"], pub["repositories"]],
                       "coldest_caused_room_per_run_c": [s_saved["coldest_caused_room_per_run_c"],
                                                         pub["coldest_caused_room_per_run_c"]],
                       "baseline_coldest_room_c": [s_saved["baseline_coldest_room_c"],
                                                   pub["baseline_coldest_room_c"]]}
        mine = {(r["automation"], r["_key"]): r for r in hall}
        ref = {(r["automation"], r["_key"]): r for r in saved}
        rerun = {"same_runs": set(mine) == set(ref),
                 "same_breaching_runs": {k for k, r in mine.items() if r["caused"]}
                 == {k for k, r in ref.items() if r["caused"]},
                 "field_mismatches": sum(1 for k in set(mine) & set(ref) for f in fields
                                         if _r(mine[k][f]) != _r(ref[k][f])), "fields_compared": list(fields)}
        s_hall, s_room = stats(saved), stats(room)
        h_runs, r_runs = set(s_hall.pop("runs_set")), set(s_room.pop("runs_set"))
        stats(hall).pop("runs_set")
        out["corners"][label] = {
            "check_saved_reproduces_r4b": {k: v[0] == v[1] for k, v in check_saved.items()} | {"values": check_saved},
            "check_rerun_hallway_reproduces_saved": rerun,
            "hallway": s_hall, "per_room": s_room,
            "per_room_vs_hallway": {"per_room_subset_of_hallway": r_runs <= h_runs, "lost": len(h_runs - r_runs),
                                    "gained": len(r_runs - h_runs),
                                    "lost_by_automation": dict(sorted(Counter(x for x, _ in h_runs - r_runs).items()))},
            "identity_hallway_vs_per_room": r8y.identity_shift(saved, room),
            "r4b_other_warmups_core": {w: per_corner[label.replace("W1", w)]["breaching_runs_core"]
                                       for w in ("W1", "W5", "W10", "Wpss")},
            "r4b_J_of_132": pub["breaching_runs_J"], "r4b_48_of_1056": pub["breaching_runs_48"],
            "in_context": "not available: R4-B's companion runs (A7.4) are at the nominal corner only; not run"}
    out["turns_thermostat_off"] = {"automations": len(off)}
    out["nominal_g0_for_reference"] = {"hallway_breaching_runs": per_corner["G0-Foff-W1-C165"]["breaching_runs_core"],
                                       "r8y_per_room_breaching_runs": 46}
    return out


# ---- A19.3: the tolerance grid ---------------------------------------------------------------------------------------

def flag_kinds(entry: dict, by_key: dict, h_clk: float, h_sun: float) -> list[str]:
    """The sweep's frame flag at (h_clk, h_sun): frame divergences less an envelope kind holding everywhere."""
    from vesper.edt.sweep import ENVELOPE
    outs = {by_key[k]: Outcome(**o) for k, o in entry["outcomes"].items()}
    if not outs:
        return []
    ft = {by_key[k]: ((entry.get("rich") or {}).get(k) or {}).get("first_t") for k in entry["outcomes"]}
    kinds = oracle.divergences(outs)
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
    return sorted(oracle.frame_divergences(outs, ft, oracle.frames_of(entry["bound"]), h_clk, h_sun_min=h_sun)
                  - static)


def clock_kinds(entry: dict, by_key: dict, h: float) -> list[str]:
    """The clock relation at tolerance h (the sweep's rule, static envelope kinds removed)."""
    from vesper.edt.sweep import ENVELOPE
    outs = {by_key[k]: Outcome(**o) for k, o in entry["outcomes"].items()}
    if not outs:
        return []
    kinds = oracle.divergences(outs) - {"time_spread"}
    tods = [o.first_tod_h for o in outs.values() if o.fired and o.first_tod_h is not None]
    if oracle._clock_spread(tods, h):  # noqa: SLF001
        kinds.add("time_spread")
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
    return sorted(kinds - static)


def frame_class(frames) -> str:
    j = frames.judged
    if len(j) > 1:
        return "mixed"
    return {"clock": "clock only", "sun": "sun only", "sensed": "sensed only"}[next(iter(j))]


def grid_summary(cell: dict[str, list], pub: dict[str, list]) -> dict:
    flagged = {i for i, k in cell.items() if k}
    ref = {i for i, k in pub.items() if k}
    return {"frame_flags": len(flagged), "gained": len(flagged - ref), "lost": len(ref - flagged),
            "same_kinds": sum(1 for i in flagged & ref if sorted(cell[i]) == sorted(pub[i])),
            "by_kind": dict(Counter(k for i in flagged for k in cell[i]))}


def part_grid(a, lab: Path, scratch: Path) -> None:
    s = load_sweep(lab, S6)
    by_key = envs_by_key(s)
    run = {i: x for i, x in s["automations"].items() if x["outcomes"]}
    t0 = time.monotonic()
    cells = {}
    for hc in H_CLK:
        for hs in H_SUN:
            cells[(hc, hs)] = {i: flag_kinds(x, by_key, hc, hs) for i, x in run.items()}
            print(f"grid {hc:g} h / {hs:g} min: {sum(1 for v in cells[(hc, hs)].values() if v)} flags "
                  f"({time.monotonic() - t0:.0f} s)", flush=True)
    clock = {h: {i: clock_kinds(x, by_key, h) for i, x in run.items()} for h in H_CLK}
    cls = {i: frame_class(oracle.frames_of(x["bound"])) for i, x in run.items()}
    pub = {i: sorted(x.get("frame_divergences") or []) for i, x in run.items()}
    pub_clock = {i: sorted(x.get("divergences") or []) for i, x in run.items()}
    dump(scratch / "grid.pkl", {"cells": cells, "clock": clock, "class": cls, "pub": pub, "pub_clock": pub_clock,
                                "run": len(run), "elapsed_s": time.monotonic() - t0})


def report_grid(scratch: Path) -> dict:
    g = load_pkl(scratch / "grid.pkl")
    pub, cls = g["pub"], g["class"]
    nominal = g["cells"][(3.0, 180.0)]
    out = {"run_automations": g["run"],
           "check_nominal_reproduces_published": {
               "flags": sum(1 for v in nominal.values() if v), "published": sum(1 for v in pub.values() if v),
               "kind_mismatches": sum(1 for i in pub if sorted(nominal[i]) != pub[i])},
           "cells": {}, "clock_relation": {}}
    for (hc, hs), cell in g["cells"].items():
        s = grid_summary(cell, pub)
        ts = [i for i, k in cell.items() if "time_spread" in k]
        s["time_spread_by_frames"] = dict(sorted(Counter(cls[i] for i in ts).items()))
        out["cells"][f"h_clk {hc:g} h / h_sun {hs:g} min"] = s
    for h, cell in g["clock"].items():
        s = grid_summary(cell, g["pub_clock"])
        out["clock_relation"][f"h {h:g} h"] = s
    out["clock_check_3h_reproduces_published"] = out["clock_relation"]["h 3 h"]["gained"] == 0 and \
        out["clock_relation"]["h 3 h"]["lost"] == 0
    out["elapsed_s"] = round(g["elapsed_s"], 1)
    return out


# ---- A19.2: the orientation cores ------------------------------------------------------------------------------------

_RELEVANT = {"R7-A": {"ap0.21", "ap0.25", "ap0.29", "ap0.5", "ap2", "ds0.6", "ds0.95"}, "R7-W": {"shortwave"}}
_UNPERTURBED = {"R7-A": {"nominal"}, "R7-B corners": {"ref"}, "R7-B offsets": {"ref"}}


def setting_class(source: str, setting: str) -> str:
    """A19.2(c)'s rule: orientation-relevant (the term moves with the irradiance on a room's windows), unperturbed
    (a copy of the nominal model), or orientation-isotropic (everything else among the 139)."""
    if setting in _UNPERTURBED.get(source, set()):
        return "unperturbed"
    if setting in _RELEVANT.get(source, set()):
        return "orientation-relevant"
    return "orientation-isotropic"


def core_counts(published: set, settings: dict[str, set]) -> dict:
    """The published members held at every setting, the possible set, the core without each setting, and how many
    settings hold each published member."""
    def core(names):
        return set(published).intersection(*[settings[n] for n in names])
    names = list(settings)
    held = Counter(sum(1 for s in settings.values() if i in s) for i in published)
    union = set().union(*settings.values()) if settings else set()
    return {"of": len(published), "settings": len(names), "core": len(core(names)),
            "possible": len(set(published) | union), "possible_outside_published": len(union - set(published)),
            "core_without": {n: len(core([m for m in names if m != n])) for n in names},
            "held_in": {str(k): held.get(k, 0) for k in range(len(names) + 1)}}


def _entities(node) -> set[str]:
    return set(EID.findall(json.dumps(node, default=str)))


def _part(bound: dict, *keys):
    return [bound.get(k) for k in keys if bound.get(k) is not None]


def rooms_read(bound: dict, role_of: dict) -> set[str]:
    ents = _entities(_part(bound, "trigger", "triggers", "condition", "conditions"))
    return {role_of[e][1] for e in ents if e in role_of}


def rooms_acted(bound: dict, role_of: dict) -> set[str]:
    ents = _entities(_part(bound, "action", "actions"))
    return {role_of[e][1] for e in ents if e in role_of}


def features_read(bound: dict, role_of: dict) -> set[str]:
    ents = _entities(_part(bound, "trigger", "triggers", "condition", "conditions"))
    return {role_of[e][0] for e in ents if e in role_of}


def facings(rooms: set, windows: dict[str, set]) -> set:
    return set().union(*[windows.get(r, set()) for r in rooms]) if rooms else set()


def feature_table(core: set, all_ids: set, feats: dict[str, set]) -> dict:
    out = defaultdict(lambda: {"core": 0, "outside": 0})
    for i in all_ids:
        for f in feats[i]:
            out[f]["core" if i in core else "outside"] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def _pair_kinds(entry: dict, by_key: dict, pairs) -> set[str]:
    outs = {by_key[k]: Outcome(**o) for k, o in entry["outcomes"].items()}
    ft = {by_key[k]: ((entry.get("rich") or {}).get(k) or {}).get("first_t") for k in entry["outcomes"]}
    fr = oracle.frames_of(entry["bound"])
    out = set()
    for x, y in pairs:
        if x in outs and y in outs:
            out |= oracle.frame_restricted_divergences(outs, ft, fr, [x, y])
    return out


def part_cores(a, lab: Path, scratch: Path) -> None:
    import r8x_analyses as r8x
    from vesper.edt.home import build_edt_home
    x_scratch = lab / "r8-X" / "scratch"
    m = load_pkl(x_scratch / "members.pkl")
    R = m["records"]
    fam = {k: r8x._fam(v) for k, v in R.items()}  # noqa: SLF001
    pub = fam[("whole", S6)]
    W = "orientation_witnesses"
    pw = load_pkl(lab / "r8-Y" / "scratch" / "plan-witness-ids.pkl")
    plans_w = {n: set(v) for n, v in pw["witnesses"].items()}
    out = {"published": {f: len(s) for f, s in pub.items()},
           "check_plan_published_equals_members": set(pw["published"]) == pub[W]}
    sw = fam[("whole", r8x.SW)]
    g05, g2 = fam[("whole", r8x.VARIANTS["glazing x0.5"])], fam[("whole", r8x.VARIANTS["glazing x2"])]
    mirror = fam[("whole", r8x.VARIANTS["mirror east-west"])]
    attributable = {"recorded beam (A14)": sw[W], "glazing x0.5": g05[W], "glazing x2": g2[W],
                    "plan seed 9": plans_w["seed9"], "plan seed 11": plans_w["seed11"],
                    "plan seed 16": plans_w["seed16"]}
    brackets = {"recorded beam": ["recorded beam (A14)"], "glazing": ["glazing x0.5", "glazing x2"],
                "plans 9/11/16": ["plan seed 9", "plan seed 11", "plan seed 16"]}
    out["check_single_brackets"] = {b: len(pub[W].intersection(*[attributable[n] for n in ns]))
                                    for b, ns in brackets.items()}
    out["attributable_core"] = core_counts(pub[W], attributable)
    out["attributable_core_without_bracket"] = {
        b: len(pub[W].intersection(*[attributable[n] for n in attributable if n not in ns]))
        for b, ns in brackets.items()}
    out["attributable_core_with_seed8_and_mirror"] = core_counts(
        pub[W], attributable | {"plan seed 8": plans_w["seed8"], "mirror east-west": mirror[W]})
    # secondary: the frame-flag family over the 6 (the plans' within the 199, from R8-Y's records)
    plan_flags = {}
    import r4b_error_budget as r4b
    import r7a_analyses as r7a
    d = r4b.load(lab, S6)
    ids = sorted(i for i, x in d["run"].items() if x.get("frame_divergences"))
    envs = [e for e in envs_by_key(d["sweep"]).values()]
    pairs = r7a.orient_pairs(envs)
    for n in ("seed9", "seed11", "seed16"):
        f = r7a.setting_findings(d, ids, load_pkl(lab / "r8-Y" / "scratch" / f"plan-{n}.pkl"), envs, pairs)
        plan_flags[n] = {i for i in ids if f[i]["frame"]}
        if {i for i in ids if f[i]["witness"]} != plans_w[n]:
            out.setdefault("check_plan_witnesses_recomputed", {})[n] = False
    out.setdefault("check_plan_witnesses_recomputed", {}).update(
        {n: True for n in plan_flags if out["check_plan_witnesses_recomputed"].get(n) is not False})
    ff = "frame_flags"
    out["attributable_frame_flags"] = core_counts(
        pub[ff], {"recorded beam (A14)": sw[ff], "glazing x0.5": g05[ff], "glazing x2": g2[ff],
                  "plan seed 9": plan_flags["seed9"], "plan seed 11": plan_flags["seed11"],
                  "plan seed 16": plan_flags["seed16"]})
    # (b) the all-bracket core, restated by R8-X's own code, and with the plans
    cr = r8x.core_report(x_scratch)
    out["all_bracket_139"] = {f: cr["joint_139"][f]["joint_core"] for f in r8x.FAMILIES}
    out["all_bracket_141_with_glazing"] = {f: cr["joint_141_with_glazing"][f]["joint_core"] for f in r8x.FAMILIES}
    r7a_set = {n: fam[("R7-A", n)] for n in r8x.R7A_SETTINGS} | {"nominal": pub, "corrected": fam[("whole", r8x.RD)]}
    corners = {k[1]: v for k, v in fam.items() if k[0] == "R7-B corners"}
    offs = {n: fam[("R7-B offsets", n)] for n in r8x.OFFSETS_REGISTERED}
    settings139 = ([("R7-A", n, s) for n, s in r7a_set.items()] + [("R7-B corners", n, s) for n, s in corners.items()]
                   + [("R7-B offsets", n, s) for n, s in offs.items()] + [("R7-W", "shortwave", sw)])
    w139 = pub[W].intersection(*[s[W] for _, _, s in settings139])
    out["check_139_settings"] = len(settings139)
    out["check_139_witness_core"] = len(w139)
    w144 = w139 & g05[W] & g2[W] & plans_w["seed9"] & plans_w["seed11"] & plans_w["seed16"]
    out["all_bracket_144_with_glazing_and_plans_witnesses"] = len(w144)
    # (c) isotropy
    cls = Counter(setting_class(src, n) for src, n, _ in settings139)
    out["isotropy"] = {"classes": dict(cls), "r7b_corners": len(corners),
                       "by_source": {src: dict(Counter(setting_class(s2, n) for s2, n, _ in settings139 if s2 == src))
                                     for src in ("R7-A", "R7-B corners", "R7-B offsets", "R7-W")}}
    for c in ("orientation-relevant", "orientation-isotropic"):
        sel = [s[W] for src, n, s in settings139 if setting_class(src, n) == c]
        out["isotropy"][f"witness_core_over_{c}_only"] = len(pub[W].intersection(*sel))
    # (d) structural features of the attributable core's witnesses against the rest of the 100
    core_ids = pub[W].intersection(*attributable.values())
    s = d["sweep"]
    with tempfile.TemporaryDirectory() as tmp:
        home_yaml, roles = build_edt_home(Path(tmp), a.catalog)
        same_home = home_yaml.read_text() == (lab / f"work-{S6}" / "edt" / "home.yaml").read_text()
        wins = json.loads((Path(tmp) / "edt" / "windows.json").read_text())["items"]
        same_windows = wins == json.loads((lab / f"work-{S6}" / "edt" / "windows.json").read_text())["items"]
    role_of = {eid: k for k, eid in roles.items()}
    windows = defaultdict(set)
    for w in wins:
        windows[w["room"]].add(int(w["facing"]))
    by_key = envs_by_key(s)
    feats = defaultdict(dict)
    for i in pub[W]:
        x = s["automations"][i]
        b = x["bound"]
        rr = rooms_read(b, role_of)
        fac = facings(rr, windows)
        feats["rooms read"][i] = rr or {"no room-scoped role"}
        feats["rooms acted on"][i] = rooms_acted(b, role_of) or {"none"}
        feats["window facings of the rooms read"][i] = {str(f) for f in fac} or {"none (windowless or no room)"}
        feats["one facing only"][i] = {"one facing" if len(fac) == 1 else "several facings" if fac else "no facing"}
        feats["rooms read (count)"][i] = {str(len(rr))}
        feats["read kind"][i] = {r7a.read_kind(x)}
        feats["environment kinds"][i] = set(x["kinds"]) or {"none"}
        feats["role features read"][i] = features_read(b, role_of) or {"none"}
        feats["frame kinds"][i] = set(x["frame_divergences"])
        feats["kinds on the witnessing orientation pairs"][i] = _pair_kinds(x, by_key, pairs) or {"none"}
        feats["frames judged"][i] = {frame_class(oracle.frames_of(b))}
    out["features"] = {"core": len(core_ids), "outside": len(pub[W] - core_ids),
                       "check_rebuilt_home_equals_8fcd672": same_home, "check_windows_equal": same_windows,
                       "window_facings_by_room": {r: sorted(v) for r, v in sorted(windows.items())},
                       "tables": {name: feature_table(core_ids, pub[W], f) for name, f in feats.items()}}
    dump(scratch / "cores.pkl", out)


# ---- A19.4(a): the humidity clamp ------------------------------------------------------------------------------------

def clamped(v) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x >= CLAMP_HI - CLAMP_EPS or x <= CLAMP_LO + CLAMP_EPS


def value_at(series: list, t: float):
    """The last value at or before `t` (None before the first)."""
    v = None
    for ts, x in series:
        if ts > t:
            break
        v = x
    return v


def reaches_clamp(series: list, lo: float, hi: float) -> bool:
    """Whether the value held at some instant in [lo, hi) is clamped."""
    if clamped(value_at(series, lo)):
        return True
    return any(lo <= t < hi and clamped(x) for t, x in series)


def bound_beyond_clamp(above, below) -> bool:
    return (above is not None and above >= CLAMP_HI) or (below is not None and below <= CLAMP_LO)


def part_clamp(a, lab: Path, scratch: Path) -> None:
    import r4a_analyses as r4a
    import r6a_analyses as r6a
    from vesper.edt.home import build_edt_home
    s = load_sweep(lab, S6)
    run = r4a.ran(s)
    flags = sorted(i for i, x in run.items() if x["frame_divergences"] and "humidity" in x["kinds"])
    with tempfile.TemporaryDirectory() as tmp:
        _, roles = build_edt_home(Path(tmp), a.catalog)
    role_of = {eid: k for k, eid in roles.items()}
    reads = {}
    for i in flags:
        ents = _entities(_part(run[i]["bound"], "trigger", "triggers", "condition", "conditions"))
        reads[i] = sorted(e for e in ents if e in role_of and role_of[e][0] == "humidity")
    watched = {e for v in reads.values() for e in v}
    envs = r4a.envs_of(s)
    work = lab / f"work-{S6}"
    t0 = time.monotonic()
    series = r6a.env_series(envs, work / "cache", work / "edt" / "home.yaml", watched, set(), a.workers, a.catalog)
    by_key = {e.key: e for e in envs}
    rows = []
    for i in flags:
        x = run[i]
        refs = oracle.sensed_crossing_refs(x["bound"])
        hum_refs = [r for r in refs if any(e in reads[i] for e in r.entities)]
        beyond = any(bound_beyond_clamp(r.above, r.below) for r in hum_refs)
        for k, o in x["outcomes"].items():
            env = by_key[k]
            ser = series.get(env, {})
            indoor = [e for e in reads[i] if role_of[e][1] != "porch"]
            porch = [e for e in reads[i] if role_of[e][1] == "porch"]
            ft = ((x.get("rich") or {}).get(k) or {}).get("first_t")
            at_first = o["fired"] and ft is not None and any(clamped(value_at(ser.get(e, []), ft)) for e in indoor)
            cross = oracle.crossing_instant(hum_refs, ser, *WINDOW) if hum_refs else None
            at_cross = cross is not None and any(clamped(value_at(ser.get(e, []), cross)) for e in indoor)
            rows.append({"flag": i, "env": k, "city": env.city.split(",")[0], "fired": o["fired"],
                         "reads_indoor": bool(indoor), "reads_porch": bool(porch),
                         "at_first_firing": at_first, "at_crossing": at_cross, "bound_beyond_clamp": beyond,
                         "reaches_clamp": any(reaches_clamp(ser.get(e, []), *WINDOW) for e in indoor),
                         "porch_reaches_85": any(reaches_clamp(ser.get(e, []), *WINDOW) for e in porch)})
    dump(scratch / "clamp.pkl", {"rows": rows, "flags": len(flags),
                                 "reads": {i: len(v) for i, v in reads.items()},
                                 "no_humidity_role_read": sum(1 for v in reads.values() if not v),
                                 "elapsed_s": time.monotonic() - t0})


def report_clamp(scratch: Path) -> dict:
    c = load_pkl(scratch / "clamp.pkl")
    rows = c["rows"]
    anon = {i: f"H{k + 1}" for k, i in enumerate(sorted({r["flag"] for r in rows}))}

    def flagset(key):
        return {r["flag"] for r in rows if r[key]}
    out = {"humidity_frame_flags": c["flags"], "flags_reading_no_humidity_role": c["no_humidity_role_read"],
           "flags_reading_an_indoor_humidity_role": len({r["flag"] for r in rows if r["reads_indoor"]}),
           "flags_reading_the_porch_humidity": len({r["flag"] for r in rows if r["reads_porch"]}),
           "cells": len(rows)}
    for key in ("at_first_firing", "at_crossing", "bound_beyond_clamp", "reaches_clamp", "porch_reaches_85"):
        fs = flagset(key)
        cells = [r for r in rows if r[key]]
        out[key] = {"flags": len(fs), "cells": len(cells), "by_city": dict(sorted(Counter(r["city"] for r in cells)
                                                                                .items())),
                    "flags_anonymous": sorted(anon[i] for i in fs)}
    out["either_first_firing_or_crossing"] = len(flagset("at_first_firing") | flagset("at_crossing"))
    out["elapsed_s"] = round(c["elapsed_s"], 1)
    return out


# ---- A19.4(b): dead-rule reason predicates ---------------------------------------------------------------------------

def _listed(x):
    return x if isinstance(x, list) else [] if x is None else [x]


def dead_probes(aid: str, bound: dict, probe: list) -> dict:
    trig = bound.get("triggers", bound.get("trigger"))
    base = {k: v for k, v in bound.items() if k in ("trigger_variables", "variables", "mode", "max")}
    cs = _listed(bound.get("conditions", bound.get("condition")))
    acts = bound.get("actions", bound.get("action"))
    return {f"{aid}|P1": {**base, "triggers": trig, "actions": probe},
            f"{aid}|P3": {**base, "triggers": [PATTERN], "conditions": cs, "actions": probe},
            f"{aid}|P4": {**base, "triggers": [PATTERN], "actions": acts}}


def _names_trigger(node) -> bool:
    return bool(TRIGGER_WORD.search(json.dumps(node, default=str)))


def reason_predicates(aid: str, bound: dict, fired: dict) -> dict:
    """A19.4(b): T the triggers never fire; C a condition never holds (None: it names the trigger); A the actions
    end before any call (None: they name the trigger)."""
    cs = _listed(bound.get("conditions", bound.get("condition")))
    acts = bound.get("actions", bound.get("action"))
    c = None if cs and _names_trigger(cs) else (bool(cs) and not fired.get(f"{aid}|P3", False))
    act = None if _names_trigger(acts) else not fired.get(f"{aid}|P4", False)
    return {"T": not fired.get(f"{aid}|P1", False), "C": c, "A": act}


def multiply_caused(p: dict) -> bool:
    return sum(1 for v in p.values() if v is True) >= 2


def part_dead(a, lab: Path, scratch: Path) -> None:
    import multiprocessing

    import r4a_analyses as r4a
    from vesper.edt.home import build_edt_home
    s = load_sweep(lab, S6)
    run = r4a.ran(s)
    coupled = sorted(i for i, x in run.items() if x["kinds"] and not any(o["fired"] for o in x["outcomes"].values()))
    with tempfile.TemporaryDirectory() as tmp:
        _, roles = build_edt_home(Path(tmp), a.catalog)
    probe = [{"action": "switch.turn_on", "target": {"entity_id": roles[("switch", "living", 0)]}}]
    probes = {}
    for i in coupled:
        probes |= dead_probes(i, run[i]["bound"], probe)
    work = lab / f"work-{S6}"
    suffix = "" if s.get("reporting_rule", "catalog") == "catalog" else f"-rho_{s['reporting_rule']}"
    traces = [str(work / "cache" / f"trace-{e.key}{suffix}.jsonl.gz") for e in r4a.envs_of(s)]
    fired_any: dict[str, bool] = defaultdict(bool)
    t0 = time.monotonic()
    with multiprocessing.get_context("spawn").Pool(min(a.workers, len(traces)), r4a._dead_init,  # noqa: SLF001
                                                   (str(work / "edt" / "home.yaml"), a.catalog, probes, [])) as pool:
        for k, (_trace, fired, _series) in enumerate(pool.imap_unordered(r4a._dead_env, traces), 1):  # noqa: SLF001
            for key, v in fired.items():
                fired_any[key] |= v
            print(f"dead probes {k}/{len(traces)} ({time.monotonic() - t0:.0f} s)", flush=True)
    dump(scratch / "dead.pkl", {"coupled": coupled, "fired": dict(fired_any), "probes": len(probes),
                                "environments": len(traces), "elapsed_s": time.monotonic() - t0})


def report_dead(lab: Path, scratch: Path) -> dict:
    p = load_pkl(scratch / "dead.pkl")
    s = load_sweep(lab, S6)
    cached = load_pkl(lab / "r7-A" / "scratch" / "dead-reasons.pkl")["reasons"]
    preds = {i: reason_predicates(i, s["automations"][i]["bound"], p["fired"]) for i in p["coupled"]}

    def stage(reason: str) -> str:
        return ("actions" if reason.startswith("the actions end") else
                "condition" if reason.startswith("a condition never holds") else "trigger")
    st = {i: stage(cached[i]) for i in p["coupled"]}
    check = {"trigger_reasons_with_T": sum(1 for i in preds if st[i] == "trigger" and preds[i]["T"]),
             "trigger_reasons": sum(1 for i in preds if st[i] == "trigger"),
             "action_or_condition_reasons_without_T": sum(1 for i in preds if st[i] != "trigger"
                                                          and not preds[i]["T"]),
             "action_or_condition_reasons": sum(1 for i in preds if st[i] != "trigger")}
    check["holds"] = (check["trigger_reasons_with_T"] == check["trigger_reasons"]
                      and check["action_or_condition_reasons_without_T"] == check["action_or_condition_reasons"])

    def combo(q):
        return "+".join(k for k in ("T", "C", "A") if q[k] is True) or "none"
    multi = {i for i, q in preds.items() if multiply_caused(q)}
    return {"population": len(p["coupled"]), "cascade_order": DEAD_ORDER, "check_T_against_a66": check,
            "multiply_caused": len(multi),
            "combinations": dict(sorted(Counter(combo(q) for q in preds.values()).items())),
            "not_evaluable": {"C": sum(1 for q in preds.values() if q["C"] is None),
                              "A": sum(1 for q in preds.values() if q["A"] is None)},
            "predicate_counts": {k: sum(1 for q in preds.values() if q[k] is True) for k in ("T", "C", "A")},
            "multiply_caused_by_a66_reason": dict(sorted(Counter(cached[i] for i in multi).items())),
            "by_a66_reason": {r: {"n": n, "multiply_caused": sum(1 for i in multi if cached[i] == r),
                                  "C_true": sum(1 for i in preds if cached[i] == r and preds[i]["C"] is True),
                                  "A_true": sum(1 for i in preds if cached[i] == r and preds[i]["A"] is True)}
                              for r, n in sorted(Counter(cached[i] for i in p["coupled"]).items())},
            "pattern": "time_pattern every 5 min", "probes": p["probes"], "environments": p["environments"],
            "elapsed_s": round(p["elapsed_s"], 1)}


# ---- A19.4(c): environment-blocked propagation -----------------------------------------------------------------------

def assign_environments(rng: random.Random, sweep_keys: list[str], compared: list[str]) -> dict[str, str]:
    return {k: rng.choice(compared) for k in sweep_keys}


def propagate_blocked(autos: dict, flag_ids: list[str], pairs_by_env: dict[str, list[dict]], envs_by_key: dict,
                      reps: int = 1000, seed: int = PROPAGATION_SEED, keep: bool = False) -> dict:
    """A19.4(c): A10.5(b)'s propagation with each sweep environment assigned, per replicate, one compared environment
    (uniformly, with replacement); a flag's outcome there draws a pair of the same VESPER firing status from that
    environment's pairs only. Home Assistant's first-firing times for a VESPER-silent pair that it fires are drawn
    from every observed one, as A10.5(b)."""
    from vesper.validation.ha_diff import _divergences, _shift_outcome, _summary
    rng = random.Random(seed)
    compared = sorted(pairs_by_env)
    every = [p for ps in pairs_by_env.values() for p in ps]
    h_tods = [p["h_first"] for p in every if p["h_fired"] and not p["v_fired"]]
    fire = {c: [p for p in ps if p["v_fired"]] for c, ps in pairs_by_env.items()}
    silent = {c: [p for p in ps if not p["v_fired"]] for c, ps in pairs_by_env.items()}
    keys = sorted({k for i in flag_ids for k in autos[i]["outcomes"]})
    changed_frame, changed_clock, per_rep = [], [], []
    per_flag = Counter()
    for _ in range(reps):
        assign = assign_environments(rng, keys, compared)
        nf = nc = 0
        changed = set()
        for i in flag_ids:
            e = autos[i]
            outs = {kk: dict(v) for kk, v in e["outcomes"].items()}
            rich = {kk: dict(v) for kk, v in (e.get("rich") or {}).items()}
            for kk, o in outs.items():
                r = rich.get(kk) or {}
                c = assign[kk]
                if o.get("fired"):
                    p = rng.choice(fire[c]) if fire[c] else None
                    if p is None:
                        continue
                    if not p["h_fired"]:
                        outs[kk] = {**o, "fired": False, "first_tod_h": None, "flaps": False, "night_light": False,
                                    "night_cover": False}
                        rich[kk] = {**r, "first_t": None}
                    elif p["delta_s"]:
                        outs[kk], rich[kk] = _shift_outcome(o, r, -p["delta_s"])
                else:
                    p = rng.choice(silent[c]) if silent[c] else None
                    if p is not None and p["h_fired"] and h_tods:
                        t = rng.choice(h_tods)
                        outs[kk] = {**o, "fired": True, "first_tod_h": (t % 86400.0) / 3600.0}
                        rich[kk] = {**r, "first_t": 86400.0 + (t % 86400.0)}
            clock, frame = _divergences(e, outs, rich, envs_by_key)
            if frame != (e.get("frame_divergences") or []):
                nf += 1
                per_flag[i] += 1
                changed.add(i)
            if clock != (e.get("divergences") or []):
                nc += 1
        changed_frame.append(nf)
        changed_clock.append(nc)
        if keep:
            per_rep.append(changed)
    out = {"reps": reps, "frame_changed": _summary(changed_frame), "clock_changed": _summary(changed_clock),
           "flags_ever_changed": len(per_flag), "n": len(flag_ids)}
    if keep:
        out["per_replicate"] = per_rep
    return out


def part_propagation(a, lab: Path, scratch: Path) -> None:
    from vesper.edt.trace import ENVIRONMENTS
    from vesper.validation.ha_diff import propagate
    pairs = json.loads((lab / "r6-C" / "pairs-134b209.json").read_text())
    pub = json.loads((ROOT / "results/edt/r6c-ha-diff-533ceb1.json").read_text())
    autos = load_sweep(lab, S6)["automations"]
    sample = [tuple(x) for x in pairs["sample"]]
    artifact = {(x["env"], sample[x["index"]][0]) for x in pub["discrepancies"] if x["class"] == "harness artifact"}
    rows = [(env, k, pairs["per_env"][env]["per"][k]) for env in pairs["per_env"] for k, _ in sample]
    obs = [({**p, "h_fired": p["v_fired"], "delta_s": 0.0 if p["v_fired"] else None} if (e, k) in artifact else p)
           for e, k, p in rows]
    ebk = {e.key: e for e in ENVIRONMENTS}
    frame_flags = sorted(k for k, x in autos.items() if x.get("outcomes") and x.get("frame_divergences"))
    ts = [k for k in frame_flags if "time_spread" in autos[k]["frame_divergences"]]
    t0 = time.monotonic()
    groups = defaultdict(list)
    for (_e, k, _p), q in zip(rows, obs, strict=True):
        groups[k].append(q)
    by_env = defaultdict(list)
    for (e, _k, _p), q in zip(rows, obs, strict=True):
        by_env[e].append(q)
    res = {"registered": {"frame": propagate(autos, frame_flags, obs, ebk, 1000),
                          "time_spread": propagate(autos, ts, obs, ebk, 1000)},
           "by_automation": {"frame": propagate(autos, frame_flags, obs, ebk, 1000, groups=list(groups.values())),
                             "time_spread": propagate(autos, ts, obs, ebk, 1000, groups=list(groups.values()))}}
    print(f"propagation: checks ({time.monotonic() - t0:.0f} s)", flush=True)
    res["environment_blocked"] = {"frame": propagate_blocked(autos, frame_flags, dict(by_env), ebk, 1000),
                                  "time_spread": propagate_blocked(autos, ts, dict(by_env), ebk, 1000)}
    pp = pub["flags"]["propagation"]
    res["check"] = {
        "registered_frame": res["registered"]["frame"]["frame_changed"] == pp["primary (artifacts agree)"]["frame"][
            "frame_changed"],
        "registered_time_spread": res["registered"]["time_spread"]["frame_changed"]
        == pp["primary (artifacts agree)"]["time_spread"]["frame_changed"],
        "by_automation_frame": res["by_automation"]["frame"]["frame_changed"]
        == pp["post hoc: by automation (artifacts agree)"]["frame"]["frame_changed"],
        "by_automation_time_spread": res["by_automation"]["time_spread"]["frame_changed"]
        == pp["post hoc: by automation (artifacts agree)"]["time_spread"]["frame_changed"]}
    res["compared_environments"] = len(by_env)
    res["pairs"] = len(rows)
    res["artifact_pairs"] = len(artifact)
    res["elapsed_s"] = time.monotonic() - t0
    dump(scratch / "propagation.pkl", res)


def report_propagation(scratch: Path) -> dict:
    r = load_pkl(scratch / "propagation.pkl")
    limit = 9

    def brief(x):
        f = x["frame_changed"]
        return {"mean": _r(f["mean"], 2), "p2.5": f["p2.5"], "p97.5": f["p97.5"], "max": f["max"],
                "flags_ever_changed": x["flags_ever_changed"], "n": x["n"]}
    out = {k: {fam: brief(v) for fam, v in r[k].items()} for k in ("registered", "by_automation",
                                                                    "environment_blocked")}
    out["check_reproduces_r6c"] = r["check"]
    out["a10_7_limit_frame_p97_5"] = limit
    out["over_limit_by"] = {k: out[k]["frame"]["p97.5"] - limit for k in ("registered", "by_automation",
                                                                         "environment_blocked")}
    out |= {"compared_environments": r["compared_environments"], "pairs": r["pairs"],
            "artifact_pairs": r["artifact_pairs"], "elapsed_s": round(r["elapsed_s"], 1)}
    return out


# ---- report ----------------------------------------------------------------------------------------------------------

def report(a, lab: Path, scratch: Path) -> int:
    commit = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT).stdout.strip()
    res = {"analysis": "revision R9-Z (protocol Addendum 19)", "code_commit": commit,
           "inputs": {"frame": S6, "setback": SETBACK_COMMIT, "r6c_pairs": "134b209"}}
    if (scratch / "setback.pkl").is_file():
        res["item2_setback"] = report_setback(lab, scratch)
    if (scratch / "cores.pkl").is_file():
        res["item3_cores"] = load_pkl(scratch / "cores.pkl")
    if (scratch / "grid.pkl").is_file():
        res["item5a_tolerance_grid"] = report_grid(scratch)
    if (scratch / "clamp.pkl").is_file():
        res["item10a_clamp"] = report_clamp(scratch)
    if (scratch / "dead.pkl").is_file():
        res["item10b_dead"] = report_dead(lab, scratch)
    if (scratch / "propagation.pkl").is_file():
        res["item10c_propagation"] = report_propagation(scratch)
    out = Path(a.out) if a.out else ROOT / "results/edt" / f"r9z-{commit}.json"
    out.write_text(json.dumps(res, indent=1, sort_keys=True, default=lambda x: sorted(x) if isinstance(x, set)
                              else str(x)) + "\n")
    print("wrote", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True,
                    choices=("setback", "grid", "cores", "clamp", "dead", "propagation", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r9-Z/scratch"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    parts = {"setback": part_setback, "grid": part_grid, "cores": part_cores, "clamp": part_clamp,
             "dead": part_dead, "propagation": part_propagation}
    if a.part == "report":
        return report(a, lab, scratch)
    parts[a.part](a, lab, scratch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
