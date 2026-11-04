"""Reading a floor-plan image with Claude (floor-plan import, task A3). Every test uses a fake runner: no network,
no real Claude. The runner takes the `claude` argument list and returns what the CLI printed."""
import json
import subprocess

import pytest

from tests.lab.test_cli_lab import setup
from tests.lab.test_import import three_rooms
from vesper import cli
from vesper.lab import plan_read
from vesper.lab.plan import PlanError
from vesper.lab.plan_read import PROMPT, read_plan_image


def px_plan(**kw) -> dict:
    """What a good answer looks like: the three-room plan, drawn at 50 px to the metre, scale read from the image."""
    plan = three_rooms()
    plan["units"] = "px"
    for r in plan["rooms"]:
        r["polygon"] = [[x * 50, y * 50] for x, y in r["polygon"]]
    for d in plan["doors"]:
        d["at"] = [d["at"][0] * 50, d["at"][1] * 50]
        d["width"] *= 50
    for w in plan["windows"]:
        w["at"] = [w["at"][0] * 50, w["at"][1] * 50]
        w["width"] *= 50
    plan["scale"] = {"m_per_px": 0.02}
    plan.update(kw)
    return plan


def envelope(text: str, *, is_error=False) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text})


class Fake:
    """A runner that answers from a list, remembering every argument list it was given."""

    def __init__(self, *answers: str):
        self.answers, self.calls = list(answers), []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        return self.answers.pop(0)


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "plan.png"
    path.write_bytes(b"\x89PNG\r\n")
    return path


def test_a_valid_answer_is_the_plan(image):
    fake = Fake(envelope(json.dumps(px_plan())))
    assert read_plan_image(image, runner=fake) == px_plan()
    assert len(fake.calls) == 1


def test_the_arguments_carry_the_image_path_the_json_format_and_only_the_read_tool(image):
    fake = Fake(envelope(json.dumps(px_plan())))
    read_plan_image(image, runner=fake)
    args = fake.calls[0]
    assert args[:2] == ["claude", "-p"] and str(image.resolve()) in args[2]
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--allowedTools") + 1] == f"Read(/{image.resolve()})"  # that file alone, not the disk
    assert args[args.index("--tools") + 1] == "Read"
    assert "--dangerously-skip-permissions" not in args


def test_no_model_flag_by_default_and_one_when_chosen(image):
    fake = Fake(envelope(json.dumps(px_plan())), envelope(json.dumps(px_plan())))
    read_plan_image(image, runner=fake)
    read_plan_image(image, runner=fake, model="sonnet")
    assert "--model" not in fake.calls[0]  # the user's own Claude Code default model
    assert fake.calls[1][fake.calls[1].index("--model") + 1] == "sonnet"


def test_a_retry_keeps_the_model(image):
    fake = Fake(envelope("no plan here"), envelope(json.dumps(px_plan())))
    read_plan_image(image, runner=fake, model="haiku")
    assert [c[c.index("--model") + 1] for c in fake.calls] == ["haiku", "haiku"]


def test_the_prompt_places_a_missing_entrance_at_the_likeliest_front_door():
    flat = " ".join(PROMPT.split())
    assert "If no outside door is drawn, do not refuse" in flat and "most plausible front-door position" in flat


def test_the_prompt_names_the_schema_the_types_and_the_things_to_read():
    for needle in ('"vesper-plan/1"', '"units": "px"', "m_per_px", "length_m", "area_m2", "polygon", "entrance",
                   "outside", "living", "kitchen", "laundry", "corridor", "hall", "other", "windows", "doors",
                   "y grows downward", "ONLY the JSON", '"error"'):
        assert needle in PROMPT, needle


def test_a_json_fence_and_talk_around_the_plan_are_tolerated(image):
    text = "Here is the plan:\n```json\n" + json.dumps(px_plan()) + "\n```\nHope that helps."
    assert read_plan_image(image, runner=Fake(envelope(text))) == px_plan()
    bare = "Sure. " + json.dumps(px_plan()) + " Done."
    assert read_plan_image(image, runner=Fake(envelope(bare))) == px_plan()


def test_an_invalid_first_answer_is_retried_with_the_errors_and_the_second_is_returned(image):
    bad = px_plan()
    bad["rooms"][0]["polygon"] = [[0, 0], [250, 0]]
    fake = Fake(envelope(json.dumps(bad)), envelope(json.dumps(px_plan())))
    assert read_plan_image(image, runner=fake) == px_plan()
    assert len(fake.calls) == 2
    retry = fake.calls[1][2]
    assert "room 'lr': polygon is open" in retry and PROMPT in retry and json.dumps(bad) in retry
    assert fake.calls[1][3:] == fake.calls[0][3:]  # the same permissions


