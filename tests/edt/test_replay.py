"""vesper.edt.replay: replay public automations (bound to the EDT home) against a recorded environment trace."""
import gzip
import json
import time
from pathlib import Path

import pytest

from vesper.edt.bind import bind
from vesper.edt.home import build_edt_home
from vesper.edt.replay import Replay, replay, replay_detailed
from vesper.edt.trace import record_trace
from vesper.edt.types import Env, Firing

pytestmark = pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")

PHOENIX = "Phoenix, AZ, USA"
DAY = 86400


@pytest.fixture(scope="module")
def edt(tmp_path_factory):
    path, roles = build_edt_home(tmp_path_factory.mktemp("edt"), "catalog/vds")
    return path, roles


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("traces")


@pytest.fixture(scope="module")
def trace4(edt, cache):
    """Three days, rung 4, Phoenix in July (assumed clear sky)."""
    return record_trace(edt[0], Env(PHOENIX, "2026-07-15", 0.0, 4), cache, fetch=False)


@pytest.fixture(scope="module")
def trace1(edt, cache):
    return record_trace(edt[0], Env(PHOENIX, "2026-07-15", 0.0, 1), cache, fetch=False)


@pytest.fixture(scope="module")
def trace2(edt, cache):
    return record_trace(edt[0], Env(PHOENIX, "2026-07-15", 0.0, 2), cache, fetch=False)


def read(trace):
    with gzip.open(trace, "rt") as f:
        lines = [json.loads(line) for line in f]
    return lines[0], lines[1:]


def reports_of(trace, twin_feature):
    _, lines = read(trace)
    return [r for r in lines if (r["twin_id"], r["feature"]) == twin_feature]


def lux_twin(roles, room="kitchen"):
    """(twin id, feature) behind a room's illuminance entity (the home names its sensors <room>_lux0)."""
    return f"zigbee:aqara:rtcgq11lm@{room}_lux0", "illuminance"


def lamp(roles, below=50, **extra):
    return bind({"alias": "lamp", "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.kitchen_illuminance",
                                                 "below": below, **extra}],
                 "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}]}, roles, 0)


def below_crossings(reports, threshold, lo=DAY, hi=3 * DAY):
    """Independent oracle: when the held value enters (-inf, threshold) from at or above it."""
    out, held = [], None
    for r in sorted(reports, key=lambda r: r["observed_t"]):
        v = r["value"]
        if held is not None and held >= threshold > v and lo <= r["observed_t"] < hi:
            out.append(r["observed_t"])
        held = v
    return out


def synthetic(tmp_path, source, header_extra, reports):
    """A trace file of `source`'s header (plus `header_extra`) and exactly `reports`."""
    header, _ = read(source)
    out = tmp_path / "synthetic.jsonl.gz"
    with gzip.open(out, "wt") as f:
        f.write(json.dumps({**header, **header_extra}) + "\n")
        for r in reports:
            f.write(json.dumps(r) + "\n")
    return out


def test_lamp_on_below_50_lx_fires_at_the_traces_crossings(edt, trace4):
    _, roles = edt
    out = replay(edt[0], trace4, {"lamp": lamp(roles)})
    expected = below_crossings(reports_of(trace4, lux_twin(roles)), 50)
    assert len(expected) >= 2  # an evening on day 1 and on day 2
    assert [f.t for f in out["lamp"]] == expected
    assert all(f == Firing(f.t, "lamp", "light.turn_on", roles[("light", "kitchen", 0)]) for f in out["lamp"])


def test_a_for_still_pending_at_the_end_gives_no_firing_and_no_error(edt, trace4):
    _, roles = edt
    evening = below_crossings(reports_of(trace4, lux_twin(roles)), 50)[0]
    held = {"lamp": lamp(roles, **{"for": "02:00:00"})}
    assert replay(edt[0], trace4, held, until=evening + 3600) == {"lamp": []}
    full = replay(edt[0], trace4, held)["lamp"]
    assert full and full[0].t == pytest.approx(evening + 7200)


