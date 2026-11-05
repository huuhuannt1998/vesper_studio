#!/usr/bin/env python3
"""r8x_analyses.py -- VESPER revision R8-X (post hoc, protocol Addendum 17): the fifth internal review's items 1, 2,
4(a), 6(b), 6(c) and 7(b), on saved runs only (no sweep, corner or offset is run again).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `members`  A17.1, A17.2  per source and setting, the membership record (frame divergences, orientation witness,
                    kinds) of every frame-flagged automation, recomputed from the saved outcomes and first-firing
                    instants: R7-A's open-loop replays, R7-B's corners and offsets, and the whole sweeps (`8fcd672`,
                    `5b54586`, R7-W's shortwave run, A8.8's variants and their standard home).
- `sunref`   A17.3  the sun references cut to the first named one.
- `residual` A17.4  the reference-free frames outside the crossing-time construction.
- `sundump`  A17.5  the 44 sun-reading frame flags dumped for hand labelling (LOCAL ONLY: corpus text).
- `report`   results/edt/r8x-<commit>.json: aggregates only (counts, rates, setting labels): no automation, alias,
             entity name, repository, hash or path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r8x_analyses.py --part P [--workers 3]

Inputs (local, never committed): --lab/sweep-rung4-*.json, --lab/r7-A/scratch (openloop-8fcd672-*.pkl),
--lab/r7-B/scratch (ol-c-*.pkl, ol-t-*.pkl), --lab/audit-r4c/labels-flagged.tsv, and the hand labels of A17.5
(--scratch/../labels-sunloc.tsv).
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import subprocess
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "edt_analysis"))
sys.path.insert(0, str(ROOT / "scripts" / "revision"))
sys.path.insert(0, str(ROOT))

from vesper.edt import oracle  # noqa: E402
from vesper.edt.types import Env, Outcome  # noqa: E402

S6, RD = "8fcd672", "r6d-5b54586"
SW = "r7w-shortwave-52ded7b"
VARIANTS = {"glazing x0.5": "r6a-variant-glazing-x0.5-ba8a54c", "glazing x2": "r6a-variant-glazing-x2-ba8a54c",
            "plan seed 8": "r6a-variant-plan-seed8-ba8a54c", "mirror east-west": "r6a-variant-mirror-ew-ba8a54c"}
VARIANT_BASE = "r6a-base-ba8a54c"
FAMILIES = ("frame_flags", "orientation_witnesses", "temperature_flags", "humidity_flags")
SEED, BOOT = 0, 1000

# A17.1: R7-A's settings (A12.4); the x1 replay is its check, not a setting
R7A_SETTINGS = ("ap0.21", "ap0.25", "ap0.29", "ap0.5", "ap2", "ds0.6", "ds0.95", "out-2", "out+2", "hc2", "hc3")
R7A_BRACKETS = {"aperture (R4-B)": ("ap0.25", "ap0.5", "nominal", "ap2"),
                "outdoor air (R4-B)": ("out-2", "nominal", "out+2"),
                "humidity class (R4-B)": ("nominal", "hc2", "hc3"),
                "k_hat (R6-B)": ("ap0.21", "ap0.25", "ap0.29"),
                "direct share (R6-B)": ("ds0.6", "nominal", "ds0.95"),
                "engine (R6-D)": ("nominal", "corrected")}
OFFSETS_REGISTERED = ("ref", "tin-1.7", "tin-0.9", "tin+2", "tin+7")
OFFSETS_SECONDARY = ("tin+1.7", "tin+0.9", "tin-2", "tin-7")


def r4(x, k=4):
    return None if x is None else round(float(x), k)


def wilson(k: int, n: int, z: float = 1.959964):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def dump(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(pickle.dumps(obj))
    tmp.replace(path)


def load_pkl(path: Path):
    return pickle.loads(path.read_bytes())


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")


# ---- memberships (A17.1, A17.2) -------------------------------------------------------------------------------------

def orient_pairs(envs) -> list[tuple[Env, Env]]:
    """Two environments of one city, date and rung differing only in plan north."""
    return [(a, b) for k, a in enumerate(envs) for b in envs[k + 1:]
            if (a.city, a.date, a.rung) == (b.city, b.date, b.rung) and a.north_deg != b.north_deg]


def witnessed(outs: dict, ft: dict, frames, pairs) -> bool:
    return any(oracle.frame_restricted_divergences(outs, ft, frames, [x, y]) for x, y in pairs
               if x in outs and y in outs)


def frame_kinds(outs: dict, ft: dict, frames) -> list[str]:
    """The frame relation's divergences as the sweep reports them: an envelope kind holding everywhere left out."""
    from vesper.edt.sweep import ENVELOPE
    if not outs:
        return []
    kinds = oracle.divergences(outs)
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
    return sorted(oracle.frame_divergences(outs, ft, frames) - static)


