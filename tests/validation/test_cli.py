"""The experiment commands, wired to an in-memory broker and Home Assistant: no process is started."""
import json
import time
from types import SimpleNamespace

import pytest

import vesper.exposure.mqtt as mqtt_mod
import vesper.twins.clock as clock_mod
import vesper.validation.acceptance as acceptance_mod
import vesper.validation.roundtrip as roundtrip_mod
import vesper.validation.stack as stack_mod
from tests.catalog.conftest import write_all
from tests.twins.conftest import records, write_catalog
from tests.exposure.fake_ha import FakeHA
from tests.exposure.fake_mqtt import FakeBroker
from vesper import cli


@pytest.fixture
def broker(monkeypatch):
    b = FakeBroker()
    connected = []

    def connect(host, port, client_id, timeout=10.0, will=None):
        connected.append((host, port, client_id))
        if will:
            b.wills[client_id] = will
        return b.client()

    def disconnect(client):
        b.unflushed += sum(not d.waited for d in client.deliveries)  # paho drops what is still queued
        client.connected = False

    monkeypatch.setattr(mqtt_mod, "connect", connect)
    monkeypatch.setattr(mqtt_mod, "disconnect", disconnect)
    b.connected, b.wills, b.unflushed = connected, {}, 0
    return b


@pytest.fixture
def catalog(tmp_path):
    return write_catalog(tmp_path / "vds", list(records().values()), inputs={"zigbee": {"version": "26.123.0"}})


class FakeMosquitto:
    instances = []

    def __init__(self, root, port=18830, **kwargs):
        self.root, self.port, self.events = root, port, []
        FakeMosquitto.instances.append(self)

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


@pytest.fixture
def mosquitto(monkeypatch):
    FakeMosquitto.instances = []
    monkeypatch.setattr(stack_mod, "Mosquitto", FakeMosquitto)
    return FakeMosquitto.instances


def test_setup_ha_reports_what_it_installed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(stack_mod, "setup_ha", lambda root: ["home-assistant-frontend==20260930.4"])
    assert cli.main(["setup", "ha", "--root", str(tmp_path)]) == 0
    assert "home-assistant-frontend==20260930.4" in capsys.readouterr().out


def test_setup_ha_failure_exits_nonzero(monkeypatch, tmp_path, capsys):
    def fail(root):
        raise stack_mod.StackError("uv not found")

    monkeypatch.setattr(stack_mod, "setup_ha", fail)
    assert cli.main(["setup", "ha", "--root", str(tmp_path)]) == 1
    assert "FAILED setup: uv not found" in capsys.readouterr().err


