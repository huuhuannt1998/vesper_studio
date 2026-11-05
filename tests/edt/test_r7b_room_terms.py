"""Revision R7-B's script (protocol Addendum 13): its settings, the families, the robust core and possible set, the
frame-relation orientation witness, and the second illuminance product's home and rebinding."""
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CAT = "catalog/vds"


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r7b_room_terms", ROOT / "scripts/revision/r7b_room_terms.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r7b = _load()


# ---- settings --------------------------------------------------------------------------------------------------------

def test_the_corner_settings_are_r4b_s_120_and_the_nominal_one_is_the_reference_at_the_defaults():
    from vesper.env import indoor
    s = r7b.corner_settings()
    assert len(s) == 120 and "ref" in s and s["ref"] == {}
    assert len({json.dumps(k, sort_keys=True) for k in s.values()}) == 120  # no two corners alike
    for name, kn in s.items():
        if name == "ref":
            continue
        assert kn and set(kn) <= set(indoor.KNOBS)
        assert any(indoor.KNOB_DEFAULTS[k] != v for k, v in kn.items())  # every other corner moves a knob
    worst = s[r7b.setting_name((0.0, 1, "pss", 80))]
    assert worst["PREWARM_DAYS"] == math.inf and worst["ROOF_GROUND"] == 1.0
    assert worst["CAPACITY_SCALE"] == pytest.approx(80 / 165)
    assert s[r7b.setting_name((5.0, 0, 10, 370))]["PREWARM_DAYS"] == 9.0  # 10 days, the run's first day one of them


def test_the_offset_settings_are_the_registered_values():
    s = r7b.offset_settings(r7b.OFFSETS_PRIMARY)
    assert list(s) == ["tin-1.7", "tin-0.9", "tin+2", "tin+7"]
    assert s["tin+7"] == {"INDOOR_T_OFFSET_C": 7.0}
    assert set(r7b.OFFSETS_PRIMARY) | set(r7b.OFFSETS_SECONDARY) == {-7.0, -2.0, -1.7, -0.9, 0.9, 1.7, 2.0, 7.0}


# ---- the bracket's sets ----------------------------------------------------------------------------------------------

def test_robust_core_is_the_intersection_and_possible_set_the_union_over_every_setting():
    members = {"ref": {"a", "b", "c"}, "s1": {"a", "b", "d"}, "s2": {"a", "c", "d", "e"}}
    out = r7b.core_and_possible(members, published={"a", "b", "c"})
    assert out["published"] == 3 and out["robust_core"] == 1 and out["possible_set"] == 5
    assert out["lost_somewhere"] == 2 and out["gained_somewhere"] == 2
    assert out["per_setting_min"] == 3 and out["per_setting_max"] == 4
    assert out["reference_equals_published"] is True
    assert r7b.core_and_possible({"ref": {"a"}}, published={"b"})["reference_equals_published"] is False


def _env(city, date, north):
    from vesper.edt.types import Env
    return Env(city, date, north, 4)


def _out(fired, tod=None):
    from vesper.edt.types import Outcome
    return Outcome(fired=fired, first_tod_h=tod, flaps=False, night_light=False, night_cover=False)


def test_a_frame_orientation_witness_needs_two_bearings_of_one_place_and_date_to_diverge_in_the_frame():
    from vesper.edt.oracle import frames_of
    bound = {"trigger": [{"platform": "time", "at": "07:00:00"}], "action": []}
    fr = frames_of(bound)
    a0, a1 = _env("X", "2026-01-14", 0.0), _env("X", "2026-01-14", 180.0)
    b0, b1 = _env("Y", "2026-01-14", 0.0), _env("Y", "2026-01-14", 180.0)
    ft = {e: None for e in (a0, a1, b0, b1)}
    # fires in one bearing and not the other: an orientation witness
    outs = {a0: _out(True, 7.0), a1: _out(False), b0: _out(False), b1: _out(False)}
    assert r7b.frame_orientation_witness(outs, ft, fr)
    # the only difference is across cities, not bearings
    outs = {a0: _out(True, 7.0), a1: _out(True, 7.0), b0: _out(False), b1: _out(False)}
    assert not r7b.frame_orientation_witness(outs, ft, fr)


# ---- the second illuminance product ----------------------------------------------------------------------------------

def test_swap_entities_replaces_whole_entity_ids_everywhere_and_nothing_else():
    mapping = {"sensor.living_lux0_illuminance": "sensor.living_lux1_illuminance"}
    bound = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.living_lux0_illuminance", "below": 30}],
             "condition": [{"condition": "template",
                            "value_template": "{{ states('sensor.living_lux0_illuminance') | int < 5 }}"},
                           {"condition": "state", "entity_id": ["sensor.living_lux0_illuminance_2",
                                                                "sensor.living_lux0_illuminance"], "state": "on"}],
             "action": [{"service": "light.turn_on", "target": {"entity_id": "light.living_light"}}],
             "alias": None, "n": 3}
    out = r7b.swap_entities(bound, mapping)
    assert out["trigger"][0]["entity_id"] == "sensor.living_lux1_illuminance"
    assert "sensor.living_lux1_illuminance" in out["condition"][0]["value_template"]
    assert "lux0" not in out["condition"][0]["value_template"]
    assert out["condition"][1]["entity_id"] == ["sensor.living_lux0_illuminance_2", "sensor.living_lux1_illuminance"]
    assert out["action"] == bound["action"] and out["n"] == 3 and out["alias"] is None
    assert bound["trigger"][0]["entity_id"] == "sensor.living_lux0_illuminance"  # the input is not changed
    assert r7b.swap_count(bound, mapping) == 3


@pytest.mark.skipif(not Path(CAT, "zigbee.json").is_file(), reason="needs catalog/vds")
def test_the_augmented_home_adds_one_light_sensor_beside_each_lux0_and_keeps_every_other_entity(tmp_path):
    from vesper.edt.home import build_edt_home
    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    from vesper.twins.home import load_catalog
    path, roles = build_edt_home(tmp_path / "w", CAT)
    home_yaml, mapping = r7b.augment_home(path.parent, tmp_path / "aug", r7b.LUX_PRODUCT, CAT)
    cat = load_catalog(Path(CAT))
    before = entity_ids(load_lab_home(path, cat))
    after = entity_ids(load_lab_home(home_yaml, cat))
    assert {k: v for k, v in after.items() if k in before} == before  # every existing entity id unchanged
    lux0 = sorted(n for n in before if n.endswith("_lux0"))
    assert sorted(n for n in after if n not in before) == sorted(n.replace("_lux0", "_lux1") for n in lux0)
    assert mapping == {roles[k]: after[k[1] + "_lux1"]["illuminance"] for k in roles if k[0] == "illuminance"}
    assert len(mapping) == len(lux0)
    devices = {d["name"]: d for d in yaml.safe_load(home_yaml.read_text())["devices"]}
    for n in lux0:
        new = devices[n.replace("_lux0", "_lux1")]
        assert new["product"] == r7b.LUX_PRODUCT and new["at"] == devices[n]["at"]