def record(bound: dict, outcomes: dict[str, dict], first_t: dict[str, float | None], by_key: dict, pairs) -> dict:
    """One automation's frame divergences and orientation witness from its outcomes by env key."""
    outs = {by_key[k]: Outcome(**o) for k, o in outcomes.items()}
    ft = {by_key[k]: first_t.get(k) for k in outcomes}
    frames = oracle.frames_of(bound)
    frame = frame_kinds(outs, ft, frames)
    return {"frame": frame, "witness": bool(frame) and witnessed(outs, ft, frames, pairs)}


def families_of(records: dict[str, dict], kinds: dict[str, set]) -> dict[str, set]:
    frame = {i for i, r in records.items() if r["frame"]}
    return {"frame_flags": frame, "orientation_witnesses": {i for i in frame if records[i]["witness"]},
            "temperature_flags": {i for i in frame if "temperature" in kinds[i]},
            "humidity_flags": {i for i in frame if "humidity" in kinds[i]}}


def _envs(sweep: dict) -> list[Env]:
    return [Env(e["city"], e["date"], e["north_deg"], e["rung"]) for e in sweep["envs"]]


def sweep_records(sweep: dict) -> dict[str, dict]:
    """A whole sweep's frame flags as the sweep reports them, with their orientation witness and kinds."""
    envs = _envs(sweep)
    pairs = orient_pairs(envs)
    out = {}
    for i, a in sweep["automations"].items():
        if not a["outcomes"] or not a.get("frame_divergences"):
            continue
        outs = {e: Outcome(**a["outcomes"][e.key]) for e in envs if e.key in a["outcomes"]}
        ft = {e: ((a.get("rich") or {}).get(e.key) or {}).get("first_t") for e in envs}
        out[i] = {"frame": sorted(a["frame_divergences"]), "kinds": sorted(a["kinds"]),
                  "witness": witnessed(outs, ft, oracle.frames_of(a["bound"]), pairs)}
    return out


_W: dict = {}


def _init(base_path: str) -> None:
    _W.update(load_pkl(Path(base_path)))


def _setting_job(job: dict):
    """Records of one saved open-loop setting (R7-A's or R7-B's pickle layout)."""
    by_key = {e.key: e for e in _W["envs"]}
    pairs = orient_pairs(_W["envs"])
    p = load_pkl(Path(job["path"]))
    o, f = defaultdict(dict), defaultdict(dict)
    for row in p["done"]:
        if job["layout"] == "r7a":  # (setting, key, outcomes, ref, secs, failure, first_t)
            key, outcomes, first_t, fail = row[1], row[2], row[6], row[5]
        else:  # r7b: (key, outcomes, secs, failure, first_t)
            key, outcomes, first_t, fail = row[0], row[1], row[4], row[3]
        if fail:
            raise RuntimeError(f"{job['source']} {job['setting']}: a failed environment in the saved run")
        for aid, x in outcomes.items():
            o[aid][key] = x
            f[aid][key] = first_t.get(aid)
    recs = {}
    for i in o:
        r = record(_W["bound"][i], o[i], f[i], by_key, pairs)
        if r["frame"]:
            recs[i] = r | {"kinds": _W["kinds"][i]}
    return job["source"], job["setting"], recs, sorted(o)


