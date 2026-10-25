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
    ('{"type": "walk_to", "x": 1e308, "z": -1e308}', "outside the map"),
    ("[" * 30000 + "]" * 30000, "not JSON"),
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


def test_the_browser_s_icon_request_is_answered_without_an_error(play):
    assert get(f"http://127.0.0.1:{play.port}/favicon.ico")[0] == 204  # Chrome asks for it; a 404 is a page error


def test_a_failure_inside_the_session_is_answered_and_kept_and_the_page_stays(play):
    def broken(target=None, actor="player"):
        raise RuntimeError("boom in a rule")
    play.session.interact = broken
    with client(play) as ws:
        until(ws, lambda m: m["type"] == "hello")
        ws.send(json.dumps({"type": "interact"}))
        assert "boom in a rule" in until(ws, lambda m: m["type"] == "error")["message"]
        ws.send(json.dumps({"type": "move", "dx": 0.2, "dz": 0}))
        until(ws, lambda m: m["type"] == "pose")
    assert any("boom in a rule" in e for e in play.errors)


def test_a_walk_off_the_map_is_refused_before_it_reaches_the_trace(play):
    with client(play) as ws:
        until(ws, lambda m: m["type"] == "hello")
        ws.send(json.dumps({"type": "walk_to", "x": 1e308, "z": -1e308}))
        until(ws, lambda m: m["type"] == "error")
    assert not any(r["kind"] == "input" and r["op"] == "walk_to" for r in play.session.records)


def test_a_page_that_joins_late_gets_the_recent_console(play):
    play.session.note({"resident": "alex", "decision": "forgot the light", "device": "bedroom_lamp"})
    with client(play) as ws:
        hello = until(ws, lambda m: m["type"] == "hello")
    kinds = [r["kind"] for r in hello["records"]]
    assert kinds[0] == "meta" and kinds[-1] == "resident" and len(hello["records"]) <= 200
