"""Revision R9-Z's script (protocol Addendum 19): the frame relation's separate sun tolerance (bit-identical at its
default), the tolerance grid, the orientation cores and the isotropy rule, the witnesses' structural features, the
humidity clamp, the dead-rule reason predicates, the environment-blocked propagation and the setback breakdown, on toy
inputs."""
import importlib.util
import random
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r9z_analyses", ROOT / "scripts/revision/r9z_analyses.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r9z = _load()

from vesper.edt.oracle import (  # noqa: E402
    SunRef,
    _ref_instant,
    frame_divergences,
    frame_invariance,
    frame_restricted_divergences,
    frames_of,
    outcome,
    site_of,
)
from vesper.edt.types import Env, Firing  # noqa: E402

JAN = Env("Stockholm, Sweden", "2026-01-14", 0.0, 4)
JUL = Env("Stockholm, Sweden", "2026-07-15", 0.0, 4)
JAN180 = Env("Stockholm, Sweden", "2026-01-14", 180.0, 4)
LAMP = [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]
SUNSET = {"trigger": [{"platform": "sun", "event": "sunset"}], "action": LAMP}
CLOCKED = {"trigger": [{"platform": "time", "at": "07:00"}], "action": LAMP}
DAY, H = 86400.0, 3600.0


def _sunset(env):
    s = site_of(env)
    return _ref_instant(s.lat, s.lon, s.elevation_m, s.tz, s.date.isoformat(), 1, SunRef("sunset"))


def _fire(env, t):
    return outcome([Firing(t, "a", "switch.turn_on", "switch.porch")], site_of(env))  # no lamp: no night_light


def _sun_case(minutes_late: float):
    """Fires at sunset in January and `minutes_late` after sunset in July."""
    tj, tl = _sunset(JAN), _sunset(JUL) + minutes_late * 60
    return {JAN: _fire(JAN, tj), JUL: _fire(JUL, tl)}, {JAN: tj, JUL: tl}


# ---- A19.3: the separate sun tolerance -------------------------------------------------------------------------------

def test_the_sun_tolerance_defaults_to_h_in_minutes_so_every_caller_is_unchanged():
    fr = frames_of(SUNSET)
    for late in (0, 90, 170, 190, 300, 400):
        by_env, ft = _sun_case(late)
        for h in (1.0, 3.0, 6.0):
            assert frame_divergences(by_env, ft, fr, h) == frame_divergences(by_env, ft, fr, h, h_sun_min=h * 60)
            assert frame_invariance(by_env, ft, fr, h) == frame_invariance(by_env, ft, fr, h, h_sun_min=h * 60)
        assert frame_divergences(by_env, ft, fr) == frame_divergences(by_env, ft, fr, 3.0, h_sun_min=180.0)


def test_the_sun_tolerance_is_judged_apart_from_the_clock_one():
    fr = frames_of(SUNSET)
    by_env, ft = _sun_case(200)  # a 200 min spread from sunset
    assert "time_spread" in frame_divergences(by_env, ft, fr, 3.0)
    assert "time_spread" not in frame_divergences(by_env, ft, fr, 3.0, h_sun_min=360.0)
    assert "time_spread" in frame_divergences(by_env, ft, fr, 6.0, h_sun_min=60.0)  # h_clk does not judge the sun
    by_env, ft = _sun_case(90)
    assert "time_spread" not in frame_divergences(by_env, ft, fr, 3.0)
    assert "time_spread" in frame_divergences(by_env, ft, fr, 3.0, h_sun_min=60.0)


def test_the_clock_tolerance_still_judges_the_clock_frame_whatever_the_sun_one():
    fr = frames_of(CLOCKED)
    by_env = {JAN: _fire(JAN, DAY + 6 * H), JUL: _fire(JUL, DAY + 10 * H)}  # 4 h apart
    ft = {JAN: DAY + 6 * H, JUL: DAY + 10 * H}
    assert "time_spread" in frame_divergences(by_env, ft, fr, 3.0, h_sun_min=360.0)
    assert "time_spread" not in frame_divergences(by_env, ft, fr, 6.0, h_sun_min=60.0)


def test_the_restricted_relation_takes_the_sun_tolerance_too():
    fr = frames_of(SUNSET)
    by_env, ft = _sun_case(200)
    pair = [JAN, JUL]
    assert "time_spread" in frame_restricted_divergences(by_env, ft, fr, pair)
    assert "time_spread" not in frame_restricted_divergences(by_env, ft, fr, pair, h_sun_min=360.0)


