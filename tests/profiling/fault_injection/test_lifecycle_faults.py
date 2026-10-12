"""Fault-injection tests for the profiler START / RUN / STOP lifecycle.

Complements the memory-read fuzzing tests (memalloc snapshot parsing) with
lifecycle fuzzing: every stage of the profiler pipeline is made to fail, and
the "graceful degradation" oracle is asserted:

  liveness    — start() / stop() always return within a deadline (no wedging
                of the host application thread)
  containment — faults never leak exceptions into application threads
  cleanliness — after stop(), no profiler worker threads are left behind,
                and a fresh profiler can still start (restartability)

See faultlib.py for the injection primitives.  Hang-type faults run in
subprocesses (a hung stop() parks a native thread that also blocks process
exit, so it must not poison the pytest process).
"""

import logging
import os
import sys
import time
import typing

import pytest

from ddtrace.internal import service
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling import collector
from ddtrace.profiling import profiler
from ddtrace.profiling import scheduler
from ddtrace.profiling.collector import exception as exception_collector
from ddtrace.profiling.collector import memalloc
from ddtrace.profiling.collector import stack as stack_collector

from tests.profiling.fault_injection import faultlib
from tests.profiling.fault_injection.conftest import assert_clean_shutdown
from tests.profiling.fault_injection.conftest import assert_restartable
from tests.profiling.fault_injection.conftest import make_profiler

LOG = logging.getLogger(__name__)

START_DEADLINE = 20.0
STOP_DEADLINE = 20.0
HANG_DEADLINE = 5.0  # short: we *expect* these to complete fast; a hang is the bug


# ---------------------------------------------------------------------------
# Fault catalog: every armable point in the lifecycle, as (obj, attr) pairs
# ---------------------------------------------------------------------------


def fault_points() -> dict[str, tuple[typing.Any, str]]:
    """name -> (obj, attr) mapping of injectable lifecycle call sites."""
    return {
        # -- start path (exporter) --
        "ddup.config": (ddup, "config"),
        "ddup.start": (ddup, "start"),
        # -- start path (collectors) --
        "stack_collector.start": (stack_collector.StackCollector, "_start_service"),
        "exception_collector.start": (exception_collector.ExceptionCollector, "_start_service"),
        "memalloc_collector.start": (memalloc.MemoryCollector, "start"),
        # -- start path (scheduler) --
        "scheduler.start": (scheduler.Scheduler, "_start_service"),
        # -- run path (called on the scheduler thread) --
        "stack_collector.snapshot": (stack_collector.StackCollector, "snapshot"),
        "memalloc_collector.snapshot": (memalloc.MemoryCollector, "snapshot"),
        "ddup.upload": (ddup, "upload"),
        "scheduler.periodic": (scheduler.Scheduler, "periodic"),
        # -- stop path --
        "stack_collector.stop": (stack_collector.StackCollector, "_stop_service"),
        "memalloc_collector.stop": (memalloc.MemoryCollector, "stop"),
        "ddup.upload_final": (ddup, "upload"),
        "scheduler.flush": (scheduler.Scheduler, "flush"),
    }


def arm(injector: faultlib.Injector, name: str, action: faultlib.Action, policy: faultlib.Policy = 1) -> None:
    obj, attr = fault_points()[name]
    injector.arm(name, obj, attr, action, policy)


# ---------------------------------------------------------------------------
# A. Start-path faults
# ---------------------------------------------------------------------------


def tolerant_start(p: profiler.Profiler, deadline: float = START_DEADLINE) -> typing.Optional[BaseException]:
    """Start the profiler, tolerating a *clean* raise.

    Profiler.start() may propagate the injected exception to its caller —
    defensible for an explicit API call (the auto-start path has its own
    containment).  What is never acceptable is hanging, or failing *and*
    leaving collectors running.  Returns the exception if one propagated.
    """
    try:
        faultlib.completes_within(p.start, deadline, label="start with faults armed")
        return None
    except BaseException as e:  # noqa: BLE001
        return e


