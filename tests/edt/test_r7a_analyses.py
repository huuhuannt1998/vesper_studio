"""Revision R7-A's script (protocol Addendum 12): its witness rules, robust core, cover bounds, refusal table and
semantics counts, on toy inputs."""
import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def _load():
    sys.path.insert(0, str(ROOT / "scripts/revision"))
    spec = importlib.util.spec_from_file_location("r7a_analyses", ROOT / "scripts/revision/r7a_analyses.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r7a = _load()

from vesper.edt.oracle import frames_of, outcome, site_of  # noqa: E402
from vesper.edt.types import Env, Firing  # noqa: E402

DAY, H = 86400.0, 3600.0
N0 = Env("Stockholm, Sweden", "2026-01-14", 0.0, 4)
N180 = Env("Stockholm, Sweden", "2026-01-14", 180.0, 4)
JUL = Env("Stockholm, Sweden", "2026-07-15", 0.0, 4)
TEMP = "sensor.living_room_temperature"
HEAT = {"trigger": [{"platform": "numeric_state", "entity_id": TEMP, "above": 20}],
        "action": [{"action": "switch.turn_on", "target": {"entity_id": "switch.living_room_plug"}}]}
CLOCKED = {"trigger": [{"platform": "time", "at": "07:00"}], "action": HEAT["action"]}


def fire(t):
    return [Firing(t, "a", "switch.turn_on", "switch.living_room_plug")]


def test_orientation_pairs_differ_in_north_only():
    assert r7a.orient_pairs([N0, N180, JUL]) == [(N0, N180)]


def test_the_three_witness_rules():
    envs = [N0, N180, JUL]
    pairs = r7a.orient_pairs(envs)
    # fires 8 h apart between the orientations: a clock witness and an outcome-class witness; under the frame
    # relation a sensed-only automation is exempt (no witness), a clock-framed one is witnessed
    outs = {N0: outcome(fire(DAY + 6 * H), site_of(N0)), N180: outcome(fire(DAY + 14 * H), site_of(N180)),
            JUL: outcome(fire(DAY + 6 * H), site_of(JUL))}
    ft = {N0: DAY + 6 * H, N180: DAY + 14 * H, JUL: DAY + 6 * H}
    assert r7a.clock_witnessed(outs, pairs) and r7a.class_witnessed(outs, pairs)
    assert not r7a.frame_witnessed(outs, ft, frames_of(HEAT), pairs)
    assert r7a.frame_witnessed(outs, ft, frames_of(CLOCKED), pairs)
    # the orientations agree; only the date differs: no orientation witness under any rule
    same = {N0: outs[N0], N180: outcome(fire(DAY + 6 * H), site_of(N180)), JUL: outs[N180]}
    ft2 = {N0: DAY + 6 * H, N180: DAY + 6 * H, JUL: DAY + 14 * H}
    assert not r7a.clock_witnessed(same, pairs) and not r7a.class_witnessed(same, pairs)
    assert not r7a.frame_witnessed(same, ft2, frames_of(CLOCKED), pairs)


def test_robust_core_and_possible_set():
    per = {"a": {"f": {1, 2, 3}}, "b": {"f": {1, 2, 4}}, "nominal": {"f": {1, 2, 3, 5}}}
    out = r7a.core_and_possible(per, {"f": {1, 2, 3, 5}}, ("a", "nominal", "b"))
    assert out["f"] == {"published": 4, "robust_core": 2, "possible_set": 5, "possible_outside_published": 1}


def _toy_w():
    # 5 environments, 3 flags: flag 0 witnessed by (0, 1), flag 1 by (2, 3), flag 2 by (0, 4) and (3, 4)
    W = np.zeros((3, 5, 5), dtype=bool)
    for f, pairs in ((0, [(0, 1)]), (1, [(2, 3)]), (2, [(0, 4), (3, 4)])):
        for p, q in pairs:
            W[f, p, q] = W[f, q, p] = True
    return W


def test_cover_counts_and_bounds():
    W = _toy_w()
    assert r7a.covered(W, [0, 1, 2]) == 1
    assert r7a.covered(W, [0, 1, 4]) == 2
    assert r7a.pair_covered(W, [(0, 1), (3, 4)]) == 2
    best, envs = r7a.local_search(W, [0, 1, 2], 5)
    assert best == 2 and len(envs) == 3
    assert r7a.covered(W, [0, 2, 3, 4]) == 2
    assert r7a.milp_bound(W, 4, lp=False, time_limit=10)["objective"] == 2.0
    assert r7a.milp_bound(W, 5, lp=False, time_limit=10)["objective"] == 3.0  # all three need all five
    lp = r7a.milp_bound(W, 3, lp=True, time_limit=10)
    exact = r7a.milp_bound(W, 3, lp=False, time_limit=10)
    assert exact["objective"] == 2.0 and lp["objective"] >= exact["objective"]


def test_greedy_pairs_take_the_training_flags_first_then_the_smaller_keys():
    W = _toy_w()
    envs = [Env(c, "2026-01-14", 0.0, 4) for c in ("A", "B", "C", "D", "E")]
    train = np.array([False, True, True])
    assert r7a.greedy_pairs(W, train, envs, k=2) == [(0, 4), (2, 3)]  # a tie: keys A, E before C, D


def test_refusal_table_closes_with_an_other_line():
    autos = {
        "a": {"bound": {}, "outcomes": {}, "refused": {"e1": "it has no trigger"}},
        "b": {"bound": {}, "outcomes": {}, "refused": {"e2": "trigger mqtt is not supported (it needs an MQTT "
                                                             "message)", "e1": "it has no trigger"}},
        "c": {"bound": {}, "outcomes": {}, "refused": {"e1": "action parallel is not supported (parallel branches)"}},
        "d": {"bound": {}, "outcomes": {"e1": {}}, "refused": {"e2": "trigger event is not supported (x)"}},
        "e": {"bound": None, "outcomes": {}, "refused": {}},
    }
    t = r7a.refusal_table({"automations": autos})
    assert t["bound"] == 4 and t["run"] == 1 and t["refused_in_every_environment"] == 3
    assert t["refused_in_some_environments_only"] == 1 and t["bound_equals_run_plus_refused_everywhere"]
    assert t["by_class"]["it has no trigger"] == 2  # b's first reason in key order is e1's
    assert t["by_class"]["other"] == 1 and t["named_six_total"] == 2
    assert t["refused_everywhere_with_more_than_one_reason_class"] == 1


def test_semantics_reads_the_configuration_as_written():
    raw = {"mode": "restart", "max": 3, "initial_state": False, "trigger": [],
           "action": [{"choose": [{"conditions": [], "sequence": [{"delay": "00:01:00"}]}]},
                      {"action": "notify.x", "data": {"delay": 5}}]}
    s = r7a.semantics_of(raw)
    assert {"mode_not_single", "mode=restart", "max", "initial_state", "initial_state_off", "delay"} <= s
    assert "wait_for_trigger" not in s
    assert r7a.semantics_of({"mode": "single", "actions": [{"action": "notify.x", "data": {"delay": 1}}]}) == set()
    assert "wait_for_trigger" in r7a.semantics_of({"actions": [{"wait_for_trigger": []}]})
