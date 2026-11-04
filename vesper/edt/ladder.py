"""The necessity ladder: does the physics matter?

Rung 4 is the full environment model. Rungs 3 (daylight rescaled), 2 (Home Assistant's sun plus an assumed indoor
curve) and 1 (no site at all) are cheaper simulations; rung 0 is a static linter that never simulates. "Flagged" means
a non-empty `divergences` in a sweep result (the shape in the task's interfaces-sweep note). The question is how much of
what rung 4 flags the cheaper rungs reproduce.

Rung 0: a logistic regression on eight syntactic features of the automation as written, scored out of fold (5-fold
stratified cross-validation, seed 0) against "flagged at rung 4": the AUC and the recall at precision >= 0.55.
Rungs 1-3 against rung 4, over the automations bound in both: the share of rung-4 flags also flagged at the rung, the
firing-time error in hours (circular time of day), and the rank correlation of the per-automation time spreads.

numpy only: scikit-learn and scipy are not in the environment, so the regression, the AUC and Spearman's rho are here.
"""
from __future__ import annotations

import numpy as np

from vesper.edt.corpus import _first, _step_kinds

PRECISION_FLOOR = 0.55
FOLDS = 5
SEED = 0
L2 = 1.0

FEATURES = ("sun_trigger", "sun_condition", "numeric_state_illuminance", "numeric_state_temperature",
            "numeric_state_humidity", "numeric_state_weather", "time_trigger", "n_conditions")


# --- rung 0: the linter ------------------------------------------------------------------------------------------

def _listed(value) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def _step_kind(step) -> str:
    if isinstance(step, str):
        return "template"  # a shorthand template condition
    if not isinstance(step, dict):
        return ""
    return str(step.get("platform") or step.get("trigger") or step.get("condition") or "")


def _conditions(steps) -> list:
    """Every condition, nested `conditions` (and/or/not) included, as a flat list."""
    out = []
    for s in _listed(steps):
        if not isinstance(s, (dict, str)):
            continue
        out.append(s)
        if isinstance(s, dict) and isinstance(s.get("conditions"), list):
            out.extend(_conditions(s["conditions"]))
    return out


def _kinds_of(step) -> set[str]:
    out: set[str] = set()
    _step_kinds(step, out)  # recurses into `conditions` itself, which is what a nested numeric_state needs
    return out


def linter_features(raw: dict) -> dict[str, float]:
    """Eight syntactic features of a corpus automation as parsed, both Home Assistant syntaxes (`trigger`/`triggers`,
    `condition`/`conditions`, `platform:`/`trigger:`), read without resolving anything:
    sun_trigger / sun_condition      a trigger / condition that reads the sun (the sun platform, a sun.* entity)
    numeric_state_<kind>             a numeric_state trigger or condition (nested ones too) on an entity that names
                                     illuminance, temperature, humidity or weather (the sun is the first two features)
    time_trigger                     a `time` or `time_pattern` trigger
    n_conditions                     conditions, nested members counted and a combinator counted too."""
    triggers = [t for t in _listed(_first(raw, "trigger", "triggers")) if isinstance(t, dict)]
    conditions = _conditions(_first(raw, "condition", "conditions"))
    numeric = set()
    for step in triggers + conditions:
        if _step_kind(step) == "numeric_state":
            numeric |= _kinds_of(step)
    f = {
        "sun_trigger": float(any("sun" in _kinds_of(t) for t in triggers)),
        "sun_condition": float(any("sun" in _kinds_of(c) for c in conditions)),
        "time_trigger": float(any(_step_kind(t) in ("time", "time_pattern") for t in triggers)),
        "n_conditions": float(len(conditions)),
    }
    for kind in ("illuminance", "temperature", "humidity", "weather"):
        f[f"numeric_state_{kind}"] = float(kind in numeric)
    return {k: f[k] for k in FEATURES}


# --- statistics (numpy only) -------------------------------------------------------------------------------------

