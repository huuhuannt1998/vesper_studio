"""Closed-loop confirmation: run a sample of the screened automations for real and see whether the screening held.

The screening (the sweep) replays each public automation OPEN loop: its actions are recorded, never executed, against
pre-recorded sensor traces. Here a sample of them runs CLOSED loop: one bound automation alone in a full
`Session(LogicalClock)` of the standard EDT home for RUN_DAYS days from local midnight, where its actions change the
home (a lamp raises the lux it reacts to) and what it did is judged by the oracle's own rules (`oracle.outcome`), so
open and closed loop differ only in the loop.

The engine is permissive, as in the screening: an action it cannot execute (`cover.*`, `notify.*`, a climate action the
thermostat cannot take) is recorded and sends nothing; lights, switches, locks and climate actions on the home's
thermostat are real, so a lamp raises the lux an automation reacts to and a setpoint changes the rooms' temperature.

Sample (`pick`): a random sample of the flagged automations (non-empty `divergences`) and of the unflagged ones, each
`sample` strong, from those that bound. A flagged one runs in the environments where its screening outcome class
differed from the most common class there; an unflagged one runs in two random environments.

Agreement (`disagreements`): the screening outcome and the closed-loop outcome agree when `fired`, `flaps`,
`night_light` and `night_cover` are equal and the first firing's time of day is within one hour (the short way round
the clock; both absent also agrees).

Temperature envelope (`envelope`): over the measured days, the lowest and highest temperature of each room a
`climate.*` target of the automation heats or cools, and whether it went below 10 C or above 30 C. A thermostat in a
hallway serves every indoor room (the model's central heating), so for the EDT home's one hallway thermostat that is
every room, reported per room. The envelope is causal: the same home and environment is also run with no automation
(`baseline_temperatures`, once per environment), and a room counts as a violation only if it breaches with the
automation and not in the baseline; each room reports both min/max and the delta, and the report carries CAVEAT.
"""
from __future__ import annotations

import dataclasses
import random
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from vesper.edt.oracle import outcome as oracle_outcome
from vesper.edt.types import Env, Firing, Outcome

DAY_S = 86400.0
TOD_TOLERANCE_H = 1.0
TOD_BUCKET_H = 3.0         # screening outcomes within a bucket are one class (the oracle's time-spread threshold)
COLD_C, HOT_C = 10.0, 30.0
COMMAND_PARTS_S = 1.0      # the rows of one command (state, brightness, ...) are written within this of its first
RUNS_PER_UNFLAGGED = 2
CAVEAT = ("indoor temperature carries the thermal model's validation error (sunny rooms overheat; "
          "see results-2026-11-04-environment.md)")
FIELDS = ("fired", "flaps", "night_light", "night_cover")


# -- comparing ---------------------------------------------------------------------------------
def _gap(a: float, b: float) -> float:
    d = abs(a - b) % 24.0
    return min(d, 24.0 - d)


def disagreements(open_loop: dict, closed: Outcome) -> list[str]:
    """The outcome fields on which the screening outcome (a dict) and the closed-loop one differ."""
    out = [f for f in FIELDS if open_loop[f] != getattr(closed, f)]
    a, b = open_loop["first_tod_h"], closed.first_tod_h
    if (a is None) != (b is None) or (a is not None and _gap(a, b) > TOD_TOLERANCE_H):
        out.append("first_tod_h")
    return out


