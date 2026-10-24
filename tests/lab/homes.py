"""A small catalog and a home on the synthetic house, for the Lab tests."""
import textwrap

from tests.catalog.test_matter_normalize import records as matter_records
from tests.lab.maps import house
from tests.twins.conftest import records as zigbee_records

LOCK = "matter:115f:0001"


def catalog():
    out = {r["id"]: r for r in zigbee_records().values()}
    out[LOCK] = matter_records()[LOCK]
    return out


HOME = """\
    version: 2
    scene: test/house
    seed: 3
    start_time: "07:30"
    avatar: {model: female_0, start: {room: hall}}
    doors:
      - {doorway: bedroom-hall, open: true}
    devices:
      - {product: "zigbee:aqara:mccgq11lm", name: front_door, at: {door: hall-porch}}
      - {product: "matter:115f:0001", name: front_lock, at: {door: hall-porch},
         state: {"Door Lock.LockState": Unlocked}}
      - {product: "zigbee:acme:pir-1", name: hall_motion, at: {room: hall, mount: ceiling}}
      - {product: "zigbee:acme:dim-1", name: bedroom_lamp, at: {room: bedroom, xy: [5.0, 5.0], mount: table}}
      - {product: "zigbee:acme:btn-1", name: bedroom_button, at: {room: bedroom, xy: [3.2, 3.5], mount: wall},
         controls: [bedroom_lamp]}
      - {product: "zigbee:acme:th-1", name: kitchen_climate, at: {room: kitchen, xy: [6.5, 0.5], mount: wall,
         facing: 90}}
"""


def write_home(tmp_path, text=HOME, world_map=None):
    (world_map or house()).save(tmp_path / "map.json")
    path = tmp_path / "home.yaml"
    path.write_text(textwrap.dedent(text))
    return path