def test_two_invalid_answers_raise_with_claudes_last_text(image):
    first = px_plan(units="feet")
    last = px_plan(format="vesper-plan/2")
    fake = Fake(envelope(json.dumps(first)), envelope(json.dumps(last)))
    with pytest.raises(PlanError) as e:
        read_plan_image(image, runner=fake)
    assert len(fake.calls) == 2
    assert "plan.png" in str(e.value) and "vesper-plan/2" in str(e.value) and "is not 'vesper-plan/1'" in str(e.value)


def test_text_that_is_not_json_counts_as_an_invalid_answer(image):
    fake = Fake(envelope("I cannot see an image."), envelope(json.dumps(px_plan())))
    assert read_plan_image(image, runner=fake) == px_plan()
    assert "no JSON" in fake.calls[1][2]


def test_a_picture_that_is_not_a_floor_plan_fails_with_claudes_reason_after_one_retry(image):
    reason = "This is a photograph of a cat, not a floor plan."
    fake = Fake(envelope(json.dumps({"error": reason})), envelope(json.dumps({"error": reason})))
    with pytest.raises(PlanError) as e:
        read_plan_image(image, runner=fake)
    assert len(fake.calls) == 2 and reason in str(e.value)


def test_an_error_answer_followed_by_a_plan_is_accepted(image):
    fake = Fake(envelope('{"error": "too small to read"}'), envelope(json.dumps(px_plan())))
    assert read_plan_image(image, runner=fake) == px_plan()


def test_a_cli_that_reports_an_error_stops_at_once_with_its_message(image):
    fake = Fake(envelope("Not logged in. Please run /login", is_error=True))
    with pytest.raises(PlanError, match="Not logged in"):
        read_plan_image(image, runner=fake)
    assert len(fake.calls) == 1


def test_a_missing_file_is_refused_before_claude_is_run(tmp_path):
    fake = Fake()
    with pytest.raises(PlanError, match="no such file"):
        read_plan_image(tmp_path / "nope.png", runner=fake)
    assert fake.calls == []


def test_the_default_runner_says_so_when_claude_is_not_installed(monkeypatch, image):
    def gone(*a, **k):
        raise FileNotFoundError("claude")
    monkeypatch.setattr(subprocess, "run", gone)
    with pytest.raises(PlanError, match="Claude Code is not installed"):
        read_plan_image(image)


def test_the_default_runner_reports_a_failed_run_and_a_timeout(monkeypatch, image):
    done = subprocess.CompletedProcess(["claude"], 1, stdout="", stderr="Invalid API key")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    with pytest.raises(PlanError, match="Invalid API key"):
        read_plan_image(image)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(PlanError, match="did not answer"):
        read_plan_image(image)


def test_the_default_runner_returns_stdout(monkeypatch, image):
    seen = {}

    def run(args, **k):
        seen.update(args=args, **k)
        return subprocess.CompletedProcess(args, 0, stdout=envelope(json.dumps(px_plan())), stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert read_plan_image(image) == px_plan()
    assert seen["args"][0] == "claude" and seen["capture_output"] and seen["timeout"] > 0
    assert seen["stdin"] == subprocess.DEVNULL


# --- the command ---------------------------------------------------------------------------------------------------

def fake_claude(monkeypatch, *answers):
    fake = Fake(*answers)
    fake.models = []

    def reader(path, runner=None, model=None):
        fake.models.append(model)
        return read_plan_image(path, runner=fake, model=model)
    monkeypatch.setattr(plan_read, "read_plan_image", reader)
    return fake


def test_an_image_goes_through_the_reader_and_builds_a_home(tmp_path, monkeypatch, capsys, image):
    cat, _ = setup(tmp_path)
    fake = fake_claude(monkeypatch, envelope(json.dumps(px_plan())))
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(image), str(out), "--catalog", str(cat)]) == 0
    assert len(fake.calls) == 1 and (out / "home.yaml").exists()
    assert "3 rooms" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["plan.PNG", "plan.jpg", "plan.jpeg", "plan.pdf"])
def test_every_image_suffix_goes_to_the_reader(tmp_path, monkeypatch, name):
    cat, _ = setup(tmp_path)
    src = tmp_path / name
    src.write_bytes(b"x")
    fake = fake_claude(monkeypatch, envelope(json.dumps(px_plan())))
    assert cli.main(["lab", "import", str(src), str(tmp_path / "mine"), "--catalog", str(cat)]) == 0
    assert len(fake.calls) == 1


