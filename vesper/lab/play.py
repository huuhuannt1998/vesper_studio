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
import collections
import concurrent.futures
import http
import json
import math
import sys
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from vesper.lab.mapworld import WorldError
from vesper.lab.rules import INTERACT_M
from vesper.lab.session import PLAYER

UI_DIR = Path(__file__).parent / "ui"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json"}
QUEUE_MAX = 5000  # messages waiting for one page; a page that falls this far behind is disconnected
RECENT = 200  # trace records a page gets when it connects: the console of a page that joins late


class PlayError(RuntimeError):
    """The play server could not start."""


def hello(session, hub_url: str | None = None, recent=()) -> dict:
    home, world, m = session.home, session.world, session.home.map
    return {
        "type": "hello", "scene": home.scene, "player": PLAYER, "hub": hub_url, "reach_m": INTERACT_M,
        "clock": {"t": session.clock.now(), "start_time": home.start_time},
        "map": {"grid": m.grid.to_json(),
                "rooms": [{"id": r.id, "label": r.label, "polygon": [list(p) for p in r.polygon],
                           "outdoor": r.outdoor} for r in m.rooms],
                "doorways": [{"id": d.id, "rooms": list(d.rooms), "center": list(d.center), "width": d.width,
                              "outdoor": d.outdoor, "box": _box(m.grid, d.cells)} for d in m.doorways]},
        "devices": [_device(session, d) for d in home.devices],
        "doors": [_door(session, door) for door in world.doors],
        "actors": [_pose(world.pose(a)) for a in world.actors()],
        "residents": [_resident(session, r) for r in session.residents.values()],
        "reach": reach(session), "records": list(recent)}


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
            "effect": session.rules.effects.get(d.name), "state": dict(session.rules.twin(d.name).state),
            "operable": session.rules.operable(d.name)}


def _resident(session, r) -> dict:
    """Who, doing what, where: the page's residents list (spec §2a), kept current by `task` records and poses."""
    task, pose = r.task, session.world.pose(r.name)
    return {"name": r.name, "model": r.model, "task": task.name if task else None,
            "category": task.category if task else None, "room": pose.room}


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
        self._recent = collections.deque(session.records[-RECENT:], maxlen=RECENT)  # written on the clock thread
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: Exception | None = None
        self.errors: list[str] = []  # failures inside the session while a page used it (a look checks these)
        self._thread: threading.Thread | None = None
        session.world.on_event(self._on_world)
        session.rules.on_effect(lambda key, effect: self._emit({"type": "effect", "target": key, "effect": effect}))
        session.on_record(self._on_record)

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
        if path == "/favicon.ico" and not (self.ui_dir / "favicon.ico").is_file():
            return _response(204, "image/x-icon", b"")  # browsers ask for it; a 404 would be a page error
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
            try:
                snapshot = await self._on_clock(lambda: hello(self.session, self.hub_url, self._recent))
            except Exception as e:  # noqa: BLE001 - kept for the look, then the page is closed
                self._fail(f"hello: {type(e).__name__}: {e}")
                return
            await ws.send(json.dumps(snapshot))
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
        except (ValueError, RecursionError):
            return {"type": "error", "message": "an input that is not JSON (or is nested too deeply)"}
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
                if not self._on_map(x, z):
                    raise ValueError(f"walk_to: ({x:g}, {z:g}) is outside the map")
                path = await self._on_clock(lambda: s.walk_to((x, z)))
                return {"type": "walk", "ok": path is not None, "x": x, "z": z}
            if kind == "interact":
                target = msg.get("target")
                if target is not None and not isinstance(target, str):
                    raise ValueError(f"interact: target must be a device or door name, not {target!r}")
                return {"type": "result", **await self._on_clock(lambda: s.interact(target))}
        except (ValueError, WorldError) as e:
            return {"type": "error", "message": str(e)}
        except TimeoutError:
            self._fail(f"{kind}: the session did not answer within 10 s")
            return {"type": "error", "message": "the session did not answer within 10 s"}
        except Exception as e:  # noqa: BLE001 - a bug in the rules or the twins: the page is told, a look sees it
            self._fail(f"{kind}: {type(e).__name__}: {e}")
            return {"type": "error", "message": f"the session failed: {type(e).__name__}: {e}"}
        return {"type": "error", "message": f"unknown input {kind!r}; the inputs are move, walk_to and interact"}

    def _on_map(self, x: float, z: float) -> bool:
        g = self.session.home.map.grid
        return (g.origin[0] <= x <= g.origin[0] + g.width * g.cell and
                g.origin[1] <= z <= g.origin[1] + g.height * g.cell)

    def _fail(self, text: str) -> None:
        self.errors.append(text)
        print(f"vesper lab: {text}", file=sys.stderr)

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

    def _on_record(self, rec: dict) -> None:
        self._recent.append(rec)
        self._emit({"type": "record", "record": rec})

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
