#!/usr/bin/env python3
"""Post hoc revision analyses of the environmental differential testing (EDT) results, on cached results only.

Nothing here is pre-registered and nothing re-runs a sweep: every number is computed from the local results of commit
C (default dc119cd) that `scripts/edt_summary.py` also reads, with the oracle's and the cover's own code
(vesper.edt.oracle, vesper.edt.cover). The output holds aggregates only: counts, rates, intervals, environment labels
(city, date, plan north) and anonymous indices (A1, A2, ...). It holds no automation, no repository or entity name, no
automation or repository hash and no corpus path.

    .venv/bin/python scripts/edt_analysis/revision_analyses.py [--lab DIR] [--commit dc119cd]
        [--out results/edt/analysis-dc119cd.json]

Inputs, in --lab (default ~/vesper-lab/edt), for commit C:
    sweep-rung{4,3,2,1}-C.json      `vesper edt run` at each rung
    ladder-C.json                   `vesper edt ladder`
    confirm-rung4-C.json            the closed-loop confirmation (vesper.edt.confirm)
    work-C/cache/weather            the cached recorded weather the rung-4 traces read
and results/edt/census-20261104.json (committed) for the corpus-level repository counts.

Analyses (docs/experiments/results-edt.md, "Revision analyses"):
    1 time_spread threshold sweep and leave-one-out stability     6 oracle blind spots (environment-coupled, unflagged)
    2 repository-clustered uncertainty and concentration          7 the self-state subset (guards on own targets)
    3 arithmetic reconciliation                                   8 the closed loop's two arms, with intervals
    4 static classifications on random halves                     9 the below-10 C breaches, by automation and city
    5 the pair-based (set-based) cover
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from vesper.edt.cover import ENVELOPE, half, restricted_divergences
from vesper.edt.oracle import TIME_SPREAD_H, _circular_gap, divergences
from vesper.edt.types import Env, Outcome

ENV_KINDS = ("sun", "temperature", "humidity", "illuminance", "weather")
SENSED = {"temperature", "humidity", "illuminance", "weather"}
DIV_KINDS = ("time_spread", "fires_somewhere_only", "night_light", "flaps", "night_cover")
THRESHOLDS_H = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0)
BOOT_N, BOOT_SEED = 1000, 0
HALVES_N, HALVES_SEED = 20, 0
PAIR_SIZES = (2, 3, 4, 6)
RANDOM_DRAWS, RANDOM_SEED = 100, 0
SAME_TOD_H = 1 / 60  # "identical first time": within one minute
DAY_S = 86400.0


# ---- shared helpers ------------------------------------------------------------------------------------------------

def envs_of(result: dict) -> list[Env]:
    return [Env(e["city"], e["date"], e["north_deg"], e["rung"]) for e in result["envs"]]


def outs_of(entry: dict, envs: list[Env]) -> dict[Env, Outcome]:
    return {e: Outcome(**entry["outcomes"][e.key]) for e in envs if e.key in entry.get("outcomes", {})}


def label(env: Env) -> str:
    """An environment's label: city (first word), date, plan north."""
    return f"{env.city.split(',')[0]} {env.date} n{int(env.north_deg)}"


def ran(result: dict) -> dict[str, dict]:
    return {aid: a for aid, a in result["automations"].items() if a["outcomes"]}


def flagged(result: dict) -> dict[str, dict]:
    return {aid: a for aid, a in ran(result).items() if a["divergences"]}


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def cluster_bootstrap(pairs_by_cluster: dict[str, tuple[float, float]], n: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    """Ratio sum(num)/sum(den) with a percentile 95 % interval from resampling whole clusters with replacement.
    `pairs_by_cluster`: cluster -> (numerator, denominator)."""
    keys = sorted(pairs_by_cluster)
    num = sum(v[0] for v in pairs_by_cluster.values())
    den = sum(v[1] for v in pairs_by_cluster.values())
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        draw = [pairs_by_cluster[k] for k in rng.choices(keys, k=len(keys))]
        d = sum(v[1] for v in draw)
        if d:
            vals.append(sum(v[0] for v in draw) / d)
    vals.sort()
    lo, hi = vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]
    return {"num": num, "den": den, "rate": round(num / den, 4) if den else None, "ci95": [round(lo, 4), round(hi, 4)],
            "clusters": len(keys), "resamples": n, "seed": seed}


def gini(values: list[int]) -> float:
    xs = sorted(values)
    n, total = len(xs), sum(xs)
    if not n or not total:
        return 0.0
    return round(sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * total), 4)


