"""A home file: which real products are installed, under which Zigbee2MQTT friendly names.

    broker: {host: 127.0.0.1, port: 18830}
    seed: 0
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: "hall/door"}

Placement in Habitat rooms is sub-project #2; here the room is only part of the friendly name.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from vesper.catalog.errors import CatalogError
from vesper.catalog.fingerprint import staleness
from vesper.exposure.z2m_bridge import Placement


@dataclass(frozen=True)
class Home:
    records: list[dict]
    placements: list[Placement]
    host: str
    port: int
    seed: int


def load_catalog(catalog_dir: Path) -> dict[str, dict]:
    records = {}
    for name in ("zigbee", "matter", "zwave", "tasmota"):
        path = Path(catalog_dir) / f"{name}.json"
        if path.is_file():
            records.update((r["id"], r) for r in json.loads(path.read_text(encoding="utf-8")))
    if not records:
        raise FileNotFoundError(f"no catalog in {catalog_dir}; run `vesper catalog build` first")
    why = staleness(catalog_dir)
    if why:
        raise CatalogError(f"the catalog in {catalog_dir} is stale: {why}; rebuild it with `vesper catalog build`")
    return records


def load_home(path: Path, catalog_dir: Path) -> Home:
    spec = yaml.safe_load(Path(path).read_text()) or {}
    catalog = load_catalog(catalog_dir)
    devices = spec.get("devices") or []
    missing = [d.get("product") for d in devices if d.get("product") not in catalog]
    if missing:
        raise KeyError(f"products not in the catalog: {missing}")
    broker = spec.get("broker") or {}
    return Home(records=[catalog[d["product"]] for d in devices],
                placements=[Placement(f"{d['product']}@{d['name']}", d["name"]) for d in devices],
                host=broker.get("host", "127.0.0.1"), port=int(broker.get("port", 18830)),
                seed=int(spec.get("seed", 0)))
