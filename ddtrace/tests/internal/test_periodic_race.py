"""Race-injection stress tests for the middle-ground PeriodicThread fix.

These deliberately try to surface a hang in the awake()/stop() interleaving
that the middle-ground design has to handle without an explicit
condition_variable. Run is bounded so the harness fails fast (rather than
hanging the test session) if a regression appears.
"""

import os
import random
import threading
import time

import pytest

from ddtrace.internal import periodic


# Conservative budget so the test suite stays fast in CI but the race window
# gets enough chances. Override with PERIODIC_RACE_ITERATIONS=N for soaks.
_ITERATIONS = int(os.environ.get("PERIODIC_RACE_ITERATIONS", "5000"))
_PER_OP_DEADLINE = 5.0


def _watchdog(deadline, what):
    """Raise pytest.fail (rather than hang) if `what` does not complete in time."""
    if not deadline.wait(timeout=_PER_OP_DEADLINE):
        pytest.fail("watchdog: %s did not complete within %ss" % (what, _PER_OP_DEADLINE))


def test_race_stop_concurrent_with_awake():
    """Hammer the window where stop() races in-flight awake().

    awake() releases the GIL after the early _stopping check, then competes
    with stop() for _awake_mutex. The middle-ground design must:

    1. If stop() wins the mutex first: awake() observes _stopping under the
       mutex and silently bails (does NOT touch _served).
    2. If awake() wins: it publishes AWAKE; either the worker serves it
       (then exits on STOP) or the worker's cleanup _served->set() wakes us.

    Either branch must complete without hanging.
    """
    rng = random.Random(0xBADBEEF)

    for i in range(_ITERATIONS):
        t = periodic.PeriodicThread(60.0, lambda: None)
        t.start()

        # Variable interleaving — sometimes stop fires before awake even
        # enters the C function, sometimes during, sometimes after.
        jitter_us = rng.randint(0, 500)

        awake_done = threading.Event()
        stop_done = threading.Event()

        def _awake_target():
            try:
                t.awake()
            finally:
                awake_done.set()

        def _stop_target():
            if jitter_us:
                # Busy-spin for a short jitter to vary the race window.
                deadline = time.perf_counter() + jitter_us / 1e6
                while time.perf_counter() < deadline:
                    pass
            t.stop()
            stop_done.set()

        a = threading.Thread(target=_awake_target)
        s = threading.Thread(target=_stop_target)
        a.start()
        s.start()

        _watchdog(awake_done, "iter=%d awake()" % i)
        _watchdog(stop_done, "iter=%d stop()" % i)

        a.join(timeout=1.0)
        s.join(timeout=1.0)
        t.join(timeout=2.0)


def test_race_awake_after_completed_stop_does_not_hang():
    """Tight loop: stop+join+awake must always return, never hang.

    This is the case literally described in PR 17707. The early _stopping
    check is GIL-serialized so it must always observe the prior stop().
    awake() short-circuits silently instead of raising — see
    test_periodic_awake_after_stop_returns_not_hangs.
    """
    for _ in range(_ITERATIONS):
        t = periodic.PeriodicThread(60.0, lambda: None)
        t.start()
        t.stop()
        t.join(timeout=2.0)

        t.awake()


def test_race_callback_stop_with_concurrent_awakes():
    """Timer._periodic pattern + many concurrent awakers.

    The callback self-stops, and several outside threads call awake() in
    parallel. None of them may deadlock or hang regardless of who wins the
    race for _awake_mutex. The first awake() to be served triggers stop()
    inside the callback; subsequent awake()s either complete (worker still
    serving) or silently bail (worker gone).
    """
    NUM_AWAKERS = 8

    for i in range(_ITERATIONS // 4 or 1):  # heavier per-iteration cost
        called = threading.Event()
        t_holder = [None]

        def _target():
            called.set()
            t_holder[0].stop()

        t = periodic.PeriodicThread(60.0, _target)
        t_holder[0] = t
        t.start()

        done_events = [threading.Event() for _ in range(NUM_AWAKERS)]

        def _awaker(idx):
            try:
                t.awake()
            finally:
                done_events[idx].set()

        threads = [threading.Thread(target=_awaker, args=(j,)) for j in range(NUM_AWAKERS)]
        for th in threads:
            th.start()

        for j, ev in enumerate(done_events):
            _watchdog(ev, "iter=%d awaker[%d]" % (i, j))

        for th in threads:
            th.join(timeout=1.0)
        t.join(timeout=2.0)

        # At least one awaker must have produced the wake that triggered
        # the callback. The callback signals `called` before calling stop().
        assert called.is_set(), "iter=%d: callback never ran" % i
