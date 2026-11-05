#!/usr/bin/env python3
"""r7b_room_terms.py -- VESPER revision R7-B (post hoc, protocol Addendum 13): the room-model terms behind the flags
(fourth internal review, items 6(a), 6(b) and 11(b); 6(c) is a fact read from the repository, no run).

Parts (each writes its raw results to --scratch, local only; `report` assembles the committed aggregates):

- `corners`  A13.1: the replay set (the frame relation's 199 flags, the clock relation's 790, and every run automation
             that reads a temperature, a humidity or an illuminance) replayed open loop (the sweep's own worker, the
             device reporting rule of 8fcd672) in all 48 environments against traces recorded afresh at each of R4-B's
             120 corners (internal gains 0/2/5 W/m², roof and ground off/on, warm-up 1/5/10 days/periodic, heat
             capacity 80/110/165/260/370 kJ/(m² K)); the nominal corner is the reference (the model as built).
- `offsets`  A13.2: the same replay set at the indoor-temperature offset INDOOR_T_OFFSET_C in {-1.7, -0.9, +2, +7} K
             (registered) and {+1.7, +0.9, -2, -7} K (the opposite signs, secondary).
- `lux`      A13.4: the 67 run automations that read an illuminance, in a home with a second illuminance sensor
             (LUX_PRODUCT, a change-reporting light sensor of the same catalog) beside every RTCGQ11LM: once as bound
             (the reference) and once with every RTCGQ11LM illuminance entity they name replaced by the new sensor's.
- `report`   results/edt/r7b-<commit>.json: aggregates only (counts, environment labels, corner labels): no automation,
             repository or entity name, no hash, no path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r7b_room_terms.py --part P [--lab ~/vesper-lab/edt]
        [--catalog catalog/vds] [--workers 4] [--scratch DIR] [--settings a,b,...]

Inputs (local, never committed): --lab/sweep-rung4-8fcd672.json and sweep-rung2.5-8fcd672.json, --lab/work-8fcd672
(its home and cached weather).
"""
from __future__ import annotations

import argparse
import copy
import json
import pickle
import re
import shutil
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import r4b_error_budget as r4b  # noqa: E402  (its corners, pool, loader and dump, unchanged)
import r6b_physics as r6b  # noqa: E402  (its trace worker and relations, unchanged)

COMMIT = "8fcd672"
RULE = "device"  # the twins' reporting rule 8fcd672 ran under
OFFSETS_PRIMARY = (-1.7, -0.9, 2.0, 7.0)  # the review's residuals (model minus measured), K
OFFSETS_SECONDARY = (1.7, 0.9, -2.0, -7.0)  # their opposite signs: the correction each residual implies
LUX_PRODUCT = "zigbee:tuya:s-lux-zb"  # L1 "Light sensor": illuminance by the catalog's change rule, no occupancy
LUX_REFERENCE = "zigbee:aqara:rtcgq11lm"  # the standard home's illuminance sensor (reported with motion, A6.9)
FAMILIES = ("frame_flags", "frame_orientation_witnesses", "frame_temperature_flags", "frame_humidity_flags",
            "frame_room_model_flags", "clock_flags", "clock_outcome_orientation_witnesses")
FACTORS = (("gains_w_m2", r4b.GAINS), ("roof_ground", r4b.FABRIC), ("warm_up", r4b.WARMUPS),
           ("capacity_kj_m2k", r4b.CAPACITIES))


# ---- settings --------------------------------------------------------------------------------------------------------

def setting_name(corner) -> str:
    return "ref" if tuple(corner) == tuple(r4b.NOMINAL) else f"c-{r4b.corner_label(corner)}"


def corner_settings() -> dict[str, dict]:
    """R4-B's 120 corners as open-loop settings; the nominal corner (every knob at its default) is `ref`."""
    return {setting_name(c): ({} if tuple(c) == tuple(r4b.NOMINAL) else r4b.corner_knobs(c)) for c in r4b.ALL_CORNERS}


