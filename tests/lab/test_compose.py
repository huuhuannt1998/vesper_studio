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
                      launch=launch, play_port=18999, wait_for_play=lambda port, **kw: True,
                      stack_root=tmp_path / "stack")
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


def test_play_puts_home_assistant_in_the_loop_when_it_is_set_up_and_says_where_it_is_installed(tmp_path):
    from pathlib import Path

    from vesper.lab.compose import hub_ready, play_args
    root = tmp_path / "stack"
    assert hub_ready(root, which=lambda name: "/usr/sbin/mosquitto") is False  # no Home Assistant installed
    (root / "ha" / "venv" / "bin").mkdir(parents=True)
    (root / "ha" / "venv" / "bin" / "hass").write_text("")
    assert hub_ready(root, which=lambda name: None, known=()) is False  # no broker
    assert hub_ready(root, which=lambda name: "/usr/sbin/mosquitto") is True
    args = play_args("h/home.yaml", 18082, Path("cat"), root, hub=True)
    assert args[:2] == ["lab", "play"] and "--hub" in args
    assert args[args.index("--root") + 1] == str(root)
    assert "--hub" not in play_args("h/home.yaml", 18082, Path("cat"), root, hub=False)


class FakeFurnisher:
    """A furnishing run the server starts, follows and stops (vesper.lab.furnish.Furnisher's surface)."""

    made: list = []

    def __init__(self, home_yaml, catalog, catalog_dir, *, model=None, budget=25.0):
        self.home_yaml, self.model, self.budget, self.state = home_yaml, model, budget, "ready"
        FakeFurnisher.made.append(self)

    def start(self):
        self.state = "running"

    def stop(self):
        self.state = "stopped"

    def status(self):
        return {"state": self.state, "home": str(self.home_yaml), "model": self.model, "budget_usd": self.budget}


def test_the_lobby_has_claude_furnish_the_generated_home_in_blender_one_run_at_a_time(server, tmp_path):
    s, _ = server
    s._furnisher = FakeFurnisher
    FakeFurnisher.made = []
    assert json.loads(call(s, "/api/furnish")[1]) == {"state": "none"}
    assert call(s, "/api/furnish", {})[0] == 400  # nothing generated yet
    call(s, "/api/generate", {"format": "vesper-request/1", "rooms": 2, "seed": 1,
                              "devices": [{"category": "light", "per": "room"}]})
    status, body = call(s, "/api/furnish", {"model": "sonnet", "budget": 10})
    assert status == 200 and json.loads(body)["state"] == "running"
    run = FakeFurnisher.made[-1]
    assert (run.home_yaml, run.model, run.budget) == (s.out / "home.yaml", "sonnet", 10.0)
    assert call(s, "/api/furnish", {})[0] == 400  # one run at a time
    assert call(s, "/api/generate", {"format": "vesper-request/1", "rooms": 2, "seed": 2, "devices": []})[0] == 400
    assert json.loads(call(s, "/api/furnish/stop", {})[1])["state"] == "stopped"
    assert call(s, "/api/furnish", {"budget": 500})[0] == 400  # a cap beyond what the page offers


def test_the_page_is_told_whether_claude_and_blender_are_here(server):
    s, _ = server
    meta = json.loads(call(s, "/api/meta")[1])
    assert set(meta["furnish"]) == {"claude", "blender"}


# -- importing a floor plan (floor-plan import, task A4) ------------------------------------------------------------

def px_plan(**kw) -> dict:
    """tests/lab/test_import.py's three rooms drawn at 100 px a metre, as Claude returns a plan read from an image
    (in pixels, no scale read): a living room 5 x 5 m with the front door, a kitchen and a bedroom east of it."""
    from tests.lab.test_import import three_rooms
    plan = three_rooms()
    for r in plan["rooms"]:
        r["polygon"] = [[x * 100, z * 100] for x, z in r["polygon"]]
    for item in (*plan["doors"], *plan["windows"]):
        item["at"] = [item["at"][0] * 100, item["at"][1] * 100]
        item["width"] *= 100
    plan.update(units="px", scale=None, **kw)
    return plan


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64  # what the magic-byte check reads; the fake reader never looks further