def test_the_scale_flags_work_on_a_read_plan(tmp_path, monkeypatch, image):
    cat, _ = setup(tmp_path)
    unscaled = px_plan(scale=None)
    fake_claude(monkeypatch, envelope(json.dumps(unscaled)))
    assert cli.main(["lab", "import", str(image), str(tmp_path / "a"), "--area", "58.5", "--catalog", str(cat)]) == 0
    fake_claude(monkeypatch, envelope(json.dumps(unscaled)))
    assert cli.main(["lab", "import", str(image), str(tmp_path / "b"), "--scale", "kitchen width 3.5 m",
                     "--catalog", str(cat)]) == 0


def test_a_read_plan_with_no_scale_and_none_given_is_refused_and_writes_nothing(tmp_path, monkeypatch, capsys, image):
    cat, _ = setup(tmp_path)
    fake_claude(monkeypatch, envelope(json.dumps(px_plan(scale=None))))
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(image), str(out), "--catalog", str(cat)]) == 1
    assert "no scale: give --scale or --area" in capsys.readouterr().err and not out.exists()


def test_a_picture_claude_cannot_read_writes_no_home(tmp_path, monkeypatch, capsys, image):
    cat, _ = setup(tmp_path)
    reason = "this is a photograph of a cat"
    fake_claude(monkeypatch, envelope(json.dumps({"error": reason})), envelope(json.dumps({"error": reason})))
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(image), str(out), "--catalog", str(cat)]) == 1
    assert reason in capsys.readouterr().err and not out.exists()


def test_a_json_input_never_runs_claude(tmp_path, monkeypatch):
    cat, _ = setup(tmp_path)

    def boom(*a, **k):
        raise AssertionError("Claude was run for a .json plan")
    monkeypatch.setattr(plan_read, "read_plan_image", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    src = tmp_path / "three.json"
    src.write_text(json.dumps(three_rooms()))
    assert cli.main(["lab", "import", str(src), str(tmp_path / "mine"), "--catalog", str(cat)]) == 0


def test_an_existing_directory_stops_an_image_import_before_claude_is_run(tmp_path, monkeypatch, capsys, image):
    cat, _ = setup(tmp_path)
    fake = fake_claude(monkeypatch)
    out = tmp_path / "mine"
    out.mkdir()
    assert cli.main(["lab", "import", str(image), str(out), "--catalog", str(cat)]) == 1
    assert fake.calls == [] and "already exists" in capsys.readouterr().err


def test_the_cli_passes_the_model_to_the_reader(tmp_path, monkeypatch, image):
    cat, _ = setup(tmp_path)
    fake = fake_claude(monkeypatch, envelope(json.dumps(px_plan())), envelope(json.dumps(px_plan())))
    assert cli.main(["lab", "import", str(image), str(tmp_path / "a"), "--catalog", str(cat)]) == 0
    assert cli.main(["lab", "import", str(image), str(tmp_path / "b"), "--model", "sonnet", "--catalog", str(cat)]) == 0
    assert fake.models == [None, "sonnet"]


def test_an_image_import_prints_what_was_read_before_building(tmp_path, monkeypatch, capsys, image):
    cat, _ = setup(tmp_path)
    fake_claude(monkeypatch, envelope(json.dumps(px_plan())))
    assert cli.main(["lab", "import", str(image), str(tmp_path / "mine"), "--catalog", str(cat)]) == 0
    out = capsys.readouterr().out
    assert "Claude read 3 rooms and 3 doors" in out
    assert "Living room (living): 25.0 m²" in out and "Kitchen (kitchen): 10.5 m²" in out
    assert "Bedroom (bedroom): 14.0 m²" in out
    assert "door 1: Living room - Kitchen" in out
    assert "entrance: door 0 between Living room and outside" in out
    assert out.index("entrance: door 0") < out.index("3 rooms (living")  # shown before the home is built


def test_the_summary_is_printed_even_when_the_plan_is_then_refused(tmp_path, monkeypatch, capsys, image):
    cat, _ = setup(tmp_path)
    plan = px_plan()
    plan["rooms"][1]["polygon"] = [[250, 0], [300, 0], [300, 100], [250, 100]]  # a 2 m² kitchen
    fake_claude(monkeypatch, envelope(json.dumps(plan)))
    out = tmp_path / "mine"
    assert cli.main(["lab", "import", str(image), str(out), "--catalog", str(cat)]) == 1
    captured = capsys.readouterr()
    assert "Kitchen (kitchen)" in captured.out and not out.exists()


def test_a_json_plan_prints_no_reading_summary(tmp_path, capsys):
    cat, _ = setup(tmp_path)
    src = tmp_path / "three.json"
    src.write_text(json.dumps(three_rooms()))
    assert cli.main(["lab", "import", str(src), str(tmp_path / "mine"), "--catalog", str(cat)]) == 0
    assert "Claude read" not in capsys.readouterr().out
