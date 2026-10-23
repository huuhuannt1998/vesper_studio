from vesper.twins.clock import LogicalClock
from vesper.twins.runtime import OCCUPANCY_TIMEOUT_S, Runtime


def runtime(*records, latency=None):
    rt = Runtime(clock=LogicalClock(), latency=latency)
    events = []
    rt.subscribe(events.append)
    ids = [rt.add(r).id for r in records]
    return rt, events, ids


def test_platform_command_on_a_read_only_feature_is_rejected_and_logged(vds):
    rt, events, (tid,) = runtime(vds["MCCGQ11LM"])
    assert rt.platform_set(tid, "contact", False) is False
    assert rt.actions[-1].accepted is False and "read-only" in rt.actions[-1].reason
    assert rt.state(tid)["contact"] is True and events == []


def test_platform_and_human_commands_are_logged_separately(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"])
    assert rt.platform_set(tid, "state", "ON") and rt.operate(tid, "state", "OFF", actor="alice")
    assert [(a.actor, a.value) for a in rt.actions] == [("platform", "ON"), ("human:alice", "OFF")]
    assert [(e.feature, e.value, e.cause) for e in events] == [("state", "ON", "platform"), ("state", "OFF", "human")]


def test_reports_wait_for_min_interval_and_keep_the_physical_time(vds):
    rt, events, (tid,) = runtime(vds["DIM-1"])  # brightness: min 1 s, max 3600 s, change 1
    rt.clock.advance(0.5)
    rt.platform_set(tid, "brightness", 100)
    assert [e.feature for e in events] == []
    rt.clock.advance(0.5)
    (e,) = events
    assert (e.feature, e.value, e.physical_t, e.observed_t) == ("brightness", 100, 0.5, 1.0)


def test_changes_below_the_reportable_change_are_not_reported(vds):
    rt, events, (tid,) = runtime(vds["TH-1"])  # temperature: min 10 s, change 1.0 °C
    rt.clock.advance(20)
    rt.stimulate(tid, {"type": "ambient", "temperature": 0.5})
    rt.stimulate(tid, {"type": "ambient", "temperature": 21.5})
    assert [(e.feature, e.value) for e in events] == [("temperature", 21.5)]


def test_periodic_reports_follow_max_interval(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"])  # power: max 3600 s
    rt.clock.advance(3600)
    assert ("power", "periodic") in [(e.feature, e.cause) for e in events]


def test_presence_sets_occupancy_and_clears_after_the_timeout(vds):
    rt, events, (tid,) = runtime(vds["PIR-1"])
    assert rt.stimulate(tid, {"type": "presence"}) == ["occupancy"]
    rt.clock.advance(OCCUPANCY_TIMEOUT_S - 1)
    assert rt.state(tid)["occupancy"] is True
    rt.stimulate(tid, {"type": "presence"})  # renewed presence restarts the timeout
    rt.clock.advance(OCCUPANCY_TIMEOUT_S - 1)
    assert rt.state(tid)["occupancy"] is True
    rt.clock.advance(1)
    assert rt.state(tid)["occupancy"] is False
    assert [e.value for e in events if e.feature == "occupancy"] == [True, False]


def test_contact_button_load_and_unknown_stimuli(vds):
    rt, events, (door, button, plug) = runtime(vds["MCCGQ11LM"], vds["BTN-1"], vds["PLUG-1"])
    assert rt.stimulate(door, {"type": "contact", "state": "opened"}) == ["contact"]
    assert rt.state(door)["contact"] is False  # Zigbee2MQTT reports contact=false when open
    rt.stimulate(button, {"type": "button_press", "action": "double"})
    rt.stimulate(button, {"type": "button_press", "action": "double"})
    assert [e.value for e in events if e.feature == "action"] == ["double", "double"]
    rt.clock.advance(5)
    assert rt.stimulate(plug, {"type": "load", "watts": 60}) == ["power"]
    assert rt.stimulate(door, {"type": "ambient", "temperature": 20}) == []
    assert any("no matching feature" in n for n in rt.notes)
    assert rt.stimulate(door, {"type": "earthquake"}) == [] and any("unknown stimulus" in n for n in rt.notes)


def test_latency_separates_physical_and_observed_time(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"], latency=lambda twin, feature: 0.25)
    rt.platform_set(tid, "state", "ON")
    assert events == []
    rt.clock.advance(0.25)
    assert (events[0].physical_t, events[0].observed_t) == (0.0, 0.25)


def test_a_failing_twin_is_isolated(vds, monkeypatch):
    rt, events, (bad, good) = runtime(vds["PLUG-1"], vds["LED1545G12"])
    monkeypatch.setattr(rt.twins[bad], "coerce", lambda *a: 1 / 0)
    assert rt.platform_set(bad, "state", "ON") is False and "ZeroDivisionError" in rt.faulted[bad]
    assert rt.platform_set(good, "state", "ON") is True


def test_a_failing_subscriber_does_not_stop_the_runtime(vds):
    rt, events, (tid,) = runtime(vds["PLUG-1"])
    rt.subscribe(lambda e: 1 / 0)
    assert rt.platform_set(tid, "state", "ON") and events and any("subscriber failed" in n for n in rt.notes)


def test_logical_runs_are_reproducible(vds):
    def run():
        rt, events, (a, b) = runtime(vds["PIR-1"], vds["TH-1"])
        for t in range(0, 600, 37):
            rt.clock.advance_to(t)
            rt.stimulate(a, {"type": "presence"})
            rt.stimulate(b, {"type": "ambient", "temperature": 18 + t % 7})
        rt.clock.advance(4000)
        return [(e.twin_id, e.feature, e.value, e.physical_t, e.observed_t, e.cause) for e in events]
    assert run() == run()


def test_one_product_can_be_installed_twice(vds):
    rt = Runtime(clock=LogicalClock())
    a = rt.add(vds["MCCGQ11LM"], twin_id="zigbee:aqara:mccgq11lm@hall")
    b = rt.add(vds["MCCGQ11LM"], twin_id="zigbee:aqara:mccgq11lm@back")
    rt.stimulate(a.id, {"type": "contact", "state": "opened"})
    assert rt.state(a.id)["contact"] is False and rt.state(b.id)["contact"] is True
