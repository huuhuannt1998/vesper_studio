"""The engine's coverage of Home Assistant's common automation features (EDT task 12): each one with Home Assistant's
documented semantics; strict mode refuses what it cannot do faithfully, permissive mode records it."""

import pytest

from tests.lab.test_automations import lab
from vesper.lab.automations import Engine

H = 3600


def setup(tmp_path, autos, permissive=False, start="07:00", text_home=None):
    """(entity ids by device, session, engine, the commands it sent): the engine started at `start`."""
    if text_home is None:
        _, ids, s = lab(tmp_path, "[]\n", start=start)
    else:
        ids, s = text_home(tmp_path)
    s.start(spawn_player=False, residents=False)
    calls = []
    s.automation_command = lambda *args: calls.append(args)
    e = Engine(s, permissive=permissive, automations=autos(ids) if callable(autos) else autos)
    e.start()
    return ids, s, e, calls


def at(when="08:00"):
    return [{"trigger": "time", "at": when}]


def auto(actions, triggers=None, **extra):
    return {"alias": "a", "triggers": triggers or at(), "actions": actions, **extra}


def refusal(tmp_path, autos, permissive=False):
    _, _, e, _ = setup(tmp_path, autos, permissive)
    assert not e.automations
    return e.refused[0]["reason"]


def services(calls):
    return [c[2] for c in calls]


# ---- what Home Assistant accepts and ignores ----------------------------------------------------------------------

def test_max_hide_entity_and_an_actions_metadata_are_accepted_as_home_assistant_accepts_them(tmp_path):
    def autos(ids):
        lamp = ids["bedroom_lamp"]["state"]
        return [auto([{"action": "light.turn_on", "metadata": {}, "data": {}, "target": {"entity_id": lamp}}],
                     max=10, max_exceeded="silent", hide_entity=False)]
    _, s, e, calls = setup(tmp_path, autos)
    assert e.refused == []
    s.clock.advance(1.5 * H)
    assert services(calls) == ["light.turn_on"]


def test_numbers_given_as_strings_are_coerced_as_home_assistant_coerces_them(tmp_path):
    """numeric_state above/below: vol.Coerce(float); a duration or offset given as a number string is seconds."""
    def autos(ids):
        temp, lamp = ids["kitchen_climate"]["temperature"], ids["bedroom_lamp"]["state"]
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp}}],
                     [{"trigger": "numeric_state", "entity_id": temp, "above": "30", "for": "60"}])]
    ids, s, e, calls = setup(tmp_path, autos)
    (a,) = e.automations
    assert (a.triggers[0]["above"], a.triggers[0]["for"]) == (30.0, 60.0)
    assert e.refused == []


def test_an_entity_id_in_data_or_data_template_is_the_target_as_in_home_assistants_legacy_form(tmp_path):
    def autos(ids):
        lamp = ids["bedroom_lamp"]["state"]
        return [auto([{"service": "light.turn_on", "data": {"entity_id": lamp, "brightness_pct": 50}},
                      {"service": "light.turn_off", "data_template": {"entity_id": [lamp]}}])]
    ids, s, e, calls = setup(tmp_path, autos)
    assert e.refused == []
    lamp = ids["bedroom_lamp"]["state"]
    assert [(x["service"], x["entities"]) for x in e.automations[0].actions] == [
        ("light.turn_on", [lamp]), ("light.turn_off", [lamp])]
    assert e.automations[0].actions[0]["brightness"] == round(50 * 255 / 100)


def test_light_parameters_a_light_does_not_support_are_dropped_as_home_assistant_drops_them(tmp_path):
    """Home Assistant's light service filters out a colour the light has no colour mode for (filter_turn_on_params);
    the bedroom lamp is brightness-only, so an rgb_color is dropped and the lamp turns on."""
    def autos(ids):
        lamp = ids["bedroom_lamp"]["state"]
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp},
                       "data": {"rgb_color": [255, 0, 0], "color_temp_kelvin": 2700, "brightness": 128}}])]
    _, s, e, calls = setup(tmp_path, autos)
    assert e.refused == [] and e.automations[0].approximations == []
    assert e.automations[0].actions[0]["brightness"] == 128


@pytest.mark.parametrize("key, value", [("kelvin", 2500), ("color_temp", 370)])
def test_kelvin_and_mireds_stop_the_run_as_home_assistant_2026_10_refuses_them(tmp_path, key, value):
    """Home Assistant 2026.10.2's light.turn_on schema has no `kelvin` and no `color_temp` (mireds; only
    color_temp_kelvin): the call fails, the run stops there, and what came before it ran (protocol Addendum 10)."""
    def autos(ids):
        lamp = ids["bedroom_lamp"]["state"]
        return [auto([{"action": "notify.phone"},
                      {"action": "light.turn_on", "target": {"entity_id": lamp}, "data": {key: value}},
                      {"action": "notify.after"}])]
    _, s, e, calls = setup(tmp_path, autos, permissive=True)
    assert e.refused == []
    s.clock.advance(1.5 * H)
    assert services(calls) == ["notify.phone"]


def test_a_transition_is_refused_in_strict_mode_and_dropped_in_permissive_mode_with_a_note(tmp_path):
    def autos(ids):
        lamp = ids["bedroom_lamp"]["state"]
        return [auto([{"action": "light.turn_off", "target": {"entity_id": lamp}, "data": {"transition": 2}}])]
    assert refusal(tmp_path, autos) == ("action light.turn_off: data 'transition' is not supported (the twin has no "
                                        "fade)")
    (tmp_path / "p").mkdir()
    _, s, e, calls = setup(tmp_path / "p", autos, permissive=True)
    assert e.refused == [] and e.automations[0].approximations == ["light.turn_off: transition ignored"]
    s.clock.advance(1.5 * H)
    assert services(calls) == ["light.turn_off"]


def test_homeassistant_turn_on_off_and_toggle_call_each_entitys_own_domain(tmp_path):
    def autos(ids):
        lamp = ids["bedroom_lamp"]["state"]
        return [auto([{"action": "homeassistant.turn_on", "target": {"entity_id": lamp}},
                      {"action": "homeassistant.toggle", "entity_id": lamp},
                      {"action": "homeassistant.turn_off", "target": {"entity_id": [lamp]}}])]
    ids, s, e, calls = setup(tmp_path, autos)
    assert e.refused == []
    assert [x["service"] for x in e.automations[0].actions] == ["light.turn_on", "light.toggle", "light.turn_off"]


def test_homeassistant_turn_on_skips_an_entity_whose_domain_has_no_turn_on_as_home_assistant_does(tmp_path):
    """Home Assistant logs "does not support entities" and calls the other domains' services."""
    def autos(ids):
        lock, lamp = ids["front_lock"]["Door Lock.LockState"], ids["bedroom_lamp"]["state"]
        return [auto([{"action": "homeassistant.turn_on", "target": {"entity_id": [lock, lamp]}},
                      {"action": "homeassistant.turn_off", "target": {"entity_id": lock}}])]
    _, s, e, calls = setup(tmp_path, autos)
    assert e.refused == []
    s.clock.advance(1.5 * H)
    assert services(calls) == ["light.turn_on"]


