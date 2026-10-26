"""Scripted residents (spec §5a), on the logical clock in the test house:
hall [0.1, 2.9] x [0, 6], kitchen [3.1, 7] x [0, 2.9], bedroom [3.1, 7] x [3.1, 6], porch outside the front door."""
import copy
import dataclasses
from types import SimpleNamespace

from tests.lab.homes import HOME, catalog, write_home
from vesper.lab.home import load_lab_home
from vesper.lab.profile import Profile
from vesper.lab.resident import Resident
from vesper.lab.routine import Routine, Task
from vesper.lab.rules import is_locked, is_on
from vesper.lab.session import Session, compare_reports, replay
from vesper.twins.clock import LogicalClock

PLUG = ('      - {product: "zigbee:acme:plug-1", name: kitchen_plug, at: {room: kitchen, xy: [4.0, 0.5], '
        'mount: floor}}\n')
H = 3600


def task(at, minutes, name, category, room=None):
    h, m = (int(x) for x in at.split(":"))
    return Task(start=h * H + m * 60, duration=minutes * 60, name=name, category=category, room=room)


def lab(tmp_path, tasks, start="07:00", profile=None, extra="", residents=1, cat=None):
    text = HOME.replace('start_time: "07:30"', f'start_time: "{start}"') + extra
    home = load_lab_home(write_home(tmp_path, text), cat or catalog())
    s = Session(home, clock=LogicalClock())
    s.start(spawn_player=False)
    people = [Resident(s, f"r{k}", "male_0", Routine(f"r{k}", tuple(tasks[k] if residents > 1 else tasks)),
                       profile or Profile()) for k in range(residents)]
    for p in people:
        p.start()
    h, m = (int(x) for x in start.split(":"))
    return SimpleNamespace(s=s, people=people, home=home, room=lambda name="r0": s.world.pose(name).room,
                           at=lambda hh_mm: s.clock.advance_to(
                               int(hh_mm[:2]) * H + int(hh_mm[3:]) * 60 - (h * H + m * 60)),
                           on=lambda d: is_on(s.rules.twin(d)), locked=lambda d: is_locked(s.rules.twin(d)))


def inputs(s, actor="r0"):
    return [r for r in s.records if r["kind"] == "input" and r["actor"] == actor]


def test_a_resident_follows_the_routine_room_by_room(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"),
                       task("07:30", 20, "breakfast", "EATING", "kitchen"),
                       task("08:00", 30, "read the paper", "LEISURE", "hall")])
    assert t.room() == "bedroom"  # where the day's task in progress is
    t.at("07:32")
    assert t.room() == "kitchen"
    t.at("08:02")
    assert t.room() == "hall"
    walks = [r for r in inputs(t.s) if r["op"] == "walk_to"]
    assert [r["why"]["task"] for r in walks] == ["breakfast", "read the paper"]


def test_cooking_uses_the_kitchen_plug_and_turns_it_off_after(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"),
                       task("07:30", 20, "breakfast", "EATING", "kitchen")], extra=PLUG)
    t.at("07:35")
    assert t.on("kitchen_plug") is True
    t.at("07:55")
    assert t.on("kitchen_plug") is False
    uses = [r for r in inputs(t.s) if r["op"] == "interact"]
    assert [(r["args"]["target"], r["why"]["reason"]) for r in uses] == [("kitchen_plug", "breakfast"),
                                                                         ("kitchen_plug", "breakfast is over")]


def test_a_dark_room_gets_its_light_and_leaving_turns_it_off(tmp_path):
    t = lab(tmp_path, [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING",
                                                                              "kitchen")], start="20:59")
    t.at("21:02")
    assert t.on("bedroom_lamp") is True
    t.at("21:33")
    assert t.on("bedroom_lamp") is False and t.room() == "kitchen"
    reasons = [r["why"]["reason"] for r in inputs(t.s) if r["op"] == "interact"]
    assert reasons == ["a dark room", "leaving the room"]


def test_a_forgetful_resident_leaves_the_light_on_and_the_trace_says_so(tmp_path):
    t = lab(tmp_path, [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING",
                                                                              "kitchen")],
            start="20:59", profile=Profile(forget_light=1.0))
    t.at("21:33")
    assert t.on("bedroom_lamp") is True
    assert any(r["kind"] == "resident" and r["decision"] == "forgot the light" and r["device"] == "bedroom_lamp"
               for r in t.s.records)


