#!/usr/bin/env python3
"""r8y_physics.py -- VESPER revision R8-Y (post hoc, protocol Addendum 18): structural physics runs (fifth internal
review, items 5 and 4(b)).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `setback`  A18.1, A18.2: R2-c's 22 setback automations closed loop (R4-B's worker) in the core four environments at
             the U corner U_wall {0.35, 0.6, 1.0} x U_glass {1.4, 2.0, 2.8} under the hallway thermostat (the model as
             built) and under per-room thermostats; and per-room at the nominal setting in the other 44 environments.
- `plans`    A18.3: the 199 frame flags of `8fcd672` replayed open loop (the sweep's worker) on traces recorded afresh
             in the standard home and in the generated plans seed 8, 9, 11 and 16.
- `report`   results/edt/r8y-<commit>.json: aggregates only (counts, temperatures, environment and generic room labels,
             anonymous indices): no automation, repository or entity name, no hash, no path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r8y_physics.py --part P [--lab ~/vesper-lab/edt]
        [--catalog catalog/vds] [--workers 4] [--scratch DIR]

Inputs (local, never committed): --lab/sweep-rung4-{120f4ca,8fcd672,r6d-5b54586}.json, A8.8's sweeps of plan-seed8
and mirror-ew, the work directories' homes and their cached weather (never fetched).
"""
from __future__ import annotations

import argparse
import json
import pickle
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "edt_analysis"))
sys.path.insert(0, str(ROOT / "scripts" / "revision"))
sys.path.insert(0, str(ROOT))
import r2c_setback_population as r2c  # noqa: E402
import r4b_error_budget as r4b  # noqa: E402

U_WALLS = (0.35, 0.6, 1.0)
U_GLASS = (1.4, 2.0, 2.8)
U_REF = (0.35, 1.4)  # the model as built
SETBACK_COMMIT = "120f4ca"  # R2-c's population and R4-B's / R6-B's path
FRAME_COMMIT = "8fcd672"
ENGINE_COMMIT = "r6d-5b54586"  # the engine-corrected sweep the current code reproduces (A13.6)
PLANS = {"standard": None, "seed8": "plan-seed8", "seed9": "plan-seed9", "seed11": "plan-seed11",
         "seed16": "plan-seed16"}
NEW_PLANS = ("seed9", "seed11", "seed16")
FULL_SWEEPS = {"seed8": "r6a-variant-plan-seed8-ba8a54c", "mirror": "r6a-variant-mirror-ew-ba8a54c"}


# ---- settings --------------------------------------------------------------------------------------------------------

def setting_label(per_room: bool, u_wall: float, u_glass: float) -> str:
    return f"{'room' if per_room else 'hall'}-W{u_wall:g}-G{u_glass:g}"


def setting_knobs(per_room: bool, u_wall: float, u_glass: float) -> dict:
    return {"U_WALL": float(u_wall), "U_WINDOW": float(u_glass), "PER_ROOM_THERMOSTAT": int(per_room)}


def setback_settings() -> dict[str, dict]:
    return {setting_label(p, w, g): setting_knobs(p, w, g) for p in (False, True) for w in U_WALLS for g in U_GLASS}


REF = setting_label(False, *U_REF)
ROOM = setting_label(True, *U_REF)


# ---- per-run summaries -----------------------------------------------------------------------------------------------

def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _span(xs) -> list | None:
    xs = sorted(x for x in xs if x is not None)
    return [_r(xs[0]), _r(statistics.median(xs)), _r(xs[-1])] if xs else None


def coldest_room(row: dict) -> str | None:
    """The room with the lowest day 1-2 minimum with the automation (ties: the first room id)."""
    cells = row["_cells"]
    return min(sorted(cells), key=lambda r: cells[r][0]) if cells else None