def concentration(counts: Counter) -> dict:
    vals = sorted(counts.values(), reverse=True)
    total = sum(vals)
    return {"automations": total, "repositories": len(vals), "max_per_repo": vals[0] if vals else 0,
            "top10_share": round(sum(vals[:10]) / total, 4) if total else None,
            "top1_share": round(vals[0] / total, 4) if total else None, "gini": gini(vals),
            "median_per_repo": statistics.median(vals) if vals else None}


# ---- 1 time_spread threshold ---------------------------------------------------------------------------------------

def divergences_at(by_env: dict[Env, Outcome], h: float) -> set[str]:
    """The oracle's divergences with the time_spread threshold set to `h` hours (everything else unchanged), minus the
    envelope kinds static over these environments (the sweep's rule)."""
    kinds = divergences(by_env) - {"time_spread"}
    tods = [o.first_tod_h for o in by_env.values() if o.fired and o.first_tod_h is not None]
    if any(_circular_gap(a, b) > h for i, a in enumerate(tods) for b in tods[i + 1:]):
        kinds.add("time_spread")
    static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in by_env.values())}
    return kinds - static


def circular_range(tods: list[float]) -> float:
    """The shortest arc of the 24 h clock holding every time."""
    if len(tods) < 2:
        return 0.0
    xs = sorted(t % 24.0 for t in tods)
    gaps = [b - a for a, b in zip(xs, xs[1:])] + [xs[0] + 24.0 - xs[-1]]
    return 24.0 - max(gaps)


def threshold_sweep(s4: dict) -> dict:
    envs = envs_of(s4)
    autos = ran(s4)
    outs = {aid: outs_of(a, envs) for aid, a in autos.items()}
    rows = []
    for h in THRESHOLDS_H:
        kinds = {aid: divergences_at(o, h) for aid, o in outs.items()}
        rows.append({"h": h, "flagged": sum(1 for k in kinds.values() if k),
                     "time_spread": sum(1 for k in kinds.values() if "time_spread" in k),
                     "time_spread_only": sum(1 for k in kinds.values() if k == {"time_spread"}),
                     "environment_coupled_flagged": sum(1 for aid, k in kinds.items() if k and autos[aid]["kinds"]),
                     "not_coupled_flagged": sum(1 for aid, k in kinds.items() if k and not autos[aid]["kinds"])})
    check = next(r for r in rows if r["h"] == TIME_SPREAD_H)
    assert check["flagged"] == s4["flagged"]["n"], "threshold 3 h must reproduce the sweep's flags"
    return {"rows": rows, "reproduces_sweep_at_3h": True}


def loo_time_spread(s4: dict, h: float = TIME_SPREAD_H) -> dict:
    """For every time_spread-flagged automation: drop its most extreme environment (the one whose removal shrinks the
    circular range of first firing times most; ties to the smaller key) and test again. Also the same with whole
    (city, date) groups (both orientations) and whole cities removed, since the two orientations of a (city, date)
    give the same sun times."""
    envs = envs_of(s4)
    ts = {aid: outs_of(a, envs) for aid, a in flagged(s4).items() if "time_spread" in a["divergences"]}

    def group_of(env: Env, how: str):
        return {"env": env.key, "city_date": (env.city, env.date), "city": env.city}[how]

    out = {"n_time_spread_flagged": len(ts), "h": h}
    for how in ("env", "city_date", "city"):
        still_ts = still_flagged = 0
        lost_city = Counter()
        for o in ts.values():
            groups = sorted({group_of(e, how) for e in o}, key=str)
            best, best_range = None, None
            for g in groups:
                rest = [x.first_tod_h for e, x in o.items() if group_of(e, how) != g and x.fired
                        and x.first_tod_h is not None]
                r = circular_range(rest)
                if best_range is None or r < best_range - 1e-12:
                    best, best_range = g, r
            keep = [e for e in o if group_of(e, how) != best]
            if "time_spread" in divergences_at({e: o[e] for e in keep}, h):
                still_ts += 1
            elif how == "city":
                lost_city[best.split(",")[0]] += 1
            if restricted_divergences(o, keep):
                still_flagged += 1
        out[f"drop_most_extreme_{how}"] = {"still_time_spread": still_ts, "still_flagged": still_flagged,
                                           "share_still_time_spread": round(still_ts / len(ts), 4)}
        if how == "city":
            out["drop_most_extreme_city"]["lost_by_city_dropped"] = dict(lost_city.most_common())
    return out


# ---- 2 repository-clustered uncertainty ----------------------------------------------------------------------------

