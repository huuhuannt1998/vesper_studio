"""A look with a real headless browser: opt-in (VESPER_UI_BROWSER=1), like the Matter host contract test.

It plays the bedroom scene and checks the page the way a person would see it: the lamp in reach, E lights it
(and the pixels around it get brighter), a click on the lamp switches it off again, and a scripted walk takes the
player into the hall (play itself walks by WASD only).
"""
import os

import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.test_play import IN_BEDROOM
from vesper.lab.browser import Browser, find_browser
from vesper.lab.home import load_lab_home
from vesper.lab.play import PlayServer
from vesper.lab.png import decode
from vesper.lab.session import Session
from vesper.lab.visual import run_look
from vesper.twins.clock import WallClock

pytestmark = pytest.mark.skipif(os.environ.get("VESPER_UI_BROWSER") != "1" or find_browser() is None,
                                reason="opt-in: VESPER_UI_BROWSER=1 and a Chromium-based browser")

STEPS = [
    {"shot": "start"},
    {"expect": "vesper.state.reach[0].target === 'bedroom_lamp'", "label": "the lamp is in reach"},
    {"expect": "document.getElementById('hint').textContent.includes('bedroom_lamp')",
     "label": "the hint names the lamp"},
    {"press": "e"},
    {"until": "vesper.state.effects.bedroom_lamp.on === true", "label": "the lamp turns on", "timeout": 10},
    {"wait": 0.3},
    {"shot": "lamp_on"},
    {"click_world": [5.0, 5.0]},
    {"until": "vesper.state.effects.bedroom_lamp.on === false", "label": "a click on the lamp switches it off",
     "timeout": 10},
    {"walk": [1.5, 4.5]},
    {"until": "vesper.state.actors.player.room === 'hall' && Math.hypot(vesper.state.actors.player.x - 1.5, "
              "vesper.state.actors.player.z - 4.5) < 0.05", "label": "the player walks into the hall and stops there",
     "timeout": 15},
    {"wait": 0.3},
    {"shot": "in_hall"},
]


BESIDE_PX = 45  # floor this far right of the lamp: clear of its icon, its label and the reach ring


def yellowness(img, x, y, half=8):
    """How yellow the floor around (x, y) is: red and green above blue. A lamp's glow raises it."""
    part = img.crop(int(x) - half, int(y) - half, 2 * half, 2 * half)
    px = range(0, len(part.rgba), 4)
    return sum((part.rgba[k] + part.rgba[k + 1]) / 2 - part.rgba[k + 2] for k in px) / len(px)


def bluish(img, x, y, half=8):
    """The share of the patch around (x, y) in the player's blue."""
    part = img.crop(int(x) - half, int(y) - half, 2 * half, 2 * half)
    px = range(0, len(part.rgba), 4)
    return sum(part.rgba[k + 2] > 200 and part.rgba[k] < 90 and 90 < part.rgba[k + 1] < 140 for k in px) / len(px)


def test_a_real_browser_sees_the_lamp_light_up_and_the_player_walk(tmp_path):
    home = load_lab_home(write_home(tmp_path, IN_BEDROOM), catalog())
    session = Session(home, clock=WallClock(), trace=tmp_path / "trace.jsonl")
    session.start()
    server = PlayServer(session, port=0)
    server.start()
    try:
        with Browser.launch(width=1280, height=800) as browser:
            report = run_look(browser, server.url, STEPS, tmp_path / "look")
            lamp = browser.evaluate("vesper.toScreen(5.0, 5.0)")
            first = next(r for r in session.records if r["kind"] == "input" and r["op"] == "spawn")["args"]["at"]
            last = session.world.pose("player")
            began = browser.evaluate(f"vesper.toScreen({first[0]}, {first[1]})")
            ended = browser.evaluate(f"vesper.toScreen({last.x}, {last.z})")
    finally:
        server.stop()
        session.stop()
    assert report.ok, [c for c in report.checks if not c.ok]
    start, lit, hall = (decode((tmp_path / "look" / f"{n}.png").read_bytes()) for n in ("start", "lamp_on", "in_hall"))
    beside = (lamp[0] + BESIDE_PX, lamp[1])
    assert yellowness(lit, *beside) > yellowness(start, *beside) + 15  # the glow, not just the icon's colour
    assert bluish(lit, *began) > 0.3 and bluish(hall, *ended) > 0.3  # the player is drawn where it is
    assert bluish(hall, *began) < 0.05  # and is gone from where it started
