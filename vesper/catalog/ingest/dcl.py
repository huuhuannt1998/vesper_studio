"""Certified Matter products from the CSA Distributed Compliance Ledger REST API (spec §2).

The ledger is live, so a snapshot is versioned by the chain height at sync time. Its reuse terms are
not yet verified, so DCL-derived records stay out of release builds (`sources.SOURCES["dcl"]`).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch_json
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

BASE = "https://on.dcl.csa-iot.org"
MODELS = "/dcl/model/models"
VENDORS = "/dcl/vendorinfo/vendors"
LATEST_BLOCK = "/cosmos/base/tendermint/v1beta1/blocks/latest"


def fetch_all(client: httpx.Client, path: str, list_key: str, *, page_size: int = 100,
              max_pages: int = 1000) -> list[dict]:
    """Every record behind a paginated DCL endpoint; refuses a partial result."""
    items: list[dict] = []
    key, total = None, None
    for _ in range(max_pages):
        params = {"pagination.limit": str(page_size)}
        if key:
            params["pagination.key"] = key
        else:
            params["pagination.count_total"] = "true"
        page = fetch_json(client, BASE + path, params=params)
        pagination = page.get("pagination") or {}
        if total is None:
            if "total" not in pagination:
                raise IngestError(f"{path}: the ledger did not report a total; refusing a possibly partial read")
            total = int(pagination["total"])
        items.extend(page.get(list_key) or [])
        key = pagination.get("next_key")
        if not key:
            break
    else:
        raise IngestError(f"{path}: pagination did not end after {max_pages} pages")
    if len(items) != total:
        raise IngestError(f"{path}: received {len(items)} records but the ledger reports {total}")
    return items


def sync_dcl(raw_root: Path, client: httpx.Client) -> Path:
    block = fetch_json(client, BASE + LATEST_BLOCK)
    try:
        height = int(block["block"]["header"]["height"])
    except (KeyError, TypeError, ValueError) as e:
        raise IngestError(f"{BASE + LATEST_BLOCK}: unexpected reply, no block height ({e!r})") from e
    models = fetch_all(client, MODELS, "model")
    vendors = fetch_all(client, VENDORS, "vendorInfo")
    if not models:
        raise IngestError("the DCL returned no models")
    if not vendors:  # without vendor names every Matter product would silently fall below L0
        raise IngestError("the DCL returned no vendors")
    files = {"models.json": json.dumps(models, sort_keys=True).encode(),
             "vendors.json": json.dumps(vendors, sort_keys=True).encode()}
    return write_snapshot(raw_root, "dcl", f"height-{height}", files, license=SOURCES["dcl"].license,
                          origin=SOURCES["dcl"].origin,
                          extra={"models": len(models), "vendors": len(vendors), "block_height": height})
