"""The differential test against Home Assistant (protocol Addendum 10): the pieces that run without Home Assistant,
and the driver itself when Home Assistant's venv is present (`VESPER_HA_PYTHON`, else `.vesper/ha/venv`)."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from vesper.edt.types import Env, Firing
from vesper.validation import ha_diff

DAY = 86400.0


def test_features_name_the_trigger_kinds_and_constructs():
    a = {"trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:30:00"},
                     {"platform": "numeric_state", "entity_id": "sensor.t", "above": 20, "for": "00:05:00"},
                     {"platform": "time_pattern", "minutes": "/5"}],
         "condition": [{"condition": "or", "conditions": [{"condition": "template", "value_template": "{{ 1 }}"}]}],
         "action": [{"if": [], "then": [{"choose": [{"conditions": [], "sequence": []}]}]}]}
    f = ha_diff.features(a)
    assert {"sun_offset", "numeric_state", "for", "time_pattern", "template", "choose"} <= f
    assert "sun_other" not in f
    assert "sun_other" in ha_diff.features({"trigger": [{"platform": "state", "entity_id": "sun.sun"}]})
    assert "sun_other" in ha_diff.features({"trigger": [{"platform": "sun", "event": "sunrise"}]})
    assert "sun_other" in ha_diff.features({"trigger": [{"platform": "sun", "event": "sunrise", "offset": 0}]})
    assert "template" in ha_diff.features({"trigger": [], "condition": ["{{ is_state('a.b', 'on') }}"]})
    assert ha_diff.features("not a mapping") == set()


def _auto(fired_in: set, frame=(), trig=None):
    envs = ha_diff.ENVS
    return {"bound": {"trigger": trig or [{"platform": "time", "at": "07:00"}]},
            "frame_divergences": list(frame),
            "outcomes": {e.key: {"fired": e.key in fired_in} for e in envs}}


def test_the_sample_is_stratified_seeded_and_deterministic():
    keys = [e.key for e in ha_diff.ENVS]
    autos = {f"ts{i}": _auto({keys[0]}, ["time_spread"]) for i in range(3)}
    autos |= {f"ff{i}": _auto({keys[0]}, ["fires_somewhere_only"]) for i in range(60)}
    autos |= {f"so{i}": _auto({keys[1]} if i % 2 else set(), trig=[{"platform": "sun", "event": "sunset",
                                                                    "offset": "01:00:00"}]) for i in range(80)}
    autos |= {f"ot{i}": _auto(set()) for i in range(50)}
    autos["unbound"] = {"bound": None, "outcomes": {}}
    autos["missing_env"] = {**_auto(set()), "outcomes": {keys[0]: {"fired": False}}}
    s1, s2 = ha_diff.draw_sample(autos), ha_diff.draw_sample(autos)
    assert s1 == s2
    by = {}
    for k, name in s1:
        by.setdefault(name, []).append(k)
    assert sorted(by["time_spread_frame_flag"]) == ["ts0", "ts1", "ts2"]
    assert len(by["frame_flag"]) == 40 and all(k.startswith("ff") for k in by["frame_flag"])
    assert len(by["sun_offset"]) == 30
    firing = [k for k in by["sun_offset"] if int(k[2:]) % 2]
    assert len(firing) == round(30 * ha_diff.FIRING_SHARE)
    assert len(by["other"]) == 20
    assert "unbound" not in dict(s1) and "missing_env" not in dict(s1)
    assert len(set(dict(s1))) == len(s1)
    assert ha_diff.draw_sample(autos, seed=1) != s1


def test_ha_calls_become_vesper_firings():
    label_of = {"edt:0": "a", "edt:1": "b"}
    calls = [
        [DAY + 5, "edt:0", "homeassistant", "turn_on", ["light.x", "switch.y"], {}, False, None, None],
        [DAY + 5, "edt:0", "light", "turn_on", ["light.x"], {}, True, "homeassistant.turn_on", None],
        [DAY + 5, "edt:0", "switch", "turn_on", ["switch.y"], {}, True, "homeassistant.turn_on", None],
        [DAY + 9, "edt:1", "notify", "phone", [], {"message": "m"}, False, None, None],
        [DAY + 9, "edt:1", "script", "x", [], {}, True, "notify.phone", None],  # nested in a stub: not a firing
        [DAY + 10, "edt:1", "light", "turn_on", ["light.x"], {"brightness_pct": 120}, False, None, "Invalid"],
        [DAY + 11, None, "light", "turn_off", ["light.x"], {}, False, None, None],
    ]
    f = ha_diff.ha_firings({"calls": calls}, label_of, DAY, 3 * DAY)
    assert [(x.t, x.service, x.target) for x in f["a"]] == [(DAY + 5, "light.turn_on", "light.x"),
                                                           (DAY + 5, "switch.turn_on", "switch.y")]
    assert [(x.service, x.target) for x in f["b"]] == [("notify.phone", "")]


def test_compare_pair_measures_the_difference():
    v = [Firing(DAY + 100.0, "a", "light.turn_on", "light.x", (("state", "ON"), ("brightness", 127))),
         Firing(DAY + 200.0, "a", "light.turn_off", "light.x"), Firing(50.0, "a", "light.turn_on", "light.x")]
    h = [Firing(DAY + 99.5, "a", "light.turn_on", "light.x", (("brightness_pct", 50),)),
         Firing(DAY + 200.0, "a", "light.turn_off", "light.x")]
    r = ha_diff.compare_pair(v, h, DAY, 3 * DAY, {"light.x": 254})
    assert r["v_fired"] and r["h_fired"] and r["delta_s"] == pytest.approx(0.5)
    assert r["calls_equal"] and r["v_count"] == r["h_count"] == 2
    assert r["brightness_comparable"] and r["brightness_equal"]  # 127 of 254 is 128 of 255, 50 % is 128
    r = ha_diff.compare_pair(v, [], DAY, 3 * DAY, {})
    assert r["v_fired"] and not r["h_fired"] and r["delta_s"] is None and not r["calls_equal"]


def test_log_lines_are_attributed_to_their_automation():
    label_of = {"edt:12": "k12", "edt:1": "k1"}
    assert ha_diff._log_owner([0, "ERROR", "homeassistant.components.automation.edt_12", "x"], label_of) == "k12"
    assert ha_diff._log_owner([0, "ERROR", "homeassistant.helpers.script", "edt:1: Error executing"], label_of) == "k1"
    assert ha_diff._log_owner([0, "WARNING", "homeassistant.helpers.service", "Referenced entities"], label_of) is None


def _flag_entry(tods: dict[str, float]) -> tuple[dict, dict]:
    envs = {f"e{i}": Env(f"City {i}", "2026-01-14", 0.0, 4) for i in range(3)}
    outcomes = {k: {"fired": t is not None, "first_tod_h": t, "flaps": False, "night_light": False,
                    "night_cover": False} for k, t in tods.items()}
    rich = {k: {"first_t": None if t is None else DAY + t * 3600} for k, t in tods.items()}
    entry = {"bound": {"trigger": [{"platform": "time", "at": "07:00"}]}, "outcomes": outcomes, "rich": rich}
    return entry, envs


def test_substitution_with_the_sweeps_own_outcomes_changes_nothing():
    entry, envs = _flag_entry({"e0": 7.0, "e1": 11.0, "e2": None})
    clock, frame = ha_diff._divergences(entry, entry["outcomes"], entry["rich"], envs)
    assert clock == frame == ["fires_somewhere_only", "time_spread"]
    same = ha_diff.substituted_flags(entry, {}, envs)
    assert same == {"clock": clock, "frame": frame}
    moved = ha_diff.substituted_flags(entry, {"e1": ({**entry["outcomes"]["e1"], "first_tod_h": 9.0},
                                                     {"first_t": DAY + 9 * 3600})}, envs)
    assert moved["frame"] == ["fires_somewhere_only"]


def test_margin_counts_the_decisions_a_shift_could_change():
    entry, envs = _flag_entry({"e0": 7.0, "e1": 10.01, "e2": 7.0})  # spread 3.01 h: 36 s above the tolerance
    autos = {"a": entry}
    assert ha_diff.margin_at_risk(autos, ["a"], 10.0, envs) == 0
    assert ha_diff.margin_at_risk(autos, ["a"], 20.0, envs) == 1


def _pair(**kw):
    base = {"v_fired": True, "h_fired": True, "delta_s": 0.0, "calls_equal": True, "v_count": 2, "h_count": 2,
            "brightness_equal": None, "ha_unavailable": False, "notes": []}
    return {**base, **kw}


def test_a_pair_is_discrepant_by_firing_time_calls_or_counts_and_subsecond_apart():
    assert not ha_diff.discrepant(_pair())
    assert not ha_diff.discrepant(_pair(delta_s=0.4)) and ha_diff.subsecond_only(_pair(delta_s=0.4))
    for kw in ({"h_fired": False, "delta_s": None}, {"delta_s": -119.0}, {"calls_equal": False}, {"h_count": 3},
               {"brightness_equal": False}):
        assert ha_diff.discrepant(_pair(**kw)), kw


def test_classes_follow_the_rules_of_addendum_10():
    sun = {"trigger": [{"platform": "state", "entity_id": "sun.sun"}]}
    plain = {"trigger": [{"platform": "time", "at": "07:00"}]}
    assert ha_diff.classify(_pair(delta_s=1.0), sun, _pair())[0] == "engine bug"
    kelvin = _pair(h_fired=False, delta_s=None, notes=[[0, "ERROR", "x", "not a valid option at 'kelvin'"]])
    assert ha_diff.classify(kelvin, plain, _pair())[1].startswith("light.turn_on kelvin")
    off = {**plain, "initial_state": "off"}
    assert ha_diff.classify(_pair(h_fired=False, delta_s=None), off, None)[0] == "HA semantics VESPER lacks"
    resp = _pair(calls_equal=False, notes=[[0, "ERROR", "x", "from_json got invalid input '' ... response"]])
    assert ha_diff.classify(resp, plain, resp)[0] == "harness artifact"
    assert ha_diff.classify(_pair(delta_s=-0.26, h_count=4, v_count=8), plain, None)[1].startswith("the 50-500")
    assert ha_diff.classify(_pair(delta_s=30.0), plain, _pair(delta_s=30.0)) == ("unexplained", "")


def test_fix_reach_counts_the_run_automations_a_fix_touches():
    def entry(bound, fired, frame=()):
        return {"bound": bound, "outcomes": {"e": {"fired": fired}}, "divergences": list(frame),
                "frame_divergences": list(frame)}
    autos = {"s": entry({"trigger": [{"platform": "state", "entity_id": "sun.sun"}]}, True, ["time_spread"]),
             "k": entry({"trigger": [], "action": [{"if": [], "then": [
                 {"action": "light.turn_on", "data": {"kelvin": 2500}}]}]}, True),
             "o": entry({"trigger": [], "initial_state": False}, False),
             "x": {"bound": None, "outcomes": {}}}
    r = ha_diff.fix_reach(autos)
    assert r["of_run"] == 3
    assert r["sun_sun"] == {"run": 1, "fire_somewhere": 1, "clock_flags": 1, "frame_flags": 1,
                            "time_spread_frame_flags": 1}
    assert r["kelvin_color_temp"]["run"] == 1 and r["initial_state_off"]["run"] == 1


def test_propagation_draws_per_environment_or_per_automation():
    entry, envs = _flag_entry({"e0": 7.0, "e1": 11.0, "e2": 8.0})
    entry["divergences"] = entry["frame_divergences"] = ["time_spread"]
    autos = {"a": entry}
    agree = [_pair() for _ in range(4)]
    silent = [_pair(h_fired=False, delta_s=None) for _ in range(4)]
    none = ha_diff.propagate(autos, ["a"], agree, envs, reps=20)
    assert none["frame_changed"]["max"] == 0
    grouped = ha_diff.propagate(autos, ["a"], agree + silent, envs, reps=20, groups=[silent])
    assert grouped["frame_changed"]["min"] == 1  # Home Assistant never runs it: the flag is lost every time


def _ha_python() -> Path | None:
    cands = [os.environ.get("VESPER_HA_PYTHON"), Path(__file__).parents[2] / ".vesper/ha/venv/bin/python"]
    return next((Path(c) for c in cands if c and Path(c).is_file()), None)


@pytest.mark.skipif(_ha_python() is None, reason="Home Assistant's venv is not installed")
def test_the_driver_runs_home_assistant_on_the_virtual_clock(tmp_path):
    from astral import Observer
    from astral.sun import sunset
    zone = ZoneInfo("Europe/Stockholm")
    day0 = dt.date(2026, 1, 14)
    t0 = dt.datetime.combine(day0, dt.time(0), tzinfo=zone).timestamp()
    autos = [
        {"alias": "edt:0", "trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:30:00"}],
         "action": [{"service": "light.turn_on", "target": {"entity_id": "light.x"}}]},
        {"alias": "edt:1", "trigger": [{"platform": "numeric_state", "entity_id": "sensor.t", "above": 20,
                                        "for": "00:05:00"}],
         "action": [{"service": "homeassistant.turn_off", "entity_id": "light.x"}]},
        {"alias": "edt:2", "trigger": [{"platform": "time", "at": "04:45:00"}],
         "action": [{"service": "homeassistant.restart"}, {"service": "notify.phone", "data": {"message": "m"}}]},
        {"alias": "edt:3", "trigger": [{"platform": "time", "at": "06:00:00"}],
         "action": [{"service": "light.turn_on", "target": {"entity_id": "light.x"}, "data": {"kelvin": 2500}},
                    {"service": "notify.phone"}]},
        {"alias": "edt:4", "initial_state": "off", "trigger": [{"platform": "time", "at": "06:00:00"}],
         "action": [{"service": "notify.phone"}]},
    ]
    stream = [[3600.05, "sensor.t", "21", {}], [3700.05, "sensor.t", "19", {}], [7200.05, "sensor.t", "22", {}]]
    job = {"t0": t0, "end": 2 * DAY, "seed": 1, "site": {"lat": 59.3293, "lon": 18.0686, "elevation_m": 28,
                                                         "tz": "Europe/Stockholm"},
           "integrations": ["light", "switch"], "services": {}, "noop": ha_diff.HA_NOOP, "automations": autos,
           "initial": {"sensor.t": ["18", {}], "light.x": ["off", {}]}, "stream": stream}
    jp, op = tmp_path / "job.json", tmp_path / "out.json"
    jp.write_text(json.dumps(job))
    subprocess.run([str(_ha_python()), "-I", str(ha_diff.DRIVER), str(jp), str(op)], check=True, timeout=600)
    out = json.loads(op.read_text())
    assert out.get("error") is None and out["ha_version"] == "2026.10.2"
    by = {}
    for t, alias, d, s, ids, data, nested, parent, refused in out["calls"]:
        by.setdefault(alias, []).append((t, f"{d}.{s}", ids, nested, refused))
    obs = Observer(latitude=59.3293, longitude=18.0686, elevation=28)
    for day in (0, 1):  # the sun trigger, at astral's sunset at the configured elevation, 30 min early, each day
        want = sunset(obs, day0 + dt.timedelta(days=day), tzinfo=zone).timestamp() - t0 - 1800
        got = [t for t, name, *_ in by["edt:0"] if day * DAY <= t < (day + 1) * DAY]
        assert got == [pytest.approx(want, abs=1e-3)]
    # numeric_state: the crossing at 3600 is not held for 5 min; the one at 7200 is, so it fires at 7500
    assert [(round(t, 2), name, nested) for t, name, _, nested, _ in by["edt:1"]] == \
        [(7500.05, "homeassistant.turn_off", False), (7500.05, "light.turn_off", True)]
    # a restart is a recorded no-op: the run goes on to the notify, at 04:45 plus the sub-second offset
    names = [name for _, name, *_ in by["edt:2"]]
    assert names == ["homeassistant.restart", "notify.phone"] * 2
    assert all(0.05 <= t % 60 <= 0.5 for t, *_ in by["edt:2"])
    # Home Assistant 2026.10.2 refuses `kelvin`: the call is refused and the run stops
    assert [(name, bool(refused)) for _, name, _, _, refused in by["edt:3"]] == [("light.turn_on", True)] * 2
    assert "edt:4" not in by  # initial_state off: never runs
    states = [s for _, s in out["sun"]]
    assert "below_horizon" in states and "above_horizon" in states