def _average_ranks(x) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def auc(scores, labels) -> float | None:
    """Area under the ROC curve (Mann-Whitney U, ties counted half); None when a class is missing."""
    y = np.asarray(labels, dtype=int)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    ranks = _average_ranks(scores)
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def recall_at_precision(scores, labels, floor: float = PRECISION_FLOOR) -> dict:
    """Over every threshold t (predict positive when score >= t, thresholds taken from the scores themselves), the
    best recall among those whose precision is >= floor; ties in recall go to the higher precision. recall 0.0 and
    threshold None when no threshold reaches the floor."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    total = int(y.sum())
    best = {"recall": 0.0, "precision": None, "threshold": None}
    if total == 0:
        return best
    for t in sorted(set(s.tolist()), reverse=True):
        pred = s >= t
        tp = int((pred & (y == 1)).sum())
        precision = tp / int(pred.sum())
        recall = tp / total
        if precision >= floor and (recall > best["recall"]
                                   or (recall == best["recall"] and best["precision"] is not None
                                       and precision > best["precision"])):
            best = {"recall": recall, "precision": precision, "threshold": t}
    return best


def spearman(a, b) -> float | None:
    """Spearman's rho (Pearson over average ranks); None for fewer than 3 points or a constant series."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = _average_ranks(a), _average_ranks(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _fit_logistic(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """L2-regularised logistic regression by Newton's method on standardised columns (the intercept is free).
    Returns the weights and intercept on the standardised scale (deterministic: no randomness)."""
    n, d = x.shape
    w = np.zeros(d)
    b = 0.0
    reg = L2 * np.eye(d)
    for _ in range(100):
        p = 1.0 / (1.0 + np.exp(-(x @ w + b)))
        g_w = x.T @ (p - y) + L2 * w
        g_b = float((p - y).sum())
        r = np.maximum(p * (1 - p), 1e-9)
        h = np.zeros((d + 1, d + 1))
        h[:d, :d] = x.T @ (x * r[:, None]) + reg
        h[:d, d] = h[d, :d] = x.T @ r
        h[d, d] = float(r.sum())
        step = np.linalg.solve(h, np.append(g_w, g_b))
        w -= step[:d]
        b -= step[d]
        if np.abs(step).max() < 1e-8:
            break
    return w, b


def _out_of_fold_scores(x: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    fold = np.empty(len(y), dtype=int)
    for cls in (0, 1):  # stratified: each class dealt round-robin over the folds after a seeded shuffle
        idx = rng.permutation(np.flatnonzero(y == cls))
        fold[idx] = np.arange(len(idx)) % k
    scores = np.empty(len(y))
    for f in range(k):
        tr, te = fold != f, fold == f
        mu = x[tr].mean(axis=0)
        sd = x[tr].std(axis=0)
        sd[sd == 0] = 1.0
        w, b = _fit_logistic((x[tr] - mu) / sd, y[tr].astype(float))
        scores[te] = ((x[te] - mu) / sd) @ w + b
    return scores


def _rung0(autos: dict) -> dict:
    """The detector over the automations bound at rung 4 (an unbindable one was never tested, so it is no negative)."""
    rows = [a for a in autos.values() if a.get("bound") is not None]
    y = np.array([int(bool(a["divergences"])) for a in rows], dtype=int)
    base = {"features": list(FEATURES), "n": len(rows), "n_flagged": int(y.sum()), "folds": 0, "seed": SEED,
            "model": f"logistic regression, L2 {L2}, standardised features", "auc": None,
            f"recall_at_precision_{PRECISION_FLOOR}": None, "null": None}
    minority = int(min(y.sum(), len(y) - y.sum())) if len(rows) else 0
    if not rows:
        return {**base, "null": "no automation is bound at rung 4"}
    if minority == 0:
        return {**base, "null": "one class only: every bound automation is flagged at rung 4, or none is"}
    if minority < 2:
        return {**base, "null": "a class has fewer than 2 members: no cross-validation is possible"}
    k = min(FOLDS, minority)
    x = np.array([[linter_features(a["raw"])[f] for f in FEATURES] for a in rows], dtype=float)
    scores = _out_of_fold_scores(x, y, k)
    return {**base, "folds": k, "auc": auc(scores, y),
            f"recall_at_precision_{PRECISION_FLOOR}": recall_at_precision(scores, y, PRECISION_FLOOR)}


# --- rungs 1-3 against rung 4 ------------------------------------------------------------------------------------

def _circ(a: float, b: float) -> float:
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def _env_index(result: dict) -> dict:
    """(city, date, north_deg) -> env key: keys include the rung, so rungs are matched on the witness axes."""
    out = {}
    for e in result.get("envs", []):
        north = e.get("north_deg")
        out[(e.get("city"), e.get("date"), None if north is None else round(float(north), 6))] = e["key"]
    return out


def _spread(times: list[float]) -> float | None:
    """The largest circular gap between any two firing times (None for fewer than two)."""
    if len(times) < 2:
        return None
    return max(_circ(a, b) for i, a in enumerate(times) for b in times[i + 1:])


def _compare(r4: dict, rr: dict) -> dict:
    a4, ar = r4["automations"], rr["automations"]
    common = sorted(i for i in a4 if i in ar and a4[i].get("bound") is not None and ar[i].get("bound") is not None)
    i4, ir = _env_index(r4), _env_index(rr)
    shared = [(i4[m], ir[m]) for m in i4 if m in ir]
    flagged4 = [i for i in common if a4[i]["divergences"]]
    both = [i for i in flagged4 if ar[i]["divergences"]]
    errors: list[float] = []
    spreads4: list[float] = []
    spreadsr: list[float] = []
    for i in common:
        t4, tr = [], []
        for k4, kr in shared:
            o4, orr = a4[i]["outcomes"].get(k4), ar[i]["outcomes"].get(kr)
            if o4 is None or orr is None:
                continue
            f4 = o4.get("fired") and o4.get("first_tod_h") is not None
            fr = orr.get("fired") and orr.get("first_tod_h") is not None
            if f4:
                t4.append(float(o4["first_tod_h"]))
            if fr:
                tr.append(float(orr["first_tod_h"]))
            if f4 and fr:
                errors.append(_circ(float(o4["first_tod_h"]), float(orr["first_tod_h"])))
        s4, sr = _spread(t4), _spread(tr)
        if s4 is not None and sr is not None:
            spreads4.append(s4)
            spreadsr.append(sr)
    return {
        "n_bound_both": len(common),
        "n_envs_matched": len(shared),
        "n_flagged_rung4": len(flagged4),
        "n_flagged_both": len(both),
        "reproduced_share": len(both) / len(flagged4) if flagged4 else None,
        "n_flagged_rung_only": sum(1 for i in common if ar[i]["divergences"] and not a4[i]["divergences"]),
        "n_time_pairs": len(errors),
        "mae_hours": float(np.mean(errors)) if errors else None,
        "n_spread": len(spreads4),
        "spearman_time_spread": spearman(spreads4, spreadsr),
    }


def ladder_report(results_by_rung: dict[int, dict]) -> dict:
    """The necessity ladder from sweep results keyed by rung (rung 4 is required). Rung 0's detector is fitted on
    rung 4's automations; each other rung present is compared with rung 4. Degenerate inputs give None, not errors."""
    by_rung = {int(k): v for k, v in results_by_rung.items()}
    if 4 not in by_rung:
        raise ValueError("the ladder needs the rung 4 result to compare against")
    r4 = by_rung[4]
    return {
        "n_automations_rung4": len(r4["automations"]),
        "flagged": "a non-empty list of divergences",
        "rung0": _rung0(r4["automations"]),
        "rungs": {str(r): _compare(r4, by_rung[r]) for r in sorted(by_rung) if r != 4},
    }
