"""Lifecycle stress tests for PeriodicThread.

Randomly interleaves lifecycle operations (create / start / stop / join /
awake / drop-last-ref / fork / gc / thread-churn) across a pool of
PeriodicThread objects to surface races in the native _threads.cpp
implementation.

The assertion surface is "no crash": any child killed by a signal, or any
unexpected exception escaping the public API, fails the test. Past bugs in
the general vicinity of this code the harness is designed to catch:

- PR 14163 - fork-safety of native threads.
- PR 16955 - auto-restart=False cleanup + deallocator joining a stale pthread
  descriptor after the OS thread has exited and glibc has recycled its handle.
- PR 17485 - refcount TOCTOU between std::thread creation and the lambda
  acquiring the GIL to build PyRef.

Reproducibility:
- Seed is random by default and printed to stderr. Set DD_STRESS_SEED to pin it.
- Iteration count can be raised with DD_STRESS_ITERS.
- Duration can be set with DD_STRESS_SECONDS (overrides iters when > 0).
- Per-op trace to a file with DD_STRESS_TRACE_FILE for hang debugging.

The default configuration is kept short enough for normal CI. Use a larger
budget (DD_STRESS_ITERS=50000 or DD_STRESS_SECONDS=120) under ASan/TSan to
run this as a soak.

Each test body is self-contained because @pytest.mark.subprocess extracts
only the test function's AST and runs it as a standalone script - module-
level helpers would not be available to the subprocess.
"""
import pytest


