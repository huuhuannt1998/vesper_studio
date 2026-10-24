# Plan W2b: play the home in the browser, and check what it shows automatically — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vesper lab play` opens a running home in the browser — the map, the player, the devices, the console — and `vesper lab look` drives that page in a headless browser and checks what a person would see, so that every change to the game is looked at, not only unit-tested.

**Architecture:**
1. `vesper/lab/png.py` reads and writes the PNGs a browser makes, compares two of them pixel by pixel, and tells a blank screen from a drawn one: pure Python, no Pillow or numpy.
2. `vesper/lab/browser.py` drives a headless Chromium over the DevTools protocol (keys, clicks, JavaScript, screenshots) and collects the page's errors. It uses the one WebSocket library the spec allows, and a browser already on the machine (Playwright's cached `chrome-headless-shell`, or Chrome); nothing is downloaded.
3. `vesper/lab/play.py` is the play server on 127.0.0.1: the static page, and one WebSocket per page that streams the session (a `hello` snapshot, then poses, effects, what is in reach, every trace record) and takes the player's inputs (move, walk to, interact), run on the session's clock thread.
4. `vesper/lab/ui/` is the page: static HTML and JavaScript, no build step, nothing from the internet. A canvas map (floor, rooms, doors, devices with their state, the glow of a lit lamp, people), the console, a hub panel and a placeholder for the 3D view (plan W1). `mapview.js` holds its arithmetic, tested with `node --test`.
5. `vesper/lab/visual.py` runs a look: a YAML list of steps (press, click on a world point, wait until, expect, shot), and checks that the page got ready, the map is drawn, no shot is blank, each shot matches its reference when references are given, and the page raised no error.
6. `vesper lab play` and `vesper lab look`.

