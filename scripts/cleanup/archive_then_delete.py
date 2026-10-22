#!/usr/bin/env python3
"""Delete files/directories only if every file in them is in the archive manifest with the same SHA-256.

Usage: archive_then_delete.py --manifest MANIFEST.sha256 --root REPO PATH [PATH ...]
All-or-nothing: if any file under any PATH is unarchived or differs, nothing is deleted (exit 1).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


def _is_junk(p: Path) -> bool:
    return "__pycache__" in p.parts or p.suffix == ".pyc" or p.name == ".DS_Store"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(m: Path) -> dict[str, str]:
    out = {}
    for line in m.read_text().splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        out[rel[2:] if rel.startswith("./") else rel] = digest
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--skip-missing", action="store_true")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    manifest, root = Path(a.manifest), Path(a.root).resolve()
    if not manifest.is_file():
        print(f"refuse: manifest not found: {manifest}")
        return 1
    known = _load_manifest(manifest)
    targets, problems = [], []
    for raw in a.paths:
        t = (root / raw).resolve()
        if root not in t.parents:
            problems.append(f"outside root: {raw}")
            continue
        if not t.exists() and not t.is_symlink():
            if not a.skip_missing:
                problems.append(f"missing: {raw}")
            continue
        files = [t] if t.is_file() or t.is_symlink() else [p for p in t.rglob("*") if p.is_file() and not p.is_symlink()]
        for f in files:
            if _is_junk(f):
                continue
            rel = f.relative_to(root).as_posix()
            if rel not in known:
                problems.append(f"not archived: {rel}")
            elif known[rel] != _sha256(f):
                problems.append(f"hash differs: {rel}")
        targets.append(t)
    if problems:
        print("refuse to delete; problems:")
        for p in problems[:50]:
            print("  " + p)
        return 1
    for t in targets:
        if t.is_dir() and not t.is_symlink():
            shutil.rmtree(t)
        else:
            t.unlink()
        print(f"deleted: {t.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