def test_going_to_bed_locks_the_front_door_unless_forgotten(tmp_path):
    day = [task("22:00", 30, "tv", "LEISURE", "hall"), task("22:30", 480, "sleep", "SLEEP", "bedroom")]
    t = lab(tmp_path, day, start="21:59")
    t.at("22:40")
    assert t.locked("front_lock") is True and t.room() == "bedroom"
    (tmp_path / "f").mkdir()
    forgetful = lab(tmp_path / "f", day, start="21:59", profile=Profile(forget_lock=1.0))
    forgetful.at("22:40")
    assert forgetful.locked("front_lock") is False


def test_leaving_home_and_coming_back_go_through_the_front_door(tmp_path):
    t = lab(tmp_path, [task("08:00", 10, "coffee", "EATING", "kitchen"), task("08:10", 60, "work", "AWAY"),
                       task("09:10", 20, "lunch prep", "HOUSEHOLD", "kitchen")], start="07:59")
    t.at("08:20")
    assert t.room() == "porch" and t.s.world.doors["hall-porch"] is False and t.locked("front_lock") is True
    t.at("09:15")
    assert t.room() == "kitchen" and t.s.world.doors["hall-porch"] is False
    reasons = [r["why"]["reason"] for r in inputs(t.s) if r["op"] == "interact"]
    assert reasons[:3] == ["leaving home", "leaving home", "leaving home"]  # open, close, lock behind
    assert "coming home" in reasons


def test_two_residents_live_side_by_side_and_the_sensors_see_both(tmp_path):
    a = [task("07:00", 60, "breakfast", "EATING", "kitchen")]
    b = [task("07:00", 60, "tidy up", "HOUSEHOLD", "hall")]
    t = lab(tmp_path, [a, b], residents=2)
    t.at("07:10")
    assert (t.room("r0"), t.room("r1")) == ("kitchen", "hall")
    seen = {r["actor"] for r in t.s.records if r["kind"] == "rule" and r["rule"] == "presence"}
    assert "r1" in seen


def test_leaving_a_room_someone_else_is_in_leaves_its_light_on(tmp_path):
    a = [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING", "kitchen")]
    b = [task("21:00", 60, "read", "LEISURE", "bedroom"), task("22:00", 10, "tea", "EATING", "kitchen")]
    t = lab(tmp_path, [a, b], start="20:59", residents=2, profile=Profile(forget_light=0.0))
    t.at("21:33")
    assert t.room("r0") == "kitchen" and t.on("bedroom_lamp") is True  # r1 is still reading
    t.at("22:03")
    assert t.room("r1") == "kitchen" and t.on("bedroom_lamp") is False  # the last one out switches it off


def test_a_day_that_starts_at_night_finds_the_resident_in_bed_from_the_night_before(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "breakfast", "EATING", "kitchen"),
                       task("22:30", 480, "sleep", "SLEEP", "bedroom")], start="03:00")
    assert t.room() == "bedroom" and t.on("bedroom_lamp") is False
    t.at("07:02")
    assert t.room() == "kitchen"
    assert not [r for r in t.s.records if r["kind"] == "input" and r["op"] == "interact"]  # no light at 03:00


def test_every_resident_action_says_who_and_why(tmp_path):
    t = lab(tmp_path, [task("08:00", 10, "coffee", "EATING", "kitchen"), task("08:10", 60, "work", "AWAY"),
                       task("21:00", 30, "read", "LEISURE", "bedroom"), task("22:30", 480, "sleep", "SLEEP",
                                                                              "bedroom")],
            start="07:59", extra=PLUG)
    t.at("23:00")
    acts = [r for r in t.s.records if r["kind"] == "input" and r["op"] in ("walk_to", "interact")]
    assert acts and all(r["why"]["resident"] == r["actor"] == "r0" and r["why"]["reason"] for r in acts)


