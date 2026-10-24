"""`vesper validate matter`, wired to fakes: no Matter Server, Home Assistant or Node process is started."""
import json
from types import SimpleNamespace

import pytest

import vesper.exposure.matter_tier as tier_mod
import vesper.twins.clock as clock_mod
import vesper.validation.matter_acceptance as macc_mod
import vesper.validation.stack as stack_mod
from tests.catalog.test_matter_normalize import records as matter_records
from tests.twins.conftest import write_catalog
from vesper import cli


class Events:
    log = []


class FakeServer:
    def __init__(self, root, storage, port=15580, interface=None, **kw):
        self.storage, self.port, self.interface = storage, port, interface
        Events.servers.append(self)

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/ws"

    def start(self):
        Events.log.append("server.start")
        if Events.fail == "server":
            raise stack_mod.StackError("port 15580 is already in use")

    def stop(self):
        Events.log.append("server.stop")


class FakeHA:
    def __init__(self, root, port=18123, **kw):
        self.port = port

    def start(self):
        Events.log.append("ha.start")

    def onboard(self):
        Events.log.append("ha.onboard")

    def ensure_matter(self, url):
        Events.log.append(("ha.ensure_matter", url))

    def stop(self):
        Events.log.append("ha.stop")


class FakeHost:
    def __init__(self, storage, *, interface=None, command=None, log=None, **kw):
        self.storage, self.interface, self.command, self.log, self.notes = storage, interface, command, log, []
        Events.hosts.append(self)

    def start(self):
        Events.log.append("host.start")

    def stop(self):
        Events.log.append("host.stop")

    def on_event(self, fn):
        pass

    def request(self, op, wait=True, **a):
        Events.log.append(f"host.{op}")
        if op == "add":
            Events.adds.append(a)
            return {"resolved": [f["name"] for f in a["features"]], "unresolved": [],
                    "pairing": {"manual": "34970112332", "qr": "MT:Y.K90AFN00KA0648G00"}}
        return {}


def fake_check(ha, runtime, tier, twin_id, server_url, *, timeout, poll, commission_timeout):
    Events.checked.append((twin_id, server_url, commission_timeout))
    spec = tier.specs[twin_id]
    paired = Events.fail != "commission"
    return {"id": twin_id, "twin_id": twin_id, "device_type": spec.device_type,
            "category": runtime.twins[twin_id].vds["identity"]["category"], "serial": spec.serial,
            "node_id": 1 if paired else None, "passed": paired and twin_id not in Events.failing, "exercised": True,
            "entities": [{"created": paired and not Events.no_entities}]}


@pytest.fixture
def matter(monkeypatch, tmp_path):
    Events.log, Events.servers, Events.hosts, Events.checked, Events.adds = [], [], [], [], []
    Events.fail, Events.failing, Events.no_entities = None, set(), False
    monkeypatch.setattr(stack_mod, "MatterServer", FakeServer)
    monkeypatch.setattr(stack_mod, "HomeAssistant", FakeHA)
    monkeypatch.setattr(tier_mod, "Host", FakeHost)
    monkeypatch.setattr(macc_mod, "check_matter_product", fake_check)
    monkeypatch.setattr(stack_mod, "ha_has_matter", lambda root: True)

    class Clock(clock_mod.LogicalClock):
        def close(self):
            Events.log.append("clock.close")

    monkeypatch.setattr(clock_mod, "WallClock", Clock)
    root = tmp_path / ".vesper"
    host_dir = stack_mod.Paths(root).matter_host
    host_dir.mkdir(parents=True)
    (host_dir / "host.mjs").write_text("// installed")
    (host_dir / "package.json").write_text(json.dumps({"dependencies": {"@matter/main": "0.17.9",
                                                                         "matter-server": "1.4.0"}}))
    catalog = write_catalog(tmp_path / "vds", list(matter_records().values()))
    return SimpleNamespace(root=root, catalog=catalog, host_dir=host_dir)


def run(m, tmp_path, *extra):
    return cli.main(["validate", "matter", "--catalog", str(m.catalog), "--root", str(m.root),
                     "--interface", "en0", "--out", str(tmp_path / "matter.json"), *extra])


