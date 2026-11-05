"""Automation modes (protocol Addendum 20): `single`, `restart`, `queued`, `parallel`, as Home Assistant 2026.10.2 runs
them (homeassistant/helpers/script.py), and the run events the engine reports to a session that asks for them."""
from tests.lab.test_automations_permissive import home_session
from vesper.lab.automations import Engine


def auto(actions, **extra):
    return {"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}], "actions": actions, **extra}


def recording(s):
    calls, runs = [], []
    s.automation_command = lambda name, why, service, entity, *rest: calls.append((s.clock.now(), why, entity))
    s.automation_run = lambda name, why, event: runs.append((s.clock.now(), why, event))
    return calls, runs


def test_a_session_that_asks_is_told_of_each_run_the_engine_accepts(tmp_path):
    _, s = home_session(tmp_path)
    calls, runs = recording(s)
    e = Engine(s, permissive=True, automations=[auto([{"action": "notify.x"}])])
    (a,) = e.automations
    e._fire(a, "one")
    e._fire(a, "two")
    assert runs == [(s.clock.now(), "one", "start"), (s.clock.now(), "two", "start")]
    assert [c[1] for c in calls] == ["one", "two"]


def test_a_dropped_trigger_is_not_a_run_and_a_session_without_the_method_sees_nothing(tmp_path):
    _, s = home_session(tmp_path)
    runs = []
    s.automation_run = lambda name, why, event: runs.append((why, event))
    e = Engine(s, permissive=True, automations=[auto([{"delay": 60}, {"action": "notify.x"}])])
    (a,) = e.automations
    e._fire(a, "one")
    e._fire(a, "two")  # mode single, running: dropped
    assert runs == [("one", "start")]
    del s.automation_run
    (tmp_path / "2").mkdir()
    _, s2 = home_session(tmp_path / "2")
    e2 = Engine(s2, permissive=True, automations=[auto([{"action": "notify.x"}])])
    e2._fire(e2.automations[0], "one")  # no automation_run on the session: nothing to tell, no error
