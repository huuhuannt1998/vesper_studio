"""Matter data-model conformance of the Matter tier's nodes (spec §1 criterion 3; §4 L2, Matter tier).

What the Matter SDK's device-conformance test (TC_DeviceConformance) and its global-attribute test (TC_IDM_10_1)
check, re-implemented over the attributes a commissioned node reports to the Matter Server, against the data model
(the SDK's `data_model/<version>` XML, the spec version the node says it implements):

- every endpoint's device types (Descriptor's DeviceTypeList) are known, and their mandatory server clusters are on
  the endpoint (its ServerList);
- every cluster is known, its ClusterRevision is the data model's, and its FeatureMap sets only defined features and
  every mandatory one;
- its AttributeList has every attribute mandatory under its features (conformance rules evaluated as the spec writes
  them), and nothing disallowed; every listed attribute was read; AcceptedCommandList has every mandatory command.

A rule the reader cannot decide from a node's own data (a condition such as "LIT" or a value comparison) is not
counted either way. This is VESPER's re-implementation of those checks, not the official certification test.
"""
from __future__ import annotations

import functools
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

GLOBAL = {0xFFFD: "ClusterRevision", 0xFFFC: "FeatureMap", 0xFFFB: "AttributeList", 0xFFF9: "AcceptedCommandList",
          0xFFF8: "GeneratedCommandList"}
DESCRIPTOR, BASIC = 0x001D, 0x0028
CONDITIONS = {"Matter": True, "Zigbee": False, "ZHA": False, "ZSE": False, "GP": False, "SuZi": False}


@dataclass
class Element:
    id: int
    name: str
    conform: ET.Element | None


@dataclass
class Cluster:
    id: int
    name: str
    revision: int
    features: dict[str, int] = field(default_factory=dict)  # code -> bit
    feature_conform: dict[str, ET.Element | None] = field(default_factory=dict)
    attributes: dict[int, Element] = field(default_factory=dict)
    commands: dict[int, Element] = field(default_factory=dict)  # client -> server


@dataclass
class DeviceType:
    id: int
    name: str
    clusters: dict[int, ET.Element | None] = field(default_factory=dict)  # server clusters -> conformance


def _int(text: str | None) -> int | None:
    try:
        return int(text, 0) if text else None
    except ValueError:
        return None


def _conform(el: ET.Element) -> ET.Element | None:
    for child in el:
        if child.tag.endswith("Conform"):
            return child
    return None


def _short(name: str) -> str:
    return name.removesuffix(" Cluster").strip()


def _elements(root: ET.Element) -> tuple[dict, dict, dict, dict]:
    """A cluster file's own features (code -> bit, code -> rule), attributes and client->server commands."""
    features, feature_conform, attributes, commands = {}, {}, {}, {}
    for f in root.findall("features/feature"):  # not the <feature> terms inside conformance rules
        if f.get("code") and f.get("bit") is not None:
            features[f.get("code")] = int(f.get("bit"))
            feature_conform[f.get("code")] = _conform(f)
    for a in root.findall("attributes/attribute"):
        aid = _int(a.get("id"))
        if aid is not None:
            attributes[aid] = Element(aid, a.get("name", ""), _conform(a))
    for x in root.findall("commands/command"):
        xid = _int(x.get("id"))
        if xid is not None and x.get("direction", "commandToServer") == "commandToServer":
            commands[xid] = Element(xid, x.get("name", ""), _conform(x))
    return features, feature_conform, attributes, commands


def _merge(base: dict, own: dict) -> dict:
    """A derived cluster's elements over its base's: one listed without a rule keeps the base's rule."""
    out = dict(base)
    for k, e in own.items():
        b = base.get(k)
        out[k] = Element(e.id, e.name, e.conform if e.conform is not None or b is None else b.conform)
    return out


@functools.lru_cache(maxsize=4)
def load_data_model_cached(folder: str) -> tuple[dict[int, Cluster], dict[int, DeviceType]]:
    return load_data_model(Path(folder))


