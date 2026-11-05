"""The plan library: real floor plans to choose from by their picture, in compose's gallery (design 2026-11-05).

The library is a folder of plans, one folder each holding the plan's image (`F1_scaled.png`) and its drawn ground
truth (`model.svg`), grouped one level up (CubiCasa5K's subsets: colorful, high_quality, high_quality_architectural).
It is the user's own download (`vesper setup plan-library`), recorded as `plan_library` in VESPER's configuration;
nothing of it is part of VESPER. CubiCasa5K is licensed CC BY-NC-SA 4.0.

- The index: per plan its id ("high_quality/1245"), image size, rooms, floors, area and whether its ground truth
  parses into a plan VESPER reads. Built once in the background (the gallery shows its progress and the plans indexed
  so far), cached in VESPER's cache folder keyed by the library's path and its number of plans, and rebuilt only when
  that changes.
- Thumbnails: about 320 px wide, made on demand with Pillow when it is installed and kept in the same cache; without
  Pillow the plan's own image is served and the browser scales it.
- A plan used: its ground truth (`vesper.validation.plans.parse_cubicasa_svg`) as a `vesper-plan/1` in the image's
  pixels, of one storey: a plan that draws two floors is built from the floor with its entrance, as the image import
  reads only that floor.

Every request names a plan by its id, and an id is looked up in the index: nothing from a request reaches the file
system. Rooms are counted as a listing counts them (living rooms, bedrooms, dining rooms, offices): CubiCasa5K draws
every closet and storage space as its own space, so counting spaces would make a two-bedroom flat a "9 rooms" plan.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections import Counter
from pathlib import Path

IMAGE = "F1_scaled.png"  # the image whose pixels the ground truth's coordinates are
TRUTH = "model.svg"
FOLDER = "cubicasa5k"  # the library's own folder, when `plan_library` names the folder holding it
LICENCE = "CC BY-NC-SA 4.0"
# the archive's Zenodo record, as vesper.validation.plans fetches it (kept here so setup needs no numpy; a test keeps
# the two equal)
SOURCE_URL = "https://zenodo.org/records/2613548/files/cubicasa5k.zip?download=1"
INDEX_VERSION = 1  # bumped when an index row changes meaning: an old cache is rebuilt
THUMB_WIDTH = 320
PAGE = 24
PAGE_MAX = 96
CACHE_ENV = "VESPER_CACHE"  # as vesper.lab.dress: VESPER's cache folder
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,64}$")
LIVING = ("living", "bedroom", "dining", "office")  # the rooms a listing counts
ROOMS = {"1-2": (1, 2), "3-4": (3, 4), "5-6": (5, 6), "7-": (7, None)}  # the gallery's room filters
ASSUMED = "assumed 1 cm a unit"  # parse_cubicasa_svg's scale_source with no dimension label


class LibraryError(ValueError):
    """A request the library cannot answer (a filter it does not know); the message says why."""


def cache_root() -> Path:
    """VESPER's cache folder for the library's index and thumbnails: derived from the library, so never in the
    repository."""
    return Path(os.environ.get(CACHE_ENV) or Path.home() / ".cache" / "vesper") / "plan-library"


def find_library(path: str | Path | None) -> Path | None:
    """The library's folder: `path` itself when it holds plans, else its `cubicasa5k/`; None when neither does."""
    if not path:
        return None
    p = Path(path).expanduser()
    for candidate in (p / FOLDER, p):
        if candidate.is_dir() and any(True for _ in _plan_folders(candidate, first=True)):
            return candidate
    return None


def configured_library() -> str | None:
    from vesper.setup_tools import configured
    return configured("plan_library")


def _number_first(name: str) -> tuple:
    return (0, int(name), "") if name.isdigit() else (1, 0, name)


def _plan_folders(root: Path, first: bool = False):
    """Each plan's folder under `root` (<group>/<plan>/ with its image and ground truth), in a stable order: groups by
    name, plans by number."""
    for group in sorted((g for g in root.iterdir() if g.is_dir() and not g.name.startswith(".")), key=lambda g: g.name):
        try:
            names = sorted((d.name for d in group.iterdir() if d.is_dir()), key=_number_first)
        except OSError:
            continue
        for name in names:
            pid = f"{group.name}/{name}"
            if ID.match(pid) and (group / name / TRUTH).is_file() and (group / name / IMAGE).is_file():
                yield pid
                if first:
                    return


