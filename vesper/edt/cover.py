"""The environment cover and witnesses, from a sweep result (docs/experiments/edt-protocol.md, section 6).

An environment *exposes* a flagged automation when its outcome there departs from what the automation does in most
environments, kind by kind: the minority side of fired / not fired (a tie: both sides); a first firing outside the
modal 3-hour window of the firing environments (tied windows: outside any of them); an environmental envelope kind
(flaps, night_light, night_cover) where it holds. A set of environments covers the automation if it holds one that
exposes it. Beside it, the set-based coverage assumes no neutral behaviour: a set S covers an automation iff the
oracle's divergences over its outcomes in S are non-empty, an envelope kind holding in every environment of S being
static there (protocol addendum 1, A1.3).

`greedy_cover` adds, one at a time, the environment exposing the most not-yet-covered automations (ties to the
smaller `Env.key`) until the target share is covered or nothing adds. `witnesses` lists the pairs of environments that
differ in exactly one axis (city, date, north_deg) and whose outcome classes differ. `cover_report` computes the cover
on the training half (sha256 parity of the repository name), its coverage on the held-out half, the random-5 and
six-city-grid baselines and the non-degeneracy check.
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter

from vesper.edt.oracle import TIME_SPREAD_H, divergences
from vesper.edt.trace import DATES as _TRACE_DATES
from vesper.edt.types import Env, Outcome

TARGET = 0.8
TARGET_SIZE = 5
MAX_SHARE = 0.5          # non-degeneracy: no single environment covers more than this share
RANDOM_DRAWS, RANDOM_SEED, RANDOM_SIZE = 100, 0, 5

GRID_DATES = (_TRACE_DATES[0], _TRACE_DATES[2])  # the hand-picked grid: January and July of the sweep's dates (A3.1)
GRID_NORTH = 0.0
AXES = ("north_deg", "date", "city")  # a minimal witness prefers the smallest change, in this order
ENVELOPE = ("flaps", "night_light", "night_cover")


def half(repo: str) -> str:
    """"train" when sha256 of the UTF-8 repository name (`owner/name`) is even, else "held_out"."""
    return "train" if int(hashlib.sha256(repo.encode("utf-8")).hexdigest(), 16) % 2 == 0 else "held_out"


def _gap(a: float, b: float) -> float:
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def _modal_windows(tods: list[float]) -> list[float]:
    """The starts of the circular 3-hour windows, each starting at one of `tods`, that hold the most of them."""
    counts = {start: sum(1 for t in tods if (t - start) % 24.0 <= TIME_SPREAD_H) for start in sorted(set(tods))}
    most = max(counts.values())
    return [start for start, n in counts.items() if n == most]


def exposing(by_env: dict[Env, Outcome], kinds) -> set[Env]:
    """The environments that expose an automation flagged for `kinds` (its environmental divergences)."""
    out: set[Env] = set()
    if "fires_somewhere_only" in kinds:
        fired = {e for e, o in by_env.items() if o.fired}
        silent = set(by_env) - fired
        if len(fired) < len(silent):
            out |= fired
        elif len(silent) < len(fired):
            out |= silent
        else:  # no majority: both sides expose
            out |= fired | silent
    if "time_spread" in kinds:
        tods = {e: o.first_tod_h for e, o in by_env.items() if o.fired and o.first_tod_h is not None}
        if tods:
            for start in _modal_windows(list(tods.values())):
                out |= {e for e, t in tods.items() if (t - start) % 24.0 > TIME_SPREAD_H}
    for kind in ENVELOPE:
        if kind in kinds:
            out |= {e for e, o in by_env.items() if getattr(o, kind)}
    return out


def coverage(envs, flagged: dict[str, set[Env]]) -> float | None:
    """The share of `flagged` automations that one of `envs` exposes (None when nothing is flagged)."""
    if not flagged:
        return None
    chosen = set(envs)
    return sum(1 for exp in flagged.values() if exp & chosen) / len(flagged)


def restricted_divergences(by_env: dict[Env, Outcome], envs) -> set[str]:
    """The environmental divergences an automation shows over `envs` alone: the oracle's divergences of its outcomes
    there, minus an envelope kind that holds in every one of them (static within the set)."""
    outs = {e: by_env[e] for e in envs if e in by_env}
    if not outs:
        return set()
    kinds = divergences(outs)
    return {k for k in kinds if k not in ENVELOPE or not all(getattr(o, k) for o in outs.values())}


def set_coverage(envs, by_auto: dict[str, dict[Env, Outcome]]) -> float | None:
    """The share of automations (id -> outcomes by Env) whose divergences restricted to `envs` are non-empty."""
    if not by_auto:
        return None
    envs = list(envs)
    return sum(1 for outs in by_auto.values() if restricted_divergences(outs, envs)) / len(by_auto)


def greedy_cover(flagged: dict[str, set[Env]], target: float = TARGET) -> list[Env]:
    """Environments, in the order chosen, until `target` of `flagged` is covered or no environment adds any."""
    if not flagged:
        return []
    candidates = sorted({e for exp in flagged.values() for e in exp}, key=lambda e: e.key)
    left = {a: exp for a, exp in flagged.items() if exp}
    chosen: list[Env] = []
    need = target * len(flagged)
    covered = 0
    while covered < need - 1e-9 and left:
        gains = Counter(e for exp in left.values() for e in exp)
        best = max(candidates, key=lambda e: gains.get(e, 0))  # the first (smallest key) of the best
        if gains.get(best, 0) == 0:
            break
        chosen.append(best)
        covered += gains[best]
        left = {a: exp for a, exp in left.items() if best not in exp}
    return chosen


def non_degeneracy(flagged: dict[str, set[Env]]) -> dict:
    """The largest share of `flagged` one environment covers, which one (smaller key on a tie), and whether it is
    at most MAX_SHARE."""
    counts = Counter(e for exp in flagged.values() for e in exp)
    if not flagged or not counts:
        return {"max_share": 0.0, "env": None, "ok": True, "threshold": MAX_SHARE}
    env, n = min(counts.items(), key=lambda kv: (-kv[1], kv[0].key))
    share = n / len(flagged)
    return {"max_share": share, "env": env.key, "ok": share <= MAX_SHARE, "threshold": MAX_SHARE}


def _differ(a: Outcome, b: Outcome) -> bool:
    if a.fired != b.fired:
        return True
    if a.fired and a.first_tod_h is not None and b.first_tod_h is not None \
            and _gap(a.first_tod_h, b.first_tod_h) > TIME_SPREAD_H:
        return True
    return any(getattr(a, k) != getattr(b, k) for k in ENVELOPE)


def _axis(a: Env, b: Env) -> str | None:
    diff = [ax for ax in ("city", "date", "north_deg") if getattr(a, ax) != getattr(b, ax)]
    return diff[0] if len(diff) == 1 and a.rung == b.rung else None


def witnesses(by_env: dict[Env, Outcome], envs) -> list[tuple[Env, Env, str]]:
    """Pairs (a, b, axis) of `envs` with an outcome, a before b in `envs`' order, differing in exactly one axis and
    with different outcome classes; in `envs`' order."""
    present = [e for e in envs if e in by_env]
    out = []
    for i, a in enumerate(present):
        for b in present[i + 1:]:
            axis = _axis(a, b)
            if axis and _differ(by_env[a], by_env[b]):
                out.append((a, b, axis))
    return out


