"""`vesper catalog build`: snapshots -> validated, deduplicated VDS catalog plus coverage (spec §2).

Reads snapshots only, never the network, so the same snapshots always give byte-identical output.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from vesper.catalog.coverage import coverage, coverage_csv
from vesper.catalog.errors import CatalogError, SnapshotError
from vesper.catalog.ingest.ha import load_vocabulary
from vesper.catalog.ingest.matter_zap import parse_zap
from vesper.catalog.normalize.common import slug
from vesper.catalog.normalize.ha_map import unknown_device_classes
from vesper.catalog.normalize.matter import matter_records
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.catalog.normalize.zwave import zwave_records
from vesper.catalog.schema import validate_vds
from vesper.catalog.snapshot import load_snapshot

PROTOCOLS = ("zigbee", "matter", "zwave")


def _zigbee(raw_root):
    meta, files = load_snapshot(raw_root, "zigbee")
    return zigbee_records(json.loads(files["definitions.json"])), [], {"zigbee": meta["version"]}


def _matter(raw_root):
    dcl_meta, dcl = load_snapshot(raw_root, "dcl")
    zap_meta, zap = load_snapshot(raw_root, "matter_zap")
    recs = matter_records(json.loads(dcl["models.json"]), json.loads(dcl["vendors.json"]), parse_zap(zap),
                          dcl_version=dcl_meta["version"], zap_version=zap_meta["version"])
    return recs, [], {"dcl": dcl_meta["version"], "matter_zap": zap_meta["version"]}


def _zwave(raw_root):
    meta, files = load_snapshot(raw_root, "zwave")
    recs, rejected = zwave_records(files["config.tgz"], meta["version"])
    return recs, rejected, {"zwave": meta["version"]}


PRODUCERS = {"zigbee": _zigbee, "matter": _matter, "zwave": _zwave}


def _rank(r: dict) -> tuple[bool, bool]:
    reached = r["fidelity"]["reached"]
    return reached["L1"], reached["L0"]


def _merge(primary: dict, secondary: dict) -> dict:
    """`primary` plus the other record's protocols, ids, bindings and sources."""
    m = copy.deepcopy(primary)
    identity = m["identity"]
    for p in secondary["identity"]["protocols"]:
        if p not in identity["protocols"]:
            identity["protocols"].append(p)
    for key, values in secondary["identity"]["ids"].items():
        current = identity["ids"].setdefault(key, [])
        current.extend(copy.deepcopy(v) for v in values if v not in current)
    for key, binding in secondary["binding"].items():
        m["binding"].setdefault(key, copy.deepcopy(binding))
    for s in secondary["sources"]:
        if s not in m["sources"]:
            m["sources"].append(dict(s))
    prov = m["fidelity"]["provenance"]
    prov["merged_from"] = ", ".join(filter(None, [prov.get("merged_from"), secondary["id"]]))
    return m


def _mergeable(a: dict, b: dict) -> bool:
    """Two certified Matter models (distinct vid/pid) stay separate even when they share a name:
    vendors reuse names such as "Air Conditioner" across many different models."""
    return not ("matter_vid_pid" in a["identity"]["ids"] and "matter_vid_pid" in b["identity"]["ids"])


def dedupe(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge records for the same (vendor, model); then make every id unique. Returns (records, merges)."""
    by_key: dict[tuple[str, str], list[int]] = {}
    out: list[dict] = []
    merges = []
    for r in records:
        slots = by_key.setdefault((slug(r["identity"]["vendor"]), slug(r["identity"]["model"])), [])
        i = next((j for j in slots if _mergeable(out[j], r)), None)
        if i is None:
            slots.append(len(out))
            out.append(r)
            continue
        keep, drop = (out[i], r) if _rank(out[i]) >= _rank(r) else (r, out[i])
        out[i] = _merge(keep, drop)
        merges.append({"kept": keep["id"], "merged": drop["id"]})
    seen: dict[str, int] = {}
    for r in out:
        n = seen.get(r["id"], 0)
        seen[r["id"]] = n + 1
        if n:
            r["id"] = f"{r['id']}-{n + 1}"
    return out, merges


def build_catalog(raw_root: Path, out_dir: Path, *, release: bool = False) -> dict:
    raw_root, out_dir = Path(raw_root), Path(out_dir)
    missing = unknown_device_classes(load_vocabulary(raw_root))
    if missing:
        raise CatalogError("the crosswalk uses device classes the pinned Home Assistant lacks: " + ", ".join(missing))
    records, rejected, skipped, inputs = [], [], {}, {}
    for name, produce in PRODUCERS.items():
        try:
            recs, bad, versions = produce(raw_root)
        except SnapshotError as e:
            skipped[name] = str(e)
            continue
        inputs.update(versions)
        rejected.extend(bad)
        for r in recs:
            errors = validate_vds(r)
            if errors:
                rejected.append({"id": r.get("id"), "errors": errors[:5]})
            else:
                records.append(r)
    records, merges = dedupe(records)
    if release:
        records = [r for r in records if all(s["redistributable"] for s in r["sources"])]
    out_dir.mkdir(parents=True, exist_ok=True)
    for protocol in PROTOCOLS:  # always rewrite every file, so a skipped source never leaves a stale one
        group = sorted((r for r in records if r["id"].startswith(protocol + ":")), key=lambda r: r["id"])
        (out_dir / f"{protocol}.json").write_text(json.dumps(group, sort_keys=True, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    cov = coverage(records)
    (out_dir / "coverage.json").write_text(json.dumps(cov, indent=2, sort_keys=True) + "\n")
    (out_dir / "coverage.csv").write_text(coverage_csv(cov))
    report = {"records": len(records), "rejected": rejected, "skipped_sources": skipped, "merged": merges,
              "release": release, "inputs": inputs}
    (out_dir / "build_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