def test_a_resident_s_day_replays_exactly_without_the_resident(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"), task("07:30", 20, "breakfast", "EATING",
                                                                              "kitchen"),
                       task("08:00", 60, "errand", "AWAY"), task("21:00", 30, "read", "LEISURE", "bedroom"),
                       task("22:30", 480, "sleep", "SLEEP", "bedroom")], start="06:59", extra=PLUG,
            profile=Profile(forget_light=0.5, forget_lock=0.5))
    t.at("23:30")
    t.s.stop()
    again = replay(t.s.records, t.home)
    assert compare_reports(t.s.records, again) is None
    assert len([r for r in again if r["kind"] == "input"]) == len([r for r in t.s.records if r["kind"] == "input"])


def test_a_smoke_alarm_sends_the_residents_out(tmp_path):
    cat = catalog()
    smoke = copy.deepcopy(cat["zigbee:acme:pir-1"])
    smoke["id"], smoke["identity"]["category"] = "zigbee:acme:smoke-1", "smoke_sensor"
    smoke["features"][0].update(name="smoke", property="smoke", ha="binary_sensor/smoke")
    cat[smoke["id"]] = smoke
    extra = '      - {product: "zigbee:acme:smoke-1", name: kitchen_smoke, at: {room: kitchen, mount: ceiling}}\n'
    t = lab(tmp_path, [task("07:00", 120, "breakfast", "EATING", "kitchen")], extra=extra, cat=cat)
    t.at("07:05")
    t.s.runtime.stimulate(t.s.twin_ids["kitchen_smoke"], {"type": "measured", "feature": "smoke", "value": True})
    t.at("07:08")
    assert t.room() == "porch"
    assert any(r["kind"] == "input" and r.get("why", {}).get("reason") == "an alarm" for r in t.s.records)


def test_going_to_bed_switches_the_bedroom_light_off_and_sleep_never_switches_it_on(tmp_path):
    t = lab(tmp_path, [task("21:00", 60, "read", "LEISURE", "bedroom"), task("22:30", 480, "sleep", "SLEEP",
                                                                               "bedroom")], start="20:59")
    t.at("21:10")
    assert t.on("bedroom_lamp") is True  # reading in the dark
    t.at("22:35")
    assert t.on("bedroom_lamp") is False
    (tmp_path / "b").mkdir()
    late = lab(tmp_path / "b", [task("23:00", 30, "tv", "LEISURE", "hall"), task("23:30", 480, "sleep", "SLEEP",
                                                                                  "bedroom")], start="22:59")
    late.at("23:40")
    assert late.room() == "bedroom" and late.on("bedroom_lamp") is False


MORNING = """\
version: 1
tasks:
  - {at: "07:00", minutes: 30, name: breakfast, category: EATING, room: kitchen}
  - {at: "07:30", minutes: 30, name: read, category: LEISURE, room: bedroom}
"""


def test_the_home_s_residents_enter_with_the_session_and_a_replay_lives_their_day_again(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(MORNING)
    home = load_lab_home(write_home(tmp_path, HOME + "    residents:\n      - {name: alex, routine: "
                                                      "routines/alex.yaml}\n"), catalog())
    s = Session(dataclasses.replace(home, start_time=6 * H + 59 * 60), clock=LogicalClock())  # the file says 07:30
    s.start(spawn_player=False)
    s.clock.advance(H)
    s.stop()
    assert s.world.pose("alex").room == "bedroom"
    again = replay(s.records, home)
    lived = [r for r in again if r["kind"] == "input" and r["actor"] == "alex"]
    assert [r["op"] for r in lived].count("spawn") == 1  # lived again, not lived and re-applied
    assert lived == [r for r in s.records if r["kind"] == "input" and r["actor"] == "alex"]
    assert compare_reports(s.records, again) is None


# -- the final review's findings --------------------------------------------------------------------------------

RESIDENT = "    residents:\n      - {name: alex, routine: routines/alex.yaml}\n"
KITCHEN_LAMP = ('      - {product: "zigbee:acme:dim-1", name: kitchen_lamp, at: {room: kitchen, xy: [5.0, 1.0], '
                'mount: table}}\n')
SMOKE = '      - {product: "zigbee:acme:smoke-1", name: kitchen_smoke, at: {room: kitchen, mount: ceiling}}\n'


def smoke_catalog():
    cat = catalog()
    smoke = copy.deepcopy(cat["zigbee:acme:pir-1"])
    smoke["id"], smoke["identity"]["category"] = "zigbee:acme:smoke-1", "smoke_sensor"
    smoke["features"][0].update(name="smoke", property="smoke", ha="binary_sensor/smoke")
    cat[smoke["id"]] = smoke
    return cat


def smoke(t, value):
    t.s.runtime.stimulate(t.s.twin_ids["kitchen_smoke"], {"type": "measured", "feature": "smoke", "value": value})


def notes(t, decision):
    return [r for r in t.s.records if r["kind"] == "resident" and r["decision"] == decision]


def test_a_session_with_the_player_and_the_residents_replays_exactly(tmp_path):
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "alex.yaml").write_text(MORNING)
    motion = '      - {product: "zigbee:acme:pir-1", name: bedroom_motion, at: {room: bedroom, mount: ceiling}}\n'
    text = HOME.replace('start_time: "07:30"', 'start_time: "06:59"') + motion + RESIDENT  # alex is in bed
    home = load_lab_home(write_home(tmp_path, text), catalog())
    s = Session(home, clock=LogicalClock())
    s.start()  # the player and alex, as `vesper lab run` and the Python API do
    s.move(0.3, 0.0)  # a script's first input, before the clock moves
    s.clock.advance(600)
    s.stop()
    assert compare_reports(s.records, replay(s.records, home)) is None