@pytest.mark.parametrize("point", ["ddup.config", "ddup.start"])
def test_start_fault_exporter_raises(point: str) -> None:
    """An exception in ddup.config/ddup.start must not wedge or poison the app."""
    with faultlib.Injector() as inj:
        arm(inj, point, faultlib.raise_(RuntimeError(f"injected: {point}")))
        p = make_profiler()
        exc = tolerant_start(p)
        if exc is not None:
            assert isinstance(exc, RuntimeError), f"unexpected exception from start: {exc!r}"
    # whatever happened, stop must be possible and clean, and nothing leaked
    assert_clean_shutdown(p)
    leftovers = faultlib.leftover_profiler_threads()
    assert not leftovers, f"threads leaked after exporter fault at {point}: {[getattr(t, 'name', t) for t in leftovers]}"


@pytest.mark.parametrize("point", ["stack_collector.start", "exception_collector.start", "memalloc_collector.start"])
@pytest.mark.parametrize(
    "exc",
    [
        collector.CollectorUnavailable("injected: unavailable"),
        RuntimeError("injected: generic start failure"),
        MemoryError("injected: OOM at start"),
    ],
    ids=["unavailable", "runtime", "oom"],
)
def test_start_fault_collector_raises(point: str, exc: BaseException) -> None:
    """A failing collector is disabled; the profiler (and the app) keeps running."""
    with faultlib.Injector() as inj:
        arm(inj, point, faultlib.raise_(exc))
        p = make_profiler()
        faultlib.completes_within(p.start, START_DEADLINE, label=f"start with fault at {point}")
        # the profiler should still be RUNNING with the surviving collectors
        assert p._profiler.status == service.ServiceStatus.RUNNING, (
            f"profiler did not survive collector failure at {point}: {p._profiler.status}"
        )
        w = faultlib.Workload(threads=2, duration=0.5)
        w.run()
        assert not w.exceptions, f"fault leaked into workload threads: {w.exceptions}"
    assert_clean_shutdown(p)
    assert_restartable()


def test_start_fault_scheduler_thread_fails() -> None:
    """The upload scheduler's worker thread cannot be created (RLIMIT/cgroup pressure).

    The scheduler is started *last* in _start_service — collectors are already
    running by then.  The profiler must not leak those collector worker
    threads when the scheduler fails to start.
    """
    with faultlib.Injector() as inj:
        arm(inj, "scheduler.start", faultlib.raise_(RuntimeError("can't start new thread")))
        p = make_profiler()
        exc = tolerant_start(p)
        if exc is not None:
            assert isinstance(exc, RuntimeError), f"unexpected exception from start: {exc!r}"
    leftovers = faultlib.leftover_profiler_threads()
    assert not leftovers, (
        f"collector worker threads leaked when the scheduler failed to start: {[getattr(t, 'name', t) for t in leftovers]}"
    )
    assert_clean_shutdown(p)
    assert_restartable()


# ---------------------------------------------------------------------------
# B. Run-path faults (fire on the scheduler thread, during uploads)
# ---------------------------------------------------------------------------


def _start_and_run(p: profiler.Profiler, seconds: float = 1.0) -> None:
    faultlib.completes_within(p.start, START_DEADLINE, label="start")
    w = faultlib.Workload(threads=3, duration=seconds)
    w.run()
    assert not w.exceptions, f"fault leaked into workload threads: {w.exceptions}"


@pytest.mark.parametrize("point", ["stack_collector.snapshot", "memalloc_collector.snapshot", "ddup.upload"])
def test_run_fault_raises_every_flush(point: str) -> None:
    """snapshot()/upload() raising on every flush must not kill the scheduler thread."""
    with faultlib.Injector() as inj:
        arm(inj, point, faultlib.raise_(RuntimeError(f"injected: {point}")), policy=None)  # always
        p = make_profiler()
        _start_and_run(p)
        assert p._profiler.status == service.ServiceStatus.RUNNING
        assert inj.fired(point) >= 1, f"fault at {point} never fired (interval too slow?)"
    assert_clean_shutdown(p)
    assert_restartable()