def summary(rows: list[dict], keys: set, n_auto: int) -> dict:
    sel = [r for r in rows if r["_key"] in keys]
    hit = [r for r in sel if r["caused"]]
    return {"breaching_runs": len(hit), "denominator": n_auto * len(keys), "runs": len(sel),
            "caused_cells": sum(r["caused_cells"] for r in hit),
            "automations": len({r["automation"] for r in hit}),
            "by_city": dict(sorted(Counter(r["city"] for r in hit).items())),
            "by_env": dict(sorted(Counter(r["env"] for r in hit).items())),
            "coldest_caused_room_per_run_c": _span(r["coldest_caused_room_c"] for r in hit),
            "coldest_room_per_run_c": _span(r["coldest_room_c"] for r in sel),
            "baseline_coldest_room_c": _r(min((r["baseline_coldest_room_c"] for r in sel
                                               if r["baseline_coldest_room_c"] is not None), default=None)),
            "largest_drop_k": _r(max((r["largest_drop_k"] for r in sel if r["largest_drop_k"] is not None),
                                     default=None)),
            "runs_with_a_setback": sum(1 for r in sel if r["_setback"]),
            "coldest_room_identity": dict(sorted(Counter(coldest_room(r) for r in sel).items())),
            "caused_cells_by_room": dict(sorted(Counter(room for r in hit for room, c in r["_cells"].items()
                                                        if c[2]).items())),
            "runs_set": sorted((r["automation"], r["_key"]) for r in hit)}


def identity_shift(a: list[dict], b: list[dict]) -> dict:
    """The same runs under two settings: how many change their coldest room, and the coldest room's change (b - a)."""
    ia = {(r["_aid"], r["_key"]): r for r in a}
    ib = {(r["_aid"], r["_key"]): r for r in b}
    both = sorted(set(ia) & set(ib))
    changed = [k for k in both if coldest_room(ia[k]) != coldest_room(ib[k])]
    either = [k for k in both if ia[k]["caused"] or ib[k]["caused"]]
    return {"runs": len(both), "coldest_room_changed": len(changed), "breaching_in_either": len(either),
            "coldest_room_changed_among_breaching_in_either": len(set(changed) & set(either)),
            "coldest_room_change_c": _span(ib[k]["coldest_room_c"] - ia[k]["coldest_room_c"] for k in both),
            "transitions": dict(sorted(Counter(f"{coldest_room(ia[k])}->{coldest_room(ib[k])}"
                                               for k in changed).items()))}


def turns_thermostat_off(bound: dict) -> bool:
    """Whether the automation's climate actions turn a thermostat off (`climate.turn_off`, or an hvac_mode of off):
    the diagnostic of A18.5, read statically from the bound automation."""
    from vesper.edt.setback import climate_steps
    for svc, step in climate_steps(bound):
        data = {**(step.get("data") or {}), **(step.get("data_template") or {})}
        if svc == "climate.turn_off" or str(data.get("hvac_mode", "")).strip().lower() == "off":
            return True
    return False


def off_split(rows: list[dict], keys: set, off: set) -> dict:
    """The breaching runs and their coldest caused rooms, apart for the automations (by name) that turn the
    thermostat off and those that only set it back."""
    out = {}
    for tag, pick in (("setpoint_only", lambda n: n not in off), ("turns_thermostat_off", lambda n: n in off)):
        hit = [r for r in rows if r["_key"] in keys and r["caused"] and pick(r["automation"])]
        out[tag] = {"breaching_runs": len(hit),
                    "coldest_caused_room_per_run_c": _span(r["coldest_caused_room_c"] for r in hit)}
    return out


def nested(sets: list[set]) -> bool:
    """Whether each set holds the one before it (the order the axis gives)."""
    return all(x <= y for x, y in zip(sets, sets[1:], strict=False))


def core_over(published: set, plans: dict[str, set]) -> dict:
    """The published members held in every plan (the core), in any plan or published (the possible set), and how many
    plans hold each published member."""
    held = Counter(sum(1 for s in plans.values() if i in s) for i in published)
    union = set().union(*plans.values()) if plans else set()
    return {"of": len(published), "plans": sorted(plans),
            "core": sum(1 for i in published if all(i in s for s in plans.values())),
            "possible": len(published | union), "new_in_any": len(union - published),
            "held_in": {str(k): held.get(k, 0) for k in range(len(plans) + 1)}}


