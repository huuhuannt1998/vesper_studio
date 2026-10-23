import threading

import pytest

from vesper.twins.clock import LogicalClock, WallClock


def test_logical_clock_runs_callbacks_in_time_then_schedule_order():
    clock, fired = LogicalClock(), []
    clock.call_at(2, lambda: fired.append(("b", clock.now())))
    clock.call_at(1, lambda: fired.append(("a", clock.now())))
    clock.call_at(2, lambda: fired.append(("c", clock.now())))
    clock.advance(5)
    assert fired == [("a", 1), ("b", 2), ("c", 2)] and clock.now() == 5
    with pytest.raises(ValueError):
        clock.advance_to(4)


def test_wall_clock_fires_in_time_order():
    clock, fired, done = WallClock(), [], threading.Event()
    t0 = clock.now()
    clock.call_at(t0 + 0.06, lambda: (fired.append("b"), done.set()))
    clock.call_at(t0 + 0.02, lambda: fired.append("a"))
    assert done.wait(2)
    clock.close()
    assert fired == ["a", "b"]
