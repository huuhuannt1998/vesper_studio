"""The home's network (security-lab spec §3, plan W7a1): a router, the hub wired to it, the Zigbee coordinator on
the hub or on its own host, and each device's way to Home Assistant, link by link.

A link's quality comes from the home itself: a log-distance path-loss model (ITU-R P.1238's indoor form) with a
loss per wall and per shut door, walls taken from the home's map. The constants are typical published values,
not measured on a device, and are marked as assumed wherever they show.

`vesper.lab.relay` carries the TCP links' real traffic; `vesper.lab.linklayer` carries the Zigbee hops.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from vesper.lab.geometry import Point, centroid, point_in_polygon, polygon_area
from vesper.lab.worldmap import MapError, WorldMap

# Assumed radio constants (typical datasheet and ITU-R P.1238 values; no device was measured).
WIFI = {"tx_dbm": 20.0, "pl1m_db": 40.0, "exponent": 3.0, "wall_db": 5.0, "door_db": 3.0,
        "drop_dbm": -85.0, "base_s": 0.003, "retry_s": 0.002}
ZIGBEE = {"tx_dbm": 8.0, "pl1m_db": 40.0, "exponent": 3.0, "wall_db": 5.0, "door_db": 3.0,
          "drop_dbm": -95.0, "base_s": 0.015, "retry_s": 0.010}
RADIOS = {"wifi": WIFI, "zigbee": ZIGBEE}
ETHERNET_S = 0.0005
MEDIA = ("internal", "ethernet", "wifi", "zigbee")
LOSS_SCALE_DB = 4.0  # a link loses half its frames at the drop threshold, e-fold less every 4 dB above it


class NetworkError(ValueError):
    """A `network:` section that does not fit the home; the message says where."""


@dataclass(frozen=True)
class Place:
    room: str | None
    position: Point
    medium: str = "internal"  # how this host reaches the router (the router itself: internal)


@dataclass(frozen=True)
class Link:
    name: str
    medium: str
    ends: tuple[Point, Point]
    tcp: bool  # real TCP traffic through the relay (else carried in-process)
    devices: tuple[str, ...]  # the devices whose traffic crosses it


@dataclass(frozen=True)
class Quality:
    rssi_dbm: float | None  # None for a wired or internal link
    walls: int
    doors: int  # shut doors crossed
    connected: bool
    loss: float  # the share of frames lost on the air (each costs a retry; TCP and the MAC hide it as delay)
    latency_s: float


@dataclass
class Network:
    router: Place
    hub: Place
    coordinator: Place
    links: dict[str, Link]
    unlinked: dict[str, str] = field(default_factory=dict)  # device -> why it is not on the network yet

    def path(self, device: str) -> list[str]:
        """The links a device's traffic crosses on its way to Home Assistant, the device's end first."""
        return [name for name in (f"zigbee/{device}", "coordinator", "hub")
                if name in self.links and device in self.links[name].devices]


def is_zigbee(device) -> bool:
    return "zigbee" in device.record.get("binding", {})


def parse_network(raw, world_map: WorldMap, devices) -> Network:
    """The `network:` section (or None) of a home with this map and these placed devices."""
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise NetworkError("network: a mapping (router, coordinator)")
    for key in raw:
        if key not in ("router", "coordinator"):
            raise NetworkError(f"network: unknown key {key!r} (router, coordinator)")
    router_raw = _mapping(raw.get("router"), "network.router")
    room = router_raw.get("room") or _largest_room(world_map)
    router = Place(room, _position(world_map, room, router_raw.get("xy"), "network.router"))
    hub = Place(room, router.position, "ethernet")
    coord_raw = _mapping(raw.get("coordinator"), "network.coordinator")
    if coord_raw.get("on", "hub" if not coord_raw.get("room") else None) == "hub":
        coordinator = Place(hub.room, hub.position, "internal")
    else:
        medium = coord_raw.get("link", "wifi")
        if medium not in ("ethernet", "wifi"):
            raise NetworkError(f"network.coordinator.link: {medium!r} is not one of ethernet, wifi")
        c_room = coord_raw.get("room")
        coordinator = Place(c_room, _position(world_map, c_room, coord_raw.get("xy"), "network.coordinator"), medium)
    zigbee = [d for d in devices if is_zigbee(d)]
    links = {
        "hub": Link("hub", "internal", (hub.position, hub.position), True, tuple(d.name for d in zigbee)),
        "coordinator": Link("coordinator", coordinator.medium, (coordinator.position, router.position), True,
                            tuple(d.name for d in zigbee)),
    }
    for d in zigbee:
        links[f"zigbee/{d.name}"] = Link(f"zigbee/{d.name}", "zigbee", (d.position, coordinator.position), False,
                                         (d.name,))
    unlinked = {d.name: "a Matter device: on the network with plan W7a3" for d in devices
                if not is_zigbee(d) and "matter" in d.record.get("binding", {})}
    unlinked.update({d.name: "not on any network yet" for d in devices
                     if not is_zigbee(d) and d.name not in unlinked})
    return Network(router, hub, coordinator, links, unlinked)


def out_of_range(network: Network, world_map: WorldMap, doors: dict[str, bool]) -> list[str]:
    """The Zigbee devices whose hop to the coordinator is out of range as the home starts: with no mesh routing
    yet (a device relaying for another), they are never heard."""
    return [link.devices[0] for name, link in network.links.items()
            if link.medium == "zigbee" and not link_quality("zigbee", *link.ends, world_map, doors).connected]


def walls_between(world_map: WorldMap, a: Point, b: Point, doors: dict[str, bool]) -> tuple[int, int]:
    """The walls and the shut doors a straight line from `a` to `b` crosses. A wall is a stretch of cells that are
    neither walkable nor in any room (furniture stands inside a room, and is not counted); a doorway's cells count
    as a door when its door is shut."""
    g = world_map.grid
    steps = max(1, math.ceil(math.dist(a, b) / (g.cell / 2)))
    walls = shut = 0
    last = "room"
    for k in range(steps + 1):
        x, z = a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps
        kind = _kind(world_map, x, z, doors)
        if kind != last:
            walls += kind == "wall"
            shut += kind == "door"
        last = kind
    return walls, shut


def link_quality(medium: str, a: Point, b: Point, world_map: WorldMap, doors: dict[str, bool]) -> Quality:
    if medium == "internal":
        return Quality(None, 0, 0, True, 0.0, 0.0)
    if medium == "ethernet":
        return Quality(None, 0, 0, True, 0.0, ETHERNET_S)
    radio = RADIOS[medium]
    walls, shut = walls_between(world_map, a, b, doors)
    d = max(1.0, math.dist(a, b))
    rssi = (radio["tx_dbm"] - radio["pl1m_db"] - 10 * radio["exponent"] * math.log10(d)
            - radio["wall_db"] * walls - radio["door_db"] * shut)
    margin = rssi - radio["drop_dbm"]
    if margin < 0:
        return Quality(rssi, walls, shut, False, 1.0, math.inf)
    loss = min(0.5, 0.5 * math.exp(-margin / LOSS_SCALE_DB))
    return Quality(rssi, walls, shut, True, loss, radio["base_s"] + radio["retry_s"] * loss / (1 - loss))


def _kind(world_map: WorldMap, x: float, z: float, doors: dict[str, bool]) -> str:
    g = world_map.grid
    cell = g.cell_of(x, z)
    if cell is None:
        return "open"  # beyond the floor: open air
    i, j = cell
    if g.is_walkable(i, j):
        if world_map.room_of_cell(i, j) is not None:
            return "room"
        for d in world_map.doorways:
            if cell in d.cells:
                return "door" if doors.get(d.id) is False else "room"
        return "room"
    if any(point_in_polygon((x, z), r.polygon) for r in world_map.rooms):
        return "room"  # furniture
    return "wall"


def _largest_room(world_map: WorldMap) -> str:
    indoor = [r for r in world_map.rooms if not r.outdoor] or world_map.rooms
    return max(indoor, key=lambda r: polygon_area(r.polygon)).id


def _position(world_map: WorldMap, room, xy, where: str) -> Point:
    if room is None:
        raise NetworkError(f"{where}.room: name the room it stands in")
    try:
        r = world_map.room(str(room))
    except MapError as e:
        raise NetworkError(f"{where}.room: {e}") from e
    if xy is None:
        return centroid(r.polygon)
    if not (isinstance(xy, list | tuple) and len(xy) == 2 and all(isinstance(v, int | float) for v in xy)):
        raise NetworkError(f"{where}.xy: [x, z] in metres")
    p = (float(xy[0]), float(xy[1]))
    if not point_in_polygon(p, r.polygon):
        raise NetworkError(f"{where}.xy: {p} is not in the {r.id}")
    return p


def _mapping(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise NetworkError(f"{where}: a mapping")
    return value
