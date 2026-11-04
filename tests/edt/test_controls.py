"""vesper.edt.controls: the analytic expectations of the synthetic controls and their scoring (protocol addendum 4)."""
import pytest

from vesper.edt import controls as c

DAY = 86400.0
H = 3600.0
EVENTS = [{"sunrise": d * DAY + 6 * H, "sunset": d * DAY + 18 * H} for d in range(3)]  # a plain 06:00-18:00 sun


# ---- expected firing times ------------------------------------------------------------------------------------------

def test_sun_firings_are_each_days_event_plus_offset_inside_the_measured_window():
    assert c.sun_firings(EVENTS, "sunrise", 0) == [DAY + 6 * H, 2 * DAY + 6 * H]
    assert c.sun_firings(EVENTS, "sunset", -1800) == [DAY + 17.5 * H, 2 * DAY + 17.5 * H]


def test_sun_firings_skip_a_day_without_the_event():
    events = [EVENTS[0], {"sunrise": None, "sunset": DAY + 23 * H}, EVENTS[2]]
    assert c.sun_firings(events, "sunrise", 0) == [2 * DAY + 6 * H]


def test_a_late_offset_from_the_warmup_day_lands_in_the_window():
    events = [{"sunrise": 6 * H, "sunset": d * DAY + 23 * H} for d in range(3)]
    assert c.sun_firings(events, "sunset", 2 * H) == [DAY + 1 * H, 2 * DAY + 1 * H]


def test_time_firings():
    assert c.time_firings(7 * H) == [DAY + 7 * H, 2 * DAY + 7 * H]


def test_sun_condition_after_sunrise_and_before_sunset_with_offsets():
    assert c.sun_condition_ok(EVENTS, DAY + 7 * H, {"after": "sunrise"})
    assert not c.sun_condition_ok(EVENTS, DAY + 5 * H, {"after": "sunrise"})
    assert not c.sun_condition_ok(EVENTS, DAY + 6.5 * H, {"after": "sunrise", "after_offset": 3600})
    assert c.sun_condition_ok(EVENTS, DAY + 17 * H, {"before": "sunset"})
    assert not c.sun_condition_ok(EVENTS, DAY + 17.75 * H, {"before": "sunset", "before_offset": -1800})


def test_sun_condition_margin_is_the_distance_to_the_boundary():
    assert c.sun_condition_margin(EVENTS, DAY + 7 * H, {"after": "sunrise", "after_offset": 600}) == \
        pytest.approx(3000)


def test_time_condition_plain_and_across_midnight():
    assert c.time_condition_ok(DAY + 6.5 * H, 6 * H, 7 * H)
    assert not c.time_condition_ok(DAY + 7.5 * H, 6 * H, 7 * H)
    assert c.time_condition_ok(DAY + 23.5 * H, 22 * H, 2 * H)
    assert c.time_condition_ok(DAY + 1 * H, 22 * H, 2 * H)
    assert not c.time_condition_ok(DAY + 12 * H, 22 * H, 2 * H)
    assert c.time_condition_margin(DAY + 6.5 * H, 6 * H, 7 * H) == pytest.approx(1800)


def test_numeric_firings_follow_home_assistants_armed_rule():
    reports = [(10.05, 5.0), (100.05, 3.0), (200.05, 1.0), (300.05, 0.5), (400.05, 3.0), (500.05, 1.0)]
    # initial 5 > 2: armed; 1.0 at 200 fires and disarms; 0.5 stays; 3.0 re-arms; 1.0 at 500 fires again
    assert c.numeric_firings(5.0, reports, below=2.0) == [200.05, 500.05]
    # already in range at start: not armed until it leaves the range
    assert c.numeric_firings(1.0, [(50.05, 0.5), (60.05, 3.0), (70.05, 1.5)], below=2.0) == [70.05]
    assert c.numeric_firings(1.0, reports, above=2.5) == [10.05, 400.05]
    assert c.numeric_firings(None, [(10.05, 1.0)], below=2.0) == [10.05]  # no state at start: armed


def test_numeric_firings_with_both_bounds():
    assert c.numeric_firings(0.0, [(1.0, 5.0), (2.0, 20.0), (3.0, 6.0)], above=4.0, below=10.0) == [1.0, 3.0]


