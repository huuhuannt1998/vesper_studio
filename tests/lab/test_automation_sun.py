"""VESPER's automation engine and the sun (environment design 2026-11-03, goal 6): for a home with a site, `sun`
triggers and conditions and the entity `sun.sun`, from the same astral sun as Home Assistant's; a home without a
site refuses them by name."""
import textwrap

from tests.lab.homes import catalog, write_home
from tests.lab.test_environment_session import SITED
from vesper.env.site import parse_site
from vesper.lab.automations import entity_ids
from vesper.env.sun import sun_events
from vesper.lab.home import load_lab_home
from vesper.lab.session import Session
from vesper.twins.clock import LogicalClock

SITE = parse_site({"lat": 35.2271, "lon": -80.8431, "elevation_m": 229, "tz": "America/New_York",
                   "date": "2026-06-21"})
RULES = """\
- alias: lamp at sunset
  triggers: [{trigger: sun, event: sunset, offset: "-00:30:00"}]
  actions: [{action: light.turn_on, target: {entity_id: {lamp}}}]
- alias: lamp off at night when the sun is down
  triggers: [{trigger: time, at: "23:30"}]
  conditions: [{condition: sun, after: sunset, before: sunrise}]
  actions: [{action: light.turn_off, target: {entity_id: {lamp}}}]
- alias: sun state
  triggers: [{trigger: state, entity_id: sun.sun, to: below_horizon}]
  actions: [{action: light.turn_on, target: {entity_id: {lamp}}}]
"""


def session(tmp_path, home_text=SITED, rules=RULES):
    text = home_text.replace("    devices:\n", "    automations: automations.yaml\n    devices:\n", 1)
    (tmp_path / "automations.yaml").write_text("[]\n")
    home = load_lab_home(write_home(tmp_path, text), catalog())
    lamp = entity_ids(home)["bedroom_lamp"]["state"]
    (tmp_path / "automations.yaml").write_text(textwrap.dedent(rules).replace("{lamp}", lamp))
    s = Session(home, clock=LogicalClock())
    s.start(residents=False, automations=True)
    return s


def test_sunrise_and_sunset_on_the_solstice_at_charlotte():
    ev = sun_events(SITE, 0)
    assert 6 * 3600 < ev["sunrise"] < 6.5 * 3600  # 06:12 EDT
    assert 20.5 * 3600 < ev["sunset"] < 20.9 * 3600  # 20:37 EDT
    assert sun_events(SITE, 1)["sunrise"] > 86400


def test_every_sun_automation_loads_for_a_home_with_a_site(tmp_path):
    s = session(tmp_path)
    assert [a.name for a in s.automations.automations] == ["lamp at sunset", "lamp off at night when the sun is down",
                                                           "sun state"]
    assert s.automations.refused == [] and s.automations._state["sun.sun"] == "above_horizon"  # 07:30


def test_the_sunset_trigger_fires_half_an_hour_before_sunset_and_sun_sun_turns_at_it(tmp_path):
    s = session(tmp_path)
    sunset = sun_events(SITE, 0)["sunset"]
    lamp = lambda: s.runtime.state(s.twin_ids["bedroom_lamp"])["state"]  # noqa: E731
    s.clock.advance(sunset - 1800 - 7.5 * 3600 - 5)
    assert lamp() == "OFF"
    s.clock.advance(10)
    assert lamp() == "ON"
    s.clock.advance(1800)
    assert s.automations._state["sun.sun"] == "below_horizon"


def test_after_sunset_and_before_sunrise_holds_across_midnight(tmp_path):
    s = session(tmp_path)
    lamp = lambda: s.runtime.state(s.twin_ids["bedroom_lamp"])["state"]  # noqa: E731
    s.clock.advance(23.5 * 3600 - 7.5 * 3600 - 60)  # 23:29: the sunset automations have switched it on
    assert lamp() == "ON"
    s.clock.advance(120)  # 23:31: "the sun is down" holds, so the 23:30 automation switched it off
    assert lamp() == "OFF"


def test_a_home_without_a_site_refuses_sun_automations_by_name(tmp_path):
    from tests.lab.homes import HOME
    s = session(tmp_path, HOME)
    reasons = {r["automation"]: r["reason"] for r in s.automations.refused}
    assert "no site" in reasons["lamp at sunset"] and "no site" in reasons["lamp off at night when the sun is down"]
    assert "sun.sun" in reasons["sun state"]
