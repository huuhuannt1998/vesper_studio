"""A session on the home's network (security-lab spec §3, plan W7a1): Home Assistant (here VESPER's automation
engine) hears a report only once it has crossed the device's links, an attack's effect on a link is an input a
replay puts on again, and the trace keeps both what the device sent and what the network did to it."""
import pytest

from tests.lab.test_automations import HALL_LAMP, fired, lab, lamp_on
from vesper.lab.linklayer import Effect
from vesper.lab.network import NetworkError
from vesper.lab.session import compare_reports, replay


def hall_lamp(tmp_path):
    home, ids, s = lab(tmp_path, "")
    (tmp_path / "automations.yaml").write_text(HALL_LAMP.format(motion=ids["hall_motion"]["occupancy"],
                                                                lamp=ids["bedroom_lamp"]["state"]))
    s.start(spawn_player=False, automations=True)
    s.spawn("player", "female_0", (5.0, 1.0))  # in the kitchen
    return home, s


def first(records, **match):
    return next(r for r in records if all(r.get(k) == v for k, v in match.items()))


def test_the_automation_acts_once_the_motion_report_has_crossed_its_hop_and_its_command_crossed_back(tmp_path):
    _, s = hall_lamp(tmp_path)
    s.walk_to((1.5, 3.0))
    s.clock.advance(10)
    sent = first(s.records, kind="report", device="hall_motion", feature="occupancy", value=True)["t"]
    acted = fired(s)[0]["t"]
    lamp = first(s.records, kind="report", device="bedroom_lamp", feature="state", value="ON")["t"]
    # up the motion sensor's hop and down the lamp's: tens of milliseconds; the lamp reports its new state the
    # instant the command arrives (that report's own hop delays its listeners, not the trace)
    assert sent < acted == lamp < sent + 0.2


def test_a_delayed_motion_report_makes_the_automation_late_by_the_hold_and_the_trace_says_why(tmp_path):
    _, s = hall_lamp(tmp_path)
    s.network_effect(Effect("delay", link="coordinator", direction="up", device="hall_motion",
                            feature="occupancy", hold_s=30))
    s.walk_to((1.5, 3.0))
    s.clock.advance(20)
    assert not lamp_on(s)  # the player is in the hall; Home Assistant has not heard it yet
    s.clock.advance(20)
    assert lamp_on(s)
    sent = first(s.records, kind="report", device="hall_motion", feature="occupancy", value=True)["t"]
    held = first(s.records, kind="net", event="held", device="hall_motion")
    assert held["link"] == "coordinator" and held["sent_t"] == sent and held["deliver_t"] - sent >= 30
    assert fired(s)[0]["t"] >= sent + 30
    effect = first(s.records, kind="input", op="effect")
    assert effect["actor"] == "lab" and effect["args"]["hold_s"] == 30


def test_a_cut_coordinator_link_leaves_the_automation_deaf_and_a_replay_cuts_it_again(tmp_path):
    home, s = hall_lamp(tmp_path)
    s.network_effect(Effect("cut", link="coordinator"))
    s.walk_to((1.5, 3.0))
    s.clock.advance(60)
    assert not lamp_on(s) and fired(s) == []
    s.network_clear("coordinator")
    s.clock.advance(1)
    s.stop()
    again = replay(s.records, home)
    assert compare_reports(s.records, again) is None
    down = [r for r in again if r["kind"] == "net" and r["event"] in ("down", "up")]
    assert [(r["link"], r["event"]) for r in down] == [("coordinator", "down"), ("coordinator", "up")]


def test_shutting_a_door_changes_what_a_hop_crosses(tmp_path):
    _, s = hall_lamp(tmp_path)
    before = s.links.quality("zigbee/bedroom_lamp")
    s.world.set_door("bedroom-hall", False)
    after = s.links.quality("zigbee/bedroom_lamp")
    assert after.rssi_dbm <= before.rssi_dbm


def test_an_effect_a_home_cannot_take_is_refused_and_not_traced(tmp_path):
    _, s = hall_lamp(tmp_path)
    with pytest.raises(NetworkError, match="unknown link"):
        s.network_effect(Effect("delay", link="wifi/sofa", hold_s=1))
    assert not [r for r in s.records if r.get("op") == "effect"]


def test_on_the_live_network_a_tcp_link_s_effect_goes_to_the_relay_and_a_hop_s_to_the_link_layer(tmp_path):
    _, s = hall_lamp(tmp_path)
    put = []

    class Relay:
        def apply(self, effect):
            put.append(effect)

        def clear(self, link=None):
            put.append(("clear", link))
    s.relay = Relay()
    s.network_effect(Effect("delay", link="coordinator", hold_s=5))
    s.network_effect(Effect("delay", link="zigbee/hall_motion", hold_s=5))
    assert put == [Effect("delay", link="coordinator", hold_s=5)]
    assert s.links.effects == [Effect("delay", link="zigbee/hall_motion", hold_s=5)]
    with pytest.raises(NetworkError, match="'jam' is not one of"):
        s.network_effect(Effect("jam", link="coordinator"))
    s.network_clear()
    assert put[-1] == ("clear", None) and s.links.effects == []


def test_records_from_another_thread_reach_the_trace_on_the_session_s_clock(tmp_path):
    _, s = hall_lamp(tmp_path)
    s.net_record({"kind": "net", "link": "coordinator", "event": "down", "reason": "cut"})
    assert not [r for r in s.records if r.get("reason") == "cut"]  # not yet: on the clock's next turn
    s.clock.advance(0)
    assert [r for r in s.records if r.get("reason") == "cut"][0]["t"] == s.clock.now()
