#!/usr/bin/env python3
"""Home variation (protocol Addendum 5): the standard EDT home and its four variants compared, at rung 4.

Descriptive only: Addendum 5 sets no pass/fail threshold. Every number is computed from local sweep results with the
oracle's and the cover's own code (vesper.edt.cover). The output holds aggregates only: counts, shares, environment
kinds and divergence kinds. It holds no automation, no repository or entity name, no automation or repository hash
and no corpus path.

    .venv/bin/python scripts/edt_analysis/home_variation.py [--lab DIR] [--base sweep-rung4-120f4ca.json]
        [--base-commit 120f4ca] [--variants-commit C] [--out results/edt/home-variation.json]

Inputs, in --lab (default ~/vesper-lab/edt): the standard home's rung-4 sweep (--base) and, for every variant NAME
of vesper.edt.home.VARIANTS, `sweep-rung4-variant-NAME.json` (`vesper edt run --rung 4 --home-variant NAME`).
"""
from __future__ import annotations

import argparse
import gc
import json
from collections import Counter
from pathlib import Path

from vesper.edt.cover import _differ, _envs, minimal_witness, outcomes_of
from vesper.edt.home import VARIANTS

ENV_KINDS = ("sun", "illuminance", "temperature", "humidity", "weather")
DIV_KINDS = ("time_spread", "fires_somewhere_only", "night_light", "flaps", "night_cover")
AXES = ("north_deg", "date", "city")
STANDARD = "standard"


def orientation_witness(outs: dict, envs) -> bool:
    """Two environments differing only in plan north (same city and date) with different outcome classes."""
    by_place: dict[tuple, list] = {}
    for e in envs:
        if e in outs:
            by_place.setdefault((e.city, e.date, e.rung), []).append(e)
    return any(_differ(outs[a], outs[b]) for group in by_place.values()
               for i, a in enumerate(group) for b in group[i + 1:])


def summarise(path: Path) -> tuple[dict, dict[str, dict]]:
    """(the home's aggregates, {automation id: {div, kinds, axis, orient}} for the automations that ran). The ids
    stay in memory only, to match automations across homes."""
    result = json.loads(path.read_text())
    envs = _envs(result)
    per: dict[str, dict] = {}
    for aid, a in result["automations"].items():
        if not a["outcomes"]:
            continue
        entry = {"div": list(a["divergences"]), "kinds": list(a["kinds"]), "axis": None, "orient": False}
        if a["divergences"]:
            outs = outcomes_of(a, envs)
            w = minimal_witness(outs, envs)
            entry["axis"] = w[2] if w else "none"
            entry["orient"] = orientation_witness(outs, envs)
        per[aid] = entry
    flagged = [e for e in per.values() if e["div"]]
    nc = result["negative_control"]
    home = {
        "home_variant": result.get("home_variant"),
        "rung": result["rung"],
        "environments": len(result["envs"]),
        "failed_envs": len(result.get("failed_envs") or {}),
        "yield": result["yield"],
        "negative_control": {k: nc[k] for k in ("n", "fired", "flagged", "rate", "halt")},
        "flagged": len(flagged),
        "flagged_by_divergence_kind": {k: sum(1 for e in flagged if k in e["div"]) for k in DIV_KINDS},
        "flagged_by_environment_kind": {**{k: sum(1 for e in flagged if k in e["kinds"]) for k in ENV_KINDS},
                                        "none_as_written": sum(1 for e in flagged if not e["kinds"])},
        "minimal_witness_axis": {**{ax: sum(1 for e in flagged if e["axis"] == ax) for ax in AXES},
                                 "none": sum(1 for e in flagged if e["axis"] == "none")},
        "orientation_witnessed": sum(1 for e in flagged if e["orient"]),
        "static": result["static"]["n"],
    }
    del result
    gc.collect()
    return home, per


def histogram(sets: dict[str, set], universe: set) -> dict:
    """For the members of `universe` in at least one of `sets`: how many of the sets hold each."""
    n = len(sets)
    counts = Counter(sum(1 for s in sets.values() if x in s) for x in universe)
    anywhere = sum(v for k, v in counts.items() if k >= 1)

    def share(k):
        return None if not anywhere else round(k / anywhere, 4)
    all_n, ge3, one = counts[n], sum(v for k, v in counts.items() if k >= 3), counts[1]
    return {"in_at_least_one": anywhere, "by_number_of_homes": {str(k): counts[k] for k in range(1, n + 1)},
            "in_all": all_n, "in_all_share": share(all_n), "in_at_least_3": ge3, "in_at_least_3_share": share(ge3),
            "in_only_1": one, "in_only_1_share": share(one)}


def jaccard(a: set, b: set) -> float | None:
    return None if not (a | b) else round(len(a & b) / len(a | b), 4)


