import json

import pytest

from tests.catalog.fixtures import ZIGBEE_DOC
from tests.exposure.fake_mqtt import FakeBroker, FakeClient
from vesper.catalog.normalize.zigbee import zigbee_records
from vesper.exposure.z2m_bridge import Bridge, Placement
from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import Runtime
from vesper.validation.roundtrip import compare, diff_paths, receive_retained, source_definitions


def published(records):
    rt = Runtime(clock=LogicalClock())
    placements = [Placement(rt.add(r).id, f"d{i}") for i, r in enumerate(records)]
    client = FakeClient()
    Bridge(rt, placements, client, discovery=False).start()
    devices = json.loads(json.dumps(client.last("zigbee2mqtt/bridge/devices")))  # through JSON, as over MQTT
    return devices, {p.friendly_name: p.twin_id for p in placements}


def test_every_published_definition_equals_its_source_record():
    records = zigbee_records(ZIGBEE_DOC)
    devices, twin_of = published(records)
    report = compare(devices, twin_of, source_definitions(ZIGBEE_DOC))
    assert report == {"checked": len(records), "identical": len(records), "different": {}, "unmatched": []}


def test_a_normalization_change_shows_up_as_a_diff():
    records = zigbee_records(ZIGBEE_DOC)
    records[0]["binding"]["zigbee"]["exposes"][0]["unit"] = "percent"
    devices, twin_of = published(records)
    report = compare(devices, twin_of, source_definitions(ZIGBEE_DOC))
    assert report["identical"] == len(records) - 1
    assert report["different"][records[0]["id"]] == ["/exposes[0]/unit"]


def test_diff_paths_distinguishes_types_lengths_and_keys():
    assert diff_paths({"a": 1}, {"a": 1.0}) == ["/a"]
    assert diff_paths([1, 2], [1]) == ["[len 2 != 1]"]
    assert diff_paths({"a": 1}, {"b": 1}) == ["/a", "/b"]
    assert diff_paths({"a": [{"b": True}]}, {"a": [{"b": True}]}) == []


def test_receive_retained_reads_the_message_then_stops_listening():
    broker = FakeBroker()
    broker.client().publish("z/bridge/devices", json.dumps([{"ieee_address": "0x1"}]), retain=True)
    checker = broker.client()
    assert receive_retained(checker, "z/bridge/devices", timeout=1) == [{"ieee_address": "0x1"}]
    broker.client().publish("z/bridge/devices", "", retain=True)  # the bridge retracting: no error
    assert checker.subscriptions == [] and checker.on_message is None


def test_receive_retained_times_out_without_a_retained_message():
    with pytest.raises(TimeoutError, match="z/bridge/devices"):
        receive_retained(FakeBroker().client(), "z/bridge/devices", timeout=0.05)
