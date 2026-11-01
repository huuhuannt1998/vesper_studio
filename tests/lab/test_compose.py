"""Composing a home in the browser (plan W10): real products with their evidence, firmware and configuration, a
request built from rooms and device rows, the home generated from it."""
import json

import pytest

from tests.lab.homes import catalog
from vesper.lab.compose import ComposeError, compose_home, evidence, levels, product_card, search_products


ANCHOR = {"model": "zigbee:light", "product": "zigbee:acme:dim-1", "firmware": "test firmware 1.0",
          "soc": "ESP32-C3 (QEMU)", "interface": "RPC", "transitions_agree": "5/5", "values_agree": "18/18"}


def results(tmp_path, l2=("zigbee:acme:pir-1",)):
    d = tmp_path / "results"
    d.mkdir()
    return d, {"l2": {"validated_products": list(l2), "matter": {"validated_products": []}},
               "l4": {"zigbee:light": ANCHOR}}


def test_a_product_s_levels_show_only_what_its_evidence_supports(tmp_path, monkeypatch):
    d, report = results(tmp_path)
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda catalog_dir, results_dir: report)
    ev = evidence(tmp_path / "vds", d)
    cat = catalog()
    assert levels(cat["zigbee:acme:pir-1"], ev) == {"L0": True, "L1": True, "L2": True, "L3": False, "L4": False}
    assert levels(cat["zigbee:acme:dim-1"], ev)["L4"] is True and levels(cat["zigbee:acme:dim-1"], ev)["L2"] is False
    assert levels(cat["zigbee:acme:plug-1"], ev)["L2"] is False


def test_search_finds_by_words_and_filters_best_evidenced_first(tmp_path, monkeypatch):
    d, report = results(tmp_path)
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda catalog_dir, results_dir: report)
    ev = evidence(tmp_path / "vds", d)
    found = search_products(catalog(), ev, "", category="motion_sensor")
    assert found and found[0]["id"] == "zigbee:acme:pir-1" and all(r["category"] == "motion_sensor" for r in found)
    assert found[0]["levels"]["L2"] is True and found[0]["selectable"] is True
    lock = search_products(catalog(), ev, "lock", protocol="matter")
    assert [r["id"] for r in lock] == ["matter:115f:0001"]
    assert "Home Assistant with --matter-interface" in lock[0]["network"]


def test_a_product_s_card_says_its_configuration_its_network_and_its_firmware(tmp_path, monkeypatch):
    d, report = results(tmp_path)
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda catalog_dir, results_dir: report)
    ev = evidence(tmp_path / "vds", d)
    card = product_card(catalog()["zigbee:acme:dim-1"], ev)
    assert card["protocol"] == "zigbee" and "Zigbee mesh" in card["network"]
    settable = {f["name"] for f in card["config"] if f["settable"]}
    assert "state" in settable and "brightness" in settable
    assert card["firmware"]["anchor"]["firmware"] == "test firmware 1.0"
    plain = product_card(catalog()["zigbee:acme:pir-1"], ev)
    assert plain["firmware"]["anchor"] is None and "protocol-level twin" in plain["firmware"]["text"]


def test_a_z_wave_product_is_shown_but_cannot_be_chosen():
    from vesper.lab.compose import network_of
    assert network_of({"binding": {"zwave": {}}}) == ("Z-Wave: identity only, not simulated", False)


def test_a_request_from_the_page_becomes_a_home_or_says_what_is_wrong(tmp_path):
    out = tmp_path / "home"
    request = {"format": "vesper-request/1", "rooms": 4, "seed": 1,
               "devices": [{"category": "light", "per": "room"}, {"product": "zigbee:acme:pir-1", "at": "living"}]}
    made = compose_home(request, catalog(), out)
    assert made["home"] == str(out / "home.yaml") and (out / "request.yaml").is_file()
    assert made["summary"]["rooms"] == 4 and made["summary"]["devices"] >= 5
    with pytest.raises(ComposeError, match="kitchenette"):
        compose_home({**request, "program": ["kitchenette"]}, catalog(), tmp_path / "bad")


