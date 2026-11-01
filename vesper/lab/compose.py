"""Composing a home in the browser (plan W10): `vesper lab compose OUT`.

The page sets the rooms and picks real catalog products, reading each one's evidence, firmware and configuration;
the server turns its rows into a `vesper-request/1` file and generates the home (plan W9), then plays it. A level
shows only with its evidence: L0 and L1 come from the catalog, L2 from the latest acceptance runs (what
`vesper report` credits), L4 from a firmware anchor (an `anchors-*.json` results file). Loopback only.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from vesper.catalog_search import LEVELS
from vesper.validation.report import build_report, twin_model

PAGE = 50


class ComposeError(ValueError):
    """A request the page built cannot become a home; the message says why."""


def evidence(catalog_dir: Path, results_dir: Path) -> dict:
    """{"L2": {product ids credited by acceptance}, "L4": {twin model: its firmware anchor}}: what `vesper report`
    credits, read through it so the page and the report never disagree."""
    l2: set[str] = set()
    anchors: dict[str, dict] = {}
    try:
        report = build_report(Path(catalog_dir), Path(results_dir))
        credit = report["l2"]
        for ids in (credit.get("validated_products"), (credit.get("matter") or {}).get("validated_products"),
                    (credit.get("tasmota") or {}).get("validated_products")):
            l2 |= {i for i in ids or () if isinstance(i, str)}
        anchors = {m: {k: str(a.get(k, "?")) for k in
                       ("product", "firmware", "soc", "interface", "transitions_agree", "values_agree")}
                   for m, a in (report.get("l4") or {}).items() if isinstance(a, dict)}
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        pass  # no results yet, or none readable: nothing credited beyond the catalog
    return {"L2": l2, "L4": anchors}


def levels(record: dict, ev: dict) -> dict[str, bool]:
    reached = dict((record.get("fidelity") or {}).get("reached") or {})
    out = {name: bool(reached.get(name)) for name in LEVELS}
    out["L2"] = record["id"] in ev["L2"]
    out["L4"] = twin_model(record) in ev["L4"]
    return out


def _rank(lv: dict) -> int:
    return sum(lv.values())


def network_of(record: dict) -> tuple[str, bool]:
    """How the product joins a VESPER home, and whether VESPER simulates it."""
    binding = record.get("binding") or {}
    if "zigbee" in binding:
        return "Zigbee mesh to the coordinator; on Home Assistant through the Zigbee2MQTT-compatible bridge", True
    if "tasmota" in binding:
        return "Wi-Fi to the router, its own MQTT connection (Tasmota); on Home Assistant by Tasmota discovery", True
    if "matter" in binding:
        return ("Matter over Wi-Fi: VESPER's twin; on Home Assistant with --matter-interface (VESPER's Matter "
                "Server)"), True
    if "zwave" in binding:
        return "Z-Wave: identity only, not simulated", False
    return "no protocol VESPER simulates", False


def _row(record: dict, ev: dict) -> dict:
    i = record.get("identity") or {}
    network, selectable = network_of(record)
    return {"id": record["id"], "vendor": i.get("vendor"), "model": i.get("model"), "category": i.get("category"),
            "protocol": next(iter(record.get("binding") or {}), None), "levels": levels(record, ev),
            "anchor": record["id"] in ev["L4"], "network": network, "selectable": selectable}


def search_products(catalog: dict, ev: dict, q: str = "", *, category: str | None = None,
                    protocol: str | None = None, limit: int = PAGE, offset: int = 0) -> list[dict]:
    """Every word in the id, vendor, model, description or category; best evidenced first, then by id."""
    words = q.lower().split()
    found = []
    for r in catalog.values():
        i = r.get("identity") or {}
        if category and i.get("category") != category:
            continue
        if protocol and protocol not in (r.get("binding") or {}):
            continue
        text = " ".join(str(x) for x in (r["id"], i.get("vendor"), i.get("model"), i.get("description"),
                                         i.get("category")) if x).lower()
        if all(w in text for w in words):
            found.append(_row(r, ev))
    found.sort(key=lambda row: (not row["selectable"], -_rank(row["levels"]), row["id"]))
    return found[offset:offset + limit]


def product_card(record: dict, ev: dict) -> dict:
    """The product as the page shows it before it is chosen: identity, network, configuration (its features,
    settable or read-only), levels with their reasons and sources, and its firmware."""
    row = _row(record, ev)
    fidelity = record.get("fidelity") or {}
    config = []
    for f in record.get("features") or []:
        if f.get("type") == "unsupported":
            continue
        config.append({"name": f["name"], "type": f["type"], "settable": "set" in (f.get("access") or []),
                       "range": f.get("range"), "values": f.get("values"), "unit": f.get("unit"),
                       "category": f.get("category")})
    model = twin_model(record)
    anchor = ev["L4"].get(model)
    sources = record.get("sources") or []
    text = (f"firmware anchor for its twin model ({model.split(':', 1)[1]}): {anchor['firmware']} on "
            f"{anchor['soc']}, compared with a {anchor['product']} twin ({anchor['transitions_agree']} transitions "
            "agree); reference firmware of the device type, not this product's") if anchor else (
        "no firmware anchor: a protocol-level twin built from "
        + (", ".join(str(s.get("name") or s.get("source") or s) if isinstance(s, dict) else str(s)
                     for s in sources[:3]) or "the catalog"))
    return {**row, "description": (record.get("identity") or {}).get("description"),
            "reasons": fidelity.get("reasons") or {}, "assumed": fidelity.get("assumed") or [], "sources": sources,
            "config": config, "firmware": {"anchor": anchor, "text": text}}


def compose_home(request: dict, catalog: dict, out: Path) -> dict:
    """Write the page's request beside the home and generate it (plan W9). ComposeError says what is wrong."""
    import shutil
    from datetime import UTC, datetime

    from vesper.lab.generate import GenerateError, generate_home, load_request, summary_row
    from vesper.lab.home import HomeError, load_lab_home
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    scratch = out / f".compose-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
    try:
        scratch.mkdir()
        (scratch / "request.yaml").write_text(yaml.safe_dump(request, sort_keys=False))
        try:
            generate_home(load_request(scratch / "request.yaml"), catalog, scratch / "home")
            load_lab_home(scratch / "home" / "home.yaml", catalog)
        except (GenerateError, HomeError) as e:
            raise ComposeError(str(e)) from e
        old = [out / n for n in HOME_FILES if (out / n).exists()]
        if old:  # the old home, and a model dressed for its layout, set aside (traces stay)
            aside = out / "previous" / f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
            aside.mkdir(parents=True)
            for f in old:
                shutil.move(str(f), aside / f.name)
        shutil.move(str(scratch / "request.yaml"), out / "request.yaml")
        for f in (scratch / "home").iterdir():
            shutil.move(str(f), out / f.name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    home = load_lab_home(out / "home.yaml", catalog)
    return {"home": str(out / "home.yaml"), "summary": summary_row(out.name, home)}


HOME_FILES = ("home.yaml", "map.json", "request.yaml", "scene.glb", "map.original.json", "dress")


def _launch_play(home: str, port: int, catalog_dir: Path):
    """`vesper lab play HOME` as a child process on `port` (its page opened by the compose page)."""
    import subprocess
    import sys
    code = "import sys; from vesper.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.Popen([sys.executable, "-c", code, "lab", "play", home, "--catalog", str(catalog_dir),
                             "--port", str(port), "--no-browser"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _listening(port: int) -> bool:
    import socket
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, timeout: float = 30.0, alive=lambda: True) -> bool:
    """The child listens on `port` within `timeout`; False as soon as it has died."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end and alive():
        if _listening(port):
            return True
        time.sleep(0.2)
    return False


class ComposeServer:
    """The compose page and its JSON API on 127.0.0.1 (plan W10). A POST from another site's page is refused."""

    TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8"}

    def __init__(self, catalog: dict, catalog_dir: Path, results_dir: Path, out: Path, *, port: int = 18081,
                 ui_dir: Path | None = None, launch=None, play_port: int = 18082, wait_for_play=None):
        from vesper.lab.play import UI_DIR
        self.catalog, self.catalog_dir, self.out = catalog, Path(catalog_dir), Path(out)
        self.ui_dir = Path(ui_dir or UI_DIR)
        self.ev = evidence(catalog_dir, results_dir)
        self._launch = launch or (lambda home, p: _launch_play(home, p, self.catalog_dir))
        self._wait = wait_for_play or _wait_for_port
        self.play_port, self._want_port = play_port, port
        self._play = None
        self._httpd = None
        self.port: int | None = None
        import threading
        self.lock = threading.Lock()  # one generate or play at a time: they replace the same home and child

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def meta(self) -> dict:
        from vesper.lab.generate import AREAS, PROTOCOLS, RULES
        cats: dict[str, int] = {}
        for r in self.catalog.values():
            c = (r.get("identity") or {}).get("category")
            if c and network_of(r)[1]:
                cats[c] = cats.get(c, 0) + 1
        return {"room_types": sorted(AREAS), "categories": dict(sorted(cats.items())),
                "placed_by_rule": sorted(RULES), "protocols": [p for p in PROTOCOLS if p != "zwave"],
                "out": str(self.out)}

    def play(self, home: Path | None = None) -> dict:
        """Play the composed home (or `home`, a home file the caller has checked) in a child `vesper lab play`."""
        with self.lock:
            home = Path(home) if home is not None else self.out / "home.yaml"
            if not home.is_file():
                raise ComposeError("generate the home first")
            self.stop_play()
            if _listening(self.play_port):
                raise ComposeError(f"port {self.play_port} is in use (another `vesper lab play`?); stop it or start "
                                   "compose with another --play-port")
            child = self._play = self._launch(str(home), self.play_port)
            if not self._wait(self.play_port, alive=lambda: child.poll() is None):
                self.stop_play()
                raise ComposeError(f"vesper lab play stopped before it opened port {self.play_port}; run "
                                   f"`vesper lab play {home}` to see why")
            return {"url": f"http://127.0.0.1:{self.play_port}/"}

    def generate(self, request) -> dict:
        with self.lock:
            if not isinstance(request, dict):
                raise ComposeError("a request is a JSON object")
            try:
                made = compose_home(request, self.catalog, self.out)
            except OSError as e:
                raise ComposeError(f"the home could not be written: {e}") from e
            self.stop_play()  # the home it played is replaced (a failed request leaves it playing)
            return made

    def stop_play(self) -> None:
        child, self._play = self._play, None
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except Exception:  # noqa: BLE001 - killed, then reaped
                child.kill()
                child.wait(timeout=10)

    def start(self) -> None:
        import threading
        from http.server import ThreadingHTTPServer
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self._want_port), self._handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, name="vesper-compose", daemon=True).start()

    def stop(self) -> None:
        self.stop_play()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _handler(self):
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import parse_qs, urlparse
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, data) -> None:
                self._send(status, json.dumps(data).encode())

            def _loopback(self) -> bool:
                """A page served by this server, by any of loopback's names (not a DNS-rebound one)."""
                ok = {f"{h}:{server.port}" for h in ("127.0.0.1", "localhost", "[::1]")}
                if (self.headers.get("Host") or "") not in ok:
                    return False
                origin = self.headers.get("Origin")
                return origin is None or origin.rstrip("/").split("://", 1)[-1] in ok

            def do_GET(self):  # noqa: N802 - http.server's name
                if not self._loopback():
                    return self._json(403, {"error": "this page is served to 127.0.0.1 only"})
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path in ("/", "/compose.html", "/compose.js", "/mapview.js", "/style.css"):
                    f = server.ui_dir / ("compose.html" if u.path == "/" else u.path.lstrip("/"))
                    if f.is_file():
                        return self._send(200, f.read_bytes(), server.TYPES.get(f.suffix, "application/octet-stream"))
                    return self._json(404, {"error": "not found"})
                if u.path == "/api/meta":
                    return self._json(200, server.meta())
                if u.path == "/api/search":
                    try:
                        offset = max(int(q.get("offset", 0)), 0)
                    except ValueError:
                        offset = 0
                    found = search_products(server.catalog, server.ev, q.get("q", ""),
                                            category=q.get("category") or None, protocol=q.get("protocol") or None,
                                            offset=offset)
                    return self._json(200, {"products": found, "offset": offset})
                if u.path == "/api/product":
                    r = server.catalog.get(q.get("id", ""))
                    if r is None:
                        return self._json(404, {"error": "no such product"})
                    return self._json(200, product_card(r, server.ev))
                return self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                if not self._loopback():
                    return self._json(403, {"error": "another site's page"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if not 0 <= n <= 1_000_000:
                        raise ValueError(n)
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._json(400, {"error": "not JSON (or no length, or over 1 MB)"})
                try:
                    if self.path == "/api/generate":
                        return self._json(200, server.generate(body))
                    if self.path == "/api/play":
                        return self._json(200, server.play())
                except ComposeError as e:
                    return self._json(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001 - said to the page, never a dropped connection
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return self._json(404, {"error": "not found"})
        return Handler
