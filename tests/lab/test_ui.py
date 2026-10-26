"""The browser page: its JavaScript tests (under node), and that it stands alone."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vesper.lab.play import UI_DIR

TESTS = Path(__file__).parent / "ui"


def page_hello(tmp_path) -> dict:
    """A real hello for the page tests: the player in the bedroom by the lamp, a contact sensor and a lock on the
    front door, and a resident, alex, reading in the bedroom."""
    from tests.lab.homes import catalog, write_home
    from tests.lab.test_play import IN_BEDROOM
    from vesper.lab.home import load_lab_home
    from vesper.lab.play import hello
    from vesper.lab.resident import Resident
    from vesper.lab.routine import Routine, Task
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    home = load_lab_home(write_home(tmp_path, IN_BEDROOM.replace('start_time: "07:30"', 'start_time: "07:00"')),
                         catalog())
    s = Session(home, clock=LogicalClock())
    s.start()
    Resident(s, "alex", "male_0", Routine("alex", (Task(7 * 3600, 3600, "read", "LEISURE", "bedroom"),))).start()
    s.clock.advance(0)
    return hello(s)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_s_javascript_passes_its_tests(tmp_path):
    files = sorted(str(p) for p in TESTS.glob("*.test.mjs"))
    (tmp_path / "hello.json").write_text(json.dumps(page_hello(tmp_path)))
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "VESPER_HELLO": str(tmp_path / "hello.json")})
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"^[#ℹ] skipped 0$", r.stdout, re.M), r.stdout[-1000:]  # the page tests ran, with the hello


def test_the_page_loads_only_its_own_files():
    page = (UI_DIR / "index.html").read_text()
    local = re.findall(r'(?:src|href)="([^"]+)"', page)
    assert {"style.css", "app.js"} <= set(local)
    assert '<link rel="icon" href="data:,">' in page  # else Chrome fetches /favicon.ico
    for name in local:
        assert name.startswith("data:") or (UI_DIR / name).is_file(), name  # data: is inline, not fetched
    for f in UI_DIR.iterdir():  # the page's own files; vendor/ is checked against its release below
        if not f.is_file():
            continue
        text = f.read_text()
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", text), f"{f.name} loads from the internet"
        for imported in re.findall(r'from "\./([^"]+)"', text):
            assert (UI_DIR / imported).is_file(), f"{f.name} imports a missing {imported}"


def test_the_vendored_three_js_is_the_pinned_release_unchanged():
    vendor = UI_DIR / "vendor" / "three"
    pinned = dict(reversed(line.split("  ", 1)) for line in re.findall(r"^[0-9a-f]{64}  \S+$",
                                                                        (vendor / "NOTICE.md").read_text(), re.M))
    present = {p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file()} - {"NOTICE.md"}
    assert present == set(pinned)
    for name, digest in pinned.items():
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == digest, name


def test_the_page_knows_every_catalog_category():
    from vesper.catalog.normalize.common import CATEGORIES
    text = (UI_DIR / "mapview.js").read_text()
    glyphs = re.search(r"export const GLYPHS = \{(.*?)\};", text, re.S).group(1)
    assert set(re.findall(r"(\w+):", glyphs)) == set(CATEGORIES)


def test_every_element_the_script_looks_up_is_on_the_page():
    page, app = (UI_DIR / "index.html").read_text(), (UI_DIR / "app.js").read_text()
    for name in set(re.findall(r'\$\("([\w-]+)"\)', app)):
        assert f'id="{name}"' in page, f"app.js looks up #{name}, which index.html does not have"
    for name in ("feed", "residents", "where"):  # the event feed, the residents list, the compass (spec §2a)
        assert f'id="{name}"' in page, name


def test_the_page_walks_by_keys_only_and_a_click_uses_what_is_under_it():
    """No click-to-walk in play (spec §2): only the looks' scripted walk sends walk_to."""
    app = (UI_DIR / "app.js").read_text()
    sends = [line.strip() for line in app.splitlines() if '"walk_to"' in line]
    assert len(sends) == 1 and sends[0].startswith("walkTo:"), sends
    assert 'send({ type: "interact", target })' in app
