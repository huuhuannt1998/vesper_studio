"""The Lab home file, version 2 (spec §4): a scene, real products placed in it, the doors, the people.

    version: 2
    scene: hssd/102344022
    map: map.json                      # the scene's map file, next to home.yaml by default
    seed: 0
    start_time: "07:00"
    avatar: {model: female_0, start: {room: entryway}}
    doors: [{doorway: entryway-porch, open: false}]
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: entryway-porch}}
      - {product: "zigbee:philips:9290012607", name: living_motion, at: {room: living_room, mount: ceiling}}
      - {product: "zigbee:ikea:led1545g12", name: lamp, at: {room: living_room, xy: [3.2, -1.4], mount: table}}
      - {product: "zigbee:acme:btn-1", name: switch, at: {room: hall, xy: [0.3, 1], mount: wall},
         controls: [lamp]}

Every mistake names its field and, where one exists, the nearest valid choice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from vesper.exposure.z2m_bridge import topic_safe
from vesper.twins.twin import Twin, TwinError
from vesper.lab.geometry import Point, centroid, distance, point_in_polygon
from vesper.lab.worldmap import MapError, WorldMap, suggest

MOUNTS = ("ceiling", "wall", "table", "floor", "door")


class HomeError(ValueError):
    """A home file VESPER cannot use; the message names the field."""


@dataclass(frozen=True)
class PlacedDevice:
    name: str  # also its Zigbee2MQTT friendly name
    product: str
    record: dict
    room: str | None
    door: str | None
    position: Point
    mount: str
    facing: float | None  # degrees, for wall mounts; 0 is +x, 90 is +z
    controls: tuple[str, ...] = ()  # devices this one switches directly (a Zigbee binding)
    state: dict = field(default_factory=dict)  # starting values, checked against the product's features


@dataclass
class LabHome:
    path: Path
    map: WorldMap
    scene: str
    seed: int
    start_time: float  # seconds after midnight
    avatar: dict  # {"model": str, "start": Point}
    devices: list[PlacedDevice]
    doors: dict[str, bool]  # doorway id -> open at the start
    residents: list[dict] = field(default_factory=list)
    automations: Path | None = None
    defenses: list[str] = field(default_factory=list)
    broker: tuple[str, int] = ("127.0.0.1", 18830)
    matter_interface: str | None = None

    def device(self, name: str) -> PlacedDevice:
        for d in self.devices:
            if d.name == name:
                return d
        raise HomeError(f"unknown device {name!r}; {suggest(name, [d.name for d in self.devices])}")


def load_lab_home(path: Path, catalog: dict[str, dict]) -> LabHome:
    path = Path(path)
    spec = yaml.safe_load(path.read_text()) or {}
    if not isinstance(spec, dict) or spec.get("version") != 2:
        raise HomeError(f"{path}: a version 1 home file (no `version: 2`); it is for `vesper twins run`. "
                        "A Lab home names a scene, a map and where each device is.")
    try:
        world_map = WorldMap.load(path.parent / spec.get("map", "map.json"))
    except MapError as e:
        raise HomeError(f"map: {e}") from e
    doors = {}
    for k, d in enumerate(spec.get("doors") or []):
        _doorway(world_map, d.get("doorway"), f"doors[{k}].doorway")
        doors[d["doorway"]] = bool(d.get("open", True))
    devices: list[PlacedDevice] = []
    raw = spec.get("devices") or []
    names = [d.get("name") for d in raw]
    for k, d in enumerate(raw):
        devices.append(_device(k, d, catalog, world_map, doors, names))
    avatar = spec.get("avatar") or {}
    start = (avatar.get("start") or {}).get("room")
    if start is None:
        raise HomeError("avatar.start.room: name the room the player starts in")
    try:
        world_map.room(start)
    except MapError as e:
        raise HomeError(f"avatar.start.room: {e}") from e
    broker = spec.get("broker") or {}
    automations = spec.get("automations")
    return LabHome(path=path, map=world_map, scene=str(spec.get("scene", world_map.scene)),
                   seed=int(spec.get("seed", 0)), start_time=_clock(spec.get("start_time", "07:00")),
                   avatar={"model": avatar.get("model", "female_0"), "start": spawn_point(world_map, start)},
                   devices=devices, doors=doors, residents=list(spec.get("residents") or []),
                   automations=path.parent / automations if automations else None,
                   defenses=list((spec.get("security") or {}).get("defenses") or []),
                   broker=(broker.get("host", "127.0.0.1"), int(broker.get("port", 18830))),
                   matter_interface=spec.get("matter_interface"))


def spawn_point(world_map: WorldMap, room_id: str) -> Point:
    """The walkable cell of `room_id` nearest its centre."""
    room, g = world_map.room(room_id), world_map.grid
    c = centroid(room.polygon)
    best = None
    for j in range(g.height):
        for i in range(g.width):
            if g.is_walkable(i, j) and (r := world_map.room_of_cell(i, j)) is not None and r.id == room_id:
                d = distance(g.center(i, j), c)
                if best is None or d < best[0]:
                    best = (d, g.center(i, j))
    if best is None:
        raise HomeError(f"room {room_id!r} has no walkable floor")
    return best[1]


def _device(k: int, d: dict, catalog: dict, world_map: WorldMap, doors: dict, names: list) -> PlacedDevice:
    where = f"devices[{k}]"
    product, name = d.get("product"), d.get("name")
    if product not in catalog:
        raise HomeError(f"{where}.product: {product!r} is not in the catalog; {suggest(str(product), list(catalog))}")
    if not isinstance(name, str) or not topic_safe(name):
        raise HomeError(f"{where}.name: {name!r} cannot be an MQTT topic (no '+', '#', empty levels, 'set', 'get', "
                        "'event', a leading 'bridge' or a trailing 'availability')")
    if names.index(name) != k:
        raise HomeError(f"{where}.name: {name!r} is used twice")
    at = d.get("at") or {}
    door = at.get("door")
    mount = at.get("mount", "door" if door else "table" if "xy" in at else "ceiling")
    if mount not in MOUNTS:
        raise HomeError(f"{where}.at.mount: {mount!r} is not one of {', '.join(MOUNTS)}")
    if door is not None:
        dw = _doorway(world_map, door, f"{where}.at.door")
        doors.setdefault(door, False)  # a door with a sensor or a lock on it starts closed
        indoor = [r for r in dw.rooms if not world_map.room(r).outdoor]
        room, position, mount = (indoor or list(dw.rooms))[0], dw.center, "door"
    else:
        room = at.get("room")
        if room is None:
            raise HomeError(f"{where}.at: name a room or a door")
        try:
            polygon = world_map.room(room).polygon
        except MapError as e:
            raise HomeError(f"{where}.at.room: {e}") from e
        if "xy" in at:
            position = (float(at["xy"][0]), float(at["xy"][1]))
            if not point_in_polygon(position, polygon):
                raise HomeError(f"{where}.at.xy: {position} is not in room {room!r}")
        elif mount in ("wall", "table", "floor"):
            raise HomeError(f"{where}.at.xy: a {mount} mount needs a point (metres, in the map's x and z)")
        else:
            position = centroid(polygon)
    facing = None
    if mount == "wall":
        if "facing" in at:
            facing = float(at["facing"]) % 360
        else:
            c = centroid(world_map.room(room).polygon)
            facing = math.degrees(math.atan2(c[1] - position[1], c[0] - position[0])) % 360
    controls = tuple(d.get("controls") or ())
    for target in controls:
        if target not in names:
            raise HomeError(f"{where}.controls: unknown device {target!r}; {suggest(target, [n for n in names if n])}")
    state = dict(d.get("state") or {})
    twin = Twin.from_vds(catalog[product])
    for feature, value in state.items():
        try:
            state[feature] = twin.coerce(feature, value)
        except TwinError as e:
            raise HomeError(f"{where}.state: {e}") from e
    return PlacedDevice(name, product, catalog[product], room, door, position, mount, facing, controls, state)


def _doorway(world_map: WorldMap, doorway_id, where: str):
    try:
        return world_map.doorway(doorway_id)
    except MapError as e:
        raise HomeError(f"{where}: {e}") from e


def _clock(text) -> float:
    try:
        hours, minutes = str(text).split(":")
        return int(hours) * 3600 + int(minutes) * 60
    except ValueError as e:
        raise HomeError(f"start_time: {text!r} is not HH:MM") from e
