"""The necessity ladder: does the physics matter?

Rung 4 is the full environment model. Rungs 3 (daylight rescaled), 2 (Home Assistant's sun plus an assumed indoor
curve) and 1 (no site at all) are cheaper simulations; rung 0 is a static linter that never simulates. "Flagged" means
a non-empty `divergences` in a sweep result (the shape in the task's interfaces-sweep note). The question is how much of
what rung 4 flags the cheaper rungs reproduce.

Rung 0: a logistic regression on eight syntactic features of the automation as written, scored out of fold against
"flagged at rung 4". Folds hold whole repos (near-duplicate siblings never straddle training and test; seed 0); the
AUC carries a 95 % bootstrap interval over repos and sits beside the AUC of a plain stratified split. Recall at
precision >= 0.55 uses no oracle threshold: each fold's threshold is chosen on its training rows and applied to its
held-out rows, the base rate reported beside it.
Rungs 1-3 against rung 4, over the automations bound in both: the share of rung-4 flags also flagged at the rung, the
firing-time error in hours (circular time of day), and the rank correlation of the per-automation time spreads (the
shortest arc of the clock holding every first firing).

numpy only: scikit-learn and scipy are not in the environment, so the regression, the AUC and Spearman's rho are here.
"""
from __future__ import annotations

import numpy as np

from vesper.edt.corpus import first_present, step_kinds

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
    step_kinds(step, out)  # recurses into `conditions` itself, which is what a nested numeric_state needs
    return out


def linter_features(raw: dict) -> dict[str, float]:
    """Eight syntactic features of a corpus automation as parsed, both Home Assistant syntaxes (`trigger`/`triggers`,
    `condition`/`conditions`, `platform:`/`trigger:`), read without resolving anything:
    sun_trigger / sun_condition      a trigger / condition that reads the sun (the sun platform, a sun.* entity)
    numeric_state_<kind>             a numeric_state trigger or condition (nested ones too) on an entity that names
                                     illuminance, temperature, humidity or weather (the sun is the first two features)
    time_trigger                     a `time` or `time_pattern` trigger
    n_conditions                     conditions, nested members counted and a combinator counted too."""
    triggers = [t for t in _listed(first_present(raw, "trigger", "triggers")) if isinstance(t, dict)]
    conditions = _conditions(first_present(raw, "condition", "conditions"))
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


def _standardised_scores(x: np.ndarray, y: np.ndarray, tr: np.ndarray, te: np.ndarray):
    """Fit on the training rows; (train scores, test scores), both decision values."""
    mu = x[tr].mean(axis=0)
    sd = x[tr].std(axis=0)
    sd[sd == 0] = 1.0
    w, b = _fit_logistic((x[tr] - mu) / sd, y[tr].astype(float))
    return ((x[tr] - mu) / sd) @ w + b, ((x[te] - mu) / sd) @ w + b


