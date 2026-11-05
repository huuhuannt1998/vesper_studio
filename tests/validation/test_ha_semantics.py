"""Addendum 15: the semantic stratum of the engine differential test (mode, max, initial_state, delay, wait)."""
from __future__ import annotations

from vesper.validation import ha_diff, ha_semantics

DAY = 86400.0


def test_semantic_features_name_the_constructs():
    a = {"mode": "restart", "max": 3, "max_exceeded": "silent", "initial_state": False,
         "trigger": [{"platform": "time", "at": "07:00"}],
         "action": [{"if": [], "then": [{"delay": "00:01:00"}]},
                    {"repeat": {"count": 2, "sequence": [{"wait_for_trigger": [{"platform": "state",
                                                                                 "entity_id": "a.b"}]}]}}]}
    f = ha_semantics.semantic_features(a)
    assert f == {"mode:restart", "max", "max_exceeded", "initial_state:off", "delay", "wait_for_trigger"}
    assert ha_semantics.semantic_features({"initial_state": "on", "trigger": []}) == {"initial_state:on"}
    assert ha_semantics.semantic_features({"initial_state": True, "mode": "single", "trigger": []}) == \
        {"initial_state:on"}
    assert ha_semantics.semantic_features("not a mapping") == set()
    assert ha_semantics.carries({"max_exceeded"}) is False  # max/max_exceeded alone do not place it in the stratum
    assert ha_semantics.carries({"mode:queued"}) and ha_semantics.carries({"initial_state:on"})


def test_vesper_reading_drops_the_keys_the_engine_ignores():
    a = {"id": "x", "mode": "queued", "max": 5, "max_exceeded": "silent", "initial_state": "off",
         "trigger": [{"platform": "time", "at": "07:00"}], "action": [{"delay": 5}]}
    r = ha_semantics.vesper_reading(a)
    assert r == {"id": "x", "trigger": [{"platform": "time", "at": "07:00"}], "action": [{"delay": 5}]}
    assert a["mode"] == "queued"  # the bound automation is not changed


def _auto(fired_in: set, frame=(), **keys):
    return {"bound": {"trigger": [{"platform": "time", "at": "07:00"}], "action": [{"service": "light.turn_on"}],
                      **keys},
            "frame_divergences": list(frame), "divergences": list(frame),
            "outcomes": {e.key: {"fired": e.key in fired_in} for e in ha_diff.ENVS}}


def test_the_semantic_sample_is_stratified_seeded_and_excludes_the_r6c_sample():
    keys = [e.key for e in ha_diff.ENVS]
    autos = {f"ff{i}": _auto({keys[0]}, ["fires_somewhere_only"], mode="restart") for i in range(3)}
    autos["ff_plain"] = _auto({keys[0]}, ["fires_somewhere_only"])  # a frame flag without the constructs
    autos |= {f"mo{i}": _auto({keys[1]} if i % 2 else set(), mode="queued", initial_state="off")
              for i in range(200)}
    autos |= {f"de{i}": {**_auto(set()), "bound": {"trigger": [], "action": [{"delay": 3}]}} for i in range(100)}
    autos |= {f"on{i}": _auto({keys[2]}, initial_state=True) for i in range(30)}
    autos["excluded"] = _auto({keys[0]}, mode="parallel")
    autos["plain"] = _auto({keys[0]})
    s1 = ha_semantics.draw_semantic_sample(autos, exclude={"excluded", "mo0"})
    assert s1 == ha_semantics.draw_semantic_sample(autos, exclude={"excluded", "mo0"})
    by: dict[str, list[str]] = {}
    for k, name in s1:
        by.setdefault(name, []).append(k)
    assert sorted(by["frame_flag"]) == ["ff0", "ff1", "ff2"]
    assert len(by["mode"]) == dict(ha_semantics.SUBSTRATA)["mode"]
    assert sum(int(k[2:]) % 2 for k in by["mode"]) == round(len(by["mode"]) * ha_diff.FIRING_SHARE)
    assert "wait_for_trigger" not in by and len(by["delay"]) == dict(ha_semantics.SUBSTRATA)["delay"]
    assert len(by["initial_state_on"]) == dict(ha_semantics.SUBSTRATA)["initial_state_on"]
    assert "initial_state_off" not in by  # every initial_state off automation is a mode one (the first rule wins)
    picked = {k for k, _ in s1}
    assert not picked & {"excluded", "mo0", "plain", "ff_plain"}
    assert len(picked) == len(s1)
    assert ha_semantics.draw_semantic_sample(autos, exclude=set(), seed=1) != s1


def _pair(**kw):
    base = {"v_fired": True, "h_fired": True, "delta_s": 0.0, "calls_equal": True, "v_count": 2, "h_count": 2,
            "v_n": 2, "h_n": 2, "brightness_equal": None, "ha_unavailable": False, "notes": []}
    return {**base, **kw}


def test_classes_add_the_reading_rule_before_addendum_10s():
    mode = {"mode": "restart", "trigger": []}
    off = {"initial_state": "off", "trigger": []}
    plain = {"trigger": []}
    p = _pair(v_count=2, h_count=3, v_n=2, h_n=3)
    assert ha_semantics.classify(p, mode, reading=_pair(), fixed=None) == \
        ("HA semantics VESPER lacks", "mode restart (VESPER runs it as single)")
    never = _pair(h_fired=False, delta_s=None)
    assert ha_semantics.classify(never, off, reading=_pair(), fixed=None) == \
        ("HA semantics VESPER lacks", "initial_state off: Home Assistant never runs it")
    assert ha_semantics.classify(p, plain, reading=p, fixed=_pair(), fix="the wait") == ("engine bug", "the wait")
    assert ha_semantics.classify(p, plain, reading=p, fixed=None) == ("unexplained", "")
    sub = _pair(delta_s=-0.26, v_count=8, h_count=4)
    assert ha_semantics.classify(sub, plain, reading=sub, fixed=None)[1].startswith("the 50-500")
    # A15.11(2): a template error in the data of a call VESPER only records
    err = [[0, "ERROR", "x", "edt:3: Error executing script. Error rendering data template: IndexError"]]
    rec = _pair(h_fired=False, delta_s=None, calls_equal=False, calls_only_v=[["script.x", ""]], notes=err)
    assert ha_semantics.classify(rec, plain, reading=rec, fixed=None) == \
        ("HA semantics VESPER lacks", "permissive replay records a call without rendering its data")
    twin = {**rec, "calls_only_v": [["script.x", ""], ["light.turn_on", "light.y"]]}
    assert ha_semantics.classify(twin, plain, reading=twin, fixed=None) == ("unexplained", "")


def test_oracle_components_agree_or_not_per_pair():
    o = {"fired": True, "first_tod_h": 7.0, "flaps": False, "night_light": True, "night_cover": False}
    same = ha_semantics.component_agreement({"v_outcome": o, "h_outcome": dict(o)})
    assert same == {"fired": True, "first_tod_h": True, "flaps": True, "night_light": True, "night_cover": True}
    moved = ha_semantics.component_agreement({"v_outcome": o, "h_outcome": {**o, "first_tod_h": 7.5,
                                                                            "flaps": True}})
    assert moved["first_tod_h"] is False and moved["flaps"] is False and moved["fired"] is True
    near = ha_semantics.component_agreement({"v_outcome": o, "h_outcome": {**o, "first_tod_h": 7.0 + 0.5 / 3600}})
    assert near["first_tod_h"] is True  # within a second