def plan_ids(root: Path) -> list[str]:
    return list(_plan_folders(Path(root)))


def png_size(path: Path) -> list[int] | None:
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return list(struct.unpack(">II", head[16:24]))


# -- one plan's ground truth -------------------------------------------------------------------------------------------
def floor_plan(truth: dict) -> tuple[dict, int]:
    """The ground truth's plan of one storey, and which (from 0): the floor with the entrance (the floor with the
    most rooms when there is no entrance). A plan of one floor comes back as it is. Doors stay when both their sides
    are on that floor (or outside), windows when their room is; the entrance is renumbered."""
    plan = truth["plan"]
    floors_of = dict(zip((r["id"] for r in plan["rooms"]), truth.get("room_floors") or [], strict=False))
    if len(set(floors_of.values())) <= 1:
        return plan, next(iter(floors_of.values()), 0)
    ent = plan.get("entrance")
    if ent is not None:
        floor = floors_of[next(s for s in plan["doors"][ent]["rooms"] if s != "outside")]
    else:
        counts = Counter(floors_of.values())
        floor = max(sorted(counts), key=lambda f: counts[f])
    keep = {rid for rid, f in floors_of.items() if f == floor}
    doors, entrance = [], None
    for k, d in enumerate(plan["doors"]):
        if all(s == "outside" or s in keep for s in d["rooms"]):
            if k == ent:
                entrance = len(doors)
            doors.append(d)
    return {**plan, "rooms": [r for r in plan["rooms"] if r["id"] in keep], "doors": doors,
            "windows": [w for w in plan["windows"] if w["room"] in keep], "entrance": entrance}, floor


def library_plan(truth: dict) -> dict:
    """The plan VESPER builds from a ground truth: its entrance floor, in the image's pixels, with its scale. A scale
    taken from the plan's dimension labels is a measured one, so it is stated as a length (`length_m` over 1000 px,
    which `vesper.lab.plan.measured_scale` takes as measured: rooms of an unusual size are said, not refused); the
    1 cm a unit assumed when the plan has no label stays an inferred `m_per_px`."""
    plan, _floor = floor_plan(truth)
    m = float(truth["m_per_px"])
    scale = ({"m_per_px": m} if truth["scale_source"] == ASSUMED
             else {"length_m": round(m * 1000, 6), "from": [0, 0], "to": [1000, 0]})
    return {**plan, "scale": scale}


def _area(poly) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1], strict=True))) / 2


def read_truth(root: Path, pid: str) -> dict:
    from vesper.validation.plans import parse_cubicasa_svg
    return parse_cubicasa_svg((Path(root) / pid / TRUTH).read_text(encoding="utf-8"))


def index_row(root: Path, pid: str) -> dict:
    """What the gallery shows and filters a plan by: {id, group, size [w, h], rooms (as a listing counts them, on the
    floor built), spaces (every space drawn on it), floors, area_m2 (indoors, on the floor built), scale ("labels" or
    "assumed"), ok (its ground truth is a plan VESPER reads)} and, when not ok, why."""
    from vesper.lab.plan import PlanError, load_plan
    row = {"id": pid, "group": pid.split("/", 1)[0], "size": None, "rooms": 0, "spaces": 0, "floors": 0,
           "area_m2": 0.0, "scale": None, "ok": False}
    try:
        row["size"] = png_size(Path(root) / pid / IMAGE)
        truth = read_truth(root, pid)
        plan = library_plan(truth)
        m = float(truth["m_per_px"])
        indoor = [r for r in plan["rooms"] if r["type"] != "balcony" and len(r["polygon"]) >= 3]
        row.update(rooms=sum(r["type"] in LIVING for r in plan["rooms"]), spaces=len(plan["rooms"]),
                   floors=truth["floors"], area_m2=round(sum(_area(r["polygon"]) for r in indoor) * m * m, 1),
                   scale="assumed" if truth["scale_source"] == ASSUMED else "labels")
        load_plan(plan)
        row["ok"] = True
    except (PlanError, ValueError, KeyError, IndexError, OSError, StopIteration) as e:  # an ElementTree ParseError
        row["error"] = f"{type(e).__name__}: {e}"[:300]  # is a SyntaxError: caught below
    except SyntaxError as e:
        row["error"] = f"model.svg is not XML: {e}"[:300]
    return row


