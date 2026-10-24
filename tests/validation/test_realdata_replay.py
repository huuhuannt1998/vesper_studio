import pytest

from vesper.validation.realdata import Report, evaluate_stream, match, replay

TEMP = (30, 900, 50)  # ZHA's temperature rule: min 30 s, max 900 s, change 0.50 °C (raw 0.01 °C)


def seg(*points, session="s"):
    return [Report(t, v, session) for t, v in points]


def test_a_device_that_follows_its_rule_is_reproduced_report_for_report():
    s = seg((0, 2000), (100, 2100), (1000, 2100), (1900, 2100), (1950, 2200))
    assert replay(s, TEMP) == [(100, "stimulus"), (1000, "periodic"), (1900, "periodic"), (1950, "stimulus")]
    assert match([r.t for r in s[1:]], replay(s, TEMP), 900) == (4, 0)


def test_a_report_sooner_than_min_s_is_deferred_by_the_twin_and_both_go_unmatched():
    s = seg((0, 2000), (10, 2100))
    assert replay(s, TEMP) == []  # the deferred report would leave at 30 s, after the replay window
    s = seg((0, 2000), (10, 2100), (100, 2100))
    assert replay(s, TEMP) == [(30, "stimulus")]
    assert match([10, 100], replay(s, TEMP), 900) == (0, 1)


def test_a_device_without_heartbeats_leaves_the_twins_heartbeats_unmatched():
    s = seg((0, 2000), (2000, 2300))
    assert replay(s, TEMP) == [(900, "periodic"), (1800, "periodic"), (2000, "stimulus")]
    assert match([2000], replay(s, TEMP), 900) == (1, 2)


def test_a_late_heartbeat_matches_within_ten_percent_of_max_s_and_an_early_one_within_a_second():
    assert match([950], [(900, "periodic")], 900) == (1, 0)
    assert match([899.5], [(900, "periodic")], 900) == (1, 0)
    assert match([1000], [(900, "periodic")], 900) == (0, 1)
    assert match([898], [(900, "periodic")], 900) == (0, 1)


def test_a_change_driven_report_matches_only_within_a_second_and_each_real_report_once():
    assert match([100.8], [(100, "stimulus")], 900) == (1, 0)
    assert match([101.5], [(100, "stimulus")], 900) == (0, 1)
    assert match([100], [(100, "stimulus"), (100.5, "stimulus")], 900) == (1, 1)


def test_a_reported_change_below_the_threshold_has_no_twin_counterpart():
    s = seg((0, 2000), (100, 2010))
    assert replay(s, TEMP) == [] and match([100], [], 900) == (0, 0)


def test_stream_statistics_pool_segments_and_judge_only_with_enough_intervals():
    good = [seg(*[(i * 900.0, 2000) for i in range(12)], session="a"),
            seg(*[(i * 900.0, 2000) for i in range(12)], session="b")]
    ev = evaluate_stream(good, TEMP)
    assert (ev["intervals"], ev["matched"], ev["twin_reports"], ev["extra"]) == (22, 22, 22, 0)
    assert ev["eligible"] and ev["passed"] and ev["heartbeat_ratio"] == 1.0
    assert ev["min_interval_ok"] == 1.0 and ev["max_silence_ok"] == 1.0 and ev["early_change_ok"] is None
    short = evaluate_stream([seg((0, 2000), (900, 2000))], TEMP)
    assert not short["eligible"] and short["passed"] is None


def test_a_stream_of_bad_reports_fails_on_both_criteria():
    s = [seg(*[(i * 10.0, 2000 + 100 * i) for i in range(25)])]  # every 10 s, below min_s
    ev = evaluate_stream(s, TEMP)
    assert ev["eligible"] and not ev["passed"] and ev["matched_fraction"] < 0.95
    assert ev["min_interval_ok"] == 0.0


def test_non_numeric_values_cannot_be_replayed():
    with pytest.raises(TypeError, match="numeric"):
        evaluate_stream([seg((0, b"x"), (10, b"y"))], TEMP)
