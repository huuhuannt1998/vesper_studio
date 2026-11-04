"""A sandboxed subset of Home Assistant's templates, for VESPER's automation engine (vesper.lab.automations).

Home Assistant renders Jinja2 in an immutable sandbox with its own functions, filters and tests. This module renders
the common subset with Home Assistant's semantics, and refuses at load time, by name, every function, filter, test,
method or statement outside it (`TemplateRefused`), so an automation either runs as Home Assistant would run it or
is refused whole:
- states: `states('x')`, `states.domain.object(.state/.attributes/.last_changed/...)`, iterating `states` or
  `states.domain`, `is_state`, `state_attr`, `is_state_attr`, `has_value`; an entity that does not exist is
  'unknown' (states), None (state_attr), as in Home Assistant;
- time: `now()`, `utcnow()`, `today_at()`, `as_timestamp`, `as_datetime`, `as_local`, `timedelta`, `strptime`,
  `timestamp_custom/local/utc` (the home's site gives the date and time zone);
- Home Assistant's forgiving filters: `float`, `int`, `round`, `bool` raise without a default (a template condition
  is then false, a template trigger does not fire), `is_number`, `iif`, `min`, `max`, `regex_*`, `contains`,
  the math functions (`sin` ... `sqrt`, `log`, `pi`, `e`);
- Jinja's own safe filters and tests (`default`, `lower`, `selectattr`, `map`, `join`, ... `defined`, `in`, ...).
A render records what it read (entities, domains, all states, the time), as Home Assistant's RenderInfo does, so a
template trigger re-renders when one of them changes (all states at most once a minute, a domain once a second, the
time at the start of each minute).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from ast import literal_eval
from dataclasses import dataclass, field

ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_IS_NUMERIC = re.compile(r"^[+-]?(?!0\d)\d*(?:\.\d*)?$")
STATE_FUNCTIONS = ("states", "is_state", "state_attr", "is_state_attr", "has_value")  # first argument: an entity
TIME_NAMES = ("now", "utcnow", "today_at", "as_local", "timestamp_local", "timestamp_custom")
# Jinja's own filters and tests that are safe and behave the same in Home Assistant
JINJA_FILTERS = {"abs", "attr", "batch", "capitalize", "center", "count", "d", "default", "dictsort", "e", "escape",
                 "first", "format", "groupby", "indent", "items", "join", "last", "length", "list", "lower", "map",
                 "reject", "rejectattr", "replace", "reverse", "safe", "select", "selectattr", "slice", "sort",
                 "string", "sum", "title", "trim", "truncate", "unique", "upper", "wordcount", "tojson", "dict"}
JINJA_TESTS = {"boolean", "callable", "defined", "divisibleby", "eq", "equalto", "escaped", "even", "false", "float",
               "ge", "greaterthan", "gt", "in", "integer", "iterable", "le", "lessthan", "lower", "lt", "mapping",
               "ne", "none", "number", "odd", "sameas", "sequence", "string", "true", "undefined", "upper", "filter",
               "test"}
METHODS = {"strftime", "lower", "upper", "split", "rsplit", "replace", "startswith", "endswith", "strip", "lstrip",
           "rstrip", "title", "capitalize", "items", "keys", "values", "get", "timestamp", "isoweekday", "weekday",
           "total_seconds", "date", "time", "isoformat", "astimezone", "replace_tz", "count", "index", "find",
           "join", "format", "zfill", "isdigit", "isnumeric", "upper", "casefold"}


class TemplateRefused(ValueError):
    """A template outside the subset; the message says what."""


class TemplateFailed(Exception):
    """A render that fails as Home Assistant's does (a filter without a default, an undefined attribute)."""


_SENTINEL = object()


def is_template(value) -> bool:
    return isinstance(value, str) and any(m in value for m in ("{{", "{%", "{#"))


def result_as_boolean(value) -> bool:
    """Home Assistant's template.result_as_boolean (cv.boolean, anything else False)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.lower().strip()
        return v in ("1", "true", "yes", "on", "enable")
    if isinstance(value, int | float):
        return value != 0
    return False


def parse_result(text: str):
    """Home Assistant's Template._parse_result: a rendered string as the Python literal it spells, if any."""
    try:
        result = literal_eval(text)
    except (ValueError, TypeError, SyntaxError, MemoryError):
        return text
    if isinstance(result, str | complex):
        return text
    if isinstance(result, int | float) and not isinstance(result, bool) and _IS_NUMERIC.match(text) is None:
        return text
    return result


