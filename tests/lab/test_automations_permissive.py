"""The engine's permissive mode: an action it cannot execute is recorded, not a reason to refuse the automation (a
later task replays thousands of public automations through the engine); strict mode stays as it was."""
from tests.lab.test_automations import lab
from vesper.lab.automations import Engine


def home_session(tmp_path):
    _, ids, s = lab(tmp_path, "[]\n")
    return ids, s


def auto(actions, **extra):
    return {"alias": "a", "triggers": [{"trigger": "time", "at": "08:00"}], "actions": actions, **extra}


def test_notify_and_climate_actions_are_recorded_with_their_targets(tmp_path):
    _, s = home_session(tmp_path)
    e = Engine(s, permissive=True, automations=[auto([
        {"action": "notify.mobile_app", "target": {"entity_id": "notify.mobile_app_phone"}},
        {"action": "climate.set_temperature", "target": {"entity_id": ["climate.hall", "climate.den"]},
         "data": {"temperature": 21}}])])
    assert e.refused == []
    (a,) = e.automations
    assert a.actions == [
        {"kind": "record", "service": "notify.mobile_app", "targets": ["notify.mobile_app_phone"]},
        {"kind": "record", "service": "climate.set_temperature", "targets": ["climate.hall", "climate.den"]}]


def test_running_a_recorded_action_calls_the_session_once_per_target_with_no_twin(tmp_path):
    _, s = home_session(tmp_path)
    calls = []
    s.automation_command = lambda *args: calls.append(args)
    e = Engine(s, permissive=True, automations=[auto([
        {"action": "climate.set_temperature", "entity_id": "climate.hall"},
        {"action": "notify.mobile_app", "target": {"entity_id": ["notify.a", "notify.b"]}}])])
    (a,) = e.automations
    e._fire(a, "test")
    assert calls == [("a", "test", "climate.set_temperature", "climate.hall", "", "", None),
                     ("a", "test", "notify.mobile_app", "notify.a", "", "", None),
                     ("a", "test", "notify.mobile_app", "notify.b", "", "", None)]
    assert not a.running


def test_an_action_with_no_target_is_still_recorded_once(tmp_path):
    _, s = home_session(tmp_path)
    calls = []
    s.automation_command = lambda *args: calls.append(args)
    e = Engine(s, permissive=True, automations=[auto([{"action": "notify.notify", "data": {"message": "hi"}}])])
    e._fire(e.automations[0], "test")
    assert calls == [("a", "test", "notify.notify", "", "", "", None)]


def test_a_mode_other_than_single_loads_as_single_and_says_so(tmp_path):
    _, s = home_session(tmp_path)
    e = Engine(s, permissive=True, automations=[auto([], mode="restart"), auto([], mode="single"), auto([])])
    assert e.refused == []
    assert [a.approximations for a in e.automations] == [["mode restart run as single"], [], []]


def test_a_template_condition_is_still_refused(tmp_path):
    _, s = home_session(tmp_path)
    e = Engine(s, permissive=True, automations=[auto([], condition=[{"condition": "template",
                                                                      "value_template": "{{ true }}"}])])
    assert e.automations == []
    assert e.refused == [{"automation": "a", "reason": "condition 'template' is not supported"}]


def test_something_that_is_not_a_service_is_still_refused(tmp_path):
    _, s = home_session(tmp_path)
    e = Engine(s, permissive=True, automations=[auto([{"wait_template": "{{ x }}"}]), auto([{"action": "{{ svc }}"}])])
    assert e.automations == [] and len(e.refused) == 2


def test_strict_mode_refuses_what_permissive_records(tmp_path):
    _, s = home_session(tmp_path)
    rules = [auto([{"action": "notify.mobile_app", "target": {"entity_id": "notify.mobile_app_phone"}}]),
             auto([], mode="restart")]
    for strict in (Engine(s, automations=rules), Engine(s, permissive=False, automations=rules)):
        assert strict.automations == []
        assert [r["reason"] for r in strict.refused] == ["action 'notify.mobile_app' is not supported",
                                                          "mode 'restart' is not supported (single only)"]


def test_given_automations_replace_the_homes_file(tmp_path):
    _, _, s = lab(tmp_path, "- {alias: from the file, triggers: [{trigger: time, at: '08:00'}], actions: []}\n")
    assert [a.name for a in Engine(s).automations] == ["from the file"]
    assert [a.name for a in Engine(s, automations=[auto([])]).automations] == ["a"]
    assert Engine(s, automations=[]).automations == []


def test_a_recorded_action_fires_through_the_real_session_and_sends_nothing(tmp_path):
    _, s = home_session(tmp_path)
    s.start(spawn_player=False)  # no engine of its own: the permissive one below is the test's
    e = Engine(s, permissive=True, automations=[auto([
        {"action": "notify.mobile_app", "target": {"entity_id": "notify.mobile_app_phone"}}])])
    e.start()
    s.clock.advance(2 * 3600)  # 07:00 -> 09:00: the 08:00 trigger fired
    (r,) = [r for r in s.records if r["kind"] == "automation"]
    assert (r["automation"], r["service"], r["entity"], r["device"], r["feature"], r["value"], r["accepted"]) == (
        "a", "notify.mobile_app", "notify.mobile_app_phone", "", "", None, True)
    assert r["trigger"].startswith("time")
    assert not [r for r in s.records if r["kind"] == "input"]  # nothing reached a device or the hub


def test_the_homes_own_services_run_as_before_in_permissive_mode(tmp_path):
    ids, s = home_session(tmp_path)
    lamp = ids["bedroom_lamp"]["state"]
    e = Engine(s, permissive=True, automations=[auto([{"action": "light.turn_on", "target": {"entity_id": lamp}}])])
    assert e.automations[0].actions[0]["kind"] == "call"
