"""Discovery messages checked by Home Assistant's own schemas (run inside VESPER's HA venv, no server)."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.twins.conftest import records
from vesper.exposure.ha_discovery import device_discovery
from vesper.exposure.z2m_bridge import ieee_address
from vesper.validation.ha_schema import SCRIPT, check_with_ha, discovery_messages, summarize_schema
from vesper.validation.stack import Paths, StackError

HA_PYTHON = Paths(Path(__file__).resolve().parents[2] / ".vesper").ha_python


def test_messages_go_to_home_assistants_python_and_results_come_back(tmp_path):
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs["input"]
        out = json.dumps({"topic": "t", "component": "c", "platform": "switch", "ok": True, "error": None})
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")

    results = check_with_ha([("t", {"cmps": {}})], tmp_path / "python", run=run)
    assert seen["cmd"] == [str(tmp_path / "python"), "-I", str(SCRIPT)]
    assert json.loads(seen["input"].splitlines()[0]) == {"topic": "t", "payload": {"cmps": {}}}
    assert results == [{"topic": "t", "component": "c", "platform": "switch", "ok": True, "error": None}]


def test_a_failing_checker_is_an_error_not_an_empty_result(tmp_path):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ModuleNotFoundError: homeassistant")

    with pytest.raises(StackError, match="homeassistant"):
        check_with_ha([("t", {})], tmp_path / "python", run=run)


def test_every_l1_zigbee_record_yields_its_discovery_message():
    recs = list(records().values())
    messages = discovery_messages(recs)
    assert len(messages) == sum(1 for r in recs if r["fidelity"]["reached"]["L1"])
    r = next(r for r in recs if r["fidelity"]["reached"]["L1"])
    topic, payload, _, _ = device_discovery(r, ieee=ieee_address(r["id"]), friendly_name=r["id"])
    assert (topic, payload) in messages


def test_schema_results_are_counted_and_errors_grouped():
    results = [{"topic": "a", "component": "x", "platform": "sensor", "ok": True, "error": None},
               {"topic": "a", "component": "y", "platform": "sensor", "ok": False, "error": "bad unit"},
               {"topic": "b", "component": "z", "platform": "sensor", "ok": False, "error": "bad unit"}]
    s = summarize_schema(results)
    assert s["components"] == 3 and s["accepted"] == 1 and s["devices_fully_accepted"] == 0
    assert s["errors"] == {"bad unit": 2}


live = pytest.mark.skipif(os.environ.get("VESPER_HA_SOURCE_TESTS") != "1" or not HA_PYTHON.exists(),
                          reason="set VESPER_HA_SOURCE_TESTS=1 (needs `vesper setup ha`)")


@live
def test_home_assistant_accepts_every_generated_component():
    results = check_with_ha(discovery_messages(list(records().values())), HA_PYTHON)
    assert results and [r for r in results if not r["ok"]] == []


@live
def test_home_assistant_rejects_a_unit_its_device_class_does_not_allow():
    payload = {"dev": {"ids": ["x"], "name": "x"}, "o": {"name": "VESPER"}, "state_topic": "z/x",
               "cmps": {"t": {"p": "sensor", "unique_id": "vx_t", "device_class": "temperature",
                              "unit_of_measurement": "furlongs", "value_template": "{{ value_json.t }}"}}}
    [result] = check_with_ha([("homeassistant/device/0x1/config", payload)], HA_PYTHON)
    assert result["ok"] is False and "unit" in result["error"]
