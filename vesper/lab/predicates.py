"""Predicates over a run's trace (security-lab spec §4.4, plan W4a1): what a scenario's claims say, checked run by
run, each verdict with the trace records that decided it.

Events:
- an actor's action: `{actor: intruder, opens: hall-porch}` (also `closes`, `uses` a device, `enters` a room);
- `{door: hall-porch, open: true}`: the door itself, whoever moved it;
- `{report: {device, feature, value}}`: a device's report reaching the hub;
- `{action: {device, feature, value}}`: a command the hub sent, by VESPER's automation engine or by Home Assistant
  (the same claim reads the same in a logical and a live run);
- `{alert: {defense, device}}`, `{net: {link, event, device, feature}}`.
A field left out matches anything. Forms: an event (it happens), `before: [A, B]` (A comes first, or B never),
`after: [A, B]` (an A comes after the first B), `gap: [A, B]` with `at_least_s`/`at_most_s` (the first B after the
first A, that long after it), `count` with `at_least`/`at_most`, `never`, `all`, `any`, `not`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from vesper.lab.worldmap import MapError, suggest

ACTOR_VERBS = ("opens", "closes", "enters", "uses")
NET_EVENTS = ("down", "up", "held", "dropped", "duplicated", "reordered", "resent")
FORMS = ("before", "after", "gap", "count", "never", "all", "any", "not")


class PredicateError(ValueError):
    pass


@dataclass
class Verdict:
    holds: bool
    records: list = field(default_factory=list)
    detail: str = ""


# -- evaluation -----------------------------------------------------------------------------------------
def evaluate(p: dict, trace: list[dict]) -> Verdict:
    form = _form(p)
    if form is None:
        hit = _first(p, trace)
        return Verdict(hit is not None, [hit[1]] if hit else [])
    if form == "before":
        a, b = (_first(e, trace) for e in p["before"])
        holds = a is not None and (b is None or (a[1]["t"], a[0]) < (b[1]["t"], b[0]))
        return Verdict(holds, [x[1] for x in (a, b) if x])
    if form == "after":
        b = _first(p["after"][1], trace)
        a = _first(p["after"][0], trace, after=b) if b else None
        return Verdict(a is not None, [x[1] for x in (a, b) if x])
    if form == "gap":
        a = _first(p["gap"][0], trace)
        b = _first(p["gap"][1], trace, after=a) if a else None
        if a is None or b is None:
            return Verdict(False, [x[1] for x in (a, b) if x])
        gap = b[1]["t"] - a[1]["t"]
        holds = p.get("at_least_s", 0) <= gap <= p.get("at_most_s", float("inf"))
        return Verdict(holds, [a[1], b[1]], f"{gap:g} s")
    if form == "count":
        hits = [r for r in trace if _matches(p["count"], r)]
        holds = p.get("at_least", 1) <= len(hits) <= p.get("at_most", len(hits))
        return Verdict(holds, hits, f"{len(hits)}")
    if form == "never":
        hit = _first(p["never"], trace)
        return Verdict(hit is None, [hit[1]] if hit else [])
    if form == "not":
        v = evaluate(p["not"], trace)
        return Verdict(not v.holds, v.records, v.detail)
    verdicts = [evaluate(q, trace) for q in p[form]]
    holds = all(v.holds for v in verdicts) if form == "all" else any(v.holds for v in verdicts)
    return Verdict(holds, [r for v in verdicts for r in v.records])


def _first(event: dict, trace: list[dict], after: tuple | None = None) -> tuple[int, dict] | None:
    start = after[0] + 1 if after else 0
    return next(((k, r) for k, r in enumerate(trace) if k >= start and _matches(event, r)), None)


def _matches(e: dict, r: dict) -> bool:
    kind = r.get("kind")
    if "actor" in e:
        verb = next(v for v in ACTOR_VERBS if v in e)
        if verb == "enters":
            return kind == "world" and r.get("event") == "room" and r.get("actor") == e["actor"] and \
                r.get("room") == e["enters"]
        out = r.get("outcome") or {}
        result = {"opens": "opened", "closes": "closed"}.get(verb)
        return (kind == "input" and r.get("op") == "interact" and r.get("actor") == e["actor"]
                and (r.get("args") or {}).get("target") == e[verb] and out.get("ok") is True
                and (result is None or out.get("result") == result))
    if "door" in e:
        return kind == "world" and r.get("event") == "door" and r.get("id") == e["door"] and \
            e.get("open", r.get("open")) == r.get("open")
    (what, want), = e.items()
    if what == "report":
        return kind == "report" and _fits(want, r)
    if what == "action":
        if kind == "automation":
            return r.get("accepted") is True and _fits(want, r)
        return kind == "input" and r.get("actor") == "hub" and r.get("op") == "command" and \
            (r.get("outcome") or {}).get("accepted") is True and _fits(want, r.get("args") or {})
    return kind == what and _fits(want, r)  # alert, net


def _fits(want: dict, r: dict) -> bool:
    return all(r.get(k) == v for k, v in want.items())


def _form(p: dict) -> str | None:
    return next((f for f in FORMS if f in p), None)


# -- checking against the home ----------------------------------------------------------------------------
def check_predicate(p, home, actors: set, where: str) -> None:
    """Raise PredicateError, naming where, if `p` is not a predicate over this home's devices, doors, rooms,
    links and the scenario's actors."""
    if not isinstance(p, dict):
        raise PredicateError(f"{where}: {p!r} is not a predicate")
    form = _form(p)
    if form is None:
        _check_event(p, home, actors, where)
        return
    extra = set(p) - {form} - {"gap": {"at_least_s", "at_most_s"}, "count": {"at_least", "at_most"}}.get(form, set())
    if extra:
        raise PredicateError(f"{where}: {sorted(extra)[0]!r} does not go with {form}")
    if form in ("before", "after", "gap"):
        pair = p[form]
        if not isinstance(pair, list) or len(pair) != 2:
            raise PredicateError(f"{where}.{form}: two events, the first and the second")
        for k, e in enumerate(pair):
            _check_event(e, home, actors, f"{where}.{form}[{k}]")
        for bound in ("at_least_s", "at_most_s"):
            if bound in p and (isinstance(p[bound], bool) or not isinstance(p[bound], int | float)):
                raise PredicateError(f"{where}.{bound}: {p[bound]!r} is not a number")
    elif form in ("count", "never"):
        _check_event(p[form], home, actors, f"{where}.{form}")
        for bound in ("at_least", "at_most"):
            if bound in p and (isinstance(p[bound], bool) or not isinstance(p[bound], int)):
                raise PredicateError(f"{where}.{bound}: {p[bound]!r} is not a whole number")
    elif form == "not":
        check_predicate(p["not"], home, actors, f"{where}.not")
    else:
        if not isinstance(p[form], list) or not p[form]:
            raise PredicateError(f"{where}.{form}: a list of predicates")
        for k, q in enumerate(p[form]):
            check_predicate(q, home, actors, f"{where}.{form}[{k}]")


