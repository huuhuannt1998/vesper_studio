"""How well a scan or a 3D model is imported: the scan import's evaluation on the scan test set (task B5).

The test set (`.vesper/datasets/scans/`, git-ignored, never committed: README.md and MANIFEST.json there) holds Apple
RoomPlan exports, LiDAR and photogrammetry mesh scans and 3D models from the web. Its ground truth is written by hand
in `<dataset>/truth.json` (`vesper-scan-truth/1`, local like the models): per model, the file to import, its kind
("roomplan", "mesh_scan", "web_model"), the rooms (how many and of what types), the doors between rooms and out of
them, the windows, whether a real doorway leads out, the storeys, whether there is a ceiling, the true scale and up
axis, and how each was told and how sure it is. A count is a number, a range `{n, min, max}`, or null (cannot be told:
not scored). `types` lists, per room, the VESPER types that name it rightly.

For each model and reader, `evaluate` runs `vesper lab import`'s steps: the reading (`scan_import.read_scan`: a RoomPlan
file by its own reader, a mesh by the geometric reader or by Claude from its top-down render), then rectify,
check_house, the home built (`generate_from_plan` with the scan's objects and model, in a temporary folder) and its
layout checked (`layoutcheck.check`). It scores the reading against the truth:

- rooms, doors between rooms, doors out of them, windows: the count read against the true count or range (right when
  inside it; the error is the distance to it);
- room types: read types matched one to one to the true rooms' accepted types (a maximum matching);
- entrance: "real" (a door the reader saw), "assumed" (Claude put it in a drawn wall, or the geometric reader took the
  widest door out into a space too small to be a room) or "none"; right when real and the model shows a doorway out,
  or not real when it shows none;
- scale and up axis: the reading's metres per unit inside the true range, its up axis the true one; the ceiling found
  or not as the model has one;
- the stage reached ("read", "rectify", "check_house", "build", "layout", "done") and the layout errors.

Claude's readings are paid for once. Each is kept under `<dataset>/_claude/<claude_dir or model>/`: every answer as it
arrives (`envelope-<k>.json`), the render Claude read (`render.png`), the reading as `vesper lab import --save-plan`
writes it (`reading.json`, `vesper-scan-reading/1`), or `error.txt` when Claude's answers were no plan, and
`meta.json` (time, notional cost, models). Task B3b's earlier readings (`claude-plan.json`, `claude-meta.json`,
`claude-render.png`) are used too, but only while the render they were read from is the one the code draws now.
`offline` runs no Claude: a mesh with no kept reading is listed in "not_read"."""
from __future__ import annotations

import copy
import hashlib
import json
import statistics
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

TRUTH_SCHEMA = "vesper-scan-truth/1"
KINDS = {"roomplan": "RoomPlan", "mesh_scan": "mesh scan", "web_model": "web model"}
READERS = ("geometric", "claude")
COUNTS = ("rooms", "doors_inner", "doors_outside", "windows")
UPS = ("x", "y", "z", "-x", "-y", "-z")
STAGES = ("read", "rectify", "check_house", "build", "layout", "done")
FALLBACK_ENTRANCE = "is taken as the entrance"  # scan_mesh's warning when no door out was found and one is taken


class NotRead(Exception):
    """A mesh with no kept Claude reading, in an offline run."""


# --- the truth ---------------------------------------------------------------------------------------------------

def _range(t) -> tuple[int, int, int] | None:
    """A true count as (n, min, max), or None when it cannot be told. ValueError on a malformed one."""
    if t is None:
        return None
    if isinstance(t, bool):
        raise ValueError(f"a count is a number or {{n, min, max}}, not {t!r}")
    if isinstance(t, int):
        if t < 0:
            raise ValueError(f"a count is not negative: {t}")
        return t, t, t
    if isinstance(t, dict) and all(isinstance(t.get(k), int) and not isinstance(t.get(k), bool)
                                   for k in ("n", "min", "max")):
        if not 0 <= t["min"] <= t["n"] <= t["max"]:
            raise ValueError(f"a range is 0 <= min <= n <= max: {t}")
        return t["n"], t["min"], t["max"]
    raise ValueError(f"a count is a number or {{n, min, max}}, not {t!r}")