def compare(per: dict[str, dict[str, dict]]) -> dict:
    homes = list(per)
    common = set.intersection(*(set(p) for p in per.values()))
    flagged = {h: {a for a in common if per[h][a]["div"]} for h in homes}
    variants = [h for h in homes if h != STANDARD]
    base = flagged[STANDARD]
    out = {
        "ran_in_every_home": len(common),
        "flagged": histogram(flagged, common),
        "standard_flagged_flagged_in_every_variant": {
            "n": len(base), "k": len(base & set.intersection(*(flagged[h] for h in variants))),
            "share": None if not base else round(len(base & set.intersection(*(flagged[h] for h in variants)))
                                                / len(base), 4)},
        "per_variant_vs_standard": {h: {"flagged_both": len(flagged[h] & base), "only_variant": len(flagged[h] - base),
                                        "only_standard": len(base - flagged[h]),
                                        "jaccard": jaccard(flagged[h], base)} for h in variants},
        "by_divergence_kind": {},
        "by_environment_kind": {},
    }
    for kind in DIV_KINDS:
        sets = {h: {a for a in common if kind in per[h][a]["div"]} for h in homes}
        if any(sets.values()):
            out["by_divergence_kind"][kind] = histogram(sets, common)
    for kind in ENV_KINDS:  # the kinds as written are the same in every home: the automation is the same
        members = {a for a in common if kind in per[STANDARD][a]["kinds"]}
        sets = {h: flagged[h] & members for h in homes}
        if any(sets.values()):
            out["by_environment_kind"][kind] = histogram(sets, common)
    orient = {h: {a for a in common if per[h][a]["orient"]} for h in homes}
    o0 = orient[STANDARD]
    in_variants = Counter(sum(1 for h in variants if a in orient[h]) for a in o0)
    out["orientation"] = {
        "witnessed_per_home": {h: len(orient[h]) for h in homes},
        "standard_witnessed": len(o0),
        "standard_witnessed_per_variant": {h: {"orientation_witness": len(o0 & orient[h]),
                                               "flagged_other_axis_only": len((o0 & flagged[h]) - orient[h]),
                                               "not_flagged": len(o0 - flagged[h]),
                                               "jaccard_witnessed_sets": jaccard(orient[h], o0)} for h in variants},
        "standard_witnessed_in_number_of_variants": {str(k): in_variants[k] for k in range(len(variants) + 1)},
        "standard_witnessed_in_all_variants": in_variants[len(variants)],
        "standard_witnessed_in_no_variant": in_variants[0],
        "witnessed": histogram(orient, common),
        "standard_witnessed_by_environment_kind": {
            k: {"n": sum(1 for a in o0 if k in per[STANDARD][a]["kinds"]),
                "in_all_variants": sum(1 for a in o0 if k in per[STANDARD][a]["kinds"]
                                       and all(a in orient[h] for h in variants))} for k in ENV_KINDS},
        "standard_witnessed_by_divergence_kind": {
            k: {"n": sum(1 for a in o0 if k in per[STANDARD][a]["div"]),
                "in_all_variants": sum(1 for a in o0 if k in per[STANDARD][a]["div"]
                                       and all(a in orient[h] for h in variants))} for k in DIV_KINDS},
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab" / "edt"))
    ap.add_argument("--base", default="sweep-rung4-120f4ca.json", help="the standard home's rung-4 sweep, in --lab")
    ap.add_argument("--base-commit", default="120f4ca")
    ap.add_argument("--variants-commit", required=True, help="the commit the variant sweeps ran at")
    ap.add_argument("--out", default="results/edt/home-variation.json")
    args = ap.parse_args()
    lab = Path(args.lab)
    files = {STANDARD: lab / args.base, **{v: lab / f"sweep-rung4-variant-{v}.json" for v in VARIANTS}}
    homes, per = {}, {}
    for name, path in files.items():
        homes[name], per[name] = summarise(path)
        print(f"{name}: flagged {homes[name]['flagged']}, orientation-witnessed {homes[name]['orientation_witnessed']}",
              flush=True)
    halted = [h for h, s in homes.items() if s["negative_control"]["halt"]]
    used = {h: p for h, p in per.items() if h not in halted}
    report = {
        "protocol": "docs/experiments/edt-protocol.md, Addendum 5 (descriptive; no pass/fail threshold)",
        "rung": 4,
        "base": {"home": STANDARD, "sweep": f"rung 4 at {args.base_commit}"},
        "variants": {v: {"parameter": VARIANTS[v], "sweep": f"rung 4 at {args.variants_commit}"} for v in VARIANTS},
        "halted": halted,
        "homes": homes,
        "across_homes": compare(used) if STANDARD in used and len(used) > 1 else None,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(f"written {out}")


if __name__ == "__main__":
    main()