def _class(o: dict) -> tuple:
    tod = o["first_tod_h"]
    return (o["fired"], o["flaps"], o["night_light"], o["night_cover"],
            None if tod is None else int(tod // TOD_BUCKET_H))


# -- the sample --------------------------------------------------------------------------------
def pick(sweep_result: dict, sample: int, rng: random.Random) -> list[dict]:
    """The runs to make: [{"id", "env_key", "flagged"}, ...], flagged ones first (sorted ids, then env keys)."""
    bound = {i: a for i, a in sorted(sweep_result["automations"].items()) if a.get("bound") and a.get("outcomes")}
    flagged = [i for i, a in bound.items() if a["divergences"]]
    unflagged = [i for i in bound if i not in set(flagged)]
    chosen = [(i, True) for i in sorted(rng.sample(flagged, min(sample, len(flagged))))]
    chosen += [(i, False) for i in sorted(rng.sample(unflagged, min(sample, len(unflagged))))]
    runs = []
    for i, is_flagged in chosen:
        outcomes = bound[i]["outcomes"]
        keys = sorted(outcomes)
        picked: list[str] = []
        if is_flagged:
            common = Counter(_class(outcomes[k]) for k in keys).most_common(1)[0][0]
            picked = [k for k in keys if _class(outcomes[k]) != common]
        if not picked:  # unflagged, or flagged only by a difference the classes do not see
            picked = sorted(rng.sample(keys, min(RUNS_PER_UNFLAGGED, len(keys))))
        runs += [{"id": i, "env_key": k, "flagged": is_flagged} for k in picked]
    return runs


# -- the temperature envelope ------------------------------------------------------------------
def envelope(temps: dict[str, list[tuple[float, float]]], rooms: list[str],
             measured_days: tuple[int, int] = (1, 2), baseline: dict[str, list[tuple[float, float]]] | None = None
             ) -> dict:
    """Per room: lowest and highest temperature over the measured days, and whether it left [10, 30] C; None where
    the room has no reading (a home with no site has no temperatures).

    With `baseline` (the same home and environment with no automation, `baseline_temperatures`) the envelope is
    causal: `below_10` / `above_30` are true only where the room breaches with the automation and not in the
    baseline; `breached_*` is the breach with the automation whatever the baseline did. The room also reports the
    baseline's min and max (None when the baseline has no reading) and the deltas (automation minus baseline)."""
    lo, hi = measured_days[0] * DAY_S, (measured_days[1] + 1) * DAY_S

    def window(source, room):
        return [c for t, c in source.get(room, ()) if lo <= t < hi]
    out: dict = {}
    for room in rooms:
        v = window(temps, room)
        if not v:
            out[room] = None
            continue
        row = {"min_c": min(v), "max_c": max(v), "below_10": min(v) < COLD_C, "above_30": max(v) > HOT_C}
        if baseline is not None:
            b = window(baseline, room)
            b_min, b_max = (min(b), max(b)) if b else (None, None)
            row |= {"baseline_min_c": b_min, "baseline_max_c": b_max,
                    "delta_min_c": None if b_min is None else round(min(v) - b_min, 2),
                    "delta_max_c": None if b_max is None else round(max(v) - b_max, 2),
                    "breached_below_10": row["below_10"], "breached_above_30": row["above_30"],
                    "below_10": row["below_10"] and not (b_min is not None and b_min < COLD_C),
                    "above_30": row["above_30"] and not (b_max is not None and b_max > HOT_C)}
        out[room] = row
    return out


def _climate_targets(node) -> set[str]:
    """Every `climate.*` entity id an automation's actions name."""
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "entity_id":
                found |= {e.strip() for e in (v if isinstance(v, list) else str(v).split(",")) if
                          isinstance(e, str) and e.strip().startswith("climate.")}
            else:
                found |= _climate_targets(v)
    elif isinstance(node, list):
        for x in node:
            found |= _climate_targets(x)
    return found


def controlled_rooms(bound: dict, home) -> list[str]:
    """The rooms the automation's climate targets heat or cool: the room of each thermostat behind a `climate.*`
    target, and, for a thermostat in a hallway (the model's central heating, vesper.lab.rules.CENTRAL_ROOMS), every
    indoor room."""
    from vesper.lab.automations import entity_ids
    from vesper.lab.rules import CENTRAL_ROOMS
    targets = _climate_targets(bound.get("actions", bound.get("action")))
    ids = entity_ids(home)
    room_of = {d.name: d.room for d in home.devices}
    rooms: set[str] = set()
    for name, feats in ids.items():
        if room_of.get(name) and targets & set(feats.values()):
            label = home.map.room(room_of[name]).label.lower()
            central = any(w in label for w in CENTRAL_ROOMS)
            rooms |= {r.id for r in home.map.rooms if not r.outdoor} if central else {room_of[name]}
    return sorted(rooms)


# -- one closed-loop run ---------------------------------------------------------------------------
def closed_loop(bound: dict | None, env: Env, home_yaml: Path, cache: Path, *, days: int = 3,
                catalog_dir: str = "catalog/vds") -> dict:
    """Live `bound` alone for `days` days from local midnight in `env`, its actions real (lights, switches, locks and
    climate; the rest recorded). Returns {"outcome": Outcome, "firings": [Firing], "refused": [...], "rooms": [room]
    (those its climate targets heat or cool), "temperatures": {room: [(t, C), ...]} for those rooms}. The home is
    copied, never touched. `bound` None: the home with no automation (the baseline), "rooms" every indoor room."""
    from vesper.edt.trace import copy_home, env_site, rung_scaling
    from vesper.lab.automations import Engine
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    from vesper.twins.home import load_catalog
    site = env_site(env)
    with rung_scaling(env), tempfile.TemporaryDirectory(prefix="edt-confirm-") as tmp:
        run_yaml = copy_home(Path(home_yaml), Path(tmp) / "home", site if env.rung >= 3 else None,
                             Path(cache) / "weather", [bound] if bound else None)
        home = load_lab_home(run_yaml, load_catalog(Path(catalog_dir)))
        s = Session(home, clock=LogicalClock())
        s.start(spawn_player=False, residents=False, automations=False)
        s.automations = Engine(s, permissive=True)  # as the screening: record what it cannot execute
        s.automations.start()
        try:
            s.clock.advance(days * DAY_S)
        finally:
            s.stop()
        rooms = controlled_rooms(bound, home) if bound else [r.id for r in home.map.rooms if not r.outdoor]
    rows = [r for r in s.records if r.get("kind") == "automation"]
    firings = firings_from(rows)
    temps: dict[str, list[tuple[float, float]]] = {room: [] for room in rooms}
    for r in s.records:
        if r.get("rule") == "environment":
            for room in rooms:
                if room in r["rooms"]:
                    temps[room].append((r["t"], r["rooms"][room]["temperature"]))
    return {"outcome": oracle_outcome(firings, site, (1, days - 1)), "firings": firings,
            "refused": s.automations.refused, "rooms": rooms, "temperatures": temps, "rows": rows}


_BASELINES: dict[tuple, dict] = {}


def baseline_temperatures(env: Env, home_yaml: Path, cache: Path, days: int = 3,
                          catalog_dir: str = "catalog/vds") -> dict[str, list[tuple[float, float]]]:
    """Every indoor room's temperature over the run with no automation, in `env` (run once per home, environment and
    length, then remembered)."""
    key = (str(home_yaml), str(cache), env, days, catalog_dir)
    if key not in _BASELINES:
        _BASELINES[key] = closed_loop(None, env, Path(home_yaml), Path(cache), days=days,
                                      catalog_dir=catalog_dir)["temperatures"]
    return _BASELINES[key]


def firings_from(rows: list[dict]) -> list[Firing]:
    """The oracle's firings from a live session's `automation` rows. The session writes one row per part of a command
    at delivery (a light turned on with a brightness is a `state` row, then a `brightness` row); the open-loop replay
    counts one firing. Rows of the same automation, service and target within COMMAND_PARTS_S of a firing's first row
    are its parts."""
    out: list[Firing] = []
    last: dict[tuple, Firing] = {}
    for r in sorted((r for r in rows if r.get("kind") == "automation"), key=lambda r: r["t"]):
        key = (r["automation"], r["service"], r["entity"])
        prev = last.get(key)
        if prev is not None and r["t"] - prev.t <= COMMAND_PARTS_S:
            continue
        last[key] = firing = Firing(t=r["t"], automation=r["automation"], service=r["service"], target=r["entity"])
        out.append(firing)
    return out


def _job(args: dict) -> dict:
    env = Env(**args["env"])
    r = closed_loop(args["bound"], env, Path(args["home_yaml"]), Path(args["cache"]), days=args["days"],
                    catalog_dir=args["catalog_dir"])
    base = None
    if r["rooms"]:  # it controls a thermostat: judge the temperatures against the home without it
        base = args.get("baseline") or baseline_temperatures(env, Path(args["home_yaml"]), Path(args["cache"]),
                                                             args["days"], args["catalog_dir"])
    closed = dataclasses.asdict(r["outcome"])
    return {"id": args["id"], "env_key": args["env_key"], "flagged": args["flagged"], "open": args["open"],
            "closed": closed, "fields": disagreements(args["open"], r["outcome"]), "refused": r["refused"],
            "firings": len(r["firings"]),
            "envelope": envelope(r["temperatures"], r["rooms"], (1, args["days"] - 1), baseline=base)}


# -- the report ------------------------------------------------------------------------------
def _rate(agree: int, n: int) -> float | None:
    return None if n == 0 else agree / n


def confirm(sweep_result: dict, work: Path, sample: int = 200, seed: int = 0, *, cache: Path | None = None,
            catalog_dir: str = "catalog/vds", days: int = 3, workers: int = 1) -> dict:
    """Run a sample of the sweep's automations closed loop (see the module doc) and report. `work`: scratch directory
    (the standard home is built in it). `cache`: where recorded weather lives (`<cache>/weather`; default
    `<work>/cache`; never fetched here, a missing day is clear sky). `days`: run length (default RUN_DAYS; the
    measured days are 1..days-1). `workers`: processes (default 1; keep to 2 on the shared Mac)."""
    from vesper.edt.home import build_edt_home
    work = Path(work)
    cache = Path(cache) if cache is not None else work / "cache"
    envs = {e["key"]: {k: e[k] for k in ("city", "date", "north_deg", "rung")} for e in sweep_result["envs"]}
    runs = pick(sweep_result, sample, random.Random(seed))
    home_yaml, _ = build_edt_home(work, catalog_dir)
    jobs = []
    for r in runs:
        a = sweep_result["automations"][r["id"]]
        base = None
        if _climate_targets(a["bound"].get("actions", a["bound"].get("action"))):  # once per environment, shared
            base = baseline_temperatures(Env(**envs[r["env_key"]]), home_yaml, cache, days, catalog_dir)
        jobs.append({**r, "bound": a["bound"], "open": a["outcomes"][r["env_key"]], "env": envs[r["env_key"]],
                     "home_yaml": str(home_yaml), "cache": str(cache), "days": days, "catalog_dir": catalog_dir,
                     "baseline": base})
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_job, jobs))
    else:
        results = [_job(j) for j in jobs]
    return _report(results, seed, sample, days)


