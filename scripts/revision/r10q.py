#!/usr/bin/env python3
"""r10q.py -- VESPER revision R10-Q (post hoc, protocol Addendum 21): the seventh internal review's items 5, 6(a) and 4.

Parts (`beam` and `lux` write their raw results to --scratch, local only; `report` assembles the committed aggregates):

- `beam`     A21.3  the 67 run automations that read an illuminance, as bound, in the standard home, open loop in all
                    48 environments, at BEAM_LIGHT_SCALE 1 (the reference), 0.205, 0.250 and 0.289 (k̂ and its
                    interval), every trace recorded afresh (R7-B's code path).
- `lux`      A21.4  R7-B's change-reporting sensor home and swap, at DAYLIGHT_SCALE 1/2.49, 1/0.93, 1/3.49 (primary:
                    check D's residual corrected) and 2.49, 0.93, 3.49 (secondary: the literal factors), each at
                    BEAM_LIGHT_SCALE 1 and 0.25; and two anchors at the defaults (the home as bound, the swap).
- `report`   results/edt/r10q-<commit>.json: A21.1's geometry (read from the simulator's inputs), A21.2's trade-off
             curve (A6.7's and A8.1's scores, recomputed), and A21.3-A21.4's aggregates: counts, room labels and
             anonymous indices only; no automation, repository, entity name of the corpus, hash or path.

    PYTHONPATH=. nice -n 5 .venv/bin/python scripts/revision/r10q.py --part P [--workers 4]

Inputs (local, never committed): --lab/sweep-rung4-8fcd672.json, --lab/sweep-rung4-r6d-5b54586.json and
--lab/work-8fcd672 (its standard home and cached weather).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

COMMIT = "8fcd672"
BASE = "r6d-5b54586"  # A13.6's convention: the published memberships of the 67 are the engine-corrected sweep's
K_HAT = (0.205, 0.250, 0.289)  # the thermal validation's aperture and its interval (A9.4, A14.4)
RESIDUAL = (2.49, 0.93, 3.49)  # check D's residual after A9.6's three factors, model / measured: point, low, high
BEAMS_B = (1.0, 0.25)  # A21.4's beam term: as built and at k̂
RECALLS = (0.8, 0.9, 0.95)
PRECISIONS = (0.3, 0.5, 0.7)
RELATIONS = (("frame", "frame_divergences"), ("clock", "divergences"))
BOOT, SEED = 1000, 0
RHO_CP_MODEL = 0.34 * 3600  # J/(m³ K): the model's 0.34 Wh/(m³ K)
RHO_CP_TEXTBOOK = 1.2 * 1005.0  # J/(m³ K): air at 1.2 kg/m³ and 1,005 J/(kg K)


# ---- A21.2: the operating points ------------------------------------------------------------------------------------

def _blocks(scores, labels):
    """Over the thresholds the scores give (descending; tied scores pass together): each threshold, the automations
    passed and the positives among them."""
    s, y = np.asarray(scores, dtype=float), np.asarray(labels, dtype=int)
    order = np.argsort(-s, kind="stable")
    ss, tp = s[order], np.cumsum(y[order])
    ends = np.flatnonzero(np.r_[ss[1:] != ss[:-1], True])
    return ss[ends], ends + 1, tp[ends], int(y.sum())


def at_recall(scores, labels, r: float) -> dict:
    """At the highest threshold whose recall is at least `r` (the fewest automations passed): its threshold, passed
    count, positives, precision and recall; and the interpolated precision (the largest precision over every threshold
    with recall at least `r`)."""
    thr, passed, tp, total = _blocks(scores, labels)
    ok = np.flatnonzero(tp >= r * total - 1e-12)
    if total == 0 or not len(ok):
        return {"threshold": None, "passed": None, "tp": None, "precision": None, "recall": None,
                "interpolated_precision": None}
    i = int(ok[0])
    prec = tp / passed
    return {"threshold": float(thr[i]), "passed": int(passed[i]), "tp": int(tp[i]),
            "precision": float(prec[i]), "recall": float(tp[i] / total),
            "interpolated_precision": float(prec[ok].max())}


def at_precision(scores, labels, p: float) -> dict:
    """The largest recall over thresholds with precision at least `p`, ties to the higher precision (A1.4's
    `ladder.recall_at_precision`), with the automations passed; recall 0 and no threshold when none reaches `p`."""
    thr, passed, tp, total = _blocks(scores, labels)
    best = {"recall": 0.0, "precision": None, "threshold": None, "passed": 0, "tp": 0}
    if total == 0:
        return best
    for t, n, k in zip(thr, passed, tp, strict=True):
        prec, rec = k / n, k / total
        if prec >= p and (rec > best["recall"] or (rec == best["recall"] and best["precision"] is not None
                                                   and prec > best["precision"])):
            best = {"recall": float(rec), "precision": float(prec), "threshold": float(t), "passed": int(n),
                    "tp": int(k)}
    return best


def budget_share_ci(scores, labels, groups, r: float = 0.9, n: int = BOOT, seed: int = SEED) -> dict:
    """A 95 % percentile interval of the passed share at recall `r`: whole repositories resampled with replacement,
    the scores held fixed; a resample without a positive is not counted."""
    s, y = np.asarray(scores, dtype=float), np.asarray(labels, dtype=int)
    by: dict = {}
    for i, g in enumerate(groups):
        by.setdefault(g, []).append(i)
    keys = list(by)
    idx = [np.array(by[k]) for k in keys]
    rng = np.random.default_rng(seed)
    shares = []
    for _ in range(n):
        rows = np.concatenate([idx[j] for j in rng.integers(0, len(keys), len(keys))])
        if not y[rows].sum():
            continue
        p = at_recall(s[rows], y[rows], r)
        shares.append(p["passed"] / len(rows))
    lo, hi = (np.percentile(shares, [2.5, 97.5]).tolist() if shares else (None, None))
    return {"ci95": [lo, hi], "resamples_used": len(shares), "resamples": n, "seed": seed}


def _curve(scores, labels) -> dict:
    n = len(scores)
    return {"n": n, "n_flagged": int(np.sum(labels)),
            "precision_at_recall": {str(r): at_recall(scores, labels, r) for r in RECALLS},
            "recall_at_precision": {str(p): at_precision(scores, labels, p) for p in PRECISIONS}}


def tradeoff(autos: dict) -> dict:
    from vesper.edt import ladder as lad
    out = {}
    for name, key in RELATIONS:
        for frames in (False, True):
            feats = "8_plus_4" if frames else "8"
            y, s, g = lad.oof_scores(autos, key, "run", frames)
            row = {"oof": {**_curve(s, y), "auc": round(lad.auc(s, y), 4)}}
            budget = {}
            for r in RECALLS:
                p = row["oof"]["precision_at_recall"][str(r)]
                budget[str(r)] = {"passed": p["passed"], "of": len(y), "share": p["passed"] / len(y),
                                  "random_expected": r * len(y)}
            budget["0.9"]["share_ci95_repo_bootstrap"] = budget_share_ci(s, y, g, 0.9)
            row["oof"]["budget"] = budget
            y2, s2, _g2, thr = lad.split_scores(autos, key, "run", frames)
            row["split"] = {**_curve(s2, y2), "auc": round(lad.auc(s2, y2), 4), "training_threshold": thr}
            row["split"]["budget_scaled_to_population"] = {
                str(r): (row["split"]["precision_at_recall"][str(r)]["passed"] / len(y2)) * len(y)
                for r in RECALLS}
            out[f"{name}/{feats}"] = row
            print(f"tradeoff {name}/{feats}: oof AUC {row['oof']['auc']}, split AUC {row['split']['auc']}, "
                  f"budget@0.9 {budget['0.9']['passed']} of {len(y)}", flush=True)
    return out


# ---- A21.3 and A21.4: the settings ----------------------------------------------------------------------------------

def beam_settings() -> dict[str, dict]:
    return {"ref": {}, **{f"beam{k:g}": {"BEAM_LIGHT_SCALE": k} for k in K_HAT}}


def lux_settings(which: str) -> dict[str, dict]:
    """`primary`: daylight scaled by the correction 1/r of each of check D's residuals; `secondary`: by r itself."""
    out = {}
    for r in RESIDUAL:
        scale, tag = (1.0 / r, f"d1over{r:g}") if which == "primary" else (r, f"dx{r:g}")
        for b in BEAMS_B:
            out[f"{tag}-b{b:g}"] = {"DAYLIGHT_SCALE": scale, "BEAM_LIGHT_SCALE": b}
    return out