# ---- conditions ----------------------------------------------------------------------------------------------------

def lamp_motion(ids):
    return ids["bedroom_lamp"]["state"], ids["hall_motion"]["occupancy"]


def test_and_or_not_nest_and_the_shorthand_forms_are_home_assistants(tmp_path):
    """`not` holds when none of its conditions holds; `- or: [...]` is the shorthand of `condition: or`; a
    condition's `conditions` may be one condition."""
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        on = {"condition": "state", "entity_id": lamp, "state": "on"}
        moving = {"condition": "state", "entity_id": motion, "state": "on"}
        return [auto([], conditions=[{"or": [on, {"condition": "not", "conditions": moving}]}]),
                auto([], conditions={"condition": "and", "conditions": [on, {"and": [moving]}]})]
    ids, s, e, _ = setup(tmp_path, autos)
    lamp, motion = lamp_motion(ids)
    either, both = (a.conditions[0] for a in e.automations)
    table = []
    for lamp_state in ("off", "on"):
        for motion_state in ("off", "on"):
            e._state[lamp], e._state[motion] = lamp_state, motion_state
            table.append((e._holds(either, {}), e._holds(both, {})))
    assert table == [(True, False), (False, False), (True, False), (True, True)]


def test_a_trigger_condition_tells_which_trigger_fired_by_id_or_by_index(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([{"choose": [{"conditions": [{"condition": "trigger", "id": "moved"}],
                                   "sequence": [{"action": "light.turn_on", "target": {"entity_id": lamp}}]},
                                  {"conditions": {"condition": "trigger", "id": ["1"]},
                                   "sequence": [{"action": "light.turn_off", "target": {"entity_id": lamp}}]}]}],
                     triggers=[{"trigger": "state", "entity_id": motion, "to": "on", "id": "moved"},
                               {"trigger": "time", "at": "08:00"}])]
    ids, s, e, calls = setup(tmp_path, autos)
    _, motion = lamp_motion(ids)
    e._apply([e._set(motion, "on")])
    s.clock.advance(1.5 * H)  # 08:00 passes: trigger index 1
    assert services(calls) == ["light.turn_on", "light.turn_off"]


# ---- actions -------------------------------------------------------------------------------------------------------

def test_if_then_else_and_a_failed_condition_step_ends_only_its_own_sequence(tmp_path):
    """A condition step that fails ends the sequence it is in (a sub-script in Home Assistant), and the run goes on;
    at the top level it ends the run."""
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        moving = {"condition": "state", "entity_id": motion, "state": "on"}
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        off = {"action": "light.turn_off", "target": {"entity_id": lamp}}
        return [auto([{"if": [moving], "then": [on], "else": [off]},
                      {"sequence": [moving, on]},
                      {"choose": [], "default": [moving, on]},
                      off, moving, on])]
    _, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(1.5 * H)
    assert services(calls) == ["light.turn_off", "light.turn_off"]


def test_stop_ends_the_whole_run_from_inside_a_sequence(tmp_path):
    def autos(ids):
        lamp, _ = lamp_motion(ids)
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        return [auto([on, {"if": [{"condition": "time", "after": "07:30"}], "then": [{"stop": "done"}]}, on])]
    _, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(1.5 * H)
    assert services(calls) == ["light.turn_on"] and not e.automations[0].running


def test_repeat_count_while_and_until_loop_as_home_assistants_repeat(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        toggle = {"action": "light.toggle", "target": {"entity_id": lamp}}
        morning = {"condition": "time", "after": "08:30"}  # false at 08:00, true at 09:00
        return [auto([{"repeat": {"count": 3, "sequence": [toggle]}}]),
                auto([{"repeat": {"while": [morning], "sequence": [toggle]}}]),
                auto([{"repeat": {"until": [morning], "sequence": [toggle]}}], triggers=at("09:00"))]
    ids, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(3.5 * H)
    # count: 3; while: its condition is false before the first iteration, 0; until: checked after, so 1
    assert [c[0] for c in calls] == ["a"] * 4


def test_a_repeat_without_end_is_stopped_as_home_assistant_stops_it(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([{"repeat": {"while": [{"condition": "state", "entity_id": motion, "state": "off"}],
                                  "sequence": [{"action": "light.toggle", "target": {"entity_id": lamp}}]}}])]
    _, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(1.5 * H)
    assert len(calls) == 10000 and not e.automations[0].running


def test_wait_for_trigger_resumes_on_the_trigger_or_at_the_timeout(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        moved = [{"trigger": "state", "entity_id": motion, "to": "on"}]
        return [auto([{"wait_for_trigger": moved, "timeout": "00:10:00"}, on]),
                auto([{"wait_for_trigger": moved, "timeout": 60, "continue_on_timeout": False}, on],
                     triggers=at("08:30")),
                auto([{"wait_for_trigger": moved}, on], triggers=at("09:00"))]
    ids, s, e, calls = setup(tmp_path, autos)
    _, motion = lamp_motion(ids)
    s.clock.advance(H + 5 * 60)  # 08:05: the first waits
    assert calls == []
    e._apply([e._set(motion, "on")])
    s.clock.advance(1)
    assert [c[1] for c in calls] == ["time 28800"]  # resumed by the motion
    s.clock.advance(2.5 * H)  # 08:30: times out and stops; 09:00: waits for good
    assert len(calls) == 1 and e.automations[2].running
    e._apply([e._set(motion, "off")])
    e._apply([e._set(motion, "on")])
    s.clock.advance(1)
    assert len(calls) == 2


def test_a_wait_for_a_time_resumes_at_that_time(tmp_path):
    def autos(ids):
        lamp, _ = lamp_motion(ids)
        return [auto([{"wait_for_trigger": [{"trigger": "time", "at": "09:00"}]},
                      {"action": "light.turn_on", "target": {"entity_id": lamp}}])]
    _, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(1.9 * H)
    assert calls == []
    s.clock.advance(0.2 * H)
    assert services(calls) == ["light.turn_on"]


@pytest.mark.parametrize("step, reason", [
    ({"wait_template": "{{ is_state('sun.sun', 'above_horizon') }}"},
     "action wait_template is not supported (a template wait)"),
    ({"parallel": []}, "action parallel is not supported (parallel branches)"),
    ({"event": "my_event"}, "action event is not supported (it fires an event, which only Home Assistant's other "
                            "listeners see)"),
    ({"device_id": "abc", "domain": "light", "type": "turn_on"},
     "action device_id is not supported (a device action needs Home Assistant's device registry)"),
])
def test_what_the_engine_cannot_run_faithfully_is_refused_precisely(tmp_path, step, reason):
    assert refusal(tmp_path, [auto([step])]) == reason


def test_a_fired_event_is_recorded_in_permissive_mode(tmp_path):
    _, s, e, calls = setup(tmp_path, [auto([{"event": "my_event", "event_data": {"x": 1}}])], permissive=True)
    s.clock.advance(1.5 * H)
    assert services(calls) == ["event.my_event"]


# ---- time, time_pattern, homeassistant ----------------------------------------------------------------------------

def sited_home(tmp_path):
    """A home at Charlotte; its first day, 2026-06-21, is a Sunday; it starts at 07:30."""
    from tests.lab.homes import catalog, write_home
    from tests.lab.test_environment_session import SITED
    from vesper.lab.automations import entity_ids
    from vesper.lab.home import load_lab_home
    from vesper.lab.session import Session
    from vesper.twins.clock import LogicalClock
    home = load_lab_home(write_home(tmp_path, SITED), catalog())
    return entity_ids(home), Session(home, clock=LogicalClock())


def test_time_weekday_in_a_condition_and_a_trigger_follow_the_sites_calendar(tmp_path):
    def autos(ids):
        lamp, _ = lamp_motion(ids)
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        return [auto([on], at("08:00"), alias="sunday", conditions=[{"condition": "time", "weekday": "sun"}]),
                auto([on], at("08:00"), alias="weekdays",
                     conditions=[{"condition": "time", "weekday": ["mon", "tue", "wed", "thu", "fri"]}]),
                auto([on], [{"trigger": "time", "at": "09:00", "weekday": ["mon"]}], alias="monday")]
    _, s, e, calls = setup(tmp_path, autos, text_home=sited_home)
    assert e.refused == []
    s.clock.advance(2 * 24 * H)  # Sunday 07:30 to Tuesday 07:30
    assert [(c[0], c[1]) for c in calls] == [("sunday", "time 28800"), ("weekdays", "time 28800"),
                                              ("monday", "time 32400")]


def test_a_weekday_needs_a_site_for_its_date(tmp_path):
    assert refusal(tmp_path, [auto([], conditions=[{"condition": "time", "weekday": "mon"}])]) == (
        "condition time: weekday: the home has no site, so it has no date (add site: to home.yaml)")


@pytest.mark.parametrize("pattern, first_three", [
    ({"minutes": "/15"}, ["07:15:00", "07:30:00", "07:45:00"]),  # hours every hour, seconds 0
    ({"hours": 9}, ["09:00:00", "09:00:00+1d", "09:00:00+2d"]),  # minutes and seconds 0
    ({"seconds": "/20"}, ["07:00:20", "07:00:40", "07:01:00"]),
    ({"hours": "/4", "minutes": 30}, ["08:30:00", "12:30:00", "16:30:00"]),
    ({"minutes": "*", "seconds": 5}, ["07:00:05", "07:01:05", "07:02:05"]),
])
def test_time_pattern_fires_as_home_assistants_time_change_listener(tmp_path, pattern, first_three):
    def autos(ids):
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp_motion(ids)[0]}}],
                     [{"trigger": "time_pattern", **pattern}])]
    _, s, e, calls = setup(tmp_path, autos)
    times = []
    s.automation_command = lambda *args: times.append(s.clock.now())
    s.clock.advance(3 * 24 * H)

    def clock(t):
        day, sec = divmod(int(t), 86400)
        text = f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
        return text + (f"+{day}d" if day else "")
    assert [clock(t + 7 * H) for t in times[:3]] == first_three  # the session starts at 07:00


