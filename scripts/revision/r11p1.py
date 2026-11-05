#!/usr/bin/env python3
"""r11p1.py -- VESPER revision R11-P1 (post hoc, protocol Addendum 22): the eighth internal review's items 2(a), 3,
5(a), 5(b) and 9, on cached data only (no trace is recorded, no sweep is run).

Parts:

- `guards`   LOCAL ONLY: the distinct gate-open guards of the frame flags that carry an assumption, with the lexicon's
             class beside each, written to --scratch for A22.3's hand reading (never committed).
- `report`   results/edt/r11p1-<commit>.json: A22.1 the binder discount's arithmetic, A22.2 the 2 x 2 attribution of
             the time spread, A22.3 the gate-open assumption, A22.4 the occupancy range, A22.5 the full rung-0
             precision-recall curve. Counts, rates and anonymous curve points only: no automation, repository, entity
             name, assumption string, hash or path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r11p1.py --part P

Inputs (local, never committed): --lab/sweep-rung4-8fcd672.json, --lab/audit-r4c/labels-flagged.tsv,
--lab/r8-X/labels-sunloc.tsv.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "edt_analysis"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

COMMIT = "8fcd672"
H = 3.0
CELLS = {"clock_only": (False, False), "sun_normalisation_only": (True, False),
         "exemption_only": (False, True), "published": (True, True)}  # name: (sun_normalise, sensed_exempt)
RECALL_GRID = tuple(round(0.1 * k, 1) for k in range(1, 11))
BUDGETS = (0.10, 0.25, 0.50, 0.75)
Q_COUPLED = (184, 1205)  # R4-A: the environment-coupled run automations' frame flag rate
# A22.4's printed counts (re-read from 8fcd672's summary where it carries them)
E1_TRIGGER = "a helper or presence entity in a trigger"
E1_OTHERS = ("a helper or presence entity in a template or an action",
             "a helper or presence entity elsewhere (inside or/not, beside a device, as a threshold, in a zone or "
             "device condition)")
PRINTED = {"E1_helper_presence_in_trigger": 7257, "E2_dead_trigger_entities_never_change": 2762,
           "E3_cover_commanded": 1116, "E3_cover_run": 4, "E4_illuminance_parsed": 462, "E4_illuminance_run": 67,
           "E1prime_template_or_action": 4418, "E1prime_elsewhere": 814}

# ---- A22.1: the binder discount -------------------------------------------------------------------------------------


def discount(others: int, covered: int, misbind: int, uncovered: int, uncertain: int = 0,
             uncertain_weight: float = 0.0) -> float:
    """A12.5's rule, D = O + (c - m) + u (1 - m / c), with the uncertain labels counted as mis-binds at
    `uncertain_weight` (0, as coded: an uncertain label is kept at full weight)."""
    m = misbind + uncertain_weight * uncertain
    return others + (covered - m) + uncovered * (1 - m / covered)


def discount_line(others: int, covered: int, misbind: int, uncovered: int, uncertain: int) -> str:
    d = discount(others, covered, misbind, uncovered)
    return (f"D = O + (c - m) + u x (1 - m/c) = {others} + ({covered} - {misbind}) + {uncovered} x "
            f"(1 - {misbind}/{covered}) = {d:.4f}  [the {uncertain} uncertain labels among the c count as not a "
            f"mis-bind: weight 0 as a mis-bind]")


def review_readings(others: int, sensed: int, correct: int, correct_conv: int, misbind: int, uncertain: int) -> dict:
    total = correct + correct_conv + misbind + uncertain
    decided = total - uncertain
    prec = (correct + correct_conv) / decided
    share = misbind / total
    return {"labels": total, "decided": decided,
            "precision_over_decided": {"value": prec, "D": others + sensed * prec,
                                       "line": f"{others} + {sensed} x {correct + correct_conv}/{decided}"},
            "misbind_share_over_all": {"value": share, "D": others + sensed * (1 - share),
                                       "line": f"{others} + {sensed} x (1 - {misbind}/{total})"},
            "misbind_share_over_decided": {"value": misbind / decided,
                                           "D": others + sensed * (1 - misbind / decided)}}


def binder(s6: dict, lab: Path) -> dict:
    import r7a_analyses as r7a
    import r8x_analyses as r8x
    labels_path = lab / "audit-r4c" / "labels-flagged.tsv"
    b = r7a.binder_discount(s6, labels_path)
    prim = b["estimates"]["primary (uncertain as not a mis-bind)"]
    others, c, m, u = b["not_discounted"], prim["covered"], prim["misbind"], prim["uncovered"]
    unc = b["covered_by_label"].get("uncertain", 0)
    args = SimpleNamespace(sun_labels=str(lab / "r8-X" / "labels-sunloc.tsv"), r4c_labels=str(labels_path))
    sun = r8x.sunloc_report(args, lab, lab / "r11-P1" / "scratch")
    s = sun["mislocated_outside_the_148"]
    d = discount(others, c, m, u)
    all_labels = Counter(v[0] for v in r7a.read_labels(labels_path, 3).values())
    out = {"terms": {"O_not_discounted": others, "c_covered": c, "m_misbind": m, "u_uncovered": u,
                     "uncertain_among_covered": unc, "covered_by_label": b["covered_by_label"],
                     "sensed_frame_flags": b["sensed_frame"], "s_sun_mislocated_outside": s},
           "line": discount_line(others, c, m, u, unc),
           "D": round(d, 4), "D_ext": round(d - s, 4),
           "D_ext_line": f"D_ext = D - s = {d:.4f} - {s} = {d - s:.4f}",
           "checks": {"D_rounds_to_158.5": round(d, 1) == 158.5, "D_ext_rounds_to_144.5": round(d - s, 1) == 144.5,
                      "r7a_D": prim["D"], "r8x_D_ext": sun["discount"]["D_ext"]["point"]},
           "covered_misbind_share": m / c,
           "uncertain_weight_sensitivity": {str(w): round(discount(others, c, m, u, unc, w), 4)
                                            for w in (0.0, 0.5, 1.0)},
           "r4c_all_labels": dict(all_labels),
           "review_readings": review_readings(others, b["sensed_frame"], all_labels["correct"],
                                              all_labels["correct_conv"], all_labels["misbind"],
                                              all_labels["uncertain"]),
           "bootstrap_as_published": prim["repo_bootstrap_95"],
           "D_ext_bootstrap_as_published": sun["discount"]["D_ext"]["repo_bootstrap_95"]}
    return out


# ---- A22.2: the 2 x 2 attribution -----------------------------------------------------------------------------------


def frame_set_label(frames) -> str:
    return "+".join(sorted(frames)) or "none"


def attribution(counts: dict) -> dict:
    red = counts["clock_only"] - counts["published"]
    a = counts["clock_only"] - counts["sun_normalisation_only"]
    b = counts["clock_only"] - counts["exemption_only"]
    return {"reduction": red, "by_sun_normalisation_alone": a, "by_exemption_alone": b, "interaction": red - a - b}


def cells(s6: dict) -> dict:
    import r4a_analyses as r4a

    from vesper.edt import oracle
    from vesper.edt.sweep import ENVELOPE
    from vesper.edt.types import Outcome
    by_key = {e.key: e for e in r4a.envs_of(s6)}
    run = {i: a for i, a in s6["automations"].items() if a["outcomes"]}
    ts = Counter()
    flags = Counter()
    ts_by_frames: dict = {n: Counter() for n in CELLS}
    flags_by_frames: dict = {n: Counter() for n in CELLS}
    declared = Counter()
    mism = {"published_vs_stored": 0, "clock_only_vs_stored_clock": 0}
    for a in run.values():
        outs = {by_key[k]: Outcome(**o) for k, o in a["outcomes"].items()}
        ft = {by_key[k]: ((a.get("rich") or {}).get(k) or {}).get("first_t") for k in a["outcomes"]}
        kinds = oracle.divergences(outs)
        static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
        fr = oracle.frames_of(a["bound"])
        label = frame_set_label(fr.frames)
        declared[label] += 1
        for name, (sn, se) in CELLS.items():
            k = oracle.frame_divergences(outs, ft, fr, sun_normalise=sn, sensed_exempt=se) - static
            if "time_spread" in k:
                ts[name] += 1
                ts_by_frames[name][label] += 1
            if k:
                flags[name] += 1
                flags_by_frames[name][label] += 1
            if name == "published" and sorted(k) != sorted(a["frame_divergences"]):
                mism["published_vs_stored"] += 1
            if name == "clock_only" and sorted(k) != sorted(a["divergences"]):
                mism["clock_only_vs_stored_clock"] += 1
    pub = {i for i, a in run.items() if a["frame_divergences"]}
    exempting = sum(1 for i in pub if oracle.SENSED in oracle.frames_of(run[i]["bound"]).frames)
    return {"run": len(run), "time_spread": dict(ts), "flags": dict(flags),
            "time_spread_by_frame_set": {n: dict(sorted(c.items())) for n, c in ts_by_frames.items()},
            "flags_by_frame_set": {n: dict(sorted(c.items())) for n, c in flags_by_frames.items()},
            "declared_frame_sets_of_run": dict(sorted(declared.items())),
            "attribution_time_spread": attribution(ts), "attribution_flags": attribution(flags),
            "frame_flags_declaring_a_sensed_frame": exempting,
            "checks": {**mism, "clock_only_709": ts["clock_only"] == 709, "published_8": ts["published"] == 8,
                       "clock_only_790_flags": flags["clock_only"] == 790,
                       "published_199_flags": flags["published"] == 199}}


# ---- A22.3: the gate-open assumption --------------------------------------------------------------------------------

PRESENCE_DOMAINS = {"person", "device_tracker", "zone"}
PRESENCE_STATES = {"home", "not_home", "away"}
ENV_PHYSICAL = {"temp", "temperature", "temperatur", "heat", "heating", "heizung", "cool", "cooling", "frost", "freeze",
                "freezing", "cold", "hot", "warm", "humid", "humidity", "feucht", "feuchte", "lux", "illuminance",
                "bright", "brightness", "hell", "dark", "darkness", "dunkel", "donker", "sun", "sunny", "sonne", "zon",
                "daylight", "weather", "wetter", "rain", "raining", "regen", "wind", "windy", "storm", "snow"}
ENV_TIME_SEASON = {"night", "nacht", "day", "daytime", "evening", "morning", "dawn", "dusk", "winter", "summer",
                   "sommer", "season", "seasonal"}
_GUARD = re.compile(r"^(?P<eid>[a-z_]+\.[^\s\[]+)(?:\[(?P<attr>[^\]]+)\])?\s+(?P<rest>.+)$")


def parse_guard(text: str) -> tuple[str, str | None, str]:
    m = _GUARD.match(text.strip())
    if not m:
        return text.strip(), None, ""
    return m["eid"], m["attr"], m["rest"]


def is_numeric_guard(text: str) -> bool:
    rest = parse_guard(text)[2]
    return rest.startswith(">") or rest.startswith("<") or rest.startswith("in (")


def _required_states(rest: str) -> set[str]:
    if rest.startswith("== "):
        return {rest[3:].strip().lower()}
    if rest.startswith("in [") and rest.endswith("]"):
        return {s.strip().lower() for s in rest[4:-1].split(",")}
    return set()


def guard_class(text: str) -> str:
    eid, _, rest = parse_guard(text)
    domain, _, obj = eid.partition(".")
    if domain in PRESENCE_DOMAINS or _required_states(rest) & PRESENCE_STATES:
        return "presence"
    tokens = set(re.split(r"[^a-z]+", obj.lower())) - {""}
    if tokens & ENV_PHYSICAL:
        return "env_physical"
    if tokens & ENV_TIME_SEASON:
        return "env_time_season"
    return "other"


def flag_class(classes) -> str:
    cs = set(classes)
    if cs & {"env_physical", "env_time_season"}:
        return "environmental"
    if "presence" in cs:
        return "presence"
    return "other"


def gate_open(s6: dict) -> dict:
    from revision_analyses import wilson
    run = {i: a for i, a in s6["automations"].items() if a["outcomes"]}
    free = {i for i, a in run.items() if not a.get("assumptions")}
    out = {"run": len(run), "run_assumption_free": len(free), "run_with_an_assumption": len(run) - len(free)}
    for name, key in (("frame", "frame_divergences"), ("clock", "divergences")):
        flagged = {i for i, a in run.items() if a[key]}
        ff = flagged & free
        out[name] = {"flags": len(flagged), "flags_assumption_free": len(ff), "flags_with_an_assumption":
                     len(flagged - ff), "rate": len(flagged) / len(run),
                     "rate_wilson95": wilson(len(flagged), len(run)),
                     "rate_assumption_free": len(ff) / len(free), "rate_assumption_free_wilson95":
                     wilson(len(ff), len(free)),
                     "rate_among_assumed": len(flagged - ff) / (len(run) - len(free)),
                     "kinds_of_flags_with_an_assumption": dict(sorted(Counter(
                         k for i in flagged - free for k in run[i][key]).items()))}
    out["checks"] = {"frame_199_to_155": (out["frame"]["flags"], out["frame"]["flags_assumption_free"]) == (199, 155),
                     "clock_790_to_641": (out["clock"]["flags"], out["clock"]["flags_assumption_free"]) == (790, 641)}
    affected = [run[i] for i in sorted(run) if run[i]["frame_divergences"] and run[i].get("assumptions")]
    guard_classes = Counter()
    flags_by_class = Counter()
    physical_flags = 0
    numeric = 0
    several = 0
    for a in affected:
        cl = [guard_class(g) for g in a["assumptions"]]
        guard_classes.update(cl)
        flags_by_class[flag_class(cl)] += 1
        physical_flags += "env_physical" in cl
        numeric += sum(is_numeric_guard(g) for g in a["assumptions"])
        several += len(a["assumptions"]) > 1
    out["affected_frame_flags"] = {"n": len(affected), "by_class": dict(sorted(flags_by_class.items())),
                                   "with_a_physical_environmental_guard": physical_flags,
                                   "guards": sum(guard_classes.values()),
                                   "guards_by_class": dict(sorted(guard_classes.items())),
                                   "numeric_range_guards": numeric, "flags_with_several_guards": several,
                                   "kinds_reading_the_environment": dict(sorted(Counter(
                                       k for a in affected for k in a["kinds"]).items()))}
    return out


def part_guards(s6: dict, scratch: Path) -> None:
    """LOCAL ONLY: the distinct guards of the affected frame flags with the lexicon's class, for the hand reading."""
    run = {i: a for i, a in s6["automations"].items() if a["outcomes"]}
    rows = Counter()
    for a in run.values():
        if a["frame_divergences"] and a.get("assumptions"):
            for g in a["assumptions"]:
                rows[(guard_class(g), g)] += 1
    lines = ["# LOCAL ONLY. lexicon_class\tflags\tguard\thand_class (fill in: same | other class)"]
    lines += [f"{c}\t{n}\t{g}\t" for (c, g), n in sorted(rows.items())]
    (scratch / "guards.tsv").write_text("\n".join(lines) + "\n")
    print(len(rows), "distinct guards written to", scratch / "guards.tsv")


