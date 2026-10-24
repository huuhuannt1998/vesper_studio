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