def test_composing_again_replaces_the_home_keeps_its_traces_and_sets_the_old_one_aside(tmp_path):
    out = tmp_path / "home"
    request = {"format": "vesper-request/1", "rooms": 3, "seed": 1, "devices": [{"category": "light", "per": "room"}]}
    compose_home(request, catalog(), out)
    (out / "traces").mkdir()
    (out / "traces" / "t.jsonl").write_text("{}\n")
    (out / "scene.glb").write_bytes(b"glTF")  # dressed for the old layout
    made = compose_home({**request, "rooms": 5}, catalog(), out)
    assert made["summary"]["rooms"] == 5 and (out / "traces" / "t.jsonl").is_file()
    (old,) = (out / "previous").iterdir()
    assert (old / "home.yaml").is_file() and (old / "scene.glb").is_file() and not (out / "scene.glb").exists()
    assert not list(out.glob(".compose-*"))


class Launched:
    def __init__(self):
        self.started, self.stopped = [], []

    def __call__(self, home, port):
        self.started.append((home, port))
        me = self

        class Proc:
            def poll(self):
                return None

            def terminate(self):
                me.stopped.append(home)

            def wait(self, timeout=None):
                return 0
        return Proc()


@pytest.fixture
def server(tmp_path, monkeypatch):
    from vesper.lab.compose import ComposeServer
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {"validated_products": [],
                                                                                "matter": {"validated_products": []}}})
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "compose.html").write_text("<h1>compose</h1>")
    (ui / "compose.js").write_text("// compose")
    launch = Launched()
    s = ComposeServer(catalog(), tmp_path / "vds", tmp_path / "results", tmp_path / "out", port=0, ui_dir=ui,
                      launch=launch, play_port=18999, wait_for_play=lambda port, alive=None: True)
    s.start()
    yield s, launch
    s.stop()


def call(s, path, body=None, origin=None):
    import urllib.request
    req = urllib.request.Request(f"{s.url}{path.lstrip('/')}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_the_page_and_its_api_are_served_on_loopback(server):
    s, _ = server
    assert s.url.startswith("http://127.0.0.1:")
    assert call(s, "/")[1] == b"<h1>compose</h1>" and call(s, "/compose.js")[0] == 200
    assert call(s, "/../secret")[0] == 404
    meta = json.loads(call(s, "/api/meta")[1])
    assert "bedroom" in meta["room_types"] and "light" in meta["categories"] and "zigbee" in meta["protocols"]
    found = json.loads(call(s, "/api/search?category=motion_sensor")[1])
    assert found["products"][0]["id"] == "zigbee:acme:pir-1"
    card = json.loads(call(s, "/api/product?id=zigbee:acme:dim-1")[1])
    assert card["firmware"]["anchor"] is None and any(f["settable"] for f in card["config"])
    assert call(s, "/api/product?id=nope")[0] == 404


def test_generate_writes_the_home_and_play_starts_one_play_at_a_time(server, tmp_path):
    s, launch = server
    req = {"format": "vesper-request/1", "rooms": 3, "seed": 2, "devices": [{"category": "light", "per": "room"}]}
    status, body = call(s, "/api/generate", req, origin=s.url.rstrip("/"))
    made = json.loads(body)
    assert status == 200 and made["summary"]["rooms"] == 3 and (tmp_path / "out" / "home.yaml").is_file()
    status, body = call(s, "/api/generate", {**req, "program": ["kitchenette"]})
    assert status == 400 and "kitchenette" in json.loads(body)["error"]
    status, body = call(s, "/api/play", {})
    assert status == 200 and json.loads(body)["url"] == "http://127.0.0.1:18999/"
    call(s, "/api/play", {})
    assert len(launch.started) == 2 and launch.stopped == [str(tmp_path / "out" / "home.yaml")]


def test_another_site_s_page_cannot_make_the_server_generate_or_play(server):
    s, launch = server
    req = {"format": "vesper-request/1", "rooms": 3, "devices": []}
    assert call(s, "/api/generate", req, origin="https://evil.example")[0] == 403
    assert call(s, "/api/play", {}, origin="https://evil.example")[0] == 403 and launch.started == []


def test_vesper_lab_compose_serves_the_page_until_interrupted(tmp_path, capsys, monkeypatch):
    import re
    import urllib.request
    from tests.lab.test_cli_lab import setup
    from vesper import cli
    cat, _ = setup(tmp_path / "c")
    seen = {}

    def interrupted():
        seen["out"] = capsys.readouterr().out
        url = re.search(r"http://127\.0\.0\.1:\d+/", seen["out"]).group(0)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + "api/meta", timeout=5) as r:
            seen["meta"] = json.loads(r.read())
    monkeypatch.setattr(cli, "_wait_for_interrupt", interrupted)
    out = tmp_path / "mine"
    assert cli.main(["lab", "compose", str(out), "--catalog", str(cat), "--port", "0", "--no-browser"]) == 0
    assert "Compose a home" in seen["out"] and seen["meta"]["out"] == str(out)


