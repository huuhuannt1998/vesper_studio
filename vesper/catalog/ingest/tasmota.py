"""The Tasmota device templates, from the blakadder/templates repository (EPL-2.0) at a pinned commit
(security-lab spec §3.3): GitHub's tarball of that commit, kept with its SHA-256."""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from vesper.catalog.ingest._http import fetch
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

TARBALL = "https://codeload.github.com/blakadder/templates/tar.gz/{commit}"


def sync_tasmota(raw_root: Path, client: httpx.Client, version: str | None = None) -> Path:
    commit = version or SOURCES["tasmota"].version
    tarball = fetch(client, TARBALL.format(commit=commit)).content
    return write_snapshot(raw_root, "tasmota", commit, {"templates.tgz": tarball}, license=SOURCES["tasmota"].license,
                          origin=f"{SOURCES['tasmota'].origin}@{commit}",
                          extra={"sha256": hashlib.sha256(tarball).hexdigest()})