# ---- outcome and divergences ----------------------------------------------------------------------------------------

def test_expected_outcome_reads_the_measured_window_only():
    assert c.expected_outcome([0.5 * DAY, DAY + 7.25 * H, 2 * DAY + 6 * H, 3 * DAY + H]) == \
        {"fired": True, "first_tod_h": 7.25, "flaps": False}
    assert c.expected_outcome([0.5 * DAY]) == {"fired": False, "first_tod_h": None, "flaps": False}


def test_expected_outcome_flaps_on_seven_distinct_times_in_an_hour():
    seven = [DAY + 10 * H + 500 * i for i in range(7)]
    assert c.expected_outcome(seven)["flaps"] is True
    assert c.expected_outcome(seven[:6] + seven[:1])["flaps"] is False  # repeats of one instant count once


def test_expected_divergences():
    same = {"fired": True, "first_tod_h": 7.0, "flaps": False}
    never = {"fired": False, "first_tod_h": None, "flaps": False}
    assert c.expected_divergences({"a": same, "b": same}) == (set(), set())
    assert c.expected_divergences({"a": same, "b": never}) == ({"fires_somewhere_only"}, set())
    late = {**same, "first_tod_h": 10.5}
    assert c.expected_divergences({"a": same, "b": late}) == ({"time_spread"}, set())
    assert c.expected_divergences({"a": same, "b": {**same, "first_tod_h": 9.9}}) == (set(), set())
    wrap = {**same, "first_tod_h": 23.5}  # 23:30 and 01:00 are 1.5 h apart the short way round
    assert c.expected_divergences({"a": wrap, "b": {**same, "first_tod_h": 1.0}}) == (set(), set())
    flap = {**same, "flaps": True}
    assert c.expected_divergences({"a": flap, "b": same}) == ({"flaps"}, set())
    assert c.expected_divergences({"a": flap, "b": flap}) == (set(), {"flaps"})


def test_spread_margin_is_the_distance_of_the_largest_gap_from_three_hours():
    o = {k: {"fired": True, "first_tod_h": t, "flaps": False} for k, t in (("a", 6.0), ("b", 8.5), ("c", 7.0))}
    assert c.spread_margin_h(o) == pytest.approx(0.5)
    assert c.spread_margin_h({"a": o["a"]}) is None


# ---- scoring --------------------------------------------------------------------------------------------------------

def _control(name, kind, expected, divs):
    return {"name": name, "kind": kind, "hash": name, "expected": expected, "divergences": sorted(divs),
            "static": []}


def _auto(outcomes, divs, bound=True):
    return {"bound": {} if bound else None, "outcomes": outcomes, "divergences": sorted(divs), "static": [],
            "refused": {}, "unbindable": None if bound else "no"}


def _o(fired, tod=None):
    return {"fired": fired, "first_tod_h": tod, "flaps": False, "night_light": False, "night_cover": False}


def test_score_positive_counts_detections_false_positives_and_time_errors():
    exp_spread = {"e1": {"fired": True, "first_tod_h": 6.0, "flaps": False},
                  "e2": {"fired": True, "first_tod_h": 10.0, "flaps": False}}
    exp_flat = {"e1": {"fired": True, "first_tod_h": 7.0, "flaps": False},
                "e2": {"fired": True, "first_tod_h": 7.0, "flaps": False}}
    controls = [_control("s", "sun_trigger", exp_spread, {"time_spread"}),
                _control("t", "time_trigger", exp_flat, set()),
                _control("m", "numeric_state", exp_spread, {"time_spread", "fires_somewhere_only"})]
    result = {"automations": {
        "s": _auto({"e1": _o(True, 6.0 + 1 / 60), "e2": _o(True, 10.0 - 3 / 60)}, {"time_spread"}),
        "t": _auto({"e1": _o(True, 7.0), "e2": _o(True, 7.0)}, {"flaps"}),   # a false positive
        "m": _auto({"e1": _o(True, 6.0), "e2": _o(True, 10.0)}, {"time_spread"}),  # fires_somewhere_only missed
    }}
    s = c.score_positive(controls, result)
    assert s["expected_divergences"] == 3 and s["detected"] == 2
    assert s["detection_rate"] == pytest.approx(2 / 3)
    assert s["by_divergence_kind"]["fires_somewhere_only"] == {"expected": 1, "detected": 0}
    assert s["by_automation_kind"]["sun_trigger"]["detected"] == 1
    assert s["false_positives"] == [{"name": "t", "kind": "time_trigger", "divergence": "flaps"}]
    assert s["missed"] == [{"name": "m", "kind": "numeric_state", "divergence": "fires_somewhere_only"}]
    assert s["first_time"]["n"] == 6
    assert s["first_time"]["mae_min"] == pytest.approx(4 / 6)
    assert s["first_time"]["max_min"] == pytest.approx(3)
    assert s["fired_agreement"] == {"n": 6, "agree": 6}
    assert s["pass"] is False