class FakeReader:
    """read_plan_image's stand-in: it returns `plan` once `go` is set (a reading takes minutes), or raises."""

    def __init__(self, plan=None, error=None):
        import threading
        self.plan, self.error, self.calls, self.go = plan, error, [], threading.Event()

    def __call__(self, path, stop):
        import time
        from pathlib import Path

        from vesper.lab.plan_read import ReadingStopped
        self.calls.append(Path(path).read_bytes())
        end = time.monotonic() + 5
        while not self.go.is_set():
            if stop.is_set():
                raise ReadingStopped("the reading was stopped")
            assert time.monotonic() < end
            time.sleep(0.01)
        if self.error:
            raise self.error
        return self.plan


def post_bytes(s, path, data, ctype, origin=None, name=None):
    import urllib.request
    req = urllib.request.Request(f"{s.url}{path.lstrip('/')}", data=data,
                                 headers={"Content-Type": ctype, **({"Origin": origin} if origin else {}),
                                          **({"X-Vesper-Name": name} if name else {})})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_for(s, job, state="reading"):
    import time
    for _ in range(100):
        got = json.loads(call(s, f"/api/import/{job}")[1])
        if got["state"] != state:
            return got
        time.sleep(0.05)
    raise AssertionError(f"still {state}")


def test_an_imported_image_is_read_in_the_background_and_its_plan_and_problems_come_back(server):
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    assert json.loads(call(s, "/api/import")[1]) == {"state": "none"}
    status, body = post_bytes(s, "/api/import", PNG, "image/png", origin=s.url.rstrip("/"),
                              name="..%2Fmy%20flat%0A.png")
    started = json.loads(body)
    assert status == 200 and started["state"] == "reading" and started["kind"] == "png"
    assert started["name"] == "my flat.png" and started["started"] > 0  # its last part, printable
    assert json.loads(call(s, "/api/meta")[1])["out"]  # the server answers the page while Claude reads
    assert json.loads(call(s, f"/api/import/{started['job']}")[1])["state"] == "reading"
    assert json.loads(call(s, "/api/import")[1])["job"] == started["job"]  # the latest reading, for a page reopened
    reader.go.set()
    done = wait_for(s, started["job"])
    assert done["state"] == "done" and reader.calls == [PNG]
    assert done["plan"] == px_plan()  # as read, in pixels: the page draws it over the image
    assert done["problems"] and done["problems"][0].startswith("No scale")  # none read, none given yet
    assert "Claude read 3 rooms and 3 doors" in done["summary"]
    assert done["transform"] is None and done["rooms"] is None  # nothing to straighten without a scale
    status, image = call(s, f"/api/import/{started['job']}/image")
    assert status == 200 and image == PNG
    assert json.loads(open(done["saved"]).read()) == px_plan()  # the paid reading is kept


def test_a_reading_that_fails_says_claude_s_reason_and_a_second_reading_waits_for_the_first(server):
    from vesper.lab.plan import PlanError
    s, _ = server
    s._reader = reader = FakeReader(error=PlanError("Claude could not read a floor plan in plan.png: a cat"))
    job = json.loads(post_bytes(s, "/api/import", PNG, "image/png")[1])["job"]
    status, body = post_bytes(s, "/api/import", PNG, "image/png")
    assert status == 400 and "reading a plan already" in json.loads(body)["error"]
    reader.go.set()
    failed = wait_for(s, job)
    assert failed["state"] == "failed" and "a cat" in failed["error"] and "plan" not in failed


def test_a_file_that_is_no_image_is_refused_before_claude_and_a_huge_one_before_it_is_read(server):
    import http.client
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    status, body = post_bytes(s, "/api/import", b"GIF89a not a plan", "image/png")
    assert status == 400 and "not a PNG, JPEG or PDF" in json.loads(body)["error"] and reader.calls == []
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
    c.putrequest("POST", "/api/import")
    c.putheader("Content-Type", "image/png")
    c.putheader("Content-Length", str(50 * 2**20 + 1))
    c.endheaders()
    r = c.getresponse()
    assert r.status == 413 and "50 MB" in json.loads(r.read())["error"]
    assert call(s, "/api/import/0123456789abcdef")[0] == 404
    assert post_bytes(s, "/api/import", PNG, "image/png", origin="https://evil.example")[0] == 403
    assert reader.calls == []