def load_truth(path: Path) -> dict:
    """The hand truth (`vesper-scan-truth/1`), checked: every model's kind, file, counts, types, scale and up axis."""
    from vesper.lab.plan import ROOM_TYPES
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or raw.get("schema") != TRUTH_SCHEMA:
        raise ValueError(f"{path}: not a {TRUTH_SCHEMA} file")
    models = raw.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError(f"{path}: no models")
    for mid, t in models.items():
        where = f"{path}: {mid}"
        if not isinstance(t, dict):
            raise ValueError(f"{where}: not an object")
        if t.get("kind") not in KINDS:
            raise ValueError(f"{where}: kind {t.get('kind')!r} is not one of {', '.join(KINDS)}")
        f = t.get("file")
        if not isinstance(f, str) or not f or Path(f).is_absolute() or ".." in Path(f).parts:
            raise ValueError(f"{where}: file {f!r} is not a path inside the model's folder")
        d = t.get("claude_dir")
        if d is not None and (not isinstance(d, str) or not d or "/" in d or d in (".", "..")):
            raise ValueError(f"{where}: claude_dir {d!r} is not a folder name")
        for k in COUNTS:
            try:
                _range(t.get(k))
            except ValueError as e:
                raise ValueError(f"{where}: {k}: {e}") from None
        if t.get("rooms") is None:
            raise ValueError(f"{where}: rooms cannot be unknown")
        types = t.get("types")
        if types is not None:
            if not isinstance(types, list) or not all(isinstance(r, list) and r for r in types):
                raise ValueError(f"{where}: types is a list of lists of room types")
            bad = sorted({x for r in types for x in r if x not in ROOM_TYPES})
            if bad:
                raise ValueError(f"{where}: types: {', '.join(map(repr, bad))} not VESPER room types")
        if t.get("up") is not None and t["up"] not in UPS:
            raise ValueError(f"{where}: up {t['up']!r} is not one of {', '.join(UPS)}")
        s = t.get("scale")
        if s is not None:
            if not isinstance(s, dict) or not all(isinstance(s.get(k), (int, float)) and s[k] > 0
                                                  for k in ("m_per_unit", "min", "max")):
                raise ValueError(f"{where}: scale is {{m_per_unit, min, max}}, all above 0")
            if not s["min"] <= s["m_per_unit"] <= s["max"]:
                raise ValueError(f"{where}: scale: min <= m_per_unit <= max")
        for k in ("ceiling", "entrance"):
            if t.get(k) is not None and not isinstance(t[k], bool):
                raise ValueError(f"{where}: {k} is true, false or null")
    return raw


# --- the score ---------------------------------------------------------------------------------------------------

def count_score(pred: int | None, truth) -> dict:
    """A count read (None: nothing read) against the true count or range: right when inside it; "error" is the signed
    distance to it (0 inside; a reading of nothing counts as 0). Not scored ("ok" None) when the truth is unknown."""
    r = _range(truth)
    if r is None:
        return {"pred": pred, "true": None, "min": None, "max": None, "ok": None, "error": None}
    n, lo, hi = r
    p = 0 if pred is None else pred
    error = p - lo if p < lo else p - hi if p > hi else 0
    return {"pred": pred, "true": n, "min": lo, "max": hi, "ok": pred is not None and error == 0, "error": error}


