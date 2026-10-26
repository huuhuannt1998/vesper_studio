"""The relay (security-lab spec §3.4, §7, plan W7a1): real TCP between VESPER's own services, through which an attack
acts on whole MQTT messages.

One relay per session, on 127.0.0.1 only. Each link listens on a port of its own and forwards to one service the
session started (its broker); any other upstream, and the standard ports 1883 and 8123 always, are refused before
anything is opened. The relay reads MQTT's framing (3.1.1 and 5), so an effect holds, drops, duplicates or reorders
whole messages, chosen by direction, device (a topic's second level: Zigbee2MQTT's friendly name) and feature (a key
of the JSON payload):
- a `delay` holds the matching messages, and the messages behind them on the stream wait too, as TCP keeps order;
  with `keep_connection` the keepalives and acknowledgements pass meanwhile, as an attacker who keeps the session
  alive lets them (Phantom-Delay);
- a `cut` is a lost link: nothing passes either way and what was sent waits for the link to return (TCP
  retransmits it), the endpoints noticing only by their own keepalive timeouts; with `keep_connection: false` it is
  a reset: the sockets are aborted at once, with no MQTT DISCONNECT, so the broker publishes the client's will, and
  new connections are refused until the cut ends;
- a `throttle` paces the bytes of a direction.

A QoS 1 message resent after a reconnect (MQTT's DUP flag) is named in the trace: it may arrive late or twice.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
from dataclasses import dataclass, field
from typing import Callable

from vesper.lab.linklayer import DIRECTIONS, KINDS, Effect
from vesper.lab.network import NetworkError

HOST = "127.0.0.1"
FORBIDDEN = (1883, 8123)  # the standard MQTT and Home Assistant ports: never VESPER's, never touched
# What a session-keeping hold lets pass: the acknowledgements (PUBACK, PUBREC, PUBREL, PUBCOMP, SUBACK, UNSUBACK)
# and the keepalives (PINGREQ, PINGRESP); a CONNECT, a SUBSCRIBE or a DISCONNECT keeps its place in the stream.
KEEPALIVE = frozenset({4, 5, 6, 7, 9, 11, 12, 13})


class RelayError(NetworkError):
    """A link or an effect the relay refuses; the message says why."""


class SessionSink:
    """The relay's way into a session that starts after it (Home Assistant must be pointed at the relay first):
    records go to the session's trace on its clock, times are the session's, latencies its links'."""

    def __init__(self):
        self.session = None

    def attach(self, session) -> None:
        self.session = session

    def record(self, r: dict) -> None:
        if self.session is not None:
            self.session.net_record(r)

    def now(self) -> float:
        return self.session.clock.now() if self.session is not None else 0.0

    def latency(self, link: str) -> float:
        links = self.session.links if self.session is not None else None
        return links.quality(link).latency_s if links is not None and link in links.network.links else 0.0


def _varint(buf: bytes, i: int) -> tuple[int, int] | None:
    value, mult = 0, 1
    for k in range(4):
        if i + k >= len(buf):
            return None
        b = buf[i + k]
        value += (b & 127) * mult
        if not b & 128:
            return value, i + k + 1
        mult *= 128
    raise RelayError("a malformed MQTT remaining length")


def split_packets(buf: bytes) -> tuple[list[bytes], bytes]:
    """Whole MQTT packets at the start of `buf`, and the bytes of an incomplete one."""
    packets, i = [], 0
    while len(buf) - i >= 2:
        length = _varint(buf, i + 1)
        if length is None or length[1] + length[0] > len(buf):
            break
        packets.append(buf[i:length[1] + length[0]])
        i = length[1] + length[0]
    return packets, buf[i:]


def publish_of(packet: bytes, level: int) -> tuple[str, bytes] | None:
    """A PUBLISH's topic and payload (None for any other packet)."""
    if packet[0] >> 4 != 3:
        return None
    _, i = _varint(packet, 1)
    n = int.from_bytes(packet[i:i + 2], "big")
    topic = packet[i + 2:i + 2 + n].decode("utf-8", "replace")
    i += 2 + n + (2 if (packet[0] >> 1) & 3 else 0)
    if level == 5:
        props, i = _varint(packet, i)
        i += props
    return topic, packet[i:]


def _features(payload: bytes) -> tuple[str, ...]:
    try:
        obj = json.loads(payload)
    except ValueError:
        return ()
    return tuple(obj) if isinstance(obj, dict) else ()


@dataclass(eq=False)
class _Conn:
    transports: list = field(default_factory=list)
    level: int = 4  # MQTT protocol level, from the client's CONNECT


