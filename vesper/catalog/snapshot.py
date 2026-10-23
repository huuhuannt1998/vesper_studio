"""Pinned, hashed snapshots of raw source data under catalog/raw/<source>/<version>/ (spec §2, §5 Errors).

`CURRENT` is written last, so an interrupted sync never replaces the previous good snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
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
    d = Path(raw_root) / source / version
    d.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in sorted(files.items()):
        target = d / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = _sha256(data)
    meta = {
        **(extra or {}),
        "source": source, "version": version, "license": license, "origin": origin,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": hashes,
    }
    (d / "snapshot.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    (Path(raw_root) / source / "CURRENT").write_text(version + "\n")
    return d


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
