"""The sweep: every corpus automation, bound to the standard EDT home, replayed in every environment, judged by the
oracle (docs/experiments/edt-protocol.md).

`sweep(corpus, work, envs, rung, workers, limit)` reads the de-duplicated corpus, binds each automation (variant 0:
one product per sensor role), and runs a pool of `workers` processes over the environments: each records its
environment's trace into `<work>/cache` if it is not there yet and replays every bound automation against it, 500 per
engine. The result is the shared sweep shape (see `sweep`'s docstring), the same whatever the number of workers.

Unbindable automations stay, with `bound: None` and the binder's reason. An environment in which the engine refuses an
automation (a sun trigger with no site, at rung 1) gives it no outcome there; the refusal is recorded. An envelope kind
(flaps, night_light, night_cover) found in every environment where the automation has an outcome is `static`, not an
environmental divergence; "flagged" means environmental divergences.
"""
from __future__ import annotations

import dataclasses
import gzip
import json
import multiprocessing
import re
import time
from collections import Counter
from pathlib import Path

from vesper.edt.bind import ENTITY, Unbindable, bind
from vesper.edt.corpus import env_kinds, iter_automations
from vesper.edt.oracle import divergences, outcome
from vesper.edt.types import Env, Outcome

BATCH = 500  # automations per engine
ENVELOPE = ("flaps", "night_light", "night_cover")
NC_TRIGGERS = {"state", "time", "time_pattern"}
NC_CONDITIONS = {"state", "time"}
NC_LOGIC = {"and", "or", "not"}
NC_ROLES = {"motion", "door", "light", "switch", "lock"}
PROTOCOL = "docs/experiments/edt-protocol.md"


# ---- the negative control ------------------------------------------------------------------------------------------

def _listed(steps) -> list:
    if steps is None:
        return []
    return steps if isinstance(steps, list) else [steps]


def _first(raw: dict, *keys: str):
    return next((raw[k] for k in keys if raw.get(k) is not None), None)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in ("id", "alias"):
                yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _condition_ok(step) -> bool:
    if not isinstance(step, dict):  # a shorthand template condition, or something else
        return False
    kind = str(step.get("condition", ""))
    if kind in NC_LOGIC:
        return all(_condition_ok(s) for s in _listed(step.get("conditions")))
    return kind in NC_CONDITIONS


def is_negative_control(raw: dict, bound: dict, entity_roles: dict[str, str]) -> bool:
    """Whether an automation reads no environment: no environment kinds as written, only state / time / time_pattern
    triggers and state / time conditions (and/or/not of them), at least one trigger, and every entity its bound
    triggers and conditions name is the home's motion, door, light, switch or lock (`entity_roles`: id -> role)."""
    if not isinstance(raw, dict) or not isinstance(bound, dict) or env_kinds(raw):
        return False
    triggers = _listed(_first(bound, "trigger", "triggers"))
    conditions = _listed(_first(bound, "condition", "conditions"))
    if not triggers:
        return False
    for t in triggers:
        if not isinstance(t, dict) or str(t.get("platform") or t.get("trigger") or "") not in NC_TRIGGERS:
            return False
    if not all(_condition_ok(c) for c in conditions):
        return False
    for text in _strings([triggers, conditions]):
        if "{{" in text or "{%" in text:
            return False
        for part in (p.strip() for p in text.split(",")):
            if ENTITY.match(part) and entity_roles.get(part) not in NC_ROLES:
                return False
    return True


# ---- one environment (a pool worker) -------------------------------------------------------------------------------

_W: dict = {}


def _init(home_yaml: str, cache: str, bound: dict, catalog_dir: str, batch: int) -> None:
    _W.update(home_yaml=Path(home_yaml), cache=Path(cache), bound=bound, catalog_dir=catalog_dir, batch=batch)


def _site_of(trace: Path):
    from vesper.env.site import parse_site
    with gzip.open(trace, "rt") as f:
        return parse_site(json.loads(f.readline()).get("site"))


def _replay(trace: Path, ids: list[str], out: dict) -> None:
    """Replay `ids` in one engine; if the engine fails, halve the batch until the failing automation is alone, and
    record it as refused with the error."""
    from vesper.edt.replay import replay_detailed
    try:
        r = replay_detailed(_W["home_yaml"], trace, {i: _W["bound"][i] for i in ids}, catalog_dir=_W["catalog_dir"])
    except Exception as e:  # noqa: BLE001 - one bad automation must not sink its batch
        if len(ids) == 1:
            out["refused"][ids[0]] = f"replay error: {type(e).__name__}: {str(e)[:200]}"
            return
        mid = len(ids) // 2
        _replay(trace, ids[:mid], out)
        _replay(trace, ids[mid:], out)
        return
    out["refused"].update(r.refused)
    for i in ids:
        if i not in r.refused:
            out["firings"][i] = r.firings.get(i, [])
    for name, approx in r.approximations.items():
        out["approximations"][name] = sorted(set(approx))


