"""Revision R11-P1's script and the oracle's two attribution knobs (protocol Addendum 22): the binder discount's
arithmetic, the 2 x 2 attribution of the time spread, the gate-open guards' classes, the occupancy range and the full
precision-recall curve."""
import importlib.util
import sys
from pathlib import Path

import pytest

from vesper.edt.oracle import (
    CLOCK,
    SENSED,
    SUN,
    divergences,
    frame_divergences,
    frame_invariance,
    frame_restricted_divergences,
    frames_of,
    outcome,
    rich_outcome,
    site_of,
)
from vesper.edt.types import Env, Firing
from vesper.env.sun import sun_events

ROOT = Path(__file__).resolve().parents[2]
DAY, H = 86400.0, 3600.0
JAN = Env("Stockholm, Sweden", "2026-01-14", 0.0, 4)
JUL = Env("Stockholm, Sweden", "2026-07-15", 0.0, 4)
SING = Env("Singapore", "2026-01-14", 0.0, 4)
ACTION = [{"action": "switch.turn_on", "target": {"entity_id": "switch.living_room_plug"}}]
SUNSET = {"trigger": [{"platform": "sun", "event": "sunset"}], "action": ACTION}
SENSED_RULE = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.porch_temperature", "above": 20}],
               "action": ACTION}


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r11p1", ROOT / "scripts/revision/r11p1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p = _load()


def sunset(env: Env, day: int = 1) -> float:
    return sun_events(site_of(env), day)["sunset"]


def fire(t: float) -> list[Firing]:
    return [Firing(t, "a", "switch.turn_on", "switch.living_room_plug")]


def _judge(bound, firings, **kw):
    outs = {e: outcome(f, site_of(e)) for e, f in firings.items()}
    first_t = {e: rich_outcome(f)["first_t"] for e, f in firings.items()}
    return outs, first_t, frame_divergences(outs, first_t, frames_of(bound), **kw)


# ---- A22.2: the oracle's knobs --------------------------------------------------------------------------------------

def test_the_knobs_default_on_and_are_identical_to_the_published_relation():
    firings = {JAN: fire(sunset(JAN)), JUL: fire(sunset(JUL))}
    outs, ft, published = _judge(SUNSET, firings)
    fr = frames_of(SUNSET)
    assert published == set()
    assert frame_divergences(outs, ft, fr, sun_normalise=True, sensed_exempt=True) == published
    assert frame_invariance(outs, ft, fr, sun_normalise=True, sensed_exempt=True) == frame_invariance(outs, ft, fr)


def test_without_sun_normalisation_the_sun_frame_is_judged_by_clock_time():
    firings = {JAN: fire(sunset(JAN)), JUL: fire(sunset(JUL))}
    assert (sunset(JUL) - sunset(JAN)) > 3 * H
    _, _, kinds = _judge(SUNSET, firings, sun_normalise=False)
    assert kinds == {"time_spread"}
    _, _, kinds = _judge(SUNSET, firings, sensed_exempt=False)  # no sensed frame: the exemption knob is inert
    assert kinds == set()


def test_a_sun_frame_without_a_reference_is_judged_by_clock_time_when_normalisation_is_off():
    tpl = {"trigger": [{"platform": "template", "value_template": "{{ is_state('sun.sun', 'below_horizon') }}"}],
           "action": ACTION}
    assert frames_of(tpl).frames == {SUN} and not frames_of(tpl).sun_refs
    firings = {JAN: fire(DAY + 15 * H), JUL: fire(DAY + 22 * H)}
    assert _judge(tpl, firings)[2] == set()  # published: not computable, invariant
    assert _judge(tpl, firings, sun_normalise=False)[2] == {"time_spread"}


def test_without_the_exemption_the_sensed_frame_is_judged_by_clock_time():
    firings = {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 14 * H)}
    assert _judge(SENSED_RULE, firings)[2] == set()
    assert _judge(SENSED_RULE, firings, sensed_exempt=False)[2] == {"time_spread"}
    assert _judge(SENSED_RULE, firings, sun_normalise=False)[2] == set()
    # within 3 h of clock time: still invariant without the exemption
    assert _judge(SENSED_RULE, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 8 * H)}, sensed_exempt=False)[2] == set()


