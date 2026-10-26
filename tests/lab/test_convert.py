"""Routines from the earlier work's schedules (spec §5a): the 90 LLM persona schedules and the ARAS day plans."""
import json

import pytest
import yaml

from tests.lab.homes import catalog, write_home
from tests.lab.maps import house
from tests.twins.conftest import write_catalog
from vesper import cli
from vesper.lab.convert import ConvertError, from_aras, from_persona, minutes, room_map, routine_yaml
from vesper.lab.routine import load_routine

H = 3600


def persona(tasks, wfh=True):
    return {"model": "gemma-2-9b-it", "persona": 7, "date": "2026-10-14", "works_from_home": wfh,
            "tasks": [dict(zip(("name", "category", "room", "start_s", "duration_s"), t)) for t in tasks]}


def test_canonical_rooms_find_the_home_s_rooms_and_say_where_they_fell_back():
    rooms, notes = room_map(house())
    assert rooms["kitchen"] == "kitchen" and rooms["bedroom"] == "bedroom" and rooms["hallway"] == "hall"
    assert rooms["living_room"] == "hall" and rooms["entryway"] == "hall"  # the test house has neither
    assert any("living_room" in n and "hall" in n for n in notes)
    rooms, _ = room_map(house(), {"living_room": "bedroom"})
    assert rooms["living_room"] == "bedroom"
    with pytest.raises(ConvertError, match="--room living_room=attic: unknown room 'attic'"):
        room_map(house(), {"living_room": "attic"})


def test_a_persona_day_becomes_a_routine_with_its_trips_and_commute_away():
    day = persona([("Wake Up", "SLEEP", "bedroom", 7 * H, 900),
                   ("Prepare Breakfast", "HOUSEHOLD", "kitchen", 7 * H + 900, 900),
                   ("Leave for work", "WORK", "entryway", 8 * H, 600),
                   ("Work at the office", "WORK", "living_room", 8 * H + 600, 8 * H),
                   ("Commute home", "WORK", "entryway", 17 * H, 1800),
                   ("Grocery shopping", "HOUSEHOLD", "kitchen", 18 * H, 3600),
                   ("Dinner", "EATING", "kitchen", 19 * H, 1800),
                   ("Watch TV", "LEISURE", "living_room", 20 * H, 7200),
                   ("Sleep", "SLEEP", "bedroom", 22 * H + 1800, 8 * H)], wfh=False)
    tasks, notes = from_persona(day, house())
    shape = [(t.name, t.category, t.room) for t in tasks]
    assert shape == [("Wake Up", "SLEEP", "bedroom"), ("Prepare Breakfast", "HOUSEHOLD", "kitchen"),
                     ("away: Leave for work .. Commute home", "AWAY", None),
                     ("away: Grocery shopping", "AWAY", None), ("Dinner", "EATING", "kitchen"),
                     ("Watch TV", "LEISURE", "hall"), ("Sleep", "SLEEP", "bedroom")]
    away = tasks[2]
    assert (away.start, away.end) == (8 * H, 17 * H + 1800)


def test_work_at_home_stays_home_and_work_away_without_a_commute_is_an_away_task():
    tasks, _ = from_persona(persona([("Work", "WORK", "living_room", 9 * H, 8 * H)], wfh=True), house())
    assert (tasks[0].category, tasks[0].room) == ("WORK", "hall")
    tasks, _ = from_persona(persona([("Work", "WORK", "living_room", 9 * H, 8 * H)], wfh=False), house())
    assert (tasks[0].category, tasks[0].room, tasks[0].name) == ("AWAY", None, "away: Work")


def test_tasks_run_in_start_order_an_overlap_is_clipped_and_a_task_under_a_minute_is_dropped():
    tasks, notes = from_persona(persona([("Breakfast", "EATING", "kitchen", 7 * H, 3600),
                                         ("Shower", "HYGIENE", "bathroom", 7 * H + 1800, 900),
                                         ("Glass of water", "EATING", "kitchen", 9 * H, 30)]), house())
    assert [(t.name, t.start, t.end) for t in tasks] == [("Breakfast", 7 * H, 7 * H + 1800),
                                                          ("Shower", 7 * H + 1800, 7 * H + 2700)]
    assert any("Breakfast" in n and "clipped" in n for n in notes)
    assert any("Glass of water" in n and "under a minute" in n for n in notes)


def test_a_converted_routine_loads_and_names_its_source(tmp_path):
    day = persona([("Wake Up", "SLEEP", "bedroom", 7 * H, 900), ("Dinner", "EATING", "kitchen", 19 * H, 1800)])
    tasks, notes = from_persona(day, house())
    path = tmp_path / "persona7.yaml"
    path.write_text(routine_yaml(tasks, source="persona gemma-2-9b-it p07 2026-10-14", notes=notes))
    assert path.read_text().startswith("# from: persona gemma-2-9b-it p07 2026-10-14")
    r = load_routine(path, house())
    assert [t.name for t in r.tasks] == ["Wake Up", "Dinner"] and r.tasks[1].room == "kitchen"


def aras_rows(runs_r1, runs_r2):
    """One ARAS day: 20 sensor columns, then each resident's activity id per second."""
    def expand(runs):
        out = []
        for act, seconds in runs:
            out += [act] * seconds
        return out + [out[-1]] * (86400 - len(out))
    a, b = expand(runs_r1), expand(runs_r2)
    return [[0] * 20 + [x, y] for x, y in zip(a, b)]