# -- the gallery's filters ---------------------------------------------------------------------------------------------
def filters(rooms: str | None = None, floors: str | None = None, q: str | None = None) -> dict:
    """The gallery's query as a filter: rooms one of ROOMS' keys (empty: any), floors "1" (one floor) or empty (any),
    q words found in the plan's id. LibraryError for a filter it does not know."""
    rooms, floors = (rooms or "").strip(), (floors or "").strip()
    if rooms and rooms not in ROOMS:
        raise LibraryError(f"rooms: one of {', '.join(ROOMS)}, or empty for any")
    if floors not in ("", "1"):
        raise LibraryError("floors: 1 (one floor) or empty for any")
    return {"rooms": ROOMS.get(rooms), "one_floor": floors == "1", "words": (q or "").lower().split()[:8]}


def matches(row: dict, f: dict) -> bool:
    if not row.get("ok"):
        return False
    if f["rooms"]:
        lo, hi = f["rooms"]
        if row["rooms"] < lo or (hi is not None and row["rooms"] > hi):
            return False
    if f["one_floor"] and row["floors"] != 1:
        return False
    return all(w in row["id"].lower() for w in f["words"])


# -- the library -------------------------------------------------------------------------------------------------------
def _pillow():
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def pillow_thumbnail(src: Path, dest: Path, width: int = THUMB_WIDTH) -> bool:
    """A JPEG about `width` px wide of the image at `src`, written to `dest`; False without Pillow."""
    Image = _pillow()  # noqa: N806 - Pillow's own name
    if Image is None:
        return False
    with Image.open(src) as im:
        im.draft("RGB", (width, width * 4))  # a JPEG decodes at a reduced size; a PNG ignores it
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
        fd, tmp = tempfile.mkstemp(prefix=".thumb-", suffix=".jpg", dir=dest.parent)
        with os.fdopen(fd, "wb") as f:
            im.save(f, "JPEG", quality=82, optimize=True)
    os.replace(tmp, dest)
    return True


