"""Closed-loop confirmation: run a sample of the screened automations for real and see whether the screening held.

The screening (the sweep) replays each public automation OPEN loop: its actions are recorded, never executed, against
pre-recorded sensor traces. Here a sample of them runs CLOSED loop: one bound automation alone in a full
`Session(LogicalClock)` of the standard EDT home for RUN_DAYS days from local midnight, where its actions change the
home (a lamp raises the lux it reacts to) and what it did is judged by the oracle's own rules (`oracle.outcome`), so
open and closed loop differ only in the loop.

The engine is permissive, as in the screening: an action it cannot execute (`cover.*`, `climate.*`, `notify.*`) is
recorded and sends nothing; lights, switches and locks are real. A closed-loop run therefore captures feedback through
the home's own lamps, switches and locks, not through heating (the engine cannot command a thermostat).

Sample (`pick`): a random sample of the flagged automations (non-empty `divergences`) and of the unflagged ones, each
`sample` strong, from those that bound. A flagged one runs in the environments where its screening outcome class
differed from the most common class there; an unflagged one runs in two random environments.

Agreement (`disagreements`): the screening outcome and the closed-loop outcome agree when `fired`, `flaps`,
`night_light` and `night_cover` are equal and the first firing's time of day is within one hour (the short way round
the clock; both absent also agrees).

Temperature envelope (`envelope`): over the measured days, the lowest and highest temperature of each room a
`climate.*` target of the automation sits in, and whether it went below 10 C or above 30 C. The target's room is the
room of the device behind the entity (the hallway thermostat's room, whichever room its heat serves).
"""
from __future__ import annotations

import dataclasses
import random
import shutil
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml

from vesper.edt.oracle import outcome as oracle_outcome
from vesper.edt.types import Env, Firing, Outcome

DAY_S = 86400.0
TOD_TOLERANCE_H = 1.0
TOD_BUCKET_H = 3.0         # screening outcomes within a bucket are one class (the oracle's time-spread threshold)
COLD_C, HOT_C = 10.0, 30.0
RUNS_PER_UNFLAGGED = 2
RUNG3_DAYLIGHT_SCALE = 1 / 14.8  # as vesper.edt.trace: what validation D measured against a real lux sensor
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
             measured_days: tuple[int, int] = (1, 2)) -> dict:
    """Per room: lowest and highest temperature over the measured days, and whether it left [10, 30] C; None where
    the room has no reading (a home with no site has no temperatures)."""
    lo, hi = measured_days[0] * DAY_S, (measured_days[1] + 1) * DAY_S
    out: dict = {}
    for room in rooms:
        v = [c for t, c in temps.get(room, ()) if lo <= t < hi]
        out[room] = None if not v else {"min_c": min(v), "max_c": max(v), "below_10": min(v) < COLD_C,
                                        "above_30": max(v) > HOT_C}
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
    """The rooms of the devices behind the climate entities the automation's actions target."""
    from vesper.lab.automations import entity_ids
    targets = _climate_targets(bound.get("actions", bound.get("action")))
    ids = entity_ids(home)
    room_of = {d.name: d.room for d in home.devices}
    return sorted({room_of[name] for name, feats in ids.items() for e in feats.values()
                   if e in targets and room_of.get(name)})


# -- one closed-loop run ---------------------------------------------------------------------------
def _site(env: Env):
    from vesper.env.site import parse_site
    if env.city is None or env.date is None:
        return None
    return parse_site({"city": env.city, "date": env.date, "north_deg": env.north_deg or 0.0})


