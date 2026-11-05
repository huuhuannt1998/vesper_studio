"""VESPER Studio's lobby (plans W5, W6): the homes in the user's folders (play one, compose a new one), this
computer's checks, and setup run in the background; on 127.0.0.1 only."""
import json
import time
from pathlib import Path

import pytest

from tests.lab.homes import catalog, write_home
from tests.lab.test_compose import Launched, call
from vesper.setup_tools import Check


class Job:
    def __init__(self, code=0):
        self.code, self.done = code, False

    def poll(self):
        return self.code if self.done else None

    def wait(self, timeout=None):
        while not self.done:
            time.sleep(0.01)
        return self.code

    def terminate(self):
        self.done = True


@pytest.fixture
def lobby(tmp_path, monkeypatch):
    from vesper.lab.studio import StudioServer
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {"validated_products": [],
                                                                                "matter": {"validated_products": []}}})
    homes = tmp_path / "homes"
    (homes / "flat").mkdir(parents=True)
    write_home(homes / "flat")
    (homes / "flat" / "previous" / "old").mkdir(parents=True)
    (homes / "flat" / "previous" / "old" / "home.yaml").write_text("not a home")
    jobs = []

    def run_setup(what, log):
        log.write_text(f"setting up {what}\n")
        jobs.append(Job())
        return jobs[-1]
    launch = Launched()
    s = StudioServer(catalog(), tmp_path / "vds", tmp_path / "results", [homes], root=tmp_path / "stack", port=0,
                     play_port=18998, launch=launch, wait_for_play=lambda port, **kw: True, run_setup=run_setup,
                     doctor=lambda: [Check("habitat", False, "no Python", "HSSD homes", "vesper setup habitat")])
    s.start()
    yield s, launch, jobs, homes
    s.stop()


def test_the_lobby_lists_the_homes_but_not_their_kept_old_versions(lobby):
    s, _, _, _ = lobby
    assert b"VESPER Studio" in call(s, "/")[1] and call(s, "/studio.js")[0] == 200 and call(s, "/compose")[0] == 200
    homes = json.loads(call(s, "/api/homes")[1])["homes"]
    assert [h["name"] for h in homes] == ["homes/flat"] and homes[0]["devices"] == 6


def test_a_listed_home_plays_and_nothing_else_can(lobby):
    s, launch, _, homes = lobby
    status, body = call(s, "/api/open", {"home": "homes/flat"})
    assert status == 200 and launch.started == [(str(homes / "flat" / "home.yaml"), 18998)]
    for name in ("../flat", "/etc", "homes/flat/../../x", "homes/flat/previous/old"):
        assert call(s, "/api/open", {"home": name})[0] == 400


def test_a_new_home_gets_its_own_folder_and_never_replaces_one(lobby):
    s, _, _, homes = lobby
    assert json.loads(call(s, "/api/new", {"name": "studio-flat"})[1])["out"] == str(homes / "studio-flat")
    assert s.out == homes / "studio-flat"
    assert call(s, "/api/new", {"name": "flat"})[0] == 400 and call(s, "/api/new", {"name": "../x"})[0] == 400


def test_setup_runs_in_the_background_once_and_the_doctor_says_what_is_missing(lobby):
    s, _, jobs, _ = lobby
    assert json.loads(call(s, "/api/doctor")[1])["checks"][0]["fix"] == "vesper setup habitat"
    assert call(s, "/api/setup", {"what": "habitat"})[0] == 200
    call(s, "/api/setup", {"what": "habitat"})
    assert len(jobs) == 1  # one at a time
    status = json.loads(call(s, "/api/setup")[1])["jobs"][0]
    assert status["state"] == "running" and "setting up habitat" in status["tail"]
    jobs[0].terminate()
    time.sleep(0.1)
    assert json.loads(call(s, "/api/setup")[1])["jobs"][0]["state"] == "done"
    assert call(s, "/api/setup", {"what": "rm -rf"})[0] == 400


def test_another_site_s_page_is_refused(lobby):
    s, launch, jobs, _ = lobby
    assert call(s, "/api/setup", {"what": "ha"}, origin="http://evil.example")[0] == 403
    assert call(s, "/api/open", {"home": "homes/flat"}, origin="http://evil.example")[0] == 403
    assert jobs == [] and launch.started == []


