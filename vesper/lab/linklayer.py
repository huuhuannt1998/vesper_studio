"""The in-process link layer (security-lab spec §3.2, §3.7, plan W7a1): between a twin's report and whoever hears it
(the bridge, VESPER's automation engine), and between a command and its device, across the device's links.

Each Zigbee device's hop to the coordinator is always carried here (it is not IP). On the logical clock, where no
socket exists, the coordinator's and the hub's TCP links are carried here too; with Home Assistant in the loop the
relay (`vesper.lab.relay`) carries them instead, with their real traffic. A link adds its latency (with jitter from
a seeded generator, so a replay is exact), and the effects an attack puts on it: delay, drop, duplicate, reorder,
cut. One device's messages keep their order in each direction, as on its stream, unless an effect reorders them.

Every message an effect touched, and every time a link goes down or up, is a `net` record in the trace.
"""
from __future__ import annotations

import dataclasses
import math
import random
from dataclasses import dataclass
from typing import Callable

from vesper.lab.network import Network, NetworkError, Quality, link_quality
from vesper.lab.worldmap import WorldMap, suggest

KINDS = ("delay", "drop", "duplicate", "reorder", "throttle", "cut")
DIRECTIONS = ("up", "down", "both")  # up: from the device towards Home Assistant


@dataclass(frozen=True)
class Effect:
    """What an attack does to a link's messages. `device` and `feature` narrow it to some messages (None: all);
    `hold_s` is a delay's hold, a duplicate's gap or a reorder's hold; `rate_bps` a throttle's rate; `until` the
    session time at which it ends by itself (None: when cleared); `keep_connection` keeps a held TCP session alive
    (the relay lets its keepalives and acknowledgements pass)."""
    kind: str
    link: str
    direction: str = "both"
    device: str | None = None
    feature: str | None = None
    hold_s: float = 0.0
    rate_bps: float | None = None
    keep_connection: bool = True
    until: float | None = None

    def matches(self, link: str, direction: str, device: str | None, feature) -> bool:
        """`feature`: the message's feature, or a tuple of them (a command that sets several at once)."""
        names = feature if isinstance(feature, tuple) else (feature,)
        return (self.link == link and self.direction in (direction, "both")
                and self.device in (None, device) and (self.feature is None or self.feature in names))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Effect:
        return cls(**d)


def check_effect(effect: Effect, network: Network) -> None:
    if effect.kind not in KINDS:
        raise NetworkError(f"effect: {effect.kind!r} is not one of {', '.join(KINDS)}")
    if effect.direction not in DIRECTIONS:
        raise NetworkError(f"effect: direction {effect.direction!r} is not one of {', '.join(DIRECTIONS)}")
    if effect.link not in network.links:
        raise NetworkError(f"effect: unknown link {effect.link!r}; {suggest(effect.link, list(network.links))}")
    if effect.hold_s < 0 or not math.isfinite(effect.hold_s):
        raise NetworkError(f"effect: hold_s {effect.hold_s} is not a duration")