# ---- A18.1, A18.2: closed loop ---------------------------------------------------------------------------------------

def setback_jobs(d: dict, catalog: str) -> list[dict]:
    core, keys48 = r4b.r2c_envs(d), sorted(d["envs"])
    plan = [(label, kn, core) for label, kn in setback_settings().items()]
    plan.append((ROOM, setback_settings()[ROOM], [k for k in keys48 if k not in core]))
    js = []
    for label, kn, keys in plan:
        for j in r4b.corner_jobs(d, [r4b.NOMINAL], keys, catalog):
            j["knobs"] = {**j["knobs"], **kn}
            j["tag"] = (label, j["tag"][1], j["tag"][2])
            js.append(j)
    return js


def part_setback(a, scratch: Path) -> None:
    out = scratch / "setback.pkl"
    if out.is_file():
        print("have", out.name, flush=True)
        return
    d = r4b.load(Path(a.lab), SETBACK_COMMIT)
    jobs = setback_jobs(d, a.catalog)
    print(f"setback: {len(jobs)} runs", flush=True)
    t0 = time.monotonic()
    done = r4b._pool(r4b._closed_job, jobs, a.workers, "setback")
    r4b._dump(out, {"done": done, "elapsed_s": time.monotonic() - t0})


def h_by_corner(d: dict) -> dict:
    from vesper.env import indoor
    from vesper.env.indoor import room_models
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    from vesper.twins.home import load_catalog
    import r6b_physics as r6b
    home = load_lab_home(Path(d["home"]), load_catalog(Path("catalog/vds")))
    models = room_models(home.map, load_windows(Path(d["home"]).parent))
    out = {}
    for w in U_WALLS:
        for g in U_GLASS:
            with indoor.knobs(U_WALL=w, U_WINDOW=g):
                h = r6b.h_breakdown(models, ach_values=(indoor.ACH,))
            out[f"W{w:g}-G{g:g}"] = {k: h[k] for k in ("total_w_k", "walls_w_k", "glass_w_k", "ventilation_w_k",
                                                       "shares")}
    return out


