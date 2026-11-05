#!/usr/bin/env python3
"""Aggregate the environmental differential testing (EDT) results into one committed summary.

The full sweep, cover, ladder and closed-loop results hold corpus content (automations, repository names, entity
names) and stay local, outside the repository. This script reads them and writes aggregates only: counts, rates,
reason classes, environment labels (city, date, plan north) and timings. It writes no automation, no repository or
entity name, no automation or repository hash and no corpus path.

    python scripts/edt_summary.py [--lab DIR] [--commit dc119cd] [--workers 6] [--out results/edt/summary-C.json]

Inputs, in --lab (default ~/vesper-lab/edt), for commit C:
    sweep-rung{4,3,2,1}-C.json and .log    `vesper edt run` at each rung (rung 2.5 too when present; Addendum 6)
    sweep-rung4-{check120f4ca,catalogrule}-C.json   optional: Addendum 6's check and reporting-rule comparison
    cover-rung4-C.json                     optional: `vesper edt cover` on the rung-4 sweep
    ladder-C.json                          optional: `vesper edt ladder`
    confirm-rung4-C.json                   optional: closed-loop confirmation (vesper.edt.confirm)
    posthoc-setcover-C.txt                 optional: stdout of the post hoc set-based greedy cover

Standard library only. Protocol: docs/experiments/edt-protocol.md; results: docs/experiments/results-edt.md.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

RUNGS = ("4", "3", "2.5", "2", "1")  # 2.5: protocol Addendum 6, read when present
EXTRA_SWEEPS = ("rung4-check120f4ca", "rung4-catalogrule")  # Addendum 6, A6.8 as amended by A6.9
ENV_KINDS = ("sun", "temperature", "illuminance", "humidity", "weather")
SENSED = {"temperature", "illuminance", "humidity", "weather"}
TOP = 20
HELPERS = r"(input_\w+|group|person|device_tracker|zone|timer)"
# Home Assistant core entity domains; any other domain in an unbindable reason is an integration's own name and is
# reported only inside an aggregate class.
CORE_DOMAINS = {
    "media_player", "alarm_control_panel", "automation", "input_datetime", "script", "scene", "vacuum", "input_text",
    "calendar", "fan", "notify", "event", "input_button", "weather", "remote", "camera", "select", "button",
    "schedule", "number", "update", "todo", "counter", "persistent_notification", "humidifier", "valve",
    "assist_satellite", "ai_task", "alert", "image_processing", "tts", "conversation", "siren", "plant",
    "lawn_mower", "time", "water_heater", "image", "utility_meter", "text", "air_quality", "device_tracker",
}

# (pattern, class); first match wins. A reason no rule matches becomes "other" (its text may carry corpus content).
UNBINDABLE_RULES = [
    (r"^its name names no role the standard home has$", "an entity's name names no role the standard home has"),
    (rf"^{HELPERS} in a trigger\b", "a helper or presence entity in a trigger"),
    (rf"^{HELPERS} in a template or an action$", "a helper or presence entity in a template or an action"),
    (rf"^{HELPERS} (inside |and a device |in a |as a numeric threshold|compared against)",
     "a helper or presence entity elsewhere (inside or/not, beside a device, as a threshold, in a zone or device "
     "condition)"),
    (r"^conflicting conditions on one helper$", "conflicting conditions on one helper"),
    (r"^\w+ entities are not modelled$", "an entity domain the standard home does not model"),
    (r"^device triggers of remotes are not modelled$", "device triggers of remotes are not modelled"),
    (r"^\w+ device (triggers|conditions|actions) are not modelled$",
     "a device trigger, condition or action of an unmodelled integration"),
    (r"^blueprint inputs are not expanded$", "blueprint inputs are not expanded"),
    (r"^distinct entities collapse onto one device$", "distinct entities collapse onto one device"),
    (r"^semi-outdoor spaces are not modelled$", "semi-outdoor spaces are not modelled"),
    (r"^device_id/area_id not modelled$", "device_id/area_id not modelled"),
    (r"^device_id targets name no entity$", "device_id targets name no entity"),
    (r"^device steps on a registry id name no room$", "device steps on a registry id name no room"),
    (r"^device groups are not modelled$", "device groups are not modelled"),
    (r"^the standard home has no (cover|climate) in ", "the standard home has no cover or climate in that room"),
    (r"^threshold looks Fahrenheit or non-ambient$", "threshold looks Fahrenheit or non-ambient"),
    (r"^weather service sensors other than", "weather service sensors other than temperature, humidity and "
                                             "illuminance are not modelled"),
    (r"^time sensors are not modelled", "time sensors are not modelled"),
    (r"^sun sensors are not modelled", "sun sensors are not modelled"),
    (r"^YAML tags are not resolved$", "YAML tags are not resolved"),
    (r"^no room of the standard home for the area", "no room of the standard home for the area"),
    (r"^area targets of ", "area targets of an unmodelled domain"),
    (r"^entity_id .* is not an entity id$", "a value given as an entity id is not one"),
    (r"^enclosure or medium, not room air$", "enclosure or medium, not room air"),
]
REFUSED_RULES = [
    (r"^(trigger|condition) sun: the home has no site", "a sun trigger or condition with no site (rung 1)"),
    (r"weekday: the home has no site", "a weekday trigger or condition with no site, so no date (rung 1)"),
    (r"the home has no site, so it has no date", "a template reading the date with no site (rung 1)"),
    (r"^it has no trigger$", "it has no trigger"),
    (r"^trigger (event|mqtt|webhook|tag|conversation|persistent_notification) is not supported \([a-zA-Z ,'-]+\)$",
     None),  # the reason's own text (written by the engine, no corpus content)
    (r"^'blueprint' is not supported$", "an automation made from a blueprint"),
    (r"^'[\w#]+' is not supported$", "an unsupported top-level key"),
    (r"^entity .* is not one of this home's", "an entity (templated or unbound) that is not one of this home's"),
    (r"value_template is not supported \(a template\)$", "a value_template in a numeric_state trigger or condition"),
    (r"template (name|filter|method|statement) .* is not supported|template syntax error",
     "a template feature the engine does not support"),
    (r"^(delay|for): ", "a delay or duration that is a template or malformed"),
    (r"^action parallel is not supported", "action parallel is not supported (parallel branches)"),
    (r"^action wait_template is not supported", "action wait_template is not supported (a template wait)"),
    (r"^action .*target 'label_id' is not supported$", "a label_id target"),
    (r"^action .*: no target entity$", "an action with no target entity"),
    (r"^action .* is not a (light|switch)$", "an action whose target belongs to another domain"),
    (r"^action ", "another unsupported action"),
    (r"^(trigger|condition) ", "another unsupported trigger or condition"),
]


def classify(reason: str, rules) -> str:
    for pattern, label in rules:
        if re.search(pattern, reason):
            return reason if label is None else label
    return "other"


def reason_table(reasons: dict[str, int], rules) -> dict:
    classes = Counter()
    for reason, n in reasons.items():
        classes[classify(reason, rules)] += n
    top = classes.most_common(TOP)
    return {"total": sum(classes.values()), "n_classes": len(classes),
            "top": [{"class": c, "n": n} for c, n in top],
            "rest": sum(classes.values()) - sum(n for _, n in top)}


def unmodelled_domains(reasons: dict[str, int]) -> list[dict]:
    domains = Counter()
    for reason, n in reasons.items():
        m = re.match(r"^(\w+) entities are not modelled$", reason)
        if m:
            domains[m.group(1) if m.group(1) in CORE_DOMAINS else "(other integrations)"] += n
    return [{"domain": d, "n": n} for d, n in domains.most_common(15)]


def on_every_sensor_change(bound: dict) -> bool:
    """Every trigger is a `state` trigger with no `to`/`from` on `sensor.*` entities: the automation fires on each
    report, so its first firing time is when the sensor first reports on the day, not a behavioural choice."""
    trig = bound.get("triggers", bound.get("trigger", []))
    trig = trig if isinstance(trig, list) else [trig]

    def ok(t):
        if not isinstance(t, dict) or t.get("platform", t.get("trigger")) != "state" or "to" in t or "from" in t:
            return False
        ids = t.get("entity_id") if isinstance(t.get("entity_id"), list) else [t.get("entity_id")]
        return all(str(e).startswith("sensor.") for e in ids)
    return bool(trig) and all(ok(t) for t in trig)


def has_sun_trigger(bound: dict) -> bool:
    """A `sun` trigger, or a trigger on `sun.sun` (state or numeric_state on its elevation)."""
    trig = bound.get("triggers", bound.get("trigger", []))
    trig = trig if isinstance(trig, list) else [trig]
    return any(isinstance(t, dict) and (t.get("platform", t.get("trigger")) == "sun" or "sun.sun" in json.dumps(
        t.get("entity_id"))) for t in trig)


def env_label(e: dict) -> str:
    return f"{e['city']}, {e['date']}, north {int(e['north_deg'])}"


def share(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


# -- one sweep ---------------------------------------------------------------------------------------------------
def sweep_summary(path: Path, log: Path) -> dict:
    r = json.loads(path.read_text())
    autos = r["automations"].values()
    bound = [a for a in autos if a["bound"] is not None]
    run = [a for a in bound if a["outcomes"]]
    flagged = [a for a in run if a["divergences"]]
    def funnel(xs):
        b = [a for a in xs if a["bound"] is not None]
        ru = [a for a in b if a["outcomes"]]
        fl = [a for a in ru if a["divergences"]]
        return {"parsed": len(xs), "bound": len(b), "run": len(ru), "flagged": len(fl),
                "bound_share": share(len(b), len(xs)), "run_share_of_bound": share(len(ru), len(b)),
                "flagged_share_of_run": share(len(fl), len(ru))}

    coupled = [a for a in autos if a["kinds"]]
    by_env_kind = {k: funnel([a for a in autos if k in a["kinds"]]) for k in ENV_KINDS}
    static = [a for a in run if a["static"]]
    out = {
        "rung": r["rung"],
        "n_envs": len(r["envs"]),
        "yield": r["yield"],
        "yield_assumption_free": r["yield_assumption_free"],
        "environment_coupled": funnel(coupled),
        "environment_coupled_assumption_free": funnel([a for a in coupled if not a["assumptions"]]),
        "not_environment_coupled": funnel([a for a in autos if not a["kinds"]]),
        "by_environment_kind": by_env_kind,
        "flagged": r["flagged"],
        "flagged_assumption_free": r["flagged_assumption_free"],
        "flagged_by_environment_kind": dict(Counter(k for a in flagged for k in a["kinds"])),
        "flagged_by_environment_and_divergence_kinds": {
            f"{'+'.join(k) or '(none)'} / {'+'.join(d)}": n for (k, d), n in
            Counter((tuple(a["kinds"]), tuple(a["divergences"])) for a in flagged).most_common(15)},
        "flagged_reading_a_sensed_quantity": sum(1 for a in flagged if set(a["kinds"]) & SENSED),
        "flagged_reading_a_sensed_quantity_without_sun": sum(
            1 for a in flagged if set(a["kinds"]) & SENSED and "sun" not in a["kinds"]),
        "flagged_with_no_environment_kind": sum(1 for a in flagged if not a["kinds"]),
        "flagged_with_no_environment_kind_reading_the_clock": sum(
            1 for a in flagged if not a["kinds"] and "now()" in json.dumps(a["bound"])),
        "repositories": {"run": len({a["repo"] for a in run}), "flagged": len({a["repo"] for a in flagged})},
        "flagged_reading_the_clock_in_a_template": sum(1 for a in flagged if "now()" in json.dumps(a["bound"])),
        "flagged_with_assumptions": sum(1 for a in flagged if a["assumptions"]),
        "flagged_triggered_on_every_sensor_change": sum(1 for a in flagged if on_every_sensor_change(a["bound"])),
        "flagged_triggered_on_every_sensor_change_time_spread_only": sum(
            1 for a in flagged if on_every_sensor_change(a["bound"]) and a["divergences"] == ["time_spread"]),
        "flagged_frame": r.get("flagged_frame"),  # protocol Addendum 6, A6.3
        "flagged_frame_by_environment_kind": dict(Counter(k for a in run if a.get("frame_divergences")
                                                          for k in a["kinds"])),
        "rich_divergent": r.get("rich_divergent"),
        "reporting_rule": r.get("reporting_rule", "catalog"),
        "binder": r.get("binder", "120f4ca"),
        "static": r["static"],
        "static_and_flagged": sum(1 for a in static if a["divergences"]),
        "negative_control": r["negative_control"],
        "partially_refused": r["partially_refused"],
        "failed_envs": len(r["failed_envs"]),
        "bound_with_assumptions": sum(1 for a in bound if a["assumptions"]),
        "unbindable_reasons": reason_table(r["unbindable_reasons"], UNBINDABLE_RULES),
        "unmodelled_domains": unmodelled_domains(r["unbindable_reasons"]),
        "refused_reasons": reason_table(r["refused_reasons"], REFUSED_RULES),
        "timing": timing(log, WORKERS),
    }
    assert out["yield"]["run"] == len(run) and out["flagged"]["n"] == len(flagged), "sweep counts disagree"
    return out, r


def timing(log: Path, workers: int = 6) -> dict:
    text = log.read_text()
    out: dict = {}
    m = re.search(r"replayed (\d+) x (\d+) in ([\d.]+) s", text)
    if m:
        bound, envs, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
        out.update(replay_s=secs, evaluations=bound * envs,
                   evaluations_per_minute=round(bound * envs / secs * 60))
    m = re.search(r"in (\d+) s; flagged", text)
    if m:
        out["wall_s"] = int(m.group(1))
    per_env = [float(x) for x in re.findall(r"^\d+/\d+ \S+ ([\d.]+) s$", text, flags=re.M)]
    if per_env:
        out["per_env_s"] = {"median": statistics.median(per_env), "min": min(per_env), "max": max(per_env)}
    out["workers"] = workers  # the run ledger (--workers of the sweeps)
    return out


# -- cover -------------------------------------------------------------------------------------------------------
def cover_summary(path: Path, envs: dict[str, str], posthoc: Path, kinds_of: dict[str, list]) -> dict:
    c = json.loads(path.read_text())
    lab = lambda keys: [envs[k] for k in keys]  # noqa: E731
    grid = dict(c["baselines"]["six_city_grid"])
    grid["envs"] = lab(grid["envs"])
    shares = sorted(c["single_env_share"].items(), key=lambda kv: (-kv[1], kv[0]))
    pairs = Counter((w["axis"], envs[w["a"]], envs[w["b"]]) for w in c["witnesses"].values() if w)
    out = {
        "flagged": c["flagged"],
        "split": "sha256 parity of the repository name; even = train, odd = held_out",
        "cover": {**c["cover"], "envs": lab(c["cover"]["envs"])},
        "baselines": {"random_5": c["baselines"]["random_5"], "six_city_grid": grid},
        "non_degeneracy": {**c["non_degeneracy"], "env": envs[c["non_degeneracy"]["env"]]},
        "single_env_share": [{"env": envs[k], "share": v} for k, v in shares],
        "witnesses_by_axis": c["witnesses_by_axis"],
        "witnesses_by_axis_and_environment_kind": {
            f"{axis} / {'+'.join(k) or '(none)'}": n for (axis, k), n in sorted(
                Counter((w["axis"], tuple(kinds_of[i])) for i, w in c["witnesses"].items() if w).items())},
        "no_witness": c["no_witness"],
        "witness_pairs_top": [{"axis": a, "a": x, "b": y, "n": n} for (a, x, y), n in pairs.most_common(10)],
    }
    if posthoc.exists():
        steps = []
        for line in posthoc.read_text().splitlines():
            m = re.match(r"^(\d+) (\S+) ([\d.]+) ([\d.]+)$", line.strip())
            if m:
                steps.append({"size": int(m.group(1)), "added": envs[m.group(2)],
                              "set_coverage_train": float(m.group(3)), "set_coverage_held_out": float(m.group(4))})
        out["post_hoc_set_based_cover"] = {
            "pre_registered": False,
            "method": "greedy on set-based coverage (A1.3) over the training half until >= 0.8, ties to the earlier "
                      "environment in the sweep's order; a single environment covers nothing by itself (a divergence "
                      "needs two outcomes), so the first step is a tie broken by order",
            "steps": steps,
        }
    return out


# -- closed loop -------------------------------------------------------------------------------------------------
def confirm_summary(path: Path, envs: dict[str, dict], repo_of: dict[str, str], sun_trigger: dict[str, bool]) -> dict:
    c = json.loads(path.read_text())
    city = lambda k: envs[k]["city"]  # noqa: E731
    month = lambda k: envs[k]["date"]  # noqa: E731
    results = c["results"]
    dis = c["disagreements"]
    by_auto = Counter(d["id"] for d in dis)
    runs_per_auto = Counter(r["id"] for r in results)
    flag_of = {r["id"]: r["flagged"] for r in results}
    viol = c["envelope"]["violations"]
    viol_runs = {(v["id"], v["env_key"]) for v in viol}
    cells = [(r, room, v) for r in results for room, v in r["envelope"].items() if v is not None]
    hot = [(r, room, v) for r, room, v in cells if v.get("breached_above_30")]
    return {
        "seed": c["seed"], "sample_per_group": c["sample"], "days": c["days"], "runs": c["runs"],
        "automations": c["automations"],
        "agreement": c["agreement"],
        "by_group": c["by_group"],
        "refused_runs": len(c["refused"]),
        "disagreeing_runs": len(dis),
        "disagreement_fields": c["disagreement_fields"],
        "disagreement_field_combinations": {"+".join(sorted(k)): n for k, n in
                                            Counter(tuple(sorted(d["fields"])) for d in dis).most_common()},
        "disagreeing_automations": dict(Counter("flagged" if flag_of[i] else "unflagged" for i in by_auto)),
        "disagreeing_repositories": len({repo_of[i] for i in by_auto}),
        "disagreeing_automations_share_of_their_runs": sorted(
            round(n / runs_per_auto[i], 3) for i, n in by_auto.items()),
        "disagreeing_runs_by_city": dict(Counter(city(d["env_key"]) for d in dis)),
        "disagreeing_runs_total_firings": dict(Counter(str(d["firings"]) for d in dis)),
        "disagreeing_runs_one_firing": {
            "runs": sum(1 for d in dis if d["firings"] == 1),
            "automations": len({d["id"] for d in dis if d["firings"] == 1}),
            "runs_of_sun_triggered_automations": sum(1 for d in dis if d["firings"] == 1 and sun_trigger[d["id"]])},
        "envelope": {
            "runs_with_climate_targets": c["envelope"]["runs_with_climate_targets"],
            "automations_with_climate_targets": len({r["id"] for r, _, _ in cells}),
            "room_cells": len(cells),
            "caused_below_10_room_cells": c["envelope"]["below_10"],
            "caused_below_10_runs": len(viol_runs),
            "caused_below_10_automations": len({v["id"] for v in viol}),
            "caused_below_10_automations_flagged": sum(1 for i in {v["id"] for v in viol} if flag_of[i]),
            "caused_below_10_repositories": len({repo_of[v["id"]] for v in viol}),
            "caused_below_10_room_cells_by_city": dict(Counter(city(v["env_key"]) for v in viol)),
            "caused_below_10_runs_by_city": dict(Counter(city(k) for _, k in viol_runs)),
            "caused_below_10_by_date": dict(Counter(month(v["env_key"]) for v in viol)),
            "caused_below_10_min_c": {"min": min(v["min_c"] for v in viol), "max": max(v["min_c"] for v in viol),
                                      "median": statistics.median(v["min_c"] for v in viol)} if viol else None,
            "caused_below_10_baseline_min_c_lowest": min(v["baseline_min_c"] for v in viol) if viol else None,
            "caused_below_10_delta_min_c": {"min": min(v["delta_min_c"] for v in viol),
                                            "max": max(v["delta_min_c"] for v in viol)} if viol else None,
            "caused_above_30_room_cells": c["envelope"]["above_30"],
            "breached_below_10_room_cells": c["envelope"]["breached_below_10"],
            "breached_above_30_room_cells": c["envelope"]["breached_above_30"],
            "breached_above_30_runs": len({(r["id"], r["env_key"]) for r, _, _ in hot}),
            "breached_above_30_automations": len({r["id"] for r, _, _ in hot}),
            "breached_above_30_with_baseline_above_30": sum(
                1 for _, _, v in hot if v["baseline_max_c"] is not None and v["baseline_max_c"] > 30),
            "breached_above_30_room_cells_by_city": dict(Counter(city(r["env_key"]) for r, _, _ in hot)),
            "caveat": c["envelope"]["caveat"],
        },
    }


WORKERS = 6


def main() -> None:
    global WORKERS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lab", type=Path, default=Path.home() / "vesper-lab" / "edt")
    ap.add_argument("--commit", default="dc119cd")
    ap.add_argument("--workers", type=int, default=6, help="the sweeps' --workers, for the timing record")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    WORKERS = a.workers
    out_path = a.out or Path("results/edt") / f"summary-{a.commit}.json"
    lab, c = a.lab, a.commit

    rungs, envs4, repo_of, kinds_of, sun_trigger = {}, None, {}, {}, {}
    for rung in (*RUNGS, *EXTRA_SWEEPS):
        f = lab / (f"sweep-rung{rung}-{c}.json" if rung in RUNGS else f"sweep-{rung}-{c}.json")
        if not f.exists():
            if rung == "4":
                raise SystemExit(f"no rung-4 sweep: {f}")
            continue
        rungs[rung], raw = sweep_summary(f, f.with_suffix(".log"))
        if rung == "4":
            envs4 = raw["envs"]
            repo_of = {i: a["repo"] for i, a in raw["automations"].items()}  # in memory only, never written
            kinds_of = {i: a["kinds"] for i, a in raw["automations"].items()}
            sun_trigger = {i: has_sun_trigger(a["bound"]) for i, a in raw["automations"].items() if a["bound"]}
        del raw
        print(f"rung {rung}: {rungs[rung]['yield']}, flagged {rungs[rung]['flagged']['n']}", flush=True)
    labels = {e["key"]: env_label(e) for e in envs4}

    summary = {
        "commit": c,
        "protocol": "docs/experiments/edt-protocol.md (sections 1-7, addenda 1-6)",
        "corpus": "45,208 automations from 1,186 public Home Assistant repositories (1,123 of them with automations); "
                  "counts are over automations "
                  "de-duplicated by canonical hash (results/edt/census-20261104.json)",
        "environments": {"n": len(envs4), "cities": sorted({e["city"] for e in envs4}),
                         "dates": sorted({e["date"] for e in envs4}),
                         "north_deg": sorted({e["north_deg"] for e in envs4})},
        "rungs": rungs,
        "binding_audit": {
            "source": "protocol addendum 2, A2.5 (the audit itself names corpus entities and stays local)",
            "pass_1": {"ambient_sensor_names": 63, "mis_binds_found_and_fixed": 11, "classes": 5},
            "pass_2": {"ambient_sensor_names": 54, "correct": 47, "correct_by_convention": 9, "residual": 6,
                       "uncertain": 1},
        },
    }
    if (lab / f"ladder-{c}.json").exists():
        summary["ladder"] = json.loads((lab / f"ladder-{c}.json").read_text())
    if (lab / f"cover-rung4-{c}.json").exists():
        summary["cover"] = cover_summary(lab / f"cover-rung4-{c}.json", labels, lab / f"posthoc-setcover-{c}.txt",
                                         kinds_of)
    if (lab / f"confirm-rung4-{c}.json").exists():
        summary["closed_loop"] = confirm_summary(lab / f"confirm-rung4-{c}.json", {e["key"]: e for e in envs4},
                                                 repo_of, sun_trigger)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
