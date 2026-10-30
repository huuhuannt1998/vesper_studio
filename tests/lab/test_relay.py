"""The relay (security-lab spec §3.4, plan W7a1): real TCP between VESPER's own services, on 127.0.0.1 only, through
which an attack delays, drops, duplicates, reorders, throttles or cuts whole MQTT messages. Tested with real sockets
on loopback: an upstream that records what reaches it, and a client that speaks MQTT's framing."""
import json
import socket
import threading
import time

import pytest

from vesper.lab.linklayer import Effect
from vesper.lab.relay import Relay, RelayError, split_packets

PINGREQ = b"\xc0\x00"


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (128 if n else 0))
        if not n:
            return bytes(out)


def connect(level: int = 4) -> bytes:
    body = b"\x00\x04MQTT" + bytes([level, 2]) + b"\x00\x3c" + (b"\x00" if level == 5 else b"") + b"\x00\x01c"
    return b"\x10" + varint(len(body)) + body


def publish(topic: str, payload: dict, qos: int = 1, pid: int = 1, level: int = 4) -> bytes:
    t = topic.encode()
    body = len(t).to_bytes(2, "big") + t + (pid.to_bytes(2, "big") if qos else b"")
    body += (b"\x00" if level == 5 else b"") + json.dumps(payload).encode()
    return bytes([0x30 | (qos << 1)]) + varint(len(body)) + body


