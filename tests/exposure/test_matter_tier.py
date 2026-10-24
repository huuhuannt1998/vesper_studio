import sys
import threading
from pathlib import Path

import pytest

from tests.catalog.test_matter_normalize import records as matter_records
from vesper.exposure.matter_tier import (COMMISSION_MARGIN_S, NODE_PORT_BASE, TEST_VENDOR_ID, Host, HostError,
                                         MatterTier, node_spec)
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime

FAKE = [sys.executable, "-I", str(Path(__file__).parent / "fake_matter_host.py")]


# -- node identity -----------------------------------------------------------------------------------------------
def test_a_node_carries_the_products_identity_and_a_serial_home_assistant_keeps():
    rec = matter_records()["matter:100b:0016"]
    spec = node_spec(rec, "matter:100b:0016@0", 3)
    assert (spec.vendor_id, spec.product_id) == (0x100B, 0x0016) and spec.device_type == "Contact Sensor"
    assert spec.vendor_name == "Philips Hue" and spec.port == NODE_PORT_BASE + 3
    assert spec.serial.startswith("VSP-") and "test" not in spec.serial.lower() and len(spec.serial) <= 32
    assert node_spec(rec, "matter:100b:0016@0", 3) == spec  # deterministic
    assert node_spec(rec, "matter:100b:0016@1", 4).serial != spec.serial


def test_test_vendor_mode_keeps_the_names_but_uses_the_test_vendor_id():
    spec = node_spec(matter_records()["matter:100b:0016"], "t", 0, vid_mode="test")
    assert spec.vendor_id == TEST_VENDOR_ID and 0x8000 <= spec.product_id <= 0xFFFF
    assert spec.vendor_name == "Philips Hue"


def test_pairing_codes_are_valid_and_distinct_and_long_names_are_cut_to_matters_limit():
    rec = matter_records()["matter:100b:0016"]
    rec = {**rec, "identity": {**rec["identity"], "model": "M" * 60}}
    specs = [node_spec(rec, f"t{i}", i) for i in range(400)]
    invalid = {0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888, 99999999,
               12345678, 87654321}
    assert all(0 < s.passcode < 99999999 and s.passcode not in invalid for s in specs)
    assert len({s.passcode for s in specs}) == 400 and all(0 <= s.discriminator <= 0xFFF for s in specs)
    # distinct long discriminators: commissioning by QR code finds exactly the node it means
    assert len({s.discriminator for s in specs}) == 400
    assert all(len(s.product_name) <= 32 and len(s.label) <= 32 for s in specs)


def test_a_record_without_a_matter_device_type_cannot_be_a_node():
    with pytest.raises(ValueError, match="device type"):
        node_spec(matter_records()["matter:fff1:8000"], "t", 0)


# -- the host process --------------------------------------------------------------------------------------------
@pytest.fixture
def host(tmp_path):
    h = Host(tmp_path / "nodes", interface="en0", command=FAKE, timeout=5.0)
    h.start()
    yield h
    h.stop()


def test_requests_get_their_own_replies_and_an_error_reply_raises(host):
    assert host.request("add", twin="a", features=[{"name": "x", "type": "binary"}], state={})["resolved"] == ["x"]
    with pytest.raises(HostError, match="boom"):
        host.request("fail")


def test_the_host_gets_its_storage_and_interface(host, tmp_path):
    env = host.request("env")["env"]
    # MATTER_LOG_FORMAT: the host's log is read by people, not a terminal, so no colour codes
    assert env == {"MATTER_STORAGE_PATH": str(tmp_path / "nodes"), "MATTER_MDNS_NETWORKINTERFACE": "en0",
                   "MATTER_LOG_FORMAT": "plain"}


def test_a_slow_reply_times_out(tmp_path):
    h = Host(tmp_path, command=FAKE, timeout=0.3)
    h.start()
    try:
        with pytest.raises(HostError, match="timed out"):
            h.request("sleep", seconds=2)
    finally:
        h.stop()


def test_an_event_handler_may_call_the_host_without_deadlock(host):
    done = threading.Event()

    def handler(event):
        host.request("set", twin="a", feature="x", value=True)  # the reader thread must still deliver this reply
        done.set()
    host.on_event(handler)
    host.request("emit", event={"event": "changed", "twin": "a", "feature": "x", "value": True})
    assert done.wait(5)


def test_a_host_that_dies_fails_the_pending_and_every_later_request(host):
    with pytest.raises(HostError, match="exited"):
        host.request("die")
    with pytest.raises(HostError, match="exited"):
        host.request("set", twin="a", feature="x", value=1)


def test_stop_shuts_the_host_down(tmp_path):
    h = Host(tmp_path, command=FAKE, timeout=5.0)
    h.start()
    h.stop()
    assert h.proc is None


# -- the tier ----------------------------------------------------------------------------------------------------
class FakeHost:
    def __init__(self, unresolved=(), refuse=()):
        self.calls, self.handlers, self.unresolved, self.refuse = [], [], set(unresolved), set(refuse)

    def request(self, op, wait=True, **args):
        self.calls.append((op, args))
        if op == "add" and args["device_type"] in self.refuse:
            raise HostError(f"add: no recipe for device type {args['device_type']!r}")
        if op == "add":
            names = [f["name"] for f in args["features"]]
            return {"resolved": [n for n in names if n not in self.unresolved],
                    "unresolved": [n for n in names if n in self.unresolved],
                    "pairing": {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}}
        if op == "commission":
            return {"node_id": 7}
        return {}

    def on_event(self, fn):
        self.handlers.append(fn)

    def emit(self, **event):
        for fn in self.handlers:
            fn({"event": "changed", **event})

    def sets(self):
        return [(a["twin"], a["feature"], a["value"]) for op, a in self.calls if op == "set"]