@dataclass
class RenderInfo:
    """What a render read (Home Assistant's RenderInfo)."""
    entities: set = field(default_factory=set)
    domains: set = field(default_factory=set)
    all_states: bool = False
    time: bool = False


class TemplateState:
    """Home Assistant's TemplateState: an entity's state as templates see it."""

    def __init__(self, view, entity_id: str):
        self._view, self.entity_id = view, entity_id

    def _read(self):
        self._view.info.entities.add(self.entity_id)
        return self._view.engine_state(self.entity_id)

    @property
    def state(self):
        return self._read()[0]

    @property
    def attributes(self):
        return dict(self._read()[1])

    @property
    def domain(self):
        return self.entity_id.split(".")[0]

    @property
    def object_id(self):
        return self.entity_id.split(".")[1]

    @property
    def name(self):
        return self._read()[1].get("friendly_name") or self.object_id.replace("_", " ")

    @property
    def last_changed(self):
        return self._view.datetime_of(self._read()[2])

    last_updated = last_reported = last_changed

    def __repr__(self):
        return f"<template TemplateState({self.entity_id}={self.state})>"

    def __str__(self):
        return self.state


class FixedState:
    """A state captured when a trigger fired (`trigger.from_state`, `trigger.to_state`)."""

    def __init__(self, entity_id: str, state, attributes: dict, changed: float, datetime_of):
        self.entity_id, self.state, self.attributes = entity_id, state, dict(attributes or {})
        self._changed, self._datetime_of = changed, datetime_of

    @property
    def domain(self):
        return self.entity_id.split(".")[0]

    @property
    def object_id(self):
        return self.entity_id.split(".")[1]

    @property
    def name(self):
        return self.attributes.get("friendly_name") or self.object_id.replace("_", " ")

    @property
    def last_changed(self):
        return self._datetime_of(self._changed)

    last_updated = last_reported = last_changed


class DomainStates:
    def __init__(self, view, domain: str):
        self._view, self._domain = view, domain

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        entity_id = f"{self._domain}.{name}"
        self._view.info.entities.add(entity_id)
        return TemplateState(self._view, entity_id) if entity_id in self._view.entity_ids() else None

    def __iter__(self):
        self._view.info.domains.add(self._domain)
        return iter([TemplateState(self._view, e) for e in sorted(self._view.entity_ids())
                     if e.split(".")[0] == self._domain])

    def __len__(self):
        return len(list(iter(self)))


class AllStates:
    def __init__(self, view):
        self._view = view

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return DomainStates(self._view, name)

    def __iter__(self):
        self._view.info.all_states = True
        return iter([TemplateState(self._view, e) for e in sorted(self._view.entity_ids())])

    def __len__(self):
        return len(list(iter(self)))

    def __call__(self, entity_id, rounded=False, with_unit=False):
        return self._view.state_of(entity_id)


class View:
    """What templates read of the engine: its entities' states, the clock and the site (for dates)."""

    def __init__(self, engine):
        self.engine = engine
        self.info = RenderInfo()

    def entity_ids(self):
        return self.engine._state.keys()  # noqa: SLF001

    def engine_state(self, entity_id):
        e = self.engine
        return (e._state.get(entity_id, "unknown"), e._attrs.get(entity_id, {}),  # noqa: SLF001
                e._changed.get(entity_id, e.session.clock.now()))  # noqa: SLF001

    def state_of(self, entity_id):
        self.info.entities.add(entity_id)
        return self.engine._state.get(entity_id, "unknown")  # noqa: SLF001

    def attrs_of(self, entity_id):
        self.info.entities.add(entity_id)
        if entity_id not in self.engine._state:  # noqa: SLF001
            return None
        return self.engine._attrs.get(entity_id, {})  # noqa: SLF001

    def _site(self):
        site = self.engine.site
        if site is None:
            raise TemplateFailed("the home has no site, so it has no date")
        return site

    def datetime_of(self, t: float) -> dt.datetime:
        return self._site().utc(t - self.engine._midnight)  # noqa: SLF001

    def now(self) -> dt.datetime:
        self.info.time = True
        return self.datetime_of(self.engine.session.clock.now()).astimezone(self._site().zone)

    def zone(self):
        return self._site().zone


def _no_default(name, value):
    raise TemplateFailed(f"{name} got invalid input {value!r} and no default was specified")