def part_members(a, lab: Path, scratch: Path) -> None:
    out_path = scratch / "members.pkl"
    have = load_pkl(out_path) if out_path.is_file() else {"records": {}, "replayed": {}}
    s6 = json.loads((lab / f"sweep-rung4-{S6}.json").read_text())
    run = {i: x for i, x in s6["automations"].items() if x["outcomes"]}
    base = {"envs": _envs(s6), "bound": {i: x["bound"] for i, x in run.items()},
            "kinds": {i: sorted(x["kinds"]) for i, x in run.items()}}
    base_path = scratch / "base-8fcd672.pkl"
    dump(base_path, base)
    t0 = time.monotonic()
    if ("whole", S6) not in have["records"]:
        have["records"][("whole", S6)] = sweep_records(s6)
    del s6
    for name in (RD, SW, VARIANT_BASE, *VARIANTS.values()):
        if ("whole", name) in have["records"]:
            continue
        s = json.loads((lab / f"sweep-rung4-{name}.json").read_text())
        have["records"][("whole", name)] = sweep_records(s)
        have["replayed"][("whole", name)] = len([1 for x in s["automations"].values() if x["outcomes"]])
        print(f"whole {name}: {len(have['records'][('whole', name)])} frame flags ({time.monotonic() - t0:.0f} s)",
              flush=True)
        dump(out_path, have)
    a7, b7 = Path(a.r7a_scratch).expanduser(), Path(a.r7b_scratch).expanduser()
    jobs = [{"source": "R7-A", "setting": n, "layout": "r7a", "path": str(a7 / f"openloop-{S6}-{n}.pkl")}
            for n in ("ref", *R7A_SETTINGS)]
    jobs += [{"source": "R7-B corners", "setting": p.stem[len("ol-c-"):], "layout": "r7b", "path": str(p)}
             for p in sorted(b7.glob("ol-c-*.pkl"))]
    jobs += [{"source": "R7-B offsets", "setting": n, "layout": "r7b", "path": str(b7 / f"ol-t-{n}.pkl")}
             for n in OFFSETS_REGISTERED + OFFSETS_SECONDARY]
    jobs = [j for j in jobs if (j["source"], j["setting"]) not in have["records"]]
    print(f"{len(jobs)} saved settings to read", flush=True)
    with Pool(a.workers, initializer=_init, initargs=(str(base_path),)) as pool:
        for k, (src, name, recs, ids) in enumerate(pool.imap_unordered(_setting_job, jobs), 1):
            have["records"][(src, name)] = recs
            have["replayed"][(src, name)] = len(ids)
            if k % 10 == 0 or k == len(jobs):
                dump(out_path, have)
                print(f"{k}/{len(jobs)} settings ({time.monotonic() - t0:.0f} s)", flush=True)
    dump(out_path, have)


# ---- the joint core (A17.1, A17.2) ----------------------------------------------------------------------------------

def joint(per_source: dict[str, dict[str, dict[str, set]]], published: dict[str, set]) -> dict:
    """Per family: the AND over every setting of every source (within the published family) and the OR; each source's
    own core; and the core without each source in turn."""
    out = {}
    for fam, pub in published.items():
        def core(srcs):
            return set(pub).intersection(*[s[fam] for src in srcs for s in per_source[src].values()])
        every = list(per_source)
        possible = set().union(*[s[fam] for src in every for s in per_source[src].values()])
        out[fam] = {"published": len(pub), "joint_core": len(core(every)), "joint_possible": len(possible),
                    "possible_outside_published": len(possible - set(pub)),
                    "settings": sum(len(v) for v in per_source.values()),
                    "own_core": {src: len(core([src])) for src in every},
                    "core_without": {src: len(core([s for s in every if s != src])) for src in every}}
    return out


def _fam(records: dict, kinds: dict | None = None) -> dict[str, set]:
    return families_of(records, {i: set(r["kinds"]) for i, r in records.items()} if kinds is None else kinds)


