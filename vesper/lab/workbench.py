"""The paper-to-scenario workbench (security-lab spec §5, plan W4b): what VESPER's MCP server lets a model do, as
plain functions. The model reads a paper and builds a `vesper-scenario/1` file with these tools, element by
element; every edit is checked against the home before it is kept, so the draft is always a valid scenario, and an
error names what is wrong and the nearest valid choice.

No tool takes a host, a port, a shell command or code (spec §5.2, §7): a draft names a home's devices, links,
doorways, rooms and actors, and Home Assistant automations as data, which VESPER's own engine checks.
"""
from __future__ import annotations

import copy
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

import yaml

from vesper.lab.automations import Engine, entity_ids
from vesper.lab.home import HomeError, load_lab_home
from vesper.lab.linklayer import DIRECTIONS, KINDS
from vesper.lab.predicates import ACTOR_VERBS, FORMS, NET_EVENTS
from vesper.lab.scenario import (CONDITIONS, DEFENSE_FIELDS, DEFENSES, DOES, FORMAT, LATER_DEFENSES,
                                 LATER_VANTAGES, MODES, VANTAGES, ScenarioError, load_scenario)
from vesper.lab.worldmap import suggest

LISTS = ("actors", "timeline", "attack", "defenses", "expect", "automations", "devices")  # a draft's list sections
EXAMPLES = {  # one item of each list section, as `scenario_edit ... add` takes it
    "setup.devices": {"product": "zigbee:aqara:mccgq11lm", "name": "back_door", "at": {"door": "kitchen-garden"}},
    "automations": {"id": "lock_on_close", "alias": "Lock the door when it closes",
                    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.<id>_contact", "to": "off"}],
                    "actions": [{"action": "lock.lock", "target": {"entity_id": "lock.<id>_lock"}}]},
    "actors": {"name": "intruder", "model": "male_1", "start": {"room": "porch"}},
    "timeline": {"at": "19:30:50", "actor": "intruder", "do": {"open": "hall-porch"}, "cites": ["attack"]},
    "attack": {"at": "19:30:01", "vantage": "network", "effect": "delay", "link": "coordinator", "direction": "up",
               "match": {"device": "front_door", "feature": "contact"}, "hold_s": 30, "keep_connection": True,
               "cites": ["attack"]},
    "defenses": {"kind": "freshness", "max_age_s": 10, "action": "alert"},
    "expect": {"claim": "the intruder gets in before the door is locked",
               "predicate": {"before": [{"actor": "intruder", "opens": "hall-porch"},
                                        {"action": {"device": "front_lock", "feature": "state", "value": "LOCK"}}]},
               "conditions": {"baseline": False, "defense": False, "attack": True, "attack+defense": True},
               "cites": ["result"]},
    "gaps": {"what": "the cloud leg", "why": "VESPER has no cloud"},
    "runs": {"mode": "logical", "repeat": 3, "conditions": ["baseline", "defense", "attack", "attack+defense"],
             "until": "19:33:00"},
}
MESSAGE_CHARS = 600  # an error names what is wrong; it never echoes a whole value back
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RUN_LIMIT = 50  # runs per scenario run (conditions × repeat) through the server


class WorkbenchError(ValueError):
    pass


def _dump(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)


