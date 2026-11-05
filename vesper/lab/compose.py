"""Composing a home in the browser (plan W10): `vesper lab compose OUT`.

The page sets the rooms and picks real catalog products, reading each one's evidence, firmware and configuration;
the server turns its rows into a `vesper-request/1` file and generates the home (plan W9), then plays it. A level
shows only with its evidence: L0 and L1 come from the catalog, L2 from the latest acceptance runs (what
`vesper report` credits), L4 from a firmware anchor (an `anchors-*.json` results file). Loopback only.

Import a plan (floor-plan import, task A4): POST /api/import takes a floor-plan image (Claude reads it in the
background: GET /api/import/<job> follows it) or a vesper-plan/1 file; POST /api/import/check checks the plan as
the user edited it with their scale; /api/generate with a `plan` builds it (`generate_from_plan`).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import yaml

from vesper.catalog_search import LEVELS
from vesper.lab.plan_read import TIMEOUT_S as READ_TIMEOUT_S
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


_AREA_TEXT = re.compile(r"^\s*(?:area\s*)?(\d+(?:\.\d+)?)\s*(?:m²|m2|sq\.?\s*m)?\s*$", re.I)


def scale_args(text: str | None) -> tuple[str | None, float | None]:
    """The page's scale field as `scale_plan`'s two arguments: "area 95" (or a bare number, m² optional) is the home's
    floor area; anything else a room's size ("kitchen width 3.2 m"); empty is neither (the plan's own scale)."""
    text = "" if text is None else str(text).strip()
    if not text:
        return None, None
    m = _AREA_TEXT.match(text)
    return (None, float(m.group(1))) if m else (text, None)


def _page_words(problem: str) -> str:
    """A PlanError worded for the command line, worded for the page's scale field."""
    if problem.startswith("no scale:"):
        return ("No scale: write a room's size in Scale (like 'kitchen width 3.2 m') or the home's floor area "
                "('area 95')")
    return (problem.replace("give --scale or --area, not both", "give a room's size or the area, not both")
            .replace("give --scale or --area", "write a room's size in Scale (like 'kitchen width 3.2 m') or the "
                     "home's floor area ('area 95')"))


def import_check(plan: dict, scale: str | None = None, numbers: list[int] | None = None,
                 floors_drawn: int | None = None) -> dict:
    """What the page shows for a plan as read (or as the user edited it) and the user's scale: {problems (every
    reason VESPER would not build it, empty when it would), warnings (what straightening left out or narrowed
    because the walls cannot carry it, and rooms of an unusual size for their type: built so, never blocking),
    summary (plan_read.summary: each room, door and the entrance), transform (plan_transform of the scaled plan,
    with m_per_px: a straightened point (x, z) goes back to the image by turning it -angle_deg about centre and
    dividing by m_per_px), rooms (the rooms as
    VESPER would build them, in metres; None when it cannot straighten the plan)}. `numbers` (the page's
    door_numbers) is each door's place in the plan as read, so that every message names a door as the reading does
    (vesper.lab.plan.door_name), whatever rooms the user left out; `floors_drawn` (the reading's count of floors
    drawn, sent back by the page) puts which storey was read in the summary."""
    from vesper.lab.geometry import polygon_area
    from vesper.lab.plan import PlanError, check_house, load_plan, plan_transform, rectify, scale_plan
    from vesper.lab.plan_read import summary
    out = {"problems": [], "warnings": [], "summary": None, "transform": None, "rooms": None}
    try:
        load_plan(plan, numbers)
    except PlanError as e:
        out["problems"] = [str(e)]
        return out
    floors = floors_drawn if isinstance(floors_drawn, int) and not isinstance(floors_drawn, bool) else None
    out["summary"] = summary(plan, numbers, floors)
    numbers = list(range(len(plan.get("doors") or []))) if numbers is None else list(numbers)
    try:
        room_size, area = scale_args(scale)
        scaled = scale_plan(plan, room_size, area)
        drawn = sum(polygon_area([tuple(p) for p in r["polygon"]]) for r in plan["rooms"])
        real = sum(polygon_area([tuple(p) for p in r["polygon"]]) for r in scaled["rooms"])
        out["transform"] = {**plan_transform(scaled), "m_per_px": math.sqrt(real / drawn) if drawn > 0 else 1.0}
        warnings: list[str] = []
        straight = rectify(scaled, warnings, numbers)
        out["warnings"] = warnings
        out["summary"] = summary(straight, numbers, floors)
        out["rooms"] = [{"id": r["id"], "polygon": r["polygon"]} for r in straight["rooms"]]
        out["problems"] = [_page_words(p) for p in check_house(straight, numbers, warnings)]
    except PlanError as e:
        out["problems"] = [_page_words(str(e))]
    return out


