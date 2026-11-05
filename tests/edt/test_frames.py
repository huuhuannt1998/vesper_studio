"""The frame-relative relation and the richer outcome (protocol Addendum 6): vesper.edt.oracle."""
from vesper.edt.oracle import (
    CLOCK,
    SENSED,
    SUN,
    Frames,
    SunRef,
    divergences,
    frame_divergences,
    frame_invariance,
    frame_restricted_divergences,
    frames_of,
    outcome,
    rich_divergences,
    rich_outcome,
    site_of,
    sun_relative_min,
)
from vesper.edt.types import Env, Firing
from vesper.env.sun import sun_events

DAY = 86400.0
H = 3600.0
JAN = Env("Stockholm, Sweden", "2026-01-14", 0.0, 4)
JUL = Env("Stockholm, Sweden", "2026-07-15", 0.0, 4)
SING = Env("Singapore", "2026-01-14", 0.0, 4)
ACTION = [{"action": "switch.turn_on", "target": {"entity_id": "switch.living_room_plug"}}]


def sunset(env: Env, day: int = 1) -> float:
    return sun_events(site_of(env), day)["sunset"]


def fire(t: float) -> list[Firing]:
    return [Firing(t, "a", "switch.turn_on", "switch.living_room_plug")]


def judge(bound: dict, firings: dict[Env, list[Firing]]):
    """(clock relation, frame relation) for an automation's firings by environment."""
    outs = {e: outcome(f, site_of(e)) for e, f in firings.items()}
    first_t = {e: rich_outcome(f)["first_t"] for e, f in firings.items()}
    return divergences(outs), frame_divergences(outs, first_t, frames_of(bound))


SUNSET = {"trigger": [{"platform": "sun", "event": "sunset"}], "action": ACTION}


def test_frames_of_reads_each_frame():
    assert frames_of(SUNSET) == Frames(frozenset({SUN}), (SunRef("sunset"),))
    assert frames_of({"trigger": {"platform": "time", "at": "07:00"}}).frames == {CLOCK}
    sensed = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.porch_temperature", "below": 5}]}
    assert frames_of(sensed).frames == {SENSED}
    assert frames_of({"trigger": [{"platform": "state", "entity_id": "binary_sensor.hallway_motion"}]}).frames == set()
    mixed = {"trigger": [{"platform": "sun", "event": "sunset", "offset": "-00:30:00"}],
             "condition": [{"condition": "time", "before": "21:00"}]}
    f = frames_of(mixed)
    assert f.frames == {SUN, CLOCK} and f.mixed and f.sun_refs == (SunRef("sunset", -1800.0),)


def test_frames_from_sun_entity_conditions_and_templates():
    elev = {"trigger": [{"platform": "numeric_state", "entity_id": "sun.sun", "attribute": "elevation", "below": 3}]}
    assert frames_of(elev).sun_refs == (SunRef("elevation", elevation=3.0, rising=False),)
    state = {"trigger": [{"platform": "state", "entity_id": "sun.sun", "to": "below_horizon"}]}
    assert frames_of(state).sun_refs == (SunRef("sunset"),)
    cond = {"trigger": [{"platform": "state", "entity_id": "binary_sensor.hallway_motion", "to": "on"}],
            "condition": [{"condition": "or", "conditions": [{"condition": "sun", "after": "sunset"}]}]}
    assert frames_of(cond) == Frames(frozenset({SUN}), (SunRef("sunset"),))
    tpl = {"trigger": [{"platform": "template", "value_template": "{{ now().hour == 7 and "
                        "states('sensor.living_room_temperature')|float < 18 }}"}]}
    assert frames_of(tpl).frames == {CLOCK, SENSED}
    sun_tpl = {"trigger": [{"platform": "template", "value_template": "{{ is_state('sun.sun', 'below_horizon') }}"}]}
    f = frames_of(sun_tpl)
    assert f.frames == {SUN} and f.sun_refs == () and not f.computable(SUN)


def test_sun_relative_minutes_measure_from_the_named_event():
    t = sunset(JAN) + 600.0
    assert abs(sun_relative_min(site_of(JAN), (SunRef("sunset"),), t) - 10.0) < 1e-6
    assert abs(sun_relative_min(site_of(JAN), (SunRef("sunset", -1800.0),), t) - 40.0) < 1e-6
    rise = sun_relative_min(site_of(JAN), (SunRef("elevation", elevation=0.0, rising=True),),
                            sun_events(site_of(JAN), 1)["sunrise"])
    assert abs(rise) < 10.0  # the sun at 0 degrees rising is (nearly) sunrise


def test_pure_sunset_in_stockholm_january_and_july_is_not_flagged():
    clock, frame = judge(SUNSET, {JAN: fire(sunset(JAN)), JUL: fire(sunset(JUL))})
    assert sunset(JUL) - sunset(JAN) > 3 * H  # the clock relation flags what the trigger commissions
    assert clock == {"time_spread"}
    assert frame == set()


def test_sunset_with_a_before_2100_condition_is_flagged():
    bound = {**SUNSET, "condition": [{"condition": "time", "before": "21:00:00"}]}
    assert sunset(JUL) % DAY > 21 * H > sunset(JAN) % DAY  # in a Stockholm July the rule never runs
    _, frame = judge(bound, {JAN: fire(sunset(JAN)), JUL: []})
    assert frame == {"fires_somewhere_only"}


