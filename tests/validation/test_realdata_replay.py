import pytest

from vesper.validation.realdata import Report, evaluate_stream, interval_class, predict, verdict

TEMP = (30, 900, 50)  # ZHA's temperature rule: min 30 s, max 900 s, change 0.50 °C (raw 0.01 °C)
ONOFF = (0, 900, 1)  # ZHA's on/off and occupancy rule


def r(t, v, session="s"):
    return Report(t, v, session)


def test_the_twin_predicts_the_next_report_from_the_last_real_one():
    assert predict(r(0, 2000), r(100, 2100), TEMP) == (100, "stimulus")  # a reportable change after min_s
    assert predict(r(0, 2000), r(10, 2100), TEMP) == (30, "stimulus")  # sooner than min_s: deferred
    assert predict(r(0, 2000), r(100, 2010), TEMP) == (900, "periodic")  # below the change: the heartbeat
    assert predict(r(0, 2000), r(2000, 2300), TEMP) == (900, "periodic")  # a silence longer than max_s
    assert predict(r(5000, 2000), r(5100, 2100), TEMP) == (5100, "stimulus")  # anchored at the earlier report


def test_verdicts_allow_a_second_for_changes_and_ten_percent_of_max_s_for_heartbeats():
    assert verdict(100.8, (100, "stimulus"), 900) == "matched"
    assert verdict(101.5, (100, "stimulus"), 900) == "extra"  # the twin would have reported first
    assert verdict(98.5, (100, "stimulus"), 900) == "missed"  # the device reported before the twin would
    assert verdict(990, (900, "periodic"), 900) == verdict(810, (900, "periodic"), 900) == "matched"
    assert verdict(991, (900, "periodic"), 900) == "extra" and verdict(809, (900, "periodic"), 900) == "missed"


def test_interval_classes_separate_agreement_by_construction_from_informative_intervals():
    assert interval_class(r(0, 2000), r(100, 2100), TEMP) == "change"
    assert interval_class(r(0, 2000), r(900, 2010), TEMP) == "steady"
    assert interval_class(r(0, 2000), r(10, 2100), TEMP) == "early_change"
    assert interval_class(r(0, 2000), r(2000, 2300), TEMP) == "late_change"
    assert interval_class(r(0, 0), r(5, 1), ONOFF) == "change"


def test_heartbeats_that_drift_against_max_s_are_judged_interval_by_interval():
    for period, n in ((899.9, 32), (904.0, 32), (901.0, 100), (899.6, 40)):  # clocks off by ppm to 0.4 %
        ev = evaluate_stream([[r(i * period, 1) for i in range(n + 1)]], ONOFF)
        assert (ev["informative"], ev["matched"], ev["passed"]) == (n, n, True), period


def test_change_driven_intervals_agree_by_construction_and_do_not_make_a_stream_eligible():
    ev = evaluate_stream([[r(i * 100.0, 2000 + 100 * i) for i in range(30)]], TEMP)
    assert ev["classes"] == {"change": 29} and ev["change_agreement"] == 1.0
    assert ev["informative"] == 0 and not ev["eligible"] and ev["passed"] is None


def test_a_device_without_heartbeats_shows_as_extra_twin_reports():
    ev = evaluate_stream([[r(i * 2000.0, 2000) for i in range(25)]], TEMP)  # silent for 2000 s, max_s 900
    assert (ev["informative"], ev["extra"], ev["matched"]) == (24, 24, 0) and ev["passed"] is False


def test_reports_sooner_than_min_s_show_as_missed():
    ev = evaluate_stream([[r(i * 10.0, 2000 + 100 * i) for i in range(25)]], TEMP)
    assert ev["classes"] == {"early_change": 24} and ev["missed"] == 24 and ev["passed"] is False
    assert ev["min_interval_ok"] == 0.0


def test_stream_statistics_pool_segments():
    good = [[r(i * 900.0, 2000, "a") for i in range(12)], [r(i * 900.0, 2000, "b") for i in range(12)]]
    ev = evaluate_stream(good, TEMP)
    assert (ev["segments"], ev["intervals"], ev["informative"], ev["matched"]) == (2, 22, 22, 22)
    assert ev["eligible"] and ev["passed"] and ev["matched_fraction"] == 1.0 and ev["heartbeat_ratio"] == 1.0
    assert ev["min_interval_ok"] == 1.0 and ev["max_silence_ok"] == 1.0 and ev["early_change_ok"] is None
    short = evaluate_stream([[r(0, 2000), r(900, 2000)]], TEMP)
    assert not short["eligible"] and short["passed"] is None


def test_a_heartbeat_carrying_a_change_below_the_threshold_still_counts_as_a_heartbeat():
    assert evaluate_stream([[r(i * 900.0, 2000 + (i % 2) * 10) for i in range(5)]], TEMP)["heartbeat_ratio"] == 1.0


def test_non_numeric_values_cannot_be_replayed():
    with pytest.raises(TypeError, match="numeric"):
        evaluate_stream([[r(0, b"x"), r(10, b"y")]], TEMP)