def test_score_positive_passes_and_counts_an_unbound_control_as_missed():
    exp = {"e1": {"fired": True, "first_tod_h": 6.0, "flaps": False},
           "e2": {"fired": False, "first_tod_h": None, "flaps": False}}
    ok = c.score_positive([_control("a", "numeric_state", exp, {"fires_somewhere_only"})],
                          {"automations": {"a": _auto({"e1": _o(True, 6.0), "e2": _o(False)},
                                                      {"fires_somewhere_only"})}})
    assert ok["pass"] is True and ok["detection_rate"] == 1.0 and ok["false_positives"] == []
    bad = c.score_positive([_control("a", "numeric_state", exp, {"fires_somewhere_only"})],
                           {"automations": {"a": _auto({}, set(), bound=False)}})
    assert bad["detected"] == 0 and bad["not_run"] == ["a"] and bad["pass"] is False


def test_score_positive_reports_fired_disagreements_per_environment():
    exp = {"e1": {"fired": True, "first_tod_h": 6.0, "flaps": False},
           "e2": {"fired": True, "first_tod_h": 6.0, "flaps": False}}
    s = c.score_positive([_control("a", "time_trigger", exp, set())],
                         {"automations": {"a": _auto({"e1": _o(True, 6.0), "e2": _o(False)},
                                                     {"fires_somewhere_only"})}})
    assert s["fired_agreement"] == {"n": 2, "agree": 1}
    assert s["fired_disagreements"] == [{"name": "a", "env": "e2", "expected": True, "observed": False}]


def test_score_negative():
    controls = [{"name": "n1", "kind": "numeric_state", "hash": "n1"}, {"name": "n2", "kind": "state", "hash": "n2"},
                {"name": "n3", "kind": "state", "hash": "n3"}]
    result = {"automations": {"n1": _auto({"e1": _o(False), "e2": _o(False)}, set()),
                              "n2": _auto({"e1": _o(True, 3.0), "e2": _o(False)}, {"fires_somewhere_only"}),
                              "n3": _auto({}, set(), bound=False)}}
    s = c.score_negative(controls, result)
    assert s["n"] == 3 and s["ran"] == 2 and s["not_run"] == ["n3"]
    assert s["flagged"] == [{"name": "n2", "kind": "state", "divergences": ["fires_somewhere_only"]}]
    assert s["fired"] == [{"name": "n2", "kind": "state", "envs": ["e1"]}]
    assert s["flag_rate"] == pytest.approx(1 / 2) and s["pass"] is False
    clean = c.score_negative(controls[:1], result)
    assert clean["pass"] is True and clean["flag_rate"] == 0.0


# ---- the corpora ----------------------------------------------------------------------------------------------------

def test_negative_controls_are_distinct_and_read_a_sensor():
    neg = c.negative_controls()
    assert 10 <= len(neg) <= 25
    assert len({n["hash"] for n in neg}) == len(neg)
    for n in neg:
        trig = n["automation"]["trigger"][0]
        assert trig["platform"] in ("numeric_state", "state", "time", "sun")
        assert n["automation"]["action"] == [c.ACTION]