def test_two_residents_leaving_a_lit_room_together_switch_its_light_off(tmp_path):
    day = [task("21:00", 30, "read", "LEISURE", "bedroom"), task("21:30", 10, "tea", "EATING", "kitchen")]
    t = lab(tmp_path, [day, list(day)], start="20:59", residents=2, profile=Profile(forget_light=0.0))
    t.at("21:33")
    assert (t.room("r0"), t.room("r1")) == ("kitchen", "kitchen") and t.on("bedroom_lamp") is False


def test_going_to_bed_from_a_lit_room_switches_that_room_s_light_off(tmp_path):
    t = lab(tmp_path, [task("21:00", 60, "tea", "EATING", "kitchen"), task("22:30", 480, "sleep", "SLEEP",
                                                                           "bedroom")],
            start="20:59", extra=KITCHEN_LAMP, profile=Profile(forget_light=0.0, forget_lock=0.0))
    t.at("21:05")
    assert t.on("kitchen_lamp") is True
    t.at("22:40")
    assert t.room() == "bedroom" and t.on("kitchen_lamp") is False and t.locked("front_lock") is True


def test_the_routine_is_lived_every_day_and_across_midnight(tmp_path):
    day = [task("07:00", 30, "breakfast", "EATING", "kitchen"), task("23:30", 450, "sleep", "SLEEP", "bedroom")]
    t = lab(tmp_path, day, start="23:00")
    t.s.clock.advance_to(8 * H + 2 * 60)  # 07:02 the next morning
    assert t.room() == "kitchen"
    t.s.clock.advance_to(32 * H + 2 * 60)  # 07:02 the morning after
    to_task = [r["why"]["task"] for r in inputs(t.s) if r["op"] == "walk_to" and r["why"]["reason"] == r["why"]["task"]]
    assert t.room() == "kitchen" and to_task == ["sleep", "breakfast", "sleep", "breakfast"]


def test_going_to_bed_after_midnight_is_going_to_bed(tmp_path):
    t = lab(tmp_path, [task("23:30", 60, "tv", "LEISURE", "hall"), task("00:30", 420, "sleep", "SLEEP",
                                                                        "bedroom")],
            start="23:29", profile=Profile(forget_lock=0.0))
    t.s.clock.advance_to(H + 11 * 60)  # 00:40
    assert t.room() == "bedroom" and t.locked("front_lock") is True


def test_an_alarm_keeps_the_residents_out_until_it_clears(tmp_path):
    t = lab(tmp_path, [task("07:00", 120, "breakfast", "EATING", "kitchen"), task("09:30", 60, "read", "LEISURE",
                                                                                   "bedroom")],
            extra=PLUG + SMOKE, cat=smoke_catalog())
    t.at("07:05")
    assert t.on("kitchen_plug") is True
    smoke(t, True)
    t.at("09:35")  # breakfast ended at 09:00 and reading began at 09:30, with the smoke still reported
    assert t.room() == "porch" and t.s.world.doors["hall-porch"] is False
    assert [r["task"] for r in notes(t, "stayed out: an alarm")] == ["read"]
    assert not [r for r in inputs(t.s) if r["t"] > 6 * 60 and r["op"] == "interact"
                and r["args"]["target"] == "kitchen_plug"]  # nobody went back in to switch the plug off
    smoke(t, False)
    t.at("09:40")
    assert t.room() == "bedroom" and t.s.world.doors["hall-porch"] is False and t.on("kitchen_plug") is False


