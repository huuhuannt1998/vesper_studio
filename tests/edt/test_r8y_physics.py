"""Revision R8-Y's script (protocol Addendum 18): its settings, the per-run summaries (coldest room and its identity),
nesting, and the core over plans."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r8y_physics", ROOT / "scripts/revision/r8y_physics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r8y = _load()


def test_the_settings_are_the_u_corner_under_each_thermostat_and_the_reference_is_the_model_as_built():
    from vesper.env import indoor
    s = r8y.setback_settings()
    assert len(s) == 18
    ref = s[r8y.setting_label(False, 0.35, 1.4)]
    assert all(indoor.KNOB_DEFAULTS[k] == v for k, v in ref.items())  # every knob at its default
    room = s[r8y.setting_label(True, 0.35, 1.4)]
    assert room["PER_ROOM_THERMOSTAT"] == 1 and room["U_WALL"] == 0.35 and room["U_WINDOW"] == 1.4
    leaky = s[r8y.setting_label(False, 1.0, 2.8)]
    assert (leaky["U_WALL"], leaky["U_WINDOW"], leaky["PER_ROOM_THERMOSTAT"]) == (1.0, 2.8, 0)
    assert set(r8y.setting_label(p, w, g) for p in (False, True) for w in r8y.U_WALLS for g in r8y.U_GLASS) == set(s)


def _row(aid, key, cells, caused_rooms=(), setback=((0, 3600),)):
    caused = {r: (m, b, r in caused_rooms) for r, (m, b) in cells.items()}
    hit = [cells[r][0] for r in caused_rooms]
    return {"automation": aid, "_aid": aid, "_key": key, "env": key,
            "city": "Minneapolis" if "M" in key else "Stockholm",
            "caused": bool(caused_rooms), "caused_cells": len(caused_rooms),
            "coldest_caused_room_c": min(hit) if hit else None,
            "coldest_room_c": min(m for m, _ in cells.values()),
            "baseline_coldest_room_c": min(b for _, b in cells.values()), "largest_drop_k": 1.0,
            "_cells": caused, "_setback": list(setback)}


def test_the_coldest_room_is_the_lowest_minimum_and_ties_go_to_the_first_room_id():
    assert r8y.coldest_room(_row("A1", "M", {"living": (8.0, 16.0), "bedroom_1": (6.0, 15.0)})) == "bedroom_1"
    assert r8y.coldest_room(_row("A1", "M", {"office": (6.0, 16.0), "kitchen": (6.0, 15.0)})) == "kitchen"


def test_the_summary_counts_breaches_coldest_rooms_and_identities():
    rows = [_row("A1", "M", {"living": (8.0, 16.0), "bedroom_1": (6.0, 15.0)}, ("living", "bedroom_1")),
            _row("A2", "M", {"living": (12.0, 16.0), "bedroom_1": (11.0, 15.0)}),
            _row("A1", "S", {"living": (9.0, 16.0), "bedroom_1": (14.0, 15.0)}, ("living",), setback=())]
    s = r8y.summary(rows, {"M", "S"}, 2)
    assert s["breaching_runs"] == 2 and s["denominator"] == 4 and s["caused_cells"] == 3 and s["automations"] == 1
    assert s["coldest_caused_room_per_run_c"] == [6.0, 7.5, 9.0]
    assert s["coldest_room_per_run_c"] == [6.0, 9.0, 11.0]
    assert s["coldest_room_identity"] == {"bedroom_1": 2, "living": 1}
    assert s["caused_cells_by_room"] == {"bedroom_1": 1, "living": 2}
    assert s["runs_with_a_setback"] == 2 and s["by_city"] == {"Minneapolis": 1, "Stockholm": 1}


def test_the_identity_shift_compares_the_same_runs_under_two_settings():
    a = [_row("A1", "M", {"living": (8.0, 16.0), "bedroom_1": (6.0, 15.0)}, ("bedroom_1",)),
         _row("A2", "M", {"living": (12.0, 16.0), "bedroom_1": (11.0, 15.0)})]
    b = [_row("A1", "M", {"living": (6.5, 16.0), "bedroom_1": (6.7, 15.0)}, ("living", "bedroom_1")),
         _row("A2", "M", {"living": (12.0, 16.0), "bedroom_1": (11.5, 15.0)})]
    sh = r8y.identity_shift(a, b)
    assert sh["runs"] == 2 and sh["coldest_room_changed"] == 1 and sh["breaching_in_either"] == 1
    assert sh["coldest_room_changed_among_breaching_in_either"] == 1
    assert sh["coldest_room_change_c"] == [0.5, 0.5, 0.5]  # 6.5 - 6.0 and 11.5 - 11.0


def test_nesting_reads_subset_order_along_an_axis():
    assert r8y.nested([{1}, {1, 2}, {1, 2, 3}]) is True
    assert r8y.nested([{1, 2}, {1}, {1, 3}]) is False


def test_the_core_over_plans_is_the_published_witnesses_held_in_every_plan():
    pub = {"a", "b", "c", "d"}
    plans = {"p1": {"a", "b", "c", "x"}, "p2": {"a", "b", "y"}, "p3": {"a", "b", "d"}}
    c = r8y.core_over(pub, plans)
    assert c["core"] == 2 and c["possible"] == 6 and c["of"] == 4 and c["new_in_any"] == 2
    assert c["held_in"] == {"0": 0, "1": 2, "2": 0, "3": 2}  # c and d in one plan each; a and b in all three