@pytest.mark.parametrize("pattern, reason", [
    ({}, "trigger time_pattern: hours, minutes or seconds is required"),
    ({"minutes": "/0"}, "trigger time_pattern: minutes: '/0' is not a time pattern"),
    ({"hours": 24}, "trigger time_pattern: hours: 24 is not a time pattern"),
])
def test_a_time_pattern_home_assistant_refuses_is_refused(tmp_path, pattern, reason):
    assert refusal(tmp_path, [auto([], [{"trigger": "time_pattern", **pattern}])]) == reason


def test_homeassistant_start_fires_once_when_the_engine_starts_and_shutdown_never(tmp_path):
    def autos(ids):
        on = {"action": "light.turn_on", "target": {"entity_id": lamp_motion(ids)[0]}}
        return [auto([on], [{"trigger": "homeassistant", "event": "start"}], alias="start"),
                auto([on], [{"platform": "homeassistant", "event": "shutdown"}], alias="shutdown")]
    _, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(24 * H)
    assert [(c[0], c[1]) for c in calls] == [("start", "homeassistant start")]


@pytest.mark.parametrize("trigger, reason", [
    ({"trigger": "event", "event_type": "zha_event"},
     "trigger event is not supported (a device's button or remote event)"),
    ({"platform": "event", "event_type": "mobile_app_notification_action"},
     "trigger event is not supported (a phone app's event)"),
    ({"platform": "event", "event_type": "telegram_callback"},
     "trigger event is not supported (a messaging or web service's event)"),
    ({"platform": "event", "event_type": "call_service"},
     "trigger event is not supported (a Home Assistant internal event)"),
    ({"platform": "event", "event_type": "my_custom_event"},
     "trigger event is not supported (a custom event another automation or script fires)"),
    ({"platform": "webhook", "webhook_id": "x"}, "trigger webhook is not supported (it needs an HTTP request)"),
    ({"platform": "mqtt", "topic": "x"}, "trigger mqtt is not supported (it needs an MQTT message)"),
    ({"platform": "tag", "tag_id": "x"}, "trigger tag is not supported (it needs an NFC tag scan)"),
])
def test_triggers_that_need_a_device_a_resident_or_a_service_are_refused_by_class(tmp_path, trigger, reason):
    assert refusal(tmp_path, [auto([], [trigger])]) == reason


# ---- states, attributes ------------------------------------------------------------------------------------------

def fired_by(calls):
    return [c[0] for c in calls]