def test_the_grid_flags_a_record_per_cell_and_strips_static_envelope_kinds():
    by_env, ft = _sun_case(200)
    entry = {"bound": SUNSET, "outcomes": {e.key: asdict(o) for e, o in by_env.items()},
             "rich": {e.key: {"first_t": t} for e, t in ft.items()}}
    by_key = {JAN.key: JAN, JUL.key: JUL}
    assert r9z.flag_kinds(entry, by_key, 3.0, 180.0) == ["time_spread"]
    assert r9z.flag_kinds(entry, by_key, 3.0, 360.0) == []
    # a lamp left on in every environment is static, not a divergence
    lamp = {k: {**o, "night_light": True} for k, o in entry["outcomes"].items()}
    assert r9z.flag_kinds({**entry, "outcomes": lamp}, by_key, 3.0, 360.0) == []


def test_frame_class_names_the_judged_frames():
    assert r9z.frame_class(frames_of(CLOCKED)) == "clock only"
    assert r9z.frame_class(frames_of(SUNSET)) == "sun only"
    sensed = {"trigger": [{"platform": "state", "entity_id": "sensor.living_room_temperature"}], "action": LAMP}
    assert r9z.frame_class(frames_of(sensed)) == "sensed only"
    mixed = {"trigger": [{"platform": "state", "entity_id": "sensor.living_room_temperature"},
                         {"platform": "time", "at": "07:00"}], "action": LAMP}
    assert r9z.frame_class(frames_of(mixed)) == "mixed"
    door = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.front_door"}], "action": LAMP}
    assert r9z.frame_class(frames_of(door)) == "clock only"  # judged in the clock frame


def test_grid_summary_counts_kinds_gained_and_lost():
    pub = {"a": ["time_spread"], "b": ["fires_somewhere_only"]}
    cell = {"b": ["fires_somewhere_only"], "c": ["time_spread", "flaps"]}
    s = r9z.grid_summary(cell, pub)
    assert s["frame_flags"] == 2 and s["gained"] == 1 and s["lost"] == 1 and s["same_kinds"] == 1
    assert s["by_kind"] == {"fires_somewhere_only": 1, "time_spread": 1, "flaps": 1}


# ---- A19.2: cores, isotropy, features --------------------------------------------------------------------------------

def test_core_counts_and_which_setting_binds():
    pub = {1, 2, 3, 4, 5}
    settings = {"beam": {1, 2, 3, 4, 9}, "g0.5": {1, 2, 3, 5}, "p9": {1, 2, 4, 5}}
    out = r9z.core_counts(pub, settings)
    assert out["core"] == 2 and out["possible"] == 6 and out["of"] == 5
    assert out["core_without"] == {"beam": 3, "g0.5": 3, "p9": 3}  # {1,2,5}, {1,2,4}, {1,2,3}
    assert out["held_in"] == {"0": 0, "1": 0, "2": 3, "3": 2}


def test_the_isotropy_rule():
    c = r9z.setting_class
    for s in ("ap0.21", "ap0.25", "ap0.29", "ap0.5", "ap2", "ds0.6", "ds0.95"):
        assert c("R7-A", s) == "orientation-relevant"
    for s in ("out-2", "out+2", "hc2", "hc3", "corrected"):
        assert c("R7-A", s) == "orientation-isotropic"
    assert c("R7-A", "nominal") == "unperturbed"
    assert c("R7-B corners", "G2-Foff-W5-C260") == "orientation-isotropic"
    assert c("R7-B corners", "ref") == "unperturbed"
    assert c("R7-B offsets", "tin+7") == "orientation-isotropic"
    assert c("R7-B offsets", "ref") == "unperturbed"
    assert c("R7-W", "shortwave") == "orientation-relevant"