def clustered_uncertainty(s4: dict) -> dict:
    autos = ran(s4)
    by_repo_all: dict[str, list] = defaultdict(lambda: [0, 0])
    by_repo_ec: dict[str, list] = defaultdict(lambda: [0, 0])
    by_repo_kind = {k: defaultdict(lambda: [0, 0]) for k in ENV_KINDS}
    for a in autos.values():
        f = 1 if a["divergences"] else 0
        by_repo_all[a["repo"]][0] += f
        by_repo_all[a["repo"]][1] += 1
        if a["kinds"]:
            by_repo_ec[a["repo"]][0] += f
            by_repo_ec[a["repo"]][1] += 1
        for k in a["kinds"]:
            by_repo_kind[k][a["repo"]][0] += f
            by_repo_kind[k][a["repo"]][1] += 1
    rates = {"flagged_of_run": cluster_bootstrap({r: tuple(v) for r, v in by_repo_all.items()}),
             "env_coupled_flagged_of_env_coupled_run": cluster_bootstrap({r: tuple(v) for r, v in by_repo_ec.items()})}
    rates["by_quantity"] = {k: cluster_bootstrap({r: tuple(v) for r, v in by_repo_kind[k].items()}) for k in ENV_KINDS}
    for v in [rates["flagged_of_run"], rates["env_coupled_flagged_of_env_coupled_run"], *rates["by_quantity"].values()]:
        v["wilson95_iid"] = wilson(v["num"], v["den"])

    fl = flagged(s4)
    repos_by_div = {k: len({a["repo"] for a in fl.values() if k in a["divergences"]}) for k in DIV_KINDS}
    repos_by_env = {k: len({a["repo"] for a in fl.values() if k in a["kinds"]}) for k in ENV_KINDS}
    repos_by_env["not_environment_coupled"] = len({a["repo"] for a in fl.values() if not a["kinds"]})
    per_repo = Counter(a["repo"] for a in fl.values())
    run_repos = {a["repo"] for a in autos.values()}
    with_zeros = [per_repo.get(r, 0) for r in run_repos]

    # the largest sun pattern: reads the sun only, flagged for time_spread only
    pattern = {aid: a for aid, a in fl.items() if a["kinds"] == ["sun"] and a["divergences"] == ["time_spread"]}
    offs = Counter()
    by_offset = {"offset": Counter(), "no_offset": Counter(), "other": Counter()}
    for a in pattern.values():
        kind = sun_trigger_kind(a["bound"])
        offs[kind] += 1
        by_offset[kind][a["repo"]] += 1
    return {
        "rates": rates,
        "repositories_by_divergence_kind": repos_by_div,
        "repositories_by_environment_kind": repos_by_env,
        "flags_per_repository": {**concentration(per_repo),
                                 "gini_over_repos_with_a_run_automation": gini(with_zeros),
                                 "repos_with_a_run_automation": len(run_repos)},
        "sun_only_time_spread_only": {**concentration(Counter(a["repo"] for a in pattern.values())),
                                      "by_sun_trigger": {k: concentration(v) for k, v in by_offset.items() if v}},
    }


def _triggers(bound: dict) -> list:
    t = bound.get("trigger", bound.get("triggers")) if isinstance(bound, dict) else None
    return t if isinstance(t, list) else [t] if t else []


def sun_trigger_kind(bound: dict) -> str:
    """"offset": a sunrise/sunset trigger with a non-zero offset; "no_offset": one without; "other": no sun trigger
    (the automation reads the sun through a condition or a template)."""
    kinds = []
    for t in _triggers(bound):
        if isinstance(t, dict) and str(t.get("platform") or t.get("trigger") or "") == "sun":
            off = t.get("offset")
            kinds.append("offset" if off is not None and re.search(r"[1-9]", str(off)) else "no_offset")
    return "offset" if "offset" in kinds else "no_offset" if kinds else "other"


# ---- 3 arithmetic reconciliation -----------------------------------------------------------------------------------