def hand_reading(scratch: Path) -> dict | None:
    path = scratch / "guards-read.tsv"
    if not path.is_file():
        return None
    rows = [r.split("\t") for r in path.read_text().splitlines() if r and not r.startswith("#")]
    differ = [r for r in rows if len(r) > 3 and r[3].strip() not in ("", "same")]
    return {"distinct_guards_read": len(rows), "classed_differently": len(differ),
            "flags_with_a_guard_classed_differently": sum(int(r[1]) for r in differ),
            "moves": dict(Counter(f"{r[0]} -> {r[3].strip()}" for r in differ))}


# ---- A22.4: the occupancy range -------------------------------------------------------------------------------------


def occupancy_rate(flags: int, run: int, q: float, excluded: int, dead: int) -> float:
    """The frame flag rate when `excluded` automations not run are counted and they and the `dead` (already among the
    run, unflagged) flag at rate q."""
    return (flags + q * (excluded + dead)) / (run + excluded)


def union_bounds(sizes) -> tuple[int, int]:
    return max(sizes), sum(sizes)


def occupancy(s6: dict, lab: Path) -> dict:
    summary = json.loads((ROOT / "results" / "edt" / f"summary-{COMMIT}.json").read_text())["rungs"]["4"]
    reasons = {r["class"]: r["n"] for r in summary["unbindable_reasons"]["top"]}
    ill = summary["by_environment_kind"]["illuminance"]
    checks = {"E1_from_summary": reasons.get(E1_TRIGGER), "E1prime_from_summary": [reasons.get(c) for c in E1_OTHERS],
              "illuminance_from_summary": [ill["parsed"], ill["run"]],
              "run_from_summary": summary["yield"]["run"], "parsed_from_summary": summary["yield"]["parsed"]}
    checks["all_match"] = (checks["E1_from_summary"] == PRINTED["E1_helper_presence_in_trigger"]
                           and checks["E1prime_from_summary"] == [PRINTED["E1prime_template_or_action"],
                                                                  PRINTED["E1prime_elsewhere"]]
                           and checks["illuminance_from_summary"] == [PRINTED["E4_illuminance_parsed"],
                                                                      PRINTED["E4_illuminance_run"]]
                           and checks["run_from_summary"] == 5926 and checks["parsed_from_summary"] == 43280)
    run = sum(1 for a in s6["automations"].values() if a["outcomes"])
    flags = sum(1 for a in s6["automations"].values() if a["outcomes"] and a["frame_divergences"])
    q = Q_COUPLED[0] / Q_COUPLED[1]
    e1 = PRINTED["E1_helper_presence_in_trigger"]
    e2 = PRINTED["E2_dead_trigger_entities_never_change"]
    e3 = PRINTED["E3_cover_commanded"] - PRINTED["E3_cover_run"]
    e4 = PRINTED["E4_illuminance_parsed"] - PRINTED["E4_illuminance_run"]
    e1p = e1 + PRINTED["E1prime_template_or_action"] + PRINTED["E1prime_elsewhere"]
    table = {"E1 unbindable: a helper or presence entity in a trigger": e1,
             "E2 run, dead: trigger entities never change (inside the 5,926)": e2,
             "E3 command a cover, not run": e3, "E4 read an illuminance, not run": e4,
             "E1' secondary: every helper/presence unbindable class": e1p}

    def rng(sizes, dead):
        lo_x, hi_x = union_bounds(sizes) if sizes else (0, 0)
        return {"X_bounds": [lo_x, hi_x],
                "at_q": [occupancy_rate(flags, run, q, x, dead) for x in (lo_x, hi_x)],
                "at_q0": [occupancy_rate(flags, run, 0.0, x, 0) for x in (lo_x, hi_x)]}

    alone = {"E1": rng([e1], 0), "E2": rng([], e2), "E3": rng([e3], 0), "E4": rng([e4], 0)}
    return {"flags": flags, "run": run, "published_rate": flags / run, "q": q, "q_counts": list(Q_COUPLED),
            "table": table, "printed": PRINTED, "each_class_alone": alone,
            "all_primary": rng([e1, e3, e4], e2), "all_secondary_with_E1prime": rng([e1p, e3, e4], e2),
            "dead_only_reflagged_in_place": occupancy_rate(flags, run, q, 0, e2), "checks": checks}


