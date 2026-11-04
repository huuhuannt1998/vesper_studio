"""vesper.edt.ladder: the necessity ladder, on synthetic sweep results of the shared shape (interfaces-sweep.md)."""
import json
import math

import pytest

from vesper.edt.ladder import FEATURES, auc, ladder_report, linter_features, recall_at_precision, spearman


# --- the linter's features ---------------------------------------------------------------------------------------

def test_linter_features_legacy_syntax():
    raw = {"trigger": [{"platform": "sun", "event": "sunset"}, {"platform": "time", "at": "07:00:00"}],
           "condition": [{"condition": "numeric_state", "entity_id": "sensor.living_lux", "below": 50},
                         {"condition": "state", "entity_id": "light.x", "state": "off"}],
           "action": []}
    f = linter_features(raw)
    assert set(f) == set(FEATURES) and len(FEATURES) == 8
    assert f["sun_trigger"] == 1 and f["time_trigger"] == 1 and f["sun_condition"] == 0
    assert f["numeric_state_illuminance"] == 1 and f["numeric_state_temperature"] == 0
    assert f["n_conditions"] == 2


def test_linter_features_modern_syntax():
    raw = {"triggers": [{"trigger": "numeric_state", "entity_id": "sensor.outdoor_temperature", "above": 25},
                        {"trigger": "numeric_state", "entity_id": "sensor.hum_bath", "above": 70}],
           "conditions": [{"condition": "sun", "after": "sunrise"},
                          {"condition": "and", "conditions": [
                              {"condition": "state", "entity_id": "a.b", "state": "on"},
                              {"condition": "numeric_state", "entity_id": "weather.home",
                               "attribute": "humidity", "above": 1}]}],
           "actions": []}
    f = linter_features(raw)
    assert f["numeric_state_temperature"] == 1 and f["numeric_state_humidity"] == 1
    assert f["sun_condition"] == 1 and f["sun_trigger"] == 0 and f["time_trigger"] == 0
    assert f["n_conditions"] == 4  # the sun condition, the `and`, and its two members


def test_linter_features_degenerate_shapes_do_not_raise():
    assert linter_features({})["n_conditions"] == 0
    f = linter_features({"trigger": {"platform": "time_pattern", "minutes": "/5"},
                         "condition": "{{ is_state('sun.sun','below_horizon') }}"})
    assert f["time_trigger"] == 1 and f["n_conditions"] == 1
    assert linter_features({"trigger": "oops", "condition": None})["n_conditions"] == 0


# --- the statistics ----------------------------------------------------------------------------------------------

def test_auc_perfect_inverted_and_ties():
    assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    assert auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) == 0.5
    assert auc([1, 2], [1, 1]) is None


def test_recall_at_precision_picks_the_best_recall_that_meets_it():
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
    labels = [1, 1, 0, 1, 0, 0]
    r = recall_at_precision(scores, labels, 0.55)
    # top 2: p=1, r=2/3; top 3: p=2/3, r=2/3; top 4: p=3/4, r=1; top 5: p=.6, r=1; top 6: p=.5
    assert r["recall"] == 1.0 and r["precision"] == pytest.approx(0.75) and r["threshold"] == 0.6
    none = recall_at_precision([0.9, 0.8], [0, 1], 0.9)
    assert none["recall"] == 0.0 and none["threshold"] is None


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [1, 2]) is None  # too few to say anything


# --- synthetic sweep results -------------------------------------------------------------------------------------

ENVS = [("Oslo", "2025-12-21", 0.0), ("Madrid", "2025-06-21", 0.0), ("Quito", "2025-03-21", 90.0)]


def _env(rung, city, date, north):
    return {"city": city, "date": date, "north_deg": north, "rung": rung, "key": f"r{rung}|{city}|{date}|{north}"}


