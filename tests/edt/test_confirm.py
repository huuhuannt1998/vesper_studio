"""vesper.edt.confirm: run screened automations for real (closed loop) and compare with the open-loop outcome."""
import random

import pytest

from vesper.edt import confirm as cf
from vesper.edt.home import build_edt_home
from vesper.edt.types import Env, Outcome

CITY = "Stockholm, Sweden"
ENV_A = Env(CITY, "2026-01-15", 0.0, 4)
ENV_B = Env(CITY, "2026-07-15", 180.0, 4)
ENV_C = Env("Singapore", "2026-04-15", 0.0, 4)
CAT = "catalog/vds"


def out(fired=True, tod=8.0, flaps=False, night_light=False, night_cover=False):
    return {"fired": fired, "first_tod_h": tod if fired else None, "flaps": flaps, "night_light": night_light,
            "night_cover": night_cover}


def entry(outcomes, divergences=(), bound=True, **kw):
    return {"repo": "r", "hash": "h", "kinds": [], "raw": {}, "bound": {"alias": "x"} if bound else None,
            "unbindable": None if bound else "no", "outcomes": outcomes, "divergences": list(divergences),
            "negative_control": False, **kw}


def sweep(automations, envs=(ENV_A, ENV_B, ENV_C)):
    return {"rung": 4, "envs": [{"city": e.city, "date": e.date, "north_deg": e.north_deg, "rung": e.rung,
                                 "key": e.key} for e in envs],
            "automations": automations, "negative_control_rate": 0.0}


# -- comparing outcome classes ------------------------------------------------------------------
def test_agree_when_the_classes_match_and_first_time_is_within_an_hour():
    assert cf.disagreements(out(tod=8.0), Outcome(True, 8.9, False, False, False)) == []


def test_first_time_more_than_an_hour_apart_disagrees():
    assert cf.disagreements(out(tod=8.0), Outcome(True, 9.2, False, False, False)) == ["first_tod_h"]


def test_first_time_is_compared_the_short_way_round_the_clock():
    assert cf.disagreements(out(tod=23.8), Outcome(True, 0.3, False, False, False)) == []


def test_fired_and_flags_are_compared_exactly():
    assert cf.disagreements(out(fired=False), Outcome(True, 8.0, False, False, False)) == ["fired", "first_tod_h"]
    assert cf.disagreements(out(), Outcome(True, 8.0, True, True, False)) == ["flaps", "night_light"]


def test_two_runs_that_never_fire_agree():
    assert cf.disagreements(out(fired=False), Outcome(False, None, False, False, False)) == []


# -- the sample ---------------------------------------------------------------------------------
def test_flagged_run_in_the_environments_that_differ_from_the_most_common_outcome():
    s = sweep({"f": entry({ENV_A.key: out(fired=False), ENV_B.key: out(), ENV_C.key: out()}, ["fires_somewhere_only"])})
    picks = cf.pick(s, sample=5, rng=random.Random(0))
    assert [(p["id"], p["env_key"], p["flagged"]) for p in picks] == [("f", ENV_A.key, True)]


def test_unflagged_run_in_two_random_environments():
    s = sweep({"u": entry({e.key: out() for e in (ENV_A, ENV_B, ENV_C)})})
    picks = cf.pick(s, sample=5, rng=random.Random(0))
    assert len(picks) == 2 and {p["id"] for p in picks} == {"u"} and not any(p["flagged"] for p in picks)
    assert len({p["env_key"] for p in picks}) == 2


def test_sample_is_per_group_deterministic_and_skips_unbindable():
    autos = {f"f{i}": entry({ENV_A.key: out(), ENV_B.key: out(fired=False)}, ["fires_somewhere_only"])
             for i in range(6)}
    autos |= {f"u{i}": entry({ENV_A.key: out(), ENV_B.key: out()}) for i in range(6)}
    autos["x"] = entry({}, ["flaps"], bound=False)
    s = sweep(autos)
    a = cf.pick(s, sample=3, rng=random.Random(7))
    assert a == cf.pick(s, sample=3, rng=random.Random(7))
    ids = {p["id"] for p in a}
    assert len([i for i in ids if i.startswith("f")]) == 3 and len([i for i in ids if i.startswith("u")]) == 3
    assert "x" not in ids


def test_flagged_with_no_environment_differing_falls_back_to_two_random():
    s = sweep({"f": entry({ENV_A.key: out(tod=1.0), ENV_B.key: out(tod=1.5), ENV_C.key: out(tod=2.0)},
                          ["time_spread"])})
    picks = cf.pick(s, sample=5, rng=random.Random(0))
    assert len(picks) == 2 and all(p["flagged"] for p in picks)