class PlanLibrary:
    """The library behind the gallery: its index (built in the background), its listing, thumbnails, images and
    plans, every one by an id the index holds. `folder` is the configured path (None: not configured); `thumbnailer`
    (src, dest) -> bool makes a thumbnail (pillow_thumbnail; a test's stand-in)."""

    def __init__(self, folder: str | Path | None, cache: Path | None = None, *, thumbnailer=None):
        self.folder = str(folder) if folder else None
        self.root = find_library(folder)
        self.cache = Path(cache) if cache is not None else cache_root()
        self._thumbnailer = thumbnailer or pillow_thumbnail
        self._lock = threading.Lock()
        self._rows: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._state = "absent" if self.root is None else "idle"
        self._total = 0
        self._error: str | None = None
        self.seconds: float | None = None  # how long the index took (None: read from the cache, or not yet built)

    # -- the index ----------------------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        """absent (no library at the folder), idle (not indexed yet), indexing, ready or failed."""
        return self._state

    @property
    def key(self) -> str:
        return hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:16]

    @property
    def place(self) -> Path:
        """This library's own folder in the cache: its index and thumbnails."""
        return self.cache / self.key

    def ensure(self) -> None:
        """Start the index (once): from the cache when it is current, else built in the background."""
        with self._lock:
            if self._state != "idle":
                return
            self._state = "indexing"
        threading.Thread(target=self._index, name="vesper-plan-library", daemon=True).start()

    def wait(self, timeout: float = 60.0) -> str:
        """The state once the index is no longer being built (tests; the CLI's timing)."""
        import time
        self.ensure()
        end = time.monotonic() + timeout
        while self._state == "indexing" and time.monotonic() < end:
            time.sleep(0.02)
        return self._state

    def _index(self) -> None:
        import time
        t0 = time.monotonic()
        try:
            import numpy  # noqa: F401 - the ground truth's reader (vesper.validation.plans) needs it
        except ImportError:
            with self._lock:
                self._state, self._error = "failed", ("reading the plans needs numpy: pip install "
                                                      "'vesper-studio[gallery]'")
            return
        try:
            ids = plan_ids(self.root)
            with self._lock:
                self._total = len(ids)
            saved = self._load(len(ids))
            if saved is not None:
                with self._lock:
                    self._rows, self._by_id = saved, {r["id"]: r for r in saved}
                    self._state = "ready"
                return
            for pid in ids:
                row = index_row(self.root, pid)
                with self._lock:
                    self._rows.append(row)
                    self._by_id[pid] = row
            self._save(len(ids))
            self.seconds = time.monotonic() - t0
            with self._lock:
                self._state = "ready"
        except OSError as e:  # the library's drive went away while it was read
            with self._lock:
                self._state, self._error = "failed", f"the library could not be read: {e}"

    def _index_file(self) -> Path:
        return self.place / "index.json"

    def _load(self, count: int) -> list[dict] | None:
        try:
            data = json.loads(self._index_file().read_text())
        except (OSError, ValueError):
            return None
        if (not isinstance(data, dict) or data.get("version") != INDEX_VERSION or data.get("count") != count
                or data.get("library") != str(self.root.resolve())):
            return None
        rows = data.get("plans")
        return rows if isinstance(rows, list) and len(rows) == count else None

    def _save(self, count: int) -> None:
        try:
            self.place.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".tmp", dir=self.place)
            with os.fdopen(fd, "w") as f:
                json.dump({"version": INDEX_VERSION, "library": str(self.root.resolve()), "count": count,
                           "plans": self._rows}, f)
            os.replace(tmp, self._index_file())
        except OSError:
            pass  # indexed again next time; the gallery works from memory meanwhile

    # -- what the gallery asks --------------------------------------------------------------------------------
    def listing(self, offset: int = 0, limit: int = PAGE, rooms: str | None = None, floors: str | None = None,
                q: str | None = None) -> dict:
        """{state: absent | indexing | ready | failed, progress {done, total}, total (plans matching), items (this
        page of them), unreadable (plans whose ground truth VESPER cannot read: never listed), folder (the configured
        path), licence, error}. Starts the index."""
        f = filters(rooms, floors, q)
        offset, limit = max(int(offset), 0), min(max(int(limit), 1), PAGE_MAX)
        self.ensure()
        with self._lock:
            rows, state, total, error = list(self._rows), self._state, self._total, self._error
        found = [r for r in rows if matches(r, f)]
        return {"state": state, "progress": {"done": len(rows), "total": total}, "total": len(found),
                "items": found[offset:offset + limit], "unreadable": sum(not r["ok"] for r in rows),
                "folder": self.folder, "licence": LICENCE, "error": error}

    def row(self, pid: str) -> dict | None:
        """The plan's index row, for an id the index holds (anything else: None)."""
        if not isinstance(pid, str) or not ID.match(pid):
            return None
        with self._lock:
            return self._by_id.get(pid)

    def image(self, pid: str) -> bytes | None:
        """The plan's own image (the preview draws its rooms over it), or None."""
        if self.row(pid) is None:
            return None
        return (self.root / pid / IMAGE).read_bytes()

    def thumb(self, pid: str) -> tuple[bytes, str] | None:
        """A thumbnail (made once, then from the cache) and its type; the plan's own image without Pillow."""
        if self.row(pid) is None:
            return None
        dest = self.place / "thumbs" / (pid.replace("/", "__") + ".jpg")
        if dest.is_file():
            return dest.read_bytes(), "image/jpeg"
        src = self.root / pid / IMAGE
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if self._thumbnailer(src, dest):
                return dest.read_bytes(), "image/jpeg"
        except OSError:
            pass  # a cache that cannot be written: the image itself still shows
        return src.read_bytes(), "image/png"

    def plan(self, pid: str) -> dict | None:
        """The plan as the gallery's preview and Use this plan take it: {id, name (a file name for the home), plan
        (vesper-plan/1 of the floor built, in the image's pixels), floors (drawn), floor (built, from 0), rooms_drawn
        (on every floor), m_per_px, scale_source, size}; None for an id the index does not hold."""
        row = self.row(pid)
        if row is None or not row["ok"]:
            return None
        truth = read_truth(self.root, pid)
        _plan, floor = floor_plan(truth)
        return {"id": pid, "name": f"{FOLDER}-{pid.replace('/', '-')}", "plan": library_plan(truth),
                "floors": truth["floors"], "floor": floor, "rooms_drawn": len(truth["plan"]["rooms"]),
                "m_per_px": truth["m_per_px"], "scale_source": truth["scale_source"], "size": row["size"],
                "row": row}
