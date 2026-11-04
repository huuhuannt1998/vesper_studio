"""vesper.edt.ladder: the necessity ladder, on synthetic sweep results of the shared shape (interfaces-sweep.md)."""
import json
import math

import pytest

from vesper.edt.ladder import (
    FEATURES,
    auc,
    bootstrap_auc_ci,
    circular_range,
    grouped_folds,
    heldout_recall_at_precision,
    ladder_report,
    linter_features,
    recall_at_precision,
    spearman,
)


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
    assert auc([1, 2, 2, 3], [0, 0, 1, 1]) == 0.875  # 3.5 of 4 pairs: the tied pair counts half


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
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486832980505139)  # average ranks 1, 2.5, 2.5, 4


def test_circular_range_is_24_minus_the_largest_gap():
    assert circular_range([23 + 50 / 60, 10 / 60]) == pytest.approx(1 / 3)  # 23:50 and 00:10 are 20 minutes apart
    assert circular_range([0.0, 8.0, 16.0]) == pytest.approx(16.0)
    assert circular_range([5.0, 5.0]) == 0.0
    assert circular_range([10.0, 22.0]) == pytest.approx(12.0)
    assert circular_range([24.0, 1.0]) == pytest.approx(1.0)  # 24 h is midnight
    assert circular_range([7.0]) is None


def test_heldout_threshold_comes_from_the_training_fold_not_the_test_fold():
    folds = [{"train_scores": [0.9, 0.8, 0.1], "train_labels": [1, 1, 0],
              "test_scores": [0.5, 0.4], "test_labels": [1, 0]}]
    r = heldout_recall_at_precision(folds, 0.55)
    # training picks 0.8; the held-out positive scores 0.5, so nothing is predicted: an oracle would have had recall 1
    assert r["thresholds"] == [0.8] and r["recall"] == 0.0 and r["precision"] is None
    assert r["base_rate"] == 0.5 and r["n_predicted_positive"] == 0
    folds.append({"train_scores": [0.9, 0.8, 0.1], "train_labels": [1, 1, 0],
                  "test_scores": [0.95, 0.2], "test_labels": [1, 0]})
    r = heldout_recall_at_precision(folds, 0.55)  # pooled: one of two positives found, no false positive
    assert r["recall"] == 0.5 and r["precision"] == 1.0 and r["base_rate"] == 0.5
    degenerate = [{"train_scores": None, "train_labels": [1, 1], "test_scores": [3.0], "test_labels": [1]}]
    assert heldout_recall_at_precision(degenerate, 0.55)["recall"] == 0.0  # no model: no positive predicted