def test_day_zero_is_warm_up(edt, trace4):
    _, roles = edt
    at6 = bind({"alias": "six", "triggers": [{"trigger": "time", "at": "06:00:00"}],
                "actions": [{"action": "light.toggle", "target": {"entity_id": "light.kitchen"}}]}, roles, 0)
    assert [f.t for f in replay(edt[0], trace4, {"six": at6})["six"]] == [DAY + 6 * 3600, 2 * DAY + 6 * 3600]


def test_reports_are_a_step_function_held_until_the_next(edt, trace4):
    """A noon condition sees the last report before noon, however old."""
    _, roles = edt
    noon = bind({"alias": "noon", "triggers": [{"trigger": "time", "at": "12:00:00"}],
                 "conditions": [{"condition": "numeric_state", "entity_id": "sensor.kitchen_illuminance",
                                 "above": 5}],
                 "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]}, roles, 0)
    reports = sorted(reports_of(trace4, lux_twin(roles)), key=lambda r: r["observed_t"])
    want = []
    for day in (1, 2):
        at = day * DAY + 12 * 3600
        held = [r["value"] for r in reports if r["observed_t"] <= at]
        if held and held[-1] > 5:
            want.append(at)
    assert want  # the noon sun lights the kitchen
    assert [f.t for f in replay(edt[0], trace4, {"noon": noon})["noon"]] == want


def test_a_sun_trigger_fires_at_sunset_with_a_site_and_is_refused_without_one(edt, trace4, trace1, trace2):
    _, roles = edt
    from vesper.env.site import parse_site
    from vesper.env.sun import sun_events
    sunset = {"alias": "dusk", "triggers": [{"trigger": "sun", "event": "sunset"}],
              "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.living"}}]}
    plain = {"alias": "plain", "triggers": [{"trigger": "time", "at": "20:00"}],
             "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.living"}}]}
    autos = {"dusk": bind(sunset, roles, 0), "plain": bind(plain, roles, 0)}
    site = parse_site({"city": PHOENIX, "date": "2026-07-15", "north_deg": 0.0})
    want = [sun_events(site, d)["sunset"] for d in (1, 2)]  # seconds from the run's midnight
    for trace in (trace4, trace2):  # rung 2 has the sun in its header though its home had no site
        got = replay(edt[0], trace, autos)
        assert [f.t for f in got["dusk"]] == pytest.approx(want) and len(got["plain"]) == 2
    r1 = replay_detailed(edt[0], trace1, autos)
    assert isinstance(r1, Replay)
    assert r1.firings["dusk"] == [] and len(r1.firings["plain"]) == 2
    assert list(r1.refused) == ["dusk"] and "site" in r1.refused["dusk"]


def test_one_firing_per_action_and_target_even_when_a_command_has_several_parts(edt, trace4):
    _, roles = edt
    both = {"alias": "both", "triggers": [{"trigger": "time", "at": "06:00"}],
            "actions": [{"action": "light.turn_on", "data": {"brightness_pct": 30},
                         "target": {"entity_id": ["light.kitchen", "light.office"]}},
                        {"action": "notify.mobile_app_phone", "data": {"message": "hi"}},
                        {"action": "script.night"}]}
    out = replay(edt[0], trace4, {"both": bind(both, roles, 0)})["both"]
    first_day = [(f.service, f.target) for f in out if f.t == DAY + 6 * 3600]
    assert first_day == [("light.turn_on", roles[("light", "kitchen", 0)]),
                         ("light.turn_on", roles[("light", "office", 0)]),
                         ("notify.mobile_app_phone", ""), ("script.night", "")]


def test_every_given_automation_has_an_entry_even_one_that_is_refused(edt, trace4):
    _, roles = edt
    bad = {"alias": "bad", "triggers": [{"trigger": "template", "value_template": "{{ true }}"}], "actions": []}
    got = replay_detailed(edt[0], trace4, {"lamp": lamp(roles), "bad": bad})
    assert set(got.firings) == {"lamp", "bad"} and got.firings["bad"] == [] and "bad" in got.refused


