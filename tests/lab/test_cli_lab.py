"""`vesper lab`: the headless Lab commands, on the logical clock; nothing is started."""
import json
import textwrap

import yaml

from tests.lab.homes import HOME, catalog, write_home
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli

SCRIPT = """\
    def run(lab):
        lab.walk_to((0.4, 3.0))
        lab.wait(20)
        lab.interact()
        lab.wait(60)
"""


def setup(tmp_path):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    return cat, write_home(home_dir)


def test_check_validates_a_home_and_summarises_it(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "check", str(home), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "6 devices" in out and "2 doors" in out and "4 rooms" in out and "test/house" in out


def test_check_names_the_mistake(tmp_path, capsys):
    cat, home = setup(tmp_path)
    home.write_text(textwrap.dedent(HOME).replace("{room: hall, mount: ceiling}", "{room: hal, mount: ceiling}"))
    assert cli.main(["lab", "check", str(home), "--catalog", str(cat)]) == 1
    assert "FAILED: devices[2].at.room: unknown room 'hal'" in capsys.readouterr().err


def test_run_executes_a_script_and_writes_its_trace(tmp_path, capsys):
    cat, home = setup(tmp_path)
    script = tmp_path / "walk.py"
    script.write_text(textwrap.dedent(SCRIPT))
    trace = tmp_path / "t.jsonl"
    assert cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat),
                     "--trace", str(trace)]) == 0
    lines = [json.loads(x) for x in trace.read_text().splitlines()]
    assert lines[0]["kind"] == "meta" and any(r.get("result") == "opened" for r in lines if r["kind"] == "rule")
    assert "reports" in capsys.readouterr().out


def test_run_without_a_run_function_is_refused(tmp_path, capsys):
    cat, home = setup(tmp_path)
    script = tmp_path / "empty.py"
    script.write_text("x = 1\n")
    assert cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat)]) == 1
    assert "defines no run(lab)" in capsys.readouterr().err


def test_replay_confirms_a_trace_and_reports_a_tampered_one(tmp_path, capsys):
    cat, home = setup(tmp_path)
    script = tmp_path / "walk.py"
    script.write_text(textwrap.dedent(SCRIPT))
    trace = tmp_path / "t.jsonl"
    cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat), "--trace", str(trace)])
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 0
    assert "identical" in capsys.readouterr().out
    lines = trace.read_text().splitlines()
    k = next(i for i, x in enumerate(lines) if json.loads(x)["kind"] == "report")
    rec = json.loads(lines[k])
    rec["observed_t"] += 1.0
    lines[k] = json.dumps(rec)
    trace.write_text("\n".join(lines) + "\n")
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 1
    assert "report 0" in capsys.readouterr().out


def test_new_creates_a_home_from_a_map_that_check_accepts(tmp_path, capsys):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    house().save(tmp_path / "scene.map.json")
    assert cli.main(["lab", "new", str(tmp_path / "myhome"), "--map", str(tmp_path / "scene.map.json")]) == 0
    spec = yaml.safe_load((tmp_path / "myhome" / "home.yaml").read_text())
    assert spec["version"] == 2 and spec["scene"] == "test/house" and spec["avatar"]["start"]["room"] == "hall"
    assert (tmp_path / "myhome" / "map.json").exists()
    assert cli.main(["lab", "check", str(tmp_path / "myhome" / "home.yaml"), "--catalog", str(cat)]) == 0
    assert cli.main(["lab", "new", str(tmp_path / "myhome"), "--map", str(tmp_path / "scene.map.json")]) == 1
    assert "already exists" in capsys.readouterr().err


def test_replay_explains_a_missing_or_empty_trace(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "replay", str(tmp_path / "none.jsonl"), "--home", str(home), "--catalog", str(cat)]) == 1
    assert "FAILED: cannot read" in capsys.readouterr().err
    (tmp_path / "empty.jsonl").write_text("")
    assert cli.main(["lab", "replay", str(tmp_path / "empty.jsonl"), "--catalog", str(cat)]) == 1
    assert "not a VESPER trace" in capsys.readouterr().err