class Templates:
    """Compiles and renders templates over one engine."""

    def __init__(self, engine):
        from jinja2.sandbox import ImmutableSandboxedEnvironment

        templates = self

        class Environment(ImmutableSandboxedEnvironment):
            def is_safe_attribute(self, obj, attr, value):
                if isinstance(obj, TemplateState | FixedState | DomainStates | AllStates):
                    return not attr.startswith("_")
                return super().is_safe_attribute(obj, attr, value)

        self.engine = engine
        self.view = View(engine)
        self.env = Environment()
        self.env.globals.clear()
        self.env.globals.update(range=range, dict=dict)
        from jinja2.utils import Namespace
        self.env.globals["namespace"] = Namespace
        self.env.filters = {k: v for k, v in self.env.filters.items() if k in JINJA_FILTERS}
        self.env.tests = {k: v for k, v in self.env.tests.items() if k in JINJA_TESTS}
        g, f, tst = self.env.globals, self.env.filters, self.env.tests
        v = self.view
        g.update(states=AllStates(v), is_state=templates._is_state, state_attr=templates._state_attr,
                 is_state_attr=templates._is_state_attr, has_value=templates._has_value, now=v.now,
                 utcnow=lambda: v.now().astimezone(dt.UTC), today_at=templates._today_at,
                 as_timestamp=_as_timestamp, as_datetime=templates._as_datetime, as_local=templates._as_local,
                 timedelta=dt.timedelta, strptime=_strptime, float=_float, int=_int, bool=_bool, iif=_iif,
                 min=_min, max=_max, is_number=_is_number, pi=math.pi, e=math.e, tau=math.tau, inf=math.inf,
                 **MATH)
        f.update(MATH)
        f.update(float=_float, int=_int, round=_round, bool=_bool, iif=_iif, is_number=_is_number, min=_min,
                 max=_max, as_timestamp=_as_timestamp, as_datetime=templates._as_datetime,
                 as_local=templates._as_local, timestamp_custom=templates._timestamp_custom,
                 timestamp_local=templates._timestamp_local, timestamp_utc=_timestamp_utc,
                 regex_match=_regex_match, regex_search=_regex_search, regex_replace=_regex_replace,
                 regex_findall=_regex_findall, contains=lambda seq, value: value in seq,
                 is_state=templates._is_state, state_attr=templates._state_attr,
                 is_state_attr=templates._is_state_attr, has_value=templates._has_value)
        tst.update(is_number=_is_number, match=_regex_match, search=_regex_search,
                   contains=lambda seq, value: value in seq, list=lambda v: isinstance(v, list),
                   datetime=lambda v: isinstance(v, dt.datetime), is_state=templates._is_state,
                   has_value=templates._has_value)

    # -- Home Assistant's state functions --------------------------------------------------------------
    def _is_state(self, entity_id, state) -> bool:
        current = self.view.state_of(entity_id)
        if entity_id not in self.view.entity_ids():
            return False
        return current in state if isinstance(state, list) else current == state

    def _state_attr(self, entity_id, name):
        attrs = self.view.attrs_of(entity_id)
        return None if attrs is None else attrs.get(name)

    def _is_state_attr(self, entity_id, name, value) -> bool:
        attrs = self.view.attrs_of(entity_id)
        return attrs is not None and name in attrs and attrs[name] == value

    def _has_value(self, entity_id) -> bool:
        state = self.view.state_of(entity_id)
        return entity_id in self.view.entity_ids() and state not in ("unknown", "unavailable")

    def _today_at(self, time_str: str = "") -> dt.datetime:
        today = self.view.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if not time_str:
            return today
        try:
            parts = [int(p) for p in str(time_str).split(":")]
            t = dt.time(*parts)
        except (TypeError, ValueError):
            raise TemplateFailed(f"could not convert {time_str!r} to a time") from None
        return dt.datetime.combine(today.date(), t, today.tzinfo)

    def _as_datetime(self, value, default=_SENTINEL):
        if isinstance(value, dt.datetime):
            return value
        try:
            if isinstance(value, int | float) and not isinstance(value, bool):
                return dt.datetime.fromtimestamp(value, dt.UTC)
            if isinstance(value, str) and _IS_NUMERIC.match(value.strip() or "x"):
                return dt.datetime.fromtimestamp(float(value), dt.UTC)
            parsed = _parse_datetime(str(value))
            if parsed is None:
                raise ValueError
            return parsed
        except (TypeError, ValueError, OverflowError):
            if default is _SENTINEL:
                _no_default("as_datetime", value)
            return default

    def _as_local(self, value):
        return value.astimezone(self.view.zone())

    def _timestamp_custom(self, value, date_format="%Y-%m-%d %H:%M:%S", local=True, default=_SENTINEL):
        try:
            date = dt.datetime.fromtimestamp(float(value), dt.UTC)
            return (date.astimezone(self.view.zone()) if local else date).strftime(date_format)
        except (TypeError, ValueError, OverflowError):
            if default is _SENTINEL:
                _no_default("timestamp_custom", value)
            return default

    def _timestamp_local(self, value, default=_SENTINEL):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.UTC).astimezone(self.view.zone()).isoformat()
        except (TypeError, ValueError, OverflowError):
            if default is _SENTINEL:
                _no_default("timestamp_local", value)
            return default

    # -- compiling and rendering ----------------------------------------------------------------------
    def compile(self, source: str, names: set[str], entity_ids: set[str], where: str):
        """A compiled template, after checking it uses only the subset; `names` are the variables it may read
        (trigger, wait, repeat and the automation's variables); a literal entity id it reads must be in
        `entity_ids`, else `TemplateRefused` names it."""
        from jinja2 import TemplateSyntaxError, nodes
        try:
            tree = self.env.parse(source)
        except TemplateSyntaxError as e:
            raise TemplateRefused(f"{where}: template syntax error: {e.message}") from None
        local = {n.name for n in tree.find_all(nodes.Name) if n.ctx in ("store", "param")}
        for node in tree.find_all((nodes.Extends, nodes.Include, nodes.Import, nodes.FromImport, nodes.Macro,
                                   nodes.CallBlock, nodes.FilterBlock, nodes.ScopedEvalContextModifier)):
            raise TemplateRefused(f"{where}: template statement {type(node).__name__.lower()!r} is not supported")
        allowed = set(self.env.globals) | names | local
        for node in tree.find_all(nodes.Name):
            if node.ctx == "load" and node.name not in allowed:
                raise TemplateRefused(f"{where}: template name {node.name!r} is not supported")
            if node.ctx == "load" and node.name in TIME_NAMES and self.engine.site is None:
                raise TemplateRefused(f"{where}: template {node.name}(): the home has no site, so it has no date")
        for node in tree.find_all(nodes.Filter):
            if node.name not in self.env.filters:
                raise TemplateRefused(f"{where}: template filter {node.name!r} is not supported")
            if node.name in TIME_NAMES and self.engine.site is None:
                raise TemplateRefused(f"{where}: template {node.name}: the home has no site, so it has no date")
        for node in tree.find_all(nodes.Test):
            if node.name not in self.env.tests:
                raise TemplateRefused(f"{where}: template test {node.name!r} is not supported")
        for node in tree.find_all(nodes.Call):
            if isinstance(node.node, nodes.Getattr) and node.node.attr not in METHODS:
                raise TemplateRefused(f"{where}: template method {node.node.attr!r} is not supported")
            if isinstance(node.node, nodes.Name) and node.node.name in STATE_FUNCTIONS and node.args and \
                    isinstance(node.args[0], nodes.Const) and isinstance(node.args[0].value, str):
                self._known(node.args[0].value, entity_ids, where)
        for node in tree.find_all(nodes.Getattr):  # states.domain.object
            inner = node.node
            if isinstance(inner, nodes.Getattr) and isinstance(inner.node, nodes.Name) and \
                    inner.node.name == "states" and inner.node.name not in local:
                self._known(f"{inner.attr}.{node.attr}", entity_ids, where)
        return self.env.from_string(source)

    @staticmethod
    def _known(entity_id: str, entity_ids: set[str], where: str) -> None:
        if ENTITY_ID.match(entity_id.lower()) and entity_id.lower() not in entity_ids:
            raise TemplateRefused(f"entity {entity_id} is not one of this home's")

    def render(self, template, variables: dict) -> tuple[str, RenderInfo]:
        """(the rendered text, stripped as Home Assistant strips it; what the render read). A failure raises
        TemplateFailed."""
        from jinja2 import TemplateError
        self.view.info = RenderInfo()
        try:
            text = template.render(**variables)
        except TemplateFailed:
            raise
        except (TemplateError, TypeError, ValueError, AttributeError, KeyError, IndexError, ZeroDivisionError,
                OverflowError, re.error) as e:
            raise TemplateFailed(f"{type(e).__name__}: {e}") from None
        return text.strip(), self.view.info


