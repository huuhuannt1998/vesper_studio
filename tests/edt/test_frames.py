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


# ---- protocol Addendum 8: the crossing-time sensed reference (A8.5) and the first firing's frame (A8.4) -----------

TEMP = "sensor.living_room_temperature"
HEAT = {"trigger": [{"platform": "numeric_state", "entity_id": TEMP, "above": 20}], "action": ACTION}


def test_sensed_crossing_refs_are_numeric_state_triggers_with_numeric_bounds():
    from vesper.edt.oracle import SensedRef, sensed_crossing_refs
    assert sensed_crossing_refs(HEAT) == (SensedRef((TEMP,), None, 20.0, None),)
    both = {"trigger": [{"platform": "numeric_state", "entity_id": [TEMP, "binary_sensor.hallway_motion"],
                         "above": "10", "below": 25, "attribute": "x"}]}
    assert sensed_crossing_refs(both) == (SensedRef((TEMP,), "x", 10.0, 25.0),)
    assert sensed_crossing_refs({"trigger": [{"platform": "numeric_state", "entity_id": TEMP,
                                              "above": "input_number.target"}]}) == ()
    assert sensed_crossing_refs({"trigger": [{"platform": "numeric_state", "entity_id": TEMP, "above": 1,
                                              "value_template": "{{ state.state }}"}]}) == ()
    assert sensed_crossing_refs({"trigger": [{"platform": "state", "entity_id": TEMP}],
                                 "condition": [{"condition": "numeric_state", "entity_id": TEMP, "above": 3}]}) == ()
    assert sensed_crossing_refs({"trigger": [{"platform": "sun", "event": "sunset"}]}) == ()


def test_first_crossing_is_the_first_entry_into_the_range_in_the_window():
    from vesper.edt.oracle import first_crossing
    lo, hi = DAY, 3 * DAY
    series = [(0.0, "18"), (DAY + 100, "19"), (DAY + 200, "21"), (DAY + 300, "19"), (DAY + 400, "22")]
    assert first_crossing(series, 20.0, None, lo, hi) == DAY + 200
    assert first_crossing([(0.0, "18"), (100.0, "21"), (DAY + 5, "19"), (DAY + 50, "22")], 20.0, None, lo, hi) \
        == DAY + 50  # a crossing on day 0 is not in the judged window
    assert first_crossing([(0.0, "unknown"), (DAY + 1, "21")], 20.0, None, lo, hi) is None
    assert first_crossing([(0.0, "25"), (DAY + 9, "15")], 10.0, 20.0, lo, hi) == DAY + 9
    assert first_crossing([(0.0, "25"), (DAY + 9, "15")], None, 20.0, lo, hi) == DAY + 9
    assert first_crossing([(0.0, "21"), (DAY + 9, "22")], 20.0, None, lo, hi) is None  # never armed
    assert first_crossing([(0.0, "18"), (3 * DAY, "22")], 20.0, None, lo, hi) is None  # at the run's end


def test_crossing_instant_is_the_earliest_over_refs_and_entities():
    from vesper.edt.oracle import SensedRef, crossing_instant
    refs = (SensedRef(("sensor.a", "sensor.b"), None, 20.0, None), SensedRef(("sensor.c",), "t", None, 5.0))
    series = {"sensor.a": [(0.0, "18"), (DAY + 900, "21")], "sensor.b": [(0.0, "18"), (DAY + 700, "21")],
              "sensor.c|t": [(0.0, "8"), (DAY + 800, "4")]}
    assert crossing_instant(refs, series, DAY, 3 * DAY) == DAY + 700
    assert crossing_instant(refs, {}, DAY, 3 * DAY) is None


def test_the_crossing_reference_is_off_by_default_and_flags_an_environment_dependent_latency():
    outs = {JAN: outcome(fire(DAY + 6 * H), site_of(JAN)), JUL: outcome(fire(DAY + 14 * H), site_of(JUL))}
    first_t = {JAN: DAY + 6 * H, JUL: DAY + 14 * H}
    f = frames_of(HEAT)
    assert frame_divergences(outs, first_t, f) == set()  # as published: the sensed frame is exempt
    assert frame_divergences(outs, first_t, f, sensed_crossing=None) == set()
    # the firing follows the crossing at once in both: invariant in the sensed frame
    at_once = {JAN: DAY + 6 * H, JUL: DAY + 14 * H}
    assert frame_divergences(outs, first_t, f, sensed_crossing=at_once) == set()
    # crossed at 06:00 in both, fired 8 h later in July: an environment-dependent latency
    late = {JAN: DAY + 6 * H, JUL: DAY + 6 * H}
    assert frame_divergences(outs, first_t, f, sensed_crossing=late) == {"time_spread"}
    assert frame_invariance(outs, first_t, f, sensed_crossing=late) == {SENSED: False}
    # fewer than two environments with a crossing: invariant
    assert frame_divergences(outs, first_t, f, sensed_crossing={JAN: DAY, JUL: None}) == set()
    # a clock+sensed automation needs both frames to diverge
    mixed = frames_of({**HEAT, "condition": [{"condition": "time", "after": "05:00"}]})
    assert frame_divergences(outs, first_t, mixed, sensed_crossing=late) == {"time_spread"}
    near = {JAN: DAY + 6 * H, JUL: DAY + 7 * H}
    outs2 = {JAN: outcome(fire(DAY + 6 * H), site_of(JAN)), JUL: outcome(fire(DAY + 7 * H), site_of(JUL))}
    assert frame_divergences(outs2, {JAN: DAY + 6 * H, JUL: DAY + 7 * H}, mixed, sensed_crossing=near) == set()


