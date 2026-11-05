"""Revision R9-R (protocol Addendum 20): the re-triggering stratum's aggregates, from the runs' outputs.

`python scripts/revision/r9r_results.py --scratch DIR --sweep-8fcd672 FILE --sweep-db19e34 FILE --out FILE`, where DIR
holds `stratum-{before,after}.json`, `reach-{before,after}.json` and `flags-{before,after}.json` (written by
`python -m vesper.validation.ha_retrigger`). The stratum's 8 automations are synthetic, so their records are kept in
full; the sweep's automations appear only as counts and anonymous indices.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from vesper.validation import ha_diff, ha_retrigger

MEASURES = ("fired", "n_firings", "instants", "calls", "sequence", "starts", "cancels", "outcome")


def static_reach(sweep: dict) -> dict:
    """Per mode: run automations, those firing somewhere, those with clock and with frame flags (A20.6, static)."""
    out: dict[str, Counter] = {}
    for a in sweep["automations"].values():
        if not (a.get("bound") and a.get("outcomes")):
            continue
        mode = a["bound"].get("mode", "single")
        c = out.setdefault(mode, Counter())
        c["run"] += 1
        c["fire_somewhere"] += any((o or {}).get("fired") for o in a["outcomes"].values())
        c["clock_flags"] += bool(a.get("divergences"))
        c["frame_flags"] += bool(a.get("frame_divergences"))
    return {m: dict(c) for m, c in sorted(out.items())}


def stratum_summary(res: dict) -> dict:
    per = {}
    for k, p in res["per"].items():
        per[k] = {"mode": p["mode"], "max": p["max"], "agrees": p["agrees"],
                  "differs_in": [m for m in MEASURES if not p["equal"][m]], "vesper": p["v"], "home_assistant": p["h"],
                  "approximations": p["approximations"], "vesper_refused": p["vesper_refused"],
                  "ha_unavailable": p["ha_unavailable"]}
    return {"engine_commit": res["engine_commit"], "ha_version": res["ha_version"], "env": res["env"],
            "trace": res["trace"], "agree": res["agree"], "n": res["n"], "per": per}


def flags_summary(sweep: dict, res: dict, envs: list[str] | None = None) -> list[dict]:
    sub = res if envs is None else {**res, "envs": [e for e in res["envs"] if e in envs]}
    return ha_retrigger.flags_analysis(sweep, sub)["flags"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--sweep-8fcd672", required=True)
    ap.add_argument("--sweep-db19e34", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    d = Path(args.scratch)
    load = lambda name: json.loads((d / name).read_text())  # noqa: E731
    s8 = json.loads(Path(args.sweep_8fcd672).read_text())
    sdb = json.loads(Path(args.sweep_db19e34).read_text())
    before, after = load("reach-before.json"), load("reach-after.json")
    four = [e.key for e in ha_diff.ENVS]
    out = {
        "addendum": 20,
        "stratum": {"before_fix": stratum_summary(load("stratum-before.json")),
                    "after_fix": stratum_summary(load("stratum-after.json"))},
        "reach_static": {"8fcd672": static_reach(s8), "db19e34": static_reach(sdb)},
        "reach_dynamic": {"sweep": "db19e34", "engine_before": before["engine_commit"],
                          "engine_after": after["engine_commit"], "automations": len(before["keys"]),
                          "environments": len(before["envs"]), "gate_before_vs_sweep": before["gate_vs_sweep"],
                          "gate_after_vs_sweep": after["gate_vs_sweep"],
                          **ha_retrigger.reach_analysis(sdb, before, after)},
        "restart_flags": {}}
    for tag in ("before", "after"):
        res = load(f"flags-{tag}.json")
        out["restart_flags"][f"{tag}_fix"] = {"engine_commit": res["engine_commit"],
                                              "four_environments": flags_summary(sdb, res, four),
                                              "all_environments": flags_summary(sdb, res)}
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
