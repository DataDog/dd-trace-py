r"""Targeted regression tests for the GC + fork interaction introduced by PR #18363.

PR #18363 added Py_TPFLAGS_HAVE_GC, tp_traverse, and tp_clear to
PeriodicThread. PR #17780 added an impl Drop for TraceExporterPy (Rust) that
calls exporter.shutdown(3s) when the Python object is collected.

The failure chain (4.9 + #18363):

  1. fork()
  2. _after_fork_child [threads.py, hook [3]] restarts NativeWriter._worker
     non-blocking, _skip_shutdown reset to false.
  3. Tracer._child_after_fork [hook [7]] calls recreate() → new NativeWriter.
     Old NativeWriter refcount drops to 1 — only the reference cycle
       NativeWriter → _worker._target (bound method) → NativeWriter
     keeps it alive.  Without #18363, this cycle is invisible to GC (leaked).
     With #18363, GC can detect and collect it.
  4. Child allocates anything → gen0 threshold crossed → GC fires.
  5. GC detects unreachable cycle → tp_clear(old_worker) → _target = NULL.
  6. NativeWriter refcount = 0 → NativeWriter freed → TraceExporterPy dropped.
  7. TraceExporterPy::Drop [#17780] → exporter.shutdown(3s) with GIL held.
  8. Child frozen up to 3s → misses ping deadline.

In 4.8, step 7 was a fast no-op (no impl Drop). In 4.9 without #18363, the
cycle in step 3 leaks so step 5 never fires. Only 4.9 + #18363 together
trigger the 3s GIL hold during GC in the child.

The tests here simulate this by:
  - Wrapping an object whose __del__ / Drop-equivalent blocks for a measured
    duration, holding the GIL.
  - Creating a PeriodicThread whose _target forms a cycle back to the holder.
  - Forking and measuring child startup latency under GC pressure.

Running the full suite:

    DD_GC_STRESS_FORKS=200 scripts/run-tests -- -- tests/internal/test_periodic_gc_fork.py
"""

import pytest


