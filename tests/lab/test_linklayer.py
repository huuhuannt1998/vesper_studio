"""The in-process link layer (security-lab spec §3.2, §3.7, plan W7a1): each Zigbee device's hop to the
coordinator, and on the logical clock the TCP links too, between a twin's report and whoever hears it (the bridge,
VESPER's automation engine), with the effects an attack puts on a link."""
import pytest

from tests.lab.maps import house
from vesper.lab.home import PlacedDevice
from vesper.lab.linklayer import Effect, LinkLayer
from vesper.lab.network import NetworkError, parse_network
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Event

ZB = {"binding": {"zigbee": {}}}


class FakeRuntime:
    def __init__(self):
        self.fns = []

    def subscribe(self, fn):
        self.fns.append(fn)

    def report(self, clock, twin_id, feature, value):
        e = Event(twin_id, feature, value, clock.now(), clock.now(), "human")
        for fn in self.fns:
            fn(e)


def layer(carry_tcp=True, devices=None, doors=None):
    devices = devices or [PlacedDevice("lamp", "zigbee:acme:dim-1", ZB, "bedroom", None, (5.0, 5.0), "table", None),
                          PlacedDevice("door", "zigbee:aqara:mccgq11lm", ZB, "hall", "hall-porch", (0.0, 3.0),
                                       "door", None)]
    m, clock, rt, records = house(), LogicalClock(), FakeRuntime(), []
    net = parse_network(None, m, devices)
    names = {f"tw-{d.name}": d.name for d in devices}
    links = LinkLayer(net, m, clock, rt, names, records.append, seed=3, carry_tcp=carry_tcp,
                      doors=lambda: dict(doors or {}))
    heard = []
    links.subscribe(lambda e: heard.append((clock.now(), e.twin_id, e.feature, e.value)))
    links.start()
    return links, clock, rt, heard, records


def test_a_report_reaches_its_listeners_after_its_hop_and_on_the_logical_clock_the_coordinator_and_hub_too():
    links, clock, rt, heard, _ = layer()
    rt.report(clock, "tw-lamp", "state", "ON")
    assert heard == []  # on its way
    clock.advance(1.0)
    (t, twin, feature, value), = heard
    assert (twin, feature, value) == ("tw-lamp", "state", "ON")
    hop = links.quality("zigbee/lamp").latency_s
    assert hop * 0.5 <= t <= hop * 1.5  # the hop's latency with up to ±50% jitter; coordinator and hub are internal


def test_without_carrying_tcp_the_layer_carries_only_the_zigbee_hop():
    links, *_ = layer(carry_tcp=False)
    assert links.carried("lamp") == ["zigbee/lamp"]
    links, *_ = layer(carry_tcp=True)
    assert links.carried("lamp") == ["zigbee/lamp", "coordinator", "hub"]


def test_one_device_s_reports_never_overtake_each_other():
    links, clock, rt, heard, _ = layer()
    for k in range(20):
        rt.report(clock, "tw-lamp", "brightness", k)
        clock.advance(0.001)
    clock.advance(1.0)
    assert [v for _, _, _, v in heard] == list(range(20))


def test_a_delay_holds_the_matching_reports_and_everything_behind_them_and_the_trace_says_so():
    links, clock, rt, heard, records = layer()
    links.apply(Effect("delay", link="zigbee/door", direction="up", device="door", feature="contact", hold_s=40))
    rt.report(clock, "tw-door", "contact", False)
    clock.advance(0.5)
    rt.report(clock, "tw-door", "battery", 90)  # behind the held report, as on the device's stream
    rt.report(clock, "tw-lamp", "state", "ON")  # another device's hop: not held
    clock.advance(1.0)
    assert [(f, v) for _, _, f, v in heard] == [("state", "ON")]
    clock.advance(40.0)
    assert [(f, v) for _, _, f, v in heard] == [("state", "ON"), ("contact", False), ("battery", 90)]
    assert heard[1][0] == pytest.approx(40.0, abs=0.1)
    held, = [r for r in records if r.get("event") == "held"]
    assert held["kind"] == "net" and held["link"] == "zigbee/door" and held["device"] == "door"
    assert held["feature"] == "contact" and held["sent_t"] == 0.0 and held["deliver_t"] == pytest.approx(40, abs=0.1)


def test_a_drop_loses_the_matching_reports_and_a_cut_loses_everything_until_it_is_cleared():
    links, clock, rt, heard, records = layer()
    links.apply(Effect("drop", link="zigbee/door", device="door", feature="contact"))
    rt.report(clock, "tw-door", "contact", False)
    rt.report(clock, "tw-door", "battery", 90)
    clock.advance(1)
    assert [f for _, _, f, _ in heard] == ["battery"]
    links.clear()
    links.apply(Effect("cut", link="coordinator"))
    rt.report(clock, "tw-lamp", "state", "ON")
    rt.report(clock, "tw-door", "contact", True)
    clock.advance(1)
    assert [f for _, _, f, _ in heard] == ["battery"]
    links.clear("coordinator")
    rt.report(clock, "tw-lamp", "state", "OFF")
    clock.advance(1)
    assert [v for _, _, _, v in heard][-1] == "OFF"
    events = [(r["link"], r["event"]) for r in records if r.get("event") in ("down", "up", "dropped")]
    assert events == [("zigbee/door", "dropped"), ("coordinator", "down"), ("coordinator", "dropped"),
                      ("coordinator", "dropped"), ("coordinator", "up")]


