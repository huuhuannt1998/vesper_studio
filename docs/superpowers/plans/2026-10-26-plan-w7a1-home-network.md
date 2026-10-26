# Plan W7a1: the home's network, its core — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every VESPER home has a network, as a real house does (a router, the hub wired to it, the Zigbee coordinator, a hop per Zigbee device), and every device's reports and every command really cross it: with Home Assistant in the loop, its MQTT connection and the bridge's go through a loopback relay on which an attack delays, drops, duplicates, reorders, throttles or cuts whole MQTT messages; without it, the same effects apply in-process on the session's clock.

**Architecture:**
1. **Topology and link quality** (`network.py`): a `network:` section in `home.yaml` (router, coordinator), with a default for every home: the router in the largest indoor room, the hub wired to it, the coordinator on the hub. A radio link's quality comes from the home: ITU-R P.1238's indoor log-distance path loss with a loss per wall and per shut door, the walls read from the map's grid (cells neither walkable nor in any room). The radio constants are typical published values, marked assumed wherever they show.
2. **The link layer** (`linklayer.py`) sits between a twin's report and whoever hears it (the bridge, VESPER's automation engine), and between a command and its device. It always carries the Zigbee hops (not IP); on the logical clock it carries the TCP links too. It adds each link's latency (seeded jitter, so a replay is exact), keeps one device's stream in order, and applies an attack's effects. The trace keeps the report when the device sent it (ground truth) and a `net` record for every message an effect touched.
3. **The relay** (`relay.py`): with Home Assistant in the loop, a real TCP relay on 127.0.0.1 between VESPER's own services: the `hub` link (Home Assistant's MQTT) and the `coordinator` link (the bridge's). It reads MQTT's framing (3.1.1 and 5), so effects act on whole messages; a delay can keep the session alive (Phantom-Delay); a cut is a lost link or a reset (the broker then publishes the bridge's will). Any upstream but the session's own broker, and ports 1883 and 8123 always, are refused before anything opens.
4. **Zigbee2MQTT's availability:** each device's discovery lists the bridge's state too (`availability_mode: all`), the bridge says `online` again after a reconnect, and a device whose hop stays down goes unavailable after Zigbee2MQTT's own timeout (10 min mains, 25 h battery).
5. **The commands:** `vesper lab play --hub` and `vesper lab look --hub` put the session on the live network; `vesper lab network` shows the topology and each link's quality.

**Tech Stack:** Python 3.11+ (asyncio for the relay, paho-mqtt 2.1 for the bridge), Mosquitto and Home Assistant as installed by `vesper setup ha` (opt-in live tests).

**Spec:** `docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md` (§1, §3, §7, §8; W7a1 of W7a: W7a2 the virtual Tasmota devices and W7a3 Matter on the hub follow).

## Global Constraints

- The relay binds 127.0.0.1 only and forwards only to services the session started; ports 1883 and 8123 are refused always (spec §1, the PI's scope of 2026-10-26: no ARP or DNS spoofing, no radio, no LAN, no other host).
- Every device is a virtual twin; nothing touches a real device or another broker.
- Shared Mac (`~/Research/MAC_COORDINATION.md`, `~/Research/BUSY_WINDOWS.json`): unit tests under `taskpolicy -b`; the live tests (a Mosquitto, a Home Assistant) outside every window, each with a ledger row.
- Never name another project of the Mac in a committed file (the repository is public).
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`.

## Review Focus

- **A real HSSD home:** its map's outside band (cells beyond the house, neither walkable nor in a room) counts as one wall, not one per cell, for a device on a porch or a router by a window; `vesper lab network` on a real scene shows plausible signals.
- **A busy wall clock:** the relay's thread hands its records to the session's clock thread; a held report released while the bridge publishes keeps one device's order, and the trace's times stay monotonic.
- **paho's reconnect after a reset cut:** the bridge reconnects (1 to 120 s back-off), says `online`, resubscribes; resent QoS 1 messages are named `resent` in the trace; nothing is published twice by VESPER itself.
- **An effect that ends by itself** (`until`) at or before the current time, and an effect cleared twice: no error, one `up` record.
- **A home with no Zigbee device:** the hub and coordinator links exist with no devices; `vesper lab network` and `play --hub` work.

---
### Task 1: The home's network: topology and link quality

**Files:**
- Create: `vesper/lab/network.py`, `tests/lab/test_network.py`
- Modify: `vesper/lab/home.py` (`LabHome.network`, parsed from the home file's `network:` section)

**Interfaces:**
- Produces: `parse_network(raw, world_map, devices) -> Network`; `Network(router: Place, hub: Place, coordinator: Place, links: dict[str, Link], unlinked: dict[str, str])` with `path(device) -> list[str]` (the device's end first: `zigbee/<device>`, `coordinator`, `hub`); `Place(room, position, medium)`; `Link(name, medium, ends, tcp, devices)`; `walls_between(world_map, a, b, doors) -> (walls, shut_doors)`; `link_quality(medium, a, b, world_map, doors) -> Quality(rssi_dbm, walls, doors, connected, loss, latency_s)`; `WIFI`, `ZIGBEE`, `ETHERNET_S`, `NetworkError`; `LabHome.network: Network | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_network.py`:
```python
"""The home's network (security-lab spec §3, plan W7a1): its topology, from the home file or by default, and each
link's quality from distance and walls in the home."""
import math
import textwrap

import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.maps import house
from vesper.lab.home import HomeError, load_lab_home
from vesper.lab.network import (ETHERNET_S, WIFI, ZIGBEE, NetworkError, link_quality, parse_network,
                                walls_between)


def home_with(tmp_path, network: str = ""):
    from tests.lab.homes import HOME
    return load_lab_home(write_home(tmp_path, HOME + textwrap.indent(textwrap.dedent(network), "    ")), catalog())


def test_a_home_without_a_network_section_gets_one_router_in_its_largest_room_and_the_hub_beside_it(tmp_path):
    net = home_with(tmp_path).network
    assert net.router.room == "hall" and net.router.position == pytest.approx((1.5, 3.0))
    assert net.hub.position == net.router.position  # the hub is wired to the router
    assert net.coordinator.position == net.hub.position and net.coordinator.medium == "internal"


def test_every_zigbee_device_reaches_home_assistant_through_its_hop_the_coordinator_and_the_hub(tmp_path):
    net = home_with(tmp_path).network
    assert net.path("hall_motion") == ["zigbee/hall_motion", "coordinator", "hub"]
    assert net.links["coordinator"].tcp and net.links["hub"].tcp and not net.links["zigbee/hall_motion"].tcp
    assert set(net.links["coordinator"].devices) == {"front_door", "hall_motion", "bedroom_lamp", "bedroom_button",
                                                     "kitchen_climate"}
    assert net.unlinked == {"front_lock": "a Matter device: on the network with plan W7a3"}


def test_the_router_and_the_coordinator_can_be_placed(tmp_path):
    net = home_with(tmp_path, """\
    network:
      router: {room: kitchen, xy: [6.0, 1.0]}
      coordinator: {room: bedroom, link: wifi}
""").network
    assert net.router.room == "kitchen" and net.router.position == (6.0, 1.0)
    assert net.coordinator.medium == "wifi" and net.coordinator.room == "bedroom"
    assert net.coordinator.position == pytest.approx((5.05, 4.55))  # the bedroom's centre
    assert net.links["coordinator"].medium == "wifi"
    assert net.links["zigbee/bedroom_lamp"].ends == ((5.0, 5.0), net.coordinator.position)


@pytest.mark.parametrize("network, message", [
    ("network: {router: {room: attic}}", "network.router.room: unknown room 'attic'"),
    ("network: {modem: {}}", "network: unknown key 'modem' (router, coordinator)"),
    ("network: {coordinator: {room: hall, link: radio}}", "network.coordinator.link: 'radio' is not one of"),
    ("network: {router: {room: hall, xy: [40, 1]}}", "network.router.xy: (40.0, 1.0) is not in the hall"),
])
def test_a_wrong_network_section_is_named(tmp_path, network, message):
    with pytest.raises(HomeError, match=message.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[")):
        home_with(tmp_path, network + "\n")


def test_walls_are_counted_where_a_line_crosses_them_and_furniture_or_an_open_doorway_is_not_a_wall():
    m = house()
    assert walls_between(m, (1.5, 3.0), (6.5, 0.5), {}) == (1, 0)  # the hall | kitchen wall, beside its doorway
    assert walls_between(m, (5.0, 1.0), (5.0, 5.0), {}) == (1, 0)  # the solid kitchen | bedroom wall
    assert walls_between(m, (1.5, 4.5), (5.0, 4.5), {"bedroom-hall": True}) == (0, 0)  # through the open door
    assert walls_between(m, (1.5, 4.5), (5.0, 4.5), {"bedroom-hall": False}) == (0, 1)  # the same door, shut
    assert walls_between(m, (1.5, 1.0), (1.5, 5.0), {}) == (0, 0)  # along the hall


def test_wi_fi_signal_falls_with_distance_and_walls_and_a_weak_link_loses_more_and_is_slower():
    m = house()
    q = link_quality("wifi", (1.5, 3.0), (6.5, 0.5), m, {})
    d = math.hypot(5.0, 2.5)
    assert q.rssi_dbm == pytest.approx(WIFI["tx_dbm"] - WIFI["pl1m_db"] - 10 * WIFI["exponent"] * math.log10(d)
                                       - WIFI["wall_db"])
    assert q.connected and q.walls == 1
    near = link_quality("wifi", (1.5, 3.0), (2.0, 3.0), m, {})
    assert near.rssi_dbm > q.rssi_dbm and near.loss < q.loss and near.latency_s < q.latency_s
    far = link_quality("wifi", (0.0, 0.0), (500.0, 0.0), m, {})
    assert not far.connected and far.loss == 1.0


def test_zigbee_hops_have_their_own_radio_and_wired_links_are_near_perfect():
    m = house()
    z = link_quality("zigbee", (5.0, 5.0), (1.5, 3.0), m, {})
    w = link_quality("wifi", (5.0, 5.0), (1.5, 3.0), m, {})
    assert z.rssi_dbm == pytest.approx(w.rssi_dbm - WIFI["tx_dbm"] + ZIGBEE["tx_dbm"])
    assert z.latency_s > w.latency_s  # 802.15.4 at 250 kbit/s, with its MAC's backoff
    e = link_quality("ethernet", (0, 0), (9, 9), m, {})
    assert e.rssi_dbm is None and e.connected and e.loss == 0 and e.latency_s == ETHERNET_S
    i = link_quality("internal", (0, 0), (0, 0), m, {})
    assert i.latency_s == 0 and i.loss == 0


def test_parse_network_needs_no_home_file():
    from vesper.lab.home import PlacedDevice
    lamp = PlacedDevice("lamp", "zigbee:acme:dim-1", {"binding": {"zigbee": {}}}, "bedroom", None, (5.0, 5.0),
                        "table", None)
    net = parse_network(None, house(), [lamp])
    assert net.path("lamp") == ["zigbee/lamp", "coordinator", "hub"]
    with pytest.raises(NetworkError):
        parse_network({"router": "hall"}, house(), [lamp])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_network.py -q 2>&1 | tail -1`

Expected: `1 error`: collection stops on `ModuleNotFoundError: No module named 'vesper.lab.network'`

- [ ] **Step 3: The topology and the link models**

Create `vesper/lab/network.py`:
```python
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
```

In `vesper/lab/home.py`, replace:
```python
from vesper.lab.geometry import Point, centroid, distance, point_in_polygon
from vesper.lab.profile import Profile, ProfileError, load_profile
```
with:
```python
from vesper.lab.geometry import Point, centroid, distance, point_in_polygon
from vesper.lab.network import Network, NetworkError, parse_network
from vesper.lab.profile import Profile, ProfileError, load_profile
```

In `vesper/lab/home.py`, replace:
```python
    matter_interface: str | None = None
```
with:
```python
    matter_interface: str | None = None
    network: Network | None = None  # the home's network (security-lab spec §3); set by load_lab_home
```

In `vesper/lab/home.py`, replace:
```python
    broker = _mapping(spec.get("broker") or {}, "broker")
    automations = spec.get("automations")
```
with:
```python
    broker = _mapping(spec.get("broker") or {}, "broker")
    try:
        network = parse_network(spec.get("network"), world_map, devices)
    except NetworkError as e:
        raise HomeError(str(e)) from e
    automations = spec.get("automations")
```

In `vesper/lab/home.py`, replace:
```python
                           _int(broker.get("port", 18830), "broker.port")),
                   matter_interface=spec.get("matter_interface"))
```
with:
```python
                           _int(broker.get("port", 18830), "broker.port")),
                   matter_interface=spec.get("matter_interface"), network=network)
```

- [ ] **Step 4: Run them and watch them pass, then the Lab's tests**

Run: `.venv/bin/python -m pytest tests/lab/test_network.py -q 2>&1 | tail -1`

Expected: `11 passed`

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `354 passed, 8 skipped` (in an export of `main`; the working copy's catalog runs a few of the skipped ones)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/network.py vesper/lab/home.py tests/lab/test_network.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): every home has a network: a router, the hub, the coordinator, a hop per Zigbee device, each link's quality from walls and distance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: The link layer: latency, order and an attack's effects, in-process

**Files:**
- Create: `vesper/lab/linklayer.py`, `tests/lab/test_linklayer.py`

**Interfaces:**
- Consumes: Task 1's `Network`, `link_quality`, `NetworkError`.
- Produces: `Effect(kind, link, direction="both", device=None, feature=None, hold_s=0.0, rate_bps=None, keep_connection=True, until=None)` with `matches()`, `to_dict()`, `from_dict()`; `KINDS`, `DIRECTIONS`, `check_effect(effect, network)`; `LinkLayer(network, world_map, clock, runtime, names, record, *, seed, carry_tcp, doors)` with `start()`, `subscribe(fn)`, `carried(device)`, `quality(link)`, `refresh()`, `effects`, `apply(effect)`, `clear(link=None)`, `down(device, feature, deliver)`. Its records: `{"kind": "net", "link", "event": "held"|"dropped"|"duplicated"|"reordered"|"down"|"up", ...}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_linklayer.py`:
```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_linklayer.py -q 2>&1 | tail -1`

Expected: `1 error`: collection stops on `ModuleNotFoundError: No module named 'vesper.lab.linklayer'`

- [ ] **Step 3: The link layer**

Create `vesper/lab/linklayer.py`:
```python
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

    def matches(self, link: str, direction: str, device: str | None, feature: str | None) -> bool:
        return (self.link == link and self.direction in (direction, "both")
                and self.device in (None, device) and self.feature in (None, feature))

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

    def refresh(self) -> None:
        """Each link's quality from the home as it is now (a door shut or opened changes what a hop crosses)."""
        doors = self._doors()
        for name, link in self.network.links.items():
            q = link_quality(link.medium, *link.ends, self.world_map, doors)
            before = self._quality.get(name)
            self._quality[name] = q
            if (before is None or before.connected) and not q.connected:
                self._net(name, "down", reason="out of range")
            elif before is not None and not before.connected and q.connected:
                self._net(name, "up", reason="in range")

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
            self._net(effect.link, "down", reason="cut")
        if effect.until is not None:
            self.clock.call_at(effect.until, lambda: self._end(effect))

    def clear(self, link: str | None = None) -> None:
        for e in [e for e in self._effects if link in (None, e.link)]:
            self._end(e)

    def _end(self, effect: Effect) -> None:
        if effect in self._effects:
            self._effects.remove(effect)
            if effect.kind == "cut" and not any(e.kind == "cut" and e.link == effect.link for e in self._effects):
                self._net(effect.link, "up", reason="cut ended")

    # -- traffic --------------------------------------------------------------------------------
    def down(self, device: str, feature: str | None, deliver: Callable[[], None]) -> None:
        """A command towards `device`: `deliver()` runs when it arrives, or never if a link loses it."""
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
            cut = next((e for e in self._effects if e.kind == "cut" and e.link == name), None)
            if cut is not None or not self._quality[name].connected:
                self._net(name, "dropped", device=device, feature=feature, direction=direction, sent_t=now,
                          reason="cut" if cut else "out of range")
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
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab/test_linklayer.py tests/lab/test_network.py -q 2>&1 | tail -1`

Expected: `22 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/linklayer.py tests/lab/test_linklayer.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the link layer: each link's latency, one device's order, and an attack's effects on its messages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The session on the network

**Files:**
- Modify: `vesper/lab/session.py` (the link layer; `live_network`; `automation_command` down the links; `subscribe_reports`; `network_effect`, `network_clear` as inputs; a door refreshes the links; `replay` puts effects on again)
- Modify: `vesper/lab/automations.py` (the engine hears reports as Home Assistant would: across the links)
- Create: `tests/lab/test_session_network.py`

**Interfaces:**
- Consumes: Task 2's `LinkLayer`, `Effect`.
- Produces: `Session(home, *, clock=None, world=None, trace=None, live_network=False)` with `links: LinkLayer | None`, `relay` (None until the hub sets it), `subscribe_reports(fn)`, `network_effect(effect)`, `network_clear(link=None)`; trace inputs `{"actor": "lab", "op": "effect", "args": effect.to_dict()}` and `{"op": "effect_clear", "args": {"link": ...}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_session_network.py`:
```python
"""A session on the home's network (security-lab spec §3, plan W7a1): Home Assistant (here VESPER's automation
engine) hears a report only once it has crossed the device's links, an attack's effect on a link is an input a
replay puts on again, and the trace keeps both what the device sent and what the network did to it."""
import pytest

from tests.lab.test_automations import HALL_LAMP, fired, lab, lamp_on
from vesper.lab.linklayer import Effect
from vesper.lab.network import NetworkError
from vesper.lab.session import compare_reports, replay


def hall_lamp(tmp_path):
    home, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 1.0))  # in the kitchen
    return home, s


def first(records, **match):
    return next(r for r in records if all(r.get(k) == v for k, v in match.items()))


def test_the_automation_acts_once_the_motion_report_has_crossed_its_hop_and_its_command_crossed_back(tmp_path):
    _, s = hall_lamp(tmp_path)
    s.walk_to((1.5, 3.0))
    s.clock.advance(10)
    sent = first(s.records, kind="report", device="hall_motion", feature="occupancy", value=True)["t"]
    acted = fired(s)[0]["t"]
    lamp = first(s.records, kind="report", device="bedroom_lamp", feature="state", value="ON")["t"]
    # up the motion sensor's hop and down the lamp's: tens of milliseconds; the lamp reports its new state the
    # instant the command arrives (that report's own hop delays its listeners, not the trace)
    assert sent < acted == lamp < sent + 0.2


def test_a_delayed_motion_report_makes_the_automation_late_by_the_hold_and_the_trace_says_why(tmp_path):
    _, s = hall_lamp(tmp_path)
    s.network_effect(Effect("delay", link="coordinator", direction="up", device="hall_motion",
                            feature="occupancy", hold_s=30))
    s.walk_to((1.5, 3.0))
    s.clock.advance(20)
    assert not lamp_on(s)  # the player is in the hall; Home Assistant has not heard it yet
    s.clock.advance(20)
    assert lamp_on(s)
    sent = first(s.records, kind="report", device="hall_motion", feature="occupancy", value=True)["t"]
    held = first(s.records, kind="net", event="held", device="hall_motion")
    assert held["link"] == "coordinator" and held["sent_t"] == sent and held["deliver_t"] - sent >= 30
    assert fired(s)[0]["t"] >= sent + 30
    effect = first(s.records, kind="input", op="effect")
    assert effect["actor"] == "lab" and effect["args"]["hold_s"] == 30


def test_a_cut_coordinator_link_leaves_the_automation_deaf_and_a_replay_cuts_it_again(tmp_path):
    home, s = hall_lamp(tmp_path)
    s.network_effect(Effect("cut", link="coordinator"))
    s.walk_to((1.5, 3.0))
    s.clock.advance(60)
    assert not lamp_on(s) and fired(s) == []
    s.network_clear("coordinator")
    s.clock.advance(1)
    s.stop()
    again = replay(s.records, home)
    assert compare_reports(s.records, again) is None
    down = [r for r in again if r["kind"] == "net" and r["event"] in ("down", "up")]
    assert [(r["link"], r["event"]) for r in down] == [("coordinator", "down"), ("coordinator", "up")]


def test_shutting_a_door_changes_what_a_hop_crosses(tmp_path):
    _, s = hall_lamp(tmp_path)
    before = s.links.quality("zigbee/bedroom_lamp")
    s.world.set_door("bedroom-hall", False)
    after = s.links.quality("zigbee/bedroom_lamp")
    assert after.rssi_dbm <= before.rssi_dbm


def test_an_effect_a_home_cannot_take_is_refused_and_not_traced(tmp_path):
    _, s = hall_lamp(tmp_path)
    with pytest.raises(NetworkError, match="unknown link"):
        s.network_effect(Effect("delay", link="wifi/sofa", hold_s=1))
    assert not [r for r in s.records if r.get("op") == "effect"]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_session_network.py -q 2>&1 | tail -1`

Expected: `5 failed`: the session has no `network_effect` or `links` yet, and the automation acts at the very instant the motion sensor reports (no hop)

- [ ] **Step 3: The session and the engine on the network**

In `vesper/lab/session.py`, replace:
```python
class Session:
    def __init__(self, home, *, clock=None, world=None, trace: Path | None = None):
        self.home = home
```
with:
```python
class Session:
    def __init__(self, home, *, clock=None, world=None, trace: Path | None = None, live_network: bool = False):
        """`live_network`: the relay carries the home's TCP links (Home Assistant in the loop); otherwise the link
        layer carries them in-process, on the session's clock."""
        self.home = home
```

In `vesper/lab/session.py`, replace:
```python
                           record=lambda r: self._write({**r, "kind": "rule"}))
```
with:
```python
                           record=lambda r: self._write({**r, "kind": "rule"}))
        self.relay = None  # the live network's relay, when Home Assistant is in the loop (set by the hub)
        self.links = None
        if home.network is not None:
            from vesper.lab.linklayer import LinkLayer
            self.links = LinkLayer(home.network, home.map, self.clock, self.runtime, self._names, self._write,
                                   seed=home.seed, carry_tcp=not live_network, doors=lambda: dict(self.world.doors))
```

In `vesper/lab/session.py`, replace:
```python
        self.runtime.subscribe(self._on_report)
        self.world.on_event(self._on_world)
```
with:
```python
        self.runtime.subscribe(self._on_report)
        if self.links is not None:
            self.links.start()
        self.world.on_event(self._on_world)
```

In `vesper/lab/session.py`, replace:
```python
    def automation_command(self, automation: str, trigger: str, service: str, entity: str, twin_id: str,
                           feature: str, value) -> bool:
        """The automation engine sends a command as Home Assistant would, through the bridge (`platform_set`)."""
        self._acting = {"automation": automation, "trigger": trigger, "service": service, "entity": entity}
        try:
            return self.runtime.platform_set(twin_id, feature, value)
        finally:
            self._acting = None
```
with:
```python
    def automation_command(self, automation: str, trigger: str, service: str, entity: str, twin_id: str,
                           feature: str, value) -> None:
        """The automation engine sends a command as Home Assistant would, through the bridge (`platform_set`), and
        across the device's links: it reaches the device when they deliver it."""
        acting = {"automation": automation, "trigger": trigger, "service": service, "entity": entity}

        def deliver():
            self._acting = acting
            try:
                self.runtime.platform_set(twin_id, feature, value)
            finally:
                self._acting = None
        device = self._names.get(twin_id)
        if self.links is not None and device is not None:
            self.links.down(device, feature, deliver)
        else:
            deliver()

    def subscribe_reports(self, fn) -> None:
        """`fn(event)` hears each twin's report as the hub would: once it has crossed the device's links."""
        (self.links or self.runtime).subscribe(fn)

    def network_effect(self, effect) -> None:
        """Put an effect on a link (an attack): an input, so a replay puts it on again at the same time. On the live
        network the relay carries the TCP links' effects."""
        link = self.home.network.links.get(effect.link) if self.home.network else None
        target = self.relay if (link is not None and link.tcp and self.relay is not None) else self.links
        if target is None:
            from vesper.lab.network import NetworkError
            raise NetworkError("effect: this home has no network")
        target.apply(effect)
        self._input("lab", "effect", effect.to_dict())

    def network_clear(self, link: str | None = None) -> None:
        for target in (self.links, self.relay):
            if target is not None:
                target.clear(link)
        self._input("lab", "effect_clear", {"link": link})
```

In `vesper/lab/session.py`, replace:
```python
            self._write({"kind": "world", **event})
```
with:
```python
            self._write({"kind": "world", **event})
        if event["event"] == "door" and self.links is not None:
            self.links.refresh()  # a door shut or opened: what each hop crosses changed
```

In `vesper/lab/session.py`, replace:
```python
                s.adjust(a["target"], a["feature"], a["value"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "command":
```
with:
```python
                s.adjust(a["target"], a["feature"], a["value"], actor=r["actor"], why=r.get("why"))
            elif r["op"] == "effect":
                from vesper.lab.linklayer import Effect
                s.network_effect(Effect.from_dict(a))
            elif r["op"] == "effect_clear":
                s.network_clear(a["link"])
            elif r["op"] == "command":
```

In `vesper/lab/automations.py`, replace:
```python
            self._state[e.entity_id] = "unknown" if e.platform == "event" else self._state_of(e, value)
        self.runtime.subscribe(self._on_report)
        self._midnight = clock.now() - self.home.start_time
```
with:
```python
            self._state[e.entity_id] = "unknown" if e.platform == "event" else self._state_of(e, value)
        self.session.subscribe_reports(self._on_report)  # as Home Assistant hears them: across the links
        self._midnight = clock.now() - self.home.start_time
```

- [ ] **Step 4: Run them and watch them pass, then the Lab's tests (replays stay exact: the jitter is seeded)**

Run: `.venv/bin/python -m pytest tests/lab/test_session_network.py -q 2>&1 | tail -1`

Expected: `5 passed`

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `370 passed, 8 skipped` (an export of `main`); the existing replays stay exact, as the jitter is seeded

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/session.py vesper/lab/automations.py tests/lab/test_session_network.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the session on the home's network: the engine hears reports across their links, effects are inputs a replay applies again

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: Zigbee2MQTT's availability, and the bridge on the links

**Files:**
- Modify: `vesper/exposure/ha_discovery.py` (each device's availability lists the bridge's state, `availability_mode: all`)
- Modify: `vesper/exposure/z2m_bridge.py` (`events`, `send`: reports from the links, commands down them; `online` again on a reconnect; `set_available`)
- Modify: `vesper/lab/hub.py` (the bridge on the session's links; `AVAILABILITY_S`; a hop down that long makes its device unavailable)
- Modify: `tests/exposure/test_ha_discovery.py`, `tests/exposure/test_z2m_bridge.py`, `tests/lab/test_hub.py`

**Interfaces:**
- Consumes: Task 3's `Session.links`, `network_effect`, `network_clear`; the `net` records' `down`/`up`.
- Produces: `Bridge(..., events=None, send=None)`, `Bridge.set_available(friendly_name, online)`; `hub.AVAILABILITY_S = {"active": 600.0, "passive": 90000.0}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/exposure/test_ha_discovery.py`:
```python


def test_a_device_is_available_only_while_both_it_and_the_bridge_are_as_zigbee2mqtt_says(vds):
    """Zigbee2MQTT lists the bridge's state too: when the bridge's broker connection dies, its will marks the bridge
    offline and Home Assistant marks every Zigbee device unavailable."""
    _, payload, _, _ = discover(vds["MCCGQ11LM"], "hall/door")
    assert [a["topic"] for a in payload["availability"]] == ["zigbee2mqtt/hall/door/availability",
                                                             "zigbee2mqtt/bridge/state"]
    assert payload["availability_mode"] == "all"
    assert {render(a["value_template"], {"state": "offline"}) for a in payload["availability"]} == {"offline"}
```

Append to `tests/exposure/test_z2m_bridge.py`:
```python


def test_a_reconnect_says_the_bridge_is_online_again_after_its_will_said_offline(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM")
    client.published.clear()
    client.on_connect(client, None, {}, 0, None)  # paho reconnected; the broker had published the will
    assert client.last("zigbee2mqtt/bridge/state") == {"state": "online"}
    assert client.retained("zigbee2mqtt/bridge/state") is True


def test_reports_can_come_through_a_link_and_commands_go_down_it(vds):
    """A home's network carries each device's reports and Home Assistant's commands (security-lab spec §3.2):
    the bridge hears reports from `events` and hands each command to `send`, which runs it when it arrives."""
    rt = Runtime(clock=LogicalClock())
    plug = Placement(rt.add(vds["PLUG-1"]).id, "plug-1")
    heard, sent = [], []

    class Link:
        def subscribe(self, fn):
            heard.append(fn)
    client = FakeClient()
    b = Bridge(rt, [plug], client, events=Link(), send=lambda name, feature, fn: sent.append((name, feature, fn)))
    b.start()
    assert len(heard) == 1  # the bridge hears the link, not the runtime
    client.deliver("zigbee2mqtt/plug-1/set", {"state": "ON"})
    assert rt.state(plug.twin_id)["state"] == "OFF"  # on its way down
    (name, feature, run), = sent
    assert name == "plug-1" and feature is None
    run()
    assert rt.state(plug.twin_id)["state"] == "ON" and client.last("zigbee2mqtt/plug-1")["state"] == "ON"


def test_a_device_can_be_marked_unavailable_and_available_again(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM")
    b.set_available("mccgq11lm", False)
    assert client.last("zigbee2mqtt/mccgq11lm/availability") == {"state": "offline"}
    b.set_available("mccgq11lm", True)
    assert client.last("zigbee2mqtt/mccgq11lm/availability") == {"state": "online"}
```

Append to `tests/lab/test_hub.py`:
```python


def test_a_device_whose_hop_stays_down_goes_unavailable_after_zigbee2mqtt_s_timeout_and_comes_back(lab, monkeypatch):
    """Zigbee2MQTT marks a device offline when it stops answering for its availability timeout (10 min for a
    mains-powered device, 25 h for a battery one); here shortened. Only that device: the others stay available."""
    from vesper.lab import hub as hub_module
    from vesper.lab.linklayer import Effect
    session, hub, mqtt = lab
    monkeypatch.setitem(hub_module.AVAILABILITY_S, "active", 0.3)
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    eventually(lambda: mqtt.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "offline"})
    assert mqtt.last("zigbee2mqtt/hall_motion/availability") == {"state": "online"}
    session.network_clear("zigbee/bedroom_lamp")
    eventually(lambda: mqtt.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "online"})


def test_a_command_to_a_device_behind_a_cut_hop_never_arrives(lab):
    from vesper.lab.linklayer import Effect
    session, hub, mqtt = lab
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    FakeHA(mqtt).call("light", "turn_on", {"entity_id": hub.entities("bedroom_lamp")["state"]})
    time.sleep(0.3)
    assert session.rules.effects.get("bedroom_lamp") != {"on": True}
    assert any(r["kind"] == "net" and r["event"] == "dropped" and r.get("direction") == "down"
               for r in session.records)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/exposure/test_ha_discovery.py tests/exposure/test_z2m_bridge.py tests/lab/test_hub.py -q 2>&1 | tail -1`

Expected: `6 failed, 44 passed`: the availability lists only the device's topic; no `online` on a reconnect; `Bridge` takes no `events`; no `set_available`; no `hub.AVAILABILITY_S`; Home Assistant's command reaches a lamp behind a cut hop

- [ ] **Step 3: Availability as Zigbee2MQTT's, and the bridge on the links**

In `vesper/exposure/ha_discovery.py`, replace:
```python
        "state_topic": state_topic,
        "availability": [{"topic": f"{state_topic}/availability", "value_template": "{{ value_json.state }}"}],
        "cmps": cmps,
```
with:
```python
        "state_topic": state_topic,
        "availability": [{"topic": f"{state_topic}/availability", "value_template": "{{ value_json.state }}"},
                         {"topic": f"{base_topic}/bridge/state", "value_template": "{{ value_json.state }}"}],
        "availability_mode": "all",  # as Zigbee2MQTT: the device, and the bridge that carries it
        "cmps": cmps,
```

In `vesper/exposure/z2m_bridge.py`, replace:
```python
    rejections: list[dict] = field(default_factory=list)
```
with:
```python
    rejections: list[dict] = field(default_factory=list)
    events: object = None  # where reports come from (anything with `subscribe`): a home's network; default the runtime
    send: object = None  # send(name, feature, run): a command crosses the device's links, `run()` when it arrives
```

In `vesper/exposure/z2m_bridge.py`, replace:
```python
        self.client.on_connect = self._on_connect
        self.runtime.subscribe(self._on_event)
```
with:
```python
        self.client.on_connect = self._on_connect
        (self.events or self.runtime).subscribe(self._on_event)
```

In `vesper/exposure/z2m_bridge.py`, replace:
```python
            self._track(self.client.publish(f"{b}/{topic}", "", qos=1, retain=True))
```
with:
```python
            self._track(self.client.publish(f"{b}/{topic}", "", qos=1, retain=True))

    def set_available(self, friendly_name: str, online: bool) -> None:
        """Mark one device available or not, as Zigbee2MQTT's availability check does when a device stops answering."""
        self._publish(f"{self.base_topic}/{friendly_name}/availability", {"state": "online" if online else "offline"},
                      retain=True)
```

In `vesper/exposure/z2m_bridge.py`, replace:
```python
    def _on_connect(self, client, userdata, flags, reason, properties=None) -> None:
        """paho reconnects by itself, but with a clean session: subscribe again."""
        self._subscribe()
```
with:
```python
    def _on_connect(self, client, userdata, flags, reason, properties=None) -> None:
        """paho reconnects by itself, but with a clean session: subscribe again, and say the bridge is online (the
        broker published its will, `offline`, when the connection died)."""
        self._subscribe()
        self._publish(f"{self.base_topic}/bridge/state", {"state": "online"}, retain=True)
```

In `vesper/exposure/z2m_bridge.py`, replace:
```python
            return
        accepted = []
```
with:
```python
            return
        if self.send is not None:
            self.send(name, None, lambda: self._apply(p, name, command))
        else:
            self._apply(p, name, command)

    def _apply(self, p: Placement, name: str, command: dict) -> None:
        accepted = []
```

In `vesper/lab/hub.py`, replace:
```python

from vesper.exposure.z2m_bridge import Bridge, Placement
```
with:
```python

from vesper.exposure.z2m_bridge import Bridge, Placement, device_entry, ieee_address

# Zigbee2MQTT's availability timeouts: a device that stops answering is marked offline after this long
# (its `availability.active.timeout` and `passive.timeout` defaults: 10 min and 1500 min).
AVAILABILITY_S = {"active": 600.0, "passive": 90000.0}
```

In `vesper/lab/hub.py`, replace:
```python
                            if "zigbee" in d.record["binding"]}
        self.bridge = Bridge(session.runtime, list(self._placements.values()), ClockThreadClient(client, session.clock),
                             base_topic=base_topic, zhc_version=zhc_version)
```
with:
```python
                            if "zigbee" in d.record["binding"]}
        links = session.links  # the home's network: each device's reports and commands cross its hop
        self.bridge = Bridge(session.runtime, list(self._placements.values()), ClockThreadClient(client, session.clock),
                             base_topic=base_topic, zhc_version=zhc_version, events=links,
                             send=(lambda name, feature, run: links.down(name, feature, run)) if links else None)
        self._passive = {name: device_entry(session.runtime.twins[p.twin_id].vds, ieee=ieee_address(p.twin_id),
                                            friendly_name=name)["type"] == "EndDevice"
                         for name, p in self._placements.items()}
        self._down: dict[str, int] = {}  # device -> the down event it is waiting out
        self._offline: set[str] = set()
        session.on_record(self._on_record)
```

In `vesper/lab/hub.py`, replace:
```python

    def entities(self, name: str) -> dict[str, str]:
```
with:
```python

    def _on_record(self, r: dict) -> None:
        """A device's hop down for Zigbee2MQTT's availability timeout makes it unavailable; up again, available."""
        if r.get("kind") != "net" or not r["link"].startswith("zigbee/") or r["event"] not in ("down", "up"):
            return
        name = r["link"][len("zigbee/"):]
        if name not in self._placements:
            return
        if r["event"] == "down":
            token = self._down[name] = self._down.get(name, 0) + 1
            timeout = AVAILABILITY_S["passive" if self._passive[name] else "active"]
            self.session.clock.call_at(self.session.clock.now() + timeout, lambda: self._expired(name, token))
        else:
            self._down[name] = self._down.get(name, 0) + 1  # any wait in progress is void
            if name in self._offline:
                self._offline.discard(name)
                self.bridge.set_available(name, True)

    def _expired(self, name: str, token: int) -> None:
        if self._down.get(name) == token:
            self._offline.add(name)
            self.bridge.set_available(name, False)

    def entities(self, name: str) -> dict[str, str]:
```

- [ ] **Step 4: Run them and watch them pass, then the Lab's and the exposure's tests**

Run: `.venv/bin/python -m pytest tests/exposure/test_ha_discovery.py tests/exposure/test_z2m_bridge.py tests/lab/test_hub.py -q 2>&1 | tail -1`

Expected: `50 passed`

Run: `.venv/bin/python -m pytest tests/lab tests/exposure -q 2>&1 | tail -1`

Expected: `460 passed, 14 skipped` (an export of `main`)

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/exposure/ha_discovery.py vesper/exposure/z2m_bridge.py vesper/lab/hub.py tests/exposure/test_ha_discovery.py tests/exposure/test_z2m_bridge.py tests/lab/test_hub.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(exposure): availability as Zigbee2MQTT's: the bridge's state in every device's, online after a reconnect, a device unavailable when its hop stays down

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: The relay

**Files:**
- Create: `vesper/lab/relay.py`, `tests/lab/test_relay.py`

**Interfaces:**
- Consumes: Task 2's `Effect`, `KINDS`, `DIRECTIONS`.
- Produces: `Relay(record, allowed, now, latency=None)` with `start()`, `stop()`, `add_link(name, upstream_port, client_is_device) -> listen_port`, `effects`, `apply(effect)`, `clear(link=None)`; `RelayError`; `split_packets(buf) -> (packets, rest)`; `publish_of(packet, level) -> (topic, payload) | None`; `HOST`, `FORBIDDEN`. Its records: `net` records as the link layer's, plus `resent` (MQTT's DUP flag).

- [ ] **Step 1: Write the failing tests** (real sockets on loopback: an upstream that records what reaches it)

Create `tests/lab/test_relay.py`:
```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_relay.py -q 2>&1 | tail -1`

Expected: `1 error`: collection stops on `ModuleNotFoundError: No module named 'vesper.lab.relay'`

- [ ] **Step 3: The relay**

Create `vesper/lab/relay.py`:
```python
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

HOST = "127.0.0.1"
FORBIDDEN = (1883, 8123)  # the standard MQTT and Home Assistant ports: never VESPER's, never touched


class RelayError(ValueError):
    """A link or an effect the relay refuses; the message says why."""


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
        except (OSError, RelayError):
            pass
        finally:
            out.task.cancel()
            if not writer.transport.is_closing():
                writer.close()  # the other side ended: so does this one

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
            if any(e.kind == "delay" and e.keep_connection for e in effects):
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
```

- [ ] **Step 4: Run them and watch them pass, three times (they time real sockets)**

Run: `for k in 1 2 3; do taskpolicy -b .venv/bin/python -m pytest tests/lab/test_relay.py -q 2>&1 | tail -1; done`

Expected: `15 passed`, three times

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/relay.py tests/lab/test_relay.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the relay: real TCP between VESPER's own services, on loopback only, where an attack acts on whole MQTT messages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 6: play and look on the live network, and `vesper lab network`

**Files:**
- Modify: `vesper/lab/relay.py` (`RelayError` is a `NetworkError`; `SessionSink`, the relay's way into a session that starts after it)
- Modify: `vesper/lab/session.py` (`network_effect` checks the effect first and sends a TCP link's to the relay; `net_record`)
- Modify: `vesper/cli.py` (`_lab_stack` starts the relay and points Home Assistant at its hub link; `_lab_session`; the bridge dials the coordinator link; the `network:` line; `vesper lab network`)
- Modify: `tests/lab/test_cli_play.py`, `tests/lab/test_session_network.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces: `SessionSink` (`attach(session)`, `record`, `now`, `latency`); `Session.net_record(record)`; `vesper lab network HOME [--catalog]`; play's line `network: Home Assistant's MQTT through the relay's hub link (port N), the bridge's through its coordinator link (port M)`.

- [ ] **Step 1: Write the failing tests**

In `tests/lab/test_cli_play.py`, replace:
```python
    assert ha.frameable and ha.automations == RULES
    assert ha.events == ["start", "onboard", ("ensure_mqtt", 18830), "login", "stop"]
    assert "log in as vesper, password s3cret-lab-login" in seen["out"]
    assert clients[0][0][:3] == ("127.0.0.1", 18830, "vesper-lab-bridge") and clients[-1][0] == "disconnected"
    assert seen["hub"] == "http://127.0.0.1:18123"
```
with:
```python
    assert ha.frameable and ha.automations == RULES
    # Home Assistant and the bridge reach the broker through the home's network: the relay's hub and coordinator
    # links, on ports of their own (security-lab spec §3.4), never the broker's directly
    (_, hub_port), ((_, coord_port, name), _) = ha.events[2], clients[0]
    assert ha.events[:2] == ["start", "onboard"] and ha.events[2][0] == "ensure_mqtt" and ha.events[3:] == [
        "login", "stop"]
    assert name == "vesper-lab-bridge" and len({hub_port, coord_port, 18830}) == 3 and clients[-1][0] == "disconnected"
    assert f"network: Home Assistant's MQTT through the relay's hub link (port {hub_port}), the bridge's through its " \
           f"coordinator link (port {coord_port})" in seen["out"]
    assert "log in as vesper, password s3cret-lab-login" in seen["out"]
    assert seen["hub"] == "http://127.0.0.1:18123"
```

In `tests/lab/test_cli_play.py`, replace:
```python
    assert "automations-from-home-assistant.yaml" in capsys.readouterr().out
```
with:
```python
    assert "automations-from-home-assistant.yaml" in capsys.readouterr().out


def test_lab_network_shows_the_router_each_link_and_what_is_not_on_the_network(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "network", str(home), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "router: hall (1.5, 3.0); the hub is wired to it; the Zigbee coordinator is on the hub" in out
    assert "assumed" in out  # the radio constants are typical values, not measured
    lamp = next(line for line in out.splitlines() if line.strip().startswith("zigbee/bedroom_lamp"))
    assert "dBm" in lamp and "1 wall" in lamp and "ms" in lamp
    assert "front_lock: a Matter device: on the network with plan W7a3" in out
```

Append to `tests/lab/test_session_network.py`:
```python


def test_on_the_live_network_a_tcp_link_s_effect_goes_to_the_relay_and_a_hop_s_to_the_link_layer(tmp_path):
    _, s = hall_lamp(tmp_path)
    put = []

    class Relay:
        def apply(self, effect):
            put.append(effect)

        def clear(self, link=None):
            put.append(("clear", link))
    s.relay = Relay()
    s.network_effect(Effect("delay", link="coordinator", hold_s=5))
    s.network_effect(Effect("delay", link="zigbee/hall_motion", hold_s=5))
    assert put == [Effect("delay", link="coordinator", hold_s=5)]
    assert s.links.effects == [Effect("delay", link="zigbee/hall_motion", hold_s=5)]
    with pytest.raises(NetworkError, match="'jam' is not one of"):
        s.network_effect(Effect("jam", link="coordinator"))
    s.network_clear()
    assert put[-1] == ("clear", None) and s.links.effects == []


def test_records_from_another_thread_reach_the_trace_on_the_session_s_clock(tmp_path):
    _, s = hall_lamp(tmp_path)
    s.net_record({"kind": "net", "link": "coordinator", "event": "down", "reason": "cut"})
    assert not [r for r in s.records if r.get("reason") == "cut"]  # not yet: on the clock's next turn
    s.clock.advance(0)
    assert [r for r in s.records if r.get("reason") == "cut"][0]["t"] == s.clock.now()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_cli_play.py tests/lab/test_session_network.py -q 2>&1 | tail -1`

Expected: `4 failed, 24 passed`: Home Assistant and the bridge are given the broker's port 18830; `lab network` is `invalid choice`; an effect of an unknown kind reaches the relay unchecked (`DID NOT RAISE`); the session has no `net_record`

- [ ] **Step 3: The relay meets the session**

In `vesper/lab/relay.py`, replace:
```python
from vesper.lab.linklayer import DIRECTIONS, KINDS, Effect

HOST = "127.0.0.1"
FORBIDDEN = (1883, 8123)  # the standard MQTT and Home Assistant ports: never VESPER's, never touched


class RelayError(ValueError):
    """A link or an effect the relay refuses; the message says why."""
```
with:
```python
from vesper.lab.linklayer import DIRECTIONS, KINDS, Effect
from vesper.lab.network import NetworkError

HOST = "127.0.0.1"
FORBIDDEN = (1883, 8123)  # the standard MQTT and Home Assistant ports: never VESPER's, never touched


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
```

In `vesper/lab/session.py`, replace:
```python
        network the relay carries the TCP links' effects."""
        link = self.home.network.links.get(effect.link) if self.home.network else None
        target = self.relay if (link is not None and link.tcp and self.relay is not None) else self.links
        if target is None:
            from vesper.lab.network import NetworkError
            raise NetworkError("effect: this home has no network")
        target.apply(effect)
        self._input("lab", "effect", effect.to_dict())
```
with:
```python
        network the relay carries the TCP links' effects."""
        from vesper.lab.linklayer import check_effect
        from vesper.lab.network import NetworkError
        if self.home.network is None:
            raise NetworkError("effect: this home has no network")
        check_effect(effect, self.home.network)
        link = self.home.network.links[effect.link]
        (self.relay if link.tcp and self.relay is not None else self.links).apply(effect)
        self._input("lab", "effect", effect.to_dict())

    def net_record(self, record: dict) -> None:
        """A record from another thread (the relay's): written on the session's clock, like every other change."""
        self.clock.call_at(self.clock.now(), lambda: self._write(record))
```

- [ ] **Step 4: The commands**

In `vesper/cli.py`, replace:
```python
            return 0
        if args.action == "run":
```
with:
```python
            return 0
        if args.action == "network":
            return _lab_network(load_lab_home(Path(args.home), catalog))
        if args.action == "run":
```

In `vesper/cli.py`, replace:
```python
    except (HomeError, MapError, TraceError, SceneError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _lab_scenes(args) -> int:
```
with:
```python
    except (HomeError, MapError, TraceError, SceneError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1


def _lab_network(home) -> int:
    """The home's network (security-lab spec §3): where the router stands, and each link's quality."""
    from vesper.lab.network import WIFI, link_quality
    net = home.network
    place = lambda p: f"{p.room} ({p.position[0]:.1f}, {p.position[1]:.1f})"  # noqa: E731
    coord = "on the hub" if net.coordinator.medium == "internal" else \
        f"in the {place(net.coordinator)}, on {net.coordinator.medium}"
    print(f"{home.scene}: router: {place(net.router)}; the hub is wired to it; the Zigbee coordinator is {coord}")
    print(f"radio: assumed typical values (ITU-R P.1238 indoor path loss, {WIFI['wall_db']:g} dB a wall), "
          "not measured on a device")
    for link in net.links.values():
        q = link_quality(link.medium, *link.ends, home.map, home.doors)
        if q.rssi_dbm is None:
            quality = "wired" if link.medium == "ethernet" else "on the same host"
        elif not q.connected:
            quality = f"{q.rssi_dbm:.0f} dBm: out of range"
        else:
            walls = f"{q.walls} wall{'s' if q.walls != 1 else ''}" + (f", {q.doors} shut door" if q.doors else "")
            quality = f"{q.rssi_dbm:.0f} dBm, {walls}, {q.latency_s * 1000:.0f} ms"
        print(f"  {link.name:<28} {link.medium:<9} {quality}")
    for name, why in net.unlinked.items():
        print(f"  {name}: {why}")
    return 0


def _lab_scenes(args) -> int:
```

In `vesper/cli.py`, replace:
```python
    from vesper.lab.play import PlayError, PlayServer
    from vesper.lab.session import Session
    from vesper.twins.clock import WallClock
    traces = home.path.parent / "traces"
```
with:
```python
    from vesper.lab.play import PlayError, PlayServer
    traces = home.path.parent / "traces"
```

In `vesper/cli.py`, replace:
```python
            lab = stack.enter_context(_lab_stack(args, home)) if args.hub else None
            session = Session(home, clock=WallClock(), trace=trace)  # its time starts once the hub is up
            stack.callback(session.stop)
```
with:
```python
            lab = stack.enter_context(_lab_stack(args, home)) if args.hub else None
            session = _lab_session(home, trace, lab)  # its time starts once the hub is up
            stack.callback(session.stop)
```

In `vesper/cli.py`, replace:
```python
            user, password = lab.login
            print(f"Home Assistant is in the hub panel ({lab.ha.url}): log in as {user}, password {password} "
```
with:
```python
            user, password = lab.login
            print(f"network: Home Assistant's MQTT through the relay's hub link (port {lab.hub_port}), the bridge's "
                  f"through its coordinator link (port {hub.coordinator_port})")
            print(f"Home Assistant is in the hub panel ({lab.ha.url}): log in as {user}, password {password} "
```

In `vesper/cli.py`, replace:
```python
    root, port = Path(args.root), home.broker[1]
    rules = home.automations.read_text() if home.automations is not None else "[]\n"
    broker = stack.Mosquitto(root, port=port)
    try:
        broker.start()
        ha = stack.HomeAssistant(root, frameable=True, automations=rules)
        try:
            ha.start()
            ha.onboard()
            ha.ensure_mqtt(port)
            yield SimpleNamespace(ha=ha, port=port, login=ha.lab_login())
        finally:
            _keep_automations(ha, home, rules)
            ha.stop()
    finally:
        broker.stop()
```
with:
```python
    root, port = Path(args.root), home.broker[1]
    from vesper.lab.relay import Relay, SessionSink
    rules = home.automations.read_text() if home.automations is not None else "[]\n"
    broker = stack.Mosquitto(root, port=port)
    sink = SessionSink()  # the session starts once Home Assistant is up; the relay must exist before
    relay = Relay(record=sink.record, allowed={port}, now=sink.now, latency=sink.latency)
    try:
        broker.start()
        relay.start()
        hub_port = relay.add_link("hub", port, client_is_device=False)  # Home Assistant's MQTT: the home's network
        ha = stack.HomeAssistant(root, frameable=True, automations=rules)
        try:
            ha.start()
            ha.onboard()
            ha.ensure_mqtt(hub_port)
            yield SimpleNamespace(ha=ha, port=port, hub_port=hub_port, relay=relay, sink=sink, login=ha.lab_login())
        finally:
            _keep_automations(ha, home, rules)
            ha.stop()
    finally:
        relay.stop()
        broker.stop()


def _lab_session(home, trace, lab):
    """A wall-clock session; with the hub, on the live network: the relay carries its TCP links."""
    from vesper.lab.session import Session
    from vesper.twins.clock import WallClock
    session = Session(home, clock=WallClock(), trace=trace, live_network=lab is not None)
    if lab is not None:
        session.relay = lab.relay
        lab.sink.attach(session)
    return session
```

In `vesper/cli.py`, replace:
```python
    from vesper.lab.hub import Hub
    client = mqtt.connect("127.0.0.1", port, "vesper-lab-bridge", will=OFFLINE_WILL)
    try:
        hub = Hub(session, client, zhc_version=_zhc_version(Path(args.catalog)))
        hub.start()
```
with:
```python
    from vesper.lab.hub import Hub
    via = session.relay.add_link("coordinator", port, client_is_device=True) if session.relay else port
    client = mqtt.connect("127.0.0.1", via, "vesper-lab-bridge", will=OFFLINE_WILL)
    try:
        hub = Hub(session, client, zhc_version=_zhc_version(Path(args.catalog)))
        hub.coordinator_port = via
        hub.start()
```

In `vesper/cli.py`, replace:
```python
    from vesper.lab.play import PlayServer
    from vesper.lab.session import Session
    from vesper.lab.visual import DEFAULT_STEPS, LookError, load_steps, run_look
    from vesper.twins.clock import WallClock
    if args.update_golden and not args.golden:
```
with:
```python
    from vesper.lab.play import PlayServer
    from vesper.lab.visual import DEFAULT_STEPS, LookError, load_steps, run_look
    if args.update_golden and not args.golden:
```

In `vesper/cli.py`, replace:
```python
            lab = stack.enter_context(_lab_stack(args, home)) if args.hub else None
            session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
            stack.callback(session.stop)
```
with:
```python
            lab = stack.enter_context(_lab_stack(args, home)) if args.hub else None
            session = _lab_session(home, _new_trace(out, "look"), lab)
            stack.callback(session.stop)
```

In `vesper/cli.py`, replace:
```python
    lab_scenes.add_argument("--hssd", help="the HSSD dataset (default: $VESPER_HSSD or data/scene_datasets/hssd-hab)")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
```
with:
```python
    lab_scenes.add_argument("--hssd", help="the HSSD dataset (default: $VESPER_HSSD or data/scene_datasets/hssd-hab)")
    lab_network = lab_actions.add_parser("network", help="the home's network: the router and each link's quality")
    lab_network.add_argument("home")
    lab_network.add_argument("--catalog", default="catalog/vds")
    lab_check = lab_actions.add_parser("check", help="validate a home file")
```

- [ ] **Step 5: Run them and watch them pass, then the Lab's, the exposure's and the world's tests**

Run: `.venv/bin/python -m pytest tests/lab/test_cli_play.py tests/lab/test_session_network.py -q 2>&1 | tail -1`

Expected: `28 passed`

Run: `taskpolicy -b .venv/bin/python -m pytest tests/lab tests/exposure tests/world -q 2>&1 | tail -1`

Expected: `504 passed, 21 skipped` (an export of `main`; the live tests skip)

- [ ] **Step 6: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/relay.py vesper/lab/session.py vesper/cli.py tests/lab/test_cli_play.py tests/lab/test_session_network.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): play and look with the hub on the live network, through the relay; vesper lab network shows the home's links

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 7: The network end to end, live, and the docs

**Files:**
- Create: `tests/lab/test_network_live.py` (a real Mosquitto: VESPER_STACK_TESTS=1), `tests/lab/test_network_ha_live.py` (the real Home Assistant: VESPER_HA_TESTS=1)
- Modify: `README.md` (the home's network; `vesper lab network`; the opt-in tests), `docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md` (progress)

- [ ] **Step 1: The end-to-end tests** (the old VESPER's failure, turned round: the traffic crosses the network)

Create `tests/lab/test_network_live.py`:
```python
"""The home's network end to end, against a real Mosquitto (opt-in: VESPER_STACK_TESTS=1; needs `mosquitto`).

The old VESPER's emulated network did not carry the traffic it measured; this test is that failure turned round
(security-lab spec §8): the real bridge (paho) reaches a real broker only through the relay's coordinator link, a
real client in Home Assistant's place only through its hub link, and cutting one link silences exactly what
crosses it.
"""
import json
import os
import socket
import time

import pytest

from tests.lab.homes import catalog, write_home
from vesper.exposure.mqtt import connect, disconnect
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.linklayer import Effect
from vesper.lab.relay import Relay, SessionSink
from vesper.lab.session import Session
from vesper.twins.clock import WallClock
from vesper.validation.stack import Mosquitto

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_STACK_TESTS") != "1", reason="set VESPER_STACK_TESTS=1")]

WILL = ("zigbee2mqtt/bridge/state", json.dumps({"state": "offline"}))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Observer:
    """A client in Home Assistant's place: every message it hears, with the time."""

    def __init__(self, port):
        self.heard: list[tuple[float, str, dict | None]] = []
        self.client = connect("127.0.0.1", port, "observer")
        self.client.on_message = lambda c, u, m: self.heard.append(
            (time.monotonic(), m.topic, json.loads(m.payload) if m.payload else None))
        self.client.subscribe("zigbee2mqtt/#", qos=1)

    def last(self, topic):
        return next((p for _, t, p in reversed(self.heard) if t == topic), None)

    def times(self, topic, **match):
        return [t for t, top, p in self.heard if top == topic and p and all(p.get(k) == v for k, v in match.items())]


def until(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def open_front_door(session):
    """The player walks to the front door and opens it: its contact sensor reports `contact: false`."""
    session.walk_to((0.4, 3.0))
    assert until(lambda: abs(session.world.pose("player").x - 0.4) < 0.15)
    assert session.interact("hall-porch")["result"] == "opened"


@pytest.fixture
def live(tmp_path, monkeypatch):
    from vesper.lab import hub as hub_module
    monkeypatch.setitem(hub_module.AVAILABILITY_S, "active", 1.0)
    monkeypatch.setitem(hub_module.AVAILABILITY_S, "passive", 1.0)
    port = free_port()
    broker = Mosquitto(tmp_path / "mqtt", port=port)
    broker.start()
    sink = SessionSink()
    relay = Relay(record=sink.record, allowed={port}, now=sink.now, latency=sink.latency)
    relay.start()
    hub_port = relay.add_link("hub", port, client_is_device=False)
    home = load_lab_home(write_home(tmp_path), catalog())
    session = Session(home, clock=WallClock(), live_network=True)
    session.relay = relay
    sink.attach(session)
    session.start()
    coord_port = relay.add_link("coordinator", port, client_is_device=True)
    bridge_client = connect("127.0.0.1", coord_port, "vesper-lab-bridge", will=WILL)
    bridge_client.reconnect_delay_set(min_delay=1, max_delay=1)
    hub = Hub(session, bridge_client)
    hub.start()
    observer = Observer(hub_port)
    yield session, hub, observer, relay
    disconnect(observer.client)
    hub.stop()
    disconnect(bridge_client)
    session.stop()
    relay.stop()
    broker.stop()


def test_every_device_s_reports_cross_the_coordinator_and_hub_links_to_home_assistant(live):
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "online"})
    for name in ("front_door", "hall_motion", "bedroom_lamp", "bedroom_button", "kitchen_climate"):
        assert until(lambda name=name: observer.last(f"zigbee2mqtt/{name}") is not None), name
    relay.apply(Effect("cut", link="hub"))  # silent: Home Assistant hears nothing at all
    n = len(observer.heard)
    open_front_door(session)
    time.sleep(0.5)
    assert len(observer.heard) == n
    relay.clear("hub")
    assert until(lambda: observer.times("zigbee2mqtt/front_door", contact=False))  # what waited arrives


def test_a_reset_coordinator_link_makes_the_broker_publish_the_bridge_s_will_and_the_bridge_comes_back(live):
    """Every Zigbee device's discovery lists the bridge's state (availability_mode: all): offline here is every
    device unavailable in Home Assistant."""
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "online"})
    session.network_effect(Effect("cut", link="coordinator", keep_connection=False))
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "offline"})
    session.network_clear("coordinator")
    assert until(lambda: observer.last("zigbee2mqtt/bridge/state") == {"state": "online"}, timeout=15)
    events = [(r["link"], r["event"]) for r in session.records if r["kind"] == "net" and r["event"] in ("down", "up")]
    assert ("coordinator", "down") in events and ("coordinator", "up") in events


def test_a_held_report_reaches_home_assistant_late_on_a_session_that_stays_up(live):
    """Phantom-Delay's mechanism, on real TCP: one device's report held for its hold, the keepalives passing, so
    the broker never drops the bridge and never publishes its will."""
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/front_door") is not None)
    session.network_effect(Effect("delay", link="coordinator", direction="up", device="front_door",
                                  feature="contact", hold_s=3))
    open_front_door(session)
    t0 = time.monotonic()
    assert until(lambda: observer.times("zigbee2mqtt/front_door", contact=False), timeout=10)
    assert observer.times("zigbee2mqtt/front_door", contact=False)[0] - t0 >= 2.9
    assert observer.last("zigbee2mqtt/bridge/state") == {"state": "online"}  # no will: the session stayed alive
    held = [r for r in session.records if r["kind"] == "net" and r["event"] == "held"]
    assert held and held[0]["device"] == "front_door"


def test_a_cut_zigbee_hop_silences_only_its_device_and_zigbee2mqtt_marks_only_it_unavailable(live):
    session, hub, observer, relay = live
    assert until(lambda: observer.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "online"})
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    assert until(lambda: observer.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "offline"})
    for other in ("front_door", "hall_motion", "kitchen_climate"):
        assert observer.last(f"zigbee2mqtt/{other}/availability") == {"state": "online"}
    session.network_clear("zigbee/bedroom_lamp")
    assert until(lambda: observer.last("zigbee2mqtt/bedroom_lamp/availability") == {"state": "online"})


def test_the_relay_only_ever_listens_on_loopback(live):
    session, hub, observer, relay = live
    for link in relay._links.values():
        for sock in link.server.sockets:
            assert sock.getsockname()[0] == "127.0.0.1"
```

Create `tests/lab/test_network_ha_live.py`:
```python
"""The home's network end to end with the real Home Assistant (opt-in: VESPER_HA_TESTS=1, after `vesper setup ha`;
VESPER_ROOT names its folder, default .vesper). Security-lab spec §0, success criterion 3: cutting one link silences
exactly its devices in Home Assistant.

It runs VESPER's broker and Home Assistant on their own ports (18830, 18123), as `vesper lab play --hub` does, and
takes about a minute.
"""
import os
import time
from pathlib import Path

import pytest

from tests.lab.homes import catalog, write_home
from vesper.exposure.mqtt import connect, disconnect
from vesper.lab.home import load_lab_home
from vesper.lab.hub import Hub
from vesper.lab.linklayer import Effect
from vesper.lab.relay import Relay, SessionSink
from vesper.lab.session import Session
from vesper.twins.clock import WallClock
from vesper.validation import stack

pytestmark = [pytest.mark.stack,
              pytest.mark.skipif(os.environ.get("VESPER_HA_TESTS") != "1", reason="set VESPER_HA_TESTS=1")]

WILL = ("zigbee2mqtt/bridge/state", '{"state": "offline"}')
ZIGBEE = ("front_door", "hall_motion", "bedroom_lamp", "bedroom_button", "kitchen_climate")


def until(check, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def ha_live(tmp_path_factory):
    from vesper.lab import hub as hub_module
    saved = dict(hub_module.AVAILABILITY_S)
    hub_module.AVAILABILITY_S.update(active=2.0, passive=2.0)
    root, tmp = Path(os.environ.get("VESPER_ROOT", ".vesper")), tmp_path_factory.mktemp("home")
    broker = stack.Mosquitto(root, port=18830)
    broker.start()
    sink = SessionSink()
    relay = Relay(record=sink.record, allowed={18830}, now=sink.now, latency=sink.latency)
    relay.start()
    ha = stack.HomeAssistant(root, automations="[]\n")
    ha.start()
    ha.onboard()
    ha.ensure_mqtt(relay.add_link("hub", 18830, client_is_device=False))
    home = load_lab_home(write_home(tmp), catalog())
    session = Session(home, clock=WallClock(), live_network=True)
    session.relay = relay
    sink.attach(session)
    session.start()
    client = connect("127.0.0.1", relay.add_link("coordinator", 18830, client_is_device=True), "vesper-lab-bridge",
                     will=WILL)
    client.reconnect_delay_set(min_delay=1, max_delay=2)
    hub = Hub(session, client)
    hub.start()
    yield session, hub, ha
    hub.stop()
    hub.bridge.retract()
    disconnect(client)
    session.stop()
    ha.stop()
    relay.stop()
    broker.stop()
    hub_module.AVAILABILITY_S.update(saved)


def available(ha, hub, name) -> bool:
    entity = hub.entities(name)
    s = ha.state(sorted(entity.values())[0])
    return s is not None and s["state"] != "unavailable"


def test_home_assistant_sees_every_zigbee_device_through_the_network(ha_live):
    session, hub, ha = ha_live
    for name in ZIGBEE:
        assert until(lambda name=name: available(ha, hub, name)), name


def test_a_cut_hop_makes_only_its_device_unavailable_in_home_assistant(ha_live):
    session, hub, ha = ha_live
    assert until(lambda: all(available(ha, hub, n) for n in ZIGBEE))
    session.network_effect(Effect("cut", link="zigbee/bedroom_lamp"))
    assert until(lambda: not available(ha, hub, "bedroom_lamp"))
    assert all(available(ha, hub, n) for n in ZIGBEE if n != "bedroom_lamp")
    session.network_clear("zigbee/bedroom_lamp")
    assert until(lambda: available(ha, hub, "bedroom_lamp"))


def test_a_reset_coordinator_link_makes_every_zigbee_device_unavailable_until_the_bridge_is_back(ha_live):
    session, hub, ha = ha_live
    assert until(lambda: all(available(ha, hub, n) for n in ZIGBEE))
    session.network_effect(Effect("cut", link="coordinator", keep_connection=False))
    assert until(lambda: not any(available(ha, hub, n) for n in ZIGBEE))
    session.network_clear("coordinator")
    assert until(lambda: all(available(ha, hub, n) for n in ZIGBEE))
```

- [ ] **Step 2: Run them against a real Mosquitto, then the real Home Assistant** (outside every window of the shared Mac; ledger rows)

Run: `VESPER_STACK_TESTS=1 taskpolicy -b .venv/bin/python -m pytest tests/lab/test_network_live.py -q 2>&1 | tail -1`

Expected: `5 passed` in about 15 s (a real Mosquitto on a free port)

Run: `VESPER_HA_TESTS=1 VESPER_ROOT=$PWD/.vesper taskpolicy -b .venv/bin/python -m pytest tests/lab/test_network_ha_live.py -q 2>&1 | tail -1`

Expected: `3 passed` in about 20 s (Mosquitto on 18830 and Home Assistant on 18123, the ports `vesper lab play --hub` uses)

- [ ] **Step 3: The docs**

In `README.md`, replace:
```markdown
| Let the residents live a day in seconds, with the home's automations | `vesper lab day` | the catalog |
| Replay any session and check that it reproduces exactly | `vesper lab replay` | the catalog |
```
with:
```markdown
| Let the residents live a day in seconds, with the home's automations | `vesper lab day` | the catalog |
| See the home's network: the router, and each link's signal and latency | `vesper lab network` | the catalog |
| Replay any session and check that it reproduces exactly | `vesper lab replay` | the catalog |
```

In `README.md`, replace:
```markdown
the standard ports 1883 and 8123, so it does not disturb a Home Assistant you already run.
```
with:
```markdown
the standard ports 1883 and 8123, so it does not disturb a Home Assistant you already run.

## The home's network

Every home has a network, as a real house does: a router, the hub (Home Assistant and VESPER's broker) wired to
it, and the Zigbee coordinator on the hub, with each Zigbee device on its own hop to the coordinator. Every
device's reports and every command cross their links, so a link's latency, a weak signal or an attack shows up
where it would in a real home. `vesper lab network` shows where the router stands and each link's quality:

    vesper lab network examples/lab/test-house/home.yaml

A link's signal comes from the home itself: the distance and the walls between its two ends, with a shut door
costing a little more. The radio constants are typical published values (ITU-R P.1238's indoor path loss), not
measured on a device, and the output says so. A home can place its router and its coordinator:

    network:
      router: {room: hall}
      coordinator: {room: bedroom, link: wifi}      # its own host, on the router's Wi-Fi

With `--hub`, Home Assistant's and the bridge's MQTT connections really cross the network: they go through a relay
on 127.0.0.1 that only ever forwards to the broker the session started (never ports 1883 or 8123). An attack acts on
those real connections, on whole MQTT messages: it can delay, drop, duplicate, reorder or throttle them, or cut a
link. A delay can let the keepalives through, so the connection stays up while a report arrives late (the
Phantom-Delay attacks). A cut is either a lost link or a reset, after which the broker publishes the bridge's will
and Home Assistant marks every Zigbee device unavailable. A device whose own hop is down goes unavailable after
Zigbee2MQTT's availability timeout, alone. Without Home Assistant, on the logical clock, the same effects apply
in-process. Every effect is an input of the trace, so a replay applies it again, and every message an effect
touched is in the trace with when it was sent and when it arrived.
```

In `README.md`, replace:
```markdown

    .venv/bin/python -m pytest          # about 860 tests, a minute or two
    .venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper_world \
```
with:
```markdown

    .venv/bin/python -m pytest          # about 900 tests, a minute or two
    .venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper_world \
```

In `README.md`, replace:
```markdown
| `VESPER_UI_BROWSER=1` | a real headless browser (the map and the 3D view) |
| `VESPER_STACK_TESTS=1` | a real Mosquitto |
| `VESPER_NETWORK_TESTS=1` | the upstream catalog sources |
```
with:
```markdown
| `VESPER_UI_BROWSER=1` | a real headless browser (the map and the 3D view) |
| `VESPER_STACK_TESTS=1` | a real Mosquitto (also the home's network end to end) |
| `VESPER_HA_TESTS=1` | the real Home Assistant of `vesper setup ha` (the network end to end; about a minute) |
| `VESPER_NETWORK_TESTS=1` | the upstream catalog sources |
```

In `README.md`, replace:
```markdown
- **Guided setup:** `vesper setup habitat`, scene downloads, and `vesper doctor`.
- **The security lab:** attacks and defenses as scenario files, run against VESPER's own local services, with their
```
with:
```markdown
- **Guided setup:** `vesper setup habitat`, scene downloads, and `vesper doctor`.
- **The home's network, continued:** Wi-Fi devices with their own connections (virtual Tasmota twins), Matter
  devices on the hub, and real 802.11 on Linux.
- **The security lab:** attacks and defenses as scenario files, run against VESPER's own local services, with their
```

In `docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md`, replace:
```markdown
| **W8** | Device physical profiles and sensing: a datasheet-to-profile pipeline, a 3D sensing model with line of sight, zones shown in the 3D view (§6a) | the 20-sensor spike; its sensing model may follow W7a |
```
with:
```markdown
| **W8** | Device physical profiles and sensing: a datasheet-to-profile pipeline, a 3D sensing model with line of sight, zones shown in the 3D view (§6a) | the 20-sensor spike; its sensing model may follow W7a |

**Progress.** W7a is built in three plans: W7a1 (the topology, the link layer, the relay, Zigbee2MQTT's availability,
`vesper lab network`; plan 2026-10-26), W7a2 (the virtual Tasmota devices), W7a3 (Matter on the hub).
```

- [ ] **Step 4: The whole suite, lint and commit**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `907 passed, 29 skipped` in an export of `main` (the working copy, with its catalog: more pass, fewer skip)

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation vesper_world tests/world`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add tests/lab/test_network_live.py tests/lab/test_network_ha_live.py README.md docs/superpowers/specs/2026-10-26-vesper-security-lab-design.md
git diff --cached --name-only   # expect exactly the paths above
git commit -m "test(lab): the home's network end to end, against a real Mosquitto and the real Home Assistant; README: the home's network

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```



---

## After this plan

- **W7a2, Wi-Fi devices:** the Tasmota templates (EPL-2.0) as a catalog source; virtual Tasmota twins, each with its own MQTT connection and Wi-Fi link through the relay; HATasmota in `vesper setup ha`.
- **W7a3, Matter on the hub:** the Matter nodes commissioned into Home Assistant's Matter server; their links' effects inside the Matter host process (a relay cannot front a node: spec §3.2).
- **Left from spec §3 for later:** Zigbee mesh routing through mains-powered routers (each device has a direct hop
  to the coordinator here); trace records of signal changes (only links going down and up are recorded); moving
  the router during play.
- **W4a, the scenario engine** (Phantom-Delay's gold scenario first), then **W4b, the paper-to-scenario MCP server**.
