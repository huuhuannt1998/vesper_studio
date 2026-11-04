"""vesper.edt.sweep: a tiny corpus swept across a few environments (assumed clear sky: no weather is fetched)."""
import json
import textwrap
from pathlib import Path

import pytest

from vesper.edt.corpus import automation_hash
from vesper.edt.sweep import is_negative_control, negative_control_summary, sweep
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
    assert result["negative_control"] == {"n": 2, "fired": 1, "flagged": 0, "rate": 0.0, "threshold": 0.05,
                                          "halt": False, "rate_over": "control automations that fire somewhere",
                                          "uninformative": False}
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
    assert reason_class("entity_id 'binary_sensor.MCU1_GPIO4' is not an entity id") == \
        "entity_id <...> is not an entity id"
    assert reason_class("entity light.a, light.b is not one of this home's") == "entity <...> is not one of this home's"


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


def _nc(fired, flagged):
    out = {"outcomes": {"e": {"fired": fired}}, "divergences": ["time_spread"] if flagged else [],
           "negative_control": True}
    return out


def test_negative_control_halts_above_five_percent_of_its_firing_subset():
    autos = {f"q{i}": _nc(False, False) for i in range(80)}  # never fire: cannot diverge, not in the rate
    autos |= {f"f{i}": _nc(True, i < 2) for i in range(20)}  # 2 of 20 firing ones flagged: 10 %
    nc = negative_control_summary(autos)
    assert nc["n"] == 100 and nc["fired"] == 20 and nc["flagged"] == 2
    assert nc["rate"] == 0.1 and nc["halt"] is True
    autos["f0"] = _nc(True, False)
    assert negative_control_summary(autos)["halt"] is False  # 1 of 20 = 5 %: not above
    assert negative_control_summary({})["rate"] is None and negative_control_summary({})["halt"] is False


def test_cli_run_prints_halt_and_fails(tmp_path, monkeypatch, capsys):
    from vesper import cli
    from vesper.edt import sweep as sw
    fake = {"rung": 4, "envs": [], "automations": {}, "yield": {"parsed": 0, "bound": 0, "run": 0},
            "flagged": {"n": 0, "by_kind": {}}, "failed_envs": {}, "negative_control_rate": 0.5,
            "negative_control": {"n": 4, "fired": 2, "flagged": 1, "rate": 0.5, "threshold": 0.05, "halt": True}}
    monkeypatch.setattr(sw, "sweep", lambda *a, **k: fake)
    out = tmp_path / "sweep.json"
    assert cli.main(["edt", "run", "--corpus", str(tmp_path), "--work", str(tmp_path / "w"), "--out", str(out),
                     "--no-fetch"]) != 0
    assert "HALT" in capsys.readouterr().err
    assert json.loads(out.read_text())["negative_control"]["halt"] is True  # the result is still written


def _failing_on(bad_ids=(), refuse=None):
    """replay_detailed that raises when a batch holds one of `bad_ids`, and adds `refuse` = (id, env key, reason)."""
    from vesper.edt import replay as rp
    real = rp.replay_detailed

    def fake(home_yaml, trace, automations, **kw):
        if any(i in automations for i in bad_ids):
            raise TypeError("unhashable type: 'list'")
        r = real(home_yaml, trace, automations, **kw)
        if refuse and refuse[0] in automations and refuse[1] in Path(trace).name:
            r.refused[refuse[0]] = refuse[2]
            r.firings[refuse[0]] = []
        return r
    return fake


def test_a_failing_automation_is_bisected_out_of_its_batch(corpus, work, result, monkeypatch):
    from vesper.edt import replay as rp
    bad = hid(SUNSET)
    monkeypatch.setattr(rp, "replay_detailed", _failing_on([bad]))
    r = sweep(corpus, work, ENVS, rung=4, workers=1, limit=20, fetch=False)
    keys = [e.key for e in ENVS]
    dusk = r["automations"][bad]
    assert dusk["outcomes"] == {} and sorted(dusk["refused"]) == sorted(keys)
    assert all(v.startswith("replay error: TypeError") for v in dusk["refused"].values())
    for aid, a in r["automations"].items():
        if aid != bad and a["bound"] is not None:
            assert a["outcomes"] == result["automations"][aid]["outcomes"] and a["refused"] == {}
    assert r["failed_envs"] == {} and r["yield"]["run"] == 3


def test_an_environment_where_every_replay_fails_is_one_failure(corpus, work, monkeypatch):
    from vesper.edt import replay as rp
    monkeypatch.setattr(rp, "replay_detailed", _failing_on([hid(a) for a in (LAMP, SUNSET, MOTION, MORNING)]))
    r = sweep(corpus, work, ENVS[:2], rung=4, workers=1, limit=20, fetch=False)
    assert sorted(r["failed_envs"]) == sorted(e.key for e in ENVS[:2])
    assert "replay error" in r["failed_envs"][ENVS[0].key]
    assert all(a["refused"] == {} and a["outcomes"] == {} for a in r["automations"].values())
    assert r["yield"]["run"] == 0


def test_a_trace_that_cannot_be_recorded_is_an_environment_failure(corpus, work, monkeypatch):
    from vesper.edt import trace as tr

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(tr, "record_trace", boom)
    r = sweep(corpus, work, ENVS[:1], rung=4, workers=1, limit=20, fetch=False)
    assert "disk full" in r["failed_envs"][ENVS[0].key]