def _plan_home(request: dict) -> tuple[dict, dict, str, list[int]]:
    """A page request that carries a plan (as read, as edited) -> (the request without it, the plan straightened in
    metres, the plan's file name for the home's scene, each kept door's place in the plan as read). ComposeError lists
    every problem."""
    from vesper.lab.plan import PlanError, rectify, scale_plan
    spec = {k: v for k, v in request.items() if k not in ("plan", "scale", "source", "door_numbers")}
    if any(spec.get(k) is not None for k in ("rooms", "program", "scene")):
        raise ComposeError("an imported home's rooms come from the plan: its request names no rooms, program or scene")
    checked = import_check(request["plan"], request.get("scale"), request.get("door_numbers"))
    if checked["problems"]:
        raise ComposeError("the plan is not a house VESPER can build: " + "; ".join(checked["problems"]))
    room_size, area = scale_args(request.get("scale"))
    given = request.get("door_numbers")
    numbers = list(range(len(request["plan"].get("doors") or []))) if given is None else list(given)
    try:
        plan_m = rectify(scale_plan(request["plan"], room_size, area), None, numbers)
    except PlanError as e:  # checked above: only a plan that changed under us
        raise ComposeError(str(e)) from None
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(str(request.get("source") or "plan.json")).name).strip(".") or "plan"
    return spec, plan_m, name, numbers


def compose_home(request: dict, catalog: dict, out: Path) -> dict:
    """Write the page's request beside the home and generate it (plan W9). A request with a `plan` (a vesper-plan/1
    as read and edited, with its `scale` text and `source` file name) builds that plan (`generate_from_plan`); the
    request beside it keeps its devices, seed and site, and the home keeps the plan (plan.json). ComposeError says
    what is wrong."""
    import dataclasses
    import shutil
    from datetime import UTC, datetime

    from vesper.lab.generate import GenerateError, generate_from_plan, generate_home, load_request, summary_row
    from vesper.lab.home import HomeError, load_lab_home
    out = Path(out)
    plan_m = None
    if request.get("plan") is not None:
        request, plan_m, source, numbers = _plan_home(request)
    out.mkdir(parents=True, exist_ok=True)
    scratch = out / f".compose-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
    try:
        scratch.mkdir()
        (scratch / "request.yaml").write_text(yaml.safe_dump(request, sort_keys=False))
        try:
            req = load_request(scratch / "request.yaml")
            if plan_m is None:
                generate_home(req, catalog, scratch / "home")
            else:
                req = dataclasses.replace(req, program=[r["type"] for r in plan_m["rooms"]])
                generate_from_plan(plan_m, req, catalog, scratch / "home", source=source, numbers=numbers)
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


HOME_FILES = ("home.yaml", "map.json", "request.yaml", "scene.glb", "map.original.json", "dress", "render.json",
              "openings.json", "furniture.json", "windows.json", "plan.json", "scan.json", "source", ".gitignore")


def _file_name(name: str | None) -> str | None:
    """The file name the page sent with an import (URL-encoded): its last part, printable, at most 120 characters."""
    from urllib.parse import unquote
    if not name:
        return None
    text = "".join(c for c in Path(unquote(name).replace("\\", "/")).name if c.isprintable())[:120]
    return text or None


IMPORT_PATH = re.compile(r"^/api/import/([0-9a-f]{16})(/image)?$")  # a reading, by its job id; its image
STOP_PATH = re.compile(r"^/api/import/([0-9a-f]{16})/stop$")

MOSQUITTO_PATHS =("/opt/homebrew/sbin/mosquitto", "/usr/local/sbin/mosquitto", "/usr/sbin/mosquitto")


