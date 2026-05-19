"""Tests for faulthandler + stack profiler coexistence.

Validates that:
- faulthandler.enable can be called before/after the profiler starts
- faulthandler's traceback output works when our SIGSEGV handler is installed
- The pause/resume mechanism works correctly for safe handler swapping
- Edge cases like stop-while-paused and fork-after-enable are handled
"""

import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_manual_enable_after_profiler_start() -> None:
    """faulthandler.enable called after profiler start must not crash."""
    import faulthandler

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        faulthandler.enable()
        assert faulthandler.is_enabled()
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_manual_enable_before_profiler_start() -> None:
    """faulthandler already enabled when profiler starts must not crash."""
    import faulthandler

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    faulthandler.enable()
    assert faulthandler.is_enabled()

    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        from ddtrace.profiling import _faulthandler  # noqa: F401

        assert faulthandler.is_enabled()
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_manual_multiple_enables() -> None:
    """Calling faulthandler.enable called multiple times must not crash or leak handlers."""
    import faulthandler

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        for _ in range(10):
            faulthandler.enable()
        assert faulthandler.is_enabled()
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={
        "PYTHONFAULTHANDLER": "1",
        "DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_FAST_COPY": "1",
    },
    err=None,
)
def test_pythonfaulthandler_env_after_profiler_start() -> None:
    """PYTHONFAULTHANDLER=1 auto-enables faulthandler before any user code runs.

    The profiler must coexist with the pre-enabled faulthandler.
    """
    import faulthandler

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    assert faulthandler.is_enabled()

    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        assert faulthandler.is_enabled()
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={
        "PYTHONFAULTHANDLER": "1",
        "DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0",
        "_DD_PROFILING_STACK_FAST_COPY": "1",
    },
    err=None,
)
def test_pythonfaulthandler_env_with_multiple_manual_enables() -> None:
    """Calling faulthandler.enable repeatedly with PYTHONFAULTHANDLER=1 must not corrupt the handler chain."""
    import faulthandler

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import _faulthandler  # noqa: F401

    assert stack.is_available
    assert faulthandler.is_enabled()

    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        for _ in range(10):
            faulthandler.enable()
        assert faulthandler.is_enabled()
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"},
    status=-11,
    err=lambda s: "Fatal Python error" in s or "Segmentation fault" in s,
)
def test_faulthandler_traceback_on_segfault() -> None:
    """When a real SIGSEGV occurs, faulthandler's traceback must be printed.

    Our handler is on top but not armed (no safe_memcpy in progress), so it
    chains to faulthandler which prints the traceback, then the process dies.

    faulthandler must be enabled BEFORE importing the native stack module so
    that init_segv_catcher (which runs as a library constructor)
    saves faulthandler's handler as g_old_segv with a clean ``previous``
    pointing at SIG_DFL.
    """
    import faulthandler
    import signal
    import sys

    # Enable faulthandler BEFORE loading the native stack module.
    # The library constructor installs our handler at import time, so
    # faulthandler must already be set up with previous=SIG_DFL.
    if not faulthandler.is_enabled():
        faulthandler.enable(file=sys.stderr)

    from ddtrace.internal.datadog.profiling import stack

    if not stack.is_available:
        sys.stderr.write("Fatal Python error\n")
        signal.raise_signal(signal.SIGSEGV)

    # Our handler was installed by the library constructor.
    # Chain: our handler → faulthandler → SIG_DFL.
    signal.raise_signal(signal.SIGSEGV)


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_pause_sampling_returns_false_when_not_running() -> None:
    """Calling pause_sampling must return False when the sampler is not running."""
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    assert stack.pause_sampling() is False


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_pause_resume_while_running() -> None:
    """Calling pause_sampling must return True when sampler is running, and resume must work."""
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        time.sleep(0.05)
        assert stack.pause_sampling() is True
        stack.resume_sampling()
        time.sleep(0.05)
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_stop_while_paused() -> None:
    """Stopping the sampler while paused must not hang or crash."""
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available
    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    time.sleep(0.05)
    assert stack.pause_sampling() is True
    stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_enable_during_sampling() -> None:
    """Calling faulthandler.enable while sampling is active must pause/swap/resume safely."""
    import faulthandler
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import _faulthandler  # noqa: F401

    assert stack.is_available
    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        time.sleep(0.1)
        faulthandler.enable()
        assert faulthandler.is_enabled()
        time.sleep(0.1)
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_with_fork() -> None:
    """Fork after calling faulthandler.enable + profiler start must not crash."""
    import os

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import _faulthandler  # noqa: F401

    assert stack.is_available

    import faulthandler

    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    faulthandler.enable()
    stack.start()
    try:
        pid = os.fork()
        if pid == 0:
            try:
                import time

                time.sleep(0.05)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status), f"Child killed by signal {os.WTERMSIG(status)}"
            assert os.WEXITSTATUS(status) == 0, "Child exited with non-zero status"
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_uninstall_not_called_on_pause_timeout() -> None:
    """When pause_sampling times out (returns None), uninstall_segv_handler must not be called.

    This guards against the race where the sampling thread is still running
    (pause timed out) but we proceed to uninstall the SIGSEGV handler anyway,
    leaving safe_memcpy without its recovery handler during the swap window.
    """
    import faulthandler
    from unittest.mock import MagicMock
    from unittest.mock import patch

    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available

    # Import _faulthandler to patch faulthandler.enable. No sampler started.
    from ddtrace.profiling import _faulthandler  # noqa: F401

    mock_uninstall = MagicMock()
    mock_reinstall = MagicMock()
    mock_resume = MagicMock()

    with (
        patch.object(stack, "pause_sampling", return_value=None),
        patch.object(stack, "uninstall_segv_handler", mock_uninstall),
        patch.object(stack, "reinstall_segv_handler", mock_reinstall),
        patch.object(stack, "resume_sampling", mock_resume),
    ):
        faulthandler.enable()

    mock_uninstall.assert_not_called()
    mock_resume.assert_not_called()
    # reinstall is still attempted as a best-effort even after a timeout
    mock_reinstall.assert_called_once()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_enable_concurrent_threads() -> None:
    """Calling faulthandler.enable from two threads simultaneously must not deadlock or crash.

    The threading.Lock inside _patched_enable serialises concurrent calls;
    this test fires them in parallel via a Barrier to maximise contention.
    """
    import faulthandler
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.datadog.profiling import stack
    from ddtrace.profiling import _faulthandler  # noqa: F401

    assert stack.is_available
    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    stack.start()
    try:
        errors: list[BaseException] = []
        n_threads = 4
        barrier = threading.Barrier(n_threads)

        def _enable() -> None:
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    faulthandler.enable()
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_enable) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        still_alive = [t for t in threads if t.is_alive()]
        assert not still_alive, f"{len(still_alive)} thread(s) deadlocked"
        assert not errors, f"Thread errors: {errors}"
        assert faulthandler.is_enabled()
        time.sleep(0.05)
    finally:
        stack.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Signal handling not supported on Windows")
@pytest.mark.subprocess(
    env={"DD_PROFILING_ADAPTIVE_SAMPLING_ENABLED": "0", "_DD_PROFILING_STACK_FAST_COPY": "1"}, err=None
)
def test_faulthandler_real_profiler_instance() -> None:
    import threading

    from ddtrace.internal.datadog.profiling import ddup

    ddup.config(env="test", service="test", version="0.0.0")
    ddup.start()

    from ddtrace.internal.datadog.profiling import stack

    assert stack.is_available

    from ddtrace.profiling.profiler import Profiler

    p = Profiler()
    p.start()

    import faulthandler

    def target():
        result = 2
        while result < 50_000:
            result += result

    faulthandler.enable()
    try:
        threads: list[threading.Thread] = []
        for _ in range(10):
            t = threading.Thread(target=target)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
    finally:
        p.stop()