def test_a_plan_file_is_imported_at_once_with_no_claude(server):
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    status, body = post_bytes(s, "/api/import", json.dumps(px_plan()).encode(), "application/json")
    got = json.loads(body)
    assert status == 200 and got["state"] == "done" and got["kind"] == "json" and reader.calls == []
    assert got["plan"] == px_plan() and "saved" not in got
    status, body = post_bytes(s, "/api/import", b'{"format": "vesper-plan/1"}', "application/json")
    assert status == 400 and "missing key 'rooms'" in json.loads(body)["error"]


def test_a_plan_file_nested_too_deeply_is_a_400_not_a_crash(server):
    s, _ = server
    deep = b'{"rooms": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    status, body = post_bytes(s, "/api/import", deep, "application/json")
    assert status == 400 and json.loads(body)["error"] == "the plan file is nested too deeply"
    assert post_bytes(s, "/api/import", json.dumps(px_plan()).encode(), "application/json")[0] == 200


def test_the_check_scales_straightens_and_lists_problems_for_the_plan_as_the_user_edited_it(server):
    s, _ = server
    status, body = call(s, "/api/import/check", {"plan": px_plan(), "scale": "living width 5 m"})
    ok = json.loads(body)
    assert status == 200 and ok["problems"] == []
    assert ok["transform"]["m_per_px"] == pytest.approx(0.01) and ok["transform"]["angle_deg"] == 0.0
    assert [r["id"] for r in ok["rooms"]] == ["lr", "k", "b"]  # as VESPER will build them, in metres
    assert ok["rooms"][0]["polygon"][2] == pytest.approx([5.0, 5.0])
    assert "check them against your image" in ok["summary"]
    assert json.loads(call(s, "/api/import/check", {"plan": px_plan(), "scale": "area 49.5"})[1])["problems"] == []
    edited = px_plan()
    edited["rooms"][2]["type"] = "bathroom"  # 14 m²: too big for a bathroom
    bad = json.loads(call(s, "/api/import/check", {"plan": edited, "scale": "living width 5 m"})[1])
    assert len(bad["problems"]) == 1 and "room 'b' (bathroom)" in bad["problems"][0]
    nope = json.loads(call(s, "/api/import/check", {"plan": px_plan(), "scale": "garage width 3 m"})[1])
    assert "matches no room" in nope["problems"][0] and nope["rooms"] is None
    assert call(s, "/api/import/check", {"scale": "area 50"})[0] == 400


def test_the_check_lists_what_was_left_out_as_warnings_that_do_not_block_generate(server, tmp_path):
    s, _ = server
    plan = px_plan()
    plan["windows"].append({"at": [500, 100], "width": 100, "room": "k"})  # on the wall the kitchen shares
    got = json.loads(call(s, "/api/import/check", {"plan": plan, "scale": "living width 5 m"})[1])
    assert got["problems"] == []
    assert got["warnings"] == ["window 2 of 'k' is not on an outside wall: left out"]
    assert json.loads(call(s, "/api/import/check", {"plan": px_plan(), "scale": "living width 5 m"})[1])[
        "warnings"] == []
    nope = json.loads(call(s, "/api/import/check", {"plan": plan, "scale": "garage width 3 m"})[1])
    assert nope["warnings"] == []  # not straightened: nothing left out yet
    req = {"format": "vesper-request/1", "seed": 3, "devices": [], "plan": plan, "scale": "living width 5 m"}
    status, body = call(s, "/api/generate", req, origin=s.url.rstrip("/"))
    assert status == 200, body