def test_not_to_and_not_from_as_home_assistants_state_trigger(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        return [auto([on], [{"trigger": "state", "entity_id": motion, "not_from": ["unknown", "unavailable"],
                             "not_to": "unavailable"}])]
    ids, s, e, calls = setup(tmp_path, autos)
    _, motion = lamp_motion(ids)
    for state in ("unknown", "on", "off", "unavailable", "on"):
        e._apply([e._set(motion, state)])
        s.clock.advance(1)
    assert len(calls) == 2  # on -> off; unknown -> on (not_from), off -> unavailable (not_to), unavailable -> on no


def test_with_no_to_or_from_an_attribute_change_fires_and_with_to_null_it_does_not(tmp_path):
    def autos(ids):
        lamp, _ = lamp_motion(ids)
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        return [auto([on], [{"trigger": "state", "entity_id": lamp}], alias="any"),
                auto([on], [{"trigger": "state", "entity_id": lamp, "to": None}], alias="state"),
                auto([on], [{"trigger": "state", "entity_id": lamp, "attribute": "brightness", "to": 120}],
                     alias="attribute"),
                auto([on], [{"trigger": "numeric_state", "entity_id": lamp, "attribute": "brightness",
                             "above": 100}], alias="numeric")]
    ids, s, e, calls = setup(tmp_path, autos)
    lamp, _ = lamp_motion(ids)
    e._apply([e._set(lamp, "on", {"brightness": 90})])
    s.clock.advance(1)
    assert sorted(fired_by(calls)) == ["any", "state"]
    calls.clear()
    e._apply([e._set(lamp, "on", {"brightness": 120})])
    s.clock.advance(1)
    assert sorted(fired_by(calls)) == ["any", "attribute", "numeric"]


def test_a_lights_brightness_attribute_is_home_assistants_scaled_brightness_and_none_while_off(tmp_path):
    ids, s, e, _ = setup(tmp_path, [])
    lamp, _ = lamp_motion(ids)
    twin = e.entities[lamp].twin_id
    scale = e.entities[lamp].cfg["brightness_scale"]
    e._on_report(type("R", (), {"twin_id": twin, "feature": "state", "value": "ON", "observed_t": 0})())
    e._on_report(type("R", (), {"twin_id": twin, "feature": "brightness", "value": 127, "observed_t": 0})())
    assert e._state[lamp] == "on" and e._attrs[lamp]["brightness"] == -(-127 * 255 // scale)
    e._on_report(type("R", (), {"twin_id": twin, "feature": "state", "value": "OFF", "observed_t": 0})())
    assert e._attrs[lamp]["brightness"] is None


def test_state_conditions_with_attribute_for_and_match_any(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([], conditions=[{"condition": "state", "entity_id": [lamp, motion], "state": "on",
                                      "match": "any"},
                                     {"condition": "state", "entity_id": lamp, "attribute": "brightness",
                                      "state": 120},
                                     {"condition": "state", "entity_id": lamp, "state": "on",
                                      "for": {"minutes": 5}}])]
    ids, s, e, _ = setup(tmp_path, autos)
    lamp, motion = lamp_motion(ids)
    anyone, bright, held = e.automations[0].conditions
    e._apply([e._set(lamp, "on", {"brightness": 120})])
    assert (e._holds(anyone, {}), e._holds(bright, {}), e._holds(held, {})) == (True, True, False)
    s.clock.advance(5 * 60 + 1)
    e._apply([e._set(lamp, "on", {"brightness": 90})])  # an attribute change keeps last_changed
    assert (e._holds(bright, {}), e._holds(held, {})) == (False, True)


def test_a_numeric_state_threshold_may_be_a_sensor_whose_state_is_read_when_checked(tmp_path):
    def autos(ids):
        temp, battery = ids["kitchen_climate"]["temperature"], ids["kitchen_climate"]["battery"]
        return [auto([], conditions=[{"condition": "numeric_state", "entity_id": temp, "above": battery}])]
    ids, s, e, _ = setup(tmp_path, autos)
    temp, hum = ids["kitchen_climate"]["temperature"], ids["kitchen_climate"]["battery"]
    (c,) = e.automations[0].conditions
    e._state[temp], e._state[hum] = "21.0", "40"
    assert not e._holds(c, {})
    e._state[hum] = "20"
    assert e._holds(c, {})
    e._state[hum] = "unknown"
    assert not e._holds(c, {})


def test_a_climate_entity_is_its_hvac_mode_with_setpoint_current_temperature_and_action_attributes(tmp_path):
    ids, s, e, _ = setup(tmp_path, [], text_home=sited_home)
    climate = next(x for x in ids["bedroom_thermostat"].values() if x.startswith("climate."))
    attrs = e._attrs[climate]
    assert e._state[climate] == "heat" and attrs["temperature"] == 25
    assert {"current_temperature", "hvac_modes", "min_temp", "max_temp"} <= set(attrs)
    assert "heat" in attrs["hvac_modes"]


def test_sun_sun_has_home_assistants_elevation_azimuth_and_next_event_attributes(tmp_path):
    import datetime as dt

    from astral import Observer
    from astral.sun import elevation, sunset
    _, s, e, _ = setup(tmp_path, [], text_home=sited_home)
    site = e.site
    obs = Observer(site.lat, site.lon, site.elevation_m)
    attrs = e._attrs["sun.sun"]
    assert attrs["next_setting"] == sunset(obs, site.date, tzinfo=dt.UTC).isoformat()
    assert attrs["elevation"] == round(elevation(obs, site.utc(7.5 * H)), 2) and attrs["rising"] is True
    updates = []
    apply = e._apply

    def spy(changes):
        updates.extend((s.clock.now(), c[4]["elevation"]) for c in changes if c[0] == "sun.sun" and c[3].get(
            "elevation") != c[4].get("elevation"))
        apply(changes)
    e._apply = spy
    s.clock.advance(3 * H)  # 07:30 to 10:30: the sun is above 10 degrees from about 08:15 (the day phase)
    late = [(t, el) for t, el in updates if t > 1.5 * H]
    assert {b[0] - a[0] for a, b in zip(late, late[1:])} == {240.0}  # every 4 minutes
    assert all(el == round(elevation(obs, site.utc(t + 7.5 * H)), 2) for t, el in updates)


def test_a_numeric_state_on_the_suns_elevation_fires_in_the_evening(tmp_path):
    def autos(ids):
        lamp, _ = lamp_motion(ids)
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp}}],
                     [{"trigger": "numeric_state", "entity_id": "sun.sun", "attribute": "elevation", "below": 5}])]
    from vesper.env.sun import sun_events
    _, s, e, calls = setup(tmp_path, autos, text_home=sited_home)
    times = []
    s.automation_command = lambda *args: times.append(s.clock.now())
    s.clock.advance(20 * H)
    sunset = sun_events(e.site, 0)["sunset"]
    assert len(times) == 1 and sunset - 1.2 * H < times[0] + 7.5 * H < sunset  # 5 degrees: about 40 minutes before


def test_sun_sun_turns_below_horizon_at_sunset_as_home_assistant_2026_10_updates_it(tmp_path):
    # Home Assistant 2026.10.2 (sun/entity.py): update_events runs at the next event itself (not a second later) and
    # the state is above_horizon while the rounded elevation is above -0.833 degrees (protocol Addendum 10)
    from vesper.env.sun import sun_events
    _, s, e, _ = setup(tmp_path, [], text_home=sited_home)
    sunset = sun_events(e.site, 0)["sunset"] - 7.5 * H  # from the session's start
    s.clock.advance(sunset - 0.5)
    assert e._state["sun.sun"] == "above_horizon"
    s.clock.advance(1)
    assert e._state["sun.sun"] == "below_horizon"


