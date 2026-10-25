"""A resident's profile: habits, comfort, and the devices an activity uses."""
import pytest

from vesper.lab.profile import ProfileError, load_profile

H = 3600


@pytest.mark.parametrize("bad, message", [({"forget_light": 1.5}, "forget_light: 1.5 is not a probability"),
                                          ({"dark_lux": -1}, "dark_lux: -1 is not"),
                                          ({"bedtime": "26:00"}, "bedtime: '26:00' is not a time of day"),
                                          ({"uses": {"COOKING": ["plug"]}}, "uses: 'COOKING' is not one of"),
                                          ({"snore": True}, "unknown setting 'snore'")])
def test_a_profile_names_its_mistakes(bad, message):
    with pytest.raises(ProfileError, match=message):
        load_profile(bad, "residents[0].profile")


def test_a_profile_has_sensible_defaults():
    p = load_profile(None, "residents[0].profile")
    assert (p.forget_light, p.forget_lock, p.dark_lux, p.bedtime) == (0.2, 0.1, 50.0, 20 * H)
    assert p.uses["EATING"] == ("plug",) and load_profile({"forget_light": 0}, "x").forget_light == 0
