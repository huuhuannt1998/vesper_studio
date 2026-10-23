import threading

import pytest

from tests.exposure.fake_mqtt import FakeClient
from vesper.exposure.ha_discovery import event_topic
from vesper.exposure.z2m_bridge import Bridge, Placement, definition, flatten, ieee_address
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime


def bridge(vds, *models):
    rt = Runtime(clock=LogicalClock())
    placements = [Placement(rt.add(vds[m]).id, m.lower()) for m in models]
    client = FakeClient()
    b = Bridge(rt, placements, client, zhc_version="26.123.0")
    b.start()
    return b, rt, client


def test_start_publishes_retained_bridge_topics_with_the_real_definitions(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM", "PLUG-1")
    assert client.last("zigbee2mqtt/bridge/state") == {"state": "online"}
    assert client.retained("zigbee2mqtt/bridge/devices") is True
    coordinator, door, plug = client.last("zigbee2mqtt/bridge/devices")
    assert coordinator["type"] == "Coordinator" and door["type"] == "EndDevice" and plug["type"] == "Router"
    assert door["definition"] == definition(vds["MCCGQ11LM"])
    assert door["definition"]["exposes"] == vds["MCCGQ11LM"]["binding"]["zigbee"]["exposes"]
    assert door["model_id"] == "lumi.sensor_magnet.aq2" and door["ieee_address"] == ieee_address(door_id(rt))
    assert client.last("zigbee2mqtt/mccgq11lm") == {"battery": 0, "contact": True}
    assert client.last("zigbee2mqtt/mccgq11lm/availability") == {"state": "online"}
    assert client.last(f"homeassistant/device/{ieee_address(door_id(rt))}/config")["cmps"]
    assert set(client.subscriptions) == {"zigbee2mqtt/#", "vesper/control/#"}


def door_id(rt):
    return next(t for t in rt.twins if "mccgq11lm" in t)


def test_set_json_and_set_attribute_reach_the_twin_and_republish_state(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    client.deliver("zigbee2mqtt/plug-1/set", {"state": "ON"})
    assert client.last("zigbee2mqtt/plug-1")["state"] == "ON"
    client.deliver("zigbee2mqtt/plug-1/set/state", "OFF")
    assert client.last("zigbee2mqtt/plug-1")["state"] == "OFF"
    assert [a.actor for a in rt.actions] == ["platform", "platform"]


def test_a_rejected_command_is_recorded_and_state_unchanged(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM")
    client.deliver("zigbee2mqtt/mccgq11lm/set", {"contact": False})
    assert b.rejections[-1]["feature"] == "contact" and "read-only" in b.rejections[-1]["reason"]
    assert client.last("zigbee2mqtt/mccgq11lm")["contact"] is True
    client.deliver("zigbee2mqtt/mccgq11lm/set", "not json")
    assert b.rejections[-1]["reason"] == "payload is not a JSON object"


def test_nested_light_commands_and_color_mode(vds):
    b, rt, client = bridge(vds, "LED1545G12")
    client.deliver("zigbee2mqtt/led1545g12/set", {"state": "ON", "brightness": 128, "color": {"x": 0.3, "y": 0.4}})
    state = client.last("zigbee2mqtt/led1545g12")
    assert (state["state"], state["brightness"], state["color"], state["color_mode"]) == ("ON", 128,
                                                                                        {"x": 0.3, "y": 0.4}, "xy")


def test_get_republishes_and_unknown_devices_are_ignored(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    before = len(client.published)
    client.deliver("zigbee2mqtt/plug-1/get", {"state": ""})
    client.deliver("zigbee2mqtt/someone-else/set", {"state": "ON"})
    assert [t for t, _, _ in client.published[before:]] == ["zigbee2mqtt/plug-1"]


def test_button_events_go_to_their_topic_and_are_not_retained_state(vds):
    b, rt, client = bridge(vds, "BTN-1")
    client.deliver("vesper/control/stimulate",
                   {"device": "btn-1", "stimulus": {"type": "button_press", "action": "hold"}})
    topic = event_topic("zigbee2mqtt/btn-1", "action")
    assert client.last(topic) == {"event_type": "hold"} and client.retained(topic) is False
    assert "action" not in client.last("zigbee2mqtt/btn-1")


def test_control_channel_stimulate_operate_and_bad_messages(vds):
    b, rt, client = bridge(vds, "MCCGQ11LM", "PLUG-1")
    client.deliver("vesper/control/stimulate",
                   {"device": "mccgq11lm", "stimulus": {"type": "contact", "state": "opened"}})
    assert client.last("zigbee2mqtt/mccgq11lm")["contact"] is False
    client.deliver("vesper/control/operate", {"device": "plug-1", "feature": "state", "value": "ON", "actor": "bob"})
    assert rt.actions[-1].actor == "human:bob" and client.last("zigbee2mqtt/plug-1")["state"] == "ON"
    client.deliver("vesper/control/stimulate", {"device": "nobody"})
    assert b.rejections[-1]["reason"] == "bad control message"


def test_bridge_requests_get_an_explicit_error_and_stop_goes_offline(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    client.deliver("zigbee2mqtt/bridge/request/permit_join", {"value": True})
    assert client.last("zigbee2mqtt/bridge/response/permit_join")["status"] == "error"
    b.stop()
    assert client.last("zigbee2mqtt/bridge/state") == {"state": "offline"}
    assert client.last("zigbee2mqtt/plug-1/availability") == {"state": "offline"}


def test_placements_must_be_zigbee_twins_with_unique_names(vds):
    rt = Runtime(clock=LogicalClock())
    a, b = rt.add(vds["PLUG-1"]).id, rt.add(vds["MCCGQ11LM"]).id
    with pytest.raises(ValueError, match="duplicate friendly name"):
        Bridge(rt, [Placement(a, "x"), Placement(b, "x")], FakeClient())
    matter = dict(vds["PLUG-1"], id="matter:1:1", binding={"matter": {}})
    rt.add(matter)
    with pytest.raises(ValueError, match="no Zigbee binding"):
        Bridge(rt, [Placement("matter:1:1", "m")], FakeClient())


@pytest.mark.parametrize("name", ["", "a+b", "hall/#", "/hall", "hall/", "hall//door", "bridge/x", "door/set",
                                  "door/get", "door/availability"])
def test_a_friendly_name_that_would_break_the_topic_tree_is_refused(vds, name):
    rt = Runtime(clock=LogicalClock())
    p = Placement(rt.add(vds["PLUG-1"]).id, name)
    with pytest.raises(ValueError, match="friendly name"):
        Bridge(rt, [p], FakeClient())


def test_commands_on_the_network_thread_and_reports_on_the_clock_thread_do_not_deadlock(vds):
    b, rt, client = bridge(vds, "PLUG-1")
    twin_id = b.placements[0].twin_id

    def commands():
        for i in range(300):
            client.deliver("zigbee2mqtt/plug-1/set", {"state": "ON" if i % 2 else "OFF"})

    def reports():
        for i in range(300):
            rt.stimulate(twin_id, {"type": "load", "watts": i})

    threads = [threading.Thread(target=commands), threading.Thread(target=reports)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "bridge and runtime deadlocked"
    assert rt.faulted == {} and b.rejections == []


def test_flatten():
    assert flatten({"state": "ON", "color": {"x": 1, "y": 2}}) == [("state", "ON"), ("color.x", 1), ("color.y", 2)]