# ---- A22.5: the full curve ------------------------------------------------------------------------------------------


def pr_curve(scores, labels) -> list[dict]:
    import r10q
    thr, passed, tp, total = r10q._blocks(scores, labels)  # noqa: SLF001
    return [{"threshold": float(t), "passed": int(n), "tp": int(k), "precision": float(k / n),
             "recall": float(k / total)} for t, n, k in zip(thr, passed, tp, strict=True)]


def average_precision(scores, labels) -> float:
    ap, prev = 0.0, 0.0
    for pt in pr_curve(scores, labels):
        ap += pt["precision"] * (pt["recall"] - prev)
        prev = pt["recall"]
    return ap


def recall_at_budget(scores, labels, share: float) -> dict:
    n = len(scores)
    best = {"passed": 0, "tp": 0, "recall": 0.0, "precision": None}
    for pt in pr_curve(scores, labels):
        if pt["passed"] <= share * n + 1e-9:
            best = {"passed": pt["passed"], "tp": pt["tp"], "recall": pt["recall"], "precision": pt["precision"]}
    return best


def _summary(scores, labels) -> dict:
    import r10q
    curve = pr_curve(scores, labels)
    return {"n": len(scores), "positives": int(np.sum(labels)), "points": len(curve),
            "average_precision": average_precision(scores, labels),
            "precision_at_recall_grid": {str(r): {k: r10q.at_recall(scores, labels, r)[k] for k in
                                                  ("passed", "tp", "precision", "recall", "interpolated_precision")}
                                         for r in RECALL_GRID},
            "curve": curve}


