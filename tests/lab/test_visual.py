import json

import pytest

from vesper.lab.browser import BrowserError
from vesper.lab.png import Image, decode, encode
from vesper.lab.visual import CONNECTED, READY, VIEW3D_COLOURS, VIEW3D_READY, LookError, load_steps, run_look


def picture(seed=0, size=(40, 30)):
    w, h = size
    return Image(w, h, bytes(c for k in range(w * h) for c in ((k * 7 + seed) % 256, (k * 3) % 256, 90, 255)))


class FakeBrowser:
    """Answers what the page would: `values` maps an expression to its value (or a callable)."""

    def __init__(self, values=None, shots=None, problems=()):
        self.values = {READY: True, CONNECTED: True, "vesper.canvasColors()": 40, **(values or {})}
        self.shots = list(shots or [picture()])
        self.problems, self.calls = list(problems), []

    def goto(self, url, ready=None, timeout=None):
        self.calls.append(("goto", url, ready))

    def evaluate(self, expression):
        self.calls.append(("evaluate", expression))
        v = self.values.get(expression)
        return v() if callable(v) else v

    def wait_for(self, expression, timeout=None, interval=0.05):
        self.calls.append(("wait_for", expression, timeout))
        v = self.evaluate(expression)
        if not v:
            raise BrowserError(f"{expression} was not true within {timeout:g} s (last value: {json.dumps(v)})")
        return v

    def press(self, key, times=1):
        self.calls.append(("press", key, times))

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def pump(self, seconds=0.05):
        pass

    def screenshot(self):
        img = self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]
        return encode(img)


def test_the_steps_run_in_order_and_every_shot_is_saved(tmp_path):
    steps = [{"press": "d", "times": 3}, {"click_world": [5.0, 5.0]}, {"until": "vesper.lamp", "timeout": 2},
             {"expect": "vesper.hint.includes('lamp')", "label": "the hint names the lamp"}, {"shot": "lamp_on"}]
    b = FakeBrowser({"vesper.toScreen(5.0, 5.0)": [412.5, 300.0], "vesper.lamp": True,
                     "vesper.hint.includes('lamp')": True})
    report = run_look(b, "http://127.0.0.1:9/", steps, tmp_path / "out")
    assert report.ok, report.checks
    assert ("goto", "http://127.0.0.1:9/", READY) == b.calls[0]
    acts = [c for c in b.calls if c[0] in ("press", "click", "wait_for")]
    assert acts[1:] == [("press", "d", 3), ("click", 412.5, 300.0), ("wait_for", "vesper.lamp", 2)]
    assert decode((tmp_path / "out" / "lamp_on.png").read_bytes()).width == 40
    names = [c.name for c in report.checks]
    assert names == ["the page is ready", "the map is drawn", "vesper.lamp", "the hint names the lamp",
                     "lamp_on is not blank", "the page stayed connected", "no page errors"]
    saved = json.loads((tmp_path / "out" / "report.json").read_text())
    assert saved["ok"] is True and saved["shots"] == ["lamp_on.png"]
    assert "lamp_on.png" in (tmp_path / "out" / "report.md").read_text()


def test_a_blank_shot_fails(tmp_path):
    blank = Image(20, 20, bytes([255, 255, 255, 255]) * 400)
    report = run_look(FakeBrowser(shots=[blank]), "http://x/", [{"shot": "start"}], tmp_path)
    check = next(c for c in report.checks if c.name == "start is not blank")
    assert not check.ok and not report.ok and "1 colour" in check.detail


def test_shots_are_compared_with_their_references(tmp_path):
    ref = tmp_path / "golden"
    first = run_look(FakeBrowser(shots=[picture(0)]), "http://x/", [{"shot": "start"}], tmp_path / "a",
                     golden=ref, update_golden=True)
    assert first.ok and (ref / "start.png").is_file()
    same = run_look(FakeBrowser(shots=[picture(0)]), "http://x/", [{"shot": "start"}], tmp_path / "b", golden=ref)
    assert same.ok
    moved = run_look(FakeBrowser(shots=[picture(100)]), "http://x/", [{"shot": "start"}], tmp_path / "c", golden=ref)
    check = next(c for c in moved.checks if c.name == "start matches its reference")
    assert not check.ok and "% of pixels differ" in check.detail
    assert (tmp_path / "c" / "start.diff.png").is_file()
    missing = run_look(FakeBrowser(), "http://x/", [{"shot": "other"}], tmp_path / "d", golden=ref)
    assert "--update-golden" in next(c for c in missing.checks if c.name == "other matches its reference").detail


def test_a_wait_that_never_ends_fails_and_skips_the_rest(tmp_path):
    steps = [{"until": "vesper.lamp", "timeout": 0.1}, {"press": "e"}, {"shot": "after"}]
    b = FakeBrowser({"vesper.lamp": False})
    report = run_look(b, "http://x/", steps, tmp_path)
    assert not report.ok
    failed = next(c for c in report.checks if c.name == "vesper.lamp")
    assert not failed.ok and "not true within 0.1 s" in failed.detail
    assert next(c for c in report.checks if c.name == "steps 2-3").detail == "skipped after a failed step"
    assert not any(c[0] == "press" for c in b.calls)