def report_setback(scratch: Path, lab: Path) -> dict:
    from vesper.edt.setback import low_setpoints
    d = r4b.load(lab, SETBACK_COMMIT)
    r2c_pub = json.loads((ROOT / "results/edt/setback-population-r2c-120f4ca.json").read_text())
    p = pickle.loads((scratch / "setback.pkl").read_bytes())
    rows = r2c.judge(p["done"], d["envs"], d["names"], Path(d["cache"]))
    n_auto, core, all48 = len(d["pop"]), set(r4b.r2c_envs(d)), set(d["envs"])
    settings = {}
    for label in setback_settings():
        settings[label] = summary(rows.get(label, []), core, n_auto)
    # A18.1's check: the hallway reference against R2-c step 2 (the same runs, coldest rooms, cells, baselines)
    pub = {(b["automation"], b["env"]): b for b in r2c_pub["step2_closed_loop"]["breaching_runs"]}
    mine = {(r["automation"], r["env"]): r for r in rows[REF] if r["caused"] and r["_key"] in core}
    fields = ("coldest_caused_room_c", "caused_cells", "baseline_coldest_room_c", "coldest_room_c", "largest_drop_k",
              "setback_h")
    diffs = sum(1 for k in set(pub) & set(mine) for f in fields if _r(pub[k][f]) != _r(mine[k][f]))
    check = {"r2c_breaching_runs_core": len(pub), "reference_breaching_runs_core": len(mine),
             "same_runs": set(pub) == set(mine), "field_mismatches": diffs, "fields_compared": list(fields)}
    # A18.1: the coldest room's identity, hallway against per-room, and the per-room depth below the setpoint
    shift = identity_shift([r for r in rows[REF] if r["_key"] in core], [r for r in rows[ROOM] if r["_key"] in core])
    setpoint = {d["names"][aid]: min(s.value for s in low_setpoints(d["run"][aid]["bound"])) for aid in d["pop"]}
    below = [r["coldest_room_c"] - setpoint[r["automation"]] for r in rows[ROOM] if r["_key"] in core and r["_setback"]]
    room48 = summary(rows[ROOM], all48, n_auto)
    # A18.2: nesting along each U axis, per thermostat structure
    nest = {}
    for per_room in (False, True):
        tag = "room" if per_room else "hall"
        sets = {(w, g): set(settings[setting_label(per_room, w, g)]["runs_set"]) for w in U_WALLS for g in U_GLASS}
        nest[tag] = {"along_u_wall_at_each_u_glass": {f"G{g:g}": nested([sets[(w, g)] for w in U_WALLS])
                                                      for g in U_GLASS},
                     "along_u_glass_at_each_u_wall": {f"W{w:g}": nested([sets[(w, g)] for g in U_GLASS])
                                                      for w in U_WALLS}}
    nest["hall_subset_of_room_at_each_corner"] = {
        f"W{w:g}-G{g:g}": set(settings[setting_label(False, w, g)]["runs_set"])
        <= set(settings[setting_label(True, w, g)]["runs_set"]) for w in U_WALLS for g in U_GLASS}
    primary = [settings[setting_label(False, w, g)] for w in U_WALLS for g in U_GLASS] + [settings[ROOM]]
    every = list(settings.values())

    def restated(group):
        lows = [s["coldest_caused_room_per_run_c"] for s in group if s["coldest_caused_room_per_run_c"]]
        return {"breaching_runs_min": min(s["breaching_runs"] for s in group),
                "breaching_runs_max": max(s["breaching_runs"] for s in group),
                "coldest_caused_room_c_lowest": min((x[0] for x in lows), default=None),
                "coldest_caused_room_c_highest": max((x[2] for x in lows), default=None),
                "settings": len(group)}
    # A18.5 (a diagnostic written after the runs): the automations that turn the thermostat off, apart
    off = {d["names"][aid] for aid in d["pop"] if turns_thermostat_off(d["run"][aid]["bound"])}
    off_breaching = sorted({r["automation"] for label in settings for r in rows[label]
                            if r["_key"] in core and r["caused"] and r["automation"] in off})
    split = {label: off_split(rows[label], core, off) for label in settings}
    below_on = [r["coldest_room_c"] - setpoint[r["automation"]] for r in rows[ROOM]
                if r["_key"] in core and r["_setback"] and r["automation"] not in off]
    for s in [*settings.values(), room48]:
        s.pop("runs_set")
    return {"automations": n_auto, "core": sorted(r2c.label(r2c._env(d["envs"][k])) for k in core),
            "reference": REF, "per_room": ROOM, "check_reference_reproduces_r2c": check,
            "settings": settings, "h_by_corner_w_k": h_by_corner(d),
            "per_room_nominal_48": room48,
            "per_room_nominal_outside_core": room48["breaching_runs"] - settings[ROOM]["breaching_runs"],
            "identity_hallway_vs_per_room": shift,
            "per_room_coldest_room_minus_setpoint_k": _span(below),
            "nesting": nest, "restated_primary": restated(primary), "restated_all_18": restated(every),
            "a18_5_turns_thermostat_off": {"automations": len(off), "of_them_breaching_somewhere": len(off_breaching),
                                           "breaching_anonymous": off_breaching, "by_setting": split,
                                           "per_room_coldest_room_minus_setpoint_k_setpoint_only": _span(below_on)},
            "elapsed_s": round(p["elapsed_s"], 1)}


# ---- A18.3: the plans ------------------------------------------------------------------------------------------------

