"""Protocol Addendum 11 (A11.2): the engine-corrected rung-4 sweep beside 8fcd672's main run.

Usage: python scripts/revision/r6d_compare.py LAB_DIR NEW_COMMIT [--out results/edt/r6d-<commit7>.json]

Reads LAB_DIR/sweep-rung4-8fcd672.json (the published main run) and LAB_DIR/sweep-rung4-r6d-NEW_COMMIT.json, and writes
aggregates only: counts, kinds and the defects' reach. Never automation identities or text.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

CLOCK_KEY, FRAME_KEY = "divergences", "frame_divergences"
MAIN = "8fcd672"


def ran(result: dict) -> dict[str, dict]:
    """Run automations: those with an outcome in some environment."""
    return {i: a for i, a in result["automations"].items() if a.get("outcomes")}


def flags(autos: dict[str, dict], key: str) -> dict[str, list[str]]:
    return {i: sorted(a.get(key) or []) for i, a in autos.items() if a.get(key)}


def dead(autos: dict[str, dict]) -> int:
    """Environment-coupled run automations (an outcome kind) that fire in no environment (390 at 8fcd672)."""
    return sum(1 for a in autos.values() if a.get("kinds") and not any(o.get("fired") for o in a["outcomes"].values()))


def reach(a: dict | None) -> str:
    """Which of Addendum 10's defects an automation could be touched by: `sun.sun`, `kelvin`/`color_temp`, or other."""
    text = json.dumps((a or {}).get("bound"), sort_keys=True)
    if "sun.sun" in text:
        return "sun.sun"
    if '"kelvin"' in text or '"color_temp"' in text:
        return "kelvin/color_temp"
    return "other"


def compare(old: dict, new: dict) -> dict:
    ro, rn = ran(old), ran(new)
    out: dict = {
        "bound": {"8fcd672": sum(1 for a in old["automations"].values() if a.get("bound")),
                  "r6d": sum(1 for a in new["automations"].values() if a.get("bound"))},
        "run": {"8fcd672": len(ro), "r6d": len(rn)},
        "negative_control": {"8fcd672": old.get("negative_control"), "r6d": new.get("negative_control")},
        "dead_environment_coupled": {"8fcd672": dead(ro), "r6d": dead(rn)},
    }
    for name, key, top in (("clock_relation", CLOCK_KEY, "flagged"), ("frame_relation", FRAME_KEY, "flagged_frame")):
        fo, fn = flags(ro, key), flags(rn, key)
        same_kinds = sum(1 for i in fo if i in fn and fn[i] == fo[i])
        other_kinds = sum(1 for i in fo if i in fn and fn[i] != fo[i])
        lost, new_ = [i for i in fo if i not in fn], [i for i in fn if i not in fo]
        rel = {
            "flags": {"8fcd672": old[top]["n"], "r6d": new[top]["n"]},
            "by_kind": {"8fcd672": old[top]["by_kind"], "r6d": new[top]["by_kind"]},
            "held_same_kinds": same_kinds, "held_other_kinds": other_kinds,
            "lost": len(lost), "new": len(new_),
            "lost_by_reach": dict(Counter(reach(old["automations"].get(i)) for i in lost)),
            "new_by_reach": dict(Counter(reach(new["automations"].get(i)) for i in new_)),
            "other_kinds_by_reach": dict(Counter(reach(old["automations"].get(i))
                                                 for i in fo if i in fn and fn[i] != fo[i])),
        }
        ts_o = {i for i, k in fo.items() if "time_spread" in k}
        ts_n = {i for i, k in fn.items() if "time_spread" in k}
        rel["time_spread"] = {"8fcd672": len(ts_o), "r6d": len(ts_n), "held": len(ts_o & ts_n)}
        out[name] = rel
    out["reach_among_run_8fcd672"] = dict(Counter(reach(a) for a in ro.values()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lab", type=Path)
    ap.add_argument("commit")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    old = json.loads((args.lab / f"sweep-rung4-{MAIN}.json").read_text())
    new = json.loads((args.lab / f"sweep-rung4-r6d-{args.commit}.json").read_text())
    report = {"protocol": "docs/experiments/edt-protocol.md, Addendum 11", "commit": args.commit, **compare(old, new)}
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