def test_run_fault_scheduler_thread_dies() -> None:
    """The scheduler worker thread dies mid-run (PeriodicThread exits on exception).

    Expectation: the profiler does not crash, stop() still completes.  (That
    uploads silently stop is itself the finding — asserted here as liveness.)
    """
    with faultlib.Injector() as inj:
        arm(inj, "scheduler.periodic", faultlib.raise_(RuntimeError("injected: scheduler death")), policy=2)
        p = make_profiler()
        _start_and_run(p, seconds=1.5)
        assert inj.fired("scheduler.periodic") >= 1
    assert_clean_shutdown(p)


def test_run_fault_snapshot_hangs_temporarily() -> None:
    """A slow snapshot (GC pause, IO stall) delays a flush but must not wedge anything."""
    with faultlib.Injector() as inj:
        arm(inj, "stack_collector.snapshot", faultlib.hang(0.5), policy=None)
        p = make_profiler()
        _start_and_run(p, seconds=1.5)
        assert inj.fired("stack_collector.snapshot") >= 1
    assert_clean_shutdown(p)


# ---------------------------------------------------------------------------
# C. Stop-path faults
# ---------------------------------------------------------------------------


def _stop_time_upload(ctx: faultlib.CallContext) -> bool:
    """Fire only for uploads triggered from Profiler.stop() (main thread, post-stop)."""
    return ctx.thread.name == "MainThread"


def test_stop_fault_final_upload_raises() -> None:
    """The final flush at stop fails — stop must still complete."""
    with faultlib.Injector() as inj:
        p = make_profiler()
        _start_and_run(p, seconds=0.6)
        arm(inj, "ddup.upload_final", faultlib.raise_(RuntimeError("injected: final upload")), policy=None)
    assert_clean_shutdown(p)


def test_stop_fault_collector_stop_raises() -> None:
    """A collector failing to stop must not break the rest of the shutdown."""
    with faultlib.Injector() as inj:
        arm(inj, "stack_collector.stop", faultlib.raise_(RuntimeError("injected: collector stop")))
        p = make_profiler()
        _start_and_run(p, seconds=0.6)
    assert_clean_shutdown(p)
    assert_restartable()


def test_stop_fault_scheduler_flush_raises() -> None:
    with faultlib.Injector() as inj:
        arm(inj, "scheduler.flush", faultlib.raise_(RuntimeError("injected: flush")), policy=None)
        p = make_profiler()
        _start_and_run(p, seconds=0.6)
    assert_clean_shutdown(p)


def test_stop_twice_with_faults() -> None:
    with faultlib.Injector() as inj:
        arm(inj, "stack_collector.snapshot", faultlib.raise_(RuntimeError("injected snapshot")))
        p = make_profiler()
        _start_and_run(p, seconds=0.6)
    assert_clean_shutdown(p)
    # second stop is a no-op, must not raise
    faultlib.completes_within(lambda: p.stop(), STOP_DEADLINE, label="second stop")


# ---------------------------------------------------------------------------
# D. Hang faults — run in subprocesses (a hung stop parks native threads)
# ---------------------------------------------------------------------------


# The subprocess marker executes only the test function body, with the repo
# root on PYTHONPATH — so faultlib is importable via the tests package.