def test_errors_in_the_page_fail_the_look(tmp_path):
    report = run_look(FakeBrowser(problems=["exception: TypeError: x"]), "http://x/", [], tmp_path)
    check = next(c for c in report.checks if c.name == "no page errors")
    assert not check.ok and "TypeError" in check.detail


def test_a_page_that_never_gets_ready_says_so(tmp_path):
    report = run_look(FakeBrowser({READY: False}), "http://x/", [{"shot": "start"}], tmp_path)
    assert not report.ok and report.checks[0].name == "the page is ready" and not report.checks[0].ok


@pytest.mark.parametrize("text, words", [
    ("- {fly: 1}", "step 1: unknown action 'fly'"),
    ("- {shot: a}\n- {press: e, shot: b}", "step 2: one action per step"),
    ("- {click_world: [1]}", "step 1: click_world needs [x, z]"),
    ("- {walk: [1, true]}", "step 1: walk needs [x, z]"),
    ("- {press: e, times: 0}", "step 1: times"),
    ("- {shot: '../escape'}", "step 1: a shot's name"),
    ("- {aim: [1, 2]}", "step 1: aim needs the name of a device or door"),
    ("- {view: top}", "step 1: view is map or 3d"),
    ("shot: a", "a list of steps"),
])
def test_a_bad_steps_file_names_the_step(tmp_path, text, words):
    f = tmp_path / "look.yaml"
    f.write_text(text)
    with pytest.raises(LookError, match=words.replace("[", r"\[")):
        load_steps(f)


def test_a_page_that_lost_its_session_fails_the_look(tmp_path):
    report = run_look(FakeBrowser({CONNECTED: False}), "http://x/", [{"shot": "start"}], tmp_path)
    check = next(c for c in report.checks if c.name == "the page stayed connected")
    assert not check.ok and not report.ok


def test_errors_inside_the_session_fail_the_look(tmp_path):
    ok = run_look(FakeBrowser(), "http://x/", [], tmp_path / "a", session_errors=lambda: [])
    assert ok.ok and any(c.name == "the session raised no errors" for c in ok.checks)
    bad = run_look(FakeBrowser(), "http://x/", [], tmp_path / "b", session_errors=lambda: ["RuntimeError: boom"])
    check = next(c for c in bad.checks if c.name == "the session raised no errors")
    assert not check.ok and "boom" in check.detail and not bad.ok


def test_a_scripted_walk_moves_the_player_where_play_itself_has_no_click_to_walk(tmp_path):
    """The page walks by WASD only (spec §2); a look walks the player along the planned path with `walk`."""
    b = FakeBrowser({"vesper.state.actors.player.room === 'hall'": True})
    steps = [{"walk": [1.5, 4.5]}, {"until": "vesper.state.actors.player.room === 'hall'", "timeout": 5}]
    report = run_look(b, "http://x/", steps, tmp_path)
    assert report.ok, report.checks
    assert ("evaluate", "vesper.walkTo(1.5, 4.5)") in b.calls and not [c for c in b.calls if c[0] == "click"]


def test_aim_turns_the_view_onto_a_device_and_fails_for_one_the_view_does_not_have(tmp_path):
    b = FakeBrowser({'vesper.aim("kitchen_plug")': {"yaw": -30, "pitch": -12, "mode": "third"},
                     'vesper.aim("nowhere")': None})
    report = run_look(b, "http://x/", [{"aim": "kitchen_plug"}, {"aim": "nowhere"}, {"shot": "never"}], tmp_path)
    assert ("evaluate", 'vesper.aim("kitchen_plug")') in b.calls
    failed = [c for c in report.checks if not c.ok]
    assert [c.name for c in failed] == ["aim at nowhere", "step 3"] and "no device or door" in failed[0].detail
    assert report.shots == [] and failed[1].detail == "skipped after a failed step"


def test_view_switches_to_3d_waits_for_it_and_checks_it_is_drawn(tmp_path):
    b = FakeBrowser({VIEW3D_READY: True, VIEW3D_COLOURS: 30})
    report = run_look(b, "http://x/", [{"view": "3d"}, {"view": "map"}], tmp_path)
    assert ("evaluate", 'vesper.setView("3d")') in b.calls and ("evaluate", 'vesper.setView("map")') in b.calls
    assert ("wait_for", VIEW3D_READY, 90) in b.calls
    names = [c.name for c in report.checks if c.ok]
    assert "the 3D view is ready" in names and "the 3D view is drawn" in names and report.ok


def test_a_page_that_starts_in_3d_must_draw_it(tmp_path):
    blank = run_look(FakeBrowser({VIEW3D_COLOURS: 1}), "http://x/", [], tmp_path / "a")
    assert [c.name for c in blank.checks if not c.ok] == ["the 3D view is drawn"]
    on_map = run_look(FakeBrowser({VIEW3D_COLOURS: None}), "http://x/", [], tmp_path / "b")
    assert on_map.ok and "the 3D view is drawn" not in [c.name for c in on_map.checks]