def _auto(rung, i, raw, tods, divergences=(), bound=True):
    """tods: first_tod_h per env (None = did not fire)."""
    outcomes = {}
    for (city, date, north), t in zip(ENVS, tods):
        outcomes[_env(rung, city, date, north)["key"]] = {"fired": t is not None, "first_tod_h": t, "flaps": False,
                                                        "night_light": False, "night_cover": False}
    return {"repo": "o/r", "hash": f"h{i}", "kinds": [], "raw": raw, "bound": {"x": 1} if bound else None,
            "unbindable": None if bound else "no entity", "outcomes": outcomes, "divergences": list(divergences),
            "negative_control": False}


def _result(rung, autos):
    return {"rung": rung, "envs": [_env(rung, *e) for e in ENVS], "automations": autos, "negative_control_rate": 0.0}


SUN = {"trigger": [{"platform": "sun", "event": "sunset"}], "condition": [], "action": []}
PLAIN = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.door"}], "condition": [], "action": []}


def _rung4(n=20):
    autos = {}
    for i in range(n):
        sunny = i % 2 == 0
        autos[f"a{i}"] = _auto(4, i, SUN if sunny else PLAIN, [18.0 + i % 3, 20.0, 6.0 + i % 4],
                               divergences=["fires_differently"] if sunny else [])
    return _result(4, autos)


def _as_rung(result, rung):
    """The same sweep as another rung: its keys differ from rung 4's (keys include the rung)."""
    out = json.loads(json.dumps(result))
    out["rung"] = rung
    out["envs"] = [_env(rung, *e) for e in ENVS]
    for a in out["automations"].values():
        a["outcomes"] = {k.replace("r4|", f"r{rung}|"): v for k, v in a["outcomes"].items()}
    return out


def test_identical_rung_reproduces_everything_with_zero_error():
    r4 = _rung4()
    row = ladder_report({4: r4, 3: _as_rung(r4, 3)})["rungs"]["3"]
    assert row["n_bound_both"] == 20 and row["n_flagged_rung4"] == 10 and row["reproduced_share"] == 1.0
    assert row["mae_hours"] == 0.0 and row["spearman_time_spread"] == pytest.approx(1.0)


def test_rung_that_flags_nothing_reproduces_nothing_and_time_error_is_circular():
    r4 = _rung4(10)
    r2 = _as_rung(r4, 2)
    for a in r2["automations"].values():
        a["divergences"] = []
        for v in a["outcomes"].values():
            v["first_tod_h"] = (v["first_tod_h"] + 23.0) % 24.0
    row = ladder_report({4: r4, 2: r2})["rungs"]["2"]
    assert row["reproduced_share"] == 0.0
    assert row["mae_hours"] == pytest.approx(1.0)  # 23 h later is 1 h away round the clock, not 23
    assert row["n_time_pairs"] == 30


def test_only_automations_bound_in_both_count_and_unfired_pairs_are_skipped():
    r4 = _result(4, {"a": _auto(4, 0, SUN, [10.0, None, 12.0], ["d"]), "b": _auto(4, 1, SUN, [10.0, 10.0, 10.0], ["d"]),
                     "c": _auto(4, 2, SUN, [1.0, 1.0, 1.0], ["d"], bound=False)})
    r1 = _result(1, {"a": _auto(1, 0, SUN, [11.0, 5.0, None], ["d"]), "b": _auto(1, 1, SUN, [10.0, 10.0, 10.0], []),
                     "c": _auto(1, 2, SUN, [1.0, 1.0, 1.0], ["d"])})
    row = ladder_report({4: r4, 1: r1})["rungs"]["1"]
    assert row["n_bound_both"] == 2 and row["n_flagged_rung4"] == 2 and row["reproduced_share"] == 0.5
    assert row["n_time_pairs"] == 4 and row["mae_hours"] == pytest.approx(0.25)  # only (a, Oslo) is 1 h off


def test_envs_are_matched_by_city_date_north_not_key():
    r4 = _result(4, {"a": _auto(4, 0, SUN, [10.0, 11.0, 12.0], ["d"])})
    r3 = _result(3, {"a": _auto(3, 0, SUN, [10.0, 11.0, 12.0], ["d"])})
    r3["envs"] = list(reversed(r3["envs"]))  # order is irrelevant too
    row = ladder_report({4: r4, 3: r3})["rungs"]["3"]
    assert row["n_time_pairs"] == 3 and row["mae_hours"] == 0.0