def type_score(pred: list[str], truth: list[list[str]] | None) -> dict:
    """How many true rooms a read room of an accepted type can be matched to, one to one (a maximum matching)."""
    if truth is None:
        return {"right": None, "of": None}
    match: dict[int, int] = {}  # read room -> true room

    def augment(i: int, seen: set) -> bool:
        for j, t in enumerate(pred):
            if t in truth[i] and j not in seen:
                seen.add(j)
                if j not in match or augment(match[j], seen):
                    match[j] = i
                    return True
        return False
    right = sum(augment(i, set()) for i in range(len(truth)))
    return {"right": right, "of": len(truth)}


def reading_facts(plan: dict | None, meta: dict | None, warnings: list[str]) -> dict | None:
    """What a reading says, to be scored: rooms and their types, doors between rooms and out of them, windows, the
    entrance ("real", "assumed", "none"), the up axis, metres per unit and whether a ceiling was found. A RoomPlan
    reading with no model (meta None) is in metres, y up, with no ceiling."""
    if plan is None:
        return None
    doors = plan.get("doors") or []
    out = sum("outside" in d["rooms"] for d in doors)
    meta = meta or {}
    if plan.get("entrance") is None:
        entrance = "none"
    elif meta.get("entrance_drawn") is False or any(FALLBACK_ENTRANCE in w for w in warnings):
        entrance = "assumed"
    else:
        entrance = "real"
    return {"rooms": len(plan["rooms"]), "types": [r["type"] for r in plan["rooms"]], "doors_inner": len(doors) - out,
            "doors_outside": out, "windows": len(plan.get("windows") or []), "entrance": entrance,
            "up": str(meta.get("up", "y")).lstrip("+"), "scale": float(meta.get("scale", 1.0)),
            "ceiling": meta.get("ceiling_y") is not None}


def score(facts: dict | None, truth: dict) -> dict:
    """A reading's facts (None: nothing read) against one model's truth. Each field is None where the truth is
    unknown. A mesh no plan was read from may still have its up axis, scale and ceiling (`detected`): those are scored
    from them, everything else as nothing read."""
    f = facts or {}
    out = {k: count_score(f.get(k), truth.get(k)) for k in COUNTS}
    out["types"] = type_score(f.get("types") or [], truth.get("types"))
    t_ent, t_scale, t_up, t_ceiling = (truth.get(k) for k in ("entrance", "scale", "up", "ceiling"))
    out["entrance_ok"] = None if t_ent is None else (f.get("entrance") == "real") == t_ent
    out["scale_ok"] = None if t_scale is None else ("scale" in f and t_scale["min"] <= f["scale"] <= t_scale["max"])
    out["up_ok"] = None if t_up is None else ("up" in f and f["up"] == t_up)
    out["ceiling_ok"] = None if t_ceiling is None else ("ceiling" in f and f["ceiling"] == t_ceiling)
    return out


# --- the import of a reading ---------------------------------------------------------------------------------------

