"""VESPER Studio's lobby (Lab spec §2a and §7, plans W5 and W6): the first page, in the browser or in the desktop app.

It lists the homes in the user's folders (play one, or compose a new one with the compose page), shows `vesper
doctor`'s checks, and runs the setup commands in the background with their output. It is the compose server with
these pages and endpoints added, on 127.0.0.1 only, with the same checks against other sites' pages.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

from vesper.lab.compose import ComposeError, ComposeServer

SETUPS = ("ha", "matter", "habitat")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def list_homes(roots: list[Path], catalog: dict) -> list[dict]:
    from vesper.lab.home import HomeError, load_lab_home
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("home.yaml")):
            rel = path.parent.relative_to(root)
            if any(part.startswith(".") or part in ("previous", "runs", "traces") for part in rel.parts):
                continue
            row = {"name": f"{root.name}/{rel}" if str(rel) != "." else root.name, "path": str(path)}
            try:
                h = load_lab_home(path, catalog)
                row.update(scene=h.scene, rooms=sum(1 for r in h.map.rooms if not r.outdoor), devices=len(h.devices),
                           residents=len(h.residents), model=(path.parent / "scene.glb").is_file())
            except (HomeError, OSError, ValueError) as e:
                row["error"] = str(e)[:300]
            out.append(row)
    return out


class StudioServer(ComposeServer):
    """The lobby at /, the compose page at /compose; `roots` are the folders of homes, the first one where new homes
    go."""

    def __init__(self, catalog: dict, catalog_dir: Path, results_dir: Path, roots: list[Path], *, root: Path,
                 port: int = 18080, play_port: int = 18082, launch=None, wait_for_play=None, run_setup=None,
                 doctor=None):
        self.roots = [Path(r).expanduser() for r in roots]
        self.roots[0].mkdir(parents=True, exist_ok=True)
        super().__init__(catalog, catalog_dir, results_dir, self.roots[0] / "untitled", port=port,
                         play_port=play_port, launch=launch, wait_for_play=wait_for_play)
        self.stack_root = Path(root)
        self._run_setup = run_setup or self._setup_child
        self._doctor = doctor
        self.setups: dict[str, dict] = {}

    # -- the lobby's data -----------------------------------------------------------------------------------
    def homes(self) -> list[dict]:
        return list_homes(self.roots, self.catalog)

    def doctor(self) -> list[dict]:
        from vesper.setup_tools import doctor
        checks = (self._doctor or (lambda: doctor(self.stack_root, self.catalog_dir)))()
        return [c.__dict__ for c in checks]

    def home_file(self, name: str) -> Path:
        """A home the lobby listed, by its name (nothing else is reachable)."""
        for row in self.homes():
            if row["name"] == name and "error" not in row:
                return Path(row["path"])
        raise ComposeError(f"no home {name!r} in {', '.join(str(r) for r in self.roots)}")

    def new_home(self, name: str) -> dict:
        """Where the compose page writes the next home: a new folder under the first root."""
        if not NAME.match(name or ""):
            raise ComposeError("a home's name: lower-case letters, digits and -, e.g. my-flat")
        out = self.roots[0] / name
        if (out / "home.yaml").exists():
            raise ComposeError(f"{out} already holds a home: open it from the list, or choose another name")
        with self.lock:
            self.out = out
        return {"out": str(out)}

    # -- setup in the background ---------------------------------------------------------------------------
    def setup(self, what: str) -> dict:
        if what not in SETUPS:
            raise ComposeError(f"setup {what!r}: one of {', '.join(SETUPS)}")
        with self.lock:
            job = self.setups.get(what)
            if job and job["state"] == "running":
                return {k: v for k, v in job.items() if k != "child"}
            log = self.stack_root / f"setup-{what}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            job = self.setups[what] = {"what": what, "state": "running", "log": str(log), "exit": None}
            job["child"] = self._run_setup(what, log)
            threading.Thread(target=self._watch, args=(job,), daemon=True).start()
            return {k: v for k, v in job.items() if k != "child"}

    def setup_status(self) -> list[dict]:
        out = []
        for job in self.setups.values():
            tail = ""
            try:
                tail = Path(job["log"]).read_text(errors="replace")[-2000:]
            except OSError:
                pass
            out.append({**{k: v for k, v in job.items() if k != "child"}, "tail": tail})
        return out

    def _watch(self, job: dict) -> None:
        code = job["child"].wait()
        job.update(state="done" if code == 0 else "failed", exit=code)

    def _setup_child(self, what: str, log: Path):
        args = ["setup", what] + (["--root", str(self.stack_root)] if what in ("ha", "matter") else [])
        code = "import sys; from vesper.cli import main; sys.exit(main(sys.argv[1:]))"
        return subprocess.Popen([sys.executable, "-c", code, *args], stdout=open(log, "w"),  # noqa: SIM115
                                stderr=subprocess.STDOUT)

    def stop(self) -> None:
        for job in self.setups.values():
            if job["child"].poll() is None:
                job["child"].terminate()
        super().stop()

    # -- routes -----------------------------------------------------------------------------------------------
    def _handler(self):
        base = super()._handler()
        server = self

        class Handler(base):
            def do_GET(self):  # noqa: N802 - http.server's name
                if not self._loopback():
                    return self._json(403, {"error": "this page is served to 127.0.0.1 only"})
                path = self.path.split("?", 1)[0]
                if path in ("/", "/studio.js"):
                    f = server.ui_dir / ("studio.html" if path == "/" else "studio.js")
                    return self._send(200, f.read_bytes(), server.TYPES[f.suffix])
                if path == "/compose":
                    f = server.ui_dir / "compose.html"
                    return self._send(200, f.read_bytes(), server.TYPES[".html"])
                if path == "/api/homes":
                    return self._json(200, {"homes": server.homes(), "roots": [str(r) for r in server.roots]})
                if path == "/api/doctor":
                    return self._json(200, {"checks": server.doctor()})
                if path == "/api/setup":
                    return self._json(200, {"jobs": server.setup_status()})
                return super().do_GET()

            def do_POST(self):  # noqa: N802
                if self.path not in ("/api/open", "/api/new", "/api/setup"):
                    return super().do_POST()
                if not self._loopback():
                    return self._json(403, {"error": "another site's page"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if not 0 <= n <= 100_000:
                        raise ValueError(n)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError(body)
                except ValueError:
                    return self._json(400, {"error": "not a JSON object (or no length, or too long)"})
                try:
                    if self.path == "/api/open":
                        return self._json(200, server.play(server.home_file(str(body.get("home", "")))))
                    if self.path == "/api/new":
                        return self._json(200, server.new_home(str(body.get("name", ""))))
                    return self._json(200, server.setup(str(body.get("what", ""))))
                except ComposeError as e:
                    return self._json(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001 - said to the page
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return Handler