def hub_ready(root: Path, which=None, known=MOSQUITTO_PATHS) -> bool:
    """Home Assistant (`vesper setup ha`, in `root`) and a Mosquitto broker are here: a played home puts them in the
    loop, so its hub panel shows Home Assistant's dashboard with the home's devices."""
    import shutil
    which = which or shutil.which
    mosquitto = which("mosquitto") or next((p for p in known if Path(p).is_file()), None)
    return (Path(root) / "ha" / "venv" / "bin" / "hass").is_file() and bool(mosquitto)


def play_args(home: str, port: int, catalog_dir: Path, root: Path, *, hub: bool) -> list[str]:
    """`vesper lab play`'s arguments for the compose page's child; with `hub`, Home Assistant in the loop."""
    args = ["lab", "play", home, "--catalog", str(catalog_dir), "--port", str(port), "--no-browser"]
    return [*args, "--hub", "--root", str(root)] if hub else args


HUB_START_S = 240  # Home Assistant's first start (its database, its integrations) can take a few minutes


def _launch_play(home: str, port: int, catalog_dir: Path, root: Path | None = None, hub: bool = False):
    """`vesper lab play HOME` as a child process on `port` (its page opened by the compose page)."""
    import subprocess
    import sys
    import tempfile
    code = "import sys; from vesper.cli import main; sys.exit(main(sys.argv[1:]))"
    log = tempfile.NamedTemporaryFile(prefix="vesper-play-", suffix=".log", delete=False)  # noqa: SIM115
    child = subprocess.Popen([sys.executable, "-c", code,
                              *play_args(home, port, catalog_dir, Path(root or ".vesper").resolve(), hub=hub)],
                             stdout=log, stderr=subprocess.STDOUT)
    log.close()
    child.log = Path(log.name)  # what it printed, so a play that stops can say why
    return child


