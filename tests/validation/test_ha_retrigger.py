"""The synthetic re-triggering stratum (protocol Addendum 20): its automations, its trace, the comparison, and the
driver's capture of Home Assistant's "Restarting" line (when Home Assistant's venv is present)."""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from tests.validation.test_ha_diff import _ha_python
from vesper.edt.types import Firing
from vesper.validation import ha_diff, ha_retrigger

DAY = 86400.0
H = 3600.0


def test_the_schedule_is_the_registered_one():
    day = ha_retrigger.pulse_instants()
    assert day[:3] == [8 * H, 8 * H + 120, 8 * H + 240]
    assert day[3:18] == [12 * H + 70 * k for k in range(15)]
    assert day[18:] == [18 * H]
    rows = ha_retrigger.reports("twin@office_motion")
    ons = [r["observed_t"] for r in rows if r["value"] is True]
    offs = [r["observed_t"] for r in rows if r["value"] is False]
    assert ons == [d * DAY + t for d in (0, 1, 2) for t in day]
    assert offs == [t + ha_retrigger.PULSE_S for t in ons]
    assert all(r["feature"] == "occupancy" and r["t"] == r["observed_t"] for r in rows)
    assert [r["observed_t"] for r in rows] == sorted(r["observed_t"] for r in rows)


def test_no_trigger_meets_a_delay_or_timeout_end_within_a_segment():
    """A20.2: within each segment, no difference between two trigger instants equals the offset of a timer a run
    started at the first sets (a delay's or timeout's end; for a queued automation, the end of the n-th run of a chain
    started there), so no same-instant ordering decides anything."""
    offsets = ha_retrigger.timer_offsets()
    assert {100.0, 200.0, 300.0, 95.0, 2 * 95.0, 3 * 300.0} <= offsets
    for seg in ha_retrigger.segments().values():
        for i, a in enumerate(seg):
            for b in seg[i + 1:]:
                assert all(abs((b - a) - x) > 1 for x in offsets), (a, b)


IDS = {name: {"occupancy": f"binary_sensor.{name}_occ"} for name in ("office_motion", "kitchen_motion")}
IDS |= {name: {"state": f"light.{name}"} for name in ha_retrigger.LIGHTS}
IDS |= {ha_retrigger.PLUG: {"state": f"switch.{ha_retrigger.PLUG}"}}


def test_the_stratum_has_its_registered_modes_and_only_the_homes_entities():
    s = ha_retrigger.stratum(IDS)
    assert list(s) == [f"r{i}" for i in range(1, 9)]
    assert [a.get("mode", "single") for a in s.values()] == ["single", "restart", "restart", "restart", "queued",
                                                             "queued", "parallel", "parallel"]
    assert [a.get("max") for a in s.values()] == [None, None, None, None, 3, None, 2, None]
    assert s["r7"]["max_exceeded"] == "silent"
    text = json.dumps(s)
    assert "{" not in text.replace('{"', "").replace("{}", "")  # every placeholder is filled
    assert all(a["triggers"] == [{"trigger": "state", "entity_id": "binary_sensor.office_motion_occ", "to": "on"}]
               for a in s.values())
    lights = [a["actions"][0]["target"]["entity_id"] for a in s.values()]
    assert len(set(lights)) == 8


@pytest.mark.skipif(not Path("catalog/vds/zigbee.json").is_file(), reason="needs catalog/vds")
def test_the_edt_home_has_the_stratums_entities_and_the_engine_refuses_none(tmp_path):
    from vesper.edt.home import build_edt_home
    from vesper.edt.replay import TraceSession, _home
    from vesper.lab.automations import Engine
    from vesper.twins.clock import LogicalClock
    path, _ = build_edt_home(tmp_path, "catalog/vds")
    ids, twin_of = ha_retrigger.home_entities(path, "catalog/vds")
    s = ha_retrigger.stratum(ids)
    assert s["r3"]["actions"][2]["target"]["entity_id"].startswith("switch.")
    assert twin_of["office_motion"].endswith("@office_motion")
    home, twins = _home(str(path.resolve()), path.stat().st_mtime_ns, str(Path("catalog/vds").resolve()))
    e = Engine(TraceSession(home, twins, {}, LogicalClock()), permissive=True, automations=list(s.values()))
    assert e.refused == [] and len(e.automations) == 8


def _f(ts, service="light.turn_on", target="light.x"):
    return [Firing(t, "a", service, target) for t in ts]


