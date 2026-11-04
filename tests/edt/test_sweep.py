"""vesper.edt.sweep: a tiny corpus swept across a few environments (assumed clear sky: no weather is fetched)."""
import json
import textwrap
from pathlib import Path

import pytest

from vesper.edt.corpus import automation_hash
from vesper.edt.sweep import is_negative_control, sweep
from vesper.edt.types import Env

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")

STO, PHX = "Stockholm, Sweden", "Phoenix, AZ, USA"
ENVS = [Env(STO, "2026-01-15", 0.0), Env(STO, "2026-07-15", 0.0), Env(PHX, "2026-01-15", 0.0),
        Env(PHX, "2026-07-15", 0.0)]

LAMP = {"alias": "dark living room", "trigger": [{"platform": "numeric_state", "entity_id": "sensor.living_room_lux",
                                                  "below": 50}],
        "action": [{"service": "light.turn_on", "entity_id": "light.living_room"}]}
SUNSET = {"alias": "dusk", "trigger": [{"platform": "sun", "event": "sunset"}],
          "action": [{"service": "light.turn_on", "entity_id": "light.kitchen"}]}
MOTION = {"alias": "motion light", "trigger": [{"platform": "state", "entity_id": "binary_sensor.kitchen_motion",
                                                "to": "on"}],
          "action": [{"service": "light.turn_on", "entity_id": "light.kitchen"}]}
MORNING = {"alias": "morning", "trigger": [{"platform": "time", "at": "07:00:00"}],
           "condition": [{"condition": "state", "entity_id": "lock.front_door", "state": "unlocked"}],
           "action": [{"service": "light.turn_on", "entity_id": "light.bedroom"},
                      {"delay": "00:30:00"}, {"service": "light.turn_off", "entity_id": "light.bedroom"}]}
RAINY = {"alias": "rain", "trigger": [{"platform": "state", "entity_id": "weather.home", "to": "rainy"}],
         "action": [{"service": "cover.close_cover", "entity_id": "cover.living_room"}]}


def _put(raw, name, autos):
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(textwrap.dedent(json.dumps(autos)))  # JSON is YAML


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    raw = tmp_path_factory.mktemp("corpus") / "raw"
    _put(raw, "alice~home##automations.yaml", [LAMP, SUNSET])
    _put(raw, "bob~ha##automations.yaml", [MOTION, MORNING])
    _put(raw, "carol~cfg##automations.yaml", [RAINY, LAMP])  # LAMP again: one automation, counted once
    return raw.parent


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return tmp_path_factory.mktemp("work")


@pytest.fixture(scope="module")
def result(corpus, work):
    return sweep(corpus, work, ENVS, rung=4, workers=1, limit=20, fetch=False)


def hid(auto):
    return automation_hash(auto)


def test_every_bound_automation_has_an_outcome_in_every_environment(result):
    keys = [e.key for e in ENVS]
    assert [e["key"] for e in result["envs"]] == keys
    assert len(result["automations"]) == 5  # the corpus is de-duplicated
    for a in result["automations"].values():
        if a["bound"] is not None:
            assert sorted(a["outcomes"]) == sorted(keys)
            assert set(a["outcomes"][keys[0]]) == {"fired", "first_tod_h", "flaps", "night_light", "night_cover"}


def test_unbindable_stays_with_its_reason(result):
    rain = result["automations"][hid(RAINY)]
    assert rain["bound"] is None and "weather" in rain["unbindable"] and rain["outcomes"] == {}
    assert rain["kinds"] == ["weather"] and rain["repo"] == "carol/cfg"
    assert result["yield"] == {"parsed": 5, "bound": 4, "run": 4}
    assert result["unbindable_reasons"] == {"weather entities are not modelled": 1}


def test_lamp_and_sunset_depend_on_the_environment(result):
    lamp = result["automations"][hid(LAMP)]
    assert lamp["repo"] == "alice/home" and lamp["kinds"] == ["illuminance"]
    assert "time_spread" in lamp["divergences"]
    dusk = result["automations"][hid(SUNSET)]
    assert "time_spread" in dusk["divergences"]
    assert dusk["static"] == ["night_light"] and "night_light" not in dusk["divergences"]  # on in every env