# ---- A21.1: the geometry --------------------------------------------------------------------------------------------

def geometry(models: dict, labels: dict, norths=(0.0, 180.0)) -> dict:
    """The standard home's per-room geometry and loss terms, from the model's own room models at the defaults."""
    from vesper.env import indoor
    compass = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    rooms, tot = {}, {"floor": 0.0, "volume": 0.0, "wall": 0.0, "glass": 0.0, "hw": 0.0, "hg": 0.0, "hv": 0.0}
    for rid, r in sorted(models.items()):
        wins = []
        for w in r.windows:
            bear = {f"{n:g}": (n + w["facing"]) % 360 for n in norths}
            wins.append({"glass_m2": round(w["width"] * w["height"], 3), "width_m": w["width"],
                         "height_m": w["height"], "facing_plan_deg": w["facing"], "bearing_deg": bear,
                         "bearing": {k: compass[int(((v + 22.5) % 360) // 45)] for k, v in bear.items()}})
        glass = sum(w["width"] * w["height"] for w in r.windows)
        hw, hg, hv = indoor.U_WALL * r.outside_wall_m2, indoor.U_WINDOW * glass, 0.34 * indoor.ACH * r.volume
        rooms[rid] = {"label": labels.get(rid, rid), "floor_m2": round(r.floor_m2, 3), "volume_m3": round(r.volume, 3),
                      "outside_wall_opaque_m2": round(r.outside_wall_m2, 3), "glass_m2": round(glass, 3),
                      "windows": wins, "H_walls_w_k": round(hw, 2), "H_glass_w_k": round(hg, 2),
                      "H_ventilation_w_k": round(hv, 2), "H_w_k": round(r.h_w_per_k, 2),
                      "C_MJ_per_K": round(indoor.C_PER_M2 * r.floor_m2 / 1e6, 3),
                      "tau_h": round(indoor.C_PER_M2 * r.floor_m2 / r.h_w_per_k / 3600, 1)}
        for k, v in (("floor", r.floor_m2), ("volume", r.volume), ("wall", r.outside_wall_m2), ("glass", glass),
                     ("hw", hw), ("hg", hg), ("hv", hv)):
            tot[k] += v
    h = tot["hw"] + tot["hg"] + tot["hv"]
    c = indoor.C_PER_M2 * tot["floor"]
    vent_model = RHO_CP_MODEL / 3600 * indoor.ACH * tot["volume"]
    return {"rooms": rooms, "ceiling_m": indoor.CEILING_M, "ach": indoor.ACH,
            "total": {"floor_m2": round(tot["floor"], 2), "volume_m3": round(tot["volume"], 2),
                      "outside_wall_opaque_m2": round(tot["wall"], 2), "glass_m2": round(tot["glass"], 2),
                      "H_w_k": round(h, 2), "H_walls_w_k": round(tot["hw"], 2), "H_glass_w_k": round(tot["hg"], 2),
                      "H_ventilation_w_k": round(tot["hv"], 2),
                      "shares": {k: round(tot[x] / h, 4) for k, x in (("walls", "hw"), ("glass", "hg"),
                                                                       ("ventilation", "hv"))}},
            "ventilation": {"rho_cp_model_J_m3K": RHO_CP_MODEL, "rho_cp_textbook_J_m3K": RHO_CP_TEXTBOOK,
                            "model_w_k": round(vent_model, 2),
                            "textbook_w_k": round(RHO_CP_TEXTBOOK / 3600 * indoor.ACH * tot["volume"], 2),
                            "volume_implied_by_35_3_model_m3": round(35.3 / (RHO_CP_MODEL / 3600 * indoor.ACH), 1),
                            "volume_implied_by_35_3_textbook_m3": round(35.3 / (RHO_CP_TEXTBOOK / 3600 * indoor.ACH),
                                                                        1)},
            "capacity": {"C_per_m2_kJ_m2K": indoor.C_PER_M2 / 1000, "C_MJ_per_K": round(c / 1e6, 3),
                         "C_over_H_h": round(c / h / 3600, 1),
                         "floor_implied_by_48_9_h_m2": round(48.9 * 3600 * h / indoor.C_PER_M2, 1)}}


def part_geometry(lab: Path, catalog: str) -> dict:
    from vesper.env.indoor import room_models
    from vesper.lab.home import load_lab_home
    from vesper.lab.windows import load_windows
    from vesper.twins.home import load_catalog
    work = lab / f"work-{COMMIT}"
    home = load_lab_home(work / "edt" / "home.yaml", load_catalog(Path(catalog)))
    models = room_models(home.map, load_windows(work / "edt"))
    out = geometry(models, {r.id: r.label for r in home.map.rooms})
    out["indoor_rooms"] = len(models)
    out["outdoor_spaces"] = sum(1 for r in home.map.rooms if r.outdoor)
    same = {f: (work / "edt" / f).read_bytes() == (work / "base" / f).read_bytes() for f in ("map.json",
                                                                                           "windows.json")}
    out["edt_inputs_identical_to_base"] = same
    t = out["total"]
    out["check_against_r6b"] = {"H": [t["H_w_k"], 77.8], "walls": [t["H_walls_w_k"], 28.3],
                                "glass": [t["H_glass_w_k"], 14.2], "ventilation": [t["H_ventilation_w_k"], 35.3],
                                "C_MJ_per_K": [out["capacity"]["C_MJ_per_K"], 13.7],
                                "C_over_H_h": [out["capacity"]["C_over_H_h"], 48.9]}
    out["check_passes"] = (round(t["H_w_k"], 1) == 77.8 and round(t["H_walls_w_k"], 1) == 28.3
                           and round(t["H_glass_w_k"], 1) == 14.2 and round(t["H_ventilation_w_k"], 1) == 35.3
                           and round(out["capacity"]["C_MJ_per_K"], 1) == 13.7
                           and out["capacity"]["C_over_H_h"] == 48.9)
    return out


# ---- runs -----------------------------------------------------------------------------------------------------------

def _readers(d: dict) -> list[str]:
    import r7b_room_terms as r7b
    return sorted(r7b.groups_of(d, None)["illuminance"])


def part_beam(d, a, scratch: Path) -> None:
    import r7b_room_terms as r7b
    ids = _readers(d)
    bfile = scratch / "bound-beam.json"
    bfile.write_text(json.dumps({i: d["run"][i]["bound"] for i in ids}))
    table = beam_settings()
    r7b.part_run(d, a, scratch, table, list(table), bfile, d["home"], "beam")


def part_lux(d, a, scratch: Path) -> None:
    import r7b_room_terms as r7b
    ids = _readers(d)
    home_yaml, mapping = r7b.augment_home(Path(d["home"]).parent, scratch / "home-lux" / "edt", r7b.LUX_PRODUCT,
                                          a.catalog)
    ref = {i: d["run"][i]["bound"] for i in ids}
    swap = {i: r7b.swap_entities(b, mapping) for i, b in ref.items()}
    (scratch / "bound-lux-ref.json").write_text(json.dumps(ref))
    (scratch / "bound-lux-swap.json").write_text(json.dumps(swap))
    (scratch / "lux-meta.json").write_text(json.dumps({
        "swapped_ids": sorted(i for i in ids if swap[i] != ref[i]),
        "replaced": sum(r7b.swap_count(b, mapping) for b in ref.values()), "sensors_added": len(mapping)}))
    r7b.part_run(d, a, scratch, {"ref": {}}, ["ref"], scratch / "bound-lux-ref.json", str(home_yaml), "lux")
    table = {"swap": {}, **lux_settings("primary"), **lux_settings("secondary")}
    r7b.part_run(d, a, scratch, table, list(table), scratch / "bound-lux-swap.json", str(home_yaml), "lux")


# ---- report ---------------------------------------------------------------------------------------------------------

def _settings_report(base, ids, scratch: Path, tag: str, names: list[str], bounds: dict[str, dict],
                     anchor: str, groups_of_settings: dict[str, list[str]]) -> dict:
    """Each setting's memberships among `ids` (with the bound file each was replayed with), against the published
    (`base`'s) memberships and against `anchor`'s outcomes; robust core and possible set over each named group."""
    import r7b_room_terms as r7b
    from vesper.edt.cover import _envs
    envs = _envs(base["sweep"])
    by_key = {e.key: e for e in envs}
    pub = r7b._published_records(base, ids, by_key, envs)
    outs, recs, meta = {}, {}, {}
    for n in names:
        o, f, meta[n] = r7b._load_setting(scratch / f"ol-{tag}-{n}.pkl")
        outs[n] = o
        recs[n] = {i: r7b.memberships(bounds[n][i], o.get(i, {}), f.get(i, {}), by_key, envs) for i in ids}
    published = {rel: {i for i in ids if pub[i][rel]} for rel in ("frame", "clock")}
    per = {}
    for n in names:
        row = {"fire_somewhere": sum(1 for i in ids if any(x["fired"] for x in outs[n].get(i, {}).values())),
               "outcomes_changed_vs_anchor": sum(1 for i in ids for k, x in outs[anchor].get(i, {}).items()
                                                 if outs[n].get(i, {}).get(k) != x),
               "automations_changed_vs_anchor": sum(1 for i in ids if any(
                   outs[n].get(i, {}).get(k) != x for k, x in outs[anchor].get(i, {}).items())),
               "knobs": meta[n]["knobs"], "elapsed_s": meta[n]["elapsed_s"],
               "failed_environments": meta[n]["failed_environments"]}
        for rel in ("frame", "clock"):
            flagged = {i for i in ids if recs[n][i][rel]}
            row[rel] = {"flagged": len(flagged), "lost": len(published[rel] - flagged),
                        "gained": len(flagged - published[rel]),
                        "kinds_changed": sum(1 for i in ids if recs[n][i][rel] != pub[i][rel])}
        per[n] = row
    brackets = {}
    for gname, members in groups_of_settings.items():
        b = {}
        for rel in ("frame", "clock"):
            b[rel] = r7b.core_and_possible({n: {i for i in ids if recs[n][i][rel]} for n in members},
                                           published[rel], ref=anchor)
            b[rel]["kinds_changed_somewhere"] = sum(1 for i in published[rel]
                                                    if any(recs[n][i][rel] != pub[i][rel] for n in members))
        b["fire_somewhere"] = {"robust": sum(1 for i in ids if all(
            any(x["fired"] for x in outs[n].get(i, {}).values()) for n in members)),
            "any": sum(1 for i in ids if any(any(x["fired"] for x in outs[n].get(i, {}).values()) for n in members))}
        b["settings"] = members
        brackets[gname] = b
    return {"published": {rel: len(s) for rel, s in published.items()},
            "published_fire_somewhere": sum(1 for i in ids if any(x["fired"] for x in base["run"][i]["outcomes"]
                                                                  .values())),
            "per_setting": per, "brackets": brackets,
            "_outs": outs, "_recs": recs, "_pub": pub}


def report_beam(base, ids, scratch: Path) -> dict | None:
    import r7b_room_terms as r7b
    names = list(beam_settings())
    if not all((scratch / f"ol-beam-{n}.pkl").is_file() for n in names):
        return None
    bound = json.loads((scratch / "bound-beam.json").read_text())
    out = _settings_report(base, ids, scratch, "beam", names, {n: bound for n in names}, "ref",
                           {"k_hat_bracket": names})
    out["reference_against_published"] = r7b._check_reference(base, ids, out["_outs"]["ref"], out["_pub"],
                                                              out["_recs"]["ref"])
    return out


def report_lux(base, ids, scratch: Path) -> dict | None:
    import r7b_room_terms as r7b
    prim, sec = list(lux_settings("primary")), list(lux_settings("secondary"))
    names = ["ref", "swap", *prim, *sec]
    if not all((scratch / f"ol-lux-{n}.pkl").is_file() for n in names):
        return None
    ref_b = json.loads((scratch / "bound-lux-ref.json").read_text())
    swap_b = json.loads((scratch / "bound-lux-swap.json").read_text())
    bounds = {n: (ref_b if n == "ref" else swap_b) for n in names}
    out = _settings_report(base, ids, scratch, "lux", names, bounds, "swap",
                           {"primary_with_swap": ["swap", *prim], "all_swapped": ["swap", *prim, *sec],
                            "primary_beam1_with_swap": ["swap", *[n for n in prim if n.endswith("-b1")]],
                            "primary_beam0.25": [n for n in prim if n.endswith("-b0.25")]})
    meta = json.loads((scratch / "lux-meta.json").read_text())
    out["reference_against_published"] = r7b._check_reference(base, ids, out["_outs"]["ref"], out["_pub"],
                                                              out["_recs"]["ref"])
    out["swapped"], out["entities_replaced"], out["sensors_added"] = (len(meta["swapped_ids"]), meta["replaced"],
                                                                       meta["sensors_added"])
    return out


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if not str(k).startswith("_")}
    return x


def report(a, lab: Path, scratch: Path) -> int:
    import r4b_error_budget as r4b
    commit7 = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    d = r4b.load(lab, COMMIT)
    base = r4b.load(lab, BASE)
    ids = _readers(d)
    out = {"analysis": "R10-Q (protocol Addendum 21, post hoc): the standard home's geometry, the rung-0 trade-off "
                       "curve, the interior beam-light term bracketed by k-hat, the change-reporting sensor at a "
                       "corrected daylight level",
           "code_commit": commit7, "inputs": {"sweep": COMMIT, "published_memberships": BASE},
           "illuminance_readers": len(ids),
           "item5_geometry": part_geometry(lab, a.catalog),
           "item6a_tradeoff": tradeoff(d["sweep"]["automations"]),
           "item4a_beam_bracket": _strip(report_beam(base, ids, scratch)),
           "item4b_corrected_daylight": _strip(report_lux(base, ids, scratch))}
    path = Path(a.out or ROOT / f"results/edt/r10q-{commit7}.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print("wrote", path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", required=True, choices=("beam", "lux", "report"))
    ap.add_argument("--lab", default=str(Path.home() / "vesper-lab/edt"))
    ap.add_argument("--catalog", default="catalog/vds")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", default=str(Path.home() / "vesper-lab/edt/r10-Q/scratch"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    lab, scratch = Path(a.lab).expanduser(), Path(a.scratch).expanduser()
    scratch.mkdir(parents=True, exist_ok=True)
    import vesper
    print("vesper from", Path(vesper.__file__).parent, flush=True)
    if a.part == "report":
        return report(a, lab, scratch)
    import r4b_error_budget as r4b
    d = r4b.load(lab, COMMIT)
    if a.part == "beam":
        part_beam(d, a, scratch)
    else:
        part_lux(d, a, scratch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
