"""Zigbee2MQTT device definitions from zigbee-herdsman-converters (MIT), exported to JSON by Node (spec §2).

The definitions are JavaScript code, so a small Node script (`zhc_export.cjs`) evaluates them.
npm runs with --ignore-scripts: no install hook of any dependency is executed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from vesper.catalog.errors import IngestError
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

PACKAGE = "zigbee-herdsman-converters"
EXPORT_SCRIPT = Path(__file__).with_name("zhc_export.cjs")
MIN_NODE = 20


def _run(run: Callable, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return run(cmd, check=True, **kwargs)
    except (subprocess.CalledProcessError, OSError) as e:
        raise IngestError(f"command failed: {' '.join(cmd)}: {e}") from e


def sync_zigbee(
    raw_root: Path, cache_root: Path, *, version: str | None = None,
    run: Callable = subprocess.run, which: Callable = shutil.which,
) -> Path:
    version = version or SOURCES["zigbee"].version
    node, npm = which("node"), which("npm")
    if not node or not npm:
        raise IngestError(f"Zigbee ingestion needs Node.js >= {MIN_NODE} and npm on PATH")
    found = _run(run, [node, "--version"], capture_output=True, text=True).stdout.strip()
    major = re.match(r"v(\d+)\.", found)
    if not major or int(major.group(1)) < MIN_NODE:
        raise IngestError(f"Node.js >= {MIN_NODE} required, found {found!r}")
    pkg = Path(cache_root) / f"zhc-{version}"
    pkg.mkdir(parents=True, exist_ok=True)
    _run(run, [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund", "--prefix", str(pkg),
               f"{PACKAGE}@{version}"])
    export = pkg / "export.json"
    _run(run, [node, str(EXPORT_SCRIPT), str(pkg / "node_modules" / PACKAGE), str(export)])
    data = export.read_bytes()
    doc = json.loads(data)
    if doc.get("version") != version:
        raise IngestError(f"exported {PACKAGE} {doc.get('version')!r}, but {version!r} is pinned")
    files = {"definitions.json": data}
    lock = pkg / "package-lock.json"
    if lock.is_file():
        files["package-lock.json"] = lock.read_bytes()  # records the resolved dependency tree
    return write_snapshot(raw_root, "zigbee", version, files, license=SOURCES["zigbee"].license,
                          origin=f"{SOURCES['zigbee'].origin}@{version}",
                          extra={"definitions": len(doc["definitions"])})