class LinkLayer:
    def __init__(self, network: Network, world_map: WorldMap, clock, runtime, names: dict[str, str],
                 record: Callable[[dict], None], *, seed: int, carry_tcp: bool,
                 doors: Callable[[], dict[str, bool]]):
        self.network, self.world_map, self.clock, self.runtime = network, world_map, clock, runtime
        self.names, self._record, self.carry_tcp, self._doors = names, record, carry_tcp, doors
        self._rng = {name: random.Random(f"{seed}:{name}") for name in network.links}
        self._listeners: list[Callable] = []
        self._effects: list[Effect] = []
        self._quality: dict[str, Quality] = {}
        self._up: dict[str, bool] = {}  # a link is up when it is in range and not cut
        self._last: dict[tuple[str, str], float] = {}  # (device, direction) -> its last delivery time

    # -- set-up ---------------------------------------------------------------------------------
    def start(self) -> None:
        self.runtime.subscribe(self._on_report)
        self.refresh()

    def subscribe(self, fn: Callable) -> None:
        """`fn(event)` hears a twin's report once it has crossed the device's links."""
        self._listeners.append(fn)

    def carried(self, device: str) -> list[str]:
        """The links of the device's path this layer carries, the device's end first."""
        return [name for name in self.network.path(device) if self.carry_tcp or not self.network.links[name].tcp]

    def quality(self, link: str) -> Quality:
        return self._quality[link]

    def is_up(self, link: str) -> bool:
        """In range and not cut."""
        return self._up.get(link, True)

    def refresh(self) -> None:
        """Each link's quality from the home as it is now (a door shut or opened changes what a hop crosses)."""
        doors = self._doors()
        for name, link in self.network.links.items():
            self._quality[name] = link_quality(link.medium, *link.ends, self.world_map, doors)
            self._settle(name, "out of range", "in range")

    def _cut(self, link: str) -> bool:
        return any(e.kind == "cut" and e.link == link for e in self._effects)

    def _settle(self, link: str, down_reason: str, up_reason: str) -> None:
        """Say so when a link goes down or comes up: up is in range and not cut, whatever changed."""
        up = self._quality[link].connected and not self._cut(link)
        before = self._up.get(link, True)
        self._up[link] = up
        if before and not up:
            self._net(link, "down", reason=down_reason)
        elif up and not before:
            self._net(link, "up", reason=up_reason)

    # -- effects --------------------------------------------------------------------------------
    @property
    def effects(self) -> list[Effect]:
        return list(self._effects)

    def apply(self, effect: Effect) -> None:
        check_effect(effect, self.network)
        if effect.kind == "throttle":
            raise NetworkError("effect: throttle acts on a TCP link's bytes: the live network (vesper lab play --hub)")
        if self.network.links[effect.link].tcp and not self.carry_tcp:
            raise NetworkError(f"effect: the {effect.link} link is carried by the relay here, not in-process")
        self._effects.append(effect)
        if effect.kind == "cut":
            self._settle(effect.link, "cut", "cut ended")
        if effect.until is not None:
            self.clock.call_at(effect.until, lambda: self._end(effect))

    def clear(self, link: str | None = None) -> None:
        for e in [e for e in self._effects if link in (None, e.link)]:
            self._end(e)

    def _end(self, effect: Effect) -> None:
        if effect in self._effects:
            self._effects.remove(effect)
            if effect.kind == "cut":
                self._settle(effect.link, "cut", "cut ended")

    # -- traffic --------------------------------------------------------------------------------
    def down(self, device: str, feature, deliver: Callable[[], None]) -> None:
        """A command towards `device` (setting `feature`, or a tuple of features): `deliver()` runs when it arrives,
        or never if a link loses it."""
        self._send(device, "down", feature, deliver)

    def _on_report(self, event) -> None:
        device = self.names.get(event.twin_id)
        if device is None or not self.network.path(device):
            self._deliver(event)  # not on the network yet (plan W7a3): heard at once, as before
            return
        self._send(device, "up", event.feature, lambda: self._deliver(event))

    def _send(self, device: str, direction: str, feature: str | None, deliver: Callable[[], None]) -> None:
        now, delay = self.clock.now(), 0.0
        held = duplicate = reorder = None
        for name in self.carried(device):
            if not self._quality[name].connected or self._cut(name):
                self._net(name, "dropped", device=device, feature=feature, direction=direction, sent_t=now,
                          reason="out of range" if not self._quality[name].connected else "cut")
                return
            latency = self._quality[name].latency_s
            if latency:
                delay += latency * (0.5 + self._rng[name].random())
            for e in self._effects:
                if not e.matches(name, direction, device, feature):
                    continue
                if e.kind == "drop":
                    self._net(name, "dropped", device=device, feature=feature, direction=direction, sent_t=now,
                              effect="drop")
                    return
                if e.kind == "delay":
                    delay += e.hold_s
                    held = (name, e)
                elif e.kind == "duplicate":
                    duplicate = (name, e)
                elif e.kind == "reorder":
                    delay += e.hold_s
                    reorder = (name, e)
        when = now + delay
        if reorder is None:  # one device's stream keeps its order
            key = (device, direction)
            when = max(when, self._last.get(key, -math.inf))
            self._last[key] = when
        self.clock.call_at(when, deliver)
        for touched, event in ((held, "held"), (reorder, "reordered")):
            if touched is not None:
                self._net(touched[0], event, device=device, feature=feature, direction=direction, sent_t=now,
                          deliver_t=when, effect=touched[1].kind)
        if duplicate is not None:
            again = when + duplicate[1].hold_s
            self.clock.call_at(again, deliver)
            self._net(duplicate[0], "duplicated", device=device, feature=feature, direction=direction, sent_t=now,
                      deliver_t=again, effect="duplicate")

    def _deliver(self, event) -> None:
        for fn in self._listeners:
            fn(event)

    def _net(self, link: str, event: str, **fields) -> None:
        self._record({"kind": "net", "link": link, "event": event, **fields})