def reconciliation(sweeps: dict[int, dict], ladder: dict, census: dict) -> dict:
    s4 = sweeps[4]
    f4 = set(flagged(s4))
    rung_sets = {}
    for r in (3, 2, 1):
        fr = set(flagged(sweeps[r]))
        rung_sets[str(r)] = {"flagged": len(fr), "subset_of_rung4": fr <= f4, "in_both": len(fr & f4),
                             "rung_only": len(fr - f4), "rung4_only": len(f4 - fr)}
    fl = flagged(s4)

    def group(a):
        ks = set(a["kinds"])
        if not ks:
            return "not_environment_coupled"
        if ks == {"sun"}:
            return "sun_only"
        return "sensed_and_sun" if "sun" in ks else "sensed_only"

    lost2 = f4 - set(flagged(sweeps[2]))
    sensed = [a for a in fl.values() if SENSED & set(a["kinds"])]
    sensor = {
        "flagged_reading_a_sensed_quantity": len(sensed),
        "of_which_without_sun": sum(1 for a in sensed if "sun" not in a["kinds"]),
        "of_which_with_sun": sum(1 for a in sensed if "sun" in a["kinds"]),
        "sensed_kind_counts_multicounted": {k: sum(1 for a in fl.values() if k in a["kinds"]) for k in sorted(SENSED)},
        "flagged_not_reading_sun": sum(1 for a in fl.values() if "sun" not in a["kinds"]),
        "flagged_by_group": dict(Counter(group(a) for a in fl.values())),
        "lost_at_rung2": len(lost2),
        "lost_at_rung2_by_group": dict(Counter(group(fl[a]) for a in lost2)),
    }
    autos = s4["automations"]
    repos = {
        "corpus_repositories": "1,186 (the collection's count; not recomputable from the sweep, which holds only "
                               "repositories that contribute an automation)",
        "with_automations_raw": census["repos_with_automations"]["raw"],
        "with_automations_deduplicated": len({a["repo"] for a in autos.values()}),
        "with_a_bound_automation": len({a["repo"] for a in autos.values() if a["bound"] is not None}),
        "ladder_rung0_repositories": ladder["rung0"]["n_repos"],
        "with_a_run_automation": len({a["repo"] for a in ran(s4).values()}),
        "with_a_flagged_automation": len({a["repo"] for a in fl.values()}),
        "with_an_environment_coupled_automation_deduplicated": census["repos_with_environment_coupled"]["deduplicated"],
        "with_a_run_environment_coupled_automation": len({a["repo"] for a in ran(s4).values() if a["kinds"]}),
    }
    rec = ladder["rung0"]["recall_at_precision_0.55"]
    tp = round(rec["recall"] * ladder["rung0"]["n_flagged"])
    ladder_rec = {"registered_criterion": "held-out recall at precision 0.55 >= 0.80",
                  "recall": round(rec["recall"], 4), "precision_realised": round(rec["precision"], 4),
                  "true_positives": tp, "predicted_positive": rec["n_predicted_positive"],
                  "n_flagged": ladder["rung0"]["n_flagged"], "n": ladder["rung0"]["n"],
                  "per_fold_thresholds": len(rec["thresholds"]),
                  "note": "a threshold per fold is set for precision >= 0.55 on that fold's training data; pooled "
                          "over the held-out folds the realised precision is higher (TP / predicted positive)"}

    run_ec = [a for a in ran(s4).values() if a["kinds"]]
    nk = Counter(len(a["kinds"]) for a in run_ec)
    per_kind_run = {k: sum(1 for a in run_ec if k in a["kinds"]) for k in ENV_KINDS}
    pairs = Counter(tuple(sorted(a["kinds"])) for a in run_ec if len(a["kinds"]) > 1)
    overlap = {"per_kind_run": per_kind_run, "sum_per_kind": sum(per_kind_run.values()),
               "distinct_environment_coupled_run": len(run_ec),
               "automations_by_number_of_kinds": {str(k): v for k, v in sorted(nk.items())},
               "extra_counts_from_overlap": sum((k - 1) * v for k, v in nk.items()),
               "kind_combinations": {"+".join(k): v for k, v in pairs.most_common()}}

    # Table I, assumption-free rows with "parsed" kept at the full population
    def row(pred):
        sub = [a for a in autos.values() if pred(a)]
        free = [a for a in sub if not a.get("assumptions")]
        b = [a for a in free if a["bound"] is not None]
        r = [a for a in b if a["outcomes"]]
        f = [a for a in r if a["divergences"]]
        return {"parsed": len(sub), "bound_assumption_free": len(b),
                "bound_share_of_parsed": round(len(b) / len(sub), 4) if sub else None,
                "run": len(r), "run_share_of_bound": round(len(r) / len(b), 4) if b else None, "flagged": len(f),
                "flagged_share_of_run": round(len(f) / len(r), 4) if r else None}
    table1 = {"all": row(lambda a: True), "environment_coupled": row(lambda a: bool(a["kinds"])),
              "not_environment_coupled": row(lambda a: not a["kinds"])}
    with_assumption = sum(1 for a in autos.values() if a["kinds"] and a.get("assumptions"))
    table1["note"] = (f"assumption-free rows over the full parsed population; the published environment-coupled row's "
                      f"'parsed' 4,719 = {sum(1 for a in autos.values() if a['kinds'])} - {with_assumption} "
                      f"(environment-coupled automations bound with an assumption)")
    return {"rungs_vs_rung4": rung_sets, "sensor_driven": sensor, "repositories": repos, "ladder_recall": ladder_rec,
            "per_quantity_run_overlap": overlap, "table1_assumption_free": table1}


# ---- 4 static classifications on random halves ---------------------------------------------------------------------

