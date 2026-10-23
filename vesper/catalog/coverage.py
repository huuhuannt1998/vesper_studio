"""Coverage per protocol, category and fidelity level (spec §1 success criterion 1, §4 fidelity report)."""
from __future__ import annotations

import csv
import io

from vesper.catalog.normalize.common import LEVELS


def _row() -> dict:
    return {"total": 0, **{lv: 0 for lv in LEVELS}}


def coverage(records: list[dict]) -> dict:
    out: dict = {}
    for r in records:
        protocol = r["id"].split(":", 1)[0]
        top = out.setdefault(protocol, {**_row(), "by_category": {}, "l1_fail_reasons": {}})
        cat = top["by_category"].setdefault(r["identity"]["category"], _row())
        reached = r["fidelity"]["reached"]
        for row in (top, cat):
            row["total"] += 1
            for lv in LEVELS:
                row[lv] += bool(reached[lv])
        if not reached["L1"]:
            why = r["fidelity"]["reasons"].get("L1", "unspecified").split(":", 1)[0]
            top["l1_fail_reasons"][why] = top["l1_fail_reasons"].get(why, 0) + 1
    return out


def coverage_csv(cov: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["protocol", "category", "total", *LEVELS])
    for protocol in sorted(cov):
        w.writerow([protocol, "ALL", cov[protocol]["total"], *(cov[protocol][lv] for lv in LEVELS)])
        for cat in sorted(cov[protocol]["by_category"]):
            row = cov[protocol]["by_category"][cat]
            w.writerow([protocol, cat, row["total"], *(row[lv] for lv in LEVELS)])
    return buf.getvalue()
