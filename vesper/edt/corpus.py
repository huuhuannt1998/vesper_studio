"""A corpus of public Home Assistant configurations: its automations, which of them read the environment, a census.

The corpus is a directory `raw/` of YAML files named `owner~repo##path~to~file.yaml` (a repository's file, its `/`
written `~`). The loader and the discovery of automations are copied from scripts/revision/rule_corpus_prevalence.py,
which stays as it is: nothing is resolved (no `!secret`, no `!include`), every local tag becomes the literal string
"!tag value", and a file that does not parse contributes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

ENV_KINDS = ("sun", "illuminance", "temperature", "humidity", "weather")


MAX_FILE_BYTES = 2 * 1024 * 1024   # a corpus file larger than this is refused before it is parsed
# Per document, aliases counted as they would expand. The corpus's most aliased document expands 1,109 of them
# (a 1,000 cap would refuse it and change the census); the 509-byte bomb passes 10,000 at its fourth level.
MAX_ALIAS_EXPANSIONS = 10_000


class CorpusRefusal(ValueError):
    """A corpus file refused before or while it is composed; `reason` is what the census records."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class HALoader(yaml.SafeLoader):
    """SafeLoader that tolerates Home Assistant's tags: a scalar tag becomes the string "!tag value"; a sequence or a
    mapping tag becomes a plain list or dict; an unknown tag becomes "!unknown_tag".

    The corpus is untrusted, and an alias stays a shared reference until something walks it (canonicalisation,
    json.dumps, binding, the engine): a 509-byte file of 7 levels of 10 aliases expands to 10^7 nodes. So aliases are
    counted as they would expand while each document is composed (an alias adds one plus the aliases inside the node
    it names) and a document above MAX_ALIAS_EXPANSIONS is refused ("alias_limit"). An alias naming a node that is
    still being composed, one that would contain itself, is refused too ("recursive_alias")."""

    def compose_document(self):
        self._expansions = 0
        self._inside: dict[int, int] = {}   # id(node) -> aliases expanded inside it
        self._closed: set[int] = set()      # nodes composed to their end
        try:
            return super().compose_document()
        finally:
            self._inside, self._closed = {}, set()

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            anchor = self.peek_event().anchor
            node = self.anchors.get(anchor)
            if node is not None:  # an undefined alias is left to the composer's own error
                if id(node) not in self._closed:
                    raise CorpusRefusal("recursive_alias", "the alias *%s names a node that contains it" % anchor)
                self._expansions += 1 + self._inside.get(id(node), 0)
                if self._expansions > MAX_ALIAS_EXPANSIONS:
                    raise CorpusRefusal("alias_limit", "more than %d aliases once expanded" % MAX_ALIAS_EXPANSIONS)
            return super().compose_node(parent, index)
        start = self._expansions
        node = super().compose_node(parent, index)
        self._closed.add(id(node))
        if self._expansions > start:
            self._inside[id(node)] = self._expansions - start
        return node


def _custom_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return ("!%s %s" % (suffix, loader.construct_scalar(node))).strip()
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


def _unknown_tag(loader, node):
    return "!unknown_tag"


HALoader.add_multi_constructor("!", _custom_tag)
HALoader.add_constructor(None, _unknown_tag)


def load_docs(text: str) -> list:
    return list(yaml.load_all(text, Loader=HALoader))


AUTO_KEY_RE = re.compile(r"^automation(\s+.+)?$")
TEMPLATE_ENTITY_KEYS = {"sensor", "binary_sensor", "number", "select", "button", "image",
                        "weather", "switch", "light", "cover", "fan", "lock", "vacuum",
                        "alarm_control_panel", "event", "update", "text"}


def _kind_of(d) -> str | None:
    if not isinstance(d, dict):
        return None
    keys = {str(k) for k in d}
    if "use_blueprint" in keys:
        return "use_blueprint"
    has_t = "trigger" in keys or "triggers" in keys
    has_a = "action" in keys or "actions" in keys
    if has_t and (keys & TEMPLATE_ENTITY_KEYS):
        return "template_entity"
    if has_t and has_a:
        return "automation"
    if has_t:
        return "trigger_without_action"
    return None


