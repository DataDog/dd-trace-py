"""Fault-injection tests for thread lifecycle: concurrency, churn, fork, signals.

These complement the lifecycle matrix in test_lifecycle_faults.py with the
"threads are all good candidates of possible failure models" side:

  * concurrent start/stop hammering (races on Service status + _active_lock)
  * two profilers racing to start (the singleton refusal path)
  * heavy thread create/destroy churn while the lock/stack collectors sample
  * fork() while the profiler is running (thread restart in the child)
  * SIGTERM racing with start()/stop() (the exit-signal flush path)
"""

import logging
import os
import random
import signal
import threading
import time
import typing

import pytest

from ddtrace.internal import service
from ddtrace.profiling import profiler

from tests.profiling.fault_injection import faultlib
from tests.profiling.fault_injection.conftest import assert_restartable
from tests.profiling.fault_injection.conftest import make_profiler

LOG = logging.getLogger(__name__)


class _Recorder:
    """Collects exceptions from hammer threads; only expected ones are OK."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.errors: list[tuple[str, BaseException]] = []

    def call(self, label: str, fn: typing.Callable[[], typing.Any]) -> None:
        try:
            fn()
        except service.ServiceStatusError as e:
            # start-while-running / stop-while-stopped are expected races and
            # are part of the public API contract (Profiler.stop swallows them,
            # _ProfilerInstance.start does not) — record but do not fail.
            LOG.debug("expected status race at %s: %s", label, e)
        except BaseException as e:  # noqa: BLE001 — the oracle
            with self.lock:
                self.errors.append((label, e))


# ---------------------------------------------------------------------------
# Concurrent start / stop hammering
# ---------------------------------------------------------------------------


def test_concurrent_start_stop_hammer() -> None:
    """One profiler, threads calling start()/stop() concurrently, N rounds.

    Every call must either succeed or raise ServiceStatusError; nothing else
    may leak out, and the final state must be cleanly stoppable.
    """
    p = make_profiler()
    rec = _Recorder()
    rng = random.Random(42)

    for round_ in range(8):
        barrier = threading.Barrier(2)

        def _start() -> None:
            barrier.wait()
            rec.call(f"round{round_}.start", p.start)

        def _stop() -> None:
            barrier.wait()
            rec.call(f"round{round_}.stop", lambda: p.stop(flush=False))

        t_start = threading.Thread(target=_start, daemon=True)
        t_stop = threading.Thread(target=_stop, daemon=True)
        t_start.start()
        t_stop.start()
        t_start.join(timeout=25)
        t_stop.join(timeout=25)
        assert not t_start.is_alive() and not t_stop.is_alive(), f"round {round_}: start/stop wedged"
        assert not rec.errors, f"unexpected exceptions in rounds 0..{round_}: {rec.errors}"
        time.sleep(rng.uniform(0, 0.05))

    # The hammer leaves the profiler in an arbitrary status; a final stop must
    # terminate everything (stop-when-stopped is tolerated).
    faultlib.completes_within(lambda: p.stop(), 25.0, label="final stop after hammer")
    leftovers = faultlib.leftover_profiler_threads()
    assert not leftovers, f"threads leaked after hammer: {[getattr(t, 'name', t) for t in leftovers]}"
    assert_restartable()


def test_two_profilers_race_to_start() -> None:
    """Two Profiler instances starting concurrently: only one may win.

    The loser must log-and-refuse (not crash, not double-start collectors).
    """
    p1 = make_profiler()
    p2 = make_profiler()
    rec = _Recorder()
    barrier = threading.Barrier(2)
    threads = [
        threading.Thread(target=lambda: rec.call("p1", lambda: (barrier.wait(), p1.start())[1]), daemon=True),
        threading.Thread(target=lambda: rec.call("p2", lambda: (barrier.wait(), p2.start())[1]), daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=25)
    assert not rec.errors, f"unexpected exceptions during profiler start race: {rec.errors}"

    # exactly one active instance; stop it and confirm a clean world
    active = profiler.Profiler._active_instance
    assert active in (p1, p2), f"unexpected active instance {active!r}"
    faultlib.completes_within(lambda: active.stop(), 25.0, label="stop winner")
    # the loser is still STOPPED; stopping it is a no-op
    loser = p2 if active is p1 else p1
    faultlib.completes_within(lambda: loser.stop(), 25.0, label="stop loser")
    leftovers = faultlib.leftover_profiler_threads()
    assert not leftovers, f"threads leaked after start race: {[getattr(t, 'name', t) for t in leftovers]}"


# ---------------------------------------------------------------------------
# Thread churn under the collectors
# ---------------------------------------------------------------------------


def test_thread_churn_during_sampling() -> None:
    """Rapid thread create/lock/destroy while stack+lock collectors sample.

    Exercises the native thread registry, the _threading lock hooks and the
    sampler's thread table under churn (a classic source of dangling entries).
    """
    p = make_profiler()
    faultlib.completes_within(p.start, 25.0, label="start")

    errors: list[BaseException] = []

    def _churn(n: int) -> None:
        for i in range(60):
            try:
                lock = threading.Lock()
                cond = threading.Condition()
                sem = threading.Semaphore(2)
                t = threading.Thread(target=lambda: [lock.acquire(), lock.release(), sem.acquire(), sem.release()])
                t.start()
                with cond:
                    cond.notify_all()
                t.join(timeout=5)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
                return

    churners = [threading.Thread(target=_churn, args=(n,), daemon=True) for n in range(4)]
    for t in churners:
        t.start()
    for t in churners:
        t.join(timeout=60)
    assert not errors, f"exceptions during thread churn: {errors}"
    time.sleep(0.5)  # let a flush or two observe the churn
    faultlib.completes_within(lambda: p.stop(), 25.0, label="stop after churn")
    leftovers = faultlib.leftover_profiler_threads()
    assert not leftovers, f"threads leaked after churn: {[getattr(t, 'name', t) for t in leftovers]}"


# ---------------------------------------------------------------------------
# Fork while running (subprocess: forking the pytest process is not sane)
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(timeout=120)
def test_fork_while_running() -> None:
    """fork() with the profiler running: threads must restart in the child.

    Both parent and child must complete their work, stop the profiler and exit
    cleanly (the classic native-thread-after-fork failure mode is a hang or a
    crash in the child).
    """
    import os
    import sys
    import time

    from tests.profiling.fault_injection import faultlib

    from ddtrace.profiling import profiler as profiler_mod

    p = profiler_mod.Profiler(
        _memory_collector_enabled=True,
        _lock_collector_enabled=True,
        _exception_profiling_enabled=True,
    )
    p._scheduler.interval = 0.2
    p.start()
    time.sleep(0.5)  # let the sampler observe the parent threads

    pid = os.fork()
    if pid == 0:
        # child: threads were restarted by the fork hooks; do work, stop, exit
        try:
            w = faultlib.Workload(threads=2, duration=0.4)
            w.run()
            if w.exceptions:
                print("CHILD WORKLOAD ERRORS: %r" % (w.exceptions,))
                os._exit(43)
            faultlib.completes_within(lambda: p.stop(), 20.0, label="child stop")
            leftovers = faultlib.leftover_profiler_threads()
            if leftovers:
                print("CHILD LEAKED: %r" % [getattr(t, "name", t) for t in leftovers])
                os._exit(44)
            os._exit(0)
        except BaseException as e:
            print("CHILD FAILED: %r" % (e,))
            os._exit(45)

    # parent: reap the child, keep working, stop, exit
    _, status = os.waitpid(pid, 0)
    child_code = os.waitstatus_to_exitcode(status)
    if child_code != 0:
        print(f"CHILD EXITED WITH {child_code}")
        os._exit(child_code if child_code > 0 else 46)
    w = faultlib.Workload(threads=2, duration=0.4)
    w.run()
    if w.exceptions:
        print("PARENT WORKLOAD ERRORS: %r" % (w.exceptions,))
        os._exit(47)
    faultlib.completes_within(lambda: p.stop(), 20.0, label="parent stop")
    leftovers = faultlib.leftover_profiler_threads()
    if leftovers:
        print("PARENT LEAKED: %r" % [getattr(t, "name", t) for t in leftovers])
        os._exit(48)
    os._exit(0)


# ---------------------------------------------------------------------------
# SIGTERM racing with start()/stop() (subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(timeout=60, status=[0, -15])
def test_sigterm_races_lifecycle() -> None:
    """SIGTERM delivered at random points of the start/stop lifecycle.

    The profiler's exit-signal handler flushes and stops the profiler; it uses
    a non-blocking acquire against start/stop races.  Oracle: the process
    never crashes with a traceback, never wedges.  Exit 0 after a handled
    signal, or -15 if the signal legitimately landed before the profiler had
    installed its handler (default SIGTERM action — acceptable: the profiler
    cannot expect to intercept signals from before its own start()).
    """
    import os
    import random
    import signal
    import threading
    import time

    from ddtrace.profiling import profiler as profiler_mod

    delays = [0.0, 0.005, 0.02, 0.05, 0.1, 0.3]
    rng = random.Random(7)

    def _term_later(delay: float) -> None:
        time.sleep(delay)
        os.kill(os.getpid(), signal.SIGTERM)

    for delay in delays:
        p = profiler_mod.Profiler(
            _memory_collector_enabled=True,
            _lock_collector_enabled=True,
            _exception_profiling_enabled=True,
        )
        p._scheduler.interval = 0.2

        killer = threading.Thread(target=_term_later, args=(delay,), daemon=True)
        killer.start()
        # main thread: start, work, stop — the SIGTERM will land somewhere in
        # here and run the exit handler on the main thread between bytecodes.
        try:
            p.start()
        except Exception as e:
            # If the signal beat the start and the profiler refused, that is a
            # contained failure: it must be restartable on the next round.
            print(f"round delay={delay}: start raised {e!r}")
        time.sleep(rng.uniform(0.01, 0.08))
        try:
            p.stop(flush=True)
        except Exception as e:
            print(f"round delay={delay}: stop raised {e!r}")
        killer.join(timeout=10)
        assert not killer.is_alive(), f"round delay={delay}: signal handler wedged"

    # clean any survivor, then exit 0
    active = profiler_mod.Profiler._active_instance
    if active is not None:
        active.stop(flush=True)
    print("SIGTERM RACE OK")
    os._exit(0)