@pytest.mark.subprocess(timeout=120, err=None)
def test_periodic_thread_lifecycle_stress():
    """Randomized lifecycle stress over a pool of PeriodicThread / PeriodicService.

    Fails only on crashes (signal death in subprocess) or unexpected
    exceptions escaping the public API. Tolerates documented API errors
    (RuntimeError / ServiceStatusError from calling stop/join/awake on
    unstarted threads, double-start, etc.).
    """
    import gc
    import os
    import random
    import signal
    import sys
    import threading
    import time

    from ddtrace.internal import periodic
    from ddtrace.internal import service

    def _env_int(name, default):
        raw = os.environ.get(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _env_float(name, default):
        raw = os.environ.get(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    seed_env = os.environ.get("DD_STRESS_SEED")
    seed = int(seed_env) if seed_env else random.randrange(1 << 31)
    iterations = _env_int("DD_STRESS_ITERS", 200)
    seconds = _env_float("DD_STRESS_SECONDS", 0.0)
    pool_size = _env_int("DD_STRESS_POOL", 4)
    fork_every = _env_int("DD_STRESS_FORK_EVERY", 50)
    trace_file = os.environ.get("DD_STRESS_TRACE_FILE", "")
    deadline = (time.monotonic() + seconds) if seconds > 0 else 0.0

    sys.stderr.write(
        "[stress] seed=%d iters=%d seconds=%.1f pool=%d fork_every=%d\n"
        % (seed, iterations, seconds, pool_size, fork_every)
    )
    sys.stderr.flush()

    def _trace(line):
        if trace_file:
            try:
                with open(trace_file, "a") as fh:
                    fh.write(line)
                    fh.flush()
            except Exception:
                pass

    rnd = random.Random(seed)

    # Pool slot -> PeriodicThread | PeriodicService | None.
    # Mixing both classes exercises the Service + worker handoff as well as
    # the raw PeriodicThread path.
    pool = [None] * pool_size

    def _noop():
        pass

    class _Svc(periodic.PeriodicService):
        def periodic(self):
            pass

    class _AwakeSvc(periodic.AwakeablePeriodicService):
        def periodic(self):
            pass

    # Moderate intervals. Too tight (<1ms) means threads race the main
    # thread for the GIL and slow the harness dramatically without adding
    # lifecycle coverage. Too long (seconds) stretches stop() waits. The
    # interesting interactions happen at start/stop/dealloc — the sampling
    # rate itself is not the point.
    _intervals = [0.005, 0.02, 0.1, 0.5]

    def _new_obj():
        interval = rnd.choice(_intervals)
        autorestart = rnd.random() < 0.5
        kind = rnd.choice(["thread", "service", "awake_service"])
        if kind == "thread":
            t = periodic.PeriodicThread(interval, _noop, name="stress-%x" % rnd.randrange(1 << 16))
            t.__autorestart__ = autorestart
            return t
        if kind == "service":
            return _Svc(interval=interval, autorestart=autorestart)
        return _AwakeSvc(interval=interval, autorestart=autorestart)

    def _is_service(obj):
        return isinstance(obj, periodic.PeriodicService)

    def _churn_os_threads(n):
        batch = [threading.Thread(target=lambda: None, daemon=True) for _ in range(n)]
        for th in batch:
            th.start()
        for th in batch:
            th.join()

    forks_done = [0]

    def _do_fork():
        forks_done[0] += 1
        child_seed = seed ^ (forks_done[0] * 0x9E3779B1)
        pid = os.fork()
        if pid == 0:
            try:
                cr = random.Random(child_seed)
                # Alarm so a futex deadlock on a stale pthread still fails.
                signal.alarm(10)
                for _ in range(cr.randint(5, 20)):
                    idx2 = cr.randrange(pool_size)
                    pick = cr.choice(["drop", "gc", "churn", "stop-drop"])
                    if pick == "drop":
                        pool[idx2] = None
                    elif pick == "gc":
                        gc.collect()
                    elif pick == "churn":
                        _churn_os_threads(cr.randint(5, 20))
                    elif pick == "stop-drop":
                        obj2 = pool[idx2]
                        if obj2 is not None:
                            try:
                                obj2.stop()
                            except Exception:
                                pass
                            pool[idx2] = None
                gc.collect()
                os._exit(0)
            except BaseException:
                os._exit(2)
        else:
            _, status_ = os.waitpid(pid, 0)
            assert not os.WIFSIGNALED(status_), (
                "child %d killed by signal %d after %d fork(s); seed=%d, child_seed=%d"
                % (pid, os.WTERMSIG(status_), forks_done[0], seed, child_seed)
            )
            assert os.WEXITSTATUS(status_) == 0, (
                "child %d exit=%d; seed=%d, child_seed=%d"
                % (pid, os.WEXITSTATUS(status_), seed, child_seed)
            )

    # Ops. "drop" stops first; "racy_drop" nullifies without stopping to
    # exercise the dealloc-vs-running-thread path. Kept at a low weight to
    # bound OS thread accumulation over long soaks — a running periodic
    # thread stays alive via the module-level periodic_threads dict even
    # after the pool ref is dropped, so racy_drop leaks a thread each time.
    ops = ["create", "start", "stop", "join", "awake", "drop", "racy_drop", "gc", "churn"]
    weights = [8, 10, 10, 5, 3, 8, 1, 2, 1]

    def _stop_safely(o):
        if o is None:
            return
        try:
            o.stop()
        except (RuntimeError, service.ServiceStatusError, Exception):
            pass

    start_ts = time.monotonic()
    step = 0
    while step < iterations:
        if deadline and time.monotonic() >= deadline:
            break
        step += 1

        # Force a fork at a fixed cadence — cheaper than making fork an RNG
        # pick, and keeps coverage independent of the seed.
        if fork_every and (step % fork_every) == 0:
            _trace("step=%d op=fork\n" % step)
            _do_fork()
            continue

        op = rnd.choices(ops, weights=weights, k=1)[0]
        idx = rnd.randrange(pool_size)
        obj = pool[idx]

        _trace("step=%d op=%s idx=%d obj=%s\n" % (step, op, idx, type(obj).__name__))

        try:
            if op == "create":
                # Stop the existing occupant first; otherwise the old
                # PeriodicThread stays alive via periodic_threads[ident] and
                # the OS thread keeps running, leaking across iterations.
                _stop_safely(obj)
                pool[idx] = _new_obj()

            elif op == "start":
                if obj is None:
                    pool[idx] = _new_obj()
                    obj = pool[idx]
                try:
                    obj.start()
                except (RuntimeError, service.ServiceStatusError):
                    pass

            elif op == "stop":
                if obj is not None:
                    try:
                        obj.stop()
                    except (RuntimeError, service.ServiceStatusError):
                        pass

            elif op == "join":
                if obj is not None:
                    try:
                        obj.join(timeout=0.1)
                    except (RuntimeError, service.ServiceStatusError):
                        pass

            elif op == "awake":
                if obj is not None:
                    try:
                        if isinstance(obj, periodic.AwakeablePeriodicService):
                            obj.awake()
                        elif not _is_service(obj):
                            obj.awake()
                    except RuntimeError:
                        pass

            elif op == "drop":
                # Clean drop: stop first so the OS thread exits and the
                # periodic_threads ref is released. This is the common case.
                _stop_safely(obj)
                pool[idx] = None

            elif op == "racy_drop":
                # Drop the pool ref without stopping. The OS thread stays
                # alive via periodic_threads, so this is a leak over time;
                # the low weight on this op keeps accumulation bounded.
                # Exercises the dealloc-vs-running-thread path.
                pool[idx] = None

            elif op == "gc":
                gc.collect()

            elif op == "churn":
                _churn_os_threads(rnd.randint(5, 15))

        except Exception as e:
            sys.stderr.write(
                "[stress] step=%d op=%s idx=%d raised %s: %s\n"
                % (step, op, idx, type(e).__name__, e)
            )
            raise

    # Final drain: leave no state behind.
    for i in range(pool_size):
        obj = pool[i]
        if obj is None:
            continue
        try:
            obj.stop()
        except Exception:
            pass
        try:
            obj.join(timeout=2.0)
        except Exception:
            pass
        pool[i] = None

    gc.collect()

    elapsed = time.monotonic() - start_ts
    sys.stderr.write(
        "[stress] done: seed=%d steps=%d forks=%d elapsed=%.2fs\n"
        % (seed, step, forks_done[0], elapsed)
    )


@pytest.mark.subprocess(timeout=60, err=None)
def test_periodic_thread_concurrent_dealloc_race():
    """Regression harness for PR 17485 (refcount TOCTOU at start).

    The 17485 race window is between std::thread creation and the lambda
    building PyRef under the GIL. For a drop from *the same thread* that
    called start(), the window is already closed by the time start() returns
    (it blocks on _started->wait). To meaningfully stress the race we need a
    second Python-executing thread that can drop the ref *while* start() is
    in flight.

    This test publishes each fresh PeriodicThread into a module-level list
    shared with a worker thread that continuously pops and gc.collects. That
    way, between start()'s unlock and the lambda's GIL acquire, the worker
    can race in and drop the last reference.
    """
    import gc
    import threading
    import time

    from ddtrace.internal import periodic

    shared = []
    shared_lock = threading.Lock()
    stop_worker = threading.Event()

    def _drop_worker():
        while not stop_worker.is_set():
            with shared_lock:
                batch = shared[:]
                shared.clear()
            del batch  # Drop refs — may race with an in-flight start.
            gc.collect()

    worker = threading.Thread(target=_drop_worker, daemon=True)
    worker.start()

    try:
        for _ in range(2000):
            t = periodic.PeriodicThread(0.5, lambda: None)
            t.start()
            with shared_lock:
                shared.append(t)
            del t
    finally:
        stop_worker.set()
        worker.join(timeout=2.0)
        # Drain anything the worker didn't grab; stop gracefully so orphan
        # OS threads don't accumulate after the test.
        with shared_lock:
            remaining = shared[:]
            shared.clear()
        for t in remaining:
            try:
                t.stop()
            except Exception:
                pass
        for t in remaining:
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
        del remaining
        gc.collect()
        # Give any still-alive OS threads a brief window to exit.
        time.sleep(0.1)


@pytest.mark.subprocess(timeout=60, err=None)
def test_periodic_thread_stop_without_join_then_fork_repeat():
    """Regression harness for PR 16955's pthread_t recycling scenario.

    Loop: start, stop (no join), short sleep to let the OS thread exit, fork
    into a child that churns threads to encourage pthread_t recycling, then
    drop the last reference so dealloc fires in the child against a possibly
    recycled handle. Before the fix this crashed in the child with SIGSEGV.
    """
    import gc
    import os
    import signal
    import threading
    from time import sleep

    from ddtrace.internal import periodic

    def noop():
        pass

    for i in range(20):
        t = periodic.PeriodicThread(60.0, noop, name="svj-%d" % i)
        t.start()
        t.stop()
        sleep(0.05)

        pid = os.fork()
        if pid == 0:
            for _ in range(3):
                batch = [threading.Thread(target=lambda: None, daemon=True) for _ in range(20)]
                for th in batch:
                    th.start()
                for th in batch:
                    th.join()
            signal.alarm(3)
            del t
            gc.collect()
            os._exit(0)

        _, status = os.waitpid(pid, 0)
        assert not os.WIFSIGNALED(status), (
            "iter=%d child killed by signal %d - dealloc crashed on a stale/recycled pthread handle"
            % (i, os.WTERMSIG(status))
        )
        assert os.WEXITSTATUS(status) == 0

        t.join()
        del t
        gc.collect()