class Upstream:
    """A TCP server on loopback that records each byte it receives, with the time, and can answer."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.got: list[tuple[float, bytes]] = []
        self.conns: list[socket.socket] = []
        self.closed = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._read, args=(c,), daemon=True).start()

    def _read(self, c):
        while True:
            try:
                data = c.recv(65536)
            except OSError:
                data = b""
            if not data:
                self.closed.set()
                return
            self.got.append((time.monotonic(), data))

    def packets(self) -> list[bytes]:
        packets, rest = split_packets(b"".join(d for _, d in self.got))
        assert rest == b""
        return packets

    def arrived(self, packet: bytes) -> float | None:
        buf, t0 = b"", None
        for t, d in self.got:
            buf += d
            if packet in buf:
                return t
        return t0

    def close(self):
        self.sock.close()
        for c in self.conns:
            c.close()


@pytest.fixture
def net():
    up = Upstream()
    records = []
    relay = Relay(record=records.append, allowed={up.port}, now=time.monotonic)
    relay.start()
    port = relay.add_link("coordinator", up.port, client_is_device=True)
    yield relay, up, port, records
    relay.stop()
    up.close()


def dial(port):
    c = socket.create_connection(("127.0.0.1", port))
    c.settimeout(5)
    return c


def wait(check, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_mqtt_packets_are_split_from_a_byte_stream_even_across_reads():
    a, b = publish("zigbee2mqtt/door", {"contact": False}), PINGREQ
    assert split_packets(a + b) == ([a, b], b"")
    assert split_packets(a[:5]) == ([], a[:5])
    big = publish("t", {"x": "y" * 300})  # a two-byte remaining length
    assert split_packets(big + a[:3]) == ([big], a[:3])


def test_traffic_passes_unchanged_both_ways_with_no_effect(net):
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect() + publish("zigbee2mqtt/door", {"contact": True}))
    assert wait(lambda: len(up.packets()) == 2)
    up.conns[0].sendall(b"\x20\x02\x00\x00")  # CONNACK, back to the client
    assert c.recv(4) == b"\x20\x02\x00\x00"
    assert records == []
    conn, = relay._links["coordinator"].conns  # the relay's own sockets send each message at once (no Nagle)
    assert all(t.get_extra_info("socket").getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) for t in conn.transports)


@pytest.mark.parametrize("allowed, port, message", [
    ({1883}, 1883, "never the standard port 1883"), ({8123}, 8123, "never the standard port 8123"),
    (set(), 5555, "5555 is not a service this session started")])
def test_the_relay_refuses_any_upstream_but_the_session_s_own(allowed, port, message):
    relay = Relay(record=lambda r: None, allowed=allowed, now=time.monotonic)
    with pytest.raises(RelayError, match=message):
        relay.add_link("coordinator", port, client_is_device=True)


def test_a_held_report_arrives_late_while_keepalives_pass_and_the_reports_behind_it_wait(net):
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect())
    relay.apply(Effect("delay", link="coordinator", direction="up", device="door", feature="contact", hold_s=0.6))
    held, later = publish("zigbee2mqtt/door", {"contact": False}), publish("zigbee2mqtt/plug", {"state": "ON"}, pid=2)
    t0 = time.monotonic()
    c.sendall(held + PINGREQ + later)
    assert wait(lambda: up.arrived(PINGREQ) is not None)  # the session stays alive
    assert up.arrived(held) is None and up.arrived(later) is None
    assert wait(lambda: up.arrived(later) is not None)
    assert up.arrived(held) - t0 >= 0.55 and up.arrived(later) >= up.arrived(held)
    assert [p for p in up.packets() if p[0] >> 4 == 3] == [held, later]  # in order, as TCP keeps them
    h, = [r for r in records if r["event"] == "held"]
    assert (h["link"], h["device"], h["feature"], h["direction"]) == ("coordinator", "door", "contact", "up")


def test_without_keep_connection_the_keepalive_waits_behind_the_held_report_too(net):
    relay, up, port, _ = net
    c = dial(port)
    relay.apply(Effect("delay", link="coordinator", direction="up", hold_s=0.5, keep_connection=False))
    held = publish("zigbee2mqtt/door", {"contact": False})
    t0 = time.monotonic()
    c.sendall(held + PINGREQ)
    assert wait(lambda: up.arrived(PINGREQ) is not None)
    assert up.arrived(PINGREQ) - t0 >= 0.45


def test_a_dropped_report_never_arrives_and_the_others_do(net):
    relay, up, port, records = net
    c = dial(port)
    relay.apply(Effect("drop", link="coordinator", direction="up", device="door"))
    door, plug = publish("zigbee2mqtt/door", {"contact": False}), publish("zigbee2mqtt/plug", {"state": "ON"})
    c.sendall(door + plug)
    assert wait(lambda: up.arrived(plug) is not None)
    time.sleep(0.1)
    assert up.arrived(door) is None
    assert [r["event"] for r in records] == ["dropped"]


def test_a_command_down_the_link_is_held_too(net):
    relay, up, port, _ = net
    c = dial(port)
    c.sendall(connect())
    assert wait(lambda: up.conns)
    relay.apply(Effect("delay", link="coordinator", direction="down", device="lock", hold_s=0.4))
    cmd = publish("zigbee2mqtt/lock/set", {"state": "LOCK"})
    t0 = time.monotonic()
    up.conns[0].sendall(cmd)  # the broker sends Home Assistant's command to the bridge
    assert c.recv(len(cmd)) == cmd
    assert time.monotonic() - t0 >= 0.35


def test_a_duplicate_arrives_twice_and_a_reordered_report_is_overtaken(net):
    relay, up, port, _ = net
    c = dial(port)
    relay.apply(Effect("duplicate", link="coordinator", direction="up", device="door", hold_s=0.1))
    door = publish("zigbee2mqtt/door", {"contact": False})
    c.sendall(door)
    assert wait(lambda: b"".join(d for _, d in up.got).count(door) == 2)
    relay.clear()
    relay.apply(Effect("reorder", link="coordinator", direction="up", device="door", hold_s=0.3))
    first, second = publish("zigbee2mqtt/door", {"contact": True}, pid=7), publish("zigbee2mqtt/plug", {"x": 1}, pid=8)
    c.sendall(first + second)
    assert wait(lambda: up.arrived(first) is not None)
    assert up.arrived(second) < up.arrived(first)


def test_a_reset_cut_aborts_the_connection_without_a_goodbye_and_refuses_new_ones_until_cleared(net):
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect())
    assert wait(lambda: up.conns)
    relay.apply(Effect("cut", link="coordinator", keep_connection=False))  # a reset, not a silent loss
    assert up.closed.wait(2)  # the broker sees the socket die: it publishes the client's will
    assert b"\xe0\x00" not in b"".join(d for _, d in up.got)  # no MQTT DISCONNECT passed
    c2 = dial(port)
    assert c2.recv(10) == b""  # refused while cut
    relay.clear("coordinator")
    c3 = dial(port)
    c3.sendall(PINGREQ)
    assert wait(lambda: up.arrived(PINGREQ) is not None)
    assert [(r["link"], r["event"]) for r in records] == [("coordinator", "down"), ("coordinator", "up")]


def test_a_silent_cut_passes_nothing_either_way_and_delivers_what_waited_when_the_link_returns(net):
    """A lost link: the connection stays open, nothing passes, TCP retransmits what was sent once the link is
    back; the endpoints notice only by their own keepalive timeouts."""
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect())
    assert wait(lambda: up.conns)
    relay.apply(Effect("cut", link="coordinator"))
    door = publish("zigbee2mqtt/door", {"contact": False})
    c.sendall(door)
    up.conns[0].sendall(PINGREQ)
    time.sleep(0.3)
    assert up.arrived(door) is None and not up.closed.is_set()
    c.settimeout(0.2)
    with pytest.raises(socket.timeout):
        c.recv(2)
    relay.clear("coordinator")
    assert wait(lambda: up.arrived(door) is not None)
    c.settimeout(2)
    assert c.recv(2) == PINGREQ
    assert [(r["link"], r["event"]) for r in records] == [("coordinator", "down"), ("coordinator", "up")]


def test_a_throttle_limits_the_bytes_a_second(net):
    relay, up, port, _ = net
    c = dial(port)
    relay.apply(Effect("throttle", link="coordinator", direction="up", rate_bps=8000))  # 1000 bytes a second
    msgs = [publish("zigbee2mqtt/plug", {"pad": "x" * 180}, pid=k) for k in range(1, 6)]  # ~1 kB in all
    t0 = time.monotonic()
    c.sendall(b"".join(msgs))
    assert wait(lambda: up.arrived(msgs[-1]) is not None)
    assert up.arrived(msgs[-1]) - t0 >= 0.7


def test_mqtt_5_publishes_are_read_past_their_properties(net):
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect(level=5))
    relay.apply(Effect("drop", link="coordinator", direction="up", feature="contact"))
    door = publish("zigbee2mqtt/door", {"contact": False}, level=5)
    plug = publish("zigbee2mqtt/plug", {"state": "ON"}, level=5)
    c.sendall(door + plug)
    assert wait(lambda: up.arrived(plug) is not None)
    assert up.arrived(door) is None


def test_a_message_resent_after_a_reconnect_is_named_in_the_trace(net):
    """paho resends an unacknowledged QoS 1 message after a reconnect, with MQTT's DUP flag: it can arrive late or
    twice, and the trace says which message it was."""
    relay, up, port, records = net
    c = dial(port)
    resent = bytearray(publish("zigbee2mqtt/door", {"contact": False}))
    resent[0] |= 0x08  # DUP
    c.sendall(bytes(resent))
    assert wait(lambda: up.arrived(bytes(resent)) is not None)
    r, = records
    assert (r["event"], r["link"], r["device"], r["feature"], r["direction"]) == (
        "resent", "coordinator", "door", "contact", "up")


@pytest.mark.parametrize("effect", [None, Effect("delay", link="coordinator", direction="up", device="door",
                                                 hold_s=0.3)])
def test_a_sender_that_closes_has_what_it_sent_delivered_first_then_the_close(net, effect):
    """As TCP: the bytes before a FIN arrive before it. A graceful MQTT disconnect (DISCONNECT, then close) must
    reach the broker, or it publishes the client's will as for a crash; a held message is not lost by the close."""
    relay, up, port, _ = net
    if effect is not None:
        relay.apply(effect)
    c = dial(port)
    door, bye = publish("zigbee2mqtt/door", {"contact": False}), b"\xe0\x00"
    c.sendall(connect() + door + bye)
    c.close()
    assert up.closed.wait(3)
    got = b"".join(d for _, d in up.got)
    assert door in got and got.endswith(bye)


