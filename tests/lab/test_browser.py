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
