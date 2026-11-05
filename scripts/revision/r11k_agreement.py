"""Protocol Addendum 24: agreement between the binder audits' first rater and a blind second rater.

Usage: python scripts/revision/r11k_agreement.py LAB_DIR [--out results/edt/r11k-<commit7>.json]

Reads the local, never-committed label files under LAB_DIR (audit-r4c/labels-flagged.tsv, labels-dead.tsv,
r8-X/labels-sunloc.tsv, r11-rater/rater2-*.tsv) and writes aggregates only: counts, raw agreement, Cohen's kappa,
Wilson intervals. No case id, label or note leaves the lab directory.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def read_labels(path: Path, column: int) -> dict[str, str]:
    """id12 -> the label in `column` (0-based, the id being column 0); comment lines skipped."""
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) > column:
            out[cells[0].strip()] = cells[column].strip()
    return out


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa over paired labels; None when undefined (no pairs, or chance agreement 1)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def compare(first: dict, second: dict, binary=None, drop=("uncertain",)) -> dict:
    """Raw agreement and kappa on the full label, and on `binary` (a label -> bool map) with `drop` removed."""
    ids = sorted(set(first) & set(second))
    full = [(first[i], second[i]) for i in ids]
    out = {"n": len(ids), "agree": sum(a == b for a, b in full), "kappa": kappa(full)}
    if binary:
        kept = [(binary(a), binary(b)) for a, b in full if a not in drop and b not in drop]
        out["binary"] = {"n": len(kept), "agree": sum(a == b for a, b in kept), "kappa": kappa(kept)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lab", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    lab, r2 = a.lab, a.lab / "r11-rater"
    report: dict = {"protocol": "docs/experiments/edt-protocol.md, Addendum 24"}

    b1, b2 = read_labels(lab / "audit-r4c/labels-flagged.tsv", 1), read_labels(r2 / "rater2-binder.tsv", 1)
    c1, c2 = read_labels(lab / "audit-r4c/labels-flagged.tsv", 2), read_labels(r2 / "rater2-binder.tsv", 2)
    report["binder"] = compare(b1, b2, binary=lambda x: x == "misbind")
    both = [i for i in set(b1) & set(b2) if b1[i] == b2[i] == "misbind"]
    report["binder"]["misbind_class_agree"] = {"n": len(both), "agree": sum(c1.get(i) == c2.get(i) for i in both)}
    sample = sorted(set(b1) & set(b2))
    for who, lab_ in (("first", b1), ("second", b2)):
        k = sum(lab_[i] == "misbind" for i in sample)
        m = sum(lab_[i] != "uncertain" for i in sample)
        report["binder"][f"{who}_misbind_share"] = {"misbind": k, "decided": m, "wilson": wilson(k, m)}

    d1, d2 = read_labels(lab / "audit-r4c/labels-dead.tsv", 1), read_labels(r2 / "rater2-dead.tsv", 1)
    report["dead"] = compare(d1, d2, binary=lambda x: x.startswith("G"), drop=())

    s1, s2 = read_labels(lab / "r8-X/labels-sunloc.tsv", 1), read_labels(r2 / "rater2-sunloc.tsv", 1)
    report["sunloc"] = compare(s1, s2, binary=lambda x: x == "located")
    sample = sorted(set(s1) & set(s2))
    for who, lab_ in (("first", s1), ("second", s2)):
        k = sum(lab_[i] == "located" for i in sample)
        m = sum(lab_[i] != "uncertain" for i in sample)
        report["sunloc"][f"{who}_location_precision"] = {"located": k, "decided": m, "wilson": wilson(k, m)}

    text = json.dumps(report, indent=1, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