def test_negative_control_rate(result):
    nc = {k for k, a in result["automations"].items() if a["negative_control"]}
    assert nc == {hid(MOTION), hid(MORNING)}
    assert result["negative_control"] == {"n": 2, "flagged": 0}
    assert result["negative_control_rate"] == 0.0
    assert result["automations"][hid(MORNING)]["outcomes"][ENVS[0].key]["fired"] is True


def test_flag_counts_and_json(result):
    assert result["flagged"]["n"] == 2 and result["flagged"]["by_kind"]["time_spread"] == 2
    json.dumps(result)


def test_workers_and_limit_do_not_change_what_is_computed(corpus, work, result):
    again = sweep(corpus, work, ENVS, rung=4, workers=2, limit=20, fetch=False)
    assert json.dumps(again, sort_keys=True) == json.dumps(result, sort_keys=True)
    two = sweep(corpus, work, ENVS[:1], rung=4, workers=1, limit=2, fetch=False)
    assert two["yield"]["parsed"] == 2


def test_rung1_refuses_the_sun_and_records_why(corpus, work):
    r1 = sweep(corpus, work, ENVS[:1], rung=1, workers=1, limit=20, fetch=False)
    assert r1["rung"] == 1 and r1["envs"][0]["rung"] == 1
    dusk = r1["automations"][hid(SUNSET)]
    key = r1["envs"][0]["key"]
    assert "site" in dusk["refused"][key] and key not in dusk["outcomes"]
    assert r1["yield"]["run"] == 3  # the sunset automation ran nowhere


def test_reason_class_drops_entities_and_keeps_the_feature():
    from vesper.edt.sweep import reason_class
    assert reason_class("sensor.xyz_1: its name names no role the standard home has") == \
        "its name names no role the standard home has"
    assert reason_class("the standard home has no switch in the living (for switch.tv)") == \
        "the standard home has no switch in the living"
    assert reason_class("entity_id '!input motion' is not an entity id") == "entity_id <...> is not an entity id"
    assert reason_class("trigger 'template' is not supported") == "trigger 'template' is not supported"


def test_is_negative_control():
    roles = {"binary_sensor.m": "motion", "light.l": "light", "sensor.lux": "illuminance", "lock.d": "lock"}
    motion = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.m"}], "action": []}
    assert is_negative_control(motion, motion, roles)
    lux = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.lux", "below": 5}]}
    assert not is_negative_control(lux, lux, roles)
    via_state = {"trigger": [{"platform": "state", "entity_id": "sensor.lux"}]}
    assert not is_negative_control(via_state, via_state, roles)  # an environment entity, even via a state trigger
    tmpl = {"trigger": [{"platform": "time", "at": "07:00"}], "condition": [{"condition": "template",
                                                                             "value_template": "{{ true }}"}]}
    assert not is_negative_control(tmpl, tmpl, roles)
    nested = {"trigger": {"trigger": "time_pattern", "minutes": "/5"},
              "conditions": [{"condition": "or", "conditions": [{"condition": "state", "entity_id": "lock.d",
                                                                 "state": "locked"}]}]}
    assert is_negative_control(nested, nested, roles)
    assert not is_negative_control({"action": []}, {"action": []}, roles)  # no trigger


def test_cli_run_and_cover(corpus, work, tmp_path, monkeypatch):
    from vesper import cli
    from vesper.edt import trace
    monkeypatch.setattr(trace, "ENVIRONMENTS", ENVS[:2])
    out, cov = tmp_path / "out" / "sweep.json", tmp_path / "out" / "cover.json"
    assert cli.main(["edt", "run", "--corpus", str(corpus), "--work", str(work), "--out", str(out), "--workers", "1",
                     "--no-fetch"]) == 0
    text = out.read_text()
    assert str(corpus) not in text
    result = json.loads(text)
    assert result["yield"] == {"parsed": 5, "bound": 4, "run": 4} and len(result["envs"]) == 2
    assert cli.main(["edt", "cover", "--sweep", str(out), "--out", str(cov)]) == 0
    assert json.loads(cov.read_text())["flagged"]["all"] == result["flagged"]["n"]