def discover(doc) -> list[tuple[str, dict, list]]:
    """One YAML document -> [(kind, object, path)]; kind is "automation", "use_blueprint", "template_entity",
    "trigger_without_action", or "under_other_key" (automation-shaped, under a key that is not `automation`)."""
    out: list[tuple[str, dict, list]] = []

    def scan_list(lst, path):
        for i, it in enumerate(lst):
            k = _kind_of(it)
            if k:
                out.append((k, it, path + [i]))

    def scan_dict(d, path, depth):
        for key, v in d.items():
            ks = str(key)
            if AUTO_KEY_RE.match(ks):
                if isinstance(v, list):
                    scan_list(v, path + [key])
                elif isinstance(v, dict):
                    k = _kind_of(v)
                    if k:
                        out.append((k, v, path + [key]))
            elif ks in ("template", "script", "blueprint"):
                continue
            elif isinstance(v, dict) and depth < 4:
                scan_dict(v, path + [key], depth + 1)
            elif isinstance(v, list) and depth == 0:
                for i, it in enumerate(v):
                    if _kind_of(it) in ("automation", "use_blueprint"):
                        out.append(("under_other_key", it, path + [key, i]))

    if isinstance(doc, list):
        scan_list(doc, [])
    elif isinstance(doc, dict):
        k = _kind_of(doc)
        if k in ("automation", "use_blueprint", "template_entity", "trigger_without_action"):
            out.append((k, doc, []))
        else:
            scan_dict(doc, [], 0)
    return out


def _canon(o):
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_canon(x) for x in o]
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    return str(o)