def minimal_witness(by_env: dict[Env, Outcome], envs) -> tuple[Env, Env, str] | None:
    """The witness that changes the least: orientation, then date, then city; then the smaller keys."""
    found = witnesses(by_env, envs)
    if not found:
        return None
    return min(found, key=lambda w: (AXES.index(w[2]), w[0].key, w[1].key))


def _envs(result: dict) -> list[Env]:
    return [Env(e["city"], e["date"], e["north_deg"], e["rung"]) for e in result["envs"]]


def outcomes_of(entry: dict, envs: list[Env]) -> dict[Env, Outcome]:
    """An automation's outcomes in a sweep result, by Env (environments without one left out)."""
    return {e: Outcome(**entry["outcomes"][e.key]) for e in envs if e.key in entry.get("outcomes", {})}


def exposure_matrix(result: dict) -> dict[str, set[Env]]:
    """Every flagged automation of a sweep result -> the environments that expose it."""
    envs = _envs(result)
    return {aid: exposing(outcomes_of(a, envs), set(a["divergences"]))
            for aid, a in sorted(result["automations"].items()) if a.get("divergences")}


def _cov(envs, flagged) -> float | None:
    c = coverage(envs, flagged)
    return None if c is None else round(c, 6)


def _set_cov(envs, by_auto) -> float | None:
    c = set_coverage(envs, by_auto)
    return None if c is None else round(c, 6)