def test_with_both_off_the_time_spread_is_the_clock_relations_for_any_frame_set():
    mixed = {"trigger": [{"platform": "sun", "event": "sunset"}],
             "condition": [{"condition": "numeric_state", "entity_id": "sensor.porch_temperature", "below": 5}],
             "action": ACTION}
    clock_rule = {"trigger": [{"platform": "time", "at": "07:00:00"}], "action": ACTION}
    cases = [(SUNSET, {JAN: fire(sunset(JAN)), JUL: fire(sunset(JUL))}),
             (SENSED_RULE, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 14 * H), SING: []}),
             (mixed, {JAN: fire(sunset(JAN)), JUL: fire(sunset(JUL))}),
             (mixed, {JAN: fire(sunset(JAN)), JUL: fire(sunset(JAN) + 3600.0)}),
             (clock_rule, {JAN: fire(DAY + 7 * H), JUL: fire(DAY + 7 * H)})]
    for bound, firings in cases:
        outs, ft, kinds = _judge(bound, firings, sun_normalise=False, sensed_exempt=False)
        assert kinds == divergences(outs)


def test_the_exemption_knob_cannot_be_combined_with_the_crossing_reference():
    outs, ft, _ = _judge(SENSED_RULE, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 14 * H)})
    with pytest.raises(ValueError):
        frame_divergences(outs, ft, frames_of(SENSED_RULE), sensed_exempt=False, sensed_crossing={JAN: None})


def test_the_restricted_divergences_pass_the_knobs_through():
    outs, ft, _ = _judge(SENSED_RULE, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 14 * H)})
    fr = frames_of(SENSED_RULE)
    assert frame_restricted_divergences(outs, ft, fr, [JAN, JUL]) == set()
    assert frame_restricted_divergences(outs, ft, fr, [JAN, JUL], sensed_exempt=False) == {"time_spread"}


def test_frame_invariance_reports_each_frame_under_the_knobs():
    outs, ft, _ = _judge(SENSED_RULE, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 14 * H)})
    assert frame_invariance(outs, ft, frames_of(SENSED_RULE), sensed_exempt=False) == {SENSED: False}
    both = {"trigger": [{"platform": "time", "at": "07:00:00"}],
            "condition": [{"condition": "numeric_state", "entity_id": "sensor.porch_temperature", "below": 5}]}
    inv = frame_invariance(outs, ft, frames_of(both), sensed_exempt=False)
    assert inv == {CLOCK: False, SENSED: False}


# ---- A22.2: the cells and the attribution ---------------------------------------------------------------------------

def test_cells_are_named_and_ordered():
    assert p.CELLS == {"clock_only": (False, False), "sun_normalisation_only": (True, False),
                       "exemption_only": (False, True), "published": (True, True)}


def test_attribution_splits_the_reduction_into_two_main_effects_and_an_interaction():
    a = p.attribution({"clock_only": 709, "sun_normalisation_only": 300, "exemption_only": 500, "published": 8})
    assert a == {"reduction": 701, "by_sun_normalisation_alone": 409, "by_exemption_alone": 209,
                 "interaction": 83}
    assert a["by_sun_normalisation_alone"] + a["by_exemption_alone"] + a["interaction"] == a["reduction"]


def test_frame_set_label():
    assert p.frame_set_label([]) == "none"
    assert p.frame_set_label(["sun", "clock"]) == "clock+sun"


# ---- A22.1: the binder discount ------------------------------------------------------------------------------------

