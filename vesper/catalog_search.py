"""Finding a product to place in a home (spec §7): `vesper catalog search` and `vesper catalog show`.

Every word of a query must appear in a record's id, vendor, model, description or category (case-insensitive).
The products modelled to the highest fidelity level come first: they are the ones the twins reproduce best.
It lives outside `vesper/catalog/` on purpose: the catalog's code fingerprint covers that package, and searching
must not make a built catalog stale.
"""
from __future__ import annotations

LEVELS = ("L0", "L1", "L2", "L3", "L4")


def fidelity_level(record: dict) -> int:
    """The highest level reached, counting up from L0 without gaps (-1 if not even L0)."""
    reached = record.get("fidelity", {}).get("reached", {})
    level = -1
    for k, name in enumerate(LEVELS):
        if not reached.get(name):
            break
        level = k
    return level


def _text(record: dict) -> str:
    i = record.get("identity", {})
    return " ".join(str(x) for x in (record["id"], i.get("vendor"), i.get("model"), i.get("description"),
                                     i.get("category")) if x).lower()


def search(records: dict[str, dict], query: str, category: str | None = None) -> list[dict]:
    words = query.lower().split()
    found = [r for r in records.values() if all(w in _text(r) for w in words)
             and (category is None or r.get("identity", {}).get("category") == category)]
    return sorted(found, key=lambda r: (-fidelity_level(r), r["id"]))


def describe(record: dict) -> str:
    i = record.get("identity", {})
    level = fidelity_level(record)
    return (f"{record['id']}  {i.get('vendor') or '?'} {i.get('model') or '?'}  ({i.get('category') or 'other'}, "
            f"{LEVELS[level] if level >= 0 else 'no level'})")


def show(record: dict) -> list[str]:
    """A product's identity, fidelity and features, as `vesper catalog show` prints them."""
    i = record.get("identity", {})
    lines = [describe(record)]
    if i.get("description"):
        lines.append(f"  {i['description']}")
    reasons = record.get("fidelity", {}).get("reasons", {})
    for name in LEVELS:
        if name in reasons:
            lines.append(f"  {name}: {reasons[name]}")
    lines.append("  features:")
    for f in record.get("features", []):
        extra = f.get("range") or f.get("values") or ""
        access = "/".join(f.get("access", []))
        lines.append(f"    {f['name']}  {f['type']}{' ' + str(extra) if extra else ''}  [{access}]"
                     + (f"  ({f['unit']})" if f.get("unit") else ""))
    return lines