def _env(sunrise_h, sunset_h, temps):
    events = [{"sunrise": d * DAY + sunrise_h * H, "sunset": d * DAY + sunset_h * H} for d in range(3)]
    flat = (0, [])
    return {"events": events, "series": {"porch_temperature": (temps[0][1], temps),
                                         "living_illuminance": flat, "porch_illuminance": flat}}


def test_positive_controls_expect_what_the_site_says_and_drop_boundary_cases():
    cold = [(10.05, 5.0)] + [(DAY + k * H + 0.05, -5.0 if k % 24 < 6 else 5.0) for k in range(48)]
    warm = [(10.05, 25.0)] + [(DAY + k * H + 0.05, 25.0) for k in range(48)]
    envs = {"winter": _env(8.75, 15.5, cold), "summer": _env(3.75, 21.9, warm)}
    keep, dropped = c.positive_controls(envs)
    assert keep and all(k["hash"] for k in keep)
    names = [k["name"] for k in keep] + [d["name"] for d in dropped]
    assert len(names) == len(set(names))
    sunrise0 = next(k for k in keep if k["spec"]["trigger"] == ["sun", "sunrise", 0.0])
    assert sunrise0["expected"]["winter"]["first_tod_h"] == pytest.approx(8.75)
    assert sunrise0["expected"]["summer"]["first_tod_h"] == pytest.approx(3.75)
    assert sunrise0["divergences"] == ["time_spread"]
    t = next(k for k in keep if k["kind"] == "time_trigger")
    assert t["divergences"] == [] and all(o["fired"] for o in t["expected"].values())
    # 07:25 after sunrise: summer (03:45) fires, winter (08:45) does not
    after = next(k for k in keep if k["spec"]["trigger"] == ["time", 7 * 3600 + 25 * 60])
    assert after["expected"]["summer"]["fired"] and not after["expected"]["winter"]["fired"]
    assert after["divergences"] == ["fires_somewhere_only"]
    assert all(d["reason"] for d in dropped)
    # the porch-temperature 'below' controls cross only where it is cold
    below = [k for k in keep if k["spec"]["trigger"][:3] == ["numeric", "porch_temperature", "below"]]
    assert below and any(k["expected"]["winter"]["fired"] for k in below)
    assert not any(k["expected"]["summer"]["fired"] for k in below if k["spec"]["trigger"][3] < 25)


def test_a_firing_within_ten_minutes_of_a_condition_boundary_drops_the_candidate():
    envs = {"a": _env(7.25 + 5 / 60, 18.0, [(10.05, 0.0)]), "b": _env(5.0, 20.0, [(10.05, 0.0)])}
    keep, dropped = c.positive_controls(envs)
    # 07:25 after sunrise at 07:20: 5 min from the boundary
    d = next(d for d in dropped if d["spec"]["trigger"] == ["time", 7 * 3600 + 25 * 60])
    assert "boundary" in d["reason"]
    assert not any(k["spec"]["trigger"] == ["time", 7 * 3600 + 25 * 60] and k["kind"] == "time_sun_condition"
                   for k in keep)


def test_trace_series_reads_the_named_sensors_in_delivery_order(tmp_path):
    import gzip
    import json
    path = tmp_path / "trace.jsonl.gz"
    twin, feature = c.SERIES["porch_temperature"][1:]
    header = {"env": {"key": "k"}, "initial_state": {twin: {feature: 3.0}}}
    rows = [{"t": 20, "twin_id": twin, "feature": feature, "value": 2.0, "observed_t": 20},
            {"t": 10, "twin_id": twin, "feature": feature, "value": 1.0, "observed_t": 10},
            {"t": 10, "twin_id": twin, "feature": "humidity", "value": 50, "observed_t": 10},
            {"t": 3 * DAY + 5, "twin_id": twin, "feature": feature, "value": 9.0, "observed_t": 3 * DAY + 5}]
    with gzip.open(path, "wt") as f:
        f.write(json.dumps(header) + "\n" + "".join(json.dumps(r) + "\n" for r in rows))
    h, series = c.trace_series(path)
    assert h["env"]["key"] == "k"
    assert series["porch_temperature"] == (3.0, [(10.05, 1.0), (20.05, 2.0)])
    assert series["living_illuminance"] == (None, [])