def core_report(scratch: Path) -> dict:
    m = load_pkl(scratch / "members.pkl")
    R = m["records"]
    pub = _fam(R[("whole", S6)])
    fam = {k: _fam(v) for k, v in R.items()}
    out = {"published": {f: len(s) for f, s in pub.items()}}

    # checks: each source's own published cores
    r7a = {n: fam[("R7-A", n)] for n in R7A_SETTINGS} | {"nominal": pub, "corrected": fam[("whole", RD)]}
    ref_a = fam[("R7-A", "ref")]
    out["check_r7a_x1_reproduces_published"] = {f: ref_a[f] == pub[f] for f in FAMILIES}
    out["check_r7a_brackets"] = {b: {f: len(set(pub[f]).intersection(*[r7a[n][f] for n in ns])) for f in FAMILIES}
                                 for b, ns in R7A_BRACKETS.items()}
    out["check_r7a_every_setting"] = {f: len(set(pub[f]).intersection(*[s[f] for s in r7a.values()]))
                                      for f in FAMILIES}
    corners = {k[1]: v for k, v in fam.items() if k[0] == "R7-B corners"}
    offs = {n: fam[("R7-B offsets", n)] for n in OFFSETS_REGISTERED}
    offs_all = {n: fam[("R7-B offsets", n)] for n in OFFSETS_REGISTERED + OFFSETS_SECONDARY}
    sw = {"shortwave": fam[("whole", SW)]}
    out["check_r7b"] = {"corners": len(corners),
                        "corner_ref_equals_published": {f: corners["ref"][f] == pub[f] for f in FAMILIES},
                        "offset_ref_equals_published": {f: offs["ref"][f] == pub[f] for f in FAMILIES},
                        "corners_core": {f: len(set(pub[f]).intersection(*[s[f] for s in corners.values()]))
                                         for f in FAMILIES},
                        "offsets_core": {f: len(set(pub[f]).intersection(*[s[f] for s in offs.values()]))
                                         for f in FAMILIES}}
    out["check_r7w_held"] = {f: len(pub[f] & sw["shortwave"][f]) for f in FAMILIES}
    per = {"R7-A (13 settings)": r7a, "R7-B corners (120)": corners, "R7-B offsets (5 registered)": offs,
           "R7-W shortwave (1)": sw}
    out["joint_139"] = joint(per, pub)
    # A17.2: the glazing bracket, and the joint core with it
    base = fam[("whole", VARIANT_BASE)]
    out["check_variant_base_equals_published"] = {f: base[f] == pub[f] for f in FAMILIES}
    var = {n: fam[("whole", s)] for n, s in VARIANTS.items()}
    out["variants"] = {n: {f: {"held": len(pub[f] & v[f]), "lost": len(pub[f] - v[f]), "gained": len(v[f] - pub[f]),
                               "members": len(v[f])} for f in FAMILIES} for n, v in var.items()}
    glaz = {"nominal": pub, "glazing x0.5": var["glazing x0.5"], "glazing x2": var["glazing x2"]}
    plan = {"nominal": pub, "plan seed 8": var["plan seed 8"], "mirror east-west": var["mirror east-west"]}
    out["glazing_bracket"] = joint({"glazing": glaz}, pub)
    out["plan_variants_secondary"] = joint({"plan": plan}, pub)
    out["joint_141_with_glazing"] = joint(per | {"glazing (2)": {k: v for k, v in glaz.items() if k != "nominal"}},
                                          pub)
    out["joint_with_secondary_offsets"] = joint(
        per | {"R7-B offsets (5 registered)": offs_all} | {"glazing (2)": {k: v for k, v in glaz.items()
                                                                            if k != "nominal"}}, pub)
    out["replayed_sets"] = {f"{k[0]}|{k[1]}": v for k, v in m["replayed"].items()
                            if k[0] == "whole" or k[1] in ("ref", "ap2", "tin+7")}
    return out


# ---- A17.3: the sun reference ---------------------------------------------------------------------------------------

def sun_ambiguity(frames) -> tuple[bool, bool]:
    """(two or more distinct sun instants named, two or more distinct events named) among the references used."""
    refs = frames.sun_refs
    events = {(r.event, r.elevation, r.rising) if r.event == "elevation" else r.event for r in refs}
    return len(set(refs)) >= 2, len(events) >= 2


