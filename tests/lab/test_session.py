import json

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.session import Session, compare_reports, read_trace, replay


def session(tmp_path, text=HOME, trace=True):
    home = load_lab_home(write_home(tmp_path, text), catalog())
    s = Session(home, trace=tmp_path / "trace.jsonl" if trace else None)
    s.start()
    return s


def script(s):
    s.walk_to((0.4, 3.0))
    s.wait(20)
    s.interact()  # the front door opens
    s.wait(5)
    s.walk_to((5.0, 4.4))
    s.wait(30)
    s.interact()  # the bedroom lamp
    s.wait(200)


def test_a_session_runs_the_home_and_traces_inputs_rules_and_reports(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    records = read_trace(tmp_path / "trace.jsonl")
    meta = records[0]
    assert meta["kind"] == "meta" and meta["format"] == "vesper-trace/1" and meta["seed"] == 3
    assert meta["devices"]["front_door"] == "zigbee:aqara:mccgq11lm"
    inputs = [r for r in records if r["kind"] == "input"]
    assert [r["op"] for r in inputs] == ["spawn", "walk_to", "interact", "walk_to", "interact"]
    assert inputs[2]["outcome"]["result"] == "opened"
    reports = [r for r in records if r["kind"] == "report"]
    assert any(r["device"] == "front_door" and r["feature"] == "contact" and r["value"] is False for r in reports)
    assert any(r["device"] == "bedroom_lamp" and r["value"] == "ON" for r in reports)
    assert any(r["kind"] == "rule" and r["rule"] == "presence" for r in records)
    assert all(a["t"] <= b["t"] for a, b in zip(records[1:], records[2:]))


def test_replaying_the_inputs_reproduces_the_same_device_reports(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    original = read_trace(tmp_path / "trace.jsonl")
    home = load_lab_home(tmp_path / "home.yaml", catalog())
    again = replay(original, home)
    assert compare_reports(original, again) is None
    assert len([r for r in again if r["kind"] == "report"]) > 5


def test_a_replay_on_a_changed_home_names_the_first_difference(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    original = read_trace(tmp_path / "trace.jsonl")
    changed = HOME.replace("mount: table}", "mount: table}, state: {state: \"ON\"}")
    other = tmp_path / "other"
    other.mkdir()
    home = load_lab_home(write_home(other, changed), catalog())
    diff = compare_reports(original, replay(original, home))
    assert diff is not None and "bedroom_lamp" in diff


def test_a_session_without_a_trace_still_runs(tmp_path):
    s = session(tmp_path, trace=False)
    script(s)
    s.stop()
    assert s.runtime.state(s.twin_ids["bedroom_lamp"])["state"] == "ON"
    assert not (tmp_path / "trace.jsonl").exists()


def test_the_trace_is_one_json_object_per_line(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    for line in (tmp_path / "trace.jsonl").read_text().splitlines():
        assert isinstance(json.loads(line), dict)


def test_an_interaction_is_traced_as_a_rule_with_its_target_kind(tmp_path):
    s = session(tmp_path)
    script(s)
    s.stop()
    door = [r for r in s.records if r.get("rule") == "interact" and r["target"] == "hall-porch"]
    assert door and door[0]["kind"] == "rule" and door[0]["target_kind"] == "door" and door[0]["result"] == "opened"