def test_first_firing_frames_name_the_triggers_that_could_have_fired_then():
    from vesper.edt.oracle import first_firing_frames
    bound = {"trigger": [{"platform": "time", "at": "07:00:00"}, {"platform": "sun", "event": "sunset"},
                         {"platform": "numeric_state", "entity_id": TEMP, "above": 20, "for": "00:05:00"},
                         {"platform": "state", "entity_id": "binary_sensor.hallway_motion", "to": "on"}],
             "action": ACTION}
    site = site_of(JAN)
    changes = {TEMP: [DAY + 3 * H + 0.05], "binary_sensor.hallway_motion": [DAY + 4 * H]}
    assert first_firing_frames(bound, DAY + 7 * H, site, changes) == {CLOCK}
    assert first_firing_frames(bound, sunset(JAN), site, changes) == {SUN}
    assert first_firing_frames(bound, DAY + 3 * H + 0.05 + 300, site, changes) == {SENSED}
    assert first_firing_frames(bound, DAY + 4 * H, site, changes) == {"none"}
    assert first_firing_frames(bound, DAY + 5 * H + 17, site, changes) == set()  # e.g. after a delay
    pattern = {"trigger": [{"platform": "time_pattern", "minutes": "/15"}, {"platform": "time", "at": "06:30"}]}
    assert first_firing_frames(pattern, DAY + 6 * H + 30 * 60, site, {}) == {CLOCK}
    assert first_firing_frames(pattern, DAY + 6 * H + 31 * 60, site, {}) == set()
    sun_state = {"trigger": [{"platform": "state", "entity_id": "sun.sun", "to": "below_horizon"}]}
    assert first_firing_frames(sun_state, sunset(JAN) + 1, site, {}) == {SUN}
    tpl = {"trigger": [{"platform": "template", "value_template": f"{{{{ states('{TEMP}')|float > 20 }}}}"},
                       {"platform": "template", "value_template": "{{ now().hour == 7 }}"}]}
    assert first_firing_frames(tpl, DAY + 3 * H + 0.05, site, changes) == {SENSED}
    assert first_firing_frames(tpl, DAY + 7 * H, site, changes) == {CLOCK}


def test_env_latency_is_off_by_default_and_holds_only_for_a_waiting_spread():
    """Protocol A12.10: env_latency, a kind of the named variant frame+crossing+env_latency."""
    from vesper.edt.oracle import ENV_LATENCY, env_latency
    outs = {JAN: outcome(fire(DAY + 6 * H), site_of(JAN)), JUL: outcome(fire(DAY + 14 * H), site_of(JUL))}
    first_t = {JAN: DAY + 6 * H, JUL: DAY + 14 * H}
    late = {JAN: DAY + 6 * H, JUL: DAY + 6 * H}  # crossed at 06:00 in both; fired 0 h and 8 h later
    f = frames_of(HEAT)
    # off by default: the published relation and A8.5's option are unchanged
    assert frame_divergences(outs, first_t, f, sensed_crossing=late) == {"time_spread"}
    assert frame_divergences(outs, first_t, f, sensed_crossing=late, env_latency=False) == {"time_spread"}
    assert frame_divergences(outs, first_t, f) == set()
    # on: the waiting spread of 8 h > 3 h is env_latency
    assert env_latency(outs, first_t, late) is True
    assert frame_divergences(outs, first_t, f, sensed_crossing=late, env_latency=True) == {"time_spread", ENV_LATENCY}
    # a firing before its crossing in some environment ("precede") is not env_latency, though the spread is large
    precede = {JAN: DAY + 6 * H, JUL: DAY + 20 * H}
    assert env_latency(outs, first_t, precede) is False
    assert ENV_LATENCY not in frame_divergences(outs, first_t, f, sensed_crossing=precede, env_latency=True)
    # within the tolerance, or fewer than two latencies: not env_latency
    assert env_latency(outs, first_t, {JAN: DAY + 6 * H, JUL: DAY + 12 * H}) is False
    assert env_latency(outs, first_t, {JAN: DAY + 6 * H, JUL: None}) is False
    assert frame_divergences(outs, first_t, f, env_latency=True) == set()  # needs the crossing reference
    # a clock+sensed automation can carry env_latency without time_spread (its clock frame is invariant)
    mixed = frames_of({**HEAT, "condition": [{"condition": "time", "after": "05:00"}]})
    outs2 = {JAN: outcome(fire(DAY + 6 * H), site_of(JAN)), JUL: outcome(fire(DAY + 7 * H), site_of(JUL))}
    ft2 = {JAN: DAY + 6 * H, JUL: DAY + 7 * H}
    early = {JAN: DAY + 6 * H, JUL: DAY + 2 * H}  # the July crossing waited 5 h for the condition
    assert frame_divergences(outs2, ft2, mixed, sensed_crossing=early, env_latency=True) == {ENV_LATENCY}
    # restricted to a pair, the kind is judged on the pair alone
    assert frame_restricted_divergences(outs2, ft2, mixed, [JAN, JUL], sensed_crossing=early,
                                        env_latency=True) == {ENV_LATENCY}