def full_curves(autos: dict, lab: Path) -> dict:
    import r10q

    from vesper.edt import ladder as lad
    prior = sorted((ROOT / "results" / "edt").glob("r10q-*.json"))
    old = json.loads(prior[-1].read_text())["item6a_tradeoff"] if prior else {}
    out = {}
    checks = {}
    for name, key in r10q.RELATIONS:
        for frames in (False, True):
            feats = "8_plus_4" if frames else "8"
            y, s, g = lad.oof_scores(autos, key, "run", frames)
            y2, s2, _g2, _thr = lad.split_scores(autos, key, "run", frames)
            row = {"oof": _summary(s, y), "split": _summary(s2, y2)}
            row["oof"]["auc"] = round(lad.auc(s, y), 4)
            row["split"]["auc"] = round(lad.auc(s2, y2), 4)
            budget = {}
            for r in (0.8, 0.9):
                p = r10q.at_recall(s, y, r)
                p2 = r10q.at_recall(s2, y2, r)
                budget[str(r)] = {"oof_passed": p["passed"], "oof_share": p["passed"] / len(y),
                                  "oof_precision": p["precision"],
                                  "oof_share_ci95_repo_bootstrap": r10q.budget_share_ci(s, y, g, r),
                                  "split_passed": p2["passed"], "split_of": len(y2),
                                  "split_scaled_to_population": p2["passed"] / len(y2) * len(y),
                                  "split_precision": p2["precision"], "random_expected": r * len(y)}
                o = old.get(f"{name}/{feats}")
                if o:
                    for src in ("oof", "split"):
                        mine = (p if src == "oof" else p2)
                        theirs = o[src]["precision_at_recall"][str(r)]
                        checks[f"{name}/{feats}/{src}/{r}"] = (mine["passed"], mine["tp"]) == \
                            (theirs["passed"], theirs["tp"])
            row["budget"] = budget
            row["oof"]["recall_at_budget"] = {str(b): recall_at_budget(s, y, b) for b in BUDGETS}
            out[f"{name}/{feats}"] = row
            print(f"curve {name}/{feats}: {row['oof']['points']} points, AP {row['oof']['average_precision']:.3f}, "
                  f"budget@0.8 {budget['0.8']['oof_passed']}, @0.9 {budget['0.9']['oof_passed']}", flush=True)
    out["checks_against_A21_2"] = {"reproduced": all(checks.values()) if checks else None,
                                   "compared": len(checks), "file": prior[-1].name if prior else None}
    return out