def first_named(frames):
    return oracle.Frames(frames.frames, frames.sun_refs[:1])


def part_sunref(a, lab: Path, scratch: Path) -> dict:
    s6 = json.loads((lab / f"sweep-rung4-{S6}.json").read_text())
    envs = _envs(s6)
    pairs = orient_pairs(envs)
    run = {i: x for i, x in s6["automations"].items() if x["outcomes"]}
    amb_i, amb_ii = set(), set()
    nominal_mismatch, gained, lost, other_kinds, ts_pub, ts_first = 0, set(), set(), set(), 0, 0
    wit_changed, flagged_i = 0, set()
    for i, x in run.items():
        fr = oracle.frames_of(x["bound"])
        inst, ev = sun_ambiguity(fr)
        if not inst:
            continue
        amb_i.add(i)
        if ev:
            amb_ii.add(i)
        outs = {e: Outcome(**x["outcomes"][e.key]) for e in envs if e.key in x["outcomes"]}
        ft = {e: ((x.get("rich") or {}).get(e.key) or {}).get("first_t") for e in envs}
        pub = sorted(x["frame_divergences"])
        nominal = frame_kinds(outs, ft, fr)
        nominal_mismatch += nominal != pub
        first = frame_kinds(outs, ft, first_named(fr))
        if pub:
            flagged_i.add(i)
        ts_pub += "time_spread" in pub
        ts_first += "time_spread" in first
        if pub and not first:
            lost.add(i)
        elif first and not pub:
            gained.add(i)
        elif first != pub:
            other_kinds.add(i)
        w0 = bool(pub) and witnessed(outs, ft, fr, pairs)
        w1 = bool(first) and witnessed(outs, ft, first_named(fr), pairs)
        wit_changed += w0 != w1
    frame_flags = {i for i, x in run.items() if x["frame_divergences"]}
    res = {"run": len(run), "ambiguous_two_or_more_instants": len(amb_i),
           "ambiguous_two_or_more_events": len(amb_ii),
           "frame_flags_among_instants": len(amb_i & frame_flags),
           "frame_flags_among_events": len(amb_ii & frame_flags),
           "check_nominal_recomputation_mismatches": nominal_mismatch,
           "first_named": {"frame_flags_gained": len(gained), "frame_flags_lost": len(lost),
                           "frame_flags_other_kinds": len(other_kinds),
                           "changed_among_two_or_more_events": len((gained | lost | other_kinds) & amb_ii),
                           "time_spread_published": ts_pub, "time_spread_first_named": ts_first,
                           "orientation_witness_changed": wit_changed,
                           "frame_flags_after": len(frame_flags) + len(gained) - len(lost)}}
    write_json(scratch / "sunref.json", res)
    return res


# ---- A17.4: residual exposure ---------------------------------------------------------------------------------------

def reference_free(frames) -> tuple[bool, bool]:
    """(declares only reference-free frames, declares at least one); a reference-free frame is the sensed frame or a
    sun frame without references. No declared frame: judged in the clock frame, so neither."""
    free = {f for f in frames.frames if f == oracle.SENSED or (f == oracle.SUN and not frames.sun_refs)}
    return bool(frames.frames) and free == set(frames.frames), bool(free)


def in_crossing(bound: dict) -> bool:
    return bool(oracle.sensed_crossing_refs(bound))


