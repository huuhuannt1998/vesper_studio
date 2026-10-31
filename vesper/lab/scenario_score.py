"""Scoring a model's scenario against the hand-written gold ones (security-lab spec §5.4, plan W4b).

Devices are compared by what they are (their category and the feature acted on), not by their names: a model may add
its own front-door sensor where the gold file has `front_door`. Each element kind is a set of signatures, the gold
files' union against the drafts' union, with recall (how much of the gold the drafts have) and precision (how much of
the drafts the gold has). Hold times, claims' expected conditions and gaps are listed beside each other for the
researcher, since a near miss there is a judgement, not a set difference.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vesper.lab.automations import entity_ids
from vesper.lab.predicates import events
from vesper.lab.scenario import load_scenario

KINDS = ("attack", "defenses", "timeline", "automations", "claims")


def _category(home, name) -> str:
    try:
        return (home.device(name).record.get("identity") or {}).get("category") or "device"
    except Exception:  # noqa: BLE001 - an actor or a missing name: kept as it is
        return str(name)


def signatures(path: Path, catalog: dict) -> dict:
    s = load_scenario(Path(path), catalog)
    home = s.home
    out = {k: set() for k in KINDS}
    holds = []
    for a in s.attack:
        e = a.effect
        out["attack"].add((a.vantage, e.kind, e.direction, _category(home, e.device) if e.device else None,
                           e.feature))
        holds.append({"effect": e.kind, "device": _category(home, e.device) if e.device else None,
                      "feature": e.feature, "hold_s": e.hold_s})
    out["defenses"] = {d.kind for d in s.defenses}
    for act in s.timeline:
        (verb, target), = act.do.items()
        who = "player" if act.actor == "player" else "other"
        out["timeline"].add((who, verb, _category(home, target) if verb == "use" else None))
    ids = {eid: (name, feature) for name, by in entity_ids(home).items() for feature, eid in by.items()}
    raw = yaml.safe_load(home.automations.read_text()) if home.automations and home.automations.is_file() else []
    for a in raw or []:
        if not isinstance(a, dict):
            continue
        triggers = a.get("triggers", a.get("trigger")) or []
        actions = a.get("actions", a.get("action")) or []
        for t in triggers if isinstance(triggers, list) else [triggers]:
            for x in actions if isinstance(actions, list) else [actions]:
                ent = t.get("entity_id") if isinstance(t, dict) else None
                ent = ent[0] if isinstance(ent, list) and ent else ent
                name, feature = ids.get(ent, (None, None))
                service = x.get("action", x.get("service")) if isinstance(x, dict) else None
                out["automations"].add((_category(home, name) if name else None, feature, str(t.get("to")),
                                        str(service).split(".")[-1] if service else None))
    claims = []
    for c in s.claims:
        sig = tuple(_event(e, home) for e in events(c.predicate))  # in order: before and after read it
        form = next((k for k in ("before", "after", "gap", "count", "never", "all", "any", "not") if k in c.predicate),
                    "event")
        out["claims"].add((form, sig))
        claims.append({"claim": c.claim, "form": form, "events": sig, "conditions": c.conditions})
    return {"sets": out, "holds": holds, "claims": claims, "gaps": s.gaps, "title": s.title}


def _event(e: dict, home) -> str:
    """An event's kind and what it is about, without names: `action:lock:state:LOCK`, `actor:opens`."""
    for kind in ("report", "heard", "action"):
        if kind in e:
            x = e[kind]
            return f"{kind}:{_category(home, x.get('device'))}:{x.get('feature')}:{x.get('value')}"
    if "alert" in e:
        return f"alert:{e['alert'].get('defense')}"
    if "net" in e:
        return f"net:{e['net'].get('event')}"
    if "door" in e:
        return f"door:{e.get('open')}"
    verb = next((v for v in ("opens", "closes", "enters", "uses") if v in e), "?")
    return f"actor:{verb}"


def score(drafts: list[Path], gold: list[Path], catalog: dict) -> dict:
    d = [signatures(p, catalog) for p in drafts]
    g = [signatures(p, catalog) for p in gold]
    rows = {}
    for k in KINDS:
        have = set().union(*(x["sets"][k] for x in d))
        want = set().union(*(x["sets"][k] for x in g))
        hit = have & want
        rows[k] = {"gold": len(want), "draft": len(have), "matched": len(hit),
                   "recall": len(hit) / len(want) if want else 1.0,
                   "precision": len(hit) / len(have) if have else (1.0 if not want else 0.0),
                   "missing": sorted(map(str, want - have)), "extra": sorted(map(str, have - want))}
    gold_claims = [c for x in g for c in x["claims"]]
    same_conditions = 0
    for c in gold_claims:
        match = next((m for x in d for m in x["claims"] if (m["form"], m["events"]) == (c["form"], c["events"])), None)
        if match and all(match["conditions"].get(k) == v for k, v in c["conditions"].items()
                         if k in match["conditions"]):
            same_conditions += 1
    return {"elements": rows, "claims_with_the_gold_expectations": f"{same_conditions}/{len(gold_claims)}",
            "holds": {"draft": [h for x in d for h in x["holds"]], "gold": [h for x in g for h in x["holds"]]},
            "gaps": {"draft": [x2 for x in d for x2 in x["gaps"]], "gold": [x2 for x in g for x2 in x["gaps"]]}}


def markdown(result: dict) -> str:
    lines = ["| element | gold | draft | matched | recall | precision |", "|---|---|---|---|---|---|"]
    for k, r in result["elements"].items():
        lines.append(f"| {k} | {r['gold']} | {r['draft']} | {r['matched']} | {r['recall']:.2f} | "
                     f"{r['precision']:.2f} |")
    lines += ["", f"Claims with the gold's expected conditions: {result['claims_with_the_gold_expectations']}", ""]
    for k, r in result["elements"].items():
        if r["missing"] or r["extra"]:
            lines += [f"- {k}: missing {r['missing'] or 'nothing'}; extra {r['extra'] or 'nothing'}"]
    lines += ["", "Hold times (draft / gold):", ""]
    lines += [f"- draft {h['effect']} {h['device']}.{h['feature']} {h['hold_s']:g} s" for h in result["holds"]["draft"]]
    lines += [f"- gold {h['effect']} {h['device']}.{h['feature']} {h['hold_s']:g} s" for h in result["holds"]["gold"]]
    lines += ["", f"Gaps: draft {len(result['gaps']['draft'])}, gold {len(result['gaps']['gold'])}", ""]
    lines += [f"- draft: {x.get('what')}" for x in result["gaps"]["draft"]]
    lines += [f"- gold: {x.get('what')}" for x in result["gaps"]["gold"]]
    return "\n".join(lines)