def import_check(scan, catalog: dict | None) -> dict:
    """What `vesper lab import` makes of a scan's reading (as its `_lab_import_scan` does): rectify, check_house
    (metric: a scan's scale is not refused), then, with a catalog, the home built with the scan's objects as
    furniture and its model as the 3D view, in a temporary folder, and its layout checked. "stage" is where it stopped
    ("done" when the layout was checked); the plan's rooms, doors and windows after rectify are kept."""
    from vesper.lab.generate import GenerateError, Request, generate_from_plan, starter
    from vesper.lab.plan import PlanError, check_house, plan_transform, rectify
    out = {"stage": "rectify", "check_house": False, "problems": [], "warnings": list(scan.warnings),
           "rooms": None, "doors": None, "windows": None, "layout_errors": None, "layout_warnings": None,
           "layout_failures": [], "devices": None}
    try:
        raw = copy.deepcopy(scan.plan)
        turn = plan_transform(raw)
        numbers = list(range(len(raw.get("doors") or [])))
        window_numbers = list(range(len(raw.get("windows") or [])))
        plan = rectify(raw, out["warnings"], numbers, window_numbers)
        out["stage"] = "check_house"
        out["problems"] = check_house(plan, numbers, out["warnings"], metric=True)
    except PlanError as e:
        out["problems"] = [str(e)]
        return out
    out.update(rooms=len(plan["rooms"]), doors=len(plan.get("doors") or []), windows=len(plan.get("windows") or []))
    if out["problems"]:
        return out
    out["check_house"] = True
    if catalog is None:
        return out
    out["stage"] = "build"
    from vesper.lab.home import load_lab_home
    from vesper.lab.layoutcheck import check
    md, mw = scan.heights.get("doors") or [], scan.heights.get("windows") or []
    heights = {"doors": [md[n] if n < len(md) else None for n in numbers],
               "windows": [mw[n] if n < len(mw) else None for n in window_numbers]}
    types = [r["type"] for r in plan["rooms"]]
    meta = scan.meta or {}
    model = {"path": scan.model, "transform": meta["transform"]} if scan.model is not None else None
    provenance = {"kind": scan.kind, "given": scan.given.name, "read": scan.read.name, "turn": turn,
                  "heights": heights, "files": [scan.given, scan.read], "notes": scan.notes,
                  "warnings": out["warnings"],
                  "model": ({k: meta.get(k) for k in ("up", "scale", "floor_y", "ceiling_y", "transform")}
                            if scan.model is not None else None)}
    try:
        with tempfile.TemporaryDirectory(prefix="vesper-scans-") as tmp:
            devices, _left = starter(types, catalog)
            home_dir = Path(tmp) / "home"
            generate_from_plan(plan, Request(Path("."), types, 0, devices), catalog, home_dir,
                               source=scan.given.name, numbers=numbers, objects=scan.pieces, model=model,
                               scan=provenance)
            home = load_lab_home(home_dir / "home.yaml", catalog)
            out["devices"] = len(home.devices)
            out["stage"] = "layout"
            report = check(home)
    except (GenerateError, PlanError, ValueError, OSError) as e:
        out["problems"] = [f"{type(e).__name__}: {e}"]
        return out
    out.update(stage="done", layout_errors=report["errors"], layout_warnings=report["warnings"],
               layout_failures=[f"{f['rule']}: {f['message']}" for f in report["findings"]
                                if not f["ok"] and f["severity"] == "error"])
    return out


# --- the readings --------------------------------------------------------------------------------------------------

def _envelope_facts(text: str) -> dict:
    try:
        env = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(env, dict):
        return {}
    return {"cost_usd": env.get("total_cost_usd") or 0.0, "duration_ms": env.get("duration_ms") or 0,
            "models": sorted(env.get("modelUsage") or {})}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _from_reading(raw: dict, src: Path):
    """A kept reading (`vesper-scan-reading/1`) as the Scan `vesper lab import` builds from it, its model the test
    set's file (wherever the set now is)."""
    from vesper.lab.scan_import import load_reading
    return load_reading(src.parent / "reading.json", {**raw, "source": str(Path(src).absolute())})


def _b3b_entrance_drawn(plan: dict, meta: dict, view: dict, png: Path) -> bool | None:
    """Task B3b's meta has no "entrance_drawn": the entrance is found in the render it was read from again (the plan
    in metres taken back to the render's pixels by the two transforms; None when they do not agree)."""
    import numpy as np

    from vesper.lab.scan_render import _entrance_drawn
    t, vt = np.asarray(meta["transform"]), np.asarray(view["transform"])
    if plan.get("entrance") is None or not np.allclose(t[:3, :3], vt[:3, :3], atol=1e-6):
        return None
    mpp = view["m_per_px"]
    sx = vt[0, 3] - t[0, 3] - view["origin"][0]
    sz = vt[2, 3] - t[2, 3] - view["origin"][1]
    doors = [{"at": [(d["at"][0] + sx) / mpp, (d["at"][1] + sz) / mpp]} for d in plan["doors"]]
    return _entrance_drawn({"units": "px", "entrance": plan["entrance"], "doors": doors}, png, 1.0 / mpp)