def part_residual(a, lab: Path, scratch: Path) -> dict:
    s6 = json.loads((lab / f"sweep-rung4-{S6}.json").read_text())
    run = {i: x for i, x in s6["automations"].items() if x["outcomes"]}
    pop = {i: x for i, x in run.items() if not x["frame_divergences"]}
    sets = {"only_reference_free": set(), "some_reference_free": set()}
    crossing_all = {i for i, x in run.items() if in_crossing(x["bound"])}
    for i, x in pop.items():
        only, some = reference_free(oracle.frames_of(x["bound"]))
        if i in crossing_all:
            continue
        if only:
            sets["only_reference_free"].add(i)
        if some:
            sets["some_reference_free"].add(i)

    def describe(ids):
        fires2 = {i for i in ids if sum(1 for o in pop[i]["outcomes"].values() if o["fired"]) >= 2}
        ts = {i for i in ids if "time_spread" in pop[i]["divergences"]}
        return {"automations": len(ids), "fire_in_two_or_more_environments": len(fires2),
                "clock_relation_time_spread": len(ts), "clock_flags": sum(1 for i in ids if pop[i]["divergences"]),
                "by_frames": dict(Counter("+".join(sorted(oracle.frames_of(pop[i]["bound"]).frames)) for i in ids)),
                "reads": {k: sum(1 for i in ids if k in pop[i]["kinds"])
                          for k in ("temperature", "humidity", "illuminance", "weather", "sun")},
                "clock_time_spread_reads": {k: sum(1 for i in ts if k in pop[i]["kinds"])
                                            for k in ("temperature", "humidity", "illuminance", "weather", "sun")}}
    res = {"run": len(run), "not_frame_flagged": len(pop), "crossing_construction_run": len(crossing_all),
           "crossing_construction_not_flagged": len(crossing_all & set(pop)),
           "primary_only_reference_free": describe(sets["only_reference_free"]),
           "secondary_some_reference_free": describe(sets["some_reference_free"])}
    write_json(scratch / "residual.json", res)
    return res


# ---- A17.5: location precision, the extended discount ---------------------------------------------------------------

def location_precision(labels: dict[str, str], repo: dict[str, str], boot: int = BOOT, seed: int = SEED) -> dict:
    c = Counter(labels.values())
    loc, mis, unc = c["located"], c["mislocated"], c["uncertain"]
    repos = sorted({repo[i] for i in labels})
    by = defaultdict(list)
    for i in labels:
        by[repo[i]].append(i)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(boot):
        ids = [i for k in rng.integers(0, len(repos), len(repos)) for i in by[repos[k]]]
        cc = Counter(labels[i] for i in ids)
        if cc["located"] + cc["mislocated"]:
            vals.append(cc["located"] / (cc["located"] + cc["mislocated"]))
    return {"n": len(labels), "located": loc, "mislocated": mis, "uncertain": unc,
            "precision": r4(loc / (loc + mis)) if loc + mis else None, "wilson_95": wilson(loc, loc + mis),
            "precision_uncertain_as_mislocated": r4(loc / len(labels)) if labels else None,
            "wilson_95_uncertain_as_mislocated": wilson(loc, len(labels)),
            "repo_bootstrap_95": [r4(np.percentile(vals, 2.5)), r4(np.percentile(vals, 97.5))],
            "repositories": len(repos)}


def extended_point(d: float, mislocated_outside: int) -> float:
    return d - mislocated_outside


def _sensed_part(labels: dict) -> tuple[float, int]:
    """A12.5's discounted part of the sensed-frame flags: covered and not a mis-bind, plus the uncovered at the
    covered share; labels None = uncovered."""
    cov = [v for v in labels.values() if v is not None]
    m = sum(1 for v in cov if v == "misbind")
    share = m / len(cov) if cov else 0.0
    unc = sum(1 for v in labels.values() if v is None)
    return (len(cov) - m) + unc * (1 - share), len(labels)


def discount_bootstrap(sensed: dict, sun: dict, repo: dict, others: int, boot: int = BOOT, seed: int = SEED) -> dict:
    """D_ext = others + the sensed-frame part (A12.5's primary rule) + the sun readers not mislocated; a repository
    bootstrap over both parts together, each part rescaled to its own size."""
    sp, ns = _sensed_part(sensed)
    up = sum(1 for v in sun.values() if v != "mislocated")
    point = others + sp + up
    ids_all = list(sensed) + list(sun)
    repos = sorted({repo[i] for i in ids_all})
    by = defaultdict(list)
    for i in ids_all:
        by[repo[i]].append(i)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(boot):
        ids = [i for k in rng.integers(0, len(repos), len(repos)) for i in by[repos[k]]]
        s_ids = [i for i in ids if i in sensed]
        u_ids = [i for i in ids if i in sun]
        if s_ids:
            v, n = _sensed_part({k: sensed[i] for k, i in enumerate(s_ids)})
            s_val = v * ns / n
        else:
            s_val = sp
        u_val = (sum(1 for i in u_ids if sun[i] != "mislocated") * len(sun) / len(u_ids)) if u_ids else up
        vals.append(others + s_val + u_val)
    return {"point": r4(point, 2), "repo_bootstrap_95": [r4(np.percentile(vals, 2.5), 1),
                                                         r4(np.percentile(vals, 97.5), 1)],
            "repositories": len(repos)}


