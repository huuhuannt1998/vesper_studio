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
