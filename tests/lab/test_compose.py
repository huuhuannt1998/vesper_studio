"""Composing a home in the browser (plan W10): real products with their evidence, firmware and configuration, a
request built from rooms and device rows, the home generated from it."""
import json

import pytest

from tests.lab.homes import catalog
from vesper.lab.compose import ComposeError, compose_home, evidence, levels, product_card, search_products


def results(tmp_path, l2=("zigbee:acme:pir-1",)):
    d = tmp_path / "results"
    d.mkdir()
    (d / "anchors-20261031T200000Z.json").write_text(json.dumps({"kind": "anchors", "anchors": [
        {"product": "zigbee:acme:dim-1", "firmware": "test firmware 1.0", "soc": "ESP32-C3 (QEMU)",
         "interface": "RPC", "transitions_agree": "5/5", "values_agree": "18/18"}]}))
    return d, {"l2": {"validated_products": list(l2), "matter": {"validated_products": []}}}


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
                      launch=launch, play_port=18999, wait_for_play=lambda port: True)
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