# -- the temperature envelope ---------------------------------------------------------------------
def test_envelope_counts_only_the_measured_days_and_the_controlled_rooms():
    day = 86400.0
    temps = {"corridor": [(0.0, 5.0), (day + 60, 21.0), (day + 120, 9.5), (2 * day + 60, 31.0), (3 * day, 40.0)],
             "living": [(day + 60, 2.0)]}
    r = cf.envelope(temps, ["corridor"])
    assert r == {"corridor": {"min_c": 9.5, "max_c": 31.0, "below_10": True, "above_30": True}}


def test_envelope_with_a_baseline_counts_only_the_breaches_the_automation_caused():
    day = 86400.0
    temps = {"hot_anyway": [(day + 60, 33.0), (day + 120, 35.0)], "caused": [(day + 60, 31.0), (day + 120, 32.0)],
             "cold_anyway": [(day + 60, 4.0)], "cold_caused": [(day + 60, 8.0)], "fine": [(day + 60, 20.0)]}
    base = {"hot_anyway": [(day + 60, 31.0), (day + 120, 34.0)], "caused": [(day + 60, 22.0), (day + 120, 24.0)],
            "cold_anyway": [(day + 60, 3.0)], "cold_caused": [(day + 60, 15.0)], "fine": [(day + 60, 18.0)]}
    r = cf.envelope(temps, list(temps), baseline=base)
    assert r["hot_anyway"] == {"min_c": 33.0, "max_c": 35.0, "baseline_min_c": 31.0, "baseline_max_c": 34.0,
                               "delta_min_c": 2.0, "delta_max_c": 1.0, "below_10": False, "above_30": False,
                               "breached_below_10": False, "breached_above_30": True}
    assert r["caused"]["above_30"] is True and r["caused"]["breached_above_30"] is True
    assert r["caused"]["delta_max_c"] == 8.0
    assert r["cold_anyway"]["below_10"] is False and r["cold_anyway"]["breached_below_10"] is True
    assert r["cold_caused"]["below_10"] is True
    assert r["fine"]["below_10"] is r["fine"]["above_30"] is False


def test_a_baseline_with_no_reading_for_a_room_makes_every_breach_the_automations():
    day = 86400.0
    r = cf.envelope({"a": [(day + 1, 35.0)]}, ["a"], baseline={})
    assert r["a"]["above_30"] is True and r["a"]["baseline_max_c"] is None and r["a"]["delta_max_c"] is None


def test_envelope_without_readings_is_none():
    assert cf.envelope({}, ["corridor"]) == {"corridor": None}


# -- the closed loop ------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def edt(tmp_path_factory):
    work = tmp_path_factory.mktemp("edt")
    home_yaml, roles = build_edt_home(work, CAT)
    return work, home_yaml, roles


def toggle_pair(roles):
    """One automation, in the evening: dusk -> toggle the lamp, light -> toggle it again. Its own lamp lifts the lux
    it reacts to, so the lamp it turns on is what it then sees as light."""
    lux = roles[("illuminance", "living", 0)]
    lamp = roles[("light", "living", 0)]
    return {"alias": "lamp pair",
            "triggers": [{"trigger": "numeric_state", "entity_id": lux, "below": 100},
                         {"trigger": "numeric_state", "entity_id": lux, "above": 100}],
            "conditions": [{"condition": "time", "after": "12:00", "before": "23:00"}],
            "actions": [{"action": "light.toggle", "target": {"entity_id": lamp}}]}


def test_a_lamp_pair_flaps_closed_loop_but_not_open_loop(edt):
    work, _, roles = edt
    open_loop = out(fired=True, tod=14.0, flaps=False)  # the recorded lux falls below 100 once, at dusk
    s = sweep({"pair": entry({ENV_A.key: open_loop}, ["flaps"]) | {"bound": toggle_pair(roles)}}, envs=(ENV_A,))
    report = cf.confirm(s, work / "run", sample=1, seed=0, cache=work / "cache", days=2)
    assert report["runs"] == 1
    assert report["agreement"]["overall"] == 0.0
    [d] = report["disagreements"]
    assert d["id"] == "pair" and d["env_key"] == ENV_A.key and "flaps" in d["fields"]
    assert d["closed"]["flaps"] is True and d["open"]["flaps"] is False