def _b3b(folder: Path, src: Path) -> tuple[object, dict] | None:
    """Task B3b's reading in `folder`, as a Scan with what is known of it; None when it is not there, or when the
    render it was read from is not the one the code draws now (the reading then belongs to an older render)."""
    from vesper.lab.scan_import import READING
    from vesper.lab.scan_render import render_plan_view
    plan_f, meta_f, png = folder / "claude-plan.json", folder / "claude-meta.json", folder / "claude-render.png"
    if not (plan_f.is_file() and meta_f.is_file() and png.is_file()):
        return None
    with tempfile.TemporaryDirectory(prefix="vesper-scans-") as tmp:
        now = Path(tmp) / "render.png"
        view = render_plan_view(src, now)
        if now.read_bytes() != png.read_bytes():
            return None
    plan, meta = json.loads(plan_f.read_text()), json.loads(meta_f.read_text())
    summary = json.loads((folder / "claude.json").read_text()) if (folder / "claude.json").is_file() else {}
    if "entrance_drawn" not in meta:
        meta["entrance_drawn"] = _b3b_entrance_drawn(plan, meta, view, png)
    raw = {"format": READING, "plan": plan, "meta": meta, "warnings": summary.get("warnings") or []}
    return _from_reading(raw, src), {"seconds": summary.get("s"), "cost_usd": None, "render_sha256": _sha(png)}


def _read_with_claude(src: Path, folder: Path, runner, model: str | None, timeout: float):
    """A new Claude reading of the mesh at `src`, every answer kept in `folder` as it arrives, then the reading as
    `--save-plan` writes it (reading.json) or error.txt. A render the code refuses (ScanError: no Claude call) is
    raised and not kept: it costs nothing to try again."""
    from vesper.lab import plan_read
    from vesper.lab.plan import PlanError
    from vesper.lab.scan_import import Scan, save_reading
    from vesper.lab.scan_mesh import ScanError
    from vesper.lab.scan_render import read_mesh_with_claude
    folder.mkdir(parents=True, exist_ok=True)
    inner = runner or plan_read._claude
    calls: list[dict] = []

    def run(args, cwd=None, timeout=plan_read.TIMEOUT_S):
        t0 = time.monotonic()
        text = inner(args, cwd=cwd, timeout=timeout)
        (folder / f"envelope-{len(calls) + 1}.json").write_text(text)
        calls.append({"seconds": time.monotonic() - t0, **_envelope_facts(text)})
        return text
    started, t0 = datetime.now(UTC), time.monotonic()
    error, scan = None, None
    try:
        plan, meta, warnings = read_mesh_with_claude(src, run, model=model, timeout=timeout,
                                                     render_to=folder / "render.png")
        scan = Scan("mesh", src, src, plan, [], list(warnings), {"doors": [], "windows": []}, meta=meta, model=src)
        save_reading(folder / "reading.json", scan)
    except PlanError as e:
        if isinstance(e, ScanError) and not calls:  # the render refused: nothing was sent, nothing to keep
            raise
        error = str(e)
    if error is not None:
        (folder / "error.txt").write_text(error + "\n")
    info = {"model_file": str(src), "model_sha256": _sha(src), "model_arg": model,
            "render_sha256": _sha(folder / "render.png") if (folder / "render.png").is_file() else None,
            "models": sorted({m for c in calls for m in c.get("models", [])}), "calls": len(calls),
            "seconds": time.monotonic() - t0, "cost_usd": sum(c.get("cost_usd", 0.0) for c in calls),
            "per_call": calls, "read_at": started.isoformat(timespec="seconds"), "ok": error is None}
    (folder / "meta.json").write_text(json.dumps(info, indent=1) + "\n")
    if error is not None:
        raise PlanError(error)
    return scan, info