def frame_ids(d: dict) -> list[str]:
    return sorted(i for i, a in d["run"].items() if a.get("frame_divergences"))


def part_plans(a, scratch: Path) -> None:
    import r6b_physics as r6b

    from vesper.edt.home import build_edt_home
    d = r4b.load(Path(a.lab), FRAME_COMMIT)
    ids = frame_ids(d)
    bfile = scratch / f"bound-frame-{FRAME_COMMIT}.json"
    bfile.write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    weather = Path(d["cache"]) / "weather"
    for name in a.plans.split(","):
        out = scratch / f"plan-{name}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        work = scratch / f"work-{name}"
        home_yaml, _ = build_edt_home(work, a.catalog, PLANS[name])
        cache = work / "cache"
        if not (cache / "weather").is_dir():
            shutil.copytree(weather, cache / "weather")
        before = len(list(cache.glob("trace-*.jsonl.gz")))
        jobs = [{"setting": name, "knobs": {}, "env": e, "home": str(home_yaml), "cache": str(cache),
                 "bound_file": str(bfile), "catalog": a.catalog, "reporting_rule": "device"}
                for e in d["sweep"]["envs"]]
        t0 = time.monotonic()
        done = r4b._pool(r6b._trace_job, jobs, a.workers, name)
        r4b._dump(out, {"done": done, "ids": len(ids), "traces_before": before,
                        "traces_after": len(list(cache.glob("trace-*.jsonl.gz"))), "elapsed_s": time.monotonic() - t0})
        print(f"{name}: {len(jobs)} environments in {time.monotonic() - t0:.0f} s", flush=True)


def _compare(found: dict, ref: dict, ids: list[str]) -> dict:
    return {"frame_flag_mismatches": sum(1 for i in ids if bool(found[i]["frame"]) != bool(ref[i]["frame"])),
            "frame_kind_mismatches": sum(1 for i in ids if sorted(found[i]["frame"]) != sorted(ref[i]["frame"])),
            "witness_mismatches": sum(1 for i in ids if found[i]["witness"] != ref[i]["witness"])}