def _run_env(env: Env) -> tuple[str, dict, dict, dict, float]:
    """(env key, {id: outcome dict}, {id: refusal}, {id: approximations}, seconds) for one environment."""
    from vesper.edt.trace import record_trace
    t0 = time.monotonic()
    trace = record_trace(_W["home_yaml"], env, _W["cache"], fetch=False, catalog_dir=_W["catalog_dir"])
    site = _site_of(trace)
    ids = sorted(_W["bound"])
    out = {"firings": {}, "refused": {}, "approximations": {}}
    for k in range(0, len(ids), _W["batch"]):
        _replay(trace, ids[k:k + _W["batch"]], out)
    outcomes = {i: dataclasses.asdict(outcome(f, site)) for i, f in out["firings"].items()}
    return env.key, outcomes, out["refused"], out["approximations"], time.monotonic() - t0


# ---- the sweep ------------------------------------------------------------------------------------------------------

def _jsonable(x):
    """A JSON-safe copy (YAML timestamps and the like as strings, non-string keys stringified)."""
    return json.loads(json.dumps(x, default=str))


_ENTITY_PREFIX = re.compile(r"^[a-z_]+\.[a-z0-9_]+: ")
_FOR_ENTITY = re.compile(r" \(for [^)]*\)$")
_QUOTED = re.compile(r"""(['"])(?:[a-z_]+\.[^'"]+|![^'"]*)\1""")  # a quoted entity id or YAML tag value
_NOT_IN_HOME = re.compile(r"^entity .+ is not one of this home's$")


def reason_class(reason: str) -> str:
    """A reason without the entity it names (the unsupported feature it names stays), for counting reasons."""
    if _NOT_IN_HOME.match(reason):
        return "entity <...> is not one of this home's"
    return _QUOTED.sub("<...>", _FOR_ENTITY.sub("", _ENTITY_PREFIX.sub("", reason)))


def _prefetch_weather(envs: list[Env], cache: Path) -> None:
    """Recorded weather for every city and date the sweep needs, fetched once, before the workers start."""
    from vesper.edt.trace import _fetch_weather, _site
    seen = set()
    for env in envs:
        if env.rung >= 3 and env.city and env.date and (env.city, env.date) not in seen:
            seen.add((env.city, env.date))
            _fetch_weather(_site(env), cache / "weather", None)


