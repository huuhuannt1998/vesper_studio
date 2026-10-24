"""A look with a real headless browser: opt-in (VESPER_UI_BROWSER=1), like the Matter host contract test.

It plays the bedroom scene and checks the page the way a person would see it: the lamp in reach, E lights it
(and the pixels around it get brighter), a click on the map walks the player into the hall.
"""
import os

import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.test_play import IN_BEDROOM
from vesper.lab.browser import Browser, find_browser
from vesper.lab.home import load_lab_home
from vesper.lab.play import PlayServer
from vesper.lab.png import decode, diff
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
    {"click_world": [1.5, 4.5]},
    {"until": "vesper.state.actors.player.room === 'hall'", "label": "the player walks into the hall",
     "timeout": 15},
    {"wait": 0.3},
    {"shot": "in_hall"},
]


def yellowness(img, x, y, half=30):
    """How yellow the floor around (x, y) is: red and green above blue. A lamp's glow raises it."""
    part = img.crop(int(x) - half, int(y) - half, 2 * half, 2 * half)
    px = range(0, len(part.rgba), 4)
    return sum((part.rgba[k] + part.rgba[k + 1]) / 2 - part.rgba[k + 2] for k in px) / len(px)


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
    finally:
        server.stop()
        session.stop()
    assert report.ok, [c for c in report.checks if not c.ok]
    start, lit, hall = (decode((tmp_path / "look" / f"{n}.png").read_bytes()) for n in ("start", "lamp_on", "in_hall"))
    assert yellowness(lit, *lamp) > yellowness(start, *lamp) + 15  # the glow is drawn
    assert diff(lit, hall).fraction > 0.001  # the player moved on the map
