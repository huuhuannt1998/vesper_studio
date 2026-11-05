"""Addendum 14's script: its comparisons, labels, windows and the validation outdoors with the recorded split."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "r7w_weather", Path(__file__).resolve().parents[2] / "scripts" / "revision" / "r7w_weather.py")
r7w = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(r7w)


def _auto(frame=None, clock=None, kinds=("sun",)):
    return {"outcomes": {"e1": {"fired": True}}, "frame_divergences": frame or [], "divergences": clock or [],
            "kinds": list(kinds)}


def test_compare_counts_held_lost_and_new_by_kind_and_quantity():
    old = {"automations": {"a": _auto(["time_spread"], ["time_spread"]), "b": _auto(["flaps"], kinds=["temperature"]),
                           "c": _auto(["night_light"]), "d": _auto()}}
    new = {"automations": {"a": _auto(["time_spread"], ["time_spread"]), "c": _auto(["flaps"]),
                           "d": _auto(["fires_somewhere_only"], kinds=["humidity"]), "x": {"outcomes": {}}}}
    out = r7w.compare(old, new)
    f = out["frame_relation"]
    assert (f["old"], f["new"], f["held_same_kinds"], f["held_other_kinds"], f["lost"], f["new_flags"]) == \
        (3, 3, 1, 1, 1, 1)
    assert f["lost_by_kind"] == {"flaps": 1} and f["new_by_kind"] == {"fires_somewhere_only": 1}
    assert f["lost_by_quantity"] == {"temperature": 1} and f["new_by_quantity"] == {"humidity": 1}
    assert out["clock_relation"]["held_same_kinds"] == 1 and out["run"] == {"old": 4, "new": 3}


def test_weekday_readers_and_the_dates_weekdays():
    """Deviation A14.9: the further years' dates fall on other weekdays; 2024's on two different ones."""
    assert r7w.reads_weekday({"bound": {"condition": [{"condition": "time", "weekday": ["mon"]}]}})
    assert r7w.reads_weekday({"bound": {"condition": [{"value_template": "{{ now().weekday() < 5 }}"}]}})
    assert not r7w.reads_weekday({"bound": {"trigger": [{"platform": "time", "at": "07:00"}]}})
    assert set(r7w.weekdays(None).values()) == {"Wed / Thu / Fri"}
    assert set(r7w.weekdays(2023).values()) == {"Sat / Sun / Mon"}
    assert set(r7w.weekdays(2025).values()) == {"Tue / Wed / Thu"}
    assert set(r7w.weekdays(2024).values()) == {"Sun / Mon / Tue", "Mon / Tue / Wed"}


def test_a_pair_label_drops_the_year():
    assert r7w.md_label(["Minneapolis 2023-01-14 n0", "Stockholm 2023-07-15 n180"]) == \
        ["Minneapolis 01-14 n0", "Stockholm 07-15 n180"]


def test_the_core_four_and_the_windows_of_a_year():
    core = r7w.core_envs(2024)
    assert len(core) == 4 and {e["date"] for e in core.values()} == {"2024-01-14"}
    assert {e["city"].split(",")[0] for e in core.values()} == {"Minneapolis", "Stockholm"}
    w = r7w.windows(2025)
    assert len(w) == 24 and all((last - first).days in (2, 3) for _s, first, last in w)
    assert {first.year for _s, first, _l in w} == {2025}


def test_the_validation_outdoors_takes_the_recorded_split_only_when_asked():
    from vesper.env.site import parse_site
    site = parse_site({"city": "Valencia, Spain", "date": "2012-04-01"})
    start = dt.datetime(2012, 4, 1, 10, tzinfo=dt.UTC)
    series = ([15.0] * 3, [60.0] * 3, [400.0] * 3, [100.0] * 3)
    extra = ([300.0, None, 0.0], [100.0, 50.0, 0.0], [0.0, 0.0, 0.2])
    rec = r7w._measured_outdoor(True)(site, start, *series, *extra)
    ass = r7w._measured_outdoor(False)(site, start, *series, *extra)
    assert rec.at(0).direct_fraction == pytest.approx(0.75) and ass.at(0).direct_fraction == 0.0
    assert rec.at(3600).direct_fraction == 0.0  # no recorded direct: the assumed share (cloud 100 %)
    assert rec.at(7200).snow_depth_m == 0.2 and ass.at(7200).snow_depth_m == 0.2
