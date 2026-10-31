"""The scenario file (security-lab spec §4.1, plan W4a1): an attack and its defenses on a home, from a paper,
plain and rerunnable without any model.

`vesper-scenario/1` is YAML next to a home. It is checked against that home before anything runs: every step names a
device, link, doorway, room or actor of the home, never a host, a port or a command (spec §7), and every mistake is
named where it is. Times are the home's clock times (`"22:00:00"`) or seconds after the session's start.

W4a1 builds the `network` vantage (the six effects of the link layer and the relay) and the timeline's actors (the
physical vantage: walking, opening and closing doors, using devices), with the `freshness` and
`physical_consistency` defenses; the other vantages and defenses of §4.2–§4.3 come with plan W4a2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from vesper.lab.home import HomeError, LabHome, load_lab_home
from vesper.lab.linklayer import DIRECTIONS, KINDS, Effect
from vesper.lab.predicates import PredicateError, check_predicate
from vesper.lab.worldmap import MapError, suggest

FORMAT = "vesper-scenario/1"
CONDITIONS = ("baseline", "attack", "attack+defense")
MODES = ("logical", "live")
PLAYER = "player"
VANTAGES = ("network",)
LATER_VANTAGES = ("broker", "device", "hub")  # broker client, compromised device, hub: plan W4a2
DEFENSES = ("freshness", "physical_consistency")
LATER_DEFENSES = ("broker_acl", "automation_hardening", "timeouts")  # plan W4a2
DOES = ("walk", "open", "close", "use")
TOP = {"format", "title", "source", "home", "setup", "actors", "timeline", "attack", "defenses", "expect", "runs",
       "gaps"}
SETUP = {"devices", "network", "automations"}
ACTION_FIELDS = {"at", "actor", "do", "cites"}
NETWORK_FIELDS = {"at", "vantage", "effect", "link", "direction", "match", "hold_s", "keep_connection", "until",
                  "rate_bps", "cites"}
DEFENSE_FIELDS = {"freshness": {"kind", "max_age_s", "action"}, "physical_consistency": {"kind", "within_s"}}
CLAIM_FIELDS = {"claim", "predicate", "conditions", "cites"}


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    name: str
    model: str
    start: str  # a room


@dataclass(frozen=True)
class Action:
    """An actor's action in the world at a time (seconds after the session's start)."""
    at: float
    actor: str
    do: dict
    cites: tuple = ()


@dataclass(frozen=True)
class AttackStep:
    at: float
    vantage: str
    effect: Effect
    cites: tuple = ()


@dataclass(frozen=True)
class Defense:
    kind: str
    max_age_s: float | None = None  # freshness: a report older than this on arrival
    action: str = "alert"  # freshness: alert, or drop the stale report
    within_s: float = 5.0  # physical_consistency: how soon the hub must have heard a world event


@dataclass(frozen=True)
class Claim:
    claim: str
    predicate: dict
    conditions: dict  # condition -> whether the claim is expected to hold
    cites: tuple = ()


@dataclass(frozen=True)
class Runs:
    mode: str
    repeat: int
    conditions: list
    until: float | None = None


@dataclass
class Scenario:
    path: Path
    title: str
    source: dict
    home: LabHome
    actors: dict[str, Actor]
    timeline: list[Action]
    attack: list[AttackStep]
    defenses: list[Defense]
    claims: list[Claim]
    runs: Runs
    gaps: list[dict] = field(default_factory=list)


def load_scenario(path: Path, catalog: dict[str, dict], mode: str | None = None) -> Scenario:
    """`mode`: run it logical or live whatever the file says (`vesper lab scenario run --mode`)."""
    path = Path(path)
    try:
        spec = yaml.safe_load(path.read_text()) or {}
    except OSError as e:
        raise ScenarioError(f"{path}: cannot read it: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ScenarioError(f"{path.name}: not valid YAML: {e}") from e
    _mapping(spec, path.name)
    if spec.get("format") != FORMAT:
        raise ScenarioError(f"format: {spec.get('format')!r} is not {FORMAT}")
    _fields(spec, TOP, "the scenario")
    setup = _mapping(spec.get("setup") or {}, "setup")
    _fields(setup, SETUP, "setup")
    home = _home(path, _text(spec.get("home"), "home"), setup, catalog)
    passages = _mapping(_mapping(spec.get("source") or {}, "source").get("passages") or {}, "source.passages")
    actors = _actors(spec.get("actors") or [], home)
    runs_spec = dict(_mapping(spec.get("runs") or {}, "runs"))
    if mode is not None:
        runs_spec["mode"] = mode
    runs = _runs(runs_spec, home)
    timeline = [_action(k, a, home, actors, passages) for k, a in enumerate(_list(spec.get("timeline") or [],
                                                                                   "timeline"))]
    attack = [_attack(k, a, home, runs, passages) for k, a in enumerate(_list(spec.get("attack") or [], "attack"))]
    defenses = [_defense(k, d, runs) for k, d in enumerate(_list(spec.get("defenses") or [], "defenses"))]
    claims = [_claim(k, c, runs, passages, home, {PLAYER, *actors})
              for k, c in enumerate(_list(spec.get("expect") or [], "expect"))]
    gaps = [_mapping(g, f"gaps[{k}]") for k, g in enumerate(_list(spec.get("gaps") or [], "gaps"))]
    return Scenario(path=path, title=_text(spec.get("title"), "title"), source=dict(spec.get("source") or {}),
                    home=home, actors=actors, timeline=sorted(timeline, key=lambda a: a.at),
                    attack=sorted(attack, key=lambda a: a.at), defenses=defenses, claims=claims, runs=runs,
                    gaps=gaps)


# -- the home -----------------------------------------------------------------------------------------
def _home(path: Path, name: str, setup: dict, catalog: dict) -> LabHome:
    def patch(home_spec: dict) -> None:
        home_spec["devices"] = list(home_spec.get("devices") or []) + list(_list(setup.get("devices") or [],
                                                                                 "setup.devices"))
        if "network" in setup:
            home_spec["network"] = setup["network"]
        if "automations" in setup:
            home_spec["automations"] = str(automations)
    automations = None
    if "automations" in setup:  # beside the scenario, wherever it is run from
        automations = (path.parent / _text(setup["automations"], "setup.automations")).resolve()
        if not automations.is_file():
            raise ScenarioError(f"setup.automations: {automations} does not exist")
    try:
        return load_lab_home(path.parent / name, catalog, patch=patch)
    except HomeError as e:
        raise ScenarioError(f"home: {e}") from e


def _actors(raw, home: LabHome) -> dict[str, Actor]:
    out: dict[str, Actor] = {}
    taken = {d.name for d in home.devices} | {r.name for r in home.residents} | {PLAYER, "hub"}
    for k, a in enumerate(_list(raw, "actors")):
        where = f"actors[{k}]"
        a = _mapping(a, where)
        _fields(a, {"name", "model", "start"}, where)
        name = _text(a.get("name"), f"{where}.name")
        if name in taken or name in out:
            raise ScenarioError(f"{where}.name: {name!r} is already a name in the home")
        room = _text(_mapping(a.get("start") or {}, f"{where}.start").get("room"), f"{where}.start.room")
        _room(home, room, f"{where}.start.room")
        out[name] = Actor(name, _text(a.get("model", "male_1"), f"{where}.model"), room)
    return out


# -- steps --------------------------------------------------------------------------------------------
def _action(k: int, a, home: LabHome, actors: dict, passages: dict) -> Action:
    where = f"timeline[{k}]"
    a = _mapping(a, where)
    _fields(a, ACTION_FIELDS, where)
    actor = _text(a.get("actor"), f"{where}.actor")
    if actor != PLAYER and actor not in actors:
        raise ScenarioError(f"{where}.actor: unknown actor {actor!r}; {suggest(actor, [PLAYER, *actors])}")
    do = _mapping(a.get("do"), f"{where}.do")
    if len(do) != 1 or next(iter(do)) not in DOES:
        raise ScenarioError(f"{where}.do: one of {', '.join(DOES)}")
    (verb, target), = do.items()
    if verb == "walk":
        room = target.get("room") if isinstance(target, dict) else target
        _room(home, _text(room, f"{where}.do.walk"), f"{where}.do.walk")
    elif verb in ("open", "close"):
        try:
            home.map.doorway(_text(target, f"{where}.do.{verb}"))
        except MapError as e:
            raise ScenarioError(f"{where}.do.{verb}: {e}") from e
    else:
        _device(home, _text(target, f"{where}.do.use"), f"{where}.do.use")
    return Action(_at(a.get("at"), f"{where}.at", home), actor, dict(do), _cites(a, where, passages))


def _attack(k: int, a, home: LabHome, runs: Runs, passages: dict) -> AttackStep:
    where = f"attack[{k}]"
    a = _mapping(a, where)
    vantage = a.get("vantage")
    if vantage in LATER_VANTAGES:
        raise ScenarioError(f"{where}.vantage: {vantage!r} comes with plan W4a2")
    if vantage == "physical":
        raise ScenarioError(f"{where}.vantage: a physical step is an actor's action: put it in the timeline")
    if vantage not in VANTAGES:
        raise ScenarioError(f"{where}.vantage: {vantage!r} is not one of {', '.join(VANTAGES + LATER_VANTAGES)}")
    _fields(a, NETWORK_FIELDS, where)
    kind = a.get("effect")
    if kind not in KINDS:
        raise ScenarioError(f"{where}.effect: {kind!r} is not one of {', '.join(KINDS)}")
    if kind == "throttle" and runs.mode != "live":
        raise ScenarioError(f"{where}.effect: a throttle paces a TCP link's bytes: only in a live run")
    network = home.network
    link = _text(a.get("link"), f"{where}.link")
    if network is None or link not in network.links:
        names = list(network.links) if network else []
        raise ScenarioError(f"{where}.link: unknown link {link!r}; {suggest(link, names)}")
    direction = a.get("direction", "both")
    if direction not in DIRECTIONS:
        raise ScenarioError(f"{where}.direction: {direction!r} is not one of {', '.join(DIRECTIONS)}")
    match = _mapping(a.get("match") or {}, f"{where}.match")
    _fields(match, {"device", "feature"}, f"{where}.match")
    device, feature = match.get("device"), match.get("feature")
    if device is not None:
        d = _device(home, _text(device, f"{where}.match.device"), f"{where}.match.device")
        if link not in network.path(device):
            raise ScenarioError(f"{where}.match.device: {device}'s messages do not cross {link} "
                                f"(its path: {', '.join(network.path(device)) or 'none'})")
        if feature is not None and feature not in {f["name"] for f in d.record["features"]}:
            raise ScenarioError(f"{where}.match.feature: {device} has no feature {feature!r}")
    at = _at(a.get("at"), f"{where}.at", home)
    until = _at(a["until"], f"{where}.until", home) if a.get("until") is not None else None
    if until is not None and until <= at:
        raise ScenarioError(f"{where}.until: it ends before it begins")
    effect = Effect(kind=kind, link=link, direction=direction, device=device, feature=feature,
                    hold_s=_number(a.get("hold_s", 0.0), f"{where}.hold_s"),
                    rate_bps=_number(a["rate_bps"], f"{where}.rate_bps", positive=True) if "rate_bps" in a else None,
                    keep_connection=_bool(a.get("keep_connection", True), f"{where}.keep_connection"), until=until)
    return AttackStep(at, vantage, effect, _cites(a, where, passages))


def _defense(k: int, d, runs: Runs) -> Defense:
    where = f"defenses[{k}]"
    d = _mapping(d, where)
    kind = d.get("kind")
    if kind in LATER_DEFENSES:
        raise ScenarioError(f"{where}.kind: {kind!r} comes with plan W4a2")
    if kind not in DEFENSES:
        raise ScenarioError(f"{where}.kind: {kind!r} is not a defense; "
                            f"choose one of {', '.join(DEFENSES + LATER_DEFENSES)}")
    _fields(d, DEFENSE_FIELDS[kind], where)
    if kind == "freshness":
        action = d.get("action", "alert")
        if action not in ("alert", "drop"):
            raise ScenarioError(f"{where}.action: {action!r} is not alert or drop")
        if action == "drop" and runs.mode == "live":
            raise ScenarioError(f"{where}.action: a live run's freshness alerts; dropping a message before Home "
                                "Assistant has it comes with plan W4a2")
        return Defense(kind, max_age_s=_number(d.get("max_age_s"), f"{where}.max_age_s", positive=True), action=action)
    return Defense(kind, within_s=_number(d.get("within_s", 5.0), f"{where}.within_s", positive=True))


def _claim(k: int, c, runs: Runs, passages: dict, home: LabHome, actors: set) -> Claim:
    where = f"expect[{k}]"
    c = _mapping(c, where)
    _fields(c, CLAIM_FIELDS, where)
    conditions = _mapping(c.get("conditions") or {}, f"{where}.conditions")
    missing = [x for x in runs.conditions if x not in conditions]
    if missing:
        raise ScenarioError(f"{where}.conditions: say what is expected under {', '.join(missing)}")
    for x, v in conditions.items():
        if x not in CONDITIONS:
            raise ScenarioError(f"{where}.conditions: {x!r} is not {', '.join(CONDITIONS[:-1])} or {CONDITIONS[-1]}")
        _bool(v, f"{where}.conditions.{x}")
    predicate = _mapping(c.get("predicate"), f"{where}.predicate")
    try:
        check_predicate(predicate, home, actors, f"{where}.predicate")
    except PredicateError as e:
        raise ScenarioError(str(e)) from e
    return Claim(_text(c.get("claim"), f"{where}.claim"), predicate, dict(conditions), _cites(c, where, passages))


def _runs(r: dict, home: LabHome) -> Runs:
    _fields(r, {"mode", "repeat", "conditions", "until"}, "runs")
    mode = r.get("mode", "logical")
    if mode not in MODES:
        raise ScenarioError(f"runs.mode: {mode!r} is not logical or live")
    repeat = r.get("repeat", 1)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ScenarioError(f"runs.repeat: {repeat!r} is not a whole number of runs")
    conditions = _list(r.get("conditions", list(CONDITIONS)), "runs.conditions")
    for x in conditions:
        if x not in CONDITIONS:
            raise ScenarioError(f"runs.conditions: {x!r} is not {', '.join(CONDITIONS[:-1])} or {CONDITIONS[-1]}")
    until = _at(r["until"], "runs.until", home) if r.get("until") is not None else None
    return Runs(mode, repeat, list(conditions), until)


# -- small checks -------------------------------------------------------------------------------------
def _at(value, where: str, home: LabHome) -> float:
    """Seconds after the session's start: a number, or the home's clock time HH:MM:SS (the same day)."""
    if isinstance(value, bool):
        raise ScenarioError(f"{where}: {value!r} is not a time")
    if isinstance(value, int | float):
        if value < 0:
            raise ScenarioError(f"{where}: {value} is before the session's start")
        return float(value)
    try:
        h, m, s = (int(x) for x in str(value).split(":"))
        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
            raise ValueError
    except ValueError:
        raise ScenarioError(f"{where}: {value!r} is not HH:MM:SS") from None
    t = h * 3600 + m * 60 + s - home.start_time
    if t < 0:
        start = int(home.start_time)
        raise ScenarioError(f"{where}: {value} is before the home's start "
                            f"({start // 3600:02d}:{start // 60 % 60:02d}:{start % 60:02d})")
    return float(t)


def _cites(item: dict, where: str, passages: dict) -> tuple:
    cites = _list(item.get("cites") or [], f"{where}.cites")
    for c in cites:
        if c not in passages:
            raise ScenarioError(f"{where}.cites: {c!r} is not a passage of source.passages "
                                f"({', '.join(passages) or 'none quoted'})")
    return tuple(cites)


def _device(home: LabHome, name: str, where: str):
    for d in home.devices:
        if d.name == name:
            return d
    raise ScenarioError(f"{where}: unknown device {name!r}; {suggest(name, [d.name for d in home.devices])}")


def _room(home: LabHome, room: str, where: str) -> None:
    try:
        home.map.room(room)
    except MapError as e:
        raise ScenarioError(f"{where}: {e}") from e


def _fields(item: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(item) - allowed, key=str)
    if unknown:
        what = "a step's field" if where.endswith("]") else "one of its fields"
        raise ScenarioError(f"{where}: {unknown[0]!r} is not {what}")


def _mapping(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ScenarioError(f"{where}: {value!r} is not a mapping")
    return value


def _list(value, where: str) -> list:
    if not isinstance(value, list):
        raise ScenarioError(f"{where}: {value!r} is not a list")
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str):
        raise ScenarioError(f"{where}: {value!r} is not text")
    return value


def _number(value, where: str, positive: bool = False) -> float:
    """A finite number, at least 0 (above 0 when `positive`)."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value or value in (
            float("inf"), float("-inf")):
        raise ScenarioError(f"{where}: {value!r} is not a number")
    if value < 0 or (positive and value == 0):
        raise ScenarioError(f"{where}: {value} must be {'above' if positive else 'at least'} 0")
    return float(value)


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise ScenarioError(f"{where}: {value!r} is not true or false")
    return value