**Tech Stack:** Python ≥3.11; `websockets>=13` (new: the spec's one WebSocket library, §7); a Chromium-based browser for `look`; Node ≥18 for the page's JavaScript tests (skipped without it).

**Spec:** `docs/superpowers/specs/2026-10-24-vesper-lab-design.md`, approved by the PI on 2026-10-24 (RKA `jrn_01M5QSGDYGE158FRVPVWKN0QNZ`). Covered here:
- §2 `vesper lab play` (play mode: WASD or arrows walk, E interacts, Tab cycles the panels; edit mode is a later plan);
- §3 the browser at 127.0.0.1:18080 with the map, the hub panel and the console (the 3D stream waits for W1; the hub is wired in W2c);
- §5 the devices → world effects on the map (a light's glow, a lock's state, an icon's state and a tooltip with the last report);
- §7 the WebSocket dependency and the static page shipped in the wheel;
- §8 the UI server's messages and the map arithmetic under `node --test`.
Requested by the PI on 2026-10-24: "I need you to check a game's visuals automatically" (`vesper lab look`).

## Global Constraints

- One new dependency: `websockets>=13` (spec §7). The page loads nothing from the internet and needs no build step.
- The play server listens on 127.0.0.1 only and refuses WebSocket connections whose `Origin` is another site.
- A look never downloads a browser: `VESPER_BROWSER`, else Playwright's cached `chrome-headless-shell`, else Chrome or Chromium; none found is a clear error.
- Nothing fails silently: a bad input gets an `error` message and the page stays connected; a bad steps file names the step; a look that fails exits 1 and says which check failed.
- Shared Mac (another project's scored run until about 2026-10-27): unit tests under `nice -n 19` / `taskpolicy -b`; the one real-browser test is opt-in (`VESPER_UI_BROWSER=1`) and runs a single headless page for about a minute. No Habitat, no Home Assistant, no broker.
- Lint gate: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`. Never `git add -A`.

## Review Focus

1. **A page from another site must not drive the avatar.** Pinned by `test_a_page_from_another_site_cannot_drive_the_avatar` (Task 3).
2. **The server serves the page's own files and nothing else,** including `..` and its percent-encoded form. Pinned by `test_the_page_and_its_files_are_served_and_nothing_else` (Task 3).
3. **A bad input never stops the session or drops the page.** Pinned by `test_a_bad_input_gets_an_error_and_the_page_stays_connected` (Task 3).
4. **A blank, broken or never-ready page fails the look; it must never pass.** Pinned by `test_a_blank_shot_fails`, `test_errors_in_the_page_fail_the_look` and `test_a_page_that_never_gets_ready_says_so` (Task 5).
5. **The check looks at the pixels a person sees:** the lamp's glow appears where the lamp is, and the player moves on the map. Pinned by the opt-in `test_a_real_browser_sees_the_lamp_light_up_and_the_player_walk` (Task 6).

## Rulings carried into this plan

- **The driver speaks the DevTools protocol itself** over `websockets`, instead of depending on Playwright or Selenium. Cost if wrong: a user without any Chromium must install Chrome or set `VESPER_BROWSER`.
- **Image checks are pure Python.** Cost if wrong: decoding a 1280×800 shot takes about a second.
- **Play runs on the wall clock, and inputs run on the clock thread,** like every other change to the world. The hub (broker, Home Assistant, Matter) is wired in plan W2c; this page says "no hub". The 3D panel is a placeholder until plan W1.
- **E uses the highlighted target and Q cycles through what is in reach,** so a lock mounted on a door can be reached (W2a deferred minor 2).
- **What looking found, and this plan fixes in the page** (from reading the first real screenshots): seams between floor rows, room labels hidden under icons, devices on one door drawn on top of each other, light glowing through walls, an unlocked lock drawn red, two categories sharing a glyph, dark labels over doors.
- **No reference images are committed.** Fonts render differently across machines; the real-browser test compares the run's own shots (the glow raises the yellowness around the lamp; the player's move changes the map). `--golden` lets a user keep their own references.
- **A browser's first start can take tens of seconds on a busy machine** (13 s measured under `taskpolicy -b`): starting gets 60 s, every later command 20 s.

---
### Task 1: Read, compare and write PNG images

**Files:**
- Create: `vesper/lab/png.py`
- Create tests: `tests/lab/test_png.py`

**Interfaces:**
- Produces in `vesper/lab/png.py`: `PngError`; `Image(width, height, rgba)` with `pixel(x, y)` and `crop(x, y, width, height)`; `Diff(fraction, image, note)`; `decode(data) -> Image` (8-bit RGB or RGBA, not interlaced); `encode(img) -> bytes`; `diff(reference, actual, tolerance=16) -> Diff`; `distinct_colors(img, limit=None) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_png.py`:
```python
import struct
import zlib

import pytest

from vesper.lab.png import Image, PngError, decode, diff, distinct_colors, encode


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _png(width, height, rows, *, color_type=6, filters=(0,), depth=8, interlace=0):
    """A PNG built the long way, with each row filtered by filters[row % len(filters)]."""
    bpp = len(rows[0]) // width
    out, prev = bytearray(), bytes(width * bpp)
    for j, row in enumerate(rows):
        f = filters[j % len(filters)]
        line = bytearray()
        for k, x in enumerate(row):
            a = row[k - bpp] if k >= bpp else 0
            b, c = prev[k], prev[k - bpp] if k >= bpp else 0
            pred = (0, a, b, (a + b) // 2, _paeth(a, b, c))[f]
            line.append((x - pred) & 0xFF)
        out += bytes([f]) + line
        prev = row

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    head = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(out))) + chunk(
        b"IEND", b"")


def _rows(width, height, bpp):
    return [bytes((7 * i + 13 * j + 31 * (i % bpp)) % 256 for i in range(width * bpp)) for j in range(height)]


def test_an_image_survives_encoding_and_decoding():
    img = Image(3, 2, bytes(range(24)))
    back = decode(encode(img))
    assert (back.width, back.height, back.rgba) == (3, 2, bytes(range(24)))
    assert back.pixel(2, 1) == (20, 21, 22, 23)


@pytest.mark.parametrize("color_type", [2, 6])
def test_every_row_filter_decodes(color_type):
    bpp = 4 if color_type == 6 else 3
    rows = _rows(5, 6, bpp)
    img = decode(_png(5, 6, rows, color_type=color_type, filters=(0, 1, 2, 3, 4)))
    for j, row in enumerate(rows):
        for i in range(5):
            px = tuple(row[i * bpp:(i + 1) * bpp]) + ((255,) if bpp == 3 else ())
            assert img.pixel(i, j) == px


@pytest.mark.parametrize("kwargs, words", [({"interlace": 1}, "interlaced"), ({"depth": 16}, "16-bit"),
                                           ({"color_type": 3}, "colour type 3")])
def test_unsupported_pngs_are_refused_by_name(kwargs, words):
    rows = _rows(2, 2, 4)
    with pytest.raises(PngError, match=words):
        decode(_png(2, 2, rows, **kwargs))


def test_bytes_that_are_not_a_png_are_refused():
    with pytest.raises(PngError, match="not a PNG"):
        decode(b"GIF89a....")


def test_diff_counts_pixels_beyond_the_tolerance_and_marks_them():
    base = Image(4, 4, bytes([100, 100, 100, 255]) * 16)
    near = bytearray(base.rgba)
    near[0] = 110  # within tolerance
    far = bytearray(base.rgba)
    far[4 * 5] = 200  # pixel (1, 1)
    assert diff(base, base).fraction == 0
    assert diff(base, Image(4, 4, bytes(near)), tolerance=16).fraction == 0
    d = diff(base, Image(4, 4, bytes(far)), tolerance=16)
    assert d.fraction == 1 / 16
    assert d.image.pixel(1, 1) == (255, 0, 0, 255)
    assert d.image.pixel(0, 0) != (255, 0, 0, 255)


def test_diff_of_different_sizes_is_total_and_says_why():
    d = diff(Image(2, 2, bytes(16)), Image(3, 2, bytes(24)))
    assert d.fraction == 1.0 and d.image is None and "2x2" in d.note and "3x2" in d.note


def test_distinct_colors_tells_a_blank_screen_from_a_drawn_one():
    blank = Image(8, 8, bytes([255, 255, 255, 255]) * 64)
    drawn = Image(8, 8, bytes(c for k in range(64) for c in (k * 4 % 256, 0, 255 - k, 255)))
    assert distinct_colors(blank) == 1
    assert distinct_colors(drawn, limit=10) == 10


def test_crop_keeps_the_region_it_names():
    img = Image(4, 3, bytes(c for k in range(12) for c in (k, k, k, 255)))
    part = img.crop(1, 1, 2, 2)
    assert (part.width, part.height) == (2, 2)
    assert [part.pixel(i, j)[0] for j in range(2) for i in range(2)] == [5, 6, 9, 10]
    with pytest.raises(PngError, match="outside"):
        img.crop(3, 0, 2, 1)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_png.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.png'`

- [ ] **Step 3: Implement**

Create `vesper/lab/png.py`:
```python
"""PNG images for the visual checks: read a browser's screenshot, compare it with a reference, write the result.

Only what a browser writes is read: 8-bit RGB or RGBA, not interlaced. Pure Python, so the checks need neither
Pillow nor numpy.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

SIGNATURE = b"\x89PNG\r\n\x1a\n"
_KINDS = {0: "greyscale", 3: "palette", 4: "greyscale with alpha"}


class PngError(ValueError):
    """An image the checks cannot read or a region outside it."""


@dataclass(frozen=True)
class Image:
    width: int
    height: int
    rgba: bytes  # row-major, 4 bytes per pixel

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        k = 4 * (y * self.width + x)
        return tuple(self.rgba[k:k + 4])

    def crop(self, x: int, y: int, width: int, height: int) -> Image:
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise PngError(f"region {width}x{height} at ({x}, {y}) is outside the {self.width}x{self.height} image")
        rows = (self.rgba[4 * (j * self.width + x):4 * (j * self.width + x + width)] for j in range(y, y + height))
        return Image(width, height, b"".join(rows))


@dataclass(frozen=True)
class Diff:
    fraction: float  # of pixels that differ beyond the tolerance
    image: Image | None  # the reference, faded, with differing pixels in red
    note: str = ""


def decode(data: bytes) -> Image:
    if not data.startswith(SIGNATURE):
        raise PngError("not a PNG (no PNG signature)")
    pos, head, idat = len(SIGNATURE), None, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if head is None:
        raise PngError("not a PNG (no IHDR chunk)")
    width, height, depth, color_type, _, _, interlace = head
    if interlace:
        raise PngError("interlaced PNGs are not supported")
    if depth != 8:
        raise PngError(f"{depth}-bit PNGs are not supported (8-bit only)")
    if color_type not in (2, 6):
        raise PngError(f"colour type {color_type} ({_KINDS.get(color_type, 'unknown')}) is not supported")
    bpp = 4 if color_type == 6 else 3
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise PngError(f"corrupt image data: {e}") from e
    stride = width * bpp
    if len(raw) < height * (stride + 1):
        raise PngError("truncated image data")
    out, prev = bytearray(), bytearray(stride)
    for j in range(height):
        start = j * (stride + 1)
        row = _unfilter(raw[start], bytearray(raw[start + 1:start + 1 + stride]), prev, bpp)
        if bpp == 3:
            wide = bytearray(width * 4)
            wide[0::4], wide[1::4], wide[2::4] = row[0::3], row[1::3], row[2::3]
            wide[3::4] = b"\xff" * width
            out += wide
        else:
            out += row
        prev = row
    return Image(width, height, bytes(out))


def _unfilter(kind: int, row: bytearray, prev: bytearray, bpp: int) -> bytearray:
    n = len(row)
    if kind == 0:
        return row
    if kind == 1:
        for k in range(bpp, n):
            row[k] = (row[k] + row[k - bpp]) & 0xFF
    elif kind == 2:
        return bytearray((a + b) & 0xFF for a, b in zip(row, prev))
    elif kind == 3:
        for k in range(n):
            row[k] = (row[k] + ((row[k - bpp] if k >= bpp else 0) + prev[k]) // 2) & 0xFF
    elif kind == 4:
        for k in range(n):
            a, b = (row[k - bpp] if k >= bpp else 0), prev[k]
            c = prev[k - bpp] if k >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            row[k] = (row[k] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
    else:
        raise PngError(f"unknown row filter {kind}")
    return row


def encode(img: Image) -> bytes:
    stride = img.width * 4
    raw = b"".join(b"\x00" + img.rgba[j * stride:(j + 1) * stride] for j in range(img.height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    head = struct.pack(">IIBBBBB", img.width, img.height, 8, 6, 0, 0, 0)
    return SIGNATURE + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def diff(reference: Image, actual: Image, tolerance: int = 16) -> Diff:
    """The pixels whose largest channel difference exceeds `tolerance` (anti-aliasing stays below it)."""
    if (reference.width, reference.height) != (actual.width, actual.height):
        return Diff(1.0, None, f"sizes differ: {reference.width}x{reference.height} reference, "
                               f"{actual.width}x{actual.height} now")
    a, b, out, bad = reference.rgba, actual.rgba, bytearray(len(reference.rgba)), 0
    for k in range(0, len(a), 4):
        if max(abs(a[k + c] - b[k + c]) for c in range(4)) > tolerance:
            out[k:k + 4] = b"\xff\x00\x00\xff"
            bad += 1
        else:
            grey = 160 + (a[k] + a[k + 1] + a[k + 2]) // 12  # the reference, faded
            out[k:k + 4] = bytes((grey, grey, grey, 255))
    return Diff(bad / (reference.width * reference.height), Image(reference.width, reference.height, bytes(out)))


def distinct_colors(img: Image, limit: int | None = None) -> int:
    """How many different colours the image holds, counting up to `limit`: 1 is a blank screen."""
    seen: set[bytes] = set()
    for k in range(0, len(img.rgba), 4):
        seen.add(img.rgba[k:k + 4])
        if limit is not None and len(seen) >= limit:
            break
    return len(seen)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `101 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/png.py tests/lab/test_png.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): PNG images for the visual checks: read, compare, write

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 2: A headless browser over the DevTools protocol

**Files:**
- Modify: `pyproject.toml` (the `websockets` dependency)
- Create: `vesper/lab/browser.py`
- Create tests: `tests/lab/test_browser.py` (a fake DevTools endpoint; no browser is started)

**Interfaces:**
- Produces in `vesper/lab/browser.py`: `BrowserError`; `find_browser(env=None, home=None, system=None) -> Path | None`; `read_devtools_port(profile, timeout, process=None) -> int`; `Browser(ws_url, *, process=None, profile=None, timeout=10.0)` with `launch(path=None, *, width=1280, height=800, timeout=20.0, start_timeout=60.0)`, `prepare(width, height)`, `call(method, **params)`, `pump(seconds)`, `goto(url, ready=..., timeout=None)`, `evaluate(expression)`, `wait_for(expression, timeout=None, interval=0.05)`, `press(key, times=1)`, `click(x, y)`, `screenshot() -> bytes`, `close()`, and `problems: list[str]`; a context manager.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, replace:
```toml
  "pyyaml>=6.0",
]
```
with:
```toml
  "pyyaml>=6.0",
  "websockets>=13",
]
```

Run: `uv pip install --python .venv/bin/python "websockets>=13"`

Expected: uv installs `websockets` (17.3 when this plan was written), or reports it already present

- [ ] **Step 2: Write the failing tests**

Create `tests/lab/test_browser.py`:
```python
import base64
import json
import threading
import time

import pytest
from websockets.sync.server import serve

from vesper.lab.browser import Browser, BrowserError, find_browser, read_devtools_port


class FakeDevTools:
    """A page's DevTools endpoint: answers each method from `results`, emitting `events[method]` first."""

    def __init__(self, results=None, events=None):
        self.results, self.events, self.received = dict(results or {}), dict(events or {}), []
        self.server = serve(self._handle, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.socket.getsockname()[1]}/devtools/page/1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handle(self, ws):
        for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            for event in self.events.get(msg["method"], []):
                ws.send(json.dumps(event))
            result = self.results.get(msg["method"], {})
            if callable(result):
                result = result(msg.get("params", {}))
            if isinstance(result, Exception):
                ws.send(json.dumps({"id": msg["id"], "error": {"code": -32000, "message": str(result)}}))
            else:
                ws.send(json.dumps({"id": msg["id"], "result": result}))

    def sent(self, method):
        return [m.get("params", {}) for m in self.received if m["method"] == method]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def devtools():
    made = []

    def make(**kw):
        made.append(FakeDevTools(**kw))
        return made[-1]
    yield make
    for d in made:
        d.close()


def test_a_call_returns_its_result_and_numbers_its_requests(devtools):
    d = devtools(results={"Page.navigate": {"frameId": "f1"}})
    with Browser(d.url) as b:
        assert b.call("Page.navigate", url="about:blank") == {"frameId": "f1"}
        b.call("Page.enable")
    assert [m["id"] for m in d.received] == [1, 2]
    assert d.sent("Page.navigate") == [{"url": "about:blank"}]


def test_a_refused_call_names_the_method_and_the_reason(devtools):
    d = devtools(results={"Page.navigate": RuntimeError("Cannot navigate to invalid URL")})
    with Browser(d.url) as b, pytest.raises(BrowserError, match="Page.navigate.*invalid URL"):
        b.call("Page.navigate", url="nope")


def test_page_problems_are_collected_from_events(devtools):
    events = [
        {"method": "Runtime.consoleAPICalled", "params": {"type": "error", "args": [{"value": "map failed"}]}},
        {"method": "Runtime.consoleAPICalled", "params": {"type": "log", "args": [{"value": "fine"}]}},
        {"method": "Runtime.exceptionThrown", "params": {"exceptionDetails": {
            "text": "Uncaught", "exception": {"description": "TypeError: x is undefined"}}}},
        {"method": "Network.requestWillBeSent", "params": {"requestId": "r1", "request": {"url": "http://h/app.js"}}},
        {"method": "Network.requestWillBeSent", "params": {"requestId": "r2", "request": {"url": "http://h/a.png"}}},
        {"method": "Network.requestWillBeSent", "params": {"requestId": "r3", "request": {"url": "http://h/b"}}},
        {"method": "Network.responseReceived", "params": {"requestId": "r1", "response": {
            "status": 404, "url": "http://h/app.js"}}},
        {"method": "Network.loadingFailed", "params": {"requestId": "r2", "errorText": "net::ERR_REFUSED"}},
        {"method": "Network.loadingFailed", "params": {"requestId": "r3", "errorText": "net::ERR_ABORTED",
                                                      "canceled": True}},
        {"method": "Log.entryAdded", "params": {"entry": {"level": "error", "source": "network", "text": "dup"}}},
        {"method": "Log.entryAdded", "params": {"entry": {"level": "error", "source": "security", "text": "CSP"}}},
    ]
    d = devtools(events={"Page.reload": events})
    with Browser(d.url) as b:
        b.call("Page.reload")
        assert b.problems == ["console.error: map failed", "exception: TypeError: x is undefined",
                              "HTTP 404 http://h/app.js", "failed to load http://h/a.png: net::ERR_REFUSED",
                              "security: CSP"]


def test_a_key_press_is_a_key_down_and_a_key_up(devtools):
    d = devtools()
    with Browser(d.url) as b:
        b.press("d")
        b.press("ArrowUp", times=2)
        with pytest.raises(BrowserError, match="unknown key 'F13'"):
            b.press("F13")
    keys = d.sent("Input.dispatchKeyEvent")
    assert keys[0] == {"type": "keyDown", "key": "d", "code": "KeyD", "windowsVirtualKeyCode": 68, "text": "d"}
    assert keys[1] == {"type": "keyUp", "key": "d", "code": "KeyD", "windowsVirtualKeyCode": 68}
    assert [k["type"] for k in keys[2:]] == ["keyDown", "keyUp"] * 2
    assert keys[2] == {"type": "keyDown", "key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38}


def test_a_click_moves_presses_and_releases_at_the_point(devtools):
    d = devtools()
    with Browser(d.url) as b:
        b.click(120, 45.5)
    mouse = d.sent("Input.dispatchMouseEvent")
    assert [m["type"] for m in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert all((m["x"], m["y"]) == (120, 45.5) for m in mouse)
    assert mouse[1]["button"] == "left" and mouse[1]["clickCount"] == 1


def test_a_screenshot_is_the_png_bytes(devtools):
    d = devtools(results={"Page.captureScreenshot": {"data": base64.b64encode(b"\x89PNG-bytes").decode()}})
    with Browser(d.url) as b:
        assert b.screenshot() == b"\x89PNG-bytes"
    assert d.sent("Page.captureScreenshot") == [{"format": "png"}]


def test_evaluate_returns_values_and_raises_page_exceptions(devtools):
    def run(params):
        if params["expression"] == "boom()":
            return {"result": {"type": "object"}, "exceptionDetails": {
                "text": "Uncaught", "exception": {"description": "ReferenceError: boom is not defined"}}}
        return {"result": {"type": "number", "value": 42}}
    d = devtools(results={"Runtime.evaluate": run})
    with Browser(d.url) as b:
        assert b.evaluate("6 * 7") == 42
        with pytest.raises(BrowserError, match="boom is not defined"):
            b.evaluate("boom()")
    assert d.sent("Runtime.evaluate")[0] == {"expression": "6 * 7", "returnByValue": True, "awaitPromise": True}


def test_wait_for_polls_until_true_and_names_what_never_happened(devtools):
    calls = []

    def run(params):
        calls.append(1)
        return {"result": {"type": "boolean", "value": len(calls) >= 3}}
    d = devtools(results={"Runtime.evaluate": run})
    with Browser(d.url) as b:
        assert b.wait_for("vesper.ready", timeout=5) is True
        assert len(calls) == 3
    d2 = devtools(results={"Runtime.evaluate": {"result": {"type": "boolean", "value": False}}})
    with Browser(d2.url) as b, pytest.raises(BrowserError, match=r"vesper\.lamp\.on.*0\.2 s.*false"):
        b.wait_for("vesper.lamp.on", timeout=0.2)


def test_find_browser_prefers_the_setting_then_the_newest_cached_headless_shell(tmp_path):
    def exe(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path
    cache = tmp_path / "home" / "Library" / "Caches" / "ms-playwright"
    exe(cache / "chromium_headless_shell-1200" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
    newest = exe(cache / "chromium_headless_shell-1234" / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
    chosen = exe(tmp_path / "my-chrome")
    assert find_browser(env={"VESPER_BROWSER": str(chosen)}, home=tmp_path / "home", system=()) == chosen
    assert find_browser(env={}, home=tmp_path / "home", system=()) == newest
    assert find_browser(env={}, home=tmp_path / "nobody", system=()) is None
    with pytest.raises(BrowserError, match="VESPER_BROWSER"):
        find_browser(env={"VESPER_BROWSER": str(tmp_path / "missing")}, home=tmp_path / "home", system=())


def test_the_debugging_port_is_read_once_the_browser_writes_it(tmp_path):
    def later():
        time.sleep(0.2)
        (tmp_path / "DevToolsActivePort").write_text("53111\n/devtools/browser/abc\n")
    threading.Thread(target=later).start()
    assert read_devtools_port(tmp_path, timeout=5) == 53111
    with pytest.raises(BrowserError, match="did not start"):
        read_devtools_port(tmp_path / "empty", timeout=0.2)
```

- [ ] **Step 3: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_browser.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.browser'`

- [ ] **Step 4: Implement**

Create `vesper/lab/browser.py`:
```python
"""A headless Chromium driven over the DevTools protocol, for the Lab's visual checks (`vesper lab look`).

It loads a page, presses keys, clicks, evaluates JavaScript and takes screenshots, and it collects what went
wrong in the page: console errors, uncaught exceptions, failed loads and HTTP errors. The browser is found, in
order: `VESPER_BROWSER`; Playwright's cached `chrome-headless-shell` (the lightest); an installed Chrome or
Chromium. Nothing is downloaded.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path

from websockets.sync.client import connect

_SYSTEM = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium", "google-chrome", "chromium", "chromium-browser")
_NAMED_KEYS = {  # key: (code, Windows virtual key code, text)
    "ArrowUp": ("ArrowUp", 38, None), "ArrowDown": ("ArrowDown", 40, None), "ArrowLeft": ("ArrowLeft", 37, None),
    "ArrowRight": ("ArrowRight", 39, None), "Tab": ("Tab", 9, None), "Enter": ("Enter", 13, "\r"),
    "Escape": ("Escape", 27, None), " ": ("Space", 32, " ")}


class BrowserError(RuntimeError):
    """The browser could not be found or started, refused a command, or a wait timed out."""


def find_browser(env=None, home: Path | None = None, system=None) -> Path | None:
    env = os.environ if env is None else env
    if env.get("VESPER_BROWSER"):
        p = Path(env["VESPER_BROWSER"])
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise BrowserError(f"VESPER_BROWSER={p} is not an executable file")
    home = Path.home() if home is None else home
    shells = []
    for cache in (home / "Library" / "Caches" / "ms-playwright", home / ".cache" / "ms-playwright"):
        for p in cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"):
            revision = p.parent.parent.name.rsplit("-", 1)[-1]
            if revision.isdigit() and os.access(p, os.X_OK):
                shells.append((int(revision), p))
    if shells:
        return max(shells)[1]
    for candidate in _SYSTEM if system is None else system:
        found = candidate if Path(candidate).is_absolute() and Path(candidate).is_file() else shutil.which(candidate)
        if found:
            return Path(found)
    return None


def read_devtools_port(profile: Path, timeout: float, process=None) -> int:
    """The port a browser started with --remote-debugging-port=0 writes to its profile."""
    deadline, f = time.monotonic() + timeout, Path(profile) / "DevToolsActivePort"
    while time.monotonic() < deadline:
        if f.exists():
            first = f.read_text().split("\n", 1)[0].strip()
            if first.isdigit():
                return int(first)
        if process is not None and process.poll() is not None:
            raise BrowserError(f"the browser exited with code {process.returncode} before it started")
        time.sleep(0.05)
    raise BrowserError(f"the browser did not start within {timeout:g} s (no DevToolsActivePort in {profile})")


def _page_target(port: int, timeout: float) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback never goes through a proxy
    deadline = time.monotonic() + timeout
    while True:
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                pages = [t for t in json.load(r) if t.get("type") == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise BrowserError(f"the browser on port {port} offered no page within {timeout:g} s")
        time.sleep(0.05)


class Browser:
    def __init__(self, ws_url: str, *, process=None, profile: Path | None = None, timeout: float = 10.0):
        self.timeout, self.process, self.profile = timeout, process, profile
        self.problems: list[str] = []
        self._stack = ExitStack()
        self._ws = self._stack.enter_context(connect(ws_url, max_size=None, open_timeout=timeout, proxy=None))
        self._next = 0
        self._urls: dict[str, str] = {}

    @classmethod
    def launch(cls, path: Path | None = None, *, width: int = 1280, height: int = 800, timeout: float = 20.0,
               start_timeout: float = 60.0) -> Browser:
        """Start a headless browser. A first start can take tens of seconds on a busy machine, hence the
        separate `start_timeout`; `timeout` bounds every later command."""
        path = path or find_browser()
        if path is None:
            raise BrowserError("no Chromium-based browser found; install Google Chrome or set VESPER_BROWSER")
        profile = Path(tempfile.mkdtemp(prefix="vesper-browser-"))
        process = subprocess.Popen(
            [str(path), "--headless", "--remote-debugging-port=0", f"--user-data-dir={profile}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--disable-extensions", "--disable-sync",
             "--disable-background-networking", "--hide-scrollbars", "--mute-audio",
             f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port = read_devtools_port(profile, start_timeout, process)
            browser = cls(_page_target(port, start_timeout), process=process, profile=profile, timeout=timeout)
            browser.prepare(width, height)
            return browser
        except BaseException:
            process.kill()
            process.wait()
            shutil.rmtree(profile, ignore_errors=True)
            raise

    def prepare(self, width: int, height: int) -> None:
        for domain in ("Page", "Runtime", "Network", "Log"):
            self.call(f"{domain}.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                  mobile=False)

    # -- the protocol -------------------------------------------------------------------------
    def call(self, method: str, **params) -> dict:
        self._next += 1
        self._ws.send(json.dumps({"id": self._next, "method": method, "params": params}))
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                msg = json.loads(self._ws.recv(timeout=max(0.0, deadline - time.monotonic())))
            except TimeoutError:
                raise BrowserError(f"{method}: no answer within {self.timeout:g} s") from None
            if "id" not in msg:
                self._on_event(msg)
            elif msg["id"] == self._next:
                if "error" in msg:
                    raise BrowserError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def pump(self, seconds: float = 0.05) -> None:
        """Take in the page's events for a moment, without a command."""
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                msg = json.loads(self._ws.recv(timeout=left))
            except TimeoutError:
                return
            if "id" not in msg:
                self._on_event(msg)

    def _on_event(self, msg: dict) -> None:
        method, p = msg.get("method"), msg.get("params", {})
        if method == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.problems.append(f"console.{p['type']}: {text}")
        elif method == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            self.problems.append(f"exception: {d.get('exception', {}).get('description') or d.get('text')}")
        elif method == "Network.requestWillBeSent":
            self._urls[p.get("requestId")] = p.get("request", {}).get("url", "?")
        elif method == "Network.responseReceived" and p.get("response", {}).get("status", 0) >= 400:
            self.problems.append(f"HTTP {p['response']['status']} {p['response'].get('url', '?')}")
        elif method == "Network.loadingFailed" and not p.get("canceled"):
            self.problems.append(f"failed to load {self._urls.get(p.get('requestId'), '?')}: {p.get('errorText')}")
        elif method == "Log.entryAdded":
            entry = p.get("entry", {})
            if entry.get("level") == "error" and entry.get("source") != "network":  # network ones are above
                self.problems.append(f"{entry.get('source')}: {entry.get('text')}")

    # -- what a test does ---------------------------------------------------------------------
    def goto(self, url: str, ready: str = "document.readyState === 'complete'", timeout: float | None = None):
        result = self.call("Page.navigate", url=url)
        if result.get("errorText"):
            raise BrowserError(f"cannot open {url}: {result['errorText']}")
        self.wait_for(ready, timeout)

    def evaluate(self, expression: str):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise BrowserError(f"{expression}: {d.get('exception', {}).get('description') or d.get('text')}")
        return r.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float | None = None, interval: float = 0.05):
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            value = self.evaluate(expression)
            if value:
                return value
            if time.monotonic() > deadline:
                raise BrowserError(f"{expression} was not true within {timeout:g} s "
                                   f"(last value: {json.dumps(value)})")
            self.pump(interval)

    def press(self, key: str, times: int = 1) -> None:
        if key in _NAMED_KEYS:
            code, vk, text = _NAMED_KEYS[key]
        elif len(key) == 1 and key.isalnum():
            code, vk, text = (f"Key{key.upper()}" if key.isalpha() else f"Digit{key}"), ord(key.upper()), key
        else:
            raise BrowserError(f"unknown key {key!r}")
        for _ in range(times):
            down = {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk}
            self.call("Input.dispatchKeyEvent", **(down | ({"text": text} if text else {})))
            self.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk)

    def click(self, x: float, y: float) -> None:
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def screenshot(self) -> bytes:
        return base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"])

    # -- the end ------------------------------------------------------------------------------
    def close(self) -> None:
        try:
            self._stack.close()
        except Exception:  # noqa: BLE001 - closing a dead connection is not an error worth raising
            pass
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.profile is not None:
            shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self) -> Browser:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
```

- [ ] **Step 5: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `111 passed`

- [ ] **Step 6: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add pyproject.toml vesper/lab/browser.py tests/lab/test_browser.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): a headless browser over the DevTools protocol, and the websockets dependency

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 3: The play server: the page's files and a WebSocket to the session

