"""Which catalog code built a catalog directory.

A catalog built by other code (for instance before a fix to how reporting rules are normalized) would feed
old values to every experiment without any sign, so commands that run twins refuse it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent


def code_fingerprint(root: Path = CODE_ROOT) -> str:
    """SHA-256 over the names and contents of every .py and .json file under the catalog package."""
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json") and "__pycache__" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def staleness(catalog_dir: Path) -> str | None:
    """Why catalog_dir was not built by the current catalog code, or None if it was."""
    report = Path(catalog_dir) / "build_report.json"
    if not report.is_file():
        return "it has no build_report.json"
    built = json.loads(report.read_text()).get("code_sha256")
    if built is None:
        return "its build report predates code fingerprints"
    current = code_fingerprint()
    if built != current:
        return f"it was built by other catalog code ({built[:12]}, now {current[:12]})"
    return None