def test_a_closed_door_on_the_way_is_opened_and_closed_behind(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "breakfast", "EATING", "kitchen"), task("07:30", 30, "dress", "HYGIENE",
                                                                              "bedroom")])
    t.at("07:05")
    t.s.world.set_door("bedroom-hall", False)  # someone closed it while alex was in the kitchen
    t.at("07:31")
    assert t.room() == "bedroom" and t.s.world.doors["bedroom-hall"] is False
    assert [r["args"]["target"] for r in inputs(t.s) if r["op"] == "interact"] == ["bedroom-hall", "bedroom-hall"]


def test_a_door_closed_in_front_of_a_walking_resident_is_opened_again(tmp_path):
    t = lab(tmp_path, [task("07:00", 30, "breakfast", "EATING", "kitchen"), task("07:30", 30, "dress", "HYGIENE",
                                                                              "bedroom")])
    t.s.clock.advance_to(30 * 60 + 2)  # 07:30:02: on the way from the kitchen to the bedroom
    assert t.room() != "bedroom"
    t.s.world.set_door("bedroom-hall", False)
    t.s.clock.advance_to(31 * 60 + 30)
    assert t.room() == "bedroom" and not notes(t, "could not get there")


def test_a_task_in_progress_when_the_day_starts_gets_its_light_and_its_devices(tmp_path):
    t = lab(tmp_path, [task("21:00", 60, "read", "LEISURE", "bedroom")], start="21:10")
    t.s.clock.advance_to(30)
    assert t.room() == "bedroom" and t.on("bedroom_lamp") is True
    (tmp_path / "u").mkdir()
    u = lab(tmp_path / "u", [task("07:00", 60, "breakfast", "EATING", "kitchen")], start="07:10", extra=PLUG)
    u.s.clock.advance_to(30)
    assert u.room() == "kitchen" and u.on("kitchen_plug") is True


def test_a_room_that_grows_dark_or_whose_light_is_switched_off_gets_its_light_again(tmp_path):
    t = lab(tmp_path, [task("19:00", 120, "read", "LEISURE", "bedroom")], start="18:59")
    t.at("19:02")
    assert t.on("bedroom_lamp") is False  # still light enough
    t.at("20:00")
    assert t.on("bedroom_lamp") is True  # dusk
    t.s.spawn("player", "female_0", (5.0, 4.4))
    t.s.interact("bedroom_lamp", actor="player")
    assert t.on("bedroom_lamp") is False
    t.at("20:02")
    assert t.on("bedroom_lamp") is True
    assert [r["why"]["reason"] for r in inputs(t.s) if r["op"] == "interact"] == ["a dark room", "a dark room"]


def test_the_next_task_s_devices_stay_on_and_a_switch_off_names_the_task_that_used_them(tmp_path):
    t = lab(tmp_path, [task("17:00", 30, "cooking", "HOUSEHOLD", "kitchen"), task("17:30", 30, "dinner", "EATING",
                                                                                   "kitchen"),
                       task("18:00", 60, "walk", "AWAY")], start="16:59", extra=PLUG)
    t.at("18:05")
    uses = [(r["why"]["task"], r["why"]["reason"]) for r in inputs(t.s) if r["op"] == "interact"
            and r["args"]["target"] == "kitchen_plug"]
    assert uses == [("cooking", "cooking"), ("dinner", "dinner is over")]


def test_each_task_a_resident_begins_is_a_trace_record(tmp_path):
    """Ground truth and the page's residents list: who began what, where, and when (spec §2a: the team list)."""
    t = lab(tmp_path, [task("07:00", 30, "wake up", "SLEEP", "bedroom"), task("07:30", 20, "breakfast", "EATING",
                                                                               "kitchen")])
    t.at("07:31")
    began = [(r["resident"], r["task"], r["category"], r["room"]) for r in t.s.records if r["kind"] == "task"]
    assert began == [("r0", "wake up", "SLEEP", "bedroom"), ("r0", "breakfast", "EATING", "kitchen")]
    assert t.people[0].task.name == "breakfast"