def _cross_validate(x: np.ndarray, y: np.ndarray, fold: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    """Out-of-fold scores for the given fold assignment, and per fold the material for a held-out threshold. A fold
    whose training rows hold one class has no model: its test rows score 0 and its `train_scores` is None."""
    scores = np.zeros(len(y))
    folds: list[dict] = []
    for f in sorted(set(fold.tolist())):
        tr, te = fold != f, fold == f
        if len(set(y[tr].tolist())) < 2:
            folds.append({"train_scores": None, "train_labels": y[tr], "test_scores": scores[te], "test_labels": y[te]})
            continue
        s_tr, s_te = _standardised_scores(x, y, tr, te)
        scores[te] = s_te
        folds.append({"train_scores": s_tr, "train_labels": y[tr], "test_scores": s_te, "test_labels": y[te]})
    return scores, folds


def stratified_folds(y, k: int) -> np.ndarray:
    """Fold ids for a plain stratified split (seed 0): each class is shuffled and dealt round-robin over the folds,
    the second class starting where the first stopped so that fold sizes stay level."""
    y = np.asarray(y, dtype=int)
    rng = np.random.default_rng(SEED)
    fold = np.empty(len(y), dtype=int)
    start = 0
    for cls in (0, 1):
        idx = rng.permutation(np.flatnonzero(y == cls))
        fold[idx] = (np.arange(len(idx)) + start) % k
        start += len(idx)
    return fold


def grouped_folds(groups, y, k: int) -> np.ndarray:
    """Fold ids with every group (a repo) wholly in one fold, so a repo's near-duplicate automations are never split
    between training and test. Deterministic (seed 0): groups are shuffled, ordered by flagged count then size, and
    each goes to the fold with the fewest flagged (then fewest rows) so far."""
    groups = list(groups)
    y = np.asarray(y, dtype=int)
    names = sorted(set(groups))
    rng = np.random.default_rng(SEED)
    names = [names[i] for i in rng.permutation(len(names))]
    stats = {g: [0, 0] for g in names}  # flagged, rows
    for g, label in zip(groups, y, strict=True):
        stats[g][0] += int(label)
        stats[g][1] += 1
    order = sorted(names, key=lambda g: (-stats[g][0], -stats[g][1]))  # stable: ties keep the shuffled order
    load = [[0, 0] for _ in range(k)]
    where: dict[str, int] = {}
    for g in order:
        f = min(range(k), key=lambda i: (load[i][0], load[i][1], i))
        where[g] = f
        load[f][0] += stats[g][0]
        load[f][1] += stats[g][1]
    return np.array([where[g] for g in groups], dtype=int)


def heldout_recall_at_precision(folds: list[dict], floor: float = PRECISION_FLOOR) -> dict:
    """Recall and precision with no oracle threshold: in each fold the threshold is chosen on the TRAINING rows' scores
    (best recall at precision >= floor there), applied to the held-out rows, and the held-out predictions are pooled.
    A fold with no model, or whose training scores reach the floor at no threshold, predicts nothing positive."""
    tp = fp = fn = tn = 0
    thresholds: list[float | None] = []
    for f in folds:
        thr = None
        if f["train_scores"] is not None:
            thr = recall_at_precision(f["train_scores"], f["train_labels"], floor)["threshold"]
        thresholds.append(thr)
        ts = np.asarray(f["test_scores"], dtype=float)
        tl = np.asarray(f["test_labels"], dtype=int)
        pred = ts >= thr if thr is not None else np.zeros(len(ts), dtype=bool)
        tp += int((pred & (tl == 1)).sum())
        fp += int((pred & (tl == 0)).sum())
        fn += int((~pred & (tl == 1)).sum())
        tn += int((~pred & (tl == 0)).sum())
    total = tp + fp + fn + tn
    return {"recall": tp / (tp + fn) if tp + fn else None, "precision": tp / (tp + fp) if tp + fp else None,
            "base_rate": (tp + fn) / total if total else None, "n_predicted_positive": tp + fp,
            "thresholds": thresholds, "degenerate_folds": sum(f["train_scores"] is None for f in folds)}


def bootstrap_auc_ci(scores, labels, groups, n: int = 1000) -> list[float] | None:
    """95 % percentile interval of the AUC over `n` resamples of whole groups (repos) with replacement, seed 0.
    A resample that holds one class is skipped; None when fewer than two groups or no usable resample."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=int)
    names = sorted(set(groups))
    if len(names) < 2:
        return None
    members = {g: [] for g in names}
    for i, g in enumerate(groups):
        members[g].append(i)
    parts = [np.array(members[g]) for g in names]
    rng = np.random.default_rng(SEED)
    values = []
    for _ in range(n):
        idx = np.concatenate([parts[j] for j in rng.integers(0, len(parts), len(parts))])
        a = auc(s[idx], y[idx])
        if a is not None:
            values.append(a)
    if not values:
        return None
    return [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]


def _rung0(autos: dict) -> dict:
    """The detector over the automations bound at rung 4 (an unbindable one was never tested, so it is no negative).
    Primary: cross-validation by repo. Beside it: the AUC of a plain stratified split, which near-duplicates inflate."""
    items = [(i, a) for i, a in autos.items() if a.get("bound") is not None]
    y = np.array([int(bool(a["divergences"])) for _, a in items], dtype=int)
    groups = [a.get("repo") or i for i, a in items]
    recall_key = f"recall_at_precision_{PRECISION_FLOOR}"
    base = {"features": list(FEATURES), "n": len(items), "n_flagged": int(y.sum()), "n_repos": len(set(groups)),
            "base_rate": float(y.mean()) if len(items) else None, "folds": 0, "folds_random": 0, "seed": SEED,
            "model": f"logistic regression, L2 {L2}, standardised features",
            "cv": "5-fold, whole repos per fold, stratified as well as repos allow",
            "auc": None, "auc_ci95": None, "auc_random_folds": None, recall_key: None, "null": None}
    minority = int(min(y.sum(), len(y) - y.sum())) if len(items) else 0
    if not items:
        return {**base, "null": "no automation is bound at rung 4"}
    if minority == 0:
        return {**base, "null": "one class only: every bound automation is flagged at rung 4, or none is"}
    if minority < 2:
        return {**base, "null": "a class has fewer than 2 members: no cross-validation is possible"}
    x = np.array([[linter_features(a["raw"])[f] for f in FEATURES] for _, a in items], dtype=float)
    k_random = min(FOLDS, minority)
    random_scores, _ = _cross_validate(x, y, stratified_folds(y, k_random))
    out = {**base, "folds_random": k_random, "auc_random_folds": auc(random_scores, y)}
    if base["n_repos"] < 2:
        return {**out, "null": "fewer than 2 repos: no cross-validation by repo is possible"}
    k = min(FOLDS, base["n_repos"])
    scores, folds = _cross_validate(x, y, grouped_folds(groups, y, k))
    held = heldout_recall_at_precision(folds, PRECISION_FLOOR)
    return {**out, "folds": k, "auc": auc(scores, y), "auc_ci95": bootstrap_auc_ci(scores, y, groups),
            recall_key: held}


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


def circular_range(times) -> float | None:
    """The shortest arc of the 24 h clock that holds every time: 24 minus the largest gap between neighbouring times
    (the wrap past midnight included). 23:50 and 00:10 -> 1/3 h; 0, 8, 16 -> 16 h. None for fewer than two times."""
    if len(times) < 2:
        return None
    t = sorted(float(x) % 24.0 for x in times)
    gaps = [b - a for a, b in zip(t, t[1:], strict=False)] + [t[0] + 24.0 - t[-1]]
    return 24.0 - max(gaps)


def _compare(r4: dict, rr: dict) -> dict:
    a4, ar = r4["automations"], rr["automations"]
    bound4 = sorted(i for i in a4 if a4[i].get("bound") is not None)
    common = [i for i in bound4 if i in ar and ar[i].get("bound") is not None]
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
        s4, sr = circular_range(t4), circular_range(tr)
        if s4 is not None and sr is not None:
            spreads4.append(s4)
            spreadsr.append(sr)
    moving = [(s4, sr) for s4, sr in zip(spreads4, spreadsr, strict=True) if s4 > 0]  # a flat rung-4 spread has no rank
    null = None
    if not common:
        null = "no automation is bound at both rungs"
    elif not shared:
        null = "no environment matches rung 4 on (city, date, north_deg): no firing time can be compared"
    return {
        "n_flagged_rung4_total": sum(1 for i in bound4 if a4[i]["divergences"]),
        "n_unbound_at_rung": len(bound4) - len(common),
        "n_bound_both": len(common),
        "n_envs_matched": len(shared),
        "n_flagged_rung4": len(flagged4),
        "n_flagged_both": len(both),
        "reproduced_share": len(both) / len(flagged4) if flagged4 else None,
        "n_flagged_rung_only": sum(1 for i in common if ar[i]["divergences"] and not a4[i]["divergences"]),
        "n_time_pairs": len(errors),
        "mae_hours": float(np.mean(errors)) if errors else None,
        "n_spread_both": len(spreads4),
        "n_spread": len(moving),
        "spearman_time_spread": spearman([m[0] for m in moving], [m[1] for m in moving]),
        "null": null,
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
