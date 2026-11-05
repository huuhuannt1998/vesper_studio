"""Revision R8-X's script (protocol Addendum 17): the joint robust core, the first-named sun reference, the residual
exposure of the sensed exemption, the location precision and the extended binder discount, and the rate check, on toy
inputs."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r8x_analyses", ROOT / "scripts/revision/r8x_analyses.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r8x = _load()

from dataclasses import asdict  # noqa: E402

from vesper.edt.oracle import SunRef, _ref_instant, frame_divergences, frames_of, outcome, site_of  # noqa: E402
from vesper.edt.types import Env, Firing  # noqa: E402

JAN = Env("Stockholm, Sweden", "2026-01-14", 0.0, 4)
JUL = Env("Stockholm, Sweden", "2026-07-15", 0.0, 4)
LAMP = [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}]
BOTH = {"trigger": [{"platform": "sun", "event": "sunrise"}, {"platform": "sun", "event": "sunset"}], "action": LAMP}
TWO_OFFSETS = {"trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:30:00"},
                           {"platform": "sun", "event": "sunset"}], "action": LAMP}
ONE = {"trigger": [{"platform": "sun", "event": "sunset"}], "action": LAMP}
TEMP = "sensor.living_room_temperature"
SENSED = {"trigger": [{"platform": "state", "entity_id": TEMP}], "action": LAMP}
SENSED_CLOCK = {"trigger": [{"platform": "state", "entity_id": TEMP}, {"platform": "time", "at": "07:00"}],
                "action": LAMP}
SENSED_NUMERIC = {"trigger": [{"platform": "numeric_state", "entity_id": TEMP, "above": 20}], "action": LAMP}
SUN_NO_REF = {"trigger": [{"platform": "template", "value_template": "{{ state_attr('sun.sun', 'elevation') < 3 }}"}],
              "action": LAMP}
CLOCKED = {"trigger": [{"platform": "time", "at": "07:00"}], "action": LAMP}
NO_FRAME = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.front_door"}], "action": LAMP}


# ---- A17.1 / A17.2: families, joint core, possible set ---------------------------------------------------------------

def test_families_come_from_one_record_per_automation():
    rec = {"a": {"frame": ["time_spread"], "witness": True}, "b": {"frame": ["fires_somewhere_only"], "witness": False},
           "c": {"frame": [], "witness": False}}
    kinds = {"a": {"temperature"}, "b": {"humidity", "sun"}, "c": {"temperature"}}
    fam = r8x.families_of(rec, kinds)
    assert fam == {"frame_flags": {"a", "b"}, "orientation_witnesses": {"a"}, "temperature_flags": {"a"},
                   "humidity_flags": {"b"}}


def test_joint_core_is_the_and_over_every_setting_of_every_source_and_the_possible_set_the_or():
    pub = {"f": {1, 2, 3, 4}}
    per = {"A": {"nominal": {"f": {1, 2, 3, 4}}, "x": {"f": {1, 2, 3, 9}}},
           "B": {"c1": {"f": {1, 2, 4}}, "c2": {"f": {1, 2, 3, 4, 8}}},
           "W": {"sw": {"f": {2, 3, 4}}}}
    out = r8x.joint(per, pub)["f"]
    assert out["published"] == 4
    assert out["joint_core"] == 1 and out["joint_possible"] == 6  # {2}; {1,2,3,4,8,9}
    assert out["settings"] == 5
    assert out["own_core"] == {"A": 3, "B": 3, "W": 3}  # {1,2,3}, {1,2,4}, {2,3,4}
    assert out["core_without"] == {"A": 2, "B": 2, "W": 2}  # {2,4}, {2,3}, {1,2}
    assert out["possible_outside_published"] == 2


def test_the_core_ignores_members_outside_the_published_family():
    out = r8x.joint({"A": {"s": {"f": {1, 7}}}}, {"f": {1, 2}})["f"]
    assert out["joint_core"] == 1 and out["joint_possible"] == 2 and out["possible_outside_published"] == 1


def test_membership_record_from_outcomes_matches_the_oracle():
    # a clock-framed automation firing 8 h apart over two bearings of one place and date is witnessed; a sensed-only
    # one with the same firings is exempt from time_spread and so neither flagged nor witnessed
    n0, n180 = JAN, Env("Stockholm, Sweden", "2026-01-14", 180.0, 4)
    by_key = {n0.key: n0, n180.key: n180}
    day, h = 86400.0, 3600.0
    outs = {n0.key: asdict(outcome([Firing(day + 6 * h, "a", "light.turn_on", "light.porch")], site_of(n0))),
            n180.key: asdict(outcome([Firing(day + 14 * h, "a", "light.turn_on", "light.porch")], site_of(n180)))}
    ft = {n0.key: day + 6 * h, n180.key: day + 14 * h}
    pairs = [(n0, n180)]
    rec = r8x.record(CLOCKED, outs, ft, by_key, pairs)
    assert rec["frame"] == ["time_spread"] and rec["witness"] is True
    rec = r8x.record(SENSED, outs, ft, by_key, pairs)
    assert rec["frame"] == [] and rec["witness"] is False


# ---- A17.3: the sun reference --------------------------------------------------------------------------------------

def test_ambiguity_counts_distinct_instants_and_distinct_events():
    assert r8x.sun_ambiguity(frames_of(BOTH)) == (True, True)
    assert r8x.sun_ambiguity(frames_of(TWO_OFFSETS)) == (True, False)
    assert r8x.sun_ambiguity(frames_of(ONE)) == (False, False)
    assert r8x.sun_ambiguity(frames_of(CLOCKED)) == (False, False)


def test_first_named_keeps_the_frames_and_the_first_reference_only():
    fr = frames_of(BOTH)
    first = r8x.first_named(fr)
    assert first.frames == fr.frames and len(fr.sun_refs) == 2
    assert first.sun_refs == fr.sun_refs[:1] and first.sun_refs[0].event == "sunrise"
    assert r8x.first_named(frames_of(CLOCKED)) == frames_of(CLOCKED)


def test_the_first_named_reference_can_spread_where_the_nearest_does_not():
    # fires at sunset in January and at sunrise in July: 0 min from the nearest named instant in both, but several
    # hours from sunrise in January
    def at(env, event):
        s = site_of(env)
        return _ref_instant(s.lat, s.lon, s.elevation_m, s.tz, s.date.isoformat(), 1,
                            SunRef(event))
    t_jan, t_jul = at(JAN, "sunset"), at(JUL, "sunrise")
    by_env = {JAN: outcome([Firing(t_jan, "a", "light.turn_on", "light.porch")], site_of(JAN)),
              JUL: outcome([Firing(t_jul, "a", "light.turn_on", "light.porch")], site_of(JUL))}
    ft = {JAN: t_jan, JUL: t_jul}
    fr = frames_of(BOTH)
    assert "time_spread" not in frame_divergences(by_env, ft, fr)
    assert "time_spread" in frame_divergences(by_env, ft, r8x.first_named(fr))


# ---- A17.4: reference-free frames ----------------------------------------------------------------------------------

def test_reference_free_frames():
    assert r8x.reference_free(frames_of(SENSED)) == (True, True)
    assert r8x.reference_free(frames_of(SENSED_CLOCK)) == (False, True)
    assert r8x.reference_free(frames_of(SUN_NO_REF)) == (True, True)
    assert r8x.reference_free(frames_of(ONE)) == (False, False)
    assert r8x.reference_free(frames_of(CLOCKED)) == (False, False)
    assert r8x.reference_free(frames_of(NO_FRAME)) == (False, False)  # judged in the clock frame


def test_crossing_construction_reaches_a_numeric_trigger_only():
    assert r8x.in_crossing(SENSED_NUMERIC) and not r8x.in_crossing(SENSED)


# ---- A17.5: location precision and the extended discount -----------------------------------------------------------

def test_location_precision_with_wilson_and_a_repository_bootstrap():
    labels = {f"a{k}": "located" for k in range(8)} | {"m1": "mislocated", "m2": "mislocated", "u": "uncertain"}
    repo = {i: f"r{k % 4}" for k, i in enumerate(sorted(labels))}
    out = r8x.location_precision(labels, repo, boot=200, seed=0)
    assert out["located"] == 8 and out["mislocated"] == 2 and out["uncertain"] == 1
    assert out["precision"] == 0.8 and out["precision_uncertain_as_mislocated"] == round(8 / 11, 4)
    lo, hi = out["wilson_95"]
    assert lo < 0.8 < hi
    blo, bhi = out["repo_bootstrap_95"]
    assert 0.0 <= blo <= 0.8 <= bhi <= 1.0
    assert r8x.location_precision(labels, repo, boot=200, seed=0) == out  # seeded


def test_extended_discount_takes_the_mislocated_sun_readers_off_the_undiscounted_part():
    assert r8x.extended_point(158.5, 0) == 158.5
    assert r8x.extended_point(158.5, 3) == 155.5


def test_discount_bootstrap_rescales_each_part_to_its_size():
    # 4 sensed-frame flags (1 mis-bind, all covered) and 2 sun readers (1 mislocated), 13 others undiscounted
    sensed = {"s1": "misbind", "s2": "correct", "s3": "correct", "s4": "correct"}
    sun = {"u1": "mislocated", "u2": "located"}
    repo = {"s1": "r1", "s2": "r1", "s3": "r2", "s4": "r3", "u1": "r2", "u2": "r4"}
    out = r8x.discount_bootstrap(sensed, sun, repo, others=13, boot=300, seed=0)
    assert out["point"] == 13 + 3 + 1
    lo, hi = out["repo_bootstrap_95"]
    assert 13 <= lo <= out["point"] <= hi <= 13 + 4 + 2


# ---- A17.6: rate check -------------------------------------------------------------------------------------------

def test_rate_check_against_the_stated_rounding():
    assert r8x.rate_ok(771, 1205, 64.0, 1)
    assert not r8x.rate_ok(790, 1205, 64.0, 1)
    assert r8x.rate_ok(185, 199, 93, 0)
