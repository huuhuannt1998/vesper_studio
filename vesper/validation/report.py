"""The fidelity report (spec §4): what a researcher cites about how faithful their virtual devices are.

It combines the catalog (sources, coverage per level and category, every `assumed` field) with the latest
result of every check under results/. A check that has not run is shown as not run, never as passed, and
L2 is credited only to products that passed acceptance.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from vesper.exposure.ha_discovery import device_discovery
from vesper.twins.latency import PATHS

KINDS = ("discovery", "roundtrip", "acceptance", "rules", "latency")


def latest(results_dir: Path, kind: str) -> tuple[str, dict] | None:
    files = sorted(Path(results_dir).glob(f"{kind}-*.json"))
    return (files[-1].name, json.loads(files[-1].read_text())) if files else None


def _records(catalog_dir: Path) -> list[dict]:
    out = []
    for name in ("zigbee", "matter", "zwave"):
        path = Path(catalog_dir) / f"{name}.json"
        if path.is_file():
            out.extend(json.loads(path.read_text()))
    return out


def _summary(kind: str, data: dict) -> dict:
    if kind == "acceptance":
        return {"passed": data["summary"]["passed"] == data["summary"]["products"], **{
            k: data["summary"][k] for k in ("products", "passed", "categories_passing", "categories_displayed")}}
    if kind == "roundtrip":
        return {"passed": bool(data.get("complete")) and not data.get("different"),
                **{k: data.get(k) for k in ("checked", "identical", "l1_with_diffs", "missing", "duplicates")}}
    if kind == "discovery":
        s = data["summary"]
        return {"passed": s["accepted"] == s["components"], **s}
    if kind == "rules":
        return {k: data.get(k) for k in ("passed", "products", "features_checked", "reports", "violation_count")}
    return {"passed": data.get("passed"), "overhead_median_s": data.get("overhead", {}).get("median_s"),
            "paths": {n: {k: r[k] for k in ("median_s", "published_median_s", "ratio_ci90", "T3_coverage", "passed")}
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
        checks[kind] = None if found is None else {"file": found[0], **_summary(kind, found[1])}
    acceptance = latest(results_dir, "acceptance")
    validated = sorted(p["id"] for p in acceptance[1]["products"] if p["passed"]) if acceptance else []
    eligible = sum(1 for r in records if r["id"].startswith("zigbee:") and r["fidelity"]["reached"]["L1"]
                   and device_discovery(r, ieee="0x" + "0" * 16, friendly_name="x")[2])
    return {"sources": sources, "coverage": coverage, "assumed": dict(assumed.most_common()),
            "checks": checks, "l2": {"kind": "z2m-api", "eligible_zigbee": eligible, "validated_products": validated},
            "timing_paths": {n: {"source": p.source, "basis": p.basis, "median_s": p.median()}
                             for n, p in PATHS.items()}}


def render_markdown(report: dict) -> str:
    lines = ["# VESPER fidelity report", "", "## Sources", "",
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
    lines += ["", "## Checks", "", "| Check | Result | File |", "|---|---|---|"]
    for kind, c in report["checks"].items():
        if c is None:
            lines.append(f"| {kind} | not run yet | |")
        else:
            lines.append(f"| {kind} | {'PASS' if c.get('passed') else 'FAIL'} | {c['file']} |")
    lines += ["", "## Timing paths", "", "| Path | Median | Source |", "|---|---|---|"]
    for name, p in report["timing_paths"].items():
        lines.append(f"| {name} | {p['median_s']:.4g} s | {p['source'] or 'assumed: ' + p['basis']} |")
    return "\n".join(lines) + "\n"