@dataclass(eq=False)
class _Link:
    name: str
    upstream: int
    client_is_device: bool
    server: object = None
    conns: set = field(default_factory=set)
    reset: bool = False  # a reset cut: new connections are refused
    silent: bool = False  # a silent cut: nothing passes
    waiting: list = field(default_factory=list)  # (transport, bytes) held by a silent cut, in order


class _Direction:
    """One direction of one connection: messages written in order, each when it is due."""

    def __init__(self, relay: Relay, link: _Link, transport):
        self.relay, self.link, self.transport = relay, link, transport
        self.queue: asyncio.Queue = asyncio.Queue()
        self.free_at = 0.0  # a throttle's pace
        self.task = relay._loop.create_task(self._run())

    async def _run(self) -> None:
        loop = self.relay._loop
        while True:
            due, data = await self.queue.get()
            if data is None:  # the sender closed: as TCP, what it sent before arrives first, then the close
                if not self.transport.is_closing():
                    self.transport.close()
                return
            if due > loop.time():
                await asyncio.sleep(due - loop.time())
            self.relay._write(self.link, self.transport, data)


class Relay:
    def __init__(self, record: Callable[[dict], None], allowed: set[int], now: Callable[[], float],
                 latency: Callable[[str], float] | None = None):
        """`record(net_record)` hears what the relay did (on the relay's thread); `allowed` are the ports of the
        services this session started; `now()` is the session's time, for the records; `latency(link)` the
        link's own latency in seconds."""
        self._record, self.allowed, self._now, self._latency = record, set(allowed), now, latency or (lambda _: 0.0)
        self._links: dict[str, _Link] = {}
        self._effects: list[Effect] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="vesper-relay", daemon=True)

    # -- life cycle -----------------------------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if not self._thread.is_alive():
            return

        async def close():
            for link in self._links.values():
                link.server.close()
                for conn in link.conns:
                    for t in conn.transports:
                        t.abort()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(close(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop.close()

    def _call(self, fn, *args):
        """Run `fn(*args)` on the relay's loop and return its result."""
        async def run():
            return fn(*args)
        return asyncio.run_coroutine_threadsafe(run(), self._loop).result(10)

    def add_link(self, name: str, upstream_port: int, client_is_device: bool) -> int:
        """Listen for `name`'s clients and forward to the session's service on `upstream_port`; the port to give
        the clients. `client_is_device`: the client is the device's side (its messages go up)."""
        if upstream_port in FORBIDDEN:
            raise RelayError(f"{name}: never the standard port {upstream_port}")
        if upstream_port not in self.allowed:
            raise RelayError(f"{name}: port {upstream_port} is not a service this session started")
        link = _Link(name, upstream_port, client_is_device)

        async def listen():
            link.server = await asyncio.start_server(lambda r, w: self._accept(link, r, w), HOST, 0)
            return link.server.sockets[0].getsockname()[1]
        port = asyncio.run_coroutine_threadsafe(listen(), self._loop).result(10)
        self._links[name] = link
        return port

    # -- effects --------------------------------------------------------------------------------
    @property
    def effects(self) -> list[Effect]:
        return list(self._effects)

    def apply(self, effect: Effect) -> None:
        if effect.kind not in KINDS:
            raise RelayError(f"effect: {effect.kind!r} is not one of {', '.join(KINDS)}")
        if effect.direction not in DIRECTIONS:
            raise RelayError(f"effect: direction {effect.direction!r} is not one of {', '.join(DIRECTIONS)}")
        if effect.link not in self._links:
            raise RelayError(f"effect: the relay carries no link {effect.link!r} ({', '.join(self._links)})")
        if effect.kind == "throttle" and not (effect.rate_bps and effect.rate_bps > 0):
            raise RelayError("effect: a throttle needs rate_bps above 0")
        self._call(self._apply, effect)

    def clear(self, link: str | None = None) -> None:
        self._call(self._clear, link)

    def _apply(self, effect: Effect) -> None:
        self._effects.append(effect)
        if effect.kind == "cut":
            link = self._links[effect.link]
            self._net(link.name, "down", reason="cut" if effect.keep_connection else "reset")
            if effect.keep_connection:
                link.silent = True
            else:
                link.reset = True
                for conn in list(link.conns):
                    for t in conn.transports:
                        t.abort()
        if effect.until is not None:
            self._loop.call_later(max(0.0, effect.until - self._now()), self._end, effect)

    def _clear(self, link: str | None) -> None:
        for e in [e for e in self._effects if link in (None, e.link)]:
            self._end(e)

    def _end(self, effect: Effect) -> None:
        if effect not in self._effects:
            return
        self._effects.remove(effect)
        if effect.kind == "cut":
            link = self._links[effect.link]
            if not any(e.kind == "cut" and e.link == link.name for e in self._effects):
                link.reset = link.silent = False
                self._net(link.name, "up", reason="cut ended")
                waiting, link.waiting = link.waiting, []
                for transport, data in waiting:
                    if not transport.is_closing():
                        transport.write(data)

    # -- traffic --------------------------------------------------------------------------------
    async def _accept(self, link: _Link, reader, writer) -> None:
        if link.reset:
            writer.close()  # refused while the link is reset
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(HOST, link.upstream)
        except OSError:
            writer.close()
            return
        conn = _Conn([writer.transport, up_writer.transport])
        for t in conn.transports:
            sock = t.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        link.conns.add(conn)
        up, down = ("up", "down") if link.client_is_device else ("down", "up")
        await asyncio.gather(self._pump(link, conn, reader, up_writer, up),
                             self._pump(link, conn, up_reader, writer, down))
        link.conns.discard(conn)

    async def _pump(self, link: _Link, conn: _Conn, reader, writer, direction: str) -> None:
        out, buf = _Direction(self, link, writer.transport), b""
        try:
            while data := await reader.read(65536):
                packets, buf = split_packets(buf + data)
                for packet in packets:
                    self._forward(link, conn, packet, direction, out)
        except (OSError, RelayError):  # a reset or a malformed stream: nothing more passes
            out.task.cancel()
            if not writer.transport.is_closing():
                writer.transport.abort()
            return
        out.queue.put_nowait((0.0, None))  # the sender closed: the writer drains what it holds, then closes
        await out.task

    def _forward(self, link: _Link, conn: _Conn, packet: bytes, direction: str, out: _Direction) -> None:
        loop = self._loop
        if packet[0] >> 4 == 1 and len(packet) > 8:  # CONNECT: its protocol level
            _, i = _varint(packet, 1)
            conn.level = packet[i + 2 + int.from_bytes(packet[i:i + 2], "big")]
        due = loop.time() + self._latency(link.name)
        effects = [e for e in self._effects if e.link == link.name and e.direction in (direction, "both")]
        for e in effects:
            if e.kind == "throttle":
                due = max(due, out.free_at)
                out.free_at = due + len(packet) * 8 / e.rate_bps
        pub = publish_of(packet, conn.level)
        if pub is None:  # a keepalive, an acknowledgement, a subscription: not a device's message
            if packet[0] >> 4 in KEEPALIVE and any(e.kind == "delay" and e.keep_connection for e in effects):
                self._write(link, out.transport, packet)  # the attacker lets it pass: the session stays alive
            else:
                out.queue.put_nowait((due, packet))
            return
        topic, payload = pub
        parts, features = topic.split("/"), _features(payload)
        device = parts[1] if len(parts) > 1 else None
        if packet[0] & 0x08:  # DUP: a QoS 1 message resent after a reconnect; it may arrive late or twice
            self._net(link.name, "resent", device=device, feature=features[0] if features else None,
                      direction=direction, sent_t=self._now())
        held = duplicate = reorder = None
        for e in effects:
            if e.kind in ("cut", "throttle") or e.device not in (None, device):
                continue
            if e.feature is not None and e.feature not in features:
                continue
            feature = e.feature or (features[0] if features else None)
            if e.kind == "drop":
                self._net(link.name, "dropped", device=device, feature=feature, direction=direction,
                          sent_t=self._now(), effect="drop")
                return
            if e.kind == "delay":
                due += e.hold_s
                held = (e, feature)
            elif e.kind == "duplicate":
                duplicate = (e, feature)
            elif e.kind == "reorder":
                reorder = (e, feature)
        now = self._now()
        if reorder is not None:
            e, feature = reorder
            loop.call_at(due + e.hold_s, self._write, link, out.transport, packet)
            self._net(link.name, "reordered", device=device, feature=feature, direction=direction, sent_t=now,
                      deliver_t=now + due + e.hold_s - loop.time(), effect="reorder")
            return
        out.queue.put_nowait((due, packet))
        if held is not None:
            self._net(link.name, "held", device=device, feature=held[1], direction=direction, sent_t=now,
                      deliver_t=now + due - loop.time(), effect="delay")
        if duplicate is not None:
            e, feature = duplicate
            loop.call_at(due + e.hold_s, self._write, link, out.transport, packet)
            self._net(link.name, "duplicated", device=device, feature=feature, direction=direction, sent_t=now,
                      deliver_t=now + due + e.hold_s - loop.time(), effect="duplicate")

    def _write(self, link: _Link, transport, data: bytes) -> None:
        if transport.is_closing():
            return
        if link.silent:
            link.waiting.append((transport, data))  # the link is lost: TCP will retransmit it
        else:
            transport.write(data)

    def _net(self, link: str, event: str, **fields) -> None:
        self._record({"kind": "net", "link": link, "event": event, **fields})
