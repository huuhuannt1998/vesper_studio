"""A virtual Tasmota device on MQTT (security-lab spec §3.3, plan W7a2): its own connection, its discovery as Home
Assistant's Tasmota integration (HATasmota) reads it, its last will, and Tasmota's command and state topics."""
import json

from tests.catalog.fixtures import TASMOTA_PIN, TASMOTA_TEMPLATES, tasmota_tarball
from tests.exposure.fake_mqtt import FakeClient
from vesper.catalog.normalize.tasmota import tasmota_records
from vesper.exposure.tasmota import DISCOVERY_PREFIX, TasmotaDevice, expected_entities, mac_of, topic_of
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime

RECORDS = {r["identity"]["ids"]["tasmota_template"][0]: r for r in tasmota_records(
    tasmota_tarball(TASMOTA_TEMPLATES), TASMOTA_PIN)[0]}


def device(name="acme_plug_p1", friendly="kitchen_kettle", **kw):
    clock = LogicalClock()
    rt = Runtime(clock=clock)
    twin = rt.add(RECORDS[name])
    client = FakeClient()
    d = TasmotaDevice(rt, twin.id, friendly, client, **kw)
    d.start()
    return d, rt, client, clock


def test_ids_are_stable_and_shaped_as_tasmota_s():
    mac = mac_of("tasmota:acme:acme_plug_p1@kitchen_kettle")
    assert mac == mac_of("tasmota:acme:acme_plug_p1@kitchen_kettle") and len(mac) == 12 and mac == mac.upper()
    assert int(mac[:2], 16) & 0x02  # a locally administered address: never a real device's
    assert topic_of(mac) == f"tasmota_{mac[-6:]}"


def test_start_says_online_and_announces_the_device_as_hatasmota_reads_it():
    d, rt, client, _ = device()
    t, mac = d.topic, d.mac
    lwt = [(top, p, r) for top, p, r in client.raw if top == f"tele/{t}/LWT"]
    assert lwt[-1] == (f"tele/{t}/LWT", "Online", True)
    cfg = client.last(f"{DISCOVERY_PREFIX}/{mac}/config")
    assert client.retained(f"{DISCOVERY_PREFIX}/{mac}/config") is True
    assert (cfg["dn"], cfg["t"], cfg["mac"], cfg["md"], cfg["ver"]) == ("kitchen_kettle", t, mac, "P1", 1)
    assert cfg["ft"] == "%prefix%/%topic%/" and cfg["tp"] == ["cmnd", "stat", "tele"]
    assert (cfg["ofln"], cfg["onln"], cfg["state"]) == ("Offline", "Online", ["OFF", "ON", "TOGGLE", "HOLD"])
    assert cfg["rl"][:2] == [1, 0] and len(cfg["rl"]) == 8 and cfg["lt_st"] == 0 and cfg["ty"] == 0
    assert set(cfg["so"]) >= {"4", "11", "13", "17", "20", "30", "68", "73", "82", "114", "117"}
    for key in ("ip", "hn", "sw", "fn", "swc", "btn", "lk"):
        assert key in cfg
    assert client.last(f"{DISCOVERY_PREFIX}/{mac}/sensors")["ver"] == 1


def test_a_power_command_switches_the_twin_and_the_result_is_published():
    d, rt, client, _ = device()
    client.deliver(f"cmnd/{d.topic}/POWER", "ON")
    assert rt.state(d.twin_id)["POWER"] == "ON"
    assert client.last(f"stat/{d.topic}/RESULT") == {"POWER": "ON"}
    assert [p for top, p, _ in client.raw if top == f"stat/{d.topic}/POWER"][-1] == "ON"
    client.deliver(f"cmnd/{d.topic}/POWER", "TOGGLE")
    assert rt.state(d.twin_id)["POWER"] == "OFF"


def test_an_empty_power_command_asks_the_state_without_changing_it():
    d, rt, client, _ = device()
    client.deliver(f"cmnd/{d.topic}/POWER", "")
    assert rt.state(d.twin_id)["POWER"] == "OFF" and client.last(f"stat/{d.topic}/RESULT") == {"POWER": "OFF"}