def _claude_reading(src: Path, folder: Path, runner, model, timeout, offline: bool):
    """(Scan, how, info) for Claude's reading of `src`: a kept one (reading.json or error.txt, then task B3b's), else
    a new one (NotRead when offline). A kept failure is raised again as the PlanError it was."""
    from vesper.lab.plan import PlanError
    from vesper.lab.scan_import import is_reading
    info = json.loads((folder / "meta.json").read_text()) if (folder / "meta.json").is_file() else {}
    if (folder / "reading.json").is_file():
        raw = json.loads((folder / "reading.json").read_text())
        if not is_reading(raw):
            raise PlanError(f"{folder / 'reading.json'}: not a vesper-scan-reading/1 file")
        return _from_reading(raw, src), "saved", info
    if (folder / "error.txt").is_file():
        raise _Kept((folder / "error.txt").read_text().strip(), info)
    b3b = _b3b(folder, src)
    if b3b is not None:
        return b3b[0], "saved (B3b)", b3b[1]
    if offline:
        raise NotRead
    scan, info = _read_with_claude(src, folder, runner, model, timeout)
    return scan, "new", info


class _Kept(Exception):
    """A kept failed reading: its text and meta."""

    def __init__(self, text: str, info: dict):
        super().__init__(text)
        self.info = info


def _detected(src: Path) -> dict | None:
    """A mesh's up axis, metres per unit and ceiling as the readers find them (`scan_mesh.upright_mesh`, the step
    before the rooms), when no plan could be read from it; None when they cannot be found either (a refused scale)."""
    from vesper.lab.plan import PlanError
    from vesper.lab.scan_mesh import upright_mesh
    try:
        meta = upright_mesh(src, None)[3]
    except PlanError:
        return None
    return {"up": str(meta["up"]).lstrip("+"), "scale": float(meta["scale"]),
            "ceiling": meta.get("ceiling_y") is not None}


# --- the run -------------------------------------------------------------------------------------------------------

def evaluate(dataset: Path, readers=READERS, runner=None, model: str | None = None, timeout: float | None = None,
             catalog: dict | None = None, offline: bool = False, only: list[str] | None = None,
             say=lambda text: None) -> dict:
    """Every model of `<dataset>/truth.json` (or those in `only`) imported by each reader in `readers` (a RoomPlan
    file by its own, "roomplan"), scored and, with a catalog, built and its layout checked."""
    from vesper.lab import plan_read
    from vesper.lab.plan import PlanError
    from vesper.lab.scan_import import read_scan
    from vesper.lab.scan_mesh import SUFFIXES as MESH, ScanError
    dataset = Path(dataset)
    truth = load_truth(dataset / "truth.json")
    timeout = plan_read.TIMEOUT_S if timeout is None else timeout
    rows, not_read = [], []
    unknown = sorted(set(only or []) - set(truth["models"]))
    if unknown:
        raise ValueError(f"not in the truth: {', '.join(unknown)}")
    for mid, t in truth["models"].items():
        if only and mid not in only:
            continue
        src = dataset / mid / t["file"]
        mesh = src.suffix.lower() in MESH
        for reader in (readers if mesh else ("roomplan",)):
            t0 = time.monotonic()
            scan, how, info, error = None, "computed", {}, None
            try:
                if reader == "claude":
                    folder = dataset / "_claude" / (t.get("claude_dir") or mid)
                    scan, how, info = _claude_reading(src, folder, runner, model, timeout, offline)
                else:
                    scan = read_scan(src, reader="geometric") if mesh else read_scan(src)
            except NotRead:
                not_read.append(f"{mid}/{reader}")
                say(f"{mid} ({reader}): no kept reading (offline)")
                continue
            except _Kept as e:
                how, info, error = "saved", e.info, str(e)
            except ScanError as e:  # the model refused (a scale VESPER cannot tell): for Claude, before any call
                how, error = ("render refused" if reader == "claude" else how), str(e)
            except PlanError as e:  # Claude's answers were no plan (kept in error.txt)
                how, error = ("new" if reader == "claude" else how), str(e)
            except Exception as e:  # a model trimesh or usd-core cannot read: said, and the run goes on
                error = f"{type(e).__name__}: {e}"
            seconds = time.monotonic() - t0
            facts = reading_facts(scan.plan, scan.meta, scan.warnings) if scan is not None else None
            detected = _detected(src) if scan is None and mesh else None
            try:
                build = import_check(scan, catalog) if scan is not None else None
            except Exception as e:  # noqa: BLE001 - one model's failure is a result, not the end of the run
                build = {"stage": "build", "check_house": False, "problems": [f"{type(e).__name__}: {e}"],
                         "layout_errors": None}
            row = {"model": mid, "kind": t["kind"], "reader": reader, "file": t["file"], "reading": how,
                   "error": error, "stage": build["stage"] if build else "read", "facts": facts,
                   "detected": detected,
                   "warnings": list(scan.warnings) if scan is not None else [], "score": score(facts or detected, t),
                   "build": build, "read_seconds": info.get("seconds", seconds if reader != "claude" else None),
                   "cost_usd": info.get("cost_usd"), "claude_calls": info.get("calls"),
                   "claude_models": info.get("models")}
            rows.append(row)
            s = row["score"]
            say(f"{mid} ({reader}, {how}): stage {row['stage']}, rooms {s['rooms']['pred']}/{s['rooms']['true']}, "
                f"doors {s['doors_inner']['pred']}+{s['doors_outside']['pred']} out, windows {s['windows']['pred']}"
                + (f", layout errors {build['layout_errors']}" if build and build.get('layout_errors') is not None
                   else "") + (f"; FAILED: {error[:160]}" if error else ""))
    return {"protocol": {"dataset": str(dataset), "truth": TRUTH_SCHEMA, "readers": list(readers),
                         "offline": offline, "model": model, "kinds": KINDS},
            "rows": rows, "not_read": not_read, "aggregate": aggregate(rows)}