def test_l4_follows_the_twin_model_and_the_card_says_so(tmp_path, monkeypatch):
    from vesper.validation.report import twin_model
    d, report = results(tmp_path, l2=())
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: report)
    ev = evidence(tmp_path / "vds", d)
    cat = catalog()
    assert twin_model(cat["matter:115f:0001"]) == "matter:Door Lock"
    assert twin_model(cat["zigbee:acme:dim-1"]) == "zigbee:light"
    assert levels(cat["zigbee:acme:dim-1"], ev)["L4"] is True and levels(cat["zigbee:acme:pir-1"], ev)["L4"] is False
    text = product_card(cat["zigbee:acme:dim-1"], ev)["firmware"]["text"]
    assert "twin model" in text and "zigbee:acme:dim-1" in text


def test_tasmota_products_that_passed_their_acceptance_show_l2(tmp_path, monkeypatch):
    d = tmp_path / "results"
    d.mkdir()
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {
        "l2": {"validated_products": [], "tasmota": {"validated_products": ["tasmota:a:b"]}}})
    assert evidence(tmp_path / "vds", d)["L2"] == {"tasmota:a:b"}


def test_two_plays_at_once_leave_one_child_and_stopping_ends_every_child(server):
    import threading
    s, launch = server
    call(s, "/api/generate", {"format": "vesper-request/1", "rooms": 3, "devices": []})
    threads = [threading.Thread(target=lambda: call(s, "/api/play", {})) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(launch.started) == 4 and len(launch.stopped) == 3  # each Play replaced the one before
    s.stop()
    assert len(launch.stopped) == 4


def test_play_refuses_a_port_another_program_holds_and_says_when_the_child_dies(tmp_path, monkeypatch):
    import socket
    from vesper.lab.compose import ComposeServer
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {"validated_products": [],
                                                                                "matter": {"validated_products": []}}})
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    busy = held.getsockname()[1]

    class Dead:
        def poll(self):
            return 1

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 1
    s = ComposeServer(catalog(), tmp_path / "vds", tmp_path / "results", tmp_path / "out", port=0,
                      launch=lambda home, p: Dead(), play_port=busy)
    compose_home({"format": "vesper-request/1", "rooms": 2, "devices": []}, catalog(), tmp_path / "out")
    with pytest.raises(ComposeError, match=f"port {busy} is in use"):
        s.play()
    held.close()
    with pytest.raises(ComposeError, match="vesper lab play stopped"):
        s.play()