@pytest.mark.subprocess(timeout=90)
@pytest.mark.skipif(not hasattr(sys, "_getframe"), reason="needs frame introspection")
@pytest.mark.xfail(
    reason="FINDINGS.md F3: hung ddup.upload wedges Profiler.stop() (join blocks on the parked worker); "
    "also wedges process exit via the native atexit join — see the standalone repro. "
    "Fails by design until the profiler bounds the join.",
    strict=False,
)
def test_subprocess_hung_upload_does_not_wedge_stop() -> None:
    """ddup.upload hangs forever (agent deadlock / network black hole).

    The upload runs on the scheduler thread; stop() joins that thread, so a
    hung upload can wedge Profiler.stop() — and the C-level atexit hook joins
    periodic threads too, so it can also hang process exit.

    Exit codes: 0 = stop completed, 42 = stop hung, 44 = threads leaked.
    """
    import os
    import time

    from tests.profiling.fault_injection import faultlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling import profiler as profiler_mod

    inj = faultlib.Injector()
    inj.arm("ddup.upload", ddup, "upload", faultlib.hang())  # forever, every flush
    try:
        p = profiler_mod.Profiler(
            _memory_collector_enabled=True,
            _lock_collector_enabled=True,
            _exception_profiling_enabled=True,
        )
        p._scheduler.interval = 0.2
        p.start()
        time.sleep(0.8)  # let at least one flush hit the fault
        try:
            faultlib.completes_within(lambda: p.stop(flush=True), 5.0, label="stop with hung upload")
        except faultlib.DeadlineExceeded as e:
            print("HUNG: " + str(e).splitlines()[0])
            os._exit(42)
        leftovers = faultlib.leftover_profiler_threads()
        if leftovers:
            print("LEAKED: %r" % [getattr(t, "name", t) for t in leftovers])
            os._exit(44)
        os._exit(0)
    finally:
        inj.disarm_all()


@pytest.mark.subprocess(timeout=90)
@pytest.mark.skipif(not hasattr(sys, "_getframe"), reason="needs frame introspection")
@pytest.mark.xfail(
    reason="FINDINGS.md F3: hung collector snapshot wedges Profiler.stop(); same root cause as the "
    "hung-upload variant. Fails by design until the profiler bounds the join.",
    strict=False,
)
def test_subprocess_hung_snapshot_does_not_wedge_stop() -> None:
    """A collector snapshot hangs forever (native-fuzz-style failure).

    before_flush == _collectors_snapshot: stop() waits on the scheduler flush,
    which waits on every snapshot — so a hung snapshot can wedge stop().

    Exit codes: 0 = stop completed, 42 = stop hung, 44 = threads leaked.
    """
    import os
    import time

    from tests.profiling.fault_injection import faultlib

    from ddtrace.profiling import profiler as profiler_mod
    from ddtrace.profiling.collector import stack as stack_collector

    inj = faultlib.Injector()
    inj.arm("snapshot.hang", stack_collector.StackCollector, "snapshot", faultlib.hang())
    try:
        p = profiler_mod.Profiler(
            _memory_collector_enabled=True,
            _lock_collector_enabled=True,
            _exception_profiling_enabled=True,
        )
        p._scheduler.interval = 0.2
        p.start()
        time.sleep(0.8)  # let at least one flush hit the fault
        try:
            faultlib.completes_within(lambda: p.stop(flush=True), 5.0, label="stop with hung snapshot")
        except faultlib.DeadlineExceeded as e:
            print("HUNG: " + str(e).splitlines()[0])
            os._exit(42)
        leftovers = faultlib.leftover_profiler_threads()
        if leftovers:
            print("LEAKED: %r" % [getattr(t, "name", t) for t in leftovers])
            os._exit(44)
        os._exit(0)
    finally:
        inj.disarm_all()


# ---------------------------------------------------------------------------
# E. Chaos: random fault combinations against the full lifecycle
# ---------------------------------------------------------------------------


