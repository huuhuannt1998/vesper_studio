"""The fidelity report (spec §4): what a researcher cites about how faithful their virtual devices are.

It combines the catalog (sources, coverage per level and category, every `assumed` field) with the latest
result of every check under results/. A check that has not run is shown as not run, never as passed; a
run over nothing does not pass; a run whose parameters differ from the pre-registration, or that does
not record them, is labelled exploratory; and L2 is credited only to products that passed acceptance.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from vesper.catalog.fingerprint import staleness
from vesper.exposure.ha_discovery import device_discovery
from vesper.twins.latency import PATHS
from vesper.validation.realdata import ZIOTP_COMMIT

KINDS = ("discovery", "roundtrip", "acceptance", "rules", "latency", "realdata")
# The pre-registered parameters (docs/experiments/l3-timing-design.md and the run book).
PREREGISTERED = {
    "rules": {"seed": 0, "hours": 4.0},
    "latency": {"seed": 0, "n": 200, "paths": "local,vendor_cloud,trigger_action_cloud", "spacing": 0.05,
                "product": "zigbee:aqara:mccgq11lm"},
    "acceptance": {"seed": 0, "per_category": 5},
}
# Pre-registered values a result carries in its own fields rather than in its command-line parameters.
PREREGISTERED_DATA = {"realdata": {"dataset_commit": ZIOTP_COMMIT, "dataset_dirty": False}}
_RESULT_NAME = re.compile(r"^[a-z]+-\d{8}T\d{6}Z\.json$")


def latest(results_dir: Path, kind: str) -> tuple[str, dict | None] | None:
    """The newest timestamped result of a kind; (name, None) if that file cannot be read."""
    files = sorted(f for f in Path(results_dir).glob(f"{kind}-*.json") if _RESULT_NAME.match(f.name))
    if not files:
        return None
    try:
        return files[-1].name, json.loads(files[-1].read_text())
    except (OSError, ValueError):
        return files[-1].name, None


def _exploratory(kind: str, data: dict) -> bool:
    if kind not in PREREGISTERED and kind not in PREREGISTERED_DATA:
        return False
    params = (data.get("run") or {}).get("parameters")
    return (params is None or any(params.get(k) != v for k, v in PREREGISTERED.get(kind, {}).items())
            or any(data.get(k) != v for k, v in PREREGISTERED_DATA.get(kind, {}).items()))


def _records(catalog_dir: Path) -> list[dict]:
    out = []
    for name in ("zigbee", "matter", "zwave"):
        path = Path(catalog_dir) / f"{name}.json"
        if path.is_file():
            out.extend(json.loads(path.read_text()))
    return out


def _summary(kind: str, data: dict) -> dict:
    if kind == "acceptance":
        s = data["summary"]
        return {**{k: s[k] for k in ("products", "categories_passing", "categories_displayed")},
                "products_passed": s["passed"], "passed": s["products"] > 0 and s["passed"] == s["products"]}
    if kind == "roundtrip":
        return {"passed": bool(data.get("complete")) and not data.get("different") and bool(data.get("checked")),
                **{k: data.get(k) for k in ("checked", "identical", "l1_with_diffs", "missing", "duplicates")}}
    if kind == "discovery":
        s = data["summary"]
        return {**s, "passed": s["components"] > 0 and s["accepted"] == s["components"]}
    if kind == "realdata":
        return {"passed": bool(data.get("passed")), **{k: data.get(k) for k in (
            "judged", "judged_passed", "eligible", "decryption_ok", "rule_window_ok", "dataset_commit")}}
    if kind == "rules":
        return {k: data.get(k) for k in ("passed", "products", "features_checked", "reports", "violation_count",
                                          "r4_checked")}
    return {"passed": bool(data.get("passed")), "error": data.get("error"), "overhead_s": data.get("overhead_s"),
            "generator_passed": all(g.get("passed") for g in data.get("generator", {}).values()),
            "calibration": {k: (data.get("calibration") or {}).get(k) for k in ("median_s", "p95_s", "passed")},
            "paths": {n: {"residual": r["residual"], "end_to_end_median_s": r["described"]["median_s"],
                          "published_median_s": r["described"]["published_median_s"]}
                      for n, r in data.get("paths", {}).items()}}


def build_report(catalog_dir: Path, results_dir: Path) -> dict:
    catalog_dir, results_dir = Path(catalog_dir), Path(results_dir)
    records = _records(catalog_dir)
    coverage = json.loads((catalog_dir / "coverage.json").read_text())
    sources = json.loads((catalog_dir / "build_report.json").read_text()).get("inputs", {})
    assumed = Counter(a for r in records for a in r["fidelity"].get("assumed", []))
    checks = {}
    for kind in KINDS:
        found = latest(results_dir, kind)
        if found is None:
            checks[kind] = None
        elif found[1] is None:
            checks[kind] = {"file": found[0], "unreadable": True, "passed": False}
        else:
            checks[kind] = {"file": found[0], **_summary(kind, found[1]), "exploratory": _exploratory(kind, found[1]),
                            "run": found[1].get("run")}
    acceptance = latest(results_dir, "acceptance")
    validated = sorted(p["id"] for p in acceptance[1]["products"] if p["passed"]) if acceptance and acceptance[1] \
        else []
    eligible = sum(1 for r in records if r["id"].startswith("zigbee:") and r["fidelity"]["reached"]["L1"]
                   and device_discovery(r, ieee="0x" + "0" * 16, friendly_name="x")[2])
    return {"catalog_stale": staleness(catalog_dir), "sources": sources, "coverage": coverage,
            "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated},
            "timing_paths": {n: {"source": p.source, "basis": p.basis, "median_s": p.median()}
                             for n, p in PATHS.items()}}


def _details(kind: str, c: dict) -> str:
    if kind == "rules":
        return (f"{c.get('products')} products, {c.get('reports')} reports, {c.get('violation_count')} violations, "
                f"{c.get('r4_checked')} occupancy clears; the rules are zigbee-herdsman-converters defaults "
                "(assumed)")
    if kind == "latency":
        if c.get("error"):
            return f"failed: {c['error']}"
        parts = [f"overhead {1000 * (c.get('overhead_s') or 0):.1f} ms",
                 f"generator {'PASS' if c.get('generator_passed') else 'FAIL'}"]
        for name, p in c.get("paths", {}).items():
            r = p["residual"]
            parts.append(f"{name}: residual median {1000 * r['median_s']:.1f} ms, P95 {1000 * r['p95_s']:.1f} ms")
        return "; ".join(parts) + "; sleepy_downlink not measured (assumed)"
    if kind == "realdata":
        text = (f"{c.get('judged_passed')}/{c.get('judged')} judged streams reproduced under ZHA's rule "
                f"({c.get('eligible')} eligible; firmware-governed devices described only); decryption "
                f"{'ok' if c.get('decryption_ok') else 'FAILED'}")
        if not c.get("rule_window_ok"):
            text += "; capture outside the dates over which ZHA's rule was verified"
        return text
    if kind == "acceptance":
        return (f"{c.get('products_passed')}/{c.get('products')} products; categories passing "
                f"{len(c.get('categories_passing') or [])}")
    if kind == "roundtrip":
        return f"{c.get('identical')}/{c.get('checked')} identical"
    return f"{c.get('accepted')}/{c.get('components')} components accepted"


def render_markdown(report: dict) -> str:
    lines = ["# VESPER fidelity report", ""]
    if report.get("catalog_stale"):
        lines += [f"**Warning: the catalog is stale ({report['catalog_stale']}); rebuild it with "
                  "`vesper catalog build` before citing these numbers.**", ""]
    lines += ["## Sources", "",
             "| Source | Version | Manifest SHA-256 |", "|---|---|---|"]
    for name, s in sorted(report["sources"].items()):
        lines.append(f"| {name} | {s.get('version', '')} | {str(s.get('manifest_sha256', ''))[:16]}… |")
    lines += ["", "## Coverage", "", "| Protocol | L0 | L1 | L2 (validated) | L3 | L4 |", "|---|---|---|---|---|---|"]
    for proto, c in sorted(report["coverage"].items()):
        l2 = len(report["l2"]["validated_products"]) if proto == "zigbee" else 0
        lines.append(f"| {proto} | {c['L0']} | {c['L1']} | {l2} | {c['L3']} | {c['L4']} |")
    lines += ["", f"L2 ({report['l2']['kind']}) is credited only to products that passed acceptance; "
                  f"{report['l2']['eligible_zigbee']} L1 Zigbee products are eligible (they expose at least one "
                  "Home Assistant entity through the bridge).", "", "## Assumed fields", "",
              "Counted out of coverage (spec §3.2). Most frequent:", "", "| Field | Records |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in list(report["assumed"].items())[:15]]
    lines += ["", "## Checks", "", "| Check | Result | Details | File |", "|---|---|---|---|"]
    for kind, c in report["checks"].items():
        if c is None:
            lines.append(f"| {kind} | not run yet | | |")
            continue
        if c.get("unreadable"):
            lines.append(f"| {kind} | unreadable | the result file could not be read | {c['file']} |")
            continue
        result = ("PASS" if c.get("passed") else "FAIL") + (" (exploratory)" if c.get("exploratory") else "")
        lines.append(f"| {kind} | {result} | {_details(kind, c)} | {c['file']} |")
    lines += ["", "## Timing paths", "", "| Path | Median | Source |", "|---|---|---|"]
    for name, p in report["timing_paths"].items():
        lines.append(f"| {name} | {p['median_s']:.4g} s | {p['source'] or 'assumed: ' + p['basis']} |")
    return "\n".join(lines) + "\n"