**Files:**
- Modify: `vesper/lab/session.py` (`Session.on_record`)
- Create: `vesper/lab/play.py`
- Create tests: `tests/lab/test_play.py` (the real server on a free port, a session on the wall clock)

**Interfaces:**
- Consumes: `Session` (W2a), its `world.on_event`, `rules.on_effect`, `rules.reachable`, `rules.effects`; `WorldError`.
- Produces:
  - in `vesper/lab/session.py`: `Session.on_record(fn)`, called with every trace record as it is written;
  - in `vesper/lab/play.py`: `UI_DIR`, `PlayError`, `hello(session, hub_url=None) -> dict`, `reach(session) -> list[dict]`, `PlayServer(session, *, host="127.0.0.1", port=18080, hub_url=None, ui_dir=UI_DIR)` with `start() -> int` (the port), `stop()`, `url`, `port`;
  - messages to a page: `hello` (scene, player, hub, clock, map with grid, rooms and doorways with a `box`, devices, doors, actors, reach), `pose`, `effect`, `reach`, `record`, `result`, `walk`, `error`; inputs from a page: `move` {dx, dz}, `walk_to` {x, z}, `interact` {target?}.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_play.py`:
```python
import json
import time
import urllib.request
from urllib.error import HTTPError

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.play import PlayServer
from vesper.lab.session import Session
from vesper.twins.clock import WallClock

IN_BEDROOM = HOME.replace("start: {room: hall}", "start: {room: bedroom}")  # the lamp is 0.45 m away


@pytest.fixture
def play(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>lab</h1>")
    (ui / "app.js").write_text("// app")
    (tmp_path / "secret.txt").write_text("not for the browser")
    home = load_lab_home(write_home(tmp_path, IN_BEDROOM), catalog())
    session = Session(home, clock=WallClock())
    session.start()
    server = PlayServer(session, port=0, ui_dir=ui, hub_url="http://127.0.0.1:18123")
    server.start()
    yield server
    server.stop()
    session.stop()


def client(server, origin=None):
    return connect(f"ws://127.0.0.1:{server.port}/ws", proxy=None,
                   origin=origin or f"http://127.0.0.1:{server.port}")


def until(ws, pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            msg = json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic())))
        except TimeoutError:
            break
        if pred(msg):
            return msg
        seen.append(msg["type"])
    raise AssertionError(f"no matching message within {timeout} s; saw {seen[-20:]}")


def until_all(ws, *preds, timeout=5.0):
    """Wait for one message matching each predicate, in any order; return them in the predicates' order."""
    found, deadline = [None] * len(preds), time.monotonic() + timeout
    while None in found and time.monotonic() < deadline:
        try:
            msg = json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic())))
        except TimeoutError:
            break
        for k, pred in enumerate(preds):
            if found[k] is None and pred(msg):
                found[k] = msg
                break
    assert None not in found, f"missing messages {[k for k, m in enumerate(found) if m is None]}"
    return found


def get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def test_the_page_and_its_files_are_served_and_nothing_else(play):
    base = f"http://127.0.0.1:{play.port}"
    assert get(base + "/") == (200, "text/html; charset=utf-8", b"<h1>lab</h1>")
    assert get(base + "/app.js")[:2] == (200, "text/javascript; charset=utf-8")
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/missing.js"):
        assert get(base + path)[0] == 404, path


def test_a_new_page_gets_the_whole_home_at_once(play):
    with client(play) as ws:
        hello = until(ws, lambda m: m["type"] == "hello")
    assert hello["player"] == "player" and hello["hub"] == "http://127.0.0.1:18123"
    assert {r["id"] for r in hello["map"]["rooms"]} == {"hall", "kitchen", "bedroom", "porch"}
    assert hello["map"]["grid"]["width"] == 90
    front = next(d for d in hello["map"]["doorways"] if d["id"] == "hall-porch")
    assert front["box"] == pytest.approx([-0.1, 2.5, 0.1, 3.5])  # the gap in the front wall, in metres
    lamp = next(d for d in hello["devices"] if d["name"] == "bedroom_lamp")
    assert (lamp["x"], lamp["z"], lamp["category"], lamp["vendor"], lamp["model"]) == (5.0, 5.0, "light", "Acme",
                                                                                          "DIM-1")
    assert lamp["effect"] == {"on": False}
    doors = {d["id"]: d for d in hello["doors"]}
    assert (doors["hall-porch"]["open"], doors["hall-porch"]["locked"]) == (False, False)
    assert doors["bedroom-hall"]["open"] is True
    (me,) = [a for a in hello["actors"] if a["actor"] == "player"]
    assert me["room"] == "bedroom"
    assert hello["reach"][0]["target"] == "bedroom_lamp"


def test_pressing_e_by_the_lamp_turns_it_on_and_every_page_sees_it(play):
    with client(play) as a, client(play) as b:
        for ws in (a, b):
            until(ws, lambda m: m["type"] == "hello")
        a.send(json.dumps({"type": "interact"}))
        lit = lambda m: m["type"] == "effect" and m["target"] == "bedroom_lamp" and m["effect"] == {"on": True}  # noqa: E731
        result, _ = until_all(a, lambda m: m["type"] == "result", lit)
        assert (result["ok"], result["target"], result["result"]) == (True, "bedroom_lamp", "toggled")
        until(b, lit)


def test_a_step_moves_the_player_is_traced_and_changes_what_is_in_reach(play):
    with client(play) as ws:
        hello = until(ws, lambda m: m["type"] == "hello")
        (me,) = [a for a in hello["actors"] if a["actor"] == "player"]
        ws.send(json.dumps({"type": "move", "dx": 0, "dz": -0.5}))
        pose, _ = until_all(ws, lambda m: m["type"] == "pose" and m["actor"] == "player",
                            lambda m: m["type"] == "record" and m["record"]["kind"] == "input"
                            and m["record"]["op"] == "move")
        assert (pose["x"], pose["z"]) == pytest.approx((me["x"], me["z"] - 0.5))
        ws.send(json.dumps({"type": "move", "dx": 0, "dz": -0.5}))
        assert until(ws, lambda m: m["type"] == "reach")["targets"] == []


def test_a_click_on_the_map_walks_the_player_there(play):
    with client(play) as ws:
        until(ws, lambda m: m["type"] == "hello")
        ws.send(json.dumps({"type": "walk_to", "x": 6.0, "z": 5.0}))
        walk, _ = until_all(ws, lambda m: m["type"] == "walk",
                            lambda m: m["type"] == "record" and m["record"].get("event") == "arrived")
        assert walk["ok"] is True


@pytest.mark.parametrize("raw, words", [
    ("not json", "not JSON"),
    ('{"type": "fly"}', "unknown input 'fly'"),
    ('{"type": "move", "dx": "far", "dz": 0}', "dx"),
    ('{"type": "move", "dx": NaN, "dz": 0}', "dx"),
    ('{"type": "walk_to", "x": 1}', "z"),
    ('{"type": "interact", "target": "toaster"}', "toaster"),
])
def test_a_bad_input_gets_an_error_and_the_page_stays_connected(play, raw, words):
    with client(play) as ws:
        until(ws, lambda m: m["type"] == "hello")
        ws.send(raw)
        assert words in until(ws, lambda m: m["type"] == "error")["message"]
        ws.send(json.dumps({"type": "move", "dx": 0.2, "dz": 0}))
        until(ws, lambda m: m["type"] == "pose")


def test_a_page_from_another_site_cannot_drive_the_avatar(play):
    with pytest.raises(InvalidStatus) as refused:
        client(play, origin="http://evil.example")
    assert refused.value.response.status_code == 403
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_play.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.play'`

- [ ] **Step 3: Implement**

