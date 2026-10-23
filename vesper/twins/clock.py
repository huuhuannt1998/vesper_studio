"""Clocks for the twin runtime (spec §3.3 Determinism).

`LogicalClock` runs simulated seconds and reproduces a run exactly. `WallClock` runs real seconds for
real platforms; its callbacks fire on one background thread.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable


class LogicalClock:
    """Callbacks run in (time, scheduling order) as the clock is advanced."""

    def __init__(self, start: float = 0.0):
        self._now = start
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()

    def now(self) -> float:
        return self._now

    def call_at(self, when: float, fn: Callable[[], None]) -> None:
        heapq.heappush(self._queue, (max(when, self._now), next(self._seq), fn))

    def advance_to(self, when: float) -> None:
        if when < self._now:
            raise ValueError("a logical clock cannot run backwards")
        while self._queue and self._queue[0][0] <= when:
            at, _, fn = heapq.heappop(self._queue)
            self._now = at
            fn()
        self._now = when

    def advance(self, seconds: float) -> None:
        self.advance_to(self._now + seconds)


class WallClock:
    """Real seconds since creation. Callbacks fire in time order on one daemon thread."""

    def __init__(self):
        self._t0 = time.monotonic()
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, name="vesper-wallclock", daemon=True)
        self._thread.start()

    def now(self) -> float:
        return time.monotonic() - self._t0

    def call_at(self, when: float, fn: Callable[[], None]) -> None:
        with self._cv:
            heapq.heappush(self._queue, (when, next(self._seq), fn))
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._stopped and (not self._queue or self._queue[0][0] > self.now()):
                    self._cv.wait(None if not self._queue else max(0.0, self._queue[0][0] - self.now()))
                if self._stopped:
                    return
                _, _, fn = heapq.heappop(self._queue)
            fn()

    def close(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify()
        self._thread.join(timeout=5)