def test_time_trigger_with_a_sensed_condition_is_flagged_by_fires_somewhere_only():
    bound = {"trigger": [{"platform": "time", "at": "07:00:00"}],
             "condition": [{"condition": "numeric_state", "entity_id": "sensor.porch_temperature", "below": 5}],
             "action": ACTION}
    assert frames_of(bound).frames == {CLOCK, SENSED}
    _, frame = judge(bound, {JAN: fire(DAY + 7 * H), JUL: [], SING: []})
    assert frame == {"fires_somewhere_only"}


def test_pure_clock_spread_is_still_flagged():
    bound = {"trigger": [{"platform": "time_pattern", "minutes": "/5"}],
             "condition": [{"condition": "template", "value_template": "{{ now().hour > 3 }}"}], "action": ACTION}
    clock, frame = judge(bound, {JAN: fire(DAY + 4 * H), JUL: fire(DAY + 9 * H)})
    assert clock == frame == {"time_spread"}


def test_pure_sensed_never_gets_a_timing_flag():
    bound = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.porch_temperature", "above": 20}],
             "action": ACTION}
    clock, frame = judge(bound, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 14 * H)})
    assert clock == {"time_spread"} and frame == set()


def test_mixed_frames_flagged_only_when_invariant_in_none():
    sun_and_clock = {"trigger": [{"platform": "sun", "event": "sunset"}, {"platform": "time", "at": "06:00:00"}],
                     "action": ACTION}
    # first firings at 06:00 in one and at sunset in the other: neither the clock nor the sun frame is invariant
    _, frame = judge(sun_and_clock, {JAN: fire(DAY + 6 * H), JUL: fire(sunset(JUL))})
    assert frame == {"time_spread"}
    # both at 06:00: invariant in the clock frame
    _, frame = judge(sun_and_clock, {JAN: fire(DAY + 6 * H), JUL: fire(DAY + 6 * H)})
    assert frame == set()


def test_sensed_sensitivity_drops_the_sensed_frame_when_another_is_computable():
    bound = {"trigger": [{"platform": "time_pattern", "minutes": "/5"}],
             "condition": [{"condition": "numeric_state", "entity_id": "sensor.porch_temperature", "above": 20}]}
    outs = {JAN: outcome(fire(DAY + 6 * H), site_of(JAN)), JUL: outcome(fire(DAY + 14 * H), site_of(JUL))}
    first_t = {JAN: DAY + 6 * H, JUL: DAY + 14 * H}
    frames = frames_of(bound)
    assert frame_invariance(outs, first_t, frames) == {CLOCK: False, SENSED: True}
    assert frame_divergences(outs, first_t, frames) == set()
    assert frame_divergences(outs, first_t, frames, sensed_invariant=False) == {"time_spread"}


def test_frameless_automation_is_judged_in_the_clock_frame():
    bound = {"trigger": [{"platform": "homeassistant", "event": "start"}], "action": ACTION}
    clock, frame = judge(bound, {JAN: fire(DAY + 1 * H), JUL: fire(DAY + 9 * H)})
    assert clock == frame == {"time_spread"}


def test_restricted_frame_divergences_drop_a_static_envelope_kind():
    lamp = [Firing(DAY + 22 * H, "a", "light.turn_on", "light.x")]
    outs = {JAN: outcome(lamp, site_of(JAN)), JUL: outcome(lamp, site_of(JUL))}
    first_t = {JAN: DAY + 22 * H, JUL: DAY + 22 * H}
    frames = frames_of({"trigger": [{"platform": "time", "at": "22:00:00"}]})
    assert frame_divergences(outs, first_t, frames) == {"night_light"}
    assert frame_restricted_divergences(outs, first_t, frames, [JAN, JUL]) == set()


def test_rich_outcome_counts_payloads_and_branches():
    a = [Firing(DAY + 7 * H, "a", "light.turn_on", "light.x", (("state", "ON"), ("brightness", 128))),
         Firing(DAY + 8 * H, "a", "light.turn_on", "light.x", (("state", "ON"), ("brightness", 128))),
         Firing(0.5 * DAY, "a", "light.turn_on", "light.x")]  # warm-up: left out
    r = rich_outcome(a, [(DAY + 7 * H, 0, 1), (DAY + 8 * H, 0, 1), (10.0, 0, -1)])
    assert r["first_t"] == DAY + 7 * H and r["count"] == 2 and r["branches"] == [[0, 1]] and r["payload"]
    dim = [Firing(DAY + 7 * H, "a", "light.turn_on", "light.x", (("state", "ON"), ("brightness", 20)))]
    r2 = rich_outcome(dim, [(DAY + 7 * H, 0, -1)])
    assert rich_divergences({JAN: r, JUL: r}) == set()
    assert rich_divergences({JAN: r, JUL: r2}) == {"count_differs", "payload_differs", "branch_differs"}
    assert rich_divergences({JAN: r, JUL: rich_outcome([])}) == set()  # a silent environment is not compared