def _last_words(log) -> str | None:
    """The last line a stopped child printed, or None."""
    try:
        lines = [ln.strip() for ln in Path(log).read_text(errors="replace").splitlines() if ln.strip()]
    except (OSError, TypeError):
        return None
    return lines[-1][:400] if lines else None


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
                 ui_dir: Path | None = None, launch=None, play_port: int = 18082, wait_for_play=None,
                 stack_root: Path | None = None, reader=None):
        from vesper.lab.play import UI_DIR
        self.catalog, self.catalog_dir, self.out = catalog, Path(catalog_dir), Path(out)
        self.ui_dir = Path(ui_dir or UI_DIR)
        self.ev = evidence(catalog_dir, results_dir)
        self.stack_root = Path(stack_root or ".vesper").resolve()  # `vesper setup ha`'s folder
        self._furnisher = None  # the Furnisher class (vesper.lab.furnish), or a test's stand-in
        self._furnish = None  # the current (or last) furnishing run
        self._launch = launch or (lambda home, p: _launch_play(home, p, self.catalog_dir, self.stack_root,
                                                               hub=self.hub()))
        self._wait = wait_for_play or _wait_for_port
        self.play_port, self._want_port = play_port, port
        self._play = None
        self._httpd = None
        self.port: int | None = None
        import threading
        self.lock = threading.Lock()  # one generate or play at a time: they replace the same home and child
        self._reader = reader  # path -> a vesper-plan/1 in pixels (plan_read.read_plan_image), or a test's stand-in
        self._imports: dict[str, dict] = {}  # the latest readings, by job id, oldest first
        self._import_lock = threading.RLock()  # start_import holds it while it keeps the new job

    # -- importing a floor plan (floor-plan import, task A4) ------------------------------------------------
    IMPORTS_KEPT = 4  # the readings (and their images) the server remembers
    READING_LIMIT_S = 2 * READ_TIMEOUT_S  # the worst a reading takes: an answer and its one retry, each to the limit

    def _public(self, job: dict) -> dict:
        with self._import_lock:  # a reading's thread updates its job
            return {k: v for k, v in job.items() if k not in ("image", "stop")}

    def start_import(self, data: bytes, name: str | None = None) -> dict:
        """A floor-plan image (PNG, JPEG or PDF by its first bytes) read by Claude in the background, or a
        vesper-plan/1 file (JSON) checked at once. -> the job: {job, kind, name (the file's, as the page gave it),
        started (Unix time), state: reading | done | failed, ...}; a done one carries the plan as read (pixels) and
        import_check's problems, summary, transform and rooms; a failed one its error."""
        import secrets
        import threading
        import time

        from vesper.lab.plan import PlanError, load_plan
        from vesper.lab.plan_read import MAGIC, MAX_BYTES
        if len(data) > MAX_BYTES:
            raise ComposeError(f"the file is larger than {MAX_BYTES // 2**20} MB")
        kind = next((ext for magic, ext in MAGIC if data.startswith(magic)), None)
        head = {"job": secrets.token_hex(8), "name": _file_name(name), "started": time.time()}
        if kind is None:
            if not data.lstrip().startswith(b"{"):
                raise ComposeError("not a PNG, JPEG or PDF image, nor a vesper-plan/1 file (.json)")
            try:
                plan = load_plan(json.loads(data))
            except (ValueError, PlanError) as e:  # a JSONDecodeError is a ValueError
                raise ComposeError(f"not a vesper-plan/1 file: {e}") from None
            except RecursionError:  # json.loads (or the plan's reading) of a file of nested brackets
                raise ComposeError("the plan file is nested too deeply") from None
            return self._public(self._keep({**head, "kind": "json", "state": "done", "plan": plan,
                                            **import_check(plan)}))
        with self._import_lock:
            if any(j["state"] == "reading" for j in self._imports.values()):
                raise ComposeError("Claude is reading a plan already: wait for it, or stop it (each reading uses "
                                   "your quota)")
            job = self._keep({**head, "kind": kind, "state": "reading", "limit_s": self.READING_LIMIT_S,
                              "image": data, "stop": threading.Event()})
        threading.Thread(target=self._read, args=(job,), name="vesper-plan-read", daemon=True).start()
        return self._public(job)

    def stop_import(self, job_id: str) -> dict | None:
        """Stop a reading (the page's Stop reading): Claude's process is killed and the job is "stopped" at once,
        so another reading may start. None for an unknown id; ComposeError when it is not reading."""
        with self._import_lock:
            job = self._imports.get(job_id)
            if job is None:
                return None
            if job["state"] != "reading":
                raise ComposeError(f"that reading is {job['state']}: nothing to stop")
            job["stop"].set()
            job.update(state="stopped", error="stopped: Claude's reading was ended and nothing was read")
            return self._public(job)

    def _keep(self, job: dict) -> dict:
        with self._import_lock:
            self._imports[job["job"]] = job
            for old in list(self._imports)[:-self.IMPORTS_KEPT]:
                if self._imports[old]["state"] != "reading":
                    del self._imports[old]
        return job

    def _read(self, job: dict) -> None:
        import shutil
        import tempfile

        from vesper.lab.plan import PlanError
        tmp = Path(tempfile.mkdtemp(prefix="vesper-import-"))
        try:
            path = tmp / f"plan.{job['kind']}"
            path.write_bytes(job["image"])
            read = self._reader
            if read is None:
                from vesper.lab.plan_read import read_plan_image

                def read(p, stop, meta):
                    return read_plan_image(p, stop=stop, meta=meta)
            meta: dict = {}  # (path, stop, meta): `stop` set ends the reading; meta gets floors_drawn
            plan = read(path, job["stop"], meta)
            floors = meta.get("floors_drawn")
            result = {"plan": plan, "floors_drawn": floors, **import_check(plan, floors_drawn=floors)}
            saved = None if job["stop"].is_set() else self._save_reading(job["job"], plan)
            end = {**result, **({"saved": saved} if saved else {}), "state": "done"}
        except PlanError as e:
            end = {"state": "failed", "error": str(e)}
        except Exception as e:  # noqa: BLE001 - said to the page, never a silent thread
            end = {"state": "failed", "error": f"{type(e).__name__}: {e}"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        with self._import_lock:
            if job["state"] == "reading":  # a stopped job stays stopped
                job.update(end)

    def _save_reading(self, job_id: str, plan: dict) -> str | None:
        """A paid reading kept in the stack's folder (imports/<job>.plan.json), to import again with no Claude."""
        try:
            folder = self.stack_root / "imports"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{job_id}.plan.json"
            with open(path, "x") as f:
                json.dump(plan, f, indent=1)
            return str(path)
        except OSError:
            return None  # the page still has the plan; nothing else depends on the copy

    def import_status(self, job_id: str | None = None) -> dict | None:
        """One job, or the one a reopened page follows: the reading going on, else the newest ({"state": "none"}
        when there is none); None for an unknown id."""
        with self._import_lock:
            if job_id is None:
                jobs = list(self._imports.values())
                follow = next((j for j in jobs if j["state"] == "reading"), jobs[-1] if jobs else None)
                return self._public(follow) if follow is not None else {"state": "none"}
            job = self._imports.get(job_id)
            return self._public(job) if job is not None else None

    def import_image(self, job_id: str) -> tuple[bytes, str] | None:
        """The image a job read, and its type, for the page to draw under the plan; None for a PDF (never drawn)."""
        with self._import_lock:
            job = self._imports.get(job_id)
            if job is None or "image" not in job or job["kind"] not in ("png", "jpg"):
                return None
            return job["image"], {"png": "image/png", "jpg": "image/jpeg"}[job["kind"]]

    def hub(self) -> bool:
        """A played home gets Home Assistant (set up here)."""
        return hub_ready(self.stack_root)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def meta(self) -> dict:
        from vesper.env.site import CITIES
        from vesper.lab.generate import AREAS, PROTOCOLS, RULES
        cats: dict[str, int] = {}
        for r in self.catalog.values():
            c = (r.get("identity") or {}).get("category")
            if c and network_of(r)[1]:
                cats[c] = cats.get(c, 0) + 1
        return {"room_types": sorted(AREAS), "categories": dict(sorted(cats.items())),
                "placed_by_rule": sorted(RULES), "protocols": [p for p in PROTOCOLS if p != "zwave"],
                "out": str(self.out), "hub": self.hub(), "furnish": self.furnish_tools(), "cities": list(CITIES)}

    # -- furnishing in Blender with Claude (vesper.lab.furnish) -------------------------------------------
    def furnish_tools(self) -> dict:
        import shutil

        from vesper.lab.furnish import addon_file, blender_app
        return {"claude": shutil.which("claude") is not None,
                "blender": blender_app() is not None and addon_file() is not None}

    def furnishing(self) -> bool:
        return self._furnish is not None and self._furnish.status()["state"] == "running"

    def furnish(self, body) -> dict:
        """Start a run that has Claude furnish the composed home in Blender: {model, budget} from the page."""
        with self.lock:
            if not isinstance(body, dict):
                raise ComposeError("a furnishing request is a JSON object")
            if self.furnishing():
                raise ComposeError("Claude is furnishing this home already: stop that run first")
            home = self.out / "home.yaml"
            if not home.is_file():
                raise ComposeError("generate the home first")
            model = body.get("model") or None
            if model not in (None, "opus", "sonnet"):
                raise ComposeError("model: opus or sonnet")
            try:
                budget = float(body.get("budget", 25))
            except (TypeError, ValueError) as e:
                raise ComposeError("budget: US dollars, a number") from e
            if not 1 <= budget <= 100:
                raise ComposeError("budget: 1 to 100 US dollars")
            make = self._furnisher
            if make is None:
                from vesper.lab.furnish import Furnisher as make
            from vesper.lab.furnish import FurnishError
            try:
                run = make(home, self.catalog, self.catalog_dir, model=model, budget=budget)
            except FurnishError as e:
                raise ComposeError(str(e)) from e
            self.stop_play()  # the home's model and map change under a play
            self._furnish = run
            run.start()
            return run.status()

    def furnish_status(self) -> dict:
        return self._furnish.status() if self._furnish is not None else {"state": "none"}

    def stop_furnish(self) -> dict:
        if self._furnish is not None:
            self._furnish.stop()
        return self.furnish_status()

    def scenes(self) -> dict:
        """The HSSD houses here, the complete ones first, each with what the page shows to choose by."""
        from vesper.lab import scenes as hssd
        try:
            found = hssd.list_scenes(hssd.hssd_dir(None))
        except hssd.SceneError as e:
            return {"scenes": [], "error": str(e)}
        return {"scenes": [{"id": s.id, "rooms": s.rooms, "labels": s.labels, "indoor_m2": s.indoor_m2,
                            "storeys": s.storeys, "ready": s.ready, "missing": len(s.missing)} for s in found],
                "habitat": hssd.habitat_python(None).is_file()}

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
            hub = self.hub()
            if not self._wait(self.play_port, timeout=HUB_START_S if hub else 30.0,
                              alive=lambda: child.poll() is None):
                why = _last_words(getattr(child, "log", None))
                self.stop_play()
                raise ComposeError(f"vesper lab play stopped before it opened port {self.play_port}: {why}" if why
                                   else f"vesper lab play stopped before it opened port {self.play_port}; run "
                                   f"`vesper lab play {home}` to see why")
            return {"url": f"http://127.0.0.1:{self.play_port}/", "hub": hub}

    def generate(self, request) -> dict:
        with self.lock:
            if self.furnishing():
                raise ComposeError("Claude is furnishing this home in Blender: stop that run before generating again")
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
        if child is not None and isinstance(getattr(child, "log", None), Path):
            child.log.unlink(missing_ok=True)

    def start(self) -> None:
        import threading
        from http.server import ThreadingHTTPServer
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self._want_port), self._handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, name="vesper-compose", daemon=True).start()

    def stop(self) -> None:
        self.stop_play()
        if self.furnishing():
            self._furnish.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def _handler(self):
        from http.server import BaseHTTPRequestHandler
        from urllib.parse import parse_qs, urlparse
        server = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 60  # seconds a stalled client may hold its thread (a 50 MB upload on loopback takes far less)

            def log_message(self, *a):
                pass

            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")  # an imported image is served as its type only
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
                if u.path == "/api/import":
                    return self._json(200, server.import_status())
                job = IMPORT_PATH.match(u.path)
                if job and job.group(2):
                    found = server.import_image(job.group(1))
                    if found is None:
                        return self._json(404, {"error": "no such reading"})
                    return self._send(200, found[0], found[1])
                if job:
                    status = server.import_status(job.group(1))
                    return self._json(200, status) if status is not None else self._json(404, {"error":
                                                                                               "no such reading"})
                if u.path == "/api/furnish":
                    return self._json(200, server.furnish_status())
                if u.path == "/api/scenes":
                    return self._json(200, server.scenes())
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

            def _import(self):
                """The import's body is the file itself (an image, or a plan's JSON), up to 50 MB, its size checked
                before it is read and its first bytes before anything else."""
                from vesper.lab.plan_read import MAX_BYTES
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n > MAX_BYTES:
                    self.close_connection = True  # the body is not read
                    return self._json(413, {"error": f"the file is larger than {MAX_BYTES // 2**20} MB"})
                if n <= 0:
                    return self._json(400, {"error": "no file (or no length)"})
                try:
                    data = self.rfile.read(n)
                except OSError:  # the socket's timeout: a client that stalled mid-upload
                    data = b""
                if len(data) != n:  # a body cut short is never read, by Claude or anyone
                    self.close_connection = True
                    return self._json(400, {"error": "the file arrived incomplete: send it again"})
                try:
                    return self._json(200, server.start_import(data, self.headers.get("X-Vesper-Name")))
                except ComposeError as e:
                    return self._json(400, {"error": str(e)})

            def do_POST(self):  # noqa: N802
                if not self._loopback():
                    return self._json(403, {"error": "another site's page"})
                if self.path == "/api/import":
                    return self._import()
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
                    if self.path == "/api/furnish":
                        return self._json(200, server.furnish(body))
                    if self.path == "/api/furnish/stop":
                        return self._json(200, server.stop_furnish())
                    if stop := STOP_PATH.match(self.path):
                        stopped = server.stop_import(stop.group(1))
                        return self._json(200, stopped) if stopped is not None else self._json(
                            404, {"error": "no such reading"})
                    if self.path == "/api/import/check":
                        if not isinstance(body, dict) or not isinstance(body.get("plan"), dict):
                            raise ComposeError("a check is {plan, scale}: the plan as edited and the scale text")
                        return self._json(200, import_check(body["plan"], body.get("scale"), body.get("door_numbers"),
                                                            body.get("floors_drawn")))
                except ComposeError as e:
                    return self._json(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001 - said to the page, never a dropped connection
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return self._json(404, {"error": "not found"})
        return Handler