In `vesper/lab/session.py`, replace:
```python
        self.records: list[dict] = []
        try:
```
with:
```python
        self.records: list[dict] = []
        self._listeners: list = []
        try:
```

In `vesper/lab/session.py`, replace:
```python
            self.clock.close()
```
with:
```python
            self.clock.close()

    def on_record(self, fn) -> None:
        """`fn(record)` hears every trace record as it is written (the play view's console)."""
        self._listeners.append(fn)
```

In `vesper/lab/session.py`, replace:
```python
            self._file.write(json.dumps(rec) + "\n")
```
with:
```python
            self._file.write(json.dumps(rec) + "\n")
        for fn in self._listeners:
            fn(rec)
```

Create `vesper/lab/play.py`:
```python
"""The play server (spec §2, §3): the browser page, and one WebSocket per page to the running session.

It listens on 127.0.0.1 only, and refuses WebSocket connections from another site's pages. A page gets `hello`
(the whole home) when it connects, then every change:
- `pose` {actor, x, z, yaw, room} after every step;
- `effect` {target, effect} when a device or a door looks different;
- `reach` {targets} when what the player can reach changes;
- `record` {record}, every trace record (the console);
and the answers to its own inputs: `result` (interact), `walk` (walk_to), `error`.

Inputs are `move` {dx, dz}, `walk_to` {x, z} and `interact` {target?}. They run on the session's clock thread,
like every other change to the world, so the rules never see two things at once. The session's clock must be
running (the wall clock, as play is).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import http
import json
import math
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from vesper.lab.mapworld import WorldError
from vesper.lab.session import PLAYER

UI_DIR = Path(__file__).parent / "ui"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json"}
QUEUE_MAX = 5000  # messages waiting for one page; a page that falls this far behind is disconnected


class PlayError(RuntimeError):
    """The play server could not start."""


def hello(session, hub_url: str | None = None) -> dict:
    home, world, m = session.home, session.world, session.home.map
    return {
        "type": "hello", "scene": home.scene, "player": PLAYER, "hub": hub_url,
        "clock": {"t": session.clock.now(), "start_time": home.start_time},
        "map": {"grid": m.grid.to_json(),
                "rooms": [{"id": r.id, "label": r.label, "polygon": [list(p) for p in r.polygon],
                           "outdoor": r.outdoor} for r in m.rooms],
                "doorways": [{"id": d.id, "rooms": list(d.rooms), "center": list(d.center), "width": d.width,
                              "outdoor": d.outdoor, "box": _box(m.grid, d.cells)} for d in m.doorways]},
        "devices": [_device(session, d) for d in home.devices],
        "doors": [_door(session, door) for door in world.doors],
        "actors": [_pose(world.pose(a)) for a in world.actors()],
        "reach": reach(session)}


def _box(grid, cells) -> list[float]:
    """The doorway's cells as one rectangle [x0, z0, x1, z1] in metres: where the page draws its door."""
    i0, i1 = min(c[0] for c in cells), max(c[0] for c in cells) + 1
    j0, j1 = min(c[1] for c in cells), max(c[1] for c in cells) + 1
    (x0, z0), cell = grid.origin, grid.cell
    return [round(x0 + i0 * cell, 6), round(z0 + j0 * cell, 6), round(x0 + i1 * cell, 6), round(z0 + j1 * cell, 6)]


def _device(session, d) -> dict:
    identity = d.record["identity"]
    return {"name": d.name, "product": d.product, "vendor": identity.get("vendor"), "model": identity.get("model"),
            "category": identity.get("category"), "room": d.room, "door": d.door, "x": d.position[0],
            "z": d.position[1], "mount": d.mount, "facing": d.facing, "controls": list(d.controls),
            "effect": session.rules.effects.get(d.name), "state": dict(session.rules.twin(d.name).state)}


def _door(session, door: str) -> dict:
    dw, effect = session.home.map.doorway(door), session.rules.effects.get(door, {})
    return {"id": door, "rooms": list(dw.rooms), "x": dw.center[0], "z": dw.center[1], "width": dw.width,
            "open": session.world.doors[door], "locked": bool(effect.get("locked"))}


def _pose(p) -> dict:
    return {"actor": p.actor, "model": p.model, "x": p.x, "z": p.z, "yaw": p.yaw, "room": p.room}


def reach(session) -> list[dict]:
    if PLAYER not in session.world.actors():
        return []
    return [{"target": t, "kind": k, "distance": round(d, 3)} for d, k, t in session.rules.reachable(PLAYER)]


def _number(msg: dict, key: str) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise ValueError(f"{msg.get('type')}: {key} must be a finite number, not {v!r}")
    return float(v)


class PlayServer:
    def __init__(self, session, *, host: str = "127.0.0.1", port: int = 18080, hub_url: str | None = None,
                 ui_dir: Path = UI_DIR):
        self.session, self.host, self.hub_url, self.ui_dir = session, host, hub_url, Path(ui_dir)
        self.port: int | None = None
        self._want_port = port
        self._pages: dict[asyncio.Queue, object] = {}  # each page's outgoing messages -> its connection
        self._reach = self._reach_key()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None
        session.world.on_event(self._on_world)
        session.rules.on_effect(lambda key, effect: self._emit({"type": "effect", "target": key, "effect": effect}))
        session.on_record(lambda rec: self._emit({"type": "record", "record": rec}))

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # -- life cycle ---------------------------------------------------------------------------
    def start(self) -> int:
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="vesper-play", daemon=True)
        self._thread.start()
        if not ready.wait(10):
            raise PlayError("the play server did not start within 10 s")
        if self._error is not None:
            raise PlayError(f"cannot listen on {self.host}:{self._want_port}: {self._error}")
        return self.port

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._main(ready))
        finally:
            loop.close()

    async def _main(self, ready: threading.Event) -> None:
        self._stop = asyncio.Event()
        try:
            server = await serve(self._handle, self.host, self._want_port, process_request=self._http,
                                 max_size=2 ** 16)
        except OSError as e:
            self._error = e
            ready.set()
            return
        self.port = server.sockets[0].getsockname()[1]
        self._loop = asyncio.get_running_loop()
        ready.set()
        try:
            await self._stop.wait()
        finally:
            server.close()
            await server.wait_closed()

    # -- HTTP: the page's files ---------------------------------------------------------------
    def _http(self, connection, request):
        path = unquote(urlsplit(request.path).path)
        if path == "/ws":
            origin = request.headers.get("Origin")
            if origin is not None and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
                return _response(403, "text/plain; charset=utf-8", b"a page from another site cannot play")
            return None
        root = self.ui_dir.resolve()
        f = (root / ("index.html" if path in ("", "/") else path.lstrip("/"))).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            return _response(404, "text/plain; charset=utf-8", b"not found")
        return _response(200, CONTENT_TYPES.get(f.suffix, "application/octet-stream"), f.read_bytes())

    # -- WebSocket: one page --------------------------------------------------------------------
    async def _handle(self, ws) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._pages[queue] = ws  # before the snapshot, so nothing after it is missed
        sender = None
        try:
            await ws.send(json.dumps(await self._on_clock(lambda: hello(self.session, self.hub_url))))
            sender = asyncio.create_task(self._send_queued(ws, queue))
            async for raw in ws:
                reply = await self._input(raw)
                if reply is not None:
                    queue.put_nowait(json.dumps(reply))
        except ConnectionClosed:
            pass
        finally:
            self._pages.pop(queue, None)
            if sender is not None:
                sender.cancel()

    @staticmethod
    async def _send_queued(ws, queue: asyncio.Queue) -> None:
        while True:
            await ws.send(await queue.get())

    async def _on_clock(self, fn):
        """Run `fn` on the session's clock thread and wait for its result."""
        done: concurrent.futures.Future = concurrent.futures.Future()

        def run():
            try:
                done.set_result(fn())
            except BaseException as e:  # noqa: BLE001 - handed back to the page as an error
                done.set_exception(e)
        clock = self.session.clock
        clock.call_at(clock.now(), run)
        return await asyncio.wait_for(asyncio.wrap_future(done), timeout=10)

    async def _input(self, raw) -> dict | None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return {"type": "error", "message": "an input that is not JSON"}
        if not isinstance(msg, dict):
            return {"type": "error", "message": "an input must be a JSON object"}
        kind, s = msg.get("type"), self.session
        try:
            if kind == "move":
                dx, dz = _number(msg, "dx"), _number(msg, "dz")
                await self._on_clock(lambda: s.move(dx, dz))
                return None
            if kind == "walk_to":
                x, z = _number(msg, "x"), _number(msg, "z")
                path = await self._on_clock(lambda: s.walk_to((x, z)))
                return {"type": "walk", "ok": path is not None, "x": x, "z": z}
            if kind == "interact":
                target = msg.get("target")
                if target is not None and not isinstance(target, str):
                    raise ValueError(f"interact: target must be a device or door name, not {target!r}")
                return {"type": "result", **await self._on_clock(lambda: s.interact(target))}
        except (ValueError, WorldError) as e:
            return {"type": "error", "message": str(e)}
        return {"type": "error", "message": f"unknown input {kind!r}; the inputs are move, walk_to and interact"}

    # -- the session, heard (on the clock thread) -----------------------------------------------
    def _on_world(self, event: dict) -> None:
        if event["event"] != "pose":
            return  # doors arrive as effects, the rest as records
        self._emit({"type": "pose", **{k: event[k] for k in ("actor", "x", "z", "yaw", "room")}})
        if event["actor"] == PLAYER:
            key = self._reach_key()
            if key != self._reach:
                self._reach = key
                self._emit({"type": "reach", "targets": reach(self.session)})

    def _reach_key(self) -> tuple:
        return tuple((r["kind"], r["target"]) for r in reach(self.session))

    def _emit(self, msg: dict) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._fan_out, json.dumps(msg))

    def _fan_out(self, text: str) -> None:
        for queue, ws in list(self._pages.items()):
            try:
                queue.put_nowait(text)
            except asyncio.QueueFull:
                self._pages.pop(queue, None)
                asyncio.ensure_future(ws.close(1013, "the page fell behind"))


def _response(status: int, content_type: str, body: bytes) -> Response:
    return Response(status, http.HTTPStatus(status).phrase, Headers(
        [("Content-Type", content_type), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]), body)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `123 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/session.py vesper/lab/play.py tests/lab/test_play.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the play server: the page, and a WebSocket to the running session

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 4: The page: the map, the console, the panels

**Files:**
- Create: `vesper/lab/ui/mapview.js` (the arithmetic), `vesper/lab/ui/index.html`, `vesper/lab/ui/style.css`, `vesper/lab/ui/app.js`
- Create tests: `tests/lab/ui/mapview.test.mjs` (`node --test`), `tests/lab/test_ui.py` (runs it under pytest; the page loads only its own files; it knows every catalog category)

**Interfaces:**
- Consumes: the messages of Task 3; `UI_DIR`; `CATEGORIES` from `vesper/catalog/normalize/common.py`.
- Produces:
  - in `vesper/lab/ui/mapview.js`: `STEP_M`, `GLYPHS`, `glyphFor`, `colorFor`, `decodeGrid`, `gridBounds`, `fitView`, `stepFor`, `hintFor`, `formatClock`, `recordLine`;
  - in the page, for the checks: `window.vesper` with `ready`, `connected`, `state` (`effects`, `devices`, `doors`, `actors`, `reach`), `lastResult`, `lastError`, `messages`, `toScreen(x, z)` (page coordinates of a world point) and `canvasColors()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/ui/mapview.test.mjs`:
```javascript
// The map view's arithmetic, run with `node --test` (tests/lab/test_ui.py runs it under pytest).
import assert from "node:assert/strict";
import { test } from "node:test";

import { STEP_M, decodeGrid, fitView, formatClock, hintFor, recordLine, stepFor } from "../../../vesper/lab/ui/mapview.js";

test("the walkable grid decodes bit for bit as Python wrote it (least significant bit first)", () => {
  // walkable 1,0,1 / 0,1,0 / 1,1,0 -> bits 0,2,4,6,7 -> 0xD5 0x00
  assert.deepEqual(Array.from(decodeGrid("1QA=", 3, 3)), [1, 0, 1, 0, 1, 0, 1, 1, 0]);
});

test("the view fits the map in the canvas, centred, and maps back exactly", () => {
  const v = fitView({ minX: -2, minZ: 0, maxX: 7, maxZ: 6 }, 940, 640, 20);
  assert.equal(v.scale, 100); // (940 - 40) / 9 = 100 is tighter than (640 - 40) / 6 = 100: equal here
  assert.deepEqual(v.toScreen(-2, 0), [20, 20]);
  assert.deepEqual(v.toScreen(7, 6), [920, 620]);
  const wide = fitView({ minX: 0, minZ: 0, maxX: 10, maxZ: 2 }, 400, 400, 0);
  assert.equal(wide.scale, 40);
  assert.deepEqual(wide.toScreen(0, 0), [0, 160]); // centred vertically
  for (const [x, z] of [[0, 0], [3.3, 1.7], [10, 2]]) {
    const [px, pz] = wide.toWorld(...wide.toScreen(x, z));
    assert.ok(Math.abs(px - x) < 1e-9 && Math.abs(pz - z) < 1e-9);
  }
});

test("WASD and the arrows step a quarter metre; other keys do nothing", () => {
  assert.equal(STEP_M, 0.25);
  assert.deepEqual(stepFor("w"), [0, -0.25]);
  assert.deepEqual(stepFor("ArrowDown"), [0, 0.25]);
  assert.deepEqual(stepFor("A"), [-0.25, 0]);
  assert.deepEqual(stepFor("d"), [0.25, 0]);
  assert.equal(stepFor("e"), null);
});

test("the hint names what E would use", () => {
  const devices = { bedroom_lamp: { name: "bedroom_lamp", vendor: "Acme", model: "DIM-1", category: "light" } };
  assert.equal(hintFor([], devices), "");
  assert.equal(hintFor([{ kind: "door", target: "bedroom-hall" }], devices), "E: open or close the bedroom-hall door");
  assert.equal(hintFor([{ kind: "device", target: "bedroom_lamp" }, { kind: "door", target: "x" }], devices),
    "E: use bedroom_lamp (Acme DIM-1)");
});

test("the clock shows the home's time of day and wraps at midnight", () => {
  assert.equal(formatClock(7.5 * 3600, 65.4), "07:31:05");
  assert.equal(formatClock(23 * 3600 + 59 * 60, 120), "00:01:00");
});

test("console lines say what happened in words", () => {
  const start = 7 * 3600;
  assert.equal(recordLine({ t: 5, kind: "rule", rule: "presence", device: "hall_motion", actor: "player" }, start),
    "07:00:05 presence: hall_motion sees player");
  assert.equal(recordLine({ t: 6, kind: "report", device: "bedroom_lamp", feature: "state", value: "ON" }, start),
    "07:00:06 bedroom_lamp reports state = \"ON\"");
  assert.equal(recordLine({ t: 7, kind: "rule", rule: "interact", actor: "player", target: "front_door",
    target_kind: "door", ok: false, result: "locked" }, start), "07:00:07 player uses front_door: locked");
  assert.equal(recordLine({ t: 8, kind: "world", event: "room", actor: "player", room: "hall", previous: "bedroom" },
    start), "07:00:08 player enters hall");
  assert.equal(recordLine({ t: 9, kind: "input", actor: "player", op: "move", args: {} }, start), null);
});

test("every device category has its own glyph and a colour; an unknown one is a question mark", async () => {
  const { GLYPHS, colorFor, glyphFor } = await import("../../../vesper/lab/ui/mapview.js");
  const glyphs = Object.values(GLYPHS);
  assert.equal(new Set(glyphs).size, glyphs.length);
  for (const category of Object.keys(GLYPHS)) assert.match(colorFor(category), /^#[0-9a-f]{6}$/);
  assert.equal(glyphFor("motion_sensor"), "M");
  assert.equal(glyphFor("teleporter"), "?");
  assert.equal(colorFor("teleporter"), colorFor("other"));
});
```