def _report(results: list[dict], seed: int, sample: int, days: int) -> dict:
    def stats(rows):
        agree = sum(1 for r in rows if not r["fields"])
        return {"runs": len(rows), "agree": agree, "rate": _rate(agree, len(rows))}
    flagged, unflagged = [r for r in results if r["flagged"]], [r for r in results if not r["flagged"]]
    cells = [(r, room, v) for r in results for room, v in r["envelope"].items() if v is not None]
    return {
        "seed": seed, "sample": sample, "days": days, "runs": len(results),
        "automations": {"flagged": len({r["id"] for r in flagged}), "unflagged": len({r["id"] for r in unflagged})},
        "agreement": {"overall": _rate(sum(1 for r in results if not r["fields"]), len(results)),
                      "flagged": stats(flagged)["rate"], "unflagged": stats(unflagged)["rate"]},
        "by_group": {"flagged": stats(flagged), "unflagged": stats(unflagged)},
        "disagreement_fields": dict(Counter(f for r in results for f in r["fields"])),
        "disagreements": [r for r in results if r["fields"]],
        "refused": [r for r in results if r["refused"]],
        "caveat": CAVEAT,
        "envelope": {"caveat": CAVEAT, "runs_with_climate_targets": len({(r["id"], r["env_key"]) for r, _, _ in cells}),
                     "below_10": sum(1 for _, _, v in cells if v["below_10"]),
                     "above_30": sum(1 for _, _, v in cells if v["above_30"]),
                     "breached_below_10": sum(1 for _, _, v in cells if v.get("breached_below_10", v["below_10"])),
                     "breached_above_30": sum(1 for _, _, v in cells if v.get("breached_above_30", v["above_30"])),
                     "violations": [{"id": r["id"], "env_key": r["env_key"], "room": room, **v}
                                    for r, room, v in cells if v["below_10"] or v["above_30"]]},
        "results": results,
    }
