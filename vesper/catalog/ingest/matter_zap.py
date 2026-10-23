"""Matter clusters and device types from connectedhomeip's Apache-2.0 ZAP XML (spec §5 Licensing).

Never reads connectedhomeip's `data_model/<version>/` XML: its CSA license allows internal use only and
forbids publishing it or creating derivative works. Every file must carry the Apache-2.0 header.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from vesper.catalog.errors import IngestError
from vesper.catalog.ingest._http import fetch, fetch_json
from vesper.catalog.snapshot import write_snapshot
from vesper.catalog.sources import SOURCES

LISTING = ("https://api.github.com/repos/project-chip/connectedhomeip/contents/"
           "src/app/zap-templates/zcl/data-model/chip")
ALLOWED_PATH = "/src/app/zap-templates/zcl/data-model/chip/"
APACHE = b"Licensed under the Apache License, Version 2.0"


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str
    optional: bool
    writable: bool
    min: int | None
    max: int | None
    define: str = ""  # the id device types use in <requireAttribute>


@dataclass(frozen=True)
class Cluster:
    name: str
    code: int
    attributes: tuple[Attribute, ...]


@dataclass(frozen=True)
class DeviceType:
    name: str
    device_id: int
    server_clusters: tuple[str, ...]  # mandatory server clusters (server="true" serverLocked="true")
    required: dict[str, tuple[str, ...]] = field(default_factory=dict)  # cluster -> <requireAttribute> defines


@dataclass
class ZapModel:
    clusters: dict[str, Cluster] = field(default_factory=dict)
    device_types: dict[int, DeviceType] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)  # names defined without a cluster
    bitmaps: dict[str, list[str]] = field(default_factory=dict)
    scoped_enums: dict[tuple[int, str], list[str]] = field(default_factory=dict)  # (cluster code, name)
    scoped_bitmaps: dict[tuple[int, str], list[str]] = field(default_factory=dict)
    structs: set[str] = field(default_factory=set)
    atomic: dict[str, str] = field(default_factory=dict)  # atomic type name -> VDS feature type


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text.strip(), 0)
    except ValueError:
        return None


def _atomic_kind(name: str) -> str:
    """Bare enum8/enum16 and bitmapN carry no item names, so they are modeled as plain integers."""
    if name == "boolean":
        return "binary"
    if name in ("char_string", "long_char_string"):
        return "text"
    if name in ("octet_string", "long_octet_string", "array", "struct", "no_data", "unknown"):
        return "unsupported"
    return "numeric"


def parse_zap(files: dict[str, bytes]) -> ZapModel:
    m = ZapModel()
    for name, data in sorted(files.items()):
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise IngestError(f"{name}: invalid XML: {e}") from e
        for atomic in root.iter("atomic"):
            for t in atomic.findall("type"):
                m.atomic[t.get("name")] = _atomic_kind(t.get("name"))
        for tag, child, plain, scoped in (("enum", "item", m.enums, m.scoped_enums),
                                          ("bitmap", "field", m.bitmaps, m.scoped_bitmaps)):
            for e in root.iter(tag):
                members = [i.get("name") for i in e.findall(child)]
                codes = [c for c in (_int(x.get("code")) for x in e.findall("cluster")) if c is not None]
                for code in codes:  # the same name can mean different things in different clusters
                    scoped[(code, e.get("name"))] = members
                if not codes:
                    plain[e.get("name")] = members
        for s in root.iter("struct"):
            m.structs.add(s.get("name"))
        for c in root.iter("cluster"):
            cname, code = (c.findtext("name") or "").strip(), _int(c.findtext("code"))
            if not cname or code is None:  # <cluster code=.../> references inside enums and structs
                continue
            m.clusters[cname] = Cluster(cname, code, tuple(
                Attribute(name=a.get("name") or (a.text or "").strip(), type=a.get("type", ""),
                          optional=a.get("optional") == "true", writable=a.get("writable") == "true",
                          min=_int(a.get("min")), max=_int(a.get("max")), define=a.get("define") or "")
                for a in c.findall("attribute") if a.get("side") == "server"))
        for d in root.iter("deviceType"):
            did = _int(d.findtext("deviceId"))
            if did is not None:
                mandatory = [i for i in d.iter("include")
                             if i.get("server") == "true" and i.get("serverLocked") == "true"]
                m.device_types[did] = DeviceType(
                    name=(d.findtext("typeName") or "").strip(), device_id=did,
                    server_clusters=tuple(i.get("cluster") for i in mandatory),
                    required={i.get("cluster"): tuple((r.text or "").strip() for r in i.findall("requireAttribute"))
                              for i in mandatory if i.findall("requireAttribute")})
    if not m.device_types or not m.clusters:
        raise IngestError("ZAP XML defines no device types or no clusters")
    return m


def resolve_type(m: ZapModel, type_name: str, cluster_code: int | None = None) -> tuple[str, list[str] | None]:
    """VDS feature type for a ZAP attribute type, plus enum values or bitmap flag names.

    Named enums and bitmaps resolve in the attribute's own cluster first, then globally. A name defined
    differently by several other clusters is ambiguous and reported as unsupported, never guessed.
    """
    kinds = (("enum", m.enums, m.scoped_enums), ("bitmap", m.bitmaps, m.scoped_bitmaps))
    for kind, _, scoped in kinds:
        if (cluster_code, type_name) in scoped:
            return kind, scoped[(cluster_code, type_name)]
    for kind, plain, scoped in kinds:
        if type_name in plain:
            return kind, plain[type_name]
        candidates = {tuple(v) for (_, name), v in scoped.items() if name == type_name}
        if len(candidates) == 1:
            return kind, list(candidates.pop())
        if candidates:
            return "unsupported", None
    if type_name in m.structs:
        return "unsupported", None
    return m.atomic.get(type_name, "unsupported"), None


def sync_matter_zap(raw_root: Path, client: httpx.Client, tag: str | None = None) -> Path:
    tag = tag or SOURCES["matter_zap"].version
    listing = fetch_json(client, LISTING, params={"ref": tag})
    entries = [e for e in listing if e.get("type") == "file" and e.get("name", "").endswith(".xml")]
    if not entries:
        raise IngestError(f"no ZAP XML listed at {tag}")
    files = {}
    for e in entries:
        url = e["download_url"]
        if "/data_model/" in url or ALLOWED_PATH not in url:
            raise IngestError(f"refusing {url}: Matter data may come only from the Apache-2.0 ZAP XML")
        data = fetch(client, url).content
        if git_blob_sha1(data) != e["sha"]:
            raise IngestError(f"{e['name']}: content does not match git blob {e['sha']}")
        if APACHE not in data[:4000]:
            raise IngestError(f"{e['name']}: no Apache-2.0 license header; refusing it")
        files[e["name"]] = data
    parse_zap(files)  # fail at sync time, not at build time
    return write_snapshot(raw_root, "matter_zap", tag, files, license=SOURCES["matter_zap"].license,
                          origin=f"{SOURCES['matter_zap'].origin}@{tag}", extra={"xml_files": len(files)})
