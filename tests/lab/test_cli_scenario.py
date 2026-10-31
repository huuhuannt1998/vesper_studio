"""`vesper lab scenario check|run|report` (security-lab spec §4.5, plan W4a1)."""
import json

from tests.lab.test_cli_lab import setup
from tests.lab.test_scenario_run import scenario
from vesper import cli


def files(tmp_path):
    cat, _ = setup(tmp_path / "c")
    s = scenario(tmp_path)
    return cat, s.path


def test_check_summarises_a_scenario_and_names_a_mistake(tmp_path, capsys):
    cat, path = files(tmp_path)
    assert cli.main(["lab", "scenario", "check", str(path), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "Case 3, the lock-on-close action delayed" in out and "3 claims" in out and "1 attack step" in out
    path.write_text(path.read_text().replace("link: coordinator", "link: coordinatr"))
    assert cli.main(["lab", "scenario", "check", str(path), "--catalog", str(cat)]) == 1
    assert "FAILED: attack[0].link: unknown link 'coordinatr'" in capsys.readouterr().err


def test_run_writes_each_run_s_trace_the_report_and_a_summary(tmp_path, capsys):
    cat, path = files(tmp_path)
    out_dir = tmp_path / "runs"
    assert cli.main(["lab", "scenario", "run", str(path), "--catalog", str(cat), "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "reproduced: the intruder opens the door before it is locked" in out and "3 of 3 claims reproduced" in out
    assert (out_dir / "report.md").is_file() and len(list(out_dir.glob("*.jsonl"))) == 6
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["scenario"] == str(path) and len(summary["runs"]) == 6


def test_report_checks_the_claims_again_on_the_saved_traces_without_running(tmp_path, capsys):
    cat, path = files(tmp_path)
    out_dir = tmp_path / "runs"
    cli.main(["lab", "scenario", "run", str(path), "--catalog", str(cat), "--out", str(out_dir)])
    capsys.readouterr()
    text = path.read_text()
    old = "conditions: {baseline: false, attack: true, attack+defense: true}\n    cites"
    assert old in text  # the first claim's expectations, as the file has them
    path.write_text(text.replace(old, "conditions: {baseline: true, attack: true, attack+defense: true}\n    cites"))
    assert cli.main(["lab", "scenario", "report", str(path), "--catalog", str(cat), "--out", str(out_dir)]) == 0
    assert "2 of 3 claims reproduced" in capsys.readouterr().out
    assert "**not reproduced**" in (out_dir / "report.md").read_text()