def test_a_duplicate_delivers_twice_and_a_reorder_lets_later_reports_overtake():
    links, clock, rt, heard, _ = layer()
    links.apply(Effect("duplicate", link="zigbee/lamp", device="lamp", hold_s=2))
    rt.report(clock, "tw-lamp", "state", "ON")
    clock.advance(5)
    assert [v for _, _, _, v in heard] == ["ON", "ON"]
    links.clear()
    links.apply(Effect("reorder", link="zigbee/lamp", device="lamp", feature="state", hold_s=3))
    rt.report(clock, "tw-lamp", "state", "OFF")
    rt.report(clock, "tw-lamp", "brightness", 10)
    clock.advance(5)
    assert [v for _, _, _, v in heard][2:] == [10, "OFF"]


def test_commands_go_down_the_same_links_and_a_held_command_runs_late():
    links, clock, rt, heard, records = layer()
    ran = []
    links.down("lamp", "state", lambda: ran.append(clock.now()))
    clock.advance(1)
    assert len(ran) == 1 and 0 < ran[0] < 0.1
    links.apply(Effect("delay", link="coordinator", direction="down", device="lamp", hold_s=10))
    links.down("lamp", "state", lambda: ran.append(clock.now()))
    links.down("door", "state", lambda: ran.append(("door", clock.now())))  # not this device: not held
    clock.advance(20)
    assert ran[1][0] == "door" and ran[2] == pytest.approx(11, abs=0.1)


def test_an_effect_can_end_by_itself():
    links, clock, rt, heard, records = layer()
    links.apply(Effect("cut", link="zigbee/lamp", until=5.0))
    rt.report(clock, "tw-lamp", "state", "ON")
    clock.advance(6)
    rt.report(clock, "tw-lamp", "state", "OFF")
    clock.advance(1)
    assert [v for _, _, _, v in heard] == ["OFF"]
    assert [(r["link"], r["event"]) for r in records if r.get("event") in ("down", "up")] == [
        ("zigbee/lamp", "down"), ("zigbee/lamp", "up")]


def test_a_hop_out_of_radio_range_is_down_and_a_shut_door_can_take_one_out_of_range(monkeypatch):
    """With the radio's drop threshold raised for the test: the lamp (4 m, one wall) is out of range from the
    start, and the front door's sensor (1.5 m, no wall) only once its door is shut (3 dB more)."""
    from vesper.lab.network import ZIGBEE
    monkeypatch.setitem(ZIGBEE, "drop_dbm", -39.0)
    doors = {}
    links, clock, rt, heard, records = layer(doors=doors)
    assert not links.quality("zigbee/lamp").connected and links.quality("zigbee/door").connected
    rt.report(clock, "tw-lamp", "state", "ON")
    rt.report(clock, "tw-door", "contact", True)
    clock.advance(1)
    assert [f for _, _, f, _ in heard] == ["contact"]
    doors["hall-porch"] = False
    links.refresh()
    rt.report(clock, "tw-door", "contact", False)
    clock.advance(1)
    assert [f for _, _, f, _ in heard] == ["contact"]
    assert [(r["link"], r["event"], r.get("reason")) for r in records if r.get("event") in ("down", "up")] == [
        ("zigbee/lamp", "down", "out of range"), ("zigbee/door", "down", "out of range")]


def test_an_effect_on_an_unknown_link_or_of_an_unknown_kind_is_refused():
    links, *_ = layer()
    with pytest.raises(NetworkError, match="unknown link 'zigbee/sofa'"):
        links.apply(Effect("delay", link="zigbee/sofa", hold_s=1))
    with pytest.raises(NetworkError, match="'jam' is not one of"):
        links.apply(Effect("jam", link="coordinator"))
    with pytest.raises(NetworkError, match="throttle acts on a TCP link's bytes: the live network"):
        links.apply(Effect("throttle", link="coordinator", rate_bps=100))


def test_an_effect_round_trips_as_a_trace_input():
    e = Effect("delay", link="coordinator", direction="up", device="door", feature="contact", hold_s=40,
               until=120.0)
    assert Effect.from_dict(e.to_dict()) == e


def test_a_link_is_up_only_in_range_and_not_cut_so_a_cut_ending_on_a_dead_link_says_nothing(monkeypatch):
    from vesper.lab.network import ZIGBEE
    monkeypatch.setitem(ZIGBEE, "drop_dbm", -39.0)  # the lamp's hop is out of range from the start
    links, clock, rt, heard, records = layer()
    links.apply(Effect("cut", link="zigbee/lamp"))
    links.clear("zigbee/lamp")
    rt.report(clock, "tw-lamp", "state", "ON")
    clock.advance(1)
    assert [(r["link"], r["event"], r.get("reason")) for r in records] == [
        ("zigbee/lamp", "down", "out of range"), ("zigbee/lamp", "dropped", "out of range")]