def cover_report(result: dict) -> dict:
    """The cover (training half), its held-out coverage, the baselines, non-degeneracy and the minimal witnesses.
    Every coverage is given twice: by exposure (`coverage_*`, the primary criterion) and set-based
    (`set_coverage_*`)."""
    envs = _envs(result)
    matrix = exposure_matrix(result)
    outs = {aid: outcomes_of(result["automations"][aid], envs) for aid in matrix}
    halves = {aid: half(result["automations"][aid]["repo"]) for aid in matrix}
    parts = {name: {a for a in matrix if name == "all" or halves[a] == name} for name in ("train", "held_out", "all")}
    exp = {name: {a: matrix[a] for a in sorted(ids)} for name, ids in parts.items()}
    sets = {name: {a: outs[a] for a in sorted(ids)} for name, ids in parts.items()}
    cover = greedy_cover(exp["train"], TARGET)
    rng = random.Random(RANDOM_SEED)
    draws = [rng.sample(envs, min(RANDOM_SIZE, len(envs))) for _ in range(RANDOM_DRAWS)] if envs else []

    def mean(fn, part):
        vals = [fn(d, part) for d in draws]
        return None if not vals or vals[0] is None else round(sum(vals) / len(vals), 6)

    def both(chosen, names):
        out = {}
        for name in names:
            out[f"coverage_{name}"] = _cov(chosen, exp[name])
            out[f"set_coverage_{name}"] = _set_cov(chosen, sets[name])
        return out

    rung = envs[0].rung if envs else 4
    grid = [e for e in envs if e.date in GRID_DATES and e.north_deg == GRID_NORTH and e.rung == rung]
    wit, by_axis = {}, Counter()
    for aid in matrix:
        w = minimal_witness(outs[aid], envs)
        if w is None:
            wit[aid] = None
            continue
        wit[aid] = {"a": w[0].key, "b": w[1].key, "axis": w[2]}
        by_axis[w[2]] += 1
    return {
        "rung": result.get("rung"),
        "flagged": {"all": len(matrix), "train": len(exp["train"]), "held_out": len(exp["held_out"])},
        "split": "sha256(repo.encode('utf-8')) parity, repo = 'owner/name'; even = train, odd = held_out",
        "cover": {"envs": [e.key for e in cover], "size": len(cover), "target": TARGET, "target_size": TARGET_SIZE,
                  **both(cover, ("train", "held_out", "all"))},
        "baselines": {
            "random_5": {"draws": RANDOM_DRAWS, "seed": RANDOM_SEED, "size": RANDOM_SIZE,
                         **{f"{kind}_{name}": mean(fn, part[name])
                            for kind, fn, part in (("coverage", coverage, exp), ("set_coverage", set_coverage, sets))
                            for name in ("held_out", "all")}},
            "six_city_grid": {"envs": [e.key for e in grid], "size": len(grid), **both(grid, ("held_out", "all"))},
        },
        "non_degeneracy": non_degeneracy(matrix),
        "single_env_share": {e.key: _cov([e], matrix) for e in envs} if matrix else {},
        "witnesses": wit,
        "witnesses_by_axis": {ax: by_axis[ax] for ax in AXES},
        "no_witness": sum(1 for w in wit.values() if w is None),
    }
