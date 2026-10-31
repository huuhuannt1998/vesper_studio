"""VESPER Studio's lobby (plans W5, W6): the homes in the user's folders (play one, compose a new one), this
computer's checks, and setup run in the background; on 127.0.0.1 only."""
import json
import time

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
                     play_port=18998, launch=launch, wait_for_play=lambda port, alive=None: True, run_setup=run_setup,
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