def automation_hash(raw: dict) -> str:
    """sha256 of the automation's canonical JSON without its `id` and `alias`: two copies of one rule agree."""
    body = {k: v for k, v in _canon(raw).items() if k not in ("id", "alias")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class CorpusAutomation:
    repo: str          # "owner/repo"
    file: str          # the file's name in raw/ (never an absolute path)
    path: tuple        # where in the file's document the automation sits
    raw: dict          # the automation as written
    hash: str          # automation_hash(raw)


def _read(path: Path) -> str:
    b = path.read_bytes()
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace")


def _scan(raw_dir: Path, stats: Counter | None = None) -> Iterator[CorpusAutomation]:
    """Every automation, in sorted file order, duplicates included. `stats` counts files read and failed, and each
    failure's reason under "reason:<reason>" (too_large, alias_limit, recursive_alias or parse_error)."""
    for name in sorted(os.listdir(raw_dir)):
        path = raw_dir / name
        if not path.is_file():
            continue
        if stats is not None:
            stats["total"] += 1
        repo = name.split("##", 1)[0].replace("~", "/", 1)
        try:  # the whole file is one unit: a size, load, discovery or hash failure fails it
            if path.stat().st_size > MAX_FILE_BYTES:
                raise CorpusRefusal("too_large", "over %d bytes" % MAX_FILE_BYTES)
            found = [CorpusAutomation(repo, name, tuple(npath), obj, automation_hash(obj))
                     for doc in load_docs(_read(path)) for kind, obj, npath in discover(doc) if kind == "automation"]
        except Exception as e:  # noqa: BLE001 - any failure is a parse failure; the file contributes nothing
            if stats is not None:
                stats["failed"] += 1
                stats["reason:" + (e.reason if isinstance(e, CorpusRefusal) else "parse_error")] += 1
            continue
        if stats is not None:
            stats["parsed"] += 1
        yield from found


def iter_automations(raw_dir: Path) -> Iterator[CorpusAutomation]:
    """The corpus's automations, de-duplicated by hash: the first in sorted file order is kept."""
    seen: set[str] = set()
    for a in _scan(Path(raw_dir)):
        if a.hash not in seen:
            seen.add(a.hash)
            yield a


# --- which automations read the environment ---------------------------------------------------------------------

_ILLUMINANCE = ("illuminance", "luminosity", "luminance", "lightlevel")
_ILLUMINANCE_TOKENS = {"lux", "illuminance", "luminosity", "luminance", "lightlevel"}
_TEMPERATURE_TOKENS = {"temperature", "temp", "temperatur", "temperatura", "temperature"}
_HUMIDITY = ("humidity", "feuchtigkeit")
_HUMIDITY_TOKENS = {"humidity", "humid", "feuchtigkeit", "luftfeuchtigkeit", "humedad"}
_WEATHER_TOKENS = {"weather", "darksky", "openweathermap", "pirateweather", "accuweather"}
_SENSOR_DOMAINS = {"sensor", "binary_sensor"}
_ATTRIBUTE_DOMAINS = {"weather", "climate", "sensor", "humidifier", "sun"}
_TPL_ENTITY_RE = re.compile(r"""['"]([a-z_][a-z0-9_]*\.[a-z0-9_]+)['"]|\bstates\.([a-z_][a-z0-9_]*\.[a-z0-9_]+)""")


def _name_kinds(name: str) -> set[str]:
    """The environment kinds a sensor's object id names (as whole words: `luxury` is not `lux`)."""
    tokens = set(name.split("_"))
    kinds = set()
    if tokens & _ILLUMINANCE_TOKENS or any(w in name for w in _ILLUMINANCE) or "light_level" in name:
        kinds.add("illuminance")
    if tokens & _TEMPERATURE_TOKENS or "temperature" in name:
        kinds.add("temperature")
    if tokens & _HUMIDITY_TOKENS or any(w in name for w in _HUMIDITY):
        kinds.add("humidity")
    if tokens & _WEATHER_TOKENS:
        kinds.add("weather")
    if name.startswith("sun_") or name == "sun":
        kinds.add("sun")
    return kinds


def _entity_kinds(entity_id: str, attribute: str | None = None) -> set[str]:
    domain, _, name = entity_id.partition(".")
    if not name:
        return set()
    kinds: set[str] = set()
    if domain == "sun":
        kinds.add("sun")
    elif domain == "weather":
        kinds.add("weather")
    elif domain in _SENSOR_DOMAINS:
        kinds |= _name_kinds(name)
    if attribute and domain in _ATTRIBUTE_DOMAINS:
        kinds |= _name_kinds(attribute.lower()) - {"weather", "sun"}
    return kinds


def _ids(value) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [x for v in value for x in _ids(v)]
    return []


def _strings(node) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def step_kinds(step, out: set[str]) -> None:
    """The kinds one trigger or condition reads (a condition's `conditions` are walked)."""
    if isinstance(step, str):  # shorthand template condition
        step = {"condition": "template", "value_template": step}
    if not isinstance(step, dict):
        return
    kind = str(step.get("platform") or step.get("trigger") or step.get("condition") or "")
    if kind == "sun":
        out.add("sun")
    attribute = step.get("attribute") if isinstance(step.get("attribute"), str) else None
    for key in ("entity_id", "entity"):
        for eid in _ids(step.get(key)):
            out |= _entity_kinds(eid, attribute)
    if kind == "device":  # a device trigger or condition names its quantity in `type`
        if str(step.get("domain")) in ("sensor", "binary_sensor") and isinstance(step.get("type"), str):
            out |= _name_kinds(step["type"].lower().replace("is_", "").replace("-", "_"))
    for text in _strings({k: v for k, v in step.items() if k not in ("conditions", "id", "alias")}):
        if "{{" in text or "{%" in text:
            for m in _TPL_ENTITY_RE.finditer(text):
                out |= _entity_kinds(m.group(1) or m.group(2))
    sub = step.get("conditions")
    if isinstance(sub, list):
        for s in sub:
            step_kinds(s, out)


def first_present(raw: dict, *keys: str):
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


_step_kinds = step_kinds  # the old private names stay as aliases
_first = first_present


def env_kinds(raw: dict) -> set[str]:
    """Which parts of the environment an automation reads in its triggers and conditions, among ENV_KINDS."""
    out: set[str] = set()
    for key in (("trigger", "triggers"), ("condition", "conditions")):
        steps = first_present(raw, *key)
        for step in (steps if isinstance(steps, list) else [steps]):
            step_kinds(step, out)
    return out & set(ENV_KINDS)


def census(raw_dir: Path) -> dict:
    """Counts over the corpus: files, automations and the environment-coupled ones, raw and de-duplicated.
    Paths are never recorded, only the corpus's own file names."""
    stats: Counter = Counter()
    seen: set[str] = set()
    raw_n = dedup_n = coupled_raw = coupled_dedup = 0
    kinds_raw: Counter = Counter()
    kinds_dedup: Counter = Counter()
    repos_raw: set[str] = set()
    repos_coupled_raw: set[str] = set()
    repos_dedup: set[str] = set()          # a de-duplicated automation belongs to its first occurrence's repo
    repos_coupled_dedup: set[str] = set()
    for a in _scan(Path(raw_dir), stats):
        kinds = env_kinds(a.raw)
        raw_n += 1
        repos_raw.add(a.repo)
        coupled_raw += bool(kinds)
        kinds_raw.update(kinds)
        if kinds:
            repos_coupled_raw.add(a.repo)
        if a.hash in seen:
            continue
        seen.add(a.hash)
        dedup_n += 1
        repos_dedup.add(a.repo)
        coupled_dedup += bool(kinds)
        kinds_dedup.update(kinds)
        if kinds:
            repos_coupled_dedup.add(a.repo)
    return {
        "files": {"total": stats["total"], "parsed": stats["parsed"], "failed": stats["failed"]},
        "failed_reasons": {k.split(":", 1)[1]: n for k, n in sorted(stats.items()) if k.startswith("reason:")},
        "repos_with_automations": {"raw": len(repos_raw), "deduplicated": len(repos_dedup)},
        "repos_with_environment_coupled": {"raw": len(repos_coupled_raw), "deduplicated": len(repos_coupled_dedup)},
        "automations": {"raw": raw_n, "deduplicated": dedup_n},
        "environment_coupled": {"raw": coupled_raw, "deduplicated": coupled_dedup},
        "by_kind": {"raw": {k: kinds_raw[k] for k in ENV_KINDS if kinds_raw[k]},
                    "deduplicated": {k: kinds_dedup[k] for k in ENV_KINDS if kinds_dedup[k]}},
        "hash": "sha256 of the canonical JSON of the automation without id and alias",
    }
