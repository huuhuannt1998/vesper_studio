"""The defenses at Home Assistant's edge in a live run (plan W4a1): a client on the relay's hub link hears exactly
what Home Assistant hears; freshness reads Zigbee2MQTT's `last_seen`, physical consistency what reached the hub."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from tests.exposure.fake_mqtt import FakeClient
from tests.lab.homes import catalog, write_home
from tests.lab.test_scenario import LOCKED_HOME
from vesper.lab.home import load_lab_home
from vesper.lab.scenario import Defense
from vesper.lab.scenario_run import HubWatch, LiveFreshness, PhysicalConsistency
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def watched(tmp_path):
    session = Session(load_lab_home(write_home(tmp_path, LOCKED_HOME), catalog()), clock=LogicalClock())
    session.start(spawn_player=False)
    client = FakeClient()
    return session, client, HubWatch(session, client)


def message(client, topic, payload, retain=False):
    client.on_message(client, None, SimpleNamespace(topic=topic, payload=json.dumps(payload).encode(), retain=retain))


def test_a_report_older_than_the_bound_when_it_reaches_the_hub_raises_an_alert(tmp_path):
    session, client, watch = watched(tmp_path)
    LiveFreshness(session, Defense("freshness", max_age_s=10), watch, now=lambda: 1000.0)
    assert client.subscriptions == ["zigbee2mqtt/+"]
    message(client, "zigbee2mqtt/front_door", {"contact": True, "last_seen": iso(995.0)})  # 5 s: fresh
    message(client, "zigbee2mqtt/front_door", {"contact": False, "last_seen": iso(970.0)})  # 30 s: stale
    message(client, "zigbee2mqtt/front_door", {"contact": False, "last_seen": iso(900.0)}, retain=True)
    message(client, "zigbee2mqtt/bridge", {"state": "online"})
    session.clock.advance(0)
    alerts = [r for r in session.records if r["kind"] == "alert"]
    assert [(a["defense"], a["device"], a["age_s"]) for a in alerts] == [("freshness", "front_door", 30.0)]


def test_physical_consistency_can_hear_the_hub_through_the_watch(tmp_path):
    session, client, watch = watched(tmp_path)
    PhysicalConsistency(session, Defense("physical_consistency", within_s=5), watch=watch)
    session.world.set_door("hall-porch", True)  # the door opens in the world
    session.clock.advance(2)
    message(client, "zigbee2mqtt/front_door", {"contact": False})  # the hub hears it in time
    session.clock.advance(10)
    assert not [r for r in session.records if r["kind"] == "alert"]
    session.world.set_door("hall-porch", False)  # and closes; the hub never hears it
    session.clock.advance(10)
    (alert,) = [r for r in session.records if r["kind"] == "alert"]
    assert (alert["device"], alert["open"]) == ("front_door", False)


def test_the_watch_records_what_home_assistant_heard_as_heard_events(tmp_path):
    from vesper.lab.predicates import evaluate
    session, client, watch = watched(tmp_path)
    message(client, "zigbee2mqtt/front_door", {"contact": False, "battery": 90})
    message(client, "zigbee2mqtt/front_door", {"contact": True}, retain=True)  # old news
    session.clock.advance(0)
    heard = [r for r in session.records if r["kind"] == "heard"]
    assert [(r["device"], r["values"]) for r in heard] == [("front_door", {"contact": False, "battery": 90})]
    assert evaluate({"heard": {"device": "front_door", "feature": "contact", "value": False}}, session.records).holds
    assert not evaluate({"heard": {"device": "front_door", "feature": "contact", "value": True}},
                        session.records).holds