def test_the_discount_rule_as_coded():
    # O + (c - m) + u (1 - m / c): 51 + 106 + 2 x 106/146
    assert p.discount(51, 146, 40, 2) == pytest.approx(158.4521, abs=1e-4)
    assert p.discount(51, 146, 40, 2, uncertain=4, uncertain_weight=0.5) == pytest.approx(
        51 + (146 - 42) + 2 * (1 - 42 / 146))
    line = p.discount_line(51, 146, 40, 2, 4)
    assert "51" in line and "146" in line and "40" in line and "158.4521" in line


def test_the_reviews_readings():
    r = p.review_readings(others=51, sensed=148, correct=75, correct_conv=44, misbind=51, uncertain=9)
    assert r["precision_over_decided"]["D"] == pytest.approx(51 + 148 * 119 / 170)
    assert r["misbind_share_over_all"]["D"] == pytest.approx(51 + 148 * (1 - 51 / 179))


# ---- A22.3: the guards ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,cls", [
    ("person.someone == home", "presence"),
    ("device_tracker.phone == not_home", "presence"),
    ("group.family == home", "presence"),
    ("input_boolean.heating_season == on", "env_physical"),
    ("input_boolean.its_dark == on", "env_physical"),
    ("input_number.outside_temp in (5, 20)", "env_physical"),
    ("input_boolean.night_mode == off", "env_time_season"),
    ("input_select.season in [autumn, winter]", "env_time_season"),
    ("input_boolean.guest_mode == off", "other"),
    ("input_boolean.holiday == on", "other"),
    ("input_boolean.today_done == off", "other"),
    ("person.someone[source] == gps", "presence"),
])
def test_guard_class(text, cls):
    assert p.guard_class(text) == cls


def test_guard_parse_and_numeric():
    assert p.parse_guard("input_number.t in (18, 22)") == ("input_number.t", None, "in (18, 22)")
    assert p.parse_guard("person.me[source] == gps") == ("person.me", "source", "== gps")
    assert p.is_numeric_guard("input_number.t > 18") and p.is_numeric_guard("input_number.t in (18, 22)")
    assert not p.is_numeric_guard("input_select.m in [a, b]")


def test_flag_class_takes_environmental_first_then_presence():
    assert p.flag_class(["other", "env_time_season"]) == "environmental"
    assert p.flag_class(["presence", "other"]) == "presence"
    assert p.flag_class(["other"]) == "other"


# ---- A22.4: the occupancy range -------------------------------------------------------------------------------------

def test_occupancy_rate():
    assert p.occupancy_rate(199, 5926, 0.153, 0, 0) == pytest.approx(199 / 5926)
    assert p.occupancy_rate(199, 5926, 0.153, 1000, 2762) == pytest.approx((199 + 0.153 * 3762) / 6926)
    assert p.occupancy_rate(199, 5926, 0.0, 1000, 2762) == pytest.approx(199 / 6926)


def test_union_bounds():
    assert p.union_bounds([7257, 1112, 395]) == (7257, 8764)


# ---- A22.5: the curve -----------------------------------------------------------------------------------------------

S = [0.9, 0.8, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
Y = [1, 1, 0, 1, 0, 0, 1, 0, 0, 0]


def test_the_full_curve_has_one_point_per_distinct_score():
    c = p.pr_curve(S, Y)
    assert [pt["passed"] for pt in c] == [1, 3, 4, 5, 6, 7, 8, 9, 10]
    assert c[1] == {"threshold": 0.8, "passed": 3, "tp": 2, "precision": pytest.approx(2 / 3), "recall": 0.5}
    assert c[-1]["recall"] == 1.0


def test_average_precision_is_the_step_sum():
    ap = p.average_precision(S, Y)
    assert ap == pytest.approx(1.0 * 0.25 + (2 / 3) * 0.25 + 0.75 * 0.25 + (4 / 7) * 0.25)


def test_recall_at_budget_takes_the_largest_threshold_block_within_it():
    r = p.recall_at_budget(S, Y, 0.35)  # 3.5 of 10: the blocks pass 1, 3, 4 ...; 3 fits
    assert (r["passed"], r["recall"]) == (3, 0.5)
    assert p.recall_at_budget(S, Y, 0.05)["passed"] == 0