def test_two_runs_in_the_same_second_keep_both_traces(tmp_path, monkeypatch, capsys):
    from datetime import UTC, datetime

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(cli, "datetime", Frozen)
    cat, home = setup(tmp_path)
    script = tmp_path / "walk.py"
    script.write_text(textwrap.dedent(SCRIPT))
    for _ in range(2):
        assert cli.main(["lab", "run", str(script), "--home", str(home), "--catalog", str(cat)]) == 0
    assert len(list((home.parent / "traces").glob("*.jsonl"))) == 2


DAY = """\
version: 1
tasks:
  - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
  - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
  - {at: "08:00", minutes: 240, name: work, category: AWAY}
  - {at: "21:00", minutes: 60, name: read, category: LEISURE, room: bedroom}
  - {at: "22:30", minutes: 480, name: sleep, category: SLEEP, room: bedroom}
"""


def with_alex(tmp_path):
    cat, home = setup(tmp_path)
    (home.parent / "routines").mkdir()
    (home.parent / "routines" / "alex.yaml").write_text(DAY)
    home.write_text(home.read_text() + "residents:\n  - {name: alex, routine: routines/alex.yaml}\n")
    return cat, home


def test_a_day_lives_the_residents_and_replays_exactly(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
    trace = tmp_path / "day.jsonl"
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--trace", str(trace)]) == 0
    out = capsys.readouterr().out
    assert "alex: 5 tasks" in out and "a dark room" in out and "leaving home" in out
    records = [json.loads(x) for x in trace.read_text().splitlines()]
    assert records[0]["clock"] == "logical" and records[0]["start_time"] == 0
    assert max(r["t"] for r in records) <= 24 * 3600
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 0
    assert "identical" in capsys.readouterr().out


def test_a_day_can_start_later_and_stop_in_the_middle_of_a_walk_and_still_replay_exactly(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
    trace = tmp_path / "morning.jsonl"
    hours = 1.5 + 2 / 3600  # 07:30:02: alex is on the way from the bedroom to breakfast
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--trace", str(trace), "--from", "06:00",
                     "--hours", str(hours)]) == 0
    records = [json.loads(x) for x in trace.read_text().splitlines()]
    assert records[0]["start_time"] == 6 * 3600 and max(r["t"] for r in records) <= hours * 3600
    walks = [r for r in records if r["kind"] == "input" and r["op"] == "walk_to"]
    assert walks and walks[-1]["why"]["task"] == "breakfast"
    capsys.readouterr()
    assert cli.main(["lab", "replay", str(trace), "--home", str(home), "--catalog", str(cat)]) == 0
    assert "identical" in capsys.readouterr().out


def test_a_day_says_that_the_home_s_automations_do_not_run_without_home_assistant(tmp_path, capsys):
    cat, home = with_alex(tmp_path)
    (home.parent / "automations.yaml").write_text("[]\n")
    home.write_text(home.read_text() + "automations: automations.yaml\n")
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--hours", "1"]) == 0
    assert "automations.yaml does not run in a day: Home Assistant is not in the loop" in capsys.readouterr().out


def test_a_day_needs_residents_and_a_time_of_day(tmp_path, capsys):
    cat, home = setup(tmp_path)
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat)]) == 1
    assert "has no residents" in capsys.readouterr().err
    cat, home = with_alex(tmp_path / "b")
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--from", "25:00"]) == 1
    assert "--from: '25:00' is not a time of day" in capsys.readouterr().err


def test_a_replay_lives_the_residents_again_and_names_an_action_that_changed(tmp_path, capsys):
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import compare_reports, read_trace, replay
    from vesper.twins.home import load_catalog
    cat, home = with_alex(tmp_path)
    trace = tmp_path / "day.jsonl"
    assert cli.main(["lab", "day", str(home), "--catalog", str(cat), "--trace", str(trace)]) == 0
    records, lab = read_trace(trace), load_lab_home(home, load_catalog(cat))
    again = replay(records, lab)
    mine = [r for r in again if r["kind"] == "input" and r["actor"] == "alex"]
    assert len([r for r in again if r["kind"] == "input" and r["op"] == "spawn" and r["actor"] == "alex"]) == 1
    assert mine == [r for r in records if r["kind"] == "input" and r["actor"] == "alex"]  # lived again, the same
    (home.parent / "routines" / "alex.yaml").write_text(DAY.replace('"21:00", minutes: 60', '"21:10", minutes: 50'))
    diff = compare_reports(records, replay(records, load_lab_home(home, load_catalog(cat))))
    assert diff is not None and "alex" in diff