def text_publish(topic: str, payload: str, qos: int = 1, pid: int = 1) -> bytes:
    t = topic.encode()
    body = len(t).to_bytes(2, "big") + t + (pid.to_bytes(2, "big") if qos else b"") + payload.encode()
    return bytes([0x30 | (qos << 1)]) + varint(len(body)) + body


def test_an_effect_finds_a_tasmota_device_by_its_name_through_what_it_identifies(net):
    """A Tasmota device's topics carry its MQTT topic (tasmota_XXXXXX), not its name; the device says which of its
    features a message carries, so an effect aimed at `kettle` and `POWER` finds HA's `POWER1 ON`."""
    relay, up, port, records = net
    relay.add_device("tasmota_0A0B0C", lambda parts, payload: ("kettle", ("POWER",) if parts[0] == "cmnd" else ()))
    c = dial(port)
    relay.apply(Effect("drop", link="coordinator", direction="up", device="kettle", feature="POWER"))
    cmd, lwt = text_publish("cmnd/tasmota_0A0B0C/POWER1", "ON"), text_publish("tele/tasmota_0A0B0C/LWT", "Online")
    c.sendall(cmd + lwt)
    assert wait(lambda: up.arrived(lwt) is not None)
    time.sleep(0.1)
    assert up.arrived(cmd) is None
    assert [(r["event"], r["device"], r["feature"]) for r in records] == [("dropped", "kettle", "POWER")]


