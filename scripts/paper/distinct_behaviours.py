"""How many distinct twin behaviours the catalog's L1 products make (manuscript, Q1): two products count as one when
their twins would behave alike, the same features (name, type, range or values, unit, access, Home Assistant mapping)
and the same reporting rules. Prints a table per protocol and category.

    python scripts/paper/distinct_behaviours.py [catalog/vds]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def signature(r: dict) -> str:
    feats = sorted(json.dumps({k: f.get(k) for k in ("name", "type", "range", "values", "unit", "access", "ha",
                                                        "value_on", "value_off")}, sort_keys=True)
                   for f in r.get("features") or [] if f.get("type") != "unsupported")
    rules = json.dumps(((r.get("behavior") or {}).get("reporting") or {}), sort_keys=True)
    return json.dumps([feats, rules])


def main(catalog: Path) -> None:
    out = {}
    for proto in ("zigbee", "matter", "tasmota"):
        recs = [r for r in json.loads((catalog / f"{proto}.json").read_text()) if r["fidelity"]["reached"]["L1"]]
        by_cat = defaultdict(set)
        for r in recs:
            by_cat[r["identity"].get("category") or "other"].add(signature(r))
        total = len({signature(r) for r in recs})
        out[proto] = {"l1_products": len(recs), "distinct": total,
                      "by_category": {c: len(s) for c, s in sorted(by_cat.items())}}
        print(f"{proto}: {len(recs)} L1 products, {total} distinct behaviours")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "catalog/vds"))
