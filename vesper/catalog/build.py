"""`vesper catalog build`: snapshots -> validated VDS catalog, cross-protocol links and coverage (spec §2).

Reads snapshots only, never the network, so the same snapshots always give byte-identical output.
A source that was never synced is skipped and reported; a snapshot that fails its hash check fails the
whole build before any output is written.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from vesper.catalog.coverage import coverage, coverage_csv
from vesper.catalog.errors import CatalogError
from vesper.catalog.fingerprint import code_fingerprint
from vesper.catalog.ingest.ha import load_vocabulary
from vesper.catalog.ingest.matter_zap import parse_zap
from vesper.catalog.normalize.ha_map import unknown_device_classes
from vesper.catalog.normalize.matter import matter_records
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.catalog.normalize.zwave import zwave_records
from vesper.catalog.schema import validate_vds
from vesper.catalog.snapshot import load_snapshot

PROTOCOLS = ("zigbee", "matter", "zwave")


def _input(meta: dict) -> dict:
    """Snapshot version plus a digest of its manifest, so a re-sync under the same version is visible."""
    digest = hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()
    return {"version": meta["version"], "manifest_sha256": digest}


def _zigbee(raw_root):
    meta, files = load_snapshot(raw_root, "zigbee")
    return zigbee_records(json.loads(files["definitions.json"])), [], {"zigbee": _input(meta)}


def _matter(raw_root):
    dcl_meta, dcl = load_snapshot(raw_root, "dcl")
    zap_meta, zap = load_snapshot(raw_root, "matter_zap")
    recs = matter_records(json.loads(dcl["models.json"]), json.loads(dcl["vendors.json"]), parse_zap(zap),
                          dcl_version=dcl_meta["version"], zap_version=zap_meta["version"])
    return recs, [], {"dcl": _input(dcl_meta), "matter_zap": _input(zap_meta)}


def _zwave(raw_root):
    meta, files = load_snapshot(raw_root, "zwave")
    recs, rejected = zwave_records(files["config.tgz"], meta["version"])
    return recs, rejected, {"zwave": _input(meta)}


PRODUCERS = {"zigbee": _zigbee, "matter": _matter, "zwave": _zwave}
REQUIRED_SOURCES = {"zigbee": ("zigbee",), "matter": ("dcl", "matter_zap"), "zwave": ("zwave",)}


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _protocol(r: dict) -> str:
    return r["id"].split(":", 1)[0]


def link_products(records: list[dict]) -> list[list[str]]:
    """Cross-reference records of one product in different protocols (exact, case-folded vendor and model).

    Records are never merged: each keeps its own definition, features, ids and sources, so a
    redistributable record is never tainted by a non-redistributable one (spec §5).
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        identity = r["identity"]
        groups.setdefault((identity["vendor"].strip().casefold(), identity["model"].strip().casefold()), []).append(r)
    links = []
    for group in groups.values():
        if len({_protocol(r) for r in group}) < 2:
            continue
        for r in group:
            r["identity"]["same_product_as"] = sorted(o["id"] for o in group if _protocol(o) != _protocol(r))
        links.append(sorted(r["id"] for r in group))
    return sorted(links)


def _release_only(records: list[dict], links: list[list[str]], rejected: list[dict]):
    """Drop records that use a non-redistributable source, and every reference to them."""
    records = [r for r in records if all(s["redistributable"] for s in r["sources"])]
    kept = {r["id"]: _protocol(r) for r in records}
    for r in records:
        others = [i for i in r["identity"].get("same_product_as", []) if i in kept]
        if others:
            r["identity"]["same_product_as"] = others
        else:
            r["identity"].pop("same_product_as", None)
    links = [g for g in ([i for i in g if i in kept] for g in links) if len({kept[i] for i in g}) > 1]
    rejected = [x for x in rejected if x.get("redistributable", True)]
    return records, links, rejected


def build_catalog(raw_root: Path, out_dir: Path, *, release: bool = False) -> dict:
    raw_root, out_dir = Path(raw_root), Path(out_dir)
    missing = unknown_device_classes(load_vocabulary(raw_root))
    if missing:
        raise CatalogError("the crosswalk uses device classes the pinned Home Assistant lacks: " + ", ".join(missing))
    records, rejected, skipped = [], [], {}
    inputs = {"ha": _input(load_snapshot(raw_root, "ha")[0])}
    for name, produce in PRODUCERS.items():
        never_synced = [s for s in REQUIRED_SOURCES[name] if not (raw_root / s / "CURRENT").is_file()]
        if never_synced:
            skipped[name] = (f"no snapshot for source {never_synced[0]!r}; "
                             f"run `vesper catalog sync --source {never_synced[0]}`")
            continue
        recs, bad, versions = produce(raw_root)  # a damaged snapshot raises and fails the build
        inputs.update(versions)
        rejected.extend(bad)
        for r in recs:
            errors = validate_vds(r)
            if errors:
                rejected.append({"id": r.get("id"), "errors": errors[:5],
                                 "redistributable": all(s.get("redistributable") for s in r.get("sources", []))})
            else:
                records.append(r)
    links = link_products(records)
    if release:
        records, links, rejected = _release_only(records, links, rejected)
    out_dir.mkdir(parents=True, exist_ok=True)
    for protocol in PROTOCOLS:  # always rewrite every file, so a skipped source never leaves a stale one
        group = sorted((r for r in records if r["id"].startswith(protocol + ":")), key=lambda r: r["id"])
        _write(out_dir / f"{protocol}.json", json.dumps(group, sort_keys=True, ensure_ascii=False) + "\n")
    cov = coverage(records)
    _write(out_dir / "coverage.json", json.dumps(cov, indent=2, sort_keys=True) + "\n")
    _write(out_dir / "coverage.csv", coverage_csv(cov))
    report = {"records": len(records), "rejected": rejected, "skipped_sources": skipped, "linked": links,
              "release": release, "inputs": inputs, "code_sha256": code_fingerprint()}
    _write(out_dir / "build_report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