def test_a_lost_station_passes_nothing_and_is_refused_until_it_rejoins_on_a_new_connection(net):
    """A Wi-Fi device out of range: what it sent never arrives (its old TCP connection dies with the association),
    new connections are refused, and once it rejoins its old connection is gone and a new one passes. The link
    layer records the link's state; the relay records nothing of its own."""
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect())
    assert wait(lambda: up.conns)
    relay.lose("coordinator")
    report = publish("zigbee2mqtt/door", {"contact": False})
    c.sendall(report)
    time.sleep(0.2)
    assert up.arrived(report) is None
    assert dial(port).recv(10) == b""  # refused while lost
    relay.rejoin("coordinator")
    assert up.closed.wait(2)  # the old connection is gone: the broker publishes its will
    c.settimeout(2)
    try:
        assert c.recv(10) == b""  # closed, or reset
    except ConnectionResetError:
        pass
    time.sleep(0.2)
    assert up.arrived(report) is None  # what waited on the dead connection is never delivered
    c2 = dial(port)
    c2.sendall(PINGREQ)
    assert wait(lambda: up.arrived(PINGREQ) is not None)
    # what it sent out of range is named in the trace, as the in-process link layer names it
    assert [(r["event"], r["device"], r["direction"], r["reason"]) for r in records] == [
        ("dropped", "door", "up", "out of range")]


def test_home_assistant_s_command_to_a_lost_station_is_dropped_and_named(net):
    relay, up, port, records = net
    c = dial(port)
    c.sendall(connect())
    assert wait(lambda: up.conns)
    relay.lose("coordinator")
    up.conns[0].sendall(text_publish("cmnd/tasmota_0A0B0C/POWER1", "ON"))
    assert wait(lambda: records)
    assert [(r["event"], r["device"], r["direction"], r["reason"]) for r in records] == [
        ("dropped", "tasmota_0A0B0C", "down", "out of range")]
    up.conns[0].sendall(PINGREQ)
    time.sleep(0.2)
    c.settimeout(0.2)
    with pytest.raises(socket.timeout):
        c.recv(2)  # nothing reaches a station out of range


def test_a_cut_that_ends_while_the_station_is_lost_releases_nothing(net):
    relay, up, port, _ = net
    c = dial(port)
    c.sendall(connect())
    assert wait(lambda: up.conns)
    relay.lose("coordinator")
    relay.apply(Effect("cut", link="coordinator"))
    relay.clear("coordinator")
    assert dial(port).recv(10) == b""  # still lost: the attack's end does not bring the station back