def test_an_aras_day_becomes_one_routine_per_resident_and_short_runs_merge():
    rows = aras_rows([(11, 7 * H), (21, 300), (4, 30), (3, 1500), (2, 9 * H), (12, 2 * H)],
                     [(11, 8 * H), (14, 900), (17, 4 * H)])
    r1, notes1 = from_aras(rows, 1, house())
    assert [(t.name, t.category, t.room) for t in r1[:5]] == [
        ("Sleeping", "SLEEP", "bedroom"), ("Brushing Teeth", "HYGIENE", "hall"),
        ("Preparing Breakfast", "HOUSEHOLD", "kitchen"), ("Going Out", "AWAY", None),
        ("Watching TV", "LEISURE", "hall")]
    assert any("Having Breakfast" in n and "30 s" in n for n in notes1)  # merged into its neighbour
    r2, _ = from_aras(rows, 2, house())
    assert [t.name for t in r2] == ["Sleeping", "Having Shower", "Using Internet"]


def test_sleep_across_midnight_is_one_task():
    rows = aras_rows([(11, 6 * H), (4, 1800), (12, 15 * H), (11, 2 * H + 1800)], [(11, 86400)])
    tasks, _ = from_aras(rows, 1, house())
    assert tasks[0].name == "Having Breakfast" and tasks[-1].name == "Sleeping"
    assert (tasks[-1].start, tasks[-1].duration) == (21 * H + 1800, 2 * H + 1800 + 6 * H)


def test_the_cli_writes_a_routine_from_either_source(tmp_path, capsys):
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    home = write_home(tmp_path)
    src = tmp_path / "gemma-2-9b-it_p07_2026-10-14.json"
    src.write_text(json.dumps(persona([("Dinner", "EATING", "kitchen", 19 * H, 1800)])))
    out = tmp_path / "routines" / "p07.yaml"
    assert cli.main(["lab", "routine", "persona", str(src), "--home", str(home), "--catalog", str(cat),
                     "--out", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["tasks"][0]["room"] == "kitchen"
    assert "living_room -> hall" in capsys.readouterr().out
    day = tmp_path / "DAY_1.txt"
    day.write_text("\n".join(" ".join(str(v) for v in r) for r in aras_rows([(11, 86400)], [(12, 86400)])))
    assert cli.main(["lab", "routine", "aras", str(day), "--resident", "2", "--home", str(home), "--catalog",
                     str(cat), "--out", str(tmp_path / "routines" / "r2.yaml")]) == 0
    assert yaml.safe_load((tmp_path / "routines" / "r2.yaml").read_text())["tasks"][0]["name"] == "Watching TV"
    assert cli.main(["lab", "routine", "aras", str(day), "--resident", "3", "--home", str(home), "--catalog",
                     str(cat)]) == 1
    assert "--resident: 3 is not 1 or 2" in capsys.readouterr().err


def test_back_to_back_tasks_of_odd_seconds_still_load_as_a_routine(tmp_path):
    rows = aras_rows([(11, 3378), (21, 1234), (4, 1999), (12, 86400 - 3378 - 1234 - 1999)], [(11, 86400)])
    tasks, notes = from_aras(rows, 1, house())
    path = tmp_path / "odd.yaml"
    path.write_text(routine_yaml(tasks, source="test", notes=notes))
    r = load_routine(path, house())
    assert [t.name for t in r.tasks] == ["Sleeping", "Brushing Teeth", "Having Breakfast", "Watching TV"]
    assert all(abs(a.duration - b.duration) < 0.01 for a, b in zip(r.tasks, tasks))


def test_a_routine_that_would_not_load_is_never_left_behind(tmp_path, capsys, monkeypatch):
    import vesper.lab.convert as convert
    cat = write_catalog(tmp_path / "vds", list(catalog().values()))
    home = write_home(tmp_path)
    src = tmp_path / "p.json"
    src.write_text(json.dumps(persona([("Dinner", "EATING", "kitchen", 19 * H, 1800)])))
    monkeypatch.setattr(convert, "routine_yaml", lambda *a, **k: "version: 2\ntasks: []\n")  # a converter defect
    out = tmp_path / "routines" / "p.yaml"
    args = ["lab", "routine", "persona", str(src), "--home", str(home), "--catalog", str(cat), "--out", str(out)]
    assert cli.main(args) == 1
    assert "does not load" in capsys.readouterr().err
    assert not out.exists() and not list((tmp_path / "routines").glob("*"))


def test_a_duration_in_minutes_never_reads_back_longer_than_the_task():
    """floor(d / 60, 4 decimals) * 60 can exceed d in floating point (249 s gave 249.00000000000003 s, and the
    next task then started before this one ended): every duration of up to two days reads back at most d."""
    for d in range(1, 2 * 86400):
        m = minutes(d)
        assert float(repr(m)) * 60 <= d and d - float(repr(m)) * 60 < 0.01, d


def test_an_aras_day_that_begins_with_a_249_second_task_loads(tmp_path):
    rows = aras_rows([(1, 249), (11, 6 * H), (12, 86400 - 249 - 6 * H)], [(11, 86400)])
    tasks, notes = from_aras(rows, 1, house())
    path = tmp_path / "r1.yaml"
    path.write_text(routine_yaml(tasks, source="test", notes=notes))
    assert [t.name for t in load_routine(path, house()).tasks][:2] == ["Other", "Sleeping"]