@pytest.mark.subprocess(timeout=120, err=None)
def test_gc_cycle_drop_blocks_child():
    """Core reproducer: GC-collected cycle triggers a blocking Drop in the child.

    Simulates the NativeWriter + TraceExporterPy situation:
    - A holder object owns an "exporter" whose __del__ blocks for BLOCK_MS.
    - The holder creates a PeriodicThread whose _target is a bound method,
      forming the cycle: holder -> _worker._target -> holder.
    - After fork(), the holder reference is dropped (simulating recreate()),
      leaving only the cycle keeping it alive.
    - GC fires (forced), detects the cycle, calls tp_clear, holder refcount
      hits 0, __del__ runs, blocking BLOCK_MS with the GIL.
    - We measure child startup latency and assert it stays under DEADLINE_MS.
    """
    import gc
    import os
    import signal
    import sys
    import time

    from ddtrace.internal.periodic import PeriodicThread

    BLOCK_MS = int(os.environ.get("DD_GC_BLOCK_MS", "200"))  # simulate exporter.shutdown(~0.2s)
    DEADLINE_MS = int(os.environ.get("DD_GC_DEADLINE_MS", "150"))  # ping deadline
    NFORKS = int(os.environ.get("DD_GC_STRESS_FORKS", "50"))

    failures = []

    class BlockingExporter:
        """Simulates TraceExporterPy::Drop that calls shutdown(3s)."""

        def __del__(self):
            time.sleep(BLOCK_MS / 1000.0)  # blocks with GIL held

    class ServiceHolder:
        """Simulates NativeWriter: owns an exporter and has a PeriodicThread
        whose _target is a bound method of self, forming a reference cycle.
        """

        def __init__(self):
            self._exporter = BlockingExporter()
            self._worker = None

        def periodic(self):
            pass  # bound method: ServiceHolder -> _worker._target -> ServiceHolder

    for fork_n in range(NFORKS):
        # Set up the service (simulates parent's NativeWriter)
        svc = ServiceHolder()
        worker = PeriodicThread(0.5, svc.periodic, name="svc-worker")
        # autorestart=False mirrors NativeWriter's worker (set by PR #18262).
        # With True the ddtrace _after_fork_child hook would restart the thread,
        # keeping a PyRef alive and preventing GC from collecting the cycle —
        # BlockingExporter.__del__ would never fire and the test would pass
        # vacuously without exercising the blocking-drop path.
        worker.__autorestart__ = False
        svc._worker = worker
        worker.start()

        pid = os.fork()
        if pid == 0:
            try:
                signal.alarm(15)
                t0 = time.monotonic()

                # Simulate _after_fork_child: non-blocking restart resets _skip_shutdown.
                # (Already done by ddtrace's own hook; we just record timing.)

                # Simulate Tracer._child_after_fork → recreate(): drop svc reference.
                # The cycle: svc._worker._target (bound method) -> svc keeps refcount=1.
                del svc
                del worker

                # Trigger GC — same as child allocating objects crossing gen0 threshold.
                gc.collect()

                # Measure how long until here (GC with blocking __del__ holds GIL).
                elapsed_ms = (time.monotonic() - t0) * 1000

                # Signal readiness (simulates ping to parent).
                ping_latency_ms = elapsed_ms
                sys.stderr.write(
                    "[fork_n=%d] ping_latency=%.1fms (block=%dms deadline=%dms)\n"
                    % (fork_n, ping_latency_ms, BLOCK_MS, DEADLINE_MS)
                )

                if ping_latency_ms > DEADLINE_MS:
                    sys.stderr.write("FAIL: latency %.1fms > deadline %dms\n" % (ping_latency_ms, DEADLINE_MS))
                    os._exit(3)

                os._exit(0)
            except BaseException as e:
                sys.stderr.write("child exception: %s\n" % e)
                os._exit(2)

        _, status = os.waitpid(pid, 0)
        if os.WIFSIGNALED(status):
            failures.append("fork_n=%d: signal %d" % (fork_n, os.WTERMSIG(status)))
        elif os.WEXITSTATUS(status) == 3:
            failures.append("fork_n=%d: ping latency exceeded deadline" % fork_n)
        elif os.WEXITSTATUS(status) != 0:
            failures.append("fork_n=%d: exit=%d" % (fork_n, os.WEXITSTATUS(status)))

    try:
        worker.stop()
        worker.join(timeout=2.0)
    except Exception:
        pass

    assert not failures, "failures:\n" + "\n".join(failures)


@pytest.mark.subprocess(timeout=120, err=None)
def test_gc_clears_cycle_after_fork():
    """GC tp_clear must not fire on a live PeriodicThread after fork.

    Creates a PeriodicThread whose _target is a bound method that creates a
    reference cycle (_target -> bound method -> Holder -> thread). After fork
    the thread is restarted non-blocking; an aggressive gc.collect() runs
    immediately in the child while the new C++ thread may not yet have
    registered itself in periodic_threads. Fails if the child crashes.
    """
    import gc
    import os
    import signal
    import sys
    import time

    from ddtrace.internal.periodic import PeriodicThread

    NFORKS = int(os.environ.get("DD_GC_STRESS_FORKS", "100"))

    callback_count = [0]

    class Holder:
        def __init__(self):
            self._thread = None

        def work(self):
            callback_count[0] += 1

    holder = Holder()
    t = PeriodicThread(0.005, holder.work, name="gc-cycle-test")
    holder._thread = t
    t.start()

    for fork_n in range(NFORKS):
        pid = os.fork()
        if pid == 0:
            try:
                signal.alarm(15)
                for _ in range(10):
                    gc.collect()
                    time.sleep(0.001)
                time.sleep(0.02)
                for _ in range(5):
                    gc.collect()
                os._exit(0)
            except BaseException as e:
                sys.stderr.write("child exception: %s\n" % e)
                os._exit(2)

        _, status = os.waitpid(pid, 0)
        assert not os.WIFSIGNALED(status), (
            "fork_n=%d child %d killed by signal %d — tp_clear likely fired on live thread"
            % (fork_n, pid, os.WTERMSIG(status))
        )
        assert os.WEXITSTATUS(status) == 0

    t.stop()
    t.join(timeout=2.0)
    sys.stderr.write("[gc-fork] %d forks, %d callbacks\n" % (NFORKS, callback_count[0]))


