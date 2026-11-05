"""A plan library of three hand-made plans in CubiCasa5K's layout (<group>/<plan>/F1_scaled.png and model.svg), for
the gallery's tests. Nothing here comes from the dataset: the SVGs are written below, the images are blank PNGs.

- hq/1: one floor, 1 px = 1 cm by its living room's dimension label: an entry hall with the front door, a living
  room, a kitchen, two bedrooms and a bathroom (3 rooms as a listing counts them: living and two bedrooms).
- hq/2: two floors side by side, no dimension label (1 cm a unit assumed): downstairs an entry hall with the front
  door, a living room and a bedroom (2 rooms); upstairs two bedrooms.
- colorful/7: a model.svg with no floor drawn: its ground truth cannot be read.
"""
from __future__ import annotations

from pathlib import Path


def door(x0, y0, x1, y1):
    return f'<g class="Door Swing Beside"><polygon points="{x0},{y0} {x1},{y0} {x1},{y1} {x0},{y1}"/></g>'


def space(cls, x0, y0, x1, y1, label=""):
    text = f'<g class="SpaceDimensionsLabel"><text>{label}</text></g>' if label else ""
    return f'<g class="Space {cls}"><polygon points="{x0},{y0} {x1},{y0} {x1},{y1} {x0},{y1}"/>{text}</g>'


def svg(*floors: str) -> str:
    body = "".join(f'<g class="Floorplan Floor-{k + 1}">{f}</g>' for k, f in enumerate(floors))
    return (f'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800">'
            f'<g class="Model"><g class="Floor">{body}</g></g></svg>')


ONE_FLOOR = svg("".join([
    space("Entry", 0, 0, 200, 300),
    space("LivingRoom", 200, 0, 700, 400, "5.00 m x 4.00 m"),
    space("Kitchen", 700, 0, 1000, 300),
    space("Bedroom", 200, 400, 550, 750),
    space("Bath", 550, 400, 750, 600),
    space("Bedroom", 750, 300, 1000, 600),
    door(-5, 100, 5, 190),  # the front door, in the hall's west wall
    door(195, 100, 205, 190),  # hall - living room
    door(695, 100, 705, 190),  # living room - kitchen
    door(300, 395, 390, 405),  # living room - bedroom
    door(600, 395, 680, 405),  # living room - bathroom
    door(800, 295, 890, 305),  # kitchen - second bedroom
    '<g class="Window Regular"><polygon points="300,-5 450,-5 450,5 300,5"/></g>',
]))

TWO_FLOORS = svg("".join([
    space("Entry", 0, 0, 200, 300),
    space("LivingRoom", 200, 0, 700, 400),
    space("Bedroom", 200, 400, 550, 750),
    door(-5, 100, 5, 190),
    door(195, 100, 205, 190),
    door(300, 395, 390, 405),
]), "".join([
    space("Bedroom", 800, 0, 1100, 350),
    space("Bedroom", 800, 350, 1100, 700),
    door(900, 345, 990, 355),
]))

NO_ENTRANCE = ONE_FLOOR.replace(door(-5, 100, 5, 190), "")  # no front door: it reads, it does not build as drawn

NO_FLOOR = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><g class="Model"/></svg>'


def blank_png(width: int, height: int) -> bytes:
    from vesper.lab.png import Image, encode
    return encode(Image(width, height, b"\xff" * (4 * width * height)))


def write_library(root: Path, extra: dict[str, str] | None = None) -> Path:
    """The three plans (and `extra`, {id: svg}) under root/cubicasa5k; returns that folder."""
    lib = Path(root) / "cubicasa5k"
    image = blank_png(1200, 800)
    for pid, text in {"hq/1": ONE_FLOOR, "hq/2": TWO_FLOORS, "colorful/7": NO_FLOOR, **(extra or {})}.items():
        d = lib / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.svg").write_text(text)
        (d / "F1_scaled.png").write_bytes(image)
    (lib / "test.txt").write_text("/hq/1/\n")  # the dataset's split lists sit beside the groups
    return lib
