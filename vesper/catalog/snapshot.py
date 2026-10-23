"""Pinned, hashed snapshots of raw source data under catalog/raw/<source>/<version>/ (spec §2, §5 Errors).

A snapshot is written into a hidden partial directory and swapped into place only when complete, and
`CURRENT` is replaced last, so an interrupted sync never damages the previous good snapshot, even when
it re-syncs the same version.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from vesper.catalog.errors import SnapshotError

_SAFE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_snapshot(
    raw_root: Path, source: str, version: str, files: dict[str, bytes], *,
    license: str, origin: str, extra: dict | None = None,
) -> Path:
    """Write `files` and a manifest of their SHA-256 hashes, then point CURRENT at `version`."""
    for part in (source, version):
        if not _SAFE.match(part) or part in {".", ".."}:
            raise SnapshotError(f"unsafe snapshot path component: {part!r}")
    for name in files:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or not name:
            raise SnapshotError(f"unsafe file name in snapshot: {name!r}")
    base = Path(raw_root) / source
    final, pid = base / version, os.getpid()
    partial, old = base / f".{version}.partial-{pid}", base / f".{version}.old-{pid}"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    try:
        hashes = {}
        for name, data in sorted(files.items()):
            target = partial / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            hashes[name] = _sha256(data)
        meta = {
            **(extra or {}),
            "source": source, "version": version, "license": license, "origin": origin,
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": hashes,
        }
        (partial / "snapshot.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if final.exists():
        final.rename(old)
    partial.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    current = base / f".CURRENT.tmp-{pid}"
    current.write_text(version + "\n")
    os.replace(current, base / "CURRENT")
    return final


def current_version(raw_root: Path, source: str) -> str:
    f = Path(raw_root) / source / "CURRENT"
    if not f.is_file():
        raise SnapshotError(f"no snapshot for source {source!r}; run `vesper catalog sync --source {source}`")
    return f.read_text().strip()


def load_snapshot(raw_root: Path, source: str, version: str | None = None) -> tuple[dict, dict[str, bytes]]:
    """Return (manifest, files), refusing any file whose bytes no longer match the manifest."""
    version = version or current_version(raw_root, source)
    d = Path(raw_root) / source / version
    manifest = d / "snapshot.json"
    if not manifest.is_file():
        raise SnapshotError(f"snapshot manifest missing: {manifest}")
    meta = json.loads(manifest.read_text())
    files = {}
    for name, digest in meta["files"].items():
        p = d / name
        if not p.is_file():
            raise SnapshotError(f"snapshot file missing: {p}")
        data = p.read_bytes()
        if _sha256(data) != digest:
            raise SnapshotError(f"snapshot file changed since sync: {p}")
        files[name] = data
    return meta, files
