"""vesper.edt.setback: the setpoints an automation commands, and how long a run's commanded setpoint stays low."""
from vesper.edt.setback import Setpoint, low_setpoints, setback_intervals, setpoints

DAY = 86400.0


def auto(*steps, key="action"):
    return {"trigger": [{"platform": "time", "at": "22:00"}], key: list(steps)}


def test_a_literal_set_temperature_below_10_is_found():
    a = auto({"service": "climate.set_temperature", "target": {"entity_id": "climate.x"}, "data": {"temperature": 7}})
    assert low_setpoints(a) == [Setpoint("climate.set_temperature", "temperature", 7.0, False)]


def test_new_syntax_actions_key_and_action_service_and_numeric_strings():
    a = auto({"action": "climate.set_temperature", "data": {"temperature": "5.5"}}, key="actions")
    assert [s.value for s in low_setpoints(a)] == [5.5]


def test_a_setpoint_at_or_above_10_is_not_low():
    a = auto({"service": "climate.set_temperature", "data": {"temperature": 10}},
             {"service": "climate.set_temperature", "data": {"temperature": 18.5}})
    assert low_setpoints(a) == []
    assert [s.value for s in setpoints(a)] == [10.0, 18.5]


def test_nested_choose_and_target_temp_low_and_data_template():
    a = auto({"choose": [{"conditions": [], "sequence": [
        {"service": "climate.set_temperature", "data_template": {"target_temp_low": 6, "target_temp_high": 24}}]}],
        "default": [{"service": "CLIMATE.set_temperature", "data": {"temperature": 8}}]})
    assert sorted((s.field, s.value) for s in low_setpoints(a)) == [("target_temp_low", 6.0), ("temperature", 8.0)]


def test_a_mode_change_sets_no_value_unless_it_carries_a_temperature():
    a = auto({"service": "climate.set_hvac_mode", "data": {"hvac_mode": "off"}},
             {"service": "climate.turn_off", "target": {"entity_id": "climate.x"}})
    assert setpoints(a) == []
    b = auto({"service": "climate.set_hvac_mode", "data": {"hvac_mode": "heat", "temperature": 4}})
    assert [s.value for s in low_setpoints(b)] == [4.0]


def test_a_templated_setpoint_has_no_value_and_is_never_low():
    a = auto({"service": "climate.set_temperature", "data": {"temperature": "{{ states('input_number.t') }}"}})
    assert setpoints(a) == [Setpoint("climate.set_temperature", "temperature", None, True)]
    assert low_setpoints(a) == []


def test_triggers_and_conditions_are_not_actions_and_other_domains_are_ignored():
    a = {"trigger": [{"platform": "numeric_state", "entity_id": "sensor.t", "below": 5}],
         "condition": [{"condition": "template", "value_template": "{{ 1 }}"}],
         "action": [{"service": "light.turn_on", "data": {"temperature": 3}}]}
    assert setpoints(a) == []
    assert setpoints(None) == [] and setpoints({"action": None}) == []


def row(t, value, feature="current_heating_setpoint", accepted=True):
    return {"kind": "automation", "t": t, "service": "climate.set_temperature", "feature": feature, "value": value,
            "accepted": accepted}


def test_setback_lasts_from_a_low_command_to_the_next_normal_one_clipped_to_the_window():
    rows = [row(0.8 * DAY, 18), row(0.9 * DAY, 7), row(1.9 * DAY, 18), row(1.95 * DAY, 7)]
    assert setback_intervals(rows, DAY, 3 * DAY) == [(DAY, 1.9 * DAY), (1.95 * DAY, 3 * DAY)]


def test_no_command_means_no_setback_and_refused_or_mode_commands_do_not_count():
    assert setback_intervals([], DAY, 3 * DAY) == []
    rows = [row(1.2 * DAY, 7, accepted=False), row(1.3 * DAY, "off", feature="system_mode")]
    assert setback_intervals(rows, DAY, 3 * DAY) == []


def test_consecutive_low_commands_merge():
    rows = [row(1.1 * DAY, 7), row(1.2 * DAY, 5), row(1.5 * DAY, 20)]
    assert setback_intervals(rows, DAY, 3 * DAY) == [(1.1 * DAY, 1.5 * DAY)]