Create `tests/lab/test_ui.py`:
```python
"""The browser page: its JavaScript tests (under node), and that it stands alone."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vesper.lab.play import UI_DIR

TESTS = Path(__file__).parent / "ui"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_map_view_javascript_passes_its_tests():
    files = sorted(str(p) for p in TESTS.glob("*.test.mjs"))
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_the_page_loads_only_its_own_files():
    page = (UI_DIR / "index.html").read_text()
    local = re.findall(r'(?:src|href)="([^"]+)"', page)
    assert {"style.css", "app.js"} <= set(local)
    for name in local:
        assert (UI_DIR / name).is_file(), name
    for f in UI_DIR.iterdir():
        text = f.read_text()
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", text), f"{f.name} loads from the internet"
        for imported in re.findall(r'from "\./([^"]+)"', text):
            assert (UI_DIR / imported).is_file(), f"{f.name} imports a missing {imported}"


def test_the_page_knows_every_catalog_category():
    from vesper.catalog.normalize.common import CATEGORIES
    text = (UI_DIR / "mapview.js").read_text()
    glyphs = re.search(r"export const GLYPHS = \{(.*?)\};", text, re.S).group(1)
    assert set(re.findall(r"(\w+):", glyphs)) == set(CATEGORIES)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_ui.py -q 2>&1 | tail -1`

Expected: `3 failed`: `node --test` cannot find `vesper/lab/ui/mapview.js`, and `index.html` and `mapview.js` do not exist yet (`FileNotFoundError`)

- [ ] **Step 3: Implement the arithmetic, then the page**

Create `vesper/lab/ui/mapview.js`:
```javascript
// The map view's arithmetic: no DOM, so `node --test` checks it (tests/lab/ui/mapview.test.mjs).
// World coordinates are metres on the floor: +x to the right, +z down the screen (a top-down view).

export const STEP_M = 0.25; // one key press walks this far

// One glyph per catalog category (vesper/catalog/normalize/common.py CATEGORIES); tests/lab/test_ui.py checks.
export const GLYPHS = {
  light: "L", plug: "P", switch: "Sw", button: "B", lock: "K", thermostat: "Th", cover: "Cv", fan: "F",
  contact_sensor: "D", motion_sensor: "M", leak_sensor: "W", smoke_sensor: "Sm", gas_sensor: "G",
  vibration_sensor: "V", climate_sensor: "T", air_quality_sensor: "A", light_sensor: "Lx",
  energy_meter: "E", bridge: "Br", appliance: "Ap", media: "Md", camera: "Cm", other: "?",
};
const SENSOR = "#60a5fa", ALARM = "#fb7185", PLAIN = "#94a3b8";
const COLORS = {
  light: "#f2c94c", plug: "#34d399", energy_meter: "#34d399", switch: "#a78bfa", button: "#a78bfa",
  thermostat: "#f97316", cover: "#2dd4bf", fan: "#2dd4bf", lock: PLAIN,
  contact_sensor: SENSOR, motion_sensor: SENSOR, vibration_sensor: SENSOR, climate_sensor: SENSOR,
  air_quality_sensor: SENSOR, light_sensor: SENSOR, leak_sensor: ALARM, smoke_sensor: ALARM, gas_sensor: ALARM,
};

export const glyphFor = (category) => GLYPHS[category] || "?";
export const colorFor = (category) => COLORS[category] || PLAIN;

const KEYS = {
  w: [0, -1], arrowup: [0, -1], s: [0, 1], arrowdown: [0, 1],
  a: [-1, 0], arrowleft: [-1, 0], d: [1, 0], arrowright: [1, 0],
};

export function decodeGrid(b64, width, height) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const cells = new Uint8Array(width * height);
  for (let k = 0; k < cells.length; k++) cells[k] = (bytes[k >> 3] >> (k & 7)) & 1;
  return cells;
}

export function gridBounds(grid) {
  const [x0, z0] = grid.origin;
  return { minX: x0, minZ: z0, maxX: x0 + grid.width * grid.cell, maxZ: z0 + grid.height * grid.cell };
}

export function fitView(bounds, width, height, margin = 20) {
  const spanX = bounds.maxX - bounds.minX, spanZ = bounds.maxZ - bounds.minZ;
  const scale = Math.min((width - 2 * margin) / spanX, (height - 2 * margin) / spanZ);
  const ox = (width - scale * spanX) / 2, oz = (height - scale * spanZ) / 2;
  return {
    scale,
    toScreen: (x, z) => [ox + (x - bounds.minX) * scale, oz + (z - bounds.minZ) * scale],
    toWorld: (px, pz) => [bounds.minX + (px - ox) / scale, bounds.minZ + (pz - oz) / scale],
  };
}

export function stepFor(key) {
  const d = KEYS[key.toLowerCase()];
  return d ? [d[0] * STEP_M, d[1] * STEP_M] : null;
}

export function hintFor(reach, devices) {
  if (!reach.length) return "";
  const { kind, target } = reach[0];
  if (kind === "door") return `E: open or close the ${target} door`;
  const d = devices[target];
  return `E: use ${target}` + (d && d.vendor ? ` (${d.vendor} ${d.model})` : "");
}

export function formatClock(startSeconds, t) {
  const total = Math.floor(startSeconds + t) % 86400;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}`;
}

export function recordLine(r, startSeconds) {
  const text = describe(r);
  return text === null ? null : `${formatClock(startSeconds, r.t)} ${text}`;
}

function describe(r) {
  if (r.kind === "meta") return `the session starts on ${r.scene}`;
  if (r.kind === "report") return `${r.device} reports ${r.feature} = ${JSON.stringify(r.value)}`;
  if (r.kind === "rule") {
    if (r.rule === "presence") return `presence: ${r.device} sees ${r.actor}`;
    if (r.rule === "contact") return `contact: ${r.device}, the door ${r.open ? "opened" : "closed"}`;
    if (r.rule === "binding") return `binding: ${r.via} switches ${r.device}`;
    if (r.rule === "interact") return r.target ? `${r.actor} uses ${r.target}: ${r.result}` : `${r.actor}: ${r.result}`;
    return `${r.rule}: ${r.device || ""}`;
  }
  if (r.kind === "world") {
    if (r.event === "room") return `${r.actor} enters ${r.room}`;
    if (r.event === "door") return `the ${r.id} door ${r.open ? "opens" : "closes"}`;
    if (r.event === "arrived") return `${r.actor} arrives`;
    if (r.event === "blocked") return `${r.actor} is blocked`;
  }
  return null; // inputs are shown by what they cause
}
```

Create `vesper/lab/ui/index.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VESPER Lab</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <h1>VESPER Lab</h1>
    <span id="scene" class="muted"></span>
    <span id="clock" class="clock">--:--:--</span>
    <span id="status" class="status">connecting…</span>
  </header>
  <main>
    <section id="map-panel" class="panel" tabindex="0" aria-label="Map of the home">
      <canvas id="map"></canvas>
      <div id="hint" class="hint" aria-live="polite"></div>
      <div id="toast" class="toast" role="status"></div>
      <div id="tip" class="tip" hidden></div>
    </section>
    <aside>
      <section id="view-panel" class="panel" tabindex="0">
        <h2>3D view</h2>
        <p class="placeholder">The 3D view needs the Habitat world service (plan W1). The map shows the same world.</p>
      </section>
      <section id="hub-panel" class="panel" tabindex="0">
        <h2>Home Assistant</h2>
        <div id="hub" class="hub"></div>
      </section>
      <section id="console-panel" class="panel" tabindex="0">
        <h2>Console</h2>
        <ol id="log" class="log"></ol>
      </section>
    </aside>
  </main>
  <footer>WASD or arrows: walk · E: use · Q: next thing in reach · click the map: walk there · Tab: next panel</footer>
  <script type="module" src="app.js"></script>
