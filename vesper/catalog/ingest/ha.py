"""Home Assistant device classes, the shared feature vocabulary (spec §2). Apache-2.0."""
from __future__ import annotations

import ast
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch
from vesper.catalog.snapshot import load_snapshot, write_snapshot
from vesper.catalog.sources import SOURCES

RAW = "https://raw.githubusercontent.com/home-assistant/core/{tag}/homeassistant/components/{path}"
FILES = {
    "binary_sensor_const.py": ("binary_sensor/const.py", "BinarySensorDeviceClass"),
    "sensor_const.py": ("sensor/const.py", "SensorDeviceClass"),
}


def parse_device_classes(source: str, class_name: str) -> list[str]:
    """The string values of a Home Assistant StrEnum, read with `ast` (never imported or executed)."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [s.value.value for s in node.body
                    if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)
                    and isinstance(s.value.value, str)]
    raise IngestError(f"class {class_name} not found in Home Assistant source")


def sync_ha(raw_root: Path, client: httpx.Client, tag: str | None = None) -> Path:
    tag = tag or SOURCES["ha"].version
    files = {name: fetch(client, RAW.format(tag=tag, path=path)).content for name, (path, _) in FILES.items()}
    for name, (_, cls) in FILES.items():
        parse_device_classes(files[name].decode("utf-8"), cls)  # fail at sync time, not at build time
    return write_snapshot(raw_root, "ha", tag, files, license=SOURCES["ha"].license,
                          origin=f"{SOURCES['ha'].origin}@{tag}")


def load_vocabulary(raw_root: Path) -> dict[str, frozenset[str]]:
    _, files = load_snapshot(raw_root, "ha")
    return {
        "binary_sensor": frozenset(parse_device_classes(files["binary_sensor_const.py"].decode("utf-8"),
                                                        "BinarySensorDeviceClass")),
        "sensor": frozenset(parse_device_classes(files["sensor_const.py"].decode("utf-8"), "SensorDeviceClass")),
    }