def test_matter_acceptance_runs_the_whole_stack_and_writes_a_report(matter, tmp_path, capsys):
    assert run(matter, tmp_path) == 0
    log = Events.log
    assert log[:4] == ["server.start", "ha.start", "ha.onboard", ("ha.ensure_matter", "ws://127.0.0.1:15580/ws")]
    assert log.index("host.start") < log.index("host.add") and log.count("host.add") == 4
    assert log[-4:] == ["host.stop", "clock.close", "ha.stop", "server.stop"] and log.count("host.remove") == 4
    [server], [host] = Events.servers, Events.hosts
    assert server.interface == host.interface == "en0" and server.storage.parent == host.storage.parent
    assert server.storage.name == "server" and host.storage.name == "nodes"
    assert host.command == ["node", str(matter.host_dir / "host.mjs")]
    assert {c[1] for c in Events.checked} == {"ws://127.0.0.1:15580/ws"}
    report = json.loads((tmp_path / "matter.json").read_text())
    assert report["summary"]["passed"] == report["summary"]["products"] == 4
    assert report["matter_packages"] == {"@matter/main": "0.17.9", "matter-server": "1.4.0"}
    assert (report["interface"], report["vid_mode"], report["seed"], report["per_type"]) == ("en0", "product", 0, 1)
    assert report["run"]["parameters"]["per_type"] == 1
    assert "4/4 products passed" in capsys.readouterr().out


def test_a_failing_product_fails_the_run(matter, tmp_path):
    Events.failing = {"matter:115f:0001"}
    assert run(matter, tmp_path) == 1
    report = json.loads((tmp_path / "matter.json").read_text())
    assert report["summary"]["passed"] == 3


def test_the_interface_is_required_because_matter_cannot_use_loopback(matter, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["validate", "matter", "--catalog", str(matter.catalog), "--root", str(matter.root)])
    assert "--interface" in capsys.readouterr().err


def test_a_failed_start_stops_whatever_was_started(matter, tmp_path, capsys):
    Events.fail = "server"
    assert run(matter, tmp_path) == 1
    assert "FAILED matter: port 15580 is already in use" in capsys.readouterr().err
    assert Events.log == ["server.start", "host.stop", "clock.close", "ha.stop", "server.stop"]
    assert not (tmp_path / "matter.json").exists()


def test_the_run_gives_up_when_no_node_can_be_commissioned(matter, tmp_path, capsys):
    Events.fail = "commission"
    assert run(matter, tmp_path) == 1
    assert "commissioned none of the first 3 nodes" in capsys.readouterr().err
    assert len(Events.checked) == 3 and Events.log[-1] == "server.stop"


def test_an_uninstalled_host_is_reported_before_anything_starts(matter, tmp_path, capsys):
    (matter.host_dir / "host.mjs").unlink()
    assert run(matter, tmp_path) == 1
    assert "vesper setup matter" in capsys.readouterr().err and "server.start" not in Events.log


def test_the_run_stops_when_home_assistant_shows_nothing_for_commissioned_nodes(matter, tmp_path, capsys):
    Events.no_entities = True
    assert run(matter, tmp_path) == 1
    err = capsys.readouterr().err
    assert "Home Assistant showed no entity for the first 3 commissioned nodes" in err and "hass.log" in err
    assert len(Events.checked) == 3 and Events.log[-1] == "server.stop"


def test_home_assistant_without_the_matter_packages_is_reported_before_anything_starts(matter, tmp_path, capsys,
                                                                                         monkeypatch):
    monkeypatch.setattr(stack_mod, "ha_has_matter", lambda root: False)
    assert run(matter, tmp_path) == 1
    assert "run `vesper setup ha` again" in capsys.readouterr().err and "server.start" not in Events.log


def test_setup_codes_differ_between_runs(matter, tmp_path):
    assert run(matter, tmp_path) == 0
    first = sorted(a["passcode"] for a in Events.adds)
    Events.adds.clear()
    assert run(matter, tmp_path) == 0
    assert sorted(a["passcode"] for a in Events.adds) != first


def test_test_vendor_mode_is_passed_to_every_node(matter, tmp_path):
    assert run(matter, tmp_path, "--vid-mode", "test") == 0
    assert {a["vendor_id"] for a in Events.adds} == {tier_mod.TEST_VENDOR_ID}
    assert json.loads((tmp_path / "matter.json").read_text())["vid_mode"] == "test"