def test_entities_rooms_and_facings_read_from_triggers_and_conditions():
    role_of = {"sensor.a_t": ("temperature", "bedroom_1", 0), "sensor.k_h": ("humidity", "kitchen", 0),
               "light.l": ("light", "living", 0), "sensor.p_t": ("temperature", "porch", 0)}
    bound = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.a_t", "below": 18}],
             "condition": [{"condition": "template", "value_template": "{{ states('sensor.k_h') | float > 60 }}"}],
             "action": [{"action": "light.turn_on", "target": {"entity_id": "light.l"}}]}
    assert r9z.rooms_read(bound, role_of) == {"bedroom_1", "kitchen"}
    assert r9z.rooms_acted(bound, role_of) == {"living"}
    assert r9z.features_read(bound, role_of) == {"temperature", "humidity"}
    windows = {"bedroom_1": {270}, "kitchen": {180}, "living": {0}}
    assert r9z.facings(r9z.rooms_read(bound, role_of), windows) == {180, 270}
    assert r9z.facings({"corridor"}, windows) == set()
    assert r9z.rooms_read({"trigger": [{"platform": "sun", "event": "sunset"}], "action": LAMP}, role_of) == set()


def test_feature_table_counts_inside_and_outside_the_core():
    feats = {"a": {"x", "y"}, "b": {"x"}, "c": {"y"}}
    t = r9z.feature_table({"a", "b"}, {"a", "b", "c"}, feats)
    assert t == {"x": {"core": 2, "outside": 0}, "y": {"core": 1, "outside": 1}}


# ---- A19.4(a): the clamp ---------------------------------------------------------------------------------------------

def test_clamped_values_and_the_value_at_an_instant():
    assert r9z.clamped("85.0") and r9z.clamped(84.96) and r9z.clamped(15) and not r9z.clamped(84.9)
    assert not r9z.clamped("unavailable") and not r9z.clamped(None)
    series = [(0.0, "50"), (100.0, "85.0"), (200.0, "70")]
    assert r9z.value_at(series, 150.0) == "85.0" and r9z.value_at(series, 99.0) == "50"
    assert r9z.value_at(series, 200.0) == "70" and r9z.value_at([], 5.0) is None
    assert r9z.reaches_clamp(series, 0.0, 300.0) and r9z.reaches_clamp(series, 150.0, 300.0)  # held from t=100
    assert not r9z.reaches_clamp(series, 200.0, 300.0) and not r9z.reaches_clamp(series, 0.0, 100.0)


def test_bounds_beyond_the_clamp():
    assert r9z.bound_beyond_clamp(85, None) and r9z.bound_beyond_clamp(None, 15)
    assert not r9z.bound_beyond_clamp(70, None) and not r9z.bound_beyond_clamp(None, 40)


# ---- A19.4(b): reason predicates ------------------------------------------------------------------------------------

def test_probes_are_the_triggers_alone_the_conditions_on_a_pattern_and_the_actions_on_a_pattern():
    b = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.d"}],
         "condition": [{"condition": "state", "entity_id": "light.l", "state": "on"}],
         "action": [{"action": "light.turn_off", "target": {"entity_id": "light.l"}}], "mode": "single"}
    probe = [{"action": "switch.turn_on", "target": {"entity_id": "switch.p"}}]
    p = r9z.dead_probes("x", b, probe)
    assert set(p) == {"x|P1", "x|P3", "x|P4"}
    assert p["x|P1"]["triggers"] == b["trigger"] and "conditions" not in p["x|P1"] and p["x|P1"]["actions"] == probe
    assert p["x|P3"]["triggers"] == [r9z.PATTERN] and p["x|P3"]["conditions"] == b["condition"]
    assert p["x|P3"]["actions"] == probe
    assert p["x|P4"]["triggers"] == [r9z.PATTERN] and "conditions" not in p["x|P4"]
    assert p["x|P4"]["actions"] == b["action"] and p["x|P4"]["mode"] == "single"


def test_reason_predicates_and_multiply_caused():
    b = {"trigger": [], "condition": [{"condition": "state", "entity_id": "light.l", "state": "on"}],
         "action": [{"action": "light.turn_off", "target": {"entity_id": "light.l"}}]}
    fired = {"x|P1": False, "x|P3": False, "x|P4": True}
    p = r9z.reason_predicates("x", b, fired)
    assert p == {"T": True, "C": True, "A": False}
    assert r9z.multiply_caused(p)
    # a condition naming the trigger cannot be judged on a time pattern; no conditions: C cannot hold
    b2 = {**b, "condition": [{"condition": "template", "value_template": "{{ trigger.to_state.state == 'on' }}"}]}
    assert r9z.reason_predicates("x", b2, fired)["C"] is None
    assert r9z.reason_predicates("x", {**b, "condition": []}, fired)["C"] is False
    b3 = {**b, "action": [{"action": "notify.x", "data": {"message": "{{ trigger.entity_id }}"}}]}
    assert r9z.reason_predicates("x", b3, {**fired, "x|P4": False})["A"] is None
    assert not r9z.multiply_caused({"T": True, "C": None, "A": False})