def _copy_home(home_yaml: Path, out: Path, site, weather_cache: Path, bound: dict) -> Path:
    """A copy of the EDT home for one run (as vesper.edt.trace makes it): the site (or none), midnight start, no
    residents, and the one bound automation as the home's automations."""
    shutil.copytree(home_yaml.parent, out, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    data = yaml.safe_load((out / "home.yaml").read_text())
    data["start_time"] = "00:00"
    data.pop("residents", None)
    data.pop("site", None)
    if site is not None:
        data["site"] = {k: v for k, v in site.as_dict().items() if k != "name"} | {"city": site.name}
    data["automations"] = "automations.yaml"
    (out / "automations.yaml").write_text(yaml.safe_dump([bound], sort_keys=False, allow_unicode=True))
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    if site is not None and weather_cache.is_dir():
        shutil.copytree(weather_cache, out / "weather", dirs_exist_ok=True)
    return out / "home.yaml"


def closed_loop(bound: dict, env: Env, home_yaml: Path, cache: Path, *, days: int = 3,
                catalog_dir: str = "catalog/vds") -> dict:
    """Live `bound` alone for `days` days from local midnight in `env`, its actions real. Returns {"outcome": Outcome,
    "firings": [Firing], "refused": [...], "rooms": [room, ...] (where its climate targets sit), "temperatures":
    {room: [(t, C), ...]} for those rooms}. The home is copied, never touched."""
    from vesper.env import indoor
    from vesper.lab.automations import Engine
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    from vesper.twins.home import load_catalog
    site = _site(env)
    old_scale = indoor.DAYLIGHT_SCALE
    if env.rung == 3:
        indoor.DAYLIGHT_SCALE = RUNG3_DAYLIGHT_SCALE
    try:
        with tempfile.TemporaryDirectory(prefix="edt-confirm-") as tmp:
            run_yaml = _copy_home(Path(home_yaml), Path(tmp) / "home", site if env.rung >= 3 else None,
                                  Path(cache) / "weather", bound)
            home = load_lab_home(run_yaml, load_catalog(Path(catalog_dir)))
            s = Session(home, clock=LogicalClock())
            s.start(spawn_player=False, residents=False, automations=False)
            s.automations = Engine(s, permissive=True)  # as the screening: record what it cannot execute
            s.automations.start()
            try:
                s.clock.advance(days * DAY_S)
            finally:
                s.stop()
            rooms = controlled_rooms(bound, home)
    finally:
        indoor.DAYLIGHT_SCALE = old_scale
    firings = [Firing(t=r["t"], automation=r["automation"], service=r["service"], target=r["entity"])
               for r in s.records if r.get("kind") == "automation"]
    temps: dict[str, list[tuple[float, float]]] = {room: [] for room in rooms}
    for r in s.records:
        if r.get("rule") == "environment":
            for room in rooms:
                if room in r["rooms"]:
                    temps[room].append((r["t"], r["rooms"][room]["temperature"]))
    return {"outcome": oracle_outcome(firings, site, (1, days - 1)), "firings": firings,
            "refused": s.automations.refused, "rooms": rooms, "temperatures": temps}


def _job(args: dict) -> dict:
    env = Env(**args["env"])
    r = closed_loop(args["bound"], env, Path(args["home_yaml"]), Path(args["cache"]), days=args["days"],
                    catalog_dir=args["catalog_dir"])
    closed = dataclasses.asdict(r["outcome"])
    return {"id": args["id"], "env_key": args["env_key"], "flagged": args["flagged"], "open": args["open"],
            "closed": closed, "fields": disagreements(args["open"], r["outcome"]), "refused": r["refused"],
            "firings": len(r["firings"]),
            "envelope": envelope(r["temperatures"], r["rooms"], (1, args["days"] - 1))}


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
        jobs.append({**r, "bound": a["bound"], "open": a["outcomes"][r["env_key"]], "env": envs[r["env_key"]],
                     "home_yaml": str(home_yaml), "cache": str(cache), "days": days, "catalog_dir": catalog_dir})
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
        "envelope": {"runs_with_climate_targets": len({(r["id"], r["env_key"]) for r, _, _ in cells}),
                     "below_10": sum(1 for _, _, v in cells if v["below_10"]),
                     "above_30": sum(1 for _, _, v in cells if v["above_30"]),
                     "violations": [{"id": r["id"], "env_key": r["env_key"], "room": room, **v}
                                    for r, room, v in cells if v["below_10"] or v["above_30"]]},
        "results": results,
    }