def test_an_hssd_house_downloads_in_the_background_and_only_a_scene_id_is_taken(lobby):
    s, _, jobs, _ = lobby
    assert call(s, "/api/setup", {"what": "scene:102344022"})[0] == 200
    status = json.loads(call(s, "/api/setup")[1])["jobs"][0]
    assert status["what"] == "scene:102344022" and "setting up scene:102344022" in status["tail"]
    assert call(s, "/api/setup", {"what": "scene:1; rm -rf /"})[0] == 400
    assert call(s, "/api/setup", {"what": "scene:"})[0] == 400


def test_a_scene_download_runs_vesper_setup_scenes_with_that_id(tmp_path):
    from vesper.lab.studio import StudioServer
    s = StudioServer.__new__(StudioServer)
    s.stack_root = tmp_path
    args = s._setup_args("scene:102344022")
    assert args == ["setup", "scenes", "102344022"]


@pytest.fixture
def two_roots(tmp_path, monkeypatch):
    """The user's homes folder (first root) and an examples folder (a later root), with the Trash in tmp_path."""
    from vesper.lab import studio
    from vesper.lab.studio import StudioServer
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {"validated_products": [],
                                                                                "matter": {"validated_products": []}}})
    trash = tmp_path / "Trash"
    trash.mkdir()
    monkeypatch.setattr(studio, "trash_dir", lambda root: trash)
    homes, examples = tmp_path / "vesper-homes", tmp_path / "examples"
    for d in (homes / "test1", homes / "test2", examples / "demo"):
        d.mkdir(parents=True)
        write_home(d)
    (homes / "broken").mkdir()
    (homes / "broken" / "home.yaml").write_text("not a home")
    launch = Launched()
    s = StudioServer(catalog(), tmp_path / "vds", tmp_path / "results", [homes, examples], root=tmp_path / "stack",
                     port=0, play_port=18997, launch=launch, wait_for_play=lambda port, **kw: True,
                     run_setup=lambda what, log: Job(), doctor=lambda: [])
    s.start()
    yield s, launch, homes, examples, trash
    s.stop()


def test_a_home_in_the_user_s_folder_is_deletable_and_an_example_is_not(two_roots):
    s, _, _, _, _ = two_roots
    rows = {h["name"]: h for h in json.loads(call(s, "/api/homes")[1])["homes"]}
    assert rows["vesper-homes/test1"]["deletable"] and rows["vesper-homes/broken"]["deletable"]
    assert not rows["examples/demo"]["deletable"]


def test_delete_moves_the_home_to_the_trash_and_it_leaves_the_list(two_roots):
    s, _, homes, _, trash = two_roots
    status, body = call(s, "/api/delete", {"home": "vesper-homes/test1"})
    assert status == 200 and not (homes / "test1").exists()
    moved = Path(json.loads(body)["trash"])
    assert moved.parent == trash and (moved / "home.yaml").is_file()  # restorable, not erased
    names = [h["name"] for h in json.loads(call(s, "/api/homes")[1])["homes"]]
    assert "vesper-homes/test1" not in names and "vesper-homes/test2" in names
    status, _ = call(s, "/api/delete", {"home": "vesper-homes/test2"})  # a second one with no name clash
    assert status == 200 and len(list(trash.iterdir())) == 2


def test_a_broken_home_can_be_deleted_too(two_roots):
    s, _, homes, _, _ = two_roots
    assert call(s, "/api/delete", {"home": "vesper-homes/broken"})[0] == 200 and not (homes / "broken").exists()


def test_only_the_user_s_listed_homes_can_be_deleted(two_roots):
    s, _, homes, examples, trash = two_roots
    for name in ("examples/demo", "../vesper-homes/test1", "/etc", "vesper-homes", "vesper-homes/test1/..",
                 "vesper-homes/none"):
        assert call(s, "/api/delete", {"home": name})[0] == 400, name
    assert (examples / "demo").is_dir() and (homes / "test1").is_dir() and not list(trash.iterdir())


def test_deleting_the_home_that_is_playing_stops_its_game_first(two_roots):
    s, launch, homes, _, _ = two_roots
    assert call(s, "/api/open", {"home": "vesper-homes/test2"})[0] == 200
    assert call(s, "/api/delete", {"home": "vesper-homes/test2"})[0] == 200
    assert s._play is None and launch.stopped == [str(homes / "test2" / "home.yaml")]
    assert not (homes / "test2").exists()


def test_the_trash_is_the_mac_s_trash_or_a_hidden_folder_in_the_homes_folder(tmp_path, monkeypatch):
    from vesper.lab.studio import trash_dir
    monkeypatch.setattr("sys.platform", "linux")
    assert trash_dir(tmp_path) == tmp_path / ".trash"
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".Trash").mkdir()
    assert trash_dir(tmp_path / "homes") == tmp_path / ".Trash"