def test_generates_at_once_run_one_after_the_other(server, monkeypatch):
    import threading
    import time
    s, _ = server
    inside, overlap = [], []
    real = __import__("vesper.lab.compose", fromlist=["compose_home"]).compose_home

    def slow(*a, **k):
        if inside:
            overlap.append(1)
        inside.append(1)
        time.sleep(0.1)
        try:
            return real(*a, **k)
        finally:
            inside.pop()
    monkeypatch.setattr("vesper.lab.compose.compose_home", slow)
    req = {"format": "vesper-request/1", "rooms": 3, "devices": []}
    threads = [threading.Thread(target=lambda: call(s, "/api/generate", req)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlap == []


def test_the_server_answers_localhost_refuses_a_foreign_host_and_a_negative_length(server):
    import http.client
    s, _ = server
    req = {"format": "vesper-request/1", "rooms": 3, "devices": []}
    assert call(s, "/api/generate", req, origin=f"http://localhost:{s.port}")[0] == 200
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    c.request("GET", "/api/meta", headers={"Host": "evil.example"})
    assert c.getresponse().status == 403
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    c.putrequest("POST", "/api/generate")
    c.putheader("Content-Length", "-5")
    c.endheaders()
    assert c.getresponse().status == 400


def test_a_request_that_fails_leaves_a_running_play_alone(server):
    s, launch = server
    call(s, "/api/generate", {"format": "vesper-request/1", "rooms": 3, "devices": []})
    call(s, "/api/play", {})
    assert call(s, "/api/generate", {"format": "vesper-request/1", "program": ["kitchenette"]})[0] == 400
    assert launch.stopped == []


def test_a_play_that_stops_says_why_in_its_own_words(tmp_path, monkeypatch):
    from vesper.lab.compose import ComposeServer
    monkeypatch.setattr("vesper.lab.compose.build_report", lambda c, r: {"l2": {"validated_products": []}})
    log = tmp_path / "play.log"
    log.write_text("starting\nFAILED: the catalog in catalog/vds is stale: rebuild it with `vesper catalog build`\n\n")

    class Dead:
        def __init__(self):
            self.log = log

        def poll(self):
            return 1

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 1
    s = ComposeServer(catalog(), tmp_path / "vds", tmp_path / "results", tmp_path / "out", port=0,
                      launch=lambda home, p: Dead(), play_port=0)
    compose_home({"format": "vesper-request/1", "rooms": 2, "devices": []}, catalog(), tmp_path / "out")
    with pytest.raises(ComposeError, match="the catalog in catalog/vds is stale"):
        s.play()


def test_the_page_lists_the_hssd_houses_that_are_ready_and_a_house_request_makes_that_house(server, tmp_path,
                                                                                             monkeypatch):
    from tests.lab.test_generate import fake_export
    from vesper.lab import scenes
    from vesper.lab.scenes import SceneInfo
    s, _ = server
    monkeypatch.setattr(scenes, "list_scenes", lambda hssd: [
        SceneInfo("102344022", 20, ["bedroom", "kitchen", "living room"], 180.5),
        SceneInfo("102343992", 9, ["kitchen"], 90.0, missing=["objects/x.glb"])])
    listed = json.loads(call(s, "/api/scenes")[1])
    assert listed["scenes"][0] == {"id": "102344022", "rooms": 20, "labels": ["bedroom", "kitchen", "living room"],
                                   "indoor_m2": 180.5, "storeys": 1, "ready": True, "missing": 0}
    assert listed["scenes"][1]["ready"] is False and listed["scenes"][1]["missing"] == 1  # offered for download
    monkeypatch.setattr("vesper.lab.generate._export_scene", fake_export)
    req = {"format": "vesper-request/1", "scene": "102344022", "seed": 0,
           "devices": [{"category": "light", "per": "room"}]}
    status, body = call(s, "/api/generate", req, origin=s.url.rstrip("/"))
    assert status == 200, body
    assert "HSSD scene 102344022" in (tmp_path / "out" / "home.yaml").read_text()


def test_without_hssd_the_page_says_how_to_get_it(server, monkeypatch):
    from vesper.lab import scenes
    s, _ = server

    def none(hssd):
        raise scenes.SceneError("/nowhere holds no HSSD scenes")
    monkeypatch.setattr(scenes, "list_scenes", none)
    listed = json.loads(call(s, "/api/scenes")[1])
    assert listed["scenes"] == [] and "no HSSD scenes" in listed["error"]