def test_an_automation_agrees_only_if_every_measure_is_equal():
    v = _f([DAY + 8 * H, DAY + 8 * H + 300]) + _f([DAY + 12 * H], "light.turn_off")
    side = {"firings": v, "starts": [DAY + 8 * H], "cancels": [], "dropped": []}
    r = ha_retrigger.compare(side, dict(side), site=None)
    assert r["agrees"] and all(r["equal"].values())
    assert r["v"]["n_firings"] == 3 and r["v"]["instants"] == 3
    assert r["v"]["by_segment"] == {"A": {"firings": 2, "starts": 1, "cancels": 0},
                                    "B": {"firings": 1, "starts": 0, "cancels": 0},
                                    "C": {"firings": 0, "starts": 0, "cancels": 0}}
    shifted = {**side, "firings": _f([DAY + 8 * H + 0.4, DAY + 8 * H + 300]) + _f([DAY + 12 * H], "light.turn_off")}
    assert ha_retrigger.compare(side, shifted, site=None)["agrees"]  # within 1 s
    late = {**side, "firings": _f([DAY + 8 * H + 2, DAY + 8 * H + 300]) + _f([DAY + 12 * H], "light.turn_off")}
    r = ha_retrigger.compare(side, late, site=None)
    assert not r["agrees"] and not r["equal"]["sequence"] and r["equal"]["calls"]
    more = {**side, "starts": [DAY + 8 * H, DAY + 8 * H + 120], "cancels": [DAY + 8 * H + 120]}
    r = ha_retrigger.compare(side, more, site=None)
    assert not r["agrees"] and not r["equal"]["starts"] and not r["equal"]["cancels"] and r["equal"]["sequence"]
    flapping = {**side, "firings": _f([DAY + 12 * H + 70 * k for k in range(8)])}
    r = ha_retrigger.compare(side, flapping, site=None)
    assert r["h"]["outcome"]["flaps"] and not r["v"]["outcome"]["flaps"] and not r["equal"]["outcome"]


def test_home_assistants_side_is_read_from_the_drivers_output():
    label_of = {"edt:0": "r1", "edt:1": "r2"}
    out = {"calls": [[DAY + 5, "edt:0", "light", "turn_on", ["light.x"], {}, False, None, None],
                     [5.0, "edt:0", "light", "turn_on", ["light.x"], {}, False, None, None]],
           "triggered": [[DAY + 5, "edt:0", "state"], [DAY + 9, "edt:1", "state"], [5.0, "edt:1", "state"]],
           "info": [[DAY + 9, "INFO", "homeassistant.components.automation.edt_1", "edt:1: Restarting"]],
           "log": [[DAY + 7, "WARNING", "homeassistant.components.automation.edt_0", "edt:0: Already running"]]}
    sides = ha_retrigger.ha_sides(out, label_of)
    assert [f.t for f in sides["r1"]["firings"]] == [DAY + 5]
    assert sides["r1"]["starts"] == [DAY + 5] and sides["r2"]["starts"] == [DAY + 9]
    assert sides["r2"]["cancels"] == [DAY + 9] and sides["r1"]["cancels"] == []
    assert sides["r1"]["dropped"] == [[DAY + 7, "Already running"]]


@pytest.mark.skipif(_ha_python() is None, reason="Home Assistant's venv is not installed")
def test_the_driver_captures_restarting_only_when_asked(tmp_path):
    autos = [{"alias": "edt:0", "mode": "restart", "trigger": [{"platform": "state", "entity_id": "binary_sensor.m",
                                                                "to": "on"}],
              "action": [{"delay": 300}, {"service": "light.turn_off", "target": {"entity_id": "light.x"}}]}]
    stream = [[100.0, "binary_sensor.m", "on", {}], [110.0, "binary_sensor.m", "off", {}],
              [200.0, "binary_sensor.m", "on", {}], [210.0, "binary_sensor.m", "off", {}]]
    base = {"t0": 1768370400.0, "end": 2000.0, "seed": 1, "site": {"lat": 44.98, "lon": -93.27, "elevation_m": 264,
                                                                    "tz": "America/Chicago"},
            "integrations": ["light"], "services": {}, "noop": ha_diff.HA_NOOP, "automations": autos,
            "initial": {"binary_sensor.m": ["off", {}], "light.x": ["off", {}]}, "stream": stream}
    outs = []
    for job in (base, {**base, "info_messages": ["Restarting"]}):
        jp, op = tmp_path / "job.json", tmp_path / f"out{len(outs)}.json"
        jp.write_text(json.dumps(job))
        subprocess.run([str(_ha_python()), "-I", str(ha_diff.DRIVER), str(jp), str(op)], check=True, timeout=600)
        outs.append(json.loads(op.read_text()))
    plain, asked = outs
    assert plain.get("error") is None and asked.get("error") is None
    assert "info" not in plain
    assert [(round(r[0]), r[3]) for r in asked["info"]] == [(200, "edt:0: Restarting")]
    for o in outs:  # the run started at 100 is stopped at 200; only the second turns the light off
        assert [round(c[0]) for c in o["calls"]] == [500]
        assert [round(t[0]) for t in o["triggered"]] == [100, 200]
    # the WARNING lines are unchanged by the INFO capture (a `.storage` save racing the scratch directory's removal
    # at the run's end is logged by homeassistant.util.file now and then: not an automation's line, left out)
    def warnings(o):
        return Counter((r[1], r[2]) for r in o["log"] if r[2] != "homeassistant.util.file")
    assert warnings(asked) == warnings(plain)