def test_refused_in_one_environment_and_run_in_the_others(corpus, work, result, monkeypatch):
    from vesper.edt import replay as rp
    lamp = hid(LAMP)
    monkeypatch.setattr(rp, "replay_detailed", _failing_on(refuse=(lamp, ENVS[0].key, "trigger 'x' is not supported")))
    r = sweep(corpus, work, ENVS, rung=4, workers=1, limit=20, fetch=False)
    a = r["automations"][lamp]
    assert a["refused"] == {ENVS[0].key: "trigger 'x' is not supported"}
    assert sorted(a["outcomes"]) == sorted(e.key for e in ENVS[1:])
    assert r["partially_refused"] == 1 and r["yield"]["run"] == 4


def test_a_negative_control_that_never_fires_is_reported_uninformative():
    nc = negative_control_summary({f"q{i}": _nc(False, False) for i in range(5)})
    assert nc["fired"] == 0 and nc["rate"] is None and nc["halt"] is False and nc["uninformative"] is True
    assert negative_control_summary({})["uninformative"] is False  # no control at all: nothing to report
    assert negative_control_summary({"f": _nc(True, False)})["uninformative"] is False


def test_prefetch_weather_fetches_once_per_city_and_date(tmp_path, monkeypatch):
    from vesper.edt import sweep as sw
    from vesper.edt import trace
    calls = []
    monkeypatch.setattr(trace, "_fetch_weather", lambda site, cache, get: calls.append((site.lat, site.lon, cache)))
    envs = [Env("Stockholm, Sweden", "2026-01-15", 0.0, 4), Env("Stockholm, Sweden", "2026-01-15", 180.0, 4),
            Env("Phoenix, AZ, USA", "2026-07-15", 0.0, 4), Env("Phoenix, AZ, USA", "2026-07-15", 0.0, 1)]
    sw._prefetch_weather(envs, tmp_path)
    assert len(calls) == 2 and all(c[2] == tmp_path / "weather" for c in calls)


def test_negative_control_excludes_templates_and_environment_reads_in_actions():
    roles = {"binary_sensor.m": "motion", "light.l": "light"}
    trig = [{"trigger": "state", "entity_id": "binary_sensor.m", "to": "on"}]
    plain = {"triggers": trig, "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.l"}}]}
    assert is_negative_control(plain, plain, roles)
    templ = {"triggers": trig, "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.l"},
                                            "data": {"brightness_pct": "{{ states('sensor.lux') | int }}"}}]}
    assert not is_negative_control(templ, templ, roles)
    reads = {"triggers": trig, "actions": [{"action": "light.turn_on", "target": {"entity_id": "sensor.lux"}}]}
    assert not is_negative_control(reads, reads, {**roles, "sensor.lux": "illuminance"})


GATED = {"alias": "dusk unless away", "trigger": [{"platform": "sun", "event": "sunset"}],
         "condition": [{"condition": "state", "entity_id": "input_boolean.away_mode", "state": "off"}],
         "action": [{"service": "light.turn_on", "entity_id": "light.kitchen"}]}


def test_assumptions_are_stored_and_results_reported_also_over_the_assumption_free_subset(tmp_path):
    _put(tmp_path / "corpus" / "raw", "dan~home##automations.yaml", [GATED, SUNSET])
    r = sweep(tmp_path / "corpus", tmp_path / "work", ENVS[:2], rung=4, workers=1, fetch=False)
    gated, plain = r["automations"][hid(GATED)], r["automations"][hid(SUNSET)]
    assert gated["assumptions"] == ["input_boolean.away_mode == off"] and plain["assumptions"] == []
    assert gated["bound"] is not None and "condition" in gated["bound"] and gated["outcomes"]
    assert r["yield"] == {"parsed": 2, "bound": 2, "run": 2}
    assert r["yield_assumption_free"] == {"parsed": 2, "bound": 1, "run": 1}
    assert r["flagged_assumption_free"]["n"] == int(bool(plain["divergences"]))
    assert r["flagged"]["n"] == sum(1 for a in (gated, plain) if a["divergences"])
    assert set(r["flagged_assumption_free"]) == {"n", "by_kind"}


def test_a_batch_that_hangs_is_bisected_and_the_hung_automation_refused_as_a_replay_timeout(monkeypatch):
    import time

    from vesper.edt import replay as replay_module
    from vesper.edt import sweep as sweep_module
    from vesper.edt.replay import Replay

    def fake(home, trace, autos, catalog_dir):
        if "hang" in autos:
            time.sleep(5)
        return Replay({k: [] for k in autos})
    monkeypatch.setattr(replay_module, "replay_detailed", fake)
    monkeypatch.setattr(sweep_module, "_W", {"home_yaml": None, "catalog_dir": None, "timeout": 0.2,
                                             "bound": {k: {} for k in ("a", "b", "hang", "c")}})
    out = {"firings": {}, "refused": {}, "approximations": {}, "errors": {}}
    t0 = time.monotonic()
    sweep_module._replay(None, ["a", "b", "hang", "c"], out)
    assert time.monotonic() - t0 < 2
    assert out["errors"] == {"hang": "replay timeout"} and set(out["firings"]) == {"a", "b", "c"}