def sweep(corpus, work, envs=None, rung: int = 4, workers: int = 2, limit: int | None = None, *, fetch: bool = True,
          catalog_dir: str = "catalog/vds", batch: int = BATCH, log=None) -> dict:
    """Sweep the corpus (`corpus/raw`, or `corpus` itself) across `envs` (default: the 48 of
    `vesper.edt.trace.ENVIRONMENTS`), every one at `rung`. `work`: the EDT home and the trace cache (`work/cache`;
    one work directory per catalog version). `limit`: the first N de-duplicated automations. `fetch`: fetch recorded
    weather (False: what is cached, else the assumed clear sky). `log`: a callable given progress lines.

    Returns {"rung", "envs": [{city, date, north_deg, rung, key}], "automations": {id: {repo, hash, kinds, raw,
    bound, unbindable, outcomes: {env key: Outcome fields}, refused: {env key: reason}, approximations, divergences,
    static, negative_control}}, "negative_control_rate", "negative_control": {n, flagged}, "yield": {parsed, bound,
    run}, "flagged": {n, by_kind}, "static": {n, by_kind}, "unbindable_reasons", "refused_reasons", "protocol"}."""
    from vesper.edt.home import build_edt_home
    from vesper.edt.trace import ENVIRONMENTS
    log = log or (lambda _msg: None)
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cache = work / "cache"
    envs = [dataclasses.replace(e, rung=rung) for e in (ENVIRONMENTS if envs is None else envs)]
    raw_dir = Path(corpus) / "raw" if (Path(corpus) / "raw").is_dir() else Path(corpus)
    home_yaml, roles = build_edt_home(work, catalog_dir)
    entity_roles = {eid: role for (role, _room, _v), eid in roles.items()}

    autos: dict[str, dict] = {}
    bound_by_id: dict[str, dict] = {}
    for a in iter_automations(raw_dir):
        if limit is not None and len(autos) >= limit:
            break
        raw = a.raw
        try:
            b = bind(raw, roles, 0) if isinstance(raw, dict) else Unbindable("not a mapping")
        except Exception as e:  # noqa: BLE001 - a binder crash is reported as the automation's reason
            b = Unbindable(f"binder error: {type(e).__name__}")
        if not isinstance(b, (dict, Unbindable)):
            b = Unbindable("not a mapping")
        entry = {"repo": a.repo, "hash": a.hash, "kinds": sorted(env_kinds(raw)) if isinstance(raw, dict) else [],
                 "raw": _jsonable(raw), "bound": None, "unbindable": None, "outcomes": {}, "refused": {},
                 "approximations": [], "divergences": [], "static": [], "negative_control": False}
        if isinstance(b, Unbindable):
            entry["unbindable"] = b.reason
        else:
            entry["bound"] = _jsonable(b)
            entry["negative_control"] = is_negative_control(raw, b, entity_roles)
            bound_by_id[a.hash] = b
        autos[a.hash] = entry
    log(f"corpus: {len(autos)} automations, {len(bound_by_id)} bound; {len(envs)} environments, rung {rung}")

    if fetch:
        _prefetch_weather(envs, cache)
    args = (str(home_yaml), str(cache), bound_by_id, catalog_dir, batch)
    per_env: dict[str, tuple] = {}
    t0 = time.monotonic()
    if workers <= 1 or len(envs) <= 1:
        _init(*args)
        results = map(_run_env, envs)
        for res in results:
            per_env[res[0]] = res
            log(f"{len(per_env)}/{len(envs)} {res[0]} {res[4]:.1f} s")
    else:
        with multiprocessing.get_context("spawn").Pool(min(workers, len(envs)), _init, args) as pool:
            for res in pool.imap_unordered(_run_env, envs):
                per_env[res[0]] = res
                log(f"{len(per_env)}/{len(envs)} {res[0]} {res[4]:.1f} s")
    log(f"replayed {len(bound_by_id)} x {len(envs)} in {time.monotonic() - t0:.1f} s")

    for env in envs:  # the given order, whatever order the workers finished in
        _, outcomes, refused, approximations, _ = per_env[env.key]
        for aid, o in outcomes.items():
            autos[aid]["outcomes"][env.key] = o
        for aid, why in refused.items():
            if aid in autos:
                autos[aid]["refused"][env.key] = why
        for aid, approx in approximations.items():
            if aid in autos:
                autos[aid]["approximations"] = sorted(set(autos[aid]["approximations"]) | set(approx))

    for entry in autos.values():
        outs = {k: Outcome(**o) for k, o in entry["outcomes"].items()}
        if not outs:
            continue
        kinds = divergences(outs)
        static = {k for k in ENVELOPE if k in kinds and all(getattr(o, k) for o in outs.values())}
        entry["divergences"] = sorted(kinds - static)
        entry["static"] = sorted(static)
    return _summarise(rung, envs, dict(sorted(autos.items())))


def _summarise(rung: int, envs: list[Env], autos: dict) -> dict:
    ran = [a for a in autos.values() if a["outcomes"]]
    flagged = [a for a in ran if a["divergences"]]
    static = [a for a in ran if a["static"]]
    nc = [a for a in ran if a["negative_control"]]
    nc_flagged = sum(1 for a in nc if a["divergences"])
    refused = Counter(reason_class(r) for a in autos.values() for r in set(a["refused"].values()))
    return {
        "rung": rung,
        "envs": [{**dataclasses.asdict(e), "key": e.key} for e in envs],
        "automations": autos,
        "negative_control_rate": (nc_flagged / len(nc)) if nc else None,
        "negative_control": {"n": len(nc), "flagged": nc_flagged},
        "yield": {"parsed": len(autos), "bound": sum(1 for a in autos.values() if a["bound"] is not None),
                  "run": len(ran)},
        "flagged": {"n": len(flagged), "by_kind": dict(sorted(Counter(k for a in flagged
                                                                      for k in a["divergences"]).items()))},
        "static": {"n": len(static), "by_kind": dict(sorted(Counter(k for a in static for k in a["static"]).items()))},
        "unbindable_reasons": dict(sorted(Counter(reason_class(a["unbindable"]) for a in autos.values()
                                                  if a["unbindable"]).items())),
        "refused_reasons": dict(sorted(refused.items())),
        "protocol": PROTOCOL,
    }