def test_twins_run_exposes_the_home_and_goes_offline_on_ctrl_c(monkeypatch, tmp_path, broker, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("broker: {host: 127.0.0.1, port: 18830}\nseed: 3\ndevices:\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: hall/door}\n"
                    "  - {product: 'zigbee:aqara:mccgq11lm', name: kitchen/door}\n")

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", interrupt)
    assert cli.main(["twins", "run", str(home), "--catalog", str(catalog)]) == 0
    assert broker.connected == [("127.0.0.1", 18830, "vesper-bridge")]
    devices = broker.last("zigbee2mqtt/bridge/devices")
    assert [d["friendly_name"] for d in devices] == ["Coordinator", "hall/door", "kitchen/door"]
    assert devices[1]["ieee_address"] != devices[2]["ieee_address"]
    assert broker.last("zigbee2mqtt/bridge/info")["zigbee_herdsman_converters"]["version"] == "26.123.0"
    assert broker.last("zigbee2mqtt/bridge/state") == {"state": "offline"}


def test_roundtrip_publishes_every_definition_unchanged(monkeypatch, tmp_path, broker, mosquitto, capsys):
    raw = write_all(tmp_path / "raw")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--external-broker"]) == 0
    assert mosquitto == []  # an external broker was asked for: none is started
    [path] = (tmp_path / "results" / "device-twins").glob("roundtrip-*.json")
    report = json.loads(path.read_text())
    assert report["checked"] == report["identical"] == 13
    assert report["different"] == {} and report["unmatched"] == []
    assert report["zhc_version"] == "26.123.0" and report["l1_with_diffs"] == []
    assert "13/13 definitions identical" in capsys.readouterr().out
    assert broker.retained == {}  # the bridge retracted every retained message it published


def test_roundtrip_reports_a_definition_that_changed_on_the_way(monkeypatch, tmp_path, broker, mosquitto):
    raw = write_all(tmp_path / "raw")
    real, changed = roundtrip_mod.source_definitions, []

    def tampered(doc):
        sources = real(doc)
        first = sorted(sources)[0]
        sources[first] = {**sources[first], "description": "something else"}
        changed.append(first)
        return sources

    monkeypatch.setattr(roundtrip_mod, "source_definitions", tampered)
    out = tmp_path / "rt.json"
    assert cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--external-broker", "--out", str(out)]) == 1
    assert list(json.loads(out.read_text())["different"]) == changed


def test_roundtrip_starts_and_always_stops_its_own_broker(monkeypatch, tmp_path, mosquitto):
    raw = write_all(tmp_path / "raw")

    def refused(*a, **k):
        raise ConnectionError("no MQTT connection")

    monkeypatch.setattr(mqtt_mod, "connect", refused)
    with pytest.raises(ConnectionError):
        cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--root", str(tmp_path / ".vesper")])
    [m] = mosquitto
    assert m.port == 18830 and m.events == ["start", "stop"]


class StackHA:
    """`stack.HomeAssistant` replaced by the in-memory one, recording its lifecycle."""
    instances = []

    def __init__(self, root, port=18123, **kwargs):
        self.port, self.events, self.fail_start = port, [], False
        StackHA.instances.append(self)

    def start(self):
        self.events.append("start")
        if StackHA.fail_start:
            raise stack_mod.StackError("Home Assistant did not answer")

    def onboard(self):
        self.events.append("onboard")

    def ensure_mqtt(self, port):
        self.events.append(("ensure_mqtt", port))

    def stop(self):
        self.events.append("stop")

    def state(self, entity_id):
        return FakeHA(StackHA.broker).state(entity_id)

    def call(self, domain, service, data):
        FakeHA(StackHA.broker).call(domain, service, data)


@pytest.fixture
def stack(monkeypatch, broker, mosquitto):
    StackHA.instances, StackHA.broker, StackHA.fail_start = [], broker, False
    monkeypatch.setattr(stack_mod, "HomeAssistant", StackHA)
    clocks = []

    class Clock(clock_mod.LogicalClock):
        def __init__(self):
            super().__init__()
            clocks.append(self)

        def close(self):
            pass

    # The twins keep simulated time; each poll of Home Assistant lets 0.25 s of it pass.
    monkeypatch.setattr(clock_mod, "WallClock", Clock)
    monkeypatch.setattr(acceptance_mod, "time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=lambda s: clocks[-1].advance(s)))
    return SimpleNamespace(ha=StackHA.instances, mosquitto=mosquitto)


def test_acceptance_runs_the_whole_stack_and_writes_a_report(tmp_path, catalog, stack, capsys):
    out = tmp_path / "acc.json"
    code = cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--per-category", "1", "--timeout", "2", "--out", str(out)])
    report = json.loads(out.read_text())
    assert code == 0, report["products"]
    [ha], [m] = stack.ha, stack.mosquitto
    assert ha.events == ["start", "onboard", ("ensure_mqtt", 18830), "stop"] and ha.port == 18123
    assert m.events == ["start", "stop"]
    s = report["summary"]
    assert s["passed"] == s["products"] == len(s["by_category"]) and s["products"] >= 5
    assert report["ha_version"] == "2026.10.2" and report["rejections"] == []
    assert f"{s['passed']}/{s['products']} products passed" in capsys.readouterr().out


def test_acceptance_stops_the_stack_when_home_assistant_fails(tmp_path, catalog, stack, capsys):
    StackHA.fail_start = True
    code = cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--out", str(tmp_path / "acc.json")])
    assert code == 1 and "FAILED acceptance: Home Assistant did not answer" in capsys.readouterr().err
    assert stack.ha[0].events == ["start", "stop"] and stack.mosquitto[0].events == ["start", "stop"]
    assert not (tmp_path / "acc.json").exists()


def test_ctrl_c_during_acceptance_still_stops_home_assistant_and_the_broker(monkeypatch, tmp_path, catalog, stack):
    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(StackHA, "onboard", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper")])
    assert stack.ha[0].events == ["start", "stop"] and stack.mosquitto[0].events == ["start", "stop"]


def test_a_sigterm_stops_the_services_like_ctrl_c(monkeypatch):
    import signal

    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installed.setdefault(sig, handler))
    cli._install_sigterm()
    with pytest.raises(SystemExit):
        installed[signal.SIGTERM](signal.SIGTERM, None)


def test_long_running_commands_install_the_sigterm_handler(monkeypatch, tmp_path, catalog, stack):
    calls = []
    monkeypatch.setattr(cli, "_install_sigterm", lambda: calls.append(1))
    StackHA.fail_start = True
    cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper")])
    assert calls == [1]


def test_a_broker_that_cannot_start_fails_the_roundtrip_cleanly(monkeypatch, tmp_path, mosquitto, capsys):
    raw = write_all(tmp_path / "raw")

    def cannot(self):
        self.events.append("start")
        raise stack_mod.StackError("nothing listens on 127.0.0.1:18830 after 10 s")

    monkeypatch.setattr(FakeMosquitto, "start", cannot)
    assert cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--root", str(tmp_path / ".vesper")]) == 1
    assert "FAILED roundtrip: nothing listens" in capsys.readouterr().err
    assert mosquitto[0].events == ["start", "stop"]


def test_acceptance_gives_up_when_home_assistant_creates_nothing(monkeypatch, tmp_path, catalog, stack, capsys):
    monkeypatch.setattr(StackHA, "state", lambda self, entity_id: None)
    code = cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--per-category", "1", "--timeout", "0.05", "--out", str(tmp_path / "acc.json")])
    assert code == 1 and "created no entity for the first 3 products" in capsys.readouterr().err
    assert stack.ha[0].events[-1] == "stop"


def test_the_bridge_leaves_an_offline_last_will(monkeypatch, tmp_path, broker, catalog):
    home = tmp_path / "home.yaml"
    home.write_text("devices:\n  - {product: 'zigbee:aqara:mccgq11lm', name: door}\n")
    monkeypatch.setattr(cli.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    cli.main(["twins", "run", str(home), "--catalog", str(catalog)])
    topic, payload = broker.wills["vesper-bridge"]
    assert topic == "zigbee2mqtt/bridge/state" and json.loads(payload) == {"state": "offline"}


def test_queued_messages_are_delivered_before_disconnecting(monkeypatch, tmp_path, broker, mosquitto, catalog):
    raw = write_all(tmp_path / "raw")
    assert cli.main(["validate", "roundtrip", "--raw-root", str(raw), "--external-broker",
                     "--out", str(tmp_path / "rt.json")]) == 0
    home = tmp_path / "home.yaml"
    home.write_text("devices:\n  - {product: 'zigbee:aqara:mccgq11lm', name: door}\n")
    monkeypatch.setattr(cli.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    cli.main(["twins", "run", str(home), "--catalog", str(catalog)])
    assert broker.unflushed == 0


def test_acceptance_retracts_the_twins_even_when_a_check_fails(monkeypatch, tmp_path, catalog, stack):
    def failing(*a, **k):
        raise stack_mod.StackError("Home Assistant answered 502")

    monkeypatch.setattr(acceptance_mod, "check_product", failing)
    code = cli.main(["validate", "acceptance", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--out", str(tmp_path / "acc.json")])
    assert code == 1 and StackHA.broker.retained == {}


def test_discovery_check_runs_home_assistants_schemas_over_the_catalog(monkeypatch, tmp_path, catalog, capsys):
    import vesper.validation.ha_schema as ha_schema_mod

    seen = {}

    def fake_check(messages, ha_python):
        seen["n"], seen["python"] = len(messages), ha_python
        return [{"topic": t, "component": "c", "platform": "sensor", "ok": i != 0, "error": None if i else "bad unit"}
                for i, (t, _) in enumerate(messages)]

    monkeypatch.setattr(ha_schema_mod, "check_with_ha", fake_check)
    out = tmp_path / "disc.json"
    code = cli.main(["validate", "discovery", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--out", str(out)])
    report = json.loads(out.read_text())
    assert code == 1 and seen["python"] == stack_mod.Paths(tmp_path / ".vesper").ha_python
    assert report["summary"]["components"] == seen["n"] and report["summary"]["errors"] == {"bad unit": 1}
    assert report["rejected"][0]["error"] == "bad unit"
    assert f"{seen['n'] - 1}/{seen['n']} components accepted" in capsys.readouterr().out


def test_rules_command_checks_every_twin_offline(tmp_path, catalog, capsys):
    out = tmp_path / "rules.json"
    code = cli.main(["validate", "rules", "--catalog", str(catalog), "--hours", "2", "--seed", "0", "--out", str(out)])
    report = json.loads(out.read_text())
    assert code == 0 and report["passed"] and report["reports"] > 0 and report["violation_count"] == 0
    assert "0 violations" in capsys.readouterr().out


def test_latency_command_calibrates_then_measures_each_path(monkeypatch, tmp_path, broker, mosquitto, catalog, capsys):
    import random

    import vesper.validation.timing as timing_mod


    calls = []

    def fake_measure(record, bridge_client, observer, *, n, path, overhead_s, spacing_s, timeout_s, seed):
        calls.append((path.name, overhead_s, n, timeout_s))
        rng = random.Random(seed)
        out = []
        for i in range(n):
            injected = max(0.0, path.sample(rng) - overhead_s)
            out.append({"twin": f"t{i}", "injected_s": injected, "observed_s": injected + 0.004})
        return out

    monkeypatch.setattr(timing_mod, "measure_latency", fake_measure)
    out = tmp_path / "lat.json"
    code = cli.main(["validate", "latency", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--paths", "local,vendor_cloud", "--out", str(out)])
    report = json.loads(out.read_text())
    assert [c[0] for c in calls] == ["calibration", "local", "vendor_cloud"] and calls[0][2] == 200
    assert calls[1][1] == pytest.approx(0.004)  # overhead = the median calibration residual
    assert calls[2][3] > calls[1][3]  # the slower path waits longer
    assert report["calibration"]["passed"] and report["overhead_s"] == pytest.approx(0.004)
    generator = report["generator"]
    assert set(generator) == {"local", "vendor_cloud"} and all(g["passed"] for g in generator.values())
    assert report["paths"]["local"]["residual"]["passed"] and "described" in report["paths"]["local"]
    assert code == 0 and report["passed"] and mosquitto[0].events == ["start", "stop"]
    assert report["run"]["parameters"]["paths"] == "local,vendor_cloud" and "git_commit" in report["run"]
    assert "local:" in capsys.readouterr().out


def test_a_failed_latency_run_still_leaves_a_result(monkeypatch, tmp_path, broker, mosquitto, catalog, capsys):
    import vesper.validation.timing as timing_mod

    def lost(*a, **k):
        raise TimeoutError("120 of 200 reports arrived within 360 s")

    monkeypatch.setattr(timing_mod, "measure_latency", lost)
    out = tmp_path / "lat.json"
    code = cli.main(["validate", "latency", "--catalog", str(catalog), "--root", str(tmp_path / ".vesper"),
                     "--out", str(out)])
    report = json.loads(out.read_text())
    assert code == 1 and report["passed"] is False and "120 of 200" in report["error"]
    assert "FAILED latency" in capsys.readouterr().err


def test_every_result_records_how_it_was_produced(tmp_path, catalog):
    out = tmp_path / "rules.json"
    cli.main(["validate", "rules", "--catalog", str(catalog), "--hours", "1", "--out", str(out)])
    run = json.loads(out.read_text())["run"]
    assert run["parameters"]["hours"] == 1.0 and run["parameters"]["seed"] == 0
    assert run["catalog_inputs"]["zigbee"]["version"] == "26.123.0" and "git_dirty" in run


def test_report_command_writes_markdown_and_json(tmp_path, catalog, capsys):
    (catalog / "coverage.json").write_text(json.dumps({"zigbee": {"L0": 1, "L1": 1, "L2": 0, "L3": 0, "L4": 0}}))
    out = tmp_path / "report"
    assert cli.main(["report", "--catalog", str(catalog), "--results", str(tmp_path / "none"),
                     "--out-dir", str(out)]) == 0
    [md] = out.glob("fidelity-*.md")
    [js] = out.glob("fidelity-*.json")
    assert "## Checks" in md.read_text() and json.loads(js.read_text())["checks"]["roundtrip"] is None
    assert "fidelity report" in capsys.readouterr().out


def test_a_stale_catalog_stops_every_command_that_runs_twins_with_exit_2(tmp_path, catalog, capsys):
    (catalog / "build_report.json").write_text(json.dumps({"inputs": {}}))
    assert cli.main(["validate", "rules", "--catalog", str(catalog), "--out", str(tmp_path / "r.json")]) == 2
    assert "stale" in capsys.readouterr().err and not (tmp_path / "r.json").exists()


def test_a_result_records_the_code_that_built_its_catalog(tmp_path, catalog):
    out = tmp_path / "rules.json"
    cli.main(["validate", "rules", "--catalog", str(catalog), "--hours", "1", "--out", str(out)])
    from vesper.catalog.fingerprint import code_fingerprint
    assert json.loads(out.read_text())["run"]["catalog_code_sha256"] == code_fingerprint()


def test_realdata_replays_a_dataset_and_records_where_it_came_from(monkeypatch, tmp_path, catalog, capsys):
    import vesper.validation.capture as capture_mod
    from tests.validation.ziotp_fixture import GOOD, tree
    from vesper.validation.capture import Capture
    root = tree(tmp_path, {"Idle/Topology_A/idle1.pcapng": GOOD})
    monkeypatch.setattr(capture_mod, "read_capture", lambda path, key, byte_order="Normal", tshark="tshark":
                        Capture(list(GOOD), secured=len(GOOD), decrypted=len(GOOD)))
    out = tmp_path / "realdata.json"
    code = cli.main(["validate", "realdata", "--dataset", str(root), "--catalog", str(catalog), "--out", str(out)])
    data = json.loads(out.read_text())
    assert code == 0 and data["passed"] and data["judged"] == 1
    assert data["dataset_commit"] is None  # not a git checkout of its own: no commit is claimed
    assert data["run"]["parameters"]["dataset"] == str(root)
    assert "1/1 judged streams reproduced" in capsys.readouterr().out


def test_realdata_without_tshark_or_data_fails_cleanly(tmp_path, catalog, capsys):
    code = cli.main(["validate", "realdata", "--dataset", str(tmp_path / "absent"), "--catalog", str(catalog),
                     "--out", str(tmp_path / "r.json")])
    assert code == 1 and "FAILED realdata" in capsys.readouterr().err and not (tmp_path / "r.json").exists()


def test_the_dataset_commit_is_the_checkouts_own_head_never_an_enclosing_repo(tmp_path):
    import subprocess
    outer = tmp_path / "outer"
    (outer / "data").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    subprocess.run(["git", "-C", str(outer), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    assert cli._dataset_commit(outer / "data") is None
    assert len(cli._dataset_commit(outer)) == 40