def _check_event(e, home, actors: set, where: str) -> None:
    from vesper.lab.scenario import DEFENSES  # the scenario module imports this one
    if not isinstance(e, dict):
        raise PredicateError(f"{where}: {e!r} is not an event")
    if "actor" in e:
        verbs = [v for v in ACTOR_VERBS if v in e]
        if len(verbs) != 1 or set(e) - {"actor", verbs[0]}:
            raise PredicateError(f"{where}: an actor and one of {', '.join(ACTOR_VERBS)}")
        if e["actor"] not in actors:
            raise PredicateError(f"{where}.actor: unknown actor {e['actor']!r}; "
                                 f"{suggest(str(e['actor']), sorted(actors))}")
        verb, target = verbs[0], e[verbs[0]]
        try:
            if verb in ("opens", "closes"):
                home.map.doorway(target)
            elif verb == "enters":
                home.map.room(target)
            else:
                _device(home, target, f"{where}.uses")
        except MapError as err:
            raise PredicateError(f"{where}.{verb}: {err}") from err
        return
    if "door" in e:
        try:
            home.map.doorway(e["door"])
        except MapError as err:
            raise PredicateError(f"{where}.door: {err}") from err
        if set(e) - {"door", "open"} or not isinstance(e.get("open", True), bool):
            raise PredicateError(f"{where}: a door and whether it is open (true or false)")
        return
    if len(e) != 1 or next(iter(e)) not in ("report", "action", "alert", "net"):
        raise PredicateError(f"{where}: {next(iter(e), '')!r} is not a predicate or an event")
    (what, want), = e.items()
    if not isinstance(want, dict):
        raise PredicateError(f"{where}.{what}: {want!r} is not a mapping")
    allowed = {"report": {"device", "feature", "value"}, "action": {"device", "feature", "value"},
               "alert": {"defense", "device", "feature"},
               "net": {"link", "event", "device", "feature", "direction"}}[what]
    if set(want) - allowed:
        raise PredicateError(f"{where}.{what}: {sorted(set(want) - allowed)[0]!r} is not one of its fields")
    device = _device(home, want["device"], f"{where}.{what}.device") if "device" in want else None
    names = {f["name"] for f in device.record["features"]} if device is not None else set()
    if device is not None and "feature" in want and want["feature"] not in names:
        raise PredicateError(f"{where}.{what}.feature: {device.name} has no feature {want['feature']!r}")
    if what == "alert" and "defense" in want and want["defense"] not in DEFENSES:
        raise PredicateError(f"{where}.alert.defense: {want['defense']!r} is not a defense")
    if what == "net":
        links = list(home.network.links) if home.network else []
        if "link" in want and want["link"] not in links:
            raise PredicateError(f"{where}.net.link: unknown link {want['link']!r}; {suggest(want['link'], links)}")
        if "event" in want and want["event"] not in NET_EVENTS:
            raise PredicateError(f"{where}.net.event: {want['event']!r} is not one of {', '.join(NET_EVENTS)}")


def _device(home, name, where: str):
    for d in home.devices:
        if d.name == name:
            return d
    raise PredicateError(f"{where}: unknown device {name!r}; {suggest(str(name), [d.name for d in home.devices])}")