# ---- Home Assistant's forgiving filters and functions (helpers/template.py) ----------------------------------------

def _math(name, fn):
    """Home Assistant's forgiving math function (sin, cos, ... log): fails without a default."""
    def forgiving(value, *args, default=_SENTINEL):
        try:
            return fn(float(value), *(float(a) for a in args))
        except (ValueError, TypeError):
            if default is _SENTINEL:
                _no_default(name, value)
            return default
    return forgiving


MATH = {name: _math(name, getattr(math, name)) for name in ("sin", "cos", "tan", "asin", "acos", "atan", "sqrt")}
MATH["log"] = _math("log", lambda value, base=math.e: math.log(value, base))
MATH["atan2"] = _math("atan2", math.atan2)


def _float(value, default=_SENTINEL):
    try:
        return float(value)
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _no_default("float", value)
        return default


def _int(value, default=_SENTINEL, base=10):
    try:
        if isinstance(value, str) and base != 10:
            return int(value, base)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(float(value))
    except (TypeError, ValueError, OverflowError):
        if default is _SENTINEL:
            _no_default("int", value)
        return default


def _round(value, precision=0, method="common", default=_SENTINEL):
    try:
        value = float(value)
        if method == "ceil":
            rounded = math.ceil(value * (10 ** precision)) / (10 ** precision)
        elif method == "floor":
            rounded = math.floor(value * (10 ** precision)) / (10 ** precision)
        elif method == "half":
            rounded = round(value * 2) / 2
        else:
            rounded = round(value, precision)
        return int(rounded) if precision == 0 else rounded
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _no_default("round", value)
        return default