# ---- main -----------------------------------------------------------------------------------------------------------


def report(a, lab: Path, scratch: Path) -> int:
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    s6 = json.loads((lab / f"sweep-rung4-{COMMIT}.json").read_text())
    out = {"analysis": "R11-P1 (protocol Addendum 22, post hoc): the binder discount's arithmetic, the 2 x 2 "
                       "attribution of the time spread, the gate-open assumption, the occupancy exclusion's "
                       "arithmetic bound, the full rung-0 precision-recall curve",
           "code_commit": commit7, "inputs": {"sweep": COMMIT}}
    out["item2a_binder_discount"] = binder(s6, lab)
    print("A22.1", out["item2a_binder_discount"]["line"], flush=True)
    out["item3_attribution"] = cells(s6)
    print("A22.2", out["item3_attribution"]["time_spread"], out["item3_attribution"]["checks"], flush=True)
    out["item5a_gate_open"] = gate_open(s6)
    out["item5a_gate_open"]["hand_reading"] = hand_reading(scratch)
    print("A22.3", out["item5a_gate_open"]["checks"], out["item5a_gate_open"]["affected_frame_flags"]["by_class"],
          flush=True)
    out["item5b_occupancy"] = occupancy(s6, lab)
    out["item9_pr_curve"] = full_curves(s6["automations"], lab)
    path = Path(a.out or ROOT / f"results/edt/r11p1-{commit7}.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print("wrote", path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("guards", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r11-P1/scratch"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    if a.part == "report":
        return report(a, lab, scratch)
    part_guards(json.loads((lab / f"sweep-rung4-{COMMIT}.json").read_text()), scratch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