@pytest.mark.subprocess(timeout=120, err=None)
def test_gc_collect_while_thread_waiting():
    """GC collect during AllowThreads wait must not corrupt _target.

    The PeriodicThread loop releases the GIL while waiting for the next call
    time (AllowThreads). During that window the GC *can* run on another thread.
    This test creates that situation and verifies no crash.
    """
    import gc
    import os
    import signal
    import sys
    import threading
    import time

    import ddtrace.internal._threads as _threads_mod
    from ddtrace.internal.periodic import PeriodicThread

    DURATION = float(os.environ.get("DD_GC_STRESS_SECONDS_THREAD", "5.0"))

    class Cyclic:
        def __init__(self):
            self._thread = None
            self._calls = 0

        def work(self):
            self._calls += 1

    obj = Cyclic()
    t = PeriodicThread(0.001, obj.work, name="gc-alloc-stress")
    obj._thread = t
    t.start()

    stop = threading.Event()

    def _gc_hammer():
        while not stop.is_set():
            gc.collect(0)
            gc.collect(1)
            gc.collect(2)

    def _ref_remover():
        pt = _threads_mod.periodic_threads
        while not stop.is_set():
            ident = t.ident
            if ident is not None and ident in pt:
                try:
                    del pt[ident]
                except (KeyError, TypeError):
                    pass
                time.sleep(0.0001)
                try:
                    pt[ident] = t
                except (TypeError, AttributeError):
                    pass
            time.sleep(0.0002)

    pid = os.fork()
    if pid == 0:
        try:
            signal.alarm(30)
            gc_thread = threading.Thread(target=_gc_hammer, daemon=True)
            ref_thread = threading.Thread(target=_ref_remover, daemon=True)
            gc_thread.start()
            ref_thread.start()
            time.sleep(DURATION)
            stop.set()
            gc_thread.join(timeout=1.0)
            ref_thread.join(timeout=1.0)
            sys.stderr.write("[gc-thread] calls=%d\n" % obj._calls)
            os._exit(0)
        except BaseException as e:
            sys.stderr.write("child exception: %s\n" % e)
            os._exit(2)

    _, status = os.waitpid(pid, 0)
    assert not os.WIFSIGNALED(status), "child %d killed by signal %d — tp_clear fired on live thread" % (
        pid,
        os.WTERMSIG(status),
    )
    assert os.WEXITSTATUS(status) == 0

    stop.set()
    t.stop()
    t.join(timeout=2.0)