</body>
</html>
```

Create `vesper/lab/ui/style.css`:
```css
:root {
  --bg: #12161f; --panel: #1b2130; --line: #2c3446; --text: #e6e9ef; --muted: #9aa3b5;
  --accent: #4c8dff; --warn: #ffb347; --bad: #ff6b6b;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--text); background: var(--bg);
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); }
body { display: grid; grid-template-rows: auto 1fr auto; }
header { display: flex; align-items: baseline; gap: 16px; padding: 8px 16px; border-bottom: 1px solid var(--line); }
header h1 { font-size: 18px; margin: 0; }
.muted { color: var(--muted); font-size: 13px; }
.clock { font-variant-numeric: tabular-nums; font-size: 15px; margin-left: auto; }
.status { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: var(--line); }
.status.ok { background: #1f5130; }
.status.bad { background: #6b1f25; }
main { display: grid; grid-template-columns: 1fr 380px; gap: 8px; padding: 8px; min-height: 0; }
aside { display: grid; grid-template-rows: 170px 1fr 230px; gap: 8px; min-height: 0; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; position: relative;
  min-height: 0; overflow: hidden; }
.panel:focus { outline: 2px solid var(--accent); }
.panel h2 { font-size: 13px; margin: 0; padding: 6px 10px; color: var(--muted); border-bottom: 1px solid var(--line); }
#map-panel canvas { width: 100%; height: 100%; display: block; cursor: crosshair; }
.placeholder { color: var(--muted); font-size: 13px; padding: 10px; margin: 0; }
.hub { height: calc(100% - 30px); }
.hub iframe { width: 100%; height: 100%; border: 0; background: #fff; }
.hub p { color: var(--muted); font-size: 13px; padding: 10px; margin: 0; }
.log { list-style: none; margin: 0; padding: 6px 10px; height: calc(100% - 30px); overflow-y: auto;
  font: 12px/1.45 ui-monospace, Menlo, monospace; }
.log li.report { color: #9fd3ff; }
.log li.rule { color: #ffd98a; }
.hint { position: absolute; left: 12px; bottom: 12px; background: rgba(18, 22, 31, 0.85); padding: 6px 10px;
  border-radius: 6px; font-size: 14px; }
.hint:empty { display: none; }
.toast { position: absolute; left: 50%; top: 12px; transform: translateX(-50%); padding: 6px 14px;
  border-radius: 6px; background: rgba(18, 22, 31, 0.9); font-size: 14px; opacity: 0; transition: opacity .2s; }
.toast.show { opacity: 1; }
.toast.bad { color: var(--bad); }
.tip { position: absolute; pointer-events: none; background: rgba(18, 22, 31, 0.95); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 8px; font-size: 12px; max-width: 280px; white-space: pre-wrap; }
footer { padding: 6px 16px; font-size: 12px; color: var(--muted); border-top: 1px solid var(--line); }
```

Create `vesper/lab/ui/app.js`:
```javascript
// The play page: draws the home from the server's messages and sends the player's inputs (vesper/lab/play.py).
// `window.vesper` exposes the page's state to the visual checks (`vesper lab look`).
import { colorFor, decodeGrid, fitView, formatClock, glyphFor, gridBounds, hintFor, recordLine, stepFor } from "./mapview.js";

const LOCKED = "#e74c3c", UNLOCKED = "#3fb37f";
const SLOT_PX = 40; // devices at one spot (a sensor and a lock on one door) are drawn side by side
const WALK_REPEAT_S = 0.22; // a held key walks at about walking speed (0.25 m per 0.22 s)
const LOG_MAX = 300;

const $ = (id) => document.getElementById(id);
const canvas = $("map");
const ctx = canvas.getContext("2d");
const vesper = (window.vesper = {
  ready: false, connected: false, state: null, lastResult: null, lastError: null, messages: 0,
  toScreen: (x, z) => { // page coordinates of a world point, for clicks
    const r = canvas.getBoundingClientRect(), [sx, sz] = view.toScreen(x, z);
    return [r.left + sx, r.top + sz];
  },
  canvasColors: () => { // how many colours the map shows (1 is a blank map)
    const { width, height } = canvas, data = ctx.getImageData(0, 0, width, height).data, seen = new Set();
    for (let k = 0; k < data.length && seen.size < 64; k += 4 * 97) seen.add(data[k] << 16 | data[k + 1] << 8 | data[k + 2]);
    return seen.size;
  },
});
let state = null, view = null, floor = null, dirty = true, selected = 0, lastStep = 0, helloAt = 0, toastTimer = 0;

// -- the connection -------------------------------------------------------------------------------
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => setStatus("connected", "ok");
ws.onclose = () => { vesper.connected = false; setStatus("disconnected: restart vesper lab play, then reload", "bad"); };
ws.onmessage = (e) => { vesper.messages += 1; handle(JSON.parse(e.data)); dirty = true; };
const send = (msg) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); };

function setStatus(text, kind) {
  const s = $("status");
  s.textContent = text;
  s.className = `status ${kind}`;
}

function handle(m) {
  if (m.type === "hello") return onHello(m);
  if (!state) return;
  if (m.type === "pose") state.actors[m.actor] = { ...state.actors[m.actor], ...m };
  else if (m.type === "effect") onEffect(m.target, m.effect);
  else if (m.type === "reach") { state.reach = m.targets; selected = 0; updateHint(); }
  else if (m.type === "record") onRecord(m.record);
  else if (m.type === "result") { vesper.lastResult = m; if (!m.ok) toast(m.result, true); }
  else if (m.type === "walk") { if (!m.ok) toast("there is no way there", true); }
  else if (m.type === "error") { vesper.lastError = m.message; toast(m.message, true); }
}

function onHello(h) {
  helloAt = performance.now();
  state = {
    hello: h, player: h.player, reach: h.reach, effects: {},
    devices: Object.fromEntries(h.devices.map((d) => [d.name, d])),
    doors: Object.fromEntries(h.doors.map((d) => [d.id, d])),
    doorways: Object.fromEntries(h.map.doorways.map((d) => [d.id, d])),
    actors: Object.fromEntries(h.actors.map((a) => [a.actor, a])),
    cells: decodeGrid(h.map.grid.walkable, h.map.grid.width, h.map.grid.height),
  };
  for (const d of h.devices) if (d.effect) state.effects[d.name] = d.effect;
  spread(h.devices, state.doorways);
  for (const d of h.doors) state.effects[d.id] = { open: d.open, locked: d.locked };
  vesper.state = state;
  vesper.connected = true;
  $("scene").textContent = h.scene;
  $("hub").innerHTML = "";
  if (h.hub) {
    const frame = document.createElement("iframe");
    frame.src = h.hub;
    frame.title = "Home Assistant";
    $("hub").append(frame);
  } else {
    $("hub").innerHTML = "<p>No hub in this session. With Home Assistant set up, <code>vesper lab play</code> shows its dashboard here.</p>";
  }
  layout();
  updateHint();
}

function spread(devices, doorways) {
  const groups = {};
  for (const d of devices) (groups[`${Math.round(d.x * 10)},${Math.round(d.z * 10)}`] ||= []).push(d);
  for (const group of Object.values(groups)) {
    group.forEach((d, k) => {
      const box = d.door && doorways[d.door] ? doorways[d.door].box : null;
      const alongZ = box ? box[3] - box[1] > box[2] - box[0] : false; // along the door's long side
      const offset = (k - (group.length - 1) / 2) * SLOT_PX;
      d.dx = alongZ ? 0 : offset;
      d.dz = alongZ ? offset : 0;
    });
  }
}

function devicePoint(d) {
  const [x, z] = view.toScreen(d.x, d.z);
  return [x + (d.dx || 0), z + (d.dz || 0)];
}

function roomPath(c, roomId) {
  const room = state.hello.map.rooms.find((r) => r.id === roomId);
  if (!room) return false;
  c.beginPath();
  room.polygon.forEach(([x, z], k) => { const [sx, sz] = view.toScreen(x, z); k ? c.lineTo(sx, sz) : c.moveTo(sx, sz); });
  c.closePath();
  return true;
}

function onEffect(target, effect) {
  state.effects[target] = effect;
  if (state.devices[target]) state.devices[target].effect = effect;
  if (state.doors[target]) Object.assign(state.doors[target], effect);
}

function onRecord(r) {
  if (r.kind === "report" && state.devices[r.device]) state.devices[r.device].state[r.feature] = r.value;
  const line = recordLine(r, state.hello.clock.start_time);
  if (line === null) return;
  const li = document.createElement("li");
  li.textContent = line;
  li.className = r.kind;
  const log = $("log");
  log.append(li);
  while (log.children.length > LOG_MAX) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
}

function updateHint() {
  const reach = state.reach;
  if (!reach.length) { $("hint").textContent = ""; return; }
  selected %= reach.length;
  const more = reach.length > 1 ? ` · Q: next (${reach.length} in reach)` : "";
  $("hint").textContent = hintFor([reach[selected]], state.devices) + more;
}

function toast(text, bad = false) {
  const t = $("toast");
  t.textContent = text;
  t.className = `toast show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, 2500);
}

// -- input ------------------------------------------------------------------------------------------
window.addEventListener("keydown", (e) => {
  if (!state || e.metaKey || e.ctrlKey || e.altKey) return;
  const step = stepFor(e.key);
  if (step) {
    e.preventDefault();
    const now = performance.now() / 1000;
    if (e.repeat && now - lastStep < WALK_REPEAT_S) return;
    lastStep = now;
    send({ type: "move", dx: step[0], dz: step[1] });
  } else if (e.key === "e" || e.key === "E") {
    const target = state.reach.length ? state.reach[selected % state.reach.length].target : null;
    send({ type: "interact", target });
  } else if (e.key === "q" || e.key === "Q") {
    selected += 1;
    updateHint();
  }
});

canvas.addEventListener("click", (e) => {
  if (!view) return;
  const r = canvas.getBoundingClientRect(), [x, z] = view.toWorld(e.clientX - r.left, e.clientY - r.top);
  send({ type: "walk_to", x, z });
});

canvas.addEventListener("mousemove", (e) => {
  const tip = $("tip");
  if (!view) return;
  const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, pz = e.clientY - r.top;
  const near = Object.values(state.devices).find((d) => {
    const [sx, sz] = devicePoint(d);
    return Math.hypot(sx - px, sz - pz) < 12;
  });
  tip.hidden = !near;
  if (near) {
    tip.textContent = `${near.name}\n${near.vendor} ${near.model} (${near.category})\n${JSON.stringify(near.state)}`;
    tip.style.left = `${px + 14}px`;
    tip.style.top = `${pz + 14}px`;
  }
});

// -- drawing ----------------------------------------------------------------------------------------
function layout() {
  const box = canvas.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(box.width * dpr);
  canvas.height = Math.round(box.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!state) return;
  view = fitView(gridBounds(state.hello.map.grid), box.width, box.height, 28);
  floor = drawFloor(box.width, box.height, dpr);
  dirty = true;
}

function drawFloor(width, height, dpr) {
  const g = state.hello.map.grid, off = document.createElement("canvas");
  off.width = Math.round(width * dpr);
  off.height = Math.round(height * dpr);
  const c = off.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = "#d9d3c7";
  for (let j = 0; j < g.height; j++) {
    let i = 0;
    while (i < g.width) { // one rectangle per run of walkable cells
      if (!state.cells[j * g.width + i]) { i++; continue; }
      const start = i;
      while (i < g.width && state.cells[j * g.width + i]) i++;
      const [x0, z0] = view.toScreen(g.origin[0] + start * g.cell, g.origin[1] + j * g.cell);
      const [x1, z1] = view.toScreen(g.origin[0] + i * g.cell, g.origin[1] + (j + 1) * g.cell);
      c.fillRect(Math.round(x0), Math.round(z0), Math.round(x1) - Math.round(x0), Math.round(z1) - Math.round(z0));
    }
  }
  for (const room of state.hello.map.rooms) {
    c.strokeStyle = room.outdoor ? "rgba(90, 140, 90, 0.7)" : "rgba(60, 70, 90, 0.35)";
    c.setLineDash(room.outdoor ? [4, 4] : []);
    c.beginPath();
    room.polygon.forEach(([x, z], k) => { const [sx, sz] = view.toScreen(x, z); k ? c.lineTo(sx, sz) : c.moveTo(sx, sz); });
    c.closePath();
    c.stroke();
    const [lx, lz] = view.toScreen(Math.min(...room.polygon.map((p) => p[0])), Math.min(...room.polygon.map((p) => p[1])));
    c.fillStyle = "rgba(40, 46, 60, 0.75)";
    c.font = "600 13px system-ui, sans-serif";
    c.textAlign = "left";
    c.fillText(room.label.split("/")[0], lx + 8, lz + 18);
    c.fillStyle = "#d9d3c7";
  }
  c.setLineDash([]);
  return off;
}

function draw() {
  const { width, height } = canvas.parentElement.getBoundingClientRect();
  ctx.fillStyle = "#12161f";
  ctx.fillRect(0, 0, width, height);
  if (!state || !view) return;
  ctx.drawImage(floor, 0, 0, width, height);
  const reachable = new Set(state.reach.map((r) => r.target));
  const chosen = state.reach.length ? state.reach[selected % state.reach.length].target : null;
  for (const d of Object.values(state.devices)) if (d.effect && d.effect.on) glow(d);
  for (const door of Object.values(state.doors)) drawDoor(door, reachable.has(door.id), chosen === door.id);
  for (const d of Object.values(state.devices)) drawDevice(d, reachable.has(d.name), chosen === d.name);
  for (const a of Object.values(state.actors)) drawActor(a, a.actor === state.player);
  $("clock").textContent = formatClock(state.hello.clock.start_time,
    state.hello.clock.t + (performance.now() - helloAt) / 1000);
  if (!vesper.ready) vesper.ready = true;
}

function glow(d) {
  const [x, z] = view.toScreen(d.x, d.z), r = 1.6 * view.scale;
  ctx.save();
  if (roomPath(ctx, d.room)) ctx.clip(); // light stays in its room
  const g = ctx.createRadialGradient(x, z, 0, x, z, r);
  g.addColorStop(0, "rgba(255, 214, 90, 0.75)");
  g.addColorStop(1, "rgba(255, 214, 90, 0)");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.fill();
  ctx.restore();
}

function drawDoor(door, inReach, isChosen) {
  const dw = state.doorways[door.id];
  if (!dw) return;
  const [x0, z0] = view.toScreen(dw.box[0], dw.box[1]), [x1, z1] = view.toScreen(dw.box[2], dw.box[3]);
  if (door.open) {
    ctx.strokeStyle = "rgba(60, 160, 90, 0.9)";
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x0, z0, x1 - x0, z1 - z0);
    ctx.setLineDash([]);
  } else {
    ctx.fillStyle = door.locked ? "#c0392b" : "#8a5a2b";
    ctx.fillRect(x0, z0, x1 - x0, z1 - z0);
    if (door.locked) padlock((x0 + x1) / 2, (z0 + z1) / 2);
  }
  if (inReach) ring((x0 + x1) / 2, (z0 + z1) / 2, Math.max(x1 - x0, z1 - z0) / 2 + 6, isChosen);
}

function padlock(x, z) {
  ctx.fillStyle = "#fff";
  ctx.fillRect(x - 5, z - 1, 10, 8);
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(x, z - 2, 3.5, Math.PI, 0);
  ctx.stroke();
}

function drawDevice(d, inReach, isChosen) {
  const [x, z] = devicePoint(d), r = Math.max(8, 0.16 * view.scale);
  const lock = d.effect && "locked" in d.effect;
  const active = d.state && (d.state.occupancy === true || d.state.presence === true);
  if (active) {
    ctx.strokeStyle = "rgba(255, 140, 50, 0.95)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(x, z, r + 6, 0, 2 * Math.PI);
    ctx.stroke();
  }
  ctx.fillStyle = lock ? (d.effect.locked ? LOCKED : UNLOCKED) : colorFor(d.category);
  if (d.effect && d.effect.on === false) ctx.fillStyle = "#6b7280";
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.fill();
  ctx.strokeStyle = "#1b2130";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = "#12161f";
  const glyph = glyphFor(d.category);
  ctx.font = `700 ${glyph.length > 1 ? 9 : 11}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, x, z + 0.5);
  ctx.textBaseline = "alphabetic";
  ctx.font = "11px system-ui, sans-serif";
  label(lock ? `${d.name} · ${d.effect.locked ? "locked" : "unlocked"}` : d.name, x, z + r + 13);
  if (inReach) ring(x, z, r + 10, isChosen);
}

function label(text, x, z) { // dark text on a light halo, readable over doors and walls too
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.strokeStyle = "rgba(217, 211, 199, 0.9)";
  ctx.strokeText(text, x, z);
  ctx.fillStyle = "#2a3040";
  ctx.fillText(text, x, z);
}

function ring(x, z, r, solid) {
  ctx.strokeStyle = "#4c8dff";
  ctx.lineWidth = solid ? 3 : 1.5;
  ctx.setLineDash(solid ? [] : [3, 3]);
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawActor(a, isPlayer) {
  const [x, z] = view.toScreen(a.x, a.z), r = Math.max(7, 0.22 * view.scale), yaw = (a.yaw * Math.PI) / 180;
  ctx.fillStyle = isPlayer ? "#2f6fed" : "#7a7f8c";
  ctx.beginPath();
  ctx.arc(x, z, r, 0, 2 * Math.PI);
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x, z);
  ctx.lineTo(x + Math.cos(yaw) * r * 1.4, z + Math.sin(yaw) * r * 1.4);
  ctx.stroke();
  ctx.font = "600 11px system-ui, sans-serif";
  ctx.textAlign = "center";
  label(isPlayer ? "you" : a.actor, x, z - r - 5);
}

window.addEventListener("resize", layout);
(function loop() {
  if (dirty) { dirty = false; draw(); }
  requestAnimationFrame(loop);
})();
setInterval(() => { dirty = true; }, 1000); // the clock
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `126 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/ui/mapview.js vesper/lab/ui/index.html vesper/lab/ui/style.css vesper/lab/ui/app.js tests/lab/ui/mapview.test.mjs tests/lab/test_ui.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): the play page: the map, the console and the panels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 5: A look: drive the page, photograph it, judge it

**Files:**
- Create: `vesper/lab/visual.py`
- Create tests: `tests/lab/test_visual.py` (a fake browser)

**Interfaces:**
- Consumes: `BrowserError` (Task 2); `decode`, `encode`, `diff`, `distinct_colors`, `PngError` (Task 1); the page's `window.vesper` (Task 4).
- Produces in `vesper/lab/visual.py`: `READY`, `ACTIONS`, `DEFAULT_STEPS`, `LookError`, `Check(name, ok, detail)`, `Report(url, out, checks, shots)` with `ok`, `load_steps(path) -> list[dict]`, `run_look(browser, url, steps, out, *, golden=None, update_golden=False, tolerance=16, max_diff=0.002, ready_timeout=15.0) -> Report`, which writes `report.json`, `report.md` and the shots.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_visual.py`:
```python
import json

import pytest

from vesper.lab.browser import BrowserError
from vesper.lab.png import Image, decode, encode
from vesper.lab.visual import READY, LookError, load_steps, run_look


def picture(seed=0, size=(40, 30)):
    w, h = size
    return Image(w, h, bytes(c for k in range(w * h) for c in ((k * 7 + seed) % 256, (k * 3) % 256, 90, 255)))


class FakeBrowser:
    """Answers what the page would: `values` maps an expression to its value (or a callable)."""

    def __init__(self, values=None, shots=None, problems=()):
        self.values = {READY: True, "vesper.canvasColors()": 40, **(values or {})}
        self.shots = list(shots or [picture()])
        self.problems, self.calls = list(problems), []

    def goto(self, url, ready=None, timeout=None):
        self.calls.append(("goto", url, ready))

    def evaluate(self, expression):
        self.calls.append(("evaluate", expression))
        v = self.values.get(expression)
        return v() if callable(v) else v

    def wait_for(self, expression, timeout=None, interval=0.05):
        self.calls.append(("wait_for", expression, timeout))
        v = self.evaluate(expression)
        if not v:
            raise BrowserError(f"{expression} was not true within {timeout:g} s (last value: {json.dumps(v)})")
        return v

    def press(self, key, times=1):
        self.calls.append(("press", key, times))

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def pump(self, seconds=0.05):
        pass

    def screenshot(self):
        img = self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]
        return encode(img)


def test_the_steps_run_in_order_and_every_shot_is_saved(tmp_path):
    steps = [{"press": "d", "times": 3}, {"click_world": [5.0, 5.0]}, {"until": "vesper.lamp", "timeout": 2},
             {"expect": "vesper.hint.includes('lamp')", "label": "the hint names the lamp"}, {"shot": "lamp_on"}]
    b = FakeBrowser({"vesper.toScreen(5.0, 5.0)": [412.5, 300.0], "vesper.lamp": True,
                     "vesper.hint.includes('lamp')": True})
    report = run_look(b, "http://127.0.0.1:9/", steps, tmp_path / "out")
    assert report.ok, report.checks
    assert ("goto", "http://127.0.0.1:9/", READY) == b.calls[0]
    acts = [c for c in b.calls if c[0] in ("press", "click", "wait_for")]
    assert acts[1:] == [("press", "d", 3), ("click", 412.5, 300.0), ("wait_for", "vesper.lamp", 2)]
    assert decode((tmp_path / "out" / "lamp_on.png").read_bytes()).width == 40
    names = [c.name for c in report.checks]
    assert names == ["the page is ready", "the map is drawn", "vesper.lamp", "the hint names the lamp",
                     "lamp_on is not blank", "no page errors"]
    saved = json.loads((tmp_path / "out" / "report.json").read_text())
    assert saved["ok"] is True and saved["shots"] == ["lamp_on.png"]
    assert "lamp_on.png" in (tmp_path / "out" / "report.md").read_text()


def test_a_blank_shot_fails(tmp_path):
    blank = Image(20, 20, bytes([255, 255, 255, 255]) * 400)
    report = run_look(FakeBrowser(shots=[blank]), "http://x/", [{"shot": "start"}], tmp_path)
    check = next(c for c in report.checks if c.name == "start is not blank")
    assert not check.ok and not report.ok and "1 colour" in check.detail


def test_shots_are_compared_with_their_references(tmp_path):
    ref = tmp_path / "golden"
    first = run_look(FakeBrowser(shots=[picture(0)]), "http://x/", [{"shot": "start"}], tmp_path / "a",
                     golden=ref, update_golden=True)
    assert first.ok and (ref / "start.png").is_file()
    same = run_look(FakeBrowser(shots=[picture(0)]), "http://x/", [{"shot": "start"}], tmp_path / "b", golden=ref)
    assert same.ok
    moved = run_look(FakeBrowser(shots=[picture(100)]), "http://x/", [{"shot": "start"}], tmp_path / "c", golden=ref)
    check = next(c for c in moved.checks if c.name == "start matches its reference")
    assert not check.ok and "% of pixels differ" in check.detail
    assert (tmp_path / "c" / "start.diff.png").is_file()
    missing = run_look(FakeBrowser(), "http://x/", [{"shot": "other"}], tmp_path / "d", golden=ref)
    assert "--update-golden" in next(c for c in missing.checks if c.name == "other matches its reference").detail


def test_a_wait_that_never_ends_fails_and_skips_the_rest(tmp_path):
    steps = [{"until": "vesper.lamp", "timeout": 0.1}, {"press": "e"}, {"shot": "after"}]
    b = FakeBrowser({"vesper.lamp": False})
    report = run_look(b, "http://x/", steps, tmp_path)
    assert not report.ok
    failed = next(c for c in report.checks if c.name == "vesper.lamp")
    assert not failed.ok and "not true within 0.1 s" in failed.detail
    assert next(c for c in report.checks if c.name == "steps 2-3").detail == "skipped after a failed step"
    assert not any(c[0] == "press" for c in b.calls)


def test_errors_in_the_page_fail_the_look(tmp_path):
    report = run_look(FakeBrowser(problems=["exception: TypeError: x"]), "http://x/", [], tmp_path)
    check = next(c for c in report.checks if c.name == "no page errors")
    assert not check.ok and "TypeError" in check.detail


def test_a_page_that_never_gets_ready_says_so(tmp_path):
    report = run_look(FakeBrowser({READY: False}), "http://x/", [{"shot": "start"}], tmp_path)
    assert not report.ok and report.checks[0].name == "the page is ready" and not report.checks[0].ok


@pytest.mark.parametrize("text, words", [
    ("- {fly: 1}", "step 1: unknown action 'fly'"),
    ("- {shot: a}\n- {press: e, shot: b}", "step 2: one action per step"),
    ("- {click_world: [1]}", "step 1: click_world needs [x, z]"),
    ("- {press: e, times: 0}", "step 1: times"),
    ("- {shot: '../escape'}", "step 1: a shot's name"),
    ("shot: a", "a list of steps"),
])
def test_a_bad_steps_file_names_the_step(tmp_path, text, words):
    f = tmp_path / "look.yaml"
    f.write_text(text)
    with pytest.raises(LookError, match=words.replace("[", r"\[")):
        load_steps(f)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_visual.py -q 2>&1 | tail -1`

Expected: `1 error`; collection stops on `ModuleNotFoundError: No module named 'vesper.lab.visual'`

- [ ] **Step 3: Implement**

Create `vesper/lab/visual.py`:
```python
"""Automatic visual checks of the play page (`vesper lab look`): drive it, photograph it, judge it.

A steps file is a YAML list, one action per step:

    - {until: "vesper.state.reach.length > 0", timeout: 5}   # wait until the page shows it
    - {press: e}                                              # a key; `times: 3` repeats it
    - {click_world: [5.0, 4.5]}                               # a click on the map at a world point
    - {wait: 0.5}                                             # seconds
    - {expect: "vesper.state.effects.bedroom_lamp.on", label: "the lamp is on"}
    - {shot: lamp_on}                                         # a screenshot, lamp_on.png

Every look also checks that the page got ready, that the map is drawn, that no shot is blank, that each shot
matches its reference image when references are given, and that the page raised no error. The result is
`report.json`, `report.md` and the screenshots, in the output directory.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from vesper.lab.browser import BrowserError
from vesper.lab.png import PngError, decode, diff, distinct_colors, encode

READY = "window.vesper !== undefined && window.vesper.ready === true"
ACTIONS = ("shot", "press", "click_world", "wait", "until", "expect")
OPTIONS = {"press": {"times"}, "until": {"timeout", "label"}, "expect": {"label"}}
MIN_COLOURS = 8  # fewer colours than this in a shot of the play page means nothing was drawn
DEFAULT_STEPS = [{"shot": "start"}]


class LookError(ValueError):
    """A steps file the checks cannot run; the message names the step."""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    url: str
    out: Path
    checks: list[Check] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def load_steps(path: Path) -> list[dict]:
    try:
        steps = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise LookError(f"{path}: {e}") from e
    if not isinstance(steps, list):
        raise LookError(f"{path}: a steps file is a list of steps, one action each")
    for k, step in enumerate(steps, 1):
        _check_step(k, step)
    return steps


def _check_step(k: int, step) -> None:
    if not isinstance(step, dict):
        raise LookError(f"step {k}: a step is a mapping such as {{press: e}}")
    actions = [a for a in step if a in ACTIONS]
    unknown = [a for a in step if a not in ACTIONS and not any(a in o for o in OPTIONS.values())]
    if unknown:
        raise LookError(f"step {k}: unknown action {unknown[0]!r}; the actions are {', '.join(ACTIONS)}")
    if len(actions) != 1:
        raise LookError(f"step {k}: one action per step (found {', '.join(actions) or 'none'})")
    action, value = actions[0], step[actions[0]]
    extra = set(step) - {action} - OPTIONS.get(action, set())
    if extra:
        raise LookError(f"step {k}: {action} takes no {', '.join(sorted(extra))}")
    if action == "shot" and not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value)):
        raise LookError(f"step {k}: a shot's name is letters, digits, '-' and '_' (it names the file)")
    if action == "press":
        times = step.get("times", 1)
        if not isinstance(value, str) or not value:
            raise LookError(f"step {k}: press needs a key, such as e or ArrowUp")
        if isinstance(times, bool) or not isinstance(times, int) or times < 1:
            raise LookError(f"step {k}: times must be a whole number of at least 1")
    if action == "click_world" and not (isinstance(value, list) and len(value) == 2 and all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in value)):
        raise LookError(f"step {k}: click_world needs [x, z] in metres")
    if action == "wait" and (isinstance(value, bool) or not isinstance(value, int | float) or value < 0):
        raise LookError(f"step {k}: wait needs a number of seconds")
    if action in ("until", "expect") and not (isinstance(value, str) and value.strip()):
        raise LookError(f"step {k}: {action} needs a JavaScript expression")


def run_look(browser, url: str, steps: list[dict], out: Path, *, golden: Path | None = None,
             update_golden: bool = False, tolerance: int = 16, max_diff: float = 0.002,
             ready_timeout: float = 15.0) -> Report:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report = Report(url, out)
    checks = report.checks
    try:
        browser.goto(url, ready=READY, timeout=ready_timeout)
        browser.wait_for(READY, timeout=ready_timeout)
        checks.append(Check("the page is ready", True))
    except BrowserError as e:
        checks.append(Check("the page is ready", False, str(e)))
        _finish(browser, report, skipped=(1, len(steps)) if steps else None)
        return report
    colours = browser.evaluate("vesper.canvasColors()") or 0
    checks.append(Check("the map is drawn", colours >= MIN_COLOURS, f"{colours} colours on the map"))
    for k, step in enumerate(steps, 1):
        try:
            if not _step(browser, step, report, golden, update_golden, tolerance, max_diff):
                _finish(browser, report, skipped=(k + 1, len(steps)) if k < len(steps) else None)
                return report
        except (BrowserError, PngError) as e:
            action = next(a for a in step if a in ACTIONS)
            checks.append(Check(f"step {k} ({action})", False, str(e)))
            _finish(browser, report, skipped=(k + 1, len(steps)) if k < len(steps) else None)
            return report
    _finish(browser, report)
    return report


def _step(browser, step: dict, report: Report, golden, update_golden, tolerance, max_diff) -> bool:
    """Run one step; False when a failed step makes the rest meaningless."""
    checks = report.checks
    if "press" in step:
        browser.press(step["press"], times=step.get("times", 1))
    elif "click_world" in step:
        x, z = step["click_world"]
        px, py = browser.evaluate(f"vesper.toScreen({x}, {z})")
        browser.click(px, py)
    elif "wait" in step:
        deadline = time.monotonic() + step["wait"]
        while time.monotonic() < deadline:
            browser.pump(min(0.05, max(0.0, deadline - time.monotonic())))
    elif "until" in step:
        name = step.get("label", step["until"])
        try:
            browser.wait_for(step["until"], timeout=step.get("timeout", 10))
        except BrowserError as e:
            checks.append(Check(name, False, str(e)))
            return False
        checks.append(Check(name, True))
    elif "expect" in step:
        value = browser.evaluate(step["expect"])
        checks.append(Check(step.get("label", step["expect"]), bool(value), json.dumps(value)))
    elif "shot" in step:
        _shot(browser, step["shot"], report, golden, update_golden, tolerance, max_diff)
    return True


def _shot(browser, name: str, report: Report, golden, update_golden, tolerance, max_diff) -> None:
    data = browser.screenshot()
    (report.out / f"{name}.png").write_bytes(data)
    report.shots.append(f"{name}.png")
    img = decode(data)
    n = distinct_colors(img, limit=MIN_COLOURS + 1)
    report.checks.append(Check(f"{name} is not blank", n > MIN_COLOURS,
                               f"{n} colour{'s' if n != 1 else ''}{'+' if n > MIN_COLOURS else ''}"))
    if golden is None:
        return
    ref = Path(golden) / f"{name}.png"
    if update_golden:
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_bytes(data)
        report.checks.append(Check(f"{name} matches its reference", True, f"reference saved to {ref}"))
        return
    if not ref.is_file():
        report.checks.append(Check(f"{name} matches its reference", False,
                                   f"no reference {ref}; run with --update-golden to save one"))
        return
    d = diff(decode(ref.read_bytes()), img, tolerance=tolerance)
    if d.image is not None and d.fraction > max_diff:
        (report.out / f"{name}.diff.png").write_bytes(encode(d.image))
    detail = d.note or f"{d.fraction:.3%} of pixels differ (at most {max_diff:.3%} may)"
    report.checks.append(Check(f"{name} matches its reference", d.fraction <= max_diff, detail))


def _finish(browser, report: Report, skipped: tuple[int, int] | None = None) -> None:
    if skipped is not None:
        a, b = skipped
        report.checks.append(Check(f"step {a}" if a == b else f"steps {a}-{b}", False, "skipped after a failed step"))
    browser.pump(0.1)
    problems = list(browser.problems)
    report.checks.append(Check("no page errors", not problems, "; ".join(problems)))
    _write(report)


def _write(report: Report) -> None:
    summary = {"url": report.url, "ok": report.ok, "when": datetime.now(UTC).isoformat(timespec="seconds"),
               "checks": [asdict(c) for c in report.checks], "shots": report.shots}
    (report.out / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [f"# VESPER Lab look: {'PASS' if report.ok else 'FAIL'}", "", f"{report.url}, {summary['when']}", ""]
    lines += [f"- {'PASS' if c.ok else 'FAIL'} {c.name}" + (f": {c.detail}" if c.detail else "") for c in report.checks]
    lines += ["", "Shots:"] + [f"- {s}" for s in report.shots]
    (report.out / "report.md").write_text("\n".join(lines) + "\n")
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `138 passed`

- [ ] **Step 5: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/lab/visual.py tests/lab/test_visual.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): a look: drive the play page, photograph it and judge it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


### Task 6: `vesper lab play` and `vesper lab look`, and a look with a real browser

**Files:**
- Modify: `vesper/cli.py` (`_lab` dispatch, `_lab_play`, `_wait_for_interrupt`, `_lab_look`; the `play` and `look` parsers)
- Create tests: `tests/lab/test_cli_play.py` (the real server, a fake browser), `tests/lab/test_look_browser.py` (opt-in: a real headless browser)

**Interfaces:**
- Consumes: `PlayServer`, `PlayError` (Task 3); `Browser`, `BrowserError` (Task 2); `load_steps`, `run_look`, `DEFAULT_STEPS`, `LookError` (Task 5); `Session`, `_new_trace` (W2a); `WallClock`.
- Produces:
  - `vesper lab play <home> [--catalog] [--port 18080] [--trace] [--no-browser]`: the session on the wall clock, the page at `http://127.0.0.1:<port>/`, until Ctrl-C;
  - `vesper lab look <home> [--catalog] [--steps] [--out] [--golden] [--update-golden] [--browser] [--width] [--height]`: exits 0 when every check passes, 1 otherwise, and prints each check.

- [ ] **Step 1: Write the failing tests**

Create `tests/lab/test_cli_play.py`:
```python
"""`vesper lab play` and `vesper lab look`, with the real play server on a free port and a fake browser."""
import json
import re
import urllib.request

from tests.lab.test_cli_lab import setup
from tests.lab.test_visual import FakeBrowser
from vesper import cli
from vesper.lab import browser as browser_mod
from vesper.lab.browser import BrowserError


def get(url):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as r:
        return r.read()


def test_play_serves_the_home_until_interrupted_and_keeps_its_trace(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    seen = {}

    def interrupted():
        seen["out"] = capsys.readouterr().out
        seen["page"] = get(re.search(r"http://127\.0\.0\.1:\d+/", seen["out"]).group(0))
    monkeypatch.setattr(cli, "_wait_for_interrupt", interrupted)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0", "--no-browser"]) == 0
    assert "6 devices" in seen["out"] and b"VESPER Lab" in seen["page"]
    (trace,) = (home.parent / "traces").glob("*.jsonl")
    assert json.loads(trace.read_text().splitlines()[0])["format"] == "vesper-trace/1"


def test_play_opens_the_browser_unless_told_not_to(tmp_path, monkeypatch):
    cat, home = setup(tmp_path)
    opened = []
    monkeypatch.setattr(cli, "_wait_for_interrupt", lambda: None)
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert cli.main(["lab", "play", str(home), "--catalog", str(cat), "--port", "0"]) == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")


def test_look_runs_the_steps_and_reports_each_check(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    steps = tmp_path / "look.yaml"
    steps.write_text("- {shot: start}\n- {press: e}\n")
    fake = FakeBrowser()
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(lambda cls, path=None, **kw: fake))
    out = tmp_path / "look"
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(steps), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "PASS the page is ready" in printed and "PASS start is not blank" in printed
    assert f"1 shots and the report in {out}" in printed
    assert fake.calls[0][1].startswith("http://127.0.0.1:") and ("press", "e", 1) in fake.calls
    assert (out / "start.png").is_file() and (out / "report.md").is_file()
    assert len(list(out.glob("*-look-*.jsonl"))) == 1  # the session's trace, beside the shots


def test_look_fails_when_a_check_fails(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)
    fake = FakeBrowser(problems=["exception: TypeError: draw"])
    fake.close = lambda: None
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(lambda cls, path=None, **kw: fake))
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--out", str(tmp_path / "o")]) == 1
    assert "FAIL no page errors: exception: TypeError: draw" in capsys.readouterr().out


def test_look_explains_a_missing_browser_and_a_bad_steps_file(tmp_path, capsys, monkeypatch):
    cat, home = setup(tmp_path)

    def no_browser(cls, path=None, **kw):
        raise BrowserError("no Chromium-based browser found; install Google Chrome or set VESPER_BROWSER")
    monkeypatch.setattr(browser_mod.Browser, "launch", classmethod(no_browser))
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--out", str(tmp_path / "o")]) == 1
    assert "FAILED: no Chromium-based browser found" in capsys.readouterr().err
    bad = tmp_path / "bad.yaml"
    bad.write_text("- {fly: 1}\n")
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--steps", str(bad)]) == 1
    assert "FAILED: step 1: unknown action 'fly'" in capsys.readouterr().err


def test_look_needs_a_reference_directory_to_update(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "look", str(home), "--catalog", str(cat), "--update-golden"]) == 1
    assert "--update-golden needs --golden" in capsys.readouterr().err
```

Create `tests/lab/test_look_browser.py`:
```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/lab/test_cli_play.py -q 2>&1 | tail -1`

Expected: `6 failed`: `argparse.ArgumentError: argument action: invalid choice: 'look'` (4) and `vesper.cli` has no attribute `_wait_for_interrupt` (2)

- [ ] **Step 3: Implement**

In `vesper/cli.py`, replace:
```python
            return _lab_run(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
```
with:
```python
            return _lab_run(args, load_lab_home(Path(args.home), catalog))
        if args.action == "play":
            return _lab_play(args, load_lab_home(Path(args.home), catalog))
        if args.action == "look":
            return _lab_look(args, load_lab_home(Path(args.home), catalog))
        return _lab_replay(args, catalog, load_lab_home)
```

In `vesper/cli.py`, replace:
```python
          f"trace {trace}")
    return 0
```
with:
```python
          f"trace {trace}")
    return 0


def _lab_play(args, home) -> int:
    import webbrowser

    from vesper.lab.play import PlayError, PlayServer
    from vesper.lab.session import Session
    from vesper.twins.clock import WallClock
    traces = home.path.parent / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    trace = Path(args.trace) if args.trace else _new_trace(traces, "play")
    session = Session(home, clock=WallClock(), trace=trace)
    session.start()
    server = PlayServer(session, port=args.port)
    try:
        try:
            server.start()
        except PlayError as e:
            print(f"FAILED: {e}; is another `vesper lab play` running? Choose another --port", file=sys.stderr)
            return 1
        print(f"VESPER Lab: {home.scene}, {len(home.devices)} devices, on the wall clock. Open {server.url} "
              "(Ctrl-C stops)")
        if not args.no_browser:
            webbrowser.open(server.url)
        _wait_for_interrupt()
    finally:
        server.stop()
        session.stop()
    print(f"trace {trace}")
    return 0


def _wait_for_interrupt() -> None:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _lab_look(args, home) -> int:
    from vesper.lab.browser import Browser, BrowserError
    from vesper.lab.play import PlayServer
    from vesper.lab.session import Session
    from vesper.lab.visual import DEFAULT_STEPS, LookError, load_steps, run_look
    from vesper.twins.clock import WallClock
    if args.update_golden and not args.golden:
        print("FAILED: --update-golden needs --golden DIR, the directory of reference shots", file=sys.stderr)
        return 1
    try:
        steps = load_steps(Path(args.steps)) if args.steps else DEFAULT_STEPS
    except LookError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else home.path.parent / "looks" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    session = Session(home, clock=WallClock(), trace=_new_trace(out, "look"))
    session.start()
    server = PlayServer(session, port=0)
    try:
        server.start()
        try:
            browser = Browser.launch(Path(args.browser) if args.browser else None, width=args.width,
                                     height=args.height)
        except BrowserError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        try:
            report = run_look(browser, server.url, steps, out, golden=Path(args.golden) if args.golden else None,
                              update_golden=args.update_golden)
        finally:
            browser.close()
    finally:
        server.stop()
        session.stop()
    for c in report.checks:
        print(f"{'PASS' if c.ok else 'FAIL'} {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"{'PASS' if report.ok else 'FAIL'}: {len(report.shots)} shots and the report in {out}")
    return 0 if report.ok else 1
```

In `vesper/cli.py`, replace:
```python
    lab_run.add_argument("--trace")
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
```
with:
```python
    lab_run.add_argument("--trace")
    lab_play = lab_actions.add_parser("play", help="play a home in the browser: the map, the hub and the console")
    lab_play.add_argument("home")
    lab_play.add_argument("--catalog", default="catalog/vds")
    lab_play.add_argument("--port", type=int, default=18080, help="the page's port on 127.0.0.1 (default 18080)")
    lab_play.add_argument("--trace", help="the trace file (default: traces/<time>.jsonl next to the home)")
    lab_play.add_argument("--no-browser", action="store_true", help="print the page's address, do not open it")
    lab_look = lab_actions.add_parser("look", help="drive the play page in a headless browser and check what it shows")
    lab_look.add_argument("home")
    lab_look.add_argument("--catalog", default="catalog/vds")
    lab_look.add_argument("--steps", help="a YAML list of steps (default: one shot of the start)")
    lab_look.add_argument("--out", help="where the shots and the report go (default: looks/<time> next to the home)")
    lab_look.add_argument("--golden", help="a directory of reference shots to compare with")
    lab_look.add_argument("--update-golden", action="store_true", help="save this look's shots as the references")
    lab_look.add_argument("--browser", help="a Chromium-based browser (default: found, or VESPER_BROWSER)")
    lab_look.add_argument("--width", type=int, default=1280)
    lab_look.add_argument("--height", type=int, default=800)
    lab_replay = lab_actions.add_parser("replay", help="replay a trace's inputs and compare the device reports")
```

- [ ] **Step 4: Run them, then the whole suite**

Run: `.venv/bin/python -m pytest tests/lab -q 2>&1 | tail -1`

Expected: `144 passed, 1 skipped` (the real-browser test is opt-in)

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -1`

Expected: `622 passed, 13 skipped` in the repository's working copy (an export without the local research data reports `621 passed, 14 skipped`)

- [ ] **Step 5: Look with a real browser, and look at the shots**

Run: `VESPER_UI_BROWSER=1 taskpolicy -b .venv/bin/python -m pytest tests/lab/test_look_browser.py -q --basetemp=.superpowers/look 2>&1 | tail -1`

Expected: `1 passed` (about a minute; the browser's first start alone can take 10-20 s)

Then open the three shots in `.superpowers/look/test_a_real_browser_sees_the_l0/look/` (`start.png`, `lamp_on.png`, `in_hall.png`) and confirm by eye: the lamp glows only inside the bedroom, the room labels are clear of the icons, the door sensor and the lock on the front door are side by side and both labelled, the hall's motion sensor is ringed once the player is in the hall, and the console lists the lamp's report, the interaction, the presence and the room change.

- [ ] **Step 6: Lint and commit**

Run: `.venv/bin/ruff check vesper/lab vesper/catalog vesper/twins vesper/exposure vesper/validation vesper/cli.py tests/lab tests/catalog tests/twins tests/exposure tests/validation`

Expected: `All checks passed!`

```bash
cd /Users/huanbui/Research/vesper
git add vesper/cli.py tests/lab/test_cli_play.py tests/lab/test_look_browser.py
git diff --cached --name-only   # expect exactly the paths above
git commit -m "feat(lab): vesper lab play and vesper lab look

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```


---

## After this plan

- **W2c:** the hub in the loop: VESPER's broker, the Zigbee bridge, Home Assistant (its dashboard in the hub panel, its automations acting on the twins) and the Matter tier, on the wall clock; hub commands recorded in the trace so that a session still replays; then the placement editor (spec §2 edit mode).
- **W3:** residents. **W4:** the security lab. **W1:** the Habitat world service and the 3D view (after another project's run).
