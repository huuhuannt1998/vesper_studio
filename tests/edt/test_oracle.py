"""vesper.edt.oracle: classify one automation's firings in one environment, and the outcomes across environments."""
from vesper.edt.oracle import divergences, outcome
from vesper.edt.types import Env, Firing, Outcome
from vesper.env.site import parse_site
from vesper.env.sun import sun_events

DAY = 86400.0
H = 3600.0
STOCKHOLM = parse_site({"lat": 59.3293, "lon": 18.0686, "tz": "Europe/Stockholm", "date": "2026-01-15"})
POLAR_JUNE = parse_site({"lat": 70.0, "lon": 25.0, "tz": "Europe/Oslo", "date": "2026-06-21"})


def f(t, service="light.turn_on", target="light.a", automation="a1"):
    return Firing(t=t, automation=automation, service=service, target=target)


def test_no_firings():
    assert outcome([], STOCKHOLM) == Outcome(False, None, False, False, False)


def test_warmup_day_is_ignored():
    o = outcome([f(0.5 * DAY + 8 * H, "switch.turn_on", "switch.a")], STOCKHOLM)
    assert o.fired is False and o.first_tod_h is None


def test_first_tod_is_first_measured_firing():
    firings = [f(DAY + 9.5 * H, "switch.turn_on"), f(DAY + 7.25 * H, "switch.turn_on"), f(2 * DAY + 6 * H)]
    assert outcome(firings, STOCKHOLM).first_tod_h == 7.25
    assert outcome(firings, STOCKHOLM).fired is True


def test_day_three_onward_is_outside_the_window():
    assert outcome([f(3 * DAY + 8 * H, "switch.turn_on")], STOCKHOLM).fired is False


def test_seven_firings_in_one_hour_flap():
    seven = [f(DAY + 10 * H + 500 * i, "switch.toggle") for i in range(7)]  # 0 .. 3000 s
    assert outcome(seven, STOCKHOLM).flaps is True


def test_six_firings_in_one_hour_do_not_flap():
    assert outcome([f(DAY + 10 * H + 500 * i, "switch.toggle") for i in range(6)], STOCKHOLM).flaps is False


def test_seven_firings_spread_over_two_hours_do_not_flap():
    assert outcome([f(DAY + 10 * H + 1200 * i, "switch.toggle") for i in range(7)], STOCKHOLM).flaps is False


def test_flapping_is_per_automation():
    mixed = [f(DAY + 10 * H + 500 * i, "switch.toggle", automation=f"a{i % 2}") for i in range(8)]
    assert outcome(mixed, STOCKHOLM).flaps is False


def test_light_on_at_2200_and_never_off_is_night_light():
    assert outcome([f(DAY + 22 * H)], STOCKHOLM).night_light is True


def test_light_turned_off_before_one_is_not_night_light():
    firings = [f(DAY + 22 * H), f(2 * DAY + 0.5 * H, "light.turn_off")]
    assert outcome(firings, STOCKHOLM).night_light is False


def test_light_turned_off_after_one_is_night_light():
    firings = [f(DAY + 22 * H), f(2 * DAY + 1.5 * H, "light.turn_off")]
    assert outcome(firings, STOCKHOLM).night_light is True


def test_other_lights_turn_off_does_not_count():
    firings = [f(DAY + 22 * H), f(DAY + 23 * H, "light.turn_off", "light.b")]
    assert outcome(firings, STOCKHOLM).night_light is True


def test_light_on_in_the_morning_off_by_evening_is_not_night_light():
    assert outcome([f(DAY + 7 * H), f(DAY + 9 * H, "light.turn_off")], STOCKHOLM).night_light is False


def test_cover_opened_after_sunset_and_not_closed_is_night_cover():
    s = DAY + sun_events(STOCKHOLM, 1)["sunset"]
    assert outcome([f(s + 600, "cover.open_cover", "cover.w")], STOCKHOLM).night_cover is True


def test_cover_closed_before_midnight_is_not_night_cover():
    s = DAY + sun_events(STOCKHOLM, 1)["sunset"]
    firings = [f(s + 600, "cover.open_cover", "cover.w"), f(s + 3 * H, "cover.close_cover", "cover.w")]
    assert outcome(firings, STOCKHOLM).night_cover is False


def test_cover_closed_after_midnight_is_night_cover():
    s = DAY + sun_events(STOCKHOLM, 1)["sunset"]
    firings = [f(s + 600, "cover.open_cover", "cover.w"), f(2 * DAY + 0.5 * H, "cover.close_cover", "cover.w")]
    assert outcome(firings, STOCKHOLM).night_cover is True


def test_cover_opened_before_sunset_is_not_night_cover():
    s = DAY + sun_events(STOCKHOLM, 1)["sunset"]
    assert outcome([f(s - 600, "cover.open_cover", "cover.w")], STOCKHOLM).night_cover is False


def test_sunset_trigger_with_no_sunset_does_not_raise():
    """Lat 70 in June: the sun does not set, so a sunset-triggered automation never fires; no exception either way."""
    assert sun_events(POLAR_JUNE, 1)["sunset"] is None
    assert outcome([], POLAR_JUNE) == Outcome(False, None, False, False, False)
    o = outcome([f(DAY + 23 * H, "cover.open_cover", "cover.w")], POLAR_JUNE)
    assert o.fired is True and o.night_cover is False


def _o(fired=True, tod=8.0, flaps=False, nl=False, nc=False):
    return Outcome(fired, tod if fired else None, flaps, nl, nc)


E1, E2, E3 = Env("a", "2026-01-15", 0.0), Env("b", "2026-07-15", 0.0), Env("c", "2026-07-15", 90.0)


def test_identical_outcomes_have_no_divergence():
    assert divergences({E1: _o(), E2: _o(), E3: _o()}) == set()


def test_empty_and_single_env():
    assert divergences({}) == set()
    assert divergences({E1: _o()}) == set()


def test_fires_somewhere_only():
    assert divergences({E1: _o(), E2: _o(fired=False), E3: _o(fired=False)}) == {"fires_somewhere_only"}


def test_fires_nowhere_is_no_divergence():
    assert divergences({E1: _o(fired=False), E2: _o(fired=False)}) == set()


def test_time_spread_over_three_hours_only():
    assert divergences({E1: _o(tod=8.0), E2: _o(tod=11.5)}) == {"time_spread"}
    assert divergences({E1: _o(tod=8.0), E2: _o(tod=11.0)}) == set()


def test_time_spread_ignores_envs_where_it_does_not_fire():
    assert divergences({E1: _o(tod=8.0), E2: _o(fired=False)}) == {"fires_somewhere_only"}


def test_envelope_kinds_when_any_environment_shows_them():
    got = divergences({E1: _o(flaps=True), E2: _o(nl=True), E3: _o(nc=True)})
    assert got == {"flaps", "night_light", "night_cover"}
