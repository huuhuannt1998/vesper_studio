"""The real Matter host (host.mjs on matter.js), offline: nodes are built but never started, so no socket is
opened and nothing is announced by mDNS (VESPER_MATTER_OFFLINE=1).

Opt-in: VESPER_MATTER_HOST_TESTS=1, with the host installed by `vesper setup matter` (or VESPER_MATTER_HOST_DIR
naming an installed copy). With catalog/vds present, every device type the Matter acceptance can sample is built.
"""
import json
import os
import socket
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.catalog.test_matter_normalize import records as matter_records
from vesper.exposure.matter_tier import Host, HostError, node_spec
from vesper.twins.twin import Twin
from vesper.validation.matter_acceptance import select_matter_products

pytestmark = pytest.mark.skipif(not os.environ.get("VESPER_MATTER_HOST_TESTS"),
                                reason="opt-in: VESPER_MATTER_HOST_TESTS=1 (needs `vesper setup matter`)")
HOST_DIR = Path(os.environ.get("VESPER_MATTER_HOST_DIR", ".vesper/matter/host"))


@pytest.fixture
def host(tmp_path, monkeypatch):
    if not (HOST_DIR / "node_modules").is_dir():
        pytest.skip(f"no installed host in {HOST_DIR}; run `vesper setup matter`")
    monkeypatch.setenv("VESPER_MATTER_OFFLINE", "1")
    h = Host(tmp_path / "nodes", command=["node", str(HOST_DIR / "host.mjs")], timeout=120,
             log=tmp_path / "host.log")
    h.start()
    yield h
    h.stop()


def add(host, record, index, state=None):
    spec = node_spec(record, record["id"], index)
    feats = [f for f in record["features"] if f["type"] != "unsupported"]
    fields = {k: v for k, v in asdict(spec).items() if k != "twin_id"}
    return host.request("add", twin=record["id"], **fields, features=[{"name": f["name"], "type": f["type"]}
                                                                         for f in feats], state=state or {})


def test_a_node_resolves_its_features_and_has_pairing_codes(host):
    reply = add(host, matter_records()["matter:100b:0018"], 0, {"On/Off.OnOff": True})
    assert reply["unresolved"] == [] and reply["initial_errors"] == []
    assert reply["pairing"]["qr"].startswith("MT:") and len(reply["pairing"]["manual"]) in (11, 21)
    assert host.request("get", twin="matter:100b:0018")["values"]["On/Off.OnOff"] is True


def test_values_cross_the_codec_both_ways(host):
    lock = matter_records()["matter:115f:0001"]
    add(host, lock, 1)
    host.request("set", twin=lock["id"], feature="Door Lock.LockState", value="Locked")
    host.request("set", twin=lock["id"], feature="Door Lock.OperatingMode", value="Vacation")
    values = host.request("get", twin=lock["id"])["values"]
    assert values["Door Lock.LockState"] == "Locked" and values["Door Lock.OperatingMode"] == "Vacation"
    with pytest.raises(HostError, match="not a value of"):
        host.request("set", twin=lock["id"], feature="Door Lock.LockState", value="Ajar")


def test_a_device_type_without_a_recipe_is_refused(host):
    rec = matter_records()["matter:100b:0016"]
    rec = {**rec, "binding": {"matter": {**rec["binding"]["matter"], "device_type": "Heat Pump"}}}
    with pytest.raises(HostError, match="no recipe for device type \"Heat Pump\""):
        add(host, rec, 2)


def test_a_removed_node_is_gone(host):
    rec = matter_records()["matter:100b:0016"]
    add(host, rec, 3)
    host.request("remove", twin=rec["id"])
    with pytest.raises(HostError, match="no node"):
        host.request("set", twin=rec["id"], feature="Boolean State.StateValue", value=True)


def test_commissioning_against_no_server_fails_quickly(host):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(HostError, match="commission"):
        host.request("commission", timeout=30, url=f"ws://127.0.0.1:{port}/ws", code="MT:Y.K90AFN00KA0648G00",
                     seconds=10)


def test_every_device_type_the_acceptance_can_sample_builds_and_resolves(host):
    path = Path("catalog/vds/matter.json")
    if not path.exists():
        pytest.skip("no built catalog")
    picked = select_matter_products(json.loads(path.read_text()), per_type=1, seed=0)
    problems = {}
    for i, rec in enumerate(picked):
        twin = Twin.from_vds(rec)  # the twin's real starting state, as MatterTier.start pushes it
        state = {n: v for n, v in twin.state.items()
                 if twin.features[n]["type"] != "unsupported" and twin.features[n].get("category") != "diagnostic"}
        try:
            reply = add(host, rec, 10 + i, state)
        except HostError as e:
            problems[rec["binding"]["matter"]["device_type"]] = str(e)
            continue
        # a refused initial value is allowed (MatterTier adopts the node's value), but it must name a resolved
        # feature, or the tier could not adopt it
        orphans = [e for e in reply["initial_errors"] if not any(e.startswith(n + ": ") for n in reply["resolved"])]
        if reply["unresolved"] or orphans:
            problems[rec["binding"]["matter"]["device_type"]] = reply
    assert len(picked) >= 17 and problems == {}
