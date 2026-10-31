"""`vesper lab generate` (home-generator spec §1, §6, plan W9): one home from a number of rooms or a request file, or
a batch of homes and its summary."""
import csv

from tests.lab.test_cli_lab import setup
from vesper import cli


def test_a_number_of_rooms_makes_a_home_with_a_starter_set_of_devices_that_checks(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    home = tmp_path / "gen"
    assert cli.main(["lab", "generate", str(home), "--rooms", "6", "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "6 rooms" in out and "synthetic" in out and "devices" in out
    assert cli.main(["lab", "check", str(home / "home.yaml"), "--catalog", str(cat)]) == 0
    text = (home / "home.yaml").read_text()
    assert "front_door_lock" in text and "living_light" in text
    assert cli.main(["lab", "generate", str(home), "--rooms", "6", "--catalog", str(cat)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_a_batch_writes_every_home_and_a_summary_row_each(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "homes"
    assert cli.main(["lab", "generate", str(out), "--count", "5", "--rooms", "3-8", "--catalog", str(cat)]) == 0
    rows = list(csv.DictReader((out / "summary.csv").open()))
    assert [r["home"] for r in rows] == [f"home-{k:03d}" for k in range(5)]
    assert all(3 <= int(r["rooms"]) <= 8 and float(r["area_m2"]) > 0 and int(r["devices"]) > 0 for r in rows)
    assert len({r["rooms"] + r["area_m2"] for r in rows}) > 1  # different homes
    for r in rows:
        assert cli.main(["lab", "check", str(out / r["home"] / "home.yaml"), "--catalog", str(cat)]) == 0
    assert "5 homes" in capsys.readouterr().out


def test_a_request_file_s_mistake_is_named(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    req = tmp_path / "req.yaml"
    req.write_text("format: vesper-request/1\nrooms: 0\n")
    assert cli.main(["lab", "generate", str(tmp_path / "x"), "--request", str(req), "--catalog", str(cat)]) == 1
    assert "FAILED: rooms: 0 is not 1 to 60" in capsys.readouterr().err


def test_a_range_of_rooms_is_for_a_batch_and_a_bad_number_is_named(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    assert cli.main(["lab", "generate", str(tmp_path / "a"), "--rooms", "3-8", "--catalog", str(cat)]) == 1
    assert "FAILED: rooms: a range (3-8) makes a batch: give --count" in capsys.readouterr().err
    assert cli.main(["lab", "generate", str(tmp_path / "b"), "--rooms", "many", "--catalog", str(cat)]) == 1
    assert "FAILED: rooms: 'many' is not a number or a range such as 3-8" in capsys.readouterr().err


def test_a_batch_of_no_homes_is_refused_before_anything_is_written(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "none"
    assert cli.main(["lab", "generate", str(out), "--count", "0", "--rooms", "3-5", "--catalog", str(cat)]) == 1
    assert "count: 0 is not a number of homes" in capsys.readouterr().err and not out.exists()


def test_a_batch_whose_room_range_goes_past_sixty_is_refused_before_anything_is_written(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    out = tmp_path / "big"
    assert cli.main(["lab", "generate", str(out), "--count", "2", "--rooms", "58-100", "--catalog", str(cat)]) == 1
    assert "rooms: 58-100 goes past 60" in capsys.readouterr().err and not out.exists()