def test_rung0_perfect_feature_gives_auc_one():
    rep = ladder_report({4: _rung4()})
    r0 = rep["rung0"]
    assert r0["auc"] == pytest.approx(1.0) and r0["recall_at_precision_0.55"]["recall"] == 1.0
    assert r0["n"] == 20 and r0["n_flagged"] == 10 and r0["folds"] == 5 and r0["null"] is None
    json.dumps(rep)  # the report is JSON-serialisable


def test_rung0_uninformative_features_are_near_chance():
    autos = {f"a{i}": _auto(4, i, SUN, [10.0, 10.0, 10.0], ["d"] if i % 2 else []) for i in range(30)}
    r0 = ladder_report({4: _result(4, autos)})["rung0"]
    assert r0["auc"] == pytest.approx(0.5, abs=0.15)


def test_rung0_is_deterministic():
    assert ladder_report({4: _rung4()})["rung0"] == ladder_report({4: _rung4()})["rung0"]


def test_degenerate_populations_give_a_null_result_not_an_exception():
    one_class = {f"a{i}": _auto(4, i, SUN, [10.0, 10.0, 10.0]) for i in range(8)}
    rep = ladder_report({4: _result(4, one_class)})
    assert rep["rung0"]["auc"] is None and "one class" in rep["rung0"]["null"]
    assert rep["rungs"] == {}
    tiny = {"a": _auto(4, 0, SUN, [1.0, 1.0, 1.0], ["d"]), "b": _auto(4, 1, PLAIN, [1.0, 1.0, 1.0])}
    assert ladder_report({4: _result(4, tiny)})["rung0"]["auc"] is None
    assert ladder_report({4: _result(4, {})})["rung0"]["null"]
    assert ladder_report({4: _rung4(4), 1: _result(1, {})})["rungs"]["1"]["reproduced_share"] is None


def test_rung4_is_required():
    with pytest.raises(ValueError, match="rung 4"):
        ladder_report({3: _result(3, {})})


def test_time_spread_rank_correlation_tracks_agreement():
    def spread(w):
        return [10.0, 10.0 + w / 2, 10.0 + w]

    widths4, widths3 = [1.0, 3.0, 5.0, 7.0], [2.0, 5.0, 8.0, 11.0]
    r4 = _result(4, {f"a{i}": _auto(4, i, SUN, spread(w), ["d"]) for i, w in enumerate(widths4)})
    r3 = _result(3, {f"a{i}": _auto(3, i, SUN, spread(w), ["d"]) for i, w in enumerate(widths3)})
    assert ladder_report({4: r4, 3: r3})["rungs"]["3"]["spearman_time_spread"] == pytest.approx(1.0)
    r3_rev = _result(3, {f"a{i}": _auto(3, i, SUN, spread(w), ["d"]) for i, w in enumerate(reversed(widths3))})
    assert ladder_report({4: r4, 3: r3_rev})["rungs"]["3"]["spearman_time_spread"] == pytest.approx(-1.0)


# --- the CLI -----------------------------------------------------------------------------------------------------

def test_cli_ladder_reads_rung_from_each_file(tmp_path):
    from vesper import cli
    r4 = _rung4()
    p4, p3, out = tmp_path / "s4.json", tmp_path / "s3.json", tmp_path / "out" / "ladder.json"
    p4.write_text(json.dumps(r4))
    p3.write_text(json.dumps(_as_rung(r4, 3)))
    assert cli.main(["edt", "ladder", "--sweep", str(p4), "--sweep", str(p3), "--out", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["rungs"]["3"]["reproduced_share"] == 1.0 and rep["rung0"]["auc"] == pytest.approx(1.0)
    assert not math.isnan(rep["rungs"]["3"]["mae_hours"])
    assert cli.main(["edt", "ladder", "--sweep", str(p3), "--out", str(out)]) == 1  # no rung 4
    assert cli.main(["edt", "ladder", "--sweep", str(p4), "--sweep", str(p4), "--out", str(out)]) == 1  # rung twice