def read_labels(path: Path, ncol: int) -> dict[str, list[str]]:
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        out[parts[0].strip()] = [x.strip() for x in parts[1:ncol]]
    return out


def part_sundump(a, lab: Path, scratch: Path) -> None:
    """LOCAL ONLY: the 44 sun-reading frame flags, raw beside bound, every bound entity with its role and room."""
    import yaml

    from vesper.edt.home import build_edt_home
    work = scratch / "home-work"
    home_yaml, roles = build_edt_home(work, a.catalog)
    same = Path(home_yaml).read_text() == (lab / f"work-{S6}" / "edt" / "home.yaml").read_text()
    role_of = {eid: list(k) for k, eid in roles.items()}
    s6 = json.loads((lab / f"sweep-rung4-{S6}.json").read_text())
    run = {i: x for i, x in s6["automations"].items() if x["outcomes"]}
    sel = sorted(i for i, x in run.items() if x["frame_divergences"] and "sun" in x["kinds"])
    sensed = {i for i in sel if oracle.SENSED in oracle.frames_of(run[i]["bound"]).frames}
    r4c = read_labels(Path(a.r4c_labels).expanduser(), 3)
    lines = [f"# home identical to {S6}'s: {same}; {len(sel)} sun-reading frame flags"]
    for i in sel:
        x = run[i]
        ents = sorted(set(_entity_ids(x["bound"])))
        lines.append(f"=== {i[:12]} repo={x['repo']} kinds={x['kinds']} frame={x['frame_divergences']} "
                     f"sensed_frame={i in sensed} r4c={r4c.get(i[:12])}")
        for e in ents:
            lines.append(f"  BOUND {e} -> {role_of.get(e)}")
        raw = {k: v for k, v in x["raw"].items() if k not in ("id", "description")}
        lines.append(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=140)[:2500])
    (scratch / "dump-sun.txt").write_text("\n".join(lines))
    print(len(sel), "dumped; home identical:", same)