def test_the_headers_initial_state_seeds_the_entities_before_the_first_report(edt, trace4, tmp_path):
    _, roles = edt
    twin, feature = lux_twin(roles)
    first = {"t": DAY + 100.0, "twin_id": twin, "feature": feature, "value": 20.0, "observed_t": DAY + 100.0}
    autos = {"lamp": lamp(roles)}
    bright = synthetic(tmp_path, trace4, {"initial_state": {twin: {feature: 300.0}}}, [first])
    assert [f.t for f in replay(edt[0], bright, autos)["lamp"]] == [DAY + 100.0]  # 300 lx -> 20 lx: a crossing
    dark = synthetic(tmp_path, trace4, {"initial_state": {twin: {feature: 5.0}}}, [first])
    assert replay(edt[0], dark, autos) == {"lamp": []}  # already below 50 lx: nothing to cross
    header, _ = read(trace4)
    header.pop("initial_state", None)  # a trace without the key: no state until the first report
    absent = tmp_path / "absent.jsonl.gz"
    with gzip.open(absent, "wt") as f:
        f.write(json.dumps(header) + "\n" + json.dumps(first) + "\n")
    assert [f.t for f in replay(edt[0], absent, autos)["lamp"]] == [DAY + 100.0]


def test_actuator_reports_are_never_fed_to_the_engine(edt, trace4, tmp_path):
    """In open loop only the automation's own recorded actions touch actuators: a light's report changes nothing."""
    _, roles = edt
    twin = "zigbee:m-elec:ml-st-bp-dim@kitchen_light"
    flips = [{"t": DAY + 10.0, "twin_id": twin, "feature": "state", "value": "ON", "observed_t": DAY + 10.0},
             {"t": DAY + 20.0, "twin_id": twin, "feature": "state", "value": "OFF", "observed_t": DAY + 20.0}]
    watch = {"alias": "w", "triggers": [{"trigger": "state", "entity_id": "light.kitchen", "to": "on"}],
             "actions": [{"action": "notify.x", "target": {"entity_id": "x.y"}}]}
    trace = synthetic(tmp_path, trace4, {"initial_state": {twin: {"state": "OFF"}}}, flips)
    assert replay(edt[0], trace, {"w": bind(watch, roles, 0)}) == {"w": []}


def test_200_automations_replay_in_under_10_seconds(edt, trace4):
    _, roles = edt
    autos = {}
    for k in range(200):
        room = ("kitchen", "living", "office", "bedroom_1")[k % 4]
        autos[f"a{k}"] = bind({"alias": f"a{k}", "triggers": [{"trigger": "numeric_state",
                                                                "entity_id": f"sensor.{room}_illuminance",
                                                                "below": 10 + 5 * (k % 40)}],
                               "conditions": [{"condition": "time", "after": "16:00", "before": "23:59"}],
                               "actions": [{"action": "light.turn_on", "target": {"entity_id": f"light.{room}"}}]},
                              roles, 0)
    t0 = time.perf_counter()
    out = replay(edt[0], trace4, autos)
    assert time.perf_counter() - t0 < 10
    assert len(out) == 200 and any(out.values())


def test_the_same_inputs_give_identical_firings_in_the_same_order(edt, trace4):
    _, roles = edt
    autos = {f"a{k}": lamp(roles, below=20 + 10 * k) for k in range(5)}
    assert replay(edt[0], trace4, autos) == replay(edt[0], trace4, autos)
    assert list(replay(edt[0], trace4, autos)) == list(autos)


def test_each_automations_firings_are_in_time_order(edt, trace4):
    _, roles = edt
    out = replay(edt[0], trace4, {"x": lamp(roles, below=60), "y": lamp(roles, below=40)})
    assert all(a.t <= b.t for fs in out.values() for a, b in zip(fs, fs[1:], strict=False))
