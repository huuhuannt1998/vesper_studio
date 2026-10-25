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
from vesper.lab.profile import Profile, ProfileError, load_profile
from vesper.lab.routine import Routine, RoutineError, load_routine
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


@dataclass(frozen=True)
class ResidentSpec:
    """A resident of the home (spec §5a): who, how they look, the day they live, and their habits."""
    name: str
    model: str
    routine: Routine
    profile: Profile


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
    residents: list[ResidentSpec] = field(default_factory=list)
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
    try:
        spec = yaml.safe_load(path.read_text()) or {}
    except OSError as e:
        raise HomeError(f"{path}: cannot read it: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise HomeError(f"{path.name}: not valid YAML: {e}") from e
    if not isinstance(spec, dict) or spec.get("version") != 2:
        raise HomeError(f"{path}: a version 1 home file (no `version: 2`); it is for `vesper twins run`. "
                        "A Lab home names a scene, a map and where each device is.")
    try:
        world_map = WorldMap.load(path.parent / _text(spec.get("map", "map.json"), "map"))
    except MapError as e:
        raise HomeError(f"map: {e}") from e
    doors = {}
    for k, d in enumerate(_list(spec.get("doors") or [], "doors")):
        d = _mapping(d, f"doors[{k}]")
        if d.get("doorway") is None:
            raise HomeError(f"doors[{k}].doorway: name a doorway; {suggest('', [w.id for w in world_map.doorways])}")
        _doorway(world_map, _text(d["doorway"], f"doors[{k}].doorway"), f"doors[{k}].doorway")
        doors[d["doorway"]] = _bool(d.get("open", True), f"doors[{k}].open")
    devices: list[PlacedDevice] = []
    raw = [_mapping(d, f"devices[{k}]") for k, d in enumerate(_list(spec.get("devices") or [], "devices"))]
    names = [d.get("name") for d in raw]
    for k, d in enumerate(raw):
        devices.append(_device(k, d, catalog, world_map, doors, names))
    avatar = _mapping(spec.get("avatar") or {}, "avatar")
    start = _mapping(avatar.get("start") or {}, "avatar.start").get("room")
    if start is None:
        raise HomeError("avatar.start.room: name the room the player starts in")
    start = _text(start, "avatar.start.room")
    try:
        world_map.room(start)
    except MapError as e:
        raise HomeError(f"avatar.start.room: {e}") from e
    broker = _mapping(spec.get("broker") or {}, "broker")
    automations = spec.get("automations")
    return LabHome(path=path, map=world_map, scene=str(spec.get("scene", world_map.scene)),
                   seed=_int(spec.get("seed", 0), "seed"), start_time=_clock(spec.get("start_time", "07:00")),
                   avatar={"model": avatar.get("model", "female_0"), "start": spawn_point(world_map, start)},
                   devices=devices, doors=doors,
                   residents=_residents(spec.get("residents") or [], path, world_map, devices),
                   automations=path.parent / automations if automations else None,
                   defenses=list(_mapping(spec.get("security") or {}, "security").get("defenses") or []),
                   broker=(_text(broker.get("host", "127.0.0.1"), "broker.host"),
                           _int(broker.get("port", 18830), "broker.port")),
                   matter_interface=spec.get("matter_interface"))


def _residents(raw, path: Path, world_map: WorldMap, devices: list[PlacedDevice]) -> list[ResidentSpec]:
    out: list[ResidentSpec] = []
    for k, r in enumerate(_list(raw, "residents")):
        where = f"residents[{k}]"
        r = _mapping(r, where)
        name = r.get("name")
        if not isinstance(name, str) or not topic_safe(name):
            raise HomeError(f"{where}.name: name the resident (letters, digits, '_' or '-')")
        if name == "player":
            raise HomeError(f"{where}.name: 'player' is the player's name")
        if any(d.name == name for d in devices):
            raise HomeError(f"{where}.name: {name!r} is a device's name")
        if any(s.name == name for s in out):
            raise HomeError(f"{where}.name: {name!r} is used twice")
        routine = r.get("routine")
        if not isinstance(routine, str):
            raise HomeError(f"{where}.routine: the resident's routine file, such as routines/{name}.yaml")
        try:
            day = load_routine(path.parent / routine, world_map)
            profile = load_profile(r.get("profile"), f"{where}.profile")
        except RoutineError as e:
            raise HomeError(f"{where}.routine: {e}") from e
        except ProfileError as e:
            raise HomeError(str(e)) from e
        out.append(ResidentSpec(name, _text(r.get("avatar", "male_0"), f"{where}.avatar"), day, profile))
    return out


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
    product, name = _text(d.get("product"), f"{where}.product"), d.get("name")
    if product not in catalog:
        raise HomeError(f"{where}.product: {product!r} is not in the catalog; {suggest(str(product), list(catalog))}")
    if not isinstance(name, str) or not topic_safe(name):
        raise HomeError(f"{where}.name: {name!r} cannot be an MQTT topic (no '+', '#', empty levels, 'set', 'get', "
                        "'event', a leading 'bridge' or a trailing 'availability')")
    if names.index(name) != k:
        raise HomeError(f"{where}.name: {name!r} is used twice")
    at = _mapping(d.get("at") or {}, f"{where}.at")
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
            position = _point(at["xy"], f"{where}.at.xy")
            if not point_in_polygon(position, polygon):
                raise HomeError(f"{where}.at.xy: {position} is not in room {room!r}")
        elif mount in ("wall", "table", "floor"):
            raise HomeError(f"{where}.at.xy: a {mount} mount needs a point (metres, in the map's x and z)")
        else:
            position = centroid(polygon)
    facing = None
    if mount == "wall":
        if "facing" in at:
            facing = _number(at["facing"], f"{where}.at.facing") % 360
        else:
            c = centroid(world_map.room(room).polygon)
            facing = math.degrees(math.atan2(c[1] - position[1], c[0] - position[0])) % 360
    controls = tuple(_list(d.get("controls") or [], f"{where}.controls"))
    for target in controls:
        if target not in names:
            raise HomeError(f"{where}.controls: unknown device {target!r}; {suggest(target, [n for n in names if n])}")
    state = dict(_mapping(d.get("state") or {}, f"{where}.state"))
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
        hours, minutes = (int(part) for part in str(text).split(":"))
    except ValueError as e:
        raise HomeError(f"start_time: {text!r} is not HH:MM") from e
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise HomeError(f"start_time: {text!r} is not HH:MM")
    return hours * 3600 + minutes * 60


# -- field types: every mistake names its field ------------------------------------------------
def _mapping(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise HomeError(f"{where}: {value!r} is not a mapping")
    return value


def _list(value, where: str) -> list:
    if not isinstance(value, list):
        raise HomeError(f"{where}: {value!r} is not a list")
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str):
        raise HomeError(f"{where}: {value!r} is not text")
    return value


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise HomeError(f"{where}: {value!r} is not a number")
    return float(value)


def _int(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HomeError(f"{where}: {value!r} is not a whole number")
    return value


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise HomeError(f"{where}: {value!r} is not true or false")
    return value


def _point(value, where: str) -> Point:
    if not (isinstance(value, list) and len(value) == 2 and all(
            not isinstance(v, bool) and isinstance(v, int | float) and math.isfinite(v) for v in value)):
        raise HomeError(f"{where}: {value!r} is not a point [x, z] in metres")
    return (float(value[0]), float(value[1]))
