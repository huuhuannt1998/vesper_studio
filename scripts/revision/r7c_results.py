"""Protocol Addendum 15: the aggregates file of revision R7-C, and A15.9 beside it.

Usage: python scripts/revision/r7c_results.py ANALYSIS R6C_FIXED_PAIRS SWEEP_8FCD672 OUT

ANALYSIS is `python -m vesper.validation.ha_semantics --analyse ...`'s output; R6C_FIXED_PAIRS is Addendum 10's per-pair
records with its fixed engine (3dcd4e7). A15.9: the automations of Addendum 10's 263 that carry a construct of the
stratum, tabulated with Addendum 10's measures. The output names automations by sample index only.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from vesper.validation import ha_diff
from vesper.validation.ha_semantics import carries, semantic_features


def beside_r6c(pairs: dict, autos: dict) -> dict:
    """A15.9: Addendum 10's records for the automations of its sample that carry a construct (`carries`)."""
    sample = [k for k, _ in pairs["sample"]]
    keep = [k for k in sample if carries(semantic_features(autos[k]["bound"]))]
    rows = [(e, k, pairs["per_env"][e]["per"][k]) for e in pairs["per_env"] for k in keep]
    both = [p for _, _, p in rows if p["v_fired"] and p["h_fired"]]
    deltas = [abs(p["delta_s"]) for p in both]
    status = Counter("both" if p["v_fired"] and p["h_fired"] else "vesper_only" if p["v_fired"] else
                     "ha_only" if p["h_fired"] else "neither" for _, _, p in rows)
    return {"automations": len(keep), "pairs": len(rows), "status": dict(status),
            "constructs": dict(Counter(f for k in keep for f in semantic_features(autos[k]["bound"]))),
            "both_fire": len(both), "abs_delta_lt_1s": sum(d < 1 for d in deltas),
            "abs_delta_lt_60s": sum(d < 60 for d in deltas), "max_abs_delta_s": max(deltas, default=None),
            "calls_equal": sum(p["calls_equal"] for p in both),
            "instants_equal": sum(p["v_count"] == p["h_count"] for p in both),
            "discrepant_pairs": sum(ha_diff.discrepant(p) for _, _, p in rows),
            "discrepant_automations": len({k for _, k, p in rows if ha_diff.discrepant(p)}),
            "one_engine_only_automations_initial_state_off": len({
                k for _, k, p in rows if p["v_fired"] != p["h_fired"]
                and "initial_state:off" in semantic_features(autos[k]["bound"])}),
            "one_engine_only_pairs": sum(p["v_fired"] != p["h_fired"] for _, _, p in rows)}


def main(argv: list[str]) -> int:
    analysis_path, r6c_path, sweep_path, out_path = argv
    out = json.loads(Path(analysis_path).read_text())
    autos = json.loads(Path(sweep_path).read_text())["automations"]
    out["beside_r6c_fixed_engine (A15.9)"] = beside_r6c(json.loads(Path(r6c_path).read_text()), autos)
    Path(out_path).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