def test_a_strip_s_relays_are_power1_to_power4_and_announced_as_four_relays():
    d, rt, client, _ = device("acme_strip_s4", "desk_strip")
    assert client.last(f"{DISCOVERY_PREFIX}/{d.mac}/config")["rl"][:5] == [1, 1, 1, 1, 0]
    client.deliver(f"cmnd/{d.topic}/POWER3", "ON")
    assert rt.state(d.twin_id)["POWER3"] == "ON" and client.last(f"stat/{d.topic}/RESULT") == {"POWER3": "ON"}


def test_a_dimmer_is_announced_as_a_light_and_takes_dimmer_commands():
    d, rt, client, _ = device("brite_dimmer_d1", "hall_light")
    cfg = client.last(f"{DISCOVERY_PREFIX}/{d.mac}/config")
    assert cfg["rl"][0] == 2 and cfg["lt_st"] == 1
    client.deliver(f"cmnd/{d.topic}/Dimmer", "40")
    assert rt.state(d.twin_id)["Dimmer"] == 40 and rt.state(d.twin_id)["POWER"] == "ON"  # as Tasmota: dimming lights it
    assert client.last(f"stat/{d.topic}/RESULT") == {"POWER": "ON", "Dimmer": 40}


def test_home_assistant_s_state_poll_gets_the_whole_state():
    d, rt, client, _ = device()
    client.deliver(f"cmnd/{d.topic}/STATE", "")
    state = client.last(f"tele/{d.topic}/STATE")
    assert state["POWER"] == "OFF" and "Time" in state and "Wifi" in state
    client.deliver(f"cmnd/{d.topic}/STATUS", "11")
    assert client.last(f"stat/{d.topic}/STATUS11")["StatusSTS"]["POWER"] == "OFF"


def test_the_whole_state_is_published_every_tele_period():
    d, rt, client, clock = device()
    n = len([1 for top, _, _ in client.raw if top == f"tele/{d.topic}/STATE"])
    clock.advance(301)
    assert len([1 for top, _, _ in client.raw if top == f"tele/{d.topic}/STATE"]) == n + 1


def test_a_hand_on_the_device_is_reported_as_the_device_reports_it():
    d, rt, client, _ = device()
    rt.operate(d.twin_id, "POWER", "ON", actor="resident")
    assert client.last(f"stat/{d.topic}/RESULT") == {"POWER": "ON"}


def test_the_device_s_reports_can_come_through_its_link_and_commands_go_down_it():
    sent, heard = [], []

    class Link:
        def subscribe(self, fn):
            heard.append(fn)
    d, rt, client, _ = device(events=Link(), send=lambda name, feature, run: sent.append((name, feature, run)))
    assert heard  # the device hears its reports from the link
    client.deliver(f"cmnd/{d.topic}/POWER", "ON")
    assert rt.state(d.twin_id)["POWER"] == "OFF"
    (name, features, run), = sent
    assert name == "kitchen_kettle" and features == ("POWER",)
    run()
    assert rt.state(d.twin_id)["POWER"] == "ON"


def test_stop_says_offline():
    d, rt, client, _ = device()
    d.stop()
    assert [(top, p) for top, p, _ in client.raw if top == f"tele/{d.topic}/LWT"][-1] == (f"tele/{d.topic}/LWT",
                                                                                            "Offline")


def test_the_will_is_offline_and_retained():
    assert TasmotaDevice.will("tasmota_ABCDEF") == ("tele/tasmota_ABCDEF/LWT", "Offline")


def test_a_malformed_command_is_refused_without_stopping_the_device():
    d, rt, client, _ = device("brite_dimmer_d1", "hall_light")
    client.deliver(f"cmnd/{d.topic}/Dimmer", "bright")
    client.deliver(f"cmnd/{d.topic}/POWER7", "ON")
    assert rt.state(d.twin_id)["POWER"] == "OFF"
    assert [r["command"] for r in d.rejections] == ["Dimmer", "POWER7"]
    assert json.dumps(d.rejections)