def corner_of(name: str):
    return {setting_name(c): c for c in r4b.ALL_CORNERS}[name]


def offset_settings(values) -> dict[str, dict]:
    return {f"tin{v:+g}": {"INDOOR_T_OFFSET_C": float(v)} for v in values}


# ---- the bracket's sets ----------------------------------------------------------------------------------------------

def core_and_possible(members: dict[str, set], published: set, ref: str = "ref") -> dict:
    """Over every setting (the reference included): the robust core (members at every setting) and the possible set
    (members at any), with the published membership beside them."""
    sets = list(members.values())
    core, possible = set.intersection(*sets), set.union(*sets)
    counts = [len(s) for s in sets]
    return {"published": len(published), "robust_core": len(core), "possible_set": len(possible),
            "lost_somewhere": len(published - core), "gained_somewhere": len(possible - published),
            "per_setting_min": min(counts), "per_setting_max": max(counts),
            "reference_equals_published": members.get(ref) == published, "settings": len(sets)}


def frame_orientation_witness(outs: dict, first_t: dict, frames) -> bool:
    """Under the frame relation: two environments of one city and date that differ only in the plan's bearing over
    which the automation's frame-restricted divergences are not empty (r4a's one-axis witness, the north axis)."""
    from vesper.edt.oracle import frame_restricted_divergences
    by_place: dict[tuple, list] = defaultdict(list)
    for e in outs:
        by_place[(e.city, e.date, e.rung)].append(e)
    for group in by_place.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.north_deg != b.north_deg and frame_restricted_divergences(outs, first_t, frames, [a, b]):
                    return True
    return False


def memberships(bound: dict, outcomes: dict[str, dict], first_t: dict[str, float | None], by_key: dict, envs) -> dict:
    """One automation's relations and witnesses from its outcomes by env key (as the sweep computes them)."""
    from scripts.edt_analysis.home_variation import orientation_witness
    from vesper.edt.oracle import frames_of
    from vesper.edt.types import Outcome
    clock, frame = r6b.relations(bound, outcomes, first_t, by_key)
    outs = {by_key[k]: Outcome(**o) for k, o in outcomes.items()}
    ft = {by_key[k]: first_t.get(k) for k in outcomes}
    return {"clock": clock, "frame": frame,
            "fo": bool(frame) and frame_orientation_witness(outs, ft, frames_of(bound)),
            "co": bool(clock) and orientation_witness(outs, envs)}


def family_sets(rec: dict[str, dict], groups: dict[str, set]) -> dict[str, set]:
    """Each family's members from one setting's membership records (automation id -> memberships)."""
    frame = {i for i, m in rec.items() if m["frame"]}
    return {"frame_flags": frame,
            "frame_orientation_witnesses": {i for i, m in rec.items() if m["fo"]},
            "frame_temperature_flags": frame & groups["temperature"],
            "frame_humidity_flags": frame & groups["humidity"],
            "frame_room_model_flags": (frame & groups["sensor"]) - groups["era5_only"],
            "clock_flags": {i for i, m in rec.items() if m["clock"]},
            "clock_outcome_orientation_witnesses": {i for i, m in rec.items() if m["co"]}}


# ---- the second illuminance product ----------------------------------------------------------------------------------

def _pattern(mapping: dict[str, str]):
    keys = sorted(mapping, key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])")


