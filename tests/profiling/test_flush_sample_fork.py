"""Regression tests for the flush_sample TLS-batching fork deadlock.

Phase 1 introduced a thread-local batch slot whose constructor takes
slot_registry_mtx. If the parent thread that calls fork() never
constructed its tls_slot before forking, the child's pthread_atfork
handler (postfork_child) would access tls_slot() while holding
slot_registry_mtx; the constructor would then try to re-acquire the
same non-recursive mutex, deadlocking the child.

The fix forces tls_slot construction at the top of Profile::prefork()
so the lock is released before fork happens, and the child's
tls_slot() call is a no-op (slot already constructed).
"""

import pytest


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_PROFILING_HEAP_SAMPLE_SIZE="128",
    ),
    err=None,
    timeout=30,
)
def test_fork_does_not_deadlock_when_parent_has_no_pending_samples() -> None:
    """Parent forks without ever calling flush_sample. Child must not hang.

    This is the gunicorn master-forks-worker pattern: the master process
    loads ddtrace and registers pthread_atfork handlers, but never
    actually samples (the workers do). The forking thread's tls_slot is
    therefore unconstructed at fork time.
    """
    import os
    import signal
    import time

    # Give ddtrace-run's profiler time to start (and thus register
    # pthread_atfork handlers via ProfilerState::start).
    time.sleep(0.5)

    pid = os.fork()
    if pid == 0:
        # Child: just exit cleanly. The bug manifests as a hang inside
        # postfork_child before any user-code in the child runs.
        os._exit(0)

    # Parent: wait with a timeout. Use a SIGALRM-based watchdog because
    # os.waitpid has no native timeout pre-3.13.
    deadline = time.monotonic() + 10.0
    child_exited = False
    while time.monotonic() < deadline:
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            assert not os.WIFSIGNALED(status), f"child died from signal {os.WTERMSIG(status)}"
            assert os.WEXITSTATUS(status) == 0, f"child exit={os.WEXITSTATUS(status)}"
            child_exited = True
            break
        time.sleep(0.05)

    if not child_exited:
        # Child still alive after 10s — almost certainly the fork-time deadlock.
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        raise AssertionError("child hung after fork (>10s) — fork-time deadlock regression")


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_PROFILING_HEAP_SAMPLE_SIZE="128",
    ),
    err=None,
    timeout=30,
)
def test_fork_does_not_deadlock_when_parent_has_pending_samples() -> None:
    """Parent has emitted samples (tls_slot already constructed). Verifies
    the previously-working path still works after the fix.
    """
    import os
    import signal
    import time

    from ddtrace.internal.datadog.profiling import ddup

    # Configure and start to ensure pthread_atfork handlers are armed.
    ddup.start()

    # Build & flush a sample in the main thread so its tls_slot is
    # constructed before fork.
    h = ddup.SampleHandle()
    h.push_walltime(1_000_000, 1)
    h.push_threadinfo(0, 0, "test")
    h.flush_sample()

    time.sleep(0.3)

    pid = os.fork()
    if pid == 0:
        # Child: emit more samples to exercise the post-fork buffered
        # path. Each flush_sample goes through buffered_collect ->
        # tls_slot() which must not deadlock.
        for _ in range(8):
            ch = ddup.SampleHandle()
            ch.push_walltime(1_000_000, 1)
            ch.flush_sample()
        os._exit(0)

    deadline = time.monotonic() + 10.0
    child_exited = False
    while time.monotonic() < deadline:
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            assert not os.WIFSIGNALED(status), f"child died from signal {os.WTERMSIG(status)}"
            assert os.WEXITSTATUS(status) == 0, f"child exit={os.WEXITSTATUS(status)}"
            child_exited = True
            break
        time.sleep(0.05)

    if not child_exited:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        raise AssertionError("child hung after fork (>10s) — fork-time deadlock regression")