def test_chaos_random_fault_mix() -> None:
    """Arm a random (seeded) mix of faults, run the full lifecycle, assert the oracle."""
    import random

    rng = random.Random(20240607)
    points = list(fault_points())
    chosen = rng.sample(points, k=min(4, len(points)))

    inj = faultlib.Injector()
    try:
        for name in chosen:
            obj, attr = fault_points()[name]
            # Only non-fatal actions in-process; chaos policies vary
            action = rng.choice(
                [
                    faultlib.raise_(RuntimeError(f"chaos: {name}")),
                    faultlib.raise_(MemoryError(f"chaos: {name}")),
                    faultlib.hang(rng.choice([0.01, 0.05])),
                    faultlib.skip(None),
                ]
            )
            policy = rng.choice([1, 2, None, 0.5])
            inj.arm(name, obj, attr, action, policy)
        p = make_profiler()
        faultlib.completes_within(p.start, START_DEADLINE, label="chaos start")
        w = faultlib.Workload(threads=3, duration=1.0)
        w.run()
        assert not w.exceptions, f"chaos faults leaked into workload threads: {w.exceptions}"
    finally:
        inj.disarm_all()
    assert_clean_shutdown(p)


def test_chaos_stress_repeated_lifecycle() -> None:
    """Repeated start/stop cycles with a persistent low-probability fault mix."""
    inj = faultlib.Injector()
    try:
        obj, attr = fault_points()["stack_collector.snapshot"]
        inj.arm("snap.raise", obj, attr, faultlib.raise_(RuntimeError("chaos snapshot")), policy=0.25)
        obj2, attr2 = fault_points()["ddup.upload"]
        inj.arm("upload.skip", obj2, attr2, faultlib.skip(None), policy=0.25)
        for i in range(5):
            p = make_profiler()
            faultlib.completes_within(p.start, START_DEADLINE, label=f"cycle {i} start")
            time.sleep(0.3)
            faultlib.completes_within(lambda: p.stop(), STOP_DEADLINE, label=f"cycle {i} stop")
    finally:
        inj.disarm_all()


# ---------------------------------------------------------------------------
# F. Cross-product findings: threads owned by *other* ddtrace products
# ---------------------------------------------------------------------------
# The liveness oracle splits leftovers per product. Profiler leaks fail hard;
# threads owned by other products are recorded. This test pins the known
# telemetry finding explicitly so it stays visible instead of being silently
# filtered out.


def test_telemetry_threads_outlive_profiler() -> None:
    """FINDING (telemetry product boundary): Profiler activation starts
    telemetry machinery that Profiler.stop() does not (and cannot) stop.

    Profiler.start() calls telemetry_writer.product_activated(PROFILER, True),
    which starts telemetry's own periodic worker(s) (e.g. the dependency
    collector).  Profiler.stop() reports deactivation but leaves those
    workers running until process exit (telemetry's atexit).  For an
    embedder that start/stops profilers per unit of work (our hammer test),
    telemetry threads accumulate their lifecycle decisions independently of
    the profiler's own thread accounting.
    """
    telemetry_threads = lambda: faultlib.leftover_threads_by_product().get("telemetry", [])

    before = len(telemetry_threads())

    p = make_profiler()
    p.start()
    during = telemetry_threads()
    assert p._profiler.status == service.ServiceStatus.RUNNING

    p.stop()

    after = telemetry_threads()
    # The finding, asserted so it stays visible:
    assert after, (
        "expected telemetry worker threads to still be running after Profiler.stop() "
        "(the dependency collector outlives the profiler); if this now fails, telemetry "
        "lifecycle changed and this finding should be re-evaluated"
    )
    # And the profiler's own accounting stays clean regardless:
    profiling_leftovers = faultlib.leftover_threads_by_product().get("profiling", [])
    assert not profiling_leftovers, f"profiler leak: {[str(getattr(t, 'name', t)) for t in profiling_leftovers]}"
    LOG.info(
        "telemetry finding: threads before profiler start=%r, during=%r, after profiler stop=%r (outlive the profiler by design)",
        before,
        [str(getattr(t, "name", t)) for t in during],
        [str(getattr(t, "name", t)) for t in after],
    )