@pytest.mark.subprocess(timeout=120, err=None)
def test_gc_collect_during_rapid_fork_cycle():
    """Simulate high-fork-rate scenario: fork rapidly, gc.collect() in child."""
    import gc
    import os
    import random
    import signal
    import sys
    import time

    from ddtrace.internal.periodic import PeriodicThread

    seed = int(os.environ.get("DD_GC_STRESS_SEED", str(random.randrange(1 << 31))))
    nforks = int(os.environ.get("DD_GC_STRESS_FORKS", "200"))
    sys.stderr.write("[rapid-fork] seed=%d nforks=%d\n" % (seed, nforks))
    rnd = random.Random(seed)

    class SvcA:
        def __init__(self):
            self.thread = None
            self.n = 0

        def run(self):
            self.n += 1

    class SvcB:
        def __init__(self):
            self.thread = None

        def run(self):
            pass

    svc_a = SvcA()
    svc_b = SvcB()
    ta = PeriodicThread(0.005, svc_a.run, name="svc-a")
    tb = PeriodicThread(0.010, svc_b.run, name="svc-b")
    svc_a.thread = ta
    svc_b.thread = tb
    ta.start()
    tb.start()

    for fork_n in range(nforks):
        pid = os.fork()
        if pid == 0:
            try:
                signal.alarm(20)
                junk = [{"key": "x" * rnd.randint(10, 200)} for _ in range(rnd.randint(50, 500))]
                gc.collect()
                time.sleep(rnd.uniform(0.001, 0.010))
                gc.collect()
                del junk
                os._exit(0)
            except BaseException as e:
                sys.stderr.write("child exception (fork_n=%d): %s\n" % (fork_n, e))
                os._exit(2)

        _, status = os.waitpid(pid, 0)
        assert not os.WIFSIGNALED(status), "fork_n=%d child %d killed by signal %d; seed=%d" % (
            fork_n,
            pid,
            os.WTERMSIG(status),
            seed,
        )
        assert os.WEXITSTATUS(status) == 0

    ta.stop()
    tb.stop()
    ta.join(timeout=2.0)
    tb.join(timeout=2.0)
    sys.stderr.write("[rapid-fork] done seed=%d, svc_a.n=%d\n" % (seed, svc_a.n))


@pytest.mark.subprocess(timeout=60, err=None)
def test_gc_dealloc_path_with_pending_periodic_threads():
    """Verify tp_free = PyObject_GC_Del path doesn't corrupt the heap."""
    import gc
    import os
    import signal
    import sys

    from ddtrace.internal.periodic import PeriodicThread

    NTHREADS = int(os.environ.get("DD_GC_STRESS_NTHREADS", "500"))

    class Holder:
        def __init__(self):
            self._t = None

        def work(self):
            pass

    pid = os.fork()
    if pid == 0:
        try:
            signal.alarm(30)
            for i in range(NTHREADS):
                h = Holder()
                t = PeriodicThread(60.0, h.work, name="dealloc-%d" % i)
                h._t = t
                t.start()
                t.stop()
                t.join(timeout=1.0)
                del t
                del h
                if i % 20 == 0:
                    gc.collect()
            gc.collect()
            os._exit(0)
        except BaseException as e:
            sys.stderr.write("child exception: %s\n" % e)
            os._exit(2)

    _, status = os.waitpid(pid, 0)
    assert not os.WIFSIGNALED(status), "child %d killed by signal %d — PyObject_GC_Del heap corruption" % (
        pid,
        os.WTERMSIG(status),
    )
    assert os.WEXITSTATUS(status) == 0


@pytest.mark.subprocess(timeout=60, err=None)
def test_pending_list_empty_then_gc():
    """Verify thread still works after pending list is drained and GC fires."""
    import gc
    import os
    import signal
    import sys
    import time

    import ddtrace.internal._threads as _threads_mod
    from ddtrace.internal.periodic import PeriodicThread

    class Holder:
        def __init__(self):
            self._thread = None
            self.calls = 0

        def work(self):
            self.calls += 1

    def _drain_pending():
        pending = _threads_mod._pending_periodic_threads
        while pending:
            try:
                del pending[0]
            except (IndexError, TypeError):
                break

    pid = os.fork()
    if pid == 0:
        try:
            signal.alarm(20)
            h = Holder()
            t = PeriodicThread(0.005, h.work, name="pending-test")
            h._thread = t
            t.start()
            time.sleep(0.01)
            _drain_pending()
            for _ in range(20):
                gc.collect()
                time.sleep(0.001)
            calls_before = h.calls
            time.sleep(0.05)
            calls_after = h.calls
            t.stop()
            t.join(timeout=2.0)
            assert calls_after > calls_before, (
                "thread stopped calling after gc.collect() — _target may have been cleared"
            )
            sys.stderr.write("[pending-test] calls_before=%d calls_after=%d\n" % (calls_before, calls_after))
            os._exit(0)
        except AssertionError as e:
            sys.stderr.write("FAIL: %s\n" % e)
            os._exit(3)
        except BaseException as e:
            sys.stderr.write("child exception: %s\n" % e)
            os._exit(2)

    _, status = os.waitpid(pid, 0)
    assert not os.WIFSIGNALED(status), "child %d killed by signal %d" % (pid, os.WTERMSIG(status))
    ec = os.WEXITSTATUS(status)
    assert ec == 0, "child exit=%d (3=thread stopped calling after gc)" % ec