def static_halves(s4: dict) -> dict:
    envs = envs_of(s4)
    autos = ran(s4)
    outs = {aid: outs_of(a, envs) for aid, a in autos.items()}
    static48 = {aid for aid, a in autos.items() if a["static"]}
    env_flag48 = {aid for aid, a in autos.items() if set(a["divergences"]) & set(ENVELOPE)}
    rng = random.Random(HALVES_SEED)
    rows = []
    for _ in range(HALVES_N):
        hv = rng.sample(envs, len(envs) // 2)
        survive = new_static = env_to_static = 0
        for aid, o in outs.items():
            sub = {e: o[e] for e in hv if e in o}
            if not sub:
                continue
            static_h = {k for k in ENVELOPE if all(getattr(x, k) for x in sub.values())}
            st = set(autos[aid]["static"])
            if aid in static48 and st <= static_h:
                survive += 1
            if static_h and aid not in static48:
                new_static += 1
            if aid in env_flag48 and (set(autos[aid]["divergences"]) & set(ENVELOPE)) & static_h:
                env_to_static += 1
        rows.append({"static48_still_static": survive, "newly_static": new_static,
                     "envelope_divergence_turned_static": env_to_static})

    def summ(key):
        vs = [r[key] for r in rows]
        return {"min": min(vs), "max": max(vs), "mean": round(statistics.mean(vs), 2)}
    return {"halves": HALVES_N, "seed": HALVES_SEED, "half_size": len(envs) // 2, "static_over_48": len(static48),
            "envelope_divergent_over_48": len(env_flag48),
            "static48_still_static": summ("static48_still_static"), "newly_static": summ("newly_static"),
            "envelope_divergence_turned_static": summ("envelope_divergence_turned_static"),
            "note": "an envelope kind holding in all 48 environments holds in every subset, so the 353 survive by "
                    "construction; the non-monotone direction is the converse: a kind divergent over 48 can hold in "
                    "every environment of a half and become static there"}


# ---- 5 pair-based cover --------------------------------------------------------------------------------------------

def pair_cover(s4: dict) -> dict:
    envs = envs_of(s4)
    idx = {e: i for i, e in enumerate(envs)}
    fl = flagged(s4)
    outs = {aid: outs_of(a, envs) for aid, a in fl.items()}
    part = {aid: half(a["repo"]) for aid, a in fl.items()}
    pairs: dict[str, set] = {}
    for aid, o in outs.items():
        present = [e for e in envs if e in o]
        pairs[aid] = {(idx[a], idx[b]) for a, b in itertools.combinations(present, 2)
                      if restricted_divergences(o, [a, b])}
    groups = {"train": [a for a in fl if part[a] == "train"], "held_out": [a for a in fl if part[a] == "held_out"],
              "all": list(fl)}

    def covered(aid, chosen):
        cs = sorted(chosen)
        return any(p in pairs[aid] for p in itertools.combinations(cs, 2))

    def cov(chosen, name):
        ids = groups[name]
        return sum(1 for a in ids if covered(a, chosen)) / len(ids)

    order = sorted(range(len(envs)), key=lambda i: envs[i].key)
    rank = {i: r for r, i in enumerate(order)}
    best = max(itertools.combinations(order, 2), key=lambda p: (cov(p, "train"), -rank[p[0]], -rank[p[1]]))
    chosen = list(best)
    steps = {2: list(chosen)}
    while len(chosen) < max(PAIR_SIZES):
        cand = [i for i in order if i not in chosen]
        nxt = max(cand, key=lambda i: (cov(chosen + [i], "train"), -rank[i]))
        chosen.append(nxt)
        steps[len(chosen)] = list(chosen)
    greedy = {}
    for size in PAIR_SIZES:
        s = steps[size]
        set_based = sum(1 for a in groups["all"] if restricted_divergences(outs[a], [envs[i] for i in s]))
        assert set_based == sum(1 for a in groups["all"] if covered(a, s)), "pair and set formulations must agree"
        greedy[str(size)] = {"envs": [label(envs[i]) for i in s],
                             **{f"coverage_{n}": round(cov(s, n), 4) for n in ("train", "held_out", "all")}}
    rng = random.Random(RANDOM_SEED)
    rand = {}
    for size in PAIR_SIZES:
        draws = [rng.sample(range(len(envs)), size) for _ in range(RANDOM_DRAWS)]
        r = {}
        for n in ("train", "held_out", "all"):
            vals = sorted(cov(d, n) for d in draws)
            r[f"coverage_{n}_mean"] = round(statistics.mean(vals), 4)
            r[f"coverage_{n}_p5_p95"] = [round(vals[5], 4), round(vals[94], 4)]
        rand[str(size)] = r
    return {"criterion": "a set S covers an automation iff some pair of S shows a divergence (restricted_divergences "
                         "over the pair non-empty); equivalent to restricted_divergences over S non-empty",
            "flagged": {n: len(v) for n, v in groups.items()},
            "greedy_on_train": greedy, "random": {"draws": RANDOM_DRAWS, "seed": RANDOM_SEED, **rand},
            "tie_break": "the first pair is the best over all 1,128 pairs; ties to the smaller environment keys"}


# ---- 6 oracle blind spots ------------------------------------------------------------------------------------------

def blind_spots(s4: dict) -> dict:
    envs = envs_of(s4)
    pool = {aid: a for aid, a in ran(s4).items() if a["kinds"] and not a["divergences"]}
    cls = Counter()
    gaps = []
    static = 0
    for a in pool.values():
        o = outs_of(a, envs)
        if a["static"]:
            static += 1
        fired = [x for x in o.values() if x.fired]
        if not fired:
            cls["fired_in_no_environment"] += 1
            continue
        if len(fired) < len(o):  # cannot happen for an unflagged automation (fires_somewhere_only)
            cls["fired_in_some_only"] += 1
            continue
        tods = [x.first_tod_h for x in fired]
        g = max((_circular_gap(p, q) for p, q in itertools.combinations(tods, 2)), default=0.0)
        gaps.append(g)
        if g < SAME_TOD_H:
            cls["fired_everywhere_same_first_time_within_1_min"] += 1
        elif g <= 1.0:
            cls["fired_everywhere_first_times_differ_up_to_1h"] += 1
        elif g <= 2.0:
            cls["fired_everywhere_first_times_differ_1h_to_2h"] += 1
        else:
            cls["fired_everywhere_first_times_differ_2h_to_3h"] += 1
    exact = sum(1 for g in gaps if g == 0.0)
    return {"n": len(pool), "classes": dict(cls), "fired_everywhere_exactly_identical_first_time": exact,
            "with_a_static_envelope_kind": static,
            "not_in_cache": "firing counts per environment: the sweep keeps per environment only fired, the first "
                            "firing's time of day and the flaps / night_light / night_cover booleans, so 'fires a "
                            "different number of times (below the flap threshold) in different environments' and "
                            "'fires at different later times with the same first time' cannot be counted without "
                            "replaying the automations"}


# ---- 7 the self-state subset ---------------------------------------------------------------------------------------

_ID = re.compile(r"(?<![\w.])([a-z_]+)\.([a-z0-9_]+)\b")
_SERVICE_VERB = re.compile(r"^(turn_on|turn_off|toggle|open_cover|close_cover|stop_cover|set_\w+|lock|unlock|"
                           r"open|close|press|select_option|increment|decrement|reload|start|stop|pause|"
                           r"send_command)$")
_NOT_ENTITY_DOMAINS = {"states", "trigger", "this", "now", "state_attr", "is_state", "states_attr", "data", "value"}


def _entity_ids(node, skip_service: bool) -> set[str]:
    out: set[str] = set()

    def walk(x, key=None):
        if isinstance(x, str):
            if skip_service and key in ("service", "action"):
                return
            for m in _ID.finditer(x.replace("states.", "")):
                dom, obj = m.group(1), m.group(2)
                if dom in _NOT_ENTITY_DOMAINS or _SERVICE_VERB.match(obj):
                    continue
                out.add(f"{dom}.{obj}")
        elif isinstance(x, dict):
            for k, v in x.items():
                if k not in ("id", "alias", "description"):
                    walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
    walk(node)
    return out


def self_state_kind(bound: dict) -> str | None:
    """"condition": a condition reads an entity an action targets (e.g. turn on only if off); "trigger": only a
    trigger does (e.g. off ten minutes after it turned on); None: neither."""
    if not isinstance(bound, dict):
        return None
    actions = _entity_ids(bound.get("action", bound.get("actions")), True)
    if not actions:
        return None
    cond = _entity_ids(bound.get("condition", bound.get("conditions")), False)
    trig = _entity_ids(bound.get("trigger", bound.get("triggers")), False)
    if cond & actions:
        return "condition"
    if trig & actions:
        return "trigger"
    return None


def self_state(s4: dict, confirm: dict) -> dict:
    autos = ran(s4)
    kind = {aid: self_state_kind(a["bound"]) for aid, a in autos.items()}
    fl = {aid for aid, a in autos.items() if a["divergences"]}
    dis = {d["id"] for d in confirm["disagreements"]}
    sample = {r["id"] for r in confirm["results"]}

    def count(ids, which):
        return sum(1 for a in ids if kind.get(a) in which)
    both = ("condition", "trigger")
    runs_by_kind = defaultdict(lambda: [0, 0])
    for r in confirm["results"]:
        k = kind.get(r["id"]) or "none"
        runs_by_kind[k][1] += 1
        runs_by_kind[k][0] += 0 if r["fields"] else 1
    return {
        "definition": "the bound automation's conditions (or, failing that, triggers) name an entity its actions "
                      "target (service names excluded; template entity references included)",
        "run": {"n": len(autos), "condition": count(autos, ("condition",)), "trigger_only": count(autos, ("trigger",)),
                "any": count(autos, both)},
        "flagged": {"n": len(fl), "condition": count(fl, ("condition",)), "trigger_only": count(fl, ("trigger",)),
                    "any": count(fl, both)},
        "closed_loop_sample": {"n": len(sample), "condition": count(sample, ("condition",)),
                               "trigger_only": count(sample, ("trigger",))},
        "closed_loop_disagreeing": {"n": len(dis), "condition": count(dis, ("condition",)),
                                    "trigger_only": count(dis, ("trigger",)),
                                    "neither": len(dis) - count(dis, both)},
        "closed_loop_run_agreement_by_kind": {k: {"agree": v[0], "runs": v[1], "rate": round(v[0] / v[1], 4)}
                                              for k, v in sorted(runs_by_kind.items())},
    }


# ---- 8 the closed loop's two arms ----------------------------------------------------------------------------------

def closed_loop_arms(s4: dict, confirm: dict) -> dict:
    autos = s4["automations"]
    out = {}
    for name, flag in (("flagged", True), ("unflagged", False), ("all", None)):
        rs = [r for r in confirm["results"] if flag is None or r["flagged"] == flag]
        agree = sum(1 for r in rs if not r["fields"])
        by_repo = defaultdict(lambda: [0, 0])
        by_auto = defaultdict(lambda: [0, 0])
        for r in rs:
            ok = 0 if r["fields"] else 1
            by_repo[autos[r["id"]]["repo"]][0] += ok
            by_repo[autos[r["id"]]["repo"]][1] += 1
            by_auto[r["id"]][0] += ok
            by_auto[r["id"]][1] += 1
        auto_all = sum(1 for v in by_auto.values() if v[0] == v[1])
        out[name] = {"runs": len(rs), "agree": agree, "rate": round(agree / len(rs), 4),
                     "wilson95_runs_iid": wilson(agree, len(rs)),
                     "repo_bootstrap95": cluster_bootstrap({k: tuple(v) for k, v in by_repo.items()})["ci95"],
                     "automation_bootstrap95": cluster_bootstrap({k: tuple(v) for k, v in by_auto.items()})["ci95"],
                     "automations": len(by_auto), "automations_agreeing_everywhere": auto_all,
                     "wilson95_automations": wilson(auto_all, len(by_auto)), "repositories": len(by_repo)}
    return out


# ---- 9 the below-10 C breaches -------------------------------------------------------------------------------------

def _setpoints(node) -> list[float]:
    vals: list[float] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("temperature", "target_temp_low", "target_temp_high") and isinstance(v, (int, float)):
                    vals.append(float(v))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(node)
    return sorted(set(vals))


def _thresholds(node) -> list[float]:
    vals: list[float] = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("below", "above") and isinstance(v, (int, float)):
                    vals.append(float(v))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(node)
    return sorted(set(vals))


def cold_breaches(s4: dict, confirm: dict, weather_cache: Path) -> dict:
    from vesper.edt.trace import env_site
    from vesper.env.outdoor import Outdoor
    envs = {e.key: e for e in envs_of(s4)}
    runs = defaultdict(list)
    for v in confirm["envelope"]["violations"]:
        if v.get("below_10"):
            runs[(v["id"], v["env_key"])].append(v)
    ids = sorted({aid for aid, _ in runs})
    name = {aid: f"A{i + 1}" for i, aid in enumerate(ids)}
    outdoor_cache: dict[str, dict] = {}

    def outdoor(env: Env) -> dict:
        if env.key not in outdoor_cache:
            od = Outdoor.for_run(env_site(env), weather_cache)
            ts = [od.at(t) for t in range(int(DAY_S), int(3 * DAY_S), 600)]  # the measured days, every 10 min
            temps = [s.temperature_c for s in ts]
            outdoor_cache[env.key] = {"mean_c": round(statistics.mean(temps), 1), "min_c": round(min(temps), 1),
                                      "max_c": round(max(temps), 1), "source": ts[0].sources["temperature"]}
        return outdoor_cache[env.key]

    per_run = []
    for (aid, key), cells in sorted(runs.items()):
        env = envs[key]
        b = s4["automations"][aid]["bound"]
        per_run.append({"automation": name[aid], "city": env.city.split(",")[0], "date": env.date,
                        "north": int(env.north_deg), "rooms_breached": len(cells),
                        "room_min_c": min(c["min_c"] for c in cells),
                        "baseline_min_c": min(c["baseline_min_c"] for c in cells),
                        "largest_drop_k": round(-min(c["delta_min_c"] for c in cells), 1),
                        "outdoor": outdoor(env),
                        "setpoints_c": _setpoints(b.get("action", b.get("actions"))),
                        "guard_thresholds": _thresholds([b.get("trigger", b.get("triggers")),
                                                         b.get("condition", b.get("conditions"))])})
    by_auto = {}
    for n in sorted(name.values(), key=lambda s: int(s[1:])):
        rs = [r for r in per_run if r["automation"] == n]
        by_auto[n] = {"runs": len(rs), "cells": sum(r["rooms_breached"] for r in rs),
                      "cities": sorted({r["city"] for r in rs}), "setpoints_c": rs[0]["setpoints_c"],
                      "guard_thresholds": rs[0]["guard_thresholds"],
                      "room_min_c": min(r["room_min_c"] for r in rs),
                      "outdoor_mean_c_range": [min(r["outdoor"]["mean_c"] for r in rs),
                                               max(r["outdoor"]["mean_c"] for r in rs)]}
    by_city = {}
    for c in sorted({r["city"] for r in per_run}):
        rs = [r for r in per_run if r["city"] == c]
        envk = sorted({(r["date"], r["north"]) for r in rs})
        by_city[c] = {"runs": len(rs), "cells": sum(r["rooms_breached"] for r in rs),
                      "automations": len({r["automation"] for r in rs}),
                      "environments": [f"{d} n{n}" for d, n in envk],
                      "outdoor": {f"{d} n{n}": next(r["outdoor"] for r in rs if (r["date"], r["north"]) == (d, n))
                                  for d, n in envk},
                      "room_min_c_range": [min(r["room_min_c"] for r in rs), max(r["room_min_c"] for r in rs)],
                      "baseline_min_c_range": [min(r["baseline_min_c"] for r in rs),
                                               max(r["baseline_min_c"] for r in rs)]}
    # every closed-loop run of the 7, breaching or not, by environment, beside that environment's outdoors
    context = defaultdict(lambda: {"runs": 0, "breaching_runs": 0})
    for r in confirm["results"]:
        if r["id"] in name:
            env = envs[r["env_key"]]
            k = f"{env.city.split(',')[0]} {env.date}"
            context[k]["runs"] += 1
            context[k]["breaching_runs"] += 1 if (r["id"], r["env_key"]) in runs else 0
            context[k]["outdoor_mean_c"] = outdoor(env)["mean_c"]
            context[k]["outdoor_min_c"] = outdoor(env)["min_c"]
    all_rep = {s4["automations"][a]["repo"] for a in ids}
    return {"runs": len(per_run), "cells": sum(r["rooms_breached"] for r in per_run), "automations": len(ids),
            "repositories": len(all_rep), "by_automation": by_auto, "by_city": by_city,
            "all_closed_loop_runs_of_the_7_by_environment": dict(sorted(context.items())), "per_run": per_run,
            "outdoor_window": "measured days 1-2, every 10 minutes, recorded weather from the trace cache"}


# ---- main ----------------------------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lab", type=Path, default=Path.home() / "vesper-lab" / "edt")
    ap.add_argument("--commit", default="dc119cd")
    ap.add_argument("--census", type=Path, default=Path("results/edt/census-20261104.json"))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    c = args.commit
    sweeps = {r: json.loads((args.lab / f"sweep-rung{r}-{c}.json").read_text()) for r in (4, 3, 2, 1)}
    ladder = json.loads((args.lab / f"ladder-{c}.json").read_text())
    confirm = json.loads((args.lab / f"confirm-rung4-{c}.json").read_text())
    census = json.loads(args.census.read_text())
    s4 = sweeps[4]
    report = {
        "commit": c, "post_hoc": True,
        "inputs": "the cached sweeps (rungs 4, 3, 2, 1), ladder and closed-loop results of the commit; no new runs",
        "1_time_spread_threshold": threshold_sweep(s4),
        "1_leave_one_out": loo_time_spread(s4),
        "2_clustered_uncertainty": clustered_uncertainty(s4),
        "3_reconciliation": reconciliation(sweeps, ladder, census),
        "4_static_halves": static_halves(s4),
        "5_pair_cover": pair_cover(s4),
        "6_blind_spots": blind_spots(s4),
        "7_self_state": self_state(s4, confirm),
        "8_closed_loop_arms": closed_loop_arms(s4, confirm),
        "9_cold_breaches": cold_breaches(s4, confirm, args.lab / f"work-{c}" / "cache" / "weather"),
    }
    out = args.out or Path("results/edt") / f"analysis-{c}.json"
    out.write_text(json.dumps(report, indent=1, sort_keys=False) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