def test_a_time_automation_agrees_and_its_report_counts_the_groups(edt):
    work, _, roles = edt
    lamp = roles[("light", "living", 0)]
    auto = {"alias": "morning lamp", "triggers": [{"trigger": "time", "at": "08:00:00"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": lamp}}]}
    # the lamp it turns on is never turned off: a lamp left on all night, open loop and closed
    s = sweep({"clock": entry({ENV_A.key: out(tod=8.0, night_light=True), ENV_B.key: out(tod=8.0, night_light=True)})
                    | {"bound": auto}},
              envs=(ENV_A, ENV_B))
    report = cf.confirm(s, work / "run2", sample=5, seed=3, cache=work / "cache", days=3, workers=2)
    assert report["runs"] == 2 and report["agreement"]["overall"] == 1.0
    assert report["agreement"]["flagged"] is None and report["agreement"]["unflagged"] == 1.0
    assert report["disagreements"] == [] and report["envelope"]["runs_with_climate_targets"] == 0


def test_actions_the_engine_cannot_run_are_recorded_and_climate_rooms_are_measured(edt):
    work, home_yaml, roles = edt
    auto = {"alias": "heat", "triggers": [{"trigger": "time", "at": "06:00:00"}],
            "actions": [{"action": "climate.set_temperature", "target": {"entity_id": roles[("climate", "office", 0)]},
                         "data": {"temperature": 22}},
                        {"action": "cover.open_cover", "target": {"entity_id": "cover.blind"}}]}
    r = cf.closed_loop(auto, ENV_A, home_yaml, work / "cache", days=3)
    assert r["refused"] == [] and r["outcome"].fired is True
    assert {f.service for f in r["firings"]} == {"climate.set_temperature", "cover.open_cover"}
    # the hallway thermostat heats every indoor room: the envelope covers them all, per room (not the porch)
    assert r["rooms"] == sorted({rid for (_, rid, _) in roles} - {"porch"})
    env = cf.envelope(r["temperatures"], r["rooms"])
    assert env["corridor"]["min_c"] < env["corridor"]["max_c"] and env["corridor"]["above_30"] is False
    assert set(env) == set(r["rooms"]) and all(v is not None for v in env.values())


def set_temperature(roles_or_id, value, at="08:00:00"):
    climate = roles_or_id if isinstance(roles_or_id, str) else roles_or_id[("climate", "office", 0)]
    return {"alias": f"set {value}", "triggers": [{"trigger": "time", "at": at}],
            "actions": [{"action": "climate.set_temperature", "target": {"entity_id": climate},
                         "data": {"temperature": value}}]}


def test_a_climate_automation_changes_the_room_temperature_in_the_closed_loop(edt):
    work, home_yaml, roles = edt
    cold = Env(CITY, "2026-01-15", 0.0, 4)
    base = cf.closed_loop(set_temperature(roles, 20), cold, home_yaml, work / "cache")
    warm = cf.closed_loop(set_temperature(roles, 28), cold, home_yaml, work / "cache")
    assert base["refused"] == warm["refused"] == []
    b, w = cf.envelope(base["temperatures"], base["rooms"]), cf.envelope(warm["temperatures"], warm["rooms"])
    assert set(b) == set(w) and "living" in w
    for room in w:  # every room the hallway thermostat heats
        assert w[room]["min_c"] > b[room]["max_c"] + 3, room
    assert not any(v["below_10"] for v in b.values())


@pytest.fixture(scope="module")
def hot_home(edt):
    """The EDT home with a thermostat that goes to 35 (the starter's stops at 30), and its climate entity."""
    import shutil
    from pathlib import Path

    import yaml

    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    from vesper.twins.home import load_catalog
    work, home_yaml, _ = edt
    out = work / "hot"
    shutil.copytree(home_yaml.parent, out)
    data = yaml.safe_load((out / "home.yaml").read_text())
    for d in data["devices"]:
        if d["name"] == "corridor_thermostat":
            d["product"] = "zigbee:sonoff:trvzb"
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    ids = entity_ids(load_lab_home(out / "home.yaml", load_catalog(Path(CAT))))
    return out / "home.yaml", next(e for e in ids["corridor_thermostat"].values() if e.startswith("climate."))


def test_a_setting_above_30_gives_an_above_30_envelope_violation(edt, hot_home):
    work, _, _ = edt
    home_yaml, climate = hot_home
    cold = Env(CITY, "2026-01-15", 0.0, 4)
    r = cf.closed_loop(set_temperature(climate, 35), cold, home_yaml, work / "cache")
    base = cf.baseline_temperatures(cold, home_yaml, work / "cache")
    env = cf.envelope(r["temperatures"], r["rooms"], baseline=base)
    assert r["refused"] == [] and all(v["above_30"] and v["breached_above_30"] for v in env.values())
    assert all(v["delta_max_c"] > 10 for v in env.values())  # 35 against the starter's 20
    assert not any(v["below_10"] for v in env.values())


def test_a_setting_the_thermostat_refuses_leaves_the_rooms_near_their_baseline(edt):
    work, home_yaml, roles = edt
    cold = Env(CITY, "2026-01-15", 0.0, 4)
    r = cf.closed_loop(set_temperature(roles, 35), cold, home_yaml, work / "cache")  # the starter stops at 30
    env = cf.envelope(r["temperatures"], r["rooms"])
    assert r["firings"] and not any(v["above_30"] for v in env.values())


def test_28_in_stockholm_counts_the_rooms_it_pushed_over_30_and_the_baseline_is_the_home_without_it(edt):
    work, home_yaml, roles = edt
    cold = Env(CITY, "2026-01-15", 0.0, 4)
    r = cf.closed_loop(set_temperature(roles, 28), cold, home_yaml, work / "cache")
    base = cf.baseline_temperatures(cold, home_yaml, work / "cache")
    assert set(base) == set(r["rooms"]) and base is cf.baseline_temperatures(cold, home_yaml, work / "cache")
    env = cf.envelope(r["temperatures"], r["rooms"], baseline=base)
    assert max(v["baseline_max_c"] for v in env.values()) < 30  # nothing is hot without the automation
    over = {room for room, v in env.items() if v["above_30"]}
    assert "kitchen" in over and over == {room for room, v in env.items() if v["breached_above_30"]}
    assert all(v["delta_max_c"] > 3 for v in env.values())


@pytest.fixture(scope="module")
def warm_home(edt):
    """The EDT home whose thermostat already starts at 28: its baseline is hot in the sunny rooms too."""
    import shutil

    import yaml
    work, home_yaml, _ = edt
    out = work / "warm"
    shutil.copytree(home_yaml.parent, out)
    data = yaml.safe_load((out / "home.yaml").read_text())
    for d in data["devices"]:
        if d["name"] == "corridor_thermostat":
            d["state"] = {"current_heating_setpoint": 28}
    (out / "home.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return out / "home.yaml"


def test_a_room_above_30_in_the_baseline_as_well_is_not_counted(edt, warm_home):
    work, _, roles = edt
    cold = Env(CITY, "2026-01-15", 0.0, 4)
    r = cf.closed_loop(set_temperature(roles, 28), cold, warm_home, work / "cache")
    base = cf.baseline_temperatures(cold, warm_home, work / "cache")
    env = cf.envelope(r["temperatures"], r["rooms"], baseline=base)
    hot_anyway = {room for room, v in env.items() if v["baseline_max_c"] > 30}
    assert "kitchen" in hot_anyway
    for room in hot_anyway:
        assert env[room]["breached_above_30"] is True and env[room]["above_30"] is False, room
    assert not any(v["above_30"] for v in env.values())  # the automation changed nothing here


def test_the_baseline_cache_follows_the_homes_contents_and_each_confirm_starts_it_empty(edt, warm_home, tmp_path):
    import shutil
    work, home_yaml, roles = edt
    cold = Env(CITY, "2026-01-15", 0.0, 4)
    copy = tmp_path / "home"
    shutil.copytree(home_yaml.parent, copy)
    first = cf.baseline_temperatures(cold, copy / "home.yaml", work / "cache")
    (copy / "home.yaml").write_text((warm_home).read_text())  # the same path, a different home
    second = cf.baseline_temperatures(cold, copy / "home.yaml", work / "cache")
    assert max(c for _, c in first["kitchen"]) < max(c for _, c in second["kitchen"]) - 3
    cf._BASELINES["sentinel"] = {}
    s = sweep({"u": entry({ENV_A.key: out(tod=8.0)}) | {"bound": set_temperature(roles, 21)}}, envs=(ENV_A,))
    cf.confirm(s, work / "run4", sample=1, seed=0, cache=work / "cache", days=3)
    assert "sentinel" not in cf._BASELINES


def test_the_report_counts_envelope_violations_per_run_and_room():
    ok = {"min_c": 18.0, "max_c": 22.0, "below_10": False, "above_30": False}
    hot = {"min_c": 29.0, "max_c": 36.0, "below_10": False, "above_30": True}
    row = {"id": "a", "env_key": "k", "flagged": False, "open": {}, "closed": {}, "fields": [], "refused": [],
           "firings": 1, "envelope": {"living": hot, "office": ok}}
    r = cf._report([row, {**row, "env_key": "k2", "envelope": {"living": ok}}], seed=0, sample=1, days=3)
    assert r["envelope"]["runs_with_climate_targets"] == 2 and r["envelope"]["above_30"] == 1
    assert r["envelope"]["violations"] == [{"id": "a", "env_key": "k", "room": "living", **hot}]
    assert r["caveat"] == r["envelope"]["caveat"] == (
        "indoor temperature carries the thermal model's validation error (sunny rooms overheat; "
        "see results-2026-11-04-environment.md); runs without cached weather use the assumed outdoor curve")


def test_confirm_reports_the_envelope_against_a_baseline_run_once_per_environment(edt):
    work, _, roles = edt
    s = sweep({"heat": entry({ENV_A.key: out(tod=8.0)}) | {"bound": set_temperature(roles, 28)},
               "heat2": entry({ENV_A.key: out(tod=8.0)}) | {"bound": set_temperature(roles, 29)}}, envs=(ENV_A,))
    cf._BASELINES.clear()
    report = cf.confirm(s, work / "run3", sample=2, seed=0, cache=work / "cache", days=3)
    assert report["runs"] == 2 and len(cf._BASELINES) == 1  # one baseline run for both automations
    e = report["envelope"]
    assert e["runs_with_climate_targets"] == 2 and e["above_30"] >= 2 and e["breached_above_30"] >= e["above_30"]
    v = next(x for x in e["violations"] if x["room"] == "kitchen")
    assert v["above_30"] and v["baseline_max_c"] < 30 and v["delta_max_c"] > 3
    assert "indoor temperature carries" in report["caveat"]


# -- a command's parts are one firing ------------------------------------------------------------
def row(t, service="light.turn_on", entity="light.a", feature="state", automation="a1", device="lamp"):
    return {"kind": "automation", "t": t, "automation": automation, "service": service, "entity": entity,
            "device": device, "feature": feature, "value": 1}


def test_the_parts_of_one_command_are_one_firing():
    rows = [row(100.0), row(100.0, feature="brightness"), row(500.0), row(500.02, feature="brightness")]
    firings = cf.firings_from(rows)
    assert [(f.t, f.service, f.target) for f in firings] == [(100.0, "light.turn_on", "light.a"),
                                                             (500.0, "light.turn_on", "light.a")]


def test_a_repeated_feature_within_a_second_is_a_new_firing_so_an_oscillating_toggle_still_flaps():
    rows = [row(100.0), row(100.2), row(100.4, feature="brightness"), row(100.6)]
    assert [f.t for f in cf.firings_from(rows)] == [100.0, 100.2, 100.6]  # state | state+brightness | state


def test_recorded_actions_are_never_merged():
    rows = [row(100.0, device="", feature=""), row(100.0, device="", feature=""), row(100.0, device="", feature=""),
            row(100.0)]
    assert len(cf.firings_from(rows)) == 4  # three recorded calls, and a twin command that a record does not join


def test_other_targets_services_and_automations_stay_separate_firings():
    rows = [row(100.0), row(100.0, entity="light.b"), row(100.0, service="light.turn_off"),
            row(100.0, automation="a2"), {"kind": "report", "t": 100.0}]
    assert len(cf.firings_from(rows)) == 4


def test_a_light_turned_on_with_brightness_twice_is_two_firings_closed_loop(edt):
    work, home_yaml, roles = edt
    lamp = roles[("light", "living", 0)]
    auto = {"alias": "dim", "triggers": [{"trigger": "time", "at": ["08:00:00", "09:00:00"]}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": lamp}, "data": {"brightness": 128}}]}
    r = cf.closed_loop(auto, Env(CITY, "2026-01-15", 0.0, 4), home_yaml, work / "cache", days=1)
    assert len(r["rows"]) == 4  # the session writes a row per part of the command (state, brightness)
    assert [(f.service, f.target) for f in r["firings"]] == [("light.turn_on", lamp)] * 2


def test_a_copy_light_lifts_the_rooms_lux_only_once_turned_on(edt):
    """The extra lights of a room are real lamps: the same pair on the living room's third light flaps closed loop
    as on its first (its lamp lifts the lux), and the open loop, where no automation turns one on, does not."""
    work, _, roles = edt
    pair = toggle_pair(roles)
    pair["actions"][0]["target"]["entity_id"] = roles[("light", "living", 2)]
    s = sweep({"pair3": entry({ENV_A.key: out(fired=True, tod=14.0, flaps=False)}, ["flaps"]) | {"bound": pair}},
              envs=(ENV_A,))
    report = cf.confirm(s, work / "run3", sample=1, seed=0, cache=work / "cache", days=2)
    [d] = report["disagreements"]
    assert d["closed"]["flaps"] is True and d["open"]["flaps"] is False
