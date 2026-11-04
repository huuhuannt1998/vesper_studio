"""The engine's coverage of Home Assistant's common automation features (EDT task 12): each one with Home Assistant's
documented semantics; strict mode refuses what it cannot do faithfully, permissive mode records it."""

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
