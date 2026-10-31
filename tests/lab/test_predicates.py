"""Predicates over a run's trace (security-lab spec §4.4, plan W4a1): events, their order and timing, counts; each
verdict gives the records that decided it. A claim reads the same whether VESPER's engine or Home Assistant acted."""
import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.home import load_lab_home
from vesper.lab.predicates import PredicateError, check_predicate, evaluate

OPENS = {"actor": "intruder", "opens": "hall-porch"}
LOCKS = {"action": {"device": "front_lock", "feature": "state", "value": "LOCK"}}

TRACE = [
    {"t": 0.0, "kind": "meta"},
    {"t": 10.0, "kind": "report", "device": "front_door", "feature": "contact", "value": True, "physical_t": 2.0},
    {"t": 20.0, "kind": "input", "actor": "intruder", "op": "interact", "args": {"target": "hall-porch"},
     "outcome": {"ok": True, "kind": "door", "target": "hall-porch", "result": "opened"}},
    {"t": 21.0, "kind": "world", "event": "room", "actor": "intruder", "room": "hall", "previous": "porch"},
    {"t": 60.0, "kind": "automation", "automation": "lock_on_close", "device": "front_lock", "feature": "state",
     "value": "LOCK", "accepted": True},
    {"t": 61.0, "kind": "alert", "defense": "freshness", "device": "front_door", "feature": "contact", "age_s": 8.0},
]
HA_TRACE = [r for r in TRACE if r["kind"] != "automation"] + [
    {"t": 60.0, "kind": "input", "actor": "hub", "op": "command",
     "args": {"device": "front_lock", "feature": "state", "value": "LOCK"}, "outcome": {"accepted": True}}]


def test_an_event_holds_when_it_happens_and_names_the_record_that_shows_it():
    v = evaluate(OPENS, TRACE)
    assert v.holds and v.records == [TRACE[2]]
    assert not evaluate({"actor": "intruder", "opens": "bedroom-hall"}, TRACE).holds


def test_before_holds_when_the_first_comes_first_or_the_second_never_comes():
    assert evaluate({"before": [OPENS, LOCKS]}, TRACE).holds
    assert not evaluate({"before": [LOCKS, OPENS]}, TRACE).holds
    assert evaluate({"before": [OPENS, {"action": {"device": "front_lock", "value": "UNLOCK"}}]}, TRACE).holds
    assert not evaluate({"before": [{"actor": "intruder", "enters": "bedroom"}, LOCKS]}, TRACE).holds


def test_an_action_reads_the_same_from_vesper_s_engine_and_from_home_assistant():
    assert evaluate(LOCKS, TRACE).records == [TRACE[4]]
    assert evaluate(LOCKS, HA_TRACE).holds and evaluate({"before": [OPENS, LOCKS]}, HA_TRACE).holds


def test_a_gap_bounds_how_long_after_one_event_another_comes():
    door_report = {"report": {"device": "front_door", "feature": "contact"}}
    assert evaluate({"gap": [OPENS, LOCKS], "at_least_s": 40}, TRACE).holds  # 40 s after the intruder opened
    assert not evaluate({"gap": [OPENS, LOCKS], "at_most_s": 30}, TRACE).holds
    assert not evaluate({"gap": [LOCKS, door_report], "at_least_s": 0}, TRACE).holds  # the report came first
    v = evaluate({"gap": [OPENS, LOCKS], "at_least_s": 40}, TRACE)
    assert v.records == [TRACE[2], TRACE[4]] and v.detail == "40 s"


def test_counts_alerts_nets_and_the_combinators():
    alert = {"alert": {"defense": "freshness", "device": "front_door"}}
    assert evaluate({"count": alert, "at_least": 1}, TRACE).holds
    assert not evaluate({"count": alert, "at_least": 2}, TRACE).holds
    assert evaluate({"never": {"alert": {"defense": "physical_consistency"}}}, TRACE).holds
    assert evaluate({"all": [OPENS, alert]}, TRACE).holds
    assert not evaluate({"all": [OPENS, {"not": alert}]}, TRACE).holds
    assert evaluate({"any": [{"net": {"link": "coordinator"}}, alert]}, TRACE).holds
    assert evaluate({"actor": "intruder", "enters": "hall"}, TRACE).holds
    assert evaluate({"door": "hall-porch", "open": True}, [{"t": 3.0, "kind": "world", "event": "door",
                                                          "id": "hall-porch", "open": True}]).holds


@pytest.mark.parametrize("predicate, message", [
    ({"before": [OPENS]}, r"before: two events"),
    ({"actor": "intruder", "opens": "hall-porc"}, r"opens: unknown doorway 'hall-porc'"),
    ({"actor": "ghost", "opens": "hall-porch"}, r"actor: unknown actor 'ghost'"),
    ({"action": {"device": "front_lok"}}, r"action\.device: unknown device 'front_lok'; did you mean front_lock"),
    ({"report": {"device": "front_door", "feature": "colour"}}, r"report\.feature: front_door has no feature 'colour'"),
    ({"gap": [OPENS, LOCKS], "at_least_s": "a minute"}, r"at_least_s: 'a minute' is not a number"),
    ({"eventually": OPENS}, r"'eventually' is not a predicate"),
    ({"alert": {"defense": "firewall"}}, r"alert\.defense: 'firewall' is not a defense"),
])
def test_a_predicate_is_checked_against_the_home_when_the_scenario_loads(tmp_path, predicate, message):
    home = load_lab_home(write_home(tmp_path, LOCKED_HOME), catalog())
    with pytest.raises(PredicateError, match=message):
        check_predicate(predicate, home, actors={"player", "intruder"}, where="expect[0].predicate")
