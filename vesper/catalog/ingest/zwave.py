"""Z-Wave JS device configuration database, from the npm package @zwave-js/config (MIT) (spec §2)."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch, fetch_json
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

REGISTRY = "https://registry.npmjs.org/@zwave-js/config/{version}"


def sync_zwave(raw_root: Path, client: httpx.Client, version: str | None = None) -> Path:
    version = version or SOURCES["zwave"].version
    dist = fetch_json(client, REGISTRY.format(version=version))["dist"]
    tarball = fetch(client, dist["tarball"]).content
    algo, _, expected = dist["integrity"].partition("-")
    actual = base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    if algo != "sha512" or actual != expected:
        raise IngestError(f"@zwave-js/config {version}: tarball does not match the registry integrity hash")
    return write_snapshot(raw_root, "zwave", version, {"config.tgz": tarball}, license=SOURCES["zwave"].license,
                          origin=f"{SOURCES['zwave'].origin}@{version}", extra={"integrity": dist["integrity"]})