def test_generate_builds_the_edited_plan_as_the_composed_home(server, tmp_path):
    s, _ = server
    edited = px_plan()
    edited["rooms"][2]["type"] = "office"  # the user's edit is what is built
    req = {"format": "vesper-request/1", "seed": 3, "devices": [{"category": "light", "per": "room"}],
           "plan": edited, "scale": "area 49.5", "source": "my flat (1).png"}
    status, body = call(s, "/api/generate", req, origin=s.url.rstrip("/"))
    assert status == 200, body
    made = json.loads(body)
    assert made["summary"]["rooms"] == 3 and made["summary"]["devices"] >= 3
    out = tmp_path / "out"
    text = (out / "home.yaml").read_text()
    assert text.startswith("# Imported from my-flat-1-.png") and "scene: imported/my-flat-1-" in text
    assert "office_light" in text and "bedroom_light" not in text
    assert (out / "plan.json").is_file() and "plan" not in (out / "request.yaml").read_text()
    status, body = call(s, "/api/generate", {**req, "plan": px_plan(), "scale": None})
    assert status == 400 and "No scale" in json.loads(body)["error"]
    bad = px_plan()
    bad["rooms"][1]["type"] = "laundry"
    status, body = call(s, "/api/generate", {**req, "plan": bad})
    assert status == 400 and "not a house" in json.loads(body)["error"]
    assert "'k' (laundry)" in json.loads(body)["error"]
    assert call(s, "/api/generate", {**req, "rooms": 4})[0] == 400  # the plan's rooms, not a count
    # a generated home after an imported one sets the plan aside with the rest
    assert call(s, "/api/generate", {"format": "vesper-request/1", "rooms": 2, "devices": []})[0] == 200
    assert not (out / "plan.json").exists()


def raw(s, request: bytes) -> tuple[int, dict, bytes]:
    """One request written to the server as given (a body shorter than its length, a half-closed socket)."""
    import socket
    k = socket.create_connection(("127.0.0.1", s.port), timeout=5)
    k.sendall(request)
    k.shutdown(socket.SHUT_WR)
    got = b""
    while chunk := k.recv(65536):
        got += chunk
    k.close()
    head, _, body = got.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    return int(lines[0].split()[1]), dict(line.split(": ", 1) for line in lines[1:]), body


def test_a_body_shorter_than_its_length_is_refused_and_nothing_is_read(server):
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    status, _, body = raw(s, f"POST /api/import HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nContent-Length: 1000\r\n"
                             "\r\n".encode() + PNG)
    assert status == 400 and "arrived incomplete" in json.loads(body)["error"]
    assert reader.calls == [] and s.import_status() == {"state": "none"}
    assert not (s.stack_root / "imports").exists()


def test_a_stalled_client_gives_its_thread_back_after_a_minute(server):
    s, _ = server
    assert s._handler().timeout == 60


def test_a_reading_can_be_stopped_and_the_next_one_starts_at_once(server):
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    job = json.loads(post_bytes(s, "/api/import", PNG, "image/png")[1])["job"]
    status, body = call(s, f"/api/import/{job}/stop", {})
    assert status == 200 and json.loads(body)["state"] == "stopped"
    second = json.loads(post_bytes(s, "/api/import", PNG, "image/png")[1])
    assert second["state"] == "reading"  # the one-at-a-time slot is free
    reader.go.set()
    assert wait_for(s, second["job"])["state"] == "done"
    import time
    time.sleep(0.1)
    assert json.loads(call(s, f"/api/import/{job}")[1])["state"] == "stopped"  # its thread never overwrites that
    assert call(s, "/api/import/0123456789abcdef/stop", {})[0] == 404
    assert call(s, f"/api/import/{second['job']}/stop", {})[0] == 400  # done: nothing to stop


def test_the_image_is_served_as_its_type_only_and_a_pdf_is_not_served(server):
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    reader.go.set()
    job = json.loads(post_bytes(s, "/api/import", PNG, "image/png")[1])["job"]
    wait_for(s, job)
    status, headers, body = raw(s, f"GET /api/import/{job}/image HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\n"
                                   "Connection: close\r\n\r\n".encode())
    assert status == 200 and body == PNG
    assert headers["Content-Type"] == "image/png" and headers["X-Content-Type-Options"] == "nosniff"
    pdf = json.loads(post_bytes(s, "/api/import", b"%PDF-1.4\n" + b"\0" * 32, "application/pdf")[1])["job"]
    wait_for(s, pdf)
    assert call(s, f"/api/import/{pdf}/image")[0] == 404  # the page never draws a PDF


def test_a_plan_file_imported_during_a_reading_leaves_the_reading_the_one_followed(server):
    s, _ = server
    s._reader = reader = FakeReader(px_plan())
    reading = json.loads(post_bytes(s, "/api/import", PNG, "image/png")[1])["job"]
    plan_file = json.loads(post_bytes(s, "/api/import", json.dumps(px_plan()).encode(), "application/json")[1])
    assert plan_file["state"] == "done" and plan_file["job"] != reading
    assert json.loads(call(s, "/api/import")[1])["job"] == reading  # a reopened page follows the reading
    reader.go.set()
    assert wait_for(s, reading)["state"] == "done"
    assert json.loads(call(s, "/api/import")[1])["job"] == plan_file["job"]  # then the newest


