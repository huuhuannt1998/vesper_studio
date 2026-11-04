"""vesper.edt.cover: which environments expose a flagged automation, the greedy cover, witnesses, the cover report."""
import hashlib

from vesper.edt.cover import (
    cover_report,
    coverage,
    exposing,
    greedy_cover,
    half,
    non_degeneracy,
    witnesses,
)
from vesper.edt.types import Env, Outcome

STO, PHX = "Stockholm, Sweden", "Phoenix, AZ, USA"
A, B, C, D = (Env(STO, "2026-01-15", 0.0), Env(STO, "2026-07-15", 0.0), Env(PHX, "2026-01-15", 0.0),
              Env(PHX, "2026-07-15", 0.0))
NO = Outcome(False, None, False, False, False)


def at(h, **kw):
    return Outcome(True, h, kw.get("flaps", False), kw.get("night_light", False), kw.get("night_cover", False))


def test_greedy_cover_picks_the_two_environments_a_four_automation_matrix_needs():
    flagged = {"a1": {A}, "a2": {A, B}, "a3": {C}, "a4": {C, D}}
    cover = greedy_cover(flagged, target=0.8)
    assert set(cover) == {A, C} and len(cover) == 2  # equal gains: the smaller key first
    assert cover == sorted(cover, key=lambda e: e.key)
    assert coverage([A, C], flagged) == 1.0
    assert coverage([B], flagged) == 0.25


def test_greedy_cover_stops_at_the_target_and_when_nothing_adds():
    flagged = {"a1": {A}, "a2": {A}, "a3": {A}, "a4": {B}, "a5": set()}
    assert greedy_cover(flagged, target=0.6) == [A]
    assert greedy_cover(flagged, target=1.0) == [A, B]  # a5 cannot be covered: stop, not loop
    assert greedy_cover({}, target=0.8) == []


def test_greedy_cover_breaks_ties_by_the_smaller_key():
    flagged = {"a1": {B}, "a2": {A}}
    first = greedy_cover(flagged, target=0.5)
    assert first == [min((A, B), key=lambda e: e.key)]


def test_non_degeneracy_flags_one_environment_covering_60_percent():
    flagged = {"a1": {A}, "a2": {A}, "a3": {A, B}, "a4": {C}, "a5": {D}}
    nd = non_degeneracy(flagged)
    assert nd["max_share"] == 0.6 and nd["env"] == A.key and nd["ok"] is False
    assert non_degeneracy({"a1": {A}, "a2": {B}})["ok"] is True


def test_witnesses_find_the_single_axis_that_flips_an_outcome():
    a0, a180 = A, Env(STO, "2026-01-15", 180.0)
    by_env = {a0: NO, a180: NO, B: at(22.0), C: NO}
    assert witnesses(by_env, [a0, a180, B, C]) == [(a0, B, "date")]


def test_witnesses_compare_times_beyond_three_hours_and_envelope_kinds():
    by_env = {A: at(14.0), B: at(16.5), C: at(19.0, night_light=True)}
    w = witnesses(by_env, [A, B, C])
    assert (A, C, "city") in w            # 5 h apart, and night_light differs
    assert all(pair[:2] != (A, B) for pair in w)  # 2.5 h, same envelope: no witness


def test_exposing_minority_side_of_fired():
    by_env = {A: NO, B: at(20.0), C: at(20.5), D: at(21.0)}
    assert exposing(by_env, {"fires_somewhere_only"}) == {A}
    tie = {A: NO, B: NO, C: at(20.0), D: at(20.0)}
    assert exposing(tie, {"fires_somewhere_only"}) == {A, B}  # a tie: the not-firing side


def test_exposing_outside_the_modal_three_hour_window():
    by_env = {A: at(14.0), B: at(15.0), C: at(16.5), D: at(21.0)}
    assert exposing(by_env, {"time_spread"}) == {D}
    wrap = {A: at(23.5), B: at(0.5), C: at(1.0), D: at(12.0)}  # the window wraps round midnight
    assert exposing(wrap, {"time_spread"}) == {D}


def test_exposing_envelope_kinds_where_they_hold():
    by_env = {A: at(20.0, night_light=True), B: at(20.0), C: at(20.0, flaps=True)}
    assert exposing(by_env, {"night_light", "flaps"}) == {A, C}


def _repo_in(parity):
    """A repository name whose sha256 has the given parity (0 even = train, 1 odd = held-out)."""
    for i in range(100):
        name = f"owner{i}/home"
        if int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16) % 2 == parity:
            return name
    raise AssertionError


def test_half_is_the_parity_of_the_repo_name_hash():
    assert half(_repo_in(0)) == "train" and half(_repo_in(1)) == "held_out"


def _entry(repo, outcomes, divergences):
    return {"repo": repo, "hash": "h", "kinds": ["illuminance"], "raw": {}, "bound": {}, "unbindable": None,
            "outcomes": {e.key: o.__dict__ for e, o in outcomes.items()}, "refused": {},
            "divergences": sorted(divergences), "static": [], "negative_control": False}


def test_cover_report_has_the_cover_the_held_out_coverage_baselines_and_witnesses():
    import dataclasses
    from vesper.validation.crosssite import CITIES
    envs = [Env(c, d, n) for c in CITIES for d in ("2026-01-15", "2026-07-15") for n in (0.0, 180.0)]
    train, held = _repo_in(0), _repo_in(1)
    sto_jan = Env(STO, "2026-01-15", 0.0)
    autos = {}
    for i, repo in enumerate([train, train, held, held]):
        outs = {e: at(17.0) for e in envs}
        outs[sto_jan] = NO  # never fires in Stockholm's January
        autos[f"a{i}"] = _entry(repo, outs, {"fires_somewhere_only"})
    autos["quiet"] = _entry(train, {e: NO for e in envs}, set())
    result = {"rung": 4, "envs": [{**dataclasses.asdict(e), "key": e.key} for e in envs], "automations": autos}
    rep = cover_report(result)
    assert rep["flagged"] == {"all": 4, "train": 2, "held_out": 2}
    assert rep["cover"]["envs"] == [sto_jan.key]
    assert rep["cover"]["coverage_train"] == 1.0 and rep["cover"]["coverage_held_out"] == 1.0
    assert rep["baselines"]["six_city_grid"]["size"] == 12
    assert rep["baselines"]["six_city_grid"]["coverage_held_out"] == 1.0
    assert 0.0 < rep["baselines"]["random_5"]["coverage_held_out"] < 1.0
    assert rep["baselines"]["random_5"]["draws"] == 100 and rep["baselines"]["random_5"]["seed"] == 0
    assert rep["non_degeneracy"]["max_share"] == 1.0 and rep["non_degeneracy"]["ok"] is False
    w = rep["witnesses"]["a0"]
    assert w["axis"] == "north_deg" and sto_jan.key in (w["a"], w["b"])
    assert rep["witnesses_by_axis"]["north_deg"] == 4
    assert cover_report(result) == rep  # deterministic
