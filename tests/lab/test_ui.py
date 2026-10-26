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
    assert '<link rel="icon" href="data:,">' in page  # else Chrome fetches /favicon.ico
    for name in local:
        assert name.startswith("data:") or (UI_DIR / name).is_file(), name  # data: is inline, not fetched
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
