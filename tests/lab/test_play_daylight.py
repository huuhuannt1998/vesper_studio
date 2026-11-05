"""The play server's daylight (play-view environment design 2026-11-05, §3, §4): `GET /api/daylight?step=900`, the
home's day for the page's time-of-day preview, computed by the session's own vesper.env model from the cached weather;
and an illuminance sensor's card with the session's readings so far and the thresholds the home's automations use."""
import json

import pytest

from tests.lab.maps import house
from tests.lab.test_daylight_lights import SENSOR, lux_catalog
from tests.lab.test_environment_session import SITE
from tests.lab.test_play import get
from tests.lab.homes import HOME, write_home
from vesper.lab.daylight import daylight_table, write_daylight_automations
from vesper.lab.home import load_lab_home
from vesper.lab.play import PlayServer, device_card
from vesper.lab.session import Session
from vesper.lab.windows import plan_windows, write_windows
from vesper.twins.clock import LogicalClock

SITED = HOME.replace('    start_time: "07:30"\n', '    start_time: "07:30"\n' + SITE).replace(
    "    devices:\n", "    devices:\n" + SENSOR, 1)


def session(tmp_path, text=SITED, rules=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_windows(tmp_path, plan_windows(house()))
    path = write_home(tmp_path, text)
    if rules:
        write_daylight_automations(path, lux_catalog())
    s = Session(load_lab_home(path, lux_catalog()), clock=LogicalClock())
    s.start(spawn_player=False)
    return s


@pytest.fixture
def server(tmp_path):
    s = session(tmp_path, rules=True)
    srv = PlayServer(s, port=0)
    srv.start()
    yield srv
    srv.stop()


def test_the_day_is_served_one_row_a_step_with_each_room_s_daylight(server):
    status, ctype, body = get(f"http://127.0.0.1:{server.port}/api/daylight?step=900")
    assert status == 200 and ctype == "application/json"
    day = json.loads(body)
    assert day["date"] == "2026-06-21" and day["step"] == 900 and len(day["rows"]) == 97  # 00:00 to 24:00
    rows = {r["hour"]: r for r in day["rows"]}
    assert rows[0]["sun_elevation"] < 0 and rows[0]["illuminance_lux"] == 0 and rows[0]["rooms"]["bedroom"] == 0
    assert rows[13]["sun_elevation"] > 60 and rows[13]["rooms"]["bedroom"] > rows[7]["rooms"]["bedroom"] > 0
    assert all(r["rooms"]["hall"] == 0 for r in day["rows"])  # the hallway has no window
    assert {"sun_azimuth", "direct_fraction", "temperature_c", "cloud_cover_pct", "t"} <= set(rows[13])
    assert day["lamp_lux"] == 200 and day["rules"][0]["room"] == "bedroom"  # the daylight-following rule to preview


def test_the_preview_s_daylight_is_the_shades_as_they_are_now(tmp_path):
    s = session(tmp_path)
    open_ = daylight_table(s, 900)
    for w in s.rules.env.shades:
        s.rules.env.set_shade(w, False)
    shut = daylight_table(s, 900)
    noon = 13 * 4
    assert shut["rows"][noon]["rooms"]["bedroom"] < open_["rows"][noon]["rooms"]["bedroom"]
    assert shut["rows"][noon]["illuminance_lux"] == open_["rows"][noon]["illuminance_lux"]  # the sky is the sky


def test_a_home_without_a_site_has_no_day_to_preview_and_a_bad_step_is_refused(tmp_path, server):
    assert get(f"http://127.0.0.1:{server.port}/api/daylight?step=5")[0] == 400
    assert get(f"http://127.0.0.1:{server.port}/api/daylight?step=x")[0] == 400
    s = session(tmp_path / "plain", HOME)
    srv = PlayServer(s, port=0)
    srv.start()
    try:
        assert get(f"http://127.0.0.1:{srv.port}/api/daylight")[0] == 404
    finally:
        srv.stop()


def test_an_illuminance_sensor_s_card_has_its_readings_so_far_and_its_thresholds(tmp_path):
    s = session(tmp_path, rules=True)
    s.clock.advance(10 * 60)
    card = device_card(s, "bedroom_lux")
    assert card["readings"] and all(len(p) == 2 for p in card["readings"])
    assert card["readings"][-1][1] == s.rules.twin("bedroom_lux").state["illuminance"]
    assert card["thresholds"] == ["lights follow daylight to 300 lx"]
    lamp = device_card(s, "bedroom_lamp")
    assert lamp["readings"] == [] and lamp["thresholds"] == []