class Workbench:
    """`roots`: the folders whose homes (a `home.yaml` below them) the model may use; `runs_dir`: where runs write
    their traces; `allow_live`: whether a run may start Home Assistant (VESPER's own, on its own ports)."""

    def __init__(self, roots: list[Path], catalog: dict, *, runs_dir: Path, allow_live: bool = False,
                 stack_root: Path | None = None, catalog_dir: Path | None = None):
        self.roots = [Path(r).expanduser().resolve() for r in roots]
        self.catalog = catalog
        self.runs_dir = Path(runs_dir)
        self.allow_live = allow_live
        self.stack_root, self.catalog_dir = stack_root, catalog_dir
        self.drafts: dict[str, dict] = {}  # id -> {"home": Path, "spec": dict}
        self.runs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- what VESPER can express ----------------------------------------------------------------
    def capabilities(self) -> dict:
        return {
            "format": FORMAT,
            "vantages": {"now": list(VANTAGES), "later (plan W4a2: record what needs them as a gap)":
                         list(LATER_VANTAGES)},
            "network_effects": {
                "effects": list(KINDS), "directions": list(DIRECTIONS),
                "fields": {"at": "the home's clock time HH:MM:SS, or seconds after the start",
                           "link": "a link of the home (home_describe: network.links)",
                           "match": "{device, feature}: only that device's (and feature's) messages",
                           "hold_s": "delay/reorder: how long a message is held",
                           "keep_connection": "true: keepalives and acks pass, so no timeout fires",
                           "until": "when the effect ends (else it ends after one message for delay/reorder)",
                           "rate_bps": "throttle only (live runs)"}},
            "timeline": {"actors": "the player (who lives there) and the scenario's own actors (an intruder ...)",
                         "do": list(DOES), "forms": {"walk": "{room: ROOM}", "open|close": "a doorway id",
                                                     "use": "a device name"}},
            "defenses": {"now": {k: sorted(DEFENSE_FIELDS[k] - {"kind"}) for k in DEFENSES},
                         "later (plan W4a2)": list(LATER_DEFENSES)},
            "predicates": {"events": {"actor": f"{{actor: NAME, VERB: TARGET}}, VERB in {list(ACTOR_VERBS)}",
                                      "door": "{door: ID, open: true|false}",
                                      "report": "{report: {device, feature, value}}: the device sent it",
                                      "heard": "{heard: {device, feature, value}}: the hub has it",
                                      "action": "{action: {device, feature, value}}: the device accepted a command",
                                      "alert": "{alert: {defense, device}}",
                                      "net": f"{{net: {{link, event, device, feature}}}}, event in {list(NET_EVENTS)}"},
                           "forms": list(FORMS),
                           "form_fields": {"gap": "at_least_s / at_most_s", "count": "at_least / at_most"}},
            "conditions": list(CONDITIONS), "modes": list(MODES), "live_runs_allowed": self.allow_live,
            "automations": "Home Assistant automation YAML as data (triggers, conditions, actions), entity ids "
                           "from home_describe; VESPER's engine refuses what Home Assistant would refuse",
            "section_examples": EXAMPLES,
            "limits": ["no hosts, ports, shell commands or code", "the attacker sits in VESPER's own relay and "
                       "link layer: no ARP/DNS spoofing, radio, LAN or cloud", "VESPER has no cloud services",
                       "what cannot be expressed is recorded with the gap tool, never faked"],
        }

    # -- homes and products ---------------------------------------------------------------------
    def homes(self) -> list[dict]:
        out = []
        for root in self.roots:
            for path in sorted(root.rglob("home.yaml")):
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    continue
                entry = {"home": self._name(path.parent)}
                try:
                    home = load_lab_home(path, self.catalog)
                    entry.update(rooms=sum(1 for r in home.map.rooms if not r.outdoor), devices=len(home.devices),
                                 scene=home.scene)
                except HomeError as e:
                    entry["error"] = str(e)
                entry["scenarios"] = sorted(self._name(p) for p in path.parent.rglob("*.yaml")
                                            if p.name != "home.yaml" and _is_scenario(p))
                out.append(entry)
        return out

    def home_describe(self, home: str) -> dict:
        path = self._home_path(home)
        try:
            h = load_lab_home(path, self.catalog)
        except HomeError as e:
            raise WorkbenchError(str(e)) from e
        ids = entity_ids(h)
        net = h.network
        devices = []
        for d in h.devices:
            i = d.record.get("identity") or {}
            devices.append({
                "name": d.name, "product": d.product, "vendor": i.get("vendor"), "model": i.get("model"),
                "category": i.get("category"), "protocol": next(iter(d.record.get("binding") or {}), None),
                "room": d.room, "door": d.door, "state": d.state,
                "features": [{k: f[k] for k in ("name", "type", "values", "range", "unit") if f.get(k) is not None}
                             | {"settable": "set" in (f.get("access") or [])}
                             for f in d.record.get("features") or [] if f.get("type") != "unsupported"],
                "entity_ids": ids.get(d.name, {}),
                "network_path": net.path(d.name) if net else []})
        automations = []
        if h.automations and h.automations.is_file():
            for a in yaml.safe_load(h.automations.read_text()) or []:
                if isinstance(a, dict):
                    automations.append({"id": a.get("id"), "alias": a.get("alias")})
        start = int(h.start_time)
        return {
            "home": self._name(path.parent), "scene": h.scene,
            "start_time": f"{start // 3600:02d}:{start // 60 % 60:02d}:{start % 60:02d}",
            "rooms": [{"id": r.id, "label": r.label, "outdoor": r.outdoor} for r in h.map.rooms],
            "doorways": [{"id": w.id, "rooms": list(w.rooms), "outdoor": w.outdoor,
                          "open": h.doors.get(w.id, False)} for w in h.map.doorways],
            "devices": devices,
            "network": {"links": [{"name": n, "medium": link.medium, "tcp": link.tcp, "devices": list(link.devices)}
                                  for n, link in (net.links.items() if net else [])],
                        "unlinked": dict(net.unlinked) if net else {}},
            "residents": [r.name for r in h.residents],
            "automations": automations,
        }

    def catalog_search(self, query: str = "", category: str | None = None, protocol: str | None = None,
                       limit: int = 20) -> list[dict]:
        from vesper.lab.compose import search_products
        return search_products(self.catalog, {"L2": set(), "L4": {}}, query, category=category, protocol=protocol,
                               limit=max(1, min(int(limit), 50)))

    # -- drafts -------------------------------------------------------------------------------------
    def scenario_draft(self, home: str, title: str, paper: dict | None = None, from_scenario: str | None = None
                       ) -> dict:
        """A new draft on a home, empty or copied from a saved scenario of that home."""
        path = self._home_path(home)
        if from_scenario:
            src = self._file(from_scenario)
            spec = yaml.safe_load(src.read_text()) or {}
            automations = spec.get("setup", {}).get("automations")
            if automations:
                spec["setup"]["automations"] = yaml.safe_load((src.parent / automations).read_text()) or []
            spec["home"] = None
        else:
            spec = {"format": FORMAT, "title": title, "source": {"paper": dict(paper or {}), "passages": {}},
                    "home": None, "setup": {}, "actors": [], "timeline": [], "attack": [], "defenses": [],
                    "expect": [], "runs": {"mode": "logical", "repeat": 1, "conditions": list(CONDITIONS)},
                    "gaps": []}
        spec["title"] = title or spec.get("title") or "untitled"
        draft = uuid.uuid4().hex[:8]
        with self._lock:
            self.drafts[draft] = {"home": path, "spec": spec}
        self._check(draft, spec)
        return {"draft": draft, "home": self._name(path.parent), "scenario": self._public(spec)}

    def scenario_edit(self, draft: str, section: str, action: str, value=None, index: int | None = None) -> dict:
        """Change one section of a draft; kept only if the whole draft is still a valid scenario.

        section: title, setup.devices, automations, actors, timeline, attack, defenses, expect, runs, gaps;
        action: add (to a list), replace (an item at `index`, or a whole section), remove (an item at `index`)."""
        spec = copy.deepcopy(self._draft(draft)["spec"])
        setup = spec.setdefault("setup", {})
        if section in ("setup.devices", "devices"):
            target, key = setup, "devices"
        elif section == "automations":
            target, key = setup, "automations"
        elif section in ("title", "runs", "actors", "timeline", "attack", "defenses", "expect", "gaps"):
            target, key = spec, section
        else:
            raise WorkbenchError(f"section {section!r}: {suggest(section, ['title', 'setup.devices', 'automations', 'actors', 'timeline', 'attack', 'defenses', 'expect', 'runs', 'gaps'])}")  # noqa: E501
        current = target.get(key)
        example = EXAMPLES.get("setup.devices" if key == "devices" else key)
        if action == "add":
            if key not in LISTS and key != "gaps":
                raise WorkbenchError(f"{section} is not a list: replace it")
            if not isinstance(value, dict):
                raise WorkbenchError(f"{section}: add takes one item, a mapping (call add once per item); for "
                                     f"example {example}")
            target[key] = list(current or []) + [value]
        elif action == "replace":
            if index is None:
                target[key] = value
            else:
                items = list(current or [])
                if not 0 <= index < len(items):
                    raise WorkbenchError(f"{section}[{index}]: there are {len(items)} items")
                items[index] = value
                target[key] = items
        elif action == "remove":
            items = list(current or [])
            if index is None or not 0 <= index < len(items):
                raise WorkbenchError(f"{section}: give the index of the item to remove (0..{len(items) - 1})")
            del items[index]
            target[key] = items
        else:
            raise WorkbenchError(f"action {action!r} is not add, replace or remove")
        self._check(draft, spec)
        with self._lock:
            self.drafts[draft]["spec"] = spec
        return {"ok": True, "scenario": self._public(spec)}

    def cite(self, draft: str, key: str, passage: str) -> dict:
        """Quote the paper: a passage that steps and claims cite by its key."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key or ""):
            raise WorkbenchError(f"key {key!r}: lower-case letters, digits and _ (e.g. threat, attack, result)")
        if not isinstance(passage, str) or not passage.strip():
            raise WorkbenchError("passage: the quoted text of the paper")
        spec = copy.deepcopy(self._draft(draft)["spec"])
        spec.setdefault("source", {}).setdefault("passages", {})[key] = " ".join(passage.split())
        self._check(draft, spec)
        with self._lock:
            self.drafts[draft]["spec"] = spec
        return {"ok": True, "passages": sorted(spec["source"]["passages"])}

    def gap(self, draft: str, what: str, why: str) -> dict:
        """Record what the paper has and VESPER cannot express, and why: never faked."""
        if not (isinstance(what, str) and what.strip() and isinstance(why, str) and why.strip()):
            raise WorkbenchError("a gap needs both what and why")
        return self.scenario_edit(draft, "gaps", "add", {"what": what.strip(), "why": why.strip()})

    def scenario_validate(self, draft: str) -> dict:
        d = self._draft(draft)
        s = self._check(draft, d["spec"])
        return {"ok": True, "title": s.title, "devices": [x.name for x in s.home.devices], "actors": list(s.actors),
                "timeline": len(s.timeline), "attack": len(s.attack), "defenses": [x.kind for x in s.defenses],
                "claims": [c.claim for c in s.claims], "gaps": len(s.gaps), "runs": s.runs.__dict__,
                "yaml": _dump(self._public(d["spec"]))}

    def scenario_save(self, draft: str, name: str, overwrite: bool = False) -> dict:
        """Write the draft beside its home: `scenarios/NAME.yaml` (and its automations, if it has its own)."""
        if not SLUG.match(name or ""):
            raise WorkbenchError(f"name {name!r}: lower-case letters, digits and -, e.g. phantom-delay-case3")
        d = self._draft(draft)
        out_dir = d["home"].parent / "scenarios"
        out = out_dir / f"{name}.yaml"
        if out.exists() and not overwrite:
            raise WorkbenchError(f"{self._name(out)} exists; save under another name, or overwrite it")
        self._check(draft, d["spec"])
        out_dir.mkdir(exist_ok=True)
        spec = copy.deepcopy(d["spec"])
        spec["home"] = "../home.yaml"
        automations = spec.get("setup", {}).pop("automations", None)
        if automations:
            (out_dir / f"{name}.automations.yaml").write_text(_dump(automations))
            spec["setup"]["automations"] = f"{name}.automations.yaml"
        if not spec.get("setup"):
            spec.pop("setup", None)
        out.write_text(f"# Written through VESPER's MCP server ({time.strftime('%Y-%m-%d')}); review before citing.\n"
                       + _dump(spec))
        return {"saved": self._name(out)}

    # -- runs -----------------------------------------------------------------------------------------
    def scenario_run(self, scenario: str, mode: str | None = None, repeat: int | None = None) -> dict:
        """Run a saved scenario (or a draft) in the background; run_status and run_report follow it."""
        if scenario in self.drafts:
            spec = copy.deepcopy(self.drafts[scenario]["spec"])
            path = self._write_check_file(self.drafts[scenario]["home"], spec,
                                          Path(tempfile.mkdtemp(prefix="vesper-draft-")))
        else:
            path = self._file(scenario)
        try:
            s = load_scenario(path, self.catalog, mode=mode)
        except ScenarioError as e:
            raise WorkbenchError(str(e)) from e
        if repeat is not None:
            if not 1 <= int(repeat) <= 20:
                raise WorkbenchError("repeat: 1 to 20 runs per condition")
            s.runs = type(s.runs)(s.runs.mode, int(repeat), s.runs.conditions, s.runs.until)
        if s.runs.mode == "live" and not self.allow_live:
            raise WorkbenchError("live runs start Home Assistant: the server was started without --allow-live; "
                                 "run it logical, or ask the researcher to run it live from the command line")
        total = len(s.runs.conditions) * s.runs.repeat
        if total > RUN_LIMIT:
            raise WorkbenchError(f"{total} runs: at most {RUN_LIMIT} per call")
        run = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
        out = self.runs_dir / run
        state = {"scenario": str(path), "mode": s.runs.mode, "total": total, "state": "running", "out": out,
                 "report": None, "summary": None, "error": None}
        with self._lock:
            self.runs[run] = state
        threading.Thread(target=self._run, args=(run, s, out), daemon=True).start()
        return {"run": run, "mode": s.runs.mode, "runs": total}

    def _run(self, run: str, s, out: Path) -> None:
        from vesper.lab.scenario_run import report, run_scenario
        try:
            runs = run_scenario(s, out, root=self.stack_root, catalog_dir=self.catalog_dir)
            text, summary = report(s, runs)
            (out / "report.md").write_text(text)
            self.runs[run].update(state="done", report=text, summary=summary)
        except Exception as e:  # noqa: BLE001 - reported to the model, which tells the researcher
            self.runs[run].update(state="failed", error=f"{type(e).__name__}: {e}")

    def run_status(self, run: str) -> dict:
        r = self._run_state(run)
        done = len(list(r["out"].glob("*.jsonl"))) if r["out"].exists() else 0
        return {"run": run, "state": r["state"], "traces": done, "of": r["total"], "error": r["error"]}

    def run_report(self, run: str) -> dict:
        r = self._run_state(run)
        if r["state"] == "running":
            return {"run": run, "state": "running", "note": "not finished: ask run_status again"}
        if r["state"] == "failed":
            return {"run": run, "state": "failed", "error": r["error"]}
        claims = r["summary"]["claims"]
        return {"run": run, "state": "done", "reproduced": sum(c["reproduced"] for c in claims),
                "claims": len(claims), "report": r["report"]}

    # -- helpers ----------------------------------------------------------------------------------------
    def _check(self, draft: str, spec: dict):
        """The draft as a scenario, or WorkbenchError naming what is wrong (and the automations VESPER refuses)."""
        home = self._draft(draft)["home"] if draft in self.drafts else None
        with tempfile.TemporaryDirectory(prefix="vesper-draft-") as tmp:
            path = self._write_check_file(home, spec, Path(tmp))
            try:
                s = load_scenario(path, self.catalog)
            except ScenarioError as e:
                text = str(e)
                raise WorkbenchError(text if len(text) <= MESSAGE_CHARS else text[:MESSAGE_CHARS] + " ...") from e
            if spec.get("setup", {}).get("automations"):
                from vesper.lab.session import Session
                refused = Engine(Session(s.home)).refused
                if refused:
                    raise WorkbenchError("automations: " + "; ".join(f"{r['automation']}: {r['reason']}"
                                                                     for r in refused))
            return s

    def _write_check_file(self, home: Path, spec: dict, folder: Path) -> Path:
        spec = copy.deepcopy(spec)
        spec["home"] = str(home)
        automations = spec.get("setup", {}).get("automations")
        if not automations:  # none of its own: the home's automations run, as when it is saved
            spec.get("setup", {}).pop("automations", None)
        else:
            if not isinstance(automations, list):
                raise WorkbenchError(f"automations: {automations!r} is not a list of automations")
            (folder / "automations.yaml").write_text(_dump(automations))
            spec["setup"]["automations"] = "automations.yaml"
        path = folder / "scenario.yaml"
        path.write_text(_dump(spec))
        return path

    def _public(self, spec: dict) -> dict:
        return {k: v for k, v in spec.items() if k != "home"}

    def _draft(self, draft: str) -> dict:
        try:
            return self.drafts[draft]
        except KeyError:
            raise WorkbenchError(f"unknown draft {draft!r}; {suggest(draft, list(self.drafts))}") from None

    def _run_state(self, run: str) -> dict:
        try:
            return self.runs[run]
        except KeyError:
            raise WorkbenchError(f"unknown run {run!r}; {suggest(run, list(self.runs))}") from None

    def _name(self, path: Path) -> str:
        path = Path(path).resolve()
        for root in self.roots:
            if path.is_relative_to(root):
                return f"{root.name}/{path.relative_to(root)}"
        return str(path)

    def _resolve(self, name: str) -> Path:
        """A name from homes() back to its path, only inside the roots (no other file is reachable)."""
        for root in self.roots:
            head, _, rest = (name or "").partition("/")
            if head == root.name and rest:
                path = (root / rest).resolve()
                if path.is_relative_to(root) and path.is_file():
                    return path
        raise WorkbenchError(f"unknown name {name!r}: use a name homes() lists")

    def _home_path(self, home: str) -> Path:
        path = self._resolve(home if home.endswith(".yaml") else f"{home.rstrip('/')}/home.yaml")
        if path.name != "home.yaml":
            raise WorkbenchError(f"{home!r} is not a home: use a name homes() lists")
        return path

    def _file(self, name: str) -> Path:
        path = self._resolve(name)
        if not _is_scenario(path):
            raise WorkbenchError(f"{name!r} is not a {FORMAT} file")
        return path


def _is_scenario(path: Path) -> bool:
    try:
        with open(path) as f:
            return FORMAT in f.read(4096)
    except OSError:
        return False