def _bool(value, default=_SENTINEL):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.lower().strip()
        if v in ("1", "true", "yes", "on", "enable"):
            return True
        if v in ("0", "false", "no", "off", "disable"):
            return False
    elif isinstance(value, int | float):
        return value != 0
    if default is _SENTINEL:
        _no_default("bool", value)
    return default


def _iif(value, if_true=True, if_false=False, if_none=_SENTINEL):
    if value is None and if_none is not _SENTINEL:
        return if_none
    return if_true if bool(value) else if_false


def _is_number(value) -> bool:
    try:
        f = float(value)
    except (ValueError, TypeError):
        return False
    return math.isfinite(f)


def _min(*args, **kwargs):
    return min(args[0] if len(args) == 1 else args, **kwargs)


def _max(*args, **kwargs):
    return max(args[0] if len(args) == 1 else args, **kwargs)


def _parse_datetime(text: str):
    try:
        return dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_timestamp(value, default=_SENTINEL):
    try:
        parsed = value if isinstance(value, dt.datetime) else _parse_datetime(str(value))
        if parsed is None:
            raise ValueError
        return parsed.timestamp()
    except (TypeError, ValueError):
        if default is _SENTINEL:
            _no_default("as_timestamp", value)
        return default


def _timestamp_utc(value, default=_SENTINEL):
    try:
        return dt.datetime.fromtimestamp(float(value), dt.UTC).isoformat()
    except (TypeError, ValueError, OverflowError):
        if default is _SENTINEL:
            _no_default("timestamp_utc", value)
        return default


def _strptime(string, fmt, default=_SENTINEL):
    try:
        return dt.datetime.strptime(string, fmt)
    except (ValueError, AttributeError, TypeError):
        if default is _SENTINEL:
            _no_default("strptime", string)
        return default


def _flags(ignorecase):
    return re.IGNORECASE if ignorecase else 0


def _regex_match(value, find="", ignorecase=False) -> bool:
    return bool(re.match(find, value if isinstance(value, str) else str(value), _flags(ignorecase)))


def _regex_search(value, find="", ignorecase=False) -> bool:
    return bool(re.search(find, value if isinstance(value, str) else str(value), _flags(ignorecase)))


def _regex_replace(value="", find="", replace="", ignorecase=False) -> str:
    return re.sub(find, replace, value if isinstance(value, str) else str(value), flags=_flags(ignorecase))


def _regex_findall(value, find="", ignorecase=False) -> list:
    return re.findall(find, value if isinstance(value, str) else str(value), _flags(ignorecase))