def test_sun_sun_turns_above_horizon_at_the_first_position_update_above_the_horizon(tmp_path):
    from astral import Observer
    from astral.sun import elevation

    from vesper.env.sun import sun_events
    _, s, e, _ = setup(tmp_path, [], text_home=sited_home)
    obs = Observer(e.site.lat, e.site.lon, e.site.elevation_m)
    flips, apply = [], e._apply

    def spy(changes):
        flips.extend((s.clock.now(), c[2]) for c in changes if c[0] == "sun.sun" and c[1] != c[2])
        apply(changes)
    e._apply = spy
    sunrise = sun_events(e.site, 1)["sunrise"] - 7.5 * H  # the second day's, from the session's start
    s.clock.advance(sunrise + 0.5)
    assert e._state["sun.sun"] == "below_horizon"  # at sunrise the rounded elevation is not yet above -0.833
    s.clock.advance(300)
    (t, state), = [f for f in flips if f[0] > sunrise - 1]
    assert state == "above_horizon" and 0 < t - sunrise <= 120.5
    assert round(elevation(obs, e.site.utc(t + 7.5 * H)), 2) > -0.833


def test_sun_sun_position_updates_at_home_assistants_phase_intervals(tmp_path):
    # Home Assistant 2026.10.2 sun/entity.py _PHASE_UPDATES: night 20, astronomical 8, nautical 4, civil twilight 2,
    # small day 2, day 4 minutes (protocol Addendum 10)
    from vesper.lab.automations import SUN_PHASE_UPDATE_S
    assert SUN_PHASE_UPDATE_S == {"night": 1200, "astronomical_twilight": 480, "nautical_twilight": 240,
                                  "twilight": 120, "small_day": 120, "day": 240}
    from vesper.env.sun import sun_events
    _, s, e, _ = setup(tmp_path, [], text_home=sited_home)
    times, apply = [], e._apply

    def spy(changes):
        times.extend(s.clock.now() for c in changes if c[0] == "sun.sun")
        apply(changes)
    e._apply = spy
    sunset = sun_events(e.site, 0)["sunset"] - 7.5 * H
    s.clock.advance(sunset + 20 * 60)  # civil twilight after sunset: every 2 minutes
    after = [t for t in times if t > sunset + 1]
    assert {round(b - a) for a, b in zip(after, after[1:])} == {120}


def test_sun_sun_is_written_once_per_update_as_home_assistant_writes_it(tmp_path):
    from vesper.env.sun import sun_events
    _, s, e, _ = setup(tmp_path, [], text_home=sited_home)
    writes, apply = [], e._apply

    def spy(changes):
        writes.extend(s.clock.now() for c in changes if c[0] == "sun.sun")
        apply(changes)
    e._apply = spy
    sunset = sun_events(e.site, 0)["sunset"] - 7.5 * H
    s.clock.advance(sunset + 1.5)
    assert writes[-1] >= sunset - 1e-6  # the update at sunset
    assert writes.count(writes[-1]) == 1  # the events and the position of one update are one state write


def test_a_trvs_hvac_action_follows_its_running_state_through_the_discovery_template(tmp_path):
    from tests.lab.test_automations_climate import CATALOG, TRV, thermostat_lab
    if not CATALOG.is_dir():
        pytest.skip("catalog/vds is not in this checkout")
    climate, s = thermostat_lab(tmp_path, TRV)
    e = Engine(s, automations=[])
    e.start()
    twin = e.entities[climate].twin_id
    for running, action in (("heat", "heating"), ("idle", "idle")):
        e._on_report(type("R", (), {"twin_id": twin, "feature": "running_state", "value": running,
                                     "observed_t": 0})())
        assert e._attrs[climate]["hvac_action"] == action


# ---- templates -----------------------------------------------------------------------------------------------------

def template_condition(tmp_path, template, text_home=None):
    """(engine, the parsed condition, ids)."""
    _, s, e, _ = setup(tmp_path, [auto([], conditions=[template])], text_home=text_home)
    assert e.refused == [], e.refused
    return e, e.automations[0].conditions[0]


def test_a_template_condition_reads_states_with_home_assistants_functions_and_filters(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        temp = ids["kitchen_climate"]["temperature"]
        return [auto([], conditions=[
            {"condition": "template", "value_template":
                f"{{{{ is_state('{lamp}', 'on') and states('{temp}') | float > 20 }}}}"},
            f"{{{{ states.{motion}.state == 'on' or state_attr('{lamp}', 'brightness') | int(0) > 100 }}}}",
            f"{{{{ states('{temp}') | float > 20 }}}}",
            f"{{{{ states('{temp}') | float(0) > 20 }}}}"])]
    ids, s, e, _ = setup(tmp_path, autos)
    lamp, motion = lamp_motion(ids)
    temp = ids["kitchen_climate"]["temperature"]
    both, either, strict, forgiving = e.automations[0].conditions
    e._state[lamp], e._attrs[lamp], e._state[temp], e._state[motion] = "on", {"brightness": 150}, "21.5", "off"
    assert [e._holds(c, {}) for c in (both, either, strict, forgiving)] == [True, True, True, True]
    e._state[temp], e._attrs[lamp] = "unknown", {"brightness": None}
    # float without a default fails on 'unknown': Home Assistant's condition error, so the condition is false
    assert [e._holds(c, {}) for c in (both, either, strict, forgiving)] == [False, False, False, False]


@pytest.mark.parametrize("text, holds", [("{{ 'on' }}", True), ("{{ 'yes' }}", True), ("{{ 1 }}", True),
                                         ("{{ 2 }}", False), ("{{ 'x' }}", False), ("{{ true }}", True),
                                         (" {{ 'True' }} ", True), ("{{ none }}", False)])
def test_a_template_conditions_result_is_read_as_home_assistants_result_as_boolean(tmp_path, text, holds):
    e, c = template_condition(tmp_path, {"condition": "template", "value_template": text})
    assert e._holds(c, {}) is holds


def test_trigger_variables_and_variables_are_rendered_for_the_conditions_and_actions(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([{"variables": {"twice": "{{ limit * 2 }}"}},
                      {"condition": "template", "value_template": "{{ twice == 6 and trigger.id == 'moved' }}"},
                      {"repeat": {"count": "{{ 2 }}" if False else 2, "sequence": [
                          {"if": "{{ repeat.index == 2 and trigger.to_state.state == 'on' }}",
                           "then": [{"action": "light.turn_on", "target": {"entity_id": lamp}}]}]}}],
                     [{"trigger": "state", "entity_id": motion, "to": "on", "id": "moved"}],
                     variables={"limit": 3, "who": "{{ trigger.entity_id }}"},
                     conditions=[f"{{{{ who == '{motion}' }}}}"])]
    ids, s, e, calls = setup(tmp_path, autos)
    assert e.refused == []
    e._apply([e._set(lamp_motion(ids)[1], "on")])
    s.clock.advance(1)
    assert services(calls) == ["light.turn_on"]


def test_a_template_trigger_fires_when_its_result_turns_true_after_it_was_false(tmp_path):
    """Home Assistant arms a template trigger at setup only if it is false; it fires once per false -> true."""
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        on = {"action": "light.turn_on", "target": {"entity_id": lamp}}
        return [auto([on], [{"trigger": "template", "value_template": f"{{{{ is_state('{motion}', 'on') }}}}"}],
                     alias="motion"),
                auto([on], [{"trigger": "template", "value_template": f"{{{{ is_state('{lamp}', 'off') }}}}"}],
                     alias="already true"),
                auto([on], [{"platform": "template", "value_template": f"{{{{ is_state('{motion}', 'on') }}}}",
                             "for": 60}], alias="held")]
    ids, s, e, calls = setup(tmp_path, autos)
    lamp, motion = lamp_motion(ids)
    e._apply([e._set(lamp, "off")])  # already off at setup: true, not armed
    for state in ("on", "on", "off", "on"):
        e._apply([e._set(motion, state)])
        s.clock.advance(20)
    s.clock.advance(60)
    assert fired_by(calls) == ["motion", "motion", "held"]  # the first `held` was cut short by the off


def test_a_template_reading_the_time_renders_again_at_each_minute(tmp_path):
    def autos(ids):
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp_motion(ids)[0]}}],
                     [{"trigger": "template", "value_template": "{{ now().hour == 8 and now().minute == 30 }}"}])]
    _, s, e, calls = setup(tmp_path, autos, text_home=sited_home)
    times = []
    s.automation_command = lambda *args: times.append(s.clock.now())
    s.clock.advance(24 * H)
    assert times == [H]  # the site's session starts at 07:30: 08:30:00 exactly