def load_data_model(folder: Path) -> tuple[dict[int, Cluster], dict[int, DeviceType]]:
    """The clusters by id (a derived cluster merged over its base, as the SDK's spec parser does) and the device
    types by id."""
    files = []
    for path in sorted(Path(folder, "clusters").glob("*.xml")):
        root = ET.parse(path).getroot()
        cls = root.find("classification")
        files.append((root, _elements(root), cls.get("baseCluster") if cls is not None else None))
    bases = {_short(root.get("name", "")): elements for root, elements, _ in files}
    clusters: dict[int, Cluster] = {}
    for root, (features, fconf, attributes, commands), base_name in files:
        if base_name and _short(base_name) in bases:
            bf, bfc, ba, bc = bases[_short(base_name)]
            features, attributes, commands = {**bf, **features}, _merge(ba, attributes), _merge(bc, commands)
            fconf = {**bfc, **{k: v if v is not None else bfc.get(k) for k, v in fconf.items()}}
        for c in root.findall("clusterIds/clusterId"):  # an aliased file (several concentration clusters) has several
            cid = _int(c.get("id"))
            if cid is not None:
                clusters[cid] = Cluster(cid, c.get("name") or _short(root.get("name", "")),
                                        _int(root.get("revision")) or 1, dict(features), dict(fconf),
                                        dict(attributes), dict(commands))
    devices: dict[int, DeviceType] = {}
    for path in sorted(Path(folder, "device_types").glob("*.xml")):
        root = ET.parse(path).getroot()
        did = _int(root.get("id"))
        if did is None:
            continue
        d = DeviceType(did, root.get("name", path.stem))
        for c in root.findall("clusters/cluster"):
            cid = _int(c.get("id"))
            if cid is not None and c.get("side") == "server":
                d.clusters[cid] = _conform(c)
        devices[did] = d
    return clusters, devices


# -- conformance ---------------------------------------------------------------------------------------------
def _term(t: ET.Element, ctx: dict) -> bool | None:
    tag = t.tag
    if tag == "feature":
        return t.get("name") in ctx["features"]
    if tag == "attribute":
        return t.get("name") in ctx["attributes"] if "attributes" in ctx else None
    if tag == "command":
        return t.get("name") in ctx["commands"] if "commands" in ctx else None
    if tag == "condition":
        return CONDITIONS.get(t.get("name"))
    kids = [_term(c, ctx) for c in t]
    if tag == "notTerm":
        return None if kids[0] is None else not kids[0]
    if tag == "andTerm":
        return False if False in kids else (None if None in kids else True)
    if tag == "orTerm":
        return True if True in kids else (None if None in kids else False)
    if tag == "xorTerm":
        return None if None in kids else sum(kids) == 1
    return None  # value comparisons and anything else: undecidable here


def _applies(conf: ET.Element, ctx: dict) -> bool | None:
    terms = [c for c in conf if not c.tag.endswith("Conform")]
    if not terms:
        return True
    vals = [_term(t, ctx) for t in terms]
    return False if False in vals else (None if None in vals else True)


def requirement(conf: ET.Element | None, ctx: dict) -> str | None:
    """'M' (mandatory), 'O' (optional, provisional, deprecated or described), 'X' (disallowed), or None
    (undecidable from the node's own data)."""
    if conf is None:
        return None  # no rule in the data model (a derived cluster's base supplies it, or the spec leaves it out)
    tag = conf.tag
    if tag == "otherwiseConform":
        for c in conf:
            r = requirement(c, ctx) if _applies(c, ctx) is not False else "skip"
            if r == "skip":
                continue
            if _applies(c, ctx) is None:
                return None
            return r
        return "X"
    applies = _applies(conf, ctx)
    if tag == "mandatoryConform":
        return None if applies is None else ("M" if applies else "X")
    if tag == "disallowConform":
        return "X"
    if tag in ("optionalConform", "provisionalConform", "deprecateConform", "describedConform"):
        return "O" if applies is not False else "X"
    return None


# -- checking a node ------------------------------------------------------------------------------------------
def node_attributes(raw: dict) -> dict[int, dict[int, dict[int, object]]]:
    """The Matter Server's {"ep/cluster/attr": value} as endpoint -> cluster -> attribute -> value."""
    out: dict[int, dict[int, dict[int, object]]] = {}
    for key, value in raw.items():
        ep, cl, at = (int(x) for x in key.split("/"))
        out.setdefault(ep, {}).setdefault(cl, {})[at] = value
    return out


