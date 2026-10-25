import textwrap

import pytest

from tests.lab.maps import house
from vesper.lab.routine import CATEGORIES, RoutineError, Task, load_routine

DAY = """\
    version: 1
    tasks:
      - {at: "07:00", minutes: 30, name: wake up, category: SLEEP, room: bedroom}
      - {at: "07:30", minutes: 20, name: breakfast, category: EATING, room: kitchen}
      - {at: "08:00", minutes: 240, name: work, category: AWAY}
      - {at: "12:00:30", minutes: 0.5, name: a glass of water, category: EATING, room: kitchen}
"""


def write(tmp_path, text):
    path = tmp_path / "alex.yaml"
    path.write_text(textwrap.dedent(text))
    return path


def test_a_routine_is_a_day_of_tasks_in_rooms_or_away(tmp_path):
    r = load_routine(write(tmp_path, DAY), house())
    assert r.name == "alex"
    assert r.tasks[0] == Task(start=7 * 3600, duration=1800, name="wake up", category="SLEEP", room="bedroom")
    assert r.tasks[2].room is None and r.tasks[2].category == "AWAY"
    assert (r.tasks[3].start, r.tasks[3].duration) == (12 * 3600 + 30, 30)
    assert "AWAY" in CATEGORIES and "SLEEP" in CATEGORIES


@pytest.mark.parametrize("old, new, message", [
    ("room: kitchen}\n      - {at: \"08:00\"", "room: kitchn}\n      - {at: \"08:00\"",
     r"tasks\[1\]\.room: unknown room 'kitchn'; did you mean kitchen"),
    ("category: EATING, room: kitchen}\n      - {at: \"08:00\"", "category: COOKING, room: kitchen}\n"
     "      - {at: \"08:00\"", r"tasks\[1\]\.category: 'COOKING' is not one of"),
    ("{at: \"07:30\", minutes: 20", "{at: \"07:20\", minutes: 20", r"tasks\[1\]\.at: 07:20 is before the end of "
                                                                    r"tasks\[0\] \(07:30\)"),
    ("{at: \"07:00\"", "{at: \"25:00\"", r"tasks\[0\]\.at: '25:00' is not a time of day"),
    ("minutes: 30", "minutes: 0", r"tasks\[0\]\.minutes: 0 is not a positive number of minutes"),
    ("name: wake up, category: SLEEP, room: bedroom", "name: wake up, category: SLEEP, room: porch",
     r"tasks\[0\]\.room: 'porch' is outdoors; an outdoor task is AWAY"),
    ("version: 1", "version: 2", r"version: a routine file is version 1"),
])
def test_mistakes_in_a_routine_name_the_task_and_the_field(tmp_path, old, new, message):
    text = DAY.replace(old, new, 1)
    assert text != DAY
    with pytest.raises(RoutineError, match=message):
        load_routine(write(tmp_path, text), house())


def test_a_routine_that_is_not_a_list_of_tasks_is_refused(tmp_path):
    with pytest.raises(RoutineError, match="tasks: a routine is a list of tasks"):
        load_routine(write(tmp_path, "version: 1\ntasks: {at: '07:00'}\n"), house())
    with pytest.raises(RoutineError, match="not valid YAML"):
        load_routine(write(tmp_path, "version: 1\ntasks: [\n"), house())