@pytest.mark.parametrize("template, reason", [
    ("{{ expand('group.all') | count }}", "condition template: template name 'expand' is not supported"),
    ("{{ area_entities('kitchen') }}", "condition template: template name 'area_entities' is not supported"),
    ("{{ 'x' | slugify }}", "condition template: template filter 'slugify' is not supported"),
    ("{{ ''.__class__ }}", None),
    ("{{ 'a'.encode() }}", "condition template: template method 'encode' is not supported"),
    ("{% import 'x' as y %}{{ y }}", "condition template: template statement 'import' is not supported"),
    ("{{ states('sensor.nowhere') }}", "entity sensor.nowhere is not one of this home's"),
    ("{{ states.light.nowhere.state }}", "entity light.nowhere is not one of this home's"),
    ("{{ x ", "condition template: template syntax error: unexpected end of template, expected 'end of print "
              "statement'."),
])
def test_a_template_outside_the_subset_is_refused_by_name(tmp_path, template, reason):
    _, s, e, _ = setup(tmp_path, [auto([], conditions=[template])])
    if reason is None:  # the sandbox: compiles, but rendering it fails, so the condition is false
        assert e._holds(e.automations[0].conditions[0], {}) is False
    else:
        assert e.refused[0]["reason"] == reason


def test_a_template_needing_a_date_needs_a_site(tmp_path):
    assert refusal(tmp_path, [auto([], conditions=["{{ now().hour > 7 }}"])]) == (
        "condition template: template now(): the home has no site, so it has no date")


def test_brightness_is_coerced_and_clamped_and_brightness_pct_coerced_as_home_assistants_light_schema(tmp_path):
    def autos(ids):
        lamp = lamp_motion(ids)[0]
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp}, "data": {"brightness": 300}},
                      {"action": "light.turn_on", "target": {"entity_id": lamp}, "data": {"brightness": "64"}},
                      {"action": "light.turn_on", "target": {"entity_id": lamp}, "data": {"brightness_pct": "50"}}])]
    _, s, e, _ = setup(tmp_path, autos)
    assert [x["brightness"] for x in e.automations[0].actions] == [255, 64, 128]


def test_light_data_with_a_template_is_rendered_when_the_action_runs(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp},
                       "data": {"brightness": f"{{{{ 200 if is_state('{motion}', 'on') else 20 }}}}"}}],
                     alias="dim"),
                auto([{"action": "light.turn_on", "target": {"entity_id": lamp},
                       "data": {"brightness_pct": "{{ 'bright' }}"}},
                      {"action": "light.turn_off", "target": {"entity_id": lamp}}], alias="bad", triggers=at("09:00"))]
    ids, s, e, calls = setup(tmp_path, autos)
    _, motion = lamp_motion(ids)
    e._apply([e._set(motion, "on")])
    s.clock.advance(3 * H)
    # the lamp's own scale is 254: 200 of Home Assistant's 255; "bright" is not a number, so 'bad' stops there
    assert [(c[0], c[5], c[6]) for c in calls] == [("dim", "state", "ON"), ("dim", "brightness", 199)]


def test_a_service_template_is_refused_in_strict_mode_and_rendered_in_permissive_mode(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([{"service_template": f"{{{{ 'light.turn_on' if is_state('{motion}', 'on') else "
                                           "'light.turn_off' }}", "entity_id": lamp},
                      {"service": "{{ 'notify.' ~ 'phone' }}", "data": {"message": "hi"}}])]
    assert refusal(tmp_path, autos) == "action service_template is not supported (a service chosen when it runs)"
    (tmp_path / "p").mkdir()
    ids, s, e, calls = setup(tmp_path / "p", autos, permissive=True)
    e._apply([e._set(lamp_motion(ids)[1], "on")])
    s.clock.advance(1.5 * H)
    assert [c[2] for c in calls] == ["light.turn_on", "notify.phone"]


def test_home_assistants_math_functions_fail_without_a_default_like_its_other_filters(tmp_path):
    _, s, e, _ = setup(tmp_path, [auto([], conditions=[
        "{{ (cos(0) * 2) | round == 2 and sqrt(16) == 4 and log(100, 10) == 2 and pi > 3 }}",
        "{{ sin('a') == 0 }}", "{{ sin('a', default=1) == 1 }}"])])
    assert [e._holds(c, {}) for c in e.automations[0].conditions] == [True, False, True]


def test_a_repeat_count_may_be_a_template_rendered_when_the_repeat_starts(tmp_path):
    def autos(ids):
        lamp = lamp_motion(ids)[0]
        return [auto([{"repeat": {"count": "{{ 1 + 1 }}", "sequence": [
            {"action": "light.toggle", "target": {"entity_id": lamp}}]}}])]
    _, s, e, calls = setup(tmp_path, autos)
    s.clock.advance(1.5 * H)
    assert services(calls) == ["light.toggle", "light.toggle"]


# ---- untrusted templates are bounded (each payload must end fast, refused at load or failing at run) ---------------