# ---- A19.4(c): environment-blocked propagation ----------------------------------------------------------------------

def _autos():
    # two flags, each firing only in sweep environment k1 (so "fires somewhere only")
    out = {}
    for name in ("f", "g"):
        o = {"k1": {"fired": True, "first_tod_h": 7.0, "flaps": False, "night_light": False, "night_cover": False},
             "k2": {"fired": False, "first_tod_h": None, "flaps": False, "night_light": False, "night_cover": False}}
        out[name] = {"bound": CLOCKED, "outcomes": o, "rich": {"k1": {"first_t": DAY + 7 * H}, "k2": {}},
                     "frame_divergences": ["fires_somewhere_only"], "divergences": ["fires_somewhere_only"]}
    return out


def _pair(v, h):
    return {"v_fired": v, "h_fired": h, "delta_s": 0.0 if v and h else None, "h_first": DAY + 7 * H if h else None}


def test_blocked_propagation_changes_nothing_when_every_compared_environment_agrees():
    envs = {"k1": JAN, "k2": JAN180}
    pairs = {"E1": [_pair(True, True), _pair(False, False)], "E2": [_pair(True, True), _pair(False, False)]}
    out = r9z.propagate_blocked(_autos(), ["f", "g"], pairs, envs, reps=20, seed=1)
    assert out["frame_changed"]["max"] == 0


def test_blocked_propagation_hits_every_flag_of_an_environment_together():
    envs = {"k1": JAN, "k2": JAN180}
    # E1: Home Assistant never fires; E2: it always agrees
    pairs = {"E1": [_pair(True, False), _pair(False, False)], "E2": [_pair(True, True), _pair(False, False)]}
    out = r9z.propagate_blocked(_autos(), ["f", "g"], pairs, envs, reps=200, seed=3, keep=True)
    changed = out["per_replicate"]
    assert all(c in (set(), {"f", "g"}) for c in changed)  # both or neither, never one
    assert any(changed) and not all(changed)


def test_environment_assignment_is_per_replicate_and_per_sweep_environment():
    rng = random.Random(0)
    a = r9z.assign_environments(rng, ["k1", "k2", "k3"], ["E1", "E2"])
    assert set(a) == {"k1", "k2", "k3"} and set(a.values()) <= {"E1", "E2"}


# ---- A19.1: the setback breakdown -----------------------------------------------------------------------------------

def test_repository_breakdown_counts_distinct_automations_per_repository():
    rows = [{"automation": "A1", "caused": True}, {"automation": "A1", "caused": True},
            {"automation": "A2", "caused": True}, {"automation": "A3", "caused": False},
            {"automation": "A4", "caused": True}]
    repo_of = {"A1": "R1", "A2": "R1", "A3": "R2", "A4": "R3"}
    out = r9z.repository_breakdown(rows, repo_of)
    assert out == {"automations": 3, "repositories": 2, "automations_per_repository": {"R1": 2, "R3": 1}}


def test_the_corners_are_r4b_corners_and_the_per_room_option_only_adds_the_thermostat():
    from vesper.env import indoor
    s = r9z.setback_settings()
    assert set(s) == {"hall-G2", "room-G2", "hall-G5", "room-G5"}
    assert s["hall-G2"]["INTERNAL_GAINS_W_PER_M2"] == 2.0 and s["hall-G5"]["INTERNAL_GAINS_W_PER_M2"] == 5.0
    for k in ("ROOF_GROUND", "PREWARM_DAYS", "CAPACITY_SCALE"):
        assert s["hall-G2"][k] == indoor.KNOB_DEFAULTS[k]
    assert s["room-G2"] == {**s["hall-G2"], "PER_ROOM_THERMOSTAT": 1}
    assert s["hall-G2"].get("PER_ROOM_THERMOSTAT", 0) == 0
    assert r9z.R4B_LABEL == {"G2": "G2-Foff-W1-C165", "G5": "G5-Foff-W1-C165"}