def tier(*rids, unresolved=(), refuse=()):
    rt = Runtime(clock=LogicalClock())
    recs = matter_records()
    specs = {}
    for i, rid in enumerate(rids):
        tid = rt.add(recs[rid], twin_id=f"{rid}@{i}").id
        specs[tid] = node_spec(recs[rid], tid, i)
    host = FakeHost(unresolved, refuse)
    t = MatterTier(rt, host, specs)
    t.start()
    return t, rt, host, list(specs)


def test_start_adds_every_twin_with_its_features_and_state_and_notes_what_did_not_resolve():
    t, rt, host, (lamp,) = tier("matter:100b:0018", unresolved={"Color Control.ColorMode"})
    (op, args), = host.calls
    assert op == "add" and args["twin"] == lamp and args["device_type"] == "Color Temperature Light"
    assert {f["name"] for f in args["features"]} == set(rt.twins[lamp].features)
    assert args["state"]["On/Off.OnOff"] is False and args["serial"].startswith("VSP-")
    # a descriptive attribute keeps the node's own value: the twin's default is only the first enum value
    assert "Color Control.ColorMode" not in args["state"] and "Color Control.ColorMode" in {
        f["name"] for f in args["features"]}
    assert t.pairing[lamp] == {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}
    assert t.notes == [f"{lamp}: Color Control.ColorMode has no matter.js attribute; it is not exposed"]


def test_a_node_is_commissioned_by_its_qr_code_and_its_node_id_kept():
    t, rt, host, (door,) = tier("matter:100b:0016")
    assert t.commission(door, "ws://127.0.0.1:15580/ws", timeout=90) == 7 and t.node_ids == {door: 7}
    # the host gives the Matter Server `seconds`; VESPER waits a little longer for the host's own answer
    assert host.calls[-1] == ("commission", {"url": "ws://127.0.0.1:15580/ws", "code": "MT:Y.K90AFN00KA0648G00",
                                             "seconds": 90, "timeout": 90 + COMMISSION_MARGIN_S})


def test_a_twin_the_host_cannot_add_fails_alone():
    t, rt, host, (door, lamp) = tier("matter:100b:0016", "matter:100b:0018", refuse={"Contact Sensor"})
    assert set(t.resolved) == {lamp} and t.failed == {door: "add: no recipe for device type 'Contact Sensor'"}
    with pytest.raises(HostError, match="no node.*no recipe"):
        t.commission(door, "ws://127.0.0.1:15580/ws", timeout=5)
    rt.stimulate(door, {"type": "contact", "state": "closed"})  # its reports go nowhere, quietly
    assert host.sets() == []
    t.stop()
    assert [a["twin"] for op, a in host.calls if op == "remove"] == [lamp]


def test_a_dead_host_still_ends_the_run():
    class Dead(FakeHost):
        dead = "the Matter host exited (1)"

        def request(self, op, wait=True, **args):
            raise HostError(self.dead)

    rt = Runtime(clock=LogicalClock())
    rec = matter_records()["matter:100b:0016"]
    tid = rt.add(rec).id
    with pytest.raises(HostError, match="exited"):
        MatterTier(rt, Dead(), {tid: node_spec(rec, tid, 0)}).start()


def test_the_host_says_whether_it_died(host):
    assert host.dead is None
    with pytest.raises(HostError):
        host.request("die")
    assert "exited" in host.dead


def test_a_reported_value_reaches_the_node():
    t, rt, host, (door,) = tier("matter:100b:0016")
    rt.stimulate(door, {"type": "contact", "state": "closed"})
    assert host.sets() == [(door, "Boolean State.StateValue", True)]


def test_a_controllers_change_becomes_a_platform_command_and_its_report_follows():
    t, rt, host, (lamp,) = tier("matter:100b:0018")
    host.emit(twin=lamp, feature="On/Off.OnOff", value=True)
    assert rt.state(lamp)["On/Off.OnOff"] is True and rt.actions[-1].actor == "platform"
    assert host.sets() == [(lamp, "On/Off.OnOff", True)]


def test_a_change_the_device_would_refuse_is_rejected_and_the_node_put_back():
    t, rt, host, (door,) = tier("matter:100b:0016")
    host.emit(twin=door, feature="Boolean State.StateValue", value=True)  # read-only on a real contact sensor
    assert rt.state(door)["Boolean State.StateValue"] is False and rt.actions[-1].accepted is False
    assert host.sets() == [(door, "Boolean State.StateValue", False)]


def test_an_event_for_an_unknown_twin_is_noted_not_fatal():
    t, rt, host, _ = tier("matter:100b:0016")
    host.emit(twin="nobody", feature="On/Off.OnOff", value=True)
    assert t.notes == ["change for unknown twin 'nobody' ignored"]


def test_stop_removes_every_node():
    t, rt, host, (door, lamp) = tier("matter:100b:0016", "matter:100b:0018")
    t.stop()
    assert [a["twin"] for op, a in host.calls if op == "remove"] == [door, lamp]