HOSTILE = [
    "{{ range(10**12) | count }}",
    "{{ range(10**9) | list | length }}",
    "{{ 'a' * 10**11 }}",
    "{{ ('a' * 60000) * 60000 }}",
    "{{ 9 ** (9 ** 9) }}",
    "{{ 10 ** 100000 }}",
    "{{ '%999999999d' % 1 }}",
    "{{ '%*d' % (999999999, 1) }}",
    "{{ '{:999999999}'.format(1) }}",
    "{{ '1'.zfill(999999999) }}",
    "{{ 'x' | center(999999999) }}",
    "{{ 'x\nx' | indent(999999999) }}",
    "{{ '%999999999d' | format(1) }}",
    "{{ ('a' * 60000) | replace('a', 'a' * 60000) }}",
    "{{ ('a' * 60000).replace('a', 'a' * 60000) }}",
    "{{ range(100000) | join('x' * 60000) }}",
    "{{ ('x' * 60000).join(range(100000) | map('string')) }}",
    "{{ [['a'] * 60000] * 60000 | sum(start=[]) | length }}",
    "{% set ns = namespace(s='ab') %}{% for i in range(60) %}{% set ns.s = ns.s ~ ns.s %}{% endfor %}{{ ns.s }}",
    "{% for i in range(100000) %}{% for j in range(100000) %}{% endfor %}{% endfor %}",
    "{% for i in range(100000) %}0123456789{% endfor %}",
    "{{ ('a' * 30 ~ 'b') | regex_match('(a+)+$') }}",
    "{{ 'x' | attr('__class__') }}",
    "{{ ''.__class__.__mro__ }}",
    "{{ [1] | batch(10**7, fill_with=0) | list | length }}",
    "{{ [1] | slice(10**7, fill_with=0) | list | length }}",
    "{{ ('{0}' * 1000).format('x' * 60000) }}",
    "{{ ('{}' * 1000).format(*(['x' * 60000] * 1000)) }}",
    "{{ ('%(a)s' * 1000) % {'a': 'x' * 60000} }}",
    "{{ ('%s' * 1000) % ((['x' * 60000] * 1000) | list) }}",
    "{{ ('%(a)s' * 1000) | format(a='x' * 60000) }}",
    "{{ ([('x' * 60000)] * 1000) | join }}",
    "{{ ('x' * 60000).join(['y' * 60000] * 1000) }}",
    "{{ ('x\n' * 30000) | indent(1000) }}",
    "{{ ('a' * 34 ~ '!') is match('(a+)+$') }}",
    "{{ ('a' * 34 ~ '!') | regex_search('(a|a)+$') }}",
    "{{ ('a' * 34 ~ '!') | regex_replace(find='(a*)*$', replace='') }}",
    "{% set p = '(a+)+$' %}{{ ('a' * 34 ~ '!') | regex_match(p) }}",
    "{{ 'aa' | regex_match('(a)\\\\1') }}",
]


def hostile_templates():
    """A Templates over a home-less engine stub, as the reviewer's probes build it."""
    import types

    from vesper.lab.ha_template import Templates

    class Clock:
        def now(self):
            return 100.0
    engine = types.SimpleNamespace(_state={"light.a": "on"}, _attrs={"light.a": {}}, _changed={}, site=None,
                                   session=types.SimpleNamespace(clock=Clock()), _midnight=0.0)
    return Templates(engine)


@pytest.mark.parametrize("source", HOSTILE)
def test_a_hostile_template_is_refused_or_fails_within_its_budget(source):
    import time

    from vesper.lab.ha_template import TemplateFailed, TemplateRefused
    templates = hostile_templates()
    t0 = time.monotonic()
    try:
        compiled = templates.compile(source, set(), {"light.a"}, "t")
        with pytest.raises(TemplateFailed):
            templates.render(compiled, {})
    except TemplateRefused:
        pass
    assert time.monotonic() - t0 < 2.0


def test_ordinary_templates_still_render_within_the_bounds():
    templates = hostile_templates()
    for source, out in (("{{ 'ab' * 3 }}", "ababab"), ("{{ 2 ** 10 }}", "1024"), ("{{ '%5.1f' % 3.14159 }}", "3.1"),
                        ("{{ '{:.2f}'.format(2.5) }}", "2.50"), ("{{ range(5) | join(',') }}", "0,1,2,3,4"),
                        ("{% for i in range(3) %}{{ loop.index }}{% endfor %}", "123"),
                        ("{{ 'on' | regex_match('^(on|off)$') }}", "True"), ("{{ [1, 2] | sum }}", "3")):
        assert templates.render(templates.compile(source, set(), set(), "t"), {})[0] == out


def test_an_old_jinja2_refuses_every_template(monkeypatch):
    import jinja2

    from vesper.lab.ha_template import TemplateRefused
    monkeypatch.setattr(jinja2, "__version__", "3.1.5")
    templates = hostile_templates()
    with pytest.raises(TemplateRefused, match="jinja2 3.1.5 is older than 3.1.6"):
        templates.compile("{{ 1 }}", set(), set(), "t")


@pytest.mark.parametrize("source", [
    "{% set p = '(a+)+$' %}{{ ('a' * 34 ~ '!') | regex_match(p) }}",
    "{% for i in range(100000) %}{% for j in range(100000) %}{% endfor %}{% endfor %}",
    "{% set l = [1] * 60000 %}{% for i in l %}{% for j in l %}{% endfor %}{% endfor %}",
])
def test_a_hostile_template_ends_within_its_budget_in_a_worker_thread_too(source):
    """The Lab runs engines in threads, where no alarm can interrupt a render: the deadline is cooperative there,
    and a catastrophic pattern is refused before it runs."""
    import threading
    import time

    from vesper.lab.ha_template import TemplateFailed, TemplateRefused
    templates, outcome = hostile_templates(), []

    def render():
        try:
            templates.render(templates.compile(source, set(), set(), "t"), {})
            outcome.append("rendered")
        except (TemplateRefused, TemplateFailed) as e:
            outcome.append(type(e).__name__)
    t0 = time.monotonic()
    worker = threading.Thread(target=render, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive() and time.monotonic() - t0 < 2.0
    assert outcome and outcome[0] in ("TemplateRefused", "TemplateFailed")


def test_a_safe_pattern_still_matches():
    templates = hostile_templates()
    for source, out in (("{{ '12.5' | regex_match('^\\\\d+(\\\\.\\\\d+)?$') }}", "True"),
                        ("{{ 'a-b' | regex_replace('[-_]', ' ') }}", "a b"),
                        ("{{ 'on' is match('on|off') }}", "True")):
        assert templates.render(templates.compile(source, set(), set(), "t"), {})[0] == out


def test_the_budget_restores_an_outer_alarm():
    import signal
    import time

    from vesper.lab.ha_template import budget
    fired = []
    old = signal.signal(signal.SIGALRM, lambda *a: fired.append(time.monotonic()))
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.3)
        with budget(0.1, "x"):
            pass
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0.1  # the outer alarm still pending
        time.sleep(0.4)
        assert fired
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# ---- review fixes: Home Assistant's armed numeric_state, wait variables, trigger.now, rate limits, optimistic climate