# --- the aggregate -------------------------------------------------------------------------------------------------

def _share(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(bool(v) for v in values) / len(values) if values else None


def _mean_abs(values) -> float | None:
    values = [abs(v) for v in values if v is not None]
    return statistics.fmean(values) if values else None


def _group(rows: list[dict]) -> dict:
    sc = [r["score"] for r in rows]
    builds = [r.get("build") or {} for r in rows]
    right = [s["types"]["right"] for s in sc if s["types"]["of"]]
    of = [s["types"]["of"] for s in sc if s["types"]["of"]]
    out = {"models": len(rows), "read": sum(r.get("error") is None for r in rows)}
    for k in COUNTS:
        out[f"{k}_ok"] = _share([s[k]["ok"] for s in sc])
        out[f"{k}_abs_error"] = _mean_abs([s[k]["error"] for s in sc])
    out["types_right"] = sum(right) / sum(of) if of else None
    out["types_right_n"] = [sum(right), sum(of)]
    for k in ("entrance_ok", "scale_ok", "up_ok", "ceiling_ok"):
        out[k] = _share([s[k] for s in sc])
    out["check_house"] = _share([b.get("check_house", False) for b in builds]) if rows else None
    out["layout_zero_errors"] = _share([b.get("layout_errors") == 0 for b in builds]) if rows else None
    out["stages"] = dict(Counter(r["stage"] for r in rows))
    return out


def aggregate(rows: list[dict]) -> dict:
    """The rows by "<kind>/<reader>" and by "all/<reader>": the share of counts right and their mean absolute error
    (over the models whose truth knows the count), room types right (pooled), the shares with the entrance, scale,
    up axis and ceiling right, and the shares passing check_house and built with 0 layout errors (out of every
    model)."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(f"{r['kind']}/{r['reader']}", []).append(r)
        groups.setdefault(f"all/{r['reader']}", []).append(r)
    return {k: _group(v) for k, v in sorted(groups.items())}