def check_node(attrs: dict, clusters: dict[int, Cluster], devices: dict[int, DeviceType]) -> list[dict]:
    """Every conformance problem of a node's endpoints: [{endpoint, cluster, check, detail}] (empty: conformant)."""
    problems = []

    def fail(ep, cl, check, detail):
        problems.append({"endpoint": ep, "cluster": f"0x{cl:04X}" if cl is not None else None, "check": check,
                         "detail": detail})
    for ep, cls in sorted(attrs.items()):
        desc = cls.get(DESCRIPTOR, {})
        server = set(desc.get(1) or [])
        for cid in sorted(cls):
            if cid not in server and cid != DESCRIPTOR and cid < 0xFC00:
                fail(ep, cid, "server-list", "a cluster read on the endpoint is not in its ServerList")
        for dt in desc.get(0) or []:
            did = dt.get("0") if isinstance(dt, dict) else None
            d = devices.get(did)
            if d is None:
                fail(ep, None, "device-type", f"unknown device type {did}")
                continue
            for cid, conf in d.clusters.items():
                if requirement(conf, {"features": set()}) == "M" and cid not in server:
                    fail(ep, cid, "device-type-cluster", f"{d.name} needs this server cluster")
        for cid in sorted(server):
            c = clusters.get(cid)
            got = cls.get(cid, {})
            if c is None:
                if cid < 0xFC00:  # 0xFC00 up: manufacturer-specific
                    fail(ep, cid, "cluster", "not a cluster of the data model")
                continue
            for gid, gname in GLOBAL.items():
                if gid not in got:
                    fail(ep, cid, "global", f"{gname} not read")
            if got.get(0xFFFD) is not None and got.get(0xFFFD) != c.revision:
                fail(ep, cid, "revision", f"ClusterRevision {got.get(0xFFFD)}, data model {c.revision}")
            fmap = got.get(0xFFFC) or 0
            features = {code for code, bit in c.features.items() if fmap >> bit & 1}
            undefined = fmap & ~sum(1 << b for b in c.features.values())
            if undefined:
                fail(ep, cid, "feature-map", f"undefined feature bits 0x{undefined:X}")
            listed = set(got.get(0xFFFB) or [])
            names = {c.attributes[a].name for a in listed if a in c.attributes}
            accepted = set(got.get(0xFFF9) or [])
            ctx = {"features": features, "attributes": names,
                   "commands": {c.commands[x].name for x in accepted if x in c.commands}}
            for code, conf in c.feature_conform.items():
                r = requirement(conf, ctx)
                if r == "M" and code not in features:
                    fail(ep, cid, "feature", f"feature {code} is mandatory and not set")
                if r == "X" and code in features:
                    fail(ep, cid, "feature", f"feature {code} is not allowed here and is set")
            for aid, a in c.attributes.items():
                r = requirement(a.conform, ctx)
                if r == "M" and aid not in listed:
                    fail(ep, cid, "attribute", f"{a.name} is mandatory and not in AttributeList")
                if r == "X" and aid in listed:
                    fail(ep, cid, "attribute", f"{a.name} is not allowed and is in AttributeList")
            for aid in listed:
                if aid not in got:
                    fail(ep, cid, "attribute-read", f"0x{aid:04X} is in AttributeList and was not read")
            for xid, x in c.commands.items():
                r = requirement(x.conform, ctx)
                if r == "M" and xid not in accepted:
                    fail(ep, cid, "command", f"{x.name} is mandatory and not in AcceptedCommandList")
                if r == "X" and xid in accepted:
                    fail(ep, cid, "command", f"{x.name} is not allowed and is in AcceptedCommandList")
    return problems


def spec_version(attrs: dict) -> str | None:
    """Basic Information's SpecificationVersion (0x0015), as 'major.minor[.dot]', e.g. 0x01040200 -> '1.4.2'."""
    v = (attrs.get(0, {}).get(BASIC, {}) or {}).get(0x0015)
    if not isinstance(v, int):
        return None
    major, minor, dot = v >> 24 & 0xFF, v >> 16 & 0xFF, v >> 8 & 0xFF
    return f"{major}.{minor}" + (f".{dot}" if dot else "")


def data_model_dir(root: Path, version: str | None) -> Path | None:
    """The data model of a spec version, or the nearest earlier one the SDK has."""
    have = sorted((p for p in Path(root).iterdir() if p.is_dir() and re.fullmatch(r"\d+(\.\d+)*", p.name)),
                  key=lambda p: tuple(int(x) for x in p.name.split(".")))
    if not have:
        return None
    if version is None:
        return have[-1]
    want = tuple(int(x) for x in version.split("."))
    fit = [p for p in have if tuple(int(x) for x in p.name.split(".")) <= want]
    return fit[-1] if fit else have[0]


def read_node(server_url: str, node_id: int, timeout: float = 30.0) -> dict:
    """A commissioned node's attributes from the Matter Server's WebSocket API (`get_node`)."""
    from websockets.sync.client import connect
    with connect(server_url, open_timeout=timeout, max_size=None) as ws:
        ws.send(json.dumps({"message_id": "vesper-conformance", "command": "get_node", "args": {"node_id": node_id}}))
        while True:
            reply = json.loads(ws.recv(timeout=timeout))
            if reply.get("message_id") != "vesper-conformance":
                continue
            if reply.get("error_code") is not None:
                raise RuntimeError(f"get_node {node_id}: {reply.get('details') or reply.get('error_code')}")
            return reply["result"]