def test_power1_is_power_on_a_one_relay_device_as_home_assistant_sends_it():
    """HATasmota commands relay n as POWER<n>; on a device with one relay Tasmota takes POWER1 for POWER, and its
    reports keep saying POWER."""
    d, rt, client, _ = device()
    client.deliver(f"cmnd/{d.topic}/POWER1", "ON")
    assert rt.state(d.twin_id)["POWER"] == "ON" and client.last(f"stat/{d.topic}/RESULT") == {"POWER": "ON"}
    client.deliver(f"cmnd/{d.topic}/power1", "OFF")  # Tasmota's commands are case-insensitive
    assert rt.state(d.twin_id)["POWER"] == "OFF"


def test_a_backlog_runs_its_commands_in_order_as_one_command_across_the_link():
    """HATasmota sends a light's commands as one Backlog of `NoDelay;<command> <value>`; transitions (Fade, Speed)
    are taken and not modelled: the light changes at once."""
    sent = []
    d, rt, client, _ = device("brite_dimmer_d1", "hall_light",
                              send=lambda name, features, run: (sent.append((name, features)), run()))
    client.deliver(f"cmnd/{d.topic}/Backlog", "NoDelay;Fade 0;NoDelay;Dimmer 40")
    assert rt.state(d.twin_id)["Dimmer"] == 40 and rt.state(d.twin_id)["POWER"] == "ON"
    assert sent == [("hall_light", ("Dimmer", "POWER"))]
    assert client.last(f"stat/{d.topic}/RESULT") == {"POWER": "ON", "Dimmer": 40}
    client.deliver(f"cmnd/{d.topic}/Backlog", "NoDelay;POWER1 OFF")
    assert rt.state(d.twin_id)["POWER"] == "OFF" and not d.rejections


def test_a_backlog_with_an_unknown_command_runs_nothing_and_says_why():
    d, rt, client, _ = device("brite_dimmer_d1", "hall_light")
    client.deliver(f"cmnd/{d.topic}/Backlog", "NoDelay;Dimmer 40;NoDelay;CT 300")
    assert rt.state(d.twin_id)["Dimmer"] != 40
    assert d.rejections[-1]["command"] == "Backlog" and "CT" in d.rejections[-1]["reason"]


def test_identify_names_the_device_and_the_features_a_message_carries():
    """What the relay asks, to find a Tasmota device's messages by the device's name and its features."""
    d, *_ = device("brite_dimmer_d1", "hall_light")
    t = d.topic
    assert d.identify(["cmnd", t, "POWER1"], b"ON") == ("hall_light", ("POWER",))
    assert d.identify(["cmnd", t, "Dimmer"], b"40") == ("hall_light", ("Dimmer",))
    assert d.identify(["cmnd", t, "Backlog"], b"NoDelay;Fade 0;NoDelay;Dimmer 40") == ("hall_light", ("Dimmer",))
    assert d.identify(["stat", t, "RESULT"], b'{"POWER": "ON", "Dimmer": 40}') == ("hall_light", ("POWER", "Dimmer"))
    assert d.identify(["tele", t, "STATE"], b'{"Time": "x", "POWER": "ON", "Dimmer": 4, "Wifi": {}}') == (
        "hall_light", ("POWER", "Dimmer"))
    assert d.identify(["tele", t, "LWT"], b"Online") == ("hall_light", ())


def test_the_entities_home_assistant_creates_are_named_after_the_device():
    """HATasmota names relay 1 after the device (its friendly name is the device's) and relay n `<device> switch n`;
    Home Assistant's entity ids follow. Automations name these."""
    def ids(name, friendly):
        return [(e.entity_id, e.platform, e.feature) for e in expected_entities(RECORDS[name], friendly)]
    assert ids("acme_plug_p1", "kitchen_kettle") == [("switch.kitchen_kettle", "switch", "POWER")]
    assert ids("acme_strip_s4", "desk_strip") == [("switch.desk_strip", "switch", "POWER1")] + [
        (f"switch.desk_strip_switch_{n}", "switch", f"POWER{n}") for n in (2, 3, 4)]
    assert ids("brite_dimmer_d1", "hall_light") == [("light.hall_light", "light", "POWER")]
