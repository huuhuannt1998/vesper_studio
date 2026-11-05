"""Automation modes (protocol Addendum 20): `single`, `restart`, `queued`, `parallel`, as Home Assistant 2026.10.2 runs
them (homeassistant/helpers/script.py), and the run events the engine reports to a session that asks for them."""
from tests.lab.test_automations_permissive import home_session
from vesper.lab.automations import Engine


def auto(actions, **extra):
    return {"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}], "actions": actions, **extra}


def recording(s):
    calls, runs = [], []
    s.automation_command = lambda name, why, service, entity, *rest: calls.append((s.clock.now(), why, service))
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


HOLD = [{"action": "notify.on"}, {"delay": 300}, {"action": "notify.off"}]


def started(tmp_path, *autos):
    _, s = home_session(tmp_path)
    calls, runs = recording(s)
    e = Engine(s, permissive=True, automations=list(autos))  # the replay's engine (notify.* is recorded)
    assert e.refused == []
    e.start()
    return s, e, calls, runs


def at(s, e, a, *offsets):
    """Fire `a` at each offset (s) after now, named by its offset; then run the clock 2000 s past the first."""
    t0 = s.clock.now()
    for dt in offsets:
        s.clock.advance(t0 + dt - s.clock.now())
        e._fire(a, f"t{dt}")
    s.clock.advance(t0 + 2000 - s.clock.now())
    return t0


def rel(t0, rows):
    return [(round(t - t0), *rest) for t, *rest in rows]


def test_single_drops_a_trigger_during_a_run(tmp_path):
    s, e, calls, runs = started(tmp_path, auto(HOLD))
    t0 = at(s, e, e.automations[0], 0, 100, 400)
    assert rel(t0, calls) == [(0, "t0", "notify.on"), (300, "t0", "notify.off"), (400, "t400", "notify.on"),
                              (700, "t400", "notify.off")]
    assert rel(t0, runs) == [(0, "t0", "start"), (400, "t400", "start")]


def test_restart_stops_the_run_under_way_and_starts_again(tmp_path):
    s, e, calls, runs = started(tmp_path, auto(HOLD, mode="restart"))
    t0 = at(s, e, e.automations[0], 0, 100, 250)
    assert rel(t0, calls) == [(0, "t0", "notify.on"), (100, "t100", "notify.on"), (250, "t250", "notify.on"),
                              (550, "t250", "notify.off")]
    assert rel(t0, runs) == [(0, "t0", "start"), (100, "t0", "cancel"), (100, "t100", "start"),
                             (250, "t100", "cancel"), (250, "t250", "start")]
    assert not e.automations[0].running


def test_restart_ends_a_wait_under_way_and_its_timeout_never_resumes_it(tmp_path):
    wait = {"wait_for_trigger": [{"trigger": "time", "at": "23:00"}], "timeout": 200}
    s, e, calls, runs = started(tmp_path, auto([{"action": "notify.on"}, wait, {"action": "notify.off"}],
                                               mode="restart"))
    t0 = at(s, e, e.automations[0], 0, 100)
    assert rel(t0, calls) == [(0, "t0", "notify.on"), (100, "t100", "notify.on"), (300, "t100", "notify.off")]
    assert e._waits == []


def test_queued_waits_for_the_run_before_it_and_drops_beyond_max(tmp_path):
    s, e, calls, runs = started(tmp_path, auto(HOLD, mode="queued", max=3))
    t0 = at(s, e, e.automations[0], 0, 10, 20, 30)
    assert rel(t0, calls) == [(0, "t0", "notify.on"), (300, "t0", "notify.off"), (300, "t10", "notify.on"),
                              (600, "t10", "notify.off"), (600, "t20", "notify.on"), (900, "t20", "notify.off")]
    assert rel(t0, runs) == [(0, "t0", "start"), (10, "t10", "start"), (20, "t20", "start")]
    assert not e.automations[0].running


def test_queued_and_parallel_hold_ten_runs_by_default(tmp_path):
    q, p = auto(HOLD, mode="queued"), {**auto(HOLD, mode="parallel"), "alias": "p"}
    s, e, calls, runs = started(tmp_path, q, p)
    t0 = s.clock.now()
    for dt in range(11):
        s.clock.advance(t0 + dt - s.clock.now())
        e._fire(e.automations[0], f"q{dt}")
        e._fire(e.automations[1], f"p{dt}")
    s.clock.advance(4000)
    assert sum(why.startswith("q") for _, why, ev in runs) == 10
    assert sum(why.startswith("p") for _, why, ev in runs) == 10
    assert sum(service == "notify.on" for _, why, service in calls if why.startswith("q")) == 10
    parallel = [round(t - t0) for t, why, service in calls if why.startswith("p") and service == "notify.off"]
    assert parallel == [300 + k for k in range(10)]


def test_parallel_runs_beside_the_run_under_way_up_to_max(tmp_path):
    s, e, calls, runs = started(tmp_path, auto(HOLD, mode="parallel", max=2))
    t0 = at(s, e, e.automations[0], 0, 10, 20, 400)
    assert rel(t0, calls) == [(0, "t0", "notify.on"), (10, "t10", "notify.on"), (300, "t0", "notify.off"),
                              (310, "t10", "notify.off"), (400, "t400", "notify.on"), (700, "t400", "notify.off")]
    assert [why for _, why, ev in runs] == ["t0", "t10", "t400"]


def test_the_modes_load_without_approximation_and_bad_values_are_refused(tmp_path):
    _, s = home_session(tmp_path)
    good = [auto([], mode=m, max=2, max_exceeded="silent") for m in ("single", "restart", "queued", "parallel")]
    for permissive in (False, True):
        e = Engine(s, permissive=permissive, automations=good)
        assert e.refused == [] and [a.approximations for a in e.automations] == [[]] * 4
    bad = [auto([], mode="bogus"), auto([], mode="queued", max=1), auto([], mode="parallel", max="x"),
           auto([], max_exceeded="loud")]
    e = Engine(s, permissive=True, automations=bad)
    assert e.automations == []
    assert [r["reason"] for r in e.refused] == [
        "mode 'bogus' is not one of parallel, queued, restart, single", "max: 1 is less than 2",
        "max: 'x' is not a number", "max_exceeded: 'loud' is not one of CRITICAL, FATAL, ERROR, WARNING, WARN, "
        "INFO, DEBUG, NOTSET, SILENT"]


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
