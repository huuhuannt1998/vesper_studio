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
    rt, events, (tid,) = runtime(vds["PLUG-1"], latency=lambda twin, feature, cause: 0.25)
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


def test_a_malformed_stimulus_is_rejected_without_faulting_the_twin(vds):
    rt = Runtime()
    plug = rt.add(vds["PLUG-1"]).id
    assert rt.stimulate(plug, {"type": "load"}) == []  # no watts
    assert rt.stimulate(plug, "load") == []  # not even a dict
    assert rt.stimulate(plug, {"type": "load", "watts": "lots"}) == []
    assert plug not in rt.faulted and any("rejected" in n for n in rt.notes)
    assert rt.stimulate(plug, {"type": "load", "watts": 5}) == ["power"]


def test_a_failing_latency_model_faults_only_its_own_twin(vds):
    def latency(twin, feature, cause):
        if twin.vds["identity"]["model"] == "PLUG-1":
            raise ZeroDivisionError("bad model")
        return 0.0

    rt = Runtime(latency=latency)
    plug, door = rt.add(vds["PLUG-1"]).id, rt.add(vds["MCCGQ11LM"]).id
    seen = []
    rt.subscribe(seen.append)
    rt.platform_set(plug, "state", "ON")
    rt.stimulate(door, {"type": "contact", "state": "opened"})
    assert "ZeroDivisionError" in rt.faulted[plug] and door not in rt.faulted
    assert [e.feature for e in seen] == ["contact"]


def test_a_failing_scheduled_report_faults_only_its_own_twin(vds):
    rt = Runtime()
    th = rt.add(vds["TH-1"]).id
    door = rt.add(vds["MCCGQ11LM"]).id
    rt.stimulate(th, {"type": "ambient", "temperature": 30.0})  # within min_s: a flush is scheduled
    rt.twins[th].state = None  # the scheduled flush will now fail
    rt.clock.advance(20)  # must not raise into the clock
    assert th in rt.faulted and door not in rt.faulted


def test_a_deferred_report_is_dropped_if_the_value_drifted_back_below_the_change(vds):
    rt, events, (th,) = runtime(vds["TH-1"])
    rt.clock.advance(60)
    rt.stimulate(th, {"type": "ambient", "temperature": 20.0})  # reported at once (last report long ago)
    rt.clock.advance(1)
    rt.stimulate(th, {"type": "ambient", "temperature": 22.0})  # significant, but min_s 10: deferred
    rt.clock.advance(1)
    rt.stimulate(th, {"type": "ambient", "temperature": 20.4})  # back within 1.0 of the last report
    rt.clock.advance(20)
    assert [(e.feature, e.value) for e in events if e.feature == "temperature"] == [("temperature", 20.0)]


def test_a_periodic_report_after_a_fractional_report_time_does_not_spin(vds):
    """(a + max_s) - a can round below max_s; the periodic tick must still report, not reschedule forever."""
    import threading

    rt, events, (plug,) = runtime(vds["PLUG-1"])
    t = 851.518787493795  # found in a rule run: (t + 3600) - t == 3599.9999999999995
    rt.clock.advance_to(t)
    rt.platform_set(plug, "state", "ON")
    done = threading.Event()
    worker = threading.Thread(target=lambda: (rt.clock.advance_to(t + 3700), done.set()), daemon=True)
    worker.start()
    assert done.wait(5), "the periodic tick spins at one instant"
    assert [e.cause for e in events if e.feature == "state"] == ["platform", "periodic"]
