#!/usr/bin/env python3
"""r7a_analyses.py -- VESPER revision R7-A (post hoc, protocol Addendum 12): the fourth internal review's items 1(b, d,
f), 4, 7, 8(a-c), 10, 11(a), 12 and 13, on the cached sweeps, open-loop replays, audits and pair records, plus the
null-difference control.

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `witness`  A12.1  the three orientation-witness populations and their overlaps.
- `openloop` A12.1, A12.4  the frame set replayed open loop at R6-B's settings (cached traces, hard-linked) and at
                    R4-B's outdoor-air and humidity-class settings (traces recorded afresh); --settings picks them.
- `recon`    A12.2, A12.3, A12.5, A12.6, A12.9  bookkeeping, the discrepant pair, the binder discount, the
                    non-default semantics, the not-environment-coupled flags (dumped locally for hand reading).
- `dead`     A12.8  the mechanical dead reasons re-derived at 8fcd672 for the environment-coupled dead automations.
- `crossing` A12.10 the named variant frame+crossing+env_latency on the 8fcd672 cache.
- `cover`    A12.11 the six-environment cover: LP bound, MILP, local search, greedy.
- `null`     A12.7  the null-difference control: weather for the new sites, two duplicate caches, the near-identical
                    pairs.
- `report`   results/edt/r7a-<commit>.json: aggregates only (counts, rates, environment labels, anonymous indices):
             no automation, alias, entity name, repository, hash or path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r7a_analyses.py --part P [--workers 3]

Inputs (local, never committed): --lab/sweep-rung4-{120f4ca,8fcd672,r6d-5b54586}.json, --lab/work-8fcd672 (home,
traces, weather), --lab/work-120f4ca/cache/weather, R6-B's open-loop caches, the R4-C labels, R6-C's pair records,
and the hand labels of A12.9 (--scratch/../labels-nonenv.tsv).
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import pickle
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "edt_analysis"))
sys.path.insert(0, str(ROOT / "scripts" / "revision"))
sys.path.insert(0, str(ROOT))
import r2c_setback_population as r2c  # noqa: E402
import r4a_analyses as r4a  # noqa: E402
import r4b_error_budget as r4b  # noqa: E402
import r6b_physics as r6b  # noqa: E402
from revision_analyses import wilson  # noqa: E402

from vesper.edt import oracle  # noqa: E402
from vesper.edt.cover import _axis, _differ, half, restricted_divergences  # noqa: E402
from vesper.edt.types import Env, Outcome  # noqa: E402

S6, SR, RD = "8fcd672", "120f4ca", "5b54586"
CLOCK_KEY, FRAME_KEY = "divergences", "frame_divergences"
ENV_KINDS = ("sun", "temperature", "humidity", "illuminance", "weather")
SEED = 0
BOOT = 1000
DAY = 86400.0
LO, HI = DAY, 3 * DAY  # the judged window: days 1 and 2

# A12.4: the open-loop settings on the frame set (8fcd672). R6-B ran the first eight; R4-B's last four ran only on the
# clock set (120f4ca) and are run here.
SETTINGS = {
    "ref": {}, "ap0.21": {"SOLAR_APERTURE_SCALE": 0.21}, "ap0.25": {"SOLAR_APERTURE_SCALE": 0.25},
    "ap0.29": {"SOLAR_APERTURE_SCALE": 0.29}, "ap0.5": {"SOLAR_APERTURE_SCALE": 0.5},
    "ap2": {"SOLAR_APERTURE_SCALE": 2.0},
    "ds0.6": {"DIRECT_SHARE": 0.6}, "ds0.95": {"DIRECT_SHARE": 0.95},
    "out-2": {"OUTDOOR_T_OFFSET_C": -2.0}, "out+2": {"OUTDOOR_T_OFFSET_C": 2.0},
    "hc2": {"HUMIDITY_CLASS": 2}, "hc3": {"HUMIDITY_CLASS": 3},
}
R6B_CACHED = ("ref", "ap0.21", "ap0.25", "ap0.29", "ap0.5", "ap2", "ds0.6", "ds0.95")
BRACKETS = {  # name -> settings ("nominal": the published sweep)
    "aperture (R4-B)": ("ap0.25", "ap0.5", "nominal", "ap2"),
    "outdoor air (R4-B)": ("out-2", "nominal", "out+2"),
    "humidity class (R4-B)": ("nominal", "hc2", "hc3"),
    "k_hat (R6-B)": ("ap0.21", "ap0.25", "ap0.29"),
    "direct share (R6-B)": ("ds0.6", "nominal", "ds0.95"),
}
APERTURES = (("x0.21", "ap0.21"), ("x0.25", "ap0.25"), ("x0.29", "ap0.29"), ("x0.5", "ap0.5"), ("x1", "ref"),
             ("x2", "ap2"))

# A12.2: the engine's six named refusal classes
REFUSAL_CLASSES = (
    ("it has no trigger", lambda r: r == "it has no trigger"),
    ("event triggers", lambda r: r.startswith("trigger event is not supported")),
    ("MQTT", lambda r: r.startswith("trigger mqtt is not supported")),
    ("webhooks", lambda r: r.startswith("trigger webhook is not supported")),
    ("NFC (tag)", lambda r: r.startswith("trigger tag is not supported")),
    ("blueprint definitions", lambda r: r == "'blueprint' is not supported"),
)

# A12.7: the near-identical sites (the towns' centres; elevation from the towns' records, rounded)
NEW_SITES = {
    "Anoka, MN, USA": {"lat": 45.1977, "lon": -93.3872, "elevation_m": 262, "tz": "America/Chicago"},
    "Södertälje, Sweden": {"lat": 59.1955, "lon": 17.6253, "elevation_m": 20, "tz": "Europe/Stockholm"},
    "Mesa, AZ, USA": {"lat": 33.4152, "lon": -111.8315, "elevation_m": 378, "tz": "America/Phoenix"},
    "St Paul, MN, USA": {"lat": 44.9537, "lon": -93.0900, "elevation_m": 214, "tz": "America/Chicago"},
    "Uppsala, Sweden": {"lat": 59.8586, "lon": 17.6389, "elevation_m": 15, "tz": "Europe/Stockholm"},
}
BASES = ("Minneapolis, MN, USA", "Stockholm, Sweden", "Phoenix, AZ, USA")
PAIRS = (  # (base, town, primary?)
    ("Minneapolis, MN, USA", "Anoka, MN, USA", True), ("Stockholm, Sweden", "Södertälje, Sweden", True),
    ("Phoenix, AZ, USA", "Mesa, AZ, USA", True), ("Minneapolis, MN, USA", "St Paul, MN, USA", False),
    ("Stockholm, Sweden", "Uppsala, Sweden", False),
)
DATES = ("2026-01-14", "2026-04-15", "2026-07-15", "2026-10-14")
DUP_NORTH = 360.0  # the second duplicate's Env: the same place (north 360 = 0) under a distinct key for the judge
USER_AGENT = "Mozilla/5.0 (compatible; research-script)"


def r4(x, k=4):
    return None if x is None else round(float(x), k)


def load_sweep(lab: Path, name: str) -> dict:
    return json.loads((lab / f"sweep-rung4-{name}.json").read_text())


def short(e: Env) -> str:
    return f"{e.city.split(',')[0]} {e.date} n{int(e.north_deg) % 360}"


def orient_pairs(envs) -> list[tuple[Env, Env]]:
    return [(a, b) for a, b in itertools.combinations(envs, 2) if _axis(a, b) == "north_deg"]


def frame_witnessed(outs: dict, ft: dict, fr, pairs) -> bool:
    return any(oracle.frame_restricted_divergences(outs, ft, fr, [x, y]) for x, y in pairs if x in outs and y in outs)


def clock_witnessed(outs: dict, pairs) -> bool:
    return any(restricted_divergences(outs, [x, y]) for x, y in pairs if x in outs and y in outs)


def class_witnessed(outs: dict, pairs) -> bool:
    return any(_differ(outs[x], outs[y]) for x, y in pairs if x in outs and y in outs)


def read_kind(a: dict) -> str:
    k = set(a["kinds"])
    return "temperature" if "temperature" in k else "humidity" if "humidity" in k else "neither"


def dump(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(pickle.dumps(obj))
    tmp.replace(path)


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")


# ---- A12.1: the three populations -----------------------------------------------------------------------------------

def witness_sets(s: dict, frame: bool = True) -> dict[str, set]:
    envs = r4a.envs_of(s)
    pairs = orient_pairs(envs)
    out = {"frame_flags": set(), "frame_witnessed": set(), "clock_flags": set(), "clock_witnessed": set(),
           "class_witnessed": set()}
    for i, a in r4a.ran(s).items():
        outs = r4a.outs_of(a, envs)
        if a[CLOCK_KEY]:
            out["clock_flags"].add(i)
            if clock_witnessed(outs, pairs):
                out["clock_witnessed"].add(i)
            if class_witnessed(outs, pairs):
                out["class_witnessed"].add(i)
        if frame and a.get(FRAME_KEY):
            out["frame_flags"].add(i)
            if frame_witnessed(outs, r4a.first_t_of(a, envs), oracle.frames_of(a["bound"]), pairs):
                out["frame_witnessed"].add(i)
    return out


def part_witness(a, lab: Path, scratch: Path) -> dict:
    s6 = load_sweep(lab, S6)
    w6 = witness_sets(s6)
    run6 = r4a.ran(s6)
    del s6
    sr = load_sweep(lab, SR)
    wr = witness_sets(sr, frame=False)
    del sr
    f, c, o = w6["frame_witnessed"], w6["clock_witnessed"], w6["class_witnessed"]
    res = {
        "S_R_120f4ca": {"clock_flags": len(wr["clock_flags"]), "clock_relation_orientation_witnessed":
                        len(wr["clock_witnessed"]), "outcome_class_orientation_witnessed": len(wr["class_witnessed"]),
                        "the_two_clock_sets_equal": wr["clock_witnessed"] == wr["class_witnessed"]},
        "S_6_8fcd672": {
            "frame_flags": len(w6["frame_flags"]), "frame_relation_orientation_witnessed": len(f),
            "clock_flags": len(w6["clock_flags"]), "clock_relation_orientation_witnessed": len(c),
            "outcome_class_orientation_witnessed": len(o), "the_two_clock_sets_equal": c == o,
            "frame_set_in_clock_relation_set": len(f & c), "frame_set_in_outcome_class_set": len(f & o),
            "frame_set_not_clock_flags": len(f - w6["clock_flags"]),
            "frame_set_clock_flagged_without_clock_orientation_witness": len((f & w6["clock_flags"]) - c),
            "outcome_class_set_not_frame_witnessed": len(o - f),
            "outcome_class_set_not_frame_flagged": len(o - w6["frame_flags"]),
            "frame_set_by_read_kind": dict(Counter(read_kind(run6[i]) for i in f)),
            "outcome_class_set_by_read_kind": dict(Counter(read_kind(run6[i]) for i in o)),
            "frame_set_reads_illuminance": sum(1 for i in f if "illuminance" in run6[i]["kinds"]),
        },
    }
    dump(scratch / "witness-ids.pkl", {k: sorted(v) for k, v in w6.items()})
    write_json(scratch / "witness.json", res)
    return res


# ---- A12.1, A12.4: open loop on the frame set -----------------------------------------------------------------------

def replayed_set(d: dict) -> list[str]:
    ids, *_ = r4b.affected(d)
    frame = {i for i, x in d["run"].items() if x.get(FRAME_KEY)}
    return sorted(set(ids) | frame)


def _inodes(cache: Path) -> dict[str, int]:
    return {p.name: p.stat().st_ino for p in cache.glob("trace-*.jsonl.gz")}


def prepare_cache(name: str, d: dict, scratch: Path, r6b_scratch: Path) -> Path:
    cache = scratch / f"cache-{S6}-{name}"
    if cache.is_dir():
        return cache
    if name in R6B_CACHED:
        shutil.copytree(r6b_scratch / f"cache-{S6}-{name}", cache, copy_function=os.link)  # hard links: read only
    else:
        shutil.copytree(Path(d["cache"]) / "weather", cache / "weather")
    return cache


def part_openloop(a, lab: Path, scratch: Path) -> None:
    d = r4b.load(lab, S6)
    ids = replayed_set(d)
    bfile = scratch / f"bound-replayed-{S6}.json"
    bfile.write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    for name in a.settings.split(","):
        out = scratch / f"openloop-{S6}-{name}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        cache = prepare_cache(name, d, scratch, Path(a.r6b_scratch).expanduser())
        before = _inodes(cache)
        jobs = [{"setting": name, "knobs": SETTINGS[name], "env": e, "home": d["home"], "cache": str(cache),
                 "bound_file": str(bfile), "catalog": a.catalog, "reporting_rule": "device"}
                for e in d["sweep"]["envs"]]
        t0 = time.monotonic()
        done = r4b._pool(r6b._trace_job, jobs, a.workers, name)
        after = _inodes(cache)
        rerecorded = sum(1 for k, v in before.items() if after.get(k) != v)
        dump(out, {"done": done, "knobs": SETTINGS[name], "elapsed_s": time.monotonic() - t0, "ids": len(ids),
                   "traces_before": len(before), "traces_rerecorded": rerecorded, "traces_after": len(after)})
        print(f"{name}: {len(jobs)} environments in {time.monotonic() - t0:.0f} s; traces before {len(before)}, "
              f"re-recorded {rerecorded}", flush=True)


def setting_findings(d: dict, ids: list[str], pkl: dict, envs, pairs) -> dict:
    """Per replayed automation at one setting: clock and frame divergences, frame witness, outcomes."""
    by_key = {e.key: e for e in envs}
    o, f = defaultdict(dict), defaultdict(dict)
    for _s, key, outcomes, _ref, _secs, _fail, first_t in pkl["done"]:
        for aid, x in outcomes.items():
            o[aid][key] = x
            f[aid][key] = first_t.get(aid)
    res = {}
    for i in ids:
        bound = d["run"][i]["bound"]
        clock, frame = r6b.relations(bound, o.get(i, {}), f.get(i, {}), by_key)
        outs = {by_key[k]: Outcome(**v) for k, v in o.get(i, {}).items()}
        ft = {by_key[k]: t for k, t in f.get(i, {}).items()}
        wit = bool(frame) and frame_witnessed(outs, ft, oracle.frames_of(bound), pairs)
        res[i] = {"clock": clock, "frame": frame, "witness": wit, "outcomes": dict(o.get(i, {}))}
    return res


def families(findings: dict, kinds: dict) -> dict[str, set]:
    return {"orientation_witnesses": {i for i, x in findings.items() if x["witness"]},
            "temperature_flags": {i for i, x in findings.items() if x["frame"] and "temperature" in kinds[i]},
            "humidity_flags": {i for i, x in findings.items() if x["frame"] and "humidity" in kinds[i]}}


def published_findings(s: dict, ids=None) -> dict:
    envs = r4a.envs_of(s)
    pairs = orient_pairs(envs)
    out = {}
    for i, a in r4a.ran(s).items():
        if ids is not None and i not in ids:
            continue
        wit = bool(a[FRAME_KEY]) and frame_witnessed(r4a.outs_of(a, envs), r4a.first_t_of(a, envs),
                                                      oracle.frames_of(a["bound"]), pairs)
        out[i] = {"clock": a[CLOCK_KEY], "frame": a[FRAME_KEY], "witness": wit, "outcomes": a["outcomes"]}
    return out


def core_and_possible(per_setting: dict[str, dict[str, set]], published: dict[str, set], names) -> dict:
    out = {}
    for fam, pub in published.items():
        sets = [per_setting[n][fam] for n in names]
        core = set(pub).intersection(*sets)
        possible = set().union(*sets)
        out[fam] = {"published": len(pub), "robust_core": len(core), "possible_set": len(possible),
                    "possible_outside_published": len(possible - pub)}
    return out


def report_openloop(lab: Path, scratch: Path) -> dict:
    d = r4b.load(lab, S6)
    ids = replayed_set(d)
    envs = r4a.envs_of(d["sweep"])
    pairs = orient_pairs(envs)
    kinds = {i: set(d["run"][i]["kinds"]) for i in ids}
    pub = published_findings(d["sweep"], set(ids))
    pub_fam = families(pub, kinds)
    names = [n for n in SETTINGS if (scratch / f"openloop-{S6}-{n}.pkl").is_file()]
    findings, meta = {}, {}
    for n in names:
        p = pickle.loads((scratch / f"openloop-{S6}-{n}.pkl").read_bytes())
        findings[n] = setting_findings(d, ids, p, envs, pairs)
        meta[n] = {k: p[k] for k in ("traces_before", "traces_rerecorded", "traces_after", "ids")} | {
            "failed_environments": sum(1 for x in p["done"] if x[5]), "elapsed_s": round(p["elapsed_s"], 1)}
    per = {n: families(findings[n], kinds) for n in names}
    per["nominal"] = pub_fam
    out = {"replayed_set": len(ids), "published": {k: len(v) for k, v in pub_fam.items()}, "runs": meta}
    if "ref" in findings:
        ref = findings["ref"]
        out["check_ref_x1"] = {
            "frame_flag_mismatches": sum(1 for i in ids if bool(ref[i]["frame"]) != bool(pub[i]["frame"])),
            "frame_kind_mismatches": sum(1 for i in ids if ref[i]["frame"] != pub[i]["frame"]),
            "clock_kind_mismatches": sum(1 for i in ids if ref[i]["clock"] != pub[i]["clock"]),
            "witness_mismatches": sum(1 for i in ids if ref[i]["witness"] != pub[i]["witness"]),
            "outcome_mismatches": sum(1 for i in ids for k, v in pub[i]["outcomes"].items()
                                      if ref[i]["outcomes"].get(k) != v)}
        # the replays run the current (engine-corrected, 5b54586) code: the defects' reach, by a text match (A11.2)
        moved = [json.dumps(d["run"][i]["bound"]) for i in ids
                 if any(ref[i]["outcomes"].get(k) != v for k, v in pub[i]["outcomes"].items())]
        out["check_ref_x1"]["automations_with_an_outcome_mismatch"] = len(moved)
        out["check_ref_x1"]["of_them_reading_sun_sun"] = sum(1 for t in moved if "sun.sun" in t)
        out["check_ref_x1"]["of_them_using_kelvin_or_color_temp"] = sum(1 for t in moved
                                                                         if "kelvin" in t or "color_temp" in t)
    run = d["run"]
    pw = pub_fam["orientation_witnesses"]
    rows = {}
    for label, n in APERTURES:
        if n not in per:
            continue
        w = per[n]["orientation_witnesses"]
        rows[label] = {
            "orientation_witnesses_kept": len(pw & w), "of": len(pw),
            "kept_by_read_kind": {k: [sum(1 for i in pw & w if read_kind(run[i]) == k),
                                      sum(1 for i in pw if read_kind(run[i]) == k)]
                                  for k in ("temperature", "humidity", "neither")},
            "new_orientation_witnesses": len(w - pw),
            "temperature_flags_kept": [len(pub_fam["temperature_flags"] & per[n]["temperature_flags"]),
                                       len(pub_fam["temperature_flags"])],
            "humidity_flags_kept": [len(pub_fam["humidity_flags"] & per[n]["humidity_flags"]),
                                    len(pub_fam["humidity_flags"])]}
    out["aperture_propagation_on_the_frame_set"] = rows
    out["per_setting"] = {n: {k: len(v) for k, v in per[n].items()} for n in per}
    out["brackets"] = {b: core_and_possible(per, pub_fam, ns) for b, ns in BRACKETS.items()
                       if all(n in per for n in ns)}
    # the engine bracket: whole sweeps
    rd = load_sweep(lab, f"r6d-{RD}")
    all_kinds = {i: set(x["kinds"]) for i, x in rd["automations"].items()}
    fam_rd = families(published_findings(rd), all_kinds)
    del rd
    s6_all = families(published_findings(d["sweep"]), all_kinds)
    eng = {"nominal": s6_all, "corrected": fam_rd}
    out["brackets"]["engine (R6-D), whole sweeps"] = core_and_possible(eng, s6_all, ("nominal", "corrected"))
    # every setting together (the engine's corrected sweep restricted to the replayed set)
    every = [n for n in per if n != "ref"]
    per_all = dict(per)
    per_all["corrected"] = {k: v & set(ids) for k, v in fam_rd.items()}
    out["brackets"]["all settings together"] = core_and_possible(per_all, pub_fam, every + ["corrected"])
    out["brackets_note"] = ("robust core: of the published family, held at every setting of the bracket; possible "
                            "set: held at some setting, within the replayed set (the engine bracket: all run "
                            "automations)")
    return out


# ---- A12.2, A12.3, A12.5, A12.6, A12.9 ------------------------------------------------------------------------------

def _first_reason(a: dict) -> str:
    from vesper.edt.sweep import reason_class
    return reason_class(a["refused"][sorted(a["refused"])[0]])


def refusal_table(s: dict) -> dict:
    from vesper.edt.sweep import reason_class
    autos = s["automations"]
    bound = {i: a for i, a in autos.items() if a["bound"] is not None}
    every = {i: a for i, a in bound.items() if not a["outcomes"]}
    partial = {i for i, a in bound.items() if a["outcomes"] and a["refused"]}
    reasons = {i: _first_reason(a) for i, a in every.items() if a["refused"]}
    cls = Counter()
    other = Counter()
    for r in reasons.values():
        for name, test in REFUSAL_CLASSES:
            if test(r):
                cls[name] += 1
                break
        else:
            cls["other"] += 1
            other[r] += 1
    multi = sum(1 for a in every.values() if len({reason_class(x) for x in a["refused"].values()}) > 1)
    run = sum(1 for a in bound.values() if a["outcomes"])
    return {"bound": len(bound), "run": run, "refused_in_every_environment": len(every),
            "refused_in_some_environments_only": len(partial),
            "bound_equals_run_plus_refused_everywhere": len(bound) == run + len(every),
            "refused_with_no_recorded_reason": len(every) - len(reasons),
            "by_class": {**{n: cls[n] for n, _ in REFUSAL_CLASSES}, "other": cls["other"]},
            "named_six_total": sum(cls[n] for n, _ in REFUSAL_CLASSES),
            "other_largest_five": [[r, k] for r, k in other.most_common(5)],
            "other_distinct_reason_classes": len(other),
            "refused_everywhere_with_more_than_one_reason_class": multi}


def bookkeeping(s6: dict, sr: dict) -> dict:
    from vesper.edt.sweep import reason_class
    A6, AR = s6["automations"], sr["automations"]
    assert set(A6) == set(AR)

    def b(a):
        return a["bound"] is not None

    def cause(i):
        a6, ar = A6[i], AR[i]
        if b(ar) and not b(a6):
            return "refused by the binder at S_6: " + reason_class(a6["unbindable"] or "")
        if b(a6) and not b(ar):
            return "bound at S_6 only"
        if a6["bound"] != ar["bound"]:
            return "bound differently"
        if bool(a6["outcomes"]) != bool(ar["outcomes"]):
            return "run at one sweep only"
        return "same binding, run at both, outcomes differ (reporting rule or engine)"
    c6 = {i for i, a in A6.items() if a[CLOCK_KEY]}
    cr = {i for i, a in AR.items() if a[CLOCK_KEY]}

    def dead_coupled(A):
        return {i for i, a in A.items() if a["outcomes"] and a["kinds"]
                and not any(o["fired"] for o in a["outcomes"].values())}
    d6, dr = dead_coupled(A6), dead_coupled(AR)
    b6 = {i for i, a in A6.items() if b(a)}
    br = {i for i, a in AR.items() if b(a)}
    both = b6 & br
    rebind = {i for i in both if A6[i]["bound"] != AR[i]["bound"]}
    rebind_kinds = Counter()
    for i in rebind:
        rebind_kinds[("run at both" if A6[i]["outcomes"] and AR[i]["outcomes"] else "run at one or neither")] += 1
    return {
        "clock_flags_790_vs_797": {"S_6": len(c6), "S_R": len(cr), "kept": len(c6 & cr), "lost": len(cr - c6),
                                   "new": len(c6 - cr), "lost_by_cause": dict(Counter(cause(i) for i in cr - c6)),
                                   "new_by_cause": dict(Counter(cause(i) for i in c6 - cr)),
                                   "kept_with_other_kinds": sum(1 for i in c6 & cr
                                                                if A6[i][CLOCK_KEY] != AR[i][CLOCK_KEY])},
        "dead_environment_coupled_390_vs_391": {"S_6": len(d6), "S_R": len(dr), "both": len(d6 & dr),
                                                "S_R_only": len(dr - d6), "S_6_only": len(d6 - dr),
                                                "S_R_only_by_cause": dict(Counter(cause(i) for i in dr - d6)),
                                                "S_6_only_by_cause": dict(Counter(cause(i) for i in d6 - dr))},
        "bound": {"S_R": len(br), "S_6": len(b6), "S_R_evaluations_over_48": len(br) * 48,
                  "S_R_only": len(br - b6), "S_6_only": len(b6 - br),
                  "S_R_only_by_cause": dict(Counter(cause(i) for i in br - b6)),
                  "bound_at_both_with_a_different_binding": len(rebind),
                  "rebind_by_run": dict(rebind_kinds),
                  "rebind_clock_flagged_at_both": sum(1 for i in rebind if i in c6 and i in cr),
                  "run_S_R": sum(1 for a in AR.values() if a["outcomes"]),
                  "run_S_6": sum(1 for a in A6.values() if a["outcomes"])},
        "engine_refusals": {"S_6": refusal_table(s6), "S_R": refusal_table(sr)},
    }


def discrepant_pairs(path: Path) -> dict:
    from vesper.validation.ha_diff import discrepant, subsecond_only
    data = json.loads(path.read_text())
    recs = [p for env in data["envs"] for p in data["per_env"][env]["per"].values()]
    both = [p for p in recs if p["v_fired"] and p["h_fired"]]
    one = [p for p in recs if p["v_fired"] != p["h_fired"]]
    neither = [p for p in recs if not p["v_fired"] and not p["h_fired"]]
    big = [p for p in both if p["delta_s"] is not None and abs(p["delta_s"]) >= 1.0]
    small = [p for p in both if p not in big]
    pay = [p for p in small if discrepant(p)]

    def fields(p):
        f = []
        if not p["calls_equal"]:
            f.append("calls")
        if p["v_count"] != p["h_count"]:
            f.append("count")
        if p.get("brightness_equal") is False:
            f.append("brightness")
        if p.get("ha_unavailable"):
            f.append("unavailable")
        return "+".join(f) or "none"
    disc = [p for p in recs if discrepant(p)]
    return {"pairs": len(recs), "both_fire": len(both), "neither": len(neither), "one_engine_only": len(one),
            "both_within_1s": len(small), "both_within_60s": sum(1 for p in both if abs(p["delta_s"] or 0) < 60),
            "discrepant": len(disc),
            "decomposition": {"one_engine_only": sum(1 for p in one if discrepant(p)),
                              "both_fire_abs_delta_ge_1s": len(big),
                              "both_fire_abs_delta_lt_1s_payload_differs": len(pay)},
            "payload_differs_by_field": dict(Counter(fields(p) for p in pay)),
            "both_fire_abs_delta_ge_1s_also_payload": sum(1 for p in big if fields(p) != "none"),
            "subsecond_only": sum(1 for p in recs if subsecond_only(p)),
            "neither_discrepant": sum(1 for p in neither if discrepant(p))}


def read_labels(path: Path, ncol: int) -> dict[str, list[str]]:
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        out[parts[0].strip()] = [x.strip() for x in parts[1:ncol]]
    return out


def binder_discount(s6: dict, labels_path: Path) -> dict:
    run = r4a.ran(s6)
    frame = {i: a for i, a in run.items() if a[FRAME_KEY]}
    sensed = {i for i, a in frame.items() if oracle.SENSED in oracle.frames_of(a["bound"]).frames}
    others = len(frame) - len(sensed)
    sun_readers = {i for i, a in frame.items() if "sun" in a["kinds"]}
    labels = read_labels(labels_path, 3)
    lab = {i: labels.get(i[:12]) for i in sensed}
    covered = {i for i, v in lab.items() if v}
    repo = {i: run[i]["repo"] for i in sensed}

    def estimate(ids, misbind):
        cov = [i for i in ids if i in covered]
        unc = [i for i in ids if i not in covered]
        m = sum(1 for i in cov if misbind(lab[i]))
        share = m / len(cov) if cov else 0.0
        return others + (len(cov) - m) + len(unc) * (1 - share), m, len(cov), len(unc)

    rules = {"primary (uncertain as not a mis-bind)": lambda v: v[0] == "misbind",
             "sensitivity (uncertain as a mis-bind)": lambda v: v[0] in ("misbind", "uncertain"),
             "wrong medium only (C1-C4, C6)": lambda v: v[0] == "misbind" and not v[1].startswith("C5")}
    repos = sorted(set(repo.values()))
    by_repo = defaultdict(list)
    for i in sensed:
        by_repo[repo[i]].append(i)
    rng = np.random.default_rng(SEED)
    draws = [rng.integers(0, len(repos), len(repos)) for _ in range(BOOT)]
    out = {"frame_flags": len(frame), "sensed_frame": len(sensed), "not_discounted": others,
           "sun_readers": len(sun_readers), "sun_readers_among_sensed_frame": len(sun_readers & sensed),
           "covered_by_a_label": len(covered), "uncovered": len(sensed) - len(covered),
           "covered_by_label": dict(Counter(lab[i][0] for i in covered)),
           "misbind_class_among_covered": dict(Counter(lab[i][1] for i in covered if lab[i][0] == "misbind")),
           "repositories": len(repos), "estimates": {}}
    for name, rule in rules.items():
        point, m, nc, nu = estimate(sensed, rule)
        boots = []
        for dr in draws:
            ids = [i for k in dr for i in by_repo[repos[k]]]
            # the resample's own sensed count: D scaled to 148 so the interval is on the same population size
            val, *_ = estimate(ids, rule)
            boots.append(others + (val - others) * len(sensed) / len(ids))
        out["estimates"][name] = {"D": r4(point, 2), "misbind": m, "covered": nc, "uncovered": nu,
                                  "repo_bootstrap_95": [r4(np.percentile(boots, 2.5), 1),
                                                        r4(np.percentile(boots, 97.5), 1)]}
    share, lo, hi = 51 / 179, 0.188, 0.392
    out["review_form"] = {"D": r4(others + len(sensed) * (1 - share), 2),
                          "at_R4C_bootstrap_ends": [r4(others + len(sensed) * (1 - hi), 1),
                                                    r4(others + len(sensed) * (1 - lo), 1)]}
    return out


def _walk_steps(node, found: set) -> None:
    if isinstance(node, list):
        for x in node:
            _walk_steps(x, found)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("delay", "wait_for_trigger"):
                found.add(k)
            if k in ("data", "data_template", "variables", "target"):
                continue
            _walk_steps(v, found)


def semantics_of(raw: dict) -> set[str]:
    out = set()
    if not isinstance(raw, dict):
        return out
    mode = raw.get("mode")
    if mode not in (None, "single"):
        out.add("mode_not_single")
        out.add(f"mode={mode}")
    for k in ("max", "max_exceeded", "initial_state"):
        if k in raw:
            out.add(k)
    if "initial_state" in raw and str(raw.get("initial_state")).lower() in ("false", "off"):
        out.add("initial_state_off")
    _walk_steps(raw.get("actions", raw.get("action")), out)
    return out


def semantics(s6: dict) -> dict:
    run = r4a.ran(s6)
    pops = {"frame_flags": [i for i, a in run.items() if a[FRAME_KEY]],
            "clock_flags": [i for i, a in run.items() if a[CLOCK_KEY]], "run": list(run)}
    keys = ("mode_not_single", "max", "max_exceeded", "initial_state", "initial_state_off", "delay",
            "wait_for_trigger")
    out = {}
    for name, ids in pops.items():
        sem = {i: semantics_of(run[i]["raw"]) for i in ids}
        row = {"n": len(ids), **{k: sum(1 for i in ids if k in sem[i]) for k in keys}}
        row["any_of_these"] = sum(1 for i in ids if sem[i] & set(keys))
        row["modes"] = dict(Counter(k for i in ids for k in sem[i] if k.startswith("mode=")))
        out[name] = row
    return out


def nonenv_dump(s6: dict, path: Path) -> dict:
    """The not-environment-coupled flags, written locally for hand reading (A12.9); their frame partition."""
    envs = r4a.envs_of(s6)
    run = r4a.ran(s6)
    clock = {i for i, a in run.items() if a[CLOCK_KEY] and not a["kinds"]}
    frame = {i for i, a in run.items() if a[FRAME_KEY] and not a["kinds"]}
    lines = []
    for i in sorted(clock | frame):
        a = run[i]
        fr = oracle.frames_of(a["bound"])
        lines.append(f"==== {i[:12]}  clock={a[CLOCK_KEY]} frame={a[FRAME_KEY]} frames={sorted(fr.frames)} "
                     f"approx={a['approximations']}")
        lines.append("BOUND " + json.dumps(a["bound"], default=str)[:6000])
        by = defaultdict(list)
        for e in envs:
            o = a["outcomes"].get(e.key)
            if o is not None:
                tod = o["first_tod_h"]
                by[(e.city.split(",")[0], e.date)].append(f"n{int(e.north_deg)}:{'F' if o['fired'] else '-'}"
                                                          f"{'' if tod is None else f'{tod:.2f}'}")
        for k, v in sorted(by.items()):
            lines.append(f"  {k[0]:12s} {k[1]} " + " ".join(v))
    path.write_text("\n".join(lines) + "\n")

    def label(ids):
        return dict(Counter("+".join(sorted(oracle.frames_of(run[i]["bound"]).frames)) or "none" for i in ids))
    return {"clock_flags": len(clock), "frame_flags": len(frame), "both": len(clock & frame),
            "frame_only": len(frame - clock), "clock_frames": label(clock), "frame_frames": label(frame)}


def nonenv_classes(s6: dict, labels_path: Path) -> dict | None:
    if not labels_path.is_file():
        return None
    labels = read_labels(labels_path, 3)
    run = r4a.ran(s6)
    out = {}
    for name, key in (("clock", CLOCK_KEY), ("frame", FRAME_KEY)):
        ids = [i for i, a in run.items() if a[key] and not a["kinds"]]
        missing = [i for i in ids if i[:12] not in labels]
        out[name] = {"n": len(ids), "unlabelled": len(missing),
                     "by_class": dict(Counter(labels[i[:12]][0] for i in ids if i[:12] in labels)),
                     "by_detector_rule": dict(Counter(labels[i[:12]][1] for i in ids if i[:12] in labels
                                                      and labels[i[:12]][0].startswith("L-"))),
                     "class_by_frames": dict(Counter(
                         f"{labels[i[:12]][0]} | " + ("+".join(sorted(oracle.frames_of(run[i]["bound"]).frames))
                                                      or "none") for i in ids if i[:12] in labels))}
    return out


def part_recon(a, lab: Path, scratch: Path) -> dict:
    s6 = load_sweep(lab, S6)
    sr = load_sweep(lab, SR)
    res = {"bookkeeping": bookkeeping(s6, sr)}
    del sr
    res["discrepant_pair"] = discrepant_pairs(lab / "r6-C" / "pairs-134b209.json")
    res["binder_discount"] = binder_discount(s6, lab / "audit-r4c" / "labels-flagged.tsv")
    res["non_default_semantics"] = semantics(s6)
    res["not_environment_coupled"] = nonenv_dump(s6, scratch / "nonenv-dump.txt")
    res["not_environment_coupled_classes"] = nonenv_classes(s6, scratch.parent / "labels-nonenv.tsv")
    write_json(scratch / "recon.json", res)
    return res


# ---- A12.8 ----------------------------------------------------------------------------------------------------------

MATCH = {
    "trigger entities never change (no residents; open loop)": {"P", "E"},
    "a condition never holds when a trigger fires: condition on an entity that never changes (motion, door, actuator)":
        {"P", "E"},
    "a condition never holds when a trigger fires: time condition": {"S", "G2"},
    "a condition never holds when a trigger fires: template or other": {"S", "G2"},
    "a condition never holds when a trigger fires: sensed-quantity or sun-state condition": {"B", "G2"},
    "threshold out of the observed range in every environment": {"B", "G1"},
    "never armed": {"B", "G1", "G3"},
    "the actions end before any call": {"X"},
}
NOT_COMPARABLE = ("a condition never holds when a trigger fires: several conditions", "not derived")


def part_dead(a, lab: Path, scratch: Path) -> None:
    s6 = load_sweep(lab, S6)
    run = r4a.ran(s6)
    coupled = {i for i, x in run.items() if x["kinds"] and not any(o["fired"] for o in x["outcomes"].values())}
    only = None if a.dead_all else coupled
    dead, reasons, probes = r4a.dead_reasons(s6, lab, S6, a.workers, a.catalog, only=only)
    dump(scratch / "dead-reasons.pkl", {"reasons": reasons, "probes": probes, "only_coupled": only is not None})


def report_dead(lab: Path, scratch: Path) -> dict:
    p = pickle.loads((scratch / "dead-reasons.pkl").read_bytes())
    s6 = load_sweep(lab, S6)
    run = r4a.ran(s6)
    coupled = {i for i, x in run.items() if x["kinds"] and not any(o["fired"] for o in x["outcomes"].values())}
    reasons = {i: r for i, r in p["reasons"].items() if i in coupled}
    published = json.loads((ROOT / "results" / "edt" / f"analysis-{S6}.json").read_text())
    pub = published["dead_rules"]["by_reason_environment_coupled"]
    mine = dict(Counter(reasons.values()))
    labels = read_labels(lab / "audit-r4c" / "labels-dead.tsv", 2)
    by12 = {i[:12]: i for i in coupled}
    joined = {by12[k]: v[0] for k, v in labels.items() if k in by12}
    rows = {}
    for reason in sorted(set(reasons[i] for i in joined)):
        ids = [i for i in joined if reasons[i] == reason]
        if reason in MATCH:
            m = sum(1 for i in ids if joined[i] in MATCH[reason])
            rows[reason] = {"audited": len(ids), "matches": m, "precision": r4(m / len(ids), 3),
                            "wilson_95": wilson(m, len(ids)), "auditor_classes": dict(Counter(joined[i] for i in ids))}
        else:
            rows[reason] = {"audited": len(ids), "not_comparable": True,
                            "auditor_classes": dict(Counter(joined[i] for i in ids))}
    comparable = [i for i in joined if reasons[i] in MATCH]
    m = sum(1 for i in comparable if joined[i] in MATCH[reasons[i]])
    return {"environment_coupled_dead": len(coupled), "derived_for": len(reasons),
            "reproduces_published_column": mine == pub, "published_column": pub, "rederived_column": mine,
            "probes": p["probes"], "restricted_to_coupled": p["only_coupled"],
            "audit_labels": len(labels), "audit_in_S_6": len(joined),
            "auditor_classes": dict(Counter(joined.values())),
            "by_reason": rows,
            "comparable": len(comparable), "matches": m, "precision_overall": r4(m / len(comparable), 3)
            if comparable else None, "precision_overall_wilson_95": wilson(m, len(comparable)),
            "not_derived": sum(1 for i in joined if reasons[i] == "not derived"),
            "not_comparable": sum(1 for i in joined if reasons[i] in NOT_COMPARABLE)}


# ---- A12.10 ---------------------------------------------------------------------------------------------------------

def part_crossing(a, lab: Path, scratch: Path) -> dict:
    import r6a_analyses as r6a
    s6 = load_sweep(lab, S6)
    envs = r4a.envs_of(s6)
    run = r4a.ran(s6)
    refs = {i: oracle.sensed_crossing_refs(x["bound"]) for i, x in run.items()}
    applies = {i for i, r in refs.items() if r}
    watched, attrs = set(), set()
    for i in applies:
        e, at = r6a._trigger_entities(run[i]["bound"])
        watched |= e
        attrs |= at
    work = lab / f"work-{S6}"
    series = r6a.env_series(envs, work / "cache", work / "edt" / "home.yaml", watched, attrs, a.workers, a.catalog)
    mism = 0
    variant, lat, theta, ts_gain = {}, set(), {}, set()
    for i, x in run.items():
        outs, ft, fr = r4a.outs_of(x, envs), r4a.first_t_of(x, envs), oracle.frames_of(x["bound"])
        static = set(x["static"])
        off = sorted(oracle.frame_divergences(outs, ft, fr, env_latency=False) - static)
        mism += off != sorted(x[FRAME_KEY])
        if i not in applies:
            variant[i] = off
            continue
        cross = {e: oracle.crossing_instant(refs[i], series[e], LO, HI) for e in envs}
        v = sorted(oracle.frame_divergences(outs, ft, fr, sensed_crossing=cross, env_latency=True) - static)
        variant[i] = v
        theta[i] = oracle._latencies_min(outs, ft, cross)
        if oracle.ENV_LATENCY in v:
            lat.add(i)
        if "time_spread" in v and "time_spread" not in x[FRAME_KEY]:
            ts_gain.add(i)
    pub = {i for i, x in run.items() if x[FRAME_KEY]}
    flagged = {i for i, v in variant.items() if v}

    def category(i):
        th = theta[i]
        return "precede" if th and min(th) < 0 else "latency"
    res = {"option_off_reproduces_published": mism == 0, "mismatches": mism, "run": len(run),
           "computable_sensed_frame": len(applies),
           "variant_flags": len(flagged), "published_flags": len(pub), "added": len(flagged - pub),
           "lost": len(pub - flagged), "variant_by_kind": dict(Counter(k for v in variant.values() for k in v)),
           "env_latency": len(lat), "env_latency_also_time_spread": sum(1 for i in lat if "time_spread" in variant[i]),
           "env_latency_published_flags": len(lat & pub), "env_latency_added_flags": len(lat - pub),
           "env_latency_by_frames": dict(Counter("+".join(sorted(oracle.frames_of(run[i]["bound"]).frames))
                                                 for i in lat)),
           "env_latency_by_quantity": dict(Counter(k for i in lat for k in run[i]["kinds"])),
           "env_latency_with_conditions": sum(1 for i in lat if oracle._as_list(
               run[i]["bound"].get("condition", run[i]["bound"].get("conditions")))),
           "time_spread_gained": len(ts_gain), "time_spread_gained_by_category": dict(Counter(category(i)
                                                                                          for i in ts_gain)),
           "time_spread_gained_and_env_latency": len(ts_gain & lat),
           "env_latency_spread_min": sorted(r4(max(theta[i]) - min(theta[i]), 0) for i in lat)}
    write_json(scratch / "crossing.json", res)
    return res


# ---- A12.11 ---------------------------------------------------------------------------------------------------------

def coverage(s6: dict) -> dict:
    envs = r4a.envs_of(s6)
    pairs = list(itertools.combinations(range(len(envs)), 2))
    out = {}
    for name, key in (("clock", CLOCK_KEY), ("frame", FRAME_KEY)):
        fl = {i: x for i, x in r4a.ran(s6).items() if x[key]}
        W = np.zeros((len(fl), len(envs), len(envs)), dtype=bool)
        ids = sorted(fl)
        for r, i in enumerate(ids):
            x = fl[i]
            o, ft, fr = r4a.outs_of(x, envs), r4a.first_t_of(x, envs), oracle.frames_of(x["bound"])
            for p, q in pairs:
                a_, b_ = envs[p], envs[q]
                if a_ not in o or b_ not in o:
                    continue
                d = oracle.frame_restricted_divergences(o, ft, fr, [a_, b_]) if key == FRAME_KEY else \
                    restricted_divergences(o, [a_, b_])
                if d:
                    W[r, p, q] = W[r, q, p] = True
        out[name] = {"W": W, "train": np.array([half(fl[i]["repo"]) == "train" for i in ids])}
    return {"envs": envs, "cov": out}


def covered(W: np.ndarray, S) -> int:
    S = list(S)
    return int(W[:, S][:, :, S].any(axis=(1, 2)).sum())


def pair_covered(W: np.ndarray, pairs) -> int:
    m = np.zeros(W.shape[0], dtype=bool)
    for p, q in pairs:
        m |= W[:, p, q]
    return int(m.sum())


def greedy_pairs(W: np.ndarray, train: np.ndarray, envs, k: int = 3) -> list[tuple[int, int]]:
    pairs = list(itertools.combinations(range(len(envs)), 2))
    chosen, got = [], np.zeros(W.shape[0], dtype=bool)
    for _ in range(k):
        best = min(pairs, key=lambda p: (-int((W[:, p[0], p[1]] & ~got & train).sum()),
                                          (envs[p[0]].key, envs[p[1]].key)))
        chosen.append(best)
        got |= W[:, best[0], best[1]]
    return chosen


def milp_bound(W: np.ndarray, k: int, lp: bool, time_limit: float) -> dict:
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix
    nf, ne = W.shape[0], W.shape[1]
    pairs = [(p, q) for p, q in itertools.combinations(range(ne), 2) if W[:, p, q].any()]
    npr = len(pairs)
    nv = ne + npr + nf
    rows = 1 + 2 * npr + nf
    A = lil_matrix((rows, nv))
    lb, ub = np.full(rows, -np.inf), np.zeros(rows)
    A[0, :ne] = 1
    lb[0] = ub[0] = k
    r = 1
    for j, (p, q) in enumerate(pairs):
        A[r, ne + j], A[r, p] = 1, -1
        A[r + 1, ne + j], A[r + 1, q] = 1, -1
        r += 2
    pidx = {pq: j for j, pq in enumerate(pairs)}
    for f in range(nf):
        A[r, ne + npr + f] = 1
        for p, q in zip(*np.nonzero(np.triu(W[f], 1)), strict=True):
            A[r, ne + pidx[(int(p), int(q))]] = -1
        r += 1
    c = np.zeros(nv)
    c[ne + npr:] = -1
    integ = np.zeros(nv)
    if not lp:
        integ[:ne] = 1
    t0 = time.monotonic()
    res = milp(c, constraints=LinearConstraint(A.tocsr(), lb, ub), integrality=integ, bounds=Bounds(0, 1),
               options={"time_limit": time_limit, "disp": False})
    out = {"status": int(res.status), "message": str(res.message), "seconds": round(time.monotonic() - t0, 1),
           "pairs_with_a_witness": npr}
    if res.x is not None:
        out["objective"] = r4(-res.fun, 3)
        if not lp:
            out["chosen"] = [int(i) for i in np.nonzero(res.x[:ne] > 0.5)[0]]
    bound = getattr(res, "mip_dual_bound", None)
    if bound is not None and not lp:
        out["dual_bound"] = r4(-bound, 3)
    return out


def local_search(W: np.ndarray, start, ne: int) -> tuple[int, list[int]]:
    S = list(start)
    best = covered(W, S)
    while True:
        move = None
        for i in range(len(S)):
            for e in range(ne):
                if e in S:
                    continue
                T = S[:i] + [e] + S[i + 1:]
                v = covered(W, T)
                if v > best:
                    best, move = v, T
        if move is None:
            return best, sorted(S)
        S = move


def part_cover(a, lab: Path, scratch: Path) -> dict:
    s6 = load_sweep(lab, S6)
    c = coverage(s6)
    del s6
    envs = c["envs"]
    res = {}
    rng = np.random.default_rng(SEED)
    for name, m in c["cov"].items():
        W, train = m["W"], m["train"]
        g = greedy_pairs(W, train, envs)
        g_envs = sorted({e for p in g for e in p})
        row = {"greedy_pairs": [[short(envs[p]), short(envs[q])] for p, q in g]}
        for pop, mask in (("all", np.ones(len(train), dtype=bool)), ("train", train)):
            Wp = W[mask]
            ls = [local_search(Wp, g_envs, len(envs))]
            for _ in range(20):
                ls.append(local_search(Wp, sorted(rng.choice(len(envs), 6, replace=False).tolist()), len(envs)))
            best_ls = max(ls, key=lambda x: x[0])
            row[pop] = {"flags": int(mask.sum()),
                        "coverable_by_some_pair": int(Wp.any(axis=(1, 2)).sum()),
                        "greedy_3_pairs": pair_covered(Wp, g),
                        "greedy_6_envs_induced": covered(Wp, g_envs),
                        "local_search_best": best_ls[0], "local_search_envs": [short(envs[e]) for e in best_ls[1]],
                        "local_search_values": sorted(x[0] for x in ls),
                        "lp_relaxation": milp_bound(Wp, 6, True, a.time_limit),
                        "milp": milp_bound(Wp, 6, False, a.time_limit)}
            mi = row[pop]["milp"]
            if "chosen" in mi:
                mi["chosen_envs"] = [short(envs[e]) for e in mi.pop("chosen")]
            ub = mi.get("dual_bound", mi.get("objective"))
            row[pop]["gap_greedy_pairs_to_upper_bound"] = None if ub is None else r4(ub - row[pop]["greedy_3_pairs"],
                                                                                     3)
        res[name] = row
    write_json(scratch / "cover.json", res)
    return res


# ---- A12.7: the null-difference control ---------------------------------------------------------------------------

def register_sites() -> None:
    from vesper.env import site
    site.CITIES.update(NEW_SITES)


def _null_job(job: dict):
    register_sites()
    from vesper.edt import sweep as sw
    sw._init(job["home"], job["cache"], r4b._bound_from(job["bound_file"]), job["catalog"], sw.BATCH, "device")
    key, outcomes, refused, _approx, secs, failure, rich = sw._run_env(r2c._env(job["env"]))
    return job["run"], key, outcomes, refused, secs, failure, rich


def fetch_new_weather(weather_dir: Path, log_path: Path) -> list[str]:
    import httpx

    from vesper.edt.trace import _fetch_weather, env_site
    register_sites()
    urls = []

    def get(url, params=None, timeout=60):
        r = httpx.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
        urls.append(str(r.url))
        time.sleep(2.0)  # politely
        return r
    for city in NEW_SITES:
        for date in DATES:
            _fetch_weather(env_site(Env(city, date, 0.0, 4)), weather_dir, get)
    with log_path.open("a") as f:
        for u in urls:
            f.write(u + "\n")
    return urls


def part_null(a, lab: Path, scratch: Path) -> None:
    d = r4b.load(lab, S6)
    bfile = scratch / f"bound-all-{S6}.json"
    if not bfile.is_file():
        bfile.write_text(json.dumps({i: x["bound"] for i, x in d["autos"].items() if x["bound"] is not None}))
    new_weather = scratch.parent / "weather-new"
    urls = fetch_new_weather(new_weather, scratch.parent / "weather-urls.txt")
    print(f"weather: {len(urls)} requests", flush=True)
    era5 = lab / f"work-{SR}" / "cache" / "weather"
    for run in ("A", "B"):
        cache = scratch / f"null-cache-{run}"
        if not (cache / "weather").is_dir():
            shutil.copytree(era5, cache / "weather")
            if run == "A":
                shutil.copytree(new_weather, cache / "weather", dirs_exist_ok=True)
        cities = BASES + tuple(NEW_SITES) if run == "A" else BASES
        out = scratch / f"null-{run}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        jobs = [{"run": run, "env": {"city": c, "date": dt, "north_deg": 0.0, "rung": 4}, "home": d["home"],
                 "cache": str(cache), "bound_file": str(bfile), "catalog": a.catalog}
                for c in cities for dt in DATES]
        t0 = time.monotonic()
        done = r4b._pool(_null_job, jobs, a.workers, f"null {run}")
        dump(out, {"done": done, "elapsed_s": time.monotonic() - t0})
        print(f"null {run}: {len(jobs)} environments in {time.monotonic() - t0:.0f} s", flush=True)


def _pair_flags(bound: dict, oa: dict, ob: dict, fa, fb, ea: Env, eb: Env) -> tuple[set, set]:
    outs = {ea: Outcome(**oa), eb: Outcome(**ob)}
    ft = {ea: fa, eb: fb}
    return (restricted_divergences(outs, [ea, eb]),
            oracle.frame_restricted_divergences(outs, ft, oracle.frames_of(bound), [ea, eb]))


def judge_pair(autos: dict, A: dict, B: dict, ka: str, kb: str, ea: Env, eb: Env) -> dict:
    oa, ob = A["outcomes"].get(ka, {}), B["outcomes"].get(kb, {})
    ra, rb = A["rich"].get(ka, {}), B["rich"].get(kb, {})
    both = sorted(set(oa) & set(ob))
    out = {"both": len(both), "both_coupled": sum(1 for i in both if autos[i]["kinds"]),
           "clock": Counter(), "frame": Counter(), "clock_ids": [], "frame_ids": []}
    for i in both:
        c, f = _pair_flags(autos[i]["bound"], oa[i], ob[i], (ra.get(i) or {}).get("first_t"),
                           (rb.get(i) or {}).get("first_t"), ea, eb)
        for rel, kinds in (("clock", c), ("frame", f)):
            if kinds:
                out[f"{rel}_ids"].append(i)
                for k in kinds:
                    out[rel][k] += 1
    return out


def _collect(pkl: dict) -> dict:
    by = {}
    for _run, key, outcomes, refused, _secs, failure, rich in pkl["done"]:
        by[key] = (outcomes, refused, failure, rich)
    return by


def report_null(lab: Path, scratch: Path) -> dict:
    register_sites()
    s6 = load_sweep(lab, S6)
    autos = s6["automations"]
    del s6
    A = _collect(pickle.loads((scratch / "null-A.pkl").read_bytes()))
    B = _collect(pickle.loads((scratch / "null-B.pkl").read_bytes()))

    def view(by):
        return {"outcomes": {k: v[0] for k, v in by.items()}, "rich": {k: v[3] for k, v in by.items()}}
    va, vb = view(A), view(B)
    failures = {k: v[2] for k, v in {**A, **B}.items() if v[2]}
    res = {"failed_environments": len(failures), "environments_run": {"A": len(A), "B": len(B)}}
    # the duplicated environment
    dup = []
    tot = Counter()
    for city in BASES:
        for dt in DATES:
            e = Env(city, dt, 0.0, 4)
            e2 = Env(city, dt, DUP_NORTH, 4)
            j = judge_pair(autos, va, vb, e.key, e.key, e, e2)
            oa, ob = A[e.key][0], B[e.key][0]
            rich_a, rich_b = A[e.key][3], B[e.key][3]
            row = {"env": short(e), "with_both": j["both"],
                   "outcome_only_in_one": len(set(oa) ^ set(ob)),
                   "outcome_mismatches": sum(1 for i in set(oa) & set(ob) if oa[i] != ob[i]),
                   "rich_mismatches": sum(1 for i in set(rich_a) & set(rich_b) if rich_a[i] != rich_b[i]),
                   "refusal_mismatches": len(set(A[e.key][1]) ^ set(B[e.key][1])),
                   "clock_flags": len(j["clock_ids"]), "frame_flags": len(j["frame_ids"])}
            for k in ("with_both", "outcome_only_in_one", "outcome_mismatches", "rich_mismatches",
                      "refusal_mismatches", "clock_flags", "frame_flags"):
                tot[k] += row[k]
            dup.append(row)
    res["duplicated_environment"] = {"pairs": dup, "total": dict(tot),
                                     "expected_flag_rate": 0, "observed_clock_rate": r4(tot["clock_flags"] /
                                                                                       tot["with_both"], 6),
                                     "observed_frame_rate": r4(tot["frame_flags"] / tot["with_both"], 6)}
    # the near-identical pairs
    rows, pooled = [], {True: defaultdict(Counter), False: defaultdict(Counter)}
    kinds_by = {True: {"clock": Counter(), "frame": Counter()}, False: {"clock": Counter(), "frame": Counter()}}
    reads_by = {True: {"clock": Counter(), "frame": Counter()}, False: {"clock": Counter(), "frame": Counter()}}
    for base, town, primary in PAIRS:
        for dt in DATES:
            ea, eb = Env(base, dt, 0.0, 4), Env(town, dt, 0.0, 4)
            j = judge_pair(autos, va, va, ea.key, eb.key, ea, eb)
            coupled_c = sum(1 for i in j["clock_ids"] if autos[i]["kinds"])
            coupled_f = sum(1 for i in j["frame_ids"] if autos[i]["kinds"])
            row = {"pair": f"{base.split(',')[0]} / {town.split(',')[0]}", "date": dt, "primary": primary,
                   "with_both": j["both"], "with_both_coupled": j["both_coupled"],
                   "clock_flags": len(j["clock_ids"]), "frame_flags": len(j["frame_ids"]),
                   "clock_flags_coupled": coupled_c, "frame_flags_coupled": coupled_f,
                   "clock_rate": r4(len(j["clock_ids"]) / j["both"], 5),
                   "frame_rate": r4(len(j["frame_ids"]) / j["both"], 5),
                   **weather_gap(scratch, base, town, dt)}
            rows.append(row)
            p = pooled[primary]["n"]
            for k in ("with_both", "with_both_coupled", "clock_flags", "frame_flags", "clock_flags_coupled",
                      "frame_flags_coupled"):
                p[k] += row[k]
            for rel in ("clock", "frame"):
                kinds_by[primary][rel].update(j[rel])
                reads_by[primary][rel].update(k for i in j[f"{rel}_ids"] for k in (autos[i]["kinds"] or ["none"]))
    res["near_identical_pairs"] = rows
    res["pooled"] = {}
    for primary in (True, False):
        p = pooled[primary]["n"]
        res["pooled"]["primary" if primary else "secondary"] = {
            **dict(p), "clock_rate": r4(p["clock_flags"] / p["with_both"], 5),
            "frame_rate": r4(p["frame_flags"] / p["with_both"], 5),
            "clock_rate_coupled": r4(p["clock_flags_coupled"] / p["with_both_coupled"], 4),
            "frame_rate_coupled": r4(p["frame_flags_coupled"] / p["with_both_coupled"], 4),
            "kinds": {r: dict(v) for r, v in kinds_by[primary].items()},
            "reads": {r: dict(v) for r, v in reads_by[primary].items()}}
    res["reference_city_pairs_R6D"] = reference_rates(lab)
    return res


def weather_gap(scratch: Path, base: str, town: str, date: str) -> dict:
    """The two sites' ERA5 cells and their mean absolute hourly outdoor-temperature difference over the run's days."""
    from vesper.env import weather
    from vesper.env.outdoor import RUN_DAYS
    from vesper.edt.trace import env_site
    cache = scratch / "null-cache-A" / "weather"
    vals, cells = [], []
    for city in (base, town):
        s = env_site(Env(city, date, 0.0, 4))
        series = {}
        cell = None
        for day in weather.utc_days(s.utc(0), s.utc(RUN_DAYS * 86400)):
            p = weather.cached(cache, s.lat, s.lon, day)
            data = json.loads(Path(p).read_text())
            cell = data.get("cell")
            for t, v in zip(data["hourly"]["time"], data["hourly"]["temperature_2m"], strict=True):
                series[t] = v
        vals.append(series)
        cells.append(cell)
    common = sorted(set(vals[0]) & set(vals[1]))
    diffs = [abs(vals[0][t] - vals[1][t]) for t in common if vals[0][t] is not None and vals[1][t] is not None]
    same_cell = cells[0] == cells[1]
    return {"same_era5_cell": same_cell, "hours": len(diffs),
            "mean_abs_temperature_difference_c": r4(sum(diffs) / len(diffs), 2) if diffs else None,
            "max_abs_temperature_difference_c": r4(max(diffs), 2) if diffs else None}


def reference_rates(lab: Path) -> dict:
    rd = load_sweep(lab, f"r6d-{RD}")
    envs = [e for e in r4a.envs_of(rd) if e.north_deg == 0.0]
    autos = rd["automations"]
    view = {"outcomes": defaultdict(dict), "rich": defaultdict(dict)}
    for i, x in autos.items():
        for k, o in x["outcomes"].items():
            view["outcomes"][k][i] = o
            view["rich"][k][i] = (x.get("rich") or {}).get(k)
    rates = {"clock": [], "frame": []}
    for dt in DATES:
        es = [e for e in envs if e.date == dt]
        for ea, eb in itertools.combinations(es, 2):
            j = judge_pair(autos, view, view, ea.key, eb.key, ea, eb)
            for rel in ("clock", "frame"):
                rates[rel].append(len(j[f"{rel}_ids"]) / j["both"])
    return {rel: {"pairs": len(v), "mean": r4(float(np.mean(v)), 4), "min": r4(min(v), 4), "max": r4(max(v), 4)}
            for rel, v in rates.items()}


# ---- report ---------------------------------------------------------------------------------------------------------

def report(a, lab: Path, scratch: Path) -> int:
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    out = {"analysis": "R7-A (protocol Addendum 12, post hoc): review 4's items 1(b, d, f), 4, 7, 8(a-c), 10, 11(a), "
                       "12, 13", "inputs": {"S_R": SR, "S_6": S6, "engine_corrected": RD}, "code_commit": commit7}
    for name in ("witness", "recon", "crossing", "cover"):
        p = scratch / f"{name}.json"
        if p.is_file():
            out[name] = json.loads(p.read_text())
    if any(scratch.glob(f"openloop-{S6}-*.pkl")):
        out["openloop"] = report_openloop(lab, scratch)
    if (scratch / "dead-reasons.pkl").is_file():
        out["dead_reason_precision"] = report_dead(lab, scratch)
    if (scratch / "null-A.pkl").is_file() and (scratch / "null-B.pkl").is_file():
        out["null_difference"] = report_null(lab, scratch)
    path = Path(a.out or ROOT / "results" / "edt" / f"r7a-{commit7}.json")
    write_json(path, out)
    print("wrote", path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True,
                    choices=("witness", "openloop", "recon", "dead", "crossing", "cover", "null", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r7-A/scratch"))
    ap.add_argument("--r6b-scratch", default=str(Path.home() / "vesper-lab/edt/r6-B/scratch"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--settings", default=",".join(SETTINGS), help="openloop: comma-separated settings")
    ap.add_argument("--dead-all", action="store_true", help="dead: probe every dead automation, not only the coupled")
    ap.add_argument("--time-limit", type=float, default=600.0, help="cover: the MILP's time limit, seconds")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    import vesper
    print("vesper from", Path(vesper.__file__).parent, flush=True)
    parts = {"witness": part_witness, "openloop": part_openloop, "recon": part_recon, "dead": part_dead,
             "crossing": part_crossing, "cover": part_cover, "null": part_null}
    if a.part == "report":
        return report(a, lab, scratch)
    res = parts[a.part](a, lab, scratch)
    if isinstance(res, dict):
        print(json.dumps(res, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
