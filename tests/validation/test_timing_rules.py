from tests.twins.conftest import records
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Event
from vesper.validation.timing import RuleChecker, run_rules


def checker():
    rules = {("t", "temperature"): {"min_s": 10, "max_s": 100, "change": 1.0},
             ("t", "occupancy"): {"min_s": 0, "max_s": 3600, "change": 0}}
    return RuleChecker(end_t=150.0, rules=rules, initial={("t", "temperature"): 20.0, ("t", "occupancy"): False},
                       added={"t": 0.0}, presence_values={("t", "occupancy"): (True, False)})


def ev(t, feature, value, cause="stimulus"):
    return Event("t", feature, value, t, t, cause)


def test_reports_that_obey_their_rules_pass():
    c = checker()
    for e in (ev(5, "temperature", 21.5), ev(20, "temperature", 23.0), ev(120, "temperature", 23.0, "periodic")):
        c.on_event(e)
    c.on_presence("t", 30.0)
    c.on_event(ev(30, "occupancy", True))
    c.on_event(ev(30 + OCCUPANCY_TIMEOUT_S, "occupancy", False))
    assert c.finish()["violations"] == []


def test_each_kind_of_violation_is_named():
    c = checker()
    c.on_event(ev(5, "temperature", 21.5))
    c.on_event(ev(8, "temperature", 23.0))  # R1: 3 s after the last report, min_s 10
    c.on_event(ev(30, "temperature", 23.4))  # R3: a change of 0.4 < 1.0
    c.on_event(ev(140, "temperature", 25.0))  # R2: 110 s of silence > max_s 100
    c.on_presence("t", 20.0)
    c.on_event(ev(20, "occupancy", True))
    c.on_event(ev(50, "occupancy", False))  # R4: cleared 30 s after presence, not 90 s
    kinds = [v["rule"] for v in c.finish()["violations"]]
    assert kinds == ["R1 min_s", "R3 change", "R2 max_s", "R4 occupancy timeout"]


def test_the_end_of_the_run_counts_as_silence():
    c = checker()
    c.on_event(ev(10, "temperature", 21.5))
    assert [v["rule"] for v in c.finish()["violations"]] == ["R2 max_s"]  # 140 s to the end > 100 s


def test_every_fixture_twin_obeys_its_rules_under_a_seeded_script():
    vds = records()
    report = run_rules(list(vds.values()), seed=0, duration_s=4 * 3600)
    assert report["products"] == len([r for r in vds.values() if r["fidelity"]["reached"]["L1"]])
    assert report["reports"] > 500 and report["features_checked"] > 10
    assert report["violations"] == [] and report["passed"] is True


def test_the_rule_run_is_reproducible():
    a = run_rules(list(records().values()), seed=1, duration_s=3600)
    b = run_rules(list(records().values()), seed=1, duration_s=3600)
    assert a == b and a["reports"] > 0