def test_a_reading_says_how_long_it_may_take_at_worst():
    from vesper.lab.compose import ComposeServer
    from vesper.lab.plan_read import TIMEOUT_S
    assert ComposeServer.READING_LIMIT_S == 2 * TIMEOUT_S  # one answer, one retry


def with_closet() -> dict:
    """px_plan with a 1.8 m² toilet off the bedroom (as plan1's reading of the 30x35 plan has): too small for any
    type, so only leaving it out fixes it. A window in it, and a back door from the kitchen."""
    plan = px_plan()
    plan["rooms"].append({"id": "c", "name": "Toilet", "type": "bathroom",
                          "polygon": [[850, 520], [970, 520], [970, 670], [850, 670]]})
    plan["doors"] += [{"at": [850, 600], "width": 70, "rooms": ["b", "c"]},
                      {"at": [850, 100], "width": 90, "rooms": ["k", "outside"]}]
    plan["windows"].append({"at": [970, 600], "width": 60, "room": "c"})
    return plan


def left_out(plan: dict, room: str) -> dict:
    """What the page's editedPlan sends for a room left out (tests/lab/ui/compose.test.mjs checks the page does
    this): the room, every door touching it and its windows gone, the entrance's index kept on its door."""
    out = json.loads(json.dumps(plan))
    entrance = plan["doors"][plan["entrance"]]
    out["rooms"] = [r for r in out["rooms"] if r["id"] != room]
    out["doors"] = [d for d in out["doors"] if room not in d["rooms"]]
    out["windows"] = [w for w in out["windows"] if w["room"] != room]
    out["entrance"] = out["doors"].index(entrance) if entrance in out["doors"] else None
    return out


def test_a_room_left_out_on_the_page_is_checked_without_it_and_a_lost_entrance_is_a_problem(server):
    s, _ = server
    for kind in ("bathroom", "other"):  # no type fits a 1.8 m² closet
        plan = with_closet()
        plan["rooms"][3]["type"] = kind
        got = json.loads(call(s, "/api/import/check", {"plan": plan, "scale": "living width 5 m"})[1])
        assert [p.split(":")[0] for p in got["problems"]] == [f"room 'c' ({kind})"]
    fixed = json.loads(call(s, "/api/import/check", {"plan": left_out(with_closet(), "c"),
                                                     "scale": "living width 5 m"})[1])
    assert fixed["problems"] == [] and [r["id"] for r in fixed["rooms"]] == ["lr", "k", "b"]
    no_door = json.loads(call(s, "/api/import/check", {"plan": left_out(with_closet(), "lr"),
                                                       "scale": "kitchen width 3.5 m"})[1])
    # the page puts its own note ("the entrance was in Living room: choose another") in this line's place
    assert "no entrance: the plan names no door as the entrance" in no_door["problems"]


def test_scale_text_is_a_room_s_size_or_the_home_s_area():
    from vesper.lab.compose import scale_args
    assert scale_args("") == (None, None) and scale_args(None) == (None, None)
    assert scale_args("area 95") == (None, 95.0) and scale_args(" 95 m² ") == (None, 95.0)
    assert scale_args("Area 80.5 m2") == (None, 80.5)
    assert scale_args("kitchen width 3.2 m") == ("kitchen width 3.2 m", None)


def test_the_page_offers_every_type_a_plan_s_room_may_have():
    import re

    from vesper.lab.play import UI_DIR
    from vesper.lab.plan import ROOM_TYPES
    js = re.search(r"export const PLAN_TYPES = \[(.*?)\];", (UI_DIR / "compose.js").read_text(), re.S).group(1)
    assert re.findall(r'"(\w+)"', js) == list(ROOM_TYPES)


def test_every_element_the_compose_script_looks_up_is_on_its_page():
    import re

    from vesper.lab.play import UI_DIR
    page, js = (UI_DIR / "compose.html").read_text(), (UI_DIR / "compose.js").read_text()
    for name in set(re.findall(r'\$\("([\w-]+)"\)', js)):
        assert f'id="{name}"' in page, f"compose.js looks up #{name}, which compose.html does not have"