def swap_entities(obj, mapping: dict[str, str]):
    """A copy of `obj` (a bound automation) with every whole entity id in `mapping` replaced, in values and keys,
    templates included; nothing else changes."""
    if not mapping:
        return copy.deepcopy(obj)
    pat = _pattern(mapping)

    def walk(x):
        if isinstance(x, str):
            return pat.sub(lambda m: mapping[m.group(1)], x)
        if isinstance(x, dict):
            return {walk(k): walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(obj)


def swap_count(obj, mapping: dict[str, str]) -> int:
    """How many entity ids `swap_entities` would replace."""
    if not mapping:
        return 0
    pat = _pattern(mapping)
    return len(pat.findall(json.dumps(obj)))


def augment_home(src: Path, dst: Path, product: str, catalog_dir: str) -> tuple[Path, dict[str, str]]:
    """A copy of the EDT home at `src` (its directory) with a `product` sensor named `<room>_lux1` beside every
    `<room>_lux0`, at the same place; (its home.yaml, {lux0 illuminance entity: lux1 illuminance entity})."""
    import yaml

    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    from vesper.twins.home import load_catalog
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("traces", "looks", "weather"))
    data = yaml.safe_load((dst / "home.yaml").read_text())
    lux0 = [d for d in data["devices"] if d["name"].endswith("_lux0")]
    data["devices"] += [{"product": product, "name": d["name"][:-1] + "1", "at": copy.deepcopy(d["at"])} for d in lux0]
    (dst / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    ids = entity_ids(load_lab_home(dst / "home.yaml", load_catalog(Path(catalog_dir))))
    mapping = {ids[d["name"]]["illuminance"]: ids[d["name"][:-1] + "1"]["illuminance"] for d in lux0}
    return dst / "home.yaml", mapping


# ---- inputs ----------------------------------------------------------------------------------------------------------

def groups_of(d: dict, s25: dict | None) -> dict[str, set]:
    from vesper.edt.oracle import SENSED_KINDS
    run = d["run"]
    out = {k: {i for i in run if k in run[i]["kinds"]} for k in ("temperature", "humidity", "illuminance")}
    out["sensor"] = {i for i in run if set(SENSED_KINDS) & set(run[i]["kinds"])}
    if s25 is not None:  # the frame flags still flagged at rung 2.5 (R4-A's ERA5-only partition)
        a25 = s25["automations"]
        out["era5_only"] = {i for i in run if run[i]["frame_divergences"] and i in out["sensor"]
                            and a25.get(i, {}).get("frame_divergences")}
    return out


def replay_set(d: dict) -> list[str]:
    run, g = d["run"], groups_of(d, None)
    flagged = {i for i, a in run.items() if a["divergences"] or a["frame_divergences"]}
    return sorted(flagged | g["temperature"] | g["humidity"] | g["illuminance"])


def part_run(d, a, scratch: Path, table: dict[str, dict], names: list[str], bound_file: Path, home: str,
             tag: str) -> None:
    """Each named setting: 48 traces recorded afresh under its knobs, the bound file replayed on them (R6-B's worker),
    the outcomes and first firing instants kept; the setting's traces are then removed (the pickle is the record)."""
    weather = Path(d["cache"]) / "weather"
    for name in names:
        out = scratch / f"ol-{tag}-{name}.pkl"
        if out.is_file():
            print("have", out.name, flush=True)
            continue
        cache = scratch / f"cache-{tag}-{name}"
        if not (cache / "weather").is_dir():
            shutil.copytree(weather, cache / "weather")
        jobs = [{"setting": name, "knobs": table[name], "env": e, "home": home, "cache": str(cache),
                 "bound_file": str(bound_file), "catalog": a.catalog, "reporting_rule": RULE}
                for e in d["sweep"]["envs"]]
        t0 = time.monotonic()
        done = r4b._pool(r6b._trace_job, jobs, a.workers, name)
        compact = [(key, outcomes, secs, failure, first_t) for _s, key, outcomes, _ref, secs, failure, first_t in done]
        r4b._dump(out, {"done": compact, "knobs": table[name], "elapsed_s": time.monotonic() - t0})
        shutil.rmtree(cache)
        print(f"{tag} {name}: {len(jobs)} environments in {time.monotonic() - t0:.0f} s", flush=True)


def _load_setting(path: Path) -> tuple[dict, dict, dict]:
    p = pickle.loads(path.read_bytes())
    outs, firsts = defaultdict(dict), defaultdict(dict)
    for key, outcomes, _secs, _fail, first_t in p["done"]:
        for aid, x in outcomes.items():
            outs[aid][key] = x
            firsts[aid][key] = first_t.get(aid)
    meta = {"elapsed_s": round(p["elapsed_s"], 1), "failed_environments": sum(1 for x in p["done"] if x[3]),
            "knobs": p["knobs"]}
    return outs, firsts, meta


def _published_records(d: dict, ids: list[str], by_key: dict, envs) -> dict[str, dict]:
    run = d["run"]
    return {i: memberships(run[i]["bound"], run[i]["outcomes"],
                           {k: (r or {}).get("first_t") for k, r in (run[i].get("rich") or {}).items()},
                           by_key, envs) for i in ids}


def _records(d, ids, outs, firsts, by_key, envs) -> dict[str, dict]:
    return {i: memberships(d["run"][i]["bound"], outs.get(i, {}), firsts.get(i, {}), by_key, envs) for i in ids}


# ---- report ----------------------------------------------------------------------------------------------------------

def _check_reference(d, ids, outs, pub_rec, ref_rec) -> dict:
    run = d["run"]
    return {"outcomes": sum(len(run[i]["outcomes"]) for i in ids),
            "outcome_mismatches_exact": sum(1 for i in ids for k, o in run[i]["outcomes"].items()
                                            if outs.get(i, {}).get(k) != o),
            "clock_kind_mismatches": sum(1 for i in ids if ref_rec[i]["clock"] != run[i]["divergences"]),
            "frame_kind_mismatches": sum(1 for i in ids if ref_rec[i]["frame"] != run[i]["frame_divergences"]),
            "membership_mismatches": sum(1 for i in ids if ref_rec[i] != pub_rec[i])}


def _bracket(sets_by_setting: dict[str, dict[str, set]], published: dict[str, set], recs: dict, pub_rec: dict) -> dict:
    out = {}
    for fam in FAMILIES:
        out[fam] = core_and_possible({n: s[fam] for n, s in sets_by_setting.items()}, published[fam])
    frame_pub = published["frame_flags"]
    out["frame_flags"]["kinds_changed_somewhere"] = sum(
        1 for i in frame_pub if any(recs[n][i]["frame"] != pub_rec[i]["frame"] for n in recs))
    return out


def _marginals(sets_by_setting: dict[str, dict[str, set]]) -> dict:
    """Each family's mean count per level of each corner factor (over the other factors), and the extreme corners."""
    out = {}
    for fam in FAMILIES:
        counts = {n: len(s[fam]) for n, s in sets_by_setting.items()}
        by = {}
        for k, (factor, levels) in enumerate(FACTORS):
            by[factor] = {str(lv): round(statistics.mean(c for n, c in counts.items() if corner_of(n)[k] == lv), 2)
                          for lv in levels}
        lo, hi = min(counts.values()), max(counts.values())
        out[fam] = {"mean_by_level": by, "min": lo, "max": hi,
                    "corners_at_min": sorted(r4b.corner_label(corner_of(n)) for n, c in counts.items() if c == lo),
                    "corners_at_max": sorted(r4b.corner_label(corner_of(n)) for n, c in counts.items() if c == hi)}
    return out


def report_bracket(d, scratch: Path, tag: str, names: list[str], groups: dict, ids: list[str]) -> dict:
    from vesper.edt.cover import _envs
    envs = _envs(d["sweep"])
    by_key = {e.key: e for e in envs}
    pub_rec = _published_records(d, ids, by_key, envs)
    published = family_sets(pub_rec, groups)
    recs, sets, meta, ref_outs = {}, {}, {}, None
    for n in names:
        path = scratch / f"ol-{tag}-{n}.pkl"
        outs, firsts, meta[n] = _load_setting(path)
        recs[n] = _records(d, ids, outs, firsts, by_key, envs)
        sets[n] = family_sets(recs[n], groups)
        if n == "ref":
            ref_outs = outs
    out = {"settings_run": len(names), "per_setting": {n: {"counts": {f: len(s[f]) for f in FAMILIES}, **meta[n]}
                                                       for n, s in sets.items()},
           "published": {f: len(s) for f, s in published.items()}}
    if ref_outs is not None:
        out["reference_against_published"] = _check_reference(d, ids, ref_outs, pub_rec, recs["ref"])
    out["bracket"] = _bracket(sets, published, recs, pub_rec)
    return out, sets, recs, published, pub_rec


def report_corners(d, scratch, groups, ids) -> dict:
    names = [n for n in corner_settings() if (scratch / f"ol-c-{n}.pkl").is_file()]
    out, sets, *_ = report_bracket(d, scratch, "c", names, groups, ids)
    if len(names) == 120:
        out["marginals"] = _marginals(sets)
    out["per_setting"] = {(r4b.corner_label(corner_of(n))): v for n, v in out["per_setting"].items()}
    return out


def report_offsets(d, scratch, groups, ids) -> dict:
    out = {}
    for label, vals in (("registered", OFFSETS_PRIMARY), ("both_signs", OFFSETS_PRIMARY + OFFSETS_SECONDARY)):
        names = ["ref", *[n for n in offset_settings(vals) if (scratch / f"ol-t-{n}.pkl").is_file()]]
        if (scratch / "ol-t-ref.pkl").is_file() and len(names) > 1:
            out[label], *_ = report_bracket(d, scratch, "t", names, groups, ids)
    return out


def report_lux(d, scratch: Path) -> dict | None:
    if not all((scratch / f"ol-lux-{n}.pkl").is_file() for n in ("ref", "swap")):
        return None
    from vesper.edt.cover import _envs
    envs = _envs(d["sweep"])
    by_key = {e.key: e for e in envs}
    meta_in = json.loads((scratch / "lux-meta.json").read_text())
    ids = sorted(groups_of(d, None)["illuminance"])
    pub_rec = _published_records(d, ids, by_key, envs)
    outs, recs, meta = {}, {}, {}
    for n in ("ref", "swap"):
        o, f, meta[n] = _load_setting(scratch / f"ol-lux-{n}.pkl")
        outs[n] = o
        bounds = json.loads((scratch / f"bound-lux-{n}.json").read_text())
        recs[n] = {i: memberships(bounds[i], o.get(i, {}), f.get(i, {}), by_key, envs) for i in ids}
    ref, swap = recs["ref"], recs["swap"]

    def flips(rel):
        pub = {i for i in ids if ref[i][rel]}
        new = {i for i in ids if swap[i][rel]}
        return {"flagged_reference": len(pub), "flagged_swapped": len(new), "lost": len(pub - new),
                "gained": len(new - pub), "kinds_changed": sum(1 for i in ids if ref[i][rel] != swap[i][rel]),
                "flagged_reference_among_swapped": len(pub & set(meta_in["swapped_ids"])),
                "flagged_swapped_among_swapped": len(new & set(meta_in["swapped_ids"]))}
    fired = {n: sum(1 for i in ids if any(o["fired"] for o in outs[n].get(i, {}).values())) for n in outs}
    return {"readers": len(ids), "swapped": len(meta_in["swapped_ids"]), "entities_replaced": meta_in["replaced"],
            "sensors_added": meta_in["sensors_added"], "product": LUX_PRODUCT, "replaced_product": LUX_REFERENCE,
            "reference_against_published": _check_reference(d, ids, outs["ref"], pub_rec, ref),
            "fire_somewhere": fired,
            "outcomes_changed": sum(1 for i in ids for k, o in outs["ref"].get(i, {}).items()
                                    if outs["swap"].get(i, {}).get(k) != o),
            "automations_with_an_outcome_change": sum(1 for i in ids if any(
                outs["swap"].get(i, {}).get(k) != o for k, o in outs["ref"].get(i, {}).items())),
            "frame": flips("frame"), "clock": flips("clock"),
            "elapsed_s": {n: m["elapsed_s"] for n, m in meta.items()},
            "failed_environments": {n: m["failed_environments"] for n, m in meta.items()}}


# ---- main ------------------------------------------------------------------------------------------------------------

def _bound_file(d, scratch: Path, ids: list[str]) -> Path:
    path = scratch / "bound-replay.json"
    path.write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    return path


def part_lux(d, a, scratch: Path) -> None:
    ids = sorted(groups_of(d, None)["illuminance"])
    home_yaml, mapping = augment_home(Path(d["home"]).parent, scratch / "home-lux" / "edt", LUX_PRODUCT, a.catalog)
    ref = {i: d["run"][i]["bound"] for i in ids}
    swap = {i: swap_entities(b, mapping) for i, b in ref.items()}
    (scratch / "bound-lux-ref.json").write_text(json.dumps(ref))
    (scratch / "bound-lux-swap.json").write_text(json.dumps(swap))
    (scratch / "lux-meta.json").write_text(json.dumps({
        "swapped_ids": sorted(i for i in ids if swap[i] != ref[i]),
        "replaced": sum(swap_count(b, mapping) for b in ref.values()), "sensors_added": len(mapping)}))
    for n in ("ref", "swap"):
        part_run(d, a, scratch, {n: {}}, [n], scratch / f"bound-lux-{n}.json", str(home_yaml), "lux")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("corners", "offsets", "lux", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r7-B/scratch"))
    ap.add_argument("--settings", default="", help="corners/offsets: comma-separated subset of the settings")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    import vesper
    print("vesper from", Path(vesper.__file__).parent, flush=True)
    d = r4b.load(lab, COMMIT)
    only = [x for x in a.settings.split(",") if x]
    if a.part == "corners":
        table = corner_settings()
        names = only or ["ref", *[n for n in table if n != "ref"]]
        part_run(d, a, scratch, table, names, _bound_file(d, scratch, replay_set(d)), d["home"], "c")
    elif a.part == "offsets":
        table = {"ref": {}, **offset_settings(OFFSETS_PRIMARY), **offset_settings(OFFSETS_SECONDARY)}
        names = only or list(table)
        part_run(d, a, scratch, table, names, _bound_file(d, scratch, replay_set(d)), d["home"], "t")
    elif a.part == "lux":
        part_lux(d, a, scratch)
    else:
        return report(a, lab, scratch, d)
    return 0


def report(a, lab: Path, scratch: Path, d: dict) -> int:
    import subprocess
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                             check=True).stdout.strip()
    s25 = json.loads((lab / f"sweep-rung2.5-{COMMIT}.json").read_text())
    groups = groups_of(d, s25)
    ids = replay_set(d)
    run, idset = d["run"], set(ids)
    out = {"analysis": "R7-B (protocol Addendum 13, post hoc): the open-loop screen over R4-B's 120 corners, the "
                       "indoor-temperature offset, the second illuminance product",
           "inputs": {"sweep": COMMIT, "reporting_rule": RULE}, "code_commit": commit7,
           "replay_set": {"automations": len(ids), "frame_flags": sum(1 for i in ids if run[i]["frame_divergences"]),
                          "clock_flags": sum(1 for i in ids if run[i]["divergences"]),
                          **{f"reads_{k}": len(groups[k] & idset) for k in ("temperature", "humidity", "illuminance")},
                          "sensor_reading": len(groups["sensor"] & idset),
                          "era5_only_frame_flags": len(groups["era5_only"])},
           "item6a_corners": report_corners(d, scratch, groups, ids),
           "item6b_indoor_offset": report_offsets(d, scratch, groups, ids),
           "item11b_illuminance": report_lux(d, scratch)}
    path = Path(a.out or f"results/edt/r7b-{commit7}.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