def test_a_numeric_state_trigger_is_armed_per_entity_so_a_moved_threshold_entity_counts(tmp_path):
    """Home Assistant arms (trigger, entity) at setup when out of range and fires when the value is in range while
    armed. The reviewer's probe: limit 15, temperature 20 (out of range: armed); the limit moves to 25; 20 -> 21
    fires, though 20 was already below the new limit."""
    def autos(ids):
        temp, battery = ids["kitchen_climate"]["temperature"], ids["kitchen_climate"]["battery"]
        return [auto([], [{"trigger": "numeric_state", "entity_id": temp, "below": battery}])]
    ids, s, e, _ = setup(tmp_path, autos)
    temp, battery = ids["kitchen_climate"]["temperature"], ids["kitchen_climate"]["battery"]
    (a,) = e.automations
    t, owner = a.triggers[0], id(a)
    e._armed.clear()
    e._state.update({temp: "20", battery: "15"})
    e._arm(owner, a.triggers)
    e._state[battery] = "25"
    assert e._matches(t, (temp, "20", "21", {}, {}), owner) is True
    assert e._matches(t, (temp, "21", "22", {}, {}), owner) is False  # fired: disarmed until out of range again
    e._state[battery] = "15"
    assert e._matches(t, (temp, "22", "23", {}, {}), owner) is False  # out of range: armed again
    e._state[battery] = "25"
    assert e._matches(t, (temp, "23", "24", {}, {}), owner) is True


def test_an_entity_in_range_at_setup_is_not_armed(tmp_path):
    def autos(ids):
        return [auto([], [{"trigger": "numeric_state", "entity_id": ids["kitchen_climate"]["temperature"],
                           "above": 10}])]
    ids, s, e, _ = setup(tmp_path, autos)
    temp = ids["kitchen_climate"]["temperature"]
    (a,) = e.automations
    t, owner = a.triggers[0], id(a)
    e._armed.clear()
    e._state[temp] = "20"
    e._arm(owner, a.triggers)
    assert e._matches(t, (temp, "20", "21", {}, {}), owner) is False  # Home Assistant: not armed at setup
    assert e._matches(t, (temp, "21", "5", {}, {}), owner) is False
    assert e._matches(t, (temp, "5", "12", {}, {}), owner) is True


def test_a_wait_for_a_template_trigger_renders_with_the_runs_variables(tmp_path):
    def autos(ids):
        lamp, motion = lamp_motion(ids)
        return [auto([{"variables": {"who": motion}},
                      {"wait_for_trigger": [{"trigger": "template", "value_template": "{{ is_state(who, 'on') }}"}],
                       "timeout": 600, "continue_on_timeout": False},
                      {"action": "light.turn_on", "target": {"entity_id": lamp}}])]
    ids, s, e, calls = setup(tmp_path, autos)
    assert e.refused == []
    s.clock.advance(H + 60)  # 08:01: waiting
    e._apply([e._set(lamp_motion(ids)[1], "on")])
    s.clock.advance(1)
    assert services(calls) == ["light.turn_on"]


def test_time_triggers_carry_trigger_now_and_a_description(tmp_path):
    def autos(ids):
        return [auto([{"condition": "template", "value_template":
                       "{{ trigger.now.hour == 8 and trigger.now.minute == 0 and trigger.description == 'time' }}"},
                      {"action": "light.turn_on", "target": {"entity_id": lamp_motion(ids)[0]}}], at("08:00")),
                auto([{"condition": "template", "value_template":
                       "{{ trigger.now.minute == 45 and trigger.platform == 'time_pattern' }}"},
                      {"action": "light.turn_off", "target": {"entity_id": lamp_motion(ids)[0]}}],
                     [{"trigger": "time_pattern", "minutes": 45}])]
    _, s, e, calls = setup(tmp_path, autos, text_home=sited_home)
    s.clock.advance(1.5 * H)  # 07:30 -> 09:00
    assert services(calls) == ["light.turn_off", "light.turn_on", "light.turn_off"]  # 07:45, 08:00, 08:45


def test_a_template_reading_all_states_renders_at_most_once_a_minute(tmp_path):
    def autos(ids):
        return [auto([{"action": "light.turn_on", "target": {"entity_id": lamp_motion(ids)[0]}}],
                     [{"trigger": "template", "value_template":
                       "{{ states | selectattr('state', 'eq', 'on') | list | count > 0 }}"}])]
    ids, s, e, calls = setup(tmp_path, autos)
    (w,) = e._watches
    lamp, motion = lamp_motion(ids)
    e._apply([e._set(motion, "off")])
    rendered = w.rendered_at
    e._apply([e._set(motion, "on")])  # within the minute: not rendered now, but scheduled
    assert w.rendered_at == rendered and w.pending and calls == []
    s.clock.advance(61)
    assert w.rendered_at == rendered + 60 and len(calls) == 1


def test_a_template_reading_a_domain_renders_at_most_once_a_second(tmp_path):
    def autos(ids):
        return [auto([], [{"trigger": "template",
                           "value_template": "{{ states.binary_sensor | selectattr('state', 'eq', 'on') | list }}"}])]
    ids, s, e, _ = setup(tmp_path, autos)
    (w,) = e._watches
    _, motion = lamp_motion(ids)
    e._apply([e._set(motion, "on")])
    first = w.rendered_at
    e._apply([e._set(motion, "off")])
    assert w.rendered_at == first and w.pending
    s.clock.advance(1.5)
    assert w.rendered_at == first + 1


def test_a_climate_whose_mode_nothing_reports_is_optimistic_as_home_assistants_mqtt_climate(tmp_path):
    ids, s, e, calls = setup(tmp_path, [], text_home=sited_home)
    climate = next(x for x in ids["bedroom_thermostat"].values() if x.startswith("climate."))
    entity = e.entities[climate]
    entity.state_feature, entity.optimistic = "", True  # as a discovery with no mode_state_template
    assert e._entity_state(entity) == "off"  # not its setpoint
    e2 = Engine(s, automations=[auto([{"action": "climate.set_hvac_mode", "target": {"entity_id": climate},
                                       "data": {"hvac_mode": "heat"}}])])
    e2.entities[climate].state_feature, e2.entities[climate].optimistic = "", True
    e2.start()
    assert e2._state[climate] == "off"
    s.clock.advance(1 * H)  # 08:00
    assert e2._state[climate] == "heat"


def test_a_template_budget_is_cpu_time_so_a_loaded_machine_does_not_fail_a_render(monkeypatch):
    """Protocol A16.3: a render on a loaded machine (wall-clock time racing ahead while the worker waits for a core)
    must not fail. The template budget counts the thread's CPU time, not wall-clock time; a sweep's results do not
    depend on the load it ran under."""
    import itertools

    from vesper.lab import ha_template
    templates = hostile_templates()
    compiled = templates.compile("{% set x = 1 %}{{ states('light.a') == 'on' and x == 1 }}", set(), {"light.a"}, "t")
    wall = itertools.count(0.0, 10.0)  # every reading of the wall clock is 10 s after the last
    monkeypatch.setattr(ha_template.time, "monotonic", lambda: next(wall))
    assert templates.render(compiled, {})[0] == "True"