def test_grouped_folds_keep_a_repo_in_one_fold_and_are_deterministic():
    groups = [f"r{i // 3}" for i in range(30)]  # 10 repos of 3 automations
    labels = [int(i // 3 % 2 == 0) for i in range(30)]
    fold = grouped_folds(groups, labels, 5)
    assert fold.tolist() == grouped_folds(groups, labels, 5).tolist()
    for g in set(groups):
        folds_of_repo = {int(f) for gg, f in zip(groups, fold, strict=True) if gg == g}
        assert len(folds_of_repo) == 1  # a repo is in one fold: never in train and test of the same split
    assert sorted(set(fold.tolist())) == [0, 1, 2, 3, 4]
    for f in range(5):  # stratified as well as whole groups allow: each fold holds one flagged and one clear repo
        assert sum(labels[i] for i in range(30) if fold[i] == f) == 3


# --- synthetic sweep results -------------------------------------------------------------------------------------

ENVS = [("Oslo", "2025-12-21", 0.0), ("Madrid", "2025-06-21", 0.0), ("Quito", "2025-03-21", 90.0)]


def _env(rung, city, date, north):
    return {"city": city, "date": date, "north_deg": north, "rung": rung, "key": f"r{rung}|{city}|{date}|{north}"}


def _auto(rung, i, raw, tods, divergences=(), bound=True, repo=None):
    """tods: first_tod_h per env (None = did not fire). Each automation is its own repo unless one is given."""
    outcomes = {}
    for (city, date, north), t in zip(ENVS, tods):
        outcomes[_env(rung, city, date, north)["key"]] = {"fired": t is not None, "first_tod_h": t, "flaps": False,
                                                        "night_light": False, "night_cover": False}
    return {"repo": repo or f"o/r{i}", "hash": f"h{i}", "kinds": [], "raw": raw, "bound": {"x": 1} if bound else None,
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


def test_denominators_are_visible():
    r4 = _result(4, {"a": _auto(4, 0, SUN, [10.0, 10.0, 10.0], ["d"]), "b": _auto(4, 1, SUN, [10.0, 10.0, 10.0], ["d"]),
                     "d": _auto(4, 2, SUN, [10.0, 10.0, 10.0], ["d"]), "e": _auto(4, 3, SUN, [10.0, 10.0, 10.0], [])})
    r1 = _result(1, {"a": _auto(1, 0, SUN, [10.0, 10.0, 10.0], ["d"]),
                     "d": _auto(1, 2, SUN, [10.0, 10.0, 10.0], ["d"], bound=False),
                     "e": _auto(1, 3, SUN, [10.0, 10.0, 10.0], [])})
    row = ladder_report({4: r4, 1: r1})["rungs"]["1"]
    assert row["n_flagged_rung4_total"] == 3            # a, b, d: every automation bound at rung 4 and flagged there
    assert row["n_bound_both"] == 2 and row["n_flagged_rung4"] == 1  # a, e
    assert row["n_unbound_at_rung"] == 2                # b is absent at rung 1, d is unbound there
    assert row["reproduced_share"] == 1.0 and row["null"] is None


def test_no_matching_environments_gives_a_null_reason():
    r4 = _result(4, {"a": _auto(4, 0, SUN, [10.0, 10.0, 10.0], ["d"])})
    r1 = _result(1, {"a": _auto(1, 0, SUN, [10.0, 10.0, 10.0], ["d"])})
    r1["envs"] = [_env(1, "Paris", "2025-09-01", 45.0)]
    row = ladder_report({4: r4, 1: r1})["rungs"]["1"]
    assert row["n_envs_matched"] == 0 and "environment" in row["null"]
    assert row["mae_hours"] is None and row["spearman_time_spread"] is None


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
    assert r0["base_rate"] == 0.5 and r0["recall_at_precision_0.55"]["base_rate"] == 0.5
    assert r0["auc_random_folds"] == pytest.approx(1.0) and r0["n_repos"] == 20
    lo, hi = r0["auc_ci95"]
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)
    assert r0["auc_ci95_n"] >= 100 and r0["degenerate_folds"] == 0 and r0["n_excluded"] == 0
    assert len(r0["recall_at_precision_0.55"]["thresholds"]) == 5
    json.dumps(rep)  # the report is JSON-serialisable


def test_recall_at_high_prevalence_is_reported_next_to_the_base_rate_and_is_not_oracle():
    # 75 % flagged and a feature that says nothing: the best a detector can do is flag everything (recall 1.0 at
    # precision = the base rate), and the report must show that precision equals the base rate, i.e. no skill
    autos = {f"a{i}": _auto(4, i, SUN, [10.0, 10.0, 10.0], ["d"] if i % 4 else []) for i in range(24)}
    r0 = ladder_report({4: _result(4, autos)})["rung0"]
    rec = r0["recall_at_precision_0.55"]
    assert r0["base_rate"] == 0.75 and rec["base_rate"] == 0.75
    assert rec["precision"] == pytest.approx(0.75) and len(rec["thresholds"]) == r0["folds"]


def test_repos_are_cross_validated_whole_and_the_random_folds_number_is_reported_beside_it():
    # 16 repos x 4 near-identical automations; a repo's label is arbitrary but its four siblings agree on features
    labels = [1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1]
    autos = {}
    for r in range(16):
        raw = {"trigger": [{"platform": "sun"}] if r & 1 else [{"platform": "time", "at": "07:00:00"}],
               "condition": [{"condition": "numeric_state", "entity_id": "sensor.lux", "below": 5}] * (r >> 1 & 3),
               "action": []}
        raw["trigger"] = raw["trigger"] + ([{"platform": "state", "entity_id": "sensor.hum_x"}] * (r >> 3))
        for j in range(4):
            i = r * 4 + j
            autos[f"a{i}"] = _auto(4, i, raw, [10.0, 10.0, 10.0], ["d"] if labels[r] else [], repo=f"o/r{r}")
    r0 = ladder_report({4: _result(4, autos)})["rung0"]
    assert r0["n_repos"] == 16 and r0["folds"] == 5
    assert r0["auc_random_folds"] > r0["auc"] + 0.1  # siblings across folds flatter a random split
    assert r0["auc_ci95"][0] <= r0["auc"] <= r0["auc_ci95"][1]


def test_bootstrap_ci_resamples_repos_and_is_seeded():
    scores = [0.1, 0.7, 0.3, 0.4, 0.6, 0.5, 0.8, 0.9]
    labels = [0, 0, 0, 0, 1, 0, 1, 1]
    groups = ["a", "a", "b", "b", "c", "c", "d", "d"]
    ci, n_used = bootstrap_auc_ci(scores, labels, groups, n=300)
    assert (ci, n_used) == bootstrap_auc_ci(scores, labels, groups, n=300)
    assert 0.0 <= ci[0] <= auc(scores, labels) <= ci[1] <= 1.0 and ci[0] < ci[1]
    assert 100 <= n_used <= 300  # a resample that draws only one class is dropped, and counted out
    assert bootstrap_auc_ci([0.1, 0.9], [0, 1], ["a", "a"], n=50) == (None, 0)  # one repo: nothing to resample


def test_bootstrap_ci_counts_the_resamples_it_used_and_is_null_below_100():
    scores = [0.9, 0.8, 0.1, 0.2, 0.3, 0.4]
    labels = [1, 1, 0, 0, 0, 0]
    groups = ["p", "p", "n1", "n2", "n3", "n4"]  # the only positives are one repo: many resamples miss it
    ci, n_used = bootstrap_auc_ci(scores, labels, groups, n=1000)
    assert ci is not None and 100 <= n_used < 1000
    ci, n_used = bootstrap_auc_ci(scores, labels, groups, n=60)  # fewer than 100 resamples can be usable
    assert ci is None and n_used <= 60


def test_a_fold_with_no_model_does_not_leak_into_the_auc():
    # 10 flagged automations in ONE repo and 10 clear ones in ten repos: the fold holding the flagged repo trains on
    # negatives only, has no model, and its test rows must not be pooled as if they had been scored
    autos = {f"p{i}": _auto(4, i, SUN, [10.0, 10.0, 10.0], ["d"], repo="o/flagged") for i in range(10)}
    autos.update({f"n{i}": _auto(4, 10 + i, PLAIN, [10.0, 10.0, 10.0], [], repo=f"o/clear{i}") for i in range(10)})
    r0 = ladder_report({4: _result(4, autos)})["rung0"]
    assert r0["degenerate_folds"] == 1 and r0["n_excluded"] == 10
    assert r0["auc"] is None and r0["auc_ci95"] is None and r0["null"]  # the usable rows hold one class
    assert r0["auc_ci95_n"] == 0


def test_fewer_than_two_usable_folds_leave_the_auc_null_with_a_reason():
    # two repos, so two folds; the flagged one has no model when held out, the clear one trains on flagged only
    autos = {f"p{i}": _auto(4, i, SUN, [10.0, 10.0, 10.0], ["d"], repo="o/flagged") for i in range(4)}
    autos.update({f"n{i}": _auto(4, 4 + i, PLAIN, [10.0, 10.0, 10.0], [], repo="o/clear") for i in range(4)})
    r0 = ladder_report({4: _result(4, autos)})["rung0"]
    assert r0["auc"] is None and "usable" in r0["null"] and r0["degenerate_folds"] == 2 and r0["n_excluded"] == 8


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


def test_spread_is_the_circular_range_and_rank_correlation_skips_flat_rung4_automations():
    # rung-4 spreads: 20 minutes across midnight, 16 h, 0 h (never moves), 4 h
    t4 = {"a": [23 + 50 / 60, 10 / 60, 23.9], "b": [0.0, 8.0, 16.0], "c": [9.0, 9.0, 9.0], "d": [6.0, 8.0, 10.0]}
    # at rung 3 the same automations spread 1 h, 12 h, 5 h (c: was flat at 4, moves at 3), 3 h
    t3 = {"a": [0.0, 0.5, 1.0], "b": [0.0, 6.0, 12.0], "c": [3.0, 5.0, 8.0], "d": [6.0, 7.0, 9.0]}
    r4 = _result(4, {k: _auto(4, i, SUN, v, ["d"]) for i, (k, v) in enumerate(t4.items())})
    r3 = _result(3, {k: _auto(3, i, SUN, v, ["d"]) for i, (k, v) in enumerate(t3.items())})
    row = ladder_report({4: r4, 3: r3})["rungs"]["3"]
    assert row["n_spread_both"] == 4 and row["n_spread"] == 3  # c is dropped: its rung-4 spread is 0
    # over a, d, b: rung 4 spreads ~0.33, 4, 16 and rung 3 spreads 1, 3, 12 -> the same order
    assert row["spearman_time_spread"] == pytest.approx(1.0)


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
