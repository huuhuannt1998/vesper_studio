"""Revision R10-Q's script (protocol Addendum 21): the trade-off curve's operating points and budget, the settings of
the beam-light bracket and the corrected daylight, and the standard home's geometry table."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r10q", ROOT / "scripts/revision/r10q.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


q = _load()


# ---- A21.2: the operating points ------------------------------------------------------------------------------------

S = [0.9, 0.8, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
Y = [1, 1, 0, 1, 0, 0, 1, 0, 0, 0]


def test_precision_at_recall_takes_the_highest_threshold_that_reaches_it_and_ties_pass_together():
    p = q.at_recall(S, Y, 0.5)  # 2 of 4 positives: the tie at 0.8 passes 3, with 2 positives
    assert (p["passed"], p["tp"], p["threshold"], p["tie_block"]) == (3, 2, 0.8, 2)
    assert p["recall"] == 0.5 and p["precision"] == pytest.approx(2 / 3)
    p = q.at_recall(S, Y, 0.75)  # the third positive at 0.7
    assert (p["passed"], p["tp"]) == (4, 3) and p["precision"] == 0.75
    p = q.at_recall(S, Y, 1.0)
    assert (p["passed"], p["tp"]) == (7, 4) and p["precision"] == pytest.approx(4 / 7)
    # interpolated: the largest precision over every threshold whose recall reaches r
    assert q.at_recall(S, Y, 0.5)["interpolated_precision"] == 0.75


def test_recall_at_precision_agrees_with_the_ladder_s_rule_and_counts_what_passes():
    from vesper.edt.ladder import recall_at_precision
    rng = np.random.default_rng(3)
    s, y = rng.normal(size=300).round(2), (rng.random(300) < 0.2).astype(int)
    s = s + y * 0.8
    for p in (0.3, 0.5, 0.7):
        mine, theirs = q.at_precision(s, y, p), recall_at_precision(s, y, p)
        assert mine["recall"] == theirs["recall"] and mine["threshold"] == theirs["threshold"]
        if theirs["threshold"] is not None:
            assert mine["passed"] == int((s >= theirs["threshold"]).sum())
            assert mine["precision"] == pytest.approx(theirs["precision"])
    assert q.at_precision([0.1, 0.2], [1, 0], 0.9) == {"recall": 0.0, "precision": None, "threshold": None,
                                                        "passed": 0, "tp": 0}


def test_a_random_score_needs_about_the_recall_share_of_the_population():
    rng = np.random.default_rng(0)
    n = 20_000
    y = (rng.random(n) < 0.05).astype(int)
    p = q.at_recall(rng.random(n), y, 0.9)
    assert p["passed"] / n == pytest.approx(0.9, abs=0.03)


def test_the_budget_bootstrap_resamples_repositories_and_is_reproducible():
    rng = np.random.default_rng(1)
    s, y = rng.random(400), (rng.random(400) < 0.15).astype(int)
    s = s + 0.5 * y
    groups = [f"r{i % 40}" for i in range(400)]
    a, b = q.budget_share_ci(s, y, groups, 0.9, n=200), q.budget_share_ci(s, y, groups, 0.9, n=200)
    assert a == b and a["ci95"][0] <= q.at_recall(s, y, 0.9)["passed"] / 400 <= a["ci95"][1]
    assert a["resamples_used"] == 200


# ---- A21.3 and A21.4: the settings ----------------------------------------------------------------------------------

def test_the_beam_bracket_is_the_reference_and_k_hat_s_point_and_interval():
    from vesper.env import indoor
    s = q.beam_settings()
    assert s["ref"] == {}
    assert sorted(v["BEAM_LIGHT_SCALE"] for k, v in s.items() if k != "ref") == [0.205, 0.25, 0.289]
    assert all(set(v) <= set(indoor.KNOBS) for v in s.values())


def test_the_daylight_settings_are_the_corrections_primary_and_the_literal_factors_secondary():
    prim, sec = q.lux_settings("primary"), q.lux_settings("secondary")
    assert len(prim) == len(sec) == 6
    scales = sorted({round(v["DAYLIGHT_SCALE"], 6) for v in prim.values()})
    assert scales == sorted(round(1 / r, 6) for r in (2.49, 0.93, 3.49))
    assert sorted({v["DAYLIGHT_SCALE"] for v in sec.values()}) == [0.93, 2.49, 3.49]
    for t in (prim, sec):
        assert sorted({v["BEAM_LIGHT_SCALE"] for v in t.values()}) == [0.25, 1.0]
    assert not set(prim) & set(sec) and "swap" not in prim and "ref" not in prim


# ---- A21.1: the geometry --------------------------------------------------------------------------------------------

def test_the_geometry_table_sums_its_rooms_and_does_the_ventilation_arithmetic():
    from tests.lab.maps import house
    from vesper.env.indoor import room_models
    from vesper.lab.windows import plan_windows
    m = house()
    g = q.geometry(room_models(m, plan_windows(m)), {r.id: r.label for r in m.rooms}, (0.0, 180.0))
    rooms = g["rooms"]
    assert set(rooms) == {"hall", "kitchen", "bedroom"}
    assert g["total"]["floor_m2"] == pytest.approx(sum(r["floor_m2"] for r in rooms.values()), abs=0.02)
    assert g["total"]["volume_m3"] == pytest.approx(2.5 * g["total"]["floor_m2"], abs=0.05)
    v = g["ventilation"]
    assert v["model_w_k"] == pytest.approx(0.34 * 0.5 * g["total"]["volume_m3"], abs=0.02)
    assert v["textbook_w_k"] == pytest.approx(1.2 * 1005 / 3600 * 0.5 * g["total"]["volume_m3"], abs=0.02)
    c = g["capacity"]
    assert c["C_MJ_per_K"] == pytest.approx(0.165 * g["total"]["floor_m2"], abs=0.002)
    for r in rooms.values():
        for w in r["windows"]:
            assert w["bearing_deg"]["180"] == (w["facing_plan_deg"] + 180) % 360