def _entity_ids(node):
    import re
    pat = re.compile(r"\b(?:sensor|binary_sensor|light|switch|climate|cover|fan|lock|media_player|input_\w+|"
                     r"weather|sun|scene|script|humidifier|water_heater|vacuum)\.[A-Za-z0-9_]+")
    if isinstance(node, str):
        yield from pat.findall(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _entity_ids(k)
            yield from _entity_ids(v)
    elif isinstance(node, list):
        for v in node:
            yield from _entity_ids(v)


def sunloc_report(a, lab: Path, scratch: Path) -> dict | None:
    path = Path(a.sun_labels).expanduser()
    if not path.is_file():
        return None
    s6 = json.loads((lab / f"sweep-rung4-{S6}.json").read_text())
    run = {i: x for i, x in s6["automations"].items() if x["outcomes"]}
    frame = {i for i, x in run.items() if x["frame_divergences"]}
    sun = sorted(i for i in frame if "sun" in run[i]["kinds"])
    sensed = {i for i in frame if oracle.SENSED in oracle.frames_of(run[i]["bound"]).frames}
    raw = read_labels(path, 5)  # id12, label, class, room_scoped (yes/no), guard (yes/no/-)
    lab44 = {i: raw[i[:12]] for i in sun}
    labels = {i: v[0] for i, v in lab44.items()}
    repo = {i: run[i]["repo"] for i in frame}
    out = {"population": len(sun), "labelled": sum(1 for i in sun if i[:12] in raw),
           "precision": location_precision(labels, repo),
           "classes_of_mislocated": dict(Counter(v[1] for v in lab44.values() if v[0] == "mislocated")),
           "no_room_scoped_role": sum(1 for v in lab44.values() if v[2] == "no"),
           "mislocated_role_is_a_guard": sum(1 for v in lab44.values() if v[0] == "mislocated" and v[3] == "yes"),
           "sun_readers_with_a_sensed_frame": len(set(sun) & sensed)}
    outside = [i for i in sun if i not in sensed]
    inside = [i for i in sun if i in sensed]
    out["mislocated_outside_the_148"] = sum(1 for i in outside if labels[i] == "mislocated")
    out["mislocated_inside_the_148"] = sum(1 for i in inside if labels[i] == "mislocated")
    r4c = read_labels(Path(a.r4c_labels).expanduser(), 3)
    sens_lab = {i: (r4c[i[:12]][0] if i[:12] in r4c else None) for i in sensed}
    others = len(frame) - len(sensed) - len(outside)
    sun_lab = {i: labels[i] for i in outside}
    out["discount"] = {"others_undiscounted": others, "sensed_frame": len(sensed), "sun_outside_148": len(outside),
                       "A12_5_primary_D": r4(others + len(outside) + _sensed_part(sens_lab)[0], 2),
                       "D_ext": discount_bootstrap(sens_lab, sun_lab, repo, others)}
    # secondary: the 6 inside count as mis-binds when either label says so
    sens2 = {i: ("misbind" if (v == "misbind" or (i in labels and labels[i] == "mislocated")) else v)
             for i, v in sens_lab.items()}
    out["discount"]["D_ext_secondary_inside_too"] = discount_bootstrap(sens2, sun_lab, repo, others)
    return out


# ---- A17.6: the rate check -------------------------------------------------------------------------------------------

def rate_ok(num: float, den: float, stated_pct: float, decimals: int) -> bool:
    """Whether num/den, as a percentage, rounds to the stated value at the stated decimals (half a unit either side)."""
    return abs(100.0 * num / den - stated_pct) <= 0.5 * 10 ** (-decimals) + 1e-9


# ---- main ------------------------------------------------------------------------------------------------------------

def report(a, lab: Path, scratch: Path) -> int:
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
    out = {"analysis": "R8-X (protocol Addendum 17, post hoc): the joint robust core, the glazing core, the "
                       "first-named sun reference, the residual exposure, the sun-flag location precision",
           "code_commit": commit7, "inputs": {"sweep": S6}}
    if (scratch / "members.pkl").is_file():
        out["item1_4a_cores"] = core_report(scratch)
    for name in ("sunref", "residual"):
        p = scratch / f"{name}.json"
        if p.is_file():
            out[{"sunref": "item6b_sun_reference", "residual": "item6c_residual_exposure"}[name]] = \
                json.loads(p.read_text())
    out["item7b_sun_location"] = sunloc_report(a, lab, scratch)
    rc = Path(a.rate_check).expanduser()
    if rc.is_file():
        out["item2_rate_check"] = json.loads(rc.read_text())
    path = Path(a.out or f"results/edt/r8x-{commit7}.json")
    write_json(path, out)
    print("wrote", path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("members", "sunref", "residual", "sundump", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r8-X/scratch"))
    ap.add_argument("--r7a-scratch", default=str(Path.home() / "vesper-lab/edt/r7-A/scratch"))
    ap.add_argument("--r7b-scratch", default=str(Path.home() / "vesper-lab/edt/r7-B/scratch"))
    ap.add_argument("--r4c-labels", default=str(Path.home() / "vesper-lab/edt/audit-r4c/labels-flagged.tsv"))
    ap.add_argument("--sun-labels", default=str(Path.home() / "vesper-lab/edt/r8-X/labels-sunloc.tsv"))
    ap.add_argument("--rate-check", default=str(Path.home() / "vesper-lab/edt/r8-X/rate-check.json"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    if a.part == "report":
        return report(a, lab, scratch)
    {"members": part_members, "sunref": part_sunref, "residual": part_residual, "sundump": part_sundump}[a.part](
        a, lab, scratch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