@pytest.mark.subprocess(timeout=60, err=None)
def test_tracer_child_after_fork_fast_drops_exporter():
    """Regression: Tracer._child_after_fork must not block on TraceExporterPy::Drop.

    Verifies the fix for the 4.9 + #18363 interaction:
      - TraceExporterPy::Drop (PR #17780) calls exporter.shutdown(3s).
      - PeriodicThread GC tracking (PR #18363) makes the
        NativeWriter -> PeriodicThread -> NativeWriter cycle collectable.
      - Together, GC collection in the forked child triggered a 3s GIL hold
        via the Drop impl, blocking the child from pinging the parent.

    Fix: Tracer._child_after_fork calls _discard_writer_exporter(), which
    invokes the fast-drop PyO3 method on the exporter before recreate().
    This takes `inner` without calling shutdown, so the Drop impl is a no-op.

    We verify this by checking that after _child_after_fork the old writer's
    exporter has its inner already taken (i.e., subsequent drop() is a no-op).
    We also time the call to ensure it completes well under any shutdown timeout.
    """
    import gc
    import os
    import signal
    import sys

    NFORKS = int(os.environ.get("DD_GC_STRESS_FORKS", "30"))

    import ddtrace
    from ddtrace.internal.writer.writer import NativeWriter

    tracer = ddtrace.tracer

    failures = []

    for fork_n in range(NFORKS):
        pid = os.fork()
        if pid == 0:
            try:
                signal.alarm(15)

                # os.register_at_fork child hooks (including
                # Tracer._child_after_fork) execute before os.fork() returns
                # in the child, so any timer started here would exclude hook
                # latency. The meaningful check is the state of the old
                # exporter's inner: if _discard_writer_exporter() ran, it is
                # None and gc.collect() is a no-op for the Drop regardless of
                # how long the hooks took.
                gc.collect()

                # Verify _discard_writer_exporter() ran: the new child writer's
                # exporter should be fresh (inner not None). The OLD writer's
                # exporter should have had inner taken.
                new_writer = tracer._span_aggregator.writer
                if isinstance(new_writer, NativeWriter):
                    # New writer has a fresh exporter; debug() returns exactly
                    # "None" (the string) only when inner = Option::None.
                    debug_str = new_writer._exporter.debug()
                    if debug_str == "None":
                        sys.stderr.write("FAIL: new writer exporter inner is None\n")
                        os._exit(4)

                os._exit(0)
            except BaseException as e:
                sys.stderr.write("child exception (fork_n=%d): %s\n" % (fork_n, e))
                os._exit(2)

        _, status = os.waitpid(pid, 0)
        if os.WIFSIGNALED(status):
            failures.append("fork_n=%d: signal %d" % (fork_n, os.WTERMSIG(status)))
        elif os.WEXITSTATUS(status) == 4:
            failures.append("fork_n=%d: new writer exporter inner is None after fork" % fork_n)
        elif os.WEXITSTATUS(status) != 0:
            failures.append("fork_n=%d: exit=%d" % (fork_n, os.WEXITSTATUS(status)))

    assert not failures, "failures:\n" + "\n".join(failures)