def report_plans(scratch: Path, lab: Path) -> dict:
    import r4a_analyses as r4a
    import r7a_analyses as r7a
    d = r4b.load(lab, FRAME_COMMIT)
    ids = frame_ids(d)
    envs = r4a.envs_of(d["sweep"])
    pairs = r7a.orient_pairs(envs)
    run = d["run"]
    pub = r7a.published_findings(d["sweep"], set(ids))
    pub_w = {i for i in ids if pub[i]["witness"]}
    kind = {i: r7a.read_kind(run[i]) for i in ids}
    found, meta = {}, {}
    for name in PLANS:
        path = scratch / f"plan-{name}.pkl"
        if not path.is_file():
            continue
        pk = pickle.loads(path.read_bytes())
        found[name] = r7a.setting_findings(d, ids, pk, envs, pairs)
        meta[name] = {"elapsed_s": round(pk["elapsed_s"], 1), "traces_recorded": pk["traces_after"],
                      "traces_before": pk["traces_before"],
                      "failed_environments": sum(1 for x in pk["done"] if x[5])}
    out = {"frame_flags": len(ids), "orientation_witnesses": len(pub_w),
           "witnesses_by_read_kind": dict(sorted(Counter(kind[i] for i in pub_w).items())), "runs": meta}
    checks = {}
    if "standard" in found:  # check (i)
        eng = json.loads((lab / f"sweep-rung4-{ENGINE_COMMIT}.json").read_text())["automations"]
        std = found["standard"]
        checks["standard_vs_8fcd672"] = {
            **_compare(std, pub, ids),
            "outcome_mismatches": sum(1 for i in ids for k, o in run[i]["outcomes"].items()
                                      if std[i]["outcomes"].get(k) != o),
            "outcomes": sum(len(run[i]["outcomes"]) for i in ids)}
        checks["standard_vs_5b54586_outcome_mismatches"] = sum(
            1 for i in ids for k, o in eng[i]["outcomes"].items() if std[i]["outcomes"].get(k) != o)
        del eng
    full = {}
    for name, sweep in FULL_SWEEPS.items():
        p = lab / f"sweep-rung4-{sweep}.json"
        if p.is_file():
            full[name] = r7a.published_findings(json.loads(p.read_text()), set(ids))
    if "seed8" in found and "seed8" in full:  # check (ii)
        checks["seed8_vs_a88_full_sweep"] = _compare(found["seed8"], full["seed8"], ids)
    out["checks"] = checks
    wit = {n: {i for i in ids if f[i]["witness"]} for n, f in found.items()}
    flags = {n: {i for i in ids if f[i]["frame"]} for n, f in found.items()}
    if "mirror" in full:
        wit["mirror"] = {i for i in ids if full["mirror"][i]["witness"]}
        flags["mirror"] = {i for i in ids if full["mirror"][i]["frame"]}
    per = {}
    for n in wit:
        f = found.get(n) or full[n]
        per[n] = {"frame_flags_kept": len(flags[n]),
                  "frame_flags_same_kinds": sum(1 for i in ids if sorted(f[i]["frame"]) == sorted(pub[i]["frame"])),
                  "witnesses_kept": len(wit[n] & pub_w), "witnesses_lost": len(pub_w - wit[n]),
                  "new_witnesses_among_199": len(wit[n] - pub_w),
                  "witnesses_kept_by_read_kind": {k: [sum(1 for i in pub_w & wit[n] if kind[i] == k),
                                                      sum(1 for i in pub_w if kind[i] == k)]
                                                  for k in ("temperature", "humidity", "neither")},
                  "source": "restricted replay" if n in found else "A8.8 full sweep"}
    out["per_plan"] = per
    groups = {"new_three": NEW_PLANS, "four_generated": ("seed8", *NEW_PLANS),
              "four_generated_and_mirror": ("seed8", *NEW_PLANS, "mirror")}
    out["core_over_plans"] = {g: {**core_over(pub_w, {n: wit[n] for n in ns}),
                                  "frame_flags": core_over(set(ids), {n: flags[n] for n in ns})}
                              for g, ns in groups.items() if all(n in wit for n in ns)}
    core3 = set.intersection(*(wit[n] for n in NEW_PLANS)) & pub_w if all(n in wit for n in NEW_PLANS) else set()
    out["core_new_three_by_read_kind"] = dict(sorted(Counter(kind[i] for i in core3).items()))
    pickle.dump({"witnesses": {n: sorted(v) for n, v in wit.items()}, "published": sorted(pub_w),
                 "core_new_three": sorted(core3)}, (scratch / "plan-witness-ids.pkl").open("wb"))
    return out


# ---- report ----------------------------------------------------------------------------------------------------------

def report(a, scratch: Path) -> int:
    lab = Path(a.lab)
    commit = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT).stdout.strip()
    res = {"analysis": "revision R8-Y (protocol Addendum 18)", "code_commit": commit,
           "inputs": {"setback": SETBACK_COMMIT, "frame": FRAME_COMMIT, "engine_reference": ENGINE_COMMIT,
                      "full_sweeps": sorted(FULL_SWEEPS.values())}}
    if (scratch / "setback.pkl").is_file():
        res["item5_setback"] = report_setback(scratch, lab)
    if any((scratch / f"plan-{n}.pkl").is_file() for n in PLANS):
        res["item4b_plans"] = report_plans(scratch, lab)
    out = Path(a.out) if a.out else ROOT / "results/edt" / f"r8y-{commit}.json"
    out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print("wrote", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("setback", "plans", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r8-Y/scratch"))
    ap.add_argument("--plans", default=",".join(PLANS), help="plans: comma-separated subset of " + ", ".join(PLANS))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    scratch = Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    if a.part == "setback":
        part_setback(a, scratch)
    elif a.part == "plans":
        part_plans(a, scratch)
    else:
        return report(a, scratch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
